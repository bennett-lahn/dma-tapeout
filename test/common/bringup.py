"""Shared L0/L1 bring-up: agent lifecycle, reset, model attach, monitors.

One helper per DUT level replaces the copied ``_ATTACHED`` / ``_bring_up``
patterns that grew during M1. Every bring-up performs the same ordered steps so
a test cannot accidentally skip one:

1. stop the agents and monitors of the previous bring-up in this run,
2. park stimulus and clear the wrapper's host / fault injectors,
3. attach the PSRAM model(s) for the level,
4. start the always-on monitors (all non-strict by default), and
5. start ``clk`` and apply the level's synchronous active-low reset.

Monitors are constructed **before** reset release, as
``docs/llm/verification/06-checkers.md`` requires. Backdoor memory preload may
happen either side of the call; the models never observe it on the bus.

Public API (frozen for M2):

* :func:`bring_up_engine` - L0 ``tb_engine``
* :func:`bring_up_top` - L1 ``tb_top`` / L2 ``tb_gl``
* :func:`bring_up` - dispatch on ``DUT_LEVEL`` (or the wrapper's own handles)
* :class:`BringUp` - returned handle bundle (devices + monitors + lifecycle)

The started ``pin`` monitor (:class:`monitors.qspi.QspiPinMonitor`) is the
authoritative source for ``CHK-PIN-ADDR23-ZERO`` / ``CHK-PIN-KNOWN`` and for
the ordered observed transaction log a scoreboard compares.
:func:`bring_up_top` (L1) defaults ``pin_monitor=True``; :func:`bring_up_engine`
(L0) defaults ``pin_monitor=False`` so M1 engine suites are not surprised by an
extra pin decoder. Pass ``pin_monitor=True`` at L0 when a test wants pin
evidence. :func:`common.dispose.dispose_run` prefers that pin evidence and
falls back to the per-device model ``Q-*`` twins when the monitor is blocked or
absent.

Dispose the resulting monitors and model logs with :mod:`common.dispose`.
"""

from dataclasses import dataclass, field

from common.clocks import apply_engine_reset, apply_reset, start_clock
from common.config import parse_run_config
from models.psram import (
    PsramDevice,
    ViolationLog,
    attach_dual_psram,
    attach_engine_psram,
)
from monitors.arbitration import ArbitrationMonitor, start_arbitration_monitor
from monitors.handshake import (
    ControllerMonitor,
    HandshakeMonitor,
    start_controller_monitor,
    start_handshake_monitor,
)
from monitors.qspi import (
    QspiPinMonitor,
    SharedBusMonitor,
    start_qspi_pin_monitor,
    start_shared_bus_monitor,
)
from monitors.timing import CeTimingMonitor, start_ce_timing_monitor

LEVEL_L0 = "L0"
LEVEL_L1 = "L1"
LEVEL_L2 = "L2"

DEFAULT_CLOCK_PERIOD_NS = 10
DEFAULT_RESET_CYCLES = 5

# Bring-ups started in this simulation, newest last. A new bring-up stops the
# previous one so only one model per device ever drives the shared SIO handles.
_HISTORY: "list[BringUp]" = []


@dataclass
class BringUp:
    """Attached models and always-on monitors for one test window."""

    dut: object
    level: str
    devices: "tuple[PsramDevice, ...]"
    violations: "ViolationLog | None" = None
    bus: "SharedBusMonitor | None" = None
    ce: "CeTimingMonitor | None" = None
    handshake: "HandshakeMonitor | None" = None
    pin: "QspiPinMonitor | None" = None
    arbitration: "ArbitrationMonitor | None" = None
    controller: "ControllerMonitor | None" = None
    notes: "list[str]" = field(default_factory=list)

    # -- device access -----------------------------------------------------

    @property
    def psram0(self) -> PsramDevice:
        return self.device(0)

    @property
    def psram1(self) -> PsramDevice:
        return self.device(1)

    def device(self, device_id: int) -> PsramDevice:
        """Return the attached :class:`PsramDevice` with *device_id*."""
        for device in self.devices:
            if device.device_id == device_id:
                return device
        raise KeyError(
            f"PSRAM{device_id} is not attached in this bring-up "
            f"(attached: {[d.device_id for d in self.devices]})"
        )

    @property
    def agents(self) -> tuple:
        return tuple(device.agent for device in self.devices)

    @property
    def monitors(self) -> tuple:
        """Always-on monitors :mod:`common.dispose` expands a :class:`BringUp` into."""
        return tuple(
            monitor
            for monitor in (
                self.bus,
                self.ce,
                self.handshake,
                self.pin,
                self.arbitration,
                self.controller,
            )
            if monitor is not None
        )

    def __iter__(self):
        """Unpack as the attached devices, e.g. ``psram0, psram1 = bringup``."""
        return iter(self.devices)

    # -- lifecycle ---------------------------------------------------------

    def clear(self) -> None:
        """Drop recorded findings and history for a fresh directed window.

        Model transaction logs are kept: a test that wants a clean transaction
        window clears them itself, so an accidental clear cannot erase the
        evidence a scoreboard is about to compare.
        """
        for monitor in self.monitors:
            monitor.clear()
        for agent in self.agents:
            agent.violations.clear()

    def clear_transactions(self) -> None:
        """Drop model transaction logs (directed multi-window tests)."""
        for agent in self.agents:
            agent.transactions.clear()

    def stop(self) -> None:
        """Stop model agents and soft-stop every monitor."""
        for device in self.devices:
            if device.agent is not None:
                device.agent.stop()
        for monitor in self.monitors:
            monitor.stop()


