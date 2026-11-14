"""A correctness check that never ran must not read like one that ran and found nothing.

Accountable Agents item 2. Three outcomes, kept apart in the report, the trust
rider and ceremony.json: ``ran``, ``nothing-to-check`` and ``did-not-run: <reason>``.
"""
from rapier.envelope import Envelope
from rapier.pipeline import Pipeline, StageSpec
from rapier.stage import StageContext, TransformStage, register_stage
from rapier.stages.resolver.compose import (
    ComposeStage,
    _ceremony_row,
    _verdict_sentence,
    correctness_outcome,
)


def _composed(env):
    return ComposeStage().run(env, StageContext())


def _gate(verdict, n=0, **extra):
    d = {"answer_verdict": verdict, "n_specifics": n, "failures": [], "rows": [{}] * n}
    d.update(extra)
    return d


def _ran_env(verdict="PASS", n=2):
    env = Envelope(request="p", recommendation="rec")
    env.verdict = verdict
    env.meta["definitiveness"] = _gate(verdict, n)
    env.add_trace("definitiveness_gate", "transform", f"verdict={verdict}")
    return env


def test_ran_with_specifics_is_ran():
    assert correctness_outcome(_ran_env("FAIL", 2)) == ("ran", "")


def test_pass_over_zero_specifics_is_nothing_to_check_not_a_plain_pass():
    env = _ran_env("PASS", 0)
    status, _ = correctness_outcome(env)
    assert status == "nothing-to-check"
    out = _composed(env)
    assert out.trust_rider["correctness_check"] == "nothing-to-check"
    report = out.meta["report_md"]
    assert "found no hard specifics" in report
    assert "every hard specific it states either traces" not in report


def test_unavailable_checker_is_not_run_with_its_reason():
    env = Envelope(request="p", recommendation="rec")
    env.verdict = "unchecked"
    env.meta["definitiveness"] = _gate("unchecked", degraded="no api keys")
    env.add_trace("definitiveness_gate", "transform", "verdict=unchecked")
    assert correctness_outcome(env) == ("did-not-run", "no api keys")
    out = _composed(env)
    assert out.trust_rider["correctness_check"] == "did-not-run: no api keys"
    row = out.meta["ceremony_row"]
    assert row["correctness_check"] == "did-not-run"
    assert row["correctness_check_reason"] == "no api keys"
    assert "did NOT run (no api keys)" in out.meta["report_md"]


def test_skipped_gate_is_not_run():
    env = Envelope(request="p", recommendation=None)
    env.add_trace("definitiveness_gate", "transform", "no recommendation to gate — skipped")
    assert correctness_outcome(env) == ("did-not-run", "there was no recommendation to check")


def test_gate_absent_from_pipeline_is_not_run():
    env = Envelope(request="p", recommendation="rec")
    assert correctness_outcome(env) == ("did-not-run", "the check is not part of this pipeline")


@register_stage("_test_seed_recommendation")
class _SeedRecommendation(TransformStage):
    text = "rec"

    def run(self, env, ctx):
        env.recommendation = self.text
        return env


def test_crashed_gate_is_not_run_end_to_end(monkeypatch):
    """The pipeline catches a stage that raises and goes on (fail-soft). The
    crash must reach the report as not-run, never as 'nothing found'."""
    from rapier.verify import _bootstrap

    def boom(problem, rec):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(_bootstrap, "run_gate", boom)
    env = Pipeline(
        [StageSpec(stage="_test_seed_recommendation"),
         StageSpec(stage="definitiveness_gate"),
         StageSpec(stage="compose")],
        name="t",
    ).run("decide X")
    status, reason = correctness_outcome(env)
    assert status == "did-not-run"
    assert "gate exploded" in reason
    assert env.trust_rider["correctness_check"].startswith("did-not-run: the check crashed")
    assert "did NOT run" in env.meta["report_md"]
    assert env.meta["ceremony_row"]["correctness_check"] == "did-not-run"


def test_not_run_sentence_never_offers_nothing_to_check_as_an_explanation():
    s = _verdict_sentence("unchecked", {}, ("did-not-run", "no api keys"))
    assert "no hard specifics" not in s
    assert "not a pass" in s
    legacy = _verdict_sentence(None, {})  # old two-argument call
    assert "no hard specifics" not in legacy and "did NOT run" in legacy


