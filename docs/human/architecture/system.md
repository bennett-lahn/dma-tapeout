# Architecture Overview

Block-level map of the chip. Per-block detail lives under [`blocks/`](blocks/).

## I/O map (Tiny Tapeout)

Tiny Tapeout exposes **10 inputs**, **8 bidirectional**, and **8 outputs** to each design:

| Group | Count | TT ports | TinyDMA |
|---|---|---|---|
| Inputs | 10 | `clk`, `rst_n`, `ui_in[7:0]` | Clock, reset, host control (incl. **START**) |
| Bidirectional | 8 | `uio[7:0]` (`uio_in` / `uio_out` / `uio_oe`) | QSPI to PSRAM PMOD (shared with MCU) |
| Outputs | 8 | `uo_out[7:0]` | **DONE**, **BUS_GNT**; unused `[7:2]` tied 0 (D34) |

### Bidirectional: QSPI (current)

Aligned with the TT community QSPI flash+PSRAM PMOD map. V1 DMA uses **both RAM A and RAM B**; flash is MCU pass-through only.

| `uio` | Signal | ASIC role when DMA master / bus keeper (`~BUS_GNT`) |
|---|---|---|
| 0 | Flash CS | **Park high** - never selected (never driven low); MCU may use flash under `BUS_GNT` |
| 1 | SD0 / MOSI | SIO0 - drive or listen per QSPI phase |
| 2 | SD1 / MISO | SIO1 - drive or listen per QSPI phase |
| 3 | SCK | Drive low when parked; toggle while txn live |
| 4 | SD2 | SIO2 - drive or listen per QSPI phase |
| 5 | SD3 | SIO3 - drive or listen per QSPI phase |
| 6 | RAM A CS | Park high when idle/other die; drive low only for PSRAM A txns |
| 7 | RAM B CS | Park high when idle/other die; drive low only for PSRAM B txns |

Only one PSRAM CE# low per transaction (shared SIO). Cross-device = read then write with CS switch. While `~BUS_GNT`, ASIC is the bus keeper (D26): CS high / SCK low between txns and in IDLE; SIO drives a don't-care in park after `tHZ`, and floats for dummy/read (and through `tHZ`). Board has **10 kΩ** pull-ups on each CS. Ownership matrix: [`blocks/host-interface.md`](blocks/host-interface.md); firmware: [`firmware.md`](firmware.md).

### Inputs: host control (partial freeze)

| Port | Assignment |
|---|---|
| `clk` | Design clock **66 MHz** target (RP2040-generated on demoboard; D16); QSPI engine SCK = clk/2 |
| `rst_n` | Active-low reset |
| `ui_in[0]` | **START** - synchronized and rising-edge detected by the top level; resulting one-`clk` pulse accepted only while IDLE/`DONE` and `~BUS_REQ`; otherwise ignored and not queued (resent after `BUS_GNT` low). After START, wait for DONE low before raising `BUS_REQ` again |
| `ui_in[1]` | Unused (tied 0; D34) |
| `ui_in[2]` | **BUS_REQ** - MCU wants bidirectional `uio` (D22) |
| `ui_in[7:3]` | Unused (tied 0; D34) |

### Outputs: status (partial freeze)

| Port | Assignment |
|---|---|
| `uo_out[0]` | **DONE** - high whenever ASIC is idle (incl. after reset) |
| `uo_out[1]` | **BUS_GNT** - MCU may drive `uio` (D22) |
| `uo_out[7:2]` | Unused (tied 0; D34) |

## Runtime modes (bus ownership)

The ASIC and MCU cannot both freely drive the same QSPI pins.

Pass-through is **shared-bus OE arbitration** on `uio` with an explicit **request/grant** (`BUS_REQ` / `BUS_GNT`, D22): MCU drives while `BUS_GNT=1` or `rst_n=0` (D26); ASIC yields after the current QPI txn (atomic) when requested. Not a separate MCU-to-PSRAM pin proxy. Protocol detail: [`blocks/host-interface.md`](blocks/host-interface.md); firmware: [`firmware.md`](firmware.md).

