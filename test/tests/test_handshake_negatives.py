"""L0 public negatives for handshake/controller monitors.

tb-hs-08 unresolved accepted request, tb-hs-04 hang counts, tb-hs-12 illegal
opcode, tb-hs-13 reset-truncated partial counts. Stimulus uses engine ports;
dispose uses public ``dispose_run`` (no private monitor pokes).
"""

import cocotb
from cocotb.triggers import RisingEdge

from common.bringup import bring_up_engine
from common.config import parse_run_config
from common.constants import FILL, REVIEW
from common.dispose import dispose_run, expect
from models.psram import QSPI_CMD_FAST_READ, Q_OPCODE, Q_PHASE
from monitors.handshake import (
    CHK_HS_OPCODE,
    CHK_HS_RDATA_COUNT,
    CHK_HS_REQ_STABLE,
)


def _x_bits(width: int) -> str:
    return "x" * width


def _repro(config: dict, test_filter: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=engine SIM={sim} SEED={seed} TIMING_PROFILE=ideal "
        "COCOTB_TEST_MODULES=tests.test_handshake_negatives "
        "TEST_FILTER={test_filter}"
    ).format(sim=config["sim"], seed=config["seed"], test_filter=test_filter)


def _drive_request(dut, *, cmd, addr, device_sel=0, byte_len=1) -> None:
    dut.cmd.value = cmd
    dut.addr.value = addr
    dut.device_sel.value = device_sel
    dut.byte_len.value = byte_len


@cocotb.test()
async def handshake_illegal_opcode(dut):
    """Accepted cmd outside 0xEB/0x02 fails CHK-HS-OPCODE (allowlist half)."""
    config = parse_run_config()
    repro = _repro(config, "handshake_illegal_opcode")
    bringup = await bring_up_engine(dut, fill=FILL, bus_monitor=False)
    _drive_request(dut, cmd=0xFF, addr=0x000100, byte_len=1)
    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0
    for _ in range(8):
        await RisingEdge(dut.clk)
    dispose_run(
        bringup,
        test="TC-HS-NEG-OPCODE",
        expect_fail=[expect(CHK_HS_OPCODE, count=1), expect(Q_OPCODE, count=1)],
        log=dut._log,
        repro=repro,
    )


@cocotb.test()
async def handshake_unresolved_addr_at_accept(dut):
    """tb-hs-08: unresolved accepted addr fails CHK-HS-REQ-STABLE immediately."""
    config = parse_run_config()
    repro = _repro(config, "handshake_unresolved_addr_at_accept")
    bringup = await bring_up_engine(dut, fill=FILL, bus_monitor=False)
    _drive_request(dut, cmd=QSPI_CMD_FAST_READ, addr=0x000100, byte_len=1)
    dut.addr.value = _x_bits(24)
    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    dispose_run(
        bringup,
        test="TC-HS-NEG-REQ-X-ACCEPT",
        expect_fail=[expect(CHK_HS_REQ_STABLE), expect(CHK_HS_RDATA_COUNT)],
        log=dut._log,
        repro=repro,
    )


@cocotb.test()
async def handshake_unresolved_addr_while_busy(dut):
    """tb-hs-08: addr going X/Z while busy fails CHK-HS-REQ-STABLE."""
    config = parse_run_config()
    repro = _repro(config, "handshake_unresolved_addr_while_busy")
    bringup = await bring_up_engine(dut, fill=FILL, bus_monitor=False)
    _drive_request(dut, cmd=QSPI_CMD_FAST_READ, addr=0x000100, byte_len=1)
    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0
    for _ in range(8):
        await RisingEdge(dut.clk)
        try:
            if int(dut.busy.value) == 1:
                break
        except ValueError:
            break
    dut.addr.value = _x_bits(24)
    for _ in range(4):
        await RisingEdge(dut.clk)
    dispose_run(
        bringup,
        test="TC-HS-NEG-REQ-X-BUSY",
        expect_fail=[
            expect(CHK_HS_REQ_STABLE),
            expect(CHK_HS_RDATA_COUNT),
            expect(Q_PHASE),
        ],
        log=dut._log,
        repro=repro,
    )


@cocotb.test()
async def handshake_hang_emits_count_fail(dut):
    """tb-hs-04: dispose while busy fails CHK-HS-RDATA-COUNT with expected beats."""
    config = parse_run_config()
    repro = _repro(config, "handshake_hang_emits_count_fail")
    bringup = await bring_up_engine(dut, fill=FILL, bus_monitor=False)
    _drive_request(dut, cmd=QSPI_CMD_FAST_READ, addr=0x000100, byte_len=1)
    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0
    for _ in range(16):
        await RisingEdge(dut.clk)
        try:
            if int(dut.busy.value) == 1:
                break
        except ValueError:
            break
    report = dispose_run(
        bringup,
        test="TC-HS-NEG-HANG-COUNT",
        expect_fail=[expect(CHK_HS_RDATA_COUNT)],
        log=dut._log,
        repro=repro,
    )
    details = " ".join(
        event.detail
        for event in bringup.handshake.violations_for(CHK_HS_RDATA_COUNT)
    )
    assert "expected_rdata_valid" in details and "observed_rdata_valid" in details, (
        f"hang count fail must include expected vs observed beats: {details} "
        f"report={report.summary()}"
    )


@cocotb.test()
async def handshake_reset_truncated_partial_counts(dut):
    """tb-hs-13: in-reset abort records RESET-TRUNCATED count rows."""
    config = parse_run_config()
    repro = _repro(config, "handshake_reset_truncated_partial_counts")
    bringup = await bring_up_engine(dut, fill=FILL, bus_monitor=False)
    _drive_request(dut, cmd=QSPI_CMD_FAST_READ, addr=0x000100, byte_len=1)
    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0
    for _ in range(16):
        await RisingEdge(dut.clk)
        try:
            if int(dut.busy.value) == 1:
                break
        except ValueError:
            break
    dut.rst_n.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    report = dispose_run(
        bringup,
        test="TC-HS-NEG-RESET-TRUNCATED",
        expect_fail=(),
        reset_truncated=REVIEW,
        log=dut._log,
        repro=repro,
    )
    truncated = bringup.handshake.review_reset_truncated()
    assert any(event.check_id == CHK_HS_RDATA_COUNT for event in truncated), (
        "reset abort must record truncated RDATA-COUNT with partial counts, "
        f"got {truncated} report={report.summary()}"
    )
