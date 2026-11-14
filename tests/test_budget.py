"""A hard dollar cap on a run (D-111/D-115 prep: no spend without a cap that holds)."""
from __future__ import annotations

import pytest

from rapier import budget, models


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv("RAPIER_MAX_USD", raising=False)
    monkeypatch.delenv("RAPIER_PRICES", raising=False)
    tok = budget.set_budget(None)
    yield
    budget.reset_budget(tok)


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {
            "usage": {"input_tokens": 1000, "output_tokens": 500}, "content": []}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _fake_send(monkeypatch, fn):
    monkeypatch.setattr(models, "_send_once", fn)


@pytest.fixture
def fake_http(monkeypatch):
    calls = []

    def send(url, headers, payload, timeout=120):
        calls.append(payload)
        return _Resp()

    _fake_send(monkeypatch, send)
    return calls


def _payload(model="claude-haiku-5-5", max_tokens=1000, chars=4000):
    return {"model": model, "max_tokens": max_tokens, "messages": [{"content": "x" * chars}]}


def test_no_cap_means_no_guard(fake_http):
    models._post_with_retry("u", {}, _payload("unpriced-model"))
    assert len(fake_http) == 1 and budget.snapshot() is None


def test_spend_is_settled_from_usage(fake_http):
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _payload())
    snap = budget.snapshot()
    # 1000 in at $0.10/M + 500 out at $0.50/M
    assert snap["spent_usd"] == pytest.approx(0.0001 + 0.00025)
    assert snap["calls"] == 1 and snap["reserved_usd"] == 0


def test_a_call_that_could_pass_the_cap_is_refused_before_it_is_sent(fake_http):
    budget.set_budget(0.01)
    big = _payload("gpt-5.5", max_tokens=8000)  # worst case 8000 out x $30/M = $0.24
    with pytest.raises(budget.BudgetExceeded):
        models._post_with_retry("u", {}, big)
    assert fake_http == [] and budget.snapshot()["stopped"] is True


def test_control_same_call_under_a_big_enough_cap_runs(fake_http):
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _payload("gpt-5.5", max_tokens=8000))
    assert len(fake_http) == 1


def test_never_exceeds_the_cap_over_many_calls(fake_http):
    budget.set_budget(0.15)  # a few worst cases of ~$0.05 each (UTF-8 byte estimate)
    sent = 0
    for _ in range(1000):
        try:
            models._post_with_retry("u", {}, _payload("gpt-5.5", max_tokens=1000))
            sent += 1
        except budget.BudgetExceeded:
            break
    snap = budget.snapshot()
    assert sent > 1 and snap["spent_usd"] <= 0.15 and snap["stopped"]


def test_unpriced_model_is_refused_under_a_cap(fake_http):
    budget.set_budget(10.0)
    with pytest.raises(budget.BudgetExceeded, match="no price"):
        models._post_with_retry("u", {}, _payload("mystery-model"))
    assert fake_http == []


