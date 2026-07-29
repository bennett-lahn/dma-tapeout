# APS6404L QPI Model

## Purpose and boundary

The verification platform uses two independent Python cocotb models of the AP Memory APS6404L-3SQR. The models provide:

- sparse byte-addressable storage for each 8 MB device,
- pin-level QPI decoding and read-data driving,
- protocol policing for the V1 ASIC command subset,
- transaction logs for later scoreboarding, and
- configurable timing behavior through the delay layer in `04-timing-in-sim.md`.

The model represents only the ASIC-facing V1 data path. It starts in QPI mode because MCU-owned reset and Enter Quad Mode happen before `START` by architecture decision D17. It does not implement SPI, mode-entry commands, flash behavior, DRAM refresh internals, analog loading, or signal integrity.

Protocol truth and all device AC values come from `../05-qspi-psram.md`, backed by the local APS6404L Rev 2.3 PDF and converted text under `../../datasheets/`. The model is an executable verification oracle, not a replacement for that datasheet and not timing signoff.

## Attachment at each DUT level

### L0 - engine

Attach one selected model to `qspi_engine` SCK, SIO, and the selected RAM CE#. Tie or monitor the unselected CE# high. The L0 wrapper also exposes DUT SIO output enable so the model can distinguish driven values from high impedance.

### L1 and L2 - integrated top

Attach two model instances to the shared `uio` SCK and SIO nets:

| Instance | CE# pin | Address space |
|---|---|---|
| PSRAM0 | `uio[6]` | `0x000000` through `0x7FFFFF` |
| PSRAM1 | `uio[7]` | `0x000000` through `0x7FFFFF` |

Both instances observe the same SCK and SIO values, but only the instance with CE# low may parse or drive a transaction. A shared-bus monitor, outside either individual memory instance, detects both RAM CE# signals low together, flash CS assertion by the ASIC, and SIO contention.

Flash CS must remain high while the ASIC owns or parks the bus under `~BUS_GNT`. This is not a ban on MCU flash access: architecture permits the MCU to assert flash CS while `BUS_GNT` is high and all ASIC `uio_oe` bits are clear.

## Pin-level QPI grammar

The model samples command, address, and write-data nibbles on model-observed rising SCK edges. It launches read-data nibbles relative to model-observed falling SCK edges as specified below.

Within every byte:

- the upper nibble is transferred first,
- the lower nibble is transferred second, and
- `SIO[3]` is the nibble MSB while `SIO[0]` is the nibble LSB.

A CE# falling edge begins a transaction. A CE# rising edge terminates it. The supported transactions are:

### Fast Read Quad `0xEB`

1. two QPI command nibbles,
2. six address nibbles for one 24-bit address,
3. exactly six dummy SCK cycles with the ASIC SIO output enable clear, and
4. zero or more complete data bytes driven by the selected model.

After the sixth dummy cycle, each falling SCK edge schedules the next read nibble. The nibble becomes visible at the model output after the configured `tACLK`, then reaches the DUT through the return delay in `04-timing-in-sim.md`. This falling-edge launch is required by APS6404L Rev 2.3 sections 11.1 and 14.6. The architecture samples that value on the following rising SCK edge.

### QPI Write `0x02`

1. two QPI command nibbles,
2. six address nibbles, and
3. zero or more complete data bytes sampled on rising SCK edges with no dummy cycles.

Each completed byte is committed to sparse memory in wire order. A transaction ending after only one nibble of a byte is malformed and must not silently commit a partial byte.

The model rejects every other ASIC opcode. In particular, it does not accept `0x38` as an alias even though the device supports it in QPI mode, because the frozen V1 ASIC allowlist is exactly `0xEB` and `0x02`.

## Parser state and termination

Each instance keeps transaction-local state:

- active or idle,
- command nibble count and decoded opcode,
- address nibble count and decoded 24-bit address,
- dummy-cycle count,
- data nibble count,
- current byte assembly,
- next byte address, and
- a monotonically increasing transaction generation.

The generation tags delayed read-output tasks. Raising CE#, reset, or starting a later transaction invalidates stale tasks so a delayed assignment from an old transaction cannot drive a new one.

On CE# rising, the parser:

1. checks that command and address phases completed,
2. checks the exact dummy count for a read,
3. rejects an odd data-nibble count,
4. records the final byte count and end timestamp,
5. schedules model SIO release according to `tHZ`, and
6. returns to idle after invalidating any response not legal after termination.

`tHZ` is a maximum release delay, not permission to source another data beat after CE# rises. The model holds only the last driven value during this release interval.

## Sparse memory semantics

Each model owns a separate sparse mapping keyed by 23-bit byte address. Tests may preload and inspect memory through explicit backdoor methods that do not emit QPI traffic.

