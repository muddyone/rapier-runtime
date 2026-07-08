"""Extract checkable artifacts from recommendation text for the citation gate.

The vendored grounding checker (``verify_grounding.classify``) routes a ref to a
backend by pattern — CWE (MITRE), RFC (IETF datatracker), DOI / arXiv (Crossref),
url (liveness), code (``path:line``). This module's only job is to *find* those
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
    re.compile(r"\bCWE-\d+\b", re.I),                                          # cwe
    re.compile(r"\bRFC[\s-]?\d+\b", re.I),                                     # rfc
    re.compile(r"\b[\w/][\w/.\-]*\.(?:php|py|js|ts|go|java|rb|cpp|sql):\d+\b"),  # code path:line
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