def test_failed_call_is_charged_its_reservation(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout")

    _fake_send(monkeypatch, boom)
    budget.set_budget(1.0)
    with pytest.raises(RuntimeError):
        models._post_with_retry("u", {}, _payload())
    assert budget.snapshot()["spent_usd"] > 0  # may have been billed: never assume free


def test_env_cap_is_a_ceiling_a_caller_cannot_raise(monkeypatch):
    monkeypatch.setenv("RAPIER_MAX_USD", "2")
    assert budget.effective_cap(50) == 2.0
    assert budget.effective_cap(1) == 1.0
    monkeypatch.setenv("RAPIER_MAX_USD", "not-a-number")
    assert budget.effective_cap(50) == 0.0  # unreadable: fails closed


def test_capped_map_run_reads_as_not_run_and_defers(monkeypatch):
    """Through Pipeline: a map run whose cap stops it reports did-not-run and
    defer, never a pass, and the report shows the spend."""
    import json

    from rapier.pipeline import Pipeline
    from rapier.presets import load_preset
    from rapier.stages.map import _common as C
    from rapier.stages.map.compose import map_final_state

    monkeypatch.setattr(C, "available_vendors", lambda: ["anthropic", "openai"])
    monkeypatch.setattr(C, "client_for", lambda v: models.build_client(
        models.ModelSpec("anthropic" if v == "anthropic" else "openai",
                         "claude-haiku-5-5" if v == "anthropic" else "gpt-5.5", max_tokens=8000)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    def send(url, headers, payload, timeout=120):
        if "anthropic" in url:
            text = json.dumps({"items": [{"text": "a", "anchor": {"class": "c", "basis": "x"}}]})
            return _Resp(body={"usage": {"input_tokens": 10, "output_tokens": 10},
                               "content": [{"type": "text", "text": text}]})
        text = json.dumps({"none_found": True, "searched": "all"})
        return _Resp(body={"usage": {"prompt_tokens": 10, "completion_tokens": 10},
                           "choices": [{"message": {"content": text}}]})

    _fake_send(monkeypatch, send)
    env = Pipeline.from_manifest(load_preset("map")).run(
        "plan", seed_meta={"map_regions": ["A", "B"]}, max_usd=0.20)
    m = env.meta["map"]
    assert env.meta["budget"]["stopped"] is True
    assert env.meta["budget"]["spent_usd"] <= 0.20
    assert any("dollar cap" in x for x in m["gate"]["did_not_run"])
    assert map_final_state(m)[0] == "defer"
    assert "STOPPED at the cap" in env.meta["report_md"]


# --- B1: the budget belongs to one run, not the process -------------------------

def test_two_concurrent_runs_each_keep_their_own_cap(monkeypatch):
    """rr-1 B1: two runs at once shared one global budget, so a $0.30 run reached
    ~$1.22. Each thread now holds its own budget."""
    import threading

    _fake_send(monkeypatch, lambda *a, **k: _Resp(
        body={"usage": {"input_tokens": 1000, "output_tokens": 8000}, "content": []}))
    barrier = threading.Barrier(2)
    out: dict[str, dict] = {}

    def run(name, cap):
        tok = budget.set_budget(cap)
        try:
            barrier.wait(5)
            for _ in range(50):
                try:
                    models._post_with_retry("u", {}, _payload("gpt-5.5", max_tokens=8000))
                except budget.BudgetExceeded:
                    break
            out[name] = budget.snapshot()
        finally:
            budget.reset_budget(tok)

    ts = [threading.Thread(target=run, args=("a", 0.30)),
          threading.Thread(target=run, args=("b", 3.0))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out["a"]["cap_usd"] == 0.30 and 0 < out["a"]["spent_usd"] <= 0.30
    assert out["b"]["cap_usd"] == 3.0 and 0.30 < out["b"]["spent_usd"] <= 3.0
    assert out["a"]["stopped"] and out["b"]["stopped"]
    assert budget.snapshot() is None  # this (main) context never had a budget


def test_a_run_ending_does_not_clear_a_concurrent_runs_cap(monkeypatch):
    import threading

    _fake_send(monkeypatch, lambda *a, **k: _Resp())
    a_set, b_done = threading.Event(), threading.Event()
    result = {}

    def run_a():
        tok = budget.set_budget(0.0)  # nothing may be spent
        a_set.set()
        b_done.wait(5)
        try:
            models._post_with_retry("u", {}, _payload())
            result["a"] = "sent"
        except budget.BudgetExceeded:
            result["a"] = "refused"
        finally:
            budget.reset_budget(tok)

    def run_b():
        a_set.wait(5)
        tok = budget.set_budget(10.0)
        budget.reset_budget(tok)  # B ends: under the old global, this cleared A's cap
        b_done.set()

    ta, tb = threading.Thread(target=run_a), threading.Thread(target=run_b)
    ta.start(); tb.start(); ta.join(); tb.join()
    assert result["a"] == "refused"


def test_pipeline_runs_in_parallel_worker_threads_keep_separate_budgets(monkeypatch):
    """Through Pipeline.run on anyio worker threads, the way the MCP server runs
    tools, including one run that is abandoned on timeout and keeps going."""
    import threading

    import anyio

    from rapier.pipeline import Pipeline, StageSpec
    from rapier.stage import _REGISTRY, TransformStage

    barrier = threading.Barrier(2)

    class Spend(TransformStage):
        name = "test_spend"

        def run(self, env, ctx):
            barrier.wait(5)
            for _ in range(40):
                try:
                    models._post_with_retry("u", {}, _payload("gpt-5.5", max_tokens=8000))
                except budget.BudgetExceeded:
                    break
            return env

    monkeypatch.setitem(_REGISTRY, "test_spend", Spend)
    _fake_send(monkeypatch, lambda *a, **k: _Resp(
        body={"usage": {"input_tokens": 1000, "output_tokens": 8000}, "content": []}))
    out = {}

    def go(name, cap):
        out[name] = Pipeline([StageSpec("test_spend")]).run("x", max_usd=cap).meta.get("budget")

    async def main():
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: anyio.to_thread.run_sync(go, "a", 0.30))
            tg.start_soon(lambda: anyio.to_thread.run_sync(go, "b", 3.0))

    anyio.run(main)
    assert out["a"]["cap_usd"] == 0.30 and out["a"]["spent_usd"] <= 0.30
    assert out["b"]["cap_usd"] == 3.0 and 0.30 < out["b"]["spent_usd"] <= 3.0


# --- B2: a cap the guard can't compare against is never "no cap" ------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, -0.01, "nan", "inf",
                                 "1e999", "-1", "abc", True])
def test_caller_cap_that_is_not_finite_and_non_negative_is_refused(bad):
    with pytest.raises(budget.InvalidCap):
        budget.effective_cap(bad)


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-1", "1e999", "NaN"])
def test_operator_ceiling_that_is_not_finite_fails_closed(monkeypatch, bad):
    monkeypatch.setenv("RAPIER_MAX_USD", bad)
    assert budget.effective_cap(None) == 0.0
    assert budget.effective_cap(50) == 0.0


def test_nan_cap_cannot_pass_the_operator_ceiling(monkeypatch):
    """rr-1 B2: min(nan, 5) is nan, and nan > cap is False, so $15.25 went out
    under a $5 ceiling."""
    monkeypatch.setenv("RAPIER_MAX_USD", "5")
    with pytest.raises(budget.InvalidCap):
        budget.effective_cap(float("nan"))
    tok = budget.set_budget(float("nan"))  # even if one were installed directly
    try:
        with pytest.raises(budget.BudgetExceeded):
            budget.reserve(_payload())
    finally:
        budget.reset_budget(tok)


def test_cli_refuses_a_bad_max_usd():
    import argparse

    from rapier.cli import _usd

    for bad in ("nan", "inf", "-1", "1e999"):
        with pytest.raises(argparse.ArgumentTypeError):
            _usd(bad)
    assert _usd("8") == 8.0 and _usd("0") == 0.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_mcp_tool_refuses_a_bad_max_usd_before_running(monkeypatch, bad):
    from rapier.mcp import tools

    monkeypatch.setattr(tools, "preflight_error", lambda: None)
    ran = []
    monkeypatch.setattr(tools, "load_preset", lambda *a, **k: ran.append(1))
    res = tools.run_spar("x", max_usd=bad)
    assert res["ok"] is False and res["final_state"] == "refuse"
    assert res["refused_by"] == "max_usd" and ran == []


# --- B3: every attempt is reserved and settled ----------------------------------

def test_each_retry_after_a_read_timeout_is_charged(monkeypatch):
    """rr-1 B3: up to 6 requests could be billed but only one was counted."""
    import requests

    n = {"i": 0}

    def send(*a, **k):
        n["i"] += 1
        if n["i"] < 4:
            raise requests.exceptions.ReadTimeout("read timed out")
        return _Resp()

    _fake_send(monkeypatch, send)
    budget.set_budget(10.0)
    one = budget.worst_case_usd(_payload())
    models._post_with_retry("u", {}, _payload())
    snap = budget.snapshot()
    assert snap["calls"] == 4  # three timeouts + the answer, all charged
    assert snap["spent_usd"] == pytest.approx(3 * one + 0.0001 + 0.00025, rel=1e-6)
    assert snap["reserved_usd"] == 0


def test_retries_stop_at_the_cap(monkeypatch):
    import requests

    sent = []

    def send(*a, **k):
        sent.append(1)
        raise requests.exceptions.ReadTimeout("read timed out")

    _fake_send(monkeypatch, send)
    one = budget.worst_case_usd(_payload("gpt-5.5", max_tokens=8000))
    budget.set_budget(one * 2.5)  # room for two attempts, not three
    with pytest.raises(budget.BudgetExceeded):
        models._post_with_retry("u", {}, _payload("gpt-5.5", max_tokens=8000))
    snap = budget.snapshot()
    assert len(sent) == 2 and snap["spent_usd"] <= snap["cap_usd"]


def test_never_sent_and_rate_limited_attempts_are_released(monkeypatch):
    import requests
    from urllib3.exceptions import NewConnectionError

    seq = iter([
        requests.exceptions.ConnectTimeout("connect timed out"),
        requests.exceptions.ConnectionError(type("M", (), {"reason": NewConnectionError(None, "refused")})()),
        _Resp(status=429), _Resp(status=503), _Resp()])

    def send(*a, **k):
        x = next(seq)
        if isinstance(x, BaseException):
            raise x
        return x

    _fake_send(monkeypatch, send)
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _payload())
    snap = budget.snapshot()
    assert snap["released"] == 4 and snap["calls"] == 1
    assert snap["spent_usd"] == pytest.approx(0.0001 + 0.00025)


