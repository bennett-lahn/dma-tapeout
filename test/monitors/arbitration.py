"""Bus grant, output-enable, park, and reset-release monitors.

Catalog: ``docs/llm/verification/06-checkers.md``. This module owns the grant /
OE / park / reset-release rows, matching the planned monitor split in that
document ("grant, OE, park, reset release, and bus atomicity"):

======================== ======== ======== ==============================
``CHK-*``                L0       L1       Visibility
======================== ======== ======== ==============================
``CHK-ARB-GNT-OE``       ``na``   required top-observable
``CHK-ARB-GNT-QUIET``    ``na``   required top-observable
``CHK-ARB-PARK``         ``na``   required top-observable
``CHK-ARB-GNT-NOT-BUSY`` ``na``   required RTL-hierarchy-only
``CHK-RST-OE``           ``na``   required top-observable
``CHK-RST-STATUS``       ``na``   required top-observable
``CHK-RST-INTERNAL``     subset   required hierarchy (+ L0 ports)
======================== ======== ======== ==============================

The ``CHK-CTRL-*`` rows are **not** here: ``06-checkers.md`` assigns controller
bounds to :mod:`monitors.handshake`, which owns all six of them (see
:class:`monitors.handshake.ControllerMonitor`).

Sampling, per ``06-checkers.md``:

* ``CHK-RST-OE`` is the one deliberate combinational row. Top-level ``uio_oe``
  is gated directly by ``rst_n``, so this monitor watches value changes and
  requires immediate release at **all** observed times with ``rst_n=0``.
* every other row is judged after a rising ``clk`` edge in the read-only phase.
  That is complete evidence at L1: ``uio_oe`` is a function of ``rst_n`` plus
  the registered ``bus_gnt`` and ``sio_oe``, so it cannot glitch between
  sampled edges.
* ``CHK-RST-STATUS`` / ``CHK-RST-INTERNAL`` are judged only at rising ``clk``
  edges sampled with ``rst_n=0``, never at the asynchronous ``rst_n`` fall, and
  never before the first such edge.
* the ARB rows re-arm on the first rising ``clk`` sampled with ``rst_n=1``.

Findings here are never classified ``RESET-TRUNCATED``: the ``CHK-RST-*`` rows
exist to judge the reset window itself, and the ``CHK-ARB-*`` rows only apply
while ``rst_n=1`` (park requires it explicitly; ``bus_gnt`` is 0 in reset). The
:attr:`ArbitrationMonitor.reset_truncated` list therefore stays empty and is
kept only so :mod:`common.dispose` can expand this monitor like any other.

Two float-tolerance rules keep the resolved-bus rows honest without weakening
them (``04-timing-in-sim.md``, bus resolution):

1. A released net is judged as "not driven low" / "not driven high", never as a
   known level. ``BUS_GNT`` rises in the same delta that clears ``uio_oe``, so
   the shared CS nets fall back to their board pull-ups and SCK floats at the
   very cycle the grant-rise condition is evaluated.
2. ``CHK-ARB-PARK`` skips the complete CE#-low interval **and** the post-CE#
   SIO release window (``release_cycles``, one ``clk`` by construction: the
   engine's ``CS_OFF`` cycle reclaims SIO OE only on the following edge). A
   read must float SIO for its dummy and read phases, so demanding all eight
   OEs there would be wrong.

Signal source by level (:func:`start_arbitration_monitor` resolves it):

* **L1** - ``tb_top`` alias set (``uio_oe``, ``uio_out`` scalars, ``bus_*``,
  ``done``, ``bus_gnt``) plus ``dut.dut`` / ``dut.dut.sys_controller`` /
  ``dut.dut.qspi_engine`` for the hierarchy rows.
* **L0** - ``tb_engine`` ports plus ``dut.dut`` (the ``qspi_engine`` instance)
  for the ``CHK-RST-INTERNAL`` engine subset; every other row is ``na``.
* **L2** - top-observable rows only; the hierarchy rows are ``na`` because
  source hierarchy is not a sign-off interface.
"""

from dataclasses import dataclass

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import First, ReadOnly, RisingEdge

from monitors.handshake import (
    QSPI_ENGINE_STATES,
    SYS_CONTROL_IDLE,
    SYS_CONTROL_STATES,
)

CHK_ARB_GNT_OE = "CHK-ARB-GNT-OE"
CHK_ARB_GNT_QUIET = "CHK-ARB-GNT-QUIET"
CHK_ARB_PARK = "CHK-ARB-PARK"
CHK_ARB_GNT_NOT_BUSY = "CHK-ARB-GNT-NOT-BUSY"
CHK_RST_OE = "CHK-RST-OE"
CHK_RST_STATUS = "CHK-RST-STATUS"
CHK_RST_INTERNAL = "CHK-RST-INTERNAL"

