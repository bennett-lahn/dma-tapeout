"""Shared REPRO strings and start-of-run log lines.

Cocotb tests call :func:`begin_run` instead of copying a local ``_repro()``.
This module only formats the copy-paste command and the SEED/config banner.
:func:`common.dispose.dispose_run` still owns the pass/fail contract and the
end-of-window catalog print (compact on a clean pass; per-ID on fail,
``blocked``, or ``DISPOSE_VERBOSE``).

Both ``test/scripts/run_test.sh`` and ``cd test && make test`` forms are
generated from the same config. L2 uses ``run_gl.sh`` / ``make gl_test``.

Contract: ``docs/llm/verification/02-platform.md``.
"""

from __future__ import annotations

import inspect
import os

from common.config import parse_run_config, timing_env_overrides

RUNNER_RUN_TEST = "run_test"
RUNNER_RUN_GL = "run_gl"

_VERBOSE_TRUE = {"1", "true", "yes", "on"}


def dispose_verbose() -> bool:
    """Return True when per-ID dispose lines should print even on a clean pass.

    Reads ``DISPOSE_VERBOSE``. Does not change dispose pass/fail semantics.
    """
    return os.environ.get("DISPOSE_VERBOSE", "").strip().lower() in _VERBOSE_TRUE


def format_repro(
    config: dict,
    test_filter: str,
    *,
    module: str | None = None,
    extra: dict[str, str] | None = None,
    runner: str | None = None,
    level: str | None = None,
    timing_profile: str | None = None,
) -> str:
    """Return the primary ``REPRO:`` line for assertion suffixes and logs.

    Builds the line from *config* (``parse_run_config()`` shape) plus
    *test_filter* and optional *module* (``COCOTB_TEST_MODULES``). *extra*
    and any live ``PSRAM_*`` / ``TB_*`` env knobs are appended. *runner*
    selects ``run_test`` or ``run_gl``; default follows ``LEVEL=gl``.
    *level* / *timing_profile* override the config when a case must name a
    required profile rather than whatever the process currently has.

    The returned string is the script form (``run_test.sh`` or ``run_gl.sh``).
    :func:`format_repro_lines` also emits the matching ``make`` form.
    """
    return format_repro_lines(
        config,
        test_filter,
        module=module,
        extra=extra,
        runner=runner,
        level=level,
        timing_profile=timing_profile,
    )[0]


def format_repro_lines(
    config: dict,
    test_filter: str,
    *,
    module: str | None = None,
    extra: dict[str, str] | None = None,
    runner: str | None = None,
    level: str | None = None,
    timing_profile: str | None = None,
) -> list[str]:
    """Return script and make ``REPRO:`` lines for the same configuration.

    The first line is the preferred hook-script form. The second is the
    equivalent ``make test`` / ``make gl_test`` form. Relative paths assume
    the command is pasted from the repository root (``source test/env.sh``).
    """
    runner = runner or _default_runner(config)
    assignments = _assignments(
        config,
        test_filter,
        module=module,
        extra=extra,
        level=level,
        timing_profile=timing_profile,
        runner=runner,
    )
    args = " ".join(assignments)
    if runner == RUNNER_RUN_GL:
        return [
            f"REPRO: source test/env.sh && test/scripts/run_gl.sh {args}",
            f"REPRO: source test/env.sh && cd test && make gl_test {args}",
        ]
    return [
        f"REPRO: source test/env.sh && test/scripts/run_test.sh {args}",
        f"REPRO: source test/env.sh && cd test && make test {args}",
    ]


