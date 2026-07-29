/*
 * SPDX-FileCopyrightText: 2026 Zero-Overhead Scatter-Gather DMA
 * SPDX-License-Identifier: Apache-2.0
 *
 * Tiny Tapeout user module wrapper (TTIHP26b, ihp-sg13g2 PDK).
 * Port list is fixed by the TT mux and identical across PDKs;
 * see ttihp-verilog-template/src/project.v.
 *
 * Pin map (docs/human/architecture/blocks/host-interface.md):
 *   ui_in[0]  START            uo_out[0]  DONE
 *   ui_in[1]  reserved         uo_out[1]  BUS_GNT
 *   ui_in[2]  BUS_REQ          uo_out[7:2] reserved (status / DFT)
 *   ui_in[7:3] reserved
 *   uio[0] flash CS, uio[1:2] SIO0/1, uio[3] SCK, uio[4:5] SIO2/3,
 *   uio[6] RAM A CS, uio[7] RAM B CS
 */

`default_nettype none

module tt_um_lahnb_sgdma
   import qspi_pkg::*;
(
  input  wire [7:0] ui_in     // Dedicated inputs
  ,output wire [7:0] uo_out    // Dedicated outputs
  ,input  wire [7:0] uio_in    // IOs: Input path
  ,output wire [7:0] uio_out   // IOs: Output path
  ,output wire [7:0] uio_oe    // IOs: Enable path (active high: 0=input, 1=output)
  ,input  wire       ena       // Always 1 when the design is powered, so it can be ignored
  ,input  wire       clk
  ,input  wire       rst_n
);

// Host input sync (ui_in is async to clk; see docs/human/architecture/blocks/host-interface.md)
logic [1:0] start_sync;
logic       start_sync_d;
logic [1:0] bus_req_sync;
logic       start;
logic       done;

// Top level
logic bus_req;
logic bus_gnt;
logic bus_park;

// System Controller
logic [3:0]       qspi_wdata;
logic             qspi_txn_valid;
qspi_cmd_t        qspi_cmd;
qspi_addr_t       qspi_addr;
qspi_device_sel_t qspi_device_sel;
qpi_byte_len_t    qspi_byte_len;


// QSPI Engine
logic          qspi_busy;
logic    [3:0] qspi_rdata;
logic          qspi_rdata_valid;
logic          qspi_wdata_next;
logic          qspi_sclk;
logic          qspi_ram_a_cs_n;
logic          qspi_ram_b_cs_n;
logic    [3:0] sio_in;
logic    [3:0] sio_out;
logic    [3:0] sio_oe;

// Synchronize START/BUS_REQ into clk, then rising-edge detect START into a
// one-clk pulse. sys_controller never samples raw ui_in.
always_ff @(posedge clk) begin
   if (~rst_n) begin
      start_sync   <= 2'b00;
      start_sync_d <= 1'b0;
      bus_req_sync <= 2'b00;
   end else begin
      start_sync   <= {start_sync[0], ui_in[0]};
      start_sync_d <= start_sync[1];
      bus_req_sync <= {bus_req_sync[0], ui_in[2]};
   end
end

assign start   = start_sync[1] & ~start_sync_d;
assign bus_req = bus_req_sync[1];

qspi_engine qspi_engine (
   .clk        (clk)
   ,.rst_n      (rst_n)
   ,.txn_valid  (qspi_txn_valid)
   ,.cmd        (qspi_cmd)
   ,.addr       (qspi_addr)
   ,.device_sel (qspi_device_sel)
   ,.byte_len   (qspi_byte_len)
   ,.wdata      (qspi_wdata)
   ,.busy       (qspi_busy)
   ,.rdata      (qspi_rdata)
   ,.rdata_valid(qspi_rdata_valid)
   ,.wdata_next (qspi_wdata_next)
   ,.sio_in     (sio_in)
   ,.sclk       (qspi_sclk)
   ,.ram_a_cs_n (qspi_ram_a_cs_n)
   ,.ram_b_cs_n (qspi_ram_b_cs_n)
   ,.sio_out    (sio_out)
   ,.sio_oe     (sio_oe)
);

sys_controller sys_controller (
   .clk             (clk)
   ,.rst_n           (rst_n)
   ,.start           (start)
   ,.bus_req         (bus_req)
   ,.done            (done)
   ,.bus_gnt         (bus_gnt)
   ,.qspi_busy       (qspi_busy)
   ,.qspi_rdata_valid(qspi_rdata_valid)
   ,.qspi_rdata      (qspi_rdata)
   ,.qspi_wdata_next (qspi_wdata_next)
   ,.qspi_txn_valid  (qspi_txn_valid)
   ,.qspi_cmd        (qspi_cmd)
   ,.qspi_addr       (qspi_addr)
   ,.qspi_device_sel (qspi_device_sel)
   ,.qspi_byte_len   (qspi_byte_len)
   ,.qspi_wdata      (qspi_wdata)
);

// Dedicated outputs. done/bus_gnt are already registered inside
// sys_controller, so no extra glitch-guard flop is needed here.
assign uo_out[0]   = done;
assign uo_out[1]   = bus_gnt;
assign uo_out[7:2] = 6'b0; // reserved (status / DFT - open)

// uio pad mapping (uio[0] flash CS, [1:2]/[4:5] SIO0-3, [3] SCK, [6:7] RAM A/B CS)
assign sio_in = {uio_in[5], uio_in[4], uio_in[2], uio_in[1]};

assign uio_out[0] = 1'b1;             // flash CS: parked high, never driven low by ASIC
assign uio_out[1] = sio_out[0];
assign uio_out[2] = sio_out[1];
assign uio_out[3] = qspi_sclk;
assign uio_out[4] = sio_out[2];
assign uio_out[5] = sio_out[3];
assign uio_out[6] = qspi_ram_a_cs_n;
assign uio_out[7] = qspi_ram_b_cs_n;

// Bus arbitration / uio_oe (D26 bus keeper): while rst_n && ~bus_gnt the ASIC
// drives flash CS + both RAM CS high and SCK low; SIO follows the engine's
// per-phase mask, which drives a don't-care everywhere except the dummy/wait
// and read-data phases (float to listen for the PSRAM). Asserted reset disables
// every shared output enable.
assign bus_park = ~bus_gnt;

assign uio_oe[0] = bus_park & rst_n;             // flash CS
assign uio_oe[1] = bus_park & sio_oe[0] & rst_n; // SIO0
assign uio_oe[2] = bus_park & sio_oe[1] & rst_n; // SIO1
assign uio_oe[3] = bus_park & rst_n;             // SCK
assign uio_oe[4] = bus_park & sio_oe[2] & rst_n; // SIO2
assign uio_oe[5] = bus_park & sio_oe[3] & rst_n; // SIO3
assign uio_oe[6] = bus_park & rst_n;             // RAM A CS
assign uio_oe[7] = bus_park & rst_n;             // RAM B CS

wire _unused = &{ena, ui_in[7:3], ui_in[1], uio_in[0], uio_in[3], uio_in[6], uio_in[7], 1'b0};

endmodule

`default_nettype wire
