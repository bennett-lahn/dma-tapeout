# Verification Signoff

Signoff requires reproducible evidence for the exact RTL or netlist revision and configuration. A passing test without its required checkers, scoreboard, seed, tool version, and artifacts is not signoff evidence.

## RTL verification freeze

- [ ] M0 through M5 are complete.
- [ ] Every required simulation row is `pass`: `Q-*`, `CHK-*`, `TC-*`, and `COV-*`.
- [ ] Both scoreboard axes pass: ordered pin-decoded QPI transactions and final memory on both PSRAM devices.
- [ ] Same-device copies, both cross-device directions, chaining, `QUIT`, zero length, bus handoff, and reset recovery pass.
- [ ] `Q-LAUNCH` and `Q-RXEDGE` pass at M3 with reproducible nominal and boundary sweeps.
- [ ] Every required `FP-*` safety property proves with the real `qspi_engine`; helper invariants are asserted and proved, required covers produce witnesses, and bounded deadlock checks pass.
- [ ] Formal assumptions are audited and do not duplicate their conclusions.
- [ ] Icarus passes the full required suite and Verilator passes its assigned directed and high-volume subset.
- [ ] `DMA_BUF_DEPTH=1,2,4,8` passes the assigned M5 suite and required `COV-DEPTH*` crosses.
- [ ] No unresolved reproducible seed or unreviewed exclusion remains.
- [ ] Every waiver names an owner, rationale, affected configuration, risk, and expiration condition.

Current blockers to this gate:

- `Q-LAUNCH` and `Q-RXEDGE` remain `todo` pending M3 execution against current RTL.
- The depth sweep cannot run until the sim harness selects module parameter `DMA_BUF_DEPTH`. Default / tapeout remains depth 1.

## Final-netlist and shuttle freeze

- [ ] RTL verification freeze is complete.
- [ ] M6 passes on the final `DMA_BUF_DEPTH=1` netlist using the required Icarus L2 test subset and IHP cell models.
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
- [`../../llm/verification/07-formal.md`](../../llm/verification/07-formal.md) - `FP-*` catalog and proof requirements
- [`../../llm/verification/08-stimulus-and-coverage.md`](../../llm/verification/08-stimulus-and-coverage.md) - `TC-*`, `COV-*`, and depth sweep
- [`../../llm/verification/09-gate-level-and-x.md`](../../llm/verification/09-gate-level-and-x.md) - L2, X, and SDF criteria
- [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md) - physical `T-*` closure
