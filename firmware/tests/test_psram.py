"""tCEM planner, enter/exit frames, chunked QPI, mocked transport (no rp2)."""

import pytest

from firmware.psram import (
    CMD_ENTER_QPI,
    CMD_EXIT_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
    CMD_RESET_ENABLE,
    TPU_US,
    Psram,
    PsramError,
    enter_qpi_frame,
    exit_qpi_frame,
    make_board_transport,
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
    assert qpi_chunk_bytes(CMD_QPI_READ, 20_000_000) == 23
    assert qpi_chunk_bytes(CMD_QPI_WRITE, 20_000_000) == 26


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
    assert any(row[0] == "spi" and row[2] == bytes([CMD_RESET]) for row in transport.log)
    assert any(row[0] == "spi" and row[2] == enter_qpi_frame() for row in transport.log)
    writes = [row for row in transport.log if row[0] == "qpi_write"]
    assert writes[0][2] == qpi_write_frame(0x10, b"XY")
    exit_rows = [row for row in writes if row[2] == exit_qpi_frame()]
    assert exit_rows
    assert exit_rows[0][3] == qpi_exit_sck_count() == 2
    assert exit_qpi_frame() == bytes([CMD_EXIT_QPI])


def test_qpi_write_chunks_under_tcem():
    transport = MockTransport()
    psram = Psram(transport)
    payload = bytes(range(30))
    psram.write(0, 0x100, payload)
    writes = [row for row in transport.log if row[0] == "qpi_write"]
    assert len(writes) == 2
    assert writes[0][2][0] == CMD_QPI_WRITE
    assert len(writes[0][2]) - 4 == 26
    assert len(writes[1][2]) - 4 == 4
    assert transport.mem[0][0x100] == 0
    assert transport.mem[0][0x100 + 29] == 29


def test_qpi_read_chunks_and_dummy_cycles():
    transport = MockTransport()
    for i in range(40):
        transport.mem[0][0x200 + i] = i
    psram = Psram(transport)
    data = psram.read(0, 0x200, 40)
    assert data == bytes(range(40))
    reads = [row for row in transport.log if row[0] == "qpi_read"]
    assert len(reads) == 2
    assert reads[0][2][0] == CMD_QPI_READ
    assert reads[0][3] == 6
    assert reads[0][4] == 23
    assert reads[1][4] == 17


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
