"""M3 ``Q-CSP`` / ``Q-CHD`` / ``Q-TERM`` directed timing (``TIMING_PROFILE=nominal``).

This module is the delay-annotated counterpart to
:mod:`tests.test_qspi_timing` (which stays M1 ``Q-CEM``/``Q-CPH`` only under
``TIMING_PROFILE=ideal``). It follows the same shape: grant ``BUS_GNT``, park
the MCU pass-through master, and drive raw CE#/SCK edges on the shared
``uio`` bus directly so :class:`monitors.timing.CeTimingMonitor` can be
judged in isolation. Other always-on catalogs (bus/handshake/pin/
arbitration/controller) stay off, matching ``test_qspi_timing``'s rationale:
bare CE#/SCK pulses without a framed QPI transfer are outside those
catalogs' scope.

Thresholds are the resolved ``TIMING_PROFILE=nominal`` manifest from
:func:`models.psram_timing.resolve_timing_params` (``PSRAM_TCSP_NS=2.5``,
``PSRAM_TCHD_NS=3.0``; see ``docs/llm/verification/04-timing-in-sim.md``),
read from ``bringup.timing_params`` rather than hardcoded, so this suite
tracks the manifest instead of asserting its own copy of the datasheet
values. ``PSRAM_TCEM_NS`` / ``PSRAM_TCPH_NS`` keep their monitor defaults
(4 us extended / 18 ns) unchanged: this module does not exercise ``Q-CEM``/
``Q-CPH`` (that is ``test_qspi_timing``'s job), so no directed shortening is
needed for speed here.

``Q-TERM``'s architectural "final read data committed" branch needs a real
``rdata_valid`` pulse count, which is an L0 ``tb_engine`` signal, not an L1
top-level port (:mod:`monitors.timing` only wires ``rdata_valid`` when the
DUT exposes it). At L1 that leaves ``committed`` trivially true whenever the
test declares ``read_expected_nibbles=0`` (0 committed == 0 expected), which
isolates the other architectural precondition this suite *can* drive
directly: SCK must be frozen low at CE#'s rise. ``TC-TERM-SCK-NOT-FROZEN``
exercises exactly that branch with legal ``Q-CSP``/``Q-CHD`` margins on
either side so only ``Q-TERM`` fires.

Test-case IDs:
    TC-QTIMING-DELAY-BASELINE
    TC-CSP-BOUNDARY-PASS
    TC-CHD-BOUNDARY-PASS
    TC-CSP-VIOLATION
    TC-CHD-VIOLATION
    TC-TERM-SCK-NOT-FROZEN
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.dispose import dispose_run, expect
from common.host import UIO_PSRAM_CE_BITS, UIO_SCK_BIT, QpiPassthroughMaster, assert_bus_req
from monitors.timing import Q_CHD, Q_CSP, Q_TERM, start_ce_timing_monitor

FILL = 0x00

# Comfortably above the resolved nominal tCSP/tCHD (2.5/3.0 ns) and above the
# monitor's fixed 18 ns tCPH default, so the two-device baseline phase and
# the inter-phase gaps never draw an undeclared Q-CPH surprise.
LEGAL_GAP_NS = 25.0


async def _await_bus_gnt(dut, *, cycles: int = 32) -> None:
    await assert_bus_req(dut, hold=True)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> 1) & 1:
            return
    raise AssertionError("BUS_GNT did not assert after BUS_REQ")


async def _bring_up(dut, *, read_expected_nibbles=None):
    """Shared top bring-up + directed CE timing monitor under ``TIMING_PROFILE=nominal``.

    Mirrors :func:`tests.test_qspi_timing._bring_up`: every other always-on
    catalog stays off because this suite drives raw pin edges directly,
    outside the DMA controller/handshake contract. Thresholds come from
    ``bringup.timing_params`` (the resolved profile manifest) rather than a
    directed override, since the nominal ``tCSP``/``tCHD`` values are already
    small enough to keep the module fast.
    """
    bringup = await bring_up_top(
        dut,
        fill=FILL,
        bus_monitor=False,
        ce_monitor=False,
        handshake_monitor=False,
        pin_monitor=False,
        arbitration_monitor=False,
        controller_monitor=False,
    )
    assert bringup.timing_profile == "nominal", (
        "test_qspi_timing_delay requires TIMING_PROFILE=nominal, got "
        f"{bringup.timing_profile!r} (production defaults live in "
        "models.psram_timing; this suite does not redefine them)"
    )
    await _await_bus_gnt(dut)

    master = QpiPassthroughMaster(dut)
    await master.park()
    bringup.clear()
    # Attach after park: L1 bus_sck is Z until the MCU drives SCK, and an
    # early OE sample would otherwise log Q-LAUNCH with SCK=None.
    bringup.ce = start_ce_timing_monitor(
        dut,
        strict=False,
        timing_params=bringup.timing_params,
        read_expected_nibbles=read_expected_nibbles,
        timed_devices=bringup.devices,
    )
    return bringup, master


def _repro(config: dict, test_filter: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=top SIM={sim} SEED={seed} TIMING_PROFILE=nominal "
        "COCOTB_TEST_MODULES=tests.test_qspi_timing_delay "
        "TEST_FILTER={test_filter}"
    ).format(sim=config["sim"], seed=config["seed"], test_filter=test_filter)


def _assert_positive_margins(ce, *, test: str, log) -> None:
    """W3b margin gate: legal-pass min margins must be strictly positive."""
    summary = ce.summary()
    log.info("MARGIN %s: %s", test, summary)
    fields = (
        ("min_cem_margin_ns", getattr(ce, "_min_cem_margin_ns", None)),
        ("min_cph_margin_ns", getattr(ce, "_min_cph_margin_ns", None)),
        ("min_csp_margin_ns", getattr(ce, "_min_csp_margin_ns", None)),
        ("min_chd_margin_ns", getattr(ce, "_min_chd_margin_ns", None)),
    )
    seen = [(name, value) for name, value in fields if value is not None]
    assert seen, f"{test}: W3b margin gate FAIL - no margins recorded. {summary}"
    for name, value in seen:
        assert value > 0, (
            f"{test}: W3b margin gate FAIL {name}={value:.3f} (must be > 0). "
            f"{summary}"
        )


def _assert_detail(ce, check_id: str, *, test: str, detail_substr: str) -> None:
    """Keep the directed detail substrings the M1 CEM/CPH cases asserted."""
    events = ce.violations_for(check_id)
    assert events, f"{test}: missing {check_id} after dispose"
    assert detail_substr in events[0].detail, (
        f"{test}: missing {detail_substr!r} in {events[0].detail}"
    )


async def _drive_ce_sck(
    master: QpiPassthroughMaster,
    device: int,
    *,
    csp_gap_ns: float,
    rise_to_ce_ns: float,
    sck_high_ns: "float | None" = None,
) -> None:
    """Fall one RAM CE#, drive a single SCK rising edge, then raise CE#.

    ``csp_gap_ns`` is the CE# fall -> first SCK rise gap (``Q-CSP``).
    ``rise_to_ce_ns`` is the total gap from that SCK rise to CE#'s rise
    (``Q-CHD``), measured independently of when/whether SCK falls again in
    between: the monitor keys ``Q-CHD`` off the rising edge itself. Passing
    ``sck_high_ns`` (some value ``< rise_to_ce_ns``) lets SCK fall again
    before CE# rises, matching a normal frame; leaving it ``None`` leaves SCK
    high straight through CE#'s rise (the ``Q-TERM`` "SCK not frozen"
    precondition) and does **not** repark SCK low afterward, so the caller is
    responsible for lowering it once the monitor has observed the CE# rise.
    """
    master._set_bit(UIO_PSRAM_CE_BITS[device], 0)
    master._apply()
    await Timer(csp_gap_ns, unit="ns")
    master._set_bit(UIO_SCK_BIT, 1)
    master._apply()
    if sck_high_ns is None:
        await Timer(rise_to_ce_ns, unit="ns")
    else:
        assert 0.0 <= sck_high_ns < rise_to_ce_ns, (
            f"sck_high_ns={sck_high_ns} must be within [0, rise_to_ce_ns={rise_to_ce_ns})"
        )
        await Timer(sck_high_ns, unit="ns")
        master._set_bit(UIO_SCK_BIT, 0)
        master._apply()
        await Timer(rise_to_ce_ns - sck_high_ns, unit="ns")
    for ce_bit in UIO_PSRAM_CE_BITS:
        master._set_bit(ce_bit, 1)
    master._apply()


@cocotb.test()
async def qspi_timing_delay_csp_chd(dut):
    """TC-QTIMING-DELAY-BASELINE, CSP/CHD boundary-pass, and CSP/CHD violation."""
    config = parse_run_config()
    dut._log.info(
        "SEED=%d LEVEL=%s SIM=%s TIMING_PROFILE=%s",
        config["seed"], config["level"], config["sim"], config["timing_profile"],
    )

    bringup, master = await _bring_up(dut)
    nom_tcsp_ns = bringup.timing_params["PSRAM_TCSP_NS"]
    nom_tchd_ns = bringup.timing_params["PSRAM_TCHD_NS"]
    dut._log.info(
        "resolved nominal thresholds: PSRAM_TCSP_NS=%.3f PSRAM_TCHD_NS=%.3f",
        nom_tcsp_ns, nom_tchd_ns,
    )

    # -- TC-QTIMING-DELAY-BASELINE: generous legal margins on both devices ---
    repro = _repro(config, "qspi_timing_delay_csp_chd")
    dut._log.info(repro)
    await _drive_ce_sck(
        master, 0, csp_gap_ns=LEGAL_GAP_NS, rise_to_ce_ns=LEGAL_GAP_NS,
        sck_high_ns=LEGAL_GAP_NS / 2,
    )
    await Timer(LEGAL_GAP_NS, unit="ns")
    await _drive_ce_sck(
        master, 1, csp_gap_ns=LEGAL_GAP_NS, rise_to_ce_ns=LEGAL_GAP_NS,
        sck_high_ns=LEGAL_GAP_NS / 2,
    )
    await Timer(1, unit="ns")
    _assert_positive_margins(
        bringup.ce, test="TC-QTIMING-DELAY-BASELINE", log=dut._log
    )
    dispose_run(
        bringup.ce, test="TC-QTIMING-DELAY-BASELINE", log=dut._log, repro=repro,
    )

    # -- TC-CSP-BOUNDARY-PASS: CE# fall to first SCK rise exactly at tCSP ----
    bringup.clear()
    dut._log.info(repro)
    await _drive_ce_sck(
        master, 0, csp_gap_ns=nom_tcsp_ns, rise_to_ce_ns=LEGAL_GAP_NS,
        sck_high_ns=LEGAL_GAP_NS / 2,
    )
    await Timer(1, unit="ns")
    dispose_run(bringup.ce, test="TC-CSP-BOUNDARY-PASS", log=dut._log, repro=repro)

    # -- TC-CHD-BOUNDARY-PASS: final SCK rise to CE# rise exactly at tCHD ----
    bringup.clear()
    dut._log.info(repro)
    await _drive_ce_sck(
        master, 1, csp_gap_ns=LEGAL_GAP_NS, rise_to_ce_ns=nom_tchd_ns,
        sck_high_ns=nom_tchd_ns / 2,
    )
    await Timer(1, unit="ns")
    dispose_run(bringup.ce, test="TC-CHD-BOUNDARY-PASS", log=dut._log, repro=repro)

    # -- TC-CSP-VIOLATION: CE# fall to first SCK rise below tCSP -> Q-CSP ----
    bringup.clear()
    dut._log.info(repro)
    short_csp_gap_ns = max(nom_tcsp_ns - 1.5, 0.5)
    await _drive_ce_sck(
        master, 0, csp_gap_ns=short_csp_gap_ns, rise_to_ce_ns=LEGAL_GAP_NS,
        sck_high_ns=LEGAL_GAP_NS / 2,
    )
    await Timer(1, unit="ns")
    dispose_run(
        bringup.ce,
        test="TC-CSP-VIOLATION",
        expect_fail=[expect(Q_CSP, count=1)],
        log=dut._log,
        repro=repro,
    )
    _assert_detail(
        bringup.ce, Q_CSP, test="TC-CSP-VIOLATION",
        detail_substr="CE# fall to first SCK rise",
    )
    dut._log.info("TC-CSP-VIOLATION recorded: %s", bringup.ce.violations_for(Q_CSP)[0])

    # -- TC-CHD-VIOLATION: final SCK rise to CE# rise below tCHD -> Q-CHD ----
    bringup.clear()
    dut._log.info(repro)
    short_chd_gap_ns = max(nom_tchd_ns - 2.0, 0.5)
    await _drive_ce_sck(
        master, 1, csp_gap_ns=LEGAL_GAP_NS, rise_to_ce_ns=short_chd_gap_ns,
        sck_high_ns=short_chd_gap_ns / 2,
    )
    await Timer(1, unit="ns")
    dispose_run(
        bringup.ce,
        test="TC-CHD-VIOLATION",
        expect_fail=[expect(Q_CHD, count=1)],
        log=dut._log,
        repro=repro,
    )
    _assert_detail(
        bringup.ce, Q_CHD, test="TC-CHD-VIOLATION",
        detail_substr="final SCK rise to CE# rise",
    )
    dut._log.info("TC-CHD-VIOLATION recorded: %s", bringup.ce.violations_for(Q_CHD)[0])

    dut._log.info(
        "qspi_timing_delay_csp_chd passed: BASELINE, CSP/CHD boundary-pass, "
        "CSP/CHD violation"
    )


@cocotb.test()
async def qspi_timing_delay_term(dut):
    """TC-TERM-SCK-NOT-FROZEN: ``Q-TERM`` architectural "SCK frozen" precondition.
    
    ``rdata_valid`` is not an L1 top-level port, so this suite cannot drive a
    real committed-nibble count into the monitor (see module docstring).
    Declaring ``read_expected_nibbles=0`` makes the "committed" precondition
    trivially satisfied (0 commits == 0 expected) so the case isolates the
    other architectural precondition instead: SCK must be frozen low at CE#'s
    rise. ``Q-CSP``/``Q-CHD`` use legal margins on both sides of the single
    SCK edge so only ``Q-TERM`` fires.
    """
    config = parse_run_config()
    repro = _repro(config, "qspi_timing_delay_term")
    dut._log.info(repro)

    bringup, master = await _bring_up(dut, read_expected_nibbles=lambda label: 0)

    await _drive_ce_sck(
        master, 0, csp_gap_ns=LEGAL_GAP_NS, rise_to_ce_ns=LEGAL_GAP_NS,
        sck_high_ns=None,
    )
    # Let the monitor observe CE#'s rise (SCK still high) before reparking.
    await Timer(1, unit="ns")
    master._set_bit(UIO_SCK_BIT, 0)
    master._apply()
    await Timer(1, unit="ns")

    dispose_run(
        bringup.ce,
        test="TC-TERM-SCK-NOT-FROZEN",
        expect_fail=[expect(Q_TERM, count=1)],
        log=dut._log,
        repro=repro,
    )
    _assert_detail(
        bringup.ce, Q_TERM, test="TC-TERM-SCK-NOT-FROZEN",
        detail_substr="SCK was not frozen low at CE# rise",
    )
    dut._log.info(
        "TC-TERM-SCK-NOT-FROZEN recorded: %s", bringup.ce.violations_for(Q_TERM)[0]
    )
    dut._log.info("qspi_timing_delay_term passed: TC-TERM-SCK-NOT-FROZEN")
