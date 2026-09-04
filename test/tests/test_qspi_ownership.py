"""Shared-bus ownership negative tests (M1).

Each case uses the shared-bus monitor from :func:`common.bringup.bring_up_top`
and either proves a clean parked/legal window or injects one ownership fault
that must produce the matching ``CHK-PIN-*`` / ``Q-*`` ID. Findings while
``rst_n`` is low are ``RESET-TRUNCATED`` and do not count as fails, so every
injection runs after reset release. Stimulus uses TB ``fault_uio_*``
(ASIC-side misbehavior) and, for SIO dual-drive, the model's OE injection
hook - matching how the monitor judges ownership from OE handles, not the
resolved net.

All ``TC-OWN-*`` cases run as **sub-steps** inside one cocotb test
(:func:`ownership_shared_bus_negatives`). They are catalog IDs for the
directed windows, **not** selectable ``TEST_FILTER`` names. A free-running
clock plus always-on monitor is kept for the whole module; splitting across
``@cocotb.test`` entry points cancels the next case on ``RisingEdge`` under
this Icarus/cocotb 2.0.1 stack after the first fault is logged. Full
per-case re-split is deferred past M2 (W5a decision 3).

Sub-steps (not TEST_FILTER targets):
    TC-OWN-BASELINE
    TC-OWN-CS-MUTEX
    TC-OWN-CS-MUTEX-XZ
    TC-OWN-CS-MUTEX-OE
    TC-OWN-FLASH-CS
    TC-OWN-SIO-DUAL
    TC-OWN-SIO-DUAL-SELECTED
    TC-OWN-CS-MUTEX-SELECTED
    TC-OWN-SCK-IDLE
    TC-OWN-SCK-IDLE-CE-X
    TC-OWN-GNT-QUIET-OE-X

Selectable filter:
    TEST_FILTER=ownership_shared_bus_negatives
    TEST_FILTER=gnt_quiet_unresolved_ce_oe
"""

import cocotb
from cocotb.handle import Force, Release
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.clocks import apply_reset
from common.runlog import begin_run
from common.constants import FILL
from common.dispose import dispose_run, expect
from common.host import (
    UIO_FLASH_CS_BIT,
    UIO_PSRAM_CE_BITS,
    UIO_SCK_BIT,
    QpiPassthroughMaster,
    assert_bus_req,
)
from models.psram import (
    QSPI_CMD_FAST_READ,
    QSPI_CMD_WRITE,
    Q_DRIVE_DESEL,
    Q_PHASE,
    SIO_UIO_BITS,
)
from monitors.arbitration import CHK_ARB_GNT_OE, CHK_ARB_GNT_QUIET
from monitors.qspi import (
    CHK_PIN_CS_MUTEX,
    CHK_PIN_FLASH_HIGH,
    CHK_PIN_SCK_PARK,
    CHK_PIN_SIO_OWN,
    Q_MUX,
    Q_SCKIDLE,
    Q_SIO_OWN,
)

EQUAL_SIO_NIBBLE = 0x1  # SIO0=1; equal-value dual drive on one bit

# Real @cocotb.test function name; the only honest TEST_FILTER for this module.
OWNERSHIP_TEST_FILTER = "ownership_shared_bus_negatives"

def _sio_oe_mask(*sio_indices: int) -> int:
    """``uio`` OE mask for the listed SIO indices (default: all four)."""
    indices = sio_indices if sio_indices else range(len(SIO_UIO_BITS))
    mask = 0
    for index in indices:
        mask |= 1 << SIO_UIO_BITS[index]
    return mask

def _pack_sio_uio(nibble: int) -> int:
    """Place a SIO[3:0] nibble onto the corresponding ``uio`` drive bits."""
    value = 0
    for sio_bit, uio_bit in enumerate(SIO_UIO_BITS):
        if (nibble >> sio_bit) & 1:
            value |= 1 << uio_bit
    return value

def _clear_fault(dut) -> None:
    dut.fault_uio_oe.value = 0
    dut.fault_uio_drive.value = 0

async def _park_clean(dut, bringup) -> None:
    """Clear injectors, sync-reset, and start a fresh ownership log window."""
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    _clear_fault(dut)
    for agent in bringup.agents:
        agent.inject_sio_release()
        agent.violations.clear()
        agent.transactions.clear()
    bringup.clear()
    await apply_reset(dut)
    await Timer(20, unit="ns")
    bringup.clear()
    for agent in bringup.agents:
        agent.violations.clear()
        agent.transactions.clear()

