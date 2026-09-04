# Roadmap

**Current focus (2026-08):** Phase 2. V1 feature RTL is in place (shipped RTL scope only); cocotb **M0–M5** simulation exits accepted (M5: 2026-08-16). **M4** formal (`FP-*`) is not a V1 freeze gate (D33). Tapeout `DMA_BUF_DEPTH` is **N=5**. Remaining checklist: CI smoke, M6–M7, firmware bring-up, re-harden at N=5. Host unused pins and no ERROR logic are frozen (D34). Phase 0–1 are complete.

## Phase 0 - Planning

- [x] Choose DMA over hash
- [x] Capture dual documentation set (`docs/human`, `docs/llm`)
- [x] Dual-PSRAM in scope; ASIC flash out of V1 (MCU pass-through only) - D11
- [x] V1 cut: bulk mover only; no ALU / cond-stop / ring / ASIC flash in shipped RTL - D12
- [x] Freeze TCD layout + zero-length rules (`CTRL_FLAGS` byte; later encoding revised by D19) - D13
- [x] Freeze pin/host protocol behavior (idle / START / DONE / pass-through) - D14
- [x] Freeze SPI vs QPI for data path (QPI data) - D15
- [x] Freeze fixed head at `0x000000`/PSRAM0; address 0 valid (ABORT pin later revoked by D23) - D18
- [x] Freeze `ptr[23]` device select + `QUIT` end-of-chain (later revised by D24 for flag device selects) - D19
- [x] Freeze clock / RX sample policy - **66 MHz `clk`**, **SCK=clk/2**, rising-edge RX (D16)
- [x] Freeze MCU-owned enter/exit QPI; ASIC QPI opcodes `0xEB` / `0x02` only - D17
- [x] Freeze pass-through request/grant: `ui_in[2]=BUS_REQ`, `uo_out[1]=BUS_GNT` (MCU priority; atomic QPI) - D22
- [x] Revoke ABORT; kill via `rst_n`; quit → IDLE then next START from addr 0 - D23
- [x] Device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`); 11-byte / 88-bit memory TCD unchanged - D24 (working set later 88 DFFs; reserved latched, D31)

## Phase 1 - Skeleton RTL

- [x] Tiny Tapeout wrapper + pin map
- [x] Pass-through mux + mode control (ASIC bus keeper while `~BUS_GNT`; flash CS parked high / never low; `BUS_REQ`/`BUS_GNT`)
- [x] QSPI engine (QPI `0xEB` read / `0x02` write, CE# time limit, **RAM A/B CS mux**; no ASIC enter/exit quad)
- [x] Working register file (11-byte TCD fields)

## Phase 2 - Descriptor DMA (V1 feature complete) (current)

Feature path (fetch / copy / chain / cross-device) and verification through M3 are done. Remaining Phase 2 work is hardening and unfinished V1 polish, not skeleton RTL.

- [x] Fetch + single-TCD copy (same-device PSRAM) - M0 `TC-SMOKE` plus M2 length/address corners
- [x] Cross-device PSRAM copy (A↔B) - M2 `TC-CROSS-01` / `TC-CROSS-10`
- [x] Chained TCDs (incl. next TCD on other device; zero-length no-op) - M2 `TC-CHAIN` / `TC-NEXT-DEVICE` / `TC-EMPTY` / `TC-QUIT`
- [x] DONE / BUS_GNT status (`uo_out[0]`/`[1]` exercised by M2 directed; unused `uo_out[7:2]` tied 0 per D34)
- [x] M0 - toolchain and L1 same-device smoke (`source test/env.sh && test/scripts/run_smoke.sh`)
- [x] Verification scaffold + durable toolchain hooks (`test/env.sh`, wrappers, doctor/run scripts)
- [x] SCK-accurate dual PSRAM model (6 dummy cycles; table-driven `0xEB`/`0x02`) with M1 protocol policing
- [x] M1 - protocol policing, L0 QPI directed tests, and Icarus/Verilator agreement (behavioral `Q-*` under `ideal`; residual: CI smoke still open; delays / `Q-LAUNCH` / `Q-RXEDGE` closed at M3; Z-to-0 overlay retired)
- [x] M2 - reference model, dual-axis scoreboard, directed `TC-*` (24 cases; `TC-DEPTH` deferred to M5), always-on `CHK-*`, pin monitor (`via=pin`); Acceptance 2026-08-08
- [x] M3 - delay layer, setup/hold sweeps, launch/RX edge checks, centralized pending-item lifecycle; Acceptance 2026-08-10 (physical `T-*` remain post-M3; residual-wave TB fixes for device-plane `Q-RXEDGE` race + Verilator `Q-LAUNCH` `asic_sck_oe` gate landed without reopening M3)
- [x] M5 - randomized regression and coverage closure; buffer-depth sweep - rematch **2026-08-25:** `test/runs/m5_coverage_closure.json` `closed=true` (reviewer `tb-closure-2026-08-25`); `TC-DEPTH` Icarus 15/15 per N=1..8
- [ ] CI smoke job (L1 Icarus)
- [ ] Firmware library + demoboard bring-up for M7 readiness (MicroPython under `firmware/`; host-side `firmware/tests` may start earlier; demoboard HIL still Phase 3 / M7)

## Phase 3 - Demoboard + hardening

Once the cocotb/RTL verification milestones that gate M7 entry are complete, **FPGA testing must be ready to run**. That requires demoboard/FPGA bring-up including firmware, so the firmware library and bring-up work above is allowed and needed before or as M7 starts (not deferred until after M7). Host-side unit logic under `firmware/tests/` can start earlier; demoboard HIL remains M7.

- [ ] RP2 demoboard scripts (bulk A↔B / scatter-gather patterns) - firmware bring-up complete enough that M7 can start
- [x] Area/DFF audit vs 1-tile / **1x1** budget - manual LibreLane: **158** DFFs; **1x1 @ 66 MHz** closes (2026-08); keep re-auditing after RTL growth (D36; [`architecture/hardening.md`](architecture/hardening.md))
- [ ] M6 - gate-level and X checks, then hand remaining physical `T-*` rows to STA and demoboard closure
- [ ] M7 - FPGA hardware validation: load synthesizable RTL on an FPGA standing in for the ASIC on the same carrier board and MCU, run firmware-driven high-value hardware regression before freezing RTL for shuttle
- [ ] Close **66 MHz `clk` / 33 MHz SCK** / rising-edge RX (D16 / D27): use delay-annotated simulation for `Q-*` pre-checks, STA for IHP pad + TT mux margin, and the demoboard for final number validity; see [`verification/strategy.md`](verification/strategy.md), [`architecture/timing.md`](architecture/timing.md), and [`../llm/verification/04-timing-in-sim.md`](../llm/verification/04-timing-in-sim.md)
- [ ] CI + GDS flow green
- [ ] Freeze RTL for shuttle

## Shipped RTL scope

This V1 feature set is the only RTL that ships. Historical cut decisions (ALU / cond-stop / ring / ASIC flash contemplated and rejected for silicon) remain in [`../llm/07-decision-log.md`](../llm/07-decision-log.md) (D11/D12). Formal M4 (`FP-*`) is not a V1 freeze gate (D33); do not claim pass.

## V1 cut guidance

Ship order if schedule slips:

1. Pass-through + QPI + single TCD copy on **RAM A**
2. **RAM B** CS + same-device B + **cross-device** A↔B
3. Chaining
4. Status polish / `rst_n` kill language (D23; unused host bits tied 0, D34)

Do not expand RTL beyond this bulk A↔B mover to “save” the interview story.

## Verification roadblocks (lessons)

Keep these out of the next milestones; details in the verification execution plan:

1. **Do not mix nix Icarus with cocotb** - use OSS CAD Suite via `test/env.sh`; harden with Docker/Nix LibreLane in a separate shell (runbook: [`architecture/hardening.md`](architecture/hardening.md)).
2. **Never call raw suite `bin/vvp`** - use wrappers from `env.sh` (`PYTHONHOME` breaks `dma-venv` / cocotb).
3. **PSRAM model must stay SCK/CE#-driven** - no clk-polling or dummy-count calibration hacks.
4. **Agents run only `test/scripts/*.sh`** - avoid brittle one-liners and `/tmp` logs.
5. **Wipe `test/sim_build/`** after Icarus/wrapper changes.

## Planned housekeeping (not a shuttle freeze gate)

Engineering hygiene. Does not reopen M0–M5, grow RTL, or block shuttle freeze by itself. Detail: [`architecture/firmware.md`](architecture/firmware.md), [`../llm/12-firmware.md`](../llm/12-firmware.md), [`../llm/verification/02-platform.md`](../llm/verification/02-platform.md).

- [x] **Centralize constants.** Duplicated locals in the cocotb testbench (`test/`) and demoboard firmware (`firmware/`) live in three leaf modules: `firmware/constants.py`, `test/reference/constants.py` (mechanical twin of the overlapping architecture subset), and `test/common/constants.py` (sim-only). Firmware still must not import `test/` (D30).
- [ ] **Complete function comments, plus a repo commenting standard.** Review and update testbench and firmware docs and source so every function has a complete comment. As part of that same change, write a commenting standard for the rest of the repo (Python first; SystemVerilog and shell follow the same intent on later edits).
- [x] **Centralize testbench interaction and make output easier to read.** Cocotb tests call `common.runlog.begin_run` for `REPRO` / SEED banners. Passing dispose output is a compact summary; a fail still prints every catalog ID. Does not change checker semantics.

## Open questions

Tracked in detail at `../llm/08-open-questions.md`. Remaining V1 lean:

- Multi-outstanding transactions (no)
