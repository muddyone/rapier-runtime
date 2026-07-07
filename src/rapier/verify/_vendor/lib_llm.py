#!/usr/bin/env python3
"""lib_llm.py — minimal dual-vendor LLM client for the SPARRING tooling.

Claude via /v1/messages, GPT via /v1/chat/completions. Tolerant JSON extraction,
retry with backoff, thread-safe. Keys come from the ENVIRONMENT ONLY
(ANTHROPIC_API_KEY / OPENAI_API_KEY) — this module never reads a .env or a file.

Ported almost verbatim from the resolver-iteration study harness
(pilots/resolver-iteration-2026-06-29/scripts/lib_llm.py). Two production changes:

  1. Model ids are overridable via env (SPAR_CLAUDE_MODEL / SPAR_GPT_MODEL) with
     the study's pins as defaults. call_claude/call_gpt still take an explicit
     `model` arg — callers pass CLAUDE_MODEL / GPT_MODEL.
  2. have_key(name) / keys_present() helpers so callers can degrade fail-soft
     when a key is absent instead of crashing on a KeyError.

Usage:
  import lib_llm as L
  L.claude_json(L.CLAUDE_MODEL, system, user)
  python3 lib_llm.py --self-test        # offline; exercises extract_json
"""
import os, re, json, time, sys, threading, requests

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Model ids: study pins are the defaults; override via env for prod pinning.
CLAUDE_MODEL = os.environ.get("SPAR_CLAUDE_MODEL", "claude-opus-4-8")
GPT_MODEL = os.environ.get("SPAR_GPT_MODEL", "gpt-5.2")

# Serialize nothing globally; requests is thread-safe per-call. We do bound a
# polite call rate per vendor to avoid 429 storms.
_rate_lock = threading.Lock()
_last_call = {"claude": 0.0, "gpt": 0.0}
_MIN_GAP = {"claude": 0.05, "gpt": 0.05}


def have_key(name):
    """True iff the named API key is present and non-empty in the environment."""
    return bool(os.environ.get(name))


def keys_present():
    """Which vendor keys are available (for fail-soft degradation decisions)."""
    return {"anthropic": have_key("ANTHROPIC_API_KEY"),
            "openai": have_key("OPENAI_API_KEY")}


def _throttle(vendor):
    with _rate_lock:
        now = time.monotonic()
        gap = _MIN_GAP[vendor]
        wait = _last_call[vendor] + gap - now
        if wait > 0:
            time.sleep(wait)
        _last_call[vendor] = time.monotonic()


class LLMError(RuntimeError):
    pass


def _retry(fn, what, tries=6):
    delay = 4.0
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - we want to retry broadly
            last = e
            msg = str(e)
            # Don't retry obvious client errors except rate limits / overload.
            if any(s in msg for s in ("status 400", "status 401", "status 403", "status 404")):
                raise
            time.sleep(delay)
            delay = min(delay * 1.8, 60.0)
    raise LLMError(f"{what}: exhausted retries: {last}")


def call_claude(model, system, user, max_tokens=4000, temperature=None):
    # temperature is accepted for signature compatibility but NOT sent:
    # claude-opus-4-8 rejects the parameter ("deprecated for this model").
    def _do():
        _throttle("claude")
        r = requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=300,
        )
        if r.status_code != 200:
            raise LLMError(f"claude status {r.status_code}: {r.text[:300]}")
        d = r.json()
        parts = [b.get("text", "") for b in d.get("content", []) if b.get("type") == "text"]
        return "".join(parts)

    return _retry(_do, f"claude({model})")


def call_gpt(model, system, user, max_completion_tokens=16000):
    def _do():
        _throttle("gpt")
        r = requests.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_completion_tokens": max_completion_tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=300,
        )
        if r.status_code != 200:
            raise LLMError(f"gpt status {r.status_code}: {r.text[:300]}")
        d = r.json()
        return d["choices"][0]["message"]["content"] or ""

    return _retry(_do, f"gpt({model})")


