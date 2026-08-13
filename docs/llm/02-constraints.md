# Constraints

These are hard or near-hard limits. Feature proposals must respect them.

## Silicon / Tiny Tapeout (TTIHP26b / ihp-sg13g2)

| Constraint | Value / guidance |
|---|---|
| Shuttle / PDK | **TTIHP26b**; LibreLane with **`ihp-sg13g2`** (IHP Open PDK). Digital stdcells `sg13g2_*`; I/O library `sg13g2_io` |
| Tile budget | **2 tiles maximum** |
| Tile geometry (IHP) | **1x1** die box **202.08 × 154.98 µm**; **1x2** **202.08 × 313.74 µm** (`tt-support-tools` `tech/ihp-sg13g2/tile_sizes.yaml`). ~1.7× the sky130 1x2 area; site is **0.48 × 3.78 µm** (vs 0.46 × 2.72 µm on sky130), so usable cell capacity is only modestly higher - keep the soft DFF ceiling until a real synth count |
| Approx safe DFF budget | ~**500 DFFs** across 2 tiles before routing congestion becomes likely. First IHP harden (2026-08): **158** mapped `sg13g2_dfrbpq_1` at 66 MHz - under budget; see [`13-hardening-librelane.md`](13-hardening-librelane.md) |
| Per-tile DFF heuristics (from notes) | ~256 DFFs comfortable; absolute extreme ~440 DFFs/tile if optimized purely for DFF count and routing is pushed to the limit (sky130-era heuristics; IHP synth count now available - still treat as soft) |
| First area audit (manual LibreLane) | **1x1 fits** at `CLOCK_PERIOD` **15.15** ns (66 MHz): ~24k µm² logic in ~29k µm² core (~60% pre-fill); DRC/LVS/setup/hold pass. Accidental 1x2 (stale merged config) also passed with more fill. Runbook: [`13-hardening-librelane.md`](13-hardening-librelane.md) |
| I/O | **10 in** (`clk`, `rst_n`, `ui_in[7:0]`), **8 bidir** (`uio`), **8 out** (`uo_out`) - severe bottleneck; port list identical to sky130 TT digital projects |
| Core / pad voltages | **1.2 V** digital core; **3.3 V** I/O (`sg13g2_IOPad*` level-shift 3.3 V ↔ 1.2 V). Demoboard 3.3 V PMOD / PSRAM remains electrically valid |
| TT pad cells (ttiHP mux) | Inputs (`clk`, `rst_n`, `ui_in`): **`sg13g2_IOPadIn`**. Outputs (`uo_out`): **`sg13g2_IOPadOut30mA`**. Bidirectional (`uio`): **`sg13g2_IOPadInOut30mA`**. (From `tt-multiplexer` `ttihp26b` `tt_ihp_wrapper.v` / `tt_ihp_gpio.v`.) |
| Published I/O speed rating | **None** in IHP Open PDK IO docs or liberty - no MHz toggle ceiling analogous to sky130's 66/33 MHz pad ratings. Use delay / load / transition limits + TT mux STA instead (D27) |
| Liberty limits (typ 1.2 V / 3.3 V / 25 °C) | `sg13g2_IOPadInOut30mA`: pad `max_capacitance` ≈ **4.83 pF**; core-side `max_transition` **2.5 ns**. `sg13g2_IOPadIn`: pad `max_transition` **3.5 ns**. Characterization stops at those loads; board + PMOD C may exceed `max_cap` - Phase 3 board check required |
| Pad delay (same corner, datasheet tables) | Input `pad→p2c` rise ~**0.08 ns** / fall ~**0.45 ns** at light load. Output `c2p→pad` ~**1.7 ns** rise / ~**1.6 ns** fall at **1 pF** for InOut30mA (mid-table ~2 ns at 4 pF). 30 mA drive is far stronger than sky130's 4 mA pad - the old **33 MHz output** slew argument does **not** carry over |
| TT I/O mux budgets (`signoff.sdc`) | Pad → user-module inward max delay **5.0 ns**; user-module outward → pad **12.5 ns** (plus separate control-path budgets). `clk`/`rst_n` are in the inward group |
| Clock ceiling (D16, amended D27) | System **`clk` max 66 MHz** (demoboard generator ~66.5 MHz class); registered QSPI **SCK = clk/2** (≈ **33 MHz**). Justification is demoboard / tACLK / simplicity - **not** a published IHP pad MHz rating |
| Library | Digital standard cells only (no analog IP); harden via LibreLane, not OpenLane |
| Harden vehicle | Local: `ttihp-verilog-template/` + Nix LibreLane (`~/librelane` `nix develop`) or Docker `tt_tool --harden`; PDK via `PDK_ROOT=~/ttsetup/pdk`, `PDK=ihp-sg13g2`. Details: [`13-hardening-librelane.md`](13-hardening-librelane.md) |

