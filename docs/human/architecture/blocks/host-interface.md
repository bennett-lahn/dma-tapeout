# Host / Mode Control

Status: bus-ownership OE model + idle/START/DONE/abort/pass-through behavior frozen (D14). Pins: **START** = `ui_in[0]`, **ABORT** = `ui_in[1]`, **DONE** = `uo_out[0]` (D18). No head-pointer pins. QSPI on `uio` per system I/O map.

## Role

Own the boundary between MCU programming and ASIC DMA mastership:

- Mode switch: pass-through vs DMA master (via pad OE, not a second QSPI cable)
- Accept START / ABORT under TT pin limits (fixed head; no head vector)
- Drive status: done (= idle), error, debug mux

## How Tiny Tapeout pins work

Each design gets three I/O groups (plus `clk` / `rst_n` / `ena`):


| Port          | Direction     | Role                                |
| ------------- | ------------- | ----------------------------------- |
| `ui_in[7:0]`  | Input only    | Host control / config into the ASIC |
| `uo_out[7:0]` | Output only   | Status / DONE / DFT out of the ASIC |
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
RP2040 GPIO ──OE_mcu──┐
                      ├── uio / PMOD net ── PSRAM (CS, SCK, SIO)
ASIC pad     ──uio_oe─┘
```

- MCU and ASIC each have an independent output enable.
- TT `uio_oe` only controls the ASIC side; firmware must release RP2040 pins separately.
- Exactly one master may drive a net at a time. Both enabled → contention (undefined levels, possible pad damage).

QSPI PMOD map (frozen for V1 planning; matches TT community flash+PSRAM Pmod). Full table: `[../system.md](../system.md)` I/O section.


| `uio`      | Role                                                                          |
| ---------- | ----------------------------------------------------------------------------- |
| 0          | Flash CS (**ASIC OE always off** in V1; MCU may master flash in pass-through) |
| 1..2, 4..5 | SIO0..3                                                                       |
| 3          | SCK                                                                           |
| 6          | RAM A CS (DMA endpoint)                                                       |
| 7          | RAM B CS (DMA endpoint)                                                       |


Control plane:


| Pin                         | Assignment                           |
| --------------------------- | ------------------------------------ |
| `ui_in[0]`                  | **START**                            |
| `ui_in[1]`                  | **ABORT**                            |
| `uo_out[0]`                 | **DONE** (= ASIC idle)               |
| `ui_in[7:2]`, `uo_out[7:1]` | Reserved (status/DFT - packing open) |




## Host protocol (V1 freeze / D14 / D18 / D19)


| Rule             | Behavior                                                                                      |
| ---------------- | --------------------------------------------------------------------------------------------- |
| Idle             | Wait for START; **DONE** asserted; pass-through on (`uio_oe=0`)                               |
| START from idle  | Leave idle; deassert DONE; disable pass-through; seize bus; fetch TCD at `0x000000` / PSRAM 0 |
| START while busy | **Ignored** until back in idle                                                                |
| Quit TCD         | `CTRL_FLAGS.QUIT=1` after fetch → return to idle (no execute)                                 |
| DONE             | High whenever idle (including after reset, before first run)                                  |
| Pass-through     | Enabled iff DONE; disabled while not idle                                                     |
| Abort            | Finish **current QPI transaction**, then idle (DONE + pass-through)                           |




### Signals checklist


| Signal                  | Status                           |
| ----------------------- | -------------------------------- |
| `clk` / `rst_n` / `ena` | TT standard                      |
| START (`ui_in[0]`)      | Frozen index + behavior          |
| ABORT (`ui_in[1]`)      | Frozen index + behavior          |
| DONE (`uo_out[0]`)      | Frozen index + behavior (= idle) |
| ERROR / ACTIVE / DFT    | Optional; `uo_out[7:1]` open     |




## Pin configuration by control phase

Rule for every handoff: **release before seize**.

### Phase 0 - Reset / safe default


| Actor | Configuration                                                       |
| ----- | ------------------------------------------------------------------- |
| ASIC  | Idle: `uio_oe = 8'h00`; DONE high; `uio_out` don't-care or tied low |
| MCU   | May own bus once ASIC is known idle / DONE                          |
| PSRAM | CE# should idle high once power-up wait is done                     |


After `rst_n` deasserts, ASIC must come up idle with all QSPI-related `uio_oe` bits clear.

### Phase 1 - Idle / MCU pass-through (programming)


| Actor | Configuration                                                                                       |
| ----- | --------------------------------------------------------------------------------------------------- |
| ASIC  | DONE high; `uio_oe = 8'h00` on all QSPI pins. May still **read** `uio_in` if useful; must not drive |
| MCU   | Owns the bus: drive CS/SCK; drive or float SIO per SPI/QSPI phase in firmware                       |
| PSRAM | Sees MCU as sole master                                                                             |


