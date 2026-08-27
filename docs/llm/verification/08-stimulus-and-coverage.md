# Stimulus and Functional Coverage

## Purpose and scope

This document owns the stable `TC-*` directed-test catalog, constrained-random stimulus contract, and `COV-*` functional-coverage catalog for the V1 descriptor DMA. It specifies work for M2 and M5. It does not add test code.

The durable end-to-end oracles are the final memory image and ordered QPI transaction log specified in `05-reference-model.md`. All applicable `CHK-*` monitors run in every case. Internal state may be sampled for coverage at L1 while hierarchy is stable, but no test passes solely because an internal state was observed.

## Stable test-case catalog

IDs identify required behavior, not Python function names. One implementation may cover several IDs, but results and failures must report each ID separately. New cases get new IDs. Retired IDs remain reserved.


| ID                | Level | Directed stimulus                                                                                                                         | Required result                                                                                                                               | Milestone                         |
| ----------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| `TC-SMOKE`        | L1    | One PSRAM0 to PSRAM0 copy, length 1, then a quit TCD                                                                                      | Destination byte matches, ordered transactions are fetch, read, write, fetch, and DONE returns                                                | M0                                |
| `TC-QPI-READ`     | L0    | `0xEB` reads on each device with lengths 1 and 11                                                                                         | Correct command, 24-bit address, six dummy cycles on the pin decoder (`CHK-HS-OPCODE` wait half live; `pin_monitor=True`), nibble order, device CE#, and exact payload count | M1                                |
| `TC-QPI-WRITE`    | L0    | `0x02` writes on each device with representative lengths                                                                                  | No dummy cycles, correct nibble order, exact payload count, and correct device CE#                                                            | M1                                |
| `TC-TCD-BE`       | L1    | Known 11-byte descriptor containing pointer `0x123456` serialized as `12 34 56`, dest_device=0, all three device flags exercised, reserved bits zero | Pointer fields decode big-endian and all defined flags decode at their assigned positions                                                     | M2                                |
| `TC-TCD-DEST1`    | L1    | Sibling vector: dest_device=1 and dest_ptr[23]=1 (D35)                                                                                    | Pin writes land on PSRAM1 with `A[22:0]` masked; frozen A0 vector stays dest_device=0                                                         | M5 residual                       |
| `TC-SAME-0`       | L1    | PSRAM0 to PSRAM0 copy                                                                                                                     | Exact payload copy with only PSRAM0 selected                                                                                                  | M2                                |
| `TC-SAME-1`       | L1    | PSRAM1 to PSRAM1 copy after head fetch on PSRAM0                                                                                          | Exact payload copy with data transactions on PSRAM1                                                                                           | M2                                |
| `TC-CROSS-01`     | L1    | PSRAM0 source to PSRAM1 destination                                                                                                       | Read and write select different devices and data matches                                                                                      | M2                                |
| `TC-CROSS-10`     | L1    | PSRAM1 source to PSRAM0 destination                                                                                                       | Read and write select different devices and data matches                                                                                      | M2                                |
| `TC-CHAIN`        | L1    | At least three executable TCDs followed by quit, with non-contiguous buffers                                                              | Every TCD executes once in order and all destination ranges match                                                                             | M2                                |
| `TC-NEXT-DEVICE`  | L1    | Chain whose next TCD alternates between PSRAM0 and PSRAM1                                                                                 | Each fetch uses `NEXT_DEVICE`; address zero remains a valid link                                                                              | M2                                |
| `TC-LEN-CORNERS`  | L1    | Lengths `0`, `1`, `N-1`, `N`, `N+1`, `2N-1`, `2N`, `2N+1`, and `255`, omitting duplicate or invalid values for the selected `N`           | Zero is a no-op, full chunks and final partial chunks are exact, and no extra transaction occurs                                              | M2; `2N-*` added for M5 `COV-DEPTH-LEN` |
| `TC-QUIT`         | L1    | Quit TCD with nonzero pointer and length fields                                                                                           | Quit is not executed, no data read or write follows that fetch, and DONE returns                                                              | M2                                |
| `TC-EMPTY`        | L1    | Quit TCD at fixed head `0x000000` on PSRAM0                                                                                               | Exactly one descriptor fetch, no data transaction, then DONE                                                                                  | M2                                |
| `TC-RESTART`      | L1    | Complete a chain, replace or retain the head, then issue a new START                                                                      | Every accepted START begins by fetching `0x000000` on PSRAM0, independent of stale working state                                              | M2                                |
| `TC-ADDR-WIDE`    | L1    | Valid TCD and payload addresses below, at, and above `0x010000`, including near `0x7FFFFF`, plus SRC/DEST/NEXT with `ptr[23]=1`           | All 23 address bits (`A[22:0]`) reach the wire; pin log masks `ptr[23]` (D35)                                                                 | M2                                |
| `TC-OVERLAP`      | L1    | Same-device source and destination ranges that overlap in each direction                                                                  | Result matches the architecture's sequential chunk behavior and the ordered transaction oracle, not an assumed full-buffer `memmove` snapshot | M2                                |
| `TC-START-ACTIVE` | L1    | START during fetch/read/write plus cycle-accurate ``UPDATE``, then stall. ``NEW_FETCH`` is not 2-flop-schedulable from the WRITE wrap-up slot (that landing is UPDATE) | Synchronized START is observed in those states, ignored, and not queued; later command needs a fresh IDLE edge. NEW_FETCH ignore shares the D22 active-path with UPDATE | M2                                |
| `TC-START-HELD`   | L1    | Hold raw START high through acceptance and completion, then lower it                                                                      | Exactly one synchronized rising-edge pulse and no unintended restart                                                                          | M2                                |
| `TC-START-PHASE`  | L1    | Sweep raw START assertion phase and legal pulse width around `clk` edges                                                                  | Captured assertions produce one pulse after synchronization; intentionally uncaptured short pulses do not create partial or repeated commands | M2                                |
| `TC-BUS-IDLE`     | L1    | Assert and release BUS_REQ in IDLE, including START while request or grant is high                                                        | Grant follows only after ASIC OE release, all `uio_oe` clear under grant, and blocked START is not queued                                     | M2                                |
| `TC-BUS-BOUNDARY` | L1    | Assert BUS_REQ in `NEW_FETCH`, `NEW_OP`, and `UPDATE`, before transaction launch                                                          | No new QPI transaction starts, grant occurs, and release resumes the retained state exactly once                                              | M2                                |
| `TC-BUS-ACTIVE`   | L1    | Assert BUS_REQ during fetch, payload read, and payload write                                                                              | Current QPI transaction completes atomically, then grant occurs before any next transaction                                                   | M2                                |
| `TC-BUS-PHASE`    | L1    | Assert BUS_REQ during command, address, dummy, read-data, write-data, and CE# end padding                                                 | No phase is torn, no CE# is switched early, and resume neither repeats nor skips a chunk update                                               | M2                                |
| `TC-BUS-REPEAT`   | L1    | Multiple request, grant, release cycles in one descriptor chain, including a request adjacent to completion                               | Data and transaction log equal an uninterrupted run except for legal idle gaps                                                                | M2                                |
| `TC-RESET-IDLE`   | L1    | Reset from IDLE and while BUS_GNT is active                                                                                               | DONE returns high, BUS_GNT clears, all shared OE clears during reset, and post-reset START uses the fixed head                                | M2                                |
| `TC-RESET-ACTIVE` | L1    | Reset during every controller state and each QPI phase, including one-cycle `CS_ON` and `SEND_CMD_2`                                      | Transaction may be truncated by reset, but CE# and OE become reset-safe, working state clears, and no spontaneous resume occurs               | M2                                |
| `TC-RESET-REPEAT` | L1    | Run one directed chain to normal quit completion, assert and release `rst_n` from IDLE, re-initialize source and destination memory identically, then run the identical chain again with a fresh START | The second run's ordered transaction log and final memory are byte-for-byte identical to the first run; no working state, counter, or pointer carries over across the reset boundary | M2                                |
| `TC-DEPTH`        | L1    | Run the applicable directed suite at each compile-time `DMA_BUF_DEPTH` in `1..DMA_BUF_DEPTH_MAX` (8), including tapeout **N=5**, via `make depth` or `test/scripts/run_depth_sweep.sh` (one isolated compile per depth; not a cocotb function in `tests.test_dma_directed`) | Final memory and transaction lengths follow `k=min(N, remaining)` with no depth-specific functional change                                    | M5; **pass** N=1..8 (2026-08-16) |


