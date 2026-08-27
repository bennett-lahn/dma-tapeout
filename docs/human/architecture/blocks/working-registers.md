# Working Registers

Status: implemented as the working TCD fields inside `sys_controller.sv` (88 DFFs; full 11-byte record; widths follow D18/D19/D24/D31/D32). No head pointer.

## Role

Hold only the **currently executing TCD** on-chip. Static multi-channel register files are a non-goal.

## Fields (V1)

| Field | Width | Role |
|---|---|---|
| `SRC_PTR` | 24 | Source byte address (`[22:0]` on wire; `[23]` don't-care; D35) |
| `DEST_PTR` | 24 | Dest byte address (`[22:0]` on wire; `[23]` don't-care; D35) |
| `TRANSFER_LEN` | 8 | Bytes remaining in this descriptor (0 = no-op) |
| `NEXT_TCD` | 24 | Next descriptor byte address (`[22:0]` on wire; `[23]` don't-care; D35) |
| `NEXT_DEVICE` | 1 | Next-TCD device (`CTRL_FLAGS` bit 7) |
| `DEST_DEVICE` | 1 | Dest device (`CTRL_FLAGS` bit 6) |
| `SRC_DEVICE` | 1 | Source device (`CTRL_FLAGS` bit 5) |
| `QUIT` | 1 | End-of-chain after fetch (`CTRL_FLAGS` bit 4) |

`CTRL_FLAGS[3:0]` reserved is the last nibble of the 11-byte TCD. Hardware latches it (D31); V1 control ignores it. Firmware still writes 0.

**Memory layout:** last TCD byte is still called **`CTRL_FLAGS`** (offset 10). Packed `tcd_t` in `types.svh` is the layout: `next_tcd_device`, `dest_device`, `src_device`, `quit` occupy `CTRL_FLAGS[7:4]` (packed LSB of that nibble = `quit`); `reserved` is `CTRL_FLAGS[3:0]` (packed LSB of `tcd_t`).

Device selects (D24):

| Flag | Selects device for |
|---|---|
| `SRC_DEVICE` | Source reads (`SRC_PTR`) |
| `DEST_DEVICE` | Destination writes (`DEST_PTR`) |
| `NEXT_DEVICE` | Next TCD fetch (`NEXT_TCD`) |

Encoding for each: `0`=PSRAM 0, `1`=PSRAM 1. Pointer MSBs are **not** device selects.

Approximate working metadata: **88 DFFs** (24+24+8+24+8 flags), plus:

- **Data buffer** between read and write (**`N=5` bytes / 40 DFFs at tapeout**, nibble shift register; D20)
- FSM state flops
- QSPI shifter / bit counters

### Data buffer depth (D20)

Tapeout implements **`N=5`** (`DMA_BUF_DEPTH=5`) RX→TX hold as a nibble shift register (LSB-insert on READ, drop MSB nibble on WRITE). **Correctness must not depend on buffer depth:** the descriptor FSM and QSPI engine treat depth as parameter `N` in `1..DMA_BUF_DEPTH_MAX` (8). A later deeper scratch (for fewer cmd+addr reissues) must remain a pure performance / DFF trade, not a semantic change to TCD fields, pointer updates, or cross-device CS rules. Short held CE# pulses at tapeout N=5 (and at N=1) also make `tCEM` / Linear Burst page slicing a non-goal until **`N ≥ 60`** (`tCEM` 4 us / read @ 33 MHz SCK) or **`N ≥ 1026`** (two page crosses) - see [`descriptor-fsm.md`](descriptor-fsm.md).

TCD FETCH is also a shift register: all 22 wire nibbles into `tcd_t`, including the last nibble (`CTRL_FLAGS[3:0]` reserved).

No on-chip head pointer (fixed start at `0x000000` / PSRAM 0). No ALU immediate.

## Related

- TCD layout: [`tcd.md`](tcd.md)
- FSM: [`descriptor-fsm.md`](descriptor-fsm.md)
- Agent detail: [`../../../llm/04-tcd-and-datapath.md`](../../../llm/04-tcd-and-datapath.md)
