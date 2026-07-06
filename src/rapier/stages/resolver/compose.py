"""Compose stage — the two-part output + records.

Assembles the final report (recommendation + trust rider), where the rider's
"contested" section carries both the cross-vendor reviewer's objections AND the
Proposer's forwarded standing objections (so the deliberation's dissent is
visible to the user). Writes the /spar-parity named files and the derived
ceremony-ledger row.
"""
from __future__ import annotations

import json
import os

from ...envelope import Envelope
from ...secrets import redact_obj
from ...stage import StageContext, TransformStage, register_stage


def _texts(objs) -> list[str]:
    return [o.get("text", "") for o in (objs or []) if isinstance(o, dict)]


def _render_report(env: Envelope) -> str:
    review = env.meta.get("review") or {}
    gate = env.meta.get("definitiveness") or {}
    rider = env.trust_rider or {}
    lines = [f"# Rapier report: {env.request[:80]}", ""]
    lines += [f"**Answer verdict**: {env.verdict}", ""]
    lines += ["## Recommendation", "", env.recommendation or "(none)", ""]
    lines += ["## Trust rider", ""]
    if rider.get("assumptions_to_verify"):
        lines += ["**Assumptions to verify against your context:**"]
        lines += [f"- {a}" for a in rider["assumptions_to_verify"]] + [""]
    contested = _texts(review.get("objections"))
    if contested:
        lines += ["**Contested and resolved (cross-vendor review):**"]
        lines += [f"- {c}" for c in contested] + [""]
    if rider.get("proposer_dissent_forwarded"):
        lines += ["**Standing objections from the deliberation (forwarded, weigh these):**"]
        lines += [f"- {c}" for c in rider["proposer_dissent_forwarded"]] + [""]
    lines += [
        "**Overall confidence:** "
        f"gate={env.verdict}; review cross_vendor={review.get('cross_vendor')}; "
        f"gate cross_vendor={gate.get('cross_vendor')}.",
        "",
    ]
    return "\n".join(lines)


def _ceremony_row(env: Envelope) -> dict:
    """Derive the /spar-schema 'did the Challenger matter' row MECHANICALLY from
    the run (no self-coding) — more honest than a model grading its own work."""
    review = env.meta.get("review") or {}
    gate = env.meta.get("definitiveness") or {}
    citation = env.meta.get("citation_gate") or {}
    objections = review.get("objections") or []
    before = env.meta.get("recommendation_before_fix")
    changed = bool(before is not None and before != env.recommendation)
    surfaced = bool(objections)
    load_bearing = changed or surfaced
    return {
        "skill": "rapier",
        "sparring_version": "v2",
        "mode": "full-ceremony" if env.committed else "one-pass",
        "reviewer_vendor": review.get("reviewer_vendor"),
        "cross_vendor": bool(review.get("cross_vendor")),
        "topic": env.request[:120],
        "converged": env.verdict == "PASS",
        "challenger_changed_recommendation": changed,
        "what_changed": "recommendation revised under review" if changed else "",
        "challenger_surfaced_error_or_risk": surfaced,
        "what_surfaced": "; ".join(_texts(objections))[:400],
        "load_bearing": load_bearing,
        "verdict": "MATTERED" if load_bearing else "DID_NOT_MATTER",
        "answer_verdict": env.verdict or "unchecked",
        "gate": citation.get("gate", ""),
        "grounding_rate": citation.get("grounding_rate"),
        "theater_flags": citation.get("theater_flags", 0),
    }


@register_stage("compose")
class ComposeStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        review = env.meta.get("review") or {}
        gate = env.meta.get("definitiveness") or {}
        standing = ((env.meta.get("proposer") or {}).get("cut") or {}).get("standing_objections") or []

        rider = dict(env.trust_rider or {})
        rider.setdefault("contested_and_resolved", _texts(review.get("objections")))
        if standing:
            rider["proposer_dissent_forwarded"] = _texts(standing)
        rider["overall_confidence"] = env.verdict
        env.trust_rider = rider

        env.meta["report"] = {
            "recommendation": env.recommendation,
            "trust_rider": rider,
            "answer_verdict": env.verdict,
            "citation_gate": env.meta.get("citation_gate"),
        }
        report_md = _render_report(env)
        env.meta["report_md"] = report_md

        # /spar-parity named files + the ceremony-ledger row.
        row = _ceremony_row(env)
        env.meta["ceremony_row"] = row
        if ctx.ledger is not None:
            ctx.ledger.write_text("report.md", report_md)
            ctx.ledger.write_text("recommendation.md", env.recommendation or "")
            ctx.ledger.write_text("pack.md", env.request)
            if review:
                ctx.ledger.write_json("review.json", review)
            if gate:
                ctx.ledger.write_json("definitiveness.json", gate)
            ctx.ledger.write_json("ceremony.json", row)
        _append_corpus_ledger(row)

        env.add_trace("compose", self.kind, f"report composed; verdict={env.verdict} load_bearing={row['load_bearing']}")
        return env


def _append_corpus_ledger(row: dict) -> None:
    """Append the ceremony row to the shared corpus so runs accrue with /spar's.

    Path from RAPIER_CEREMONY_LEDGER (default the /spar global corpus
    ~/.claude/spar-ledger.jsonl). Set it empty to disable. Fail-soft.
    """
    default = os.path.join(os.path.expanduser("~"), ".claude", "spar-ledger.jsonl")
    path = os.environ.get("RAPIER_CEREMONY_LEDGER", default)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(redact_obj(row), default=str) + "\n")
    except OSError:
        pass  # never break a run on a ledger write
