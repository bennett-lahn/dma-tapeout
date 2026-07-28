# Working Registers

Status: widths follow the V1 24-bit / **11-byte** TCD freeze (D18/D19/D24). No head pointer.

## Role

Hold only the **currently executing TCD** on-chip. Static multi-channel register files are a non-goal.

## Fields (V1)

| Field | Width | Role |
|---|---|---|
| `SRC_PTR` | 24 | Source byte address (`[22:0]` on wire; `[23]` unused / 0) |
| `DEST_PTR` | 24 | Dest byte address (`[22:0]` on wire; `[23]` unused / 0) |
| `TRANSFER_LEN` | 8 | Bytes remaining in this descriptor (0 = no-op) |
| `NEXT_TCD` | 24 | Next descriptor byte address (`[22:0]` on wire; `[23]` unused / 0) |
| `QUIT` | 1 | End-of-chain after fetch (CTRL_FLAGS bit 0) |
| `SRC_DEVICE` | 1 | Source device (CTRL_FLAGS bit 1) |
| `DEST_DEVICE` | 1 | Dest device (CTRL_FLAGS bit 2) |
| `NEXT_DEVICE` | 1 | Next-TCD device (CTRL_FLAGS bit 3) |
| reserved | 4 | CTRL_FLAGS `[7:4]`; write 0 |

**Memory layout:** last TCD byte is still called **`CTRL_FLAGS`** (offset 10). **RTL** (`tcd_t` in `types.svh`) flattens those bits as members of the working TCD struct - there is no nested ctrl-flags typedef.

Device selects (D24):

| Flag | Selects device for |
|---|---|
| `SRC_DEVICE` | Source reads (`SRC_PTR`) |
| `DEST_DEVICE` | Destination writes (`DEST_PTR`) |
| `NEXT_DEVICE` | Next TCD fetch (`NEXT_TCD`) |

Encoding for each: `0`=PSRAM 0, `1`=PSRAM 1. Pointer MSBs are **not** device selects.

Approximate working metadata: **88 DFFs** (unchanged: 24+24+8+24+8), plus:

- **Data buffer** between read and write (**1 byte / 8 DFFs for V1**; D20)
- FSM state flops
- QSPI shifter / bit counters
- Error sticky bits

### Data buffer depth (D20)

V1 implements a **1-byte** RX→TX hold. **Correctness must not depend on buffer depth:** the descriptor FSM and QSPI engine should treat depth as a parameter `N` (V1: `N=1`). A later deeper scratch (for fewer cmd+addr reissues) must remain a pure performance / DFF trade, not a semantic change to TCD fields, pointer updates, or cross-device CS rules. Short held CE# pulses at `N=1` also make `tCEM` / Linear Burst page slicing a non-goal until **`N ≥ 60`** (`tCEM` 4 us / read @ 33 MHz SCK) or **`N ≥ 1026`** (two page crosses) - see [`descriptor-fsm.md`](descriptor-fsm.md).

No on-chip head pointer (fixed start at `0x000000` / PSRAM 0). No ALU immediate in V1. Post-V1 register growth: [`../post-v1.md`](../post-v1.md).

## Related

- TCD layout: [`tcd.md`](tcd.md)
- FSM: [`descriptor-fsm.md`](descriptor-fsm.md)
- Agent detail: [`../../../llm/04-tcd-and-datapath.md`](../../../llm/04-tcd-and-datapath.md)
