"""Sim-only shared constants.

Leaf module: no imports of models, monitors, or cocotb. Host pin indices,
DONE/BUS_GNT masks, dispose vocabulary, directed fixture addresses, and RTL
FSM encodings live here. Architecture opcodes / TCD layout live in
``reference.constants``.
"""

# --- Host pins (ui_in / uo_out). Indices vs masks are not interchangeable. ---

START_BIT = 0  # ui_in bit index
BUS_REQ_BIT = 2  # ui_in bit index
DONE_MASK = 0x1  # uo_out[0] mask; not the firmware DONE_BIT index
BUS_GNT_MASK = 0x2  # uo_out[1] mask; not a uio bit index

# Physical ``uio`` bit map (src/top.v / test/tb/tb_top.sv).
UIO_FLASH_CS_BIT = 0
UIO_SCK_BIT = 3
UIO_PSRAM_CE_BITS = (6, 7)
SIO_UIO_BITS = (1, 2, 4, 5)

SCK_PERIOD_NS = 30.0  # D16: SCK = clk/2 at the 66 MHz clk target

DONE_TIMEOUT_NS = 100_000

# --- Clock / reset defaults ---

DEFAULT_CLOCK_PERIOD_NS = 10
DEFAULT_RESET_CYCLES = 5
STATE_TIMEOUT_CYCLES = 50_000
GRANT_TIMEOUT_CYCLES = 2_000

# --- Dispose / checker results (01-strategy.md status vocabulary) ---

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_NA = "na"
RESULT_BLOCKED = "blocked"

FORBID = "forbid"
REVIEW = "review"
REQUIRE = "require"

# --- Directed fixture addresses (smoke / reset / reference unit tests) ---

FILL = 0x00
TCD_HEAD_ADDR = 0x000000
NEXT_TCD_ADDR = 0x000020
QUIT_ADDR = NEXT_TCD_ADDR
SRC_ADDR = 0x000100
DST_ADDR = 0x000200
SRC_BYTE = 0xA5
DST_SENTINEL = 0x00

# Directed CE# gap used by timing tests; greater than datasheet tCPH (18 ns).
LEGAL_GAP_NS = 25.0

# --- Injection Determinism streams ---

STREAM_START = "start"
STREAM_BUS_REQ = "bus_req"
STREAM_RESET = "reset"

# --- RTL FSM encodings (types.svh copies; not parsed from SystemVerilog) ---

SYS_CONTROL_IDLE = 0
SYS_CONTROL_NEW_FETCH = 1
SYS_CONTROL_FETCH = 2
SYS_CONTROL_NEW_OP = 3
SYS_CONTROL_READ = 4
SYS_CONTROL_WRITE = 5
SYS_CONTROL_UPDATE = 6
SYS_CONTROL_STALL = 7

SYS_CONTROL_STATES = {
    SYS_CONTROL_IDLE: "SYS_CTRL_IDLE",
    SYS_CONTROL_NEW_FETCH: "NEW_FETCH",
    SYS_CONTROL_FETCH: "FETCH",
    SYS_CONTROL_NEW_OP: "NEW_OP",
    SYS_CONTROL_READ: "READ",
    SYS_CONTROL_WRITE: "WRITE",
    SYS_CONTROL_UPDATE: "UPDATE",
    SYS_CONTROL_STALL: "STALL",
}

QSPI_ENGINE_STATES = {
    0: "QSPI_IDLE",
    1: "CS_ON",
    2: "SEND_CMD_1",
    3: "SEND_CMD_2",
    4: "SEND_ADDR",
    5: "WAIT",
    6: "READ_DATA",
    7: "WRITE_DATA",
    8: "SCLK_OFF",
    9: "CS_OFF",
}
