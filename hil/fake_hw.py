"""In-process fake demoboard, QSPI PMOD, and DMA engine for host-side HIL tests.

`firmware.session` binds hardware through a factory, so a `FakeHardware`
instance can stand in for `DemoBoard.get()` plus the PIO transport with no
board attached. That is what lets `hil.link.LoopbackTransport` exercise the
whole link and session round-trip under pytest.

What the fake does enforce, because host pytest can check it without a scope:

* D26 bus keeper - `uio_oe_pico` must be Hi-Z (0x00) unless BUS_GNT=1 or
  rst_n=0, and each transfer phase must present its own OE mask (`OE_SPI` for
  1-bit bring-up, `OE_QPI` for QPI command / address / write data, SIO Hi-Z
  during QPI read data).
* D34 - unused `ui_in` bits stay 0.
* Device mode - SPI opcodes are refused while a device is in QPI, and QPI
  opcodes are refused before Enter Quad 0x35.
* One CE# pulse carries at most `MCU_QPI_PAYLOAD_MAX` payload bytes, the
  stand-in for tCEM (max CE# low time allowed by the PSRAM refresh) that host
  pytest cannot measure in wall clock.

What it deliberately does not model: BUS_GNT is combinational with BUS_REQ (no
two-flop synchronizer), and START is accepted on the falling edge instead of a
controller DONE-low pulse. The chain engine does use tapeout N=5
(`DMA_BUF_DEPTH_TAPEOUT`) chunking so overlapping spans match the sim oracle;
that is still not coverage of the RTL handshake.
"""

from firmware.board.board import EXPECTED_MODE
from firmware.board.pins import (
    BUS_GNT_BIT,
    BUS_REQ_BIT,
    DONE_BIT,
    OE_HIZ,
    OE_QPI,
    OE_QPI_READ,
    OE_SPI,
    SIO_OE_MASK,
    START_BIT,
    UI_IN_UNUSED_MASK,
)
from firmware.board.qspi import SPI_PIN_MODES
from firmware.constants import (
    CMD_ENTER_QPI,
    CMD_EXIT_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
    DEVICES,
    DMA_BUF_DEPTH_TAPEOUT,
    HEAD_ADDRESS,
    HEAD_DEVICE,
    MCU_QPI_PAYLOAD_MAX,
    PTR_MAX,
    SCK_HZ_DEFAULT,
    TCD_BYTES,
)
from firmware.tcd import decode_tcd

# info.yaml top_module; the M7 FPGA bitstream is enabled under the same name.
DESIGN_NAME = "tt_um_lahnb_sgdma"

# Fetch budget for the fake chain engine, matching the debug walker default.
MAX_CHAIN_NODES = 64


class FakeHardwareError(Exception):
    """The firmware broke a rule the fake board or PMOD enforces."""


class BitPort:
    """DemoBoard-style byte port: int-like, indexable per bit, change callback."""

    def __init__(self, value=0, on_change=None):
        self._value = int(value) & 0xFF
        self._on_change = on_change

    def _set(self, value):
        self._value = int(value) & 0xFF
        if self._on_change is not None:
            self._on_change()

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._set(value)

    def __int__(self):
        return self._value

    def __getitem__(self, bit):
        return (self._value >> bit) & 1

    def __setitem__(self, bit, value):
        if value:
            self._set(self._value | (1 << bit))
        else:
            self._set(self._value & ~(1 << bit))


def read_byte(memory, device, address):
    """Read one byte of a sparse device image; undefined bytes read as 0."""
    return memory.get(device, {}).get(address & PTR_MAX, 0)


def write_byte(memory, device, address, value):
    """Write one byte into a sparse device image."""
    memory.setdefault(device, {})[address & PTR_MAX] = int(value) & 0xFF


def read_span(memory, device, address, length):
    """Read *length* bytes of a sparse device image."""
    return bytes(read_byte(memory, device, address + i) for i in range(int(length)))


