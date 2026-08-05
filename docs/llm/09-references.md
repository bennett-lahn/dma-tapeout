# References and External Material

## In-repo docs

- LLM context: `docs/llm/`
- Human docs: `docs/human/`

## Handwritten notes (read-only)

Path:

`C:\Users\lahnb\Documents\Obsidian Vault\Projects\Tiny Tapeout\`

Files of interest:

| File | Contents |
|---|---|
| `Project/Zero-Overhead Scatter-Gather DMA Engine.md` | Working architecture notes, FSM, registers, QSPI init questions |
| `Specifications.md` | Tile/DFF heuristics, PSRAM warnings, clock notes |
| `Useful Links.md` | TT PCB, demoboard firmware, QSPI PMOD links |
| `Ideas.md` | Early brainstorming and constraint reminders |

Do not modify these files from the repo agent workflow. If notes and repo docs diverge, update **repo docs** after confirming with the user, or ask.

## Tiny Tapeout / bring-up

- PCB specs: https://tinytapeout.com/specs/pcb/
- Demo PCB repo: https://github.com/TinyTapeout/tt-demo-pcb
- Demoboard MicroPython firmware: https://github.com/TinyTapeout/tt-micropython-firmware (local clone: `tt-micropython-firmware/`; project firmware contract: [`12-firmware.md`](12-firmware.md), D30)
- QSPI Flash/PSRAM PMOD reference: https://github.com/mole99/qspi-pmod
- TT QSPI PMOD configure/flash guide (in-repo): [`../datasheets/pdfs/Using_QSPI_TinyTapeout.pdf`](../datasheets/pdfs/Using_QSPI_TinyTapeout.pdf); ETR pin map + **`PIOSPI` / PSRAM SPI code catalog**: [`../datasheets/md/Using_QSPI_TinyTapeout.md`](../datasheets/md/Using_QSPI_TinyTapeout.md)
- **Shuttle / PDK (D27):** Tiny Tapeout **TTIHP26b**, PDK **`ihp-sg13g2`**
  - Project template: https://github.com/TinyTapeout/ttihp-verilog-template (local clone: `ttihp-verilog-template/`)
  - IHP Open PDK: https://github.com/IHP-GmbH/IHP-Open-PDK (local clone: `IHP-Open-PDK/`)
  - IO library docs: `IHP-Open-PDK/ihp-sg13g2/libs.ref/sg13g2_io/doc/` (typ/fast/slow PDFs; **no MHz toggle rating**)
  - IO liberty (typ): `.../libs.ref/sg13g2_io/lib/sg13g2_io_typ_1p2V_3p3V_25C.lib`
  - Process overview: `IHP-Open-PDK/ihp-sg13g2/libs.doc/doc/SG13G2_os_process_spec.pdf`
  - Mux / pad config (TTIHP26b): https://github.com/TinyTapeout/tt-multiplexer/tree/ttihp26b (`rtl/tt_ihp_gpio.v`, `ol2/tt_top/tt_ihp_wrapper.v`, `ol2/tt_top/signoff.sdc`)
  - Clock / GPIO pages on tinytapeout.com still describe **sky130** pad ratings; do not apply those MHz figures to IHP without re-deriving

## Prior art (separate context; do not copy)

**TinyDMA-2C** - Andrew Kim - TT submission 296

Full dump (pinout, config protocol, verification notes, warnings):

[`prior-art/tinydma-2c.md`](prior-art/tinydma-2c.md)

Rules when using it:

- Explicitly attribute ("Per TinyDMA-2C prior art...")
- Do not present as this project's frozen design
- Do not copy RTL / microarchitecture

High-level contrast only: static 2-channel SPI PSRAM DMA that barely fits 1x2; this repo targets original descriptor-based scatter-gather DMA instead.

## Planning conversation sources

User branched Gemini conversations while selecting the project:

- ASIC project ideas / DMA vs hash selection branch
- QSPI / PSRAM understanding branch

The durable conclusions of those chats are captured in `07-decision-log.md` and the architecture files. Prefer repo docs over re-fetching chat transcripts.

## Datasheets / standards

How to re-convert any PDF: [`../datasheets/README.md`](../datasheets/README.md)

In-repo APS6404L-3SQR (Rev 2.3) QSPI PSRAM:

- PDF: [`../datasheets/pdfs/APS6404L_3SQR.pdf`](../datasheets/pdfs/APS6404L_3SQR.pdf)
- Converted markdown: [`../datasheets/md/APS6404L_3SQR.md`](../datasheets/md/APS6404L_3SQR.md)

In-repo W25Q128JV (Rev I, 2021-08-23) QSPI flash (PMOD companion part):

- PDF: [`../datasheets/pdfs/W25Q128JV.pdf`](../datasheets/pdfs/W25Q128JV.pdf)
- Converted markdown: [`../datasheets/md/W25Q128JV.md`](../datasheets/md/W25Q128JV.md)
- Note: this JV (non-M) datasheet points DTR/QPI support to the separate **W25Q128JV-M** datasheet; JV itself is Standard/Dual/Quad SPI with QE, not true QPI (4-4-4 command phase).
- Project policy (D11): ASIC does **not** master flash in V1; MCU uses pass-through. ASIC flash read (maybe write) is super-stretch only.

Opcode / init requirements for the ASIC PSRAM path are summarized in `05-qspi-psram.md` (do not treat the full converted dumps as architecture).

Also:

- JEDEC JESD84-B50 only if sample-eye training is seriously considered (unlikely for V1)
- Tiny Tapeout hardened wrapper / pin constraints for the chosen shuttle (**TTIHP26b** / `tt_ihp_wrapper`; see D27)
