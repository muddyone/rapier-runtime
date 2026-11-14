"""map_frame: declare, attack and record the search frame before any generation.

SPARRING spec v1.4 §3.7, "The search frame is declared before generation" and
"Frame size and blocks".

1. **Declare.** The frame is supplied by the requester (``config["regions"]`` or
   ``env.meta["map_regions"]``, with optional ``map_blocks`` and
   ``map_max_units``), or, when none is given, drafted by a separate Frame call
   and marked *drafted*. The Generator never declares its own frame and never
   groups it: drafting and grouping run on the Challenger's vendor when there is
   one, as the framer role.
2. **Units.** A map attacks at most ``MAX_UNITS`` (12) units, a unit being a
   region or a declared block, unless the frame declares a higher cap. Above the
   cap, the declarer groups regions into blocks along one seam (all by section
   or all by lens). A supplied frame above the cap with no blocks is refused
   before any call; a drafted one is grouped by a second framer call.
3. **Frame-attack.** A Challenger on a vendor other than the Generator's is asked
   to name a region the (blocked) frame omits, under a fixed contract
   (``FRAME_ATTACK``, carried in the manifest config so it is hashed). An
   accepted addition joins the frame as its own unit. If that takes the frame
   past its cap, the run stops before generation: the frame must be regrouped.
4. **Record.** The frame, blocks and cap are recorded verbatim with a sha256 and
   the trace position, before the first generation call. The gate checks it.
5. **Plan.** Before any generation, the planned call count is computed from the
   frame (``per_unit_calls`` per unit, an upper bound). If it exceeds the
   declared call budget (``max_calls``), the run does not start.
"""
from __future__ import annotations

import math
from typing import Any

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from . import _common as C

FRAME_DRAFT = (
    "You draft the SEARCH FRAME for a map of a problem space: the list of regions a "
    "thorough search must cover. You do not search the space yourself and you do not "
    "list findings. Base the regions on the source's own structure crossed with the "
    "lenses the request names (or, if it names none, the standard lenses for this kind "
    "of request). Respond STRICT JSON: "
    '{"regions": [{"name": str, "definition": str}]}.'
)

FRAME_ATTACK = (
    "You attack a SEARCH FRAME: the list of regions a map of this problem space will "
    "cover. Name any region the frame omits that a thorough search of this source and "
    "request would need. Do not list findings, only missing regions. Respond STRICT JSON, "
    "exactly one of: "
    '{"missing_regions": [{"name": str, "definition": str, "reason": str}]} '
    'or {"none_found": true, "searched": "<what you checked the frame against>"}.'
)


MAX_UNITS = 12
PER_UNIT_CALLS = 6  # generate, expand, merge, filter, attack, match: an upper bound

FRAME_BLOCK = (
    "You group the regions of a SEARCH FRAME into at most {cap} BLOCKS, so a map can "
    "attack each block as one unit. Group along ONE of the frame's own seams, by section "
    "of the source or by lens, never a mix of the two. Every region goes in exactly one "
    "block. Do not add, drop or rename regions. Respond STRICT JSON: "
    '{{"basis": "section" | "lens", "blocks": [{{"name": str, "members": ["<region id>", ...]}}]}}.'
)


def _norm_regions(raw: Any) -> list[dict]:
    out: list[dict] = []
    for i, r in enumerate(raw or []):
        if isinstance(r, str) and r.strip():
            out.append({"name": r.strip(), "definition": r.strip()})
        elif isinstance(r, dict) and str(r.get("name", "")).strip():
            out.append({"name": str(r["name"]).strip(),
                        "definition": str(r.get("definition") or r["name"]).strip()})
    for i, r in enumerate(out, 1):
        r["id"] = f"R{i}"
    return out


def make_units(regions: list[dict], blocks: Any, basis: Any = None) -> tuple[list[dict], str]:
    """Units from regions and declared blocks: ``(units, error)``. With no blocks,
    each region is its own unit. Blocks must share one basis (section or lens)
    and cover every region exactly once; members name a region by id or name."""
    if not blocks:
        return [{"id": r["id"], "kind": "region", "name": r["name"], "definition": r["definition"],
                 "members": [r["id"]]} for r in regions], ""
    if not isinstance(blocks, list):
        return [], "blocks must be a list"
    by_key = {}
    for r in regions:
        by_key[r["id"].lower()] = r["id"]
        by_key[r["name"].lower()] = r["id"]
    units, seen, bases = [], set(), set()
    for i, b in enumerate(blocks, 1):
        if not isinstance(b, dict) or not str(b.get("name", "")).strip():
            return [], f"block {i} has no name"
        members = []
        for x in b.get("members") or []:
            rid = by_key.get(str(x).strip().lower())
            if rid is None:
                return [], f"block {b['name']!r} names {x!r}, which is not a region of the frame"
            if rid in seen:
                return [], f"region {rid} is in more than one block"
            seen.add(rid)
            members.append(rid)
        if not members:
            return [], f"block {b['name']!r} has no member regions"
        bb = str(b.get("basis") or basis or "").strip().lower()
        if bb not in ("section", "lens"):
            return [], f"block {b['name']!r} needs a basis: section or lens"
        bases.add(bb)
        units.append({"id": f"B{i}", "kind": "block", "name": str(b["name"]).strip(),
                      "basis": bb, "members": members,
                      "definition": "; ".join(f"{rid}" for rid in members)})
    if len(bases) > 1:
        return [], "blocks mix two seams (section and lens); group along one"
    missing = [r["id"] for r in regions if r["id"] not in seen]
    if missing:
        return [], f"regions in no block: {', '.join(missing)}"
    return units, ""


