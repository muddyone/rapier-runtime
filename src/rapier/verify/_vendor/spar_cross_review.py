#!/usr/bin/env python3
"""spar-cross-review.py — the cross-vendor independent reviewer.

Given a decision PROBLEM (the pack) and a candidate RECOMMENDATION, produce an
independent cross-vendor critique: material would-ship objections the author (a
Claude ceremony) may not see on its own. This is the resolver-iteration study's
single review pass (run_study.run_r1), the CROSS arm — ported and decoupled.

Why cross-vendor: the SPARRING author is Claude, so a genuinely independent
challenge comes from a DIFFERENT substrate (GPT). Same-vendor self-review is a
weaker signal (the study's `same` arm); we prefer real cross-vendor and mark the
output when we have to fall back.

Reviewer selection (keys from the ENVIRONMENT only):
- OPENAI_API_KEY present            -> GPT reviewer, cross_vendor=true (default).
- only ANTHROPIC_API_KEY present    -> Claude reviewer, cross_vendor=false, WARN.
- neither key                       -> fail-soft degraded shape, exit 0.
`--reviewer claude|gpt` forces a vendor (still degrades if its key is absent).

Fail-soft everywhere: an absent key or a model/network error degrades to a
documented `degraded` output and exit 0 — never a traceback that aborts the
ceremony.

Input (one of):
  --run-dir DIR        reads pack.md (problem) + recommendation.md; writes review.json
  --problem F --recommendation F
  stdin JSON           {"problem": "...", "recommendation": "..."}
Optional:
  --prev FILE          JSON list of prior objections -> runs the RN (settle) prompt
  --reviewer claude|gpt

Output JSON:
  {"reviewer_vendor": "gpt"|"claude"|null, "cross_vendor": bool,
   "objections": [{"handle": str, "text": str}],
   "material_objections_remaining": bool, "degraded": str (only if degraded)}

Usage:
  spar-cross-review.py --run-dir docs/spars/<run>
  echo '{"problem":"...","recommendation":"..."}' | spar-cross-review.py
  spar-cross-review.py --self-test
"""
import argparse, json, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import lib_llm as L  # noqa: E402

# --------------------------------------------------------------------------- prompts (verbatim from run_study.py)
CHAL_SYS = (
    "You are an adversarial Challenger reviewing a recommendation another expert produced for a "
    "hard decision problem. Work from a DIFFERENT angle and evidence base than the author. Your job "
    "is to find MATERIAL would-ship flaws: errors that would change the decision or ship a real defect "
    "-- not nitpicks, not style, not 'consider also' additions. Be polite, not pleasing: do not smooth "
    "the road, and do not soften a real objection. But be honest in BOTH directions -- if the "
    "recommendation has no remaining material flaw, say so plainly; do NOT invent objections to prolong "
    "the exchange, and do NOT concede prematurely just to be agreeable. Assign each objection a short "
    "stable handle (snake_case) so it can be tracked across rounds."
)
CHAL_R1_USER = (
    "PROBLEM:\n{problem}\n\nRECOMMENDATION UNDER REVIEW:\n{rec}\n\n"
    "Return JSON: {{\"objections\": [{{\"handle\": str, \"text\": str}}], "
    "\"material_objections_remaining\": bool}}"
)
CHAL_RN_USER = (
    "PROBLEM:\n{problem}\n\nREVISED RECOMMENDATION (the author just revised it):\n{rec}\n\n"
    "YOUR OBJECTIONS FROM THE PREVIOUS ROUND (handles + text):\n{prev}\n\n"
    "Re-evaluate the revised recommendation. For each previous objection, decide if the revision "
    "ADEQUATELY addressed it. Raise any genuinely NEW material flaw the revision introduced or that "
    "you now see. Return JSON: {{\"objections\": [{{\"handle\": str, \"text\": str, "
    "\"status\": \"persists\"|\"new\"}}], \"resolved_last_round\": [handle, ...], "
    "\"material_objections_remaining\": bool}}"
)


def fmt_objections(objs):
    return "\n".join(f"- [{o.get('handle','?')}] {o.get('text','')}" for o in objs) or "(none)"


def _coerce_obj(x, list_key):
    """Tolerant JSON sometimes yields a bare list when an object was asked for; wrap it."""
    if isinstance(x, dict):
        return x
    if isinstance(x, list):
        return {list_key: x, "material_objections_remaining": bool(x)}
    return {}


def challenger_call(vendor, system, user):
    """One challenger pass via the ported lib_llm (verbatim vendor dispatch from run_study)."""
    if vendor == "claude":
        return L.claude_json(L.CLAUDE_MODEL, system, user, max_tokens=6000, temperature=0.8)
    return L.gpt_json(L.GPT_MODEL, system, user, max_completion_tokens=16000)


