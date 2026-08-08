"""Engine handshake and integrated-controller monitors.

Catalog: ``docs/llm/verification/06-checkers.md``. Per its implementation
separation ("engine request stability, pulse counts, pulse widths, and
controller bounds"), this module owns two always-on monitors.

:class:`HandshakeMonitor` - the whole ``CHK-HS-*`` group on the engine
request/stream ports:

======================== ==================================================
``CHK-*``                Condition enforced here
======================== ==================================================
``CHK-HS-TXN-START``     ``txn_valid`` is accepted only while ``busy=0``,
                         carries a nonzero ``byte_len``, and cannot be
                         accepted again before the prior transaction ends
``CHK-HS-REQ-STABLE``    ``{cmd, addr, device_sel, byte_len}`` equals the
                         accepted request through the last ``busy=1`` sample
``CHK-HS-RDATA-COUNT``   a completed ``0xEB`` read emits exactly
                         ``2 * byte_len`` ``rdata_valid`` pulses; a write
                         emits none; no pulse lands outside a transaction
``CHK-HS-WDATA-COUNT``   a completed ``0x02`` write emits exactly
                         ``2 * byte_len - 1`` ``wdata_next`` pulses; a read
                         emits none; no pulse lands after the final nibble or
                         outside an accepted transaction
``CHK-HS-WDATA-KNOWN``   ``wdata`` is a resolved nibble on write acceptance and
                         on the sample after every ``wdata_next``; the
                         presented sequence is retained for pin compare
``CHK-HS-PULSE-WIDTH``   ``txn_valid``, ``rdata_valid``, and ``wdata_next`` are
                         never high on two consecutive rising ``clk`` samples
``CHK-HS-OPCODE``        every accepted command is ``0xEB`` or ``0x02``, and
                         the pin decoder measured six QPI wait cycles for
                         ``0xEB`` and none for ``0x02``
======================== ==================================================

:class:`ControllerMonitor` - the integrated ``CHK-CTRL-*`` group (``na`` at L0,
required at L1):

======================== ==================================================
``CHK-*``                Condition enforced here
======================== ==================================================
``CHK-CTRL-REQ-GATE``    every ``qspi_txn_valid`` implies ``qspi_busy=0`` and
                         synchronized ``bus_req=0`` on that sampled cycle
``CHK-CTRL-REQ-SHAPE``   an accepted request is a length-11 fetch read, or a
                         payload read/write of ``1..DMA_BUF_DEPTH``
``CHK-CTRL-FETCH-HEAD``  the first pin transaction after each accepted START
                         is ``0xEB``, device 0, ``0x000000``, length 11
``CHK-CTRL-DATA-PAIR``   between descriptor fetches, every payload read is
                         followed by exactly one same-length payload write
``CHK-CTRL-STATE-VALID`` ``curr_state`` / ``next_state`` / ``stalled_state``
                         are resolved members of ``sys_control_state_t``, and
                         ``stalled_state`` is not overwritten while stalled
``CHK-CTRL-DATA-CNT``    ``data_cnt`` stays inside its per-phase bound and
                         satisfies every dynamic-index precondition
======================== ==================================================

``CHK-CTRL-FETCH-HEAD`` and ``CHK-CTRL-DATA-PAIR`` are top-observable rows: they
are judged from :class:`monitors.qspi.QspiPinMonitor` intervals, so this module
never re-decodes the bus and never reads a request field to establish a pin
fact. The same pin evidence supplies the six-wait-cycle half of
``CHK-HS-OPCODE`` (``06-checkers.md``: "measured by the pin protocol decoder").
Attach it with :meth:`HandshakeMonitor.attach_pin` /
:meth:`ControllerMonitor.attach_pin`; with no usable pin monitor those rows
report ``blocked`` with a reason, never a silent skip.

``CHK-HS-WDATA-KNOWN`` records the nibble presented on the sample *after* each
``wdata_next``. That rule holds for both consumers: the L0 BFM advances
``wdata`` in the timestep after the pulse (``ReadOnly`` then ``NextTimeStep``,
D21), and ``sys_controller`` presents the next nibble combinationally during the
pulse cycle itself. Either way the value settled at the following rising ``clk``
is the nibble the engine will launch, so :attr:`HsTransaction.wdata_nibbles` is
directly comparable with a pin-decoded write stream.

Reset handling follows ``06-checkers.md``: a transaction live at a rising ``clk``
sampled with ``rst_n=0`` is closed as aborted, its partial counts are retained
for diagnostics, no completed-transaction total is demanded, and the checker
re-arms on the first rising ``clk`` sampled with ``rst_n=1``. Findings raised
during that window are classified ``RESET-TRUNCATED`` and must be reviewed
explicitly (see :mod:`common.dispose`), never ignored.

Signal source by level (the ``start_*`` helpers resolve it):

* **L0** - ``tb_engine`` top-level stimulus/status handles (L0-port visibility);
  there is no ``sys_controller``, so every ``CHK-CTRL-*`` row is ``na``.
* **L1** - ``dut.qspi_engine`` instance ports and ``dut.sys_controller`` state
  inside ``tt_um_lahnb_sgdma`` (RTL-hierarchy-only visibility).
* **L2** - no source hierarchy, so every row here is ``na``.
"""

from dataclasses import dataclass, field

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import ReadOnly, RisingEdge

from common.config import parse_run_config
from monitors.qspi import (
    DIR_READ as PIN_DIR_READ,
    DIR_UNKNOWN as PIN_DIR_UNKNOWN,
    DIR_WRITE as PIN_DIR_WRITE,
    FAULT_ADDR23,
    FAULT_ADDR_TRUNCATED,
    FAULT_CMD_TRUNCATED,
    FAULT_REFRAME,
    FAULT_RESET,
    READ_DUMMY_CYCLES,
)

CHK_HS_TXN_START = "CHK-HS-TXN-START"
CHK_HS_REQ_STABLE = "CHK-HS-REQ-STABLE"
CHK_HS_RDATA_COUNT = "CHK-HS-RDATA-COUNT"
CHK_HS_WDATA_COUNT = "CHK-HS-WDATA-COUNT"
CHK_HS_PULSE_WIDTH = "CHK-HS-PULSE-WIDTH"
CHK_HS_WDATA_KNOWN = "CHK-HS-WDATA-KNOWN"
CHK_HS_OPCODE = "CHK-HS-OPCODE"

CHK_CTRL_REQ_GATE = "CHK-CTRL-REQ-GATE"
CHK_CTRL_REQ_SHAPE = "CHK-CTRL-REQ-SHAPE"
CHK_CTRL_FETCH_HEAD = "CHK-CTRL-FETCH-HEAD"
CHK_CTRL_DATA_PAIR = "CHK-CTRL-DATA-PAIR"
CHK_CTRL_STATE_VALID = "CHK-CTRL-STATE-VALID"
CHK_CTRL_DATA_CNT = "CHK-CTRL-DATA-CNT"

HANDSHAKE_CHECK_IDS = (
    CHK_HS_TXN_START,
    CHK_HS_REQ_STABLE,
    CHK_HS_RDATA_COUNT,
    CHK_HS_WDATA_COUNT,
    CHK_HS_PULSE_WIDTH,
    CHK_HS_WDATA_KNOWN,
    CHK_HS_OPCODE,
)

CONTROLLER_CHECK_IDS = (
    CHK_CTRL_REQ_GATE,
    CHK_CTRL_REQ_SHAPE,
    CHK_CTRL_FETCH_HEAD,
    CHK_CTRL_DATA_PAIR,
    CHK_CTRL_STATE_VALID,
    CHK_CTRL_DATA_CNT,
)

# Rows that need pin-decoded evidence in addition to (or instead of) the clocked
# ports. Without a usable QspiPinMonitor they report `blocked`.
PIN_EVIDENCE_CHECK_IDS = (CHK_HS_OPCODE, CHK_CTRL_FETCH_HEAD, CHK_CTRL_DATA_PAIR)

