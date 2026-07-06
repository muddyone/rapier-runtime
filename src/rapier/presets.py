"""Built-in ceremony presets — the manifests the `/spar` and `/sparring`
adapters select. Embedded (not file paths) so they work when pip-installed.
The `manifests/*.yaml` files mirror these for human reference.
"""
from __future__ import annotations

_AUTHOR = {"vendor": "anthropic", "model": "claude-opus-4-8"}

_RESOLVER = [
    {"stage": "author", "roles": {"author": _AUTHOR}},
    {"stage": "cross_review", "config": {}},
    {"stage": "anchored_fix", "roles": {"author": _AUTHOR}},
    {"stage": "definitiveness_gate", "config": {}},
    {"stage": "citation_gate", "config": {"judge": False}},
    {"stage": "compose", "config": {}},
]

_PROPOSER = [
    {"stage": "spark", "config": {"cap": 5}},
    {"stage": "pattern_lock", "config": {"cap": 3}},
    {"stage": "cut", "config": {"cap": 2, "integrity_check": True}},
]

PRESETS: dict[str, dict] = {
    # Resolver only — the /spar ceremony.
    "spar": {"name": "spar", "pipeline": _RESOLVER},
    # Full ceremony — Proposer -> Resolver -> compose (the /sparring ceremony).
    "sparring": {
        "name": "sparring",
        "policy": {"independence": "preferred"},
        "pipeline": _PROPOSER + _RESOLVER,
    },
    # Proposer only.
    "proposer": {"name": "proposer", "pipeline": _PROPOSER},
}


def load_preset(name: str):
    from .manifest import Manifest

    if name not in PRESETS:
        raise KeyError(f"unknown preset '{name}'; known: {sorted(PRESETS)}")
    return Manifest.from_dict(PRESETS[name])
