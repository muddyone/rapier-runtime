"""compose_map: the map and its coverage rider, the map's terminal artifact.

SPARRING spec v1.4 §3.7, "The coverage rider" and "Terminal point". The map is
emitted after Pattern Lock and the completeness attacks; the Cut and the
Resolver do not run. Raw SPARK output is kept only as a debugging view
(``map["spark_raw"]``), never rendered as the map.

Order: by region, then anchor class (a, b, c), then origin. There is no
importance ranking: a ranking is a verdict, and map mode leaves verdicts to the
reader.
"""
from __future__ import annotations

import textwrap

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from ..resolver.compose import _HEAVY, _RULE, _W, _bullets, _overrun_note, _para, _sec, declared_vs_ran
from . import _common as C
from .gate import evaluate

LIMIT_SENTENCE = ("This map covers the frame above as far as a cross-vendor Challenger could "
                  "find. It says nothing about regions outside that frame, or gaps every model "
                  "shares.")
UNMEASURED_SENTENCE = "The completeness attack's recovery rate is unmeasured."
CAPTURE_LINE = ("estimate of how thoroughly the reachable space was sampled; says nothing "
                "about the unreachable one.")

BLOCK_SENTENCE = ("{label} for a block of {n} regions, attacked together; the attack was not "
                  "run region by region.")

_CLASS_ORDER = {"a": 0, "b": 1, "c": 2}
_ORIGIN_ORDER = {"spark": 0, "frame-attack": 1, "completeness-attack": 2}


def _units(m: dict) -> list[dict]:
    return list((m.get("frame") or {}).get("units") or [])


def ordered_items(m: dict) -> list[dict]:
    order = {r["id"]: i for i, r in enumerate(_units(m))}
    return sorted(m.get("items") or [], key=lambda it: (
        min((order.get(r, 999) for r in it["regions"]), default=999),
        _CLASS_ORDER.get(it["anchor"].get("class"), 3),
        _ORIGIN_ORDER.get(it.get("origin"), 3),
        int(it["id"][1:]) if it["id"][1:].isdigit() else 0))


def map_final_state(m: dict) -> tuple[str, str]:
    """The papers' final state for a map. Precedence block > defer > escalate > act:
    a control that decided (the gate's FAIL) is credited; a control that did not
    run defers; otherwise the gate's REVIEW escalates and PASS acts."""
    g = m.get("gate") or evaluate(m)
    st = m.get("stopped") or {}
    if st.get("refused"):
        return "refuse", f"The map did not start: {st['cause']}. No model call was made."
    if g["verdict"] == "FAIL":
        return "block", "Do not rely on this map as it stands: " + "; ".join(g["fail"]) + "."
    if g["did_not_run"]:
        return "defer", ("Some checks did not run (" + "; ".join(g["did_not_run"]) + "). Treat the "
                         "affected regions as unchecked before relying on the map.")
    if g["verdict"] == "REVIEW":
        return "escalate", ("The map needs your judgment: " + "; ".join(g["review"]) + ". See the "
                            "coverage rider.")
    pl = m.get("pattern_lock") or {}
    merges = (f" Pattern Lock did not run for: {pl.get('reason')}, so duplicates there were "
              "not merged." if pl.get("status") in ("did-not-run", "partial") else "")
    return "act", ("The map cleared its checks: every region was probed and its attack held, "
                   "and every cited anchor passed the citation check." + merges
                   + " Which items matter is yours to judge.")


def capture_recapture(m: dict) -> dict | None:
    """A Chao1-style disclosure, only with at least 2 trials across at least 2
    adversaries. Never read by any label, verdict or ordering."""
    trials = m.get("trials") or []
    adversaries = {t.get("adversary") for t in trials}
    if len(trials) < 2 or len(adversaries) < 2:
        return None
    seen: dict[str, int] = {}
    for t in trials:
        for f in set(t.get("findings") or []):
            seen[f] = seen.get(f, 0) + 1
    s_obs = len(seen)
    f1 = sum(1 for n in seen.values() if n == 1)
    f2 = sum(1 for n in seen.values() if n == 2)
    chao1 = s_obs + (f1 * f1) / (2 * f2) if f2 else s_obs + f1 * (f1 - 1) / 2
    return {"method": "Chao1", "trials": len(trials), "adversaries": len(adversaries),
            "observed": s_obs, "estimate": round(chao1, 2)}


