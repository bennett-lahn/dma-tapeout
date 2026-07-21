# Architecture Overview

Status: planning-level. V1 freezes: QSPI on `uio`, `ui_in[0]=START`, `uo_out[0]=DONE` (= idle), idle/START/abort/pass-through (D14), **24-bit** pointers, address 0 = null, **11-byte** TCD with `CTRL_FLAGS` device bits (D13), **QPI data path** (D15), **dual PSRAM** DMA (incl. cross-device), **ASIC flash unsupported** (MCU pass-through only). ALU / cond-stop / ring / ASIC flash are **post-V1** (`10-post-v1-features.md`). Remaining open: ABORT/head pin pack, clock, QPI read opcode.

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
[ RP2 MCU ] ---- host control (START/DONE/abort/head) ----> [ DMA ASIC (TT) ] <---- QSPI ----> [ PSRAM A + PSRAM B ]
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
| `uo_out[0]` | **DONE** (ASIC → MCU; high whenever IDLE) |
| `ui_in[7:1]`, `uo_out[7:1]` | Reserved (head/arm/**ABORT**/status/DFT - pin pack open) |

Protocol: DONE ⇔ idle ⇔ pass-through; START ignored while busy; abort finishes current QPI txn then idle (D14). Human summary: `docs/human/architecture/system.md` (I/O section). OE phases: `docs/human/architecture/blocks/host-interface.md`.

## Memory layout and interfacing

### Address model (V1 freeze)

- Internal pointers are **24-bit** (`SRC_PTR`, `DEST_PTR`, `NEXT_TCD`, head).
- QSPI address phase uses **`ptr[22:0]`** (device `A[22:0]`); `ptr[23]` unused / must be 0.
- Device select: **`CTRL_FLAGS`** `SRC_DEV` / `DEST_DEV` / `NEXT_DEV` (D13) - not pointer MSB.
- **Address `0x000000` is reserved for null** (end-of-chain `NEXT_TCD`, invalid head). Do not place TCDs or buffers there.
- Full APS6404L range is DMA-reachable (`A[22:0]`, 8 MB) per die.
- **DFF cost:** working TCD metadata **88 DFFs** (24+24+8+24+8) plus 24-bit head.

### Firmware map (convention)

Software places TCD lists, source buffers, and destinations anywhere in the usable device range except `0x000000`. Hardware does not enforce regions.

### TCD

**11-byte** record: `SRC_PTR`, `DEST_PTR`, `TRANSFER_LEN`, `NEXT_TCD`, `CTRL_FLAGS`. Detail: `04-tcd-and-datapath.md` and `docs/human/architecture/blocks/tcd.md`. `TRANSFER_LEN == 0` is a no-op. Chain ends when `NEXT_TCD == 0x000000` → IDLE / DONE.

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
- START ignored until idle returns. On null `NEXT_TCD` or abort completion (finish current QPI txn), return to idle: clear `uio_oe`, assert DONE (D14).

This boundary is as important as the internal FSM. Without a clean programming path, descriptor DMA is undemoable.

## Major blocks

### 1. Host interface / mode control

Responsibilities:

- Accept configuration of the head pointer / start controls under TT pin limits
- Detect START
- Drive status (active, done, error, cfg pending, debug mux)
- Own mode switch between pass-through and DMA master by gating `uio_oe` (idle: all clear; active: engine-driven per pin/phase)

START/DONE bit indices and idle protocol are frozen (`ui_in[0]` / `uo_out[0]`; D14); head/arm/ABORT/status encoding on the remaining pins is not. Per TinyDMA-2C prior art, that design used `ui_in` + `uio` strobes with a command/payload config adapter and a **fixed** SPI `uio_oe` mask; this project needs dynamic SIO OE for QSPI and a shared-bus pass-through model instead. Same I/O scarcity applies.

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
| `NEXT_TCD` | 24 | Next descriptor address (`0x000000` = end of chain / null) |
| `CTRL_FLAGS` | 8 | `SRC_DEV` / `DEST_DEV` / `NEXT_DEV` + reserved |

Approximate working metadata: **88 DFFs**, plus at least:

- 8-bit **data buffer / holding register** between read and write
- FSM state flops
- QSPI shifter / bit counters / CE# timing counters
- Head pointer / arm / error sticky bits

### 3. Dataflow FSM (descriptor engine)

Planned conceptual states:

1. `IDLE` - DONE high; pass-through enabled; wait for START (ignored elsewhere).
2. `STATE_FETCH` - burst-read **11 bytes** from the `NEXT_TCD` / head address (die from `NEXT_DEV` / head policy) into working registers.
3. `STATE_READ` - read one source byte from `SRC_PTR` into the data buffer (skip if length 0).
4. `STATE_WRITE` - write data-buffer byte to `DEST_PTR`.
5. `STATE_UPDATE` - decrement `TRANSFER_LEN`; increment SRC/DEST linearly; if `TRANSFER_LEN > 0`, loop to `STATE_READ`; if length is 0 and `NEXT_TCD` valid, loop to `STATE_FETCH`; if null, return to `IDLE` (DONE).

Notes from planning:

