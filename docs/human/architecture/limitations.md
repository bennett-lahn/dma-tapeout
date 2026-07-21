# Limitations

Hard or near-hard limits. Feature proposals must respect these. Deeper PSRAM protocol detail: [`blocks/qspi-engine.md`](blocks/qspi-engine.md) and [`../../llm/05-qspi-psram.md`](../../llm/05-qspi-psram.md).

## Silicon / Tiny Tapeout

| Limit | Value / guidance |
|---|---|
| Tile budget | **2 Tiny Tapeout tiles max** |
| Comfortable DFFs / tile | ~**256** |
| Extreme DFFs / tile | ~**440** (~55 bytes) if optimized only for DFF count and routing is pushed to the limit |
| Soft 2-tile ceiling | treat ~**500 DFFs** total as the practical warning line |
| I/O | Severe; serialize host interfaces; reserve a muxed DFT/debug output for FSM observe |
| Process | Digital standard cells only |

### Design implication

DFFs are the scarce resource. Prefer externalizing configuration into PSRAM (TCDs), narrow byte datapaths, and a shared QSPI engine. Avoid multi-channel static register files, deep FIFOs, and wide crypto/DSP paths. Post-V1 may add a tiny combinational ALU (see [`post-v1.md`](post-v1.md)).

## I/O principles

1. Serialize (UART/SPI/I2C-style), do not assume wide parallel host buses
2. DFT: at least one muxed debug/status pin for post-tapeout FSM observation
3. Verification replaces probing: edge cases must be caught in simulation

## External memory (APS6404L-class PSRAM)

| Topic | Constraint |
|---|---|
| Power-up | >= 150 us before commands (`tPU`; CE# high) |
| Boot mode | Standard 1-bit SPI; enter Quad/QPI via command (e.g. `0x35`) |
| Addressing | Device uses `A[22:0]`; V1 uses **24-bit internal pointers** (full address phase); address `0` reserved null |
| CE# low (`tCEM`) | Max **4 us** (extended) / **8 us** (standard). Longer holds block refresh and can corrupt memory |
| CE# high (`tCPH`) | Min **18 ns** between bursts |
| Read terminate | Prefer **`tCHD > tACLK + tCLK`** so the last beat latches before CE# rises |
| `tACLK` | CLK-to-Q about **2 ns min / 5.5 ns max** - rising-edge sample margin collapses near 84 MHz |
| Practical clocks | ~**66 MHz** SPI / ~**84 MHz** QPI linear burst, or lower for margin |
| Soft reset | After `0x66`/`0x99`, wait `tRST` min **50 ns** |

**Design requirement:** long DMA transfers must be sliced with CE# high gaps. Descriptor **11-byte** fetches may hold CE# across the burst; multi-kilobyte copies must not. DMA data path is QPI-only (SPI config/bring-up only).

## Demoboard memory context

| Part | Constraint / role |
|---|---|
| **2x** APS6404L-3SQR PSRAM | Both are DMA endpoints (same- and cross-device). Each die has its own `tCEM` / refresh; only one CE# low at a time on the shared bus. |
| W25Q128JV flash | On PMOD; **MCU pass-through only** for V1. ASIC must keep flash CS OE off. ASIC flash R/W is super-stretch, not a V1 requirement. |

## See also

- Overview: [`overview.md`](overview.md)
- System map: [`system.md`](system.md)
- Agent constraints: [`../../llm/02-constraints.md`](../../llm/02-constraints.md)
