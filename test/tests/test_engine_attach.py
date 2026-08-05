"""L0 attach self-check (M1 T4). Temporary; T5 may absorb or delete this.

Proves ``attach_engine_psram`` wires one/two agents onto ``tb_engine`` without
crash, parks CE# high after reset, and tolerates a non-strict shared-bus
monitor. Does not exercise QPI read/write traffic (that is T5/T6).
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.clocks import apply_engine_reset, start_clock
from common.config import parse_run_config
from models.psram import attach_engine_psram, format_violations
from monitors.qspi import start_shared_bus_monitor

# Agents from an earlier test in this module are cancelled before the next
# attach so only one model per device ever drives the shared SIO handles.
_ATTACHED: list = []


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=engine SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_engine_attach TEST_FILTER={test}"
    ).format(sim=config["sim"], seed=config["seed"], test=test)


def _stop_attached() -> None:
    for device in _ATTACHED:
        device.agent.stop()
    _ATTACHED.clear()


def _level(handle) -> "int | None":
    try:
        return int(handle.value)
    except ValueError:
        return None


async def _bring_up(dut, devices=(0, 1)):
    """Clock, park/reset the engine, attach *devices*, return them."""
    _stop_attached()
    await start_clock(dut)
    await apply_engine_reset(dut)
    attached = attach_engine_psram(dut, devices=devices)
    _ATTACHED.extend(attached)
    return attached


@cocotb.test()
async def engine_attach_dual(dut):
    """Attach both PSRAM agents; CE# idle high; non-strict monitor stays clean."""
    config = parse_run_config()
    repro = _repro(config, "engine_attach_dual")
    dut._log.info(repro)

    psram0, psram1 = await _bring_up(dut, devices=(0, 1))
    bus = start_shared_bus_monitor(dut, psram0.agent, psram1.agent, strict=False)

    # Settle a few clocks in IDLE with no txn_valid.
    for _ in range(4):
        await RisingEdge(dut.clk)

    assert _level(dut.psram0_ce_n) == 1, f"PSRAM0 CE# not idle high. {repro}"
    assert _level(dut.psram1_ce_n) == 1, f"PSRAM1 CE# not idle high. {repro}"
    assert _level(dut.psram_sck) == 0, f"SCK not parked low while deselected. {repro}"
    assert not psram0.agent.selected and not psram0.agent.oe
    assert not psram1.agent.selected and not psram1.agent.oe

    await Timer(100, unit="ns")

    violations = psram0.agent.violations + psram1.agent.violations
    assert not violations, (
        "engine_attach_dual: PSRAM violations: " + format_violations(violations) + ". " + repro
    )
    assert not bus.violations, (
        "engine_attach_dual: shared-bus violations: "
        + "; ".join(bus.violations)
        + ". "
        + repro
    )
    dut._log.info("engine_attach_dual passed")


@cocotb.test()
async def engine_attach_single(dut):
    """Attach PSRAM0 only; both CE# still idle high (unselected stays high)."""
    config = parse_run_config()
    repro = _repro(config, "engine_attach_single")
    dut._log.info(repro)

    (psram0,) = await _bring_up(dut, devices=0)
    bus = start_shared_bus_monitor(dut, psram0.agent, strict=False)

    for _ in range(4):
        await RisingEdge(dut.clk)

    assert _level(dut.psram0_ce_n) == 1, f"PSRAM0 CE# not idle high. {repro}"
    assert _level(dut.psram1_ce_n) == 1, (
        f"Unselected PSRAM1 CE# must stay high with only device 0 attached. {repro}"
    )
    assert not psram0.agent.selected and not psram0.agent.oe

    await Timer(100, unit="ns")

    assert not psram0.agent.violations, (
        "engine_attach_single: PSRAM violations: "
        + format_violations(psram0.agent.violations)
        + ". "
        + repro
    )
    assert not bus.violations, (
        "engine_attach_single: shared-bus violations: "
        + "; ".join(bus.violations)
        + ". "
        + repro
    )
    dut._log.info("engine_attach_single passed")
