"""L1 START, BUS_REQ, and reset directed tests.

Test-case IDs:
    TC-START-ACTIVE
    TC-START-HELD
    TC-START-PHASE
    TC-BUS-IDLE
    TC-BUS-BOUNDARY
    TC-BUS-ACTIVE
    TC-BUS-PHASE
    TC-BUS-REPEAT
    TC-RESET-IDLE
    TC-RESET-ACTIVE
    TC-RESET-REPEAT

Coverage IDs (where applicable):
    COV-START-PHASE, COV-START-RESULT,
    COV-BUS-STATE, COV-BUS-PHASE, COV-BUS-RESUME,
    COV-RESET-STATE, COV-RESET-PHASE
"""

import cocotb


@cocotb.test()
async def start_while_active(dut):
    """TC-START-ACTIVE: START edges during fetch, read, write, update, stall."""
    raise NotImplementedError("M2+ implements TC-START-ACTIVE")


@cocotb.test()
async def start_held_high(dut):
    """TC-START-HELD: hold raw START through acceptance and completion."""
    raise NotImplementedError("M2+ implements TC-START-HELD")


@cocotb.test()
async def start_phase_sweep(dut):
    """TC-START-PHASE: sweep START assertion phase around clk edges."""
    raise NotImplementedError("M2+ implements TC-START-PHASE")


@cocotb.test()
async def bus_req_from_idle(dut):
    """TC-BUS-IDLE: BUS_REQ assert/release in IDLE."""
    raise NotImplementedError("M2+ implements TC-BUS-IDLE")


@cocotb.test()
async def bus_req_at_boundaries(dut):
    """TC-BUS-BOUNDARY: BUS_REQ in NEW_FETCH, NEW_OP, and UPDATE."""
    raise NotImplementedError("M2+ implements TC-BUS-BOUNDARY")


@cocotb.test()
async def bus_req_during_transaction(dut):
    """TC-BUS-ACTIVE: BUS_REQ during fetch, payload read, and payload write."""
    raise NotImplementedError("M2+ implements TC-BUS-ACTIVE")


@cocotb.test()
async def bus_req_during_qpi_phase(dut):
    """TC-BUS-PHASE: BUS_REQ during each externally visible QPI phase."""
    raise NotImplementedError("M2+ implements TC-BUS-PHASE")


@cocotb.test()
async def bus_req_repeat_cycles(dut):
    """TC-BUS-REPEAT: multiple request/grant/release cycles in one chain."""
    raise NotImplementedError("M2+ implements TC-BUS-REPEAT")


@cocotb.test()
async def reset_from_idle(dut):
    """TC-RESET-IDLE: reset from IDLE and while BUS_GNT is active."""
    raise NotImplementedError("M2+ implements TC-RESET-IDLE")


@cocotb.test()
async def reset_during_activity(dut):
    """TC-RESET-ACTIVE: reset during every controller state and QPI phase."""
    raise NotImplementedError("M2+ implements TC-RESET-ACTIVE")


@cocotb.test()
async def reset_then_identical_rerun(dut):
    """TC-RESET-REPEAT: identical chain twice across a reset boundary."""
    raise NotImplementedError("M2+ implements TC-RESET-REPEAT")
