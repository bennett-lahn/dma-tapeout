"""M1 T9 model-plane evidence for ``Q-SIO-X``.

This module does **not** map model ``Q-SIO-X`` onto ``CHK-PIN-KNOWN`` (that
mapping is tautological when ``pin_monitor=False``). Pin ``CHK-PIN-KNOWN``
coverage lives in suites that start ``QspiPinMonitor`` (``TC-QNEG-SIO-X``).
``pin_monitor`` stays off here so the ASIC can be held in reset for MCU
pass-through; ``CHK-PIN-KNOWN`` / pin ``Q-SIO-X`` are ``na`` at this window.

D35: ``A[23]`` is don't-care (masked to ``A[22:0]``); this module has no
ADDR23=0 fail test.

Test-case IDs:
    TC-PIN-DISP-PASS   - legal traffic leaves model ``Q-SIO-X`` quiet
    TC-PIN-DISP-KNOWN  - SIO X in a host-driven write → ``Q-SIO-X``
    TC-PIN-DISP-KNOWN-Z - host-driven write SIO Hi-Z → ``Q-SIO-X``

L1 (``LEVEL=top``) owns the directed evidence. L0 directed pass evidence is
also printed from ``tests.test_qspi`` after legal engine traffic.
"""

import cocotb
from cocotb.triggers import Timer

from common.bringup import bring_up_top
from common.runlog import begin_run
from common.constants import FILL, REVIEW
from common.dispose import dispose_run, expect
from common.host import QpiPassthroughMaster
from models.psram import (
    Q_SIO_X,
    QSPI_CMD_FAST_READ,
    QSPI_CMD_WRITE,
    SIO_UIO_BITS,
    format_violations,
)

async def _bring_up(dut):
    """Shared L1 attach/clock/reset, then hold ASIC reset for MCU pass-through."""
    bringup = await bring_up_top(dut, fill=FILL, pin_monitor=False)
    # Host drive is legal while rst_n is low (D26/D22); keep ASIC deselected
    # so the MCU master owns uio for model-plane framing.
    dut.rst_n.value = 0
    await Timer(1, unit="ns")

    master = QpiPassthroughMaster(dut)
    await master.park()
    return bringup, master

def _clear_agent_logs(*psrams) -> None:
    for psram in psrams:
        psram.agent.violations.clear()
        psram.agent.transactions.clear()

def _sio_x_records(*psrams) -> list:
    records = []
    for psram in psrams:
        records.extend(r for r in psram.agent.violations if r.code == Q_SIO_X)
    return records

@cocotb.test()
async def pin_dispose_legal_frames_pass(dut):
    """TC-PIN-DISP-PASS: legal write/read leave model ``Q-SIO-X`` quiet."""
    config, repro = begin_run(dut, "pin_dispose_legal_frames_pass")

    bringup, master = await _bring_up(dut)
    psram0, psram1 = bringup.psram0, bringup.psram1
    psram1.write(0x001000, b"\xDE\xAD")

    await master.frame(0, QSPI_CMD_WRITE, 0x000040, write_data=b"\x11\x22")
    await master.frame(1, QSPI_CMD_FAST_READ, 0x001000, dummy_cycles=6, read_bytes=2)

    records = _sio_x_records(psram0, psram1)
    assert not records, (
        "TC-PIN-DISP-PASS: unexpected Q-SIO-X: " + format_violations(records)
    )
    dut._log.info("TC-PIN-DISP-PASS: model Q-SIO-X quiet (pin CHK-PIN-KNOWN na)")
    dispose_run(
        bringup,
        test="TC-PIN-DISP-PASS",
        log=dut._log,
        reset_truncated=REVIEW,
        repro=repro,
    )

def _sio_uio_mask(*sio_indices: int) -> int:
    indices = sio_indices if sio_indices else range(len(SIO_UIO_BITS))
    mask = 0
    for index in indices:
        mask |= 1 << SIO_UIO_BITS[index]
    return mask

@cocotb.test()
async def pin_dispose_known_sio_x_fails(dut):
    """TC-PIN-DISP-KNOWN: SIO X on a write beat → model ``Q-SIO-X``.

    Physical SIO Hi-Z is visible to the parser (no Z-to-0 overlay). This case
    still injects dual-drive X on SIO0 during one host-driven write nibble;
    ``TC-PIN-DISP-KNOWN-Z`` covers a released (Z) host-driven beat.
    Pin ``CHK-PIN-KNOWN`` is not claimed here (``pin_monitor=False``).
    """
    config, repro = begin_run(dut, "pin_dispose_known_sio_x_fails")

    bringup, master = await _bring_up(dut)
    psram0, psram1 = bringup.psram0, bringup.psram1
    _clear_agent_logs(psram0, psram1)

    await master.open(0)
    await master.send_opcode(QSPI_CMD_WRITE)
    await master.send_address(0x000080)

    dut.fault_uio_oe.value = _sio_uio_mask(0)
    dut.fault_uio_drive.value = 0
    await master.send_nibbles([0x1])
    dut.fault_uio_oe.value = 0
    dut.fault_uio_drive.value = 0

    await master.send_data(b"\x5A")
    await master.close()

    records = _sio_x_records(psram0, psram1)
    assert records, (
        "TC-PIN-DISP-KNOWN: expected Q-SIO-X: "
        + format_violations(list(psram0.agent.violations) + list(psram1.agent.violations))
    )
    dut._log.info("TC-PIN-DISP-KNOWN: model Q-SIO-X x%d", len(records))
    dispose_run(
        bringup,
        test="TC-PIN-DISP-KNOWN",
        expect_fail=[expect(Q_SIO_X)],
        log=dut._log,
        reset_truncated=REVIEW,
        repro=repro,
    )

@cocotb.test()
async def pin_dispose_known_sio_z_fails(dut):
    """TC-PIN-DISP-KNOWN-Z: host-driven write SIO Hi-Z → model ``Q-SIO-X``."""
    config, repro = begin_run(dut, "pin_dispose_known_sio_z_fails")

    bringup, master = await _bring_up(dut)
    psram0, psram1 = bringup.psram0, bringup.psram1
    _clear_agent_logs(psram0, psram1)

    await master.open(0)
    await master.send_opcode(QSPI_CMD_WRITE)
    await master.send_address(0x000090)
    await master.float_clocks(1)
    await master.close()

    records = _sio_x_records(psram0, psram1)
    assert records, (
        "TC-PIN-DISP-KNOWN-Z: expected Q-SIO-X: "
        + format_violations(list(psram0.agent.violations) + list(psram1.agent.violations))
    )
    dut._log.info("TC-PIN-DISP-KNOWN-Z: model Q-SIO-X x%d", len(records))
    dispose_run(
        bringup,
        test="TC-PIN-DISP-KNOWN-Z",
        expect_fail=[expect(Q_SIO_X)],
        log=dut._log,
        reset_truncated=REVIEW,
        repro=repro,
    )
