"""The Proposer-side traceability gate (RM 13.1) — offline. No keys, no network.

Before this stage existed, verification was Resolver-only: a Proposer could cite a source
that does not exist and the run reported clean, with every downstream gate inheriting the
frame. These tests pin the wiring, the skip path, and the in-pack verification path.
"""
from __future__ import annotations

import rapier.stages  # noqa: F401  (registers every stage)
from rapier.envelope import Envelope
from rapier.presets import _build
from rapier.stage import StageContext, get_stage


def _run(env, config=None):
    return get_stage("traceability_gate")().run(env, StageContext(config=config or {}))


def test_gate_is_in_the_proposer_preset_and_respects_verify_off():
    assert [s["stage"] for s in _build("proposer")["pipeline"]] == [
        "spark", "pattern_lock", "cut", "traceability_gate",
    ]
    assert [s["stage"] for s in _build("proposer", verify="off")["pipeline"]] == [
        "spark", "pattern_lock", "cut",
    ]


def test_composite_sparring_keeps_only_the_resolver_gate():
    """`sparring` runs the Resolver's citation_gate; adding the Proposer gate there too
    would double the network work, so it stays off until that is a deliberate call."""
    stages = [s["stage"] for s in _build("sparring")["pipeline"]]
    assert "citation_gate" in stages
    assert "traceability_gate" not in stages


def test_skips_cleanly_when_the_proposition_cites_nothing():
    env = Envelope(request="q")
    env.committed = "Adopt passkeys. Nothing checkable is cited here."
    env = _run(env)
    assert env.meta.get("traceability_gate") is None
    assert "cites nothing checkable" in env.trace[-1].summary


def test_verifies_supplied_artifacts_against_the_pack_offline():
    # NOTE: the extractor has no "#N" pattern, so pack facts reach the gate only when a
    # caller supplies them (or the mapper runs). Offline verification uses that path.
    pack = "1. rotation is quarterly\n2. the vendor SLA is 99.9%"
    env = Envelope(request=pack)
    env.meta["proposer_artifacts"] = [
        {"concern_id": "a1", "artifact_ref": "#1", "concern_text": "commit to #1", "load_bearing": True},
        {"concern_id": "a2", "artifact_ref": "#2", "concern_text": "option leans on #2", "load_bearing": True},
    ]
    env = _run(env)
    summary = env.meta["traceability_gate"]
    verdicts = env.meta["traceability_verdicts"]
    assert summary["gate"] == "clean"
    assert {v["artifact_ref"] for v in verdicts} == {"#1", "#2"}
    assert all(v["status"] == "verified" for v in verdicts)


def test_fabricated_pack_fact_blocks_the_gate():
    env = Envelope(request="1. only one fact here")
    env.meta["proposer_artifacts"] = [
        {"concern_id": "a1", "artifact_ref": "#7", "concern_text": "commit per #7", "load_bearing": True}
    ]
    env = _run(env)
    assert env.meta["traceability_gate"]["gate"] != "clean"
    assert env.meta["traceability_verdicts"][0]["status"] == "refuted"


def test_summary_states_its_own_limited_scope():
    """The gate proves less than RM 10.1 requires and must say so: it speaks only to the
    source-origin branch, not to whether every material element carries an origin."""
    env = Envelope(request="1. a fact")
    env.meta["proposer_artifacts"] = [
        {"concern_id": "a1", "artifact_ref": "#1", "concern_text": "per #1", "load_bearing": True}
    ]
    env = _run(env)
    scope = env.meta["traceability_gate"]["scope"]
    assert "source-origin only" in scope
    assert "Trust Rider" in scope


def test_caller_supplied_artifacts_override_extraction():
    env = Envelope(request="1. a fact")
    env.committed = "Text citing #1 that we want ignored."
    env.meta["proposer_artifacts"] = [
        {"concern_id": "x1", "artifact_ref": "#1", "concern_text": "explicit", "load_bearing": True}
    ]
    env = _run(env)
    assert [v["artifact_ref"] for v in env.meta["traceability_verdicts"]] == ["#1"]


def test_extractor_finds_law_and_patent_refs():
    """Step-2 added law/patent backends; the extractor had no patterns to feed them."""
    from rapier.stages.resolver._extract import extract_artifacts

    refs = [a["artifact_ref"] for a in extract_artifacts(
        "see Brown v. Board of Education, 347 U.S. 483 and U.S. Patent No. 4,405,829."
    )]
    assert refs == ["Brown v. Board of Education, 347 U.S. 483", "U.S. Patent No. 4,405,829"]


def test_extractor_span_may_absorb_a_leading_capitalised_word():
    """Known limit, compensated in backend_law: a sentence-initial capitalised word can be
    pulled into the left party. The reporter cite inside the span still resolves exactly,
    and a bare name gets a trimmed retry before any verdict."""
    from rapier.stages.resolver._extract import extract_artifacts

    refs = [a["artifact_ref"] for a in extract_artifacts("Per Marbury v. Madison, review exists.")]
    assert refs and refs[0].endswith("Marbury v. Madison")


def test_extractor_does_not_invent_case_names_from_prose():
    from rapier.stages.resolver._extract import extract_artifacts

    assert extract_artifacts(
        "We compared the Generator and Challenger roles. The Proposer versus the Resolver."
    ) == []
