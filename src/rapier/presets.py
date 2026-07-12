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

_PROPOSER = [
    {"stage": "spark", "config": {"cap": 5}},
    {"stage": "pattern_lock", "config": {"cap": 3}},
    {"stage": "cut", "config": {"cap": 2, "integrity_check": True}},
]

# The front-door classifier. Judgment-only + short output, and deterministic
# (temperature 0) so a classification is stable across runs. Vendor is remapped
# to an available one when the named key is absent (BYO-any-vendor).
_FRAMER = {"vendor": "anthropic", "model": "claude-opus-4-8", "max_tokens": 1024, "temperature": 0}

VERIFY_MODES = ("off", "gate", "round")


def _review_round() -> list[dict]:
    return [
        {"stage": "cross_review", "config": {}},
        {"stage": "anchored_fix", "roles": {"author": _AUTHOR}},
        {"stage": "definitiveness_gate", "config": {}},
    ]


def _citation_gate() -> dict:
    return {"stage": "citation_gate", "config": {"judge": False}}


def _resolver(settle: int = 0, verify: str = "gate") -> list[dict]:
    if verify not in VERIFY_MODES:
        raise ValueError(f"unknown verify mode '{verify}'; known: {list(VERIFY_MODES)}")
    stages: list[dict] = [{"stage": "author", "roles": {"author": _AUTHOR}}]
    for _ in range(1 + max(0, int(settle))):
        stages += _review_round()
        if verify == "round":
            stages.append(_citation_gate())
    if verify == "gate":
        stages.append(_citation_gate())
    stages.append({"stage": "compose", "config": {}})
    return stages


def _build(name: str, settle: int = 0, verify: str = "gate") -> dict:
    if name == "spar":
        return {"name": "spar", "pipeline": _resolver(settle, verify)}
    if name == "sparring":
        return {
            "name": "sparring",
            "policy": {"independence": "preferred"},
            "pipeline": list(_PROPOSER) + _resolver(settle, verify),
        }
    if name == "proposer":  # settle/verify are resolver-only — no-ops here
        return {"name": "proposer", "pipeline": list(_PROPOSER)}
    if name == "frame":  # front-door classifier only — settle/verify are no-ops
        return {"name": "frame", "pipeline": [{"stage": "frame", "roles": {"framer": _FRAMER}}]}
    raise KeyError(f"unknown preset '{name}'; known: ['frame', 'proposer', 'spar', 'sparring']")


# The canonical default manifests (settle=0, verify=gate) — these mirror
# `manifests/*.yaml`. Kept as a dict so callers can enumerate the preset names.
PRESETS: dict[str, dict] = {name: _build(name) for name in ("spar", "sparring", "proposer", "frame")}


def load_preset(name: str, settle: int = 0, verify: str = "gate"):
    from .manifest import Manifest

    if name not in PRESETS:
        raise KeyError(f"unknown preset '{name}'; known: {sorted(PRESETS)}")
    return Manifest.from_dict(_build(name, settle=settle, verify=verify))
