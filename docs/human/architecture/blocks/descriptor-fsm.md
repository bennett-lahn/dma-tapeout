# Descriptor FSM

Status: skeleton. State names and whether UPDATE folds into WRITE are open. Idle / DONE / abort / null / zero-length rules follow D13 / D14.

## Role

Orchestrate descriptor fetch and byte moves. Issues transaction requests to the QSPI engine; does not own bit-level SPI timing.

## Planned states (V1)

1. **`IDLE`** - DONE high; pass-through enabled (`uio_oe=0`); wait for **START** (`ui_in[0]`). START ignored in every other state.
2. **`STATE_FETCH`** - QPI read **11 bytes** from next/head (die from `NEXT_DEV` / head policy) into working regs
3. **`STATE_READ`** - read one source byte from `SRC_PTR` into the data buffer (skipped entirely if `TRANSFER_LEN == 0`)
4. **`STATE_WRITE`** - write data-buffer byte to `DEST_PTR`
5. **`STATE_UPDATE`** - decrement `TRANSFER_LEN`; increment SRC/DEST; if length remains, loop to READ; if length hits 0 and `NEXT_TCD != 0`, go FETCH; if `NEXT_TCD == 0x000000`, return **IDLE** (DONE)

No `STATE_PROCESS` / ALU in V1. Post-V1 may insert process / cond-stop after READ: [`../post-v1.md`](../post-v1.md).

## Notes

- `STATE_UPDATE` may fold into `STATE_WRITE` to save states
- Zero-length TCD: after FETCH, skip READ/WRITE and immediately follow `NEXT_TCD`
- Data moves stay QPI byte-oriented in V1 for simplicity (D15)
- Long transfers still require CE# refresh slicing inside the QSPI engine
- **Abort:** finish current QPI transaction, then IDLE / DONE / pass-through (pin index TBD)

## Related

- TCD: [`tcd.md`](tcd.md)
- QSPI: [`qspi-engine.md`](qspi-engine.md)
- Agent detail: [`../../../llm/03-architecture.md`](../../../llm/03-architecture.md), [`../../../llm/04-tcd-and-datapath.md`](../../../llm/04-tcd-and-datapath.md)
