"""A hard dollar cap on a run's model calls.

Set with :func:`set_budget` (the CLI's ``--max-usd``, the MCP tools'
``max_usd``, or ``RAPIER_MAX_USD``; the lowest one given wins). Every model call
goes through ``models._post_with_retry``, which asks :func:`reserve` before the
request and calls :func:`settle` with the response's usage after it.

The cap is enforced BEFORE each call, against that call's worst case: its input
(estimated from the payload, about 4 characters a token) plus its full
``max_tokens`` of output, at the model's price. A call that could cross the cap
is refused with :class:`BudgetExceeded`, so a run may stop a little short of the
cap but can never pass it. A stage that hits the cap fails soft, and its check
reads as did-not-run, never as passed.

With a cap set, a model whose price is not in :data:`PRICES` is refused outright
(fail closed): an unpriced call cannot be held to a dollar cap.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

# $ per million tokens (input, output), standard tier, no cache discount.
# Checked 2026-11-14: Anthropic from Anthropic's model table; GPT-5.5 from
# OpenAI's API pricing. Add a model here (or via RAPIER_PRICES) before capping
# a run that uses it.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "gpt-5.5": (5.00, 30.00),
}

CHARS_PER_TOKEN = 4


class BudgetExceeded(RuntimeError):
    """A call was refused because it could take the run past its dollar cap."""


_lock = threading.Lock()
_state: dict[str, Any] | None = None


def _prices() -> dict[str, tuple[float, float]]:
    extra = os.environ.get("RAPIER_PRICES")
    table = dict(PRICES)
    if extra:
        try:
            for k, v in json.loads(extra).items():
                table[str(k)] = (float(v[0]), float(v[1]))
        except (ValueError, TypeError, IndexError, AttributeError):
            pass  # a bad override adds nothing; unpriced models are still refused
    return table


def effective_cap(requested: float | None = None) -> float | None:
    caps = [c for c in (requested, _env_cap()) if c is not None]
    return min(caps) if caps else None


def _env_cap() -> float | None:
    raw = os.environ.get("RAPIER_MAX_USD")
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return 0.0  # an unreadable cap fails closed: nothing may be spent


def set_budget(cap_usd: float | None) -> None:
    """Start a fresh budget for one run (None clears it)."""
    global _state
    with _lock:
        _state = None if cap_usd is None else {
            "cap_usd": float(cap_usd), "spent_usd": 0.0, "reserved_usd": 0.0, "calls": 0,
            "refused": 0, "stopped": False, "by_model": {}}


def snapshot() -> dict | None:
    with _lock:
        return None if _state is None else json.loads(json.dumps(_state))


def _model_of(payload: dict) -> str:
    return str((payload or {}).get("model") or "")


def worst_case_usd(payload: dict) -> float:
    model = _model_of(payload)
    price = _prices().get(model)
    if price is None:
        raise BudgetExceeded(f"no price for model {model!r}; a capped run can't use it "
                             "(add it to rapier.budget.PRICES or RAPIER_PRICES)")
    chars = len(json.dumps(payload, default=str))
    out_max = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 8000)
    return (chars / CHARS_PER_TOKEN) * price[0] / 1e6 + out_max * price[1] / 1e6


def reserve(payload: dict) -> float:
    """Reserve a call's worst case, or refuse it. Returns the reservation."""
    with _lock:
        if _state is None:
            return 0.0
        try:
            need = worst_case_usd(payload)
        except BudgetExceeded:
            _state["refused"] += 1
            _state["stopped"] = True
            raise
        if _state["spent_usd"] + _state["reserved_usd"] + need > _state["cap_usd"]:
            _state["refused"] += 1
            _state["stopped"] = True
            raise BudgetExceeded(
                f"dollar cap ${_state['cap_usd']:.2f} reached: ${_state['spent_usd']:.2f} spent, "
                f"this call could cost up to ${need:.2f}")
        _state["reserved_usd"] += need
        return need


def settle(payload: dict, data: dict | None, reserved: float) -> None:
    """Replace a call's reservation with what it actually cost."""
    with _lock:
        if _state is None:
            return
        _state["reserved_usd"] = max(0.0, _state["reserved_usd"] - reserved)
        model = _model_of(payload)
        price = _prices().get(model, (0.0, 0.0))
        u = (data or {}).get("usage") or {}
        inp = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
        out = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
        if not u:  # no usage reported: charge the reservation, never less
            cost = reserved
        else:
            cost = inp * price[0] / 1e6 + out * price[1] / 1e6
        _state["spent_usd"] = round(_state["spent_usd"] + cost, 6)
        _state["calls"] += 1
        m = _state["by_model"].setdefault(model, {"calls": 0, "input_tokens": 0,
                                                  "output_tokens": 0, "usd": 0.0})
        m["calls"] += 1
        m["input_tokens"] += inp
        m["output_tokens"] += out
        m["usd"] = round(m["usd"] + cost, 6)


def release(reserved: float) -> None:
    """Drop a reservation for a call that failed without a response."""
    with _lock:
        if _state is not None:
            _state["reserved_usd"] = max(0.0, _state["reserved_usd"] - reserved)
