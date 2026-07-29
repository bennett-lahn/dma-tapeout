# Agent Guide

This repository documents and will implement a **descriptor-based scatter-gather DMA** for Tiny Tapeout on the **IHP SG13G2** shuttle (**TTIHP26b**, 2-tile budget) targeting **dual** QSPI PSRAM as a **bulk mover** (cross-device OK). Flash on the PMOD is MCU pass-through only for V1. ALU / cond-stop / ring / ASIC flash are post-V1 (`docs/llm/10-post-v1-features.md`).

## Context sources

1. **Canonical verbose context:** [`docs/llm/00-index.md`](docs/llm/00-index.md)
2. **Human summaries:** [`docs/human/overview.md`](docs/human/overview.md), architecture under [`docs/human/architecture/`](docs/human/architecture/00-index.md)
3. **Handwritten notes (read-only):** `C:\Users\lahnb\Documents\Obsidian Vault\Projects\Tiny Tapeout\`
4. **Prior art (separate):** [`docs/llm/prior-art/tinydma-2c.md`](docs/llm/prior-art/tinydma-2c.md) - Andrew Kim / TT 296
5. **Local shuttle / PDK clones (supporting, not architecture truth):** `ttihp-verilog-template/`, `IHP-Open-PDK/`

## Prior-art attribution

Anything taken from TinyDMA-2C context must be labeled in-product (chat replies, docs, commit messages) as prior art. Example: "Per TinyDMA-2C (Andrew Kim, TT 296)...". Do not silently merge those details into this project's architecture voice.

Related skill: [`.cursor/skills/tinydma-prior-art/SKILL.md`](.cursor/skills/tinydma-prior-art/SKILL.md)

## Current phase

Planning / architecture. Early RTL under `src/rtl/`. Resolve items in `docs/llm/08-open-questions.md` before freezing interfaces. Process / pad model: **D27** in `docs/llm/07-decision-log.md`.

## Do / don't

- Do update `docs/llm/` when architectural decisions change
- Do keep `docs/human/` short
- Do cite TinyDMA-2C explicitly when using that file
- Don't edit Obsidian vault files
- Don't copy TinyDMA-2C architecture or RTL
- Don't expand scope past the 2-tile / shuttle reality without an explicit cut plan
- Don't cite sky130 GPIO MHz ratings as binding on this IHP project
