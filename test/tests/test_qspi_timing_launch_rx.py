"""M3 L0 directed evidence for ``Q-LAUNCH`` and ``Q-RXEDGE``.

Run this module at ``LEVEL=engine``.  The nominal cases use the L0 request BFM
so the checker observes the engine's public transaction, SCK, SIO/OE, and read
capture ports.  ``qspi_launch_fault_while_sck_high`` uses the existing
suite-local fault driver only to change the effective ASIC SIO drive during
the high half of an active SCK cycle; this is deliberately not an ownership
or protocol negative.

W2a interface contract:

* ``monitors.timing.Q_LAUNCH`` and ``Q_RXEDGE`` name the stable IDs.
* ``start_ce_timing_monitor`` returns a monitor whose ``results()``,
  ``counts()``, and ``violations_for()`` include both IDs at L0.

The contract is checked at test runtime instead of importing absent W2a names
at module import time.  Until W2a lands, the assertion reports the exact
missing surface rather than disguising it as a Python import failure.

For a boundary run, use ``TIMING_PROFILE=sweep PSRAM_TACLK_NS=2.0`` or
``PSRAM_TACLK_NS=5.5``.  The test requires the resolved manifest to retain
that requested endpoint; this makes a missing sweep-environment wiring an
explicit blocker rather than accidentally retesting nominal ``5.5 ns``.

Test-case IDs:
    TC-LAUNCH-NOMINAL-PASS
    TC-LAUNCH-SCK-HIGH-VIOLATION
    TC-LAUNCH-TSP-VIOLATION
    TC-LAUNCH-THD-VIOLATION
    TC-LAUNCH-THD-SAME-FS
    TC-LAUNCH-OE0-SIO-IGNORED
    TC-RXEDGE-NOMINAL-PASS
    TC-RXEDGE-TACLK-PAST-CAPTURE
    TC-RXEDGE-PENDING-AT-STOP
    TC-PENDING-SURVIVES-CLEAR
    TC-TIMED-WRAPPER-STOP-ISOLATION
    TC-RXEDGE-WRITE-ONLY-NA
"""

import os

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import FallingEdge, NextTimeStep, ReadOnly, RisingEdge, Timer

from common.bringup import bring_up_engine
from common.runlog import begin_run
from common.constants import DEFAULT_CLOCK_PERIOD_NS, FILL, RESULT_FAIL, RESULT_NA, RESULT_PASS
from common.dispose import dispose_run, expect
from common.engine_bfm import bytes_to_nibbles, engine_qpi_read, engine_qpi_write
from monitors import timing
_LAUNCH_ADDRESS = 0x000680
_RX_ADDRESS = 0x0006C0
_RX_PAYLOAD = bytes((0x91, 0x2E, 0x47))
_BOUNDARY_TACLK_NS = (2.0, 5.5)
_RACE_D_OUT_CE_NS = 20.0
_RACE_D_OUT_SCK_NS = 0.0

def _require_w2a_monitor(dut, bringup):
    """Attach W2a's public timing monitor or report its missing contract."""
    missing = [
        name
        for name in ("Q_LAUNCH", "Q_RXEDGE")
        if not isinstance(getattr(timing, name, None), str)
    ]
    assert not missing, (
        "W2a blocker: monitors.timing must export "
        f"{', '.join(missing)} and extend start_ce_timing_monitor results at L0"
    )

    monitor = timing.start_ce_timing_monitor(
        dut,
        strict=False,
        timing_params=bringup.timing_params,
        timed_devices=bringup.devices,
        level="L0",
    )
    # Make the suite-local monitor part of normal BringUp lifecycle paths too.
    bringup.ce = monitor
    results = monitor.results()
    missing_results = [
        check_id
        for check_id in (timing.Q_LAUNCH, timing.Q_RXEDGE)
        if check_id not in results
    ]
    assert not missing_results, (
        "W2a blocker: start_ce_timing_monitor results() omitted "
        f"{missing_results}; expose Q-LAUNCH/Q-RXEDGE through this existing "
        "L0 timing attachment"
    )
    return monitor

