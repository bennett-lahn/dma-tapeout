#!/usr/bin/env bash
# Executable timing CI (not STA). Runs under TIMING_PROFILE=nominal.
# Suites: test_qspi_timing, test_qspi_timing_delay, test_qspi_timing_launch_rx,
# and ownership delay (tests.test_qspi_ownership).
#
# Does not run TIMING_PROFILE=sweep or STA compose.
# If a LibreLane summary.rpt is present, print WNS but do not fail on
# paper T-ACLK (tACLK read-return path) or unsigned T-CLKQ / T-GPIO-LIB / T-66.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/.." && pwd)"

export TIMING_PROFILE="${TIMING_PROFILE:-nominal}"
WAVES="${WAVES:-never}"
SIM="${SIM:-icarus}"
SEED="${SEED:-1}"

if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$TEST_DIR/env.sh" ] && [ -d "${REPO_ROOT}/dma-venv" ]; then
  # Local agent path. CI installs cocotb via pip and skips dma-venv.
  # shellcheck disable=SC1091
  source "$TEST_DIR/env.sh"
fi

cd "$TEST_DIR"
mkdir -p "$TEST_DIR/runs"

run_one() {
  local modules="$1"
  local level="$2"
  local filter="${3:-}"
  echo "=== timing: LEVEL=$level TIMING_PROFILE=$TIMING_PROFILE MODULES=$modules FILTER=${filter:-<all>} ==="
  if [ -n "$filter" ]; then
    make test LEVEL="$level" SIM="$SIM" SEED="$SEED" \
      TIMING_PROFILE="$TIMING_PROFILE" WAVES="$WAVES" \
      COCOTB_TEST_MODULES="$modules" TEST_FILTER="$filter"
  else
    make test LEVEL="$level" SIM="$SIM" SEED="$SEED" \
      TIMING_PROFILE="$TIMING_PROFILE" WAVES="$WAVES" \
      COCOTB_TEST_MODULES="$modules"
  fi
}

fail=0
run_one tests.test_qspi_timing top || fail=1
run_one tests.test_qspi_timing_delay top || fail=1
run_one tests.test_qspi_timing_launch_rx engine || fail=1
run_one tests.test_qspi_ownership top ownership_shared_bus_negatives || fail=1

# Prefer WS2 contract path; otherwise stitch the last RUN_DIR results into test/results.xml.
if [ ! -f "$TEST_DIR/results.xml" ]; then
  latest="$(find "$TEST_DIR/runs" -name results.xml -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2- || true)"
  if [ -n "${latest:-}" ] && [ -f "$latest" ]; then
    cp -f "$latest" "$TEST_DIR/results.xml"
  fi
fi

if [ -f "$TEST_DIR/results.xml" ]; then
  if grep -q failure "$TEST_DIR/results.xml"; then
    echo "run_timing: failure entries in test/results.xml" >&2
    fail=1
  fi
else
  echo "run_timing: no test/results.xml produced" >&2
  fail=1
fi

# Optional paper STA comment. Never a CI fail for T-ACLK / T-CLKQ / T-GPIO-LIB / T-66.
python3 - "$REPO_ROOT" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
candidates = list(root.glob("**/summary.rpt"))
unsigned = ("T-ACLK", "T-CLKQ", "T-GPIO-LIB", "T-66")
if not candidates:
    print("run_timing: no summary.rpt (skip WNS parse; T-ACLK/T-CLKQ/T-GPIO-LIB/T-66 stay unsigned)")
    sys.exit(0)

rpt = candidates[0]
text = rpt.read_text(errors="replace")
print(f"run_timing: parsing WNS from {rpt}")
wns = None
for pat in (
    r"wns\s*[:=]\s*(-?[0-9]+(?:\.[0-9]+)?)",
    r"worst(?:\s+negative)?\s+slack\s*[:=]\s*(-?[0-9]+(?:\.[0-9]+)?)",
):
    m = re.search(pat, text, flags=re.I)
    if m:
        wns = m.group(1)
        break
if wns is None:
    print("run_timing: summary.rpt present but no WNS field found")
else:
    print(f"run_timing: WNS={wns} (informational; not a CI gate)")
print(
    "run_timing: not failing on paper "
    + "/".join(unsigned)
    + " (analog, liberty load, or board/silicon)"
)
PY

if [ "$fail" -ne 0 ]; then
  echo "run_timing: FAILED"
  exit 1
fi
echo "run_timing: PASSED"
