"""L1 START, BUS_REQ, and reset directed tests.

Every case is DUT-master, mirroring ``tests.test_dma_directed``: a legal chain
is backdoor-installed via :func:`common.bringup.bring_up_top` models, and the
ASIC itself fetches descriptors and moves payload over QPI. START/BUS_REQ are
asynchronous host levels behind a two-flop synchronizer (D24,
``src/rtl/top.v``); several cases below drive the raw ``ui_in`` bits at exact
clock-edge offsets to land the *synchronized* level inside one specific
single-cycle controller/engine state, not just "close to" it.

Test-case IDs:
    TC-START-ACTIVE
    TC-START-HELD
    TC-START-PHASE
    TC-BUS-IDLE
    TC-BUS-BOUNDARY
    TC-BUS-ACTIVE
    TC-BUS-PHASE
    TC-BUS-REPEAT
    TC-RESET-IDLE
    TC-RESET-ACTIVE
    TC-RESET-REPEAT

Coverage IDs (where applicable):
    COV-START-PHASE, COV-START-RESULT,
    COV-BUS-STATE, COV-BUS-PHASE, COV-BUS-RESUME,
    COV-RESET-STATE, COV-RESET-PHASE

Not in scope here: ``test_qspi_reset_protocol.py`` (``TC-QRST-ACTIVE``) is the
M1 Q-RST behavioral dispose proof, a different case ID from the ``TC-RESET-*``
matrix below.
"""

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

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.directed import (
    DONE_BIT,
    auto_timeout_ns as _auto_timeout_ns,
    commit_l1_window as _commit_l1_window,
    compare_and_dispose as _compare_and_dispose,
    install_chain as _install_chain,
    l1_adapter as _l1_adapter,
    run_context as _run_context,
    wait_for_done_pulse as _wait_for_done_pulse,
    wait_until_done as _wait_until_done,
)
from common.dispose import REVIEW, dispose_run
from common.host import BUS_REQ_BIT, START_BIT, assert_bus_req, pulse_start
from common.injection import (
    START_PHASE_BINS,
    SYNC_LATENCY_CYCLES,
    classify_start_phase,
    inject_bus_req,
    inject_bus_req_at_new_fetch,
    offset_for_phase_bin,
    resolve_clk_period_ns,
)
from monitors.timing import Q_LAUNCH
from monitors.handshake import (
    QSPI_ENGINE_STATES,
    SYS_CONTROL_FETCH,
    SYS_CONTROL_IDLE,
    SYS_CONTROL_NEW_FETCH,
    SYS_CONTROL_NEW_OP,
    SYS_CONTROL_READ,
    SYS_CONTROL_STALL,
    SYS_CONTROL_STATES,
    SYS_CONTROL_UPDATE,
    SYS_CONTROL_WRITE,
)
from reference.generator import PATTERN_INCREMENT, TcdSpec, build_directed_chain
from reference.scoreboard import Scoreboard

BUS_GNT_BIT = 0x2

_STATE_TIMEOUT_CYCLES = 50_000
_GRANT_TIMEOUT_CYCLES = 2_000
_RESET_SETTLE_CYCLES = 5
_POST_RELEASE_IDLE_CYCLES = 10


def _assert_no_ordinary_qlaunch(bringup, *, window: str, repro: str) -> None:
    """Grant-park OE release must not produce ordinary Q-LAUNCH.

    ``Q-LAUNCH`` (driven SIO/OE changes only while SCK is low, with modeled
    setup/hold) applies while the ASIC drives SCK; ``BUS_GNT`` park clears
    ``asic_sck_oe`` and is owned by arbitration / reset OE checks instead.
    """
    ce = getattr(bringup, "ce", None)
    if ce is None:
        return
    hits = [
        event
        for event in ce.events
        if event.check_id == Q_LAUNCH and not event.reset_truncated
    ]
    assert not hits, (
        f"{window}: grant-park OE release produced ordinary Q-LAUNCH "
        f"(ASIC SCK OE off is not a launch window): {hits[0]}. {repro}"
    )

# qspi_pkg::qspi_state_t names (src/rtl/types.svh) resolved through the same
# dict monitors.handshake already owns, so no magic numbers are duplicated.
_ENGINE_STATE_BY_NAME = {name: code for code, name in QSPI_ENGINE_STATES.items()}
_QSPI_CS_ON = _ENGINE_STATE_BY_NAME["CS_ON"]
_QSPI_SEND_CMD_1 = _ENGINE_STATE_BY_NAME["SEND_CMD_1"]
_QSPI_SEND_ADDR = _ENGINE_STATE_BY_NAME["SEND_ADDR"]
_QSPI_WAIT = _ENGINE_STATE_BY_NAME["WAIT"]
_QSPI_READ_DATA = _ENGINE_STATE_BY_NAME["READ_DATA"]
_QSPI_WRITE_DATA = _ENGINE_STATE_BY_NAME["WRITE_DATA"]
_QSPI_SCLK_OFF = _ENGINE_STATE_BY_NAME["SCLK_OFF"]
_QSPI_CS_OFF = _ENGINE_STATE_BY_NAME["CS_OFF"]

# sys_control_pkg::sys_control_state_t bins TC-RESET-ACTIVE sweeps (all eight
# encodings; COV-RESET-STATE). STALL is reached through a BUS_REQ detour.
_CONTROLLER_RESET_TARGETS = (
    SYS_CONTROL_IDLE,
    SYS_CONTROL_NEW_FETCH,
    SYS_CONTROL_FETCH,
    SYS_CONTROL_NEW_OP,
    SYS_CONTROL_READ,
    SYS_CONTROL_WRITE,
    SYS_CONTROL_UPDATE,
    SYS_CONTROL_STALL,
)

