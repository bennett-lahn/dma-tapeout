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
| **M4** | Required `FP-*` safety proofs, helper invariants, covers, and bounded deadlock checks |
| **M5** | Reproducible random regression, `COV-*` closure, and depth sweep |
| **M6** | Required Icarus L2 subset, X checks, SDF disposition, and `T-*` handoff |
| **M7** | FPGA hardware validation on the carrier board with real MCU firmware, before shuttle commit (firmware bring-up ready once M0-M5 sim gate is met; see roadmap / D30) |

Milestones are cumulative. A required child ID in `fail`, `wip`, or `blocked` prevents its parent milestone from closing.

## Known blockers

- `Q-LAUNCH` and `Q-RXEDGE` remain `todo`: the M3 harness has not yet executed them against current RTL.
- M3 also owns delay-annotated reruns of `Q-CEM` / `Q-CPH` / `Q-SIO-OWN` and `Q-CSP` / `Q-CHD` / `Q-TERM`.
- Independent `QspiPinMonitor` is still a stub; pin ADDR23/KNOWN dispose via model IDs for now (M2 path).
- Model-plane Z→0 idealization remains (`tb_top` / `tb_engine` float→0).
- CI L1 Icarus smoke job is still open (local smoke is green).
- SDF remains `blocked` until hardening produces a compatible netlist-matched artifact and annotation is qualified.
- The M5 `DMA_BUF_DEPTH=1,2,4,8` sweep needs the sim harness to select the module parameter. RTL default remains depth 1 (V1 tapeout).
- M7 FPGA hardware validation has not started.

## Progress and operational lessons

- M0 (`TC-SMOKE`) and M1 behavioral exit are green (Icarus ≡ Verilator on the directed protocol set under `ideal`). Next milestone is M2 reference / scoreboard / directed `TC-*`.
- Do not mix nix Icarus into cocotb runs; use `source test/env.sh` (suite wrappers). Raw suite `bin/vvp` breaks `dma-venv` via `PYTHONHOME`.
- PSRAM model must remain SCK/CE#-edge driven with six real dummy cycles - no clk-polling calibration.

Full strategy and milestone gates: [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md).