NO_PIN_EVIDENCE_REASON = (
    "no usable QspiPinMonitor attached; this row's pin half is measured by the "
    "pin protocol decoder (06-checkers.md)"
)

QSPI_CMD_FAST_READ = 0xEB
QSPI_CMD_WRITE = 0x02

# sys_control_pkg::sys_control_state_t (src/rtl/types.svh). All eight encodings
# of the 3-bit enum are members, so CHK-CTRL-STATE-VALID reduces to resolution
# plus membership.
SYS_CONTROL_IDLE = 0
SYS_CONTROL_NEW_FETCH = 1
SYS_CONTROL_FETCH = 2
SYS_CONTROL_NEW_OP = 3
SYS_CONTROL_READ = 4
SYS_CONTROL_WRITE = 5
SYS_CONTROL_UPDATE = 6
SYS_CONTROL_STALL = 7

SYS_CONTROL_STATES = {
    SYS_CONTROL_IDLE: "SYS_CTRL_IDLE",
    SYS_CONTROL_NEW_FETCH: "NEW_FETCH",
    SYS_CONTROL_FETCH: "FETCH",
    SYS_CONTROL_NEW_OP: "NEW_OP",
    SYS_CONTROL_READ: "READ",
    SYS_CONTROL_WRITE: "WRITE",
    SYS_CONTROL_UPDATE: "UPDATE",
    SYS_CONTROL_STALL: "STALL",
}

# qspi_pkg::qspi_state_t (src/rtl/types.svh); 4-bit encoding, 10 members.
QSPI_ENGINE_STATES = {
    0: "QSPI_IDLE",
    1: "CS_ON",
    2: "SEND_CMD_1",
    3: "SEND_CMD_2",
    4: "SEND_ADDR",
    5: "WAIT",
    6: "READ_DATA",
    7: "WRITE_DATA",
    8: "SCLK_OFF",
    9: "CS_OFF",
}

FETCH_STATES = (SYS_CONTROL_NEW_FETCH, SYS_CONTROL_FETCH)
PAYLOAD_STATES = (
    SYS_CONTROL_NEW_OP,
    SYS_CONTROL_READ,
    SYS_CONTROL_WRITE,
    SYS_CONTROL_UPDATE,
)

# qspi_pkg::QPI_TCD_BYTES and sys_control_pkg::TCD_LEN.
TCD_BYTES = 11
TCD_NIBBLES = 2 * TCD_BYTES

DIR_READ = "read"
DIR_WRITE = "write"
DIR_UNKNOWN = "unknown"

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_NA = "na"
RESULT_BLOCKED = "blocked"

# Handles HandshakeMonitor needs; a missing name blocks every row it owns.
REQUIRED_SIGNALS = (
    "clk",
    "rst_n",
    "txn_valid",
    "cmd",
    "addr",
    "device_sel",
    "byte_len",
    "busy",
    "wdata",
    "wdata_next",
    "rdata_valid",
)

# Handles ControllerMonitor needs for its clocked rows.
CONTROLLER_REQUIRED_SIGNALS = (
    "clk",
    "rst_n",
    "txn_valid",
    "busy",
    "bus_req",
    "cmd",
    "byte_len",
    "rdata_valid",
    "wdata_next",
    "curr_state",
    "next_state",
    "stalled_state",
    "data_cnt",
)

# Pin intervals that never carry a judgement: the CE# frame was torn down by
# reset or by a second CE# fall, so no phase count is meaningful.
_ABORT_FAULTS = (FAULT_RESET, FAULT_REFRAME)


def _resolved(handle) -> "int | None":
    """Return the integer value of *handle*, or ``None`` while any bit is x/z."""
    if handle is None:
        return None
    text = str(handle.value).strip().lower()
    if not text or any(char not in "01" for char in text):
        return None
    return int(text, 2)


def _show(value: "int | None") -> str:
    return "x/z" if value is None else str(value)


def _show_op(value: "int | None") -> str:
    return "x/z" if value is None else f"0x{value:02X}"


def _show_addr(value: "int | None") -> str:
    return "x/z" if value is None else f"0x{value:06X}"


def _state_text(value: "int | None", symbols: "dict[int, str]") -> str:
    """Render an enum sample symbolically and rawly (``06-checkers.md``)."""
    if value is None:
        return "x/z"
    name = symbols.get(value)
    return f"{name}(0x{value:X})" if name else f"<not-in-enum>(0x{value:X})"


def _now_ns() -> float:
    return float(get_sim_time(unit="ns"))


def _aborted(interval) -> bool:
    return any(fault in interval.faults for fault in _ABORT_FAULTS)


class _PinCursor:
    """Cursor over a :class:`monitors.qspi.QspiPinMonitor` interval log.

    The pin monitor owns decoding; a checker only walks the intervals it has not
    judged yet. A shorter log than the cursor means the pin monitor was cleared
    for a new window, so the cursor restarts.
    """

    def __init__(self) -> None:
        self._index = 0

    def reset(self) -> None:
        self._index = 0

    def take(self, pin) -> tuple:
        if pin is None or pin.blocked:
            return ()
        intervals = pin.intervals
        if len(intervals) < self._index:
            self._index = 0
        fresh = tuple(intervals[self._index :])
        self._index = len(intervals)
        return fresh


@dataclass(frozen=True)
class HsViolation:
    """One handshake finding, timestamped at the sampled rising ``clk`` edge."""

    check_id: str
    time_ns: float
    cycle: int
    detail: str
    reset_truncated: bool = False

    def __str__(self) -> str:
        prefix = "RESET-TRUNCATED " if self.reset_truncated else ""
        return (
            f"{prefix}{self.check_id} at {self.time_ns:.3f}ns "
            f"cycle={self.cycle}: {self.detail}"
        )


@dataclass
class HsTransaction:
    """One accepted engine request as seen on the request/stream ports."""

    opcode: "int | None"
    byte_len: "int | None"
    direction: str
    start_ns: float
    address: "int | None" = None
    device_sel: "int | None" = None
    end_ns: "float | None" = None
    wdata_next_count: int = 0
    rdata_valid_count: int = 0
    wdata_nibbles: "list[int | None]" = field(default_factory=list)
    aborted: bool = False
    complete: bool = False

    @property
    def expected_wdata_next(self) -> "int | None":
        if self.direction != DIR_WRITE or self.byte_len is None:
            return 0 if self.direction == DIR_READ else None
        return (2 * self.byte_len) - 1

    @property
    def expected_rdata_valid(self) -> "int | None":
        if self.direction == DIR_WRITE:
            return 0
        if self.direction != DIR_READ or self.byte_len is None:
            return None
        return 2 * self.byte_len

    def request(self) -> "dict[str, int | None]":
        """Return the accepted request fields ``CHK-HS-REQ-STABLE`` holds."""
        return {
            "cmd": self.opcode,
            "addr": self.address,
            "device_sel": self.device_sel,
            "byte_len": self.byte_len,
        }

    def __str__(self) -> str:
        return (
            f"op={_show_op(self.opcode)} len={_show(self.byte_len)} "
            f"dev={_show(self.device_sel)} addr={_show_addr(self.address)} "
            f"dir={self.direction} wdata_next={self.wdata_next_count} "
            f"rdata_valid={self.rdata_valid_count} nibbles={len(self.wdata_nibbles)}"
            f"{' aborted' if self.aborted else ''}"
        )


