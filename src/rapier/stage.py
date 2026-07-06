"""The Stage contract and the stage registry.

Every module in a Rapier pipeline is a Stage with the uniform interface
``run(envelope, ctx) -> envelope``. There are two kinds:

* ``TransformStage`` — a (possibly single-model-call) deterministic transform.
  The Resolver's modules are these.
* ``ConvergenceStage`` — a two-agent Generator×Challenger loop. The Proposer's
  SPARK / Pattern Lock / the Cut are these. The convergence primitive itself is
  built in M2; M0 only fixes the interface.

Control flow lives in code (the Pipeline); content/model-bindings live in
config (the manifest); only genuine judgment is delegated to a model call.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from .envelope import Envelope

TRANSFORM = "transform"
CONVERGENCE = "convergence"


@dataclass
class StageContext:
    """Everything a stage needs, injected — so stages never reach for globals.

    ``clients`` maps a role name (e.g. ``"generator"``, ``"challenger"``,
    ``"author"``) to a bound model client. Cross-vendor is simply two roles
    pointing at different vendors in the manifest.
    """

    config: dict[str, Any] = field(default_factory=dict)
    clients: dict[str, Any] = field(default_factory=dict)
    ledger: Any = None
    run_dir: str | None = None
    log: Callable[[str], None] = lambda _m: None
    policy: Any = None  # a models.Policy governing vendor selection (V3); None => default


class Stage(ABC):
    """Uniform pipeline unit. Subclass ``TransformStage`` or ``ConvergenceStage``."""

    name: str = "stage"
    kind: str = TRANSFORM

    @abstractmethod
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        ...


class TransformStage(Stage):
    kind = TRANSFORM


class ConvergenceStage(Stage):
    """Two-agent both-must-agree loop. Interface only in M0.

    The reusable convergence primitive (Generator × Challenger, a shifting
    Challenger function, a self-verified exit condition, a cap) is built in M2.
    """

    kind = CONVERGENCE

    def run(self, env: Envelope, ctx: StageContext) -> Envelope:  # pragma: no cover
        raise NotImplementedError(
            "ConvergenceStage is implemented in M2 (the Proposer build)"
        )


# --- stage registry: name -> class -------------------------------------------
_REGISTRY: dict[str, type[Stage]] = {}


def register_stage(name: str) -> Callable[[type[Stage]], type[Stage]]:
    """Decorator: register a Stage subclass under a manifest-visible name."""

    def deco(cls: type[Stage]) -> type[Stage]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def get_stage(name: str) -> type[Stage]:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown stage '{name}'; registered stages: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def registered_stages() -> dict[str, type[Stage]]:
    return dict(_REGISTRY)
