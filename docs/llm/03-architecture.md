# Architecture Overview

Status: Architecture frozen and implemented in `src/`; simulation exits M0–M5 accepted (M5: 2026-08-16). Freezes: QSPI on `uio`, `ui_in[0]=START`, `ui_in[2]=BUS_REQ`, `uo_out[0]=DONE` (= idle), `uo_out[1]=BUS_GNT`, idle/START (D14/D18), kill via **`rst_n`** (D23/D34), pass-through request/grant (D22), **24-bit** address pointers with device selects in **`CTRL_FLAGS`** (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`; D24) + **`QUIT`** end-of-chain (D19/D23: quit → IDLE; next START from fixed head), fixed head at `0x000000`/PSRAM0 (D18), **11-byte** TCD (D19), **QPI data path** `0xEB`/`0x02` (D15/D17), MCU-owned enter/exit QPI (D17), **66 MHz** `clk` + **SCK=clk/2** + rising-edge RX (D16), **5-byte** on-chip scratch buffer (**`DMA_BUF_DEPTH=5`** tapeout; depth-agnostic correctness per D20), FSM↔QSPI pulse-start handshake (D21), **dual PSRAM** DMA (incl. cross-device), **ASIC flash unsupported** (MCU pass-through only). Shipped RTL is this feature set only (no ALU / cond-stop / ring / ASIC flash). Formal M4 (`FP-*`) is not a V1 freeze gate (D33). Host pin packing and error model closed (D34): unused `ui_in[1]`, `ui_in[7:3]`, `uo_out[7:2]` tied 0; no ERROR logic.

## Product framing

Working title: **Zero-Overhead Scatter-Gather DMA Engine**.

Target: an **isolated descriptor DMA / bulk mover** between the two QSPI PSRAM devices on the TT PMOD - learning vehicle and resume artifact. ADC / live telemetry integration is out of scope.

Core ideas:

- Memory-management coprocessor for **bulk byte moves**
- **Zero overhead:** TCDs embedded in RAM, not a multi-channel on-chip register file
- **Scatter-gather:** TCD -> next TCD linked lists tolerate arbitrary fragmentation
- **Dual-device:** same-device and cross-device (A↔B) copies on a shared QSPI bus

## System topology

```
[ RP2 MCU ] ---- host (START/DONE/BUS_REQ/BUS_GNT; rst_n) ----> [ DMA ASIC (TT) ] <---- QSPI ----> [ PSRAM A + PSRAM B ]
                                                                                      (shared bus; flash via MCU pass-through)
```

Roles:

- **MCU**: builds TCD linked lists, programs either PSRAM (under `BUS_GNT`), may also talk to **flash** on the same PMOD via grant, stages source buffers, pulses START, handles DONE / mid-run bus yield; uses `rst_n` to kill a runaway DMA (D23).
- **DMA ASIC**: after START, masters the QSPI bus, fetches descriptors, **byte-copies** across **RAM A and/or RAM B** (including cross-device), chains TCDs, yields on `BUS_REQ` between atomic QPI txns, returns bus / asserts completion. Does **not** assert flash CS. No in-flight ALU.
- **PSRAM A/B**: store TCDs, source buffers, destination buffers. Either device may be src and/or dest.
- **Flash (PMOD)**: MCU-only via pass-through. ASIC does not master flash.

## I/O map (Tiny Tapeout)

TT provides **10 inputs** (`clk`, `rst_n`, `ui_in[7:0]`), **8 bidirectional** (`uio[7:0]`), and **8 outputs** (`uo_out[7:0]`).

### Bidirectional - QSPI (V1)

| `uio` | Signal | V1 use |
|---|---|---|
| 0 | Flash CS | **Park high** while `~BUS_GNT` (D26); never driven low by ASIC; MCU may master flash under grant |
| 1 | SD0 / MOSI | SIO0 |
| 2 | SD1 / MISO | SIO1 |
| 3 | SCK | Clock (park low while `~BUS_GNT` between txns) |
| 4 | SD2 | SIO2 |
| 5 | SD3 | SIO3 |
| 6 | RAM A CS | PSRAM A CE# (DMA endpoint; park high when not active) |
| 7 | RAM B CS | PSRAM B CE# (DMA endpoint; park high when not active) |

Shared SIO/SCK: only **one** PSRAM CE# may be low per transaction. Cross-device copies are read-then-write with CS switched between beats. Board **10 kΩ** pull-ups on each CS cover reset / pre-enable; live `~BUS_GNT` parking is ASIC (D26).

### Dedicated host strobes (V1 freeze)

| Port | Assignment |
|---|---|
| `ui_in[0]` | **START** (MCU → ASIC; accepted only in IDLE with `~BUS_REQ`) |
| `ui_in[1]` | Unused (tied 0; D34) |
| `ui_in[2]` | **BUS_REQ** (MCU wants `uio`; D22) |
| `uo_out[0]` | **DONE** (ASIC → MCU; high whenever IDLE) |
| `uo_out[1]` | **BUS_GNT** (MCU may drive `uio`; D22) |
| `ui_in[7:3]` | Unused (tied 0; D34) |
| `uo_out[7:2]` | Unused (tied 0; D34) |

Protocol: DONE ⇔ idle; MCU drives `uio` while `BUS_GNT=1` or `rst_n=0` (D22/D26); START ignored while busy; kill runaway DMA with `rst_n` (D23); `BUS_REQ` pauses DMA after atomic QPI txn (D22); fixed head (D18) + `QUIT` → IDLE, next START from addr 0 (D19/D23). Human summary: `docs/human/architecture/system.md` (I/O section). Bidirectional ownership matrix: this file (below) and `docs/human/architecture/blocks/host-interface.md`. Firmware: `docs/llm/12-firmware.md` / human `firmware.md` (D30).

## Memory layout and interfacing

### Address model (V1 freeze)

- Internal pointers are **24-bit** (`SRC_PTR`, `DEST_PTR`, `NEXT_TCD`) byte addresses. No programmed head register.
- QSPI address phase uses **`ptr[22:0]`** (device `A[22:0]`); **`ptr[23]` is don't-care** (may be any value; D35).
- **Device select:** **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** (D24).
- **Fixed head:** START always fetches TCD at **`0x000000` on PSRAM 0**. Address 0 is a valid TCD/buffer location.
- **End of chain:** fetched TCD with **`CTRL_FLAGS.QUIT=1`** → IDLE / DONE (D19). Next START always refetches **`0x000000` / PSRAM 0** (D23).
- Full APS6404L range is DMA-reachable (`A[22:0]`, 8 MB) per device.
- **DFF cost:** working TCD metadata **88 DFFs** (24+24+8+24+8 flags; full 11-byte record including reserved `[3:0]`); no 24-bit head.

### Firmware map (convention)

Software places the first TCD at `0x000000` on PSRAM 0 (or a `QUIT` TCD for an empty run). Other TCDs/buffers anywhere in the usable range on either device (device via `SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`). Hardware does not enforce regions.

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

On the demoboard, RP2 MCU GPIOs, ASIC `uio`, and the QSPI PMOD share those nets. **Pass-through is OE arbitration on a shared bus**, not a gate-level proxy that copies MCU QSPI from `ui_in` onto a separate PSRAM port. The MCU has its own GPIO OE; TT `uio_oe` does not control the MCU. Both masters enabled on one net is contention (undefined levels, pad stress). Handoffs must **release before seize**.

Human-facing phase tables and conceptual RTL: `docs/human/architecture/blocks/host-interface.md`.

### Bidirectional I/O ownership specification (normative)

This matrix is the V1 source of truth for who may drive each shared `uio` net. Verification (`CHK-PIN-SIO-OWN`, `Q-SIO-OWN`, `CHK-ARB-*`, park/reset checkers) and firmware handoffs implement this table. Handoffs are always **release before seize**.

**Actors**

| Actor | What it can drive |
|---|---|
| **ASIC** | TT `uio_out` when the matching `uio_oe` bit is 1 |
| **MCU** | RP2 GPIO outputs when firmware enables them (legal while `BUS_GNT=1` or `rst_n=0`; D22/D26) |
| **PSRAM A / B** | Device SIO outputs while that device's CE# is low and the protocol phase is a device-output window (and briefly after CE# rises through `tHZ`) |
| **Flash** | Device SIO only when flash CS is low under MCU grant (ASIC never selects flash) |
| **Board** | 10 kΩ pull-ups on flash CS, RAM A CS, and RAM B CS (keepers during reset / pre-enable unless MCU drives CS) |

**Legend for the tables below**

| Cell | Meaning |
|---|---|
| **Drive** | Actor intentionally enables its output driver |
| **Hi-Z** | Actor's output driver is off |
| **Float** | No intentional digital driver on that net (ASIC and MCU Hi-Z; device not sourcing). Allowed only where listed. |
| **Pull-up** | Board resistor is the only defined keeper (CS nets during reset / pre-enable) |
| **Don't-care** | ASIC drives an arbitrary stable value (implementation may use `0`); level is not protocol-meaningful |

**Global invariants (every phase)**

1. At most one intentional digital driver per net. Equal driven values still count as illegal dual-drive on SIO (`Q-SIO-OWN` / `CHK-PIN-SIO-OWN`).
2. MCU and ASIC must not both enable drivers on the same net with disagreeing levels. Brief overlap only on idle levels (CS high / SCK low) is the only benign MCU/ASIC exception; SIO has no such exception.
3. RAM A CE# and RAM B CE# are never both low.
4. ASIC never drives flash CS low.
5. Unselected PSRAM never drives SIO (except its own bounded `tHZ` after its CE# rises).
6. CS and SCK are never left floating while the ASIC is bus keeper (`rst_n=1`, `~BUS_GNT`). SIO is never left floating in park / IDLE / between-txn after the post-CE# `tHZ` window.

#### Control-plane phases (all eight `uio` pins)

| Phase | Condition | Flash CS | RAM A CS | RAM B CS | SCK | SIO[3:0] |
|---|---|---|---|---|---|---|
| **Reset** | `rst_n=0` | ASIC Hi-Z; board **Pull-up** unless MCU drives CS | same | same | ASIC Hi-Z; **MCU may Drive** (MCU-safe window, D26) | ASIC Hi-Z; **MCU** or selected memory per host SPI phase |
| **ASIC park** | `rst_n=1`, `~BUS_GNT`, no live QPI txn | **ASIC Drive** high | **ASIC Drive** high | **ASIC Drive** high | **ASIC Drive** low | **ASIC Drive** don't-care; MCU Hi-Z; both PSRAM Hi-Z |
| **MCU grant** | `BUS_GNT=1` | **ASIC Hi-Z**; **MCU** owns per host txn (else rely on pull-up when MCU leaves CS undriven) | same pattern for each CS MCU uses | same | **ASIC Hi-Z**; **MCU Drive** when mastering | **ASIC Hi-Z**; **MCU** or selected memory per host QSPI/SPI phase (see below) |
| **ASIC live txn** | `rst_n=1`, `~BUS_GNT`, CE# window active | **ASIC Drive** high | **ASIC Drive** (one low / one high per `device_sel`) | same | **ASIC Drive** (clk/2 toggle) | See QPI sub-phases below |

Reset does **not** assert `BUS_GNT`, but while `rst_n=0` MCU drive of the shared QSPI nets is legal (D26). While `rst_n=1`, MCU drivers stay Hi-Z until `BUS_GNT=1`.

#### ASIC-master QPI sub-phases (SIO ownership)

Applies only while ASIC is master (`~BUS_GNT`) and a RAM CE# is in its transaction window. CS/SCK follow the live-txn row above for the whole CE#-low interval.

| QPI sub-phase | ASIC SIO | Selected PSRAM SIO | Unselected PSRAM / Flash SIO | Net intent |
|---|---|---|---|---|
| Command | **Drive** opcode nibbles | **Hi-Z** (inputs) | **Hi-Z** | ASIC → device |
| Address | **Drive** address nibbles (`addr[23]` don't-care; D35) | **Hi-Z** | **Hi-Z** | ASIC → device |
| Write data (`0x02`) | **Drive** data nibbles | **Hi-Z** | **Hi-Z** | ASIC → device |
| Dummy / wait (`0xEB`, 6 SCK) | **Hi-Z** (float) | **Hi-Z** (not yet sourcing) | **Hi-Z** | **Float** (listen window; no dual-drive) |
| Read data (`0xEB`) | **Hi-Z** (float); sample `uio_in` | **Drive** data nibbles | **Hi-Z** | Device → ASIC |
| Post-CE# turnaround | **Hi-Z** on SIO until modeled `tHZ` expires | May still **Drive** until `tHZ`, then **Hi-Z** | **Hi-Z** | **Float** or device-only; ASIC must not reclaim SIO OE early |
| Between txns / IDLE park | After `tHZ`: return to **ASIC park** (SIO don't-care driven) | **Hi-Z** | **Hi-Z** | ASIC bus keeper |

Write transactions have no dummy/read window: SIO stays ASIC-driven for command, address, and data, then enters the post-CE# turnaround rule before park reclaim.

#### MCU-master phases (while `BUS_GNT=1` or `rst_n=0`)

ASIC `uio_oe` is all 0. Firmware is the only legal external master.

| Host sub-phase | MCU SIO | Selected memory SIO | ASIC |
|---|---|---|---|
| Command / address / write / SPI-mode bring-up | **Drive** | **Hi-Z** | **Hi-Z** |
| Dummy / read / device-output | **Hi-Z** | **Drive** (selected flash or PSRAM) | **Hi-Z** |
| Idle gap under grant (between host txns) | Firmware choice: drive idle levels or Hi-Z; CE# should be high | **Hi-Z** | **Hi-Z** |

#### Illegal (must fail in sim; firmware must avoid)

| Condition | Why |
|---|---|
| ASIC SIO OE=1 while any PSRAM/flash model drives SIO | Pad contention (`CHK-PIN-SIO-OWN` / `Q-SIO-OWN`) |
| MCU drives any `uio` while `rst_n=1` and `BUS_GNT=0` | Fights ASIC bus keeper |
| ASIC `uio_oe!=0` while `BUS_GNT=1` | Grant broken (`CHK-ARB-GNT-OE`) |
| Both RAM CE# low | Shared SIO multiplex violation |
| ASIC drives flash CS low | Flash is MCU pass-through only in V1 |
| ASIC reclaim of SIO OE inside the selected device's `tHZ` after CE# rise | Turnaround contention (`T-HZ`) |

### Mode A - MCU pass-through (programming)

- MCU asserts **BUS_REQ**, waits for **BUS_GNT**; ASIC **releases** `uio_oe = 0` on all QSPI pins.
- MCU firmware drives the shared `uio` nets as QSPI master to **PSRAM A, PSRAM B, and/or flash**.
- Works in IDLE or as a mid-DMA yield after the current QPI txn completes atomically. Idle/`DONE` alone is not a drive permit (D22). MCU may also drive while `rst_n=0` without grant (D26).

### Mode B - DMA master (execution)

- MCU finishes any QSPI txn, high-Zs its QSPI GPIOs, drops **BUS_REQ**, waits for **BUS_GNT** low, then asserts START (`ui_in[0]`) while DONE is high.
- ASIC leaves idle (DONE low): remains **bus keeper** while `~BUS_GNT` (D26) - parks all CS high and SCK low between txns; during a live txn drives SCK + CS mux (one RAM CE# low; flash CS stays high); SIO OE follows the ownership matrix (drive cmd/addr/write; float dummy/read and through post-CE# `tHZ`; park don't-care after `tHZ`).
- START ignored until idle returns. On quit TCD, return to idle: keep parking, assert DONE; next START fetches fixed head again (D14/D18/D19/D23). Kill mid-run with `rst_n` (D23; board CS pull-ups hold CE# during reset). Mid-run `BUS_REQ` pauses between atomic txns without forcing IDLE (D22).

This boundary is as important as the internal FSM. Without a clean programming path, descriptor DMA is undemoable.

## Major blocks

### 1. Top module / host-input synchronizers

The TT entry (project top) owns pad wiring and **brings asynchronous MCU host inputs into the design `clk` domain** before any control logic samples them.

Responsibilities:

- Instantiate two-flop (or equivalent) synchronizers for async MCU levels on `ui_in`: at least **START** and **BUS_REQ**
- Rising-edge detect synchronized START and feed `sys_controller.start` a **one-`clk` pulse**; feed synchronized BUS_REQ as a level
- Feed only these CDC-qualified signals into the integrated host/descriptor controller (`sys_controller`)
- Instantiate and wire the integrated system controller and QSPI engine; mux `uio_oe` / status onto TT ports

MCU GPIOs are not guaranteed synchronous to the demoboard `clk`. Raw `ui_in` must not drive FSM / grant logic directly. `BUS_REQ` is synchronized and retained as a level. START is synchronized as a level and then rising-edge detected; `sys_controller` receives exactly one `clk` pulse per captured low-to-high transition. A pulse that occurs while DMA is active or `BUS_REQ` is high is ignored and not queued - firmware must drop REQ (if any), wait for `BUS_GNT` low, and issue a **new** START rising edge. After pulsing START, firmware must not raise `BUS_REQ` until **DONE falls** (START-accept ACK); overlapping REQ with the START hold window can discard the pulse in IDLE or accept-then-stall at `NEW_FETCH`. V1 does not latch a sticky pending START (DFF cost / restart hazards). Firmware must return START low before issuing another command and must hold each raw assertion long enough for the synchronizer to capture it.

**DFF / tile impact:** ~2 DFFs per synchronized bit plus one delayed-START flop for edge detection (≈5 DFFs for START/BUS_REQ); negligible within the 1x1 budget (D36). Human detail: `docs/human/architecture/blocks/host-interface.md`.

### 2. Integrated system controller

Responsibilities:

- Consume the post-sync one-`clk` START pulse and synchronized BUS_REQ level (not raw pad levels)
- Drive status: DONE and BUS_GNT only (`uo_out[7:2]` tied 0; D34)
- Fetch and execute descriptor chains through the QSPI engine
- Own the mode switch between pass-through and DMA master via internal FSM `uio_oe` arbitration + D22 request/grant + D26 bus keeper (park CS/SCK while `rst_n=1` and `~BUS_GNT`; release OE on grant or while `rst_n=0`; SIO phase-accurate during live txns)

`sys_controller.sv` intentionally combines host/mode control and descriptor sequencing. "Host interface" and "descriptor FSM" name two behavioral views of this module, not an RTL port boundary. The QSPI engine remains separate.

Frozen: `ui_in[0]=START`, `ui_in[2]=BUS_REQ`, `uo_out[0]=DONE`, `uo_out[1]=BUS_GNT` (D14/D18/D22/D23/D34); unused `ui_in[1]`, `ui_in[7:3]`, `uo_out[7:2]` tied 0; no head-pointer pins. Per TinyDMA-2C prior art, that design used `ui_in` + `uio` strobes with a command/payload config adapter and a **fixed** SPI `uio_oe` mask; this project needs dynamic SIO OE for QSPI and a shared-bus pass-through model instead. Same I/O scarcity applies.

I/O principles (still binding):

1. Serialize host interfaces; do not assume wide parallel buses
2. Verification must cover edge cases that cannot be probed on silicon (including double-drive / host drives without `BUS_GNT` while out of reset)
3. While `rst_n=1` and `~BUS_GNT`, ASIC parks the bus (D26); release all shared OE under `BUS_GNT` or while `rst_n=0`; never enable MCU and ASIC drivers with disagreeing levels
4. Sample host control only after top-level sync into `clk`

### 3. Working-state register file (explicit, DFF-critical)

Only the **currently executing TCD** is resident on-chip. Planned fields:

| Field | Width | Role |
|---|---|---|
| `SRC_PTR` | 24 | Source byte address (`[22:0]`; `[23]` unused) |
| `DEST_PTR` | 24 | Dest byte address (`[22:0]`; `[23]` unused) |
| `TRANSFER_LEN` | 8 | Bytes remaining (0 = no-op) |
| `NEXT_TCD` | 24 | Next descriptor byte address (`[22:0]`; `[23]` unused) |
| `QUIT` / device selects / reserved | 8 | Flattened last byte of hardware `tcd_t`: `next_tcd_device`, `dest_device`, `src_device`, `quit` (maps to `CTRL_FLAGS[7:4]`), then `reserved` (`CTRL_FLAGS[3:0]`, packed LSB). V1 control ignores reserved. |

Approximate working metadata: **88 DFFs**, plus at least:

- **Data buffer / holding register** between read and write (**`N` bytes; tapeout `N=5`**, nibble shift register; D20)
- FSM state flops
- QSPI shifter / bit counters / CE# timing counters

**Buffer depth (D20):** V1 tapeout uses **`N=5`**. FSM / QSPI sequencing must treat `N` as a parameter: correctness (TCD semantics, pointer/`TRANSFER_LEN` updates, single-CS cross-device) must not depend on a specific buffer length. Deepening the scratch later is a performance/DFF trade only. Verification may elaborate any integer `1..DMA_BUF_DEPTH_MAX` (8) via Makefile `-G`/`-P`; default sim/Make depth is 5. At `N=1` (and 11-byte TCD fetch), `tCEM` / Linear Burst page-cross limits are not binding. First failing depths @ 33 MHz SCK for a full-buffer hold: **`N ≥ 60`** (`tCEM` 4 us / `0xEB`), **`N ≥ 1026`** (two 1K page crosses).

### 4. Descriptor-control behavior inside `sys_controller`

The integrated controller currently uses these states:

1. `IDLE` - DONE high; park bus while `~BUS_GNT` (D26); wait for START with `~BUS_REQ` (ignored elsewhere).
2. `STATE_FETCH` - burst-read **11 bytes** into working registers (nibble shifter; all 22 wire nibbles latched, including `CTRL_FLAGS[3:0]` reserved). First fetch: `0x000000` / PSRAM 0; later: `NEXT_TCD` on `NEXT_DEVICE`. If `QUIT=1` → `IDLE`.
3. `STATE_READ` - read up to buffer depth `N` bytes from `SRC_PTR` into the data buffer (tapeout: `N=5`; skip if length 0).
4. `STATE_WRITE` - write the buffered bytes to `DEST_PTR`.
5. `STATE_UPDATE` - decrement `TRANSFER_LEN` by bytes moved. Pointers are already advanced by `N` on READ/WRITE exit (don't-care on the final chunk). If length remains, loop to `STATE_READ`. If length reaches 0, loop to `STATE_FETCH` for `NEXT_TCD` on `NEXT_DEVICE`.

Notes from planning:

- `STATE_UPDATE` may fold into `STATE_WRITE` to save states.
- Descriptor fetch should use a **held-CE# burst** so command+address overhead is not paid per TCD byte.
- Data moves are QPI byte-oriented in V1 (D15). With `N=1`, each READ/WRITE raises CE# after one byte - no CE# refresh / page slicer required; revisit if `N` grows (D20).
- Buffer depth **`N=5`** for V1 tapeout; do not bake `N` into correctness assumptions (D20).
- Abort mid-run: use **`rst_n`** (D23); no soft-abort pin (D34).
- **BUS_REQ (D22):** MCU priority; do not start a new QPI txn while REQ; finish in-flight txn atomically, assert `BUS_GNT`, resume when REQ drops (unless IDLE).
- No `STATE_PROCESS` (no ALU / cond-stop).
- **`uio_oe` arbitration (D26):** ASIC is bus keeper while `rst_n=1 && ~BUS_GNT` (park all CS high / SCK low; SIO per the bidirectional ownership matrix: drive don't-care in park; float only on dummy/read and through post-CE# `tHZ`). Force every shared output enable low while active-low reset is asserted (`rst_n=0`) or under `BUS_GNT`; MCU drive while `rst_n=0` is legal. Do not float CS/SCK, or SIO in idle / between transactions outside reset and outside the `tHZ` window. Board 10 kΩ CS pull-ups cover reset / pre-enable when MCU is not driving CS.

### 5. QSPI / SPI engine

Distinct submodule responsible for:

- QPI-only master: Fast Read Quad **`0xEB`**, Write **`0x02`** (D17); no SPI / Enter / Exit Quad on ASIC
- QPI command, address, dummy (6 wait for `0xEB`), data phases
- Burst holds vs CE# high refresh windows
- Bidirectional SIO direction control
- Read sampling: rising-edge of SCK; system **`clk` 66 MHz**, **SCK=clk/2** (D16)

QSPI summary: four data lines reused for I/O, approaching **4x** throughput vs 1-bit SPI. ASIC never emits SPI; MCU owns enter/exit QPI (D17).

#### Transaction phases (QPI)

1. **Command** - 8-bit instruction (2 cycles at 4 bits/clock)
2. **Address** - 24-bit phase on the wire (6 cycles); useful bits are `A[22:0]`; phase MSB (`addr[23]`) is don't-care (D35); device via `device_sel`
3. **Wait / dummy** - 6 empty cycles for `0xEB`; float host data pins while DRAM produces data
4. **Data** - sample read data on rising SCK; capture / drive bytes

#### Initialization / mode ownership (D17)

ASIC expects the MCU to put each PSRAM device into **QPI mode** before START (and to Exit Quad / reset after DONE if firmware needs SPI again). Typical MCU pass-through sequence:

1. Wait **150 us** (`tPU`) on start-up before issuing commands
2. Issue Reset Enable (`0x66`) then Reset (`0x99`) over standard SPI (datasheet requires Reset immediately after Reset Enable). Wait recovery **`tRST` min 50 ns**
3. Send Enter Quad Mode (`0x35`)
4. After DMA (optional): Exit Quad Mode (`0xF5`) over QPI, or reset back to SPI

Full opcode / timing truth lives in `05-qspi-psram.md`.

The DMA FSM issues transaction requests and arbitrates `uio_oe` (bus keeper while `~BUS_GNT`; release on grant; QSPI engine phase mask during a live txn); the QSPI engine owns bit-level timing and the live-txn SIO OE mask.

#### FSM ↔ QSPI engine handshake (D21)

Request (not a TCD): `{cmd, addr, device_sel, byte_len}` via `qspi_pkg` (`qspi_cmd_t`, `qspi_addr_t`, `qspi_device_sel_t`, `QPI_BYTE_LEN_W` in `src/types.svh`). `qspi_addr_t` is **24-bit** to match the QPI address phase; device uses `addr[22:0]` (`A[22:0]`); **`addr[23]` is don't-care** (may be any value; D35). `device_sel` selects PSRAM 0/1 from `SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE` as appropriate; pad CE#s remain `ram_a_cs_n` / `ram_b_cs_n`. `byte_len` is `logic [QPI_BYTE_LEN_W-1:0]` with `QPI_BYTE_LEN_W = $clog2(QPI_MAX_BYTES + 1)` and `QPI_MAX_BYTES = max(DMA_BUF_DEPTH_MAX, QPI_TCD_BYTES)` (`DMA_BUF_DEPTH` itself is a module parameter; V1 tapeout and default sim: **5**). Engine does **not** latch the request: FSM must keep it stable from `txn_valid` until `busy` low. Engine SCK is a registered toggle (**SCK = clk/2**); no `txn_ready` / no `wdone`.

| Signal | Dir | Contract |
|---|---|---|
| `txn_valid` | FSM → eng | **1-cycle pulse** to start; only when `~busy` |
| `busy` | eng → FSM | In-flight txn; also the start qualifier; OE reclaim / BUS_GNT wait for clear |
| `rdata` / `rdata_valid` | eng → FSM | Held read nibble + **1-`clk` pulse** on rising SCK; exactly `2 * byte_len` pulses |
| `wdata` / `wdata_next` | FSM ↔ eng | First nibble with `txn_valid`; `wdata_next` asserts on falling SCK iff another nibble is needed (`2 * byte_len - 1` pulses). On `wdata_next`, next nibble must be on `wdata` before the next `clk` (same-cycle) for SPI/SIO setup |
| `sclk` | eng → pad | **clk/2** when enabled; 0 in pad/idle states |
| `ram_*_cs_n`, `sio_*` | eng ↔ pad | Device mux + SIO drive/OE (FSM grants `uio_oe` at top) |

Write ends after `2 * byte_len` SCK (no `wdone`). Full per-port table: `docs/human/architecture/blocks/qspi-engine.md`.

## MCU set-up flow

1. **Init / enter QPI** on each device DMA will touch (`BUS_REQ`/`BUS_GNT`) - D17 precondition
2. **Creating TCDs** - place first **11-byte** TCD at `0x000000` on PSRAM 0; chain via `NEXT_TCD` + `NEXT_DEVICE`; end with a `QUIT=1` TCD
3. Stage source regions (firmware-filled buffers for bulk-copy demos)
4. High-Z MCU QSPI GPIOs; drop BUS_REQ; wait for BUS_GNT low; assert START (`ui_in[0]`) while DONE; wait for DONE again, pause mid-run with BUS_REQ, or assert `rst_n` to kill (D23). After `QUIT` → IDLE, next START always refetches `0x000000` / PSRAM 0.
5. **Reading memory / exit QPI** - assert BUS_REQ, wait for BUS_GNT; firmware re-enables MCU QSPI, checks destinations, Exit Quad if needed

## Data path mental model

```
PSRAM A/B --QPI--> RX hold --------> TX stage --QPI--> PSRAM A/B
                         ^
                         |
              SRC_PTR[23] / DEST_PTR[23]
```

Same-device or cross-device (A→B, B→A). Pure memcpy path in V1. Devices from `CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`.

## Demoboard memory context

PMOD-class hardware context (`mole99/qspi-pmod` style):

| Part | Role |
|---|---|
| Winbond **W25Q128JV** (128 M-bit QSPI Flash) | On PMOD; **MCU pass-through only**. ASIC never masters flash CS. |
| **2x** AP Memory **APS6404L-3SQR** (64 M-bit QSPI PSRAM) | **Both** are first-class DMA working memory (TCDs, buffers; cross-device copies OK) |

Device notes: `A[22:0]` addressing; design **`clk` 66 MHz**, **SCK=clk/2** (D16); powers up in SPI mode; **MCU** enters QPI via pass-through before START (D17). Internal design uses **24-bit** pointers; device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`; D24); `QUIT` in `CTRL_FLAGS`. ASIC data opcodes: `0xEB` / `0x02` only (D15/D17).

