"""Built-in ceremony presets — the manifests the `/spar` and `/sparring`
adapters select. Embedded (not file paths) so they work when pip-installed.
The `manifests/*.yaml` files mirror the *default* presets for human reference.

The resolver is parameterized by two knobs the CLI exposes (`--settle`,
`--verify`), so the SPARRING skills route those flags to the engine rather than
falling back to an in-session path:

* ``settle=N`` — after the first grounded pass, run N more review-and-revise
  rounds on the recommendation (decision-stability for governance sign-off; a
  measured output-quality *null*, so it is off by default). Each round is
  ``cross_review → anchored_fix → definitiveness_gate``.
* ``verify=off|gate|round`` — the external-canon citation gate. ``gate``
  (default) runs it once before compose; ``off`` drops it; ``round`` runs it
  after every settle round as well.
"""
from __future__ import annotations

# The resolver's generative stages (author + anchored_fix) need real headroom —
# 1024 truncates a recommendation mid-sentence. Match the Proposer's budget.
_RESOLVER_MAX_TOKENS = 8000
_AUTHOR = {"vendor": "anthropic", "model": "claude-opus-4-8", "max_tokens": _RESOLVER_MAX_TOKENS}

# Proposer depth — how much divergence and rigor the SPARK → Pattern Lock → the
# Cut loop runs, expressed as the per-phase convergence caps (+ the Cut's
# cross-vendor prematurity audit). ``standard`` is the shipped default and is
# unchanged. ``shallow`` is a quick answer *without full SPARK divergence*
# (§10 of the input-typing design): one divergent shot plus a single
# expand-fold, a single filter pass, and a direct commit — no integrity reopen.
# ``deep`` widens the field and pressure-tests the commitment harder for
# high-stakes coverage. Depth only shapes the Proposer; the Resolver is
# unaffected.
PROPOSER_DEPTHS = ("shallow", "standard", "deep")

_PROPOSER_BY_DEPTH: dict[str, list[dict]] = {
    "shallow": [
        {"stage": "spark", "config": {"cap": 2}},
        {"stage": "pattern_lock", "config": {"cap": 1}},
        {"stage": "cut", "config": {"cap": 1}},  # no integrity_check — the quick path
    ],
    "standard": [
        {"stage": "spark", "config": {"cap": 5}},
        {"stage": "pattern_lock", "config": {"cap": 3}},
        {"stage": "cut", "config": {"cap": 2, "integrity_check": True}},
    ],
    "deep": [
        {"stage": "spark", "config": {"cap": 8}},
        {"stage": "pattern_lock", "config": {"cap": 3}},
        {"stage": "cut", "config": {"cap": 3, "integrity_check": True}},
    ],
}

# Back-compat alias: the canonical default Proposer stage set.
_PROPOSER = _PROPOSER_BY_DEPTH["standard"]


def _proposer(seed: list[str] | None = None, depth: str = "standard") -> list[dict]:
    """The Proposer stages at the requested ``depth`` (shallow | standard | deep),
    freshly copied (never the shared module-level dicts), with an optional
    ``seed`` injected into SPARK's config. A seed is a candidate option dropped
    into SPARK's field — a Frame anchor for a hybrid/leaning input (or a demoted
    G2-fail proposition). It is not privileged; it survives only if it wins
    Pattern Lock + the Cut on the merits. Depth and seed compose freely."""
    if depth not in _PROPOSER_BY_DEPTH:
        raise ValueError(f"unknown proposer depth '{depth}'; known: {list(PROPOSER_DEPTHS)}")
    stages = [dict(s, config=dict(s["config"])) for s in _PROPOSER_BY_DEPTH[depth]]
    if seed:
        stages[0]["config"]["seed"] = list(seed)
    return stages


# The front-door classifier. Judgment-only + short output, and deterministic
# (temperature 0) so a classification is stable across runs. Vendor is remapped
# to an available one when the named key is absent (BYO-any-vendor).
_FRAMER = {"vendor": "anthropic", "model": "claude-opus-4-8", "max_tokens": 1024, "temperature": 0}

VERIFY_MODES = ("off", "gate", "round")

# The reconcile stage's client. It EXTRACTS numbers and never judges them, so it is
# deterministic (temperature 0) and needs only enough headroom for a list of values with
# their quotes. Vendor is remapped to an available one when the named key is absent.
_RECONCILER = {"vendor": "anthropic", "model": "claude-opus-4-8", "max_tokens": 4000, "temperature": 0}

RECONCILE_MODES = ("off", "gate")


def _review_round() -> list[dict]:
    return [
        {"stage": "cross_review", "config": {}},
        {"stage": "anchored_fix", "roles": {"author": _AUTHOR}},
        {"stage": "definitiveness_gate", "config": {}},
    ]


def _citation_gate() -> dict:
    return {"stage": "citation_gate", "config": {"judge": False}}


def _resolver(settle: int = 0, verify: str = "gate", reconcile: str = "gate") -> list[dict]:
    if verify not in VERIFY_MODES:
        raise ValueError(f"unknown verify mode '{verify}'; known: {list(VERIFY_MODES)}")
    if reconcile not in RECONCILE_MODES:
        raise ValueError(f"unknown reconcile mode '{reconcile}'; known: {list(RECONCILE_MODES)}")
    stages: list[dict] = [{"stage": "author", "roles": {"author": _AUTHOR}}]
    for _ in range(1 + max(0, int(settle))):
        stages += _review_round()
        if verify == "round":
            stages.append(_citation_gate())
    if reconcile == "gate":
        stages.append({"stage": "reconcile", "roles": {"author": _RECONCILER}})
    if verify == "gate":
        stages.append(_citation_gate())
    stages.append({"stage": "compose", "config": {}})
    return stages


def _build(name: str, settle: int = 0, verify: str = "gate", seed: list[str] | None = None,
           depth: str = "standard", reconcile: str = "gate") -> dict:
    if name == "spar":  # Resolver-only — no SPARK, so seed/depth are no-ops
        return {"name": "spar", "pipeline": _resolver(settle, verify, reconcile)}
    if name == "sparring":
        return {
            "name": "sparring",
            "policy": {"independence": "preferred"},
            "pipeline": _proposer(seed, depth) + _resolver(settle, verify, reconcile),
        }
    if name == "proposer":  # settle/verify are resolver-only — no-ops here
        return {"name": "proposer", "pipeline": _proposer(seed, depth)}
    if name == "frame":  # front-door classifier only — settle/verify/seed/depth are no-ops
        return {"name": "frame", "pipeline": [{"stage": "frame", "roles": {"framer": _FRAMER}}]}
    raise KeyError(f"unknown preset '{name}'; known: ['frame', 'proposer', 'spar', 'sparring']")


# The canonical default manifests (settle=0, verify=gate) — these mirror
# `manifests/*.yaml`. Kept as a dict so callers can enumerate the preset names.
PRESETS: dict[str, dict] = {name: _build(name) for name in ("spar", "sparring", "proposer", "frame")}


def load_preset(name: str, settle: int = 0, verify: str = "gate", seed: list[str] | None = None,
                depth: str = "standard", reconcile: str = "gate"):
    from .manifest import Manifest

    if name not in PRESETS:
        raise KeyError(f"unknown preset '{name}'; known: {sorted(PRESETS)}")
    return Manifest.from_dict(_build(name, settle=settle, verify=verify, seed=seed, depth=depth,
                                     reconcile=reconcile))
