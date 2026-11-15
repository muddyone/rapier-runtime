"""The dollar cap, swept as a class (verifier M-1/M-4 widened, with the PM's points).

Every number that feeds a reservation or a charge fails closed unless it is
finite, non-negative and under its ceiling; the budget is set and reset around
the whole run; one test per input path, each with a positive control; a seeded
property test over random call sequences; fault injection on every exit path.
"""
from __future__ import annotations

import json
import math
import os
import random

import pytest

from rapier import budget, models


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import time
    monkeypatch.delenv("RAPIER_MAX_USD", raising=False)
    monkeypatch.delenv("RAPIER_PRICES", raising=False)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    tok = budget.set_budget(None)
    yield
    budget.reset_budget(tok)


class _Resp:
    def __init__(self, status=200, usage=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._usage = {"input_tokens": 100, "output_tokens": 100} if usage is None else usage

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return {"usage": self._usage, "content": []}


def _send(monkeypatch, fn):
    sent = []

    def wrapped(url, headers, payload, timeout):
        sent.append(payload)
        return fn(payload)
    monkeypatch.setattr(models, "_send_once", wrapped)
    return sent


def _pay(model="claude-haiku-5-5", max_tokens=1000, **extra):
    p = {"model": model, "messages": [{"role": "user", "content": "x" * 200}], **extra}
    if max_tokens is not ...:
        p["max_tokens"] = max_tokens
    return p


BAD_INTS = [0, -1, -100_000_000, True, False, 1.5, float("nan"), float("inf"), "1000", None,
            budget.MAX_OUTPUT_TOKENS + 1, 10**12]


# --- M-1: the output limit -----------------------------------------------------

@pytest.mark.parametrize("bad", BAD_INTS)
def test_bad_output_limit_is_refused_before_sending(monkeypatch, bad):
    sent = _send(monkeypatch, lambda p: _Resp())
    budget.set_budget(1.0)
    with pytest.raises(budget.BudgetExceeded):
        models._post_with_retry("u", {}, _pay(max_tokens=bad))
    assert sent == [] and budget.snapshot()["spent_usd"] == 0


@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens"])
def test_control_good_output_limit_runs(monkeypatch, key):
    sent = _send(monkeypatch, lambda p: _Resp())
    budget.set_budget(1.0)
    p = _pay(max_tokens=...)
    p[key] = budget.MAX_OUTPUT_TOKENS
    models._post_with_retry("u", {}, p)
    assert len(sent) == 1


def test_missing_output_limit_is_refused(monkeypatch):
    sent = _send(monkeypatch, lambda p: _Resp())
    budget.set_budget(1.0)
    with pytest.raises(budget.BudgetExceeded, match="no output limit"):
        models._post_with_retry("u", {}, _pay(max_tokens=...))
    assert sent == []


def test_m1_repro_negative_limit_cannot_credit_spend_and_unlock_the_cap(monkeypatch):
    """rr-1's repro: cap $0.10, max_tokens -1e8, vendor 400 -> spent -$2999.999,
    then 100 normal calls went through."""
    _send(monkeypatch, lambda p: _Resp(status=400) if p.get("max_tokens", 0) < 0 else _Resp())
    budget.set_budget(0.10)
    with pytest.raises(budget.BudgetExceeded):
        models._post_with_retry("u", {}, _pay("gpt-5.5", max_tokens=-100_000_000))
    ok = 0
    for _ in range(100):
        try:
            models._post_with_retry("u", {}, _pay("gpt-5.5", max_tokens=1000))
            ok += 1
        except budget.BudgetExceeded:
            break
    snap = budget.snapshot()
    assert 0 <= snap["spent_usd"] <= 0.10 and ok < 100


def test_reservations_and_charges_are_floored_at_zero(monkeypatch):
    budget.set_budget(1.0)
    budget.settle(_pay(), {"usage": {"input_tokens": 1, "output_tokens": 1}}, budget.Reservation(-5.0))
    snap = budget.snapshot()
    assert snap["spent_usd"] >= 0 and snap["reserved_usd"] >= 0


# --- manifest load (the binding that feeds max_tokens) ----------------------------

def _manifest(tmp_path, max_tokens):
    import yaml
    f = tmp_path / "m.yaml"
    f.write_text(yaml.safe_dump({"name": "t", "pipeline": [{"stage": "echo", "roles": {
        "author": {"vendor": "anthropic", "model": "claude-haiku-5-5", "max_tokens": max_tokens}}}]}))
    return str(f)


@pytest.mark.parametrize("bad", [0, -1, -100_000_000, True, 1.5, float("nan"), "4096",
                                 budget.MAX_OUTPUT_TOKENS + 1])
def test_manifest_with_bad_max_tokens_is_refused_on_load(tmp_path, bad):
    from rapier.manifest import Manifest
    with pytest.raises(ValueError, match="max_tokens"):
        Manifest.load(_manifest(tmp_path, bad))


def test_control_manifest_with_good_max_tokens_loads(tmp_path):
    from rapier.manifest import Manifest
    m = Manifest.load(_manifest(tmp_path, 4096))
    assert m.stages[0].roles["author"].max_tokens == 4096


def test_cli_run_with_a_bad_manifest_exits_2_and_control_runs(tmp_path, monkeypatch, capsys):
    import rapier.cli as cli
    monkeypatch.setattr("rapier.onboarding.preflight_error", lambda: None)
    assert cli.main(["run", "--manifest", _manifest(tmp_path, -5), "--request", "x", "--no-save"]) == 2
    assert "max_tokens" in capsys.readouterr().err
    called = {}
    monkeypatch.setattr(cli, "_run", lambda m, *a, **k: called.setdefault("ok", True) and 0)
    assert cli.main(["run", "--manifest", _manifest(tmp_path, 4096), "--request", "x", "--no-save"]) == 0
    assert called["ok"]


def test_every_preset_role_has_a_valid_output_limit():
    """MCP can't supply a manifest: its only model specs are the presets'."""
    from rapier.presets import PRESETS, load_preset
    for name in PRESETS:
        for s in load_preset(name).stages:
            for spec in s.roles.values():
                assert isinstance(spec.max_tokens, int) and 0 < spec.max_tokens <= budget.MAX_OUTPUT_TOKENS


def test_a_client_built_with_a_bad_limit_is_refused_at_the_call(monkeypatch):
    """Any path that builds a ModelSpec directly still meets the payload check."""
    sent = _send(monkeypatch, lambda p: _Resp())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    budget.set_budget(1.0)
    client = models.build_client(models.ModelSpec("anthropic", "claude-haiku-5-5", max_tokens=-7))
    with pytest.raises(budget.BudgetExceeded):
        client.complete("s", "p")
    assert sent == []


# --- the cap: CLI, MCP, env, ceiling ---------------------------------------------

@pytest.mark.parametrize("bad", ["nan", "inf", "-1", "1e999", "abc", str(budget.MAX_CAP_USD + 1), "1e9"])
def test_cli_bad_max_usd_is_a_usage_error(bad, monkeypatch):
    import rapier.cli as cli
    monkeypatch.setattr("rapier.onboarding.preflight_error", lambda: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["spar", "--request", "x", "--max-usd", bad, "--no-save"])
    assert e.value.code == 2


def test_cli_control_good_max_usd_reaches_the_run(monkeypatch):
    import rapier.cli as cli
    monkeypatch.setattr("rapier.onboarding.preflight_error", lambda: None)
    seen = {}
    monkeypatch.setattr(cli, "_run", lambda *a, **k: seen.update(k) or 0)
    assert cli.main(["spar", "--request", "x", "--max-usd", "8", "--no-save"]) == 0
    assert seen["max_usd"] == 8.0


@pytest.mark.parametrize("tool", ["run_spar", "run_sparring", "run_proposer", "run_map"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, budget.MAX_CAP_USD + 1, True, "abc"])
def test_mcp_bad_max_usd_is_refused_before_running(monkeypatch, tool, bad):
    from rapier.mcp import tools
    monkeypatch.setattr(tools, "preflight_error", lambda: None)
    ran = []
    monkeypatch.setattr(tools, "load_preset", lambda *a, **k: ran.append(1))
    res = getattr(tools, tool)("x", max_usd=bad)
    assert res["final_state"] == "refuse" and res["refused_by"] == "max_usd" and ran == []


@pytest.mark.parametrize("tool", ["run_spar", "run_map"])
@pytest.mark.parametrize("good", [0, 0.0, 8, budget.MAX_CAP_USD])
def test_mcp_control_good_max_usd_reaches_the_pipeline(monkeypatch, tool, good):
    from rapier.mcp import tools
    monkeypatch.setattr(tools, "preflight_error", lambda: None)
    seen = {}

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
        getattr(tools, tool)("x", max_usd=good)
    assert seen["max_usd"] == float(good)


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-3", "1e999", "x", "1e9",
                                 str(budget.MAX_CAP_USD + 0.01)])
