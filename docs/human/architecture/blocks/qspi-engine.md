# QSPI Engine

Status: implemented in `qspi_engine.sv` (L0/L1 exercised through M3). **QPI-only ASIC path (D15/D17):** Fast Read Quad `0xEB`, Write `0x02`. System **`clk` 66 MHz** (D16); engine **SCK = clk/2** (registered toggle). Rising-edge RX on SCK. MCU owns enter/exit QPI. CS mux for RAM A/B is in scope (D11); flash opcodes out of V1.

## Role

Bit-level QSPI master used by the descriptor FSM:

- QPI command, address, dummy (6 wait for `0xEB`), data phases for all DMA reads/writes
- Bidirectional SIO direction control (`uio_oe` per phase while ASIC is bus master)
- **RAM A / RAM B chip-select mux** (never both low; flash CS parked high / never asserted low by ASIC in V1)
- **SCK generation:** registered toggle when enabled → **SCK = clk/2**; idle low when disabled (no combo mux/gate of `clk` onto the pad)
- Read sampling: capture `sio_in` into `rdata` on detected **rising SCK** (clk-domain); pulse `rdata_valid` one `clk`

**Not in ASIC:** Enter Quad (`0x35`), Exit Quad (`0xF5`), Reset (`0x66`/`0x99`), Fast Read `0x0B`, any SPI data opcodes. MCU does mode bring-up / teardown via pass-through (D17).

QSPI has four bidirectional data lines reused for I/O, giving up to about **4x** throughput vs 1-bit SPI (half-duplex data phases, tighter timing).

`uio_oe` is **arbitrated by the descriptor FSM** as a **bus keeper** (D26): while `rst_n && ~BUS_GNT`, ASIC drives CS high and SCK low (including IDLE and between transactions); the engine's own SIO mask drives cmd/addr/write, floats for **dummy/read**, and stays floated through post-CE# **`tHZ`** before park reclaim of SIO don't-care. On `BUS_GNT` or asserted active-low reset (`rst_n=0`), all shared OE is forced off. Board **10 kΩ** CS pull-ups cover reset / pre-enable. Ownership matrix: [`host-interface.md`](host-interface.md); arbiter: [`descriptor-fsm.md`](descriptor-fsm.md).

Hard CE# / clock limits are summarized in `[../limitations.md](../limitations.md)`. Full opcode tables: `[../../../llm/05-qspi-psram.md](../../../llm/05-qspi-psram.md)`. With V1 buffer depth `N=1` (and 11-byte TCD fetch), each CE# pulse is short enough that `**tCEM` and Linear Burst one-page-cross limits are not binding** - no CE# refresh timer or page slicer required (see `[descriptor-fsm.md](descriptor-fsm.md)`). Revisit if `N` grows (D20).

### Cross-device transfers

Shared SIO bus ⇒ only one CE# low at a time. A→B (or B→A) is: read byte from src device, raise CE#, then write byte to dest device. Same APS6404L QPI opcodes on both devices. Device select comes from `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`; D24), not from `ptr[23]`.

## SPI vs QPI (D15 / D17)


| Use                                                            | Mode / owner              |
| -------------------------------------------------------------- | ------------------------- |
| TCD fetch / payload read (`0xEB`) / payload write (`0x02`)     | **ASIC QPI**              |
| Enter Quad (`0x35`), Exit Quad (`0xF5`), Reset (`0x66`/`0x99`) | **MCU pass-through only** |
| SPI data opcodes                                               | **Not used by ASIC**      |


ASIC expects both devices already in **QPI mode** before START.

## Transaction phases (QPI)

1. **Command** - 8-bit opcode (2 SCK at 4 bits/SCK)
2. **Address** - **24-bit** phase on the wire (6 SCK). APS6404L consumes `A[22:0]` only; the MSB of this phase is don't-care (`addr[23]` may be any value; D35). Device/CS comes from `device_sel`, not that MSB.
3. **Wait / dummy** - 6 SCK for `0xEB`; float host data pins. No wait for write.
4. **Data** - sample read data on rising SCK; 2 SCK per byte in quad mode

