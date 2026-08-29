"""Type-3 rider check: does the record back what the rider says was contested?"""

import json

import pytest

from rapier.verify.rider_record import check_rider_record, check_run, render


def _run(tmp_path, responses, rider=None, envelope=True):
    d = tmp_path / "run"
    d.mkdir(exist_ok=True)
    if responses is not None:
        with open(d / "transcript.jsonl", "w", encoding="utf-8") as fh:
            for r in responses:
                fh.write(json.dumps({"vendor": "mock", "model": "m", "system": "",
                                     "prompt": "p", "response": r}) + "\n")
    if envelope:
        (d / "envelope.json").write_text(json.dumps({"trust_rider": rider or {}}))
    return str(d)


def test_verbatim_objection_is_in_the_record(tmp_path):
    rider = {"contested_and_resolved": ["The failover plan has no tested rollback path."]}
    run = _run(tmp_path, ["I object: The failover plan has no tested rollback path. Fix it."], rider)
    rep = check_run(run)
    assert rep["status"] == "ok"
    assert rep["in_record"] == 1 and rep["not_in_record"] == 0


def test_fabricated_contest_is_caught(tmp_path):
    """The whole reason this check exists: a rider inventing its own accountability."""
    rider = {"contested_and_resolved": ["The reviewer flagged an unbounded retry loop."]}
    run = _run(tmp_path, ["Looks fine to me. No concerns about the caching layer."], rider)
    rep = check_run(run)
    assert rep["status"] == "failed"
    assert rep["not_in_record"] == 1
    assert "does not show" in rep["summary"]


def test_paraphrase_is_reported_separately_not_as_a_clean_pass(tmp_path):
    rider = {"contested_and_resolved": ["the failover plan has no tested rollback path"]}
    run = _run(tmp_path, ["My objection: the plan for failover lacks any tested "
                          "rollback path whatsoever and no rehearsal is scheduled."], rider)
    rep = check_run(run)
    assert rep["status"] == "ok"
    assert rep["paraphrased"] == 1 and rep["in_record"] == 0


def test_missing_transcript_is_unchecked_never_a_pass(tmp_path):
    """An unverifiable claim must not silently degrade to verified."""
    rider = {"contested_and_resolved": ["Something was contested."]}
    run = _run(tmp_path, None, rider)
    rep = check_run(run)
    assert rep["status"] == "unchecked"
    assert rep["status"] != "ok"
    assert rep["in_record"] == 0
    assert all(c["verdict"] == "UNCHECKED" for c in rep["claims"])
    assert "Not a pass" in rep["summary"]


def test_proposer_dissent_is_checked_too(tmp_path):
    rider = {"proposer_dissent_forwarded": ["Cost was never bounded."]}
    run = _run(tmp_path, ["Standing objection: Cost was never bounded."], rider)
    rep = check_run(run)
    assert rep["status"] == "ok" and rep["checked"] == 1


def test_empty_rider_is_none_not_ok(tmp_path):
    rep = check_run(_run(tmp_path, ["anything"], {}))
    assert rep["status"] == "none" and rep["checked"] == 0


def test_short_claim_needs_verbatim_match(tmp_path):
    """Below the token floor, overlap scoring is meaningless — require verbatim."""
    rider = {"contested_and_resolved": ["too slow"]}
    run = _run(tmp_path, ["The system is slow and too expensive."], rider)
    rep = check_rider_record(rider, run)
    assert rep["not_in_record"] == 1


def test_torn_transcript_line_does_not_fail_the_check(tmp_path):
    d = tmp_path / "run"; d.mkdir()
    with open(d / "transcript.jsonl", "w", encoding="utf-8") as fh:
        fh.write('{"response": "The rollback path is untested here."}\n')
        fh.write("{ this is not json\n")
    rider = {"contested_and_resolved": ["The rollback path is untested here."]}
    rep = check_rider_record(rider, str(d))
    assert rep["status"] == "ok"


def test_render_marks_the_miss(tmp_path):
    rider = {"contested_and_resolved": ["A contest that never happened."]}
    out = render(check_run(_run(tmp_path, ["unrelated"], rider)))
    assert "MISS" in out and "RIDER RECORD CHECK" in out


def test_no_model_is_ever_called(tmp_path, monkeypatch):
    """Model-free is the guarantee. Blow up if anything reaches the model layer."""
    import rapier.models as models

    def boom(*a, **k):
        raise AssertionError("rider record check must not call a model")

    monkeypatch.setattr(models, "build_client", boom, raising=False)
    rider = {"contested_and_resolved": ["The failover plan has no tested rollback."]}
    run = _run(tmp_path, ["The failover plan has no tested rollback."], rider)
    assert check_run(run)["status"] == "ok"
