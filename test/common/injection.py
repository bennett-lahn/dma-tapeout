"""Phase-jittered START and targeted BUS_REQ / reset injection.

Catalog: ``docs/llm/verification/08-stimulus-and-coverage.md`` sections
"Asynchronous START phase jitter", "BUS_REQ injection model", and Determinism
(child streams ``start`` / ``bus_req`` / ``reset``).

Planning is pure Python (``SEED`` child streams via :func:`common.seeds.child_random`).
DUT drivers import :mod:`common.host` and the handshake name tables lazily so
this module stays importable without cocotb. Legal-chain random
(``tests.test_dma_random``) is expected to stub-call the async helpers later.

M3 lifecycle: any window that forces ``rst_n=0`` with a live CE monitor must
pass ``reset_truncated=REVIEW`` or ``REQUIRE``, never the dispose default
``FORBID``. ``RESET-TRUNCATED`` is a timing observation explained by reset
OE/state convergence. ``Q-LAUNCH`` (driven SIO/OE changes only while SCK is
low, with modeled setup/hold) applies only while the ASIC drives SCK.
"""

from dataclasses import asdict, dataclass, field
import os
import random

from common.seeds import child_random
from common.constants import (
    BUS_GNT_MASK,
    DEFAULT_CLOCK_PERIOD_NS,
    DEFAULT_RESET_CYCLES,
    FORBID,
    GRANT_TIMEOUT_CYCLES,
    QSPI_ENGINE_STATES,
    REQUIRE,
    REVIEW,
    STATE_TIMEOUT_CYCLES,
    STREAM_BUS_REQ,
    STREAM_RESET,
    STREAM_START,
    SYS_CONTROL_STATES,
)

INJECTION_STREAMS = (STREAM_START, STREAM_BUS_REQ, STREAM_RESET)

CAPTURE_REQUIRED = "capture_required"
CAPTURE_UNCERTAIN = "capture_uncertain"
CAPTURE_CLASSES = (CAPTURE_REQUIRED, CAPTURE_UNCERTAIN)

# COV-START-PHASE bins (raw START assertion phase).
PHASE_EARLY = "early"
PHASE_NEAR_EDGE_BEFORE = "near_edge_before"
PHASE_ON_EDGE = "on_edge"
PHASE_NEAR_EDGE_AFTER = "near_edge_after"
PHASE_LATE = "late"
START_PHASE_BINS = (
    PHASE_EARLY,
    PHASE_NEAR_EDGE_BEFORE,
    PHASE_ON_EDGE,
    PHASE_NEAR_EDGE_AFTER,
    PHASE_LATE,
)
FOCUSED_PHASE_BINS = (PHASE_NEAR_EDGE_BEFORE, PHASE_ON_EDGE, PHASE_NEAR_EDGE_AFTER)

LANDING_START = "start"
LANDING_MIDDLE = "middle"
LANDING_FINAL = "final"
BUS_LANDINGS = (LANDING_START, LANDING_MIDDLE, LANDING_FINAL)

# reset_truncated policies (same strings as common.dispose). This helper never
# defaults to FORBID: a forced rst_n=0 window must REVIEW or REQUIRE.
RESET_TRUNCATED_POLICIES = (REVIEW, REQUIRE)

DEFAULT_CLK_PERIOD_NS = float(DEFAULT_CLOCK_PERIOD_NS)
DEFAULT_RESET_HOLD_CYCLES = DEFAULT_RESET_CYCLES
SYNC_LATENCY_CYCLES = 2
CTRL_STATE_BY_CODE = SYS_CONTROL_STATES
QPI_STATE_BY_CODE = QSPI_ENGINE_STATES
# Capture-uncertain raw width stays strictly under one clk so two sampling
# edges are impossible even if deassert jitter is present on the record.
UNCERTAIN_WIDTH_RATIO = 0.49
LANDING_CYCLES = {
    LANDING_START: 0,
    LANDING_MIDDLE: 2,
    LANDING_FINAL: 6,
}

# Catalog / coverage aliases accepted by the targeted injectors.
# One-cycle controller/QPI bins cannot use middle/final landing offsets.
ONE_CYCLE_CTRL_STATES = frozenset({"NEW_FETCH", "NEW_OP", "UPDATE"})
ONE_CYCLE_QPI_PHASES = frozenset({"CS_ON", "SEND_CMD_1", "SEND_CMD_2"})
_CTRL_ALIASES = {
    "IDLE": "SYS_CTRL_IDLE",
    "SYS_CTRL_IDLE": "SYS_CTRL_IDLE",
    "NEW_FETCH": "NEW_FETCH",
    "FETCH": "FETCH",
    "NEW_OP": "NEW_OP",
    "READ": "READ",
    "WRITE": "WRITE",
    "UPDATE": "UPDATE",
    "STALL": "STALL",
}
_QPI_ALIASES = {
    "QSPI_IDLE": ("QSPI_IDLE",),
    "idle_pad": ("QSPI_IDLE", "CS_ON"),
    "CS_ON": ("CS_ON",),
    "command": ("SEND_CMD_1", "SEND_CMD_2"),
    "SEND_CMD_1": ("SEND_CMD_1",),
    "SEND_CMD_2": ("SEND_CMD_2",),
    "address": ("SEND_ADDR",),
    "SEND_ADDR": ("SEND_ADDR",),
    "wait": ("WAIT",),
    "WAIT": ("WAIT",),
    "read_data": ("READ_DATA",),
    "read data": ("READ_DATA",),
    "READ_DATA": ("READ_DATA",),
    "write_data": ("WRITE_DATA",),
    "write data": ("WRITE_DATA",),
    "WRITE_DATA": ("WRITE_DATA",),
    "SCLK_OFF": ("SCLK_OFF",),
    "CS_OFF": ("CS_OFF",),
    "termination": ("SCLK_OFF", "CS_OFF"),
}

# COV-BUS-STATE excludes STALL: reaching STALL already requires the request.
BUS_REQ_EXCLUDED_STATES = frozenset({"STALL"})

_HOST_HOLD_WEIGHTS = ((1, 40), (2, 25), (4, 20), (8, 10), (16, 5))
_RESET_HOLD_WEIGHTS = ((3, 35), (4, 25), (5, 20), (6, 12), (8, 8))
_LANDING_WEIGHTS = ((LANDING_START, 40), (LANDING_MIDDLE, 35), (LANDING_FINAL, 25))

