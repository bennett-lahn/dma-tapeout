"""Golden chain interpreter and normalized transaction log generation.

Covers oracle semantics for ``TC-SAME-*``, ``TC-CROSS-*``, ``TC-CHAIN``,
``TC-NEXT-DEVICE``, ``TC-LEN-CORNERS``, ``TC-QUIT``, ``TC-EMPTY``,
``TC-OVERLAP``, and related directed cases. Pure Python only.
"""

from typing import Any, List, Protocol


class MemoryImage(Protocol):
    """Byte-addressed device memory interface for the interpreter."""

    def read(self, device: int, address: int, length: int) -> bytes: ...

    def write(self, device: int, address: int, data: bytes) -> None: ...

    def clone(self) -> Any: ...


def interpret_chain(
    initial_memory: MemoryImage,
    dma_buf_depth: int = 1,
    fetch_budget: int = 64,
    txn_budget: int = 4096,
) -> List[dict]:
    """Run the V1 fixed-head interpreter; return normalized transaction records.

    Raises:
        NotImplementedError: Phase 0 scaffold only.
    """
    raise NotImplementedError("M2+ implements golden chain interpreter")
