#!/usr/bin/env bash
# Toolchain health check for dma-tapeout verification.
# Prefer: source test/env.sh && test/scripts/doctor.sh
# Safe to invoke directly; will source env.sh if VIRTUAL_ENV is unset.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/.." && pwd)"
RUNS_DIR="$TEST_DIR/runs"
mkdir -p "$RUNS_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$RUNS_DIR/doctor-${STAMP}.log"

log() { echo "$*" | tee -a "$LOG"; }

log "=== dma-tapeout doctor ==="
log "log=$LOG"
log "repo=$REPO_ROOT"

if [ -z "${VIRTUAL_ENV:-}" ] || [ ! -f "${VIRTUAL_ENV}/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$TEST_DIR/env.sh" 2>&1 | tee -a "$LOG"
fi

FAIL=0
warn() { log "WARN: $*"; }
fail() { log "FAIL: $*"; FAIL=1; }
ok()   { log "OK:   $*"; }

# --- Python / venv / cocotb -------------------------------------------------

if [ -z "${VIRTUAL_ENV:-}" ]; then
    fail "dma-venv is not active (VIRTUAL_ENV unset). source test/env.sh"
else
    ok "VIRTUAL_ENV=$VIRTUAL_ENV"
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    fail "PYTHON=$PYTHON not found on PATH"
else
    ok "$PYTHON -> $(command -v "$PYTHON") ($("$PYTHON" --version 2>&1))"
fi

if ! "$PYTHON" -c "import cocotb" >/dev/null 2>&1; then
    fail "cocotb not importable via $PYTHON (pip install -r test/requirements.txt)"
else
    COCOTB_VER="$("$PYTHON" -c "import cocotb; print(cocotb.__version__)" 2>/dev/null || echo unknown)"
    ok "cocotb $COCOTB_VER"
fi

if ! command -v cocotb-config >/dev/null 2>&1; then
    fail "cocotb-config not on PATH (venv activation incomplete?)"
else
    ok "cocotb-config=$(command -v cocotb-config) ($(cocotb-config --version 2>/dev/null || true))"
fi

# --- OSS CAD Suite HDL tools -----------------------------------------------

check_tool() {
    local name="$1"
    local expect_substr="${2:-}"
    if ! command -v "$name" >/dev/null 2>&1; then
        fail "$name not on PATH (source OSS CAD Suite environment)"
        return
    fi
    local path
    path="$(command -v "$name")"
    if [[ "$path" == *'/.nix-profile/'* ]]; then
        fail "$name resolves to Nix profile ($path); use OSS CAD Suite binary instead"
        return
    fi
    # Avoid SIGPIPE/pipefail when tools print multi-line banners into head.
    local ver
    case "$name" in
        iverilog) ver="$(iverilog -V 2>&1 | sed -n '1p' || true)" ;;
        verilator) ver="$(verilator --version 2>&1 | sed -n '1p' || true)" ;;
        yosys) ver="$(yosys -V 2>&1 | sed -n '1p' || true)" ;;
        sby) ver="$(sby --version 2>&1 | sed -n '1p' || true)" ;;
        bitwuzla) ver="$(bitwuzla --version 2>&1 | sed -n '1p' || true)" ;;
        yices) ver="$(yices --version 2>&1 | sed -n '1p' || true)" ;;
        z3) ver="$(z3 --version 2>&1 | sed -n '1p' || true)" ;;
        *) ver="present" ;;
    esac
    if [ -n "$expect_substr" ] && [[ "$ver" != *"$expect_substr"* ]]; then
        warn "$name version '$ver' does not contain expected '$expect_substr' (qualify before sign-off)"
    fi
    ok "$name=$path ($ver)"
}

log "--- HDL / formal tools (OSS CAD Suite) ---"
if command -v iverilog >/dev/null 2>&1; then
    IVL_PATH="$(command -v iverilog)"
    if [[ "$IVL_PATH" == *'/test/scripts/wrappers/iverilog' ]]; then
        ok "iverilog=$IVL_PATH (wrapper; bakes wrappers/vvp into sim.vvp)"
    elif [[ "$IVL_PATH" == *'/oss-cad-suite/bin/iverilog' ]]; then
        fail "iverilog is raw suite bin/iverilog; source test/env.sh so wrappers/iverilog is first on PATH"
    else
        warn "iverilog=$IVL_PATH (expected test/scripts/wrappers/iverilog after source test/env.sh)"
    fi
    # Version via the active iverilog (wrapper or suite).
    IVL_VER="$(iverilog -V 2>&1 | sed -n '1p' || true)"
    if [[ "$IVL_VER" != *'14.0'* ]]; then
        warn "iverilog version '$IVL_VER' does not contain expected '14.0'"
    else
        ok "iverilog version: $IVL_VER"
    fi
else
    fail "iverilog not on PATH (source OSS CAD Suite environment)"
fi

check_tool verilator "5.051"
check_tool yosys "0.67"
check_tool sby "0.67"
check_tool bitwuzla "0.9.1"
check_tool yices "2.7.0"
check_tool z3 "4.15.5"

if command -v vvp >/dev/null 2>&1; then
    VVP_PATH="$(command -v vvp)"
    if [[ "$VVP_PATH" == *'/.nix-profile/'* ]]; then
        fail "vvp is from Nix ($VVP_PATH); use OSS CAD Suite via test/env.sh (no libexpat shim)"
    elif [[ "$VVP_PATH" == *'/test/scripts/wrappers/vvp' ]]; then
        ok "vvp=$VVP_PATH (cocotb-friendly suite libexec wrapper)"
    elif [[ "$VVP_PATH" == *'/oss-cad-suite/bin/vvp' ]]; then
        fail "vvp is raw suite bin/vvp ($VVP_PATH); source test/env.sh so scripts/wrappers/vvp is first on PATH (suite wrapper sets PYTHONHOME and breaks cocotb)"
    else
        warn "vvp=$VVP_PATH (expected test/scripts/wrappers/vvp after source test/env.sh)"
    fi
else
    fail "vvp not on PATH"
fi

if [ -z "${PYGPI_PYTHON_BIN:-}" ]; then
    warn "PYGPI_PYTHON_BIN unset (env.sh normally sets it to dma-venv python)"
else
    ok "PYGPI_PYTHON_BIN=$PYGPI_PYTHON_BIN"
fi
if [ -z "${LIBPYTHON_LOC:-}" ]; then
    warn "LIBPYTHON_LOC unset (env.sh normally sets it via cocotb-config --libpython)"
else
    ok "LIBPYTHON_LOC=$LIBPYTHON_LOC"
fi

# Prefer suite Verilator over stale /usr/local installs.
if command -v verilator >/dev/null 2>&1; then
    VERI_PATH="$(command -v verilator)"
    if [[ "$VERI_PATH" == /usr/local/* ]]; then
        warn "verilator is $VERI_PATH (often 5.034); prefer OSS CAD Suite Verilator 5.051 on PATH"
    fi
fi

# --- Summary ---------------------------------------------------------------

log "---"
if [ "$FAIL" -ne 0 ]; then
    log "doctor: FAILED (see $LOG)"
    exit 1
fi
log "doctor: PASS (see $LOG)"
exit 0
