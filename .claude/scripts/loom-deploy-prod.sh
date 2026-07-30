#!/usr/bin/env bash
# loom-deploy-prod.sh -- publish rapier-runtime to PyPI.
#
# A PyPI upload is IRREVERSIBLE: a version can be yanked but never replaced or re-used.
# Every gate below exists because of that. The API token is read from 1Password at run
# time into the process environment and is never written to disk, echoed, or logged.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
[ "${LOOM_DRYRUN:-0}" = "1" ] && { echo "[deploy-prod] DRY RUN -- would publish to PyPI"; exit 0; }

die() { echo "[deploy-prod] REFUSED: $*" >&2; exit 1; }

VER="$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)"
[ -n "$VER" ] || die "could not read version from pyproject.toml"

# --- gates ------------------------------------------------------------------
[ -z "$(git status --porcelain)" ] || die "working tree is dirty -- commit or stash first."
BR="$(git rev-parse --abbrev-ref HEAD)"
[ "$BR" = main ] || die "on branch '$BR'; releases are cut from main."
git fetch origin --quiet
[ "$(git rev-list --count HEAD..origin/main)" = 0 ] || die "behind origin/main -- pull first."
[ "$(git rev-list --count origin/main..HEAD)" = 0 ] || die "unpushed commits -- push first."
git rev-parse "v$VER" >/dev/null 2>&1 && die "v$VER is already tagged; bump the version in pyproject.toml."

echo "[deploy-prod] tests"
python3 -m pytest -q || die "suite is red."

for m in build twine; do python3 -c "import $m" 2>/dev/null || die "$m not installed (pip install build twine)."; done

echo "[deploy-prod] build $VER"
rm -rf dist/*.whl dist/*.tar.gz 2>/dev/null
python3 -m build || die "build error."
python3 -m twine check dist/* || die "package metadata invalid."

# --- confirmation -----------------------------------------------------------
echo
echo "  About to publish rapier-runtime $VER to PyPI. This CANNOT be undone."
ls -1 dist/
printf '  Type the version to confirm: '
read -r CONFIRM
[ "$CONFIRM" = "$VER" ] || die "confirmation did not match ('$CONFIRM' != '$VER')."

# --- publish ----------------------------------------------------------------
# Token read straight into the env; never printed, never persisted.
TOKEN="$(op item get 'PyPI — rapier-runtime (bart.niedner)' --vault 'Sparring Framework' \
          --fields label=live --reveal 2>/dev/null)"
[ -n "$TOKEN" ] || die "could not read the PyPI token from 1Password."
echo "[deploy-prod] token loaded (${#TOKEN} chars) -- uploading"

TWINE_USERNAME=__token__ TWINE_PASSWORD="$TOKEN" python3 -m twine upload dist/* \
  || die "upload failed."
unset TOKEN

git tag -a "v$VER" -m "release: v$VER" && git push origin "v$VER"
echo "[deploy-prod] published $VER and tagged v$VER -- https://pypi.org/project/rapier-runtime/$VER/"
echo "[deploy-prod] remaining by hand: update docs/STATUS.md, and the site if the landing copy changed."
