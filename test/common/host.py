"""Host-side START, BUS_REQ, and pass-through drive actions.

Covers stimulus for ``TC-START-*``, ``TC-BUS-*``, and top-level host protocol.
Milestone M0+ fills cocotb drivers; only the plain accepted-pulse case is
wired for M0's smoke test. Phase jitter and held/short-pulse cases are M2+.

:class:`QpiPassthroughMaster` is the MCU-side pin-level QPI master on the shared
``uio`` bus (``host_uio_drive`` / ``host_uio_oe`` in ``tb/tb_top.sv``). It exists
so a test can frame QPI traffic, including deliberately malformed frames, at the
PSRAM models without the ASIC as the master. Per architecture (D26/D22), MCU
drive on shared ``uio`` is legal while ``BUS_GNT`` is high or ``rst_n`` is
low (reset / deselected-design window); callers must establish one of those
conditions before driving. This is not a sim-only exception.
"""

from cocotb.triggers import RisingEdge, Timer

from common.constants import (
    BUS_REQ_BIT,
    DONE_MASK,
    SCK_PERIOD_NS,
    SIO_UIO_BITS,
    START_BIT,
    UIO_FLASH_CS_BIT,
    UIO_PSRAM_CE_BITS,
    UIO_SCK_BIT,
)
from reference.constants import ADDR_NIBBLES, CMD_NIBBLES

# Capture-required START must remain high across three rising clk edges after
# the first possible sample (two-flop sync plus edge detect).
START_HOLD_CYCLES = 3
BUS_REQ_SYNC_CYCLES = 2


async def pulse_start(
    dut, hold_cycles: int = START_HOLD_CYCLES, *, wait_ack: bool = False
) -> None:
    """Issue one START rising-edge pulse after ``BUS_GNT`` is low.

    Default *hold_cycles* is 3 so a capture-required pulse survives the
    two-flop synchronizer. Pass ``hold_cycles=1`` only via
    :func:`pulse_start_one_cycle` for one-cycle state injection.
    """
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << START_BIT)
    for _ in range(hold_cycles):
        await RisingEdge(dut.clk)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current & ~(1 << START_BIT) & 0xFF
    await RisingEdge(dut.clk)
    if wait_ack:
        for _ in range(10_000):
            if (int(dut.uo_out.value) & DONE_MASK) == 0:
                return
            await RisingEdge(dut.clk)
        raise AssertionError("pulse_start wait_ack: DONE never fell")


async def pulse_start_one_cycle(dut) -> None:
    """One-cycle raw START for state-machine injection, not capture-required."""
    await pulse_start(dut, hold_cycles=1)


async def assert_bus_req(dut, hold: bool = True, *, wait_sync: bool = True) -> None:
    """Assert or release raw ``BUS_REQ`` with host release-before-seize model.

    After asserting, wait the two-flop synchronizer so a visible ``bus_req``
    (when hierarchy exposes it) is high on return. Release stays one clock.
    """
    current = int(dut.ui_in.value)
    if hold:
        dut.ui_in.value = current | (1 << BUS_REQ_BIT)
    else:
        dut.ui_in.value = current & ~(1 << BUS_REQ_BIT) & 0xFF
    await RisingEdge(dut.clk)
    if hold and wait_sync:
        for _ in range(BUS_REQ_SYNC_CYCLES):
            await RisingEdge(dut.clk)
        inner = getattr(dut, "dut", None)
        for block in (inner, dut):
            if block is None:
                continue
            handle = getattr(block, "bus_req", None)
            if handle is None:
                continue
            try:
                level = int(handle.value)
            except (ValueError, TypeError, AttributeError):
                break
            assert level == 1, "assert_bus_req returned before synchronized bus_req was high"
            break


