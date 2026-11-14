"""Load the vendored SPARRING Resolver stack in isolation, and back its LLM
calls with Rapier's model layer (V4).

The vendored scripts import siblings by bare name and ``verify_grounding``
locates ``cite_check`` via ``CITE_CHECK_PY``. This wires both to the vendored
copies (no runtime loom dependency), and — crucially — installs a Rapier-backed
``lib_llm`` shim into ``sys.modules`` *before* the reviewer/gate import it, so
their two vendor slots can be bound to any Rapier vendor (``bind_slots``).
Unbound / anthropic+openai, the shim delegates to the original verbatim (parity).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys

_VENDOR_DIR = os.path.join(os.path.dirname(__file__), "_vendor")

os.environ.setdefault("CITE_CHECK_PY", os.path.join(_VENDOR_DIR, "cite_check.py"))
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

# The grounding stack does not use lib_llm — import it directly.
verify_grounding = importlib.import_module("verify_grounding")
spar_verify_gate = importlib.import_module("spar_verify_gate")

# Load the original vendored lib_llm privately (its pure utilities + the verbatim
# anthropic/openai clients the shim delegates to), then install the shim as the
# module named 'lib_llm' so the reviewer/gate pick it up.
_spec = importlib.util.spec_from_file_location(
    "_rapier_vendored_lib_llm", os.path.join(_VENDOR_DIR, "lib_llm.py")
)
_orig_lib_llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_orig_lib_llm)

from . import _llm_shim  # noqa: E402

_llm_shim.install(_orig_lib_llm)
sys.modules["lib_llm"] = _llm_shim

# Now the reviewer/gate resolve `import lib_llm` to the shim.
spar_cross_review = importlib.import_module("spar_cross_review")
spar_definitiveness_gate = importlib.import_module("spar_definitiveness_gate")

# The vendored stack also has vendor callers that do NOT go through Rapier's
# model layer: cite_check's call_anthropic/call_openai (used by the grounding
# judge, the claim mapper and cite_check's own extraction) and the original
# lib_llm's clients. Their spend can't be held to a dollar cap, so under a cap
# every one of them refuses (rr-1 S-6). Patched on the module objects, so calls
# made from inside the module (by global name) are guarded too.
def _refuse_when_capped(name, fn):
    import functools

    @functools.wraps(fn)
    def guarded(*a, **k):
        from .. import budget

        if budget.active():
            raise budget.BudgetExceeded(
                f"{name} calls a model outside Rapier's model layer, so a capped run can't use it")
        return fn(*a, **k)
    guarded._rapier_capped_guard = True
    return guarded


_CC = getattr(verify_grounding, "CC", None)
for _mod, _names in ((_CC, ("call_anthropic", "call_openai")),
                     (_orig_lib_llm, ("call_claude", "call_gpt", "claude_json", "gpt_json"))):
    if _mod is None:
        continue
    for _n in _names:
        _f = getattr(_mod, _n, None)
        if callable(_f) and not getattr(_f, "_rapier_capped_guard", False):
            setattr(_mod, _n, _refuse_when_capped(f"{getattr(_mod, '__name__', 'vendored')}.{_n}", _f))
if _CC is not None and isinstance(getattr(_CC, "SUBSTRATES", None), dict):
    _CC.SUBSTRATES = {k: getattr(_CC, v.__name__, v) for k, v in _CC.SUBSTRATES.items()}

# Library entry points used by the Resolver stages.
lib_llm = _llm_shim
review = spar_cross_review.review                 # (problem, recommendation, prev, vendor) -> dict
run_gate = spar_definitiveness_gate.run_gate       # (problem, recommendation) -> dict
verify_run = spar_verify_gate.run                  # (artifacts, pack_text, judge, map_claims) -> (verdicts, summary)
verify_concern = verify_grounding.verify_concern   # (concern, judge, map_claims) -> dict
keys_present = _llm_shim.keys_present              # () -> {"anthropic": bool, "openai": bool}  (slot availability)
bind_slots = _llm_shim.bind_slots                  # (primary, secondary) -> None
reset_slots = _llm_shim.reset_slots                # () -> None

__all__ = [
    "lib_llm",
    "verify_grounding",
    "review",
    "run_gate",
    "verify_run",
    "verify_concern",
    "keys_present",
    "bind_slots",
    "reset_slots",
]
