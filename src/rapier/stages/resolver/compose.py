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


def _verdict_sentence(verdict, gate) -> str:
    """The correctness (definitiveness) gate verdict in plain words — no shorthand."""
    fails = len(gate.get("failures") or [])
    v = (verdict or "unchecked").upper()
    if v == "PASS":
        return ("It passed the correctness check: every hard specific it states either traces "
                "to a fact you gave or is flagged as an estimate to verify.")
    if v == "FAIL":
        noun = "figure is" if fails == 1 else "figures are"
        return (f"It did NOT pass the correctness check: {fails or 'one or more'} stated {noun} "
                "asserted as fact without tracing to your givens — treat those as unverified until you check them.")
    if v == "REVIEW":
        return ("It needs your call on the correctness check: at least one stated specific reads "
                "ambiguously between fact and estimate — see WHAT TO CHECK below.")
    return ("The correctness check could not return a verdict here (there were no hard specifics "
            "to check, or the checker was unavailable), so treat the stated specifics as unverified.")


def _reviewer_sentence(review) -> str:
    """Whether the challenge was genuinely independent — in plain words."""
    if review.get("cross_vendor"):
        v = review.get("reviewer_vendor") or "a different vendor"
        return (f"An independent reviewer from a different vendor ({v}) pressure-tested it, so the "
                "challenge was genuinely cross-vendor — not the same model checking its own work.")
    return ("The reviewer ran on the same vendor as the author (no second vendor's key was available), "
            "so this challenge was NOT independent — weigh it with that in mind.")


def _render_report(env: Envelope) -> str:
    review = env.meta.get("review") or {}
    gate = env.meta.get("definitiveness") or {}
    rider = env.trust_rider or {}
    topic = (env.request or "").strip().splitlines()[0][:100] if env.request else ""

    L = ["# RAPIER — RESOLVER REPORT", ""]
    if topic:
        L += [f"*On: {topic}*", ""]

    L += ["## SUMMARY", "*The bottom line — how far to trust this, in one breath.*", "",
          _verdict_sentence(env.verdict, gate) + " The full advice is under THE RECOMMENDATION below.", ""]

    L += ["## THE RECOMMENDATION", "*The answer on the merits — read this as the actual advice.*", "",
          env.recommendation or "(none produced)", ""]

    L += ["## WHAT TO CHECK AGAINST YOUR SITUATION",
          "*Figures that rest on an assumption rather than a fact you gave — verify these before you lean on them.*", ""]
    assumptions = rider.get("assumptions_to_verify") or []
    L += ([f"- {a}" for a in assumptions] if assumptions
          else ["Nothing flagged — the load-bearing specifics trace to the facts you provided."]) + [""]

    L += ["## WHERE IT WAS PUSHED BACK ON",
          "*The material objections the independent reviewer raised, and that the recommendation was revised to address.*", ""]
    contested = _texts(review.get("objections"))
    L += ([f"- {c}" for c in contested] if contested
          else ["The independent review raised no material objections."]) + [""]

    dissent = rider.get("proposer_dissent_forwarded")
    if dissent:
        L += ["## STANDING OBJECTIONS FROM THE DELIBERATION",
              "*Unresolved concerns the Proposer half handed forward — weigh these yourself.*", ""]
        L += [f"- {c}" for c in dissent] + [""]

    L += ["## HOW MUCH TO TRUST THIS",
          "*The confidence read, in plain words — kept separate from the recommendation itself.*", "",
          _reviewer_sentence(review),
          "What this cannot know: your real constraints, costs, and priorities — the load-bearing call stays yours.", ""]

    return "\n".join(L)


def _proposer_phase_line(ph, label, settled, unsettled):
    if not ph or ph.get("no_op"):
        return None
    r = ph.get("rounds")
    rtxt = f" over {r} round" + ("s" if r != 1 else "") if r else ""
    return f"- **{label}**{rtxt}: " + (settled if ph.get("converged") else unsettled) + "."


def _render_proposer_report(env: Envelope) -> str | None:
    """The first half of SPARRING as a reader-facing report: the option the
    Proposer (SPARK -> Pattern Lock -> the Cut) committed, the objections it
    hands forward, and the shape of how it got there. Returns None when no
    Proposer ran (e.g. the Resolver-only /spar preset)."""
    prop = env.meta.get("proposer") or {}
    if not prop and not env.committed:
        return None
    spark, plock, cut = (prop.get("spark") or {}), (prop.get("pattern_lock") or {}), (prop.get("cut") or {})
    topic = (env.request or "").strip().splitlines()[0][:100] if env.request else ""

    L = ["# RAPIER — PROPOSER REPORT", ""]
    if topic:
        L += [f"*On: {topic}*", ""]
    L += ["*The first half of SPARRING: widen the options, filter false novelty, and commit one — "
          "with its unresolved objections — for the Resolver to pressure-test.*", ""]

    L += ["## THE COMMITTED OPTION",
          "*What the deliberation chose to put forward for pressure-testing.*", "",
          (env.committed or "(no single option was committed — the deliberation did not converge)").strip(), ""]

    L += ["## STANDING OBJECTIONS CARRIED FORWARD",
          "*Unresolved concerns the deliberation could not settle — handed forward to weigh, not buried.*", ""]
    objs = []
    for ph in (cut, plock):
        for o in ph.get("standing_objections") or []:
            if isinstance(o, dict) and o.get("text"):
                art = o.get("artifact")
                objs.append(f"- {o['text']}" + (f"  _(basis: {art})_" if art else ""))
    L += (objs if objs else ["None — the deliberation closed without unresolved objections."]) + [""]

    L += ["## HOW IT WAS REACHED",
          "*The shape of the deliberation — the three phases, and whether each settled.*", ""]
    for line in (
        _proposer_phase_line(spark, "SPARK — widened the options",
                             "both roles agreed the option space was saturated",
                             "the roles did not agree the space was saturated (hit the round cap)"),
        _proposer_phase_line(plock, "PATTERN LOCK — filtered repetition",
                             "both roles agreed on the de-duplicated set",
                             "the roles did not agree on the de-duplicated set (hit the round cap)"),
        _proposer_phase_line(cut, "THE CUT — committed one option",
                             "both roles agreed on the option to put forward",
                             "the roles could not agree on a single option"),
    ):
        if line:
            L.append(line)
    gv = cut.get("generator_vendor") or spark.get("generator_vendor")
    cv = cut.get("challenger_vendor") or spark.get("challenger_vendor")
    xv = cut.get("cross_vendor") if cut.get("cross_vendor") is not None else spark.get("cross_vendor")
    if xv is not None:
        L += [""]
        if xv and gv and cv:
            L += [f"The generator and challenger ran on different vendors ({gv} vs {cv}) — "
                  "a genuinely independent deliberation."]
        else:
            L += [f"The generator and challenger ran on the same vendor ({gv or '—'}) — "
                  "the deliberation was NOT cross-vendor; weigh it accordingly."]
    L += [""]
    return "\n".join(L)


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

        # The Proposer half's handoff as its own report (only when a Proposer ran).
        proposer_md = _render_proposer_report(env)
        if proposer_md:
            env.meta["proposer_report_md"] = proposer_md

        # /spar-parity named files + the ceremony-ledger row.
        row = _ceremony_row(env)
        env.meta["ceremony_row"] = row
        if ctx.ledger is not None:
            ctx.ledger.write_text("report.md", report_md)
            if proposer_md:
                ctx.ledger.write_text("proposer-report.md", proposer_md)
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
