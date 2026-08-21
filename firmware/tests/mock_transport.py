"""Mock SPI/QPI master with optional sparse PSRAM contents (no rp2)."""

from firmware.psram import (
    CMD_ENTER_QPI,
    CMD_EXIT_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
)


class MockTransport:
    def __init__(self):
        self.log = []
        self.mem = {0: {}, 1: {}}
        self.qpi = {0: False, 1: False}
        self.sleeps = []

    def sleep_us(self, us):
        self.sleeps.append(us)
        self.log.append(("sleep_us", us))

    def spi_write(self, cs, data):
        payload = bytes(data)
        self.log.append(("spi", cs, payload))
        if payload == bytes([CMD_ENTER_QPI]):
            self.qpi[cs] = True
        elif payload == bytes([CMD_RESET]):
            self.qpi[cs] = False

    def qpi_write(self, cs, data):
        payload = bytes(data)
        sck = 2 * len(payload)
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
        header = bytes(header)
        self.log.append(("qpi_read", cs, header, dummy_cycles, n))
        addr = 0
        if header and header[0] == CMD_QPI_READ and len(header) >= 4:
            addr = int.from_bytes(header[1:4], "big")
        bank = self.mem.get(cs, {})
        return bytes(bank.get(addr + i, 0) for i in range(n))


def attach_mock_dma(tt, transport):
    """On START accept, interpret installed PSRAM and write dest bytes back."""
    from firmware.chain import MemoryImage, interpret_chain

    def hook():
        mem = MemoryImage()
        for device, bank in transport.mem.items():
            for addr, value in bank.items():
                mem.poke(device, addr, value)
        result = interpret_chain(mem)
        snap = result.final_memory.snapshot()
        transport.mem = {device: dict(values) for device, values in snap.items()}

    tt.dma_hook = hook
