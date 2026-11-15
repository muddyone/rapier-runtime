#!/usr/bin/env python3
"""Re-score a saved map run's class a anchors at $0, under v1.4 and v1.5.

Usage: python3 scripts/rescore-map.py <run dir containing map.json and pack.md>

Reads only the run's own record; makes no model call. Prints the found /
misquoted / fabricated counts under both versions, the misquote kinds
(near-quote, elision), the possible reversals by ellipsis, and every anchor
still fabricated under v1.5.
"""
from __future__ import annotations

import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rapier.stages.map import cite  # noqa: E402


def main(run_dir: str) -> int:
    m = json.load(open(os.path.join(run_dir, "map.json"), encoding="utf-8"))
    src = open(os.path.join(run_dir, "pack.md"), encoding="utf-8").read()
    c14, c15, kinds = collections.Counter(), collections.Counter(), collections.Counter()
    rev_items, rev_quotes, fab = set(), set(), []
    n = 0
    for it in m.get("items") or []:
        a = it.get("anchor") or {}
        loc = a.get("locus", "")
        if a.get("class") != "a" and a.get("downgraded_from") != "a":
            continue
        n += 1
        c14[cite.check_locus_v14(loc, src)["status"]] += 1
        v = cite.check_locus(loc, src)
        c15[v["status"]] += 1
        if v["status"] == "misquoted":
            kinds[v.get("kind") or "near-quote"] += 1
        if v.get("reversal"):
            rev_items.add(it["id"])
            rev_quotes.update(v.get("reversal_quotes") or [])
        if v["status"] == "fabricated":
            fab.append((it["id"], loc[:120], v.get("why", "")))
    if not n:
        print("no class a anchors")
        return 0
    print(f"class a anchors: {n}")
    print(f"v1.4: {dict(c14)}  misquoted {100 * c14['misquoted'] / n:.1f}%")
    print(f"v1.5: {dict(c15)}  misquoted {100 * c15['misquoted'] / n:.1f}%  {dict(kinds)}")
    print(f"possible reversal by ellipsis: {len(rev_items)} items, {len(rev_quotes)} distinct quotes")
    for f in fab:
        print("  still fabricated:", f)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
