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

**Phase 2** (V1 feature RTL + verification hardening). Working RTL under `src/rtl/` (`top.v`, `sys_controller.sv`, `qspi_engine.sv`, `types.svh`) covers TT wrap, bus keeper / pass-through, QPI engine, and descriptor fetch / copy / chain / cross-device. Cocotb milestones **M0–M3** are accepted (smoke through delay / launch / RX; latest M3 exit 2026-08-10). Manual LibreLane harden (Nix, `ttihp-verilog-template`) closed **1x1 @ 66 MHz** with ~158 DFFs - see [`architecture/hardening.md`](architecture/hardening.md). Still open inside V1: sticky error / `uo_out[7:2]` status packing, M4 formal, M5 random/coverage, CI smoke, firmware / demoboard (M7), then gate-level and physical closure. TinyDMA-2C (Andrew Kim, TT 296) is prior-art feasibility context only; this implementation is original. Details live in `../llm/prior-art/tinydma-2c.md` and must be cited when used.

## See also

- Architecture: [`architecture/00-index.md`](architecture/00-index.md)
- Roadmap / open issues: `roadmap.md`
- Deep agent context: `../llm/`
