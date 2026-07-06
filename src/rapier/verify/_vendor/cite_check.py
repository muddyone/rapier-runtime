#!/usr/bin/env python3
"""
cite-check — cross-substrate citation verifier.

Verifies the citations in a source document are (a) REAL (not hallucinated) and
(b) ACCURATELY APPLIED to the claims they back. Every citation is resolved against
live, free sources (Crossref, DOI resolver, Open Library, arXiv, raw URL fetch)
BEFORE two independent LLM substrates (Claude + OpenAI) each judge it against the
retrieved evidence. A pure-function reconciler then buckets every citation into
AGREE-clean / AGREE-problem / DISAGREE. Model *disagreement* is surfaced to the
human, never negotiated to false consensus.

Staged, cacheable pipeline (artifacts in a run directory):

    cite-check extract  <doc>        -> citations.json + citations.md (review gate)
    cite-check retrieve <run-dir>    -> evidence.json
    cite-check judge    <run-dir>    -> verdicts.json + report.md + report.json
    cite-check run      <doc>        -> all of the above end-to-end

stdlib only. Keys: OPENAI_API_KEY and ANTHROPIC_API_KEY must be in the environment.
Network egress required (sandbox off).
"""

import argparse
import datetime
import difflib
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Config (module constants — easy to retune; prompt_version invalidates caches)
# ---------------------------------------------------------------------------
OPENAI_MODEL = "gpt-5.5-2026-04-23"        # chat-completions-compatible (NOT a -pro tier)
ANTHROPIC_MODEL = "claude-opus-4-8"
OPENAI_REASONING_EFFORT = "medium"          # leaves budget for visible output
MAX_COMPLETION_TOKENS = 16000
EXTRACT_CHUNK_LINES = 150   # docs longer than this are extracted in <=this-many-line chunks (output-token budget)
JUDGE_BATCH = 20            # docs are judged in batches of <=this-many citations (output-token budget)
PROMPT_VERSION = "v1"

CONTACT_EMAIL = "bart.niedner@gmail.com"
USER_AGENT = f"cite-check/0.1 (mailto:{CONTACT_EMAIL})"

HTTP_TIMEOUT = 30
LLM_TIMEOUT = 600
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3

VERDICT_MARKS = {"verified", "nuance", "refuted", "unchecked"}
MARK_GLYPH = {"verified": "✅", "nuance": "⚠️", "refuted": "❌", "unchecked": "☐"}
SEVERITY = {"verified": 0, "nuance": 1, "refuted": 2}


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def get_key(name):
    """Keys come from the environment only (project-agnostic shared tool)."""
    val = os.environ.get(name)
    if not val:
        die(f"{name} is not set. Export it before running, e.g.:\n"
            f"    export {name}=...\n"
            f"  (cite-check reads OPENAI_API_KEY and ANTHROPIC_API_KEY from the environment.)")
    return val


