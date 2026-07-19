"""Governance default: every run leaves a durable record, and the report says so.

These exercise the persistence policy (default location, override, opt-out) and
the THE RECORD provenance section without any network/keys — the record body and
render are pure functions of the envelope.
"""
from __future__ import annotations

import os

from rapier.envelope import Envelope
from rapier.ledger import default_runs_root, persistence_disabled
from rapier.stages.resolver.compose import _record_body, _render_report


# ── default location + override ───────────────────────────────────────────────

def test_default_runs_root_is_under_home_rapier(monkeypatch):
    monkeypatch.delenv("RAPIER_RUNS_DIR", raising=False)
    root = default_runs_root()
    assert root == os.path.join(os.path.expanduser("~"), ".rapier", "runs")


def test_runs_dir_env_overrides_default(monkeypatch):
    monkeypatch.setenv("RAPIER_RUNS_DIR", "/mnt/governed/rapier")
    assert default_runs_root() == "/mnt/governed/rapier"


def test_runs_dir_env_expands_user(monkeypatch):
    monkeypatch.setenv("RAPIER_RUNS_DIR", "~/audit/rapier")
    assert default_runs_root() == os.path.join(os.path.expanduser("~"), "audit", "rapier")


# ── opt-out ───────────────────────────────────────────────────────────────────

def test_persistence_on_by_default(monkeypatch):
    monkeypatch.delenv("RAPIER_NO_PERSIST", raising=False)
    assert persistence_disabled() is False


def test_no_persist_env_disables(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("RAPIER_NO_PERSIST", truthy)
        assert persistence_disabled() is True
    for falsy in ("0", "false", "", "no"):
        monkeypatch.setenv("RAPIER_NO_PERSIST", falsy)
        assert persistence_disabled() is False


# ── THE RECORD provenance body ────────────────────────────────────────────────

def test_record_body_names_the_path_when_persisted():
    env = Envelope(request="q")
    env.meta["run_dir"] = "/home/x/.rapier/runs/20260101000000-spar"
    body = _record_body(env)
    assert "/home/x/.rapier/runs/20260101000000-spar" in body
    assert "captured verbatim" in body
    assert "audit" in body


def test_record_body_states_plainly_when_not_persisted():
    env = Envelope(request="q")  # no run_dir on meta
    body = _record_body(env)
    assert "No durable record" in body
    assert "no audit trail" in body
    assert ".rapier" not in body and "runs/" not in body  # never invents a path


def test_record_section_appears_in_the_rendered_report():
    env = Envelope(request="Should we ship?", recommendation="Ship it.", verdict="PASS")
    env.meta["run_dir"] = "/home/x/.rapier/runs/20260101000000-spar"
    report = _render_report(env)
    assert "THE RECORD" in report
    assert "/home/x/.rapier/runs/20260101000000-spar" in report


# ── CLI wiring: --no-save reaches _run (no network) ───────────────────────────

def test_no_save_flag_flows_to_run(monkeypatch):
    from rapier.cli import main

    monkeypatch.setattr("rapier.onboarding.preflight_error", lambda: None)
    captured = {}

    def fake_run(manifest, request, ledger_dir, report_all=False, seed_meta=None, no_save=False):
        captured["no_save"] = no_save
        return 0

    monkeypatch.setattr("rapier.cli._run", fake_run)
    assert main(["spar", "--request", "x", "--no-save"]) == 0
    assert captured["no_save"] is True


def test_persistence_on_by_default_from_cli(monkeypatch):
    from rapier.cli import main

    monkeypatch.setattr("rapier.onboarding.preflight_error", lambda: None)
    captured = {}

    def fake_run(manifest, request, ledger_dir, report_all=False, seed_meta=None, no_save=False):
        captured["no_save"] = no_save
        return 0

    monkeypatch.setattr("rapier.cli._run", fake_run)
    assert main(["spar", "--request", "x"]) == 0
    assert captured["no_save"] is False


# ── MCP tool behavior under the new default ───────────────────────────────────

def test_list_runs_reports_disabled_when_root_is_none():
    from rapier.mcp.tools import list_runs

    out = list_runs(None)
    assert out["ok"] is False
    assert "RAPIER_NO_PERSIST" in out["error"]


def test_list_runs_empty_when_no_runs_yet(tmp_path):
    from rapier.mcp.tools import list_runs

    out = list_runs(str(tmp_path / "does-not-exist-yet"))
    assert out["ok"] is True
    assert out["runs"] == []
