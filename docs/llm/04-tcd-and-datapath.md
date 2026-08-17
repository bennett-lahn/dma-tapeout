# TCD Format, Datapath, and Control Details

Status: **11-byte** TCD layout, **big-endian 24-bit pointer fields** (D25), device selects in **`CTRL_FLAGS`** (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`; D24), fixed head at `0x000000`/PSRAM0, `QUIT` end-of-chain, zero-length no-op, QPI data path, and **1-byte** depth-agnostic data buffer are V1 planning freezes (D14 / D15 / D18 / D19 / D20 / D22 / D24 / D25). No ALU / ring / conditional-stop in V1.

Post-V1 (ALU, `COND_STOP`, ring, flash): [`10-post-v1-features.md`](10-post-v1-features.md).

## Address model (shared with system architecture)

| Rule | V1 |
|---|---|
| Pointer width on-chip / in TCD | **24-bit** (byte address; `[23]` unused / 0) |
| QSPI address phase | **`ptr[22:0]`** as device byte address (`A[22:0]`) |
| Device select | **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** (D24); not pointer MSBs |
| Fixed head | START always fetches **`0x000000` on PSRAM 0** (no head register / pins) |
| End of chain | Fetched TCD with **`CTRL_FLAGS.QUIT=1`** → IDLE / DONE (no execute); next START fetches fixed head again (D23) |
| Address 0 | **Valid** TCD/buffer address (not null) |
| Reachable window | Full APS6404L range (`A[22:0]`, 8 MB) per device |
| DFF cost | Working TCD **88 DFFs** (no head pointer; full 11-byte record including reserved `[3:0]`) |

## Transfer Control Descriptor (TCD)

A TCD is a packed record stored in PSRAM. The ASIC fetches it into working registers, checks `QUIT`, executes a pure byte copy (or no-op if length 0), then follows `NEXT_TCD` on device **`NEXT_DEVICE`**.

### Layout (11-byte TCD)

| Offset | Field | Width | Notes |
|---|---|---|---|
| 0 | `SRC_PTR` | 24 | Big-endian; byte addr; device from `SRC_DEVICE` |
| 3 | `DEST_PTR` | 24 | Byte addr; device from `DEST_DEVICE` |
| 6 | `TRANSFER_LEN` | 8 | Bytes to move; **`0` = no-op** (follow next immediately) |
| 7 | `NEXT_TCD` | 24 | Next descriptor byte address; device from `NEXT_DEVICE`; addr 0 is a normal link |
| 10 | `CTRL_FLAGS` | 8 | Memory byte: live flags in `[7:4]`, reserved in `[3:0]`. RTL `tcd_t` latches the whole byte (no nested ctrl struct). |

`STATE_FETCH` burst-reads **11 bytes** into working registers (held-CE#).

The three 24-bit pointer fields use **big-endian byte order** (D25): the most-significant byte is stored at the lowest PSRAM address. For a pointer value `0x123456`, firmware writes bytes `12 34 56`. This matches the existing RTL fetch order and does not imply any payload conversion. `TRANSFER_LEN` and `CTRL_FLAGS` are single-byte fields.

Exact firmware serialization:

| Offset | Byte value |
|---|---|
| 0 | `SRC_PTR[23:16]` |
| 1 | `SRC_PTR[15:8]` |
| 2 | `SRC_PTR[7:0]` |
| 3 | `DEST_PTR[23:16]` |
| 4 | `DEST_PTR[15:8]` |
| 5 | `DEST_PTR[7:0]` |
| 6 | `TRANSFER_LEN[7:0]` |
| 7 | `NEXT_TCD[23:16]` |
| 8 | `NEXT_TCD[15:8]` |
| 9 | `NEXT_TCD[7:0]` |
| 10 | `CTRL_FLAGS[7:0]` |

### `CTRL_FLAGS` (V1 / D19 / D24 names; bit positions from `types.svh`)

Layout follows packed `tcd_t` in `src/rtl/types.svh` (first field = MSB). Hardware latches `reserved[3:0]` after `quit`; that nibble is the last nibble of the 11-byte record.

| Bits | Name | Encoding |
|---|---|---|
| 7 | `NEXT_DEVICE` | `0` = next TCD on PSRAM 0; `1` = next TCD on PSRAM 1 |
| 6 | `DEST_DEVICE` | `0` = DEST on PSRAM 0; `1` = DEST on PSRAM 1 |
| 5 | `SRC_DEVICE` | `0` = SRC on PSRAM 0; `1` = SRC on PSRAM 1 |
| 4 | `QUIT` | `1` = go IDLE / DONE after fetch (do not execute); `0` = run |
| 3:0 | reserved | Write 0. Hardware latches these bits (D31); V1 control ignores them. Post-V1 (ALU / cond-stop / ring) can reuse this nibble. |

Memory TCD stays **11 bytes / 88 bits**. Working `tcd_t` is **88 bits**: packed order is `src_ptr`, `dest_ptr`, `transfer_len`, `next_tcd`, `next_tcd_device`, `dest_device`, `src_device`, `quit`, `reserved` (packed LSB nibble = reserved). FETCH is MSB-first (22 wire nibbles); every nibble is latched. The flags nibble (`CTRL_FLAGS[7:4]`) is the 21st wire nibble; reserved (`CTRL_FLAGS[3:0]`) is the 22nd.

**Quit / DONE (D19/D23):** after fetch, if `QUIT==1`, go IDLE / DONE without executing that TCD's copy. Never assert both CS lines for a quit TCD. The next accepted **START** begins a new run from **`0x000000` on PSRAM 0** (fixed head); the engine does not resume mid-chain.

Device flags are **sticky** for the life of that TCD. Pointer increments advance only `[22:0]`.

No in-flight ALU, ring wrap, or conditional stop in V1.

### Pointer updates (V1)

After a completed copy step of `k` bytes (`k = min(N, TRANSFER_LEN)`; V1 `N=1`), decrement `TRANSFER_LEN` by `k`. RTL advances `SRC_PTR` by `N` on READ exit and `DEST_PTR` by `N` on WRITE exit (shared adder). When more bytes remain, `k = N` so that matches the spec. On the final chunk the new pointer values are don't-care (descriptor complete; next FETCH overwrites the working TCD). Device flags stay sticky.

No fixed-src/fixed-dest, no ring. (Fill/gather return with post-V1 flag extensions.)

Wrap within the 8 MB device is a firmware concern.

## Execution algorithm (conceptual)

```
# START: fixed head
fetch_ptr = 0x000000   # addr 0, PSRAM 0
fetch_device = 0
loop:
    FETCH 11-byte TCD at fetch_ptr[22:0] from device fetch_device into working regs
    if QUIT:
        return IDLE; DONE=1   # quit TCD (D19); next START → fixed head again (D23)
    while TRANSFER_LEN > 0:       # LEN==0 skips this loop (no-op TCD)
        # Buffer depth N is a parameter (V1: N=1). Correctness must not assume N (D20).
        k = min(N, TRANSFER_LEN)
        buf[0..k) = READ(SRC_PTR, k)   # CS from SRC_DEVICE; addr SRC_PTR[22:0]
        WRITE(DEST_PTR, buf[0..k))     # CS from DEST_DEVICE; may be other device
        TRANSFER_LEN -= k
        SRC_PTR += N; DEST_PTR += N   # RTL; consumed only if TRANSFER_LEN remains (then k was N)
        # Final-step pointer values are don't-care once TRANSFER_LEN reaches 0.
        # V1 N=1: CE# rises every short txn; tCEM / page slicer not required
    fetch_ptr = NEXT_TCD
    fetch_device = NEXT_DEVICE
```

No host **ABORT** pin (D23): stop a runaway DMA with **`rst_n`**. Mid-run bus yield uses **BUS_REQ** / **BUS_GNT** (D22). While `~BUS_GNT`, ASIC parks the shared bus (D26); board has **10 kΩ** CS pull-ups. Firmware contract: `docs/human/architecture/firmware.md`.

**Data buffer (D20):** V1 implements `N=1` (8 DFFs) as a nibble shift register (LSB-insert on READ, drop MSB nibble on WRITE). FSM / QSPI path must remain correct for any `N >= 1`; deepening the scratch is optional performance work, not a protocol or TCD change. Working TCD fetch is also a shift register: all 22 wire nibbles into `tcd_t` (D31). Short held CE# pulses also make APS6404L `tCEM` and Linear Burst one-page-cross rules non-binding for V1 (see human [`descriptor-fsm.md`](../human/architecture/blocks/descriptor-fsm.md)).

**FSM ↔ QSPI (D21):** start with `txn_valid` only when `~busy` (no `txn_ready` / no `wdone`). Engine does not latch the request; FSM holds `{cmd, addr, device_sel, byte_len}`. `byte_len` width is `QPI_BYTE_LEN_W` from `qspi_pkg`. Writes: first nibble on `wdata` with `txn_valid`; `wdata_next` then asserts iff another nibble is needed to finish that transaction, for exactly `2 * byte_len - 1` pulses. When `wdata_next` asserts, the next nibble must be on `wdata` before the next `clk` (same-cycle) to preserve SPI/SIO setup. It never asserts after the final nibble or outside the active write. The engine ends after `2 * byte_len` SCK. SCK = clk/2. Detail: [`03-architecture.md`](03-architecture.md) / human [`qspi-engine.md`](../human/architecture/blocks/qspi-engine.md).

## Host programming model (firmware view)

At boot / setup:

1. Ensure both PSRAM devices are in QPI (MCU via `BUS_REQ`/`BUS_GNT`; D17/D22).
2. **Creating TCDs:** explicitly serialize each 24-bit pointer **most-significant byte first** into the 11-byte TCD; do not copy a native little-endian MCU integer or padded C structure directly. Write TCDs into PSRAM under grant (`BUS_GNT`, `uio_oe=0`). Place the first TCD (or a `QUIT` TCD for an empty run) at **`0x000000` on PSRAM 0**. Chain with `NEXT_TCD` + `NEXT_DEVICE`, set `SRC_DEVICE` / `DEST_DEVICE` per copy, and terminate with a TCD whose `QUIT=1`.
3. Validate every complete memory range against the per-device address window `0x000000..0x7FFFFF` (`A[22:0]`), using widened arithmetic: each 11-byte TCD fetch must satisfy `tcd_ptr + 10 <= 0x7FFFFF`; when `TRANSFER_LEN > 0`, both `SRC_PTR + TRANSFER_LEN - 1` and `DEST_PTR + TRANSFER_LEN - 1` must be `<= 0x7FFFFF`. Pointer bit 23 must be zero. Any start address outside the window, or any operation only partly inside it, is undefined behavior in V1.
4. High-Z MCU QSPI; drop **BUS_REQ**; wait for **BUS_GNT** low; assert **START** (`ui_in[0]`) while DONE is high and hold it long enough for top-level synchronization. The top level converts the captured rising edge into the one-`clk` pulse consumed by `sys_controller`; deassert START before issuing another command.
5. Wait for **DONE** again (`uo_out[0]`), pause mid-run with **BUS_REQ**, or assert **`rst_n`** to kill the run (D23).
6. **Reading memory:** assert `BUS_REQ`, wait for `BUS_GNT`; firmware re-enables MCU QSPI and checks destinations.
7. After a `QUIT` return to IDLE, a later **START** always re-fetches the head at **`0x000000` / PSRAM 0**.

Condensed firmware contract: [`../human/architecture/firmware.md`](../human/architecture/firmware.md).

### Example chain (bulk mover)

1. **TCD at 0 / PSRAM0:** copy 256 B from RAM A → RAM B (`SRC_DEVICE=0`, `DEST_DEVICE=1`); `NEXT_TCD` + `NEXT_DEVICE` point at TCD B.
2. **TCD B:** next extent.
3. **Quit TCD:** `QUIT=1` → IDLE / DONE; next START from address 0 / PSRAM 0.

## Comparison to static multi-channel DMA

Static 2-channel designs keep multiple SRC/DEST/LEN sets in DFFs. That barely fits 2 tiles in known prior art.

Descriptor DMA:

- Reduces simultaneous resident configuration
- Increases FSM / SPI sequencing complexity
- Scales to many software-defined transfers without more channel register files

That is the central architectural bet of this project.

Per TinyDMA-2C prior art, that design used 16-bit internal addresses with the SPI address upper byte tied to zero as an area tactic. This project **does not** adopt that cut; pointers are full 24-bit addresses with device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`) and `QUIT` in `CTRL_FLAGS`.

## Verification implications (datapath)

Minimum interesting checks:

- Single TCD copy (same-device A, same-device B)
- Cross-device A→B and B→A
- Dual-TCD chain (scatter-gather), including next TCD on the other device
- Zero-length no-op then follow next
- Quit TCD (`QUIT=1`) returns IDLE / DONE; subsequent START refetches fixed head
- Empty run: quit TCD at `0x000000` / PSRAM0 → immediate DONE after one fetch
- `NEXT_TCD` address bits `== 0` as a valid link (not end-of-chain)
- `rst_n` mid-run returns to IDLE (no soft abort)
- START ignored while not idle
- Short CE# pulses from `N=1` / 11-byte fetch (no dedicated `tCEM` slicer required in V1)
- Pass-through only under `BUS_GNT` (D22)
- Addresses above 64 KB (e.g. `0x010000`) work end-to-end
- Full `A[22:0]` window with device selects in `CTRL_FLAGS`
- Known TCD byte vector proving big-endian pointer decoding, including a pointer such as `0x123456` serialized as `12 34 56`