class InjectionError(ValueError):
    """Illegal injection argument or drifted handshake table."""


def _weighted(rng: random.Random, pairs) -> object:
    items, weights = zip(*pairs)
    return rng.choices(items, weights=weights, k=1)[0]


def landing_offset_cycles(landing: str, *, one_cycle: bool = False) -> int:
    """Return the extra wait for a landing bin; reject middle/final on 1-cycle regions."""
    if landing not in BUS_LANDINGS:
        raise InjectionError(f"landing must be one of {BUS_LANDINGS}, got {landing!r}")
    if one_cycle and landing in (LANDING_MIDDLE, LANDING_FINAL):
        raise InjectionError(
            f"landing={landing} is illegal on a one-cycle BUS_REQ region; use {LANDING_START}"
        )
    return LANDING_CYCLES[landing]


def _region_is_one_cycle(target_state, target_phase) -> bool:
    return target_state in ONE_CYCLE_CTRL_STATES or target_phase in ONE_CYCLE_QPI_PHASES


def classify_start_capture(record: "StartPulseRecord") -> None:
    """Enforce capture-required vs uncertain synchronized-edge counts."""
    edges = 0 if record.sync_edges is None else int(record.sync_edges)
    if record.capture == CAPTURE_REQUIRED and edges == 0:
        raise InjectionError(
            "capture-required START produced zero synchronized edges"
        )
    if record.capture == CAPTURE_UNCERTAIN and edges > 1:
        raise InjectionError(
            f"capture-uncertain START produced {edges} synchronized "
            "edges (at most one is legal)"
        )


def _check_bus_landing(record: "BusReqRecord") -> None:
    landing_offset_cycles(
        record.landing,
        one_cycle=_region_is_one_cycle(record.target_state, record.target_phase),
    )


def _near_edge_ns(period_ns: float) -> float:
    return min(1.0, period_ns * 0.15)


def resolve_clk_period_ns(value: "float | None" = None) -> float:
    """Return a positive clk period: explicit value, else ``CLK_PERIOD_NS``, else 10 ns.

    Bring-up today uses 10 ns. A 66 MHz clk is about 15.15 ns; every planner
    and driver must receive that period or phase bins and holds are wrong.
    """
    if value is None:
        raw = os.environ.get("CLK_PERIOD_NS")
        period = float(raw) if raw not in (None, "") else DEFAULT_CLK_PERIOD_NS
    else:
        period = float(value)
    if period <= 0:
        raise InjectionError(f"clk_period_ns must be positive, got {period}")
    return period


def start_pulse_width_ns(record: "StartPulseRecord") -> float:
    """Driven raw START width. Deassert jitter applies only to capture-required.

    Capture-uncertain pulses stay under one ``clk`` period so they cannot
    produce two synchronized edges.
    """
    period = float(record.clk_period_ns)
    if record.capture == CAPTURE_REQUIRED:
        return float(record.hold_ns) + float(record.deassert_phase_ns)
    return min(float(record.hold_ns), UNCERTAIN_WIDTH_RATIO * period)


def classify_start_phase(phase_ns: float, period_ns: float = DEFAULT_CLK_PERIOD_NS) -> str:
    """Map an assertion offset after a rising ``clk`` to a ``COV-START-PHASE`` bin."""
    if period_ns <= 0:
        raise InjectionError(f"clk_period_ns must be positive, got {period_ns}")
    phase = float(phase_ns) % float(period_ns)
    edge_eps = min(0.05, period_ns * 0.005)
    near = _near_edge_ns(period_ns)
    if phase <= edge_eps or (period_ns - phase) <= edge_eps:
        return PHASE_ON_EDGE
    if phase <= near:
        return PHASE_NEAR_EDGE_AFTER
    if phase >= period_ns - near:
        return PHASE_NEAR_EDGE_BEFORE
    if phase <= period_ns / 2.0:
        return PHASE_EARLY
    return PHASE_LATE


def offset_for_phase_bin(
    phase_bin: str,
    period_ns: float = DEFAULT_CLK_PERIOD_NS,
    rng: "random.Random | None" = None,
) -> float:
    """Return a representative (optionally jittered) offset for a phase bin."""
    if phase_bin not in START_PHASE_BINS:
        raise InjectionError(
            f"unknown START phase bin {phase_bin!r}; expected one of {START_PHASE_BINS}"
        )
    if period_ns <= 0:
        raise InjectionError(f"clk_period_ns must be positive, got {period_ns}")
    near = _near_edge_ns(period_ns)
    edge_eps = min(0.05, period_ns * 0.005)
    ranges = {
        PHASE_ON_EDGE: (0.0, 0.0),
        PHASE_NEAR_EDGE_AFTER: (edge_eps, near),
        PHASE_EARLY: (near, period_ns / 2.0),
        PHASE_LATE: (period_ns / 2.0 + edge_eps, period_ns - near),
        PHASE_NEAR_EDGE_BEFORE: (period_ns - near, period_ns - edge_eps),
    }
    low, high = ranges[phase_bin]
    if phase_bin == PHASE_ON_EDGE or high <= low:
        return 0.0
    if rng is None:
        return (low + high) / 2.0
    return rng.uniform(low, high)


def capture_required_hold_ns(
    assert_phase_ns: float, period_ns: float = DEFAULT_CLK_PERIOD_NS
) -> float:
    """Hold from assertion until three full ``clk`` periods after first sample.

    After a rising-edge alignment plus ``assert_phase_ns``, the next rising
    ``clk`` is the first possible sampling edge. Capture-required pulses stay
    high for at least three complete periods after that edge.
    """
    if period_ns <= 0:
        raise InjectionError(f"clk_period_ns must be positive, got {period_ns}")
    phase = float(assert_phase_ns) % float(period_ns)
    to_first_sample = period_ns - phase if phase > 0.0 else period_ns
    return to_first_sample + 3.0 * period_ns


def capture_uncertain_hold_ns(
    period_ns: float = DEFAULT_CLK_PERIOD_NS,
    rng: "random.Random | None" = None,
) -> float:
    """Return a sub-period hold (capture-uncertain: 0 or 1 synchronized edge)."""
    if period_ns <= 0:
        raise InjectionError(f"clk_period_ns must be positive, got {period_ns}")
    if rng is None:
        return 0.2 * period_ns
    return rng.uniform(0.1, 0.4) * period_ns


