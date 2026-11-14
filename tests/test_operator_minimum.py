"""Accountable Agents item 1: an operator minimum for the checks that a caller cannot lower.

Each negative test pairs with a control. The same call is refused when the
operator minimum is set and gets past the policy step when it is not, so the
refusal is shown to come from the minimum and from nothing else.
"""
from __future__ import annotations

import inspect
import json
import os

import pytest

from rapier import operator_policy as OP
from rapier.envelope import Envelope
from rapier.mcp import tools
from rapier.operator_policy import PolicyError, decide_verify, operator_minimum
from rapier.stage import StageContext
from rapier.stages.resolver.compose import ComposeStage, _render_report


class _Reached(Exception):
    """Raised by a stubbed load_preset: the call got past the policy step."""


@pytest.fixture
def policy(tmp_path, monkeypatch):
    """Point the policy file at a temp path (absent until written), clear the env floor."""
    path = tmp_path / "etc-rapier" / "policy.json"
    path.parent.mkdir()
    monkeypatch.setattr(OP, "POLICY_PATH", str(path))
    monkeypatch.delenv(OP.ENV_VAR, raising=False)

    def write(level):
        path.write_text(json.dumps({"min_verify": level}))
        return path

    return write


@pytest.fixture
def mcp_stubbed(monkeypatch):
    monkeypatch.setattr(tools, "preflight_error", lambda: None)

    def reached(*a, **k):
        raise _Reached(k.get("verify"))

    monkeypatch.setattr(tools, "load_preset", reached)


# ── the control causes the refusal ──────────────────────────────────────────

@pytest.mark.parametrize("fn", [tools.run_spar, tools.run_sparring])
def test_mcp_verify_off_refused_because_of_the_minimum(policy, mcp_stubbed, fn):
    policy("gate")
    out = fn("ship it?", verify="off")
    assert out["ok"] is False
    assert out["refused_by"] == "operator_minimum"
    assert "below the operator minimum" in out["error"]


@pytest.mark.parametrize("fn", [tools.run_spar, tools.run_sparring])
def test_control_same_call_without_minimum_is_not_refused(policy, mcp_stubbed, fn):
    with pytest.raises(_Reached) as hit:  # no policy file: the call goes through
        fn("ship it?", verify="off")
    assert str(hit.value) == "off"


@pytest.mark.parametrize("mode", ["gate", "round"])
def test_gate_and_round_meet_a_gate_minimum(policy, mcp_stubbed, mode):
    policy("gate")
    with pytest.raises(_Reached):
        tools.run_spar("ship it?", verify=mode)


def test_cli_verify_off_refused_and_control(policy, monkeypatch, capsys):
    from rapier import cli, onboarding

    monkeypatch.setattr(onboarding, "preflight_error", lambda: None)

    def reached(*a, **k):
        raise _Reached(k.get("verify"))

    monkeypatch.setattr(cli, "load_preset", reached)
    with pytest.raises(_Reached):  # control: no minimum
        cli.main(["spar", "--verify", "off", "--request", "ship it?"])
    policy("gate")
    assert cli.main(["spar", "--verify", "off", "--request", "ship it?"]) == 2
    assert "below the operator minimum" in capsys.readouterr().err


# ── the minimum's source is out of the caller's reach ───────────────────────

_ALLOWED_TOOL_PARAMS = {"request", "settle", "verify", "report_all", "seed", "depth",
                        "frame", "log", "cancel", "ledger_root",
                        "max_usd"}  # a spending cap: it can only lower spend; RAPIER_MAX_USD is a ceiling


def test_no_mcp_tool_argument_reaches_the_policy():
    """Every parameter of every tool entry point is a known knob; none names the
    policy, its path or the minimum. A new parameter fails this test until it is
    reviewed."""
    for fn in (tools.run_spar, tools.run_sparring, tools.run_proposer, tools.run_frame):
        params = set(inspect.signature(fn).parameters)
        assert params <= _ALLOWED_TOOL_PARAMS, (fn.__name__, params - _ALLOWED_TOOL_PARAMS)


