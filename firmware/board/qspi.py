"""RP2 PIO SPI / QPI transports for the QSPI PMOD, plus timing helpers.

Adapts the ETR `PIOSPI` / `spi_cpha0` and four-wire QPI PIO from the Tiny
Tapeout QSPI PMOD guide catalog. Attribution: Rohan Verma
(github.com/rohanverm94) ETR appendix; see
`docs/datasheets/md/Using_QSPI_TinyTapeout.md`. SoftSPI / `machine.SPI` are
not the primary master.

Device protocol (opcodes, tCEM chunk planning, frames) lives in
`firmware/psram.py`; this module only moves bytes.

`rp2` and `machine` are imported behind guards so CPython pytest can load the
module and inject a mock transport. The PIO programs and the `PIOSPI` /
`PioTransport` classes exist only when `rp2` is present.

Pad ownership: a GPIO is function-selected to exactly one peripheral. Every
`machine.Pin(n, Pin.IN/OUT)` call selects SIO, which disconnects the PIO from
that pad; `rp2.StateMachine` selects PIO for the pins named by `out_init`,
`set_init`, and `sideset_init`. `arm()` therefore hands GPIO26..30 to the PIO
once and this module never re-inits those five pins mid-transaction. While the
PIO holds them, `uio_oe_pico` (a plain SIO `GPIO_OE` write) cannot change their
direction: `set(pindirs, ...)` is the only real output enable, and
`release_pins()` is the only real Hi-Z.
"""

try:
    import rp2
except ImportError:
    rp2 = None

try:
    from machine import Pin
except ImportError:
    Pin = None

try:
    from time import sleep_us as _mp_sleep_us
except ImportError:
    _mp_sleep_us = None

from ..constants import (
    QPI_READ_LAG_NIBBLES,
    QPI_READ_LAG_WORDS,
    QPI_READ_RX_FIFO_WORDS,
    QPI_WRITE_TX_FIFO_WORDS,
    SCK_HZ_DEFAULT,
    SCK_PER_BYTE_QPI,
)
from .pins import (
    PIN_FLASH_CS,
    PIN_MISO,
    PIN_MOSI,
    PIN_RAM_A_CS,
    PIN_RAM_B_CS,
    PIN_SCK,
    PIN_SD2,
    PIN_SD3,
)

# D16: APS6404L QPI RX samples on rising SCK (device launches on falling SCK /
# tACLK, meaning read data valid after falling SCK). side(0)=SCK low,
# side(1)=SCK high. The rp2 program body must match this order: nop on SCK low,
# in_ on SCK high.
QPI_READ_PIO_INTENT = (
    ("nop", 0),
    ("in_", 1, 5),
)

# ETR GPIO26..30 is the only contiguous range containing all QPI SIO pins:
# SIO0, SIO1, SCK, SIO2, SIO3. PIO cannot select a non-contiguous pin list.
# Each four-bit QPI nibble is consequently represented by a five-bit PIO
# symbol. Bit 2 is SCK: side-set owns it on TX and RX packing discards it.
QPI_SIO_BITS = 4
QPI_PIO_BITS = QPI_SIO_BITS + 1
QPI_PIO_BITS_PER_BYTE = 2 * QPI_PIO_BITS
QPI_PIO_TX_SHIFT = 32 - QPI_PIO_BITS_PER_BYTE

# PIO `set(pindirs, N)` writes the same GPIO26..30 window as out / in_:
# bit0 SIO0, bit1 SIO1, bit2 SCK, bit3 SIO2, bit4 SIO3. A 1 means the PIO
# drives that pad. These masks, not the `uio_oe_pico` OE bits, are what the
# five PIO-owned pads actually follow.
PINDIRS_SPI = 0b00101  # MOSI + SCK driven; MISO / SD2 / SD3 float (pulled up)
PINDIRS_QPI_TX = 0b11111  # SIO0..3 + SCK driven: command, address, write data
PINDIRS_QPI_RX = 0b00100  # SCK only; SIO floats so the PSRAM drives read data

# After Exit Quad, SPI HOLD#/WP# (SIO3/SIO2) must not stay driven as outputs.
SPI_PIN_MODES = {
    "MOSI": "OUT",
    "MISO": "IN",
    "SD2": "IN_PULLUP",
    "SD3": "IN_PULLUP",
}

