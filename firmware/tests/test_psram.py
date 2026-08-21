"""tCEM planner, enter/exit frames, chunked QPI, mocked transport (no rp2)."""

import pytest

from firmware.constants import MCU_QPI_PAYLOAD_MAX, SCK_HZ_DEFAULT, TPU_US
from firmware.psram import (
    CMD_ENTER_QPI,
    CMD_EXIT_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
    CMD_RESET_ENABLE,
    PIO_TRANSPORT_CLAIMS_PINS_IN_INIT,
    QPI_DUMMY_CYCLES,
    QPI_READ_PIO_INTENT,
    SPI_PIN_MODES,
    Psram,
    PsramError,
    drain_sm,
    enter_qpi_frame,
    exit_qpi_frame,
    make_board_transport,
    park_and_switch_sm,
    qpi_chunk_bytes,
    qpi_exit_sck_count,
    qpi_write_frame,
    rp2,
    spi_reset_frames,
    wait_at_least_us,
)

from mock_transport import MockTransport


def test_illegal_sck_rejected():
    with pytest.raises(PsramError, match="cannot fit one payload byte"):
        qpi_chunk_bytes(CMD_QPI_READ, 1_000_000)
    with pytest.raises(PsramError, match="cannot fit one payload byte"):
        Psram(MockTransport(), sck_hz=1_000_000)


def test_chunk_sizes_at_default_20mhz():
    assert qpi_chunk_bytes(CMD_QPI_READ, SCK_HZ_DEFAULT) == 23
    assert qpi_chunk_bytes(CMD_QPI_WRITE, SCK_HZ_DEFAULT) == 26
    assert qpi_chunk_bytes(CMD_QPI_WRITE, SCK_HZ_DEFAULT, mcu_payload_max=MCU_QPI_PAYLOAD_MAX) == 1


def test_enter_then_qpi_write_and_exit_is_two_sck():
    transport = MockTransport()
    psram = Psram(transport)
    psram.bring_up_both()
    psram.write(0, 0x10, b"XY")
    psram.exit_qpi(0)

    spi = [row for row in transport.log if row[0] == "spi"]
    assert spi[0] == ("spi", 0, bytes([CMD_RESET_ENABLE]))
    assert spi[1] == ("spi", 0, bytes([CMD_RESET]))
    assert spi[2] == ("spi", 0, bytes([CMD_ENTER_QPI]))
    writes = [row for row in transport.log if row[0] == "qpi_write"]
    data_writes = [row for row in writes if row[2] != exit_qpi_frame()]
    assert data_writes[0][2] == qpi_write_frame(0x10, b"X")
    assert data_writes[1][2] == qpi_write_frame(0x11, b"Y")
    exit_rows = [row for row in writes if row[2] == exit_qpi_frame()]
    assert exit_rows
    assert exit_rows[0][3] == qpi_exit_sck_count() == 2
    assert transport.pin_modes == SPI_PIN_MODES


def test_qpi_write_chunks_raise_ce_between_bytes():
    transport = MockTransport()
    psram = Psram(transport)
    psram.enter_qpi(0)
    payload = bytes(range(30))
    psram.write(0, 0x100, payload)
    writes = [row for row in transport.log if row[0] == "qpi_write"]
    assert len(writes) == 30
    assert writes[0][2][0] == CMD_QPI_WRITE
    assert len(writes[0][2]) - 4 == 1
    assert transport.mem[0][0x100] == 0
    assert transport.mem[0][0x100 + 29] == 29
    assert transport.ce_pulses == 30


def test_qpi_read_chunks_and_dummy_cycles():
    transport = MockTransport()
    for i in range(40):
        transport.mem[0][0x200 + i] = i
    psram = Psram(transport)
    psram.enter_qpi(0)
    data = psram.read(0, 0x200, 40)
    assert data == bytes(range(40))
    reads = [row for row in transport.log if row[0] == "qpi_read"]
    assert len(reads) == 40
    assert reads[0][2][0] == CMD_QPI_READ
    assert reads[0][3] == QPI_DUMMY_CYCLES
    assert reads[0][4] == 1


def test_mock_refuses_spi_after_enter_and_qpi_before():
    transport = MockTransport()
    psram = Psram(transport)
    with pytest.raises(PsramError, match="QPI write"):
        psram.write(0, 0, b"A")
    psram.enter_qpi(0)
    with pytest.raises(PsramError, match="SPI while"):
        psram.enter_qpi(0)


def test_oversized_ce_pulse_rejected_by_mock():
    transport = MockTransport(max_payload_per_ce=1)
    transport.qpi[0] = True
    with pytest.raises(PsramError, match="CE# held"):
        transport.qpi_write(0, qpi_write_frame(0, b"AB"))


def test_spi_reset_and_enter_frames():
    assert spi_reset_frames() == (bytes([CMD_RESET_ENABLE]), bytes([CMD_RESET]))
    assert enter_qpi_frame() == bytes([CMD_ENTER_QPI])


def test_cpython_import_has_no_rp2_and_board_transport_refuses():
    assert rp2 is None
    with pytest.raises(PsramError, match="rp2"):
        make_board_transport()


def test_wait_at_least_us_uses_elapsed_time_not_one_short_sleep():
    calls = []

    def short_sleep(us):
        calls.append(us)

    wait_at_least_us(TPU_US, sleep=short_sleep)
    assert calls
    assert calls[0] == TPU_US


def test_qpi_read_intent_is_rising_sck():
    from pathlib import Path

    assert QPI_READ_PIO_INTENT == (("nop", 0), ("in_", 1, 4))
    src = Path(__file__).resolve().parents[1].joinpath("psram.py").read_text(encoding="utf-8")
    assert "in_(pins, 4).side(1)" in src
    assert PIO_TRANSPORT_CLAIMS_PINS_IN_INIT is False
    assert "self.flash_cs = Pin(PIN_FLASH_CS, Pin.OUT)" not in src.split("def arm")[0]


class _FakeSM:
    def __init__(self):
        self.active_flag = 0
        self.drained = False

    def wait_idle(self):
        self.drained = True

    def active(self, value):
        if value and self.active_flag:
            raise AssertionError("overlapping active(1)")
        if value == 0 and not self.drained:
            raise PsramError("state machine deactivated before drain")
        self.active_flag = value
        if value == 0:
            self.drained = False


def test_park_and_switch_drains_before_activate():
    old = _FakeSM()
    new = _FakeSM()
    old.active_flag = 1
    parked = []
    park_and_switch_sm(old, new, park_sck=lambda: parked.append(1))
    assert old.active_flag == 0
    assert new.active_flag == 1
    assert parked == [1]
    drain_sm(new)
    new.active(0)
