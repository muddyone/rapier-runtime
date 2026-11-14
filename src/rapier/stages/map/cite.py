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
    """Compare text the way a reader would: markdown emphasis, backticks, table
    pipes and blockquote marks dropped, every dash and quote mark alike,
    whitespace (line wraps included) collapsed, case folded. Applied to both the
    locus and the source, so it can't make a quote match text that isn't there."""
    t = (s or "")
    t = re.sub(r"^\s*>+\s?", " ", t, flags=re.M)          # blockquote marks
    t = re.sub(r"[*`|]", " ", t)                           # emphasis, code, table pipes
    t = re.sub(r"(?<!\w)_|_(?!\w)", " ", t)                # _emphasis_, not snake_case
    t = re.sub(r"[\u2014\u2013\u2212]|--", "-", t)          # dashes
    t = re.sub(r"\s-\s", " ", t)                           # a spaced dash is punctuation
    t = re.sub(r"[\u2018\u2019]", "'", t)
    t = re.sub(r"[\u201c\u201d]", '"', t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([.,;:!?)])", r"\1", t)                # "nothing ." -> "nothing."
    return t.strip().lower()


_QUOTE = re.compile(r"[\"\u201c]([^\"\u201d]+)[\"\u201d]|(?<![A-Za-z0-9])['\u2018`]([^'\u2019`]+)['\u2019`](?![A-Za-z])")
# Backtick spans are checked like quotes: an identifier the locus names must be in
# the source, or an invented one under a real section would pass.
_SECTION_ANY = re.compile(r"(?:§\s*|\bsection\s+|\bsec\.?\s*)(\d+(?:\.\d+)*)|^(\d+(?:\.\d+)+)(?=[\s.:,)]|$)",
                          re.I)


def _pieces(text: str, min_evidence: bool = False) -> list[str]:
    """Split text at ellipses. For a QUOTE every non-empty piece is kept,
    however short: a quoted word that isn't in the source is strong evidence
    of fabrication (verifier C-1: "instantly", "eu-east", an invented "never").
    ``min_evidence`` keeps only pieces long enough to count as evidence (two
    words or 12 characters); it is for unquoted text only."""
    out = []
    for piece in re.split(r"\s*(?:\.\.\.|\u2026)\s*", text):
        piece = piece.strip(" ,;:")
        if not piece:
            continue
        if not min_evidence or len(piece.split()) >= 2 or len(piece) >= 12:
            out.append(piece)
    return out


def _quote_parts(loc: str) -> list[str]:
    """The quoted text in a locus, split at ellipses."""
    parts = []
    for m in _QUOTE.finditer(loc):
        parts += _pieces(m.group(1) or m.group(2) or "")
    return parts


_LEAD_REF = re.compile(r"^\s*(?:(?:§\s*|section\s+)\d+(?:\.\d+)*[^:\u2014-]*?[:\u2014-]\s+)", re.I)


def _section_exists(num: str, source: str) -> bool:
    n = re.escape(num)
    heading = re.compile(rf"^\s*(?:#+\s*)?(?:§\s*|section\s+)?{n}(?:[\s.:)]|$)", re.I | re.M)
    return bool(heading.search(source) or re.search(rf"§\s*{n}(?!\.?\d)", source))


def locus_exists(locus: str, source: str) -> bool:
    """True if the locus points at something really in the source.

    - If the locus quotes the source, every quoted piece must be found
      (whitespace and case folded). A real section with a made-up quote fails.
    - Otherwise, the whole locus found verbatim counts; failing that, every
      section it names must exist (a "§2 table vs §13" style pointer).
    A bare number is too weak to be a section reference."""
    loc = (locus or "").strip()
    if not loc:
        return False
    quotes = _quote_parts(loc)
    if quotes:
        return all(_fold(q) in _fold(source) for q in quotes)
    bare = loc.strip('"\u201c\u201d\'').strip()
    text = _LEAD_REF.sub("", bare)  # "§11: Lockfile at ..." -> "Lockfile at ..."
    pieces = _pieces(text, min_evidence=True)
    if pieces and all(_fold(p) in _fold(source) for p in pieces):
        return True
    refs = [m.group(1) or m.group(2) for m in _SECTION_ANY.finditer(bare)]
    return bool(refs) and all(_section_exists(r, source) for r in refs)


# An entry identifier that says there is none (verifier M-2): "industry best
# practice" has no entry identifier, and "N/A" is a model saying exactly that.
_PLACEHOLDERS = {"n/a", "na", "n.a.", "none", "null", "nil", "-", "--", "\u2014", "\u2013",
                 "unknown", "tbd", "tba", "general", "generic", "various", "any", "all",
                 "see title", "not applicable", "no entry", "n/a."}


def _placeholder(entry: str) -> bool:
    return entry.strip().strip(".").lower() in _PLACEHOLDERS or entry.strip().lower() in _PLACEHOLDERS


def check_item(item: dict, source: str) -> None:
    """Apply the citation check to one item, in place."""
    a = item["anchor"]
    cls = a.get("class")
    if cls == "a":
        ok = locus_exists(a.get("locus", ""), source)
        a["citation_check"] = "found" if ok else "fabricated"
    elif cls == "b":
        title, entry = a.get("title", "").strip(), a.get("entry_id", "").strip()
        if title and entry and entry.lower() != title.lower() and not _placeholder(entry):
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
