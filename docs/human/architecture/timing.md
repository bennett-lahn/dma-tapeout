# Timing Analysis

Post-RTL / Phase 3 checklist (after feature-complete RTL, before shuttle freeze). Full tables and status columns: [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md).

## Scope

- Confirm APS6404L QSPI AC timing at **66 MHz `clk` / ≈33 MHz SCK** (SCK = clk/2), **rising-edge RX** (D16)
- Prove CE#↔SCK sequencing in sim; close ns paths in STA + demoboard
- Extensible: add rows/sections in the LLM doc for host pins, internal STA, etc.

## Where

| Venue | Focus |
|---|---|
| Cocotb | `tCEM` / `tCPH` / `tCSP` / `tCHD`, terminate hold (no extra SCK), A/B CE# mux, SCK=clk/2 enable |
| STA (when available) | `tACLK`, `tSP`/`tHD`, clock duty |
| Demoboard | Real board + TT I/O margin at 66 MHz `clk` / 33 MHz SCK |

Engine generates SCK as a registered toggle (not a combo gate of `clk`). Order CE# (assert before first rise; hold after last rise; idle gap for `tCPH`).

## Related

- Limits: [`limitations.md`](limitations.md)
- QSPI engine: [`blocks/qspi-engine.md`](blocks/qspi-engine.md)
- Roadmap Phase 3: [`../roadmap.md`](../roadmap.md)
