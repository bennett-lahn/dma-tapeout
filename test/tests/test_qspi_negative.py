"""Per-device PSRAM protocol-policing negative tests (M1).

Each test injects one malformed QPI frame and requires the matching model
violation ID, so no policing rule can silently stop working. Contract:
``docs/llm/verification/03-psram-model.md`` (protocol-policing table and model
acceptance) and ``docs/llm/verification/06-checkers.md`` (a negative test names
the expected ID and the exact allowed occurrence count).

Stimulus comes from :class:`common.host.QpiPassthroughMaster` under
``BUS_GNT`` (legal MCU ownership per D26/D22) after
:func:`common.bringup.bring_up_top`, so the ASIC stays out of reset and the
pin monitor can dispose ``CHK-PIN-*`` on the ordinary path. Controller and
handshake monitors stay off here: MCU pass-through QPI is not DMA controller
traffic and would mis-classify as CTRL/HS failures. Shared-bus IDs
(``Q-MUX``, ``Q-SIO-OWN``, ``Q-SCKIDLE``, flash CS) belong to the bus-level
monitor and are not exercised here. CE# AC thresholds are off for this
module: MCU pass-through frames leave less than ``tCPH`` between bursts.
ASIC-selected illegal SIO (monitors attached) is ``TC-QPI-ASIC-SIO-X`` in
``tests.test_qspi``.

Test-case IDs:
    TC-QNEG-BASELINE
    TC-QNEG-OPCODE
    TC-QNEG-PHASE
    TC-QNEG-DUMMY
    TC-QNEG-DUMMY-EXTRA
    TC-QNEG-NIBBLE-ODD
    TC-QNEG-PAGE
    TC-ADDR23-DONTCARE
    TC-QNEG-SIO-X
    TC-QNEG-SIO-Z
    TC-QNEG-SIO-Z-READ-OK
    TC-QNEG-SCK-HIZ
    TC-QNEG-SCK-FLOAT-GRANT-RST
    TC-QNEG-CE-X
    TC-QNEG-ADDR-RANGE
    TC-QNEG-DRIVE-DESEL
    TC-QNEG-STRICT
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.runlog import begin_run
from common.constants import FILL, UIO_PSRAM_CE_BITS, UIO_SCK_BIT
from common.dispose import REVIEW, dispose_run, expect
from common.host import QpiPassthroughMaster, assert_bus_req
from models.psram import (
    CLASS_FAIL,
    CLASS_RESET_TRUNCATED,
    PSRAM_ADDR_MASK,
    PSRAM_PAGE_SIZE,
    Q_ADDR_RANGE,
    Q_DRIVE_DESEL,
    Q_DUMMY,
    Q_NIBBLE_ODD,
    Q_OPCODE,
    Q_PAGE,
    Q_PHASE,
    Q_SIO_X,
    QSPI_CMD_FAST_READ,
    QSPI_CMD_WRITE,
    SIO_UIO_BITS,
    format_violations,
)
from monitors.qspi import CHK_PIN_KNOWN, CHK_PIN_SCK_PARK

QSPI_CMD_QUAD_WRITE = 0x38  # device-supported, outside the frozen V1 allowlist

TOP_ADDR = PSRAM_ADDR_MASK  # 0x7FFFFF
_BUS_GNT_BIT = 1

async def _await_bus_gnt(dut, *, cycles: int = 32) -> None:
    await assert_bus_req(dut, hold=True)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> _BUS_GNT_BIT) & 1:
            return
    raise AssertionError("BUS_GNT did not assert after BUS_REQ")

async def _release_bus_gnt(dut, *, cycles: int = 32) -> None:
    await assert_bus_req(dut, hold=False)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if not ((int(dut.uo_out.value) >> _BUS_GNT_BIT) & 1):
            return
    raise AssertionError("BUS_GNT did not drop after BUS_REQ release")

async def _bring_up_passthrough(dut, **bringup_kwargs):
    """Attach via shared bring-up, grant the bus, and park the MCU master.

    ``controller_monitor`` and ``handshake_monitor`` default off so MCU
    pass-through QPI is not scored as DMA controller traffic. Pin monitor
    stays on for ``CHK-PIN-*`` disposal. ``ce_monitor`` defaults off: MCU
    frame gaps sit under ``tCPH``, and this suite judges protocol policing,
    not CE# AC.
    """
    kwargs = {
        "fill": FILL,
        "controller_monitor": False,
        "handshake_monitor": False,
        "ce_monitor": False,
    }
    kwargs.update(bringup_kwargs)
    bringup = await bring_up_top(dut, **kwargs)
    bringup.clear()
    await _await_bus_gnt(dut)
    master = QpiPassthroughMaster(dut)
    await master.park()
    return bringup, master

async def _finish(bringup, master, dut, *, test: str, repro: str, expect_fail=()):
    """Park the master, release ``BUS_GNT``, and dispose the run."""
    await master.park()
    await _release_bus_gnt(dut)
    return dispose_run(
        bringup,
        test=test,
        log=dut._log,
        expect_fail=expect_fail,
        repro=repro,
    )

def _model_records(bringup) -> list:
    records = []
    for device in bringup.devices:
        records.extend(device.agent.violations)
    return records

def _level(handle) -> "int | None":
    try:
        return int(handle.value)
    except ValueError:
        return None

def _read_launches(device) -> int:
    """Count wrapper ``read-launch`` events (falling SCK that sourced a nibble)."""
    events = getattr(device, "timing_events", ())
    return sum(1 for event in events if event.get("kind") == "read-launch")

def _sio_uio_mask(*sio_indices: int) -> int:
    indices = sio_indices if sio_indices else range(len(SIO_UIO_BITS))
    mask = 0
    for index in indices:
        mask |= 1 << SIO_UIO_BITS[index]
    return mask

@cocotb.test()
async def qpi_negative_baseline_frames_are_clean(dut):
    """TC-QNEG-BASELINE: legal write and read frames record no violation.

    Guards against a policing rule that fires on legal traffic, which would make
    every other case in this module meaningless.
    """
    config, repro = begin_run(dut, "baseline")
    bringup, master = await _bring_up_passthrough(dut)
    psram0, psram1 = bringup.psram0, bringup.psram1

    psram1.write(0x001000, b"\xDE\xAD\xBE\xEF")

    await master.frame(0, QSPI_CMD_WRITE, 0x000040, write_data=b"\x11\x22")
    await master.frame(1, QSPI_CMD_FAST_READ, 0x001000, dummy_cycles=6, read_bytes=4)

    written = psram0.agent.transactions[-1]
    assert written.complete, f"write transaction not clean: {written}"
    assert written.opcode == QSPI_CMD_WRITE
    assert written.start_address == 0x000040
    assert bytes(written.write_bytes) == b"\x11\x22"
    assert psram0.read(0x000040, 2) == b"\x11\x22"

    read = psram1.agent.transactions[-1]
    assert read.complete, f"read transaction not clean: {read}"
    assert read.dummy_cycles == 6, f"expected six dummy cycles, saw {read.dummy_cycles}"
    assert read.data_nibbles == 8, f"expected eight data nibbles, saw {read.data_nibbles}"
    assert bytes(read.read_bytes) == b"\xDE\xAD\xBE\xEF"
    assert read.ce_low_ns is not None and read.ce_low_ns > 0

    await _finish(bringup, master, dut, test="TC-QNEG-BASELINE", repro=repro)

@cocotb.test()
async def qpi_negative_unsupported_opcode(dut):
    """TC-QNEG-OPCODE: ``0x38`` is a device command but not in the V1 allowlist."""
    config, repro = begin_run(dut, "opcode")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    await master.frame(0, QSPI_CMD_QUAD_WRITE, 0x000100, write_data=b"\x5A")

    records = _model_records(bringup)
    assert "0x38" in records[0].detail, f"opcode not identified: {records[0]}"
    assert psram0.read(0x000100, 1) == bytes([FILL]), "rejected opcode still wrote memory"
    assert not psram0.agent.transactions[-1].complete
    dut._log.info(
        "TC-QNEG-OPCODE recorded: %s", format_violations(records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-OPCODE",
        repro=repro,
        expect_fail=[expect(Q_OPCODE, count=1)],
    )

@cocotb.test()
async def qpi_negative_truncated_phases(dut):
    """TC-QNEG-PHASE: CE# rises inside the command phase, then inside address.

    Model axis records both truncations as ``Q-PHASE`` (CE# rose before the
    command or address phase completed) with nibble-count details. Pin-axis
    ``Q-PHASE`` pending is for dispose/stop of a still-open CE# frame only; a
    completed CE# rise does not double-count via the pin ledger.
    """
    config, repro = begin_run(dut, "phase")
    bringup, master = await _bring_up_passthrough(dut)

    await master.frame(0, QSPI_CMD_FAST_READ, None, cmd_nibbles=1)
    await master.frame(1, QSPI_CMD_FAST_READ, 0x000100, addr_nibbles=3)

    phase_records = [r for r in _model_records(bringup) if r.code == Q_PHASE]
    details = " | ".join(record.detail for record in phase_records)
    assert len(phase_records) == 2, (
        f"expected 2 model Q-PHASE (command + address truncation), got "
        f"{len(phase_records)}: {details or '<none>'}"
    )
    assert "1/2 command nibbles" in details, f"command nibble count missing: {details}"
    assert "3/6 address nibbles" in details, f"address nibble count missing: {details}"
    dut._log.info(
        "TC-QNEG-PHASE recorded: %s", format_violations(phase_records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-PHASE",
        repro=repro,
        # count=2 is the two labeled model truncations (not model+pin of one).
        expect_fail=[expect(Q_PHASE, count=2)],
    )

@cocotb.test()
async def qpi_negative_wrong_dummy_count(dut):
    """TC-QNEG-DUMMY: ``0xEB`` terminated after four of six dummy cycles."""
    config, repro = begin_run(dut, "dummy")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    await master.frame(0, QSPI_CMD_FAST_READ, 0x000200, dummy_cycles=4)

    records = _model_records(bringup)
    assert "4 dummy cycles" in records[0].detail, f"dummy count missing: {records[0]}"
    assert psram0.agent.transactions[-1].dummy_cycles == 4
    dut._log.info(
        "TC-QNEG-DUMMY recorded: %s", format_violations(records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-DUMMY",
        repro=repro,
        expect_fail=[expect(Q_DUMMY, count=1)],
    )

@cocotb.test()
async def qpi_negative_dummy_then_extra_clock(dut):
    """TC-QNEG-DUMMY-EXTRA: extra dummy cycles are treated as data.

    After six dummy cycles the parser is in DATA; the extra clock is a data
    nibble (``Q-NIBBLE-ODD``: odd data-nibble count at CE# rise), not an extra
    dummy cycle.
    """
    config, repro = begin_run(dut, "dummy_extra")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    await master.frame(
        0, QSPI_CMD_FAST_READ, 0x000210, dummy_cycles=6, read_nibbles=1
    )

    txn = psram0.agent.transactions[-1]
    assert txn.dummy_cycles == 6, f"expected six dummy cycles, saw {txn.dummy_cycles}"
    assert txn.data_nibbles >= 1, f"extra clock must count as data, saw {txn.data_nibbles}"
    records = _model_records(bringup)
    assert not any(record.code == Q_DUMMY for record in records), (
        f"extra post-dummy clock must not fire Q-DUMMY: {format_violations(records)}"
    )
    dut._log.info(
        "TC-QNEG-DUMMY-EXTRA recorded: %s", format_violations(records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-DUMMY-EXTRA",
        repro=repro,
        expect_fail=[expect(Q_NIBBLE_ODD, count=1)],
    )

@cocotb.test()
async def qpi_negative_odd_data_nibble(dut):
    """TC-QNEG-NIBBLE-ODD: half-transferred byte on a write and on a read."""
    config, repro = begin_run(dut, "nibble")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    await master.frame(0, QSPI_CMD_WRITE, 0x000080, data_nibbles=(0x1, 0x2, 0x3))
    await master.frame(1, QSPI_CMD_FAST_READ, 0x000080, dummy_cycles=6, read_nibbles=1)

    assert psram0.read(0x000080, 1) == b"\x12", "complete byte was not committed"
    assert psram0.read(0x000081, 1) == bytes([FILL]), "partial byte was committed"
    assert bytes(psram0.agent.transactions[-1].write_bytes) == b"\x12"

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-NIBBLE-ODD",
        repro=repro,
        expect_fail=[expect(Q_NIBBLE_ODD, count=2)],
    )

@cocotb.test()
async def qpi_negative_page_crossings(dut):
    """TC-QNEG-PAGE: more than one ``PSRAM_PAGE_SIZE`` (1K) crossing fails ``Q-PAGE``.

    Linear Burst may occupy at most two pages per CE# (chip enable, active low)
    pulse: one crossing is legal; two crossings (three pages) fail once.
    """
    config, repro = begin_run(dut, "page")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    # Start at last byte of a 1K page so the first advance crosses once.
    page_end = PSRAM_PAGE_SIZE - 1  # addr & 0x3FF == 1023
    assert page_end & 0x3FF == 1023

    # Control: two bytes → one crossing (two pages); Q-PAGE must not fire.
    await master.frame(0, QSPI_CMD_WRITE, page_end, write_data=b"\xA5\x5A")
    legal = psram0.agent.transactions[-1]
    assert legal.page_crossings == 1, (
        f"expected one page crossing, saw {legal.page_crossings}. " + repro
    )
    assert not any(record.code == Q_PAGE for record in _model_records(bringup)), (
        "one page crossing must not fail Q-PAGE. " + repro
    )

    # Fail: enough bytes for two crossings (third page occupied).
    # page_end + 1025 bytes covers [page_end .. 2*PSRAM_PAGE_SIZE - 1].
    two_cross_len = PSRAM_PAGE_SIZE + 1
    await master.frame(
        0,
        QSPI_CMD_WRITE,
        page_end,
        write_data=bytes((i & 0xFF) for i in range(two_cross_len)),
    )
    illegal = psram0.agent.transactions[-1]
    assert illegal.page_crossings == 2, (
        f"expected two page crossings, saw {illegal.page_crossings}. " + repro
    )
    records = [r for r in _model_records(bringup) if r.code == Q_PAGE]
    assert len(records) == 1, (
        f"expected one Q-PAGE, saw {len(records)}: {format_violations(records)}. "
        + repro
    )
    dut._log.info(
        "TC-QNEG-PAGE recorded: %s", format_violations(records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-PAGE",
        repro=repro,
        expect_fail=[expect(Q_PAGE, count=1)],
    )

@cocotb.test()
async def qpi_address_bit23_dontcare(dut):
    """TC-ADDR23-DONTCARE: ``A[23]`` is masked; access proceeds on ``A[22:0]`` (D35)."""
    config, repro = begin_run(dut, "addr23")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    await master.frame(0, QSPI_CMD_WRITE, 0x800040, write_data=b"\x5A")

    assert psram0.read(0x000040, 1) == b"\x5A", (
        "D35: A[23] must be ignored and the write must land at A[22:0]=0x000040. "
        + repro
    )
    dut._log.info("TC-ADDR23-DONTCARE: write at 0x800040 reached 0x000040")

    await _finish(
        bringup,
        master,
        dut,
        test="TC-ADDR23-DONTCARE",
        repro=repro,
    )

@cocotb.test()
async def qpi_negative_unresolved_sio(dut):
    """TC-QNEG-SIO-X: unresolved SIO (X) during a host-driven write beat.

    Dual-drive X on SIO0 is preserved on the physical bus and fires
    ``Q-SIO-X`` (SIO must not be X when sampled in a host-driven phase),
    which disposes ``CHK-PIN-KNOWN=fail``. Host-driven Hi-Z is a sibling
    (``TC-QNEG-SIO-Z``); legal read dummy/data float must not fire this ID.
    """
    config, repro = begin_run(dut, "sio_x")
    bringup, master = await _bring_up_passthrough(dut)

    await master.open(0)
    await master.send_opcode(QSPI_CMD_WRITE)
    await master.send_address(0x0000C0)

    # One write nibble with SIO0 contended (host=1, fault=0) → X on that bit.
    dut.fault_uio_oe.value = _sio_uio_mask(0)
    dut.fault_uio_drive.value = 0
    await master.send_nibbles([0x1])
    dut.fault_uio_oe.value = 0
    dut.fault_uio_drive.value = 0

    await master.send_data(b"\xA5")
    await master.close()

    records = _model_records(bringup)
    assert "DATA" in records[0].detail, f"phase missing: {records[0]}"
    dut._log.info(
        "TC-QNEG-SIO-X recorded: %s", format_violations(records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-SIO-X",
        repro=repro,
        expect_fail=[
            expect(Q_SIO_X, count=1),
            expect(CHK_PIN_KNOWN, count=1),
        ],
    )

@cocotb.test()
async def qpi_negative_host_driven_sio_z(dut):
    """TC-QNEG-SIO-Z: host-driven write beat with SIO Hi-Z fires Q-SIO-X."""
    config, repro = begin_run(dut, "sio_z")
    bringup, master = await _bring_up_passthrough(dut)

    await master.open(0)
    await master.send_opcode(QSPI_CMD_WRITE)
    await master.send_address(0x0000D0)
    await master.float_clocks(1)
    await master.close()

    records = [r for r in _model_records(bringup) if r.code == Q_SIO_X]
    assert records, "TC-QNEG-SIO-Z: expected Q-SIO-X on host-driven write float. " + repro
    assert "DATA" in records[0].detail, f"phase missing: {records[0]}"
    dut._log.info(
        "TC-QNEG-SIO-Z recorded: %s", format_violations(records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-SIO-Z",
        repro=repro,
        expect_fail=[
            expect(Q_SIO_X, count=1),
            expect(CHK_PIN_KNOWN, count=1),
        ],
    )

@cocotb.test()
async def qpi_negative_read_float_not_sio_x(dut):
    """TC-QNEG-SIO-Z-READ-OK: legal 0xEB dummy/data float must not fire Q-SIO-X."""
    config, repro = begin_run(dut, "sio_z_read_ok")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0
    psram0.write(0x000300, b"\xA5\x5A")

    await master.frame(
        0, QSPI_CMD_FAST_READ, 0x000300, dummy_cycles=6, read_bytes=2
    )

    sio_x = [r for r in _model_records(bringup) if r.code == Q_SIO_X]
    assert not sio_x, (
        "legal read dummy/data float must not fire Q-SIO-X: "
        + format_violations(sio_x)
        + ". "
        + repro
    )
    read = psram0.agent.transactions[-1]
    assert read.complete, f"legal read not complete: {read}. {repro}"
    assert read.dummy_cycles == 6, f"dummy_cycles={read.dummy_cycles}. {repro}"
    assert bytes(read.read_bytes) == b"\xA5\x5A", f"payload mismatch. {repro}"

    await _finish(
        bringup, master, dut, test="TC-QNEG-SIO-Z-READ-OK", repro=repro
    )

@cocotb.test()
async def qpi_negative_sck_hiz_is_not_fall(dut):
    """TC-QNEG-SCK-HIZ: 1->Z is not a falling SCK; 1->Z->driven 0 is a real fall.

    Wrapper ``_TimedPsramDevice._run`` treats unresolved SCK as no-edge and
    keeps last known prev_sck, so Hi-Z after a high half must not launch an
    extra read nibble, and the later driven 0 must still count as a fall.
    Pin/bus monitors are off: SCK Z while selected is the stimulus, not a
    ``CHK-PIN-KNOWN`` target. Closing mid-byte expects ``Q-NIBBLE-ODD``.
    """
    config, repro = begin_run(dut, "sck_hiz")
    bringup, master = await _bring_up_passthrough(
        dut, pin_monitor=False, bus_monitor=False
    )
    psram0 = bringup.psram0
    psram0.write(0x000400, b"\x11\x22")

    await master.open(0)
    await master.send_opcode(QSPI_CMD_FAST_READ)
    await master.send_address(0x000400)
    await master.float_clocks(6)
    txn = psram0.agent._txn
    assert txn is not None, "read frame never opened. " + repro
    assert psram0.agent.phase == "DATA", (
        f"expected DATA after six dummy cycles, got {psram0.agent.phase} "
        f"dummy={txn.dummy_cycles}. {repro}"
    )

    falls = {"n": 0}
    orig_fall = psram0._on_device_fall

    def _count_fall(source_fall_fs: int) -> None:
        falls["n"] += 1
        orig_fall(source_fall_fs)

    psram0._on_device_fall = _count_fall

    master._set_bit(UIO_SCK_BIT, 1)
    master._apply()
    await Timer(10, unit="ns")
    before_launches = _read_launches(psram0)
    before_falls = falls["n"]

    master._oe &= ~(1 << UIO_SCK_BIT) & 0xFF
    master._apply()
    await Timer(10, unit="ns")
    assert _level(dut.bus_sck) is None, f"SCK not physical Z after host OE clear. {repro}"
    assert falls["n"] == before_falls, (
        f"1->Z fabricated a falling SCK (falls {before_falls} -> {falls['n']}). {repro}"
    )
    assert _read_launches(psram0) == before_launches, (
        f"1->Z launched an extra read nibble "
        f"({before_launches} -> {_read_launches(psram0)}). {repro}"
    )

    master._set_bit(UIO_SCK_BIT, 0)
    master._apply()
    await Timer(10, unit="ns")
    assert falls["n"] == before_falls + 1, (
        f"1->Z->driven 0 must still be a fall (falls {before_falls} -> {falls['n']}). "
        + repro
    )
    assert _read_launches(psram0) == before_launches + 1, (
        f"1->Z->driven 0 must launch one read nibble "
        f"({before_launches} -> {_read_launches(psram0)}). {repro}"
    )
    master._set_bit(UIO_SCK_BIT, 1)
    master._apply()
    await Timer(10, unit="ns")
    master._set_bit(UIO_SCK_BIT, 0)
    master._apply()
    await Timer(10, unit="ns")
    await master.close()
    psram0._on_device_fall = orig_fall

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-SCK-HIZ",
        repro=repro,
        expect_fail=[expect(Q_NIBBLE_ODD, count=1)],
    )

@cocotb.test()
async def qpi_negative_grant_reset_sck_float(dut):
    """TC-QNEG-SCK-FLOAT-GRANT-RST: grant/reset OE-clear is physical Z, not forced 0.

    ``Q-SCKIDLE`` (SCK idle low while deselected) watches physical ``bus_sck``
    plus ``asic_sck_oe``: OE=0 + Z is float; parked-low is OE=1 and out=0.
    Grant/reset with ASIC ``uio_oe`` clear must not fabricate a SCK fall, and
    must not pass park solely because a resolver forced 0.
    """
    config, repro = begin_run(dut, "sck_float_grant_rst")
    bringup = await bring_up_top(
        dut,
        fill=FILL,
        controller_monitor=False,
        handshake_monitor=False,
        ce_monitor=False,
    )
    bringup.clear()
    await _await_bus_gnt(dut)
    await Timer(20, unit="ns")

    assert _level(dut.bus_gnt) == 1, f"BUS_GNT not high. {repro}"
    assert int(dut.uio_oe.value) == 0, f"ASIC uio_oe not clear under grant. {repro}"
    assert int(dut.host_uio_oe.value) == 0, f"host OE not clear. {repro}"
    assert _level(dut.asic_sck_oe) == 0, f"asic_sck_oe not 0 under grant. {repro}"
    assert _level(dut.bus_sck) is None, (
        "grant SCK must be physical Z, not a resolver 0. " + repro
    )
    assert _read_launches(bringup.psram0) == 0
    assert _read_launches(bringup.psram1) == 0
    dispose_run(
        bringup,
        test="TC-QNEG-SCK-FLOAT-GRANT",
        log=dut._log,
        repro=repro,
    )
    assert bringup.bus.results()[CHK_PIN_SCK_PARK] != "fail", (
        "Q-SCKIDLE / CHK-PIN-SCK-PARK must not fail on grant float. " + repro
    )

    dut.rst_n.value = 0
    await Timer(20, unit="ns")
    assert int(dut.uio_oe.value) == 0, f"ASIC uio_oe not clear in reset. {repro}"
    assert _level(dut.asic_sck_oe) == 0, f"asic_sck_oe not 0 in reset. {repro}"
    assert _level(dut.bus_sck) is None, (
        "reset SCK must be physical Z, not a resolver 0. " + repro
    )
    assert _read_launches(bringup.psram0) == 0
    dispose_run(
        bringup,
        test="TC-QNEG-SCK-FLOAT-GRANT-RST",
        log=dut._log,
        reset_truncated=REVIEW,
        repro=repro,
    )

@cocotb.test()
async def qpi_negative_unresolved_ce(dut):
    """TC-QNEG-CE-X: unresolved CE# (X) aborts a live frame via termination.

    Mid-command dual-drive X on RAM A CE# must call ``_end_transaction`` so
    ``Q-PHASE`` (CE# rose before command/address completed) is logged as fail,
    not dropped, and not ``RESET-TRUNCATED`` (in-reset/truncated sample; not a
    fail). Pin ``CHK-PIN-KNOWN`` also fires for the unresolved framing pin.
    """
    config, repro = begin_run(dut, "ce_x")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0
    ce_bit = UIO_PSRAM_CE_BITS[0]

    await master.open(0)
    await master.send_opcode(QSPI_CMD_WRITE, nibbles=1)
    assert psram0.agent._txn is not None, "write frame never opened. " + repro
    before = len(psram0.agent.transactions)

    # Host still drives CE# low; fault drives high → X on the framing pin.
    dut.fault_uio_drive.value = 1 << ce_bit
    dut.fault_uio_oe.value = 1 << ce_bit
    await Timer(20, unit="ns")

    assert len(psram0.agent.transactions) == before + 1, (
        "unresolved CE# dropped the in-flight txn without _end_transaction. "
        + repro
    )
    assert psram0.agent._txn is None, "parser still holds an open txn after CE# X"
    phase_records = [
        r for r in _model_records(bringup) if r.code == Q_PHASE
    ]
    assert len(phase_records) == 1, (
        f"expected 1 model Q-PHASE after CE# X, got {len(phase_records)}: "
        f"{format_violations(phase_records) or '<none>'}"
    )
    assert phase_records[0].classification == CLASS_FAIL, (
        f"CE# X abort must be fail, not {phase_records[0].classification!r}. "
        + repro
    )
    assert phase_records[0].classification != CLASS_RESET_TRUNCATED
    assert "1/2 command nibbles" in phase_records[0].detail, (
        f"command nibble count missing: {phase_records[0]}"
    )
    dut._log.info(
        "TC-QNEG-CE-X recorded: %s", format_violations(phase_records) or "<none>"
    )

    # Raise host CE# while fault still drives 1 so the bus resolves high
    # without an X→0 re-select, then clear the injector.
    await master.close()
    dut.fault_uio_oe.value = 0
    dut.fault_uio_drive.value = 0
    await Timer(10, unit="ns")

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-CE-X",
        repro=repro,
        expect_fail=[
            expect(Q_PHASE, count=1),
            expect(CHK_PIN_KNOWN, count=1),
        ],
    )

@cocotb.test()
async def qpi_negative_address_range_no_wrap(dut):
    """TC-QNEG-ADDR-RANGE: a burst past ``0x7FFFFF`` fails instead of wrapping."""
    config, repro = begin_run(dut, "range")
    bringup, master = await _bring_up_passthrough(dut)
    psram0, psram1 = bringup.psram0, bringup.psram1

    psram1.write(TOP_ADDR, b"\x77")
    psram1.write(0x000000, b"\x99")

    await master.frame(0, QSPI_CMD_WRITE, TOP_ADDR, write_data=b"\xA1\xB2")
    await master.frame(1, QSPI_CMD_FAST_READ, TOP_ADDR, dummy_cycles=6, read_bytes=2)

    assert psram0.read(TOP_ADDR, 1) == b"\xA1", "in-range byte was not committed"
    assert psram0.read(0x000000, 1) == bytes([FILL]), "out-of-range write wrapped to zero"
    assert bytes(psram0.agent.transactions[-1].write_bytes) == b"\xA1"

    read = psram1.agent.transactions[-1]
    assert bytes(read.read_bytes) == b"\x77" + bytes([FILL]), (
        f"out-of-range read wrapped instead of failing: {bytes(read.read_bytes)!r}"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-ADDR-RANGE",
        repro=repro,
        expect_fail=[expect(Q_ADDR_RANGE, count=2)],
    )

@cocotb.test()
async def qpi_negative_drive_while_deselected(dut):
    """TC-QNEG-DRIVE-DESEL: model SIO drive with CE# high is a violation."""
    config, repro = begin_run(dut, "desel")
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    agent = psram0.agent
    assert not agent.selected, "CE# should be parked high before injection"
    assert not agent.oe, "model should be released before injection"

    agent.inject_sio_drive(0x5)
    assert agent.oe and agent.driven_nibble == 0x5

    records = _model_records(bringup)
    assert "CE# inactive" in records[0].detail
    dut._log.info(
        "TC-QNEG-DRIVE-DESEL recorded: %s", format_violations(records) or "<none>"
    )

    agent.inject_sio_release()
    await Timer(10, unit="ns")
    assert not agent.oe

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-DRIVE-DESEL",
        repro=repro,
        expect_fail=[expect(Q_DRIVE_DESEL, count=1)],
    )

@cocotb.test()
async def qpi_negative_strict_mode_raises(dut):
    """TC-QNEG-STRICT: ``strict=True`` raises at the first recorded violation."""
    config, repro = begin_run(dut, "strict")
    bringup, master = await _bring_up_passthrough(dut, strict_models=True)
    psram0 = bringup.psram0

    try:
        psram0.agent.inject_sio_drive(0x3)
    except AssertionError as error:
        assert Q_DRIVE_DESEL in str(error), f"strict raise lost the ID: {error}"
    else:
        raise AssertionError("TC-QNEG-STRICT: strict log did not raise on a violation")

    assert psram0.agent.violations.has(Q_DRIVE_DESEL), "strict mode skipped the record"
    psram0.agent.inject_sio_release()

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-STRICT",
        repro=repro,
        expect_fail=[expect(Q_DRIVE_DESEL, count=1)],
    )