def test_a_dropped_connection_after_sending_is_charged_in_full(monkeypatch):
    import requests

    seq = iter([requests.exceptions.ConnectionError("Connection aborted."),
                requests.exceptions.ChunkedEncodingError("cut off"), _Resp()])

    def send(*a, **k):
        x = next(seq)
        if isinstance(x, BaseException):
            raise x
        return x

    _fake_send(monkeypatch, send)
    budget.set_budget(1.0)
    one = budget.worst_case_usd(_payload())
    models._post_with_retry("u", {}, _payload())
    snap = budget.snapshot()
    assert snap["released"] == 0 and snap["calls"] == 3
    assert snap["spent_usd"] == pytest.approx(2 * one + 0.00035, rel=1e-6)


def test_a_client_error_response_is_charged_in_full(monkeypatch):
    _fake_send(monkeypatch, lambda *a, **k: _Resp(status=400))
    budget.set_budget(1.0)
    with pytest.raises(Exception):
        models._post_with_retry("u", {}, _payload())
    assert budget.snapshot()["spent_usd"] == pytest.approx(budget.worst_case_usd(_payload()))


# --- soft: the input estimate is an upper bound ---------------------------------

def test_input_estimate_counts_utf8_bytes():
    ascii_p = _payload(chars=4000)
    wide_p = {"model": "claude-haiku-5-5", "max_tokens": 1000,
              "messages": [{"content": "\u6f22" * 4000}]}  # 3 bytes each in UTF-8
    out = 1000 * 0.50 / 1e6
    a_in = (budget.worst_case_usd(ascii_p) - out) * 1e6 / 0.10
    w_in = (budget.worst_case_usd(wide_p) - out) * 1e6 / 0.10
    assert a_in >= 4000 and w_in >= 12000  # never fewer tokens than bytes of text


