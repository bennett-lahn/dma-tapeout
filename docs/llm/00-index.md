# LLM Context Index

This directory is the canonical, verbose project context for AI agents working across conversations. Prefer these files over chat history. Do not modify Obsidian vault notes; treat them as read-only source material.

## Reading order (new conversation)

1. `01-project-brief.md` - what we are building and why
2. `02-constraints.md` - hard limits (tiles, DFFs, I/O, shuttle)
3. `03-architecture.md` - block-level system architecture
4. `04-tcd-and-datapath.md` - V1 descriptors / FSM (11-byte TCD; device flags in `CTRL_FLAGS`; `QUIT` flag)
5. `05-qspi-psram.md` - external memory protocol reality
6. `06-system-use-case.md` - dual-PSRAM bulk-mover framing
7. `12-firmware.md` - demoboard MicroPython firmware (SPI / TCD / M7; twin of human `architecture/firmware.md`)
8. `07-decision-log.md` - alternatives rejected and why DMA won
9. `08-open-questions.md` - unresolved design choices
10. `09-references.md` - links and external notes
11. `10-post-v1-features.md` - add-later: ALU, cond-stop, ring, flash
12. `11-timing-analysis.md` - post-RTL timing checklist (PSRAM QSPI AC; extensible)
13. `13-hardening-librelane.md` - local LibreLane / Nix harden runbook (TTIHP26b, `ttihp-verilog-template`, area audits)
14. `verification/00-index.md` - verification strategy, cocotb platform, timed PSRAM model, scoreboards, checkers, formal, coverage, and gate-level/X closure
15. `prior-art/tinydma-2c.md` - **separate** TinyDMA-2C prior-art dump (optional; read only when comparing)

## Companion docs

- Human-facing (condensed, complete): `../human/` (architecture split under `../human/architecture/`)
- Local harden (condensed): `../human/architecture/hardening.md` (twin of `13-hardening-librelane.md`)
- Verification (verbose): `verification/00-index.md` (M0-M6 ladder; simulation, formal, and physical-closure handoff)
- Verification (condensed): `../human/verification/00-index.md`
- **Parity:** durable facts here must also appear in `../human/` in some form. llm elaborates; it is not a private source of truth. See `../README.md`. Known debt: `verification/06-checkers.md` (`CHK-*`) is still mostly llm-only.
- Datasheets (PDF + converted markdown): `../datasheets/` (see `../datasheets/README.md` for conversion)
- Handwritten notes (read-only, outside repo):
  - `C:\Users\lahnb\Documents\Obsidian Vault\Projects\Tiny Tapeout\`
- Project skill for prior-art attribution: `.cursor/skills/tinydma-prior-art/SKILL.md`

## Agent rules of engagement

- Project status: **Phase 2** (V1 feature RTL + verification hardening). Working RTL under `src/rtl/` (`top.v`, `sys_controller.sv`, `qspi_engine.sv`, `types.svh`). Simulation exits **M0–M3** accepted (M3: 2026-08-10). Manual LibreLane harden on IHP has closed **1x1 @ 66 MHz** (see `13-hardening-librelane.md`). **M5** (randomized regression, `COV-*` functional coverage closure, buffer-depth sweep) is open but has Wave 4 2026-08-16 partial random evidence at tapeout **N=5** / `TIMING_PROFILE=ideal` (Icarus seeds 1/2/3/5/8, Verilator 1/2; seed-1 Icarus ≡ Verilator); `TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`), full depth `1..8` sweep, Verilator seeds 3/5/8, and `COV-*` merge/closure remain open - do not claim M5 exit. **M4** formal (`FP-*`) may proceed independently later - do not claim M4 pass. V1 tapeout `DMA_BUF_DEPTH` is **N=5** (default sim/Make depth 5; elaboration 1..`DMA_BUF_DEPTH_MAX` (8) via Makefile `-G`/`-P`). Remaining V1 opens: `uo_out[7:2]` / error model, then M6–M7 and shuttle closure. Interfaces follow `docs/llm/` + human architecture.
- Start from scratch. TinyDMA-2C is prior art only; do not copy its architecture or RTL.
- **Attribution:** anything drawn from `prior-art/tinydma-2c.md` (or TinyDMA-2C generally) must be labeled explicitly in the reply as coming from that prior art. Never present it as this project's frozen design.
- Optimize for DFF count and routing congestion before feature richness.
- Prefer shipping a smaller, verified V1 over a feature-rich late design.
- When proposing features, state DFF / tile impact explicitly.
- Keep SystemVerilog style aligned with user rules (leading commas in ports/instantiations, sync active-low `rst_n`).
