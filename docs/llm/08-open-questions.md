# Open Questions

Unresolved items that block a frozen architecture. When one is decided, move the decision into the appropriate LLM doc and leave a one-line pointer here.

## Q1 - Upper address bits for 24-bit PSRAM phases

**Decided (V1 / D10 / D13):** full **24-bit** internal pointers; address `0x000000` reserved for null. Device select is in `CTRL_FLAGS`, not `ptr[23]`. QSPI address phase uses `ptr[22:0]` (`A[22:0]`). Working TCD metadata **88 DFFs** (11-byte TCD). See `03-architecture.md` / `04-tcd-and-datapath.md`.

## Q2 - Who initializes PSRAM (reset + enter quad)?

Options:

1. MCU always initializes via pass-through before START.
2. ASIC runs a fixed boot FSM on reset, then enables pass-through.
3. Hybrid: ASIC ensures safe reset defaults; MCU enters quad.

Related: does init happen **before** pass-through to the MCU is enabled? (lean: probably yes if ASIC-owned).

Datasheet requires Reset immediately after Reset Enable, then `tRST` >= 50 ns before the next command.

**Lean:** MCU-owned init for V1 (less ASIC state), ASIC documents required preconditions. With dual PSRAM (D11), MCU (or ASIC) must init **each die** that DMA will touch before START. Per D15, init SPI opcodes (`0x66`/`0x99`/`0x35`) are the only SPI the ASIC may emit if it owns init; DMA data path is QPI-only.

## Q3 - Exact host pin protocol

**Decided (behavior / D14):** IDLE waits for START; START ignored until back in IDLE; null `NEXT_TCD` → IDLE; DONE = idle; pass-through iff DONE; abort finishes current QPI txn then IDLE.

**Still open (pin encoding):**

- ABORT bit on `ui_in[7:1]`
- head pointer programming (24-bit; optional head-device bit - lean PSRAM 0)
- status / error / debug observe on `uo_out[7:1]`
- optional arm bits

Frozen indices: `ui_in[0] = START`, `uo_out[0] = DONE`; QSPI on `uio` per system I/O map.

Per TinyDMA-2C prior art, command/payload strobes are one known reference pattern, not a requirement for this project.

## Q4 - Bus release / re-entrancy rules after DONE

**Decided (D14):** pass-through restores automatically whenever IDLE/`DONE` is asserted. No host ACK required for OE release. Illegal: host drives `uio` while ASIC is not idle (not DONE).

## Q5 - Null / zero-length semantics

**Decided (D13 / D14):**

- `NEXT_TCD == 0x000000` → end of chain → IDLE (DONE, pass-through)
- `TRANSFER_LEN == 0` → **no-op** descriptor; immediately follow `NEXT_TCD` / `NEXT_DEV` with no data moved
- Address `0x000000` reserved (no TCD or buffer there)

**Still open:**

- Can a descriptor point to itself? Without cond-stop this only spins until **abort**/reset - allow with abort, or reject?
- Head `0x000000` at START: immediate IDLE/DONE (consistent with null) vs sticky error?

## Q6 - ALU immediate storage

**Deferred (post-V1 / D12).** See `10-post-v1-features.md` section 1. Prefer TCD-resident `IMM` byte when ALU returns; extend reserved `CTRL_FLAGS` bits for op select.

## Q7 - Ring buffer encoding

**Deferred (post-V1 / D12).** See `10-post-v1-features.md` section 3. Prefer unused `CTRL_FLAGS` bits when ring returns.

## Q8 - SPI vs QPI for V1 data path

**Decided (D15):** QPI default for all DMA data read/write. SPI never used for data; SPI only for documented config / Enter Quad (and SPI-form reset if ASIC-owned init).

**Still open:** which QPI read opcode - `0x0B` (4 wait, 66 MHz max) vs `0xEB` (6 wait, higher max). Also whether Exit Quad `0xF5` is in the ASIC or only via MCU pass-through.

## Q9 - Clock frequency target

Trade throughput vs `tACLK` sample margin vs CE# budgeting.

Need a chosen demoboard clock and RX sample edge policy.

## Q10 - Sensor data ingress path

**Lean (D12):** V1 is **memove only** - data already in PSRAM (MCU wrote it during pass-through). No live ADC/stream ingress requirement.

Optional later: streamed host-pin ingress and/or telemetry features in `10-post-v1-features.md`.

## Q11 - Feature freeze for first shuttle

**Decided (D12 / D13 / D14 / D15):** V1 = pass-through + QPI data path + dual CS + 11-byte TCD + cross-device + chaining + abort + idle/DONE protocol. **Out of V1:** ALU, conditional stop, ring, ASIC flash (post-V1 ladder in `10-post-v1-features.md`).

Still open inside V1: clock (Q9), QPI read opcode (Q8 remainder), multi-outstanding (lean: no), head/ABORT pin packing (Q3 remainder).

## Q12 - Error model

Which conditions sticky-error vs ignore vs halt?

- Illegal host sequence while running (START while busy is **ignore** per D14; other illegal sequences TBD)
- Bad descriptor address
- CE# policy violation (should be impossible if engine correct)
- External memory timeout (if detectable at all)
- Illegal device select / both RAM CS asserted (should be impossible if engine correct)

## Q13 - PSRAM device select encoding (dual-die)

**Decided (D13):** `CTRL_FLAGS` holds `SRC_DEV`, `DEST_DEV`, `NEXT_DEV` (PSRAM 0 vs 1). Full address space preserved (`ptr[23]` not used for device). Cross-device byte copy: read-then-write with only one CE# low at a time (required on shared SIO); no multi-outstanding for V1.

**Still open:** head-pointer device at START (lean: PSRAM 0). Init ownership per die remains Q2.
