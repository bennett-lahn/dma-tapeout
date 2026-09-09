"""Unit tests for `hil/checks.py`: each checker passes clean and names the fault.

Pure-formatting checks run against a tiny in-memory stub session so a bad
diff table cannot hide behind a working DUT; the descriptor-integrity and
host-status checks run against the real loopback `Session` fixture.
"""

from __future__ import annotations

import pytest

from firmware.board.pins import OE_HIZ
from hil.cases import case_smoke
from hil.checks import (
    check_descriptors_intact,
    check_dest_writes,
    check_guard_bytes,
    check_host_status,
    collapse_extents,
    format_repro_banner,
    format_windowed_mismatch,
)
from test.reference.chain import MemoryImage, interpret_chain
from test.reference.constants import TCD_BYTES


class StubSession:
    """`read_spans` over a flat `{(device, address): byte}` map, undefined = 0."""

    def __init__(self, memory=None):
        self.memory = dict(memory or {})
        self.reads = []

    def read_spans(self, extents):
        out = {}
        for device, address, length in extents:
            self.reads.append((device, address, length))
            for offset in range(length):
                key = (device, address + offset)
                out[key] = self.memory.get(key, 0)
        return out


IDLE_STATUS = {
    "done": True,
    "bus_gnt": False,
    "bus_req": False,
    "oe": OE_HIZ,
    "rst_n_low": False,
    "in_qpi": True,
}


# --- check_dest_writes ---------------------------------------------------


def test_dest_writes_matching_passes():
    expected = {(0, 0x000280 + i): 0x40 + i for i in range(8)}
    assert check_dest_writes(expected, dict(expected)) is None


def test_dest_writes_empty_passes():
    assert check_dest_writes({}, {}) is None


def test_dest_writes_mismatch_formats_table():
    expected = {(0, 0x000280): 0x40, (1, 0x000281): 0x41}
    dumped = {(0, 0x000280): 0xBF, (1, 0x000281): 0x41}
    with pytest.raises(AssertionError) as excinfo:
        check_dest_writes(expected, dumped)
    text = str(excinfo.value)
    assert "dest writes: 1 of 2 byte(s) differ" in text
    assert "class" in text and "address" in text and "expected" in text
    assert "wrong_data" in text
    assert "0x000280" in text
    assert "0x40" in text and "0xBF" in text
    # The matching byte must not be reported.
    assert "0x000281" not in text


def test_dest_writes_classifies_missing_and_unexpected():
    with pytest.raises(AssertionError) as excinfo:
        check_dest_writes({(0, 0x10): 0xAA}, {(1, 0x20): 0x55})
    text = str(excinfo.value)
    assert "missing_write" in text
    assert "unexpected_write" in text
    # Distinct markers: nothing was observed vs nothing was expected.
    assert "<missing>" in text
    assert "<none>" in text


def test_dest_writes_truncates_long_diff_but_reports_total():
    expected = {(0, i): 0x00 for i in range(40)}
    dumped = {(0, i): 0xFF for i in range(40)}
    with pytest.raises(AssertionError) as excinfo:
        check_dest_writes(expected, dumped, max_rows=4)
    text = str(excinfo.value)
    assert "40 of 40 byte(s) differ" in text
    assert "... 36 more differing byte(s)" in text


def test_dest_writes_label_appears_in_message():
    with pytest.raises(AssertionError, match="TC-SMOKE dest writes"):
        check_dest_writes({(0, 0): 1}, {(0, 0): 2}, label="TC-SMOKE dest writes")


# --- check_guard_bytes ---------------------------------------------------


def _guarded_image(dest=0x000100, length=4, guard=0xC3):
    """Image with a 4-byte destination flanked by explicit guard sentinels."""
    mem = MemoryImage(fill=0x00)
    mem.fill_range(0, dest - 8, 8, guard)
    mem.fill_range(0, dest, length, 0x5A)
    mem.fill_range(0, dest + length, 8, guard)
    return mem


