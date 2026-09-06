"""TinyDMA host protocol on top of the demoboard layer: grant, START, DONE, kill.

Unused `ui_in` bits stay 0 (D34). MCU QSPI `uio_oe_pico` is Hi-Z unless
BUS_GNT=1 or rst_n=0 (D26 bus keeper: while rst_n=1 and BUS_GNT=0 the ASIC owns
the QSPI pins). After pulsing START, do not raise BUS_REQ until the ASIC is idle
again. DONE low is the START-accept ACK, but a short chain can return to DONE=1
before firmware samples; that is treated as already idle, not a missed ACK. Do
not use a 1 us sleep as the capture or ACK mechanism: the GPIO write duration
already covers the two-flop synchronizer.

Board access (ports, mux, clock, rst_n) is delegated to `board.Board`.
"""

from .board.pins import (
    BUS_GNT_BIT,
    BUS_REQ_BIT,
    DONE_BIT,
    OE_QPI,
    OE_QPI_READ,
    OE_SPI,
    PROJECT_CLOCK_HZ,
    START_BIT,
)
from .constants import BUSY_SAMPLE_TRIES, START_HOLD_US


class DmaError(Exception):
    """Illegal START / BUS_REQ / OE sequencing, or a DONE timeout."""


class DmaController:
    """MCU-side stand-in for grant / START / kill against the TinyDMA host port."""

    def __init__(self, board):
        self.board = board
        self._awaiting_done_fall = False
        self._saw_busy = False

    # --- status ---

    @property
    def done(self):
        return self.board.read_uo_out_bit(DONE_BIT) == 1

    @property
    def bus_gnt(self):
        return self.board.read_uo_out_bit(BUS_GNT_BIT) == 1

    @property
    def bus_req(self):
        return self.board.read_ui_in_bit(BUS_REQ_BIT) == 1

    @property
    def oe(self):
        return self.board.read_uio_oe()

    @property
    def rst_n_low(self):
        return self.board.rst_n_low

    # --- MCU QSPI direction (D26) ---

    def _drive_legal(self):
        return self.bus_gnt or self.board.rst_n_low

    def enable_drive(self, oe=OE_QPI):
        if not self._drive_legal():
            raise DmaError("MCU QSPI drive only while BUS_GNT=1 or rst_n=0")
        self.board.write_uio_oe(oe)

    def qpi_write_oe(self):
        """All SIO driven: QPI command, address, and write data."""
        self.enable_drive(OE_QPI)

    def qpi_read_oe(self):
        """Float SIO during QPI dummy/data; keep CS and SCK driven."""
        self.enable_drive(OE_QPI_READ)

    def spi_oe(self):
        """SPI pin directions: MOSI out, MISO/SD2/SD3 in."""
        self.enable_drive(OE_SPI)

    def hiz(self):
        self.board.hiz()

    # --- bring-up / reset ---

    def enable_design(self, name, clock_hz=PROJECT_CLOCK_HZ):
        """Enable the design, then sample the idle host port (DONE=1, BUS_GNT=0)."""
        self.board.enable_design(name, clock_hz=clock_hz)
        if not self.done:
            raise DmaError("DONE=1 expected after reset release")
        if self.bus_gnt:
            raise DmaError("BUS_GNT=0 expected after reset release")
        self._awaiting_done_fall = False
        self._saw_busy = False

    def reset(self, asserted=True):
        """Drive rst_n and drop any in-flight START bookkeeping."""
        self.board.reset_design(asserted)
        if asserted:
            self._awaiting_done_fall = False
            self._saw_busy = False

    def kill_dma(self):
        """Hi-Z the MCU OE, clear ui_in (BUS_REQ/START=0), then assert rst_n."""
        self.hiz()
        self.board.zero_ui_in()
        self.reset(True)

    def _wait(self, pred, timeout_ms, what):
        if not self.board.poll_until(pred, timeout_ms):
            self.kill_dma()
            raise DmaError("timeout waiting for %s" % what)

    # --- bus grant ---

    def request_bus(self, timeout_ms=1000, oe=OE_QPI):
        if self._awaiting_done_fall and self.done:
            raise DmaError("BUS_REQ refused until idle after START")
        self.board.write_ui_in_bit(BUS_REQ_BIT, 1)
        self._wait(lambda: self.bus_gnt, timeout_ms, "BUS_GNT")
        if oe:
            self.enable_drive(oe)

    def release_bus(self, timeout_ms=1000):
        self.hiz()
        self.board.write_ui_in_bit(BUS_REQ_BIT, 0)
        self._wait(lambda: not self.bus_gnt, timeout_ms, "BUS_GNT low")

    # --- START / DONE ---

    def _sample_busy(self, tries=BUSY_SAMPLE_TRIES):
        """Return True if DONE is observed low. Tight polls; not a timed wait."""
        for _ in range(tries):
            if not self.done:
                return True
        return False

    def pulse_start(self, hold_us=START_HOLD_US):
        """Require DONE=1, BUS_REQ=0 and BUS_GNT=0, then pulse START.

        *hold_us* is optional padding only. Two GPIO writes already hold the pad
        longer than the two-flop synchronizer; logic must not depend on the
        sleep being 1 us or any other short delay.
        """
        self.hiz()
        if self.bus_req:
            raise DmaError("START refused while BUS_REQ is high")
        if self.bus_gnt:
            raise DmaError("START refused while BUS_GNT is high")
        if not self.done:
            raise DmaError("START requires DONE=1")
        self.board.write_ui_in_bit(START_BIT, 1)
        if hold_us:
            self.board.sleep_us(hold_us)
        saw = self._sample_busy()
        self.board.write_ui_in_bit(START_BIT, 0)
        if not saw:
            saw = self._sample_busy()
        self._saw_busy = saw
        self._awaiting_done_fall = not saw

    def wait_busy(self, tries=BUSY_SAMPLE_TRIES):
        """Return True if DONE was observed low (START accepted, chain in flight).

        If DONE stays high, the chain may already have finished (QUIT-only is
        about 1.2 us). That is not a timeout: return False and treat as idle.
        Does not call kill_dma.
        """
        saw = self._saw_busy or self._sample_busy(tries)
        self._awaiting_done_fall = False
        self._saw_busy = bool(saw)
        return bool(saw)

    def wait_done(self, timeout_ms=1000):
        """Wait until DONE rises (chain finished / idle). Sticky; timeout kills."""
        self._wait(lambda: self.done, timeout_ms, "DONE high")
        self._awaiting_done_fall = False
        self._saw_busy = False

    def wait_idle_after_start(self, timeout_ms=5000):
        """After pulse_start, block until the ASIC is idle.

        If DONE is observed low, wait for it to rise (a runaway still hits
        kill_dma). If DONE is never observed low, treat it as fast completion
        and return; dump/compare is the backstop if START was ignored.
        """
        if self.wait_busy():
            self.wait_done(timeout_ms)
            return
        self._awaiting_done_fall = False
        self._saw_busy = False
