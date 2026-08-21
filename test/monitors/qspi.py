"""Pin-level QPI decoder and ``CHK-PIN-*`` monitors.

Relevant IDs: ``CHK-PIN-CS-MUTEX``, ``CHK-PIN-FLASH-HIGH``,
``CHK-PIN-ADDR23-ZERO`` (retired D35), ``CHK-PIN-KNOWN``, ``CHK-PIN-SIO-OWN``,
``CHK-PIN-SCK-PARK``, ``CHK-HS-OPCODE`` (wait-cycle count at pins).

:class:`SharedBusMonitor` owns the shared-bus ownership subset of that list and
the timing-venue IDs mirroring it in ``04-timing-in-sim.md``:

===================== =============== ===================================
``CHK-*``             ``Q-*``         Condition
===================== =============== ===================================
``CHK-PIN-CS-MUTEX``  ``Q-MUX``       both RAM CE# low at once
``CHK-PIN-FLASH-HIGH`` ``Q-MUX``      flash CS low while the ASIC owns the bus
``CHK-PIN-SIO-OWN``   ``Q-SIO-OWN``   two enabled drivers on one SIO net
``CHK-PIN-SCK-PARK``  ``Q-SCKIDLE``   SCK not parked low while all deselected
===================== =============== ===================================

:class:`QspiPinMonitor` owns ``CHK-PIN-KNOWN`` from its own CE#/SCK/SIO decode,
and exports the ordered observed transaction log the scoreboard compares
against the reference oracle:

======================= ============ =====================================
``CHK-*``               twin ``Q-*``  Condition
======================= ============ =====================================
``CHK-PIN-KNOWN``       ``Q-SIO-X``  CE#, SCK, or SIO unresolved where the
                                     protocol requires a value
======================= ============ =====================================

``CHK-PIN-ADDR23-ZERO`` / model ``Q-ADDR23`` are retired (D35): wire ``A[23]``
is don't-care and is masked to ``A[22:0]``.

The pin decode is deliberately independent of the PSRAM models: it reads the
physical bus aliases (``bus_sck``, ``bus_ram_*_cs_n``, ``bus_sio``) and never
consults a model's access log, so agreement between the two is evidence rather
than a tautology (``05-reference-model.md``, "Independence and review rules").

:func:`common.dispose.dispose_run` / :func:`dispose_pin_checks` prefer the pin
monitor when one ran (``via=pin``). :func:`dispose_model_pin_checks` /
:func:`assert_model_pin_disposition` remain the model-evidence fallback for
runs that do not start a pin monitor, via ``Q-SIO-X``. Either
way every applicable L0/L1 run prints an explicit disposition, never a silent
skip.

Coarse ``Q-CEM`` / ``Q-CPH`` CE# pulse and gap checks live in
:mod:`monitors.timing` (:func:`monitors.timing.start_ce_timing_monitor`), not
here, so ownership suites stay decoupled from AC thresholds.

Ownership is judged from output enables - ASIC ``uio_oe`` (or L0 ``sio_oe``) and
each model's ``sio_oe`` handle - never from the resolved net alone, so two
drivers holding the same value still fail. The resolved net is reported as
supporting evidence only. Do not watch the agent's bool ``oe`` convenience.

The monitor takes its pin map from the wrapper alias set (``bus_*``,
``asic_*``, ``host_*``, ``fault_*`` in ``test/tb/tb_top.sv``, ``tb_gl.sv``, and
``tb_engine.sv``), so one implementation serves L0, L1, and L2. Aliases that do
not exist at a level - flash CS and ``BUS_GNT`` at L0 - are simply absent and
their checks report ``na``.
"""

from dataclasses import dataclass, field

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import First, ReadOnly

from common.lifecycle import (
    PendingLedger,
    REASON_RESET,
    REASON_SCOPE,
    REASON_STOP,
    SEV_DIAGNOSTIC,
    SEV_FAIL,
)
from common.constants import (
    RESULT_BLOCKED,
    RESULT_FAIL,
    RESULT_NA,
    RESULT_PASS,
)
from reference.chain import (
    OBSERVED_READ,
    OBSERVED_WRITE,
    OPCODE_READ,
    OPCODE_WRITE,
    transaction,
)
from reference.constants import (
    ADDR_NIBBLES,
    CMD_NIBBLES,
    DIR_READ,
    DIR_WRITE,
    PTR_BIT23,
    Q_PHASE,
    READ_DUMMY_CYCLES,
)

CHK_PIN_CS_MUTEX = "CHK-PIN-CS-MUTEX"
CHK_PIN_FLASH_HIGH = "CHK-PIN-FLASH-HIGH"
CHK_PIN_ADDR23_ZERO = "CHK-PIN-ADDR23-ZERO"  # retired D35; kept for historical IDs
CHK_PIN_KNOWN = "CHK-PIN-KNOWN"
CHK_PIN_SIO_OWN = "CHK-PIN-SIO-OWN"
CHK_PIN_SCK_PARK = "CHK-PIN-SCK-PARK"

SHARED_BUS_CHECK_IDS = (
    CHK_PIN_CS_MUTEX,
    CHK_PIN_FLASH_HIGH,
    CHK_PIN_SIO_OWN,
    CHK_PIN_SCK_PARK,
)

# Catalog rows QspiPinMonitor decodes from the pins on its own.
PIN_MONITOR_CHECK_IDS = (
    CHK_PIN_KNOWN,
)

# Same rows, as reported by models.psram pin decode (fallback evidence).
MODEL_PIN_CHECK_IDS = PIN_MONITOR_CHECK_IDS

# Twin per-device model ID for the same condition. Same pattern as
# SharedBusMonitor: a run may report either name, so both are always printed.
MODEL_DISPOSE_VIA = {
    CHK_PIN_KNOWN: "Q-SIO-X",
}

# Timing-venue name for the same condition (04-timing-in-sim.md). A run may
# report either ID; every message prints both.
TIMING_ID = {
    CHK_PIN_CS_MUTEX: "Q-MUX",
    CHK_PIN_FLASH_HIGH: "Q-MUX",
    CHK_PIN_SIO_OWN: "Q-SIO-OWN",
    CHK_PIN_SCK_PARK: "Q-SCKIDLE",
}

# Driver classes for SIO ownership arbitration between actors.
DRIVER_ASIC = "asic"
DRIVER_DEVICE = "device"
DRIVER_MCU = "mcu"

_KNOWN_LEVEL = {"0": 0, "1": 1}


def _bits(handle) -> "list[int | None]":
    """Return LSB-first bit levels of *handle*; ``None`` where the bit is x/z."""
    text = str(handle.value).strip().lower()
    return [_KNOWN_LEVEL.get(char) for char in reversed(text)]


def _level(handle) -> "int | None":
    """Return the level of a 1-bit *handle*, or ``None`` while it holds x/z."""
    return _bits(handle)[0]


