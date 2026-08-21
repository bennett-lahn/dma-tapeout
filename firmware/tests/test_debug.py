"""debug dump / peek / poke / decode_chain on a mock QPI transport."""

from firmware.build import add_copy, add_quit, new_image, place_bytes
from firmware.debug import decode_chain, dump, peek, poke
from firmware.psram import Psram
from firmware.runner import install_image

from mock_transport import MockTransport


def test_peek_poke_dump_decode(capsys):
    transport = MockTransport()
    psram = Psram(transport)
    poke(psram, 0, 0x40, b"hi")
    assert peek(psram, 0, 0x40, 2) == b"hi"
    dump(psram, 0, 0x40, 2, width=16)
    out = capsys.readouterr().out
    assert "0x000040" in out
    assert "68 69" in out or "hi" in out

    mem = new_image()
    add_copy(
        mem,
        0,
        0,
        src_ptr=0x100,
        dest_ptr=0x200,
        length=2,
        next_tcd=0x0B,
    )
    add_quit(mem, 0, 0x0B)
    place_bytes(mem, 0, 0x100, b"AB")
    install_image(psram, mem)
    seen = decode_chain(psram)
    assert seen[0][3].quit is False
    assert seen[1][3].quit is True
    assert seen[0][3].transfer_len == 2
