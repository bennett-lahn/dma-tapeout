"""M0 L1 same-device smoke test.

Test-case IDs:
    TC-SMOKE
"""

import cocotb
from cocotb.triggers import RisingEdge, with_timeout
from cocotb.triggers import SimTimeoutError

from common.clocks import apply_reset, start_clock
from common.config import parse_run_config
from common.host import pulse_start
from models.psram import attach_dual_psram, format_violations
from monitors.qspi import assert_model_pin_disposition, start_shared_bus_monitor
from monitors.timing import start_ce_timing_monitor

TCD_HEAD_ADDR = 0x000000
NEXT_TCD_ADDR = 0x000020
SRC_ADDR = 0x000100
DST_ADDR = 0x000200
SRC_BYTE = 0xA5
DST_SENTINEL = 0x00

DONE_BIT = 0x1
DONE_TIMEOUT_NS = 100_000


def _build_tcd(
    src_ptr: int,
    dest_ptr: int,
    transfer_len: int,
    next_tcd: int,
    *,
    src_device: int = 0,
    dest_device: int = 0,
    next_device: int = 0,
    quit: bool = False,
    reserved: int = 0,
) -> bytes:
    """Serialize one 11-byte TCD: big-endian pointers, CTRL_FLAGS last byte.

    CTRL_FLAGS (byte 10): bits[7:4]=reserved, [3]=NEXT, [2]=DEST, [1]=SRC,
    [0]=QUIT, matching ``src/rtl/types.svh`` ``tcd_t`` bit order.
    """
    ctrl_flags = (
        ((reserved & 0xF) << 4)
        | ((next_device & 1) << 3)
        | ((dest_device & 1) << 2)
        | ((src_device & 1) << 1)
        | (1 if quit else 0)
    )
    return bytes(
        [
            (src_ptr >> 16) & 0xFF,
            (src_ptr >> 8) & 0xFF,
            src_ptr & 0xFF,
            (dest_ptr >> 16) & 0xFF,
            (dest_ptr >> 8) & 0xFF,
            dest_ptr & 0xFF,
            transfer_len & 0xFF,
            (next_tcd >> 16) & 0xFF,
            (next_tcd >> 8) & 0xFF,
            next_tcd & 0xFF,
            ctrl_flags,
        ]
    )


def _repro(config: dict) -> str:
    return (
        "REPRO: make test LEVEL={level} SIM={sim} SEED={seed} "
        "DMA_BUF_DEPTH={depth} TIMING_PROFILE={timing} TEST_FILTER=smoke"
    ).format(
        level=config["level"],
        sim=config["sim"],
        seed=config["seed"],
        depth=config["dma_buf_depth"],
        timing=config["timing_profile"],
    )


async def _wait_for_done_pulse(dut) -> None:
    """DONE (uo_out[0]) is high in IDLE; wait for it to drop then return high."""
    while int(dut.uo_out.value) & DONE_BIT:
        await RisingEdge(dut.clk)
    while not (int(dut.uo_out.value) & DONE_BIT):
        await RisingEdge(dut.clk)


@cocotb.test()
async def smoke_same_device_copy(dut):
    """TC-SMOKE: one PSRAM0-to-PSRAM0 copy, length 1, then quit TCD.

    Ordered QPI transactions are fetch, read, write, fetch; DONE returns.
    """
    config = parse_run_config()
    repro = _repro(config)
    dut._log.info("SEED=%d LEVEL=%s SIM=%s DUT_LEVEL=%s", config["seed"], config["level"], config["sim"], config["dut_level"])
    dut._log.info(repro)

    psram0, psram1 = attach_dual_psram(dut)
    # Non-strict: collect ownership and coarse CE# timing findings and dispose
    # them with the PSRAM violation lists below, so one failure prints the
    # whole picture. Defaults: tCEM=4 us (extended), tCPH=18 ns.
    bus = start_shared_bus_monitor(dut, psram0.agent, psram1.agent, strict=False)
    ce = start_ce_timing_monitor(dut, strict=False)

    tcd_head = _build_tcd(SRC_ADDR, DST_ADDR, 1, NEXT_TCD_ADDR, quit=False)
    tcd_quit = _build_tcd(0, 0, 0, 0, quit=True)
    psram0.write(TCD_HEAD_ADDR, tcd_head)
    psram0.write(NEXT_TCD_ADDR, tcd_quit)
    psram0.write(SRC_ADDR, bytes([SRC_BYTE]))
    psram0.write(DST_ADDR, bytes([DST_SENTINEL]))

    await start_clock(dut)
    await apply_reset(dut)

    await pulse_start(dut)

    try:
        await with_timeout(_wait_for_done_pulse(dut), DONE_TIMEOUT_NS, "ns")
    except SimTimeoutError:
        dut._log.error(repro)
        raise AssertionError(
            "TC-SMOKE: DONE did not return within "
            f"{DONE_TIMEOUT_NS} ns; classify DUT vs TB before retry. " + repro
        )

    observed = psram0.read(DST_ADDR, 1)[0]
    assert observed == SRC_BYTE, (
        f"TC-SMOKE: destination byte mismatch at 0x{DST_ADDR:06X}: "
        f"expected 0x{SRC_BYTE:02X}, got 0x{observed:02X}. " + repro
    )

    violations = psram0.agent.violations + psram1.agent.violations
    assert not violations, (
        "TC-SMOKE: PSRAM protocol violations: " + format_violations(violations) + ". " + repro
    )
    assert not bus.violations, (
        "TC-SMOKE: shared-bus ownership violations: "
        + "; ".join(bus.violations)
        + ". "
        + repro
    )
    assert not ce.violations, (
        "TC-SMOKE: CE# timing violations (Q-CEM/Q-CPH): "
        + "; ".join(ce.violations)
        + ". "
        + repro
    )

    assert_model_pin_disposition(
        psram0, psram1, log=dut._log, test="TC-SMOKE"
    )
    dut._log.info(
        "TC-SMOKE passed: dest[0x%06X]=0x%02X after %d PSRAM0 transactions (%s)",
        DST_ADDR,
        observed,
        len(psram0.agent.transactions),
        ce.summary(),
    )
