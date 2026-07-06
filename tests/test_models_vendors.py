"""Vendor layer: the generic OpenAI-compatible client, xai wiring, auto-detect."""
from __future__ import annotations

import pytest

from rapier.models import (
    ModelSpec,
    OpenAICompatibleModelClient,
    available_vendors,
    build_client,
)

_ALL_KEY_ENVS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_API_KEY",
)


def test_xai_builds_openai_compatible_client_with_defaults():
    client = build_client(ModelSpec(vendor="xai", model=""))
    assert isinstance(client, OpenAICompatibleModelClient)
    assert client.base_url == "https://api.x.ai/v1"
    assert client.key_env == "XAI_API_KEY"
    assert client.spec.model == "grok-4.3"  # default filled in


def test_explicit_model_overrides_default():
    client = build_client(ModelSpec(vendor="xai", model="grok-4.20-0309-reasoning"))
    assert client.spec.model == "grok-4.20-0309-reasoning"


def test_other_compatible_vendors_resolve():
    for vendor, base in [
        ("deepseek", "https://api.deepseek.com/v1"),
        ("mistral", "https://api.mistral.ai/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
    ]:
        client = build_client(ModelSpec(vendor=vendor, model=""))
        assert isinstance(client, OpenAICompatibleModelClient)
        assert client.base_url == base


def test_unknown_vendor_raises():
    with pytest.raises(ValueError):
        build_client(ModelSpec(vendor="nope", model="x"))


def test_available_vendors_reflects_env(monkeypatch):
    for env in _ALL_KEY_ENVS:
        monkeypatch.delenv(env, raising=False)
    assert available_vendors() == ["mock"]

    monkeypatch.setenv("XAI_API_KEY", "xai-testtesttesttest1234")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-testtesttesttest1234")
    avail = available_vendors()
    assert "mock" in avail and "xai" in avail and "openai" in avail
    assert "anthropic" not in avail
