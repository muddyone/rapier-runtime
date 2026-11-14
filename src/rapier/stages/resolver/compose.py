"""Compose stage — the two-part output + records.

Assembles the final report (recommendation + trust rider), where the rider's
"contested" section carries both the cross-vendor reviewer's objections AND the
Proposer's forwarded standing objections (so the deliberation's dissent is
visible to the user). Writes the /spar-parity named files and the derived
ceremony-ledger row.


Trust-rider field note: ``specifics_traceability`` carries the definitiveness
gate's verdict -- whether every hard specific in the answer traces to a given
fact or is flagged as an estimate. It is deliberately NOT named
"overall confidence": it is one narrow evidence check, and says nothing about
coverage, soundness, or the decision. ``overall_confidence`` remains as a
deprecated alias with the same value.
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


GATE_STAGE = "definitiveness_gate"


_LIST_MARK = re.compile(r"^\s*(?:#+\s*)?(?:\d+[.)]|[-*•])?\s*", re.M)


def _states_figures(text: str | None) -> bool:
    """Does the text state any numeral, outside list numbering? A crude guard:
    erring toward "did not run" is the safe direction."""
    return bool(re.search(r"\d", _LIST_MARK.sub("", text or "")))


def _last(env: Envelope, stage: str):
    """The last trace a stage wrote. With settle >= 1 a gate runs once per
    round, and only its final run speaks for the draft the reader gets."""
    traces = [t for t in env.trace if t.stage == stage and t.kind != "reconcile"]
    return traces[-1] if traces else None


def correctness_outcome(env: Envelope) -> tuple[str, str]:
    """What happened to the correctness (definitiveness) check, as ``(status, reason)``.

    ``status`` is one of:
      ``ran``              the check ran and returned PASS / REVIEW / FAIL over >= 1 specific
      ``nothing-to-check`` the check ran and found no hard specifics to judge
      ``did-not-run``      the check never produced a verdict; ``reason`` says why

    A check that never ran must never read like one that ran and found nothing:
    they call for opposite reactions from the reader. Only the gate's LAST run
    counts: an earlier round's verdict or crash says nothing about the final draft."""
    if env.meta.get("correctness_outcome"):
        return tuple(env.meta["correctness_outcome"])
    last = _last(env, GATE_STAGE)
    if last is None and not env.meta.get("definitiveness"):
        return "did-not-run", "the check is not part of this pipeline"
    if last is None:
        pass  # a result with no trace (supplied by the caller): judge the result itself
    elif (last.data or {}).get("error"):
        return "did-not-run", f"the check crashed: {str(last.data['error'])[:200]}"
    elif "skipped" in (last.summary or ""):
        return "did-not-run", "there was no recommendation to check"
    gate = env.meta.get("definitiveness")
    if not gate:
        return "did-not-run", "the check produced no result"
    v = (env.verdict or gate.get("answer_verdict") or "unchecked").upper()
    if v not in ("PASS", "REVIEW", "FAIL"):
        return "did-not-run", (gate.get("degraded") or "the checker was unavailable")
    if v == "PASS" and gate.get("n_specifics") == 0 and not gate.get("rows"):
        if _states_figures(env.recommendation):
            return "did-not-run", ("the checker found no specifics in an answer that states "
                                   "figures, so its result cannot be trusted")
        return "nothing-to-check", "the answer states no hard specifics"
    return "ran", ""


CITATION_STAGE = "citation_gate"


def citation_outcome(env: Envelope) -> tuple[str, str]:
    """What happened to the external-canon citation check, as ``(status, reason)``.

    Same three statuses as :func:`correctness_outcome`, judged on the gate's last
    run (with verify=round it runs every round). ``nothing-to-check`` means it
    ran and the answer cited nothing checkable; a gate that was switched off,
    crashed or was never in the pipeline is ``did-not-run``."""
    if env.meta.get("citation_outcome"):
        return tuple(env.meta["citation_outcome"])
    last = _last(env, CITATION_STAGE)
    if last is None and env.meta.get("citation_verdicts"):
        return "ran", ""  # a result with no trace (supplied by the caller)
    if last is None:
        if str(env.meta.get("verify") or "") == "off":
            by = env.meta.get("verify_set_by")
            return "did-not-run", (f"switched off by {by} (verify=off)" if by
                                   else "switched off (verify=off)")
        return "did-not-run", "the check is not part of this pipeline"
    if (last.data or {}).get("error"):
        return "did-not-run", f"the check crashed: {str(last.data['error'])[:200]}"
    if "skipped" in (last.summary or ""):
        return "nothing-to-check", "the answer cites nothing externally checkable"
    if env.meta.get("citation_verdicts"):
        return "ran", ""
    return "did-not-run", "the check produced no result"


