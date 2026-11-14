"""map_frame: declare, attack and record the search frame before any generation.

SPARRING spec v1.3 §3.7, "The search frame is declared before generation".

1. **Declare.** The frame is supplied by the requester (``config["regions"]`` or
   ``env.meta["map_regions"]``), or, when none is given, drafted by a separate
   Frame call and marked *drafted*. The Generator never declares its own frame:
   the drafting call runs on the Challenger's vendor when there is one, under its
   own prompt, and never as the Generator role.
2. **Frame-attack.** A Challenger on a vendor other than the Generator's is asked
   to name a region the frame omits, under a fixed contract (``FRAME_ATTACK``,
   carried in the manifest config so it is hashed). A reply counts only if it is
   ``{"missing_regions": [...]}`` with named, defined regions, or
   ``{"none_found": true, "searched": "<what it searched>"}``. A crash, a
   malformed or empty reply, or no second vendor means the frame-attack did not
   run (independently), and the record says which.
3. **Record.** The frame (with accepted additions) is recorded verbatim with a
   sha256 and the trace position, before the first generation call. The gate
   checks that position.
"""
from __future__ import annotations

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

        supplied = ctx.config.get("regions") or env.meta.get("map_regions")
        if supplied:
            regions, source, drafted_by = _norm_regions(supplied), "supplied", None
        else:
            drafter = challenger or v["generator"]
            try:
                reply = C.call(env, role="framer", vendor=drafter, purpose="frame-draft",
                               system=ctx.config.get("draft_prompt", FRAME_DRAFT),
                               prompt=f"REQUEST AND SOURCE:\n{env.request}")
                regions = _norm_regions((reply or {}).get("regions") if isinstance(reply, dict) else None)
            except Exception as exc:  # noqa: BLE001
                regions = []
                m["frame_error"] = f"drafting failed: {str(exc)[:200]}"
            source, drafted_by = "drafted", drafter
        if not regions:
            env.add_trace("map_frame", self.kind, "no search frame could be declared — skipped",
                          error=m.get("frame_error") or "no regions")
            return env
        declared = [dict(r) for r in regions]

        attack: dict[str, Any] = {"vendor": challenger, "additions": [], "independent": bool(challenger)}
        attacker = challenger or v["generator"]
        try:
            reply = C.call(env, role="challenger", vendor=attacker, purpose="frame-attack",
                           system=ctx.config.get("frame_attack_prompt", FRAME_ATTACK),
                           prompt=(f"REQUEST AND SOURCE:\n{env.request}\n\nSEARCH FRAME:\n"
                                   + "\n".join(f"- {r['name']}: {r['definition']}" for r in regions)))
            status, adds, note = parse_frame_attack(reply)
        except Exception as exc:  # noqa: BLE001
            status, adds, note = "error", [], f"the frame-attack crashed: {str(exc)[:200]}"
        attack.update(vendor=attacker, status=status, note=note)
        known = {r["name"].lower() for r in regions}
        for a in adds:
            if a["name"].lower() in known:
                continue
            known.add(a["name"].lower())
            regions.append({"id": f"R{len(regions) + 1}", "name": a["name"],
                            "definition": a["definition"],
                            "added": {"by": "frame-attack", "reason": a["reason"]}})
            attack["additions"].append(a["name"])

        frame = {"regions": regions, "source": source, "declared": declared,
                 "drafted_by": drafted_by}
        frame["hash"] = C.sha256({"regions": regions, "source": source})
        frame["recorded_at_call"] = len(m["calls"])  # calls made before the frame was fixed
        m["frame"] = frame
        m["frame_attack"] = attack
        if ctx.ledger is not None:
            ctx.ledger.write_json("map-frame.json", {"frame": frame, "frame_attack": attack})
        env.add_trace("map_frame", self.kind,
                      f"frame {source} regions={len(regions)} hash={frame['hash'][:19]} "
                      f"frame_attack={status} independent={attack['independent']} "
                      f"added={len(attack['additions'])}")
        return env


def frame_attack_ran_independently(m: dict) -> bool:
    a = m.get("frame_attack") or {}
    return a.get("status") == "ran" and bool(a.get("independent"))
