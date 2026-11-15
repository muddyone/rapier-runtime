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


def _forms(w: str) -> set[str]:
    """The word and its candidate bases with an inflection removed (-s, -es,
    -ed, -ing; a doubled consonant undone; a dropped e restored)."""
    out = {w}
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            b = w[:-len(suf)]
            out |= {b, b + "e"}
            if len(b) >= 3 and b[-1] == b[-2]:
                out.add(b[:-1])
    return out


_MEANING_BASE = frozenset(_NEGATIONS) | frozenset(_MODALS) | frozenset(_NUMBER_WORDS)


def _protected_stem(w: str, protect: frozenset[str]) -> str | None:
    """The protected word ``w`` is a form of, by its stem, or None. Two words
    with the same stem ("starting", "starts") are the same protected word; a
    change between different stems ("starting", "ending") is a protected change
    (verifier V15-1)."""
    if any(ch.isdigit() for ch in w):
        return w
    if w.endswith("n't"):
        return w
    base = _MEANING_BASE | protect
    hit = _forms(w) & base
    if not hit:
        return None
    cur = min(hit)
    while True:  # reduce to a fixed point, so "adds", "added" and "add" share one stem
        nxt = min(_forms(cur) & base)
        if nxt == cur:
            return cur
        cur = nxt


def _edge_trimmed_diff(q: list[str], w: list[str]) -> tuple[list[str], list[str]]:
    """The words that differ between a quote and a window, after dropping the
    window's own unmatched words at either edge: a same-length window that slid
    into the next sentence picks up words the quote never had (verifier V15-2)."""
    ops = SequenceMatcher(None, q, w, autojunk=False).get_opcodes()
    while ops and ops[0][0] == "insert":
        ops = ops[1:]
    while ops and ops[-1][0] == "insert":
        ops = ops[:-1]
    qd, wd = [], []
    for tag, a1, a2, b1, b2 in ops:
        if tag != "equal":
            qd += q[a1:a2]
            wd += w[b1:b2]
    return qd, wd


def _windows_over(quote: str, scope: str, threshold: float) -> list[tuple[float, list[str], list[str]]]:
    """Every same-length passage at or above ``threshold``, best first, as
    ``(similarity, quote words, passage words)``."""
    q = _words(_fold(quote))
    s = _words(_fold(scope))
    k = len(q)
    if not k or len(s) < k:
        return []
    need: dict[str, int] = {}
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
    out = []
    for i in range(len(s) - k + 1):
        if i:
            add(s[i - 1], -1)
            add(s[i + k - 1], 1)
        if overlap / k < threshold:
            continue
        r = SequenceMatcher(None, q, s[i:i + k], autojunk=False).ratio()
        if r >= threshold:
            out.append((r, q, s[i:i + k]))
    out.sort(key=lambda x: -x[0])
    return out


def judge_failed(quote: str, scope: str, threshold: float,
                 protect: frozenset[str] = frozenset()) -> dict:
    """A quote (or fragment) not found: misquoted (a near-quote) or fabricated.

    A near-quote is a same-length passage at ``threshold`` or more word-level
    similarity whose differing words change no protected word: a negation, a
    modal, a number, or (``protect``) a direction or quantity word. Protected
    words are compared by stem, and the window's own edge words are not counted
    as differences. Any passage that qualifies makes it a near-quote."""
    best_bad = None
    for r, q, w in _windows_over(quote, scope, threshold):
        qd, wd = _edge_trimmed_diff(q, w)
        qs = sorted(x for x in (_protected_stem(t, protect) for t in qd) if x)
        ws = sorted(x for x in (_protected_stem(t, protect) for t in wd) if x)
        if qs == ws:
            return {"status": "misquoted", "kind": "near-quote", "quote": quote,
                    "passage": " ".join(w), "similarity": round(r, 3)}
        if best_bad is None:
            changed = sorted(set(qs) ^ set(ws)) or sorted(set(qs + ws))
            best_bad = (r, w, changed)
    if best_bad:
        r, w, changed = best_bad
        return {"status": "fabricated", "quote": quote, "passage": " ".join(w), "similarity": round(r, 3),
                "why": f"a protected word differs ({', '.join(changed)})"}
    r, passage, _ = nearest_passage(quote, scope)
    return {"status": "fabricated", "quote": quote, "passage": passage, "similarity": round(r, 3),
            "why": f"no passage of the source is close (best word-level similarity {r:.2f})"}


