"""Map mode, SPARRING spec v1.3 §3.7.

Runs go through the real Pipeline and the real map stages, with only the model
call stubbed (``_common.client_for``), so every contract parser runs. Each
failure case is paired with a control that differs only in the reply that fails.
"""
from __future__ import annotations

import json
import re

import pytest

from rapier.pipeline import Pipeline
from rapier.presets import load_preset
from rapier.stages.map import _common as C
from rapier.stages.map import attack as A
from rapier.stages.map import field as F
from rapier.stages.map import frame as FR
from rapier.stages.map.compose import LIMIT_SENTENCE, UNMEASURED_SENTENCE, map_final_state
from rapier.stages.map.gate import evaluate

SOURCE = """# Plan 19
## 1. Goals
The service must answer within 200 ms.
## 2. Rollout
Rollout happens region by region, starting with eu-west.
"""

REGIONS = [{"name": "Performance", "definition": "latency and load"},
           {"name": "Rollout", "definition": "how the change reaches users"}]


def _gen_items(prompt):
    rid = re.search(r"REGION (R\d+)", prompt).group(1)
    return {"items": [{"text": f"{rid} cited risk", "anchor": {"class": "a", "locus": "§1"}},
                      {"text": f"{rid} ground risk",
                       "anchor": {"class": "b", "title": "OWASP ASVS", "entry_id": "V1.2"}}]}


DEFAULT = {
    FR.FRAME_DRAFT: lambda p, v: {"regions": REGIONS},
    FR.FRAME_ATTACK: lambda p, v: {"none_found": True, "searched": "the plan's sections"},
    F.GENERATE: lambda p, v: _gen_items(p),
    F.EXPAND: lambda p, v: {"items": []},
    F.MERGE: lambda p, v: {"groups": []},
    F.FILTER: lambda p, v: {"wrong_merges": []},
    A.ATTACK_CONTRACT: lambda p, v: {"none_found": True, "searched": "sections 1-2 and the field"},
    A.MATCHER: lambda p, v: {"new": True},
}


class _Resp:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def run(monkeypatch):
    """Run the map preset with scripted replies. ``overrides`` maps a prompt
    template to a function (prompt, vendor) -> reply, an Exception to raise, or a
    raw string to return as-is."""
    def go(overrides=None, vendors=("anthropic", "openai"), regions=REGIONS, **meta):
        script = {**DEFAULT, **(overrides or {})}
        monkeypatch.setattr(C, "available_vendors", lambda: list(vendors))

        class _Client:
            def __init__(self, vendor):
                self.vendor = vendor

            def complete(self, system, prompt):
                r = script[system]
                if isinstance(r, Exception):
                    raise r
                if isinstance(r, str):
                    return _Resp(r)
                return _Resp(json.dumps(r(prompt, self.vendor)))

        monkeypatch.setattr(C, "client_for", lambda v: _Client(v))
        seed = dict(meta)
        if regions is not None:
            seed["map_regions"] = regions
        return Pipeline.from_manifest(load_preset("map")).run(SOURCE, seed_meta=seed)
    return go


def _m(env):
    return env.meta["map"]


# ── the control: a clean, independent run passes ───────────────────────────

def test_clean_run_is_held_pass_act(run):
    env = run()
    g = _m(env)["gate"]
    assert g["overall"] == "HELD" and g["verdict"] == "PASS" and g["did_not_run"] == []
    assert map_final_state(_m(env))[0] == "act"
    assert env.meta["ceremony_row"]["final_state"] == "act"


# ── F1 / F2: the frame ─────────────────────────────────────────────────────

def test_frame_recorded_with_hash_before_generation(run):
    m = _m(run())
    f = m["frame"]
    assert f["source"] == "supplied" and f["hash"].startswith("sha256:")
    first_gen = next(i for i, c in enumerate(m["calls"]) if c["purpose"] == "generate")
    assert f["recorded_at_call"] <= first_gen


def test_frame_recorded_after_generation_fails(run):
    m = _m(run())
    m["frame"]["recorded_at_call"] = len(m["calls"])  # as if recorded after SPARK
    g = evaluate(m)
    assert g["verdict"] == "FAIL" and any("not recorded before generation" in x for x in g["fail"])


def test_drafted_frame_never_drafted_by_the_generator(run):
    m = _m(run(regions=None))
    draft = next(c for c in m["calls"] if c["purpose"] == "frame-draft")
    assert m["frame"]["source"] == "drafted"
    assert draft["role"] == "framer" and draft["vendor"] == m["vendors"]["challenger"]
    assert draft["vendor"] != m["vendors"]["generator"]