`TC-OVERLAP` records actual V1 byte or chunk ordering. Firmware must not infer stronger overlap semantics than the architecture provides.

### M2 directed acceptance (2026-08-08)

All M2 rows in the table above (everything except `TC-DEPTH`) are `pass` at L1 Icarus under `ideal` / seed 1 / depth 1:

- Descriptor/data: `tests.test_dma_directed` (13 cases); `TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`) is M5-only via `make depth` / `run_depth_sweep.sh`, not a case inside that module
- START / bus / reset: `tests.test_reset_and_bus` (11/11)
- Shared helpers: `test/common/directed.py` (install, read-back, done-wait, dual-axis compare, dispose window)
- Makefile `make directed` default filter enumerates the 13 directed function names and excludes the skipped depth sweep (do not use a dishonest bare `TEST_FILTER=directed`)

Ownership negatives live in `tests.test_qspi_ownership` as one consolidated test (`ownership_shared_bus_negatives`); `TC-OWN-*` IDs are sub-steps, not selectable filters. Full per-case re-split is deferred past M2.

`TC-RESET-ACTIVE` and `TC-RESET-REPEAT` continue to run normal `Q-*` timing checks up to the sampled reset edge. Any apparent violation fully explained by the reset-driven OE release is reported as a distinct `RESET-TRUNCATED` event per `04-timing-in-sim.md`, not folded into an ordinary timing pass or fail.