def _item_line(it: dict) -> str:
    a = it["anchor"]
    if a.get("class") == "a":
        anc = f"cited source: {a.get('locus')}"
        if a.get("citation_check") == "fabricated":
            fb = a.get("fabricated") or {}
            anc += " (FABRICATED: not found in the source" + (f"; {fb['why']}" if fb.get("why") else "") + ")"
    elif a.get("thin_locus"):
        anc = (f"demoted to speculative: cited {a.get('locus')!r}, which is in the source but "
               "is too thin to locate a claim")
    elif a.get("misquoted") and a.get("misquote_kind") == "elision":
        anc = (f"MISQUOTED (elision), demoted to speculative: cited {a.get('locus')}; "
               f"{a.get('note', '').removeprefix('misquoted (elision): ')}")
        if a.get("possible_reversal"):
            anc += " — POSSIBLE REVERSAL BY ELLIPSIS"
    elif a.get("misquoted"):
        anc = (f"MISQUOTED, demoted to speculative: cited {a.get('locus')}; the quote "
               f"“{a.get('quote')}” is not in the source; nearest source passage "
               f"“{a.get('nearest_passage')}” (word-level similarity {a.get('similarity')})")
    elif a.get("class") == "b":
        anc = f"cited ground: {a.get('title')}, {a.get('entry_id')}"
    else:
        anc = "speculative" + (f": {a.get('basis')}" if a.get("basis") else "")
        if a.get("note"):
            anc += f" ({a['note']})"
    extra = [f"from {it['origin']}, {it['role']} on {it['vendor']}"]
    if len(it["regions"]) > 1:
        extra.append("units " + ", ".join(it["regions"]))
    if it.get("also_fits"):
        extra.append("also fits " + ", ".join(it["also_fits"]))
    if it.get("merged_ids"):
        extra.append("merged " + ", ".join(it["merged_ids"]))
    if it.get("support"):
        extra.append(f"support check: {it['support']}")
    return f"{it['id']} {it['text']}  [{anc}; {'; '.join(extra)}]"


