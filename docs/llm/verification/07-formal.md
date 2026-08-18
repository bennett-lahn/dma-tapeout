# Formal Verification

## Scope

**Status (D33):** M4 is not a V1 RTL verification freeze gate. Formal stubs under `test/formal/` remain the starting point. Do not claim M4 pass. Missing `FP-*` rows do not block V1.

M4 uses native WSL Yosys, SymbiYosys, and SMT solvers to verify V1 control-plane safety. This is Option B:

- bounded model checking for short counterexamples and reset checks
- k-induction for required safety invariants
- cover properties for state and arc reachability
- bounded watchdog checks for local deadlock
- `sys_controller` integrated with the real `qspi_engine`

The integration harness must not replace `qspi_engine` with an abstract responder. In particular, it must not assume values or timing for `qspi_busy`, `qspi_rdata_valid`, or `qspi_wdata_next`. Those signals come from the real engine RTL. `sio_in` remains external symbolic data.

Formal does not prove PSRAM storage fidelity, arbitrary-chain payload equivalence, analog timing, setup/hold time, or eventual completion of every descriptor chain. Those obligations belong to simulation, STA, and the demoboard as defined in `01-strategy.md`.

All depths in this document count rising edges of the 66 MHz system `clk`, not SCK edges. The engine generates SCK at `clk/2`, so every QPI beat consumes about two formal steps before state-transition overhead.

## Tool and execution contract

Run formal commands from WSL with native WSL executables:

```sh
yosys -V
sby --version
bitwuzla --version
yices-smt2 --version
z3 --version
```

The OSS CAD Suite environment described in `02-platform.md` is the intended source. Do not invoke Windows executables through WSL, install tools into the repository, or commit machine-specific paths.

Use:

- Bitwuzla as the primary BMC and cover solver
- Yices as the primary k-induction solver
- Z3 as a diagnostic cross-check for reduced failing jobs

A solver disagreement is not resolved by majority vote. Preserve the smallest failing job, exact tool versions, and generated trace, then determine whether the cause is RTL, property, unsupported language, or solver behavior.

## Planned formal layout

The implementation belongs under the layout reserved by `02-platform.md`:

```text
test/formal/
  engine/
    qspi_engine_harness.sv
    qspi_engine_bmc.sby
    qspi_engine_prove.sby
    qspi_engine_cover.sby
  integration/
    sys_qspi_harness.sv
    top_harness.sv
    control_bmc.sby
    control_prove.sby
    control_cover.sby
    control_deadlock.sby
  bind/
    qspi_engine_properties.sv
    sys_controller_properties.sv
    top_properties.sv
```

This document specifies that future layout but does not create it.

### Harness boundaries

`qspi_engine_harness.sv` is a leaf harness used to prove engine-local state, counter, chip-select, and pulse-count lemmas. Its request inputs are symbolic but constrained to the published D21 request contract.

`sys_qspi_harness.sv` instantiates both `sys_controller` and the real `qspi_engine`, with their production ports connected directly. It exposes symbolic post-synchronizer `start`, `bus_req`, and `sio_in`. It is the primary proof boundary for controller-engine handshake and progress properties.

`top_harness.sv` instantiates `tt_um_lahnb_sgdma`. It checks START synchronization, registered status, pin parking, and output-enable release at the production boundary. Its internal controller is still connected to the real engine.

The integration jobs must compile production files in this order:

1. `src/rtl/types.svh`
2. `src/rtl/qspi_engine.sv`
3. `src/rtl/sys_controller.sv`
4. `src/rtl/top.v` when the top harness is selected
5. the selected harness
6. the applicable bind files

Read all of them with SystemVerilog enabled. Bind files contain properties only. Environment assumptions belong in harnesses so the assumption boundary is visible.

### SymbiYosys job shape

Keep BMC, proof, cover, and deadlock jobs separate. A representative proof job has this shape:

```text
[options]
mode prove
depth 64
multiclock off

[engines]
smtbmc yices

[script]
read -formal -sv types.svh qspi_engine.sv sys_controller.sv
read -formal -sv sys_qspi_harness.sv sys_controller_properties.sv qspi_engine_properties.sv
prep -top sys_qspi_harness
```

