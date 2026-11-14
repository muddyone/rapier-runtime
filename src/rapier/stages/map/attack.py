"""map_attack: one completeness attack per unit, and the "new?" matcher.

SPARRING spec v1.4 §3.7, "Each region gets a probe and an attack" and "'New' is
a judgment, disclosed and biased toward caution". Every rule written for a
region applies to a unit (a region, or a declared block).

The attack contract and the matcher prompt are fixed templates carried in the
manifest config, so the method hash covers them. A reply counts as an attack
only if it carries candidates, each with an anchor, or ``none_found`` with what
was searched. Anything else (a crash, an empty or malformed reply) is recorded
as **no attack**, which can never make a unit HELD.

Each candidate keeps the anchor class it CLAIMS (``claimed_class``): a new
candidate claiming class a or b keeps its unit from HELD whatever the citation
check later finds, so an attack that misquotes can't produce HELD (v1.4).

The matcher judges all of one attack's candidates in one call, against the
WHOLE field (v1.4 overlap rule: newness is never judged only against the
attacked unit's items). A new candidate is credited to the attacking unit and
tagged with any other unit the matcher says it also fits; a unit's label
depends only on its own attack. Ties go to *new*, and so does a candidate the
matcher left out, a reply off-contract, or a matcher call that failed: the rider
then says the matcher did not judge it. Every "not new" is shown beside the
item it matched, since a false "not new" is what produces a false HELD.
"""
from __future__ import annotations

from typing import Any

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from . import _common as C
from .field import ANCHOR_RULE, add_item, norm_anchor

ATTACK_CONTRACT = (
    "You are the Challenger running a COMPLETENESS ATTACK on one region of a SPARRING "
    "map. You see the region's definition, the source, and the whole field so far. Find "
    "anything in this region the field is missing. " + ANCHOR_RULE
    + " Respond STRICT JSON, exactly one of: "
    '{"candidates": [{"text": str, "anchor": {...}}]} or '
    '{"none_found": true, "searched": "<what you searched in the source and the field>"}.'
)

MATCHER = (
    "You decide, for each numbered CANDIDATE, whether it is already in the FIELD of items. "
    "A candidate is 'not new' only if one field item already says the same thing; if you "
    "are unsure, it is new. For a new candidate, also list any UNITS other than the "
    "attacked one that it fits. Respond STRICT JSON: "
    '{"judgments": [{"candidate": <number>, "new": true, "also_fits": ["<unit id>", ...]} '
    'or {"candidate": <number>, "new": false, "match": "<the field item id>"}]}.'
)


def parse_attack(reply: Any) -> tuple[str, list[dict], str]:
    """Apply the attack contract: ``(status, candidates, note)``, status ``ran``
    or ``off-contract``."""
    if not isinstance(reply, dict):
        return "off-contract", [], "reply was not a JSON object"
    if reply.get("none_found") is True:
        searched = str(reply.get("searched") or "").strip()
        if not searched:
            return "off-contract", [], "'none found' without saying what was searched"
        return "ran", [], searched
    cands = reply.get("candidates")
    if isinstance(cands, list) and cands:
        ok = [c for c in cands if isinstance(c, dict) and str(c.get("text", "")).strip()
              and isinstance(c.get("anchor"), dict)]
        if len(ok) == len(cands):
            return "ran", [{"text": str(c["text"]).strip(), "anchor": norm_anchor(c["anchor"])}
                           for c in ok], ""
        return "off-contract", [], "a candidate had no text or no anchor"
    return "off-contract", [], "neither candidates with anchors nor 'none found' with what was searched"


def parse_match(reply: Any, field_ids: set[str]) -> tuple[bool, str | None, str]:
    """One judgment: ``(is_new, matched_id, note)``. Anything but a clean "not
    new" naming a real field item is new."""
    if isinstance(reply, dict) and reply.get("new") is False:
        mid = str(reply.get("match") or "")
        if mid in field_ids:
            return False, mid, ""
        return True, None, "the matcher said 'not new' without naming a real field item, so it counts as new"
    if isinstance(reply, dict) and reply.get("new") is True:
        return True, None, ""
    return True, None, "the matcher replied off-contract, so the candidate counts as new"


