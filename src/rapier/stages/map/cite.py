"""map_cite (mechanical) and map_support (optional, a model judgment).

SPARRING spec v1.4 §3.7, "Provenance and anchors per item".

- **Class a, cited source.** The locus must exist in the source. Whitespace
  (line breaks included), letter case and inline markup are normalized on both
  sides. A quote with no ellipsis must appear in the source. A quote with an
  ellipsis passes only if every fragment does, in order, within the cited
  section (or within one section, when the locus names none). A quote or
  fragment that fails is judged on its own:
  - **misquoted** if a same-length passage of the source matches it at
    ``misquote_threshold`` (0.8, fixed in the manifest) or more word-level
    similarity and no differing word is a negation, a modal or a number. The
    item moves to class c, tagged misquoted, with the nearest passage beside it.
  - **fabricated** otherwise, including a changed negation, modal or number, or
    fragments that are each in the source but not in order in one section. A
    fabricated anchor fails the map gate.
  A locus with no quote passes if its text is in the source, or every section it
  names exists.
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
from difflib import SequenceMatcher
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
    t = t.replace("\\n", " ").replace("\\t", " ")          # escaped line breaks in a quote
    t = re.sub(r"[\u2190-\u21ff\u2500-\u257f\u25a0-\u25ff\u27f0-\u27ff]+", " ", t)  # arrows, box drawing
    t = re.sub(r"^\s*>+\s?", " ", t, flags=re.M)          # blockquote marks
    t = re.sub(r"[*`|]", " ", t)                           # emphasis, code, table pipes
    t = re.sub(r"(?<!\w)_|_(?!\w)", " ", t)                # _emphasis_, not snake_case
    t = re.sub(r"[\u2014\u2013\u2212]|--", "-", t)          # dashes
    t = re.sub(r"\s-\s", " ", t)                           # a spaced dash is punctuation
    t = re.sub(r"[\u2018\u2019]", "'", t)
    t = re.sub(r"[\u201c\u201d]", '"', t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([.,;:!?)])", r"\1", t)                # "nothing ." -> "nothing."
    t = re.sub(r"([(\[])\s+", r"\1", t)                     # "( `x`" -> "(x"
    return t.strip().lower()


# A single-quoted quote may hold an apostrophe between letters ("today's").
_QUOTE = re.compile(r"[\"\u201c]([^\"\u201d]+)[\"\u201d]|(?<![A-Za-z0-9])['\u2018`]"
                    r"((?:[^'\u2019`]|(?<=[A-Za-z])['\u2019](?=[A-Za-z]))+)['\u2019`](?![A-Za-z])")
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


def _quotes(loc: str) -> list[list[str]]:
    """Each quote in a locus, as its list of ellipsis-separated pieces."""
    return [ps for ps in (_pieces(m.group(1) or m.group(2) or "") for m in _QUOTE.finditer(loc)) if ps]


_HEADING = re.compile(r"^\s*#{1,6}\s", re.M)


def _sections(source: str) -> list[str]:
    """The source split at its markdown headings, each section folded."""
    cuts = [m.start() for m in _HEADING.finditer(source)]
    bounds = [0, *cuts, len(source)]
    return [_fold(source[a:b]) for a, b in zip(bounds, bounds[1:]) if source[a:b].strip()]


MISQUOTE_THRESHOLD = 0.8

_WORD = re.compile(r"[a-z0-9]+(?:['.\-][a-z0-9]+)*")
_NEGATIONS = {"not", "no", "never", "none", "nor", "neither", "without", "nothing", "nobody",
              "nowhere", "cannot", "unless"}
_MODALS = {"can", "could", "may", "might", "must", "shall", "should", "will", "would", "need",
           "needs", "ought", "cannot"}
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                 "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
                 "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million",
                 "billion", "half", "once", "twice", "first", "second", "third", "single",
                 "double", "dozen"}


def _words(folded: str) -> list[str]:
    return _WORD.findall(folded)


def _meaning_word(w: str) -> bool:
    """A word whose change alters what a quote says: a negation, a modal, a number."""
    return (w in _NEGATIONS or w in _MODALS or w in _NUMBER_WORDS or w.endswith("n't")
            or any(ch.isdigit() for ch in w))


def nearest_passage(quote: str, scope: str) -> tuple[float, str, list[str]]:
    """The same-length passage of ``scope`` most like ``quote``, by word-level
    similarity: ``(similarity, passage, differing words)``. Both are folded."""
    q = _words(_fold(quote))
    s = _words(_fold(scope))
    k = len(q)
    if not k or len(s) < k:
        return 0.0, "", q
    need = {}
    for w in q:
        need[w] = need.get(w, 0) + 1
    have: dict[str, int] = {}
    overlap = 0

    def add(w, d):
        nonlocal overlap
        if w not in need:
            return
        before = min(have.get(w, 0), need[w])
        have[w] = have.get(w, 0) + d
        overlap += min(have[w], need[w]) - before

    for w in s[:k]:
        add(w, 1)
    best = (0.0, 0)
    # SequenceMatcher's ratio is 2M/(2k) = M/k, and M can't exceed the multiset
    # overlap, so a window whose overlap is below the best so far is skipped.
    for i in range(len(s) - k + 1):
        if i:
            add(s[i - 1], -1)
            add(s[i + k - 1], 1)
        if overlap / k <= best[0]:
            continue
        r = SequenceMatcher(None, q, s[i:i + k], autojunk=False).ratio()
        if r > best[0]:
            best = (r, i)
    r, i = best
    w = s[i:i + k]
    diff: list[str] = []
    for tag, a1, a2, b1, b2 in SequenceMatcher(None, q, w, autojunk=False).get_opcodes():
        if tag != "equal":
            diff += q[a1:a2] + w[b1:b2]
    return r, " ".join(w), diff


def judge_failed(quote: str, scope: str, threshold: float) -> dict:
    """A quote (or fragment) not found: misquoted or fabricated."""
    r, passage, diff = nearest_passage(quote, scope)
    meaning = sorted({w for w in diff if _meaning_word(w)})
    if r >= threshold and not meaning:
        return {"status": "misquoted", "quote": quote, "passage": passage,
                "similarity": round(r, 3)}
    why = (f"a negation, modal or number differs ({', '.join(meaning)})" if r >= threshold and meaning
           else f"no passage of the source is close (best word-level similarity {r:.2f})")
    return {"status": "fabricated", "quote": quote, "passage": passage, "similarity": round(r, 3),
            "why": why}


def _in_order(folded: list[str], sec: str) -> bool:
    at = 0
    for p in folded:
        j = sec.find(p, at)
        if j < 0:
            return False
        at = j + len(p)
    return True


def check_quote(pieces: list[str], source: str, section: str | None,
                threshold: float = MISQUOTE_THRESHOLD) -> dict:
    """One quote's verdict: ``{"status": found|misquoted|fabricated, ...}``.
    ``section`` is the cited section's raw text, or None when none is cited."""
    folded = [_fold(p) for p in pieces]
    if len(folded) == 1:
        if folded[0] in _fold(source):
            return {"status": "found"}
        return judge_failed(pieces[0], source, threshold)
    scopes = [_fold(section)] if section is not None else _sections(source)
    if any(_in_order(folded, sec) for sec in scopes):
        return {"status": "found"}
    scope_raw = section if section is not None else source
    scope_f = _fold(scope_raw)
    fragments = []
    for p, f in zip(pieces, folded):
        if f in scope_f:
            fragments.append({"status": "found", "quote": p})
        else:
            fragments.append(judge_failed(p, scope_raw, threshold))
    if any(fr["status"] == "fabricated" for fr in fragments):
        bad = next(fr for fr in fragments if fr["status"] == "fabricated")
        return {**bad, "fragments": fragments}
    if any(fr["status"] == "misquoted" for fr in fragments):
        mis = next(fr for fr in fragments if fr["status"] == "misquoted")
        return {**mis, "fragments": fragments}
    return {"status": "fabricated", "quote": " … ".join(pieces), "fragments": fragments,
            "why": "every fragment is in the source, but not in order within "
                   + ("the cited section" if section is not None else "one section")}