Use `mode bmc` with Bitwuzla for counterexample search and `mode cover` with Bitwuzla for reachability. In `mode prove`, `smtbmc` performs base-case checking and temporal induction up to the configured depth. A larger depth is not automatically a stronger architecture claim. Record the first depth that closes and one reproducible margin run above it.

`DMA_BUF_DEPTH` is a module parameter (default 1). Formal results apply to the elaborated depth; do not report a 1/2/4/8 formal sweep unless each depth is deliberately bound and proven.

## Assertion style

Use the Yosys-supported synthesizable assertion subset: clocked `assert`, `assume`, and `cover`, plus `$past`, `$stable`, `$rose`, and `$fell` only where the selected tool versions support them. Prefer explicit history and watchdog registers over complex sequence syntax.

Bind checker ports explicitly to DUT signals. Do not rely on fragile deep hierarchical references for the main property expression. Internal FSM state, counters, `next_state`, and retained request data may be bound because this is RTL formal, but top-level pin properties should be stated at the production ports.

For example, request stability is best checked by capturing a request shadow on `txn_valid`, then comparing it while `busy` remains high:

```systemverilog
always_ff @(posedge clk) begin
   if (~rst_n) begin
      request_active <= 1'b0;
      saved_cmd      <= QSPI_CMD_FAST_READ;
      saved_addr     <= '0;
      saved_device   <= QSPI_PSRAM0;
      saved_len      <= '0;
   end else begin
      if (txn_valid) begin
         request_active <= 1'b1;
         saved_cmd      <= cmd;
         saved_addr     <= addr;
         saved_device   <= device_sel;
         saved_len      <= byte_len;
      end

      if (request_active && busy) begin
         assert(cmd == saved_cmd);
         assert(addr == saved_addr);
         assert(device_sel == saved_device);
         assert(byte_len == saved_len);
      end

      if (~busy)
         request_active <= 1'b0;
   end
end
```

The comparison deliberately ends when `busy` is low. D21 permits the controller to prepare the next request on that cycle.

## Assumptions audit

Every `.sby` job must print or preserve the set of active assumptions. Each assumption needs an ID, rationale, owning interface contract, and list of properties that depend on it.

### Baseline assumptions

| ID | Assumption | Allowed use |
|---|---|---|
| `FA-RST-INIT` | Synchronous `rst_n` is sampled low for at least the first formal clock edge. | Establish reset-reachable state for every required job. |
| `FA-RST-RUN` | After the initial reset window, `rst_n` stays high. | Main safety, cover, and deadlock jobs. Separate reset jobs exercise later reset assertions. |
| `FA-START-PULSE` | Direct `sys_controller.start` is at most one clock wide. | `sys_qspi_harness` only, because this port is defined as post-sync and edge-detected. |
| `FA-REQ-LEGAL` | Leaf-engine `txn_valid` occurs only while `~busy`; `cmd` is `0xEB` or `0x02`; `byte_len` is in `1..QPI_MAX_BYTES`; request fields remain stable while busy. | Leaf engine jobs only. Integration jobs must prove these facts from the real controller. |

`bus_req` may rise, fall, or remain asserted at any clock. The required arbitration properties must not assume a maximum host hold time.

`sio_in` is unconstrained in baseline jobs. Arbitrary read data may produce zero length, quit, any device flags, self-pointing chains, and invalid pointer values. This is intentional for unconditional control safety.

### Optional legal-descriptor profile

Some address and deep reachability checks need firmware-legal TCD contents. A separately named profile may constrain only descriptor nibbles actually sampled during FETCH so that:

- pointer bit 23 is don't-care (D35; not a legal-profile constraint)
- reserved `CTRL_FLAGS[3:0]` is zero in firmware-legal stimulus (the DUT latches that last nibble; V1 control ignores it, so nonzero reserved must not change `QUIT` / device flags)
- complete TCD, source, and destination ranges remain within `0x000000..0x7FFFFF`
- a bounded scenario contains the specific length, device flags, and quit or next link needed by a cover goal

This profile still drives data through symbolic `sio_in` and the real `qspi_engine`. It is not an abstract engine or memory model. Properties that use this profile must be marked conditional and must not be reported as unconditional proofs.

### Forbidden assumptions

Do not assume:

- `qspi_busy` eventually clears in an integration harness
- a fixed schedule for `qspi_rdata_valid` or `qspi_wdata_next`
- mutual exclusion of RAM chip selects
- `qspi_txn_valid` legality or request stability in integration
- `BUS_GNT` implies released output enables
- that every descriptor chain reaches `QUIT`
- that `bus_req` eventually deasserts
- the conclusion of any `FP-*` property

Those are proof targets or explicit non-guarantees. Assuming them would make the corresponding result circular.

### Reset profiles

Reset is synchronous and active-low in the RTL. Assertions about reset effects are evaluated after a rising `clk` edge that samples `rst_n==0`.

Use two profiles:

1. `reset_init`: reset at the initial sampled edge, then release for ordinary proofs.
2. `reset_recovery`: allow a later low pulse lasting at least one sampled edge and cover reset from representative controller and engine states.

Do not claim asynchronous pad shutdown from these proofs. The combinational `uio_oe` gating by `rst_n` can be checked as a Boolean safety property, but sequential state convergence still occurs only on a sampled clock edge.

## Stable `FP-*` safety catalog

All rows start at status `todo`. IDs remain stable if the implementation file or exact depth changes.

Depths are starting guidance, not guaranteed closure depths. `prove 32/64` means first attempt at 32, then 64 if induction needs more history.

| ID | Required condition | Harness | Method and starting depth |
|---|---|---|---|
| `FP-RST-SYS` | A sampled reset puts `sys_controller` in IDLE with `done=1`, `bus_gnt=0`, no pending write, and fixed-head state restored. | integration | BMC 8, prove 16 |
| `FP-RST-QSPI` | A sampled reset puts the engine in IDLE with `busy=0`, SCK low, both RAM CE# high, and response pulses low. | engine and integration | BMC 8, prove 16 |
| `FP-RST-OE` | `rst_n=0` implies every top-level `uio_oe` bit is 0. | top | BMC 4, prove 8 |
| `FP-SYS-STATE` | The controller state and retained stall state are valid enum values after reset. | integration | prove 16 |
| `FP-QSPI-STATE` | The engine state is a valid enum value after reset. | engine and integration | prove 16 |
| `FP-CS-MUTEX` | RAM A CE# and RAM B CE# are never low together. | engine, integration, top | BMC 64, prove 32 |
| `FP-FLASH-HIGH` | While the ASIC drives flash CS, its output value is high. | top | BMC 32, prove 16 |
| `FP-GNT-RELEASE` | `bus_gnt` implies `uio_oe==0`. | top | BMC 64, prove 32 |
| `FP-GNT-NOT-BUSY` | `bus_gnt` implies no real QPI transaction is busy. | integration and top | BMC 128, prove 64 |
| `FP-REQ-NO-START` | While synchronized `bus_req` is high, the controller does not issue `qspi_txn_valid`. | integration | BMC 96, prove 32 |
| `FP-BUS-ATOMIC` | A request arriving during `qspi_busy` cannot grant the bus until that transaction completes. | integration and top | BMC 128, prove 64 |
| `FP-START-GATE` | START changes execution only from controller IDLE with `~bus_req`; active or requested START pulses are not queued. | integration | BMC 64, prove 32 |
| `FP-TXN-LEGAL` | Every controller `qspi_txn_valid` pulse implies `~qspi_busy`, `~bus_req`, legal command, and nonzero legal length. | integration | BMC 128, prove 64 |
| `FP-TXN-PULSE` | `qspi_txn_valid` is one clock wide and cannot repeat before the accepted transaction completes. | integration | BMC 128, prove 64 |
| `FP-REQ-STABLE` | Accepted `{cmd, addr, device_sel, byte_len}` remains equal to its acceptance shadow while the real engine is busy. | integration | BMC 128, prove 64 |
| `FP-ADDR-MSB` | **Retired (D35).** Formerly required `addr[23]==0` on issued QPI addresses. Bit 23 is don't-care. | retired | n/a |
| `FP-RVALID-SCOPE` | `rdata_valid` is a one-clock pulse and occurs only in engine READ_DATA on a detected rising SCK. | engine and integration | BMC 96, prove 32 |
| `FP-WNEXT-SCOPE` | `wdata_next` is a one-clock pulse and occurs only in WRITE_DATA when another nibble remains. | engine and integration | BMC 96, prove 32 |
| `FP-RVALID-COUNT` | A completed legal read emits exactly `2 * accepted_byte_len` `rdata_valid` pulses. | engine and integration | BMC 128, prove 64 |
| `FP-WNEXT-COUNT` | A completed legal write emits exactly `2 * accepted_byte_len - 1` `wdata_next` pulses. | engine and integration | BMC 128, prove 64 |
| `FP-QSPI-COUNT` | Engine phase counters never overflow and remain within the bound for the active command and accepted length. | engine | BMC 96, prove 64 |
| `FP-DATA-COUNT` | **Retired (D31).** Former controller `data_cnt` bounds. Keep the ID; do not prove it. | integration | na |
| `FP-DYNAMIC-INDEX` | Every dynamic data-buffer part-select (`used_bits` from accepted `byte_len`) is reached only with a nonzero in-range index. TCD fetch has no skip index. | integration | BMC 384, prove 64 |
| `FP-STALL-RESUME` | A stall records one legal origin and, after `bus_req` falls, resumes that origin without inventing a transaction. | integration | BMC 192, prove 64 |
| `FP-UPDATE-ONCE` | One completed write causes at most one transfer-length update, and a BUS_REQ stall around UPDATE cannot repeat or skip that update. | integration | BMC 384, prove 96 |
| `FP-QUIT-IDLE` | After a fetched descriptor with `quit=1` reaches NEW_OP, no data transaction is issued and the controller returns to IDLE. | integration | BMC 192, prove 64 |
| `FP-ZERO-NO-DATA` | A non-quit zero-length descriptor issues no source read or destination write before proceeding to its next fetch. | integration | BMC 256, prove 64 |
| `FP-FIXED-HEAD` | The first fetch after every accepted START uses address 0 on PSRAM0, including after a prior quit. | integration | BMC 384, prove 96 |

