"""Map mode, SPARRING spec v1.4 §3.7.

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
    rid = re.search(r"UNIT ([RB]\d+)", prompt).group(1)
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
    A.MATCHER: lambda p, v: _all_new(p),
}


def _all_new(prompt):
    n = len(re.findall(r"^\d+\. ", prompt.split("CANDIDATES:")[-1], re.M))
    return {"judgments": [{"candidate": k, "new": True} for k in range(1, n + 1)]}


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
               A.MATCHER: lambda p, v: {"judgments": [{"candidate": 1, "new": False, "match": "I1"}]}})
    m = _m(env)
    assert m["gate"]["labels"]["R1"]["label"] == "HELD"
    assert "not new: “dup” matched to I1" in env.meta["report_md"]


@pytest.mark.parametrize("bad", [RuntimeError("matcher down"), "nonsense", {"new": False},
                                 {"judgments": [{"candidate": 1, "new": False}]},
                                 {"judgments": []},
                                 {"judgments": [{"candidate": 1, "new": False, "match": "I1"},
                                                {"candidate": 1, "new": False, "match": "I2"}]}])
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


# ── verifier M-1 / M-2 ─────────────────────────────────────────────────────

def test_crashed_framer_with_nothing_generated_defers_not_fails(run):
    env = run({FR.FRAME_DRAFT: RuntimeError("framer 503")}, regions=None)
    m = _m(env)
    g = m["gate"]
    assert g["fail"] == [] and g["verdict"] != "FAIL"
    assert any(x.startswith("search frame (drafting failed: framer 503") for x in g["did_not_run"])
    assert map_final_state(m)[0] == "defer"
    assert not any(c["purpose"] == "generate" for c in m["calls"])


def test_control_frame_recorded_out_of_order_still_fails(run):
    m = _m(run())
    m["frame"]["recorded_at_call"] = len(m["calls"])
    assert evaluate(m)["verdict"] == "FAIL"


def test_generation_without_any_frame_fails(run):
    m = _m(run())
    del m["frame"]
    g = evaluate(m)
    assert any("not recorded before generation" in x for x in g["fail"])


@pytest.mark.parametrize("entry", ["N/A", "none", "null", "-", "—", "unknown", "TBD",
                                   "general", "various", "see title", "Not applicable"])
def test_placeholder_entry_id_drops_to_c(run, entry):
    env = run({F.GENERATE: lambda p, v: {"items": [
        {"text": "x", "anchor": {"class": "b", "title": "Industry best practice", "entry_id": entry}}]}})
    a = _m(env)["items"][0]["anchor"]
    assert a["class"] == "c" and a["downgraded_from"] == "b"


def test_control_real_entry_id_is_named(run):
    env = run({F.GENERATE: lambda p, v: {"items": [
        {"text": "x", "anchor": {"class": "b", "title": "OWASP ASVS", "entry_id": "V1.2"}}]}})
    assert _m(env)["items"][0]["anchor"]["citation_check"] == "named"


def test_empty_region_is_disclosed(run):
    env = run({F.GENERATE: lambda p, v: {"items": []}})
    assert "this unit yielded no items" in env.meta["report_md"]


# ── live-run regressions (D-100, plan 19): real loci the first check misread ──

LIVE_SOURCE = """## 2. Tiers
Every /sync-now step sorts cleanly into one of two buckets. There is no spectrum.

| Outcome | What happens |
|---|---|
| All clear | **Say nothing.** Not one line. |

> **Decision (locked): the sweep is a single process emitting one consolidated
> result.**

