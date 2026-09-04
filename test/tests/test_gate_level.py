"""L2 gate-level directed subset (``LEVEL=gl``, ``GATES=yes``).

Reuses L1 directed helpers (``common.directed`` / ``common.bringup``) with
top-pin-only pass criteria. No RTL hierarchy, source enums, internal register
names, or synthesis instance names.

Test-case IDs are ``TC-GL-*`` so L2 rows are not double-counted as L1 closure
(``09-gate-level-and-x.md``):
    TC-GL-SMOKE: same-device PSRAM0-to-PSRAM0 length-1 then quit
    TC-GL-TCD-BE: known 11-byte descriptor encoding and flag decode
    TC-GL-SAME-0 / TC-GL-SAME-1: both PSRAM CE# paths and shared SIO mapping
    TC-GL-CROSS-01 / TC-GL-CROSS-10: device-select muxing in both directions
    TC-GL-CHAIN / TC-GL-QUIT / TC-GL-RESTART: chain control and reset-to-fixed-head
    TC-GL-BUS-IDLE / TC-GL-BUS-ACTIVE / TC-GL-BUS-REPEAT: grant polarity, atomic
        completion, OE release, and resume (pin-observable only)
    TC-GL-RESET-IDLE / TC-GL-RESET-ACTIVE: initialization and reset recovery
        across gate storage (pin-observable only)
    TC-GL-RESET-RANDOM: seed-derived pin-observable reset campaign (M6 open)

Always-on ``CHK-*`` (runtime monitors) still dispose via ``dispose_run``.
L2 hierarchy rows are ``na``. SDF remains blocked.
"""

import os
import random
import zlib

import cocotb
from cocotb.triggers import (
    NextTimeStep,
    ReadOnly,
    RisingEdge,
    SimTimeoutError,
    Timer,
    with_timeout,
)

from common.bringup import bring_up
from common.runlog import begin_run
from common.constants import (
    BUS_GNT_MASK,
    GRANT_TIMEOUT_CYCLES,
    STATE_TIMEOUT_CYCLES,
)
from common.directed import (
    DONE_MASK,
    auto_timeout_ns,
    compare_and_dispose,
    install_chain,
    run_directed_window,
    wait_for_done_pulse,
)
from common.dispose import REVIEW, dispose_run
from common.host import assert_bus_req, pulse_start
from reference.chain import DATA_READ, DATA_WRITE, FETCH_READ, HEAD_ADDRESS, HEAD_DEVICE
from reference.constants import DMA_BUF_DEPTH_TAPEOUT
from reference.generator import PATTERN_INCREMENT, TcdSpec, build_directed_chain
from reference.tcd import TCD_BYTES, TC_TCD_BE_BYTES, TC_TCD_BE_TCD, decode_tcd, encode_tcd

_RESET_SETTLE_CYCLES = 5
_POST_RELEASE_IDLE_CYCLES = 10

def _require_l2(config: dict, *, repro: str) -> None:
    if config["level"] != "gl" or config["dut_level"] != "L2":
        raise AssertionError(
            "L2 suite requires LEVEL=gl / DUT_LEVEL=L2 "
            f"(got level={config['level']} dut_level={config['dut_level']}). "
            + repro
        )
    if config["dma_buf_depth"] != DMA_BUF_DEPTH_TAPEOUT:
        raise AssertionError(
            f"L2 netlist is flattened at DMA_BUF_DEPTH={DMA_BUF_DEPTH_TAPEOUT} "
            f"(got {config['dma_buf_depth']}). " + repro
        )
    if os.environ.get("SDF"):
        raise AssertionError(
            "SDF is blocked for this campaign (zero-delay functional GL is not "
            "an SDF pass). Unset SDF. " + repro
        )