def _drop_stale_results(env: Envelope) -> None:
    """When a gate's last run did not produce the result in hand, that result is
    from an earlier draft. Move it aside (kept for audit) so no field the reader
    or a calling agent reads still shows it as the answer's verdict."""
    env.meta.pop("correctness_outcome", None)
    env.meta.pop("citation_outcome", None)
    outcome = correctness_outcome(env)
    coutcome = citation_outcome(env)
    # Fixed here, before anything is moved aside, so every later reader agrees.
    env.meta["correctness_outcome"] = list(outcome)
    env.meta["citation_outcome"] = list(coutcome)
    status = outcome[0]
    if status == "did-not-run" and (env.verdict or "").upper() in ("PASS", "REVIEW", "FAIL"):
        env.meta["stale_verdict"] = env.verdict
        env.meta["definitiveness_stale"] = env.meta.pop("definitiveness", None)
        env.verdict = "unchecked"
    cstatus = coutcome[0]
    if cstatus != "ran" and env.meta.get("citation_verdicts"):
        env.meta["citation_verdicts_stale"] = env.meta.pop("citation_verdicts")
        env.meta["citation_gate_stale"] = env.meta.pop("citation_gate", None)


def correctness_label(status: str, reason: str) -> str:
    """One-token form for the trust rider and ceremony.json: ``did-not-run: <reason>``."""
    return f"did-not-run: {reason}" if status == "did-not-run" else status


def _verdict_sentence(verdict, gate, outcome: tuple[str, str] | None = None) -> str:
    """The correctness (definitiveness) gate verdict in plain words — no shorthand."""
    fails = len(gate.get("failures") or [])
    v = (verdict or "unchecked").upper()
    status, reason = outcome or ("", "")
    if status == "did-not-run" or (not status and v not in ("PASS", "FAIL", "REVIEW")):
        why = f" ({reason})" if reason else ""
        return ("The correctness check did NOT run" + why + ". Nothing was checked, so this is "
                "not a pass: treat every stated specific as unverified.")
    if status == "nothing-to-check":
        return ("The correctness check ran and found no hard specifics to check: the answer "
                "states no figures, dates or named facts that could trace to your givens.")
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
    return ("The correctness check did NOT run. Nothing was checked, so this is not a pass: "
            "treat every stated specific as unverified.")


def _checks_policy_sentence(env: Envelope) -> str:
    """Who set the citation check's level, and whether an operator minimum stood
    behind it that the caller could not lower."""
    mode = env.meta.get("verify")
    if not mode:
        return ""
    by = env.meta.get("verify_set_by") or "the caller"
    if mode == "off":
        s = f"The citation check was switched off, by {by}."
    else:
        s = f"The citation check ran at level '{mode}', set by {by}."
    minimum = env.meta.get("verify_minimum")
    if minimum:
        src = env.meta.get("verify_minimum_source") or "operator policy"
        if env.meta.get("verify_minimum_external"):
            s += (f" The operator requires at least '{minimum}' ({src}), a setting the "
                  "calling agent cannot change.")
        else:
            s += (f" The operator requires at least '{minimum}' ({src}), but that setting "
                  "is within the calling agent's reach, so it is not an independent control.")
    else:
        s += " No operator minimum was set, so the caller chose which checks ran."
    return s


_RUN_WORD = {"ran": "ran", "failed-soft": "FAILED (the run went on without it)",
             "skipped": "skipped", "not-reached": "not reached"}


def declared_vs_ran(env: Envelope) -> list[str]:
    """One line per declared stage: what the manifest asked for, and what happened.
    Compose is this stage, so it is reported as the one writing the report."""
    runs = env.meta.get("stage_runs")
    if runs is None:
        return []
    lines = []
    for r in runs:
        if r.get("stage") == "compose" and r.get("status") == "ran":
            lines.append("compose: wrote this report")
            continue
        word = _RUN_WORD.get(r.get("status"), r.get("status") or "?")
        detail = r.get("detail")
        lines.append(f"{r.get('stage')}: {word}" + (f" ({detail})" if detail else ""))
    return lines


def _challenge_phrase(review) -> str:
    """Who raised the challenge, worded so a same-vendor review is never called independent."""
    if review.get("cross_vendor"):
        return "an independent, different-vendor reviewer"
    return "a reviewer on the same vendor as the author (not an independent check)"


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
        textwrap.fill(_plain_math(str(it)).strip(), width=_W, initial_indent="• ",
                      subsequent_indent="  ")
        for it in items
    )


