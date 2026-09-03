"""Pure-Python unit tests for ``common.injection`` planning helpers.

No cocotb import: these run under pytest without a simulator. DUT drivers
(``jitter_start``, ``inject_bus_req``, ``pulse_reset``) are stub-called later
by ``tests.test_dma_random``.

Catalog: ``docs/llm/verification/08-stimulus-and-coverage.md`` (Asynchronous
START phase jitter, BUS_REQ injection model, Determinism child streams).
"""

import ast
from pathlib import Path

import pytest

from common.injection import (
    BUS_REQ_EXCLUDED_STATES,
    CAPTURE_REQUIRED,
    CAPTURE_UNCERTAIN,
    CTRL_STATE_BY_CODE,
    FORBID,
    INJECTION_STREAMS,
    LANDING_FINAL,
    LANDING_MIDDLE,
    LANDING_START,
    PHASE_EARLY,
    PHASE_LATE,
    PHASE_NEAR_EDGE_AFTER,
    PHASE_NEAR_EDGE_BEFORE,
    PHASE_ON_EDGE,
    QPI_STATE_BY_CODE,
    REQUIRE,
    REVIEW,
    START_PHASE_BINS,
    STREAM_BUS_REQ,
    STREAM_RESET,
    STREAM_START,
    SYNC_LATENCY_CYCLES,
    InjectionError,
    InjectionPlanner,
    StartPulseRecord,
    capture_required_hold_ns,
    capture_uncertain_hold_ns,
    classify_start_capture,
    classify_start_phase,
    has_live_ce_monitor,
    landing_offset_cycles,
    offset_for_phase_bin,
    require_reset_truncated_policy,
    resolve_clk_period_ns,
    resolve_ctrl_state,
    resolve_qpi_phase,
    start_pulse_width_ns,
)
from common.seeds import child_random

INJECTION_PATH = Path(__file__).resolve().parents[1] / "common" / "injection.py"


def test_module_has_no_top_level_cocotb_or_host_import():
    tree = ast.parse(INJECTION_PATH.read_text(encoding="utf-8"))
    blocked = ("cocotb", "common.host", "monitors.handshake", "common.dispose", "common.bringup")
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(alias.name == name or alias.name.startswith(name + ".") for name in blocked)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in blocked
            assert not module.startswith("cocotb")


def test_child_stream_names_match_catalog():
    assert INJECTION_STREAMS == ("start", "bus_req", "reset")
    assert STREAM_START == "start"
    assert STREAM_BUS_REQ == "bus_req"
    assert STREAM_RESET == "reset"


def test_child_streams_are_independent():
    planner = InjectionPlanner(17)
    start_first = planner.stream(STREAM_START).random()
    bus_only = InjectionPlanner(17).stream(STREAM_BUS_REQ).random()
    reset_only = InjectionPlanner(17).stream(STREAM_RESET).random()
    planner.stream(STREAM_START).random()
    assert planner.stream(STREAM_BUS_REQ).random() == bus_only
    assert planner.stream(STREAM_RESET).random() == reset_only
    fresh = InjectionPlanner(17)
    assert fresh.stream(STREAM_START).random() == start_first
    assert child_random(17, STREAM_START).random() == start_first


def test_injection_streams_independent_across_multiple_items():
    """cov-refu-06: replay several items; perturb START, BUS_REQ, and reset independently."""

    def collect(planner):
        starts = [
            planner.plan_start(capture=CAPTURE_REQUIRED).to_manifest() for _ in range(3)
        ]
        buses = [planner.plan_bus_req(target_state="FETCH").to_manifest() for _ in range(3)]
        resets = [planner.plan_reset().to_manifest() for _ in range(3)]
        return starts, buses, resets

    seed = 23
    baseline = collect(InjectionPlanner(seed))

    start_only = InjectionPlanner(seed)
    for _ in range(5):
        start_only.stream(STREAM_START).random()
    starts, buses, resets = collect(start_only)
    assert buses == baseline[1]
    assert resets == baseline[2]
    assert starts != baseline[0]

    bus_only = InjectionPlanner(seed)
    for _ in range(5):
        bus_only.stream(STREAM_BUS_REQ).random()
    starts, buses, resets = collect(bus_only)
    assert starts == baseline[0]
    assert resets == baseline[2]
    assert buses != baseline[1]

    reset_only = InjectionPlanner(seed)
    for _ in range(5):
        reset_only.stream(STREAM_RESET).random()
    starts, buses, resets = collect(reset_only)
    assert starts == baseline[0]
    assert buses == baseline[1]
    assert resets != baseline[2]


def test_same_seed_replays_start_plans():
    first = [InjectionPlanner(9).plan_start(capture=CAPTURE_REQUIRED).to_manifest() for _ in range(1)]
    second = [InjectionPlanner(9).plan_start(capture=CAPTURE_REQUIRED).to_manifest() for _ in range(1)]
    assert first == second
    other = InjectionPlanner(10).plan_start(capture=CAPTURE_REQUIRED).to_manifest()
    assert other != first[0]