def resolve_ctrl_state(value) -> int:
    """Return the ``sys_control_state_t`` encoding for a name or code."""
    if isinstance(value, bool) or not isinstance(value, int):
        name = _CTRL_ALIASES.get(str(value), str(value))
        for code, label in CTRL_STATE_BY_CODE.items():
            if label == name:
                return code
        raise InjectionError(f"unknown controller state {value!r}")
    if value in CTRL_STATE_BY_CODE:
        return int(value)
    raise InjectionError(f"unknown controller state encoding {value!r}")


def resolve_qpi_phase(value) -> tuple:
    """Return one or more ``qspi_state_t`` encodings for a name, alias, or code."""
    if isinstance(value, bool) or not isinstance(value, int):
        names = _QPI_ALIASES.get(str(value))
        if names is None:
            raise InjectionError(f"unknown QPI phase {value!r}")
        codes = []
        reverse = {label: code for code, label in QPI_STATE_BY_CODE.items()}
        for name in names:
            if name not in reverse:
                raise InjectionError(f"unknown QPI phase name {name!r}")
            codes.append(reverse[name])
        return tuple(codes)
    if value in QPI_STATE_BY_CODE:
        return (int(value),)
    raise InjectionError(f"unknown QPI phase encoding {value!r}")


def require_reset_truncated_policy(
    policy: "str | None" = None, *, live_ce: bool = True
) -> str:
    """Return REVIEW or REQUIRE for a forced ``rst_n=0`` window.

    ``RESET-TRUNCATED`` (timing observation explained by reset OE/state
    convergence) must be reviewed or required. ``FORBID`` is the dispose
    default and is illegal here whenever this helper forces reset, including
    when a live CE monitor is attached (*live_ce*).
    """
    chosen = REVIEW if policy is None else policy
    if chosen == FORBID:
        context = " with a live CE monitor" if live_ce else ""
        raise InjectionError(
            "forced rst_n=0 windows"
            f"{context} must pass reset_truncated={REVIEW!r} or {REQUIRE!r}, "
            f"never {FORBID!r} (M3 lifecycle; RESET-TRUNCATED must be reviewed)"
        )
    if chosen not in RESET_TRUNCATED_POLICIES:
        raise InjectionError(
            f"reset_truncated must be {REVIEW!r} or {REQUIRE!r}, got {chosen!r}"
        )
    return chosen


def has_live_ce_monitor(bringup) -> bool:
    """True when *bringup* has a usable CE# timing monitor."""
    if bringup is None:
        return False
    ce = getattr(bringup, "ce", None)
    if ce is None:
        return False
    return not bool(getattr(ce, "blocked", False))


@dataclass
class StartPulseRecord:
    """One planned or driven raw START pulse (manifest-friendly)."""

    capture: str
    phase_bin: str
    assert_phase_ns: float
    deassert_phase_ns: float
    hold_ns: float
    clk_period_ns: float
    stream: str = STREAM_START
    assert_time_ns: "float | None" = None
    deassert_time_ns: "float | None" = None
    sync_edges: "int | None" = None
    idle_uncaptured: bool = False

    def to_manifest(self) -> dict:
        return asdict(self)


@dataclass
class BusReqRecord:
    """One planned or driven BUS_REQ injection (manifest-friendly)."""

    target_state: "str | None"
    target_phase: "str | None"
    landing: str
    assert_phase_ns: float
    host_hold_cycles: int
    clk_period_ns: float
    stream: str = STREAM_BUS_REQ
    observed_state: "str | None" = None
    observed_phase: "str | None" = None
    assert_time_ns: "float | None" = None
    grant_time_ns: "float | None" = None
    release_time_ns: "float | None" = None

    def to_manifest(self) -> dict:
        return asdict(self)


@dataclass
class ResetPulseRecord:
    """One planned or driven ``rst_n`` pulse (manifest-friendly)."""

    target_state: "str | None"
    target_phase: "str | None"
    reset_truncated: str
    hold_cycles: int
    release: bool
    stream: str = STREAM_RESET
    observed_state: "str | None" = None
    observed_phase: "str | None" = None
    assert_time_ns: "float | None" = None
    release_time_ns: "float | None" = None
    live_ce: bool = False

    def to_manifest(self) -> dict:
        return asdict(self)


