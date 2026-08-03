"""Dual APS6404L-3SQR QPI behavioral models (PSRAM0 and PSRAM1).

Every parser action is taken on a resolved-bus edge: SCK rising, SCK falling, or
a CE# transition. System clock is not observed.

Edge contract (APS6404L Rev 2.3 sections 11.1 / 14.6, summarised in
``docs/llm/05-qspi-psram.md``; model contract in
``docs/llm/verification/03-psram-model.md``):

* CE# falling starts a transaction; CE# rising terminates it and releases SIO.
* Command, address, and write-data nibbles are sampled on **rising** SCK.
* Read-data nibbles are launched on **falling** SCK so the host captures them on
  the following rising SCK.
* ``0xEB`` inserts exactly six dummy SCK cycles between address and data. The
  first read nibble is launched on the falling edge that closes dummy cycle six.
* Within a byte the upper nibble goes first, and ``SIO3`` is the nibble MSB.

Opcodes dispatch through :data:`QPI_COMMANDS`. A new command is a
:class:`QpiCommand` entry (phase lengths, dummy count, data direction) plus a
:class:`QpiDataHandler`; the SCK state machine does not change. The frozen V1
ASIC allowlist is still exactly ``0xEB`` and ``0x02``.

Full protocol policing is the M1 deliverable. This model records what it sees in
:attr:`PsramQpiAgent.violations` and only raises when built with ``strict=True``.
"""

from dataclasses import dataclass, field

import cocotb
from cocotb.triggers import First

PSRAM_ADDR_BITS = 23
PSRAM_SIZE = 1 << PSRAM_ADDR_BITS  # APS6404L: 8 MiB, 23-bit address space
PSRAM_ADDR_MASK = PSRAM_SIZE - 1

QSPI_CMD_FAST_READ = 0xEB
QSPI_CMD_WRITE = 0x02

CMD_NIBBLES = 2
ADDR_NIBBLES = 6
FAST_READ_DUMMY_CYCLES = 6

# Resolved ``uio`` bit map, mirroring src/rtl/top.v and test/tb/tb_top.sv:
# uio[0] flash CS, uio[1,2,4,5] SIO0..3, uio[3] SCK, uio[6:7] PSRAM0/1 CE#.
SIO_UIO_BITS = (1, 2, 4, 5)

DIR_NONE = "none"
DIR_READ = "read"
DIR_WRITE = "write"

PHASE_IDLE = "IDLE"
PHASE_CMD = "CMD"
PHASE_ADDR = "ADDR"
PHASE_DUMMY = "DUMMY"
PHASE_DATA = "DATA"
PHASE_IGNORE = "IGNORE"


def _nibble_from_uio(uio_value: int) -> int:
    """Reconstruct a SIO[3:0] nibble from resolved ``uio`` bits 1, 2, 4, 5."""
    nibble = 0
    for sio_bit, uio_bit in enumerate(SIO_UIO_BITS):
        nibble |= ((uio_value >> uio_bit) & 1) << sio_bit
    return nibble


def _level(handle):
    """Return the integer value of a cocotb *handle*, or ``None`` while it holds X/Z."""
    try:
        return int(handle.value)
    except ValueError:
        return None


def uio_nibble_reader(dut):
    """Return a callable sampling SIO[3:0] from an L1/L2 resolved ``uio`` bus."""
    handle = dut.resolved_uio

    def read():
        value = _level(handle)
        return None if value is None else _nibble_from_uio(value)

    return read


def sio_nibble_reader(handle):
    """Return a callable sampling SIO[3:0] from a 4-bit resolved SIO net (L0)."""

    def read():
        return _level(handle)

    return read


