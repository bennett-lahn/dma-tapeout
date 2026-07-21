# PRIOR ART CONTEXT: TinyDMA-2C (TT 296)

> **ATTRIBUTION REQUIRED**
>
> This file is **third-party prior art**, not this project's architecture.
> Author: **Andrew Kim**. Tiny Tapeout project **296**: TinyDMA-2C.
>
> Whenever you use facts, pin maps, protocols, widths, verification ideas, or
> feasibility arguments drawn from this file (or from TinyDMA-2C generally):
>
> 1. **Say so explicitly** in the reply (e.g. "Per TinyDMA-2C prior art..." /
>    "Andrew Kim's TinyDMA-2C used...").
> 2. Do **not** present those details as decisions already made for *this* repo.
> 3. Do **not** copy RTL, module breakdown, or microarchitecture into this project.
>
> This repo's design is an original descriptor-based scatter-gather DMA.
> TinyDMA-2C is an existence proof and comparison reference only.

## Identity

| Field | Value |
|---|---|
| TT project | 296 |
| Name | TinyDMA-2C |
| Author | Andrew Kim |
| Description | Two-channel byte DMA engine using external SPI PSRAM |
| Clock (submitted) | 50,000,000 Hz |
| Tile fit | 1x2 (barely); uses narrowed address/length fields to fit |

## What it is

TinyDMA-2C is a two-channel byte DMA engine for Tiny Tapeout. It copies data between addresses in an external SPI PSRAM device through:

- a small scheduler
- a byte-wide DMA controller
- a single-bit SPI PSRAM controller

The submitted Tiny Tapeout-sized build uses **16-bit internal addresses** and **8-bit transfer lengths** to fit the 1x2 tile budget. The PSRAM controller sends standard SPI read/write commands with a **24-bit address phase**; the upper address byte is driven as zero and the internal 16-bit address supplies the lower two bytes.

## Pinout

Configuration enters through the dedicated input bus and two UIO control strobes:

| Signal | Pin | Direction / role |
|---|---|---|
| configuration command or data byte | `ui_in[7:0]` | input |
| `cfg_valid` strobe | `uio_in[0]` | input |
| `start` strobe | `uio_in[1]` | input |
| SPI MISO from PSRAM | `uio_in[2]` | input |
| SPI chip select, active low | `uio_out[3]` | output |
| SPI clock | `uio_out[4]` | output |
| SPI MOSI to PSRAM | `uio_out[5]` | output |

Bidirectional output-enable mask is fixed at:

```text
uio_oe = 8'b0011_1000
```

Only `uio[3]`, `uio[4]`, and `uio[5]` are driven by the design. Unused UIO outputs are tied low.

### Status on `uo_out`

| Bit | Meaning |
|---|---|
| `uo_out[0]` | any DMA channel active |
| `uo_out[1]` | done pulse when either channel completes |
| `uo_out[2]` | channel 0 done |
| `uo_out[3]` | channel 1 done |
| `uo_out[4]` | channel 0 active |
| `uo_out[5]` | channel 1 active |
| `uo_out[6]` | configuration adapter is waiting for a data byte |
| `uo_out[7]` | invalid configuration sequence detected |

### IO summary table

| # | Input | Output | Bidirectional |
|---|---|---|---|
| 0 | `cfg_data[0]` | `dma_active` | `cfg_valid` |
| 1 | `cfg_data[1]` | `done_pulse` | `start` |
| 2 | `cfg_data[2]` | `ch0_done` | `spi_miso` |
| 3 | `cfg_data[3]` | `ch1_done` | `spi_cs_n` |
| 4 | `cfg_data[4]` | `ch0_active` | `spi_clk` |
| 5 | `cfg_data[5]` | `ch1_active` | `spi_mosi` |
| 6 | `cfg_data[6]` | `cfg_pending` | |
| 7 | `cfg_data[7]` | `cfg_error` | |

## Configuration protocol

