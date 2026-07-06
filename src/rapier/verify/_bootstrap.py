"""Load the vendored SPARRING Resolver stack in isolation.

The vendored scripts import their siblings by bare module name (``import
lib_llm``, ``import verify_grounding``) and ``verify_grounding`` locates
``cite_check`` via the ``CITE_CHECK_PY`` env var. This module wires both up to
the vendored copies — so Rapier has **no runtime dependency on the loom tree** —
and exposes the library entry points the Resolver stages call.

Import side effects are contained here; nothing else in Rapier touches the
vendored modules directly.
"""
from __future__ import annotations

import importlib
import os
import sys

_VENDOR_DIR = os.path.join(os.path.dirname(__file__), "_vendor")

# Point verify_grounding at the vendored cite_check before it is imported.
os.environ.setdefault("CITE_CHECK_PY", os.path.join(_VENDOR_DIR, "cite_check.py"))

if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

# Import the vendored modules (their sibling imports now resolve).
lib_llm = importlib.import_module("lib_llm")
verify_grounding = importlib.import_module("verify_grounding")
spar_cross_review = importlib.import_module("spar_cross_review")
spar_definitiveness_gate = importlib.import_module("spar_definitiveness_gate")
spar_verify_gate = importlib.import_module("spar_verify_gate")

# The library entry points the Resolver stages use.
review = spar_cross_review.review                 # (problem, recommendation, prev, vendor) -> dict
run_gate = spar_definitiveness_gate.run_gate       # (problem, recommendation) -> dict
verify_run = spar_verify_gate.run                  # (artifacts, pack_text, judge, map_claims) -> (verdicts, summary)
verify_concern = verify_grounding.verify_concern   # (concern, judge, map_claims) -> dict
keys_present = lib_llm.keys_present                 # () -> {"anthropic": bool, "openai": bool}

__all__ = [
    "lib_llm",
    "verify_grounding",
    "review",
    "run_gate",
    "verify_run",
    "verify_concern",
    "keys_present",
]
