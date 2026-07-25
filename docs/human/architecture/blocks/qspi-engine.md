# QSPI Engine

Status: skeleton. **QPI-only ASIC path (D15/D17):** Fast Read Quad `0xEB`, Write `0x02`. System **`clk` 66 MHz** (D16); engine **SCK = clk/2** (registered toggle). Rising-edge RX on SCK. MCU owns enter/exit QPI. CS mux for RAM A/B is in scope (D11); flash opcodes out of V1.

## Role

Bit-level QSPI master used by the descriptor FSM:

- QPI command, address, dummy (6 wait for `0xEB`), data phases for all DMA reads/writes
- Bidirectional SIO direction control (`uio_oe` per phase while ASIC is bus master)
- **RAM A / RAM B chip-select mux** (never both low; flash CS never asserted by ASIC in V1)
- **SCK generation:** registered toggle when enabled → **SCK = clk/2**; idle low when disabled (no combo mux/gate of `clk` onto the pad)
- Read sampling: capture `sio_in` into `rdata` on detected **rising SCK** (clk-domain); pulse `rdata_valid` one `clk`

**Not in ASIC:** Enter Quad (`0x35`), Exit Quad (`0xF5`), Reset (`0x66`/`0x99`), Fast Read `0x0B`, any SPI data opcodes. MCU does mode bring-up / teardown via pass-through (D17).

QSPI has four bidirectional data lines reused for I/O, giving up to about **4x** throughput vs 1-bit SPI (half-duplex data phases, tighter timing).

`uio_oe` is **arbitrated by the descriptor FSM**: FSM owns OE by default (idle / between txns → typically `uio_oe=0`); the engine drives the per-pin mask only while the FSM has granted a live transaction (SCK + selected RAM CS driven; flash CS OE forced off; SIO drive on cmd/addr/write, float on dummy/read). When not granted, the engine's OE contribution is ignored / forced off. Bus ownership phases: [`host-interface.md`](host-interface.md); arbiter: [`descriptor-fsm.md`](descriptor-fsm.md).

Hard CE# / clock limits are summarized in `[../limitations.md](../limitations.md)`. Full opcode tables: `[../../../llm/05-qspi-psram.md](../../../llm/05-qspi-psram.md)`. With V1 buffer depth `N=1` (and 11-byte TCD fetch), each CE# pulse is short enough that `**tCEM` and Linear Burst one-page-cross limits are not binding** - no CE# refresh timer or page slicer required (see `[descriptor-fsm.md](descriptor-fsm.md)`). Revisit if `N` grows (D20).

### Cross-device transfers

Shared SIO bus ⇒ only one CE# low at a time. A→B (or B→A) is: read byte from src die, raise CE#, then write byte to dest die. Same APS6404L QPI opcodes on both dies. Device select comes from pointer MSBs (`ptr[23]`).

## SPI vs QPI (D15 / D17)


| Use                                                            | Mode / owner              |
| -------------------------------------------------------------- | ------------------------- |
| TCD fetch / payload read (`0xEB`) / payload write (`0x02`)     | **ASIC QPI**              |
| Enter Quad (`0x35`), Exit Quad (`0xF5`), Reset (`0x66`/`0x99`) | **MCU pass-through only** |
| SPI data opcodes                                               | **Not used by ASIC**      |


ASIC expects both dies already in **QPI mode** before START.

## Transaction phases (QPI)

1. **Command** - 8-bit opcode (2 SCK at 4 bits/SCK)
2. **Address** - **24-bit** phase on the wire (6 SCK). APS6404L consumes `A[22:0]` only; the MSB of this phase is unused (`addr[23]=0`). Die/CS comes from `die_sel`, not that MSB.
3. **Wait / dummy** - 6 SCK for `0xEB`; float host data pins. No wait for write.
4. **Data** - sample read data on rising SCK; 2 SCK per byte in quad mode

Phase lengths are counted in **SCK beats**. Pad states (`CS_ON`, `SCLK_OFF`, `CS_OFF`, `IDLE`) advance on **`clk`**.

## Descriptor FSM interface (D21)

Engine is a **transaction slave** of the descriptor FSM. Request is **not** a TCD; TCD fields stay in the FSM.