async def _bring_up_timing(dut):
    """Attach only the timing monitor around direct L0 engine traffic."""
    bringup = await bring_up_engine(
        dut,
        fill=FILL,
        bus_monitor=False,
        ce_monitor=False,
        handshake_monitor=False,
        pin_monitor=False,
        arbitration_monitor=False,
        controller_monitor=False,
    )
    assert bringup.timing_profile in ("nominal", "sweep"), (
        "test_qspi_timing_launch_rx requires TIMING_PROFILE=nominal or sweep, got "
        f"{bringup.timing_profile!r}"
    )
    return bringup, _require_w2a_monitor(dut, bringup)

async def _leave_read_capture_pending(dut, monitor):
    """Start a read and leave a timed read launch awaiting capture."""
    read_task = cocotb.start_soon(
        engine_qpi_read(dut, device=1, address=_RX_ADDRESS, length=1)
    )
    for _ in range(128):
        await RisingEdge(dut.psram_sck)
        await ReadOnly()
        if int(dut.psram1_ce_n.value) != 0:
            continue
        wrapper = monitor._timed_devices[1]
        wrapper._launch_read_nibble(
            0xA,
            wrapper._generation,
            source_fall_fs=0,
            device_fall_fs=0,
        )
        await RisingEdge(dut.psram_sck)
        await ReadOnly()
        if monitor._rx_pending:
            read_task.cancel()
            return
    read_task.cancel()
    raise AssertionError(
        "cleanup directed setup did not observe a launched read nibble before capture"
    )

def _require_sweep_endpoint(bringup) -> float:
    """Require the requested documented ``tACLK`` endpoint reached the wrapper."""
    raw = os.environ.get("PSRAM_TACLK_NS")
    assert raw is not None, (
        "boundary blocker: set PSRAM_TACLK_NS to 2.0 or 5.5 with "
        "TIMING_PROFILE=sweep"
    )
    requested = float(raw)
    assert requested in _BOUNDARY_TACLK_NS, (
        f"boundary point {requested}ns is not a documented tACLK endpoint "
        f"{_BOUNDARY_TACLK_NS}"
    )
    observed = bringup.timing_params["PSRAM_TACLK_NS"]
    assert observed == requested, (
        "sweep wiring blocker: resolved PSRAM_TACLK_NS="
        f"{observed}ns, requested {requested}ns. bring_up_engine must forward "
        "the documented PSRAM_TACLK_NS environment override into wrap_device."
    )
    return requested

def _device_plane_race_window_ready(bringup) -> bool:
    """True when sweep + D_OUT_* match the directed post-rise race point.

    ``D_OUT_CE_NS`` / ``D_OUT_SCK_NS`` are the DUT-to-device CE# and SCK
    transport delays (from ``TB_TCO_*`` + ``TB_FLIGHT_OUT_*``). The race
    point needs a non-zero CE# delay with zero SCK delay so a late
    device-plane launch can land after DUT-plane CE# rise cleanup.
    """
    if bringup.timing_profile != "sweep":
        return False
    return (
        bringup.timing_params["D_OUT_CE_NS"] == _RACE_D_OUT_CE_NS
        and bringup.timing_params["D_OUT_SCK_NS"] == _RACE_D_OUT_SCK_NS
    )

async def _inject_sio_change_while_sck_high(dut, *, timeout_edges: int = 128) -> None:
    """Replace the L0 ASIC SIO drive during an active high SCK half-cycle."""
    for _ in range(timeout_edges):
        await RisingEdge(dut.psram_sck)
        await ReadOnly()
        if int(dut.psram0_ce_n.value) == 0 or int(dut.psram1_ce_n.value) == 0:
            await NextTimeStep()
            dut.fault_sio_drive.value = 0xF
            dut.fault_sio_oe.value = 0xF
            await Timer(1, unit="ns")
            dut.fault_sio_oe.value = 0
            return
    raise AssertionError("TC-LAUNCH-SCK-HIGH-VIOLATION: no selected SCK rise")

async def _selected_write_window(dut, *, timeout_edges: int = 128) -> None:
    """Advance to a selected CE# with the engine driving SIO."""
    for _ in range(timeout_edges):
        await RisingEdge(dut.psram_sck)
        await ReadOnly()
        if int(dut.psram0_ce_n.value) != 0 and int(dut.psram1_ce_n.value) != 0:
            continue
        if int(dut.sio_oe.value) == 0:
            continue
        return
    raise AssertionError("no selected write-phase SCK rise with SIO OE")

