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

__version__ = "0.0.1"

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
    "build_client",
    "available_vendors",
    "__version__",
]
