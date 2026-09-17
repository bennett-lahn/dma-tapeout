"""Pythonic host-side TinyDMA session over a `Link`.

Each method is one remote statement against `firmware.session`, so the HIL
suite can read like host code (`write_spans`, `start_and_wait`, `read_spans`)
while the MCU keeps the board, controller, and PSRAM driver alive between
calls. Payload bytes cross as base64 because the transport is a text REPL line.

Target profiles name what to enable (`loopback`, `asic`, `fpga`). The FPGA
profile is not a different protocol: the M7 breakout bitstream is copied to
`/bitstreams` and appears under the same shuttle name, and the project clock
stays 66 MHz (D16), so the same firmware drives START against FPGA or ASIC.
`loopback` uses the same rates with in-process fake hardware.
"""

from firmware.board.pins import PROJECT_CLOCK_HZ
from firmware.constants import SCK_HZ_DEFAULT
from firmware.link import b64encode

from .fpga.tool import fpga_clock_hz
from .link import Link, LoopbackTransport

# info.yaml top_module; also the /bitstreams name for the M7 FPGA stand-in.
DESIGN_NAME = "tt_um_lahnb_sgdma"

DEFAULT_TIMEOUT_MS = 5000
DEFAULT_PROFILE = "asic"


class SessionError(Exception):
    """The host asked for something the remote session cannot honor, or the
    remote reply did not match the request."""


class TargetProfile:
    """What to enable and at what rates for one target (loopback, ASIC, or FPGA).

    `bitstream` is host-side metadata for FPGA targets (path to a `.bin` under
    `/bitstreams` or a local copy); the MCU still enables `design_name`.
    """

    def __init__(
        self,
        name,
        design_name=DESIGN_NAME,
        clock_hz=PROJECT_CLOCK_HZ,
        sck_hz=SCK_HZ_DEFAULT,
        bitstream=None,
    ):
        self.name = name
        self.design_name = design_name
        self.clock_hz = int(clock_hz)
        self.sck_hz = int(sck_hz)
        self.bitstream = bitstream

    def __repr__(self):
        return (
            "TargetProfile(%r, design_name=%r, clock_hz=%d, sck_hz=%d, bitstream=%r)"
            % (
                self.name,
                self.design_name,
                self.clock_hz,
                self.sck_hz,
                self.bitstream,
            )
        )


TARGET_PROFILES = {
    "loopback": TargetProfile("loopback"),
    "asic": TargetProfile("asic"),
    "fpga": TargetProfile("fpga", clock_hz=fpga_clock_hz()),
}


def resolve_profile(profile):
    """Return a `TargetProfile` from a profile object or a known profile name."""
    if isinstance(profile, TargetProfile):
        return profile
    try:
        return TARGET_PROFILES[profile]
    except KeyError:
        raise SessionError(
            "unknown target profile %r; known: %s"
            % (profile, ", ".join(sorted(TARGET_PROFILES)))
        )