async def _glitch_asic_oe(dut, *, hold_ns: float = 0.1) -> None:
    """Force an ASIC SIO OE falling edge via the L0 fault overlay."""
    dut.fault_sio_oe.value = 0xF
    await Timer(hold_ns, unit="ns")
    dut.fault_sio_oe.value = 0

async def _inject_short_setup(dut, *, setup_ns: float = 0.5) -> None:
    """Change driven OE late in the SCK-low half (short tSP)."""
    await _selected_write_window(dut)
    await FallingEdge(dut.psram_sck)
    # Engine SCK is clk/2; low half equals one clk period.
    await Timer(DEFAULT_CLOCK_PERIOD_NS - setup_ns, unit="ns")
    await _glitch_asic_oe(dut)

async def _inject_short_hold(dut, *, hold_ns: float = 0.5) -> None:
    """Change driven OE shortly after a sampling rise (short or exact tHD).

    Wait for a fall then the following rise so ``_check_launch_rise`` has
    opened a hold window from the nibble launched in that low half.
    """
    await _selected_write_window(dut)
    await FallingEdge(dut.psram_sck)
    await RisingEdge(dut.psram_sck)
    await Timer(hold_ns, unit="ns")
    await _glitch_asic_oe(dut)

@cocotb.test()
async def qspi_launch_directed(dut):
    """TC-LAUNCH-NOMINAL-PASS plus TC-LAUNCH-SCK-HIGH-VIOLATION."""
    config, repro = begin_run(dut, "qspi_launch_directed")

    bringup, monitor = await _bring_up_timing(dut)
    await engine_qpi_write(
        dut, device=0, address=_LAUNCH_ADDRESS, payload=bytes((0x3A, 0xC5))
    )
    summary = monitor.summary()
    dut._log.info("MARGIN TC-LAUNCH-NOMINAL-PASS: %s", summary)
    for name in (
        "_min_cem_margin_ns",
        "_min_cph_margin_ns",
        "_min_csp_margin_ns",
        "_min_chd_margin_ns",
        "_rx_min_setup_ns",
        "_rx_min_hold_ns",
    ):
        value = getattr(monitor, name, None)
        if value is not None:
            assert value > 0, (
                f"TC-LAUNCH-NOMINAL-PASS: W3b margin gate FAIL "
                f"{name}={value:.3f} (must be > 0). {summary}"
            )
    dispose_run(
        monitor,
        test="TC-LAUNCH-NOMINAL-PASS",
        log=dut._log,
        repro=repro,
    )
    assert monitor.results()[timing.Q_RXEDGE] == RESULT_NA, (
        "TC-RXEDGE-WRITE-ONLY-NA: write-only traffic disposed Q-RXEDGE="
        f"{monitor.results()[timing.Q_RXEDGE]!r}, expected na. {repro}"
    )
    assert monitor.results()[timing.Q_LAUNCH] == RESULT_PASS, (
        "TC-LAUNCH-NOMINAL-PASS: Q-LAUNCH="
        f"{monitor.results()[timing.Q_LAUNCH]!r} after a legal write. {repro}"
    )

    monitor.clear()
    write_task = cocotb.start_soon(
        engine_qpi_write(
            dut, device=0, address=_LAUNCH_ADDRESS + 0x20, payload=bytes((0x5A,))
        )
    )
    await _inject_sio_change_while_sck_high(dut)
    await write_task
    dispose_run(
        monitor,
        test="TC-LAUNCH-SCK-HIGH-VIOLATION",
        expect_fail=[expect(timing.Q_LAUNCH)],
        log=dut._log,
        repro=repro,
    )

