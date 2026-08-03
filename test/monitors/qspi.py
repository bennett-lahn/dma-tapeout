"""Pin-level QPI decoder and ``CHK-PIN-*`` monitors.

Relevant IDs: ``CHK-PIN-CS-MUTEX``, ``CHK-PIN-FLASH-HIGH``,
``CHK-PIN-ADDR23-ZERO``, ``CHK-PIN-KNOWN``, ``CHK-PIN-SIO-OWN``,
``CHK-PIN-SCK-PARK``, ``CHK-HS-OPCODE`` (wait-cycle count at pins).
"""


class QspiPinMonitor:
    """Decode resolved CE#, SCK, SIO into normalized transaction records."""

    def __init__(self, dut) -> None:
        self.dut = dut

    async def run(self) -> None:
        """Background monitor coroutine; start before reset release.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M1+ implements QSPI pin monitor")

    def transactions(self) -> list:
        """Return completed normalized records for scoreboard compare.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M1+ implements QSPI transaction log export")
