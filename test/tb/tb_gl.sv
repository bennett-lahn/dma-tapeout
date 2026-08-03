// L2 cocotb wrapper: gate-level tt_um_lahnb_sgdma (TT GATES=yes flow).
// Contract: docs/llm/verification/02-platform.md, 09-gate-level-and-x.md.
// Netlist and IHP cell models are supplied by test/Makefile when GATES=yes.
// Same external environment as tb_top; no RTL hierarchy references.

`default_nettype none
`timescale 1ns / 1ps

module tb_gl #(
   parameter int unsigned DMA_BUF_DEPTH = 1
);

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
   // MCU pass-through hooks when BUS_GNT (cocotb drives)
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

   logic  [7:0] uio_in;
   logic  [7:0] resolved_uio;

   // -------------------------------------------------------------------------
   // Shared uio bus resolution (identical placeholder to tb_top)
   // TODO(M6): verify resolved nets remain sufficient when internal hierarchy
   //           is flattened; L2 pass criteria are top-observable only.
   // -------------------------------------------------------------------------
   function automatic logic resolve_uio_bit(
      input logic        asic_oe
      ,input logic        asic_out
      ,input logic        host_oe
      ,input logic        host_drv
      ,input logic        psram0_oe
      ,input logic        psram0_drv
      ,input logic        psram1_oe
      ,input logic        psram1_drv
      ,input logic        idle_default
   );
      if (asic_oe)
         return asic_out;
      if (host_oe)
         return host_drv;
      if (psram0_oe)
         return psram0_drv;
      if (psram1_oe)
         return psram1_drv;
      return idle_default;
   endfunction

   assign resolved_uio[0] = resolve_uio_bit(
      uio_oe[0], uio_out[0],
      host_uio_oe[0], host_uio_drive[0],
      1'b0, 1'b0,
      1'b0, 1'b0,
      1'b1
   );
   assign resolved_uio[1] = resolve_uio_bit(
      uio_oe[1], uio_out[1],
      host_uio_oe[1], host_uio_drive[1],
      psram0_sio_oe[0], psram0_sio_drive[0],
      psram1_sio_oe[0], psram1_sio_drive[0],
      1'b0
   );
   assign resolved_uio[2] = resolve_uio_bit(
      uio_oe[2], uio_out[2],
      host_uio_oe[2], host_uio_drive[2],
      psram0_sio_oe[1], psram0_sio_drive[1],
      psram1_sio_oe[1], psram1_sio_drive[1],
      1'b0
   );
   assign resolved_uio[3] = resolve_uio_bit(
      uio_oe[3], uio_out[3],
      host_uio_oe[3], host_uio_drive[3],
      1'b0, 1'b0,
      1'b0, 1'b0,
      1'b0
   );
   assign resolved_uio[4] = resolve_uio_bit(
      uio_oe[4], uio_out[4],
      host_uio_oe[4], host_uio_drive[4],
      psram0_sio_oe[2], psram0_sio_drive[2],
      psram1_sio_oe[2], psram1_sio_drive[2],
      1'b0
   );
   assign resolved_uio[5] = resolve_uio_bit(
      uio_oe[5], uio_out[5],
      host_uio_oe[5], host_uio_drive[5],
      psram0_sio_oe[3], psram0_sio_drive[3],
      psram1_sio_oe[3], psram1_sio_drive[3],
      1'b0
   );
   assign resolved_uio[6] = resolve_uio_bit(
      uio_oe[6], uio_out[6],
      host_uio_oe[6], host_uio_drive[6],
      1'b0, 1'b0,
      1'b0, 1'b0,
      1'b1
   );
   assign resolved_uio[7] = resolve_uio_bit(
      uio_oe[7], uio_out[7],
      host_uio_oe[7], host_uio_drive[7],
      1'b0, 1'b0,
      1'b0, 1'b0,
      1'b1
   );

   assign uio_in = resolved_uio;

   // -------------------------------------------------------------------------
   // Scalar pin aliases for the Python PSRAM models (identical to tb_top).
   // -------------------------------------------------------------------------
   wire psram_sck   = resolved_uio[3];
   wire psram0_ce_n = resolved_uio[6];
   wire psram1_ce_n = resolved_uio[7];

   // -------------------------------------------------------------------------
   // Gate-level DUT
   // Makefile adds: -DGL_TEST -DFUNCTIONAL -DSIM, NETLIST, sg13g2_io/stdcell.
   // TODO(M6): confirm synthesized top instance name matches tt_um_lahnb_sgdma.
   // Do not invent a netlist here; test/Makefile supplies $(NETLIST).
   // -------------------------------------------------------------------------
`ifdef GL_TEST
   tt_um_lahnb_sgdma #(
      .DMA_BUF_DEPTH(DMA_BUF_DEPTH)
   ) dut (
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
   // Compile-time guard: tb_gl is intended for GATES=yes / LEVEL=gl only.
   // A bare compile without GL_TEST will fail here until Makefile defines are set.
   initial begin
      $fatal(1, "tb_gl requires GL_TEST (use make LEVEL=gl GATES=yes)");
   end
`endif

   // -------------------------------------------------------------------------
   // Waveform dump (L2 default WAVES=always per policy)
   // -------------------------------------------------------------------------
   initial begin
      $dumpfile("dump.fst");
      $dumpvars(0, tb_gl);
      #1;
   end

endmodule

`default_nettype wire
