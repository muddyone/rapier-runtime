"""The echo stage — M0's proof-of-bones.

It does the minimum that exercises the whole skeleton: it pulls the injected
``author`` client from the StageContext, runs the request through it, writes the
result as the recommendation, and appends a trace entry. With the echo
manifest's ``mock`` vendor this needs no keys and no network — it proves the
Envelope flows, clients inject, config reaches the stage, and the trace records.
"""
from __future__ import annotations

from ..envelope import Envelope
from ..stage import StageContext, TransformStage, register_stage


@register_stage("echo")
class EchoStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        note = ctx.config.get("note", "echo")
        client = ctx.clients.get("author")
        if client is not None:
            resp = client.complete(system="You are a Rapier echo stage.", prompt=env.request)
            env.recommendation = resp.text
            detail = f"{note}: {client.spec.vendor}:{client.spec.model}"
        else:
            env.recommendation = env.request
            detail = f"{note}: no client (verbatim echo)"
        env.add_trace("echo", self.kind, detail, chars=len(env.recommendation or ""))
        return env