class HandshakeMonitor:
    """Always-on ``CHK-HS-*`` checker on the engine request/stream ports.

    Samples once per rising ``clk`` in the read-only phase so sequential
    assignments have settled, exactly as ``06-checkers.md`` requires. Findings
    are recorded, and additionally raise when built with ``strict=True``.
    """

    def __init__(
        self,
        *,
        clk=None,
        rst_n=None,
        txn_valid=None,
        cmd=None,
        addr=None,
        device_sel=None,
        byte_len=None,
        busy=None,
        wdata=None,
        wdata_next=None,
        rdata_valid=None,
        pin=None,
        level: str = "L0",
        name: str = "handshake",
        visibility: str = "L0-port",
        scope: str = "",
        strict: bool = False,
        max_events: int = 64,
        missing: "tuple[str, ...]" = (),
        blocked_reason: str = "",
        log=None,
    ) -> None:
        self._clk = clk
        self._rst_n = rst_n
        self._txn_valid = txn_valid
        self._cmd = cmd
        self._addr = addr
        self._device_sel = device_sel
        self._byte_len = byte_len
        self._busy = busy
        self._wdata = wdata
        self._wdata_next = wdata_next
        self._rdata_valid = rdata_valid
        self._pin = pin
        self._strict = strict
        self._max_events = max_events
        self._log = log

        self.level = level
        self.name = name
        self.visibility = visibility
        self.scope = scope
        self.missing = tuple(missing)
        self.blocked_reason = blocked_reason or (
            f"missing handles: {', '.join(self.missing)}" if self.missing else ""
        )

        self.violations: "list[str]" = []
        self.events: "list[HsViolation]" = []
        self.reset_truncated: "list[HsViolation]" = []
        self.transactions: "list[HsTransaction]" = []

        self._txn: "HsTransaction | None" = None
        self._saw_busy = False
        self._pending_wdata = False
        self._unstable: "set[str]" = set()
        self._prev = {"txn_valid": 0, "rdata_valid": 0, "wdata_next": 0}
        self._pins = _PinCursor()
        self._in_reset = True
        self._cycle = 0
        self._samples = 0
        self._suppressed = 0
        self._active = False
        self._task = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def blocked(self) -> bool:
        """True when a required handle was absent, so no row could be judged."""
        return bool(self.missing)

    def attach_pin(self, pin) -> None:
        """Supply the pin monitor that measures this monitor's pin half.

        Additive and idempotent: bring-up calls it once the pin decoder exists,
        and ``CHK-HS-OPCODE`` stops reporting ``blocked``.
        """
        self._pin = pin
        self._pins.reset()

    def start(self):
        """Launch the background checker. Call before reset release."""
        if self.blocked:
            if self._log is not None:
                self._log.warning(
                    "CHECKER BLOCKED ids=%s level=%s reason=%s",
                    ",".join(HANDSHAKE_CHECK_IDS),
                    self.level,
                    self.blocked_reason,
                )
            return None
        self._active = True
        self._task = cocotb.start_soon(self._run())
        return self._task

    def stop(self) -> None:
        """Soft-stop so a later test in the same module can re-attach."""
        self._active = False

    def clear(self) -> None:
        """Drop findings and transaction history for a fresh directed window."""
        self.events.clear()
        self.violations.clear()
        self.reset_truncated.clear()
        self.transactions.clear()
        self._txn = None
        self._saw_busy = False
        self._pending_wdata = False
        self._unstable.clear()
        self._prev = {"txn_valid": 0, "rdata_valid": 0, "wdata_next": 0}
        self._pins.reset()
        self._suppressed = 0

    async def _run(self) -> None:
        while True:
            await RisingEdge(self._clk)
            await ReadOnly()
            if self._active:
                self._sample()

    # -- reporting ---------------------------------------------------------

    def _report(self, check_id: str, detail: str, *, in_reset: bool = False) -> None:
        event = HsViolation(
            check_id=check_id,
            time_ns=_now_ns(),
            cycle=self._cycle,
            detail=detail,
            reset_truncated=in_reset,
        )
        if in_reset:
            self.reset_truncated.append(event)
            return

        if len(self.events) >= self._max_events:
            self._suppressed += 1
            return

        self.events.append(event)
        self.violations.append(f"{self.name} {event}")
        if self._log is not None:
            self._log.error(
                "CHECKER FAIL id=%s level=%s visibility=%s %s",
                check_id,
                self.level,
                self.visibility,
                event,
            )
        if self._strict:
            raise AssertionError(str(event))

    # -- sampling ----------------------------------------------------------

    def _sample(self) -> None:
        self._samples += 1
        rst_n = _resolved(self._rst_n)

        if rst_n != 1:
            self._on_reset_sample()
            return

        if self._in_reset:
            # Re-arm on the first rising clk sampled with rst_n=1.
            self._in_reset = False
            self._prev = {"txn_valid": 0, "rdata_valid": 0, "wdata_next": 0}

        self._cycle += 1

        txn_valid = _resolved(self._txn_valid)
        busy = _resolved(self._busy)
        wdata_next = _resolved(self._wdata_next)
        rdata_valid = _resolved(self._rdata_valid)

        self._check_pulse_width(txn_valid, rdata_valid, wdata_next)
        self._capture_pending_wdata()
        self._check_req_stable(busy)
        # Close first: the controller may present the next request on the very
        # sample the previous transaction's busy falls, and a stream pulse never
        # coincides with busy=0, so closing early cannot lose a count.
        self._check_completion(busy)
        self._check_acceptance(txn_valid, busy)
        self._check_wdata_next(wdata_next, busy)
        self._check_rdata_valid(rdata_valid, busy)
        self._check_pin_intervals()

        self._prev = {
            "txn_valid": 0 if txn_valid is None else txn_valid,
            "rdata_valid": 0 if rdata_valid is None else rdata_valid,
            "wdata_next": 0 if wdata_next is None else wdata_next,
        }

    def _on_reset_sample(self) -> None:
        """Close an in-flight transaction as aborted; do not demand its totals."""
        self._in_reset = True
        if self._txn is not None:
            self._txn.aborted = True
            self._txn.end_ns = _now_ns()
            self.transactions.append(self._txn)
            self._txn = None
        self._saw_busy = False
        self._pending_wdata = False
        self._unstable.clear()
        self._prev = {"txn_valid": 0, "rdata_valid": 0, "wdata_next": 0}

    def _check_pulse_width(self, txn_valid, rdata_valid, wdata_next) -> None:
        """``CHK-HS-PULSE-WIDTH``: no pulse high on two consecutive samples."""
        for label, value in (
            ("txn_valid", txn_valid),
            ("rdata_valid", rdata_valid),
            ("wdata_next", wdata_next),
        ):
            if value is None:
                self._report(
                    CHK_HS_PULSE_WIDTH,
                    f"{label} sampled unresolved (x/z) while rst_n=1; "
                    "a pulse signal must hold a resolved value every sample",
                )
                continue
            if value == 1 and self._prev[label] == 1:
                self._report(
                    CHK_HS_PULSE_WIDTH,
                    f"{label} high on two consecutive rising clk samples",
                )

    def _capture_pending_wdata(self) -> None:
        """``CHK-HS-WDATA-KNOWN``: record the nibble presented after a request."""
        if not self._pending_wdata:
            return
        self._pending_wdata = False
        value = _resolved(self._wdata)
        if self._txn is not None:
            self._txn.wdata_nibbles.append(value)
        if value is None:
            self._report(
                CHK_HS_WDATA_KNOWN,
                "wdata unresolved (x/z) on the sample after wdata_next; D21 "
                "requires the next nibble before the following clk",
            )

    def _check_req_stable(self, busy) -> None:
        """``CHK-HS-REQ-STABLE``: held request fields through the busy window.

        Judged only on samples with ``busy=1``: the request must hold from
        acceptance through the last such sample. One finding per field per
        transaction keeps a wandering field from flooding the log.
        """
        txn = self._txn
        if txn is None or busy != 1:
            return
        observed = {
            "cmd": _resolved(self._cmd),
            "addr": _resolved(self._addr),
            "device_sel": _resolved(self._device_sel),
            "byte_len": _resolved(self._byte_len),
        }
        accepted = txn.request()
        for field_name, value in observed.items():
            if value == accepted[field_name] or field_name in self._unstable:
                continue
            self._unstable.add(field_name)
            shown = _show_addr if field_name == "addr" else _show
            if field_name == "cmd":
                shown = _show_op
            self._report(
                CHK_HS_REQ_STABLE,
                f"{field_name} changed during the accepted transaction: "
                f"accepted={shown(accepted[field_name])} "
                f"observed={shown(value)} ({txn})",
            )

    def _check_acceptance(self, txn_valid, busy) -> None:
        """Latch the accepted request and judge the acceptance-time rows."""
        if txn_valid != 1:
            return

        if busy != 0:
            # CHK-HS-TXN-START owns the qualifier: an accepted request may only
            # be issued while the engine is idle. Do not open a transaction.
            self._report(
                CHK_HS_TXN_START,
                f"txn_valid=1 sampled with busy={_show(busy)}; a request is only "
                "accepted while the engine is idle",
            )
            return

        opcode = _resolved(self._cmd)
        byte_len = _resolved(self._byte_len)
        if opcode == QSPI_CMD_WRITE:
            direction = DIR_WRITE
        elif opcode == QSPI_CMD_FAST_READ:
            direction = DIR_READ
        else:
            direction = DIR_UNKNOWN

        if direction == DIR_UNKNOWN:
            self._report(
                CHK_HS_OPCODE,
                f"accepted cmd={_show_op(opcode)}; only 0xEB (fast read) and "
                "0x02 (write) are V1 opcodes",
            )

        if byte_len is None or byte_len == 0:
            self._report(
                CHK_HS_TXN_START,
                f"accepted byte_len={_show(byte_len)}; an accepted request must "
                "carry a nonzero length",
            )

        if self._txn is not None:
            # A second acceptance before the prior transaction ended.
            self._report(
                CHK_HS_TXN_START,
                f"second acceptance before the prior transaction ended ({self._txn})",
            )
            self._txn.aborted = True
            self._txn.end_ns = _now_ns()
            self.transactions.append(self._txn)

        self._txn = HsTransaction(
            opcode=opcode,
            byte_len=byte_len,
            direction=direction,
            start_ns=_now_ns(),
            address=_resolved(self._addr),
            device_sel=_resolved(self._device_sel),
        )
        self._saw_busy = False
        self._unstable.clear()

        if direction == DIR_WRITE:
            first = _resolved(self._wdata)
            self._txn.wdata_nibbles.append(first)
            if first is None:
                self._report(
                    CHK_HS_WDATA_KNOWN,
                    "wdata unresolved (x/z) on write acceptance; the first "
                    "nibble must ride with txn_valid",
                )

    def _check_wdata_next(self, wdata_next, busy) -> None:
        """``CHK-HS-WDATA-COUNT`` pulse placement, plus nibble-capture arming."""
        if wdata_next != 1:
            return

        txn = self._txn
        if txn is None:
            self._report(
                CHK_HS_WDATA_COUNT,
                f"wdata_next pulse outside an accepted transaction (busy={_show(busy)})",
            )
            return

        if txn.direction == DIR_READ:
            self._report(
                CHK_HS_WDATA_COUNT,
                f"wdata_next pulse during a 0xEB read (len={_show(txn.byte_len)}); "
                "a read must emit zero",
            )
            return

        txn.wdata_next_count += 1
        expected = txn.expected_wdata_next
        if expected is not None and txn.wdata_next_count > expected:
            self._report(
                CHK_HS_WDATA_COUNT,
                f"wdata_next pulse {txn.wdata_next_count} past the final nibble "
                f"(len={_show(txn.byte_len)} expected {expected})",
            )
            return

        self._pending_wdata = True

    def _check_rdata_valid(self, rdata_valid, busy) -> None:
        """``CHK-HS-RDATA-COUNT`` pulse placement, plus per-transaction counting."""
        if rdata_valid != 1:
            return

        txn = self._txn
        if txn is None:
            self._report(
                CHK_HS_RDATA_COUNT,
                f"rdata_valid pulse outside an accepted transaction (busy={_show(busy)})",
            )
            return

        txn.rdata_valid_count += 1
        if txn.direction == DIR_WRITE:
            self._report(
                CHK_HS_RDATA_COUNT,
                f"rdata_valid pulse during a 0x02 write (len={_show(txn.byte_len)}); "
                "a write must emit zero",
            )
            return

        expected = txn.expected_rdata_valid
        if expected is not None and txn.rdata_valid_count > expected:
            self._report(
                CHK_HS_RDATA_COUNT,
                f"rdata_valid pulse {txn.rdata_valid_count} past the final nibble "
                f"(len={_show(txn.byte_len)} expected {expected})",
            )

    def _check_completion(self, busy) -> None:
        """Compare counts only when the transaction ends normally."""
        txn = self._txn
        if txn is None:
            return

        if busy == 1:
            self._saw_busy = True
            return
        if not self._saw_busy:
            return  # acceptance cycle and the gap before busy rises

        txn.end_ns = _now_ns()
        expected_wdata = txn.expected_wdata_next
        expected_rdata = txn.expected_rdata_valid
        opcode = _show_op(txn.opcode)
        if expected_wdata is not None and txn.wdata_next_count != expected_wdata:
            self._report(
                CHK_HS_WDATA_COUNT,
                f"op={opcode} len={_show(txn.byte_len)} "
                f"expected_wdata_next={expected_wdata} "
                f"observed_wdata_next={txn.wdata_next_count} transaction_end=normal",
            )
        elif txn.direction == DIR_WRITE and txn.byte_len is not None:
            presented = len(txn.wdata_nibbles)
            if presented != 2 * txn.byte_len:
                self._report(
                    CHK_HS_WDATA_KNOWN,
                    f"presented {presented} wdata nibbles for a {txn.byte_len}-byte "
                    f"write; expected {2 * txn.byte_len}",
                )

        if expected_rdata is not None and txn.rdata_valid_count != expected_rdata:
            self._report(
                CHK_HS_RDATA_COUNT,
                f"op={opcode} len={_show(txn.byte_len)} "
                f"expected_rdata_valid={expected_rdata} "
                f"observed_rdata_valid={txn.rdata_valid_count} transaction_end=normal",
            )

        txn.complete = not txn.aborted
        self.transactions.append(txn)
        self._txn = None
        self._saw_busy = False
        self._pending_wdata = False
        self._unstable.clear()

    def _check_pin_intervals(self) -> None:
        """Check QPI wait/dummy cycles on each completed pin-decoded interval.

        Walks intervals drained from the pin decoder and reports
        ``CHK-HS-OPCODE`` when a finished 0xEB read did not use
        ``READ_DUMMY_CYCLES`` wait cycles, or when a finished 0x02 write used
        any. Aborted intervals are skipped. Reads whose command or address
        phase never completed (truncated cmd/addr, ADDR23) are skipped too:
        their wait count is not meaningful, and those faults are owned
        elsewhere.
        """
        for interval in self._pins.take(self._pin):
            if _aborted(interval):
                continue
            if interval.direction == PIN_DIR_READ:
                if (
                    FAULT_CMD_TRUNCATED in interval.faults
                    or FAULT_ADDR_TRUNCATED in interval.faults
                    or FAULT_ADDR23 in interval.faults
                ):
                    continue
                if interval.dummy_cycles != READ_DUMMY_CYCLES:
                    self._report(
                        CHK_HS_OPCODE,
                        f"pin-decoded 0xEB read used {interval.dummy_cycles} QPI "
                        f"wait cycles, expected {READ_DUMMY_CYCLES} "
                        f"({interval.canonical()})",
                    )
            elif interval.direction == PIN_DIR_WRITE and interval.dummy_cycles:
                self._report(
                    CHK_HS_OPCODE,
                    f"pin-decoded 0x02 write used {interval.dummy_cycles} QPI "
                    f"wait cycles, expected none ({interval.canonical()})",
                )

    # -- results -----------------------------------------------------------

    def _pin_evidence(self) -> bool:
        return self._pin is not None and not self._pin.blocked

    def counts(self) -> "dict[str, int]":
        """Return the ordinary violation count for every implemented ID."""
        counts = {check_id: 0 for check_id in HANDSHAKE_CHECK_IDS}
        for event in self.events:
            counts[event.check_id] += 1
        return counts

    def results(self) -> "dict[str, str]":
        """Return per-ID ``pass`` / ``fail`` / ``blocked`` disposition."""
        if self.blocked:
            return {check_id: RESULT_BLOCKED for check_id in HANDSHAKE_CHECK_IDS}
        counts = self.counts()
        reasons = self.blocked_reasons()
        dispositions: "dict[str, str]" = {}
        for check_id in HANDSHAKE_CHECK_IDS:
            if counts[check_id]:
                dispositions[check_id] = RESULT_FAIL
            elif check_id in reasons:
                dispositions[check_id] = RESULT_BLOCKED
            else:
                dispositions[check_id] = RESULT_PASS
        return dispositions

    def blocked_reasons(self) -> "dict[str, str]":
        """Return the reason string behind every ``blocked`` row."""
        if self.blocked:
            return {check_id: self.blocked_reason for check_id in HANDSHAKE_CHECK_IDS}
        if not self._pin_evidence():
            return {CHK_HS_OPCODE: NO_PIN_EVIDENCE_REASON}
        return {}

    def violations_for(self, check_id: str) -> "list[HsViolation]":
        """Return recorded events for one catalog ID (negative-test helper)."""
        return [event for event in self.events if event.check_id == check_id]

    def review_reset_truncated(self) -> "list[HsViolation]":
        """Return ``RESET-TRUNCATED`` findings for explicit test dispose."""
        return list(self.reset_truncated)

    def write_nibbles(self) -> "list[list[int | None]]":
        """Return the presented write-nibble stream of each accepted write."""
        return [
            list(txn.wdata_nibbles)
            for txn in self.transactions
            if txn.direction == DIR_WRITE
        ]

    def summary(self) -> str:
        parts = [
            f"{check_id}={result}" for check_id, result in self.results().items()
        ]
        if self.reset_truncated:
            parts.append(f"reset_truncated={len(self.reset_truncated)}")
        if self._suppressed:
            parts.append(f"suppressed={self._suppressed}")
        scope = f", scope={self.scope}" if self.scope else ""
        return (
            f"{self.name} ({self.level}, {self.visibility}{scope}, "
            f"{self._samples} samples, {len(self.transactions)} txn): "
            + " ".join(parts)
        )