class Session:
    """Host-side wrapper over the remote `firmware.session` entry points."""

    def __init__(self, link, profile=DEFAULT_PROFILE):
        self.link = link
        self.profile = resolve_profile(profile)

    @classmethod
    def loopback(cls, hardware=None, profile=DEFAULT_PROFILE):
        """Build a session over an in-process loopback (no hardware needed)."""
        return cls(Link(LoopbackTransport(hardware=hardware)), profile=profile)

    # --- bring-up ---

    def init(self, target_profile=None):
        """Enable the target design and leave the host port idle.

        Returns:
            dict: the design name and the clock / SCK rates the MCU applied.
        """
        profile = resolve_profile(
            self.profile if target_profile is None else target_profile
        )
        self.profile = profile
        result = self.link.call_json(
            "session.init(%r, clock_hz=%d, sck_hz=%d)"
            % (profile.design_name, profile.clock_hz, profile.sck_hz)
        )
        if result.get("design") != profile.design_name:
            raise SessionError(
                "remote enabled %r, expected %r"
                % (result.get("design"), profile.design_name)
            )
        return result

    def bring_up_psram(self, force=False):
        """Ensure both PSRAMs are in QPI (Enter Quad 0x35) before START.

        Returns:
            str: ``"qpi"`` if this call brought them up, ``"already-qpi"`` if
            the session had already done it.
        """
        return self.link.call_text("session.bring_up_psram(force=%r)" % bool(force))

    # --- data ---

    def write_spans(self, spans):
        """QPI-write each `(device, addr, bytes)` span; return the byte total.

        Raises:
            SessionError: the MCU reported a different length than was sent.
        """
        total = 0
        for device, addr, data in spans:
            payload = bytes(data)
            result = self.link.call_json(
                "session.write_span(%d, %d, '%s')"
                % (int(device), int(addr), b64encode(payload))
            )
            if result.get("length") != len(payload):
                raise SessionError(
                    "wrote %d bytes to %d:0x%06X, MCU reported %r"
                    % (len(payload), int(device), int(addr), result.get("length"))
                )
            total += len(payload)
        return total

    def read_spans(self, extents):
        """QPI-read each `(device, addr, length)` extent.

        Returns:
            dict: ``{(device, address): byte}``, one entry per byte read, so a
            comparison against expected bytes names the exact address.

        Raises:
            SessionError: an extent came back short.
        """
        out = {}
        for device, addr, length in extents:
            device = int(device)
            addr = int(addr)
            length = int(length)
            data = self.link.call_bytes(
                "session.read_span(%d, %d, %d)" % (device, addr, length)
            )
            if len(data) != length:
                raise SessionError(
                    "read %d:0x%06X expected %d bytes, got %d"
                    % (device, addr, length, len(data))
                )
            for offset, value in enumerate(data):
                out[(device, addr + offset)] = value
        return out

    def qpi_probe_read(self, device, addr, length):
        """Bring-up probe: read *length* bytes in one `0xEB` frame, unchunked.

        Unlike `read_spans` this does not split on `Psram.eb_chunk`, so the
        returned bytes are one uninterrupted nibble stream and a read-phase
        error shows up as a shift across the whole frame.

        Raises:
            SessionError: the frame came back short.
        """
        device = int(device)
        addr = int(addr)
        length = int(length)
        data = self.link.call_bytes(
            "session.qpi_probe_read(%d, %d, %d)" % (device, addr, length)
        )
        if len(data) != length:
            raise SessionError(
                "probe read %d:0x%06X expected %d bytes, got %d"
                % (device, addr, length, len(data))
            )
        return data

    # --- run ---

    def start_and_wait(self, timeout_ms=DEFAULT_TIMEOUT_MS):
        """Pulse START with the bus released, then wait until the ASIC is idle.

        Raises:
            RemoteError: DONE never returned. The MCU has already killed the
                transfer with rst_n; call `reset_recovery` before retrying.
        """
        self.link.call_none("session.start_and_wait(timeout_ms=%d)" % int(timeout_ms))

    def reset_recovery(self):
        """Recover from a timeout or runaway chain.

        Kill is rst_n only (D23): assert rst_n with the MCU Hi-Z and `ui_in`
        zeroed, then release rst_n, recheck DONE=1 / BUS_GNT=0, and put both
        devices back in QPI (Exit Quad 0xF5 first, since a kill does not reset
        the PSRAMs).

        Returns:
            str: ``"qpi"`` once the devices are usable again.
        """
        self.link.call_none("session.reset_dma()")
        return self.link.call_text("session.recover()")

    def get_status(self):
        """Return the sampled host port: done, bus_gnt, bus_req, oe, rst_n_low."""
        return self.link.call_json("session.status()")

    def shutdown(self, exit_qpi=False):
        """Leave the board safe (Hi-Z, `ui_in`=0) and drop the remote session."""
        self.link.call_none("session.shutdown(exit_qpi=%r)" % bool(exit_qpi))

    def close(self):
        """Close the underlying link."""
        self.link.close()
