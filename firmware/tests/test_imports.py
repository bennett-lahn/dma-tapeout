"""Firmware (including tests) must not import test/, test.reference, or test.common."""

from pathlib import Path

FIRMWARE_ROOT = Path(__file__).resolve().parents[1]
MCU_MODULES = [
    path
    for path in FIRMWARE_ROOT.glob("*.py")
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


def test_no_cases_py_or_second_demo_script():
    names = {path.name for path in FIRMWARE_ROOT.glob("*.py")}
    assert "cases.py" not in names
    assert "demo_min.py" not in names
    demos = [name for name in names if name.startswith("demo")]
    assert demos == ["demo.py"]
