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
    TC-RXEDGE-NOMINAL-PASS
    TC-RXEDGE-TACLK-BOUNDARY
    TC-RXEDGE-PENDING-AT-STOP
    TC-PENDING-SURVIVES-CLEAR
    TC-TIMED-WRAPPER-STOP-ISOLATION
"""

import os

import cocotb
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge, Timer

from common.bringup import bring_up_engine
from common.config import parse_run_config
from common.dispose import dispose_run, expect
from common.engine_bfm import bytes_to_nibbles, engine_qpi_read, engine_qpi_write
from monitors import timing

FILL = 0x00
_LAUNCH_ADDRESS = 0x000680
_RX_ADDRESS = 0x0006C0
_RX_PAYLOAD = bytes((0x91, 0x2E, 0x47))
_BOUNDARY_TACLK_NS = (2.0, 5.5)


def _repro(config: dict, test_filter: str) -> str:
    profile = config["timing_profile"]
    extra = ""
    if profile == "sweep":
        extra = f" PSRAM_TACLK_NS={os.environ.get('PSRAM_TACLK_NS', '<unset>')}"
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=engine SIM={sim} SEED={seed} TIMING_PROFILE={profile}{extra} "
        "COCOTB_TEST_MODULES=tests.test_qspi_timing_launch_rx "
        "TEST_FILTER={test_filter}"
    ).format(
        sim=config["sim"],
        seed=config["seed"],
        profile=profile,
        extra=extra,
        test_filter=test_filter,
    )


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


@cocotb.test()
async def qspi_launch_directed(dut):
    """TC-LAUNCH-NOMINAL-PASS plus TC-LAUNCH-SCK-HIGH-VIOLATION."""
    config = parse_run_config()
    repro = _repro(config, "qspi_launch_directed")
    dut._log.info(repro)

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
async def qspi_rxedge_directed(dut):
    """TC-RXEDGE-NOMINAL-PASS and the selected ``tACLK`` boundary read."""
    config = parse_run_config()
    repro = _repro(config, "qspi_rxedge_directed")
    dut._log.info(repro)

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


@cocotb.test()
async def qspi_rxedge_pending_lifecycle(dut):
    """TC-RXEDGE-PENDING-AT-STOP and TC-PENDING-SURVIVES-CLEAR."""
    config = parse_run_config()
    repro = _repro(config, "qspi_rxedge_pending_lifecycle")
    dut._log.info(repro)

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
    config = parse_run_config()
    repro = _repro(config, "qspi_timed_wrapper_stop_isolation")
    dut._log.info(repro)

    bringup, _ = await _bring_up_timing(dut)
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
    bringup.stop()
    await _bring_up_timing(dut)
    await Timer(bringup.timing_params["PSRAM_TACLK_NS"] + 1.0, unit="ns")
    assert old_wrapper.timing_events == events_before_stop, (
        "TC-TIMED-WRAPPER-STOP-ISOLATION: retired delayed response appended "
        "timing events after stop/re-bring-up"
    )
