#!/usr/bin/env python3
"""spar-definitiveness-gate.py — the correctness gate (the definitiveness rule).

A faithful, decoupled port of the resolver-iteration study's bucket_gate.py, run on
ONE (problem, recommendation) pair instead of a study corpus.

THE RULE. Every HARD SPECIFIC a recommendation states (a number, money amount,
percentage, rate, count, date, duration, definite magnitude) is read as DEFINITIVE
unless marked otherwise, so each must fall into one bucket:

  bucket 1  TRACEABLE      — follows from the PROBLEM's given facts (echoed verbatim,
                             a calendar span of problem dates, or derivable from stated
                             numbers). May stand unmarked.
  bucket 2  CONTEXTUALIZED — explicitly flagged as an estimate/example/assumption/
                             approximation/thing-to-verify.
  bucket 3  NEITHER        — unmarked AND not traceable = an implicit false-definitive.
                             GATE FAILURE.

DIVISION OF LABOR. The MODEL does perception — enumerate the specifics, judge how each
is presented, and PROPOSE a derivation from problem-stated numbers. The CALCULATOR does
the verdict — a proposed derivation only counts if every input is quoted verbatim from
the problem AND the arithmetic reproduces the value. No model is ever asked to assert
"traceable"; Python decides.

CROSS-VENDOR. Presentation and traceability are judged by BOTH Claude and GPT; the
verdict is the CONSERVATIVE union — a specific fails only if unmarked by BOTH vendors AND
traceable by NEITHER — so the gate does not over-fail on detector noise. Where the two
vendors split on presentation (fact vs estimate), the specific is routed to REVIEW_SPLIT
rather than auto-failed. Flagged specifics get a targeted re-ask: a fully problem-grounded
re-derivation recovers to traceable; one that only derives via an unstated assumption
stays flagged, now carrying a trust RIDER naming the assumption.

FAIL-SOFT (keys from the ENVIRONMENT only):
- both keys           -> cross-vendor (Claude + GPT), cross_vendor=true.
- ANTHROPIC only      -> Claude-only present+trace, cross_vendor=false (reduced detector
                         confidence; no split can be detected with one vendor).
- no ANTHROPIC key    -> answer_verdict "unchecked" (the enumerate/kind/re-ask passes are
                         Claude-primary); degraded note; exit 0.
A model/network error at any pass degrades toward "unchecked" rather than crashing.

Input (one of):
  --run-dir DIR        reads pack.md (problem) + recommendation.md; writes definitiveness.json
  --problem F --recommendation F
  stdin JSON           {"problem": "...", "recommendation": "..."}

Output JSON:
  {"answer_verdict": "PASS"|"REVIEW"|"FAIL"|"unchecked", "cross_vendor": bool,
   "n_specifics": int,
   "buckets": {"traceable":n,"contextualized":n,"prescriptive":n,"REVIEW_SPLIT":n,"BUCKET3_FAIL":n},
   "failures": [{"text","value","claim","reason"}], "review_splits": [{...}],
   "recovered_on_reask": int, "rider_lines": [str], "rows": [...]}

Usage:
  spar-definitiveness-gate.py --run-dir docs/spars/<run>
  echo '{"problem":"...","recommendation":"..."}' | spar-definitiveness-gate.py
  spar-definitiveness-gate.py --self-test
"""
import argparse, json, os, sys, re, ast, operator, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import lib_llm as L  # noqa: E402

REL_TOL = 0.05   # 5% tolerance (rounding/approximation); matches the study's _close / range-band

# ==================================================================== helpers (re-ported from step3b_showwork.py)
def _close(a, b):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= REL_TOL


def _number_in_text(val, text):
    """Is the numeric value asserted anywhere in the prose (tolerant of formatting/commas/units)?"""
    if not isinstance(val, (int, float)):
        return False
    nums = re.findall(r"-?\d[\d,]*\.?\d*", text or "")
    for tok in nums:
        try:
            if _close(float(tok.replace(",", "")), val):
                return True
        except ValueError:
            continue
    return False


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


# ==================================================================== safe arithmetic (from bucket_gate.py)
# Arithmetic + a small whitelist of safe functions models actually use in derivations (round, min,
# max, abs, sum). No attribute access, no arbitrary calls.
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"round": round, "min": min, "max": max, "abs": abs, "sum": sum}


def _ev(n, env):
    if isinstance(n, ast.Constant):
        if isinstance(n.value, bool) or not isinstance(n.value, (int, float)):
            raise ValueError("non-numeric const")
        return float(n.value)
    if isinstance(n, ast.Name):
        if n.id not in env or not isinstance(env[n.id], (int, float)) or isinstance(env[n.id], bool):
            raise ValueError(f"unbound:{n.id}")
        return float(env[n.id])
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.left, env), _ev(n.right, env))
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.operand, env))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
        args = [_ev(a, env) for a in n.args]
        if n.func.id == "round" and len(args) == 2:   # round's ndigits must be int, not float
            args[1] = int(args[1])
        return float(_FUNCS[n.func.id](*args))
    raise ValueError(f"disallowed:{type(n).__name__}")