def test_guard_bytes_pass_on_untouched_guards():
    mem = _guarded_image()
    extents = ((0, 0x000100, 4),)
    memory = {
        (0, address): mem.byte(0, address) for address in range(0x0000F8, 0x00010C)
    }
    # A legitimate destination write inside the extent must not trip the check.
    for offset in range(4):
        memory[(0, 0x000100 + offset)] = 0xEE
    checked = check_guard_bytes(mem, extents, StubSession(memory))
    assert checked, "guard check must not be vacuous on an explicitly guarded image"
    assert all(key[1] not in range(0x000100, 0x000104) for key in checked)


def test_guard_bytes_detect_corruption_below_and_above():
    mem = _guarded_image()
    extents = ((0, 0x000100, 4),)
    memory = {
        (0, address): mem.byte(0, address) for address in range(0x0000F8, 0x00010C)
    }
    memory[(0, 0x0000FF)] = 0x00  # one byte below the destination
    memory[(0, 0x000104)] = 0x01  # one byte above the destination
    with pytest.raises(AssertionError) as excinfo:
        check_guard_bytes(mem, extents, StubSession(memory))
    text = str(excinfo.value)
    assert "out-of-bounds write" in text
    assert "0x0000FF" in text and "0x000104" in text
    assert "0xC3" in text


def test_guard_bytes_skip_undefined_baseline_by_default():
    mem = MemoryImage(fill=0x00)
    mem.fill_range(0, 0x000100, 4, 0x5A)
    extents = ((0, 0x000100, 4),)
    session = StubSession()
    # Nothing outside the extent was ever installed, so there is no baseline.
    assert check_guard_bytes(mem, extents, session) == {}
    assert session.reads == []
    # Opting in compares against the image fill byte instead.
    checked = check_guard_bytes(mem, extents, session, skip_undefined=False)
    assert len(checked) == 2 * 16


def test_guard_bytes_clamp_at_address_zero():
    mem = MemoryImage(fill=0x00)
    mem.fill_range(0, 0x000000, 2, 0x5A)
    mem.fill_range(0, 0x000002, 4, 0xC3)
    memory = {
        (0, address): mem.byte(0, address) for address in range(0x000000, 0x000006)
    }
    checked = check_guard_bytes(
        mem, ((0, 0x000000, 2),), StubSession(memory), guard_len=4
    )
    assert set(checked) == {(0, 0x000002), (0, 0x000003), (0, 0x000004), (0, 0x000005)}


def test_guard_bytes_no_extents_is_a_pass():
    assert check_guard_bytes(MemoryImage(fill=0x00), (), StubSession()) == {}


# --- check_descriptors_intact -------------------------------------------


def test_descriptors_intact_passes_on_installed_image(session):
    case = case_smoke()
    session.write_spans(case.spans)
    assert check_descriptors_intact(case, session) is None


def test_descriptors_intact_survives_a_real_run(session):
    case = case_smoke()
    session.write_spans(case.spans)
    session.start_and_wait(timeout_ms=5000)
    assert check_descriptors_intact(case, session) is None


def test_descriptors_intact_detects_modified_descriptor(session):
    case = case_smoke()
    session.write_spans(case.spans)
    head = case.initial_mem.read(0, 0x000000, TCD_BYTES)
    corrupt = bytes([head[0] ^ 0xFF]) + head[1:]
    session.write_spans([(0, 0x000000, corrupt)])
    with pytest.raises(AssertionError) as excinfo:
        check_descriptors_intact(case, session)
    text = str(excinfo.value)
    assert "were overwritten during the run" in text
    assert "0x000000" in text
    assert "bad offsets" in text
    assert "\n" in text


def test_descriptors_intact_accepts_declared_locations(session):
    """A GeneratedChain-style object supplies its own descriptor slot list."""
    case = case_smoke()
    session.write_spans(case.spans)
    path = tuple(interpret_chain(case.initial_mem).path)
    assert check_descriptors_intact(case, session, locations=path) is None


