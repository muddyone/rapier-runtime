#!/usr/bin/env python3
"""spar-verify-gate.py — the /spar convergence-gate grounding check.

Takes the load-bearing artifacts a SPARRING ceremony is about to converge on,
resolves each against external truth via the grounding verifier (verify_grounding.py),
and returns a GATE DECISION the skill reads before firing the both-must-agree close.

This is what turns the verifiable-artifact rule from honor-system into enforced:
"claims to cite a checkable thing" -> "the checkable thing was checked."

Design (per docs/plans/sparring-artifact-verification-spec.md):
- Gate-time, load-bearing-only by default (verifying every utterance is too slow).
- Verify against external truth, never the same brain (the verifier's backends do this).
- FAIL-SOFT: any backend/infra error -> that artifact is `unverified-not-checked`;
  the gate NEVER blocks on infrastructure and NEVER crashes the ceremony.

Gate decision (over LOAD-BEARING artifacts only):
- any `refuted`            -> GATE=blocked   (a load-bearing artifact didn't hold; address or record disagreement-at-cap)
- else any `unverifiable`
       or `unverified-not-checked` -> GATE=flagged  (converge allowed, but the artifact isn't standing on checked ground)
- else                     -> GATE=clean     (every load-bearing artifact verified)

Input: a run dir with `artifacts.json` (list), optionally `pack.md` (persisted pack,
applied as pack_text to artifacts that lack it). Or artifacts on stdin with --pack.
  artifact = {concern_id, artifact_ref, concern_text, type?, load_bearing?, pack_text?}

Output: writes `verdicts.json` to the run dir and prints a machine-readable
`---SPAR-VERIFY---` summary block (GATE / GROUNDING_RATE / THEATER_FLAGS + per-artifact).

Usage:
  spar-verify-gate.py --run-dir docs/spars/<run> [--judge] [--map-claims]
  echo '<artifacts json>' | spar-verify-gate.py --pack pack.md
  spar-verify-gate.py --self-test
"""
import argparse, json, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import verify_grounding as vg  # noqa: E402

# grounding-code (verifier) -> gate status (spec §5)
STATUS = {
    "GROUNDED_VERIFIED": "verified",
    "GROUNDED_REFUTED": "refuted",
    "UNGROUNDED": "unverifiable",            # no checkable artifact = the theater signature
    "UNVERIFIED_NOT_CHECKED": "unverified-not-checked",
}


def verify_artifact(a, judge=False, map_claims=False):
    """Fail-soft single-artifact verify. Returns the spec verdict dict; never raises."""
    try:
        v = vg.verify_concern(a, judge=judge, map_claims=map_claims)
        status = STATUS.get(v["grounding"], "unverified-not-checked")
        return {
            "concern_id": a.get("concern_id") or a.get("id") or "",
            "artifact_ref": v["artifact_ref"],
            "type": v["artifact_type"],
            "claim": (a.get("concern_text") or "")[:280],
            "backend": v["backend"],
            "status": status,
            "supports_claim": v.get("supports_claim"),
            "evidence": v["evidence"],
            "load_bearing": bool(a.get("load_bearing", True)),
        }
    except Exception as e:  # fail-soft: infra error never blocks the ceremony
        return {
            "concern_id": a.get("concern_id") or a.get("id") or "",
            "artifact_ref": a.get("artifact_ref", ""),
            "type": a.get("type") or "unknown",
            "claim": (a.get("concern_text") or "")[:280],
            "backend": "error",
            "status": "unverified-not-checked",
            "supports_claim": None,
            "evidence": f"verifier error (fail-soft): {e}",
            "load_bearing": bool(a.get("load_bearing", True)),
        }


def decide(verdicts):
    """Gate decision + grounding metrics over load-bearing artifacts."""
    lb = [v for v in verdicts if v["load_bearing"]]
    counts = {"verified": 0, "refuted": 0, "unverifiable": 0, "unverified-not-checked": 0}
    for v in lb:
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    decided = counts["verified"] + counts["refuted"] + counts["unverifiable"]
    grounding_rate = round(counts["verified"] / decided, 4) if decided else None
    theater_flags = counts["refuted"] + counts["unverifiable"]
    if counts["refuted"]:
        gate = "blocked"
    elif counts["unverifiable"] or counts["unverified-not-checked"]:
        gate = "flagged"
    else:
        gate = "clean"
    # Durability guardrail (locks the `unverifiable -> flag` invariant against a
    # future edit to the counting above): an unverifiable or refuted load-bearing
    # artifact MUST stay in the grounding_rate denominator and in theater_flags,
    # and MUST NOT let the gate read "clean". These are exactly the properties a
    # downstream reader (and the ledger) relies on to tell verified ground from
    # flagged ground -- assert them so they can't silently regress.
    assert decided == counts["verified"] + counts["refuted"] + counts["unverifiable"], \
        "unverifiable dropped from the grounding_rate denominator"
    assert theater_flags == counts["refuted"] + counts["unverifiable"], \
        "unverifiable dropped from theater_flags"
    assert not (gate == "clean" and theater_flags > 0), \
        "a flagged/refuted artifact must never read as a clean gate"
    assert not (grounding_rate == 1.0 and theater_flags > 0), \
        "grounding_rate cannot be perfect while theater_flags > 0"
    return {
        "gate": gate,
        "load_bearing_n": len(lb),
        "counts": counts,
        "grounding_rate": grounding_rate,
        "theater_flags": theater_flags,
    }


def run(artifacts, pack_text="", judge=False, map_claims=False):
    for a in artifacts:
        if pack_text and not a.get("pack_text"):
            a["pack_text"] = pack_text
    verdicts = [verify_artifact(a, judge=judge, map_claims=map_claims) for a in artifacts]
    return verdicts, decide(verdicts)