def safe_eval_named(expr, env):
    """Evaluate arithmetic + whitelisted functions over named numeric vars. (value, names, ops, err)."""
    e = (expr or "").strip()
    if not e:
        return None, set(), [], "empty"
    try:
        node = ast.parse(e, mode="eval").body
    except Exception as ex:   # noqa: BLE001
        return None, set(), [], f"parse:{type(ex).__name__}"
    try:
        return float(_ev(node, env)), set(), [], None
    except Exception as ex:   # noqa: BLE001
        return None, set(), [], f"eval:{str(ex)[:40]}"


# ==================================================================== perception prompts (verbatim from bucket_gate.py)
ENUM_SYS = (
    "You audit a recommendation written for a hard decision problem. List every HARD SPECIFIC it states: "
    "a number, money amount, percentage, rate, count, date, duration, or definite factual magnitude the "
    "decision could rest on. For EACH give: the verbatim text as it appears; a single numeric value (for a "
    "range, the midpoint); a short description of what the number claims; and how it is PRESENTED — "
    "'definitive' if stated as a definite fact/figure, or 'contextualized' if EXPLICITLY hedged as an "
    "estimate, example, assumption, approximation, or something to verify (markers such as ~, about, "
    "roughly, approximately, 'assume', 'e.g.', 'for example', 'order of', 'ballpark', 'verify', 'subject "
    "to'). Exclude pure labels and enumerations (e.g. 'option 2', 'Phase 1', 'step 3') — those are not "
    "quantities. Be complete: every distinct stated quantity."
)
ENUM_USER = (
    "PROBLEM:\n{problem}\n\nRECOMMENDATION:\n{rec}\n\n"
    "Return JSON: {{\"specifics\": [{{\"text\": str, \"value\": number, \"claim\": str, "
    "\"presented_as\": \"definitive\"|\"contextualized\"}}]}}"
)

PRESENT_SYS = (
    "For each listed specific, judge how the recommendation PRESENTS it. Distinguish two kinds of wording, "
    "because they are NOT the same:\n"
    "  - 'definitive' — stated as a figure the reader is meant to rely on. CRITICAL: a precision hedge "
    "ALONE (~, about, roughly, approximately, 'around', 'on the order of') is STILL definitive — it only "
    "fuzzes the exact value while still asserting the magnitude as a real, relied-upon fact. '~$650k/month "
    "savings' is definitive: it claims there really is about that much.\n"
    "  - 'contextualized' — explicitly marked as NOT an established fact the reader should rely on: an "
    "assumption to supply ('assume X', 'if we assume'), an illustrative example ('e.g.', 'for example', "
    "'say, $X'), a placeholder to confirm ('verify against your actuals', 'TBD', 'subject to "
    "confirmation'), or an openly hypothetical figure.\n"
    "The test: does the wording tell the reader NOT to take this number as a given fact? Approximation is "
    "NOT contextualization. Judge presentation, not correctness.\n"
    "Calibration examples (a precision hedge stays definitive; only a basis flag is contextualized):\n"
    "  • \"~$650k/month in savings\" -> definitive (approximates a magnitude claimed real)\n"
    "  • \"roughly 30 engineers\" -> definitive (precision hedge)\n"
    "  • \"on the order of $2M\" -> definitive (precision hedge)\n"
    "  • \"a 22% renewal increase\" -> definitive (plain stated figure)\n"
    "  • \"assume a $50/unit cost\" -> contextualized (explicit assumption)\n"
    "  • \"for example, if churn is 5%\" -> contextualized (illustrative)\n"
    "  • \"a placeholder 20% margin, to be confirmed\" -> contextualized (flagged to confirm)\n"
    "  • \"budget ~$500k, but verify against your actuals\" -> contextualized (explicitly flagged to verify)\n"
    "  • \"say, 3 vendors\" -> contextualized (illustrative)"
)
PRESENT_USER = (
    "RECOMMENDATION:\n{rec}\n\nSPECIFICS:\n{items}\n\n"
    "Return JSON: {{\"labels\": [{{\"index\": int, \"presented_as\": \"definitive\"|\"contextualized\"}}]}}"
)

KIND_SYS = (
    "For each specific, classify it as DESCRIPTIVE or PRESCRIPTIVE:\n"
    "  - DESCRIPTIVE — a claim about the world, the situation, or a predicted outcome: a cost that will "
    "be incurred, a saving/advantage that will result, a quantity that exists, a rate that holds, how "
    "long something will take. It asserts something IS or WILL BE true, so it can be right or wrong.\n"
    "  - PRESCRIPTIVE — a parameter of the action the recommendation PROPOSES: a pilot length it advises "
    "running, a deadline it sets, a price it suggests charging, a staffing config it recommends, a target "
    "it says to hold. It is a CHOICE the plan makes, not a claim that can be false.\n"
    "When a number is both (a proposed action justified by a predicted magnitude), classify by what the "
    "number itself asserts. Judge the role of the number, not its correctness."
)
KIND_USER = (
    "RECOMMENDATION:\n{rec}\n\nSPECIFICS:\n{items}\n\n"
    "Return JSON: {{\"kinds\": [{{\"index\": int, \"kind\": \"descriptive\"|\"prescriptive\"}}]}}"
)