def _in_order(folded: list[str], sec: str) -> bool:
    at = 0
    for p in folded:
        j = sec.find(p, at)
        if j < 0:
            return False
        at = j + len(p)
    return True


def check_quote_v14(pieces: list[str], source: str, section: str | None,
                    threshold: float = MISQUOTE_THRESHOLD,
                    protect: frozenset[str] = frozenset()) -> dict:
    """One quote's v1.4 verdict: ``{"status": found|misquoted|fabricated, ...}``.
    ``section`` is the cited section's raw text, or None when none is cited."""
    folded = [_fold(p) for p in pieces]
    if len(folded) == 1:
        if folded[0] in _fold(source):
            return {"status": "found"}
        return judge_failed(pieces[0], source, threshold, protect)
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
            fragments.append(judge_failed(p, scope_raw, threshold, protect))
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


def _check_locus_core(locus: str, source: str, threshold: float, quote_check) -> dict:
    """The citation check's shared core for one class a locus: ``{"status": found
    | misquoted | fabricated, ...}`` with the failing quote and nearest passage.

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
    quotes = [(mm.start(), mm.group(1) or mm.group(2) or "") for mm in _QUOTE.finditer(loc)]
    quotes = [(at, raw, _pieces(raw)) for at, raw in quotes]
    quotes = [(at, raw, ps) for at, raw, ps in quotes if ps]
    if quotes:
        verdicts = [quote_check(ps, source, _section_before(loc, at, source), threshold, raw)
                    for at, raw, ps in quotes]
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


def check_locus_v14(locus: str, source: str, threshold: float = MISQUOTE_THRESHOLD) -> dict:
    """The v1.4 class a check, kept so a re-score can show both versions."""
    return _check_locus_core(locus, source, threshold,
                             lambda ps, src, sec, thr, _raw=None: check_quote_v14(ps, src, sec, thr))


# ---------------------------------------------------------------------------
# v1.5 (SPARRING spec v1.5 §3.7, D-128 a). Ported step for step from the
# reviewed reference re-score (sparring-framework docs/spars/
# 202611141303-map-mode-quality-bar/rescore-v15-2026-11-14.py), which is the
# acceptance test: the same D-100 counts in this build.
# ---------------------------------------------------------------------------

# Direction and quantity words, protected in near-quotes. The list is fixed in
# the manifest (map_cite config) and so hashed with the method.
DIRECTION_WORDS = frozenset({
    "start", "starts", "started", "end", "ends", "ended", "begin", "begins", "finish", "finishes",
    "before", "after", "first", "last", "earlier", "later", "earliest", "latest", "all", "some",
    "any", "every", "each", "most", "few", "many", "more", "less", "fewer", "increase",
    "increases", "increased", "decrease", "decreases", "decreased", "higher", "lower", "above",
    "below", "up", "down", "min", "max", "minimum", "maximum", "always", "sometimes", "only",
    "include", "includes", "exclude", "excludes", "enable", "enabled", "disable", "disabled",
    "allow", "allows", "deny", "denies", "add", "adds", "remove", "removes", "open", "close",
    "on", "off", "in", "out", "from", "to", "into", "over", "under", "inside", "outside", "both",
    "either"})
ELLIPSIS_MIN_WORDS = 3
ELLIPSIS_MAX_GAP = 300
# Words an ellipsis must not skip inside one sentence: a negation, a modal, or an
# exclusion word.
_EXCLUSION = frozenset({"only", "except", "unless", "without", "never"})
_GAP_PROTECTED = frozenset(_NEGATIONS) | frozenset(_MODALS) | _EXCLUSION
_SENT = re.compile(r"[.!?;]\s|[.!?;]$")
_BOXCH = "│┃|─━▶▸►→←↓↑┌┐└┘├┤┬┴┼╔╗╚╝═║"
_CELLSEP = re.compile("[" + _BOXCH + "]+")
_NONSEP = re.compile("[^" + _BOXCH + "]+")
_ELL = re.compile(r"\.\.\.|…")
_MINF = 4  # a diagram or table fragment shorter than this must equal a whole cell
_DRAW = re.compile(r"[│┃─━▶┌┐└┘├┤┬┴┼╔╗╚╝═║]")  # box and arrow drawing (not the table pipe)


class Rules:
    """The v1.5 check's parameters, all from the manifest."""

    def __init__(self, threshold: float = MISQUOTE_THRESHOLD, direction_words=DIRECTION_WORDS,
                 min_words: int = ELLIPSIS_MIN_WORDS, max_gap: int = ELLIPSIS_MAX_GAP):
        self.threshold = float(threshold)
        self.direction = frozenset(direction_words)
        self.min_words = int(min_words)
        self.max_gap = int(max_gap)


