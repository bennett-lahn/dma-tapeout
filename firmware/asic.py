"""DemoBoard host protocol: enable, clock, rst_n/kill, BUS_REQ/GNT, START, DONE.

Unused `ui_in` bits stay 0 (D34). MCU QSPI `uio_oe_pico` is Hi-Z unless
`BUS_GNT=1` or `rst_n=0`. After pulsing START, do not raise BUS_REQ until
the ASIC is idle again. DONE low is the START-accept ACK, but a short chain
can return to DONE=1 before firmware samples; that is treated as already idle,
not a missed ACK. Do not use a 1 us sleep as the capture or ACK mechanism:
GPIO write duration already covers the two-flop synchronizer.

Takes a `tt` object so REPL can pass `DemoBoard.get()` and tests can inject a
mock with integer `ui_in` / `uo_out` / `uio_oe_pico`.

`rst_n_low` uses firmware-recorded `reset_project` state, not a DemoBoard
private `_in_reset` attribute (the SDK has no such field).
"""

from .constants import (
    BUS_GNT_BIT,
    BUS_REQ_BIT,
    BUSY_SAMPLE_TRIES,
    DEFAULT_PROJECT,
    DONE_BIT,
    OE_HIZ,
    OE_QPI,
    OE_QPI_READ,
    OE_SPI,
    PROJECT_CLOCK_HZ,
    START_BIT,
    START_HOLD_US,
)
from .psram import sleep_us as _default_sleep_us

EXPECTED_MODE = "ASIC_RP_CONTROL"


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
        self._saw_busy = False
        self._rst_held = False

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
        """True while this Host has `reset_project(True)` held (DemoBoard rst_n=0)."""
        return bool(self._rst_held)

    def _drive_legal(self):
        return self.bus_gnt or self.rst_n_low

    def zero_ui_in(self):
        """Drive unused and used `ui_in` bits to 0 (D34)."""
        _set_port(self.tt, "ui_in", 0)

    def hiz(self):
        _set_port(self.tt, "uio_oe_pico", OE_HIZ)

    def enable_drive(self, oe=OE_QPI):
        if not self._drive_legal():
            raise HostError("MCU QSPI drive only while BUS_GNT=1 or rst_n=0")
        _set_port(self.tt, "uio_oe_pico", oe)

    def qpi_write_oe(self):
        """All SIO driven: QPI command, address, and write data."""
        self.enable_drive(OE_QPI)

    def qpi_read_oe(self):
        """Float SIO during QPI dummy/data; keep CS and SCK driven."""
        self.enable_drive(OE_QPI_READ)

    def spi_oe(self):
        """SPI pin directions: MOSI out, MISO/SD2/SD3 in."""
        self.enable_drive(OE_SPI)

    def enable_project(self, name=None, clock_hz=PROJECT_CLOCK_HZ):
        """ui_in=0, reset assert, mux, 66 MHz, reset deassert, sample DONE/GNT."""
        name = name or DEFAULT_PROJECT
        mode = getattr(self.tt, "mode", None)
        if mode is not None and str(mode) != EXPECTED_MODE:
            raise HostError("expected mode %s, got %s" % (EXPECTED_MODE, mode))
        self.zero_ui_in()
        self.reset_asic(True)
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
        self.hiz()
        self.reset_asic(False)
        if not self.done:
            raise HostError("DONE=1 expected after reset release")
        if self.bus_gnt:
            raise HostError("BUS_GNT=0 expected after reset release")
        self._awaiting_done_fall = False
        self._saw_busy = False

    def reset_asic(self, asserted=True):
        """Drive rst_n. While held (asserted=True), MCU QSPI drive is legal without grant."""
        reset = getattr(self.tt, "reset_project", None)
        if reset is None:
            raise HostError("tt.reset_project is not available")
        reset(bool(asserted))
        self._rst_held = bool(asserted)
        if asserted:
            self.hiz()
            self._awaiting_done_fall = False
            self._saw_busy = False

    def kill_dma(self):
        """Assert rst_n, clear ui_in (BUS_REQ/START=0), leave MCU OE Hi-Z."""
        self.hiz()
        self.zero_ui_in()
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
            raise HostError("BUS_REQ refused until idle after START")
        _bit_set(self.tt, "ui_in", BUS_REQ_BIT, 1)
        self._wait(lambda: self.bus_gnt, timeout_ms, "BUS_GNT")
        if oe:
            self.enable_drive(oe)

    def release_bus(self, timeout_ms=1000):
        self.hiz()
        _bit_set(self.tt, "ui_in", BUS_REQ_BIT, 0)
        self._wait(lambda: not self.bus_gnt, timeout_ms, "BUS_GNT low")

    def _sample_busy(self, tries=BUSY_SAMPLE_TRIES):
        """Return True if DONE is observed low. Tight polls; not a timed wait."""
        for _ in range(tries):
            if not self.done:
                return True
        return False

    def pulse_start(self, hold_us=START_HOLD_US):
        """Require DONE=1 and BUS_REQ=0, then pulse START.

        *hold_us* is optional padding only. Two GPIO writes already hold the
        pad longer than the two-flop synchronizer; logic must not depend on
        the sleep being 1 us or any other short delay.
        """
        self.hiz()
        if self.bus_req:
            raise HostError("START refused while BUS_REQ is high")
        if self.bus_gnt:
            raise HostError("START refused while BUS_GNT is high")
        if not self.done:
            raise HostError("START requires DONE=1")
        _bit_set(self.tt, "ui_in", START_BIT, 1)
        if hold_us:
            self._sleep_us(hold_us)
        saw = self._sample_busy()
        _bit_set(self.tt, "ui_in", START_BIT, 0)
        if not saw:
            saw = self._sample_busy()
        self._saw_busy = saw
        self._awaiting_done_fall = not saw

    def wait_busy(self, tries=BUSY_SAMPLE_TRIES):
        """Return True if DONE is observed low (START accepted, chain in flight).

        If DONE stays high, the chain may already have finished (QUIT-only is
        about 1.2 us). That is not a timeout: return False and treat idle.
        Does not call kill_dma.
        """
        saw = self._saw_busy or self._sample_busy(tries)
        if saw:
            self._saw_busy = True
            self._awaiting_done_fall = False
            return True
        self._awaiting_done_fall = False
        self._saw_busy = False
        return False

    def wait_done(self, timeout_ms=1000):
        """Wait until DONE rises (chain finished / idle). Sticky; timeout kills."""
        self._wait(lambda: self.done, timeout_ms, "DONE high")
        self._awaiting_done_fall = False
        self._saw_busy = False

    def wait_idle_after_start(self, timeout_ms=5000):
        """After pulse_start, block until the ASIC is idle.

        If DONE is observed low, wait for it to rise (runaway still kill_dma).
        If DONE is never observed low, treat as fast completion and return.
        Dump/compare is the backstop if START was ignored.
        """
        if self.wait_busy():
            self.wait_done(timeout_ms)
            return
        self._awaiting_done_fall = False
        self._saw_busy = False
