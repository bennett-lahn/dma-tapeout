"""L0 QPI protocol directed tests (``LEVEL=engine``).

Test-case IDs:
    TC-QPI-READ
    TC-QPI-WRITE

Stimulus launches ``qspi_engine`` through the frozen L0 request ports
(``txn_valid`` / ``cmd`` / ``addr`` / ``device_sel`` / ``byte_len``). Oracles are
the PSRAM model transaction log plus engine ``rdata`` / ``rdata_valid`` capture
(and ``wdata`` / ``wdata_next`` for writes).
"""

import cocotb
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge, Timer

from common.clocks import apply_engine_reset, start_clock
from common.config import parse_run_config
from models.psram import (
    ADDR_NIBBLES,
    CMD_NIBBLES,
    FAST_READ_DUMMY_CYCLES,
    QSPI_CMD_FAST_READ,
    QSPI_CMD_WRITE,
    attach_engine_psram,
    format_violations,
)
from monitors.qspi import assert_model_pin_disposition, start_shared_bus_monitor

# Agents from an earlier test in this module are cancelled before the next
# attach so only one model per device ever drives the shared SIO handles.
_ATTACHED: list = []

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

_BUSY_TIMEOUT_CYCLES = 512


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=engine SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi TEST_FILTER={test}"
    ).format(sim=config["sim"], seed=config["seed"], test=test)


def _stop_attached() -> None:
    for device in _ATTACHED:
        device.agent.stop()
    _ATTACHED.clear()


def _level(handle) -> "int | None":
    try:
        return int(handle.value)
    except ValueError:
        return None


def _bytes_to_nibbles(data: bytes) -> list:
    """Upper nibble first per byte; SIO[3]=MSB of each nibble."""
    nibbles = []
    for value in data:
        nibbles.append((value >> 4) & 0xF)
        nibbles.append(value & 0xF)
    return nibbles


def _nibbles_to_bytes(nibbles: list) -> bytes:
    assert len(nibbles) % 2 == 0, f"odd nibble count {len(nibbles)}"
    out = bytearray()
    for index in range(0, len(nibbles), 2):
        out.append(((nibbles[index] & 0xF) << 4) | (nibbles[index + 1] & 0xF))
    return bytes(out)


async def _bring_up(dut):
    """Clock, park/reset the engine, attach both devices, return them."""
    _stop_attached()
    await start_clock(dut)
    await apply_engine_reset(dut)
    attached = attach_engine_psram(dut, devices=(0, 1))
    _ATTACHED.extend(attached)
    return attached


async def _engine_qpi_read(
    dut,
    *,
    device: int,
    address: int,
    length: int,
    timeout_cycles: int = _BUSY_TIMEOUT_CYCLES,
) -> tuple:
    """Issue one ``0xEB`` read and return ``(rdata_nibbles, ce_trace)``.

    Holds the request fields until ``busy`` falls (D21). ``ce_trace`` records
    ``(psram0_ce_n, psram1_ce_n, sck)`` on every rising ``clk`` while busy so
    the test can check device CE# exclusivity and SCK park after completion.

    Write path (``_engine_qpi_write``) mirrors this helper: first ``wdata``
    nibble with ``txn_valid``, then advance on each ``wdata_next`` before the
    following ``clk`` (same-cycle response via ``ReadOnly`` + ``NextTimeStep``).
    """
    assert _level(dut.busy) == 0, "engine busy before read start"
    assert _level(dut.psram0_ce_n) == 1 and _level(dut.psram1_ce_n) == 1
    assert _level(dut.psram_sck) == 0

    dut.cmd.value = QSPI_CMD_FAST_READ
    dut.addr.value = address & 0xFFFFFF
    dut.device_sel.value = device & 1
    dut.byte_len.value = length
    dut.wdata.value = 0

    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0

    nibbles = []
    ce_trace = []
    saw_busy = False
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        busy = _level(dut.busy)
        if busy:
            saw_busy = True
            ce_trace.append(
                (
                    _level(dut.psram0_ce_n),
                    _level(dut.psram1_ce_n),
                    _level(dut.psram_sck),
                )
            )
        if _level(dut.rdata_valid) == 1:
            nibbles.append(_level(dut.rdata) & 0xF)
        if saw_busy and busy == 0:
            break
    else:
        raise AssertionError(
            f"timeout waiting for engine busy to clear "
            f"(device={device} addr=0x{address:06X} len={length})"
        )

    # tCPH: CS_OFF already raised CE# for one clk; spend one IDLE before reuse.
    await RisingEdge(dut.clk)
    return nibbles, ce_trace