# Externally visible QPI phase bins TC-RESET-ACTIVE and TC-BUS-PHASE sweep
# (COV-RESET-PHASE / COV-BUS-PHASE); idle/pad is already the IDLE controller
# bin above, so it is not repeated here.
_ENGINE_PHASE_RESET_TARGETS = (
    _QSPI_SEND_CMD_1,
    _QSPI_SEND_ADDR,
    _QSPI_WAIT,
    _QSPI_READ_DATA,
    _QSPI_WRITE_DATA,
    _QSPI_SCLK_OFF,
    _QSPI_CS_OFF,
)


def _repro(config: dict, test_filter: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} DMA_BUF_DEPTH={depth} "
        "TIMING_PROFILE={timing} COCOTB_TEST_MODULES=tests.test_reset_and_bus "
        "TEST_FILTER={test_filter}"
    ).format(
        level=config["level"],
        sim=config["sim"],
        seed=config["seed"],
        depth=config["dma_buf_depth"],
        timing=config["timing_profile"],
        test_filter=test_filter,
    )


# -- signal / hierarchy access ----------------------------------------------


def _level(handle) -> "int | None":
    try:
        return int(handle.value)
    except ValueError:
        return None


def _done(dut) -> int:
    return int(dut.uo_out.value) & DONE_BIT


def _bus_gnt(dut) -> int:
    return 1 if (int(dut.uo_out.value) & BUS_GNT_BIT) else 0


def _controller(dut):
    """``dut.dut.sys_controller`` (RTL-hierarchy-only visibility, L1 tb_top)."""
    return dut.dut.sys_controller


def _engine(dut):
    """``dut.dut.qspi_engine`` (RTL-hierarchy-only visibility, L1 tb_top)."""
    return dut.dut.qspi_engine


# -- precise state-targeted stimulus -----------------------------------------


async def _await_controller_state(
    dut, targets, *, timeout_cycles: int = _STATE_TIMEOUT_CYCLES, repro: str = ""
) -> int:
    """Poll ``sys_controller.curr_state`` until it is in *targets*.

    Returns from the read-only-safe (write-enabled) point right after the
    sampling edge, so the caller may drive ``ui_in`` / ``rst_n`` immediately
    and have it land inside the very cycle the target state was observed.
    """
    controller = _controller(dut)
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        state = int(controller.curr_state.value)
        if state in targets:
            await NextTimeStep()
            return state
        await NextTimeStep()
    raise AssertionError(
        f"sys_controller.curr_state never reached {targets} within "
        f"{timeout_cycles} cycles. {repro}"
    )


async def _await_engine_state(
    dut, targets, *, timeout_cycles: int = _STATE_TIMEOUT_CYCLES, repro: str = ""
) -> int:
    """Poll ``qspi_engine.curr_state`` until it is in *targets* (see above)."""
    engine = _engine(dut)
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        state = int(engine.curr_state.value)
        if state in targets:
            await NextTimeStep()
            return state
        await NextTimeStep()
    raise AssertionError(
        f"qspi_engine.curr_state never reached {targets} within "
        f"{timeout_cycles} cycles. {repro}"
    )


async def _pulse_start_with_bus_req_at_new_fetch(dut) -> None:
    """Accepted START with BUS_REQ timed to synchronize exactly at NEW_FETCH.

    ``bus_req`` and ``start`` share IDLE's priority check (D22/D23: BUS_REQ
    wins), so BUS_REQ cannot go raw-high before or with START -- that would
    divert IDLE straight to STALL and NEW_FETCH would never be entered. The
    two-flop synchronizer needs the raw edge exactly one cycle after START's,
    so the synchronized level lands the cycle ``curr_state`` first becomes
    NEW_FETCH, not the cycle before (still IDLE) or after (already FETCH).
    """
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << START_BIT)
    await RisingEdge(dut.clk)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << BUS_REQ_BIT)
    await RisingEdge(dut.clk)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current & ~(1 << START_BIT) & 0xFF
    await RisingEdge(dut.clk)


def _sample_ctrl_qpi(dut):
    """Return ``(controller_state, qpi_phase)`` encodings at this instant."""
    return (
        int(_controller(dut).curr_state.value),
        int(_engine(dut).curr_state.value),
    )


async def _record_bus_after_sync(
    dut, adapter, *, observed_state=None, observed_phase=None
) -> None:
    """Wait the two-flop latency, then record COV-BUS-STATE / COV-BUS-PHASE.

    ``COV-BUS-STATE`` is BUS_REQ synchronized assertion x controller state;
    ``COV-BUS-PHASE`` is the same edge x QPI phase. One-cycle bins update
    ``curr_state`` in the same NBA as the synchronized request, so callers
    pass the targeted name when ReadOnly would already show the next state.
    """
    if adapter is None:
        return
    for index in range(SYNC_LATENCY_CYCLES):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if index == SYNC_LATENCY_CYCLES - 1:
            state, phase = _sample_ctrl_qpi(dut)
            adapter.record_bus_assertion(
                observed_state if observed_state is not None else state,
                observed_phase if observed_phase is not None else phase,
            )
        await NextTimeStep()