class _Index:
    """What the v1.5 rules need from one source: its fenced blocks (where the
    gap rules don't apply), its diagrams and its table rows."""

    def __init__(self, source: str):
        fenced = re.findall(r"```.*?```", source, re.S)
        self.fenced = [_fold(b) for b in fenced]
        self.blocks = [b.split("\n") for b in fenced if _DRAW.search(b)]
        # A diagram need not be fenced (verifier V15-3): a run of two or more
        # consecutive lines drawn with box or arrow characters, outside any fence.
        inside, run = False, []
        for ln in source.split("\n"):
            if ln.lstrip().startswith("```"):
                inside = not inside
                if run:
                    self._add_run(run)
                run = []
                continue
            if not inside and _DRAW.search(ln):
                run.append(ln)
            else:
                self._add_run(run)
                run = []
        self._add_run(run)
        self.rows = [ln for ln in source.split("\n") if ln.count("|") >= 2]
        self._cells: dict[int, list] = {}

    def _add_run(self, run: list[str]) -> None:
        if len(run) >= 2:
            self.blocks.append(list(run))

    def cells(self, k: int) -> list:
        if k not in self._cells:
            self._cells[k] = _diagram_cells(self.blocks[k])
        return self._cells[k]


_INDEX_CACHE: dict[int, tuple[str, _Index]] = {}


def _index(source: str) -> _Index:
    key = hash(source)
    hit = _INDEX_CACHE.get(key)
    if hit and hit[0] is source:
        return hit[1]
    ix = _Index(source)
    _INDEX_CACHE.clear()
    _INDEX_CACHE[key] = (source, ix)
    return ix


def _dens(t: str) -> str:
    return re.sub(r"\s+", "", _fold(t))


def _unescape(t: str) -> str:
    return t.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")


def _gap_protected(w: str) -> bool:
    return w in _GAP_PROTECTED or w.endswith("n't")


def _gap_kind(text: str) -> str:
    """Skipped text between two fragments: ``ok``; ``cross`` (crosses a sentence
    break: misquoted); ``cross-reversal`` (crosses one AND skips a protected word:
    misquoted plus the REVIEW trigger); ``bad`` (skips a protected word inside
    one sentence: fabricated)."""
    t = _fold(text)
    crosses = bool(_SENT.search(t))
    prot = any(_gap_protected(w) for w in _words(t))
    if prot and not crosses:
        return "bad"
    if prot:
        return "cross-reversal"
    return "cross" if crosses else "ok"


def _ordered_spans(folded: list[str], sec: str):
    at, spans = 0, []
    for p in folded:
        j = sec.find(p, at)
        if j < 0:
            return None
        spans.append((j, j + len(p)))
        at = j + len(p)
    return spans


def _in_order_limited(folded: list[str], sec: str, R: Rules) -> bool:
    if any(len(_words(f)) < R.min_words for f in folded):
        return False
    at, prev_end = 0, None
    for p in folded:
        j = sec.find(p, at)
        if j < 0:
            return False
        if prev_end is not None and j - prev_end > R.max_gap:
            return False
        prev_end = j + len(p)
        at = prev_end
    return True


def check_quote_v15(pieces: list[str], source: str, section: str | None, R: Rules,
                    hits: list | None = None, raw: str | None = None) -> dict:
    """One quote's v1.5 verdict. v1.4's verdict first, without limits; a quote it
    finds only through an ellipsis splice is then held to v1.5's limits."""
    if len(pieces) > 1 and raw is not None and _fold(raw) in _fold(source):
        return {"status": "found", "how": "verbatim"}  # the source has the ellipsis itself (V15-4)
    v = check_quote_v14(pieces, source, section, R.threshold)
    if v["status"] != "found":
        return check_quote_v14(pieces, source, section, R.threshold, R.direction)
    if len(pieces) < 2:
        return v
    ix = _index(source)
    folded = [_fold(p) for p in pieces]
    scopes = [_fold(section)] if section is not None else _sections(source)
    joined = " … ".join(pieces)
    for sec in scopes:
        sp = _ordered_spans(folded, sec)
        if not sp or all(any(f in fb for fb in ix.fenced) for f in folded):
            continue  # not here, or inside a code/data block, where the gap rules don't apply
        kinds = [_gap_kind(sec[a[1]:b[0]]) for a, b in zip(sp, sp[1:])]
        if "bad" in kinds:
            return {"status": "fabricated", "quote": joined,
                    "why": "the ellipsis skips a negation, modal or exclusion word inside one sentence"}
        if "cross-reversal" in kinds:
            if hits is not None:
                hits.append(joined)
            return {"status": "misquoted", "kind": "elision", "reversal": True, "quote": joined,
                    "why": "possible reversal by ellipsis: it crosses a sentence break and skips "
                           "a negation, modal or exclusion word"}
        if "cross" in kinds:
            return {"status": "misquoted", "kind": "elision", "quote": joined,
                    "why": "the ellipsis crosses a sentence break"}
        break
    if any(_in_order_limited(folded, sec, R) for sec in scopes):
        return v
    return {"status": "misquoted", "kind": "elision", "quote": joined,
            "why": f"an ellipsis splice outside the limits (a fragment under {R.min_words} words, "
                   f"or a gap over {R.max_gap} characters)"}


