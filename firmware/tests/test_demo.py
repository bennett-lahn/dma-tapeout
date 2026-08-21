"""demo.py builds the one canned PSRAM0 copy via build.py and the runner matches."""

from firmware.asic import Host
from firmware.demo import DEMO_DEST, DEMO_PATTERN, DEMO_QUIT, DEMO_SRC, build_demo_memory, main
from firmware.psram import Psram
from firmware.runner import run_chain
from firmware.tcd import TCD_BYTES, decode_tcd

from mock_board import MockDemoBoard
from mock_transport import MockTransport, attach_mock_dma


def test_demo_vector_is_psram0_copy_plus_quit():
    mem = build_demo_memory()
    head = decode_tcd(mem.read(0, 0, TCD_BYTES))
    assert head.quit is False
    assert head.dest_ptr == DEMO_DEST
    assert head.src_ptr == DEMO_SRC
    quit_raw = mem.read(0, DEMO_QUIT, TCD_BYTES)
    assert quit_raw[10] & 0x10  # QUIT
    assert mem.read(0, DEMO_SRC, len(DEMO_PATTERN)) == DEMO_PATTERN


def test_demo_main_pass_on_mock(capsys):
    tt = MockDemoBoard(auto_ack_start=True)
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    attach_mock_dma(tt, transport)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    psram = Psram(transport, host=host)
    assert main(host=host, psram=psram) is True
    assert "PASS" in capsys.readouterr().out
    assert transport.mem[0][DEMO_DEST] == DEMO_PATTERN[0]
    resets = [ev for ev in tt.events if ev[0] == "reset"]
    assert resets
    assert resets[0][1] is True
    start_events = [ev for ev in tt.events if ev[0] == "ui" and (ev[1] & 1)]
    reset_true_idx = next(i for i, ev in enumerate(tt.events) if ev[0] == "reset" and ev[1])
    start_idx = next(i for i, ev in enumerate(tt.events) if ev[0] == "ui" and (ev[1] & 1))
    assert reset_true_idx < start_idx


def test_demo_main_mismatch_is_fail(capsys):
    tt = MockDemoBoard(auto_ack_start=True)
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    attach_mock_dma(tt, transport, mismatch=True)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    psram = Psram(transport, host=host)
    assert main(host=host, psram=psram) is False
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "PASS" not in out.split("FAIL")[0] or "FAIL" in out


def test_demo_runner_compare_without_main():
    tt = MockDemoBoard(auto_ack_start=True)
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    attach_mock_dma(tt, transport)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    ok, result, mismatches = run_chain(
        host, Psram(transport, host=host), build_demo_memory()
    )
    assert ok and not mismatches
    assert result.final_memory.read(0, DEMO_DEST, len(DEMO_PATTERN)) == DEMO_PATTERN
    dumped = {
        (0, DEMO_DEST + i): transport.mem[0][DEMO_DEST + i]
        for i in range(len(DEMO_PATTERN))
    }
    expected = {
        (0, DEMO_DEST + i): DEMO_PATTERN[i] for i in range(len(DEMO_PATTERN))
    }
    assert dumped == expected