def test_env_cap_fails_closed(monkeypatch, bad):
    monkeypatch.setenv("RAPIER_MAX_USD", bad)
    assert budget.effective_cap(None) == 0.0


@pytest.mark.parametrize("good,expect", [("5", 5.0), ("0", 0.0), (str(budget.MAX_CAP_USD), budget.MAX_CAP_USD)])
def test_env_cap_control(monkeypatch, good, expect):
    monkeypatch.setenv("RAPIER_MAX_USD", good)
    assert budget.effective_cap(None) == expect


# --- prices ---------------------------------------------------------------------

@pytest.mark.parametrize("entry", [[float("inf"), 1], [1, float("nan")], [-1, 1], [0, 1], [1, 0],
                                   [budget.MAX_PRICE_PER_M + 1, 1], ["1", 1], [1], "1,1", None, [True, 1]])
def test_bad_price_override_adds_nothing(monkeypatch, entry):
    monkeypatch.setenv("RAPIER_PRICES", json.dumps({"new-model": entry}, allow_nan=True))
    assert "new-model" not in budget._prices() or entry == ["1", 1]
    if entry == ["1", 1]:
        assert budget._prices()["new-model"] == (1.0, 1.0)


def test_numeric_string_price_is_read_as_a_number(monkeypatch):
    # float("1") is a valid price; only the shape check matters, never the type
    monkeypatch.setenv("RAPIER_PRICES", json.dumps({"new-model": ["1", "2"]}))
    assert budget._prices()["new-model"] == (1.0, 2.0)


