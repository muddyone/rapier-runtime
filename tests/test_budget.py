"""A hard dollar cap on a run (D-111/D-115 prep: no spend without a cap that holds)."""
from __future__ import annotations

import pytest

from rapier import budget, models


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv("RAPIER_MAX_USD", raising=False)
    monkeypatch.delenv("RAPIER_PRICES", raising=False)
    budget.set_budget(None)
    yield
    budget.set_budget(None)


@pytest.fixture
def fake_http(monkeypatch):
    calls = []

    def raw(url, headers, payload, timeout=120):
        calls.append(payload)
        return {"usage": {"input_tokens": 1000, "output_tokens": 500}, "content": []}

    monkeypatch.setattr(models, "_post_with_retry_raw", raw)
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
    budget.set_budget(0.05)
    sent = 0
    for _ in range(1000):
        try:
            models._post_with_retry("u", {}, _payload("gpt-5.5", max_tokens=1000))
            sent += 1
        except budget.BudgetExceeded:
            break
    snap = budget.snapshot()
    assert sent > 0 and snap["spent_usd"] <= 0.05 and snap["stopped"]


def test_unpriced_model_is_refused_under_a_cap(fake_http):
    budget.set_budget(10.0)
    with pytest.raises(budget.BudgetExceeded, match="no price"):
        models._post_with_retry("u", {}, _payload("mystery-model"))
    assert fake_http == []


def test_failed_call_is_charged_its_reservation(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout")

    monkeypatch.setattr(models, "_post_with_retry_raw", boom)
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

    def raw(url, headers, payload, timeout=120):
        if "anthropic" in url:
            text = json.dumps({"items": [{"text": "a", "anchor": {"class": "c", "basis": "x"}}]})
            return {"usage": {"input_tokens": 10, "output_tokens": 10},
                    "content": [{"type": "text", "text": text}]}
        text = json.dumps({"none_found": True, "searched": "all"})
        return {"usage": {"prompt_tokens": 10, "completion_tokens": 10},
                "choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(models, "_post_with_retry_raw", raw)
    env = Pipeline.from_manifest(load_preset("map")).run(
        "plan", seed_meta={"map_regions": ["A", "B"]}, max_usd=0.20)
    m = env.meta["map"]
    assert env.meta["budget"]["stopped"] is True
    assert env.meta["budget"]["spent_usd"] <= 0.20
    assert any("dollar cap" in x for x in m["gate"]["did_not_run"])
    assert map_final_state(m)[0] == "defer"
    assert "STOPPED at the cap" in env.meta["report_md"]
