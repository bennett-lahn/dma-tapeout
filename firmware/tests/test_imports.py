"""Firmware (including tests) must not import test/, pytest, or cocotb on the MCU."""

from pathlib import Path

FIRMWARE_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = FIRMWARE_ROOT / "tests"
MCU_MODULES = [
    path
    for path in sorted(FIRMWARE_ROOT.rglob("*.py"))
    if TESTS_DIR not in path.parents
]


def _code_line(line):
    return line.split("#", 1)[0].strip()


def _forbidden_test_import(line):
    text = _code_line(line)
    if text.startswith("from test.") or text.startswith("from test import"):
        return True
    if text == "from test":
        return True
    if text.startswith("import test.") or text.startswith("import test as"):
        return True
    if text == "import test" or text.startswith("import test,"):
        return True
    return False


def test_no_test_package_imports():
    offenders = []
    for path in FIRMWARE_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _forbidden_test_import(line):
                offenders.append("%s:%d:%s" % (path, lineno, line.strip()))
    assert offenders == []


def test_mcu_modules_do_not_import_pytest_or_cocotb():
    offenders = []
    for path in MCU_MODULES:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            text = _code_line(line)
            if text.startswith("import pytest") or text.startswith("from pytest"):
                offenders.append("%s:%d" % (path.name, lineno))
            if text.startswith("import cocotb") or text.startswith("from cocotb"):
                offenders.append("%s:%d" % (path.name, lineno))
    assert offenders == []


def test_mcu_modules_have_no_runtime_typing_import():
    """MicroPython UF2 has no `typing` module; annotations must stay string-free."""
    offenders = []
    for path in MCU_MODULES:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            text = _code_line(line)
            if text.startswith("import typing") or text.startswith("from typing"):
                offenders.append("%s:%d" % (path.name, lineno))
    assert offenders == []


def test_oracle_and_runner_modules_are_gone_from_the_mcu_tree():
    """D30 amendment: chain interpretation and run/compare live on the host PC."""
    names = {path.name for path in FIRMWARE_ROOT.glob("*.py")}
    assert names.isdisjoint({"chain.py", "runner.py", "build.py", "demo.py", "asic.py"})


def test_board_layer_is_importable_without_rp2():
    from firmware.board import board, pins, qspi

    assert qspi.rp2 is None
    assert pins.PIN_FLASH_CS == pins.QSPI_BASE
    assert board.Board is not None
