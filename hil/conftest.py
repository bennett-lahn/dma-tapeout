"""Pytest options, markers, and session fixtures for the `hil/` suite.

Discovered for any test under `hil/`. Default `--target=loopback` keeps CI and
local unit tests off the demoboard; `--target=fpga` / `asic` switch the link to
`SerialTransport` (mpremote) without changing the session verb surface.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_root = str(_REPO_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

import firmware.session as fw_session  # noqa: E402
from hil.link import Link, LoopbackTransport, SerialTransport  # noqa: E402
from hil.session import Session  # noqa: E402
from hil.target import create_target_profile  # noqa: E402

# Markers used by directed / random HIL cases (B2+). Registered here so pytest
# does not warn about unknown markers when collecting under `hil/`.
_HIL_MARKERS = (
    ("smoke", "short bring-up / sanity case"),
    ("same_device", "src and dest on the same PSRAM device"),
    ("cross_device", "src and dest on opposite PSRAM devices"),
    ("chain", "multi-TCD descriptor chain"),
    ("length", "transfer-length corner"),
    ("quit", "QUIT end-of-chain behavior"),
    ("address", "address / pointer corner"),
    ("overlap", "overlapping source / dest regions"),
    ("start", "START pulse / restart behavior"),
    ("bus", "BUS_REQ / BUS_GNT (bus request / bus grant) discipline"),
    ("reset", "rst_n kill and recovery"),
    ("random", "randomized regression case"),
)


def pytest_addoption(parser):
    group = parser.getgroup("hil", "TinyDMA hardware-in-the-loop")
    group.addoption(
        "--target",
        choices=["loopback", "fpga", "asic"],
        default="loopback",
        help="HIL target kind (default: loopback, no physical hardware)",
    )
    group.addoption(
        "--port",
        default="/dev/ttyACM0",
        help="Serial port for mpremote when --target is fpga or asic",
    )
    group.addoption(
        "--clock-hz",
        type=int,
        default=None,
        help="Override project clock Hz (default: profile / 66 MHz)",
    )
    group.addoption(
        "--sck-hz",
        type=int,
        default=None,
        help="Override PSRAM SCK Hz (default: profile / SCK_HZ_DEFAULT)",
    )
    group.addoption(
        "--seed",
        type=int,
        default=0,
        help="RNG seed for randomized HIL cases (default: 0)",
    )


def pytest_configure(config):
    for name, description in _HIL_MARKERS:
        config.addinivalue_line("markers", "%s: %s" % (name, description))


@pytest.fixture(autouse=True)
def _clear_firmware_session_state():
    """Drop `firmware.session` module state between every HIL test.

    Mirrors an MCU soft reboot: board, DmaController, and PSRAM driver are
    forgotten, and any fake-hardware factory is detached so a later test cannot
    inherit bus OE / ui_in from a previous run.
    """
    fw_session.set_hardware_factory(None)
    fw_session._clear_state()
    yield
    fw_session.set_hardware_factory(None)
    fw_session._clear_state()


@pytest.fixture
def target_profile(request):
    """Resolved `TargetProfile` from `--target` / `--clock-hz` / `--sck-hz`."""
    return create_target_profile(
        request.config.getoption("--target"),
        clock_hz=request.config.getoption("--clock-hz"),
        sck_hz=request.config.getoption("--sck-hz"),
    )


@pytest.fixture
def link(request, target_profile):
    """Envelope `Link`: loopback fake, or mpremote serial for fpga/asic."""
    if target_profile.name == "loopback":
        transport = LoopbackTransport()
    else:
        transport = SerialTransport(port=request.config.getoption("--port"))
    lnk = Link(transport)
    yield lnk
    lnk.close()


@pytest.fixture
def session(link, target_profile):
    """Brought-up host `Session`; teardown leaves the MCU bus safe.

    Setup enables the design and puts both PSRAMs in QPI (Enter Quad 0x35).
    Teardown prefers `shutdown` (Hi-Z `uio_oe_pico`, `ui_in` zeroed) so the
    next test does not suffer bus contention; falls back to `reset_recovery`
    (rst_n kill then QPI re-enter) if shutdown cannot run cleanly.
    """
    sess = Session(link, profile=target_profile)
    sess.init(target_profile)
    sess.bring_up_psram()
    try:
        yield sess
    finally:
        try:
            sess.shutdown()
        except Exception:
            try:
                sess.reset_recovery()
            except Exception:
                pass
            try:
                sess.shutdown()
            except Exception:
                pass
        sess.close()
