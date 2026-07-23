# Architecture Overview

Status: planning-level. V1 freezes: QSPI on `uio`, `ui_in[0]=START`, `ui_in[1]=ABORT`, `uo_out[0]=DONE` (= idle), idle/START/abort/pass-through (D14/D18), **24-bit** pointers with **`ptr[23]` device select** + **`QUIT`** end-of-chain (D19), fixed head at `0x000000`/PSRAM0 (D18), **11-byte** TCD (D19), **QPI data path** `0xEB`/`0x02` (D15/D17), MCU-owned enter/exit QPI (D17), **84 MHz** clock + rising-edge RX sample (D16), **1-byte** data buffer with depth-agnostic correctness (D20), **dual PSRAM** DMA (incl. cross-device), **ASIC flash unsupported** (MCU pass-through only). ALU / cond-stop / ring / ASIC flash are **post-V1** (`10-post-v1-features.md`). Remaining open: `uo_out[7:1]` status pack.

## Product framing

Working title: **Zero-Overhead Scatter-Gather DMA Engine**.

V1 target: an **isolated descriptor DMA / bulk mover** between the two QSPI PSRAM dies on the TT PMOD - learning vehicle and resume artifact. ADC / live telemetry integration is **unlikely** for the first shuttle; those behaviors are sketched under post-V1 features.

Core ideas:

- Memory-management coprocessor for **bulk byte moves**
- **Zero overhead:** TCDs embedded in RAM, not a multi-channel on-chip register file
- **Scatter-gather:** TCD -> next TCD linked lists tolerate arbitrary fragmentation
- **Dual-die:** same-device and cross-device (A↔B) copies on a shared QSPI bus

## System topology

```
[ RP2 MCU ] ---- host control (START/ABORT/DONE) ----> [ DMA ASIC (TT) ] <---- QSPI ----> [ PSRAM A + PSRAM B ]
                                                                              (shared bus; flash via MCU pass-through only)
```

Roles:

- **MCU**: builds TCD linked lists, programs either PSRAM (while ASIC is in pass-through / idle), may also talk to **flash** on the same PMOD via pass-through, stages source buffers, pulses START, handles DONE/abort.
- **DMA ASIC**: after START, masters the QSPI bus, fetches descriptors, **byte-copies** across **RAM A and/or RAM B** (including cross-device), chains TCDs, returns bus / asserts completion. Does **not** assert flash CS in V1. No in-flight ALU in V1.
- **PSRAM A/B**: store TCDs, source buffers, destination buffers. Either die may be src and/or dest.
- **Flash (PMOD)**: MCU-only via pass-through in V1. ASIC flash read/write is post-V1 / last on the add-later ladder.

## I/O map (Tiny Tapeout)

TT provides **10 inputs** (`clk`, `rst_n`, `ui_in[7:0]`), **8 bidirectional** (`uio[7:0]`), and **8 outputs** (`uo_out[7:0]`).

### Bidirectional - QSPI (V1)

| `uio` | Signal | V1 use |
|---|---|---|
| 0 | Flash CS | **ASIC OE always off** - MCU may master flash in pass-through; ASIC never selects flash in V1 |
| 1 | SD0 / MOSI | SIO0 |
| 2 | SD1 / MISO | SIO1 |
| 3 | SCK | Clock |
| 4 | SD2 | SIO2 |
| 5 | SD3 | SIO3 |
| 6 | RAM A CS | PSRAM A CE# (DMA endpoint) |
| 7 | RAM B CS | PSRAM B CE# (DMA endpoint) |

Shared SIO/SCK: only **one** PSRAM CE# may be low per transaction. Cross-device copies are read-then-write with CS switched between beats.

### Dedicated host strobes (V1 freeze)

| Port | Assignment |
|---|---|
| `ui_in[0]` | **START** (MCU → ASIC; accepted only in IDLE) |
| `ui_in[1]` | **ABORT** (finish current QPI txn → IDLE) |
| `uo_out[0]` | **DONE** (ASIC → MCU; high whenever IDLE) |
| `ui_in[7:2]`, `uo_out[7:1]` | Reserved (status/DFT - packing open) |

Protocol: DONE ⇔ idle ⇔ pass-through; START ignored while busy; abort finishes current QPI txn then idle (D14); fixed head (D18) + `QUIT` TCD (D19). Human summary: `docs/human/architecture/system.md` (I/O section). OE phases: `docs/human/architecture/blocks/host-interface.md`.