@dataclass
class InjectionPlanner:
    """Owns independent ``start`` / ``bus_req`` / ``reset`` child streams.

    Constructed from the run ``SEED``. Adding a draw on one stream never
    perturbs the others. Does not read module-global random state.
    """

    seed: int
    clk_period_ns: "float | None" = None
    _streams: dict = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.clk_period_ns = resolve_clk_period_ns(self.clk_period_ns)
        self._streams = {name: child_random(int(self.seed), name) for name in INJECTION_STREAMS}

    def stream(self, name: str) -> random.Random:
        if name not in self._streams:
            raise InjectionError(
                f"unknown injection stream {name!r}; expected one of {INJECTION_STREAMS}"
            )
        return self._streams[name]

    def plan_start(
        self,
        *,
        capture: str = CAPTURE_REQUIRED,
        phase: "str | None" = None,
        deassert_phase: "str | None" = None,
        mode: str = "bins",
    ) -> StartPulseRecord:
        """Draw a capture-class START pulse from the ``start`` stream."""
        if capture not in CAPTURE_CLASSES:
            raise InjectionError(
                f"capture must be {CAPTURE_REQUIRED!r} or {CAPTURE_UNCERTAIN!r}, got {capture!r}"
            )
        rng = self.stream(STREAM_START)
        period = self.clk_period_ns
        if phase is None:
            if mode == "uniform":
                assert_phase = rng.uniform(0.0, period)
            elif mode == "focused":
                assert_phase = offset_for_phase_bin(rng.choice(FOCUSED_PHASE_BINS), period, rng)
            elif mode == "bins":
                assert_phase = offset_for_phase_bin(rng.choice(START_PHASE_BINS), period, rng)
            else:
                raise InjectionError(f"unknown START draw mode {mode!r}")
        else:
            assert_phase = offset_for_phase_bin(phase, period, rng)
        if capture == CAPTURE_REQUIRED:
            hold_ns = capture_required_hold_ns(assert_phase, period)
            if deassert_phase is None:
                deassert_ns = rng.uniform(0.0, period)
            else:
                deassert_ns = offset_for_phase_bin(deassert_phase, period, rng)
        else:
            hold_ns = capture_uncertain_hold_ns(period, rng)
            # Deassert-phase jitter is capture-required only. Adding it to a
            # short hold can span two sampling edges.
            deassert_ns = 0.0
        return StartPulseRecord(
            capture=capture,
            phase_bin=classify_start_phase(assert_phase, period),
            assert_phase_ns=assert_phase,
            deassert_phase_ns=deassert_ns,
            hold_ns=hold_ns,
            clk_period_ns=period,
        )

    def plan_bus_req(
        self,
        *,
        target_state: "str | int | None" = None,
        target_phase: "str | int | None" = None,
        landing: "str | None" = None,
    ) -> BusReqRecord:
        """Draw a targeted BUS_REQ injection from the ``bus_req`` stream."""
        rng = self.stream(STREAM_BUS_REQ)
        state_name = None
        if target_state is not None:
            code = resolve_ctrl_state(target_state)
            state_name = CTRL_STATE_BY_CODE[code]
            if state_name in BUS_REQ_EXCLUDED_STATES:
                raise InjectionError(
                    "BUS_REQ injection does not target STALL; reaching STALL "
                    "already requires the synchronized request (COV-BUS-STATE)"
                )
        phase_name = None
        if target_phase is not None:
            codes = resolve_qpi_phase(target_phase)
            phase_name = QPI_STATE_BY_CODE[codes[0]] if len(codes) == 1 else str(target_phase)
        if landing is None:
            if _region_is_one_cycle(state_name, phase_name):
                chosen_landing = LANDING_START
            else:
                chosen_landing = _weighted(rng, _LANDING_WEIGHTS)
        elif landing in BUS_LANDINGS:
            chosen_landing = landing
        else:
            raise InjectionError(
                f"landing must be one of {BUS_LANDINGS}, got {landing!r}"
            )
        record = BusReqRecord(
            target_state=state_name,
            target_phase=phase_name,
            landing=chosen_landing,
            assert_phase_ns=rng.uniform(0.0, self.clk_period_ns),
            host_hold_cycles=int(_weighted(rng, _HOST_HOLD_WEIGHTS)),
            clk_period_ns=self.clk_period_ns,
        )
        _check_bus_landing(record)
        return record

    def plan_reset(
        self,
        *,
        target_state: "str | int | None" = None,
        target_phase: "str | int | None" = None,
        reset_truncated: str = REVIEW,
        hold_cycles: "int | None" = None,
        release: bool = False,
        live_ce: bool = True,
    ) -> ResetPulseRecord:
        """Draw a targeted ``rst_n`` pulse from the ``reset`` stream."""
        policy = require_reset_truncated_policy(reset_truncated, live_ce=live_ce)
        rng = self.stream(STREAM_RESET)
        state_name = None
        if target_state is not None:
            state_name = CTRL_STATE_BY_CODE[resolve_ctrl_state(target_state)]
        phase_name = None
        if target_phase is not None:
            codes = resolve_qpi_phase(target_phase)
            phase_name = QPI_STATE_BY_CODE[codes[0]] if len(codes) == 1 else str(target_phase)
        if hold_cycles is None:
            hold_cycles = int(_weighted(rng, _RESET_HOLD_WEIGHTS))
        if hold_cycles < 1:
            raise InjectionError(f"hold_cycles must be >= 1, got {hold_cycles}")
        return ResetPulseRecord(
            target_state=state_name,
            target_phase=phase_name,
            reset_truncated=policy,
            hold_cycles=hold_cycles,
            release=release,
            live_ce=live_ce,
        )


def _fixed_start_plan(
    *,
    capture: str,
    phase: "str | None",
    clk_period_ns: "float | None",
) -> StartPulseRecord:
    """Deterministic plan for stub/directed calls that omit a planner."""
    if capture not in CAPTURE_CLASSES:
        raise InjectionError(
            f"capture must be {CAPTURE_REQUIRED!r} or {CAPTURE_UNCERTAIN!r}, got {capture!r}"
        )
    period = resolve_clk_period_ns(clk_period_ns)
    phase_bin = phase if phase is not None else PHASE_EARLY
    assert_phase = offset_for_phase_bin(phase_bin, period)
    if capture == CAPTURE_REQUIRED:
        hold_ns = capture_required_hold_ns(assert_phase, period)
    else:
        hold_ns = capture_uncertain_hold_ns(period)
    return StartPulseRecord(
        capture=capture,
        phase_bin=classify_start_phase(assert_phase, period),
        assert_phase_ns=assert_phase,
        deassert_phase_ns=0.0,
        hold_ns=hold_ns,
        clk_period_ns=period,
    )


def _resolve_start_plan(plan, planner, capture, phase, clk_period_ns) -> StartPulseRecord:
    if plan is not None:
        return plan
    if planner is not None:
        return planner.plan_start(capture=capture, phase=phase)
    return _fixed_start_plan(capture=capture, phase=phase, clk_period_ns=clk_period_ns)


def _resolve_bus_plan(plan, planner, target_state, target_phase, landing, clk_period_ns) -> BusReqRecord:
    period = resolve_clk_period_ns(clk_period_ns)
    if plan is not None:
        return plan
    if planner is not None:
        return planner.plan_bus_req(
            target_state=target_state, target_phase=target_phase, landing=landing
        )
    state_name = None
    if target_state is not None:
        state_name = CTRL_STATE_BY_CODE[resolve_ctrl_state(target_state)]
        if state_name in BUS_REQ_EXCLUDED_STATES:
            raise InjectionError(
                "BUS_REQ injection does not target STALL; reaching STALL "
                "already requires the synchronized request (COV-BUS-STATE)"
            )
    phase_name = None
    if target_phase is not None:
        codes = resolve_qpi_phase(target_phase)
        phase_name = QPI_STATE_BY_CODE[codes[0]] if len(codes) == 1 else str(target_phase)
    chosen = landing if landing is not None else LANDING_START
    if chosen not in BUS_LANDINGS:
        raise InjectionError(f"landing must be one of {BUS_LANDINGS}, got {chosen!r}")
    record = BusReqRecord(
        target_state=state_name,
        target_phase=phase_name,
        landing=chosen,
        assert_phase_ns=0.0,
        host_hold_cycles=1,
        clk_period_ns=period,
    )
    _check_bus_landing(record)
    return record


