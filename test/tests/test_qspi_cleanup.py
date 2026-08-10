"""Directed lifecycle cleanup evidence for controller and live CE# windows."""

from types import SimpleNamespace

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.dispose import dispose_run, expect
from common.host import QpiPassthroughMaster, assert_bus_req
from models.psram import QSPI_CMD_FAST_READ
from monitors.handshake import CHK_CTRL_DATA_PAIR
from monitors.qspi import DIR_READ

FILL = 0x00


def _repro(config: dict, test_filter: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=top SIM={sim} SEED={seed} TIMING_PROFILE=nominal "
        "COCOTB_TEST_MODULES=tests.test_qspi_cleanup TEST_FILTER={test_filter}"
    ).format(sim=config["sim"], seed=config["seed"], test_filter=test_filter)


async def _bring_up(dut):
    bringup = await bring_up_top(
        dut,
        fill=FILL,
        bus_monitor=False,
        ce_monitor=False,
        handshake_monitor=False,
        arbitration_monitor=False,
    )
    assert bringup.timing_profile == "nominal", (
        "test_qspi_cleanup requires TIMING_PROFILE=nominal, got "
        f"{bringup.timing_profile!r}"
    )
    await assert_bus_req(dut, hold=True)
    for _ in range(32):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> 1) & 1:
            break
    else:
        raise AssertionError("cleanup directed setup did not receive BUS_GNT")
    master = QpiPassthroughMaster(dut)
    await master.park()
    bringup.clear()
    return bringup, master


@cocotb.test()
async def qspi_cleanup_controller_pair_pending(dut):
    """TC-CTRL-DATA-PAIR-PENDING-AT-STOP preserves an unpaired completed read."""
    config = parse_run_config()
    repro = _repro(config, "qspi_cleanup_controller_pair_pending")
    bringup, _ = await _bring_up(dut)
    controller = bringup.controller
    assert controller is not None and not controller.blocked, (
        "TC-CTRL-DATA-PAIR-PENDING-AT-STOP requires a usable top pin monitor"
    )

    # This is the controller's completed pin-interval input, not a partially
    # decoded frame. Deliberately omit the following write interval.
    interval = SimpleNamespace(
        direction=DIR_READ,
        length=1,
        canonical=lambda: "completed payload read len=1",
    )
    controller._check_data_pair(interval)
    dispose_run(
        bringup,
        test="TC-CTRL-DATA-PAIR-PENDING-AT-STOP",
        expect_fail=[expect(CHK_CTRL_DATA_PAIR)],
        log=dut._log,
        repro=repro,
    )
    details = [event.detail for event in controller.violations_for(CHK_CTRL_DATA_PAIR)]
    assert any("reason=dispose" in detail for detail in details), (
        "TC-CTRL-DATA-PAIR-PENDING-AT-STOP: missing lifecycle reason"
    )
    bringup.stop()


@cocotb.test()
async def qspi_cleanup_live_ce_frame(dut):
    """TC-LIVE-CE-FRAME-AT-STOP retains an aborted CE# frame as diagnostic."""
    config = parse_run_config()
    repro = _repro(config, "qspi_cleanup_live_ce_frame")
    bringup, master = await _bring_up(dut)
    device = bringup.psram0

    await master.open(0)
    await master.send_opcode(QSPI_CMD_FAST_READ, nibbles=1)
    await Timer(1, unit="ns")
    notes_before = len(device.agent.notes)
    dispose_run(
        bringup,
        test="TC-LIVE-CE-FRAME-AT-STOP",
        log=dut._log,
        repro=repro,
    )
    notes = device.agent.notes[notes_before:]
    assert any("incomplete-window" in note and "reason=dispose" in note for note in notes), (
        "TC-LIVE-CE-FRAME-AT-STOP: incomplete CE# frame was silently lost"
    )
    bringup.stop()