def _stop_previous() -> None:
    for bringup in _HISTORY:
        bringup.stop()
    _HISTORY.clear()


def _park(handle, value: int) -> None:
    if handle is not None:
        handle.value = value


def _optional(dut, name):
    try:
        return getattr(dut, name)
    except AttributeError:
        return None


def _start_monitors(
    dut,
    bringup: BringUp,
    *,
    bus_monitor: bool,
    ce_monitor: bool,
    handshake_monitor: bool,
    pin_monitor: bool,
    arbitration_monitor: bool,
    controller_monitor: bool,
    strict_monitors: bool,
    log,
) -> None:
    """Start the always-on monitors before reset release.

    The pin decoder starts first because two catalog groups take their pin half
    from it: ``CHK-HS-OPCODE`` wait cycles and the ``CHK-CTRL-FETCH-HEAD`` /
    ``CHK-CTRL-DATA-PAIR`` sequence rows. Passing it in at construction keeps
    those rows out of ``blocked`` without any test-side wiring.
    """
    if bus_monitor:
        bringup.bus = start_shared_bus_monitor(
            dut, *bringup.agents, strict=strict_monitors, log=log
        )
    if ce_monitor:
        bringup.ce = start_ce_timing_monitor(dut, strict=strict_monitors, log=log)
    if pin_monitor:
        bringup.pin = start_qspi_pin_monitor(dut, strict=strict_monitors, log=log)
        if bringup.pin.blocked:
            bringup.notes.append(
                f"pin monitor blocked: {bringup.pin.blocked_reason}"
            )
    if handshake_monitor:
        bringup.handshake = start_handshake_monitor(
            dut, strict=strict_monitors, pin=bringup.pin, log=log
        )
        if bringup.handshake.blocked:
            bringup.notes.append(
                f"handshake monitor blocked: {bringup.handshake.blocked_reason}"
            )
    if arbitration_monitor:
        bringup.arbitration = start_arbitration_monitor(
            dut, strict=strict_monitors, level=bringup.level, log=log
        )
        for check_id, reason in sorted(bringup.arbitration.blocked.items()):
            bringup.notes.append(f"{check_id} blocked: {reason}")
    if controller_monitor:
        bringup.controller = start_controller_monitor(
            dut, strict=strict_monitors, level=bringup.level, pin=bringup.pin, log=log
        )
        for check_id in bringup.controller.blocked_rows:
            reason = bringup.controller.blocked_reasons().get(check_id, "")
            bringup.notes.append(f"{check_id} blocked: {reason}")


async def bring_up_engine(
    dut,
    *,
    devices=(0, 1),
    fill: int = 0x00,
    seed: "int | None" = None,
    violations: "ViolationLog | None" = None,
    strict_models: bool = False,
    strict_monitors: bool = False,
    bus_monitor: bool = True,
    ce_monitor: bool = False,
    handshake_monitor: bool = True,
    pin_monitor: bool = False,
    arbitration_monitor: bool = True,
    controller_monitor: bool = True,
    clock_period_ns: int = DEFAULT_CLOCK_PERIOD_NS,
    reset_cycles: int = DEFAULT_RESET_CYCLES,
    log=None,
) -> BringUp:
    """Bring up L0 ``tb_engine``: park stimulus, attach models, reset.

    *devices* selects which PSRAM instances attach (``0``, ``1``, or an iterable
    of those ids), matching :func:`models.psram.attach_engine_psram`.

    ``ce_monitor`` defaults to off at L0: back-to-back directed engine
    transactions legitimately sit near ``tCPH``, and the CE# AC thresholds are a
    board-level claim, not an engine-port one. Pass ``ce_monitor=True`` when a
    test means to judge them.

    ``pin_monitor`` defaults off at L0 so existing M1 engine suites keep the
    model-only dispose path unless they opt in. ``tb_engine`` still exposes the
    same ``bus_sck`` / ``bus_ram_*_cs_n`` / ``bus_sio`` aliases, so pass
    ``pin_monitor=True`` when a test wants pin-axis evidence. L1
    :func:`bring_up_top` keeps ``pin_monitor=True`` by default.

    ``arbitration_monitor`` and ``controller_monitor`` default on at both levels
    so no run drops a catalog row. At L0 they resolve to the honest disposition
    the catalog assigns: every ``CHK-ARB-*`` / ``CHK-RST-OE`` / ``CHK-RST-STATUS``
    and ``CHK-CTRL-*`` row is ``na``, and ``CHK-RST-INTERNAL`` runs its engine
    subset. ``CHK-HS-OPCODE`` needs the pin decoder for its wait-cycle half, so
    it reports ``blocked`` at L0 unless ``pin_monitor=True``.
    """
    log = dut._log if log is None else log
    _stop_previous()

    _park(_optional(dut, "rst_n"), 0)
    _park(_optional(dut, "txn_valid"), 0)
    _park(_optional(dut, "cmd"), 0)
    _park(_optional(dut, "addr"), 0)
    _park(_optional(dut, "device_sel"), 0)
    _park(_optional(dut, "byte_len"), 0)
    _park(_optional(dut, "wdata"), 0)
    _park(_optional(dut, "fault_sio_drive"), 0)
    _park(_optional(dut, "fault_sio_oe"), 0)

    attached = attach_engine_psram(
        dut,
        devices,
        strict=strict_models,
        fill=fill,
        seed=seed,
        violations=violations,
    )
    bringup = BringUp(
        dut=dut, level=LEVEL_L0, devices=attached, violations=violations
    )
    _start_monitors(
        dut,
        bringup,
        bus_monitor=bus_monitor,
        ce_monitor=ce_monitor,
        handshake_monitor=handshake_monitor,
        pin_monitor=pin_monitor,
        arbitration_monitor=arbitration_monitor,
        controller_monitor=controller_monitor,
        strict_monitors=strict_monitors,
        log=log,
    )

    await start_clock(dut, period_ns=clock_period_ns)
    await apply_engine_reset(dut, cycles=reset_cycles)

    _HISTORY.append(bringup)
    return bringup


