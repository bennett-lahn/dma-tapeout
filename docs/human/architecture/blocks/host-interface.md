# Host / Mode Control

Status: implemented in `top.v` + `sys_controller.sv` (exercised by M2 reset/bus and ownership suites). Bus-ownership OE model + idle/START/DONE frozen (D14/D18); kill via **`rst_n`** (D23). Pass-through is **request/grant** (`BUS_REQ` / `BUS_GNT`, D22). ASIC is **bus keeper** while `rst_n=1 && ~BUS_GNT`; while `rst_n=0`, shared OE is off and MCU drive of QSPI nets is legal (D26; board 10 kΩ CS pull-ups hold CE# unless MCU selects). Live pins: **START** = `ui_in[0]`, **BUS_REQ** = `ui_in[2]`, **DONE** = `uo_out[0]`, **BUS_GNT** = `uo_out[1]`. Unused `ui_in[1]`, `ui_in[7:3]`, and `uo_out[7:2]` tied 0 (D34). No head-pointer pins. QSPI on `uio` per system I/O map.

## Role

Describe the host-facing behavior of the integrated `sys_controller` module. Host control and descriptor sequencing are not separate RTL blocks. Together they own the boundary between MCU programming and ASIC DMA mastership:

- Mode switch: pass-through vs DMA master (via pad OE + `BUS_REQ`/`BUS_GNT`, not a second QSPI cable)
- Accept START under TT pin limits (fixed head; no head vector)
- Drive status: **DONE** (= idle) and **BUS_GNT** only

## Top-level host sync

MCU host pins (`ui_in`) are **asynchronous** to the design `clk`. The **top-level** TT module synchronizes them into `clk` before `sys_controller` uses them. After synchronizing START as a level, the top level detects its rising edge and supplies `sys_controller.start` as a **one-`clk` pulse**. `BUS_REQ` remains a synchronized level.

| Raw pad | Synced use |
|---|---|
| `ui_in[0]` START | Synchronize, then rising-edge detect into a one-`clk` pulse |
| `ui_in[2]` BUS_REQ | Synchronized level into `sys_controller`; drives yield / grant behavior |

- Prefer a **two-flop** synchronizer per bit before qualification.
- `sys_controller` sees a one-cycle START pulse and a synchronized BUS_REQ level; it never samples raw `ui_in`.
- A START edge presented while busy or while `BUS_REQ` is high is ignored and is not queued. Firmware must deassert and reassert START to issue another command (drop `BUS_REQ` / wait `BUS_GNT=0` first if a request was held).
- Firmware must hold raw START long enough for the level synchronizer to capture it, then deassert it before issuing a later START.
- After pulsing START, do not assert `BUS_REQ` until `DONE` falls. That ACK closes the START/`BUS_REQ` sync race in IDLE (discarded START vs accept-then-stall at `NEW_FETCH`).
- **DFF cost:** ~2 per synchronized bit plus one delayed-START flop for edge detection (START/BUS_REQ ≈ 5 DFFs total).

## How Tiny Tapeout pins work

Each design gets three I/O groups (plus `clk` / `rst_n` / `ena`):


| Port          | Direction     | Role                                |
| ------------- | ------------- | ----------------------------------- |
| `ui_in[7:0]`  | Input only    | Host control / config into the ASIC |
| `uo_out[7:0]` | Output only   | Status out of the ASIC              |
| `uio[7:0]`    | Bidirectional | Shared QSPI bus to the PSRAM PMOD   |


Bidirectional pins are **not** a single RTL net. For each bit the top module exposes three wires that collapse to one package pin:


| RTL signal   | Direction from design | Meaning                                               |
| ------------ | --------------------- | ----------------------------------------------------- |
| `uio_in[i]`  | input                 | Level sensed on the pad                               |
| `uio_out[i]` | output                | Value to drive when enabled                           |
| `uio_oe[i]`  | output                | `1` = drive pad from `uio_out`; `0` = high-Z (listen) |


`uio_oe = 0` turns off the ASIC **output driver**. The pad is not galvanically removed: the input buffer and pad parasitics remain, but the ASIC is no longer a driver on that net.

### Shared-bus pass-through (this project)

On the demoboard, the RP2040 GPIOs, the selected ASIC's `uio` pads, and the QSPI PMOD sit on the **same physical nets**. Pass-through is therefore **OE arbitration**, not a pin-mux that copies MCU QSPI from `ui_in` out to a separate PSRAM port.

```
RP2 MCU GPIO ──OE_mcu──┐
                       ├── uio / PMOD net ── PSRAM (CS, SCK, SIO)
ASIC pad      ──uio_oe─┘
```

- MCU and ASIC each have an independent output enable.
- TT `uio_oe` only controls the ASIC side; firmware must release MCU pins separately.
- Exactly one master may drive a net at a time when levels disagree. Both enabled → contention (undefined levels, possible pad damage). Brief overlap on idle levels (CS high / SCK low) is benign if it occurs.
- Firmware may enable MCU QSPI drivers while **`BUS_GNT=1`** or while **`rst_n=0`** (D22 / D26). While `rst_n=1` and `~BUS_GNT`, MCU stays Hi-Z and the ASIC is bus keeper.
- **Board:** the PMOD / demoboard path has a **10 kΩ pull-up on each CS** (flash, RAM A, RAM B). Those pull-ups keep CE# high during reset / power-up / pre-mux windows unless the MCU selects a device. While the design is live (`rst_n=1`) and `~BUS_GNT`, the ASIC is the active bus keeper (D26).

QSPI PMOD map (frozen for V1; matches TT community flash+PSRAM Pmod). Full table: `[../system.md](../system.md)` I/O section.


| `uio`      | Role                                                                          |
| ---------- | ----------------------------------------------------------------------------- |
| 0          | Flash CS (**park high** while `~BUS_GNT`; never driven low by ASIC; MCU may master flash under grant) |
| 1..2, 4..5 | SIO0..3                                                                       |
| 3          | SCK                                                                           |
| 6          | RAM A CS (DMA endpoint; park high while `~BUS_GNT` when not the active die)   |
| 7          | RAM B CS (DMA endpoint; park high while `~BUS_GNT` when not the active die)   |


Control plane:


| Pin                         | Assignment                           |
| --------------------------- | ------------------------------------ |
| `ui_in[0]`                  | **START**                            |
| `ui_in[1]`                  | Unused (tied 0; D34)                 |
| `ui_in[2]`                  | **BUS_REQ** (MCU wants `uio`)        |
| `ui_in[7:3]`                | Unused (tied 0; D34)                 |
| `uo_out[0]`                 | **DONE** (= ASIC idle)               |
| `uo_out[1]`                 | **BUS_GNT** (MCU may drive `uio`)    |
| `uo_out[7:2]`               | Unused (tied 0; D34)                 |




## Host protocol (V1 freeze / D14 / D18 / D19 / D22 / D23 / D26 / D34)


| Rule             | Behavior                                                                                                      |
| ---------------- | ------------------------------------------------------------------------------------------------------------- |
| Idle             | Wait for START; **DONE** asserted; ASIC **parks** bus while `~BUS_GNT` (CS high / SCK low; D26)              |
| START from idle  | One-`clk` post-sync rising-edge pulse accepted only if **`~BUS_REQ`** (and thus `~BUS_GNT`); leave idle; deassert DONE; keep bus; fetch head TCD |
| START while busy / REQ | Pulse is **ignored and not queued**; a later command requires a new rising edge after IDLE returns (and after `BUS_GNT` low if REQ was held) |
| START then BUS_REQ | Firmware must not raise `BUS_REQ` after/with START until **DONE has fallen** (START-accept ACK); avoids IDLE priority discarding START |
| Quit TCD         | `CTRL_FLAGS.QUIT=1` after fetch → return to idle (no execute); next START fetches `0x000000` / PSRAM 0 again |
| DONE             | High whenever idle (including after reset, before first run)                                                  |
| Pass-through     | MCU may drive `uio` while **`BUS_GNT=1`** or **`rst_n=0`** (D22/D26); idle alone is not a drive permit |
| BUS_REQ          | MCU priority: finish current QPI txn (atomic), then **release** `uio_oe` + assert `BUS_GNT`; no new DMA txn while REQ   |
| BUS_GNT release  | After MCU Hi-Z and drops `BUS_REQ`, ASIC drops `BUS_GNT` and **resumes parking**; if not idle, DMA may resume |
| Bus keeper       | While `rst_n=1` and `~BUS_GNT`, ASIC drives all CS high and SCK low; SIO don't-care in park after `tHZ`, float on dummy/read and through `tHZ` (D26) |
| Kill / reset drive | Assert **`rst_n`** to stop a runaway DMA (D23). While `rst_n=0`, MCU drive is legal (D26); board pull-ups hold CE# unless MCU selects |



### Signals checklist


| Signal                  | Status                           |
| ----------------------- | -------------------------------- |
| `clk` / `rst_n` / `ena` | TT standard; `rst_n` kills DMA   |
| START (`ui_in[0]`)      | Frozen index + behavior          |
| BUS_REQ (`ui_in[2]`)    | Frozen index + behavior (D22)    |
| DONE (`uo_out[0]`)      | Frozen index + behavior (= idle) |
| BUS_GNT (`uo_out[1]`)   | Frozen index + behavior (D22)    |
| Unused host bits        | `ui_in[1]`, `ui_in[7:3]`, `uo_out[7:2]` tied 0 (D34) |


### `sys_controller` RTL interface

RTL: [`../../../../src/rtl/sys_controller.sv`](../../../../src/rtl/sys_controller.sv). The module integrates host/mode control and the descriptor FSM. START is post-sync and rising-edge detected by the top module; BUS_REQ is a post-sync level. The QPI engine remains a separate submodule.


| Signal group | Direction | Contract |
|---|---|---|
| `start` | top → controller | One-`clk` command pulse; accepted only in IDLE with `~bus_req`. |
| `bus_req` | top → controller | Synchronized level; prevents a new QPI transaction and requests an atomic yield. |
| `done` | controller → pin | High while the integrated controller is idle, including an idle-origin BUS_REQ stall. |
| `bus_gnt` | controller → pin/control | High after the controller reaches STALL; the ASIC must **release** shared-bus OE (MCU may drive). |
| `qspi_busy`, `qspi_rdata_valid`, `qspi_rdata`, `qspi_wdata_next` | engine → controller | Transaction status and nibble-stream handshake. |
| `qspi_txn_valid`, `qspi_cmd`, `qspi_addr`, `qspi_device_sel`, `qspi_byte_len`, `qspi_wdata` | controller → engine | Complete QPI transaction request and write-data stream. |




## Pin configuration by control phase

Rule for every handoff: **release before seize**.

### Phase 0 - Reset / safe default


| Actor | Configuration                                                       |
| ----- | ------------------------------------------------------------------- |
| ASIC  | After `rst_n` deasserts with `~BUS_GNT`: park CS high / SCK low (D26); DONE high; `BUS_GNT` low until `BUS_REQ` |
| Board | **10 kΩ** pull-ups on flash / RAM A / RAM B CS hold CE# high during reset and before the design is enabled unless MCU selects |
| MCU   | May drive QSPI while `rst_n=0` (D26); while `rst_n=1`, Hi-Z until `BUS_GNT` |
| PSRAM | CE# should idle high once power-up wait is done                     |


While active-low reset is asserted (`rst_n=0`), the top level forces all shared `uio_oe` bits low and the MCU **may** master the shared QSPI nets (D26; TT mux often holds a deselected design in reset). Board CS pull-ups keep devices deselected until the MCU drives a CS low. After `rst_n` deasserts, ASIC resumes parking unless `BUS_GNT` is high. Asserting reset mid-run is the V1 kill path (D23); there is no soft-abort pin (D34).

### Phase 1 - MCU pass-through (programming)


| Actor | Configuration                                                                                       |
| ----- | -------------------------------------------------------------------                                 |
| ASIC  | `uio_oe = 0` while `BUS_GNT` high (`BUS_REQ` idle: immediate; mid-DMA: after current QPI txn) |
| MCU   | Asserts `BUS_REQ`, waits for `BUS_GNT`, then owns the bus: drive CS/SCK; drive or float SIO per phase |
| PSRAM | Sees MCU as sole master                                                                             |


Used to write TCD chains, stage payloads, talk to **flash or either PSRAM**, run Read ID / reset / Enter Quad / Exit Quad (D17; MCU-owned), and later read DMA results. May also pause an active DMA run between atomic QPI transactions (D22).

### Phase 2 - Handoff MCU → ASIC (START)

Ordered sequence:

1. MCU finishes any in-flight QSPI transaction and drives CE# high.
2. MCU sets its QSPI GPIOs to input / high-Z (firmware OE off).
3. MCU deasserts **BUS_REQ**; waits for **BUS_GNT** low.
4. MCU asserts **START** on `ui_in[0]` long enough to cross the synchronizer while DONE is high and `~BUS_REQ`; the top level converts its rising edge to one `clk` pulse.
5. ASIC samples START, leaves idle (DONE low), and continues as bus keeper / DMA master (already parking while `~BUS_GNT`).
6. MCU must see **DONE low** before asserting `BUS_REQ` again (START-accept ACK; closes the IDLE START/`BUS_REQ` sync race).



### Phase 3 - DMA master (ASIC execution)


| Actor | Configuration                                                                                                                                                    |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MCU   | QSPI GPIOs remain high-Z unless / until `BUS_GNT` (or while `rst_n=0`). Host may still drive `ui_in` (START ignored while busy; `BUS_REQ`) and read `uo_out`; kill via `rst_n` |
| ASIC  | Masters QSPI when not yielding: parks or drives **all CS high / SCK** while `~BUS_GNT`; during a live txn, one RAM CS may go low; **flash CS stays high**; SIO OE follows phase (drive cmd/addr/write; float dummy/read and through post-CE# `tHZ`) |
| PSRAM | Sees ASIC as sole master on the selected device (when not granted to MCU)                                                                                           |


START is ignored in this phase. **BUS_REQ** pauses after the current QPI transaction (atomic), grants the bus, then resumes DMA when REQ drops (unless quit / `rst_n` also applies).

### Phase 4 - Handoff ASIC → MCU (BUS_REQ mid-run or after DONE)

1. ASIC finishes the in-flight QPI transaction (atomic).
2. ASIC releases all shared `uio_oe` and asserts **BUS_GNT**.
3. MCU waits for **BUS_GNT**, then enables its QSPI drivers.
4. When finished, MCU Hi-Z, drops **BUS_REQ**, waits for **BUS_GNT** low.
5. If DMA was paused (not quit), ASIC resumes when REQ clears.

After **DONE** (quit or end of programmed chain), same grant path applies for SPI dump / re-init.

## Contention hazards

If MCU and ASIC both enable drivers on the same `uio` net with disagreeing levels:

- Levels are undefined; pads can source/sink large current
- Reads are unreliable; long-term reliability risk

Mitigations:

1. ASIC parks while `~BUS_GNT`; releases fully only under grant / reset (D26)
2. Firmware: drive only while `BUS_GNT` or `rst_n=0`; Hi-Z before dropping `BUS_REQ` and before START
3. Verification: cover double-drive and "host drives without grant while out of reset" cases
4. Board 10 kΩ CS pull-ups limit CE# float during reset / pre-enable windows



## Planned behavior (summary)

- Idle: DONE high; ASIC parks bus while `~BUS_GNT` (D26); MCU uses `BUS_REQ`/`BUS_GNT` before driving **both PSRAMs and flash**
- On START (only from idle, `~BUS_REQ`): DONE low; ASIC continues as bus keeper / DMA master (RAM A/B CS mux; flash CS parked high) and runs the descriptor engine
- Mid-run `BUS_REQ`: finish current QPI txn, release OE, grant bus, pause DMA; resume parking + DMA when REQ cleared
- On quit TCD: idle again (DONE) with parking; next START refetches fixed head; grant still follows REQ
- Kill mid-run: `rst_n` only (D23/D34)
- Fixed head: every START fetch is `0x000000` / PSRAM 0
- Bad / out-of-range chains: firmware validates before START; undefined at runtime; recover with `rst_n` (D34)



## Related

- Firmware contract: `[../firmware.md](../firmware.md)`
- Modes: `[../system.md](../system.md)`
- Descriptor FSM (`uio_oe` arbiter + yield): `[descriptor-fsm.md](descriptor-fsm.md)`
- QSPI engine (phase OE when granted): `[qspi-engine.md](qspi-engine.md)`
- Agent detail: `[../../../llm/03-architecture.md](../../../llm/03-architecture.md)`
- Decisions: D14 / D15 / D18 / D19 / D22 / D23 / D26 / D34 in `[../../../llm/07-decision-log.md](../../../llm/07-decision-log.md)`
