"""Type-3 rider check — does the record back what the rider says was contested?

The trust rider claims *"an independent reviewer raised this objection, and the
answer was revised to address it."* That is a claim about something that
happened during the run, and every run already writes a verbatim record of
every model call to ``transcript.jsonl``. So the claim is checkable **against
the run's own record, mechanically, with no model in the loop.**

That last part is the point. Rapier's strongest guarantee — the one the
Resolver paper says carries the weight — is the *model-free* existence check:
a reader confirms a cited source is real without taking any AI's word for it.
This check holds contested-and-held claims to the same standard. It never asks
a model whether the rider is honest; it looks.

What it catches: a rider asserting a contest the reviewer never raised. That is
the most literal form of governance theater — the accountability artifact
inventing its own accountability — and nothing else in the pipeline would
notice it.

Verdicts, per claim:

``IN_RECORD``      the claim's text is present in a recorded model response.
``PARAPHRASED``    not verbatim, but token overlap with some response is at or
                   above ``PARAPHRASE_FLOOR``. Legitimate — compose may reword —
                   but weaker evidence, so it is reported separately rather than
                   folded into a pass.
``NOT_IN_RECORD``  no recorded response supports it. **This is the finding.**
``UNCHECKED``      no transcript available (persistence off, or an older run).

``UNCHECKED`` is deliberately not a pass. An unverifiable claim that quietly
counts as verified is exactly the degradation this project has already had to
fix once in the grounding verifier: it stays a distinct state, it lands in the
denominator, and no caller can read it as success.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

#: Token-overlap floor (Jaccard-like, claim-side recall) for PARAPHRASED.
PARAPHRASE_FLOOR = 0.60

#: Claims shorter than this are too small for overlap scoring to mean anything;
#: they must match verbatim or they do not match.
MIN_TOKENS_FOR_OVERLAP = 4

_WS = re.compile(r"\s+")
_NONWORD = re.compile(r"[^a-z0-9\s]+")

# Rider keys carrying "this was raised during the run" claims. Both are
# contested-and-held claims in the §10 sense; they differ only in which half of
# the ceremony raised them.
CONTESTED_KEYS = ("contested_and_resolved", "proposer_dissent_forwarded")


def _norm(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — comparison form only."""
    return _WS.sub(" ", _NONWORD.sub(" ", (s or "").lower())).strip()


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if t}


def _overlap(claim: str, response: str) -> float:
    """Fraction of the claim's tokens present in the response (claim-side recall).

    Recall, not Jaccard: a long reviewer response that fully contains a short
    objection should score 1.0. Symmetric measures punish exactly the shape we
    expect, since responses are much longer than the claims drawn from them.
    """
    c = _tokens(claim)
    if not c:
        return 0.0
    return len(c & _tokens(response)) / len(c)


def _responses(run_dir: str) -> list[str] | None:
    """Every recorded model response for a run, or None when there is no record."""
    path = os.path.join(run_dir, "transcript.jsonl")
    if not os.path.isfile(path):
        return None
    out: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue  # a torn line is not a reason to fail the whole check
            if isinstance(ev, dict) and ev.get("response"):
                out.append(str(ev["response"]))
    return out


def _classify(claim: str, responses: list[str]) -> tuple[str, float]:
    norm_claim = _norm(claim)
    if not norm_claim:
        return "NOT_IN_RECORD", 0.0
    for r in responses:
        if norm_claim in _norm(r):
            return "IN_RECORD", 1.0
    best = max((_overlap(claim, r) for r in responses), default=0.0)
    if len(_tokens(claim)) < MIN_TOKENS_FOR_OVERLAP:
        return "NOT_IN_RECORD", best  # too short for overlap to carry meaning
    if best >= PARAPHRASE_FLOOR:
        return "PARAPHRASED", best
    return "NOT_IN_RECORD", best


def check_rider_record(rider: dict[str, Any] | None, run_dir: str) -> dict[str, Any]:
    """Check a rider's contested claims against the run's transcript.

    Returns a report dict. ``status`` is ``ok`` when every claim is IN_RECORD or
    PARAPHRASED, ``failed`` when any claim is NOT_IN_RECORD, ``unchecked`` when
    there is no transcript, and ``none`` when the rider makes no such claim.
    """
    claims: list[tuple[str, str]] = []
    for key in CONTESTED_KEYS:
        for text in (rider or {}).get(key) or []:
            if isinstance(text, str) and text.strip():
                claims.append((key, text))

    if not claims:
        return {"status": "none", "checked": 0, "in_record": 0, "paraphrased": 0,
                "not_in_record": 0, "claims": [],
                "summary": "rider makes no contested-and-held claim"}

    responses = _responses(run_dir)
    if responses is None:
        return {
            "status": "unchecked", "checked": 0, "in_record": 0, "paraphrased": 0,
            "not_in_record": 0,
            "claims": [{"key": k, "text": t, "verdict": "UNCHECKED", "score": 0.0}
                       for k, t in claims],
            "summary": (f"{len(claims)} contested claim(s) could NOT be checked — no "
                        f"transcript.jsonl in {run_dir}. Not a pass."),
        }

    results = []
    for key, text in claims:
        verdict, score = _classify(text, responses)
        results.append({"key": key, "text": text, "verdict": verdict,
                        "score": round(score, 3)})

    counts = {v: sum(1 for r in results if r["verdict"] == v)
              for v in ("IN_RECORD", "PARAPHRASED", "NOT_IN_RECORD")}
    status = "failed" if counts["NOT_IN_RECORD"] else "ok"
    summary = (
        f"{len(results)} contested claim(s): {counts['IN_RECORD']} in the record, "
        f"{counts['PARAPHRASED']} paraphrased, {counts['NOT_IN_RECORD']} unsupported"
    )
    if status == "failed":
        summary += " — the rider claims a contest the record does not show"
    return {"status": status, "checked": len(results),
            "in_record": counts["IN_RECORD"], "paraphrased": counts["PARAPHRASED"],
            "not_in_record": counts["NOT_IN_RECORD"],
            "claims": results, "summary": summary}


def check_run(run_dir: str) -> dict[str, Any]:
    """Check a persisted run directory (reads its envelope.json for the rider)."""
    env_path = os.path.join(run_dir, "envelope.json")
    if not os.path.isfile(env_path):
        return {"status": "unchecked", "checked": 0, "in_record": 0, "paraphrased": 0,
                "not_in_record": 0, "claims": [],
                "summary": f"no envelope.json in {run_dir}"}
    with open(env_path, "r", encoding="utf-8") as fh:
        env = json.load(fh)
    rider = env.get("trust_rider") or (env.get("meta") or {}).get("trust_rider")
    return check_rider_record(rider, run_dir)


def render(report: dict[str, Any]) -> str:
    """Plain-text rendering, in the report register: say what it is, plainly."""
    lines = ["RIDER RECORD CHECK", ""]
    lines.append(report["summary"])
    if report["claims"]:
        lines.append("")
        for c in report["claims"]:
            mark = {"IN_RECORD": "  ok  ", "PARAPHRASED": " para ",
                    "NOT_IN_RECORD": " MISS ", "UNCHECKED": " ---- "}.get(c["verdict"], "      ")
            text = c["text"] if len(c["text"]) <= 96 else c["text"][:93] + "..."
            lines.append(f"[{mark}] {text}")
    return "\n".join(lines)
