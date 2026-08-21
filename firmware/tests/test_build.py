"""build.py helpers plus interpret_chain on a small same-device copy."""

from firmware.build import add_copy, add_quit, link, new_image, place_bytes, place_tcd
from firmware.chain import interpret_chain
from firmware.tcd import Tcd, encode_tcd


def test_add_copy_add_quit_interpret_chain_dest_bytes():
    mem = new_image()
    pattern = b"ABCD"
    src = 0x000100
    dest = 0x000200
    quit_addr = 0x00000B
    add_copy(
        mem,
        tcd_device=0,
        tcd_addr=0,
        src_ptr=src,
        dest_ptr=dest,
        length=len(pattern),
        src_device=0,
        dest_device=0,
        next_tcd=quit_addr,
        next_device=0,
    )
    add_quit(mem, 0, quit_addr)
    place_bytes(mem, 0, src, pattern)

    result = interpret_chain(mem)
    assert result.completed is True
    assert result.final_memory.read(0, dest, len(pattern)) == pattern
    assert result.path == ((0, 0), (0, quit_addr))


def test_link_fills_next_fields():
    mem = new_image()
    place_tcd(mem, 0, 0, Tcd(src_ptr=1, dest_ptr=2, transfer_len=0))
    add_quit(mem, 0, 0x20)
    updated = link(mem, 0, 0, 0, 0x20)
    assert updated.next_tcd == 0x20
    assert updated.next_device == 0
    assert encode_tcd(updated) == mem.read(0, 0, 11)


def test_address_zero_is_not_a_terminator():
    mem = new_image()
    # Empty run: QUIT at the fixed head. Address 0 is occupied by that TCD.
    add_quit(mem, 0, 0)
    result = interpret_chain(mem)
    assert result.path == ((0, 0),)
    assert result.descriptors[0].tcd.quit is True
