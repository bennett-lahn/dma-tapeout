# TinyDMA: A Descriptor-Based Dual-PSRAM Bulk Mover

## How it works

**TinyDMA** for Tiny Tapeout IHP (**TTIHP26b**). After the host pulses **START**, the ASIC masters the shared QSPI bus and copies bytes between two APS6404L-class PSRAM devices using 11-byte in-memory transfer control descriptors (TCDs).

Each TCD names a source pointer, destination pointer, length, next-TCD pointer, and flags (which device is src/dest/next, and **QUIT** to end the chain). The first TCD is always at address 0 on PSRAM 0. Same-device and cross-device copies are supported. The ASIC does not talk to the PMOD flash; that stays MCU pass-through.

Host pins:

- `ui_in[0]` **START** - accepted only while idle and `BUS_REQ` is low
- `ui_in[2]` **BUS_REQ** - MCU wants the shared QSPI pins
- `uo_out[0]` **DONE** - high whenever the DMA is idle
- `uo_out[1]` **BUS_GNT** - MCU may drive `uio`

While out of reset and not granted, the ASIC parks flash CS and both RAM CS high and SCK low. Kill a runaway chain with `rst_n`. Target clock is 66 MHz; SCK is clk/2.

## How to test

On a Tiny Tapeout demoboard (or FPGA stand-in in the same connector):

1. Enable this design and set the project clock to 66 MHz.
2. Release reset. Expect **DONE** high and **BUS_GNT** low (ASIC parking the bus).
3. Assert **BUS_REQ**, wait for **BUS_GNT**, then drive the QSPI pins from the MCU.
4. Put **both** PSRAM devices into QPI mode (Enter Quad `0x35`). The ASIC never issues that command.
5. Write a TCD chain starting at `0x000000` on PSRAM 0. End the chain with `QUIT=1`. Stage any source payloads.
6. High-Z the MCU QSPI drivers, drop **BUS_REQ**, wait for **BUS_GNT** low.
7. Pulse **START** while **DONE** is high. Wait until **DONE** falls (START accepted), then until **DONE** rises again (chain finished).
8. Request the bus again and read back destination bytes.

A mid-run **BUS_REQ** pauses after the current QPI transaction. There is no soft abort; use `rst_n` to stop a run.

## External hardware

- Tiny Tapeout QSPI PMOD with **dual** APS6404L (or compatible) PSRAM. Flash on the same PMOD is optional and MCU-only.
- Shared `uio` map: flash CS, SIO0, SIO1, SCK, SIO2, SIO3, RAM A CS, RAM B CS.
- Board 10 kOhm pull-ups on the three CS nets are expected.
