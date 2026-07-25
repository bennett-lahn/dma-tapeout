# TCD Format, Datapath, and Control Details

Status: **11-byte** TCD layout, 24-bit address model (`ptr[23]` = device), fixed head at `0x000000`/PSRAM0, `QUIT` end-of-chain, zero-length no-op, QPI data path, and **1-byte** depth-agnostic data buffer are V1 planning freezes (D14 / D15 / D18 / D19 / D20 / D22). No ALU / ring / conditional-stop in V1.

Post-V1 (ALU, `COND_STOP`, ring, flash): [`10-post-v1-features.md`](10-post-v1-features.md).

## Address model (shared with system architecture)

| Rule | V1 |
|---|---|
| Pointer width on-chip / in TCD | **24-bit** |
| QSPI address phase | **`ptr[22:0]`** as device byte address (`A[22:0]`) |
| Device select | **`ptr[23]`** (`0`=PSRAM 0, `1`=PSRAM 1) on `SRC_PTR` / `DEST_PTR` / `NEXT_TCD` |
| Fixed head | START always fetches **`0x000000` on PSRAM 0** (no head register / pins) |
| End of chain | Fetched TCD with **`CTRL_FLAGS.QUIT=1`** → IDLE / DONE (no execute) |
| Address 0 | **Valid** TCD/buffer address (not null) |
| Reachable window | Full APS6404L range (`A[22:0]`, 8 MB) per die |
| DFF cost | Working TCD **88 DFFs** (no head pointer) |

## Transfer Control Descriptor (TCD)

A TCD is a packed record stored in PSRAM. The ASIC fetches it into working registers, checks `QUIT`, executes a pure byte copy (or no-op if length 0), then follows `NEXT_TCD` (die from `NEXT_TCD[23]`).

### Layout (11-byte TCD)

| Offset | Field | Width | Notes |
|---|---|---|---|
| 0 | `SRC_PTR` | 24 | Little-endian working assumption; `[22:0]` byte addr, `[23]` src die |
| 3 | `DEST_PTR` | 24 | `[22:0]` byte addr, `[23]` dest die |
| 6 | `TRANSFER_LEN` | 8 | Bytes to move; **`0` = no-op** (follow next immediately) |
| 7 | `NEXT_TCD` | 24 | Next descriptor (`[22:0]` addr, `[23]` die); addr 0 is a normal link |
| 10 | `CTRL_FLAGS` | 8 | `QUIT` + reserved |

