# Transfer Control Descriptor (TCD)

Status: **11-byte** layout, 24-bit pointers, `CTRL_FLAGS` device bits, null / zero-length rules frozen for V1 planning (D13).

Post-V1 ALU / cond-stop / ring (extend reserved flag bits): [`../post-v1.md`](../post-v1.md).

## Role

Programmable transfer record stored in PSRAM. The ASIC fetches it into working registers, **byte-copies** source to dest (or no-ops if length 0), then follows `NEXT_TCD` on the die selected by `NEXT_DEV`.

Addressing context (see [`../system.md`](../system.md) memory section):

- Pointers in the TCD are **24-bit** (full device window; device is not in the MSB)
- **`0x000000` is reserved null** (end of chain / invalid link)

## Layout (11 bytes)

Little-endian multi-byte fields (working assumption).

| Offset | Field | Width | Description |
|---|---|---|---|
| 0 | `SRC_PTR` | 24 | Source byte address on `SRC_DEV` die |
| 3 | `DEST_PTR` | 24 | Dest byte address on `DEST_DEV` die |
| 6 | `TRANSFER_LEN` | 8 | Bytes to move; **`0` = no-op** |
| 7 | `NEXT_TCD` | 24 | Next TCD address; **`0x000000` = end of chain** |
| 10 | `CTRL_FLAGS` | 8 | Device select + reserved |

### `CTRL_FLAGS`

| Bit | Name | Meaning |
|---|---|---|
| 0 | `SRC_DEV` | PSRAM 0 / 1 for source |
| 1 | `DEST_DEV` | PSRAM 0 / 1 for dest |
| 2 | `NEXT_DEV` | PSRAM 0 / 1 holding next TCD |
| 7:3 | reserved | Write 0; post-V1 |

`STATE_FETCH` burst-reads these **11 bytes** from the head / `NEXT_TCD` address (die from prior `NEXT_DEV` / head policy) into the working registers (held-CE#).

Firmware may place TCDs on convenient alignments (e.g. 16-byte); hardware still consumes exactly 11 bytes per record.

## Behavior

```
head = programmed_head_pointer   # must be non-zero; lean: on PSRAM 0
NEXT = head; NEXT_DEV = head_device
while NEXT != 0x000000:
    FETCH 11-byte TCD at NEXT from NEXT_DEV
    while TRANSFER_LEN > 0:      # LEN==0 is no-op
        byte = READ(SRC_PTR)     # CS from SRC_DEV
        WRITE(DEST_PTR, byte)    # CS from DEST_DEV
        SRC_PTR += 1; DEST_PTR += 1
        TRANSFER_LEN -= 1
        (CE# refresh slicing inside QSPI engine)
    NEXT = NEXT_TCD; use NEXT_DEV from flags
# null → IDLE; DONE; pass-through
```

### Chain rules

| Case | Behavior |
|---|---|
| `NEXT_TCD == 0x000000` | End of chain → **IDLE** / **DONE** / pass-through |
| `TRANSFER_LEN == 0` | No-op; immediately follow `NEXT_TCD` |
| `NEXT_TCD != 0` | Fetch and run that descriptor from `NEXT_DEV` |
| Head `0x000000` at START | Immediate DONE vs sticky error - TBD |
| Self-pointing `NEXT_TCD` | Open; without cond-stop this can spin until **abort** / reset |

## Related

- System memory map: [`../system.md`](../system.md)
- Working regs: [`working-registers.md`](working-registers.md)
- FSM: [`descriptor-fsm.md`](descriptor-fsm.md)
- Agent detail: [`../../../llm/04-tcd-and-datapath.md`](../../../llm/04-tcd-and-datapath.md)