# -- attachment ------------------------------------------------------------


def _optional(obj, name):
    if obj is None:
        return None
    try:
        return getattr(obj, name)
    except AttributeError:
        return None


def _engine_scope(dut) -> "tuple[object | None, str]":
    """Return the handle scope carrying the engine ports, plus its path name.

    L0 exposes the ports on ``tb_engine`` itself. L1 reads them from the
    ``qspi_engine`` instance inside ``tt_um_lahnb_sgdma`` (RTL-hierarchy-only
    visibility per ``06-checkers.md``). L2 has no such hierarchy.
    """
    if _optional(dut, "txn_valid") is not None:
        return dut, "tb"

    inner = _optional(dut, "dut")
    if inner is not None:
        engine = _optional(inner, "qspi_engine")
        if engine is not None and _optional(engine, "txn_valid") is not None:
            return engine, "dut.qspi_engine"
    return None, ""


def _controller_scope(dut) -> "tuple[object | None, object | None, str]":
    """Return ``(top, sys_controller, path)`` for the integrated controller."""
    inner = _optional(dut, "dut")
    if inner is None:
        return None, None, ""
    controller = _optional(inner, "sys_controller")
    if controller is None:
        return inner, None, ""
    return inner, controller, "dut.sys_controller"


def start_handshake_monitor(
    dut,
    *,
    strict: bool = False,
    level: "str | None" = None,
    name: str = "handshake",
    pin=None,
    log=None,
    **kwargs,
) -> HandshakeMonitor:
    """Create and start the ``CHK-HS-*`` monitor for *dut*.

    Resolves the engine request/stream ports at the DUT level in use and starts
    sampling immediately, so callers must invoke this before reset release
    (:func:`common.bringup.bring_up_engine` / ``bring_up_top`` already do).
    Pass *pin* (or call :meth:`HandshakeMonitor.attach_pin` afterwards) so the
    wait-cycle half of ``CHK-HS-OPCODE`` has pin evidence.

    Returns the monitor even when its signals are unavailable; the returned
    object then reports every row ``blocked`` with a reason instead of vanishing
    from the run's disposition table.
    """
    log = dut._log if log is None else log
    scope, scope_name = _engine_scope(dut)
    clk = _optional(dut, "clk")
    rst_n = _optional(dut, "rst_n")

    if scope is None:
        return HandshakeMonitor(
            level=level or "L2",
            name=name,
            visibility="unavailable",
            strict=strict,
            pin=pin,
            missing=("qspi_engine hierarchy",),
            blocked_reason=(
                "no qspi_engine request/stream hierarchy under this DUT level"
            ),
            log=log,
            **kwargs,
        )

    handles = {
        "clk": clk,
        "rst_n": rst_n,
        "txn_valid": _optional(scope, "txn_valid"),
        "cmd": _optional(scope, "cmd"),
        "addr": _optional(scope, "addr"),
        "device_sel": _optional(scope, "device_sel"),
        "byte_len": _optional(scope, "byte_len"),
        "busy": _optional(scope, "busy"),
        "wdata": _optional(scope, "wdata"),
        "wdata_next": _optional(scope, "wdata_next"),
        "rdata_valid": _optional(scope, "rdata_valid"),
    }
    missing = tuple(name_ for name_ in REQUIRED_SIGNALS if handles[name_] is None)

    if level is None:
        level = "L0" if scope_name == "tb" else "L1"

    monitor = HandshakeMonitor(
        level=level,
        name=name,
        visibility="L0-port" if level == "L0" else "RTL-hierarchy-only",
        scope=scope_name,
        strict=strict,
        pin=pin,
        missing=missing,
        log=log,
        **handles,
        **kwargs,
    )
    monitor.start()
    return monitor


