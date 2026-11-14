"""Accountable Agents item 4 (Bart, D-67 a): name the final state, using the
papers' six names, and label a Proposer that ends without agreeing as a
non-decision while the Resolver still runs."""
import pytest

from rapier.envelope import Envelope
from rapier.stage import StageContext
from rapier.stages.resolver.compose import (
    FINAL_STATES,
    ComposeStage,
    _render_proposer_report,
    final_state,
)


def _env(verdict="PASS", n=2, **meta):
    env = Envelope(request="p", recommendation="Do B; it saves 12k.")
    env.verdict = verdict
    env.meta["definitiveness"] = {"answer_verdict": verdict, "n_specifics": n,
                                  "failures": [{"text": "12k"}] if verdict == "FAIL" else [],
                                  "rows": [{}] * n}
    env.add_trace("definitiveness_gate", "transform", f"verdict={verdict}")
    env.meta.update(meta)
    return env


def _composed(env):
    return ComposeStage().run(env, StageContext())


@pytest.mark.parametrize("verdict,state", [("PASS", "act"), ("FAIL", "block"),
                                           ("REVIEW", "escalate")])
def test_verdicts_map_to_states(verdict, state):
    env = _composed(_env(verdict))
    assert env.trust_rider["final_state"] == state
    assert env.meta["ceremony_row"]["final_state"] == state
    assert env.meta["ceremony_row"]["final_state_next"]


def test_check_that_did_not_run_is_defer_not_act():
    env = Envelope(request="p", recommendation="Do B.")
    env.add_trace("definitiveness_gate", "transform", "ERROR (fail-soft): boom", error="boom")
    assert final_state(_composed(env))[0] == "defer"


def test_blocked_citation_is_block():
    env = _env("PASS", citation_gate={"gate": "blocked"},
               citation_verdicts=[{"artifact_ref": "CVE-0000-0000", "status": "refuted"}])
    env.add_trace("citation_gate", "transform", "gate=blocked")
    assert final_state(_composed(env))[0] == "block"


def test_switched_off_citation_check_does_not_change_the_state():
    env = _env("PASS", verify="off", verify_set_by="caller (MCP tool argument)")
    assert final_state(_composed(env))[0] == "act"


def test_proposer_disagreement_at_cap_is_a_labelled_non_decision():
    env = _env("PASS", proposer={"cut": {"converged": False, "rounds": 2,
                                         "standing_objections": [{"text": "lock-in"}]}})
    env.committed = "Option B"
    out = _composed(env)
    assert out.trust_rider["final_state"] == "non-decision"
    report = out.meta["report_md"]
    assert "NON-DECISION" in report.split("BOTTOM LINE")[0]  # at the top
    assert out.recommendation  # the Resolver still ran (D-67 a: keep going)
    assert "NON-DECISION" in _render_proposer_report(out)


def test_control_converged_cut_is_not_a_non_decision():
    env = _env("PASS", proposer={"cut": {"converged": True, "rounds": 1}})
    env.committed = "Option B"
    out = _composed(env)
    assert out.trust_rider["final_state"] == "act"
    assert "NON-DECISION" not in out.meta["report_md"]
    assert "NON-DECISION" not in _render_proposer_report(out)


def test_every_state_named_is_one_of_the_six():
    for v in ("PASS", "FAIL", "REVIEW"):
        assert final_state(_composed(_env(v)))[0] in FINAL_STATES


def test_crashed_citation_check_is_defer():
    env = _env("PASS")
    env.add_trace("citation_gate", "transform", "ERROR (fail-soft): down", error="down")
    assert final_state(_composed(env))[0] == "defer"
