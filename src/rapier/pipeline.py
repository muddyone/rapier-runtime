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


def writes_answer(stage: str) -> bool:
    """Does this stage write or rewrite the answer (env.recommendation)? Declared
    by the stage class (``writes_answer = True``). A stage name that isn't
    registered counts as a writer: an unknown stage could change the answer, so a
    gate before it must not be trusted to have checked the answer."""
    try:
        cls = get_stage(stage)
    except Exception:  # noqa: BLE001 — unknown stage: assume the worst
        return True
    return bool(getattr(cls, "writes_answer", False))


def _window(names: list[str]) -> tuple[int | None, int]:
    """(index of the last answer-writing stage, index where output is fixed).
    Output is fixed at compose, or at the end when there is no compose: then
    the bare recommendation is what the caller gets."""
    writers = [i for i, n in enumerate(names) if writes_answer(n)]
    end = names.index("compose") if "compose" in names else len(names)
    return (writers[-1] if writers else None), end


def manifest_warnings(stages: list[StageSpec]) -> list[str]:
    """Check what a pipeline declares before it runs.

    A pipeline that writes no answer (Frame, Proposer-only) is not held to this.
    Any other must run a correctness gate after the last stage that writes the
    answer and before the output is fixed. A gate before that stage checks an
    earlier draft (or nothing); a gate after compose never reaches the report."""
    names = [s.stage for s in stages]
    if "compose_map" in names:
        return map_manifest_warnings(names)
    last_writer, end = _window(names)
    if last_writer is None:
        return []
    out: list[str] = []
    if "compose" not in names:
        out.append("writes no report (no compose stage), so the answer is returned bare, "
                   "without its verdict or trust rider")
    if not manifest_declares(stages, "definitiveness_gate"):
        out.append("declares no correctness gate (definitiveness_gate) between the last "
                   f"stage that writes the answer ({names[last_writer]}) and the report, so "
                   "the answer is never checked")
    for i, n in enumerate(names):
        if n not in GATE_STAGES or n == "traceability_gate":
            continue
        if i > end and "compose" in names:
            out.append(f"{n} runs after compose, so its result never reaches the verdict "
                       "or the report")
        elif i < last_writer and not any(
                names[j] == n for j in range(last_writer + 1, end)):
            out.append(f"{n} runs only before {names[last_writer]}, so it checks an earlier "
                       "draft, not the answer")
    return list(dict.fromkeys(out))


# Map mode (spec v1.3 §3.7): each check must run, in this order, before compose_map.
MAP_REQUIRED = ("map_frame", "map_spark", "map_attack", "map_cite", "map_gate")


def map_manifest_warnings(names: list[str]) -> list[str]:
    """A map pipeline must frame before generating, attack after generating, check
    citations after the attacks (attack candidates carry anchors too), and gate
    before compose_map. The field is never shipped without the gate."""
    out: list[str] = []
    end = names.index("compose_map")
    pos = {n: i for i, n in enumerate(names[:end])}
    for n in MAP_REQUIRED:
        if n not in pos:
            out.append(f"map mode declares no {n} before compose_map, so the map ships without "
                       "that check")
    order = [n for n in MAP_REQUIRED if n in pos]
    if [pos[n] for n in order] != sorted(pos[n] for n in order):
        out.append("map stages are out of order (needed: " + " -> ".join(MAP_REQUIRED) + ")")
    for n in ("cut", "author", "cross_review", "anchored_fix", "definitiveness_gate"):
        if n in names:
            out.append(f"{n} runs in a map pipeline; in map mode the Cut and the Resolver do not run")
    return out


def manifest_declares(stages: list[StageSpec], stage: str) -> bool:
    """Is ``stage`` declared where it checks the answer the caller gets: after the
    last answer-writing stage and before the output is fixed?"""
    names = [s.stage for s in stages]
    last_writer, end = _window(names)
    if last_writer is None:
        return stage in names
    return any(names[i] == stage for i in range(last_writer + 1, end))


def _canon(obj: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        return _canon(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    return obj


# Stages that bind vendors at run time (stages/resolver/_binding.py) rather than
# through roles, with the config key that carries their second-vendor preference.
_BINDING_STAGES = {"cross_review": "reviewer_vendor", "definitiveness_gate": "second_vendor",
                   "map_frame": "challenger_vendor"}  # map mode fixes its vendors at map_frame


def _code_digest() -> str:
    """sha256 over every .py file in the installed package, vendored code included.
    Prompts live in stage code, so an edit under an unchanged version still shows."""
    import hashlib
    import os

    root = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                h.update(os.path.relpath(full, root).replace(os.sep, "/").encode())
                h.update(b"\0")
                with open(full, "rb") as fh:
                    h.update(fh.read())
                h.update(b"\0")
    return "sha256:" + h.hexdigest()


def method_fingerprint(stages: list[StageSpec], policy, available: list[str]) -> dict[str, Any]:
    """Identify the method that runs (Accountable Agents item 5).

    ``hash`` covers what was declared: the Rapier version and a digest of the
    package's code (stage code and its prompts, vendored gates included), the
    stages in order with their config and roles, and the vendor policy.
    ``resolved_hash`` covers what actually ran: the same, with each role after
    vendor substitution for the keys present, and, for the stages that bind
    vendors at run time (cross-review, the correctness gate), the primary and
    second vendor they will bind. Losing the second vendor, and with it the
    independent review, moves ``resolved_hash``."""
    import hashlib
    import json

    from . import __version__

    code = _code_digest()

    def digest(stage_list):
        body = {"rapier_version": __version__, "code": code, "policy": _canon(policy),
                "stages": stage_list}
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    declared = [_canon({"stage": s.stage, "config": s.config, "roles": s.roles}) for s in stages]
    resolved = []
    author_vendor = None
    for s in stages:
        roles = {r: _resolve_role_spec(ms, policy, available)[0] for r, ms in s.roles.items()}
        if "author" in roles:
            author_vendor = roles["author"].vendor
        entry: dict[str, Any] = {"stage": s.stage, "config": s.config, "roles": roles}
        if s.stage in _BINDING_STAGES:
            try:
                pair = (policy or Policy()).resolve(
                    available, primary_pref=author_vendor,
                    secondary_pref=s.config.get(_BINDING_STAGES[s.stage]))
                entry["binds"] = {"primary": pair[0], "second": pair[1]}
            except Exception as exc:  # noqa: BLE001 — record, don't fail the run here
                entry["binds"] = {"error": type(exc).__name__}
        resolved.append(_canon(entry))
    return {"hash": digest(declared), "resolved_hash": digest(resolved),
            "rapier_version": __version__, "code_hash": code, "stages": len(stages)}


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
        max_usd: float | None = None,
    ) -> Envelope:
        from . import budget

        env = Envelope(request=request)
        cap = budget.effective_cap(max_usd)  # raises InvalidCap before anything runs
        budget_token = budget.set_budget(cap)
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
            env = self._run_stages(env, ledger, log, cancel)
            snap = budget.snapshot()
            if snap is not None:
                env.meta["budget"] = snap
                if ledger:
                    ledger.write_json("budget.json", snap)
            return env
        finally:
            set_transcript_sink(None)
            budget.reset_budget(budget_token)

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
        env.meta["method"] = method_fingerprint(self.stages, self.policy, available)
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