def test_frame_attack_runs_on_another_vendor_and_its_additions_join(run):
    env = run({FR.FRAME_ATTACK: lambda p, v: {"missing_regions": [
        {"name": "Security", "definition": "abuse and access", "reason": "the plan exposes an API"}]}})
    m = _m(env)
    assert m["frame_attack"]["vendor"] != m["vendors"]["generator"]
    assert [r["name"] for r in m["frame"]["regions"]][-1] == "Security"
    assert m["frame"]["regions"][-1]["added"]["reason"] == "the plan exposes an API"
    assert "Security" in env.meta["report_md"]


@pytest.mark.parametrize("bad", [RuntimeError("503"), "not json", {"none_found": True},
                                 {"missing_regions": []}])
def test_frame_attack_failure_is_did_not_run_and_defers(run, bad):
    over = bad if not isinstance(bad, dict) else (lambda p, v, b=bad: b)
    env = run({FR.FRAME_ATTACK: over}, regions=None)  # drafted frame
    m = _m(env)
    assert m["frame_attack"]["status"] in ("error", "off-contract")
    g = m["gate"]
    assert g["overall"] == "YIELDING" and g["verdict"] == "REVIEW"
    assert any("frame-attack" in x for x in g["did_not_run"])
    assert map_final_state(m)[0] == "defer"


# ── R1: probed is read from the call record ────────────────────────────────

def test_crashed_generation_is_unprobed_and_defers(run):
    calls = {"n": 0}

    def gen(p, v):
        calls["n"] += 1
        if "R2" in p:
            raise RuntimeError("timeout")
        return _gen_items(p)

    def gen_wrap(p, v):
        return gen(p, v)

    env = run({F.GENERATE: gen_wrap})
    g = _m(env)["gate"]
    assert g["labels"]["R2"]["label"] == "UNPROBED" and g["overall"] == "UNPROBED"
    assert map_final_state(_m(env))[0] == "defer"


def test_probe_with_self_reported_coverage_but_no_call_is_unprobed(run):
    m = _m(run())
    m["calls"] = [c for c in m["calls"] if not (c["purpose"] == "generate" and c["region"] == "R1")]
    assert evaluate(m)["labels"]["R1"]["label"] == "UNPROBED"


# ── R2: an attack that didn't count is never HELD ──────────────────────────

@pytest.mark.parametrize("bad", [RuntimeError("reviewer 503"), "", "{}", {"candidates": []},
                                 {"none_found": True},
                                 {"candidates": [{"text": "x"}]}])
def test_attack_that_did_not_count_is_yielding_never_held(run, bad):
    over = bad if not isinstance(bad, dict) else (lambda p, v, b=bad: b)
    env = run({A.ATTACK_CONTRACT: over})
    m = _m(env)
    for rid in ("R1", "R2"):
        assert m["attacks"][rid]["status"] in ("error", "off-contract")
        assert m["gate"]["labels"][rid]["label"] == "YIELDING"
    assert m["gate"]["verdict"] == "REVIEW"
    assert map_final_state(m)[0] == "defer"


# ── R3 / R4: landed attacks, class c, and the matcher ───────────────────────

def test_new_cited_candidate_lands_and_blocks_held(run):
    env = run({A.ATTACK_CONTRACT: lambda p, v: {"candidates": [
        {"text": "eu-west outage plan missing", "anchor": {"class": "a", "locus": "starting with eu-west"}}]}})
    m = _m(env)
    assert m["gate"]["labels"]["R1"]["label"] == "YIELDING"
    assert m["gate"]["verdict"] != "PASS"  # a Challenger that found something missing can't PASS
    assert any(i["origin"] == "completeness-attack" for i in m["items"])
    assert map_final_state(m)[0] == "escalate"


def test_speculative_candidate_does_not_make_a_region_yielding(run):
    env = run({A.ATTACK_CONTRACT: lambda p, v: {"candidates": [
        {"text": "maybe solar flares", "anchor": {"class": "c", "basis": "imagination"}}]}})
    m = _m(env)
    assert m["gate"]["labels"]["R1"]["label"] == "HELD"


def test_not_new_is_shown_beside_its_match(run):
    env = run({A.ATTACK_CONTRACT: lambda p, v: {"candidates": [
               {"text": "dup", "anchor": {"class": "a", "locus": "§1"}}]},
               A.MATCHER: lambda p, v: {"new": False, "match": "I1"}})
    m = _m(env)
    assert m["gate"]["labels"]["R1"]["label"] == "HELD"
    assert "not new: “dup” matched to I1" in env.meta["report_md"]


