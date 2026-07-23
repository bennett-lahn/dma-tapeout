# Working Registers

Status: widths follow the V1 24-bit / **11-byte** TCD freeze (D18/D19). No head pointer.

## Role

Hold only the **currently executing TCD** on-chip. Static multi-channel register files are a non-goal.

## Fields (V1)

| Field | Width | Role |
|---|---|---|
| `SRC_PTR` | 24 | Source `[22:0]` addr + `[23]` die |
| `DEST_PTR` | 24 | Dest `[22:0]` addr + `[23]` die |
| `TRANSFER_LEN` | 8 | Bytes remaining in this descriptor (0 = no-op) |
| `NEXT_TCD` | 24 | Next descriptor `[22:0]` addr + `[23]` die |
| `CTRL_FLAGS` | 8 | `QUIT` (bit 0) + reserved `[7:1]` |

Approximate working metadata: **88 DFFs**, plus:

- **Data buffer** between read and write (**1 byte / 8 DFFs for V1**; D20)
- FSM state flops
- QSPI shifter / bit counters / CE# timing counters
- Error sticky bits

### Data buffer depth (D20)

V1 implements a **1-byte** RX→TX hold. **Correctness must not depend on buffer depth:** the descriptor FSM and QSPI engine should treat depth as a parameter `N` (V1: `N=1`). A later deeper scratch (for fewer cmd+addr reissues) must remain a pure performance / DFF trade, not a semantic change to TCD fields, pointer updates, CE# refresh policy, or cross-device CS rules.

No on-chip head pointer (fixed start at `0x000000` / PSRAM 0). No ALU immediate in V1. Post-V1 register growth: [`../post-v1.md`](../post-v1.md).

## Related

- TCD layout: [`tcd.md`](tcd.md)
- FSM: [`descriptor-fsm.md`](descriptor-fsm.md)
- Agent detail: [`../../../llm/04-tcd-and-datapath.md`](../../../llm/04-tcd-and-datapath.md)
