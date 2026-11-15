"""The model layer — the ONLY place a vendor or model name lives.

A ``ModelSpec`` (vendor + model + prompt) comes straight from the manifest.
``build_client`` turns it into a ``ModelClient``. Cross-vendor independence is
therefore a config property, not a code change: point two roles at two vendors.

Real provider SDKs are imported lazily and only when a real call is made, so
the package imports and the M0 echo pipeline run with no SDKs and no keys.
Secrets are read exclusively through :mod:`rapier.secrets` (env-only, redacted).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .secrets import register_secret_value, require_secret


@dataclass
class ModelSpec:
    vendor: str  # mock | anthropic | openai
    model: str
    prompt_template: str | None = None
    max_tokens: int = 4096  # generative floor; 1024 truncates real recommendations
    temperature: float = 1.0


@dataclass
class ModelResponse:
    text: str
    vendor: str
    model: str
    raw: dict[str, Any] | None = None
    # Why the provider stopped: "end_turn"/"stop" (finished) or "max_tokens"/
    # "length" (cut off at the output limit). None when the provider gave none.
    stop_reason: str | None = None

    @property
    def cut_off(self) -> bool:
        return self.stop_reason in CUT_OFF_REASONS


CUT_OFF_REASONS = frozenset({"max_tokens", "length", "model_context_window_exceeded"})


# --- one HTTP path + one transcript hook for EVERY vendor -------------------
# Every model call — native Anthropic/OpenAI, Gemini/Grok, any compatible
# endpoint — goes through _post_with_retry and _record(), so retry/backoff and
# transcript capture are identical regardless of the LLM pairing.

_transcript_sink = None  # set per run; called with one dict per model call


def set_transcript_sink(fn) -> None:
    global _transcript_sink
    _transcript_sink = fn


def _record(vendor: str, model: str, system: str, prompt: str, response: str,
            stop_reason: str | None = None) -> None:
    if _transcript_sink is not None:
        try:
            _transcript_sink(
                {"vendor": vendor, "model": model, "system": system, "prompt": prompt, "response": response,
                 "stop_reason": stop_reason}
            )
        except Exception:  # transcript capture must never break a run
            pass


def _post_with_retry(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    """POST JSON with exponential backoff on 429/5xx, held to the run's dollar cap.

    The budget (``rapier.budget``) is asked before EVERY request this sends,
    retries included, for that request's worst case, and refuses it if it could
    pass the cap. After each request:

    - a usable response is charged what its usage says;
    - a 429 or 5xx response, or an error raised before anything was sent (no
      connection made, a malformed URL), releases the reservation: not billed;
    - anything else (a read timeout, a dropped connection after sending, a 4xx,
      an unreadable body) is charged the full reservation, since it may still
      have been billed.
    """
    import time

    import requests

    from . import budget

    delay, last = 2.0, None
    for _ in range(6):
        reserved = budget.reserve(payload)
        try:
            resp = _send_once(url, headers, payload, timeout)
        except requests.RequestException as exc:
            if _never_sent(exc):
                budget.release(reserved)
            else:
                budget.settle(payload, None, reserved)
            last = str(exc)
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        except BaseException:
            budget.settle(payload, None, reserved)
            raise
        if resp.status_code == 429 or resp.status_code >= 500:
            budget.release(reserved)
            last = f"HTTP {resp.status_code}"
            time.sleep(_retry_wait(resp.headers.get("Retry-After"), delay))
            delay = min(delay * 2, 30)
            continue
        try:
            resp.raise_for_status()
            data = resp.json()
        except BaseException:
            budget.settle(payload, None, reserved)
            raise
        budget.settle(payload, data if isinstance(data, dict) else None, reserved)
        return data
    raise RuntimeError(f"request to {url} failed after retries: {last}")


def _retry_wait(retry_after: Any, default: float) -> float:
    """Seconds to wait before a retry: Retry-After when it is a finite number
    from 0 to 30, else the backoff delay."""
    try:
        v = float(retry_after)
    except (TypeError, ValueError):
        return default
    import math

    return v if math.isfinite(v) and 0 <= v <= 30 else min(default, 30)


def _send_once(url: str, headers: dict, payload: dict, timeout: int):
    """One HTTP request (no retry, no budget). Tests replace this."""
    import requests

    return requests.post(url, headers=headers, json=payload, timeout=timeout)


def _never_sent(exc: BaseException) -> bool:
    """True only when the request certainly never reached the provider."""
    import requests

    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.InvalidURL,
                        requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema,
                        requests.exceptions.InvalidHeader)):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError) and not isinstance(
            exc, requests.exceptions.Timeout):
        try:
            from urllib3.exceptions import NewConnectionError
        except ImportError:  # pragma: no cover
            return False
        inner = exc.args[0] if exc.args else None
        reason = getattr(inner, "reason", inner)
        return isinstance(reason, NewConnectionError)
    return False


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
        stop = data.get("stop_reason")
        _record("anthropic", self.spec.model, system, prompt, text, stop)
        return ModelResponse(text=text, vendor="anthropic", model=self.spec.model, stop_reason=stop)


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
        choice = (data.get("choices") or [{}])[0]
        text = choice.get("message", {}).get("content") or ""
        stop = choice.get("finish_reason")
        _record("openai", self.spec.model, system, prompt, text, stop)
        return ModelResponse(text=text, vendor="openai", model=self.spec.model, stop_reason=stop)


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
        choice = (data.get("choices") or [{}])[0]
        text = choice.get("message", {}).get("content") or ""
        stop = choice.get("finish_reason")
        _record(self.spec.vendor, self.spec.model, system, prompt, text, stop)
        return ModelResponse(text=text, vendor=self.spec.vendor, model=self.spec.model, stop_reason=stop)


# vendor -> (base_url, key_env, default_model). Defaults are overridable in the
# manifest; only xai's default is live-validated (2026-07-06 — Grok's API is
# OpenAI-compatible). Others are best-effort until validated with a real key.
# Checked against each vendor's deprecation page 2026-10-15 (see CHANGELOG.md).
_OPENAI_COMPATIBLE: dict[str, tuple[str, str, str]] = {
    # gemini-2.5-flash is closed to new API users; Google points new projects here, same price.
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-3.5-flash-lite"),  # also has a native API
    "xai":        ("https://api.x.ai/v1",            "XAI_API_KEY",        "grok-4.3"),
    # deepseek-chat was retired 2026-07-24; deepseek-flash is DeepSeek's current name.
    "deepseek":   ("https://api.deepseek.com/v1",    "DEEPSEEK_API_KEY",   "deepseek-flash"),
    "mistral":    ("https://api.mistral.ai/v1",      "MISTRAL_API_KEY",    "mistral-large-latest"),  # alias, follows Mistral's current Large
    # llama-3.3-70b-versatile was shut down 2026-08-16; this is Groq's named replacement.
    "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY",       "openai/gpt-oss-120b"),
    # Llama-3.3-70B-Instruct-Turbo leaves serverless 2026-10-22; the cheapest current open model there.
    "together":   ("https://api.together.xyz/v1",    "TOGETHER_API_KEY",   "openai/gpt-oss-120b"),
    # gpt-4o stops working 2027-01-14. gpt-6-sol is OpenAI's current flagship, the same class
    # gpt-4o was and slightly cheaper, so no caller gets a weaker critic or a bigger bill.
    "openrouter": ("https://openrouter.ai/api/v1",   "OPENROUTER_API_KEY", "openai/gpt-6-sol"),
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
        base_url, key_env, _fallback = _OPENAI_COMPATIBLE[vendor]
        if not spec.model:
            # via default_model() so RAPIER_MODEL_<VENDOR> applies here too
            spec.model = default_model(vendor) or _fallback
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
    "openai": "gpt-5.5",
    **{v: cfg[2] for v, cfg in _OPENAI_COMPATIBLE.items()},
}

# preference order when auto-assigning frontier vendors to roles.
_FRONTIER_ORDER = ["anthropic", "openai", "gemini", "xai"]


def default_model(vendor: str) -> str:
    """The default model for a vendor, env-overridable machine-wide.

    ``RAPIER_MODEL_<VENDOR>`` (e.g. ``RAPIER_MODEL_OPENAI=gpt-5.5``) overrides
    the built-in default for every project on the box, so retuning a vendor's
    model does not require a code edit or a release. A model named explicitly
    in a manifest still wins — this only supplies the default.
    """
    override = os.environ.get(f"RAPIER_MODEL_{vendor.upper()}")
    if override:
        return override.strip()
    return _DEFAULT_MODEL.get(vendor, "")


def vendor_key_envs() -> dict[str, str]:
    """Vendor -> the env var holding its key (public, for onboarding / doctor)."""
    return dict(_VENDOR_KEY_ENV)


def frontier_vendors() -> list[str]:
    """The frontier vendors, in preference order (for onboarding hints)."""
    return list(_FRONTIER_ORDER)


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