def test_descriptors_intact_rejects_object_without_memory(session):
    with pytest.raises(TypeError, match="no initial_mem"):
        check_descriptors_intact(object(), session)


# --- check_host_status ---------------------------------------------------


def test_host_status_accepts_idle_loopback_port(session):
    assert check_host_status(session.get_status()) is None


def test_host_status_accepts_idle_after_a_run(session):
    case = case_smoke()
    session.write_spans(case.spans)
    session.start_and_wait(timeout_ms=5000)
    assert check_host_status(session.get_status()) is None


@pytest.mark.parametrize(
    "field, value, needle",
    [
        ("done", False, "done=False, expected 1"),
        ("bus_gnt", True, "bus_gnt=True, expected 0"),
        ("bus_req", True, "bus_req=True, expected 0"),
        ("rst_n_low", True, "rst_n_low=True, expected False"),
        ("oe", 0x0F, "oe=0x0F, expected 0x00"),
    ],
)
def test_host_status_rejects_non_idle_signal(field, value, needle):
    status = dict(IDLE_STATUS)
    status[field] = value
    with pytest.raises(AssertionError) as excinfo:
        check_host_status(status)
    text = str(excinfo.value)
    assert "1 signal(s) not idle" in text
    assert needle in text


def test_host_status_reports_every_bad_signal():
    with pytest.raises(AssertionError) as excinfo:
        check_host_status({})
    assert "5 signal(s) not idle" in str(excinfo.value)


# --- diagnostics ---------------------------------------------------------


def test_repro_banner_is_one_runnable_line():
    banner = format_repro_banner("TC-SMOKE", 42, "loopback")
    assert banner == "REPRO: python -m hil --target=loopback --seed=42 -k TC-SMOKE"
    assert "\n" not in banner


def test_repro_banner_appends_node_id():
    banner = format_repro_banner(
        "random-3", 7, "asic", "hil/tests/test_random.py::test_random_chain[3]"
    )
    assert "--target=asic" in banner
    assert "--seed=7" in banner
    assert banner.endswith("# node: hil/tests/test_random.py::test_random_chain[3]")


def test_windowed_mismatch_windows_the_oracle_log():
    case = case_smoke()
    result = interpret_chain(case.initial_mem)
    dumped = dict(result.expected_writes)
    bad_key = sorted(dumped)[-1]
    dumped[bad_key] = (~dumped[bad_key]) & 0xFF
    text = format_windowed_mismatch(result, dumped, window=1)
    assert "first mismatch dev=0" in text
    assert "window +/-1" in text
    assert "DATA_WRITE" in text
    lines = text.splitlines()
    # Header plus at most 2*window+1 log records.
    assert 2 <= len(lines) <= 4


def test_windowed_mismatch_reports_no_mismatch():
    case = case_smoke()
    result = interpret_chain(case.initial_mem)
    text = format_windowed_mismatch(result, dict(result.expected_writes))
    assert "no destination mismatch" in text


def test_windowed_mismatch_flags_unexpected_write_without_a_writer():
    case = case_smoke()
    result = interpret_chain(case.initial_mem)
    dumped = dict(result.expected_writes)
    dumped[(1, 0x7FFFFF)] = 0xA5
    text = format_windowed_mismatch(result, dumped)
    assert "first mismatch dev=1 addr=0x7FFFFF expected=<none> got=0xA5" in text
    assert "no write transaction covers that byte" in text


def test_windowed_mismatch_without_a_log():
    class NoLog:
        transactions = ()
        expected_writes = {}

    assert "no transaction log available" in format_windowed_mismatch(NoLog(), {})


# --- shared helpers ------------------------------------------------------


def test_collapse_extents_merges_runs_per_device():
    keys = [(0, 4), (0, 5), (0, 6), (0, 9), (1, 0)]
    assert collapse_extents(keys) == ((0, 4, 3), (0, 9, 1), (1, 0, 1))