# --- the grounding judge calls models outside the cap -----------------------------

def test_grounding_judge_is_refused_under_a_cap():
    from rapier.verify import service

    budget.set_budget(5.0)
    with pytest.raises(budget.BudgetExceeded, match="outside the dollar cap"):
        service.verify_artifacts([{"x": 1}], judge=True)
    with pytest.raises(budget.BudgetExceeded):
        service.verify_one({"x": 1}, map_claims=True)


# --- D-120 /spar findings (rr-1 S-1 .. S-6) ------------------------------------

def test_s1_spend_is_written_when_a_run_exits_abnormally(monkeypatch, tmp_path):
    """A stage settles a call, then raises something that isn't fail-soft: the
    spend still reaches budget.json and the envelope."""
    import os

    from rapier.pipeline import Pipeline, StageSpec
    from rapier.stage import _REGISTRY, TransformStage

    class SpendThenRaise(TransformStage):
        name = "_spend_then_raise"

        def run(self, env, ctx):
            models._post_with_retry("u", {}, _payload())
            raise KeyboardInterrupt  # not an Exception: escapes fail-soft

    monkeypatch.setitem(_REGISTRY, "_spend_then_raise", SpendThenRaise)
    _fake_send(monkeypatch, lambda *a, **k: _Resp())
    with pytest.raises(KeyboardInterrupt):
        Pipeline([StageSpec("_spend_then_raise")], name="t").run("x", ledger_root=str(tmp_path), max_usd=1.0)
    found = [os.path.join(r, f) for r, _, fs in os.walk(tmp_path) for f in fs if f == "budget.json"]
    assert found
    import json
    snap = json.load(open(found[0]))
    assert snap["calls"] == 1 and snap["spent_usd"] > 0