### M3 timing and cleanup directed cases (2026-08-10)

M3 timing evidence uses dedicated modules rather than expanding the M2 DMA `TC-*` table. Stable case IDs:

| ID | Module | Level | Required result |
|---|---|---|---|
| `TC-LAUNCH-NOMINAL-PASS` / `TC-LAUNCH-SCK-HIGH-VIOLATION` | `tests.test_qspi_timing_launch_rx` | L0 | `Q-LAUNCH` clean under `nominal`; injected SCK-high drive fails |
| `TC-LAUNCH-TSP-VIOLATION` / `TC-LAUNCH-THD-VIOLATION` / `TC-LAUNCH-THD-SAME-FS` | `tests.test_qspi_timing_launch_rx` | L0 | Short `tSP` / `tHD` (SIO setup/hold vs rising SCK) and exact-`tHD` same-fs hold fail `Q-LAUNCH` |
| `TC-LAUNCH-OE0-SIO-IGNORED` | `tests.test_qspi_timing_launch_rx` | L0 | SIO value change while OE=0 is not a launch |
| `TC-RXEDGE-NOMINAL-PASS` / `TC-RXEDGE-TACLK-BOUNDARY-*` | `tests.test_qspi_timing_launch_rx` | L0 | `Q-RXEDGE` clean; `tACLK` endpoints under `sweep` when selected |
| `TC-RXEDGE-WRITE-ONLY-NA` | `tests.test_qspi_timing_launch_rx` | L0 | Write-only disposes `Q-RXEDGE=na`, not pass |
| `TC-RXEDGE-L1-READ-PASS` | `tests.test_qspi_timing_rxedge` | L1 | Timed L1 DMA read; armed rising SCK capture; no `rdata_valid` alias |
| `TC-RXEDGE-PENDING-AT-STOP` | `tests.test_qspi_timing_launch_rx` | L0 | unresolved launch fails `Q-RXEDGE` with `reason=dispose` |
| `TC-PENDING-SURVIVES-CLEAR` | `tests.test_qspi_timing_launch_rx` | L0 | same finding after `BringUp.clear`, tagged `reason=window-clear` |
| `TC-TIMED-WRAPPER-STOP-ISOLATION` | `tests.test_qspi_timing_launch_rx` | L0 | retired delayed tasks do not drive DUT or append events |
| `TC-RXEDGE-RACE-DEVICE-PLANE` | `tests.test_qspi_timing_launch_rx` | L0 | under `TIMING_PROFILE=sweep` + race `D_OUT_*`, delayed device-plane CE# commit audits post-rise launches via second `close_scope` |
| `TC-CTRL-DATA-PAIR-PENDING-AT-STOP` | `tests.test_qspi_cleanup` | L1 | MCU `0xEB` with dummy and no payload bytes leaves pairing pending; dispose fails `CHK-CTRL-DATA-PAIR` with `reason=dispose` from the report (monitors are cleared after dispose) |
| `TC-LIVE-CE-FRAME-AT-STOP` | `tests.test_qspi_cleanup` | L1 | still-open CE# after a completed opcode fails `Q-PHASE` at dispose (model + pin = count 2) |
| `TC-QPI-ASIC-SIO-X` | `tests.test_qspi` | L0 | ASIC-selected write with selected-model extra SIO OE (not `fault_sio_oe`, which mutes the engine) fails `Q-SIO-X` / `CHK-PIN-KNOWN` plus `Q-SIO-OWN` (count 4) and companion `Q-OPCODE` |
| CE# / CSP / CHD / TERM delay cases | `tests.test_qspi_timing`, `tests.test_qspi_timing_delay` | L1 | legal baselines + directed violations under `nominal` |

