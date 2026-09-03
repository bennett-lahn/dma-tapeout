"""Blessed L0 ``qspi_engine`` request BFM (``LEVEL=engine``).

One driver owns the D21 request contract so no test re-derives it:

* request fields (``cmd`` / ``addr`` / ``device_sel`` / ``byte_len``) are driven
  before ``txn_valid`` and held until ``busy`` falls,
* ``txn_valid`` is a single-``clk`` pulse issued only while ``busy=0``,
* the first write nibble rides with ``txn_valid``, and
* every later nibble is presented **after** the ``wdata_next`` cycle has been
  read in the read-only phase and the timestep has advanced
  (``ReadOnly()`` then ``NextTimeStep()``).

That last rule is the reason this module exists. A bare post-edge deposit
(``await RisingEdge(clk)`` immediately followed by ``dut.wdata.value = ...``)
lands in the same timestep the engine registers ``sio_out <= wdata``, so whether
the new or old nibble is captured depends on simulator write-region ordering.
Advancing the timestep first keeps setup into the SIO path deterministic on both
Icarus and Verilator. Do not write ``wdata`` from a test directly; extend this
module instead, and let ``CHK-HS-WDATA-*`` in :mod:`monitors.handshake` police
the result.

L0 scope: this BFM drives ``qspi_engine`` ports only. It cannot cover the
controller ``wdata`` mux; that path is an L1 pin-nibble check (ordered
``DATA_WRITE`` payload vs source bytes). Reads sample ``rdata`` in the
read-only region. After busy falls, one extra IDLE clock covers ``tCPH``
(CE# high gap) before the next request. ``BUSY_TIMEOUT_CYCLES`` (512) is
sized for an 11-byte TCD fetch (cmd+addr+dummy+22 data nibbles at SCK=clk/2)
with margin; timeout is an assertion, not a hang.

Public API (frozen for M2):

* :func:`engine_qpi_write` -> :class:`EngineWriteResult`
* :func:`engine_qpi_read` -> :class:`EngineReadResult`
* :func:`bytes_to_nibbles` / :func:`nibbles_to_bytes`
"""

from dataclasses import dataclass, field

from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

from models.psram import QSPI_CMD_FAST_READ, QSPI_CMD_WRITE

# Longest V1 transaction is an 11-byte TCD fetch (22 data nibbles plus
# command, address, dummy, and CS on/off). SCK is clk/2, so 512 clocks is
# well above 2*(2+6+6+22) plus pad; timeout distinguishes a hang.
BUSY_TIMEOUT_CYCLES = 512


def bytes_to_nibbles(data: bytes) -> "list[int]":
    """Split *data* into QPI nibbles, upper nibble of each byte first."""
    nibbles = []
    for value in data:
        nibbles.append((value >> 4) & 0xF)
        nibbles.append(value & 0xF)
    return nibbles


def nibbles_to_bytes(nibbles) -> bytes:
    """Recombine an even-length nibble stream into bytes."""
    nibbles = list(nibbles)
    assert len(nibbles) % 2 == 0, f"odd nibble count {len(nibbles)}"
    out = bytearray()
    for index in range(0, len(nibbles), 2):
        out.append(((nibbles[index] & 0xF) << 4) | (nibbles[index + 1] & 0xF))
    return bytes(out)


def _level(handle) -> "int | None":
    try:
        return int(handle.value)
    except ValueError:
        return None


@dataclass
class EngineTxnResult:
    """Shared observation window of one engine transaction."""

    device: int
    address: int
    byte_len: int
    ce_trace: "list[tuple[int | None, int | None, int | None]]" = field(
        default_factory=list
    )
    busy_cycles: int = 0


@dataclass
class EngineReadResult(EngineTxnResult):
    """``0xEB`` read: captured ``rdata`` nibbles plus the busy-window CE# trace."""

    nibbles: "list[int]" = field(default_factory=list)

    @property
    def data(self) -> bytes:
        return nibbles_to_bytes(self.nibbles)


@dataclass
class EngineWriteResult(EngineTxnResult):
    """``0x02`` write: observed ``wdata_next`` count and the CE# trace."""

    payload: bytes = b""
    wdata_next_count: int = 0
    nibbles_presented: int = 0

    @property
    def expected_wdata_next(self) -> int:
        return (2 * self.byte_len) - 1


def _assert_idle(dut, what: str) -> None:
    assert _level(dut.busy) == 0, f"engine busy before {what} start"
    assert _level(dut.psram0_ce_n) == 1 and _level(dut.psram1_ce_n) == 1, (
        f"a PSRAM CE# was already low before {what} start"
    )
    assert _level(dut.psram_sck) == 0, f"SCK not parked low before {what} start"


