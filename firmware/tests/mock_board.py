"""CPython mock of Tiny Tapeout DemoBoard pin ports (no serial, no ttboard)."""


class BitPort:
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


class MockShuttleDesign:
    def __init__(self):
        self.enabled = False

    def enable(self):
        self.enabled = True


class MockShuttle:
    def __init__(self):
        self.tt_um_lahnb_sgdma = MockShuttleDesign()


class MockDemoBoard:
    """Integer ui_in / uo_out / uio_oe_pico plus grant and optional START ACK."""

    def __init__(self, auto_ack_start=False):
        self.ui_in = BitPort(0, self._on_ui)
        self.uo_out = BitPort(0x01)  # DONE=1, BUS_GNT=0
        self.uio_oe_pico = BitPort(0, self._on_oe)
        self.shuttle = MockShuttle()
        self.clock_hz = None
        self._in_reset = False
        self.auto_ack_start = auto_ack_start
        self.dma_hook = None
        self.events = []
        self._start_seen = False
        self._complete_dma = False

    def clock_project_PWM(self, freq):
        self.clock_hz = freq

    def reset_project(self, asserted):
        self._in_reset = bool(asserted)
        self.events.append(("reset", bool(asserted)))
        if asserted:
            self.uo_out[0] = 1
            self.uo_out[1] = 0

    def _on_oe(self):
        self.events.append(("oe", int(self.uio_oe_pico), "req", int(self.ui_in[2])))

    def _on_ui(self):
        req = self.ui_in[2]
        start = self.ui_in[0]
        self.events.append(("ui", int(self.ui_in), "oe", int(self.uio_oe_pico)))
        if req:
            self.uo_out[1] = 1
        else:
            self.uo_out[1] = 0
        if start and not req and self.uo_out[0]:
            self._start_seen = True
        elif self._start_seen and not start:
            self._start_seen = False
            if self.auto_ack_start:
                self.uo_out[0] = 0
                if self.dma_hook is not None:
                    self.dma_hook()
                self._complete_dma = True

    def poll_tick(self):
        """Raise DONE after wait_busy has observed the low (runner tests)."""
        if self._complete_dma and self.uo_out[0] == 0:
            self.uo_out[0] = 1
            self._complete_dma = False
