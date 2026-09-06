"""Demoboard signal map: ui_in / uo_out bit indices, uio OE masks, ETR PMOD pins.

Leaf module: no imports, no board or DMA behaviour. Bit names ending in
``_BIT`` are positions; names ending in ``_MASK`` or starting with ``OE_`` are
masks over the eight ``uio`` bits.
"""

# --- Host control (ui_in) and status (uo_out) bit indices; src/top.v ---

START_BIT = 0
BUS_REQ_BIT = 2
DONE_BIT = 0
BUS_GNT_BIT = 1

# D34: the ASIC does not use ui_in[1] or ui_in[7:3]; firmware keeps them 0.
UI_IN_UNUSED_MASK = (1 << 1) | 0xF8

PROJECT_CLOCK_HZ = 66_000_000

# --- uio OE masks (SDK: a bit set to 1 is driven by the RP2) ---
# uio bit: 0 flash CS, 1 SIO0, 2 SIO1, 3 SCK, 4 SIO2, 5 SIO3, 6 RAM A CS, 7 RAM B CS.

OE_HIZ = 0
# SPI: drive flash CS, MOSI, SCK, RAM CS; MISO/SD2/SD3 are inputs (HOLD#/WP#).
OE_SPI = (1 << 0) | (1 << 1) | (1 << 3) | (1 << 6) | (1 << 7)
# QPI command / address / write data: all eight uio bits driven by the RP2.
OE_QPI = 0xFF
# QPI dummy / read data: drive CS and SCK only; SIO0..3 must be Hi-Z.
OE_QPI_READ = (1 << 0) | (1 << 3) | (1 << 6) | (1 << 7)
SIO_OE_MASK = (1 << 1) | (1 << 2) | (1 << 4) | (1 << 5)

# --- ETR uio[0..7] -> GPIO25..32 (Rohan Verma / TT QSPI PMOD guide) ---

QSPI_BASE = 25
PIN_FLASH_CS = QSPI_BASE + 0
PIN_MOSI = QSPI_BASE + 1
PIN_MISO = QSPI_BASE + 2
PIN_SCK = QSPI_BASE + 3
PIN_SD2 = QSPI_BASE + 4
PIN_SD3 = QSPI_BASE + 5
PIN_RAM_A_CS = QSPI_BASE + 6
PIN_RAM_B_CS = QSPI_BASE + 7

# --- Device chip-select indices (RAM A is device 0, RAM B is device 1) ---

CS_PSRAM0 = 0
CS_PSRAM1 = 1
