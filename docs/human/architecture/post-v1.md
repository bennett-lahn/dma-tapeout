# Post-V1 Features

Not in the V1 ship set. Full sketches: [`../../llm/10-post-v1-features.md`](../../llm/10-post-v1-features.md).

## Add order

1. **Byte ALU** - pass / invert / XOR·ADD/SUB imm on the copy path; extend reserved `CTRL_FLAGS` bits + `IMM`, `STATE_PROCESS`
2. **Conditional stop** - after READ, if pred(byte, IMM) then skip write and follow `NEXT_TCD`; LT/Z/NZ; `LEN==0` = until (needs host abort)
3. **Ring / modulo** - power-of-two wrap on pointer update for last-*N* windows
4. **ASIC flash R/W** - flash CS + opcodes; read first, write maybe (NOR erase/BUSY)

## V1 reminder

V1 is a descriptor **bulk mover** across dual PSRAM (pass-through, QSPI, TCD chain with `ptr[23]` device + `QUIT`, A↔B). No ALU, no ring, no cond-stop, no ASIC flash.