def _resolve_reset_plan(
    plan, planner, target_state, target_phase, reset_truncated, hold_cycles, release, live_ce
) -> ResetPulseRecord:
    if plan is not None:
        plan.reset_truncated = require_reset_truncated_policy(
            plan.reset_truncated, live_ce=live_ce
        )
        plan.live_ce = live_ce
        return plan
    if planner is not None:
        return planner.plan_reset(
            target_state=target_state,
            target_phase=target_phase,
            reset_truncated=reset_truncated,
            hold_cycles=hold_cycles,
            release=release,
            live_ce=live_ce,
        )
    policy = require_reset_truncated_policy(reset_truncated, live_ce=live_ce)
    state_name = None
    if target_state is not None:
        state_name = CTRL_STATE_BY_CODE[resolve_ctrl_state(target_state)]
    phase_name = None
    if target_phase is not None:
        codes = resolve_qpi_phase(target_phase)
        phase_name = QPI_STATE_BY_CODE[codes[0]] if len(codes) == 1 else str(target_phase)
    return ResetPulseRecord(
        target_state=state_name,
        target_phase=phase_name,
        reset_truncated=policy,
        hold_cycles=DEFAULT_RESET_HOLD_CYCLES if hold_cycles is None else hold_cycles,
        release=release,
        live_ce=live_ce,
    )


# -- DUT drivers (lazy cocotb / host / handshake) --------------------------


def _now_ns() -> float:
    from cocotb.simtime import get_sim_time

    return float(get_sim_time(unit="ns"))


def _sim_ns(value: float) -> float:
    """Round to 1 ps so ``Timer`` matches Icarus 1e-12 precision."""
    return round(float(value), 3)


async def _await_phase_ns(phase_ns: float) -> None:
    """Wait a sub-cycle offset; skip a zero-width Timer (on-edge)."""
    from cocotb.triggers import Timer

    stepped = _sim_ns(phase_ns)
    if stepped > 0.0:
        await Timer(stepped, unit="ns")


def _controller(dut):
    inner = getattr(dut, "dut", None)
    if inner is not None:
        controller = getattr(inner, "sys_controller", None)
        if controller is not None:
            return controller
    return getattr(dut, "sys_controller", None)


def _engine(dut):
    inner = getattr(dut, "dut", None)
    if inner is not None:
        engine = getattr(inner, "qspi_engine", None)
        if engine is not None:
            return engine
    return getattr(dut, "qspi_engine", None)


def _bus_gnt(dut) -> int:
    return 1 if (int(dut.uo_out.value) & BUS_GNT_MASK) else 0


def _release_host_uio(dut) -> None:
    """Drop MCU pass-through drive before BUS_REQ release (release-before-seize)."""
    if hasattr(dut, "host_uio_drive"):
        dut.host_uio_drive.value = 0
    if hasattr(dut, "host_uio_oe"):
        dut.host_uio_oe.value = 0


def _note_reset_agents(bringup) -> None:
    if bringup is None:
        return
    for agent in getattr(bringup, "agents", ()):
        note = getattr(agent, "note_reset", None)
        if note is not None:
            note()


def _sample_names(dut) -> tuple:
    controller = _controller(dut)
    engine = _engine(dut)
    state_name = None
    phase_name = None
    if controller is not None:
        try:
            state_name = CTRL_STATE_BY_CODE.get(int(controller.curr_state.value))
        except ValueError:
            state_name = None
    if engine is not None:
        try:
            phase_name = QPI_STATE_BY_CODE.get(int(engine.curr_state.value))
        except ValueError:
            phase_name = None
    return state_name, phase_name


def _optional_state_name(block, attr: str, table: dict):
    if block is None or not hasattr(block, attr):
        return None
    try:
        return table.get(int(getattr(block, attr).value))
    except (ValueError, TypeError, AttributeError):
        return None


def _ctrl_next_name(dut):
    return _optional_state_name(_controller(dut), "next_state", CTRL_STATE_BY_CODE)


def _eng_curr_name(dut):
    return _optional_state_name(_engine(dut), "curr_state", QPI_STATE_BY_CODE)


def _eng_next_name(dut):
    return _optional_state_name(_engine(dut), "next_state", QPI_STATE_BY_CODE)


def _synchronized_bus_req(dut) -> "int | None":
    """Return the DUT-visible synchronized ``bus_req``, or None if hidden."""
    inner = getattr(dut, "dut", None)
    for block in (inner, dut):
        if block is None:
            continue
        handle = getattr(block, "bus_req", None)
        if handle is None:
            continue
        try:
            return int(handle.value)
        except (ValueError, TypeError, AttributeError):
            return None
    return None


def _engine_in(dut, *names: str) -> bool:
    curr = _eng_curr_name(dut)
    nxt = _eng_next_name(dut)
    return curr in names or nxt in names


def _cycles_until_ctrl(dut, target_name: str) -> "int | None":
    """Best-effort cycles until *target_name* is ``curr_state``.

    0 = already current, 1 = ``next_state`` matches, 2/3 = predecessor plus
    engine wrap-up (CS_OFF / SCLK_OFF) so raw BUS_REQ can be asserted
    ``SYNC_LATENCY_CYCLES`` before a one-cycle landing.
    """
    curr, _ = _sample_names(dut)
    nxt = _ctrl_next_name(dut)
    if curr == target_name:
        return 0
    if nxt == target_name:
        return 1
    wrapping = _engine_in(dut, "CS_OFF", "SCLK_OFF")
    if target_name == "NEW_OP" and curr in ("FETCH", "READ") and wrapping:
        return 2 if _eng_curr_name(dut) == "CS_OFF" else 3
    if target_name == "UPDATE" and curr == "WRITE" and wrapping:
        return 2 if _eng_curr_name(dut) == "CS_OFF" else 3
    return None


