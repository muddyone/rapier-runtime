"""Extract checkable artifacts from recommendation text for the citation gate.

The vendored grounding checker (``verify_grounding.classify``) routes a ref to a
backend by pattern — CVE (MITRE CVE Services), CWE (MITRE), RFC (IETF datatracker),
DOI / arXiv (Crossref), url (liveness), code (``path:line``). This module's only job is to *find* those
tokens in free-form prose and hand them over as artifact dicts; the checker
decides what each one is and whether it resolves against external canon.

Extraction is conservative: it emits a ref only for a token shape one of those
backends can actually resolve, and it drops a token nested inside a longer one
(a DOI inside a URL) so the gate never double-counts. This is the "automatic
extraction" the citation gate deferred in M1.
"""
from __future__ import annotations

import re

# Each shape mirrors a ``verify_grounding.classify()`` family, so every token we
# emit routes to a real backend. Overlaps are resolved widest-first below, so a
# DOI nested in a URL loses to the enclosing URL.
_PATTERNS = (
    re.compile(r"https?://[^\s)>\]}\"'`]+", re.I),                              # url
    re.compile(r"\b10\.\d{4,9}/[^\s)>\]}\"'`]+", re.I),                         # doi
    re.compile(r"\barxiv[:/ ]?\d{4}\.\d{4,5}\b", re.I),                        # arxiv
    re.compile(r"\bCVE-\d{4}-\d{3,}\b", re.I),                                 # cve
    re.compile(r"\bCWE-\d+\b", re.I),                                          # cwe
    re.compile(r"\bRFC[\s-]?\d+\b", re.I),                                     # rfc
    re.compile(r"\b[\w/][\w/.\-]*\.(?:php|py|js|ts|go|java|rb|cpp|sql):\d+\b"),  # code path:line
    # Reporter citation, e.g. "347 U.S. 483" / "404 F.3d 821". Highly distinctive, so it
    # is safe to match inside prose; backend_law resolves it exactly via CourtListener.
    re.compile(r"\b\d+\s+(?:U\.?\s?S\.?|S\.\s?Ct\.|L\.\s?Ed\.(?:\s?2d)?|F\.\s?\d?d|"
               r"F\.\s?Supp\.(?:\s?\d?d)?|N\.E\.\s?\d?d|N\.W\.\s?\d?d|S\.E\.\s?\d?d|"
               r"S\.W\.\s?\d?d|P\.\s?\d?d|A\.\s?\d?d)\s+\d+\b"),                      # law: reporter
    # Adversarial case name with capitalised parties, swallowing its own reporter cite when
    # present so "Brown v. Board of Education, 347 U.S. 483" is ONE artifact rather than two
    # (the nested reporter span is dropped by the overlap rule below). Each party run
    # alternates capitalised words with lowercase connectors but always ENDS capitalised, so
    # it cannot bleed into the following clause. " v. " between two capitalised runs is
    # essentially always a case, so prose false positives are rare.
    re.compile(r"\b[A-Z][\w.'&-]*(?:\s+(?:of|the|and|for|in|de|van|ex|rel\.)\s+[A-Z][\w.'&-]*"
               r"|\s+[A-Z][\w.'&-]*)*"
               r"\s+v\.?\s+"
               r"[A-Z][\w.'&-]*(?:\s+(?:of|the|and|for|in|de|van)\s+[A-Z][\w.'&-]*"
               r"|\s+[A-Z][\w.'&-]*)*"
               r"(?:,\s*\d+\s+(?:U\.?\s?S\.?|S\.\s?Ct\.|L\.\s?Ed\.(?:\s?2d)?|F\.\s?\d?d|"
               r"F\.\s?Supp\.(?:\s?\d?d)?|P\.\s?\d?d|A\.\s?\d?d)\s+\d+)?"),           # law: case name
    re.compile(r"\b(?:U\.?S\.?\s*)?[Pp]atent\s*(?:No\.?)?\s*\d[\d,]*\b"),              # patent
)

_TRIM = " \t\r\n.,;:!?)]}>\"'`"


def _line_context(text: str, start: int, end: int, width: int = 200) -> str:
    """The line the ref sits on, trimmed — enough context for a human/judge."""
    lo = text.rfind("\n", 0, start)
    lo = 0 if lo < 0 else lo + 1
    hi = text.find("\n", end)
    hi = len(text) if hi < 0 else hi
    return text[lo:hi].strip()[:width]


def extract_artifacts(text: str | None) -> list[dict]:
    """Find checkable refs in ``text``, in reading order, deduped.

    Returns artifact dicts shaped for ``verify.service.verify_artifacts``:
    ``{concern_id, artifact_ref, concern_text, load_bearing}``. A ref the answer
    cites is treated as load-bearing — if it cannot be shown to exist, that should
    surface at the gate rather than pass silently.
    """
    if not text:
        return []

    spans: list[tuple[int, int, str]] = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            spans.append((m.start(), m.end(), m.group(0)))
    # Left-to-right for stable ids; longer span wins a tie so an inner token
    # (a DOI inside a URL) is dropped in favour of the enclosing match.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))

    accepted: list[tuple[int, int]] = []
    seen: set[str] = set()
    out: list[dict] = []
    for start, end, raw in spans:
        if any(start < ae and end > astart for astart, ae in accepted):
            continue  # nested inside an already-accepted (longer) ref
        accepted.append((start, end))
        ref = raw.strip(_TRIM)
        key = ref.lower()
        if not ref or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "concern_id": f"a{len(out) + 1}",
                "artifact_ref": ref,
                "concern_text": _line_context(text, start, end),
                "load_bearing": True,
            }
        )
    return out
