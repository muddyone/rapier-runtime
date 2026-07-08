"""MCP tool logic — pure functions the MCP server wraps.

No dependency on the ``mcp`` SDK, so this is testable without the extra installed
and reusable outside MCP. Each function runs a preset through the engine and
returns a structured result: a human-readable ``report_md`` plus machine fields
(verdict, grounding, cross-vendor, standing objections). Honest first: if no
vendor key is configured, it returns ``{"ok": False, "error": …}`` rather than an
empty run.
"""
from __future__ import annotations

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
    return {
        "ok": True,
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
) -> dict[str, Any]:
    err = preflight_error()
    if err:
        return {"ok": False, "error": err}
    preset = load_preset(name, settle=settle, verify=verify)
    env = preset.build().run(request, log=log or (lambda _m: None))
    return _result(env, report_all)


def run_spar(
    request: str, settle: int = 0, verify: str = "gate",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    return _run("spar", request, settle, verify, False, log)


def run_sparring(
    request: str, settle: int = 0, verify: str = "gate", report_all: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    return _run("sparring", request, settle, verify, report_all, log)


def doctor() -> dict[str, Any]:
    return {"report": doctor_report(), "configured_vendors": configured_vendors()}