@pytest.mark.parametrize("override", [{"gpt-5.5": [0, 0]}, {"gpt-5.5": [0.01, 0.01]},
                                      {"gpt-5.5": [-1, 30]}, {"gpt-5.5": ["nan", 30]}])
def test_s2_prices_override_can_never_lower_a_built_in_price(monkeypatch, override):
    import json
    monkeypatch.setenv("RAPIER_PRICES", json.dumps(override))
    assert budget._prices()["gpt-5.5"] == budget.PRICES["gpt-5.5"]


def test_s2_zero_price_override_cannot_open_the_cap(monkeypatch):
    import json
    monkeypatch.setenv("RAPIER_PRICES", json.dumps({"gpt-5.5": [0, 0]}))
    _fake_send(monkeypatch, lambda *a, **k: _Resp(
        body={"usage": {"prompt_tokens": 100, "completion_tokens": 1000}}))
    budget.set_budget(1.0)
    sent = 0
    for _ in range(50):
        try:
            models._post_with_retry("u", {}, _payload("gpt-5.5"))
            sent += 1
        except budget.BudgetExceeded:
            break
    snap = budget.snapshot()
    assert sent < 50 and 0 < snap["spent_usd"] <= 1.0 and snap["refused"] == 1


def test_s2_control_override_may_add_a_model_or_raise_a_price(monkeypatch):
    import json
    monkeypatch.setenv("RAPIER_PRICES", json.dumps({"new-model": [1, 2], "gpt-5.5": [6, 40],
                                                    "zero-model": [0, 0]}))
    t = budget._prices()
    assert t["new-model"] == (1.0, 2.0) and t["gpt-5.5"] == (6.0, 40.0)
    assert "zero-model" not in t  # a zero price is invalid: still unpriced, still refused


@pytest.mark.parametrize("usage", [{"total_tokens": 1100}, {"prompt_tokens": 100},
                                   {"completion_tokens": 1000}, {"input_tokens": "x", "output_tokens": 1},
                                   {"input_tokens": -5, "output_tokens": 1}, {}])
def test_s3_usage_without_both_token_counts_is_charged_the_reservation(monkeypatch, usage):
    _fake_send(monkeypatch, lambda *a, **k: _Resp(body={"usage": usage}))
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _payload("gpt-5.5"))
    assert budget.snapshot()["spent_usd"] == pytest.approx(budget.worst_case_usd(_payload("gpt-5.5")))


def test_s3_anthropic_cache_tokens_are_charged(monkeypatch):
    _fake_send(monkeypatch, lambda *a, **k: _Resp(body={"usage": {
        "input_tokens": 100, "output_tokens": 100,
        "cache_creation_input_tokens": 10000, "cache_read_input_tokens": 20000}}))
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _payload("claude-haiku-5-5"))
    # (100 + 10000*1.25 + 20000*0.1) * $0.10/M + 100 * $0.50/M
    assert budget.snapshot()["spent_usd"] == pytest.approx((100 + 12500 + 2000) * 0.10 / 1e6 + 100 * 0.50 / 1e6)


