"""Pure-Python unit tests for the memory image API and golden chain interpreter.

Oracle-side coverage for ``TC-SAME-0``, ``TC-SAME-1``, ``TC-CROSS-01``,
``TC-CROSS-10``, ``TC-CHAIN``, ``TC-NEXT-DEVICE``, ``TC-LEN-CORNERS``,
``TC-QUIT``, ``TC-EMPTY``, ``TC-ADDR-WIDE``, and ``TC-OVERLAP`` semantics, plus
the budget and bounds rules from ``05-reference-model.md``.

No cocotb import: these run under pytest without a simulator.
"""

import pytest

from reference.chain import (
    ADDR_MAX,
    DATA_READ,
    DATA_WRITE,
    FETCH_READ,
    HEAD_ADDRESS,
    HEAD_DEVICE,
    OPCODE_READ,
    OPCODE_WRITE,
    MemoryImage,
    MemoryRangeError,
    MemoryUndefinedError,
    ReferenceLimitError,
    Transaction,
    commit_prefix,
    interpret_chain,
    memory_from_snapshot,
    transaction,
)
from common.constants import DST_ADDR, QUIT_ADDR, SRC_ADDR
from reference.tcd import TCD_BYTES, Tcd, TcdError, encode_tcd


def image(fill: int = 0x00) -> MemoryImage:
    return MemoryImage(fill=fill)


def place(memory: MemoryImage, device: int, address: int, tcd: Tcd) -> None:
    memory.write(device, address, encode_tcd(tcd))


def kinds(result) -> list:
    return [txn.kind for txn in result.transactions]


def positions(result, kind) -> list:
    return [(txn.device, txn.address, txn.length) for txn in result.transactions if txn.kind == kind]


# -- memory image ----------------------------------------------------------


def test_memory_read_write_round_trip():
    memory = image()
    memory.write(1, 0x1234, b"\xDE\xAD\xBE\xEF")
    assert memory.read(1, 0x1234, 4) == b"\xDE\xAD\xBE\xEF"
    assert memory.read(1, 0x1235, 2) == b"\xAD\xBE"


def test_memory_clone_is_independent():
    memory = image()
    memory.write(0, 0x10, b"\x01\x02")
    clone = memory.clone()
    clone.write(0, 0x10, b"\xFF\xFF")
    assert memory.read(0, 0x10, 2) == b"\x01\x02"
    assert clone.read(0, 0x10, 2) == b"\xFF\xFF"


def test_undefined_byte_is_a_reference_error_without_fill():
    memory = MemoryImage()
    with pytest.raises(MemoryUndefinedError):
        memory.read(0, 0x40, 1)


def test_explicit_fill_defines_unwritten_bytes():
    memory = MemoryImage(fill=0xEE)
    assert memory.read(0, 0x40, 2) == b"\xEE\xEE"
    assert memory.fill == 0xEE


@pytest.mark.parametrize(
    "device, address, length",
    [
        (0, ADDR_MAX, 2),
        (1, ADDR_MAX - 9, TCD_BYTES),
        (0, ADDR_MAX + 1, 1),
        (0, -1, 1),
        (2, 0x00, 1),
    ],
)
def test_memory_rejects_out_of_range_access(device, address, length):
    memory = image()
    with pytest.raises(MemoryRangeError):
        memory.read(device, address, length)


def test_highest_legal_complete_range_is_allowed():
    memory = image()
    memory.write(0, ADDR_MAX - 10, bytes(range(11)))
    assert memory.read(0, ADDR_MAX - 10, 11) == bytes(range(11))


def test_snapshot_round_trip():
    memory = image()
    memory.write(0, 0x20, b"\x11")
    memory.write(1, 0x30, b"\x22")
    rebuilt = memory_from_snapshot(memory.snapshot(), fill=0x00)
    assert rebuilt.read(0, 0x20, 1) == b"\x11"
    assert rebuilt.read(1, 0x30, 1) == b"\x22"


# -- fixed head, quit, empty ----------------------------------------------


def test_head_is_always_psram0_address_zero():
    memory = image()
    place(memory, HEAD_DEVICE, HEAD_ADDRESS, Tcd(quit=True))
    # A legal-looking descriptor elsewhere must not be reachable.
    place(memory, 1, HEAD_ADDRESS, Tcd(src_ptr=SRC_ADDR, transfer_len=8))
    result = interpret_chain(memory)
    assert kinds(result) == [FETCH_READ]
    assert result.path == ((HEAD_DEVICE, HEAD_ADDRESS),)


