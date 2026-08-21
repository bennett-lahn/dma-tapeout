---
name: tinydma-prior-art
description: >-
  Use when discussing TinyDMA-2C, TT project 296, Andrew Kim's DMA, prior-art
  SPI PSRAM DMA pinouts/protocols, or comparing this repo's design to that
  reference. Enforces explicit attribution and no-copy rules.
---

# TinyDMA-2C Prior Art Skill

## When this applies

Any time the agent reads or uses `docs/llm/prior-art/tinydma-2c.md`, or otherwise reasons from TinyDMA-2C / TT 296 / Andrew Kim's two-channel SPI PSRAM DMA.

## Mandatory attribution

If a statement, recommendation, pin idea, protocol detail, width choice, verification idea, or feasibility claim is drawn from that prior-art context, the response **must explicitly say so**.

Good:

- "Per TinyDMA-2C (Andrew Kim, TT 296)..."
- "TinyDMA-2C prior art used `uio_oe = 8'b0011_1000`..."
- "Drawing from the TinyDMA-2C config protocol (not our frozen design)..."

Bad:

- Stating TinyDMA pin/protocol details as if they are this project's decisions
- Silently reusing prior-art numbers/widths without a source label

## Hard limits

- Do not copy TinyDMA-2C RTL, module structure, or microarchitecture into this repo
- Treat the prior-art file as a separate context from `docs/llm/01` through `09`
- Prefer contrasting with **TinyDMA** rather than adopting TinyDMA-2C as the default architecture

## Source file

Canonical prior-art dump: `docs/llm/prior-art/tinydma-2c.md`
