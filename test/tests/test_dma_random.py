"""L1 constrained-random legal-chain regression.

Firmware-legal chains only (reserved=0, acyclic, ``A[22:0]`` ranges inside
``0x000000..0x7FFFFF``; ``ptr[23]`` don't-care per D35). Reserved Determinism streams ``start`` / ``bus_req``
/ ``reset`` are planned and driven through :mod:`common.injection` via the
``schedule_*`` / ``apply_*`` adapters below (injection names are ``plan_*`` /
``jitter_start`` / ``inject_*`` / ``pulse_reset``).

Always-on ``CHK-*`` monitors stay attached via :func:`common.bringup.bring_up_top`.

Functional coverage IDs (``COV-*``) sampled from passing windows:
    COV-LEN, COV-CHUNK, COV-DEVICE, COV-NEXTDEV, COV-CHAINLEN, COV-END,
    COV-ADDR, COV-DATA, COV-CTRL-STATE, COV-QPI-PHASE,
    COV-DEPTH, COV-DEPTH-LEN, COV-DEPTH-DEVICE
    COV-START-PHASE, COV-START-RESULT, COV-BUS-STATE, COV-BUS-PHASE,
    COV-RESET-STATE, COV-RESET-PHASE
"""

import hashlib
import json
import os
import subprocess

import cocotb
from cocotb.triggers import RisingEdge, SimTimeoutError, with_timeout

from common.artifacts import run_dir
from common.bringup import bring_up_top
from common.config import parse_run_config
from common.coverage_l1 import L1CoverageAdapter
from common.directed import (
    auto_timeout_ns,
    install_chain,
    read_back,
    run_context,
    wait_for_done_pulse,
)
from common.dispose import REVIEW, dispose_run
from common.injection import (
    CAPTURE_REQUIRED,
    InjectionPlanner,
    inject_bus_req,
    jitter_start,
    pulse_reset,
    resolve_clk_period_ns,
)
from reference.chain import ADDR_MAX, HEAD_ADDRESS, HEAD_DEVICE
from reference.coverage import FRAGMENT_FILENAME, CoverageError, CoverageSampler
from reference.generator import STREAMS, ChainGenerator
from reference.scoreboard import Scoreboard
from reference.tcd import TCD_BYTES, format_bytes, validate_tcd

# Reserved Determinism streams. Wave 3 plans and drives these via the adapters.
STREAM_START = "start"
STREAM_BUS_REQ = "bus_req"
STREAM_RESET = "reset"
RESERVED_STREAMS = (STREAM_START, STREAM_BUS_REQ, STREAM_RESET)

STIMULUS_FILENAME = "stimulus.json"
STIMULUS_SCHEMA = "dma-tapeout.stimulus.v1"


def _repro(config: dict, test_filter: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} DMA_BUF_DEPTH={depth} "
        "TIMING_PROFILE={timing} COCOTB_TEST_MODULES=tests.test_dma_random "
        "TEST_FILTER={test_filter}"
    ).format(
        level=config["level"],
        sim=config["sim"],
        seed=config["seed"],
        depth=config["dma_buf_depth"],
        timing=config["timing_profile"],
        test_filter=test_filter,
    )


def _child_seed(base_seed: int, stream_name: str) -> int:
    """Return the integer seed :func:`child_random` would use for *stream_name*."""
    digest = hashlib.sha256(f"{base_seed}:{stream_name}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big")