TRACE_SYS = (
    "You check whether each stated quantity can be derived using ONLY numbers that appear in the PROBLEM "
    "statement (the given facts). For each specific, either derive it or declare it underivable:\n"
    "  - If derivable, give inputs — each an object {{\"name\", \"value\", \"source_quote\"}} where "
    "source_quote is the VERBATIM phrase from the PROBLEM that states that number — and a single-line "
    "Python arithmetic expression over the input NAMES that computes the quantity, and the result. A "
    "quantity that is simply a number already stated in the problem has one input (itself).\n"
    "  - If it CANNOT be computed from problem-stated numbers alone — it needs an outside assumption, a "
    "market figure, or any number not in the problem — set derivable=false and give empty inputs/expr.\n"
    "NEVER invent a number that is not in the problem, and never fabricate a source_quote."
)
TRACE_USER = (
    "PROBLEM:\n{problem}\n\nSPECIFICS TO DERIVE:\n{items}\n\n"
    "Return JSON: {{\"derivations\": [{{\"index\": int, \"derivable\": bool, "
    "\"inputs\": [{{\"name\": str, \"value\": number, \"source_quote\": str}}], "
    "\"expr\": str, \"result\": number}}]}}"
)

EXPLAIN_SYS = (
    "A release gate flagged a specific figure in a recommendation as not obviously traceable to the "
    "problem's given facts. For each flagged figure, try hard to derive it using ONLY numbers stated in "
    "the PROBLEM, and be explicit about what you cannot ground:\n"
    "  - inputs_grounded: inputs whose value IS stated in the problem — each {name, value, source_quote} "
    "with source_quote the VERBATIM problem phrase.\n"
    "  - inputs_assumed: inputs you must ASSUME because the problem does NOT state them — each {name, "
    "value, why} (why = what it represents and why the figure needs it).\n"
    "  - expr: a single-line Python arithmetic expression over the input NAMES; result: its value.\n"
    "Never invent a source_quote — if a number is not in the problem it is ASSUMED, not grounded. If the "
    "figure cannot be reached even with assumptions, set derivable=false and use inputs_assumed to name "
    "what would be required."
)
EXPLAIN_USER = (
    "PROBLEM:\n{problem}\n\nFLAGGED FIGURES:\n{items}\n\n"
    "Return JSON: {{\"explanations\": [{{\"index\": int, \"derivable\": bool, "
    "\"inputs_grounded\": [{{\"name\": str, \"value\": number, \"source_quote\": str}}], "
    "\"inputs_assumed\": [{{\"name\": str, \"value\": number, \"why\": str}}], "
    "\"expr\": str, \"result\": number}}]}}"
)


def _items_block(specifics):
    return "\n".join(f"{i}. \"{s.get('text','')}\" — {s.get('claim','')} (value {s.get('value')})"
                     for i, s in enumerate(specifics))


# ==================================================================== model passes (single-shot, no caching)
def enumerate_specifics(problem, rec):
    out = L.claude_json(L.CLAUDE_MODEL, ENUM_SYS, ENUM_USER.format(problem=problem, rec=rec), max_tokens=3500)
    sp = out.get("specifics", []) if isinstance(out, dict) else []
    return sp[:40]


def present_labels(vendor, rec, specifics):
    """Presentation labels from a given vendor (precision-hedge != basis-flag; calibrated)."""
    items = _items_block(specifics)
    if vendor == "claude":
        out = L.claude_json(L.CLAUDE_MODEL, PRESENT_SYS, PRESENT_USER.format(rec=rec, items=items), max_tokens=2000)
    else:
        out = L.gpt_json(L.GPT_MODEL, PRESENT_SYS, PRESENT_USER.format(rec=rec, items=items))
    labels = out.get("labels", []) if isinstance(out, dict) else []
    return {int(x["index"]): str(x.get("presented_as", "definitive")).lower()
            for x in labels if isinstance(x, dict) and "index" in x}


def trace(vendor, problem, specifics):
    items = _items_block(specifics)
    if vendor == "claude":
        out = L.claude_json(L.CLAUDE_MODEL, TRACE_SYS, TRACE_USER.format(problem=problem, items=items), max_tokens=4000)
    else:
        out = L.gpt_json(L.GPT_MODEL, TRACE_SYS, TRACE_USER.format(problem=problem, items=items))
    ders = out.get("derivations", []) if isinstance(out, dict) else []
    return {int(d["index"]): d for d in ders if isinstance(d, dict) and "index" in d}