class PsramDevice:
    """Behavioral byte-addressed memory for one APS6404L instance."""

    def __init__(self, device_id: int, fill: int = 0x00) -> None:
        self.device_id = device_id
        self.agent: "PsramQpiAgent | None" = None
        self._mem = bytearray([fill & 0xFF]) * PSRAM_SIZE

    def read(self, address: int, length: int) -> bytes:
        """Read *length* bytes from *address* (backdoor; wraps at the 23-bit limit)."""
        address &= PSRAM_ADDR_MASK
        end = address + length
        if end <= PSRAM_SIZE:
            return bytes(self._mem[address:end])
        return bytes(self._mem[address:PSRAM_SIZE]) + bytes(self._mem[0 : end - PSRAM_SIZE])

    def write(self, address: int, data: bytes) -> None:
        """Write *data* at *address* (backdoor; wraps at the 23-bit limit)."""
        address &= PSRAM_ADDR_MASK
        end = address + len(data)
        if end <= PSRAM_SIZE:
            self._mem[address:end] = data
        else:
            head = PSRAM_SIZE - address
            self._mem[address:PSRAM_SIZE] = data[:head]
            self._mem[0 : end - PSRAM_SIZE] = data[head:]

    def clone_image(self) -> bytearray:
        """Return an independent copy of stored bytes for scoreboard compare."""
        return bytearray(self._mem)


@dataclass
class QpiTransaction:
    """Pin-decoded record of one CE#-framed transaction."""

    device_id: int
    opcode: int = 0
    name: str = "UNDECODED"
    address: int = 0
    start_address: int = 0
    cmd_nibbles: int = 0
    addr_nibbles: int = 0
    dummy_cycles: int = 0
    data_nibbles: int = 0
    read_bytes: bytearray = field(default_factory=bytearray)
    write_bytes: bytearray = field(default_factory=bytearray)
    partial_nibble: "int | None" = None
    pending_low: "int | None" = None
    scratch: dict = field(default_factory=dict)
    complete: bool = False


class QpiDataHandler:
    """Data-phase behavior for one opcode.

    Subclass and override the method matching the command's direction. All
    per-transaction state belongs on the :class:`QpiTransaction`, so a single
    handler instance can be shared by both device models.
    """

    def on_data_rise(self, txn: QpiTransaction, memory: PsramDevice, nibble: int) -> None:
        """Consume one host-driven nibble sampled on rising SCK."""

    def on_data_fall(self, txn: QpiTransaction, memory: PsramDevice) -> "int | None":
        """Return the nibble to launch on this falling SCK, or ``None`` to float."""
        return None


class MemoryReadHandler(QpiDataHandler):
    """Byte-serial read-out with post-byte address increment (``0xEB``).

    The device keeps sourcing data until CE# rises, so the nibble launched on
    the last falling edge of a burst is fetched even though the host never
    clocks it in. ``PsramQpiAgent`` trims that unread tail from the log.
    """

    def on_data_fall(self, txn, memory):
        if txn.pending_low is None:
            value = memory.read(txn.address, 1)[0]
            txn.read_bytes.append(value)
            txn.pending_low = value & 0xF
            return (value >> 4) & 0xF
        nibble = txn.pending_low
        txn.pending_low = None
        txn.address = (txn.address + 1) & PSRAM_ADDR_MASK
        return nibble


class MemoryWriteHandler(QpiDataHandler):
    """Byte-serial write-in with post-byte address increment (``0x02``)."""

    def on_data_rise(self, txn, memory, nibble):
        if txn.partial_nibble is None:
            txn.partial_nibble = nibble
            return
        value = ((txn.partial_nibble << 4) | nibble) & 0xFF
        txn.partial_nibble = None
        memory.write(txn.address, bytes([value]))
        txn.write_bytes.append(value)
        txn.address = (txn.address + 1) & PSRAM_ADDR_MASK


@dataclass(frozen=True)
class QpiCommand:
    """Phase contract for one opcode."""

    name: str
    opcode: int
    direction: str
    handler: QpiDataHandler
    address_nibbles: int = ADDR_NIBBLES
    dummy_cycles: int = 0


QPI_COMMANDS: "dict[int, QpiCommand]" = {}


def register_command(command: QpiCommand) -> QpiCommand:
    """Add *command* to the dispatch table used by new :class:`PsramQpiAgent`s."""
    QPI_COMMANDS[command.opcode] = command
    return command


