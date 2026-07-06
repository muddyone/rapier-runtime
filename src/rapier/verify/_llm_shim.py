"""A ``lib_llm``-compatible module backed by Rapier's model layer (V4).

The vendored SPARRING reviewer/gate import ``lib_llm`` and drive everything off
two "slots" — the primary (historically Claude) and the secondary/reviewer
(historically GPT) — plus ``keys_present()``. This shim presents that exact
surface, but the two slots can be **bound to any Rapier vendor**. That is what
makes the gate/reviewer vendor-agnostic (Gemini author + Grok reviewer, no
Anthropic) and lets them degrade honestly to a single vendor instead of going
``unchecked``.

Parity discipline: **unbound, or bound to anthropic/openai, every call delegates
verbatim to the original vendored ``lib_llm``** — so the default path is
byte-identical to shipped ``/spar``. Only a slot bound to a *different* vendor
takes the Rapier code path.
"""
from __future__ import annotations

import os

# Set by install() from the original vendored lib_llm (pure utilities + the
# verbatim anthropic/openai clients we delegate to for parity).
_orig = None
LLMError = RuntimeError
CLAUDE_MODEL = "claude-opus-4-8"
GPT_MODEL = "gpt-5.2"

_JSON_SUFFIX = "\n\nRespond with STRICT JSON only — no prose, no markdown fences."

# Per-run slot bindings. _BOUND=False => behave exactly like the original.
_BOUND = False
_PRIMARY: tuple[str, str] | None = None      # (vendor, model) or None
_SECONDARY: tuple[str, str] | None = None    # (vendor, model) or None


def install(orig_lib_llm) -> None:
    global _orig, LLMError, CLAUDE_MODEL, GPT_MODEL
    _orig = orig_lib_llm
    LLMError = orig_lib_llm.LLMError
    CLAUDE_MODEL = orig_lib_llm.CLAUDE_MODEL
    GPT_MODEL = orig_lib_llm.GPT_MODEL


def bind_slots(primary: tuple[str, str] | None, secondary: tuple[str, str] | None) -> None:
    """Bind the two slots to Rapier ``(vendor, model)`` tuples for one run.

    ``secondary=None`` means no distinct second vendor is available → the
    scripts' own logic degrades to single-vendor (cross_vendor=False), never
    to ``unchecked``.
    """
    global _BOUND, _PRIMARY, _SECONDARY
    _BOUND, _PRIMARY, _SECONDARY = True, primary, secondary


def reset_slots() -> None:
    global _BOUND, _PRIMARY, _SECONDARY
    _BOUND, _PRIMARY, _SECONDARY = False, None, None


def _slot(which: str) -> tuple[str, str]:
    if not _BOUND:
        return ("anthropic", CLAUDE_MODEL) if which == "primary" else ("openai", GPT_MODEL)
    binding = _PRIMARY if which == "primary" else _SECONDARY
    # A bound-but-None secondary should never be *called* (keys_present reports
    # it absent), but fall back safely if it is.
    return binding or (("anthropic", CLAUDE_MODEL) if which == "primary" else ("openai", GPT_MODEL))


def extract_json(text):
    return _orig.extract_json(text)


def have_key(name: str) -> bool:
    return bool(os.environ.get(name))


def keys_present() -> dict[str, bool]:
    """Slot availability. 'anthropic' = primary slot, 'openai' = secondary slot.

    Unbound: the real ANTHROPIC/OPENAI env keys (parity). Bound: whether each
    slot's mapped vendor has a key (secondary=None -> False -> single-vendor).
    """
    if not _BOUND:
        return _orig.keys_present()
    return {"anthropic": _vendor_available(_PRIMARY), "openai": _vendor_available(_SECONDARY)}


def _vendor_available(binding: tuple[str, str] | None) -> bool:
    if not binding:
        return False
    from ..models import _VENDOR_KEY_ENV

    key_env = _VENDOR_KEY_ENV.get(binding[0])
    return True if key_env is None else bool(os.environ.get(key_env))  # None => local/no-key vendor


# Floor for Rapier-routed calls: thinking models (e.g. Gemini 2.5) spend output
# tokens on internal reasoning before emitting JSON, so the vendored scripts'
# modest caps (2000–4000) truncate them. Give generous headroom.
_MIN_OUTPUT_TOKENS = 8000


def _rapier_call(vendor: str, model: str, system: str, user: str, max_tokens: int) -> str:
    from ..models import ModelSpec, build_client

    mt = max(max_tokens, _MIN_OUTPUT_TOKENS)
    client = build_client(ModelSpec(vendor=vendor, model=model, max_tokens=mt))
    return client.complete(system, user).text


def call_claude(model, system, user, max_tokens=4000, temperature=None):
    vendor, m = _slot("primary")
    if vendor == "anthropic":
        return _orig.call_claude(m, system, user, max_tokens=max_tokens)
    return _rapier_call(vendor, m, system, user, max_tokens)


def call_gpt(model, system, user, max_completion_tokens=16000):
    vendor, m = _slot("secondary")
    if vendor == "openai":
        return _orig.call_gpt(m, system, user, max_completion_tokens=max_completion_tokens)
    return _rapier_call(vendor, m, system, user, max_completion_tokens)


def claude_json(model, system, user, max_tokens=4000, temperature=0.7):
    vendor, m = _slot("primary")
    if vendor == "anthropic":
        return _orig.claude_json(m, system, user, max_tokens, temperature)
    return extract_json(_rapier_call(vendor, m, system + _JSON_SUFFIX, user, max_tokens))


def gpt_json(model, system, user, max_completion_tokens=16000):
    vendor, m = _slot("secondary")
    if vendor == "openai":
        return _orig.gpt_json(m, system, user, max_completion_tokens)
    return extract_json(_rapier_call(vendor, m, system + _JSON_SUFFIX, user, max_completion_tokens))