- **`ran_at` per check** — every rendered advisory line carries its age.
## 9. Cache
The cache stores `launched_at` and `finding_id` for each check.
"""


@pytest.mark.parametrize("locus", [
    "§2: 'Every /sync-now step sorts cleanly into one of two buckets. There is no spectrum.'",
    "§2: “Every /sync-now step sorts cleanly … There is no spectrum.”",
    "§2 table: 'All clear — Say nothing. Not one line.'",
    "the sweep is a single process emitting one consolidated result",
    "§9 cache format includes `launched_at`, but no sweep id",       # section + comment
    "§2 table vs §9 list",
    "§9: \"`ran_at` per check — every rendered advisory line carries its age.\"",
])
def test_real_loci_are_found(locus):
    from rapier.stages.map.cite import locus_exists
    assert locus_exists(locus, LIVE_SOURCE)


@pytest.mark.parametrize("locus", [
    "§2: 'Every step is on a spectrum of urgency.'",        # real section, made-up quote
    "§4: 'Every /sync-now step sorts cleanly into one of two buckets.'"[:4] + " table",  # no §4
    "Lockfile at ~/.loom/audit/.lock",                             # not in the source
    "§2 table vs §13 list",                               # §13 doesn't exist
])
def test_misquotes_and_missing_sections_are_fabricated(locus):
    from rapier.stages.map.cite import locus_exists
    assert not locus_exists(locus, LIVE_SOURCE)


# ── Pattern Lock per region (sparring-2's D-100 finding: 0 merges over 1,135 items) ──

def test_pattern_lock_runs_per_region_and_says_so(run):
    seen = []

    def merge(p, v):
        seen.append(p)
        ids = re.findall(r"^(I\d+):", p, re.M)
        return {"groups": [{"keep": ids[0], "merged": [ids[1]]}]} if len(ids) >= 2 else {"groups": []}

    env = run({F.MERGE: merge})
    m = _m(env)
    pl = m["pattern_lock"]
    assert pl["merge_calls"] == 2 and len(seen) == 2          # one per region
    assert all("FIELD (one unit)" in p for p in seen)
    assert pl["merged_count"] == 2 and pl["scope"] == "within each unit"
    flat = " ".join(env.meta["report_md"].split())
    assert "in 2 merge call(s) made, within each unit; duplicates across units are not merged" in flat


def test_one_region_merge_failing_is_partial_and_disclosed(run):
    def merge(p, v):
        if "I1:" in p:
            raise RuntimeError("timeout")
        return {"groups": []}

    env = run({F.MERGE: merge})
    pl = _m(env)["pattern_lock"]
    assert pl["status"] == "partial" and "timeout" in pl["reason"]
    assert "Pattern Lock did not run for:" in " ".join(env.meta["report_md"].split())


# ── verifier C-1: a short invented quote must not pass ─────────────────────

@pytest.mark.parametrize("bad,good", [
    ('§2 "instantly"', '§2 "eu-west"'),
    ('§2: "eu-east"', '§2: "eu-west"'),
    ('"Rollout happens region by region ... instantly"', '"Rollout happens region by region ... eu-west"'),
    ('"The service must answer ... never"', '"The service must answer ... 200 ms"'),
])
def test_short_invented_quote_is_fabricated_and_real_control_found(bad, good):
    from rapier.stages.map.cite import locus_exists
    assert not locus_exists(bad, SOURCE)
    assert locus_exists(good, SOURCE)


def test_invented_backtick_identifier_is_fabricated():
    from rapier.stages.map.cite import locus_exists
    assert not locus_exists("\u00a72: `fake_identifier`", LIVE_SOURCE)
    assert locus_exists("\u00a79: `finding_id`", LIVE_SOURCE)


# ── sparring-1: a quote spliced from scraps must not count as cited ────────

def test_fragment_spliced_quote_is_fabricated():
    from rapier.stages.map.cite import locus_exists
    assert not locus_exists('"the … sweep … is … never"', LIVE_SOURCE)
    # two-word pieces, but drawn from different sections: still not one quote
    assert not locus_exists('"Every /sync-now step sorts … for each check"', LIVE_SOURCE)
    # out of order within one section
    assert not locus_exists('"There is no spectrum … Every /sync-now step"', LIVE_SOURCE)


def test_control_real_elided_quote_within_one_section_is_found():
    from rapier.stages.map.cite import locus_exists
    assert locus_exists('"Every /sync-now step sorts cleanly … There is no spectrum."', LIVE_SOURCE)
    assert locus_exists('"eu-west"', SOURCE)  # an unelided short quote is still checked whole


# ── v1.4 overlap: newness against the whole field, credit and tags ───────────

def test_matcher_sees_the_whole_field_in_one_call_per_attack(run):
    seen = []

    def matcher(p, v):
        seen.append(p)
        return _all_new(p)

    run({A.ATTACK_CONTRACT: lambda p, v: {"candidates": [
         {"text": "c1", "anchor": {"class": "c", "basis": "x"}},
         {"text": "c2", "anchor": {"class": "c", "basis": "x"}}]},
         A.MATCHER: matcher})
    assert len(seen) == 2  # one per unit, not one per candidate
    r1 = seen[0]
    assert "R1 cited risk" in r1 and "R2 cited risk" in r1  # the whole field


def test_cross_unit_duplicate_is_not_new_and_overlap_is_tagged(run):
    """v1.4: a candidate already in another unit is not new; a new one is
    credited to the attacking unit and tagged with others it fits; the other
    unit's label depends only on its own attack."""
    def attack(p, v):
        if "UNIT R1" in p:
            return {"candidates": [{"text": "R2 cited risk again", "anchor": {"class": "a", "locus": "§1"}},
                                   {"text": "shared risk", "anchor": {"class": "c", "basis": "x"}}]}
        return {"none_found": True, "searched": "all"}

    def matcher(p, v):
        m = re.search(r"^(I\d+) \[R2\]: R2 cited risk$", p, re.M)
        return {"judgments": [{"candidate": 1, "new": False, "match": m.group(1)},
                              {"candidate": 2, "new": True, "also_fits": ["R2", "R9", "R1"]}]}

    env = run({A.ATTACK_CONTRACT: attack, A.MATCHER: matcher})
    m = _m(env)
    c1, c2 = m["attacks"]["R1"]["candidates"]
    assert c1["new"] is False and c2["new"] is True and c2["also_fits"] == ["R2"]
    item = next(i for i in m["items"] if i["text"] == "shared risk")
    assert item["regions"] == ["R1"] and item["also_fits"] == ["R2"]
    assert m["gate"]["labels"]["R1"]["label"] == "HELD"   # the only new one is class c
    assert m["gate"]["labels"]["R2"]["label"] == "HELD"   # its own attack held
    assert "credited to R1, also fits R2" in env.meta["report_md"]