def test_policy_path_is_not_taken_from_the_environment(monkeypatch):
    """A path the caller can redirect is a floor the caller can remove."""
    for name in ("RAPIER_POLICY", "RAPIER_POLICY_FILE", "RAPIER_POLICY_PATH"):
        monkeypatch.setenv(name, "/tmp/caller-controlled.json")
    assert "caller-controlled" not in OP._default_policy_path()
    src = inspect.getsource(OP)
    reads = {ln.strip() for ln in src.splitlines() if "os.environ" in ln}
    assert all(("ENV_VAR" in r) or ("PROGRAMDATA" in r) for r in reads), reads


def test_env_minimum_applies_but_is_reported_within_reach(policy, monkeypatch):
    monkeypatch.setenv(OP.ENV_VAR, "gate")
    assert operator_minimum() == ("gate", OP.ENV_VAR, False)
    with pytest.raises(PolicyError, match="within the caller's reach"):
        decide_verify("off", "caller (MCP tool argument)")


def test_lower_env_value_cannot_undercut_the_file(policy, monkeypatch):
    policy("gate")
    monkeypatch.setenv(OP.ENV_VAR, "off")
    with pytest.raises(PolicyError):
        decide_verify("off", "caller")


def test_writable_policy_file_is_not_external(policy):
    policy("gate")
    assert operator_minimum()[2] is False


def test_read_only_file_i_own_is_not_external(policy):
    """Verifier R1-2: modes are not authority. A 0444 file in a 0555 directory,
    both owned by this process, can be chmodded back and rewritten, so it is
    within reach."""
    path = policy("gate")
    os.chmod(path, 0o444)
    os.chmod(path.parent, 0o555)
    try:
        assert operator_minimum()[2] is False
        with pytest.raises(PolicyError, match="within the caller's reach"):
            decide_verify("off", "caller")
    finally:
        os.chmod(path.parent, 0o755)
        os.chmod(path, 0o644)


def test_external_when_nothing_on_the_path_is_mine(policy, monkeypatch):
    """The positive case can't be set up unprivileged (it needs a file owned by
    another user, e.g. root), so ownership and writability are simulated here
    by patching os.stat's st_uid and os.access."""
    path = policy("gate")
    real_stat = os.stat
    me = os.geteuid()

    class _St:
        def __init__(self, st):
            self._st = st
            self.st_uid = me + 1  # someone else owns every component

        def __getattr__(self, name):
            return getattr(self._st, name)

    monkeypatch.setattr(OP.os, "stat", lambda p, *a, **k: _St(real_stat(p, *a, **k)))
    monkeypatch.setattr(OP.os, "access", lambda p, mode: mode != os.W_OK)
    level, _src, external = operator_minimum()
    assert (level, external) == ("gate", True)
    with pytest.raises(PolicyError, match="out of the caller's reach"):
        decide_verify("off", "caller")


def test_one_owned_ancestor_is_enough_to_be_in_reach(policy, monkeypatch):
    path = policy("gate")
    real_stat = os.stat
    me = os.geteuid()
    mine = str(path.parent.parent)

    class _St:
        def __init__(self, p, st):
            self._st = st
            self.st_uid = me if os.path.abspath(p) == mine else me + 1

        def __getattr__(self, name):
            return getattr(self._st, name)

    monkeypatch.setattr(OP.os, "stat", lambda p, *a, **k: _St(p, real_stat(p, *a, **k)))
    monkeypatch.setattr(OP.os, "access", lambda p, mode: mode != os.W_OK)
    assert operator_minimum()[2] is False


def test_root_is_never_external(policy, monkeypatch):
    policy("gate")
    monkeypatch.setattr(OP.os, "geteuid", lambda: 0)
    assert operator_minimum()[2] is False