def kind_labels(rec, specifics):
    """descriptive (a factual/predictive claim, gate-relevant) vs prescriptive (a proposed action).
    Claude-primary (as in the source)."""
    items = _items_block(specifics)
    out = L.claude_json(L.CLAUDE_MODEL, KIND_SYS, KIND_USER.format(rec=rec, items=items), max_tokens=2000)
    ks = out.get("kinds", []) if isinstance(out, dict) else []
    return {int(x["index"]): str(x.get("kind", "descriptive")).lower()
            for x in ks if isinstance(x, dict) and "index" in x}


def explain_flags(problem, flagged):
    """Targeted re-ask over the flags: derive each or name the input(s) it must assume. Claude-primary.
    `flagged` = {orig_index: spec}. Returns {orig_index: explanation}."""
    items = "\n".join(f"{i}. \"{s.get('text','')}\" — {s.get('claim','')} (value {s.get('value')})"
                      for i, s in flagged.items())
    out = L.claude_json(L.CLAUDE_MODEL, EXPLAIN_SYS, EXPLAIN_USER.format(problem=problem, items=items), max_tokens=3000)
    exps = out.get("explanations", []) if isinstance(out, dict) else []
    return {int(e["index"]): e for e in exps if isinstance(e, dict) and "index" in e}


# ==================================================================== calculator adjudication (Python; verbatim)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def problem_date_spans(problem):
    """Day-counts derivable from date ranges stated in the problem (calendar arithmetic the checker can't
    otherwise do). Pairs consecutive month/day tokens; records both inclusive and exclusive span."""
    toks = re.findall(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})", (problem or "").lower())
    dates = []
    for mon, day in toks:
        try:
            dates.append((_MONTHS[mon[:3]], int(day)))
        except (KeyError, ValueError):
            continue
    spans = set()
    for (m1, d1), (m2, d2) in zip(dates, dates[1:]):
        y2 = 2025 if (m2, d2) >= (m1, d1) else 2026          # handle a Dec->Jan wrap
        try:
            delta = (datetime.date(y2, m2, d2) - datetime.date(2025, m1, d1)).days
        except ValueError:
            continue
        if 0 < delta < 400:
            spans.add(delta); spans.add(delta + 1)           # exclusive and inclusive counts
    return spans


def _in_spans(value, spans):
    return isinstance(value, (int, float)) and any(abs(value - s) < 0.5 for s in spans)


def _scaled_range(text, value):
    """If the specific's text states a numeric range (e.g. '17-19 MW', '$53M-$75M'), return it scaled to
    the value's magnitude (enumerator records the midpoint, so factor = value/midpoint)."""
    if not isinstance(value, (int, float)):
        return None
    m = re.search(r"(\d[\d,]*\.?\d*)\s*(?:-|–|—|to)\s*(\d[\d,]*\.?\d*)", text or "")
    if not m:
        return None
    try:
        lo, hi = sorted((float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))))
    except ValueError:
        return None
    mid = (lo + hi) / 2
    if mid == 0 or hi / max(lo, 1e-9) > 5:                   # skip implausibly wide ranges (too vague to credit)
        return None
    factor = value / mid if mid else 1.0
    return (lo * factor, hi * factor)


def _matches_target(computed, spec):
    """Compare a computed value to the specific: to its stated RANGE if it has one (±tol), else point (±tol)."""
    value = spec.get("value")
    rng = _scaled_range(spec.get("text", ""), value)
    if rng:
        lo, hi = rng
        return lo * (1 - REL_TOL) <= computed <= hi * (1 + REL_TOL)
    return isinstance(value, (int, float)) and _close(computed, value)


def derivation_verifies(d, spec, problem_text, date_spans):
    """A proposed derivation counts ONLY if every input is grounded in the problem (quoted verbatim, or a
    calendar span of problem dates) AND the arithmetic reproduces the specific's value/range."""
    if not d or not d.get("derivable"):
        return False
    inputs = d.get("inputs", [])
    if not isinstance(inputs, list) or not inputs:
        return False
    env, pnorm = {}, _norm(problem_text)
    for it in inputs:
        if not isinstance(it, dict):
            return False
        name, val, q = it.get("name"), it.get("value"), it.get("source_quote", "")
        if not name or not isinstance(val, (int, float)) or isinstance(val, bool):
            return False
        if _norm(q) not in pnorm and not _in_spans(val, date_spans):
            return False
        env[name] = val
    computed, _, _, err = safe_eval_named(d.get("expr", ""), env)
    return (computed is not None) and (not err) and _matches_target(computed, spec)


