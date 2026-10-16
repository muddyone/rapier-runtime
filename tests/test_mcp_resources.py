"""Saved runs as MCP resources: rapier://runs, rapier://runs/{id}, rapier://runs/{id}/record.

The index/report/record logic is plain Python and always runs. The protocol test
needs the optional ``mcp`` extra and is skipped without it.
"""
from __future__ import annotations

import json
import os

import pytest

from rapier.mcp import tools


def _save_run(root, run_id, request="Should we ship?", verdict="PASS", report="REPORT TEXT"):
    d = os.path.join(root, run_id)
    os.makedirs(d)
    with open(os.path.join(d, "envelope.json"), "w", encoding="utf-8") as fh:
        json.dump({"request": request, "verdict": verdict,
                   "recommendation": "do it", "meta": {"report_md": report}}, fh)


@pytest.fixture
def ledger(tmp_path):
    root = str(tmp_path / "runs")
    os.makedirs(root)
    _save_run(root, "20261016095019-frame", request="Is this a question?\nsecond line",
              verdict=None, report="FRAME REPORT")
    _save_run(root, "20261016095044-spar", report="SPAR REPORT")
    os.makedirs(os.path.join(root, "20261016100000-spar"))  # in progress: no envelope yet
    return root


def test_index_lists_runs_newest_first_and_skips_unfinished(ledger):
    out = tools.run_index(ledger)
    assert out["ok"] is True
    assert [r["run_id"] for r in out["runs"]] == ["20261016095044-spar", "20261016095019-frame"]
    spar, frame = out["runs"]
    assert spar["kind"] == "spar" and spar["verdict"] == "PASS"
    assert frame["topic"] == "Is this a question?"
    assert spar["report_uri"] == "rapier://runs/20261016095044-spar"
    assert spar["record_uri"] == "rapier://runs/20261016095044-spar/record"


def test_index_when_persistence_is_off_or_nothing_saved(tmp_path):
    assert tools.run_index(None)["ok"] is False
    assert tools.run_index(str(tmp_path / "absent"))["runs"] == []


def test_report_and_record_read_the_saved_run(ledger):
    assert tools.run_report(ledger, "20261016095044-spar") == "SPAR REPORT"
    assert tools.run_record(ledger, "20261016095044-spar")["verdict"] == "PASS"


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "nope-spar"])
def test_report_rejects_traversal_and_unknown_ids(ledger, bad):
    with pytest.raises(LookupError):
        tools.run_report(ledger, bad)


def test_resources_over_the_protocol(ledger, monkeypatch):
    pytest.importorskip("mcp.server.fastmcp")
    anyio = pytest.importorskip("anyio")
    from mcp.shared.memory import create_connected_server_and_client_session

    from rapier.mcp.server import build_server

    monkeypatch.delenv("RAPIER_NO_PERSIST", raising=False)
    monkeypatch.setenv("RAPIER_MCP_LEDGER", ledger)
    server = build_server()

    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            listed = {str(r.uri): r for r in (await client.list_resources()).resources}
            templates = {t.uriTemplate for t in
                         (await client.list_resource_templates()).resourceTemplates}

            # A run saved after the server started still shows up in the next listing.
            _save_run(ledger, "20261016110000-sparring", request="Later run")
            relisted = {str(r.uri) for r in (await client.list_resources()).resources}

            index = json.loads((await client.read_resource("rapier://runs")).contents[0].text)
            report = (await client.read_resource("rapier://runs/20261016095044-spar")).contents[0]
            record = (await client.read_resource(
                "rapier://runs/20261016095044-spar/record")).contents[0]
            return listed, templates, relisted, index, report, record

    listed, templates, relisted, index, report, record = anyio.run(go)

    assert "rapier://runs" in listed
    assert listed["rapier://runs/20261016095044-spar"].mimeType == "text/plain"
    assert "rapier://runs/20261016100000-spar" not in listed  # unfinished run not listed
    assert {"rapier://runs/{run_id}", "rapier://runs/{run_id}/record"} <= templates
    assert "rapier://runs/20261016110000-sparring" in relisted
    assert [r["run_id"] for r in index["runs"]][-1] == "20261016095019-frame"
    assert report.text == "SPAR REPORT"
    assert json.loads(record.text)["verdict"] == "PASS"
