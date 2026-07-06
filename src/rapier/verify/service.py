"""The single shared verification service.

One clean API over the vendored grounding + citation-gate stack. This is the
*one canonical verifier* — the M1 collapse of the former pilot-vs-loom
two-copies split. Both the Resolver's citation gate and (in M2) the Proposer's
Cut call this, so there is exactly one copy of the grounding logic.

The heavy vendored stack (and its ``requests`` dependency) is imported lazily,
so ``import rapier`` stays light and network-capable code loads only when a
verification actually runs.
"""
from __future__ import annotations

from typing import Any


def verify_artifacts(
    artifacts: list[dict[str, Any]],
    pack_text: str | None = None,
    judge: bool = False,
    map_claims: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the external-canon citation gate over a list of artifacts.

    Returns ``(verdicts, summary)`` where summary carries
    ``gate`` (clean|flagged|blocked), ``grounding_rate``, ``theater_flags``.
    """
    from ._bootstrap import verify_run

    return verify_run(artifacts, pack_text, judge, map_claims)


def verify_one(
    concern: dict[str, Any], judge: bool = False, map_claims: bool = False
) -> dict[str, Any]:
    """Resolve a single grounding concern against external truth."""
    from ._bootstrap import verify_concern

    return verify_concern(concern, judge, map_claims)


def keys_present() -> dict[str, bool]:
    """Which vendor keys are available in the environment (for honest fail-soft)."""
    from ._bootstrap import keys_present as _kp

    return _kp()
