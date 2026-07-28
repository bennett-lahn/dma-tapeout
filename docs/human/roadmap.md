# Roadmap

## Phase 0 - Planning (current)

- [x] Choose DMA over hash
- [x] Capture dual documentation set (`docs/human`, `docs/llm`)
- [x] Dual-PSRAM in scope; ASIC flash out of V1 (MCU pass-through only) - D11
- [x] V1 cut: bulk mover only; ALU / cond-stop / ring / flash → post-V1 - D12
- [x] Freeze TCD layout + zero-length rules (`CTRL_FLAGS` byte; later encoding revised by D19) - D13
- [x] Freeze pin/host protocol behavior (idle / START / DONE / pass-through) - D14
- [x] Freeze SPI vs QPI for V1 data path (QPI data) - D15
- [x] Freeze fixed head at `0x000000`/PSRAM0; address 0 valid (ABORT pin later revoked by D23) - D18
- [x] Freeze `ptr[23]` device select + `QUIT` end-of-chain (later revised by D24 for flag device selects) - D19
- [x] Freeze clock / RX sample policy - **66 MHz `clk`**, **SCK=clk/2**, rising-edge RX (D16)
- [x] Freeze MCU-owned enter/exit QPI; ASIC QPI opcodes `0xEB` / `0x02` only - D17
- [x] Freeze pass-through request/grant: `ui_in[2]=BUS_REQ`, `uo_out[1]=BUS_GNT` (MCU priority; atomic QPI) - D22
- [x] Revoke ABORT; kill via `rst_n`; quit → IDLE then next START from addr 0 - D23
- [x] Device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`); 88-bit TCD unchanged - D24

## Phase 1 - Skeleton RTL

- [ ] Tiny Tapeout wrapper + pin map
- [ ] Pass-through mux + mode control (flash CS always OE-off from ASIC; `BUS_REQ`/`BUS_GNT`)
- [ ] QSPI engine (QPI `0xEB` read / `0x02` write, CE# time limit, **RAM A/B CS mux**; no ASIC enter/exit quad)
- [ ] Working register file (11-byte TCD fields)

## Phase 2 - Descriptor DMA (V1 feature complete)

- [ ] Fetch + single-TCD copy (same-device PSRAM)
- [ ] Cross-device PSRAM copy (A↔B)
- [ ] Chained TCDs (incl. next TCD on other device; zero-length no-op)
- [ ] DONE / error status
- [ ] Cocotb BFM for dual PSRAM + host protocol (flash model optional / MCU-side only)

## Phase 3 - Demoboard + hardening

- [ ] RP2 demoboard scripts (bulk A↔B / scatter-gather patterns)
- [ ] Area/DFF audit vs 2-tile budget
- [ ] Re-check hardware constraints against **66 MHz `clk` / 33 MHz SCK** / rising-edge RX (D16) per [`architecture/timing.md`](architecture/timing.md) / [`../llm/11-timing-analysis.md`](../llm/11-timing-analysis.md); drop clock or revisit sample edge only if that review fails
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

- Status / DFT packing on `uo_out[7:2]`
- Error model sticky bits; self-pointing TCD policy
