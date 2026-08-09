"""L1 directed DMA and descriptor semantic tests.

Every case is DUT-master: a legal chain is backdoor-installed into PSRAM via
:func:`common.bringup.bring_up_top` models, one accepted START pulse (D24:
:func:`common.host.pulse_start`) hands control to the ASIC, and the DMA itself
fetches descriptors and moves payload over QPI. The dual-axis scoreboard
(``05-reference-model.md``) then compares the pin-decoded ordered transaction
log and the backdoor-read final memory against :mod:`reference.chain`'s golden
interpretation, and :func:`common.dispose.dispose_run` requires every
applicable always-on ``CHK-*`` to come back clean.

Test-case IDs:
    TC-TCD-BE
    TC-SAME-0
    TC-SAME-1
    TC-CROSS-01
    TC-CROSS-10
    TC-CHAIN
    TC-NEXT-DEVICE
    TC-LEN-CORNERS
    TC-QUIT
    TC-EMPTY
    TC-RESTART
    TC-ADDR-WIDE
    TC-OVERLAP
    TC-DEPTH (skipped for M5 / harness depth)
"""

import cocotb

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.directed import run_directed_window as _run_directed_window
from reference.chain import DATA_READ, DATA_WRITE, FETCH_READ, HEAD_ADDRESS, HEAD_DEVICE
from reference.generator import (
    ADDR_BOUNDARY_64K,
    ADDR_HIGH,
    ADDR_LOW,
    LAYOUT_OVERLAP_BACKWARD,
    LAYOUT_OVERLAP_FORWARD,
    PATTERN_INCREMENT,
    TcdSpec,
    build_directed_chain,
)
from reference.tcd import (
    PTR_BIT23,
    PTR_MAX,
    TCD_BYTES,
    TC_TCD_BE_BYTES,
    TC_TCD_BE_TCD,
    decode_tcd,
    encode_tcd,
)


def _repro(config: dict, test_filter: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} DMA_BUF_DEPTH={depth} "
        "TIMING_PROFILE={timing} COCOTB_TEST_MODULES=tests.test_dma_directed "
        "TEST_FILTER={test_filter}"
    ).format(
        level=config["level"],
        sim=config["sim"],
        seed=config["seed"],
        depth=config["dma_buf_depth"],
        timing=config["timing_profile"],
        test_filter=test_filter,
    )


def _chunk_pairs(length: int, depth: int) -> int:
    """Expected ``DATA_READ``/``DATA_WRITE`` pair count for one TCD's transfer."""
    if length <= 0:
        return 0
    return -(-length // depth)


def _assert_ranges_overlap(tcd, *, test: str, repro: str) -> None:
    src_range = set(range(tcd.src_ptr, tcd.src_ptr + tcd.transfer_len))
    dest_range = set(range(tcd.dest_ptr, tcd.dest_ptr + tcd.transfer_len))
    assert src_range & dest_range, (
        f"{test}: stimulus must produce overlapping source/destination ranges, "
        f"got src=0x{tcd.src_ptr:06X} dest=0x{tcd.dest_ptr:06X} "
        f"len={tcd.transfer_len}. " + repro
    )


@cocotb.test()
async def tcd_big_endian_flags(dut):
    """TC-TCD-BE: known 11-byte descriptor encoding and flag decode."""
    config = parse_run_config()
    test = "TC-TCD-BE"
    repro = _repro(config, "tcd_big_endian_flags")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)

    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=TC_TCD_BE_TCD.transfer_len,
                src_device=TC_TCD_BE_TCD.src_device,
                dest_device=TC_TCD_BE_TCD.dest_device,
                next_device=TC_TCD_BE_TCD.next_device,
                src_addr=TC_TCD_BE_TCD.src_ptr,
                dest_addr=TC_TCD_BE_TCD.dest_ptr,
                next_tcd_addr=TC_TCD_BE_TCD.next_tcd,
                pattern=PATTERN_INCREMENT,
            )
        ],
        seed=1001,
    )
    head = chain.tcds[0]
    assert head == TC_TCD_BE_TCD, (
        f"{test}: generated head {head} does not match the mandatory unit "
        f"vector {TC_TCD_BE_TCD}. " + repro
    )
    assert encode_tcd(head) == TC_TCD_BE_BYTES, (
        f"{test}: encoded head bytes {encode_tcd(head).hex()} do not match the "
        f"mandatory vector bytes {TC_TCD_BE_BYTES.hex()}. " + repro
    )

    await _run_directed_window(dut, bringup, chain, test=test, config=config, repro=repro)


