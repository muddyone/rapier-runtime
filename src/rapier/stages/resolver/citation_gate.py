"""Citation gate stage — external-canon grounding over the recommendation's artifacts.

Wraps the single shared verification service (which wraps the vendored grounding
stack). Consumes load-bearing artifacts on ``env.meta['artifacts']`` (a CWE /
DOI / ``#N`` pack-fact / ``path:line`` each) and records the gate decision.

Automatic extraction of artifacts from the recommendation text is a later
refinement; in M1 the gate verifies whatever artifacts are present and skips
cleanly when there are none.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage


@register_stage("citation_gate")
class CitationGateStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        artifacts = env.meta.get("artifacts") or []
        if not artifacts:
            env.add_trace("citation_gate", self.kind, "no artifacts to verify — skipped")
            return env
        from ...verify import service

        judge = bool(ctx.config.get("judge"))
        map_claims = bool(ctx.config.get("map_claims"))
        verdicts, summary = service.verify_artifacts(
            artifacts, pack_text=env.request, judge=judge, map_claims=map_claims
        )
        env.meta["citation_gate"] = summary
        env.meta["citation_verdicts"] = verdicts
        env.add_trace(
            "citation_gate",
            self.kind,
            f"gate={summary.get('gate')} "
            f"grounding_rate={summary.get('grounding_rate')} "
            f"theater={summary.get('theater_flags')}",
        )
        return env
