#!/usr/bin/env bash
# loom-deploy-nonprod.sh -- Rapier Runtime has no staging server. "Nonprod" here means
# everything a release does EXCEPT publishing: run the suite, build the artifacts, and
# validate the package metadata. Safe to run any time; publishes nothing.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
[ "${LOOM_DRYRUN:-0}" = "1" ] && { echo "[deploy-nonprod] DRY RUN"; exit 0; }

echo "[deploy-nonprod] tests"
python3 -m pytest -q || { echo "[deploy-nonprod] FAILED: suite is red -- nothing built."; exit 1; }

if ! python3 -c "import build" 2>/dev/null; then
  echo "[deploy-nonprod] 'build' not installed; skipping artifact build (pip install build twine)."
  exit 0
fi

echo "[deploy-nonprod] build"
rm -rf dist/*.whl dist/*.tar.gz 2>/dev/null
python3 -m build || { echo "[deploy-nonprod] FAILED: build error."; exit 1; }

if python3 -c "import twine" 2>/dev/null; then
  echo "[deploy-nonprod] twine check"
  python3 -m twine check dist/* || { echo "[deploy-nonprod] FAILED: package metadata invalid."; exit 1; }
fi

echo "[deploy-nonprod] OK -- artifacts in dist/, nothing published."
ls -1 dist/ | tail -4
