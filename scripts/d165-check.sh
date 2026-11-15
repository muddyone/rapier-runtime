#!/bin/bash
# D-165 live check: 5 calls, hard cap $1.00. Usage: d165_check.sh <rr-1-passed-sha>
PASSED="$1"; [ -n "$PASSED" ] || { echo "give the SHA rr-1 passed"; exit 9; }
# Run only after D-165 is answered a or c on the queue (verified first-hand) and rr-1 passed <sha>.
cd /home/bartniedner/projects/rapier-runtime/.claude/worktrees/demeter-rapier-runtime-2 || exit 8
OUT=${RAPIER_CHECK_OUT:-$HOME/.rapier/d165-check}; mkdir -p "$OUT"
[ -z "$(git diff "$PASSED" HEAD -- src)" ] || { echo "src changed since $PASSED; not running"; exit 5; }
[ -z "$(git status --porcelain -- src)" ] || { echo "uncommitted changes; not running"; exit 7; }
ps -eo args= | grep -qE '^python3 -m rapier[.]cli map' && { echo "a map run is in progress; not running"; exit 6; }
LOCK=$OUT/d165-check.lock
if ! ( set -o noclobber; echo "$$ $(date -u +%FT%TZ) D-165 check $PASSED" > "$LOCK" ) 2>/dev/null; then echo "REFUSED: the D-165 check was already started: $(cat $LOCK)"; exit 3; fi
export ANTHROPIC_API_KEY="$(op read 'op://Sparring Framework/sparring-framework LLM API keys/ANTHROPIC_API_KEY' </dev/null)"
export OPENAI_API_KEY="$(op read 'op://Sparring Framework/sparring-framework LLM API keys/OPENAI_API_KEY' </dev/null)"
[ -n "$ANTHROPIC_API_KEY" ] && [ -n "$OPENAI_API_KEY" ] || { echo "a key did not resolve; nothing run"; exit 4; }
export RAPIER_MODEL_ANTHROPIC=claude-haiku-5-5; unset RAPIER_MODEL_OPENAI RAPIER_PRICES RAPIER_MAX_USD
echo "start $(date -u +%FT%TZ)"
python3 scripts/d165-check.py 2>&1 | tee "$OUT/d165-check.out"
echo "exit ${PIPESTATUS[0]} at $(date -u +%FT%TZ)"