def test_unreadable_prices_json_adds_nothing(monkeypatch):
    monkeypatch.setenv("RAPIER_PRICES", "{not json")
    assert budget._prices() == budget.PRICES


@pytest.mark.parametrize("bad", [(0.0, 1.0), (float("nan"), 1.0), (-1.0, 1.0), (1.0, float("inf")),
                                 (budget.MAX_PRICE_PER_M * 2, 1.0)])
def test_bad_built_in_price_is_refused(monkeypatch, bad):
    monkeypatch.setitem(budget.PRICES, "claude-haiku-5-5", bad)
    sent = _send(monkeypatch, lambda p: _Resp())
    budget.set_budget(1.0)
    with pytest.raises(budget.BudgetExceeded, match="not a usable number"):
        models._post_with_retry("u", {}, _pay())
    assert sent == []


def test_every_built_in_price_is_usable():
    for model, (i, o) in budget.PRICES.items():
        assert budget._good_price(i) and budget._good_price(o), model


# --- usage fields ---------------------------------------------------------------

USAGE_FIELDS = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
BAD_COUNTS = [-1, True, 1.5, float("nan"), float("inf"), "100", [1], budget.MAX_USAGE_TOKENS + 1]


@pytest.mark.parametrize("field", USAGE_FIELDS)
@pytest.mark.parametrize("bad", BAD_COUNTS)
def test_bad_usage_count_is_charged_the_reservation(monkeypatch, field, bad):
    usage = {"input_tokens": 100, "output_tokens": 100, field: bad}
    _send(monkeypatch, lambda p: _Resp(usage=usage))
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _pay())
    snap = budget.snapshot()
    assert snap["spent_usd"] == pytest.approx(budget.worst_case_usd(_pay()))
    assert math.isfinite(snap["spent_usd"])


@pytest.mark.parametrize("field", USAGE_FIELDS)
def test_control_good_usage_count_is_charged_at_its_value(monkeypatch, field):
    usage = {"input_tokens": 100, "output_tokens": 100, field: 1000}
    _send(monkeypatch, lambda p: _Resp(usage=usage))
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _pay())
    assert budget.snapshot()["spent_usd"] < budget.worst_case_usd(_pay())


def test_openai_usage_shape_is_read(monkeypatch):
    _send(monkeypatch, lambda p: _Resp(usage={"prompt_tokens": 1000, "completion_tokens": 1000}))
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _pay("gpt-5.5"))
    assert budget.snapshot()["spent_usd"] == pytest.approx(1000 * 5 / 1e6 + 1000 * 30 / 1e6)


# --- Retry-After ------------------------------------------------------------------

@pytest.mark.parametrize("ra", ["inf", "nan", "-5", "1e999", "abc", "", None, "99999"])
def test_bad_retry_after_uses_the_backoff(ra):
    assert 0 <= models._retry_wait(ra, 2.0) <= 30


