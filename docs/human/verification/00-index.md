# Verification

Condensed map of the V1 verification plan. Verbose catalogs live in [`../../llm/verification/`](../../llm/verification/00-index.md). Per [`../../README.md`](../../README.md), human docs stay condensed but must still carry durable requirements; llm should not be the only place a fact exists.

**Known debt:** the `CHK-*` runtime invariant catalog is still mostly llm-only in [`06-checkers.md`](../../llm/verification/06-checkers.md). That shape is an anti-pattern for new docs; a condensed human checker summary should be added when that catalog is next edited.

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
| Firmware-driven hardware regression | `TC-*` subset | FPGA on the carrier board (M7) | [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md) |
| Nanosecond and physical closure | `T-*` | STA and demoboard | [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md) |

Published IDs keep their meanings even if implementation files change. Required results use `todo`, `wip`, `pass`, `fail`, `blocked`, or `na`, and every `pass` retains its revision, configuration, tool, and artifact.

## Toolchain (how to run)

WSL: OSS CAD Suite is already on PATH in interactive shells. Activate the repo-root `dma-venv` and use the hook scripts (not ad-hoc one-liners):

```sh
source test/env.sh
test/scripts/doctor.sh
test/scripts/run_smoke.sh
```

System Python is `python3`. Prefer suite Verilator 5.051 over older `/usr/local` builds. `env.sh` puts a cocotb-friendly `vvp` wrapper first on PATH (raw suite `bin/vvp` breaks the venv). Full contract: [`../../llm/verification/02-platform.md`](../../llm/verification/02-platform.md).

## Current status

| Gate | Status | Note |
|---|---|---|
| Platform scaffold + toolchain hooks | `pass` | `test/env.sh`, wrappers, doctor/run scripts; suite Verilator 5.051 |
| M0 exit (`TC-SMOKE`) | `pass` | L1 same-device length-1 + quit; run via `test/scripts/run_smoke.sh` |
| SCK-accurate PSRAM model + M1 policing | `pass` | Dual models, negatives, shared-bus ownership; Icarus ≡ Verilator on directed set |
| M1 exit (behavioral `Q-*`) | `pass` | `Q-CEM/CPH/MUX/SIO-OWN/RST/SCKIDLE` under `ideal`; residual gaps below |
| M2-M6 implementation | `todo` | Next: M2 reference/scoreboard + independent pin monitor |
| `Q-LAUNCH`, `Q-RXEDGE` | `todo` | Execute the M3 checks against current RTL before assigning `pass` or `fail` |
| SDF run | `blocked` | No compatible final-netlist SDF artifact is available yet |
| `DMA_BUF_DEPTH=1,2,4,8` sweep | `blocked` | RTL module parameter exists; sim harness must select depths before the sweep can run |
| M7 FPGA hardware validation | `todo` | Not started; requires an FPGA-synthesizable build and carrier-board bring-up with real MCU firmware |
| `T-*` closure | `todo` | STA and demoboard evidence follow M6 |
| CI smoke job | `todo` | Local smoke green; CI job still open |
| Independent `QspiPinMonitor` | stub | `CHK-PIN-ADDR23-ZERO` / `CHK-PIN-KNOWN` dispose via model IDs until M2 |
| Model-plane Z→0 idealization | open | Floating SIO still forced to 0 for the parser; float-as-known remains limited |

**Roadblocks already hit:** nix Icarus vs OSS CAD Suite / cocotb GPI; suite `bin/vvp` `PYTHONHOME` vs `dma-venv`; early PSRAM clk-polling + 5-dummy hack (fixed). Avoid by always using `source test/env.sh` and keeping the PSRAM model SCK-edge driven. See the verification execution plan.

Do not treat delay-annotated simulation, zero-delay gate simulation, or missing future artifacts as physical signoff.