def test_empty_chain_is_one_fetch_and_no_data():
    """TC-EMPTY: quit descriptor at the fixed head."""
    memory = image()
    place(memory, 0, 0x000000, Tcd(quit=True))
    result = interpret_chain(memory)
    assert len(result.transactions) == 1
    assert result.transactions[0].length == TCD_BYTES
    assert result.transactions[0].opcode == OPCODE_READ
    assert result.fetch_count == 1


def test_quit_outranks_transfer_len_and_next():
    """TC-QUIT: quit descriptor with nonzero pointers is fetched, not executed."""
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(
            src_ptr=SRC_ADDR,
            dest_ptr=DST_ADDR,
            transfer_len=64,
            next_tcd=QUIT_ADDR,
            quit=True,
            src_device=1,
            dest_device=1,
            next_device=1,
        ),
    )
    memory.write(0, SRC_ADDR, b"\xA5" * 64)
    result = interpret_chain(memory)
    assert kinds(result) == [FETCH_READ]
    assert result.final_memory.read(0, DST_ADDR, 1) == b"\x00"


# -- data transfers --------------------------------------------------------


def test_same_device_single_byte_copy():
    """TC-SAME-0 / TC-SMOKE ordering: fetch, read, write, fetch."""
    memory = image()
    place(memory, 0, 0x000000, Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=1, next_tcd=QUIT_ADDR))
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\xA5")
    result = interpret_chain(memory)

    assert kinds(result) == [FETCH_READ, DATA_READ, DATA_WRITE, FETCH_READ]
    assert [txn.index for txn in result.transactions] == [0, 1, 2, 3]
    assert [txn.opcode for txn in result.transactions] == [
        OPCODE_READ,
        OPCODE_READ,
        OPCODE_WRITE,
        OPCODE_READ,
    ]
    assert result.final_memory.read(0, DST_ADDR, 1) == b"\xA5"
    assert result.expected_writes == {(0, DST_ADDR): 0xA5}


def test_same_device_one_copy_on_psram1():
    """TC-SAME-1: head fetch on PSRAM0, data traffic on PSRAM1."""
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(
            src_ptr=SRC_ADDR,
            dest_ptr=DST_ADDR,
            transfer_len=2,
            next_tcd=QUIT_ADDR,
            src_device=1,
            dest_device=1,
        ),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(1, SRC_ADDR, b"\x11\x22")
    result = interpret_chain(memory)

    assert positions(result, DATA_READ) == [(1, SRC_ADDR, 1), (1, SRC_ADDR + 1, 1)]
    assert positions(result, DATA_WRITE) == [(1, DST_ADDR, 1), (1, DST_ADDR + 1, 1)]
    assert result.final_memory.read(1, DST_ADDR, 2) == b"\x11\x22"
    assert result.final_memory.read(0, DST_ADDR, 2) == b"\x00\x00"


@pytest.mark.parametrize("src_device, dest_device", [(0, 1), (1, 0)])
def test_cross_device_copies_select_different_devices(src_device, dest_device):
    """TC-CROSS-01 / TC-CROSS-10."""
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(
            src_ptr=SRC_ADDR,
            dest_ptr=DST_ADDR,
            transfer_len=3,
            next_tcd=QUIT_ADDR,
            src_device=src_device,
            dest_device=dest_device,
        ),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(src_device, SRC_ADDR, b"\x01\x02\x03")
    result = interpret_chain(memory)

    assert {txn.device for txn in result.transactions if txn.kind == DATA_READ} == {src_device}
    assert {txn.device for txn in result.transactions if txn.kind == DATA_WRITE} == {dest_device}
    assert result.final_memory.read(dest_device, DST_ADDR, 3) == b"\x01\x02\x03"


def test_zero_length_emits_no_data_and_follows_next():
    """TC-LEN-CORNERS zero bin: no data transaction, immediate next fetch."""
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=0, next_tcd=QUIT_ADDR, next_device=1),
    )
    place(memory, 1, QUIT_ADDR, Tcd(quit=True))
    result = interpret_chain(memory)

    assert kinds(result) == [FETCH_READ, FETCH_READ]
    assert result.path == ((0, 0x000000), (1, QUIT_ADDR))
    assert result.expected_writes == {}