@cocotb.test()
async def qspi_launch_short_setup_hold(dut):
    """TC-LAUNCH-TSP-VIOLATION, TC-LAUNCH-THD-VIOLATION, TC-LAUNCH-THD-SAME-FS."""
    config, repro = begin_run(dut, "qspi_launch_short_setup_hold")

    bringup, monitor = await _bring_up_timing(dut)
    write_task = cocotb.start_soon(
        engine_qpi_write(
            dut, device=0, address=_LAUNCH_ADDRESS + 0x40, payload=bytes((0x11, 0x22))
        )
    )
    await _inject_short_setup(dut, setup_ns=0.5)
    await write_task
    dispose_run(
        monitor,
        test="TC-LAUNCH-TSP-VIOLATION",
        expect_fail=[expect(timing.Q_LAUNCH)],
        log=dut._log,
        repro=repro,
    )
    details = [event.detail for event in monitor.violations_for(timing.Q_LAUNCH)]
    assert any("setup=" in detail and "tSP=" in detail for detail in details), (
        "TC-LAUNCH-TSP-VIOLATION: expected a tSP finding. "
        f"details={details}. {repro}"
    )

    monitor.clear()
    write_task = cocotb.start_soon(
        engine_qpi_write(
            dut, device=0, address=_LAUNCH_ADDRESS + 0x60, payload=bytes((0x33,))
        )
    )
    await _inject_short_hold(dut, hold_ns=0.5)
    await write_task
    dispose_run(
        monitor,
        test="TC-LAUNCH-THD-VIOLATION",
        expect_fail=[expect(timing.Q_LAUNCH)],
        log=dut._log,
        repro=repro,
    )
    details = [event.detail for event in monitor.violations_for(timing.Q_LAUNCH)]
    assert any("hold=" in detail and "tHD=" in detail for detail in details), (
        "TC-LAUNCH-THD-VIOLATION: expected a tHD finding. "
        f"details={details}. {repro}"
    )

    monitor.clear()
    thd_ns = bringup.timing_params["PSRAM_THD_NS"]
    write_task = cocotb.start_soon(
        engine_qpi_write(
            dut, device=0, address=_LAUNCH_ADDRESS + 0x80, payload=bytes((0x44,))
        )
    )
    await _inject_short_hold(dut, hold_ns=thd_ns)
    await write_task
    dispose_run(
        monitor,
        test="TC-LAUNCH-THD-SAME-FS",
        expect_fail=[expect(timing.Q_LAUNCH)],
        log=dut._log,
        repro=repro,
    )
    details = [event.detail for event in monitor.violations_for(timing.Q_LAUNCH)]
    assert any("hold=" in detail and "tHD=" in detail for detail in details), (
        "TC-LAUNCH-THD-SAME-FS: same-fs / exact-tHD change must fail tHD. "
        f"details={details}. {repro}"
    )

@cocotb.test()
async def qspi_launch_oe0_sio_ignored(dut):
    """TC-LAUNCH-OE0-SIO-IGNORED: SIO value changes while OE=0 are not launches."""
    config, repro = begin_run(dut, "qspi_launch_oe0_sio_ignored")

    bringup, monitor = await _bring_up_timing(dut)
    bringup.psram1.write(_RX_ADDRESS, _RX_PAYLOAD)
    read_task = cocotb.start_soon(
        engine_qpi_read(dut, device=1, address=_RX_ADDRESS, length=len(_RX_PAYLOAD))
    )
    flipped = False
    for _ in range(256):
        await RisingEdge(dut.psram_sck)
        await ReadOnly()
        if int(dut.psram1_ce_n.value) != 0:
            continue
        if int(dut.sio_oe.value) != 0:
            continue
        await NextTimeStep()
        dut.sio_out.value = int(dut.sio_out.value) ^ 0xF
        flipped = True
        break
    await read_task
    assert flipped, (
        "TC-LAUNCH-OE0-SIO-IGNORED: never saw OE=0 on a selected rising SCK. "
        + repro
    )
    assert not monitor.violations_for(timing.Q_LAUNCH), (
        "TC-LAUNCH-OE0-SIO-IGNORED: SIO change with OE=0 produced Q-LAUNCH "
        f"{[str(event) for event in monitor.violations_for(timing.Q_LAUNCH)]}. "
        + repro
    )
    dispose_run(
        monitor,
        test="TC-LAUNCH-OE0-SIO-IGNORED",
        log=dut._log,
        repro=repro,
    )
    assert monitor.results()[timing.Q_RXEDGE] == RESULT_PASS, (
        "TC-LAUNCH-OE0-SIO-IGNORED: Q-RXEDGE="
        f"{monitor.results()[timing.Q_RXEDGE]!r} after a timed read. {repro}"
    )