def print_summary(verdicts, summary, phase=""):
    print("---SPAR-VERIFY---")
    if phase:
        print(f"PHASE={phase}")
    print(f"GATE={summary['gate']}")
    gr = summary["grounding_rate"]
    print(f"GROUNDING_RATE={'' if gr is None else gr}")
    print(f"THEATER_FLAGS={summary['theater_flags']}")
    print(f"LOAD_BEARING_N={summary['load_bearing_n']}")
    c = summary["counts"]
    print(f"COUNTS=verified:{c['verified']},refuted:{c['refuted']},"
          f"unverifiable:{c['unverifiable']},unchecked:{c['unverified-not-checked']}")
    for v in verdicts:
        lb = "LB" if v["load_bearing"] else "  "
        print(f"  [{lb}] {v['status']:<22} {v['artifact_ref'][:40]:<40} {v['evidence'][:60]}")
    print("---ENDSPAR-VERIFY---")


def self_test():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    pack = "1. Target uptime 99.95%.\n2. Competitor credits.\n3. MSA §9.3 caps credits at 15%.\n"
    # all offline (pack backend; no network): a present fact, an absent fact, a no-artifact vibe
    artifacts = [
        {"concern_id": "a1", "artifact_ref": "#3", "concern_text": "schedule breaches the #3 cap",
         "pack_text": pack, "load_bearing": True},
        {"concern_id": "a2", "artifact_ref": "#9", "concern_text": "see pack #9",
         "pack_text": pack, "load_bearing": True},
        {"concern_id": "a3", "artifact_ref": "", "concern_text": "this just feels risky",
         "load_bearing": True},
        {"concern_id": "a4", "artifact_ref": "#1", "concern_text": "uptime target per #1",
         "pack_text": pack, "load_bearing": False},  # not load-bearing -> excluded from gate
    ]
    verdicts, summary = run(artifacts)
    by = {v["concern_id"]: v["status"] for v in verdicts}
    check("present pack fact -> verified", by["a1"] == "verified")
    check("absent pack fact -> refuted", by["a2"] == "refuted")
    check("no artifact -> unverifiable", by["a3"] == "unverifiable")
    check("a refuted load-bearing artifact blocks the gate", summary["gate"] == "blocked")
    check("grounding_rate over load-bearing = 1/3", summary["grounding_rate"] == round(1/3, 4))
    check("theater_flags = refuted + unverifiable = 2", summary["theater_flags"] == 2)
    check("non-load-bearing artifact excluded from gate metrics", summary["load_bearing_n"] == 3)
    # fail-soft: a malformed artifact must not crash, lands unverified-not-checked
    bad = verify_artifact({"concern_id": "x", "artifact_ref": object()})
    check("malformed artifact fails soft", bad["status"] == "unverified-not-checked")
    # all-clean gate
    _, clean = run([{"concern_id": "c", "artifact_ref": "#3", "concern_text": "the #3 cap",
                     "pack_text": pack, "load_bearing": True}])
    check("all load-bearing verified -> clean", clean["gate"] == "clean")
    # unverifiable WITHOUT any refuted: the artifact must still flag the gate
    # (never clean), stay counted in theater_flags, and stay in the denominator.
    _, unv = run([
        {"concern_id": "u1", "artifact_ref": "", "concern_text": "best practice says so",
         "load_bearing": True},                       # no artifact -> unverifiable
        {"concern_id": "u2", "artifact_ref": "#1", "concern_text": "uptime per #1",
         "pack_text": pack, "load_bearing": True},     # resolves -> verified
    ])
    check("unverifiable (no refuted) -> flagged, never clean", unv["gate"] == "flagged")
    check("unverifiable counted in theater_flags", unv["theater_flags"] == 1)
    check("unverifiable kept in denominator (rate = 1/2)", unv["grounding_rate"] == 0.5)
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="/spar convergence-gate grounding check.")
    ap.add_argument("--run-dir", help="ceremony dir with artifacts.json (+ optional pack.md); verdicts.json written here")
    ap.add_argument("--phase", default="",
                    help="phase label (full-sparring): reads artifacts.<phase>.json, writes verdicts.<phase>.json")
    ap.add_argument("--pack", help="path to a pack file applied as pack_text to artifacts lacking it")
    ap.add_argument("--judge", action="store_true", help="enable dual-substrate supports-claim judge (needs keys)")
    ap.add_argument("--map-claims", action="store_true", help="map prose concerns to pack facts (needs pack + key)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())

    pack_text = ""
    artifacts = None
    afile = f"artifacts.{args.phase}.json" if args.phase else "artifacts.json"
    vfile = f"verdicts.{args.phase}.json" if args.phase else "verdicts.json"
    if args.run_dir:
        apath = os.path.join(args.run_dir, afile)
        if not os.path.isfile(apath):
            sys.exit(f"[spar-verify] no {afile} in {args.run_dir}")
        artifacts = json.load(open(apath))
        ppath = args.pack or os.path.join(args.run_dir, "pack.md")
        if os.path.isfile(ppath):
            pack_text = open(ppath).read()
    else:
        artifacts = json.loads(sys.stdin.read())
        if args.pack and os.path.isfile(args.pack):
            pack_text = open(args.pack).read()

    verdicts, summary = run(artifacts, pack_text=pack_text, judge=args.judge, map_claims=args.map_claims)
    if args.run_dir:
        with open(os.path.join(args.run_dir, vfile), "w") as fh:
            json.dump({"summary": summary, "verdicts": verdicts}, fh, indent=2)
    print_summary(verdicts, summary, phase=args.phase)


if __name__ == "__main__":
    main()