- `STATE_UPDATE` may fold into `STATE_WRITE` to save states.
- Descriptor fetch should use a **held-CE# burst** so command+address overhead is not paid per TCD byte.
- Data moves are QPI byte-oriented in V1 (D15); CE# refresh slicing remains mandatory for long transfers.
- Abort: finish current QPI txn, then IDLE.
- No `STATE_PROCESS` in V1 (ALU / cond-stop are post-V1).

### 4. Post-V1 blocks (not in V1 silicon plan)

ALU, conditional stop, ring/modulo addressing, and ASIC flash R/W are documented in [`10-post-v1-features.md`](10-post-v1-features.md). Extend reserved `CTRL_FLAGS` bits / add `IMM`; do not invent a parallel TCD layout.

### 5. QSPI / SPI engine

Distinct submodule responsible for:

- Reset / enter-quad initialization policy (open: ASIC-owned vs MCU-owned; SPI **config only** per D15)
- QPI command, address, dummy, data phases for all DMA data
- Burst holds vs CE# high refresh windows
- Bidirectional SIO direction control
- Read sampling edge policy at chosen clock

QSPI summary: four data lines reused for I/O, approaching **4x** throughput vs 1-bit SPI. SPI data opcodes are not in the ASIC DMA path.

#### Transaction phases (QPI)

1. **Command** - 8-bit instruction (2 cycles at 4 bits/clock)
2. **Address** - 24-bit memory address (6 cycles)
3. **Wait / dummy** - empty cycles; float host data pins while DRAM produces data
4. **Data** - sample on chosen edge; capture / drive bytes

#### Initialization sequence

Whoever owns init:

1. Wait **150 us** (`tPU`) on start-up before issuing commands
2. Issue Reset Enable (`0x66`) then Reset (`0x99`) over standard SPI (datasheet requires Reset immediately after Reset Enable). Wait recovery **`tRST` min 50 ns**
3. Send Enter Quad Mode (`0x35`)

Full opcode / timing truth lives in `05-qspi-psram.md`.

The DMA FSM issues transaction requests; the QSPI engine owns bit-level timing.

## MCU set-up flow

1. **Creating TCDs** - pack linked-list **11-byte** descriptors into PSRAM while ASIC is idle (`DONE`, `uio_oe=0`)
2. Stage source regions (firmware-filled buffers for bulk-copy demos)
3. Program head / arm through TT host protocol (pin pack not frozen; lean head die = PSRAM 0)
4. High-Z MCU QSPI GPIOs; assert START while DONE; wait for DONE again (or abort)
5. **Reading memory** - while DONE, pass-through is on; firmware re-enables MCU QSPI and checks destinations

## Data path mental model

```
PSRAM A/B --QPI--> RX hold --------> TX stage --QPI--> PSRAM A/B
                         ^
                         |
              CTRL_FLAGS SRC_DEV / DEST_DEV
```

Same-device or cross-device (A→B, B→A). Pure memcpy path in V1. Next TCD die from `NEXT_DEV`.

## Demoboard memory context

PMOD-class hardware context (`mole99/qspi-pmod` style):

| Part | Role |
|---|---|
| Winbond **W25Q128JV** (128 M-bit QSPI Flash) | On PMOD; **MCU pass-through only** in V1. ASIC never masters flash CS. Post-V1: ASIC flash read (maybe write). |
| **2x** AP Memory **APS6404L-3SQR** (64 M-bit QSPI PSRAM) | **Both** are first-class DMA working memory (TCDs, buffers; cross-device copies OK) |

Device notes: `A[22:0]` addressing; practical clock caution ~66 MHz SPI (config) / ~84 MHz QPI linear burst; powers up in SPI mode; enter QPI via command. Internal design uses **24-bit** pointers; device select in `CTRL_FLAGS` (D13). DMA data path is QPI-only (D15).

## Clock note

Plan around ~**84 MHz** for linear burst operation, with `tACLK` up to **5.5 ns** CLK-to-output delay suggesting a lower clock may be needed for sample margin. Prefer lower clock and/or falling-edge RX sample over DLL training (see `05-qspi-psram.md`).

## What makes this different from a trivial memcpy DMA

1. **Descriptors in memory** - programmable chains, not static channel regs.
2. **Scatter-gather** - non-contiguous regions via `NEXT_TCD`.
3. **Host/ASIC bus multiplex** - real systems problem under pin constraints.
4. **Dual-die PSRAM orchestration** - including cross-device A↔B on one shared QSPI.
5. **QSPI + refresh-aware mastering** - protocol and timing complexity that interviews care about.

## Inspiration boundary

Per TinyDMA-2C prior art (Andrew Kim, TT 296; see `prior-art/tinydma-2c.md`), a 2-channel byte DMA over SPI PSRAM can fit in 1x2 tiles with aggressive width cuts (16-bit addresses, 8-bit lengths). That is a feasibility existence proof and comparison reference only. This project intentionally changes the programming model to descriptor-based scatter-gather and must not inherit that codebase's internal structure. Any reuse of TinyDMA-2C pin/protocol details in discussion must be attributed explicitly.