async def _bus_req_cycle(
    dut,
    trigger=None,
    *,
    repro: str,
    adapter=None,
    observed_state=None,
    observed_phase=None,
    resume_origin=None,
) -> None:
    """One assert/catch/grant/release BUS_REQ cycle with atomicity checks.

    *trigger*, when given, is an unstarted coroutine (e.g.
    :func:`_await_controller_state` / :func:`_await_engine_state`) this
    function awaits before asserting raw BUS_REQ. Pass ``None`` when the
    caller already asserted BUS_REQ itself (the NEW_FETCH boundary case).
    """
    if trigger is not None:
        await trigger
        await assert_bus_req(dut, hold=True)
        await _record_bus_after_sync(
            dut,
            adapter,
            observed_state=observed_state,
            observed_phase=observed_phase,
        )
    elif adapter is not None:
        state, phase = _sample_ctrl_qpi(dut)
        adapter.record_bus_assertion(
            observed_state if observed_state is not None else state,
            observed_phase if observed_phase is not None else phase,
        )

    await _await_controller_state(dut, (SYS_CONTROL_STALL,), repro=repro)

    for _ in range(_GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if _bus_gnt(dut) == 1:
            assert int(_engine(dut).busy.value) == 0, (
                "BUS_GNT asserted while the QPI engine is still busy "
                f"(transaction not atomic). {repro}"
            )
            assert _level(dut.bus_ram_a_cs_n) == 1 and _level(dut.bus_ram_b_cs_n) == 1, (
                "BUS_GNT asserted with a RAM CE# still low. " + repro
            )
            await NextTimeStep()
            break
        await NextTimeStep()
    else:
        raise AssertionError(f"BUS_GNT never asserted after BUS_REQ. {repro}")

    assert int(dut.uio_oe.value) == 0, f"uio_oe not clear under BUS_GNT. {repro}"

    await assert_bus_req(dut, hold=False)
    for _ in range(_GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _bus_gnt(dut) == 0:
            break
    else:
        raise AssertionError(f"BUS_GNT never released after BUS_REQ release. {repro}")
    if adapter is not None and resume_origin is not None:
        adapter.record_bus_resume(resume_origin)


async def _bus_req_targeted(
    dut,
    *,
    repro: str,
    adapter=None,
    target_state=None,
    target_phase=None,
    new_fetch: bool = False,
    resume_origin=None,
):
    """Assert BUS_REQ so the synchronized edge lands in a short state or phase."""
    if new_fetch:
        record = await inject_bus_req_at_new_fetch(dut, release=False)
    else:
        record = await inject_bus_req(
            dut,
            target_state=target_state,
            target_phase=target_phase,
            release=False,
        )
    if adapter is not None:
        state = "NEW_FETCH" if new_fetch else (target_state or record.observed_state)
        phase = target_phase if target_phase is not None else record.observed_phase
        if state is not None:
            adapter.record_bus_assertion(state, phase)
    assert _bus_gnt(dut) == 1, f"BUS_GNT never asserted after targeted BUS_REQ. {repro}"
    assert int(dut.uio_oe.value) == 0, f"uio_oe not clear under BUS_GNT. {repro}"
    await assert_bus_req(dut, hold=False)
    for _ in range(_GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _bus_gnt(dut) == 0:
            break
    else:
        raise AssertionError(f"BUS_GNT never released after targeted BUS_REQ. {repro}")
    if adapter is not None and resume_origin is not None:
        adapter.record_bus_resume(resume_origin)
    return record


# -- reset stimulus -----------------------------------------------------------


async def _assert_reset_safe(dut, *, window: str, cycles: int = _RESET_SETTLE_CYCLES) -> None:
    """CHK-RST-OE / CHK-RST-STATUS-equivalent local check for one reset window.

    Mirrors ``tests.test_qspi_reset_protocol._assert_rst_n_clears_top_oe`` /
    ``_assert_sampled_reset_status_top``: combinational ``uio_oe`` clears
    immediately, then every sampled edge with ``rst_n=0`` shows DONE=1,
    BUS_GNT=0, and both PSRAM CE# idle high.
    """
    await Timer(1, unit="ns")
    oe = int(dut.uio_oe.value)
    assert oe == 0, f"{window}: uio_oe=0x{oe:02X} while rst_n=0 (CHK-RST-OE). "

    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert _level(dut.rst_n) == 0, f"{window}: rst_n not held low across sampled edge"
        assert _done(dut) == 1, f"{window}: DONE not 1 after sampled reset"
        assert _bus_gnt(dut) == 0, f"{window}: BUS_GNT not 0 after sampled reset"
        await NextTimeStep()

    assert _level(dut.bus_ram_a_cs_n) == 1, f"{window}: PSRAM0 CE# not idle high"
    assert _level(dut.bus_ram_b_cs_n) == 1, f"{window}: PSRAM1 CE# not idle high"


async def _release_reset(dut) -> None:
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    await RisingEdge(dut.clk)


async def _drive_to_controller_state(dut, target: int, *, repro: str) -> None:
    """Advance a running chain until ``curr_state == target``, inducing STALL
    via a BUS_REQ detour when *target* is STALL itself."""
    if target == SYS_CONTROL_STALL:
        await _await_controller_state(
            dut, (SYS_CONTROL_FETCH, SYS_CONTROL_READ, SYS_CONTROL_WRITE), repro=repro
        )
        await assert_bus_req(dut, hold=True)
        await _await_controller_state(dut, (SYS_CONTROL_STALL,), repro=repro)
    else:
        await _await_controller_state(dut, (target,), repro=repro)


async def _reset_mid_run(
    dut, config: dict, *, kind: str, target: int, window: str, repro: str
) -> None:
    """Fresh bring-up, drive to *target*, assert/verify/release ``rst_n``.

    Axis 1 only (``compare_reset_prefix``): the required result for
    ``TC-RESET-ACTIVE`` is CE#/OE reset-safety, cleared working state, and no
    spontaneous resume, not a final-memory match at the truncation point
    itself (unlike ``TC-RESET-REPEAT``, which requires memory equality after a
    *subsequent completed* run).
    """
    bringup = await bring_up_top(dut)
    # zlib.crc32, not the builtin hash(): PYTHONHASHSEED randomizes str hash()
    # per process, which would silently break REPRO reproducibility.
    seed = zlib.crc32(window.encode()) % 1_000_000
    chain = build_directed_chain(
        [TcdSpec(transfer_len=24, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=seed,
    )
    _install_chain(bringup, chain)
    bringup.clear()

    if not (kind == "controller" and target == SYS_CONTROL_IDLE):
        await pulse_start(dut)
        if kind == "controller":
            await _drive_to_controller_state(dut, target, repro=repro)
        else:
            await _await_engine_state(dut, (target,), repro=repro)

    txn_count = 0 if bringup.pin is None else len(bringup.pin.transactions())
    adapter = _l1_adapter(config, test=window)
    state, phase = _sample_ctrl_qpi(dut)
    adapter.record_reset(state, phase)
    for agent in bringup.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_reset_safe(dut, window=window)

    if bringup.pin is not None and not bringup.pin.blocked:
        golden = chain.interpret(dma_buf_depth=config["dma_buf_depth"])
        Scoreboard.from_result(
            golden,
            guards=chain.guards,
            regions=chain.regions,
            context=_run_context(config, window, repro),
            log=dut._log,
        ).compare_reset_prefix(bringup.pin.transactions())

    dispose_run(bringup, test=window, log=dut._log, reset_truncated=REVIEW, repro=repro)
    _commit_l1_window(config, test=window, checkers_ok=True, scoreboard_ok=True)

    await _release_reset(dut)

    for _ in range(_POST_RELEASE_IDLE_CYCLES):
        await RisingEdge(dut.clk)
        assert _done(dut) == 1, (
            f"{window}: DONE not 1 after release with no fresh START "
            f"(spontaneous resume). {repro}"
        )
        assert _bus_gnt(dut) == 0, (
            f"{window}: BUS_GNT set without a fresh BUS_REQ. {repro}"
        )
    if bringup.pin is not None:
        assert len(bringup.pin.transactions()) == txn_count, (
            f"{window}: a new QPI transaction appeared with no fresh START "
            f"(spontaneous resume). {repro}"
        )


# =============================================================================
# TC-START-ACTIVE
# =============================================================================

_ACTIVE_START_TARGETS = (
    ("fetch", (SYS_CONTROL_NEW_FETCH, SYS_CONTROL_FETCH)),
    ("read", (SYS_CONTROL_READ,)),
    ("write", (SYS_CONTROL_WRITE,)),
    ("update", (SYS_CONTROL_UPDATE,)),
)


@cocotb.test()
async def start_while_active(dut):
    """TC-START-ACTIVE: START edges during fetch, read, write, update, stall."""
    test = "TC-START-ACTIVE"
    config = parse_run_config()
    repro = _repro(config, "start_while_active")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=6, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT),
            TcdSpec(transfer_len=5, src_device=1, dest_device=0, pattern=PATTERN_INCREMENT),
        ],
        seed=3001,
    )
    _install_chain(bringup, chain)

    await pulse_start(dut)
    for label, states in _ACTIVE_START_TARGETS:
        await _await_controller_state(dut, states, repro=repro)
        await pulse_start(dut)
        adapter.record_start_result("active_ignored")
        state = int(_controller(dut).curr_state.value)
        assert state != SYS_CONTROL_NEW_FETCH, (
            f"{test}: START during {label} was accepted (curr_state jumped "
            f"to NEW_FETCH). {repro}"
        )

    # STALL: induce a stall via BUS_REQ, inject START while stalled, release.
    await assert_bus_req(dut, hold=True)
    await _await_controller_state(
        dut, (SYS_CONTROL_FETCH, SYS_CONTROL_READ, SYS_CONTROL_WRITE), repro=repro
    )
    await _await_controller_state(dut, (SYS_CONTROL_STALL,), repro=repro)
    await pulse_start(dut)
    adapter.record_start_result("req_gnt_ignored")
    state = int(_controller(dut).curr_state.value)
    assert state == SYS_CONTROL_STALL, (
        f"{test}: START during STALL left STALL early (curr_state={state}). "
        + repro
    )
    await assert_bus_req(dut, hold=False)

    timeout_ns = _auto_timeout_ns(chain)
    try:
        await with_timeout(_wait_for_done_pulse(dut), timeout_ns, "ns")
    except SimTimeoutError:
        raise AssertionError(
            f"{test}: DONE did not return within {timeout_ns} ns after "
            "ignored active-time START edges. " + repro
        )
    await _compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)

    # A later command still needs a fresh edge in IDLE.
    bringup.clear()
    second = build_directed_chain(
        [TcdSpec(transfer_len=3, src_device=1, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=3002,
    )
    _install_chain(bringup, second)
    await pulse_start(dut)
    await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(second), "ns")
    await _compare_and_dispose(
        dut, bringup, second, test=f"{test}[fresh-edge]", config=config, repro=repro
    )


# =============================================================================
# TC-START-HELD
# =============================================================================


@cocotb.test()
async def start_held_high(dut):
    """TC-START-HELD: hold raw START through acceptance and completion."""
    test = "TC-START-HELD"
    config = parse_run_config()
    repro = _repro(config, "start_held_high")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=4, pattern=PATTERN_INCREMENT)], seed=3010
    )
    _install_chain(bringup, chain)

    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << START_BIT)
    adapter.record_start_result("held_high_single")
    try:
        await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(chain), "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}: DONE did not return with START held high. " + repro)

    # Raw START is still held; confirm no unintended restart.
    for _ in range(50):
        await RisingEdge(dut.clk)
        assert _done(dut) == 1, (
            f"{test}: unintended restart while START held high. " + repro
        )
    await _compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)

    # Lower then re-raise: a fresh edge still starts a new run normally.
    dut.ui_in.value = 0
    await RisingEdge(dut.clk)
    bringup.clear()
    second = build_directed_chain(
        [TcdSpec(transfer_len=3, src_device=1, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=3011,
    )
    _install_chain(bringup, second)
    await pulse_start(dut)
    await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(second), "ns")
    await _compare_and_dispose(
        dut, bringup, second, test=f"{test}[refresh]", config=config, repro=repro
    )


# =============================================================================
# TC-START-PHASE
# =============================================================================

_START_LONG_HOLD_NS = 35.0  # capture-required: >= 3 full clk periods
_START_SHORT_HOLD_NS = 2.0  # capture-uncertain: sub-period
_START_PHASE_WINDOW_CYCLES = 40


async def _raw_start_pulse(dut, *, phase_ns: float, hold_ns: float) -> None:
    """Assert then release raw START at a sub-cycle *phase_ns* after a clk edge."""
    await RisingEdge(dut.clk)
    await Timer(max(phase_ns, 0.001), unit="ns")
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << START_BIT)
    await Timer(hold_ns, unit="ns")
    current = int(dut.ui_in.value)
    dut.ui_in.value = current & ~(1 << START_BIT) & 0xFF


