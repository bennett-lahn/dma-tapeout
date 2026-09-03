"""Pure-Python unit tests for the legal chain generator.

Checks determinism per ``SEED``, per-dimension independence, and that every
generated chain is legal V1 stimulus that interprets without a reference-model
error (``08-stimulus-and-coverage.md``, Legal chain generator). No cocotb import.
"""

import pytest

from reference.chain import (
    DATA_READ,
    DATA_WRITE,
    FETCH_READ,
    HEAD_ADDRESS,
    HEAD_DEVICE,
    interpret_chain,
    ReferenceLimitError,
)
from reference.generator import (
    ADDR_HIGH,
    DEST_SENTINEL,
    LAYOUT_EQUAL,
    LAYOUT_OVERLAP_BACKWARD,
    LAYOUT_OVERLAP_FORWARD,
    PATTERN_INCREMENT,
    REGION_DESCRIPTOR,
    REGION_DESTINATION,
    REGION_SOURCE,
    STREAM_DEVICES,
    STREAM_NEXT_DEVICE,
    STREAMS,
    ChainGenerator,
    GeneratorError,
    TcdSpec,
    build_directed_chain,
    len_addr_corner_specs,
    multi_chunk_length,
    one_chunk_length,
)
from reference.scoreboard import Scoreboard
from reference.tcd import TCD_BYTES, decode_tcd, validate_tcd


def test_empty_spec_list_puts_quit_at_the_fixed_head():
    """TC-EMPTY stimulus shape."""
    chain = build_directed_chain(())
    assert chain.descriptor_locations == ((HEAD_DEVICE, HEAD_ADDRESS),)
    assert len(chain.tcds) == 1 and chain.tcds[0].quit
    result = chain.interpret()
    assert [txn.kind for txn in result.transactions] == [FETCH_READ]


def test_directed_single_copy_is_legal_and_interprets():
    chain = build_directed_chain([TcdSpec(transfer_len=1)])
    for tcd in chain.tcds:
        validate_tcd(tcd)
    result = chain.interpret()
    assert [txn.kind for txn in result.transactions] == [
        FETCH_READ,
        DATA_READ,
        DATA_WRITE,
        FETCH_READ,
    ]
    head = chain.tcds[0]
    assert result.final_memory.read(head.dest_device, head.dest_ptr, 1) == chain.memory.read(
        head.src_device, head.src_ptr, 1
    )


def test_descriptor_bytes_land_at_their_locations():
    chain = build_directed_chain([TcdSpec(transfer_len=2)])
    for tcd, (device, address) in zip(chain.tcds, chain.descriptor_locations):
        assert decode_tcd(chain.memory.read(device, address, TCD_BYTES)) == tcd


def test_chain_links_follow_next_device_and_next_tcd():
    specs = [
        TcdSpec(transfer_len=1, next_device=1),
        TcdSpec(transfer_len=1, src_device=1, dest_device=1, next_device=0),
        TcdSpec(transfer_len=1, next_device=1),
    ]
    chain = build_directed_chain(specs)
    devices = [device for device, _ in chain.descriptor_locations]
    assert devices == [0, 1, 0, 1]
    for index, tcd in enumerate(chain.executable):
        next_device, next_address = chain.descriptor_locations[index + 1]
        assert (tcd.next_device, tcd.next_tcd) == (next_device, next_address)
    result = chain.interpret()
    assert result.path == chain.descriptor_locations
    assert result.fetch_count == 4


@pytest.mark.parametrize(
    "src_device, dest_device", [(0, 0), (0, 1), (1, 0), (1, 1)]
)
def test_all_four_device_directions_are_buildable(src_device, dest_device):
    chain = build_directed_chain(
        [TcdSpec(transfer_len=3, src_device=src_device, dest_device=dest_device)]
    )
    result = chain.interpret()
    assert {txn.device for txn in result.transactions if txn.kind == DATA_READ} == {src_device}
    assert {txn.device for txn in result.transactions if txn.kind == DATA_WRITE} == {dest_device}


def test_zero_length_spec_produces_no_data_transaction():
    chain = build_directed_chain([TcdSpec(transfer_len=0)])
    result = chain.interpret()
    assert [txn.kind for txn in result.transactions] == [FETCH_READ, FETCH_READ]


