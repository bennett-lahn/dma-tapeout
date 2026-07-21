# Architecture (Human)

Planning-level human docs for the Zero-Overhead Scatter-Gather DMA. V1 freezes START/DONE (= idle) pins, idle/abort/pass-through (D14), QSPI `uio` map (dual PSRAM; flash OE-off from ASIC), 24-bit/null address model, **11-byte TCD** with `CTRL_FLAGS` device bits (D13), QPI data path (D15). Remaining open: ABORT/head pin pack, clock, QPI read opcode. Post-V1: ALU → cond-stop → ring → flash.

Verbose agent context: `../../llm/03-architecture.md`, `../../llm/04-tcd-and-datapath.md`, `../../llm/05-qspi-psram.md`, `../../llm/10-post-v1-features.md`.

## Reading order

1. [`overview.md`](overview.md) - product idea, topology, non-goals
2. [`limitations.md`](limitations.md) - tile / DFF / I/O / PSRAM hard limits
3. [`system.md`](system.md) - I/O map, modes, memory/TCD, block map, MCU flow, open items
4. [`blocks/`](blocks/) - per-block detail (expand as design hardens)
5. [`post-v1.md`](post-v1.md) - add-later features (not V1)

## Blocks

| Block | Doc | Status |
|---|---|---|
| Host / mode control | [`blocks/host-interface.md`](blocks/host-interface.md) | OE phases + START/DONE; dual RAM CS; flash OE-off |
| Working registers | [`blocks/working-registers.md`](blocks/working-registers.md) | 88 DFF TCD working set |
| TCD format | [`blocks/tcd.md`](blocks/tcd.md) | 11-byte / 24-bit + `CTRL_FLAGS` device bits |
| Descriptor FSM | [`blocks/descriptor-fsm.md`](blocks/descriptor-fsm.md) | fetch/read/write/update (no PROCESS) |
| QSPI engine | [`blocks/qspi-engine.md`](blocks/qspi-engine.md) | skeleton + A/B CS mux; no flash opcodes V1 |
| Byte ALU | [`blocks/alu.md`](blocks/alu.md) | **post-V1** stub |
| Ring / modulo | [`blocks/ring-buffer.md`](blocks/ring-buffer.md) | **post-V1** stub |

## How this folder grows

- Keep **overview / limitations / system** stable as the short map of the chip.
- Put new depth under **`blocks/`** (or add a new block file and a row in the table above).
- When a decision freezes, update the relevant block file and the matching `docs/llm/` note; do not grow overview into a second full design dump.
- Optional later siblings (only when needed): `pins.md`, `clocking.md`, `errors.md`, `verification.md`.

## Related

| Topic | Doc |
|---|---|
| Project one-pager | [`../overview.md`](../overview.md) |
| Roadmap | [`../roadmap.md`](../roadmap.md) |
| Post-V1 features | [`post-v1.md`](post-v1.md) |
| Open questions (detailed) | [`../../llm/08-open-questions.md`](../../llm/08-open-questions.md) |
| Bulk-mover use case | [`../../llm/06-system-use-case.md`](../../llm/06-system-use-case.md) |
| Decision log | [`../../llm/07-decision-log.md`](../../llm/07-decision-log.md) |
| References | [`../../llm/09-references.md`](../../llm/09-references.md) |
| APS6404L datasheet | [`../../datasheets/`](../../datasheets/) |
| TinyDMA-2C prior art | [`../../llm/prior-art/tinydma-2c.md`](../../llm/prior-art/tinydma-2c.md) |