def _drive_request(dut, *, cmd: int, device: int, address: int, byte_len: int) -> None:
    dut.cmd.value = cmd
    dut.addr.value = address & 0xFFFFFF
    dut.device_sel.value = device & 1
    dut.byte_len.value = byte_len


def _sample_ce(dut):
    return (
        _level(dut.psram0_ce_n),
        _level(dut.psram1_ce_n),
        _level(dut.psram_sck),
    )


async def engine_qpi_read(
    dut,
    *,
    device: int,
    address: int,
    length: int,
    timeout_cycles: int = BUSY_TIMEOUT_CYCLES,
) -> EngineReadResult:
    """Issue one ``0xEB`` read and return the captured nibbles and CE# trace.

    Request fields are held until ``busy`` falls (D21). ``ce_trace`` records
    ``(psram0_ce_n, psram1_ce_n, sck)`` on every rising ``clk`` while busy, so a
    test can judge device CE# exclusivity and SCK park after completion.
    """
    _assert_idle(dut, "read")

    _drive_request(
        dut, cmd=QSPI_CMD_FAST_READ, device=device, address=address, byte_len=length
    )
    dut.wdata.value = 0

    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0

    result = EngineReadResult(device=device, address=address, byte_len=length)
    saw_busy = False
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        busy = _level(dut.busy)
        if busy:
            saw_busy = True
            result.busy_cycles += 1
            result.ce_trace.append(_sample_ce(dut))
        if _level(dut.rdata_valid) == 1:
            result.nibbles.append(_level(dut.rdata) & 0xF)
        await NextTimeStep()
        if saw_busy and busy == 0:
            break
    else:
        raise AssertionError(
            "timeout waiting for engine busy to clear "
            f"(device={device} addr=0x{address:06X} len={length})"
        )

    # tCPH: CS_OFF already raised CE# for one clk; spend one IDLE before reuse.
    await RisingEdge(dut.clk)
    return result


async def engine_qpi_write(
    dut,
    *,
    device: int,
    address: int,
    payload: bytes,
    timeout_cycles: int = BUSY_TIMEOUT_CYCLES,
) -> EngineWriteResult:
    """Issue one ``0x02`` write, advancing ``wdata`` on the D21 contract.

    The first nibble rides with ``txn_valid``. Each later nibble is presented
    after leaving the read-only phase of the ``wdata_next`` cycle and advancing
    the timestep, so the engine's next ``sio_out`` capture sees a settled value.
    A write of ``N`` bytes must request exactly ``2*N - 1`` later nibbles; the
    mismatch is raised here and independently by ``CHK-HS-WDATA-COUNT``.
    """
    length = len(payload)
    assert length > 0, "write payload must be non-empty"
    nibbles = bytes_to_nibbles(payload)

    _assert_idle(dut, "write")

    _drive_request(
        dut, cmd=QSPI_CMD_WRITE, device=device, address=address, byte_len=length
    )
    dut.wdata.value = nibbles[0]

    dut.txn_valid.value = 1
    await RisingEdge(dut.clk)
    dut.txn_valid.value = 0

    result = EngineWriteResult(
        device=device, address=address, byte_len=length, payload=payload
    )
    nibble_idx = 0
    saw_busy = False
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        # Settle combinational wdata_next after the edge, then leave the
        # read-only region before driving; see the module docstring for why a
        # bare post-edge deposit is not equivalent.
        await ReadOnly()
        busy = _level(dut.busy)
        wdata_next = _level(dut.wdata_next)
        if busy:
            saw_busy = True
            result.busy_cycles += 1
            result.ce_trace.append(_sample_ce(dut))
        await NextTimeStep()
        if wdata_next == 1:
            result.wdata_next_count += 1
            nibble_idx += 1
            assert nibble_idx < len(nibbles), (
                f"wdata_next past final nibble (idx={nibble_idx} "
                f"len={len(nibbles)} device={device} addr=0x{address:06X})"
            )
            dut.wdata.value = nibbles[nibble_idx]
        if saw_busy and busy == 0:
            break
    else:
        raise AssertionError(
            "timeout waiting for engine busy to clear "
            f"(device={device} addr=0x{address:06X} len={length})"
        )

    result.nibbles_presented = nibble_idx + 1
    assert result.wdata_next_count == result.expected_wdata_next, (
        f"wdata_next count={result.wdata_next_count}, expected "
        f"{result.expected_wdata_next} (device={device} addr=0x{address:06X} "
        f"len={length})"
    )
    assert nibble_idx == len(nibbles) - 1, (
        f"consumed nibble index={nibble_idx}, expected {len(nibbles) - 1}"
    )

    # tCPH: CS_OFF already raised CE# for one clk; spend one IDLE before reuse.
    await RisingEdge(dut.clk)
    return result
