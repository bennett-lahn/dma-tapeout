"""Coarse CE# timing negatives for ``Q-CEM`` and ``Q-CPH`` (M1).

Each case uses :func:`monitors.timing.start_ce_timing_monitor` and injects one
CE# pulse/gap fault that must produce exactly the matching ID once. Stimulus
takes ``BUS_GNT`` and drives the MCU pass-through pins after reset release so
findings are ordinary ``Q-*`` fails, not ``RESET-TRUNCATED``.

Thresholds for the directed faults are shortened (``tCEM``=100 ns) so the
module stays fast; the production defaults (4 us / 18 ns) remain what smoke
and legal traffic use via :func:`start_ce_timing_monitor` with no overrides.

Test-case IDs:
    TC-CEM-BASELINE
    TC-CEM-PULSE
    TC-CPH-GAP
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer

from common.clocks import apply_reset, start_clock
from common.config import parse_run_config
from common.host import UIO_PSRAM_CE_BITS, QpiPassthroughMaster, assert_bus_req
from models.psram import attach_dual_psram
from monitors.timing import Q_CEM, Q_CPH, start_ce_timing_monitor

FILL = 0x00

# Directed-test thresholds: short enough to keep the module fast, still above
# the master's half-SCK park gaps used by the legal baseline.
DIRECTED_TCEM_NS = 100.0
DIRECTED_TCPH_NS = 18.0
LEGAL_GAP_NS = 25.0  # > tCPH
SHORT_GAP_NS = 5.0  # < tCPH
LONG_PULSE_NS = 150.0  # > directed tCEM

_ATTACHED: list = []


async def _await_bus_gnt(dut, *, cycles: int = 32) -> None:
    await assert_bus_req(dut, hold=True)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        if (int(dut.uo_out.value) >> 1) & 1:
            return
    raise AssertionError("BUS_GNT did not assert after BUS_REQ")


async def _bring_up(dut, **timing_kwargs):
    """Attach models, release reset, take BUS_GNT, park the MCU, start CE timing."""
    for device in _ATTACHED:
        device.agent.stop()
    _ATTACHED.clear()

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    dut.fault_uio_drive.value = 0
    dut.fault_uio_oe.value = 0

    psram0, psram1 = attach_dual_psram(dut, fill=FILL)
    _ATTACHED.extend([psram0, psram1])

    ce = start_ce_timing_monitor(
        dut,
        strict=False,
        tcem_ns=timing_kwargs.pop("tcem_ns", DIRECTED_TCEM_NS),
        tcph_ns=timing_kwargs.pop("tcph_ns", DIRECTED_TCPH_NS),
        **timing_kwargs,
    )
    await start_clock(dut)
    await apply_reset(dut)
    await _await_bus_gnt(dut)

    master = QpiPassthroughMaster(dut)
    await master.park()
    ce.clear()
    return psram0, psram1, master, ce


def _repro(config: dict, test: str) -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL={level} SIM={sim} SEED={seed} "
        "COCOTB_TEST_MODULES=tests.test_qspi_timing TEST_FILTER={test}"
    ).format(level=config["level"], sim=config["sim"], seed=config["seed"], test=test)


def _assert_clean(ce, *, test: str, log=None) -> None:
    assert not ce.events, (
        f"{test}: unexpected CE timing events: " + "; ".join(str(e) for e in ce.events)
    )
    assert not ce.violations, (
        f"{test}: unexpected CE timing violations: " + "; ".join(ce.violations)
    )
    if log is not None:
        log.info("%s clean: %s", test, ce.summary())


def _assert_only(ce, check_id: str, *, test: str, detail_substr: str = "") -> list:
    """Require exactly one event for *check_id* and no other CE timing IDs."""
    events = ce.violations_for(check_id)
    others = [event for event in ce.events if event.check_id != check_id]
    assert len(events) == 1, (
        f"{test}: expected exactly one {check_id}, observed {ce.summary()}: "
        + "; ".join(str(e) for e in ce.events)
    )
    assert not others, (
        f"{test}: unexpected extra IDs {[e.check_id for e in others]}: "
        + "; ".join(str(e) for e in others)
    )
    event = events[0]
    assert not event.reset_truncated, f"{test}: finding was RESET-TRUNCATED: {event}"
    if detail_substr:
        assert detail_substr in event.detail, (
            f"{test}: missing {detail_substr!r} in {event.detail}"
        )
    return events


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
    _psram0, _psram1, master, ce = await _bring_up(dut)
    await _pulse_ce(master, 0, low_ns=40.0)
    await Timer(LEGAL_GAP_NS, unit="ns")
    await _pulse_ce(master, 1, low_ns=40.0)
    await Timer(LEGAL_GAP_NS, unit="ns")
    _assert_clean(ce, test="TC-CEM-BASELINE", log=dut._log)

    # -- TC-CEM-PULSE: hold CE# low past directed tCEM → Q-CEM once -----------
    dut._log.info(_repro(config, "cem"))
    ce.clear()
    await _pulse_ce(master, 0, low_ns=LONG_PULSE_NS)
    await Timer(1, unit="ns")
    events = _assert_only(
        ce,
        Q_CEM,
        test="TC-CEM-PULSE",
        detail_substr="exceeds tCEM",
    )
    dut._log.info("TC-CEM-PULSE recorded: %s", events[0])

    # Legal gap so the next directed case's fall is not also a CPH fault.
    await Timer(LEGAL_GAP_NS, unit="ns")

    # -- TC-CPH-GAP: CE# high for less than tCPH → Q-CPH once ----------------
    dut._log.info(_repro(config, "cph"))
    ce.clear()
    await _pulse_ce(master, 0, low_ns=20.0)
    await Timer(SHORT_GAP_NS, unit="ns")
    await _pulse_ce(master, 1, low_ns=20.0)
    await Timer(1, unit="ns")
    events = _assert_only(
        ce,
        Q_CPH,
        test="TC-CPH-GAP",
        detail_substr="< tCPH",
    )
    dut._log.info("TC-CPH-GAP recorded: %s", events[0])

    dut._log.info("TC-CEM/CPH suite passed: BASELINE, CEM-PULSE, CPH-GAP")
