// Shared L1/L2 physical uio plane: wired tristate, CS pull-ups, model aliases.
// Included by tb_top.sv and tb_gl.sv. Does not instantiate the DUT (L2 is
// flattened, no #(.DMA_BUF_DEPTH)).
//
// Expects the including module to declare:
//   uio_out, uio_oe, uo_out
//   host_uio_drive, host_uio_oe
//   psram0_sio_drive, psram0_sio_oe, psram1_sio_drive, psram1_sio_oe
//   fault_uio_drive, fault_uio_oe
//
// Physical plane: undriven SCK/SIO float as Z. Board 10k pull-ups keep CS
// bits 0/6/7 high. No Z->0 idle overlay on SCK (bit 3) or SIO (bits 1,2,4,5).
// Q-SIO-X: SIO must not be X when sampled in a host-driven phase.
// Q-SCKIDLE: SCK idle low while deselected; OE=0 + Z is float, not parked-low.
// DUT uio_in stays this physical net.

   initial begin
      host_uio_drive   = '0;
      host_uio_oe      = '0;
      fault_uio_drive  = '0;
      fault_uio_oe     = '0;
      psram0_sio_drive = '0;
      psram0_sio_oe    = '0;
      psram1_sio_drive = '0;
      psram1_sio_oe    = '0;
   end

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

   // Board keepers: 10k pull-ups exist on the three CS nets only. SIO and SCK
   // have no keeper, so their listen windows are genuinely floating.
   pullup pu_flash_cs (uio_bus[0]);
   pullup pu_ram_a_cs (uio_bus[6]);
   pullup pu_ram_b_cs (uio_bus[7]);

   wire   [7:0] uio_in = uio_bus;

   // Model aliases from the physical net. CE# sees the pull-up to 1; SCK/SIO
   // remain Z when undriven so the parser can treat unresolved SCK as no-edge.
   wire psram_sck   = uio_bus[3];
   wire psram0_ce_n = uio_bus[6];
   wire psram1_ce_n = uio_bus[7];

   // Ownership view for test/monitors/qspi.py (SharedBusMonitor). These alias
   // names are the level-independent contract shared with tb_engine: the uio
   // pin map lives here, not in the Python monitor. Ownership is judged from
   // the OE aliases; bus_* is evidence, never the ownership source.
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
   wire       asic_sck_out      = uio_out[3];
   wire       asic_ram_a_cs_oe  = asic_uio_oe[6];
   wire       asic_ram_a_cs_out = uio_out[6];
   wire       asic_ram_b_cs_oe  = asic_uio_oe[7];
   wire       asic_ram_b_cs_out = uio_out[7];

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

   wire       done            = uo_out[0];
   wire       bus_gnt         = uo_out[1];