def verify_explanation(e, spec, problem_text, date_spans):
    """Adjudicate a re-ask explanation: is the arithmetic sound, is it FULLY grounded, and if not, which
    inputs are ungrounded (rider text). Claimed-grounded inputs whose quote isn't in the problem are demoted."""
    env, pnorm = {}, _norm(problem_text)
    ungrounded = []
    for it in (e.get("inputs_grounded") or []):
        if not isinstance(it, dict):
            continue
        n, v, q = it.get("name"), it.get("value"), it.get("source_quote", "")
        if not n or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        env[n] = v
        if _norm(q) not in pnorm and not _in_spans(v, date_spans):
            ungrounded.append(f"{n} (claimed from problem but not found)")
    for it in (e.get("inputs_assumed") or []):
        if not isinstance(it, dict):
            continue
        n, v, w = it.get("name"), it.get("value"), it.get("why", "")
        if n and isinstance(v, (int, float)) and not isinstance(v, bool):
            env[n] = v
            ungrounded.append(f"{n} — {w}" if w else n)
    computed, _, _, err = safe_eval_named(e.get("expr", ""), env)
    sound = (computed is not None) and (not err) and _matches_target(computed, spec)
    return {"sound": sound, "fully_grounded": sound and not ungrounded, "ungrounded": ungrounded,
            "computed": round(computed, 4) if computed is not None else None}


def bucket_for(idx, spec, problem_text, pres_by_vendor, trace_by_vendor, kind, date_spans):
    value = spec.get("value")
    echoed = isinstance(value, (int, float)) and (_number_in_text(value, problem_text) or _in_spans(value, date_spans))
    ver_by = {v: derivation_verifies(trace_by_vendor[v].get(idx), spec, problem_text, date_spans) for v in trace_by_vendor}
    traceable = echoed or any(ver_by.values())
    pres = {v: pres_by_vendor[v].get(idx, "definitive") for v in pres_by_vendor}
    both_ctx = all(p == "contextualized" for p in pres.values()) if pres else False
    any_ctx = any(p == "contextualized" for p in pres.values())
    split = any_ctx and not both_ctx
    if kind == "prescriptive":
        b = "prescriptive"          # a proposed action, not a factual claim -> out of the gate's scope
    elif traceable:
        b = "traceable"
    elif both_ctx:
        b = "contextualized"        # every vendor agrees it's flagged as an estimate
    elif split:
        b = "REVIEW_SPLIT"          # vendors disagree on whether it's stated as fact -> escalate
    else:
        b = "BUCKET3_FAIL"          # unmarked by all, and untraceable
    return {"index": idx, "text": spec.get("text"), "value": value, "claim": spec.get("claim"),
            "bucket": b, "kind": kind, "echoed": echoed, "traceable": traceable, "verified_by": ver_by,
            "presented_as": pres, "contextualized": both_ctx, "presentation_split": split}


# ==================================================================== the gate on ONE (problem, rec) pair
def _which_vendors():
    """Present+trace vendor set + cross_vendor flag from env keys.
    Returns (vendors|None, cross_vendor, degraded_note). None vendors => cannot run (unchecked)."""
    keys = L.keys_present()
    if not keys["anthropic"]:
        # enumerate/kind/re-ask are Claude-primary; without a Claude key the gate cannot run.
        return None, False, "no api keys" if not keys["openai"] else "no ANTHROPIC_API_KEY (gate is Claude-primary)"
    if keys["openai"]:
        return ("claude", "gpt"), True, None
    return ("claude",), False, "OPENAI_API_KEY absent — Claude-only detector (reduced confidence; no split detection)"


def _unchecked(note):
    return {"answer_verdict": "unchecked", "cross_vendor": False, "n_specifics": 0,
            "buckets": {"traceable": 0, "contextualized": 0, "prescriptive": 0,
                        "REVIEW_SPLIT": 0, "BUCKET3_FAIL": 0},
            "failures": [], "review_splits": [], "recovered_on_reask": 0,
            "rider_lines": [], "rows": [], "degraded": note}


