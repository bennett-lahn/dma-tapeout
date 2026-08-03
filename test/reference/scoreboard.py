"""Dual-axis scoreboard: ordered transactions and final memory images.

Supports normal quit completion, reset-interrupted epochs (``TC-RESET-ACTIVE``),
and repeated-run equality (``TC-RESET-REPEAT``). Pure Python only.
"""


class Scoreboard:
    """Compare observed monitor records and memory against the golden oracle."""

    def __init__(self, expected_transactions: list, expected_memory) -> None:
        self.expected_transactions = expected_transactions
        self.expected_memory = expected_memory

    def compare_transactions(self, observed: list) -> None:
        """Axis 1: ordered transaction log equality.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M2+ implements transaction scoreboard axis")

    def compare_memory(self, observed_memory) -> None:
        """Axis 2: final PSRAM image equality on normal completion.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M2+ implements memory scoreboard axis")

    def compare_reset_prefix(self, observed: list, reset_index: int) -> None:
        """Compare completed transactions before a sampled reset edge.

        Raises:
            NotImplementedError: Phase 0 scaffold only.
        """
        raise NotImplementedError("M2+ implements reset-prefix scoreboard rules")