def _word(handle) -> "int | None":
    """Return the integer value of *handle*, or ``None`` if any bit is x/z."""
    value = 0
    for index, bit in enumerate(_bits(handle)):
        if bit is None:
            return None
        value |= bit << index
    return value


def _show(value: "int | None") -> str:
    return "x/z" if value is None else str(value)


def _now_ns() -> float:
    return float(get_sim_time(unit="ns"))


@dataclass(frozen=True)
class SioDriver:
    """One actor able to enable a driver on the four shared SIO nets.

    ``oe`` and ``value`` are 4-bit handles ordered SIO[3:0]; the wrapper alias
    already maps them out of the ``uio`` pin order.
    """

    name: str
    kind: str
    oe: object
    value: "object | None" = None

    def oe_bits(self) -> "list[int | None]":
        return _bits(self.oe)

    def value_bits(self) -> "list[int | None]":
        return [None] * 4 if self.value is None else _bits(self.value)


@dataclass(frozen=True)
class BusViolation:
    """One shared-bus ownership finding, timestamped at the settled event."""

    check_id: str
    timing_id: str
    time_ns: float
    detail: str
    reset_truncated: bool = False

    def __str__(self) -> str:
        prefix = "RESET-TRUNCATED " if self.reset_truncated else ""
        return (
            f"{prefix}{self.check_id} / {self.timing_id} "
            f"at {self.time_ns:.3f}ns: {self.detail}"
        )