@pytest.mark.parametrize(
    "depth, length, expected_chunks",
    [
        (1, 1, [1]),
        (1, 3, [1, 1, 1]),
        (2, 5, [2, 2, 1]),
        (4, 4, [4]),
        (4, 6, [4, 2]),
        (8, 255, [8] * 31 + [7]),
    ],
)
def test_chunking_follows_min_depth_remaining(depth, length, expected_chunks):
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=length, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, bytes((index + 1) & 0xFF for index in range(length)))
    result = interpret_chain(memory, depth)

    reads = [txn for txn in result.transactions if txn.kind == DATA_READ]
    writes = [txn for txn in result.transactions if txn.kind == DATA_WRITE]
    assert [txn.length for txn in reads] == expected_chunks
    assert [txn.length for txn in writes] == expected_chunks
    offsets = [0]
    for chunk in expected_chunks[:-1]:
        offsets.append(offsets[-1] + chunk)
    assert [txn.address for txn in reads] == [SRC_ADDR + offset for offset in offsets]
    assert [txn.address for txn in writes] == [DST_ADDR + offset for offset in offsets]
    assert result.final_memory.read(0, DST_ADDR, length) == memory.read(0, SRC_ADDR, length)


def test_read_and_write_alternate_strictly():
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=4, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\x0A\x0B\x0C\x0D")
    result = interpret_chain(memory)
    assert kinds(result) == [FETCH_READ] + [DATA_READ, DATA_WRITE] * 4 + [FETCH_READ]


# -- chaining --------------------------------------------------------------


def test_three_descriptor_chain_executes_in_order():
    """TC-CHAIN with non-contiguous buffers."""
    memory = image()
    slots = [0x000000, 0x000040, 0x000080, 0x0000C0]
    sources = [0x001000, 0x002000, 0x003000]
    dests = [0x011000, 0x012000, 0x013000]
    payloads = [b"\xA0\xA1", b"\xB0", b"\xC0\xC1\xC2"]
    for index, payload in enumerate(payloads):
        place(
            memory,
            0,
            slots[index],
            Tcd(
                src_ptr=sources[index],
                dest_ptr=dests[index],
                transfer_len=len(payload),
                next_tcd=slots[index + 1],
            ),
        )
        memory.write(0, sources[index], payload)
    place(memory, 0, slots[3], Tcd(quit=True))

    result = interpret_chain(memory)
    assert result.fetch_count == 4
    assert result.path == tuple((0, slot) for slot in slots)
    assert positions(result, FETCH_READ) == [(0, slot, TCD_BYTES) for slot in slots]
    for index, payload in enumerate(payloads):
        assert result.final_memory.read(0, dests[index], len(payload)) == payload


def test_next_device_alternates_and_address_zero_is_a_valid_link():
    """TC-NEXT-DEVICE: each fetch uses NEXT_DEVICE; address zero is not null."""
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(transfer_len=0, next_tcd=0x000000, next_device=1),
    )
    place(
        memory,
        1,
        0x000000,
        Tcd(transfer_len=0, next_tcd=0x000030, next_device=0),
    )
    place(memory, 0, 0x000030, Tcd(quit=True))

    result = interpret_chain(memory)
    assert result.path == ((0, 0x000000), (1, 0x000000), (0, 0x000030))
    assert positions(result, FETCH_READ) == [
        (0, 0x000000, TCD_BYTES),
        (1, 0x000000, TCD_BYTES),
        (0, 0x000030, TCD_BYTES),
    ]


def test_later_fetch_reads_current_memory():
    """A preceding copy may rewrite a descriptor that has not been fetched yet."""
    memory = image()
    second_slot = 0x000040
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=second_slot, transfer_len=TCD_BYTES, next_tcd=second_slot),
    )
    # Descriptor 2 currently copies 8 bytes; the first copy turns it into a quit.
    place(memory, 0, second_slot, Tcd(src_ptr=0x004000, dest_ptr=0x005000, transfer_len=8, next_tcd=0x000080))
    memory.write(0, SRC_ADDR, encode_tcd(Tcd(quit=True)))
    memory.write(0, 0x004000, b"\x77" * 8)

    result = interpret_chain(memory)
    assert result.descriptors[1].tcd.quit is True
    assert result.final_memory.read(0, 0x005000, 1) == b"\x00"
    assert positions(result, DATA_WRITE) == [
        (0, second_slot + offset, 1) for offset in range(TCD_BYTES)
    ]