def _cap(raw: Any) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return MAX_UNITS
    return v if v > 0 else MAX_UNITS


def _max_calls(raw: Any) -> int | None:
    if raw in (None, "", 0):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0  # an unreadable budget fails closed
    return int(v) if math.isfinite(v) and v >= 0 else 0


def parse_frame_attack(reply: Any) -> tuple[str, list[dict], str]:
    """Apply the frame-attack contract. Returns ``(status, additions, note)``,
    status ``ran`` or ``off-contract``."""
    if not isinstance(reply, dict):
        return "off-contract", [], "reply was not a JSON object"
    if reply.get("none_found") is True:
        searched = str(reply.get("searched") or "").strip()
        if not searched:
            return "off-contract", [], "'none found' without saying what was searched"
        return "ran", [], searched
    missing = reply.get("missing_regions")
    if isinstance(missing, list) and missing:
        adds = [{"name": str(r.get("name", "")).strip(),
                 "definition": str(r.get("definition", "")).strip(),
                 "reason": str(r.get("reason", "")).strip()}
                for r in missing if isinstance(r, dict)]
        adds = [a for a in adds if a["name"] and a["definition"]]
        if adds:
            return "ran", adds, ""
    return "off-contract", [], "neither named missing regions nor 'none found' with what was searched"


