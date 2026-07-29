# Open Questions

Unresolved items that block a frozen architecture. When one is decided, move the decision into the appropriate LLM doc and leave a one-line pointer here.

## Q1 - Upper address bits for 24-bit PSRAM phases

**Decided (V1 / D10 / D18 / D19 / D24):** full **24-bit** internal pointers (byte addresses). Device select is **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** (D24). QSPI address phase uses `ptr[22:0]` (`A[22:0]`); `ptr[23]` unused. Address `0x000000` is a **valid** location (fixed head on PSRAM 0). End-of-chain is `CTRL_FLAGS.QUIT`. Working TCD metadata **88 DFFs** (11-byte TCD; no head register). See `03-architecture.md` / `04-tcd-and-datapath.md`.

## Q2 - Who initializes PSRAM (reset + enter quad)?

**Decided (D17):** **MCU-owned** via pass-through. MCU resets / Enter Quad / Exit Quad on each device; ASIC emits none of those opcodes and expects both devices already in QPI before START.

## Q3 - Exact host pin protocol

**Decided (behavior / D14; pins / D18; quit / D19/D23; pass-through grant / D22; no abort / D23):** IDLE waits for START; START ignored until back in IDLE; `QUIT=1` TCD → IDLE; next START always fetches fixed head at `0x000000` / PSRAM 0; DONE = idle; **no ABORT pin** - use **`rst_n`** to kill a runaway DMA. MCU may drive `uio` only while **`BUS_GNT`** (D22); not merely when DONE.

**Frozen pins:** `ui_in[0] = START`, `ui_in[2] = BUS_REQ`, `uo_out[0] = DONE`, `uo_out[1] = BUS_GNT`; `ui_in[1]` reserved (ABORT removed); QSPI on `uio` per system I/O map. **No head-pointer pins** (fixed head at `0x000000` / PSRAM 0).

**Still open:** status / error / debug observe on `uo_out[7:2]`; optional use of `ui_in[7:3]` / `ui_in[1]`.

Per TinyDMA-2C prior art, command/payload strobes are one known reference pattern, not a requirement for this project.

## Q4 - Bus release / re-entrancy rules after DONE

**Decided (D14 / D22 / D26):** ASIC **releases** `uio_oe` when yielding for `BUS_REQ` / asserting `BUS_GNT` (after the current QPI txn). While `~BUS_GNT`, ASIC is the **bus keeper** (park CS high / SCK low; SIO floats only for dummy/read). Board **10 kΩ** CS pull-ups cover reset / pre-enable. No host ACK for OE release. **Illegal: MCU drives `uio` while `BUS_GNT` is low.** Idle/`DONE` alone is not a drive permit; MCU must assert `BUS_REQ` and wait for `BUS_GNT`.

## Q5 - Null / zero-length / chain-end semantics

**Decided (D14 / D18 / D19 / D23):**

- **End of chain:** fetched TCD with **`CTRL_FLAGS.QUIT=1`** → IDLE (DONE); no copy for that TCD. Next START always refetches **`0x000000` on PSRAM 0**
- `TRANSFER_LEN == 0` → **no-op** descriptor; immediately follow `NEXT_TCD` with no data moved (unless the TCD is already a quit TCD)
- Fixed head: START always fetches `0x000000` on PSRAM 0; place a `QUIT` TCD there for an empty run
- `NEXT_TCD` with address bits `0x000000` is a **valid** next address (device from `CTRL_FLAGS.NEXT_DEVICE`), not end-of-chain

**Still open:**

- Can a descriptor point to itself? Without cond-stop this only spins until **`rst_n`** - allow with reset, or reject?

## Q6 - ALU immediate storage

**Deferred (post-V1 / D12).** See `10-post-v1-features.md` section 1. Prefer TCD-resident `IMM` byte when ALU returns; extend reserved `CTRL_FLAGS` bits for op select.

## Q7 - Ring buffer encoding

**Deferred (post-V1 / D12).** See `10-post-v1-features.md` section 3. Prefer unused `CTRL_FLAGS` bits when ring returns.

