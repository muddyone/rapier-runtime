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


# --- MCP-1: progress streaming + tool registration (need the SDK/anyio) ---

def test_run_with_progress_streams_stage_progress():
    import pytest

    anyio = pytest.importorskip("anyio")

    class _Ctx:
        def __init__(self):
            self.infos: list[str] = []
            self.progress: list[tuple] = []

        async def info(self, m):
            self.infos.append(m)

        async def report_progress(self, done, total=None):
            self.progress.append((done, total))

    def _fn(log=None, **kw):
        log("stage: author (transform)")
        log("  vendor substitution: author anthropic->openai (no anthropic key)")
        log("stage: compose (transform)")
        return {"ok": True, "report_md": "R"}

    ctx = _Ctx()
    out = anyio.run(server._run_with_progress, _fn, ctx, 2)
    assert out == {"ok": True, "report_md": "R"}
    assert ctx.progress == [(1, 2), (2, 2)]  # one tick per 'stage:' line
    assert any("substitution" in m for m in ctx.infos)  # every line streamed as info


def test_run_with_progress_tolerates_no_ctx():
    import pytest

    anyio = pytest.importorskip("anyio")

    def _fn(log=None, **kw):
        log("stage: author (transform)")
        return {"ok": True}

    assert anyio.run(server._run_with_progress, _fn, None, 1) == {"ok": True}


def test_build_server_registers_all_tools():
    import pytest

    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    srv = server.build_server()
    names = {t.name for t in anyio.run(srv.list_tools)}
    assert {"spar", "sparring", "rapier_doctor"} <= names