def _log_netlist(dut, config: dict) -> None:
    netlist = os.environ.get("NETLIST", "gate_level_netlist.v")
    sha = os.environ.get("NETLIST_SHA256", "<unset>")
    pdk = os.environ.get("PDK_ROOT", "<unset>")
    sdf = os.environ.get("SDF", "")
    dut._log.info(
        "L2 netlist=%s sha256=%s PDK_ROOT=%s SDF=%s "
        "(zero-delay functional GL is not an SDF pass; SDF remains blocked)",
        netlist,
        sha,
        pdk,
        sdf or "<unset>",
    )
    dut._log.info(
        "SEED=%d LEVEL=%s SIM=%s DUT_LEVEL=%s DMA_BUF_DEPTH=%d TIMING_PROFILE=%s",
        config["seed"],
        config["level"],
        config["sim"],
        config["dut_level"],
        config["dma_buf_depth"],
        config["timing_profile"],
    )

def _known_int(handle, *, name: str, window: str, repro: str) -> int:
    """Integer-convert a top pin; X/Z is a failure (CHK-PIN-KNOWN)."""
    value = handle.value
    try:
        return int(value)
    except ValueError as exc:
        raise AssertionError(
            f"{window}: {name} is {value!s} (unexpected X/Z; CHK-PIN-KNOWN). {repro}"
        ) from exc

def _done(dut, *, window: str, repro: str) -> int:
    return _known_int(dut.uo_out, name="uo_out", window=window, repro=repro) & DONE_MASK

def _bus_gnt(dut, *, window: str, repro: str) -> int:
    return (
        1
        if (
            _known_int(dut.uo_out, name="uo_out", window=window, repro=repro)
            & BUS_GNT_MASK
        )
        else 0
    )

def _uio_oe(dut, *, window: str, repro: str) -> int:
    return _known_int(dut.uio_oe, name="uio_oe", window=window, repro=repro)

def _ce_levels(dut, *, window: str, repro: str) -> tuple[int, int]:
    return (
        _known_int(dut.bus_ram_a_cs_n, name="bus_ram_a_cs_n", window=window, repro=repro),
        _known_int(dut.bus_ram_b_cs_n, name="bus_ram_b_cs_n", window=window, repro=repro),
    )

def _assert_post_reset_known(dut, *, window: str, repro: str) -> None:
    """No unexpected X/Z on DONE, BUS_GNT, or uio_oe after reset release."""
    assert _done(dut, window=window, repro=repro) == 1, (
        f"{window}: DONE not 1 after L2 reset release. {repro}"
    )
    assert _bus_gnt(dut, window=window, repro=repro) == 0, (
        f"{window}: BUS_GNT not 0 after L2 reset release. {repro}"
    )
    # Idle park after release drives flash CS, SCK, and both RAM CS
    # (uio_oe 0xC9). uio_oe==0 is required during reset and BUS_GNT, not here.
    _uio_oe(dut, window=window, repro=repro)

async def _bring_up_l2(dut, config: dict, *, window: str, repro: str):
    bringup = await bring_up(dut)
    _assert_post_reset_known(dut, window=window, repro=repro)
    return bringup

async def _assert_reset_safe(dut, *, window: str, repro: str, cycles: int = _RESET_SETTLE_CYCLES) -> None:
    """CHK-RST-OE / CHK-RST-STATUS on top pins for one forced-reset window."""
    await Timer(1, unit="ns")
    oe = _uio_oe(dut, window=window, repro=repro)
    assert oe == 0, f"{window}: uio_oe=0x{oe:02X} while rst_n=0 (CHK-RST-OE). {repro}"

    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        rst = _known_int(dut.rst_n, name="rst_n", window=window, repro=repro)
        assert rst == 0, f"{window}: rst_n not held low across sampled edge. {repro}"
        assert _done(dut, window=window, repro=repro) == 1, (
            f"{window}: DONE not 1 after sampled reset (CHK-RST-STATUS). {repro}"
        )
        assert _bus_gnt(dut, window=window, repro=repro) == 0, (
            f"{window}: BUS_GNT not 0 after sampled reset (CHK-RST-STATUS). {repro}"
        )
        await NextTimeStep()

    ce0, ce1 = _ce_levels(dut, window=window, repro=repro)
    assert ce0 == 1, f"{window}: PSRAM0 CE# not idle high. {repro}"
    assert ce1 == 1, f"{window}: PSRAM1 CE# not idle high. {repro}"

