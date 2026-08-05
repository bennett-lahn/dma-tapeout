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

    For ``tb_engine`` (L0), use :func:`apply_engine_reset` instead.
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


async def apply_engine_reset(dut, cycles: int = 5) -> None:
    """Park L0 ``qspi_engine`` stimulus, assert sync ``rst_n``, then release.

    Clears ``txn_valid`` and the held request fields, and parks the L0 fault
    injector inert, so both CE# idle high and SCK low after release. PSRAM
    model agents own their own SIO handles.
    """
    dut.rst_n.value = 0
    dut.txn_valid.value = 0
    dut.cmd.value = 0
    dut.addr.value = 0
    dut.device_sel.value = 0
    dut.byte_len.value = 0
    dut.wdata.value = 0
    dut.fault_sio_drive.value = 0
    dut.fault_sio_oe.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