register_command(
    QpiCommand(
        name="FAST_READ_QUAD",
        opcode=QSPI_CMD_FAST_READ,
        direction=DIR_READ,
        handler=MemoryReadHandler(),
        dummy_cycles=FAST_READ_DUMMY_CYCLES,
    )
)
register_command(
    QpiCommand(
        name="WRITE",
        opcode=QSPI_CMD_WRITE,
        direction=DIR_WRITE,
        handler=MemoryWriteHandler(),
    )
)


class PsramQpiAgent:
    """QPI slave BFM for one PSRAM instance, clocked by SCK and CE#.

    The agent wakes on resolved-bus SCK and CE# transitions only, samples inbound 
    nibbles on rising SCK, and launches read-data nibbles on falling SCK.
    """

    def __init__(
        self,
        memory: PsramDevice,
        *,
        sck,
        ce_n,
        read_nibble,
        drive,
        oe,
        commands: "dict[int, QpiCommand] | None" = None,
        strict: bool = False,
    ) -> None:
        self._memory = memory
        self._sck = sck
        self._ce_n = ce_n
        self._read_nibble = read_nibble
        self._drive = drive
        self._oe = oe
        self._commands = dict(QPI_COMMANDS if commands is None else commands)
        self._strict = strict

        self._phase = PHASE_IDLE
        self._command: "QpiCommand | None" = None
        self._txn: "QpiTransaction | None" = None
        self._driving = False

        self.transactions: "list[QpiTransaction]" = []
        self.violations: "list[str]" = []

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Park SIO released and launch the background BFM task."""
        self._drive.value = 0
        self._oe.value = 0
        return cocotb.start_soon(self._run())

    # -- policing ----------------------------------------------------------

    def _violation(self, code: str, detail: str) -> None:
        message = f"PSRAM{self._memory.device_id} {code}: {detail}"
        self.violations.append(message)
        if self._strict:
            raise AssertionError(message)

    # -- SIO drive ---------------------------------------------------------

    def _drive_nibble(self, nibble: int) -> None:
        self._drive.value = nibble & 0xF
        if not self._driving:
            self._oe.value = 0xF
            self._driving = True

    def _release_sio(self) -> None:
        if self._driving:
            self._oe.value = 0
            self._driving = False

    # -- transaction framing ----------------------------------------------

    def _begin_transaction(self) -> None:
        self._txn = QpiTransaction(device_id=self._memory.device_id)
        self._command = None
        self._phase = PHASE_CMD
        self._release_sio()

    def _end_transaction(self) -> None:
        self._release_sio()
        txn = self._txn
        command = self._command
        self._txn = None
        self._command = None
        self._phase = PHASE_IDLE
        if txn is None:
            return

        # data_nibbles counts rising SCK edges in the data phase, which is what
        # the host actually transferred. Read-out that the device sourced but
        # the host never clocked in is dropped from the log.
        del txn.read_bytes[txn.data_nibbles // 2 :]

        if txn.cmd_nibbles < CMD_NIBBLES:
            self._violation("Q-PHASE", f"CE# rose after {txn.cmd_nibbles} command nibbles")
        elif command is None:
            pass  # unsupported opcode, already reported as Q-OPCODE
        elif txn.addr_nibbles < command.address_nibbles:
            self._violation(
                "Q-PHASE", f"{txn.name}: CE# rose after {txn.addr_nibbles} address nibbles"
            )
        elif txn.dummy_cycles < command.dummy_cycles:
            self._violation(
                "Q-DUMMY", f"{txn.name}: CE# rose after {txn.dummy_cycles} dummy cycles"
            )
        elif txn.data_nibbles % 2:
            self._violation("Q-NIBBLE-ODD", f"{txn.name}: {txn.data_nibbles} data nibbles")
        else:
            txn.complete = True
        self.transactions.append(txn)

    def _decode_opcode(self) -> None:
        txn = self._txn
        command = self._commands.get(txn.opcode)
        if command is None:
            self._violation("Q-OPCODE", f"unsupported opcode 0x{txn.opcode:02X}")
            self._phase = PHASE_IGNORE
            return
        self._command = command
        txn.name = command.name
        if command.address_nibbles:
            self._phase = PHASE_ADDR
        else:
            self._enter_data_phase()

    def _enter_data_phase(self) -> None:
        if self._command.direction == DIR_NONE:
            self._phase = PHASE_IGNORE
        elif self._command.dummy_cycles:
            self._phase = PHASE_DUMMY
        else:
            self._phase = PHASE_DATA

    # -- edge handlers -----------------------------------------------------

    def _on_sck_rise(self) -> None:
        txn = self._txn
        if txn is None:
            return  # clocked while selected but never framed by a CE# fall
        nibble = self._read_nibble()
        if nibble is None:
            self._violation("Q-SIO-X", f"unresolved SIO during {self._phase}")
            return

        if self._phase == PHASE_CMD:
            txn.opcode = ((txn.opcode << 4) | nibble) & 0xFF
            txn.cmd_nibbles += 1
            if txn.cmd_nibbles == CMD_NIBBLES:
                self._decode_opcode()
        elif self._phase == PHASE_ADDR:
            txn.address = ((txn.address << 4) | nibble) & 0xFFFFFF
            txn.addr_nibbles += 1
            if txn.addr_nibbles == self._command.address_nibbles:
                if txn.address & ~PSRAM_ADDR_MASK:
                    self._violation("Q-ADDR23", f"A[23] set in 0x{txn.address:06X}")
                txn.address &= PSRAM_ADDR_MASK
                txn.start_address = txn.address
                self._enter_data_phase()
        elif self._phase == PHASE_DUMMY:
            txn.dummy_cycles += 1
            if txn.dummy_cycles == self._command.dummy_cycles:
                self._phase = PHASE_DATA
        elif self._phase == PHASE_DATA:
            # For a write this is the host nibble; for a read it acknowledges the
            # nibble the device launched on the preceding falling edge.
            txn.data_nibbles += 1
            self._command.handler.on_data_rise(txn, self._memory, nibble)

    def _on_sck_fall(self) -> None:
        if self._phase != PHASE_DATA or self._command.direction != DIR_READ:
            self._release_sio()
            return
        nibble = self._command.handler.on_data_fall(self._txn, self._memory)
        if nibble is None:
            self._release_sio()
        else:
            self._drive_nibble(nibble)

    # -- bus task ----------------------------------------------------------

    async def _run(self) -> None:
        prev_ce = 1
        prev_sck = 0

        while True:
            await First(self._sck.value_change, self._ce_n.value_change)
            ce = _level(self._ce_n)
            sck = _level(self._sck)

            if ce is None or sck is None:
                # Pre-reset X on the shared bus: stay released and resynchronize.
                self._release_sio()
                self._phase = PHASE_IDLE
                self._txn = None
                prev_ce, prev_sck = ce, sck
                continue

            if prev_ce != 0 and ce == 0:
                self._begin_transaction()
            elif prev_ce == 0 and ce != 0:
                self._end_transaction()

            if ce == 0 and prev_sck is not None:
                if sck == 1 and prev_sck == 0:
                    self._on_sck_rise()
                elif sck == 0 and prev_sck == 1:
                    self._on_sck_fall()

            prev_ce, prev_sck = ce, sck


def attach_dual_psram(dut, *, strict: bool = False) -> "tuple[PsramDevice, PsramDevice]":
    """Create both PSRAM memories, start their SCK-driven agents, return them.

    Each returned device exposes its agent as ``device.agent`` for transaction
    logs and recorded protocol violations.
    """
    read_nibble = uio_nibble_reader(dut)

    psram0 = PsramDevice(0)
    psram1 = PsramDevice(1)

    psram0.agent = PsramQpiAgent(
        psram0,
        sck=dut.psram_sck,
        ce_n=dut.psram0_ce_n,
        read_nibble=read_nibble,
        drive=dut.psram0_sio_drive,
        oe=dut.psram0_sio_oe,
        strict=strict,
    )
    psram1.agent = PsramQpiAgent(
        psram1,
        sck=dut.psram_sck,
        ce_n=dut.psram1_ce_n,
        read_nibble=read_nibble,
        drive=dut.psram1_sio_drive,
        oe=dut.psram1_sio_oe,
        strict=strict,
    )

    psram0.agent.start()
    psram1.agent.start()
    return psram0, psram1