async def _release_reset(dut) -> None:
    """Release ``rst_n`` away from a rising ``clk`` edge, then settle two clocks."""
    await Timer(10, unit="ns")
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)

async def _wait_done_low(dut, *, window: str, repro: str, timeout_cycles: int = STATE_TIMEOUT_CYCLES) -> None:
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        if _done(dut, window=window, repro=repro) == 0:
            return
    raise AssertionError(f"{window}: DONE never dropped after START. {repro}")

async def _wait_ce_low(dut, *, window: str, repro: str, timeout_cycles: int = STATE_TIMEOUT_CYCLES) -> None:
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        ce0, ce1 = _ce_levels(dut, window=window, repro=repro)
        if ce0 == 0 or ce1 == 0:
            return
    raise AssertionError(f"{window}: no RAM CE# fell (no QPI transaction). {repro}")

async def _wait_both_ce_high(
    dut, *, window: str, repro: str, timeout_cycles: int = STATE_TIMEOUT_CYCLES
) -> None:
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        ce0, ce1 = _ce_levels(dut, window=window, repro=repro)
        if ce0 == 1 and ce1 == 1:
            return
    raise AssertionError(f"{window}: RAM CE# never both idle high. {repro}")

async def _wait_bus_gnt(dut, *, want: int, window: str, repro: str) -> None:
    for _ in range(GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _bus_gnt(dut, window=window, repro=repro) == want:
            return
    expected = "asserted" if want else "released"
    raise AssertionError(f"{window}: BUS_GNT never {expected}. {repro}")

async def _pin_bus_req_cycle(dut, *, window: str, repro: str, wait_ce: bool = True) -> None:
    """Assert BUS_REQ, wait for atomic CE# high + grant, then release.

    Pin-only stand-in for L1 hierarchy-targeted ``TC-BUS-ACTIVE`` /
    ``TC-BUS-REPEAT`` cycles.
    """
    if wait_ce:
        await _wait_ce_low(dut, window=window, repro=repro)
    await assert_bus_req(dut, hold=True)
    for _ in range(GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        await ReadOnly()
        gnt = _bus_gnt(dut, window=window, repro=repro)
        ce0, ce1 = _ce_levels(dut, window=window, repro=repro)
        if gnt == 1:
            assert ce0 == 1 and ce1 == 1, (
                f"{window}: BUS_GNT with a RAM CE# still low (not atomic). {repro}"
            )
            await NextTimeStep()
            break
        await NextTimeStep()
    else:
        raise AssertionError(f"{window}: BUS_GNT never asserted after BUS_REQ. {repro}")
    oe = _uio_oe(dut, window=window, repro=repro)
    assert oe == 0, f"{window}: uio_oe=0x{oe:02X} under BUS_GNT. {repro}"
    await assert_bus_req(dut, hold=False)
    await _wait_bus_gnt(dut, want=0, window=window, repro=repro)

async def _assert_no_resume(dut, *, window: str, repro: str, txn_count: int, bringup) -> None:
    for _ in range(_POST_RELEASE_IDLE_CYCLES):
        await RisingEdge(dut.clk)
        assert _done(dut, window=window, repro=repro) == 1, (
            f"{window}: DONE not 1 after release with no fresh START "
            f"(spontaneous resume). {repro}"
        )
        assert _bus_gnt(dut, window=window, repro=repro) == 0, (
            f"{window}: BUS_GNT set without a fresh BUS_REQ. {repro}"
        )
    if bringup.pin is not None:
        assert len(bringup.pin.transactions()) == txn_count, (
            f"{window}: a new QPI transaction appeared with no fresh START "
            f"(spontaneous resume). {repro}"
        )

# =============================================================================
# TC-SMOKE
# =============================================================================

@cocotb.test()
async def gate_same_device_smoke(dut):
    """TC-SMOKE: one PSRAM0-to-PSRAM0 copy, length 1, then quit."""
    config, repro = begin_run(dut, "gate_same_device_smoke", test="TC-GL-SMOKE")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=1, src_device=0, dest_device=0)], seed=9001
    )
    await run_directed_window(dut, bringup, chain, test=test, config=config, repro=repro)

