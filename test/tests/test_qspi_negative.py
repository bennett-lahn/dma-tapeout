"""Per-device PSRAM protocol-policing negative tests (M1).

Each test injects one malformed QPI frame and requires the matching model
violation ID, so no policing rule can silently stop working. Contract:
``docs/llm/verification/03-psram-model.md`` (protocol-policing table and model
acceptance) and ``docs/llm/verification/06-checkers.md`` (a negative test names
the expected ID and the exact allowed occurrence count).

Stimulus comes from :class:`common.host.QpiPassthroughMaster` with the ASIC held
in reset, so every ``uio_oe`` bit is clear and the MCU-side master owns the
shared bus. Shared-bus IDs (``Q-MUX``, ``Q-SIO-OWN``, ``Q-SCKIDLE``, flash CS)
belong to the bus-level monitor and are not exercised here.

Test-case IDs:
    TC-QNEG-BASELINE
    TC-QNEG-OPCODE
    TC-QNEG-PHASE
    TC-QNEG-DUMMY
    TC-QNEG-NIBBLE-ODD
    TC-QNEG-ADDR23
    TC-QNEG-SIO-X
    TC-QNEG-ADDR-RANGE
    TC-QNEG-DRIVE-DESEL
    TC-QNEG-STRICT
"""

import cocotb
from cocotb.triggers import Timer

from common.config import parse_run_config
from common.host import QpiPassthroughMaster
from models.psram import (
    PSRAM_ADDR_MASK,
    Q_ADDR23,
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
    attach_dual_psram,
    format_violations,
)
from monitors.qspi import (
    CHK_PIN_ADDR23_ZERO,
    CHK_PIN_KNOWN,
    assert_model_pin_disposition,
)

QSPI_CMD_QUAD_WRITE = 0x38  # device-supported, outside the frozen V1 allowlist

FILL = 0x00
TOP_ADDR = PSRAM_ADDR_MASK  # 0x7FFFFF

# Agents from an earlier test in this module are cancelled before the next
# attach so only one model per device ever drives the shared SIO handles.
_ATTACHED: list = []


async def _bring_up(dut, **attach_kwargs):
    """Hold the ASIC in reset, attach both models, and park the MCU master."""
    for device in _ATTACHED:
        device.agent.stop()
    _ATTACHED.clear()

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    dut.fault_uio_drive.value = 0
    dut.fault_uio_oe.value = 0
    dut.rst_n.value = 0
    await Timer(1, unit="ns")

    psram0, psram1 = attach_dual_psram(dut, fill=FILL, **attach_kwargs)
    _ATTACHED.extend([psram0, psram1])

    master = QpiPassthroughMaster(dut)
    await master.park()
    return psram0, psram1, master


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_negative TEST_FILTER={test}"
    ).format(level=config["level"], sim=config["sim"], seed=config["seed"], test=test)


def _all_violations(*psrams) -> list:
    records = []
    for psram in psrams:
        records.extend(psram.agent.violations)
    return records


def _assert_exact_codes(expected, *psrams, test: str = "", log=None) -> list:
    """Require the observed violation ID multiset to equal *expected*."""
    records = _all_violations(*psrams)
    observed = sorted(record.code for record in records)
    assert observed == sorted(expected), (
        f"{test}: expected violation IDs {sorted(expected)}, observed {observed}: "
        f"{format_violations(records)}"
    )
    if log is not None:
        log.info("%s recorded: %s", test, format_violations(records) or "<none>")
    return records


@cocotb.test()
async def qpi_negative_baseline_frames_are_clean(dut):
    """TC-QNEG-BASELINE: legal write and read frames record no violation.

    Guards against a policing rule that fires on legal traffic, which would make
    every other case in this module meaningless.
    """
    config = parse_run_config()
    dut._log.info(_repro(config, "baseline"))
    psram0, psram1, master = await _bring_up(dut)

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

    _assert_exact_codes([], psram0, psram1, test="TC-QNEG-BASELINE")
    assert_model_pin_disposition(
        psram0, psram1, log=dut._log, test="TC-QNEG-BASELINE"
    )


