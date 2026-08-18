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
| **M4** | Required `FP-*` safety proofs, helper invariants, covers, and bounded deadlock checks (deferred, D33; not a V1 freeze gate; do not claim pass) |
| **M5** | Reproducible random regression, `COV-*` closure, and depth sweep from accepted M2/M3 (M4 not a prerequisite) |
| **M6** | Required Icarus L2 subset, X checks, SDF disposition, and `T-*` handoff |
| **M7** | FPGA hardware validation on the carrier board with real MCU firmware, before shuttle commit (firmware bring-up ready once M0-M5 sim gate is met; see roadmap / D30) |

Milestones are cumulative. A required child ID in `fail`, `wip`, or `blocked` prevents its parent milestone from closing.

## Known blockers / residuals after M3

- M3 is `pass` (2026-08-10): delay layer, `Q-LAUNCH` / `Q-RXEDGE` / `Q-CSP` / `Q-CHD` / `Q-TERM`, delay-rerun of `Q-CEM` / `Q-CPH` / `Q-SIO-OWN`, Icarus ≡ Verilator, margin gate, and centralized `PendingLedger` cleanup.
- Physical `T-HZ` and other `T-*` remain STA / demoboard; M3 supplies pre-STA evidence only.
- `Q-LAUNCH` (`Q-LAUNCH`: driven SIO/OE changes only while SCK is low, with modeled setup/hold) applies only while ASIC drives SCK (`asic_sck_oe==1`); grant/park OE clear is not a launch event.
- Follow-ups (not M3 blockers): margin-gate field presence / boundary-pass ≈0; broader `PSRAM_TACLK_NS` sweep if needed; Handshake incomplete-window diagnostic-only; `_pending_start` ignore; no cleanup-only `Q-TERM`; `@tb_test` finally deferred. Closed residual wave: delayed post-rise `Q-RXEDGE` under non-zero `D_OUT_*` (`TC-RXEDGE-RACE-DEVICE-PLANE`). Forced-`rst_n=0` dispose windows with a live CE monitor must declare `reset_truncated=REVIEW` or `REQUIRE` (never default `FORBID`); see checkers index.
- Independent `QspiPinMonitor` is live; pin KNOWN dispose with `via=pin` when the monitor ran (`CHK-PIN-ADDR23-ZERO` retired by D35; model `Q-SIO-X` twin remains the fallback; model-plane dispose contract retained only in pin-disposition).
- Model-plane Z→0 idealization remains (`tb_top` / `tb_engine` float→0).
- CI L1 Icarus smoke job is still open (local smoke is green).
- SDF remains `blocked` until hardening produces a compatible netlist-matched artifact and annotation is qualified. A zero-delay functional gate run is not an SDF pass.
- L2 entry: `source test/env.sh && test/scripts/run_gl.sh` (or `GATES=yes make` / `make gl_test` from `test/`). Copies the unpowered N=5 `nl` view to `test/gate_level_netlist.v`, requires IHP cell models (`PDK_ROOT`), and runs `tests.test_gate_level`. The wrapper does not override netlist `DMA_BUF_DEPTH`; Makefile `-Ptb_gl.DMA_BUF_DEPTH` is not used. Missing netlist or PDK is `blocked`, not a pass.
- `TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`) **pass** for N=1..8 (2026-08-16, Icarus 13/13 per depth via `make depth` / `run_depth_sweep.sh`). `COV-DEPTH*` (compile-time depth bins from per-run `coverage.json`) is present in the closed `COV-*` merge. V1 tapeout and default sim/Make depth is **N=5**.
- M4 formal is deferred (D33; do not claim pass). **M5 exit / pass (2026-08-16):** random regression and `COV-*` (functional coverage point IDs) closure at **N=5** / `TIMING_PROFILE=ideal` (zero TB placeholders); Icarus and Verilator seeds 1/2/3/5/8; seed-1 Icarus ≡ Verilator; `TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`) **pass** N=1..8; merge at `test/runs/m5_coverage_closure.json` `closed=true` (20 catalog IDs; 13 exclusions STALL + length-class collapse N=1/2; reviewer `M5-close`, 2026-08-16).
- Catalog follow-up: BUS_GNT-aware CTRL/HS checkers for MCU pass-through suites.
- M7 FPGA hardware validation has not started.

## Progress and operational lessons

- M0 (`TC-SMOKE`), M1 behavioral exit, M2 (reference / dual-axis scoreboard / 24 directed `TC-*` / always-on `CHK-*`), M3 (delay / launch / RX / lifecycle), and **M5** (random / `COV-*` / depth sweep, exit 2026-08-16) are green. M4 formal is deferred (D33).
- Do not mix nix Icarus into cocotb runs; use `source test/env.sh` (suite wrappers). Raw suite `bin/vvp` breaks `dma-venv` via `PYTHONHOME`.
- PSRAM model must remain SCK/CE#-edge driven with six real dummy cycles - no clk-polling calibration.
- Prefer `make directed` / quoted directed function-name filters; do not use a bare `TEST_FILTER=directed`.

Full strategy and milestone gates: [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md).
