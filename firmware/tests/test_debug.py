"""debug peek / poke / dump / decode_chain on a granted mock QPI path."""

import pytest

from firmware.board.board import Board
from firmware.constants import PTR_BIT23
from firmware.debug import decode_chain, dump, peek, poke
from firmware.dma import DmaController, DmaError
from firmware.psram import Psram
from firmware.tcd import Tcd, encode_tcd

from mock_board import MockDemoBoard
from mock_transport import MockTransport

QUIT_ADDR = 0x00000B


def _granted():
    tt = MockDemoBoard()
    dma = DmaController(Board(tt, sleep_us=lambda us: None))
    dma.request_bus()
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    transport.qpi[0] = True
    transport.qpi[1] = True
    return dma, Psram(transport, dma=dma), transport, tt


def test_peek_poke_and_dump(capsys):
    dma, psram, _transport, _tt = _granted()
    poke(dma, psram, 0, 0x40, b"hi")
    assert peek(dma, psram, 0, 0x40, 2) == b"hi"
    dump(dma, psram, 0, 0x40, 2, width=16)
    out = capsys.readouterr().out
    assert "0x000040" in out
    assert "68 69" in out


def test_decode_chain_walks_to_quit(capsys):
    dma, psram, _transport, _tt = _granted()
    head = Tcd(src_ptr=0x100, dest_ptr=0x200, transfer_len=2, next_tcd=QUIT_ADDR)
    poke(dma, psram, 0, 0, encode_tcd(head))
    poke(dma, psram, 0, QUIT_ADDR, encode_tcd(Tcd(quit=True)))
    seen = decode_chain(dma, psram)
    assert len(seen) == 2
    assert seen[0][3].quit is False
    assert seen[0][3].transfer_len == 2
    assert seen[1][1] == QUIT_ADDR
    assert seen[1][3].quit is True
    assert "0:0x000000" in capsys.readouterr().out


def test_debug_refuses_without_grant():
    tt = MockDemoBoard()
    dma = DmaController(Board(tt, sleep_us=lambda us: None))
    transport = MockTransport()
    transport.qpi[0] = True
    psram = Psram(transport)
    with pytest.raises(DmaError, match="BUS_GNT"):
        peek(dma, psram, 0, 0, 1)
    with pytest.raises(DmaError, match="BUS_GNT"):
        poke(dma, psram, 0, 0, b"A")
    with pytest.raises(DmaError, match="BUS_GNT"):
        decode_chain(dma, psram)


def test_debug_allowed_while_reset_is_held():
    tt = MockDemoBoard()
    dma = DmaController(Board(tt, sleep_us=lambda us: None))
    dma.reset(True)
    dma.qpi_write_oe()
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    transport.qpi[0] = True
    psram = Psram(transport, dma=dma)
    poke(dma, psram, 0, 0, b"A")
    assert peek(dma, psram, 0, 0, 1) == b"A"


def test_decode_next_device_1_and_ptr23_mask(capsys):
    dma, psram, _transport, _tt = _granted()
    poke(
        dma,
        psram,
        0,
        0,
        encode_tcd(Tcd(transfer_len=0, next_tcd=0x30 | PTR_BIT23, next_device=1)),
    )
    poke(dma, psram, 1, 0x30, encode_tcd(Tcd(quit=True)))
    seen = decode_chain(dma, psram)
    assert seen[0][3].next_device == 1
    assert seen[1][0] == 1
    assert seen[1][1] == 0x30
    assert "next_masked=0x000030" in capsys.readouterr().out


def test_decode_cycle_detect(capsys):
    dma, psram, _transport, _tt = _granted()
    poke(dma, psram, 0, 0, encode_tcd(Tcd(transfer_len=0, next_tcd=0, next_device=0)))
    seen = decode_chain(dma, psram, max_nodes=8)
    assert capsys.readouterr().out.count("cycle") >= 1
    assert len(seen) == 1


def test_decode_prints_validate_error_and_stops(capsys):
    dma, psram, _transport, _tt = _granted()
    # reserved=0x1 in CTRL_FLAGS[3:0] is not legal V1 stimulus; the walk stops.
    poke(dma, psram, 0, 0, bytes(10) + bytes([0x01]))
    seen = decode_chain(dma, psram)
    out = capsys.readouterr().out
    assert "validate:" in out
    assert "reserved=0x1" in out
    assert len(seen) == 1
