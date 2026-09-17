"""Pytest options, markers, and session fixtures for the `hil/` suite.

`--target` has no default and is required by any test that touches hardware:
`loopback` is an in-process fake and must be asked for by name, so nobody
mistakes a fake-hardware pass for silicon. `fpga` / `asic` need a demoboard and
are never a GitHub Action.

`--target=fpga` compiles and uploads a fresh bitstream by itself, so the whole
FPGA run is `pytest hil/tests/ --target=fpga`. `--bitstream` only exists to opt
out of that: `reuse` keeps the existing `.bin` (and refuses a stale one),
`stale` accepts an out-of-date `.bin` on purpose.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_root = str(_REPO_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

import firmware.session as fw_session  # noqa: E402
from hil.detail import DEFAULT_DETAIL_BYTES, DetailReporter  # noqa: E402
from hil.link import Link, LoopbackTransport, SerialTransport  # noqa: E402
from hil.session import DESIGN_NAME, Session  # noqa: E402
from hil.target import create_target_profile  # noqa: E402

_DEFAULT_FPGA_BIN = _REPO_ROOT / "hil" / "fpga" / "build" / ("%s.bin" % DESIGN_NAME)

TARGET_KINDS = ("loopback", "fpga", "asic")

# `--bitstream` policy for `--target=fpga`. Default is `build`: a run always
# reflects the RTL in the tree unless the operator says otherwise.
BITSTREAM_BUILD = "build"
BITSTREAM_REUSE = "reuse"
BITSTREAM_STALE = "stale"
BITSTREAM_POLICIES = (BITSTREAM_BUILD, BITSTREAM_REUSE, BITSTREAM_STALE)

# Fixtures that mean "this test drives a target", so `--target` must be set.
_TARGET_FIXTURES = frozenset({"target_profile", "link", "session"})

# The one fixture that always performs live I/O against whatever `--target`
# was chosen (its own setup calls `sess.init(...)`). Any test that asks for
# it is, by construction, a hardware/DUT test rather than a host-only
# self-test, so it is auto-tagged `hw` below instead of relying on every test
# author to remember a marker. By convention its function name also carries
# a `test_hw_` prefix (see `hil/tests/test_directed.py` etc.) so the split is
# visible without pytest at all.
_HARDWARE_FIXTURE = "session"
HARDWARE_MARKER = "hw"

# Markers used by directed / random HIL cases (B2+), plus the two structural
# markers above. Registered here so pytest does not warn about unknown
# markers when collecting under `hil/`.
_HIL_MARKERS = (
    (HARDWARE_MARKER, "drives the selected --target (real DUT, or the loopback fake "
     "when asked for by name); auto-applied to any test that takes the `session` "
     "fixture, never set by hand"),
    ("checks", "checker-library / session sanity exercised against a live session "
     "(descriptor integrity, host-status idle, bring-up, shutdown)"),
    ("smoke", "short bring-up / sanity case"),
    ("same_device", "src and dest on the same PSRAM device"),
    ("cross_device", "src and dest on opposite PSRAM devices"),
    ("chain", "multi-TCD descriptor chain"),
    ("next_device", "alternating NEXT_DEVICE descriptor fetch"),
    ("length", "transfer-length corner"),
    ("quit", "QUIT end-of-chain behavior"),
    ("address", "address / pointer corner"),
    ("overlap", "overlapping source / dest regions"),
    ("restart", "second START re-fetches the fixed head"),
    ("start", "START pulse / restart behavior"),
    ("bus", "BUS_REQ / BUS_GNT (bus request / bus grant) discipline"),
    ("reset", "rst_n kill and recovery"),
    ("random", "randomized regression case"),
)


def pytest_addoption(parser):
    group = parser.getgroup("hil", "TinyDMA hardware-in-the-loop")
    group.addoption(
        "--target",
        choices=list(TARGET_KINDS),
        default=None,
        help="HIL target kind; required by any test that drives a target. "
        "loopback = in-process fake hardware (must be asked for by name)",
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
    group.addoption(
        "--bitstream",
        choices=list(BITSTREAM_POLICIES),
        default=None,
        help="FPGA bitstream policy (--target=fpga only). build (default) "
        "compiles a fresh .bin and uploads it; reuse uploads the existing "
        "hil/fpga/build/<top>.bin and fails if it is older than the RTL; "
        "stale uploads it anyway",
    )
    group.addoption(
        "--detail",
        action="store_true",
        help="Print a verbose block per case even when it passes: stimulus, "
        "descriptors, oracle transactions, expected vs actual dest bytes, "
        "guards, and host status",
    )
    group.addoption(
        "--detail-bytes",
        type=int,
        default=DEFAULT_DETAIL_BYTES,
        help="Destination bytes to hex-dump per case under --detail "
        "(default: %d; 0 keeps the per-extent summary only)" % DEFAULT_DETAIL_BYTES,
    )


def pytest_configure(config):
    for name, description in _HIL_MARKERS:
        config.addinivalue_line("markers", "%s: %s" % (name, description))
    policy = config.getoption("--bitstream")
    if policy is not None and config.getoption("--target") != "fpga":
        raise pytest.UsageError("--bitstream applies to --target=fpga only")


def bitstream_policy(config):
    """Return the effective `--bitstream` policy, or None off the fpga target."""
    if config.getoption("--target") != "fpga":
        return None
    return config.getoption("--bitstream") or BITSTREAM_BUILD


def pytest_collection_modifyitems(config, items):
    """Auto-tag hardware tests, then refuse to run them without `--target`.

    Any test that takes the `session` fixture is, by construction, a
    hardware/DUT test (its setup always performs live I/O against whatever
    `--target` was chosen), so it is tagged `hw` here regardless of markers.
    `hil/run.py`'s interactive menu uses this tag to keep hardware categories
    strictly separate from host-only self-tests; nobody has to remember to
    mark a new hardware test by hand.

    Loopback is a fake, so it may never be inherited from a default. Pure
    host self-tests (link parsing, checkers, the FPGA tool) take no target
    fixture and still run with no flags at all.
    """
    for item in items:
        if _HARDWARE_FIXTURE in getattr(item, "fixturenames", ()):
            item.add_marker(HARDWARE_MARKER)
    if config.getoption("--target") is not None:
        return
    needs_target = sorted(
        {
            item.nodeid
            for item in items
            if _TARGET_FIXTURES.intersection(getattr(item, "fixturenames", ()))
        }
    )
    if not needs_target:
        return
    raise pytest.UsageError(
        "--target is required by %d selected test(s); choose one of %s. "
        "loopback is fake hardware and must be requested by name (first: %s)"
        % (len(needs_target), ", ".join(TARGET_KINDS), needs_target[0])
    )


def pytest_report_header(config):
    """Name the target at the top of the run; shout when it is a fake."""
    target = config.getoption("--target")
    if target is None:
        return "hil: no --target selected (host unit tests only)"
    if target == "loopback":
        return (
            "hil: target=LOOPBACK - in-process FAKE hardware, no demoboard, "
            "no silicon. Not evidence for M7."
        )
    line = "hil: target=%s port=%s" % (target, config.getoption("--port"))
    if target == "fpga":
        line += " bitstream=%s" % bitstream_policy(config)
    return line


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Repeat the loopback warning after the run, where the result is read."""
    if config.getoption("--target") != "loopback":
        return
    terminalreporter.write_sep(
        "=", "HIL ran on LOOPBACK (fake hardware) - not FPGA, not silicon"
    )


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


