"""map_attack: one completeness attack per region, and the "new?" matcher.

SPARRING spec v1.3 §3.7, "Each region gets a probe and an attack" and "'New' is
a judgment, disclosed and biased toward caution".

The attack contract and the matcher prompt are fixed templates carried in the
manifest config, so the method hash covers them. A reply counts as an attack
only if it carries candidates, each with an anchor, or ``none_found`` with what
was searched. Anything else (a crash, an empty or malformed reply) is recorded
as **no attack**, which can never make a region HELD.

The matcher judges each candidate on its own call. Ties go to *new*, and so does
a matcher call that crashes or replies off-contract: the rider then says the
matcher did not run for that candidate. A false "not new" is the error that
produces a false HELD, so every "not new" is shown beside the item it matched.
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
    "You decide whether a CANDIDATE is already in a FIELD of items. It is 'not new' only "
    "if one field item already says the same thing; if you are unsure, it is new. Respond "
    'STRICT JSON: {"new": true} or {"new": false, "match": "<the field item id>"}.'
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
    """``(is_new, matched_id, note)``. Anything but a clean "not new" naming a
    real field item is new."""
    if isinstance(reply, dict) and reply.get("new") is False:
        mid = str(reply.get("match") or "")
        if mid in field_ids:
            return False, mid, ""
        return True, None, "the matcher said 'not new' without naming a real field item, so it counts as new"
    if isinstance(reply, dict) and reply.get("new") is True:
        return True, None, ""
    return True, None, "the matcher replied off-contract, so the candidate counts as new"


@register_stage("map_attack")
class MapAttackStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        regions = C.region_list(m)
        if not regions or not m.get("items") and not m.get("probes"):
            env.add_trace("map_attack", self.kind, "no recorded frame or field — skipped")
            return env
        v = C.vendors(env, ctx)
        attacker = v["challenger"] or v["generator"]
        independent = bool(v["challenger"])
        matcher_v = ctx.config.get("matcher_vendor") or attacker
        contract = ctx.config.get("attack_contract", ATTACK_CONTRACT)
        matcher = ctx.config.get("matcher_prompt", MATCHER)
        m["attack_contract_hash"] = C.sha256(contract)
        m["matcher"] = {"vendor": matcher_v, "prompt_hash": C.sha256(matcher)}
        attacks: dict[str, dict] = m.setdefault("attacks", {})
        for r in regions:
            field = list(m.get("items") or [])
            listing = "\n".join(f"{i['id']} [{','.join(i['regions'])}]: {i['text']}" for i in field)
            rec: dict[str, Any] = {"vendor": attacker, "independent": independent, "candidates": []}
            try:
                reply = C.call(env, role="challenger", vendor=attacker, purpose="completeness-attack",
                               region=r["id"], system=contract,
                               prompt=(f"REQUEST AND SOURCE:\n{env.request}\n\nREGION {r['id']}: "
                                       f"{r['name']} — {r['definition']}\n\nFIELD:\n{listing}"))
                status, cands, note = parse_attack(reply)
            except Exception as exc:  # noqa: BLE001
                status, cands, note = "error", [], f"the attack crashed: {str(exc)[:200]}"
            rec.update(status=status, note=note)
            ids = {i["id"] for i in field}
            for c in cands:
                try:
                    reply = C.call(env, role="matcher", vendor=matcher_v, purpose="match",
                                   region=r["id"], system=matcher,
                                   prompt=f"FIELD:\n{listing}\n\nCANDIDATE:\n{c['text']}")
                    is_new, matched, mnote = parse_match(reply, ids)
                except Exception as exc:  # noqa: BLE001
                    is_new, matched, mnote = True, None, (
                        f"the matcher did not run ({str(exc)[:120]}), so the candidate counts as new")
                cand = {"text": c["text"], "anchor": c["anchor"], "new": is_new, "matched": matched,
                        "matcher_note": mnote}
                if is_new:
                    item = add_item(m, text=c["text"], anchor=c["anchor"], regions=[r["id"]],
                                    origin="completeness-attack", role="challenger", vendor=attacker)
                    cand["item_id"] = item["id"]
                rec["candidates"].append(cand)
            attacks[r["id"]] = rec
        env.add_trace("map_attack", self.kind,
                      f"regions={len(regions)} attacks_ran="
                      f"{sum(1 for a in attacks.values() if a['status'] == 'ran')} "
                      f"independent={independent}")
        return env
