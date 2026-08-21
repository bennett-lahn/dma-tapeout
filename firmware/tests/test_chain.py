"""firmware/tests/test_chain.py - interpret_chain without importing test/."""

import pytest

from firmware.build import (
    add_copy,
    add_quit,
    link,
    new_image,
    place_bytes,
    place_head_quit,
    place_tcd,
)
from firmware.chain import (
    DATA_READ,
    DATA_WRITE,
    DEFAULT_DMA_BUF_DEPTH,
    FETCH_READ,
    MemoryImage,
    ReferenceLimitError,
    as_transactions,
    commit_prefix,
    format_log,
    interpret_chain,
    transaction,
)
from firmware.constants import DMA_BUF_DEPTH_TAPEOUT, PTR_BIT23
from firmware.tcd import Tcd, encode_tcd


def _copy_image(src=0x100, dest=0x200, length=4, payload=b"ABCD", dest_device=0, next_tcd=0x0B):
    mem = new_image(fill=0)
    add_copy(
        mem,
        0,
        0,
        src_ptr=src,
        dest_ptr=dest,
        length=length,
        dest_device=dest_device,
        next_tcd=next_tcd,
        next_device=0,
    )
    add_quit(mem, 0, next_tcd)
    place_bytes(mem, 0, src, payload)
    return mem


def test_default_dma_buf_depth_is_tapeout_five():
    assert DEFAULT_DMA_BUF_DEPTH == 5
    assert DEFAULT_DMA_BUF_DEPTH == DMA_BUF_DEPTH_TAPEOUT


def test_place_head_quit_empty_run():
    mem = new_image(fill=0)
    place_head_quit(mem)
    result = interpret_chain(mem, dma_buf_depth=5)
    assert result.path == ((0, 0),)
    assert result.descriptors[0].tcd.quit is True
    assert [txn.kind for txn in result.transactions] == [FETCH_READ]


def test_quit_with_nonzero_len_and_ptrs_is_terminator():
    mem = new_image(fill=0)
    place_tcd(
        mem,
        0,
        0,
        Tcd(
            src_ptr=0x100,
            dest_ptr=0x200,
            transfer_len=64,
            next_tcd=0x0B,
            quit=True,
            src_device=1,
            dest_device=1,
            next_device=1,
        ),
    )
    mem.write(0, 0x100, b"\xA5" * 8)
    result = interpret_chain(mem, dma_buf_depth=5)
    kinds = [txn.kind for txn in result.transactions]
    assert kinds == [FETCH_READ]
    assert DATA_READ not in kinds and DATA_WRITE not in kinds
    assert result.final_memory.read(0, 0x200, 1) == b"\x00"


def test_len_zero_follows_next_with_no_data():
    mem = new_image(fill=0)
    place_tcd(mem, 0, 0, Tcd(transfer_len=0, next_tcd=0x20, next_device=0))
    add_quit(mem, 0, 0x20)
    result = interpret_chain(mem, dma_buf_depth=5)
    assert [txn.kind for txn in result.transactions] == [FETCH_READ, FETCH_READ]
    assert result.path == ((0, 0), (0, 0x20))
    assert result.expected_writes == {}


def test_overlap_n1_vs_n5_differ():
    def build():
        mem = new_image(fill=0)
        add_copy(
            mem,
            0,
            0,
            src_ptr=0x100,
            dest_ptr=0x101,
            length=6,
            next_tcd=0x0B,
        )
        add_quit(mem, 0, 0x0B)
        place_bytes(mem, 0, 0x100, b"\x01\x02\x03\x04\x05\x06")
        return mem

    n1 = interpret_chain(build(), dma_buf_depth=1)
    n5 = interpret_chain(build(), dma_buf_depth=5)
    assert n1.final_memory.read(0, 0x100, 7) != n5.final_memory.read(0, 0x100, 7)
    assert n1.final_memory.read(0, 0x100, 7) == b"\x01\x01\x01\x01\x01\x01\x01"
    # N=5: one 5-byte chunk then 1-byte; dest=src+1.
    assert n5.final_memory.read(0, 0x100, 7) == b"\x01\x01\x02\x03\x04\x05\x05"


def test_multi_chunk_copy_at_n5():
    mem = _copy_image(length=8, payload=b"01234567")
    result = interpret_chain(mem, dma_buf_depth=5)
    reads = [txn for txn in result.transactions if txn.kind == DATA_READ]
    assert [txn.length for txn in reads] == [5, 3]
    assert result.final_memory.read(0, 0x200, 8) == b"01234567"


def test_address_zero_as_link_is_cycle_not_stop():
    mem = new_image(fill=0)
    add_copy(mem, 0, 0, src_ptr=0x100, dest_ptr=0x200, length=0, next_tcd=0, next_device=0)
    with pytest.raises(ReferenceLimitError, match="budget"):
        interpret_chain(mem, dma_buf_depth=5, fetch_budget=8)


def test_add_copy_requires_explicit_next():
    mem = new_image()
    with pytest.raises(ValueError, match="next_tcd"):
        add_copy(mem, 0, 0, src_ptr=1, dest_ptr=2, length=1)


def test_dest_device_1_after_link_at_n5():
    mem = new_image(fill=0)
    add_copy(
        mem,
        0,
        0,
        src_ptr=0x100,
        dest_ptr=0x200,
        length=4,
        dest_device=1,
        next_tcd=0,
        next_device=0,
    )
    add_quit(mem, 1, 0x30)
    updated = link(mem, 0, 0, 1, 0x30)
    assert updated.dest_device == 1
    assert updated.next_device == 1
    assert updated.next_tcd == 0x30
    place_bytes(mem, 0, 0x100, b"WXYZ")
    result = interpret_chain(mem, dma_buf_depth=5)
    assert result.path == ((0, 0), (1, 0x30))
    writes = [txn for txn in result.transactions if txn.kind == DATA_WRITE]
    assert writes[0].device == 1
    assert writes[0].length == 4
    assert result.final_memory.read(1, 0x200, 4) == b"WXYZ"


def test_as_transactions_commit_prefix_format_log_used():
    mem = _copy_image()
    result = interpret_chain(mem, dma_buf_depth=5)
    records = as_transactions(result.transactions)
    assert records[0].kind == FETCH_READ
    committed = commit_prefix(mem, result.transactions)
    assert committed.read(0, 0x200, 4) == b"ABCD"
    text = format_log(result.transactions)
    assert "DATA_WRITE" in text
    extra = transaction(0, DATA_WRITE, 0, 0x10, b"\x01")
    assert extra.canonical().startswith("#000")


def test_ptr23_masked_like_interpret():
    mem = new_image(fill=0)
    add_copy(
        mem,
        0,
        0,
        src_ptr=0x100 | PTR_BIT23,
        dest_ptr=0x200 | PTR_BIT23,
        length=2,
        next_tcd=0x0B | PTR_BIT23,
    )
    add_quit(mem, 0, 0x0B)
    place_bytes(mem, 0, 0x100, b"AB")
    result = interpret_chain(mem, dma_buf_depth=5)
    assert result.final_memory.read(0, 0x200, 2) == b"AB"
    assert result.path[-1] == (0, 0x0B)
