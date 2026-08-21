"""demo.py builds the one canned PSRAM0 copy via build.py and the runner matches."""

from firmware.asic import Host
from firmware.demo import DEMO_DEST, DEMO_PATTERN, DEMO_QUIT, DEMO_SRC, build_demo_memory, main
from firmware.psram import Psram
from firmware.runner import run_chain
from firmware.tcd import TCD_BYTES

from mock_board import MockDemoBoard
from mock_transport import MockTransport, attach_mock_dma


def test_demo_vector_is_psram0_copy_plus_quit():
    mem = build_demo_memory()
    head = mem.read(0, 0, TCD_BYTES)
    assert head[10] & 0x10 == 0  # not QUIT at head
    quit_raw = mem.read(0, DEMO_QUIT, TCD_BYTES)
    assert quit_raw[10] & 0x10  # QUIT
    assert mem.read(0, DEMO_SRC, len(DEMO_PATTERN)) == DEMO_PATTERN


def test_demo_main_pass_on_mock(capsys):
    tt = MockDemoBoard(auto_ack_start=True)
    transport = MockTransport()
    attach_mock_dma(tt, transport)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    psram = Psram(transport)
    assert main(host=host, psram=psram) is True
    assert "PASS" in capsys.readouterr().out
    assert transport.mem[0][DEMO_DEST] == DEMO_PATTERN[0]


def test_demo_runner_compare_without_main():
    tt = MockDemoBoard(auto_ack_start=True)
    transport = MockTransport()
    attach_mock_dma(tt, transport)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    ok, result, mismatches = run_chain(host, Psram(transport), build_demo_memory())
    assert ok and not mismatches
    assert result.final_memory.read(0, DEMO_DEST, len(DEMO_PATTERN)) == DEMO_PATTERN
