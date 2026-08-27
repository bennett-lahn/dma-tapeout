# QSPI / PSRAM Context

This file captures protocol knowledge needed for the DMA memory engine. It is not a substitute for the APS6404L datasheet.

Local sources:

- PDF (authoritative): `docs/datasheets/pdfs/APS6404L_3SQR.pdf`
- Converted text: `docs/datasheets/md/APS6404L_3SQR.md`
- Conversion process: `docs/datasheets/README.md`
- Demoboard QSPI PMOD SPI bring-up guide: `docs/datasheets/pdfs/Using_QSPI_TinyTapeout.pdf` (MCU firmware SPI transport; see [`12-firmware.md`](12-firmware.md) / D30)

## PSRAM vs Flash (why PSRAM)

| | PSRAM | Flash |
|---|---|---|
| Volatility | Volatile | Non-volatile |
| Writes | Fast, byte-level, effectively unlimited endurance | Slow; erase-before-write; limited cycles |
| Role here | Working memory, descriptors, source/dest buffers during run | Not an ASIC DMA target (MCU pass-through only) |

PSRAM is internally DRAM with hidden refresh. From the host it behaves like SRAM-ish memory over SPI/QSPI, but **CE# timing interacts with refresh**.

## QSPI in one paragraph

QSPI extends SPI to a 4-bit bidirectional data bus (SIO0..SIO3) for higher throughput. Devices often boot in 1-bit SPI and must be switched into quad/QPI mode. Throughput can approach 4x single-bit SPI, at the cost of half-duplex data phases and tighter timing.

Typical pins:

- `CE#` / CS (active low)
- `CLK`
- `SIO0` (MOSI in 1-bit)
- `SIO1` (MISO in 1-bit)
- `SIO2`, `SIO3`

## Demoboard / PMOD parts

QSPI PMOD-class inventory:

| Part | Size | Role for this project |
|---|---|---|
| Winbond **W25Q128JV** | 128 M-bit QSPI Flash | On PMOD; **MCU pass-through only** in V1. ASIC never asserts flash CS. First-party TT QSPI Pmods **ship with Quad Enable already set** (D30); V1 firmware does not program flash QE. Super-stretch: ASIC flash read (maybe write). Datasheet: `docs/datasheets/` (JV is Dual/Quad SPI; true QPI is JV-M). |
| AP Memory **APS6404L-3SQR** ×2 | 64 M-bit QSPI PSRAM each | **Both** are first-class DMA working memory (TCDs, buffers, rings, logs; cross-device A↔B supported) |

Prefer PSRAM for descriptor DMA (byte writes, no erase). Flash remains available to firmware via idle pass-through without ASIC flash support.

## Target device notes (APS6404L-3SQR class)

From datasheet (APS6404L-3SQR Rev 2.3) and planning notes:

- 64 Mbit (8 MByte), VDD 2.7-3.6 V QSPI PSRAM
- Byte-addressable with `A[22:0]`
- Powers up in **SPI mode**, Linear Burst default, drive strength 50 ohm
- Power-up wait `tPU >= 150 us` with CE# high before any command
- After software reset: wait `tRST` (min 50 ns) before next valid command
- Soft reset over SPI or QPI: Reset Enable (`0x66`) then Reset (`0x99`) immediately after (datasheet: issue Reset immediately)
- Enter Quad Mode (`0x35`) over 1-bit SPI only; thereafter command/addr/data use 4-bit phases in QPI
- Exit Quad Mode (`0xF5`) is QPI-only and returns the device to SPI
- Linear burst crosses a 1K page at most once per CE# pulse and caps at **84 MHz** when crossing (device max; ASIC SCK is lower); Wrap32 (`0xC0` toggle) is optional and not the power-up default
- Clock policy (D16): design / demoboard **66 MHz `clk`**, **SCK=clk/2** (≈33 MHz); sample read data on **rising** SCK. Phase 3 must re-check `tACLK` / board / TT timing against this target before shuttle freeze
- Mode ownership (D17): **MCU** Enter/Exit Quad via pass-through; ASIC expects devices already in QPI before START. Sole ASIC QPI read = **`0xEB`**

### Full device command set (datasheet truth table)

Widths: S = serial (1-bit), Q = quad (4-bit). Wait = dummy cycles before data-out.

