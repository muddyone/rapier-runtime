"""A hard dollar cap on a run's model calls.

Set with :func:`set_budget` (the CLI's ``--max-usd``, the MCP tools'
``max_usd``, or ``RAPIER_MAX_USD``; the lowest one given wins). Every model call
goes through ``models._post_with_retry``, which asks :func:`reserve` before EACH
request it sends (retries included) and settles or releases that reservation
after it.

The cap is enforced BEFORE each request, against that request's worst case: its
input (the payload's UTF-8 byte length, taken as a token count, since no token is
shorter than one byte, plus a fixed allowance per message) plus its full
``max_tokens`` of output, at the model's price. A request that could cross the
cap is refused with :class:`BudgetExceeded`, so a run may stop a little short of
the cap but can never pass it. A stage that hits the cap fails soft, and its
check reads as did-not-run, never as passed.

The budget belongs to ONE run. It is held in a context variable, so two runs in
the same process (concurrent MCP tool calls in worker threads, or a run that was
abandoned on timeout and is still finishing) each see only their own budget, and
one run ending never clears another's.

A cap must be a finite number, 0 or more. A caller's cap that isn't is refused
before the run (:class:`InvalidCap`); an operator ceiling (``RAPIER_MAX_USD``)
that isn't fails closed to 0, so nothing may be spent.

With a cap set, a model whose price is not in :data:`PRICES` is refused outright
(fail closed): an unpriced call cannot be held to a dollar cap.
"""
from __future__ import annotations

import contextvars
import json
import math
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

# Allowance per message for the chat template's own tokens (role markers etc.).
PER_MESSAGE_OVERHEAD_TOKENS = 64
DEFAULT_OUTPUT_TOKENS = 8000  # a payload that names no output limit


class BudgetExceeded(RuntimeError):
    """A call was refused because it could take the run past its dollar cap."""


class InvalidCap(ValueError):
    """A caller's cap isn't a finite number of dollars, 0 or more."""


class _Budget:
    def __init__(self, cap_usd: float):
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {
            "cap_usd": float(cap_usd), "spent_usd": 0.0, "reserved_usd": 0.0, "calls": 0,
            "released": 0, "refused": 0, "stopped": False, "by_model": {}}


_current: contextvars.ContextVar[_Budget | None] = contextvars.ContextVar(
    "rapier_budget", default=None)


def _prices() -> dict[str, tuple[float, float]]:
    extra = os.environ.get("RAPIER_PRICES")
    table = dict(PRICES)
    if extra:
        try:
            for k, v in json.loads(extra).items():
                inp, out = float(v[0]), float(v[1])
                if _valid(inp) and _valid(out):
                    table[str(k)] = (inp, out)
        except (ValueError, TypeError, IndexError, AttributeError):
            pass  # a bad override adds nothing; unpriced models are still refused
    return table


