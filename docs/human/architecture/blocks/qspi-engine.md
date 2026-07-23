# QSPI Engine

Status: skeleton. **QPI-only ASIC path (D15/D17):** Fast Read Quad `0xEB`, Write `0x02`. Clock **84 MHz**, rising-edge RX (D16). MCU owns enter/exit QPI. CS mux for RAM A/B is in scope (D11); flash opcodes out of V1.

## Role

Bit-level QSPI master used by the descriptor FSM:

- QPI command, address, dummy (6 wait for `0xEB`), data phases for all DMA reads/writes
- Burst holds vs CE# high refresh windows
- Bidirectional SIO direction control (`uio_oe` per phase while ASIC is bus master)
- **RAM A / RAM B chip-select mux** (never both low; flash CS never asserted by ASIC in V1)
- Read sampling: rising-edge of SCK at **84 MHz** (D16)

**Not in ASIC:** Enter Quad (`0x35`), Exit Quad (`0xF5`), Reset (`0x66`/`0x99`), Fast Read `0x0B`, any SPI data opcodes. MCU does mode bring-up / teardown via pass-through (D17).

QSPI has four bidirectional data lines reused for I/O, giving up to about **4x** throughput vs 1-bit SPI (half-duplex data phases, tighter timing).

While DMA is active, this block owns the per-pin `uio_oe` mask (SCK + selected RAM CS driven; flash CS OE forced off; SIO drive on cmd/addr/write, float on dummy/read). When the host interface is idle (`DONE`), the engine's OE contribution is forced off (`uio_oe=0`). Bus ownership phases: [`host-interface.md`](host-interface.md).

Hard CE# / clock limits are summarized in [`../limitations.md`](../limitations.md). Full opcode tables: [`../../../llm/05-qspi-psram.md`](../../../llm/05-qspi-psram.md). Payload per held CE# data phase is capped by on-chip buffer depth `N` (V1: `N=1`; depth-agnostic; D20) as well as `tCEM`.

### Cross-device transfers

Shared SIO bus ⇒ only one CE# low at a time. A→B (or B→A) is: read byte from src die, raise CE#, then write byte to dest die. Same APS6404L QPI opcodes on both dies. Device select comes from pointer MSBs (`ptr[23]`).

## SPI vs QPI (D15 / D17)

| Use | Mode / owner |
|---|---|
| TCD fetch / payload read (`0xEB`) / payload write (`0x02`) | **ASIC QPI** |
| Enter Quad (`0x35`), Exit Quad (`0xF5`), Reset (`0x66`/`0x99`) | **MCU pass-through only** |
| SPI data opcodes | **Not used by ASIC** |

ASIC expects both dies already in **QPI mode** before START.

## Transaction phases (QPI)

1. **Command** - 8-bit opcode (2 clocks at 4 bits/clock)
2. **Address** - 24-bit address (6 clocks)
3. **Wait / dummy** - 6 cycles for `0xEB`; float host data pins
4. **Data** - sample read data on rising SCK; 2 clocks per byte in quad mode

## Initialization sequence (MCU-owned, D17)

ASIC does not run this sequence. Firmware (pass-through while DONE) on each die:

1. Wait **>= 150 us** after power-up before issuing commands (`tPU`; CE# high)
2. Issue **Reset Enable** (`0x66`) then **Reset** (`0x99`) over standard SPI. After Reset, wait **`tRST` min 50 ns**
3. Send **Enter Quad Mode** (`0x35`) over 1-bit SPI
4. After DMA (optional): **Exit Quad** (`0xF5`) over QPI, or reset back to SPI

## Critical CE# / refresh rules

**WARNING:** every read/write must finish by raising **CE# high** to terminate the command and allow standby/refresh.

| Symbol | Meaning | Value (APS6404L class) |
|---|---|---|
| `tCEM` | Max CE# low pulse width | **4 us** extended grade / **8 us** standard grade |
| `tCPH` | Min CE# high between subsequent bursts | **18 ns** |
| `tACLK` | CLK to output delay | **2 ns min / 5.5 ns max** |
| `tCHD` | CE# hold from CLK rising | **3.0 ns** min (pkg); for last-byte latch before read terminate, prefer **`tCHD > tACLK + tCLK`** |
| `tRST` | After Reset command before next cmd | **50 ns** min |

**Design requirement:** slice long DMA bursts into CE#-high-bounded segments. Descriptor **11-byte** fetches should hold CE# across the burst; multi-kilobyte copies must not.

On **abort** (D14): complete the in-flight QPI transaction (do not tear mid-command), then raise CE# and return control to idle.

## Practical clocks (D16)

- Design / demoboard target: **84 MHz** QPI (within `tCEM`)
- RX sample: **rising** edge of SCK
- DLL-style eye training: V1 non-goal
- Phase 3: re-check `tACLK` / board / TT against 84 MHz rising-edge before shuttle freeze

Command frequency footnotes (APS6404L class):

- Fast Read Quad `0xEB` / many writes: up to 133/109 MHz Wrap32 or **84 MHz** Linear Burst page-cross
- QPI Fast Read `0x0B`: max 66 MHz - **not used by ASIC**
- Enter/Exit Quad, Reset: MCU pass-through only

## V1 ASIC opcode set (D17)

QPI: read **`0xEB`**, write **`0x02`**. Nothing else.

**Flash:** no ASIC opcodes in V1. MCU uses pass-through.

## Related

- Limits: [`../limitations.md`](../limitations.md)
- FSM consumer: [`descriptor-fsm.md`](descriptor-fsm.md)
- Agent detail: [`../../../llm/05-qspi-psram.md`](../../../llm/05-qspi-psram.md)
- Closed: Q2 / Q8 → D17; Q9 → D16. Open: [`../../../llm/08-open-questions.md`](../../../llm/08-open-questions.md)