| Command | Code | SPI (QE=0) | QPI (QE=1) | Notes |
|---|---|---|---|---|
| Read | `0x03` | Cmd/Addr/Data S; wait 0; max 33 MHz | N/A | SPI-only slow read |
| Fast Read | `0x0B` | S/S wait 8; up to 133/84* | Q/Q wait 4; max 66 MHz | Primary slower QPI read |
| Fast Read Quad | `0xEB` | Cmd S, Addr/Data Q, wait 6 | Q/Q wait 6; up to 133/84* | Faster QPI read, more dummies |
| Write | `0x02` | S/S wait 0 | Q/Q wait 0 | Valid in both modes |
| Quad Write | `0x38` | Cmd S, Addr/Data Q, wait 0 | Same as `0x02` in QPI | SPI quad-write form; redundant once in QPI |
| Enter Quad Mode | `0x35` | Cmd S only | N/A | SPI -> QPI |
| Exit Quad Mode | `0xF5` | N/A | Cmd Q only | QPI -> SPI |
| Reset Enable | `0x66` | Cmd S | Cmd Q | Must be followed immediately by Reset |
| Reset | `0x99` | Cmd S | Cmd Q | Returns device to SPI standby (power-up-like) |
| Wrap Boundary Toggle | `0xC0` | Cmd S | Cmd Q | Toggles Linear Burst <-> Wrap32 |
| Read ID | `0x9F` | Cmd/Addr S, EID out; max 33 MHz | N/A | SPI-only bring-up / KGD check |

\* Datasheet frequency footnotes: wrap32 can go higher than linear page-crossing bursts; see PDF section 8.5 / 14.6.

### ASIC-supported commands (planned V1 / D15)

**Policy (D15 / D17):** QPI for all ASIC DMA **data** read/write. ASIC emits **no SPI** and **no** Enter/Exit Quad / reset. MCU owns mode bring-up and teardown via pass-through.

These are the opcodes the **on-chip QSPI engine** emits (not MCU pass-through).

#### MCU pass-through only (not ASIC)

| Role | Opcode | Mode | V1 status |
|---|---|---|---|
| Reset Enable | `0x66` | SPI (and optionally QPI) | **MCU** - init / recovery |
| Reset | `0x99` | SPI (and optionally QPI) | **MCU** - must immediately follow `0x66` |
| Enter Quad Mode | `0x35` | SPI | **MCU** - SPI → QPI before START (D17 precondition) |
| Exit Quad Mode | `0xF5` | QPI | **MCU** - QPI → SPI after DONE if firmware needs SPI |
| Read ID / SPI diagnostics | `0x9F`, SPI `0x03`/`0x0B`/… | SPI | **MCU** |

#### QPI data path (ASIC DMA)

| Role | Opcode | Mode | V1 status |
|---|---|---|---|
| Fast Read Quad | `0xEB` | QPI | **Required** - sole ASIC read (6 wait cycles; D17) |
| QPI Write | `0x02` | QPI | **Required** - prefer over `0x38` (equivalent in QPI) |

#### Other commands: include or not?

| Command | Recommendation | Why |
|---|---|---|
| Exit Quad Mode `0xF5` | **MCU only (D17)** | Cut from ASIC for simplicity; firmware exits after DONE if needed |
| Enter Quad / Reset `0x35`/`0x66`/`0x99` | **MCU only (D17)** | No ASIC SPI config FSM |
| Wrap Boundary Toggle `0xC0` | **Defer (not V1)** | Default Linear Burst matches long DMA copies |
| Fast Read `0x0B` | **Not in ASIC (D17)** | Sole read is `0xEB` (also over `0x0B` QPI max at 66 MHz) |

**V1 ASIC opcode set (frozen):** QPI read **`0xEB`** + write **`0x02`** only.

### Transaction phases (QPI)

1. **Command** - 8-bit opcode (2 clocks at 4 bits/clock)
2. **Address** - 24-bit address (6 clocks)
3. **Dummy / wait** - device-specific; required for fast reads so DRAM array can produce data
4. **Data** - 2 clocks per byte in quad mode

### Engine bit order and CE# sequencing

Human summary: `docs/human/architecture/blocks/qspi-engine.md` (Engine behavior notes).