## Memory layout and interfacing

### Address model (V1 freeze)

- Internal pointers are **24-bit** (`SRC_PTR`, `DEST_PTR`, `NEXT_TCD`). No programmed head register.
- QSPI address phase uses **`ptr[22:0]`** (device `A[22:0]`); **`ptr[23]` selects die** (D19).
- Device select: **`ptr[23]`** on each pointer (`0`=PSRAM 0, `1`=PSRAM 1).
- **Fixed head:** START always fetches TCD at **`0x000000` on PSRAM 0**. Address 0 is a valid TCD/buffer location.
- **End of chain:** fetched TCD with **`CTRL_FLAGS.QUIT=1`** → IDLE / DONE (D19).
- Full APS6404L range is DMA-reachable (`A[22:0]`, 8 MB) per die.
- **DFF cost:** working TCD metadata **88 DFFs** (24+24+8+24+8); no 24-bit head.

### Firmware map (convention)

Software places the first TCD at `0x000000` on PSRAM 0 (or a `QUIT` TCD for an empty run). Other TCDs/buffers anywhere in the usable range on either die (die in pointer MSB). Hardware does not enforce regions.

### TCD

**11-byte** record: `SRC_PTR`, `DEST_PTR`, `TRANSFER_LEN`, `NEXT_TCD`, `CTRL_FLAGS`. Detail: `04-tcd-and-datapath.md` and `docs/human/architecture/blocks/tcd.md`. `TRANSFER_LEN == 0` is a no-op. Chain ends on `QUIT=1` → IDLE / DONE.

## Critical system bottleneck: bus ownership

The ASIC and MCU cannot both freely drive the same QSPI pins. Architecture therefore needs explicit modes.

### Tiny Tapeout pin model (bidirectional)

Dedicated ports: `ui_in[7:0]` (input only), `uo_out[7:0]` (output only). Bidirectional `uio[7:0]` appear in RTL as three buses per physical pin:

| RTL | Dir | Role |
|---|---|---|
| `uio_in` | in | Pad sense |
| `uio_out` | out | Drive value when enabled |
| `uio_oe` | out | `1` = drive; `0` = high-Z (listen) |

`uio_oe = 0` disables the ASIC output driver only; the pad is not removed from the net (input path and parasitics remain).

On the demoboard, RP2040 GPIOs, ASIC `uio`, and the QSPI PMOD share those nets. **Pass-through is OE arbitration on a shared bus**, not a gate-level proxy that copies MCU QSPI from `ui_in` onto a separate PSRAM port. The MCU has its own GPIO OE; TT `uio_oe` does not control the RP2040. Both masters enabled on one net is contention (undefined levels, pad stress). Handoffs must **release before seize**.

Human-facing phase tables and conceptual RTL: `docs/human/architecture/blocks/host-interface.md`.

### Mode A - Idle / MCU pass-through (programming)

- DMA is idle: **DONE** high; pass-through on.
- ASIC holds `uio_oe = 0` on all QSPI pins (CS including flash, SCK, SIO).
- MCU firmware drives the shared `uio` nets as QSPI master to **PSRAM A, PSRAM B, and/or flash**.
- MCU writes TCD chains and payload data into PSRAM; after DMA, MCU reclaims the bus whenever DONE is high again.

### Mode B - DMA master (execution)

- MCU finishes any QSPI txn, high-Zs its QSPI GPIOs, then asserts START (`ui_in[0]`) while DONE is high.
- ASIC leaves idle (DONE low): raises `uio_oe` for SCK and the **active RAM CS** (A and/or B over the run; never flash); SIO OE follows QSPI phase (drive for cmd/addr/write; float for dummy/read while sampling `uio_in`).
- START ignored until idle returns. On quit TCD or abort completion (finish current QPI txn), return to idle: clear `uio_oe`, assert DONE (D14/D18/D19).

This boundary is as important as the internal FSM. Without a clean programming path, descriptor DMA is undemoable.

## Major blocks

### 1. Host interface / mode control

Responsibilities:

- Detect START (`ui_in[0]`) and ABORT (`ui_in[1]`)
- Drive status (done, error, debug mux on remaining `uo_out`)
- Own mode switch between pass-through and DMA master by gating `uio_oe` (idle: all clear; active: engine-driven per pin/phase)