def _resolve_cli_bitstream(config):
    """Return the `.bin` this run installed, for the record in `TargetProfile`.

    Only meaningful on `--target=fpga`; `_prepare_fpga_bitstream` has already
    built or vetted the file by the time a test asks.
    """
    if config.getoption("--target") != "fpga":
        return None
    return str(_DEFAULT_FPGA_BIN) if _DEFAULT_FPGA_BIN.is_file() else None


def _detail_writer(config):
    """Route detail blocks through the terminal reporter so capture keeps them."""
    terminal = config.pluginmanager.getplugin("terminalreporter")
    if terminal is None:
        return print

    def write(text):
        terminal.ensure_newline()
        for line in text.splitlines():
            terminal.write_line(line)

    return write


@pytest.fixture
def detail(request):
    """Verbose per-case reporter. Disabled (no-op) unless `--detail` is set."""
    return DetailReporter(
        enabled=request.config.getoption("--detail"),
        writer=_detail_writer(request.config),
        max_bytes=request.config.getoption("--detail-bytes"),
        target=request.config.getoption("--target"),
        seed=request.config.getoption("--seed"),
    )


@pytest.fixture
def target_profile(request):
    """Resolved `TargetProfile` from `--target` / `--clock-hz` / `--sck-hz`."""
    return create_target_profile(
        request.config.getoption("--target"),
        clock_hz=request.config.getoption("--clock-hz"),
        sck_hz=request.config.getoption("--sck-hz"),
        bitstream=_resolve_cli_bitstream(request.config),
    )


