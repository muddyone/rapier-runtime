"""MCP tool logic — pure functions the MCP server wraps.

No dependency on the ``mcp`` SDK, so this is testable without the extra installed
and reusable outside MCP. Each function runs a preset through the engine and
returns a structured result: a human-readable ``report_md`` plus machine fields
(verdict, grounding, cross-vendor, standing objections). Honest first: if no
vendor key is configured, it returns ``{"ok": False, "error": …}`` rather than an
empty run.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from ..onboarding import configured_vendors, doctor_report, preflight_error
from ..presets import load_preset


def _result(env, report_all: bool) -> dict[str, Any]:
    report = env.meta.get("report_md") or env.recommendation or ""
    proposer_md = env.meta.get("proposer_report_md")
    if report_all and proposer_md:
        report = f"{proposer_md}\n\n---\n\n{report}"
    gate = env.meta.get("citation_gate") or {}
    review = env.meta.get("review") or {}
    cut = (env.meta.get("proposer") or {}).get("cut") or {}
    cancelled = any(t.kind == "control" and "cancelled" in t.summary for t in env.trace)
    return {
        "ok": True,
        "cancelled": cancelled,
        "run_id": env.meta.get("run_id"),
        "report_md": report,
        "verdict": env.verdict,
        "grounding": (
            {
                "gate": gate.get("gate"),
                "grounding_rate": gate.get("grounding_rate"),
                "counts": gate.get("counts"),
            }
            if gate
            else None
        ),
        "cross_vendor": review.get("cross_vendor"),
        "author_vendor": env.meta.get("author_vendor"),
        "reviewer_vendor": review.get("reviewer_vendor"),
        "standing_objections": cut.get("standing_objections") or [],
    }


def _run(
    name: str,
    request: str,
    settle: int,
    verify: str,
    report_all: bool,
    log: Callable[[str], None] | None,
    cancel: Callable[[], bool] | None = None,
    ledger_root: str | None = None,
) -> dict[str, Any]:
    err = preflight_error()
    if err:
        return {"ok": False, "error": err}
    preset = load_preset(name, settle=settle, verify=verify)
    env = preset.build().run(
        request, ledger_root=ledger_root, log=log or (lambda _m: None), cancel=cancel
    )
    return _result(env, report_all)


def run_spar(
    request: str, settle: int = 0, verify: str = "gate",
    log: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    ledger_root: str | None = None,
) -> dict[str, Any]:
    return _run("spar", request, settle, verify, False, log, cancel, ledger_root)


def run_sparring(
    request: str, settle: int = 0, verify: str = "gate", report_all: bool = False,
    log: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    ledger_root: str | None = None,
) -> dict[str, Any]:
    return _run("sparring", request, settle, verify, report_all, log, cancel, ledger_root)


def doctor() -> dict[str, Any]:
    return {"report": doctor_report(), "configured_vendors": configured_vendors()}


def _safe_run_id(run_id: str) -> bool:
    """Reject path-traversal / separators — a run id is a single dir name."""
    return bool(run_id) and os.sep not in run_id and "/" not in run_id and ".." not in run_id


def list_runs(ledger_root: str | None) -> dict[str, Any]:
    """List persisted run ids under the server's ledger dir (newest last)."""
    if not ledger_root or not os.path.isdir(ledger_root):
        return {"ok": False, "error": "run persistence is not enabled (set RAPIER_MCP_LEDGER)"}
    runs = sorted(
        d for d in os.listdir(ledger_root) if os.path.isdir(os.path.join(ledger_root, d))
    )
    return {"ok": True, "runs": runs}


def get_run(ledger_root: str | None, run_id: str) -> dict[str, Any]:
    """Return a persisted run's report + verdict by id (from its envelope.json)."""
    if not ledger_root or not os.path.isdir(ledger_root):
        return {"ok": False, "error": "run persistence is not enabled (set RAPIER_MCP_LEDGER)"}
    if not _safe_run_id(run_id):
        return {"ok": False, "error": "invalid run id"}
    path = os.path.join(ledger_root, run_id, "envelope.json")
    if not os.path.isfile(path):
        return {"ok": False, "error": f"run '{run_id}' not found"}
    with open(path, encoding="utf-8") as fh:
        env = json.load(fh)
    meta = env.get("meta") or {}
    return {
        "ok": True,
        "run_id": run_id,
        "report_md": meta.get("report_md") or env.get("recommendation") or "",
        "verdict": env.get("verdict"),
    }