### Mode A - MCU pass-through (programming)

- MCU asserts `BUS_REQ`, waits for `BUS_GNT`; ASIC **releases** QSPI `uio_oe = 0`
- MCU drives the shared `uio` QSPI nets to **PSRAM A, PSRAM B, and/or flash**
- MCU writes TCD chains and payload data into PSRAM
- Mid-DMA: same handshake pauses between atomic QPI txns (MCU priority); DMA resumes when REQ drops

### Mode B - DMA master (execution)

- MCU Hi-Zs QSPI GPIOs, drops `BUS_REQ`, waits for `BUS_GNT` low, then asserts **START** while DONE is high
- ASIC leaves idle (DONE low); remains bus keeper / DMA master (`uio_oe` parks CS high / SCK low; flash CS never low; SIO OE follows QPI phase)
- Descriptor engine runs across RAM A and/or B (QPI data path)
- Quit TCD → idle again (DONE) with parking; next START fetches fixed head at `0x000000` / PSRAM 0
- Kill mid-run with `rst_n` (no soft abort; D23); board 10 kΩ CS pull-ups hold CE# during reset

## Memory layout and interfacing

External store is **two** APS6404L-class QSPI PSRAMs (byte-addressable) on the PMOD. The ASIC talks to them only through the QSPI engine with a CS mux; firmware programs contents during pass-through. Flash shares the bus but is **not** an ASIC DMA target in V1.

### Address model (V1 freeze)

| Rule | Detail |
|---|---|
| Internal pointers | **24-bit** (`SRC_PTR`, `DEST_PTR`, `NEXT_TCD`) - no head register |
| QSPI address phase | **24 bits** on the wire; device uses `A[22:0]` from `ptr[22:0]`; phase MSB unused; device from `CTRL_FLAGS` |
| Device select | **`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** in `CTRL_FLAGS` (D24) |
| Fixed head | START always fetches **`0x000000` on PSRAM 0** |
| End of chain | **`CTRL_FLAGS.QUIT=1`** → IDLE / DONE (no execute); next START from fixed head |
| Window | Full device range usable by DMA (APS6404L: `A[22:0]` → 8 MB) per device |

**DFF cost:** working TCD metadata is **88 DFFs** (full 11-byte memory TCD, including reserved `[3:0]`; no head pointer).

### Logical memory map (firmware convention, not hardware-enforced)

Software owns placement within the device. Suggested layout for demos:

| Region (example) | Use |
|---|---|
| `0x000000` on PSRAM 0 | **Fixed head** - first TCD (or `QUIT` TCD for empty run) |
| Low (after head / chain) | Further TCDs |
| Mid | Source staging (firmware patterns) |
| High | Destinations / copy targets |

Hardware does not enforce region bounds; overlapping TCDs/buffers is a firmware bug. Stay within `0x000000`..`0x7FFFFF` on `A[22:0]` / APS6404L-class parts (`ptr[23]` don't-care; D35); pick device via `SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`. Address 0 is allowed for TCDs/buffers. Self-pointing / cyclic `NEXT_TCD` is allowed and spins until `rst_n` without `QUIT` (D35).

### TCD layout and behavior

Full field table: [`blocks/tcd.md`](blocks/tcd.md). Summary:

- Each TCD is an **11-byte** record in PSRAM (`CTRL_FLAGS` included)
- Fields: `SRC_PTR`, `DEST_PTR`, `TRANSFER_LEN`, `NEXT_TCD`, `CTRL_FLAGS`
- `TRANSFER_LEN == 0` is a no-op (follow next immediately)
- On START, engine fetches `0x000000` / PSRAM 0, byte-copies (QPI), then follows `NEXT_TCD` on `NEXT_DEVICE`
- `QUIT=1` ends the chain → IDLE / DONE; a later START always re-fetches `0x000000` / PSRAM 0
- Descriptor fetch uses a held-CE# **11-byte** burst

## Major blocks

| Block | Job | Detail |
|---|---|---|
| Top / host sync | TT entry; synchronize async MCU inputs, rising-edge detect START into a one-`clk` pulse, and retain BUS_REQ as a level | [`blocks/host-interface.md`](blocks/host-interface.md) (sync) |
| Integrated system controller | Host/mode control plus descriptor FSM: START/DONE, BUS_REQ/BUS_GNT, fetch, read, write, update, and chain sequencing; kill via `rst_n` | [`blocks/host-interface.md`](blocks/host-interface.md), [`blocks/descriptor-fsm.md`](blocks/descriptor-fsm.md) |
| Working regs | Active TCD only + **1-byte** data hold (depth-agnostic; D20) | [`blocks/working-registers.md`](blocks/working-registers.md) |
| TCD format | 11-byte record in PSRAM | [`blocks/tcd.md`](blocks/tcd.md) |
| QSPI engine | QPI `0xEB`/`0x02`, A/B CS; SCK=clk/2; D21 `~busy` / `wdata_next` (no `txn_ready`/`wdone`) | [`blocks/qspi-engine.md`](blocks/qspi-engine.md) |

## Data path mental model

```
PSRAM A/B --QPI--> RX hold --------> TX stage --QPI--> PSRAM A/B
                         ^
                         |
         CTRL_FLAGS SRC_DEVICE / DEST_DEVICE