def _cycles_until_qpi(dut, target_names) -> "int | None":
    """Best-effort cycles until an engine encoding in *target_names* is current."""
    names = set(target_names)
    curr = _eng_curr_name(dut)
    nxt = _eng_next_name(dut)
    if curr in names:
        return 0
    if nxt in names:
        return 1
    ctrl_curr, _ = _sample_names(dut)
    ctrl_nxt = _ctrl_next_name(dut)
    if "CS_ON" in names and curr == "QSPI_IDLE":
        if ctrl_nxt in ("NEW_FETCH", "NEW_OP"):
            return 2
        if ctrl_curr in ("NEW_FETCH", "NEW_OP") and ctrl_nxt in (
            "FETCH",
            "READ",
            "WRITE",
        ):
            return 1
    if "CS_OFF" in names and curr == "SCLK_OFF":
        return 1
    if "SCLK_OFF" in names and curr in ("READ_DATA", "WRITE_DATA") and nxt == "SCLK_OFF":
        return 1
    return None


def _cycles_until_targets(dut, record) -> "int | None":
    need_state = record.target_state is not None
    need_phase = record.target_phase is not None
    if not need_state and not need_phase:
        return 0
    state_until = None
    phase_until = None
    if need_state:
        state_until = _cycles_until_ctrl(dut, record.target_state)
    if need_phase:
        names = {QPI_STATE_BY_CODE[code] for code in resolve_qpi_phase(record.target_phase)}
        alias = _QPI_ALIASES.get(str(record.target_phase))
        if alias is not None:
            names.update(alias)
        phase_until = _cycles_until_qpi(dut, names)
    if need_state and need_phase:
        if state_until is None or phase_until is None:
            return None
        return max(state_until, phase_until)
    return state_until if need_state else phase_until


async def await_controller_state(
    dut, targets, *, timeout_cycles: int = STATE_TIMEOUT_CYCLES
) -> int:
    """Poll ``sys_controller.curr_state`` until it is in *targets*.

    Returns after the sampling edge so the caller may drive ``ui_in`` / ``rst_n``
    in the same cycle the target was observed (same pattern as
    ``tests.test_reset_and_bus``).
    """
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

    if isinstance(targets, (str, int)):
        codes = {resolve_ctrl_state(targets)}
    else:
        codes = {resolve_ctrl_state(item) for item in targets}
    controller = _controller(dut)
    if controller is None:
        raise InjectionError("sys_controller hierarchy is not visible on this DUT")
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        state = int(controller.curr_state.value)
        if state in codes:
            await NextTimeStep()
            return state
        await NextTimeStep()
    raise InjectionError(
        f"sys_controller.curr_state never reached {sorted(codes)} within "
        f"{timeout_cycles} cycles"
    )


async def await_engine_state(
    dut, targets, *, timeout_cycles: int = STATE_TIMEOUT_CYCLES
) -> int:
    """Poll ``qspi_engine.curr_state`` until it is in *targets*."""
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

    if isinstance(targets, (str, int)):
        codes = set(resolve_qpi_phase(targets))
    else:
        codes = set()
        for item in targets:
            codes.update(resolve_qpi_phase(item))
    engine = _engine(dut)
    if engine is None:
        raise InjectionError("qspi_engine hierarchy is not visible on this DUT")
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        state = int(engine.curr_state.value)
        if state in codes:
            await NextTimeStep()
            return state
        await NextTimeStep()
    raise InjectionError(
        f"qspi_engine.curr_state never reached {sorted(codes)} within "
        f"{timeout_cycles} cycles"
    )


async def _await_targets(dut, record, *, timeout_cycles: int) -> None:
    """Poll until the requested controller state and/or QPI phase are current."""
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

    need_state = record.target_state is not None
    need_phase = record.target_phase is not None
    if not need_state and not need_phase:
        return
    state_codes = {resolve_ctrl_state(record.target_state)} if need_state else None
    phase_codes = set(resolve_qpi_phase(record.target_phase)) if need_phase else None
    controller = _controller(dut) if need_state else None
    engine = _engine(dut) if need_phase else None
    if need_state and controller is None:
        raise InjectionError("sys_controller hierarchy is not visible on this DUT")
    if need_phase and engine is None:
        raise InjectionError("qspi_engine hierarchy is not visible on this DUT")
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        state_ok = True if state_codes is None else int(controller.curr_state.value) in state_codes
        phase_ok = True if phase_codes is None else int(engine.curr_state.value) in phase_codes
        if state_ok and phase_ok:
            await NextTimeStep()
            return
        await NextTimeStep()
    raise InjectionError(
        f"never reached target state={record.target_state!r} "
        f"phase={record.target_phase!r} within {timeout_cycles} cycles"
    )


async def _wait_raw_assert_slot(dut, record: BusReqRecord, *, timeout_cycles: int) -> None:
    """Wait until raw BUS_REQ must rise ``SYNC_LATENCY_CYCLES`` before the landing.

    Same scheduling idea as :func:`inject_bus_req_at_new_fetch` and
    ``tests.test_reset_and_bus._pulse_start_with_bus_req_at_new_fetch``: the
    raw edge is placed *before* the synchronized cycle, not after the target
    is already visible. Short bins (NEW_FETCH, NEW_OP, UPDATE, CS_ON) are
    otherwise missed.
    """
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

    extra = landing_offset_cycles(
        record.landing,
        one_cycle=_region_is_one_cycle(record.target_state, record.target_phase),
    )
    missed = 0
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        until = _cycles_until_targets(dut, record)
        if until is None:
            missed += 1
            await NextTimeStep()
            continue
        wait = until + extra - SYNC_LATENCY_CYCLES
        await NextTimeStep()
        if wait > 0:
            need_state = record.target_state is not None
            need_phase = record.target_phase is not None
            controller = _controller(dut) if need_state else None
            engine = _engine(dut) if need_phase else None
            state_codes = (
                {resolve_ctrl_state(record.target_state)} if need_state else None
            )
            phase_codes = (
                set(resolve_qpi_phase(record.target_phase)) if need_phase else None
            )
            drifted = False
            for _ in range(wait):
                await RisingEdge(dut.clk)
                await ReadOnly()
                if (
                    state_codes is not None
                    and int(controller.curr_state.value) not in state_codes
                    and _ctrl_next_name(dut) != record.target_state
                ):
                    drifted = True
                    await NextTimeStep()
                    break
                if (
                    phase_codes is not None
                    and int(engine.curr_state.value) not in phase_codes
                ):
                    drifted = True
                    await NextTimeStep()
                    break
                await NextTimeStep()
            if drifted:
                missed += 1
                continue
        return
    raise InjectionError(
        f"never reached raw-assert slot for state={record.target_state!r} "
        f"phase={record.target_phase!r} within {timeout_cycles} cycles "
        f"({missed} missed candidate slot(s))"
    )