@cocotb.test()
async def same_device_psram0(dut):
    """TC-SAME-0: PSRAM0 to PSRAM0 copy."""
    config = parse_run_config()
    test = "TC-SAME-0"
    repro = _repro(config, "same_device_psram0")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=0, dest_device=0)], seed=1002
    )
    await _run_directed_window(dut, bringup, chain, test=test, config=config, repro=repro)

    observed_devices = {txn.device for txn in bringup.pin.transactions()}
    assert observed_devices == {0}, (
        f"{test}: expected only PSRAM0 selected, observed device(s) "
        f"{sorted(observed_devices)}. " + repro
    )


@cocotb.test()
async def same_device_psram1(dut):
    """TC-SAME-1: PSRAM1 to PSRAM1 copy after head fetch on PSRAM0."""
    config = parse_run_config()
    test = "TC-SAME-1"
    repro = _repro(config, "same_device_psram1")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=1, dest_device=1)], seed=1003
    )
    golden, _ = await _run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )

    fetch_devices = {descriptor.device for descriptor in golden.descriptors}
    data_devices = {txn.device for txn in golden.data_transactions()}
    assert fetch_devices == {0}, (
        f"{test}: descriptor fetches must stay on PSRAM0, observed "
        f"{fetch_devices}. " + repro
    )
    assert data_devices == {1}, (
        f"{test}: data transactions must land on PSRAM1, observed "
        f"{data_devices}. " + repro
    )


@cocotb.test()
async def cross_device_0_to_1(dut):
    """TC-CROSS-01: PSRAM0 source to PSRAM1 destination."""
    config = parse_run_config()
    test = "TC-CROSS-01"
    repro = _repro(config, "cross_device_0_to_1")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=0, dest_device=1)], seed=1004
    )
    golden, _ = await _run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )

    reads = {txn.device for txn in golden.transactions if txn.kind == DATA_READ}
    writes = {txn.device for txn in golden.transactions if txn.kind == DATA_WRITE}
    assert reads == {0} and writes == {1}, (
        f"{test}: expected reads on PSRAM0 and writes on PSRAM1, observed "
        f"reads={reads} writes={writes}. " + repro
    )


@cocotb.test()
async def cross_device_1_to_0(dut):
    """TC-CROSS-10: PSRAM1 source to PSRAM0 destination."""
    config = parse_run_config()
    test = "TC-CROSS-10"
    repro = _repro(config, "cross_device_1_to_0")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=1, dest_device=0)], seed=1005
    )
    golden, _ = await _run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )

    reads = {txn.device for txn in golden.transactions if txn.kind == DATA_READ}
    writes = {txn.device for txn in golden.transactions if txn.kind == DATA_WRITE}
    assert reads == {1} and writes == {0}, (
        f"{test}: expected reads on PSRAM1 and writes on PSRAM0, observed "
        f"reads={reads} writes={writes}. " + repro
    )


@cocotb.test()
async def multi_tcd_chain(dut):
    """TC-CHAIN: at least three executable TCDs followed by quit."""
    config = parse_run_config()
    test = "TC-CHAIN"
    repro = _repro(config, "multi_tcd_chain")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=3, src_device=0, dest_device=0),
            TcdSpec(transfer_len=5, src_device=0, dest_device=1),
            TcdSpec(transfer_len=2, src_device=1, dest_device=0),
        ],
        seed=1006,
    )
    assert len(chain.executable) == 3, (
        f"{test}: expected 3 executable TCDs before the quit descriptor, got "
        f"{len(chain.executable)}. " + repro
    )

    golden, _ = await _run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    assert golden.fetch_count == 4, (
        f"{test}: expected 4 fetches (3 executable + quit), golden model "
        f"produced {golden.fetch_count}. " + repro
    )


