"""M0 L1 same-device smoke test.

Test-case IDs:
    TC-SMOKE
"""

import cocotb
from cocotb.triggers import RisingEdge, with_timeout
from cocotb.triggers import SimTimeoutError

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.dispose import REVIEW, dispose_run
from common.host import pulse_start
from reference.chain import MemoryImage, interpret_chain
from reference.scoreboard import RunContext, Scoreboard
from reference.tcd import Tcd, encode_tcd

TCD_HEAD_ADDR = 0x000000
NEXT_TCD_ADDR = 0x000020
SRC_ADDR = 0x000100
DST_ADDR = 0x000200
SRC_BYTE = 0xA5
DST_SENTINEL = 0x00

DONE_BIT = 0x1
DONE_TIMEOUT_NS = 100_000


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

    # Shared bring-up: stop prior agents, park host/fault injectors, attach both
    # models, start the non-strict ownership / CE# / handshake / pin monitors
    # before reset release, then clock and release rst_n.
    bringup = await bring_up_top(dut)
    psram0 = bringup.psram0

    head = Tcd(
        src_ptr=SRC_ADDR,
        dest_ptr=DST_ADDR,
        transfer_len=1,
        next_tcd=NEXT_TCD_ADDR,
    )
    psram0.write(TCD_HEAD_ADDR, encode_tcd(head))
    psram0.write(NEXT_TCD_ADDR, encode_tcd(Tcd(quit=True)))
    psram0.write(SRC_ADDR, bytes([SRC_BYTE]))
    psram0.write(DST_ADDR, bytes([DST_SENTINEL]))

    # Golden oracle for the same layout, used below for the dual-axis
    # scoreboard; the backdoor writes above never touch the bus so this mirrors
    # them independently rather than reading them back.
    initial_memory = MemoryImage(fill=0x00)
    initial_memory.write(0, TCD_HEAD_ADDR, encode_tcd(head))
    initial_memory.write(0, NEXT_TCD_ADDR, encode_tcd(Tcd(quit=True)))
    initial_memory.write(0, SRC_ADDR, bytes([SRC_BYTE]))
    initial_memory.write(0, DST_ADDR, bytes([DST_SENTINEL]))
    golden = interpret_chain(initial_memory, dma_buf_depth=config["dma_buf_depth"])

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

    # L1 smoke requires a live pin axis: missing/blocked is a hard fail, not a
    # soft skip of the transaction compare.
    if bringup.pin is None or bringup.pin.blocked:
        reason = (
            "missing"
            if bringup.pin is None
            else f"blocked ({bringup.pin.blocked_reason})"
        )
        raise AssertionError(
            f"TC-SMOKE: pin monitor {reason}; L1 smoke requires a live "
            f"pin axis for dual-axis scoreboard compare. " + repro
        )

    Scoreboard.from_result(
        golden,
        context=RunContext(
            level=config["level"],
            sim=config["sim"],
            seed=config["seed"],
            depth=config["dma_buf_depth"],
            timing=config["timing_profile"],
            test="TC-SMOKE",
            repro=repro,
        ),
        log=dut._log,
    ).compare(
        bringup.pin.transactions(),
        observed_memory={device.device_id: device for device in bringup.devices},
    )

    # Q-LAUNCH records forced SIO/OE reset convergence as RESET-TRUNCATED while
    # SCK is still X; smoke is not a reset-protocol case, so review rather than forbid.
    report = dispose_run(
        bringup,
        test="TC-SMOKE",
        log=dut._log,
        reset_truncated=REVIEW,
        repro=repro,
    )
    dut._log.info("TC-SMOKE pin txn log:\n%s", bringup.pin.log_text())
    dut._log.info(
        "TC-SMOKE passed: dest[0x%06X]=0x%02X after %d PSRAM0 transactions "
        "(%s | %s | %s | %s | %s | %s)",
        DST_ADDR,
        observed,
        len(psram0.agent.transactions),
        bringup.ce.summary(),
        bringup.handshake.summary(),
        bringup.arbitration.summary(),
        bringup.controller.summary(),
        bringup.pin.summary(),
        report.summary(),
    )
