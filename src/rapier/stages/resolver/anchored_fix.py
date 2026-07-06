"""Anchored-correction stage — revise the recommendation in place, once.

Native model-call stage. Hands the author (same ``author`` role client) the
cross-vendor reviewer's objections and requires the minimal change that fixes
the material ones — no restart from scratch, no unforced new flaws. Holds when
there are no objections to act on.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage

_SYSTEM = """You are revising a recommendation under a cross-vendor review in a \
SPARRING resolver ceremony. Fix every material, valid objection with the \
minimal change — revise IN PLACE, do not restart from scratch, do not introduce \
unforced new flaws. Hold your position on an objection that is wrong or \
immaterial, stating why briefly. Return the full revised recommendation only."""


@register_stage("anchored_fix")
class AnchoredFixStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        client = ctx.clients.get("author")
        review = env.meta.get("review") or {}
        objections = review.get("objections") or []
        if client is None or not objections:
            env.add_trace(
                "anchored_fix",
                self.kind,
                "no client or no objections — held",
                n_objections=len(objections),
            )
            return env
        obj_text = "\n".join(
            f"- [{o.get('handle', '')}] {o.get('text', '')}" for o in objections
        )
        prompt = (
            f"PACK:\n{env.request}\n\n"
            f"CURRENT RECOMMENDATION:\n{env.recommendation}\n\n"
            f"MATERIAL OBJECTIONS FROM THE CROSS-VENDOR REVIEW:\n{obj_text}\n\n"
            "Revise the recommendation in place per the rules above."
        )
        resp = client.complete(system=_SYSTEM, prompt=prompt)
        env.recommendation = resp.text
        env.add_trace(
            "anchored_fix",
            self.kind,
            f"revised for {len(objections)} objection(s)",
            chars=len(resp.text or ""),
        )
        return env