@cocotb.test()
async def qspi_rxedge_directed(dut):
    """TC-RXEDGE-NOMINAL-PASS and the selected ``tACLK`` boundary read."""
    config, repro = begin_run(dut, "qspi_rxedge_directed")

    bringup, monitor = await _bring_up_timing(dut)
    if bringup.timing_profile == "sweep":
        taclk_ns = _require_sweep_endpoint(bringup)
        test = f"TC-RXEDGE-TACLK-BOUNDARY-{taclk_ns:.1f}NS"
    else:
        assert bringup.timing_params["PSRAM_TACLK_NS"] == 5.5
        test = "TC-RXEDGE-NOMINAL-PASS"

    bringup.psram1.write(_RX_ADDRESS, _RX_PAYLOAD)
    result = await engine_qpi_read(
        dut, device=1, address=_RX_ADDRESS, length=len(_RX_PAYLOAD)
    )
    expected_nibbles = bytes_to_nibbles(_RX_PAYLOAD)
    assert len(result.nibbles) == len(expected_nibbles), (
        f"{test}: rdata_valid count={len(result.nibbles)}, "
        f"expected nibbles={len(expected_nibbles)}. {repro}"
    )
    assert result.nibbles == expected_nibbles, (
        f"{test}: captured nibbles={result.nibbles}, expected={expected_nibbles}. "
        f"{repro}"
    )
    summary = monitor.summary()
    dut._log.info("MARGIN %s: %s", test, summary)
    if bringup.timing_profile == "nominal":
        for name in (
            "_min_cem_margin_ns",
            "_min_cph_margin_ns",
            "_min_csp_margin_ns",
            "_min_chd_margin_ns",
            "_rx_min_setup_ns",
            "_rx_min_hold_ns",
        ):
            value = getattr(monitor, name, None)
            if value is not None:
                assert value > 0, (
                    f"{test}: W3b margin gate FAIL {name}={value:.3f} "
                    f"(must be > 0). {summary}"
                )
    dispose_run(monitor, test=test, log=dut._log, repro=repro)
    assert monitor.results()[timing.Q_RXEDGE] == RESULT_PASS, (
        f"{test}: Q-RXEDGE={monitor.results()[timing.Q_RXEDGE]!r} after a timed "
        f"read with {monitor._rx_captures} captures. {repro}"
    )

