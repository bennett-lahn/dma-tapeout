// QSPI engine - bit-level QPI master (D15/D17/D21)
// Consumer: descriptor FSM. Docs: docs/human/architecture/blocks/qspi-engine.md
// Types: qspi.svh (qspi_pkg)
//
// Pad side: SCK, RAM A/B CE#, SIO out/oe, SIO in. FSM muxes uio_oe grant.
// V1 opcodes: QSPI_CMD_FAST_READ (+6 dummy), QSPI_CMD_WRITE.
// clk 66 MHz (D16); SCK = clk/2 (toggle FF, idle low when disabled).
// Rising-edge RX: capture sio_in on sclk_rising_edge; pulse rdata_valid 1 clk.
// Engine owns CE# start (CS_ON) / end (SCLK_OFF then CS_OFF) + >=2 clk tCPH;
// never both RAM CS low; flash CS off at top level.
//
// --- FSM interface (D21) ---
// Request (engine does NOT latch; FSM holds until busy falls):
//   cmd, addr[23:0] (addr[23] unused/0; A[22:0] in addr[22:0]), die_sel,
//   byte_len[QSPI_BYTE_LEN_W-1:0]
// Beats (nibble-wide; exactly 2*byte_len SCK beats per payload):
//   txn_valid   FSM->eng  1-cycle start pulse (only when ~busy)
//   busy        eng->FSM  txn in flight; start qualifier; ABORT / OE wait for 0
//   rdata_valid eng->FSM  1-clk pulse with captured read nibble (rising SCK)
//   wdata_next  eng->FSM  1-clk pulse on falling SCK: present next wdata nibble
// No txn_ready. No wdone: engine ends write after 2*byte_len SCK, then pad/raise CE#.
//
// Read:  ~busy -> pulse txn_valid -> take rdata_valid pulses -> wait !busy
// Write: first wdata nibble on txn_valid cycle;
//        on wdata_next, present next wdata nibble for following rising SCK
//
// Rules:
//   1. Keep request stable from txn_valid until !busy (no engine latch)
//   2. On write, wdata holds first nibble when txn_valid asserts
//   3. Never stall SCK/CE# waiting on FSM
//   4. Pulse txn_valid only when ~busy
//

//
// TODO:

// new clock speed: 66 mhz

// need to make read/write data agnostic to clk, only change on sclk.

module qspi_engine
   import qspi_pkg::*;
(
   input   logic          clk
   ,input  logic          rst_n

   // Transaction request (FSM holds; engine does not latch)
   ,input  logic          txn_valid
   ,input  qspi_cmd_t     cmd
   ,input  qspi_addr_t    addr
   ,input  qspi_die_sel_t die_sel
   ,input  logic    [QSPI_BYTE_LEN_W-1:0] byte_len
   ,input  logic    [3:0] wdata

   // Handshake / data to FSM
   ,output logic          busy
   ,output logic    [3:0] rdata
   ,output logic          rdata_valid
   ,output logic          wdata_next

   // QSPI pads (engine drive values; FSM grants OE at top level module)
   ,input  logic    [3:0] sio_in
   ,output logic          sclk
   ,output logic          ram_a_cs_n
   ,output logic          ram_b_cs_n
   ,output logic    [3:0] sio_out
   ,output logic    [3:0] sio_oe
);

qspi_state_t curr_state;
logic [QSPI_CYCLE_CNT_W-1:0] cycle_cnt; // 0-indexed count of # of sclk cycles in current state
logic sclk_d;
logic sclk_rising_edge;
logic sclk_falling_edge;

qspi_state_t next_state;
logic cs_n;
logic sclk_en;

