"""Accountable Agents item 3: compare what the method declared with what actually ran.

At run time the pipeline records every declared stage's outcome (ran,
failed-soft, skipped, not-reached) and the report lists them. Before a run,
``manifest_warnings`` and ``rapier doctor`` flag a method that declares no
correctness gate, or a gate whose result can never reach the report.
"""
from __future__ import annotations

import json

import pytest

from rapier import operator_policy as OP
from rapier.manifest import Manifest
from rapier.pipeline import Pipeline, StageSpec, manifest_warnings
from rapier.presets import PRESETS, VERIFY_MODES, load_preset
from rapier.stage import TransformStage, register_stage


@register_stage("_test_boom")
class _Boom(TransformStage):
    def run(self, env, ctx):
        raise RuntimeError("stage exploded")


@register_stage("_test_rec")
class _Rec(TransformStage):
    writes_answer = True

    def run(self, env, ctx):
        env.recommendation = "Prefer the simpler option."
        return env


def _run(stages, **kw):
    return Pipeline([StageSpec(stage=s) for s in stages], name="t").run("decide X", **kw)


def test_every_declared_stage_is_accounted_for():
    env = _run(["_test_rec", "_test_boom", "citation_gate", "compose"])
    runs = {r["stage"]: r for r in env.meta["stage_runs"]}
    assert [r["stage"] for r in env.meta["stage_runs"]] == [
        "_test_rec", "_test_boom", "citation_gate", "compose"]
    assert runs["_test_rec"]["status"] == "ran"
    assert runs["_test_boom"]["status"] == "failed-soft"
    assert "stage exploded" in runs["_test_boom"]["detail"]
    assert runs["citation_gate"]["status"] == "skipped"  # nothing checkable cited


def test_report_lists_what_ran_and_the_failure():
    env = _run(["_test_rec", "_test_boom", "compose"])
    report = " ".join(env.meta["report_md"].split())
    assert "WHAT RAN" in report
    assert "_test_boom: FAILED (the run went on without it) (stage exploded)" in report
    assert "compose: wrote this report" in report
    row = env.meta["ceremony_row"]
    assert row["stages_failed_soft"] == ["_test_boom"] and row["stages_declared"] == 3
    assert env.trust_rider["stages_run"][1]["status"] == "failed-soft"


def test_cancelled_run_marks_the_rest_not_reached():
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] >= 2

    env = _run(["_test_rec", "_test_boom", "compose"], cancel=cancel)
    assert [r["status"] for r in env.meta["stage_runs"]] == ["ran", "not-reached", "not-reached"]


# ── before a run: the manifest itself ───────────────────────────────────────

def test_warns_when_no_correctness_gate_is_declared():
    w = manifest_warnings([StageSpec(stage="author"), StageSpec(stage="compose")])
    assert any("no correctness gate" in x for x in w)


def test_warns_when_a_gate_runs_after_compose():
    w = manifest_warnings([StageSpec(stage="author"), StageSpec(stage="definitiveness_gate"),
                           StageSpec(stage="compose"), StageSpec(stage="citation_gate")])
    assert w == ["citation_gate runs after compose, so its result never reaches the verdict "
                 "or the report"]


def test_pipelines_without_a_report_are_not_held_to_it():
    assert manifest_warnings([StageSpec(stage="frame")]) == []


@pytest.mark.parametrize("name", sorted(PRESETS))
@pytest.mark.parametrize("verify", VERIFY_MODES)
@pytest.mark.parametrize("settle", [0, 2])
def test_every_builtin_preset_is_clean(name, verify, settle):
    assert manifest_warnings(load_preset(name, settle=settle, verify=verify).stages) == []


def test_warnings_reach_the_report():
    env = _run(["_test_rec", "compose"])
    assert "no correctness gate" in " ".join(env.meta["report_md"].split())
    assert env.meta["ceremony_row"]["manifest_warnings"]


# ── a custom manifest is held to the operator minimum too ───────────────────