def _edge_cut_bad(q: str, ps: list[str], sec_raw: str | None, source: str) -> bool:
    """A leading or trailing ellipsis that cuts just before or after a protected
    word, where the source has no ellipsis of its own."""
    lead = bool(re.match(r"\s*(\.\.\.|…)", q))
    trail = bool(re.search(r"(\.\.\.|…)\s*$", q))
    if not (lead or trail):
        return False
    sec = _fold(sec_raw if sec_raw is not None else source)
    first, last = _fold(ps[0]), _fold(ps[-1])
    if lead:
        k = sec.find(first)
        if k > 0 and re.search(r"(\.\.\.|…)[\s:;,.\"'\u201c\u2018]*$", sec[:k]):
            lead = False
        if lead and k > 0:
            before = _words(sec[max(0, k - 40):k])
            if before and _gap_protected(before[-1]):
                return True
    if trail:
        k = sec.find(last)
        if k >= 0 and re.match(r"[\s:;,.!?\"'\u201d\u2019]*(\.\.\.|…)", sec[k + len(last):]):
            trail = False
        if trail and k >= 0:
            after = _words(sec[k + len(last):k + len(last) + 40])
            if after and _gap_protected(after[0]):
                return True
    return False


def _diagram_cells(lines: list[str]) -> list:
    """Cells of a diagram: runs of non-separator text with their column spans,
    joined down consecutive lines where the spans overlap. Each cell is
    ``(first line, last line, its text with whitespace removed, {line: (start, end)})``."""
    segs = []
    for li, ln in enumerate(lines):
        for mm in _NONSEP.finditer(ln):
            t = mm.group(0)
            if t.strip():
                a = mm.start() + len(t) - len(t.lstrip())
                e = mm.end() - (len(t) - len(t.rstrip()))
                segs.append([li, a, e, t.strip()])
    cells, used = [], set()
    for k, sg in enumerate(segs):
        if k in used:
            continue
        cur, last = [sg], sg
        used.add(k)
        for j in range(k + 1, len(segs)):
            o = segs[j]
            if j in used:
                continue
            if o[0] == last[0] + 1 and o[1] < last[2] and o[2] > last[1]:
                cur.append(o)
                used.add(j)
                last = o
        cells.append((cur[0][0], cur[-1][0], _dens("".join(x[3] for x in cur)),
                      {x[0]: (x[1], x[2]) for x in cur}))
        for x in cur:
            cells.append((x[0], x[0], _dens(x[3]), {x[0]: (x[1], x[2])}))
    return cells


def _cells_touch(c1, c2, lines: list[str]) -> bool:
    """Two cells touch: on one line with only separators or spaces between them,
    or on adjacent lines with overlapping columns. (Lines alone are not enough:
    the two ends of a wide diagram are not one quote.)"""
    for l1, (a1, e1) in c1[3].items():
        for l2, (a2, e2) in c2[3].items():
            if l1 == l2:
                lo, hi = (e1, a2) if e1 <= a2 else (e2, a1)
                if lo <= hi and not re.search(r"\w", lines[l1][lo:hi]):
                    return True
            elif abs(l1 - l2) == 1 and a1 < e2 and a2 < e1:
                return True
    return False


def _groups(q: str) -> list:
    gs = []
    for part in _ELL.split(q):
        frs = [f.strip() for f in _CELLSEP.split(part) if re.search(r"\w", f)]
        if frs:
            gs.append([(_dens(f), len(re.sub(r"\W", "", f)) < _MINF) for f in frs])
    return gs


