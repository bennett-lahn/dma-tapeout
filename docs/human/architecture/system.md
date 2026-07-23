# Architecture Overview

Block-level map of the chip. Per-block detail lives under [`blocks/`](blocks/).

## I/O map (Tiny Tapeout)

Tiny Tapeout exposes **10 inputs**, **8 bidirectional**, and **8 outputs** to each design:

| Group | Count | TT ports | This project |
|---|---|---|---|
| Inputs | 10 | `clk`, `rst_n`, `ui_in[7:0]` | Clock, reset, host control (incl. **START**) |
| Bidirectional | 8 | `uio[7:0]` (`uio_in` / `uio_out` / `uio_oe`) | QSPI to PSRAM PMOD (shared with MCU) |
| Outputs | 8 | `uo_out[7:0]` | Status (incl. **DONE**), future DFT/debug |

### Bidirectional: QSPI (current)

Aligned with the TT community QSPI flash+PSRAM PMOD map. V1 DMA uses **both RAM A and RAM B**; flash is MCU pass-through only.

| `uio` | Signal | ASIC role when DMA master |
|---|---|---|
| 0 | Flash CS | **OE always off** - never selected by ASIC; MCU may use flash in pass-through |
| 1 | SD0 / MOSI | SIO0 - drive or listen per QSPI phase |
| 2 | SD1 / MISO | SIO1 - drive or listen per QSPI phase |
| 3 | SCK | Drive while master |
| 4 | SD2 | SIO2 - drive or listen per QSPI phase |
| 5 | SD3 | SIO3 - drive or listen per QSPI phase |
| 6 | RAM A CS | Drive while master when txn targets PSRAM A |
| 7 | RAM B CS | Drive while master when txn targets PSRAM B |

Only one PSRAM CE# low per transaction (shared SIO). Cross-device = read then write with CS switch. Pass-through / OE rules: [`blocks/host-interface.md`](blocks/host-interface.md).

### Inputs: host control (partial freeze)

| Port | Assignment |
|---|---|
| `clk` | Design clock **84 MHz** target (RP2040-generated on demoboard; D16) |
| `rst_n` | Active-low reset |
| `ui_in[0]` | **START** - accepted only while IDLE/`DONE`; ignored while busy |
| `ui_in[1]` | **ABORT** - finish current QPI txn → IDLE |
| `ui_in[7:2]` | Reserved (config / DFT - packing open) |

### Outputs: status (partial freeze)

| Port | Assignment |
|---|---|
| `uo_out[0]` | **DONE** - high whenever ASIC is idle (incl. after reset); pass-through on iff DONE |
| `uo_out[7:1]` | Reserved (error, DFT mux - packing open) |

## Runtime modes (bus ownership)

The ASIC and MCU cannot both freely drive the same QSPI pins.

Pass-through is **shared-bus OE arbitration** on `uio`: idle/`DONE` ⇒ ASIC `uio_oe=0` (MCU may drive); not DONE ⇒ ASIC masters. Not a separate MCU-to-PSRAM pin proxy. Protocol detail: [`blocks/host-interface.md`](blocks/host-interface.md) (D14).

### Mode A - Idle / MCU pass-through (programming)

- DONE high; ASIC holds QSPI `uio_oe = 0`
- MCU drives the shared `uio` QSPI nets to **PSRAM A, PSRAM B, and/or flash**
- MCU writes TCD chains and payload data into PSRAM
- MCU may read results back whenever DONE is high

### Mode B - DMA master (execution)

- MCU releases its QSPI GPIOs, then asserts **START** while DONE is high
- ASIC leaves idle (DONE low); seizes bus (`uio_oe` for SCK + active RAM CS; flash CS OE-off; SIO OE follows QPI phase)
- Descriptor engine runs across RAM A and/or B (QPI data path)
- Stop TCD (both devices selected) or abort (after current QPI txn) → idle again (DONE, pass-through)

## Memory layout and interfacing

External store is **two** APS6404L-class QSPI PSRAMs (byte-addressable) on the PMOD. The ASIC talks to them only through the QSPI engine with a CS mux; firmware programs contents during pass-through. Flash shares the bus but is **not** an ASIC DMA target in V1.

### Address model (V1 freeze)

| Rule | Detail |
|---|---|
| Internal pointers | **24-bit** (`SRC_PTR`, `DEST_PTR`, `NEXT_TCD`) - no head register |
| QSPI address phase | **`ptr[22:0]`** on the wire (device `A[22:0]`) |
| Device select | **`ptr[23]`** (`0`=PSRAM 0, `1`=PSRAM 1) |
| Fixed head | START always fetches **`0x000000` on PSRAM 0** |
| End of chain | **`CTRL_FLAGS.QUIT=1`** → IDLE / DONE (no execute) |
| Window | Full device range usable by DMA (APS6404L: `A[22:0]` → 8 MB) per die |

**DFF cost:** working TCD metadata is **88 DFFs** (no head pointer).