@pytest.mark.parametrize("ra,expect", [("3", 3.0), ("0", 0.0), ("2.5", 2.5), ("30", 30.0)])
def test_control_retry_after_is_used(ra, expect):
    assert models._retry_wait(ra, 2.0) == expect


def test_retry_after_on_429_is_released_not_charged(monkeypatch):
    seq = iter([_Resp(status=429, headers={"Retry-After": "inf"}), _Resp()])
    _send(monkeypatch, lambda p: next(seq))
    budget.set_budget(1.0)
    models._post_with_retry("u", {}, _pay())
    snap = budget.snapshot()
    assert snap["released"] == 1 and snap["calls"] == 1


# --- property test: random sequences never break the invariants -------------------

HOSTILE_USAGE = [{"input_tokens": 100, "output_tokens": 50}, {"prompt_tokens": 3000, "completion_tokens": 900},
                 {"total_tokens": 10}, {}, None, {"input_tokens": -9, "output_tokens": 1},
                 {"input_tokens": float("nan"), "output_tokens": 1}, {"input_tokens": True, "output_tokens": 1},
                 {"input_tokens": 10**12, "output_tokens": 1},
                 {"input_tokens": 10, "output_tokens": 10, "cache_creation_input_tokens": 5000}]


def _random_outcome(rng):
    import requests
    k = rng.randrange(9)
    if k == 0:
        return requests.exceptions.ReadTimeout("t")
    if k == 1:
        return requests.exceptions.ConnectTimeout("c")
    if k == 2:
        return requests.exceptions.ConnectionError("Connection aborted.")
    if k == 3:
        return _Resp(status=rng.choice([429, 500, 503]), headers={"Retry-After": rng.choice(["1", "inf", "x"])})
    if k == 4:
        return _Resp(status=rng.choice([400, 401, 404]))
    return _Resp(usage=rng.choice(HOSTILE_USAGE))


@pytest.mark.parametrize("seed", range(40))
def test_property_random_sequences_keep_every_invariant(monkeypatch, seed):
    """Spent never exceeds the cap, never goes negative, equals the sum of the
    charges actually made, and nothing stays reserved after the sequence."""
    rng = random.Random(seed)
    charges: list[float] = []
    real_settle = budget.settle

    def spy(payload, data, reserved):
        before = budget.snapshot()["spent_usd"]
        real_settle(payload, data, reserved)
        charges.append(budget.snapshot()["spent_usd"] - before)
    monkeypatch.setattr(budget, "settle", spy)

    def send(payload):
        x = _random_outcome(rng)
        if isinstance(x, BaseException):
            raise x
        return x
    sent = _send(monkeypatch, send)
    n_sent = None
    cap = rng.choice([0.0, 0.01, 0.05, 0.3, 1.0])
    budget.set_budget(cap)
    for _ in range(rng.randrange(1, 60)):
        p = _pay(rng.choice(["claude-haiku-5-5", "gpt-5.5", "claude-sonnet-5-5"]),
                 max_tokens=rng.choice([1, 100, 1000, 8000, -5, 0, True]))
        try:
            models._post_with_retry("u", {}, p)
        except Exception:  # noqa: BLE001 - refused, HTTP error, retries exhausted
            pass
        snap = budget.snapshot()
        # Spend stays under the cap, except by a recorded overrun (a vendor billing
        # past what was reserved), which stops the run at once.
        assert snap["spent_usd"] <= cap + snap["overrun_usd"] + 1e-12
        if snap["overrun_usd"] > 0:
            assert snap["stopped"] is True
            n_sent = len(sent)
        assert snap["spent_usd"] >= 0 and snap["reserved_usd"] >= 0
        assert all(c >= 0 for c in charges)
    snap = budget.snapshot()
    assert snap["reserved_usd"] == pytest.approx(0, abs=1e-12)
    assert snap["spent_usd"] == pytest.approx(sum(charges), abs=1e-9)
    if n_sent is not None:
        assert len(sent) == n_sent  # nothing was sent after the overrun


