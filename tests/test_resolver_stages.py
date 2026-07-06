"""Resolver stage plumbing — mock author + monkeypatched vendored calls.

These prove the envelope flows correctly through the five Resolver stages
without any network or keys. The vendored review/gate logic itself is exercised
by parity (test_verify_service) and, live, by a keyed cross-vendor run.
"""
from __future__ import annotations

import pathlib

from rapier.envelope import Envelope
from rapier.manifest import Manifest
from rapier.models import ModelSpec, build_client
from rapier.stage import StageContext, get_stage

_MOCK = lambda model="m1": {"author": build_client(ModelSpec(vendor="mock", model=model))}


def test_author_writes_recommendation_with_mock():
    env = Envelope(request="decide X")
    get_stage("author")().run(env, StageContext(clients=_MOCK()))
    assert env.recommendation == "[mock:m1] decide X"


def test_author_skips_without_client():
    env = Envelope(request="decide X")
    get_stage("author")().run(env, StageContext())
    assert env.recommendation is None
    assert env.trace[-1].summary.startswith("no author client")


def test_cross_review_stores_objections(monkeypatch):
    from rapier.verify import _bootstrap

    monkeypatch.setattr(
        _bootstrap,
        "review",
        lambda problem, rec, prev, vendor: {
            "reviewer_vendor": "gpt",
            "cross_vendor": True,
            "objections": [{"handle": "o1", "text": "the $ figure is unsupported"}],
            "material_objections_remaining": 1,
        },
    )
    env = Envelope(request="p", recommendation="rec")
    get_stage("cross_review")().run(env, StageContext(config={"reviewer": "gpt"}))
    assert env.meta["review"]["cross_vendor"] is True
    assert len(env.meta["review"]["objections"]) == 1


def test_anchored_fix_revises_when_objections_present():
    env = Envelope(request="p", recommendation="draft")
    env.meta["review"] = {"objections": [{"handle": "o1", "text": "fix the number"}]}
    get_stage("anchored_fix")().run(env, StageContext(clients=_MOCK()))
    assert env.recommendation.startswith("[mock:m1]")
    assert "MATERIAL OBJECTIONS" in env.recommendation  # mock echoes the prompt


def test_anchored_fix_holds_without_objections():
    env = Envelope(request="p", recommendation="draft")
    get_stage("anchored_fix")().run(env, StageContext(clients=_MOCK()))
    assert env.recommendation == "draft"  # unchanged


def test_definitiveness_gate_sets_verdict_and_rider(monkeypatch):
    from rapier.verify import _bootstrap

    monkeypatch.setattr(
        _bootstrap,
        "run_gate",
        lambda problem, rec: {
            "answer_verdict": "PASS",
            "n_specifics": 3,
            "failures": [],
            "rider_lines": ["assume ~$X/mo — verify vs your actuals"],
            "cross_vendor": True,
        },
    )
    env = Envelope(request="p", recommendation="rec")
    env.meta["review"] = {"objections": [{"handle": "o1", "text": "held objection"}]}
    get_stage("definitiveness_gate")().run(env, StageContext())
    assert env.verdict == "PASS"
    assert env.trust_rider["assumptions_to_verify"] == ["assume ~$X/mo — verify vs your actuals"]
    assert env.trust_rider["overall_confidence"] == "PASS"
    assert env.trust_rider["contested_and_resolved"] == ["held objection"]


def test_citation_gate_runs_pack_backend_offline():
    env = Envelope(request="1. foo\n2. bar")
    env.meta["artifacts"] = [
        {"concern_id": "a1", "artifact_ref": "#2", "concern_text": "x",
         "load_bearing": True, "pack_text": "1. foo\n2. bar"}
    ]
    get_stage("citation_gate")().run(env, StageContext())
    assert env.meta["citation_gate"]["gate"] == "clean"


def test_citation_gate_skips_without_artifacts():
    env = Envelope(request="p")
    get_stage("citation_gate")().run(env, StageContext())
    assert env.trace[-1].summary.startswith("no artifacts")


def test_spar_manifest_loads_and_all_stages_registered():
    path = pathlib.Path(__file__).parent.parent / "manifests" / "sparring.spar.yaml"
    m = Manifest.load(str(path))
    assert [s.stage for s in m.stages] == [
        "author",
        "cross_review",
        "anchored_fix",
        "definitiveness_gate",
        "citation_gate",
    ]
    m.build()  # must not raise — every stage is registered