def test_explicit_data_and_addresses_are_honored():
    chain = build_directed_chain(
        [TcdSpec(transfer_len=3, src_addr=0x001000, dest_addr=0x002000, data=b"\xDE\xAD\xBE")]
    )
    head = chain.tcds[0]
    assert (head.src_ptr, head.dest_ptr) == (0x001000, 0x002000)
    assert chain.memory.read(0, 0x001000, 3) == b"\xDE\xAD\xBE"
    assert chain.memory.read(0, 0x002000, 3) == bytes([DEST_SENTINEL]) * 3
    assert chain.interpret().final_memory.read(0, 0x002000, 3) == b"\xDE\xAD\xBE"


def test_data_length_must_match_transfer_len():
    with pytest.raises(GeneratorError):
        build_directed_chain([TcdSpec(transfer_len=2, data=b"\x01")])


def test_overlap_layout_keeps_the_source_payload():
    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=4,
                src_addr=0x001000,
                dest_addr=0x001001,
                data=b"\x01\x02\x03\x04",
                layout=LAYOUT_OVERLAP_FORWARD,
            )
        ],
        dma_buf_depth=1,
    )
    assert chain.memory.read(0, 0x001000, 4) == b"\x01\x02\x03\x04"
    result = chain.interpret()
    assert result.final_memory.read(0, 0x001000, 5) == b"\x01\x01\x01\x01\x01"


def test_backward_overlap_layout_keeps_the_source_payload():
    """cov-refu-08: backward overlap on PSRAM0."""
    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=4,
                src_addr=0x001001,
                dest_addr=0x001000,
                data=b"\x01\x02\x03\x04",
                layout=LAYOUT_OVERLAP_BACKWARD,
            )
        ],
        dma_buf_depth=1,
    )
    head = chain.tcds[0]
    assert head.dest_ptr < head.src_ptr
    result = chain.interpret()
    assert result.final_memory.read(0, 0x001000, 4) == b"\x01\x02\x03\x04"


def test_dest_psram1_overlap_is_honored():
    """cov-refu-08: overlapping ranges on destination PSRAM1."""
    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=6,
                src_device=1,
                dest_device=1,
                src_addr=0x002000,
                dest_addr=0x002001,
                data=b"\x10\x11\x12\x13\x14\x15",
                layout=LAYOUT_OVERLAP_FORWARD,
            )
        ],
        dma_buf_depth=5,
        seed=44,
    )
    head = chain.tcds[0]
    assert head.src_device == 1 and head.dest_device == 1
    src_range = set(range(head.src_ptr, head.src_ptr + head.transfer_len))
    dest_range = set(range(head.dest_ptr, head.dest_ptr + head.transfer_len))
    assert src_range & dest_range
    result = chain.interpret(dma_buf_depth=5)
    assert result.final_memory.read(1, head.dest_ptr, 1)


def test_multi_chunk_length_derives_from_compile_n():
    """tb-ref-02: directed length is N+1 so every depth has a multi-chunk case."""
    assert multi_chunk_length(5) == 6
    assert one_chunk_length(5) == 5
    for depth in range(1, 9):
        chain = build_directed_chain(
            [TcdSpec(transfer_len=multi_chunk_length(depth))],
            dma_buf_depth=depth,
            seed=50 + depth,
        )
        result = chain.interpret(dma_buf_depth=depth)
        reads = [txn for txn in result.transactions if txn.kind == DATA_READ]
        assert len(reads) >= 2
        control = build_directed_chain(
            [TcdSpec(transfer_len=one_chunk_length(depth))],
            dma_buf_depth=depth,
            seed=60 + depth,
        )
        control_reads = [
            txn
            for txn in control.interpret(dma_buf_depth=depth).transactions
            if txn.kind == DATA_READ
        ]
        assert len(control_reads) == 1


def test_self_pointing_head_exhausts_fetch_budget():
    """tb-ref-05: directed self-pointing head; legal random stays acyclic."""
    chain = ChainGenerator(3).build_self_pointing_head(TcdSpec(transfer_len=0))
    assert chain.tcds[0].next_tcd == HEAD_ADDRESS
    assert chain.tcds[0].next_device == HEAD_DEVICE
    assert not chain.tcds[0].quit
    with pytest.raises(ReferenceLimitError, match="budget"):
        chain.interpret(fetch_budget=8)
    random_chain = ChainGenerator(3).build_chain()
    locations = random_chain.descriptor_locations
    for tcd, (device, address) in zip(random_chain.executable, locations):
        nxt = (tcd.next_device, tcd.next_tcd)
        assert nxt != (device, address)