```

Same-device or cross-device. Pure memcpy. Devices from `SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE` (D24; not pointer MSBs). RX hold is **1 byte**; engine correctness must not assume that depth (D20).

## MCU setup flow

1. **PSRAM bring-up over SPI** (`BUS_REQ`/`BUS_GNT`): on each device DMA will touch, wait `tPU`, issue Reset Enable (`0x66`) then Reset (`0x99`), then Enter Quad (`0x35`). ASIC expects both devices already in **QPI** before START (D17); this work is MCU-only
2. **Create TCDs** in PSRAM under grant; first TCD (or `QUIT` TCD) at `0x000000` on PSRAM 0; end chain with `QUIT=1`; link via `NEXT_TCD` + `NEXT_DEVICE`
3. Stage source data (firmware patterns) anywhere in the usable device range
4. High-Z MCU QSPI GPIOs; drop **BUS_REQ**; wait for **BUS_GNT** low; assert **START** (`ui_in[0]`) while DONE
5. Wait for **DONE** again, pause mid-run with **BUS_REQ**, or assert **`rst_n`** to kill (D23)
6. **Reading memory / exit QPI:** assert `BUS_REQ`, wait for `BUS_GNT`, check destinations, Exit Quad (`0xF5`) or reset if firmware needs SPI again
7. After `QUIT` → IDLE, the next **START** always begins at **`0x000000` / PSRAM 0** again

## Open architecture items

Tracked in detail at [`../../llm/08-open-questions.md`](../../llm/08-open-questions.md). Remaining V1 gap:

- Multi-outstanding transactions (lean: no)

Settled: **24-bit** address pointers with **`ptr[23]` don't-care** (D35); device selects in **`CTRL_FLAGS`**; **11-byte** TCD with **`QUIT`** flag; self-pointing / cyclic chains allowed (spin until `rst_n`; D35); fixed head at 0/PSRAM0; zero-length no-op; idle/START/DONE/BUS_REQ/BUS_GNT; kill via **`rst_n`** (D23); unused `ui_in[1]`, `ui_in[7:3]`, `uo_out[7:2]` tied 0, no ERROR logic (D34); **BUS_REQ/BUS_GNT** pass-through (D22); **ASIC bus keeper** (D26); QPI data `0xEB`/`0x02`; **MCU** enter/exit QPI (D17); **66 MHz `clk`**, **SCK = clk/2**, rising-edge RX (D16); D21 handshake; **`DMA_BUF_DEPTH=5`** tapeout (D20); QSPI on `uio`; **dual PSRAM** DMA; **ASIC flash unsupported**. Shipped RTL is this feature set only. Formal M4 (`FP-*`) is not a V1 freeze gate (D33).

## See also

- Idea / topology: [`overview.md`](overview.md)
- Limits: [`limitations.md`](limitations.md)
- Index: [`00-index.md`](00-index.md)