# ── v1.4 labels: a new candidate counts by the class it CLAIMS ─────────────────

def test_misquoting_attack_cannot_produce_held(run):
    """The candidate claims class a with a misquote: the check demotes the item
    to c, but the unit is YIELDING, not HELD."""
    env = run({A.ATTACK_CONTRACT: lambda p, v: {"candidates": [
        {"text": "gap", "anchor": {"class": "a",
         "locus": '§2: "Rollout happens region by region, beginning with eu-west."'}}]}
        if "UNIT R2" in p else {"none_found": True, "searched": "all"}})
    m = _m(env)
    c = m["attacks"]["R2"]["candidates"][0]
    assert c["claimed_class"] == "a" and c["anchor"]["class"] == "c" and c["anchor"]["misquoted"]
    assert m["gate"]["labels"]["R2"]["label"] == "YIELDING"


def test_control_new_speculative_candidate_keeps_held(run):
    env = run({A.ATTACK_CONTRACT: lambda p, v: {"candidates": [
        {"text": "maybe", "anchor": {"class": "c", "basis": "a hunch"}}]}})
    assert _m(env)["gate"]["labels"]["R1"]["label"] == "HELD"


# ── v1.4 class a check: misquoted vs fabricated ──────────────────────────────

@pytest.mark.parametrize("locus,status", [
    ('§2: "Rollout happens region by region, starting with eu-west."', "found"),
    ('§2: "rollout happens   REGION by **region**, starting with eu-west"', "found"),   # normalized
    ('§2: "Rollout happens region by region, beginning with eu-west."', "misquoted"),  # one word
    ('§1: "The service must answer within 300 ms."', "fabricated"),                    # a number
    ('§1: "The service should answer within 200 ms."', "fabricated"),                  # a modal
    ('§1: "The service must not answer within 200 ms."', "fabricated"),                # a negation
    ('§2: "Deployment is fully automated across every cluster."', "fabricated"),       # nothing close
])
def test_class_a_check_statuses(locus, status):
    from rapier.stages.map.cite import check_locus
    assert check_locus(locus, SOURCE)["status"] == status


