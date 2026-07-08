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


def _reconcile_definitiveness_with_grounding(env: Envelope, verdicts: list) -> None:
    """Make the two gates agree instead of contradict.

    The definitiveness gate (which ran first) flags every hard specific not
    traceable to the user's givens as an assumption-to-verify — including a CVE
    the answer brought in from external knowledge. But if the grounding gate then
    VERIFIES that same reference against a public registry, it is a confirmed
    external fact, not an open assumption: drop it from the failures/rider (which
    can lift the verdict) and re-file it as 'confirmed real, applicability is
    yours to confirm'. A REFUTED reference stays a failure — a hallucinated CVE is
    worse, not better.
    """
    defin = env.meta.get("definitiveness") or {}
    rows = defin.get("rows")
    if not rows:
        return
    verified = [
        (str(v.get("artifact_ref") or "").lower(), v)
        for v in (verdicts or [])
        if (v.get("status") or v.get("grounding") or "").lower() in ("verified", "grounded_verified")
    ]
    verified = [(r, v) for r, v in verified if r]
    if not verified:
        return

    def _hit(text: str):
        t = (text or "").lower()
        for ref, v in verified:
            if ref in t:
                return v
        return None

    grounded_specs = []
    for r in rows:
        v = _hit(r.get("text"))
        if v:
            grounded_specs.append({
                "text": r.get("text"), "claim": r.get("claim"),
                "ref": v.get("artifact_ref"), "backend": v.get("backend"),
            })
    if not grounded_specs:
        return  # nothing the grounding gate can reconcile

    def _is_g(r):
        return _hit(r.get("text")) is not None

    fails = [r for r in rows if r.get("bucket") == "BUCKET3_FAIL" and not _is_g(r)]
    splits = [r for r in rows if r.get("bucket") == "REVIEW_SPLIT" and not _is_g(r)]
    verdict = "FAIL" if fails else ("REVIEW" if splits else "PASS")

    defin["answer_verdict"] = verdict
    defin["failures"] = [f for f in (defin.get("failures") or []) if not _hit(f.get("text"))]
    defin["rider_lines"] = [r["rider"] for r in rows if r.get("rider") and not _is_g(r)]
    defin["grounded_specifics"] = grounded_specs
    defin["reconciled_against_grounding"] = True
    env.meta["definitiveness"] = defin

    env.verdict = verdict
    rider = dict(env.trust_rider or {})
    rider["assumptions_to_verify"] = defin["rider_lines"]
    rider["overall_confidence"] = verdict
    rider["verified_externally"] = grounded_specs
    env.trust_rider = rider
    env.add_trace(
        "citation_gate", "reconcile",
        f"reconciled {len(grounded_specs)} grounded specific(s) out of the "
        f"definitiveness failures; verdict -> {verdict}",
    )


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
        # Reconcile: any specific the definitiveness gate flagged as an unstated
        # assumption but grounding just VERIFIED is a confirmed external fact.
        _reconcile_definitiveness_with_grounding(env, verdicts)
        return env
