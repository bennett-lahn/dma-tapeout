"""L1 constrained-random legal-chain regression.

Functional coverage IDs (``COV-*``) sampled from passing runs:
    COV-LEN, COV-CHUNK, COV-DEVICE, COV-NEXTDEV, COV-CHAINLEN, COV-END,
    COV-ADDR, COV-DATA, COV-CTRL-STATE, COV-QPI-PHASE,
    COV-DEPTH, COV-DEPTH-LEN, COV-DEPTH-DEVICE (blocked on harness wiring)
"""

import cocotb


@cocotb.test()
async def random_legal_chain(dut):
    """Constrained-random firmware-legal descriptor chains (M5 high-volume suite)."""
    raise NotImplementedError("M5+ implements constrained-random DMA regression")
