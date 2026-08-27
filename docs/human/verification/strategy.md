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
| **L0** | `qspi_engine` plus one timed PSRAM model | QPI framing, nibble and edge behavior, handshakes, `Q-LAUNCH`, `Q-RXEDGE`, and timing sweeps. CE# monitor uses profile `tCEM` (max CE# low) / `tCPH` (min CE# high). ASIC L0 `Q-CSP`/`Q-CHD`/`Q-TERM` evidence is `TC-QPI-ASIC-CE-TIMING`; MCU pass-through isolation stays in `test_qspi_timing_delay`. |
| **L1** | `tt_um_lahnb_sgdma` plus dual PSRAM models | Primary functional signoff for TCD chains, both same-device and cross-device directions, arbitration, reset, ordered transactions, and final memory |
| **L2** | Gate-level top plus the L1 external environment | Selected final-netlist tests, four-state behavior, reset, output enables, X investigation, and conditional SDF |

Stable selectors are `LEVEL=engine`, `LEVEL=top`, and `LEVEL=gl`.

## Milestone ladder

| Milestone | Exit focus |
|---|---|
| **M0** | Toolchain and one reproducible L1 same-device smoke |
| **M1** | dual-PSRAM model, protocol policing, and behavioral `Q-*` |
| **M2** | Independent reference model, dual-axis scoreboard, directed `TC-*`, and always-on `CHK-*` |
| **M3** | Runtime delay layer, setup and hold sweeps, `Q-LAUNCH`, and `Q-RXEDGE` |
| **M4** | Required `FP-*` safety proofs, helper invariants, covers, and bounded deadlock checks (deferred, D33; not a V1 freeze gate; do not claim pass) |
| **M5** | Reproducible random regression, `COV-*` closure, and depth sweep from accepted M2/M3 (M4 not a prerequisite) |
| **M6** | Required Icarus L2 subset, X checks, SDF disposition, and `T-*` handoff |
| **M7** | FPGA hardware validation on the carrier board with real MCU firmware, before shuttle commit (firmware bring-up ready once M0-M5 sim gate is met; see roadmap / D30) |

Milestones are cumulative. A required child ID in `fail`, `wip`, or `blocked` prevents its parent milestone from closing.

The sim oracle (`test/reference/`) accepts `dma_buf_depth` **1..8** and a 65536-transaction budget (worst-case N=1). L1/L2 scoreboard compare requires observed memory; L0 may omit it. The legal-chain generator uses a dedicated `next_device` entropy stream, separate from source/destination `devices` (expected seed-stream drift). Directed overlap length is compile `N+1` so every depth has a multi-chunk case. `ptr[23]` / `A[23]` remain don't-care (D35); dest-device-1 vectors with bit 23 set mask to `A[22:0]`. Directed DMA device/count checks use the pin transaction log (fetch vs data kinds stay oracle-aligned). `COV-DEPTH` is compile-time N, not window count. IDLE+GNT is unreachable (D22: IDLE plus `BUS_REQ` enters STALL before grant). Host START cannot be 2-flop-scheduled into one-cycle `NEW_FETCH` (the WRITE wrap-up slot lands in `UPDATE`); START-in-NEW_FETCH still shares the D22 ignore path. DMA directed is happy-path; QSPI suites own protocol negatives; reset/bus owns mid-run `rst_n`.

## Known blockers / residuals after M3