1. **QPI bit order:** MSB-first within the byte; each clock drives one nibble on `SIO[3:0]` with **SIO[3] = MSB** of that nibble (SIO[0] = LSB). Upper nibble on the first SCK of the byte, lower nibble on the second.
2. **`tCPH` wait @ 66 MHz `clk`:** min CE# high is **18 ns**; one `clk` is ≈ 15.2 ns (short), two `clk` ≈ 30.3 ns (ok). After every read/write, keep CE# high for **≥ 2 `clk`** before the next CE# falling edge. Engine SCK is **clk/2** (≈33 MHz) when enabled.
3. **CE# / SCK padding:** assert CE# low for **one `clk`** with no SCK before the first SCK edge (`CS_ON`); after the last SCK edge, keep CE# low for **one `clk`** with no SCK (`SCLK_OFF`) before raising CE# (`CS_OFF`).
4. **SCK parked while deselected:** APS6404L-class devices define clocked behavior only while CE# is low; treat any SCK transition while every shared-bus chip-select (flash CS, RAM A CE#, RAM B CE#) is high as an erroneous SCK cycle, not a benign don't-care. This holds independent of which side of the shared bus, ASIC or MCU pass-through, currently owns drive. Verification IDs: `Q-SCKIDLE` / `CHK-PIN-SCK-PARK` (`docs/llm/verification/04-timing-in-sim.md`, `docs/llm/verification/06-checkers.md`).

### Refresh / CE# warning (critical)

All reads/writes must complete by returning `CE#` high to terminate the command and allow standby/refresh behavior. If a single CE# low pulse exceeds the device maximum, internal refresh can be blocked and memory contents can fail.

Key timing numbers (APS6404L Table 10 class):

| Symbol | Meaning | Value |
|---|---|---|
| `tCEM` | Max CE# low pulse width | **4 us** (extended grade) / **8 us** (standard grade) |
| `tCPH` | Min CE# HIGH between subsequent burst operations | **18 ns** |
| `tCSP` | CE# setup to CLK rising | 2.5 ns min |
| `tCHD` | CE# hold from CLK rising | 3.0 ns min (pkg) |
| `tACLK` | CLK to output delay | 2 ns min / **5.5 ns** max |
| `tHZ` | Chip disable to DQ high-Z | 5.5 ns max |
| `tRST` | End of Reset cmd to next valid cmd | 50 ns min |

Datasheet/notes also recommend, for latching the last read beat before termination: provide a longer CE# hold such that **`tCHD > tACLK + tCLK`**.

**Design requirement (device physics):** continuous CE# low must stay under `tCEM`, and Linear Burst may cross a 1K page at most once per CE# pulse. **V1 implementation:** tapeout buffer depth **`N=5`** plus 11-byte TCD fetch keep every CE# pulse far under both limits (same at **`N=1`**), so the engine does **not** need a CE# low-time counter or page-boundary slicer.