def render_map(env: Envelope) -> str:
    m = C.state(env)
    g = m.get("gate") or evaluate(m)
    state, nxt = map_final_state(m)
    topic = (env.request or "").strip().splitlines()[0][:100] if env.request else ""
    L = ["RAPIER — MAP", "=" * 12]
    if topic:
        L += ["", f"On: {topic}"]
    L += _sec("BOTTOM LINE", "What this map is, and how far its checks reach.",
              _para(f"Final state: {state.upper()}. {nxt}\n\nMap gate: {g['verdict']}. "
                    f"Overall coverage label: {g['overall']}."))

    regions = _units(m)
    all_regions = C.region_list(m)
    by_rid = {r["id"]: r for r in all_regions}
    body = []
    items = ordered_items(m)
    for r in regions:
        body.append(f"{r['id']} {r['name']}")
        mine = [i for i in items if (i["regions"] or [None])[0] == r["id"]]
        body.append(_bullets([_item_line(i) for i in mine]) if mine else "  (no items)")
        body.append("")
    L += _sec("THE MAP", "The field, by unit; within a unit by anchor class, then origin. "
                         "Not ranked: which items matter is yours to judge.",
              "\n".join(body).rstrip() or "(no items)")

    L += ["", "", _HEAVY, "  COVERAGE RIDER", _HEAVY]
    f = m.get("frame") or {}
    fa = m.get("frame_attack") or {}
    p1 = [f"Search frame ({f.get('source', 'none')}" + (f", drafted on {f.get('drafted_by')}" if f.get("drafted_by") else "")
          + f"), hash {str(f.get('hash', ''))[:19]}:"]
    for u in regions:
        if u.get("kind") == "block":
            p1.append(f"  {u['id']} block ({u['basis']}): {u['name']}, {len(u['members'])} regions:")
            p1 += [f"      {rid} {by_rid[rid]['name']}: {by_rid[rid]['definition']}"
                   for rid in u["members"] if rid in by_rid]
        else:
            r = by_rid.get(u["id"], u)
            p1.append(f"  {r['id']} {r['name']}: {r['definition']}" + (
                "  (added by the frame-attack: " + (r['added'].get('reason') or 'no reason given') + ")"
                if r.get("added") else ""))
    if f.get("units") is not None:
        declared_units = len(regions) - len(fa.get("additions") or [])
        p1.append(f"Units: {len(regions)}, cap {f.get('max_units')}"
                  + (" (declared by the frame)" if f.get("max_units") not in (None, 12) else "") + "."
                  + (" The frame was declared at its cap, with no room for frame-attack additions."
                     if f.get("max_units") and declared_units >= f["max_units"] else ""))
    if f.get("frozen"):
        p1 += ["", f"Frozen frame: the regions above, the frame-attack's additions included, were "
                   f"carried verbatim from run {f.get('carried_from')} and regrouped by the frame's "
                   "author. No second frame-attack ran on the regrouped frame. This DEPARTS from "
                   "SPARRING v1.5 §3.7's text (\"the frame-attack runs on the blocked frame\"): the "
                   "frame-attack ran on the unblocked frame of that run, not on these blocks. The "
                   "carried record was cross-checked against that run's hash, supplied frame, call "
                   "log and transcript; consistent edits to every file of that record could still "
                   "forge it."]
    st = m.get("stopped")
    if st:
        p1 += ["", f"STOPPED before generation: {st['cause']}."]
    p1 += ["", f"Frame-attack: {fa.get('status', 'not run')}" + (f" on {fa.get('vendor')}" if fa.get("vendor") else "")
           + ("" if fa.get("independent", False) else " (not independent: same vendor as the Generator)")
           + (f". {fa.get('note')}" if fa.get("note") else "")
           + (f". Added: {', '.join(fa.get('additions'))}" if fa.get("additions") else "")]
    L += _sec("1. THE SEARCH FRAME", "What this map's guarantee is scoped to.", "\n".join(p1))

    p2 = []
    attacks = m.get("attacks") or {}
    for r in regions:
        lab = g["labels"].get(r["id"], {})
        n_items = sum(1 for i in items if r["id"] in i["regions"])
        p2.append(f"{r['id']}: {lab.get('label')}" + (f" ({'; '.join(lab.get('why'))})" if lab.get("why") else "")
                  + ("; this unit yielded no items" if n_items == 0 and lab.get("label") != "UNPROBED" else ""))
        if r.get("kind") == "block" and lab.get("label"):
            p2.append("  " + (BLOCK_SENTENCE.format(label=lab["label"], n=len(r["members"]))
                              if len(r["members"]) > 1 else
                              f"{lab['label']} for a block of 1 region."))
        for c in (attacks.get(r["id"]) or {}).get("candidates", []):
            if not c.get("new"):
                p2.append(f"  not new: “{c['text']}” matched to {c.get('matched')}")
            elif c.get("matcher_note"):
                p2.append(f"  counted as new: “{c['text']}” ({c['matcher_note']})")
            if c.get("new") and c.get("also_fits"):
                p2.append(f"  credited to {r['id']}, also fits {', '.join(c['also_fits'])}: “{c['text']}”")
    p2.append(f"Overall: {g['overall']}. HELD means only that this Challenger, under this "
              "contract, found nothing more; it does not mean exhaustive, even relative to the frame.")
    L += _sec("2. REGION LABELS", "Probed, attacked and held, unit by unit (a unit is a region "
                                  "or a declared block).", "\n".join(p2))

    claim = ("PASS says every region's attack held, every cited source locus exists, and every "
             "named ground is named specifically. It does not say the map is complete, that a "
             "named ground says what the item claims, or that any citation supports its item"
             + (" (the support check ran; see 4)." if (m.get("support_check") or {}).get("status") == "ran"
                else "."))
    reasons = g["fail"] + g["review"]
    L += _sec("3. THE MAP GATE", "What the verdict does and does not claim.",
              _para(f"{g['verdict']}." + (" " + "; ".join(reasons) + "." if reasons else "")
                    + (" Not every check ran, so this verdict covers only what did. Did not run: "
                       + "; ".join(g["did_not_run"]) + "." if g.get("did_not_run") else "")
                    + "\n\n" + claim))

    n = len(items) or 1
    cls = {k: sum(1 for i in items if i["anchor"].get("class") == k) for k in ("a", "b", "c")}
    org = {k: sum(1 for i in items if i.get("origin") == k) for k in _ORIGIN_ORDER}
    sc = m.get("support_check") or {}
    n_mis = sum(1 for i in items if i["anchor"].get("misquoted"))
    n_near = sum(1 for i in items if i["anchor"].get("misquoted")
                 and i["anchor"].get("misquote_kind", "near-quote") == "near-quote")
    revs = [i for i in items if i["anchor"].get("possible_reversal")]
    claimed_a = cls["a"] + n_mis
    p4 = [f"Anchor classes: a (cited source) {cls['a']} ({cls['a'] / n:.0%}), b (cited ground) "
          f"{cls['b']} ({cls['b'] / n:.0%}), c (speculative) {cls['c']} ({cls['c'] / n:.0%}).",
          f"Misquoted class a anchors, demoted to c: {n_mis}"
          + (f" of {claimed_a} claimed ({n_mis / claimed_a:.1%})" if claimed_a else "")
          + f": {n_near} near-quote(s) and {n_mis - n_near} elision(s). Near-quote threshold "
            f"{(m.get('citation_check') or {}).get('misquote_threshold', 0.8)} word-level similarity, "
            "with no negation, modal, number, or direction or quantity word among the differing "
            "words; an elision is an ellipsis splice outside the limits or across a sentence break.",
          f"Loci too thin to locate a claim (one word that is in the source), demoted to c: "
          f"{sum(1 for i in items if i['anchor'].get('thin_locus'))}.",
          ("Possible reversal by ellipsis (the quote skips a negation, modal or exclusion word "
           "across a sentence break; check it against the source): "
           + "; ".join(f"{i['id']} “{q}”" for i in revs
                       for q in (i['anchor']['possible_reversal']
                                 if isinstance(i['anchor']['possible_reversal'], list) else
                                 [i['anchor'].get('quote', '')]))
           if revs else "Possible reversal by ellipsis: none."),
          "Support check: " + (f"ran on {sc.get('vendor')}: {sc.get('counts')} (a model judgment)"
                               if sc.get("status") == "ran" else
                               "did not run" + (f" ({sc.get('reason')})" if sc.get("reason") else "")
                               + "; cited means a real locus or a named entry, not that it supports the item."),
          "Origins: " + ", ".join(f"{k} {v}" for k, v in org.items()) + "."]
    L += _sec("4. ANCHORS, SUPPORT AND ORIGINS", "Where each item came from.", "\n".join(p4))

    pl = m.get("pattern_lock") or {}
    L += _sec("5. PATTERN LOCK MERGES", "Reported, not gated.",
              (f"{pl.get('merged_count', 0)} item(s) merged ({pl.get('merged_share', 0):.0%} of the SPARK field), "
               f"in {pl.get('merge_calls', 0)} merge call(s) "
               f"{'attempted' if pl.get('status') in ('did-not-run', 'partial') else 'made'}, "
               f"{pl.get('scope', 'over the whole field')}"
               + ("; duplicates across units are not merged" if pl.get("scope") == "within each unit" else "")
               + (f"; merges undone by the Challenger: {', '.join(pl['unmerged'])}" if pl.get("unmerged") else "")
               + (f". Pattern Lock did not run for: {pl.get('reason')}" if pl.get("status") in ("did-not-run", "partial") else "")
               + ".") if pl else "Pattern Lock did not run.")

    cr = capture_recapture(m)
    L += _sec("6. COVERAGE ESTIMATE", "A disclosure, never a gate.",
              (f"{cr['method']} over {cr['trials']} trials and {cr['adversaries']} adversaries: "
               f"{cr['observed']} observed, about {cr['estimate']} estimated. {CAPTURE_LINE}")
              if cr else "Not computed: it needs at least 2 trials across at least 2 adversaries.")

    L += _sec("7. LIMITS", "Fixed sentences; they appear on every map.",
              _para(LIMIT_SENTENCE + " " + UNMEASURED_SENTENCE))

    plan = m.get("plan") or {}
    calls = m.get("calls") or []
    L += _sec("CALLS", "Planned before generation, and what ran.",
              _para(f"Planned: {plan.get('planned_calls', 'not computed')} model call(s) at most"
                    + (f" ({plan.get('per_unit_calls')} per unit over {plan.get('units')} unit(s), plus "
                       "the frame calls)" if plan.get("units") else "")
                    + (f", under a call budget of {plan['max_calls']}" if plan.get("max_calls") is not None
                       else ", no call budget declared")
                    + (f", including {plan['retry_reserve']} held for a retry" if plan.get("retry_reserve")
                       else "")
                    + f". Actual: {len(calls)} attempted, {sum(1 for c in calls if c.get('ok'))} "
                      f"answered, {sum(1 for c in calls if c.get('stopped_by'))} refused at the dollar cap."
                    + "\n\n" + retry_note(m)))

    stage_lines = declared_vs_ran(env)
    if stage_lines:
        L += _sec("WHAT RAN", "Every stage the method declared, and what happened to it.",
                  _bullets(stage_lines))
    from ... import budget as _budget

    b = _budget.snapshot()
    if b is not None:
        L += _sec("SPEND", "The run's dollar cap and what it spent, at list prices.",
                  _para(f"${b['spent_usd']:.2f} spent of a ${b['cap_usd']:.2f} cap over {b['calls']} model "
                        "call(s)." + (f" The run STOPPED at the cap: {b['refused']} call(s) were refused, "
                                      "and every check they belonged to reads as not run."
                                      if b["stopped"] else "") + _overrun_note(b)))
    run_dir = env.meta.get("run_dir")
    L += _sec("THE RECORD", "Where this run is on disk.",
              _para(f"Saved for audit to:\n\n  {run_dir}" if run_dir else
                    "No durable record was written for this run."))
    L += ["", "", _RULE, "Produced by Rapier Runtime — a ResourceForge project · rapierruntime.com"]
    return "\n".join(L).rstrip() + "\n"