@pytest.mark.parametrize("bad", [RuntimeError("matcher down"), "nonsense", {"new": False}])
def test_failed_matcher_counts_as_new(run, bad):
    over = bad if not isinstance(bad, dict) else (lambda p, v, b=bad: b)
    env = run({A.ATTACK_CONTRACT: lambda p, v: {"candidates": [
               {"text": "dup", "anchor": {"class": "a", "locus": "§1"}}]},
               A.MATCHER: over})
    m = _m(env)
    c = m["attacks"]["R1"]["candidates"][0]
    assert c["new"] is True and c["matcher_note"]
    assert m["gate"]["labels"]["R1"]["label"] == "YIELDING"  # never a false HELD
    assert "counted as new" in env.meta["report_md"]


# ── one vendor: attacks run but aren't independent ─────────────────────────

def test_single_vendor_attacks_are_yielding_and_escalate(run):
    env = run(vendors=("anthropic",))
    m = _m(env)
    assert m["vendors"]["cross_vendor"] is False
    assert all(l["label"] == "YIELDING" for l in m["gate"]["labels"].values())
    assert m["gate"]["did_not_run"] == []
    assert map_final_state(m)[0] == "escalate"
    assert "not independent" in env.meta["report_md"]


# ── I1 / I2 / I3: items ────────────────────────────────────────────────────

def test_item_fields(run):
    it = _m(run())["items"][0]
    assert set(it) >= {"id", "regions", "anchor", "origin", "role", "vendor", "merged_ids"}


def test_fabricated_class_a_fails_and_blocks_even_with_a_missing_attack(run):
    def gen(p, v):
        return {"items": [{"text": "x", "anchor": {"class": "a", "locus": "§9.9"}}]}
    env = run({F.GENERATE: gen, A.ATTACK_CONTRACT: RuntimeError("down")})
    m = _m(env)
    assert m["gate"]["verdict"] == "FAIL"
    assert map_final_state(m)[0] == "block"  # block > defer: the citation check decided


@pytest.mark.parametrize("anchor", [{"class": "b", "title": "industry best practice", "entry_id": ""},
                                    {"class": "b", "title": "", "entry_id": "V1"},
                                    {"class": "b", "title": "ASVS", "entry_id": "asvs"}])
def test_unnamed_ground_drops_to_c_with_a_note(run, anchor):
    env = run({F.GENERATE: lambda p, v: {"items": [{"text": "x", "anchor": anchor}]}})
    a = _m(env)["items"][0]["anchor"]
    assert a["class"] == "c" and a["downgraded_from"] == "b" and a["note"]


def test_more_than_half_speculative_is_review(run):
    env = run({F.GENERATE: lambda p, v: {"items": [
        {"text": "s1", "anchor": {"class": "c", "basis": "x"}},
        {"text": "s2", "anchor": {"class": "c", "basis": "y"}},
        {"text": "a1", "anchor": {"class": "a", "locus": "§1"}}]}})
    g = _m(env)["gate"]
    assert g["verdict"] == "REVIEW" and any("speculative" in x for x in g["review"])


def test_no_prompt_asks_for_a_count_or_a_ranking():
    prompts = [FR.FRAME_DRAFT, FR.FRAME_ATTACK, F.GENERATE, F.EXPAND, F.MERGE, F.FILTER,
               A.ATTACK_CONTRACT, A.MATCHER]
    for p in prompts:
        assert not re.search(r"\b(top|at least|at most|exactly)\s+\d+|\b\d+\s+(items|risks|options)\b"
                             r"|\brank (them|the items)\b|most important", p, re.I), p


def test_order_is_region_then_class_then_origin(run):
    from rapier.stages.map.compose import ordered_items

    env = run({F.GENERATE: lambda p, v: {"items": [
        {"text": "c item", "anchor": {"class": "c", "basis": "x"}},
        {"text": "a item", "anchor": {"class": "a", "locus": "§1"}}]}})
    order = [(i["regions"][0], i["anchor"]["class"]) for i in ordered_items(_m(env))]
    assert order == sorted(order, key=lambda t: (t[0], "abc".index(t[1])))


def test_merges_are_reported_not_gated(run):
    env = run({F.MERGE: lambda p, v: {"groups": [{"keep": "I1", "merged": ["I2"]}]}})
    m = _m(env)
    assert m["pattern_lock"]["merged_count"] == 1
    assert next(i for i in m["items"] if i["id"] == "I1")["merged_ids"] == ["I2"]
    assert m["gate"]["verdict"] == "PASS"