async def _await_bus_gnt(dut, *, cycles: int = 32) -> None:
    await assert_bus_req(dut, hold=True)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> 1) & 1:
            return
    raise AssertionError("BUS_GNT did not assert after BUS_REQ")

async def _release_bus_gnt(dut, *, cycles: int = 32) -> None:
    await assert_bus_req(dut, hold=False)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if not ((int(dut.uo_out.value) >> 1) & 1):
            return
    raise AssertionError("BUS_GNT did not drop after BUS_REQ release")

def _assert_detail(bus, check_id: str, *, test: str, detail_substr: str, timing_id: str) -> list:
    """Require recorded events for *check_id* carry the expected detail / timing."""
    events = bus.violations_for(check_id)
    assert events, f"{test}: expected {check_id} events before dispose, observed {bus.summary()}"
    for event in events:
        assert event.timing_id == timing_id, f"{test}: timing id mismatch: {event}"
        assert not event.reset_truncated, f"{test}: finding was RESET-TRUNCATED: {event}"
    details = " | ".join(event.detail for event in events)
    assert detail_substr in details, f"{test}: missing {detail_substr!r} in {details}"
    return events

async def _tc_own_baseline(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-BASELINE: parked idle and legal MCU traffic stay clean."""
    bus = bringup.bus
    psram0, psram1 = bringup.psram0, bringup.psram1
    await _park_clean(dut, bringup)

    await _await_bus_gnt(dut)
    master = QpiPassthroughMaster(dut)
    await master.park()
    psram1.write(0x001000, b"\xDE\xAD")
    await master.frame(0, QSPI_CMD_WRITE, 0x000040, write_data=b"\x11\x22")
    await master.frame(1, QSPI_CMD_FAST_READ, 0x001000, dummy_cycles=6, read_bytes=2)
    await master.park()
    await _release_bus_gnt(dut)
    await Timer(20, unit="ns")

    assert psram0.read(0x000040, 2) == b"\x11\x22"
    dispose_run(bringup, test="TC-OWN-BASELINE", log=dut._log, repro=repro)

async def _tc_own_cs_mutex(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-CS-MUTEX: both RAM CE# low → ``CHK-PIN-CS-MUTEX`` / ``Q-MUX``."""
    bus = bringup.bus
    await _park_clean(dut, bringup)

    ce_mask = (1 << UIO_PSRAM_CE_BITS[0]) | (1 << UIO_PSRAM_CE_BITS[1])
    dut.fault_uio_drive.value = 0
    dut.fault_uio_oe.value = ce_mask
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_CS_MUTEX,
        test="TC-OWN-CS-MUTEX",
        detail_substr="CE# low together",
        timing_id="Q-MUX",
    )
    dispose_run(
        bringup,
        test="TC-OWN-CS-MUTEX",
        expect_fail=[expect(CHK_PIN_CS_MUTEX, count=1), expect(Q_MUX, count=1)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-CS-MUTEX recorded: %s", events[0])
    _clear_fault(dut)
    await Timer(1, unit="ns")

async def _tc_own_flash_cs(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-FLASH-CS: flash CS low under ``~BUS_GNT`` → ``CHK-PIN-FLASH-HIGH``."""
    bus = bringup.bus
    await _park_clean(dut, bringup)

    dut.fault_uio_drive.value = 0
    dut.fault_uio_oe.value = 1 << UIO_FLASH_CS_BIT
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_FLASH_HIGH,
        test="TC-OWN-FLASH-CS",
        detail_substr="flash CS low while ~BUS_GNT",
        timing_id="Q-MUX",
    )
    dispose_run(
        bringup,
        test="TC-OWN-FLASH-CS",
        expect_fail=[expect(CHK_PIN_FLASH_HIGH, count=1), expect(Q_MUX, count=1)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-FLASH-CS recorded: %s", events[0])
    _clear_fault(dut)
    await Timer(1, unit="ns")

async def _tc_own_sio_dual(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-SIO-DUAL: ASIC-FAULT + device OE on SIO0 → ``CHK-PIN-SIO-OWN``."""
    bus = bringup.bus
    agent = bringup.psram0.agent
    await _park_clean(dut, bringup)

    assert not agent.selected
    assert not agent.oe

    agent.inject_sio_drive(EQUAL_SIO_NIBBLE)
    dut.fault_uio_drive.value = _pack_sio_uio(EQUAL_SIO_NIBBLE)
    dut.fault_uio_oe.value = _sio_oe_mask(0)
    await Timer(1, unit="ns")

    assert agent.oe and agent.driven_nibble == EQUAL_SIO_NIBBLE
    events = _assert_detail(
        bus,
        CHK_PIN_SIO_OWN,
        test="TC-OWN-SIO-DUAL",
        detail_substr="equal driven values still fail",
        timing_id="Q-SIO-OWN",
    )
    assert any("SIO0" in event.detail for event in events), f"SIO0 missing: {events}"
    # Deselected inject_sio_drive → Q-DRIVE-DESEL; ASIC fault dual-drive → CHK-PIN-SIO-OWN.
    agent.inject_sio_release()
    _clear_fault(dut)
    await Timer(1, unit="ns")
    dispose_run(
        bringup,
        test="TC-OWN-SIO-DUAL",
        expect_fail=[
            expect(CHK_PIN_SIO_OWN, count=4),
            expect(Q_SIO_OWN, count=4),
            expect(Q_DRIVE_DESEL, count=1),
        ],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-SIO-DUAL recorded: %s", events[0])

async def _tc_own_sio_dual_selected(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-SIO-DUAL-SELECTED: dual SIO OE while CE# is low.

    Deselected dual-drive is ``TC-OWN-SIO-DUAL`` (includes ``Q-DRIVE-DESEL``).
    This window keeps the device selected so ``Q-SIO-OWN`` / ``CHK-PIN-SIO-OWN``
    fire without ``Q-DRIVE-DESEL``. CE# stays low through dispose: that is
    ``Q-PHASE`` (CE# rose before command/address completed), not a clean
    ownership pass.
    """
    bus = bringup.bus
    agent = bringup.psram0.agent
    await _park_clean(dut, bringup)
    await _await_bus_gnt(dut)
    master = QpiPassthroughMaster(dut)
    await master.park()
    await master.open(0)
    assert agent.selected, "TC-OWN-SIO-DUAL-SELECTED requires CE# low"

    agent.inject_sio_drive(EQUAL_SIO_NIBBLE)
    dut.fault_uio_drive.value = _pack_sio_uio(EQUAL_SIO_NIBBLE)
    dut.fault_uio_oe.value = _sio_oe_mask(0)
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_SIO_OWN,
        test="TC-OWN-SIO-DUAL-SELECTED",
        detail_substr="equal driven values still fail",
        timing_id="Q-SIO-OWN",
    )
    agent.inject_sio_release()
    _clear_fault(dut)
    # Complete the command phase so dispose of the still-low CE# is Q-PHASE,
    # not a diagnostic incomplete-window note.
    await master.send_opcode(QSPI_CMD_WRITE)
    dispose_run(
        bringup,
        test="TC-OWN-SIO-DUAL-SELECTED",
        expect_fail=[
            expect(CHK_PIN_SIO_OWN, count=1),
            expect(Q_SIO_OWN, count=1),
            expect(Q_PHASE, count=1),
        ],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-SIO-DUAL-SELECTED recorded: %s", events[0])

async def _tc_own_cs_mutex_selected(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-CS-MUTEX-SELECTED: second CE# low while PSRAM0 is selected.

    Fault-pulling CE1 fights the MCU keeper (CE1 still high) and yields X/Z,
    which is already ``TC-OWN-CS-MUTEX-XZ``. Here the MCU drives both RAM CE#
    low so ``Q-MUX`` records known dual-select. CE1 is raised again before
    ``send_opcode`` so the mutex count stays 1. Dispose leaves CE0 low, so
    ``Q-PHASE`` fires on PSRAM0. Raising CE1 aborts PSRAM1 after 0 command
    nibbles, so ``Q-PHASE`` count is 2.
    """
    bus = bringup.bus
    await _park_clean(dut, bringup)
    await _await_bus_gnt(dut)
    master = QpiPassthroughMaster(dut)
    await master.park()
    await master.open(0)

    master._set_bit(UIO_PSRAM_CE_BITS[1], 0)
    master._apply()
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_CS_MUTEX,
        test="TC-OWN-CS-MUTEX-SELECTED",
        detail_substr="CE# low together",
        timing_id="Q-MUX",
    )
    master._set_bit(UIO_PSRAM_CE_BITS[1], 1)
    master._apply()
    await master.send_opcode(QSPI_CMD_WRITE)
    dispose_run(
        bringup,
        test="TC-OWN-CS-MUTEX-SELECTED",
        expect_fail=[
            expect(CHK_PIN_CS_MUTEX, count=1),
            expect(Q_MUX, count=1),
            expect(Q_PHASE, count=2),
        ],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-CS-MUTEX-SELECTED recorded: %s", events[0])

async def _tc_own_sck_idle(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-SCK-IDLE: SCK high while every CS high → ``CHK-PIN-SCK-PARK``."""
    bus = bringup.bus
    await _park_clean(dut, bringup)

    dut.fault_uio_drive.value = 1 << UIO_SCK_BIT
    dut.fault_uio_oe.value = 1 << UIO_SCK_BIT
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_SCK_PARK,
        test="TC-OWN-SCK-IDLE",
        detail_substr="SCK high while no device is selected",
        timing_id="Q-SCKIDLE",
    )
    dispose_run(
        bringup,
        test="TC-OWN-SCK-IDLE",
        expect_fail=[expect(CHK_PIN_SCK_PARK, count=1), expect(Q_SCKIDLE, count=1)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-SCK-IDLE recorded: %s", events[0])
    _clear_fault(dut)
    await Timer(1, unit="ns")

async def _tc_own_cs_mutex_xz(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-CS-MUTEX-XZ: both RAM CE# X → ``Q-MUX`` / ``CHK-PIN-CS-MUTEX``.

    ASIC keeper drives both CE# high; host drives both low so the resolved
    nets are X. Dual-select must fail even though neither CE# is exactly 0.
    """
    bus = bringup.bus
    await _park_clean(dut, bringup)

    # ASIC keeper drives both CE# high. Host drives both low so the nets are X
    # (fault_uio would mask ASIC OE and resolve to a clean 0).
    ce_mask = (1 << UIO_PSRAM_CE_BITS[0]) | (1 << UIO_PSRAM_CE_BITS[1])
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = ce_mask
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_CS_MUTEX,
        test="TC-OWN-CS-MUTEX-XZ",
        detail_substr="unresolved CE#",
        timing_id=Q_MUX,
    )
    dispose_run(
        bringup,
        test="TC-OWN-CS-MUTEX-XZ",
        expect_fail=[expect(CHK_PIN_CS_MUTEX, count=1), expect(Q_MUX, count=1)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-CS-MUTEX-XZ recorded: %s", events[0])
    dut.host_uio_oe.value = 0
    dut.host_uio_drive.value = 0
    await Timer(1, unit="ns")

async def _tc_own_cs_mutex_oe(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-CS-MUTEX-OE: both CE# OE=1 with out=0 → ``Q-MUX``.

    ASIC keeper already enables all CS OEs. Forcing both RAM CS outputs low
    is dual-select even if the resolved nets were not independently X.
    """
    bus = bringup.bus
    await _park_clean(dut, bringup)

    # Keep flash CS high; drive both RAM CS outputs low while OE stays 1.
    dut.uio_out.value = Force(1 << UIO_FLASH_CS_BIT)
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_CS_MUTEX,
        test="TC-OWN-CS-MUTEX-OE",
        detail_substr="OE=1 with out=0",
        timing_id=Q_MUX,
    )
    dispose_run(
        bringup,
        test="TC-OWN-CS-MUTEX-OE",
        expect_fail=[expect(CHK_PIN_CS_MUTEX, count=1), expect(Q_MUX, count=1)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-CS-MUTEX-OE recorded: %s", events[0])
    dut.uio_out.value = Release()
    await Timer(1, unit="ns")

async def _tc_own_sck_idle_ce_x(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-SCK-IDLE-CE-X: keeper + one CE# X still judges ``Q-SCKIDLE``.

    Park must not skip just because a CE# is not a clean 1. ASIC remains
    keeper (``~BUS_GNT``); one CE# is dual-driven to X while SCK is forced high.
    """
    bus = bringup.bus
    await _park_clean(dut, bringup)

    # Host vs ASIC on RAM A CE# → X (not a known select). Fault clocks SCK high.
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 1 << UIO_PSRAM_CE_BITS[0]
    dut.fault_uio_drive.value = 1 << UIO_SCK_BIT
    dut.fault_uio_oe.value = 1 << UIO_SCK_BIT
    await Timer(1, unit="ns")

    events = _assert_detail(
        bus,
        CHK_PIN_SCK_PARK,
        test="TC-OWN-SCK-IDLE-CE-X",
        detail_substr="SCK high while no device is selected",
        timing_id=Q_SCKIDLE,
    )
    dispose_run(
        bringup,
        test="TC-OWN-SCK-IDLE-CE-X",
        expect_fail=[expect(CHK_PIN_SCK_PARK, count=1), expect(Q_SCKIDLE, count=1)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-SCK-IDLE-CE-X recorded: %s", events[0])
    dut.host_uio_oe.value = 0
    dut.host_uio_drive.value = 0
    _clear_fault(dut)
    await Timer(1, unit="ns")

async def _tc_gnt_quiet_ce_oe_x(dut, bringup, repro: str) -> None:
    """Sub-step TC-OWN-GNT-QUIET-OE-X: X on CE# OE while granted fails GNT-QUIET.

    ``BUS_GNT=1``; Force RAM A CE# OE (uio[6]) to X so
    ``_asic_drives_low`` cannot skip the sample. ``CHK-ARB-GNT-OE`` also
    fires because the whole ``uio_oe`` vector is no longer 0.
    """
    arb = bringup.arbitration
    assert arb is not None, "TC-OWN-GNT-QUIET-OE-X requires ArbitrationMonitor"
    await _park_clean(dut, bringup)
    await _await_bus_gnt(dut)
    await RisingEdge(dut.clk)

    # MSB-first 8-bit string: bit 6 (RAM A CE#) is the second character.
    dut.uio_oe.value = Force("0x000000")
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)

    events = arb.violations_for(CHK_ARB_GNT_QUIET)
    assert events, (
        f"TC-OWN-GNT-QUIET-OE-X: expected {CHK_ARB_GNT_QUIET}, "
        f"observed {arb.summary()}"
    )
    details = " | ".join(event.detail for event in events)
    assert "unresolved" in details, (
        f"TC-OWN-GNT-QUIET-OE-X: missing unresolved OE detail in {details}"
    )
    dispose_run(
        bringup,
        test="TC-OWN-GNT-QUIET-OE-X",
        expect_fail=[
            expect(CHK_ARB_GNT_QUIET, count=1),
            expect(CHK_ARB_GNT_OE, count=1),
        ],
        log=dut._log,
        repro=repro,
    )
    # Do not Release() a Force on DUT ``uio_oe``: Icarus 14 segfaults. This is
    # the last sub-step, so the forced X may stay until sim teardown.
    await Timer(1, unit="ns")

@cocotb.test()
async def ownership_shared_bus_negatives(dut):
    """Run TC-OWN-* ownership sub-steps in one consolidated cocotb test."""
    config, repro = begin_run(
        dut, "ownership_shared_bus_negatives", test="TC-OWN"
    )

    # Ownership suite only needs SharedBusMonitor; other always-on monitors stay
    # off so intentional fault injectors do not trip unrelated CHK-* rows.
    # ce_monitor stays off by design; Q-SIO-OWN is judged via CHK-PIN-SIO-OWN
    # under the delay-aware model OE path when TIMING_PROFILE=nominal.
    bringup = await bring_up_top(
        dut,
        fill=FILL,
        ce_monitor=False,
        handshake_monitor=False,
        pin_monitor=False,
        arbitration_monitor=True,
        controller_monitor=False,
    )
    assert bringup.bus is not None, "ownership suite requires SharedBusMonitor"
    assert bringup.timing_profile in ("nominal", "sweep", "ideal"), (
        f"unexpected TIMING_PROFILE={bringup.timing_profile!r}"
    )
    dut._log.info(
        "W3b Q-SIO-OWN delay-rerun under TIMING_PROFILE=%s (ce_monitor=off)",
        bringup.timing_profile,
    )

    await _tc_own_baseline(dut, bringup, repro)
    await _tc_own_cs_mutex(dut, bringup, repro)
    await _tc_own_cs_mutex_xz(dut, bringup, repro)
    await _tc_own_cs_mutex_oe(dut, bringup, repro)
    await _tc_own_flash_cs(dut, bringup, repro)
    await _tc_own_sio_dual(dut, bringup, repro)
    await _tc_own_sio_dual_selected(dut, bringup, repro)
    await _tc_own_cs_mutex_selected(dut, bringup, repro)
    await _tc_own_sck_idle(dut, bringup, repro)
    await _tc_own_sck_idle_ce_x(dut, bringup, repro)
    await _tc_gnt_quiet_ce_oe_x(dut, bringup, repro)

    dut._log.info(
        "TC-OWN suite passed: BASELINE, CS-MUTEX, CS-MUTEX-XZ, CS-MUTEX-OE, FLASH-CS, "
        "SIO-DUAL, SIO-DUAL-SELECTED, CS-MUTEX-SELECTED, SCK-IDLE, SCK-IDLE-CE-X, "
        "GNT-QUIET-OE-X"
    )