# -- overlap ---------------------------------------------------------------


def test_forward_overlap_follows_sequential_chunk_behavior():
    """TC-OVERLAP: read-then-write per chunk, not a whole-transfer memmove."""
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=SRC_ADDR + 1, transfer_len=4, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\x01\x02\x03\x04\x05")
    result = interpret_chain(memory, 1)
    # Depth 1 propagates the first byte forward; memmove would give 01 02 03 04.
    assert result.final_memory.read(0, SRC_ADDR, 5) == b"\x01\x01\x01\x01\x01"


def test_backward_overlap_shifts_data_down():
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR + 1, dest_ptr=SRC_ADDR, transfer_len=4, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\x01\x02\x03\x04\x05")
    result = interpret_chain(memory, 1)
    assert result.final_memory.read(0, SRC_ADDR, 5) == b"\x02\x03\x04\x05\x05"


def test_chunk_reads_all_bytes_before_its_own_write():
    """At depth 2 each chunk reads both bytes before its own write mutates memory.

    Chunk 1 reads ``01 02`` and writes it to ``0x101``; chunk 2 then reads the
    already-modified ``0x102..0x103`` (``02 04``). Chunk boundaries are
    architecturally visible, so this is not a memmove.
    """
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=SRC_ADDR + 1, transfer_len=4, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\x01\x02\x03\x04\x05")
    result = interpret_chain(memory, 2)
    assert result.final_memory.read(0, SRC_ADDR, 5) == b"\x01\x01\x02\x02\x04"


def test_equal_source_and_destination_is_a_no_op_in_value():
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=SRC_ADDR, transfer_len=3, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\x09\x08\x07")
    result = interpret_chain(memory)
    assert result.final_memory.read(0, SRC_ADDR, 3) == b"\x09\x08\x07"
    assert len(positions(result, DATA_WRITE)) == 3


# -- wide addresses --------------------------------------------------------


def test_highest_legal_complete_range_transfers():
    """TC-ADDR-WIDE: a complete range ending exactly at 0x7FFFFF."""
    memory = image()
    top = ADDR_MAX - 3
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=top, dest_ptr=0x010000, transfer_len=4, next_tcd=QUIT_ADDR, dest_device=1),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, top, b"\xF1\xF2\xF3\xF4")
    result = interpret_chain(memory)
    assert result.final_memory.read(1, 0x010000, 4) == b"\xF1\xF2\xF3\xF4"


def test_data_access_past_top_of_memory_is_a_reference_error():
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=ADDR_MAX, dest_ptr=0x010000, transfer_len=2, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    with pytest.raises(MemoryRangeError):
        interpret_chain(memory, 2)


def test_descriptor_fetch_past_top_of_memory_is_a_reference_error():
    memory = image()
    place(memory, 0, 0x000000, Tcd(transfer_len=0, next_tcd=ADDR_MAX))
    with pytest.raises(MemoryRangeError):
        interpret_chain(memory)


# -- reference errors ------------------------------------------------------


def test_self_pointing_chain_exhausts_the_fetch_budget():
    memory = image()
    place(memory, 0, 0x000000, Tcd(transfer_len=0, next_tcd=0x000000))
    with pytest.raises(ReferenceLimitError) as error:
        interpret_chain(memory, 1, 8)
    assert "budget" in str(error.value)
    assert "0:0x000000" in str(error.value)


def test_pointer_bit23_is_masked_for_memory_access():
    """D35: ptr[23] don't-care; oracle uses A[22:0] for fetches and copies."""
    from reference.tcd import PTR_BIT23

    memory = image()
    src = SRC_ADDR | PTR_BIT23
    dest = DST_ADDR | PTR_BIT23
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=src, dest_ptr=dest, transfer_len=4, next_tcd=QUIT_ADDR | PTR_BIT23),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\x11\x22\x33\x44")
    result = interpret_chain(memory, 1)
    assert result.final_memory.read(0, DST_ADDR, 4) == b"\x11\x22\x33\x44"
    data_reads = [txn for txn in result.transactions if txn.kind == "DATA_READ"]
    assert data_reads[0].address == SRC_ADDR
    assert result.path[-1] == (0, QUIT_ADDR)