# --------------------------------------------------------------------------- reviewer selection
def resolve_reviewer(requested=None):
    """Pick the reviewer vendor from env keys. Returns (vendor|None, warn|None).

    Author is Claude, so cross_vendor is only true for a GPT reviewer.
    """
    keys = L.keys_present()
    want = requested or "gpt"
    if want == "gpt":
        if keys["openai"]:
            return "gpt", None
        if keys["anthropic"]:
            return "claude", "OPENAI_API_KEY absent; falling back to a Claude reviewer (NOT cross-vendor)."
        return None, "no api keys"
    # want == "claude"
    if keys["anthropic"]:
        return "claude", None
    if keys["openai"]:
        return "gpt", "ANTHROPIC_API_KEY absent; using a GPT reviewer instead."
    return None, "no api keys"


# --------------------------------------------------------------------------- review
def review(problem, recommendation, prev=None, requested_vendor=None):
    reviewer_vendor, warn = resolve_reviewer(requested_vendor)
    if reviewer_vendor is None:
        return {"reviewer_vendor": None, "cross_vendor": False, "objections": [],
                "material_objections_remaining": False, "degraded": "no api keys"}
    if warn:
        sys.stderr.write("WARN spar-cross-review: " + warn + "\n")
    cross_vendor = (reviewer_vendor == "gpt")
    if prev:
        user = CHAL_RN_USER.format(problem=problem, rec=recommendation, prev=fmt_objections(prev))
    else:
        user = CHAL_R1_USER.format(problem=problem, rec=recommendation)
    try:
        out = challenger_call(reviewer_vendor, CHAL_SYS, user)
    except Exception as e:  # noqa: BLE001 — fail-soft: a model/network error never aborts the ceremony
        sys.stderr.write(f"WARN spar-cross-review: reviewer error (fail-soft): {e}\n")
        return {"reviewer_vendor": reviewer_vendor, "cross_vendor": cross_vendor,
                "objections": [], "material_objections_remaining": False,
                "degraded": f"reviewer error: {str(e)[:200]}"}
    out = _coerce_obj(out, "objections")
    raw = out.get("objections", []) if isinstance(out, dict) else []
    objections = [{"handle": str(o.get("handle", "?")), "text": str(o.get("text", ""))}
                  for o in raw if isinstance(o, dict)]
    material = bool(out.get("material_objections_remaining"))
    return {"reviewer_vendor": reviewer_vendor, "cross_vendor": cross_vendor,
            "objections": objections, "material_objections_remaining": material}


# --------------------------------------------------------------------------- output
def print_summary(result):
    print("---SPAR-REVIEW---")
    print(f"REVIEWER_VENDOR={result.get('reviewer_vendor')}")
    print(f"CROSS_VENDOR={str(result.get('cross_vendor', False)).lower()}")
    if result.get("degraded"):
        print(f"DEGRADED={result['degraded']}")
    objs = result.get("objections", [])
    print(f"N_OBJECTIONS={len(objs)}")
    print(f"MATERIAL_OBJECTIONS_REMAINING={str(result.get('material_objections_remaining', False)).lower()}")
    for o in objs:
        print(f"  [{o.get('handle','?')}] {o.get('text','')[:70]}")
    print("---ENDSPAR-REVIEW---")


