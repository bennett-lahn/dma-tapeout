# Verification Context Index

This directory is the canonical, verbose verification plan for the V1 descriptor DMA. It defines the contracts that later testbench, formal, gate-level, STA, and demoboard work implement. It does not replace the architecture in `../03-architecture.md` or protocol truth in `../05-qspi-psram.md`.

## Reading order

1. `01-strategy.md` - verification venues, DUT levels, milestone ladder, and sign-off gates
2. `02-platform.md` - repository layout, simulator matrix, commands, dependencies, seeds, and artifacts
3. `03-psram-model.md` - dual APS6404L QPI model and protocol policing, with timing behavior added at M3
4. `04-timing-in-sim.md` - delay annotation, timing parameters, setup/hold checks, and sweeps
5. `05-reference-model.md` - descriptor-chain interpreter and transaction/memory scoreboard
6. `06-checkers.md` - always-on runtime checker catalog
7. `07-formal.md` - formal harnesses, properties, engines, and proof expectations
8. `08-stimulus-and-coverage.md` - directed tests, constrained random stimulus, and coverage closure
9. `09-gate-level-and-x.md` - gate-level regression, SDF, X propagation, and reset randomization

Files 03 through 09 define the model, timing, scoreboard, checker, formal, stimulus, coverage, gate-level, and X-verification contracts.

## Stable vocabulary

### Verification venues

- **Simulation** - cocotb behavioral and delay-annotated checks, including protocol behavior and end-to-end data correctness
- **Formal** - control-plane safety invariants, bounded reachability, deadlock checks, and k-induction against the real `qspi_engine`
- **Closure** - post-RTL STA and physical demoboard measurement for nanosecond timing and electrical validity

The detailed boundary is in `01-strategy.md`. A simulation pass does not replace STA or board closure.

### DUT levels

| Level | DUT boundary | Primary purpose |
|---|---|---|
| **L0** | `qspi_engine` plus one selected timed PSRAM model | QPI transaction, edge, nibble, CE#, and handshake behavior |
| **L1** | `tt_um_lahnb_sgdma` plus dual PSRAM models | Host arbitration, descriptor chains, same-device and cross-device copies, and scoreboarding |
| **L2** | Gate-level `tt_um_lahnb_sgdma` plus the L1 environment | Selected sign-off regressions, reset behavior, X hunting, and optional SDF |

Level names are fixed. `LEVEL=engine`, `LEVEL=top`, and `LEVEL=gl` select L0, L1, and L2 respectively.

### Milestones

The implementation ladder is fixed as **M0 through M6**:

- **M0** - toolchain and L1 same-device smoke
- **M1** - PSRAM model and behavioral QSPI checks
- **M2** - reference model, scoreboard, and directed suite
- **M3** - delay layer, setup/hold sweeps, and launch/RX edge checks
- **M4** - formal safety proofs and cover reachability
- **M5** - randomized regression and coverage closure; the buffer-depth sweep is blocked until RTL parameterization
- **M6** - gate-level and X checks, then handoff to STA and demoboard closure

Exact entry and exit gates are in `01-strategy.md`. Milestones are cumulative and do not redefine project roadmap phase numbers.

## Stable ID scheme

IDs identify requirements and results, not individual Python function names. Once published, an ID is not reused for a different condition.

| Prefix | Owner | Definition location |
|---|---|---|
| `Q-*` | Simulation-provable QSPI protocol and edge checks | `04-timing-in-sim.md` |
| `T-*` | Nanosecond timing and physical closure checks | `../11-timing-analysis.md` |
| `FP-*` | Formal properties | `07-formal.md` |
| `CHK-*` | Always-on cocotb runtime monitors | `06-checkers.md` |
| `TC-*` | Directed test cases | `08-stimulus-and-coverage.md` |
| `COV-*` | Functional coverage points and crosses | `08-stimulus-and-coverage.md` |

Existing `Q-*` and `T-*` IDs retain their meanings when timing documentation is split by venue. `Q-LAUNCH` and `Q-RXEDGE` are reserved for driven-phase launch stability and read-sampling-edge reconciliation. Bring-up IDs such as `B-PU` and decision IDs such as `D16` remain in their existing namespaces.

## Status vocabulary

Use these lowercase states in verification catalogs:

- `todo` - specified but not implemented
- `wip` - implementation or debug in progress
- `pass` - required evidence exists for the current RTL revision and configuration
- `fail` - observed violation is unresolved
- `blocked` - evidence cannot yet be produced because a named prerequisite is missing
- `na` - reviewed and not applicable to the selected level or configuration

For every `pass`, retain the simulator or formal engine, level, seed where applicable, configuration, and log/artifact location. A later RTL change that can affect the result returns the row to `todo` until rerun.

## Status roll-up

| Area | IDs or gate | Current status | Planned milestone |
|---|---|---|---|
| Platform smoke | M0 exit | todo | M0 |
| QPI protocol | `Q-*` | todo | M1 and M3 |
| Directed behavior | `TC-*`, `CHK-*` | todo | M2 |
| Delay-annotated timing | `Q-LAUNCH`, `Q-RXEDGE` | todo | M3 |
| Formal | `FP-*` | todo | M4 |
| Random and coverage | `COV-*` | todo | M5 |
| Buffer-depth sweep | `TC-DEPTH`, `COV-DEPTH*` | blocked | M5, after RTL parameterization |
| Gate-level and X | M6 exit | todo | M6 |
| Physical timing | `T-*` | todo | Post-M6 closure |

This table is a planning roll-up, not evidence of implementation. Update the owning catalog first, then this summary.

## Architecture anchors

- Project and constraints: `../01-project-brief.md`, `../02-constraints.md`
- System architecture and handshakes: `../03-architecture.md`
- TCD encoding and behavior: `../04-tcd-and-datapath.md`
- APS6404L protocol and timing source: `../05-qspi-psram.md`
- Decision history and open questions: `../07-decision-log.md`, `../08-open-questions.md`
- Existing timing checklist: `../11-timing-analysis.md`
- Condensed human documentation: `../../human/`
- Tiny Tapeout test flow reference: `../../../ttihp-verilog-template/test/`
