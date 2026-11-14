"""map_gate: unit labels and the map gate, decided in code from the run's record.

SPARRING spec v1.4 §3.7, "Each region gets a probe and an attack", "Frame size
and blocks" and "The map gate". No model is asked about any of this. Labels are
per unit (a region, or a declared block); a block's label claims nothing about
any single region in it.

Unit labels:
- **UNPROBED**: no on-contract generation call scoped to the unit succeeded.
- **HELD**: probed, its completeness attack ran on-contract on a vendor other
  than the Generator's, and no new candidate CLAIMED class a or b, whatever the
  citation check then found (v1.4: demotion lowers an item's claim, never a
  label's). Speculative (class c) candidates never break HELD.
- **YIELDING**: anything else: a new cited candidate, no attack, an
  off-contract or crashed attack, or an attack that was not independent.

Overall: UNPROBED if any unit is, or the run stopped before generation; HELD
only if every unit is HELD and, for a drafted frame, the frame-attack ran
independently; otherwise YIELDING.

Gate: FAIL on a fabricated class a anchor or a frame not recorded before
generation; REVIEW on an overall label other than HELD, more than half the
items in class c, a skipped frame-attack, a citation check that did not run, an
incomplete run (stopped before generation, at the dollar cap, or with any
unit's calls refused), or more than 10% of class a anchors misquoted; PASS
otherwise. Capture-recapture is never read here.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from . import _common as C

_PRE_FRAME = {"frame-draft", "frame-block", "frame-attack"}
MISQUOTE_REVIEW_SHARE = 0.10


def frame_recorded_before_generation(m: dict) -> bool:
    f = m.get("frame")
    if not f:
        return False
    before = (m.get("calls") or [])[: f.get("recorded_at_call", 0)]
    return all(c.get("purpose") in _PRE_FRAME for c in before)


def _probed(m: dict, rid: str) -> bool:
    ok_call = any(c.get("purpose") == "generate" and c.get("region") == rid and c.get("ok")
                  for c in (m.get("calls") or []))
    return ok_call and ((m.get("probes") or {}).get(rid, {}).get("generate") == "ran")


def _landed(c: dict) -> bool:
    """A new candidate that CLAIMS a citation (class a or b), whatever the check
    found. Older records without ``claimed_class`` fall back to the anchor's class."""
    claimed = c.get("claimed_class") or (c.get("anchor") or {}).get("downgraded_from") \
        or (c.get("anchor") or {}).get("class")
    return bool(c.get("new")) and claimed in ("a", "b")


def _stopped_calls(m: dict, uid: str) -> list[str]:
    return sorted({c.get("purpose", "?") for c in (m.get("calls") or [])
                   if c.get("region") == uid and c.get("stopped_by")})


