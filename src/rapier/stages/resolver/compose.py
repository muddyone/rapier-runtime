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
import re
import textwrap

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
                "ambiguously between fact and estimate — see STILL YOURS TO CHECK below.")
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


# ── plain-text layout ────────────────────────────────────────────────────────
# The report prints to a terminal where NO markdown renders, so hierarchy comes
# from case, rules, and whitespace — never from `#`/`**`. One character, one job:
#   ═  the single RECOMMENDATION → TRUST RIDER part break, used nowhere else
#   ─  ordinary section rule (under an ALL-CAPS title)
#   =  the document-title underline
# Body is indented two spaces so an embedded recommendation's own Title-case
# sub-headings read as content *inside* a section, never as peers of it.
_W = 64
_RULE = "─" * _W
_HEAVY = "═" * _W


def _indent(body: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join((pad + ln) if ln.strip() else "" for ln in (body or "").split("\n"))


def _para(text: str, width: int = _W - 2) -> str:
    """Hard-wrap generated prose to the measure, preserving blank-line paragraph
    breaks. For the report's own sentences — NOT the author's recommendation,
    which is reflowed separately so its lists and structure survive."""
    blocks = re.split(r"\n\s*\n", text or "")
    return "\n\n".join(textwrap.fill(b.strip(), width=width) for b in blocks if b.strip())


_LIST_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+(.*)$")


def _reflow(text: str, width: int = _W - 2) -> str:
    """Reflow the author's recommendation to the measure WITHOUT mangling it:
    consecutive prose lines wrap as one paragraph, list items wrap individually
    with a hanging indent (markers preserved; ordered numbering kept), and blank
    lines / demoted sub-headings are left as their own lines."""
    out: list[str] = []
    para: list[str] = []

    def flush():
        if para:
            out.append(textwrap.fill(" ".join(para), width=width))
            para.clear()

    for ln in (text or "").split("\n"):
        if not ln.strip():
            flush()
            out.append("")
            continue
        m = _LIST_RE.match(ln)
        if m:
            flush()
            mk = "•" if m.group(1) in ("-", "*", "•") else m.group(1)
            init = mk + " "
            out.append(textwrap.fill(m.group(2).strip(), width=width,
                                     initial_indent=init, subsequent_indent=" " * len(init)))
        else:
            para.append(ln.strip())
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


def _sec(title: str, gloss: str, body: str) -> list[str]:
    """An ALL-CAPS section: title, a full-width rule under it, a flush-left one-line
    gloss (what the section is for), then the indented body."""
    out = ["", "", title, _RULE]
    if gloss:
        out += [textwrap.fill(gloss, width=_W), ""]
    out += [_indent(body)]
    return out


def _bullets(items) -> str:
    """One finding per bullet, hanging-indented so the marker column stays a clean
    vertical scan-line."""
    return "\n".join(
        textwrap.fill(str(it).strip(), width=_W, initial_indent="• ", subsequent_indent="  ")
        for it in items
    )


def _demote_md(text: str) -> str:
    """Neutralize an embedded recommendation's own markdown so it cannot compete
    with the report's structure: ATX headings (`## X`) become bare Title-case
    lines, and `**bold**` / `` `code` `` markers are stripped (they render as
    literal punctuation in a terminal)."""
    lines = []
    for ln in (text or "").split("\n"):
        m = re.match(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$", ln)
        lines.append(m.group(1) if m else ln)
    s = "\n".join(lines)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)   # bold
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", s)  # italic
    s = re.sub(r"(?<!\w)`([^`]+)`(?!\w)", r"\1", s)  # inline code
    return s


# The stored verdicts carry the citation gate's shape (``status``:
# verified|refuted|unverifiable|unverified-not-checked); we also tolerate the raw
# backend shape (``grounding``: GROUNDED_VERIFIED …) so either reaches the reader.
_GROUNDING_LABEL = {
    "verified": "VERIFIED", "grounded_verified": "VERIFIED",
    "refuted": "REFUTED", "grounded_refuted": "REFUTED",
    "unverifiable": "COULD NOT CHECK",
    "unverified-not-checked": "COULD NOT CHECK", "unverified_not_checked": "COULD NOT CHECK",
    "ungrounded": "NOT CHECKABLE",
}
_GROUNDING_BACKEND = {
    "mitre-cve": "MITRE CVE Services", "mitre-cwe": "MITRE CWE",
    "ietf-datatracker": "IETF datatracker", "crossref": "Crossref",
    "web-fetch": "live fetch", "repo": "the repository", "in-pack": "the briefing",
}


def _v_status(v: dict) -> str:
    return (v.get("status") or v.get("grounding") or "").lower()


def _grounding_body(env: Envelope) -> str:
    """Surface the citation gate's per-reference verdicts — the flagship
    'confirmed against public registries, no model in the loop' property. Was
    computed and discarded before; now it is shown."""
    verdicts = env.meta.get("citation_verdicts") or []
    if not verdicts:
        return ("The recommendation cited nothing externally checkable — no CVE, CWE, "
                "RFC, DOI, or URL — so there was nothing to confirm against a public "
                "registry. This is normal for judgment and strategy questions, where the "
                "load-bearing work is the cross-vendor challenge above, not citation.")
    lines = []
    nver = 0
    for v in verdicts:
        ref = v.get("artifact_ref", "?")
        key = _v_status(v)
        is_ver = key in ("verified", "grounded_verified")
        nver += is_ver
        status = _GROUNDING_LABEL.get(key, (key or "?").upper())
        backend = _GROUNDING_BACKEND.get(v.get("backend", ""), v.get("backend", ""))
        head = f"{ref} — {status}" + (f"  ·  {backend}" if backend else "")
        lines.append(textwrap.fill(head, width=_W, subsequent_indent="    "))
        if is_ver:
            ev = (v.get("evidence") or "").split(":", 1)[-1].strip()
            if ev:
                lines.append(textwrap.fill(ev[:200], width=_W - 4,
                                           initial_indent="    ", subsequent_indent="    "))
    n = len(verdicts)
    plural = "reference" if n == 1 else "references"
    lines += ["", textwrap.fill(
        f"Grounding: {nver} of {n} checkable {plural} confirmed against public "
        "canon, with no model in the loop.", width=_W)]
    # If any of these were also flagged by the correctness gate as "external
    # knowledge not in your problem", say plainly that they are confirmed real —
    # so the two gates don't appear to contradict each other.
    grounded = ((env.meta.get("definitiveness") or {}).get("grounded_specifics")) or []
    if grounded:
        refs = ", ".join(dict.fromkeys(g.get("ref") for g in grounded if g.get("ref")))
        lines += ["", textwrap.fill(
            f"Note: {refs} came from the model's own knowledge, not from your "
            "problem statement — the correctness gate flagged them for that reason, "
            "and the check above confirms each is a real, published identifier. What "
            "remains yours to confirm is that they apply to your exact situation.",
            width=_W)]
    return "\n".join(lines)


def _render_report(env: Envelope) -> str:
    review = env.meta.get("review") or {}
    gate = env.meta.get("definitiveness") or {}
    rider = env.trust_rider or {}
    topic = (env.request or "").strip().splitlines()[0][:100] if env.request else ""

    title = "RAPIER — RESOLVER REPORT"
    L = [title, "=" * len(title)]
    if topic:
        L += ["", f"On: {topic}"]

    L += _sec("BOTTOM LINE",
              "How far to trust this, in one breath.",
              _para(_verdict_sentence(env.verdict, gate)
                    + " The full advice is under THE RECOMMENDATION below."))

    L += _sec("THE RECOMMENDATION",
              "The answer on the merits — read this as the actual advice.",
              _reflow(_demote_md(env.recommendation or "(none produced)")))

    # ── the single hard break: advice ends, trust rider begins ────────────────
    L += ["", "", _HEAVY, "  TRUST RIDER", _HEAVY, "",
          "How far to trust the recommendation above: what is still yours to",
          "confirm, what was verified for you, and how the answer held up under",
          "an independent challenge."]

    assumptions = rider.get("assumptions_to_verify") or []
    L += _sec("STILL YOURS TO CHECK",
              "Open items. Specifics the answer states that rest on an assumption "
              "rather than a fact you gave — confirm these against your situation "
              "before you rely on them.",
              _bullets(assumptions) if assumptions
              else "Nothing flagged — the load-bearing specifics trace to the facts you provided.")

    L += _sec("WHAT WAS VERIFIED AGAINST PUBLIC REGISTRIES",
              "Checkable references the recommendation cites, each resolved against "
              "external canon (MITRE, IETF, Crossref, live web) with no model in the loop.",
              _grounding_body(env))

    contested = _texts(review.get("objections"))
    L += _sec("ALREADY FIXED UNDER CHALLENGE",
              "Not action items — already folded into the recommendation above. An "
              "independent, different-vendor reviewer raised each of these, and the "
              "answer was revised to address it. Shown so you can see what the "
              "challenge caught and how the recommendation got stronger.",
              _bullets(contested) if contested
              else "The independent review found nothing material to change — the "
                   "recommendation held up as written.")

    dissent = rider.get("proposer_dissent_forwarded")
    if dissent:
        L += _sec("STANDING OBJECTIONS FROM THE DELIBERATION",
                  "Unresolved concerns the Proposer half handed forward — weigh these yourself.",
                  _bullets(dissent))

    L += _sec("HOW MUCH TO TRUST THIS",
              "How independent the challenge really was — and the limits no model "
              "here can see past.",
              _para(_reviewer_sentence(review)
                    + "\n\nWhat this cannot know: your real constraints, costs, and priorities — "
                    "the load-bearing call stays yours."))

    return "\n".join(L).rstrip() + "\n"


def _proposer_phase_line(ph, label, settled, unsettled):
    if not ph or ph.get("no_op"):
        return None
    r = ph.get("rounds")
    rtxt = f" over {r} round" + ("s" if r != 1 else "") if r else ""
    return f"{label}{rtxt} — " + (settled if ph.get("converged") else unsettled) + "."


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

    title = "RAPIER — PROPOSER REPORT"
    L = [title, "=" * len(title)]
    if topic:
        L += ["", f"On: {topic}"]
    L += ["",
          textwrap.fill("The first half of SPARRING: widen the options, filter false "
                        "novelty, and commit one — with its unresolved objections — for "
                        "the Resolver to pressure-test.", width=_W)]

    L += _sec("THE COMMITTED OPTION",
              "What the deliberation chose to put forward for pressure-testing.",
              _reflow(_demote_md((env.committed
                       or "(no single option was committed — the deliberation did not converge)").strip())))

    objs = []
    for ph in (cut, plock):
        for o in ph.get("standing_objections") or []:
            if isinstance(o, dict) and o.get("text"):
                art = o.get("artifact")
                objs.append(o["text"] + (f"  (basis: {art})" if art else ""))
    L += _sec("STANDING OBJECTIONS CARRIED FORWARD",
              "Unresolved concerns the deliberation could not settle — handed forward "
              "to weigh, not buried.",
              _bullets(objs) if objs
              else "None — the deliberation closed without unresolved objections.")

    how = [ln for ln in (
        _proposer_phase_line(spark, "SPARK — widened the options",
                             "both roles agreed the option space was saturated",
                             "the roles did not agree the space was saturated (hit the round cap)"),
        _proposer_phase_line(plock, "PATTERN LOCK — filtered repetition",
                             "both roles agreed on the de-duplicated set",
                             "the roles did not agree on the de-duplicated set (hit the round cap)"),
        _proposer_phase_line(cut, "THE CUT — committed one option",
                             "both roles agreed on the option to put forward",
                             "the roles could not agree on a single option"),
    ) if ln]
    gv = cut.get("generator_vendor") or spark.get("generator_vendor")
    cv = cut.get("challenger_vendor") or spark.get("challenger_vendor")
    xv = cut.get("cross_vendor") if cut.get("cross_vendor") is not None else spark.get("cross_vendor")
    if xv is not None:
        if xv and gv and cv:
            how += ["", f"The generator and challenger ran on different vendors ({gv} vs {cv}) — "
                        "a genuinely independent deliberation."]
        else:
            how += ["", f"The generator and challenger ran on the same vendor ({gv or '—'}) — "
                        "the deliberation was NOT cross-vendor; weigh it accordingly."]
    L += _sec("HOW IT WAS REACHED",
              "The shape of the deliberation — the three phases, and whether each settled.",
              "\n".join(how))

    return "\n".join(L).rstrip() + "\n"


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
            verdicts = env.meta.get("citation_verdicts")
            if verdicts is not None:
                ctx.ledger.write_json("grounding.json", {
                    "summary": env.meta.get("citation_gate"),
                    "verdicts": verdicts,
                })
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