# D26 bus keeper: PioTransport.__init__ must not claim Pin.OUT on the shared
# uio pins; arm() runs after BUS_GNT=1 or rst_n=0.
PIO_TRANSPORT_CLAIMS_PINS_IN_INIT = False

# Every pad arm() claims, and therefore every pad release_pins() must give
# back. All eight uio bits: the five PIO-owned lines plus the three SIO chip
# selects. The OE register is not a fallback for the CS pads; see release_pins.
RELEASED_PINS = (
    PIN_FLASH_CS,
    PIN_MOSI,
    PIN_MISO,
    PIN_SCK,
    PIN_SD2,
    PIN_SD3,
    PIN_RAM_A_CS,
    PIN_RAM_B_CS,
)


class QspiError(Exception):
    """Transport contract violation: no rp2, or a state machine misuse."""


def require_write_fits_fifo(frame):
    """Refuse a write-SM prefill that would hang on idle `put()`.

    `qpi_write_cpha0` joins TX to 8 words and `qpi_write` / the `0xEB` header
    path fill that FIFO while the SM is idle. One extra word blocks forever
    instead of raising. Callers raise with this so the hang cannot happen.
    """
    payload = bytes(frame)
    if len(payload) > QPI_WRITE_TX_FIFO_WORDS:
        raise QspiError(
            "QPI write frame %d bytes exceeds the joined TX FIFO (%d); "
            "idle-SM put() would block forever"
            % (len(payload), QPI_WRITE_TX_FIFO_WORDS)
        )
    return payload


def require_read_fits_fifo(dummy_cycles, n):
    """Refuse an `0xEB` frame that would stall the read SM mid-burst.

    `qpi_read_cpha0` pushes one word per QPI byte and cannot tell a dummy
    cycle from data, and the realign spare byte is clocked too, so a frame
    costs dummy + `n` + `QPI_READ_LAG_WORDS` words. Past the joined RX FIFO
    the SM blocks on `push` with SCK parked by side-set; the capture stops
    tracking the burst and silently drops and repeats nibbles rather than
    raising. Returns the word count so callers can reuse it.
    """
    n = int(n)
    dummy_words = int(dummy_cycles) // SCK_PER_BYTE_QPI
    spare = QPI_READ_LAG_WORDS if n else 0
    words = dummy_words + n + spare
    if words > QPI_READ_RX_FIFO_WORDS:
        raise QspiError(
            "QPI read of %d bytes needs %d FIFO words (%d dummy + %d data + %d "
            "realign) but the joined RX FIFO holds %d; the SM would stall "
            "mid-burst and drop nibbles"
            % (n, words, dummy_words, n, spare, QPI_READ_RX_FIFO_WORDS)
        )
    return words


def realign_qpi_nibbles(raw, n, lag=QPI_READ_LAG_NIBBLES):
    """Drop the leading *lag* captured nibbles and repack *n* wire bytes.

    The PIO capture point sits `QPI_READ_LAG_NIBBLES` SCK behind the PSRAM's
    data launch, so a frame arrives as `[turnaround, d0, d1, ...]` and every
    byte straddles two captured words. The SM packs two nibbles per word and
    cannot be shifted by a single SCK, so `qpi_read` clocks a spare byte and
    the stream is re-paired here.
    """
    raw = bytes(raw)
    n = int(n)
    if lag == 0:
        return raw[:n]
    nibbles = []
    for value in raw:
        nibbles.append((value >> 4) & 0x0F)
        nibbles.append(value & 0x0F)
    if len(nibbles) < lag + 2 * n:
        raise QspiError(
            "QPI read captured %d nibbles; realigning %d bytes at a lag of %d "
            "needs %d" % (len(nibbles), n, lag, lag + 2 * n)
        )
    out = bytearray(n)
    for i in range(n):
        out[i] = (nibbles[lag + 2 * i] << 4) | nibbles[lag + 2 * i + 1]
    return bytes(out)


