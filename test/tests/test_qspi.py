"""L0 QPI protocol directed tests (``LEVEL=engine``).

Test-case IDs:
    TC-QPI-READ
    TC-QPI-WRITE

Stimulus is the blessed L0 BFM in :mod:`common.engine_bfm`, which drives the
frozen request ports (``txn_valid`` / ``cmd`` / ``addr`` / ``device_sel`` /
``byte_len``) and advances ``wdata`` on the D21 same-cycle contract. Oracles are
the PSRAM model transaction log plus engine ``rdata`` / ``rdata_valid`` capture,
with ``CHK-HS-*`` and the shared-bus ownership monitors running always-on from
:func:`common.bringup.bring_up_engine`.
"""

import cocotb
from cocotb.triggers import Timer

from common.bringup import bring_up_engine
from common.config import parse_run_config
from common.dispose import dispose_run
from common.engine_bfm import (
    bytes_to_nibbles,
    engine_qpi_read,
    engine_qpi_write,
    nibbles_to_bytes,
)
from models.psram import (
    ADDR_NIBBLES,
    CMD_NIBBLES,
    FAST_READ_DUMMY_CYCLES,
    QSPI_CMD_FAST_READ,
    QSPI_CMD_WRITE,
)

# Distinct addresses so each variant's log and memory window is unique.
_READ_CASES = (
    # (device, address, length, payload)
    (0, 0x000100, 1, bytes([0xA5])),
    (0, 0x001000, 11, bytes(range(0x10, 0x10 + 11))),
    (1, 0x000200, 1, bytes([0x5A])),
    (1, 0x002000, 11, bytes((0xC0 + i) & 0xFF for i in range(11))),
)

_WRITE_CASES = (
    # (device, address, length, payload) - windows disjoint from read cases
    (0, 0x000300, 1, bytes([0x3C])),
    (0, 0x003000, 11, bytes(range(0x20, 0x20 + 11))),
    (1, 0x000400, 1, bytes([0xC3])),
    (1, 0x004000, 11, bytes((0xD0 + i) & 0xFF for i in range(11))),
)


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=engine SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi TEST_FILTER={test}"
    ).format(sim=config["sim"], seed=config["seed"], test=test)


def _level(handle) -> "int | None":
    try:
        return int(handle.value)
    except ValueError:
        return None


def _assert_ce_trace(ce_trace, device: int, *, repro: str) -> None:
    """Selected CE# must go low; the other device must stay deselected."""
    assert ce_trace, f"no busy-window CE# samples. {repro}"
    sel_idx = device
    other_idx = 1 - device
    saw_selected_low = False
    for sample in ce_trace:
        sel = sample[sel_idx]
        other = sample[other_idx]
        assert other == 1, (
            f"unselected PSRAM{other_idx} CE# went low during device {device} txn "
            f"(sample={sample}). {repro}"
        )
        if sel == 0:
            saw_selected_low = True
    assert saw_selected_low, (
        f"selected PSRAM{device} CE# never observed low during txn. {repro}"
    )


def _assert_read_txn(txn, *, device: int, address: int, payload: bytes, repro: str) -> None:
    """Check model transaction log against the APS6404L / V1 ``0xEB`` shape."""
    assert txn.complete, f"read transaction incomplete: {txn}. {repro}"
    assert not txn.faults, f"read transaction faults {txn.faults}: {txn}. {repro}"
    assert txn.device_id == device, f"device_id={txn.device_id}, expected {device}. {repro}"
    assert txn.opcode == QSPI_CMD_FAST_READ, (
        f"opcode=0x{txn.opcode:02X}, expected 0xEB. {repro}"
    )
    assert txn.cmd_nibbles == CMD_NIBBLES, (
        f"cmd_nibbles={txn.cmd_nibbles}, expected {CMD_NIBBLES}. {repro}"
    )
    assert txn.addr_nibbles == ADDR_NIBBLES, (
        f"addr_nibbles={txn.addr_nibbles}, expected {ADDR_NIBBLES}. {repro}"
    )
    assert txn.start_address == address, (
        f"start_address=0x{txn.start_address:06X}, expected 0x{address:06X}. {repro}"
    )
    assert txn.dummy_cycles == FAST_READ_DUMMY_CYCLES, (
        f"dummy_cycles={txn.dummy_cycles}, expected {FAST_READ_DUMMY_CYCLES}. {repro}"
    )
    assert txn.data_nibbles == 2 * len(payload), (
        f"data_nibbles={txn.data_nibbles}, expected {2 * len(payload)}. {repro}"
    )
    assert bytes(txn.read_bytes) == payload, (
        f"model read_bytes={bytes(txn.read_bytes).hex()}, "
        f"expected {payload.hex()}. {repro}"
    )
    assert txn.ce_fall_ns is not None and txn.ce_rise_ns is not None
    assert txn.ce_low_ns is not None and txn.ce_low_ns > 0, (
        f"CE# low duration missing/zero: {txn.ce_low_ns}. {repro}"
    )