`STATE_FETCH` burst-reads **11 bytes** into working registers (held-CE#).

### `CTRL_FLAGS` (V1 / D19)

| Bits | Name | Encoding |
|---|---|---|
| 0 | `QUIT` | `1` = go IDLE / DONE after fetch (do not execute); `0` = run |
| 7:1 | reserved | Write 0; available for post-V1 (ALU / cond-stop / ring) |

**Quit / DONE:** after fetch, if `QUIT==1`, go IDLE / DONE without executing that TCD's copy. Never assert both CS lines for a quit TCD.

Device bits live in the pointer MSBs and are **preserved** on SRC/DEST increments for that descriptor (`ptr[23]` sticky; only `[22:0]` advances).

No in-flight ALU, ring wrap, or conditional stop in V1.

### Pointer updates (V1)

Every completed copy step of `k` bytes (`k = min(N, TRANSFER_LEN)`; V1 `N=1`): **`SRC_PTR[22:0] += k`**, **`DEST_PTR[22:0] += k`** (linear only); keep `ptr[23]`. No fixed-src/fixed-dest, no ring. (Fill/gather return with post-V1 flag extensions.)

Wrap within the 8 MB window is a firmware concern.

## Execution algorithm (conceptual)

```
# START: fixed head
fetch_ptr = 0x000000   # addr 0, PSRAM 0 (bit23=0)
loop:
    FETCH 11-byte TCD at fetch_ptr[22:0] from die fetch_ptr[23] into working regs
    if QUIT:
        return IDLE; DONE=1; pass-through on   # quit TCD (D19)
    while TRANSFER_LEN > 0:       # LEN==0 skips this loop (no-op TCD)
        # Buffer depth N is a parameter (V1: N=1). Correctness must not assume N (D20).
        k = min(N, TRANSFER_LEN)
        buf[0..k) = READ(SRC_PTR, k)   # CS from SRC_PTR[23]; addr SRC_PTR[22:0]
        WRITE(DEST_PTR, buf[0..k))     # CS from DEST_PTR[23]; may be other die
        SRC_PTR[22:0] += k
        DEST_PTR[22:0] += k
        TRANSFER_LEN -= k
        # V1 N=1: CE# rises every short txn; tCEM / page slicer not required
    fetch_ptr = NEXT_TCD
```

Host **ABORT** (`ui_in[1]`): finish the current QPI transaction, then IDLE / DONE (D14/D18). Mid-run bus yield uses **BUS_REQ** / **BUS_GNT** (D22), not abort.

**Data buffer (D20):** V1 implements `N=1` (8 DFFs). FSM / QSPI path must remain correct for any `N >= 1`; deepening the scratch is optional performance work, not a protocol or TCD change. Short held CE# pulses also make APS6404L `tCEM` and Linear Burst one-page-cross rules non-binding for V1 (see human [`descriptor-fsm.md`](../human/architecture/blocks/descriptor-fsm.md)).

**FSM ↔ QSPI (D21):** start with `txn_valid` only when `~busy` (no `txn_ready` / no `wdone`). Engine does not latch the request; FSM holds `{cmd, addr, die_sel, byte_len}`. `byte_len` width is `QSPI_BYTE_LEN_W` from `qspi_pkg`. Writes: first nibble on `wdata` with `txn_valid`; follow `wdata_next` pulses; engine ends after `2 * byte_len` SCK. SCK = clk/2. Detail: [`03-architecture.md`](03-architecture.md) / human [`qspi-engine.md`](../human/architecture/blocks/qspi-engine.md).

## Host programming model (firmware view)

At boot / setup:

1. Ensure both PSRAM dies are in QPI (MCU via `BUS_REQ`/`BUS_GNT`; D17/D22).
2. **Creating TCDs:** pack **11-byte** TCDs into PSRAM under grant (`BUS_GNT`, `uio_oe=0`). First TCD (or a `QUIT` TCD for empty run) at **`0x000000` on PSRAM 0**. Chain with `NEXT_TCD` (die in bit 23). Terminate with a TCD whose `QUIT=1`.
3. Place source and destination regions in the usable device range; set pointer MSBs for die.
4. High-Z MCU QSPI; drop **BUS_REQ**; wait for **BUS_GNT** low; assert **START** (`ui_in[0]`) while DONE is high.
5. Wait for **DONE** again (`uo_out[0]`), assert **ABORT** (`ui_in[1]`), or pause with **BUS_REQ**.
6. **Reading memory:** assert `BUS_REQ`, wait for `BUS_GNT`; firmware re-enables MCU QSPI and checks destinations.

### Example chain (bulk mover)

1. **TCD at 0 / PSRAM0:** copy 256 B from RAM A → RAM B (`SRC_PTR[23]=0`, `DEST_PTR[23]=1`); `NEXT_TCD` points at TCD B (die in `NEXT_TCD[23]`).
2. **TCD B:** next extent.
3. **Quit TCD:** `QUIT=1` → IDLE / DONE.

## Comparison to static multi-channel DMA

Static 2-channel designs keep multiple SRC/DEST/LEN sets in DFFs. That barely fits 2 tiles in known prior art.

Descriptor DMA:

- Reduces simultaneous resident configuration
- Increases FSM / SPI sequencing complexity
- Scales to many software-defined transfers without more channel register files

That is the central architectural bet of this project.

Per TinyDMA-2C prior art, that design used 16-bit internal addresses with the SPI address upper byte tied to zero as an area tactic. This project **does not** adopt that cut; pointers are full 24-bit with device in `ptr[23]` and `QUIT` in `CTRL_FLAGS`.

## Verification implications (datapath)

Minimum interesting checks:

- Single TCD copy (same-device A, same-device B)
- Cross-device A→B and B→A
- Dual-TCD chain (scatter-gather), including next TCD on the other die
- Zero-length no-op then follow next
- Quit TCD (`QUIT=1`) returns IDLE / DONE
- Empty run: quit TCD at `0x000000` / PSRAM0 → immediate DONE after one fetch
- `NEXT_TCD` address bits `== 0` as a valid link (not end-of-chain)
- Abort mid-run: current QPI txn completes, then IDLE
- START ignored while not idle
- Short CE# pulses from `N=1` / 11-byte fetch (no dedicated `tCEM` slicer required in V1)
- Pass-through only while DONE
- Addresses above 64 KB (e.g. `0x010000`) work end-to-end
- Full `A[22:0]` window with die select in `ptr[23]`