# ── G2 / G3 / G4 ───────────────────────────────────────────────────────────

def test_rider_has_seven_parts_and_the_fixed_sentences(run):
    report = run().meta["report_md"]
    flat = " ".join(report.split())
    for part in ("1. THE SEARCH FRAME", "2. REGION LABELS", "3. THE MAP GATE",
                 "4. ANCHORS, SUPPORT AND ORIGINS", "5. PATTERN LOCK MERGES",
                 "6. COVERAGE ESTIMATE", "7. LIMITS"):
        assert part in report
    assert LIMIT_SENTENCE in flat and UNMEASURED_SENTENCE in flat
    assert "Support check: did not run" in flat


def test_capture_recapture_never_moves_a_verdict(run):
    env = run()
    m = _m(env)
    before = evaluate(m)
    m["trials"] = [{"adversary": "x", "findings": ["a"]}, {"adversary": "y", "findings": ["b"]}]
    after = evaluate(m)
    assert before == after
    assert map_final_state(m) == ("act", map_final_state(m)[1])


def test_terminal_is_the_map_not_raw_spark_and_no_cut_or_resolver():
    names = [s.stage for s in load_preset("map").stages]
    assert names[-1] == "compose_map"
    assert not set(names) & {"cut", "author", "cross_review", "anchored_fix", "definitiveness_gate"}


def test_raw_spark_is_not_rendered_as_the_map(run):
    env = run({F.MERGE: lambda p, v: {"groups": [{"keep": "I1", "merged": ["I2"]}]}})
    the_map = env.meta["report_md"].split("COVERAGE RIDER")[0]
    assert "I2 " not in the_map  # merged away: shown only via I1's merged ids


# ── C2 / C3: policy and the method hash ────────────────────────────────────

def test_map_manifest_missing_the_attack_warns_and_is_refused_under_minimum(tmp_path, monkeypatch):
    from rapier import operator_policy as OP
    from rapier.pipeline import StageSpec, manifest_warnings

    stages = [StageSpec(stage=s) for s in
              ("map_frame", "map_spark", "map_pattern_lock", "map_cite", "map_gate", "compose_map")]
    assert any("map_attack" in w for w in manifest_warnings(stages))
    path = tmp_path / "policy.json"
    monkeypatch.setattr(OP, "POLICY_PATH", str(path))
    monkeypatch.delenv(OP.ENV_VAR, raising=False)
    assert OP.decide_manifest(stages).mode == "off"  # control: no minimum, allowed
    path.write_text('{"min_verify": "gate"}')
    with pytest.raises(OP.PolicyError):
        OP.decide_manifest(stages)
    assert OP.decide_manifest(load_preset("map").stages).mode == "gate"
    assert manifest_warnings(load_preset("map").stages) == []


def test_map_refused_with_one_vendor_under_require_independent(tmp_path, monkeypatch):
    from rapier import models
    from rapier import operator_policy as OP
    from rapier.mcp import tools

    path = tmp_path / "policy.json"
    path.write_text('{"require_independent": true}')
    monkeypatch.setattr(OP, "POLICY_PATH", str(path))
    monkeypatch.setattr(tools, "preflight_error", lambda: None)
    monkeypatch.setattr(models, "available_vendors", lambda: ["anthropic"])
    out = tools.run_map("map plan 19")
    assert out["ok"] is False and out["final_state"] == "refuse"


def test_method_hash_covers_the_attack_contract_and_matcher():
    from rapier.pipeline import method_fingerprint

    base = load_preset("map").stages
    h = method_fingerprint(base, None, ["anthropic", "openai"])["hash"]
    for key, stage in (("attack_contract", "map_attack"), ("matcher_prompt", "map_attack"),
                       ("frame_attack_prompt", "map_frame")):
        changed = load_preset("map").stages
        next(s for s in changed if s.stage == stage).config[key] += " edited"
        assert method_fingerprint(changed, None, ["anthropic", "openai"])["hash"] != h, key


def test_frame_hash_and_contract_hashes_reach_the_record(run):
    row = run().meta["ceremony_row"]
    assert row["frame_hash"].startswith("sha256:")
    assert row["attack_contract_hash"].startswith("sha256:")
    assert row["matcher_prompt_hash"].startswith("sha256:")
    assert row["method_hash"].startswith("sha256:")