Each register byte write is sent as two `cfg_valid` pulses:

1. command byte
2. payload byte

### Command byte format

| Bits | Meaning |
|---|---|
| 7 | must be 1 |
| 6 | channel select: 0 = ch0, 1 = ch1 |
| 5:4 | register field: `00` source, `01` destination, `10` length, `11` control |
| 3:2 | byte index within the register, low byte first |
| 1:0 | unused, write as 0 |

### Accepted fields in the submitted build

- **source / destination:** byte indices 0 and 1 -> 16-bit address
- **length:** byte index 0 only -> 8-bit byte count
- **control:** byte index 0 only

### Control byte

| Bit | Meaning |
|---|---|
| 0 | arm channel for the next start pulse |
| 1 | increment source after each byte |
| 2 | increment destination after each byte |

The adapter stores the arm bit separately. When `uio_in[1]` is pulsed, any armed channel has its internal start bit asserted and the DMA begins.

### Example

Configure channel 0 to copy 4 bytes from `0x0010` to `0x0020` with both addresses incrementing. Pulse `cfg_valid` for each pair below, then pulse `uio_in[1]`:

| Command | Payload | Meaning |
|---|---|---|
| `0x80` | `0x10` | ch0 source byte 0 |
| `0x84` | `0x00` | ch0 source byte 1 |
| `0x90` | `0x20` | ch0 destination byte 0 |
| `0x94` | `0x00` | ch0 destination byte 1 |
| `0xA0` | `0x04` | ch0 length |
| `0xB0` | `0x07` | ch0 control: arm + incr src + incr dest |

## External hardware

Targets a QSPI PMOD containing **APS6404** PSRAM, used here in **single-bit SPI mode**.

FPGA bring-up PMOD map:

| PSRAM signal | TinyDMA-2C pin |
|---|---|
| CS | `uio[3]` |
| SCK | `uio[4]` |
| MOSI | `uio[5]` |
| MISO | `uio[2]` |

## Verification (as reported by the project)

- cocotb test of the Tiny Tapeout wrapper protocol and SPI PSRAM model
- RTL simulations of the SPI master, PSRAM controller, DMA subsystem, and top-level DMA path
- FPGA PSRAM bring-up with real PMOD hardware
- FPGA test harness that drives the actual Tiny Tapeout-style IO wrapper
- UART-driven FPGA test scripts covering:
  - raw PSRAM access
  - channel 0 copy
  - channel 1 fixed-source fill
  - fixed-destination behavior
  - zero-length transfer
  - longer 16-byte transfer
- GitHub Actions test and GDS flows reported successful for the Tiny Tapeout repository

## How this repo may use this context

Allowed:

- Feasibility arguments ("a 1x2 SPI PSRAM byte DMA has shipped")
- Contrast for our differentiators (descriptors in PSRAM, scatter-gather, optional ALU/ring, original microarch)
- High-level lessons (16-bit addr / 8-bit length as an area tactic; upper address byte tied to zero; status-bit budgeting)

Not allowed without explicit user direction:

- Reusing pin protocol as *our* frozen protocol without labeling it as prior-art-derived
- Replicating scheduler / controller / SPI split as a copy
- Importing RTL, testbench structure, or naming as if original

## Contrast snapshot (this project vs TinyDMA-2C)

| Topic | TinyDMA-2C (prior art) | This project (intent) |
|---|---|---|
| Programming model | Static 2-channel regs via cmd/payload | TCDs in PSRAM, head pointer / chain |
| Channels | Two on-chip channel contexts | One active working set; chain depth in memory |
| SPI width | Single-bit SPI | SPI bring-up; QPI under consideration |
| Address | 16-bit internal, top SPI addr byte 0 | Same width tactic possible; not adopted by default without attribution |
| Extra ops | Copy / fill style control bits | Optional ALU + ring addressing for telemetry |
| Authorship | Andrew Kim | Original work in this repo |
