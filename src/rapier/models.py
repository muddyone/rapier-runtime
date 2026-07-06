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
from dataclasses import dataclass, field
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


# --- one HTTP path + one transcript hook for EVERY vendor -------------------
# Every model call — native Anthropic/OpenAI, Gemini/Grok, any compatible
# endpoint — goes through _post_with_retry and _record(), so retry/backoff and
# transcript capture are identical regardless of the LLM pairing.

_transcript_sink = None  # set per run; called with one dict per model call


def set_transcript_sink(fn) -> None:
    global _transcript_sink
    _transcript_sink = fn


def _record(vendor: str, model: str, system: str, prompt: str, response: str) -> None:
    if _transcript_sink is not None:
        try:
            _transcript_sink(
                {"vendor": vendor, "model": model, "system": system, "prompt": prompt, "response": response}
            )
        except Exception:  # transcript capture must never break a run
            pass


def _post_with_retry(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    """POST JSON with exponential backoff on 429/5xx (respects Retry-After)."""
    import time

    import requests

    delay, last = 2.0, None
    for _ in range(6):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() else delay
            last = f"HTTP {resp.status_code}"
            time.sleep(min(wait, 30))
            delay = min(delay * 2, 30)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"request to {url} failed after retries: {last}")


class ModelClient(ABC):
    def __init__(self, spec: ModelSpec):
        self.spec = spec

    @abstractmethod
    def complete(self, system: str, prompt: str) -> ModelResponse:
        ...


class MockModelClient(ModelClient):
    """Deterministic, no network, no key. Used by the echo pipeline and tests."""

    def complete(self, system: str, prompt: str) -> ModelResponse:
        text = f"[mock:{self.spec.model}] {prompt.strip()}"
        _record("mock", self.spec.model, system, prompt, text)
        return ModelResponse(text=text, vendor="mock", model=self.spec.model)


class AnthropicModelClient(ModelClient):
    def complete(self, system: str, prompt: str) -> ModelResponse:
        key = require_secret("ANTHROPIC_API_KEY")
        register_secret_value(key)
        data = _post_with_retry(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {
                "model": self.spec.model,
                "max_tokens": self.spec.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        _record("anthropic", self.spec.model, system, prompt, text)
        return ModelResponse(text=text, vendor="anthropic", model=self.spec.model)


class OpenAIModelClient(ModelClient):
    def complete(self, system: str, prompt: str) -> ModelResponse:
        key = require_secret("OPENAI_API_KEY")
        register_secret_value(key)
        data = _post_with_retry(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {
                "model": self.spec.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_completion_tokens": self.spec.max_tokens,
            },
        )
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        _record("openai", self.spec.model, system, prompt, text)
        return ModelResponse(text=text, vendor="openai", model=self.spec.model)


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
        key = require_secret(self.key_env)
        register_secret_value(key)
        data = _post_with_retry(
            f"{self.base_url}/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {
                "model": self.spec.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self.spec.max_tokens,
            },
        )
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        _record(self.spec.vendor, self.spec.model, system, prompt, text)
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


# vendor -> hosting jurisdiction (for egress/data-residency policy; threat model S5).
_VENDOR_JURISDICTION: dict[str, str] = {
    "anthropic": "us", "openai": "us", "gemini": "us", "xai": "us",
    "groq": "us", "together": "us", "openrouter": "us",
    "mistral": "eu", "deepseek": "cn", "qwen": "cn", "ollama": "local",
}


class PolicyError(RuntimeError):
    """Raised when the policy cannot be satisfied (e.g. independence=required,
    but only one vendor is available)."""


@dataclass
class Policy:
    """Declarative vendor policy (V3). Governs role->vendor assignment.

    - ``vendors``: preference order (falls back to the frontier order).
    - ``independence``: ``required`` (error if no distinct 2nd vendor) |
      ``preferred`` (use a distinct 2nd if available, else single-vendor) |
      ``off`` (single-vendor; don't seek a 2nd).
    - ``avoid_jurisdictions``: drop vendors hosted in these (e.g. ``["cn"]``).
    """

    vendors: list[str] | None = None
    independence: str = "preferred"
    avoid_jurisdictions: list[str] = field(default_factory=list)

    def _available(self, available: list[str]) -> list[str]:
        avail = [v for v in available if v != "mock"]
        if self.avoid_jurisdictions:
            avail = [v for v in avail if _VENDOR_JURISDICTION.get(v) not in self.avoid_jurisdictions]
        return avail

    def resolve(
        self, available: list[str], primary_pref: str | None = None, secondary_pref: str | None = None
    ) -> tuple[str | None, str | None]:
        avail = self._available(available)
        if not avail:
            if self.independence == "required":
                raise PolicyError("independence=required but no vendors are available")
            return None, None
        order = self.vendors or _FRONTIER_ORDER

        def pick(pref: str | None, exclude: str | None = None) -> str | None:
            if pref and pref in avail and pref != exclude:
                return pref
            for v in order:
                if v in avail and v != exclude:
                    return v
            for v in avail:
                if v != exclude:
                    return v
            return None

        primary = pick(primary_pref)
        if self.independence == "off":
            return primary, None
        secondary = pick(secondary_pref, exclude=primary)
        if self.independence == "required" and secondary is None:
            raise PolicyError(
                f"independence=required but only one vendor available ({primary}); "
                "add a second vendor's key or set independence: preferred/off"
            )
        return primary, secondary
