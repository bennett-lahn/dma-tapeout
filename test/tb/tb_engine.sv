// L0 cocotb wrapper: qspi_engine + one PSRAM model attachment hooks.
// Contract: docs/llm/verification/02-platform.md, 03-psram-model.md (L0 section).
// No protocol logic here; Python models and tests drive/monitor ports.
// Q-SIO-X: SIO must not be X when sampled in a host-driven phase. Dummy/read
// data may legally float; models sample sio_bus so Z is visible (no Z->0).

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
   // Dual PSRAM model SIO hooks (cocotb drives via models/psram.py
   // attach_engine_psram). Per-device OE/drive matches tb_top so SharedBusMonitor
   // can judge ownership from distinct handles; only the CE#-selected device
   // may enable drive. Timing-layer return plane is still M1+ / M3.
   // -------------------------------------------------------------------------
   logic    [3:0] psram0_sio_drive;
   logic    [3:0] psram0_sio_oe;
   logic    [3:0] psram1_sio_drive;
   logic    [3:0] psram1_sio_oe;

   // Fault-injection driver for negative ownership tests. A set fault_sio_oe bit
   // takes SIO away from the engine driver and drives fault_sio_drive in its
   // place; on a device-driven bit it is an extra ASIC-side driver, which is how
   // the CHK-PIN-SIO-OWN negative case reproduces dual drive at L0.
   logic    [3:0] fault_sio_drive;
   logic    [3:0] fault_sio_oe;

   initial begin
      psram0_sio_drive = '0;
      psram0_sio_oe    = '0;
      psram1_sio_drive = '0;
      psram1_sio_oe    = '0;
      fault_sio_drive  = '0;
      fault_sio_oe     = '0;
   end

   // -------------------------------------------------------------------------
   // Shared-bus resolution (L0: ASIC SIO OE vs PSRAM model drive)
   // Wired tristate: an undriven listen window floats, and dual drive of
   // disagreeing levels resolves to x. L0 has no board keeper on SIO.
   // Models sample sio_bus / bus_sio; Z is not mapped to 0.
   // -------------------------------------------------------------------------
   wire     [3:0] sio_bus;
   wire     [3:0] asic_sio_oe = sio_oe & ~fault_sio_oe;

   genvar gi;
   generate
      for (gi = 0; gi < 4; gi = gi + 1) begin : gen_sio_drivers
         assign sio_bus[gi] = (asic_sio_oe[gi])    ? sio_out[gi]          : 1'bz;
         assign sio_bus[gi] = (fault_sio_oe[gi])   ? fault_sio_drive[gi]  : 1'bz;
         assign sio_bus[gi] = (psram0_sio_oe[gi])  ? psram0_sio_drive[gi] : 1'bz;
         assign sio_bus[gi] = (psram1_sio_oe[gi])  ? psram1_sio_drive[gi] : 1'bz;
      end
   endgenerate

   // DUT sees the physical plane, including z during its own listen windows.
   wire     [3:0] sio_in = sio_bus;

   // -------------------------------------------------------------------------
   // Scalar pin aliases for the Python PSRAM models (same names as tb_top).
   // Engine always drives SCK/CE#, so these are direct wires.
   // -------------------------------------------------------------------------
   wire psram_sck   = sclk;
   wire psram0_ce_n = ram_a_cs_n;
   wire psram1_ce_n = ram_b_cs_n;

   // -------------------------------------------------------------------------
   // Ownership view for test/monitors/qspi.py (SharedBusMonitor). Same alias
   // names as tb_top/tb_gl so one monitor serves L0, L1, and L2. Flash CS and
   // BUS_GNT are not engine ports, so those aliases do not exist here.
   // -------------------------------------------------------------------------
   wire     [3:0] bus_sio        = sio_bus;
   wire           bus_sck        = sclk;
   wire           bus_ram_a_cs_n = ram_a_cs_n;
   wire           bus_ram_b_cs_n = ram_b_cs_n;
   wire     [3:0] asic_sio_out   = sio_out;

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
   // Waveform dump (FST; path matches test/Makefile waves target). Honor the
   // same WAVES_DISABLE as L1 (WAVES=never).
   // -------------------------------------------------------------------------
`ifndef WAVES_DISABLE
   initial begin
      $dumpfile("dump.fst");
      $dumpvars(0, tb_engine);
      #1;
   end
`endif

endmodule

`default_nettype wire