# =============================================================================
# TC-TCD-BE / same / cross / chain / quit / restart
# =============================================================================

@cocotb.test()
async def gate_tcd_big_endian_flags(dut):
    """TC-TCD-BE: known 11-byte descriptor encoding and flag decode."""
    config, repro = begin_run(dut, "gate_tcd_big_endian_flags", test="TC-GL-TCD-BE")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
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
        f"{test}: generated head {head} does not match {TC_TCD_BE_TCD}. " + repro
    )
    assert encode_tcd(head) == TC_TCD_BE_BYTES, (
        f"{test}: encoded head {encode_tcd(head).hex()} != {TC_TCD_BE_BYTES.hex()}. "
        + repro
    )
    await run_directed_window(dut, bringup, chain, test=test, config=config, repro=repro)

@cocotb.test()
async def gate_same_device_psram0(dut):
    """TC-SAME-0: PSRAM0 to PSRAM0 copy."""
    config, repro = begin_run(dut, "gate_same_device_psram0", test="TC-GL-SAME-0")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=0, dest_device=0)], seed=1002
    )
    golden, report = await run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    observed_devices = {txn.device for txn in report.pin_transactions}
    assert observed_devices == {0}, (
        f"{test}: expected only PSRAM0, observed {sorted(observed_devices)}. " + repro
    )

@cocotb.test()
async def gate_same_device_psram1(dut):
    """TC-SAME-1: PSRAM1 to PSRAM1 copy after head fetch on PSRAM0."""
    config, repro = begin_run(dut, "gate_same_device_psram1", test="TC-GL-SAME-1")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=1, dest_device=1)], seed=1003
    )
    golden, report = await run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    pin = list(report.pin_transactions)
    expected = list(golden.transactions)
    assert len(pin) == len(expected), (
        f"{test}: pin log length {len(pin)} != golden {len(expected)}. " + repro
    )
    fetch_devices = {
        obs.device
        for obs, exp in zip(pin, expected)
        if exp.kind == FETCH_READ
    }
    data_devices = {
        obs.device
        for obs, exp in zip(pin, expected)
        if exp.kind in (DATA_READ, DATA_WRITE)
    }
    assert fetch_devices == {0}, (
        f"{test}: descriptor fetches must stay on PSRAM0, observed {fetch_devices}. "
        + repro
    )
    assert data_devices == {1}, (
        f"{test}: data transactions must land on PSRAM1, observed {data_devices}. "
        + repro
    )

@cocotb.test()
async def gate_cross_device_0_to_1(dut):
    """TC-CROSS-01: PSRAM0 source to PSRAM1 destination."""
    config, repro = begin_run(dut, "gate_cross_device_0_to_1", test="TC-GL-CROSS-01")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=0, dest_device=1)], seed=1004
    )
    golden, report = await run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    pin = list(report.pin_transactions)
    expected = list(golden.transactions)
    reads = {
        obs.device
        for obs, exp in zip(pin, expected)
        if exp.kind == DATA_READ
    }
    writes = {
        obs.device
        for obs, exp in zip(pin, expected)
        if exp.kind == DATA_WRITE
    }
    assert reads == {0} and writes == {1}, (
        f"{test}: expected reads on PSRAM0 and writes on PSRAM1, "
        f"observed reads={reads} writes={writes}. " + repro
    )