- M3 is `pass` (2026-08-10): delay layer, `Q-LAUNCH` / `Q-RXEDGE` / `Q-CSP` / `Q-CHD` / `Q-TERM`, delay-rerun of `Q-CEM` / `Q-CPH` / `Q-SIO-OWN`, Icarus ≡ Verilator, margin gate, and centralized `PendingLedger` cleanup.
- Physical `T-HZ` and other `T-*` remain STA / demoboard; M3 supplies pre-STA evidence only.
- `Q-LAUNCH` (`Q-LAUNCH`: driven SIO/OE changes only while SCK is low, with modeled setup/hold) applies only while ASIC drives SCK (`asic_sck_oe==1`); grant/park OE clear is not a launch event. L0 missing SCK OE is engine-always-owns-SCK. Zero-event `Q-LAUNCH` is `na`.
- `Q-RXEDGE` (`Q-RXEDGE`: each launched read nibble captured on the following rising SCK) at L1 uses the armed rising SCK as capture (no `rdata_valid` alias) and still applies `tACLK`. Write-only / no timed stream is `na`/`blocked`, not pass.
- Follow-ups (not M3 blockers): margin-gate field presence / boundary-pass ≈0; broader `PSRAM_TACLK_NS` sweep if needed; Open handshake txn at dispose fails RDATA/WDATA counts; `_pending_start` fails `CHK-CTRL-FETCH-HEAD`; reset abort of either is `RESET-TRUNCATED`; no cleanup-only `Q-TERM`; `@tb_test` finally deferred. Closed residual wave: delayed post-rise `Q-RXEDGE` under non-zero `D_OUT_*` (`TC-RXEDGE-RACE-DEVICE-PLANE`). Forced-`rst_n=0` dispose windows with a live CE monitor must declare `reset_truncated=REVIEW` or `REQUIRE` (never default `FORBID`); see checkers index. `Q-PHASE` (CE# rose before command/address completed) fails on CE# rise with nibble counts; incomplete-window is dispose/stop of a still-open frame only. Unresolved CE# (X/Z) with a live frame terminates as fail (not `RESET-TRUNCATED`); unresolved SCK (1→Z is not a fall) while CE# is known-low does not drop the transaction. Parser SCK gates on delayed device-plane CE#, not live DUT CE#.
- Independent `QspiPinMonitor` is live; pin KNOWN dispose with `via=pin` when the monitor ran (`CHK-PIN-ADDR23-ZERO` retired by D35). Twin dispose rows: `Q-MUX` / `CHK-PIN-CS-MUTEX`, `Q-SIO-OWN` / `CHK-PIN-SIO-OWN`, `Q-SCKIDLE` / `CHK-PIN-SCK-PARK`, `Q-SIO-X` / `CHK-PIN-KNOWN`. L0 `pin_monitor=False` leaves `CHK-PIN-KNOWN` and pin `Q-SIO-X` as `na` (not a tautological model map). Model-plane `Q-SIO-X` evidence without claiming CHK is retained in pin-disposition.
- Physical SIO/SCK float is Z (`tb_top` / `tb_engine` / `tb_gl` share `tb_uio_bus.svh`; no Z-to-0 overlay). `Q-SIO-X` (SIO must not be X when sampled in a host-driven phase) is host-driven only; legal read dummy/data float does not fire it. `Q-SCKIDLE` (SCK idle low while deselected) uses physical `bus_sck` plus OE: OE=0 + Z is float, not parked-low. L2 X-on-float during PSRAM turnaround is reachable on that physical net; it is not cited as L2 X coverage without a directed L2 test.
- CI L1 Icarus smoke job is still open (local smoke is green).
- SDF remains `blocked` until hardening produces a compatible netlist-matched artifact and annotation is qualified. A zero-delay functional gate run is not an SDF pass. M6 stays open.
- L2 entry: `source test/env.sh && test/scripts/run_gl.sh` (or `GATES=yes make` / `make gl_test` from `test/`). Copies the designated 189-DFF N=5 netlist `test/gate_level_netlist.189-aug18.v` (SHA256 `9a769ad4bcc09d7cff699e8f178acab4fb5b7228e242cfdf7d027ed2274beb7a`) to `test/gate_level_netlist.v`, fails on SHA mismatch or if `SDF` is set, requires IHP cell models (`PDK_ROOT`), and runs `tests.test_gate_level` (`TC-GL-*` IDs, not L1 `TC-*`). `make verilator_x` isolates `RUN_DIR`/`SIM_BUILD` under `x-unique` (`run_verilator_x-20260825-191818.log`, 1/1); it is a binary X-assign campaign, not four-state. Bare `make` / Tiny Tapeout `GATES=yes make` uses Makefile `.DEFAULT_GOAL=test`. Missing netlist or PDK is `blocked`, not a pass.
- `make directed` runs `tests.test_dma_directed`, `tests.test_reset_and_bus`, and `tests.test_injection_dut` (injection is M5 evidence). `COV-BUS-PHASE` / `COV-BUS-RESUME` are 1-D enums (bin names unchanged). Exclusion matching is `(id, bin, depth)`.
- `TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`) is re-run after overlap/default changes via `make depth` / `run_depth_sweep.sh`. V1 tapeout and default sim/Make depth is **N=5**.
- Regenerated `test/runs/m5_coverage_closure.json` (2026-08-25): `closed=true`; reviewer `tb-closure-2026-08-25`; exclusion key `(id, bin, depth)`. Firmware oracle-hash drift is a firmware follow-up, not a coverage-bin miss.
- Catalog follow-up: BUS_GNT-aware CTRL/HS checkers for MCU pass-through suites.
- M7 FPGA hardware validation has not started.

## Progress and operational lessons

- M0 (`TC-SMOKE`), M1 behavioral exit, M2 (reference / dual-axis scoreboard / 24 directed `TC-*` / always-on `CHK-*`), M3 (delay / launch / RX / lifecycle), and **M5** (random / `COV-*` / depth sweep, exit 2026-08-16) are green. M4 formal is deferred (D33).
- Do not mix nix Icarus into cocotb runs; use `source test/env.sh` (suite wrappers). Raw suite `bin/vvp` breaks `dma-venv` via `PYTHONHOME`.
- PSRAM model must remain SCK/CE#-edge driven with six real dummy cycles - no clk-polling calibration. Extra clocks after those six are data beats, not a too-many `Q-DUMMY` (CE# rose still in DUMMY with `dummy_cycles != 6`). L0 `TC-QPI-READ` measures those six wait cycles on the pin decoder (`pin_monitor=True`); `CHK-HS-OPCODE` wait half is then `pass`, not `na`. ASIC-selected SIO contention (`TC-QPI-ASIC-SIO-X`) uses the selected model's extra OE (`fault_sio_oe` mutes the L0 engine). Live CE at stop records two `Q-PHASE` (model + pin).
- Page crossings beyond one in a single CE# pulse fail as `Q-PAGE` (Linear Burst: one CE# low may occupy at most two 1K pages) at CE# rise.
- Prefer `make directed` / quoted directed function-name filters; do not use a bare `TEST_FILTER=directed`.

Full strategy and milestone gates: [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md).
