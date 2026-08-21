"""Runner program-start-compare on mock host + mock PSRAM."""

import pytest

from firmware.asic import Host
from firmware.build import add_copy, add_quit, new_image, place_bytes, place_head_quit
from firmware.constants import OE_QPI_READ, SIO_OE_MASK
from firmware.demo import DEMO_DEST, DEMO_QUIT, DEMO_SRC
from firmware.psram import CMD_ENTER_QPI, CMD_EXIT_QPI, Psram, PsramError
from firmware.runner import RunError, dest_extents, run_chain
from firmware.tcd import decode_tcd

from mock_board import MockDemoBoard
from mock_transport import MockTransport, attach_mock_dma


def _run_env(auto_ack_start=True, **dma_kw):
    tt = MockDemoBoard(auto_ack_start=auto_ack_start)
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    attach_mock_dma(tt, transport, **dma_kw)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    psram = Psram(transport, host=host)
    return host, psram, transport, tt


def test_mock_runner_matches_golden_dest():
    host, psram, transport, tt = _run_env()
    mem = new_image()
    pattern = b"WXYZ"
    add_copy(
        mem,
        0,
        0,
        src_ptr=DEMO_SRC,
        dest_ptr=DEMO_DEST,
        length=len(pattern),
        next_tcd=DEMO_QUIT,
        next_device=0,
    )
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, pattern)

    ok, result, mismatches = run_chain(host, psram, mem, exit_qpi=True)
    assert ok is True
    assert mismatches == []
    assert result.dma_buf_depth == 5
    assert result.final_memory.read(0, DEMO_DEST, 4) == pattern
    assert transport.mem[0][DEMO_DEST] == ord("W")
    assert dest_extents(result) == ((0, DEMO_DEST, 4),)
    exits = [
        row
        for row in transport.log
        if row[0] == "qpi_write" and row[2] == bytes([CMD_EXIT_QPI])
    ]
    assert len(exits) == 2
    assert host.bus_req is False
    assert int(tt.uio_oe_pico) == 0
    assert transport.oe_during_read
    assert all((oe & SIO_OE_MASK) == 0 for oe in transport.oe_during_read)
    assert all(oe == OE_QPI_READ or (oe & SIO_OE_MASK) == 0 for oe in transport.oe_during_read)


def test_runner_fast_complete_without_done_low():
    tt = MockDemoBoard(auto_ack_start=True, instant_complete=True)
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    attach_mock_dma(tt, transport)
    host = Host(tt, sleep_us=lambda us: None)
    mem = new_image()
    pattern = b"WXYZ"
    add_copy(
        mem,
        0,
        0,
        src_ptr=DEMO_SRC,
        dest_ptr=DEMO_DEST,
        length=len(pattern),
        next_tcd=DEMO_QUIT,
        next_device=0,
    )
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, pattern)

    ok, result, mismatches = run_chain(host, Psram(transport, host=host), mem)
    assert ok is True
    assert mismatches == []
    assert result.final_memory.read(0, DEMO_DEST, 4) == pattern
    assert not any(ev[0] == "reset" for ev in tt.events)


def test_empty_dest_raises_not_pass():
    host, psram, transport, tt = _run_env()
    mem = new_image()
    place_head_quit(mem)
    with pytest.raises(RunError, match="no dest writes"):
        run_chain(host, psram, mem)
    ok, result, mismatches = run_chain(
        host, psram, mem, allow_empty_dest=True, bring_up=False
    )
    assert ok is False
    assert result.expected_writes == {}


def test_nonempty_mismatch_raises():
    host, psram, transport, tt = _run_env(mismatch=True)
    mem = new_image()
    add_copy(mem, 0, 0, src_ptr=DEMO_SRC, dest_ptr=DEMO_DEST, length=4, next_tcd=DEMO_QUIT)
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, b"WXYZ")
    with pytest.raises(RunError, match="dest mismatch"):
        run_chain(host, psram, mem)


def test_second_run_qpi_only_no_spi_enter():
    host, psram, transport, tt = _run_env()
    mem = new_image()
    add_copy(mem, 0, 0, src_ptr=DEMO_SRC, dest_ptr=DEMO_DEST, length=4, next_tcd=DEMO_QUIT)
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, b"WXYZ")
    run_chain(host, psram, mem, exit_qpi=False, bring_up=True)
    before = [row for row in transport.log if row[0] == "spi" and row[2] == bytes([CMD_ENTER_QPI])]
    n_enter = len(before)
    run_chain(host, psram, mem, exit_qpi=False, bring_up=False)
    after = [row for row in transport.log if row[0] == "spi" and row[2] == bytes([CMD_ENTER_QPI])]
    assert len(after) == n_enter


def test_second_spi_enter_while_in_qpi_fails():
    host, psram, transport, tt = _run_env()
    mem = new_image()
    add_copy(mem, 0, 0, src_ptr=DEMO_SRC, dest_ptr=DEMO_DEST, length=4, next_tcd=DEMO_QUIT)
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, b"WXYZ")
    run_chain(host, psram, mem, exit_qpi=False, bring_up=True)
    with pytest.raises(PsramError, match="SPI while"):
        run_chain(host, psram, mem, bring_up=True)


def test_prefilled_dest_missed_start_fails():
    host, psram, transport, tt = _run_env(auto_ack_start=False, interpret=False)
    mem = new_image()
    add_copy(mem, 0, 0, src_ptr=DEMO_SRC, dest_ptr=DEMO_DEST, length=4, next_tcd=DEMO_QUIT)
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, b"WXYZ")
    transport.qpi[0] = True
    transport.qpi[1] = True
    for i, b in enumerate(b"WXYZ"):
        transport.mem[0][DEMO_DEST + i] = b
    with pytest.raises(RunError, match="dest mismatch"):
        run_chain(host, psram, mem, bring_up=False)


def test_release_bus_on_dump_error():
    host, psram, transport, tt = _run_env()

    def boom(*args, **kwargs):
        raise RuntimeError("dump exploded")

    mem = new_image()
    add_copy(mem, 0, 0, src_ptr=DEMO_SRC, dest_ptr=DEMO_DEST, length=4, next_tcd=DEMO_QUIT)
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, b"WXYZ")
    psram.read = boom
    with pytest.raises(RuntimeError, match="dump exploded"):
        run_chain(host, psram, mem, bring_up=True)
    assert host.bus_req is False


def test_decode_dest_from_packed_head():
    mem = new_image()
    add_copy(mem, 0, 0, src_ptr=DEMO_SRC, dest_ptr=DEMO_DEST, length=4, next_tcd=DEMO_QUIT)
    add_quit(mem, 0, DEMO_QUIT)
    head = decode_tcd(mem.read(0, 0, 11))
    assert head.dest_ptr == DEMO_DEST
    assert head.dest_device == 0
