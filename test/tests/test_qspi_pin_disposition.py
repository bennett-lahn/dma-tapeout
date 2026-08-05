"""M1 T9 disposition for ``CHK-PIN-ADDR23-ZERO`` and ``CHK-PIN-KNOWN``.

Path: model-side pin decode already proves these catalog rows via ``Q-ADDR23``
and ``Q-SIO-X`` (see ``monitors.qspi.dispose_model_pin_checks``). This module
prints explicit pass/fail dispositions and proves each ID can fail.

Test-case IDs:
    TC-PIN-DISP-PASS   - legal traffic leaves both IDs pass
    TC-PIN-DISP-ADDR23 - ``A[23]`` set → ``CHK-PIN-ADDR23-ZERO=fail`` via ``Q-ADDR23``
    TC-PIN-DISP-KNOWN  - SIO X in a host-driven write → ``CHK-PIN-KNOWN=fail``
                         via ``Q-SIO-X``

L1 (``LEVEL=top``) owns the directed evidence. L0 directed pass evidence is
also printed from ``tests.test_qspi`` after legal engine traffic.
"""

import cocotb
from cocotb.triggers import Timer

from common.config import parse_run_config
from common.host import QpiPassthroughMaster
from models.psram import (
    Q_ADDR23,
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
    MODEL_DISPOSE_VIA,
    assert_model_pin_disposition,
)

FILL = 0x00

_ATTACHED: list = []


async def _bring_up(dut):
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

    psram0, psram1 = attach_dual_psram(dut, fill=FILL)
    _ATTACHED.extend([psram0, psram1])

    master = QpiPassthroughMaster(dut)
    await master.park()
    return psram0, psram1, master


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_pin_disposition TEST_FILTER={test}"
    ).format(level=config["level"], sim=config["sim"], seed=config["seed"], test=test)


def _clear_agent_logs(*psrams) -> None:
    for psram in psrams:
        psram.agent.violations.clear()
        psram.agent.transactions.clear()


@cocotb.test()
async def pin_dispose_legal_frames_pass(dut):
    """TC-PIN-DISP-PASS: legal write/read leave ADDR23 and KNOWN as pass."""
    config = parse_run_config()
    repro = _repro(config, "pin_dispose_legal_frames_pass")
    dut._log.info(repro)

    psram0, psram1, master = await _bring_up(dut)
    psram1.write(0x001000, b"\xDE\xAD")

    await master.frame(0, QSPI_CMD_WRITE, 0x000040, write_data=b"\x11\x22")
    await master.frame(1, QSPI_CMD_FAST_READ, 0x001000, dummy_cycles=6, read_bytes=2)

    assert_model_pin_disposition(
        psram0, psram1, log=dut._log, test="TC-PIN-DISP-PASS"
    )
    dut._log.info(
        "TC-PIN-DISP-PASS: %s=%s %s=%s",
        CHK_PIN_ADDR23_ZERO,
        MODEL_DISPOSE_VIA[CHK_PIN_ADDR23_ZERO],
        CHK_PIN_KNOWN,
        MODEL_DISPOSE_VIA[CHK_PIN_KNOWN],
    )


@cocotb.test()
async def pin_dispose_addr23_fails(dut):
    """TC-PIN-DISP-ADDR23: ``Q-ADDR23`` disposes ``CHK-PIN-ADDR23-ZERO=fail``."""
    config = parse_run_config()
    repro = _repro(config, "pin_dispose_addr23_fails")
    dut._log.info(repro)

    psram0, psram1, master = await _bring_up(dut)
    await master.frame(0, QSPI_CMD_WRITE, 0x800040, write_data=b"\x5A")

    records = list(psram0.agent.violations) + list(psram1.agent.violations)
    assert any(record.code == Q_ADDR23 for record in records), (
        "TC-PIN-DISP-ADDR23: expected Q-ADDR23: " + format_violations(records)
    )
    assert_model_pin_disposition(
        psram0,
        psram1,
        log=dut._log,
        expect_fail=(CHK_PIN_ADDR23_ZERO,),
        test="TC-PIN-DISP-ADDR23",
    )
    assert psram0.read(0x000040, 1) == bytes([FILL]), "A[23] frame reached memory"


def _sio_uio_mask(*sio_indices: int) -> int:
    indices = sio_indices if sio_indices else range(len(SIO_UIO_BITS))
    mask = 0
    for index in indices:
        mask |= 1 << SIO_UIO_BITS[index]
    return mask


@cocotb.test()
async def pin_dispose_known_sio_x_fails(dut):
    """TC-PIN-DISP-KNOWN: SIO X on a write beat → ``CHK-PIN-KNOWN=fail``.

    Floating ``z`` is idealized to 0 on the model plane (``tb_top``), so this
    case injects dual-drive X on SIO0 during one host-driven write nibble.
    ``Q-SIO-X`` fires and disposes ``CHK-PIN-KNOWN``.
    """
    config = parse_run_config()
    repro = _repro(config, "pin_dispose_known_sio_x_fails")
    dut._log.info(repro)

    psram0, psram1, master = await _bring_up(dut)
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

    records = list(psram0.agent.violations) + list(psram1.agent.violations)
    assert any(record.code == Q_SIO_X for record in records), (
        "TC-PIN-DISP-KNOWN: expected Q-SIO-X: " + format_violations(records)
    )
    assert_model_pin_disposition(
        psram0,
        psram1,
        log=dut._log,
        expect_fail=(CHK_PIN_KNOWN,),
        test="TC-PIN-DISP-KNOWN",
    )