def run_gate(problem, rec):
    vendors, cross_vendor, degraded = _which_vendors()
    if vendors is None:
        return _unchecked(degraded)

    try:
        specifics = enumerate_specifics(problem, rec)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"WARN spar-definitiveness-gate: enumerate failed (fail-soft): {e}\n")
        return _unchecked(f"enumerate error: {str(e)[:200]}")

    if not specifics:
        # nothing to check: no hard specifics stated -> the gate has nothing to fail on.
        out = _empty_pass(cross_vendor)
        if degraded:
            out["degraded"] = degraded
        return out

    # Presentation + traceability judged by each available vendor. Drop a vendor whose pass errors;
    # if that leaves one vendor, degrade cross_vendor rather than crash.
    pres_by, trace_by, live = {}, {}, []
    for v in vendors:
        try:
            pres_by[v] = present_labels(v, rec, specifics)
            trace_by[v] = trace(v, problem, specifics)
            live.append(v)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"WARN spar-definitiveness-gate: {v} present/trace failed (dropped): {e}\n")
    if "claude" not in live:
        return _unchecked("Claude present/trace pass failed")
    if len(live) < 2 and cross_vendor:
        cross_vendor = False
        degraded = (degraded + "; " if degraded else "") + "a vendor pass failed — fell back to single-vendor detector"

    try:
        kinds = kind_labels(rec, specifics)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"WARN spar-definitiveness-gate: kind pass failed (defaulting descriptive): {e}\n")
        kinds = {}

    date_spans = problem_date_spans(problem)
    rows = [bucket_for(i, s, problem, pres_by, trace_by, kinds.get(i, "descriptive"), date_spans)
            for i, s in enumerate(specifics)]

    # Targeted re-ask on each flag (BUCKET3_FAIL or REVIEW_SPLIT): recover a genuinely-missed derivation,
    # else name the assumed input (rider text). Only fully-problem-grounded, arithmetic-sound derivations
    # recover to traceable; a figure that only derives via an unstated assumption stays flagged, now WITH
    # the reason + a trust rider.
    flagged = {i: specifics[i] for i, r in enumerate(rows) if r["bucket"] in ("BUCKET3_FAIL", "REVIEW_SPLIT")}
    recovered = 0
    if flagged:
        try:
            exps = explain_flags(problem, flagged)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"WARN spar-definitiveness-gate: re-ask failed (leaving flags): {e}\n")
            exps = {}
        for i in flagged:
            split_note = ("vendors split on whether this is stated as fact vs an estimate"
                          if rows[i]["bucket"] == "REVIEW_SPLIT" else "")
            e = exps.get(i)
            if not e:
                rows[i]["reason"] = split_note or "no explanation offered on re-ask"
                continue
            v = verify_explanation(e, specifics[i], problem, date_spans)
            if v["fully_grounded"]:
                rows[i]["bucket"] = "traceable"; rows[i]["recovered_on_reask"] = True; recovered += 1
            elif v["sound"] and v["ungrounded"]:
                detail = "rests on unstated input(s): " + "; ".join(v["ungrounded"])
                rows[i]["reason"] = (split_note + "; " + detail) if split_note else detail
                rows[i]["rider"] = "Assumes " + "; ".join(v["ungrounded"]) + " — not in the problem; verify."
            else:
                rows[i]["reason"] = split_note or "no problem-grounded derivation found on re-ask"

    return _assemble(rows, recovered, cross_vendor, degraded)


def _empty_pass(cross_vendor):
    return {"answer_verdict": "PASS", "cross_vendor": cross_vendor, "n_specifics": 0,
            "buckets": {"traceable": 0, "contextualized": 0, "prescriptive": 0,
                        "REVIEW_SPLIT": 0, "BUCKET3_FAIL": 0},
            "failures": [], "review_splits": [], "recovered_on_reask": 0,
            "rider_lines": [], "rows": []}


def _assemble(rows, recovered, cross_vendor, degraded):
    buckets = {"traceable": 0, "contextualized": 0, "prescriptive": 0, "REVIEW_SPLIT": 0, "BUCKET3_FAIL": 0}
    for r in rows:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    fails = [{"text": r["text"], "value": r["value"], "claim": r["claim"], "reason": r.get("reason", "")}
             for r in rows if r["bucket"] == "BUCKET3_FAIL"]
    splits = [{"text": r["text"], "value": r["value"], "claim": r["claim"],
               "presented_as": r.get("presented_as", {}), "reason": r.get("reason", "")}
              for r in rows if r["bucket"] == "REVIEW_SPLIT"]
    rider_lines = [r["rider"] for r in rows if r.get("rider")]
    verdict = "FAIL" if fails else ("REVIEW" if splits else "PASS")
    out = {"answer_verdict": verdict, "cross_vendor": cross_vendor, "n_specifics": len(rows),
           "buckets": buckets, "failures": fails, "review_splits": splits,
           "recovered_on_reask": recovered, "rider_lines": rider_lines, "rows": rows}
    if degraded:
        out["degraded"] = degraded
    return out


# ==================================================================== output
def print_summary(result):
    print("---SPAR-GATE---")
    print(f"ANSWER_VERDICT={result['answer_verdict']}")
    print(f"CROSS_VENDOR={str(result['cross_vendor']).lower()}")
    print(f"N_SPECIFICS={result['n_specifics']}")
    print(f"BUCKET3_FAILURES={result['buckets']['BUCKET3_FAIL']}")
    print(f"REVIEW_SPLITS={result['buckets']['REVIEW_SPLIT']}")
    if result.get("degraded"):
        print(f"DEGRADED={result['degraded']}")
    for f in result["failures"]:
        line = f"  FAIL \"{f['text']}\" (value {f['value']})"
        if f.get("reason"):
            line += f" — {f['reason']}"
        print(line)
    for r in result.get("rider_lines", []):
        print(f"  RIDER {r}")
    print("---ENDSPAR-GATE---")