async def _count_accepted_starts(dut, cycles: int) -> int:
    """Count IDLE -> NEW_FETCH transitions over the next *cycles* clk edges."""
    count = 0
    controller = _controller(dut)
    prev = int(controller.curr_state.value)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        state = int(controller.curr_state.value)
        if prev == SYS_CONTROL_IDLE and state == SYS_CONTROL_NEW_FETCH:
            count += 1
        prev = state
        await NextTimeStep()
    return count


async def _raw_start_pulse_and_count(
    dut, *, phase_ns: float, hold_ns: float, cycles: int = _START_PHASE_WINDOW_CYCLES
) -> int:
    """Pulse raw START and count accepts overlapping the hold window."""
    counter = cocotb.start_soon(_count_accepted_starts(dut, cycles))
    await _raw_start_pulse(dut, phase_ns=phase_ns, hold_ns=hold_ns)
    return await counter


@cocotb.test()
async def start_phase_sweep(dut):
    """TC-START-PHASE: sweep START assertion phase and pulse width."""
    test = "TC-START-PHASE"
    config = parse_run_config()
    repro = _repro(config, "start_phase_sweep")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    period_ns = resolve_clk_period_ns()
    chain = build_directed_chain([TcdSpec(transfer_len=1, pattern=PATTERN_INCREMENT)], seed=3501)
    accepted_short = 0
    uncaptured_short = 0

    for phase_bin in START_PHASE_BINS:
        phase_ns = offset_for_phase_bin(phase_bin, period_ns)
        assert classify_start_phase(phase_ns, period_ns) == phase_bin
        # Capture-required: long hold must always produce exactly one pulse.
        bringup = await bring_up_top(dut)
        _install_chain(bringup, chain)
        window = f"{test}[long,phase={phase_bin}]"
        count = await _raw_start_pulse_and_count(
            dut, phase_ns=phase_ns, hold_ns=_START_LONG_HOLD_NS
        )
        assert count == 1, (
            f"{window}: capture-required pulse produced {count} accepted "
            f"edge(s), expected exactly 1. {repro}"
        )
        adapter.record_start_phase(phase_bin)
        adapter.record_start_result("idle_accepted")
        try:
            await with_timeout(
                _wait_until_done(dut), _auto_timeout_ns(chain), "ns"
            )
        except SimTimeoutError:
            raise AssertionError(f"{window}: DONE never returned. {repro}")
        await _compare_and_dispose(dut, bringup, chain, test=window, config=config, repro=repro)

        # Capture-uncertain: short hold gives 0 or 1 edges, never more, and
        # never a partial or repeated command.
        bringup = await bring_up_top(dut)
        _install_chain(bringup, chain)
        window = f"{test}[short,phase={phase_bin}]"
        count = await _raw_start_pulse_and_count(
            dut, phase_ns=phase_ns, hold_ns=_START_SHORT_HOLD_NS
        )
        assert count in (0, 1), (
            f"{window}: short pulse produced {count} accepted edge(s), "
            f"expected 0 or 1. {repro}"
        )
        adapter.record_start_phase(phase_bin)
        if count == 1:
            accepted_short += 1
            adapter.record_start_result("idle_accepted")
            try:
                await with_timeout(
                    _wait_until_done(dut), _auto_timeout_ns(chain), "ns"
                )
            except SimTimeoutError:
                raise AssertionError(
                    f"{window}: captured short pulse never returned DONE "
                    f"(partial command). {repro}"
                )
            await _compare_and_dispose(dut, bringup, chain, test=window, config=config, repro=repro)
        else:
            uncaptured_short += 1
            adapter.record_start_result("idle_uncaptured")
            assert _done(dut) == 1, (
                f"{window}: uncaptured short pulse still left DONE low. {repro}"
            )
            dispose_run(bringup, test=window, log=dut._log, repro=repro)
            _commit_l1_window(config, test=window, checkers_ok=True, scoreboard_ok=True)

    if uncaptured_short == 0:
        # Mid-cycle 1 ns pulse cannot reach a rising clk sampling edge.
        bringup = await bring_up_top(dut)
        _install_chain(bringup, chain)
        window = f"{test}[short,forced-uncaptured]"
        count = await _raw_start_pulse_and_count(
            dut, phase_ns=period_ns / 2.0, hold_ns=1.0
        )
        assert count == 0, (
            f"{window}: mid-cycle 1 ns pulse produced {count} accepted "
            f"edge(s), expected 0. {repro}"
        )
        adapter.record_start_phase(classify_start_phase(period_ns / 2.0, period_ns))
        adapter.record_start_result("idle_uncaptured")
        uncaptured_short = 1
        dispose_run(bringup, test=window, log=dut._log, repro=repro)
        _commit_l1_window(config, test=window, checkers_ok=True, scoreboard_ok=True)

    dut._log.info(
        "%s passed: %d capture-required phase(s) (each single-pulse), short "
        "pulses captured=%d uncaptured=%d",
        test,
        len(START_PHASE_BINS),
        accepted_short,
        uncaptured_short,
    )