_LEAD_REF = re.compile(r"^\s*(?:(?:§\s*|section\s+)\d+(?:\.\d+)*[^:\u2014-]*?[:\u2014-]\s+)", re.I)


def _section_exists(num: str, source: str) -> bool:
    n = re.escape(num)
    heading = re.compile(rf"^\s*(?:#+\s*)?(?:§\s*|section\s+)?{n}(?:[\s.:)]|$)", re.I | re.M)
    return bool(heading.search(source) or re.search(rf"§\s*{n}(?!\.?\d)", source))


_HEAD_LINE = re.compile(r"^(\s*)(#{1,6})\s.*$", re.M)


def section_text(num: str, source: str) -> str | None:
    """The raw text of section ``num``, subsections included: from its heading to
    the next heading at the same or a higher level. None if it has no heading."""
    n = re.escape(num)
    head = re.compile(rf"^\s*(#{{1,6}})\s*(?:§\s*|section\s+)?{n}(?:[\s.:)]|$).*$", re.I | re.M)
    m = head.search(source)
    if not m:
        return None
    level = len(m.group(1))
    for h in _HEAD_LINE.finditer(source, m.end()):
        if len(h.group(2)) <= level:
            return source[m.start():h.start()]
    return source[m.start():]


def _section_before(loc: str, pos: int, source: str) -> str | None:
    """The raw text of the section a quote at ``pos`` cites: the nearest section
    reference before it in the locus (outside any quote), else the first one
    after the last quote, else None."""
    outside = _QUOTE.sub(lambda mm: " " * len(mm.group(0)), loc)
    refs = [(mm.start(), mm.group(1) or mm.group(2)) for mm in _SECTION_ANY.finditer(outside)]
    before = [n for at, n in refs if at < pos]
    for n in reversed(before) if before else [n for _, n in refs]:
        txt = section_text(n, source)
        if txt is not None:
            return txt
    return None


