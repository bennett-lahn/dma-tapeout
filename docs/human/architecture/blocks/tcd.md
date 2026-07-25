# Transfer Control Descriptor (TCD)

Status: **11-byte** layout, 24-bit pointers with `ptr[23]` device select, `QUIT` end-of-chain, fixed head (D18/D19).

Post-V1 ALU / cond-stop / ring (extend reserved flag bits): `[../post-v1.md](../post-v1.md)`.

## Role

Programmable transfer record stored in PSRAM. The ASIC fetches it into working registers, **byte-copies** source to dest (or no-ops if length 0), then follows `NEXT_TCD` (die from `NEXT_TCD[23]`).

Addressing context (see `[../system.md](../system.md)` memory section):

- Pointers are **24-bit**: `[22:0]` byte address, `[23]` **die** (`0`=PSRAM 0, `1`=PSRAM 1)
- Fixed head: first TCD at `0x000000` **on PSRAM 0**
- End of chain: TCD with `QUIT=1` → DONE
- Address 0 remains a valid TCD/buffer address



## Layout (11 bytes)

Little-endian multi-byte fields (working assumption).


| Offset | Field          | Width | Description                                                  |
| ------ | -------------- | ----- | ------------------------------------------------------------ |
| 0      | `SRC_PTR`      | 24    | Source `[22:0]` addr + `[23]` die                            |
| 3      | `DEST_PTR`     | 24    | Dest `[22:0]` addr + `[23]` die                              |
| 6      | `TRANSFER_LEN` | 8     | Bytes to move; `0` **= no-op**                               |
| 7      | `NEXT_TCD`     | 24    | Next TCD (`[22:0]` addr, `[23]` die); addr 0 is a valid link |
| 10     | `CTRL_FLAGS`   | 8     | `QUIT` + reserved                                            |




### `CTRL_FLAGS`


| Bits | Name     | Encoding                                                     |
| ---- | -------- | ------------------------------------------------------------ |
| 0    | `QUIT`   | `1` = IDLE / DONE after fetching TCD (no execute); `0` = run |
| 7:1  | reserved | Write 0; post-V1                                             |


After FETCH, if `QUIT=1`, go **IDLE** / **DONE** (no copy).

`STATE_FETCH` burst-reads these **11 bytes** (first: addr 0 / PSRAM 0; later: `NEXT_TCD`) into the working registers (held-CE#).

Firmware may place TCDs on convenient alignments (e.g. 16-byte); hardware still consumes exactly 11 bytes per record.

## Behavior

```
fetch_ptr = 0x000000   # fixed head addr 0 / PSRAM 0 (D18)
loop:
    FETCH 11-byte TCD at fetch_ptr[22:0] from die fetch_ptr[23]
    if QUIT: return IDLE / DONE
    while TRANSFER_LEN > 0:      # LEN==0 is no-op
        byte = READ(SRC_PTR)     # CS from SRC_PTR[23]
        WRITE(DEST_PTR, byte)    # CS from DEST_PTR[23]
        SRC_PTR[22:0] += 1; DEST_PTR[22:0] += 1   # keep [23]
        TRANSFER_LEN -= 1
        # V1: each READ/WRITE is N=1 byte; CE# rises each txn (no tCEM slicer)
    fetch_ptr = NEXT_TCD
```



### Chain rules


| Case                     | Behavior                                                      |
| ------------------------ | ------------------------------------------------------------- |
| `QUIT=1`                 | Quit TCD → **IDLE** / **DONE** / pass-through                 |
| `TRANSFER_LEN == 0`      | No-op; immediately follow `NEXT_TCD`                          |
| Valid data TCD           | Fetch/run; die from pointer MSBs                              |
| Empty run                | Quit TCD at `0x000000` / PSRAM 0                              |
| Self-pointing `NEXT_TCD` | Open; without cond-stop this can spin until **abort** / reset |




## Related

- System memory map: `[../system.md](../system.md)`
- Working regs: `[working-registers.md](working-registers.md)`
- FSM: `[descriptor-fsm.md](descriptor-fsm.md)`
- Agent detail: `[../../../llm/04-tcd-and-datapath.md](../../../llm/04-tcd-and-datapath.md)`

