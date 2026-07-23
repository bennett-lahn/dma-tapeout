# Descriptor FSM

Status: skeleton. State names and whether UPDATE folds into WRITE are open. Idle / DONE / abort / quit-TCD / zero-length rules follow D14 / D18 / D19.

## Role

Orchestrate descriptor fetch and byte moves. Issues transaction requests to the QSPI engine; does not own bit-level SPI timing.

## Planned states (V1)

1. `IDLE` - DONE high; pass-through enabled (`uio_oe=0`); wait for **START** (`ui_in[0]`). START ignored in every other state.
2. `STATE_FETCH` - QPI read **11 bytes** into working regs. First fetch: `0x000000` / PSRAM 0; later: `NEXT_TCD` (die from bit 23). If `QUIT=1` → **IDLE.**
3. `STATE_READ` - read up to buffer depth `N` source bytes from `SRC_PTR` into the data buffer (V1: `N=1`; skipped if `TRANSFER_LEN == 0`)
4. `STATE_WRITE` - write buffered bytes to `DEST_PTR` (same `N`)
5. `STATE_UPDATE` - decrement `TRANSFER_LEN`; increment SRC/DEST address bits (keep die MSB); if length remains, loop to READ; if length hits 0, go FETCH for next TCD

No `STATE_PROCESS` / ALU in V1. Post-V1 may insert process / cond-stop after READ: `[../post-v1.md](../post-v1.md)`.

## Notes

- Zero-length TCD: after FETCH (and quit check), skip READ/WRITE and immediately follow `NEXT_TCD`
- Data moves stay QPI byte-oriented in V1 for simplicity (D15)
- Buffer depth `N=1` for V1; do not hard-code depth into correctness (D20)
- Long transfers still require CE# refresh slicing inside the QSPI engine
- **ABORT** (`ui_in[1]`): finish current QPI transaction, then IDLE / DONE / pass-through



## Related

- TCD: `[tcd.md](tcd.md)`
- QSPI: `[qspi-engine.md](qspi-engine.md)`
- Agent detail: `[../../../llm/03-architecture.md](../../../llm/03-architecture.md)`, `[../../../llm/04-tcd-and-datapath.md](../../../llm/04-tcd-and-datapath.md)`