def evaluate(m: dict) -> dict:
    """Compute labels, the gate verdict and the controls that did not run."""
    stopped = m.get("stopped")
    regions = list((m.get("frame") or {}).get("units") or []) if not stopped else []
    attacks = m.get("attacks") or {}
    labels: dict[str, dict] = {}
    did_not_run: list[str] = []
    for r in regions:
        rid = r["id"]
        a = attacks.get(rid)
        why: list[str] = []
        cut = _stopped_calls(m, rid)
        if cut:
            why.append(f"stopped at the dollar cap: its {', '.join(cut)} call(s) were refused")
        if not _probed(m, rid):
            status = (m.get("probes") or {}).get(rid, {}).get("generate", "not-run")
            labels[rid] = {"label": "UNPROBED", "why": [f"no on-contract generation call ({status})"] + why}
            if status != "ran":
                did_not_run.append(f"generation for {rid} ({status})")
            continue
        if a is None:
            why.append("no completeness attack ran")
            did_not_run.append(f"completeness attack for {rid} (not run)")
        elif a.get("status") != "ran":
            why.append(f"the completeness attack did not count ({a.get('status')}: {a.get('note')})")
            did_not_run.append(f"completeness attack for {rid} ({a.get('status')})")
        else:
            if not a.get("independent"):
                why.append("the attack ran on the Generator's own vendor (not independent)")
            landed = [c for c in a.get("candidates", []) if _landed(c)]
            if landed:
                why.append(f"the attack raised {len(landed)} new candidate(s) claiming a citation")
        labels[rid] = {"label": "YIELDING" if why else "HELD", "why": why}

    fa = m.get("frame_attack") or {}
    frame = m.get("frame") or {}
    fa_ok = fa.get("status") == "ran" and fa.get("independent")
    if frame and fa.get("status") != "ran":
        did_not_run.append(f"frame-attack ({fa.get('status', 'not run')}: {fa.get('note', '')})")
    if any(l["label"] == "UNPROBED" for l in labels.values()) or not regions or stopped:
        overall = "UNPROBED"
    elif all(l["label"] == "HELD" for l in labels.values()) and (frame.get("source") != "drafted" or fa_ok):
        overall = "HELD"
    else:
        overall = "YIELDING"

    items = m.get("items") or []
    fabricated = [i["id"] for i in items if (i.get("anchor") or {}).get("citation_check") == "fabricated"]
    n_c = sum(1 for i in items if (i.get("anchor") or {}).get("class") == "c")
    fail, review = [], []
    if fabricated:
        fail.append(f"fabricated class a anchor(s): {', '.join(fabricated)}")
    generated = any(c.get("purpose") not in _PRE_FRAME for c in (m.get("calls") or []))
    if stopped:
        did_not_run.append(f"the map (stopped before generation: {stopped['cause']})")
        review.append("incomplete run: stopped before generation")
    if not frame and not stopped:
        if generated:
            fail.append("the search frame was not recorded before generation")
        else:
            # No frame and nothing generated: the framer did not run, so it decided
            # nothing. Never a FAIL (verifier M-1); the run defers.
            did_not_run.append(f"search frame ({m.get('frame_error') or 'no frame was declared'})")
    elif frame and not frame_recorded_before_generation(m):
        fail.append("the search frame was not recorded before generation")
    if overall != "HELD":
        review.append(f"the overall label is {overall}")
    if items and n_c * 2 > len(items):
        review.append(f"more than half the items are speculative (class c: {n_c} of {len(items)})")
    if frame and not fa_ok and not stopped:
        review.append("the frame-attack was skipped or not independent")
    if items and (m.get("citation_check") or {}).get("status") != "ran":
        review.append("the citation check did not run")
        did_not_run.append("citation check")
    stop = m.get("budget_stopped")
    cut_units = [rid for rid, l in labels.items() if any("dollar cap" in w for w in l["why"])]
    if stop or cut_units:
        if stop:
            did_not_run.append(f"the run stopped at its ${stop['cap_usd']:.2f} dollar cap "
                               f"(${stop['spent_usd']:.2f} spent)")
        review.append("incomplete run: stopped at the dollar cap"
                      + (f" (units cut short: {', '.join(cut_units)})" if cut_units else ""))
    claimed_a = [i for i in items if (i.get("anchor") or {}).get("class") == "a"
                 or (i.get("anchor") or {}).get("downgraded_from") == "a"]
    misquoted = [i for i in claimed_a if (i.get("anchor") or {}).get("misquoted")]
    if claimed_a and len(misquoted) > MISQUOTE_REVIEW_SHARE * len(claimed_a):
        review.append(f"more than 10% of class a anchors are misquoted ({len(misquoted)} of "
                      f"{len(claimed_a)})")
    verdict = "FAIL" if fail else ("REVIEW" if review else "PASS")
    return {"verdict": verdict, "fail": fail, "review": review, "labels": labels,
            "overall": overall, "did_not_run": did_not_run}


@register_stage("map_gate")
class MapGateStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        g = evaluate(m)
        m["gate"] = g
        env.verdict = g["verdict"]
        env.add_trace("map_gate", self.kind,
                      f"verdict={g['verdict']} overall={g['overall']} did_not_run={len(g['did_not_run'])}")
        return env
