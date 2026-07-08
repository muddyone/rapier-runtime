"""Author stage — write the recommendation on the merits.

Native model-call stage on the M0 model layer: it uses the injected ``author``
role client. The preset names Anthropic, but the pipeline remaps the role to an
available vendor when that key is absent (BYO-any-vendor), so a run authors on
whatever the user actually configured. In the original /spar this was the Claude
orchestrator; here it is a first-class stage.
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
        # Handoff: if the Proposer committed an option, author FOR that option and
        # forward the Cut's standing objections as context to pre-empt (a single
        # fresh pass — never fed as `prev` to iterate).
        committed = env.committed
        standing = ((env.meta.get("proposer") or {}).get("cut") or {}).get("standing_objections") or []
        if committed:
            prompt = f"DECISION:\n{env.request}\n\nThe deliberation committed to this option:\n{committed}\n"
            if standing:
                objs = "\n".join(
                    f"- [{o.get('artifact', '')}] {o.get('text', '')}" for o in standing if isinstance(o, dict)
                )
                prompt += (
                    "\nKNOWN UNRESOLVED OBJECTIONS from the adversarial deliberation — address each where "
                    f"valid, in your recommendation:\n{objs}\n"
                )
            prompt += "\nWrite the recommendation for the committed option, on the merits."
            env.meta["handoff"] = {"committed": True, "standing_objections_forwarded": len(standing)}
        else:
            prompt = env.request
        resp = client.complete(system=system, prompt=prompt)
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
