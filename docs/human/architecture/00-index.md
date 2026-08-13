# Architecture (Human)

Human architecture docs for the Zero-Overhead Scatter-Gather DMA. **Phase 2:** V1 feature RTL is in `src/rtl/`; cocotb M0–M3 accepted. Shuttle / PDK: **TTIHP26b / ihp-sg13g2** (D27). V1 freezes START/DONE pins (D14/D18), idle/pass-through, **no ABORT** (D23: `rst_n`), QSPI `uio` map (dual PSRAM; flash CS parked high / never selected; D26 bus keeper when live/`~BUS_GNT`, MCU-safe drive while `rst_n=0`), fixed head at `0x000000`/PSRAM0 (D18), **11-byte TCD** with device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`; D24) + `QUIT` flag (D19/D23: quit → IDLE; next START from fixed head), QPI data `0xEB`/`0x02` (D15/D17), MCU-owned enter/exit QPI (D17), **66 MHz `clk` / SCK=clk/2** / rising-edge RX (D16 amended D27), FSM↔QSPI handshake (D21: `~busy`, `wdata_next`, no `txn_ready`/`wdone`). Remaining open: `uo_out[7:2]` status pack; `ui_in[1]` reserved; error model (Q12). Post-V1: ALU → cond-stop → ring → flash.

Verbose agent context: `../../llm/03-architecture.md`, `../../llm/04-tcd-and-datapath.md`, `../../llm/05-qspi-psram.md`, `../../llm/12-firmware.md`, `../../llm/10-post-v1-features.md`.

## Reading order

1. [`overview.md`](overview.md) - product idea, topology, non-goals
2. [`limitations.md`](limitations.md) - tile / DFF / I/O / PSRAM hard limits
3. [`system.md`](system.md) - I/O map, modes, memory/TCD, block map, MCU flow, open items
4. [`firmware.md`](firmware.md) - MicroPython demoboard firmware (D30): bus ownership, PIO SPI / `tCEM` chunking, TCD install, debug helpers, `firmware/tests`, M7 readiness; verbose twin [`../../llm/12-firmware.md`](../../llm/12-firmware.md)
5. [`blocks/`](blocks/) - per-block detail (expand as design hardens)
6. [`timing.md`](timing.md) - post-RTL timing checklist (Phase 3; PSRAM QSPI AC)
7. [`hardening.md`](hardening.md) - local LibreLane / Nix harden runbook + first area audits
8. [`post-v1.md`](post-v1.md) - add-later features (not V1)

## Blocks

| Block | Doc | Status |
|---|---|---|
| Top / host sync | [`blocks/host-interface.md`](blocks/host-interface.md) | Implemented: two-flop sync of MCU `START`/`BUS_REQ`; rising-edge qualify START |
| Integrated system controller | [`blocks/host-interface.md`](blocks/host-interface.md), [`blocks/descriptor-fsm.md`](blocks/descriptor-fsm.md) | Implemented in `sys_controller`: host/mode + fetch/read/write/update |
| Working registers | [`blocks/working-registers.md`](blocks/working-registers.md) | Implemented: 88 DFF TCD working set; device flags in `CTRL_FLAGS` |
| TCD format | [`blocks/tcd.md`](blocks/tcd.md) | Frozen + used by RTL/tests: 11-byte / device flags in `CTRL_FLAGS` + `QUIT` |
| QSPI engine | [`blocks/qspi-engine.md`](blocks/qspi-engine.md) | Implemented in `qspi_engine.sv`: QPI master + D21 handshake; A/B CS; no flash opcodes V1 |
| Byte ALU | [`blocks/alu.md`](blocks/alu.md) | **post-V1** stub |
| Ring / modulo | [`blocks/ring-buffer.md`](blocks/ring-buffer.md) | **post-V1** stub |

## How this folder grows

- Keep **overview / limitations / system** stable as the short map of the chip.
- Put new depth under **`blocks/`** (or add a new block file and a row in the table above).
- When a decision freezes, update the relevant human block/summary **and** the matching `docs/llm/` note. Human stays condensed but must state the decision; llm holds the long form. Do not grow overview into a second full design dump, and do not leave durable choices llm-only.
- Verification is a sibling documentation set under [`../verification/`](../verification/00-index.md), not an architecture block. Same parity rule: see [`../../README.md`](../../README.md).
- Optional later siblings (only when needed): `pins.md`, `clocking.md`, `errors.md`.
- Post-RTL timing checks live in [`timing.md`](timing.md) / [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md); extend those tables rather than growing block docs.

## Related

| Topic | Doc |
|---|---|
| Project one-pager | [`../overview.md`](../overview.md) |
| Roadmap | [`../roadmap.md`](../roadmap.md) |
| Firmware rules | [`firmware.md`](firmware.md) |
| Verification strategy and sign-off | [`../verification/`](../verification/00-index.md) |
| Timing analysis (post-RTL) | [`timing.md`](timing.md) |
| Local LibreLane harden | [`hardening.md`](hardening.md) |
| Post-V1 features | [`post-v1.md`](post-v1.md) |
| Open questions (detailed) | [`../../llm/08-open-questions.md`](../../llm/08-open-questions.md) |
| Bulk-mover use case | [`../../llm/06-system-use-case.md`](../../llm/06-system-use-case.md) |
| Decision log | [`../../llm/07-decision-log.md`](../../llm/07-decision-log.md) |
| References | [`../../llm/09-references.md`](../../llm/09-references.md) |
| APS6404L datasheet | [`../../datasheets/`](../../datasheets/) |
| TinyDMA-2C prior art | [`../../llm/prior-art/tinydma-2c.md`](../../llm/prior-art/tinydma-2c.md) |
