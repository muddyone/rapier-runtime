"""Author stage — write the recommendation on the merits.

Native model-call stage on the M0 model layer: it uses the injected ``author``
role client (Anthropic in the spar manifest). In the original /spar this was
the Claude orchestrator; here it is a first-class stage.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage

_SYSTEM = """You are the author in a SPARRING resolver ceremony. Write a \
recommendation on the merits for the decision described in the pack. State the \
derivation of each load-bearing figure inline (named inputs + the arithmetic) \
so a calculator can re-check traceability — but do not turn the whole answer \
into a worksheet; show the work only for load-bearing specifics. Mark any \
figure you cannot derive from the givens as an estimate/assumption to verify. \
Return the recommendation only."""


@register_stage("author")
class AuthorStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        client = ctx.clients.get("author")
        if client is None:
            env.add_trace("author", self.kind, "no author client bound — skipped")
            return env
        system = ctx.config.get("system", _SYSTEM)
        resp = client.complete(system=system, prompt=env.request)
        env.recommendation = resp.text
        # Record the author's vendor so the reviewer/gate can pick a *distinct*
        # second vendor for cross-vendor independence (V4).
        env.meta["author_vendor"] = client.spec.vendor
        env.meta["author_model"] = client.spec.model
        env.add_trace(
            "author",
            self.kind,
            f"authored via {client.spec.vendor}:{client.spec.model}",
            chars=len(resp.text or ""),
        )
        return env
