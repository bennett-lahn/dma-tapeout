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
    TC-OWN-FLASH-CS
    TC-OWN-SIO-DUAL
    TC-OWN-SCK-IDLE

Selectable filter:
    TEST_FILTER=ownership_shared_bus_negatives
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.clocks import apply_reset
from common.config import parse_run_config
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
    SIO_UIO_BITS,
)
from monitors.qspi import (
    CHK_PIN_CS_MUTEX,
    CHK_PIN_FLASH_HIGH,
    CHK_PIN_SCK_PARK,
    CHK_PIN_SIO_OWN,
)

FILL = 0x00
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


def _repro(config: dict) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_ownership "
        "TEST_FILTER={filter}"
    ).format(
        level=config["level"],
        sim=config["sim"],
        seed=config["seed"],
        filter=OWNERSHIP_TEST_FILTER,
    )


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
    dispose_run(bringup, test="TC-OWN-BASELINE (park)", log=dut._log, repro=repro)

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
        expect_fail=[expect(CHK_PIN_CS_MUTEX)],
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
        expect_fail=[expect(CHK_PIN_FLASH_HIGH)],
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
    dispose_run(
        bringup,
        test="TC-OWN-SIO-DUAL",
        expect_fail=[expect(CHK_PIN_SIO_OWN), expect(Q_DRIVE_DESEL)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-SIO-DUAL recorded: %s", events[0])

    agent.inject_sio_release()
    _clear_fault(dut)
    await Timer(1, unit="ns")


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
        expect_fail=[expect(CHK_PIN_SCK_PARK)],
        log=dut._log,
        repro=repro,
    )
    dut._log.info("TC-OWN-SCK-IDLE recorded: %s", events[0])
    _clear_fault(dut)
    await Timer(1, unit="ns")


@cocotb.test()
async def ownership_shared_bus_negatives(dut):
    """Run TC-OWN-* ownership sub-steps in one consolidated cocotb test."""
    config = parse_run_config()
    repro = _repro(config)
    dut._log.info(
        "SEED=%d LEVEL=%s SIM=%s", config["seed"], config["level"], config["sim"]
    )
    dut._log.info(repro)

    # Ownership suite only needs SharedBusMonitor; other always-on monitors stay
    # off so intentional fault injectors do not trip unrelated CHK-* rows.
    bringup = await bring_up_top(
        dut,
        fill=FILL,
        ce_monitor=False,
        handshake_monitor=False,
        pin_monitor=False,
        arbitration_monitor=False,
        controller_monitor=False,
    )
    assert bringup.bus is not None, "ownership suite requires SharedBusMonitor"

    await _tc_own_baseline(dut, bringup, repro)
    await _tc_own_cs_mutex(dut, bringup, repro)
    await _tc_own_flash_cs(dut, bringup, repro)
    await _tc_own_sio_dual(dut, bringup, repro)
    await _tc_own_sck_idle(dut, bringup, repro)

    dut._log.info(
        "TC-OWN suite passed: BASELINE, CS-MUTEX, FLASH-CS, SIO-DUAL, SCK-IDLE"
    )
