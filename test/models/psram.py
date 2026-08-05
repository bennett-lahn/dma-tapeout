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

Per-device protocol policing (this file owns the single-instance rows of the
``03-psram-model.md`` policing table):

===============  =====================================================
ID               Condition
===============  =====================================================
``Q-OPCODE``     decoded opcode is outside the command table
``Q-PHASE``      CE# rose before the command or address phase completed
``Q-DUMMY``      ``0xEB`` saw a dummy-cycle count other than six
``Q-NIBBLE-ODD`` odd data-nibble count at termination (no partial byte)
``Q-ADDR23``     wire address had ``A[23]`` set
``Q-ADDR-RANGE`` a transferred byte moved past ``0x7FFFFF`` (no wrap)
``Q-DRIVE-DESEL``model drove SIO while its CE# was inactive
``Q-SIO-X``      SIO was unresolved during a host-driven phase
===============  =====================================================

Shared-bus rows (``Q-MUX``, ``Q-SIO-OWN``, ``Q-SCKIDLE``, flash CS, ``Q-CEM``,
``Q-CPH``) belong to a bus-level monitor outside either instance. That monitor
reuses :class:`QpiViolation` / :class:`ViolationLog` here, and reads per-device
state through the public agent API: :attr:`PsramQpiAgent.sio_oe` /
:attr:`PsramQpiAgent.sio_drive` (cocotb handles for the model's 4-bit SIO OE and
drive; ownership checkers watch these), :attr:`PsramQpiAgent.oe` (bool: model
currently drives SIO), :attr:`PsramQpiAgent.driven_nibble`,
:attr:`PsramQpiAgent.selected` (CE# active), :attr:`PsramQpiAgent.ce_n`,
:attr:`PsramQpiAgent.phase`, :attr:`PsramQpiAgent.transactions`, and
:attr:`PsramQpiAgent.violations`. Pass a shared :class:`ViolationLog` into
:func:`attach_dual_psram` / :func:`attach_engine_psram` to aggregate device and
bus records in one ordered log.

Violations are always recorded. They additionally raise when the log is built
with ``strict=True``.
"""

from dataclasses import dataclass, field
from typing import Callable

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import First

PSRAM_ADDR_BITS = 23
PSRAM_SIZE = 1 << PSRAM_ADDR_BITS  # APS6404L: 8 MiB, 23-bit address space
PSRAM_ADDR_MASK = PSRAM_SIZE - 1
PSRAM_PAGE_SIZE = 1024  # Linear Burst page, one crossing per CE# pulse

QSPI_CMD_FAST_READ = 0xEB
QSPI_CMD_WRITE = 0x02

CMD_NIBBLES = 2
ADDR_NIBBLES = 6
FAST_READ_DUMMY_CYCLES = 6

# Stable violation IDs owned by this per-device model.
Q_OPCODE = "Q-OPCODE"
Q_PHASE = "Q-PHASE"
Q_DUMMY = "Q-DUMMY"
Q_NIBBLE_ODD = "Q-NIBBLE-ODD"
Q_ADDR23 = "Q-ADDR23"
Q_ADDR_RANGE = "Q-ADDR-RANGE"
Q_DRIVE_DESEL = "Q-DRIVE-DESEL"
Q_SIO_X = "Q-SIO-X"

# Classification vocabulary from 04-timing-in-sim.md.
CLASS_FAIL = "fail"
CLASS_RESET_TRUNCATED = "RESET-TRUNCATED"

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


def _now_ns() -> float:
    return get_sim_time(unit="ns")


def _oe_bit(handle, index: int) -> bool:
    """True when OE bit *index* is enabled; False if absent or clear."""
    if handle is None:
        return False
    try:
        return bool((int(handle.value) >> index) & 1)
    except ValueError:
        return False


def _value_sio_bit(handle, index: int) -> "int | None":
    """0/1 for one drive bit, or ``None`` if the handle is unresolved."""
    if handle is None:
        return None
    try:
        return (int(handle.value) >> index) & 1
    except ValueError:
        return None


def _sio_contention_unresolved(dut) -> bool:
    """True when two enabled SIO drivers disagree (wired-X that some sims drop).

    Icarus resolves conflicting ``assign`` drivers to X on ``resolved_uio``.
    Verilator often keeps a 2-state winner instead. Detect the same contention
    from OE/drive handles so ``Q-SIO-X`` still fires without weakening the check.
    """
    drivers = []
    for oe_name, drv_name in (
        ("asic_sio_oe", "asic_sio_out"),
        ("fault_sio_oe", "fault_sio_drive"),
        ("host_sio_oe", "host_sio_drive"),
        ("psram0_sio_oe", "psram0_sio_drive"),
        ("psram1_sio_oe", "psram1_sio_drive"),
    ):
        oe_h = getattr(dut, oe_name, None)
        if oe_h is None:
            continue
        drivers.append((oe_h, getattr(dut, drv_name, None)))

    for index in range(4):
        seen = None
        for oe_h, drv_h in drivers:
            if not _oe_bit(oe_h, index):
                continue
            bit = _value_sio_bit(drv_h, index)
            if bit is None:
                return True
            if seen is None:
                seen = bit
            elif bit != seen:
                return True
    return False


def uio_nibble_reader(dut):
    """Return a callable sampling SIO[3:0] from an L1/L2 resolved ``uio`` bus."""
    handle = dut.resolved_uio

    def read():
        if _sio_contention_unresolved(dut):
            return None
        value = _level(handle)
        return None if value is None else _nibble_from_uio(value)

    return read


def sio_nibble_reader(handle, dut=None):
    """Return a callable sampling SIO[3:0] from a 4-bit resolved SIO net (L0)."""

    def read():
        if dut is not None and _sio_contention_unresolved(dut):
            return None
        return _level(handle)

    return read


@dataclass(frozen=True)
class QpiViolation:
    """One recorded protocol failure, shared by device and bus-level monitors."""

    code: str
    source: str
    detail: str
    sim_time_ns: float
    phase: str = PHASE_IDLE
    txn_index: "int | None" = None
    classification: str = CLASS_FAIL

    def __str__(self) -> str:
        where = "" if self.txn_index is None else f" txn={self.txn_index}"
        tag = "" if self.classification == CLASS_FAIL else f" {self.classification}"
        return (
            f"{self.code}{tag} {self.source} t={self.sim_time_ns:.2f}ns "
            f"phase={self.phase}{where}: {self.detail}"
        )


class ViolationLog(list):
    """Ordered :class:`QpiViolation` log; raises on record when *strict*.

    Behaves as a plain list so several logs can be concatenated for reporting,
    and adds the query helpers a negative test or a bus monitor needs.
    """

    def __init__(self, *, strict: bool = False) -> None:
        super().__init__()
        self.strict = strict

    def record(self, code: str, *, source: str, detail: str, **fields) -> QpiViolation:
        violation = QpiViolation(
            code=code, source=source, detail=detail, sim_time_ns=_now_ns(), **fields
        )
        self.append(violation)
        if self.strict:
            raise AssertionError(str(violation))
        return violation

    def codes(self) -> "list[str]":
        return [violation.code for violation in self]

    def of(self, code: str) -> "list[QpiViolation]":
        return [violation for violation in self if violation.code == code]

    def has(self, code: str) -> bool:
        return any(violation.code == code for violation in self)

    def summary(self) -> str:
        return "; ".join(str(violation) for violation in self)


def format_violations(records) -> str:
    """Render any iterable of :class:`QpiViolation` as one diagnostic line."""
    return "; ".join(str(record) for record in records)


class PsramDevice:
    """Sparse byte-addressed memory for one APS6404L instance.

    Unwritten bytes read back as *fill*, or as a deterministic function of
    *seed* and address when a seed is given. Neither policy depends on Python
    dictionary iteration order.
    """

    def __init__(self, device_id: int, *, fill: int = 0x00, seed: "int | None" = None) -> None:
        self.device_id = device_id
        self.agent: "PsramQpiAgent | None" = None
        self.fill = fill & 0xFF
        self.seed = seed
        self._mem: "dict[int, int]" = {}

    def _unwritten(self, address: int) -> int:
        if self.seed is None:
            return self.fill
        mixed = (address * 0x9E3779B1 + self.seed * 0x85EBCA77) & 0xFFFFFFFF
        mixed ^= mixed >> 15
        mixed = (mixed * 0x2545F491) & 0xFFFFFFFF
        mixed ^= mixed >> 13
        return mixed & 0xFF

    def byte(self, address: int) -> int:
        """Return one stored byte at an in-range address (wire path)."""
        return self._mem.get(address, self._unwritten(address))

    def poke(self, address: int, value: int) -> None:
        """Store one byte at an in-range address (wire path)."""
        self._mem[address] = value & 0xFF

    def read(self, address: int, length: int) -> bytes:
        """Read *length* bytes from *address* (backdoor; masks to 23 bits)."""
        return bytes(self.byte((address + offset) & PSRAM_ADDR_MASK) for offset in range(length))

    def write(self, address: int, data: bytes) -> None:
        """Write *data* at *address* (backdoor; masks to 23 bits)."""
        for offset, value in enumerate(data):
            self.poke((address + offset) & PSRAM_ADDR_MASK, value)

    def snapshot(self) -> "dict[int, int]":
        """Return an independent copy of written bytes for scoreboard compare."""
        return dict(self._mem)


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
    page_crossings: int = 0
    ce_fall_ns: "float | None" = None
    ce_rise_ns: "float | None" = None
    faults: "list[str]" = field(default_factory=list)
    scratch: dict = field(default_factory=dict)
    complete: bool = False

    @property
    def byte_count(self) -> int:
        return len(self.read_bytes) + len(self.write_bytes)

    @property
    def ce_low_ns(self) -> "float | None":
        if self.ce_fall_ns is None or self.ce_rise_ns is None:
            return None
        return self.ce_rise_ns - self.ce_fall_ns


class QpiAccess:
    """Memory-access facade handed to a data handler for one transaction.

    Address policing lives here so no handler can silently wrap past
    ``0x7FFFFF``: an out-of-range wire access is skipped, not folded back to
    address zero. The matching ``Q-ADDR-RANGE`` record is raised by the parser
    for the byte the host actually transferred, so a read burst's speculative
    tail fetch past the top of memory does not fabricate a violation.
    """

    def __init__(self, agent: "PsramQpiAgent", txn: QpiTransaction, memory: PsramDevice) -> None:
        self.agent = agent
        self.txn = txn
        self.memory = memory

    @property
    def in_range(self) -> bool:
        return 0 <= self.txn.address <= PSRAM_ADDR_MASK

    def fetch_byte(self) -> int:
        """Return the byte at the current pointer, or fill when out of range."""
        if not self.in_range:
            return self.memory.fill
        return self.memory.byte(self.txn.address)

    def commit_byte(self, value: int) -> bool:
        """Store *value* at the current pointer; skip and report when out of range."""
        if not self.in_range:
            self.agent.report_range_fault(self.txn.address)
            return False
        self.memory.poke(self.txn.address, value)
        return True

    def advance(self) -> None:
        """Post-byte pointer increment; keeps overflow visible instead of wrapping."""
        self.txn.address += 1
        if self.txn.address % PSRAM_PAGE_SIZE == 0:
            self.txn.page_crossings += 1


class QpiDataHandler:
    """Data-phase behavior for one opcode.

    Subclass and override the method matching the command's direction. All
    per-transaction state belongs on the :class:`QpiTransaction` reached through
    the :class:`QpiAccess`, so one handler instance can serve both device
    models.
    """

    def on_data_rise(self, access: QpiAccess, nibble: int) -> None:
        """Consume one host-driven nibble sampled on rising SCK."""

    def on_data_fall(self, access: QpiAccess) -> "int | None":
        """Return the nibble to launch on this falling SCK, or ``None`` to float."""
        return None


class MemoryReadHandler(QpiDataHandler):
    """Byte-serial read-out with post-byte address increment (``0xEB``).

    The device keeps sourcing data until CE# rises, so the nibble launched on
    the last falling edge of a burst is fetched even though the host never
    clocks it in. ``PsramQpiAgent`` trims that unread tail from the log.
    """

    def on_data_fall(self, access):
        txn = access.txn
        if txn.pending_low is None:
            value = access.fetch_byte()
            txn.read_bytes.append(value)
            txn.pending_low = value & 0xF
            return (value >> 4) & 0xF
        nibble = txn.pending_low
        txn.pending_low = None
        access.advance()
        return nibble


class MemoryWriteHandler(QpiDataHandler):
    """Byte-serial write-in with post-byte address increment (``0x02``)."""

    def on_data_rise(self, access, nibble):
        txn = access.txn
        if txn.partial_nibble is None:
            txn.partial_nibble = nibble
            return
        value = ((txn.partial_nibble << 4) | nibble) & 0xFF
        txn.partial_nibble = None
        if access.commit_byte(value):
            txn.write_bytes.append(value)
        access.advance()


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


@dataclass(frozen=True)
class TerminationView:
    """Immutable parser state a termination rule may inspect."""

    txn: QpiTransaction
    command: "QpiCommand | None"
    phase: str

    @property
    def cmd_complete(self) -> bool:
        return self.txn.cmd_nibbles >= CMD_NIBBLES

    @property
    def addr_complete(self) -> bool:
        return self.command is not None and self.txn.addr_nibbles >= self.command.address_nibbles

    @property
    def dummy_complete(self) -> bool:
        return self.command is not None and self.txn.dummy_cycles >= self.command.dummy_cycles


@dataclass(frozen=True)
class TerminationRule:
    """One independent CE#-rising predicate.

    ``applies`` states the parser states the rule can judge, so a new rule is an
    added entry rather than an edit to an existing branch. Every applicable rule
    is evaluated; a truncated command does not hide a later condition by virtue
    of ordering alone.
    """

    code: str
    applies: Callable[[TerminationView], bool]
    violated: Callable[[TerminationView], bool]
    detail: Callable[[TerminationView], str]


TERMINATION_RULES: "tuple[TerminationRule, ...]" = (
    TerminationRule(
        code=Q_PHASE,
        applies=lambda view: True,
        violated=lambda view: not view.cmd_complete,
        detail=lambda view: (
            f"CE# rose after {view.txn.cmd_nibbles}/{CMD_NIBBLES} command nibbles"
        ),
    ),
    TerminationRule(
        code=Q_PHASE,
        applies=lambda view: view.cmd_complete and view.command is not None,
        violated=lambda view: not view.addr_complete,
        detail=lambda view: (
            f"{view.txn.name}: CE# rose after "
            f"{view.txn.addr_nibbles}/{view.command.address_nibbles} address nibbles"
        ),
    ),
    TerminationRule(
        code=Q_DUMMY,
        applies=lambda view: (
            view.command is not None and view.command.dummy_cycles > 0 and view.addr_complete
        ),
        violated=lambda view: view.txn.dummy_cycles != view.command.dummy_cycles,
        detail=lambda view: (
            f"{view.txn.name}: {view.txn.dummy_cycles} dummy cycles, "
            f"expected exactly {view.command.dummy_cycles}"
        ),
    ),
    TerminationRule(
        code=Q_NIBBLE_ODD,
        applies=lambda view: (
            view.command is not None and view.addr_complete and view.dummy_complete
        ),
        violated=lambda view: bool(view.txn.data_nibbles % 2),
        detail=lambda view: (
            f"{view.txn.name}: {view.txn.data_nibbles} data nibbles, "
            "no partial byte committed"
        ),
    ),
)


class PsramQpiAgent:
    """QPI slave BFM for one PSRAM instance, clocked by SCK and CE#.

    The agent wakes on resolved-bus SCK and CE# transitions only, samples
    inbound nibbles on rising SCK, and launches read-data nibbles on falling
    SCK.
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
        rules: "tuple[TerminationRule, ...]" = TERMINATION_RULES,
        strict: bool = False,
        violations: "ViolationLog | None" = None,
        thz_release_ns: float = 0.0,
    ) -> None:
        self._memory = memory
        self._sck = sck
        self._ce_n = ce_n
        self._read_nibble = read_nibble
        self._drive_handle = drive
        self._oe_handle = oe
        self._commands = dict(QPI_COMMANDS if commands is None else commands)
        self._rules = rules
        self._thz_release_ns = thz_release_ns
        self._source = f"PSRAM{memory.device_id}"

        self._phase = PHASE_IDLE
        self._command: "QpiCommand | None" = None
        self._txn: "QpiTransaction | None" = None
        self._access: "QpiAccess | None" = None
        self._driving = False
        self._driven_nibble = 0
        self._ce_rise_ns: "float | None" = None
        self._desel_drive_reported = False
        self._classification = CLASS_FAIL
        self._task = None

        self.transactions: "list[QpiTransaction]" = []
        self.violations = ViolationLog(strict=strict) if violations is None else violations

    # -- public state for bus-level monitors -------------------------------
    # Frozen OE/drive surface for SharedBusMonitor (and later ownership
    # negatives): ``sio_oe`` / ``sio_drive`` are the cocotb handles; ``oe`` /
    # ``selected`` are convenience bools and must not be watched for .value_change.

    @property
    def device_id(self) -> int:
        return self._memory.device_id

    @property
    def sio_oe(self):
        """Cocotb handle for the model's 4-bit SIO output enable (SIO[3:0])."""
        return self._oe_handle

    @property
    def sio_drive(self):
        """Cocotb handle for the model's 4-bit SIO drive value (SIO[3:0])."""
        return self._drive_handle

    @property
    def oe(self) -> bool:
        """True while the model itself drives SIO (convenience; not a handle)."""
        return self._driving

    @property
    def driven_nibble(self) -> int:
        """Last nibble the model placed on SIO (valid while :attr:`oe`)."""
        return self._driven_nibble

    @property
    def ce_n(self) -> "int | None":
        """Current CE# level, or ``None`` while unresolved."""
        return _level(self._ce_n)

    @property
    def selected(self) -> bool:
        """True while this instance's CE# is active low (convenience)."""
        return self.ce_n == 0

    @property
    def phase(self) -> str:
        return self._phase

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Park SIO released and launch the background BFM task."""
        self._drive_handle.value = 0
        self._oe_handle.value = 0
        self._task = cocotb.start_soon(self._run())
        return self._task

    def stop(self) -> None:
        """Cancel the background BFM task and release SIO."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._release_sio()

    def note_reset(self) -> None:
        """Abort the active transaction because system ``rst_n`` was asserted.

        Terminating records are classified ``RESET-TRUNCATED`` per
        ``04-timing-in-sim.md`` instead of ordinary fails. Preloaded memory is
        untouched: ``rst_n`` is an ASIC reset, not the APS6404L reset command.
        """
        self._release_sio()
        if self._txn is None:
            self._phase = PHASE_IDLE
            return
        self._classification = CLASS_RESET_TRUNCATED
        try:
            self._end_transaction()
        finally:
            self._classification = CLASS_FAIL

    # -- policing ----------------------------------------------------------

    def _violation(self, code: str, detail: str) -> None:
        if self._txn is not None:
            self._txn.faults.append(code)
        self.violations.record(
            code,
            source=self._source,
            detail=detail,
            phase=self._phase,
            txn_index=len(self.transactions),
            classification=self._classification,
        )

    def report_range_fault(self, address: int) -> None:
        """Record ``Q-ADDR-RANGE`` once for the active transaction."""
        txn = self._txn
        if txn is not None and Q_ADDR_RANGE in txn.faults:
            return
        self._violation(
            Q_ADDR_RANGE,
            f"byte address 0x{address:06X} is past 0x{PSRAM_ADDR_MASK:06X}; access skipped",
        )

    def _check_transferred_address(self) -> None:
        """``Q-ADDR-RANGE`` for the byte the host is actually transferring."""
        txn = self._txn
        byte_index = max(txn.data_nibbles - 1, 0) // 2
        address = txn.start_address + byte_index
        if address > PSRAM_ADDR_MASK:
            self.report_range_fault(address)

    def _check_drive_while_deselected(self) -> None:
        """``Q-DRIVE-DESEL``: only a selected instance may source SIO."""
        if not self._driving or self.selected:
            return
        if self._ce_rise_ns is not None and self._thz_release_ns > 0.0:
            if (_now_ns() - self._ce_rise_ns) <= self._thz_release_ns:
                return  # bounded tHZ release window, still holding the last value
        if self._desel_drive_reported:
            return  # one record per deselected interval; the first time is authoritative
        self._desel_drive_reported = True
        self._violation(
            Q_DRIVE_DESEL,
            f"model drove SIO=0x{self._driven_nibble:X} with CE# inactive",
        )

    # -- SIO drive ---------------------------------------------------------

    def _drive_nibble(self, nibble: int) -> None:
        self._driven_nibble = nibble & 0xF
        self._drive_handle.value = self._driven_nibble
        if not self._driving:
            self._oe_handle.value = 0xF
            self._driving = True

    def _release_sio(self) -> None:
        if self._driving:
            self._oe_handle.value = 0
            self._driving = False

    def inject_sio_drive(self, nibble: int) -> None:
        """Fault-injection hook: drive SIO regardless of parser phase.

        Negative tests use this to prove ``Q-DRIVE-DESEL`` can fire; the timing
        layer's future ``tHZ`` release also lands on this path. Not used by
        normal transaction decoding.
        """
        self._drive_nibble(nibble)
        self._check_drive_while_deselected()

    def inject_sio_release(self) -> None:
        """Release an injected drive without touching parser state."""
        self._release_sio()

    # -- transaction framing ----------------------------------------------

    def _begin_transaction(self) -> None:
        self._desel_drive_reported = False
        self._txn = QpiTransaction(device_id=self._memory.device_id, ce_fall_ns=_now_ns())
        self._access = QpiAccess(self, self._txn, self._memory)
        self._command = None
        self._phase = PHASE_CMD
        self._release_sio()

    def _end_transaction(self) -> None:
        self._release_sio()
        txn = self._txn
        if txn is None:
            self._phase = PHASE_IDLE
            return

        txn.ce_rise_ns = _now_ns()
        # data_nibbles counts rising SCK edges in the data phase, which is what
        # the host actually transferred. Read-out that the device sourced but
        # the host never clocked in is dropped from the log.
        del txn.read_bytes[txn.data_nibbles // 2 :]

        view = TerminationView(txn=txn, command=self._command, phase=self._phase)
        for rule in self._rules:
            if rule.applies(view) and rule.violated(view):
                self._violation(rule.code, rule.detail(view))

        txn.complete = not txn.faults
        self.transactions.append(txn)
        self._txn = None
        self._access = None
        self._command = None
        self._phase = PHASE_IDLE

    def _decode_opcode(self) -> None:
        txn = self._txn
        command = self._commands.get(txn.opcode)
        if command is None:
            self._violation(Q_OPCODE, f"unsupported opcode 0x{txn.opcode:02X}")
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
            self._violation(Q_SIO_X, f"unresolved SIO during {self._phase}")
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
                addr23 = bool(txn.address & ~PSRAM_ADDR_MASK)
                if addr23:
                    self._violation(Q_ADDR23, f"A[23] set in wire address 0x{txn.address:06X}")
                txn.address &= PSRAM_ADDR_MASK
                txn.start_address = txn.address
                if addr23:
                    # Fail before any memory access: device selection is CE#, not
                    # A[23], so the masked address is logged but never transferred.
                    self._phase = PHASE_IGNORE
                else:
                    self._enter_data_phase()
        elif self._phase == PHASE_DUMMY:
            txn.dummy_cycles += 1
            if txn.dummy_cycles == self._command.dummy_cycles:
                self._phase = PHASE_DATA
        elif self._phase == PHASE_DATA:
            # For a write this is the host nibble; for a read it acknowledges the
            # nibble the device launched on the preceding falling edge.
            txn.data_nibbles += 1
            self._check_transferred_address()
            self._command.handler.on_data_rise(self._access, nibble)

    def _on_sck_fall(self) -> None:
        if self._phase != PHASE_DATA or self._command.direction != DIR_READ:
            self._release_sio()
            return
        nibble = self._command.handler.on_data_fall(self._access)
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
                self._access = None
                prev_ce, prev_sck = ce, sck
                continue

            if prev_ce != 0 and ce == 0:
                self._begin_transaction()
            elif prev_ce == 0 and ce != 0:
                self._ce_rise_ns = _now_ns()
                self._end_transaction()

            if ce == 0 and prev_sck is not None:
                if sck == 1 and prev_sck == 0:
                    self._on_sck_rise()
                elif sck == 0 and prev_sck == 1:
                    self._on_sck_fall()

            self._check_drive_while_deselected()
            prev_ce, prev_sck = ce, sck


def _normalize_device_ids(devices) -> "tuple[int, ...]":
    """Return ordered unique device ids from an int or an iterable of ints."""
    if isinstance(devices, int):
        ids = (devices,)
    else:
        ids = tuple(devices)
    if not ids:
        raise ValueError("devices must list at least one of 0 or 1")
    if len(set(ids)) != len(ids):
        raise ValueError(f"devices has duplicates: {ids}")
    for device_id in ids:
        if device_id not in (0, 1):
            raise ValueError(f"device id must be 0 or 1, got {device_id}")
    return ids


def _attach_psram_agents(
    dut,
    device_ids: "tuple[int, ...]",
    *,
    read_nibble,
    strict: bool,
    fill: int,
    seed: "int | None",
    violations: "ViolationLog | None",
) -> "tuple[PsramDevice, ...]":
    """Build and start agents for *device_ids* on shared ``psram_*`` aliases."""
    attached = []
    for device_id in device_ids:
        memory = PsramDevice(device_id, fill=fill, seed=seed)
        memory.agent = PsramQpiAgent(
            memory,
            sck=dut.psram_sck,
            ce_n=getattr(dut, f"psram{device_id}_ce_n"),
            read_nibble=read_nibble,
            drive=getattr(dut, f"psram{device_id}_sio_drive"),
            oe=getattr(dut, f"psram{device_id}_sio_oe"),
            strict=strict,
            violations=violations,
        )
        memory.agent.start()
        attached.append(memory)
    return tuple(attached)


def attach_dual_psram(
    dut,
    *,
    strict: bool = False,
    fill: int = 0x00,
    seed: "int | None" = None,
    violations: "ViolationLog | None" = None,
) -> "tuple[PsramDevice, PsramDevice]":
    """Create both PSRAM memories on an L1/L2 ``uio`` wrapper and start agents.

    Each returned device exposes its agent as ``device.agent`` for transaction
    logs and recorded protocol violations. Pass *violations* to collect both
    instances, and a future bus-level monitor, into one ordered log.

    For ``tb_engine`` (L0), use :func:`attach_engine_psram` instead.
    """
    psram0, psram1 = _attach_psram_agents(
        dut,
        (0, 1),
        read_nibble=uio_nibble_reader(dut),
        strict=strict,
        fill=fill,
        seed=seed,
        violations=violations,
    )
    return psram0, psram1


def attach_engine_psram(
    dut,
    devices=(0, 1),
    *,
    strict: bool = False,
    fill: int = 0x00,
    seed: "int | None" = None,
    violations: "ViolationLog | None" = None,
) -> "tuple[PsramDevice, ...]":
    """Attach one or both PSRAM agents to ``tb_engine`` (L0).

    Uses the L0 aliases ``psram_sck``, ``psram{N}_ce_n``, ``psram{N}_sio_drive``,
    ``psram{N}_sio_oe``, and samples SIO through :func:`sio_nibble_reader` on
    ``resolved_sio``. *devices* may be ``0``, ``1``, or an iterable of those
    ids; the return value is an ordered tuple of attached :class:`PsramDevice`
    instances (each with ``.agent`` started).

    The engine always drives both CE# outputs. Attaching a single device still
    leaves the unselected CE# observable on the wrapper; idle after reset keeps
    both high. Pass the attached agents into
    :func:`monitors.qspi.start_shared_bus_monitor` for ownership checks.
    """
    device_ids = _normalize_device_ids(devices)
    return _attach_psram_agents(
        dut,
        device_ids,
        read_nibble=sio_nibble_reader(dut.resolved_sio, dut=dut),
        strict=strict,
        fill=fill,
        seed=seed,
        violations=violations,
    )
