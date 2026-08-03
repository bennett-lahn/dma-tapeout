"""Timing-oriented runtime checks bridging ``Q-*`` and reset truncation rules.

Milestone M1+ connects ``TIMING_PROFILE`` delays and ``RESET-TRUNCATED``
classification per ``04-timing-in-sim.md``.
"""


class TimingMonitor:
    """Optional timing-layer checker companion to ``models.psram_timing``."""

    def __init__(self, dut, profile: str = "ideal") -> None:
        self.dut = dut
        self.profile = profile

    async def run(self) -> None:
        """Background timing checker coroutine.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M1+ implements timing monitor")
