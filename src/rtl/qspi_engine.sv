// qspi_engine - QPI master for dual APS6404L (D15/D17/D21)
// Consumer: descriptor FSM. Types: qspi.svh. Spec: docs/human/architecture/blocks/qspi-engine.md
//
// clk 66 MHz; SCLK = clk/2 when enabled, else 0. V1 cmds: 0xEB (+6 wait), 0x02.
// Transaction request is not latched. Send start only when ~busy.
//
// --- Ports ---
// clk          in   system clock
// rst_n        in   sync active-low reset
// txn_valid    in   1-clk start pulse; legal only when ~busy
// cmd          in   QSPI_CMD_FAST_READ / QSPI_CMD_WRITE (hold until ~busy)
// addr         in   24-bit QPI address phase; addr[23]=0); A[22:0] in addr[22:0]
// die_sel      in   QSPI_PSRAM0/1 -> which ram_*_cs_n
// byte_len     in   payload bytes this CE#; width QSPI_BYTE_LEN_W; hold until ~busy
// wdata        in   write nibble; must be valid on txn_valid; update after wdata_next
// busy         out  1 while not IDLE; start qualifier; ABORT/OE wait for 0
// rdata        out  last captured read nibble (held)
// rdata_valid  out  1-clk pulse with new rdata (rising SCK in READ_DATA)
// wdata_next   out  1-clk pulse on falling SCK in WRITE_DATA: present next wdata
// sio_in       in   pad SIO sample
// sclk         out  QSPI SCK
// ram_a_cs_n   out  RAM A CE# (active low); never both RAMs low
// ram_b_cs_n   out  RAM B CE#
// sio_out      out  pad SIO drive data
// sio_oe       out  pad SIO output enable (FSM grants uio_oe at top)

module qspi_engine
   import qspi_pkg::*;
(
   input   logic          clk
   ,input  logic          rst_n

   ,input  logic          txn_valid
   ,input  qspi_cmd_t     cmd
   ,input  qspi_addr_t    addr
   ,input  qspi_die_sel_t die_sel
   ,input  logic    [QSPI_BYTE_LEN_W-1:0] byte_len
   ,input  logic    [3:0] wdata

   ,output logic          busy
   ,output logic    [3:0] rdata
   ,output logic          rdata_valid
   ,output logic          wdata_next

   ,input  logic    [3:0] sio_in
   ,output logic          sclk
   ,output logic          ram_a_cs_n
   ,output logic          ram_b_cs_n
   ,output logic    [3:0] sio_out
   ,output logic    [3:0] sio_oe
);

qspi_state_t curr_state;
qspi_state_t next_state;

logic [QSPI_CYCLE_CNT_W-1:0] cycle_cnt; // 0-indexed count of # of sclk cycles in current state
logic sclk_d;
logic sclk_rising_edge;
logic sclk_falling_edge;
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
            if (qspi_wait_cycles(cmd) == 3'd0)
               next_state = (cmd == QSPI_CMD_WRITE) ? WRITE_DATA : READ_DATA;
            else
               next_state = WAIT;
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
         // 2 SCLK nibbles per byte
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

// QPI output control
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
            'd0: sio_out = addr[23:20];
            'd1: sio_out = addr[19:16];
            'd2: sio_out = addr[15:12];
            'd3: sio_out = addr[11:8];
            'd4: sio_out = addr[7:4];
            'd5: sio_out = addr[3:0];
         endcase
      end
      WRITE_DATA: begin
         sio_oe = {4{1'b1}};
         sio_out = wdata;
      end
   endcase
end

assign busy = (curr_state != IDLE);
assign wdata_next = (curr_state == WRITE_DATA) && sclk_falling_edge;

assign sclk_en = !(curr_state == IDLE
                || curr_state == CS_ON
                || curr_state == SCLK_OFF
                || curr_state == CS_OFF);
assign cs_n = (curr_state == IDLE || curr_state == CS_OFF);
assign ram_a_cs_n = (die_sel == QSPI_PSRAM0) ? cs_n : 1'b1;
assign ram_b_cs_n = (die_sel == QSPI_PSRAM1) ? cs_n : 1'b1;

assign sclk_rising_edge  =  sclk & ~sclk_d;
assign sclk_falling_edge = ~sclk &  sclk_d;

// SCLK = clk/2 while enabled; held low otherwise
always_ff @(posedge clk) begin
   if (~rst_n) begin
      sclk   <= 1'b0;
      sclk_d <= 1'b0;
   end else begin
      if (sclk_en)
         sclk <= ~sclk;
      else
         sclk <= 1'b0;
      sclk_d <= sclk;
   end
end

// Rising-SCLK sample into clk domain
always_ff @(posedge clk) begin
   if (~rst_n) begin
      rdata       <= '0;
      rdata_valid <= 1'b0;
   end else if (curr_state == READ_DATA && sclk_rising_edge) begin
      rdata       <= sio_in;
      rdata_valid <= 1'b1;
   end else begin
      rdata_valid <= 1'b0;
   end
end

// Cycle counter, next state
always_ff @(posedge clk) begin
   if (~rst_n) begin
      curr_state <= IDLE;
      cycle_cnt  <= '0;
   end else begin
      curr_state <= next_state;
      unique case (curr_state)
         IDLE, CS_ON, SCLK_OFF, CS_OFF:
            cycle_cnt <= '0;
         SEND_CMD_1, SEND_CMD_2, SEND_ADDR, READ_DATA, WRITE_DATA, WAIT: begin
            if (next_state == curr_state) begin
               if (sclk_rising_edge)
                  cycle_cnt <= cycle_cnt + 'd1;
            end else begin
               cycle_cnt <= '0;
            end
         end
      endcase
   end
end

endmodule
