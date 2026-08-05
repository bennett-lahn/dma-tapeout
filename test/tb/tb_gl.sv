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

   // Fault-injection driver for negative ownership tests (see tb_top).
   logic  [7:0] fault_uio_drive;
   logic  [7:0] fault_uio_oe;

   initial begin
      host_uio_drive  = '0;
      host_uio_oe     = '0;
      fault_uio_drive = '0;
      fault_uio_oe    = '0;
   end

   // -------------------------------------------------------------------------
   // Shared uio bus (physical plane), identical to tb_top: wired tristate with
   // board pull-ups on the three CS nets only.
   // TODO(M6): verify resolved nets remain sufficient when internal hierarchy
   //           is flattened; L2 pass criteria are top-observable only.
   // -------------------------------------------------------------------------
   wire   [7:0] uio_bus;
   wire   [7:0] asic_uio_oe = uio_oe & ~fault_uio_oe;

   genvar gi;
   generate
      for (gi = 0; gi < 8; gi = gi + 1) begin : gen_uio_drivers
         assign uio_bus[gi] = asic_uio_oe[gi]  ? uio_out[gi]         : 1'bz;
         assign uio_bus[gi] = fault_uio_oe[gi] ? fault_uio_drive[gi] : 1'bz;
         assign uio_bus[gi] = host_uio_oe[gi]  ? host_uio_drive[gi]  : 1'bz;
      end
   endgenerate

   assign uio_bus[1] = psram0_sio_oe[0] ? psram0_sio_drive[0] : 1'bz;
   assign uio_bus[2] = psram0_sio_oe[1] ? psram0_sio_drive[1] : 1'bz;
   assign uio_bus[4] = psram0_sio_oe[2] ? psram0_sio_drive[2] : 1'bz;
   assign uio_bus[5] = psram0_sio_oe[3] ? psram0_sio_drive[3] : 1'bz;

   assign uio_bus[1] = psram1_sio_oe[0] ? psram1_sio_drive[0] : 1'bz;
   assign uio_bus[2] = psram1_sio_oe[1] ? psram1_sio_drive[1] : 1'bz;
   assign uio_bus[4] = psram1_sio_oe[2] ? psram1_sio_drive[2] : 1'bz;
   assign uio_bus[5] = psram1_sio_oe[3] ? psram1_sio_drive[3] : 1'bz;

   pullup pu_flash_cs (uio_bus[0]);
   pullup pu_ram_a_cs (uio_bus[6]);
   pullup pu_ram_b_cs (uio_bus[7]);

   wire   [7:0] uio_in = uio_bus;

   // Model plane: wrapper idle value for z only (see tb_top).
   localparam logic [7:0] BUS_IDLE_LEVEL = 8'b1100_0001; // CS high, SIO/SCK low

   wire   [7:0] resolved_uio;
   generate
      for (gi = 0; gi < 8; gi = gi + 1) begin : gen_model_plane
         assign resolved_uio[gi] = (uio_bus[gi] === 1'bz) ? BUS_IDLE_LEVEL[gi]
                                                          : uio_bus[gi];
      end
   endgenerate

   // -------------------------------------------------------------------------
   // Scalar pin aliases for the Python PSRAM models (identical to tb_top).
   // -------------------------------------------------------------------------
   wire psram_sck   = resolved_uio[3];
   wire psram0_ce_n = resolved_uio[6];
   wire psram1_ce_n = resolved_uio[7];

   // -------------------------------------------------------------------------
   // Ownership view for test/monitors/qspi.py (identical names to tb_top).
   // -------------------------------------------------------------------------
   wire       bus_flash_cs_n  = uio_bus[0];
   wire       bus_sck         = uio_bus[3];
   wire       bus_ram_a_cs_n  = uio_bus[6];
   wire       bus_ram_b_cs_n  = uio_bus[7];
   wire [3:0] bus_sio         = {uio_bus[5], uio_bus[4], uio_bus[2], uio_bus[1]};

   wire [3:0] asic_sio_oe     = {asic_uio_oe[5], asic_uio_oe[4],
                                 asic_uio_oe[2], asic_uio_oe[1]};
   wire [3:0] asic_sio_out    = {uio_out[5], uio_out[4], uio_out[2], uio_out[1]};
   wire       asic_flash_cs_oe  = asic_uio_oe[0];
   wire       asic_flash_cs_out = uio_out[0];
   wire       asic_sck_oe       = asic_uio_oe[3];

   wire [3:0] host_sio_oe     = {host_uio_oe[5], host_uio_oe[4],
                                 host_uio_oe[2], host_uio_oe[1]};
   wire [3:0] host_sio_drive  = {host_uio_drive[5], host_uio_drive[4],
                                 host_uio_drive[2], host_uio_drive[1]};
   wire       host_sck_oe     = host_uio_oe[3];

   wire [3:0] fault_sio_oe    = {fault_uio_oe[5], fault_uio_oe[4],
                                 fault_uio_oe[2], fault_uio_oe[1]};
   wire [3:0] fault_sio_drive = {fault_uio_drive[5], fault_uio_drive[4],
                                 fault_uio_drive[2], fault_uio_drive[1]};
   wire       fault_sck_oe    = fault_uio_oe[3];

   wire       bus_gnt         = uo_out[1];

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
