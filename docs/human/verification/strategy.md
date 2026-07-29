# Verification Strategy

V1 verification is cumulative across three venues. No venue replaces another.

## Three venues

- **Simulation** checks QPI transactions, descriptor semantics, same-device and cross-device copies, host arbitration, reset, scoreboards, always-on monitors, randomized stimulus, and modeled timing.
- **Formal** emphasizes control-plane safety with the real `qspi_engine`: reset, state and counter bounds, request stability, chip-select exclusion, atomic bus yield, handshake counts, fixed-head and quit behavior, reachability covers, and bounded local deadlock. Required safety properties use k-induction with proved helper invariants.
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

Milestones are cumulative. A required child ID in `fail`, `wip`, or `blocked` prevents its parent milestone from closing.

## Known blockers

- `Q-LAUNCH` and `Q-RXEDGE` are expected to fail against current RTL until launch timing and rising-edge receive behavior are corrected.
- SDF remains `blocked` until hardening produces a compatible netlist-matched artifact and annotation is qualified.
- The M5 `DMA_BUF_DEPTH=1,2,4,8` sweep requires RTL parameterization. `DMA_BUF_DEPTH` is currently a package `localparam`, so current RTL and formal evidence apply only to depth 1.

Full strategy and milestone gates: [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md).
