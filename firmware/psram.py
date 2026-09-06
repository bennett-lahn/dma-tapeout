"""APS6404L bring-up and MCU QPI install/dump.

SPI reset (`0x66` then `0x99`) and Enter Quad (`0x35`) run in 1-bit SPI.
Install/dump after enter use QPI write `0x02` and QPI read `0xEB` (6 dummy
cycles), chunked so each CE# low pulse stays under tCEM (max CE# low time
allowed by the PSRAM refresh, default 4 us extended grade, 25% margin). Exit
Quad `0xF5` is a QPI opcode (2 SCK). Flash CS stays high; this module does not
program flash Quad Enable.

Byte movement is the transport's job (`firmware/board/qspi.py` on hardware, a
mock in host pytest). Pin directions are the DMA controller's job: pass a
`DmaController` as *dma* so each phase sets the legal `uio_oe_pico` (D26 bus
keeper: the MCU only drives while BUS_GNT=1 or rst_n=0).
"""

import math

from .board.pins import CS_PSRAM0, CS_PSRAM1
from .board.qspi import wait_at_least_us
from .constants import (
    CMD_ENTER_QPI,
    CMD_EXIT_QPI,
    CMD_QPI_READ,
    CMD_QPI_WRITE,
    CMD_RESET,
    CMD_RESET_ENABLE,
    EB_OVERHEAD_SCK,
    MCU_QPI_PAYLOAD_MAX,
    QPI_DUMMY_CYCLES,
    SCK_HZ_DEFAULT,
    SCK_PER_BYTE_QPI,
    TCEM_MARGIN_DEFAULT,
    TCEM_US_DEFAULT,
    TPU_US,
    WR_OVERHEAD_SCK,
)


class PsramError(Exception):
    """Illegal SCK / tCEM planning, or a device protocol violation."""


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
    MCU path raises CE# between small chunks (tCEM: max CE# low time).
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
    """Reset / enter / exit / chunked QPI read-write on an injected transport."""

    def __init__(
        self,
        transport,
        sck_hz=SCK_HZ_DEFAULT,
        tcem_us=TCEM_US_DEFAULT,
        margin=TCEM_MARGIN_DEFAULT,
        dma=None,
    ):
        self.transport = transport
        self.dma = dma
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
        if self.dma is not None:
            self.dma.spi_oe()

    def _qpi_write_oe(self):
        if self.dma is not None:
            self.dma.qpi_write_oe()

    def _qpi_read_oe(self):
        if self.dma is not None:
            self.dma.qpi_read_oe()

    def wait_tpu(self):
        """Hold CE# high for tPU (power-up delay before the first command)."""
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
