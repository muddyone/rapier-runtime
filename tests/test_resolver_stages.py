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


# --- artifact extraction (wiring the external-canon gate into a normal run) ---

def test_extract_artifacts_finds_each_backend_family():
    from rapier.stages.resolver._extract import extract_artifacts

    text = (
        "We rely on CWE-89 for injection, per RFC 6749, and the paper "
        "doi:10.1145/3292500.3330701. The Log4Shell flaw (CVE-2021-44228) "
        "applies. See https://example.com/spec and the check in src/auth/login.py:42."
    )
    refs = [a["artifact_ref"] for a in extract_artifacts(text)]
    assert "CWE-89" in refs
    assert "CVE-2021-44228" in refs  # CVE is extracted, distinct from CWE
    assert any(r.upper().startswith("RFC") and "6749" in r for r in refs)
    assert any(r.startswith("10.1145/") for r in refs)
    assert "https://example.com/spec" in refs
    assert "src/auth/login.py:42" in refs


def test_extract_artifacts_dedups_and_drops_doi_inside_url():
    from rapier.stages.resolver._extract import extract_artifacts

    text = "See https://doi.org/10.1145/3292500 and again https://doi.org/10.1145/3292500."
    refs = [a["artifact_ref"] for a in extract_artifacts(text)]
    assert refs == ["https://doi.org/10.1145/3292500"]  # one url; nested DOI not double-emitted


def test_extract_artifacts_empty_on_none_or_plain():
    from rapier.stages.resolver._extract import extract_artifacts

    assert extract_artifacts(None) == []
    assert extract_artifacts("no checkable artifacts here, only prose.") == []


def test_citation_gate_auto_extracts_from_recommendation(monkeypatch):
    from rapier.verify import service

    captured = {}

    def _fake(artifacts, pack_text=None, judge=False, map_claims=False):
        captured["artifacts"] = artifacts
        return ([{"status": "verified"}], {"gate": "clean", "grounding_rate": 1.0, "theater_flags": 0})

    monkeypatch.setattr(service, "verify_artifacts", _fake)
    env = Envelope(request="p", recommendation="Grounded in CWE-79 and https://example.org/x.")
    get_stage("citation_gate")().run(env, StageContext())
    refs = [a["artifact_ref"] for a in env.meta["artifacts"]]
    assert "CWE-79" in refs and "https://example.org/x" in refs
    assert captured["artifacts"] == env.meta["artifacts"]  # extracted artifacts were verified
    assert env.meta["citation_gate"]["gate"] == "clean"


def test_citation_gate_prepopulated_overrides_extraction(monkeypatch):
    from rapier.verify import service

    seen = {}

    def _fake(artifacts, pack_text=None, judge=False, map_claims=False):
        seen["artifacts"] = artifacts
        return ([], {"gate": "clean"})

    monkeypatch.setattr(service, "verify_artifacts", _fake)
    pre = [{"concern_id": "x1", "artifact_ref": "CWE-1", "concern_text": "pre", "load_bearing": True}]
    env = Envelope(request="p", recommendation="text that mentions CWE-999")
    env.meta["artifacts"] = list(pre)
    get_stage("citation_gate")().run(env, StageContext())
    assert seen["artifacts"] == pre  # caller-supplied artifacts win; CWE-999 not extracted over them


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


def _defin_env(with_cost_failure=True):
    from rapier.envelope import Envelope
    rows = [{"text": "CVE-2021-44228 (Log4Shell)", "claim": "CVE id",
             "bucket": "BUCKET3_FAIL", "rider": "Assumes cve_id — not in the problem; verify."}]
    fails = [{"text": "CVE-2021-44228 (Log4Shell)", "claim": "CVE id"}]
    riders = ["Assumes cve_id — not in the problem; verify."]
    if with_cost_failure:
        rows.append({"text": "roughly $50k/yr", "claim": "cost",
                     "bucket": "BUCKET3_FAIL", "rider": "Assumes cost — verify."})
        fails.append({"text": "roughly $50k/yr", "claim": "cost"})
        riders.append("Assumes cost — verify.")
    env = Envelope(request="x")
    env.verdict = "FAIL"
    env.meta["definitiveness"] = {"answer_verdict": "FAIL", "rows": rows,
                                  "failures": fails, "rider_lines": riders}
    env.trust_rider = {"assumptions_to_verify": riders}
    return env


def test_grounding_reconciles_verified_ref_out_of_failures():
    from rapier.stages.resolver.citation_gate import _reconcile_definitiveness_with_grounding
    env = _defin_env(with_cost_failure=True)
    _reconcile_definitiveness_with_grounding(
        env, [{"artifact_ref": "CVE-2021-44228", "status": "verified", "backend": "mitre-cve"}])
    d = env.meta["definitiveness"]
    # verified CVE reconciled out; the genuine cost estimate remains -> still FAIL, right reason
    assert env.verdict == "FAIL"
    assert [f["claim"] for f in d["failures"]] == ["cost"]
    assert d["rider_lines"] == ["Assumes cost — verify."]
    assert [g["ref"] for g in d["grounded_specifics"]] == ["CVE-2021-44228"]
    assert env.trust_rider["verified_externally"]
    assert env.trust_rider["assumptions_to_verify"] == ["Assumes cost — verify."]


def test_grounding_reconcile_lifts_verdict_when_only_failure_was_verified():
    from rapier.stages.resolver.citation_gate import _reconcile_definitiveness_with_grounding
    env = _defin_env(with_cost_failure=False)  # CVE is the ONLY failure
    _reconcile_definitiveness_with_grounding(
        env, [{"artifact_ref": "CVE-2021-44228", "status": "verified", "backend": "mitre-cve"}])
    assert env.verdict == "PASS"
    assert env.meta["definitiveness"]["failures"] == []


def test_grounding_reconcile_leaves_refuted_ref_as_failure():
    from rapier.stages.resolver.citation_gate import _reconcile_definitiveness_with_grounding
    env = _defin_env(with_cost_failure=False)
    _reconcile_definitiveness_with_grounding(
        env, [{"artifact_ref": "CVE-2021-44228", "status": "refuted", "backend": "mitre-cve"}])
    # a hallucinated (refuted) CVE must NOT be reconciled away
    assert env.verdict == "FAIL"
    assert env.meta["definitiveness"].get("reconciled_against_grounding") is not True
