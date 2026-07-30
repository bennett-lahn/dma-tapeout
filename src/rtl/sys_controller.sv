// sys_controller - host / mode control (START, DONE, BUS_REQ/BUS_GNT)
// Spec: docs/human/architecture/blocks/host-interface.md (D14/D18/D22/D23)
//       docs/human/architecture/blocks/descriptor-fsm.md
//
// --- Host pins (TT map; inputs qualified by top level into clk) ---
// ui_in[0]  START     - one-clk pulse after top-level sync and rising-edge detect
// ui_in[1]  reserved (ABORT removed; kill via rst_n)
// ui_in[2]  BUS_REQ   - MCU wants bidirectional uio
// uo_out[0] DONE      - integrated controller idle
// uo_out[1] BUS_GNT   - MCU has been granted uio control
//
// --- Integrated host and descriptor control ---
// start           in   one-clk command pulse; accepted only in IDLE with ~bus_req
// bus_req         in   synchronized host request; pauses before the next QPI txn
// qspi_busy       in   QPI txn in flight; BUS_GNT waits for clear (atomic)
// qspi_txn_valid  out  one-clk QPI request pulse; suppressed while bus_req is high
// done            out  registered; controller is idle, including an idle BUS_REQ stall
// bus_gnt         out  registered; controller has yielded the shared bus to the MCU
//
// Rules (D22/D23):
// 1. MCU drives uio only while BUS_GNT.
// 2. BUS_REQ has priority over DMA; finish current QPI txn (atomic), then grant.
// 3. START accepted only in IDLE with ~BUS_REQ.
// 4. No soft abort; assert rst_n to kill a runaway DMA.
// 5. QUIT TCD → IDLE; next START fetches 0x000000 / PSRAM0 again.
// 6. qspi_wdata_next asserts iff another nibble is required by the active write;
//    next nibble must be on qspi_wdata before the next clk (same-cycle comb).

module sys_controller
   import sys_control_pkg::*;
   import qspi_pkg::qspi_cmd_t;
   import qspi_pkg::QSPI_CMD_FAST_READ;
   import qspi_pkg::QSPI_CMD_WRITE;
   import qspi_pkg::qspi_addr_t;
   import qspi_pkg::qspi_device_sel_t;
   import qspi_pkg::qpi_byte_len_t;
   import qspi_pkg::qpi_payload_nibble_cnt_t;
   import qspi_pkg::DMA_BUF_DEPTH_MAX;
   import qspi_pkg::QPI_TCD_BYTES;
   import qspi_pkg::QSPI_PSRAM0;
#(
   parameter int unsigned DMA_BUF_DEPTH = 1 // N (D20); V1 tapeout = 1
)(
   input   logic       clk
   ,input  logic       rst_n

   // Host pins
   ,input  logic       start   // one-clk pulse after top-level sync and edge detect
   ,input  logic       bus_req // post top-level sync (async MCU ui_in)
   ,output logic       done
   ,output logic       bus_gnt

   // QSPI
   ,input  logic             qspi_busy
   ,input  logic             qspi_rdata_valid
   ,input  logic       [3:0] qspi_rdata
   ,input  logic             qspi_wdata_next
   ,output logic             qspi_txn_valid
   ,output qspi_cmd_t        qspi_cmd
   ,output qspi_addr_t       qspi_addr
   ,output qspi_device_sel_t    qspi_device_sel
   ,output qpi_byte_len_t    qspi_byte_len
   ,output logic       [3:0] qspi_wdata
);

// Elaboration check: package qpi_* widths assume N <= DMA_BUF_DEPTH_MAX.
generate
   if (DMA_BUF_DEPTH < 1 || DMA_BUF_DEPTH > DMA_BUF_DEPTH_MAX)
      $error("sys_controller: DMA_BUF_DEPTH must be in 1 .. DMA_BUF_DEPTH_MAX");
endgenerate

