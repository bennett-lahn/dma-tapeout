[![gds](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/gds.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/gds.yaml)
[![docs](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/docs.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/docs.yaml)
[![test](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/test.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/test.yaml)
[![timing](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/timing.yaml/badge.svg)](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/timing.yaml)

# Zero-Overhead Scatter-Gather DMA

A **1x1** Tiny Tapeout ASIC (**TTIHP26b** / IHP SG13G2) that copies bytes between two QSPI PSRAM devices using descriptors stored in memory.

Shuttle datasheet: [docs/info.md](docs/info.md).

## Overview

Classic DMA spends on-chip resources storing source, destination, and length for every transaction. This design keeps only the **active** transaction on-chip and stores the rest in PSRAM as a linked list. After the host programs the chain and pulses START, the ASIC uses the shared QSPI bus to bulk-copy memory until a transaction with the QUIT flag ends the chain.

Target: demoboard bulk mover between PSRAM A and B (same-device and cross-device). Flash on the PMOD can be accessed by the onboard MCU using QSPI passthrough while the DMA engine is active, but is not accessed by the DMA engine.


|                 |                                                  |
| --------------- | ------------------------------------------------ |
| Shuttle         | Tiny Tapeout IHP **TTIHP26b**                    |
| Process         | IHP SG13G2 (1.2 V core / 3.3 V I/O)              |
| Budget          | One tile (`1x1`); 66 MHz `clk`; QSPI SCK = clk/2 |
| Top module      | `tt_um_lahnb_sgdma`                              |
| External memory | Dual APS6404L QSPI PSRAM on the TT QSPI PMOD     |


## How it works

1. MCU asserts `BUS_REQ`, waits for `BUS_GNT`, and programs both PSRAMs with desired transactions.
2. MCU releases the bus (`BUS_REQ` low) and pulses `START`.
3. ASIC fetches 11-byte TCDs describing the transactions, copies `TRANSFER_LEN` bytes from source to dest, and follows `NEXT_TCD` until `QUIT`.
4. `DONE` goes high when idle.

Each TCD is 11 bytes: 24-bit source, dest, and next pointers, 8-bit length (`0` is a no-op), and flags for which device is src/dest/next plus `QUIT`.

## Host pins


| Port        | Name    | Role                                          |
| ----------- | ------- | --------------------------------------------- |
| `ui_in[0]`  | START   | Accepted only while idle and `BUS_REQ` is low |
| `ui_in[2]`  | BUS_REQ | MCU wants the shared QSPI pins                |
| `uo_out[0]` | DONE    | High whenever the DMA is idle                 |
| `uo_out[1]` | BUS_GNT | MCU may drive `uio`                           |


`uio` pinout is identical to / compatible with TinyTapeout QSPI PMOD. 

Bring-up steps are available in [docs/info.md](docs/info.md).

## Documentation

- Shuttle how-it-works / how-to-test: [docs/info.md](docs/info.md)
- Overview: [docs/human/overview.md](docs/human/overview.md)
- Architecture: [docs/human/architecture/00-index.md](docs/human/architecture/00-index.md)
- Verification: [docs/human/verification/00-index.md](docs/human/verification/00-index.md)



## Tiny Tapeout

Tiny Tapeout is an educational project that makes it cheaper to get digital designs manufactured on a real chip. Learn more at [tinytapeout.com](https://tinytapeout.com).