`FP-RVALID-COUNT` and `FP-WNEXT-COUNT` use checker-local acceptance shadows and pulse counters. They must not use current `cmd` or `byte_len` after `busy` falls.

`FP-UPDATE-ONCE`, `FP-QUIT-IDLE`, `FP-ZERO-NO-DATA`, and `FP-FIXED-HEAD` are control properties, not full memory correctness. The reference model and scoreboard in later verification work own byte-level end-to-end semantics.

## Helper invariants for induction

Cross-FSM assertions are unlikely to close by induction if only the final property is present. Add small helper assertions and prove them as a mutually inductive set.

### Engine helpers

- `busy` is equivalent to `curr_state != QSPI_IDLE`.
- SCK is low in IDLE, CS_ON, SCLK_OFF, and CS_OFF.
- phase counter is zero in pad and idle states.
- WAIT is reachable only for a read command.
- READ_DATA is reachable only for an accepted read, and WRITE_DATA only for an accepted write.
- a transaction-active shadow is set by legal acceptance and cleared only when `busy` falls or reset is sampled.
- CE# selection agrees with the captured `device_sel`.

### Controller helpers

- NEW_FETCH and NEW_OP are the only transaction-launch states.
- FETCH, READ, and WRITE imply a transaction has been launched or is completing.
- `write_pending` distinguishes the read and write half of one chunk.
- `stalled_state` is written only when entering STALL and remains stable while stalled.
- active fetch address and device are captured before FETCH and remain stable through that fetch.
- write-buffer `used_bits` stays in range for the accepted length.
- a request shadow agrees with controller outputs throughout engine busy.

### Cross-FSM helpers

- controller launch implies engine IDLE in the acceptance cycle.
- once accepted, the engine remains the sole source of busy and nibble pulses.
- no STALL entry can overlap a new transaction launch.
- an in-flight engine transaction prevents `bus_gnt`.
- after engine completion, controller response depends on its retained operation state, not arbitrary external data timing.

Keep helpers as assertions, not assumptions. In one proof job they form a mutually inductive strengthening set. First prove leaf engine helpers independently, then include them as assertions in integration. If a future assume-guarantee optimization converts a proven leaf assertion into an assumption, it must be a separate audited job tied to the exact RTL revision and configuration. Baseline M4 sign-off must still include an end-to-end integration proof with the real engine.