def test_ceremony_row_carries_outcome_for_ran():
    row = _ceremony_row(_ran_env("REVIEW", 1))
    assert row["correctness_check"] == "ran"
    assert row["correctness_check_reason"] == ""


# ── the citation (external-canon) check: same rule ───────────────────────────
from rapier.stages.resolver.compose import _render_report, citation_outcome  # noqa: E402


def test_verify_off_is_did_not_run_never_cited_nothing():
    """Verifier's repro B: verify=off used to print 'cited nothing externally
    checkable ... nothing to confirm', a found-nothing line for a check that
    never ran."""
    env = Envelope(request="p", recommendation="do Y")
    env.meta["verify"] = "off"
    assert citation_outcome(env) == ("did-not-run", "switched off (verify=off)")
    report = _render_report(env)
    assert "cited nothing externally checkable" not in report
    assert "did NOT run (switched off (verify=off))" in report
    out = _composed(env)
    assert out.trust_rider["citation_check"] == "did-not-run: switched off (verify=off)"
    assert out.meta["ceremony_row"]["citation_check"] == "did-not-run"


def test_gate_ran_and_found_no_citations_is_nothing_to_check():
    env = Envelope(request="p", recommendation="do Y")
    env.meta["verify"] = "gate"
    env.add_trace("citation_gate", "transform", "no artifacts to verify — skipped")
    assert citation_outcome(env)[0] == "nothing-to-check"
    assert "cited nothing externally checkable" in _render_report(env)
    assert _composed(env).trust_rider["citation_check"] == "nothing-to-check"


def test_crashed_citation_gate_is_did_not_run(monkeypatch):
    from rapier.verify import service

    def boom(*a, **k):
        raise RuntimeError("crossref down")

    monkeypatch.setattr(service, "verify_artifacts", boom)
    env = Pipeline(
        [StageSpec(stage="_test_seed_recommendation"),
         StageSpec(stage="citation_gate", config={}),
         StageSpec(stage="compose")],
        name="t",
    ).run("decide X", seed_meta={"verify": "gate",
                                 "artifacts": [{"backend": "literature", "artifact_ref": "doi:10.1/x"}]})
    status, reason = citation_outcome(env)
    assert status == "did-not-run" and "crossref down" in reason
    assert "cited nothing externally checkable" not in env.meta["report_md"]
    assert env.meta["ceremony_row"]["citation_check"] == "did-not-run"


def test_citations_checked_is_ran():
    env = Envelope(request="p", recommendation="see doi:10.1/x")
    env.meta["citation_verdicts"] = [{"artifact_ref": "doi:10.1/x", "status": "verified"}]
    assert citation_outcome(env) == ("ran", "")


# ── settle >= 1: only the gate's LAST run speaks for the final draft ────────
# (verifier findings R2-1 and R2-2 on 3ff1e80c)

def _two_round_gate(monkeypatch, first, second, rec="rec"):
    """Run the definitiveness gate twice through Pipeline. ``first``/``second``
    are a gate result dict, or an Exception instance to raise."""
    from rapier.verify import _bootstrap

    calls = iter([first, second])

    def fake(problem, recommendation):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(_bootstrap, "run_gate", fake)
    monkeypatch.setattr(_SeedRecommendation, "text", rec, raising=False)
    return Pipeline(
        [StageSpec(stage="_test_seed_recommendation"),
         StageSpec(stage="definitiveness_gate"),
         StageSpec(stage="definitiveness_gate"),
         StageSpec(stage="compose")],
        name="t",
    ).run("decide X")


def test_final_round_crash_does_not_leave_an_earlier_pass_standing(monkeypatch):
    env = _two_round_gate(monkeypatch, _gate("PASS", 2), RuntimeError("round-2 crash"))
    assert correctness_outcome(env) == ("did-not-run", "the check crashed: round-2 crash")
    assert env.verdict == "unchecked"
    assert env.meta["stale_verdict"] == "PASS"  # kept for audit, out of the answer fields
    assert env.trust_rider["specifics_traceability"] == "unchecked"
    row = env.meta["ceremony_row"]
    assert row["answer_verdict"] == "unchecked" and row["converged"] is False
    assert "passed the correctness check" not in env.meta["report_md"]
    assert "Nothing flagged" not in env.meta["report_md"]
    from rapier.mcp.tools import _result
    assert _result(env, False)["verdict"] == "unchecked"