def die(msg, code=1):
    print(f"cite-check: error: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg):
    print(f"cite-check: warning: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# HTTP with bounded retry + per-call isolation
# ---------------------------------------------------------------------------
def _request(url, data=None, headers=None, method="GET", timeout=HTTP_TIMEOUT):
    """Single HTTP attempt. Returns (status, final_url, body_bytes). Raises on HTTPError."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.geturl(), resp.read()


def http(url, data=None, headers=None, method="GET", timeout=HTTP_TIMEOUT, retries=MAX_RETRIES):
    """Bounded-retry wrapper. Returns (status, final_url, body_bytes) or raises the last error."""
    delay = 1.0
    last = None
    for attempt in range(retries):
        try:
            return _request(url, data, headers, method, timeout)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in RETRY_STATUSES and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    if last:
        raise last


def http_json(url, body=None, headers=None, method="GET", timeout=HTTP_TIMEOUT):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
        method = "POST"
    status, final_url, raw = http(url, data=data, headers=h, method=method, timeout=timeout)
    return status, final_url, json.loads(raw.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# LLM substrate callers
# ---------------------------------------------------------------------------
def call_openai(system, user):
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "reasoning_effort": OPENAI_REASONING_EFFORT,
    }
    headers = {"Authorization": f"Bearer {get_key('OPENAI_API_KEY')}"}
    _, _, resp = http_json(
        "https://api.openai.com/v1/chat/completions",
        body=body, headers=headers, timeout=LLM_TIMEOUT,
    )
    choice = resp["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            f"OpenAI output truncated (finish_reason=length, max_completion_tokens={MAX_COMPLETION_TOKENS}); "
            "use a smaller --chunk-lines / --judge-batch, or raise MAX_COMPLETION_TOKENS")
    return choice["message"]["content"]


def call_anthropic(system, user):
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": MAX_COMPLETION_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": get_key("ANTHROPIC_API_KEY"),
        "anthropic-version": "2023-06-01",
    }
    _, _, resp = http_json(
        "https://api.anthropic.com/v1/messages",
        body=body, headers=headers, timeout=LLM_TIMEOUT,
    )
    if resp.get("stop_reason") == "max_tokens":
        raise RuntimeError(
            f"Anthropic output truncated (stop_reason=max_tokens, max_tokens={MAX_COMPLETION_TOKENS}); "
            "use a smaller --chunk-lines / --judge-batch, or raise MAX_COMPLETION_TOKENS")
    parts = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


SUBSTRATES = {
    OPENAI_MODEL: call_openai,
    ANTHROPIC_MODEL: call_anthropic,
}


def extract_json(text):
    """Tolerant JSON extraction: strip ``` fences, then grab the first balanced [..] or {..}."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = t.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(t)):
            if t[i] == opener:
                depth += 1
            elif t[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("could not parse JSON from model output")


# ---------------------------------------------------------------------------
# Run directory
# ---------------------------------------------------------------------------
def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(name))[0].lower()).strip("-")


def make_run_dir(doc_path, explicit=None):
    if explicit:
        path = explicit
    else:
        # Default under ~/.cache so running the tool from any project's cwd never
        # litters that repo. Override with --run-dir for an inspectable local copy.
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.join(os.path.expanduser("~/.cache"), "cite-check")
        path = os.path.join(base, f"{slugify(doc_path)}-{stamp}")
    os.makedirs(path, exist_ok=True)
    return path


def read_artifact(run_dir, name):
    path = os.path.join(run_dir, name)
    if not os.path.exists(path):
        die(f"{name} not found in {run_dir} — run the prior stage first")
    with open(path) as f:
        return json.load(f)


def write_artifact(run_dir, name, obj):
    path = os.path.join(run_dir, name)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    return path


# ===========================================================================
# STAGE 1 — extract
# ===========================================================================
EXTRACT_SYSTEM = """\
You are a citation-extraction parser. You convert a source document into a structured
JSON list of citation records. You do NOT judge whether citations are real or correctly
applied — that happens later. You only parse what the document says, faithfully.

Return a JSON object: {"citations": [ <record>, ... ]}. Each record:
{
  "id": "c01",                       // sequential, stable
  "source_line": <int>,              // the line number (shown as "NNN| ..." prefixes)
  "parent_id": null,                 // id of the parent citation if this is a sub-citation
  "is_citation": true,               // false for prose that is not a citation
  "deliberately_uncited": false,     // true if the doc explicitly says a claim is left uncited
  "raw_text": "...",                 // the citation text as written
  "reference": {
    "type": "journal|book|web|arxiv|hbr_article|unknown",
    "authors": ["Surname, I."],
    "year": <int|null>,
    "title": "...",                  // article/book/page title
    "container_title": null,         // journal/site name
    "publisher": null,
    "volume": null, "issue": null, "pages": null,
    "doi": null,                     // bare DOI, no "doi:" prefix, no URL
    "arxiv_id": null,                // bare id e.g. "2212.09251"
    "urls": []                       // any URLs attached to THIS citation
  },
  "claims": ["the claim(s) this citation backs"],   // the doc's text after an em-dash, or embedded
  "author_note": null,              // any nuance/caveat the doc records about this citation
  "human_mark": null,               // "verified"|"nuance"|"refuted" if the doc shows ✅/⚠️/❌, else null
  "extraction_confidence": 0.0-1.0
}

Rules:
- Nested sub-citations (indented under a parent) get parent_id set and may carry their own claims/urls.
- One source backing several claims -> multiple entries in "claims".
- If the document explicitly says something is intentionally NOT cited (e.g. "left uncited
  deliberately", "not a citation — in-progress work"), emit a record with is_citation=false
  and deliberately_uncited=true, claims describing what it would have supported.
- Capture nuance/caveat prose (popularization notes, "origin of the term is X", self-corrections)
  in author_note. Do NOT turn a caveat into a claim.
- Map ✅->verified, ⚠️->nuance, ❌->refuted into human_mark. Legend lines are not citations.
- Output ONLY the JSON object. No prose, no markdown fences.
"""


def number_lines(text):
    return "".join(f"{i:>4}| {line}" for i, line in enumerate(text.splitlines(keepends=True), 1))


def chunk_numbered_lines(text, max_lines):
    """Split text into <=max_lines-line chunks, snapped back to a blank-line boundary so
    citation groups stay intact, each numbered with GLOBAL (1-based) line numbers."""
    lines = text.splitlines(keepends=True)
    n = len(lines)
    chunks, start = [], 0
    while start < n:
        end = min(start + max_lines, n)
        if end < n:  # snap back to the last blank line in the window, if there is one
            snap = end
            while snap > start + 1 and lines[snap - 1].strip() != "":
                snap -= 1
            if snap > start + 1:
                end = snap
        chunks.append("".join(f"{i:>4}| {lines[i - 1]}" for i in range(start + 1, end + 1)))
        start = end
    return chunks


def _extract_chunk(numbered_text):
    user = "Document to parse (line numbers are the NNN| prefixes):\n\n" + numbered_text
    parsed = extract_json(call_anthropic(EXTRACT_SYSTEM, user))
    return parsed["citations"] if isinstance(parsed, dict) else parsed


def merge_renumber(chunk_results):
    """Merge per-chunk citation lists into one globally-renumbered list (c01..),
    remapping parent_id within each chunk; a sub-citation split from its parent across a
    chunk boundary drops to top-level (warned)."""
    merged, counter, orphans = [], 0, 0
    for part in chunk_results:
        local_map = {}
        for c in part:
            counter += 1
            new_id = f"c{counter:02d}"
            if c.get("id") is not None:
                local_map[c["id"]] = new_id
            c["id"] = new_id
        for c in part:
            pid = c.get("parent_id")
            if pid is not None and pid not in local_map:
                orphans += 1
            if pid is not None:
                c["parent_id"] = local_map.get(pid)
            merged.append(c)
    if orphans:
        warn(f"{orphans} sub-citation(s) lost their parent across a chunk boundary "
             "(promoted to top-level); raise --chunk-lines if a nested group was split")
    return merged


# ---------------------------------------------------------------------------
# BibTeX join. In a pandoc/bibtex document, citations are bare @keys and the
# resolvable metadata (titles, DOIs, arXiv ids) lives in a .bib file the
# extractor never sees -- so retrieve has nothing to look up and every entry
# comes back no-match. `--bib` parses that file and merges each entry's
# metadata into the matching citation's reference, by @key.
# ---------------------------------------------------------------------------
def _bib_strip(s):
    return (s or "").replace("{", "").replace("}", "").strip()


def parse_bibtex(path):
    """Return {citekey: (entry_type, {field: value})} for a .bib file. stdlib-only."""
    text = open(path, encoding="utf-8").read()
    entries = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),(.*?)\n\}", text, re.DOTALL):
        etype, key, body = m.group(1).lower(), m.group(2).strip(), m.group(3)
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*\{(.*?)\}\s*,?\s*(?=\n\s*\w+\s*=|\Z)", body, re.DOTALL):
            fields[fm.group(1).lower()] = " ".join(fm.group(2).split())
        entries[key] = (etype, fields)
    return entries


def bib_entry_to_reference(etype, f):
    """Map a parsed .bib entry to a cite-check reference object."""
    urls = [f["url"]] if f.get("url") else []
    doi_raw = f.get("doi") or ""
    blob = " ".join([f.get("journal", "")] + urls + [doi_raw])
    am = re.search(r"arxiv[:/](?:abs/)?(\d{4}\.\d{4,5})", blob, re.I)
    dm = re.search(r"(10\.\d{4,9}/\S+)", doi_raw)
    # @article -> journal (keeps Crossref fallback even when arxiv_id is set);
    # @book -> book (Open Library); everything else (@misc/@online) -> web (URL fetch).
    ctype = "book" if etype == "book" else "journal" if etype == "article" else "web"
    return {
        "type": ctype,
        "authors": [_bib_strip(a) for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()],
        "year": int(f["year"]) if (f.get("year") or "").isdigit() else f.get("year"),
        "title": _bib_strip(f.get("title", "")),
        "container_title": _bib_strip(f.get("journal", "")) or None,
        "publisher": _bib_strip(f.get("publisher", "")) or None,
        "volume": f.get("volume"), "issue": f.get("number"), "pages": f.get("pages"),
        "doi": dm.group(1) if dm else None,
        "arxiv_id": am.group(1) if am else None,
        "urls": urls,
    }


def enrich_citations_from_bib(citations, bib_entries):
    """Merge .bib metadata into each citation's reference by @key. Returns (joined, missed)."""
    joined, missed = [], []
    for c in citations:
        blob = (c.get("raw_text") or "") + " " + json.dumps(c.get("reference") or "")
        keys = re.findall(r"@([A-Za-z][A-Za-z0-9_:+-]+)", blob)
        primary = next((k for k in keys if k in bib_entries), None)
        if not primary:
            if c.get("is_citation", True):
                missed.append((c.get("id"), keys))
            continue
        ref = c.get("reference") or {}
        for k, v in bib_entry_to_reference(*bib_entries[primary]).items():
            if v not in (None, "", []):
                ref[k] = v
        c["reference"] = ref
        joined.append((c.get("id"), primary))
    return joined, missed


def cmd_extract(args):
    with open(args.doc) as f:
        doc = f.read()
    chunk_lines = getattr(args, "chunk_lines", None) or EXTRACT_CHUNK_LINES
    n_lines = len(doc.splitlines())
    if n_lines > chunk_lines:
        chunks = chunk_numbered_lines(doc, chunk_lines)
        print(f"[extract] {n_lines} lines > {chunk_lines}: extracting in {len(chunks)} "
              "chunks to stay within the output-token budget")
        results = []
        for k, ch in enumerate(chunks, 1):
            part = _extract_chunk(ch)
            print(f"[extract]   chunk {k}/{len(chunks)}: {len(part)} records")
            results.append(part)
        citations = merge_renumber(results)
    else:
        citations = _extract_chunk(number_lines(doc))

    if getattr(args, "bib", None):
        bib_entries = parse_bibtex(args.bib)
        joined, missed = enrich_citations_from_bib(citations, bib_entries)
        print(f"[extract] joined {args.bib} ({len(bib_entries)} entries): "
              f"{len(joined)} citations enriched, {len(missed)} unmatched")
        for cid, keys in missed:
            print(f"          unmatched @key in {cid}: {keys or '(none found)'}")

    run_dir = args.run_dir or make_run_dir(args.doc)
    os.makedirs(run_dir, exist_ok=True)
    bundle = {
        "run_id": os.path.basename(run_dir.rstrip("/")),
        "source_doc": os.path.abspath(args.doc),
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "reviewed": False,
        "citations": citations,
    }
    write_artifact(run_dir, "citations.json", bundle)
    write_review_md(run_dir, bundle)

    real = [c for c in citations if c.get("is_citation", True)]
    uncited = [c for c in citations if c.get("deliberately_uncited")]
    low = [c for c in real if (c.get("extraction_confidence") or 1) < 0.7]
    web = [c for c in real if c.get("reference", {}).get("type") in ("web", "hbr_article")]
    nested = [c for c in citations if c.get("parent_id")]
    print(f"[extract] {len(real)} citations "
          f"({len(web)} web, {len(nested)} nested, {len(uncited)} deliberately-uncited, "
          f"{len(low)} low-confidence)")
    print(f"[extract] review {os.path.join(run_dir, 'citations.md')}, then:")
    print(f"          cite-check retrieve {run_dir}   (add --yes to skip the review gate)")
    return run_dir


def write_review_md(run_dir, bundle):
    lines = [f"# Extracted citations — {bundle['run_id']}",
             "",
             f"Source: `{bundle['source_doc']}`  ·  reviewed: **{bundle['reviewed']}**",
             "",
             "Edit `citations.json` to correct, then set `reviewed: true` (or pass `--yes`).",
             ""]
    for c in bundle["citations"]:
        ref = c.get("reference", {})
        glyph = MARK_GLYPH.get(c.get("human_mark"), "")
        flag = ""
        if not c.get("is_citation", True):
            flag = "  _(not a citation)_"
        elif c.get("deliberately_uncited"):
            flag = "  _(deliberately uncited)_"
        lines.append(f"### {c['id']} {glyph}{flag}  ·  line {c.get('source_line')}")
        if c.get("parent_id"):
            lines.append(f"- parent: `{c['parent_id']}`")
        lines.append(f"- type: `{ref.get('type')}`  ·  confidence: {c.get('extraction_confidence')}")
        lines.append(f"- ref: {c.get('raw_text')}")
        if ref.get("doi"):
            lines.append(f"- doi: `{ref['doi']}`")
        if ref.get("arxiv_id"):
            lines.append(f"- arxiv: `{ref['arxiv_id']}`")
        if ref.get("urls"):
            lines.append(f"- urls: {', '.join(ref['urls'])}")
        for claim in c.get("claims", []):
            lines.append(f"- claim: {claim}")
        if c.get("author_note"):
            lines.append(f"- author note: {c['author_note']}")
        lines.append("")
    with open(os.path.join(run_dir, "citations.md"), "w") as f:
        f.write("\n".join(lines))


# ===========================================================================
# STAGE 2 — retrieve (deterministic, no LLM)
# ===========================================================================
def norm_title(s):
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()


def title_sim(a, b):
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def best_title_sim(cited, candidate):
    """Subtitle-tolerant: compare full and pre-colon (main-title) forms, take the max.
    Indexes often store 'Frame Innovation' for a cited 'Frame Innovation: Create ...'."""
    if not cited or not candidate:
        return 0.0
    cited_main = cited.split(":")[0].strip()
    cand_main = candidate.split(":")[0].strip()
    return max(title_sim(cited, candidate), title_sim(cited_main, cand_main))


def strip_jats(abstract):
    if not abstract:
        return None
    text = re.sub(r"<[^>]+>", " ", abstract)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def crossref_metadata(msg):
    title = (msg.get("title") or [None])[0]
    authors = [f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
               for a in msg.get("author", [])]
    year = None
    for key in ("published", "published-print", "published-online", "issued"):
        parts = (msg.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    return {
        "title": title,
        "authors": authors,
        "container_title": (msg.get("container-title") or [None])[0],
        "year": year,
        "volume": msg.get("volume"), "issue": msg.get("issue"), "pages": msg.get("page"),
        "doi": msg.get("DOI"),
        "abstract": strip_jats(msg.get("abstract")),
    }


def lookup_crossref_doi(doi):
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}?mailto={CONTACT_EMAIL}"
    status, _, data = http_json(url)
    return status, crossref_metadata(data["message"])


def lookup_crossref_search(ref_string):
    q = urllib.parse.urlencode({"query.bibliographic": ref_string, "rows": 5, "mailto": CONTACT_EMAIL})
    status, _, data = http_json(f"https://api.crossref.org/works?{q}")
    items = data.get("message", {}).get("items", [])
    return status, [crossref_metadata(m) for m in items]


def lookup_openlibrary(title, author):
    # Query on the main title (before any colon subtitle) for better recall.
    main_title = (title or "").split(":")[0].strip()
    params = {"title": main_title, "limit": 5}
    if author:
        # Open Library's author param wants a name, not "Surname, I." — use the surname.
        surname = re.split(r"[, ]", author.strip())[0]
        if surname:
            params["author"] = surname
    status, _, data = http_json(f"https://openlibrary.org/search.json?{urllib.parse.urlencode(params)}")
    docs = data.get("docs", [])[:5]
    return status, [{
        "title": d.get("title"),
        "authors": d.get("author_name", []),
        "year": d.get("first_publish_year"),
        "publisher": (d.get("publisher") or [None])[0],
        "key": d.get("key"),
    } for d in docs]


def lookup_arxiv(arxiv_id):
    url = f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}"
    status, _, raw = http(url)
    body = raw.decode("utf-8", errors="replace")
    entry = re.search(r"<entry>(.*?)</entry>", body, re.S)
    if not entry:
        return status, None
    block = entry.group(1)

    def tag(name):
        m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
        return re.sub(r"\s+", " ", html.unescape(m.group(1)).strip()) if m else None

    authors = re.findall(r"<name>(.*?)</name>", block, re.S)
    return status, {
        "title": tag("title"),
        "authors": [a.strip() for a in authors],
        "abstract": tag("summary"),
    }


def fetch_url(url):
    status, final_url, raw = http(url)
    body = raw.decode("utf-8", errors="replace")
    title_m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    title = re.sub(r"\s+", " ", html.unescape(title_m.group(1)).strip()) if title_m else None
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    snippet = re.sub(r"\s+", " ", html.unescape(text)).strip()[:2000]
    return status, final_url, title, snippet


def retrieve_one(c):
    """Run live lookups for one citation; isolate failures into the record."""
    ref = c.get("reference", {})
    ev = {
        "citation_id": c["id"],
        "existence_status": "not-checkable",
        "lookups": [],
        "application_evidence_available": False,
        "application_evidence_basis": "none",
        "application_evidence_text": None,
    }
    if not c.get("is_citation", True):
        ev["existence_status"] = "not-applicable"
        return ev

    cited_title = ref.get("title")
    cited_year = ref.get("year")
    cited_author = (ref.get("authors") or [None])[0]

    def record(source, endpoint, fn):
        try:
            return fn(), None
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 — isolation is the point
            return None, str(e)

    # 1. DOI direct
    if ref.get("doi"):
        res, err = record("crossref", "doi", lambda: lookup_crossref_doi(ref["doi"]))
        if res:
            _, meta = res
            ev["lookups"].append({
                "source": "crossref", "endpoint": f"works/{ref['doi']}", "http_status": 200,
                "matched": True, "match_confidence": 1.0, "match_basis": "doi_direct",
                "retrieved_metadata": meta, "error": None,
            })
            ev["existence_status"] = "exists"
            if meta.get("abstract"):
                ev.update(application_evidence_available=True,
                          application_evidence_basis="retrieved_abstract",
                          application_evidence_text=meta["abstract"])
        else:
            ev["lookups"].append({"source": "crossref", "endpoint": f"works/{ref['doi']}",
                                  "matched": False, "match_basis": "none", "error": err})

    # 2. arXiv
    if ev["existence_status"] != "exists" and ref.get("arxiv_id"):
        res, err = record("arxiv", "api", lambda: lookup_arxiv(ref["arxiv_id"]))
        if res and res[1]:
            _, meta = res
            ev["lookups"].append({"source": "arxiv", "endpoint": f"abs/{ref['arxiv_id']}",
                                  "matched": True, "match_confidence": 1.0,
                                  "match_basis": "arxiv_id", "retrieved_metadata": meta, "error": None})
            ev["existence_status"] = "exists"
            if meta.get("abstract"):
                ev.update(application_evidence_available=True,
                          application_evidence_basis="retrieved_abstract",
                          application_evidence_text=meta["abstract"])
        else:
            ev["lookups"].append({"source": "arxiv", "endpoint": f"abs/{ref.get('arxiv_id')}",
                                  "matched": False, "match_basis": "none", "error": err or "no entry"})

    # 3. Crossref bibliographic search (journals/articles without a DOI hit)
    if ev["existence_status"] not in ("exists",) and ref.get("type") in ("journal", "hbr_article", "unknown"):
        res, err = record("crossref", "search", lambda: lookup_crossref_search(c.get("raw_text") or cited_title or ""))
        if res:
            _, cands = res
            best, best_sim = None, 0.0
            for m in cands:
                s = best_title_sim(cited_title, m.get("title"))
                if s > best_sim:
                    best, best_sim = m, s
            status, basis = classify_match(best, best_sim, cited_year, cited_author)
            # HBR (and similar) articles are not reliably indexed in Crossref; a no-match
            # there is an index gap, not evidence of non-existence -> not-checkable.
            if status == "no-match" and ref.get("type") == "hbr_article":
                status, basis = "not-checkable", "not_indexed_in_crossref"
            ev["lookups"].append({"source": "crossref", "endpoint": "works?query.bibliographic",
                                  "matched": status == "exists", "match_confidence": round(best_sim, 3),
                                  "match_basis": basis, "retrieved_metadata": best, "error": None})
            if ev["existence_status"] == "not-checkable" or status == "exists":
                ev["existence_status"] = status
            if best and best.get("abstract") and not ev["application_evidence_available"]:
                ev.update(application_evidence_available=True,
                          application_evidence_basis="retrieved_abstract",
                          application_evidence_text=best["abstract"])
        else:
            ev["lookups"].append({"source": "crossref", "endpoint": "works?query.bibliographic",
                                  "matched": False, "match_basis": "none", "error": err})

    # 4. Open Library (books)
    if ev["existence_status"] not in ("exists",) and ref.get("type") == "book":
        res, err = record("openlibrary", "search", lambda: lookup_openlibrary(cited_title, cited_author))
        if res:
            _, cands = res
            best, best_sim = None, 0.0
            for m in cands:
                s = best_title_sim(cited_title, m.get("title"))
                if s > best_sim:
                    best, best_sim = m, s
            status, basis = classify_match(best, best_sim, cited_year, cited_author)
            ev["lookups"].append({"source": "openlibrary", "endpoint": "search.json",
                                  "matched": status == "exists", "match_confidence": round(best_sim, 3),
                                  "match_basis": basis, "retrieved_metadata": best, "error": None})
            if ev["existence_status"] == "not-checkable" or status == "exists":
                ev["existence_status"] = status
        else:
            ev["lookups"].append({"source": "openlibrary", "endpoint": "search.json",
                                  "matched": False, "match_basis": "none", "error": err})

    # 5. URL fetches (web / supporting links)
    for url in ref.get("urls", []):
        res, err = record("url_fetch", url, lambda u=url: fetch_url(u))
        if res:
            status, final_url, title, snippet = res
            ev["lookups"].append({"source": "url_fetch", "endpoint": url, "http_status": status,
                                  "matched": status == 200, "match_basis": "url_resolves",
                                  "retrieved_metadata": {"final_url": final_url, "title": title},
                                  "error": None})
            if status == 200 and ev["existence_status"] in ("not-checkable", "no-match"):
                ev["existence_status"] = "exists"
            if snippet and not ev["application_evidence_available"]:
                ev.update(application_evidence_available=True,
                          application_evidence_basis="retrieved_snippet",
                          application_evidence_text=snippet)
        else:
            ev["lookups"].append({"source": "url_fetch", "endpoint": url,
                                  "matched": False, "match_basis": "none", "error": err})

    return ev


def classify_match(best, sim, cited_year, cited_author):
    """Map a best candidate + similarity to an existence status + basis."""
    if not best:
        return "no-match", "none"
    year_ok = (not cited_year) or (best.get("year") and abs(int(best["year"]) - int(cited_year)) <= 1)
    author_ok = True
    if cited_author:
        surname = re.split(r"[, ]", cited_author.strip())[0].lower()
        author_ok = any(surname in (a or "").lower() for a in (best.get("authors") or []))
    if sim >= 0.9 and year_ok and author_ok:
        return "exists", "title_year_author_fuzzy"
    if sim >= 0.8:
        return "partial-metadata-match", "title_fuzzy_meta_drift"
    return "no-match", "low_similarity"


def cmd_retrieve(args):
    bundle = read_artifact(args.run_dir, "citations.json")
    if not bundle.get("reviewed") and not args.yes:
        die("citations.json has reviewed=false. Review citations.md and set reviewed=true, "
            "or pass --yes to override.")
    evidence = []
    for c in bundle["citations"]:
        ev = retrieve_one(c)
        ev["fetched_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        evidence.append(ev)
        glyph = {"exists": "✓", "partial-metadata-match": "~", "no-match": "✗",
                 "not-checkable": "?", "not-applicable": "-"}.get(ev["existence_status"], "?")
        basis = ev["application_evidence_basis"]
        print(f"[retrieve] {c['id']} {glyph} {ev['existence_status']:<22} app-evidence: {basis}")
    out = {"run_id": bundle["run_id"], "evidence": evidence,
           "retrieved_at": datetime.datetime.now().isoformat(timespec="seconds")}
    write_artifact(args.run_dir, "evidence.json", out)
    print(f"[retrieve] wrote {os.path.join(args.run_dir, 'evidence.json')}")
    return args.run_dir


# ===========================================================================
# STAGE 3 — judge (both substrates) + reconcile + report
# ===========================================================================
JUDGE_SYSTEM = f"""\
You are a citation verifier. For each citation you are given the bibliographic reference,
the claim(s) it backs in the source document, any author note, and the EVIDENCE retrieved
from live lookups (Crossref/DOI/Open Library/arXiv/URL fetches). Judge two SEPARATE axes:

EXISTENCE — does this cited work really exist, as described?
APPLICATION — does the source actually support the claim it is cited for?

Each axis gets exactly one mark:
  verified — confirmed by the evidence.
  nuance   — real/supported but with a caveat (e.g. a popularized figure that overstates
             the source's own framing; a partial metadata match; correct but imprecise).
  refuted  — the evidence contradicts it (no such work; misapplied claim).
  unchecked — the evidence does not let you judge this axis at all.

CRITICAL HONESTY RULES:
- Judge EXISTENCE only from the retrieved evidence. If existence_status is "not-checkable"
  (paywall/network/no free index), existence is "unchecked" unless you have decisive evidence.
- For APPLICATION: if application_evidence_basis is "none" (no source text was retrieved),
  you MAY assess from your own knowledge, but you MUST set application_basis="model_knowledge_only"
  and application_confidence <= 0.5. Only set application_basis="evidence_text" when you actually
  used retrieved abstract/snippet/full-text.
- A citation marked deliberately_uncited or is_citation=false: set both axes to "unchecked"
  and note it is intentionally uncited.
- Do NOT invent evidence. evidence_used must list only evidence actually present in the input.

Return ONLY a JSON object: {{"verdicts": [ <record>, ... ]}}. Each record:
{{
  "citation_id": "c01",
  "existence_verdict": "verified|nuance|refuted|unchecked",
  "existence_confidence": 0.0-1.0,
  "application_verdict": "verified|nuance|refuted|unchecked",
  "application_confidence": 0.0-1.0,
  "application_basis": "evidence_text|model_knowledge_only|no_basis",
  "evidence_used": ["short ids of evidence you used"],
  "rationale": "<= 2 sentences"
}}
prompt_version={PROMPT_VERSION}
"""


def judge_payload(bundle, evidence_by_id):
    items = []
    for c in bundle["citations"]:
        ev = evidence_by_id.get(c["id"], {})
        items.append({
            "citation_id": c["id"],
            "reference": c.get("reference"),
            "raw_text": c.get("raw_text"),
            "claims": c.get("claims"),
            "author_note": c.get("author_note"),
            "is_citation": c.get("is_citation", True),
            "deliberately_uncited": c.get("deliberately_uncited", False),
            "existence_status": ev.get("existence_status"),
            "application_evidence_basis": ev.get("application_evidence_basis"),
            "application_evidence_text": ev.get("application_evidence_text"),
            "retrieved": [
                {"source": lk.get("source"), "matched": lk.get("matched"),
                 "match_basis": lk.get("match_basis"),
                 "metadata": lk.get("retrieved_metadata"), "error": lk.get("error")}
                for lk in ev.get("lookups", [])
            ],
        })
    return json.dumps({"citations": items}, ensure_ascii=False, indent=2)


def run_substrate(model, fn, payload):
    """One judge call for a substrate; one retry on parse failure with a stricter nudge."""
    for attempt in range(2):
        try:
            suffix = "" if attempt == 0 else "\n\nReturn ONLY valid JSON, no prose, no fences."
            raw = fn(JUDGE_SYSTEM, payload + suffix)
            parsed = extract_json(raw)
            verdicts = parsed["verdicts"] if isinstance(parsed, dict) else parsed
            return {v["citation_id"]: v for v in verdicts}
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                warn(f"{model} judge failed: {e}")
                return {}
    return {}


def reconcile_axis(a, b):
    """Return (mark, split) for one axis given two substrate marks."""
    if a == b:
        return a, False
    pair = {a, b}
    if "unchecked" in pair:
        # one judged, one couldn't -> informative split
        return ("unchecked", True) if pair == {"unchecked"} else (sorted(pair - {"unchecked"})[0], True)
    if pair == {"verified", "nuance"}:
        return "nuance", False           # the ⚠️ zone — flag but not a hard split
    # verified vs refuted, or nuance vs refuted -> hard disagreement
    worst = max(pair, key=lambda m: SEVERITY.get(m, 0))
    return worst, True


def reconcile(citation, va, vb, model_a, model_b):
    ex_mark, ex_split = reconcile_axis(va.get("existence_verdict", "unchecked"),
                                       vb.get("existence_verdict", "unchecked"))
    ap_mark, ap_split = reconcile_axis(va.get("application_verdict", "unchecked"),
                                       vb.get("application_verdict", "unchecked"))
    degraded = (va.get("application_basis") == "model_knowledge_only"
                or vb.get("application_basis") == "model_knowledge_only")

    escalation = None
    if ex_split or ap_split:
        bucket = "DISAGREE"
        bits = []
        if ex_split:
            bits.append(f"existence: {va.get('existence_verdict')} vs {vb.get('existence_verdict')}")
        if ap_split:
            bits.append(f"application: {va.get('application_verdict')} vs {vb.get('application_verdict')}")
        escalation = "; ".join(bits)
    elif ex_mark == "verified" and ap_mark == "verified" and not degraded:
        bucket = "AGREE-clean"
    else:
        bucket = "AGREE-problem"
        if ex_mark == "verified" and ap_mark == "verified" and degraded:
            escalation = "application unverified — judged from model knowledge only"

    return {
        "citation_id": citation["id"],
        "source_line": citation.get("source_line"),
        "raw_text": citation.get("raw_text"),
        "bucket": bucket,
        "existence_reconciled": "SPLIT" if ex_split else ex_mark,
        "application_reconciled": "SPLIT" if ap_split else ap_mark,
        "degraded": degraded,
        "human_mark": citation.get("human_mark"),
        "matches_human": (citation.get("human_mark") == ex_mark) if citation.get("human_mark") else None,
        "escalation_reason": escalation,
        "models": {
            model_a: {"existence": va.get("existence_verdict"),
                      "application": va.get("application_verdict"),
                      "application_basis": va.get("application_basis"),
                      "application_confidence": va.get("application_confidence"),
                      "rationale": va.get("rationale")},
            model_b: {"existence": vb.get("existence_verdict"),
                      "application": vb.get("application_verdict"),
                      "application_basis": vb.get("application_basis"),
                      "application_confidence": vb.get("application_confidence"),
                      "rationale": vb.get("rationale")},
        },
    }


def cmd_judge(args):
    bundle = read_artifact(args.run_dir, "citations.json")
    ev_bundle = read_artifact(args.run_dir, "evidence.json")
    evidence_by_id = {e["citation_id"]: e for e in ev_bundle["evidence"]}

    cites = bundle["citations"]
    batch_size = getattr(args, "judge_batch", None) or JUDGE_BATCH
    batches = [cites[i:i + batch_size] for i in range(0, len(cites), batch_size)] or [[]]
    va, vb = {}, {}
    for k, batch in enumerate(batches, 1):
        payload = judge_payload({"citations": batch}, evidence_by_id)
        label = f" batch {k}/{len(batches)} ({len(batch)} cites)" if len(batches) > 1 else ""
        print(f"[judge]{label} querying {ANTHROPIC_MODEL} ...")
        va.update(run_substrate(ANTHROPIC_MODEL, call_anthropic, payload))
        print(f"[judge]{label} querying {OPENAI_MODEL} ...")
        vb.update(run_substrate(OPENAI_MODEL, call_openai, payload))

    records = []
    for c in bundle["citations"]:
        a = va.get(c["id"], {"existence_verdict": "unchecked", "application_verdict": "unchecked"})
        b = vb.get(c["id"], {"existence_verdict": "unchecked", "application_verdict": "unchecked"})
        records.append(reconcile(c, a, b, ANTHROPIC_MODEL, OPENAI_MODEL))

    summary = summarize(bundle, evidence_by_id, records)
    report = {"run_id": bundle["run_id"], "prompt_version": PROMPT_VERSION,
              "summary": summary, "citations": records,
              "raw_verdicts": {ANTHROPIC_MODEL: va, OPENAI_MODEL: vb}}
    write_artifact(args.run_dir, "verdicts.json", {ANTHROPIC_MODEL: va, OPENAI_MODEL: vb})
    write_artifact(args.run_dir, "report.json", report)
    md = render_report_md(bundle, report)
    with open(os.path.join(args.run_dir, "report.md"), "w") as f:
        f.write(md)
    print()
    print(md)
    print(f"\n[judge] wrote {os.path.join(args.run_dir, 'report.md')}")
    return args.run_dir


def summarize(bundle, evidence_by_id, records):
    real = [c for c in bundle["citations"] if c.get("is_citation", True)]
    existence_confirmed = sum(1 for e in evidence_by_id.values() if e.get("existence_status") == "exists")
    not_checkable = sum(1 for e in evidence_by_id.values() if e.get("existence_status") == "not-checkable")
    app_grounded = sum(1 for e in evidence_by_id.values() if e.get("application_evidence_available"))
    app_memory = sum(1 for r in records if r["degraded"])
    buckets = {"AGREE-clean": 0, "AGREE-problem": 0, "DISAGREE": 0}
    for r in records:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    scored = [r for r in records if r["matches_human"] is not None]
    return {
        "total_citations": len(real),
        "existence_independently_confirmed": existence_confirmed,
        "application_evidence_grounded": app_grounded,
        "application_model_knowledge_only": app_memory,
        "not_checkable": not_checkable,
        "buckets": buckets,
        "self_test": {"scored": len(scored),
                      "matched_human_existence": sum(1 for r in scored if r["matches_human"])},
    }


def render_report_md(bundle, report):
    s = report["summary"]
    L = [f"# cite-check report — {report['run_id']}",
         "",
         f"**Source:** `{bundle['source_doc']}`  ·  prompt `{report['prompt_version']}`  ·  "
         f"substrates `{ANTHROPIC_MODEL}` + `{OPENAI_MODEL}`",
         "",
         "## Coverage (read this first)",
         f"- **{s['total_citations']}** citations checked",
         f"- existence independently confirmed: **{s['existence_independently_confirmed']}**",
         f"- application evidence-grounded: **{s['application_evidence_grounded']}**",
         f"- application judged from model knowledge only: **{s['application_model_knowledge_only']}**",
         f"- not independently checkable: **{s['not_checkable']}**",
         f"- buckets — clean **{s['buckets']['AGREE-clean']}** · "
         f"problem **{s['buckets']['AGREE-problem']}** · disagree **{s['buckets']['DISAGREE']}**"]
    if s["self_test"]["scored"]:
        L.append(f"- self-test vs human marks: existence agreed on "
                 f"**{s['self_test']['matched_human_existence']}/{s['self_test']['scored']}**")
    L.append("")

    def section(title, bucket):
        rows = [r for r in report["citations"] if r["bucket"] == bucket]
        if not rows:
            return
        L.append(f"## {title} ({len(rows)})")
        for r in rows:
            ex = MARK_GLYPH.get(r["existence_reconciled"], r["existence_reconciled"])
            ap = MARK_GLYPH.get(r["application_reconciled"], r["application_reconciled"])
            hm = f"  · human {MARK_GLYPH.get(r['human_mark'], '—')}" if r["human_mark"] else ""
            deg = "  · ⚑ degraded" if r["degraded"] else ""
            L.append(f"### {r['citation_id']} — exists {ex} · applied {ap}{deg}{hm}  (line {r['source_line']})")
            L.append(f"_{r['raw_text']}_")
            if r["escalation_reason"]:
                L.append(f"- **escalate:** {r['escalation_reason']}")
            for model, v in r["models"].items():
                tag = model.split("-")[0]
                basis = f" [{v['application_basis']}]" if v.get("application_basis") else ""
                L.append(f"- {tag}: exists={v['existence']} applied={v['application']}{basis} — {v.get('rationale','')}")
            L.append("")

    section("⛔ DISAGREE — escalate to human", "DISAGREE")
    section("⚠️ AGREE-problem", "AGREE-problem")
    section("✅ AGREE-clean", "AGREE-clean")
    return "\n".join(L)


def cmd_run(args):
    run_dir = cmd_extract(args)
    # run mode bypasses the manual review gate by design (use staged subcommands to inspect)
    args.run_dir = run_dir
    args.yes = True
    cmd_retrieve(args)
    cmd_judge(args)
    return run_dir


# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="cite-check", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="parse a document into citations.json (+ review render)")
    pe.add_argument("doc")
    pe.add_argument("--run-dir")
    pe.add_argument("--bib", help="BibTeX file to join (@key -> metadata) for pandoc/bibtex docs")
    pe.add_argument("--chunk-lines", type=int,
                    help=f"extract docs longer than this many lines in chunks (default {EXTRACT_CHUNK_LINES})")
    pe.set_defaults(func=cmd_extract)

    pr = sub.add_parser("retrieve", help="live-lookup evidence for each citation")
    pr.add_argument("run_dir")
    pr.add_argument("--yes", action="store_true", help="skip the reviewed=true gate")
    pr.set_defaults(func=cmd_retrieve)

    pj = sub.add_parser("judge", help="both substrates verdict + reconcile + report")
    pj.add_argument("run_dir")
    pj.add_argument("--judge-batch", type=int,
                    help=f"judge in batches of this many citations (default {JUDGE_BATCH})")
    pj.set_defaults(func=cmd_judge)

    pn = sub.add_parser("run", help="extract + retrieve + judge end-to-end")
    pn.add_argument("doc")
    pn.add_argument("--run-dir")
    pn.add_argument("--bib", help="BibTeX file to join (@key -> metadata) for pandoc/bibtex docs")
    pn.add_argument("--chunk-lines", type=int,
                    help=f"extract docs longer than this many lines in chunks (default {EXTRACT_CHUNK_LINES})")
    pn.add_argument("--judge-batch", type=int,
                    help=f"judge in batches of this many citations (default {JUDGE_BATCH})")
    pn.set_defaults(func=cmd_run)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
