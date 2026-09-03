"""Reference-package architecture constants.

Leaf module: no imports of tcd, chain, cocotb, models, or monitors.
Overlapping TCD / opcode / dummy / head / buffer names are a mechanical copy
of ``firmware/constants.py`` (copies of ``src/types.svh`` and
``docs/llm/04-tcd-and-datapath.md``, never parsed from SystemVerilog).
When a shared number changes, edit both files.

Pin monitors and the PSRAM model both import protocol numbers from here so the
pin decode does not import the model.
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
READ_DUMMY_CYCLES = QPI_DUMMY_CYCLES

DIR_READ = "read"
DIR_WRITE = "write"
Q_PHASE = "Q-PHASE"

# APS6404L A[22:0]; same width as PTR_BITS.
PSRAM_ADDR_BITS = PTR_BITS
PSRAM_SIZE = 1 << PSRAM_ADDR_BITS  # 8 MiB
PSRAM_ADDR_MASK = PSRAM_SIZE - 1
PAGE_SIZE = 1024  # Linear Burst page, one crossing per CE# pulse
PSRAM_PAGE_SIZE = PAGE_SIZE

# --- Fixed head (D18) ---

HEAD_DEVICE = 0
HEAD_ADDRESS = 0x000000
DEVICES = (0, 1)