def parse_matches(reply: Any, n: int, field_ids: set[str], unit_ids: set[str],
                  attacked: str) -> list[tuple[bool, str | None, str, list[str]]]:
    """Judgments for candidates 1..n: ``(is_new, matched, note, also_fits)``.
    A candidate the reply leaves out, or judges twice, counts as new."""
    out: list[tuple[bool, str | None, str, list[str]]] = [
        (True, None, "the matcher did not judge this candidate, so it counts as new", [])] * n
    js = reply.get("judgments") if isinstance(reply, dict) else None
    if not isinstance(js, list):
        return [(True, None, "the matcher replied off-contract, so the candidate counts as new", [])] * n
    seen: dict[int, int] = {}
    for j in js:
        if not isinstance(j, dict):
            continue
        try:
            k = int(j.get("candidate"))
        except (TypeError, ValueError):
            continue
        if 1 <= k <= n:
            seen[k] = seen.get(k, 0) + 1
            is_new, matched, note = parse_match(j, field_ids)
            fits = sorted({str(u) for u in (j.get("also_fits") or []) if str(u) in unit_ids
                           and str(u) != attacked}) if is_new and isinstance(j.get("also_fits"), list) else []
            out[k - 1] = (is_new, matched, note, fits)
    for k, cnt in seen.items():
        if cnt > 1:
            out[k - 1] = (True, None, "the matcher judged this candidate twice, so it counts as new", [])
    return out


@register_stage("map_attack")
class MapAttackStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        units = C.unit_list(m)
        if not units or not m.get("items") and not m.get("probes"):
            env.add_trace("map_attack", self.kind, "no recorded frame or field — skipped")
            return env
        by_id = {r["id"]: r for r in C.region_list(m)}
        unit_ids = {u["id"] for u in units}
        v = C.vendors(env, ctx)
        attacker = v["challenger"] or v["generator"]
        independent = bool(v["challenger"])
        matcher_v = ctx.config.get("matcher_vendor") or attacker
        contract = ctx.config.get("attack_contract", ATTACK_CONTRACT)
        matcher = ctx.config.get("matcher_prompt", MATCHER)
        m["attack_contract_hash"] = C.sha256(contract)
        m["matcher"] = {"vendor": matcher_v, "prompt_hash": C.sha256(matcher), "scope": "whole field"}
        attacks: dict[str, dict] = m.setdefault("attacks", {})
        unit_listing = "\n".join(f"{u['id']}: {u['name']}" for u in units)
        for r in units:
            field = list(m.get("items") or [])
            listing = "\n".join(f"{i['id']} [{','.join(i['regions'])}]: {i['text']}" for i in field)
            rec: dict[str, Any] = {"vendor": attacker, "independent": independent, "candidates": []}
            try:
                reply = C.call(env, role="challenger", vendor=attacker, purpose="completeness-attack",
                               region=r["id"], system=contract,
                               prompt=(f"REQUEST AND SOURCE:\n{env.request}\n\n{C.unit_text(r, by_id)}"
                                       f"\n\nFIELD:\n{listing}"))
                status, cands, note = parse_attack(reply)
            except Exception as exc:  # noqa: BLE001
                status, cands, note = "error", [], f"the attack crashed: {str(exc)[:200]}"
            rec.update(status=status, note=note)
            judgments: list = []
            if cands:
                ids = {i["id"] for i in field}
                numbered = "\n".join(f"{k}. {c['text']}" for k, c in enumerate(cands, 1))
                try:
                    reply = C.call(env, role="matcher", vendor=matcher_v, purpose="match",
                                   region=r["id"], system=matcher,
                                   prompt=(f"UNITS:\n{unit_listing}\n\nATTACKED UNIT: {r['id']}\n\n"
                                           f"FIELD:\n{listing or '(no items yet)'}\n\n"
                                           f"CANDIDATES:\n{numbered}"))
                    judgments = parse_matches(reply, len(cands), ids, unit_ids, r["id"])
                except Exception as exc:  # noqa: BLE001
                    judgments = [(True, None, f"the matcher did not run ({str(exc)[:120]}), so the "
                                              "candidate counts as new", [])] * len(cands)
            for c, (is_new, matched, mnote, fits) in zip(cands, judgments):
                cand = {"text": c["text"], "anchor": c["anchor"],
                        "claimed_class": c["anchor"].get("class"), "new": is_new,
                        "matched": matched, "matcher_note": mnote, "also_fits": fits}
                if is_new:
                    item = add_item(m, text=c["text"], anchor=dict(c["anchor"]), regions=[r["id"]],
                                    origin="completeness-attack", role="challenger", vendor=attacker)
                    if fits:
                        item["also_fits"] = fits
                    cand["item_id"] = item["id"]
                rec["candidates"].append(cand)
            attacks[r["id"]] = rec
        env.add_trace("map_attack", self.kind,
                      f"units={len(units)} attacks_ran="
                      f"{sum(1 for a in attacks.values() if a['status'] == 'ran')} "
                      f"independent={independent}")
        return env
