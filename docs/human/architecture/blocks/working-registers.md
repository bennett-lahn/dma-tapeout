# Working Registers

Status: widths follow the V1 24-bit / **11-byte** TCD freeze (D13).

## Role

Hold only the **currently executing TCD** on-chip. Static multi-channel register files are a non-goal.

## Fields (V1)

| Field | Width | Role |
|---|---|---|
| `SRC_PTR` | 24 | Source byte address |
| `DEST_PTR` | 24 | Destination byte address |
| `TRANSFER_LEN` | 8 | Bytes remaining in this descriptor (0 = no-op) |
| `NEXT_TCD` | 24 | Next descriptor address (`0x000000` = end of chain / null) |
| `CTRL_FLAGS` | 8 | `SRC_DEV` / `DEST_DEV` / `NEXT_DEV` + reserved |

Approximate working metadata: **88 DFFs**, plus:

- 8-bit **data buffer** between read and write
- 24-bit **head** pointer
- FSM state flops
- QSPI shifter / bit counters / CE# timing counters
- Arm / error sticky bits

No ALU immediate in V1. Post-V1 register growth: [`../post-v1.md`](../post-v1.md).

## Related

- TCD layout: [`tcd.md`](tcd.md)
- FSM: [`descriptor-fsm.md`](descriptor-fsm.md)
- Agent detail: [`../../../llm/04-tcd-and-datapath.md`](../../../llm/04-tcd-and-datapath.md)
