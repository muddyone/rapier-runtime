"""Verifier R6-1/R4-1 and R4-2: a review that failed at run time must not read
as an independent challenge, and must not let the run end in ``act``.

The vendored reviewer is fail-soft: a model error returns no objections and a
``degraded`` note. These tests drive the real cross_review stage with only the
reviewer's model call stubbed, and pair each failure with a working control."""
from __future__ import annotations

import pytest

from rapier.pipeline import Pipeline, StageSpec
from rapier.stage import TransformStage, register_stage
from rapier.stages.resolver.compose import final_state, review_outcome


@register_stage("_rv_seed")
class _Seed(TransformStage):
    writes_answer = True

    def run(self, env, ctx):
        env.recommendation = "Choose B; migration takes two weeks."
        env.meta["author_vendor"] = "anthropic"
        env.add_trace("_rv_seed", self.kind, "wrote")
        return env


@pytest.fixture
def reviewer(monkeypatch):
    """Two vendors available; the reviewer's model call is set per test."""
    from rapier.stages.resolver import _binding
    from rapier.verify import _bootstrap as B

    monkeypatch.setattr(_binding, "available_vendors", lambda: ["anthropic", "openai"])
    cr = B.spar_cross_review
    monkeypatch.setattr(cr.L, "keys_present", lambda: {"anthropic": True, "openai": True})
    monkeypatch.setattr(B, "run_gate", lambda p, r: {
        "answer_verdict": "PASS", "n_specifics": 1, "failures": [], "rows": [{}],
        "rider_lines": [], "cross_vendor": True})

    def use(fn):
        monkeypatch.setattr(cr, "challenger_call", fn)
    return use


def _run(**meta):
    return Pipeline(
        [StageSpec(stage="_rv_seed"), StageSpec(stage="cross_review"),
         StageSpec(stage="definitiveness_gate"), StageSpec(stage="compose")],
        name="t",
    ).run("decide X", seed_meta={"verify": "off", "verify_set_by": "caller (MCP tool argument)",
                                 **meta})


def _boom(vendor, system, user):
    raise RuntimeError("reviewer 503")


def _ok(vendor, system, user):
    return {"objections": []}


@pytest.mark.parametrize("required", [False, True])
def test_failed_review_is_did_not_run_and_never_act(reviewer, required):
    reviewer(_boom)
    env = _run(require_independent=required)
    status, reason = review_outcome(env)
    assert status == "did-not-run" and "reviewer 503" in reason
    state, nxt = final_state(env)
    assert state == "defer"  # cross-lane ruling: a control that did not run -> defer
    assert "review" in nxt
    report = " ".join(env.meta["report_md"].split())
    assert "independent, different-vendor reviewer" not in report
    assert "pressure-tested it, so the challenge was genuinely cross-vendor" not in report
    assert "did NOT run on this answer (reviewer error: reviewer 503)" in report
    assert "held up as written" not in report
    assert env.meta["ceremony_row"]["final_state"] == "defer"


def test_control_working_review_is_act(reviewer):
    reviewer(_ok)
    env = _run(require_independent=True)
    assert review_outcome(env) == ("ran", "")
    assert final_state(env)[0] == "act"


def test_required_independence_with_same_vendor_review_defers(reviewer, monkeypatch):
    from rapier.verify import _bootstrap as B

    reviewer(_ok)
    monkeypatch.setattr(B.spar_cross_review.L, "keys_present",
                        lambda: {"anthropic": True, "openai": False})
    env = _run(require_independent=True)
    assert review_outcome(env) == ("ran", "same vendor (not independent)")
    state, nxt = final_state(env)
    assert state == "defer" and "independent second-vendor review" in nxt


def test_control_same_vendor_review_without_requirement_is_act_and_says_so(reviewer, monkeypatch):
    from rapier.verify import _bootstrap as B

    reviewer(_ok)
    monkeypatch.setattr(B.spar_cross_review.L, "keys_present",
                        lambda: {"anthropic": True, "openai": False})
    state, nxt = final_state(_run(require_independent=False))
    assert state == "act" and "same-vendor review, not an independent one" in nxt


def test_act_never_claims_a_switched_off_check(reviewer):
    """R4-2: with verify=off the citation check never ran."""
    reviewer(_ok)
    env = _run()
    state, nxt = final_state(env)
    assert state == "act"
    assert "cleared its checks" not in nxt
    assert "the citation check did not run (switched off by caller (MCP tool argument)" in nxt
    assert "cleared its checks" not in env.meta["report_md"]
