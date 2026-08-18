# Open Questions

Unresolved items that block a frozen architecture. When one is decided, move the decision into the appropriate LLM doc and leave a one-line pointer here.

## Q1 - Upper address bits for 24-bit PSRAM phases

**Decided (V1 / D10 / D18 / D19 / D24 / D31 / D35):** full **24-bit** internal pointers (byte addresses). Device select is **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** (D24). QSPI uses `ptr[22:0]` as device `A[22:0]` (valid per-device window `0x000000..0x7FFFFF`); **`ptr[23]` is don't-care** (may be any value; not device select; not a firmware-required zero; D35). Address `0x000000` is a **valid** location (fixed head on PSRAM 0). End-of-chain is `CTRL_FLAGS.QUIT`. Working TCD metadata **88 DFFs** (full 11-byte memory TCD, including reserved `[3:0]`; no head register). See `03-architecture.md` / `04-tcd-and-datapath.md`. Packed field order is `types.svh`.

## Q2 - Who initializes PSRAM (reset + enter quad)?

**Decided (D17):** **MCU-owned** via pass-through. MCU resets / Enter Quad / Exit Quad on each device; ASIC emits none of those opcodes and expects both devices already in QPI before START.

## Q3 - Exact host pin protocol

**Decided (D14 / D18 / D19 / D22 / D23 / D34):** IDLE waits for START; START ignored until back in IDLE; `QUIT=1` TCD → IDLE; next START always fetches fixed head at `0x000000` / PSRAM 0; DONE = idle; **no soft ABORT** - use **`rst_n`** to kill a runaway DMA. MCU may drive `uio` only while **`BUS_GNT`** (D22); not merely when DONE.

**Frozen live pins:** `ui_in[0]=START`, `ui_in[2]=BUS_REQ`, `uo_out[0]=DONE`, `uo_out[1]=BUS_GNT`; QSPI on `uio` per system I/O map. **No head-pointer pins** (fixed head at `0x000000` / PSRAM 0).

**Permanently unused (D34):** `ui_in[1]`, `ui_in[7:3]`, and `uo_out[7:2]` - tied 0 in RTL; firmware drives unused inputs low; no future ERROR, status, ABORT, or DFT observe on these bits.

Per TinyDMA-2C prior art, command/payload strobes are one known reference pattern, not a requirement for this project.

## Q4 - Bus release / re-entrancy rules after DONE

**Decided (D14 / D22 / D26):** ASIC **releases** `uio_oe` when yielding for `BUS_REQ` / asserting `BUS_GNT` (after the current QPI txn). While `~BUS_GNT`, ASIC is the **bus keeper** (park CS high / SCK low; SIO floats only for dummy/read). Board **10 kΩ** CS pull-ups cover reset / pre-enable. No host ACK for OE release. **Illegal: MCU drives `uio` while `BUS_GNT` is low.** Idle/`DONE` alone is not a drive permit; MCU must assert `BUS_REQ` and wait for `BUS_GNT`.

## Q5 - Null / zero-length / chain-end semantics

**Decided (D14 / D18 / D19 / D23 / D35):**

- **End of chain:** fetched TCD with **`CTRL_FLAGS.QUIT=1`** → IDLE (DONE); no copy for that TCD. Next START always refetches **`0x000000` on PSRAM 0**
- `TRANSFER_LEN == 0` → **no-op** descriptor; immediately follow `NEXT_TCD` with no data moved (unless the TCD is already a quit TCD)
- Fixed head: START always fetches `0x000000` on PSRAM 0; place a `QUIT` TCD there for an empty run
- `NEXT_TCD` with address bits `0x000000` is a **valid** next address (device from `CTRL_FLAGS.NEXT_DEVICE`), not end-of-chain
- **Self-pointing / cyclic `NEXT_TCD` is allowed (D35).** Without `QUIT`, the DMA spins until **`rst_n`**. No hardware cycle reject.