@cocotb.test()
async def gate_cross_device_1_to_0(dut):
    """TC-CROSS-10: PSRAM1 source to PSRAM0 destination."""
    config, repro = begin_run(dut, "gate_cross_device_1_to_0", test="TC-GL-CROSS-10")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=1, dest_device=0)], seed=1005
    )
    golden, report = await run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    pin = list(report.pin_transactions)
    expected = list(golden.transactions)
    reads = {
        obs.device
        for obs, exp in zip(pin, expected)
        if exp.kind == DATA_READ
    }
    writes = {
        obs.device
        for obs, exp in zip(pin, expected)
        if exp.kind == DATA_WRITE
    }
    assert reads == {1} and writes == {0}, (
        f"{test}: expected reads on PSRAM1 and writes on PSRAM0, "
        f"observed reads={reads} writes={writes}. " + repro
    )

@cocotb.test()
async def gate_multi_tcd_chain(dut):
    """TC-CHAIN: at least three executable TCDs followed by quit."""
    config, repro = begin_run(dut, "gate_multi_tcd_chain", test="TC-GL-CHAIN")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=3, src_device=0, dest_device=0),
            TcdSpec(transfer_len=5, src_device=0, dest_device=1),
            TcdSpec(transfer_len=2, src_device=1, dest_device=0),
        ],
        seed=1006,
    )
    assert len(chain.executable) == 3, (
        f"{test}: expected 3 executable TCDs, got {len(chain.executable)}. " + repro
    )
    golden, _ = await run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    assert golden.fetch_count == 4, (
        f"{test}: expected 4 fetches (3 executable + quit), got {golden.fetch_count}. "
        + repro
    )

@cocotb.test()
async def gate_quit_descriptor_priority(dut):
    """TC-QUIT: quit TCD with nonzero pointer and length fields."""
    config, repro = begin_run(dut, "gate_quit_descriptor_priority", test="TC-GL-QUIT")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
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
        f"{test}: quit descriptor must carry nonzero pointer/length, got {quit_tcd}. "
        + repro
    )
    golden, _ = await run_directed_window(
        dut, bringup, chain, test=test, config=config, repro=repro
    )
    assert len(golden.transactions) == 4 and golden.transactions[-1].kind == FETCH_READ, (
        f"{test}: expected quit fetch as the final transaction, got "
        f"{[txn.kind for txn in golden.transactions]}. " + repro
    )

@cocotb.test()
async def gate_restart_after_completion(dut):
    """TC-RESTART: complete a chain then issue a new START."""
    config, repro = begin_run(dut, "gate_restart_after_completion", test="TC-GL-RESTART")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    first = build_directed_chain([TcdSpec(transfer_len=5)], seed=1010)
    await run_directed_window(
        dut, bringup, first, test=f"{test}[run=1]", config=config, repro=repro
    )
    second = build_directed_chain(
        [TcdSpec(transfer_len=3, src_device=1, dest_device=0)], seed=1011
    )
    golden, _ = await run_directed_window(
        dut, bringup, second, test=f"{test}[run=2]", config=config, repro=repro
    )
    assert golden.path[0] == (HEAD_DEVICE, HEAD_ADDRESS), (
        f"{test}: second START must begin at the fixed head, path started at "
        f"{golden.path[0]}. " + repro
    )

# =============================================================================
# TC-BUS-IDLE / TC-BUS-ACTIVE / TC-BUS-REPEAT
# =============================================================================

