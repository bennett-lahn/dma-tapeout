# Gate-Level and X Verification

## Purpose and boundary

This document defines M6 for the synthesized L2 DUT. It separates three activities that answer different questions:

1. Icarus functional gate-level simulation checks the synthesized netlist, IHP cell connectivity, polarity, reset, bus ownership, and real four-state `0/1/X/Z` behavior without annotated path delay.
2. Optional Icarus SDF experiments add implementation delays when the netlist, SDF hierarchy, cell models, and simulator support have been qualified together.
3. Verilator X experiments perturb values that Verilator would otherwise choose deterministically. They are sensitivity tests, not four-state simulation and not gate-level sign-off.

None of these replaces STA. SDF simulation can expose delay-sensitive functional behavior and timing-check contamination, but STA remains the authority for `T-*` path closure and the demoboard remains the authority for external number validity.

## Tiny Tapeout L2 baseline

The local TTIHP template establishes the required entry shape:

- `GATES=yes`
- gate netlist `gate_level_netlist.v`
- `tb` as the HDL top
- IHP `sg13g2_io.v` and `sg13g2_stdcell.v`
- `GL_TEST`, `FUNCTIONAL`, and `SIM` defines
- a build directory separate from RTL simulation

This project preserves that compatible path through `LEVEL=gl`, with `GATES=yes` implying the same L2 flow. The template path is the baseline functional gate run. It does not by itself prove that SDF was generated, annotated, or honored.

The project netlist is expected to be the unpowered `nl` view described by D27. Do not mix powered and unpowered cell-model interfaces. Record the exact netlist, PDK revision, cell-model files, synthesis or hardening run, and top instance used for every L2 result.

## Required functional L2 flow

Icarus is the required L2 simulator because it supports four-state gate nets and matches the Tiny Tapeout gate-test path. The initial M6 run is zero-delay or unit-delay functional gate simulation with no SDF claim.

The required subset is:


| Test ID                                         | Why it remains at L2                                               |
| ----------------------------------------------- | ------------------------------------------------------------------ |
| `TC-SMOKE`                                      | Basic synthesized end-to-end connectivity and sequential operation |
| `TC-TCD-BE`                                     | Descriptor bit and byte connectivity through synthesized logic     |
| `TC-SAME-0`, `TC-SAME-1`                        | Both PSRAM CE# paths and shared SIO mapping                        |
| `TC-CROSS-01`, `TC-CROSS-10`                    | Device-select muxing in both directions                            |
| `TC-CHAIN`, `TC-QUIT`, `TC-RESTART`             | State retention, chain control, reset-to-fixed-head behavior       |
| `TC-BUS-IDLE`, `TC-BUS-ACTIVE`, `TC-BUS-REPEAT` | Grant polarity, atomic completion, OE release, and resume          |
| `TC-RESET-IDLE`, `TC-RESET-ACTIVE`              | Initialization and reset recovery across gate storage elements     |


Run the final tapeout depth, `DMA_BUF_DEPTH=1`. Larger-depth RTL configurations are for L1 sweeps via the module parameter; they are not required at L2 unless separate netlists are intentionally hardened.

L2 tests use only top-level pins, resolved shared-bus signals, decoded QPI transactions, final memory, and ordered transaction logs as pass criteria. They must not depend on RTL hierarchy, source enum values, internal register names, or synthesis-generated instance names.

### Functional L2 pass criteria

- every selected test and applicable external `CHK-*` monitor passes
- no unexpected X or Z reaches DONE, BUS_GNT, `uio_oe`, active CS, SCK, or driven SIO after reset release
- all `uio_oe` bits are zero while reset is asserted and while BUS_GNT is high
- no RAM CE# overlap, flash-CS assertion, or ASIC-versus-PSRAM/SPI dual drive of bidirectional SIO occurs
- no gate-model warning indicates an unresolved cell, port-width mismatch, or missing primitive
- result artifacts identify the netlist hash and PDK model revision

An X on an intentionally undriven resolved SIO net during PSRAM turnaround is not automatically a failure. It must match the expected ownership phase and be absent from sampled data after the model's valid-drive point.

## Reset sequencing and randomized reset

The RTL reset is synchronous active-low inside the design, while top-level combinational OE gating clears shared drivers whenever raw `rst_n` is low. L2 stimulus must therefore distinguish immediate pad safety from clocked state convergence.

Every L2 test starts with:

