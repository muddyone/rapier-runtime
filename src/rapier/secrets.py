"""Secret handling — env-only reads, and redaction of anything that leaks.

Two guarantees, both part of the M0 security exit criterion:

1. Secrets are read from the environment ONLY. Never from a file, never a
   hardcoded default. (Threat model: secrets exposure.)
2. Any string that is about to be logged, traced, or persisted is passed
   through :func:`redact`, which scrubs both known secret *values* (registered
   as they are read) and common key *patterns* defensively.
"""
from __future__ import annotations

import os
import re

# Concrete secret values seen at runtime that must never appear in output.
_REGISTERED: set[str] = set()

# Defensive patterns for common key shapes — redacted even if never registered.
_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),  # Anthropic
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),      # OpenAI-style
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),     # Google
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack
]

_REDACTED = "***REDACTED***"


def register_secret_value(value: str | None) -> None:
    """Mark a concrete value as secret so :func:`redact` will scrub it."""
    if value and len(value) >= 8:
        _REGISTERED.add(value)


def get_secret(name: str) -> str | None:
    """Read a secret from the environment only. Registers it for redaction."""
    value = os.environ.get(name)
    if value:
        register_secret_value(value)
    return value


def require_secret(name: str) -> str:
    """Like :func:`get_secret` but raises if the secret is unset."""
    value = get_secret(name)
    if not value:
        raise RuntimeError(f"required secret '{name}' is not set in the environment")
    return value


def redact(text: str) -> str:
    """Scrub registered secret values and known key patterns from a string."""
    if not text:
        return text
    for value in _REGISTERED:
        text = text.replace(value, _REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def redact_obj(obj):
    """Recursively redact every string inside a JSON-like structure."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