def _retry_use(outcome: str) -> str:
    if outcome == "completed":
        return "The map uses the retry's reply; the cut-off one is in the transcript, unused."
    if outcome.startswith("cut off again"):
        return ("Neither reply was usable, so that check reads as off-contract; no further retry "
                "was made.")
    return "No reply from it was used."


def retry_note(m: dict) -> str:
    """The rider's disclosure of the run's one retry of a cut-off reply (D-157 b)."""
    retries = m.get("retries") or []
    skipped = m.get("cut_offs_not_retried") or []
    lines = []
    if not retries:
        lines.append("Retries: none. No reply came back cut off." if not skipped else "Retries: none sent.")
    for r in retries:
        lines.append(f"Retried once (the run allows one): the {r['role']}'s {r['purpose']} reply"
                     + (f" for {r['region']}" if r.get("region") else "")
                     + f" came back cut off ({r['first_how']}) and was sent again with the same "
                       f"prompt to the same model. Outcome: {r['outcome']}. "
                     + _retry_use(r["outcome"]))
    for s in skipped:
        lines.append(f"Not retried: the {s['role']}'s {s['purpose']} reply"
                     + (f" for {s['region']}" if s.get("region") else "")
                     + f" came back cut off ({s['how']}) after the run's one retry was used.")
    return " ".join(lines)