def run_chain(
    memory,
    head_device=HEAD_DEVICE,
    head_address=HEAD_ADDRESS,
    max_nodes=MAX_CHAIN_NODES,
    dma_buf_depth=DMA_BUF_DEPTH_TAPEOUT,
):
    """Move bytes the way a correct TinyDMA chain must; return the node count.

    Fetches an 11-byte descriptor from the fixed head (PSRAM0 0x000000), stops
    on QUIT before copying anything, otherwise copies TRANSFER_LEN bytes from
    SRC to DEST in `dma_buf_depth` chunks (tapeout N=5 by default) and follows
    NEXT_TCD / NEXT_DEVICE. Pointers are masked to A[22:0] because ptr[23] is
    don't-care (D35). Chunked copy matches the sim oracle on overlapping
    spans; this is still a functional stand-in, not coverage of the RTL
    handshake.

    Raises:
        FakeHardwareError: no QUIT within *max_nodes* descriptors.
    """
    depth = max(int(dma_buf_depth), 1)
    device = head_device
    address = head_address & PTR_MAX
    nodes = 0
    while nodes < max_nodes:
        nodes += 1
        tcd = decode_tcd(read_span(memory, device, address, TCD_BYTES))
        if tcd.quit:
            return nodes
        src = tcd.src_ptr & PTR_MAX
        dest = tcd.dest_ptr & PTR_MAX
        remaining = tcd.transfer_len
        while remaining > 0:
            chunk = min(depth, remaining)
            payload = [
                read_byte(memory, tcd.src_device, src + offset)
                for offset in range(chunk)
            ]
            for offset, value in enumerate(payload):
                write_byte(memory, tcd.dest_device, dest + offset, value)
            remaining -= chunk
            if remaining > 0:
                src += chunk
                dest += chunk
        device = tcd.next_device
        address = tcd.next_tcd & PTR_MAX
    raise FakeHardwareError("chain did not QUIT within %d descriptors" % max_nodes)


class FakeQspi:
    """SPI / QPI byte mover over the shared sparse PSRAM images.

    Same transport surface `Psram` expects on hardware (`spi_write`,
    `qpi_write`, `qpi_read`, `sleep_us`, `restore_spi_pins`), with the OE,
    device-mode, and per-CE# payload rules from the module docstring enforced.
    """

    def __init__(
        self,
        memory=None,
        oe_getter=None,
        max_payload_per_ce=MCU_QPI_PAYLOAD_MAX,
    ):
        self.mem = {device: {} for device in DEVICES} if memory is None else memory
        self.oe_getter = oe_getter
        self.max_payload_per_ce = max_payload_per_ce
        self.qpi = {device: False for device in DEVICES}
        self.log = []
        self.sleeps = []
        self.ce_pulses = 0
        self.pin_modes = None

    def sleep_us(self, us):
        """Record a requested nap. tPU / tRST are wall-clock waits in `Psram`."""
        self.sleeps.append(us)

    def restore_spi_pins(self):
        """Put SIO back in SPI-safe directions (MOSI out, MISO / SD2 / SD3 in)."""
        self.pin_modes = dict(SPI_PIN_MODES)

    def _oe(self):
        return OE_HIZ if self.oe_getter is None else int(self.oe_getter())

    def _require_oe(self, expected, phase):
        oe = self._oe()
        if oe != expected:
            raise FakeHardwareError(
                "%s needs uio_oe_pico=0x%02X, have 0x%02X" % (phase, expected, oe)
            )

    def _require_mode(self, cs, qpi, opcode):
        if cs not in self.qpi:
            raise FakeHardwareError("no device on CS %r" % (cs,))
        if self.qpi[cs] != qpi:
            raise FakeHardwareError(
                "opcode 0x%02X needs device %d in %s"
                % (opcode, cs, "QPI" if qpi else "SPI")
            )

    def spi_write(self, cs, data):
        """One 1-bit SPI frame in its own CE# pulse."""
        payload = bytes(data)
        opcode = payload[0] if payload else 0
        self._require_oe(OE_SPI, "SPI write")
        self._require_mode(cs, False, opcode)
        self.ce_pulses += 1
        self.log.append(("spi", cs, payload))
        if payload == bytes([CMD_ENTER_QPI]):
            self.qpi[cs] = True
        elif payload == bytes([CMD_RESET]):
            self.qpi[cs] = False

    def qpi_write(self, cs, data):
        """One QPI frame: opcode, optional 24-bit address, optional payload."""
        payload = bytes(data)
        opcode = payload[0] if payload else 0
        self._require_oe(OE_QPI, "QPI write")
        self._require_mode(cs, True, opcode)
        n_payload = 0 if opcode == CMD_EXIT_QPI else max(0, len(payload) - 4)
        if self.max_payload_per_ce is not None and n_payload > self.max_payload_per_ce:
            raise FakeHardwareError(
                "CE# held for %d payload bytes (max %d); raise CE# between chunks"
                % (n_payload, self.max_payload_per_ce)
            )
        self.ce_pulses += 1
        self.log.append(("qpi_write", cs, payload))
        if opcode == CMD_EXIT_QPI:
            self.qpi[cs] = False
            return
        if opcode == CMD_QPI_WRITE and len(payload) >= 4:
            address = int.from_bytes(payload[1:4], "big")
            for offset, value in enumerate(payload[4:]):
                write_byte(self.mem, cs, address + offset, value)

    def qpi_read(self, cs, header, dummy_cycles, n):
        """QPI 0xEB: command / address driven, then *n* bytes with SIO floating."""
        header = bytes(header)
        opcode = header[0] if header else 0
        oe = self._oe()
        if oe & SIO_OE_MASK:
            raise FakeHardwareError(
                "SIO must be Hi-Z during QPI read data, have 0x%02X" % oe
            )
        self._require_oe(OE_QPI_READ, "QPI read")
        self._require_mode(cs, True, opcode)
        if self.max_payload_per_ce is not None and n > self.max_payload_per_ce:
            raise FakeHardwareError(
                "CE# held for %d read bytes (max %d); raise CE# between chunks"
                % (n, self.max_payload_per_ce)
            )
        self.ce_pulses += 1
        self.log.append(("qpi_read", cs, header, dummy_cycles, n))
        address = 0
        if opcode == CMD_QPI_READ and len(header) >= 4:
            address = int.from_bytes(header[1:4], "big")
        return read_span(self.mem, cs, address, n)