# -- integrated controller (CHK-CTRL-*) ------------------------------------


class ControllerMonitor:
    """Always-on ``CHK-CTRL-*`` checker on the integrated controller.

    Four rows are clocked hierarchy checks on ``sys_controller`` plus the
    ``qspi_*`` request nets in ``tt_um_lahnb_sgdma``; two are top-observable pin
    sequence checks fed by :class:`monitors.qspi.QspiPinMonitor`.

    Sampling matches :class:`HandshakeMonitor`: read-only after each rising
    ``clk``. That is the delta the controller's own ``always_ff`` blocks consume
    at the next edge, so a sampled ``curr_state`` / ``data_cnt`` /
    ``qspi_rdata_valid`` triple is exactly the dynamic-index precondition the
    RTL is about to apply.
    """

    def __init__(
        self,
        *,
        clk=None,
        rst_n=None,
        txn_valid=None,
        busy=None,
        bus_req=None,
        cmd=None,
        byte_len=None,
        rdata_valid=None,
        wdata_next=None,
        curr_state=None,
        next_state=None,
        stalled_state=None,
        data_cnt=None,
        done=None,
        pin=None,
        depth: int = 1,
        level: str = "L1",
        name: str = "controller",
        visibility: str = "RTL-hierarchy-only",
        scope: str = "",
        strict: bool = False,
        max_events: int = 64,
        na=(),
        blocked=None,
        log=None,
    ) -> None:
        self._h = {
            "clk": clk,
            "rst_n": rst_n,
            "txn_valid": txn_valid,
            "busy": busy,
            "bus_req": bus_req,
            "cmd": cmd,
            "byte_len": byte_len,
            "rdata_valid": rdata_valid,
            "wdata_next": wdata_next,
            "curr_state": curr_state,
            "next_state": next_state,
            "stalled_state": stalled_state,
            "data_cnt": data_cnt,
            "done": done,
        }
        self._pin = pin
        self._strict = strict
        self._max_events = max_events
        self._log = log

        self.depth = depth
        self.level = level
        self.name = name
        self.visibility = visibility
        self.scope = scope
        self.na = tuple(na)
        self.blocked = dict(blocked or {})
        self._add_handle_blocks()

        self.violations: "list[str]" = []
        self.events: "list[HsViolation]" = []
        self.reset_truncated: "list[HsViolation]" = []
        self.notes: "list[str]" = []

        self._pins = _PinCursor()
        self._accepted_len: "int | None" = None
        self._pending_start: "float | None" = None
        self._starts = 0
        self._fetch_heads = 0
        self._pending_pair: "int | None" = None
        self._prev_state: "int | None" = None
        self._prev_stalled: "int | None" = None
        self._prev_done: "int | None" = None
        self._reported: "set[str]" = set()
        self._in_reset = True
        self._cycle = 0
        self._samples = 0
        self._suppressed = 0
        self._active = False
        self._task = None

    # -- applicability -----------------------------------------------------

    def _add_handle_blocks(self) -> None:
        """Block every applicable row whose named evidence is unavailable."""
        clocked_missing = [
            handle_name
            for handle_name in CONTROLLER_REQUIRED_SIGNALS
            if self._h.get(handle_name) is None
        ]
        for check_id in (
            CHK_CTRL_REQ_GATE,
            CHK_CTRL_REQ_SHAPE,
            CHK_CTRL_STATE_VALID,
            CHK_CTRL_DATA_CNT,
        ):
            if check_id in self.na or check_id in self.blocked:
                continue
            if clocked_missing:
                self.blocked[check_id] = (
                    f"missing handles: {', '.join(clocked_missing)}"
                )

        if CHK_CTRL_FETCH_HEAD not in self.na and CHK_CTRL_FETCH_HEAD not in self.blocked:
            missing = [
                handle_name
                for handle_name in ("clk", "rst_n", "done")
                if self._h.get(handle_name) is None
            ]
            if missing:
                self.blocked[CHK_CTRL_FETCH_HEAD] = (
                    f"missing handles: {', '.join(missing)}"
                )
        if CHK_CTRL_DATA_PAIR not in self.na and CHK_CTRL_DATA_PAIR not in self.blocked:
            if self.depth == TCD_BYTES:
                self.blocked[CHK_CTRL_DATA_PAIR] = (
                    f"DMA_BUF_DEPTH={self.depth} equals the {TCD_BYTES}-byte "
                    "descriptor length, so a pin read cannot be classified as a "
                    "fetch or a payload without the reference oracle"
                )

    def _judged(self, check_id: str) -> bool:
        if check_id in self.na or check_id in self.blocked:
            return False
        if check_id in PIN_EVIDENCE_CHECK_IDS and not self._pin_evidence():
            return False
        return True

    def _pin_evidence(self) -> bool:
        return self._pin is not None and not self._pin.blocked

    @property
    def blocked_rows(self) -> "tuple[str, ...]":
        """Rows that could not be judged on this run."""
        return tuple(
            check_id
            for check_id in CONTROLLER_CHECK_IDS
            if check_id not in self.na and not self._judged(check_id)
        )

    # -- lifecycle ---------------------------------------------------------

    def attach_pin(self, pin) -> None:
        """Supply the pin monitor feeding the two top-observable rows."""
        self._pin = pin
        self._pins.reset()

    def start(self):
        """Launch the background checker. Call before reset release."""
        for check_id in sorted(self.blocked):
            if self._log is not None:
                self._log.warning(
                    "CHECKER BLOCKED id=%s level=%s reason=%s",
                    check_id,
                    self.level,
                    self.blocked[check_id],
                )
        if self._h["clk"] is None or all(
            check_id in self.na for check_id in CONTROLLER_CHECK_IDS
        ):
            return None
        self._active = True
        self._task = cocotb.start_soon(self._run())
        return self._task

    def stop(self) -> None:
        """Soft-stop so a later test in the same module can re-attach."""
        self._active = False

    def clear(self) -> None:
        """Drop findings and sequence state for a fresh directed window."""
        self.events.clear()
        self.violations.clear()
        self.reset_truncated.clear()
        self.notes.clear()
        self._pins.reset()
        self._accepted_len = None
        self._pending_start = None
        self._starts = 0
        self._fetch_heads = 0
        self._pending_pair = None
        self._prev_state = None
        self._prev_stalled = None
        self._prev_done = None
        self._reported.clear()
        self._suppressed = 0

    async def _run(self) -> None:
        clk = self._h["clk"]
        while True:
            await RisingEdge(clk)
            await ReadOnly()
            if self._active:
                self._sample()

    # -- reporting ---------------------------------------------------------

    def _report(self, check_id: str, detail: str, *, once: str = "") -> None:
        if not self._judged(check_id):
            return
        if once:
            if once in self._reported:
                return
            self._reported.add(once)

        event = HsViolation(
            check_id=check_id,
            time_ns=_now_ns(),
            cycle=self._cycle,
            detail=detail,
            reset_truncated=self._in_reset,
        )
        if self._in_reset:
            self.reset_truncated.append(event)
            return
        if len(self.events) >= self._max_events:
            self._suppressed += 1
            return

        self.events.append(event)
        self.violations.append(f"{self.name} {event}")
        if self._log is not None:
            self._log.error(
                "CHECKER FAIL id=%s level=%s visibility=%s %s",
                check_id,
                self.level,
                self.visibility,
                event,
            )
        if self._strict:
            raise AssertionError(str(event))

    # -- sampling ----------------------------------------------------------

    def _sample(self) -> None:
        self._samples += 1
        rst_n = _resolved(self._h["rst_n"])

        if rst_n != 1:
            self._on_reset_sample()
            return

        if self._in_reset:
            self._in_reset = False
            self._prev_state = None
            self._prev_stalled = None
            self._prev_done = None
            self._reported.clear()

        self._cycle += 1

        state = _resolved(self._h["curr_state"])
        next_state = _resolved(self._h["next_state"])
        stalled = _resolved(self._h["stalled_state"])
        txn_valid = _resolved(self._h["txn_valid"])
        busy = _resolved(self._h["busy"])
        bus_req = _resolved(self._h["bus_req"])
        data_cnt = _resolved(self._h["data_cnt"])
        rdata_valid = _resolved(self._h["rdata_valid"])
        wdata_next = _resolved(self._h["wdata_next"])
        done = _resolved(self._h["done"])

        self._check_state_valid(state, next_state, stalled)
        self._check_req_gate(txn_valid, busy, bus_req)
        self._check_req_shape(txn_valid, busy, state, next_state)
        self._check_data_cnt(state, data_cnt, rdata_valid, wdata_next)
        self._track_start(done)
        self._check_pin_intervals()

        self._prev_state = state
        self._prev_stalled = stalled
        self._prev_done = done

    def _on_reset_sample(self) -> None:
        """Drop per-transaction and per-sequence state at a sampled reset edge."""
        self._in_reset = True
        self._accepted_len = None
        self._pending_start = None
        self._pending_pair = None
        self._prev_state = None
        self._prev_stalled = None
        self._prev_done = None
        # A CE# interval torn down by reset is skipped, so drop the cursor's
        # backlog rather than judging half-decoded frames after re-arm.
        self._pins.take(self._pin)

    def _check_state_valid(self, state, next_state, stalled) -> None:
        """``CHK-CTRL-STATE-VALID``: resolved enum members, stable stall origin."""
        for label, value in (
            ("curr_state", state),
            ("next_state", next_state),
            ("stalled_state", stalled),
        ):
            if value is not None and value in SYS_CONTROL_STATES:
                continue
            self._report(
                CHK_CTRL_STATE_VALID,
                f"{label}={_state_text(value, SYS_CONTROL_STATES)} is not a "
                "resolved sys_control_state_t member",
                once=f"state:{label}",
            )

        if (
            self._prev_state == SYS_CONTROL_STALL
            and state == SYS_CONTROL_STALL
            and stalled != self._prev_stalled
        ):
            self._report(
                CHK_CTRL_STATE_VALID,
                "stalled_state was overwritten while stalled: "
                f"{_state_text(self._prev_stalled, SYS_CONTROL_STATES)} -> "
                f"{_state_text(stalled, SYS_CONTROL_STATES)}",
            )

    def _check_req_gate(self, txn_valid, busy, bus_req) -> None:
        """``CHK-CTRL-REQ-GATE``: a request implies idle engine and no bus request."""
        if txn_valid is None:
            self._report(
                CHK_CTRL_REQ_GATE,
                "qspi_txn_valid sampled unresolved (x/z) while rst_n=1",
                once="gate:unresolved",
            )
            return
        if txn_valid != 1:
            return
        if busy != 0:
            self._report(
                CHK_CTRL_REQ_GATE,
                f"qspi_txn_valid=1 with qspi_busy={_show(busy)}; the controller "
                "may only request while the engine is idle",
            )
        if bus_req != 0:
            self._report(
                CHK_CTRL_REQ_GATE,
                f"qspi_txn_valid=1 with synchronized bus_req={_show(bus_req)}; a "
                "pending MCU request must suppress the next QPI transaction",
            )

    def _check_req_shape(self, txn_valid, busy, state, next_state) -> None:
        """``CHK-CTRL-REQ-SHAPE``: fetch length 11, payload length ``1..depth``."""
        if txn_valid != 1 or busy != 0:
            return

        cmd = _resolved(self._h["cmd"])
        byte_len = _resolved(self._h["byte_len"])
        context = (
            f"curr_state={_state_text(state, SYS_CONTROL_STATES)} "
            f"next_state={_state_text(next_state, SYS_CONTROL_STATES)} "
            f"cmd={_show_op(cmd)} byte_len={_show(byte_len)}"
        )

        if state == SYS_CONTROL_NEW_FETCH and next_state == SYS_CONTROL_FETCH:
            if cmd != QSPI_CMD_FAST_READ or byte_len != TCD_BYTES:
                self._report(
                    CHK_CTRL_REQ_SHAPE,
                    f"descriptor fetch must be 0xEB with byte_len={TCD_BYTES}: "
                    f"{context}",
                )
            return

        if state == SYS_CONTROL_NEW_OP and next_state in (
            SYS_CONTROL_READ,
            SYS_CONTROL_WRITE,
        ):
            expected_cmd = (
                QSPI_CMD_FAST_READ
                if next_state == SYS_CONTROL_READ
                else QSPI_CMD_WRITE
            )
            legal_len = byte_len is not None and 1 <= byte_len <= self.depth
            if cmd != expected_cmd or not legal_len:
                self._report(
                    CHK_CTRL_REQ_SHAPE,
                    f"payload request must be {_show_op(expected_cmd)} with "
                    f"byte_len in 1..{self.depth}: {context}",
                )
            self._accepted_len = byte_len
            return

        self._report(
            CHK_CTRL_REQ_SHAPE,
            f"accepted request from an unexpected state pair: {context}",
        )

    def _check_data_cnt(self, state, data_cnt, rdata_valid, wdata_next) -> None:
        """``CHK-CTRL-DATA-CNT``: per-phase bound plus dynamic-index preconditions."""
        if not self._judged(CHK_CTRL_DATA_CNT):
            return
        state_text = _state_text(state, SYS_CONTROL_STATES)
        if data_cnt is None:
            self._report(
                CHK_CTRL_DATA_CNT,
                f"data_cnt sampled unresolved (x/z) in {state_text}",
                once="datacnt:unresolved",
            )
            return

        payload_limit = 2 * self.depth
        if state in PAYLOAD_STATES:
            limit = payload_limit
        else:
            # Fetch phases, plus IDLE/STALL where the RTL clears data_cnt on the
            # following edge; the descriptor bound is the global maximum.
            limit = TCD_NIBBLES
        if data_cnt > limit:
            self._report(
                CHK_CTRL_DATA_CNT,
                f"data_cnt={data_cnt} exceeds {limit} in {state_text} "
                "(an underflow wraps above the bound)",
            )
            return

        accepted = self._accepted_len if self._accepted_len else self.depth
        payload_high = 2 * accepted

        if state == SYS_CONTROL_FETCH and rdata_valid == 1:
            if not 1 <= data_cnt <= TCD_NIBBLES:
                self._report(
                    CHK_CTRL_DATA_CNT,
                    f"descriptor capture with data_cnt={data_cnt}; the dynamic "
                    f"index requires 1..{TCD_NIBBLES}",
                )
        if state == SYS_CONTROL_READ and rdata_valid == 1:
            if not 1 <= data_cnt <= payload_high:
                self._report(
                    CHK_CTRL_DATA_CNT,
                    f"payload capture with data_cnt={data_cnt}; the dynamic index "
                    f"requires 1..{payload_high} for byte_len={accepted}",
                )
        if state == SYS_CONTROL_WRITE:
            if not 1 <= data_cnt <= payload_high:
                self._report(
                    CHK_CTRL_DATA_CNT,
                    f"write data index with data_cnt={data_cnt}; driving write "
                    f"data requires 1..{payload_high} for byte_len={accepted}",
                )
            elif wdata_next == 1 and data_cnt < 2:
                self._report(
                    CHK_CTRL_DATA_CNT,
                    f"qspi_wdata_next=1 with data_cnt={data_cnt}; requesting a "
                    "later nibble requires data_cnt >= 2",
                )

    def _track_start(self, done) -> None:
        """Record accepted STARTs from the top-observable ``DONE`` falling edge.

        ``06-checkers.md`` pins ``CHK-CTRL-FETCH-HEAD`` acceptance to this
        transition, not to an internal synchronized pulse.
        """
        if self._prev_done == 1 and done == 0:
            self._starts += 1
            self._pending_start = _now_ns()

    def _check_pin_intervals(self) -> None:
        """``CHK-CTRL-FETCH-HEAD`` and ``CHK-CTRL-DATA-PAIR`` from pin intervals."""
        for interval in self._pins.take(self._pin):
            if _aborted(interval) or interval.direction == PIN_DIR_UNKNOWN:
                continue
            self._check_fetch_head(interval)
            self._check_data_pair(interval)

    def _check_fetch_head(self, interval) -> None:
        """The first transaction after an accepted START is the head fetch."""
        if not self._judged(CHK_CTRL_FETCH_HEAD):
            return
        if self._pending_start is None:
            return
        if interval.ce_fall_ns < self._pending_start:
            return  # opened before this START was accepted

        self._pending_start = None
        self._fetch_heads += 1
        faults = []
        if interval.opcode != QSPI_CMD_FAST_READ:
            faults.append(f"opcode={_show_op(interval.opcode)} (expected 0xEB)")
        if interval.device != 0:
            faults.append(f"device={interval.device} (expected 0)")
        if interval.address != 0:
            faults.append(f"address={_show_addr(interval.address)} (expected 0x000000)")
        if interval.length != TCD_BYTES:
            faults.append(f"length={interval.length} (expected {TCD_BYTES})")
        if faults:
            self._report(
                CHK_CTRL_FETCH_HEAD,
                "first transaction after an accepted START is not the head "
                f"descriptor fetch: {'; '.join(faults)} ({interval.canonical()})",
            )

    def _check_data_pair(self, interval) -> None:
        """Local read/write alternation and equal length between fetches.

        Intentionally weaker than the reference scoreboard: no TCD address or
        payload is predicted here. A read of the descriptor length is treated as
        a fetch, which restarts pairing; any other read must be answered by
        exactly one same-length write.
        """
        if not self._judged(CHK_CTRL_DATA_PAIR):
            return

        if interval.direction == PIN_DIR_READ:
            if self._pending_pair is not None:
                self._report(
                    CHK_CTRL_DATA_PAIR,
                    f"payload read of {self._pending_pair} byte(s) was not followed "
                    f"by its payload write ({interval.canonical()})",
                )
                self._pending_pair = None
                return
            if interval.length == TCD_BYTES:
                return  # descriptor fetch: pairing restarts here
            self._pending_pair = interval.length
            return

        if self._pending_pair is None:
            self._report(
                CHK_CTRL_DATA_PAIR,
                "payload write with no preceding unpaired payload read "
                f"({interval.canonical()})",
            )
            return
        if interval.length != self._pending_pair:
            self._report(
                CHK_CTRL_DATA_PAIR,
                f"payload write of {interval.length} byte(s) does not match the "
                f"{self._pending_pair}-byte payload read it answers "
                f"({interval.canonical()})",
            )
        self._pending_pair = None

    # -- results -----------------------------------------------------------

    def counts(self) -> "dict[str, int]":
        """Return the ordinary violation count for every ID this monitor owns."""
        counts = {check_id: 0 for check_id in CONTROLLER_CHECK_IDS}
        for event in self.events:
            counts[event.check_id] += 1
        return counts

    def results(self) -> "dict[str, str]":
        """Return per-ID ``pass`` / ``fail`` / ``na`` / ``blocked`` disposition."""
        counts = self.counts()
        dispositions = {}
        for check_id in CONTROLLER_CHECK_IDS:
            if check_id in self.na:
                dispositions[check_id] = RESULT_NA
            elif counts[check_id]:
                dispositions[check_id] = RESULT_FAIL
            elif self._judged(check_id):
                dispositions[check_id] = RESULT_PASS
            else:
                dispositions[check_id] = RESULT_BLOCKED
        return dispositions

    def blocked_reasons(self) -> "dict[str, str]":
        """Return the reason string behind every ``blocked`` row."""
        reasons = dict(self.blocked)
        if not self._pin_evidence():
            for check_id in (CHK_CTRL_FETCH_HEAD, CHK_CTRL_DATA_PAIR):
                if check_id not in self.na:
                    reasons.setdefault(check_id, NO_PIN_EVIDENCE_REASON)
        return {
            check_id: reason
            for check_id, reason in reasons.items()
            if check_id not in self.na
        }

    def violations_for(self, check_id: str) -> "list[HsViolation]":
        """Return recorded events for one catalog ID (negative-test helper)."""
        return [event for event in self.events if event.check_id == check_id]

    def review_reset_truncated(self) -> "list[HsViolation]":
        """Return ``RESET-TRUNCATED`` findings for explicit test dispose."""
        return list(self.reset_truncated)

    def pending_start(self) -> bool:
        """True when an accepted START has no following transaction yet.

        Not a failure on its own: a reset or an end-of-test may legitimately
        close the window before the head fetch appears. Reported in
        :meth:`summary` so the evidence is visible.
        """
        return self._pending_start is not None

    def summary(self) -> str:
        parts = [f"{check_id}={result}" for check_id, result in self.results().items()]
        parts.append(f"starts={self._starts}")
        parts.append(f"fetch_heads={self._fetch_heads}")
        if self.pending_start():
            parts.append("start_without_transaction=1")
        if self._pending_pair is not None:
            parts.append(f"unpaired_read={self._pending_pair}")
        if self.reset_truncated:
            parts.append(f"reset_truncated={len(self.reset_truncated)}")
        if self._suppressed:
            parts.append(f"suppressed={self._suppressed}")
        scope = f", scope={self.scope}" if self.scope else ""
        return (
            f"{self.name} ({self.level}, {self.visibility}{scope}, "
            f"{self._samples} samples, depth={self.depth}): " + " ".join(parts)
        )


