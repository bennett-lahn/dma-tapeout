# Verification

Condensed map of the V1 verification plan. The detailed specifications and catalogs live in [`../../llm/verification/`](../../llm/verification/00-index.md).

## Reading order

1. [`strategy.md`](strategy.md) - venues, DUT levels, milestones, and evidence boundaries
2. [`signoff.md`](signoff.md) - RTL and shuttle freeze criteria
3. [`../../llm/verification/00-index.md`](../../llm/verification/00-index.md) - full reading order, catalogs, and status vocabulary

## Stable map

| Area | Stable IDs | Primary venue | Detail |
|---|---|---|---|
| QSPI behavior and modeled timing | `Q-*` | Simulation | [`04-timing-in-sim.md`](../../llm/verification/04-timing-in-sim.md) |
| Runtime invariants | `CHK-*` | Simulation | [`06-checkers.md`](../../llm/verification/06-checkers.md) |
| Directed tests | `TC-*` | Simulation | [`08-stimulus-and-coverage.md`](../../llm/verification/08-stimulus-and-coverage.md) |
| Functional coverage | `COV-*` | Simulation | [`08-stimulus-and-coverage.md`](../../llm/verification/08-stimulus-and-coverage.md) |
| Control-plane proofs and covers | `FP-*` | Formal | [`07-formal.md`](../../llm/verification/07-formal.md) |
| Nanosecond and physical closure | `T-*` | STA and demoboard | [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md) |

Published IDs keep their meanings even if implementation files change. Required results use `todo`, `wip`, `pass`, `fail`, `blocked`, or `na`, and every `pass` retains its revision, configuration, tool, and artifact.

## Current status

| Gate | Status | Note |
|---|---|---|
| M0-M6 implementation | `todo` | The platform documents exist, but verification code and evidence do not yet exist |
| `Q-LAUNCH`, `Q-RXEDGE` | `todo` | Current QSPI RTL has known launch and receive-edge TODOs; execute the M3 checks before assigning `pass` or `fail` |
| SDF run | `blocked` | No compatible final-netlist SDF artifact is available yet |
| `DMA_BUF_DEPTH=1,2,4,8` sweep | `blocked` | `DMA_BUF_DEPTH` is currently a package `localparam`; RTL parameterization is required before compile-time depth sweeps |
| `T-*` closure | `todo` | STA and demoboard evidence follow M6 |

Do not treat delay-annotated simulation, zero-delay gate simulation, or missing future artifacts as physical signoff.
