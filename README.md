![gds](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/gds.yaml/badge.svg)
![docs](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/docs.yaml/badge.svg)
![test](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/test.yaml/badge.svg)
![timing](https://github.com/bennett-lahn/dma-tapeout/actions/workflows/timing.yaml/badge.svg)
![fpga](https://img.shields.io/badge/fpga-passing-brightgreen)

# TinyDMA: A Descriptor-Based Dual-PSRAM Memory Mover

**TinyDMA** is a **1x1** Tiny Tapeout ASIC (**TTIHP26b** / IHP SG13G2) that copies bytes between two QSPI PSRAM devices using descriptors stored in memory.

Shuttle datasheet: [docs/info.md](docs/info.md).

## Overview

Classic DMA spends on-chip resources storing source, destination, and length for every transaction. This design keeps only the **active** transaction on-chip and stores the rest in memory as a linked list. After the host programs the ASIC and pulses START, the ASIC uses the shared QSPI bus to copy memory until a transaction with the QUIT flag ends the chain.


|                   |                                                  |
| ----------------- | ------------------------------------------------ |
| Shuttle           | Tiny Tapeout IHP **TTIHP26b**                    |
| Process           | IHP SG13G2 (1.2 V core / 3.3 V I/O)              |
| Hardware          | One tile (`1x1`); 66 MHz `clk`; QSPI SCK = clk/2 |
| Top module        | `tt_um_lahnb_sgdma`                              |
| External memory   | Dual APS6404L QSPI PSRAM on the TT QSPI PMOD     |

Flash on the PMOD can be accessed by the onboard MCU using QSPI passthrough, but is not used by the DMA engine.

## How it works

1. MCU asserts `BUS_REQ`, waits for `BUS_GNT`, and programs both PSRAMs with desired transactions.
2. MCU releases the bus (`BUS_REQ` low) and pulses `START`.
3. ASIC fetches 11-byte TCDs describing the transactions, copies bytes from source to dest, and follows the next TCD pointers until a terminating TCD is found.
4. `DONE` goes high when idle.

Each TCD is 11 bytes: 24-bit source, dest, and next pointers, 8-bit length (`0` is a no-op), and flags for which device is src/dest/next plus if the ASIC should quit after current TCD.

## Host pins


| Port        | Name    | Role                                          |
| ----------- | ------- | --------------------------------------------- |
| `ui_in[0]`  | START   | Accepted only while idle and `BUS_REQ` is low |
| `ui_in[2]`  | BUS_REQ | MCU wants the shared QSPI pins                |
| `uo_out[0]` | DONE    | High whenever the DMA is idle                 |
| `uo_out[1]` | BUS_GNT | MCU may drive `uio`                           |


`uio` pinout is identical to and compatible with the Tiny Tapeout QSPI PMOD.

## Documentation

- How it works / how to test: [docs/info.md](docs/info.md)
- Overview: [docs/human/overview.md](docs/human/overview.md)
- Architecture: [docs/human/architecture/00-index.md](docs/human/architecture/00-index.md)
- Verification: [docs/human/verification/00-index.md](docs/human/verification/00-index.md)
- Firmware and hardware tests: [firmware/](firmware/)


## Tiny Tapeout

Tiny Tapeout is an educational project that makes it cheaper to get digital designs manufactured on a real chip. Learn more at [tinytapeout.com](https://tinytapeout.com).