sys_control_state_t curr_state;
sys_control_state_t next_state;
sys_control_state_t stalled_state;
sys_control_state_t stall_origin;
tcd_t task_ctrl_desc;
qspi_addr_t active_fetch_addr;
qspi_device_sel_t active_fetch_device;
logic write_pending;
logic [8*DMA_BUF_DEPTH-1:0] data_buffer;

// Nibbles remaining in the current TCD or data payload
qpi_payload_nibble_cnt_t data_cnt;

localparam logic [7:0] DMA_BUF_DEPTH_VALUE = 8'(DMA_BUF_DEPTH);

assign qspi_txn_valid = (curr_state == NEW_OP || curr_state == NEW_FETCH) && (next_state == READ || next_state == WRITE || next_state == FETCH);

// Drive SPI device select
always_comb begin
   qspi_device_sel = QSPI_PSRAM0;
   unique case (curr_state)
      NEW_FETCH:           qspi_device_sel = task_ctrl_desc.next_tcd_device;
      FETCH:               qspi_device_sel = active_fetch_device;
      NEW_OP: begin
         if (write_pending)
            qspi_device_sel = task_ctrl_desc.dest_device;
         else
            qspi_device_sel = task_ctrl_desc.src_device;
      end
      READ:                qspi_device_sel = task_ctrl_desc.src_device;
      WRITE:               qspi_device_sel = task_ctrl_desc.dest_device;
      SYS_CTRL_IDLE, UPDATE, STALL: qspi_device_sel = QSPI_PSRAM0;
   endcase
end

// Drive SPI transaction length
always_comb begin
   qspi_byte_len = '0;
   unique case (curr_state)
      NEW_FETCH, FETCH:
         qspi_byte_len = qpi_byte_len_t'(QPI_TCD_BYTES);
      NEW_OP, READ, WRITE: begin
         if (task_ctrl_desc.transfer_len > DMA_BUF_DEPTH_VALUE)
            qspi_byte_len = qpi_byte_len_t'(DMA_BUF_DEPTH);
         else
            qspi_byte_len = qpi_byte_len_t'(task_ctrl_desc.transfer_len);
      end
      SYS_CTRL_IDLE, UPDATE, STALL: qspi_byte_len = '0;
   endcase
end

// Drive current command
always_comb begin
   if (write_pending)
      qspi_cmd = QSPI_CMD_WRITE;
   else
      qspi_cmd = QSPI_CMD_FAST_READ;
end

// Drive current address
always_comb begin
   qspi_addr = '0;
   unique case (curr_state)
      NEW_FETCH: qspi_addr = task_ctrl_desc.next_tcd;
      FETCH: qspi_addr = active_fetch_addr;
      NEW_OP: begin
         if (write_pending)
            qspi_addr = task_ctrl_desc.dest_ptr;
         else
            qspi_addr = task_ctrl_desc.src_ptr;
      end
      READ: qspi_addr = task_ctrl_desc.src_ptr;
      WRITE: qspi_addr = task_ctrl_desc.dest_ptr;
      SYS_CTRL_IDLE, UPDATE, STALL: qspi_addr = '0;
   endcase
end

// qspi_wdata logic
always_comb begin
   if (curr_state != WRITE && next_state == WRITE) begin
      // Preset wdata to first nibble in previous read. For reads less than DMA_BUF_DEPTH_VALUE,
      // the MSB of buffer are unused first
      qspi_wdata = data_buffer[(8 * qspi_byte_len) - 1 -: 4];
   end else if (curr_state == WRITE) begin
      if (qspi_wdata_next)
         // Fetch wdata a cycle before data_cnt updates
         qspi_wdata = data_buffer[(4 * (data_cnt - 1)) - 1 -: 4];
      else
         qspi_wdata = data_buffer[(4 * data_cnt) - 1 -: 4];
   end else begin
      qspi_wdata = '0;
   end
end

// FSM states

