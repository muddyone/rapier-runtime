"""The Proposer stages — SPARK -> Pattern Lock -> the Cut.

Importing this package registers the three convergence stages.
"""
from . import phase_stages  # noqa: F401  (registers spark / pattern_lock / cut)
from . import traceability_gate  # noqa: F401  (registers the Proposer-side grounding gate)

__all__ = ["phase_stages", "traceability_gate"]