def _assert_write_txn(txn, *, device: int, address: int, payload: bytes, repro: str) -> None:
    """Check model transaction log against the APS6404L / V1 ``0x02`` shape."""
    assert txn.complete, f"write transaction incomplete: {txn}. {repro}"
    assert not txn.faults, f"write transaction faults {txn.faults}: {txn}. {repro}"
    assert txn.device_id == device, f"device_id={txn.device_id}, expected {device}. {repro}"
    assert txn.opcode == QSPI_CMD_WRITE, (
        f"opcode=0x{txn.opcode:02X}, expected 0x02. {repro}"
    )
    assert txn.cmd_nibbles == CMD_NIBBLES, (
        f"cmd_nibbles={txn.cmd_nibbles}, expected {CMD_NIBBLES}. {repro}"
    )
    assert txn.addr_nibbles == ADDR_NIBBLES, (
        f"addr_nibbles={txn.addr_nibbles}, expected {ADDR_NIBBLES}. {repro}"
    )
    assert txn.start_address == address, (
        f"start_address=0x{txn.start_address:06X}, expected 0x{address:06X}. {repro}"
    )
    assert txn.dummy_cycles == 0, (
        f"dummy_cycles={txn.dummy_cycles}, expected 0 for QPI write. {repro}"
    )
    assert txn.data_nibbles == 2 * len(payload), (
        f"data_nibbles={txn.data_nibbles}, expected {2 * len(payload)}. {repro}"
    )
    assert bytes(txn.write_bytes) == payload, (
        f"model write_bytes={bytes(txn.write_bytes).hex()}, "
        f"expected {payload.hex()}. {repro}"
    )
    assert not txn.read_bytes, (
        f"write transaction unexpectedly logged read_bytes={bytes(txn.read_bytes).hex()}. "
        f"{repro}"
    )
    assert txn.ce_fall_ns is not None and txn.ce_rise_ns is not None
    assert txn.ce_low_ns is not None and txn.ce_low_ns > 0, (
        f"CE# low duration missing/zero: {txn.ce_low_ns}. {repro}"
    )


@cocotb.test()
async def qpi_read_variants(dut):
    """TC-QPI-READ: ``0xEB`` reads on each device, lengths 1 and 11."""
    config = parse_run_config()
    repro = _repro(config, "qpi_read_variants")
    dut._log.info(repro)

    bringup = await bring_up_engine(dut)
    devices = (bringup.psram0, bringup.psram1)

    for device, address, length, payload in _READ_CASES:
        case = f"PSRAM{device} len={length} addr=0x{address:06X}"
        case_repro = f"{case}. {repro}"
        dut._log.info("TC-QPI-READ case: %s", case)

        psram = devices[device]
        other = devices[1 - device]
        before_sel = len(psram.agent.transactions)
        before_other = len(other.agent.transactions)

        psram.write(address, payload)
        # Sentinel past the window: a length overrun would consume it.
        psram.write(address + length, bytes([0xEE]))

        result = await engine_qpi_read(
            dut, device=device, address=address, length=length
        )

        assert _level(dut.psram0_ce_n) == 1, f"PSRAM0 CE# not high after read. {case_repro}"
        assert _level(dut.psram1_ce_n) == 1, f"PSRAM1 CE# not high after read. {case_repro}"
        assert _level(dut.psram_sck) == 0, f"SCK not parked low after read. {case_repro}"
        _assert_ce_trace(result.ce_trace, device, repro=case_repro)

        assert len(result.nibbles) == 2 * length, (
            f"rdata_valid count={len(result.nibbles)}, expected {2 * length}. {case_repro}"
        )
        assert result.nibbles == bytes_to_nibbles(payload), (
            f"engine nibble stream {result.nibbles} != expected "
            f"{bytes_to_nibbles(payload)} (payload {payload.hex()}). {case_repro}"
        )
        assert result.data == payload, (
            f"engine reconstructed {result.data.hex()} != {payload.hex()}. {case_repro}"
        )

        assert len(psram.agent.transactions) == before_sel + 1, (
            f"selected device txn count {len(psram.agent.transactions)}, "
            f"expected {before_sel + 1}. {case_repro}"
        )
        assert len(other.agent.transactions) == before_other, (
            f"unselected device gained a transaction. {case_repro}"
        )
        _assert_read_txn(
            psram.agent.transactions[-1],
            device=device,
            address=address,
            payload=payload,
            repro=case_repro,
        )
        # Backdoor memory is unchanged by a read.
        assert psram.read(address, length) == payload, (
            f"backdoor memory changed during read. {case_repro}"
        )
        assert psram.read(address + length, 1) == b"\xEE", (
            f"read overran into sentinel. {case_repro}"
        )
        # Per-case dispose keeps failure locality; the end-of-test call prints
        # the run's full catalog disposition.
        dispose_run(bringup, test=f"TC-QPI-READ ({case})", repro=repro)

    await Timer(100, unit="ns")

    report = dispose_run(bringup, test="TC-QPI-READ", log=dut._log, repro=repro)
    handshake = bringup.handshake
    assert not handshake.write_nibbles(), (
        f"TC-QPI-READ: handshake monitor saw a write transaction: "
        f"{handshake.summary()}. {repro}"
    )
    dut._log.info(
        "TC-QPI-READ passed: %d variants (devices 0/1, lengths 1/11) | %s | %s | %s | %s",
        len(_READ_CASES),
        handshake.summary(),
        bringup.arbitration.summary(),
        bringup.controller.summary(),
        report.summary(),
    )


