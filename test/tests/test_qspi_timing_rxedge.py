"""L1 directed ``Q-RXEDGE`` capture without a ``rdata_valid`` alias.

``Q-RXEDGE`` (each launched read nibble captured on the following rising SCK)
has no L1 ``rdata_valid`` port (do not add that alias to ``tb_top`` /
``tb_uio_bus.svh``). The timing monitor treats the armed external rising SCK
as the D16 capture and still applies ``tACLK`` / ``PSRAM_TACLK_NS`` (read data
valid after falling SCK). ``TIMING_PROFILE=ideal`` zeros only TB placeholders;
datasheet AC stays live.

Test-case IDs:
    TC-RXEDGE-L1-READ-PASS
"""

import cocotb
from cocotb.triggers import RisingEdge, with_timeout
from cocotb.triggers import SimTimeoutError

from common.bringup import bring_up_top
from common.runlog import begin_run
from common.constants import (
    DONE_MASK,
    DONE_TIMEOUT_NS,
    DST_ADDR,
    DST_SENTINEL,
    FILL,
    NEXT_TCD_ADDR,
    RESULT_PASS,
    SRC_ADDR,
    SRC_BYTE,
    TCD_HEAD_ADDR,
)
from common.dispose import dispose_run
from common.host import pulse_start
from monitors.timing import Q_RXEDGE
from reference.tcd import Tcd, encode_tcd

async def _wait_for_done_pulse(dut) -> None:
    while int(dut.uo_out.value) & DONE_MASK:
        await RisingEdge(dut.clk)
    while not (int(dut.uo_out.value) & DONE_MASK):
        await RisingEdge(dut.clk)

@cocotb.test()
async def qspi_rxedge_l1_read_pass(dut):
    """TC-RXEDGE-L1-READ-PASS: timed L1 DMA read disposes Q-RXEDGE=pass."""
    config, repro = begin_run(dut, "qspi_rxedge_l1_read_pass")

    bringup = await bring_up_top(dut, fill=FILL)
    assert bringup.ce is not None, (
        "TC-RXEDGE-L1-READ-PASS requires the CE timing monitor. " + repro
    )
    assert bringup.ce._rdata_valid is None, (
        "TC-RXEDGE-L1-READ-PASS: L1 must not grow a rdata_valid alias; "
        "capture is the armed rising SCK. " + repro
    )
    psram0 = bringup.psram0
    psram0.write(
        TCD_HEAD_ADDR,
        encode_tcd(
            Tcd(
                src_ptr=SRC_ADDR,
                dest_ptr=DST_ADDR,
                transfer_len=1,
                next_tcd=NEXT_TCD_ADDR,
            )
        ),
    )
    psram0.write(NEXT_TCD_ADDR, encode_tcd(Tcd(quit=True)))
    psram0.write(SRC_ADDR, bytes([SRC_BYTE]))
    psram0.write(DST_ADDR, bytes([DST_SENTINEL]))
    bringup.clear()

    await pulse_start(dut)
    try:
        await with_timeout(_wait_for_done_pulse(dut), DONE_TIMEOUT_NS, "ns")
    except SimTimeoutError as exc:
        raise AssertionError(
            f"TC-RXEDGE-L1-READ-PASS: DONE did not return. {repro}"
        ) from exc

    observed = psram0.read(DST_ADDR, 1)[0]
    assert observed == SRC_BYTE, (
        f"TC-RXEDGE-L1-READ-PASS: dest=0x{observed:02X}, expected "
        f"0x{SRC_BYTE:02X}. {repro}"
    )
    dispose_run(
        bringup,
        test="TC-RXEDGE-L1-READ-PASS",
        log=dut._log,
        repro=repro,
    )
    results = bringup.ce.results()
    assert results[Q_RXEDGE] == RESULT_PASS, (
        "TC-RXEDGE-L1-READ-PASS: Q-RXEDGE="
        f"{results[Q_RXEDGE]!r} captures={bringup.ce._rx_captures} "
        f"launches={bringup.ce._rx_launches}. {repro}"
    )
    assert bringup.ce._rx_captures > 0, (
        "TC-RXEDGE-L1-READ-PASS: L1 rising-SCK capture path recorded no "
        f"captures (launches={bringup.ce._rx_launches}). {repro}"
    )
