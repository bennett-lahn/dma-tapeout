# Timing in Simulation

## Purpose

This document owns the simulation-provable `Q-*` checks for the APS6404L QPI interface. It specifies a Python cocotb transport-delay layer, timestamped setup and hold monitors, read-edge reconciliation, and timing sweeps for L0 and applicable L1/L2 runs.

Delay-annotated simulation is an early warning and regression tool. It can show that a stated combination of launch, flight, device, and sample delays has zero or negative modeled margin. It cannot certify routed IHP pad delay, TT mux delay, package and board parasitics, waveform quality, process variation, or a physical PSRAM. Those remain `T-*` closure work in `../11-timing-analysis.md`.

## Timing planes

The environment keeps these event planes distinct:

1. **DUT plane** - raw DUT output values, output enables, external SCK transition times, and internal capture indications.
2. **Device plane** - delayed CE#, SCK, and master-driven SIO values observed by the PSRAM parser.
3. **Return plane** - delayed PSRAM SIO values presented to DUT `sio_in` or resolved `uio_in`.

The PSRAM parser in `03-psram-model.md` consumes only device-plane events. The DUT consumes only the resolved return-plane bus. Monitors may observe all three planes but must label the plane used in each margin report.

## Python transport-delay model

### DUT to device

For each DUT-driven pin transition at simulation time `t_src`, create an independent delayed-copy task with the transition value captured before the task starts:

```text
D_OUT(signal) = TB_TCO(signal) + TB_FLIGHT_OUT(signal)
t_device(signal) = t_src + D_OUT(signal)
```

After `Timer(D_OUT(signal), unit="ns")`, the task updates the corresponding device-plane signal. CE#, SCK, SIO values, and SIO output enable use the same mechanism. Each transition is transported, not collapsed into the most recent value.

The base configuration exposes `TB_TCO` and `TB_FLIGHT_OUT`. The implementation must also permit per-signal overrides for SCK, CE#, and SIO so post-route data can represent clock-to-data and clock-to-CE skew. Applying one common delay to every output only shifts the waveform in time and does not create relative setup or hold stress.

Do not implement this path as one coroutine that awaits a delay before watching for another edge. That serial form can miss transitions. An edge collector must timestamp every source event and use `cocotb.start_soon(...)` for its delayed assignment. Same-time assignments use a stable sequence number so results do not depend on Python task ordering.

### Device read response

APS6404L read data is available `tACLK` after a falling SCK edge. For a model-observed falling edge at `t_fall_device`:

```text
t_model_drive = t_fall_device + tACLK
t_dut_input = t_model_drive + TB_FLIGHT_IN(SIO)
```

The model schedules the correct read nibble after `Timer(tACLK, unit="ns")`. A separate return transport task then applies `TB_FLIGHT_IN`. The model must not delay from the rising sample edge, and it must not combine `tACLK` with the six protocol dummy cycles. Dummy-cycle counting determines which falling edge launches the first data nibble; `tACLK` determines when that nibble becomes valid.

Each delayed response carries the transaction generation described in `03-psram-model.md`. A stale generation is discarded. CE# rising prevents any new data launch and starts SIO release; the last driven value may remain until the configured `tHZ` expires.

### Bus resolution

The wrapper resolves DUT and model SIO drives after their respective delays:

- neither drives - high impedance or the wrapper's explicit idle value,
- exactly one drives - that driver's nibble,
- both drive the same known value - still report overlap, even if the resolved value agrees,
- both drive different values - resolve as unknown where supported and fail immediately.

Drive ownership is checked from delayed output enables, not inferred only from resolved logic values. Any ASIC-plus-device overlap on a bidirectional SIO bit fails `Q-SIO-OWN` / `CHK-PIN-SIO-OWN` immediately after artifacts are preserved. Equal levels do not make dual drive legal.

## Parameters and sources

