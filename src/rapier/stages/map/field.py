"""map_spark and map_pattern_lock: build the field, region by region.

SPARRING spec v1.4 §3.7. Each unit (a region, or a declared block) gets its own
generation call (the probe), and the Challenger's SPARK function, EXPAND, runs
per unit too. Every item
carries its region(s), anchor, origin, the role and vendor that raised it, and
(after Pattern Lock) the ids of the items merged into it. No prompt asks for a
number of items: breadth comes from the frame and the attacks, never a budget.
"""
from __future__ import annotations

from typing import Any

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from . import _common as C

ANCHOR_RULE = (
    "Give every item an anchor, exactly one of: "
    '{"class": "a", "locus": "<a section of the source, or a short exact quote from it>"} '
    '(cited source); {"class": "b", "title": "<a named taxonomy, checklist, incident '
    'record or stated constraint>", "entry_id": "<the specific entry within it>"} (cited '
    'ground); or {"class": "c", "basis": "<your reasoning>"} (speculative). Never present '
    "a speculative item as cited. A class a quote must be text that is in the source. If "
    "the item says something is ABSENT (a missing field, step or case), do not quote the "
    "absent thing: anchor it to the section where it would be, as the locus (for example "
    '"§9"), with no quote.'
)

GENERATE = (
    "You are the Generator in the SPARK phase of a SPARRING map. Map ONE unit of the "
    "problem space (a region, or a block of regions taken together): list what a reader "
    "should consider in it (risks, gaps, options, failure modes, as the request asks). "
    "Stay inside it. Do not rank "
    "and do not judge which matter most. " + ANCHOR_RULE
    + ' Respond STRICT JSON: {"items": [{"text": str, "anchor": {...}}]}.'
)

EXPAND = (
    "You are the Challenger in the SPARK phase of a SPARRING map; your function is "
    "EXPAND. Given one unit and the Generator's items for it, add only items that are "
    "genuinely new for this unit (not rewordings of listed items). " + ANCHOR_RULE
    + ' Respond STRICT JSON: {"items": [{"text": str, "anchor": {...}}]} (an empty list '
    "if you find nothing new)."
)

MERGE = (
    "You are the Generator in the Pattern Lock phase of a SPARRING map. Find items that "
    "say the same thing in different words and group them. Do not drop anything that is "
    "distinct, and do not judge importance. Respond STRICT JSON: "
    '{"groups": [{"keep": "<item id>", "merged": ["<item id>", ...]}]} listing only '
    "groups with at least one merged item."
)

FILTER = (
    "You are the Challenger in the Pattern Lock phase of a SPARRING map; your function "
    "is FILTER. Given proposed merges, name any group that collapsed genuinely distinct "
    'items. Respond STRICT JSON: {"wrong_merges": ["<keep id of a wrong group>", ...]}.'
)


def norm_anchor(a: Any) -> dict:
    if not isinstance(a, dict):
        return {"class": "c", "basis": "", "note": "no anchor given"}
    cls = str(a.get("class", "")).strip().lower()
    if cls == "a":
        return {"class": "a", "locus": str(a.get("locus", "")).strip()}
    if cls == "b":
        return {"class": "b", "title": str(a.get("title", "")).strip(),
                "entry_id": str(a.get("entry_id", "")).strip()}
    return {"class": "c", "basis": str(a.get("basis", "")).strip()}


def norm_items(reply: Any) -> list[dict] | None:
    """The items in a generation reply, or None when the reply is off-contract."""
    if not isinstance(reply, dict) or not isinstance(reply.get("items"), list):
        return None
    out = []
    for it in reply["items"]:
        if isinstance(it, dict) and str(it.get("text", "")).strip():
            out.append({"text": str(it["text"]).strip(), "anchor": norm_anchor(it.get("anchor"))})
    return out


def add_item(m: dict, *, text: str, anchor: dict, regions: list[str], origin: str,
             role: str, vendor: str) -> dict:
    items = m.setdefault("items", [])
    item = {"id": f"I{len(items) + 1}", "text": text, "anchor": anchor, "regions": regions,
            "origin": origin, "role": role, "vendor": vendor, "merged_ids": []}
    items.append(item)
    return item


def _unit_text(m: dict, u: dict) -> str:
    return C.unit_text(u, {r["id"]: r for r in C.region_list(m)})


@register_stage("map_spark")
class MapSparkStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        regions = C.unit_list(m)
        if not regions:
            env.add_trace("map_spark", self.kind, "no recorded search frame, or the run stopped — skipped")
            return env
        v = C.vendors(env, ctx)
        gen, chal = v["generator"], v["challenger"] or v["generator"]
        probes: dict[str, dict] = m.setdefault("probes", {})
        for r in regions:
            p = probes.setdefault(r["id"], {"generate": "not-run", "expand": "not-run"})
            try:
                reply = C.call(env, role="generator", vendor=gen, purpose="generate", region=r["id"],
                               system=ctx.config.get("generate_prompt", GENERATE),
                               prompt=f"REQUEST AND SOURCE:\n{env.request}\n\n{_unit_text(m, r)}")
                got = norm_items(reply)
                p["generate"] = "ran" if got is not None else "off-contract" + C.cut_off_note(m)
            except Exception as exc:  # noqa: BLE001
                got, p["generate"] = None, f"error: {str(exc)[:200]}"
            for it in got or []:
                add_item(m, text=it["text"], anchor=it["anchor"], regions=[r["id"]],
                         origin="spark", role="generator", vendor=gen)
            listed = [i["text"] for i in m.get("items", []) if r["id"] in i["regions"]]
            try:
                reply = C.call(env, role="challenger", vendor=chal, purpose="expand", region=r["id"],
                               system=ctx.config.get("expand_prompt", EXPAND),
                               prompt=(f"REQUEST AND SOURCE:\n{env.request}\n\n{_unit_text(m, r)}\n\n"
                                       f"GENERATOR'S ITEMS FOR THIS UNIT:\n{listed}"))
                got = norm_items(reply)
                p["expand"] = "ran" if got is not None else "off-contract" + C.cut_off_note(m)
            except Exception as exc:  # noqa: BLE001
                got, p["expand"] = None, f"error: {str(exc)[:200]}"
            for it in got or []:
                add_item(m, text=it["text"], anchor=it["anchor"], regions=[r["id"]],
                         origin="spark", role="challenger", vendor=chal)
        m["spark_raw"] = [dict(i) for i in m.get("items", [])]  # debugging view, never terminal
        env.add_trace("map_spark", self.kind,
                      f"units={len(regions)} items={len(m.get('items', []))}")
        return env