async def _engine_qpi_write(
    dut,
    *,
    device: int,
    address: int,
    payload: bytes,
    timeout_cycles: int = _BUSY_TIMEOUT_CYCLES,
) -> tuple:
    """Issue one ``0x02`` write and return ``(wdata_next_count, ce_trace)``.

    First write nibble rides with ``txn_valid``. Each later nibble is presented
    on the same cycle ``wdata_next`` pulses, before the following ``clk``, so
    setup into the engine SIO path is preserved (see ``qspi_engine`` header).
    A write of ``N`` bytes emits exactly ``2*N - 1`` ``wdata_next`` pulses.
    """
    length = len(payload)
    assert length > 0, "write payload must be non-empty"
    nibbles = _bytes_to_nibbles(payload)
    expected_pulses = (2 * length) - 1

    assert _level(dut.busy) == 0, "engine busy before write start"
    assert _level(dut.psram0_ce_n) == 1 and _level(dut.psram1_ce_n) == 1
    assert _level(dut.psram_sck) == 0

    dut.cmd.value = QSPI_CMD_WRITE
    dut.addr.value = address & 0xFFFFFF
    dut.device_sel.value = device & 1
    dut.byte_len.value = length
    dut.wdata.value = nibbles[0]

    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0

    nibble_idx = 0
    wdata_next_count = 0
    ce_trace = []
    saw_busy = False
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        # Settle combo (wdata_next) after the edge, then leave the ReadOnly
        # region before driving. A bare post-edge deposit can land in the
        # simulator's next nb write slot and miss the following clk setup
        # into sio_out (D21 same-cycle contract).
        await ReadOnly()
        busy = _level(dut.busy)
        wn = _level(dut.wdata_next)
        if busy:
            saw_busy = True
            ce_trace.append(
                (
                    _level(dut.psram0_ce_n),
                    _level(dut.psram1_ce_n),
                    _level(dut.psram_sck),
                )
            )
        await NextTimeStep()
        if wn == 1:
            wdata_next_count += 1
            nibble_idx += 1
            assert nibble_idx < len(nibbles), (
                f"wdata_next past final nibble "
                f"(idx={nibble_idx} len={len(nibbles)} device={device} "
                f"addr=0x{address:06X})"
            )
            dut.wdata.value = nibbles[nibble_idx]
        if saw_busy and busy == 0:
            break
    else:
        raise AssertionError(
            f"timeout waiting for engine busy to clear "
            f"(device={device} addr=0x{address:06X} len={length})"
        )

    assert wdata_next_count == expected_pulses, (
        f"wdata_next count={wdata_next_count}, expected {expected_pulses} "
        f"(device={device} addr=0x{address:06X} len={length})"
    )
    assert nibble_idx == len(nibbles) - 1, (
        f"consumed nibble index={nibble_idx}, expected {len(nibbles) - 1}"
    )

    # tCPH: CS_OFF already raised CE# for one clk; spend one IDLE before reuse.
    await RisingEdge(dut.clk)
    return wdata_next_count, ce_trace


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

    psram0, psram1 = await _bring_up(dut)
    devices = (psram0, psram1)
    bus = start_shared_bus_monitor(dut, psram0.agent, psram1.agent, strict=False)

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

        nibbles, ce_trace = await _engine_qpi_read(
            dut, device=device, address=address, length=length
        )

        assert _level(dut.psram0_ce_n) == 1, f"PSRAM0 CE# not high after read. {case_repro}"
        assert _level(dut.psram1_ce_n) == 1, f"PSRAM1 CE# not high after read. {case_repro}"
        assert _level(dut.psram_sck) == 0, f"SCK not parked low after read. {case_repro}"
        _assert_ce_trace(ce_trace, device, repro=case_repro)

        assert len(nibbles) == 2 * length, (
            f"rdata_valid count={len(nibbles)}, expected {2 * length}. {case_repro}"
        )
        assert nibbles == _bytes_to_nibbles(payload), (
            f"engine nibble stream {nibbles} != expected "
            f"{_bytes_to_nibbles(payload)} (payload {payload.hex()}). {case_repro}"
        )
        assert _nibbles_to_bytes(nibbles) == payload, (
            f"engine reconstructed {_nibbles_to_bytes(nibbles).hex()} "
            f"!= {payload.hex()}. {case_repro}"
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

        violations = psram0.agent.violations + psram1.agent.violations
        assert not violations, (
            f"{case}: PSRAM violations: {format_violations(violations)}. {repro}"
        )

    await Timer(100, unit="ns")

    violations = psram0.agent.violations + psram1.agent.violations
    assert not violations, (
        "TC-QPI-READ: PSRAM violations: " + format_violations(violations) + ". " + repro
    )
    # Non-strict monitor: legal engine reads must leave ownership clean.
    assert not bus.violations, (
        "TC-QPI-READ: shared-bus violations: "
        + "; ".join(bus.violations)
        + ". "
        + repro
    )
    assert_model_pin_disposition(
        psram0, psram1, log=dut._log, test="TC-QPI-READ"
    )
    dut._log.info(
        "TC-QPI-READ passed: %d variants (devices 0/1, lengths 1/11)",
        len(_READ_CASES),
    )


@cocotb.test()
async def qpi_write_variants(dut):
    """TC-QPI-WRITE: ``0x02`` writes on each device, lengths 1 and 11."""
    config = parse_run_config()
    repro = _repro(config, "qpi_write_variants")
    dut._log.info(repro)

    psram0, psram1 = await _bring_up(dut)
    devices = (psram0, psram1)
    bus = start_shared_bus_monitor(dut, psram0.agent, psram1.agent, strict=False)

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

        _, ce_trace = await _engine_qpi_write(
            dut, device=device, address=address, payload=payload
        )

        assert _level(dut.psram0_ce_n) == 1, f"PSRAM0 CE# not high after write. {case_repro}"
        assert _level(dut.psram1_ce_n) == 1, f"PSRAM1 CE# not high after write. {case_repro}"
        assert _level(dut.psram_sck) == 0, f"SCK not parked low after write. {case_repro}"
        _assert_ce_trace(ce_trace, device, repro=case_repro)

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

        violations = psram0.agent.violations + psram1.agent.violations
        assert not violations, (
            f"{case}: PSRAM violations: {format_violations(violations)}. {repro}"
        )

    await Timer(100, unit="ns")

    violations = psram0.agent.violations + psram1.agent.violations
    assert not violations, (
        "TC-QPI-WRITE: PSRAM violations: " + format_violations(violations) + ". " + repro
    )
    assert not bus.violations, (
        "TC-QPI-WRITE: shared-bus violations: "
        + "; ".join(bus.violations)
        + ". "
        + repro
    )
    assert_model_pin_disposition(
        psram0, psram1, log=dut._log, test="TC-QPI-WRITE"
    )
    dut._log.info(
        "TC-QPI-WRITE passed: %d variants (devices 0/1, lengths 1/11)",
        len(_WRITE_CASES),
    )