## Q8 - SPI vs QPI for V1 data path

**Decided (D15 / D17):** QPI for all ASIC DMA data read/write. ASIC emits **no SPI** and **no** Enter/Exit Quad. Sole QPI read opcode is **`0xEB`** (write `0x02`). MCU owns enter/exit QPI via pass-through.

## Q9 - Clock frequency target

**Decided (D16, amended D27):** demoboard / design **`clk` 66 MHz**; engine **SCK = clk/2**; sample read data on the **rising** edge of SCK. Numbers unchanged after the IHP switch; justification is demoboard / `tACLK` / SCK generation, **not** a sky130 or IHP published pad MHz rating (IHP Open PDK has none). Phase 3 must re-validate `tACLK` / board / TT mux / IHP pad liberty against this target before shuttle freeze.

## Q10 - Sensor data ingress path

**Lean (D12):** V1 is **memove only** - data already in PSRAM (MCU wrote it during pass-through). No live ADC/stream ingress requirement.

Optional later: streamed host-pin ingress and/or telemetry features in `10-post-v1-features.md`.

## Q11 - Feature freeze for first shuttle

**Decided (D12 / D14 / D15 / D16 / D17 / D18 / D19 / D20 / D21 / D22 / D23 / D24 / D25 / D26 / D27):** V1 = IHP SG13G2 / TTIHP26b + `BUS_REQ`/`BUS_GNT` pass-through + ASIC bus keeper while `~BUS_GNT` (board 10 kΩ CS pull-ups) + QPI (`0xEB`/`0x02`) + MCU enter/exit QPI + dual CS + 11-byte TCD with big-endian 24-bit pointer fields + device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`) + `QUIT` end-of-chain (next START from fixed head) + fixed head at 0/PSRAM0 + cross-device + chaining + START/DONE/BUS_REQ/BUS_GNT pins (**no ABORT**; kill via `rst_n`) + 66 MHz `clk` / SCK=clk/2 / rising-edge RX + D21 (`~busy` / `wdata_next` / length-driven write) + **1-byte** data buffer with depth-agnostic correctness. **Out of V1:** ALU, conditional stop, ring, ASIC flash (post-V1 ladder in `10-post-v1-features.md`).

Still open inside V1: multi-outstanding (lean: no), `uo_out[7:2]` status packing (Q3 remainder).

## Q12 - Error model

Which conditions sticky-error vs ignore vs halt?

- Illegal host sequence while running (a post-sync START pulse while busy is **ignored and not queued** per D14; other illegal sequences TBD)
- Bad descriptor address
- **Out-of-range PSRAM address:** firmware must keep pointer bit 23 clear and ensure each complete TCD fetch, source range, and destination range remains within `0x000000..0x7FFFFF`. A request that starts outside the window or only partly crosses outside it is currently undefined. Should hardware detect this and force deterministic failure, such as a sticky error plus halt, rather than allowing truncation or wrap behavior?
- CE# policy violation (should be impossible if engine correct)
- External memory timeout (if detectable at all)
- Illegal both RAM CS asserted during a data TCD (should be impossible if engine correct; quit TCD never asserts CS for a copy)

## Q13 - PSRAM device select encoding (dual-device)

**Decided (D19 / D24):** device select is **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** (`0`=PSRAM0, `1`=PSRAM1). QSPI drives `A[22:0]` from `ptr[22:0]`; pointer MSBs unused. End-of-chain is **`CTRL_FLAGS.QUIT`**. Cross-device byte copy: read-then-write with only one CE# low at a time; no multi-outstanding for V1. Fixed head on PSRAM 0 at address 0 (D18). Working set remains **88 bits**.

## Q14 - TCD pointer byte order

**Decided (D25):** `SRC_PTR`, `DEST_PTR`, and `NEXT_TCD` use **big-endian byte order** in PSRAM. Firmware serializes each 24-bit pointer most-significant byte first (`0x123456` becomes `12 34 56`). The existing RTL fetch ordering is unchanged, and payload bytes are copied without endian conversion.
