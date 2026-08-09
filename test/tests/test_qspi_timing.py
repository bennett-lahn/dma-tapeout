"""Coarse CE# timing negatives for ``Q-CEM`` and ``Q-CPH`` (M1).

Each case uses the CE# timing monitor from :func:`common.bringup.bring_up_top`
(with directed thresholds attached on the returned handle) and injects one CE#
pulse/gap fault that must produce exactly the matching ID once. Stimulus takes
``BUS_GNT`` and drives the MCU pass-through pins after reset release so findings
are ordinary ``Q-*`` fails, not ``RESET-TRUNCATED``.

Thresholds for the directed faults are shortened (``tCEM``=100 ns) so the
module stays fast; the production defaults (4 us / 18 ns) remain what smoke
and legal traffic use via :func:`start_ce_timing_monitor` with no overrides.

Dispose uses :func:`common.dispose.dispose_run` on the CE monitor only: bare
CE# pulses without a framed QPI transfer are outside the model / pin catalogs
this suite judges.

Test-case IDs:
    TC-CEM-BASELINE
    TC-CEM-PULSE
    TC-CPH-GAP
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.bringup import bring_up_top
from common.config import parse_run_config
from common.dispose import dispose_run, expect
from common.host import UIO_PSRAM_CE_BITS, QpiPassthroughMaster, assert_bus_req
from monitors.timing import Q_CEM, Q_CPH, start_ce_timing_monitor

FILL = 0x00

# Directed-test thresholds: short enough to keep the module fast, still above
# the master's half-SCK park gaps used by the legal baseline.
DIRECTED_TCEM_NS = 100.0
DIRECTED_TCPH_NS = 18.0
LEGAL_GAP_NS = 25.0  # > tCPH
SHORT_GAP_NS = 5.0  # < tCPH
LONG_PULSE_NS = 150.0  # > directed tCEM


async def _await_bus_gnt(dut, *, cycles: int = 32) -> None:
    await assert_bus_req(dut, hold=True)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> 1) & 1:
            return
    raise AssertionError("BUS_GNT did not assert after BUS_REQ")


async def _bring_up(dut):
    """Shared top bring-up, directed CE# monitor, BUS_GNT, parked MCU master.

    ``bring_up_top`` has no ``tcem_ns`` / ``tcph_ns`` hook, so the always-on CE
    monitor is left off and a directed-threshold twin is attached on the handle
    before stimulus. Other always-on catalogs stay off: this suite only judges
    ``Q-CEM`` / ``Q-CPH``.
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
    bringup.ce = start_ce_timing_monitor(
        dut,
        strict=False,
        tcem_ns=DIRECTED_TCEM_NS,
        tcph_ns=DIRECTED_TCPH_NS,
    )
    await _await_bus_gnt(dut)

    master = QpiPassthroughMaster(dut)
    await master.park()
    bringup.clear()
    return bringup, master


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_timing TEST_FILTER={test}"
    ).format(level=config["level"], sim=config["sim"], seed=config["seed"], test=test)


def _assert_detail(ce, check_id: str, *, test: str, detail_substr: str) -> None:
    """Keep the directed detail substrings the M1 cases asserted."""
    events = ce.violations_for(check_id)
    assert events, f"{test}: missing {check_id} after dispose"
    assert detail_substr in events[0].detail, (
        f"{test}: missing {detail_substr!r} in {events[0].detail}"
    )


async def _pulse_ce(master: QpiPassthroughMaster, device: int, low_ns: float) -> None:
    """Assert one RAM CE# for *low_ns*, then raise both CE# with no extra park wait."""
    master._set_bit(UIO_PSRAM_CE_BITS[device], 0)
    master._apply()
    await Timer(low_ns, unit="ns")
    for ce_bit in UIO_PSRAM_CE_BITS:
        master._set_bit(ce_bit, 1)
    master._apply()


@cocotb.test()
async def qspi_timing_cem_cph(dut):
    """TC-CEM-BASELINE, TC-CEM-PULSE, and TC-CPH-GAP in one cocotb entry."""
    config = parse_run_config()
    dut._log.info(
        "SEED=%d LEVEL=%s SIM=%s", config["seed"], config["level"], config["sim"]
    )

    # -- TC-CEM-BASELINE: short legal pulses with legal CE# high gaps --------
    dut._log.info(_repro(config, "baseline"))
    bringup, master = await _bring_up(dut)
    await _pulse_ce(master, 0, low_ns=40.0)
    await Timer(LEGAL_GAP_NS, unit="ns")
    await _pulse_ce(master, 1, low_ns=40.0)
    await Timer(LEGAL_GAP_NS, unit="ns")
    dispose_run(
        bringup.ce,
        test="TC-CEM-BASELINE",
        log=dut._log,
        repro=_repro(config, "baseline"),
    )

    # -- TC-CEM-PULSE: hold CE# low past directed tCEM → Q-CEM once -----------
    dut._log.info(_repro(config, "cem"))
    bringup.clear()
    await _pulse_ce(master, 0, low_ns=LONG_PULSE_NS)
    await Timer(1, unit="ns")
    dispose_run(
        bringup.ce,
        test="TC-CEM-PULSE",
        expect_fail=[expect(Q_CEM, count=1)],
        log=dut._log,
        repro=_repro(config, "cem"),
    )
    _assert_detail(
        bringup.ce, Q_CEM, test="TC-CEM-PULSE", detail_substr="exceeds tCEM"
    )
    dut._log.info("TC-CEM-PULSE recorded: %s", bringup.ce.violations_for(Q_CEM)[0])

    # Legal gap so the next directed case's fall is not also a CPH fault.
    await Timer(LEGAL_GAP_NS, unit="ns")

    # -- TC-CPH-GAP: CE# high for less than tCPH → Q-CPH once ----------------
    dut._log.info(_repro(config, "cph"))
    bringup.clear()
    await _pulse_ce(master, 0, low_ns=20.0)
    await Timer(SHORT_GAP_NS, unit="ns")
    await _pulse_ce(master, 1, low_ns=20.0)
    await Timer(1, unit="ns")
    dispose_run(
        bringup.ce,
        test="TC-CPH-GAP",
        expect_fail=[expect(Q_CPH, count=1)],
        log=dut._log,
        repro=_repro(config, "cph"),
    )
    _assert_detail(bringup.ce, Q_CPH, test="TC-CPH-GAP", detail_substr="< tCPH")
    dut._log.info("TC-CPH-GAP recorded: %s", bringup.ce.violations_for(Q_CPH)[0])

    dut._log.info("TC-CEM/CPH suite passed: BASELINE, CEM-PULSE, CPH-GAP")