ARBITRATION_CHECK_IDS = (
    CHK_ARB_GNT_OE,
    CHK_ARB_GNT_QUIET,
    CHK_ARB_PARK,
    CHK_ARB_GNT_NOT_BUSY,
    CHK_RST_OE,
    CHK_RST_STATUS,
    CHK_RST_INTERNAL,
)

# Rows the catalog marks `na` at L0: they need top-level uio_oe / BUS_GNT, which
# are not qspi_engine ports.
L1_ONLY_CHECK_IDS = (
    CHK_ARB_GNT_OE,
    CHK_ARB_GNT_QUIET,
    CHK_ARB_PARK,
    CHK_ARB_GNT_NOT_BUSY,
    CHK_RST_OE,
    CHK_RST_STATUS,
)

# Rows that read named RTL signals; `na` at L2 per 06-checkers.md.
HIERARCHY_CHECK_IDS = (CHK_ARB_GNT_NOT_BUSY, CHK_RST_INTERNAL)

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_NA = "na"
RESULT_BLOCKED = "blocked"

UIO_WIDTH = 8
ALL_OE_ENABLED = (1 << UIO_WIDTH) - 1

# Post-CE# SIO release window excluded from CHK-ARB-PARK (see module docstring).
DEFAULT_RELEASE_CYCLES = 1

# Handle names this monitor understands. A row whose names are not all present
# reports `blocked` with the missing list, never a silent skip.
HANDLE_NAMES = (
    "clk",
    "rst_n",
    "uio_oe",
    "bus_gnt",
    "done",
    "bus_sck",
    "bus_ram_a_cs_n",
    "bus_ram_b_cs_n",
    "asic_flash_cs_out",
    "asic_sck_out",
    "asic_ram_a_cs_out",
    "asic_ram_a_cs_oe",
    "asic_ram_b_cs_out",
    "asic_ram_b_cs_oe",
    "sck_oe",
    "qspi_busy",
)

REQUIRED_BY_ID = {
    CHK_ARB_GNT_OE: ("clk", "uio_oe", "bus_gnt"),
    CHK_ARB_GNT_QUIET: (
        "clk",
        "bus_gnt",
        "bus_sck",
        "bus_ram_a_cs_n",
        "bus_ram_b_cs_n",
        "asic_ram_a_cs_out",
        "asic_ram_a_cs_oe",
        "asic_ram_b_cs_out",
        "asic_ram_b_cs_oe",
    ),
    CHK_ARB_PARK: (
        "clk",
        "rst_n",
        "uio_oe",
        "bus_gnt",
        "bus_ram_a_cs_n",
        "bus_ram_b_cs_n",
        "asic_flash_cs_out",
        "asic_sck_out",
        "asic_ram_a_cs_out",
        "asic_ram_b_cs_out",
    ),
    CHK_ARB_GNT_NOT_BUSY: ("clk", "bus_gnt", "qspi_busy"),
    CHK_RST_OE: ("rst_n", "uio_oe"),
    CHK_RST_STATUS: (
        "clk",
        "rst_n",
        "done",
        "bus_gnt",
        "bus_ram_a_cs_n",
        "bus_ram_b_cs_n",
        "asic_ram_a_cs_out",
        "asic_ram_a_cs_oe",
        "asic_ram_b_cs_out",
        "asic_ram_b_cs_oe",
    ),
    CHK_RST_INTERNAL: ("clk", "rst_n"),
}

_KNOWN_LEVEL = {"0": 0, "1": 1}


def _bits(handle) -> "list[int | None]":
    """Return LSB-first bit levels of *handle*; ``None`` where the bit is x/z."""
    text = str(handle.value).strip().lower()
    return [_KNOWN_LEVEL.get(char) for char in reversed(text)]


def _level(handle) -> "int | None":
    """Return the level of a 1-bit *handle*, or ``None`` while it holds x/z."""
    if handle is None:
        return None
    return _bits(handle)[0]


def _word(handle) -> "int | None":
    """Return the integer value of *handle*, or ``None`` if any bit is x/z."""
    if handle is None:
        return None
    value = 0
    for index, bit in enumerate(_bits(handle)):
        if bit is None:
            return None
        value |= bit << index
    return value


def _show(value: "int | None") -> str:
    return "x/z" if value is None else str(value)


def _show_hex(value: "int | None", width: int = 2) -> str:
    return "x/z" if value is None else f"0x{value:0{width}X}"


