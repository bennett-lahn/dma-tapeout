# Verification Strategy

## Purpose

The V1 verification plan must show that the descriptor DMA:

1. emits legal QPI transactions to exactly one of two PSRAM devices,
2. obeys host bus ownership and reset rules,
3. interprets 11-byte TCD chains correctly,
4. copies every requested byte to the correct device and address, and
5. remains physically usable at the target 66 MHz system clock and approximately 33 MHz SCK.

No single tool can establish all five. Sign-off is split across simulation, formal, FPGA hardware validation, and physical closure, with explicit handoffs between them.

## Verification venues

### 1. Simulation

Cocotb simulation owns behavior observable in RTL or at modeled pins:

- QPI opcode, address, nibble order, dummy cycles, byte count, CE# selection, and transaction ordering
- TCD decode, fixed head, `QUIT`, zero-length no-op, chaining, device flags, and pointer updates
- same-device and cross-device copy correctness
- `START`, `DONE`, `BUS_REQ`, `BUS_GNT`, reset, and shared-bus output-enable behavior
- modeled PSRAM timing windows using explicit Python delays and timestamped monitors
- deterministic directed and constrained-random regressions

Simulation can prove that a nominal timing equation is impossible or non-positive under its stated delay parameters. It can also find sensitivity as parameters are swept. It cannot certify routed pad and net delays, actual board loading, signal integrity, or silicon process variation.

### 2. Formal

SymbiYosys owns control-plane invariants and reachability that are difficult to exhaust in simulation:

- mutual exclusion, legal state transitions, bounded counters, and handshake safety
- no QPI request while busy or granted away
- atomic response to `BUS_REQ`
- stability of unlatched QSPI request fields for the complete transaction
- reset convergence and absence of forbidden control combinations
- cover traces for important states and arcs
- deadlock checks at bounded depths, supported by helper invariants and k-induction

`sys_controller` is verified with the real `qspi_engine`. Formal does not replace the engine with an abstract responder for integration proofs. Formal is not used to establish analog timing, PSRAM storage fidelity, or full-chain payload equivalence over arbitrary memory sizes.

### 3. FPGA hardware validation

Before RTL is frozen for the shuttle, the synthesizable `tt_um_lahnb_sgdma` RTL is loaded onto an FPGA that stands in for the ASIC in the same connector position on the same carrier board and with the same MCU the eventual TT demoboard will use. The MCU, PMOD, and both APS6404L devices are otherwise unchanged from the silicon setup.

This venue owns:

- a high-value hardware regression subset of `TC-*` (same-device copies, both cross-device directions, chaining, `QUIT`, zero length, bus handoff, and reset recovery) driven by real MCU firmware instead of a cocotb host driver
- real board timing, real bus loading, and real APS6404L devices in place of the Python PSRAM model
- firmware and system-integration bugs that an idealized clock, a symbolic formal environment, or a behavioral PSRAM model cannot expose
- one hardware checkpoint before an irreversible shuttle commit, taken while an RTL fix is still cheap