class SharedBusMonitor:
    """Always-on ownership checker for the shared QSPI nets.

    The monitor wakes on any watched value change, samples in the read-only
    phase so a settled timestep is judged rather than an intermediate delta,
    and reports each condition once per entry into that condition.

    Findings observed while ``rst_n`` is low are classified ``RESET-TRUNCATED``
    per ``04-timing-in-sim.md`` instead of failing their ``Q-*`` ID: reset
    combinationally clears every shared ``uio_oe`` bit, so CS, SCK, and SIO are
    forced by documented reset behavior rather than by a protocol error. They
    are still recorded, in :attr:`reset_truncated`, and still need review.
    """

    def __init__(
        self,
        *,
        sck,
        ram_ce_n,
        sio_drivers,
        sio_bus=None,
        flash_cs=None,
        asic_flash_oe=None,
        asic_flash_out=None,
        bus_gnt=None,
        rst_n=None,
        sck_oe_handles=(),
        level: str = "L1",
        name: str = "shared-bus",
        strict: bool = False,
        max_events: int = 64,
        log=None,
    ) -> None:
        self._sck = sck
        self._ram_ce_n = list(ram_ce_n)
        self._drivers = list(sio_drivers)
        self._sio_bus = sio_bus
        self._flash_cs = flash_cs
        self._asic_flash_oe = asic_flash_oe
        self._asic_flash_out = asic_flash_out
        self._bus_gnt = bus_gnt
        self._rst_n = rst_n
        self._sck_oe_handles = tuple(sck_oe_handles)
        self._strict = strict
        self._max_events = max_events
        self._log = log

        self.level = level
        self.name = name
        self.violations: "list[str]" = []
        self.events: "list[BusViolation]" = []
        self.reset_truncated: "list[BusViolation]" = []
        self.notes: "list[str]" = []

        self._condition_active: "dict[object, bool]" = {}
        self._samples = 0
        self._suppressed = 0
        self._active = True

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Launch the background checker. Call before reset release."""
        self._active = True
        return cocotb.start_soon(self._run())

    def stop(self) -> None:
        """Disable this checker. Soft-stop so multi-test modules can re-attach
        without cancelling a background task into the next test's outcome."""
        self._active = False

    def clear(self) -> None:
        """Drop findings and latched condition state for a fresh window.

        Condition latches are dropped too, so a condition that was active
        before the clear is reported again on its next entry instead of being
        swallowed by the pre-clear edge.
        """
        self.events.clear()
        self.violations.clear()
        self.reset_truncated.clear()
        self.notes.clear()
        self._condition_active.clear()
        self._suppressed = 0

    async def _run(self) -> None:
        watched = self._watched_handles()
        while True:
            await First(*[handle.value_change for handle in watched])
            await ReadOnly()
            if self._active:
                self._evaluate()

    def _watched_handles(self) -> list:
        handles = [self._sck]
        handles += [handle for _, handle in self._ram_ce_n]
        handles += [driver.oe for driver in self._drivers]
        handles += list(self._sck_oe_handles)
        for optional in (
            self._sio_bus,
            self._flash_cs,
            self._asic_flash_oe,
            self._bus_gnt,
            self._rst_n,
        ):
            if optional is not None:
                handles.append(optional)
        return handles

    # -- reporting ---------------------------------------------------------

    def _report(self, check_id: str, key, active: bool, detail: str, in_reset: bool) -> None:
        """Record *check_id* once per false-to-true transition of *active*."""
        was_active = self._condition_active.get(key, False)
        self._condition_active[key] = active
        if not active or was_active:
            return

        event = BusViolation(
            check_id=check_id,
            timing_id=TIMING_ID[check_id],
            time_ns=_now_ns(),
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
            self._log.error("CHECKER FAIL id=%s level=%s %s", check_id, self.level, event)
        if self._strict:
            raise AssertionError(str(event))

    # -- checks ------------------------------------------------------------

    def _evaluate(self) -> None:
        self._samples += 1

        rst_n = 1 if self._rst_n is None else _level(self._rst_n)
        in_reset = rst_n != 1
        bus_gnt = 0 if self._bus_gnt is None else _level(self._bus_gnt)
        asic_owns_bus = bus_gnt == 0

        ce_levels = [(label, _level(handle)) for label, handle in self._ram_ce_n]
        flash_cs = None if self._flash_cs is None else _level(self._flash_cs)

        self._check_cs_mutex(ce_levels, in_reset)
        self._check_flash_high(flash_cs, asic_owns_bus, in_reset)
        self._check_sio_ownership(in_reset)
        self._check_sck_park(ce_levels, flash_cs, asic_owns_bus, in_reset)

    def _check_cs_mutex(self, ce_levels, in_reset: bool) -> None:
        """``CHK-PIN-CS-MUTEX`` / ``Q-MUX``: at most one RAM CE# low."""
        selected = [label for label, level in ce_levels if level == 0]
        levels = ", ".join(f"{label}={_show(level)}" for label, level in ce_levels)
        self._report(
            CHK_PIN_CS_MUTEX,
            "cs-mutex",
            len(selected) > 1,
            f"{' and '.join(selected)} CE# low together ({levels})",
            in_reset,
        )

    def _check_flash_high(self, flash_cs, asic_owns_bus: bool, in_reset: bool) -> None:
        """``CHK-PIN-FLASH-HIGH`` / ``Q-MUX``: ASIC never selects flash.

        Two ways to fail: the resolved flash CS net is low while the ASIC owns
        or parks the bus (``~BUS_GNT``), or the ASIC drives flash CS low at any
        time. Flash CS low under ``BUS_GNT`` is legal MCU pass-through.
        """
        if self._flash_cs is None:
            return

        asic_oe = None if self._asic_flash_oe is None else _level(self._asic_flash_oe)
        asic_out = None if self._asic_flash_out is None else _level(self._asic_flash_out)
        net_low = asic_owns_bus and flash_cs == 0
        drive_low = asic_oe == 1 and asic_out == 0

        if drive_low:
            detail = f"ASIC drives flash CS low (uio_oe[0]=1, uio_out[0]=0, net={_show(flash_cs)})"
        else:
            detail = f"flash CS low while ~BUS_GNT (net={_show(flash_cs)})"

        self._report(CHK_PIN_FLASH_HIGH, "flash-high", net_low or drive_low, detail, in_reset)

    def _check_sio_ownership(self, in_reset: bool) -> None:
        """``CHK-PIN-SIO-OWN`` / ``Q-SIO-OWN``: one enabled SIO driver per net.

        Judged from output enables, so an overlap where both drivers hold the
        same level still fails. MCU-versus-ASIC overlap is only noted here; that
        row is owned by ``CHK-ARB-GNT-OE`` in
        :class:`monitors.arbitration.ArbitrationMonitor`, which judges the ASIC's
        whole ``uio_oe`` against ``BUS_GNT``.
        """
        oe_by_driver = [(driver, driver.oe_bits(), driver.value_bits()) for driver in self._drivers]
        bus_levels = [None] * 4 if self._sio_bus is None else _bits(self._sio_bus)

        for index in range(4):
            enabled = [
                (driver, values[index])
                for driver, oe_bits, values in oe_by_driver
                if oe_bits[index] == 1
            ]
            devices = [entry for entry in enabled if entry[0].kind == DRIVER_DEVICE]
            asic = [entry for entry in enabled if entry[0].kind == DRIVER_ASIC]

            contention = bool(len(devices) > 1 or (devices and asic))
            self._note_non_device_overlap(index, enabled, contention)

            drivers_text = ", ".join(
                f"{driver.name}(oe=1,value={_show(value)})" for driver, value in enabled
            )
            equal = len({value for _, value in enabled}) == 1 and len(enabled) > 1
            detail = (
                f"SIO{index} has {len(enabled)} enabled drivers: {drivers_text}; "
                f"bus={_show(bus_levels[index])}"
                f"{'; equal driven values still fail' if equal else ''}"
            )
            self._report(CHK_PIN_SIO_OWN, ("sio-own", index), contention, detail, in_reset)

    def _note_non_device_overlap(self, index: int, enabled, contention: bool) -> None:
        """Record ASIC-versus-MCU SIO overlap once per entry; not this ID's scope."""
        key = ("sio-note", index)
        active = len(enabled) > 1 and not contention
        was_active = self._condition_active.get(key, False)
        self._condition_active[key] = active
        if active and not was_active:
            names = " + ".join(driver.name for driver, _ in enabled)
            self.notes.append(
                f"{self.name} SIO{index} driven by {names} at {_now_ns():.3f}ns "
                "(CHK-ARB-GNT-OE scope, not CHK-PIN-SIO-OWN)"
            )

    def _sck_has_driver(self) -> bool:
        """True when any known actor enables drive on SCK (uio bit 3 / engine sclk)."""
        if not self._sck_oe_handles:
            return True  # unknown OE set: trust the resolved net alone
        for handle in self._sck_oe_handles:
            try:
                if int(handle.value) & 1:
                    return True
            except ValueError:
                return True
        return False

    def _check_sck_park(self, ce_levels, flash_cs, asic_owns_bus: bool, in_reset: bool) -> None:
        """``CHK-PIN-SCK-PARK`` / ``Q-SCKIDLE``: SCK low while all deselected.

        Deselected means every RAM CE# high plus, where the level has one,
        flash CS high. SCK high during that interval is an erroneous SCK cycle.
        SCK floating is a violation only while the ASIC is the bus keeper: the
        MCU may legally leave SCK high impedance in an idle gap under grant,
        and reset floats it by design.

        When no actor enables SCK drive, treat a stuck 1 as float: Verilator
        often retains the last driven level on undriven multi-assign nets while
        Icarus resolves them to Z.
        """
        deselected = all(level == 1 for _, level in ce_levels) and (
            self._flash_cs is None or flash_cs == 1
        )
        sck = _level(self._sck)
        if sck == 1 and not self._sck_has_driver():
            sck = None

        high_while_deselected = deselected and sck == 1
        float_while_keeper = deselected and sck is None and asic_owns_bus and not in_reset

        if high_while_deselected:
            detail = "SCK high while no device is selected (erroneous SCK cycle)"
        else:
            detail = "SCK unresolved while the ASIC is bus keeper and no device is selected"

        self._report(
            CHK_PIN_SCK_PARK,
            "sck-park",
            high_while_deselected or float_while_keeper,
            detail,
            in_reset,
        )

    # -- results -----------------------------------------------------------

    def counts(self) -> "dict[str, int]":
        """Return the violation count for every ID this monitor disposes."""
        counts = {check_id: 0 for check_id in SHARED_BUS_CHECK_IDS}
        for event in self.events:
            counts[event.check_id] += 1
        return counts

    def results(self) -> "dict[str, str]":
        """Return per-ID ``pass``/``fail``/``na`` disposition for this run."""
        counts = self.counts()
        dispositions = {}
        for check_id in SHARED_BUS_CHECK_IDS:
            if check_id == CHK_PIN_FLASH_HIGH and self._flash_cs is None:
                dispositions[check_id] = RESULT_NA
            else:
                dispositions[check_id] = RESULT_FAIL if counts[check_id] else RESULT_PASS
        return dispositions

    def violations_for(self, check_id: str) -> "list[BusViolation]":
        """Return recorded events for one catalog ID (negative-test helper)."""
        return [event for event in self.events if event.check_id == check_id]

    def review_reset_truncated(self) -> "list[BusViolation]":
        """Return ``RESET-TRUNCATED`` findings for explicit test dispose.

        These never appear in :attr:`violations` and must not be silently
        ignored: a ``Q-RST`` / ``TC-RESET-*`` test logs or asserts each event
        is an expected reset side-effect before treating the run as clean.
        """
        return list(self.reset_truncated)

    def summary(self) -> str:
        parts = [f"{check_id}={result}" for check_id, result in self.results().items()]
        if self.reset_truncated:
            parts.append(f"reset_truncated={len(self.reset_truncated)}")
        if self._suppressed:
            parts.append(f"suppressed={self._suppressed}")
        return f"{self.name} ({self.level}, {self._samples} samples): " + " ".join(parts)


# -- attachment ------------------------------------------------------------


def _optional(dut, name):
    return getattr(dut, name, None)


def sck_is_parked(dut) -> bool:
    """True when shared SCK is low or floating (no enabled SCK driver).

    Matches :meth:`SharedBusMonitor._check_sck_park` float handling so reset
    status checks agree with ownership on Verilator's sticky undriven nets.
    """
    sck = _optional(dut, "bus_sck")
    if sck is None:
        sck = _optional(dut, "sclk")
    level = None if sck is None else _level(sck)

    oe_handles = [
        handle
        for name in ("asic_sck_oe", "host_sck_oe", "fault_sck_oe")
        if (handle := _optional(dut, name)) is not None
    ]
    if oe_handles and level == 1:
        driven = False
        for handle in oe_handles:
            try:
                if int(handle.value) & 1:
                    driven = True
            except ValueError:
                driven = True
                break
        if not driven:
            level = None
    return level in (0, None)


def _first_attr(obj, names):
    for name in names:
        handle = getattr(obj, name, None)
        if handle is not None:
            return handle
    return None


def _agent_sio_driver(agent, fallback_name: str) -> SioDriver:
    """Build the SIO driver view of one PSRAM agent.

    Requires public cocotb handles ``sio_oe`` / ``sio_drive``. The bool
    convenience ``oe`` is not a handle and must not be watched for
    ``value_change``.
    """
    oe = getattr(agent, "sio_oe", None)
    value = getattr(agent, "sio_drive", None)
    if oe is None:
        raise AttributeError(
            f"{type(agent).__name__} exposes no public sio_oe handle; "
            "SharedBusMonitor judges ownership from OE handles, not bool oe "
            "or the resolved net"
        )

    memory = _first_attr(agent, ("memory", "_memory"))
    device_id = getattr(memory, "device_id", None)
    name = getattr(agent, "name", None) or (
        fallback_name if device_id is None else f"PSRAM{device_id}"
    )
    return SioDriver(name=name, kind=DRIVER_DEVICE, oe=oe, value=value)


def start_shared_bus_monitor(dut, *psram_agents, strict: bool = False, **kwargs):
    """Create and start the shared-bus ownership monitor for *dut*.

    Works against ``tb_top`` / ``tb_gl`` (pass both PSRAM agents) and
    ``tb_engine`` (pass the single attached agent), using the wrapper alias set
    each level provides. Returns the running :class:`SharedBusMonitor`; check
    ``monitor.violations`` at end of test, or pass ``strict=True`` to fail at
    the first violation.
    """
    drivers = [
        SioDriver(
            name="ASIC",
            kind=DRIVER_ASIC,
            oe=dut.asic_sio_oe,
            value=_optional(dut, "asic_sio_out"),
        )
    ]

    fault_oe = _optional(dut, "fault_sio_oe")
    if fault_oe is not None:
        drivers.append(
            SioDriver(
                name="ASIC-FAULT",
                kind=DRIVER_ASIC,
                oe=fault_oe,
                value=_optional(dut, "fault_sio_drive"),
            )
        )

    host_oe = _optional(dut, "host_sio_oe")
    if host_oe is not None:
        drivers.append(
            SioDriver(
                name="MCU",
                kind=DRIVER_MCU,
                oe=host_oe,
                value=_optional(dut, "host_sio_drive"),
            )
        )

    for position, agent in enumerate(psram_agents):
        drivers.append(_agent_sio_driver(agent, f"PSRAM{position}"))

    sck_oe_handles = []
    for name in ("asic_sck_oe", "host_sck_oe", "fault_sck_oe"):
        handle = _optional(dut, name)
        if handle is not None:
            sck_oe_handles.append(handle)

    monitor = SharedBusMonitor(
        sck=dut.bus_sck,
        ram_ce_n=(("PSRAM0", dut.bus_ram_a_cs_n), ("PSRAM1", dut.bus_ram_b_cs_n)),
        sio_drivers=drivers,
        sio_bus=_optional(dut, "bus_sio"),
        flash_cs=_optional(dut, "bus_flash_cs_n"),
        asic_flash_oe=_optional(dut, "asic_flash_cs_oe"),
        asic_flash_out=_optional(dut, "asic_flash_cs_out"),
        bus_gnt=_optional(dut, "bus_gnt"),
        rst_n=_optional(dut, "rst_n"),
        sck_oe_handles=sck_oe_handles,
        level=kwargs.pop("level", "L0" if _optional(dut, "bus_flash_cs_n") is None else "L1"),
        strict=strict,
        log=kwargs.pop("log", dut._log),
        **kwargs,
    )
    monitor.start()
    return monitor


# -- Model disposition for KNOWN (M1; ADDR23 retired by D35) ---------------


def _agent_violation_records(*devices_or_agents) -> list:
    """Flatten violation records from :class:`PsramDevice` or agent objects."""
    records = []
    for item in devices_or_agents:
        agent = getattr(item, "agent", item)
        violations = getattr(agent, "violations", None)
        if violations is None:
            continue
        records.extend(violations)
    return records


def dispose_model_pin_checks(*devices_or_agents, log=None) -> "dict[str, str]":
    """Dispose ``CHK-PIN-KNOWN`` via model ``Q-SIO-X``.

    Returns a per-ID ``pass``/``fail`` map and always prints each disposition so
    the catalog rows are never silently skipped. Count is the number of matching
    model violation records observed on the supplied agents.
    """
    records = _agent_violation_records(*devices_or_agents)
    codes = [record.code for record in records]
    results = {}
    parts = []
    for check_id in MODEL_PIN_CHECK_IDS:
        model_id = MODEL_DISPOSE_VIA[check_id]
        count = sum(1 for code in codes if code == model_id)
        result = RESULT_FAIL if count else RESULT_PASS
        results[check_id] = result
        parts.append(f"{check_id}={result} via={model_id} count={count}")
    summary = " ".join(parts)
    if log is not None:
        log.info("PIN-DISPOSE %s", summary)
    return results


def assert_model_pin_disposition(
    *devices_or_agents,
    log=None,
    expect_fail=(),
    test: str = "",
) -> "dict[str, str]":
    """Assert model pin dispositions match *expect_fail* (empty means both pass).

    *expect_fail* lists ``CHK-PIN-*`` IDs that must report ``fail`` (their model
    ``Q-*`` counterpart must have fired at least once). Every other model-pin
    catalog ID must report ``pass``.
    """
    expect_fail = set(expect_fail)
    unknown = expect_fail - set(MODEL_PIN_CHECK_IDS)
    if unknown:
        raise ValueError(f"unknown model-pin check IDs in expect_fail: {sorted(unknown)}")

    results = dispose_model_pin_checks(*devices_or_agents, log=log)
    prefix = f"{test}: " if test else ""
    for check_id, result in results.items():
        model_id = MODEL_DISPOSE_VIA[check_id]
        if check_id in expect_fail:
            assert result == RESULT_FAIL, (
                f"{prefix}{check_id} expected fail via {model_id}, observed pass "
                f"(no {model_id} records)"
            )
        else:
            assert result == RESULT_PASS, (
                f"{prefix}{check_id} expected pass via {model_id}, observed fail"
            )
    return results


# -- Independent pin decode (QspiPinMonitor) -------------------------------

ADDR23_BIT = PTR_BIT23

PIN_PHASE_IDLE = "IDLE"
PIN_PHASE_CMD = "CMD"
PIN_PHASE_ADDR = "ADDR"
PIN_PHASE_DUMMY = "DUMMY"
PIN_PHASE_DATA = "DATA"
PIN_PHASE_IGNORE = "IGNORE"

DIR_UNKNOWN = "unknown"

# Decode faults. They keep an interval out of the normal transaction log; the
# per-device model owns the matching Q-* catalog rows, so only CHK-PIN-KNOWN
# also raises a CHK-PIN-* event here (CHK-PIN-ADDR23-ZERO retired by D35).
FAULT_CMD_TRUNCATED = "truncated-command"
FAULT_ADDR_TRUNCATED = "truncated-address"
FAULT_OPCODE = "unsupported-opcode"
FAULT_DUMMY = "dummy-count"
FAULT_ODD_NIBBLE = "odd-data-nibble"
FAULT_ADDR23 = "addr23-set"  # historical; no longer raised (D35)
FAULT_SIO_X = "sio-unresolved"
FAULT_RESET = "reset-aborted"
FAULT_REFRAME = "ce-refell-while-active"

# Wrapper aliases the decoder needs; a missing name blocks both owned rows.
REQUIRED_PIN_SIGNALS = ("bus_sck", "bus_ram_a_cs_n", "bus_ram_b_cs_n", "bus_sio")

DEFAULT_NIBBLE_TRACE = 64


@dataclass
class PinTransaction:
    """One CE#-framed interval decoded from the resolved pins.

    Only wire facts: opcode, address, and payload come from SIO nibbles clocked
    by SCK, never from a request bus or a model access log. ``complete`` is true
    only for a well-formed interval, which is what :meth:`QspiPinMonitor.transactions`
    exports; every other interval stays a diagnostic event per
    ``05-reference-model.md`` ("Observed transaction completion").
    """

    device: int
    ce_fall_ns: float
    opcode: int = 0
    direction: str = DIR_UNKNOWN
    address: int = 0
    cmd_nibbles: int = 0
    addr_nibbles: int = 0
    dummy_cycles: int = 0
    data_nibbles: int = 0
    data: bytearray = field(default_factory=bytearray)
    nibbles: "list[int | None]" = field(default_factory=list)
    ce_rise_ns: "float | None" = None
    faults: "list[str]" = field(default_factory=list)
    aborted: bool = False
    complete: bool = False

    @property
    def kind(self) -> "str | None":
        """Neutral observed kind; ordered comparison resolves fetch versus data."""
        if self.direction == DIR_READ:
            return OBSERVED_READ
        if self.direction == DIR_WRITE:
            return OBSERVED_WRITE
        return None

    @property
    def length(self) -> int:
        return len(self.data)

    def nibble_trace(self) -> str:
        return " ".join("x" if value is None else f"{value:X}" for value in self.nibbles)

    def canonical(self) -> str:
        end = "?" if self.ce_rise_ns is None else f"{self.ce_rise_ns:.3f}"
        line = (
            f"dev={self.device} op={self.opcode:02X} {self.direction} "
            f"addr=0x{self.address:06X} len={self.length} "
            f"phases=cmd{self.cmd_nibbles}/addr{self.addr_nibbles}/"
            f"dummy{self.dummy_cycles}/data{self.data_nibbles} "
            f"ce={self.ce_fall_ns:.3f}..{end}ns"
        )
        if self.faults:
            line += f" faults={','.join(self.faults)}"
        return line

    def to_transaction(self, index: int):
        """Return the normalized :class:`reference.chain.Transaction` record.

        The kind stays neutral (``READ`` / ``WRITE``); ``Scoreboard.classify_observed``
        resolves it into ``FETCH_READ`` / ``DATA_READ`` / ``DATA_WRITE`` during
        ordered comparison, so no expected field ever leaks into the observed log.
        """
        if self.kind is None:
            raise ValueError(
                f"pin interval {self.canonical()} has no decoded direction and "
                "cannot become a normalized transaction record"
            )
        return transaction(
            index,
            self.kind,
            self.device,
            self.address,
            bytes(self.data),
            opcode=self.opcode,
            start_time_ns=self.ce_fall_ns,
            end_time_ns=self.ce_rise_ns,
            meta={
                "source": "pin",
                "dummy_cycles": self.dummy_cycles,
                "data_nibbles": self.data_nibbles,
                "nibbles": self.nibble_trace(),
            },
        )


class _PinDecoder:
    """CE#-framed QPI decoder for one device, driven by resolved-pin edges.

    Command, address, and write-data nibbles are sampled on rising SCK. Read
    data is sampled on rising SCK too: the device launches each nibble on the
    preceding falling edge, so the rising edge is the value the host actually
    captures, and the payload length matches what the host clocked in
    (``03-psram-model.md``, pin-level QPI grammar).
    """

    def __init__(self, monitor: "QspiPinMonitor", device: int, *, max_trace: int) -> None:
        self._monitor = monitor
        self._max_trace = max_trace
        self._high: "int | None" = None
        self.device = device
        self.phase = PIN_PHASE_IDLE
        self.txn: "PinTransaction | None" = None
        self._pending_token = None
        self._phase_token = None

    # -- framing -----------------------------------------------------------

    def begin(self) -> None:
        """Start an interval on CE# falling."""
        if self.txn is not None:
            interval = self.abort(FAULT_REFRAME)
            self._monitor._finish(interval)
            self._monitor._close_frame(self, interval)
        self.txn = PinTransaction(device=self.device, ce_fall_ns=_now_ns())
        self._pending_token = self._monitor.pending.open(
            "",
            severity=SEV_DIAGNOSTIC,
            detail=f"PSRAM{self.device} CE# frame ended before opcode promised completion",
            scope=self.device,
        )
        self.phase = PIN_PHASE_CMD
        self._high = None

    def end(self) -> "PinTransaction | None":
        """Close an interval on CE# rising and apply the termination rules."""
        txn = self._take()
        if txn is None:
            return None
        txn.ce_rise_ns = _now_ns()

        if txn.cmd_nibbles < CMD_NIBBLES:
            txn.faults.append(FAULT_CMD_TRUNCATED)
        elif txn.direction != DIR_UNKNOWN and txn.addr_nibbles < ADDR_NIBBLES:
            txn.faults.append(FAULT_ADDR_TRUNCATED)
        elif txn.direction == DIR_READ and FAULT_ADDR23 not in txn.faults:
            if txn.dummy_cycles != READ_DUMMY_CYCLES:
                txn.faults.append(FAULT_DUMMY)
        if txn.data_nibbles % 2:
            txn.faults.append(FAULT_ODD_NIBBLE)

        txn.complete = not txn.faults
        return txn

    def abort(self, reason: str) -> "PinTransaction | None":
        """Close an interval as an aborted diagnostic event, never a record."""
        txn = self._take()
        if txn is None:
            return None
        txn.ce_rise_ns = _now_ns()
        txn.aborted = True
        txn.faults.append(reason)
        txn.complete = False
        return txn

    def _take(self) -> "PinTransaction | None":
        txn = self.txn
        self.txn = None
        self.phase = PIN_PHASE_IDLE
        self._high = None
        return txn

    # -- nibble decode -----------------------------------------------------

    def on_sck_rise(self, raw: "int | None") -> None:
        """Consume one SCK rising edge while this device's CE# is low."""
        txn = self.txn
        if txn is None or self.phase == PIN_PHASE_IGNORE:
            return

        if self.phase == PIN_PHASE_DUMMY:
            txn.dummy_cycles += 1
            if txn.dummy_cycles >= READ_DUMMY_CYCLES:
                self.phase = PIN_PHASE_DATA
            return

        if len(txn.nibbles) < self._max_trace:
            txn.nibbles.append(raw)
        nibble = raw
        if nibble is None:
            self._note_unresolved()
            nibble = 0

        if self.phase == PIN_PHASE_CMD:
            txn.opcode = ((txn.opcode << 4) | nibble) & 0xFF
            txn.cmd_nibbles += 1
            if txn.cmd_nibbles >= CMD_NIBBLES:
                self._decode_opcode()
        elif self.phase == PIN_PHASE_ADDR:
            txn.address = ((txn.address << 4) | nibble) & 0xFFFFFF
            txn.addr_nibbles += 1
            if txn.addr_nibbles >= ADDR_NIBBLES:
                self._decode_address()
        elif self.phase == PIN_PHASE_DATA:
            txn.data_nibbles += 1
            if self._high is None:
                self._high = nibble  # upper nibble first, SIO3 is the nibble MSB
            else:
                txn.data.append(((self._high << 4) | nibble) & 0xFF)
                self._high = None

    def _decode_opcode(self) -> None:
        txn = self.txn
        if txn.opcode == OPCODE_READ:
            txn.direction = DIR_READ
        elif txn.opcode == OPCODE_WRITE:
            txn.direction = DIR_WRITE
        else:
            # Q-OPCODE is the model's row; here it only voids the record.
            txn.faults.append(FAULT_OPCODE)
            self.phase = PIN_PHASE_IGNORE
            return
        self._monitor.pending.resolve(self._pending_token)
        self._phase_token = self._monitor.pending.open(
            Q_PHASE,
            severity=SEV_FAIL,
            detail=(
                f"PSRAM{self.device} op=0x{txn.opcode:02X} promised command/address "
                "completion but CE# frame remained incomplete"
            ),
            scope=self.device,
        )
        self.phase = PIN_PHASE_ADDR

    def _decode_address(self) -> None:
        """Accept the six address nibbles; ``A[23]`` is don't-care (D35)."""
        txn = self.txn
        txn.address &= ~ADDR23_BIT
        self.phase = PIN_PHASE_DUMMY if txn.direction == DIR_READ else PIN_PHASE_DATA

    def _note_unresolved(self) -> None:
        """``CHK-PIN-KNOWN``: once per interval, on the first unresolved nibble."""
        txn = self.txn
        if FAULT_SIO_X in txn.faults:
            return
        txn.faults.append(FAULT_SIO_X)
        self._monitor._record(
            CHK_PIN_KNOWN,
            f"PSRAM{self.device}: SIO unresolved (x/z) on a rising SCK in the "
            f"{self.phase} phase, where the protocol requires a value "
            f"(nibbles so far: {txn.nibble_trace()})",
        )


class QspiPinMonitor:
    """Decode resolved CE#, SCK, and SIO into normalized transaction records.

    Two jobs, both from pins only:

    1. export the ordered observed transaction log the dual-axis scoreboard
       compares against the reference oracle, and
    2. dispose ``CHK-PIN-KNOWN``.

    The monitor wakes on any watched pin change and samples in the read-only
    phase, so a settled timestep is decoded rather than an intermediate delta.
    One external CE# assertion is one interval. A malformed or reset-aborted
    interval is retained as a diagnostic event in :attr:`intervals` and is never
    rewritten into a normal record.

    Findings observed while ``rst_n`` is low are classified ``RESET-TRUNCATED``
    exactly as in :class:`SharedBusMonitor`, and the interval live at that point
    is closed as aborted. The decoder re-arms once ``rst_n`` is high again and
    both CE# levels are resolved, so pre-reset X on the engine's CS registers is
    not reported as a protocol failure.

    Ownership stays with :class:`SharedBusMonitor`: this monitor judges decoded
    values, not who drove them.
    """

    def __init__(
        self,
        *,
        sck=None,
        ram_ce_n=(),
        sio=None,
        rst_n=None,
        level: str = "L1",
        name: str = "qspi-pins",
        visibility: str = "top-observable",
        strict: bool = False,
        max_events: int = 64,
        max_nibble_trace: int = DEFAULT_NIBBLE_TRACE,
        missing: "tuple[str, ...]" = (),
        blocked_reason: str = "",
        log=None,
    ) -> None:
        self._sck = sck
        self._ram_ce_n = tuple(ram_ce_n)
        self._sio = sio
        self._rst_n = rst_n
        self._strict = strict
        self._max_events = max_events
        self._log = log

        self.level = level
        self.name = name
        self.visibility = visibility
        self.missing = tuple(missing)
        self.blocked_reason = blocked_reason or (
            f"missing handles: {', '.join(self.missing)}" if self.missing else ""
        )

        self.violations: "list[str]" = []
        self.events: "list[BusViolation]" = []
        self.reset_truncated: "list[BusViolation]" = []
        self.intervals: "list[PinTransaction]" = []

        self._decoders = {
            device: _PinDecoder(self, device, max_trace=max_nibble_trace)
            for device, _ in self._ram_ce_n
        }
        self._condition_active: "dict[object, bool]" = {}
        self._prev_ce: "dict[int, int | None]" = {}
        self._prev_sck: "int | None" = None
        self._in_reset = False
        self._armed = False
        self._samples = 0
        self._suppressed = 0
        self._active = False
        self._task = None
        self.notes: "list[str]" = []
        self.pending = PendingLedger(
            owner=self.name,
            record=self._record_pending,
            in_reset=lambda: self._in_reset,
            now_ns=_now_ns,
        )

    # -- lifecycle ---------------------------------------------------------

    @property
    def blocked(self) -> bool:
        """True when a required alias was absent, so no row could be judged."""
        return bool(self.missing)

    def start(self):
        """Launch the background decoder. Call before reset release."""
        if self.blocked:
            if self._log is not None:
                self._log.warning(
                    "CHECKER BLOCKED ids=%s level=%s reason=%s",
                    ",".join(PIN_MONITOR_CHECK_IDS),
                    self.level,
                    self.blocked_reason,
                )
            return None
        self._active = True
        self._task = cocotb.start_soon(self._run())
        return self._task

    def stop(self) -> None:
        """Soft-stop so a later test in the same module can re-attach."""
        self.pending.audit(reason=REASON_STOP)
        self._active = False

    def clear(self) -> None:
        """Drop findings, decoded intervals, and latched condition state.

        A directed test that opens a fresh scoreboard epoch calls this first, so
        the exported log holds only that epoch's transactions.
        """
        self.events.clear()
        self.violations.clear()
        self.reset_truncated.clear()
        self.intervals.clear()
        self._condition_active.clear()
        self._suppressed = 0
        for decoder in self._decoders.values():
            decoder.abort(FAULT_REFRAME)
        self.pending.clear()

    async def _run(self) -> None:
        watched = self._watched_handles()
        while True:
            await First(*[handle.value_change for handle in watched])
            await ReadOnly()
            if self._active:
                self._evaluate()

    def _watched_handles(self) -> list:
        handles = [self._sck, self._sio]
        handles += [handle for _, handle in self._ram_ce_n]
        if self._rst_n is not None:
            handles.append(self._rst_n)
        return handles

    # -- reporting ---------------------------------------------------------

    def _record(self, check_id: str, detail: str) -> None:
        """Record one finding, classifying it ``RESET-TRUNCATED`` while in reset."""
        event = BusViolation(
            check_id=check_id,
            timing_id=MODEL_DISPOSE_VIA[check_id],
            time_ns=_now_ns(),
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
            self._log.error("CHECKER FAIL id=%s level=%s %s", check_id, self.level, event)
        if self._strict:
            raise AssertionError(str(event))

    def _record_pending(
        self, check_id: str, detail: str, *, reset_truncated: bool
    ):
        if detail.startswith("incomplete-window "):
            note = f"{self.name} {detail}"
            self.notes.append(note)
            return note
        event = BusViolation(
            check_id=check_id,
            timing_id=check_id,
            time_ns=_now_ns(),
            detail=detail,
            reset_truncated=reset_truncated,
        )
        if reset_truncated:
            self.reset_truncated.append(event)
        else:
            self.events.append(event)
            self.violations.append(f"{self.name} {event}")
        return event

    def _latch(self, check_id: str, key, active: bool, detail: str) -> None:
        """Record a level condition once per false-to-true transition."""
        was_active = self._condition_active.get(key, False)
        self._condition_active[key] = active
        if active and not was_active:
            self._record(check_id, detail)

    def _finish(self, interval: "PinTransaction | None") -> None:
        if interval is not None:
            self.intervals.append(interval)

    def _close_frame(self, decoder: _PinDecoder, interval: "PinTransaction | None") -> None:
        if (
            interval is not None
            and interval.cmd_nibbles >= CMD_NIBBLES
            and interval.direction != DIR_UNKNOWN
            and interval.addr_nibbles >= ADDR_NIBBLES
        ):
            self.pending.resolve(decoder._phase_token)
        self.pending.close_scope(decoder.device, reason=REASON_SCOPE)
        decoder._pending_token = None
        decoder._phase_token = None

    # -- decode ------------------------------------------------------------

    def _evaluate(self) -> None:
        self._samples += 1

        rst_n = 1 if self._rst_n is None else _level(self._rst_n)
        self._in_reset = rst_n != 1
        ce_levels = {device: _level(handle) for device, handle in self._ram_ce_n}
        sck = _level(self._sck)

        if self._in_reset:
            self.pending.audit(reason=REASON_RESET)
            self._abort_active(FAULT_RESET)
            self._armed = False
            self._condition_active.clear()
        elif not self._armed:
            # Re-arm only once every CE# is resolved: reset releases the shared
            # OEs combinationally and the engine's CS registers settle high on
            # the first sampled reset edge, so an X here is bring-up, not fault.
            self._armed = all(level is not None for level in ce_levels.values())
        else:
            self._check_known_levels(ce_levels, sck)
            self._track_frames(ce_levels)
            self._track_clock(ce_levels, sck)

        self._prev_ce = ce_levels
        self._prev_sck = sck

    def _abort_active(self, reason: str) -> None:
        for decoder in self._decoders.values():
            interval = decoder.abort(reason)
            self._finish(interval)
            self._close_frame(decoder, interval)

    def _check_known_levels(self, ce_levels, sck) -> None:
        """``CHK-PIN-KNOWN`` for the framing pins the protocol always needs."""
        for device, level in ce_levels.items():
            self._latch(
                CHK_PIN_KNOWN,
                ("ce", device),
                level is None,
                f"PSRAM{device} CE# unresolved (x/z) while rst_n=1; CE# frames every "
                "transaction and must always hold a value",
            )

        selected = sorted(device for device, level in ce_levels.items() if level == 0)
        names = ", ".join(f"PSRAM{device}" for device in selected)
        self._latch(
            CHK_PIN_KNOWN,
            "sck",
            bool(selected) and sck is None,
            f"SCK unresolved (x/z) while {names} is selected; the clock must hold a "
            "value for the whole CE#-low interval",
        )

    def _track_frames(self, ce_levels) -> None:
        for device, level in ce_levels.items():
            previous = self._prev_ce.get(device)
            decoder = self._decoders[device]
            if previous != 0 and level == 0:
                decoder.begin()
            elif previous == 0 and level != 0:
                interval = decoder.end()
                self._finish(interval)
                self._close_frame(decoder, interval)

    def _track_clock(self, ce_levels, sck) -> None:
        if sck is None or self._prev_sck is None or sck == self._prev_sck:
            return
        if sck != 1:
            return  # falling edge launches read data; the host captures on rising
        selected = [device for device, level in ce_levels.items() if level == 0]
        if not selected:
            return  # CHK-PIN-SCK-PARK owns clocking while every device is high
        nibble = _word(self._sio)
        for device in selected:
            self._decoders[device].on_sck_rise(nibble)

    # -- results -----------------------------------------------------------

    def transactions(self) -> tuple:
        """Return the ordered observed log for :class:`reference.scoreboard.Scoreboard`.

        Only well-formed completed intervals appear, re-indexed from zero, with
        neutral ``READ`` / ``WRITE`` kinds. Malformed and aborted intervals stay
        in :attr:`intervals` as diagnostic events.
        """
        records = []
        for interval in self.intervals:
            if interval.complete and interval.kind is not None:
                records.append(interval.to_transaction(len(records)))
        return tuple(records)

    def completed(self) -> "list[PinTransaction]":
        """Return the raw pin intervals behind :meth:`transactions`."""
        return [
            interval
            for interval in self.intervals
            if interval.complete and interval.kind is not None
        ]

    def malformed(self) -> "list[PinTransaction]":
        """Return decoded intervals kept out of the log, aborted ones included."""
        return [interval for interval in self.intervals if not interval.complete]

    def completed_before(self, time_ns: float) -> int:
        """Return how many exported records closed at or before *time_ns*.

        ``TC-RESET-ACTIVE`` passes this as ``reset_index`` to
        :meth:`reference.scoreboard.Scoreboard.compare_reset_prefix`, using the
        timestamp of the first rising ``clk`` sampled with ``rst_n=0``.
        """
        return sum(
            1
            for interval in self.completed()
            if interval.ce_rise_ns is not None and interval.ce_rise_ns <= time_ns
        )

    def counts(self) -> "dict[str, int]":
        """Return the ordinary violation count for every ID this monitor owns."""
        counts = {check_id: 0 for check_id in PIN_MONITOR_CHECK_IDS}
        for event in self.events:
            counts[event.check_id] += 1
        return counts

    def results(self) -> "dict[str, str]":
        """Return per-ID ``pass`` / ``fail`` / ``blocked`` disposition."""
        if self.blocked:
            return {check_id: RESULT_BLOCKED for check_id in PIN_MONITOR_CHECK_IDS}
        counts = self.counts()
        return {
            check_id: RESULT_FAIL if counts[check_id] else RESULT_PASS
            for check_id in PIN_MONITOR_CHECK_IDS
        }

    def blocked_reasons(self) -> "dict[str, str]":
        """Return the reason string behind every ``blocked`` row."""
        if not self.blocked:
            return {}
        return {check_id: self.blocked_reason for check_id in PIN_MONITOR_CHECK_IDS}

    def violations_for(self, check_id: str) -> "list[BusViolation]":
        """Return recorded events for one catalog ID (negative-test helper)."""
        return [event for event in self.events if event.check_id == check_id]

    def review_reset_truncated(self) -> "list[BusViolation]":
        """Return ``RESET-TRUNCATED`` findings for explicit test dispose."""
        return list(self.reset_truncated)

    def log_text(self) -> str:
        """Render every decoded interval, one canonical line each."""
        return "\n".join(interval.canonical() for interval in self.intervals)

    def summary(self) -> str:
        parts = [f"{check_id}={result}" for check_id, result in self.results().items()]
        malformed = len(self.malformed())
        if malformed:
            parts.append(f"malformed={malformed}")
        if self.reset_truncated:
            parts.append(f"reset_truncated={len(self.reset_truncated)}")
        if self._suppressed:
            parts.append(f"suppressed={self._suppressed}")
        return (
            f"{self.name} ({self.level}, {self.visibility}, {self._samples} samples, "
            f"{len(self.completed())} txn): " + " ".join(parts)
        )


def start_qspi_pin_monitor(
    dut,
    *,
    strict: bool = False,
    level: "str | None" = None,
    name: str = "qspi-pins",
    log=None,
    **kwargs,
) -> QspiPinMonitor:
    """Create and start the independent pin decoder for *dut*.

    Uses the same wrapper alias set as :func:`start_shared_bus_monitor`, so one
    implementation serves L0, L1, and L2. Decoding reads the physical bus nets
    (``bus_*``), not the models' resolved plane, so a floating SIO in a phase
    that requires a value is visible as ``CHK-PIN-KNOWN``.

    Returns the monitor even when an alias is missing; the returned object then
    reports both owned rows ``blocked`` with a reason instead of vanishing from
    the run's disposition table.
    """
    log = dut._log if log is None else log
    handles = {name_: _optional(dut, name_) for name_ in REQUIRED_PIN_SIGNALS}
    if handles["bus_sck"] is None:
        handles["bus_sck"] = _optional(dut, "sclk")
    missing = tuple(name_ for name_, handle in handles.items() if handle is None)

    if level is None:
        level = "L0" if _optional(dut, "bus_flash_cs_n") is None else "L1"

    ram_ce_n = (
        ()
        if missing
        else ((0, handles["bus_ram_a_cs_n"]), (1, handles["bus_ram_b_cs_n"]))
    )

    monitor = QspiPinMonitor(
        sck=handles["bus_sck"],
        ram_ce_n=ram_ce_n,
        sio=handles["bus_sio"],
        rst_n=_optional(dut, "rst_n"),
        level=level,
        name=name,
        visibility="L0-port" if level == "L0" else "top-observable",
        strict=strict,
        missing=missing,
        log=log,
        **kwargs,
    )
    monitor.start()
    return monitor


def dispose_pin_checks(*sources, log=None) -> "dict[str, str]":
    """Dispose ``CHK-PIN-KNOWN`` from the best evidence.

    *sources* may mix :class:`QspiPinMonitor` instances with PSRAM devices or
    agents. A started pin monitor is authoritative because it decodes the pins
    independently; the per-device model ``Q-SIO-X`` records are the fallback
    for a run that started no pin monitor (or whose monitor is ``blocked``).
    The printed line always names which evidence was used.

    Raises:
        ValueError: no usable evidence source was supplied, which would
            otherwise report a silent pass.
    """
    monitors = [item for item in sources if isinstance(item, QspiPinMonitor)]
    usable = [monitor for monitor in monitors if not monitor.blocked]
    others = [item for item in sources if not isinstance(item, QspiPinMonitor)]
    records = _agent_violation_records(*others)

    if not usable and not others:
        raise ValueError(
            "dispose_pin_checks needs a started QspiPinMonitor or a PSRAM "
            "device/agent; with no evidence source both rows would report a "
            "silent pass"
        )

    codes = [record.code for record in records]
    results = {}
    parts = []
    for check_id in PIN_MONITOR_CHECK_IDS:
        if usable:
            count = sum(monitor.counts()[check_id] for monitor in usable)
            via = "pin"
        else:
            via = MODEL_DISPOSE_VIA[check_id]
            count = sum(1 for code in codes if code == via)
        result = RESULT_FAIL if count else RESULT_PASS
        results[check_id] = result
        parts.append(f"{check_id}={result} via={via} count={count}")

    if log is not None:
        log.info("PIN-DISPOSE %s", " ".join(parts))
    return results
