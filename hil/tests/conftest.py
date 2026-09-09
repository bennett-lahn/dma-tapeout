"""Make the repo root and `test/` importable; keep `firmware.session` per-test."""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_DIR = _REPO_ROOT / "test"
for _path in (str(_REPO_ROOT), str(_TEST_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import firmware.session as session  # noqa: E402

_MARKERS = (
    "smoke: HIL smoke directed cases",
    "same_device: same-device PSRAM copy cases",
    "cross_device: cross-device PSRAM copy cases",
    "chain: multi-TCD chain cases",
    "next_device: alternating NEXT_DEVICE fetch cases",
    "length: transfer-length corner cases",
    "quit: QUIT / empty-chain cases",
    "restart: second-START / fixed-head restart cases",
    "address: wide-address / ptr[23] cases",
    "overlap: overlapping src/dest chunking cases",
)


def pytest_configure(config):
    for marker in _MARKERS:
        config.addinivalue_line("markers", marker)


@pytest.fixture(autouse=True)
def clean_session():
    """`firmware.session` is module-level state; no test may inherit another's.

    Mirrors what an MCU soft reboot does: the board, controller, and PSRAM
    driver are dropped, and the fake hardware factory is detached so a stray
    call cannot reach a previous test's fake board.
    """
    session.set_hardware_factory(None)
    session._clear_state()
    yield
    session.set_hardware_factory(None)
    session._clear_state()