@cocotb.test()
async def next_device_alternate(dut):
    """TC-NEXT-DEVICE: chain with alternating NEXT_DEVICE selection."""
    config = parse_run_config()
    test = "TC-NEXT-DEVICE"
    repro = _repro(config, "next_device_alternate")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=2, next_device=1, next_tcd_addr=0x000000),
            TcdSpec(transfer_len=2, src_device=1, dest_device=1, next_device=0),
            TcdSpec(transfer_len=2, next_device=1),
        ],
        seed=1007,
    )
    assert chain.descriptor_locations[1] == (1, 0x000000), (
        f"{test}: expected the first link on PSRAM1 address 0x000000 "
        f"(address zero as a valid link), got {chain.descriptor_locations[1]}. "
        + repro
    )

    golden, _ = await _run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    devices = [device for device, _ in golden.path]
    assert devices == [0, 1, 0, 1], (
        f"{test}: expected the fetch device path [0, 1, 0, 1], golden model "
        f"path was {devices}. " + repro
    )


@cocotb.test()
async def transfer_length_corners(dut):
    """TC-LEN-CORNERS: lengths 0, 1, N-1, N, N+1, and 255."""
    config = parse_run_config()
    test = "TC-LEN-CORNERS"
    repro = _repro(config, "transfer_length_corners")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    depth = config["dma_buf_depth"]
    lengths = sorted({0, 1, depth - 1, depth, depth + 1, 255} & set(range(256)))

    for length in lengths:
        window = f"{test}[len={length}]"
        chain = build_directed_chain(
            [TcdSpec(transfer_len=length)], seed=2000 + length
        )
        golden, _ = await _run_directed_window(
            dut, bringup, chain, test=window, config=config, repro=repro
        )
        expected_pairs = _chunk_pairs(length, depth)
        observed_pairs = len(golden.data_transactions())
        assert observed_pairs == 2 * expected_pairs, (
            f"{window}: expected {2 * expected_pairs} data transaction(s) for "
            f"length={length} depth={depth}, golden model produced "
            f"{observed_pairs}. " + repro
        )


@cocotb.test()
async def quit_descriptor_priority(dut):
    """TC-QUIT: quit TCD with nonzero pointer and length fields."""
    config = parse_run_config()
    test = "TC-QUIT"
    repro = _repro(config, "quit_descriptor_priority")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=1)],
        quit_spec=TcdSpec(
            src_addr=0x001234,
            dest_addr=0x005678,
            transfer_len=0x22,
            src_device=1,
            dest_device=0,
        ),
        seed=1008,
    )
    quit_device, quit_address = chain.descriptor_locations[-1]
    quit_tcd = decode_tcd(chain.memory.read(quit_device, quit_address, TCD_BYTES))
    assert quit_tcd.quit and quit_tcd.transfer_len != 0 and quit_tcd.src_ptr != 0, (
        f"{test}: stimulus quit descriptor must carry nonzero pointer/length "
        f"fields, got {quit_tcd}. " + repro
    )

    golden, _ = await _run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    assert len(golden.transactions) == 4 and golden.transactions[-1].kind == FETCH_READ, (
        f"{test}: expected the quit fetch to be the final transaction with no "
        f"trailing data access, golden model produced "
        f"{[txn.kind for txn in golden.transactions]}. " + repro
    )


@cocotb.test()
async def empty_chain_at_head(dut):
    """TC-EMPTY: quit TCD at fixed head 0x000000 on PSRAM0."""
    config = parse_run_config()
    test = "TC-EMPTY"
    repro = _repro(config, "empty_chain_at_head")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain((), seed=1009)
    assert chain.descriptor_locations == ((HEAD_DEVICE, HEAD_ADDRESS),), (
        f"{test}: expected only the fixed head descriptor, got "
        f"{chain.descriptor_locations}. " + repro
    )

    golden, _ = await _run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    assert len(golden.transactions) == 1 and golden.transactions[0].kind == FETCH_READ, (
        f"{test}: expected exactly one fetch and no data transaction, golden "
        f"model produced {[txn.kind for txn in golden.transactions]}. " + repro
    )


