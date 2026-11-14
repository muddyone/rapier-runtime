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


GATE_STAGES = ("definitiveness_gate", "citation_gate", "reconcile", "traceability_gate")


def manifest_warnings(stages: list[StageSpec]) -> list[str]:
    """Check what a pipeline declares before it runs.

    Only pipelines that compose a report are held to this: the Frame and
    Proposer-only presets end before a verdict exists, by design. Warns when no
    correctness gate is declared, or when a gate sits after ``compose``, where
    its result can never reach the verdict or the report."""
    names = [s.stage for s in stages]
    if "compose" not in names:
        return []
    out: list[str] = []
    compose_at = names.index("compose")
    if "definitiveness_gate" not in names[:compose_at]:
        out.append("declares no correctness gate (definitiveness_gate) before compose, so "
                   "the report's verdict is never checked")
    for i, n in enumerate(names):
        if n in GATE_STAGES and i > compose_at:
            out.append(f"{n} runs after compose, so its result never reaches the verdict "
                       "or the report")
    return out


def manifest_declares(stages: list[StageSpec], stage: str) -> bool:
    names = [s.stage for s in stages]
    return stage in names and ("compose" not in names or names.index(stage) < names.index("compose"))


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
        cancel: Callable[[], bool] | None = None,
        seed_meta: dict[str, Any] | None = None,
    ) -> Envelope:
        env = Envelope(request=request)
        # Caller-supplied envelope metadata, merged before any stage runs. Used to
        # carry context the pipeline itself can't observe — e.g. the front-door
        # Frame classification (from a separate `rapier frame` call) and the
        # resolver knobs (settle/verify) — so the compose stage can record them
        # on the ceremony-ledger row.
        if seed_meta:
            env.meta.update(seed_meta)
        ledger = Ledger(ledger_root, run_slug=self.name) if ledger_root else None
        # Capture every model call (all vendors, one path) to the transcript.
        if ledger:
            set_transcript_sink(ledger.record_transcript)

        try:
            return self._run_stages(env, ledger, log, cancel)
        finally:
            set_transcript_sink(None)

    def _run_stages(self, env: Envelope, ledger, log, cancel=None) -> Envelope:
        import os

        if ledger is not None:
            run_dir = getattr(ledger, "run_dir", None)
            if run_dir:
                env.meta["run_id"] = os.path.basename(run_dir)
                # Full path + status so the report can state its own record
                # provenance (THE RECORD section) — governance is only real when
                # the reader can see that the record exists and where.
                env.meta["run_dir"] = os.path.abspath(run_dir)
                env.meta["persisted"] = True
        else:
            env.meta["persisted"] = False
        available = available_vendors()
        # Declared vs ran (Accountable Agents item 3). The pipeline, not each
        # stage, records what happened to every declared stage, so a stage that
        # writes no trace of its own still shows up.
        warnings = manifest_warnings(self.stages)
        env.meta["manifest_warnings"] = warnings
        for w in warnings:
            log(f"  manifest warning: {w}")
        runs: list[dict[str, Any]] = []
        env.meta["stage_runs"] = runs
        for spec in self.stages:
            # Cooperative cancellation: stop at a stage boundary (never mid-call)
            # so the partial envelope stays coherent and auditable.
            if cancel is not None and cancel():
                env.add_trace(spec.stage, "control", "run cancelled — stopping before this stage")
                log(f"  cancelled — stopping before {spec.stage}")
                done = len(runs)
                runs.extend({"stage": s.stage, "status": "not-reached",
                             "detail": "run cancelled"} for s in self.stages[done:])
                break
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
            n_before = len(env.trace)
            record: dict[str, Any] = {"stage": spec.stage, "status": "ran", "detail": ""}
            runs.append(record)
            try:
                env = stage.run(env, ctx)
            except NotImplementedError:
                # A genuinely-unbuilt stage (e.g. a ConvergenceStage before M2)
                # is a wiring error, not a runtime fault — surface it.
                raise
            except Exception as exc:  # fail-soft: record and continue
                env.add_trace(spec.stage, stage.kind, f"ERROR (fail-soft): {exc}", error=str(exc))
                log(f"  stage {spec.stage} failed-soft: {exc}")
                record.update(status="failed-soft", detail=str(exc)[:200])
            else:
                own = [t for t in env.trace[n_before:] if t.stage == spec.stage]
                skipped = next((t.summary for t in own if "skipped" in (t.summary or "")), None)
                if skipped:
                    record.update(status="skipped", detail=skipped)
            env.meta["stage_runs"] = runs  # a stage may have replaced env.meta

            if ledger:
                tail = env.trace[-1].summary if env.trace else ""
                ledger.record_stage(spec.stage, {"kind": stage.kind, "trace_tail": tail})

        if ledger:
            ledger.persist_envelope(env)
        return env