def pack_qpi_byte(value):
    """Expand one wire-order QPI byte to two five-bit PIO symbols.

    SIO3 is each nibble's MSB. GPIO28/SCK occupies bit 2 in the contiguous
    PIO range and is a zero placeholder: the write SM side-set output wins.
    """
    value = int(value) & 0xFF
    high = value >> 4
    low = value & 0x0F

    def expand(nibble):
        return (nibble & 0x03) | ((nibble & 0x0C) << 1)

    return (expand(high) << QPI_PIO_BITS) | expand(low)


def unpack_qpi_word(value):
    """Recover a wire-order QPI byte, discarding each captured SCK bit."""
    value = int(value) & ((1 << QPI_PIO_BITS_PER_BYTE) - 1)
    high = value >> QPI_PIO_BITS
    low = value & ((1 << QPI_PIO_BITS) - 1)

    def compact(symbol):
        return (symbol & 0x03) | ((symbol >> 1) & 0x0C)

    return (compact(high) << 4) | compact(low)


def sleep_us(us):
    if _mp_sleep_us is not None:
        _mp_sleep_us(int(us))
        return
    import time

    time.sleep(us / 1_000_000.0)


def wait_at_least_us(us, sleep=None):
    """Block until at least *us* have elapsed. Longer is OK.

    Uses wall/tick time so a short or skipped sleep cannot under-wait *us*.
    *sleep* is only a hint to yield; elapsed time is the contract.
    """
    import time

    us = int(us)
    if us <= 0:
        return
    nap = sleep if sleep is not None else sleep_us
    if hasattr(time, "ticks_us") and hasattr(time, "ticks_diff"):
        start = time.ticks_us()
        nap(us)
        while time.ticks_diff(time.ticks_us(), start) < us:
            nap(1)
        return
    if hasattr(time, "ticks_ms") and hasattr(time, "ticks_diff"):
        start = time.ticks_ms()
        need_ms = (us + 999) // 1000
        nap(us)
        while time.ticks_diff(time.ticks_ms(), start) < need_ms:
            nap(1)
        return
    deadline = time.time() + (us / 1_000_000.0)
    nap(us)
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(remaining)


def drain_sm(sm):
    """Wait until a PIO state machine has finished shifting, then it may stop."""
    wait_idle = getattr(sm, "wait_idle", None)
    if wait_idle is not None:
        wait_idle()
        return
    drained = getattr(sm, "drained", None)
    if drained is False:
        raise QspiError("state machine deactivated before drain")


def park_and_switch_sm(old_sm, new_sm, park_sck=None):
    """Drain *old_sm*, park SCK low, then activate *new_sm*. No overlapping active(1)."""
    if old_sm is not None:
        drain_sm(old_sm)
        old_sm.active(0)
    if park_sck is not None:
        park_sck()
    if new_sm is not None:
        new_sm.active(1)


