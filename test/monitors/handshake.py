"""Engine streaming handshake monitors (``CHK-HS-*``).

Relevant IDs: ``CHK-HS-TXN-START``, ``CHK-HS-REQ-STABLE``,
``CHK-HS-RDATA-COUNT``, ``CHK-HS-WDATA-COUNT``, ``CHK-HS-PULSE-WIDTH``,
``CHK-HS-WDATA-KNOWN``.
"""


class HandshakeMonitor:
    """L0-port / RTL-hierarchy handshake checker bundle."""

    def __init__(self, dut, level: str = "top") -> None:
        self.dut = dut
        self.level = level

    async def run(self) -> None:
        """Background checker coroutine; start before reset release.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M1+ implements handshake monitor")
