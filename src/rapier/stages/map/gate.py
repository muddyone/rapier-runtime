"""map_gate: region labels and the map gate, decided in code from the run's record.

SPARRING spec v1.3 §3.7, "Each region gets a probe and an attack" and "The map
gate". No model is asked about any of this.

Region labels:
- **UNPROBED**: no on-contract generation call scoped to the region succeeded.
- **HELD**: probed, its completeness attack ran on-contract on a vendor other
  than the Generator's, and no candidate was both new and cited (class a found,
  or class b named). Speculative (class c) candidates never break HELD.
- **YIELDING**: anything else: a landed attack, no attack, an off-contract or
  crashed attack, or an attack that was not independent.

Overall: UNPROBED if any region is; HELD only if every region is HELD and, for a
drafted frame, the frame-attack ran independently; otherwise YIELDING.

Gate: FAIL on a fabricated class a anchor or a frame not recorded before
generation; REVIEW on an overall label other than HELD, more than half the
items in class c, a skipped frame-attack, or a citation check that did not run;
PASS otherwise. Capture-recapture is never read here.
"""
from __future__ import annotations

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from . import _common as C

_PRE_FRAME = {"frame-draft", "frame-attack"}


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
    a = c.get("anchor") or {}
    cited = (a.get("class") == "a" and a.get("citation_check") == "found") or \
            (a.get("class") == "b" and a.get("citation_check") == "named")
    return bool(c.get("new")) and cited


def evaluate(m: dict) -> dict:
    """Compute labels, the gate verdict and the controls that did not run."""
    regions = C.region_list(m)
    attacks = m.get("attacks") or {}
    labels: dict[str, dict] = {}
    did_not_run: list[str] = []
    for r in regions:
        rid = r["id"]
        a = attacks.get(rid)
        why: list[str] = []
        if not _probed(m, rid):
            status = (m.get("probes") or {}).get(rid, {}).get("generate", "not-run")
            labels[rid] = {"label": "UNPROBED", "why": [f"no on-contract generation call ({status})"]}
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
                why.append(f"the attack landed {len(landed)} new cited item(s)")
        labels[rid] = {"label": "YIELDING" if why else "HELD", "why": why}

    fa = m.get("frame_attack") or {}
    frame = m.get("frame") or {}
    fa_ok = fa.get("status") == "ran" and fa.get("independent")
    if frame and fa.get("status") != "ran":
        did_not_run.append(f"frame-attack ({fa.get('status', 'not run')}: {fa.get('note', '')})")
    if any(l["label"] == "UNPROBED" for l in labels.values()) or not regions:
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
    if not frame_recorded_before_generation(m):
        fail.append("the search frame was not recorded before generation")
    if overall != "HELD":
        review.append(f"the overall label is {overall}")
    if items and n_c * 2 > len(items):
        review.append(f"more than half the items are speculative (class c: {n_c} of {len(items)})")
    if frame and not fa_ok:
        review.append("the frame-attack was skipped or not independent")
    if items and (m.get("citation_check") or {}).get("status") != "ran":
        review.append("the citation check did not run")
        did_not_run.append("citation check")
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
