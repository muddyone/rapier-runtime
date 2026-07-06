"""Tolerant JSON extraction from model output (fences, surrounding prose)."""
from __future__ import annotations

import json
import re
from typing import Any


def parse_json_lenient(text: str | None) -> Any:
    """Best-effort: strip code fences, else grab the first balanced {...}/[...].

    Returns {} on failure rather than raising — a malformed model reply becomes
    an empty round, not a crash (fail-soft).
    """
    if not text:
        return {}
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
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
                        return json.loads(t[start : i + 1])
                    except Exception:
                        break
    return {}
