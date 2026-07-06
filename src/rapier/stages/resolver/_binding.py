"""Resolve the primary/secondary vendor pair and bind the vendored stack's two
LLM slots to them (V4). Shared by the cross-review and definitiveness-gate stages.

The primary defaults to the author's own vendor; the secondary is a *distinct*
available vendor (the cross-vendor reviewer/judge), or None when only one is
available — which the vendored logic then degrades to single-vendor honestly
rather than going ``unchecked``.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...models import Policy, available_vendors, default_model


def bind_pair(
    env: Envelope, secondary_pref: str | None = None, policy: Policy | None = None
) -> tuple[str | None, str | None]:
    from ...verify import _bootstrap as B

    author_vendor = env.meta.get("author_vendor")
    author_model = env.meta.get("author_model")
    primary_v, secondary_v = (policy or Policy()).resolve(
        available_vendors(), primary_pref=author_vendor, secondary_pref=secondary_pref
    )

    primary = None
    if primary_v:
        model = author_model if (primary_v == author_vendor and author_model) else default_model(primary_v)
        primary = (primary_v, model)
    secondary = (secondary_v, default_model(secondary_v)) if secondary_v else None

    B.bind_slots(primary, secondary)
    return primary_v, secondary_v
