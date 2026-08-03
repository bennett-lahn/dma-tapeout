"""L0 QPI protocol directed tests (``LEVEL=engine``).

Test-case IDs:
    TC-QPI-READ
    TC-QPI-WRITE
"""

import cocotb


@cocotb.test()
async def qpi_read_variants(dut):
    """TC-QPI-READ: ``0xEB`` reads on each device, lengths 1 and 11."""
    raise NotImplementedError("M1+ implements TC-QPI-READ")


@cocotb.test()
async def qpi_write_variants(dut):
    """TC-QPI-WRITE: ``0x02`` writes on each device with representative lengths."""
    raise NotImplementedError("M1+ implements TC-QPI-WRITE")