## Q6 - ALU immediate storage

**Out of scope.** Not in shipped RTL (D12). Historical cut: `07-decision-log.md`.

## Q7 - Ring buffer encoding

**Out of scope.** Not in shipped RTL (D12). Historical cut: `07-decision-log.md`.

## Q8 - SPI vs QPI for data path

**Decided (D15 / D17):** QPI for all ASIC DMA data read/write. ASIC emits **no SPI** and **no** Enter/Exit Quad. Sole QPI read opcode is **`0xEB`** (write `0x02`). MCU owns enter/exit QPI via pass-through.

## Q9 - Clock frequency target

**Decided (D16, amended D27):** demoboard / design **`clk` 66 MHz**; engine **SCK = clk/2**; sample read data on the **rising** edge of SCK. Numbers unchanged after the IHP switch; justification is demoboard / `tACLK` / SCK generation, **not** a sky130 or IHP published pad MHz rating (IHP Open PDK has none). Phase 3 must re-validate `tACLK` / board / TT mux / IHP pad liberty against this target before shuttle freeze.

## Q10 - Sensor data ingress path

**Lean (D12):** data already in PSRAM (MCU wrote it during pass-through). No live ADC/stream ingress.

## Q11 - Feature freeze for first shuttle

**Decided (D12 / D14 / D15 / D16 / D17 / D18 / D19 / D20 / D21 / D22 / D23 / D24 / D25 / D26 / D27 / D34 / D35):** IHP SG13G2 / TTIHP26b + `BUS_REQ`/`BUS_GNT` pass-through + ASIC bus keeper while `~BUS_GNT` (board 10 kΩ CS pull-ups) + QPI (`0xEB`/`0x02`) + MCU enter/exit QPI + dual CS + 11-byte TCD with big-endian 24-bit pointer fields + device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`) + `ptr[23]` don't-care (D35) + self-pointing / cyclic chains allowed (spin until `rst_n`; D35) + `QUIT` end-of-chain (next START from fixed head) + fixed head at 0/PSRAM0 + cross-device + chaining + START/DONE/BUS_REQ/BUS_GNT pins (kill via `rst_n`; unused host bits tied 0, D34) + 66 MHz `clk` / SCK=clk/2 / rising-edge RX + D21 (`~busy` / `wdata_next` / length-driven write) + **5-byte** data buffer (`DMA_BUF_DEPTH=5` tapeout) with depth-agnostic correctness. **Not in shipped RTL:** ALU, conditional stop, ring, ASIC flash. Formal M4 is not a V1 freeze gate (D33).

Still open: multi-outstanding (lean: no).

## Q12 - Error model

**Decided (D34):** no hardware ERROR pin or sticky error logic. Firmware validates TCD chains and address ranges before START. Violations are **undefined** at runtime; recover with **`rst_n`**. Illegal START while busy remains **ignore** (D14). No hardware OOR detect, CE# violation reporting, or memory timeout.

## Q13 - PSRAM device select encoding (dual-device)

**Decided (D19 / D24 / D31 / D35):** device select is **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** (`0`=PSRAM0, `1`=PSRAM1). QSPI drives `A[22:0]` from `ptr[22:0]`; **`ptr[23]` is don't-care** (D35). End-of-chain is **`CTRL_FLAGS.QUIT`**. Cross-device byte copy: read-then-write with only one CE# low at a time; no multi-outstanding for V1. Fixed head on PSRAM 0 at address 0 (D18). Working set is **88 bits** (full 11-byte TCD, including reserved `[3:0]`).

## Q14 - TCD pointer byte order

**Decided (D25):** `SRC_PTR`, `DEST_PTR`, and `NEXT_TCD` use **big-endian byte order** in PSRAM. Firmware serializes each 24-bit pointer most-significant byte first (`0x123456` becomes `12 34 56`). The existing RTL fetch ordering is unchanged, and payload bytes are copied without endian conversion.
