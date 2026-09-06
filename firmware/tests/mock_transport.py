"""Mock SPI/QPI master with sparse PSRAM contents (no rp2).

SPI while a CS is already in QPI raises; QPI before Enter Quad raises. A CE#
pulse carrying more than *max_payload_per_ce* payload bytes raises, standing in
for tCEM (max CE# low time) that host pytest cannot measure in wall clock.
"""

from firmware.board.pins import OE_QPI_READ, SIO_OE_MASK
from firmware.board.qspi import SPI_PIN_MODES, QspiError
from firmware.constants import (
    CMD_ENTER_QPI,
    CMD_EXIT_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
    MCU_QPI_PAYLOAD_MAX,
)


class MockTransport:
    def __init__(self, oe_getter=None, max_payload_per_ce=MCU_QPI_PAYLOAD_MAX):
        self.log = []
        self.mem = {0: {}, 1: {}}
        self.qpi = {0: False, 1: False}
        self.sleeps = []
        self.oe_getter = oe_getter
        self.oe_during_read = []
        self.pin_modes = None
        self.max_payload_per_ce = max_payload_per_ce
        self.ce_pulses = 0

    def sleep_us(self, us):
        self.sleeps.append(us)
        self.log.append(("sleep_us", us))

    def restore_spi_pins(self):
        self.pin_modes = dict(SPI_PIN_MODES)
        self.log.append(("restore_spi_pins", dict(self.pin_modes)))

    def spi_write(self, cs, data):
        payload = bytes(data)
        if self.qpi.get(cs):
            raise QspiError("SPI while CS %s is in QPI" % cs)
        self.log.append(("spi", cs, payload))
        if payload == bytes([CMD_ENTER_QPI]):
            self.qpi[cs] = True
        elif payload == bytes([CMD_RESET]):
            self.qpi[cs] = False

    def qpi_write(self, cs, data):
        if not self.qpi.get(cs):
            raise QspiError("QPI write while CS %s is not in QPI" % cs)
        payload = bytes(data)
        sck = 2 * len(payload)
        n_payload = max(0, len(payload) - 4)
        if payload == bytes([CMD_EXIT_QPI]):
            n_payload = 0
        if self.max_payload_per_ce is not None and n_payload > self.max_payload_per_ce:
            raise QspiError(
                "CE# held for %d payload bytes (max %d); raise CE# between chunks"
                % (n_payload, self.max_payload_per_ce)
            )
        self.ce_pulses += 1
        self.log.append(("qpi_write", cs, payload, sck))
        if payload == bytes([CMD_EXIT_QPI]):
            self.qpi[cs] = False
            return
        if payload and payload[0] == CMD_QPI_WRITE and len(payload) >= 4:
            addr = int.from_bytes(payload[1:4], "big")
            bank = self.mem.setdefault(cs, {})
            for offset, value in enumerate(payload[4:]):
                bank[addr + offset] = value

    def qpi_read(self, cs, header, dummy_cycles, n):
        if not self.qpi.get(cs):
            raise QspiError("QPI read while CS %s is not in QPI" % cs)
        header = bytes(header)
        if self.oe_getter is not None:
            oe = int(self.oe_getter())
            self.oe_during_read.append(oe)
            if oe & SIO_OE_MASK:
                raise QspiError(
                    "SIO OE must be 0 during QPI read (have 0x%02X, expect 0x%02X)"
                    % (oe, OE_QPI_READ)
                )
        self.ce_pulses += 1
        self.log.append(("qpi_read", cs, header, dummy_cycles, n))
        addr = 0
        if header and header[0] == CMD_QPI_READ and len(header) >= 4:
            addr = int.from_bytes(header[1:4], "big")
        bank = self.mem.get(cs, {})
        return bytes(bank.get(addr + i, 0) for i in range(n))
