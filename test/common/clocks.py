"""Clock and synchronous reset helpers for cocotb 2.x (``unit="ns"``).

Milestone M0+ implements ``clk`` generation and ``rst_n`` release sequencing.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


async def start_clock(dut, period_ns: int = 10) -> None:
    """Start a free-running background ``clk`` on *dut*."""
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())


async def apply_reset(dut, cycles: int = 5) -> None:
    """Drive known-safe input defaults, assert sync active-low ``rst_n``, release.

    ``ena``, ``ui_in``, and the host uio pass-through drive are parked at
    known values before ``rst_n`` is released so no DUT input is left at
    X across the reset boundary. PSRAM model agents own their own signals.
    """
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