- Valid addresses are `0x000000` through `0x7FFFFF`.
- The 24-bit wire address must have `A[23] == 0`.
- Device selection comes from the selected CE#, not from address bit 23.
- Unwritten-byte behavior is a configured deterministic fill value or a seeded initialization policy. It is never host-language dictionary-order dependent.
- Reads increment the address after each complete byte.
- Writes increment the address after each complete byte.
- A transaction that would move beyond `0x7FFFFF` is reported as an address-range failure rather than silently wrapping.

System `rst_n` aborts the active parser and releases model drive, but does not erase preloaded PSRAM contents. `rst_n` is an ASIC reset, not the APS6404L software-reset command.

The model records page crossings and continuous CE# low time. V1 transactions are expected to remain within the one-page-cross rule and `tCEM`; the model reports violations instead of trying to emulate data corruption.

## Protocol policing

Protocol failures are immediate test failures with instance, simulation timestamp, parser phase, observed value, and transaction history. Required checks are:

| Condition | Required model response |
|---|---|
| Unsupported opcode | Fail and identify the decoded opcode |
| Command or address truncated by CE# | Fail with the completed nibble count |
| `0xEB` dummy count other than six | Fail at early termination or first mistimed data beat |
| Write data during an incomplete phase | Fail without committing a partial byte |
| Odd data-nibble count at termination | Fail |
| `A[23] != 0` | Fail before any memory access |
| Address outside `0x000000..0x7FFFFF` | Fail before access or wrap |
| Both RAM CE# signals low | Shared monitor fails and names both instances |
| ASIC flash CS low while `~BUS_GNT` | Shared monitor fails |
| ASIC drives SIO during read dummy or read-data phase | Fail as drive overlap or contention |
| Model drives while its CE# is not active, except bounded `tHZ` release | Fail |
| CE# low longer than configured `tCEM` | Fail `Q-CEM` |
| CE# high gap shorter than `tCPH` | Fail `Q-CPH` |

The model does not declare SCK high while CE# is high universally illegal. The architecture itself parks SCK low between transactions and sequences CE# only while SCK is low, so architecture-owned checks may require that waveform for this DUT. That is distinct from claiming a general APS6404L prohibition in every operating context.

## Timing handoff

The functional parser consumes delayed, model-plane copies of DUT pins. It must not read raw DUT handles and then add a blocking sleep inside the parser loop. Each source transition is copied by an independent transport-delay coroutine so later transitions remain observable while an earlier copy is pending.

The timing layer owns:

- DUT-to-device transport delay,
- falling-edge-to-read-output `tACLK`,
- model-to-DUT return flight delay,
- delayed high-impedance release through `tHZ`,
- setup and hold timestamp checks, and
- timing-profile and sweep configuration.

`04-timing-in-sim.md` defines the exact equations, parameters, and `Q-*` catalog. The parser reacts only to the resulting model-plane events.

## Transaction log

Every completed transaction produces an immutable record containing at least:

- device instance,
- opcode,
- start address,
- ordered read or write bytes,
- byte count,
- CE# fall and rise timestamps,
- observed command, address, dummy, and data nibble counts,
- timing profile and relevant delay values, and
- pass or failure classification.

The log contains pin-decoded facts, not request fields read from DUT internals. Later scoreboards compare this ordered log with the reference chain interpretation.

## Model acceptance criteria

M1 model acceptance requires:

- both instances retain independent memory images,
- directed `0xEB` and `0x02` transactions decode with the required nibble order,
- six and only six read dummy cycles are accepted,
- unsupported opcodes and malformed phase lengths fail,
- address bit 23, range, CE# overlap, flash-CS, and drive-overlap checks fire on injected violations,
- transaction logs reconstruct exact addresses and bytes, and
- Icarus and Verilator agree on the directed protocol cases.

M3 adds the delay behavior and timing checks in `04-timing-in-sim.md`. Passing either milestone does not close a physical `T-*` item.

## Repository sources

- V1 architecture, dual-device routing, bus ownership, and QPI phases: `../03-architecture.md`
- Opcode allowlist, bit order, address range, dummy count, and AC timing summary: `../05-qspi-psram.md`
- Stable levels, milestones, and IDs: `00-index.md`, `01-strategy.md`
- Platform placement: `02-platform.md`
- RTL pin and phase behavior: `../../../src/rtl/qspi_engine.sv`, `../../../src/rtl/top.v`, `../../../src/rtl/types.svh`
- Datasheet conversion policy: `../../datasheets/README.md`
- Converted APS6404L Rev 2.3 sections 11.1, 11.2, 13, and 14.6: `../../datasheets/md/APS6404L_3SQR.md`
- Manufacturer PDF, authoritative for figures and tables: `../../datasheets/pdfs/APS6404L_3SQR.pdf`