def vet_existing_bitstream(policy, bin_path, *, stale_inputs, describe, error):
    """Return `bin_path` when `policy` allows installing an existing `.bin`.

    `reuse` demands a bitstream at least as new as every build input; only
    `stale` waives that. The staleness helpers are injected so this decision
    can be tested without the FPGA toolchain.

    Raises:
        pytest.UsageError: the file is missing, or it is stale under `reuse`.
    """
    if not bin_path.is_file():
        raise pytest.UsageError(
            "--bitstream=%s needs %s; run --bitstream=build (the default) "
            "or python -m hil bitstream" % (policy, bin_path)
        )
    try:
        newer = stale_inputs(bin_path)
    except error as exc:
        raise pytest.UsageError(str(exc)) from exc
    if newer and policy != BITSTREAM_STALE:
        raise pytest.UsageError(
            "refusing a stale bitstream: %s. Rebuild with --bitstream=build "
            "(the default), or accept it with --bitstream=stale"
            % describe(bin_path)
        )
    return bin_path


@pytest.fixture(scope="session", autouse=True)
def _prepare_fpga_bitstream(request):
    """Put a known bitstream on the demoboard before any `--target=fpga` test.

    `build` (default) compiles from the RTL in the tree, so a plain
    `--target=fpga` run can never test something older than the source.
    `reuse` keeps the existing `.bin` but refuses it once any build input is
    newer; `stale` is the explicit override for that refusal. Every policy
    ends with an upload, so the board and `hil/fpga/build/` agree.
    """
    policy = bitstream_policy(request.config)
    if policy is None:
        return
    import os

    from hil.fpga.tool import (
        BREAKOUT_FABRICFOX,
        FpgaToolError,
        describe_staleness,
        harden,
        stale_inputs,
        upload_bitstream,
    )

    if policy == BITSTREAM_BUILD:
        bin_path = harden(breakout=os.environ.get("FPGA_BREAKOUT", BREAKOUT_FABRICFOX))
    else:
        bin_path = vet_existing_bitstream(
            policy,
            _DEFAULT_FPGA_BIN,
            stale_inputs=stale_inputs,
            describe=describe_staleness,
            error=FpgaToolError,
        )
    upload_bitstream(
        bin_path,
        port=request.config.getoption("--port"),
        remote_name="%s.bin" % DESIGN_NAME,
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

    Setup enables the design and puts both PSRAMs in QPI (SPI 0x66/0x99,
    Enter Quad 0x35) under grant, then releases the bus before the test
    runs. Teardown calls `shutdown()` with `exit_qpi=False`: MCU Hi-Z and
    `ui_in` zeroed only. It does not Exit Quad 0xF5 or SPI-reset the chips,
    so they stay in QPI. Falls back to `reset_recovery` (rst_n kill then
    QPI re-enter, not SPI leave) if shutdown cannot run cleanly.
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
