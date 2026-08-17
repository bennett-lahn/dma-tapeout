# Verification Strategy

V1 verification is cumulative across three venues. No venue replaces another.

## Venues

- **Simulation** checks QPI transactions, descriptor semantics, same-device and cross-device copies, host arbitration, reset, scoreboards, always-on monitors (including ASIC-versus-PSRAM/SPI bidirectional SIO ownership), randomized stimulus, and modeled timing.
- **Formal** emphasizes control-plane safety with the real `qspi_engine`: reset, state and counter bounds, request stability, chip-select exclusion, atomic bus yield, handshake counts, fixed-head and quit behavior, reachability covers, and bounded local deadlock. Required safety properties use k-induction with proved helper invariants.
- **FPGA hardware validation** loads the synthesizable RTL onto an FPGA that stands in for the ASIC on the same carrier board and MCU the eventual demoboard will use, then runs a high-value `TC-*` regression subset with real MCU firmware and real PSRAM devices before the shuttle commit. It requires adapting or writing new firmware test code, catches firmware/integration bugs no model or formal environment can, and closes no `T-*` row.
- **Closure** uses STA and the demoboard for IHP pad, TT mux, routed net, package, board, electrical, and real-device timing. These results own `T-*`.

Delay-annotated simulation can detect a broken digital prerequisite or zero or negative modeled margin under stated delays. It cannot certify routed delays, process variation, loading, signal integrity, or silicon. A simulation finding, an STA margin finding, and a board measurement are distinct evidence.

## DUT levels

| Level | DUT | Main purpose |
|---|---|---|
| **L0** | `qspi_engine` plus one timed PSRAM model | QPI framing, nibble and edge behavior, handshakes, `Q-LAUNCH`, `Q-RXEDGE`, and timing sweeps |
| **L1** | `tt_um_lahnb_sgdma` plus dual PSRAM models | Primary functional signoff for TCD chains, both same-device and cross-device directions, arbitration, reset, ordered transactions, and final memory |
| **L2** | Gate-level top plus the L1 external environment | Selected final-netlist tests, four-state behavior, reset, output enables, X investigation, and conditional SDF |

Stable selectors are `LEVEL=engine`, `LEVEL=top`, and `LEVEL=gl`.

## Milestone ladder

| Milestone | Exit focus |
|---|---|
| **M0** | Toolchain and one reproducible L1 same-device smoke |
| **M1** | Dual PSRAM model, protocol policing, and behavioral `Q-*` |
| **M2** | Independent reference model, dual-axis scoreboard, directed `TC-*`, and always-on `CHK-*` |
| **M3** | Runtime delay layer, setup and hold sweeps, `Q-LAUNCH`, and `Q-RXEDGE` |
| **M4** | Required `FP-*` safety proofs, helper invariants, covers, and bounded deadlock checks (deferred; may proceed independently) |
| **M5** | Reproducible random regression, `COV-*` closure, and depth sweep from accepted M2/M3 (M4 not a prerequisite) |
| **M6** | Required Icarus L2 subset, X checks, SDF disposition, and `T-*` handoff |
| **M7** | FPGA hardware validation on the carrier board with real MCU firmware, before shuttle commit (firmware bring-up ready once M0-M5 sim gate is met; see roadmap / D30) |

Milestones are cumulative. A required child ID in `fail`, `wip`, or `blocked` prevents its parent milestone from closing.

## Known blockers / residuals after M3

- M3 is `pass` (2026-08-10): delay layer, `Q-LAUNCH` / `Q-RXEDGE` / `Q-CSP` / `Q-CHD` / `Q-TERM`, delay-rerun of `Q-CEM` / `Q-CPH` / `Q-SIO-OWN`, Icarus ≡ Verilator, margin gate, and centralized `PendingLedger` cleanup.
- Physical `T-HZ` and other `T-*` remain STA / demoboard; M3 supplies pre-STA evidence only.
- `Q-LAUNCH` (`Q-LAUNCH`: driven SIO/OE changes only while SCK is low, with modeled setup/hold) applies only while ASIC drives SCK (`asic_sck_oe==1`); grant/park OE clear is not a launch event.
- Follow-ups (not M3 blockers): margin-gate field presence / boundary-pass ≈0; broader `PSRAM_TACLK_NS` sweep if needed; Handshake incomplete-window diagnostic-only; `_pending_start` ignore; no cleanup-only `Q-TERM`; `@tb_test` finally deferred. Closed residual wave: delayed post-rise `Q-RXEDGE` under non-zero `D_OUT_*` (`TC-RXEDGE-RACE-DEVICE-PLANE`). Forced-`rst_n=0` dispose windows with a live CE monitor must declare `reset_truncated=REVIEW` or `REQUIRE` (never default `FORBID`); see checkers index.
- Independent `QspiPinMonitor` is live; pin ADDR23/KNOWN dispose with `via=pin` when the monitor ran (model `Q-*` twins remain the fallback; model-plane dispose contract retained only in pin-disposition).
- Model-plane Z→0 idealization remains (`tb_top` / `tb_engine` float→0).
- CI L1 Icarus smoke job is still open (local smoke is green).
- SDF remains `blocked` until hardening produces a compatible netlist-matched artifact and annotation is qualified.
- The M5 depth sweep / `TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`) / `COV-DEPTH*` (compile-time depth bins from per-run `coverage.json`) runs via `make depth` or `run_depth_sweep.sh` across every integer `1..DMA_BUF_DEPTH_MAX` (8), including tapeout **N=5**; not a cocotb function in `test_dma_directed`. Do not claim those IDs pass until the full loop is green. V1 tapeout and default sim/Make depth is **N=5**.
- M4 formal deferred (may proceed independently; do not claim pass). M5 random / `COV-*` in progress from accepted M2/M3. Wave 4 2026-08-16 partial random evidence at **N=5** / `TIMING_PROFILE=ideal`: Icarus `REGRESSION_SEEDS` 1/2/3/5/8 pass; Verilator seeds 1/2 pass; artifacts `coverage.json` + `stimulus.json` per seed; seed-1 Icarus ≡ Verilator (`CHK-*`/`Q-*` clean). Verilator seeds 3/5/8, full depth `1..8` sweep, and `COV-*` closure remain open.
- Catalog follow-up: BUS_GNT-aware CTRL/HS checkers for MCU pass-through suites.
- M7 FPGA hardware validation has not started.

## Progress and operational lessons

- M0 (`TC-SMOKE`), M1 behavioral exit, M2 (reference / dual-axis scoreboard / 24 directed `TC-*` / always-on `CHK-*`), and M3 (delay / launch / RX / lifecycle) are green. **M5** is the active verification track from accepted M2/M3; Wave 4 2026-08-16 landed partial random green at tapeout **N=5** (Icarus seeds 1/2/3/5/8; Verilator 1/2). M4 formal is deferred and may proceed independently.
- Do not mix nix Icarus into cocotb runs; use `source test/env.sh` (suite wrappers). Raw suite `bin/vvp` breaks `dma-venv` via `PYTHONHOME`.
- PSRAM model must remain SCK/CE#-edge driven with six real dummy cycles - no clk-polling calibration.
- Prefer `make directed` / quoted directed function-name filters; do not use a bare `TEST_FILTER=directed`.

Full strategy and milestone gates: [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md).
