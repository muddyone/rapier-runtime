"""G2: the Challenger's verifiable-artifact rule is mechanically enforced —
concerns without an artifact are dropped, not just discouraged."""
from __future__ import annotations

from rapier.models import ModelResponse
from rapier.stages.proposer.phases import PHASES, make_agents


class _StubClient:
    def __init__(self, text):
        self._text = text

        class _Spec:
            vendor = "mock"
            model = "m"

        self.spec = _Spec()

    def complete(self, system, prompt):
        return ModelResponse(text=self._text, vendor="mock", model="m")


def test_challenger_drops_artifact_less_concerns():
    reply = (
        '{"concerns": ['
        '{"text": "grounded concern", "artifact": "CWE-79"},'
        '{"text": "vibe concern", "artifact": ""},'
        '{"text": "another vibe"}'
        '], "agree": false, "reasoning": "r"}'
    )
    _gen, challenger = make_agents(_StubClient("{}"), _StubClient(reply), PHASES["spark"], "req", None)
    out = challenger(["opt"])
    assert len(out["concerns"]) == 1
    assert out["concerns"][0]["artifact"] == "CWE-79"
    assert out["theatrical_dropped"] == 2  # the empty-artifact and the missing-artifact ones