def test_transaction_budget_exhaustion_is_reported():
    memory = image()
    place(
        memory,
        0,
        0x000000,
        Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=255, next_tcd=QUIT_ADDR),
    )
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    with pytest.raises(ReferenceLimitError):
        interpret_chain(memory, 1, 64, 16)


def test_invalid_descriptor_stops_before_data_transactions():
    memory = image()
    raw = bytearray(
        encode_tcd(Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=4, next_tcd=QUIT_ADDR))
    )
    raw[10] |= 0x01  # nonzero reserved: representable, not legal V1 stimulus
    memory.write(0, 0x000000, bytes(raw))
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    with pytest.raises(TcdError) as error:
        interpret_chain(memory)
    assert "reserved" in str(error.value)
    assert "path=" in str(error.value)


def test_undefined_source_byte_is_a_reference_error():
    memory = MemoryImage()
    place(memory, 0, 0x000000, Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=1, next_tcd=QUIT_ADDR))
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    with pytest.raises(MemoryUndefinedError):
        interpret_chain(memory)


@pytest.mark.parametrize("depth", [0, -1, True, 1.0])
def test_depth_must_be_a_positive_int(depth):
    memory = image()
    place(memory, 0, 0x000000, Tcd(quit=True))
    with pytest.raises(Exception):
        interpret_chain(memory, depth)


def test_interpreter_does_not_mutate_the_input_image():
    memory = image()
    place(memory, 0, 0x000000, Tcd(src_ptr=SRC_ADDR, dest_ptr=DST_ADDR, transfer_len=1, next_tcd=QUIT_ADDR))
    place(memory, 0, QUIT_ADDR, Tcd(quit=True))
    memory.write(0, SRC_ADDR, b"\x5A")
    before = memory.snapshot()
    first = interpret_chain(memory)
    second = interpret_chain(memory)
    assert memory.snapshot() == before
    assert first.transactions == second.transactions
    assert first.final_memory.read(0, DST_ADDR, 1) == b"\x5A"
    assert first.initial_memory.read(0, DST_ADDR, 1) == b"\x00"


# -- record shape ----------------------------------------------------------


def test_canonical_record_form_matches_the_spec_sample():
    record = transaction(4, DATA_WRITE, 1, 0x234568, b"\xA5")
    assert record.canonical() == "#004 DATA_WRITE op=02 dev=1 addr=0x234568 len=1 data=A5"


def test_metadata_is_excluded_from_semantic_equality():
    first = transaction(0, DATA_READ, 0, 0x100, b"\x01", start_time_ns=5.0)
    second = transaction(0, DATA_READ, 0, 0x100, b"\x01", start_time_ns=99.0, meta={"raw": "ab"})
    assert first == second
    assert first.differences(second) == []


def test_differences_names_every_changed_field():
    first = transaction(0, DATA_READ, 0, 0x100, b"\x01")
    second = transaction(0, DATA_WRITE, 1, 0x101, b"\x02")
    changed = {name for name, _, _ in first.differences(second)}
    assert changed == {"kind", "opcode", "device", "address", "data"}


def test_commit_prefix_replays_only_completed_writes():
    initial = image()
    initial.write(0, DST_ADDR, b"\x00\x00")
    log = [
        transaction(0, FETCH_READ, 0, 0x000000, bytes(TCD_BYTES)),
        transaction(1, DATA_READ, 0, SRC_ADDR, b"\x11"),
        transaction(2, DATA_WRITE, 0, DST_ADDR, b"\x11"),
        transaction(3, DATA_READ, 0, SRC_ADDR + 1, b"\x22"),
        transaction(4, DATA_WRITE, 0, DST_ADDR + 1, b"\x22"),
    ]
    assert commit_prefix(initial, log, 3).read(0, DST_ADDR, 2) == b"\x11\x00"
    assert commit_prefix(initial, log, 5).read(0, DST_ADDR, 2) == b"\x11\x22"
    assert commit_prefix(initial, log, 0).read(0, DST_ADDR, 2) == b"\x00\x00"


def test_transaction_records_are_hashable_values():
    record = transaction(0, DATA_WRITE, 0, 0x10, b"\x01")
    assert isinstance(record, Transaction)
    assert len({record, transaction(0, DATA_WRITE, 0, 0x10, b"\x01")}) == 1
