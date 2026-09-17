"""Target profile resolution, CLI flag policy, and target fixture smoke.

Everything here is a host-only self-test except the two `test_hw_` functions
near the bottom, which take the real `session` fixture and so drive whatever
`--target` was selected (hardware/DUT tests, auto-tagged `hw`).
`test_target_fixtures_follow_the_selected_target` takes `link` but never
calls it, so it stays a self-test: it only checks which transport class the
fixture wired up for the chosen target, never talking to hardware.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from firmware.board.pins import OE_HIZ, PROJECT_CLOCK_HZ
from firmware.constants import SCK_HZ_DEFAULT
from hil.conftest import (
    BITSTREAM_POLICIES,
    BITSTREAM_REUSE,
    BITSTREAM_STALE,
    bitstream_policy,
    vet_existing_bitstream,
)
from hil.fpga.tool import fpga_clock_hz
from hil.session import DESIGN_NAME, SessionError, TargetProfile, resolve_profile
from hil.target import (
    TARGET_KINDS,
    create_target_profile,
    resolve_target_profile,
)


def test_selftest_target_kinds_cover_loopback_fpga_asic():
    assert TARGET_KINDS == ("loopback", "asic", "fpga")
    for name in TARGET_KINDS:
        profile = resolve_profile(name)
        assert isinstance(profile, TargetProfile)
        assert profile.name == name
        assert profile.design_name == DESIGN_NAME
        expected_clock_hz = fpga_clock_hz() if name == "fpga" else PROJECT_CLOCK_HZ
        assert profile.clock_hz == expected_clock_hz
        assert profile.sck_hz == SCK_HZ_DEFAULT
        assert profile.bitstream is None


def test_selftest_create_target_profile_uses_cli_none_as_defaults():
    profile = create_target_profile("loopback")
    assert profile.clock_hz == PROJECT_CLOCK_HZ
    assert profile.sck_hz == SCK_HZ_DEFAULT
    assert profile.design_name == DESIGN_NAME


def test_selftest_create_target_profile_applies_rate_overrides():
    profile = create_target_profile("asic", clock_hz=33_000_000, sck_hz=10_000_000)
    assert profile.name == "asic"
    assert profile.clock_hz == 33_000_000
    assert profile.sck_hz == 10_000_000


def test_selftest_resolve_target_profile_overrides_without_mutating_base():
    base = resolve_profile("fpga")
    overridden = resolve_target_profile("fpga", clock_hz=50_000_000)
    assert overridden.clock_hz == 50_000_000
    assert overridden.sck_hz == base.sck_hz
    assert base.clock_hz == fpga_clock_hz()


def test_selftest_create_target_profile_rejects_unknown_kind():
    with pytest.raises(SessionError, match="unknown target kind"):
        create_target_profile("wishful")


def test_selftest_fpga_bitstream_path_is_validated_and_recorded(tmp_path):
    bitstream = tmp_path / "tt_um_lahnb_sgdma.bin"
    bitstream.write_bytes(b"\x00\x01")
    profile = create_target_profile("fpga", bitstream=str(bitstream))
    assert profile.bitstream == str(bitstream.resolve())


def test_selftest_fpga_bitstream_missing_file_raises(tmp_path):
    missing = tmp_path / "missing.bin"
    with pytest.raises(SessionError, match="bitstream not found"):
        create_target_profile("fpga", bitstream=str(missing))


def test_selftest_bitstream_rejected_for_non_fpga_targets(tmp_path):
    bitstream = tmp_path / "design.bin"
    bitstream.write_bytes(b"x")
    for name in ("loopback", "asic"):
        with pytest.raises(SessionError, match="only valid for fpga"):
            create_target_profile(name, bitstream=str(bitstream))


def test_selftest_cli_option_defaults(request):
    assert request.config.getoption("--port") == "/dev/ttyACM0"
    assert request.config.getoption("--clock-hz") is None
    assert request.config.getoption("--sck-hz") is None
    assert request.config.getoption("--seed") == 0


def test_selftest_target_and_bitstream_have_no_silent_defaults(request):
    """Neither fake hardware nor a bitstream policy may be inherited."""
    target = request.config.getoption("--target")
    assert target in TARGET_KINDS, "a target fixture ran without --target"
    policy = request.config.getoption("--bitstream")
    assert policy in (None,) + BITSTREAM_POLICIES
    if target != "fpga":
        assert policy is None


def test_selftest_bitstream_policy_is_fpga_only_and_defaults_to_build(request):
    config = request.config
    if config.getoption("--target") == "fpga":
        assert bitstream_policy(config) in BITSTREAM_POLICIES
    else:
        assert bitstream_policy(config) is None


def test_selftest_target_fixtures_follow_the_selected_target(request, target_profile, link):
    selected = request.config.getoption("--target")
    assert target_profile.name == selected
    expected_clock_hz = fpga_clock_hz() if selected == "fpga" else PROJECT_CLOCK_HZ
    assert target_profile.clock_hz == expected_clock_hz
    assert target_profile.sck_hz == SCK_HZ_DEFAULT
    expected_transport = (
        "LoopbackTransport" if selected == "loopback" else "SerialTransport"
    )
    assert type(link.transport).__name__ == expected_transport


class _Boom(Exception):
    pass


def _vet(policy, bin_path, newer=()):
    return vet_existing_bitstream(
        policy,
        bin_path,
        stale_inputs=lambda _path: tuple(newer),
        describe=lambda _path: "1 input(s) newer than the bitstream: src/top.v",
        error=_Boom,
    )


def test_selftest_reuse_accepts_a_bitstream_newer_than_every_input(tmp_path):
    bin_path = tmp_path / "design.bin"
    bin_path.write_bytes(b"\x00")
    assert _vet(BITSTREAM_REUSE, bin_path) is bin_path


def test_selftest_reuse_refuses_a_stale_bitstream(tmp_path):
    bin_path = tmp_path / "design.bin"
    bin_path.write_bytes(b"\x00")
    with pytest.raises(pytest.UsageError, match="refusing a stale bitstream"):
        _vet(BITSTREAM_REUSE, bin_path, newer=[tmp_path / "src" / "top.v"])


def test_selftest_stale_policy_is_the_only_way_to_install_an_old_bitstream(tmp_path):
    bin_path = tmp_path / "design.bin"
    bin_path.write_bytes(b"\x00")
    assert _vet(BITSTREAM_STALE, bin_path, newer=[tmp_path / "src" / "top.v"]) is bin_path


def test_selftest_reuse_without_a_built_bitstream_points_at_build(tmp_path):
    with pytest.raises(pytest.UsageError, match="run --bitstream=build"):
        _vet(BITSTREAM_REUSE, tmp_path / "missing.bin")


def test_selftest_running_without_a_target_is_a_usage_error():
    """A target-driving test must refuse to fall back to fake hardware."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "hil/tests/test_target.py::test_hw_session_fixture_bring_up",
            "-q",
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == pytest.ExitCode.USAGE_ERROR
    assert "--target is required" in (done.stderr + done.stdout)


# Both tests below take the `session` fixture, so they drive whatever
# `--target` was selected (hardware/DUT tests: `test_hw_` prefix, auto-tagged
# `hw`, and grouped into the `hw_checks` menu category with the checker
# sanity tests in `test_checks.py`).


@pytest.mark.checks
def test_hw_session_fixture_bring_up(session):
    status = session.get_status()
    assert status["done"] is True
    assert status["bus_gnt"] is False
    assert status["in_qpi"] is True
    assert status["oe"] == OE_HIZ


@pytest.mark.checks
def test_hw_session_shutdown_leaves_bus_safe(request, session):
    """shutdown Hi-Zs uio_oe_pico and zeroes ui_in (no bus contention)."""
    if request.config.getoption("--target") != "loopback":
        pytest.skip("inspects the in-process fake board; loopback only")
    hardware = session.link.transport.hardware
    session.shutdown()
    assert int(hardware.tt.uio_oe_pico) == OE_HIZ
    assert int(hardware.tt.ui_in) == 0
    # Default shutdown does not Exit Quad or SPI-reset; devices stay in QPI.
    assert hardware.transport.qpi == {0: True, 1: True}