def test_quit_descriptor_retains_nonzero_unexecuted_fields():
    """tb-ref-05: nonzero QUIT fields are stored but not executed."""
    chain = build_directed_chain(
        [TcdSpec(transfer_len=1)],
        quit_spec=TcdSpec(
            src_addr=0x001234,
            dest_addr=0x005678,
            transfer_len=0x22,
            src_device=1,
            dest_device=1,
        ),
    )
    quit_tcd = chain.tcds[-1]
    assert quit_tcd.quit
    assert quit_tcd.transfer_len == 0x22
    assert quit_tcd.src_ptr == 0x001234
    assert quit_tcd.dest_device == 1
    result = chain.interpret()
    data_writes = [txn for txn in result.transactions if txn.kind == DATA_WRITE]
    assert all(txn.address != 0x005678 for txn in data_writes)


def test_parameterized_guard_bytes_are_written():
    """tb-ref-04: guard size is a generator parameter, not a hard-coded 2."""
    chain = build_directed_chain([TcdSpec(transfer_len=2)], seed=8, guard_bytes=4)
    assert chain.guards
    assert any(region.length == 4 for region in chain.guards)


def test_guards_and_regions_are_recorded_and_defined():
    chain = build_directed_chain([TcdSpec(transfer_len=2)])
    kinds = {region.kind for region in chain.regions}
    assert {REGION_DESCRIPTOR, REGION_SOURCE, REGION_DESTINATION} <= kinds
    assert chain.guards
    for region in chain.guards:
        for offset in range(region.length):
            assert chain.memory.is_defined(region.device, region.address + offset)


def test_guards_never_overwrite_payload_or_descriptors():
    chain = build_directed_chain(
        [TcdSpec(transfer_len=4, src_addr=0x001000, dest_addr=0x001004, data=b"\x0A\x0B\x0C\x0D")]
    )
    assert chain.memory.read(0, 0x001000, 4) == b"\x0A\x0B\x0C\x0D"
    for tcd, (device, address) in zip(chain.tcds, chain.descriptor_locations):
        assert decode_tcd(chain.memory.read(device, address, TCD_BYTES)) == tcd


def test_generated_chain_feeds_the_scoreboard():
    chain = build_directed_chain([TcdSpec(transfer_len=2), TcdSpec(transfer_len=1, dest_device=1)])
    result = chain.interpret()
    board = Scoreboard.from_result(result, guards=chain.guards, regions=chain.regions)
    board.compare(result.transactions, result.final_memory)


# -- determinism and dimensions -------------------------------------------


def test_same_seed_builds_an_identical_chain():
    first = ChainGenerator(4231).build_chain()
    second = ChainGenerator(4231).build_chain()
    assert first.tcds == second.tcds
    assert first.descriptor_locations == second.descriptor_locations
    assert first.memory.snapshot() == second.memory.snapshot()


def test_different_seeds_differ():
    first = ChainGenerator(1).build_chain()
    second = ChainGenerator(2).build_chain()
    assert (first.tcds, first.descriptor_locations) != (
        second.tcds,
        second.descriptor_locations,
    )


def test_reset_streams_repeats_a_build():
    generator = ChainGenerator(99)
    first = generator.build_chain()
    generator.reset_streams()
    assert generator.build_chain().tcds == first.tcds


@pytest.mark.parametrize("seed", [0, 1, 7, 17, 4231])
def test_random_chains_are_legal_and_interpretable(seed):
    chain = ChainGenerator(seed).build_chain()
    assert chain.tcds[-1].quit
    assert all(not tcd.quit for tcd in chain.tcds[:-1])
    for tcd in chain.tcds:
        validate_tcd(tcd)
    result = interpret_chain(chain.memory, chain.dma_buf_depth)
    assert result.path[0] == (HEAD_DEVICE, HEAD_ADDRESS)
    assert result.fetch_count == len(chain.tcds)


def test_drawing_one_dimension_does_not_perturb_another():
    """Independent child streams: extra length draws leave addresses stable."""
    baseline = ChainGenerator(5)
    perturbed = ChainGenerator(5)
    for _ in range(10):
        perturbed.transfer_length()
    assert [baseline.address_class() for _ in range(5)] == [
        perturbed.address_class() for _ in range(5)
    ]