There is **no `txn_ready`** and **no `wdone`**. Start legality is `~busy`. Write length is entirely from `byte_len` (engine counts `2 * byte_len` SCK beats, then end-pad / raise CE#).

### Request (FSM-held; engine does **not** latch)

Engine samples `cmd` / `addr` / `die_sel` / `byte_len` live. FSM must keep the full request **stable from `txn_valid` until `busy` falls**.

| Field | Type (`qspi.svh`) | Role |
|---|---|---|
| `cmd` | `qspi_cmd_t` | V1: `QSPI_CMD_FAST_READ` (`0xEB`) or `QSPI_CMD_WRITE` (`0x02`) |
| `addr` | `qspi_addr_t` `[23:0]` | Full 24-bit QPI address phase. Device uses `addr[22:0]` as `A[22:0]`; **`addr[23]` unused** (drive 0). Do not put `ptr[23]` here. |
| `die_sel` | `qspi_die_sel_t` | Which PSRAM (`QSPI_PSRAM0` / `QSPI_PSRAM1` from `ptr[23]`); not a pad CE# |
| `byte_len` | `logic [QSPI_BYTE_LEN_W-1:0]` | Exact payload length for this CE# pulse (FETCH=`QSPI_TCD_BYTES`, data=`k` ≤ `N`). Width from `qspi_pkg`: `QSPI_BYTE_LEN_W = $clog2(QSPI_MAX_BYTES + 1)`, `QSPI_MAX_BYTES = max(DMA_BUF_DEPTH, QSPI_TCD_BYTES)` (V1: `N=1`, TCD=11 → width 4). |

### Handshake signals

| Signal | Dir | Meaning |
|---|---|---|
| `txn_valid` | FSM → eng | **1-cycle pulse** to start; legal only when `~busy` |
| `busy` | eng → FSM | Transaction in flight (accept through CE# complete / back to `IDLE`); ABORT and OE reclaim wait for this; also the “can start” qualifier (`txn_valid` only while low) |
| `rdata` / `rdata_valid` | eng → FSM | Read **nibble** captured on rising SCK; `rdata_valid` is a **1-`clk` pulse** with the new `rdata` |
| `wdata` / `wdata_next` | FSM → eng / eng → FSM | Write nibble on `wdata[3:0]`. `wdata_next` is a **1-`clk` pulse** on **falling SCK** in the write data phase: FSM must present the next nibble after that pulse (setup for the following rising SCK). No `wdata_next` on the last nibble’s fall before the engine leaves write (length-driven). |

### Flows

**Read (FETCH / STATE_READ):**

1. When `~busy`, FSM presents request and pulses `txn_valid` (1 cycle).
2. Engine runs cmd/addr/dummy/data; on each data-phase rising SCK captures `sio_in` → `rdata` and pulses `rdata_valid`.
3. FSM sinks every `rdata_valid` pulse (assumed always ready). Engine transfers exactly `2 * byte_len` nibbles, then `SCLK_OFF` → `CS_OFF` → `IDLE`.
4. When `busy` clears, FSM may start the next txn.

**Write (STATE_WRITE):**

1. When `~busy`, FSM presents request with the **first write nibble already on `wdata`**, and pulses `txn_valid` (same cycle).
2. Engine does not stage that nibble; FSM keeps it on `wdata` until `wdata_next`, then updates for the next beat.
3. On each falling SCK in the write data phase (except after the final beat completes the count), engine pulses `wdata_next`; FSM places the next nibble in time for the next rising SCK.
4. Engine ends the write after exactly `2 * byte_len` SCK beats (no `wdone` from the FSM), then end-pad / raise CE# (`SCLK_OFF` → `CS_OFF` → `IDLE`).
5. Engine never waits on the FSM after accept; never writes past `byte_len`.

### Rules

1. Engine does **not** latch the request; FSM must keep `{cmd, addr, die_sel, byte_len}` stable from `txn_valid` until `busy` is low.
2. On writes, the first nibble must be on `wdata` in the same cycle as `txn_valid`.
3. Engine **never stalls** SCK/CE# waiting on the FSM (deterministic QPI).
4. FSM pulses `txn_valid` only while `~busy`.
5. Engine owns start/end CE# pad and ≥2-`clk` `tCPH` (via `CS_OFF` + `IDLE` before the next CE# fall).
6. FSM grants `uio_oe` for the live txn; reclaim when `busy` clears (idle / between txns → OE off for pass-through).

RTL cheat-sheet: [`../../../../src/rtl/qspi_engine.sv`](../../../../src/rtl/qspi_engine.sv).

## Engine behavior notes

### SCK = clk/2

SCK is a FF that toggles each `clk` while `sclk_en` is high, and is held **0** when disabled (`IDLE`, `CS_ON`, `SCLK_OFF`, `CS_OFF`). At **66 MHz** `clk`, effective QPI **SCK ≈ 33 MHz**. Phase 3 timing checks use that SCK rate (not full-rate `clk` on the pad).

### QPI bit order

Within each byte, bits are sent **high → low** (MSB first): the upper nibble goes out on the first SCK, the lower nibble on the second. Within a nibble, **SIO[3]** carries the most significant bit, down to **SIO[0]** as the least. Same mapping on RX.

Example: byte `0xA5` → clock 1: `SIO[3:0] = 4'b1010`; clock 2: `SIO[3:0] = 4'b0101`.

### CE# high between transactions (`tCPH`)

After a read/write completes (CE# raised), keep CE# high for **≥ 2 system `clk` cycles** before the next CE# falling edge (`CS_OFF` + at least one `IDLE`). That meets `tCPH` at 66 MHz `clk`.


| Item              | Value                                                 |
| ----------------- | ----------------------------------------------------- |
| `tCPH` (APS6404L) | **18 ns** min CE# high between bursts                 |
| System `clk` (D16)| **66 MHz** → `T ≈ 15.15 ns`                           |
| SCK (engine)      | **clk/2** → ≈ 33 MHz when enabled                     |
| 1 `clk` CE# high  | ≈ 15.2 ns **< 18 ns** → violates `tCPH`               |
| 2 `clk` CE# high  | ≈ 30.3 ns **≥ 18 ns** → meets `tCPH` (~12 ns margin)  |


Cross-device A→B already raises CE# between read and write; that gap must still be ≥ 2 `clk`.

### CE# / SCK edge padding

Timing pad states (SCK held low) around each transaction:

1. **Start (`CS_ON`):** assert CE# low for **one `clk`** before the first SCK edge (covers `tCSP`).
2. **End (`SCLK_OFF`):** after the last SCK edge, keep CE# low for **one `clk`** with SCK off, then **`CS_OFF`** raises CE#.

Then apply the two-`clk` CE# high `tCPH` wait (`CS_OFF` + `IDLE`) before the next CE# falling edge.

## Initialization sequence (MCU-owned, D17)

ASIC does not run this sequence. Firmware (pass-through while DONE) on each die:

1. Wait **>= 150 us** after power-up before issuing commands (`tPU`; CE# high)
2. Issue **Reset Enable** (`0x66`) then **Reset** (`0x99`) over standard SPI. After Reset, wait `**tRST` min 50 ns**
3. Send **Enter Quad Mode** (`0x35`) over 1-bit SPI
4. After DMA (optional): **Exit Quad** (`0xF5`) over QPI, or reset back to SPI

## Critical CE# / refresh rules

**WARNING:** every read/write must finish by raising **CE# high** to terminate the command and allow standby/refresh.


| Symbol  | Meaning                                | Value (APS6404L class)                                                                            |
| ------- | -------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `tCEM`  | Max CE# low pulse width                | **4 us** extended grade / **8 us** standard grade                                                 |
| `tCPH`  | Min CE# high between subsequent bursts | **18 ns** → **≥ 2 `clk`** @ 66 MHz (see Engine behavior notes)                                    |
| `tACLK` | CLK to output delay                    | **2 ns min / 5.5 ns max**                                                                         |
| `tCHD`  | CE# hold from CLK rising               | **3.0 ns** min (pkg); for last-byte latch before read terminate, prefer `**tCHD > tACLK + tCLK`** |
| `tRST`  | After Reset command before next cmd    | **50 ns** min                                                                                     |


**V1 implication:** raise CE# after every short txn (1-byte data or 11-byte TCD fetch). That naturally satisfies `tCEM` and Linear Burst page rules without a dedicated slicer. With **SCK = clk/2**, wall-clock CE# low time for a given beat count is longer than a full-rate-SCK design; at V1 `N=1` this is still far under `tCEM`. Recompute failing-`N` thresholds if the buffer deepens. Detail: `[descriptor-fsm.md](descriptor-fsm.md)`.

On **abort** (D14): complete the in-flight QPI transaction (do not tear mid-command), then raise CE# and return control to idle.

## Practical clocks (D16)

- Design / demoboard **system `clk`:** **66 MHz**
- Engine **SCK:** **clk/2** (≈ 33 MHz) via toggle FF; idle low when disabled
- RX sample: **rising** edge of SCK (registered in `clk` domain)
- DLL-style eye training: V1 non-goal
- Phase 3: re-check `tACLK` / board / TT against **33 MHz SCK** rising-edge before shuttle freeze - checklist: `[../timing.md](../timing.md)`

Command frequency footnotes (APS6404L class):

- Fast Read Quad `0xEB` / many writes: up to 133/109 MHz Wrap32 or **84 MHz** Linear Burst page-cross (device max; ASIC SCK is lower)
- QPI Fast Read `0x0B`: max 66 MHz - **not used by ASIC**
- Enter/Exit Quad, Reset: MCU pass-through only

## V1 ASIC opcode set (D17)

QPI: read `**0xEB**`, write `**0x02**`. Nothing else.

**Flash:** no ASIC opcodes in V1. MCU uses pass-through.

## Related

- Limits: `[../limitations.md](../limitations.md)`
- Post-RTL timing checklist: `[../timing.md](../timing.md)` / `[../../../llm/11-timing-analysis.md](../../../llm/11-timing-analysis.md)`
- FSM consumer: `[descriptor-fsm.md](descriptor-fsm.md)`
- Agent detail: `[../../../llm/05-qspi-psram.md](../../../llm/05-qspi-psram.md)`
- Closed: Q2 / Q8 → D17; Q9 → D16. Open: `[../../../llm/08-open-questions.md](../../../llm/08-open-questions.md)`