# =============================================================================
# TC-BUS-IDLE
# =============================================================================


@cocotb.test()
async def bus_req_from_idle(dut):
    """TC-BUS-IDLE: BUS_REQ assert/release in IDLE, START while req/grant high."""
    test = "TC-BUS-IDLE"
    config = parse_run_config()
    repro = _repro(config, "bus_req_from_idle")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    assert _done(dut) == 1 and _bus_gnt(dut) == 0, (
        f"{test}: bring-up did not settle in IDLE before BUS_REQ. " + repro
    )

    await assert_bus_req(dut, hold=True)
    await _record_bus_after_sync(dut, adapter, observed_state=SYS_CONTROL_IDLE)
    await _await_controller_state(dut, (SYS_CONTROL_STALL,), repro=repro)
    for _ in range(_GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _bus_gnt(dut) == 1:
            break
    else:
        raise AssertionError(f"{test}: BUS_GNT never asserted from IDLE. {repro}")
    assert int(dut.uio_oe.value) == 0, f"{test}: uio_oe not clear under grant. {repro}"
    assert _done(dut) == 1, f"{test}: DONE dropped while stalled from IDLE. {repro}"
    _assert_no_ordinary_qlaunch(bringup, window=f"{test}[grant-park]", repro=repro)

    # START while request/grant is high is ignored and not queued.
    await pulse_start(dut)
    adapter.record_start_result("req_gnt_ignored")
    state = int(_controller(dut).curr_state.value)
    assert state == SYS_CONTROL_STALL, (
        f"{test}: START accepted while BUS_REQ/BUS_GNT active (curr_state="
        f"{state}, expected STALL). {repro}"
    )

    await assert_bus_req(dut, hold=False)
    for _ in range(_GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _bus_gnt(dut) == 0:
            break
    else:
        raise AssertionError(f"{test}: BUS_GNT never released. {repro}")
    adapter.record_bus_resume(SYS_CONTROL_IDLE)
    assert _done(dut) == 1, f"{test}: DONE not 1 after release from idle stall. {repro}"

    # A fresh edge in IDLE now starts normally.
    chain = build_directed_chain([TcdSpec(transfer_len=3, pattern=PATTERN_INCREMENT)], seed=4001)
    _install_chain(bringup, chain)
    await pulse_start(dut)
    await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(chain), "ns")
    await _compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)