def test_misquote_shows_the_nearest_passage():
    from rapier.stages.map.cite import check_locus
    v = check_locus('"Rollout happens region by region, beginning with eu-west."', SOURCE)
    assert v["passage"] == "rollout happens region by region starting with eu-west"
    assert v["similarity"] >= 0.8


def test_misquote_threshold_comes_from_the_manifest():
    from rapier.stages.map.cite import check_locus
    loc = '"Rollout happens region by region, beginning with eu-west."'
    assert check_locus(loc, SOURCE, threshold=0.95)["status"] == "fabricated"
    cfg = next(s for s in load_preset("map").stages if s.stage == "map_cite").config
    assert cfg["misquote_threshold"] == 0.8  # in the manifest, so in the method hash


def test_ellipsis_fragment_is_judged_on_its_own():
    from rapier.stages.map.cite import check_locus
    ok = check_locus('§2: "Rollout happens region by region … starting with eu-west."', SOURCE)
    mis = check_locus('§2: "Rollout happens … by region, beginning with eu-west."', SOURCE)  # 4 of 5 words
    fab = check_locus('§2: "Rollout happens region by region … never eu-west."', SOURCE)
    assert ok["status"] == "found"
    assert mis["status"] == "misquoted" and mis["quote"] == "by region, beginning with eu-west."
    assert check_locus('§2: "Rollout happens … beginning with the eu-west."', SOURCE)["status"] == "fabricated"
    assert fab["status"] == "fabricated"


def test_ellipsis_quote_must_be_within_the_cited_section():
    from rapier.stages.map.cite import check_locus
    # both fragments are real, in order, but in §1 and §2: cited §2, so it fails
    v = check_locus('§2: "The service must answer … starting with eu-west"', SOURCE)
    assert v["status"] == "fabricated"


def test_misquoted_item_is_demoted_and_shown_not_failed(run):
    env = run({F.GENERATE: lambda p, v: {"items": [
        {"text": "x", "anchor": {"class": "a",
         "locus": '§2: "Rollout happens region by region, beginning with eu-west."'}}]}})
    m = _m(env)
    assert m["gate"]["verdict"] != "FAIL"
    flat = " ".join(env.meta["report_md"].split())
    assert "MISQUOTED, demoted to speculative" in flat
    assert "nearest source passage “rollout happens region by region starting with eu-west”" in flat


def test_more_than_ten_percent_misquoted_is_review(run):
    mis = '§2: "Rollout happens region by region, beginning with eu-west."'
    good = '§2: "Rollout happens region by region, starting with eu-west."'

    def gen(p, v, loci):
        return {"items": [{"text": f"t{k}", "anchor": {"class": "a", "locus": loc}}
                          for k, loc in enumerate(loci)]}

    bad_env = run({F.GENERATE: lambda p, v: gen(p, v, [mis] + [good] * 4)})   # 2 of 10 = 20%
    ok_env = run({F.GENERATE: lambda p, v: gen(p, v, [mis] + [good] * 9)})    # 2 of 20 = 10%
    assert any("misquoted" in r for r in _m(bad_env)["gate"]["review"])
    assert not any("misquoted" in r for r in _m(ok_env)["gate"]["review"])


# ── v1.4 frame size, blocks and the call plan ────────────────────────────────

def _regions(n):
    return [{"name": f"Area {k}", "definition": f"area {k} of the plan"} for k in range(1, n + 1)]


def test_supplied_frame_above_the_cap_is_refused_before_any_call(run):
    env = run(regions=_regions(13))
    m = _m(env)
    assert m["calls"] == [] and m["stopped"]["refused"] is True
    assert map_final_state(m)[0] == "refuse"
    assert "above the cap of 12" in env.meta["report_md"]


def test_control_twelve_regions_run(run):
    env = run(regions=_regions(12))
    m = _m(env)
    assert not m.get("stopped") and len(m["frame"]["units"]) == 12


def test_declared_higher_cap_is_stated(run):
    env = run(regions=_regions(13), map_max_units=13)
    m = _m(env)
    assert not m.get("stopped") and m["frame"]["max_units"] == 13
    assert "Units: 13, cap 13 (declared by the frame)" in env.meta["report_md"]