@cocotb.test()
async def qpi_negative_unsupported_opcode(dut):
    """TC-QNEG-OPCODE: ``0x38`` is a device command but not in the V1 allowlist."""
    config = parse_run_config()
    dut._log.info(_repro(config, "opcode"))
    psram0, psram1, master = await _bring_up(dut)

    await master.frame(0, QSPI_CMD_QUAD_WRITE, 0x000100, write_data=b"\x5A")

    records = _assert_exact_codes([Q_OPCODE], psram0, psram1, test="TC-QNEG-OPCODE")
    assert "0x38" in records[0].detail, f"opcode not identified: {records[0]}"
    assert psram0.read(0x000100, 1) == bytes([FILL]), "rejected opcode still wrote memory"
    assert not psram0.agent.transactions[-1].complete


@cocotb.test()
async def qpi_negative_truncated_phases(dut):
    """TC-QNEG-PHASE: CE# rises inside the command phase, then inside address."""
    config = parse_run_config()
    dut._log.info(_repro(config, "phase"))
    psram0, psram1, master = await _bring_up(dut)

    await master.frame(0, QSPI_CMD_FAST_READ, None, cmd_nibbles=1)
    await master.frame(1, QSPI_CMD_FAST_READ, 0x000100, addr_nibbles=3)

    records = _assert_exact_codes(
        [Q_PHASE, Q_PHASE], psram0, psram1, test="TC-QNEG-PHASE"
    )
    details = " | ".join(record.detail for record in records)
    assert "1/2 command nibbles" in details, f"command nibble count missing: {details}"
    assert "3/6 address nibbles" in details, f"address nibble count missing: {details}"


@cocotb.test()
async def qpi_negative_wrong_dummy_count(dut):
    """TC-QNEG-DUMMY: ``0xEB`` terminated after four of six dummy cycles."""
    config = parse_run_config()
    dut._log.info(_repro(config, "dummy"))
    psram0, psram1, master = await _bring_up(dut)

    await master.frame(0, QSPI_CMD_FAST_READ, 0x000200, dummy_cycles=4)

    records = _assert_exact_codes([Q_DUMMY], psram0, psram1, test="TC-QNEG-DUMMY")
    assert "4 dummy cycles" in records[0].detail, f"dummy count missing: {records[0]}"
    assert psram0.agent.transactions[-1].dummy_cycles == 4


@cocotb.test()
async def qpi_negative_odd_data_nibble(dut):
    """TC-QNEG-NIBBLE-ODD: half-transferred byte on a write and on a read."""
    config = parse_run_config()
    dut._log.info(_repro(config, "nibble"))
    psram0, psram1, master = await _bring_up(dut)

    await master.frame(0, QSPI_CMD_WRITE, 0x000080, data_nibbles=(0x1, 0x2, 0x3))
    await master.frame(1, QSPI_CMD_FAST_READ, 0x000080, dummy_cycles=6, read_nibbles=1)

    _assert_exact_codes(
        [Q_NIBBLE_ODD, Q_NIBBLE_ODD], psram0, psram1, test="TC-QNEG-NIBBLE-ODD"
    )
    assert psram0.read(0x000080, 1) == b"\x12", "complete byte was not committed"
    assert psram0.read(0x000081, 1) == bytes([FILL]), "partial byte was committed"
    assert bytes(psram0.agent.transactions[-1].write_bytes) == b"\x12"


@cocotb.test()
async def qpi_negative_address_bit23(dut):
    """TC-QNEG-ADDR23: ``A[23]`` set fails before any memory access."""
    config = parse_run_config()
    dut._log.info(_repro(config, "addr23"))
    psram0, psram1, master = await _bring_up(dut)

    await master.frame(0, QSPI_CMD_WRITE, 0x800040, write_data=b"\x5A")

    records = _assert_exact_codes([Q_ADDR23], psram0, psram1, test="TC-QNEG-ADDR23")
    assert "0x800040" in records[0].detail, f"wire address missing: {records[0]}"
    assert psram0.read(0x000040, 1) == bytes([FILL]), "A[23] frame reached memory"
    assert psram0.agent.transactions[-1].data_nibbles == 0
    assert_model_pin_disposition(
        psram0,
        psram1,
        log=dut._log,
        expect_fail=(CHK_PIN_ADDR23_ZERO,),
        test="TC-QNEG-ADDR23",
    )


