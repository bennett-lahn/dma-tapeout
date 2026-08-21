# Agent Guide

This repository implements **TinyDMA** for Tiny Tapeout on the **IHP SG13G2** shuttle (**TTIHP26b**, **1x1** / one-tile budget). **TinyDMA** targets **dual** QSPI PSRAM as a bulk mover (cross-device OK). Flash on the PMOD is MCU pass-through only. Shipped RTL is this V1 feature set only (no ALU / cond-stop / ring / ASIC flash).

## Context sources

1. **Canonical verbose context:** [`docs/llm/00-index.md`](docs/llm/00-index.md)
2. **Human summaries:** [`docs/human/overview.md`](docs/human/overview.md), architecture under [`docs/human/architecture/`](docs/human/architecture/00-index.md)
3. **Handwritten notes (read-only):** `C:\Users\lahnb\Documents\Obsidian Vault\Projects\Tiny Tapeout\`
4. **Prior art (separate):** [`docs/llm/prior-art/tinydma-2c.md`](docs/llm/prior-art/tinydma-2c.md) - Andrew Kim / TT 296
5. **Local shuttle / PDK clones (supporting, not architecture truth):** `ttihp-verilog-template/`, `IHP-Open-PDK/`
6. **Local LibreLane harden runbook:** [`docs/llm/13-hardening-librelane.md`](docs/llm/13-hardening-librelane.md) (human: [`docs/human/architecture/hardening.md`](docs/human/architecture/hardening.md))

## Prior-art attribution

Anything taken from TinyDMA-2C context must be labeled in-product (chat replies, docs, commit messages) as prior art. Example: "Per TinyDMA-2C (Andrew Kim, TT 296)...". Do not silently merge those details into this project's architecture voice.

Related skill: [`.cursor/skills/tinydma-prior-art/SKILL.md`](.cursor/skills/tinydma-prior-art/SKILL.md)

## Current phase

**Phase 2** - V1 feature RTL is in place under `src/`; cocotb **M0–M5** accepted (M5: 2026-08-16). Manual IHP LibreLane harden closed **1x1 @ 66 MHz** at tapeout **N=5** (**189** DFFs; first audit ~158 was likely N=1; see harden runbook). V1 tile budget is **1x1 only** (D36); `1x2` is out of budget. **M4** formal is not a V1 freeze gate (D33). Then M6-M7 and shuttle closure. Host unused pins tied 0; no ERROR logic (D34). `ptr[23]` don't-care; self-pointing TCD allowed (D35). Process / pad model: **D27** in `docs/llm/07-decision-log.md`.

## Do / don't

- Do define PSRAM timing parameters, testbench timing parameters (`TB_*`, `D_OUT_*`, `D_IN_*`), `TIMING_PROFILE` values, and verification IDs (`Q-*`, `T-*`, `FP-*`, `CHK-*`, `TC-*`, `COV-*`) inline on first use in every response; one short plain-language phrase is enough (see `.cursor/rules/verification-vocabulary.mdc`)
- Do update `docs/llm/` when architectural decisions change
- Do keep `docs/human/` condensed **and complete**: every durable fact in `docs/llm/` must appear in `docs/human/` in some form (summary, table, or bullets). llm elaborates; it is not a private second source of truth
- Do cite TinyDMA-2C explicitly when using that file
- Don't leave new requirements, catalogs, or architecture choices llm-only with a human stub/pointer (see `docs/README.md` parity rule; `CHK-*` checkers are a known bad example)
- Don't edit Obsidian vault files
- Don't copy TinyDMA-2C architecture or RTL
- Don't expand scope past the 1x1 / shuttle reality without an explicit cut plan
- Don't cite sky130 GPIO MHz ratings as binding on this IHP project
