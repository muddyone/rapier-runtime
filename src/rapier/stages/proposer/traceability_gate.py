"""Traceability gate stage — external-canon grounding over the Proposer's own citations.

The Proposer Reference Model puts governance obligations on the *framing* step, not just
on the answer: §10.1 (Traceability) requires every material element to be attributable to
one of four origins, and §13.1 (the Traceability Obligation) makes that a conformance
requirement. Until this stage existed, nothing checked any of it — verification lived
entirely on the Resolver side (``resolver/citation_gate.py``), so a Proposer could cite a
source that does not exist and the run would report clean.

This stage closes the half of that obligation which is mechanically checkable: every
artifact the committed proposition (and the surviving options) cites is resolved against
external canon — Crossref, arXiv, CourtListener, Google Patents, MITRE, the IETF
datatracker, URL liveness — with no model in the loop.

**Honest scope.** This is NOT a full §10.1 conformance check, and must not be reported as
one. §10.1 has four origins (The Ask, a stated reading, an identifiable source, an explicit
assumption) and this stage can only speak to the third: does a cited source exist, and (with
``judge``) does it support what it is cited for. Checking that *every* material element
carries some origin — the part that would actually catch provenance laundering, per
Observation O-001 — needs a structured, machine-readable Trust Rider, which the Proposer
does not yet emit. What this gate proves is narrower than the obligation, and the summary
says so via ``scope``.

A REFUTED artifact is the serious outcome here: the representation downstream reasoning is
about to receive cites something fabricated. That is worse in the Proposer than in the
Resolver, because every later gate inherits the frame.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from ..resolver._extract import extract_artifacts


def _proposer_text(env: Envelope) -> str:
    """Everything the Proposer committed to, plus the field it chose from.

    Options are included because a standing objection often attaches to an option that
    lost; a fabricated citation in the field is still a defect in the Proposer's work.
    """
    parts: list[str] = []
    if env.committed:
        parts.append(str(env.committed))
    for opt in env.options or []:
        if opt:
            parts.append(str(opt))
    return "\n\n".join(parts)


@register_stage("traceability_gate")
class TraceabilityGateStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        artifacts = env.meta.get("proposer_artifacts")
        if not artifacts:
            artifacts = extract_artifacts(_proposer_text(env))
            if artifacts:
                env.meta["proposer_artifacts"] = artifacts
        if not artifacts:
            env.add_trace(
                "traceability_gate",
                self.kind,
                "proposition cites nothing checkable — skipped",
            )
            return env

        from ...verify import service

        judge = bool(ctx.config.get("judge"))
        verdicts, summary = service.verify_artifacts(
            artifacts, pack_text=env.request, judge=judge, map_claims=False
        )
        summary = dict(summary)
        summary["scope"] = (
            "source-origin only (RM 13.1): cited sources resolved against external canon. "
            "Does NOT verify that every material element carries an origin — that needs a "
            "structured Trust Rider the Proposer does not yet emit."
        )
        env.meta["traceability_gate"] = summary
        env.meta["traceability_verdicts"] = verdicts
        env.add_trace(
            "traceability_gate",
            self.kind,
            f"gate={summary.get('gate')} "
            f"grounding_rate={summary.get('grounding_rate')} "
            f"theater={summary.get('theater_flags')} "
            f"n={len(artifacts)}",
        )
        return env