def _state_text(value: "int | None", symbols: "dict[int, str]") -> str:
    """Render an enum sample symbolically and rawly (``06-checkers.md``)."""
    if value is None:
        return "x/z"
    name = symbols.get(value)
    return f"{name}(0x{value:X})" if name else f"<not-in-enum>(0x{value:X})"


def _now_ns() -> float:
    return float(get_sim_time(unit="ns"))


@dataclass(frozen=True)
class ArbViolation:
    """One arbitration or reset-release finding, timestamped at its sample."""

    check_id: str
    time_ns: float
    cycle: int
    detail: str
    reset_truncated: bool = False

    def __str__(self) -> str:
        return f"{self.check_id} at {self.time_ns:.3f}ns cycle={self.cycle}: {self.detail}"


@dataclass(frozen=True)
class ResetExpectation:
    """One named signal that must hold *expected* at a sampled reset edge."""

    label: str
    handle: object
    expected: int
    symbols: "dict[int, str] | None" = None

    def text(self, value: "int | None") -> str:
        if self.symbols is not None:
            return _state_text(value, self.symbols)
        return _show(value)


class ArbitrationMonitor:
    """Always-on grant / OE / park / reset-release checker.

    Two background tasks: a clocked sampler for every row except
    ``CHK-RST-OE``, and a value-change watcher for ``CHK-RST-OE`` (the catalog's
    combinational exception). Level conditions are reported once per entry into
    the condition so a multi-cycle grant or park failure is one finding, not one
    per clock.
    """

    def __init__(
        self,
        *,
        handles: "dict[str, object] | None" = None,
        reset_expectations=(),
        level: str = "L1",
        name: str = "arbitration",
        visibility: str = "top-observable",
        release_cycles: int = DEFAULT_RELEASE_CYCLES,
        na=(),
        blocked=None,
        strict: bool = False,
        max_events: int = 64,
        log=None,
    ) -> None:
        handles = handles or {}
        self._h = {handle_name: handles.get(handle_name) for handle_name in HANDLE_NAMES}
        self._resets = tuple(reset_expectations)
        self._release_cycles = release_cycles
        self._strict = strict
        self._max_events = max_events
        self._log = log

        self.level = level
        self.name = name
        self.visibility = visibility
        self.na = tuple(na)
        self.blocked = dict(blocked or {})
        self._add_handle_blocks()

        self.violations: "list[str]" = []
        self.events: "list[ArbViolation]" = []
        self.reset_truncated: "list[ArbViolation]" = []
        self.notes: "list[str]" = []

        self._condition_active: "dict[object, bool]" = {}
        self._reset_reported: "set[str]" = set()
        self._prev_gnt: "int | None" = None
        self._prev_rst: "int | None" = None
        self._saw_reset_edge = False
        self._release_wait = 0
        self._cycle = 0
        self._samples = 0
        self._reset_samples = 0
        self._suppressed = 0
        self._active = False
        self._tasks: list = []

    # -- applicability -----------------------------------------------------

    def _add_handle_blocks(self) -> None:
        """Mark every applicable row whose required handles are incomplete."""
        for check_id, names in REQUIRED_BY_ID.items():
            if check_id in self.na or check_id in self.blocked:
                continue
            missing = [name for name in names if self._h.get(name) is None]
            if check_id == CHK_RST_INTERNAL and not self._resets:
                missing.append("reset expectation signals")
            if missing:
                self.blocked[check_id] = f"missing handles: {', '.join(missing)}"

    def _judged(self, check_id: str) -> bool:
        return check_id not in self.na and check_id not in self.blocked

    @property
    def fully_blocked(self) -> bool:
        """True when no applicable row on this level could be judged."""
        return all(not self._judged(check_id) for check_id in ARBITRATION_CHECK_IDS)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Launch the background checkers. Call before reset release."""
        if self.blocked and self._log is not None:
            for check_id, reason in sorted(self.blocked.items()):
                self._log.warning(
                    "CHECKER BLOCKED id=%s level=%s reason=%s",
                    check_id,
                    self.level,
                    reason,
                )
        self._active = True
        self._tasks = []
        clocked = any(
            self._judged(check_id)
            for check_id in ARBITRATION_CHECK_IDS
            if check_id != CHK_RST_OE
        )
        if clocked and self._h["clk"] is not None:
            self._tasks.append(cocotb.start_soon(self._run_clocked()))
        if self._judged(CHK_RST_OE):
            self._tasks.append(cocotb.start_soon(self._run_reset_oe()))
        return tuple(self._tasks)

    def stop(self) -> None:
        """Soft-stop so a later test in the same module can re-attach."""
        self._active = False

    def clear(self) -> None:
        """Drop findings and latched condition state for a fresh window."""
        self.events.clear()
        self.violations.clear()
        self.reset_truncated.clear()
        self.notes.clear()
        self._condition_active.clear()
        self._reset_reported.clear()
        self._prev_gnt = None
        self._suppressed = 0

    async def _run_clocked(self) -> None:
        clk = self._h["clk"]
        while True:
            await RisingEdge(clk)
            await ReadOnly()
            if self._active:
                self._sample()

    async def _run_reset_oe(self) -> None:
        watched = [self._h["rst_n"], self._h["uio_oe"]]
        while True:
            await First(*[handle.value_change for handle in watched])
            await ReadOnly()
            if self._active:
                self._check_rst_oe()

    # -- reporting ---------------------------------------------------------

    def _report(self, check_id: str, detail: str) -> None:
        if not self._judged(check_id):
            return
        event = ArbViolation(
            check_id=check_id, time_ns=_now_ns(), cycle=self._cycle, detail=detail
        )
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

    def _latch(self, check_id: str, key, active: bool, detail: str) -> None:
        """Record *check_id* once per false-to-true transition of *active*."""
        was_active = self._condition_active.get(key, False)
        self._condition_active[key] = active
        if active and not was_active:
            self._report(check_id, detail)

    def _report_once_per_reset(self, check_id: str, key: str, detail: str) -> None:
        """Record *check_id* once per reset window, not once per reset edge."""
        if key in self._reset_reported:
            return
        self._reset_reported.add(key)
        self._report(check_id, detail)

    # -- sampling ----------------------------------------------------------

    def _sample(self) -> None:
        """Judge one settled sample, honouring the read-only sampling skew.

        A cocotb read-only sample holds each register's value *after* the edge
        just taken, but ``rst_n`` (driven from Python between edges) reads the
        value the flops will consume at the **next** edge. So the sample whose
        state reflects "a rising ``clk`` edge sampled with ``rst_n=0``" is the
        one *following* a sample that read ``rst_n=0``. Judging reset state on
        the assertion sample itself would demand reset values one edge early,
        and judging park on the release sample would demand released values one
        edge early; both are avoided here.
        """
        self._samples += 1
        rst_n = _level(self._h["rst_n"])
        reset_applied = self._prev_rst == 0 or (self._prev_rst is None and rst_n == 0)
        self._prev_rst = rst_n

        if reset_applied:
            self._on_reset_sample()
            return
        if rst_n != 1:
            # The assertion sample: reset lands on the next edge, so no row is
            # judged here. Per-condition latches drop so the post-reset window
            # reports a condition again on its next entry.
            self._condition_active.clear()
            self._prev_gnt = None
            return

        if self._reset_reported:
            # Leaving a reset window: let the next one report its own findings.
            self._reset_reported.clear()

        self._cycle += 1
        gnt = _level(self._h["bus_gnt"])
        self._check_gnt_oe(gnt)
        self._check_gnt_not_busy(gnt)
        self._check_gnt_quiet(gnt)
        self._check_park(gnt)
        self._prev_gnt = gnt

    def _on_reset_sample(self) -> None:
        """Judge the reset rows for a rising ``clk`` edge taken with ``rst_n=0``."""
        self._saw_reset_edge = True
        self._reset_samples += 1
        self._condition_active.clear()
        self._prev_gnt = None
        self._release_wait = 0
        self._check_rst_status()
        self._check_rst_internal()

    # -- resolved-bus helpers ---------------------------------------------

    def _asic_drives_low(self, oe_name: str, out_name: str) -> "bool | None":
        """True when the ASIC enables *out_name* and drives it low."""
        oe = _level(self._h[oe_name])
        out = _level(self._h[out_name])
        if oe is None or out is None:
            return None
        return oe == 1 and out == 0

    def _asic_selected_rams(self) -> "list[str]":
        """Return RAM labels the ASIC itself is driving CE# low on."""
        selected = []
        for label, oe_name, out_name in (
            ("PSRAM0", "asic_ram_a_cs_oe", "asic_ram_a_cs_out"),
            ("PSRAM1", "asic_ram_b_cs_oe", "asic_ram_b_cs_out"),
        ):
            if self._asic_drives_low(oe_name, out_name):
                selected.append(label)
        return selected

    def _net_selected_rams(self) -> "list[str]":
        """Return RAM labels whose resolved CE# net reads low."""
        selected = []
        for label, name in (
            ("PSRAM0", "bus_ram_a_cs_n"),
            ("PSRAM1", "bus_ram_b_cs_n"),
        ):
            if _level(self._h[name]) == 0:
                selected.append(label)
        return selected

    def _sck_high(self) -> bool:
        """True only when the resolved SCK net reads a driven high level.

        A released SCK has no board keeper, so a floating or simulator-sticky
        level is not evidence of an SCK cycle. Same rule as
        :func:`monitors.qspi.sck_is_parked`.
        """
        if _level(self._h["bus_sck"]) != 1:
            return False
        oe = self._h["sck_oe"]
        if oe is None:
            return True
        driven = _word(oe)
        return driven is None or driven != 0

    def _transaction_active(self) -> bool:
        """True while an ASIC QPI transaction owns the bus (CE#-low interval)."""
        return bool(self._net_selected_rams() or self._asic_selected_rams())

    # -- checks ------------------------------------------------------------

    def _check_gnt_oe(self, gnt: "int | None") -> None:
        """``CHK-ARB-GNT-OE``: every ASIC ``uio_oe`` bit is 0 under grant.

        This is also the row that owns MCU-versus-ASIC drive overlap on the
        shared nets: the MCU may only drive while ``BUS_GNT=1``, and the ASIC
        must have released every enable by then
        (:class:`monitors.qspi.SharedBusMonitor` notes such overlap and points
        here rather than failing ``CHK-PIN-SIO-OWN``).
        """
        if not self._judged(CHK_ARB_GNT_OE):
            return
        oe = _word(self._h["uio_oe"])
        self._latch(
            CHK_ARB_GNT_OE,
            "gnt-unresolved",
            gnt is None,
            "BUS_GNT unresolved (x/z) while rst_n=1; grant gates every shared "
            "output enable and must always hold a value",
        )
        self._latch(
            CHK_ARB_GNT_OE,
            "gnt-oe",
            gnt == 1 and oe != 0,
            f"BUS_GNT=1 with uio_oe={_show_hex(oe)}; all eight ASIC output "
            "enables must be 0 while the MCU owns the shared bus",
        )

    def _check_gnt_not_busy(self, gnt: "int | None") -> None:
        """``CHK-ARB-GNT-NOT-BUSY``: internal ``qspi_busy`` is 0 across a grant."""
        if not self._judged(CHK_ARB_GNT_NOT_BUSY):
            return
        busy = _level(self._h["qspi_busy"])
        if self._prev_gnt == 0 and gnt == 1 and busy != 0:
            self._report(
                CHK_ARB_GNT_NOT_BUSY,
                f"BUS_GNT rose with qspi_busy={_show(busy)}; a grant may only be "
                "issued between QPI transactions",
            )
        self._latch(
            CHK_ARB_GNT_NOT_BUSY,
            "gnt-busy-hold",
            gnt == 1 and busy != 0,
            f"qspi_busy={_show(busy)} during the grant interval; it must stay 0 "
            "for the complete grant",
        )

    def _check_gnt_quiet(self, gnt: "int | None") -> None:
        """``CHK-ARB-GNT-QUIET``: no ASIC transaction begins or persists under grant.

        The internal ``qspi_busy`` value is out of scope here; that stronger
        condition is ``CHK-ARB-GNT-NOT-BUSY``.
        """
        if not self._judged(CHK_ARB_GNT_QUIET):
            return
        asic_selected = self._asic_selected_rams()
        self._latch(
            CHK_ARB_GNT_QUIET,
            "gnt-quiet",
            gnt == 1 and bool(asic_selected),
            f"ASIC drives {' and '.join(asic_selected) or 'a RAM'} CE# low while "
            "BUS_GNT=1; no ASIC QPI transaction may begin or remain active under grant",
        )

        if not (self._prev_gnt == 0 and gnt == 1):
            return
        net_selected = self._net_selected_rams()
        if net_selected:
            self._report(
                CHK_ARB_GNT_QUIET,
                f"BUS_GNT rose with {' and '.join(net_selected)} CE# low on the "
                "resolved bus; grant may only rise with both RAM CE# high",
            )
        if self._sck_high():
            self._report(
                CHK_ARB_GNT_QUIET,
                "BUS_GNT rose with SCK high on the resolved bus; grant may only "
                "rise with SCK parked low",
            )

    def _check_park(self, gnt: "int | None") -> None:
        """``CHK-ARB-PARK``: the ASIC keeps the idle bus while it owns it.

        Skipped for the complete CE#-low interval and for the post-CE# SIO
        release window (``release_cycles``); see the module docstring.
        """
        if not self._judged(CHK_ARB_PARK):
            return

        if self._transaction_active():
            self._release_wait = self._release_cycles
            self._condition_active.pop("park", None)
            return
        if self._release_wait > 0:
            self._release_wait -= 1
            self._condition_active.pop("park", None)
            return
        if gnt != 0:
            self._condition_active.pop("park", None)
            return

        oe = _word(self._h["uio_oe"])
        flash_cs = _level(self._h["asic_flash_cs_out"])
        sck = _level(self._h["asic_sck_out"])
        ram_a = _level(self._h["asic_ram_a_cs_out"])
        ram_b = _level(self._h["asic_ram_b_cs_out"])

        faults = []
        if oe != ALL_OE_ENABLED:
            faults.append(f"uio_oe={_show_hex(oe)} (expected 0xFF)")
        if flash_cs != 1:
            faults.append(f"flash CS out={_show(flash_cs)} (expected 1)")
        if ram_a != 1:
            faults.append(f"RAM A CS out={_show(ram_a)} (expected 1)")
        if ram_b != 1:
            faults.append(f"RAM B CS out={_show(ram_b)} (expected 1)")
        if sck != 0:
            faults.append(f"SCK out={_show(sck)} (expected 0)")

        self._latch(
            CHK_ARB_PARK,
            "park",
            bool(faults),
            "idle bus not parked while rst_n=1, BUS_GNT=0, and no QPI "
            f"transaction is active: {'; '.join(faults)}",
        )

    def _check_rst_oe(self) -> None:
        """``CHK-RST-OE``: ``uio_oe`` is 0 at all observed times with ``rst_n=0``.

        The catalog's one combinational row: ``uio_oe`` is gated directly by
        ``rst_n``, so release is required immediately, not at a clock edge.
        """
        rst_n = _level(self._h["rst_n"])
        if rst_n is None:
            return  # rst_n itself unresolved: nothing to require yet
        oe = _word(self._h["uio_oe"])
        self._latch(
            CHK_RST_OE,
            "rst-oe",
            rst_n == 0 and oe != 0,
            f"uio_oe={_show_hex(oe)} while rst_n=0; every shared output enable "
            "is gated combinationally by rst_n and must release immediately",
        )

    def _check_rst_status(self) -> None:
        """``CHK-RST-STATUS``: sampled reset status at the top-level pins."""
        if not self._judged(CHK_RST_STATUS):
            return

        done = _level(self._h["done"])
        if done != 1:
            self._report_once_per_reset(
                CHK_RST_STATUS,
                "done",
                f"DONE={_show(done)} after a rising clk sampled with rst_n=0 "
                "(expected 1)",
            )
        gnt = _level(self._h["bus_gnt"])
        if gnt != 0:
            self._report_once_per_reset(
                CHK_RST_STATUS,
                "gnt",
                f"BUS_GNT={_show(gnt)} after a rising clk sampled with rst_n=0 "
                "(expected 0)",
            )
        asic_selected = self._asic_selected_rams()
        if asic_selected:
            self._report_once_per_reset(
                CHK_RST_STATUS,
                "asic-select",
                f"ASIC drives {' and '.join(asic_selected)} CE# low after a "
                "rising clk sampled with rst_n=0; no driven RAM selection may exist",
            )
        net_selected = self._net_selected_rams()
        if net_selected:
            self._report_once_per_reset(
                CHK_RST_STATUS,
                "net-select",
                f"{' and '.join(net_selected)} CE# low on the resolved bus after "
                "a rising clk sampled with rst_n=0; no RAM selection may exist",
            )

    def _check_rst_internal(self) -> None:
        """``CHK-RST-INTERNAL``: named state is at its reset value."""
        if not self._judged(CHK_RST_INTERNAL):
            return
        for expectation in self._resets:
            value = _word(expectation.handle)
            if value == expectation.expected:
                continue
            self._report_once_per_reset(
                CHK_RST_INTERNAL,
                f"internal:{expectation.label}",
                f"{expectation.label}={expectation.text(value)} after a rising "
                f"clk sampled with rst_n=0 (expected "
                f"{expectation.text(expectation.expected)})",
            )

    # -- results -----------------------------------------------------------

    def counts(self) -> "dict[str, int]":
        """Return the violation count for every ID this monitor disposes."""
        counts = {check_id: 0 for check_id in ARBITRATION_CHECK_IDS}
        for event in self.events:
            counts[event.check_id] += 1
        return counts

    def results(self) -> "dict[str, str]":
        """Return per-ID ``pass`` / ``fail`` / ``na`` / ``blocked`` disposition."""
        counts = self.counts()
        dispositions = {}
        for check_id in ARBITRATION_CHECK_IDS:
            if check_id in self.na:
                dispositions[check_id] = RESULT_NA
            elif counts[check_id]:
                dispositions[check_id] = RESULT_FAIL
            elif check_id in self.blocked:
                dispositions[check_id] = RESULT_BLOCKED
            else:
                dispositions[check_id] = RESULT_PASS
        return dispositions

    def blocked_reasons(self) -> "dict[str, str]":
        """Return the reason string behind every ``blocked`` row."""
        return dict(self.blocked)

    def violations_for(self, check_id: str) -> "list[ArbViolation]":
        """Return recorded events for one catalog ID (negative-test helper)."""
        return [event for event in self.events if event.check_id == check_id]

    def review_reset_truncated(self) -> "list[ArbViolation]":
        """Always empty here; see the module docstring."""
        return list(self.reset_truncated)

    @property
    def saw_reset_edge(self) -> bool:
        """True once a rising ``clk`` edge was sampled with ``rst_n=0``."""
        return self._saw_reset_edge

    def summary(self) -> str:
        parts = [f"{check_id}={result}" for check_id, result in self.results().items()]
        if self._suppressed:
            parts.append(f"suppressed={self._suppressed}")
        return (
            f"{self.name} ({self.level}, {self.visibility}, {self._samples} samples, "
            f"{self._reset_samples} reset edges): " + " ".join(parts)
        )