def _manifest(tmp_path, stages):
    p = tmp_path / "m.yaml"
    p.write_text(json.dumps({"name": "custom", "pipeline": [{"stage": s} for s in stages]}))
    return str(p)


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "pol" / "policy.json"
    path.parent.mkdir()
    monkeypatch.setattr(OP, "POLICY_PATH", str(path))
    monkeypatch.delenv(OP.ENV_VAR, raising=False)
    return lambda lvl: path.write_text(json.dumps({"min_verify": lvl}))


def test_manifest_without_citation_gate_refused_under_minimum(policy, tmp_path):
    m = Manifest.load(_manifest(tmp_path, ["author", "definitiveness_gate", "compose"]))
    assert OP.decide_manifest(m.stages).mode == "off"  # control: no minimum, runs
    policy("gate")
    with pytest.raises(OP.PolicyError, match="custom manifest"):
        OP.decide_manifest(m.stages)


def test_cli_run_manifest_refused_under_minimum(policy, tmp_path, capsys):
    from rapier import cli

    path = _manifest(tmp_path, ["echo", "compose"])
    policy("gate")
    assert cli.main(["run", "--manifest", path, "--request", "x", "--no-save"]) == 2
    assert "below the operator minimum" in capsys.readouterr().err


def test_doctor_flags_a_bad_custom_manifest(policy, tmp_path, capsys):
    from rapier import cli

    path = _manifest(tmp_path, ["author", "compose"])
    assert cli.main(["doctor", "--manifest", path]) == 1
    out = capsys.readouterr().out
    assert "no correctness gate" in out


def test_doctor_reports_operator_minimum_and_presets(policy, capsys):
    from rapier import cli

    policy("gate")
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "at least 'gate'" in out and "within the calling agent's reach" in out
    assert "✓ spar" in out


# ── verifier R3-1 / R3-2: a gate counts only after the last answer-writing stage ──

_NEGATIVE = {
    # R3-1: no compose, so the bare recommendation is what the caller gets
    "author-only": (["author"],
                    ["author", "definitiveness_gate", "citation_gate", "compose"]),
    # R3-2: a citation gate before the author never sees the answer
    "gate-before-author": (["citation_gate", "author", "definitiveness_gate", "compose"],
                           ["author", "definitiveness_gate", "citation_gate", "compose"]),
    # the correctness gate before the last rewrite checks an earlier draft
    "defin-before-fix": (["author", "definitiveness_gate", "anchored_fix", "citation_gate", "compose"],
                         ["author", "anchored_fix", "definitiveness_gate", "citation_gate", "compose"]),
}


@pytest.mark.parametrize("case", sorted(_NEGATIVE))
def test_negative_manifest_warns_and_its_fix_does_not(case):
    bad, fixed = _NEGATIVE[case]
    assert manifest_warnings([StageSpec(stage=s) for s in bad])
    assert manifest_warnings([StageSpec(stage=s) for s in fixed]) == []


@pytest.mark.parametrize("case", ["author-only", "gate-before-author"])
def test_negative_manifest_refused_under_minimum_and_fix_allowed(policy, case):
    bad, fixed = _NEGATIVE[case]
    policy("gate")
    with pytest.raises(OP.PolicyError, match="custom manifest"):
        OP.decide_manifest([StageSpec(stage=s) for s in bad])
    assert OP.decide_manifest([StageSpec(stage=s) for s in fixed]).mode == "gate"


def test_manifest_that_writes_no_answer_is_exempt(policy):
    policy("gate")
    assert OP.decide_manifest([StageSpec(stage="frame")]).mode == "gate"
    assert OP.decide_manifest([StageSpec(stage="spark"), StageSpec(stage="cut")]).mode == "gate"


def test_unknown_stage_counts_as_writing_the_answer(policy):
    policy("gate")
    with pytest.raises(OP.PolicyError):
        OP.decide_manifest([StageSpec(stage="author"), StageSpec(stage="citation_gate"),
                            StageSpec(stage="mystery_rewriter"), StageSpec(stage="compose")])


def test_cli_run_author_only_manifest_refused(policy, tmp_path, capsys):
    from rapier import cli

    path = _manifest(tmp_path, ["author"])
    policy("gate")
    assert cli.main(["run", "--manifest", path, "--request", "x", "--no-save"]) == 2
    assert "below the operator minimum" in capsys.readouterr().err