@cocotb.test()
async def gate_bus_req_from_idle(dut):
    """TC-BUS-IDLE: BUS_REQ in IDLE; START while req/grant high is ignored."""
    config, repro = begin_run(dut, "gate_bus_req_from_idle", test="TC-GL-BUS-IDLE")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    assert _done(dut, window=test, repro=repro) == 1
    assert _bus_gnt(dut, window=test, repro=repro) == 0

    await assert_bus_req(dut, hold=True)
    await _wait_bus_gnt(dut, want=1, window=test, repro=repro)
    oe = _uio_oe(dut, window=test, repro=repro)
    assert oe == 0, f"{test}: uio_oe=0x{oe:02X} under grant. {repro}"
    assert _done(dut, window=test, repro=repro) == 1, (
        f"{test}: DONE dropped while stalled from IDLE. {repro}"
    )

    await pulse_start(dut)
    assert _bus_gnt(dut, window=test, repro=repro) == 1, (
        f"{test}: START accepted while BUS_GNT active. {repro}"
    )
    assert _done(dut, window=test, repro=repro) == 1, (
        f"{test}: START while grant started a DMA (DONE dropped). {repro}"
    )

    await assert_bus_req(dut, hold=False)
    await _wait_bus_gnt(dut, want=0, window=test, repro=repro)
    assert _done(dut, window=test, repro=repro) == 1

    chain = build_directed_chain(
        [TcdSpec(transfer_len=3, pattern=PATTERN_INCREMENT)], seed=4001
    )
    await run_directed_window(dut, bringup, chain, test=test, config=config, repro=repro)

@cocotb.test()
async def gate_bus_req_during_transaction(dut):
    """TC-BUS-ACTIVE: BUS_REQ while a QPI transaction is on the pins."""
    config, repro = begin_run(dut, "gate_bus_req_during_transaction", test="TC-GL-BUS-ACTIVE")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=5, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=4201,
    )
    install_chain(bringup, chain)
    bringup.clear()

    await pulse_start(dut)
    await _wait_done_low(dut, window=test, repro=repro)
    await _pin_bus_req_cycle(dut, window=f"{test}[1]", repro=repro)
    await _pin_bus_req_cycle(dut, window=f"{test}[2]", repro=repro)
    await _pin_bus_req_cycle(dut, window=f"{test}[3]", repro=repro)

    try:
        await with_timeout(wait_for_done_pulse(dut), auto_timeout_ns(chain), "ns")
    except SimTimeoutError as exc:
        raise AssertionError(
            f"{test}: DONE did not return after active-time stalls. {repro}"
        ) from exc
    await compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)

@cocotb.test()
async def gate_bus_req_repeat_cycles(dut):
    """TC-BUS-REPEAT: multiple request/grant/release cycles in one chain."""
    config, repro = begin_run(dut, "gate_bus_req_repeat_cycles", test="TC-GL-BUS-REPEAT")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=test, repro=repro)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=6, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT),
            TcdSpec(transfer_len=5, src_device=1, dest_device=0, pattern=PATTERN_INCREMENT),
        ],
        seed=4401,
    )
    install_chain(bringup, chain)
    bringup.clear()

    await pulse_start(dut)
    await _wait_done_low(dut, window=test, repro=repro)
    for index in range(4):
        await _pin_bus_req_cycle(dut, window=f"{test}[{index}]", repro=repro)

    try:
        await with_timeout(wait_for_done_pulse(dut), auto_timeout_ns(chain), "ns")
    except SimTimeoutError as exc:
        raise AssertionError(
            f"{test}: DONE did not return after repeat cycles. {repro}"
        ) from exc
    await compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)

# =============================================================================
# TC-RESET-IDLE / TC-RESET-ACTIVE
# =============================================================================