## Clock note (D16)

Target **66 MHz** demoboard / design `clk` (**SCK=clk/2** ≈ 33 MHz). Sample PSRAM read data on the **rising** edge of SCK. Phase 3 must re-check board/TT/`tACLK` against this target before shuttle freeze. DLL training is a V1 non-goal (see `05-qspi-psram.md`).

## What makes this different from a trivial memcpy DMA

1. **Descriptors in memory** - programmable chains, not static channel regs.
2. **Scatter-gather** - non-contiguous regions via `NEXT_TCD`.
3. **Host/ASIC bus multiplex** - real systems problem under pin constraints.
4. **Dual-device PSRAM orchestration** - including cross-device A↔B on one shared QSPI.
5. **QSPI + refresh-aware mastering** - protocol and timing complexity that interviews care about.

## Inspiration boundary

Per TinyDMA-2C prior art (Andrew Kim, TT 296; see `prior-art/tinydma-2c.md`), a 2-channel byte DMA over SPI PSRAM can fit in 1x2 tiles with aggressive width cuts (16-bit addresses, 8-bit lengths). That is a feasibility existence proof and comparison reference only. This project intentionally changes the programming model to descriptor-based scatter-gather and must not inherit that codebase's internal structure. Any reuse of TinyDMA-2C pin/protocol details in discussion must be attributed explicitly.
