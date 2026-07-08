"""First-run onboarding — key detection, doctor, init. No network, no real keys.

All secret-value handling stays env-only and never surfaces a value; these tests
lock that in alongside the behaviour.
"""
from __future__ import annotations

import pathlib

from rapier import cli, onboarding
from rapier.models import vendor_key_envs


def _clear_all_keys(monkeypatch):
    for env in vendor_key_envs().values():
        monkeypatch.delenv(env, raising=False)


def test_preflight_error_when_no_keys(monkeypatch):
    _clear_all_keys(monkeypatch)
    msg = onboarding.preflight_error()
    assert msg is not None
    assert "No AI vendor keys" in msg
    assert "ANTHROPIC_API_KEY" in msg  # names the frontier envs


def test_preflight_none_when_a_key_is_present(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    assert onboarding.preflight_error() is None


def test_configured_vendors_excludes_mock(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "y" * 24)
    vendors = onboarding.configured_vendors()
    assert "mock" not in vendors
    assert "openai" in vendors


def test_doctor_reports_presence_never_values(monkeypatch):
    _clear_all_keys(monkeypatch)
    secret = "sk-ant-" + "z" * 30
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    report = onboarding.doctor_report()
    assert "ANTHROPIC_API_KEY" in report
    assert secret not in report  # never prints the value


def test_doctor_flags_single_vendor_as_same_vendor(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "a" * 24)
    assert "same-vendor" in onboarding.doctor_report()


def test_doctor_reports_cross_vendor_with_two(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "a" * 24)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "b" * 24)
    assert "cross-vendor review is available" in onboarding.doctor_report()


def test_init_writes_env_example_without_values(tmp_path):
    path, created, instructions = onboarding.init(str(tmp_path))
    assert created is True
    body = pathlib.Path(path).read_text()
    assert "ANTHROPIC_API_KEY=" in body
    assert "source .env" in instructions
    for line in body.splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            # placeholder only — nothing before the trailing comment
            assert line.split("=", 1)[1].split("#")[0].strip() == ""


def test_init_does_not_overwrite_existing(tmp_path):
    (tmp_path / ".env.example").write_text("PRE-EXISTING")
    path, created, _ = onboarding.init(str(tmp_path))
    assert created is False
    assert pathlib.Path(path).read_text() == "PRE-EXISTING"


def test_cli_spar_blocks_without_keys(monkeypatch):
    _clear_all_keys(monkeypatch)
    assert cli.main(["spar", "--request", "x"]) == 2  # preflight, non-zero, no run


def test_cli_doctor_and_init_run(monkeypatch, tmp_path):
    _clear_all_keys(monkeypatch)
    assert cli.main(["doctor"]) == 0
    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / ".env.example").exists()
