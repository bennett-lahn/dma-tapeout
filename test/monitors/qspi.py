"""Pin-level QPI decoder and ``CHK-PIN-*`` monitors.

Relevant IDs: ``CHK-PIN-CS-MUTEX``, ``CHK-PIN-FLASH-HIGH``,
``CHK-PIN-ADDR23-ZERO``, ``CHK-PIN-KNOWN``, ``CHK-PIN-SIO-OWN``,
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

Two further pin catalog rows are disposed by the per-device PSRAM model until
:class:`QspiPinMonitor` independently pin-decodes them (M2 transaction log):

======================= ============ =====================================
``CHK-*``               model ``Q-*`` Condition disposed
======================= ============ =====================================
``CHK-PIN-ADDR23-ZERO`` ``Q-ADDR23`` wire address had ``A[23]`` set
``CHK-PIN-KNOWN``       ``Q-SIO-X``  SIO unresolved in a host-driven phase
======================= ============ =====================================

Use :func:`dispose_model_pin_checks` / :func:`assert_model_pin_disposition` so
every applicable L0/L1 run prints an explicit disposition (never a silent
skip). ``CHK-PIN-KNOWN`` via ``Q-SIO-X`` covers unresolved SIO (X, and Z once
the ``tb_top`` model plane stops replacing float with idle 0) during
host-driven phases. CE#/SCK known-value edges and float-during-drive remain
residuals for the full pin monitor (partially overlapped by
``CHK-PIN-SCK-PARK`` float-while-keeper).

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

from dataclasses import dataclass

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import First, ReadOnly

CHK_PIN_CS_MUTEX = "CHK-PIN-CS-MUTEX"
CHK_PIN_FLASH_HIGH = "CHK-PIN-FLASH-HIGH"
CHK_PIN_ADDR23_ZERO = "CHK-PIN-ADDR23-ZERO"
CHK_PIN_KNOWN = "CHK-PIN-KNOWN"
CHK_PIN_SIO_OWN = "CHK-PIN-SIO-OWN"
CHK_PIN_SCK_PARK = "CHK-PIN-SCK-PARK"

SHARED_BUS_CHECK_IDS = (
    CHK_PIN_CS_MUTEX,
    CHK_PIN_FLASH_HIGH,
    CHK_PIN_SIO_OWN,
    CHK_PIN_SCK_PARK,
)

# Catalog rows disposed by models.psram pin decode until QspiPinMonitor owns them.
MODEL_PIN_CHECK_IDS = (
    CHK_PIN_ADDR23_ZERO,
    CHK_PIN_KNOWN,
)

# Same pattern as SharedBusMonitor: a model or wrapper may report either ID.
MODEL_DISPOSE_VIA = {
    CHK_PIN_ADDR23_ZERO: "Q-ADDR23",
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

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_NA = "na"

_KNOWN_LEVEL = {"0": 0, "1": 1}


def _bits(handle) -> "list[int | None]":
    """Return LSB-first bit levels of *handle*; ``None`` where the bit is x/z."""
    text = str(handle.value).strip().lower()
    return [_KNOWN_LEVEL.get(char) for char in reversed(text)]


def _level(handle) -> "int | None":
    """Return the level of a 1-bit *handle*, or ``None`` while it holds x/z."""
    return _bits(handle)[0]


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
        same level still fails. MCU-versus-ASIC overlap belongs to the
        ``CHK-ARB-*`` grant rows and is only noted here.
        TODO(M2): move the MCU/ASIC note into monitors/arbitration.py once the
          grant and park checkers exist.
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
                "(CHK-ARB-* scope, not CHK-PIN-SIO-OWN)"
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


# -- Model disposition for ADDR23 / KNOWN (M1) -----------------------------


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
    """Dispose ``CHK-PIN-ADDR23-ZERO`` / ``CHK-PIN-KNOWN`` via model ``Q-*`` IDs.

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


class QspiPinMonitor:
    """Decode resolved CE#, SCK, SIO into normalized transaction records.

    M1 leaves full transaction export as a stub. ``CHK-PIN-ADDR23-ZERO`` and
    ``CHK-PIN-KNOWN`` are disposed through the PSRAM model's pin-decoded
    ``Q-ADDR23`` / ``Q-SIO-X`` records via :func:`dispose_model_pin_checks`
    until this class owns an independent decoder (M2 scoreboard path).
    """

    def __init__(self, dut) -> None:
        self.dut = dut

    async def run(self) -> None:
        """Background monitor coroutine; start before reset release.

        Raises:
            NotImplementedError: Full pin decode deferred to M2; see model dispose.
        """
        raise NotImplementedError(
            "QspiPinMonitor transaction decode is M2; "
            "CHK-PIN-ADDR23-ZERO / CHK-PIN-KNOWN dispose via "
            "dispose_model_pin_checks (Q-ADDR23 / Q-SIO-X)"
        )

    def transactions(self) -> list:
        """Return completed normalized records for scoreboard compare.

        Raises:
            NotImplementedError: Full pin decode deferred to M2.
        """
        raise NotImplementedError("M2 implements QSPI transaction log export")
