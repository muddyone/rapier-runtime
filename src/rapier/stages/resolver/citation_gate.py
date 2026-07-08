"""Citation gate stage — external-canon grounding over the recommendation's artifacts.

Wraps the single shared verification service (which wraps the vendored grounding
stack). It verifies the load-bearing artifacts on ``env.meta['artifacts']`` — a
CWE / DOI / RFC / URL / ``path:line`` / ``#N`` pack-fact each — against external
canon (MITRE, Crossref, the IETF datatracker, URL liveness) with no model in the
loop, and records the gate decision.

If nothing upstream supplied artifacts, the gate now extracts them from the
recommendation text itself (``_extract.extract_artifacts``), so a normal
``spar`` / ``sparring`` run actually grounds the answer's own citations. It still
skips cleanly when the answer cites nothing checkable, and a caller may still
pre-populate ``env.meta['artifacts']`` to override extraction.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from ._extract import extract_artifacts


@register_stage("citation_gate")
class CitationGateStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        artifacts = env.meta.get("artifacts")
        if not artifacts:
            artifacts = extract_artifacts(env.recommendation)
            if artifacts:
                env.meta["artifacts"] = artifacts
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
            f"theater={summary.get('theater_flags')} "
            f"n={len(artifacts)}",
        )
        return env