@cocotb.test()
async def gate_reset_from_idle(dut):
    """TC-RESET-IDLE: reset from IDLE and while BUS_GNT is active."""
    config, repro = begin_run(dut, "gate_reset_from_idle", test="TC-GL-RESET-IDLE")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    bringup = await _bring_up_l2(dut, config, window=f"{test}[idle]", repro=repro)
    assert _done(dut, window=f"{test}[idle]", repro=repro) == 1
    assert _bus_gnt(dut, window=f"{test}[idle]", repro=repro) == 0
    for agent in bringup.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_reset_safe(dut, window=f"{test}[idle]", repro=repro)
    dispose_run(
        bringup, test=f"{test}[idle]", log=dut._log, reset_truncated=REVIEW, repro=repro
    )
    await _release_reset(dut)

    bringup.clear()
    chain = build_directed_chain(
        [TcdSpec(transfer_len=3, pattern=PATTERN_INCREMENT)], seed=5001
    )
    golden, report = await run_directed_window(
        dut, bringup, chain, test=f"{test}[idle]", config=config, repro=repro
    )
    pin = list(report.pin_transactions)
    assert pin, f"{test}[idle]: pin log empty after off-edge reset recovery. {repro}"
    head = pin[0]
    assert (
        head.device == HEAD_DEVICE
        and head.address == HEAD_ADDRESS
        and head.length == TCD_BYTES
    ), (
        f"{test}[idle]: expected 11-byte head fetch on PSRAM0 0x000000 after "
        f"off-edge reset release, pin {head.device}:0x{head.address:06X} "
        f"len={head.length}. {repro}"
    )

    bringup2 = await _bring_up_l2(dut, config, window=f"{test}[granted]", repro=repro)
    await assert_bus_req(dut, hold=True)
    await _wait_bus_gnt(dut, want=1, window=f"{test}[granted]", repro=repro)
    oe = _uio_oe(dut, window=f"{test}[granted]", repro=repro)
    assert oe == 0, f"{test}[granted]: uio_oe not clear under BUS_GNT. {repro}"
    bringup2.clear()
    for agent in bringup2.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_reset_safe(dut, window=f"{test}[granted]", repro=repro)
    dispose_run(
        bringup2,
        test=f"{test}[granted]",
        log=dut._log,
        reset_truncated=REVIEW,
        repro=repro,
    )
    await _release_reset(dut)

    bringup2.clear()
    second = build_directed_chain(
        [TcdSpec(transfer_len=3, src_device=1, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=5002,
    )
    await run_directed_window(
        dut, bringup2, second, test=f"{test}[granted]", config=config, repro=repro
    )

@cocotb.test()
async def gate_reset_during_activity(dut):
    """TC-RESET-ACTIVE: reset during pin-observable active QPI / grant."""
    config, repro = begin_run(dut, "gate_reset_during_activity", test="TC-GL-RESET-ACTIVE")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)

    async def _reset_window(label: str, prepare) -> None:
        window = f"{test}[{label}]"
        bringup = await _bring_up_l2(dut, config, window=window, repro=repro)
        chain = build_directed_chain(
            [
                TcdSpec(
                    transfer_len=24,
                    src_device=0,
                    dest_device=1,
                    pattern=PATTERN_INCREMENT,
                )
            ],
            seed=5100 + zlib_seed(label),
        )
        install_chain(bringup, chain)
        bringup.clear()
        await prepare(dut, bringup, window)
        for agent in bringup.agents:
            agent.note_reset()
        dut.rst_n.value = 0
        await _assert_reset_safe(dut, window=window, repro=repro)
        dispose_run(
            bringup, test=window, log=dut._log, reset_truncated=REVIEW, repro=repro
        )
        await _release_reset(dut)
        txn_after = 0 if bringup.pin is None else len(bringup.pin.transactions())
        await _assert_no_resume(
            dut, window=window, repro=repro, txn_count=txn_after, bringup=bringup
        )
        bringup.stop()

    async def _prep_ce_low(dut, bringup, window):
        await pulse_start(dut)
        await _wait_done_low(dut, window=window, repro=repro)
        await _wait_ce_low(dut, window=window, repro=repro)

    async def _prep_gap(dut, bringup, window):
        await pulse_start(dut)
        await _wait_done_low(dut, window=window, repro=repro)
        await _wait_ce_low(dut, window=window, repro=repro)
        await _wait_both_ce_high(dut, window=window, repro=repro)
        assert _done(dut, window=window, repro=repro) == 0, (
            f"{window}: expected inter-transaction gap with DONE still low. {repro}"
        )

    async def _prep_grant(dut, bringup, window):
        await pulse_start(dut)
        await _wait_done_low(dut, window=window, repro=repro)
        await _wait_ce_low(dut, window=window, repro=repro)
        await assert_bus_req(dut, hold=True)
        await _wait_bus_gnt(dut, want=1, window=window, repro=repro)

    await _reset_window("ce-low", _prep_ce_low)
    await _reset_window("inter-txn", _prep_gap)
    await _reset_window("granted", _prep_grant)

@cocotb.test()
async def gate_reset_random(dut):
    """TC-GL-RESET-RANDOM: seed-derived pin-observable reset, then 11-byte head fetch.

    M6 stays open: this is a feasible Icarus L2 reset campaign, not Verilator-X
    four-state coverage and not an SDF pass.
    """
    config, repro = begin_run(dut, "gate_reset_random", test="TC-GL-RESET-RANDOM")
    _require_l2(config, repro=repro)
    _log_netlist(dut, config)
    rng = random.Random(int(config["seed"]))
    mode = rng.choice(("idle", "ce-low", "granted"))
    dut._log.info("%s mode=%s %s", test, mode, repro)

    window = f"{test}[{mode}]"
    bringup = await _bring_up_l2(dut, config, window=window, repro=repro)
    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=16,
                src_device=0,
                dest_device=1,
                pattern=PATTERN_INCREMENT,
            )
        ],
        seed=5200 + int(config["seed"]),
    )
    install_chain(bringup, chain)
    bringup.clear()
    if mode == "idle":
        await RisingEdge(dut.clk)
    elif mode == "ce-low":
        await pulse_start(dut)
        await _wait_done_low(dut, window=window, repro=repro)
        await _wait_ce_low(dut, window=window, repro=repro)
    else:
        await pulse_start(dut)
        await _wait_done_low(dut, window=window, repro=repro)
        await _wait_ce_low(dut, window=window, repro=repro)
        await assert_bus_req(dut, hold=True)
        await _wait_bus_gnt(dut, want=1, window=window, repro=repro)

    for agent in bringup.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_reset_safe(dut, window=window, repro=repro)
    dispose_run(bringup, test=window, log=dut._log, reset_truncated=REVIEW, repro=repro)
    await _release_reset(dut)
    txn_after = 0 if bringup.pin is None else len(bringup.pin.transactions())
    await _assert_no_resume(
        dut, window=window, repro=repro, txn_count=txn_after, bringup=bringup
    )
    bringup.stop()

    recovery = await _bring_up_l2(dut, config, window=f"{test}[recover]", repro=repro)
    recover_chain = build_directed_chain(
        [TcdSpec(transfer_len=3, pattern=PATTERN_INCREMENT)], seed=5300
    )
    golden, report = await run_directed_window(
        dut, recovery, recover_chain, test=f"{test}[recover]", config=config, repro=repro
    )
    pin = list(report.pin_transactions)
    assert pin, f"{test}[recover]: pin log empty after randomized reset. {repro}"
    head = pin[0]
    assert (
        head.device == HEAD_DEVICE
        and head.address == HEAD_ADDRESS
        and head.length == TCD_BYTES
    ), (
        f"{test}[recover]: expected 11-byte head fetch after randomized L2 reset, "
        f"pin {head.device}:0x{head.address:06X} len={head.length}. {repro}"
    )
    assert golden is not None

def zlib_seed(label: str) -> int:
    """Stable per-window seed; not PYTHONHASHSEED-dependent."""
    return zlib.crc32(label.encode()) % 1_000
