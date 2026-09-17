"""Unit tests for `hil/checks.py`: each checker passes clean and names the fault.

Pure-formatting checks (`check_dest_writes`, `check_guard_bytes`,
`check_head_tcd_installed`, `format_windowed_mismatch`, `format_repro_banner`,
`collapse_extents`) run against a tiny in-memory `StubSession` built by hand
in this file, so a bad diff table cannot hide behind a working DUT. They are
pure host self-tests and never touch `--target`.

The descriptor-integrity and host-status checks below run against the real
`session` fixture instead, so they drive whatever `--target` was selected
(hardware/DUT tests): their names carry a `test_hw_` prefix and they are
auto-tagged `hw` by `hil/conftest.py`.
"""

from __future__ import annotations

import pytest

from firmware.board.pins import OE_HIZ
from firmware.constants import MCU_QPI_PAYLOAD_MAX
from hil.cases import case_smoke
from hil.checks import (
    PROBE_PATTERN,
    check_descriptors_intact,
    check_dest_writes,
    check_guard_bytes,
    check_head_tcd_installed,
    check_host_status,
    collapse_extents,
    format_repro_banner,
    format_windowed_mismatch,
    probe_qpi_read_phase,
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


def test_selftest_dest_writes_matching_passes():
    expected = {(0, 0x000280 + i): 0x40 + i for i in range(8)}
    assert check_dest_writes(expected, dict(expected)) is None


def test_selftest_dest_writes_empty_passes():
    assert check_dest_writes({}, {}) is None


def test_selftest_dest_writes_mismatch_formats_table():
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


def test_selftest_dest_writes_classifies_missing_and_unexpected():
    with pytest.raises(AssertionError) as excinfo:
        check_dest_writes({(0, 0x10): 0xAA}, {(1, 0x20): 0x55})
    text = str(excinfo.value)
    assert "missing_write" in text
    assert "unexpected_write" in text
    # Distinct markers: nothing was observed vs nothing was expected.
    assert "<missing>" in text
    assert "<none>" in text


def test_selftest_dest_writes_truncates_long_diff_but_reports_total():
    expected = {(0, i): 0x00 for i in range(40)}
    dumped = {(0, i): 0xFF for i in range(40)}
    with pytest.raises(AssertionError) as excinfo:
        check_dest_writes(expected, dumped, max_rows=4)
    text = str(excinfo.value)
    assert "40 of 40 byte(s) differ" in text
    assert "... 36 more differing byte(s)" in text


def test_selftest_dest_writes_label_appears_in_message():
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


def test_selftest_guard_bytes_pass_on_untouched_guards():
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


def test_selftest_guard_bytes_detect_corruption_below_and_above():
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


def test_selftest_guard_bytes_skip_undefined_baseline_by_default():
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


def test_selftest_guard_bytes_clamp_at_address_zero():
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


def test_selftest_guard_bytes_no_extents_is_a_pass():
    assert check_guard_bytes(MemoryImage(fill=0x00), (), StubSession()) == {}


# --- check_head_tcd_installed -------------------------------------------


def test_selftest_head_tcd_peek_matches_the_install_image():
    case = case_smoke()
    head = case.initial_mem.read(0, 0x000000, TCD_BYTES)
    stub = StubSession(
        {(0, offset): head[offset] for offset in range(TCD_BYTES)}
    )
    assert check_head_tcd_installed(case, stub) is None
    assert stub.reads == [(0, 0x000000, TCD_BYTES)]


def test_selftest_head_tcd_peek_flags_an_all_zero_readback():
    case = case_smoke()
    with pytest.raises(AssertionError, match="all zeros") as excinfo:
        check_head_tcd_installed(case, StubSession())
    text = str(excinfo.value)
    assert "before START" in text
    assert "self-pointing" in text
    assert "0x000000" in text


def test_selftest_head_tcd_peek_flags_a_corrupt_readback():
    case = case_smoke()
    head = bytearray(case.initial_mem.read(0, 0x000000, TCD_BYTES))
    head[0] ^= 0xFF
    stub = StubSession(
        {(0, offset): head[offset] for offset in range(TCD_BYTES)}
    )
    with pytest.raises(AssertionError, match="did not read back") as excinfo:
        check_head_tcd_installed(case, stub)
    # A stable re-read means the bytes are not decaying on their own.
    text = str(excinfo.value)
    assert "re-read is identical" in text
    assert stub.reads == [(0, 0x000000, TCD_BYTES)] * 2


def test_selftest_head_tcd_peek_calls_an_unstable_readback_blocked_refresh():
    # No write happens between the two reads, so a difference can only mean
    # the array is changing by itself: tCEM (max CE# low, 4 us) overrun.
    class _DriftingSession(StubSession):
        def read_spans(self, extents):
            out = super().read_spans(extents)
            for key in out:
                self.memory[key] = (self.memory.get(key, 0) + 1) & 0xFF
            return out

    case = case_smoke()
    head = case.initial_mem.read(0, 0x000000, TCD_BYTES)
    stub = _DriftingSession(
        {(0, offset): head[offset] ^ 0xFF for offset in range(TCD_BYTES)}
    )
    with pytest.raises(AssertionError, match="did not read back") as excinfo:
        check_head_tcd_installed(case, stub)
    text = str(excinfo.value)
    assert "re-read moves in %d of %d bytes" % (TCD_BYTES, TCD_BYTES) in text
    assert "tCEM" in text


@pytest.mark.checks
def test_hw_head_tcd_peek_passes_after_install(session):
    case = case_smoke()
    session.write_spans(case.spans)
    assert check_head_tcd_installed(case, session) is None


# --- probe_qpi_read_phase -----------------------------------------------


class _ProbeSession(StubSession):
    """Writes land verbatim; both read paths come from `_frame`."""

    def write_spans(self, spans):
        for device, addr, data in spans:
            for offset, value in enumerate(bytes(data)):
                self.memory[(device, addr + offset)] = value

    def _stored(self, device, addr, length):
        return bytes(self.memory.get((device, addr + i), 0) for i in range(length))

    def _frame(self, device, addr, length):
        return self._stored(device, addr, length)

    def qpi_probe_read(self, device, addr, length):
        return self._frame(device, addr, length)

    def read_spans(self, extents):
        # Mirrors Psram.read: one `_frame` per MCU_QPI_PAYLOAD_MAX-byte chunk.
        out = {}
        for device, addr, length in extents:
            offset = 0
            while offset < length:
                n = min(MCU_QPI_PAYLOAD_MAX, length - offset)
                chunk = self._frame(device, addr + offset, n)
                for i, value in enumerate(chunk):
                    out[(device, addr + offset + i)] = value
                offset += n
        return out


def test_selftest_read_phase_probe_passes_when_aligned():
    assert probe_qpi_read_phase(_ProbeSession())["shift"] == 0


def test_selftest_read_phase_probe_names_a_one_nibble_shift():
    # Capture one SCK late: the stream shifts right by one and the first
    # sample is bus turnaround, so every frame length shows the same lag.
    class _Lagging(_ProbeSession):
        def _frame(self, device, addr, length):
            nibbles = [0xF]
            for value in self._stored(device, addr, length):
                nibbles.extend(((value >> 4) & 0x0F, value & 0x0F))
            return bytes(
                (nibbles[2 * i] << 4) | nibbles[2 * i + 1] for i in range(length)
            )

    with pytest.raises(AssertionError, match="1 nibble") as excinfo:
        probe_qpi_read_phase(_Lagging())
    text = str(excinfo.value)
    assert "1 SCK off" in text
    assert "- 0 1 2 3 4 5 -" in text


def test_selftest_read_phase_probe_blames_the_write_path():
    # Both read paths agree, so the stored bytes themselves are wrong.
    class _BadWrite(_ProbeSession):
        def write_spans(self, spans):
            for device, addr, data in spans:
                for offset, value in enumerate(bytes(data)):
                    self.memory[(device, addr + offset)] = value ^ 0x0F

    with pytest.raises(AssertionError, match="fault is in the write path"):
        probe_qpi_read_phase(_BadWrite())


def test_selftest_read_phase_probe_blames_the_read_path():
    # Only the multi-byte frame is mangled, so capture depends on frame length.
    class _FrameDependent(_ProbeSession):
        def _frame(self, device, addr, length):
            stored = self._stored(device, addr, length)
            # The probe's unchunked path asks for pattern+1 bytes; the chunked
            # path stays at len(pattern). Only the longer frame is mangled.
            return stored if length <= len(PROBE_PATTERN) else bytes(v ^ 0x0F for v in stored)

    with pytest.raises(AssertionError, match="fault is in the read path"):
        probe_qpi_read_phase(_FrameDependent())


@pytest.mark.checks
def test_hw_qpi_read_phase_is_aligned(session):
    assert probe_qpi_read_phase(session)["shift"] == 0


# --- check_descriptors_intact -------------------------------------------
#
# These take the `session` fixture, so they drive whatever `--target` was
# selected (hardware/DUT tests, hence the `test_hw_` prefix and the
# auto-applied `hw` marker). `@pytest.mark.checks` puts them in the
# `hw_checks` menu category alongside the host-status sanity checks below,
# rather than the feature-case categories used by `test_directed.py`.


@pytest.mark.checks
def test_hw_descriptors_intact_passes_on_installed_image(session):
    case = case_smoke()
    session.write_spans(case.spans)
    assert check_descriptors_intact(case, session) is None


@pytest.mark.checks
def test_hw_descriptors_intact_survives_a_real_run(session):
    case = case_smoke()
    session.write_spans(case.spans)
    session.start_and_wait(timeout_ms=5000)
    assert check_descriptors_intact(case, session) is None


@pytest.mark.checks
def test_hw_descriptors_intact_detects_modified_descriptor(session):
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


@pytest.mark.checks
def test_hw_descriptors_intact_accepts_declared_locations(session):
    """A GeneratedChain-style object supplies its own descriptor slot list."""
    case = case_smoke()
    session.write_spans(case.spans)
    path = tuple(interpret_chain(case.initial_mem).path)
    assert check_descriptors_intact(case, session, locations=path) is None


@pytest.mark.checks
def test_hw_descriptors_intact_rejects_object_without_memory(session):
    with pytest.raises(TypeError, match="no initial_mem"):
        check_descriptors_intact(object(), session)


# --- check_host_status ---------------------------------------------------


@pytest.mark.checks
def test_hw_host_status_accepts_idle_before_any_run(session):
    assert check_host_status(session.get_status()) is None


@pytest.mark.checks
def test_hw_host_status_accepts_idle_after_a_run(session):
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
def test_selftest_host_status_rejects_non_idle_signal(field, value, needle):
    status = dict(IDLE_STATUS)
    status[field] = value
    with pytest.raises(AssertionError) as excinfo:
        check_host_status(status)
    text = str(excinfo.value)
    assert "1 signal(s) not idle" in text
    assert needle in text


def test_selftest_host_status_reports_every_bad_signal():
    with pytest.raises(AssertionError) as excinfo:
        check_host_status({})
    assert "5 signal(s) not idle" in str(excinfo.value)


# --- diagnostics ---------------------------------------------------------


def test_selftest_repro_banner_is_one_runnable_line():
    banner = format_repro_banner("TC-SMOKE", 42, "loopback")
    assert banner == "REPRO: python -m hil --target=loopback --seed=42 -k TC-SMOKE"
    assert "\n" not in banner


def test_selftest_repro_banner_appends_node_id():
    banner = format_repro_banner(
        "random-3", 7, "asic", "hil/tests/test_random.py::test_hw_random_chain[3]"
    )
    assert "--target=asic" in banner
    assert "--seed=7" in banner
    assert banner.endswith("# node: hil/tests/test_random.py::test_hw_random_chain[3]")


def test_selftest_windowed_mismatch_windows_the_oracle_log():
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


def test_selftest_windowed_mismatch_reports_no_mismatch():
    case = case_smoke()
    result = interpret_chain(case.initial_mem)
    text = format_windowed_mismatch(result, dict(result.expected_writes))
    assert "no destination mismatch" in text


def test_selftest_windowed_mismatch_flags_unexpected_write_without_a_writer():
    case = case_smoke()
    result = interpret_chain(case.initial_mem)
    dumped = dict(result.expected_writes)
    dumped[(1, 0x7FFFFF)] = 0xA5
    text = format_windowed_mismatch(result, dumped)
    assert "first mismatch dev=1 addr=0x7FFFFF expected=<none> got=0xA5" in text
    assert "no write transaction covers that byte" in text


def test_selftest_windowed_mismatch_without_a_log():
    class NoLog:
        transactions = ()
        expected_writes = {}

    assert "no transaction log available" in format_windowed_mismatch(NoLog(), {})


# --- shared helpers ------------------------------------------------------


def test_selftest_collapse_extents_merges_runs_per_device():
    keys = [(0, 4), (0, 5), (0, 6), (0, 9), (1, 0)]
    assert collapse_extents(keys) == ((0, 4, 3), (0, 9, 1), (1, 0, 1))