def _valid_groups(raw: Any, ids: set[str], used: set[str]) -> list[dict]:
    """Mechanically valid merge groups only: real ids, no item in two groups.
    An invalid group is ignored, never guessed at."""
    groups = []
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        keep = str(g.get("keep", ""))
        merged = [str(x) for x in (g.get("merged") or []) if str(x) in ids and str(x) != keep]
        if keep not in ids or keep in used or not merged or any(x in used for x in merged):
            continue
        used.update([keep, *merged])
        groups.append({"keep": keep, "merged": merged})
    return groups


@register_stage("map_pattern_lock")
class MapPatternLockStage(TransformStage):
    """Pattern Lock, one unit at a time.

    One merge call over the whole field fails quietly on a real map: on the
    D-100 live run (1,135 items, ~370k characters) the model answered "no
    groups", which is on-contract and read as "no duplicates". So the field is
    de-duplicated per region (items by their first region), each call small
    enough to work through, and the record says how many merge calls ran and
    that merges are within a region only."""

    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        items = m.get("items") or []
        if not items:
            env.add_trace("map_pattern_lock", self.kind, "no field to de-duplicate — skipped")
            return env
        v = C.vendors(env, ctx)
        gen, chal = v["generator"], v["challenger"] or v["generator"]
        by_id = {i["id"]: i for i in items}
        batches: list[dict] = []
        groups_all: list[dict] = []
        unmerged: list[str] = []
        used: set[str] = set()
        for r in C.unit_list(m):
            batch = [i for i in items if (i["regions"] or [None])[0] == r["id"]]
            if len(batch) < 2:
                continue
            ids = {i["id"] for i in batch}
            listing = "\n".join(f"{i['id']}: {i['text']}" for i in batch)
            rec: dict[str, Any] = {"region": r["id"], "items": len(batch)}
            try:
                reply = C.call(env, role="generator", vendor=gen, purpose="merge", region=r["id"],
                               system=ctx.config.get("merge_prompt", MERGE),
                               prompt=f"REQUEST:\n{env.request}\n\nFIELD (one unit):\n{listing}")
                raw = reply.get("groups") if isinstance(reply, dict) else None
                if not isinstance(raw, list):
                    raise ValueError("off-contract merge reply")
                rec["status"] = "ran"
            except Exception as exc:  # noqa: BLE001
                rec.update(status="did-not-run", reason=str(exc)[:200])
                raw = []
            groups = _valid_groups(raw, ids, used)
            if groups:
                try:
                    reply = C.call(env, role="challenger", vendor=chal, purpose="filter", region=r["id"],
                                   system=ctx.config.get("filter_prompt", FILTER),
                                   prompt=(f"FIELD (one unit):\n{listing}\n\nPROPOSED MERGES:\n"
                                           + "\n".join(f"{g['keep']} <- {g['merged']}" for g in groups)))
                    raw_wrong = reply.get("wrong_merges") if isinstance(reply, dict) else None
                    if not isinstance(raw_wrong, list):
                        # an off-contract filter checked nothing: never read it as "no wrong merges"
                        rec["filter"] = "did-not-run: off-contract filter reply; these merges are unchecked"
                    wrong = set(map(str, raw_wrong or []))
                except Exception as exc:  # noqa: BLE001
                    wrong = set()
                    rec["filter"] = f"did-not-run: {str(exc)[:200]}"
                unmerged += sorted(g["keep"] for g in groups if g["keep"] in wrong)
                groups = [g for g in groups if g["keep"] not in wrong]
            rec["groups"] = len(groups)
            batches.append(rec)
            groups_all += groups
        drop: set[str] = set()
        for g in groups_all:
            k = by_id[g["keep"]]
            for x in g["merged"]:
                k["merged_ids"].append(x)
                k["regions"] = sorted(set(k["regions"]) | set(by_id[x]["regions"]))
                drop.add(x)
        m["items"] = [i for i in items if i["id"] not in drop]
        failed = [b for b in batches if b.get("status") != "ran"]
        status = ("ran" if not failed else "did-not-run" if len(failed) == len(batches) else "partial")
        pl: dict[str, Any] = {"status": status, "scope": "within each unit", "groups": groups_all,
                              "unmerged": unmerged, "batches": batches, "merge_calls": len(batches),
                              "merged_count": len(drop),
                              "merged_share": round(len(drop) / len(items), 3) if items else 0.0}
        if failed:
            pl["reason"] = "; ".join(f"{b['region']}: {b.get('reason')}" for b in failed)[:400]
        m["pattern_lock"] = pl
        env.add_trace("map_pattern_lock", self.kind,
                      f"merged={len(drop)} of {len(items)} calls={len(batches)} status={status}")
        return env
