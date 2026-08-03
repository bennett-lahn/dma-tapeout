"""L1 directed DMA and descriptor semantic tests.

Test-case IDs:
    TC-TCD-BE
    TC-SAME-0
    TC-SAME-1
    TC-CROSS-01
    TC-CROSS-10
    TC-CHAIN
    TC-NEXT-DEVICE
    TC-LEN-CORNERS
    TC-QUIT
    TC-EMPTY
    TC-RESTART
    TC-ADDR-WIDE
    TC-OVERLAP
    TC-DEPTH (blocked until harness wires DMA_BUF_DEPTH sweep)
"""

import cocotb


@cocotb.test()
async def tcd_big_endian_flags(dut):
    """TC-TCD-BE: known 11-byte descriptor encoding and flag decode."""
    raise NotImplementedError("M2+ implements TC-TCD-BE")


@cocotb.test()
async def same_device_psram0(dut):
    """TC-SAME-0: PSRAM0 to PSRAM0 copy."""
    raise NotImplementedError("M2+ implements TC-SAME-0")


@cocotb.test()
async def same_device_psram1(dut):
    """TC-SAME-1: PSRAM1 to PSRAM1 copy after head fetch on PSRAM0."""
    raise NotImplementedError("M2+ implements TC-SAME-1")


@cocotb.test()
async def cross_device_0_to_1(dut):
    """TC-CROSS-01: PSRAM0 source to PSRAM1 destination."""
    raise NotImplementedError("M2+ implements TC-CROSS-01")


@cocotb.test()
async def cross_device_1_to_0(dut):
    """TC-CROSS-10: PSRAM1 source to PSRAM0 destination."""
    raise NotImplementedError("M2+ implements TC-CROSS-10")


@cocotb.test()
async def multi_tcd_chain(dut):
    """TC-CHAIN: at least three executable TCDs followed by quit."""
    raise NotImplementedError("M2+ implements TC-CHAIN")


@cocotb.test()
async def next_device_alternate(dut):
    """TC-NEXT-DEVICE: chain with alternating NEXT_DEVICE selection."""
    raise NotImplementedError("M2+ implements TC-NEXT-DEVICE")


@cocotb.test()
async def transfer_length_corners(dut):
    """TC-LEN-CORNERS: lengths 0, 1, N-1, N, N+1, and 255."""
    raise NotImplementedError("M2+ implements TC-LEN-CORNERS")


@cocotb.test()
async def quit_descriptor_priority(dut):
    """TC-QUIT: quit TCD with nonzero pointer and length fields."""
    raise NotImplementedError("M2+ implements TC-QUIT")


@cocotb.test()
async def empty_chain_at_head(dut):
    """TC-EMPTY: quit TCD at fixed head 0x000000 on PSRAM0."""
    raise NotImplementedError("M2+ implements TC-EMPTY")


@cocotb.test()
async def restart_after_completion(dut):
    """TC-RESTART: complete chain then issue fresh START."""
    raise NotImplementedError("M2+ implements TC-RESTART")


@cocotb.test()
async def wide_address_space(dut):
    """TC-ADDR-WIDE: valid addresses below, at, and above 0x010000."""
    raise NotImplementedError("M2+ implements TC-ADDR-WIDE")


@cocotb.test()
async def overlapping_same_device_ranges(dut):
    """TC-OVERLAP: same-device overlapping source and destination."""
    raise NotImplementedError("M2+ implements TC-OVERLAP")


@cocotb.test()
async def dma_buf_depth_sweep(dut):
    """TC-DEPTH: directed suite at DMA_BUF_DEPTH 1, 2, 4, 8 (blocked on harness)."""
    raise NotImplementedError("M5+ implements TC-DEPTH when harness supports depth sweep")
