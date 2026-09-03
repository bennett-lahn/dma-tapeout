// L2 cocotb wrapper: gate-level tt_um_lahnb_sgdma (TT GATES=yes flow).
// Contract: docs/llm/verification/02-platform.md, 09-gate-level-and-x.md.
// Netlist and IHP cell models are supplied by test/Makefile when GATES=yes.
// Same external environment as tb_top (tb_uio_bus.svh); no RTL hierarchy
// references. DMA_BUF_DEPTH N is on-chip scratch bytes; the flattened netlist
// is tapeout N=5 and this parameter does not resynthesize.

`default_nettype none
`timescale 1ns / 1ps

module tb_gl #(
   parameter int unsigned DMA_BUF_DEPTH = 5
);

   generate
      if (DMA_BUF_DEPTH != 5) begin : g_depth_mismatch
         initial $error(
            "tb_gl: DMA_BUF_DEPTH=%0d does not resynthesize the flattened N=5 netlist (tapeout N=5 on-chip scratch). Makefile must not pass -Ptb_gl.DMA_BUF_DEPTH.",
            DMA_BUF_DEPTH
         );
      end
   endgenerate

   // -------------------------------------------------------------------------
   // Clock / reset / TT dedicated pins (cocotb drives unless noted)
   // -------------------------------------------------------------------------
   logic        clk;
   logic        rst_n;
   logic        ena;
   logic  [7:0] ui_in;

   logic  [7:0] uo_out;
   logic  [7:0] uio_out;
   logic  [7:0] uio_oe;

   // -------------------------------------------------------------------------
   // MCU pass-through hooks. Host OE stays ungated in SV so negatives can drive
   // illegally. Python follows D26 (ASIC bus keeper while ~BUS_GNT) unless the
   // test is a negative.
   // -------------------------------------------------------------------------
   logic  [7:0] host_uio_drive;
   logic  [7:0] host_uio_oe;

   // -------------------------------------------------------------------------
   // Dual PSRAM model SIO hooks (cocotb drives)
   // -------------------------------------------------------------------------
   logic  [3:0] psram0_sio_drive;
   logic  [3:0] psram0_sio_oe;
   logic  [3:0] psram1_sio_drive;
   logic  [3:0] psram1_sio_oe;

   // Fault-injection driver for negative ownership tests (see tb_top).
   logic  [7:0] fault_uio_drive;
   logic  [7:0] fault_uio_oe;

`include "tb_uio_bus.svh"

   // -------------------------------------------------------------------------
   // Gate-level DUT
   // Makefile adds: -DGL_TEST -DFUNCTIONAL -DSIM, NETLIST, sg13g2_io/stdcell.
   // Flattened N=5 netlist: no DMA_BUF_DEPTH parameter on the instance.
   // Makefile must not pass -Ptb_gl.DMA_BUF_DEPTH as if that resynthesizes.
   // SDF remains blocked; this wrapper has no $sdf_annotate. A zero-delay
   // functional GL run is not an SDF pass (09-gate-level-and-x.md).
   // -------------------------------------------------------------------------
`ifdef GL_TEST
   tt_um_lahnb_sgdma dut (
      .ui_in   (ui_in)
      ,.uo_out  (uo_out)
      ,.uio_in  (uio_in)
      ,.uio_out (uio_out)
      ,.uio_oe  (uio_oe)
      ,.ena     (ena)
      ,.clk     (clk)
      ,.rst_n   (rst_n)
   );
`else
   initial begin
      $fatal(1, "tb_gl requires GL_TEST (use make LEVEL=gl GATES=yes)");
   end
`endif

   // -------------------------------------------------------------------------
   // Waveform dump (L2 default WAVES=always per policy)
   // -------------------------------------------------------------------------
`ifndef WAVES_DISABLE
   initial begin
      $dumpfile("dump.fst");
      $dumpvars(0, tb_gl);
      #1;
   end
`endif

endmodule

`default_nettype wire
