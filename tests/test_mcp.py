"""MCP-0 — tool logic (no SDK needed) + graceful behaviour when the extra is absent.

The FastMCP wiring itself is exercised only when the optional ``mcp`` extra is
installed; these tests lock in the parts that must hold regardless.
"""
from __future__ import annotations

from rapier import cli
from rapier.mcp import server, tools
from rapier.models import vendor_key_envs


def _clear_all_keys(monkeypatch):
    for env in vendor_key_envs().values():
        monkeypatch.delenv(env, raising=False)


def test_run_spar_blocks_without_keys(monkeypatch):
    _clear_all_keys(monkeypatch)
    out = tools.run_spar("should we ship?")
    assert out["ok"] is False
    assert "No AI vendor keys" in out["error"]


def test_run_sparring_blocks_without_keys(monkeypatch):
    _clear_all_keys(monkeypatch)
    out = tools.run_sparring("monorepo or polyrepo?")
    assert out["ok"] is False


def test_doctor_tool_shape(monkeypatch):
    _clear_all_keys(monkeypatch)
    out = tools.doctor()
    assert "report" in out and "configured_vendors" in out
    assert isinstance(out["configured_vendors"], list)
    assert "mock" not in out["configured_vendors"]


def test_serve_reports_missing_sdk(monkeypatch):
    def _raise():
        raise ImportError("no mcp")

    monkeypatch.setattr(server, "build_server", _raise)
    assert server.serve() == 1  # graceful, actionable exit — no traceback


def test_cli_mcp_is_wired(monkeypatch):
    import rapier.mcp as mcp_pkg

    monkeypatch.setattr(mcp_pkg, "serve", lambda: 0)  # don't launch a real server
    assert cli.main(["mcp"]) == 0