class FakeShuttleDesign:
    """One mux-selectable design; `enable` is what `Board` calls."""

    def __init__(self, name):
        self.name = name
        self.enabled = False
        self.enables = 0

    def enable(self):
        self.enabled = True
        self.enables += 1


class FakeShuttle:
    """Only the configured design exists; any other name is unknown."""

    def __init__(self, name):
        self.name = name
        setattr(self, name, FakeShuttleDesign(name))

    def __getitem__(self, name):
        design = getattr(self, name, None)
        if not isinstance(design, FakeShuttleDesign):
            raise KeyError(name)
        return design


class FakeDemoBoard:
    """DemoBoard-shaped fake that polices D26 / D34 and runs the chain on START.

    Ports are `BitPort`s so both integer and per-bit access work, matching the
    two shapes `firmware.board.board` handles. START is armed on a rising edge
    while DONE=1 and BUS_REQ=0, and the chain runs on the falling edge; DONE
    then goes low until `poll_tick` (driven by the board sleep) raises it.

    Options for the paths the controller must handle:

    * *instant_complete* - DONE never observed low (chain finished first)
    * *never_complete* - DONE stays low and no bytes move (runaway chain)
    """

    def __init__(
        self,
        memory=None,
        design_name=DESIGN_NAME,
        instant_complete=False,
        never_complete=False,
        auto_grant=True,
    ):
        self.mem = {device: {} for device in DEVICES} if memory is None else memory
        self.ui_in = BitPort(0, self._on_ui)
        self.uo_out = BitPort(1 << DONE_BIT)  # DONE=1, BUS_GNT=0
        self.uio_oe_pico = BitPort(OE_HIZ, self._on_oe)
        self.shuttle = FakeShuttle(design_name)
        self.mode = EXPECTED_MODE
        self.clock_hz = None
        self.instant_complete = instant_complete
        self.never_complete = never_complete
        self.auto_grant = auto_grant
        self.starts = 0
        self.chain_nodes = []
        self.events = []
        self._reset_held = False
        self._start_armed = False
        self._pending_done = False

    @property
    def done(self):
        return self.uo_out[DONE_BIT] == 1

    @property
    def bus_gnt(self):
        return self.uo_out[BUS_GNT_BIT] == 1

    def clock_project_PWM(self, freq):
        self.clock_hz = freq

    def reset_project(self, asserted):
        """Drive rst_n. Asserted forces the idle host port and drops START state."""
        self._reset_held = bool(asserted)
        self.events.append(("reset", bool(asserted)))
        if asserted:
            self.uo_out[DONE_BIT] = 1
            self.uo_out[BUS_GNT_BIT] = 0
            self._start_armed = False
            self._pending_done = False

    def _on_oe(self):
        oe = int(self.uio_oe_pico)
        if oe != OE_HIZ and not (self.bus_gnt or self._reset_held):
            raise FakeHardwareError(
                "D26: uio_oe_pico=0x%02X while rst_n=1 and BUS_GNT=0" % oe
            )
        self.events.append(("oe", oe))

    def _on_ui(self):
        ui = int(self.ui_in)
        if ui & UI_IN_UNUSED_MASK:
            raise FakeHardwareError(
                "D34: unused ui_in bits must stay 0, got 0x%02X" % ui
            )
        self.events.append(("ui", ui))
        req = self.ui_in[BUS_REQ_BIT]
        start = self.ui_in[START_BIT]
        if self.auto_grant:
            self.uo_out[BUS_GNT_BIT] = 1 if req and not self._reset_held else 0
        if start and not req and self.done:
            self._start_armed = True
        elif self._start_armed and not start:
            self._start_armed = False
            self._accept_start()

    def _accept_start(self):
        """Run the chain against the shared memory, then report DONE."""
        self.starts += 1
        if self.never_complete:
            self.uo_out[DONE_BIT] = 0
            self._pending_done = False
            return
        self.chain_nodes.append(run_chain(self.mem))
        if self.instant_complete:
            self.uo_out[DONE_BIT] = 1
            self._pending_done = False
            return
        self.uo_out[DONE_BIT] = 0
        self._pending_done = True

    def poll_tick(self):
        """Raise DONE once the controller has had its chance to sample it low."""
        if self._pending_done and self.uo_out[DONE_BIT] == 0:
            self.uo_out[DONE_BIT] = 1
            self._pending_done = False