async def _sample_after_sync_latency(dut, record: BusReqRecord) -> None:
    """Advance ``SYNC_LATENCY_CYCLES`` and sample the synchronized cycle."""
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

    for index in range(SYNC_LATENCY_CYCLES):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if index == SYNC_LATENCY_CYCLES - 1:
            observed_state, observed_phase = _sample_names(dut)
            record.observed_state = observed_state
            record.observed_phase = observed_phase
        await NextTimeStep()


async def _complete_bus_req_cycle(
    dut, record: BusReqRecord, *, wait_grant: bool, release: bool
) -> BusReqRecord:
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge
    from common.host import assert_bus_req

    if wait_grant:
        for _ in range(GRANT_TIMEOUT_CYCLES):
            await RisingEdge(dut.clk)
            await ReadOnly()
            if _bus_gnt(dut) == 1:
                record.grant_time_ns = _now_ns()
                await NextTimeStep()
                break
            await NextTimeStep()
        else:
            raise InjectionError("BUS_GNT never asserted after BUS_REQ")
        parked = True
        if hasattr(dut, "uio_oe"):
            parked = False
            known = False
            for _ in range(GRANT_TIMEOUT_CYCLES):
                await RisingEdge(dut.clk)
                await ReadOnly()
                try:
                    oe = int(dut.uio_oe.value)
                except (ValueError, TypeError, AttributeError):
                    oe = None
                if oe is None:
                    await NextTimeStep()
                    continue
                known = True
                if oe == 0:
                    parked = True
                    await NextTimeStep()
                    break
                await NextTimeStep()
            if known and not parked:
                raise InjectionError("ASIC uio_oe did not park after BUS_GNT")
        for _ in range(record.host_hold_cycles):
            await RisingEdge(dut.clk)
    if release:
        _release_host_uio(dut)
        await assert_bus_req(dut, hold=False)
        record.release_time_ns = _now_ns()
    return record


async def jitter_start(
    dut,
    plan: "StartPulseRecord | None" = None,
    *,
    planner: "InjectionPlanner | None" = None,
    capture: str = CAPTURE_REQUIRED,
    phase: "str | None" = None,
    clk_period_ns: "float | None" = None,
) -> StartPulseRecord:
    """Drive one phase-jittered raw START pulse.

    Capture-required pulses stay high for at least three complete ``clk``
    periods after the first possible sampling edge and must produce exactly
    one synchronized START. Capture-uncertain pulses are shorter or
    boundary-placed: zero or one synchronized edge is legal, two are not.
    Deassert-phase jitter is applied only for capture-required; adding it to
    a short hold can create two sync edges.
    """
    import cocotb
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge, Timer
    from common.host import START_BIT

    period = resolve_clk_period_ns(
        plan.clk_period_ns if plan is not None else clk_period_ns
    )
    record = _resolve_start_plan(plan, planner, capture, phase, period)
    width_ns = start_pulse_width_ns(record)

    inner = getattr(dut, "dut", None)
    start_handle = getattr(inner, "start", None) if inner is not None else None
    if start_handle is None:
        start_handle = getattr(dut, "start", None)
    edges = {"n": 0, "run": True}

    async def _count_sync_starts() -> None:
        prev = 0
        while edges["run"]:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if start_handle is not None:
                try:
                    level = int(start_handle.value)
                except (ValueError, TypeError, AttributeError):
                    level = 0
                if level and not prev:
                    edges["n"] += 1
                prev = level
            await NextTimeStep()

    counter = cocotb.start_soon(_count_sync_starts())
    await RisingEdge(dut.clk)
    await _await_phase_ns(record.assert_phase_ns)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << START_BIT)
    record.assert_time_ns = _now_ns()
    await Timer(max(_sim_ns(width_ns), 0.001), unit="ns")
    current = int(dut.ui_in.value)
    dut.ui_in.value = current & ~(1 << START_BIT) & 0xFF
    record.deassert_time_ns = _now_ns()
    for _ in range(SYNC_LATENCY_CYCLES + 1):
        await RisingEdge(dut.clk)
    edges["run"] = False
    await RisingEdge(dut.clk)
    await counter
    record.sync_edges = edges["n"]
    record.idle_uncaptured = (
        record.capture == CAPTURE_UNCERTAIN and record.sync_edges == 0
    )
    classify_start_capture(record)
    return record


async def inject_bus_req(
    dut,
    plan: "BusReqRecord | None" = None,
    *,
    planner: "InjectionPlanner | None" = None,
    target_state: "str | int | None" = None,
    target_phase: "str | int | None" = None,
    landing: "str | None" = None,
    clk_period_ns: "float | None" = None,
    wait_grant: bool = True,
    release: bool = True,
    timeout_cycles: int = STATE_TIMEOUT_CYCLES,
) -> BusReqRecord:
    """Assert raw BUS_REQ targeted at a controller state and/or QPI phase.

    The two-flop synchronizer means the raw edge is scheduled
    ``SYNC_LATENCY_CYCLES`` (and the landing offset) *before* the cycle the
    controller sees ``bus_req``, matching :func:`inject_bus_req_at_new_fetch`.
    *landing* places that synchronized assertion at the start, middle, or
    final cycle of the region where the region's duration permits. After
    ``BUS_GNT``, the request is held for a host interval (biased short) and
    released with the host release-before-seize model
    (:func:`common.host.assert_bus_req`).

    ``observed_state`` / ``observed_phase`` are sampled on the synchronized
    cycle, not at raw assertion. Callers must feed those names into
    :class:`common.coverage_l1.L1CoverageAdapter` (``COV-BUS-STATE`` /
    ``COV-BUS-PHASE``); this module does not record coverage.

    ``Q-LAUNCH`` is not judged here; grant-park OE clear is not a launch
    window because the ASIC is not driving SCK.
    """
    from common.host import BUS_REQ_BIT

    period = resolve_clk_period_ns(
        plan.clk_period_ns if plan is not None else clk_period_ns
    )
    record = _resolve_bus_plan(
        plan, planner, target_state, target_phase, landing, period
    )
    await _wait_raw_assert_slot(dut, record, timeout_cycles=timeout_cycles)
    await _await_phase_ns(record.assert_phase_ns)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << BUS_REQ_BIT)
    record.assert_time_ns = _now_ns()
    await _sample_after_sync_latency(dut, record)
    return await _complete_bus_req_cycle(
        dut, record, wait_grant=wait_grant, release=release
    )