def test_next_device_stream_is_isolated_from_device_tuple():
    """tb-ref-03 / cov-refu-05: NEXT-device entropy does not share STREAM_DEVICES."""
    assert STREAM_NEXT_DEVICE in STREAMS
    assert STREAM_NEXT_DEVICE != STREAM_DEVICES

    baseline = ChainGenerator(11)
    extra_next = ChainGenerator(11)
    for _ in range(20):
        extra_next.next_device(0)
    assert [baseline.device_tuple() for _ in range(8)] == [
        extra_next.device_tuple() for _ in range(8)
    ]

    extra_tuple = ChainGenerator(11)
    for _ in range(20):
        extra_tuple.device_tuple()
    assert [baseline.next_device(0) for _ in range(8)] == [
        extra_tuple.next_device(0) for _ in range(8)
    ]

    drifted = ChainGenerator(11)
    for _ in range(20):
        drifted.next_device(0)
    assert [drifted.next_device(1) for _ in range(8)] != [
        ChainGenerator(11).next_device(1) for _ in range(8)
    ]


def test_every_declared_stream_exists():
    generator = ChainGenerator(3)
    for name in STREAMS:
        assert generator.stream(name) is not None
    with pytest.raises(GeneratorError):
        generator.stream("nope")


def test_chain_length_stays_inside_the_bias_bounds():
    generator = ChainGenerator(11)
    lengths = {generator.chain_length() for _ in range(40)}
    assert lengths
    assert min(lengths) >= 1 and max(lengths) <= 8


def test_transfer_length_resolves_depth_relative_corners():
    generator = ChainGenerator(13, dma_buf_depth=4)
    lengths = {generator.transfer_length() for _ in range(400)}
    assert lengths <= set(range(256))
    assert 0 in lengths or 1 in lengths
    assert {7, 8, 9} & lengths  # 2N-1 / 2N / 2N+1 at depth 4


def test_len_addr_corner_specs_hit_src_zero_and_highest_next():
    specs = len_addr_corner_specs(5)
    assert [spec.transfer_len for spec in specs] == [9, 10, 11]
    chain = build_directed_chain(specs, seed=99, dma_buf_depth=5)
    head = chain.executable[0]
    assert head.src_device == 1 and head.src_ptr == 0
    assert head.next_tcd >= 0x7FFC00


def test_bias_override_narrows_one_dimension():
    generator = ChainGenerator(
        21,
        bias={
            "chain_length": {"min": 2, "max": 2, "favored": {"min": 1}, "uniform_weight": 0},
            "payload_pattern": {
                "zero": 0,
                "ones": 0,
                "walking": 0,
                "increment": 1,
                "alternating": 0,
                "random": 0,
            },
        },
    )
    assert {generator.chain_length() for _ in range(10)} == {2}
    assert {generator.payload_pattern() for _ in range(10)} == {PATTERN_INCREMENT}


def test_unknown_bias_dimension_is_rejected():
    with pytest.raises(GeneratorError):
        ChainGenerator(0, bias={"not_a_dimension": {}})


def test_payload_patterns_have_the_requested_length():
    generator = ChainGenerator(0)
    for pattern in ("zero", "ones", "walking", "increment", "alternating", "random"):
        assert len(generator.payload(pattern, 5)) == 5
    assert generator.payload("zero", 0) == b""
    with pytest.raises(GeneratorError):
        generator.payload("bogus", 1)


def test_high_address_class_stays_in_range():
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_class=ADDR_HIGH, dest_class=ADDR_HIGH)]
    )
    head = chain.tcds[0]
    assert head.src_ptr + 7 <= 0x7FFFFF
    assert head.dest_ptr + 7 <= 0x7FFFFF
    chain.interpret()


def test_manifest_lists_descriptor_bytes_and_regions():
    chain = build_directed_chain([TcdSpec(transfer_len=2)])
    manifest = chain.manifest()
    assert manifest["head"] == {"device": HEAD_DEVICE, "address": HEAD_ADDRESS}
    assert len(manifest["descriptors"]) == len(chain.tcds)
    assert manifest["descriptors"][-1]["quit"] is True
    assert any(region["kind"] == REGION_SOURCE for region in manifest["regions"])


def test_bad_spec_type_is_rejected():
    with pytest.raises(GeneratorError):
        build_directed_chain(["not-a-spec"])


def test_pinned_next_tcd_address_is_used():
    chain = build_directed_chain([TcdSpec(transfer_len=1, next_tcd_addr=0x000400)])
    assert chain.descriptor_locations[1] == (0, 0x000400)
    assert chain.tcds[0].next_tcd == 0x000400