_IDENT = re.compile(r"^[A-Za-z0-9][\w.\-/:]*[A-Za-z0-9_]$")


def _identifier(raw: str) -> bool:
    """One token shaped like an identifier: it has ``_``, ``.``, ``/``, ``::``, a
    digit, an internal capital, or a hyphen between letters, and isn't a number."""
    t = raw.strip().strip("`")
    if " " in t or len(t) < 3 or not _IDENT.match(t) or t.replace(".", "").isdigit():
        return False
    return bool(re.search(r"[_./]|::|\d|[a-z][A-Z]|[A-Za-z]-[A-Za-z]", t))


def check_locus(locus: str, source: str, threshold: float = MISQUOTE_THRESHOLD) -> dict:
    """The citation check for one class a locus: ``{"status": found | misquoted
    | fabricated, ...}`` with the failing quote and nearest passage when not found.

    - If the locus quotes the source, every quote is checked (``check_quote``);
      any fabricated quote makes the locus fabricated, else any misquoted one
      makes it misquoted. Only an ELLIPSIS quote is held to the section named
      before it in the locus; a quote with no ellipsis only has to be in the
      source (v1.4), and its nearest passage is sought over the whole source.
    - Otherwise, the whole locus found verbatim counts; failing that, every
      section it names must exist (a "§2 table vs §13" style pointer). A bare
      number is too weak to be a section reference."""
    loc = (locus or "").strip()
    if not loc:
        return {"status": "fabricated", "why": "the locus is empty"}
    quotes = [(mm.start(), _pieces(mm.group(1) or mm.group(2) or "")) for mm in _QUOTE.finditer(loc)]
    quotes = [(at, ps) for at, ps in quotes if ps]
    if quotes:
        verdicts = [check_quote(ps, source, _section_before(loc, at, source), threshold)
                    for at, ps in quotes]
        for st in ("fabricated", "misquoted"):
            hit = next((v for v in verdicts if v["status"] == st), None)
            if hit:
                return hit
        return {"status": "found"}
    bare = loc.strip('"\u201c\u201d\'').strip()
    text = _LEAD_REF.sub("", bare)  # "§11: Lockfile at ..." -> "Lockfile at ..."
    pieces = _pieces(text, min_evidence=True)
    if pieces and all(_fold(p) in _fold(source) for p in pieces):
        return {"status": "found"}
    # A bare identifier ("finding_id", "config.yaml", "eu-west"): found if it is
    # in the source as written. A common word ("service") is not a section, a
    # line or a quoted claim, so it proves nothing (verifier V-1).
    whole = _fold(text)
    if _identifier(text) and whole in _fold(source):
        return {"status": "found"}
    refs = [m.group(1) or m.group(2) for m in _SECTION_ANY.finditer(bare)]
    if refs and all(_section_exists(r, source) for r in refs):
        return {"status": "found"}
    return {"status": "fabricated", "why": "neither the text nor every section it names is in the source"}


def locus_exists(locus: str, source: str) -> bool:
    """True only if the locus is found as cited (a misquote is not found)."""
    return check_locus(locus, source)["status"] == "found"


# An entry identifier that says there is none (verifier M-2): "industry best
# practice" has no entry identifier, and "N/A" is a model saying exactly that.
_PLACEHOLDERS = {"n/a", "na", "n.a.", "none", "null", "nil", "-", "--", "\u2014", "\u2013",
                 "unknown", "tbd", "tba", "general", "generic", "various", "any", "all",
                 "see title", "not applicable", "no entry", "n/a."}


