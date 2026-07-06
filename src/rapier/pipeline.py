"""The controller — runs stages in order over one Envelope.

This is the deterministic heart of the runtime. It owns the loop and the
fail-soft policy; it holds no prompt text and no vendor names (those come from
the manifest via each stage's roles). Given a request, it mints an Envelope,
runs each stage, records to the ledger, and returns the final Envelope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .envelope import Envelope
from .ledger import Ledger
from .models import ModelSpec, build_client
from .stage import StageContext, get_stage


@dataclass
class StageSpec:
    """One resolved stage entry from a manifest."""

    stage: str
    config: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, ModelSpec] = field(default_factory=dict)


class Pipeline:
    def __init__(self, stages: list[StageSpec], name: str = "pipeline"):
        self.stages = stages
        self.name = name

    @classmethod
    def from_manifest(cls, manifest) -> "Pipeline":
        # Duck-typed on .stages / .name to avoid a manifest<->pipeline import cycle.
        return cls(list(manifest.stages), name=getattr(manifest, "name", "pipeline"))

    def run(
        self,
        request: str,
        ledger_root: str | None = None,
        log: Callable[[str], None] = lambda _m: None,
    ) -> Envelope:
        env = Envelope(request=request)
        ledger = Ledger(ledger_root, run_slug=self.name) if ledger_root else None

        for spec in self.stages:
            stage = get_stage(spec.stage)()
            clients = {role: build_client(ms) for role, ms in spec.roles.items()}
            ctx = StageContext(
                config=spec.config,
                clients=clients,
                ledger=ledger,
                run_dir=(ledger.run_dir if ledger else None),
                log=log,
            )
            log(f"stage: {spec.stage} ({stage.kind})")
            try:
                env = stage.run(env, ctx)
            except NotImplementedError:
                # A genuinely-unbuilt stage (e.g. a ConvergenceStage before M2)
                # is a wiring error, not a runtime fault — surface it.
                raise
            except Exception as exc:  # fail-soft: record and continue
                env.add_trace(spec.stage, stage.kind, f"ERROR (fail-soft): {exc}", error=str(exc))
                log(f"  stage {spec.stage} failed-soft: {exc}")

            if ledger:
                tail = env.trace[-1].summary if env.trace else ""
                ledger.record_stage(spec.stage, {"kind": stage.kind, "trace_tail": tail})

        if ledger:
            ledger.persist_envelope(env)
        return env
