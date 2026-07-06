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


_VENDORS: dict[str, type[ModelClient]] = {
    "mock": MockModelClient,
    "anthropic": AnthropicModelClient,
    "openai": OpenAIModelClient,
}


def build_client(spec: ModelSpec) -> ModelClient:
    if spec.vendor not in _VENDORS:
        raise ValueError(
            f"unknown vendor '{spec.vendor}'; known vendors: {sorted(_VENDORS)}"
        )
    return _VENDORS[spec.vendor](spec)
