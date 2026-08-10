# Verification Context Index

This directory is the canonical, verbose verification plan for the V1 descriptor DMA. It defines the contracts that later testbench, formal, gate-level, FPGA bring-up, STA, and demoboard work implement. It does not replace the architecture in `../03-architecture.md` or protocol truth in `../05-qspi-psram.md`.

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
- **FPGA hardware validation** - firmware-driven hardware regression on the carrier board and MCU with an FPGA standing in for the ASIC, run before shuttle commit
- **Closure** - post-RTL STA and physical demoboard measurement for nanosecond timing and electrical validity

The detailed boundary is in `01-strategy.md`. A simulation pass does not replace STA or board closure.

### DUT levels


| Level  | DUT boundary                                           | Primary purpose                                                                             |
| ------ | ------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| **L0** | `qspi_engine` plus one selected timed PSRAM model      | QPI transaction, edge, nibble, CE#, and handshake behavior                                  |
| **L1** | `tt_um_lahnb_sgdma` plus dual PSRAM models             | Host arbitration, descriptor chains, same-device and cross-device copies, and scoreboarding |
| **L2** | Gate-level `tt_um_lahnb_sgdma` plus the L1 environment | Selected sign-off regressions, reset behavior, X hunting, and optional SDF                  |


Level names are fixed. `LEVEL=engine`, `LEVEL=top`, and `LEVEL=gl` select L0, L1, and L2 respectively.

### Milestones

The implementation ladder is fixed as **M0 through M6**:

- **M0** - toolchain and L1 same-device smoke
- **M1** - PSRAM model and behavioral QSPI checks
- **M2** - reference model, scoreboard, and directed suite (complete 2026-08-08; `TC-DEPTH` deferred to M5)
- **M3** - delay layer, setup/hold sweeps, and launch/RX edge checks (complete 2026-08-10)
- **M4** - formal safety proofs and cover reachability
- **M5** - randomized regression and coverage closure; the buffer-depth sweep is blocked until the sim harness selects `DMA_BUF_DEPTH`
- **M6** - gate-level and X checks, then handoff to STA and demoboard closure
- **M7** - FPGA hardware validation on the carrier board with real MCU firmware, before shuttle commit

Exact entry and exit gates are in `01-strategy.md`. Milestones are cumulative and do not redefine project roadmap phase numbers.

## Stable ID scheme

IDs identify requirements and results, not individual Python function names. Once published, an ID is not reused for a different condition.


| Prefix  | Owner                                             | Definition location           |
| ------- | ------------------------------------------------- | ----------------------------- |
| `Q-*`   | Simulation-provable QSPI protocol and edge checks | `04-timing-in-sim.md`         |
| `T-*`   | Nanosecond timing and physical closure checks     | `../11-timing-analysis.md`    |
| `FP-*`  | Formal properties                                 | `07-formal.md`                |
| `CHK-*` | Always-on cocotb runtime monitors                 | `06-checkers.md`              |
| `TC-*`  | Directed test cases                               | `08-stimulus-and-coverage.md` |
| `COV-*` | Functional coverage points and crosses            | `08-stimulus-and-coverage.md` |


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


| Area                     | IDs or gate              | Current status | Planned milestone                                                                 |
| ------------------------ | ------------------------ | -------------- | --------------------------------------------------------------------------------- |
| Platform / toolchain     | `env.sh`, doctor, hooks  | pass           | M0 (complete)                                                                     |
| Platform smoke           | M0 exit / `TC-SMOKE`     | pass           | M0 (complete); CI job still open                                                  |
| PSRAM behavioral model   | SCK/CE# agent + policing | pass           | M1 exit met; model-plane Z→0 idealization remains; see `03-psram-model.md`      |
| QPI protocol (M1 rows)   | `Q-CEM/CPH/MUX/SIO-OWN/RST/SCKIDLE` | pass | M1 under `ideal`; CEM/CPH/SIO-OWN delay rerun complete at M3 (2026-08-10) |
| QPI protocol (M3 rows)   | `Q-LAUNCH`, `Q-RXEDGE`, `Q-CSP/CHD/TERM` | pass | M3 (complete 2026-08-10)                                              |
| Directed behavior (M2)   | M2 `TC-*`, `CHK-*`, dual-axis scoreboard | pass | M2 complete (2026-08-08); `TC-DEPTH` remains M5/`blocked` |
| Delay-annotated timing   | `Q-LAUNCH`, `Q-RXEDGE`   | pass           | M3 complete (2026-08-10); see `04-timing-in-sim.md` residuals         |
| Formal                   | `FP-*`                   | todo           | M4                                                                                |
| Random and coverage      | `COV-*`                  | todo           | M5                                                                                |
| Buffer-depth sweep       | `TC-DEPTH`, `COV-DEPTH*` | blocked        | M5, after sim harness wires `DMA_BUF_DEPTH`                                       |
| Gate-level and X         | M6 exit                  | todo           | M6                                                                                |
| FPGA hardware validation | M7 exit                  | todo           | M7                                                                                |
| Physical timing          | `T-*`                    | todo           | Post-M6/M7 closure                                                                |


This table is a planning roll-up, not evidence of implementation. Update the owning catalog first, then this summary. M1 matrix evidence: `test/runs/m1_t10_icarus_matrix.log` and `test/runs/m1_t10_verilator_matrix.log` (Icarus ≡ Verilator; may be wiped by `make clean`). Detail in `04-timing-in-sim.md` (M1 behavioral evidence). M2 Acceptance evidence (2026-08-08): L1 Icarus smoke, `tests.test_dma_directed` (13 `TC-*` + skipped `TC-DEPTH`), `tests.test_reset_and_bus` (11/11), and migrated M1 modules (`test_qspi_negative`, `test_qspi_timing`, `test_qspi_reset_protocol`, `test_qspi_ownership`, `test_qspi_pin_disposition`). Detail in `01-strategy.md` (M2 acceptance status) and owning catalogs. M3 Acceptance evidence (2026-08-10): delay layer + launch/RX under `nominal`, Icarus ≡ Verilator on `test_qspi_timing`, `test_qspi_timing_delay`, `test_qspi_timing_launch_rx`, `test_qspi_ownership`; centralized `PendingLedger` / `finalize_all` cleanup; directed cleanup `TC-*`. Detail in `01-strategy.md` (M3 acceptance status), `04-timing-in-sim.md`, and `06-checkers.md` (lifecycle contract).

## Architecture anchors

- Project and constraints: `../01-project-brief.md`, `../02-constraints.md`
- System architecture and handshakes: `../03-architecture.md`
- TCD encoding and behavior: `../04-tcd-and-datapath.md`
- APS6404L protocol and timing source: `../05-qspi-psram.md`
- Decision history and open questions: `../07-decision-log.md`, `../08-open-questions.md`
- Existing timing checklist: `../11-timing-analysis.md`
- Condensed human documentation: `../../human/`
- Tiny Tapeout test flow reference: `../../../ttihp-verilog-template/test/`