All device AC defaults in this table are copied from repository documentation. The authoritative repository source is the APS6404L Rev 2.3 PDF; `../05-qspi-psram.md` summarizes the values and `../../datasheets/md/APS6404L_3SQR.md` preserves converted Table 10 text.

| Parameter | Simulation default or range | Meaning | Repository source |
|---|---:|---|---|
| `PSRAM_TACLK_NS` | sweep `2.0` to `5.5`; nominal profile `5.5` | Falling SCK to read output | `../05-qspi-psram.md`, `../../datasheets/md/APS6404L_3SQR.md` Table 10 |
| `PSRAM_TCSP_NS` | `2.5` minimum | CE# setup to rising SCK | Same Table 10 sources |
| `PSRAM_TCHD_NS` | `3.0` minimum | CE# hold from rising SCK | Same Table 10 sources |
| `PSRAM_TCPH_NS` | `18.0` minimum | CE# high between bursts | Same Table 10 sources |
| `PSRAM_THZ_NS` | `5.5` maximum | CE# high to SIO high impedance | Same Table 10 sources |
| `PSRAM_TSP_NS` | `2.0` minimum | Input setup to active SCK edge | Same Table 10 sources |
| `PSRAM_THD_NS` | `2.0` minimum | Input hold from active SCK edge | Same Table 10 sources |
| `PSRAM_TCEM_US_EXT` | `4.0` maximum | CE# low, extended grade | Same Table 10 sources |
| `PSRAM_TCEM_US_STD` | `8.0` maximum | CE# low, standard grade | Same Table 10 sources |
| `PSRAM_TCH_MIN_RATIO` | `0.45` | Minimum SCK high width as a fraction of minimum period | `../../datasheets/md/APS6404L_3SQR.md` Table 10, summarized in `../11-timing-analysis.md` |
| `PSRAM_TCL_MIN_RATIO` | `0.45` | Minimum SCK low width as a fraction of minimum period | Same Table 10 sources |
| `PSRAM_TCH_MAX_RATIO` | `0.55` | Maximum SCK high width as a fraction of minimum period | Same Table 10 sources |
| `PSRAM_TCL_MAX_RATIO` | `0.55` | Maximum SCK low width as a fraction of minimum period | Same Table 10 sources |
| `PSRAM_TKHKL_NS` | `1.5` maximum | SCK rise or fall time, for reporting only in digital simulation | Same Table 10 sources |

The architecture fixes system `clk` at a 66 MHz maximum and registered SCK at `clk/2`, approximately 33 MHz. Those are project values from D16 in `../03-architecture.md` and `../05-qspi-psram.md`, not APS6404L AC defaults. At that target, the derived system-clock period is approximately 15.15 ns and the derived SCK period is approximately 30.30 ns.

Testbench path parameters are placeholders, not device specifications:

| Parameter | Initial default | Meaning |
|---|---:|---|
| `TB_TCO_NS` | `0.0` | DUT register/internal/pad launch delay represented in simulation |
| `TB_FLIGHT_OUT_NS` | `0.0` | DUT/TT/package/board flight to the PSRAM input |
| `TB_FLIGHT_IN_NS` | `0.0` | PSRAM/package/board return flight to DUT input |

Zero means "not yet annotated", not "physically zero". These values must be back-filled from STA and board estimates before relying on a physical-margin pre-check. Per-signal overrides inherit the base value until supplied.

No APS6404L typical `tACLK` is invented. The profile named `nominal` is the project's standard delay-annotated run, but it deliberately uses the documented 5.5 ns maximum rather than claiming a device-typical value. Sweep results always print the actual value.

## Timing profiles

`TIMING_PROFILE` selects a recorded set of runtime values:

- `ideal` - all transport and response delays are zero; protocol and edge-order checks still run.
- `nominal` - the standard delay-annotated profile; device response and release use documented maxima, input requirements use documented minima, and unfilled TB path placeholders remain visibly zero.
- `sweep` - one or more values are supplied explicitly by the sweep driver; the run manifest records every point.

