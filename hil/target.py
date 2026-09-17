"""HIL target kinds and CLI-friendly profile construction.

Re-exports `TargetProfile` / `resolve_profile` from `hil.session` and adds
`create_target_profile` for pytest / runner overrides (`--clock-hz`,
`--sck-hz`, optional FPGA bitstream path).

Target kinds:

* ``loopback`` - in-process fake hardware (default for unit tests; no board).
* ``asic`` - real demoboard with the shuttle ASIC mux-selected.
* ``fpga`` - same demoboard and firmware; M7 breakout bitstream appears under
  the same shuttle name (D28). Optional `bitstream` records / validates a
  host-side `.bin` path; the MCU still enables `design_name`.
"""

from pathlib import Path

from firmware.board.pins import PROJECT_CLOCK_HZ
from firmware.constants import SCK_HZ_DEFAULT
from hil.fpga.tool import fpga_clock_hz

from .session import (
    DESIGN_NAME,
    TARGET_PROFILES,
    SessionError,
    TargetProfile,
    resolve_profile,
)

TARGET_KINDS = ("loopback", "asic", "fpga")


def create_target_profile(
    name,
    design_name=None,
    clock_hz=None,
    sck_hz=None,
    bitstream=None,
):
    """Build a `TargetProfile` with CLI-friendly optional overrides.

    ``None`` for `design_name` / `clock_hz` / `sck_hz` keeps project defaults
    (`DESIGN_NAME`, `PROJECT_CLOCK_HZ` / 66 MHz project clock, `SCK_HZ_DEFAULT`
    / default PSRAM SCK). For ``fpga``, a provided `bitstream` path is
    resolved and must exist as a file; other kinds reject a bitstream.
    """
    if name not in TARGET_KINDS:
        raise SessionError(
            "unknown target kind %r; known: %s"
            % (name, ", ".join(TARGET_KINDS))
        )

    recorded = None
    if bitstream is not None:
        if name != "fpga":
            raise SessionError(
                "bitstream path is only valid for fpga targets, got %r" % (name,)
            )
        path = Path(bitstream)
        if not path.is_file():
            raise SessionError("bitstream not found: %s" % (path,))
        recorded = str(path.resolve())

    default_clock_hz = fpga_clock_hz() if name == "fpga" else PROJECT_CLOCK_HZ
    return TargetProfile(
        name,
        design_name=DESIGN_NAME if design_name is None else design_name,
        clock_hz=default_clock_hz if clock_hz is None else clock_hz,
        sck_hz=SCK_HZ_DEFAULT if sck_hz is None else sck_hz,
        bitstream=recorded,
    )


def resolve_target_profile(
    profile,
    design_name=None,
    clock_hz=None,
    sck_hz=None,
    bitstream=None,
):
    """Resolve a profile name or object, then apply optional CLI overrides.

    Unknown names still raise via `resolve_profile`. Overrides of ``None`` leave
    the resolved base value unchanged.
    """
    base = resolve_profile(profile)
    return create_target_profile(
        base.name,
        design_name=base.design_name if design_name is None else design_name,
        clock_hz=base.clock_hz if clock_hz is None else clock_hz,
        sck_hz=base.sck_hz if sck_hz is None else sck_hz,
        bitstream=base.bitstream if bitstream is None else bitstream,
    )


__all__ = [
    "DESIGN_NAME",
    "PROJECT_CLOCK_HZ",
    "SCK_HZ_DEFAULT",
    "TARGET_KINDS",
    "TARGET_PROFILES",
    "SessionError",
    "TargetProfile",
    "create_target_profile",
    "resolve_profile",
    "resolve_target_profile",
]
