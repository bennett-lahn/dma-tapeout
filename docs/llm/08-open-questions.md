# Open Questions

Unresolved items that block a frozen architecture. When one is decided, move the decision into the appropriate LLM doc and leave a one-line pointer here.

## Q1 - Upper address bits for 24-bit PSRAM phases

**Decided (V1 / D10 / D18 / D19):** full **24-bit** internal pointers. Device select is **`ptr[23]`** (`0`=PSRAM 0, `1`=PSRAM 1). QSPI address phase uses `ptr[22:0]` (`A[22:0]`). Address `0x000000` is a **valid** location (fixed head on PSRAM 0). End-of-chain is `CTRL_FLAGS.QUIT`. Working TCD metadata **88 DFFs** (11-byte TCD; no head register). See `03-architecture.md` / `04-tcd-and-datapath.md`.

## Q2 - Who initializes PSRAM (reset + enter quad)?

**Decided (D17):** **MCU-owned** via pass-through. MCU resets / Enter Quad / Exit Quad on each die; ASIC emits none of those opcodes and expects both dies already in QPI before START.

## Q3 - Exact host pin protocol

**Decided (behavior / D14; pins / D18; quit / D19):** IDLE waits for START; START ignored until back in IDLE; `QUIT=1` TCD → IDLE; DONE = idle; pass-through iff DONE; abort finishes current QPI txn then IDLE.

**Frozen pins:** `ui_in[0] = START`, `ui_in[1] = ABORT`, `uo_out[0] = DONE`; QSPI on `uio` per system I/O map. **No head-pointer pins** (fixed head at `0x000000` / PSRAM 0).

**Still open:** status / error / debug observe on `uo_out[7:1]`; optional use of `ui_in[7:2]`.

Per TinyDMA-2C prior art, command/payload strobes are one known reference pattern, not a requirement for this project.

## Q4 - Bus release / re-entrancy rules after DONE

**Decided (D14):** pass-through restores automatically whenever IDLE/`DONE` is asserted. No host ACK required for OE release. Illegal: host drives `uio` while ASIC is not idle (not DONE).

## Q5 - Null / zero-length / chain-end semantics

**Decided (D14 / D18 / D19):**

- **End of chain:** fetched TCD with **`CTRL_FLAGS.QUIT=1`** → IDLE (DONE, pass-through); no copy for that TCD
- `TRANSFER_LEN == 0` → **no-op** descriptor; immediately follow `NEXT_TCD` with no data moved (unless the TCD is already a quit TCD)
- Fixed head: START always fetches `0x000000` on PSRAM 0; place a `QUIT` TCD there for an empty run
- `NEXT_TCD` with address bits `0x000000` is a **valid** next address (die from `NEXT_TCD[23]`), not end-of-chain

**Still open:**

- Can a descriptor point to itself? Without cond-stop this only spins until **abort**/reset - allow with abort, or reject?

## Q6 - ALU immediate storage

**Deferred (post-V1 / D12).** See `10-post-v1-features.md` section 1. Prefer TCD-resident `IMM` byte when ALU returns; extend reserved `CTRL_FLAGS` bits for op select.

## Q7 - Ring buffer encoding

**Deferred (post-V1 / D12).** See `10-post-v1-features.md` section 3. Prefer unused `CTRL_FLAGS` bits when ring returns.

## Q8 - SPI vs QPI for V1 data path

**Decided (D15 / D17):** QPI for all ASIC DMA data read/write. ASIC emits **no SPI** and **no** Enter/Exit Quad. Sole QPI read opcode is **`0xEB`** (write `0x02`). MCU owns enter/exit QPI via pass-through.

## Q9 - Clock frequency target

**Decided (D16):** demoboard / design **`clk` 66 MHz**; engine **SCK = clk/2**; sample read data on the **rising** edge of SCK. Phase 3 must re-validate `tACLK` / board / TT timing against this target before shuttle freeze.

## Q10 - Sensor data ingress path

**Lean (D12):** V1 is **memove only** - data already in PSRAM (MCU wrote it during pass-through). No live ADC/stream ingress requirement.

Optional later: streamed host-pin ingress and/or telemetry features in `10-post-v1-features.md`.

## Q11 - Feature freeze for first shuttle

**Decided (D12 / D14 / D15 / D16 / D17 / D18 / D19 / D20 / D21):** V1 = pass-through + QPI (`0xEB`/`0x02`) + MCU enter/exit QPI + dual CS + 11-byte TCD + `ptr[23]` device select + `QUIT` end-of-chain + fixed head at 0/PSRAM0 + cross-device + chaining + START/ABORT/DONE pins + 66 MHz `clk` / SCK=clk/2 / rising-edge RX + D21 (`~busy` / `wdata_next` / length-driven write) + **1-byte** data buffer with depth-agnostic correctness. **Out of V1:** ALU, conditional stop, ring, ASIC flash (post-V1 ladder in `10-post-v1-features.md`).

Still open inside V1: multi-outstanding (lean: no), `uo_out[7:1]` status packing (Q3 remainder).

## Q12 - Error model

Which conditions sticky-error vs ignore vs halt?

- Illegal host sequence while running (START while busy is **ignore** per D14; other illegal sequences TBD)
- Bad descriptor address
- CE# policy violation (should be impossible if engine correct)
- External memory timeout (if detectable at all)
- Illegal both RAM CS asserted during a data TCD (should be impossible if engine correct; quit TCD never asserts CS for a copy)

## Q13 - PSRAM device select encoding (dual-die)

**Decided (D19):** device select is **`ptr[23]`** on `SRC_PTR` / `DEST_PTR` / `NEXT_TCD` (`0`=PSRAM0, `1`=PSRAM1). QSPI drives `A[22:0]` from `ptr[22:0]`. End-of-chain is **`CTRL_FLAGS.QUIT`**, not a device encoding. Cross-device byte copy: read-then-write with only one CE# low at a time; no multi-outstanding for V1. Fixed head on PSRAM 0 at address 0 (D18).