1. known host inputs and host QSPI drivers at high-Z
2. `rst_n=0` spanning at least three clean rising `clk` edges
3. checks that all shared ASIC OE is already clear during reset
4. reset release away from a clock edge for the baseline run
5. at least two additional clocks before interpreting synchronized host inputs

Randomized mid-transaction reset is a separate reproducible campaign. Choose the target from controller-observable operation classes and external QPI phases:

- idle, descriptor fetch, payload read, payload write, update or inter-transaction gap, and granted stall
- CE# lead-in, command, address, wait, read data, write data, and CE# termination

Jitter assertion and release phase across one `clk` period. Hold reset low across at least two rising edges so synchronous state reset is required. A boundary experiment that does not span a rising edge may check immediate OE clearing, but it must not require state convergence, as this is not a valid `rst_n` assertion.

After every reset:

- no pre-reset transaction is allowed to resume
- DONE returns high after the first clocked reset action
- BUS_GNT is low
- all working state behaves as reset state
- a new legal START fetches `0x000000` on PSRAM0

Random reset scheduling is derived from `SEED` and saved in the stimulus manifest. It is not the same mechanism as Verilator randomized initialization.

## X observation policy

Check four-state values explicitly before integer conversion. Cocotb conversion failure is never coerced to zero. Classify every observed X or Z by location and phase:

- expected high-Z from an owner that has released the shared bus
- expected resolved value driven by the PSRAM or MCU model
- uninitialized state before clocked reset
- timing-check notifier contamination
- contention between two drivers
- unknown control or data escaping after reset

Only the first two may be expected. A mask must be phase-specific and bit-specific. Broad X-to-zero conversion, disabling all timing notifiers, or ignoring an entire bus is not acceptable closure.

When an X reaches a checker:

1. retain the first-X timestamp and the earliest upstream signal visible in the waveform
2. rerun the same seed and configuration with waves
3. determine whether the source is missing reset, illegal stimulus, contention, timing annotation, cell-model limitation, or DUT logic
4. fix or document the root cause before marking the case pass (manual step explicitly working alongside user)



## True four-state gate simulation

A true four-state gate experiment evaluates the synthesized netlist and cell primitives with `0`, `1`, `X`, and `Z` values. The required Icarus L2 run is in this category even without SDF. It can reveal:

- flops or latches that remain unknown because reset did not reach them
- unknown select propagation through synthesized muxes
- shared-net contention and undriven periods, including ASIC-versus-PSRAM/SPI dual drive of bidirectional SIO (`CHK-PIN-SIO-OWN` / `Q-SIO-OWN`)
- gate connectivity or polarity errors hidden by RTL initialization assumptions
- X contamination from timing-check notifiers when timing checks are active

Four-state simulation is still an imperfect hardware model. RTL and gate primitives can be X-optimistic or X-pessimistic, and actual silicon powers up to a binary value rather than an X. Treat X as evidence of insufficient determinism or a model interaction that needs investigation, not as a literal voltage.

## Verilator X experiments

Verilator's normal execution is not a four-state event simulation. Its X controls choose binary values for constructs or storage that would otherwise involve unknowns. Compile dedicated diagnostic configurations with:

```text
--x-assign unique --x-initial unique
```

Run multiple Verilator seeds and record the exact compile options, runtime seed, reset randomization mode, simulator version, and test seed. Where the selected Verilator version supports its runtime random-reset controls, vary them as a second dimension rather than silently relying on one default initialization pattern.

The experiment asks whether behavior depends on an unspecified initial value or an X-producing assignment. A failure that changes with Verilator's X seed is a strong bug lead. A pass means only that sampled binary resolutions did not expose a dependency. It does not show:

- propagation of an X through combinational logic
- resolution of multiple four-state drivers
- Z behavior on the shared QSPI bus
- timing-check notifier effects
- gate-level SDF correctness

Run these experiments primarily at L0 and L1 for fast diagnosis. Optional L2 Verilator runs may help reduce a netlist problem, but they do not satisfy the required Icarus L2 row.

## SDF strategy



### Status model

SDF is optional until the hardening flow produces a compatible SDF artifact. Record one of:

- `pass` - annotation coverage was checked, delays were observed, and the assigned timed subset passed
- `fail` - a qualified timed run produced an unresolved functional or timing-check failure
- `blocked` - no SDF, incompatible hierarchy or cell naming, unsupported annotation construct, or simulator limitation prevents meaningful execution
- `na` - reviewed and deliberately not applicable to this netlist stage, with reason

