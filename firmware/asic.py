"""DemoBoard host protocol: enable, clock, rst_n/kill, BUS_REQ/GNT, START, DONE.

Unused `ui_in` bits stay 0 (D34). MCU QSPI `uio_oe_pico` is Hi-Z unless
`BUS_GNT=1` or `rst_n=0`. After pulsing START, do not raise BUS_REQ until
DONE falls (START-accept ACK across the two-flop sync).

Takes a `tt` object so REPL can pass `DemoBoard.get()` and tests can inject a
mock with integer `ui_in` / `uo_out` / `uio_oe_pico`.
"""

from .psram import sleep_us as _default_sleep_us

START_BIT = 0
BUS_REQ_BIT = 2
DONE_BIT = 0
BUS_GNT_BIT = 1

PROJECT_CLOCK_HZ = 66_000_000
DEFAULT_PROJECT = "tt_um_lahnb_sgdma"

# uio bit: 0 flash CS, 1 SIO0, 2 SIO1, 3 SCK, 4 SIO2, 5 SIO3, 6 RAM A CS, 7 RAM B CS.
# SDK: bits set to 1 are driven by the RP2.
OE_HIZ = 0
OE_SPI = (
    (1 << 0) | (1 << 1) | (1 << 3) | (1 << 6) | (1 << 7)
)
OE_QPI = 0xFF

START_HOLD_US = 1  # >> two 66 MHz clocks of the input synchronizer


class HostError(Exception):
    """Illegal START / BUS_REQ / OE sequencing, or a DONE timeout."""


def _as_int(port):
    if hasattr(port, "value") and not isinstance(port, (int, bool)):
        try:
            return int(port.value)
        except (TypeError, ValueError):
            pass
    return int(port)


def _set_port(obj, name, value):
    port = getattr(obj, name)
    if hasattr(port, "value") and not isinstance(port, (int, bool)):
        try:
            port.value = int(value) & 0xFF
            return
        except (TypeError, ValueError, AttributeError):
            pass
    setattr(obj, name, int(value) & 0xFF)


def _bit_get(port, bit):
    if hasattr(port, "__getitem__") and not isinstance(port, (int, bool)):
        try:
            return int(port[bit])
        except (TypeError, IndexError, KeyError):
            pass
    return (_as_int(port) >> bit) & 1


def _bit_set(obj, name, bit, value):
    port = getattr(obj, name)
    if hasattr(port, "__setitem__") and not isinstance(port, (int, bool)):
        try:
            port[bit] = 1 if value else 0
            return
        except (TypeError, IndexError, KeyError):
            pass
    cur = _as_int(port)
    if value:
        cur |= 1 << bit
    else:
        cur &= ~(1 << bit)
    _set_port(obj, name, cur)


class Host:
    """MCU-side stand-in for grant / START / kill on a DemoBoard-like *tt*."""

    def __init__(self, tt, sleep_us=None, poll_us=100):
        self.tt = tt
        self._sleep_us = sleep_us if sleep_us is not None else _default_sleep_us
        self._poll_us = poll_us
        self._awaiting_done_fall = False

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

    @property
    def ui_in(self):
        return _as_int(self.tt.ui_in)

    @property
    def uo_out(self):
        return _as_int(self.tt.uo_out)

    @property
    def oe(self):
        return _as_int(getattr(self.tt, "uio_oe_pico", 0))

    @property
    def done(self):
        return _bit_get(self.tt.uo_out, DONE_BIT) == 1

    @property
    def bus_gnt(self):
        return _bit_get(self.tt.uo_out, BUS_GNT_BIT) == 1

    @property
    def bus_req(self):
        return _bit_get(self.tt.ui_in, BUS_REQ_BIT) == 1

    @property
    def rst_n_low(self):
        held = getattr(self.tt, "_in_reset", None)
        if held is not None:
            return bool(held)
        return False

    def _drive_legal(self):
        return self.bus_gnt or self.rst_n_low

    def hiz(self):
        _set_port(self.tt, "uio_oe_pico", OE_HIZ)

    def enable_drive(self, oe=OE_QPI):
        if not self._drive_legal():
            raise HostError("MCU QSPI drive only while BUS_GNT=1 or rst_n=0")
        _set_port(self.tt, "uio_oe_pico", oe)

    def enable_project(self, name=None, clock_hz=PROJECT_CLOCK_HZ):
        """Mux-select this design (or an M7 bitstream), ASIC_RP_CONTROL, 66 MHz."""
        name = name or DEFAULT_PROJECT
        shuttle = getattr(self.tt, "shuttle", None)
        if shuttle is not None:
            design = getattr(shuttle, name, None)
            if design is None and hasattr(shuttle, "__getitem__"):
                try:
                    design = shuttle[name]
                except Exception:
                    design = None
            if design is not None and hasattr(design, "enable"):
                design.enable()
        clock = getattr(self.tt, "clock_project_PWM", None)
        if clock is not None:
            clock(clock_hz)
        _set_port(self.tt, "ui_in", 0)
        self.hiz()
        self._awaiting_done_fall = False

    def reset_asic(self, asserted=True):
        """Drive rst_n. While held (asserted=True), MCU QSPI drive is legal without grant."""
        reset = getattr(self.tt, "reset_project", None)
        if reset is None:
            raise HostError("tt.reset_project is not available")
        reset(bool(asserted))
        if asserted:
            self.hiz()
            self._awaiting_done_fall = False

    def kill_dma(self):
        """Assert rst_n; leave MCU OE Hi-Z. Re-enter QPI after deassert before START."""
        self.hiz()
        self.reset_asic(True)

    def _wait(self, pred, timeout_ms, what):
        start = self._now_ms()
        while True:
            if pred():
                return
            if timeout_ms is not None and self._elapsed_ms(start) >= timeout_ms:
                self.kill_dma()
                raise HostError("timeout waiting for %s" % what)
            self._sleep_us(self._poll_us)

    def request_bus(self, timeout_ms=1000, oe=OE_QPI):
        if self._awaiting_done_fall and self.done:
            raise HostError("BUS_REQ refused until DONE falls after START")
        _bit_set(self.tt, "ui_in", BUS_REQ_BIT, 1)
        self._wait(lambda: self.bus_gnt, timeout_ms, "BUS_GNT")
        if oe:
            self.enable_drive(oe)

    def release_bus(self, timeout_ms=1000):
        self.hiz()
        _bit_set(self.tt, "ui_in", BUS_REQ_BIT, 0)
        self._wait(lambda: not self.bus_gnt, timeout_ms, "BUS_GNT low")

    def pulse_start(self, hold_us=START_HOLD_US):
        """Require DONE=1 and BUS_REQ=0; hold START across the two-flop sync."""
        self.hiz()
        if self.bus_req:
            raise HostError("START refused while BUS_REQ is high")
        if self.bus_gnt:
            raise HostError("START refused while BUS_GNT is high")
        if not self.done:
            raise HostError("START requires DONE=1")
        _bit_set(self.tt, "ui_in", START_BIT, 1)
        self._sleep_us(hold_us)
        _bit_set(self.tt, "ui_in", START_BIT, 0)
        self._awaiting_done_fall = True

    def wait_busy(self, timeout_ms=1000):
        """Wait until DONE falls (START accepted)."""
        self._wait(lambda: not self.done, timeout_ms, "DONE low")
        self._awaiting_done_fall = False

    def wait_done(self, timeout_ms=1000):
        """Wait until DONE rises (chain finished / idle)."""
        self._wait(lambda: self.done, timeout_ms, "DONE high")