def test_blocked_frame_runs_per_block_and_labels_claim_nothing_per_region(run):
    regs = _regions(13)
    blocks = [{"name": "First half", "basis": "section", "members": [f"Area {k}" for k in range(1, 8)]},
              {"name": "Second half", "basis": "section", "members": [f"R{k}" for k in range(8, 14)]}]
    env = run(regions=regs, map_blocks=blocks)
    m = _m(env)
    units = m["frame"]["units"]
    assert [u["id"] for u in units] == ["B1", "B2"] and units[1]["members"][0] == "R8"
    assert sum(1 for c in m["calls"] if c["purpose"] == "generate") == 2
    assert set(m["gate"]["labels"]) == {"B1", "B2"}
    flat = " ".join(env.meta["report_md"].split())
    assert "HELD for a block of 7 regions, attacked together; the attack was not run region by region." in flat
    assert "R8 Area 8: area 8 of the plan" in flat  # members listed verbatim


@pytest.mark.parametrize("blocks,why", [
    ([{"name": "A", "basis": "section", "members": ["R1"]},
      {"name": "B", "basis": "lens", "members": ["R2"]}], "mix two seams"),
    ([{"name": "A", "basis": "section", "members": ["R1"]}], "regions in no block"),
    ([{"name": "A", "basis": "section", "members": ["R1", "R1", "R2"]}], "more than one block"),
    ([{"name": "A", "basis": "section", "members": ["R1", "Nope"]}], "not a region"),
    ([{"name": "A", "members": ["R1", "R2"]}], "needs a basis"),
])
def test_invalid_blocks_are_refused(run, blocks, why):
    env = run(map_blocks=blocks)
    m = _m(env)
    assert m["calls"] == [] and why in m["stopped"]["cause"]
    assert map_final_state(m)[0] == "refuse"


def test_drafted_frame_above_the_cap_is_grouped_by_the_framer_not_the_generator(run):
    def block(p, v):
        ids = re.findall(r"^(R\d+) ", p, re.M)
        return {"basis": "lens", "blocks": [{"name": "Odd", "members": ids[0::2]},
                                            {"name": "Even", "members": ids[1::2]}]}

    env = run({FR.FRAME_DRAFT: lambda p, v: {"regions": _regions(14)},
               FR.FRAME_BLOCK.format(cap=12): block}, regions=None)
    m = _m(env)
    grp = [c for c in m["calls"] if c["purpose"] == "frame-block"]
    assert len(grp) == 1 and grp[0]["role"] == "framer" and grp[0]["vendor"] == "openai"
    assert [u["id"] for u in m["frame"]["units"]] == ["B1", "B2"]
    assert m["frame"]["recorded_at_call"] == 3  # draft, block, attack: all before SPARK


def test_frame_attack_addition_past_the_cap_stops_before_generation(run):
    env = run({FR.FRAME_ATTACK: lambda p, v: {"missing_regions": [
        {"name": "Security", "definition": "abuse", "reason": "not covered"}]}}, regions=_regions(12))
    m = _m(env)
    assert m["stopped"]["refused"] is False and "13 units" in m["stopped"]["cause"]
    assert not any(c["purpose"] == "generate" for c in m["calls"])
    g = m["gate"]
    assert g["verdict"] == "REVIEW" and "incomplete run: stopped before generation" in g["review"]
    assert map_final_state(m)[0] == "defer"


def test_plan_over_the_call_budget_does_not_start(run):
    env = run(map_max_calls=12)   # 1 frame-attack + 6 per unit x 2 units = 13
    m = _m(env)
    assert m["calls"] == [] and m["plan"]["planned_calls"] == 13
    assert map_final_state(m)[0] == "refuse"
    assert "over the call budget of 12" in env.meta["report_md"]


def test_control_plan_within_the_call_budget_runs_and_reports_planned_and_actual(run):
    env = run(map_max_calls=13)
    m = _m(env)
    assert not m.get("stopped") and len(m["calls"]) <= 13
    flat = " ".join(env.meta["report_md"].split())
    assert "Planned: 13 model call(s) at most (6 per unit over 2 unit(s), plus the frame calls), " \
           "under a call budget of 13" in flat
    assert f"Actual: {len(m['calls'])} attempted" in flat


