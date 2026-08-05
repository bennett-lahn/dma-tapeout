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
13. `verification/00-index.md` - verification strategy, cocotb platform, timed PSRAM model, scoreboards, checkers, formal, coverage, and gate-level/X closure
14. `prior-art/tinydma-2c.md` - **separate** TinyDMA-2C prior-art dump (optional; read only when comparing)

## Companion docs

- Human-facing (condensed, complete): `../human/` (architecture split under `../human/architecture/`)
- Verification (verbose): `verification/00-index.md` (M0-M6 ladder; simulation, formal, and physical-closure handoff)
- Verification (condensed): `../human/verification/00-index.md`
- **Parity:** durable facts here must also appear in `../human/` in some form. llm elaborates; it is not a private source of truth. See `../README.md`. Known debt: `verification/06-checkers.md` (`CHK-*`) is still mostly llm-only.
- Datasheets (PDF + converted markdown): `../datasheets/` (see `../datasheets/README.md` for conversion)
- Handwritten notes (read-only, outside repo):
  - `C:\Users\lahnb\Documents\Obsidian Vault\Projects\Tiny Tapeout\`
- Project skill for prior-art attribution: `.cursor/skills/tinydma-prior-art/SKILL.md`

## Agent rules of engagement

- Project status: **architecture / planning**. Early RTL exists under `src/rtl/` (QSPI package + engine skeleton); interfaces still follow `docs/llm/` + human architecture.
- Start from scratch. TinyDMA-2C is prior art only; do not copy its architecture or RTL.
- **Attribution:** anything drawn from `prior-art/tinydma-2c.md` (or TinyDMA-2C generally) must be labeled explicitly in the reply as coming from that prior art. Never present it as this project's frozen design.
- Optimize for DFF count and routing congestion before feature richness.
- Prefer shipping a smaller, verified V1 over a feature-rich late design.
- When proposing features, state DFF / tile impact explicitly.
- Keep SystemVerilog style aligned with user rules (leading commas in ports/instantiations, sync active-low `rst_n`).