Frozen: `ui_in[0]=START`, `ui_in[1]=ABORT`, `uo_out[0]=DONE` (D14/D18); no head-pointer pins. Per TinyDMA-2C prior art, that design used `ui_in` + `uio` strobes with a command/payload config adapter and a **fixed** SPI `uio_oe` mask; this project needs dynamic SIO OE for QSPI and a shared-bus pass-through model instead. Same I/O scarcity applies.

I/O principles (still binding):

1. Serialize host interfaces; do not assume wide parallel buses
2. Reserve at least one muxed DFT/debug output for FSM observation after tapeout
3. Verification must cover edge cases that cannot be probed on silicon (including double-drive / host drives while not DONE)
4. Default and idle: `uio_oe = 0`; never enable ASIC QSPI drivers until MCU has released the bus

### 2. Working-state register file (explicit, DFF-critical)

Only the **currently executing TCD** is resident on-chip. Planned fields:

| Field | Width | Role |
|---|---|---|
| `SRC_PTR` | 24 | Source byte address |
| `DEST_PTR` | 24 | Destination byte address |
| `TRANSFER_LEN` | 8 | Bytes remaining (0 = no-op) |
| `NEXT_TCD` | 24 | Next descriptor address (any address incl. 0 is a normal link) |
| `CTRL_FLAGS` | 8 | `QUIT` (bit 0) + reserved `[7:1]` |

Approximate working metadata: **88 DFFs**, plus at least:

- **Data buffer / holding register** between read and write (**1 byte / 8 DFFs for V1**; D20)
- FSM state flops
- QSPI shifter / bit counters / CE# timing counters
- Error sticky bits (no head pointer)