def _frag_match_raw(q: str, ix: _Index, R: Rules, limited: bool) -> str | None:
    gs = _groups(q)
    if not gs or not any(not short for g in gs for _, short in g):
        return None  # needs at least one fragment of 4+ letters or digits
    flat = [x for g in gs for x in g]
    for row in ix.rows:  # a table row: fragments in order; a short one must be a whole cell
        cellz = [_dens(c) for c in row.split("|")]
        r, pos, ok = _dens(row), 0, True
        for f, short in flat:
            if short:
                k = next((r.find(c, pos) for c in cellz if c == f and r.find(c, pos) >= 0), -1)
            else:
                k = r.find(f, pos)
            if k < 0:
                ok = False
                break
            if limited and pos > 0 and _gap_kind(r[pos:k].replace("|", " ")) == "bad":
                ok = False
                break
            pos = k + len(f)
        if ok:
            return "table-row"
    for bi, b in enumerate(ix.blocks):  # a diagram: a chain of touching cells
        cells = ix.cells(bi)

        def places(f, short):
            hits = [c for c in cells if (c[2] == f if short else f in c[2])]
            if not hits:
                return []
            mn = min(c[1] - c[0] for c in hits)
            return [c for c in hits if c[1] - c[0] == mn]

        start, ok, prev_line = 0, True, None
        offs = [0]
        for ln in b:
            offs.append(offs[-1] + len(ln) + 1)
        for g in gs:
            if limited and len(gs) > 1 and sum(len(re.findall(r"[a-z0-9]+", f)) for f, _ in g) < R.min_words:
                ok = False
                break
            P = [places(f, sh) for f, sh in g]
            if not all(P):
                ok = False
                break
            reach = [x for x in P[0] if x[0] >= start]
            for nxt in P[1:]:
                reach = [n for n in nxt if any(_cells_touch(x, n, b) for x in reach)]
                if not reach:
                    break
            if not reach:
                ok = False
                break
            here = min(x[0] for x in reach)
            if limited and prev_line is not None and offs[here] - offs[prev_line + 1] > R.max_gap:
                ok = False
                break
            prev_line = max(x[1] for x in reach)
            start = here
        if ok:
            return "diagram-cell"
    return None


def _frag_match(q: str, ix: _Index, R: Rules) -> str | None:
    """``table-row`` / ``diagram-cell`` when the quote's fragments sit in one row
    or touching cells within the limits; ``outside-limits`` when they do only
    without them; None when they don't."""
    r = _frag_match_raw(q, ix, R, limited=True)
    if r:
        return r
    return "outside-limits" if _frag_match_raw(q, ix, R, limited=False) else None


