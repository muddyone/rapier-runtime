#!/usr/bin/env bash
# loom-env-check.sh -- Rapier Runtime: a published Python package.
# Health here means: can we test it, can we build it, and is the release path reachable.
set -uo pipefail
if [ "${LOOM_DRYRUN:-0}" = "1" ]; then
  printf '[loom-env-check] DRY RUN\n---SUMMARY---\nSTATUS=dryrun\nWARNINGS=0\nERRORS=0\n---ENDSUMMARY---\n'; exit 0
fi

WARN=0; ERR=0
note() { echo "[loom-env-check] $*"; }

PY_VER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo none)"
[ "$PY_VER" = none ] && { note "[ERR ] python3 not found"; ERR=$((ERR+1)); } || note "[ok  ] python3 $PY_VER"

# Tests are the release gate; a red suite must never reach PyPI.
if python3 -m pytest -q >/tmp/rapier-envcheck-pytest.log 2>&1; then
  note "[ok  ] pytest: $(tail -1 /tmp/rapier-envcheck-pytest.log)"
else
  note "[ERR ] pytest FAILED -- see /tmp/rapier-envcheck-pytest.log"; ERR=$((ERR+1))
fi

for m in build twine; do
  if python3 -c "import $m" 2>/dev/null; then note "[ok  ] $m importable"
  else note "[warn] $m not installed (pip install $m) -- needed to cut a release"; WARN=$((WARN+1)); fi
done

# The PyPI token lives in 1Password and is read at deploy time -- never stored in the repo.
if command -v op >/dev/null 2>&1 && \
   op item get "PyPI — rapier-runtime (bart.niedner)" --vault "Sparring Framework" \
      --fields label=live --reveal >/dev/null 2>&1; then
  note "[ok  ] PyPI token reachable via 1Password (value not read here)"
else
  note "[warn] PyPI token not reachable via op -- /deploy-prod will stop"; WARN=$((WARN+1))
fi

VER="$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)"
TAG="$(git tag --list "v$VER" | head -1)"
if [ -n "$TAG" ]; then note "[note] pyproject version $VER is already tagged ($TAG) -- bump before releasing"
else note "[ok  ] pyproject version $VER has no tag yet"; fi

STATUS=green; [ "$WARN" -gt 0 ] && STATUS=yellow; [ "$ERR" -gt 0 ] && STATUS=red
printf -- '---SUMMARY---\nSTATUS=%s\nWARNINGS=%d\nERRORS=%d\nVERSION=%s\n---ENDSUMMARY---\n' \
  "$STATUS" "$WARN" "$ERR" "$VER"
[ "$ERR" -gt 0 ] && exit 1 || exit 0