def test_control_final_round_pass_stands(monkeypatch):
    env = _two_round_gate(monkeypatch, _gate("FAIL", 2), _gate("PASS", 2))
    assert correctness_outcome(env) == ("ran", "")
    assert env.verdict == "PASS"


def test_earlier_round_crash_does_not_hide_a_final_fail(monkeypatch):
    fail = _gate("FAIL", 1, failures=[{"text": "40%"}])
    env = _two_round_gate(monkeypatch, RuntimeError("round-1 crash"), fail)
    assert correctness_outcome(env) == ("ran", "")
    assert env.verdict == "FAIL"
    assert "did NOT pass the correctness check" in env.meta["report_md"]
    assert env.meta["ceremony_row"]["correctness_check"] == "ran"


def test_no_keys_through_pipeline_is_did_not_run(monkeypatch):
    """End to end: no vendor keys, the real vendored gate, no stub."""
    from rapier.models import vendor_key_envs

    for name in vendor_key_envs().values():
        monkeypatch.delenv(name, raising=False)
    env = Pipeline(
        [StageSpec(stage="_test_seed_recommendation"),
         StageSpec(stage="definitiveness_gate"),
         StageSpec(stage="compose")],
        name="t",
    ).run("decide X")
    status, reason = correctness_outcome(env)
    assert status == "did-not-run" and "no api keys" in reason
    assert env.trust_rider["correctness_check"].startswith("did-not-run: no api keys")
    assert env.meta["ceremony_row"]["correctness_check"] == "did-not-run"
    assert "did NOT run" in env.meta["report_md"]


# ── R2-3: an empty result over an answer that states figures ────────────────

def test_zero_specifics_over_an_answer_with_figures_is_did_not_run(monkeypatch):
    env = _two_round_gate(monkeypatch, _gate("PASS", 0), _gate("PASS", 0),
                          rec="Latency drops 40% after the change.")
    status, reason = correctness_outcome(env)
    assert status == "did-not-run" and "states figures" in reason
    assert env.verdict == "unchecked"


def test_zero_specifics_over_prose_with_list_numbering_is_nothing_to_check(monkeypatch):
    env = _two_round_gate(monkeypatch, _gate("PASS", 0), _gate("PASS", 0),
                          rec="1. Prefer the simpler option.\n2. Revisit it later.")
    assert correctness_outcome(env)[0] == "nothing-to-check"


# ── verify=round: the citation gate's last run counts too ────────────────────

def _two_round_citations(monkeypatch, first, second):
    from rapier.verify import service

    calls = iter([first, second])

    def fake(*a, **k):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(service, "verify_artifacts", fake)
    arts = [{"backend": "literature", "artifact_ref": "doi:10.1/x"}]
    return Pipeline(
        [StageSpec(stage="_test_seed_recommendation"),
         StageSpec(stage="citation_gate", config={}),
         StageSpec(stage="citation_gate", config={}),
         StageSpec(stage="compose")],
        name="t",
    ).run("decide X", seed_meta={"verify": "round", "artifacts": arts})


_OK = ([{"artifact_ref": "doi:10.1/x", "status": "verified"}], {"gate": "PASS", "grounding_rate": 1.0})


def test_citation_final_round_crash_drops_the_earlier_verdicts(monkeypatch):
    env = _two_round_citations(monkeypatch, _OK, RuntimeError("crossref down"))
    assert citation_outcome(env)[0] == "did-not-run"
    assert "citation_verdicts" not in env.meta and env.meta["citation_verdicts_stale"]
    assert env.meta["ceremony_row"]["grounding_rate"] is None
    assert "1 of 1 checkable reference confirmed" not in " ".join(env.meta["report_md"].split())


def test_citation_earlier_round_crash_does_not_hide_the_final_result(monkeypatch):
    env = _two_round_citations(monkeypatch, RuntimeError("crossref down"), _OK)
    assert citation_outcome(env) == ("ran", "")
