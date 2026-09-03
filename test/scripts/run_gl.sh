#!/usr/bin/env bash
# Copy the designated N=5 unpowered netlist and run the L2 directed subset.
# Designated artifact: test/gate_level_netlist.189-aug18.v (189-DFF tapeout N=5).
# Do not silently fall back to a ttihp template or other untracked netlist.
# Requires PDK_ROOT (or a discoverable IHP cell-model tree).
# Missing netlist is a hard fail, not a pass.
# SDF remains blocked: this is zero-delay functional GL, not an SDF pass.
# Usage:
#   source test/env.sh
#   test/scripts/run_gl.sh
#   test/scripts/run_gl.sh TEST_FILTER=gate_same_device_smoke

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/.." && pwd)"
RUNS_DIR="$TEST_DIR/runs"
mkdir -p "$RUNS_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$RUNS_DIR/run_gl-${STAMP}.log"

EXPECTED_NETLIST_SHA256_N5="9a769ad4bcc09d7cff699e8f178acab4fb5b7228e242cfdf7d027ed2274beb7a"
DESIGNATED_NL="$TEST_DIR/gate_level_netlist.189-aug18.v"
DEST_NL="$TEST_DIR/gate_level_netlist.v"

if [ -n "${SDF:-}" ]; then
    echo "run_gl: SDF is blocked (zero-delay functional GL is not an SDF pass)." >&2
    echo "run_gl: unset SDF and rerun. This is not a pass." >&2
    exit 1
fi

if [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$TEST_DIR/env.sh"
fi

if [ -n "${NETLIST_SRC:-}" ] && [ -f "$NETLIST_SRC" ]; then
    SRC_NL="$NETLIST_SRC"
elif [ -f "$DESIGNATED_NL" ]; then
    SRC_NL="$DESIGNATED_NL"
else
    echo "run_gl: designated N=5 netlist missing." >&2
    echo "run_gl: expected 189-DFF artifact at:" >&2
    echo "  $DESIGNATED_NL" >&2
    echo "run_gl: L2 is blocked until that file exists. This is not a pass." >&2
    exit 1
fi

cp -f "$SRC_NL" "$DEST_NL"

SHA="$(sha256sum "$DEST_NL" | awk '{print $1}')"
if [ "$SHA" != "$EXPECTED_NETLIST_SHA256_N5" ]; then
    echo "run_gl: NETLIST SHA256 mismatch." >&2
    echo "run_gl: expected $EXPECTED_NETLIST_SHA256_N5" >&2
    echo "run_gl: got      $SHA" >&2
    echo "run_gl: src=$SRC_NL dest=$DEST_NL" >&2
    echo "run_gl: refuse to run an untracked netlist. This is not a pass." >&2
    exit 1
fi
export NETLIST_SHA256="$SHA"

_resolve_pdk_root() {
    local candidate
    if [ -n "${PDK_ROOT:-}" ] && [ -f "$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v" ]; then
        return 0
    fi
    for candidate in \
        "$HOME/ttsetup/pdk/ciel/ihp-sg13g2/versions/"*/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v \
        "$REPO_ROOT/IHP-Open-PDK/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v"
    do
        if [ -f "$candidate" ]; then
            if [[ "$candidate" == *"/IHP-Open-PDK/"* ]]; then
                export PDK_ROOT="$REPO_ROOT/IHP-Open-PDK"
            else
                # .../versions/<rev>/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/file
                export PDK_ROOT="$(cd "$(dirname "$candidate")/../../../.." && pwd)"
            fi
            echo "run_gl: PDK_ROOT was unset or invalid; using $PDK_ROOT"
            return 0
        fi
    done
    echo "run_gl: PDK_ROOT must be set to a tree that contains" >&2
    echo "  \$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v" >&2
    echo "  \$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v" >&2
    echo "run_gl: L2 is blocked without IHP cell models. This is not a pass." >&2
    return 1
}

_resolve_pdk_root
if [ ! -f "$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v" ] \
    || [ ! -f "$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v" ]; then
    echo "run_gl: PDK_ROOT=$PDK_ROOT is missing IHP io/stdcell Verilog models." >&2
    echo "run_gl: L2 is blocked without those files. This is not a pass." >&2
    exit 1
fi

{
    echo "=== run_gl (L2 functional, zero-delay; SDF blocked) ==="
    echo "repo=$REPO_ROOT"
    echo "netlist_src=$SRC_NL"
    echo "netlist_dest=$DEST_NL"
    echo "netlist_sha256=$SHA"
    echo "expected_sha256=$EXPECTED_NETLIST_SHA256_N5"
    echo "PDK_ROOT=$PDK_ROOT"
    echo "SDF=<unset> (zero-delay functional GL is not an SDF pass)"
    echo "args: $*"
    echo "log=$LOG"
    echo "REPRO: source test/env.sh && test/scripts/run_gl.sh $*"
} | tee "$LOG"

cd "$TEST_DIR"
set +e
make gl_test NETLIST=gate_level_netlist.v EXPECTED_NETLIST_SHA256="$EXPECTED_NETLIST_SHA256_N5" "$@" 2>&1 | tee -a "$LOG"
exit=${PIPESTATUS[0]}
set -e

echo "run_gl: exit=$exit (full log $LOG)"
exit "$exit"
