[![gds](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/gds.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/gds.yaml)
[![docs](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/docs.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/docs.yaml)
[![test](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/test.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/test.yaml)
[![timing](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/timing.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/timing.yaml)

# Zero-Overhead Scatter-Gather DMA

A **1x1** Tiny Tapeout ASIC (**TTIHP26b** / IHP SG13G2) that DMA-copies bytes between two QSPI PSRAM devices using **descriptors stored in memory**, not a pile of on-chip channel registers.

Shuttle datasheet: [docs/info.md](docs/info.md).

## Overview

Classic DMA spends flip-flops storing source, destination, and length for every channel on-chip. This design keeps only the **active** Transfer Control Descriptor (TCD) on-chip and stores the rest in PSRAM as a linked list. After the host programs the chain and pulses START, the ASIC masters the shared QSPI bus and bulk-copies until a TCD with the QUIT flag ends the chain.

Target: demoboard bulk mover between PSRAM A and B (same-device and cross-device). Flash on the PMOD is MCU pass-through only; the ASIC never selects it.

| | |
|---|---|
| Shuttle | Tiny Tapeout IHP **TTIHP26b** |
| Process | IHP SG13G2 (1.2 V core / 3.3 V I/O) |
| Budget | One tile (`1x1`); 66 MHz `clk`; QSPI SCK = clk/2 |
| Top module | `tt_um_lahnb_sgdma` |
| External memory | Dual APS6404L-class QSPI PSRAM on the TT QSPI PMOD |

V1 is this bulk-mover feature set only: no in-flight ALU, ring wrap, conditional-stop, or ASIC flash master.

## How it works

1. MCU asserts `BUS_REQ`, waits for `BUS_GNT`, and programs both PSRAMs (Enter Quad `0x35`, TCD chain at address 0 on PSRAM 0, payloads).
2. MCU releases the bus (`BUS_REQ` low) and pulses `START`.
3. ASIC fetches 11-byte TCDs, copies `TRANSFER_LEN` bytes from source to dest (including A to B / B to A), and follows `NEXT_TCD` until `QUIT`.
4. `DONE` goes high when idle. Kill a runaway chain with `rst_n`.

Each TCD is 11 bytes: 24-bit source, dest, and next pointers, 8-bit length (`0` is a no-op), and flags for which device is src/dest/next plus `QUIT`.

## Host pins

| Port | Name | Role |
|---|---|---|
| `ui_in[0]` | START | Accepted only while idle and `BUS_REQ` is low |
| `ui_in[2]` | BUS_REQ | MCU wants the shared QSPI pins |
| `uo_out[0]` | DONE | High whenever the DMA is idle |
| `uo_out[1]` | BUS_GNT | MCU may drive `uio` |

Shared `uio` map: flash CS, SIO0, SIO1, SCK, SIO2, SIO3, RAM A CS, RAM B CS. While out of reset and not granted, the ASIC parks flash CS and both RAM CS high and SCK low.

Bring-up steps live in [docs/info.md](docs/info.md).

## Status

V1 feature RTL is in `src/`. Cocotb M0-M5 simulation exits are accepted. Manual LibreLane harden closed **1x1 at 66 MHz**. Remaining work: CI smoke, firmware / demoboard, gate-level and shuttle closure.

## Documentation

- Shuttle how-it-works / how-to-test: [docs/info.md](docs/info.md)
- Human overview: [docs/human/overview.md](docs/human/overview.md)
- Architecture: [docs/human/architecture/00-index.md](docs/human/architecture/00-index.md)
- Verification: [docs/human/verification/00-index.md](docs/human/verification/00-index.md)

## Tiny Tapeout

Tiny Tapeout is an educational project that makes it cheaper to get digital designs manufactured on a real chip. Learn more at [tinytapeout.com](https://tinytapeout.com).
