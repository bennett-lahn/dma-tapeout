# Always-On Runtime Checkers

## Purpose

`CHK-*` identifies invariant monitors that run automatically in every applicable L0 and L1 simulation. A test does not opt into them. Checkers detect the first local protocol or control violation, while the reference scoreboard in `05-reference-model.md` checks end-to-end descriptor meaning.

Icarus does not provide the concurrent SVA flow required by this plan. Runtime `CHK-*` checks are cocotb monitors. Equivalent or stronger SVA properties belong in the formal bind files described by `07-formal.md`; an SVA pass does not disable the runtime checker.

The catalog IDs below are stable. Do not reuse an ID for a different condition. A materially new condition receives a new ID.

## Visibility classes

Each checker has one of these visibility classes:

- **Top-observable** - uses only `clk`, `rst_n`, TT top ports, resolved QSPI pins, and the testbench's explicit host-drive state. It remains meaningful at L2.
- **L0-port** - uses only public `qspi_engine` ports. It is black-box with respect to that module, but those signals are not chip-level observations at L1.
- **RTL-hierarchy-only** - reads named internal RTL signals below `tt_um_lahnb_sgdma`. It is diagnostic RTL evidence and is not an L2 sign-off criterion.

Do not describe an RTL-hierarchy-only result as externally observable. At L1 and L2, the durable transaction oracle is the pin decoder plus final modeled memory. Hierarchy checkers may fail earlier and explain why.

## Runtime and reset rules

All applicable checkers are constructed before reset release and remain active until test shutdown. They use simulation time and clock/pin edges, not host wall time.

`rst_n` is active low and sequential reset is synchronous:

1. A low transition of `rst_n` does not by itself imply that controller or engine state has reset.
2. Sequential reset expectations begin only after a rising `clk` edge at which `rst_n=0`.
3. Check state after sequential assignments settle in the same time step, using the cocotb read-only phase.
4. A protocol transaction active at that sampled reset edge is closed as aborted, not checked as a normally completed transaction.
5. Ordinary transaction and hierarchy invariants clear their per-transaction state at that sampled reset edge. They re-arm on the first rising `clk` edge sampled with `rst_n=1`.

There is one deliberate combinational exception: top-level `uio_oe` is gated directly by `rst_n`. `CHK-RST-OE` therefore requires immediate shared-output release while `rst_n=0`, independently of a clock edge. This does not make the internal reset asynchronous.

Every sampled logic value is checked for resolution before integer conversion. `X`, `Z`, or a failed conversion on a required control/data value is a checker failure, not zero.

## Sampling conventions

- Clocked interface checks sample after the rising `clk` edge in the read-only phase.
- Pin checks react to resolved CE#, SCK, SIO, and OE transitions and timestamp the triggering edge.
- A QPI transaction interval begins when either driven RAM CE# falls and ends when that same CE# rises.
- At L1, a pin is considered ASIC-driven only when its `uio_oe` bit is 1. The checker examines both `uio_out` and the resolved net so a released or contended pin is not mistaken for valid ASIC behavior.
- `txn_valid` acceptance means a sampled cycle with `txn_valid=1` and `busy=0`.
- A checker that reports multiple errors may retain later context, but the first violation time and ID are authoritative.

## Stable `CHK-*` catalog

### Pin and bus ownership checks

| ID | Condition | L0 | L1 | Visibility |
|---|---|---:|---:|---|
| `CHK-PIN-CS-MUTEX` | RAM A CE# and RAM B CE# are never both low. At L1, also fail if OE state makes both appear selected or the resolved bus is ambiguous. | required | required | L0-port at L0, top-observable at L1 |
| `CHK-PIN-FLASH-HIGH` | While the ASIC drives flash CS, its value is 1. The ASIC never selects flash. | na | required | top-observable |
| `CHK-PIN-ADDR23-ZERO` | Every decoded ASIC QPI address has address bit 23 equal to 0. Device selection is not encoded in this bit. | required | required | L0-port/pin at L0, top-observable at L1 |
| `CHK-PIN-KNOWN` | Driven CE#, SCK, and SIO values, and sampled read SIO values, contain no unknown or high-impedance bit where the protocol requires a value. | required | required | L0-port at L0, top-observable at L1 |
| `CHK-ARB-GNT-OE` | Whenever `BUS_GNT=1`, all eight ASIC `uio_oe` bits are 0. | na | required | top-observable |
| `CHK-ARB-GNT-QUIET` | No ASIC QPI transaction begins or remains active while `BUS_GNT=1`; a grant rise occurs only with both RAM CE# high and SCK low on the resolved bus. | na | required | top-observable |
| `CHK-ARB-PARK` | While `rst_n=1`, `BUS_GNT=0`, and no QPI transaction is active, all eight ASIC output enables are 1, flash CS and both RAM CS outputs are high, and SCK output is low. SIO output values are don't-care but their OEs are 1. | na | required | top-observable |
| `CHK-RST-OE` | At all observed times with `rst_n=0`, all eight `uio_oe` bits are 0. | na | required | top-observable |
| `CHK-RST-STATUS` | After each rising `clk` edge sampled with `rst_n=0`, `DONE=1`, `BUS_GNT=0`, and no driven RAM selection exists. Do not require these values before the first sampled reset edge. | na | required | top-observable |

