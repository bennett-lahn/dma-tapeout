# QSPI / PSRAM Context

This file captures protocol knowledge needed for the DMA memory engine. It is not a substitute for the APS6404L datasheet.

Local sources:

- PDF (authoritative): `docs/datasheets/pdfs/APS6404L_3SQR.pdf`
- Converted text: `docs/datasheets/md/APS6404L_3SQR.md`
- Conversion process: `docs/datasheets/README.md`

## PSRAM vs Flash (why PSRAM)

| | PSRAM | Flash |
|---|---|---|
| Volatility | Volatile | Non-volatile |
| Writes | Fast, byte-level, effectively unlimited endurance | Slow; erase-before-write; limited cycles |
| Role here | Working memory, descriptors, source/dest buffers during run | Not an ASIC DMA target in V1 (MCU pass-through; post-V1 flash later) |

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
| Winbond **W25Q128JV** | 128 M-bit QSPI Flash | On PMOD; **MCU pass-through only** in V1. ASIC never asserts flash CS. Super-stretch: ASIC flash read (maybe write). Datasheet: `docs/datasheets/` (JV is Dual/Quad SPI; true QPI is JV-M). |
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
- Linear burst crosses a 1K page at most once per CE# pulse and caps at 84 MHz when crossing; Wrap32 (`0xC0` toggle) is optional and not the power-up default
- Practical clock caution: limit to ~**66 MHz SPI** / ~**84 MHz QPI** linear burst for simplicity; QPI Fast Read `0x0B` max 66 MHz; other ops often quoted to 109/133 MHz wrap or 84 MHz linear page-cross - prefer lower clocks and/or falling-edge RX sample for TT

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

**Policy:** QPI is the default for all DMA **data** read/write (TCD fetch and byte copy). **SPI is never used for data.** SPI appears only in the explicitly documented config / bring-up opcodes below.

These are the opcodes the **on-chip QSPI engine** is expected to emit (not merely allow via MCU pass-through).

#### SPI-only config / bring-up (not data path)

| Role | Opcode | Mode | V1 status |
|---|---|---|---|
| Reset Enable | `0x66` | SPI (and optionally QPI) | **Config only** - if ASIC owns init / recovery |
| Reset | `0x99` | SPI (and optionally QPI) | **Config only** - must immediately follow `0x66` |
| Enter Quad Mode | `0x35` | SPI | **Config only** - SPI → QPI; required if ASIC owns init; else MCU precondition |

#### QPI data path (DMA)

| Role | Opcode | Mode | V1 status |
|---|---|---|---|
| QPI Fast Read | `0x0B` or `0xEB` | QPI | **Required** - pick one primary read opcode (open: see below) |
| QPI Write | `0x02` (or `0x38`) | QPI | **Required** - in QPI both are equivalent; prefer one (`0x02`) |

Init ownership is still open (see `08-open-questions.md` Q2). If MCU owns init via pass-through, the ASIC still needs the QPI read/write opcodes for DMA, and may omit ASIC-side SPI config entirely unless a recovery path is kept.

#### Other commands: include or not?

| Command | Recommendation | Why |
|---|---|---|
| Exit Quad Mode `0xF5` | **Strongly consider for V1** | Cheap (command-only, QPI). Lets ASIC/MCU return the part to SPI after QPI DMA, recover a stuck QE=1 device, and match pass-through firmware that assumes SPI. Without it, only a successful `0x66`/`0x99` reset returns to SPI. |
| Wrap Boundary Toggle `0xC0` | **Defer (not V1)** | Default is already Linear Burst, which matches long DMA copies. Wrap32 is an optimization / higher-clock path and adds mode state the engine must track. |
| Read ID `0x9F` | **MCU via pass-through, not ASIC** | Useful for board bring-up and KGD check; SPI-only diagnostic - not a DMA data opcode. |
| SPI Read `0x03` / SPI Fast Read `0x0B` / SPI Write `0x02`/`0x38` | **Not in ASIC (D15)** | DMA data path is QPI-only. MCU pass-through covers SPI diagnostics. |
| Fast Read Quad `0xEB` vs Fast Read `0x0B` | **Pick one QPI read for V1** | `0x0B`: 4 wait cycles, max 66 MHz. `0xEB`: 6 wait cycles, higher max freq. For a conservative TT clock, `0x0B` is simpler timing; `0xEB` is the throughput choice if clock allows. Supporting both costs little decode but two wait-length paths. |

**Lean for V1 ASIC opcode set:** SPI config `0x66`/`0x99`/`0x35` only if ASIC-owned init; QPI data = one read (`0x0B` preferred until clock target is frozen) + write `0x02`; plus **`0xF5` Exit Quad** for recovery / bus handback. Leave `0xC0` and `0x9F` to firmware/pass-through.

### Transaction phases (QPI)

1. **Command** - 8-bit opcode (2 clocks at 4 bits/clock)
2. **Address** - 24-bit address (6 clocks)
3. **Dummy / wait** - device-specific; required for fast reads so DRAM array can produce data
4. **Data** - 2 clocks per byte in quad mode

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

**Design requirement:** the QSPI engine must slice long DMA bursts into CE#-high-bounded segments even if the logical transfer is longer. Track CE# low time against `tCEM` for the chosen device grade.

## High-speed read sampling note

Datasheet-style note discussed in planning:

- `tACLK` CLK-to-output delay roughly min 2 ns, max 5.5 ns ("make" in OCR/notes = max)
- Above ~84 MHz, sampling read data on the next rising edge can leave ~0.45 ns theoretical setup before PCB flight time - not viable
- Mitigations:
  1. Sample on falling edge (about one full period of launch-to-sample margin; adds half-cycle latency)
  2. Training / pattern-based sample point alignment (JEDEC-style eye centering; likely too heavy for this ASIC)

For a 2-tile student/tapeout project, prefer **lower clock** and/or **falling-edge RX sample** over DLL training.

## Implications for this DMA

1. **Address width:** V1 uses **24-bit** internal pointers; QSPI address phase drives `A[22:0]`. Device select is in TCD `CTRL_FLAGS` (D13), not `ptr[23]`.
2. **Dual die:** engine must mux RAM A vs RAM B CS from `SRC_DEV` / `DEST_DEV` / `NEXT_DEV`; never assert both; flash CS OE stays off (D11). Cross-device = sequential read/write with CS switch.
3. **Init ownership:** someone must wait 150 us, reset, and enter quad mode on **each** PSRAM used before useful DMA. Could be MCU (via pass-through) or ASIC boot FSM. SPI only for those config steps (D15).
4. **Descriptor fetch efficiency:** hold CE# across the **11-byte** TCD read (die from `NEXT_DEV` / head policy).
5. **Byte copy efficiency vs refresh:** holding CE# across huge copies is illegal; engine must track CE# low time per active die. Data beats are QPI-only.
6. **Pass-through / bus OE:** demoboard shares `uio` among RP2040, ASIC, and PSRAM/flash PMOD. Pass-through means idle/`DONE` and ASIC `uio_oe=0` (D14); DMA means MCU GPIOs high-Z and ASIC drives with phase-accurate SIO OE + RAM CS mux. Both masters enabled is contention - see host-interface bus-ownership doc.
7. **Abort:** finish current QPI transaction, then idle (D14).
8. **Flash:** not in V1 ASIC opcode set. NOR erase/BUSY/page semantics are why write is stretch-only; see W25Q128JV converted datasheet.

## Hardware ecosystem links (see also references)

- Tiny Tapeout demoboard / PCB specs
- `mole99/qspi-pmod` style QSPI flash+PSRAM PMOD
- TT MicroPython firmware for RP-class bring-up