Missing SDF is never reported as a passing zero-delay gate run.

### Qualification before using results

The IHP standard-cell Verilog models contain `specify` paths and sequential timing checks with notifiers, initially at zero values. A meaningful SDF attempt requires all of the following:

- SDF generated from the same final netlist and timing corner
- annotation scope matching the actual DUT instance in the L2 wrapper
- cell and instance names matching the loaded unpowered IHP models
- timescale and delay units checked
- Icarus specify processing enabled, including the required `-gspecify` mode
- no compile define or alternate model view that bypasses the intended timing behavior
- annotation diagnostics retained and reviewed rather than suppressed

Use an elaboration-time `$sdf_annotate` call in the gate wrapper or an equally explicit simulator-supported mechanism. Select minimum, typical, or maximum consistently and record the chosen corner. Do not infer successful annotation from the absence of a fatal error.

### Annotation sanity test

Before running DMA tests, prove that SDF is active:

- annotation log names the intended scope and reports useful cell or path coverage
- a known cell path exhibits a nonzero delay matching the selected SDF corner within simulator resolution
- an intentionally tightened or controlled timing-check experiment produces the expected diagnostic or notifier effect
- the same probe without SDF returns to the functional delay behavior

If Icarus accepts the file but ignores material constructs, reports broad unmatched paths, or cannot execute required timing checks, classify SDF as `blocked`. Do not patch away annotation errors merely to get a green result. Use STA for closure and qualify another SDF-capable simulator if timed gate simulation becomes a shuttle requirement.

### Timed subset

After qualification, run:

- `TC-SMOKE`
- one same-device and both cross-device directions
- `TC-BUS-ACTIVE`
- `TC-RESET-ACTIVE`

Use the external timed PSRAM model and preserve the distinction between:

- netlist and cell delays from SDF
- PSRAM and board placeholders from the Python delay layer
- physical checks still owned by `T-*`

Avoid double-counting pad or flight delays. The run configuration must state exactly where each delay is modeled.

An SDF functional pass may support investigation of QSPI launch, sampling, and OE handoff. It does not close setup and hold, pad load, transition, routed clock, or board rows by itself.

## Simulator and evidence matrix


| Activity               | DUT        | Simulator                 | Logic model                                | Required for M6        | Interpretation                                  |
| ---------------------- | ---------- | ------------------------- | ------------------------------------------ | ---------------------- | ----------------------------------------------- |
| RTL regression         | L0/L1 RTL  | Icarus                    | Four-state RTL                             | Yes, inherited from M5 | Primary functional correctness                  |
| Fast random regression | L1 RTL     | Verilator                 | Binary execution with configured X choices | Yes, inherited from M5 | Throughput and X sensitivity                    |
| Functional gate        | L2 netlist | Icarus                    | Four-state gates, no SDF                   | Yes                    | Required connectivity, reset, OE, and X check   |
| Timed gate             | L2 netlist | Icarus plus qualified SDF | Four-state gates with supported annotation | Conditional            | Delay-sensitive diagnostic, not STA replacement |
| Gate diagnostic        | L2 netlist | Verilator                 | Binary execution with X choices            | No                     | Optional reduction aid                          |




## M6 closure

M6 exits when:

- the required functional Icarus L2 subset passes on the final netlist
- reset and BUS_REQ ownership behavior is clean at L2
- no unexplained post-reset X or Z reaches a sampled control or data value
- Verilator X-initial and X-assignment experiments complete across the designated seed set with no unexplained divergence
- SDF is recorded honestly as `pass`, `fail`, `blocked`, or `na` with evidence
- every remaining `T-*` item is handed to STA or demoboard closure

An M6 waiver names the exact test or observation, netlist and PDK revision, owner, rationale, risk, and expiration condition. Functional gate failures, unknown bus ownership, or unexplained X-dependent behavior cannot be waived merely because RTL simulation passes.

## Related

- Verification venues, L2 definition, and M6 gate: `01-strategy.md`
- Gate selector, artifacts, and simulator matrix: `02-platform.md`
- Directed tests, reset coverage, and random seeds: `08-stimulus-and-coverage.md`
- QSPI timing split and `T-*` ownership: `../11-timing-analysis.md`
- IHP shuttle and netlist decision: `../07-decision-log.md`
- Tiny Tapeout gate template: `../../../ttihp-verilog-template/test/`