### Design implication

DFFs are the scarce resource. Prefer:

- Externalizing static configuration into PSRAM (TCDs)
- Narrow datapaths (byte-wide)
- Shared SPI/QSPI engine rather than duplicated masters
- Narrow byte datapath (post-V1 may add a tiny combinational ALU; see `10-post-v1-features.md`)

Avoid:

- Multi-channel static register files on-chip
- Deep FIFOs
- Extra wide state beyond the decided **24-bit** pointers / **11-byte** TCD working set
- Unrolled cryptographic / DSP datapaths

## I/O strategy principles

From early idea evaluation (still applicable):

1. **Serialize interfaces** - do not assume wide parallel host buses.
2. **DFT pin** - reserve at least one muxed debug/status observability path for FSM state after tapeout.
3. **Verification replaces probing** - post-silicon internals are hard to observe; edge cases must be caught in simulation.

## External memory constraints (APS6404L-class PSRAM)

Demoboard/PMOD ecosystem parts of interest: 128 M-bit QSPI Flash (**25Q128JVSM**) plus **2x** 64 M-bit APS6404L-3SQR PSRAM. Flash is not the DMA target; PSRAM is.

| Topic | Constraint |
|---|---|
| Power-up | >= 150 us before commands (`tPU`; CE# high) |
| Boot mode | Powers up SPI; **MCU** must Enter Quad (`0x35`) on each device before START (D17). ASIC expects QPI already |
| Addressing | Device has large address space (`A[22:0]`); V1 uses **24-bit internal pointers** with device selects in `CTRL_FLAGS` (D24); fixed head at `0x000000` / PSRAM 0 (D18); `QUIT` ends chain |
| CE# low time (`tCEM`) | Continuous CE# low cannot exceed max CE# low pulse width or **internal DRAM refresh is blocked and data can corrupt**. Max **4 us** (extended grade) / **8 us** (standard grade). V1 `N=1` / 11-byte fetch pulses stay well under this without a dedicated slicer. |
| CE# high between bursts (`tCPH`) | Min **18 ns** |
| Last-byte read terminate | Datasheet/notes recommend longer CE# hold: **`tCHD > tACLK + tCLK`** so the controller latches the final beat before raising CE# |
| Timing | `tACLK` (CLK-to-Q, min ~2 ns / max ~5.5 ns); Phase 3 checklist: `11-timing-analysis.md` |
| Clock target (D16) | System **`clk` 66 MHz**; engine **SCK = clk/2** (≈ 33 MHz); RX on **rising** SCK |
| ASIC opcodes (D17) | QPI only: read **`0xEB`**, write **`0x02`**. No Enter/Exit Quad / reset on ASIC |
| Soft reset recovery | After `0x66`/`0x99`, wait `tRST` min **50 ns** before next valid command |

## Schedule constraint

- Planning assumed ~**50 days** to next shuttle (historical; re-anchor to the active **TTIHP26b** deadline).
- Missing a shuttle is acceptable but should be treated as a failure mode, not a planning assumption.
- Prefer cutting scope (see D12 / `10-post-v1-features.md`) over missing verification freeze.

## Soft constraints / project policies

- TinyDMA-2C prior art may be cited with explicit attribution; copying architecture/RTL is not allowed. See `prior-art/tinydma-2c.md`.
- Human docs stay condensed; LLM docs stay verbose and current.
- Obsidian vault notes are handwritten working notes: **read-only**, do not edit from this repo workflow.
- Verilog/SystemVerilog: leading commas on ports/instantiations; synchronous **active-low** reset (`rst_n`) only when resets are introduced.
