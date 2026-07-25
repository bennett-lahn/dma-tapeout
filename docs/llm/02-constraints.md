# Constraints

These are hard or near-hard limits. Feature proposals must respect them.

## Silicon / Tiny Tapeout

| Constraint | Value / guidance |
|---|---|
| Tile budget | **2 tiles maximum** |
| Approx safe DFF budget | ~**500 DFFs** across 2 tiles before routing congestion becomes likely |
| Per-tile DFF heuristics (from notes) | ~256 DFFs comfortable; absolute extreme ~440 DFFs/tile if optimized purely for DFF count and routing is pushed to the limit |
| I/O | **10 in** (`clk`, `rst_n`, `ui_in[7:0]`), **8 bidir** (`uio`), **8 out** (`uo_out`) - severe bottleneck |
| Library | Digital standard cells only (no analog IP) |

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
| Boot mode | Powers up SPI; **MCU** must Enter Quad (`0x35`) on each die before START (D17). ASIC expects QPI already |
| Addressing | Device has large address space (`A[22:0]`); V1 uses **24-bit internal pointers** with `ptr[23]` die select (D19); fixed head at `0x000000` / PSRAM 0 (D18); `QUIT` ends chain |
| CE# low time (`tCEM`) | Continuous CE# low cannot exceed max CE# low pulse width or **internal DRAM refresh is blocked and data can corrupt**. Max **4 us** (extended grade) / **8 us** (standard grade). V1 `N=1` / 11-byte fetch pulses stay well under this without a dedicated slicer. |
| CE# high between bursts (`tCPH`) | Min **18 ns** |
| Last-byte read terminate | Datasheet/notes recommend longer CE# hold: **`tCHD > tACLK + tCLK`** so the controller latches the final beat before raising CE# |
| Timing | `tACLK` (CLK-to-Q, min ~2 ns / max ~5.5 ns); Phase 3 checklist: `11-timing-analysis.md` |
| Clock target (D16) | System **`clk` 66 MHz**; engine **SCK = clk/2** (≈ 33 MHz); RX on **rising** SCK |
| ASIC opcodes (D17) | QPI only: read **`0xEB`**, write **`0x02`**. No Enter/Exit Quad / reset on ASIC |
| Soft reset recovery | After `0x66`/`0x99`, wait `tRST` min **50 ns** before next valid command |

## Schedule constraint

- Planning assumed ~**50 days** to next shuttle.
- Missing a shuttle is acceptable but should be treated as a failure mode, not a planning assumption.
- Prefer cutting scope (see D12 / `10-post-v1-features.md`) over missing verification freeze.

## Soft constraints / project policies

- TinyDMA-2C prior art may be cited with explicit attribution; copying architecture/RTL is not allowed. See `prior-art/tinydma-2c.md`.
- Human docs stay condensed; LLM docs stay verbose and current.
- Obsidian vault notes are handwritten working notes: **read-only**, do not edit from this repo workflow.
- Verilog/SystemVerilog: leading commas on ports/instantiations; synchronous **active-low** reset (`rst_n`) only when resets are introduced.
