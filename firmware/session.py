"""Module-level TinyDMA session: one Board / DmaController / Psram set kept
alive across remote exec calls.

The host drives the MCU one statement at a time (`mpremote exec`), so nothing
can live in a caller's local scope. This module owns that state and exposes the
remote entry points. Every public name is `envelope`-wrapped and prints one
``OK`` / ``ERR`` line; the plain implementation stays available under the
leading-underscore name so entry points can compose (`_start_and_wait` calls
`_pulse_start` and `_wait_idle`, not the printing wrappers, which would emit
three lines).

Bus discipline here is the frozen host contract, not a convenience:

* MCU QSPI OE is Hi-Z unless BUS_GNT=1 or rst_n=0 (D26 bus keeper: while
  rst_n=1 and BUS_GNT=0 the ASIC owns the QSPI pins). Every span call takes the
  bus, drives only the OE mask its phase needs, and releases in `finally`.
* Unused `ui_in` bits stay 0 (D34); `Board.zero_ui_in` is the only whole-port
  writer.
* Recovery from a runaway chain is rst_n only (D23): `reset_dma` leaves rst_n
  asserted, and `recover` releases it, rechecks the idle host port, and puts
  both PSRAMs back in QPI.

Hardware binding goes through `HARDWARE_FACTORY` so host pytest can inject a
fake DemoBoard and a fake QSPI transport (see `hil/fake_hw.py`) with no
demoboard attached. On the MCU the factory is unset and `ttboard` plus the PIO
transport are imported lazily, which keeps this module importable on CPython.
"""

from .board.board import Board
from .board.pins import (
    CS_PSRAM0,
    CS_PSRAM1,
    OE_QPI,
    OE_QPI_READ,
    OE_SPI,
    PROJECT_CLOCK_HZ,
)
from .constants import DEVICES, SCK_HZ_DEFAULT
from .dma import DmaController
from .link import b64decode, envelope
from .psram import Psram

DEFAULT_TIMEOUT_MS = 5000

# `(sck_hz) -> (tt, transport, sleep_us)`; None means real demoboard hardware.
HARDWARE_FACTORY = None

# Live session state. `init` builds all three; `clear_state` drops them.
board = None
dma = None
psram = None

# True once both devices have been put in QPI by this session (Enter Quad
# 0x35). Tracked because Exit Quad 0xF5 must precede any further MCU SPI
# opcode: a device already in QPI reads 0x66 / 0x99 as 4-bit frames.
in_qpi = False

# Design enabled by the most recent init (shuttle project or /bitstreams name).
design = None


class SessionError(Exception):
    """A session call arrived before `init`, named an unknown device, or found
    the host port in an unexpected state."""


def set_hardware_factory(factory):
    """Install a `(sck_hz) -> (tt, transport, sleep_us)` factory.

    Host-side loopback tests call this to bind fakes; *sleep_us* may be None to
    keep the demoboard default sleep. Passing None restores real hardware.
    """
    global HARDWARE_FACTORY
    HARDWARE_FACTORY = factory


def _hardware(sck_hz):
    """Return `(tt, transport, sleep_us)` for this runtime.

    `ttboard` and the PIO transport are imported here, not at module scope, so
    CPython can import this module with no demoboard present.
    """
    if HARDWARE_FACTORY is not None:
        return HARDWARE_FACTORY(sck_hz)
    from ttboard.demoboard import DemoBoard

    from .board.qspi import make_board_transport

    return DemoBoard.get(), make_board_transport(sck_hz), None


def _require_ready():
    """Raise unless `init` has built the board, controller, and PSRAM driver."""
    if board is None or dma is None or psram is None:
        raise SessionError("call init(design_name) before any other session call")


def _device_cs(device):
    """Map a TCD device index (0 = PSRAM A, 1 = PSRAM B) to its CS index."""
    if isinstance(device, bool) or not isinstance(device, int):
        raise SessionError("device must be int 0 or 1, got %r" % (device,))
    if device not in DEVICES:
        raise SessionError("device must be 0 or 1, got %r" % (device,))
    return CS_PSRAM0 if device == 0 else CS_PSRAM1


def _init(design_name, clock_hz=PROJECT_CLOCK_HZ, sck_hz=SCK_HZ_DEFAULT):
    """Build the session and enable *design_name*, leaving the host port idle.

    `DmaController.enable_design` zeroes `ui_in`, asserts rst_n, mux-selects,
    starts the project clock, Hi-Zs the MCU QSPI pins, releases rst_n, and then
    samples DONE=1 / BUS_GNT=0. PSRAM mode is unknown after a fresh enable, so
    `in_qpi` starts False and `bring_up_psram` must run before START.

    Returns:
        dict: the design name plus the clock and SCK rates actually applied.
    """
    global board, dma, psram, in_qpi, design
    tt, transport, sleep_us = _hardware(sck_hz)
    board = Board(tt, sleep_us=sleep_us)
    dma = DmaController(board)
    dma.enable_design(design_name, clock_hz=clock_hz)
    psram = Psram(transport, sck_hz=sck_hz, dma=dma)
    in_qpi = False
    design = design_name
    return {"design": design_name, "clock_hz": int(clock_hz), "sck_hz": int(sck_hz)}


def _bring_up_psram(force=False):
    """Put both PSRAMs in QPI under grant: tPU, SPI 0x66 / 0x99, Enter Quad 0x35.

    tPU is the CE#-high power-up delay before the first command; the wait is
    elapsed-time based inside `Psram.wait_tpu`. Devices already in QPI are left
    alone unless *force*, in which case Exit Quad 0xF5 runs first so the SPI
    reset opcodes are seen on a 1-bit bus.

    Returns:
        str: ``"qpi"`` if this call brought the devices up, else
        ``"already-qpi"``.
    """
    _require_ready()
    global in_qpi
    if in_qpi and not force:
        return "already-qpi"
    dma.request_bus(oe=OE_SPI)
    try:
        if in_qpi:
            psram.exit_qpi_both()
            in_qpi = False
        psram.bring_up_both()
        in_qpi = True
    finally:
        dma.release_bus()
    return "qpi"