async def inject_bus_req_at_new_fetch(
    dut,
    *,
    planner: "InjectionPlanner | None" = None,
    wait_grant: bool = True,
    release: bool = True,
    clk_period_ns: "float | None" = None,
) -> BusReqRecord:
    """Accepted START with BUS_REQ timed so sync lands on ``NEW_FETCH``.

    ``bus_req`` and ``start`` share IDLE's priority check (BUS_REQ wins), so
    the raw BUS_REQ edge is one cycle after START. Same pattern as
    ``tests.test_reset_and_bus``. Raw assertion is
    ``SYNC_LATENCY_CYCLES`` before the synchronized NEW_FETCH cycle.
    """
    from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge
    from common.host import BUS_REQ_BIT, START_BIT

    period = resolve_clk_period_ns(clk_period_ns)
    record = _resolve_bus_plan(
        None, planner, "NEW_FETCH", None, LANDING_START, period
    )
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << START_BIT)
    await RisingEdge(dut.clk)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << BUS_REQ_BIT)
    record.assert_time_ns = _now_ns()
    await RisingEdge(dut.clk)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current & ~(1 << START_BIT) & 0xFF
    await RisingEdge(dut.clk)
    await ReadOnly()
    observed_state, observed_phase = _sample_names(dut)
    await NextTimeStep()
    if observed_state != "NEW_FETCH":
        raise InjectionError(
            f"NEW_FETCH helper synchronized on {observed_state!r}, not NEW_FETCH"
        )
    record.observed_state = observed_state
    record.observed_phase = observed_phase
    return await _complete_bus_req_cycle(
        dut, record, wait_grant=wait_grant, release=release
    )


async def pulse_reset(
    dut,
    plan: "ResetPulseRecord | None" = None,
    *,
    planner: "InjectionPlanner | None" = None,
    bringup=None,
    reset_truncated: str = REVIEW,
    target_state: "str | int | None" = None,
    target_phase: "str | int | None" = None,
    hold_cycles: "int | None" = None,
    release: bool = False,
    timeout_cycles: int = STATE_TIMEOUT_CYCLES,
) -> ResetPulseRecord:
    """Force ``rst_n=0`` with a documented ``reset_truncated`` policy.

    Default *release* is ``False`` so the caller can ``dispose_run`` while
    reset is still asserted (M3: live CE monitor plus forced ``rst_n=0``
    must use ``REVIEW`` or ``REQUIRE``). Call :func:`release_reset` after
    dispose. ``RESET-TRUNCATED`` findings are timing observations explained
    by reset OE/state convergence, not ordinary ``Q-*`` fails. ``Q-LAUNCH``
    still applies only while the ASIC drives SCK.

    The returned record's ``reset_truncated`` field is the policy to pass
    to :func:`common.dispose.dispose_run`.
    """
    from cocotb.triggers import RisingEdge

    live_ce = has_live_ce_monitor(bringup)
    record = _resolve_reset_plan(
        plan,
        planner,
        target_state,
        target_phase,
        reset_truncated,
        hold_cycles,
        release,
        live_ce,
    )
    await _await_targets(dut, record, timeout_cycles=timeout_cycles)
    observed_state, observed_phase = _sample_names(dut)
    record.observed_state = observed_state
    record.observed_phase = observed_phase
    _note_reset_agents(bringup)
    dut.rst_n.value = 0
    record.assert_time_ns = _now_ns()
    for _ in range(record.hold_cycles):
        await RisingEdge(dut.clk)
    if record.release:
        await release_reset(dut)
        record.release_time_ns = _now_ns()
    return record


async def release_reset(dut) -> None:
    """Release ``rst_n`` and park host inputs (same as the directed reset tests)."""
    from cocotb.triggers import RisingEdge

    dut.rst_n.value = 1
    dut.ui_in.value = 0
    if hasattr(dut, "host_uio_drive"):
        dut.host_uio_drive.value = 0
    if hasattr(dut, "host_uio_oe"):
        dut.host_uio_oe.value = 0
    await RisingEdge(dut.clk)


__all__ = [
    "BUS_LANDINGS",
    "BUS_REQ_EXCLUDED_STATES",
    "CAPTURE_CLASSES",
    "CAPTURE_REQUIRED",
    "CAPTURE_UNCERTAIN",
    "CTRL_STATE_BY_CODE",
    "DEFAULT_CLK_PERIOD_NS",
    "FOCUSED_PHASE_BINS",
    "FORBID",
    "INJECTION_STREAMS",
    "LANDING_FINAL",
    "LANDING_MIDDLE",
    "LANDING_START",
    "PHASE_EARLY",
    "PHASE_LATE",
    "PHASE_NEAR_EDGE_AFTER",
    "PHASE_NEAR_EDGE_BEFORE",
    "PHASE_ON_EDGE",
    "QPI_STATE_BY_CODE",
    "REQUIRE",
    "RESET_TRUNCATED_POLICIES",
    "REVIEW",
    "START_PHASE_BINS",
    "STREAM_BUS_REQ",
    "STREAM_RESET",
    "STREAM_START",
    "SYNC_LATENCY_CYCLES",
    "UNCERTAIN_WIDTH_RATIO",
    "BusReqRecord",
    "InjectionError",
    "InjectionPlanner",
    "ResetPulseRecord",
    "StartPulseRecord",
    "await_controller_state",
    "await_engine_state",
    "capture_required_hold_ns",
    "capture_uncertain_hold_ns",
    "classify_start_capture",
    "classify_start_phase",
    "has_live_ce_monitor",
    "inject_bus_req",
    "inject_bus_req_at_new_fetch",
    "jitter_start",
    "landing_offset_cycles",
    "offset_for_phase_bin",
    "pulse_reset",
    "release_reset",
    "require_reset_truncated_policy",
    "resolve_clk_period_ns",
    "resolve_ctrl_state",
    "resolve_qpi_phase",
    "start_pulse_width_ns",
]