@register_stage("compose_map")
class ComposeMapStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        if "gate" not in m:
            m["gate"] = evaluate(m)
            env.verdict = m["gate"]["verdict"]
        from ... import budget as _budget

        b = _budget.snapshot()
        if b and b.get("stopped"):
            m["budget_stopped"] = {"cap_usd": b["cap_usd"], "spent_usd": b["spent_usd"]}
        state, nxt = map_final_state(m)
        env.trust_rider = {"final_state": state, "map_gate": m["gate"]["verdict"],
                           "coverage": m["gate"]["overall"],
                           "stages_run": [dict(r) for r in (env.meta.get("stage_runs") or [])]}
        report = render_map(env)
        env.meta["report_md"] = report
        method = env.meta.get("method") or {}
        row = {"skill": "rapier", "mode": "map", "topic": env.request[:120],
               "final_state": state, "final_state_next": nxt,
               "map_gate": m["gate"]["verdict"], "coverage": m["gate"]["overall"],
               "region_labels": {k: v["label"] for k, v in m["gate"]["labels"].items()},
               "did_not_run": m["gate"]["did_not_run"],
               "frame_hash": (m.get("frame") or {}).get("hash", ""),
               "frame_source": (m.get("frame") or {}).get("source", ""),
               "attack_contract_hash": m.get("attack_contract_hash", ""),
               "matcher_prompt_hash": (m.get("matcher") or {}).get("prompt_hash", ""),
               "items": len(m.get("items") or []),
               "cross_vendor": bool((m.get("vendors") or {}).get("cross_vendor")),
               "method_hash": method.get("hash", ""), "method_resolved_hash": method.get("resolved_hash", ""),
               "rapier_version": method.get("rapier_version", ""),
               "verify_set_by": env.meta.get("verify_set_by") or "",
               "require_independent": bool(env.meta.get("require_independent")),
               "budget": b}
        env.meta["ceremony_row"] = row
        if ctx.ledger is not None:
            ctx.ledger.write_text("report.md", report)
            ctx.ledger.write_json("map.json", {k: v for k, v in m.items() if k != "spark_raw"})
            ctx.ledger.write_json("spark-raw.json", m.get("spark_raw") or [])  # debugging view
            ctx.ledger.write_text("pack.md", env.request)
            ctx.ledger.write_json("ceremony.json", row)
        env.add_trace("compose_map", self.kind, f"map composed; final_state={state}")
        return env
