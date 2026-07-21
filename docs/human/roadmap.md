# Roadmap

## Phase 0 - Planning (current)

- [x] Choose DMA over hash
- [x] Capture dual documentation set (`docs/human`, `docs/llm`)
- [x] Dual-PSRAM in scope; ASIC flash out of V1 (MCU pass-through only) - D11
- [x] V1 cut: bulk mover only; ALU / cond-stop / ring / flash → post-V1 - D12
- [x] Freeze TCD device-select packing (`CTRL_FLAGS` SRC/DEST/NEXT_DEV) and null/zero-length rules - D13
- [x] Freeze pin/host protocol behavior (idle / START / DONE / abort / pass-through) - D14
- [x] Freeze SPI vs QPI for V1 data path (QPI data; SPI config only) - D15
- [ ] Freeze ABORT / head-pointer pin packing on `ui_in[7:1]`
- [ ] Freeze QPI read opcode (`0x0B` vs `0xEB`) and clock / RX sample policy

## Phase 1 - Skeleton RTL

- [ ] Tiny Tapeout wrapper + pin map
- [ ] Pass-through mux + mode control (flash CS always OE-off from ASIC; pass-through iff DONE)
- [ ] QSPI engine (SPI config bring-up if needed, QPI read/write, CE# time limit, **RAM A/B CS mux**)
- [ ] Working register file (11-byte TCD fields)

## Phase 2 - Descriptor DMA (V1 feature complete)

- [ ] Fetch + single-TCD copy (same-device PSRAM)
- [ ] Cross-device PSRAM copy (A↔B)
- [ ] Chained TCDs (incl. next TCD on other die; zero-length no-op)
- [ ] DONE / abort / error status
- [ ] Cocotb BFM for dual PSRAM + host protocol (flash model optional / MCU-side only)

## Phase 3 - Demoboard + hardening

- [ ] RP2 demoboard scripts (bulk A↔B / scatter-gather patterns)
- [ ] Area/DFF audit vs 2-tile budget
- [ ] Timing/clock policy freeze
- [ ] CI + GDS flow green
- [ ] Freeze RTL for shuttle

## Post-V1 (explicitly after V1 / only if budget remains)

Documented in [`architecture/post-v1.md`](architecture/post-v1.md) / [`../llm/10-post-v1-features.md`](../llm/10-post-v1-features.md):

1. In-flight byte ALU
2. Conditional stop (`COND_STOP`)
3. Ring / modulo addressing
4. ASIC flash read, then maybe write

## V1 cut guidance

Ship order if schedule slips:

1. Pass-through + QPI + single TCD copy on **RAM A**
2. **RAM B** CS + same-device B + **cross-device** A↔B
3. Chaining
4. Abort / status polish

Do not pull post-V1 items above chaining to “save” the interview story - the bulk A↔B demo is enough.

## Open questions

Tracked in detail at `../llm/08-open-questions.md`. Biggest remaining V1 decisions:

- MCU vs ASIC memory initialization (both PSRAM dies)
- ABORT / head-pointer / status pin packing
- QPI read opcode + clock / RX sample edge
- Error model sticky bits; self-pointing TCD policy
