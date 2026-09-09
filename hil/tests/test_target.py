"""Target profile resolution, CLI defaults, and loopback fixture smoke."""

import pytest

from firmware.board.pins import OE_HIZ, PROJECT_CLOCK_HZ
from firmware.constants import SCK_HZ_DEFAULT
from hil.session import DESIGN_NAME, SessionError, TargetProfile, resolve_profile
from hil.target import (
    TARGET_KINDS,
    create_target_profile,
    resolve_target_profile,
)


def test_target_kinds_cover_loopback_fpga_asic():
    assert TARGET_KINDS == ("loopback", "asic", "fpga")
    for name in TARGET_KINDS:
        profile = resolve_profile(name)
        assert isinstance(profile, TargetProfile)
        assert profile.name == name
        assert profile.design_name == DESIGN_NAME
        assert profile.clock_hz == PROJECT_CLOCK_HZ
        assert profile.sck_hz == SCK_HZ_DEFAULT
        assert profile.bitstream is None


def test_create_target_profile_uses_cli_none_as_defaults():
    profile = create_target_profile("loopback")
    assert profile.clock_hz == PROJECT_CLOCK_HZ
    assert profile.sck_hz == SCK_HZ_DEFAULT
    assert profile.design_name == DESIGN_NAME


def test_create_target_profile_applies_rate_overrides():
    profile = create_target_profile("asic", clock_hz=33_000_000, sck_hz=10_000_000)
    assert profile.name == "asic"
    assert profile.clock_hz == 33_000_000
    assert profile.sck_hz == 10_000_000


def test_resolve_target_profile_overrides_without_mutating_base():
    base = resolve_profile("fpga")
    overridden = resolve_target_profile("fpga", clock_hz=50_000_000)
    assert overridden.clock_hz == 50_000_000
    assert overridden.sck_hz == base.sck_hz
    assert base.clock_hz == PROJECT_CLOCK_HZ


def test_create_target_profile_rejects_unknown_kind():
    with pytest.raises(SessionError, match="unknown target kind"):
        create_target_profile("wishful")


def test_fpga_bitstream_path_is_validated_and_recorded(tmp_path):
    bitstream = tmp_path / "tt_um_lahnb_sgdma.bin"
    bitstream.write_bytes(b"\x00\x01")
    profile = create_target_profile("fpga", bitstream=str(bitstream))
    assert profile.bitstream == str(bitstream.resolve())


def test_fpga_bitstream_missing_file_raises(tmp_path):
    missing = tmp_path / "missing.bin"
    with pytest.raises(SessionError, match="bitstream not found"):
        create_target_profile("fpga", bitstream=str(missing))


def test_bitstream_rejected_for_non_fpga_targets(tmp_path):
    bitstream = tmp_path / "design.bin"
    bitstream.write_bytes(b"x")
    for name in ("loopback", "asic"):
        with pytest.raises(SessionError, match="only valid for fpga"):
            create_target_profile(name, bitstream=str(bitstream))


def test_cli_option_defaults(request):
    assert request.config.getoption("--target") == "loopback"
    assert request.config.getoption("--port") == "/dev/ttyACM0"
    assert request.config.getoption("--clock-hz") is None
    assert request.config.getoption("--sck-hz") is None
    assert request.config.getoption("--seed") == 0


def test_target_profile_fixture_defaults_to_loopback(target_profile):
    assert target_profile.name == "loopback"
    assert target_profile.clock_hz == PROJECT_CLOCK_HZ
    assert target_profile.sck_hz == SCK_HZ_DEFAULT


def test_link_fixture_is_loopback(link, target_profile):
    assert target_profile.name == "loopback"
    assert type(link.transport).__name__ == "LoopbackTransport"


def test_session_fixture_bring_up(session):
    status = session.get_status()
    assert status["done"] is True
    assert status["bus_gnt"] is False
    assert status["in_qpi"] is True
    assert status["oe"] == OE_HIZ


def test_session_shutdown_leaves_bus_safe(session):
    """shutdown Hi-Zs uio_oe_pico and zeroes ui_in (no bus contention)."""
    hardware = session.link.transport.hardware
    session.shutdown()
    assert int(hardware.tt.uio_oe_pico) == OE_HIZ
    assert int(hardware.tt.ui_in) == 0
