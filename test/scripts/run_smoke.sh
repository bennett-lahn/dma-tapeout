#!/usr/bin/env bash
# Run M0 L1 Icarus smoke via Make. Agents should use this instead of ad-hoc one-liners.
# Usage:
#   test/scripts/run_smoke.sh
#   test/scripts/run_smoke.sh WAVES=always

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/.." && pwd)"
RUNS_DIR="$TEST_DIR/runs"
mkdir -p "$RUNS_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$RUNS_DIR/run_smoke-${STAMP}.log"

if [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$TEST_DIR/env.sh"
fi

echo "=== run_smoke ===" | tee "$LOG"
echo "repo=$REPO_ROOT" | tee -a "$LOG"
echo "log=$LOG" | tee -a "$LOG"
echo "REPRO: source test/env.sh && test/scripts/run_smoke.sh $*" | tee -a "$LOG"

cd "$TEST_DIR"
set +e
make smoke "$@" 2>&1 | tee -a "$LOG"
exit=${PIPESTATUS[0]}
set -e

echo "run_smoke: exit=$exit (full log $LOG)"
exit "$exit"
