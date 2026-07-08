"""Rapier Runtime — a code-orchestrated engine that runs the SPARRING method.

Rapier executes a SPARRING *method* declared in a YAML manifest: grounded,
cross-vendor adversarial review for AI-in-the-loop decisions. This is M0 — the
skeleton (Envelope, Stage contract, Pipeline controller, model/provider layer,
ledger) proven with a dummy echo stage. The Resolver port (M1) and the Proposer
build (M2) come next.
"""
from __future__ import annotations

from .envelope import Artifact, Envelope, TraceEntry
from .manifest import Manifest
from .models import (
    ModelClient,
    ModelResponse,
    ModelSpec,
    OpenAICompatibleModelClient,
    Policy,
    PolicyError,
    available_vendors,
    build_client,
)
from .pipeline import Pipeline, StageSpec
from .stage import (
    ConvergenceStage,
    Stage,
    StageContext,
    TransformStage,
    get_stage,
    register_stage,
    registered_stages,
)
from . import stages  # noqa: F401  (registers built-in stages on import)

# Single source of truth is pyproject's version; read it from the installed
# package metadata so __version__ can never drift from what was published.
try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("rapier-runtime")
    except PackageNotFoundError:  # running from a source tree, not installed
        __version__ = "0.0.0+source"
except Exception:  # pragma: no cover - defensive
    __version__ = "0.0.0+source"

__all__ = [
    "Envelope",
    "Artifact",
    "TraceEntry",
    "Stage",
    "TransformStage",
    "ConvergenceStage",
    "StageContext",
    "register_stage",
    "get_stage",
    "registered_stages",
    "Manifest",
    "Pipeline",
    "StageSpec",
    "ModelSpec",
    "ModelClient",
    "ModelResponse",
    "OpenAICompatibleModelClient",
    "Policy",
    "PolicyError",
    "build_client",
    "available_vendors",
    "__version__",
]
