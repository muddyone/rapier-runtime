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
    assert {"frame", "proposer", "spar", "sparring",
            "rapier_doctor", "list_runs", "get_run"} <= names


def test_build_server_advertises_rapier_version_not_sdk():
    import pytest

    pytest.importorskip("mcp")
    from rapier import __version__

    srv = server.build_server()
    # The initialize handshake must report rapier's version, not the mcp SDK's.
    assert srv._mcp_server.version == __version__


# --- MCP-2: timeout + ledger-run access ---

def test_run_with_progress_times_out():
    import time

    import pytest

    anyio = pytest.importorskip("anyio")

    def _slow(log=None, cancel=None, ledger_root=None, **kw):
        for _ in range(200):
            if cancel and cancel():
                return {"ok": True, "stopped": True}
            time.sleep(0.02)
        return {"ok": True, "stopped": False}

    out = anyio.run(server._run_with_progress, _slow, None, 1, 0.1)  # 0.1s timeout
    assert out["ok"] is False
    assert "timed out" in out["error"]


def test_list_and_get_run_roundtrip(tmp_path):
    import json
    import os

    from rapier.mcp import tools

    rid = "run-1"
    os.makedirs(tmp_path / rid)
    (tmp_path / rid / "envelope.json").write_text(
        json.dumps({"recommendation": "REC", "verdict": "PASS", "meta": {"report_md": "# Report"}})
    )
    lr = str(tmp_path)

    listing = tools.list_runs(lr)
    assert listing["ok"] and "run-1" in listing["runs"]

    got = tools.get_run(lr, "run-1")
    assert got["ok"] and got["report_md"] == "# Report" and got["verdict"] == "PASS"


def test_get_run_rejects_path_traversal(tmp_path):
    from rapier.mcp import tools

    out = tools.get_run(str(tmp_path), "../secrets")
    assert out["ok"] is False and "invalid" in out["error"]


def test_runs_disabled_without_ledger():
    from rapier.mcp import tools

    assert tools.list_runs(None)["ok"] is False
    assert tools.get_run(None, "x")["ok"] is False