@cocotb.test()
async def qpi_write_variants(dut):
    """TC-QPI-WRITE: ``0x02`` writes on each device, lengths 1 and 11."""
    config = parse_run_config()
    repro = _repro(config, "qpi_write_variants")
    dut._log.info(repro)

    bringup = await bring_up_engine(dut)
    devices = (bringup.psram0, bringup.psram1)

    for device, address, length, payload in _WRITE_CASES:
        assert length == len(payload)
        case = f"PSRAM{device} len={length} addr=0x{address:06X}"
        case_repro = f"{case}. {repro}"
        dut._log.info("TC-QPI-WRITE case: %s", case)

        psram = devices[device]
        other = devices[1 - device]
        before_sel = len(psram.agent.transactions)
        before_other = len(other.agent.transactions)

        # Known prior contents + sentinel past the window for overrun detection.
        psram.write(address, bytes([0x55] * length))
        psram.write(address + length, bytes([0xEE]))

        result = await engine_qpi_write(
            dut, device=device, address=address, payload=payload
        )

        assert _level(dut.psram0_ce_n) == 1, f"PSRAM0 CE# not high after write. {case_repro}"
        assert _level(dut.psram1_ce_n) == 1, f"PSRAM1 CE# not high after write. {case_repro}"
        assert _level(dut.psram_sck) == 0, f"SCK not parked low after write. {case_repro}"
        _assert_ce_trace(result.ce_trace, device, repro=case_repro)

        assert len(psram.agent.transactions) == before_sel + 1, (
            f"selected device txn count {len(psram.agent.transactions)}, "
            f"expected {before_sel + 1}. {case_repro}"
        )
        assert len(other.agent.transactions) == before_other, (
            f"unselected device gained a transaction. {case_repro}"
        )
        _assert_write_txn(
            psram.agent.transactions[-1],
            device=device,
            address=address,
            payload=payload,
            repro=case_repro,
        )
        assert psram.read(address, length) == payload, (
            f"backdoor memory after write="
            f"{psram.read(address, length).hex()}, expected {payload.hex()}. "
            f"{case_repro}"
        )
        assert psram.read(address + length, 1) == b"\xEE", (
            f"write overran into sentinel. {case_repro}"
        )
        dispose_run(bringup, test=f"TC-QPI-WRITE ({case})", repro=repro)

    await Timer(100, unit="ns")

    report = dispose_run(bringup, test="TC-QPI-WRITE", log=dut._log, repro=repro)

    # CHK-HS-WDATA-KNOWN keeps the presented nibble stream; compare it against
    # the payloads the BFM drove, independently of the model's pin decode.
    presented = bringup.handshake.write_nibbles()
    expected = [bytes_to_nibbles(payload) for _, _, _, payload in _WRITE_CASES]
    assert presented == expected, (
        f"TC-QPI-WRITE: handshake wdata stream {presented} != driven {expected}. "
        f"{repro}"
    )
    for nibbles, (_, _, _, payload) in zip(presented, _WRITE_CASES):
        assert nibbles_to_bytes(nibbles) == payload, (
            f"TC-QPI-WRITE: presented nibbles rebuild to "
            f"{nibbles_to_bytes(nibbles).hex()}, expected {payload.hex()}. {repro}"
        )

    dut._log.info(
        "TC-QPI-WRITE passed: %d variants (devices 0/1, lengths 1/11) | %s | %s | %s | %s",
        len(_WRITE_CASES),
        bringup.handshake.summary(),
        bringup.arbitration.summary(),
        bringup.controller.summary(),
        report.summary(),
    )
