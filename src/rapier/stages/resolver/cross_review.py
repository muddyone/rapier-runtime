"""Cross-review stage — one independent, different-vendor review pass.

Wraps the vendored ``spar_cross_review.review`` (unchanged logic → parity by
construction). Reads the pack (``env.request``) + the recommendation, and writes
the reviewer's objections and cross-vendor flag to ``env.meta['review']`` for
the anchored-correction stage to act on.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage


@register_stage("cross_review")
class CrossReviewStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        if not env.recommendation:
            env.add_trace("cross_review", self.kind, "no recommendation to review — skipped")
            return env
        from ...verify._bootstrap import review as _review

        requested_vendor = ctx.config.get("reviewer")  # 'gpt' | 'claude' | None
        result = _review(env.request, env.recommendation, None, requested_vendor)
        env.meta["review"] = result
        n = len(result.get("objections") or [])
        env.add_trace(
            "cross_review",
            self.kind,
            f"reviewer={result.get('reviewer_vendor')} "
            f"cross_vendor={result.get('cross_vendor')} objections={n}",
            cross_vendor=result.get("cross_vendor"),
            n_objections=n,
        )
        return env
