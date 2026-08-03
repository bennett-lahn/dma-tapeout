// L0 cocotb wrapper: qspi_engine + one PSRAM model attachment hooks.
// Contract: docs/llm/verification/02-platform.md, 03-psram-model.md (L0 section).
// No protocol logic here; Python models and tests drive/monitor ports.

`default_nettype none
`timescale 1ns / 1ps

module tb_engine;

   import qspi_pkg::*;

   // -------------------------------------------------------------------------
   // Clock / reset (cocotb drives)
   // -------------------------------------------------------------------------
   logic clk;
   logic rst_n;

   // -------------------------------------------------------------------------
   // Direct engine stimulus (cocotb drives)
   // -------------------------------------------------------------------------
   logic          txn_valid;
   qspi_cmd_t     cmd;
   qspi_addr_t    addr;
   qspi_device_sel_t device_sel;
   qpi_byte_len_t byte_len;
   logic    [3:0] wdata;

   // -------------------------------------------------------------------------
   // Engine status (cocotb monitors)
   // -------------------------------------------------------------------------
   logic          busy;
   logic    [3:0] rdata;
   logic          rdata_valid;
   logic          wdata_next;

   // -------------------------------------------------------------------------
   // Pad / bus visibility (cocotb monitors)
   // -------------------------------------------------------------------------
   logic          sclk;
   logic          ram_a_cs_n;
   logic          ram_b_cs_n;
   logic    [3:0] sio_out;
   logic    [3:0] sio_oe;

   // -------------------------------------------------------------------------
   // PSRAM model hooks (cocotb drives; one selected device per transaction)
   // TODO(M1): tie psram_sio_* from models/psram.py via timing layer return plane.
   // -------------------------------------------------------------------------
   logic    [3:0] psram_sio_drive;
   logic    [3:0] psram_sio_oe;

   // Resolved SIO sample presented to DUT sio_in.
   logic    [3:0] sio_in;
   logic    [3:0] resolved_sio;

   // -------------------------------------------------------------------------
   // Shared-bus resolution (L0: ASIC SIO OE vs PSRAM model drive)
   // TODO(M1): high-Z / listen-phase behavior when neither side drives.
   // -------------------------------------------------------------------------
   genvar gi;
   generate
      for (gi = 0; gi < 4; gi = gi + 1) begin : gen_sio_resolve
         assign resolved_sio[gi] = sio_oe[gi] ? sio_out[gi]
                                 : (psram_sio_oe[gi] ? psram_sio_drive[gi] : 1'b0);
         assign sio_in[gi] = resolved_sio[gi];
      end
   endgenerate

   // -------------------------------------------------------------------------
   // DUT
   // -------------------------------------------------------------------------
   qspi_engine dut (
      .clk        (clk)
      ,.rst_n      (rst_n)
      ,.txn_valid  (txn_valid)
      ,.cmd        (cmd)
      ,.addr       (addr)
      ,.device_sel (device_sel)
      ,.byte_len   (byte_len)
      ,.wdata      (wdata)
      ,.busy       (busy)
      ,.rdata      (rdata)
      ,.rdata_valid(rdata_valid)
      ,.wdata_next (wdata_next)
      ,.sio_in     (sio_in)
      ,.sclk       (sclk)
      ,.ram_a_cs_n (ram_a_cs_n)
      ,.ram_b_cs_n (ram_b_cs_n)
      ,.sio_out    (sio_out)
      ,.sio_oe     (sio_oe)
   );

   // -------------------------------------------------------------------------
   // Waveform dump (FST; path matches test/Makefile waves target)
   // -------------------------------------------------------------------------
   initial begin
      $dumpfile("dump.fst");
      $dumpvars(0, tb_engine);
      #1;
   end

endmodule

`default_nettype wire