def start_controller_monitor(
    dut,
    *,
    strict: bool = False,
    level: "str | None" = None,
    name: str = "controller",
    depth: "int | None" = None,
    pin=None,
    log=None,
    **kwargs,
) -> ControllerMonitor:
    """Create and start the ``CHK-CTRL-*`` monitor for *dut*.

    Every row is ``na`` at L0 (``qspi_engine`` has no controller) and at L2 (no
    source hierarchy). At L1 a missing hierarchy name is ``blocked`` with the
    missing list, never a silent skip. Pass *pin* so the two top-observable rows
    have pin evidence, or call :meth:`ControllerMonitor.attach_pin` later.
    """
    log = dut._log if log is None else log
    top, controller, scope_name = _controller_scope(dut)

    if level is None:
        if _optional(dut, "txn_valid") is not None:
            level = "L0"
        else:
            level = "L1" if controller is not None else "L2"

    na = list(kwargs.pop("na", ()))
    if level != "L1":
        na += [check_id for check_id in CONTROLLER_CHECK_IDS if check_id not in na]

    handles = {
        "clk": _optional(dut, "clk"),
        "rst_n": _optional(dut, "rst_n"),
        "txn_valid": _optional(top, "qspi_txn_valid"),
        "busy": _optional(top, "qspi_busy"),
        "bus_req": _optional(top, "bus_req"),
        "cmd": _optional(top, "qspi_cmd"),
        "byte_len": _optional(top, "qspi_byte_len"),
        "rdata_valid": _optional(top, "qspi_rdata_valid"),
        "wdata_next": _optional(top, "qspi_wdata_next"),
        "curr_state": _optional(controller, "curr_state"),
        "next_state": _optional(controller, "next_state"),
        "stalled_state": _optional(controller, "stalled_state"),
        "data_cnt": _optional(controller, "data_cnt"),
        "done": _optional(dut, "done"),
    }

    if depth is None:
        depth = parse_run_config()["dma_buf_depth"]

    monitor = ControllerMonitor(
        level=level,
        name=name,
        visibility="RTL-hierarchy-only",
        scope=scope_name,
        depth=depth,
        strict=strict,
        pin=pin,
        na=tuple(na),
        log=log,
        **handles,
        **kwargs,
    )
    monitor.start()
    return monitor


__all__ = [
    "CHK_CTRL_DATA_CNT",
    "CHK_CTRL_DATA_PAIR",
    "CHK_CTRL_FETCH_HEAD",
    "CHK_CTRL_REQ_GATE",
    "CHK_CTRL_REQ_SHAPE",
    "CHK_CTRL_STATE_VALID",
    "CHK_HS_OPCODE",
    "CHK_HS_PULSE_WIDTH",
    "CHK_HS_RDATA_COUNT",
    "CHK_HS_REQ_STABLE",
    "CHK_HS_TXN_START",
    "CHK_HS_WDATA_COUNT",
    "CHK_HS_WDATA_KNOWN",
    "CONTROLLER_CHECK_IDS",
    "HANDSHAKE_CHECK_IDS",
    "ControllerMonitor",
    "HandshakeMonitor",
    "HsTransaction",
    "HsViolation",
    "QSPI_ENGINE_STATES",
    "SYS_CONTROL_IDLE",
    "SYS_CONTROL_STATES",
    "TCD_BYTES",
    "TCD_NIBBLES",
    "start_controller_monitor",
    "start_handshake_monitor",
]
