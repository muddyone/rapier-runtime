"""Cross-review stage — one independent, different-vendor review pass.

Wraps the vendored ``spar_cross_review.review`` (unchanged logic → parity by
construction). Reads the pack (``env.request``) + the recommendation, and writes
the reviewer's objections and cross-vendor flag to ``env.meta['review']`` for
the anchored-correction stage to act on.
"""
from __future__ import annotations

import contextlib
import io
import sys

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage


@register_stage("cross_review")
class CrossReviewStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        if not env.recommendation:
            env.add_trace("cross_review", self.kind, "no recommendation to review — skipped")
            return env
        from ...verify import _bootstrap as B
        from ._binding import bind_pair

        # Bind the vendored reviewer to a distinct second vendor (V4).
        primary_v, secondary_v = bind_pair(
            env, secondary_pref=ctx.config.get("reviewer_vendor"), policy=ctx.policy
        )
        # The vendored reviewer names its two slots after Anthropic and OpenAI, so
        # its fallback warning ("OPENAI_API_KEY absent; falling back to a Claude
        # reviewer") is wrong once the slots are bound to other vendors. Capture
        # it and say what actually happened instead; other stderr passes through.
        captured = io.StringIO()
        try:
            with contextlib.redirect_stderr(captured):
                result = B.review(env.request, env.recommendation, None, None)
        finally:
            B.reset_slots()
        for line in captured.getvalue().splitlines():
            if line.startswith("WARN spar-cross-review:") and not result.get("cross_vendor"):
                if result.get("reviewer_vendor") is None:
                    continue  # no keys at all; the pipeline's own preflight reports that
                line = (f"WARN cross_review: no second vendor available; the reviewer runs on "
                        f"{primary_v}, the author's vendor (NOT cross-vendor).")
            sys.stderr.write(line + "\n")

        env.meta["review"] = result
        n = len(result.get("objections") or [])
        reviewer_v = secondary_v if result.get("cross_vendor") else primary_v
        env.add_trace(
            "cross_review",
            self.kind,
            f"author={primary_v} reviewer={reviewer_v} "
            f"cross_vendor={result.get('cross_vendor')} objections={n}",
            cross_vendor=result.get("cross_vendor"),
            n_objections=n,
            author_vendor=primary_v,
            reviewer_vendor=reviewer_v,
        )
        return env
