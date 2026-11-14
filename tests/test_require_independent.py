"""Accountable Agents item 6 (Bart, D-68 a): the operator can require an
independent, second-vendor review. Operator-settable only: the policy file sets
it, and no tool argument or environment variable can.

Each refusal is paired with a control that differs only in the vendors
available, so the refusal is shown to come from the requirement."""
from __future__ import annotations

import json

import pytest

from rapier import operator_policy as OP
from rapier.mcp import tools


class _Reached(Exception):
    pass


class _FakePreset:
    policy = None

    def build(self):
        raise _Reached()


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "pol" / "policy.json"
    path.parent.mkdir()
    monkeypatch.setattr(OP, "POLICY_PATH", str(path))
    monkeypatch.delenv(OP.ENV_VAR, raising=False)
    return lambda data: path.write_text(json.dumps(data))


@pytest.fixture
def vendors(monkeypatch):
    from rapier import models

    def use(*vs):
        monkeypatch.setattr(models, "available_vendors", lambda: list(vs))
    return use


@pytest.fixture
def mcp_stubbed(monkeypatch):
    monkeypatch.setattr(tools, "preflight_error", lambda: None)
    monkeypatch.setattr(tools, "load_preset", lambda *a, **k: _FakePreset())


def test_single_vendor_refused_when_required(policy, vendors, mcp_stubbed):
    policy({"require_independent": True})
    vendors("anthropic")
    out = tools.run_spar("ship it?")
    assert out["ok"] is False
    assert out["refused_by"] == "operator_require_independent"
    assert out["final_state"] == "refuse"
    assert "only one vendor is available (anthropic)" in out["error"]


def test_control_two_vendors_runs(policy, vendors, mcp_stubbed):
    policy({"require_independent": True})
    vendors("anthropic", "openai")
    with pytest.raises(_Reached):
        tools.run_spar("ship it?")


def test_control_not_required_single_vendor_runs(policy, vendors, mcp_stubbed):
    policy({"min_verify": "gate"})
    vendors("anthropic")
    with pytest.raises(_Reached):
        tools.run_spar("ship it?")


def test_environment_cannot_require_or_unrequire_it(policy, vendors, monkeypatch):
    vendors("anthropic")
    for name in ("RAPIER_REQUIRE_INDEPENDENT", "RAPIER_INDEPENDENCE"):
        monkeypatch.setenv(name, "true")
    OP.decide_independence()  # env alone: nothing required
    policy({"require_independent": True})
    for name in ("RAPIER_REQUIRE_INDEPENDENT", "RAPIER_INDEPENDENCE"):
        monkeypatch.setenv(name, "false")
    with pytest.raises(OP.PolicyError):
        OP.decide_independence()


def test_non_boolean_fails_closed(policy, vendors):
    vendors("anthropic", "openai")
    policy({"require_independent": "yes"})
    with pytest.raises(OP.PolicyError, match="true or false"):
        OP.decide_independence()


def test_require_alone_sets_no_verify_minimum(policy):
    policy({"require_independent": True})
    assert OP.operator_minimum() == (None, "", False)


def test_cli_refuses_single_vendor(policy, vendors, monkeypatch, capsys):
    from rapier import cli, onboarding

    monkeypatch.setattr(onboarding, "preflight_error", lambda: None)
    monkeypatch.setattr(cli, "load_preset", lambda *a, **k: _FakePreset())
    policy({"require_independent": True})
    vendors("anthropic")
    assert cli.main(["spar", "--request", "x"]) == 2
    assert "independent review" in capsys.readouterr().err
    vendors("anthropic", "openai")
    with pytest.raises(_Reached):
        cli.main(["spar", "--request", "x", "--no-save"])
