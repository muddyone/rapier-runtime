"""Envelope: trace append + serialization."""
from __future__ import annotations

from rapier.envelope import Artifact, Envelope


def test_trace_append_and_serialize():
    env = Envelope(request="q")
    env.add_trace("s1", "transform", "did a thing", n=1)
    d = env.to_dict()
    assert d["request"] == "q"
    assert d["trace"][0]["stage"] == "s1"
    assert d["trace"][0]["data"]["n"] == 1


def test_artifact_defaults():
    a = Artifact(ref="CWE-79", claim="XSS in template")
    assert a.load_bearing is False
    assert a.verdict == "unchecked"


def test_add_trace_returns_self_for_chaining():
    env = Envelope(request="q")
    assert env.add_trace("a", "transform", "x").add_trace("b", "transform", "y") is env
    assert len(env.trace) == 2
