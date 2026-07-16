"""M3: the full ceremony — manifest/presets, the author handoff, compose."""
from __future__ import annotations

import pathlib

import pytest

from rapier.envelope import Envelope
from rapier.manifest import Manifest
from rapier.models import ModelSpec, build_client
from rapier.presets import PRESETS, load_preset
from rapier.stage import StageContext, get_stage


@pytest.fixture(autouse=True)
def _no_corpus_pollution(monkeypatch):
    # ComposeStage appends the ceremony row to the shared corpus
    # (~/.claude/spar-ledger.jsonl) by default; disable it during tests so runs
    # never pollute the real ledger. (Empty path = the append is a no-op.)
    monkeypatch.setenv("RAPIER_CEREMONY_LEDGER", "")


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


def _stages(name, **kw):
    return [s.stage for s in load_preset(name, **kw).stages]


def test_default_spar_is_unchanged():
    # settle=0, verify=gate must reproduce the historical resolver exactly.
    assert _stages("spar") == [
        "author", "cross_review", "anchored_fix", "definitiveness_gate", "citation_gate", "compose",
    ]


def test_settle_adds_review_rounds():
    s = _stages("spar", settle=2)
    # 1 base + 2 settle = 3 review rounds, each cross_review→anchored_fix→definitiveness_gate
    assert s.count("cross_review") == 3
    assert s.count("anchored_fix") == 3
    assert s.count("definitiveness_gate") == 3
    assert s.count("citation_gate") == 1  # verify=gate → once, before compose
    assert s[0] == "author" and s[-1] == "compose"


def test_verify_off_drops_citation_gate():
    assert "citation_gate" not in _stages("spar", verify="off")


def test_verify_round_gates_every_round():
    s = _stages("spar", settle=1, verify="round")
    assert s.count("citation_gate") == 2  # after each of the 2 review rounds
    assert s[-1] == "compose"


def test_author_role_has_generous_max_tokens():
    # 1024 truncates a real recommendation mid-sentence; the generative stages
    # must carry real headroom in both presets.
    for name in ("spar", "sparring"):
        author = next((s for s in load_preset(name).stages if s.stage == "author"), None)
        assert author is not None, f"{name} has no author stage"
        assert author.roles["author"].max_tokens >= 4096


def test_settle_verify_flow_through_sparring():
    s = _stages("sparring", settle=1, verify="off")
    assert s[:3] == ["spark", "pattern_lock", "cut"]  # proposer intact
    assert "citation_gate" not in s
    assert s.count("cross_review") == 2  # 1 base + 1 settle


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
    # Plain-text report: ALL-CAPS ruled sections, plain-language confidence, the
    # single ═ part break before the trust rider, and no raw markdown/shorthand.
    md = env.meta["report_md"]
    assert "BOTTOM LINE" in md and "HOW MUCH TO TRUST THIS" in md
    assert "TRUST RIDER" in md and "═" in md  # the hard recommendation→rider break
    assert "STANDING OBJECTIONS FROM THE DELIBERATION" in md  # dissent forwarded
    assert "the $12k is unsupported" in md  # a reviewer objection surfaced
    assert "## " not in md  # no unrendered markdown headings leak through
    assert "gate=" not in md  # the old shorthand line is gone


def test_proposer_report_renders_the_handoff():
    from rapier.stages.resolver.compose import _render_proposer_report
    env = Envelope(request="pick a path", committed="Option C: do the thing")
    env.meta["proposer"] = {
        "spark": {"rounds": 4, "converged": True, "cross_vendor": True,
                  "generator_vendor": "anthropic", "challenger_vendor": "openai"},
        "pattern_lock": {"rounds": 3, "converged": False},
        "cut": {"rounds": 2, "converged": True, "cross_vendor": True,
                "generator_vendor": "anthropic", "challenger_vendor": "openai",
                "standing_objections": [{"text": "cost risk", "artifact": "adr-3"}]},
    }
    md = _render_proposer_report(env)
    assert "RAPIER — PROPOSER REPORT" in md and "## " not in md  # plain-text title, no markdown
    assert "Option C: do the thing" in md                       # the committed option
    assert "cost risk" in md and "adr-3" in md                  # a standing objection + its basis
    assert "SPARK" in md and "THE CUT" in md                    # how it was reached
    assert "different vendors (anthropic vs openai)" in md       # cross-vendor deliberation


