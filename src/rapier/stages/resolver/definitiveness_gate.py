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
        from ...verify._bootstrap import run_gate

        result = run_gate(env.request, env.recommendation)
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
            f"fails={len(result.get('failures') or [])}",
            cross_vendor=result.get("cross_vendor"),
        )
        return env
