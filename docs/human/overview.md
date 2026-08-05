# Scatter-Gather DMA for Tiny Tapeout

## What this is

A 2-tile Tiny Tapeout ASIC (**TTIHP26b / IHP SG13G2**) that DMA-moves bytes through external QSPI **PSRAM** (both devices on the flash+PSRAM PMOD) using **descriptors stored in memory**, not a pile of on-chip channel registers.

V1 target: **bulk mover** between PSRAM A and B (learning / resume demo). ADC telemetry is post-V1 territory if pursued later.

## Why it matters

- Fits a severe area/I/O budget by keeping only the active descriptor on-chip
- Demonstrates real systems work: bus mastership, QSPI, firmware/hardware split, verification with memory models
- Stronger resume story than an isolated hash pipeline for this project's goals

## Main capabilities (V1)

1. **Scatter-gather** via linked Transfer Control Descriptors (TCDs) in PSRAM
2. **Dual PSRAM** (RAM A + RAM B): read/write either device, including cross-device copies
3. **Host pass-through** (`BUS_REQ`/`BUS_GNT`; ASIC releases `uio_oe` while granted, parks CS/SCK while not) so the MCU can program both PSRAMs **and** flash; START hands execution to the ASIC
4. **`rst_n` kill** path (D23; no soft-abort pin) so a bad/long run returns the ASIC to idle and releases shared OE

## Explicit non-goals / post-V1

- **V1:** no ALU, ring, conditional-stop; ASIC does **not** read or write flash
- **Post-V1 ladder:** ALU → cond-stop → ring → ASIC flash read → maybe flash write ([`architecture/post-v1.md`](architecture/post-v1.md))

## Hard limits

- Max **2 Tiny Tapeout tiles** (IHP tile geometry; see [`architecture/limitations.md`](architecture/limitations.md))
- DFF-hungry features are suspicious by default
- PSRAM `CE#` must rise often enough to allow refresh
- Digital-only ASIC on **ihp-sg13g2** (1.2 V core / 3.3 V I/O)

## Status

Architecture / planning. Early RTL under `src/rtl/` (QSPI package + engine skeleton). TinyDMA-2C (Andrew Kim, TT 296) is prior-art feasibility context only; implementation will be original. Details live in `../llm/prior-art/tinydma-2c.md` and must be cited when used.

## See also

- Architecture: [`architecture/00-index.md`](architecture/00-index.md)
- Roadmap / open issues: `roadmap.md`
- Deep agent context: `../llm/`
