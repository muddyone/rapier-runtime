"""Proposer stage plumbing — monkeypatched convergence, no network."""
from __future__ import annotations

import pathlib

import rapier.stages.proposer.phase_stages as PS
from rapier.convergence import ConvergenceResult
from rapier.envelope import Envelope
from rapier.manifest import Manifest
from rapier.stage import StageContext, get_stage


def _patch(monkeypatch, payload):
    monkeypatch.setattr(PS, "available_vendors", lambda: ["mock", "gemini", "xai"])
    monkeypatch.setattr(
        PS, "run_convergence",
        lambda *a, **k: ConvergenceResult(True, payload, [], 1, False, 0),
    )


def test_spark_writes_options_and_meta(monkeypatch):
    _patch(monkeypatch, ["A", "B", "C"])
    env = Envelope(request="decide")
    get_stage("spark")().run(env, StageContext())
    assert env.options == ["A", "B", "C"]
    meta = env.meta["proposer"]["spark"]
    assert meta["converged"] is True
    assert meta["generator_vendor"] == "gemini" and meta["challenger_vendor"] == "xai"
    assert meta["cross_vendor"] is True


def test_pattern_lock_dedups_options(monkeypatch):
    _patch(monkeypatch, ["A", "B"])
    env = Envelope(request="decide", options=["A", "A2", "B"])
    get_stage("pattern_lock")().run(env, StageContext())
    assert env.options == ["A", "B"]


def test_cut_writes_committed(monkeypatch):
    _patch(monkeypatch, {"committed": "Option B", "rationale": "best tradeoff"})
    env = Envelope(request="decide", options=["A", "Option B"])
    get_stage("cut")().run(env, StageContext())
    assert env.committed == "Option B"


def test_no_vendor_skips_cleanly(monkeypatch):
    monkeypatch.setattr(PS, "available_vendors", lambda: ["mock"])
    env = Envelope(request="decide")
    get_stage("spark")().run(env, StageContext())
    assert env.options == []
    assert env.trace[-1].summary.startswith("no vendor available")


def test_proposer_manifest_loads_and_registers():
    path = pathlib.Path(__file__).parent.parent / "manifests" / "sparring.proposer.yaml"
    m = Manifest.load(str(path))
    assert [s.stage for s in m.stages] == ["spark", "pattern_lock", "cut"]
    m.build()
