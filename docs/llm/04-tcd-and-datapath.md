# TCD Format, Datapath, and Control Details

Status: **11-byte** TCD layout (`CTRL_FLAGS` device bits), null at address 0, **24-bit** address model, zero-length no-op, and QPI data path are V1 planning freezes (D13 / D14 / D15). No ALU / ring / conditional-stop in V1.

Post-V1 (ALU, `COND_STOP`, ring, flash): [`10-post-v1-features.md`](10-post-v1-features.md).

## Address model (shared with system architecture)

| Rule | V1 |
|---|---|
| Pointer width on-chip / in TCD | **24-bit** |
| QSPI address phase | **`ptr[22:0]`** as device byte address (`A[22:0]`); `ptr[23]` unused / must be 0 |
| Device select | **`CTRL_FLAGS`** bits (`SRC_DEV` / `DEST_DEV` / `NEXT_DEV`), not pointer MSB |
| Null | `0x000000` reserved - end of chain, not a valid TCD/buffer base |
| Reachable window | Full APS6404L range (`A[22:0]`, 8 MB) per die |
| DFF cost | Working TCD **88 DFFs** + 24-bit head |

## Transfer Control Descriptor (TCD)

A TCD is a packed record stored in PSRAM. The ASIC fetches it into working registers, executes a pure byte copy (or no-op if length 0), then follows `NEXT_TCD` on the die selected by `NEXT_DEV`.

### Layout (11-byte TCD)

| Offset | Field | Width | Notes |
|---|---|---|---|
| 0 | `SRC_PTR` | 24 | Little-endian working assumption; byte address on selected src die |
| 3 | `DEST_PTR` | 24 | Byte address on selected dest die |
| 6 | `TRANSFER_LEN` | 8 | Bytes to move; **`0` = no-op** (follow next immediately) |
| 7 | `NEXT_TCD` | 24 | `0x000000` = end of chain / null |
| 10 | `CTRL_FLAGS` | 8 | Device select (3 bits used); rest reserved |

`STATE_FETCH` burst-reads **11 bytes** into working registers (held-CE#).

### `CTRL_FLAGS` (V1)

| Bit | Name | Meaning |
|---|---|---|
| 0 | `SRC_DEV` | `0` = PSRAM 0 (RAM A), `1` = PSRAM 1 (RAM B) for source |
| 1 | `DEST_DEV` | `0` / `1` for destination |
| 2 | `NEXT_DEV` | `0` / `1` for the die that holds `NEXT_TCD` |
| 7:3 | reserved | Write 0; available for post-V1 (ALU / cond-stop / ring) |

Device flags are latched with the TCD and **preserved** for that descriptor's execution. Pointer increments bump only the address; they do not change device.

No in-flight ALU, ring wrap, or conditional stop in V1.

### Pointer updates (V1)

Every completed beat: **`SRC_PTR += 1`**, **`DEST_PTR += 1`** (linear only). No fixed-src/fixed-dest, no ring. (Fill/gather return with post-V1 flag extensions.)

Wrap within the 8 MB window is a firmware concern.

## Execution algorithm (conceptual)

```
head = programmed_head_pointer   # non-zero 24-bit; lean: fetch from PSRAM 0
load NEXT_TCD = head; NEXT_DEV = head_device  # lean head_device = PSRAM 0
while NEXT_TCD != 0x000000:
    FETCH 11-byte TCD at NEXT_TCD from die NEXT_DEV into working regs
    while TRANSFER_LEN > 0:       # LEN==0 skips this loop (no-op TCD)
        byte = READ(SRC_PTR)      # CS from SRC_DEV
        WRITE(DEST_PTR, byte)     # CS from DEST_DEV; may be other die
        SRC_PTR += 1
        DEST_PTR += 1
        TRANSFER_LEN -= 1
        honor CE# refresh slicing as needed inside READ/WRITE engine
    # fall through via NEXT_TCD / NEXT_DEV already in working regs
# null NEXT_TCD: return IDLE; DONE=1; pass-through on (D14)
```

Host **abort** (pin index open Q3): finish the current QPI transaction, then IDLE / DONE / pass-through (D14).

## Host programming model (firmware view)

At boot / setup:

1. Ensure both PSRAM dies are in QPI (owner TBD: MCU via pass-through or ASIC boot FSM; SPI only for documented enter-quad / reset - D15).
2. **Creating TCDs:** pack one or more **11-byte** TCDs into PSRAM as a linked list while ASIC is idle (`DONE`, `uio_oe=0`). Never at address `0x000000`. Set `SRC_DEV` / `DEST_DEV` / `NEXT_DEV` per hop.
3. Place source and destination regions in the usable device range (firmware-staged buffers for bulk copy demos).
4. Program ASIC head pointer / arm through the Tiny Tapeout host protocol (`ui_in[7:1]` encoding TBD; head is 24-bit; lean head die = PSRAM 0).
5. High-Z MCU QSPI; assert **START** (`ui_in[0]`) while DONE is high.
6. Wait for **DONE** again (`uo_out[0]`) or assert **ABORT**.
7. **Reading memory:** while DONE, pass-through is on; firmware re-enables MCU QSPI and checks destinations.

### Example chain (bulk mover)

1. **TCD A:** copy 256 B from RAM A buffer → RAM B buffer (`SRC_DEV=0`, `DEST_DEV=1`).
2. **TCD B:** copy next 256 B extent (scatter-gather); `NEXT_DEV` points at whichever die holds TCD B.
3. `NEXT_TCD == 0` → IDLE / DONE.

## Comparison to static multi-channel DMA

Static 2-channel designs keep multiple SRC/DEST/LEN sets in DFFs. That barely fits 2 tiles in known prior art.

Descriptor DMA:

- Reduces simultaneous resident configuration
- Increases FSM / SPI sequencing complexity
- Scales to many software-defined transfers without more channel register files

That is the central architectural bet of this project.

Per TinyDMA-2C prior art, that design used 16-bit internal addresses with the SPI address upper byte tied to zero as an area tactic. This project **does not** adopt that cut; pointers are full 24-bit with device select in `CTRL_FLAGS`.

## Verification implications (datapath)

Minimum interesting checks:

- Single TCD copy (same-device A, same-device B)
- Cross-device A→B and B→A
- Dual-TCD chain (scatter-gather), including next TCD on the other die
- Zero-length no-op then follow next
- Null next pointer (`0x000000`) returns IDLE / DONE
- Head at `0x000000` rejected or immediate DONE
- Abort mid-run: current QPI txn completes, then IDLE
- START ignored while not idle
- CE# high inserted before max low time during long moves
- Pass-through only while DONE
- Addresses above 64 KB (e.g. `0x010000`) work end-to-end
- Full `A[22:0]` window (no MSB stolen for device)
