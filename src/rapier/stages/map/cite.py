"""map_cite (mechanical) and map_support (optional, a model judgment).

SPARRING spec v1.3 §3.7, "Provenance and anchors per item".

- **Class a, cited source.** The locus must exist in the source: the quoted text
  is found (whitespace and case folded), or the section it names exists as a
  heading or section mark. A class a item whose locus is not there is a
  **fabricated anchor**, which fails the map gate.
- **Class b, cited ground.** The anchor must carry a non-empty title and a
  non-empty entry identifier that is not just the title again. A miss moves the
  item to class c, with a note. Nothing here confirms the entry exists in the
  named ground or says what the item claims.
- **Class c** is speculative and is left alone.

The support check is off unless the manifest turns it on. When it runs, a
different-vendor model judges whether each cited anchor supports its item; an
item judged not supported moves to class c. Its result is a model judgment and is
reported as one.
"""
from __future__ import annotations

import re
from typing import Any

from ...envelope import Envelope
from ...stage import StageContext, TransformStage, register_stage
from . import _common as C


def _fold(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


_SECTION_REF = re.compile(r"^(§\s*|section\s+|sec\.?\s*)?(\d+(?:\.\d+)*)(?:\s|$|[.:,)])", re.I)


def locus_exists(locus: str, source: str) -> bool:
    """True if the quoted text is in the source, or the section it names exists
    there (as a numbered heading line or a "§n" mark)."""
    loc = (locus or "").strip().strip('"\u201c\u201d\'').strip()
    if not loc:
        return False
    m = _SECTION_REF.match(loc)
    if m and (m.group(1) or "." in m.group(2)) and len(loc) <= len(m.group(0)) + 60:
        num = re.escape(m.group(2))
        heading = re.compile(rf"^\s*(?:#+\s*)?(?:§\s*|section\s+)?{num}(?:[\s.:)]|$)", re.I | re.M)
        if heading.search(source) or re.search(rf"§\s*{num}(?!\.?\d)", source):
            return True
    # A quote: at least two words or 12 characters, so a stray token can't "match".
    if (len(loc.split()) >= 2 or len(loc) >= 12) and _fold(loc) in _fold(source):
        return True
    return False


def check_item(item: dict, source: str) -> None:
    """Apply the citation check to one item, in place."""
    a = item["anchor"]
    cls = a.get("class")
    if cls == "a":
        ok = locus_exists(a.get("locus", ""), source)
        a["citation_check"] = "found" if ok else "fabricated"
    elif cls == "b":
        title, entry = a.get("title", "").strip(), a.get("entry_id", "").strip()
        if title and entry and entry.lower() != title.lower():
            a["citation_check"] = "named"
        else:
            item["anchor"] = {"class": "c", "basis": "", "downgraded_from": "b",
                              "note": "a named ground needs both a title and an entry identifier"
                                      f" (got title={title!r}, entry_id={entry!r})"}


@register_stage("map_cite")
class MapCiteStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        items = m.get("items") or []
        if not items:
            env.add_trace("map_cite", self.kind, "no field to check — skipped")
            return env
        for it in items:
            check_item(it, env.request)
        # Attack candidates carry anchors too: a candidate that "landed" must be cited.
        for rec in (m.get("attacks") or {}).values():
            for c in rec.get("candidates", []):
                tmp = {"anchor": dict(c["anchor"])}
                check_item(tmp, env.request)
                c["anchor"] = tmp["anchor"]
        m["citation_check"] = {"status": "ran"}
        fab = sum(1 for i in items if i["anchor"].get("citation_check") == "fabricated")
        env.add_trace("map_cite", self.kind, f"items={len(items)} fabricated={fab}")
        return env


SUPPORT = (
    "You check whether a cited anchor supports an item. Respond STRICT JSON: "
    '{"support": "supported" | "partly" | "not supported"}.'
)


@register_stage("map_support")
class MapSupportStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        v = C.vendors(env, ctx)
        judge = v["challenger"]
        cited = [i for i in (m.get("items") or []) if i["anchor"].get("class") in ("a", "b")]
        if not judge:
            m["support_check"] = {"status": "did-not-run", "reason": "no second vendor to judge"}
            env.add_trace("map_support", self.kind, "no second vendor — skipped")
            return env
        counts: dict[str, Any] = {"supported": 0, "partly": 0, "not supported": 0, "not judged": 0}
        for it in cited:
            try:
                reply = C.call(env, role="judge", vendor=judge, purpose="support",
                               system=ctx.config.get("support_prompt", SUPPORT),
                               prompt=f"SOURCE:\n{env.request}\n\nITEM:\n{it['text']}\n\nANCHOR:\n{it['anchor']}")
                verdict = str((reply or {}).get("support", "")) if isinstance(reply, dict) else ""
            except Exception:  # noqa: BLE001
                verdict = ""
            if verdict not in ("supported", "partly", "not supported"):
                verdict = "not judged"
            counts[verdict] += 1
            it["support"] = verdict
            if verdict == "not supported":
                it["anchor"] = {"class": "c", "basis": "", "downgraded_from": it["anchor"].get("class"),
                                "note": "the support check judged the anchor does not support the item"}
        m["support_check"] = {"status": "ran", "vendor": judge, "counts": counts}
        env.add_trace("map_support", self.kind, f"judged={len(cited)} {counts}")
        return env