def log_run_start(log, config: dict, repro_lines, *, test: str | None = None) -> None:
    """Write the SEED/config banner and every ``REPRO:`` line to *log*.

    *repro_lines* is a string or a sequence of strings. Unique directed
    messages (which SIO bit was X, pin txn dump, coverage paths) stay in
    the test. This helper owns only the lines every suite repeats.
    """
    if isinstance(repro_lines, str):
        lines = [repro_lines]
    else:
        lines = list(repro_lines)
    prefix = f"test={test} " if test else ""
    waves = config.get("waves", "auto")
    extra = f" WAVES={waves}" if waves and waves != "auto" else ""
    log.info(
        "RUN %sSEED=%s LEVEL=%s SIM=%s DUT_LEVEL=%s DMA_BUF_DEPTH=%s "
        "TIMING_PROFILE=%s%s",
        prefix,
        config.get("seed"),
        config.get("level"),
        config.get("sim"),
        config.get("dut_level"),
        config.get("dma_buf_depth"),
        config.get("timing_profile"),
        extra,
    )
    for line in lines:
        log.info("%s", line)


def begin_run(
    dut,
    test_filter: str,
    *,
    test: str | None = None,
    module: str | None = None,
    extra: dict[str, str] | None = None,
    runner: str | None = None,
    level: str | None = None,
    timing_profile: str | None = None,
) -> tuple[dict, str]:
    """Parse run config, build REPRO lines, and log the start banner.

    Returns ``(config, primary_repro)``. Pass *primary_repro* to
    ``dispose_run(..., repro=...)``. *module* defaults to the calling
    ``tests.*`` module. Does not bring up the DUT or start monitors.
    """
    config = parse_run_config()
    if module is None:
        module = _infer_module()
    lines = format_repro_lines(
        config,
        test_filter,
        module=module,
        extra=extra,
        runner=runner,
        level=level,
        timing_profile=timing_profile,
    )
    log_run_start(dut._log, config, lines, test=test)
    return config, lines[0]


def _default_runner(config: dict) -> str:
    if config.get("level") == "gl" or config.get("dut_level") == "L2":
        return RUNNER_RUN_GL
    return RUNNER_RUN_TEST


def _module_arg(module: str | None) -> str | None:
    if not module:
        return None
    if module.startswith("tests."):
        return module
    return f"tests.{module}"


def _assignments(
    config: dict,
    test_filter: str,
    *,
    module: str | None,
    extra: dict[str, str] | None,
    level: str | None,
    timing_profile: str | None,
    runner: str,
) -> list[str]:
    level_value = level if level is not None else config.get("level", "top")
    timing_value = (
        timing_profile
        if timing_profile is not None
        else config.get("timing_profile", "ideal")
    )
    parts: list[str] = []
    if runner != RUNNER_RUN_GL:
        _append(parts, "LEVEL", level_value)
        _append(parts, "SIM", config.get("sim", "icarus"))
    _append(parts, "SEED", config.get("seed", 1))
    _append(parts, "DMA_BUF_DEPTH", config.get("dma_buf_depth"))
    _append(parts, "TIMING_PROFILE", timing_value)
    module_arg = _module_arg(module)
    if module_arg:
        _append(parts, "COCOTB_TEST_MODULES", module_arg)
    _append(parts, "TEST_FILTER", test_filter)
    waves = config.get("waves", "auto")
    if waves and waves != "auto":
        _append(parts, "WAVES", waves)
    env_extra = dict(timing_env_overrides())
    if extra:
        env_extra.update(extra)
    for name in sorted(env_extra):
        _append(parts, name, env_extra[name])
    return parts


def _append(parts: list[str], name: str, value) -> None:
    if value is None or value == "":
        return
    parts.append(f"{name}={value}")


def _infer_module() -> str | None:
    """Return the first ``tests.*`` module on the stack, or None."""
    for info in inspect.stack():
        name = info.frame.f_globals.get("__name__", "")
        if isinstance(name, str) and name.startswith("tests."):
            return name
    return None


__all__ = [
    "RUNNER_RUN_GL",
    "RUNNER_RUN_TEST",
    "begin_run",
    "dispose_verbose",
    "format_repro",
    "format_repro_lines",
    "log_run_start",
]