# --------------------------------------------------------------------------- self-test
def self_test():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    # Save originals so we can monkeypatch lib_llm offline.
    orig_keys = L.keys_present
    orig_cj = L.claude_json
    orig_gj = L.gpt_json

    canned = {"objections": [{"handle": "burn_rate", "text": "The runway math ignores the hiring ramp."},
                             {"handle": "vendor_lockin", "text": "Single-vendor dependency is unaddressed."}],
              "material_objections_remaining": True}

    try:
        # 1) Both keys present -> GPT reviewer, cross_vendor true, canned objections.
        L.keys_present = lambda: {"anthropic": True, "openai": True}
        L.gpt_json = lambda *a, **k: canned
        L.claude_json = lambda *a, **k: {"objections": [], "material_objections_remaining": False}
        r = review("Problem P", "Recommendation R")
        check("default reviewer is gpt", r["reviewer_vendor"] == "gpt")
        check("gpt reviewer is cross_vendor", r["cross_vendor"] is True)
        check("objections shape {handle,text}",
              r["objections"] and all(set(o) == {"handle", "text"} for o in r["objections"]))
        check("material_objections_remaining carried", r["material_objections_remaining"] is True)
        check("no degraded key on happy path", "degraded" not in r)

        # 2) Only Anthropic key -> Claude reviewer, cross_vendor false.
        L.keys_present = lambda: {"anthropic": True, "openai": False}
        r2 = review("P", "R")
        check("no OpenAI key -> claude reviewer", r2["reviewer_vendor"] == "claude")
        check("claude fallback is NOT cross_vendor", r2["cross_vendor"] is False)

        # 3) No keys -> degraded shape, no raise.
        L.keys_present = lambda: {"anthropic": False, "openai": False}
        r3 = review("P", "R")
        check("no keys -> reviewer_vendor null", r3["reviewer_vendor"] is None)
        check("no keys -> degraded='no api keys'", r3.get("degraded") == "no api keys")
        check("no keys -> empty objections", r3["objections"] == [])
        check("no keys -> not cross_vendor", r3["cross_vendor"] is False)

        # 4) --prev drives the RN (settle) prompt path (still returns valid shape).
        L.keys_present = lambda: {"anthropic": True, "openai": True}
        L.gpt_json = lambda *a, **k: {"objections": [{"handle": "burn_rate", "text": "still unaddressed", "status": "persists"}],
                                      "resolved_last_round": ["vendor_lockin"],
                                      "material_objections_remaining": True}
        r4 = review("P", "R2", prev=[{"handle": "burn_rate", "text": "..."}])
        check("settle-round objections normalized to {handle,text}",
              r4["objections"] == [{"handle": "burn_rate", "text": "still unaddressed"}])

        # 5) Bare-list response is coerced, not crashed.
        L.gpt_json = lambda *a, **k: [{"handle": "x", "text": "y"}]
        r5 = review("P", "R")
        check("bare-list response coerced", r5["objections"] == [{"handle": "x", "text": "y"}]
              and r5["material_objections_remaining"] is True)

        # 6) A model error degrades soft (no raise).
        def boom(*a, **k):
            raise RuntimeError("simulated 500")
        L.gpt_json = boom
        r6 = review("P", "R")
        check("model error -> degraded, no raise", r6.get("degraded", "").startswith("reviewer error"))
        check("model error keeps reviewer_vendor", r6["reviewer_vendor"] == "gpt")

        # 7) Forced --reviewer claude with only openai key -> substitutes gpt with warn.
        L.keys_present = lambda: {"anthropic": False, "openai": True}
        L.gpt_json = lambda *a, **k: canned
        r7 = review("P", "R", requested_vendor="claude")
        check("forced claude w/o anthropic key substitutes gpt", r7["reviewer_vendor"] == "gpt")
    finally:
        L.keys_present = orig_keys
        L.claude_json = orig_cj
        L.gpt_json = orig_gj

    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------- main
def _read(path):
    # Tolerate non-UTF-8 bytes in the input (e.g. a CP1252 em-dash / smart quote
    # that leaked in via corrupted upstream content) instead of crashing with an
    # unhandled UnicodeDecodeError. Robustness > fidelity for a stray byte.
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser(description="Cross-vendor independent reviewer for a SPARRING recommendation.")
    ap.add_argument("--run-dir", help="ceremony dir with pack.md + recommendation.md; writes review.json")
    ap.add_argument("--problem", help="path to a problem/pack file")
    ap.add_argument("--recommendation", help="path to the candidate recommendation file")
    ap.add_argument("--prev", help="JSON file: list of prior objections -> runs the settle (RN) prompt")
    ap.add_argument("--reviewer", choices=["claude", "gpt"], help="force a reviewer vendor")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    problem = recommendation = None
    out_path = None
    if args.run_dir:
        ppath = os.path.join(args.run_dir, "pack.md")
        rpath = os.path.join(args.run_dir, "recommendation.md")
        if not os.path.isfile(ppath) or not os.path.isfile(rpath):
            sys.exit(f"[spar-cross-review] need pack.md + recommendation.md in {args.run_dir}")
        problem, recommendation = _read(ppath), _read(rpath)
        out_path = os.path.join(args.run_dir, "review.json")
    elif args.problem and args.recommendation:
        problem, recommendation = _read(args.problem), _read(args.recommendation)
    else:
        data = json.loads(sys.stdin.read())
        problem, recommendation = data.get("problem", ""), data.get("recommendation", "")

    prev = None
    if args.prev and os.path.isfile(args.prev):
        try:
            prev = json.loads(_read(args.prev))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"WARN spar-cross-review: could not read --prev ({e}); ignoring.\n")

    result = review(problem, recommendation, prev=prev, requested_vendor=args.reviewer)

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(result, fh, indent=2)
    print_summary(result)


if __name__ == "__main__":
    main()