`CHK-ARB-PARK` excludes the complete CE#-low transaction interval. During a read transaction, SIO must float for dummy and read phases, so requiring all OEs high there would be wrong. CS and SCK remain enabled at L1 throughout DMA ownership.

`CHK-ARB-GNT-QUIET` is the external atomicity check. It proves that grant does not overlap an externally active transaction, but it cannot prove the internal `qspi_busy` value. That stronger RTL-only condition has its own ID below.

### Engine request and streaming handshake checks

| ID | Condition | L0 | L1 | Visibility |
|---|---|---:|---:|---|
| `CHK-HS-TXN-START` | `txn_valid` is a one-`clk` pulse, is asserted only while `busy=0`, and accepts a nonzero `byte_len`. A second acceptance cannot occur before the prior transaction ends. | required | required | L0-port at L0, RTL-hierarchy-only at L1 |
| `CHK-HS-REQ-STABLE` | From acceptance through the last sampled cycle with `busy=1`, `{cmd, addr, device_sel, byte_len}` exactly equals the accepted request. | required | required | L0-port at L0, RTL-hierarchy-only at L1 |
| `CHK-HS-RDATA-COUNT` | A completed `0xEB` read emits exactly `2 * byte_len` `rdata_valid` pulses. A write emits zero. Each pulse occurs only during the accepted transaction. | required | required | L0-port at L0, RTL-hierarchy-only at L1 |
| `CHK-HS-WDATA-COUNT` | A completed `0x02` write emits exactly `2 * byte_len - 1` `wdata_next` pulses. A read emits zero. No pulse occurs after the final nibble or outside the accepted transaction. | required | required | L0-port at L0, RTL-hierarchy-only at L1 |
| `CHK-HS-PULSE-WIDTH` | `txn_valid`, `rdata_valid`, and `wdata_next` are never high on two consecutive rising `clk` samples. | required | required | L0-port at L0, RTL-hierarchy-only at L1 |
| `CHK-HS-WDATA-KNOWN` | On a write acceptance and after every `wdata_next`, `wdata` is a resolved 4-bit value. The sequence presented is retained for comparison with pin-decoded write data. | required | required | L0-port at L0, RTL-hierarchy-only at L1 |
| `CHK-HS-OPCODE` | Every accepted command is exactly `0xEB` or `0x02`; `0xEB` has six QPI wait cycles and `0x02` has none. | required | required | L0-port plus pins at L0, RTL-hierarchy and pins at L1 |

For count checks, capture `cmd` and `byte_len` on acceptance, initialize both counts to zero, and count sampled pulses until `busy` returns low. Compare only when the transaction completes normally. If reset is sampled while busy, mark the transaction aborted, retain partial counts for diagnostics, and do not demand the completed-transaction total.

`CHK-HS-WDATA-COUNT` counts requests for later nibbles, not transmitted nibbles. The first nibble accompanies `txn_valid`, which is why the required count is one less than `2 * byte_len`.

The six-wait-cycle part of `CHK-HS-OPCODE` is measured by the pin protocol decoder. Internal state names are not used to establish it.

### Integrated controller and arbitration checks

| ID | Condition | L0 | L1 | Visibility |
|---|---|---:|---:|---|
| `CHK-CTRL-REQ-GATE` | Every `qspi_txn_valid` pulse implies `qspi_busy=0` and synchronized `bus_req=0` on that sampled cycle. | na | required | RTL-hierarchy-only |
| `CHK-CTRL-REQ-SHAPE` | An accepted controller request is one of: fetch read with length 11, payload read with length `1..DMA_BUF_DEPTH`, or payload write with length `1..DMA_BUF_DEPTH`. | na | required | RTL-hierarchy-only |
| `CHK-CTRL-FETCH-HEAD` | The first transaction after each accepted START is `0xEB`, device 0, address `0x000000`, length 11. | na | required | top-observable at pins |
| `CHK-CTRL-DATA-PAIR` | Between descriptor fetches, every payload read is followed by exactly one same-length payload write before another payload read or fetch. | na | required | top-observable at pins |
| `CHK-ARB-GNT-NOT-BUSY` | On every sampled low-to-high transition of `bus_gnt`, internal `qspi_busy=0`. It remains 0 for the complete grant interval. | na | required | RTL-hierarchy-only |
| `CHK-CTRL-STATE-VALID` | `curr_state`, `next_state`, and `stalled_state` are resolved values in `sys_control_state_t`; `stalled_state` is not overwritten while stalled. | na | required | RTL-hierarchy-only |
| `CHK-CTRL-DATA-CNT` | Controller `data_cnt` never exceeds 22 during descriptor fetch or `2 * DMA_BUF_DEPTH` during payload movement, never underflows, and satisfies every dynamic-index precondition. | na | required | RTL-hierarchy-only |
| `CHK-RST-INTERNAL` | After a rising `clk` edge sampled with `rst_n=0`, controller state is `SYS_CTRL_IDLE`, engine state is `QSPI_IDLE`, `qspi_busy=0`, request/stream pulses are 0, data counters are 0, RAM CE# registers are high, SCK is low, and first-fetch state is reset to device 0/address 0. | required subset | required | L0-port plus hierarchy at L0, RTL-hierarchy-only at L1 |