Lifecycle policy for incomplete windows: `06-checkers.md`. Catalog `Q-*` status and REPRO: `04-timing-in-sim.md`.

## Constrained-random generation



### Determinism

Use the single `SEED` contract from `02-platform.md`. Derive independent child streams for descriptors, memory contents, START scheduling, BUS_REQ scheduling, and reset scheduling from the base seed plus stable stream names. Adding a draw to one stream must not perturb the others. The legal-chain generator splits source/destination device choice (`devices`) from NEXT-device choice (`next_device`); changing that split drifts seed-determined random chains and is expected.

For every run, save a stimulus manifest containing:

- base and child seeds
- generated TCD bytes and their device/address locations
- initial source, destination, and descriptor memory ranges
- expected chain interpretation
- raw START transitions with simulation timestamps
- BUS_REQ transitions and target state or phase
- reset transitions
- `DMA_BUF_DEPTH`, timing profile, simulator, and RTL revision



### Legal chain generator

Firmware-legal chain generation is implemented as one class, not a sequence of free functions, so a single field's construction rule can be inspected, overridden, or replaced independently while constructing or debugging a specific corner. The class:

- is constructed from a base `SEED` and the stream-name contract in the Determinism section above; it owns its own child `random.Random` streams and does not read module-global random state,
- exposes one entry point that returns a complete legal chain, an ordered list of TCD values plus the terminating quit descriptor, together with the exact byte layout chosen for each descriptor and payload region in memory,
- exposes one separately callable method per generated dimension (chain length, per-TCD device tuple, per-TCD length class, per-TCD address class, payload pattern, and layout/overlap class) so a test can hold every other dimension at a fixed, inspectable value while sweeping or targeting one,
- reuses `reference/tcd.py` encoding and `reference/chain.py` validation rather than duplicating field ranges or bit layout, so any chain the generator returns is guaranteed to pass `validate_tcd` and to interpret without a `reference_limit` error, and
- treats the bias table below as data the class consumes, not literal constants inlined in its generation logic, so a directed test can construct the same class with a narrowed or overridden bias table for a targeted scenario without forking the generator.

The class lives in `reference/generator.py`, in the `reference/` package alongside the golden interpreter it depends on but in its own module: generating stimulus and interpreting it are distinct responsibilities that must stay separately testable, and the class must not import cocotb.

Generate only firmware-legal chains for the main correctness regression:

