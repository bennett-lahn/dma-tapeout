"""Shared-bus ownership negative tests (M1).

Each case uses :func:`monitors.qspi.start_shared_bus_monitor` and either proves
a clean parked/legal window or injects one ownership fault that must produce
the matching ``CHK-PIN-*`` / ``Q-*`` ID. Findings while ``rst_n`` is low are
``RESET-TRUNCATED`` and do not count as fails, so every injection runs after
reset release. Stimulus uses TB ``fault_uio_*`` (ASIC-side misbehavior) and,
for SIO dual-drive, the model's OE injection hook - matching how the monitor
judges ownership from OE handles, not the resolved net.

All ``TC-OWN-*`` cases run in one cocotb test. A free-running clock plus
always-on monitor is kept for the whole module; splitting across ``@cocotb.test``
entry points cancels the next case on ``RisingEdge`` under this Icarus/cocotb
2.0.1 stack after the first fault is logged.

Test-case IDs:
    TC-OWN-BASELINE
    TC-OWN-CS-MUTEX
    TC-OWN-FLASH-CS
    TC-OWN-SIO-DUAL
    TC-OWN-SCK-IDLE
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.clocks import apply_reset, start_clock
from common.config import parse_run_config
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
    SIO_UIO_BITS,
    attach_dual_psram,
)
from monitors.qspi import (
    CHK_PIN_CS_MUTEX,
    CHK_PIN_FLASH_HIGH,
    CHK_PIN_SCK_PARK,
    CHK_PIN_SIO_OWN,
    start_shared_bus_monitor,
)

FILL = 0x00
EQUAL_SIO_NIBBLE = 0x1  # SIO0=1; equal-value dual drive on one bit


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


def _clear_bus_log(bus) -> None:
    bus.events.clear()
    bus.violations.clear()
    bus.reset_truncated.clear()
    bus.notes.clear()
    bus._condition_active.clear()
    bus._suppressed = 0


def _clear_agent_log(agent) -> None:
    agent.inject_sio_release()
    agent.violations.clear()
    agent.transactions.clear()


def _clear_fault(dut) -> None:
    dut.fault_uio_oe.value = 0
    dut.fault_uio_drive.value = 0


async def _park_clean(dut, psram0, psram1, bus):
    """Clear injectors, sync-reset, and start a fresh ownership log window."""
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    _clear_fault(dut)
    _clear_agent_log(psram0.agent)
    _clear_agent_log(psram1.agent)
    _clear_bus_log(bus)
    await apply_reset(dut)
    await Timer(20, unit="ns")
    _clear_bus_log(bus)
    _clear_agent_log(psram0.agent)
    _clear_agent_log(psram1.agent)


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_ownership TEST_FILTER={test}"
    ).format(level=config["level"], sim=config["sim"], seed=config["seed"], test=test)


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


def _assert_clean_bus(bus, *, test: str, log=None) -> None:
    assert not bus.events, (
        f"{test}: unexpected ownership events: " + "; ".join(str(e) for e in bus.events)
    )
    assert not bus.violations, (
        f"{test}: unexpected ownership violations: " + "; ".join(bus.violations)
    )
    if log is not None:
        log.info("%s clean: %s", test, bus.summary())


def _assert_only_check(bus, check_id: str, *, timing_id: str, test: str, detail_substr: str = "") -> list:
    """Require at least one event for *check_id* and no other ownership IDs."""
    events = bus.violations_for(check_id)
    others = [event for event in bus.events if event.check_id != check_id]
    assert events, (
        f"{test}: expected {check_id} / {timing_id}, observed {bus.summary()}: "
        + "; ".join(str(e) for e in bus.events)
    )
    assert not others, (
        f"{test}: unexpected extra IDs {[e.check_id for e in others]}: "
        + "; ".join(str(e) for e in others)
    )
    for event in events:
        assert event.timing_id == timing_id, f"{test}: timing id mismatch: {event}"
        assert not event.reset_truncated, f"{test}: finding was RESET-TRUNCATED: {event}"
    if detail_substr:
        details = " | ".join(event.detail for event in events)
        assert detail_substr in details, f"{test}: missing {detail_substr!r} in {details}"
    return events


async def _tc_own_baseline(dut, psram0, psram1, bus, config) -> None:
    """TC-OWN-BASELINE: parked idle and legal MCU traffic record no bus violation."""
    dut._log.info(_repro(config, "baseline"))
    await _park_clean(dut, psram0, psram1, bus)
    _assert_clean_bus(bus, test="TC-OWN-BASELINE (park)", log=dut._log)

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
    _assert_clean_bus(bus, test="TC-OWN-BASELINE", log=dut._log)


async def _tc_own_cs_mutex(dut, psram0, psram1, bus, config) -> None:
    """TC-OWN-CS-MUTEX: fault both RAM CE# low → ``CHK-PIN-CS-MUTEX`` / ``Q-MUX``."""
    dut._log.info(_repro(config, "cs_mutex"))
    await _park_clean(dut, psram0, psram1, bus)

    ce_mask = (1 << UIO_PSRAM_CE_BITS[0]) | (1 << UIO_PSRAM_CE_BITS[1])
    dut.fault_uio_drive.value = 0
    dut.fault_uio_oe.value = ce_mask
    await Timer(1, unit="ns")

    events = _assert_only_check(
        bus,
        CHK_PIN_CS_MUTEX,
        timing_id="Q-MUX",
        test="TC-OWN-CS-MUTEX",
        detail_substr="CE# low together",
    )
    dut._log.info("TC-OWN-CS-MUTEX recorded: %s", events[0])
    _clear_fault(dut)
    await Timer(1, unit="ns")