@cocotb.test()
async def restart_after_completion(dut):
    """TC-RESTART: complete a chain then issue a new START."""
    config = parse_run_config()
    test = "TC-RESTART"
    repro = _repro(config, "restart_after_completion")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)

    first = build_directed_chain([TcdSpec(transfer_len=5)], seed=1010)
    await _run_directed_window(
        dut, bringup, first, test=f"{test}[run=1]", config=config, repro=repro
    )

    # No rst_n between runs: a fresh accepted START must still begin at the
    # fixed head, independent of whatever working state run 1 left behind.
    second = build_directed_chain(
        [TcdSpec(transfer_len=3, src_device=1, dest_device=0)], seed=1011
    )
    golden, _ = await _run_directed_window(
        dut, bringup, second, test=f"{test}[run=2]", config=config, repro=repro
    )
    assert golden.path[0] == (HEAD_DEVICE, HEAD_ADDRESS), (
        f"{test}: second START must begin at the fixed head, golden model "
        f"path started at {golden.path[0]}. " + repro
    )


_ADDR_WIDE_CASES = (
    ("below_64k", ADDR_LOW),
    ("at_64k_boundary", ADDR_BOUNDARY_64K),
    ("near_top", ADDR_HIGH),
)


@cocotb.test()
async def wide_address_space(dut):
    """TC-ADDR-WIDE: valid addresses below, at, and above 0x010000."""
    config = parse_run_config()
    test = "TC-ADDR-WIDE"
    repro = _repro(config, "wide_address_space")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    for index, (label, addr_class) in enumerate(_ADDR_WIDE_CASES):
        window = f"{test}[{label}]"
        chain = build_directed_chain(
            [TcdSpec(transfer_len=4, src_class=addr_class, dest_class=addr_class)],
            seed=1012 + index,
        )
        head = chain.tcds[0]
        assert head.src_ptr & PTR_BIT23 == 0 and head.dest_ptr & PTR_BIT23 == 0, (
            f"{window}: pointer bit 23 must stay clear, got "
            f"src=0x{head.src_ptr:06X} dest=0x{head.dest_ptr:06X}. " + repro
        )
        assert head.src_ptr + 3 <= PTR_MAX and head.dest_ptr + 3 <= PTR_MAX, (
            f"{window}: 4-byte range must stay inside 0x000000..0x{PTR_MAX:06X}. "
            + repro
        )
        await _run_directed_window(
            dut, bringup, chain, test=window, config=config, repro=repro
        )


@cocotb.test()
async def overlapping_same_device_ranges(dut):
    """TC-OVERLAP: same-device overlapping source and destination."""
    config = parse_run_config()
    test = "TC-OVERLAP"
    repro = _repro(config, "overlapping_same_device_ranges")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)

    forward = build_directed_chain(
        [
            TcdSpec(
                transfer_len=6,
                src_addr=0x001000,
                layout=LAYOUT_OVERLAP_FORWARD,
                pattern=PATTERN_INCREMENT,
            )
        ],
        seed=1015,
    )
    _assert_ranges_overlap(forward.tcds[0], test=f"{test}[forward]", repro=repro)
    await _run_directed_window(
        dut, bringup, forward, test=f"{test}[forward]", config=config, repro=repro
    )

    backward = build_directed_chain(
        [
            TcdSpec(
                transfer_len=6,
                src_addr=0x001010,
                layout=LAYOUT_OVERLAP_BACKWARD,
                pattern=PATTERN_INCREMENT,
            )
        ],
        seed=1016,
    )
    _assert_ranges_overlap(backward.tcds[0], test=f"{test}[backward]", repro=repro)
    await _run_directed_window(
        dut, bringup, backward, test=f"{test}[backward]", config=config, repro=repro
    )


@cocotb.test(skip=True)
async def dma_buf_depth_sweep(dut):
    """TC-DEPTH: skipped for M5 / harness depth (DMA_BUF_DEPTH 1/2/4/8 sweep)."""
    pass
