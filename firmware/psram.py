"""APS6404L bring-up and MCU QPI install/dump.

SPI reset (`0x66` then `0x99`) and Enter Quad (`0x35`) run in 1-bit SPI.
Install/dump after enter use QPI write `0x02` and QPI read `0xEB` (6 dummy
cycles), chunked so each CE# low pulse stays under `tCEM` (max CE# low for
PSRAM refresh; default 4 us extended grade, 25% margin). Exit Quad `0xF5` is
a QPI opcode (2 SCK). Flash CS stays high; this module does not program flash
Quad Enable.

Board transport adapts ETR `PIOSPI` / `spi_cpha0` and 4-bit QPI PIO from the
Tiny Tapeout QSPI PMOD guide catalog. Attribution: Rohan Verma
(github.com/rohanverm94) ETR appendix; see
`docs/datasheets/md/Using_QSPI_TinyTapeout.md`. SoftSPI / `machine.SPI` are
not the primary master.

`rp2` is imported behind a guard so CPython pytest can load this module and
inject a mock transport. PIO classes exist only when `rp2` is present.
"""

import math

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

from .constants import (
    CMD_ENTER_QPI,
    CMD_EXIT_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
    CMD_RESET_ENABLE,
    CS_PSRAM0,
    CS_PSRAM1,
    EB_OVERHEAD_SCK,
    MCU_QPI_PAYLOAD_MAX,
    PIN_FLASH_CS,
    PIN_MISO,
    PIN_MOSI,
    PIN_RAM_A_CS,
    PIN_RAM_B_CS,
    PIN_SCK,
    PIN_SD2,
    PIN_SD3,
    QPI_DUMMY_CYCLES,
    SCK_HZ_DEFAULT,
    SCK_PER_BYTE_QPI,
    TCEM_MARGIN_DEFAULT,
    TCEM_US_DEFAULT,
    TPU_US,
    WR_OVERHEAD_SCK,
)

# D16: APS6404L QPI RX samples on rising SCK (device launches on falling SCK /
# tACLK, read data valid after falling SCK). side(0)=SCK low, side(1)=SCK high.
# The rp2 program body must match this order: nop on SCK low, in_ on SCK high.
QPI_READ_PIO_INTENT = (
    ("nop", 0),
    ("in_", 1, 4),
)

# After Exit Quad, SPI HOLD#/WP# (SIO3/SIO2) must not stay driven as outputs.
SPI_PIN_MODES = {
    "MOSI": "OUT",
    "MISO": "IN",
    "SD2": "IN_PULLUP",
    "SD3": "IN_PULLUP",
}

# PioTransport.__init__ must not claim Pin.OUT; arm() runs after grant/reset.
PIO_TRANSPORT_CLAIMS_PINS_IN_INIT = False


class PsramError(Exception):
    """Illegal SCK / tCEM planning, or a transport contract violation."""


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