def test_unsearchable_policy_dir_fails_closed(policy, mcp_stubbed):
    """Verifier R1-1: os.path.exists() is False on EACCES too, so a locked
    directory used to read as 'no policy' and the run went ahead unfloored."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can search any directory")
    with pytest.raises(_Reached):  # control: no file at all is allowed
        tools.run_spar("ship it?", verify="off")
    path = policy("gate")
    os.chmod(path.parent, 0o000)
    try:
        if os.access(path.parent, os.X_OK):
            pytest.skip("this filesystem ignores chmod")
        out = tools.run_spar("ship it?", verify="off")
        # R4-3: the minimum was never read, so the stop holds but is not credited to it
        assert out["ok"] is False and out["final_state"] == "defer"
        assert "refused_by" not in out and out["operator_policy"].startswith("did-not-run: ")
        assert "could not be checked" in out["error"]
        with pytest.raises(PolicyError):
            decide_verify("gate", "caller")  # can't know the floor: refuse every run
    finally:
        os.chmod(path.parent, 0o755)


@pytest.mark.parametrize("body", ["not json", "{}", '{"min_verify": "loud"}'])
def test_bad_policy_file_fails_closed(policy, mcp_stubbed, body):
    import pathlib

    pathlib.Path(OP.POLICY_PATH).write_text(body)
    out = tools.run_spar("ship it?", verify="gate")
    # R4-3: the minimum was never read, so the stop holds but is not credited to it
    assert out["ok"] is False and out["final_state"] == "defer"
    assert "refused_by" not in out and out["operator_policy"].startswith("did-not-run: ")


# ── the report says who set the level, and whether the minimum is external ──

def _env(**meta):
    env = Envelope(request="p", recommendation="do Y")
    env.meta.update(meta)
    return env


def test_report_names_who_switched_the_check_off():
    env = _env(verify="off", verify_set_by="caller (MCP tool argument)", verify_minimum=None)
    report = " ".join(_render_report(env).split())
    assert "switched off, by caller (MCP tool argument)" in report
    assert "No operator minimum was set" in report
    out = ComposeStage().run(env, StageContext())
    assert out.trust_rider["citation_check"] == (
        "did-not-run: switched off by caller (MCP tool argument) (verify=off)")
    row = out.meta["ceremony_row"]
    assert row["verify_mode"] == "off" and row["verify_set_by"] == "caller (MCP tool argument)"


def test_report_states_an_external_minimum():
    env = _env(verify="gate", verify_set_by="default", verify_minimum="gate",
               verify_minimum_source="/etc/rapier/policy.json", verify_minimum_external=True)
    report = " ".join(_render_report(env).split())
    assert "calling agent cannot change" in report
    row = ComposeStage().run(env, StageContext()).meta["ceremony_row"]
    assert row["verify_minimum"] == "gate" and row["verify_minimum_external"] is True


def test_report_says_an_in_reach_minimum_is_not_independent():
    env = _env(verify="gate", verify_set_by="default", verify_minimum="gate",
               verify_minimum_source="RAPIER_MIN_VERIFY", verify_minimum_external=False)
    assert "not an independent control" in " ".join(_render_report(env).split())


# ── R4-3: a stop is credited to a control only when that control decided ────

@pytest.mark.parametrize("body", ['{"min_verify":', '{"min_verify": "loud"}', "{}",
                                  '{"require_independent": "yes"}'])
def test_unreadable_policy_stops_as_defer_not_refuse(policy, mcp_stubbed, body):
    import pathlib

    pathlib.Path(OP.POLICY_PATH).write_text(body)
    out = tools.run_spar("decide X", verify="gate")
    assert out["ok"] is False
    assert out["final_state"] == "defer"
    assert "refused_by" not in out
    assert out["operator_policy"].startswith("did-not-run: ")


def test_control_readable_minimum_refuses_and_is_credited(policy, mcp_stubbed):
    policy("gate")
    out = tools.run_spar("decide X", verify="off")
    assert out["final_state"] == "refuse" and out["refused_by"] == "operator_minimum"
    assert "operator_policy" not in out


def test_cli_unreadable_policy_says_defer(policy, monkeypatch, capsys):
    import pathlib

    from rapier import cli, onboarding

    monkeypatch.setattr(onboarding, "preflight_error", lambda: None)
    pathlib.Path(OP.POLICY_PATH).write_text('{"min_verify":')
    assert cli.main(["spar", "--request", "x"]) == 2
    err = capsys.readouterr().err
    assert "operator policy did not run, final state defer" in err and "refused:" not in err


def test_unreadable_policy_is_a_policy_error_subclass():
    assert issubclass(OP.PolicyUnreadable, OP.PolicyError)
