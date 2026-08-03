// sys_qspi_harness - integration formal harness (sys_controller + real qspi_engine)
// Compile order: types.svh, qspi_engine.sv, sys_controller.sv, this file,
//                ../bind/sys_controller_properties.sv, ../bind/qspi_engine_properties.sv
// Assumptions (M4): FA-RST-INIT, FA-RST-RUN, FA-START-PULSE; no FA-REQ-LEGAL (proved from controller).
// Does not import cocotb.

module sys_qspi_harness
   import qspi_pkg::*;
#(
   parameter int unsigned DMA_BUF_DEPTH = 1
)(
   input  logic clk
   ,input logic rst_n

   // Post-synchronizer host inputs (symbolic; FA-START-PULSE in M4)
   ,input logic       start
   ,input logic       bus_req

   // Pad sample (unconstrained in baseline jobs)
   ,input logic [3:0] sio_in
);

   logic       done;
   logic       bus_gnt;

   logic             qspi_txn_valid;
   qspi_cmd_t        qspi_cmd;
   qspi_addr_t       qspi_addr;
   qspi_device_sel_t qspi_device_sel;
   qpi_byte_len_t    qspi_byte_len;
   logic       [3:0] qspi_wdata;

   logic          qspi_busy;
   logic    [3:0] qspi_rdata;
   logic          qspi_rdata_valid;
   logic          qspi_wdata_next;
   logic          qspi_sclk;
   logic          qspi_ram_a_cs_n;
   logic          qspi_ram_b_cs_n;
   logic    [3:0] sio_out;
   logic    [3:0] sio_oe;

   // TODO(M4): add reset profiles and assumption audit IDs.

   qspi_engine qspi_engine_inst (
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

   sys_controller #(
      .DMA_BUF_DEPTH(DMA_BUF_DEPTH)
   ) sys_controller_inst (
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

   // TODO(M4): bind sys_controller_properties and qspi_engine_properties.

endmodule
