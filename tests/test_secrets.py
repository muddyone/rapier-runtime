"""Secrets: env-only reads + redaction (M0 security exit criterion)."""
from __future__ import annotations

import pytest

from rapier import secrets as S


def test_redacts_registered_value():
    S.register_secret_value("supersecretvalue123")
    out = S.redact("Authorization: Bearer supersecretvalue123 trailing")
    assert "supersecretvalue123" not in out
    assert "***REDACTED***" in out


def test_redacts_key_patterns_even_if_unregistered():
    assert "sk-" not in S.redact("openai key: sk-abcdefghijklmnop1234567890XYZ")
    assert "sk-ant-" not in S.redact("anthropic: sk-ant-abcdefghijklmnop1234567890")


def test_redact_obj_walks_nested_structures():
    S.register_secret_value("nestedsecret42")
    blob = {"a": ["x", "tok nestedsecret42"], "b": {"c": "nestedsecret42"}}
    red = S.redact_obj(blob)
    assert "nestedsecret42" not in repr(red)


def test_get_secret_is_env_only(monkeypatch):
    monkeypatch.delenv("RAPIER_TEST_KEY", raising=False)
    assert S.get_secret("RAPIER_TEST_KEY") is None
    monkeypatch.setenv("RAPIER_TEST_KEY", "envvalue12345")
    assert S.get_secret("RAPIER_TEST_KEY") == "envvalue12345"
    # reading it registers it for redaction
    assert "***REDACTED***" in S.redact("here is envvalue12345")


def test_require_secret_raises_when_unset(monkeypatch):
    monkeypatch.delenv("RAPIER_DEFINITELY_UNSET", raising=False)
    with pytest.raises(RuntimeError):
        S.require_secret("RAPIER_DEFINITELY_UNSET")
