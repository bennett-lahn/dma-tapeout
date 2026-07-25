package qspi_pkg;

typedef enum logic [3:0] {
   IDLE
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
// addr[23] is unused (drive 0). Die select is die_sel, not addr[23].
typedef logic [23:0] qspi_addr_t;

// Which PSRAM die (ptr[23]). Not a pad CE# - engine maps this to ram_*_cs_n.
typedef enum logic {
   QSPI_PSRAM0 = 1'b0
   ,QSPI_PSRAM1 = 1'b1
} qspi_die_sel_t;

localparam int unsigned DMA_BUF_DEPTH = 1;   // N (D20)
localparam int unsigned QSPI_TCD_BYTES = 11; // Must be > 2 for qspi_engine correctness
localparam int unsigned QSPI_MAX_BYTES =
   (DMA_BUF_DEPTH > QSPI_TCD_BYTES) ? DMA_BUF_DEPTH : QSPI_TCD_BYTES;

localparam int unsigned QSPI_BYTE_LEN_W = $clog2(QSPI_MAX_BYTES + 1);
// READ counts nibbles (= 2 * bytes); also covers addr/wait (6)
localparam int unsigned QSPI_CYCLE_CNT_W = $clog2(2 * QSPI_MAX_BYTES + 1);

endpackage : qspi_pkg
