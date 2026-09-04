"""L1 DUT proofs for injection sync-latency, START capture, and reset.

Planning unit tests cannot see the two-flop host synchronizer. These cases
prove the Wave 3 driver contracts on ``tb_top`` and record ``COV-*`` hits
as M5 evidence (``make injection`` / ``make directed``):

* raw BUS_REQ is asserted ``SYNC_LATENCY_CYCLES`` before the synchronized
  ``bus_req`` cycle (so short ``COV-BUS-STATE`` / ``COV-BUS-PHASE`` bins
  remain reachable)
* after BUS_REQ STALL, origin and resumed state are recorded
  (``COV-BUS-RESUME`` when the origin is a catalog resume bin)
* capture-uncertain START cannot produce two synchronized edges; observed
  phase/result are recorded (two fixtures)
* FETCH / READ / WRITE reset injections record observed state/phase

``Q-LAUNCH`` (driven SIO/OE changes only while SCK is low, with modeled
setup/hold) is not judged during grant-park; the ASIC is not driving SCK.
Forced ``rst_n=0`` windows pass ``reset_truncated=REVIEW``.
"""

import cocotb
from cocotb.triggers import (
    NextTimeStep,
    ReadOnly,
    RisingEdge,
    SimTimeoutError,
    with_timeout,
)

from common.bringup import bring_up_top
from common.runlog import begin_run
from common.constants import BUS_GNT_MASK, GRANT_TIMEOUT_CYCLES
from common.directed import (
    auto_timeout_ns,
    commit_l1_window,
    install_chain,
    l1_adapter,
    wait_for_done_pulse,
)
from common.dispose import REVIEW, dispose_run
from common.host import BUS_REQ_BIT, pulse_start
from common.injection import (
    CAPTURE_UNCERTAIN,
    PHASE_NEAR_EDGE_AFTER,
    PHASE_NEAR_EDGE_BEFORE,
    SYNC_LATENCY_CYCLES,
    StartPulseRecord,
    inject_bus_req,
    inject_bus_req_at_new_fetch,
    jitter_start,
    pulse_reset,
    release_reset,
    resolve_clk_period_ns,
    start_pulse_width_ns,
)
from common.injection import _sample_names
from reference.coverage import BUS_RESUME_BINS
from reference.generator import PATTERN_INCREMENT, TcdSpec, build_directed_chain

def _sync_bus_req(dut) -> int:
    inner = getattr(dut, "dut", None)
    handle = getattr(inner, "bus_req", None) if inner is not None else None
    if handle is None:
        handle = getattr(dut, "bus_req", None)
    if handle is None:
        raise AssertionError("synchronized bus_req is not visible on this DUT")
    return int(handle.value)

async def _count_raw_to_sync_edges(dut) -> int:
    """Count rising ``clk`` edges after raw BUS_REQ rises until sync is high.

    Polls between edges so a mid-cycle raw assert is seen immediately, then
    counts the two-flop latency (``SYNC_LATENCY_CYCLES``).
    """
    from cocotb.triggers import Timer

    while ((int(dut.ui_in.value) >> BUS_REQ_BIT) & 1) == 0:
        await Timer(0.1, unit="ns")
    edges = 0
    while True:
        await RisingEdge(dut.clk)
        edges += 1
        await ReadOnly()
        if _sync_bus_req(dut) == 1:
            await NextTimeStep()
            return edges
        await NextTimeStep()

def _bus_gnt(dut) -> int:
    return 1 if (int(dut.uo_out.value) & BUS_GNT_MASK) else 0

