"""Thin L1 adapter: hierarchy / host observations into :class:`CoverageSampler`.

Imports ``SYS_CONTROL_STATES`` and ``QSPI_ENGINE_STATES`` from
:mod:`common.constants`. This file is for L1 simulation only; unit
tests of the sampler must not import it.

Points recorded here:

* ``COV-CTRL-STATE`` - controller state reached (all eight ``sys_control_state_t`` encodings)
* ``COV-QPI-PHASE`` - QPI phase reached (all ten ``qspi_state_t`` encodings; READ_DATA and WRITE_DATA separate)
* ``COV-BUS-STATE`` - BUS_REQ synchronized assertion x controller state (STALL excluded)
* ``COV-BUS-PHASE`` - BUS_REQ synchronized assertion x active QPI phase
* ``COV-BUS-RESUME`` - stall origin x resumed action
* ``COV-RESET-STATE`` - reset assertion x controller state
* ``COV-RESET-PHASE`` - reset assertion x external QPI phase
* ``COV-START-PHASE`` - raw START assertion phase
* ``COV-START-RESULT`` - START context x capture result

Hits still wait for :meth:`reference.coverage.CoverageSampler.commit_window`.
"""

from common.constants import QSPI_ENGINE_STATES, SYS_CONTROL_STATES
from reference.coverage import (
    BUS_RESUME_BINS,
    CTRL_STATE_BINS,
    COV_BUS_PHASE,
    COV_BUS_RESUME,
    COV_BUS_STATE,
    COV_CTRL_STATE,
    COV_QPI_PHASE,
    COV_RESET_PHASE,
    COV_RESET_STATE,
    COV_START_PHASE,
    COV_START_RESULT,
    QPI_PHASE_BINS,
    CoverageError,
    CoverageSampler,
)

# Active QPI phase -> COV-BUS-PHASE bin. QSPI_IDLE is not an active phase.
_BUS_PHASE_FROM_QPI = {
    "CS_ON": "CS_ON",
    "SEND_CMD_1": "command",
    "SEND_CMD_2": "command",
    "SEND_ADDR": "address",
    "WAIT": "wait",
    "READ_DATA": "read_data",
    "WRITE_DATA": "write_data",
    "SCLK_OFF": "SCLK_OFF",
    "CS_OFF": "CS_OFF",
}

# External QPI phase for COV-RESET-PHASE (idle/pad, command, address, wait,
# read data, write data, termination).
_RESET_PHASE_FROM_QPI = {
    "QSPI_IDLE": "idle_pad",
    "CS_ON": "idle_pad",
    "SEND_CMD_1": "command",
    "SEND_CMD_2": "command",
    "SEND_ADDR": "address",
    "WAIT": "wait",
    "READ_DATA": "read_data",
    "WRITE_DATA": "write_data",
    "SCLK_OFF": "termination",
    "CS_OFF": "termination",
}

_CTRL_TO_BUS = {
    "SYS_CTRL_IDLE": "IDLE",
    "NEW_FETCH": "NEW_FETCH",
    "FETCH": "FETCH",
    "NEW_OP": "NEW_OP",
    "READ": "READ",
    "WRITE": "WRITE",
    "UPDATE": "UPDATE",
    "STALL": "STALL",
}


def _check_handshake_tables() -> None:
    """Fail loudly if handshake name tables drift from the sampler's catalog."""
    ctrl_names = set(SYS_CONTROL_STATES.values())
    qpi_names = set(QSPI_ENGINE_STATES.values())
    if ctrl_names != set(CTRL_STATE_BINS):
        raise CoverageError(
            "SYS_CONTROL_STATES values drifted from CTRL_STATE_BINS: "
            f"handshake={sorted(ctrl_names)} catalog={list(CTRL_STATE_BINS)}"
        )
    if qpi_names != set(QPI_PHASE_BINS):
        raise CoverageError(
            "QSPI_ENGINE_STATES values drifted from QPI_PHASE_BINS: "
            f"handshake={sorted(qpi_names)} catalog={list(QPI_PHASE_BINS)}"
        )


_check_handshake_tables()