The platform default remains `ideal` as specified in `02-platform.md`. M3 evidence requires `nominal` and boundary sweeps, not only `ideal`.

## Timestamped timing checks

Use integer simulator time at the finest configured precision for comparisons. Convert to nanoseconds only for configuration and reporting. Do not compare rounded display strings.

### Input setup

At every device-plane rising SCK edge during command, address, or write data:

- SIO setup margin is `edge_time - last_sio_or_sio_oe_change`.
- Pass requires margin `>= tSP`.
- CE# setup on the first rising edge is `edge_time - selected_ce_fall_time`.
- Pass requires margin `>= tCSP`.

The checker reports the observed margin, required margin, phase, nibble index, and source transition timestamp.

### Input hold

After each device-plane rising SCK edge during a driven phase:

- any SIO value or output-enable change before `edge_time + tHD` fails,
- selected CE# rising before `edge_time + tCHD` fails, and
- a transition exactly on the boundary is handled consistently at simulator time precision and reported with the raw timestamps.

Implement hold checks by retaining protected-window end times and evaluating every subsequent transition. Do not sleep in the SCK monitor and thereby miss another edge.

### CE# pulse and gap

- `Q-CEM` measures every continuous selected CE# low interval against the configured grade limit.
- `Q-CPH` measures from one CE# rising edge to the next CE# falling edge, including same-device and cross-device transactions.
- `Q-CHD` measures from the final rising SCK edge to CE# rising.
- `Q-CSP` measures from CE# falling to the first rising SCK edge.

APS6404L-class devices define clocked behavior only while CE# is low. SCK must remain low for the entire interval during which no device is selected (flash CS, RAM A CE#, and RAM B CE# all high at L1; both engine CS outputs high at L0). A SCK transition during that interval is an erroneous SCK cycle, not a benign don't-care, and is checked as `Q-SCKIDLE` below. This holds regardless of which side of the shared bus currently owns drive; `qspi_engine`'s own `CS_ON`, `SCLK_OFF`, and `CS_OFF` padding already produces the required waveform by construction, so the check exists to catch a regression, not to describe an optional style choice.

### Read termination

The APS6404L documentation recommends a longer final-read CE# hold satisfying `tCHD > tACLK + tCLK` so the controller can latch the last data before termination. This recommendation is recorded in `../05-qspi-psram.md` and the converted datasheet text near Figure 2.

`Q-TERM` therefore checks two layers:

1. required architectural behavior - the final expected read nibble reaches the DUT and is committed, SCK is then held low, and CE# rises without an extra SCK or extra byte;
2. advisory numeric report - measured final CE# hold minus `(configured tACLK + observed tCLK)`.

The advisory margin must be reported and reviewed. It is not silently converted into a different Table 10 minimum, and a simulation report does not replace physical closure.

### SCK parked while deselected

`Q-SCKIDLE` requires SCK to remain low for the complete interval during which no device is selected on the shared bus. At L1 this means flash CS, RAM A CE#, and RAM B CE# are all high; at L0, where flash CS is not an engine port, it means both engine CS outputs are high. A violation is reported at the device-plane transition timestamp together with the identity of the last-active and next-active transaction, if any, so an apparent violation caused by MCU pass-through activity to a device outside the DUT's own CS outputs is not confused with an ASIC-caused one.

This is a shared-bus protocol check, not an arbitration-park check: `CHK-ARB-PARK` in `06-checkers.md` judges only the ASIC's own driven value while it holds the bus, while `Q-SCKIDLE` judges the resolved SCK net and applies whenever no device is selected, including while the MCU masters the bus.

## `Q-LAUNCH`

`Q-LAUNCH` is the stable ID for the driven-phase launch prerequisite behind physical `T-SP-HD`.

For every command, address, and write-data nibble:

- DUT SIO value and SIO output enable may change only while external SCK is low,
- the delayed device-plane value and enable must settle at least `tSP` before the next rising SCK edge,
- they must remain stable for at least `tHD` after that edge, and
- the first command nibble, first address nibble, and first write-data nibble are checked explicitly at phase boundaries.