async def _tc_own_flash_cs(dut, psram0, psram1, bus, config) -> None:
    """TC-OWN-FLASH-CS: flash CS low under ``~BUS_GNT`` → ``CHK-PIN-FLASH-HIGH``."""
    dut._log.info(_repro(config, "flash_cs"))
    await _park_clean(dut, psram0, psram1, bus)

    dut.fault_uio_drive.value = 0
    dut.fault_uio_oe.value = 1 << UIO_FLASH_CS_BIT
    await Timer(1, unit="ns")

    events = _assert_only_check(
        bus,
        CHK_PIN_FLASH_HIGH,
        timing_id="Q-MUX",
        test="TC-OWN-FLASH-CS",
        detail_substr="flash CS low while ~BUS_GNT",
    )
    dut._log.info("TC-OWN-FLASH-CS recorded: %s", events[0])
    _clear_fault(dut)
    await Timer(1, unit="ns")


async def _tc_own_sio_dual(dut, psram0, psram1, bus, config) -> None:
    """TC-OWN-SIO-DUAL: ASIC-FAULT + device OE on SIO0, equal value → ``CHK-PIN-SIO-OWN``."""
    dut._log.info(_repro(config, "sio_dual"))
    await _park_clean(dut, psram0, psram1, bus)

    agent = psram0.agent
    assert not agent.selected
    assert not agent.oe

    agent.inject_sio_drive(EQUAL_SIO_NIBBLE)
    dut.fault_uio_drive.value = _pack_sio_uio(EQUAL_SIO_NIBBLE)
    dut.fault_uio_oe.value = _sio_oe_mask(0)
    await Timer(1, unit="ns")

    assert agent.oe and agent.driven_nibble == EQUAL_SIO_NIBBLE
    events = _assert_only_check(
        bus,
        CHK_PIN_SIO_OWN,
        timing_id="Q-SIO-OWN",
        test="TC-OWN-SIO-DUAL",
        detail_substr="equal driven values still fail",
    )
    assert any("SIO0" in event.detail for event in events), f"SIO0 missing: {events}"
    dut._log.info("TC-OWN-SIO-DUAL recorded: %s", events[0])

    agent.inject_sio_release()
    _clear_fault(dut)
    await Timer(1, unit="ns")


async def _tc_own_sck_idle(dut, psram0, psram1, bus, config) -> None:
    """TC-OWN-SCK-IDLE: SCK high while every CS high → ``CHK-PIN-SCK-PARK`` / ``Q-SCKIDLE``."""
    dut._log.info(_repro(config, "sck_idle"))
    await _park_clean(dut, psram0, psram1, bus)

    dut.fault_uio_drive.value = 1 << UIO_SCK_BIT
    dut.fault_uio_oe.value = 1 << UIO_SCK_BIT
    await Timer(1, unit="ns")

    events = _assert_only_check(
        bus,
        CHK_PIN_SCK_PARK,
        timing_id="Q-SCKIDLE",
        test="TC-OWN-SCK-IDLE",
        detail_substr="SCK high while no device is selected",
    )
    dut._log.info("TC-OWN-SCK-IDLE recorded: %s", events[0])
    _clear_fault(dut)
    await Timer(1, unit="ns")


@cocotb.test()
async def ownership_shared_bus_negatives(dut):
    """Run TC-OWN-BASELINE plus the four ownership negatives in order."""
    config = parse_run_config()
    dut._log.info(
        "SEED=%d LEVEL=%s SIM=%s", config["seed"], config["level"], config["sim"]
    )

    psram0, psram1 = attach_dual_psram(dut, fill=FILL)
    bus = start_shared_bus_monitor(dut, psram0.agent, psram1.agent, strict=False)
    await start_clock(dut)

    await _tc_own_baseline(dut, psram0, psram1, bus, config)
    await _tc_own_cs_mutex(dut, psram0, psram1, bus, config)
    await _tc_own_flash_cs(dut, psram0, psram1, bus, config)
    await _tc_own_sio_dual(dut, psram0, psram1, bus, config)
    await _tc_own_sck_idle(dut, psram0, psram1, bus, config)

    dut._log.info(
        "TC-OWN suite passed: BASELINE, CS-MUTEX, FLASH-CS, SIO-DUAL, SCK-IDLE"
    )