# ==================================================================== self-test (offline; stubbed model calls)
def self_test():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    problem = "Budget is $500000. Team of 10 people."
    rec = "Spend the $500000 budget. Expect ~$650k/month savings. Assume $50/unit cost (verify)."
    specifics = [
        {"text": "$500000 budget", "value": 500000, "claim": "the budget", "presented_as": "definitive"},
        {"text": "~$650k/month savings", "value": 650000, "claim": "monthly savings", "presented_as": "definitive"},
        {"text": "assume $50/unit (verify)", "value": 50, "claim": "unit cost", "presented_as": "contextualized"},
    ]
    pres = {0: "definitive", 1: "definitive", 2: "contextualized"}
    # trace: nothing derivable; index0 is caught by the echoed check (500000 appears in the problem).
    trace_ret = {0: {"index": 0, "derivable": False, "inputs": [], "expr": "", "result": 0},
                 1: {"index": 1, "derivable": False, "inputs": [], "expr": "", "result": 0},
                 2: {"index": 2, "derivable": False, "inputs": [], "expr": "", "result": 0}}
    kinds_ret = {"kinds": [{"index": 0, "kind": "descriptive"},
                           {"index": 1, "kind": "descriptive"},
                           {"index": 2, "kind": "descriptive"}]}
    # re-ask on the flagged index1: sound only via an ASSUMED input -> stays FAIL, emits a rider.
    explain_ret = {"explanations": [{"index": 1, "derivable": True, "inputs_grounded": [],
                                     "inputs_assumed": [{"name": "monthly_savings", "value": 650000,
                                                         "why": "assumed savings rate"}],
                                     "expr": "monthly_savings", "result": 650000}]}

    def stub_claude(model, system, user, *a, **k):
        if "List every HARD SPECIFIC" in system:
            return {"specifics": specifics}
        if "judge how the recommendation PRESENTS it" in system:
            return {"labels": [{"index": i, "presented_as": p} for i, p in pres.items()]}
        if "classify it as DESCRIPTIVE or PRESCRIPTIVE" in system:
            return kinds_ret
        if "check whether each stated quantity can be derived" in system:
            return {"derivations": list(trace_ret.values())}
        if "A release gate flagged" in system:
            return explain_ret
        raise AssertionError("unexpected claude system prompt in self-test")

    def stub_gpt(model, system, user, *a, **k):
        if "judge how the recommendation PRESENTS it" in system:
            return {"labels": [{"index": i, "presented_as": p} for i, p in pres.items()]}
        if "check whether each stated quantity can be derived" in system:
            return {"derivations": list(trace_ret.values())}
        raise AssertionError("unexpected gpt system prompt in self-test")

    orig_keys, orig_cj, orig_gj = L.keys_present, L.claude_json, L.gpt_json
    try:
        L.keys_present = lambda: {"anthropic": True, "openai": True}
        L.claude_json = stub_claude
        L.gpt_json = stub_gpt
        res = run_gate(problem, rec)

        by_text = {r["text"]: r for r in res["rows"]}
        check("cross_vendor true with both keys", res["cross_vendor"] is True)
        check("n_specifics = 3", res["n_specifics"] == 3)
        check("echoed number ($500000) -> traceable",
              by_text["$500000 budget"]["bucket"] == "traceable" and by_text["$500000 budget"]["echoed"])
        check("unmarked non-derivable ($650k savings) -> BUCKET3_FAIL",
              by_text["~$650k/month savings"]["bucket"] == "BUCKET3_FAIL")
        check("flagged/assumption ($50/unit verify) -> contextualized (passes)",
              by_text["assume $50/unit (verify)"]["bucket"] == "contextualized")
        check("answer_verdict = FAIL (a bucket-3 failure present)", res["answer_verdict"] == "FAIL")
        check("buckets tally: 1 traceable, 1 contextualized, 1 fail",
              res["buckets"]["traceable"] == 1 and res["buckets"]["contextualized"] == 1
              and res["buckets"]["BUCKET3_FAIL"] == 1)
        check("failures list carries the $650k specific",
              len(res["failures"]) == 1 and res["failures"][0]["value"] == 650000)
        check("re-ask on an assumption produces a trust rider",
              len(res["rider_lines"]) == 1 and "Assumes" in res["rider_lines"][0])
        check("assumption did NOT recover to traceable", res["recovered_on_reask"] == 0)

        # A fully-grounded re-ask recovers a flag to traceable (exercise derivation_verifies + recover).
        # Make index1 derivable from a problem-quoted number.
        recover_problem = "Budget is $500000. Team of 10 people. Savings equal the budget."
        explain_recover = {"explanations": [{"index": 1, "derivable": True,
                                             "inputs_grounded": [{"name": "b", "value": 650000,
                                                                  "source_quote": "Budget is $500000"}],
                                             "inputs_assumed": [], "expr": "b", "result": 650000}]}
        # Use a spec whose value IS a problem number so grounding + arithmetic both hold.
        specifics2 = [{"text": "$500000 budget", "value": 500000, "claim": "budget", "presented_as": "definitive"},
                      {"text": "500000 restated", "value": 500000, "claim": "restated", "presented_as": "definitive"}]
        pres2 = {0: "definitive", 1: "definitive"}
        trace2 = {0: {"index": 0, "derivable": False, "inputs": [], "expr": "", "result": 0},
                  1: {"index": 1, "derivable": False, "inputs": [], "expr": "", "result": 0}}

        def stub_claude2(model, system, user, *a, **k):
            if "List every HARD SPECIFIC" in system:
                return {"specifics": specifics2}
            if "judge how the recommendation PRESENTS it" in system:
                return {"labels": [{"index": i, "presented_as": p} for i, p in pres2.items()]}
            if "classify it as DESCRIPTIVE or PRESCRIPTIVE" in system:
                return {"kinds": [{"index": 0, "kind": "descriptive"}, {"index": 1, "kind": "descriptive"}]}
            if "check whether each stated quantity can be derived" in system:
                return {"derivations": list(trace2.values())}
            if "A release gate flagged" in system:
                return {"explanations": [{"index": 1, "derivable": True,
                                          "inputs_grounded": [{"name": "b", "value": 500000,
                                                               "source_quote": "Budget is $500000"}],
                                          "inputs_assumed": [], "expr": "b", "result": 500000}]}
            raise AssertionError("unexpected")

        def stub_gpt2(model, system, user, *a, **k):
            if "judge how the recommendation PRESENTS it" in system:
                return {"labels": [{"index": i, "presented_as": p} for i, p in pres2.items()]}
            if "check whether each stated quantity can be derived" in system:
                return {"derivations": list(trace2.values())}
            raise AssertionError("unexpected")

        L.claude_json = stub_claude2
        L.gpt_json = stub_gpt2
        res2 = run_gate("Budget is $500000. Team of 10 people.",
                        "Spend the $500000 budget. Restated: 500000.")
        # index0 echoed; index1 (500000 restated, also echoed) -> both traceable, no flags actually.
        check("both echoed -> PASS", res2["answer_verdict"] == "PASS")

        # Claude-only degrade path (no OpenAI key) -> cross_vendor false, still runs.
        L.keys_present = lambda: {"anthropic": True, "openai": False}
        L.claude_json = stub_claude
        res3 = run_gate(problem, rec)
        check("no OpenAI key -> cross_vendor false", res3["cross_vendor"] is False)
        check("Claude-only still reaches a FAIL verdict", res3["answer_verdict"] == "FAIL")
        check("Claude-only carries a degraded note", bool(res3.get("degraded")))

        # No keys -> unchecked, no raise.
        L.keys_present = lambda: {"anthropic": False, "openai": False}
        res4 = run_gate(problem, rec)
        check("no keys -> answer_verdict unchecked", res4["answer_verdict"] == "unchecked")
        check("no keys -> degraded='no api keys'", res4.get("degraded") == "no api keys")
        check("no keys -> empty rows", res4["rows"] == [])
    finally:
        L.keys_present, L.claude_json, L.gpt_json = orig_keys, orig_cj, orig_gj

    # calculator unit checks (no model calls)
    check("safe_eval_named computes round(cash/burn,1)",
          safe_eval_named("round(cash/burn, 1)", {"cash": 10.0, "burn": 3.0})[0] == 3.3)
    check("safe_eval_named rejects attribute access",
          safe_eval_named("x.foo", {"x": 1})[0] is None)
    check("_number_in_text tolerant of commas", _number_in_text(1200000, "cost is 1,200,000 total"))
    dv = derivation_verifies({"derivable": True, "inputs": [{"name": "b", "value": 500000,
                              "source_quote": "budget is $500000"}], "expr": "b", "result": 500000},
                             {"value": 500000, "text": "$500000"}, "budget is $500000", set())
    check("derivation_verifies credits a problem-quoted input", dv is True)
    dv2 = derivation_verifies({"derivable": True, "inputs": [{"name": "b", "value": 999,
                               "source_quote": "not in problem"}], "expr": "b", "result": 999},
                              {"value": 999, "text": "999"}, "budget is $500000", set())
    check("derivation_verifies rejects an ungrounded input", dv2 is False)

    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