# =============================================================================
# TC-BUS-BOUNDARY
# =============================================================================


@cocotb.test()
async def bus_req_at_boundaries(dut):
    """TC-BUS-BOUNDARY: BUS_REQ in NEW_FETCH, NEW_OP, and UPDATE."""
    test = "TC-BUS-BOUNDARY"
    config = parse_run_config()
    repro = _repro(config, "bus_req_at_boundaries")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=5, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT),
            TcdSpec(transfer_len=3, src_device=1, dest_device=0, pattern=PATTERN_INCREMENT),
        ],
        seed=4101,
    )
    _install_chain(bringup, chain)

    # NEW_FETCH: BUS_REQ synchronized to land exactly as the head fetch opens.
    await _pulse_start_with_bus_req_at_new_fetch(dut)
    await _bus_req_cycle(
        dut,
        None,
        repro=repro,
        adapter=adapter,
        observed_state="NEW_FETCH",
        resume_origin=SYS_CONTROL_NEW_FETCH,
    )

    # NEW_OP / UPDATE: inject so the synchronized edge lands in the one-cycle
    # bin (await-then-assert would sample FETCH / WRITE instead).
    await _bus_req_targeted(
        dut,
        repro=repro,
        adapter=adapter,
        target_state="NEW_OP",
        resume_origin=SYS_CONTROL_NEW_OP,
    )
    await _bus_req_targeted(
        dut,
        repro=repro,
        adapter=adapter,
        target_state="UPDATE",
        resume_origin=SYS_CONTROL_UPDATE,
    )

    try:
        await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(chain), "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}: DONE did not return after boundary stalls. " + repro)
    await _compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)


# =============================================================================
# TC-BUS-ACTIVE
# =============================================================================


@cocotb.test()
async def bus_req_during_transaction(dut):
    """TC-BUS-ACTIVE: BUS_REQ during fetch, payload read, and payload write."""
    test = "TC-BUS-ACTIVE"
    config = parse_run_config()
    repro = _repro(config, "bus_req_during_transaction")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [TcdSpec(transfer_len=5, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=4201,
    )
    _install_chain(bringup, chain)

    await pulse_start(dut)
    await _bus_req_cycle(
        dut,
        _await_controller_state(dut, (SYS_CONTROL_FETCH,), repro=repro),
        repro=repro,
        adapter=adapter,
    )
    await _bus_req_cycle(
        dut,
        _await_controller_state(dut, (SYS_CONTROL_READ,), repro=repro),
        repro=repro,
        adapter=adapter,
    )
    await _bus_req_cycle(
        dut,
        _await_controller_state(dut, (SYS_CONTROL_WRITE,), repro=repro),
        repro=repro,
        adapter=adapter,
    )

    try:
        await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(chain), "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}: DONE did not return after active-time stalls. " + repro)
    await _compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)


