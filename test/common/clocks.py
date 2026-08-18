"""Clock and synchronous reset helpers for cocotb 2.x (``unit="ns"``).

Milestone M0+ implements ``clk`` generation and ``rst_n`` release sequencing.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge, Timer


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


async def apply_gl_reset(dut, cycles: int = 5, period_ns: int = 10) -> None:
    """L2 reset: hold ``rst_n`` across at least three rising ``clk`` edges.

    Per ``09-gate-level-and-x.md``: host unused pins stay 0 (D34), shared
    ``uio_oe`` is already clear during reset (``CHK-RST-OE``: combinational
    pad OE gating while raw ``rst_n`` is low), release is away from a clock
    edge, then two more clocks before host inputs are interpreted.
    """
    if cycles < 3:
        cycles = 3
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.host_uio_drive.value = 0
    dut.host_uio_oe.value = 0
    # Let combinational ``uio_oe &= rst_n`` settle before the first sample.
    await Timer(1, unit="ns")
    for index in range(cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        try:
            oe = int(dut.uio_oe.value)
        except ValueError as exc:
            raise AssertionError(
                f"apply_gl_reset: uio_oe is {dut.uio_oe.value} after "
                f"rising edge {index + 1} with rst_n=0 (CHK-RST-OE)"
            ) from exc
        if oe != 0:
            raise AssertionError(
                f"apply_gl_reset: uio_oe=0x{oe:02X} after rising edge "
                f"{index + 1} with rst_n=0 (CHK-RST-OE)"
            )
        if index >= 1:
            try:
                status = int(dut.uo_out.value)
            except ValueError as exc:
                raise AssertionError(
                    f"apply_gl_reset: uo_out is {dut.uo_out.value} while rst_n=0 "
                    "(CHK-RST-STATUS: DONE high and BUS_GNT low after sampled reset)"
                ) from exc
            if (status & 0x1) != 1:
                raise AssertionError(
                    "apply_gl_reset: DONE not 1 while rst_n=0 (CHK-RST-STATUS)"
                )
            if (status & 0x2) != 0:
                raise AssertionError(
                    "apply_gl_reset: BUS_GNT not 0 while rst_n=0 (CHK-RST-STATUS)"
                )
        await NextTimeStep()
    await Timer(period_ns / 2.0, unit="ns")
    dut.rst_n.value = 1
    for _ in range(2):
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