def _write_span(device, addr, b64_data):
    """QPI-write one base64 payload to *device* at *addr* under grant.

    Payload bytes arrive base64-encoded because the transport is a text REPL
    line. `Psram.write` chunks the payload so CE# is raised between MCU chunks
    (tCEM: max CE# low time allowed by the PSRAM refresh).

    Returns:
        dict: device, address, and the decoded byte count.
    """
    _require_ready()
    cs = _device_cs(device)
    addr = int(addr)
    data = b64decode(b64_data)
    dma.request_bus(oe=OE_QPI)
    try:
        psram.write(cs, addr, data)
    finally:
        dma.release_bus()
    return {"device": device, "addr": addr, "length": len(data)}


def _read_span(device, addr, length):
    """QPI-read *length* bytes from *device* at *addr* under grant.

    The bus is taken with `OE_QPI_READ` (CS and SCK driven, SIO0..3 Hi-Z) so
    the device, not the MCU, drives read data.

    Returns:
        bytes: the span, which `envelope` sends as a base64 payload.
    """
    _require_ready()
    cs = _device_cs(device)
    addr = int(addr)
    dma.request_bus(oe=OE_QPI_READ)
    try:
        return psram.read(cs, addr, int(length))
    finally:
        dma.release_bus()


def _pulse_start():
    """Pulse START with the bus released (requires DONE=1, BUS_REQ=0, GNT=0)."""
    _require_ready()
    dma.pulse_start()


def _wait_idle(timeout_ms=DEFAULT_TIMEOUT_MS):
    """Block until the ASIC is idle after START.

    An observed DONE low is the START-accept ACK; a chain short enough to
    finish first is treated as already idle, not a missed ACK. A real timeout
    kills the transfer (rst_n) and raises.
    """
    _require_ready()
    dma.wait_idle_after_start(timeout_ms)


def _start_and_wait(timeout_ms=DEFAULT_TIMEOUT_MS):
    """Pulse START and wait for idle in one remote call."""
    _pulse_start()
    _wait_idle(timeout_ms)


def _reset_dma():
    """Emergency stop: Hi-Z the MCU QSPI pins, zero `ui_in`, assert rst_n.

    rst_n is still asserted when this returns (kill is rst_n only; D23). Call
    `recover` to release it and re-enter QPI.
    """
    _require_ready()
    dma.kill_dma()


def _recover():
    """Release rst_n after a kill, recheck the idle host port, re-enter QPI.

    A kill does not reset the PSRAMs, so devices left in QPI get Exit Quad
    0xF5 before the SPI reset / Enter Quad pair.

    Returns:
        str: ``"qpi"`` once both devices are back in QPI.

    Raises:
        SessionError: DONE is not 1 or BUS_GNT is not 0 after rst_n release.
    """
    _require_ready()
    global in_qpi
    dma.reset(False)
    if not dma.done:
        raise SessionError("DONE=1 expected after rst_n release")
    if dma.bus_gnt:
        raise SessionError("BUS_GNT=0 expected after rst_n release")
    dma.request_bus(oe=OE_QPI)
    try:
        if in_qpi:
            psram.exit_qpi_both()
            in_qpi = False
        psram.bring_up_both()
        in_qpi = True
    finally:
        dma.release_bus()
    return "qpi"


def _status():
    """Return the sampled host port and MCU drive state.

    `oe` is the raw `uio_oe_pico` mask (0 = Hi-Z, the only legal value while
    rst_n=1 and BUS_GNT=0; D26).
    """
    _require_ready()
    return {
        "done": dma.done,
        "bus_gnt": dma.bus_gnt,
        "bus_req": dma.bus_req,
        "oe": dma.oe,
        "rst_n_low": dma.rst_n_low,
        "in_qpi": in_qpi,
    }


def _shutdown(exit_qpi=False):
    """Leave the board safe and drop the session.

    Optionally issues Exit Quad 0xF5 under grant so the next MCU SPI opcode is
    valid, then Hi-Zs the MCU QSPI pins and zeroes `ui_in` (BUS_REQ and START
    low, unused bits low; D34). State is cleared, so `init` must run again.
    """
    global in_qpi
    if dma is not None and psram is not None:
        if exit_qpi and in_qpi:
            dma.request_bus(oe=OE_QPI)
            try:
                psram.exit_qpi_both()
                in_qpi = False
            finally:
                dma.release_bus()
        dma.hiz()
        board.zero_ui_in()
    _clear_state()


def _clear_state():
    """Forget the board, controller, and PSRAM driver without touching pins.

    Matches what an MCU soft reboot does to module state; the loopback
    transport calls this so each host test starts uninitialized.
    """
    global board, dma, psram, in_qpi, design
    board = None
    dma = None
    psram = None
    in_qpi = False
    design = None


init = envelope(_init)
bring_up_psram = envelope(_bring_up_psram)
write_span = envelope(_write_span)
read_span = envelope(_read_span)
pulse_start = envelope(_pulse_start)
wait_idle = envelope(_wait_idle)
start_and_wait = envelope(_start_and_wait)
reset_dma = envelope(_reset_dma)
recover = envelope(_recover)
status = envelope(_status)
shutdown = envelope(_shutdown)
clear_state = envelope(_clear_state)