# ==================================================================== main
def _read(path):
    # Tolerate non-UTF-8 bytes in the input (e.g. a CP1252 em-dash / smart quote
    # that leaked in via corrupted upstream content) instead of crashing the gate
    # with an unhandled UnicodeDecodeError. Robustness > fidelity for a stray byte.
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser(description="The correctness gate — definitiveness-rule detection on one (problem, recommendation) pair.")
    ap.add_argument("--run-dir", help="ceremony dir with pack.md + recommendation.md; writes definitiveness.json")
    ap.add_argument("--problem", help="path to a problem/pack file")
    ap.add_argument("--recommendation", help="path to the candidate recommendation file")
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
            sys.exit(f"[spar-definitiveness-gate] need pack.md + recommendation.md in {args.run_dir}")
        problem, recommendation = _read(ppath), _read(rpath)
        out_path = os.path.join(args.run_dir, "definitiveness.json")
    elif args.problem and args.recommendation:
        problem, recommendation = _read(args.problem), _read(args.recommendation)
    else:
        data = json.loads(sys.stdin.read())
        problem, recommendation = data.get("problem", ""), data.get("recommendation", "")

    result = run_gate(problem, recommendation)

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(result, fh, indent=2)
    print_summary(result)


if __name__ == "__main__":
    main()
