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


def _normalize_oracle_source(text):
    """Drop import lines and the MicroPython dataclass try/except shim."""
    lines = text.splitlines()
    out = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("from ") or stripped.startswith("import "):
            if "(" in stripped and ")" not in stripped:
                i += 1
                while i < n and ")" not in lines[i]:
                    i += 1
                i += 1
                continue
            i += 1
            continue
        if stripped == "try:":
            j = i + 1
            while j < n and (
                lines[j].startswith(" ")
                or lines[j].startswith("\t")
                or not lines[j].strip()
            ):
                j += 1
            if j < n and "ImportError" in lines[j]:
                j += 1
                while j < n and (
                    lines[j].startswith(" ")
                    or lines[j].startswith("\t")
                    or not lines[j].strip()
                ):
                    j += 1
                i = j
                continue
        out.append(lines[i].rstrip())
        i += 1
    compact = [line for line in out if line.strip()]
    return "\n".join(compact)


def test_firmware_oracle_copies_match_reference():
    repo = FIRMWARE_ROOT.parent
    pairs = [
        (FIRMWARE_ROOT / "tcd.py", repo / "test" / "reference" / "tcd.py"),
        (FIRMWARE_ROOT / "chain.py", repo / "test" / "reference" / "chain.py"),
    ]
    for fw_path, ref_path in pairs:
        fw = _normalize_oracle_source(fw_path.read_text(encoding="utf-8"))
        ref = _normalize_oracle_source(ref_path.read_text(encoding="utf-8"))
        assert fw == ref, "%s drifted from %s" % (fw_path.name, ref_path)