OE release into read dummy and OE reclaim after a read are also required to occur while SCK is low. Device-driven read-data changes are not `Q-LAUNCH` events; they are checked under `Q-RXEDGE`.

`Q-LAUNCH` exists to hold `qspi_engine` to the APS6404L setup/hold contract on an ongoing basis, independent of any specific RTL revision's implementation history: any future change to the engine's output timing must still satisfy this window. The check remains `todo` until the M3 harness runs it against current RTL; whether it passes or fails is determined by that execution, not asserted in this document.

## `Q-RXEDGE`

`Q-RXEDGE` is the stable ID for reconciling model launch, external SCK, and RTL read capture.

For every read-data nibble:

1. identify the device-plane falling edge that launches the nibble,
2. record when the delayed nibble becomes valid at DUT input,
3. identify the following external rising SCK edge required by D16,
4. record the actual DUT capture event through `rdata_valid` and `rdata`, and
5. require a one-to-one match between expected rising edges and captured nibbles.

Pass requires:

- no capture attributed to an external falling edge,
- exactly `2 * byte_len` captures,
- the captured nibble equals the model nibble associated with that rising edge,
- data arrived before capture with positive recorded setup margin, and
- no stale response from a prior transaction is captured.

At L0, internal engine signals may be observed to diagnose the capture event. The durable requirement remains the port behavior: `rdata`, `rdata_valid`, SCK, and SIO must agree. At L1/L2, the pin transaction log and resulting memory data remain the end-to-end evidence.

## Stable `Q-*` catalog

These IDs retain the meanings established in `../11-timing-analysis.md`. Moving their owning specification here does not rename or reuse them.