# Models often write arithmetic as LaTeX (\( H \times 0.60 \)), which a terminal
# prints literally. Only text inside \( \) or \[ \] is rewritten, so a stray
# backslash elsewhere in the prose is left alone.
_MATH_SPAN_RE = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
_MATH_SYMBOLS = {
    "times": "×", "div": "÷", "cdot": "·", "approx": "≈", "pm": "±",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "to": "→", "rightarrow": "→", "infty": "∞", "%": "%", "$": "$",
    ",": " ", ";": " ", "!": "", " ": " ", "quad": " ", "qquad": " ",
}


def _plain_math(text: str) -> str:
    def span(m: re.Match) -> str:
        t = m.group(1) if m.group(1) is not None else m.group(2)
        for _ in range(3):  # nested \frac / \text unwrap from the inside out
            t = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", t)
            t = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^{}]*)\}", r"\1", t)
        t = re.sub(r"\\left|\\right", "", t)
        t = re.sub(r"\\([A-Za-z]+|[%$,;! ])",
                   lambda k: _MATH_SYMBOLS.get(k.group(1), k.group(0)), t)
        t = t.replace("{", "").replace("}", "")
        return re.sub(r"\s+", " ", t).strip()
    return _MATH_SPAN_RE.sub(span, text or "")


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
    return _plain_math(s)


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
    status, reason = citation_outcome(env)
    if status == "did-not-run":
        return ("This check did NOT run (" + reason + "). No reference in the "
                "recommendation was looked up, so this is not a clean result: treat any "
                "CVE, CWE, RFC, DOI or URL it cites as unconfirmed.")
    if not verdicts:
        return ("The recommendation cited nothing externally checkable — no CVE, CWE, "
                "RFC, DOI, or URL — so there was nothing to confirm against a public "
                "registry. This is normal for judgment and strategy questions, where the "
                "load-bearing work is the challenge below, not citation.")
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


def _record_body(env: Envelope) -> str:
    """Provenance statement: that a durable record exists (or was declined) and where.

    Governance is only real when the reader can see the record. When the run
    persisted, name the full path; when it was opted out, say so plainly so a
    missing trail is never mistaken for a lost one.
    """
    run_dir = env.meta.get("run_dir")
    if run_dir:
        return (
            "Every model call in this run was captured verbatim. The full "
            "transcript, per-stage records, the verdict, and this report are "
            "saved for audit to:\n\n"
            f"  {run_dir}\n\n"
            "Keep or relocate that directory to retain the run's provenance."
        )
    return (
        "No durable record was written — persistence was disabled for this run "
        "(--no-save / RAPIER_NO_PERSIST). This ceremony leaves no audit trail; "
        "nothing about it can be reconstructed after this output."
    )



