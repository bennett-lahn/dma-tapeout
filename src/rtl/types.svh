package qspi_pkg;

typedef enum logic [3:0] {
   QSPI_IDLE
   ,CS_ON
   ,SEND_CMD_1
   ,SEND_CMD_2
   ,SEND_ADDR
   ,WAIT
   ,READ_DATA
   ,WRITE_DATA
   ,SCLK_OFF
   ,CS_OFF
} qspi_state_t;

// V1 ASIC QPI opcodes (D17)
typedef enum logic [7:0] {
   QSPI_CMD_WRITE     = 8'h02
   ,QSPI_CMD_FAST_READ = 8'hEB
} qspi_cmd_t;

// Returns required SCK wait cycles for a given cmd between addr and data phase
function automatic logic [2:0] qspi_wait_cycles(input qspi_cmd_t cmd);
   unique case (cmd)
      QSPI_CMD_FAST_READ: return 3'd6;
      QSPI_CMD_WRITE:     return 3'd0; // WRITE: no wait
   endcase
endfunction

// QPI address phase is 24 bits on the wire. APS6404L only uses A[22:0];
// addr[23] is unused (drive 0). Device select is device_sel, not addr[23].
typedef logic [23:0] qspi_addr_t;

// Which PSRAM device (CTRL_FLAGS device bits / device_sel). Not a pad CE# - engine maps this to ram_*_cs_n.
typedef enum logic {
   QSPI_PSRAM0 = 1'b0
   ,QSPI_PSRAM1 = 1'b1
} qspi_device_sel_t;

// Actual buffer depth N is module parameter DMA_BUF_DEPTH (default 1) in
// tt_um_lahnb_sgdma / sys_controller. When hardening, depth and max depth should be the same.
localparam int unsigned DMA_BUF_DEPTH_MAX = 8; // verification sweep ceiling (D20)
localparam int unsigned QPI_TCD_BYTES = 11; // Must be > 2 for qspi_engine correctness
localparam int unsigned QPI_MAX_BYTES =
   (DMA_BUF_DEPTH_MAX > QPI_TCD_BYTES) ? DMA_BUF_DEPTH_MAX : QPI_TCD_BYTES;

localparam int unsigned QPI_BYTE_LEN_W = $clog2(QPI_MAX_BYTES + 1);
// QPI payload uses 2 SCK cycles per byte; also covers addr/wait (6)
localparam int unsigned QPI_CYCLE_CNT_W = $clog2((2 * QPI_MAX_BYTES) + 1);

// QPI byte transfer length type (covers max possible transaction size)
typedef logic [QPI_BYTE_LEN_W-1:0] qpi_byte_len_t;

// Remaining payload nibbles, including zero (2 nibbles per byte)
// Capable of representing the maximum number of nibbles in a QPI transaction
typedef logic [QPI_CYCLE_CNT_W-1:0] qpi_payload_nibble_cnt_t;

endpackage : qspi_pkg

package sys_control_pkg;

import qspi_pkg::qspi_cmd_t;
import qspi_pkg::qspi_addr_t;
import qspi_pkg::qspi_device_sel_t;

typedef enum logic [2:0] {
   SYS_CTRL_IDLE
   ,NEW_FETCH
   ,FETCH
   ,NEW_OP
   ,READ
   ,WRITE
   ,UPDATE
   ,STALL
} sys_control_state_t;

// Working TCD: 88 bits = the 11-byte memory record. Packed LSB nibble is
// CTRL_FLAGS[3:0] reserved (latched; V1 control ignores it). next/dest/src
// device then quit occupy CTRL_FLAGS[7:4] (D24/D31). Packed LSB of the flags
// nibble is quit.
typedef struct packed {
   qspi_addr_t     src_ptr;
   qspi_addr_t     dest_ptr;
   logic     [7:0] transfer_len;
   qspi_addr_t     next_tcd;
   qspi_device_sel_t  next_tcd_device;
   qspi_device_sel_t  dest_device;
   qspi_device_sel_t  src_device;
   logic           quit;
   logic     [3:0] reserved;
} tcd_t;

// Hardware nibble count (22). Wire FETCH is 22 nibbles (11 bytes).
localparam int unsigned TCD_LEN = $bits(tcd_t) / 4;

endpackage: sys_control_pkg