`CHK-CTRL-DATA-CNT` applies these precise dynamic-index preconditions:

- a descriptor `qspi_rdata_valid` capture requires `1 <= data_cnt <= 22`,
- a payload `qspi_rdata_valid` capture requires `1 <= data_cnt <= 2 * accepted_byte_len`,
- while driving write data, `1 <= data_cnt <= 2 * accepted_byte_len`, and
- `qspi_wdata_next=1` while writing requires `data_cnt >= 2`.

For `CHK-CTRL-FETCH-HEAD`, the top-observable high-to-low transition of `DONE` identifies acceptance of the host driver's valid START protocol. The first following ASIC QPI transaction is checked only from top-level pins. The checker does not depend on the internal synchronized pulse or a synthesized hierarchy name.

`CHK-CTRL-DATA-PAIR` is intentionally weaker than the reference scoreboard. It checks local read/write alternation and equal length without predicting TCD addresses or data. The scoreboard owns complete chain meaning.

## Applicability and required disposition

Every run creates a catalog result for every ID:

- `pass` - applicable, monitor ran, and no violation occurred,
- `fail` - applicable and violated,
- `na` - structurally unavailable at that DUT level,
- `blocked` - applicable but could not run because a named required monitor signal or wrapper connection was missing.

Missing hierarchy for an RTL-hierarchy-only L1 checker is `blocked`, not a silent skip. At L2, those same rows are `na` because source hierarchy is intentionally not a sign-off interface. Top-observable rows remain required at L2 when `09-gate-level-and-x.md` assigns the test.

Minimum level sets:

- **L0:** all applicable `CHK-PIN-*`, `CHK-HS-*`, and the engine subset of `CHK-RST-INTERNAL`.
- **L1:** every catalog row except rows marked L0-only or `na`.

A test may intentionally provoke an illegal condition. The checker still runs; the test harness must declare the expected failing ID and exact allowed occurrence count before stimulus. Unexpected IDs, too few occurrences, or extra occurrences fail the test. Do not disable the monitor around negative stimulus.

## Failure diagnostics

The first line is stable:

```text
CHECKER FAIL id=CHK-HS-RDATA-COUNT level=L1 time=1845ns cycle=122
```

Every report also includes:

- simulator/version, seed, `DMA_BUF_DEPTH`, timing profile, and test identity,
- current and previous sampled values,
- transaction index and accepted request when relevant,
- expected condition and observed condition,
- reset generation and whether a reset edge was sampled,
- a bounded recent pin or clock trace,
- visibility class,
- waveform and machine-readable checker-log paths, and
- the reproduction command.

Count mismatch example:

```text
accepted op=EB dev=0 addr=0x123456 len=3
expected_rdata_valid=6 observed_rdata_valid=5
transaction_end=normal last_pulse_cycle=118
```

Arbitration mismatch example:

```text
BUS_GNT rose with qspi_busy=1
ram_a_cs_n=1 ram_b_cs_n=1 sck=0 uio_oe=00
visibility=RTL-hierarchy-only
```

Do not print enum values only as simulator-specific integers. Include the symbolic state when conversion is possible and the raw logic value in all cases.

## Implementation separation

Planned monitor ownership under `test/monitors/`:

- `qspi.py` - CE#, SCK, opcode/address/data decoding and `CHK-PIN-*`
- `arbitration.py` - grant, OE, park, reset release, and bus atomicity
- `handshake.py` - engine request stability, pulse counts, pulse widths, and controller bounds
- `timing.py` - `Q-*` timing-window checks, not the `CHK-*` catalog

The checker manager starts monitors before reset, collects one result per ID, and raises one aggregate test failure after preserving artifacts. A fatal contention or unknown-value violation may stop stimulus immediately after artifacts are captured.

Do not derive observed pin facts from internal request fields. For example, `CHK-PIN-ADDR23-ZERO` decodes the six address nibbles from SIO; `CHK-HS-REQ-STABLE` separately checks the internal request bus. Agreement between independent observations is useful evidence.

## M2 acceptance

- Every applicable L0/L1 test reports a disposition for every catalog ID.
- Required rows are never silently disabled by test selection.
- Read and write count checkers pass for lengths `1`, `DMA_BUF_DEPTH`, and every distinct directed boundary length.
- Grant, park, and reset checks pass for idle and every meaningful transaction phase.
- Reset tests distinguish the low transition from the first rising `clk` edge sampled low.
- At least one controlled negative test per monitor group proves the monitor can fail and emits the required ID and context.
- Icarus and Verilator agree on all checks assigned to both simulators.

## Related

- Reference model and dual-axis scoreboard: `05-reference-model.md`
- Strategy and level boundaries: `01-strategy.md`
- Platform and monitor layout: `02-platform.md`
- Integrated architecture: `../03-architecture.md`
- TCD semantics: `../04-tcd-and-datapath.md`