Phase lengths are counted in **SCK beats**. Pad states (`CS_ON`, `SCLK_OFF`, `CS_OFF`, `IDLE`) advance on **`clk`**.

## Descriptor FSM interface (D21)

Engine is a **transaction slave** of the descriptor FSM. Request is **not** a TCD; TCD fields stay in the FSM. Types: `qspi_pkg` in [`../../../../src/types.svh`](../../../../src/types.svh). RTL: [`../../../../src/qspi_engine.sv`](../../../../src/qspi_engine.sv).

Start legality is `~busy`. Write/read length is entirely from `byte_len` (engine counts `2 * byte_len` SCK beats, then `SCLK_OFF` → `CS_OFF` → `IDLE`). Engine does **not** latch the request: FSM holds `{cmd, addr, device_sel, byte_len}` from `txn_valid` until `busy` falls. Engine never stalls SCK/CE# for the FSM.

### Port contract

| Signal | Dir | Type / width | Contract |
|---|---|---|---|
| `clk` | in | 1 | System clock (**66 MHz**). |
| `rst_n` | in | 1 | Sync **active-low** reset. |
| `txn_valid` | in | 1 | **1-`clk` pulse** to start; legal **only when `~busy`**. |
| `cmd` | in | `logic [7:0]` (same width as `qspi_cmd_t`) | V1: `QSPI_CMD_FAST_READ` (`0xEB`) or `QSPI_CMD_WRITE` (`0x02`). Packed `logic` so the Tiny Tapeout wrapper can stay free of `qspi_pkg` for yowasp. Hold until `!busy`. |
| `addr` | in | `qspi_addr_t` `[23:0]` | Full 24-bit QPI address phase. Device uses `addr[22:0]` as `A[22:0]`; **`addr[23]` don't-care** (D35). Hold until `!busy`. |
| `device_sel` | in | `logic` (same width as `qspi_device_sel_t`) | `QSPI_PSRAM0` / `QSPI_PSRAM1` from `CTRL_FLAGS` device bits; steers `ram_*_cs_n`. Packed `logic` for the same wrapper constraint. Hold until `!busy`. |
| `byte_len` | in | `qpi_byte_len_t` | Payload bytes this CE# (FETCH=`QPI_TCD_BYTES`, data=`k` ≤ `N`). `QPI_BYTE_LEN_W = $clog2(QPI_MAX_BYTES + 1)`, `QPI_MAX_BYTES = max(DMA_BUF_DEPTH_MAX, QPI_TCD_BYTES)`. Hold until `!busy`. |
| `wdata` | in | `[3:0]` | Write nibble. **Must be valid on the `txn_valid` cycle**. When `wdata_next` asserts, the **next** nibble must already be on `wdata` **before the next `clk` cycle** (same-cycle response) so the engine has setup time into the SPI/SIO path for the following rising SCK. |
| `busy` | out | 1 | High while not `IDLE` (in flight through CE# complete). Start qualifier; OE reclaim / BUS_GNT wait for 0. |
| `rdata` | out | `[3:0]` | Last captured read nibble (held between captures). |
| `rdata_valid` | out | 1 | **1-`clk` pulse** with new `rdata` on each rising SCK in `READ_DATA`. FSM always sinks. Exactly `2 * byte_len` pulses per read. |
| `wdata_next` | out | 1 | **1-`clk` pulse** on **falling SCK** iff another nibble is needed to finish the active write. Exactly `2 * byte_len - 1` pulses per write; never asserts after the final nibble or outside that transaction. FSM must place the next `wdata` nibble on the bus before the next `clk` (see `wdata`). |
| `sio_in` | in | `[3:0]` | Pad SIO sample. |
| `sclk` | out | 1 | QSPI SCK: **clk/2** toggle while enabled; **0** in `IDLE` / `CS_ON` / `SCLK_OFF` / `CS_OFF`. |
| `ram_a_cs_n` | out | 1 | RAM A CE# (active low). Never both RAM CE#s low. |
| `ram_b_cs_n` | out | 1 | RAM B CE# (active low). |
| `sio_out` | out | `[3:0]` | Pad SIO drive data (cmd/addr/write). |
| `sio_oe` | out | `[3:0]` | Per-pin OE for this engine; driven for cmd/addr/write; floats in `WAIT` / `READ_DATA` and through post-CE# `tHZ` before park reclaim. Top / FSM parks CS+SCK while `~BUS_GNT` (flash CS never driven low). |

### Flows

**Read (FETCH / STATE_READ):**

1. When `~busy`, FSM presents request and pulses `txn_valid` (1 cycle).
2. Engine runs cmd/addr/dummy/data; on each data-phase rising SCK captures `sio_in` → `rdata` and pulses `rdata_valid`.
3. FSM sinks every `rdata_valid` pulse. Engine transfers exactly `2 * byte_len` nibbles, then end-pad / raise CE#.
4. When `busy` clears, FSM may start the next txn.

**Write (STATE_WRITE):**

1. When `~busy`, FSM presents request with the **first write nibble already on `wdata`**, and pulses `txn_valid` (same cycle).
2. Engine does not stage that nibble; FSM keeps it on `wdata` until `wdata_next`.
3. On falling SCK in the write data phase, the engine pulses `wdata_next` iff another nibble remains in the accepted transaction. The FSM must put that next nibble on `wdata` **before the next `clk` cycle** (combinational / same-cycle update) so setup time into the SPI controller is preserved for the following rising SCK.
4. Engine ends after exactly `2 * byte_len` SCK beats, then `SCLK_OFF` → `CS_OFF` → `IDLE`.
5. Because the first nibble accompanies `txn_valid`, the engine emits exactly `2 * byte_len - 1` `wdata_next` pulses. It never emits an extra pulse after the final nibble.
6. Engine never waits on the FSM after accept; never writes past `byte_len`.

### Rules

1. Engine does **not** latch the request; FSM must keep `{cmd, addr, device_sel, byte_len}` stable from `txn_valid` until `busy` is low.
2. On writes, the first nibble must be on `wdata` in the same cycle as `txn_valid`.
3. When `wdata_next` asserts, the next write nibble must be on `wdata` before the next `clk` cycle (setup into the SPI/SIO path for the next rising SCK). A registered update one cycle later is illegal.
4. Engine **never stalls** SCK/CE# waiting on the FSM (deterministic QPI).
5. FSM pulses `txn_valid` only while `~busy`.
6. Engine owns start/end CE# pad and ≥2-`clk` `tCPH` (via `CS_OFF` + `IDLE` before the next CE# fall). Never both `ram_a_cs_n` and `ram_b_cs_n` low; flash CS never driven low (parked high by top/FSM while `~BUS_GNT`).
7. FSM parks CS/SCK while `~BUS_GNT` (D26); grants SIO OE for live cmd/addr/write; float SIO for dummy/read and through `tHZ`; reclaim / release all OE when `BUS_GNT` (idle park resumes when grant falls). Do not start a new txn while `BUS_REQ` (D22).
8. `wdata_next` asserts iff the controller must provide another nibble for the current write. It must remain low after the final nibble and during every non-write phase.

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

ASIC does not run this sequence. Firmware (pass-through while DONE) on each device:

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

While active-low reset is asserted (`rst_n=0`, D23 kill path), the engine returns to idle with CE# high and the top level forces every shared `uio_oe` low. Board CS pull-ups keep the devices deselected until reset is deasserted and ASIC parking resumes. There is no soft-abort mid-txn path in V1.

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
