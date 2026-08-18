# Limitations

Hard or near-hard limits. Feature proposals must respect these. Deeper PSRAM protocol detail: [`blocks/qspi-engine.md`](blocks/qspi-engine.md) and [`../../llm/05-qspi-psram.md`](../../llm/05-qspi-psram.md).

## Silicon / Tiny Tapeout (TTIHP26b / ihp-sg13g2)

| Limit | Value / guidance |
|---|---|
| Tile budget | **1x1 only** (one Tiny Tapeout tile; D36). `1x2` / 2-tile is out of budget. If N=5 does not fit or time-close, cut area (density, hold repair, or `DMA_BUF_DEPTH`); never grow tiles |
| Tile size (IHP) | **1x1** ≈ **202.08 × 154.98 µm** (`tt_block_1x1`). IHP also publishes **1x2** ≈ **202.08 × 313.74 µm** - not an operational V1 option. Site 0.48 × 3.78 µm |
| Comfortable DFFs / tile | Historical sky130-era ~**256**; operational caution is the 1x1 ~200 DFF warning (D36) |
| Extreme DFFs / tile | Historical sky130-era ~**440** (~55 bytes) if optimized only for DFF count; not a V1 target |
| Soft 1x1 DFF caution | treat **~200 DFFs** as the warning line (first harden ~158, ~60% util). Hard gate: fits and times on `tt_block_1x1`. Old ~500 DFF / 2-tile figure is historical |
| First IHP harden (2026-08) | **158** DFFs mapped; **1x1** closes at 66 MHz (`CLOCK_PERIOD` 15.15 ns) with DRC/LVS/timing pass - see [`hardening.md`](hardening.md) |
| I/O | Severe; serialize host interfaces; unused host bits tied 0 (D34) |
| Process / voltages | IHP **SG13G2**: **1.2 V** core, **3.3 V** pads (level-shifted). Digital stdcells only |
| TT pads | `ui`/`clk`/`rst_n` → `sg13g2_IOPadIn`; `uo` → `sg13g2_IOPadOut30mA`; `uio` → `sg13g2_IOPadInOut30mA` |
| GPIO / pad speed | **No published MHz toggle rating** in IHP IO PDFs/liberty. Use pad delay + `max_cap`/`max_transition` + TT mux STA. Old sky130 **66/33 MHz** figures do **not** apply |
| System clock | **66 MHz maximum** (demoboard clock-gen class); QSPI SCK **≈33 MHz** via clk/2 (D16 / D27). Phase 3 re-validate |

### Design implication

DFFs are the scarce resource. Prefer externalizing configuration into PSRAM (TCDs), narrow byte datapaths, and a shared QSPI engine. Avoid multi-channel static register files, deep FIFOs, and wide crypto/DSP paths. Data hold is **1 byte**; do not size on-chip scratch to `tCEM`, and keep FSM/QSPI correctness independent of buffer depth (D20).

## I/O principles

1. Serialize (UART/SPI/I2C-style), do not assume wide parallel host buses
2. Verification replaces probing: edge cases must be caught in simulation

## External memory (APS6404L-class PSRAM)

| Topic | Constraint |
|---|---|
| Power-up | >= 150 us before commands (`tPU`; CE# high) |
| Boot mode | Powers up SPI; **MCU** Enter Quad (`0x35`) per device before START (D17). ASIC expects QPI |
| Addressing | Device uses `A[22:0]`; V1 uses **24-bit internal pointers** with device selects in `CTRL_FLAGS` (D24); fixed head at address `0` / PSRAM 0 (D18); `QUIT` ends chain |
| CE# low (`tCEM`) | Max **4 us** (extended) / **8 us** (standard). Longer holds block refresh and can corrupt memory |
| CE# high (`tCPH`) | Min **18 ns** between bursts → **≥ 2 `clk`** CE# high @ 66 MHz before next CE# low |
| Read terminate | Prefer **`tCHD > tACLK + tCLK`** so the last beat latches before CE# rises |
| `tACLK` | CLK-to-Q about **2 ns min / 5.5 ns max** - eased by **SCK = clk/2** (≈ 33 MHz) |
| Clock (D16 / D27) | System **`clk` max 66 MHz**; engine **SCK = clk/2**; RX on **rising** SCK. Not justified by a published IHP pad MHz rating (none found). Phase 3 re-validate vs pad delay / board / TT mux STA / `tACLK` |
| Soft reset | After `0x66`/`0x99`, wait `tRST` min **50 ns** |

**V1 implication:** with a **1-byte** data hold (and 11-byte TCD fetch), each CE# pulse is short; no dedicated `tCEM` / page-boundary slicer is required. First risky depths at 33 MHz SCK: **`N ≥ 60`** (`tCEM` 4 us read), **`N ≥ 1026`** (two page crosses). ASIC data path is QPI-only (`0xEB`/`0x02`); MCU owns enter/exit QPI (D17). Detail: [`blocks/descriptor-fsm.md`](blocks/descriptor-fsm.md).

## Demoboard memory context

| Part | Constraint / role |
|---|---|
| **2x** APS6404L-3SQR PSRAM | Both are DMA endpoints (same- and cross-device). Each device has its own `tCEM` / refresh; only one CE# low at a time on the shared bus. |
| W25Q128JV flash | On PMOD; **MCU pass-through only**. ASIC parks flash CS high while `~BUS_GNT` and never drives it low (D26). Board **10 kΩ** CS pull-ups. ASIC does not master flash. |

## See also

- Overview: [`overview.md`](overview.md)
- System map: [`system.md`](system.md)
- Post-RTL timing checklist: [`timing.md`](timing.md)
- Agent constraints: [`../../llm/02-constraints.md`](../../llm/02-constraints.md)
