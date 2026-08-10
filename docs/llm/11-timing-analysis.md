# Timing Analysis

Post-RTL physical timing checks before shuttle freeze (Phase 3). Protocol limits and opcode policy stay in `05-qspi-psram.md`. Simulation-provable QSPI checks are owned by `verification/04-timing-in-sim.md`; this file owns the `T-*` nanosecond and physical closure checklist.

**When:** RTL feature-complete (Phase 2 done), before freezing for tapeout.
**Goal:** confirm APS6404L AC timing, **IHP SG13G2 / TT I/O** budgets, and on-chip paths at the frozen **66 MHz `clk` max / SCK=clk/2 (≈33 MHz) / rising-edge RX** policy (D16, amended D27). There is **no** published IHP pad MHz toggle ceiling; Phase 3 closes pad delay, liberty `max_cap` / `max_transition`, TT mux STA, and demoboard. Fallback only if a check fails: lower `clk` and/or falling-edge RX.

## Venue split

| Venue | Owns | Does not close |
|---|---|---|
| **RTL and delay-annotated simulation** | `Q-*` protocol, edge-order, modeled setup/hold, capture, OE, and timing sweeps in `verification/04-timing-in-sim.md`; optional qualified SDF can add implementation-delay pre-checks per `verification/09-gate-level-and-x.md` | Routed IHP pad and TT mux timing, package and board parasitics, analog edge quality, process variation, or any `T-*` item |
| **STA and implementation reports** | Internal, pad, and TT mux path delay; setup/hold; generated-clock duty; liberty `max_transition` and `max_capacitance`; extracted implementation corners | Actual package, PMOD, PCB, scope waveform, or physical PSRAM behavior |
| **Demoboard measurement** | End-to-end number validity with real TT silicon, package, PMOD, board load and flight, APS6404L devices, and long-copy operation at 66 MHz `clk` / approximately 33 MHz SCK | Exhaustive process-corner path analysis or proof of internal timing paths |

Delay-annotated simulation is an approximate pre-check. Cocotb timing sweeps can expose zero or negative modeled margin, and qualified SDF can expose delay-sensitive behavior, but neither replaces STA or demoboard evidence.

M7 FPGA hardware validation (`verification/01-strategy.md`) is a separate pre-shuttle checkpoint that runs real MCU firmware against an FPGA in the ASIC's board position. It closes no `T-*` row: FPGA I/O electrical characteristics differ from IHP SG13G2 pads, so an FPGA pass is firmware and functional confidence only, not a timing pre-check.

Engine **SCK = clk/2** (registered toggle when enabled; idle low when disabled). Timing is upheld by **enable/disable of that toggle and ordering CE#**, not by muxing `clk` onto the pad or an async SPI clock.

## How to extend

1. Add a subsection under **Checks** for the new domain (e.g. host pins, CDC, DFT).
2. Append rows to that subsection's table with the physical constraint, closure venue, approximate simulation pre-check, pass evidence, and status.
3. Keep `Status` as `todo` / `wip` / `pass` / `fail` (note fallback if fail).
4. Do not duplicate datasheet tables here - link `05-qspi-psram.md` / `docs/datasheets/`.

---

## Checks

### PSRAM / QSPI simulation prerequisites

`verification/04-timing-in-sim.md` owns the full definitions, equations, thresholds, levels, milestones, and status for every `Q-*` check. These pointer rows preserve the relationship to physical closure without duplicating that specification.

| ID | Concise pointer | Physical handoff |
|---|---|---|
| [`Q-CEM`](verification/04-timing-in-sim.md) | CE# low-pulse policing | Supports refresh-limit confidence before board operation |
| [`Q-CPH`](verification/04-timing-in-sim.md) | CE# high-gap policing | Supports inter-transaction timing before physical delay is known |
| [`Q-CSP`](verification/04-timing-in-sim.md) | First-edge CE# setup check | Pre-check for device-input timing |
| [`Q-CHD`](verification/04-timing-in-sim.md) | Final-edge CE# hold check | Pre-check for termination timing |
| [`Q-TERM`](verification/04-timing-in-sim.md) | Final read commit, frozen SCK, no extra beat, advisory long-hold report | Supports read termination review |
| [`Q-MUX`](verification/04-timing-in-sim.md) | RAM CE# exclusion and ASIC flash-CS parking | Prerequisite for shared-bus physical testing |
| [`Q-SIO-OWN`](verification/04-timing-in-sim.md) | ASIC and PSRAM/SPI device never co-drive bidirectional SIO | Prerequisite for contention-free turnaround (`T-HZ`) and shared-bus bring-up |
| [`Q-RST`](verification/04-timing-in-sim.md) | Transaction abort and shared-OE release on reset | Prerequisite for safe board reset testing |
| [`Q-SCKIDLE`](verification/04-timing-in-sim.md) | SCK stays low while no device is selected; no erroneous SCK cycle while deselected | Supports contention-free bring-up on a bus shared with MCU pass-through |
| [`Q-LAUNCH`](verification/04-timing-in-sim.md) | Driven SIO and OE launch-edge discipline with modeled setup and hold | Required prerequisite for `T-SP-HD` |
| [`Q-RXEDGE`](verification/04-timing-in-sim.md) | Falling-edge model launch reconciled to one rising-edge DUT capture | Required prerequisite for `T-ACLK` |

`Q-LAUNCH` and `Q-RXEDGE` simulation prerequisites are `pass` as of M3 (2026-08-10); see `verification/04-timing-in-sim.md`. Passing them does not close routed output setup/hold or the physical read-return path. Their complete definitions remain only in that file.

### PSRAM / QSPI - post-RTL timing / board

These rows close real nanosecond paths, loads, or board behavior. They are not requests for separate FSM timers.