@pytest.mark.parametrize("seed", range(40))
def test_property_honest_usage_never_passes_the_cap(monkeypatch, seed):
    """With usage a real vendor can report (output within the limit, input
    within the payload), spend never passes the cap, with no exceptions."""
    import requests
    rng = random.Random(1000 + seed)

    def send(payload):
        k = rng.randrange(6)
        if k == 0:
            raise requests.exceptions.ReadTimeout("t")
        if k == 1:
            return _Resp(status=503)
        lim = payload["max_tokens"]
        size = len(json.dumps(payload))
        return _Resp(usage={"input_tokens": rng.randrange(1, size), "output_tokens": rng.randrange(0, lim + 1),
                            "cache_read_input_tokens": rng.randrange(0, 50)})
    _send(monkeypatch, send)
    cap = rng.choice([0.01, 0.05, 0.3, 1.0])
    budget.set_budget(cap)
    for _ in range(80):
        try:
            models._post_with_retry("u", {}, _pay(rng.choice(["claude-haiku-5-5", "gpt-5.5"]),
                                                  max_tokens=rng.choice([1, 100, 1000, 8000])))
        except Exception:  # noqa: BLE001
            pass
        snap = budget.snapshot()
        assert snap["spent_usd"] <= cap and snap["overrun_usd"] == 0


def test_an_overrun_is_charged_in_full_and_stops_the_run(monkeypatch):
    sent = _send(monkeypatch, lambda p: _Resp(usage={"input_tokens": 10, "output_tokens": 50_000}))
    budget.set_budget(10.0)
    models._post_with_retry("u", {}, _pay("gpt-5.5", max_tokens=10))
    snap = budget.snapshot()
    assert snap["overrun_usd"] > 0 and snap["stopped"] is True
    assert snap["spent_usd"] == pytest.approx(10 * 5 / 1e6 + 50_000 * 30 / 1e6)  # never clamped
    with pytest.raises(budget.BudgetExceeded, match="more than it reserved"):
        models._post_with_retry("u", {}, _pay("gpt-5.5", max_tokens=10))
    assert len(sent) == 1


# --- fault injection on every exit path ------------------------------------------

def _pipe(monkeypatch, behaviour):
    from rapier.pipeline import Pipeline, StageSpec
    from rapier.stage import _REGISTRY, TransformStage

    class S(TransformStage):
        name = "_fault"

        def run(self, env, ctx):
            models._post_with_retry("u", {}, _pay())
            behaviour(env, ctx)
            return env

    monkeypatch.setitem(_REGISTRY, "_fault", S)
    _send(monkeypatch, lambda p: _Resp())
    return Pipeline([StageSpec("_fault")], name="t")


def _budget_json(root):
    for r, _, fs in os.walk(root):
        if "budget.json" in fs:
            return json.load(open(os.path.join(r, "budget.json")))
    return None


def _boom(exc):
    def f(env, ctx):
        raise exc
    return f


@pytest.mark.parametrize("label,behaviour,raises", [
    ("normal", lambda e, c: None, None),
    ("stage raises (fail-soft)", _boom(RuntimeError("stage")), None),
    ("stage raises past fail-soft", _boom(KeyboardInterrupt()), KeyboardInterrupt),
    ("stage raises SystemExit", _boom(SystemExit(3)), SystemExit),
])
def test_every_exit_settles_writes_and_releases(monkeypatch, tmp_path, label, behaviour, raises):
    pipe = _pipe(monkeypatch, behaviour)
    if raises:
        with pytest.raises(raises):
            pipe.run("x", ledger_root=str(tmp_path), max_usd=1.0)
    else:
        env = pipe.run("x", ledger_root=str(tmp_path), max_usd=1.0)
        assert env.meta["budget"]["calls"] == 1
    snap = _budget_json(tmp_path)
    assert snap is not None and snap["calls"] == 1 and snap["reserved_usd"] == 0, label
    assert budget.active() is False


def test_setup_failure_releases_the_budget(monkeypatch):
    """rr-1 M-4: a raise while the ledger is set up left the budget installed."""
    pipe = _pipe(monkeypatch, lambda e, c: None)
    with pytest.raises(OSError):
        pipe.run("x", ledger_root="/proc/forbidden/x", max_usd=0.5)
    assert budget.active() is False


def test_seed_meta_failure_releases_the_budget(monkeypatch):
    pipe = _pipe(monkeypatch, lambda e, c: None)

    from collections.abc import Mapping

    class Bad(Mapping):
        def __getitem__(self, k):
            raise RuntimeError("bad seed")

        def __iter__(self):
            raise RuntimeError("bad seed")

        def __len__(self):
            return 1

        def keys(self):
            raise RuntimeError("bad seed")
    with pytest.raises(RuntimeError, match="bad seed"):
        pipe.run("x", max_usd=0.5, seed_meta=Bad())
    assert budget.active() is False


