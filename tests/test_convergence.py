"""The convergence primitive — deterministic, with injected fake agents."""
from __future__ import annotations

from rapier.convergence import ConvergenceResult, run_convergence


def _gen(payloads, agree_from):
    """Generator that walks a list of payloads and agrees from a given round."""
    state = {"i": 0}

    def generator(_prev, _concerns):
        i = state["i"]
        payload = payloads[min(i, len(payloads) - 1)]
        state["i"] = i + 1
        return {"payload": payload, "agree": (i + 1) >= agree_from, "reasoning": ""}

    return generator


def _chal(agree_from):
    state = {"i": 0}

    def challenger(_payload):
        state["i"] += 1
        return {"concerns": [], "agree": state["i"] >= agree_from, "reasoning": ""}

    return challenger


def test_converges_when_both_agree():
    r = run_convergence(_gen([["a"], ["a", "b"]], agree_from=2), _chal(agree_from=2), cap=5)
    assert r.converged is True
    assert r.resolved_at == 2
    assert r.payload == ["a", "b"]


def test_unresolved_at_cap():
    r = run_convergence(_gen([["a"]], agree_from=99), _chal(agree_from=99), cap=3)
    assert r.converged is False
    assert r.resolved_at is None
    assert len(r.rounds) == 3


def test_no_op_flag_when_payload_never_moves():
    # both agree round 1, payload identical to the first proposal -> rubber-stamp
    r = run_convergence(_gen([["x"]], agree_from=1), _chal(agree_from=1), cap=3)
    assert r.converged is True and r.resolved_at == 1
    assert r.no_op is True


def test_no_op_false_when_payload_changes():
    r = run_convergence(_gen([["x"], ["x", "y"]], agree_from=2), _chal(agree_from=2), cap=3)
    assert r.no_op is False


def test_integrity_check_reopens_premature_convergence():
    # both agree at round 1, but the integrity check calls it premature once.
    calls = {"n": 0}

    def integrity(_payload, _rounds):
        calls["n"] += 1
        return calls["n"] > 1  # premature the first time, genuine after

    r = run_convergence(
        _gen([["a"], ["a", "b"]], agree_from=1), _chal(agree_from=1), cap=5,
        integrity=integrity, reopen_cap=1,
    )
    assert r.converged is True
    assert r.integrity_reopened == 1
    assert r.payload == ["a", "b"]  # reopened round produced the second payload