**Numeric thresholds @ 33 MHz SCK** (full-buffer hold in one CE# pulse; overhead = 14 SCK `0xEB` / 8 SCK `0x02`; 2 SCK/byte):

| Rule | Max safe `N` | First failing `N` |
|---|---|---|
| `tCEM` 4 us (≈132 SCK) | read 59 / write 62 | **60 / 63** |
| `tCEM` 8 us (≈264 SCK) | read 125 / write 128 | **126 / 129** |
| Linear Burst ≤1 page cross | 1025 (any align) | **1026** (worst align) |

Binding limit when enlarging `N` is **`tCEM`**, starting at **60**. Detail: `docs/human/architecture/blocks/descriptor-fsm.md`.

## Post-RTL timing checklist

After RTL is feature-complete, run the Phase 3 checks in `11-timing-analysis.md` (CE#↔SCK sequencing in sim; `tACLK` / setup-hold / board on STA + demoboard). Human pointer: `../human/architecture/timing.md`.

## High-speed read sampling note (D16)

**Frozen for V1:** system **`clk` 66 MHz**; engine **SCK = clk/2** (≈ 33 MHz toggle FF); sample read data on the **rising** edge of SCK into `clk`-domain `rdata`. DLL / pattern training is a non-goal.

Datasheet-style margin note (still relevant for Phase 3 review):

- `tACLK` CLK-to-output delay roughly min 2 ns, max 5.5 ns
- At ≈ 33 MHz SCK, rising-edge sample has more margin than a full-rate 66 MHz pad clock
- Fallback if Phase 3 hardware check fails: lower `clk` and/or falling-edge RX

Phase 3 (demoboard + hardening) must **double-check** `tACLK`, board flight time, and TT I/O against **66 MHz clk / 33 MHz SCK** rising-edge before shuttle freeze.

## Implications for this DMA

1. **Address width:** V1 uses **24-bit** internal pointers. The QPI **address phase is always 24 bits** on the wire (`qspi_addr_t`); the device only consumes `A[22:0]` from `addr[22:0]` / `ptr[22:0]`. **`addr[23]` / `ptr[23]` are don't-care** (may be any value; D35). Device select is **`device_sel`** from **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`** (D24), which steers `ram_*_cs_n`.
2. **Dual device:** engine must mux RAM A vs RAM B CS from `CTRL_FLAGS` device selects; never assert both; flash CS parked high / never driven low by ASIC (D11/D26). Cross-device = sequential read/write with CS switch.
3. **Init ownership (D17):** MCU waits 150 us, resets, and Enter Quad on **each** PSRAM used before START; ASIC assumes QPI already. Exit Quad is MCU-only after DONE.
4. **Descriptor fetch efficiency:** hold CE# across the **11-byte** TCD read (first: addr 0 / PSRAM 0; later fetch uses `NEXT_DEVICE` + `NEXT_TCD`); read opcode `0xEB`.
5. **Byte copy vs refresh / pages:** tapeout scratch is **`N=5`** bytes per data phase (cmd+addr(+dummy)+`N` bytes then CE# high). Device limits only matter if `N` grows: **`N ≥ 60`** can exceed `tCEM` 4 us on `0xEB` @ 33 MHz SCK; **`N ≥ 1026`** can cross two 1K pages. At N=5 (and N=1) pulses stay non-binding. Data beats are QPI-only (`0xEB`/`0x02`). MCU QPI chunking vs `tCEM` is a firmware concern ([`12-firmware.md`](12-firmware.md)); do not treat D20 as the MCU chunk policy.
6. **Pass-through / bus OE (D22/D26):** demoboard shares `uio` among RP2 MCU, ASIC, and PSRAM/flash PMOD. Pass-through is `BUS_REQ`/`BUS_GNT`: MCU drives while granted **or** while `rst_n=0`. While `rst_n=1` and `~BUS_GNT`, ASIC is the **bus keeper** (park all CS high / SCK low; SIO don't-care driven in park; float SIO only on read dummy/data and through post-CE# on reads). In `top.v`, shared `uio_oe` is gated combinationally with `rst_n` (all OE off during reset). Board has **10 kΩ** pull-ups on each CS for reset / pre-enable unless MCU selects. Both masters enabled with disagreeing levels is contention - see host-interface / firmware docs. Normative ownership matrix: [`03-architecture.md`](03-architecture.md).
7. **Kill:** assert `rst_n` to stop a runaway DMA (D23; no soft abort).
8. **Flash:** not in V1 ASIC opcode set. NOR erase/BUSY/page semantics are why write is stretch-only; see W25Q128JV converted datasheet.
9. **FSM ↔ engine (D21):** pulse-start when `~busy` (no `txn_ready`); fixed `byte_len` (`[QPI_BYTE_LEN_W-1:0]`) with **`byte_len >= 1`** on every pulse; FSM holds request (engine does not latch); write first nibble on `wdata` with `txn_valid`; exactly `2 * byte_len - 1` `wdata_next` pulses with same-cycle next-nibble mux; `busy` / `rdata_valid` (rising-SCK capture pulse) / `wdata_next` (falling-SCK pulse; next `wdata` nibble on bus before next `clk` for SPI/SIO setup); write ends on `2 * byte_len` SCK (no `wdone`); SCK = clk/2; no SPI stall for FSM; engine owns CE# pad + `tCPH`. Read post-CE# SIO float lasts one `clk` after CE# rise (~15.2 ns @ 66 MHz). See human `qspi-engine.md` and `03-architecture.md`.

## Hardware ecosystem links (see also references)

- Tiny Tapeout demoboard / PCB specs
- `mole99/qspi-pmod` style QSPI flash+PSRAM PMOD
- TT QSPI PMOD SPI guide: [`../datasheets/pdfs/Using_QSPI_TinyTapeout.pdf`](../datasheets/pdfs/Using_QSPI_TinyTapeout.pdf)
- TT MicroPython firmware for RP-class bring-up (`tt-micropython-firmware/`; project [`12-firmware.md`](12-firmware.md))