Reaching this venue may require adapting existing testbench-derived stimulus (for example, reusing the reference-model chain generator's intent as fixed firmware test vectors) and writing new MCU firmware test code that is not part of the cocotb `test/` tree. That firmware and its test scripts are retained and tied to the RTL revision they validated.

FPGA hardware validation does not prove IHP pad, TT mux, or routed-net timing. FPGA I/O electrical characteristics differ from IHP SG13G2 pads and do not substitute for `T-*` evidence. It also does not replace M6 gate-level and X checks, which require the actual synthesized ASIC netlist rather than an FPGA bitstream. A pass here is hardware-level functional and firmware-integration confidence, not physical timing sign-off.

### 4. Physical closure

STA and demoboard work own values that depend on implementation and hardware:

- IHP pad, TT mux, routed internal, package, PMOD, and board delays
- `tSP`, `tHD`, `tACLK`, clock quality, load, transition, and turnaround margin
- real PSRAM operation at the target clock across representative boards and devices
- electrical behavior that a four-state digital model cannot establish

These checks retain `T-*` IDs in `../11-timing-analysis.md`. The demoboard is the authority for final number validity, while STA explains and bounds margin erosion.

## Timing interpretation rule

Keep these conclusions distinct:

- **Simulation finding:** the modeled launch, flight, device, and sample delays leave zero or negative nominal margin, or violate a digital protocol prerequisite.
- **STA finding:** synthesized or routed internal, pad, and net delays consume more margin than budgeted.
- **Board finding:** measured flight time, loading, waveform quality, or device response invalidates the assumed numbers.

A delayed simulation is an early warning and regression mechanism. It is not timing sign-off. Conversely, STA cannot show descriptor semantics or transaction-level functional correctness.

## DUT levels

### L0 - QSPI engine

**DUT:** `qspi_engine` with direct request/response driving and a timed model for the selected APS6404L.

**Use for:**

- `0xEB` read and `0x02` write framing
- 24-bit address phase with `addr[23] == 0`
- six read dummy cycles
- SIO direction and nibble order
- SCK/CE# sequencing and one-device selection
- `busy`, `rdata_valid`, and `wdata_next` counts
- `Q-LAUNCH`, `Q-RXEDGE`, and timing-parameter sweeps

L0 may inspect `qspi_engine` internals for diagnosis, but pass criteria should be expressed at ports whenever possible.

### L1 - Integrated Tiny Tapeout top

**DUT:** `tt_um_lahnb_sgdma` with both PSRAM models attached to the shared `uio` bus and a host-side driver for `ui_in`, `uo_out`, and bus ownership.

**Use for:**

- fixed first fetch at `0x000000` on PSRAM0
- TCD chaining, `QUIT`, zero length, and device-select flags
- same-device A-to-A and B-to-B copies
- cross-device A-to-B and B-to-A copies
- dual-axis scoreboard checks against memory image and ordered QPI transaction log
- top-level START synchronization and edge detection
- `BUS_REQ` arbitration, parking, release, and atomic transaction completion
- reset during every meaningful controller and engine phase

L1 is the primary functional sign-off level. Internal handles may support checkers while RTL hierarchy is stable, but the memory image and pin transaction log are the durable end-to-end oracles.

### L2 - Gate-level top

**DUT:** gate-level `tt_um_lahnb_sgdma`, IHP simulation libraries, and the L1 external environment.

**Use for:**

- a selected high-value subset of L1 directed tests
- reset and initialization behavior
- post-synthesis connectivity, polarity, and bus-enable checks
- X-propagation investigations
- SDF back-annotation when the flow produces a compatible artifact

L2 is not the main randomized level. Tests must avoid relying on RTL hierarchy or source-level state encodings.

## Configuration dimensions

Every regression result records:

- DUT level: L0, L1, or L2
- simulator and version
- RTL or netlist revision
- random seed
- `DMA_BUF_DEPTH`
- timing profile and overridden delay values
- gate and SDF mode where applicable

The intended buffer-depth sweep is `1, 2, 4, 8`, while the V1 implementation and tapeout configuration remain `DMA_BUF_DEPTH=1`. RTL exposes `DMA_BUF_DEPTH` as a module parameter on `tt_um_lahnb_sgdma` / `sys_controller` (package `DMA_BUF_DEPTH_MAX=8` sizes interface widths). Larger values test depth-agnostic correctness without changing the V1 tapeout configuration; the M5 sweep still depends on the sim Makefile wiring `-GDMA_BUF_DEPTH=N`.

## Milestone ladder

Milestones are cumulative. A milestone exits only when its listed evidence is reproducible from a clean test build.

### M0 - Toolchain and smoke

**Entry:** current RTL compiles and the platform layout in `02-platform.md` is implemented.

**Exit:**

- pinned Python dependencies import in the project virtual environment
- Icarus runs one L1 directed copy from PSRAM0 to PSRAM0
- destination bytes match expected bytes
- the seed and exact reproduction command are printed

M0 deliberately uses L1 so the first smoke validates the TT wrapper path, not only the engine.

### M1 - PSRAM model and QPI protocol

**Entry:** M0.

**Exit:**

- dual APS6404L models implement required V1 QPI reads and writes
- model protocol policing rejects unsupported opcode, malformed phase count, bad address bit, invalid CE# overlap, flash-CS assertion, and ASIC-versus-device bidirectional SIO drive overlap (`Q-SIO-OWN`)
- behavioral `Q-*` rows assigned to M1 pass at L0 and applicable L1 cases
- Icarus and Verilator agree on the directed protocol set

Timing-delay sweeps and sample-edge closure are M3 (closed 2026-08-10).

### M2 - Reference model and directed behavior

**Entry:** M1.

**Exit:**

- golden chain interpreter matches frozen TCD semantics
- final-memory and ordered-transaction scoreboards agree
- all M2 `TC-*` cases pass at L1
- all applicable `CHK-*` monitors run in every test and remain clean
- same-device, cross-device, chaining, `QUIT`, zero length, bus yield, and reset cases pass

**M2 acceptance status:** `pass` (2026-08-08, L1 Icarus, `TIMING_PROFILE=ideal`, `SEED=1`, `DMA_BUF_DEPTH=1`).

Evidence (W5 Acceptance):

- `test/scripts/run_smoke.sh` green
- `tests.test_dma_directed`: 13 M2 descriptor/data `TC-*` PASS; `TC-DEPTH` / `dma_buf_depth_sweep` skipped for M5 (module exit 0)
- `tests.test_reset_and_bus`: 11/11 START/BUS/RESET `TC-*` PASS
- Migrated M1 modules green under shared bring-up / `dispose_run`: `test_qspi_negative`, `test_qspi_timing`, `test_qspi_reset_protocol`, `test_qspi_ownership`, `test_qspi_pin_disposition`
- Independent pin monitor live (`via=pin`); reference dual-axis scoreboard; always-on ARB/HS/CTRL/PIN `CHK-*` disposed on ordinary DMA paths

Out of M2 (residuals, do not reopen the M2 gate):

- CI L1 Icarus smoke job still open
- M3: delays, `Q-LAUNCH` / `Q-RXEDGE`, `Q-CSP` / `Q-CHD` / `Q-TERM` (closed 2026-08-10; physical `T-HZ` remains post-M3 closure)
- M4 formal `FP-*`
- M5 random / `COV-*` and `TC-DEPTH` (harness must select `DMA_BUF_DEPTH` 2/4/8)
- Optional model-plane Z→0 retirement (`tb_top` / `tb_engine` float→0)
- `test_qspi_pin_disposition` retains the intentional model-plane dispose contract (`assert_model_pin_disposition`); ordinary paths use `dispose_run` / pin
- Ownership suite keeps one consolidated `@cocotb.test`; `TC-OWN-*` are sub-steps, not `TEST_FILTER` names (full re-split deferred)
- Catalog follow-up: BUS_GNT-aware CTRL/HS checkers so MCU pass-through negatives need not detach those monitors

### M3 - Delay-annotated timing in simulation

**Entry:** M2 and documented delay defaults.

**Exit:**

- model-side input delay and return delay are runtime configurable
- setup/hold and turnaround monitors report timestamped margins
- nominal and boundary sweeps are reproducible
- `Q-LAUNCH` passes: driven SIO and OE change only while SCK is low and settle before the sampling rise
- `Q-RXEDGE` passes: RTL sampling behavior matches the documented external rising-edge contract
- any zero or negative modeled nominal margin is resolved or explicitly blocks progression

M3 supplies pre-STA evidence. It does not close a `T-*` row.

**M3 acceptance status:** `pass` (2026-08-10).

Evidence:

- Delay layer and CE# / ownership delay rerun under `TIMING_PROFILE=nominal` (documented APS6404L min/max AC with zero testbench transport placeholders): `tests.test_qspi_timing`, `tests.test_qspi_timing_delay`, `tests.test_qspi_ownership`
- Launch / RX: `tests.test_qspi_timing_launch_rx` at L0 (`LEVEL=engine`) for `Q-LAUNCH` / `Q-RXEDGE`, including `tACLK` endpoints via `TIMING_PROFILE=sweep` when selected
- Margin gate on legal baselines: recorded present min margins must be strictly positive
- Cross-sim: Icarus ≡ Verilator on the directed timing set above
- Cleanup contract: centralized `PendingLedger` / `finalize_all` in `test/common/lifecycle.py`; directed cases `TC-RXEDGE-PENDING-AT-STOP`, `TC-PENDING-SURVIVES-CLEAR`, `TC-TIMED-WRAPPER-STOP-ISOLATION`, `TC-CTRL-DATA-PAIR-PENDING-AT-STOP`, `TC-LIVE-CE-FRAME-AT-STOP`

Out of M3 (residuals, do not reopen the M3 gate):

- Physical `T-HZ` and other `T-*` remain STA / demoboard closure
- Fuller `PSRAM_TACLK_NS` / path-delay sweep matrix beyond the documented nominal + endpoint boundary cases is post-M3 if not already covered
- Delayed post-rise `Q-RXEDGE` under non-zero `D_OUT_*` (per-signal DUT-to-device output path delay) after CE# rise cleanup remains a known follow-up
- Suites that attach `ce_monitor=True` with default `reset_truncated=FORBID` may need REVIEW disposition like smoke when a `RESET-TRUNCATED` `Q-LAUNCH` appears
- Margin gate asserts only fields present on a legal baseline; write-path baselines may omit CEM/CSP/CHD mins; boundary-pass margins near zero are expected by construction
- Lifecycle intentional non-fails and incomplete-window diagnostics: see `06-checkers.md`
- CI L1 Icarus smoke job, M4 formal, M5 random / `COV-*` / `TC-DEPTH`, Z→0 retirement, BUS_GNT-aware CTRL/HS remain unchanged open items

### M4 - Formal control-plane safety

**Entry:** M2. M3 may proceed independently once shared RTL assumptions are stable.

**Exit:**

- required `FP-*` safety properties prove with their assigned engines and depths
- induction obligations use documented helper invariants
- required state and arc covers are reachable
- bounded deadlock checks complete at documented depths
- assumptions constrain only legal environment behavior and do not assume the property being proved

### M5 - Randomized regression and coverage

**Entry:** M2 through M4.

**Exit:**

- constrained-random descriptor chains and host request injection pass on Icarus
- the designated high-volume suite passes on Verilator
- failures reproduce from one printed seed and command
- depth 1 passes its applicable suite
- `DMA_BUF_DEPTH` values 2, 4, and 8 pass their applicable suite once the sim harness selects the module parameter; until the harness lands this M5 item remains `blocked`
- required `COV-*` points and crosses meet closure criteria or have reviewed exclusions

### M6 - Gate-level and X checks

**Entry:** M5 and an available synthesized netlist.

**Exit:**

- selected L1 sign-off tests pass at L2 with Icarus and IHP cell models
- reset and bus ownership remain clean at gate level
- randomized X-initialization and X-assignment runs have no unexplained divergence
- SDF status is recorded as pass, blocked, or not applicable with reason
- unresolved physical `T-*` rows are handed to STA and demoboard closure

### M7 - FPGA hardware validation

**Entry:** M0 through M5 complete. Once that cocotb/RTL sim gate is met, FPGA testing must be ready to run: demoboard/FPGA bring-up including MicroPython firmware under `firmware/` (D30; see [`12-firmware.md`](../12-firmware.md) and human roadmap) is allowed and needed before or as M7 starts, not deferred until after M7. Host-side `firmware/tests` unit logic may start earlier; demoboard HIL is this milestone. M6 may proceed independently since it requires a different artifact, the synthesized ASIC netlist, while M7 requires only an FPGA-synthesizable build of the same RTL.

**Exit:**

- the RTL synthesizes for the selected FPGA target and fits the carrier board's connector and voltage requirements in the ASIC's pin position
- MCU firmware drives START, TCD installation, and DONE handshaking against the FPGA exactly as it will drive the ASIC
- the selected high-value hardware regression subset passes with real dual PSRAM devices: same-device copies, both cross-device directions, chaining, `QUIT`, zero length, bus handoff, and reset recovery
- any divergence from simulation is triaged as a firmware, board, FPGA-only artifact, or RTL defect before it is dismissed
- the firmware and hardware test scripts used are retained and tied to the RTL revision they validated

## Sign-off gates

### RTL verification freeze

Before declaring RTL verification complete:

- M0 through M5 are complete
- every required `Q-*`, `CHK-*`, `TC-*`, `FP-*`, and `COV-*` row is `pass`
- no unresolved reproducible seed remains
- Icarus is green on the full required suite
- Verilator is green on its assigned regression subset
- all waivers name an owner, rationale, affected configuration, and expiration condition

### Shuttle freeze

Before shuttle freeze:

- RTL verification freeze is complete
- M6 is complete for the final netlist
- M7 FPGA hardware validation passes on the carrier board with real MCU firmware
- all required `T-*` rows are closed by STA and/or demoboard evidence
- the final configuration is 66 MHz maximum `clk`, SCK=`clk/2`, rising-edge RX unless a documented architecture decision changes it
- the demoboard passes same-device, both cross-device directions, chaining, bus handoff, and reset recovery

## Failure handling

- Minimize a failure while preserving its seed and timing profile.
- Classify it as DUT, model, checker, reference model, tool divergence, or physical-assumption failure.
- A checker or model defect does not waive the behavior it was intended to verify.
- When a simulator divergence appears, whether at compile time or in a test result, retain a reduced reproducer and assign one expected behavior from the language and cocotb contracts before suppressing a configuration. A compile-time failure is rerun under Verilator specifically for its more detailed diagnostic before triage, per `02-platform.md`.
- Do not mark a parent milestone complete while a required child ID is `fail`, `wip`, or `blocked`.

## Related

- Index and stable IDs: `00-index.md`
- Platform and commands: `02-platform.md`
- Architecture: `../03-architecture.md`
- Protocol and timing source: `../05-qspi-psram.md`
- Physical timing checklist: `../11-timing-analysis.md`