class FakeHardware:
    """Fake board plus QSPI PMOD over one shared memory, callable as a factory.

    `firmware.session.set_hardware_factory(FakeHardware())` is all the loopback
    needs: calling the instance returns `(tt, transport, sleep_us)`, where
    `sleep_us` ticks the fake board so a polled DONE can rise.
    """

    def __init__(
        self,
        design_name=DESIGN_NAME,
        instant_complete=False,
        never_complete=False,
        max_payload_per_ce=MCU_QPI_PAYLOAD_MAX,
    ):
        self.memory = {device: {} for device in DEVICES}
        self.tt = FakeDemoBoard(
            memory=self.memory,
            design_name=design_name,
            instant_complete=instant_complete,
            never_complete=never_complete,
        )
        self.transport = FakeQspi(
            memory=self.memory,
            oe_getter=lambda: int(self.tt.uio_oe_pico),
            max_payload_per_ce=max_payload_per_ce,
        )
        self.sck_hz = None
        self.polls = 0

    def __call__(self, sck_hz=SCK_HZ_DEFAULT):
        """Hardware factory hook: `(tt, transport, sleep_us)` for the session."""
        self.sck_hz = sck_hz
        return self.tt, self.transport, self.sleep_us

    def sleep_us(self, us):
        """Board sleep: advances the fake board instead of burning wall clock."""
        self.polls += 1
        self.tt.poll_tick()

    def place(self, device, address, data):
        """Preload device memory directly, bypassing the MCU transport."""
        for offset, value in enumerate(bytes(data)):
            write_byte(self.memory, device, address + offset, value)

    def fetch(self, device, address, length):
        """Read device memory directly, for test observation (not a QPI dump)."""
        return read_span(self.memory, device, address, length)