# -- attachment ------------------------------------------------------------


def _optional(obj, name):
    if obj is None:
        return None
    try:
        return getattr(obj, name)
    except AttributeError:
        return None


def _first(obj, *names):
    for name in names:
        handle = _optional(obj, name)
        if handle is not None:
            return handle
    return None


def _scopes(dut) -> "tuple[object | None, object | None, object | None]":
    """Return ``(top, engine, controller)`` RTL scopes for this DUT level.

    L0 (``tb_engine``) instantiates ``qspi_engine`` as ``dut``, so the engine
    scope is ``dut.dut`` and there is no controller. L1/L2 instantiate
    ``tt_um_lahnb_sgdma`` as ``dut``, whose children are ``qspi_engine`` and
    ``sys_controller``. A gate-level netlist has neither.
    """
    inner = _optional(dut, "dut")
    if inner is None:
        return None, None, None
    engine = _optional(inner, "qspi_engine")
    if engine is not None:
        return inner, engine, _optional(inner, "sys_controller")
    if _optional(inner, "curr_state") is not None:
        return None, inner, None
    return inner, None, None


def _engine_expectations(engine, ports) -> "tuple[list[ResetExpectation], list[str]]":
    """Return the engine half of ``CHK-RST-INTERNAL`` plus missing labels."""
    wanted = (
        ("qspi_engine.curr_state", _optional(engine, "curr_state"), 0, QSPI_ENGINE_STATES),
        ("qspi_engine.cycle_cnt", _optional(engine, "cycle_cnt"), 0, None),
        ("qspi_busy", ports["busy"], 0, None),
        ("qspi_rdata_valid", ports["rdata_valid"], 0, None),
        ("qspi_wdata_next", ports["wdata_next"], 0, None),
        ("ram_a_cs_n", ports["ram_a_cs_n"], 1, None),
        ("ram_b_cs_n", ports["ram_b_cs_n"], 1, None),
        ("sclk", ports["sclk"], 0, None),
    )
    return _collect_expectations(wanted)


