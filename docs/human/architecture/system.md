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
| `clk` | Design clock (RP2040-generated on demoboard) |
| `rst_n` | Active-low reset |
| `ui_in[0]` | **START** - accepted only while IDLE/`DONE`; ignored while busy |
| `ui_in[7:1]` | Reserved (head pointer / arm / **ABORT** / config - pin pack TBD) |

### Outputs: status (partial freeze)

| Port | Assignment |
|---|---|
| `uo_out[0]` | **DONE** - high whenever ASIC is idle (incl. after reset); pass-through on iff DONE |
| `uo_out[7:1]` | Reserved (error, DFT mux - TBD) |

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
- Null `NEXT_TCD` or abort (after current QPI txn) → idle again (DONE, pass-through)

## Memory layout and interfacing

External store is **two** APS6404L-class QSPI PSRAMs (byte-addressable) on the PMOD. The ASIC talks to them only through the QSPI engine with a CS mux; firmware programs contents during pass-through. Flash shares the bus but is **not** an ASIC DMA target in V1.

### Address model (V1 freeze)

| Rule | Detail |
|---|---|
| Internal pointers | **24-bit** (`SRC_PTR`, `DEST_PTR`, `NEXT_TCD`, head) |
| QSPI address phase | **`ptr[22:0]`** on the wire (device `A[22:0]`); `ptr[23]` unused / 0 |
| Device select | **`CTRL_FLAGS`**: `SRC_DEV` / `DEST_DEV` / `NEXT_DEV` (PSRAM 0 vs 1) |
| Null | **Address `0x000000` is reserved** - means null / end-of-chain / invalid link. Do not place a TCD or buffer at 0 |
| Window | Full device range usable by DMA (APS6404L: `A[22:0]` → 8 MB) per die |

**DFF cost:** working TCD metadata is **88 DFFs**, plus a 24-bit head.

### Logical memory map (firmware convention, not hardware-enforced)

Software owns placement within the device. Suggested layout for demos:

| Region (example) | Use |
|---|---|
| `0x000000` | **Reserved null** - never allocate |
| Low (e.g. after first TCD) | TCD linked lists |
| Mid | Source staging (firmware patterns) |
| High | Destinations / copy targets |

Hardware does not enforce region bounds; overlapping TCDs/buffers is a firmware bug. Stay within `0x000001`..`0x7FFFFF` on APS6404L-class parts; pick die via `CTRL_FLAGS`.

### TCD layout and behavior

Full field table: [`blocks/tcd.md`](blocks/tcd.md). Summary:

- Each TCD is an **11-byte** record in PSRAM (`CTRL_FLAGS` included)
- Fields: `SRC_PTR`, `DEST_PTR`, `TRANSFER_LEN`, `NEXT_TCD`, `CTRL_FLAGS`
- `TRANSFER_LEN == 0` is a no-op (follow next immediately)
- On START, engine fetches from the programmed head, byte-copies (QPI), then follows `NEXT_TCD` on `NEXT_DEV`
- `NEXT_TCD == 0x000000` ends the chain → IDLE / DONE
- Descriptor fetch uses a held-CE# **11-byte** burst

Post-V1 (ALU / cond-stop / ring / flash): [`post-v1.md`](post-v1.md).

## Major blocks

| Block | Job | Detail |
|---|---|---|
| Host / mode control | Pass-through vs DMA master, START/DONE/abort | [`blocks/host-interface.md`](blocks/host-interface.md) |
| Working regs | Active TCD only + 8-bit data hold | [`blocks/working-registers.md`](blocks/working-registers.md) |
| TCD format | 11-byte record in PSRAM | [`blocks/tcd.md`](blocks/tcd.md) |
| Descriptor FSM | Fetch TCD -> read byte -> write byte -> update/chain | [`blocks/descriptor-fsm.md`](blocks/descriptor-fsm.md) |
| QSPI engine | Init (SPI config), QPI data, CE# slicing, A/B CS | [`blocks/qspi-engine.md`](blocks/qspi-engine.md) |
| Byte ALU / ring | **Post-V1** stubs | [`blocks/alu.md`](blocks/alu.md), [`blocks/ring-buffer.md`](blocks/ring-buffer.md) |

## Data path mental model

```
PSRAM A/B --QPI--> RX hold --------> TX stage --QPI--> PSRAM A/B
                         ^
                         |
              CTRL_FLAGS SRC_DEV / DEST_DEV
```

Same-device or cross-device. Pure memcpy in V1. Next TCD die from `NEXT_DEV`.

## MCU setup flow

1. **Create TCDs** in PSRAM (linked list of **11-byte** records) while DONE / idle; never at address 0
2. Stage source data (firmware patterns) anywhere in the usable device range
3. Program head pointer / arm via host protocol (encoding TBD on `ui_in[7:1]`; lean head die = PSRAM 0)
4. High-Z MCU QSPI GPIOs; assert **START** while DONE
5. Wait for **DONE** again or assert **ABORT**
6. **Reading memory:** while DONE, MCU reclaims the bus and checks destinations

## Open architecture items

Tracked in detail at [`../../llm/08-open-questions.md`](../../llm/08-open-questions.md). Biggest remaining V1 gaps:

- Who initializes PSRAM (ASIC boot FSM vs MCU via pass-through; **both dies**) and whether init runs before pass-through enables
- Head / **ABORT** / status / DFT pin packing on unused `ui_in` / `uo_out`
- Head device at START (lean PSRAM 0); self-pointing descriptor policy vs abort
- Which QPI read opcode (`0x0B` vs `0xEB`)
- Clock target and RX sample edge

Settled for V1: **24-bit** pointers; **11-byte** TCD with device `CTRL_FLAGS`; zero-length no-op; address 0 is null; idle/START/DONE/abort/pass-through (D14); QPI data path (D15); `ui_in[0]=START`; `uo_out[0]=DONE`; QSPI on `uio` per table above; **dual PSRAM** DMA (incl. cross-device); **ASIC flash unsupported** (MCU pass-through only). Post-V1 ladder: [`post-v1.md`](post-v1.md).

## See also

- Idea / topology: [`overview.md`](overview.md)
- Limits: [`limitations.md`](limitations.md)
- Index: [`00-index.md`](00-index.md)
