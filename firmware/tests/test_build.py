"""build.py helpers plus interpret_chain dest bytes and chunking at N=5."""

import pytest

from firmware.build import add_copy, add_quit, link, new_image, place_bytes, place_head_quit, place_tcd
from firmware.chain import DATA_READ, DATA_WRITE, ReferenceLimitError, interpret_chain
from firmware.demo import DEMO_DEST, DEMO_QUIT, DEMO_SRC
from firmware.tcd import TCD_BYTES, Tcd, encode_tcd


def test_add_copy_add_quit_interpret_chain_dest_bytes_n5():
    mem = new_image()
    pattern = b"ABCD"
    add_copy(
        mem,
        tcd_device=0,
        tcd_addr=0,
        src_ptr=DEMO_SRC,
        dest_ptr=DEMO_DEST,
        length=len(pattern),
        src_device=0,
        dest_device=0,
        next_tcd=DEMO_QUIT,
        next_device=0,
    )
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, pattern)

    result = interpret_chain(mem, dma_buf_depth=5)
    assert result.completed is True
    assert result.final_memory.read(0, DEMO_DEST, len(pattern)) == pattern
    assert result.path == ((0, 0), (0, DEMO_QUIT))
    reads = [txn for txn in result.transactions if txn.kind == DATA_READ]
    writes = [txn for txn in result.transactions if txn.kind == DATA_WRITE]
    assert [txn.length for txn in reads] == [4]
    assert [txn.length for txn in writes] == [4]


def test_build_chunking_n1_explicit():
    mem = new_image()
    pattern = b"ABCD"
    add_copy(
        mem,
        0,
        0,
        src_ptr=DEMO_SRC,
        dest_ptr=DEMO_DEST,
        length=len(pattern),
        next_tcd=DEMO_QUIT,
    )
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, pattern)
    result = interpret_chain(mem, dma_buf_depth=1)
    reads = [txn for txn in result.transactions if txn.kind == DATA_READ]
    assert [txn.length for txn in reads] == [1, 1, 1, 1]


def test_link_fills_next_fields():
    mem = new_image()
    place_tcd(mem, 0, 0, Tcd(src_ptr=1, dest_ptr=2, transfer_len=0, next_tcd=0x20))
    add_quit(mem, 0, 0x20)
    updated = link(mem, 0, 0, 0, 0x20)
    assert updated.next_tcd == 0x20
    assert updated.next_device == 0
    assert encode_tcd(updated) == mem.read(0, 0, TCD_BYTES)


def test_address_zero_is_not_a_terminator():
    mem = new_image()
    add_copy(
        mem,
        0,
        0,
        src_ptr=0x100,
        dest_ptr=0x200,
        length=0,
        next_tcd=0,
        next_device=0,
    )
    with pytest.raises(ReferenceLimitError, match="budget"):
        interpret_chain(mem, dma_buf_depth=5, fetch_budget=8)


def test_place_head_quit_at_address_zero():
    mem = new_image()
    place_head_quit(mem)
    result = interpret_chain(mem, dma_buf_depth=5)
    assert result.path == ((0, 0),)
    assert result.descriptors[0].tcd.quit is True
    assert result.completed is True


def test_add_copy_without_next_raises():
    with pytest.raises(ValueError, match="next_tcd"):
        add_copy(new_image(), 0, 0, src_ptr=0, dest_ptr=1, length=1)