### Logical memory map (firmware convention, not hardware-enforced)

Software owns placement within the device. Suggested layout for demos:

| Region (example) | Use |
|---|---|
| `0x000000` on PSRAM 0 | **Fixed head** - first TCD (or `QUIT` TCD for empty run) |
| Low (after head / chain) | Further TCDs |
| Mid | Source staging (firmware patterns) |
| High | Destinations / copy targets |

Hardware does not enforce region bounds; overlapping TCDs/buffers is a firmware bug. Stay within `0x000000`..`0x7FFFFF` on APS6404L-class parts; pick die via `ptr[23]`. Address 0 is allowed for TCDs/buffers.

### TCD layout and behavior

Full field table: [`blocks/tcd.md`](blocks/tcd.md). Summary:

- Each TCD is an **11-byte** record in PSRAM (`CTRL_FLAGS` included)
- Fields: `SRC_PTR`, `DEST_PTR`, `TRANSFER_LEN`, `NEXT_TCD`, `CTRL_FLAGS`
- `TRANSFER_LEN == 0` is a no-op (follow next immediately)
- On START, engine fetches `0x000000` / PSRAM 0, byte-copies (QPI), then follows `NEXT_TCD` (die from bit 23)
- `QUIT=1` ends the chain → IDLE / DONE
- Descriptor fetch uses a held-CE# **11-byte** burst

Post-V1 (ALU / cond-stop / ring / flash): [`post-v1.md`](post-v1.md).

## Major blocks

| Block | Job | Detail |
|---|---|---|
| Host / mode control | Pass-through vs DMA master, START/DONE/abort | [`blocks/host-interface.md`](blocks/host-interface.md) |
| Working regs | Active TCD only + **1-byte** data hold (depth-agnostic; D20) | [`blocks/working-registers.md`](blocks/working-registers.md) |
| TCD format | 11-byte record in PSRAM | [`blocks/tcd.md`](blocks/tcd.md) |
| Descriptor FSM | Fetch TCD -> read → write → update/chain | [`blocks/descriptor-fsm.md`](blocks/descriptor-fsm.md) |
| QSPI engine | QPI `0xEB`/`0x02`, CE# slicing, A/B CS (MCU enter/exit QPI) | [`blocks/qspi-engine.md`](blocks/qspi-engine.md) |
| Byte ALU / ring | **Post-V1** stubs | [`blocks/alu.md`](blocks/alu.md), [`blocks/ring-buffer.md`](blocks/ring-buffer.md) |

## Data path mental model

```
PSRAM A/B --QPI--> RX hold --------> TX stage --QPI--> PSRAM A/B
                         ^
                         |
              SRC_PTR[23] / DEST_PTR[23]
```

Same-device or cross-device. Pure memcpy in V1. Next TCD die from `NEXT_TCD[23]`. RX hold is **1 byte** for V1; engine correctness must not assume that depth (D20).

## MCU setup flow

1. **PSRAM bring-up over SPI** (pass-through while DONE): on each die DMA will touch, wait `tPU`, issue Reset Enable (`0x66`) then Reset (`0x99`), then Enter Quad (`0x35`). ASIC expects both dies already in **QPI** before START (D17); this work is MCU-only
2. **Create TCDs** in PSRAM while DONE / idle; first TCD (or `QUIT` TCD) at `0x000000` on PSRAM 0; end chain with `QUIT=1`
3. Stage source data (firmware patterns) anywhere in the usable device range
4. High-Z MCU QSPI GPIOs; assert **START** (`ui_in[0]`) while DONE
5. Wait for **DONE** again or assert **ABORT** (`ui_in[1]`)
6. **Reading memory / exit QPI:** while DONE, MCU reclaims the bus, checks destinations, and may Exit Quad (`0xF5`) or reset if firmware needs SPI again

## Open architecture items

Tracked in detail at [`../../llm/08-open-questions.md`](../../llm/08-open-questions.md). Biggest remaining V1 gaps:

- Status / DFT packing on `uo_out[7:1]` (and optional `ui_in[7:2]`)
- Self-pointing descriptor policy vs abort

Settled for V1: **24-bit** pointers with **`ptr[23]` device**; **11-byte** TCD with **`QUIT`** flag; fixed head at 0/PSRAM0; zero-length no-op; idle/START/ABORT/DONE/pass-through (D14/D18/D19); QPI data `0xEB`/`0x02` (D15/D17); **MCU** enter/exit QPI (D17); **84 MHz** rising-edge RX (D16); **1-byte** data buffer, depth-agnostic (D20); `ui_in[0]=START`, `ui_in[1]=ABORT`, `uo_out[0]=DONE`; QSPI on `uio`; **dual PSRAM** DMA; **ASIC flash unsupported**. Post-V1 ladder: [`post-v1.md`](post-v1.md).

## See also

- Idea / topology: [`overview.md`](overview.md)
- Limits: [`limitations.md`](limitations.md)
- Index: [`00-index.md`](00-index.md)