# =============================================================================
# TC-BUS-PHASE
# =============================================================================

_BUS_PHASE_SEQUENCE = (
    ("CS_ON", _QSPI_CS_ON, "inject"),
    ("command", _QSPI_SEND_CMD_1, "await"),
    ("address", _QSPI_SEND_ADDR, "await"),
    ("write_data", _QSPI_WRITE_DATA, "await"),
    ("dummy", _QSPI_WAIT, "await"),
    ("read_data", _QSPI_READ_DATA, "await"),
    ("padding", _QSPI_SCLK_OFF, "await"),
    ("CS_OFF", _QSPI_CS_OFF, "inject"),
)


@cocotb.test()
async def bus_req_during_qpi_phase(dut):
    """TC-BUS-PHASE: BUS_REQ during each externally visible QPI phase."""
    test = "TC-BUS-PHASE"
    config = parse_run_config()
    repro = _repro(config, "bus_req_during_qpi_phase")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    # Two multi-chunk TCDs give many fetch/read/write CE# intervals; each
    # sequential target below lands on whichever upcoming interval is the
    # next to actually carry that phase (_await_engine_state polls forward
    # through intervals that lack it, e.g. WRITE_DATA has no WAIT/READ_DATA).
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=8, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT),
            TcdSpec(transfer_len=8, src_device=1, dest_device=0, pattern=PATTERN_INCREMENT),
        ],
        seed=4301,
    )
    _install_chain(bringup, chain)

    await pulse_start(dut)
    for label, phase, how in _BUS_PHASE_SEQUENCE:
        if how == "inject":
            await _bus_req_targeted(
                dut, repro=repro, adapter=adapter, target_phase=phase
            )
            continue
        await _bus_req_cycle(
            dut,
            _await_engine_state(dut, (phase,), repro=repro),
            repro=repro,
            adapter=adapter,
            observed_phase=phase,
        )

    try:
        await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(chain), "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}: DONE did not return after phase-time stalls. " + repro)
    await _compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)


# =============================================================================
# TC-BUS-REPEAT
# =============================================================================


@cocotb.test()
async def bus_req_repeat_cycles(dut):
    """TC-BUS-REPEAT: multiple request/grant/release cycles in one chain."""
    test = "TC-BUS-REPEAT"
    config = parse_run_config()
    repro = _repro(config, "bus_req_repeat_cycles")
    dut._log.info(repro)

    adapter = _l1_adapter(config, test=test)
    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=6, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT),
            TcdSpec(transfer_len=5, src_device=1, dest_device=0, pattern=PATTERN_INCREMENT),
        ],
        seed=4401,
    )
    _install_chain(bringup, chain)

    await pulse_start(dut)
    # Head fetch.
    await _bus_req_cycle(
        dut,
        _await_controller_state(dut, (SYS_CONTROL_FETCH,), repro=repro),
        repro=repro,
        adapter=adapter,
    )
    # First payload write.
    await _bus_req_cycle(
        dut,
        _await_controller_state(dut, (SYS_CONTROL_WRITE,), repro=repro),
        repro=repro,
        adapter=adapter,
    )
    # Second descriptor's fetch.
    await _bus_req_cycle(
        dut,
        _await_controller_state(dut, (SYS_CONTROL_FETCH,), repro=repro),
        repro=repro,
        adapter=adapter,
    )
    # Quit descriptor's fetch: a request adjacent to completion.
    await _bus_req_cycle(
        dut,
        _await_controller_state(dut, (SYS_CONTROL_FETCH,), repro=repro),
        repro=repro,
        adapter=adapter,
    )

    try:
        await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(chain), "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}: DONE did not return after repeat cycles. " + repro)
    await _compare_and_dispose(dut, bringup, chain, test=test, config=config, repro=repro)


# =============================================================================
# TC-RESET-IDLE
# =============================================================================


