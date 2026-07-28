# Scatter-Gather DMA for Tiny Tapeout

## What this is

A 2-tile Tiny Tapeout ASIC that DMA-moves bytes through external QSPI **PSRAM** (both devices on the flash+PSRAM PMOD) using **descriptors stored in memory**, not a pile of on-chip channel registers.

V1 target: **bulk mover** between PSRAM A and B (learning / resume demo). ADC telemetry is post-V1 territory if pursued later.

## Why it matters

- Fits a severe area/I/O budget by keeping only the active descriptor on-chip
- Demonstrates real systems work: bus mastership, QSPI, firmware/hardware split, verification with memory models
- Stronger resume story than an isolated hash pipeline for this project's goals

## Main capabilities (V1)

1. **Scatter-gather** via linked Transfer Control Descriptors (TCDs) in PSRAM
2. **Dual PSRAM** (RAM A + RAM B): read/write either device, including cross-device copies
3. **Host pass-through** (`BUS_REQ`/`BUS_GNT`, ASIC `uio_oe=0` while granted) so the MCU can program both PSRAMs **and** flash; START hands the bus to the ASIC
4. **Abort** path (pin encoding TBD) so a bad/long run can release the bus

## Explicit non-goals / post-V1

- **V1:** no ALU, ring, conditional-stop; ASIC does **not** read or write flash
- **Post-V1 ladder:** ALU → cond-stop → ring → ASIC flash read → maybe flash write ([`architecture/post-v1.md`](architecture/post-v1.md))

## Hard limits

- Max **2 Tiny Tapeout tiles**
- DFF-hungry features are suspicious by default
- PSRAM `CE#` must rise often enough to allow refresh
- Digital-only ASIC

## Status

Architecture / planning. Early RTL under `src/rtl/` (QSPI package + engine skeleton). TinyDMA-2C (Andrew Kim, TT 296) is prior-art feasibility context only; implementation will be original. Details live in `../llm/prior-art/tinydma-2c.md` and must be cited when used.

## See also

- Architecture: [`architecture/00-index.md`](architecture/00-index.md)
- Roadmap / open issues: `roadmap.md`
- Deep agent context: `../llm/`