**Buffer depth (D20):** V1 uses `N=1`. FSM / QSPI sequencing must treat `N` as a parameter: correctness (TCD semantics, pointer/`TRANSFER_LEN` updates, CE# refresh slicing, single-CS cross-device) must not depend on a specific buffer length. Deepening the scratch later is a performance/DFF trade only.

### 3. Dataflow FSM (descriptor engine)

Planned conceptual states:

1. `IDLE` - DONE high; pass-through enabled; wait for START (ignored elsewhere).
2. `STATE_FETCH` - burst-read **11 bytes** into working registers. First fetch: `0x000000` / PSRAM 0; later: `NEXT_TCD` (die from bit 23). If `QUIT=1` → `IDLE`.
3. `STATE_READ` - read up to buffer depth `N` bytes from `SRC_PTR` into the data buffer (V1: `N=1`; skip if length 0).
4. `STATE_WRITE` - write the buffered bytes to `DEST_PTR`.
5. `STATE_UPDATE` - decrement `TRANSFER_LEN` by bytes moved; increment SRC/DEST address bits by the same count (keep die MSB); if `TRANSFER_LEN > 0`, loop to `STATE_READ`; if length is 0, loop to `STATE_FETCH` for `NEXT_TCD` (die from bit 23).

Notes from planning:

- `STATE_UPDATE` may fold into `STATE_WRITE` to save states.
- Descriptor fetch should use a **held-CE# burst** so command+address overhead is not paid per TCD byte.
- Data moves are QPI byte-oriented in V1 (D15); CE# refresh slicing remains mandatory for long transfers.
- Buffer depth `N=1` for V1; do not bake `N` into correctness assumptions (D20).
- Abort: finish current QPI txn, then IDLE.
- No `STATE_PROCESS` in V1 (ALU / cond-stop are post-V1).

### 4. Post-V1 blocks (not in V1 silicon plan)

ALU, conditional stop, ring/modulo addressing, and ASIC flash R/W are documented in [`10-post-v1-features.md`](10-post-v1-features.md). Extend reserved `CTRL_FLAGS` bits / add `IMM`; do not invent a parallel TCD layout.

### 5. QSPI / SPI engine

Distinct submodule responsible for:

- QPI-only master: Fast Read Quad **`0xEB`**, Write **`0x02`** (D17); no SPI / Enter / Exit Quad on ASIC
- QPI command, address, dummy (6 wait for `0xEB`), data phases
- Burst holds vs CE# high refresh windows
- Bidirectional SIO direction control
- Read sampling: rising-edge of SCK at **84 MHz** (D16)

QSPI summary: four data lines reused for I/O, approaching **4x** throughput vs 1-bit SPI. ASIC never emits SPI; MCU owns enter/exit QPI (D17).

#### Transaction phases (QPI)

1. **Command** - 8-bit instruction (2 cycles at 4 bits/clock)
2. **Address** - 24-bit memory address (6 cycles)
3. **Wait / dummy** - 6 empty cycles for `0xEB`; float host data pins while DRAM produces data
4. **Data** - sample read data on rising SCK; capture / drive bytes

#### Initialization / mode ownership (D17)

ASIC expects the MCU to put each PSRAM die into **QPI mode** before START (and to Exit Quad / reset after DONE if firmware needs SPI again). Typical MCU pass-through sequence:

1. Wait **150 us** (`tPU`) on start-up before issuing commands
2. Issue Reset Enable (`0x66`) then Reset (`0x99`) over standard SPI (datasheet requires Reset immediately after Reset Enable). Wait recovery **`tRST` min 50 ns**
3. Send Enter Quad Mode (`0x35`)
4. After DMA (optional): Exit Quad Mode (`0xF5`) over QPI, or reset back to SPI

Full opcode / timing truth lives in `05-qspi-psram.md`.

The DMA FSM issues transaction requests; the QSPI engine owns bit-level timing.

## MCU set-up flow

1. **Init / enter QPI** on each die DMA will touch (pass-through while DONE) - D17 precondition
2. **Creating TCDs** - place first **11-byte** TCD at `0x000000` on PSRAM 0; chain via `NEXT_TCD` (die in bit 23); end with a `QUIT=1` TCD
3. Stage source regions (firmware-filled buffers for bulk-copy demos)
4. High-Z MCU QSPI GPIOs; assert START (`ui_in[0]`) while DONE; wait for DONE again or assert ABORT (`ui_in[1]`)
5. **Reading memory / exit QPI** - while DONE, pass-through is on; firmware re-enables MCU QSPI, checks destinations, Exit Quad if needed

## Data path mental model

```
PSRAM A/B --QPI--> RX hold --------> TX stage --QPI--> PSRAM A/B
                         ^
                         |
              SRC_PTR[23] / DEST_PTR[23]
```

Same-device or cross-device (A→B, B→A). Pure memcpy path in V1. Next TCD die from `NEXT_TCD[23]`.

## Demoboard memory context

PMOD-class hardware context (`mole99/qspi-pmod` style):

| Part | Role |
|---|---|
| Winbond **W25Q128JV** (128 M-bit QSPI Flash) | On PMOD; **MCU pass-through only** in V1. ASIC never masters flash CS. Post-V1: ASIC flash read (maybe write). |
| **2x** AP Memory **APS6404L-3SQR** (64 M-bit QSPI PSRAM) | **Both** are first-class DMA working memory (TCDs, buffers; cross-device copies OK) |

Device notes: `A[22:0]` addressing; design clock **84 MHz** QPI (D16); powers up in SPI mode; **MCU** enters QPI via pass-through before START (D17). Internal design uses **24-bit** pointers; device select in `ptr[23]` (D19); `QUIT` in `CTRL_FLAGS`. ASIC data opcodes: `0xEB` / `0x02` only (D15/D17).

## Clock note (D16)

Target **84 MHz** demoboard / design clock. Sample PSRAM read data on the **rising** edge of SCK. `tACLK` up to **5.5 ns** makes rising-edge margin tight at this frequency; Phase 3 must re-check board/TT/`tACLK` constraints against this target before shuttle freeze. DLL training is a V1 non-goal (see `05-qspi-psram.md`).

## What makes this different from a trivial memcpy DMA

1. **Descriptors in memory** - programmable chains, not static channel regs.
2. **Scatter-gather** - non-contiguous regions via `NEXT_TCD`.
3. **Host/ASIC bus multiplex** - real systems problem under pin constraints.
4. **Dual-die PSRAM orchestration** - including cross-device A↔B on one shared QSPI.
5. **QSPI + refresh-aware mastering** - protocol and timing complexity that interviews care about.

## Inspiration boundary

Per TinyDMA-2C prior art (Andrew Kim, TT 296; see `prior-art/tinydma-2c.md`), a 2-channel byte DMA over SPI PSRAM can fit in 1x2 tiles with aggressive width cuts (16-bit addresses, 8-bit lengths). That is a feasibility existence proof and comparison reference only. This project intentionally changes the programming model to descriptor-based scatter-gather and must not inherit that codebase's internal structure. Any reuse of TinyDMA-2C pin/protocol details in discussion must be attributed explicitly.