def _reconcile_body(env) -> str:
    """The arithmetic gate in plain words. Reports what was checked, not just what failed —
    an empty finding list and a check that never ran must not read the same."""
    rec = env.meta.get("reconcile") or {}
    counts = rec.get("counts") or {}
    checked = rec.get("checked", 0)
    mismatches = [r for r in (rec.get("relations") or []) if r.get("status") == "mismatch"]

    if not rec or rec.get("verdict") == "UNCHECKED":
        if counts.get("unverifiable"):
            return _para(
                f"Nothing could be checked. {counts['unverifiable']} figure(s) were stated "
                "without the parts they are built from appearing anywhere in the text, so "
                "the arithmetic could not be confirmed either way. This is not a pass.")
        return _para("No stated totals were found to check.")

    unchecked = counts.get("unverifiable", 0)
    not_checked = (
        f" {unchecked} other figure(s) were not checked: their parts or the operation "
        "could not be recovered from the text, so they are neither confirmed nor flagged."
        if unchecked else "")

    if not mismatches:
        return _para(
            f"{checked} stated figure(s) were recomputed from their parts and agreed. "
            "The arithmetic was done in code, not judged by a model." + not_checked)

    lines = []
    for m in mismatches:
        stated, computed = m.get("stated"), m.get("computed")
        how = (f"evaluating {m['expr']}" if m.get("operation") == "expression" and m.get("expr")
               else m.get("operation"))
        lines.append(
            f"{m.get('label')}: the text states {stated}, but its parts come to {computed}"
            f" ({how}). Stated at {m.get('aggregate_location') or 'unknown location'}: "
            f"\u201c{(m.get('aggregate_quote') or '').strip()}\u201d")
    body = _bullets(lines)
    return body + "\n\n" + _para(not_checked.strip()) if not_checked else body


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
              _para(_verdict_sentence(env.verdict, gate, correctness_outcome(env))
                    + " The full advice is under THE RECOMMENDATION below."))

    L += _sec("THE RECOMMENDATION",
              "The answer on the merits — read this as the actual advice.",
              _reflow(_demote_md(env.recommendation or "(none produced)")))

    # ── the single hard break: advice ends, trust rider begins ────────────────
    L += ["", "", _HEAVY, "  TRUST RIDER", _HEAVY, "",
          "How far to trust the recommendation above: what is still yours to",
          "confirm, what was verified for you, and how the answer held up under",
          "challenge."]

    assumptions = rider.get("assumptions_to_verify") or []
    L += _sec("STILL YOURS TO CHECK",
              "Open items. Specifics the answer states that rest on an assumption "
              "rather than a fact you gave — confirm these against your situation "
              "before you rely on them.",
              _bullets(assumptions) if assumptions
              else ("Not checked — the correctness check did not run on this answer, so "
                    "nothing here has been traced to the facts you provided."
                    if correctness_outcome(env)[0] == "did-not-run"
                    else "Nothing flagged — the load-bearing specifics trace to the facts you provided."))

    if env.meta.get("reconcile"):
        L += _sec("WHETHER THE NUMBERS ADD UP",
                  "Every total or worked figure the answer states, recomputed from the parts "
                  "or formula it gives — arithmetic done in code, with no model deciding "
                  "the result.",
                  _reconcile_body(env))

    L += _sec("WHAT WAS VERIFIED AGAINST PUBLIC REGISTRIES",
              "Checkable references the recommendation cites, each resolved against "
              "external canon (MITRE, IETF, Crossref, live web) with no model in the loop.",
              _grounding_body(env))

    contested = _texts(review.get("objections"))
    L += _sec("ALREADY FIXED UNDER CHALLENGE",
              "Not action items — already folded into the recommendation above. "
              + _challenge_phrase(review)[0].upper() + _challenge_phrase(review)[1:]
              + " raised each of these, and the answer was revised to address it. "
              "Shown so you can see what the challenge caught and how the "
              "recommendation got stronger.",
              _bullets(contested) if contested
              else "The review found nothing material to change — the recommendation "
                   "held up as written. See HOW MUCH TO TRUST THIS for how independent "
                   "that review was.")

    dissent = rider.get("proposer_dissent_forwarded")
    if dissent:
        L += _sec("STANDING OBJECTIONS FROM THE DELIBERATION",
                  "Unresolved concerns the Proposer half handed forward — weigh these yourself.",
                  _bullets(dissent))

    L += _sec("HOW MUCH TO TRUST THIS",
              "How independent the challenge really was — and the limits no model "
              "here can see past.",
              _para(_reviewer_sentence(review)
                    + ("\n\n" + _checks_policy_sentence(env) if _checks_policy_sentence(env) else "")
                    + "\n\nWhat this cannot know: your real constraints, costs, and priorities — "
                    "the load-bearing call stays yours."))

    stage_lines = declared_vs_ran(env)
    warnings = env.meta.get("manifest_warnings") or []
    if stage_lines or warnings:
        body = _bullets(stage_lines) if stage_lines else ""
        if warnings:
            body += ("\n\n" if body else "") + _para(
                "Warning about the method itself: this pipeline "
                + "; it ".join(warnings) + ".")
        L += _sec("WHAT RAN",
                  "Every stage the method declared, and what actually happened to it. "
                  "A stage that failed or was skipped did not check this answer.",
                  body)

    L += _sec("THE RECORD",
              "Where this run is on disk — its audit trail.",
              _para(_record_body(env)))

    L += ["", "", _RULE,
          "Produced by Rapier Runtime — a ResourceForge project · rapierruntime.com"]

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
    the run (no self-coding) — more honest than a model grading its own work.

    Emits the full ceremony-description schema plus the front-door input-type
    classification (from ``env.meta["frame"]``, seeded by the caller via
    ``--frame`` since Frame runs as a separate call ahead of the pipeline). When
    no frame was seeded the classification fields are emitted empty — the schema
    stays stable, the values are just absent. Loom-project provenance
    (recorded_at / project / source / cwd / id) is deliberately NOT stamped here:
    the engine is context-agnostic, so that stamp belongs to the loom-side
    persistence layer (append-spar-ledger.py), by design."""
    review = env.meta.get("review") or {}
    gate = env.meta.get("definitiveness") or {}
    citation = env.meta.get("citation_gate") or {}
    objections = review.get("objections") or []
    before = env.meta.get("recommendation_before_fix")
    changed = bool(before is not None and before != env.recommendation)
    surfaced = bool(objections)
    load_bearing = changed or surfaced

    # Front-door classification, seeded from the separate `rapier frame` call.
    frame = env.meta.get("frame") or {}
    input_type = frame.get("input_type") or ""
    route = frame.get("route") or ""
    # offramp_taken — where the run actually stopped, read off the envelope.
    offramp_taken = ("full_resolve" if env.recommendation
                     else "proposition" if env.committed else "question")
    # A mechanically-coherent row (not self-reported), so grounding_coherence is
    # "ok" when verification ran and "n/a" when it did not — never "coerced".
    verified = bool(citation.get("gate")) or citation.get("grounding_rate") is not None \
        or bool(citation.get("theater_flags"))

    return {
        "skill": "rapier",
        "sparring_version": "v2",
        "mode": "full-ceremony" if env.committed else "one-pass",
        "iterations": 1 + int(env.meta.get("settle") or 0),
        "reviewer_vendor": review.get("reviewer_vendor"),
        "cross_vendor": bool(review.get("cross_vendor")),
        "topic": env.request[:120],
        "converged": env.verdict == "PASS",
        "held_at_cap": env.verdict == "FAIL",
        "challenger_changed_recommendation": changed,
        "what_changed": "recommendation revised under review" if changed else "",
        "challenger_surfaced_error_or_risk": surfaced,
        "what_surfaced": "; ".join(_texts(objections))[:400],
        "load_bearing": load_bearing,
        "strongest_quote": (_texts(objections)[0] if objections else "")[:400],
        "verdict": "MATTERED" if load_bearing else "DID_NOT_MATTER",
        "answer_verdict": env.verdict or "unchecked",
        "correctness_check": correctness_outcome(env)[0],
        "correctness_check_reason": correctness_outcome(env)[1],
        "citation_check": citation_outcome(env)[0],
        "citation_check_reason": citation_outcome(env)[1],
        "verify_mode": str(env.meta.get("verify") or ""),
        "stages_declared": len(env.meta.get("stage_runs") or []),
        "stages_failed_soft": [r["stage"] for r in (env.meta.get("stage_runs") or [])
                               if r.get("status") == "failed-soft"],
        "stages_skipped": [r["stage"] for r in (env.meta.get("stage_runs") or [])
                           if r.get("status") == "skipped"],
        "manifest_warnings": list(env.meta.get("manifest_warnings") or []),
        "verify_set_by": env.meta.get("verify_set_by") or "",
        "verify_minimum": env.meta.get("verify_minimum"),
        "verify_minimum_source": env.meta.get("verify_minimum_source") or "",
        "verify_minimum_external": bool(env.meta.get("verify_minimum_external")),
        "gate": citation.get("gate", ""),
        "grounding_rate": citation.get("grounding_rate"),
        "theater_flags": citation.get("theater_flags", 0),
        "grounding_coherence": "ok" if verified else "n/a",
        "artifact_path": "",  # set to <run_dir>/report.md by ComposeStage (has run_dir)
        # --- front-door input-type classification (§7) ---
        "input_type": input_type,
        "readiness": frame.get("readiness") or "",
        "earned_gate_failed": frame.get("earned_gate_failed") or "",
        "anchor": frame.get("anchor") or "",
        "routed_to": route,
        "offramp_taken": offramp_taken,
        "demoted": bool(input_type == "proposition" and route == "propose"),
    }


@register_stage("compose")
class ComposeStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        _drop_stale_results(env)
        review = env.meta.get("review") or {}
        gate = env.meta.get("definitiveness") or {}
        standing = ((env.meta.get("proposer") or {}).get("cut") or {}).get("standing_objections") or []

        rider = dict(env.trust_rider or {})
        rider.setdefault("contested_and_resolved", _texts(review.get("objections")))
        if standing:
            rider["proposer_dissent_forwarded"] = _texts(standing)
        rider["specifics_traceability"] = env.verdict
        rider["overall_confidence"] = env.verdict  # deprecated alias; remove after 0.6.x
        rider["correctness_check"] = correctness_label(*correctness_outcome(env))
        rider["citation_check"] = correctness_label(*citation_outcome(env))
        if env.meta.get("stage_runs") is not None:
            rider["stages_run"] = [dict(r) for r in env.meta["stage_runs"]]
        if env.meta.get("manifest_warnings"):
            rider["manifest_warnings"] = list(env.meta["manifest_warnings"])
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
        if ctx.run_dir:
            row["artifact_path"] = os.path.join(ctx.run_dir, "report.md")
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
