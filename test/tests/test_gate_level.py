"""L2 gate-level sign-off subset (``LEVEL=gl``, ``GATES=yes``).

Reuses high-value directed cases from ``09-gate-level-and-x.md`` when the
netlist exists. Top-observable ``CHK-*`` and pin-oracle scoreboard only.
"""

import cocotb


@cocotb.test()
async def gate_level_smoke(dut):
    """L2 smoke placeholder; assigned suite defined in 09-gate-level-and-x.md."""
    raise NotImplementedError("L2 gate netlist required for gate-level tests")