Used to write TCD chains, stage payloads, talk to **flash or either PSRAM**, run Read ID / reset / Enter Quad / Exit Quad (D17; MCU-owned), and later read DMA results.

### Phase 2 - Handoff MCU → ASIC (START)

Ordered sequence:

1. MCU finishes any in-flight QSPI transaction and drives CE# high.
2. MCU sets its QSPI GPIOs to input / high-Z (firmware OE off).
3. MCU asserts **START** on `ui_in[0]` while DONE is high.
4. ASIC samples START, leaves idle (DONE low), then raises `uio_oe` only on pins it will drive.



### Phase 3 - DMA master (ASIC execution)


| Actor | Configuration                                                                                                                                                    |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MCU   | QSPI GPIOs remain high-Z for the whole DMA run. Host may still drive `ui_in` strobes (abort) and read `uo_out`                                                   |
| ASIC  | Masters QSPI: `uio_oe` high for SCK and the **active RAM CS** (A or B) while that txn is live; **flash CS OE stays 0**; SIO OE follows transaction phase (below) |
| PSRAM | Sees ASIC as sole master on the selected die                                                                                                                     |


START is ignored in this phase. ABORT requests a clean exit after the current QPI transaction.

#### Sub-phases: SIO `uio_oe` while ASIC is master

CS (selected RAM) and SCK stay driven (`uio_oe = 1`) for the active CE# low window. Flash CS never driven by ASIC. SIO0..3 change with the QSPI engine:


| QSPI phase                     | SIO `uio_oe` | ASIC uses                      |
| ------------------------------ | ------------ | ------------------------------ |
| Command / address / write data | `1` (drive)  | `uio_out` nibbles/bits         |
| Dummy / wait                   | `0` (listen) | ignore or don't-care `uio_out` |
| Read data                      | `0` (listen) | sample `uio_in`                |


Data path is **QPI** (`0xEB` / `0x02`; D15/D17). ASIC emits no SPI and no Enter/Exit Quad; MCU must leave both dies in QPI before START.

RTL shape (conceptual):

```systemverilog
assign uio_out = qspi_out;
// Descriptor FSM arbitrates OE: default FSM (pass-through / release);
// grant to QSPI engine only while a txn is live.
assign uio_oe  = fsm_grant_oe_to_qspi ? qspi_oe : fsm_oe; // fsm_oe typically 8'h00
// qspi_oe[flash_cs] = 0 always (V1)
// qspi_oe[ram_a_cs] / qspi_oe[ram_b_cs] = 1 only for the selected die, never both
// qspi_oe[SCK] = 1 while master;
// qspi_oe[SIO*] = 1 only in cmd/addr/write phases
```



### Phase 4 - Handoff ASIC → MCU (IDLE / DONE)

Ordered sequence:

1. ASIC hits quit TCD or completes abort path; finishes any in-flight QPI txn; raises CE# high.
2. ASIC enters idle: clears QSPI `uio_oe` to `8'h00` (pass-through on).
3. ASIC asserts **DONE** on `uo_out[0]`.
4. MCU sees DONE, then re-enables its QSPI GPIOs as master and may read PSRAM.

No host ACK required for restore (D14). Illegal: MCU drives bus while not DONE.

### Phase 5 - Illegal / contention

If MCU and ASIC both enable drivers on the same `uio` net:

- Levels are undefined; pads can source/sink large current
- Reads are unreliable; long-term reliability risk

Mitigations:

1. ASIC default and idle: `uio_oe = 0`
2. Firmware: Hi-Z before START; drive only while DONE
3. Verification: cover double-drive and "host drives during DMA" cases
4. Optional sticky error if activity is detected on the bus while ASIC believes it is master (best-effort; not a hard interlock)



## Planned behavior (summary)

- Idle: DONE high; ASIC `uio_oe=0`; MCU QSPI reaches **both PSRAMs and flash**
- On START (only from idle): DONE low; ASIC seizes bus (RAM A/B CS mux; never flash) and runs descriptor engine
- On quit TCD or abort completion: idle again (DONE, pass-through)
- Fixed head: first fetch always `0x000000` / PSRAM 0



## Open

- Error sticky bits and other illegal host sequences
- DFT / status mux on `uo_out[7:1]`



## Related

- Modes: `[../system.md](../system.md)`
- Descriptor FSM (`uio_oe` arbiter): `[descriptor-fsm.md](descriptor-fsm.md)`
- QSPI engine (phase OE when granted): `[qspi-engine.md](qspi-engine.md)`
- Agent detail: `[../../../llm/03-architecture.md](../../../llm/03-architecture.md)`
- Open questions: `[../../../llm/08-open-questions.md](../../../llm/08-open-questions.md)` (Q3 remainder, Q12)
- Decisions: D14 / D15 / D18 / D19 in `[../../../llm/07-decision-log.md](../../../llm/07-decision-log.md)`

