"""tCEM planner, enter/exit frames, chunked QPI, mocked transport (no rp2)."""

import pytest

from firmware.board.board import Board
from firmware.board.pins import OE_QPI, OE_QPI_READ, OE_SPI, SIO_OE_MASK
from firmware.board.qspi import QspiError, SPI_PIN_MODES
from firmware.constants import (
    CMD_ENTER_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
    CMD_RESET_ENABLE,
    MCU_QPI_PAYLOAD_MAX,
    QPI_DUMMY_CYCLES,
    SCK_HZ_DEFAULT,
    TPU_US,
)
from firmware.dma import DmaController
from firmware.psram import (
    Psram,
    PsramError,
    be24,
    enter_qpi_frame,
    exit_qpi_frame,
    qpi_chunk_bytes,
    qpi_exit_sck_count,
    qpi_read_cmd_addr,
    qpi_write_frame,
    spi_reset_frames,
)

from mock_board import MockDemoBoard
from mock_transport import MockTransport


def _granted_psram():
    """DmaController holding BUS_GNT plus a Psram wired to the same OE state."""
    tt = MockDemoBoard()
    dma = DmaController(Board(tt, sleep_us=lambda us: None))
    dma.request_bus()
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    return dma, Psram(transport, dma=dma), transport, tt


def test_illegal_sck_rejected():
    with pytest.raises(PsramError, match="cannot fit one payload byte"):
        qpi_chunk_bytes(CMD_QPI_READ, 1_000_000)
    with pytest.raises(PsramError, match="cannot fit one payload byte"):
        Psram(MockTransport(), sck_hz=1_000_000)


def test_chunk_planner_rejects_a_non_qpi_opcode():
    with pytest.raises(PsramError, match="0xEB / 0x02"):
        qpi_chunk_bytes(CMD_ENTER_QPI, SCK_HZ_DEFAULT)


def test_chunk_sizes_at_default_20mhz():
    assert qpi_chunk_bytes(CMD_QPI_READ, SCK_HZ_DEFAULT) == 23
    assert qpi_chunk_bytes(CMD_QPI_WRITE, SCK_HZ_DEFAULT) == 26
    assert (
        qpi_chunk_bytes(
            CMD_QPI_WRITE, SCK_HZ_DEFAULT, mcu_payload_max=MCU_QPI_PAYLOAD_MAX
        )
        == 1
    )


def test_frames_are_big_endian_with_the_apS6404l_opcodes():
    assert spi_reset_frames() == (bytes([CMD_RESET_ENABLE]), bytes([CMD_RESET]))
    assert enter_qpi_frame() == bytes([CMD_ENTER_QPI])
    assert be24(0x123456) == b"\x12\x34\x56"
    assert qpi_write_frame(0x123456, b"Z") == bytes([CMD_QPI_WRITE]) + b"\x12\x34\x56Z"
    assert qpi_read_cmd_addr(0x000010) == bytes([CMD_QPI_READ]) + b"\x00\x00\x10"


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


def test_bring_up_waits_tpu_before_the_first_command():
    transport = MockTransport()
    psram = Psram(transport)
    psram.bring_up_both()
    assert transport.sleeps
    assert transport.sleeps[0] == TPU_US
    assert transport.log[0][0] == "sleep_us"


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


def test_short_qpi_read_is_reported_not_padded():
    class ShortTransport(MockTransport):
        def qpi_read(self, cs, header, dummy_cycles, n):
            return b""

    transport = ShortTransport()
    transport.qpi[0] = True
    psram = Psram(transport)
    with pytest.raises(PsramError, match="expected 1"):
        psram.read(0, 0, 1)


def test_mock_refuses_spi_after_enter_and_qpi_before():
    transport = MockTransport()
    psram = Psram(transport)
    with pytest.raises(QspiError, match="QPI write"):
        psram.write(0, 0, b"A")
    psram.enter_qpi(0)
    with pytest.raises(QspiError, match="SPI while"):
        psram.enter_qpi(0)


def test_oversized_ce_pulse_rejected_by_mock():
    transport = MockTransport(max_payload_per_ce=1)
    transport.qpi[0] = True
    with pytest.raises(QspiError, match="CE# held"):
        transport.qpi_write(0, qpi_write_frame(0, b"AB"))


def test_oe_delegates_to_the_dma_controller_per_phase():
    dma, psram, transport, _tt = _granted_psram()
    psram.spi_reset(0)
    assert dma.oe == OE_SPI
    psram.enter_qpi(0)
    psram.write(0, 0, b"A")
    assert dma.oe == OE_QPI
    psram.read(0, 0, 1)
    assert dma.oe == OE_QPI_READ
    assert transport.oe_during_read == [OE_QPI_READ]
    assert all(oe & SIO_OE_MASK == 0 for oe in transport.oe_during_read)
    psram.exit_qpi(0)
    assert dma.oe == OE_SPI


def test_psram_without_a_dma_controller_never_touches_oe():
    tt = MockDemoBoard()
    board = Board(tt, sleep_us=lambda us: None)
    transport = MockTransport()
    psram = Psram(transport)
    psram.enter_qpi(0)
    psram.write(0, 0, b"A")
    assert board.read_uio_oe() == 0
