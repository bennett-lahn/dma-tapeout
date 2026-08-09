"""Behavioral ``Q-RST`` dispose (M1 T8).

Asserting ``rst_n`` mid-transaction must abort ASIC activity, clear shared OE,
return the engine/controller to a reset-safe idle, and leave ownership /
model findings classified as ``RESET-TRUNCATED`` rather than ordinary fails.

Primary path is L1 (``LEVEL=top``): combinational ``uio_oe`` clear (``CHK-RST-OE``),
``DONE`` / ``BUS_GNT`` after the sampled reset edge, and a subsequent legal
START. L0 (``LEVEL=engine``) proves the same abort / OE / restart property on
``qspi_engine`` ports. Not the M2 ``TC-RESET-*`` matrix.

Test-case IDs:
    TC-QRST-ACTIVE
"""

import cocotb
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge, Timer, with_timeout
from cocotb.triggers import SimTimeoutError

from common.bringup import bring_up_engine, bring_up_top
from common.config import parse_run_config
from common.dispose import REQUIRE, dispose_run
from common.host import pulse_start
from models.psram import QSPI_CMD_WRITE
from monitors.qspi import sck_is_parked

FILL = 0x00
DONE_BIT = 0x1
DONE_TIMEOUT_NS = 100_000

TCD_HEAD_ADDR = 0x000000
NEXT_TCD_ADDR = 0x000020
SRC_ADDR = 0x000100
DST_ADDR = 0x000200
SRC_BYTE = 0xA5
DST_SENTINEL = 0x00
POST_SRC_BYTE = 0x5A

_MID_TXN_TIMEOUT_CYCLES = 256
_ENGINE_WRITE_LEN = 11
_ENGINE_WRITE_ADDR = 0x003100


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


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_reset_protocol TEST_FILTER={test}"
    ).format(level=config["level"], sim=config["sim"], seed=config["seed"], test=test)


def _level(handle) -> "int | None":
    try:
        return int(handle.value)
    except ValueError:
        return None


def _bytes_to_nibbles(data: bytes) -> list:
    nibbles = []
    for value in data:
        nibbles.append((value >> 4) & 0xF)
        nibbles.append(value & 0xF)
    return nibbles


def _dispose_reset_window(bringup, *, test: str, log, repro: str) -> list:
    """Review every ``RESET-TRUNCATED`` finding from the abort window.

    Shared contract via :func:`common.dispose.dispose_run`: ordinary fails must
    be empty, and the reset window must produce at least one truncated finding,
    so a silently clean abort cannot pass as a disposed one.
    """
    report = dispose_run(
        bringup,
        test=test,
        log=log,
        reset_truncated=REQUIRE,
        repro=repro,
    )
    return report.reset_truncated


def _load_smoke_chain(psram0, *, src_byte: int) -> None:
    tcd_head = _build_tcd(SRC_ADDR, DST_ADDR, 1, NEXT_TCD_ADDR, quit=False)
    tcd_quit = _build_tcd(0, 0, 0, 0, quit=True)
    psram0.write(TCD_HEAD_ADDR, tcd_head)
    psram0.write(NEXT_TCD_ADDR, tcd_quit)
    psram0.write(SRC_ADDR, bytes([src_byte]))
    psram0.write(DST_ADDR, bytes([DST_SENTINEL]))


async def _wait_for_done_pulse(dut) -> None:
    while int(dut.uo_out.value) & DONE_BIT:
        await RisingEdge(dut.clk)
    while not (int(dut.uo_out.value) & DONE_BIT):
        await RisingEdge(dut.clk)