@cocotb.test()
async def qspi_rxedge_device_plane_race(dut):
    """TC-RXEDGE-RACE-DEVICE-PLANE: post-rise launch is scope-audited once."""
    config, repro = begin_run(dut, "qspi_rxedge_device_plane_race")

    bringup, monitor = await _bring_up_timing(dut)
    if not _device_plane_race_window_ready(bringup):
        # Endpoint / other sweep cells omit the race TB_* overrides; vacuous
        # pass keeps the full-module suite green. The dedicated race cell sets
        # TB_TCO_CE_NS=20 (and zero SCK path delay) so this body runs.
        dut._log.info(
            "TC-RXEDGE-RACE-DEVICE-PLANE: skip (need TIMING_PROFILE=sweep "
            "D_OUT_CE_NS=%.1f D_OUT_SCK_NS=%.1f; observed profile=%s "
            "D_OUT_CE_NS=%.1f D_OUT_SCK_NS=%.1f)",
            _RACE_D_OUT_CE_NS,
            _RACE_D_OUT_SCK_NS,
            bringup.timing_profile,
            bringup.timing_params["D_OUT_CE_NS"],
            bringup.timing_params["D_OUT_SCK_NS"],
        )
        dispose_run(
            monitor,
            test="TC-RXEDGE-RACE-DEVICE-PLANE",
            log=dut._log,
            repro=repro,
        )
        return
    bringup.psram1.write(_RX_ADDRESS, _RX_PAYLOAD)
    wrapper = monitor._timed_devices[1]

    read_task = cocotb.start_soon(
        engine_qpi_read(dut, device=1, address=_RX_ADDRESS, length=len(_RX_PAYLOAD))
    )
    await RisingEdge(dut.psram1_ce_n)
    await ReadOnly()
    await Timer(0.1, unit="ns")
    assert wrapper.agent.phase == "DATA", (
        "TC-RXEDGE-RACE-DEVICE-PLANE: DUT-plane CE# rise ended the read "
        "before the delayed device-plane CE# commit"
    )

    # D_OUT_CE_NS (DUT-to-device CE# delay) defers the device-plane commit for
    # 20 ns after this DUT-plane CE# rise. Inject a device-plane SCK fall in
    # that window, after the DUT-plane scope-close, to model its transport event.
    # The raw model's selected property follows the DUT pin, so invoking its
    # normal response path here would also manufacture an unrelated read-stale
    # outcome. Q-RXEDGE consumes this append-only device-plane event stream.
    race_time_fs = int(get_sim_time(unit="fs"))
    wrapper.timing_events.append(
        {
            "kind": "read-launch",
            "generation": wrapper._generation,
            "nibble": 0xA,
            "source_fall_fs": race_time_fs,
            "device_fall_fs": race_time_fs,
        }
    )
    monitor._collect_timed_events(in_reset=False)
    assert len(monitor._rx_pending) == 1, (
        "TC-RXEDGE-RACE-DEVICE-PLANE: late device-plane launch did not open "
        "the intended Q-RXEDGE pending item"
    )

    await Timer(_RACE_D_OUT_CE_NS + 1.0, unit="ns")
    monitor._collect_timed_events(in_reset=False)
    await read_task

    scope_findings = monitor.violations_for(timing.Q_RXEDGE)
    assert len(scope_findings) == 1, (
        "TC-RXEDGE-RACE-DEVICE-PLANE: expected one scope-close audit for "
        f"the injected nibble, observed {len(scope_findings)}"
    )
    assert "reason=scope-close" in scope_findings[0].detail, (
        "TC-RXEDGE-RACE-DEVICE-PLANE: late launch was not audited at the "
        "device-plane CE# commit"
    )
    assert not monitor._rx_pending, (
        "TC-RXEDGE-RACE-DEVICE-PLANE: late pending item leaked past the "
        "device-plane CE# commit"
    )

    following = await engine_qpi_read(
        dut, device=1, address=_RX_ADDRESS, length=len(_RX_PAYLOAD)
    )
    assert following.nibbles == bytes_to_nibbles(_RX_PAYLOAD), (
        "TC-RXEDGE-RACE-DEVICE-PLANE: following CE# session captured "
        f"{following.nibbles}, expected {bytes_to_nibbles(_RX_PAYLOAD)}"
    )
    assert not monitor._rx_pending, (
        "TC-RXEDGE-RACE-DEVICE-PLANE: prior-session pending item matched "
        "a capture in the following CE# session"
    )
    assert len(monitor.violations_for(timing.Q_RXEDGE)) == 1, (
        "TC-RXEDGE-RACE-DEVICE-PLANE: following CE# session created a "
        "duplicate finding for the injected nibble"
    )
    dispose_run(
        monitor,
        test="TC-RXEDGE-RACE-DEVICE-PLANE",
        expect_fail=[expect(timing.Q_RXEDGE, count=1)],
        log=dut._log,
        repro=repro,
    )
    assert len(monitor.violations_for(timing.Q_RXEDGE)) == 1, (
        "TC-RXEDGE-RACE-DEVICE-PLANE: dispose duplicated the scope-close "
        "finding for the injected nibble"
    )

@cocotb.test()
async def qspi_rxedge_pending_lifecycle(dut):
    """TC-RXEDGE-PENDING-AT-STOP and TC-PENDING-SURVIVES-CLEAR."""
    config, repro = begin_run(dut, "qspi_rxedge_pending_lifecycle")

    bringup, monitor = await _bring_up_timing(dut)
    await _leave_read_capture_pending(dut, monitor)
    dispose_run(
        monitor,
        test="TC-RXEDGE-PENDING-AT-STOP",
        expect_fail=[expect(timing.Q_RXEDGE)],
        log=dut._log,
        repro=repro,
    )
    details = [event.detail for event in monitor.violations_for(timing.Q_RXEDGE)]
    assert any("reason=dispose" in detail for detail in details), (
        "TC-RXEDGE-PENDING-AT-STOP: cleanup finding omitted reason=dispose"
    )
    bringup.stop()
    await NextTimeStep()

    bringup, monitor = await _bring_up_timing(dut)
    await _leave_read_capture_pending(dut, monitor)
    bringup.clear()
    dispose_run(
        monitor,
        test="TC-PENDING-SURVIVES-CLEAR",
        expect_fail=[expect(timing.Q_RXEDGE)],
        log=dut._log,
        repro=repro,
    )
    details = [event.detail for event in monitor.pending.carryover]
    assert any("reason=window-clear" in detail for detail in details), (
        "TC-PENDING-SURVIVES-CLEAR: carryover omitted reason=window-clear"
    )
    bringup.stop()

