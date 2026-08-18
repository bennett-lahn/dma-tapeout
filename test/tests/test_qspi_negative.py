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

Test-case IDs:
    TC-QNEG-BASELINE
    TC-QNEG-OPCODE
    TC-QNEG-PHASE
    TC-QNEG-DUMMY
    TC-QNEG-NIBBLE-ODD
    TC-ADDR23-DONTCARE
    TC-QNEG-SIO-X
    TC-QNEG-ADDR-RANGE
    TC-QNEG-DRIVE-DESEL
    TC-QNEG-STRICT
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.dispose import dispose_run, expect
from common.host import QpiPassthroughMaster, assert_bus_req
from models.psram import (
    PSRAM_ADDR_MASK,
    Q_ADDR_RANGE,
    Q_DRIVE_DESEL,
    Q_DUMMY,
    Q_NIBBLE_ODD,
    Q_OPCODE,
    Q_PHASE,
    Q_SIO_X,
    QSPI_CMD_FAST_READ,
    QSPI_CMD_WRITE,
    SIO_UIO_BITS,
    format_violations,
)
from monitors.qspi import CHK_PIN_KNOWN

QSPI_CMD_QUAD_WRITE = 0x38  # device-supported, outside the frozen V1 allowlist

FILL = 0x00
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


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_negative TEST_FILTER={test}"
    ).format(level=config["level"], sim=config["sim"], seed=config["seed"], test=test)


def _model_records(bringup) -> list:
    records = []
    for device in bringup.devices:
        records.extend(device.agent.violations)
    return records


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
    config = parse_run_config()
    repro = _repro(config, "baseline")
    dut._log.info(repro)
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
    config = parse_run_config()
    repro = _repro(config, "opcode")
    dut._log.info(repro)
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
    """TC-QNEG-PHASE: CE# rises inside the command phase, then inside address."""
    config = parse_run_config()
    repro = _repro(config, "phase")
    dut._log.info(repro)
    bringup, master = await _bring_up_passthrough(dut)

    await master.frame(0, QSPI_CMD_FAST_READ, None, cmd_nibbles=1)
    await master.frame(1, QSPI_CMD_FAST_READ, 0x000100, addr_nibbles=3)

    records = _model_records(bringup)
    details = " | ".join(record.detail for record in records)
    assert "1/2 command nibbles" in details, f"command nibble count missing: {details}"
    assert "3/6 address nibbles" in details, f"address nibble count missing: {details}"
    dut._log.info(
        "TC-QNEG-PHASE recorded: %s", format_violations(records) or "<none>"
    )

    await _finish(
        bringup,
        master,
        dut,
        test="TC-QNEG-PHASE",
        repro=repro,
        expect_fail=[expect(Q_PHASE, count=2)],
    )


@cocotb.test()
async def qpi_negative_wrong_dummy_count(dut):
    """TC-QNEG-DUMMY: ``0xEB`` terminated after four of six dummy cycles."""
    config = parse_run_config()
    repro = _repro(config, "dummy")
    dut._log.info(repro)
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
async def qpi_negative_odd_data_nibble(dut):
    """TC-QNEG-NIBBLE-ODD: half-transferred byte on a write and on a read."""
    config = parse_run_config()
    repro = _repro(config, "nibble")
    dut._log.info(repro)
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
async def qpi_address_bit23_dontcare(dut):
    """TC-ADDR23-DONTCARE: ``A[23]`` is masked; access proceeds on ``A[22:0]`` (D35)."""
    config = parse_run_config()
    repro = _repro(config, "addr23")
    dut._log.info(repro)
    bringup, master = await _bring_up_passthrough(dut)
    psram0 = bringup.psram0

    await master.frame(0, QSPI_CMD_WRITE, 0x800040, write_data=b"\x5A")

    assert psram0.read(0x000040, 1) == b"\x5A", (
        "D35: A[23] must be ignored and the write must land at A[22:0]=0x000040. "
        + repro
    )
    assert not any(record.code == "Q-ADDR23" for record in _model_records(bringup)), (
        "D35: Q-ADDR23 must not fire. " + repro
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

    ``tb_top``'s model plane replaces floating ``z`` with idle 0, so a released
    SIO does not reach the parser. Dual-drive X on SIO0 is preserved through
    that plane and fires ``Q-SIO-X``, which disposes ``CHK-PIN-KNOWN=fail``.
    """
    config = parse_run_config()
    repro = _repro(config, "sio_x")
    dut._log.info(repro)
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
async def qpi_negative_address_range_no_wrap(dut):
    """TC-QNEG-ADDR-RANGE: a burst past ``0x7FFFFF`` fails instead of wrapping."""
    config = parse_run_config()
    repro = _repro(config, "range")
    dut._log.info(repro)
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
    config = parse_run_config()
    repro = _repro(config, "desel")
    dut._log.info(repro)
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
    config = parse_run_config()
    repro = _repro(config, "strict")
    dut._log.info(repro)
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
