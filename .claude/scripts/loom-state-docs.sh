#!/usr/bin/env bash
# loom-state-docs.sh -- emits the orientation paths /pickup & /sync-now read.
set -uo pipefail
[ "${LOOM_DRYRUN:-0}" = "1" ] && echo "[loom-state-docs] DRY RUN" >&2
for d in docs/STATUS.md README.md; do [ -f "$d" ] && printf '%s\n' "$d"; done
for d in docs/mcp-server-scope.md docs/threat-model.md; do [ -f "$d" ] && printf '%s\n' "$d"; done
exit 0
