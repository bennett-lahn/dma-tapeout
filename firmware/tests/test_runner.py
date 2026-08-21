"""Runner program-start-compare on mock host + mock PSRAM."""

from firmware.asic import Host
from firmware.build import add_copy, add_quit, new_image, place_bytes
from firmware.demo import DEMO_DEST, DEMO_QUIT, DEMO_SRC
from firmware.psram import CMD_EXIT_QPI, Psram
from firmware.runner import dest_extents, run_chain

from mock_board import MockDemoBoard
from mock_transport import MockTransport, attach_mock_dma


def _run_env():
    tt = MockDemoBoard(auto_ack_start=True)
    transport = MockTransport()
    attach_mock_dma(tt, transport)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    psram = Psram(transport)
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


def test_runner_fast_complete_without_done_low():
    tt = MockDemoBoard(auto_ack_start=True, instant_complete=True)
    transport = MockTransport()
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

    ok, result, mismatches = run_chain(host, Psram(transport), mem)
    assert ok is True
    assert mismatches == []
    assert result.final_memory.read(0, DEMO_DEST, 4) == pattern
    assert not any(ev[0] == "reset" for ev in tt.events)
