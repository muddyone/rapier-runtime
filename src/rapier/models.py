"""The model layer — the ONLY place a vendor or model name lives.

A ``ModelSpec`` (vendor + model + prompt) comes straight from the manifest.
``build_client`` turns it into a ``ModelClient``. Cross-vendor independence is
therefore a config property, not a code change: point two roles at two vendors.

Real provider SDKs are imported lazily and only when a real call is made, so
the package imports and the M0 echo pipeline run with no SDKs and no keys.
Secrets are read exclusively through :mod:`rapier.secrets` (env-only, redacted).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .secrets import register_secret_value, require_secret


@dataclass
class ModelSpec:
    vendor: str  # mock | anthropic | openai
    model: str
    prompt_template: str | None = None
    max_tokens: int = 1024
    temperature: float = 1.0


@dataclass
class ModelResponse:
    text: str
    vendor: str
    model: str
    raw: dict[str, Any] | None = None


class ModelClient(ABC):
    def __init__(self, spec: ModelSpec):
        self.spec = spec

    @abstractmethod
    def complete(self, system: str, prompt: str) -> ModelResponse:
        ...


class MockModelClient(ModelClient):
    """Deterministic, no network, no key. Used by the echo pipeline and tests."""

    def complete(self, system: str, prompt: str) -> ModelResponse:
        return ModelResponse(
            text=f"[mock:{self.spec.model}] {prompt.strip()}",
            vendor="mock",
            model=self.spec.model,
        )


class AnthropicModelClient(ModelClient):
    def complete(self, system: str, prompt: str) -> ModelResponse:
        key = require_secret("ANTHROPIC_API_KEY")
        register_secret_value(key)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only with SDK
            raise RuntimeError(
                "anthropic SDK not installed; `pip install rapier-runtime[providers]`"
            ) from exc
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=self.spec.model,
            max_tokens=self.spec.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        )
        return ModelResponse(text=text, vendor="anthropic", model=self.spec.model)


class OpenAIModelClient(ModelClient):
    def complete(self, system: str, prompt: str) -> ModelResponse:
        key = require_secret("OPENAI_API_KEY")
        register_secret_value(key)
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - exercised only with SDK
            raise RuntimeError(
                "openai SDK not installed; `pip install rapier-runtime[providers]`"
            ) from exc
        client = openai.OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=self.spec.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.spec.max_tokens,
            temperature=self.spec.temperature,
        )
        return ModelResponse(
            text=resp.choices[0].message.content or "",
            vendor="openai",
            model=self.spec.model,
        )


class OpenAICompatibleModelClient(ModelClient):
    """Any OpenAI-wire-format endpoint — Grok (xAI), DeepSeek, Mistral, Groq,
    Together, OpenRouter, local Ollama/vLLM. One client, parameterized by
    ``base_url`` + the env var holding its key. Uses ``requests`` directly (no
    SDK), so adding a vendor is a config entry, not new code.
    """

    def __init__(self, spec: ModelSpec, base_url: str, key_env: str):
        super().__init__(spec)
        self.base_url = base_url.rstrip("/")
        self.key_env = key_env

    def complete(self, system: str, prompt: str) -> ModelResponse:
        import requests

        key = require_secret(self.key_env)
        register_secret_value(key)
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": self.spec.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self.spec.max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or [{}]
        text = (choices[0].get("message") or {}).get("content") or ""
        return ModelResponse(text=text, vendor=self.spec.vendor, model=self.spec.model)


# vendor -> (base_url, key_env, default_model). Defaults are overridable in the
# manifest; only xai's default is live-validated (2026-07-06 — Grok's API is
# OpenAI-compatible). Others are best-effort until validated with a real key.
_OPENAI_COMPATIBLE: dict[str, tuple[str, str, str]] = {
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-2.5-flash"),  # frontier; also has a native API
    "xai":        ("https://api.x.ai/v1",            "XAI_API_KEY",        "grok-4.3"),
    "deepseek":   ("https://api.deepseek.com/v1",    "DEEPSEEK_API_KEY",   "deepseek-chat"),
    "mistral":    ("https://api.mistral.ai/v1",      "MISTRAL_API_KEY",    "mistral-large-latest"),
    "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY",       "llama-3.3-70b-versatile"),
    "together":   ("https://api.together.xyz/v1",    "TOGETHER_API_KEY",   "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "openrouter": ("https://openrouter.ai/api/v1",   "OPENROUTER_API_KEY", "openai/gpt-4o"),
    "ollama":     ("http://localhost:11434/v1",      "OLLAMA_API_KEY",     "llama3.1"),  # local, zero-egress
}

# vendor -> env var holding its key (for auto-detect). Native + compatible.
_VENDOR_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    **{v: cfg[1] for v, cfg in _OPENAI_COMPATIBLE.items()},
}


def build_client(spec: ModelSpec) -> ModelClient:
    vendor = spec.vendor
    if vendor == "mock":
        return MockModelClient(spec)
    if vendor == "anthropic":
        return AnthropicModelClient(spec)
    if vendor == "openai":
        return OpenAIModelClient(spec)
    if vendor in _OPENAI_COMPATIBLE:
        base_url, key_env, default_model = _OPENAI_COMPATIBLE[vendor]
        if not spec.model:
            spec.model = default_model
        return OpenAICompatibleModelClient(spec, base_url, key_env)
    known = ["mock", "anthropic", "openai", *sorted(_OPENAI_COMPATIBLE)]
    raise ValueError(f"unknown vendor '{vendor}'; known vendors: {known}")


def available_vendors() -> list[str]:
    """Which vendors have a key present in the environment (auto-detect).

    ``mock`` is always available; ``ollama`` is local and only appears if its
    (optional) key env is set. Keys are read env-only (threat model S1).
    """
    from .secrets import get_secret

    avail = ["mock"]
    for vendor, key_env in _VENDOR_KEY_ENV.items():
        if get_secret(key_env):
            avail.append(vendor)
    return avail


# vendor -> a sensible default model (used when a role has no explicit model).
_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.2",
    **{v: cfg[2] for v, cfg in _OPENAI_COMPATIBLE.items()},
}

# preference order when auto-assigning frontier vendors to roles.
_FRONTIER_ORDER = ["anthropic", "openai", "gemini", "xai"]


def default_model(vendor: str) -> str:
    return _DEFAULT_MODEL.get(vendor, "")


def resolve_pair(
    available: list[str],
    primary_pref: str | None = None,
    secondary_pref: str | None = None,
) -> tuple[str | None, str | None]:
    """Choose ``(primary, secondary)`` vendors from the available set.

    ``primary`` backs the author/primary slot; ``secondary`` is a **distinct**
    vendor for the reviewer/cross-vendor slot, or ``None`` when only one vendor
    is available (honest single-vendor degradation). Preferences win when
    available; otherwise frontier vendors are assigned in order.
    """
    avail = [v for v in available if v != "mock"]
    if not avail:
        return None, None

    def pick(pref: str | None, exclude: str | None = None) -> str | None:
        if pref and pref in avail and pref != exclude:
            return pref
        for v in _FRONTIER_ORDER:
            if v in avail and v != exclude:
                return v
        for v in avail:
            if v != exclude:
                return v
        return None

    primary = pick(primary_pref)
    secondary = pick(secondary_pref, exclude=primary)
    return primary, secondary
