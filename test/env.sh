# dma-tapeout verification environment
# Usage (from any cwd, in bash/WSL):
#   source test/env.sh
#
# Expects OSS CAD Suite tools already on PATH (typical interactive WSL shell
# sources ~/tools/oss-cad-suite/environment from ~/.bashrc). Activates the
# repo-root dma-venv and exports PYTHON=python3 for Make/scripts.
#
# Do not execute this file; source it.

if [ "${BASH_SOURCE-}" = "$0" ]; then
    echo "env.sh: source this file instead of executing it:" >&2
    echo "  source test/env.sh" >&2
    exit 1
fi

_ENV_SH="${BASH_SOURCE[0]:-$0}"
_TEST_DIR="$(cd "$(dirname "$_ENV_SH")" && pwd)"
_REPO_ROOT="$(cd "$_TEST_DIR/.." && pwd)"
unset _ENV_SH

# Non-interactive agent shells often skip ~/.bashrc (where OSS CAD is sourced).
# If iverilog is missing, try the common install location once.
if ! command -v iverilog >/dev/null 2>&1; then
    for _cand in \
        "${HOME}/tools/oss-cad-suite/environment" \
        "${HOME}/oss-cad-suite/environment"
    do
        if [ -f "$_cand" ]; then
            # shellcheck disable=SC1090
            source "$_cand"
            echo "env.sh: sourced $_cand (iverilog was not on PATH)"
            break
        fi
    done
    unset _cand
fi

if ! command -v iverilog >/dev/null 2>&1; then
    echo "env.sh: WARNING: iverilog not on PATH. Source OSS CAD Suite first, e.g.:" >&2
    echo "  source ~/tools/oss-cad-suite/environment" >&2
elif [[ "$(command -v iverilog)" == *'/.nix-profile/'* ]]; then
    echo "env.sh: WARNING: iverilog is from ~/.nix-profile ($(command -v iverilog))." >&2
    echo "  Suite vvp/iverilog on PATH is authoritative; the Nix/libexpat shim is obsolete." >&2
fi

_VENV="$_REPO_ROOT/dma-venv"
if [ ! -f "$_VENV/bin/activate" ]; then
    echo "env.sh: ERROR: dma-venv not found at $_VENV" >&2
    echo "  Create it and install pins: python3 -m venv dma-venv && source dma-venv/bin/activate && python3 -m pip install -r test/requirements.txt" >&2
    unset _VENV _TEST_DIR _REPO_ROOT
    return 1 2>/dev/null || exit 1
fi

# shellcheck disable=SC1091
source "$_VENV/bin/activate"
unset _VENV

# Detect suite root for wrappers (before prepending wrapper dir, which shadows
# suite bin/iverilog).
if [ -z "${OSS_CAD_SUITE_ROOT:-}" ]; then
    _iv="$(command -v iverilog 2>/dev/null || true)"
    if [ -n "$_iv" ]; then
        _iv_dir="$(cd "$(dirname "$_iv")" && pwd)"
        if [ -x "${_iv_dir}/../libexec/vvp" ]; then
            export OSS_CAD_SUITE_ROOT="$(readlink -f "${_iv_dir}/..")"
        fi
    fi
    unset _iv _iv_dir
fi

# Suite bin/vvp forces PYTHONHOME; suite iverilog bakes that path into sim.vvp.
# Prefer our wrappers (libexec + dma-venv PYGPI) ahead of suite bin/.
_WRAPPER_DIR="$_TEST_DIR/scripts/wrappers"
case ":$PATH:" in
    *":$_WRAPPER_DIR:"*) ;;
    *) export PATH="$_WRAPPER_DIR:$PATH" ;;
esac
unset _WRAPPER_DIR

# Keep suite *HDL* binaries; do not let suite Python override the venv.
if [ -n "${PYTHONHOME:-}" ]; then
    echo "env.sh: unsetting PYTHONHOME (was $PYTHONHOME) so dma-venv owns Python for cocotb"
    unset PYTHONHOME
fi

export PYTHON="${PYTHON:-python3}"
export REPO_ROOT="$_REPO_ROOT"
export DMA_TEST_DIR="$_TEST_DIR"

# Cocotb defaults used by Make and hook scripts (override as needed).
export COCOTB_REDUCED_LOG_FMT="${COCOTB_REDUCED_LOG_FMT:-1}"
if [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    export PYGPI_PYTHON_BIN="${PYGPI_PYTHON_BIN:-${VIRTUAL_ENV}/bin/python}"
fi
if command -v cocotb-config >/dev/null 2>&1; then
    _LIBPY="$(cocotb-config --libpython 2>/dev/null || true)"
    if [ -n "$_LIBPY" ]; then
        export LIBPYTHON_LOC="${LIBPYTHON_LOC:-$_LIBPY}"
    fi
    unset _LIBPY
fi

echo "env.sh: repo=$REPO_ROOT"
echo "env.sh: venv=$VIRTUAL_ENV"
echo "env.sh: PYTHON=$PYTHON ($(command -v "$PYTHON" 2>/dev/null || echo missing))"
echo "env.sh: OSS_CAD_SUITE_ROOT=${OSS_CAD_SUITE_ROOT:-<unset>}"
if command -v iverilog >/dev/null 2>&1; then
    echo "env.sh: iverilog=$(command -v iverilog)"
else
    echo "env.sh: iverilog=MISSING"
fi
if command -v vvp >/dev/null 2>&1; then
    echo "env.sh: vvp=$(command -v vvp)"
else
    echo "env.sh: vvp=MISSING"
fi
if command -v verilator >/dev/null 2>&1; then
    echo "env.sh: verilator=$(command -v verilator) ($(verilator --version 2>/dev/null | sed -n '1p' || true))"
else
    echo "env.sh: verilator=MISSING"
fi
if command -v cocotb-config >/dev/null 2>&1; then
    echo "env.sh: cocotb=$(cocotb-config --version 2>/dev/null || echo present)"
else
    echo "env.sh: cocotb=MISSING (pip install -r test/requirements.txt)"
fi
echo "env.sh: PYGPI_PYTHON_BIN=${PYGPI_PYTHON_BIN:-<unset>} LIBPYTHON_LOC=${LIBPYTHON_LOC:-<unset>}"

unset _TEST_DIR _REPO_ROOT