# -- pre-W4 blocker: generator must not clobber future TCDs ---------------


def _descriptor_byte_addresses(locations):
    return {
        (device, address + offset)
        for device, address in locations
        for offset in range(TCD_BYTES)
    }


@pytest.mark.parametrize("seed", range(50))
def test_random_chains_never_write_into_descriptor_slots(seed):
    """``build_chain`` must not clobber head/link/quit TCDs."""
    chain = ChainGenerator(seed).build_chain()
    result = chain.interpret()
    descriptor_bytes = _descriptor_byte_addresses(chain.descriptor_locations)
    for txn in result.transactions:
        if txn.kind != DATA_WRITE:
            continue
        written = {(txn.device, txn.address + offset) for offset in range(txn.length)}
        assert not written & descriptor_bytes, (
            f"seed={seed}: {txn.canonical()} writes into a descriptor slot"
        )


def test_pinned_dest_onto_a_future_descriptor_raises():
    """A pinned ``dest_addr`` landing on the next descriptor's slot is illegal."""
    with pytest.raises(GeneratorError):
        build_directed_chain(
            [
                TcdSpec(transfer_len=4, dest_addr=0x000400, next_tcd_addr=0x000400),
                TcdSpec(transfer_len=1),
            ]
        )


def test_pinned_dest_away_from_any_descriptor_still_succeeds():
    """The new check must not reject a legal, disjoint pinned destination."""
    chain = build_directed_chain(
        [TcdSpec(transfer_len=4, dest_addr=0x002000, next_tcd_addr=0x000400)]
    )
    assert chain.tcds[0].dest_ptr == 0x002000


def test_equal_layout_falls_back_to_disjoint_when_src_sits_on_a_descriptor():
    """``LAYOUT_EQUAL`` must not reuse a pinned src address that is a descriptor slot."""
    chain = build_directed_chain(
        [TcdSpec(transfer_len=4, src_addr=0x000100, layout=LAYOUT_EQUAL)]
    )
    head = chain.tcds[0]
    quit_device, quit_address = chain.descriptor_locations[-1]
    assert (quit_device, quit_address) == (0, 0x000100)
    assert head.dest_ptr != head.src_ptr
    dest_bytes = {(head.dest_device, head.dest_ptr + offset) for offset in range(4)}
    assert not dest_bytes & _descriptor_byte_addresses(chain.descriptor_locations)
    chain.interpret()


def test_overlap_layout_falls_back_to_disjoint_when_it_would_hit_a_descriptor():
    """``LAYOUT_OVERLAP_FORWARD`` must redirect a shifted dest away from a descriptor."""
    chain = build_directed_chain(
        [TcdSpec(transfer_len=6, src_addr=0x0000F8, layout=LAYOUT_OVERLAP_FORWARD)]
    )
    head = chain.tcds[0]
    # Without the fix, dest = src + shift = 0xFB..0x100 would land on the quit
    # descriptor allocated at the default region start 0x100.
    dest_bytes = {(head.dest_device, head.dest_ptr + offset) for offset in range(6)}
    assert not dest_bytes & _descriptor_byte_addresses(chain.descriptor_locations)
    chain.interpret()


def test_quit_tcd_bytes_survive_interpretation_unchanged():
    """A legal chain's quit descriptor bytes are never touched by any data write."""
    chain = build_directed_chain(
        [TcdSpec(transfer_len=3, pattern=PATTERN_INCREMENT)],
        quit_spec=TcdSpec(src_addr=0x001234, dest_addr=0x005678, transfer_len=0x22),
    )
    quit_device, quit_address = chain.descriptor_locations[-1]
    before = chain.memory.read(quit_device, quit_address, TCD_BYTES)
    result = chain.interpret()
    after = result.final_memory.read(quit_device, quit_address, TCD_BYTES)
    assert before == after


def test_overlap_payload_case_is_not_weakened_when_it_misses_descriptors():
    """TC-OVERLAP-style same-device overlap must still be honored away from TCDs."""
    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=4,
                src_addr=0x001000,
                dest_addr=0x001001,
                data=b"\x01\x02\x03\x04",
                layout=LAYOUT_OVERLAP_FORWARD,
            )
        ],
        dma_buf_depth=1,
    )
    assert chain.tcds[0].dest_ptr == 0x001001
    result = chain.interpret()
    assert result.final_memory.read(0, 0x001000, 5) == b"\x01\x01\x01\x01\x01"