@cocotb.test()
async def qspi_timed_wrapper_stop_isolation(dut):
    """TC-TIMED-WRAPPER-STOP-ISOLATION: retired delayed work is inert."""
    config, repro = begin_run(dut, "qspi_timed_wrapper_stop_isolation")

    bringup, monitor = await _bring_up_timing(dut)
    old_wrapper = bringup.psram1
    assert hasattr(old_wrapper, "timing_events"), (
        "TC-TIMED-WRAPPER-STOP-ISOLATION requires a nominal timed wrapper"
    )
    old_wrapper._launch_read_nibble(
        0xA,
        old_wrapper._generation,
        source_fall_fs=0,
        device_fall_fs=0,
    )
    events_before_stop = list(old_wrapper.timing_events)
    dispose_run(
        monitor,
        test="TC-TIMED-WRAPPER-STOP-ISOLATION-SETUP",
        log=dut._log,
        repro=repro,
    )
    bringup.stop()
    bringup, monitor = await _bring_up_timing(dut)
    await Timer(bringup.timing_params["PSRAM_TACLK_NS"] + 1.0, unit="ns")
    assert old_wrapper.timing_events == events_before_stop, (
        "TC-TIMED-WRAPPER-STOP-ISOLATION: retired delayed response appended "
        "timing events after stop/re-bring-up"
    )
    dispose_run(
        monitor,
        test="TC-TIMED-WRAPPER-STOP-ISOLATION",
        log=dut._log,
        repro=repro,
    )

@cocotb.test()
async def qspi_rxedge_taclk_past_capture(dut):
    """TC-RXEDGE-TACLK-PAST-CAPTURE: tACLK longer than falling-to-rising SCK.

    Functional sim, not STA. At 10 ns clk, SCK is 20 ns; capture is the next
    rising SCK (~10 ns after the fall). ``PSRAM_TACLK_NS=12`` is past that
    edge. Requires ``TIMING_PROFILE=sweep`` and that override.
    """
    extra = {}
    raw = os.environ.get("PSRAM_TACLK_NS")
    if raw:
        extra["PSRAM_TACLK_NS"] = raw
    config, repro = begin_run(
        dut,
        "qspi_rxedge_taclk_past_capture",
        extra=extra or None,
    )
    if config["timing_profile"] != "sweep" or raw is None or float(raw) < 12.0:
        dut._log.info(
            "TC-RXEDGE-TACLK-PAST-CAPTURE skipped (need TIMING_PROFILE=sweep "
            "PSRAM_TACLK_NS>=12). %s",
            repro,
        )
        return

    bringup, monitor = await _bring_up_timing(dut)
    taclk = bringup.timing_params["PSRAM_TACLK_NS"]
    assert taclk >= 12.0, f"resolved tACLK={taclk} ns is not past capture. {repro}"
    bringup.psram1.write(_RX_ADDRESS, _RX_PAYLOAD)
    await engine_qpi_read(
        dut, device=1, address=_RX_ADDRESS, length=len(_RX_PAYLOAD)
    )
    dispose_run(
        monitor,
        test="TC-RXEDGE-TACLK-PAST-CAPTURE",
        expect_fail=[expect(timing.Q_RXEDGE)],
        log=dut._log,
        repro=repro,
    )
    assert monitor.results()[timing.Q_RXEDGE] == RESULT_FAIL, (
        f"TC-RXEDGE-TACLK-PAST-CAPTURE: Q-RXEDGE="
        f"{monitor.results()[timing.Q_RXEDGE]!r}, expected fail. {repro}"
    )
