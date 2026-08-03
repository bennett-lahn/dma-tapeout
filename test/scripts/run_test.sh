#!/usr/bin/env bash
# Pass-through wrapper for `make test` with durable logging under test/runs/.
# Usage examples:
#   test/scripts/run_test.sh
#   test/scripts/run_test.sh LEVEL=engine SIM=icarus SEED=17 TEST_FILTER=qspi
#   test/scripts/run_test.sh LEVEL=top SIM=verilator SEED=4231

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/.." && pwd)"
RUNS_DIR="$TEST_DIR/runs"
mkdir -p "$RUNS_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$RUNS_DIR/run_test-${STAMP}.log"

if [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$TEST_DIR/env.sh"
fi

echo "=== run_test ===" | tee "$LOG"
echo "repo=$REPO_ROOT" | tee -a "$LOG"
echo "args: $*" | tee -a "$LOG"
echo "log=$LOG" | tee -a "$LOG"
echo "REPRO: source test/env.sh && test/scripts/run_test.sh $*" | tee -a "$LOG"

cd "$TEST_DIR"
set +e
make test "$@" 2>&1 | tee -a "$LOG"
exit=${PIPESTATUS[0]}
set -e

echo "run_test: exit=$exit (full log $LOG)"
exit "$exit"
