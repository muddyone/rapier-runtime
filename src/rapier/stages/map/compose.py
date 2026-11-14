"""compose_map: the map and its coverage rider, the map's terminal artifact.

SPARRING spec v1.3 §3.7, "The coverage rider" and "Terminal point". The map is
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
from ..resolver.compose import _HEAVY, _RULE, _W, _bullets, _para, _sec, declared_vs_ran
from . import _common as C
from .gate import evaluate

LIMIT_SENTENCE = ("This map covers the frame above as far as a cross-vendor Challenger could "
                  "find. It says nothing about regions outside that frame, or gaps every model "
                  "shares.")
UNMEASURED_SENTENCE = "The completeness attack's recovery rate is unmeasured."
CAPTURE_LINE = ("estimate of how thoroughly the reachable space was sampled; says nothing "
                "about the unreachable one.")

_CLASS_ORDER = {"a": 0, "b": 1, "c": 2}
_ORIGIN_ORDER = {"spark": 0, "frame-attack": 1, "completeness-attack": 2}


def ordered_items(m: dict) -> list[dict]:
    order = {r["id"]: i for i, r in enumerate(C.region_list(m))}
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
            anc += " (FABRICATED: not found in the source)"
    elif a.get("class") == "b":
        anc = f"cited ground: {a.get('title')}, {a.get('entry_id')}"
    else:
        anc = "speculative" + (f": {a.get('basis')}" if a.get("basis") else "")
        if a.get("note"):
            anc += f" ({a['note']})"
    extra = [f"from {it['origin']}, {it['role']} on {it['vendor']}"]
    if len(it["regions"]) > 1:
        extra.append("regions " + ", ".join(it["regions"]))
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

    regions = C.region_list(m)
    body = []
    items = ordered_items(m)
    for r in regions:
        body.append(f"{r['id']} {r['name']}")
        mine = [i for i in items if (i["regions"] or [None])[0] == r["id"]]
        body.append(_bullets([_item_line(i) for i in mine]) if mine else "  (no items)")
        body.append("")
    L += _sec("THE MAP", "The field, by region; within a region by anchor class, then origin. "
                         "Not ranked: which items matter is yours to judge.",
              "\n".join(body).rstrip() or "(no items)")

    L += ["", "", _HEAVY, "  COVERAGE RIDER", _HEAVY]
    f = m.get("frame") or {}
    fa = m.get("frame_attack") or {}
    p1 = [f"Search frame ({f.get('source', 'none')}" + (f", drafted on {f.get('drafted_by')}" if f.get("drafted_by") else "")
          + f"), hash {str(f.get('hash', ''))[:19]}:"]
    p1 += [f"  {r['id']} {r['name']}: {r['definition']}" + ("  (added by the frame-attack: "
           + (r['added'].get('reason') or 'no reason given') + ")" if r.get("added") else "") for r in regions]
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
                  + ("; this region yielded no items" if n_items == 0 and lab.get("label") != "UNPROBED" else ""))
        for c in (attacks.get(r["id"]) or {}).get("candidates", []):
            if not c.get("new"):
                p2.append(f"  not new: “{c['text']}” matched to {c.get('matched')}")
            elif c.get("matcher_note"):
                p2.append(f"  counted as new: “{c['text']}” ({c['matcher_note']})")
    p2.append(f"Overall: {g['overall']}. HELD means only that this Challenger, under this "
              "contract, found nothing more; it does not mean exhaustive, even relative to the frame.")
    L += _sec("2. REGION LABELS", "Probed, attacked and held, region by region.", "\n".join(p2))

    claim = ("PASS says every region's attack held, every cited source locus exists, and every "
             "named ground is named specifically. It does not say the map is complete, that a "
             "named ground says what the item claims, or that any citation supports its item"
             + (" (the support check ran; see 4)." if (m.get("support_check") or {}).get("status") == "ran"
                else "."))
    reasons = g["fail"] + g["review"]
    L += _sec("3. THE MAP GATE", "What the verdict does and does not claim.",
              _para(f"{g['verdict']}." + (" " + "; ".join(reasons) + "." if reasons else "") + "\n\n" + claim))

    n = len(items) or 1
    cls = {k: sum(1 for i in items if i["anchor"].get("class") == k) for k in ("a", "b", "c")}
    org = {k: sum(1 for i in items if i.get("origin") == k) for k in _ORIGIN_ORDER}
    sc = m.get("support_check") or {}
    p4 = [f"Anchor classes: a (cited source) {cls['a']} ({cls['a'] / n:.0%}), b (cited ground) "
          f"{cls['b']} ({cls['b'] / n:.0%}), c (speculative) {cls['c']} ({cls['c'] / n:.0%}).",
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
               + ("; duplicates across regions are not merged" if pl.get("scope") == "within each region" else "")
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

    stage_lines = declared_vs_ran(env)
    if stage_lines:
        L += _sec("WHAT RAN", "Every stage the method declared, and what happened to it.",
                  _bullets(stage_lines))
    run_dir = env.meta.get("run_dir")
    L += _sec("THE RECORD", "Where this run is on disk.",
              _para(f"Saved for audit to:\n\n  {run_dir}" if run_dir else
                    "No durable record was written for this run."))
    L += ["", "", _RULE, "Produced by Rapier Runtime — a ResourceForge project · rapierruntime.com"]
    return "\n".join(L).rstrip() + "\n"


@register_stage("compose_map")
class ComposeMapStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        if "gate" not in m:
            m["gate"] = evaluate(m)
            env.verdict = m["gate"]["verdict"]
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
               "require_independent": bool(env.meta.get("require_independent"))}
        env.meta["ceremony_row"] = row
        if ctx.ledger is not None:
            ctx.ledger.write_text("report.md", report)
            ctx.ledger.write_json("map.json", {k: v for k, v in m.items() if k != "spark_raw"})
            ctx.ledger.write_json("spark-raw.json", m.get("spark_raw") or [])  # debugging view
            ctx.ledger.write_text("pack.md", env.request)
            ctx.ledger.write_json("ceremony.json", row)
        env.add_trace("compose_map", self.kind, f"map composed; final_state={state}")
        return env