@register_stage("map_frame")
class MapFrameStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        v = C.vendors(env, ctx)
        if v["generator"] is None:
            env.add_trace("map_frame", self.kind, "no vendor available — skipped")
            return env
        challenger = v["challenger"]
        drafter = challenger or v["generator"]
        cap = _cap(ctx.config.get("max_units") or env.meta.get("map_max_units"))
        per_unit = int(ctx.config.get("per_unit_calls") or PER_UNIT_CALLS)
        max_calls = _max_calls(ctx.config.get("max_calls") or env.meta.get("map_max_calls"))
        plan: dict[str, Any] = {"per_unit_calls": per_unit, "max_calls": max_calls, "max_units": cap}
        m["plan"] = plan

        supplied = ctx.config.get("regions") or env.meta.get("map_regions")
        blocks = ctx.config.get("blocks") or env.meta.get("map_blocks")
        units: list[dict] = []
        if supplied:
            regions, source, drafted_by = _norm_regions(supplied), "supplied", None
            units, err = make_units(regions, blocks)
            if err:
                return self._stop(env, m, f"the supplied frame's blocks are invalid: {err}",
                                  refused=True)
            if len(units) > cap:
                return self._stop(env, m, (
                    f"the supplied frame has {len(units)} units, above the cap of {cap}; group "
                    "its regions into blocks along one seam (section or lens), or declare a "
                    "higher cap"), refused=True)
            # Every call this run can make, before the first one: the frame-attack
            # plus the per-unit calls. Additions are not known yet; checked again below.
            planned = 1 + per_unit * len(units)
            if max_calls is not None and planned > max_calls:
                plan["planned_calls"] = planned
                return self._stop(env, m, (
                    f"the plan needs up to {planned} calls, over the call budget of {max_calls}; "
                    "regroup the frame or raise the budget"), refused=True)
        else:
            try:
                reply = C.call(env, role="framer", vendor=drafter, purpose="frame-draft",
                               system=ctx.config.get("draft_prompt", FRAME_DRAFT),
                               prompt=f"REQUEST AND SOURCE:\n{env.request}")
                regions = _norm_regions((reply or {}).get("regions") if isinstance(reply, dict) else None)
            except Exception as exc:  # noqa: BLE001
                regions = []
                m["frame_error"] = f"drafting failed: {str(exc)[:200]}"
            source, drafted_by = "drafted", drafter
            if regions:
                units, _ = make_units(regions, None)
                if len(units) > cap:
                    units = self._group(env, ctx, m, regions, cap, drafter)
                    if not units:
                        env.add_trace("map_frame", self.kind, "the drafted frame could not be grouped",
                                      error=m.get("frame_error"))
                        return env
        if not regions:
            env.add_trace("map_frame", self.kind, "no search frame could be declared — skipped",
                          error=m.get("frame_error") or "no regions")
            return env
        declared = [dict(r) for r in regions]
        by_id = {r["id"]: r for r in regions}

        attack: dict[str, Any] = {"vendor": challenger, "additions": [], "independent": bool(challenger)}
        try:
            reply = C.call(env, role="challenger", vendor=drafter, purpose="frame-attack",
                           system=ctx.config.get("frame_attack_prompt", FRAME_ATTACK),
                           prompt=(f"REQUEST AND SOURCE:\n{env.request}\n\nSEARCH FRAME:\n"
                                   + "\n".join(C.unit_text(u, by_id) for u in units)))
            status, adds, note = parse_frame_attack(reply)
        except Exception as exc:  # noqa: BLE001
            status, adds, note = "error", [], f"the frame-attack crashed: {str(exc)[:200]}"
        attack.update(vendor=drafter, status=status, note=note)
        known = {r["name"].lower() for r in regions}
        for a in adds:
            if a["name"].lower() in known:
                continue
            known.add(a["name"].lower())
            r = {"id": f"R{len(regions) + 1}", "name": a["name"], "definition": a["definition"],
                 "added": {"by": "frame-attack", "reason": a["reason"]}}
            regions.append(r)
            units.append({"id": r["id"], "kind": "region", "name": r["name"],
                          "definition": r["definition"], "members": [r["id"]], "added": True})
            attack["additions"].append(a["name"])

        frame = {"regions": regions, "units": units, "source": source, "declared": declared,
                 "drafted_by": drafted_by, "max_units": cap,
                 "blocked": any(u["kind"] == "block" for u in units)}
        frame["hash"] = C.sha256({"regions": regions, "units": units, "source": source,
                                  "max_units": cap})
        frame["recorded_at_call"] = len(m["calls"])  # calls made before the frame was fixed
        m["frame"] = frame
        m["frame_attack"] = attack
        plan["planned_calls"] = len(m["calls"]) + per_unit * len(units)
        plan["units"] = len(units)
        if ctx.ledger is not None:
            ctx.ledger.write_json("map-frame.json", {"frame": frame, "frame_attack": attack, "plan": plan})
        if len(units) > cap:
            return self._stop(env, m, (
                f"the frame-attack's additions take the frame to {len(units)} units, above the cap "
                f"of {cap}; regroup the frame into blocks, or declare a higher cap"))
        if max_calls is not None and plan["planned_calls"] > max_calls:
            return self._stop(env, m, (
                f"the plan needs up to {plan['planned_calls']} calls, over the call budget of "
                f"{max_calls}; regroup the frame or raise the budget"))
        env.add_trace("map_frame", self.kind,
                      f"frame {source} regions={len(regions)} units={len(units)} "
                      f"hash={frame['hash'][:19]} frame_attack={status} "
                      f"independent={attack['independent']} added={len(attack['additions'])} "
                      f"planned_calls={plan['planned_calls']}")
        return env

    def _group(self, env, ctx, m, regions, cap, drafter) -> list[dict]:
        """A drafted frame above the cap: the declarer (Frame, never the
        Generator) groups it into blocks before SPARK."""
        prompt_t = ctx.config.get("block_prompt", FRAME_BLOCK)
        try:
            reply = C.call(env, role="framer", vendor=drafter, purpose="frame-block",
                           system=prompt_t.format(cap=cap),
                           prompt="SEARCH FRAME:\n" + "\n".join(
                               f"{r['id']} {r['name']}: {r['definition']}" for r in regions))
            if not isinstance(reply, dict):
                raise ValueError("off-contract grouping reply")
            units, err = make_units(regions, reply.get("blocks"), reply.get("basis"))
            if err:
                raise ValueError(err)
            if len(units) > cap:
                raise ValueError(f"{len(units)} blocks, above the cap of {cap}")
            return units
        except Exception as exc:  # noqa: BLE001
            m["frame_error"] = f"grouping the drafted frame failed: {str(exc)[:200]}"
            return []

    def _stop(self, env, m, cause: str, refused: bool = False) -> Envelope:
        """Stop before generation. ``refused``: no model call was made."""
        m["stopped"] = {"at": "frame", "cause": cause, "refused": refused and not m["calls"]}
        env.add_trace("map_frame", self.kind, f"stopped before generation: {cause}", error=cause)
        return env


def frame_attack_ran_independently(m: dict) -> bool:
    a = m.get("frame_attack") or {}
    return a.get("status") == "ran" and bool(a.get("independent"))
