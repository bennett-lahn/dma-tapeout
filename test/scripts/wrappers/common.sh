# Shared helpers for test/scripts/wrappers/* (sourced, not executed).

find_suite_top() {
    if [ -n "${OSS_CAD_SUITE_ROOT:-}" ] && [ -x "${OSS_CAD_SUITE_ROOT}/libexec/vvp" ]; then
        readlink -f "${OSS_CAD_SUITE_ROOT}"
        return 0
    fi

    local dir
    IFS=':'
    for dir in $PATH; do
        unset IFS
        case "$dir" in
            */oss-cad-suite/bin|*/oss-cad-suite/bin/)
                if [ -x "${dir}/../libexec/vvp" ]; then
                    readlink -f "${dir}/.."
                    return 0
                fi
                ;;
        esac
    done
    unset IFS

    for dir in \
        "${HOME}/tools/oss-cad-suite" \
        "${HOME}/oss-cad-suite"
    do
        if [ -x "${dir}/libexec/vvp" ]; then
            readlink -f "${dir}"
            return 0
        fi
    done

    echo "wrappers: cannot locate OSS CAD Suite (set OSS_CAD_SUITE_ROOT or put suite bin on PATH)" >&2
    return 1
}

wrapper_dir() {
    local src="${BASH_SOURCE[0]:-$0}"
    # When sourced from common.sh, BASH_SOURCE[0] is common.sh; callers pass $0 of the wrapper.
    if [ -n "${1:-}" ]; then
        src="$1"
    fi
    cd "$(dirname "$src")" && pwd
}
