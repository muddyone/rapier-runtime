"""First-run onboarding: vendor-key detection, the ``doctor`` report, and ``init``.

Rapier reads secrets from the environment only (see ``secrets.py`` — a stated
security guarantee). Nothing here weakens that: these helpers never read a secret
from a file and never print a secret *value*. They report which key *env vars* are
set, and write a non-secret ``.env.example`` template the user loads into their
own shell (``set -a; source .env; set +a``), so the engine still only ever reads
the environment.
"""
from __future__ import annotations

import os

from .models import available_vendors, frontier_vendors, vendor_key_envs


def configured_vendors() -> list[str]:
    """Real vendors (excluding the built-in ``mock``) with a key present in env."""
    return [v for v in available_vendors() if v != "mock"]


def _ordered_vendors() -> list[str]:
    """Frontier vendors first (the ones people usually reach for), then the rest."""
    envs = vendor_key_envs()
    front = [v for v in frontier_vendors() if v in envs]
    rest = [v for v in envs if v not in front]
    return front + rest


def preflight_error() -> str | None:
    """A ready-to-print error if no vendor key is configured, else ``None``.

    This is what turns a silent no-keys run into an actionable message.
    """
    if configured_vendors():
        return None
    envs = vendor_key_envs()
    names = ", ".join(envs[v] for v in frontier_vendors() if v in envs)
    return (
        "No AI vendor keys found. Rapier reads keys from the environment.\n"
        f"Set at least one (two or more enables cross-vendor review): {names}.\n"
        "Run `rapier init` to scaffold a .env, or `rapier doctor` to check your setup."
    )


def doctor_report() -> str:
    """Human-readable setup report. Reports env-var *presence* only — never values."""
    envs = vendor_key_envs()
    configured = set(configured_vendors())
    lines = ["Rapier — vendor key check", ""]
    for v in _ordered_vendors():
        mark = "✓" if v in configured else "·"  # ✓ / ·
        tag = " (frontier)" if v in frontier_vendors() else ""
        state = "set" if v in configured else "unset"
        lines.append(f"  {mark} {envs[v]:<20} {v}{tag} — {state}")
    n = len(configured)
    lines.append("")
    if n == 0:
        lines.append("✗ No vendors configured — ceremonies will not run. Set a key above.")
    elif n == 1:
        only = next(iter(configured))
        lines.append(
            f"⚠ One vendor ({only}) — runs proceed, but the review is same-vendor "
            "self-review, not independent. Add a second vendor for cross-vendor review."
        )
    else:
        lines.append(f"✓ {n} vendors configured — cross-vendor review is available.")
    return "\n".join(lines)


_ENV_EXAMPLE_HEADER = """\
# Rapier reads AI vendor keys from the environment only.
# Copy this file to `.env`, fill in the key(s) you have, then load it into your
# shell so Rapier can see them (Rapier never reads this file directly):
#
#     set -a; source .env; set +a
#
# One key works; two or more enables cross-vendor (independent) review.
# Frontier vendors are listed first.
"""


def env_example_text() -> str:
    """The contents of ``.env.example`` — key names only, no values."""
    envs = vendor_key_envs()
    lines = [_ENV_EXAMPLE_HEADER]
    for v in _ordered_vendors():
        tag = "  # frontier" if v in frontier_vendors() else ""
        lines.append(f"{envs[v]}=" + tag)
    return "\n".join(lines) + "\n"


def init(target_dir: str = ".") -> tuple[str, bool, str]:
    """Write ``.env.example`` into ``target_dir`` (never overwrites an existing one).

    Returns ``(path, created, instructions)``. Writes only the non-secret template;
    it does not create or touch a real ``.env``.
    """
    path = os.path.join(target_dir, ".env.example")
    created = not os.path.exists(path)
    if created:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(env_example_text())
    verb = "Wrote" if created else "Found existing"
    instructions = (
        f"{verb} {path}.\n"
        "Next:\n"
        "  1. cp .env.example .env\n"
        "  2. edit .env and fill in the key(s) you have\n"
        "  3. set -a; source .env; set +a\n"
        "  4. rapier doctor        # confirm they're detected"
    )
    return path, created, instructions