def be24(addr):
    addr = int(addr) & 0xFFFFFF
    return bytes([(addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF])


def sck_budget(sck_hz, tcem_us=TCEM_US_DEFAULT, margin=TCEM_MARGIN_DEFAULT):
    """Return allowed SCK cycles in one CE# pulse (tCEM with unused margin)."""
    return tcem_us * 1e-6 * sck_hz * (1.0 - margin)


def qpi_chunk_bytes(
    opcode,
    sck_hz,
    tcem_us=TCEM_US_DEFAULT,
    margin=TCEM_MARGIN_DEFAULT,
    mcu_payload_max=None,
):
    """Max payload bytes per CE# for QPI `0xEB` / `0x02`. Refuse nbytes_max < 1.

    SCK-only planning ignores Python put overhead. Pass *mcu_payload_max* so the
    MCU path raises CE# between small chunks (tCEM: max CE# low).
    """
    if opcode == CMD_QPI_READ:
        overhead = EB_OVERHEAD_SCK
    elif opcode == CMD_QPI_WRITE:
        overhead = WR_OVERHEAD_SCK
    else:
        raise PsramError("chunk planner is for QPI 0xEB / 0x02, got 0x%02X" % opcode)
    n = math.floor((sck_budget(sck_hz, tcem_us, margin) - overhead) / SCK_PER_BYTE_QPI)
    if mcu_payload_max is not None:
        n = min(n, int(mcu_payload_max))
    if n < 1:
        raise PsramError(
            "SCK %s Hz cannot fit one payload byte under tCEM=%s us (25%% margin)"
            % (sck_hz, tcem_us)
        )
    return int(n)


def drain_sm(sm):
    """Wait until a PIO state machine has finished shifting, then it may stop."""
    wait_idle = getattr(sm, "wait_idle", None)
    if wait_idle is not None:
        wait_idle()
        return
    drained = getattr(sm, "drained", None)
    if drained is False:
        raise PsramError("state machine deactivated before drain")


def park_and_switch_sm(old_sm, new_sm, park_sck=None):
    """Drain *old_sm*, park SCK low, then activate *new_sm*. No overlapping active(1)."""
    if old_sm is not None:
        drain_sm(old_sm)
        old_sm.active(0)
    if park_sck is not None:
        park_sck()
    if new_sm is not None:
        new_sm.active(1)


def spi_reset_frames():
    """SPI `0x66` then immediate `0x99` (separate CE# pulses)."""
    return (bytes([CMD_RESET_ENABLE]), bytes([CMD_RESET]))


def enter_qpi_frame():
    """SPI Enter Quad `0x35` (1-bit opcode)."""
    return bytes([CMD_ENTER_QPI])


def exit_qpi_frame():
    """QPI Exit Quad `0xF5` (4-bit opcode, 2 SCK)."""
    return bytes([CMD_EXIT_QPI])


def qpi_write_frame(addr, data):
    return bytes([CMD_QPI_WRITE]) + be24(addr) + bytes(data)


def qpi_read_cmd_addr(addr):
    return bytes([CMD_QPI_READ]) + be24(addr)


def qpi_exit_sck_count():
    """`0xF5` is one byte on a 4-bit bus: 2 SCK."""
    return len(exit_qpi_frame()) * SCK_PER_BYTE_QPI


class Psram:
    """Reset / enter / exit / chunked QPI read-write on a injected transport."""

    def __init__(
        self,
        transport,
        sck_hz=SCK_HZ_DEFAULT,
        tcem_us=TCEM_US_DEFAULT,
        margin=TCEM_MARGIN_DEFAULT,
        host=None,
    ):
        self.transport = transport
        self.host = host
        self.sck_hz = sck_hz
        self.tcem_us = tcem_us
        self.margin = margin
        self.eb_chunk = qpi_chunk_bytes(
            CMD_QPI_READ, sck_hz, tcem_us, margin, mcu_payload_max=MCU_QPI_PAYLOAD_MAX
        )
        self.wr_chunk = qpi_chunk_bytes(
            CMD_QPI_WRITE, sck_hz, tcem_us, margin, mcu_payload_max=MCU_QPI_PAYLOAD_MAX
        )

    def _spi_oe(self):
        if self.host is not None:
            self.host.spi_oe()

    def _qpi_write_oe(self):
        if self.host is not None:
            self.host.qpi_write_oe()

    def _qpi_read_oe(self):
        if self.host is not None:
            self.host.qpi_read_oe()

    def wait_tpu(self):
        wait_at_least_us(TPU_US, sleep=self.transport.sleep_us)

    def spi_reset(self, cs):
        self._spi_oe()
        enable, reset = spi_reset_frames()
        self.transport.spi_write(cs, enable)
        self.transport.spi_write(cs, reset)

    def enter_qpi(self, cs):
        self._spi_oe()
        self.transport.spi_write(cs, enter_qpi_frame())

    def exit_qpi(self, cs):
        self._qpi_write_oe()
        frame = exit_qpi_frame()
        self.transport.qpi_write(cs, frame)
        restore = getattr(self.transport, "restore_spi_pins", None)
        if restore is not None:
            restore()
        self._spi_oe()

    def enter_qpi_both(self):
        self.enter_qpi(CS_PSRAM0)
        self.enter_qpi(CS_PSRAM1)

    def exit_qpi_both(self):
        self.exit_qpi(CS_PSRAM0)
        self.exit_qpi(CS_PSRAM1)

    def bring_up_both(self):
        """CE# high tPU, SPI reset, Enter Quad on both devices."""
        self.wait_tpu()
        for cs in (CS_PSRAM0, CS_PSRAM1):
            self.spi_reset(cs)
            self.enter_qpi(cs)

    def write(self, cs, addr, data):
        payload = bytes(data)
        offset = 0
        while offset < len(payload):
            n = min(self.wr_chunk, len(payload) - offset)
            self._qpi_write_oe()
            self.transport.qpi_write(
                cs, qpi_write_frame(addr + offset, payload[offset : offset + n])
            )
            offset += n

    def read(self, cs, addr, n):
        out = bytearray()
        offset = 0
        remaining = int(n)
        while remaining > 0:
            k = min(self.eb_chunk, remaining)
            self._qpi_read_oe()
            chunk = self.transport.qpi_read(
                cs,
                qpi_read_cmd_addr(addr + offset),
                QPI_DUMMY_CYCLES,
                k,
            )
            if len(chunk) != k:
                raise PsramError(
                    "QPI read returned %d bytes, expected %d" % (len(chunk), k)
                )
            out.extend(chunk)
            offset += k
            remaining -= k
        return bytes(out)


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
        pull_thresh=8,
        sideset_init=(rp2.PIO.OUT_LOW,),
        out_init=(rp2.PIO.OUT_LOW,) * 4,
    )
    def qpi_write_cpha0():
        # 4-bit QPI write starting point: guide qspi_read nibble packing, reversed
        # for MOSI. SIO[3] is the MSB of each nibble. One byte = 2 SCK.
        out(pins, 4).side(0)
        nop().side(1)

    @rp2.asm_pio(
        in_shiftdir=0,
        autopush=True,
        push_thresh=8,
        sideset_init=(rp2.PIO.OUT_LOW,),
        set_init=(rp2.PIO.IN_LOW,) * 4,
    )
    def qpi_read_cpha0():
        # Must match QPI_READ_PIO_INTENT: nop on SCK low, in_ on rising SCK (D16).
        nop().side(0)
        in_(pins, 4).side(1)

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
        device 1. Default SCK is 20 MHz (tCEM planner refuses a too-slow SCK).
        Pin.OUT is claimed in arm(), not __init__ (D26: no drive before grant).
        """

        def __init__(self, sck_hz=SCK_HZ_DEFAULT, sleep=sleep_us):
            if rp2 is None or Pin is None:
                raise PsramError("PioTransport requires rp2 and machine.Pin")
            if PIO_TRANSPORT_CLAIMS_PINS_IN_INIT:
                raise PsramError("PioTransport must not claim pins in __init__")
            self.sck_hz = sck_hz
            self._sleep = sleep
            self._armed = False
            self.flash_cs = None
            self.ram_cs = None
            self._sck = None
            self.spi = None
            self._qpi_wr = None
            self._qpi_rd = None

        def arm(self):
            """Claim CS/SCK/SIO after BUS_GNT=1 or rst_n=0. Idempotent."""
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
            self._sck = Pin(PIN_SCK, Pin.OUT)
            self._sck.off()
            self.restore_spi_pins()
            self.spi = PIOSPI(1, PIN_MOSI, PIN_MISO, PIN_SCK, freq=self.sck_hz)
            self._qpi_wr = rp2.StateMachine(
                2,
                qpi_write_cpha0,
                freq=2 * self.sck_hz,
                sideset_base=Pin(PIN_SCK),
                out_base=Pin(PIN_MOSI),
            )
            self._qpi_rd = rp2.StateMachine(
                3,
                qpi_read_cpha0,
                freq=2 * self.sck_hz,
                sideset_base=Pin(PIN_SCK),
                in_base=Pin(PIN_MOSI),
            )
            self._qpi_wr.active(0)
            self._qpi_rd.active(0)
            self._armed = True

        def restore_spi_pins(self):
            """MOSI out, MISO in, SD2/SD3 pull-up in (SPI HOLD#/WP# safe)."""
            Pin(PIN_MOSI, Pin.OUT)
            Pin(PIN_MISO, Pin.IN)
            Pin(PIN_SD2, Pin.IN, Pin.PULL_UP)
            Pin(PIN_SD3, Pin.IN, Pin.PULL_UP)
            self.pin_modes = dict(SPI_PIN_MODES)

        def _park_sck(self):
            if self._sck is not None:
                self._sck.off()

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

        def qpi_write(self, cs, data):
            self.arm()
            payload = bytes(data)
            park_and_switch_sm(self.spi._sm, None, park_sck=self._park_sck)
            park_and_switch_sm(self._qpi_rd, None, park_sck=self._park_sck)
            for pin in (PIN_MOSI, PIN_MISO, PIN_SD2, PIN_SD3):
                Pin(pin, Pin.OUT)
            self._qpi_wr.active(0)
            for b in payload:
                self._qpi_wr.put(b, 24)
            self._select(cs)
            self._qpi_wr.active(1)
            self._drain_bytes(self._qpi_wr, len(payload))
            self._deselect()
            self._qpi_wr.active(0)
            self._park_sck()

        def qpi_read(self, cs, header, dummy_cycles, n):
            self.arm()
            header = bytes(header)
            park_and_switch_sm(self.spi._sm, None, park_sck=self._park_sck)
            park_and_switch_sm(self._qpi_rd, None, park_sck=self._park_sck)
            for pin in (PIN_MOSI, PIN_MISO, PIN_SD2, PIN_SD3):
                Pin(pin, Pin.OUT)
            self._qpi_wr.active(0)
            for b in header:
                self._qpi_wr.put(b, 24)
            self._select(cs)
            self._qpi_wr.active(1)
            self._drain_bytes(self._qpi_wr, len(header))
            park_and_switch_sm(self._qpi_wr, None, park_sck=self._park_sck)
            for pin in (PIN_MOSI, PIN_MISO, PIN_SD2, PIN_SD3):
                Pin(pin, Pin.IN)
            dummy_bytes = dummy_cycles // SCK_PER_BYTE_QPI
            self._qpi_rd.active(1)
            for _ in range(dummy_bytes):
                self._qpi_rd.get()
            out = bytearray(n)
            for i in range(n):
                out[i] = self._qpi_rd.get() & 0xFF
            self._drain_bytes(self._qpi_rd, dummy_bytes + n)
            self._qpi_rd.active(0)
            self._deselect()
            self._park_sck()
            return bytes(out)


def make_board_transport(sck_hz=SCK_HZ_DEFAULT):
    """Construct the ETR PIO master. Raises on CPython (no rp2)."""
    if rp2 is None:
        raise PsramError("rp2 is not available; inject a mock transport for tests")
    return PioTransport(sck_hz=sck_hz)