if rp2 is not None:

    @rp2.asm_pio(
        out_shiftdir=0,
        autopull=True,
        pull_thresh=8,
        autopush=True,
        push_thresh=8,
        sideset_init=(rp2.PIO.OUT_LOW,),
        out_init=rp2.PIO.OUT_LOW,
    )
    def spi_cpha0():
        # Adapted from Rohan Verma / Tiny Tapeout QSPI PMOD ETR PIOSPI catalog.
        out(pins, 1).side(0x0)
        in_(pins, 1).side(0x1)

    @rp2.asm_pio(
        out_shiftdir=0,
        autopull=True,
        pull_thresh=QPI_PIO_BITS_PER_BYTE,
        sideset_init=(rp2.PIO.OUT_LOW,),
        out_init=(rp2.PIO.OUT_LOW,) * QPI_PIO_BITS,
        # set_init sizes SET_COUNT to the same five pins, which is what lets
        # _set_pindirs retarget GPIO26..30 without a machine.Pin re-init.
        set_init=(rp2.PIO.OUT_LOW,) * QPI_PIO_BITS,
        fifo_join=rp2.PIO.JOIN_TX,
    )
    def qpi_write_cpha0():
        # Four-wire QPI write. GPIO26..30 spans SIO0/SIO1/SCK/SIO2/SIO3;
        # side-set owns the SCK bit and pack_qpi_byte supplies its placeholder.
        # One byte = two five-bit PIO symbols / 2 SCK.
        # JOIN_TX makes TX 8 words so a 5-byte 0x02+addr+1 payload frame can be
        # prefilled while the SM is idle. Do not raise MCU_QPI_PAYLOAD_MAX past
        # (QPI_WRITE_TX_FIFO_WORDS - 4) without streaming after active(1); extra
        # idle-SM put()s hang, and a longer CE# pulse still risks tCEM.
        # asm_pio evaluates this body without module globals; keep the width a
        # literal (must match QPI_PIO_BITS).
        out(pins, 5).side(0)
        nop().side(1)

    @rp2.asm_pio(
        in_shiftdir=0,
        autopush=True,
        push_thresh=QPI_PIO_BITS_PER_BYTE,
        sideset_init=(rp2.PIO.OUT_LOW,),
        set_init=(rp2.PIO.IN_LOW,) * QPI_PIO_BITS,
        fifo_join=rp2.PIO.JOIN_RX,
    )
    def qpi_read_cpha0():
        # Must match QPI_READ_PIO_INTENT: nop on SCK low, in_ on rising SCK (D16).
        # Width is a literal: asm_pio does not see QPI_PIO_BITS (must stay 5).
        # JOIN_RX makes RX 8 words. The dummy cycles are pushed like data, so a
        # frame costs QPI_READ_DUMMY_WORDS + n words; past that the SM stalls
        # mid-burst with SCK parked high and the capture drops and repeats
        # nibbles. qpi_read refuses such a frame. Do not JOIN_RX the write SM.
        nop().side(0)
        in_(pins, 5).side(1)

    class PIOSPI:
        """1-bit PIO SPI master (guide `PIOSPI`). Not used as SoftSPI / machine.SPI."""

        def __init__(self, sm_id, pin_mosi, pin_miso, pin_sck, freq=SCK_HZ_DEFAULT):
            self._sm = rp2.StateMachine(
                sm_id,
                spi_cpha0,
                freq=2 * freq,
                sideset_base=Pin(pin_sck) if not isinstance(pin_sck, Pin) else pin_sck,
                out_base=Pin(pin_mosi) if not isinstance(pin_mosi, Pin) else pin_mosi,
                in_base=Pin(pin_miso) if not isinstance(pin_miso, Pin) else pin_miso,
            )
            self._sm.active(1)

        def write(self, wdata):
            first = True
            for b in wdata:
                self._sm.put(b, 24)
                if not first:
                    self._sm.get()
                else:
                    first = False
            if wdata:
                self._sm.get()

        def read(self, n):
            return self.write_read_blocking([0] * n)

        def write_read_blocking(self, wdata):
            rdata = bytearray(len(wdata))
            i = -1
            for b in wdata:
                self._sm.put(b, 24)
                if i >= 0:
                    rdata[i] = self._sm.get()
                i += 1
            if i >= 0:
                rdata[i] = self._sm.get()
            return rdata

    class PioTransport:
        """Board SPI + QPI master. Call only while BUS_GNT=1 or rst_n=0.

        Flash CS is driven high and never selected. RAM A is device 0, RAM B is
        device 1. Default SCK is 20 MHz (the tCEM planner in psram.py refuses a
        too-slow SCK). Pin.OUT is claimed in arm(), not __init__ (D26: no drive
        before grant).
        """

        def __init__(self, sck_hz=SCK_HZ_DEFAULT, sleep=sleep_us):
            if rp2 is None or Pin is None:
                raise QspiError("PioTransport requires rp2 and machine.Pin")
            if PIO_TRANSPORT_CLAIMS_PINS_IN_INIT:
                raise QspiError("PioTransport must not claim pins in __init__")
            self.sck_hz = sck_hz
            self._sleep = sleep
            self._armed = False
            self.flash_cs = None
            self.ram_cs = None
            self.spi = None
            self._qpi_wr = None
            self._qpi_rd = None
            self.pin_modes = None
            self.pindirs = None

        def arm(self):
            """Claim CS and the GPIO26..30 PIO window. Idempotent per grant.

            Called at the top of every transaction, so it also re-claims the
            five PIO pads after a `release_pins()` at the end of the last
            grant. Pin.OUT is taken here, not in __init__ (D26: no drive
            before BUS_GNT=1 or rst_n=0).
            """
            if self._armed:
                return
            self.flash_cs = Pin(PIN_FLASH_CS, Pin.OUT)
            self.ram_cs = (
                Pin(PIN_RAM_A_CS, Pin.OUT),
                Pin(PIN_RAM_B_CS, Pin.OUT),
            )
            self.flash_cs.on()
            self.ram_cs[0].on()
            self.ram_cs[1].on()
            # Pulls are a pad property that survives a function-select change,
            # so arm SD2/SD3 (SPI WP#/HOLD#) before the PIO takes the pads.
            # They then idle high whenever pindirs leaves them as inputs.
            Pin(PIN_SD2, Pin.IN, Pin.PULL_UP)
            Pin(PIN_SD3, Pin.IN, Pin.PULL_UP)
            # Constructing the SMs function-selects GPIO26..30 to PIO0. From
            # here until release_pins() nothing may machine.Pin those five.
            self.spi = PIOSPI(1, PIN_MOSI, PIN_MISO, PIN_SCK, freq=self.sck_hz)
            self._qpi_wr = rp2.StateMachine(
                2,
                qpi_write_cpha0,
                freq=2 * self.sck_hz,
                sideset_base=Pin(PIN_SCK),
                out_base=Pin(PIN_MOSI),
                set_base=Pin(PIN_MOSI),
            )
            self._qpi_rd = rp2.StateMachine(
                3,
                qpi_read_cpha0,
                freq=2 * self.sck_hz,
                sideset_base=Pin(PIN_SCK),
                in_base=Pin(PIN_MOSI),
                set_base=Pin(PIN_MOSI),
            )
            self._qpi_wr.active(0)
            self._qpi_rd.active(0)
            self._armed = True
            self.restore_spi_pins()

        def _set_pindirs(self, dirs):
            """Point the GPIO26..30 output enables at one transfer phase.

            `uio_oe_pico` cannot do this: those pads are function-selected to
            the PIO, so they follow PIO pindirs instead of the SIO OE
            register. The exec'd word carries side-set 0, so it also parks SCK
            low. Safe only while the write SM is stopped.
            """
            self._qpi_wr.exec("set(pindirs, %d)" % dirs)
            self.pindirs = dirs

        def restore_spi_pins(self):
            """MOSI out, MISO in, SD2/SD3 pull-up in (SPI HOLD#/WP# safe)."""
            self._set_pindirs(PINDIRS_SPI)
            self.pin_modes = dict(SPI_PIN_MODES)

        def release_pins(self):
            """Hand every pad `arm()` claimed back to SIO inputs.

            The real Hi-Z (D26 bus keeper). An OE write alone would leave SCK
            and SIO driven after BUS_GNT falls, which fights the ASIC for the
            whole transfer, because those five pads are function-selected to
            the PIO and do not follow `uio_oe_pico` at all.

            The three CS pads are released here too, rather than left to the
            caller's OE write. `arm()` claims them with `machine.Pin`, so this
            is the matching release, and on the ETR the OE write cannot undo
            it: the frozen ttboard build preserves GPIO25 (uio[0], flash CS)
            when clearing `uio_oe_pico`, so flash CS stayed driven through
            every `hiz()` and tripped the D26 idle check.

            Pad restoration runs whether or not this transport armed them, so
            a session that inherits driven pads from a killed run still
            reaches a true Hi-Z.
            """
            if self._armed:
                for sm in (self.spi._sm, self._qpi_wr, self._qpi_rd):
                    if sm is not None:
                        sm.active(0)
            for pin in RELEASED_PINS:
                Pin(pin, Pin.IN)
            self.spi = None
            self._qpi_wr = None
            self._qpi_rd = None
            self.flash_cs = None
            self.ram_cs = None
            self.pin_modes = None
            self.pindirs = None
            self._armed = False

        def _park_sck(self):
            """Drive SCK low. An exec'd word side-sets 0 onto the SCK pin."""
            if self._qpi_wr is not None:
                self._qpi_wr.exec("nop()")

        def _select(self, cs):
            self.flash_cs.on()
            self.ram_cs[0].on()
            self.ram_cs[1].on()
            self.ram_cs[cs].off()

        def _deselect(self):
            self.ram_cs[0].on()
            self.ram_cs[1].on()
            self.flash_cs.on()

        def sleep_us(self, us):
            self._sleep(us)

        def spi_write(self, cs, data):
            self.arm()
            park_and_switch_sm(self._qpi_wr, None, park_sck=self._park_sck)
            park_and_switch_sm(self._qpi_rd, None, park_sck=self._park_sck)
            self.restore_spi_pins()
            self.spi._sm.active(1)
            self._select(cs)
            self.spi.write(bytes(data))
            drain_sm(self.spi._sm)
            self._deselect()

        def _drain_bytes(self, sm, n_bytes):
            wait_idle = getattr(sm, "wait_idle", None)
            if wait_idle is not None:
                wait_idle()
                return
            n_sck = n_bytes * SCK_PER_BYTE_QPI
            self._sleep(max(1, int((n_sck / float(self.sck_hz)) * 1e6) + 1))

        def _prime_rx(self):
            """Reset the read SM so a burst starts bit- and word-aligned.

            The read SM free-runs until its RX FIFO fills, so a previous
            burst leaves stale words queued and a partly filled ISR.
            `restart` clears the program counter and the input shift counter;
            the FIFO has to be drained by hand.
            """
            self._qpi_rd.active(0)
            self._qpi_rd.restart()
            while self._qpi_rd.rx_fifo():
                self._qpi_rd.get()

        def qpi_write(self, cs, data):
            self.arm()
            payload = require_write_fits_fifo(data)
            park_and_switch_sm(self.spi._sm, None, park_sck=self._park_sck)
            park_and_switch_sm(self._qpi_rd, None, park_sck=self._park_sck)
            self._qpi_wr.active(0)
            self._qpi_wr.restart()
            self._set_pindirs(PINDIRS_QPI_TX)
            for b in payload:
                self._qpi_wr.put(pack_qpi_byte(b), QPI_PIO_TX_SHIFT)
            self._select(cs)
            self._qpi_wr.active(1)
            self._drain_bytes(self._qpi_wr, len(payload))
            self._deselect()
            self._qpi_wr.active(0)
            self._park_sck()

        def qpi_read(self, cs, header, dummy_cycles, n):
            self.arm()
            header = bytes(header)
            n = int(n)
            # The SM cannot stall mid-frame without losing bit alignment, so
            # the whole burst has to fit the joined RX FIFO.
            require_read_fits_fifo(dummy_cycles, n)
            park_and_switch_sm(self.spi._sm, None, park_sck=self._park_sck)
            park_and_switch_sm(self._qpi_rd, None, park_sck=self._park_sck)
            self._qpi_wr.active(0)
            self._qpi_wr.restart()
            self._set_pindirs(PINDIRS_QPI_TX)
            for b in header:
                self._qpi_wr.put(pack_qpi_byte(b), QPI_PIO_TX_SHIFT)
            self._select(cs)
            self._qpi_wr.active(1)
            self._drain_bytes(self._qpi_wr, len(header))
            park_and_switch_sm(self._qpi_wr, None, park_sck=self._park_sck)
            # Mid-frame turnaround, still inside one CE# pulse: float SIO
            # before the dummy cycles so the PSRAM, not the PIO, drives data.
            self._set_pindirs(PINDIRS_QPI_RX)
            self._prime_rx()
            dummy_bytes = dummy_cycles // SCK_PER_BYTE_QPI
            self._qpi_rd.active(1)
            for _ in range(dummy_bytes):
                self._qpi_rd.get()
            # One spare byte past the payload: the capture runs a nibble late,
            # so the last byte's low nibble arrives in the following word.
            captured = n + (QPI_READ_LAG_WORDS if n else 0)
            raw = bytearray(captured)
            for i in range(captured):
                raw[i] = unpack_qpi_word(self._qpi_rd.get())
            # The read SM free-runs once its gets are satisfied. Stop it
            # before CE# rises so it cannot clock past the end of the frame.
            self._qpi_rd.active(0)
            self._deselect()
            self._park_sck()
            return realign_qpi_nibbles(raw, n)


def make_board_transport(sck_hz=SCK_HZ_DEFAULT):
    """Construct the ETR PIO master. Raises on CPython (no rp2)."""
    if rp2 is None:
        raise QspiError("rp2 is not available; inject a mock transport for tests")
    return PioTransport(sck_hz=sck_hz)
