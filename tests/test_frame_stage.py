"""Frame stage — the front-door classifier + the Presentation (Earnedness Rubric).

Two layers, no network or keys:
* ``_derive`` — the pure, deterministic routing logic (the load-bearing part:
  routing lives in code, not in the model's free-form output).
* ``FrameStage`` — the model-call wrapper, with a fake client + the fail-safe
  paths (no client / unparseable output).
"""
from __future__ import annotations

import json

from rapier.envelope import Envelope
from rapier.manifest import Manifest
from rapier.presets import load_preset
from rapier.stage import StageContext, get_stage
from rapier.stages.frame import INPUT_TYPES, _derive


class _Resp:
    def __init__(self, text: str):
        self.text = text


class _FakeClient:
    """Minimal client — Frame only reads ``.complete(...).text``."""

    def __init__(self, payload: dict):
        self._payload = payload

    def complete(self, system: str, prompt: str) -> _Resp:
        return _Resp(json.dumps(self._payload))


def _framer(payload: dict) -> dict:
    return {"framer": _FakeClient(payload)}


# --- _derive: the deterministic routing matrix -------------------------------

def test_derive_question_routes_to_propose():
    f = _derive("question", {}, "ignored")
    assert (f["route"], f["readiness"], f["earned_gate_failed"]) == ("propose", "n/a", "none")
    assert f["anchor"] is None  # a question carries no anchor


def test_derive_hybrid_routes_to_propose_keeping_the_anchor():
    f = _derive("hybrid", {}, "use Postgres")
    assert f["route"] == "propose"
    assert f["readiness"] == "n/a"
    assert f["anchor"] == "use Postgres"  # the leaning is seeded into the field


def test_derive_earned_proposition_routes_to_resolve_and_drops_anchor():
    f = _derive("proposition", {"G1": True, "G2": True, "G3": True}, "use Postgres")
    assert f["route"] == "resolve"
    assert f["readiness"] == "pass"
    assert f["earned_gate_failed"] == "none"
    assert f["anchor"] is None  # cleared for the piste — nothing to seed


def test_derive_g2_failure_demotes_to_propose_keeping_the_anchor():
    # A commitment with no load-bearing why is a leaning in disguise.
    f = _derive("proposition", {"G1": True, "G2": False, "G3": True}, "use Postgres")
    assert f["route"] == "propose"
    assert f["readiness"] == "fail"
    assert f["earned_gate_failed"] == "G2"
    assert f["anchor"] == "use Postgres"  # seeded back into the armory


def test_derive_g1_failure_is_a_choice_question_no_anchor():
    # A menu ("A or B?") is not a single commitment — drop the anchor.
    f = _derive("proposition", {"G1": False, "G2": True, "G3": True}, "A or B")
    assert f["route"] == "propose"
    assert f["earned_gate_failed"] == "G1"
    assert f["anchor"] is None


def test_derive_g3_failure_needs_sharpening_no_anchor():
    f = _derive("proposition", {"G1": True, "G2": True, "G3": False}, "improve the db")
    assert f["route"] == "propose"
    assert f["earned_gate_failed"] == "G3"
    assert f["anchor"] is None


def test_derive_gate_order_reports_first_failure():
    # G1 fails first even though G2 also fails — first tripped gate wins.
    f = _derive("proposition", {"G1": False, "G2": False, "G3": True}, "x")
    assert f["earned_gate_failed"] == "G1"


def test_derive_soft_signal_surfaced_not_gating():
    f = _derive("proposition", {"G1": True, "G2": True, "G3": True, "S1": False}, "x")
    assert f["route"] == "resolve"  # S1 false does not block an earned proposition
    assert f["alternative_awareness"] is False


def test_routing_invariant_only_earned_proposition_resolves():
    # The safety property: nothing but an earned proposition may route to resolve.
    from itertools import product

    for itype in INPUT_TYPES:
        for g1, g2, g3 in product((True, False), repeat=3):
            f = _derive(itype, {"G1": g1, "G2": g2, "G3": g3}, "anchor")
            if f["route"] == "resolve":
                assert itype == "proposition"
                assert (g1, g2, g3) == (True, True, True)
                assert f["readiness"] == "pass"


def test_derive_blank_anchor_normalizes_to_none():
    f = _derive("hybrid", {}, "   ")
    assert f["anchor"] is None


# --- FrameStage: the model-call wrapper --------------------------------------

def test_stage_earned_proposition_via_fake_client():
    env = Envelope(request="We should use Postgres because we need transactional integrity.")
    payload = {"input_type": "proposition", "gates": {"G1": True, "G2": True, "G3": True},
               "anchor": "use Postgres", "basis": "single decision with a load-bearing reason", "confidence": 0.9}
    get_stage("frame")().run(env, StageContext(clients=_framer(payload)))
    fr = env.meta["frame"]
    assert fr["input_type"] == "proposition"
    assert fr["route"] == "resolve"
    assert fr["readiness"] == "pass"
    assert fr["confidence"] == 0.9
    assert env.trace[-1].summary.startswith("proposition → resolve")


def test_stage_question_via_fake_client():
    env = Envelope(request="What database should we use?")
    payload = {"input_type": "question", "anchor": None, "basis": "open interrogative", "confidence": 0.8}
    get_stage("frame")().run(env, StageContext(clients=_framer(payload)))
    fr = env.meta["frame"]
    assert fr["input_type"] == "question"
    assert fr["route"] == "propose"
    assert fr["readiness"] == "n/a"


def test_stage_no_client_fails_safe_to_question():
    env = Envelope(request="We should use Postgres.")
    get_stage("frame")().run(env, StageContext())  # no framer client
    fr = env.meta["frame"]
    assert fr["route"] == "propose"  # never silently resolves
    assert fr["input_type"] == "question"
    assert fr["classification_error"] == "no_framer_client"


def test_stage_unparseable_output_fails_safe():
    class _Junk:
        def complete(self, system, prompt):
            return _Resp("I cannot classify this, sorry.")

    env = Envelope(request="We should use Postgres.")
    get_stage("frame")().run(env, StageContext(clients={"framer": _Junk()}))
    fr = env.meta["frame"]
    assert fr["route"] == "propose"
    assert fr["classification_error"] == "unparseable"


def test_stage_bad_confidence_becomes_none():
    env = Envelope(request="Should we?")
    payload = {"input_type": "question", "confidence": "high"}  # non-numeric
    get_stage("frame")().run(env, StageContext(clients=_framer(payload)))
    assert env.meta["frame"]["confidence"] is None


# --- preset + registration ---------------------------------------------------

def test_frame_preset_builds_and_stage_registered():
    m = load_preset("frame")
    assert [s.stage for s in m.stages] == ["frame"]
    m.build()  # must not raise — the stage is registered
    assert "framer" in m.stages[0].roles  # the role binding is present


def test_frame_is_a_known_preset():
    from rapier.presets import PRESETS

    assert "frame" in PRESETS