@cocotb.test()
async def reset_from_idle(dut):
    """TC-RESET-IDLE: reset from IDLE and while BUS_GNT is active."""
    test = "TC-RESET-IDLE"
    config = parse_run_config()
    repro = _repro(config, "reset_from_idle")
    dut._log.info(repro)

    # -- sub-case 1: reset directly from IDLE ------------------------------
    bringup = await bring_up_top(dut)
    assert _done(dut) == 1 and _bus_gnt(dut) == 0, (
        f"{test}[idle]: bring-up did not settle in IDLE before reset. " + repro
    )
    adapter = _l1_adapter(config, test=f"{test}[idle]")
    state, phase = _sample_ctrl_qpi(dut)
    adapter.record_reset(state, phase)
    for agent in bringup.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_reset_safe(dut, window=f"{test}[idle]")
    dispose_run(
        bringup, test=f"{test}[idle]", log=dut._log, reset_truncated=REVIEW, repro=repro
    )
    _commit_l1_window(config, test=f"{test}[idle]", checkers_ok=True, scoreboard_ok=True)
    await _release_reset(dut)

    # Post-reset START uses the fixed head, independent of stale state.
    bringup.clear()
    chain = build_directed_chain([TcdSpec(transfer_len=3, pattern=PATTERN_INCREMENT)], seed=5001)
    _install_chain(bringup, chain)
    await pulse_start(dut)
    await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(chain), "ns")
    await _compare_and_dispose(dut, bringup, chain, test=f"{test}[idle]", config=config, repro=repro)

    # -- sub-case 2: reset while BUS_GNT is active --------------------------
    bringup2 = await bring_up_top(dut)
    await assert_bus_req(dut, hold=True)
    for _ in range(_GRANT_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _bus_gnt(dut) == 1:
            break
    else:
        raise AssertionError(f"{test}[granted]: BUS_GNT never asserted. {repro}")
    assert int(dut.uio_oe.value) == 0, (
        f"{test}[granted]: uio_oe not clear under BUS_GNT. {repro}"
    )
    _assert_no_ordinary_qlaunch(bringup2, window=f"{test}[granted-park]", repro=repro)

    # Clear bring-up residue so [granted] dispose covers only the forced-reset
    # window (REVIEW for any RESET-TRUNCATED findings).
    bringup2.clear()
    adapter = _l1_adapter(config, test=f"{test}[granted]")
    state, phase = _sample_ctrl_qpi(dut)
    adapter.record_reset(state, phase)
    for agent in bringup2.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_reset_safe(dut, window=f"{test}[granted]")
    dispose_run(
        bringup2,
        test=f"{test}[granted]",
        log=dut._log,
        reset_truncated=REVIEW,
        repro=repro,
    )
    _commit_l1_window(config, test=f"{test}[granted]", checkers_ok=True, scoreboard_ok=True)
    await _release_reset(dut)

    bringup2.clear()
    second = build_directed_chain(
        [TcdSpec(transfer_len=3, src_device=1, dest_device=1, pattern=PATTERN_INCREMENT)],
        seed=5002,
    )
    _install_chain(bringup2, second)
    await pulse_start(dut)
    await with_timeout(_wait_for_done_pulse(dut), _auto_timeout_ns(second), "ns")
    await _compare_and_dispose(
        dut, bringup2, second, test=f"{test}[granted]", config=config, repro=repro
    )


# =============================================================================
# TC-RESET-ACTIVE
# =============================================================================


@cocotb.test()
async def reset_during_activity(dut):
    """TC-RESET-ACTIVE: reset during every controller state and QPI phase."""
    test = "TC-RESET-ACTIVE"
    config = parse_run_config()
    repro = _repro(config, "reset_during_activity")
    dut._log.info(repro)

    for target in _CONTROLLER_RESET_TARGETS:
        window = f"{test}[state={SYS_CONTROL_STATES[target]}]"
        await _reset_mid_run(
            dut, config, kind="controller", target=target, window=window, repro=repro
        )

    for target in _ENGINE_PHASE_RESET_TARGETS:
        window = f"{test}[phase={QSPI_ENGINE_STATES[target]}]"
        await _reset_mid_run(
            dut, config, kind="engine", target=target, window=window, repro=repro
        )

    dut._log.info(
        "%s passed: %d controller-state and %d QPI-phase reset window(s) "
        "disposed",
        test,
        len(_CONTROLLER_RESET_TARGETS),
        len(_ENGINE_PHASE_RESET_TARGETS),
    )


# =============================================================================
# TC-RESET-REPEAT
# =============================================================================


@cocotb.test()
async def reset_then_identical_rerun(dut):
    """TC-RESET-REPEAT: identical chain twice across a reset boundary."""
    test = "TC-RESET-REPEAT"
    config = parse_run_config()
    repro = _repro(config, "reset_then_identical_rerun")
    dut._log.info(repro)

    bringup = await bring_up_top(dut)
    chain = build_directed_chain(
        [
            TcdSpec(transfer_len=6, src_device=0, dest_device=1, pattern=PATTERN_INCREMENT),
            TcdSpec(transfer_len=4, src_device=1, dest_device=0, pattern=PATTERN_INCREMENT),
        ],
        seed=6001,
    )
    timeout_ns = _auto_timeout_ns(chain)

    # Epoch 1: run to normal quit completion.
    bringup.clear()
    _install_chain(bringup, chain)
    await pulse_start(dut)
    try:
        await with_timeout(_wait_for_done_pulse(dut), timeout_ns, "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}[epoch=1]: DONE did not return. " + repro)
    first_observed = bringup.pin.transactions()
    await _compare_and_dispose(
        dut, bringup, chain, test=f"{test}[epoch=1]", config=config, repro=repro
    )

    # Reset from IDLE; no working state, counter, or pointer may carry over.
    assert _done(dut) == 1 and _bus_gnt(dut) == 0, (
        f"{test}: not settled in IDLE after epoch 1 before reset. " + repro
    )
    adapter = _l1_adapter(config, test=f"{test}[reset]")
    state, phase = _sample_ctrl_qpi(dut)
    adapter.record_reset(state, phase)
    for agent in bringup.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_reset_safe(dut, window=f"{test}[reset]")
    dispose_run(
        bringup, test=f"{test}[reset]", log=dut._log, reset_truncated=REVIEW, repro=repro
    )
    _commit_l1_window(config, test=f"{test}[reset]", checkers_ok=True, scoreboard_ok=True)
    await _release_reset(dut)

    # Re-initialize source and destination memory identically, then rerun.
    bringup.clear()
    bringup.clear_transactions()
    _install_chain(bringup, chain)
    await pulse_start(dut)
    try:
        await with_timeout(_wait_for_done_pulse(dut), timeout_ns, "ns")
    except SimTimeoutError:
        raise AssertionError(f"{test}[epoch=2]: DONE did not return. " + repro)
    second_observed = bringup.pin.transactions()
    await _compare_and_dispose(
        dut, bringup, chain, test=f"{test}[epoch=2]", config=config, repro=repro
    )

    Scoreboard.compare_epochs(
        first_observed, second_observed, context=_run_context(config, test, repro)
    )

    dut._log.info(
        "%s passed: two epochs byte-for-byte equal (%d transaction(s) each)",
        test,
        len(first_observed),
    )
