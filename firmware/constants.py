"""Firmware architecture constants: TCD layout, QPI protocol, MCU timing budgets.

Leaf module: no imports of tcd, psram, dma, board, or ``test/``. Demoboard
signal mapping (ui_in / uo_out bit indices, uio OE masks, PMOD GPIO numbers)
lives in ``firmware/board/pins.py``, not here.

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
SCK_PER_BYTE_QPI = 2  # 8 bits / 4 bits per SCK
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

# --- MCU host-protocol budgets (bit positions live in board/pins.py) ---

# qpi_write_cpha0 uses fifo_join=JOIN_TX (8-word TX, no RX). One FIFO word is
# one QPI byte. qpi_write prefills that FIFO while the SM is idle so CE# stays
# high during put() (tCEM: max CE# low). A default 4-word TX FIFO cannot hold
# a 5-byte write frame (0x02 + 24-bit addr + 1 payload) and the fifth put()
# blocks forever.
QPI_WRITE_CMD_ADDR_BYTES = 4  # 0x02 + 24-bit address
QPI_WRITE_TX_FIFO_WORDS = 8  # JOIN_TX depth; do not JOIN_TX the read SM
# Whole `0x02`+addr+payload frame is prefilling the idle write SM, so this is
# also the max frame size. `qpi_write` raises instead of hanging on put().
MCU_QPI_WRITE_FRAME_MAX = QPI_WRITE_TX_FIFO_WORDS
MCU_QPI_WRITE_PAYLOAD_FIFO_MAX = (
    QPI_WRITE_TX_FIFO_WORDS - QPI_WRITE_CMD_ADDR_BYTES
)

# qpi_read_cpha0 pushes one word per QPI byte and cannot tell a dummy cycle
# from data, so the dummy cycles occupy FIFO words too: a frame needs
# (QPI_DUMMY_CYCLES / SCK_PER_BYTE_QPI) + n words. The read SM uses JOIN_RX for
# 8. Overrunning that does not raise on hardware; the SM stalls mid-frame with
# SCK parked high by side-set and the capture stops tracking the burst, which
# shows up as dropped and repeated nibbles from the first stalled word on.
# `qpi_read` enforces the bound instead of letting it corrupt silently.
QPI_READ_RX_FIFO_WORDS = 8  # JOIN_RX depth; do not JOIN_RX the write SM
QPI_READ_DUMMY_WORDS = QPI_DUMMY_CYCLES // SCK_PER_BYTE_QPI

# Measured on ETR hardware: the PIO capture point is one SCK behind the
# PSRAM's data launch, so the first sample of a frame is the bus turnaround
# and every real nibble lands one position late. `in_(pins, 5).side(1)` reads
# the synchronized input at the start of the same cycle in which it raises
# SCK, i.e. before its own edge reaches the device, and tACLK (read data valid
# after falling SCK) plus round-trip flight add to that.
#
# The SM pushes two nibbles per FIFO word, so the capture cannot be moved by a
# single SCK inside the program. `qpi_read` clocks one spare byte instead and
# `realign_qpi_nibbles` re-pairs the stream. That spare word is why the frame
# ceiling is 4 and not 5.
QPI_READ_LAG_NIBBLES = 1
QPI_READ_LAG_WORDS = (QPI_READ_LAG_NIBBLES + SCK_PER_BYTE_QPI - 1) // SCK_PER_BYTE_QPI
MCU_QPI_READ_FRAME_MAX = (
    QPI_READ_RX_FIFO_WORDS - QPI_READ_DUMMY_WORDS - QPI_READ_LAG_WORDS
)

# MCU payload bytes per CE# pulse: the tighter of the two joined-FIFO
# ceilings. tCEM (max CE# low) would allow more at 20 MHz; the FIFOs cannot.
# Do not raise this past either bound without streaming after the SM is
# active(1): extra idle-SM put()s hang on write, and a longer read stalls
# mid-burst and silently drops nibbles. A longer CE# pulse still counts
# against tCEM.
MCU_QPI_PAYLOAD_MAX = min(
    MCU_QPI_WRITE_PAYLOAD_FIFO_MAX,
    MCU_QPI_READ_FRAME_MAX,
)

# Optional extra START high time. Capture does not depend on this: two GPIO
# writes already last much longer than two 66 MHz synchronizer clocks.
START_HOLD_US = 0
# Tight samples looking for DONE low. Not a timed wait; Python loops only.
BUSY_SAMPLE_TRIES = 8

# --- MCU QPI planner (tCEM: max CE# low; tPU: CE# high after power) ---

TCEM_US_DEFAULT = 4.0
TCEM_MARGIN_DEFAULT = 0.25
SCK_HZ_DEFAULT = 20_000_000
TPU_US = 150
# tRST min 50 ns and tCPH min 18 ns. MCU Python/GPIO between commands already
# exceeds both; do not insert a 1 us sleep as if it were the timing element.
EB_OVERHEAD_SCK = 14  # 2 cmd + 6 addr + 6 dummy
WR_OVERHEAD_SCK = 8  # 2 cmd + 6 addr