async def _wait_grant_drop(dut, *, window: str, repro: str) -> None:
    for _ in range(GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _bus_gnt(dut) == 0:
            return
    raise AssertionError(f"{window}: BUS_GNT never dropped after BUS_REQ release. {repro}")

def _observed_start_result(sync_edges: int) -> str:
    return "idle_accepted" if sync_edges == 1 else "idle_uncaptured"

@cocotb.test()
async def injection_bus_req_sync_latency(dut):
    """Raw BUS_REQ rises SYNC_LATENCY_CYCLES before synchronized bus_req."""
    test = "injection_bus_req_sync_latency"
    config, repro = begin_run(dut, test)
    period = resolve_clk_period_ns()
    adapter = l1_adapter(config, test=test)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=8, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=7101,
    )
    install_chain(bringup, chain)

    latency = cocotb.start_soon(_count_raw_to_sync_edges(dut))
    bus_task = cocotb.start_soon(
        inject_bus_req(
            dut,
            target_state="FETCH",
            clk_period_ns=period,
            wait_grant=True,
            release=True,
        )
    )
    await pulse_start(dut)
    record = await bus_task
    edges = await latency
    assert edges == SYNC_LATENCY_CYCLES, (
        f"{test}: raw-to-sync took {edges} clk edges, expected "
        f"{SYNC_LATENCY_CYCLES}. observed_state={record.observed_state!r}. {repro}"
    )
    assert record.observed_state == "FETCH", (
        f"{test}: synchronized sample was {record.observed_state!r}, "
        f"expected FETCH. {repro}"
    )
    origin = record.observed_state
    adapter.record_bus_assertion(origin, record.observed_phase)
    await _wait_grant_drop(dut, window=test, repro=repro)
    resumed_state, resumed_phase = _sample_names(dut)
    dut._log.info(
        "%s: STALL origin=%s phase=%s resumed_state=%s resumed_phase=%s",
        test,
        origin,
        record.observed_phase,
        resumed_state,
        resumed_phase,
    )
    if origin in BUS_RESUME_BINS:
        adapter.record_bus_resume(origin)

    try:
        await with_timeout(wait_for_done_pulse(dut), auto_timeout_ns(chain) + 50_000, "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}: DONE did not return after FETCH stall. {repro}")
    dispose_run(bringup, test=test, log=dut._log, repro=repro)
    commit_l1_window(config, test=test, checkers_ok=True, scoreboard_na=True)

@cocotb.test()
async def injection_bus_req_new_fetch_sync_latency(dut):
    """NEW_FETCH helper also asserts raw SYNC_LATENCY_CYCLES before sync."""
    test = "injection_bus_req_new_fetch_sync_latency"
    config, repro = begin_run(dut, test)
    period = resolve_clk_period_ns()
    adapter = l1_adapter(config, test=test)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=4, pattern=PATTERN_INCREMENT)], seed=7102
    )
    install_chain(bringup, chain)

    latency = cocotb.start_soon(_count_raw_to_sync_edges(dut))
    record = await inject_bus_req_at_new_fetch(dut, clk_period_ns=period)
    edges = await latency
    assert edges == SYNC_LATENCY_CYCLES, (
        f"{test}: NEW_FETCH raw-to-sync took {edges} clk edges, expected "
        f"{SYNC_LATENCY_CYCLES}. {repro}"
    )
    assert record.observed_state == "NEW_FETCH", (
        f"{test}: helper must tag the synchronized cycle as NEW_FETCH, "
        f"got {record.observed_state!r}. {repro}"
    )
    assert record.grant_time_ns is not None, (
        f"{test}: BUS_GNT never observed after NEW_FETCH sync. {repro}"
    )
    origin = record.observed_state
    adapter.record_bus_assertion(origin, record.observed_phase)
    await _wait_grant_drop(dut, window=test, repro=repro)
    resumed_state, resumed_phase = _sample_names(dut)
    dut._log.info(
        "%s: STALL origin=%s phase=%s resumed_state=%s resumed_phase=%s",
        test,
        origin,
        record.observed_phase,
        resumed_state,
        resumed_phase,
    )
    adapter.record_bus_resume(origin)

    try:
        await with_timeout(wait_for_done_pulse(dut), auto_timeout_ns(chain) + 50_000, "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}: DONE did not return after NEW_FETCH stall. {repro}")
    dispose_run(bringup, test=test, log=dut._log, repro=repro)
    commit_l1_window(config, test=test, checkers_ok=True, scoreboard_na=True)

async def _uncertain_start_window(dut, *, test: str, config: dict, repro: str, plan: StartPulseRecord, seed: int):
    adapter = l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=1, pattern=PATTERN_INCREMENT)], seed=seed
    )
    install_chain(bringup, chain)

    record = await jitter_start(dut, plan, clk_period_ns=plan.clk_period_ns)
    assert record.sync_edges in (0, 1), (
        f"{test}: capture-uncertain START produced {record.sync_edges} "
        f"synchronized edges. {repro}"
    )
    assert record.sync_edges is not None and record.sync_edges <= 1
    adapter.record_start_phase(record.phase_bin)
    adapter.record_start_result(_observed_start_result(record.sync_edges))
    dut._log.info(
        "%s: observed phase=%s sync_edges=%s result=%s",
        test,
        record.phase_bin,
        record.sync_edges,
        _observed_start_result(record.sync_edges),
    )

    if record.sync_edges == 1:
        try:
            await with_timeout(wait_for_done_pulse(dut), auto_timeout_ns(chain), "ns")
        except SimTimeoutError:
            raise AssertionError(
                f"{test}: captured uncertain START never returned DONE. {repro}"
            )
    dispose_run(bringup, test=test, log=dut._log, repro=repro)
    commit_l1_window(config, test=test, checkers_ok=True, scoreboard_na=True)