- one to eight executable TCDs followed by one quit TCD
- fixed head at `0x000000` on PSRAM0
- subsequent TCDs on either device, including address zero as a legal link when it does not create an unintended loop
- Complete ranges inside `0x000000..0x7FFFFF` on `ptr[22:0]` (`ptr[23]` don't-care; D35)
- big-endian pointer serialization and reserved `CTRL_FLAGS[3:0]` zero (firmware contract; the DUT latches that last nibble and ignores it in V1)
- independently selected source, destination, and next devices (NEXT-device draws use a dedicated `next_device` stream, not the source/destination `devices` stream),
- initialized source bytes and deterministic sentinels around destination ranges
- acyclic chains unless a dedicated reset-termination experiment deliberately generates a runaway chain

Illegal encodings belong in protocol-policing or robustness tests with an explicit expected response. They must not be mixed into the legal random suite because V1 leaves out-of-range operations and malformed firmware input undefined.

### Biases

The generator is random but intentionally corner-biased:

- transfer length: 60 percent from the distinct values in `{0,1,N-1,N,N+1,255}`, 40 percent uniform over `0..255`
- chain length: favor 1, 2, and the configured maximum
- device tuple: equal weight for `0->0`, `1->1`, `0->1`, and `1->0`; independently bias `NEXT_DEVICE` to change device
- addresses: favor `0x000000`, nibble and byte boundaries, `0x00FFFF/0x010000`, chunk-boundary offsets, 1K page edges, and the highest legal complete range
- layout: favor disjoint ranges, then exact source/destination equality and legal overlap cases
- data: include all-zero, all-one, walking bits, incrementing bytes, alternating nibbles, and random bytes

The generated quit descriptor may contain random nonzero data fields. The oracle must still require no execution of those fields.

## Asynchronous START phase jitter

START is an asynchronous raw level followed by a two-flop synchronizer and rising-edge detector. Simulation cannot model metastability faithfully. It can test phase-dependent digital capture and ensure that one captured transition creates exactly one controller pulse. Capture-required stimulus holds raw START for at least three `clk` periods (`pulse_start` default); a one-cycle pulse is injection-only. Capture-required pulses that produce zero synchronized edges are `InjectionError`; capture-uncertain zero-edge pulses are `idle_uncaptured`.

For each selected raw pulse width, choose an assertion offset uniformly across one `clk` period and add focused offsets immediately before, exactly on, and immediately after a modeled rising edge. Deassertion gets an independently jittered phase.

Use two stimulus classes:

1. Capture-required pulses are held for at least three complete `clk` periods after the first possible sampling edge. They must produce exactly one synchronized START edge.
2. Capture-uncertain pulses are shorter or placed on a sampling boundary. Either zero or one synchronized edge is acceptable, but two edges, X-dependent control, or a partial command is not. Record whether capture occurred and check behavior from that observation.

Do not encode a precise setup/hold aperture or claim CDC analog sign-off from RTL simulation. The firmware contract remains that START must be held long enough to be captured and returned low before another command.

## BUS_REQ injection model

BUS_REQ is also synchronized at top level, so schedule the raw edge early enough to distinguish raw injection time from the cycle when the controller sees `bus_req`.

Random injection has two orthogonal selectors:

- Controller state: `SYS_CTRL_IDLE`, `NEW_FETCH`, `FETCH`, `NEW_OP`, `READ`, `WRITE`, `UPDATE`, and `STALL`.
- QPI phase when a transaction is active: `CS_ON`, command, address, wait, read data, write data, `SCLK_OFF`, and `CS_OFF`.

For each targeted state or phase, jitter raw BUS_REQ so the synchronized assertion lands at the start, middle, or final cycle of that region where its duration permits. Hold the request until BUS_GNT is observed, retain it for a random legal host interval (biased towards lower periods), then model release-before-seize by removing host drive before deasserting BUS_REQ.

Required invariants are independent of injection point:

- a transaction already accepted completes atomically
- no new `qspi_txn_valid` occurs while synchronized BUS_REQ is high
- BUS_GNT rises only after `qspi_busy` is low and all shared ASIC OE is clear
- release resumes the retained controller state without duplicate fetch, read, write, or update
- START while request or grant is active is ignored and not queued



## `DMA_BUF_DEPTH` sweep

RTL exposes `DMA_BUF_DEPTH` as a module parameter (package `DMA_BUF_DEPTH_MAX=8`; V1 tapeout and default sim/Make use **N=5**). Verification elaborates any integer `1..DMA_BUF_DEPTH_MAX` via Makefile `-G`/`-P`.

`TC-DEPTH` (directed suite at each compile-time `DMA_BUF_DEPTH`) is the harness loop `make depth` or `test/scripts/run_depth_sweep.sh`: one isolated compile and directed run per depth, not a dedicated cocotb test in `tests.test_dma_directed`. **Pass (2026-08-25):** N=1..8 green (Icarus 15/15 per depth, `run_depth_sweep-20260825-190207.log`).

`COV-DEPTH` (compile-time depth bins), `COV-DEPTH-LEN`, and `COV-DEPTH-DEVICE` sample from `coverage.json` written per directed window via `CoverageSampler` in `test/common/directed.py`; each compiled `N` hits the matching `COV-DEPTH` bin when its directed run passes. The regenerated 2026-08-25 merge at `test/runs/m5_coverage_closure.json` has all eight depth bins (`closed=true`).

Compile and run depths `1` through `8` in isolated build directories. Depth **5** is the V1 tapeout configuration; other depths are verification configurations for depth-agnostic correctness and do not change the frozen tapeout configuration.

At every depth:

- run `TC-SMOKE`, all TCD semantic cases, all four device directions, chaining, BUS_REQ boundary cases, and representative reset cases
- include lengths `0`, `1`, `N-1`, `N`, `N+1`, `2N-1`, `2N`, `2N+1`, and `255`, deduplicated and range-limited
- compare final memory and ordered QPI transactions against `k=min(N, remaining)`
- check full chunks, a partial final chunk, exact nibble counts, and pointer updates
- isolate compile products by depth and record the elaborated depth in the run artifact

The depth sweep does not exercise the known `tCEM` threshold because 8 is below the first failing read depth of 60 at the target SCK.

## Functional coverage catalog

Coverage is sampled from decoded transactions, host-visible behavior, reference-model events, and stable internal state observation where necessary. Hits count only when all applicable checkers and scoreboards pass for that case.


| ID                 | Coverage point (1-D enum unless noted)            | Required bins                                                                                               | Closure                                                                     |
| ------------------ | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `COV-LEN`          | Transfer length class                             | `0`, `1`, `N-1`, `N`, `N+1`, `2N-1`, `2N`, `2N+1`, `255`, middle                                            | Every applicable distinct bin at each swept depth                           |
| `COV-CHUNK`        | Chunk position and size                           | only chunk, first full, middle full, final full, final partial                                              | Every applicable bin at each swept depth                                    |
| `COV-DEVICE`       | Source x destination device                       | `0x0`, `0x1`, `1x0`, `1x1`                                                                                  | 100 percent                                                                 |
| `COV-NEXTDEV`      | Current fetch device x next fetch device          | all four transitions                                                                                        | 100 percent                                                                 |
| `COV-CHAINLEN`     | Executable TCD count                              | `0`, `1`, `2`, `3+`                                                                                         | 100 percent                                                                 |
| `COV-END`          | Descriptor outcome                                | quit, zero-length follow, one-chunk complete, multi-chunk complete                                          | 100 percent                                                                 |
| `COV-ADDR`         | Address class per SRC, DEST, and NEXT             | zero, below 64K, at or above 64K, 1K-edge neighborhood, highest legal range                                 | 100 percent for each pointer role                                           |
| `COV-DATA`         | Payload pattern                                   | zero, ones, walking or alternating, incrementing, random                                                    | 100 percent                                                                 |
| `COV-CTRL-STATE`   | Controller state reached                          | all eight encoded states                                                                                    | 100 percent                                                                 |
| `COV-QPI-PHASE`    | QPI phase reached                                 | all ten encoded states, with READ_DATA and WRITE_DATA separately                                            | 100 percent                                                                 |
| `COV-BUS-STATE`    | BUS_REQ synchronized assertion in a controller state | IDLE, NEW_FETCH, FETCH, NEW_OP, READ, WRITE, UPDATE                                                      | 100 percent; STALL assertion is excluded because request is already high    |
| `COV-BUS-PHASE`    | BUS_REQ synchronized assertion in an active QPI phase | CS_ON, command, address, wait, read data, write data, SCLK_OFF, CS_OFF                                   | 100 percent of legal bins (1-D enum; not a BUS-PHASE x RESUME cross)     |
| `COV-BUS-RESUME`   | Stall origin after leaving STALL                  | IDLE, NEW_FETCH, NEW_OP, UPDATE                                                                             | 100 percent (1-D enum of origins; not a origin x action cross)            |
| `COV-START-PHASE`  | Raw START assertion phase                         | early, near-edge before, on-edge, near-edge after, late                                                     | All bins for capture-required pulses                                        |
| `COV-START-RESULT` | Observed START capture result                     | idle accepted, idle uncaptured short pulse, active ignored, request/grant ignored, held-high single capture | Record observed result only, not intended capture class                     |
| `COV-RESET-STATE`  | Reset assertion x controller state                | all eight encoded states                                                                                    | 100 percent                                                                 |
| `COV-RESET-PHASE`  | Reset assertion x external QPI phase              | idle/pad, command, address, wait, read data, write data, termination                                        | 100 percent                                                                 |
| `COV-DEPTH`        | Compile-time depth                                | `1`, `2`, `3`, `4`, `5`, `6`, `7`, `8` (each integer `1..DMA_BUF_DEPTH_MAX`; bin `5` is tapeout **N=5**) | All bins with assigned suite passing; **pass** (2026-08-16) |
| `COV-DEPTH-LEN`    | Depth x length class                              | every applicable `COV-LEN` corner at every depth `1..8`                                                     | 100 percent; **pass** with reviewed exclusions at N=1/2 |
| `COV-DEPTH-DEVICE` | Depth x source/destination tuple                  | all four tuples at every depth `1..8`                                                                       | 100 percent; **pass** (2026-08-16) |




## Coverage closure and exclusions

**M5 coverage rematch (2026-08-25).** Regenerated `test/runs/m5_coverage_closure.json` from retained L1/L2 fragments: `closed=true`; `missing={}`; depths 1..8; reviewer `tb-closure-2026-08-25`. `COV-BUS-PHASE` and `COV-BUS-RESUME` remain 1-D enums (bin names unchanged; not a BUS-PHASE x RESUME cross). Exclusion matching is `(id, bin, depth)` so an N=1 `N-1` exclusion does not hide N=5 `N-1`. `TC-DEPTH` is 15/15 directed cases per compile-time N (Icarus, `make depth` / `run_depth_sweep.sh`). The 2026-08-16 first-exit claim used reviewer `M5-close` on an open rematch; that stamp is retired. M6 stays open: SDF blocked, Verilator-X is not four-state. Firmware oracle-hash / budget drift vs `test/reference/chain.py` remains a firmware follow-up (D30: firmware must not import `test/`).

M5 closes when:

- every required bin and cross above is hit by a passing run
- all `TC-*` cases assigned through M5 pass
- no unresolved reproducible random seed remains
- the required Icarus suite and assigned Verilator high-volume suite agree after classified tool differences
- the final report is regenerated from retained manifests rather than hand-edited counts

Coverage percentage alone is not closure. A bin with a checker, scoreboard, or model failure does not count.

An exclusion is allowed only when a bin is structurally unreachable, illegal under the frozen architecture, duplicated by parameter collapse, or not applicable to a DUT level. Each exclusion must record:

- `COV-*` ID and exact bin or cross
- reason and architecture citation
- affected level, simulator, and depth
- evidence of unreachability or illegality
- reviewer and date
- expiration condition

Examples of legitimate exclusions are `N-1=0` duplicating the zero bin at depth 1, and `STALL` as a fresh BUS_REQ assertion state because reaching STALL already requires the synchronized request. Lack of seeds, simulator inconvenience, or a known DUT bug is not an exclusion. A waived requirement remains visible as `fail` or `blocked` until the owning requirement is changed.

## Regression allocation

- Icarus is authoritative for the full required directed suite, four-state behavior, and a representative random seed set.
- Verilator runs the high-volume legal random suite at L1 and, once assigned, the depth sweep across `1..DMA_BUF_DEPTH_MAX`.
- Every Verilator-only failure is reduced and reproduced on Icarus, or classified with a retained tool-divergence reproducer.
- L2 reuses only the high-value subset defined in `09-gate-level-and-x.md` via `tests.test_gate_level` at flattened `DMA_BUF_DEPTH=5`. Bus and reset cases use top pins only (no RTL hierarchy). L2 does not reopen M5 functional coverage. Entry: `test/scripts/run_gl.sh` or `GATES=yes make` (copies `test/results.xml`).



## Related

- Verification levels, milestones, and sign-off: `01-strategy.md`
- Seeds, commands, and artifacts: `02-platform.md`
- Golden chain interpreter and reset-interrupted/repeated-run scoreboard rules: `05-reference-model.md`
- Timing catalog, `Q-SCKIDLE`, and reset-interrupted timing classification: `04-timing-in-sim.md`
- Gate-level, reset-randomization, and X policy: `09-gate-level-and-x.md`
- Architecture and host CDC: `../03-architecture.md`
- TCD semantics: `../04-tcd-and-datapath.md`
- QPI protocol: `../05-qspi-psram.md`