// Next state control
always_comb begin
   next_state = IDLE;
   unique case (curr_state)
      IDLE: begin
         if (txn_valid)
            next_state = CS_ON;
         else
            next_state = IDLE;
      end
      CS_ON: begin
         next_state = SEND_CMD_1;
      end
      SEND_CMD_1: begin
         if (cycle_cnt == 'd1)
            next_state = SEND_CMD_2;
         else
            next_state = SEND_CMD_1;
      end
      SEND_CMD_2: begin
         if (cycle_cnt == 'd1)
            next_state = SEND_ADDR;
         else
            next_state = SEND_CMD_2;
      end
      SEND_ADDR: begin
      if (cycle_cnt == 'd6) begin
         if (qspi_wait_cycles(cmd) == 3'd0) begin
            next_state = (cmd == QSPI_CMD_WRITE) ? WRITE_DATA : READ_DATA;
         end else begin
            next_state = WAIT;
         end
      end else begin
         next_state = SEND_ADDR;
      end
      end
      WAIT: begin
         if (cycle_cnt == qspi_wait_cycles(cmd))
            next_state = (cmd == QSPI_CMD_WRITE) ? WRITE_DATA : READ_DATA;
         else
            next_state = WAIT;
      end
      READ_DATA: begin
      // transfer 1 nibble per cycle, so need 2*byte_len cycles
         if (cycle_cnt == (byte_len << 1)) 
            next_state = SCLK_OFF;
         else
            next_state = READ_DATA;
      end
      WRITE_DATA: begin
         if (cycle_cnt == (byte_len << 1)) 
            next_state = SCLK_OFF;
         else
            next_state = WRITE_DATA;
      end
      SCLK_OFF: begin
         next_state = CS_OFF;
      end
      CS_OFF: begin
         next_state = IDLE;
      end
   endcase
end

// QSPI output control
always_comb begin
   sio_oe = '0;
   sio_out = '0;
   unique case (curr_state)
      IDLE, CS_ON, WAIT, READ_DATA, SCLK_OFF, CS_OFF: begin
         sio_oe = '0;
         sio_out = '0;
      end
      SEND_CMD_1: begin
         sio_oe = {4{1'b1}};
         sio_out = cmd[7:4];
      end
      SEND_CMD_2: begin
         sio_oe = {4{1'b1}};
         sio_out = cmd[3:0];
      end
      SEND_ADDR: begin
         sio_oe = {4{1'b1}};
         unique case (cycle_cnt)
            'd0:
               sio_out = addr[23:20];
            'd1:
               sio_out = addr[19:16];
            'd2:
               sio_out = addr[15:12];
            'd3:
               sio_out = addr[11:8];
            'd4:
               sio_out = addr[7:4];
            'd5: sio_out = addr[3:0];
         endcase
      end
      WRITE_DATA: begin
         sio_oe = {4{1'b1}};
         sio_out = wdata;
      end
   endcase
end

// Handshake Signals
assign busy = (curr_state == IDLE) ? 1'b0 : 1'b1;
assign wdata_next = (curr_state == WRITE_DATA && sclk_falling_edge) ? 1'b1 : 1'b0;

// QSPI pad control
assign sclk_en = (!(curr_state == IDLE || curr_state == CS_ON || curr_state == SCLK_OFF || curr_state == CS_OFF)) ? 1'b1 : 1'b0;
assign cs_n = (curr_state == IDLE || curr_state == CS_OFF) ? 1'b1 : 1'b0;
assign ram_a_cs_n = (die_sel == QSPI_PSRAM0) ? cs_n : 1'b1;
assign ram_b_cs_n = (die_sel == QSPI_PSRAM1) ? cs_n : 1'b1;

// Internal control signals
assign sclk_rising_edge = sclk & ~sclk_d;
assign sclk_falling_edge = ~sclk & sclk_d;

// Sclk clock generation
always_ff @(posedge clk) begin
   if (~rst_n) begin
      sclk <= 1'b0;
      sclk_d <= 1'b0;
   end else begin
      if (sclk_en)
         sclk <= ~sclk;
      else
         sclk <= 1'b0;
      sclk_d <= sclk;
   end
end

//
always_ff @(posedge clk) begin
   if (~rst_n) begin
      rdata <= '0;
      rdata_valid <= '0;
   end else begin
      if (curr_state == READ_DATA && sclk_rising_edge) begin
         rdata <= sio_in;
         rdata_valid <= 1'b1;
      end else begin
         rdata_valid <= 1'b0;
      end
   end
end

// Cycle counter, next state
always_ff @(posedge clk) begin
   if (~rst_n) begin
      curr_state <= IDLE;
      cycle_cnt <= 'd0;
   end else begin
      curr_state <= next_state;
      case (curr_state)
         IDLE, CS_ON, SCLK_OFF, CS_OFF: begin
            cycle_cnt <= 'd0;
         end
         SEND_CMD_1, SEND_CMD_2, SEND_ADDR, READ_DATA, WRITE_DATA, WAIT: begin
            if (next_state == curr_state) begin
               if (sclk_rising_edge)
                  cycle_cnt <= cycle_cnt + 1;
            end else begin
                  cycle_cnt <= 'd0;
            end
         end
         default:
            cycle_cnt <= 'd0;
      endcase
   end
end


endmodule
