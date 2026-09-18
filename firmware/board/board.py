"""DemoBoard access layer: design enable, clock, rst_n, and raw ui_in/uo_out/uio ports.

No DMA protocol lives here. START / BUS_REQ / BUS_GNT / DONE sequencing is in
`firmware/dma.py`; this class only knows how to talk to a DemoBoard-like object.

Takes a *tt* object so the REPL can pass `DemoBoard.get()` and host pytest can
inject a mock. Ports may be plain integers or SDK port objects supporting
`.value` and `[bit]`; both shapes are handled.

`rst_n_low` reports the `reset_project` state this driver recorded, not a
DemoBoard private `_in_reset` attribute (the SDK has no such field).
"""

from .pins import OE_HIZ, PROJECT_CLOCK_HZ
from .qspi import sleep_us as _default_sleep_us

EXPECTED_MODE = "ASIC_RP_CONTROL"
DEFAULT_POLL_US = 100


class BoardError(Exception):
    """Missing DemoBoard API, wrong mux mode, or an unknown shuttle design."""


def select_rp_control_mode(tt):
    """Select and verify the current SDK mode required by this driver.

    The FPGA breakout starts in `ASIC_MANUAL_INPUTS` so the SDK can safely
    configure it. TinyDMA firmware then needs RP control to drive `ui_in`, so
    select `RPMode.ASIC_RP_CONTROL` before programming the design.
    """
    try:
        from ttboard.mode import RPMode  # type: ignore[import-not-found]
    except ImportError:
        return False
    if tt.mode != RPMode.ASIC_RP_CONTROL:
        tt.mode = RPMode.ASIC_RP_CONTROL
    return tt.mode == RPMode.ASIC_RP_CONTROL


def as_int(port):
    if hasattr(port, "value") and not isinstance(port, (int, bool)):
        try:
            return int(port.value)
        except (TypeError, ValueError):
            pass
    return int(port)


def set_port(obj, name, value):
    port = getattr(obj, name)
    if hasattr(port, "value") and not isinstance(port, (int, bool)):
        try:
            port.value = int(value) & 0xFF
            return
        except (TypeError, ValueError, AttributeError):
            pass
    setattr(obj, name, int(value) & 0xFF)


def bit_get(port, bit):
    if hasattr(port, "__getitem__") and not isinstance(port, (int, bool)):
        try:
            return int(port[bit])
        except (TypeError, IndexError, KeyError):
            pass
    return (as_int(port) >> bit) & 1


def bit_set(obj, name, bit, value):
    port = getattr(obj, name)
    if hasattr(port, "__setitem__") and not isinstance(port, (int, bool)):
        try:
            port[bit] = 1 if value else 0
            return
        except (TypeError, IndexError, KeyError):
            pass
    cur = as_int(port)
    if value:
        cur |= 1 << bit
    else:
        cur &= ~(1 << bit)
    set_port(obj, name, cur)


class Board:
    """Demoboard driver: mux/clock/reset plus ui_in, uo_out, and uio OE access."""

    def __init__(self, tt, sleep_us=None, poll_us=DEFAULT_POLL_US):
        self.tt = tt
        self._sleep_us = sleep_us if sleep_us is not None else _default_sleep_us
        self.poll_us = poll_us
        self._rst_held = False

    # --- time ---

    def sleep_us(self, us):
        self._sleep_us(us)

    def _now_ms(self):
        import time

        if hasattr(time, "ticks_ms"):
            return time.ticks_ms()
        return time.time() * 1000.0

    def _elapsed_ms(self, start):
        import time

        if hasattr(time, "ticks_diff"):
            return time.ticks_diff(time.ticks_ms(), start)
        return self._now_ms() - start

    def poll_until(self, predicate, timeout_ms=1000, poll_us=None):
        """Return True when *predicate* holds, False if *timeout_ms* elapses first.

        `timeout_ms=None` waits forever; `timeout_ms=0` samples once. The caller
        decides what a timeout means (dma.py kills the transfer).
        """
        nap = self.poll_us if poll_us is None else poll_us
        start = self._now_ms()
        while True:
            if predicate():
                return True
            if timeout_ms is not None and self._elapsed_ms(start) >= timeout_ms:
                return False
            self._sleep_us(nap)

    # --- ports ---

    def read_ui_in(self):
        return as_int(self.tt.ui_in)

    def write_ui_in(self, value):
        set_port(self.tt, "ui_in", value)

    def zero_ui_in(self):
        """Drive every ui_in bit to 0, including the unused ones (D34)."""
        self.write_ui_in(0)

    def read_ui_in_bit(self, bit):
        return bit_get(self.tt.ui_in, bit)

    def write_ui_in_bit(self, bit, value):
        bit_set(self.tt, "ui_in", bit, value)

    def read_uo_out(self):
        return as_int(self.tt.uo_out)

    def read_uo_out_bit(self, bit):
        return bit_get(self.tt.uo_out, bit)

    def read_uio_oe(self):
        return as_int(getattr(self.tt, "uio_oe_pico", 0))

    def write_uio_oe(self, mask):
        """Raw uio direction write. D26 legality is enforced in dma.py."""
        set_port(self.tt, "uio_oe_pico", mask)

    def hiz(self):
        self.write_uio_oe(OE_HIZ)

    # --- design / clock / reset ---

    @property
    def rst_n_low(self):
        """True while this driver holds `reset_project(True)` (DemoBoard rst_n=0)."""
        return bool(self._rst_held)

    def reset_design(self, asserted=True):
        """Drive rst_n. While held, MCU QSPI drive is legal without BUS_GNT (D26)."""
        reset = getattr(self.tt, "reset_project", None)
        if reset is None:
            raise BoardError("tt.reset_project is not available")
        reset(bool(asserted))
        self._rst_held = bool(asserted)
        if asserted:
            self.hiz()

    def _enable_shuttle_design(self, name):
        shuttle = getattr(self.tt, "shuttle", None)
        if shuttle is None:
            return
        design = getattr(shuttle, name, None)
        if design is None and hasattr(shuttle, "__getitem__"):
            try:
                design = shuttle[name]
            except Exception:
                design = None
        if design is None:
            raise BoardError("shuttle has no design %s" % name)
        enable = getattr(design, "enable", None)
        if enable is not None:
            enable()

    def enable_design(self, name, clock_hz=PROJECT_CLOCK_HZ):
        """ui_in=0, rst_n assert, mux, clock PWM, Hi-Z, rst_n release.

        *name* is required: the demoboard layer does not know which project is
        being brought up. Callers pass their own default.
        """
        if not name:
            raise BoardError("enable_design requires a project name")
        mode = getattr(self.tt, "mode", None)
        if mode is not None and not select_rp_control_mode(self.tt):
            raise BoardError("cannot select mode %s, got %s" % (EXPECTED_MODE, mode))
        self.zero_ui_in()
        self.reset_design(True)
        self._enable_shuttle_design(name)
        clock = getattr(self.tt, "clock_project_PWM", None)
        if clock is not None:
            clock(clock_hz)
        self.hiz()
        self.reset_design(False)