// IDLE: Transition to fetch when start is asserted
// NEW_FETCH: Prepare for next TCD read. Start QSPI transaction
// NEW_OP: Start read/write transaction, depending on TCD.
// FETCH: Execute TCD read. Transition to NEW_OP when TCD read is complete.
// READ: Execute read. Transition to UPDATE when read is complete.
// WRITE: Execute write. Transition to UPDATE when write is complete.
// UPDATE: Update TCD specs. Transfer to NEW_OP/NEW_FETCH depending on updated values
// STALL: Give MCU I/O priority until request is complete. Only transition to from idle, NEW_FETCH,
// NEW_OP, UPDATE

// State control
always_comb begin
   unique case (curr_state)
      SYS_CTRL_IDLE: begin
         if (bus_req)
            next_state = STALL;
         else if (start)
            next_state = NEW_FETCH;
         else
            next_state = SYS_CTRL_IDLE;
      end
      NEW_FETCH: begin
         if (bus_req)
            next_state = STALL;
         else if (~qspi_busy)
            next_state = FETCH;
         else
            next_state = NEW_FETCH;
      end
      FETCH: begin
         if (~qspi_busy)
            next_state = NEW_OP;
         else
            next_state = FETCH;
      end
      NEW_OP: begin
         if (bus_req)
            next_state = STALL;
         else if (task_ctrl_desc.quit)
            next_state = SYS_CTRL_IDLE;
         else if (task_ctrl_desc.transfer_len == 'd0)
            next_state = NEW_FETCH;
         else if (write_pending)
            next_state = WRITE;
         else
            next_state = READ;
      end
      READ: begin
         if (~qspi_busy)
            next_state = NEW_OP;
         else
            next_state = READ;
      end
      WRITE: begin
         if (~qspi_busy)
            next_state = UPDATE;
         else
            next_state = WRITE;
      end
      UPDATE: begin
         if (bus_req)
            next_state = STALL;
         else if (stalled_state == UPDATE) begin // transfer_len has already been updated
            if (task_ctrl_desc.transfer_len == '0)
               next_state = NEW_FETCH;
            else
               next_state = NEW_OP;
         end else if (task_ctrl_desc.transfer_len <= DMA_BUF_DEPTH_VALUE)
            next_state = NEW_FETCH;
         else
            next_state = NEW_OP;
      end
      STALL: begin
         if (bus_req)
            next_state = STALL;
         else
            next_state = stalled_state;
      end
   endcase
end

// Data read / indexing and TCD management
always_ff @(posedge clk) begin
   if (~rst_n) begin
      data_cnt <= '0;
      task_ctrl_desc <= '0;
      write_pending <= '0;
      data_buffer <= '0;
      active_fetch_addr <= '0;
      active_fetch_device <= QSPI_PSRAM0;
   end else begin
      unique case (curr_state)
         SYS_CTRL_IDLE, STALL: begin
            data_cnt <= '0;
         end
         NEW_FETCH: begin 
            data_cnt <= qpi_payload_nibble_cnt_t'(TCD_LEN);
            active_fetch_addr <= task_ctrl_desc.next_tcd;
            active_fetch_device <= task_ctrl_desc.next_tcd_device;
         end
         NEW_OP: begin
            if (task_ctrl_desc.quit) begin
               // Reset TCD register so it points to address 0 of PSRAM 0
               task_ctrl_desc.next_tcd <= '0;
               task_ctrl_desc.next_tcd_device <= QSPI_PSRAM0;
            end else if (task_ctrl_desc.transfer_len < DMA_BUF_DEPTH_VALUE) begin
               data_cnt <= qpi_payload_nibble_cnt_t'(2 * task_ctrl_desc.transfer_len);
            end else begin
               data_cnt <= qpi_payload_nibble_cnt_t'(2 * DMA_BUF_DEPTH);
            end
         end
         FETCH: begin
            if (qspi_rdata_valid && data_cnt != '0) begin
               task_ctrl_desc[(4*data_cnt)-1 -: 4] <= qspi_rdata;
               data_cnt <= data_cnt - 'd1;
            end
         end
         READ: begin
            if (qspi_rdata_valid && data_cnt != '0) begin
               data_buffer[(4*data_cnt)-1 -: 4] <= qspi_rdata;
               data_cnt <= data_cnt - 'd1;
            end
            if (next_state != READ)
               write_pending <= 1'b1;
         end
         WRITE: begin
            if (qspi_wdata_next)
               data_cnt <= data_cnt - 'd1;
            if (next_state != WRITE)
               write_pending <= 1'b0;
         end
         UPDATE: begin
            data_cnt <= '0;
            if (stalled_state != UPDATE) begin
               if (task_ctrl_desc.transfer_len > DMA_BUF_DEPTH_VALUE) begin
                  task_ctrl_desc.transfer_len <= task_ctrl_desc.transfer_len - DMA_BUF_DEPTH_VALUE;
                  task_ctrl_desc.src_ptr <= task_ctrl_desc.src_ptr + DMA_BUF_DEPTH_VALUE;
                  task_ctrl_desc.dest_ptr <= task_ctrl_desc.dest_ptr + DMA_BUF_DEPTH_VALUE;
               end else begin
                  task_ctrl_desc.transfer_len <= '0;
                  // Do not update src_ptr / dest_ptr. The transfer is complete, so updated values
                  // are not needed.
               end
            end
         end
      endcase
   end
end

// State a stall resumes into: the state being left, or the retained origin while stalled.
assign stall_origin = (curr_state == STALL) ? stalled_state : curr_state;

// Register done/bus_gnt to prevent signal glitches propagating to output I/O.
always_ff @(posedge clk) begin
   if (~rst_n) begin
      done <= 1'b1;
      bus_gnt <= 1'b0;
   end else begin
      bus_gnt <= (next_state == STALL);
      done <= (next_state == SYS_CTRL_IDLE)
              || (next_state == STALL && stall_origin == SYS_CTRL_IDLE);
   end
end

// State control
always_ff @(posedge clk) begin
   if (~rst_n) begin
      curr_state <= SYS_CTRL_IDLE;
      stalled_state <= SYS_CTRL_IDLE;
   end else begin
      if (curr_state != STALL && next_state == STALL)
         stalled_state <= curr_state;
      else if (curr_state != STALL)
         stalled_state <= SYS_CTRL_IDLE;
      curr_state <= next_state;
   end
end

endmodule

// === Testing ===
//
// Assertions:
// - data_cnt never exceeds TCD_LEN during FETCH or 2*DMA_BUF_DEPTH during data
//   movement, never underflows, and is nonzero before every dynamic part-select.
// - qspi_txn_valid is a one-cycle pulse and implies !qspi_busy && !bus_req.
// - cmd, addr, device_sel, and byte_len obey the selected stability contract.
// - BUS_GNT implies the ASIC has released uio_oe and no transaction is active.
// - The two RAM chip selects are never active together.
// - Requested address should never exceed PSRAM address limits (assuming TCD/firmware is correct)
// - The highest bit of addresses should always be 0.
// - During WRITE, data_cnt >= 1, and qspi_wdata_next |-> data_cnt >= 2
//
// Directed tests:
// - Decode a known 11-byte descriptor to prove byte/nibble ordering and flags.
// - Exercise lengths 0, 1, N-1, N, N+1, and 255 where those values are distinct;
//   check the final partial chunk, exact nibble sequence, pointer increments when
//   data remains, final-pointer don't-care behavior, and TRANSFER_LEN decrement.
// - Check same-device and both cross-device copy directions, plus NEXT_DEVICE chaining.
// - Assert BUS_REQ in IDLE and during FETCH, READ, WRITE, NEW_OP, NEW_FETCH, and
//   UPDATE; finish an active transaction atomically, grant only after release,
//   then resume without repeating or skipping an UPDATE.
// - Verify START is ignored while active and a held START cannot cause an
//   unintended restart under the chosen top-level pulse contract.
// - Fetch a QUIT TCD without issuing a data transaction, enter IDLE/DONE, then
//   verify the next START fetches address 0 on PSRAM0 regardless of stale NEXT.
// - Check exactly 2*byte_len rdata_valid pulses per read and
//   2*byte_len-1 wdata_next pulses per write. Ensure wdata_next never asserts
//   after the final nibble or outside an active write transaction.
