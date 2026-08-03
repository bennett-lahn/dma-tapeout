"""Bus grant / ``uio_oe`` arbitration monitors (``CHK-ARB-*``, ``CHK-RST-OE``).

Relevant IDs: ``CHK-ARB-GNT-OE``, ``CHK-ARB-GNT-QUIET``, ``CHK-ARB-PARK``,
``CHK-ARB-GNT-NOT-BUSY``, ``CHK-RST-OE``, ``CHK-RST-STATUS``,
``CHK-CTRL-REQ-GATE``, ``CHK-CTRL-FETCH-HEAD``, ``CHK-CTRL-DATA-PAIR``.
"""


class ArbitrationMonitor:
    """Always-on top-observable grant and OE checks."""

    def __init__(self, dut) -> None:
        self.dut = dut

    async def run(self) -> None:
        """Background checker coroutine; start before reset release.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M2+ implements arbitration monitor")