def _sio_uio_mask(*sio_indices: int) -> int:
    indices = sio_indices if sio_indices else range(len(SIO_UIO_BITS))
    mask = 0
    for index in indices:
        mask |= 1 << SIO_UIO_BITS[index]
    return mask


@cocotb.test()
async def qpi_negative_unresolved_sio(dut):
    """TC-QNEG-SIO-X: unresolved SIO (X) during a host-driven write beat.

    ``tb_top``'s model plane replaces floating ``z`` with idle 0, so a released
    SIO does not reach the parser. Dual-drive X on SIO0 is preserved through
    that plane and fires ``Q-SIO-X``, which disposes ``CHK-PIN-KNOWN=fail``.
    """
    config = parse_run_config()
    dut._log.info(_repro(config, "sio_x"))
    psram0, psram1, master = await _bring_up(dut)

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

    records = _assert_exact_codes([Q_SIO_X], psram0, psram1, test="TC-QNEG-SIO-X")
    assert "DATA" in records[0].detail, f"phase missing: {records[0]}"
    assert_model_pin_disposition(
        psram0,
        psram1,
        log=dut._log,
        expect_fail=(CHK_PIN_KNOWN,),
        test="TC-QNEG-SIO-X",
    )


@cocotb.test()
async def qpi_negative_address_range_no_wrap(dut):
    """TC-QNEG-ADDR-RANGE: a burst past ``0x7FFFFF`` fails instead of wrapping."""
    config = parse_run_config()
    dut._log.info(_repro(config, "range"))
    psram0, psram1, master = await _bring_up(dut)

    psram1.write(TOP_ADDR, b"\x77")
    psram1.write(0x000000, b"\x99")

    await master.frame(0, QSPI_CMD_WRITE, TOP_ADDR, write_data=b"\xA1\xB2")
    await master.frame(1, QSPI_CMD_FAST_READ, TOP_ADDR, dummy_cycles=6, read_bytes=2)

    _assert_exact_codes(
        [Q_ADDR_RANGE, Q_ADDR_RANGE], psram0, psram1, test="TC-QNEG-ADDR-RANGE"
    )

    assert psram0.read(TOP_ADDR, 1) == b"\xA1", "in-range byte was not committed"
    assert psram0.read(0x000000, 1) == bytes([FILL]), "out-of-range write wrapped to zero"
    assert bytes(psram0.agent.transactions[-1].write_bytes) == b"\xA1"

    read = psram1.agent.transactions[-1]
    assert bytes(read.read_bytes) == b"\x77" + bytes([FILL]), (
        f"out-of-range read wrapped instead of failing: {bytes(read.read_bytes)!r}"
    )


@cocotb.test()
async def qpi_negative_drive_while_deselected(dut):
    """TC-QNEG-DRIVE-DESEL: model SIO drive with CE# high is a violation."""
    config = parse_run_config()
    dut._log.info(_repro(config, "desel"))
    psram0, psram1, _ = await _bring_up(dut)

    agent = psram0.agent
    assert not agent.selected, "CE# should be parked high before injection"
    assert not agent.oe, "model should be released before injection"

    agent.inject_sio_drive(0x5)
    assert agent.oe and agent.driven_nibble == 0x5

    records = _assert_exact_codes(
        [Q_DRIVE_DESEL], psram0, psram1, test="TC-QNEG-DRIVE-DESEL"
    )
    assert "CE# inactive" in records[0].detail

    agent.inject_sio_release()
    await Timer(10, unit="ns")
    assert not agent.oe


@cocotb.test()
async def qpi_negative_strict_mode_raises(dut):
    """TC-QNEG-STRICT: ``strict=True`` raises at the first recorded violation."""
    config = parse_run_config()
    dut._log.info(_repro(config, "strict"))
    psram0, _psram1, _ = await _bring_up(dut, strict=True)

    try:
        psram0.agent.inject_sio_drive(0x3)
    except AssertionError as error:
        assert Q_DRIVE_DESEL in str(error), f"strict raise lost the ID: {error}"
    else:
        raise AssertionError("TC-QNEG-STRICT: strict log did not raise on a violation")

    assert psram0.agent.violations.has(Q_DRIVE_DESEL), "strict mode skipped the record"
    psram0.agent.inject_sio_release()
