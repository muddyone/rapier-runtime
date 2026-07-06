"""Definitiveness gate stage — the v2 correctness check.

Wraps the vendored ``spar_definitiveness_gate.run_gate`` (unchanged logic).
Enumerates every hard specific in the recommendation and buckets it; sets
``env.verdict`` to the ANSWER_VERDICT (PASS/REVIEW/FAIL/unchecked) and seeds
``env.trust_rider`` from the gate's rider lines and the review's objections.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage


@register_stage("definitiveness_gate")
class DefinitivenessGateStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        if not env.recommendation:
            env.add_trace("definitiveness_gate", self.kind, "no recommendation to gate — skipped")
            return env
        from ...verify import _bootstrap as B
        from ._binding import bind_pair

        # Bind the gate's primary (author's vendor) + a distinct second vendor
        # for the cross-vendor union (V4). No Anthropic required.
        primary_v, secondary_v = bind_pair(
            env, secondary_pref=ctx.config.get("second_vendor"), policy=ctx.policy
        )
        try:
            result = B.run_gate(env.request, env.recommendation)
        finally:
            B.reset_slots()
        env.verdict = result.get("answer_verdict")
        env.meta["definitiveness"] = result

        rider = dict(env.trust_rider or {})
        rider["assumptions_to_verify"] = result.get("rider_lines") or []
        rider["overall_confidence"] = env.verdict
        review = env.meta.get("review") or {}
        if review.get("objections"):
            rider["contested_and_resolved"] = [
                o.get("text") for o in review["objections"]
            ]
        env.trust_rider = rider

        env.add_trace(
            "definitiveness_gate",
            self.kind,
            f"verdict={env.verdict} specifics={result.get('n_specifics')} "
            f"fails={len(result.get('failures') or [])} "
            f"primary={primary_v} second={secondary_v} cross_vendor={result.get('cross_vendor')}",
            cross_vendor=result.get("cross_vendor"),
            primary_vendor=primary_v,
            second_vendor=secondary_v,
        )
        return env