When induction fails but BMC is clean:

1. inspect the induction trace for an unreachable starting state,
2. identify the missing state relationship,
3. add the smallest true helper invariant,
4. prove that helper by BMC and induction,
5. rerun the original property.

Do not raise induction depth indefinitely to hide a missing invariant.

## Reachability covers

Cover jobs demonstrate that important states and arcs are not made unreachable by assumptions or over-strengthened helpers. A cover pass is a witness, not a safety proof.

| ID | Required witness | Profile | Starting depth |
|---|---|---|---|
| `FP-COV-START-FETCH` | Reset, accepted START, fixed-head transaction accepted, FETCH reached. | baseline | 128 |
| `FP-COV-FETCH-READ` | Non-quit nonzero descriptor reaches source READ. | scenario descriptor | 192 |
| `FP-COV-READ-WRITE` | One real read completes and its corresponding write starts. | scenario descriptor | 320 |
| `FP-COV-CROSS-AB` | Source PSRAM0 read followed by destination PSRAM1 write. | scenario descriptor | 320 |
| `FP-COV-CROSS-BA` | Source PSRAM1 read followed by destination PSRAM0 write. | scenario descriptor | 320 |
| `FP-COV-YIELD-IDLE` | BUS_REQ from idle reaches grant, then release returns to idle parking. | baseline | 48 |
| `FP-COV-YIELD-ACTIVE` | BUS_REQ during real engine busy waits for completion, grants, then resumes. | scenario descriptor | 256 |
| `FP-COV-STALL-UPDATE` | BUS_REQ around UPDATE stalls and resumes without a second update. | scenario descriptor | 384 |
| `FP-COV-ZERO-NEXT` | Zero-length descriptor skips data and launches the next TCD fetch. | scenario descriptor | 256 |
| `FP-COV-QUIT-DONE` | Quit descriptor returns to DONE without a data transaction. | scenario descriptor | 192 |
| `FP-COV-SECOND-TCD` | A completed data or zero-length descriptor reaches a second descriptor fetch. | scenario descriptor | 384 |
| `FP-COV-RESET-ACTIVE` | A sampled reset from each major controller and engine phase converges to reset state. | reset recovery | 384 |

Expected minimum depths are large. A TCD fetch takes roughly 90 system clocks after launch, a one-byte read/write iteration can add roughly 130 clocks, and a second descriptor may need more than 300 clocks from START. Exact values depend on state-entry bookkeeping and the delayed SCK edge detector. Calibrate cover depths from the first witness generated by current RTL and keep at least 25 percent margin in required jobs.

Failure to reach a deep cover at depth N does not prove unreachability. Before declaring a cover blocked, increase depth, inspect assumptions, and cover intermediate arcs to locate the missing segment.

## Bounded deadlock checks

Global eventual DONE is not a valid V1 property. Legal executions can remain active forever because:

- `bus_req` may stay high indefinitely
- a descriptor can point to itself
- an unbounded chain may never present `QUIT`
- arbitrary PSRAM data may continually produce non-quit descriptors

M4 therefore uses bounded local progress checks:

| ID | Bounded obligation | Required premise | Starting bound |
|---|---|---|---|
| `FP-DLK-QSPI` | Every accepted legal engine transaction returns `busy` low before its watchdog expires. | Legal accepted command and length, no reset. | 96 clocks |
| `FP-DLK-YIELD` | If BUS_REQ remains high, an in-flight transaction completes and grant follows before the watchdog expires. | Real transaction already accepted, BUS_REQ held high, no reset. | 112 clocks from request |
| `FP-DLK-RESUME` | If a granted BUS_REQ falls and remains low, the controller leaves STALL and returns to its saved origin promptly. | Grant reached, BUS_REQ held low afterward, no reset. | 8 clocks |
| `FP-DLK-CHUNK` | With BUS_REQ low, one nonzero chunk progresses READ to WRITE to UPDATE without a local control stall. | One legal bounded descriptor scenario, no reset. | 192 clocks |
| `FP-DLK-FETCH` | With BUS_REQ low, one descriptor fetch leaves FETCH after the real engine completes. | A fetch was accepted, no reset. | 112 clocks |

