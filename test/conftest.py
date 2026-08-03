"""Pytest configuration for the dma-tapeout verification platform.

Pytest owns Python-side collection and configuration; cocotb owns simulator
scheduling. Fixtures must not cross the simulator boundary without an explicit
adapter (see docs/llm/verification/02-platform.md).
"""

from __future__ import annotations


def pytest_configure(config) -> None:
    """Reserved for custom markers and platform hooks (M0+)."""
    config.addinivalue_line(
        "markers",
        "cocotb: cocotb test collected for simulator execution",
    )


def pytest_collection_modifyitems(items) -> None:
    """Keep collection deterministic; randomness comes from SEED, not order."""
    items.sort(key=lambda item: item.nodeid)
