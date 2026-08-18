# Scatter-Gather DMA for Tiny Tapeout

## What this is

A **1x1** Tiny Tapeout ASIC (**TTIHP26b / IHP SG13G2**) that DMA-moves bytes through external QSPI **PSRAM** (both devices on the flash+PSRAM PMOD) using **descriptors stored in memory**, not a pile of on-chip channel registers.

Target: **bulk mover** between PSRAM A and B (learning / resume demo). Shipped RTL is this feature set only.

## Why it matters

- Fits a severe area/I/O budget by keeping only the active descriptor on-chip
- Demonstrates real systems work: bus mastership, QSPI, firmware/hardware split, verification with memory models
- Stronger resume story than an isolated hash pipeline for this project's goals

## Main capabilities

1. **Scatter-gather** via linked Transfer Control Descriptors (TCDs) in PSRAM
2. **Dual PSRAM** (RAM A + RAM B): read/write either device, including cross-device copies
3. **Host pass-through** (`BUS_REQ`/`BUS_GNT`; ASIC releases `uio_oe` while granted, parks CS/SCK while not) so the MCU can program both PSRAMs **and** flash; START hands execution to the ASIC
4. **`rst_n` kill** path (D23; no soft-abort pin) so a bad/long run returns the ASIC to idle and releases shared OE

## Explicit non-goals

- No in-flight ALU, ring wrap, or conditional-stop
- ASIC does **not** read or write flash (MCU pass-through only)
- Historical cut rationale: [`../llm/07-decision-log.md`](../llm/07-decision-log.md) (D11/D12)

## Hard limits

- **1x1 only** (one Tiny Tapeout tile; `1x2` is out of budget; D36). Soft DFF caution ~200 on 1x1; hard gate is 1x1 fit/timing (see [`architecture/limitations.md`](architecture/limitations.md))
- DFF-hungry features are suspicious by default
- PSRAM `CE#` must rise often enough to allow refresh
- Digital-only ASIC on **ihp-sg13g2** (1.2 V core / 3.3 V I/O)

## Status

**Phase 2** (V1 feature RTL + verification hardening). Working RTL under `src/` covers TT wrap, bus keeper / pass-through, QPI engine, and descriptor fetch / copy / chain / cross-device. Cocotb **M0–M5** accepted (M5: 2026-08-16). Manual LibreLane harden closed **1x1 @ 66 MHz** at tapeout **N=5** (**189** DFFs; first audit ~158 was likely N=1). Remaining: CI smoke, firmware / demoboard (M7), gate-level and physical closure. **M4** formal (`FP-*`) is not a V1 freeze gate (D33). Host unused pins tied 0; no ERROR logic (D34).

## See also

- Architecture: [`architecture/00-index.md`](architecture/00-index.md)
- Roadmap / open issues: `roadmap.md`
- Deep agent context: `../llm/`
