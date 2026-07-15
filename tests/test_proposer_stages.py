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


def _spark_config(manifest):
    return next(s for s in manifest.stages if s.stage == "spark").config


def test_proposer_preset_threads_seed_into_spark():
    from rapier.presets import load_preset

    m = load_preset("proposer", seed=["Use Postgres"])
    assert _spark_config(m).get("seed") == ["Use Postgres"]


def test_sparring_preset_threads_seed_into_spark():
    from rapier.presets import load_preset

    m = load_preset("sparring", seed=["Use Postgres"])
    assert _spark_config(m).get("seed") == ["Use Postgres"]


def test_seed_does_not_mutate_shared_preset_state():
    """A seeded preset must not leave the seed on the module-level template — the
    next unseeded load must come back clean (no shared-dict mutation)."""
    from rapier.presets import load_preset

    load_preset("proposer", seed=["sticky"])
    m2 = load_preset("proposer")
    assert "seed" not in _spark_config(m2)


def test_unseeded_preset_has_no_seed_key():
    from rapier.presets import load_preset

    assert "seed" not in _spark_config(load_preset("proposer"))
    assert "seed" not in _spark_config(load_preset("sparring"))


def test_proposer_manifest_loads_and_registers():
    path = pathlib.Path(__file__).parent.parent / "manifests" / "sparring.proposer.yaml"
    m = Manifest.load(str(path))
    assert [s.stage for s in m.stages] == ["spark", "pattern_lock", "cut"]
    m.build()


# --- seeded generation (Increment 3) -----------------------------------------
def _spy_agents(monkeypatch, seen):
    """Replace make_agents with a generator/challenger pair that records the
    ``prev_payload`` the generator is called with (so we can observe seeding)."""

    def fake_make_agents(gen_client, chal_client, cfg, request, phase_input):
        def gen(prev, concerns):
            seen.append(prev)
            return {"payload": ["X"], "agree": True, "reasoning": ""}

        def chal(payload):
            return {"concerns": [], "agree": True, "reasoning": ""}

        return gen, chal

    def fake_run_convergence(generator, challenger, cap, **k):
        generator(None, None)  # drive the opening round so seeding is observable
        return ConvergenceResult(True, ["X"], [], 1, False, 0)

    monkeypatch.setattr(PS, "available_vendors", lambda: ["mock", "gemini", "xai"])
    monkeypatch.setattr(PS, "make_agents", fake_make_agents)
    monkeypatch.setattr(PS, "run_convergence", fake_run_convergence)


def test_spark_seeds_field_from_config(monkeypatch):
    """A config seed enters SPARK's opening round as its prior options, and is
    recorded in the proposer meta with source=config."""
    seen: list = []
    _spy_agents(monkeypatch, seen)
    env = Envelope(request="decide")
    get_stage("spark")().run(env, StageContext(config={"seed": ["Use Postgres"]}))
    assert seen == [["Use Postgres"]]  # opening round seeded, not empty/None
    assert env.meta["proposer"]["seed"] == {"seeds": ["Use Postgres"], "source": "config"}
    assert env.trace[-1].data["seeded"] == 1


def test_spark_seeds_field_from_frame_anchor_when_no_config_seed(monkeypatch):
    """Absent an explicit config seed, SPARK falls back to the in-envelope Frame
    anchor — so seeding 'just works' if Frame ran in the same pipeline."""
    seen: list = []
    _spy_agents(monkeypatch, seen)
    env = Envelope(request="decide")
    env.meta["frame"] = {"input_type": "hybrid", "anchor": "Lean: Postgres"}
    get_stage("spark")().run(env, StageContext())
    assert seen == [["Lean: Postgres"]]
    assert env.meta["proposer"]["seed"] == {"seeds": ["Lean: Postgres"], "source": "frame"}


def test_config_seed_wins_over_frame_anchor(monkeypatch):
    seen: list = []
    _spy_agents(monkeypatch, seen)
    env = Envelope(request="decide")
    env.meta["frame"] = {"anchor": "from frame"}
    get_stage("spark")().run(env, StageContext(config={"seed": ["from config"]}))
    assert seen == [["from config"]]
    assert env.meta["proposer"]["seed"]["source"] == "config"


def test_non_expand_phases_ignore_seed(monkeypatch):
    """Only SPARK (the expand phase) seeds the field; Pattern Lock and the Cut
    operate on the already-seeded options, so a seed must not reach them."""
    seen: list = []
    _spy_agents(monkeypatch, seen)
    env = Envelope(request="decide", options=["A", "B"])
    get_stage("pattern_lock")().run(env, StageContext(config={"seed": ["Z"]}))
    assert seen == [None]  # opening round NOT overridden
    assert "seed" not in env.meta.get("proposer", {})


def test_blank_seed_is_ignored(monkeypatch):
    seen: list = []
    _spy_agents(monkeypatch, seen)
    env = Envelope(request="decide")
    get_stage("spark")().run(env, StageContext(config={"seed": ["   ", ""]}))
    assert seen == [None]  # nothing to seed → normal empty opening round
    assert "seed" not in env.meta.get("proposer", {})


def test_generator_carries_forward_on_empty_or_unparseable_payload():
    """Regression: a parse failure / empty `{}` payload must not wipe out a good
    prior commitment (the source of the phantom 'GENERATOR COMMITTED TO: {}')."""
    import types
    from rapier.stages.proposer.phases import PHASES, make_agents

    class FakeClient:
        def __init__(self, text):
            self._t = text

        def complete(self, system, user):
            return types.SimpleNamespace(text=self._t)

    cfg = PHASES["cut"]
    gen, _chal = make_agents(FakeClient("(garbage, not json)"), FakeClient("{}"), cfg, "req", ["A", "B"])
    prev = {"committed": "Option A", "rationale": "r"}
    assert gen(prev, None)["payload"] == prev          # carried forward, not {}
    assert gen(None, None)["payload"] in ({}, None)     # nothing to carry -> honest empty
