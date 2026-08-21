"""Firmware architecture constants.

Leaf module: no imports of tcd, psram, asic, chain, or ``test/``.
Overlapping TCD / opcode / dummy / head / buffer names are a mechanical copy
of ``test/reference/constants.py`` (copies of ``src/types.svh`` and
``docs/llm/04-tcd-and-datapath.md``, never parsed from SystemVerilog).
When a shared number changes, edit both files.
"""

# --- TCD layout (types.svh QPI_TCD_BYTES / tcd_t; 04-tcd-and-datapath.md) ---

TCD_BYTES = 11

PTR_BITS = 23
PTR_MAX = (1 << PTR_BITS) - 1  # 0x7FFFFF, APS6404L A[22:0] address space
PTR_BIT23 = 1 << PTR_BITS  # don't-care MSB (D35); not device select
PTR_FIELD_MAX = (1 << 24) - 1  # 0xFFFFFF, full 24-bit TCD pointer field
TRANSFER_LEN_MAX = 0xFF
RESERVED_MAX = 0xF

OFFSET_SRC_PTR = 0
OFFSET_DEST_PTR = 3
OFFSET_TRANSFER_LEN = 6
OFFSET_NEXT_TCD = 7
OFFSET_CTRL_FLAGS = 10

CTRL_QUIT_BIT = 4
CTRL_SRC_DEVICE_BIT = 5
CTRL_DEST_DEVICE_BIT = 6
CTRL_NEXT_DEVICE_BIT = 7
CTRL_RESERVED_SHIFT = 0

# --- Buffer (types.svh DMA_BUF_DEPTH_MAX / tapeout N; chain default is N=5) ---

DMA_BUF_DEPTH_MAX = 8
DMA_BUF_DEPTH_TAPEOUT = 5

# --- QPI protocol (types.svh qspi_cmd_t / qspi_wait_cycles) ---

CMD_QPI_READ = 0xEB
CMD_QPI_WRITE = 0x02
OPCODE_READ = CMD_QPI_READ
OPCODE_WRITE = CMD_QPI_WRITE
QSPI_CMD_FAST_READ = CMD_QPI_READ
QSPI_CMD_WRITE = CMD_QPI_WRITE

CMD_NIBBLES = 2
ADDR_NIBBLES = 6
QPI_DUMMY_CYCLES = 6
FAST_READ_DUMMY_CYCLES = QPI_DUMMY_CYCLES

# --- Fixed head (D18) ---

HEAD_DEVICE = 0
HEAD_ADDRESS = 0x000000
DEVICES = (0, 1)

# --- MCU SPI bring-up opcodes (not emitted by the ASIC) ---

CMD_RESET_ENABLE = 0x66
CMD_RESET = 0x99
CMD_ENTER_QPI = 0x35
CMD_EXIT_QPI = 0xF5

# --- Host pin indices (ui_in / uo_out bit positions, not masks) ---

START_BIT = 0
BUS_REQ_BIT = 2
DONE_BIT = 0
BUS_GNT_BIT = 1

PROJECT_CLOCK_HZ = 66_000_000
DEFAULT_PROJECT = "tt_um_lahnb_sgdma"

# uio bit: 0 flash CS, 1 SIO0, 2 SIO1, 3 SCK, 4 SIO2, 5 SIO3, 6 RAM A CS, 7 RAM B CS.
# SDK: bits set to 1 are driven by the RP2.
OE_HIZ = 0
# SPI: drive flash CS, MOSI, SCK, RAM CS; MISO/SD2/SD3 are inputs (HOLD#/WP#).
OE_SPI = (
    (1 << 0) | (1 << 1) | (1 << 3) | (1 << 6) | (1 << 7)
)
# QPI command/addr/write: all eight uio bits driven by the RP2.
OE_QPI = 0xFF
# QPI read data/dummy: drive CS and SCK only; SIO0..3 must be Hi-Z.
OE_QPI_READ = (1 << 0) | (1 << 3) | (1 << 6) | (1 << 7)
SIO_OE_MASK = (1 << 1) | (1 << 2) | (1 << 4) | (1 << 5)

# Conservative MCU payload bytes per CE# pulse. SCK-only planning can fit more
# under tCEM (max CE# low); Python-fed PIO holds CE# across puts, so the MCU
# path raises CE# between these chunks. Residual: one PIO burst wall-clock on
# hardware is not proven by host pytest.
MCU_QPI_PAYLOAD_MAX = 1

# Optional extra START high time. Capture does not depend on this: two GPIO
# writes already last much longer than two 66 MHz synchronizer clocks.
START_HOLD_US = 0
# Tight samples looking for DONE low. Not a timed wait; Python loops only.
BUSY_SAMPLE_TRIES = 8

# --- ETR uio[0..7] -> GPIO25..32 (Rohan Verma / TT QSPI guide) ---

QSPI_BASE = 25
PIN_FLASH_CS = QSPI_BASE + 0
PIN_MOSI = QSPI_BASE + 1
PIN_MISO = QSPI_BASE + 2
PIN_SCK = QSPI_BASE + 3
PIN_SD2 = QSPI_BASE + 4
PIN_SD3 = QSPI_BASE + 5
PIN_RAM_A_CS = QSPI_BASE + 6
PIN_RAM_B_CS = QSPI_BASE + 7

CS_PSRAM0 = 0
CS_PSRAM1 = 1

# --- MCU QPI planner (tCEM: max CE# low; tPU: CE# high after power) ---

TCEM_US_DEFAULT = 4.0
TCEM_MARGIN_DEFAULT = 0.25
SCK_HZ_DEFAULT = 20_000_000
TPU_US = 150
# tRST min 50 ns and tCPH min 18 ns. MCU Python/GPIO between commands already
# exceeds both; do not insert a 1 us sleep as if it were the timing element.
EB_OVERHEAD_SCK = 14  # 2 cmd + 6 addr + 6 dummy
WR_OVERHEAD_SCK = 8  # 2 cmd + 6 addr
SCK_PER_BYTE_QPI = 2  # 8 bits / 4 bits per SCK