def resolve_ctrl_state(state) -> str:
    """Return the handshake name for a controller state encoding or name."""
    if isinstance(state, str):
        if state in SYS_CONTROL_STATES.values():
            return state
        raise CoverageError(f"unknown controller state {state!r}")
    try:
        return SYS_CONTROL_STATES[int(state)]
    except (KeyError, TypeError, ValueError) as error:
        raise CoverageError(f"unknown controller state encoding {state!r}") from error


def resolve_qpi_phase(phase) -> str:
    """Return the handshake name for a QPI phase encoding or name."""
    if isinstance(phase, str):
        if phase in QSPI_ENGINE_STATES.values():
            return phase
        raise CoverageError(f"unknown QPI phase {phase!r}")
    try:
        return QSPI_ENGINE_STATES[int(phase)]
    except (KeyError, TypeError, ValueError) as error:
        raise CoverageError(f"unknown QPI phase encoding {phase!r}") from error


class L1CoverageAdapter:
    """Translate DUT / host observations into sampler bins.

    Construct with the run's :class:`CoverageSampler`. Call the ``record_*``
    methods during the window; Wave 2 still calls
    :meth:`CoverageSampler.commit_window` after checkers and the scoreboard.
    """

    def __init__(self, sampler: CoverageSampler) -> None:
        self.sampler = sampler

    def record_ctrl_state(self, state) -> None:
        """Record ``COV-CTRL-STATE`` (controller state reached)."""
        self.sampler.record_observation(COV_CTRL_STATE, resolve_ctrl_state(state))

    def record_qpi_phase(self, phase) -> None:
        """Record ``COV-QPI-PHASE`` (QPI phase reached)."""
        self.sampler.record_observation(COV_QPI_PHASE, resolve_qpi_phase(phase))

    def record_bus_assertion(self, ctrl_state, qpi_phase=None) -> None:
        """Record ``COV-BUS-STATE`` and, when active, ``COV-BUS-PHASE``.

        ``STALL`` is a recorded exclusion, not a hit: reaching STALL already
        requires the synchronized request.
        """
        ctrl_name = resolve_ctrl_state(ctrl_state)
        bus_name = _CTRL_TO_BUS[ctrl_name]
        if bus_name != "STALL":
            self.sampler.record_observation(COV_BUS_STATE, bus_name)
        if qpi_phase is None:
            return
        qpi_name = resolve_qpi_phase(qpi_phase)
        bus_phase = _BUS_PHASE_FROM_QPI.get(qpi_name)
        if bus_phase is not None:
            self.sampler.record_observation(COV_BUS_PHASE, bus_phase)

    def record_bus_resume(self, origin) -> None:
        """Record ``COV-BUS-RESUME`` (stall origin: IDLE, NEW_FETCH, NEW_OP, UPDATE)."""
        name = resolve_ctrl_state(origin)
        resume = _CTRL_TO_BUS[name]
        if resume not in BUS_RESUME_BINS:
            raise CoverageError(
                f"COV-BUS-RESUME origin {name!r} is not one of {BUS_RESUME_BINS}"
            )
        self.sampler.record_observation(COV_BUS_RESUME, resume)

    def record_reset(self, ctrl_state, qpi_phase=None) -> None:
        """Record ``COV-RESET-STATE`` and ``COV-RESET-PHASE`` at the reset edge."""
        self.sampler.record_observation(COV_RESET_STATE, resolve_ctrl_state(ctrl_state))
        if qpi_phase is None:
            return
        qpi_name = resolve_qpi_phase(qpi_phase)
        self.sampler.record_observation(COV_RESET_PHASE, _RESET_PHASE_FROM_QPI[qpi_name])

    def record_start_phase(self, phase) -> None:
        """Record ``COV-START-PHASE`` (early / near-edge / on-edge / late)."""
        self.sampler.record_observation(COV_START_PHASE, phase)

    def record_start_result(self, result) -> None:
        """Record ``COV-START-RESULT`` (idle accepted, ignored, held-high, ...)."""
        self.sampler.record_observation(COV_START_RESULT, result)


__all__ = [
    "L1CoverageAdapter",
    "resolve_ctrl_state",
    "resolve_qpi_phase",
]