def test_unit_cut_short_by_the_dollar_cap_names_its_cause(run):
    from rapier import budget

    def attack(p, v):
        if "UNIT R2" in p:
            raise budget.BudgetExceeded("dollar cap $1.00 reached")
        return {"none_found": True, "searched": "all"}

    env = run({A.ATTACK_CONTRACT: attack})
    g = _m(env)["gate"]
    assert any("stopped at the dollar cap" in w for w in g["labels"]["R2"]["why"])
    assert not any("dollar cap" in w for w in g["labels"]["R1"]["why"])
    assert any("units cut short: R2" in r for r in g["review"])


# ── v1.4 inputs: MCP and CLI carry blocks, a higher cap and a call budget ──────

def test_mcp_map_passes_blocks_cap_and_call_budget(monkeypatch):
    from rapier.mcp import tools

    seen = {}

    class _Pipe:
        def run(self, request, **kw):
            seen.update(kw["seed_meta"])
            raise RuntimeError("stop here")

    class _Preset:
        policy = None

        def build(self):
            return _Pipe()

    monkeypatch.setattr(tools, "preflight_error", lambda: None)
    monkeypatch.setattr(tools, "load_preset", lambda *a, **k: _Preset())
    blocks = [{"name": "A", "basis": "lens", "members": ["R1"]}]
    with pytest.raises(RuntimeError, match="stop here"):
        tools.run_map("x", regions=["R"], blocks=blocks, max_units=20, max_calls=50)
    assert seen["map_blocks"] == blocks and seen["map_max_units"] == 20 and seen["map_max_calls"] == 50
    assert seen["map_regions"] == ["R"]


def test_cli_map_reads_a_frame_file(monkeypatch, tmp_path):
    import rapier.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "_run", lambda preset, req, ld, seed_meta=None, **k: seen.update(seed_meta) or 0)
    monkeypatch.setattr("rapier.onboarding.preflight_error", lambda: None)
    f = tmp_path / "frame.json"
    f.write_text(json.dumps({"regions": ["A", "B"], "blocks": [
        {"name": "All", "basis": "section", "members": ["A", "B"]}], "max_units": 14}))
    assert cli.main(["map", "--frame", str(f), "--max-calls", "40", "--request", "request text"]) == 0
    assert seen["map_regions"] == ["A", "B"] and seen["map_max_units"] == 14
    assert seen["map_blocks"][0]["members"] == ["A", "B"] and seen["map_max_calls"] == 40


# ── D-100 re-score under v1.4: real loci the checker misread ───────────────────

D100_SOURCE = """## 7. Plan
1. **`inventory.sh --no-fetch`.** Everything except the `timeout 8 git fetch --all`
   per clone (`full-worktree-cleanup/scripts/inventory.sh:95`) is local.
2. `/sync-now` output is **byte-identical** to today's.
## 9. Cache
This plan's `finding_id` is the key.
## 10. Sinks
       │ loom-audit-sweep │──write──▶│ cache │
"""


@pytest.mark.parametrize("locus", [
    "P0 Exit 2: '/sync-now output is byte-identical to today's'",          # apostrophe in a quote
    "Everything except the timeout 8 git fetch --all per clone "
    "(full-worktree-cleanup/scripts/inventory.sh:95) is local.",            # "( `x`" folding
    "finding_id",                                                           # a bare identifier
    '§10: "loom-audit-sweep │──write──▶│ cache"',                            # box drawing folded
    '§7: "Everything except the" and §9: "is the key"',                     # each quote, its own section
])
def test_d100_real_loci_are_found(locus):
    from rapier.stages.map.cite import check_locus
    assert check_locus(locus, D100_SOURCE)["status"] == "found"


# An ellipsis quote is held to its cited section; a quote with no ellipsis only
# needs to be in the source (v1.4), so the control for scoping uses an ellipsis.
@pytest.mark.parametrize("locus", ["cache", "1234567", '§7: "This plan\'s … is the key"'])
def test_control_weak_or_misplaced_loci_are_not_found(locus):
    from rapier.stages.map.cite import check_locus
    assert check_locus(locus, D100_SOURCE)["status"] != "found"