async def bring_up_top(
    dut,
    *,
    fill: int = 0x00,
    seed: "int | None" = None,
    violations: "ViolationLog | None" = None,
    strict_models: bool = False,
    strict_monitors: bool = False,
    bus_monitor: bool = True,
    ce_monitor: bool = True,
    handshake_monitor: bool = True,
    pin_monitor: bool = True,
    arbitration_monitor: bool = True,
    controller_monitor: bool = True,
    clock_period_ns: int = DEFAULT_CLOCK_PERIOD_NS,
    reset_cycles: int = DEFAULT_RESET_CYCLES,
    level: str = LEVEL_L1,
    log=None,
) -> BringUp:
    """Bring up L1 ``tb_top`` (or L2 ``tb_gl``): park host pins, attach, reset.

    Both PSRAM devices always attach: the shared ``uio`` bus makes a partially
    attached board unrepresentative, and cross-device descriptors are in scope
    for every L1 test.

    ``pin_monitor`` defaults on: it is the authoritative evidence for
    ``CHK-PIN-ADDR23-ZERO`` / ``CHK-PIN-KNOWN`` and the source of the ordered
    observed transaction log a scoreboard compares. Pass ``pin_monitor=False``
    only for a test that means to judge the model-only fallback path itself.

    ``arbitration_monitor`` and ``controller_monitor`` default on: at L1 every
    ``CHK-ARB-*`` / ``CHK-RST-*`` / ``CHK-CTRL-*`` row is always-on, so a run
    that turned them off would silently drop catalog coverage. At L2 the rows
    that need RTL hierarchy report ``na`` rather than ``blocked``, because the
    flattened netlist has no such names by construction.
    """
    log = dut._log if log is None else log
    _stop_previous()

    _park(_optional(dut, "rst_n"), 0)
    _park(_optional(dut, "ena"), 1)
    _park(_optional(dut, "ui_in"), 0)
    _park(_optional(dut, "host_uio_drive"), 0)
    _park(_optional(dut, "host_uio_oe"), 0)
    _park(_optional(dut, "fault_uio_drive"), 0)
    _park(_optional(dut, "fault_uio_oe"), 0)

    attached = attach_dual_psram(
        dut,
        strict=strict_models,
        fill=fill,
        seed=seed,
        violations=violations,
    )
    bringup = BringUp(dut=dut, level=level, devices=attached, violations=violations)
    _start_monitors(
        dut,
        bringup,
        bus_monitor=bus_monitor,
        ce_monitor=ce_monitor,
        handshake_monitor=handshake_monitor,
        pin_monitor=pin_monitor,
        arbitration_monitor=arbitration_monitor,
        controller_monitor=controller_monitor,
        strict_monitors=strict_monitors,
        log=log,
    )

    await start_clock(dut, period_ns=clock_period_ns)
    await apply_reset(dut, cycles=reset_cycles)

    _HISTORY.append(bringup)
    return bringup


async def bring_up(dut, *, level: "str | None" = None, **kwargs) -> BringUp:
    """Bring up *dut* at the level selected by ``LEVEL`` / ``DUT_LEVEL``.

    Pass ``level="L0"`` / ``"L1"`` / ``"L2"`` to override the environment. Any
    other keyword is forwarded to the level-specific helper, so a test that
    needs a level-only option should call that helper directly.
    """
    if level is None:
        level = parse_run_config()["dut_level"]
    if level == LEVEL_L0:
        return await bring_up_engine(dut, **kwargs)
    return await bring_up_top(dut, level=level, **kwargs)
