# Transfer Control Descriptor (TCD)

Status: **11-byte** layout with **big-endian 24-bit pointer fields** (D25); device selects in **`CTRL_FLAGS`** (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`; D24); `QUIT` end-of-chain; fixed head (D18/D19/D24).

Post-V1 ALU / cond-stop / ring (extend reserved flag bits `[7:4]`): `[../post-v1.md](../post-v1.md)`.

## Role

Programmable transfer record stored in PSRAM. The ASIC fetches it into working registers, **byte-copies** source to dest (or no-ops if length 0), then follows `NEXT_TCD` on device **`CTRL_FLAGS.NEXT_DEVICE`**.

Addressing context (see `[../system.md](../system.md)` memory section):

- `SRC_PTR` / `DEST_PTR` / `NEXT_TCD` are **24-bit byte addresses**; QSPI uses `[22:0]`; `[23]` unused (drive 0)
- Device selects live in **`CTRL_FLAGS`**: `SRC_DEVICE`, `DEST_DEVICE`, `NEXT_DEVICE` (D24)
- Fixed head: first TCD at `0x000000` **on PSRAM 0**
- End of chain: TCD with `QUIT=1` → DONE
- Address 0 remains a valid TCD/buffer address



## Layout (11 bytes)

The three 24-bit pointer fields are **big-endian** (D25): firmware stores the most-significant byte at the lowest PSRAM address. For example, pointer `0x123456` is stored as bytes `12 34 56`. This is the TCD serialization format only; payload bytes are copied without interpretation or byte swapping.


| Offset | Field          | Width | Description                                                        |
| ------ | -------------- | ----- | ------------------------------------------------------------------ |
| 0      | `SRC_PTR`      | 24    | Source byte address; device from `SRC_DEVICE`                            |
| 3      | `DEST_PTR`     | 24    | Dest byte address; device from `DEST_DEVICE`                             |
| 6      | `TRANSFER_LEN` | 8     | Bytes to move; `0` **= no-op**                                     |
| 7      | `NEXT_TCD`     | 24    | Next TCD byte address; device from `NEXT_DEVICE`; addr 0 is a valid link |
| 10     | `CTRL_FLAGS`   | 8     | `QUIT`, `SRC_DEVICE`, `DEST_DEVICE`, `NEXT_DEVICE`, reserved                |

Total **88 bits** (unchanged).

**RTL note:** `CTRL_FLAGS` is a memory/layout name for byte offset 10. In `tcd_t` (`src/rtl/types.svh`) those bits are flattened (`quit`, `src_device`, `dest_device`, `next_tcd_device`, `reserved`) - no nested ctrl-flags struct.

Firmware must serialize the record explicitly:

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

Do not write a native little-endian MCU integer or padded C structure directly into PSRAM. Use an 11-byte buffer and place each pointer byte as shown.




### `CTRL_FLAGS`


| Bits | Name       | Encoding                                                     |
| ---- | ---------- | ------------------------------------------------------------ |
| 0    | `QUIT`     | `1` = IDLE / DONE after fetching TCD (no execute); `0` = run |
| 1    | `SRC_DEVICE`  | `0` = SRC on PSRAM 0; `1` = SRC on PSRAM 1                   |
| 2    | `DEST_DEVICE` | `0` = DEST on PSRAM 0; `1` = DEST on PSRAM 1                 |
| 3    | `NEXT_DEVICE` | `0` = next TCD on PSRAM 0; `1` = next TCD on PSRAM 1         |
| 7:4  | reserved   | Write 0; post-V1                                             |


After FETCH, if `QUIT=1`, go **IDLE** / **DONE** (no copy). The next accepted **START** always begins again at **`0x000000` on PSRAM 0** (fixed head; D23) - it does not resume mid-chain.

`STATE_FETCH` burst-reads these **11 bytes** (first: addr 0 / PSRAM 0; later: `NEXT_TCD` on `NEXT_DEVICE`) into the working registers (held-CE#).

Firmware may place TCDs on convenient alignments (e.g. 16-byte); hardware still consumes exactly 11 bytes per record.

## Behavior

```
fetch_ptr = 0x000000   # fixed head addr 0 / PSRAM 0 (D18)
fetch_device = 0          # PSRAM 0
loop:
    FETCH 11-byte TCD at fetch_ptr[22:0] from device fetch_device
    if QUIT: return IDLE / DONE   # next START → fixed head again (D23)
    while TRANSFER_LEN > 0:      # LEN==0 is no-op
        byte = READ(SRC_PTR)     # CS from SRC_DEVICE
        WRITE(DEST_PTR, byte)    # CS from DEST_DEVICE
        TRANSFER_LEN -= 1
        if TRANSFER_LEN > 0:
            SRC_PTR[22:0] += 1; DEST_PTR[22:0] += 1
        # Final-step pointer values are not consumed after length reaches zero.
        # V1: each READ/WRITE is N=1 byte; CE# rises each txn (no tCEM slicer)
    fetch_ptr = NEXT_TCD
    fetch_device = NEXT_DEVICE
```



### Chain rules


| Case                     | Behavior                                                      |
| ------------------------ | ------------------------------------------------------------- |
| `QUIT=1`                 | Quit TCD → **IDLE** / **DONE**; next START fetches fixed head |
| `TRANSFER_LEN == 0`      | No-op; immediately follow `NEXT_TCD` on `NEXT_DEVICE`            |
| Valid data TCD           | Fetch/run; devices from `SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`      |
| Empty run                | Quit TCD at `0x000000` / PSRAM 0                              |
| Self-pointing `NEXT_TCD` | Open; without cond-stop this can spin until **`rst_n`**       |




## Related

- System memory map: `[../system.md](../system.md)`
- Working regs: `[working-registers.md](working-registers.md)`
- FSM: `[descriptor-fsm.md](descriptor-fsm.md)`
- Agent detail: `[../../../llm/04-tcd-and-datapath.md](../../../llm/04-tcd-and-datapath.md)`