def _internal_busy(dut, top):
    """Return the engine ``busy`` handle for the level (never a truthiness test)."""
    handle = _optional(top, "qspi_busy")
    if handle is not None:
        return handle
    return _optional(dut, "busy")


def _collect_expectations(wanted) -> "tuple[list[ResetExpectation], list[str]]":
    expectations = []
    missing = []
    for label, handle, expected, symbols in wanted:
        if handle is None:
            missing.append(label)
            continue
        expectations.append(
            ResetExpectation(
                label=label, handle=handle, expected=expected, symbols=symbols
            )
        )
    return expectations, missing


def start_arbitration_monitor(
    dut,
    *,
    strict: bool = False,
    level: "str | None" = None,
    name: str = "arbitration",
    log=None,
    **kwargs,
) -> ArbitrationMonitor:
    """Create and start the ``CHK-ARB-*`` / ``CHK-RST-*`` monitor for *dut*.

    Uses the wrapper alias set shared with :mod:`monitors.qspi` (``bus_*``,
    ``asic_*``, ``done``, ``bus_gnt``) plus the whole-vector ``uio_oe``, so one
    implementation serves L0, L1, and L2. Starts sampling immediately, so
    callers must invoke this before reset release
    (:func:`common.bringup.bring_up_top` already does).

    Returns the monitor even when handles are absent: rows the level cannot
    reach report ``na`` and rows whose named signal is missing report
    ``blocked`` with a reason, never a silent skip.
    """
    log = dut._log if log is None else log
    top, engine, controller = _scopes(dut)

    if level is None:
        level = "L0" if _optional(dut, "bus_flash_cs_n") is None else "L1"

    handles = {
        "clk": _optional(dut, "clk"),
        "rst_n": _optional(dut, "rst_n"),
        "uio_oe": _optional(dut, "uio_oe"),
        "bus_gnt": _optional(dut, "bus_gnt"),
        "done": _optional(dut, "done"),
        "bus_sck": _first(dut, "bus_sck", "sclk"),
        "bus_ram_a_cs_n": _optional(dut, "bus_ram_a_cs_n"),
        "bus_ram_b_cs_n": _optional(dut, "bus_ram_b_cs_n"),
        "asic_flash_cs_out": _optional(dut, "asic_flash_cs_out"),
        "asic_sck_out": _optional(dut, "asic_sck_out"),
        "asic_ram_a_cs_out": _optional(dut, "asic_ram_a_cs_out"),
        "asic_ram_a_cs_oe": _optional(dut, "asic_ram_a_cs_oe"),
        "asic_ram_b_cs_out": _optional(dut, "asic_ram_b_cs_out"),
        "asic_ram_b_cs_oe": _optional(dut, "asic_ram_b_cs_oe"),
        "sck_oe": _optional(dut, "asic_sck_oe"),
        "qspi_busy": _internal_busy(dut, top),
    }

    # Engine-side reset expectations come from the level's own port names; the
    # controller half exists only where sys_controller is in scope.
    if level == "L0":
        ports = {
            "busy": _optional(dut, "busy"),
            "rdata_valid": _optional(dut, "rdata_valid"),
            "wdata_next": _optional(dut, "wdata_next"),
            "ram_a_cs_n": _optional(dut, "ram_a_cs_n"),
            "ram_b_cs_n": _optional(dut, "ram_b_cs_n"),
            "sclk": _optional(dut, "sclk"),
        }
    else:
        ports = {
            "busy": _optional(top, "qspi_busy"),
            "rdata_valid": _optional(top, "qspi_rdata_valid"),
            "wdata_next": _optional(top, "qspi_wdata_next"),
            "ram_a_cs_n": _optional(top, "qspi_ram_a_cs_n"),
            "ram_b_cs_n": _optional(top, "qspi_ram_b_cs_n"),
            "sclk": _optional(top, "qspi_sclk"),
        }

    expectations, missing = _engine_expectations(engine, ports)
    if level != "L0":
        controller_wanted = (
            (
                "sys_controller.curr_state",
                _optional(controller, "curr_state"),
                SYS_CONTROL_IDLE,
                SYS_CONTROL_STATES,
            ),
            (
                "sys_controller.stalled_state",
                _optional(controller, "stalled_state"),
                SYS_CONTROL_IDLE,
                SYS_CONTROL_STATES,
            ),
            (
                "sys_controller.active_fetch_addr",
                _optional(controller, "active_fetch_addr"),
                0,
                None,
            ),
            (
                "sys_controller.active_fetch_device",
                _optional(controller, "active_fetch_device"),
                0,
                None,
            ),
            ("qspi_txn_valid", _optional(top, "qspi_txn_valid"), 0, None),
        )
        controller_expectations, controller_missing = _collect_expectations(
            controller_wanted
        )
        expectations += controller_expectations
        missing += controller_missing

    na = list(kwargs.pop("na", ()))
    blocked = dict(kwargs.pop("blocked", {}))
    if level == "L0":
        na += [check_id for check_id in L1_ONLY_CHECK_IDS if check_id not in na]
    if level == "L2":
        na += [check_id for check_id in HIERARCHY_CHECK_IDS if check_id not in na]
    if missing and CHK_RST_INTERNAL not in na:
        blocked[CHK_RST_INTERNAL] = (
            f"missing reset-state handles: {', '.join(missing)}"
        )

    monitor = ArbitrationMonitor(
        handles=handles,
        reset_expectations=expectations,
        level=level,
        name=name,
        visibility="L0-port" if level == "L0" else "top-observable",
        na=tuple(na),
        blocked=blocked,
        strict=strict,
        log=log,
        **kwargs,
    )
    monitor.start()
    return monitor


__all__ = [
    "ARBITRATION_CHECK_IDS",
    "ArbViolation",
    "ArbitrationMonitor",
    "CHK_ARB_GNT_NOT_BUSY",
    "CHK_ARB_GNT_OE",
    "CHK_ARB_GNT_QUIET",
    "CHK_ARB_PARK",
    "CHK_RST_INTERNAL",
    "CHK_RST_OE",
    "CHK_RST_STATUS",
    "HIERARCHY_CHECK_IDS",
    "L1_ONLY_CHECK_IDS",
    "ResetExpectation",
    "start_arbitration_monitor",
]