def _valid(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0


def check_cap(requested: Any) -> float | None:
    """A caller's cap as a float, or raise :class:`InvalidCap`. None = no cap."""
    if requested is None:
        return None
    try:
        v = float(requested)
    except (TypeError, ValueError):
        raise InvalidCap(f"max_usd must be a number of dollars, got {requested!r}") from None
    if isinstance(requested, bool) or not _valid(v):
        raise InvalidCap(f"max_usd must be a finite number of dollars, 0 or more; got {requested!r}")
    return v


def effective_cap(requested: Any = None) -> float | None:
    """The run's cap: the lower of the caller's and the operator's. Raises
    :class:`InvalidCap` for a caller cap that isn't finite and non-negative."""
    caps = [c for c in (check_cap(requested), _env_cap()) if c is not None]
    return min(caps) if caps else None


def _env_cap() -> float | None:
    raw = os.environ.get("RAPIER_MAX_USD")
    if raw in (None, ""):
        return None
    try:
        v = float(raw)
    except ValueError:
        return 0.0  # an unreadable cap fails closed: nothing may be spent
    return v if _valid(v) else 0.0  # nan, inf, negative: fail closed too


def set_budget(cap_usd: float | None) -> contextvars.Token:
    """Start a fresh budget for one run in the current context (None clears it).

    Returns a token for :func:`reset_budget`, which restores what was there before."""
    if cap_usd is not None and not _valid(cap_usd):
        cap_usd = 0.0  # never install a cap the guard can't compare against
    return _current.set(None if cap_usd is None else _Budget(cap_usd))


def reset_budget(token: contextvars.Token) -> None:
    _current.reset(token)


def active() -> bool:
    return _current.get() is not None


def snapshot() -> dict | None:
    b = _current.get()
    if b is None:
        return None
    with b.lock:
        return json.loads(json.dumps(b.state))


def _model_of(payload: dict) -> str:
    return str((payload or {}).get("model") or "")


def _count_messages(payload: dict) -> int:
    n = len((payload or {}).get("messages") or [])
    return n + (1 if (payload or {}).get("system") else 0)


def worst_case_usd(payload: dict) -> float:
    model = _model_of(payload)
    price = _prices().get(model)
    if price is None:
        raise BudgetExceeded(f"no price for model {model!r}; a capped run can't use it "
                             "(add it to rapier.budget.PRICES or RAPIER_PRICES)")
    in_tokens = (len(json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8"))
                 + PER_MESSAGE_OVERHEAD_TOKENS * max(1, _count_messages(payload)))
    out_max = int(payload.get("max_tokens") or payload.get("max_completion_tokens")
                  or DEFAULT_OUTPUT_TOKENS)
    return in_tokens * price[0] / 1e6 + out_max * price[1] / 1e6


def reserve(payload: dict) -> float:
    """Reserve one request's worst case, or refuse it. Returns the reservation."""
    b = _current.get()
    if b is None:
        return 0.0
    with b.lock:
        st = b.state
        try:
            need = worst_case_usd(payload)
        except BudgetExceeded:
            st["refused"] += 1
            st["stopped"] = True
            raise
        # Written so that anything that isn't a plain comparison (a NaN anywhere)
        # refuses the call rather than letting it through.
        if not (st["spent_usd"] + st["reserved_usd"] + need <= st["cap_usd"]):
            st["refused"] += 1
            st["stopped"] = True
            raise BudgetExceeded(
                f"dollar cap ${st['cap_usd']:.2f} reached: ${st['spent_usd']:.2f} spent, "
                f"this call could cost up to ${need:.2f}")
        st["reserved_usd"] += need
        return need


def settle(payload: dict, data: dict | None, reserved: float) -> None:
    """Replace one request's reservation with what it cost. With no usage
    reported (no response, or a response without usage), charge the whole
    reservation: it may have been billed, so never assume it was free."""
    b = _current.get()
    if b is None:
        return
    with b.lock:
        st = b.state
        st["reserved_usd"] = max(0.0, st["reserved_usd"] - reserved)
        model = _model_of(payload)
        price = _prices().get(model, (0.0, 0.0))
        u = (data or {}).get("usage") or {}
        inp = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
        out = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
        cost = reserved if not u else inp * price[0] / 1e6 + out * price[1] / 1e6
        st["spent_usd"] += cost
        st["calls"] += 1
        m = st["by_model"].setdefault(model, {"calls": 0, "input_tokens": 0,
                                              "output_tokens": 0, "usd": 0.0})
        m["calls"] += 1
        m["input_tokens"] += inp
        m["output_tokens"] += out
        m["usd"] += cost


def release(reserved: float) -> None:
    """Drop one request's reservation when it was certainly not billed: it was
    never sent, or the provider answered 429/5xx."""
    b = _current.get()
    if b is None:
        return
    with b.lock:
        b.state["reserved_usd"] = max(0.0, b.state["reserved_usd"] - reserved)
        b.state["released"] += 1
