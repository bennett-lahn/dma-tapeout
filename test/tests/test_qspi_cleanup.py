"""Directed lifecycle cleanup evidence for controller and live CE# windows."""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.constants import FILL
from common.dispose import dispose_run, expect
from common.host import QpiPassthroughMaster, assert_bus_req
from models.psram import QSPI_CMD_FAST_READ, Q_PHASE
from monitors.handshake import CHK_CTRL_DATA_PAIR


def _repro(config: dict, test_filter: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=top SIM={sim} SEED={seed} TIMING_PROFILE={timing} "
        "COCOTB_TEST_MODULES=tests.test_qspi_cleanup TEST_FILTER={test_filter}"
    ).format(
        sim=config["sim"],
        seed=config["seed"],
        timing=config["timing_profile"],
        test_filter=test_filter,
    )


async def _bring_up(dut, *, handshake_monitor: bool = False):
    bringup = await bring_up_top(
        dut,
        fill=FILL,
        bus_monitor=False,
        ce_monitor=False,
        handshake_monitor=handshake_monitor,
        arbitration_monitor=False,
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
    """TC-CTRL-DATA-PAIR-PENDING-AT-STOP: unpaired payload read fails at dispose.

    A completed MCU ``0xEB`` with dummy cycles and no payload bytes (not an
    11-byte fetch) leaves ``CHK-CTRL-DATA-PAIR`` pending. Closing after dummy
    avoids the legal read-data float window, so this is the public stop path
    without poking the controller's private decoder.
    """
    config = parse_run_config()
    repro = _repro(config, "qspi_cleanup_controller_pair_pending")
    bringup, master = await _bring_up(dut)
    controller = bringup.controller
    assert controller is not None and not controller.blocked, (
        "TC-CTRL-DATA-PAIR-PENDING-AT-STOP requires a usable top pin monitor"
    )
    assert bringup.pin is not None and not bringup.pin.blocked, (
        "TC-CTRL-DATA-PAIR-PENDING-AT-STOP requires pin evidence"
    )

    await master.frame(0, QSPI_CMD_FAST_READ, 0x000500, dummy_cycles=6)
    await master.park()
    assert "unpaired_read=0" in controller.summary(), (
        "TC-CTRL-DATA-PAIR-PENDING-AT-STOP: dummy-complete 0xEB did not "
        f"open a payload pair (summary={controller.summary()}). {repro}"
    )

    report = dispose_run(
        bringup,
        test="TC-CTRL-DATA-PAIR-PENDING-AT-STOP",
        expect_fail=[expect(CHK_CTRL_DATA_PAIR, count=1)],
        log=dut._log,
        repro=repro,
    )
    details = [
        finding.detail
        for finding in report.ordinary
        if finding.check_id == CHK_CTRL_DATA_PAIR
    ]
    assert any("reason=dispose" in detail for detail in details), (
        "TC-CTRL-DATA-PAIR-PENDING-AT-STOP: missing lifecycle reason"
    )
    bringup.stop()


@cocotb.test()
async def qspi_cleanup_live_ce_frame(dut):
    """TC-LIVE-CE-FRAME-AT-STOP: still-open CE# after opcode fails Q-PHASE.

    Model and pin decoder both record ``Q-PHASE`` at dispose, so the exact
    count is 2.
    """
    config = parse_run_config()
    repro = _repro(config, "qspi_cleanup_live_ce_frame")
    bringup, master = await _bring_up(dut)

    await master.open(0)
    await master.send_opcode(QSPI_CMD_FAST_READ)
    await Timer(1, unit="ns")
    report = dispose_run(
        bringup,
        test="TC-LIVE-CE-FRAME-AT-STOP",
        expect_fail=[expect(Q_PHASE, count=2)],
        log=dut._log,
        repro=repro,
    )
    details = [
        finding.detail
        for finding in report.ordinary
        if finding.check_id == Q_PHASE
    ]
    assert any("reason=dispose" in detail for detail in details), (
        "TC-LIVE-CE-FRAME-AT-STOP: missing lifecycle reason on Q-PHASE"
    )
    bringup.stop()