@cocotb.test()
async def injection_start_uncertain_single_edge(dut):
    """Capture-uncertain START cannot produce two synchronized edges."""
    test = "injection_start_uncertain_single_edge"
    config, repro = begin_run(dut, test)
    period = resolve_clk_period_ns()

    plan = StartPulseRecord(
        capture=CAPTURE_UNCERTAIN,
        phase_bin=PHASE_NEAR_EDGE_BEFORE,
        assert_phase_ns=period - 0.5,
        deassert_phase_ns=period * 0.9,
        hold_ns=0.4 * period,
        clk_period_ns=period,
    )
    assert start_pulse_width_ns(plan) < period, (
        f"{test}: width helper must cap uncertain pulses under one clk. {repro}"
    )
    assert plan.hold_ns + plan.deassert_phase_ns > period, (
        f"{test}: fixture must be the two-edge case if deassert were added. {repro}"
    )
    await _uncertain_start_window(
        dut, test=test, config=config, repro=repro, plan=plan, seed=7201
    )

@cocotb.test()
async def injection_start_uncertain_near_edge_after(dut):
    """Second capture-uncertain START fixture (near-edge after) with recorded result."""
    test = "injection_start_uncertain_near_edge_after"
    config, repro = begin_run(dut, test)
    period = resolve_clk_period_ns()

    plan = StartPulseRecord(
        capture=CAPTURE_UNCERTAIN,
        phase_bin=PHASE_NEAR_EDGE_AFTER,
        assert_phase_ns=0.5,
        deassert_phase_ns=period * 0.9,
        hold_ns=0.4 * period,
        clk_period_ns=period,
    )
    assert start_pulse_width_ns(plan) < period, (
        f"{test}: width helper must cap uncertain pulses under one clk. {repro}"
    )
    await _uncertain_start_window(
        dut, test=test, config=config, repro=repro, plan=plan, seed=7202
    )

async def _reset_in_named_ctrl(dut, *, test: str, config: dict, repro: str, target: str, seed: int):
    adapter = l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=24,
                src_device=0,
                dest_device=1,
                pattern=PATTERN_INCREMENT,
            )
        ],
        seed=seed,
    )
    install_chain(bringup, chain)
    await pulse_start(dut)
    record = await pulse_reset(
        dut,
        target_state=target,
        bringup=bringup,
        release=False,
        reset_truncated=REVIEW,
    )
    assert record.observed_state == target, (
        f"{test}: reset sampled {record.observed_state!r}, expected {target}. {repro}"
    )
    adapter.record_reset(record.observed_state, record.observed_phase)
    dut._log.info(
        "%s: reset observed_state=%s observed_phase=%s",
        test,
        record.observed_state,
        record.observed_phase,
    )
    dispose_run(
        bringup, test=test, log=dut._log, reset_truncated=REVIEW, repro=repro
    )
    await release_reset(dut)
    commit_l1_window(config, test=test, checkers_ok=True, scoreboard_na=True)

@cocotb.test()
async def injection_reset_fetch(dut):
    """Force rst_n during FETCH; record observed state/phase."""
    test = "injection_reset_fetch"
    config, repro = begin_run(dut, test)
    await _reset_in_named_ctrl(
        dut, test=test, config=config, repro=repro, target="FETCH", seed=7301
    )

@cocotb.test()
async def injection_reset_read(dut):
    """Force rst_n during READ; record observed state/phase."""
    test = "injection_reset_read"
    config, repro = begin_run(dut, test)
    await _reset_in_named_ctrl(
        dut, test=test, config=config, repro=repro, target="READ", seed=7302
    )

@cocotb.test()
async def injection_reset_write(dut):
    """Force rst_n during WRITE; record observed state/phase."""
    test = "injection_reset_write"
    config, repro = begin_run(dut, test)
    await _reset_in_named_ctrl(
        dut, test=test, config=config, repro=repro, target="WRITE", seed=7303
    )