Implement watchdogs as checker-local counters started by a precise event and cleared by the required response. Assert before counter overflow. Prove the watchdog counter bounds and start/clear exclusivity as helpers.

`FP-DLK-YIELD` does not assume that the engine finishes. It depends on the separately proven real-engine bound `FP-DLK-QSPI`. The integration job must retain both assertions so induction sees the complete chain.

The bounds above cover the current maximum transaction length of 11 bytes. If `QPI_TCD_BYTES`, clocking, wait cycles, or maximum byte length changes, derive new bounds from the RTL and rerun all dependent properties.

## Depth and performance guidance

Use staged jobs so easy failures appear quickly:

1. BMC 32 for reset, enum, and immediate mutual exclusion.
2. BMC 128 for one complete maximum-length engine transaction.
3. BMC 384 for integrated fetch, one-byte chunk, stall/update, and second-descriptor scenarios.
4. k-induction at 16, 32, 64, then 96 only where cross-FSM history requires it.
5. cover at property-specific depths, increasing in measured increments from intermediate witnesses.

Split property groups if one SMT problem becomes difficult:

- reset and top-level OE
- engine state and counters
- handshake and request stability
- arbitration and stall/resume
- descriptor control and update
- covers
- bounded deadlock

One giant job obscures the failing ID and can make induction harder. Each required property must still run in at least one integration configuration containing the real engine.

SMT state size is driven by the 84-bit working TCD, data buffer, counters, request shadows, and top-level synchronizers. Avoid adding a formal PSRAM array for Option B. Symbolic `sio_in` is sufficient for control safety and keeps the proof focused. Full memory semantics remain a simulation responsibility.

## Liveness and proof caveats

- BMC proves absence of a violation only up to its configured depth.
- k-induction proves an invariant only under the listed assumptions and helper invariant set.
- A cover witness proves existence, not that all legal executions reach the target.
- A bounded watchdog proves local response within its bound under its stated premise. It is not unbounded chain termination.
- No formal clock depth can establish nanosecond QSPI setup, hold, `tACLK`, pad delay, board flight time, or signal integrity.
- Digital formal cannot model analog metastability. The top harness can prove the implemented two-flop dataflow and one-cycle START generation, not an MTBF.
- Arbitrary `sio_in` is stronger than a friendly memory model for unconditional control safety, but it does not model stored-memory consistency.
- Conditional legal-descriptor proofs must be reported separately from baseline proofs.
- Sequential depth grows quickly because SCK is half-rate and each payload byte consumes two SCK beats. Shallow cover failures are expected and are not evidence of deadlock.

## M4 evidence and sign-off

Deferred (D33). The exit list below is the formal milestone contract when M4 is taken up; it is not required for V1 freeze.

For each `FP-*` row marked `pass`, retain:

- RTL revision and `DMA_BUF_DEPTH`
- `.sby` job and property ID
- Yosys, SBY, and solver versions
- mode, engine, configured depth, and elapsed time
- active assumption profile and audit
- log and trace path
- for cover, shortest observed witness depth
- for proof, base-case and induction completion

M4 exits only when:

- every required unconditional safety row passes in integration with the real engine
- conditional rows are clearly labeled and pass under their audited profile
- helper invariants pass rather than being silently assumed
- required covers produce witnesses
- bounded deadlock watchdogs pass at documented bounds
- no assumption duplicates the property it supports
- no abstract QSPI engine appears in an integration job

Any RTL change to `types.svh`, `qspi_engine.sv`, `sys_controller.sv`, or `top.v` returns affected rows to `todo`.

## Related

- Verification strategy (M4 is deferred (D33)): `01-strategy.md`
- Platform and native WSL tools: `02-platform.md`
- Integrated architecture and D21 handshake: `../03-architecture.md`
- TCD and datapath behavior: `../04-tcd-and-datapath.md`
- QPI protocol and timing: `../05-qspi-psram.md`
- Decisions D20 through D27: `../07-decision-log.md`
- Human host handshake: `../../human/architecture/blocks/host-interface.md`
- Human controller behavior: `../../human/architecture/blocks/descriptor-fsm.md`
- Human engine contract: `../../human/architecture/blocks/qspi-engine.md`