def _placeholder(entry: str) -> bool:
    return entry.strip().strip(".").lower() in _PLACEHOLDERS or entry.strip().lower() in _PLACEHOLDERS


def check_item(item: dict, source: str, threshold: float = MISQUOTE_THRESHOLD) -> None:
    """Apply the citation check to one item, in place."""
    a = item["anchor"]
    cls = a.get("class")
    if cls == "a":
        v = check_locus(a.get("locus", ""), source, threshold)
        if v["status"] == "found":
            a["citation_check"] = "found"
        elif v["status"] == "misquoted":
            item["anchor"] = {"class": "c", "basis": "", "downgraded_from": "a", "misquoted": True,
                              "locus": a.get("locus", ""), "quote": v.get("quote", ""),
                              "nearest_passage": v.get("passage", ""),
                              "similarity": v.get("similarity"),
                              "note": "misquoted: the quote is close to, but not, the source passage"}
        else:
            a["citation_check"] = "fabricated"
            a["fabricated"] = {k: v[k] for k in ("quote", "passage", "similarity", "why") if k in v}
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
        threshold = float(ctx.config.get("misquote_threshold", MISQUOTE_THRESHOLD))
        for it in items:
            check_item(it, env.request, threshold)
        # Attack candidates carry anchors too: a candidate that "landed" must be cited.
        for rec in (m.get("attacks") or {}).values():
            for c in rec.get("candidates", []):
                tmp = {"anchor": dict(c["anchor"])}
                check_item(tmp, env.request, threshold)
                c["anchor"] = tmp["anchor"]
        fab = sum(1 for i in items if i["anchor"].get("citation_check") == "fabricated")
        mis = sum(1 for i in items if i["anchor"].get("misquoted"))
        m["citation_check"] = {"status": "ran", "misquote_threshold": threshold,
                               "fabricated": fab, "misquoted": mis}
        env.add_trace("map_cite", self.kind, f"items={len(items)} fabricated={fab} misquoted={mis}")
        return env


SUPPORT = (
    "You check whether each numbered item's cited anchor supports the item. Respond "
    'STRICT JSON: {"judgments": [{"item": <number>, "support": "supported" | "partly" | '
    '"not supported"}]}.'
)


@register_stage("map_support")
class MapSupportStage(TransformStage):
    """One judging call per unit, over that unit's cited items, so the run's
    planned call count stays a true upper bound."""

    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        m = C.state(env)
        v = C.vendors(env, ctx)
        judge = v["challenger"]
        if not judge:
            m["support_check"] = {"status": "did-not-run", "reason": "no second vendor to judge"}
            env.add_trace("map_support", self.kind, "no second vendor — skipped")
            return env
        counts: dict[str, Any] = {"supported": 0, "partly": 0, "not supported": 0, "not judged": 0}
        n_cited = 0
        for u in C.unit_list(m):
            cited = [i for i in (m.get("items") or []) if (i["regions"] or [None])[0] == u["id"]
                     and i["anchor"].get("class") in ("a", "b")]
            if not cited:
                continue
            n_cited += len(cited)
            listing = "\n".join(f"{k}. ITEM: {it['text']}\n   ANCHOR: {it['anchor']}"
                                for k, it in enumerate(cited, 1))
            verdicts: dict[int, str] = {}
            try:
                reply = C.call(env, role="judge", vendor=judge, purpose="support", region=u["id"],
                               system=ctx.config.get("support_prompt", SUPPORT),
                               prompt=f"SOURCE:\n{env.request}\n\nITEMS:\n{listing}")
                for j in (reply.get("judgments") or []) if isinstance(reply, dict) else []:
                    try:
                        verdicts[int(j.get("item"))] = str(j.get("support", ""))
                    except (TypeError, ValueError, AttributeError):
                        continue
            except Exception:  # noqa: BLE001
                verdicts = {}
            for k, it in enumerate(cited, 1):
                verdict = verdicts.get(k, "")
                if verdict not in ("supported", "partly", "not supported"):
                    verdict = "not judged"
                counts[verdict] += 1
                it["support"] = verdict
                if verdict == "not supported":
                    it["anchor"] = {"class": "c", "basis": "",
                                    "downgraded_from": it["anchor"].get("class"),
                                    "note": "the support check judged the anchor does not support the item"}
        m["support_check"] = {"status": "ran", "vendor": judge, "counts": counts}
        env.add_trace("map_support", self.kind, f"judged={n_cited} {counts}")
        return env
