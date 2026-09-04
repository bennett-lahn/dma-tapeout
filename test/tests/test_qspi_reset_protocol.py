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
from common.runlog import begin_run
from common.constants import (
    DONE_MASK,
    DONE_TIMEOUT_NS,
    DST_ADDR,
    DST_SENTINEL,
    FILL,
    NEXT_TCD_ADDR,
    SRC_ADDR,
    SRC_BYTE,
    TCD_HEAD_ADDR,
)
from common.dispose import REQUIRE, dispose_run
from common.host import pulse_start
from models.psram import QSPI_CMD_WRITE
from monitors.qspi import sck_is_parked
from reference.tcd import Tcd, encode_tcd

POST_SRC_BYTE = 0x5A

_MID_TXN_TIMEOUT_CYCLES = 256
_ENGINE_WRITE_LEN = 11
_ENGINE_WRITE_ADDR = 0x003100

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
    tcd_head = encode_tcd(
        Tcd(
            src_ptr=SRC_ADDR,
            dest_ptr=DST_ADDR,
            transfer_len=1,
            next_tcd=NEXT_TCD_ADDR,
            quit=False,
        )
    )
    tcd_quit = encode_tcd(Tcd(quit=True))
    psram0.write(TCD_HEAD_ADDR, tcd_head)
    psram0.write(NEXT_TCD_ADDR, tcd_quit)
    psram0.write(SRC_ADDR, bytes([src_byte]))
    psram0.write(DST_ADDR, bytes([DST_SENTINEL]))

async def _wait_for_done_pulse(dut) -> None:
    while int(dut.uo_out.value) & DONE_MASK:
        await RisingEdge(dut.clk)
    while not (int(dut.uo_out.value) & DONE_MASK):
        await RisingEdge(dut.clk)

async def _await_mid_txn_top(dut, *, repro: str) -> None:
    """Reach an in-flight ASIC CE# select with shared OE driven."""
    for _ in range(_MID_TXN_TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
        if not (int(dut.uo_out.value) & DONE_MASK):
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
        assert status & DONE_MASK, (
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

async def _q_rst_top(dut, config: dict, repro: str) -> None:
    """L1: abort mid-DMA, clear ``uio_oe``, dispose truncated events, restart."""
    test = "TC-QRST-ACTIVE"

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
    # PendingLedger.carryover intentionally survives monitor.clear() (used by
    # TC-PENDING-SURVIVES-CLEAR). Drop abort-window RESET-TRUNCATED carryover
    # here so the post-reset FORBID dispose only sees the fresh chain.
    for monitor in bringup.monitors:
        pending = getattr(monitor, "pending", None)
        if pending is not None:
            pending.carryover.clear()

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

async def _q_rst_engine(dut, config: dict, repro: str) -> None:
    """L0: abort mid-engine write, clear ``sio_oe``, dispose, restart."""
    test = "TC-QRST-ACTIVE"

    bringup = await bring_up_engine(dut, fill=FILL, ce_monitor=True)
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
    for monitor in bringup.monitors:
        pending = getattr(monitor, "pending", None)
        if pending is not None:
            pending.carryover.clear()

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
    config, repro = begin_run(dut, "qspi_reset_protocol", test="TC-QRST-ACTIVE")
    if config["dut_level"] == "L0":
        await _q_rst_engine(dut, config, repro)
    else:
        await _q_rst_top(dut, config, repro)

async def _q_rst_bfm_auto_top(dut, config: dict, repro: str) -> None:
    """L1: mid-txn ``rst_n`` abort classifies without calling ``note_reset()``."""
    test = "TC-QRST-BFM-AUTO"

    bringup = await bring_up_top(dut, fill=FILL)
    psram0 = bringup.psram0

    _load_smoke_chain(psram0, src_byte=SRC_BYTE)
    bringup.clear()

    await pulse_start(dut)
    await _await_mid_txn_top(dut, repro=repro)
    assert psram0.agent.selected, f"{test}: PSRAM0 not selected mid-txn. {repro}"
    assert psram0.agent.phase != "IDLE", f"{test}: parser idle before reset. {repro}"

    # Falling rst_n alone must abort the BFM; do not call note_reset().
    dut.rst_n.value = 0
    await Timer(1, unit="ns")

    assert psram0.agent.phase == "IDLE", (
        f"{test}: parser still in {psram0.agent.phase!r} after rst_n fall. {repro}"
    )
    truncated = _dispose_reset_window(
        bringup, test=test, log=dut._log, repro=repro
    )
    assert any(finding.source.startswith("PSRAM") for finding in truncated), (
        f"{test}: expected model RESET-TRUNCATED from BFM rst_n watch "
        f"(no note_reset), observed {[str(finding) for finding in truncated]}. "
        + repro
    )
    dut._log.info("%s passed: BFM classified mid-txn abort on rst_n alone", test)

@cocotb.test()
async def qspi_reset_bfm_auto(dut):
    """TC-QRST-BFM-AUTO: BFM watches ``rst_n`` without a manual ``note_reset()``."""
    config, repro = begin_run(dut, "qspi_reset_bfm_auto", test="TC-QRST-BFM-AUTO")
    if config["dut_level"] == "L0":
        # Engine path is covered by TC-QRST-ACTIVE; this directed case is L1-only.
        dut._log.info("TC-QRST-BFM-AUTO skipped at L0 (use LEVEL=top)")
        return
    await _q_rst_bfm_auto_top(dut, config, repro)
