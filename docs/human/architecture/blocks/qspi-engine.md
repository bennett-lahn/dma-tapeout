# QSPI Engine

Status: skeleton. Init ownership and primary QPI read opcode remain open. **QPI is the DMA data path (D15).** CS mux for RAM A/B is in scope (D11); flash opcodes out of V1.

## Role

Bit-level SPI/QSPI master used by the descriptor FSM:

- Reset / enter-quad initialization policy (per PSRAM die as needed) - **SPI config only**
- QPI command, address, dummy, data phases for all DMA reads/writes
- Burst holds vs CE# high refresh windows
- Bidirectional SIO direction control (`uio_oe` per phase while ASIC is bus master)
- **RAM A / RAM B chip-select mux** (never both low; flash CS never asserted by ASIC in V1)
- Read sampling edge policy at chosen clock

QSPI has four bidirectional data lines reused for I/O, giving up to about **4x** throughput vs 1-bit SPI (half-duplex data phases, tighter timing).

While DMA is active, this block owns the per-pin `uio_oe` mask (SCK + selected RAM CS driven; flash CS OE forced off; SIO drive on cmd/addr/write, float on dummy/read). When the host interface is idle (`DONE`), the engine's OE contribution is forced off (`uio_oe=0`). Bus ownership phases: [`host-interface.md`](host-interface.md).

Hard CE# / clock limits are summarized in [`../limitations.md`](../limitations.md). Full opcode tables: [`../../../llm/05-qspi-psram.md`](../../../llm/05-qspi-psram.md).

### Cross-device transfers

Shared SIO bus ⇒ only one CE# low at a time. A→B (or B→A) is: read byte from src die, raise CE#, then write byte to dest die. Same APS6404L QPI opcodes on both dies. Device select comes from TCD `CTRL_FLAGS`.

## SPI vs QPI (D15)

| Use | Mode |
|---|---|
| TCD fetch / payload read / payload write | **QPI only** |
| Enter Quad (`0x35`), optional Reset (`0x66`/`0x99`) if ASIC-owned init | **SPI** (config / bring-up) |
| SPI data opcodes (`0x03`, SPI `0x0B`/`0x02`/`0x38`) | **Not used by ASIC** |

Every SPI opcode the ASIC emits must stay listed in `05-qspi-psram.md`.

## Transaction phases (QPI)

1. **Command** - 8-bit opcode (2 clocks at 4 bits/clock)
2. **Address** - 24-bit address (6 clocks)
3. **Wait / dummy** - float host data pins; device-specific wait for DRAM array
4. **Data** - sample on chosen clock edge; 2 clocks per byte in quad mode

## Initialization sequence (ownership open)

Open question: does init happen before pass-through to the MCU is enabled? (lean: probably yes if ASIC-owned; otherwise MCU does it via pass-through).

Planned steps whoever owns init:

1. Wait **>= 150 us** after power-up before issuing commands (`tPU`; CE# high)
2. Issue **Reset Enable** (`0x66`) then **Reset** (`0x99`) over standard SPI (or QPI if already in QPI). Datasheet requires Reset immediately after Reset Enable. After Reset, wait **`tRST` min 50 ns** before the next valid command
3. Send **Enter Quad Mode** (`0x35`) over 1-bit SPI

Device powers up in SPI mode (Linear Burst default). Exit Quad (`0xF5`) is the clean QPI->SPI return without a full reset.

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

## Practical clocks

- **66 MHz** in SPI (config only)
- **84 MHz** in QPI linear burst (within `tCEM`)

At ~84 MHz, `tACLK` max ~5.5 ns eats rising-edge sample margin; prefer lower clock and/or **falling-edge RX sample**. DLL-style eye training is a V1 non-goal.

Command frequency footnotes (APS6404L class):

- SPI Read `0x03`: max 33 MHz (MCU/pass-through only; not ASIC DMA)
- QPI Fast Read `0x0B`: max **66 MHz** (4 wait cycles)
- Fast Read Quad `0xEB` / many writes: up to 133/109 MHz Wrap32 or **84 MHz** Linear Burst page-cross
- Enter/Exit Quad, Reset Enable/Reset, Wrap Toggle: command-only, up to 133 MHz class

## Lean V1 ASIC opcode set

SPI config (if ASIC-owned init): `0x66`, `0x99`, `0x35`. QPI data: one read (`0x0B` preferred until clock freeze), write `0x02`, strongly consider Exit Quad `0xF5`. Defer Wrap Toggle `0xC0` and Read ID `0x9F` to firmware/pass-through unless needed.

**Flash:** no ASIC opcodes in V1. Super-stretch only (read first; write maybe). MCU uses pass-through.

## Related

- Limits: [`../limitations.md`](../limitations.md)
- FSM consumer: [`descriptor-fsm.md`](descriptor-fsm.md)
- Agent detail: [`../../../llm/05-qspi-psram.md`](../../../llm/05-qspi-psram.md)
- Open: [`../../../llm/08-open-questions.md`](../../../llm/08-open-questions.md) (Q2, Q8 remainder, Q9)