| ID | Requirement | Primary level | Milestone | Status |
|---|---|---|---|---|
| `Q-CEM` | Every CE# low pulse remains below `tCEM`: 4 us extended or 8 us standard | L0/L1 | M1, delay rerun M3 | pass |
| `Q-CPH` | CE# high gap is at least 18 ns between bursts | L0/L1 | M1, delay rerun M3 | pass |
| `Q-CSP` | CE# falls at least 2.5 ns before the first rising SCK | L0/L1 | M3 | todo |
| `Q-CHD` | CE# remains low at least 3.0 ns after the final rising SCK | L0/L1 | M3 | todo |
| `Q-TERM` | Final read data is committed before CE# rises, with SCK frozen and no extra beat; advisory long-hold margin is reported | L0/L1 | M3 | todo |
| `Q-MUX` | At most one RAM CE# is low; ASIC flash CS stays high while `~BUS_GNT` | L0/L1 | M1 | pass |
| `Q-SIO-OWN` | ASIC and any selected PSRAM/SPI device never drive the same bidirectional SIO bit at once; equal driven values still fail; ownership uses delayed OE / model-drive enables; legal phases follow `../03-architecture.md` | L0/L1 | M1, delay rerun M3 | pass |
| `Q-RST` | Asserted `rst_n` aborts the ASIC transaction, releases all top-level shared OE, and returns the engine/controller to reset state without a soft-abort command | L0/L1 | M1 | pass |
| `Q-SCKIDLE` | SCK remains low for the entire interval while no device is selected (flash CS, RAM A CE#, and RAM B CE# all high at L1; both engine CS outputs high at L0); no erroneous SCK cycle occurs while deselected | L0/L1 | M1 | pass |
| `Q-LAUNCH` | Driven SIO and OE change only with SCK low and meet modeled 2 ns setup and hold windows | L0 | M3 | todo |
| `Q-RXEDGE` | Each read nibble launched from a falling edge is captured exactly once on the documented following rising edge | L0, selected L1 | M3 | todo |

Every AC value in this catalog is sourced through the parameter table above from `../05-qspi-psram.md` and APS6404L Rev 2.3 Table 10. Status uses the vocabulary in `00-index.md`.

### M1 behavioral evidence (2026-08-03)

M1 `pass` rows above are under `TIMING_PROFILE=ideal`, `SEED=1`, `DMA_BUF_DEPTH=1`, Icarus 14.0 and Verilator 5.051 agreeing on the directed set. Logs: `test/runs/m1_t10_icarus_matrix.log`, `test/runs/m1_t10_verilator_matrix.log` (may be wiped by `make clean`).

| ID | Evidence module(s) | Level | Notes |
|---|---|---|---|
| `Q-CEM`, `Q-CPH` | `tests.test_qspi_timing` | L1 | Coarse directed thresholds; delay-annotated rerun remains M3 |
| `Q-MUX`, `Q-SIO-OWN`, `Q-SCKIDLE` | `tests.test_qspi_ownership` | L1 | Shared-bus monitor negatives + clean baseline |
| `Q-RST` | `tests.test_qspi_reset_protocol` | L1 (and L0 subset) | Dispose + `RESET-TRUNCATED` classification |
| `Q-LAUNCH`, `Q-RXEDGE`, `Q-CSP`, `Q-CHD`, `Q-TERM` | - | - | Still `todo` until M3 harness runs |

Cross-sim REPRO (per module; both sims exit 0 in the matrix logs):

```sh
source test/env.sh
test/scripts/run_test.sh LEVEL=top SIM=icarus SEED=1 COCOTB_TEST_MODULES=tests.test_qspi_ownership
test/scripts/run_test.sh LEVEL=top SIM=verilator SEED=1 COCOTB_TEST_MODULES=tests.test_qspi_ownership
# likewise: test_qspi_timing, test_qspi_reset_protocol, test_qspi_negative,
#           test_qspi_pin_disposition; LEVEL=engine: tests.test_qspi, tests.test_engine_attach
```

## Reset-interrupted timing checks

Timing monitors do not pause because a test intends to assert reset soon. Every `Q-*` window open before the sampled reset edge, the first rising `clk` edge observed with `rst_n=0`, is evaluated exactly as it would be in an uninterrupted run: the physical setup, hold, and CE#/SCK relationships that hold up to that edge do not depend on what happens afterward, and the interrupted operation's pre-edge timing is not exempted from the fundamental PSRAM timing requirements it is checking.

After the sampled reset edge, `rst_n=0` combinationally clears every shared `uio_oe` bit at top level (`CHK-RST-OE` in `06-checkers.md`), and sequential controller/engine state converges over the following edges (`CHK-RST-INTERNAL`, `Q-RST`). This can force CE#, SCK, or SIO to change in a way that would otherwise look like a `Q-CEM`, `Q-CPH`, `Q-CHD`, `Q-MUX`, `Q-SCKIDLE`, or `Q-LAUNCH` violation if judged only against the uninterrupted protocol contract.

Classify a timing-window observation whose violation is fully explained by the sampled reset edge's forced OE release and state convergence as a distinct `RESET-TRUNCATED` event, not a fail of the specific `Q-*` ID that would otherwise apply:

1. record the specific `Q-*` ID that would have fired, the reset-sample timestamp, and the forced signal proven responsible for the apparent violation,
2. require that every part of the window strictly before the sampled reset edge still meets its normal requirement,
3. require that the only explanation for the post-edge segment of the violation is the documented reset behavior in `Q-RST`, `CHK-RST-OE`, or `CHK-RST-INTERNAL`, and
4. report the event distinctly and permanently, for example `Q-CHD RESET-TRUNCATED at t=...`, so it is never silently dropped and never miscounted as an ordinary timing pass or fail.

A `RESET-TRUNCATED` event requires the same review discipline as a coverage exclusion in `08-stimulus-and-coverage.md`: it is recorded with the affected `Q-*` ID, the exact window, the reset-sample timestamp, the forced signal proven responsible, and a reviewer sign-off before it is excluded from that ID's pass/fail count. An unreviewed reset-adjacent anomaly is a `fail`, not a silent pass. A timing window that is already violated strictly before the sampled reset edge is an ordinary `Q-*` failure; reset does not retroactively excuse a violation that occurred while `rst_n=1`.

## `tACLK` sweep methodology

The required sweep bounds `tACLK` over the documented 2.0 ns minimum through 5.5 ns maximum at the project target of 66 MHz system `clk` and SCK=`clk/2`.

For each simulator and timing point:

1. run a directed L0 read with enough bytes to cover first-data, middle-data, and final-data boundaries;
2. use the same memory image, transaction, clock phase, and seed;
3. record device falling edge, model-drive time, DUT-input valid time, required rising edge, actual capture time, and next data transition for every nibble;
4. compute capture setup margin as `actual_capture_time - dut_input_valid_time`;
5. compute capture hold margin as `next_dut_input_change_time - actual_capture_time`;
6. require `Q-RXEDGE`, data equality, and capture count to pass; and
7. retain the first failing point and a copy-paste reproduction command.

Always run both documented endpoints. Intermediate points locate sensitivity, and a refined boundary search may be used after the first pass/fail transition. Do not extrapolate a passing endpoint from only one interior value.

After nonzero path estimates exist, sweep these independently:

- SCK DUT-to-device delay,
- SIO return flight delay,
- SIO/CE DUT-to-device delay for setup and hold, and
- clock-to-data skew represented by per-signal `TB_TCO` overrides.

A common-mode delay applied equally to SCK and master-driven SIO is recorded but must not be presented as setup/hold stress. The run report lists all effective per-signal delays.

The rising-edge nominal read margin is evaluated from actual timestamps, not only from a hand equation. A useful cross-check is:

```text
next required DUT capture
  - (DUT SCK launch + SCK output path + tACLK + SIO return path)
```

Any internal edge-detection latency or sampling offset must come from observed RTL events and be named separately. A zero or negative modeled margin blocks M3 until the RTL or timing assumption changes. A positive margin is only pre-STA evidence.

## Evidence and failure reporting

Each timing run records:

- RTL revision, DUT level, simulator/version, seed, and clock period,
- timing profile and every effective parameter,
- per-ID status,
- minimum observed setup, hold, CE# gap, CE# pulse, and read-capture margins,
- transaction and nibble indices for each minimum,
- first violation with all relevant timestamps,
- waveform path under the platform policy, and
- exact reproduction command.

An ideal-profile pass cannot close M3. Icarus and Verilator must agree on directed timing behavior, or the divergence must be reduced and classified before an ID passes.

## Signoff boundary

Simulation closes `Q-*` behavior only. It does not close:

- `T-ACLK` physical read timing,
- `T-SP-HD` routed output setup and hold,
- `T-CLKQ` analog clock quality,
- `T-HZ` physical bus turnaround,
- IHP pad, TT mux, package, PMOD, or board delay, or
- device behavior across voltage, temperature, loading, and silicon variation.

Those items remain in `../11-timing-analysis.md` for STA and demoboard evidence. The TB placeholders are inputs to an approximation, not substitutes for extracted or measured values.

## Related

- Timed PSRAM parser and dual-device behavior: `03-psram-model.md`
- Always-on runtime checkers and reset sampling rules: `06-checkers.md`
- Directed reset tests and coverage exclusion discipline: `08-stimulus-and-coverage.md`
- Verification venue split and M3 exit: `01-strategy.md`
- Platform configuration and artifacts: `02-platform.md`
- Architecture and D16 rising-edge policy: `../03-architecture.md`
- Repository APS6404L protocol and AC summary: `../05-qspi-psram.md`
- Physical `T-*` closure: `../11-timing-analysis.md`
- Converted APS6404L Rev 2.3 timing text: `../../datasheets/md/APS6404L_3SQR.md`
- Authoritative APS6404L Rev 2.3 PDF: `../../datasheets/pdfs/APS6404L_3SQR.pdf`
- Engine RTL implementing this contract: `../../../src/rtl/qspi_engine.sv`