def test_no_proposer_report_for_resolver_only():
    # /spar (Resolver-only) has no Proposer half -> no report to surface.
    from rapier.stages.resolver.compose import _render_proposer_report
    assert _render_proposer_report(Envelope(request="q", recommendation="ans")) is None


def test_ceremony_row_not_load_bearing_when_nothing_changed():
    env = Envelope(request="q", recommendation="same", verdict="PASS")
    env.meta["review"] = {"cross_vendor": True, "objections": []}
    get_stage("compose")().run(env, StageContext())
    assert env.meta["ceremony_row"]["load_bearing"] is False
    assert env.meta["ceremony_row"]["verdict"] == "DID_NOT_MATTER"


# --- Increment 5: input-type classification fields + drift-fix on the row ------
def test_ceremony_row_carries_frame_classification_and_drift_fields():
    env = Envelope(request="Use Postgres because ACID.", recommendation="Use Postgres.", verdict="PASS")
    env.meta["review"] = {"reviewer_vendor": "gpt", "cross_vendor": True,
                          "objections": [{"handle": "o1", "text": "check ops load"}]}
    env.meta["citation_gate"] = {"gate": "clean", "grounding_rate": 1.0, "theater_flags": 0}
    # seeded from the separate `rapier frame` call (via --frame)
    env.meta["frame"] = {"input_type": "proposition", "readiness": "pass",
                         "earned_gate_failed": "none", "anchor": None, "route": "resolve"}
    env.meta["settle"] = 2
    env.meta["verify"] = "gate"
    get_stage("compose")().run(env, StageContext(run_dir="/tmp/run-x"))
    row = env.meta["ceremony_row"]
    # the 7 classification fields
    assert row["input_type"] == "proposition"
    assert row["readiness"] == "pass"
    assert row["earned_gate_failed"] == "none"
    assert row["anchor"] == ""                      # None -> ""
    assert row["routed_to"] == "resolve"
    assert row["offramp_taken"] == "full_resolve"   # a recommendation was produced
    assert row["demoted"] is False                  # earned, routed to resolve
    # the drift fields the engine previously omitted, now present
    assert row["iterations"] == 3                    # 1 + settle(2)
    assert row["held_at_cap"] is False               # verdict PASS
    assert row["strongest_quote"] == "check ops load"
    assert row["verify_mode"] == "gate"
    assert row["grounding_coherence"] == "ok"        # verification ran
    assert row["artifact_path"] == "/tmp/run-x/report.md"


def test_ceremony_row_demoted_and_anchor_for_unearned_proposition():
    env = Envelope(request="Use X.", recommendation="Use Y.", committed="Use Y.", verdict="REVIEW")
    env.meta["review"] = {"objections": []}
    env.meta["frame"] = {"input_type": "proposition", "readiness": "fail",
                         "earned_gate_failed": "G2", "anchor": "Use X", "route": "propose"}
    get_stage("compose")().run(env, StageContext())
    row = env.meta["ceremony_row"]
    assert row["demoted"] is True                    # a stated proposition sent back to Propose
    assert row["routed_to"] == "propose"
    assert row["anchor"] == "Use X"
    assert row["offramp_taken"] == "full_resolve"


def test_ceremony_row_classification_empty_without_frame():
    # No --frame seeded: the schema stays stable, the classification values are empty.
    env = Envelope(request="q", recommendation="a", verdict="PASS")
    env.meta["review"] = {"objections": []}
    get_stage("compose")().run(env, StageContext())
    row = env.meta["ceremony_row"]
    for k in ("input_type", "readiness", "earned_gate_failed", "anchor", "routed_to"):
        assert row[k] == ""
    assert row["offramp_taken"] == "full_resolve"
    assert row["demoted"] is False
    assert row["iterations"] == 1                     # settle absent -> 1
    assert row["grounding_coherence"] == "n/a"        # no verification ran


def test_pipeline_run_merges_seed_meta():
    # seed_meta lands on env.meta before any stage runs (the --frame/settle path).
    from rapier.pipeline import Pipeline, StageSpec
    pipe = Pipeline([StageSpec(stage="echo", config={})], name="t")
    env = pipe.run("hello", seed_meta={"frame": {"input_type": "question"}, "settle": 1})
    assert env.meta["frame"]["input_type"] == "question"
    assert env.meta["settle"] == 1