class QpiPassthroughMaster:
    """Pin-level MCU-side QPI master on the shared ``uio`` bus.

    Phases are separate coroutines so a test can build a legal frame or stop
    part-way through one, which is what the PSRAM policing negative tests need.
    The master owns flash CS, SCK, and both RAM CE# for the whole run and takes
    SIO only while it drives command, address, or write data; it releases SIO for
    dummy and read-data cycles so the selected model can source them.
    """

    def __init__(self, dut, *, sck_period_ns: float = SCK_PERIOD_NS) -> None:
        self._dut = dut
        self._half_ns = sck_period_ns / 2.0
        self._drive = 0
        self._oe = 0

    # -- pin plumbing ------------------------------------------------------

    def _apply(self) -> None:
        self._dut.host_uio_drive.value = self._drive
        self._dut.host_uio_oe.value = self._oe

    def _set_bit(self, bit: int, value: int) -> None:
        if value:
            self._drive |= 1 << bit
        else:
            self._drive &= ~(1 << bit) & 0xFF
        self._oe |= 1 << bit

    def _drive_sio(self, nibble: int) -> None:
        for sio_bit, uio_bit in enumerate(SIO_UIO_BITS):
            self._set_bit(uio_bit, (nibble >> sio_bit) & 1)

    def _release_sio(self) -> None:
        for uio_bit in SIO_UIO_BITS:
            self._oe &= ~(1 << uio_bit) & 0xFF

    async def _sck_cycle(self) -> None:
        await Timer(self._half_ns, unit="ns")
        self._set_bit(UIO_SCK_BIT, 1)
        self._apply()
        await Timer(self._half_ns, unit="ns")
        self._set_bit(UIO_SCK_BIT, 0)
        self._apply()

    # -- framing -----------------------------------------------------------

    async def park(self) -> None:
        """Own the bus with every chip select high, SCK low, and SIO released."""
        self._set_bit(UIO_FLASH_CS_BIT, 1)
        self._set_bit(UIO_SCK_BIT, 0)
        for ce_bit in UIO_PSRAM_CE_BITS:
            self._set_bit(ce_bit, 1)
        self._release_sio()
        self._apply()
        await Timer(self._half_ns, unit="ns")

    async def open(self, device: int) -> None:
        """Assert one RAM CE# and wait the CE#-setup gap before the first SCK."""
        self._set_bit(UIO_PSRAM_CE_BITS[device], 0)
        self._apply()
        await Timer(self._half_ns, unit="ns")

    async def close(self) -> None:
        """Release SIO, raise both RAM CE#, and wait the CE#-high gap."""
        await Timer(self._half_ns, unit="ns")
        self._release_sio()
        for ce_bit in UIO_PSRAM_CE_BITS:
            self._set_bit(ce_bit, 1)
        self._apply()
        await Timer(self._half_ns, unit="ns")

    async def send_nibbles(self, nibbles) -> None:
        """Clock host-driven nibbles, MSB nibble of each byte first."""
        for nibble in nibbles:
            self._drive_sio(nibble)
            self._apply()
            await self._sck_cycle()

    async def send_opcode(self, opcode: int, *, nibbles: int = CMD_NIBBLES) -> None:
        """Send *nibbles* command nibbles; fewer than two truncates the phase."""
        all_nibbles = [(opcode >> (4 * (CMD_NIBBLES - 1 - index))) & 0xF for index in range(CMD_NIBBLES)]
        await self.send_nibbles(all_nibbles[:nibbles])

    async def send_address(self, address: int, *, nibbles: int = ADDR_NIBBLES) -> None:
        """Send *nibbles* of the 24-bit wire address, most significant first."""
        all_nibbles = [
            (address >> (4 * (ADDR_NIBBLES - 1 - index))) & 0xF for index in range(ADDR_NIBBLES)
        ]
        await self.send_nibbles(all_nibbles[:nibbles])

    async def send_data(self, data: bytes) -> None:
        """Send write-data bytes as upper-nibble-first pairs."""
        nibbles = []
        for value in data:
            nibbles.append((value >> 4) & 0xF)
            nibbles.append(value & 0xF)
        await self.send_nibbles(nibbles)

    async def send_partial_byte(self, nibble: int) -> None:
        """Send a single data nibble, leaving a byte half transferred."""
        await self.send_nibbles([nibble & 0xF])

    async def float_clocks(self, count: int) -> None:
        """Clock *count* cycles with SIO released (dummy or read-data cycles)."""
        self._release_sio()
        self._apply()
        for _ in range(count):
            await self._sck_cycle()

    async def frame(
        self,
        device: int,
        opcode: int,
        address: "int | None" = None,
        *,
        cmd_nibbles: int = CMD_NIBBLES,
        addr_nibbles: int = ADDR_NIBBLES,
        dummy_cycles: int = 0,
        write_data: bytes = b"",
        read_bytes: int = 0,
        read_nibbles: int = 0,
        data_nibbles=(),
        close: bool = True,
    ) -> None:
        """Run one CE#-framed transaction from the given phase lengths.

        Every phase length is a parameter so a malformed frame is expressed as
        data, not as a separate driver.
        """
        await self.open(device)
        await self.send_opcode(opcode, nibbles=cmd_nibbles)
        if address is not None:
            await self.send_address(address, nibbles=addr_nibbles)
        if dummy_cycles:
            await self.float_clocks(dummy_cycles)
        if write_data:
            await self.send_data(write_data)
        if data_nibbles:
            await self.send_nibbles(data_nibbles)
        if read_bytes or read_nibbles:
            await self.float_clocks(2 * read_bytes + read_nibbles)
        if close:
            await self.close()