def test_s3_the_worst_case_covers_a_full_cache_write():
    p = _payload("claude-haiku-5-5", max_tokens=0, chars=4000)
    in_tokens = len(__import__("json").dumps(p, ensure_ascii=False).encode()) + 64
    assert budget.worst_case_usd(p) >= in_tokens * 0.10 * 1.25 / 1e6 * 0.999


def test_s3_rapier_never_asks_for_prompt_caching():
    """Shown, not assumed: no payload Rapier builds carries cache_control."""
    import pathlib
    src = pathlib.Path(models.__file__).parent
    hits = [str(f) for f in src.rglob("*.py") if "cache_control" in f.read_text(encoding="utf-8")]
    assert hits == []


def test_s4_mcp_max_usd_zero_is_a_zero_cap_not_no_cap(monkeypatch):
    """server.py passed `max_usd or None`, so an explicit 0 meant uncapped."""
    import inspect

    from rapier.mcp import server, tools
    src = inspect.getsource(server)
    assert "max_usd or None" not in src and "max_usd: float = 0" not in src
    seen = {}
    monkeypatch.setattr(tools, "preflight_error", lambda: None)

    class _Pipe:
        def run(self, request, **kw):
            seen["max_usd"] = kw["max_usd"]
            raise RuntimeError("stop")

    class _Preset:
        policy = None

        def build(self):
            return _Pipe()

    monkeypatch.setattr(tools, "load_preset", lambda *a, **k: _Preset())
    with pytest.raises(RuntimeError):
        tools.run_spar("x", max_usd=0)
    assert seen["max_usd"] == 0.0
    assert budget.effective_cap(0) == 0.0


def test_s5_settle_charges_at_the_reserve_time_price(monkeypatch):
    import json
    monkeypatch.setenv("RAPIER_PRICES", json.dumps({"temp-model": [1, 1]}))
    budget.set_budget(1.0)
    p = _payload("temp-model")
    r = budget.reserve(p)
    monkeypatch.delenv("RAPIER_PRICES")          # the price disappears before settle
    budget.settle(p, {"usage": {"input_tokens": 1000, "output_tokens": 1000}}, r)
    assert budget.snapshot()["spent_usd"] == pytest.approx(2000 * 1.0 / 1e6)


def test_s6_vendored_direct_callers_refuse_under_a_cap(monkeypatch):
    """Every vendored path that calls a vendor outside models.py refuses when a
    budget is active."""
    from rapier.verify import _bootstrap as B
    cc = B.verify_grounding.CC
    targets = [(cc, "call_anthropic"), (cc, "call_openai"), (B._orig_lib_llm, "call_claude"),
               (B._orig_lib_llm, "call_gpt"), (B._orig_lib_llm, "claude_json"),
               (B._orig_lib_llm, "gpt_json")]
    for mod, name in targets:
        f = getattr(mod, name)
        assert getattr(f, "_rapier_capped_guard", False), name
    assert all(getattr(cc, v.__name__) is v for v in cc.SUBSTRATES.values())
    budget.set_budget(5.0)
    for mod, name in targets:
        with pytest.raises(budget.BudgetExceeded, match="outside Rapier's model layer"):
            getattr(mod, name)("s", "u")
    # cite_check's own extraction calls call_anthropic by global name: guarded too
    with pytest.raises(budget.BudgetExceeded):
        cc._extract_chunk("   1| text")


def test_s6_every_vendor_http_call_is_in_models_or_guarded():
    """Static audit: outside models.py, vendor endpoints appear only in the
    vendored callers that the guard wraps."""
    import pathlib
    import re as _re
    root = pathlib.Path(models.__file__).parent
    pat = _re.compile(r"api\.anthropic\.com|api\.openai\.com|generativelanguage|/chat/completions")
    hits = sorted(str(f.relative_to(root)) for f in root.rglob("*.py")
                  if f.name != "models.py" and pat.search(f.read_text(encoding="utf-8")))
    assert hits == ["verify/_vendor/cite_check.py", "verify/_vendor/lib_llm.py"]