async def _await_mid_txn_top(dut, *, repro: str) -> None:
    """Reach an in-flight ASIC CE# select with shared OE driven."""
    for _ in range(_MID_TXN_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if not (int(dut.uo_out.value) & DONE_BIT):
            break
    else:
        raise AssertionError(f"DONE never dropped after START. {repro}")

    for _ in range(_MID_TXN_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _level(dut.bus_ram_a_cs_n) == 0 and int(dut.uio_oe.value) != 0:
            # Hold a few clocks inside the CE# window so cmd/addr is in progress.
            for _ in range(4):
                await RisingEdge(dut.clk)
                assert _level(dut.bus_ram_a_cs_n) == 0, (
                    f"CE# rose before reset could be asserted. {repro}"
                )
            return
    raise AssertionError(f"never observed mid-txn PSRAM0 CE# with OE. {repro}")


async def _assert_rst_n_clears_top_oe(dut, *, test: str) -> None:
    """CHK-RST-OE: every shared ``uio_oe`` bit is 0 while ``rst_n=0``."""
    await Timer(1, unit="ns")
    oe = int(dut.uio_oe.value)
    assert oe == 0, f"{test}: CHK-RST-OE failed, uio_oe=0x{oe:02X} while rst_n=0"


async def _assert_sampled_reset_status_top(dut, *, test: str, cycles: int = 5) -> None:
    """CHK-RST-STATUS after rising ``clk`` edges sampled with ``rst_n=0``."""
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert _level(dut.rst_n) == 0, f"{test}: rst_n not held low across sampled edge"
        status = int(dut.uo_out.value)
        assert status & DONE_BIT, (
            f"{test}: DONE not 1 after sampled reset (uo_out=0x{status:02X})"
        )
        assert not ((status >> 1) & 1), (
            f"{test}: BUS_GNT not 0 after sampled reset (uo_out=0x{status:02X})"
        )
    assert _level(dut.bus_ram_a_cs_n) == 1, f"{test}: PSRAM0 CE# not idle high"
    assert _level(dut.bus_ram_b_cs_n) == 1, f"{test}: PSRAM1 CE# not idle high"
    assert sck_is_parked(dut), f"{test}: SCK not parked after reset"
    # Leave ReadOnly before the caller drives host inputs / releases rst_n.
    await NextTimeStep()


async def _q_rst_top(dut, config: dict) -> None:
    """L1: abort mid-DMA, clear ``uio_oe``, dispose truncated events, restart."""
    test = "TC-QRST-ACTIVE"
    repro = _repro(config, "qspi_reset_protocol")
    dut._log.info(repro)

    bringup = await bring_up_top(dut, fill=FILL)
    psram0 = bringup.psram0
    psram1 = bringup.psram1

    _load_smoke_chain(psram0, src_byte=SRC_BYTE)
    bringup.clear()

    await pulse_start(dut)
    await _await_mid_txn_top(dut, repro=repro)
    assert psram0.agent.selected, f"{test}: PSRAM0 not selected mid-txn. {repro}"

    # Classify the in-flight parser abort before OE release raises CE#.
    for agent in bringup.agents:
        agent.note_reset()
    dut.rst_n.value = 0
    await _assert_rst_n_clears_top_oe(dut, test=test)
    await _assert_sampled_reset_status_top(dut, test=test)

    truncated = _dispose_reset_window(
        bringup, test=test, log=dut._log, repro=repro
    )
    assert any(finding.source.startswith("PSRAM") for finding in truncated), (
        f"{test}: expected at least one model RESET-TRUNCATED from mid-txn abort, "
        f"observed {[str(finding) for finding in truncated]}. " + repro
    )

    # Release reset and prove a fresh legal chain can complete.
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    bringup.clear()
    bringup.clear_transactions()

    _load_smoke_chain(psram0, src_byte=POST_SRC_BYTE)
    await pulse_start(dut)
    try:
        await with_timeout(_wait_for_done_pulse(dut), DONE_TIMEOUT_NS, "ns")
    except SimTimeoutError as exc:
        raise AssertionError(
            f"{test}: post-reset DONE did not return within {DONE_TIMEOUT_NS} ns. "
            + repro
        ) from exc

    observed = psram0.read(DST_ADDR, 1)[0]
    assert observed == POST_SRC_BYTE, (
        f"{test}: post-reset dest mismatch at 0x{DST_ADDR:06X}: "
        f"expected 0x{POST_SRC_BYTE:02X}, got 0x{observed:02X}. " + repro
    )
    # Default policy: the post-reset chain is ordinary traffic, so neither an
    # ordinary fail nor a further RESET-TRUNCATED finding is allowed.
    dispose_run(
        bringup,
        test=f"{test} post-reset",
        log=dut._log,
        repro=repro,
    )

    bus_summary = bringup.bus.summary() if bringup.bus is not None else "bus=<off>"
    dut._log.info(
        "%s L1 passed: OE cleared, truncated disposed, post-reset copy ok (%s)",
        test,
        bus_summary,
    )


async def _await_mid_txn_engine(dut, *, repro: str) -> None:
    """Start a long write and stop while ``busy`` with CE# low.

    Later write nibbles are not advanced on purpose: the engine keeps CE# low
    for the full ``byte_len`` window, which is enough to prove mid-txn abort.
    """
    payload = bytes(range(0x40, 0x40 + _ENGINE_WRITE_LEN))
    nibbles = _bytes_to_nibbles(payload)

    assert _level(dut.busy) == 0, f"engine busy before start. {repro}"
    dut.cmd.value = QSPI_CMD_WRITE
    dut.addr.value = _ENGINE_WRITE_ADDR
    dut.device_sel.value = 0
    dut.byte_len.value = _ENGINE_WRITE_LEN
    dut.wdata.value = nibbles[0]
    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0

    for _ in range(_MID_TXN_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if _level(dut.busy) == 1 and _level(dut.psram0_ce_n) == 0:
            for _ in range(8):
                await RisingEdge(dut.clk)
                assert _level(dut.busy) == 1, f"busy cleared before reset. {repro}"
                assert _level(dut.psram0_ce_n) == 0, f"CE# rose before reset. {repro}"
            return
    raise AssertionError(f"never observed mid-txn engine busy/CE#. {repro}")


async def _q_rst_engine(dut, config: dict) -> None:
    """L0: abort mid-engine write, clear ``sio_oe``, dispose, restart."""
    test = "TC-QRST-ACTIVE"
    repro = _repro(config, "qspi_reset_protocol")
    dut._log.info(repro)

    bringup = await bring_up_engine(dut, fill=FILL)
    psram0 = bringup.psram0
    psram1 = bringup.psram1

    await _await_mid_txn_engine(dut, repro=repro)
    assert int(dut.sio_oe.value) != 0, f"{test}: expected engine SIO OE mid-txn. {repro}"

    for agent in bringup.agents:
        agent.note_reset()
    dut.txn_valid.value = 0
    dut.rst_n.value = 0

    # Combinational OE clear is L1-only; L0 OE clears on the sampled reset edge.
    for _ in range(5):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert _level(dut.rst_n) == 0, f"{test}: rst_n not held low across sampled edge"
        assert _level(dut.busy) == 0, f"{test}: busy not cleared after sampled reset"
        assert int(dut.sio_oe.value) == 0, f"{test}: sio_oe not cleared after reset"
        assert _level(dut.psram0_ce_n) == 1 and _level(dut.psram1_ce_n) == 1
        assert _level(dut.psram_sck) == 0
    await NextTimeStep()

    truncated = _dispose_reset_window(
        bringup, test=test, log=dut._log, repro=repro
    )
    assert any(finding.source.startswith("PSRAM") for finding in truncated), (
        f"{test}: expected model RESET-TRUNCATED from mid-txn engine abort, "
        f"observed {[str(finding) for finding in truncated]}. {repro}"
    )

    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    bringup.clear()
    bringup.clear_transactions()

    # Subsequent legal short write must complete cleanly.
    payload = bytes([0xA5])
    nibbles = _bytes_to_nibbles(payload)
    dut.cmd.value = QSPI_CMD_WRITE
    dut.addr.value = 0x000500
    dut.device_sel.value = 0
    dut.byte_len.value = 1
    dut.wdata.value = nibbles[0]
    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0

    nibble_idx = 0
    saw_busy = False
    for _ in range(_MID_TXN_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        await ReadOnly()
        busy = _level(dut.busy)
        wn = _level(dut.wdata_next)
        await NextTimeStep()
        if wn == 1 and nibble_idx + 1 < len(nibbles):
            nibble_idx += 1
            dut.wdata.value = nibbles[nibble_idx]
        if busy:
            saw_busy = True
        if saw_busy and busy == 0:
            break
    else:
        raise AssertionError(f"{test}: post-reset engine write did not finish. {repro}")

    await RisingEdge(dut.clk)
    assert psram0.read(0x000500, 1) == payload, (
        f"{test}: post-reset engine write mismatch. {repro}"
    )
    dispose_run(
        bringup,
        test=f"{test} post-reset",
        log=dut._log,
        repro=repro,
    )

    bus_summary = bringup.bus.summary() if bringup.bus is not None else "bus=<off>"
    dut._log.info(
        "%s L0 passed: busy/OE cleared, truncated disposed, post-reset write ok (%s)",
        test,
        bus_summary,
    )


@cocotb.test()
async def qspi_reset_protocol(dut):
    """TC-QRST-ACTIVE at the DUT level selected by ``LEVEL``."""
    config = parse_run_config()
    dut._log.info(
        "SEED=%d LEVEL=%s SIM=%s DUT_LEVEL=%s",
        config["seed"],
        config["level"],
        config["sim"],
        config["dut_level"],
    )
    if config["dut_level"] == "L0":
        await _q_rst_engine(dut, config)
    else:
        await _q_rst_top(dut, config)
