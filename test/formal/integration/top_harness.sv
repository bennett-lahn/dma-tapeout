// top_harness - production-boundary formal harness (tt_um_lahnb_sgdma)
// Compile order: types.svh, qspi_engine.sv, sys_controller.sv, top.v, this file,
//                ../bind/top_properties.sv, ../bind/sys_controller_properties.sv,
//                ../bind/qspi_engine_properties.sv
// Assumptions (M4): FA-RST-INIT, FA-RST-RUN; START/BUS_REQ via ui_in sync path.
// Does not import cocotb.

module top_harness #(
   parameter int unsigned DMA_BUF_DEPTH = 1
)(
   input  logic [7:0] ui_in
   ,output logic [7:0] uo_out
   ,input  logic [7:0] uio_in
   ,output logic [7:0] uio_out
   ,output logic [7:0] uio_oe
   ,input  logic       ena
   ,input  logic       clk
   ,input  logic       rst_n
);

   // TODO(M4): add reset profiles, pin-parking checks, and bind top_properties.

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

endmodule
