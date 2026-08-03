// qspi_engine_harness - leaf formal harness (L0 / engine jobs)
// Compile order: types.svh, qspi_engine.sv, this file, ../bind/qspi_engine_properties.sv
// Assumptions (M4): FA-RST-INIT, FA-RST-RUN, FA-REQ-LEGAL per docs/llm/verification/07-formal.md
// Does not import cocotb.

module qspi_engine_harness
   import qspi_pkg::*;
(
   input  logic clk
   ,input logic rst_n

   // Symbolic request inputs (FA-REQ-LEGAL constraints added in M4)
   ,input logic          txn_valid
   ,input qspi_cmd_t     cmd
   ,input qspi_addr_t    addr
   ,input qspi_device_sel_t device_sel
   ,input qpi_byte_len_t byte_len
   ,input logic    [3:0] wdata

   // Pad sample (unconstrained in baseline jobs)
   ,input logic    [3:0] sio_in
);

   logic          busy;
   logic    [3:0] rdata;
   logic          rdata_valid;
   logic          wdata_next;
   logic          sclk;
   logic          ram_a_cs_n;
   logic          ram_b_cs_n;
   logic    [3:0] sio_out;
   logic    [3:0] sio_oe;

   // TODO(M4): add reset_init / reset_recovery profiles and assumption audit IDs.

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

   // TODO(M4): bind qspi_engine_properties; prove FP-* engine rows from 07-formal.md.

endmodule