| ID | Physical constraint or fact | Closure venue | Approximate delay-annotated simulation pre-check | Pass evidence | Status |
|---|---|---|---|---|---|
| `T-ACLK` | APS6404L `tACLK` is 2 ns minimum to 5.5 ns maximum; rising-edge RX at approximately 33 MHz SCK must include SCK output path, device delay, return flight, input path, and capture timing | STA plus demoboard | Yes - `tACLK` sweep with nonzero per-path estimates; qualified SDF may refine DUT path delay | `Q-RXEDGE` passes first; extracted path budget is positive at required corners; TCD and payload reads are stable on the demoboard at the 66 MHz `clk` maximum | todo |
| `T-SP-HD` | APS6404L `tSP` and `tHD` are each at least 2 ns for command, address, and write data at the device pins | STA plus board estimate or measurement | Yes - per-signal SCK, SIO, CE#, and clock-to-output delay stress; qualified SDF may refine launch paths | `Q-LAUNCH` passes first; routed SCK-to-SIO and SCK-to-OE relationships meet both requirements with package and board allowance | todo |
| `T-CLKQ` | `tCH` and `tCL` each remain within 0.45 to 0.55 `tCLK`; `tKHKL` rise or fall time is at most 1.5 ns | STA for duty; scope with final load for edge rate | Partial - digital duty and edge ordering only; rise and fall time are reporting placeholders in digital simulation | Generated SCK duty closes in implementation reports and measured loaded SCK meets the APS6404L table | todo |
| `T-HZ` | APS6404L `tHZ` is at most 5.5 ns from CE# high to SIO high impedance; physical turnaround must avoid contention with another PSRAM or MCU pass-through | STA for ASIC OE/reclaim plus demoboard | Yes - modeled `tHZ`, `Q-SIO-OWN` ownership resolution, and optional qualified SDF pre-check handoff behavior | `Q-SIO-OWN` passes first; worst-case device release, ASIC ownership delay, and board flight leave a contention-free turnaround interval | todo |
| `T-PARK` | Under D26 the ASIC parks all CS high and SCK low whenever `~BUS_GNT`; board 10 kOhm CS pull-ups cover reset and pre-enable | STA for OE/control paths plus demoboard reset and handoff | Partial - `Q-MUX`, `Q-RST`, and gate-level OE checks cover digital intent, not pull-up analog behavior | No CE# floats low long enough to approach `tCEM` while live; CS remains high during reset and ownership transitions on the board | todo |
| `T-66` | V1 maximum system `clk` is 66 MHz, registered SCK is `clk/2`, and the APS6404L Linear Burst page-cross command limit is 84 MHz | Clock policy plus STA plus demoboard | Partial - regressions and timing sweeps run at the target period but do not validate the physical clock or board | Final constraints use approximately 15.15 ns `clk`; long same-device and cross-device copies pass at the maximum target with approximately 33 MHz SCK | todo |
| `T-GPIO-IN` | IHP `sg13g2_IOPadIn` plus TT inward mux, including the 5 ns or less `signoff.sdc` budget; the IHP PDK publishes no binding pad MHz rating | STA plus demoboard | Partial only with qualified SDF - no cocotb delay value is physical by default | `clk`, `rst_n`, and `ui_in` are clean at 66 MHz; pad-to-core plus TT mux closes at required corners and board input behavior is stable | todo |
| `T-GPIO-OUT` | TT uses `sg13g2_IOPadOut30mA` and `sg13g2_IOPadInOut30mA`; typical liberty core-to-pad delay is approximately 1.6 to 1.7 ns at 1 pF and pad `max_capacitance` is approximately 4.5 to 4.8 pF | STA plus board | Partial only with qualified SDF - loaded waveform quality remains physical | Registered SCK and other high-rate `uio` and `uo` paths close and drive the demoboard plus PMOD load acceptably; sky130 33 MHz and 4 mA ceilings do not apply | todo |
| `T-GPIO-LIB` | Liberty `max_transition` and `max_capacitance` must be checked against the real board capacitance | STA reports plus board load estimate or measurement | No - a functional delayed simulation does not close liberty load limits | No required pad violates characterized transition or capacitance limits; functional QSPI still passes at target rate under the real load | todo |

### Bring-up (MCU; not ASIC datapath)

| ID | Constraint | Where | Pass | Status |
|---|---|---|---|---|
| B-PU | `tPU` ≥ 150 µs, CE# high | firmware | Both devices after power-up | todo |
| B-RST | `tRST` ≥ 50 ns after `0x99` | firmware | Delay before next cmd | todo |

### Future (placeholders)

Add rows when RTL exposes the path:

| ID | Constraint | Where | Pass | Status |
|---|---|---|---|---|
| F-HOST | START/DONE/`BUS_REQ` pin timing vs clk | sim / STA | TBD | todo |
| F-INT | Critical on-chip paths (FSM to QSPI to regs) | STA | TBD | todo |

---

## Related

- Protocol / AC table context: `05-qspi-psram.md`
- Architecture and D16 timing policy: `03-architecture.md`
- D16, D26, and D27 physical decisions: `07-decision-log.md`
- Simulation-owned `Q-*` definitions and delay sweeps: `verification/04-timing-in-sim.md`
- Gate-level and optional qualified-SDF boundary: `verification/09-gate-level-and-x.md`
- IHP PDK timing sources: `../../IHP-Open-PDK/`
- TT IHP wrapper, constraints, and gate-flow context: `../../ttihp-verilog-template/`
- Human summary: `../human/architecture/timing.md`
- QSPI block: `../human/architecture/blocks/qspi-engine.md`
- Roadmap Phase 3: `../human/roadmap.md`
- Datasheet: `../datasheets/pdfs/APS6404L_3SQR.pdf`