def _rtl_revision() -> str:
    """Best-effort git HEAD for the stimulus manifest's RTL revision field."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    try:
        result = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return (result.stdout or "").strip() or "unknown"


def schedule_start_edges(base_seed: int, *, clk_period_ns=None) -> list:
    """Adapter: ``InjectionPlanner.plan_start`` on stream ``start``."""
    planner = InjectionPlanner(
        base_seed, clk_period_ns=resolve_clk_period_ns(clk_period_ns)
    )
    return [planner.plan_start(capture=CAPTURE_REQUIRED)]


def schedule_bus_req_edges(base_seed: int, *, clk_period_ns=None) -> list:
    """Adapter: ``InjectionPlanner.plan_bus_req`` on stream ``bus_req``.

    Target FETCH (always present on a legal chain, including quit-only) so
    the generic early-assert driver can land ``COV-BUS-STATE``.
    """
    planner = InjectionPlanner(
        base_seed, clk_period_ns=resolve_clk_period_ns(clk_period_ns)
    )
    return [planner.plan_bus_req(target_state="FETCH")]


def schedule_reset_edges(base_seed: int, *, clk_period_ns=None) -> list:
    """Adapter: ``InjectionPlanner.plan_reset`` on stream ``reset``."""
    planner = InjectionPlanner(
        base_seed, clk_period_ns=resolve_clk_period_ns(clk_period_ns)
    )
    return [planner.plan_reset(reset_truncated=REVIEW, target_state="SYS_CTRL_IDLE")]


def _edge_payload(edges) -> list:
    payload = []
    for edge in edges:
        if hasattr(edge, "to_manifest"):
            payload.append(edge.to_manifest())
        else:
            payload.append(edge)
    return payload


async def apply_start_edges(dut, edges, *, adapter=None, clk_period_ns=None) -> list:
    """Adapter: drive each plan with :func:`common.injection.jitter_start`."""
    period = resolve_clk_period_ns(clk_period_ns)
    driven = []
    for edge in edges:
        record = await jitter_start(dut, edge, clk_period_ns=period)
        if adapter is not None:
            adapter.record_start_phase(record.phase_bin)
            if record.sync_edges == 0:
                adapter.record_start_result("idle_uncaptured")
            else:
                adapter.record_start_result("idle_accepted")
        if record.capture == CAPTURE_REQUIRED and record.sync_edges == 0:
            raise AssertionError(
                "capture-required START produced no synchronized edge"
            )
        driven.append(record)
    return driven


async def apply_bus_req_edges(dut, edges, *, adapter=None, clk_period_ns=None) -> list:
    """Adapter: drive each plan with :func:`common.injection.inject_bus_req`."""
    period = resolve_clk_period_ns(clk_period_ns)
    driven = []
    for edge in edges:
        record = await inject_bus_req(dut, edge, clk_period_ns=period)
        if adapter is not None and record.observed_state is not None:
            adapter.record_bus_assertion(record.observed_state, record.observed_phase)
        driven.append(record)
    return driven


async def apply_reset_edges(dut, edges, *, bringup=None, adapter=None) -> list:
    """Adapter: drive each plan with :func:`common.injection.pulse_reset`."""
    driven = []
    for edge in edges:
        record = await pulse_reset(dut, edge, bringup=bringup, release=True)
        if adapter is not None and record.observed_state is not None:
            adapter.record_reset(record.observed_state, record.observed_phase)
        driven.append(record)
    return driven


def assert_firmware_legal(chain, *, test: str, repro: str) -> None:
    """Require reserved=0, acyclic slots, and in-range ``A[22:0]`` complete spans."""
    assert chain.head == (HEAD_DEVICE, HEAD_ADDRESS), (
        f"{test}: legal chains start at PSRAM0 0x{HEAD_ADDRESS:06X}, "
        f"got {chain.head}. " + repro
    )
    seen = set()
    for tcd, (device, address) in zip(chain.tcds, chain.descriptor_locations):
        validate_tcd(tcd)
        assert tcd.reserved == 0, (
            f"{test}: reserved must be 0, got 0x{tcd.reserved:X}. " + repro
        )
        for name, pointer in (
            ("src_ptr", tcd.src_ptr),
            ("dest_ptr", tcd.dest_ptr),
            ("next_tcd", tcd.next_tcd),
        ):
            effective = pointer & ADDR_MAX
            assert 0 <= effective <= ADDR_MAX, (
                f"{test}: {name}=0x{pointer:X} A[22:0]=0x{effective:X} is outside "
                f"0x000000..0x{ADDR_MAX:06X}. " + repro
            )
        if tcd.transfer_len:
            src_base = tcd.src_ptr & ADDR_MAX
            dest_base = tcd.dest_ptr & ADDR_MAX
            src_last = src_base + tcd.transfer_len - 1
            dest_last = dest_base + tcd.transfer_len - 1
            assert src_last <= ADDR_MAX, (
                f"{test}: source range 0x{src_base:06X}+{tcd.transfer_len} "
                f"leaves 0x000000..0x{ADDR_MAX:06X}. " + repro
            )
            assert dest_last <= ADDR_MAX, (
                f"{test}: dest range 0x{dest_base:06X}+{tcd.transfer_len} "
                f"leaves 0x000000..0x{ADDR_MAX:06X}. " + repro
            )
        slot = (device, address)
        assert slot not in seen, (
            f"{test}: descriptor slot {device}:0x{address:06X} repeats "
            f"(cyclic or colliding chain). " + repro
        )
        seen.add(slot)
        assert 0 <= address and address + TCD_BYTES - 1 <= ADDR_MAX, (
            f"{test}: descriptor at {device}:0x{address:06X} is not a complete "
            f"11-byte record inside 0x000000..0x{ADDR_MAX:06X}. " + repro
        )


def write_stimulus_manifest(
    config: dict,
    chain,
    golden,
    *,
    start_edges,
    bus_edges,
    reset_edges,
    test: str,
    repro: str,
) -> str:
    """Write the Determinism-section stimulus manifest under ``RUN_DIR``."""
    dest = run_dir(config)
    os.makedirs(dest, exist_ok=True)
    base_seed = int(config["seed"])
    child_seeds = {
        name: _child_seed(base_seed, name)
        for name in list(STREAMS) + list(RESERVED_STREAMS)
    }
    payload = {
        "schema": STIMULUS_SCHEMA,
        "base_seed": base_seed,
        "child_seeds": child_seeds,
        "test": test,
        "dma_buf_depth": config["dma_buf_depth"],
        "timing_profile": config["timing_profile"],
        "simulator": config["sim"],
        "level": config["level"],
        "dut_level": config["dut_level"],
        "rtl_revision": _rtl_revision(),
        "repro": repro,
        "chain": chain.manifest(),
        "expected": {
            "path": [
                {"device": device, "address": address} for device, address in golden.path
            ],
            "completed": golden.completed,
            "fetch_count": golden.fetch_count,
            "transactions": [
                {
                    "index": txn.index,
                    "kind": txn.kind,
                    "opcode": txn.opcode,
                    "device": txn.device,
                    "address": txn.address,
                    "length": txn.length,
                    "data": format_bytes(txn.data),
                }
                for txn in golden.transactions
            ],
        },
        "start_edges": _edge_payload(start_edges),
        "bus_req_edges": _edge_payload(bus_edges),
        "reset_edges": _edge_payload(reset_edges),
    }
    path = os.path.join(dest, STIMULUS_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def _optional_child(parent, name):
    if parent is None:
        return None
    return getattr(parent, name, None)


async def sample_l1_states(dut, adapter: L1CoverageAdapter, running) -> None:
    """Record ``COV-CTRL-STATE`` / ``COV-QPI-PHASE`` the first time each encoding appears."""
    inner = _optional_child(dut, "dut")
    controller = _optional_child(inner, "sys_controller")
    engine = _optional_child(inner, "qspi_engine")
    seen_ctrl = set()
    seen_qpi = set()
    while running[0]:
        await RisingEdge(dut.clk)
        if controller is not None:
            try:
                state = int(controller.curr_state.value)
            except (ValueError, TypeError, AttributeError):
                state = None
            if state is not None and state not in seen_ctrl:
                try:
                    adapter.record_ctrl_state(state)
                    seen_ctrl.add(state)
                except CoverageError:
                    pass
        if engine is not None:
            try:
                phase = int(engine.curr_state.value)
            except (ValueError, TypeError, AttributeError):
                phase = None
            if phase is not None and phase not in seen_qpi:
                try:
                    adapter.record_qpi_phase(phase)
                    seen_qpi.add(phase)
                except CoverageError:
                    pass


@cocotb.test()
async def random_legal_chain(dut):
    """Constrained-random firmware-legal descriptor chains (M5 high-volume suite)."""
    config = parse_run_config()
    test = "random_legal_chain"
    repro = _repro(config, test)
    dut._log.info(
        "SEED=%d LEVEL=%s SIM=%s DUT_LEVEL=%s DMA_BUF_DEPTH=%d TIMING_PROFILE=%s",
        config["seed"],
        config["level"],
        config["sim"],
        config["dut_level"],
        config["dma_buf_depth"],
        config["timing_profile"],
    )
    dut._log.info(repro)

    cov_config = dict(config)
    cov_config["test"] = test
    cov = CoverageSampler.from_config(cov_config)
    adapter = L1CoverageAdapter(cov)

    generator = ChainGenerator(config["seed"], dma_buf_depth=config["dma_buf_depth"])
    chain = generator.build_chain()
    assert_firmware_legal(chain, test=test, repro=repro)
    golden = chain.interpret(dma_buf_depth=config["dma_buf_depth"])
    dut._log.info("%s", chain.describe())

    clk_period_ns = resolve_clk_period_ns()
    start_edges = schedule_start_edges(config["seed"], clk_period_ns=clk_period_ns)
    bus_edges = schedule_bus_req_edges(config["seed"], clk_period_ns=clk_period_ns)
    reset_edges = schedule_reset_edges(config["seed"], clk_period_ns=clk_period_ns)

    bringup = await bring_up_top(dut)
    bringup.clear()
    install_chain(bringup, chain)

    running = [True]
    cocotb.start_soon(sample_l1_states(dut, adapter, running))
    timeout_ns = auto_timeout_ns(chain) + 50_000
    scoreboard_ok = False
    checkers_ok = False
    reset_applied = False
    try:
        bus_task = cocotb.start_soon(
            apply_bus_req_edges(
                dut, bus_edges, adapter=adapter, clk_period_ns=clk_period_ns
            )
        )
        await apply_start_edges(
            dut, start_edges, adapter=adapter, clk_period_ns=clk_period_ns
        )
        try:
            await with_timeout(wait_for_done_pulse(dut), timeout_ns, "ns")
        except SimTimeoutError:
            dut._log.error(repro)
            raise AssertionError(
                f"{test}: DONE did not return within {timeout_ns} ns; classify "
                "DUT vs TB before retry. " + repro
            ) from None
        try:
            await with_timeout(bus_task, timeout_ns, "ns")
        except SimTimeoutError:
            dut._log.error(repro)
            raise AssertionError(
                f"{test}: BUS_REQ injector did not finish within {timeout_ns} ns. "
                + repro
            ) from None

        if bringup.pin is None or bringup.pin.blocked:
            reason = (
                "missing"
                if bringup.pin is None
                else f"blocked ({bringup.pin.blocked_reason})"
            )
            raise AssertionError(
                f"{test}: pin monitor {reason}; L1 random cases require a live "
                "pin axis for dual-axis scoreboard compare. " + repro
            )

        board = Scoreboard.from_result(
            golden,
            guards=chain.guards,
            regions=chain.regions,
            context=run_context(config, test, repro),
            log=dut._log,
        )
        board.compare(
            bringup.pin.transactions(),
            observed_memory=read_back(bringup, chain),
        )
        scoreboard_ok = True
        cov.record_compare(golden, generated=chain, scoreboard=board)

        if reset_edges:
            await apply_reset_edges(
                dut, reset_edges, bringup=bringup, adapter=adapter
            )
            reset_applied = True
        report = dispose_run(
            bringup,
            test=test,
            log=dut._log,
            reset_truncated=REVIEW if reset_applied else "forbid",
            repro=repro,
        )
        checkers_ok = True
        dut._log.info(
            "%s passed: %d transaction(s) (%s)",
            test,
            len(golden.transactions),
            report.summary(),
        )
    finally:
        running[0] = False
        if not scoreboard_ok:
            cov.record_chain(golden, generated=chain)
        cov.commit_window(checkers_ok=checkers_ok, scoreboard_ok=scoreboard_ok)
        coverage_path = cov.write_fragment()
        manifest_path = write_stimulus_manifest(
            config,
            chain,
            golden,
            start_edges=start_edges,
            bus_edges=bus_edges,
            reset_edges=reset_edges,
            test=test,
            repro=repro,
        )
        dut._log.info("coverage fragment: %s", coverage_path)
        dut._log.info("stimulus manifest: %s", manifest_path)
        dut._log.info(
            "RUN_DIR=%s coverage=%s stimulus=%s",
            run_dir(config),
            FRAGMENT_FILENAME,
            STIMULUS_FILENAME,
        )
