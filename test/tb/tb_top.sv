// L1 cocotb wrapper: tt_um_lahnb_sgdma on a wired-tristate dual-PSRAM uio bus.
// Contract: docs/llm/verification/02-platform.md, 03-psram-model.md (L1 section),
// 04-timing-in-sim.md (bus resolution), ../../docs/llm/03-architecture.md
// (Bidirectional I/O ownership specification).
// No protocol logic here; host and PSRAM models are Python-side.
// DMA_BUF_DEPTH N is on-chip scratch bytes; tapeout default matches src/top.v (5).

`default_nettype none
`timescale 1ns / 1ps

module tb_top #(
   parameter int unsigned DMA_BUF_DEPTH = 5
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
   // MCU pass-through hooks. Host OE stays ungated in SV so negatives can drive
   // illegally. Python follows D26 (ASIC bus keeper while ~BUS_GNT) unless the
   // test is a negative.
   // -------------------------------------------------------------------------
   logic  [7:0] host_uio_drive;
   logic  [7:0] host_uio_oe;

   // -------------------------------------------------------------------------
   // Dual PSRAM model SIO hooks (cocotb drives; CE# is physical uio[6:7])
   // Driven by test/models/psram.py via attach_dual_psram().
   // -------------------------------------------------------------------------
   logic  [3:0] psram0_sio_drive;
   logic  [3:0] psram0_sio_oe;
   logic  [3:0] psram1_sio_drive;
   logic  [3:0] psram1_sio_oe;

   // -------------------------------------------------------------------------
   // Fault-injection driver (negative ownership tests only; inert at 0)
   // A set fault_uio_oe bit takes that pin away from the ASIC driver and drives
   // fault_uio_drive in its place, so a test can emulate ASIC misbehavior (both
   // RAM CE# low, flash CS low, SCK clocked while every device is deselected)
   // as a clean single-driver level instead of a wired X against the DUT.
   // On SIO the injector is an additional ASIC-side driver, which is how the
   // CHK-PIN-SIO-OWN negative case reproduces ASIC-versus-device dual drive
   // (including the equal-value case) during read data.
   // -------------------------------------------------------------------------
   logic  [7:0] fault_uio_drive;
   logic  [7:0] fault_uio_oe;

`include "tb_uio_bus.svh"

   // -------------------------------------------------------------------------
   // DUT
   // -------------------------------------------------------------------------
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

   // -------------------------------------------------------------------------
   // Waveform dump (test/Makefile moves dump.fst into RUN_DIR after the run;
   // WAVES=never compiles with WAVES_DISABLE to skip the dump entirely)
   // -------------------------------------------------------------------------
`ifndef WAVES_DISABLE
   initial begin
      $dumpfile("dump.fst");
      $dumpvars(0, tb_top);
      #1;
   end
`endif

endmodule

`default_nettype wire
