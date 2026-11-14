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
CACHE_WRITE_MULT = 1.25  # Anthropic bills cache-creation input at 1.25x the input price
CACHE_READ_MULT = 0.10   # and cache reads at 0.1x


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
    """The price table. ``RAPIER_PRICES`` may add a model that has no built-in
    price, or raise a built-in price; it can never lower one (rr-1 S-2: a [0, 0]
    override let 50 calls through a $1 cap). A price must be finite and above 0."""
    extra = os.environ.get("RAPIER_PRICES")
    table = dict(PRICES)
    if extra:
        try:
            items = json.loads(extra).items()
        except (ValueError, TypeError, AttributeError):
            items = []
        for k, v in items:
            try:
                inp, out = float(v[0]), float(v[1])
            except (ValueError, TypeError, IndexError, KeyError):
                continue  # a bad entry adds nothing; an unpriced model is still refused
            if not (_valid(inp) and _valid(out) and inp > 0 and out > 0):
                continue
            base = PRICES.get(str(k))
            table[str(k)] = (inp, out) if base is None else (max(inp, base[0]), max(out, base[1]))
    return table


class Reservation(float):
    """A reservation's dollars, carrying the price it was made at, so settle
    charges at that price even if the table changes in between (rr-1 S-5)."""

    price: tuple[float, float] | None = None


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


def _price_for(payload: dict) -> tuple[float, float]:
    model = _model_of(payload)
    price = _prices().get(model)
    if price is None:
        raise BudgetExceeded(f"no price for model {model!r}; a capped run can't use it "
                             "(add it to rapier.budget.PRICES or RAPIER_PRICES)")
    return price


def _worst_case(payload: dict, price: tuple[float, float]) -> float:
    in_tokens = (len(json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8"))
                 + PER_MESSAGE_OVERHEAD_TOKENS * max(1, _count_messages(payload)))
    out_max = int(payload.get("max_tokens") or payload.get("max_completion_tokens")
                  or DEFAULT_OUTPUT_TOKENS)
    # A cache write bills input at 1.25x; reserve as if all of it were one.
    return in_tokens * price[0] * CACHE_WRITE_MULT / 1e6 + out_max * price[1] / 1e6


def worst_case_usd(payload: dict) -> float:
    return _worst_case(payload, _price_for(payload))


def reserve(payload: dict) -> float:
    """Reserve one request's worst case, or refuse it. Returns the reservation."""
    b = _current.get()
    if b is None:
        return 0.0
    with b.lock:
        st = b.state
        try:
            price = _price_for(payload)
            need = _worst_case(payload, price)
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
        r = Reservation(need)
        r.price = price
        return r


def _usage_cost(u: dict, price: tuple[float, float]) -> tuple[float, int, int] | None:
    """Cost from a usage block, or None when it doesn't name both input and
    output tokens (rr-1 S-3: ``{"total_tokens": 1100}`` was charged $0)."""
    in_key = next((k for k in ("input_tokens", "prompt_tokens") if k in u), None)
    out_key = next((k for k in ("output_tokens", "completion_tokens") if k in u), None)
    if in_key is None or out_key is None:
        return None
    try:
        inp, out = int(u[in_key]), int(u[out_key])
        # Anthropic's input_tokens excludes cached input; count both kinds.
        cw = int(u.get("cache_creation_input_tokens") or 0)
        cr = int(u.get("cache_read_input_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if min(inp, out, cw, cr) < 0:
        return None
    cost = ((inp + cw * CACHE_WRITE_MULT + cr * CACHE_READ_MULT) * price[0] + out * price[1]) / 1e6
    return cost, inp + cw + cr, out


def settle(payload: dict, data: dict | None, reserved: float) -> None:
    """Replace one request's reservation with what it cost, at the price the
    reservation was made at. With no usable usage (no response, or usage that
    doesn't name input and output tokens), charge the whole reservation: it may
    have been billed, so never assume it was free."""
    b = _current.get()
    if b is None:
        return
    with b.lock:
        st = b.state
        st["reserved_usd"] = max(0.0, st["reserved_usd"] - reserved)
        model = _model_of(payload)
        price = getattr(reserved, "price", None)
        u = (data or {}).get("usage") if isinstance(data, dict) else None
        got = _usage_cost(u, price) if isinstance(u, dict) and price is not None else None
        if got is None:
            cost, inp, out = float(reserved), 0, 0
        else:
            cost, inp, out = got
            cost = cost if cost == cost else float(reserved)  # never a NaN charge
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
