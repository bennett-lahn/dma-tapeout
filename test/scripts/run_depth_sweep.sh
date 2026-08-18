#!/usr/bin/env bash
# TC-DEPTH: run the directed subset once per DMA_BUF_DEPTH in 1..DMA_BUF_DEPTH_MAX.
# Depth is compile-time; this is a Make loop with isolated SIM_BUILD/RUN_DIR per N.
# Usage:
#   test/scripts/run_depth_sweep.sh
#   test/scripts/run_depth_sweep.sh SIM=verilator SEED=1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/.." && pwd)"
RUNS_DIR="$TEST_DIR/runs"
mkdir -p "$RUNS_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$RUNS_DIR/run_depth_sweep-${STAMP}.log"

if [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$TEST_DIR/env.sh"
fi

echo "=== run_depth_sweep (TC-DEPTH) ===" | tee "$LOG"
echo "repo=$REPO_ROOT" | tee -a "$LOG"
echo "args: $*" | tee -a "$LOG"
echo "log=$LOG" | tee -a "$LOG"
echo "REPRO: source test/env.sh && test/scripts/run_depth_sweep.sh $*" | tee -a "$LOG"

cd "$TEST_DIR"
set +e
make depth "$@" 2>&1 | tee -a "$LOG"
exit=${PIPESTATUS[0]}
set -e

echo "run_depth_sweep: exit=$exit (full log $LOG)"
exit "$exit"
