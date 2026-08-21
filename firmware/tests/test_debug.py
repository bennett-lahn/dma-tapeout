"""debug dump / peek / poke / decode_chain on a mock QPI transport."""

import pytest

from firmware.asic import Host, HostError
from firmware.build import add_copy, add_quit, new_image, place_bytes
from firmware.constants import PTR_BIT23
from firmware.debug import decode_chain, dump, peek, poke
from firmware.demo import DEMO_DEST, DEMO_QUIT, DEMO_SRC
from firmware.psram import Psram
from firmware.runner import install_image
from firmware.tcd import Tcd

from mock_board import MockDemoBoard
from mock_transport import MockTransport


def _granted():
    tt = MockDemoBoard()
    host = Host(tt, sleep_us=lambda us: None)
    host.request_bus()
    transport = MockTransport(oe_getter=lambda: int(tt.uio_oe_pico))
    transport.qpi[0] = True
    transport.qpi[1] = True
    psram = Psram(transport, host=host)
    return host, psram, transport, tt


def test_peek_poke_dump_decode(capsys):
    host, psram, transport, tt = _granted()
    poke(host, psram, 0, 0x40, b"hi")
    assert peek(host, psram, 0, 0x40, 2) == b"hi"
    dump(host, psram, 0, 0x40, 2, width=16)
    out = capsys.readouterr().out
    assert "0x000040" in out
    assert "68 69" in out or "hi" in out

    mem = new_image()
    add_copy(
        mem,
        0,
        0,
        src_ptr=DEMO_SRC,
        dest_ptr=DEMO_DEST,
        length=2,
        next_tcd=DEMO_QUIT,
    )
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, b"AB")
    install_image(psram, mem)
    seen = decode_chain(host, psram)
    assert seen[0][3].quit is False
    assert seen[1][3].quit is True
    assert seen[0][3].transfer_len == 2


def test_debug_refuses_without_grant():
    tt = MockDemoBoard()
    host = Host(tt, sleep_us=lambda us: None)
    transport = MockTransport()
    transport.qpi[0] = True
    psram = Psram(transport)
    with pytest.raises(HostError, match="BUS_GNT"):
        peek(host, psram, 0, 0, 1)


def test_decode_next_device_1_and_ptr23_mask(capsys):
    host, psram, transport, tt = _granted()
    from firmware.tcd import encode_tcd

    poke(host, psram, 0, 0, encode_tcd(Tcd(transfer_len=0, next_tcd=0x30 | PTR_BIT23, next_device=1)))
    poke(host, psram, 1, 0x30, encode_tcd(Tcd(quit=True)))
    seen = decode_chain(host, psram)
    assert seen[0][3].next_device == 1
    assert seen[1][0] == 1
    assert seen[1][1] == 0x30
    out = capsys.readouterr().out
    assert "next_masked=0x000030" in out


def test_decode_cycle_detect(capsys):
    host, psram, transport, tt = _granted()
    from firmware.tcd import encode_tcd

    poke(host, psram, 0, 0, encode_tcd(Tcd(transfer_len=0, next_tcd=0, next_device=0)))
    seen = decode_chain(host, psram, max_nodes=8)
    assert capsys.readouterr().out.count("cycle") >= 1
    assert len(seen) == 1
