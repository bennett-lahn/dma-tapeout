# Verification Signoff

Signoff requires reproducible evidence for the exact RTL or netlist revision and configuration. A passing test without its required checkers, scoreboard, seed, tool version, and artifacts is not signoff evidence.

## RTL verification freeze

- [ ] M0 through M5 are complete.
- [ ] Every required simulation row is `pass`: `Q-*`, `CHK-*`, `TC-*`, and `COV-*`.
- [ ] Both scoreboard axes pass: ordered pin-decoded QPI transactions and final memory on both PSRAM devices.
- [ ] Same-device copies, both cross-device directions, chaining, `QUIT`, zero length, bus handoff, and reset recovery pass.
- [x] `Q-LAUNCH` and `Q-RXEDGE` pass at M3 with reproducible nominal and boundary evidence (2026-08-10; fuller path-delay sweep remains a post-M3 follow-up).
- [ ] Every required `FP-*` safety property proves with the real `qspi_engine`; helper invariants are asserted and proved, required covers produce witnesses, and bounded deadlock checks pass.
- [ ] Formal assumptions are audited and do not duplicate their conclusions.
- [ ] Icarus passes the full required suite and Verilator passes its assigned directed and high-volume subset.
- [ ] Depths across `1..DMA_BUF_DEPTH_MAX` (8) pass the assigned M5 suite at tapeout depth **N=5** by default and via Makefile `-G`/`-P` overrides; `TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`) via `make depth` / `run_depth_sweep.sh`; required `COV-DEPTH*` crosses (`COV-DEPTH*`: functional coverage bins for buffer-depth elaboration, sampled from directed `coverage.json` per compiled `N`) meet closure once the full sweep is green.
- [ ] No unresolved reproducible seed or unreviewed exclusion remains.
- [ ] Every waiver names an owner, rationale, affected configuration, risk, and expiration condition.

Current blockers to this gate (M0-M3 simulation exits are green as of 2026-08-10):

- M4 formal deferred (may proceed independently; do not claim pass).
- M5 random / `COV-*` and depth sweep in progress. Wave 4 2026-08-16 partial random green at **N=5** / `ideal`: Icarus seeds 1/2/3/5/8, Verilator seeds 1/2 (`CHK-*`/`Q-*` clean; seed-1 cross-sim match). Still open: Verilator seeds 3/5/8; `TC-DEPTH` / `COV-DEPTH*` (`COV-*` functional coverage point IDs) closure across all integers `1..8` via `make depth` - do not claim those IDs or M5 exit pass until green.
- V1 tapeout and default sim/Make `DMA_BUF_DEPTH` is **N=5** (elaboration `1..8` via Makefile).
- CI L1 Icarus smoke job still open.
- M3 follow-ups (not freeze blockers by themselves): margin-gate field presence / boundary ≈0; physical `T-*`. Delayed post-rise `Q-RXEDGE` under non-zero `D_OUT_*` is closed (`TC-RXEDGE-RACE-DEVICE-PLANE`).

## Final-netlist and shuttle freeze

- [ ] RTL verification freeze is complete.
- [ ] M6 passes on the final `DMA_BUF_DEPTH=5` netlist using the required Icarus L2 test subset and IHP cell models.
- [ ] M7 FPGA hardware validation passes on the carrier board with real MCU firmware and real PSRAM devices.
- [ ] Reset, `BUS_REQ` and `BUS_GNT`, shared output enables, chip selects, and sampled data have no unexplained post-reset X or Z behavior.
- [ ] Verilator X-initial and X-assignment experiments have no unexplained seed-dependent divergence.
- [ ] SDF is explicitly `pass`, `fail`, `blocked`, or `na` with evidence. It is currently `blocked` pending a compatible artifact; a zero-delay gate pass does not change that status.
- [ ] Every required `T-*` row is closed by STA and/or demoboard evidence.
- [ ] Final configuration remains 66 MHz maximum `clk`, SCK=`clk/2`, and rising-edge RX unless a recorded architecture decision changes it.
- [ ] Demoboard tests pass same-device copies, both cross-device directions, chaining, bus handoff, and reset recovery.

Delay-annotated RTL or SDF simulation is diagnostic and regression evidence only. It does not close physical setup, hold, `tACLK`, pad, TT mux, package, board, load, transition, clock-quality, or signal-integrity `T-*` rows. M7 FPGA hardware validation is firmware and system-integration confidence only; FPGA I/O electrical characteristics differ from IHP pads, so it also closes no `T-*` row.

## Evidence links

- [`strategy.md`](strategy.md) - venue, level, and milestone summary
- [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md) - complete entry and exit gates
- [`../../llm/verification/04-timing-in-sim.md`](../../llm/verification/04-timing-in-sim.md) - `Q-*` catalog and delay boundary
- [`../../llm/verification/06-checkers.md`](../../llm/verification/06-checkers.md) - `CHK-*` and pending-item lifecycle contract
- [`../../llm/verification/07-formal.md`](../../llm/verification/07-formal.md) - `FP-*` catalog and proof requirements
- [`../../llm/verification/08-stimulus-and-coverage.md`](../../llm/verification/08-stimulus-and-coverage.md) - `TC-*`, `COV-*`, and depth sweep
- [`../../llm/verification/09-gate-level-and-x.md`](../../llm/verification/09-gate-level-and-x.md) - L2, X, and SDF criteria
- [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md) - physical `T-*` closure