def test_a_run_never_clears_an_enclosing_budget(monkeypatch):
    pipe = _pipe(monkeypatch, _boom(KeyboardInterrupt()))
    budget.set_budget(7.0)
    with pytest.raises(KeyboardInterrupt):
        pipe.run("x", max_usd=1.0)
    assert budget.snapshot()["cap_usd"] == 7.0  # the outer budget is back, untouched


def test_ledger_write_failure_in_finally_still_releases(monkeypatch, tmp_path):
    from rapier.ledger import Ledger
    pipe = _pipe(monkeypatch, lambda e, c: None)
    real = Ledger.write_json

    def fail_budget(self, name, obj):
        if name == "budget.json":
            raise OSError("disk full")
        return real(self, name, obj)
    monkeypatch.setattr(Ledger, "write_json", fail_budget)
    env = pipe.run("x", ledger_root=str(tmp_path), max_usd=1.0)
    assert env.meta["budget"]["calls"] == 1 and budget.active() is False


def test_compose_failure_still_records_spend(monkeypatch, tmp_path):
    """A compose-stage crash is fail-soft like any stage: spend still recorded."""
    from rapier.pipeline import Pipeline, StageSpec
    from rapier.stage import _REGISTRY, TransformStage

    class Spend(TransformStage):
        name = "_spend"

        def run(self, env, ctx):
            models._post_with_retry("u", {}, _pay())
            return env

    class Compose(TransformStage):
        name = "_compose"

        def run(self, env, ctx):
            raise ValueError("compose broke")

    monkeypatch.setitem(_REGISTRY, "_spend", Spend)
    monkeypatch.setitem(_REGISTRY, "_compose", Compose)
    _send(monkeypatch, lambda p: _Resp())
    Pipeline([StageSpec("_spend"), StageSpec("_compose")], name="t").run(
        "x", ledger_root=str(tmp_path), max_usd=1.0)
    assert _budget_json(tmp_path)["calls"] == 1 and budget.active() is False


def test_cancel_stops_at_a_boundary_and_records_spend(monkeypatch, tmp_path):
    from rapier.pipeline import Pipeline, StageSpec
    from rapier.stage import _REGISTRY, TransformStage
    flag = {"stop": False}

    class Spend(TransformStage):
        name = "_spend_c"

        def run(self, env, ctx):
            models._post_with_retry("u", {}, _pay())
            flag["stop"] = True
            return env

    monkeypatch.setitem(_REGISTRY, "_spend_c", Spend)
    _send(monkeypatch, lambda p: _Resp())
    try:
        Pipeline([StageSpec("_spend_c"), StageSpec("_spend_c")], name="t").run(
            "x", ledger_root=str(tmp_path), max_usd=1.0, cancel=lambda: flag["stop"])
    except Exception:  # noqa: BLE001 - a cancel may surface as an error; spend must still be recorded
        pass
    snap = _budget_json(tmp_path)
    assert snap["calls"] == 1 and budget.active() is False


def test_abandoned_mcp_call_keeps_its_own_cap_and_records_spend(monkeypatch, tmp_path):
    """The MCP wrapper times out and abandons the worker; the worker finishes in
    its own context, under its own cap, and writes its record."""
    import threading

    import anyio

    from rapier.mcp.server import _run_with_progress
    release = threading.Event()
    done = threading.Event()

    def fn(log, cancel, ledger_root, **kw):
        pipe = _pipe(monkeypatch, lambda e, c: release.wait(5))
        try:
            pipe.run("x", ledger_root=ledger_root, max_usd=0.5)
        finally:
            done.set()
        return {"ok": True}

    async def main():
        return await _run_with_progress(fn, None, 1, timeout_s=0.2, ledger_root=str(tmp_path))
    res = anyio.run(main)
    assert res["ok"] is False and "timed out" in res["error"]
    assert budget.active() is False          # the caller's context never had it
    release.set()
    assert done.wait(5)
    snap = _budget_json(tmp_path)
    assert snap is not None and snap["cap_usd"] == 0.5 and snap["calls"] == 1


def test_an_overrun_is_named_in_the_report():
    from rapier.stages.resolver.compose import _overrun_note
    assert "more than it reserved" in _overrun_note({"overrun_usd": 0.5})
    assert _overrun_note({"overrun_usd": 0.0}) == ""
