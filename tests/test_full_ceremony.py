"""M3: the full ceremony — manifest/presets, the author handoff, compose."""
from __future__ import annotations

import pathlib

from rapier.envelope import Envelope
from rapier.manifest import Manifest
from rapier.models import ModelSpec, build_client
from rapier.presets import PRESETS, load_preset
from rapier.stage import StageContext, get_stage


def test_full_manifest_loads_and_registers():
    path = pathlib.Path(__file__).parent.parent / "manifests" / "sparring.full.yaml"
    m = Manifest.load(str(path))
    assert [s.stage for s in m.stages] == [
        "spark", "pattern_lock", "cut",
        "author", "cross_review", "anchored_fix", "definitiveness_gate", "citation_gate", "compose",
    ]
    m.build()  # all stages registered


def test_presets_build():
    for name in PRESETS:
        load_preset(name).build()
    assert load_preset("sparring").stages[0].stage == "spark"
    assert load_preset("spar").stages[0].stage == "author"


def test_author_handoff_uses_committed_and_forwards_objections():
    env = Envelope(request="decide X", committed="Option B")
    env.meta["proposer"] = {"cut": {"standing_objections": [{"text": "cost risk", "artifact": "src/x.py:12"}]}}
    ctx = StageContext(clients={"author": build_client(ModelSpec("mock", "m1"))})
    get_stage("author")().run(env, ctx)
    # mock echoes the prompt — so the committed option + objection must be in it
    assert "Option B" in env.recommendation
    assert "cost risk" in env.recommendation
    assert env.meta["handoff"]["standing_objections_forwarded"] == 1


def test_author_no_handoff_without_committed():
    env = Envelope(request="decide X")
    ctx = StageContext(clients={"author": build_client(ModelSpec("mock", "m1"))})
    get_stage("author")().run(env, ctx)
    assert env.recommendation == "[mock:m1] decide X"
    assert "handoff" not in env.meta


def test_compose_builds_report_and_ceremony_row():
    env = Envelope(request="decide X", recommendation="Do B because $12k/yr saved.", verdict="PASS")
    env.meta["review"] = {"reviewer_vendor": "gpt", "cross_vendor": True,
                          "objections": [{"handle": "o1", "text": "the $12k is unsupported"}]}
    env.meta["definitiveness"] = {"cross_vendor": True}
    env.meta["recommendation_before_fix"] = "Do B."  # changed -> load_bearing
    env.meta["proposer"] = {"cut": {"standing_objections": [{"text": "lock-in risk", "artifact": "adr-3"}]}}
    get_stage("compose")().run(env, StageContext())
    row = env.meta["ceremony_row"]
    assert row["challenger_changed_recommendation"] is True
    assert row["challenger_surfaced_error_or_risk"] is True
    assert row["load_bearing"] is True and row["verdict"] == "MATTERED"
    assert row["answer_verdict"] == "PASS"
    # rider carries both the review objections and the forwarded proposer dissent
    rider = env.trust_rider
    assert "the $12k is unsupported" in rider["contested_and_resolved"]
    assert "lock-in risk" in rider["proposer_dissent_forwarded"]
    assert "## Trust rider" in env.meta["report_md"]


def test_ceremony_row_not_load_bearing_when_nothing_changed():
    env = Envelope(request="q", recommendation="same", verdict="PASS")
    env.meta["review"] = {"cross_vendor": True, "objections": []}
    get_stage("compose")().run(env, StageContext())
    assert env.meta["ceremony_row"]["load_bearing"] is False
    assert env.meta["ceremony_row"]["verdict"] == "DID_NOT_MATTER"
