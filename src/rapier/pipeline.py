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
from .models import (
    ModelSpec,
    Policy,
    available_vendors,
    build_client,
    default_model,
    set_transcript_sink,
)
from .stage import StageContext, get_stage


@dataclass
class StageSpec:
    """One resolved stage entry from a manifest."""

    stage: str
    config: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, ModelSpec] = field(default_factory=dict)


def _resolve_role_spec(
    ms: ModelSpec, policy: Policy | None, available: list[str]
) -> tuple[ModelSpec, str | None]:
    """Keep a role's declared vendor when its key is present; otherwise remap it to
    a policy-resolved *available* vendor (so BYO-any-vendor works, not just the
    vendor a preset happened to name).

    ``mock`` needs no key and is always kept; an explicit choice whose key IS
    present is respected. Returns ``(spec, substituted_from)`` — ``substituted_from``
    is the original vendor when a swap happened, else ``None``. When nothing is
    available it leaves the spec as-is (the CLI preflight / honest skip handle
    the no-keys case).
    """
    if ms.vendor == "mock" or ms.vendor in available:
        return ms, None
    primary, _ = (policy or Policy()).resolve(available, primary_pref=None)
    if not primary:
        return ms, None
    swapped = ModelSpec(
        vendor=primary,
        model=default_model(primary),
        prompt_template=ms.prompt_template,
        max_tokens=ms.max_tokens,
        temperature=ms.temperature,
    )
    return swapped, ms.vendor


class Pipeline:
    def __init__(self, stages: list[StageSpec], name: str = "pipeline", policy=None):
        self.stages = stages
        self.name = name
        self.policy = policy  # a models.Policy governing vendor selection (V3)

    @classmethod
    def from_manifest(cls, manifest) -> "Pipeline":
        # Duck-typed on .stages / .name / .policy to avoid a manifest<->pipeline cycle.
        return cls(list(manifest.stages), name=getattr(manifest, "name", "pipeline"),
                   policy=getattr(manifest, "policy", None))

    def run(
        self,
        request: str,
        ledger_root: str | None = None,
        log: Callable[[str], None] = lambda _m: None,
    ) -> Envelope:
        env = Envelope(request=request)
        ledger = Ledger(ledger_root, run_slug=self.name) if ledger_root else None
        # Capture every model call (all vendors, one path) to the transcript.
        if ledger:
            set_transcript_sink(ledger.record_transcript)

        try:
            return self._run_stages(env, ledger, log)
        finally:
            set_transcript_sink(None)

    def _run_stages(self, env: Envelope, ledger, log) -> Envelope:
        available = available_vendors()
        for spec in self.stages:
            stage = get_stage(spec.stage)()
            clients = {}
            for role, ms in spec.roles.items():
                rms, sub_from = _resolve_role_spec(ms, self.policy, available)
                clients[role] = build_client(rms)
                if sub_from:
                    msg = (
                        f"vendor substitution: {role} {sub_from}->{rms.vendor} "
                        f"(no {sub_from} key; using an available vendor)"
                    )
                    env.add_trace(spec.stage, stage.kind, msg)
                    log(f"  {msg}")
            ctx = StageContext(
                config=spec.config,
                clients=clients,
                ledger=ledger,
                run_dir=(ledger.run_dir if ledger else None),
                log=log,
                policy=self.policy,
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