def test_classify_start_phase_bins():
    period = 10.0
    assert classify_start_phase(0.0, period) == PHASE_ON_EDGE
    assert classify_start_phase(10.0, period) == PHASE_ON_EDGE
    assert classify_start_phase(0.5, period) == PHASE_NEAR_EDGE_AFTER
    assert classify_start_phase(2.5, period) == PHASE_EARLY
    assert classify_start_phase(7.5, period) == PHASE_LATE
    assert classify_start_phase(9.5, period) == PHASE_NEAR_EDGE_BEFORE


@pytest.mark.parametrize("phase_bin", START_PHASE_BINS)
def test_offset_for_phase_bin_round_trips(phase_bin):
    offset = offset_for_phase_bin(phase_bin, 10.0)
    assert classify_start_phase(offset, 10.0) == phase_bin


def test_capture_required_hold_covers_three_periods_after_first_sample():
    period = 10.0
    hold = capture_required_hold_ns(2.0, period)
    to_first_sample = period - 2.0
    assert hold == to_first_sample + 3.0 * period
    assert hold >= 3.0 * period


def test_capture_uncertain_hold_is_sub_period():
    assert 0.0 < capture_uncertain_hold_ns(10.0) < 10.0
    rng = child_random(3, STREAM_START)
    for _ in range(20):
        assert 0.0 < capture_uncertain_hold_ns(10.0, rng) < 10.0


def test_plan_start_capture_classes():
    planner = InjectionPlanner(4)
    required = planner.plan_start(capture=CAPTURE_REQUIRED, phase=PHASE_EARLY)
    assert required.capture == CAPTURE_REQUIRED
    assert required.hold_ns >= capture_required_hold_ns(required.assert_phase_ns, 10.0)
    assert start_pulse_width_ns(required) == required.hold_ns + required.deassert_phase_ns
    uncertain = planner.plan_start(capture=CAPTURE_UNCERTAIN, phase=PHASE_ON_EDGE)
    assert uncertain.capture == CAPTURE_UNCERTAIN
    assert uncertain.hold_ns < uncertain.clk_period_ns
    assert uncertain.deassert_phase_ns == 0.0
    assert start_pulse_width_ns(uncertain) < uncertain.clk_period_ns


def test_uncertain_width_ignores_deassert_jitter():
    """Deassert phase on a short hold must not create two sync edges."""
    period = 10.0
    record = StartPulseRecord(
        capture=CAPTURE_UNCERTAIN,
        phase_bin=PHASE_NEAR_EDGE_BEFORE,
        assert_phase_ns=9.5,
        deassert_phase_ns=9.0,
        hold_ns=4.0,
        clk_period_ns=period,
    )
    width = start_pulse_width_ns(record)
    assert width < period
    assert width == min(4.0, 0.49 * period)
    planner = InjectionPlanner(11)
    for _ in range(40):
        planned = planner.plan_start(capture=CAPTURE_UNCERTAIN)
        assert start_pulse_width_ns(planned) < planned.clk_period_ns
        assert planned.deassert_phase_ns == 0.0


def test_planner_threads_clk_period_ns():
    period = 15.15
    planner = InjectionPlanner(1, clk_period_ns=period)
    start = planner.plan_start(capture=CAPTURE_REQUIRED, phase=PHASE_EARLY)
    assert start.clk_period_ns == period
    assert 0.0 <= start.assert_phase_ns < period
    assert start.hold_ns >= capture_required_hold_ns(start.assert_phase_ns, period)
    bus = planner.plan_bus_req(target_state="FETCH")
    assert bus.clk_period_ns == period
    assert resolve_clk_period_ns(period) == period
    assert resolve_clk_period_ns(None) == 10.0
    assert SYNC_LATENCY_CYCLES == 2


def test_plan_bus_req_rejects_stall():
    planner = InjectionPlanner(5)
    with pytest.raises(InjectionError, match="STALL"):
        planner.plan_bus_req(target_state="STALL")
    assert "STALL" in BUS_REQ_EXCLUDED_STATES


def test_one_cycle_region_rejects_middle_and_final_landing():
    """tb-help-05 / tb-help-06: NEW_FETCH cannot use middle/final landing."""
    planner = InjectionPlanner(6)
    with pytest.raises(InjectionError, match="one-cycle"):
        planner.plan_bus_req(target_state="NEW_FETCH", landing=LANDING_MIDDLE)
    with pytest.raises(InjectionError, match="one-cycle"):
        planner.plan_bus_req(target_state="UPDATE", landing=LANDING_FINAL)
    with pytest.raises(InjectionError, match="one-cycle"):
        landing_offset_cycles(LANDING_MIDDLE, one_cycle=True)
    assert landing_offset_cycles(LANDING_START, one_cycle=True) == 0
    start = planner.plan_bus_req(target_state="NEW_FETCH", landing=LANDING_START)
    assert start.landing == LANDING_START
    auto = planner.plan_bus_req(target_state="NEW_OP")
    assert auto.landing == LANDING_START