def _salvage_truncated_array(t):
    """Recover complete leading elements of a truncated JSON array.

    An over-long list output (e.g. the gate's specifics enumeration) can be cut
    off mid-element by an output-token cap, leaving unparseable JSON. Rather than
    fail the whole call, parse each complete element with ``raw_decode`` (which is
    string-aware, so braces inside string values don't fool it) and stop at the
    truncated tail. Returns ``{key: elems}`` if the array was wrapped in
    ``{"key": [ ... ]}``, else the bare list, or ``None`` if nothing is salvageable.
    """
    astart = t.find("[")
    if astart == -1:
        return None
    dec = json.JSONDecoder()
    idx, n, elems = astart + 1, len(t), []
    while True:
        while idx < n and t[idx] in " \t\r\n,":
            idx += 1
        if idx >= n or t[idx] == "]":
            break
        try:
            val, idx = dec.raw_decode(t, idx)
        except ValueError:
            break  # the truncated final element — keep what parsed
        elems.append(val)
    if not elems:
        return None
    m = re.search(r'"([^"]+)"\s*:\s*$', t[:astart].rstrip())
    return {m.group(1): elems} if m else elems


def extract_json(text):
    """Tolerant: strip code fences, grab the outermost {...} or [...]."""
    if text is None:
        raise LLMError("empty text for json extraction")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    # Find first balanced object/array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(t)):
            c = t[i]
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start : i + 1])
                    except Exception:
                        break
    # Last resort: salvage a truncated array's complete leading elements.
    salvaged = _salvage_truncated_array(t)
    if salvaged is not None:
        return salvaged
    raise LLMError(f"could not extract JSON from: {text[:200]}")


def claude_json(model, system, user, max_tokens=4000, temperature=0.7):
    sys2 = system + "\n\nRespond with STRICT JSON only — no prose, no markdown fences."
    return extract_json(call_claude(model, sys2, user, max_tokens, temperature))


def gpt_json(model, system, user, max_completion_tokens=16000):
    sys2 = system + "\n\nRespond with STRICT JSON only."
    return extract_json(call_gpt(model, sys2, user, max_completion_tokens))


# --------------------------------------------------------------------------- self-test
def self_test():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    # fenced JSON
    try:
        v = extract_json('```json\n{"a": 1, "b": [2, 3]}\n```')
        check("fenced object -> dict", v == {"a": 1, "b": [2, 3]})
    except Exception as e:
        check(f"fenced object -> dict (raised {e})", False)
    # bare JSON
    try:
        v = extract_json('{"handle": "x", "text": "y"}')
        check("bare object -> dict", v == {"handle": "x", "text": "y"})
    except Exception as e:
        check(f"bare object -> dict (raised {e})", False)
    # bare array
    try:
        v = extract_json("[1, 2, 3]")
        check("bare array -> list", v == [1, 2, 3])
    except Exception as e:
        check(f"bare array -> list (raised {e})", False)
    # embedded JSON with surrounding prose
    try:
        v = extract_json('Here is the answer:\n{"ok": true, "n": 5}\nThanks!')
        check("embedded object extracted", v == {"ok": True, "n": 5})
    except Exception as e:
        check(f"embedded object extracted (raised {e})", False)
    # embedded array in prose (scalar array — extract_json checks '{' before '[', so an
    # array-of-objects would yield the inner object; a scalar array exercises the array path)
    try:
        v = extract_json("Result follows. [1, 2, 3] done.")
        check("embedded array extracted", v == [1, 2, 3])
    except Exception as e:
        check(f"embedded array extracted (raised {e})", False)
    # unparseable -> LLMError (fail-soft: raises the module's own error type)
    raised = False
    try:
        extract_json("no json here at all")
    except LLMError:
        raised = True
    except Exception:
        raised = False
    check("garbage -> LLMError", raised)
    # None -> LLMError
    raised = False
    try:
        extract_json(None)
    except LLMError:
        raised = True
    check("None -> LLMError", raised)
    # helpers work without keys present (offline)
    kp = keys_present()
    check("keys_present returns a dict with both vendors",
          isinstance(kp, dict) and set(kp) == {"anthropic", "openai"})
    check("have_key matches env presence",
          have_key("ANTHROPIC_API_KEY") == bool(os.environ.get("ANTHROPIC_API_KEY")))
    # model ids default or env-overridden
    check("CLAUDE_MODEL set", isinstance(CLAUDE_MODEL, str) and bool(CLAUDE_MODEL))
    check("GPT_MODEL set", isinstance(GPT_MODEL, str) and bool(GPT_MODEL))

    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    print("lib_llm — dual-vendor client. Run with --self-test for offline checks.")
    print(f"  CLAUDE_MODEL={CLAUDE_MODEL}  GPT_MODEL={GPT_MODEL}")
    print(f"  keys_present={keys_present()}")
