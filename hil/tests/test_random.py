"""Seeded random HIL campaign: legal generated chains against a live session.

`ChainGenerator` draws each chain dimension (chain length, device pair,
transfer length, address class, payload pattern, source/destination layout)
from its bias table, so one seed is one whole legal stimulus. `interpret_chain`
is the only oracle: whatever it says a correct DMA must write is what the
destination read-back has to show, byte for byte.

Determinism is the point. `--seed` picks the campaign, `child_random` derives
one independent child seed per chain, and every failure prints the REPRO line
that replays exactly that chain plus an oracle log window around the first bad
byte. The default `--target=loopback` needs no demoboard.

`test_hw_random_chain` is the only hardware/DUT test in this file (it takes
the `session` fixture, hence the `test_hw_` prefix and auto-applied `hw`
marker); `test_child_seeds_are_distinct_and_deterministic` and
`test_random_chain_is_reproducible` are pure host self-tests of the generator
and never touch `--target`.
"""

from __future__ import annotations

import pytest

from hil.checks import (
    check_descriptors_intact,
    check_dest_writes,
    check_guard_bytes,
    check_head_tcd_installed,
    check_host_status,
    collapse_extents,
    contiguous_spans,
    format_repro_banner,
    format_windowed_mismatch,
)
from test.common.seeds import child_random
from test.reference.chain import DEFAULT_DMA_BUF_DEPTH
from test.reference.generator import ChainGenerator

# Chains per campaign. Each is a distinct seed, so raising this widens coverage
# without changing any existing chain.
CHAIN_COUNT = 5

# Loopback finishes synchronously; the timeout only matters on real targets.
TIMEOUT_MS = 5000


def child_seed(base_seed: int, index: int) -> int:
    """Derive chain *index*'s seed from the campaign seed, independently.

    Each index gets its own named child stream, so adding a chain never shifts
    the seed any earlier chain already used.
    """
    stream = child_random(int(base_seed), "hil-random-chain-%d" % int(index))
    return stream.randrange(1 << 31)


def poison_destinations(session, expected_writes, read_addresses) -> int:
    """Write `~expected` over every destination byte the chain never reads.

    Poisoning is what stops a DMA that moved nothing from passing: the
    destination cannot already hold the expected value. Bytes the oracle reads
    (descriptor slots, sources, and the source half of an overlapping or equal
    layout) are left pristine, so the expectations stay valid.

    Returns:
        int: bytes poisoned.
    """
    poison = {
        key: (~value) & 0xFF
        for key, value in expected_writes.items()
        if key not in read_addresses
    }
    if not poison:
        return 0
    spans = [
        (
            device,
            address,
            bytes(poison[(device, address + offset)] for offset in range(length)),
        )
        for device, address, length in collapse_extents(poison)
    ]
    return session.write_spans(spans)


@pytest.mark.random
@pytest.mark.parametrize("index", range(CHAIN_COUNT))
def test_hw_random_chain(session, request, detail, index):
    """One generated chain against the live target: install, poison, START,
    read back, check. Takes the `session` fixture, so this is a hardware/DUT
    test (auto-tagged `hw`); the two campaign self-tests below take no
    fixture and never touch `--target`.
    """
    base_seed = int(request.config.getoption("--seed"))
    seed = child_seed(base_seed, index)
    target = request.config.getoption("--target")
    case_id = "random-%d" % index
    banner = format_repro_banner(case_id, base_seed, target, request.node.nodeid)

    chain = ChainGenerator(seed=seed, dma_buf_depth=DEFAULT_DMA_BUF_DEPTH).build_chain()
    result = chain.interpret()
    expected = dict(result.expected_writes)
    extents = collapse_extents(expected)

    spans = contiguous_spans(chain.memory)
    session.write_spans(spans)
    poison_destinations(session, expected, result.read_addresses)
    check_head_tcd_installed(chain, session)
    session.start_and_wait(timeout_ms=TIMEOUT_MS)
    dumped = session.read_spans(extents) if extents else {}
    status = session.get_status()

    guard = None
    try:
        # `finally` so a failing chain still prints its detail block first.
        try:
            check_dest_writes(expected, dumped, label="%s dest writes" % case_id)
            guard = check_guard_bytes(chain.memory, extents, session)
            check_descriptors_intact(chain, session)
            check_host_status(status)
        finally:
            detail.report_case(
                case_id,
                markers=("random",),
                memory=chain.memory,
                result=result,
                expected=expected,
                dumped=dumped,
                spans=spans,
                status=status,
                guard=guard,
                notes=("child seed %d of campaign seed %d" % (seed, base_seed),),
            )
    except AssertionError as error:
        raise AssertionError(
            "\n".join(
                [
                    "%s: %s" % (case_id, error),
                    chain.describe(),
                    format_windowed_mismatch(result, dumped),
                    banner,
                ]
            )
        ) from error


@pytest.mark.random
def test_selftest_child_seeds_are_distinct_and_deterministic():
    """The campaign must be replayable and must not repeat one chain."""
    seeds = [child_seed(0, index) for index in range(CHAIN_COUNT)]
    assert len(set(seeds)) == CHAIN_COUNT
    assert seeds == [child_seed(0, index) for index in range(CHAIN_COUNT)]
    assert child_seed(1, 0) != child_seed(0, 0)


@pytest.mark.random
@pytest.mark.parametrize("index", range(CHAIN_COUNT))
def test_selftest_random_chain_is_reproducible(index):
    """The same seed must rebuild a byte-identical chain (no hardware needed)."""
    seed = child_seed(0, index)
    depth = DEFAULT_DMA_BUF_DEPTH
    first = ChainGenerator(seed=seed, dma_buf_depth=depth).build_chain()
    second = ChainGenerator(seed=seed, dma_buf_depth=depth).build_chain()
    assert first.tcds == second.tcds
    assert first.descriptor_locations == second.descriptor_locations
    assert first.memory.snapshot() == second.memory.snapshot()