def test_capture_required_zero_edges_is_an_error():
    """tb-help-02: capture-required START with zero sync edges is illegal."""
    record = StartPulseRecord(
        capture=CAPTURE_REQUIRED,
        phase_bin=PHASE_EARLY,
        assert_phase_ns=2.5,
        deassert_phase_ns=0.0,
        hold_ns=30.0,
        clk_period_ns=10.0,
        sync_edges=0,
    )
    with pytest.raises(InjectionError, match="zero synchronized"):
        classify_start_capture(record)
    record.sync_edges = 1
    classify_start_capture(record)
    uncertain = StartPulseRecord(
        capture=CAPTURE_UNCERTAIN,
        phase_bin=PHASE_EARLY,
        assert_phase_ns=2.5,
        deassert_phase_ns=0.0,
        hold_ns=4.0,
        clk_period_ns=10.0,
        sync_edges=0,
        idle_uncaptured=True,
    )
    classify_start_capture(uncertain)


def test_plan_bus_req_targets_and_landings():
    planner = InjectionPlanner(6)
    record = planner.plan_bus_req(target_state="FETCH", target_phase="command", landing=LANDING_MIDDLE)
    assert record.target_state == "FETCH"
    assert record.target_phase == "command"
    assert record.landing == LANDING_MIDDLE
    assert record.stream == STREAM_BUS_REQ
    assert record.host_hold_cycles >= 1
    drawn = {InjectionPlanner(6).plan_bus_req(target_state=2).landing for _ in range(1)}
    assert drawn == {InjectionPlanner(6).plan_bus_req(target_state=2).landing}


def test_resolve_state_and_phase_tables():
    assert resolve_ctrl_state("IDLE") == 0
    assert resolve_ctrl_state("SYS_CTRL_IDLE") == 0
    assert resolve_ctrl_state("NEW_FETCH") == 1
    assert resolve_ctrl_state(4) == 4
    assert resolve_qpi_phase("command") == (2, 3)
    assert resolve_qpi_phase("READ_DATA") == (6,)
    assert resolve_qpi_phase("read data") == (6,)
    assert resolve_qpi_phase(1) == (1,)
    assert set(CTRL_STATE_BY_CODE.values()) == {
        "SYS_CTRL_IDLE",
        "NEW_FETCH",
        "FETCH",
        "NEW_OP",
        "READ",
        "WRITE",
        "UPDATE",
        "STALL",
    }
    assert len(QPI_STATE_BY_CODE) == 10
    with pytest.raises(InjectionError):
        resolve_ctrl_state("not-a-state")
    with pytest.raises(InjectionError):
        resolve_qpi_phase("not-a-phase")


def test_reset_truncated_never_defaults_to_forbid():
    assert require_reset_truncated_policy(None) == REVIEW
    assert require_reset_truncated_policy(REVIEW) == REVIEW
    assert require_reset_truncated_policy(REQUIRE) == REQUIRE
    with pytest.raises(InjectionError, match="never 'forbid'"):
        require_reset_truncated_policy(FORBID)
    with pytest.raises(InjectionError, match="never 'forbid'"):
        require_reset_truncated_policy(FORBID, live_ce=False)
    with pytest.raises(InjectionError):
        require_reset_truncated_policy("ignore")


def test_plan_reset_records_policy_for_dispose():
    planner = InjectionPlanner(8)
    record = planner.plan_reset(target_state="WRITE", target_phase="wait", reset_truncated=REQUIRE)
    assert record.reset_truncated == REQUIRE
    assert record.release is False
    assert record.stream == STREAM_RESET
    assert record.target_state == "WRITE"
    assert record.hold_cycles >= 1
    default = planner.plan_reset()
    assert default.reset_truncated == REVIEW
    with pytest.raises(InjectionError):
        planner.plan_reset(reset_truncated=FORBID)


def test_has_live_ce_monitor():
    assert has_live_ce_monitor(None) is False

    class _Ce:
        blocked = False

    class _BringUp:
        ce = _Ce()

    assert has_live_ce_monitor(_BringUp()) is True

    class _Blocked:
        ce = type("C", (), {"blocked": True})()

    assert has_live_ce_monitor(_Blocked()) is False


def test_unknown_stream_and_landing_raise():
    planner = InjectionPlanner(1)
    with pytest.raises(InjectionError, match="unknown injection stream"):
        planner.stream("descriptors")
    with pytest.raises(InjectionError, match="landing"):
        planner.plan_bus_req(landing="sometime")
    with pytest.raises(InjectionError):
        InjectionPlanner(1, clk_period_ns=0.0)
