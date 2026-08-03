"""Host-side START, BUS_REQ, and pass-through drive actions.

Covers stimulus for ``TC-START-*``, ``TC-BUS-*``, and top-level host protocol.
Milestone M0+ fills cocotb drivers; only the plain accepted-pulse case is
wired for M0's smoke test. Phase jitter and held/short-pulse cases are M2+.
"""

from cocotb.triggers import RisingEdge

START_BIT = 0
BUS_REQ_BIT = 2


async def pulse_start(dut, hold_cycles: int = 2) -> None:
    """Issue one accepted START rising-edge pulse after ``BUS_GNT`` is low."""
    current = int(dut.ui_in.value)
    dut.ui_in.value = current | (1 << START_BIT)
    for _ in range(hold_cycles):
        await RisingEdge(dut.clk)
    current = int(dut.ui_in.value)
    dut.ui_in.value = current & ~(1 << START_BIT) & 0xFF
    await RisingEdge(dut.clk)


async def assert_bus_req(dut, hold: bool = True) -> None:
    """Assert or release raw ``BUS_REQ`` with host release-before-seize model."""
    current = int(dut.ui_in.value)
    if hold:
        dut.ui_in.value = current | (1 << BUS_REQ_BIT)
    else:
        dut.ui_in.value = current & ~(1 << BUS_REQ_BIT) & 0xFF
    await RisingEdge(dut.clk)