def check_locus(locus: str, source: str, threshold: float = MISQUOTE_THRESHOLD,
                rules: Rules | None = None) -> dict:
    """The v1.5 class a citation check for one locus: ``{"status": found |
    misquoted | fabricated, ...}``, with ``kind`` (near-quote or elision) on a
    misquote, ``reversal`` when an ellipsis may reverse the source, and ``how``
    (escape, table-row, diagram-cell) when a v1.5 rule found it.

    In order: the v1.4 core with the v1.5 quote rules; an edge-cut check on what
    it found; a retry with string escapes removed; then, for what is still not
    found, the table-row and diagram-cell rule, quote by quote (or on the bare
    text of an unquoted locus)."""
    R = rules or Rules(threshold)
    hits: list[str] = []

    def qc(ps, src, sec, _thr, raw=None):
        return check_quote_v15(ps, src, sec, R, hits, raw)

    def done(v):
        if hits:
            v = {**v, "reversal": True, "reversal_quotes": sorted(set(hits))}
        return v

    base = _check_locus_core(locus, source, R.threshold, qc)
    if base["status"] == "found":
        for mm in _QUOTE.finditer(locus or ""):
            q = mm.group(1) or mm.group(2) or ""
            ps = _pieces(q)
            if ps and _edge_cut_bad(q, ps, _section_before(locus, mm.start(), source), source):
                return done({"status": "fabricated", "quote": q,
                             "why": "an ellipsis at the edge of the quote cuts at a negation, "
                                    "modal or exclusion word"})
        return done({"status": "found"})
    loc = _unescape(locus or "")
    b2 = _check_locus_core(loc, source, R.threshold, qc)
    if b2["status"] == "found":
        return done({"status": "found", "how": "escape"})
    ix = _index(source)
    found_q = [(mm.start(), mm.group(1) or mm.group(2) or "") for mm in _QUOTE.finditer(loc)]
    found_q = [(at, q) for at, q in found_q if q.strip()]
    if not found_q:
        bare = _LEAD_REF.sub("", loc.strip('"\u201c\u201d\'').strip())
        # V-1 still holds for unquoted text: one common word proves nothing, even
        # when it is a diagram or table cell. Two or more words, or an identifier.
        if len(_words(_fold(bare))) < 2 and not _identifier(bare):
            return done(b2)
        fm = _frag_match(bare, ix, R)
        if fm == "outside-limits":
            return done({"status": "misquoted", "kind": "elision", "quote": bare,
                         "why": "table or diagram fragments outside the ellipsis limits"})
        return done({"status": "found", "how": fm} if fm else b2)
    how: list[str] = []
    worst = None
    for at, q in found_q:
        ps = _pieces(q)
        if not ps:
            continue
        sec_raw = _section_before(loc, at, source)
        v = check_quote_v15(ps, source, sec_raw, R, hits, q)
        if v["status"] == "found":
            if _edge_cut_bad(q, ps, sec_raw, source):
                worst = {"status": "fabricated", "quote": q,
                         "why": "an ellipsis at the edge of the quote cuts at a negation, "
                                "modal or exclusion word"}
            continue
        fm = _frag_match(q, ix, R)
        if fm == "outside-limits":
            if worst is None or worst["status"] != "fabricated":
                worst = worst if worst and worst["status"] == "misquoted" else {
                    "status": "misquoted", "kind": "elision", "quote": q,
                    "why": "table or diagram fragments outside the ellipsis limits"}
            continue
        if fm:
            how.append(fm)
            continue
        if v["status"] == "fabricated" or worst is None:
            worst = v
    if worst:
        return done(worst)
    return done({"status": "found", "how": ",".join(how) or "v1.4"})


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


def check_item(item: dict, source: str, threshold: float = MISQUOTE_THRESHOLD,
               rules: Rules | None = None) -> None:
    """Apply the citation check to one item, in place."""
    a = item["anchor"]
    cls = a.get("class")
    if cls == "a":
        v = check_locus(a.get("locus", ""), source, threshold, rules)
        if v["status"] == "found":
            a["citation_check"] = "found"
        elif v["status"] == "misquoted":
            kind = v.get("kind") or "near-quote"
            item["anchor"] = {"class": "c", "basis": "", "downgraded_from": "a", "misquoted": True,
                              "misquote_kind": kind, "locus": a.get("locus", ""),
                              "quote": v.get("quote", ""),
                              "nearest_passage": v.get("passage", ""),
                              "similarity": v.get("similarity"),
                              "note": ("misquoted: the quote is close to, but not, the source passage"
                                       if kind == "near-quote" else
                                       f"misquoted (elision): {v.get('why', 'an ellipsis splice')}")}
        else:
            a["citation_check"] = "fabricated"
            a["fabricated"] = {k: v[k] for k in ("quote", "passage", "similarity", "why") if k in v}
        if v.get("reversal"):
            item["anchor"]["possible_reversal"] = v.get("reversal_quotes") or True
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
        rules = Rules(threshold,
                      ctx.config.get("protected_direction_words") or DIRECTION_WORDS,
                      ctx.config.get("ellipsis_min_words", ELLIPSIS_MIN_WORDS),
                      ctx.config.get("ellipsis_max_gap", ELLIPSIS_MAX_GAP))
        for it in items:
            check_item(it, env.request, threshold, rules)
        # Attack candidates carry anchors too: a candidate that "landed" must be cited.
        for rec in (m.get("attacks") or {}).values():
            for c in rec.get("candidates", []):
                tmp = {"anchor": dict(c["anchor"])}
                check_item(tmp, env.request, threshold, rules)
                c["anchor"] = tmp["anchor"]
        fab = sum(1 for i in items if i["anchor"].get("citation_check") == "fabricated")
        mis = sum(1 for i in items if i["anchor"].get("misquoted"))
        near = sum(1 for i in items if i["anchor"].get("misquote_kind") == "near-quote")
        rev = [i["id"] for i in items if i["anchor"].get("possible_reversal")]
        m["citation_check"] = {"status": "ran", "misquote_threshold": threshold,
                               "ellipsis_min_words": rules.min_words, "ellipsis_max_gap": rules.max_gap,
                               "fabricated": fab, "misquoted": mis, "near_quotes": near,
                               "elisions": mis - near, "possible_reversal": rev}
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
