# Product Overview

## Idea

**Zero-Overhead Scatter-Gather DMA Engine** for Tiny Tapeout.

V1 is an **isolated descriptor DMA / bulk mover** across the two QSPI PSRAM devices on the demoboard PMOD - a learning and resume vehicle. Live ADC telemetry is not in scope.

Framing:

- Memory-management coprocessor for bulk byte moves
- **Zero overhead:** transfer instructions (TCDs) live in external RAM, not a fat on-chip channel register file
- **Scatter-gather:** each TCD can point to the next TCD, so arbitrary fragmentation is OK
- **Dual-device:** same-device and cross-device (A↔B) copies

The ASIC owns storage moves through **both** QSPI PSRAM devices. Flash on the same PMOD is MCU pass-through only.

## System topology

```
[ RP2 MCU ] -- host control / data -- > [ DMA ASIC (TT) ] <---- QSPI ----> [ PSRAM A + PSRAM B ]
                                                         (flash: MCU pass-through only)
```

| Actor | Job |
|---|---|
| MCU | Build TCD lists, program either PSRAM (and flash) while ASIC is idle/pass-through, stage buffers, pulse START, handle DONE / `rst_n` kill, read results |
| DMA ASIC | After START, master QSPI, fetch descriptors, **byte-copy** on RAM A and/or B, chain TCDs, return bus / assert DONE. Flash CS parked high (never selected) |
| PSRAM A/B | TCDs, source buffers, destinations |
| Flash | MCU-only via pass-through; ASIC does not master flash |

## What makes this different from trivial memcpy DMA

1. Descriptors in memory - programmable chains, not static channel regs
2. Scatter-gather via `NEXT_TCD`
3. Host/ASIC bus multiplex under pin constraints
4. Dual-device PSRAM orchestration (incl. A↔B)
5. QSPI + refresh-aware mastering

## Deliberate non-goals

- Multi-channel static register files
- In-flight ALU, ring wrap, conditional-stop / until
- Hardware watermark IRQs
- DLL-based QSPI eye training
- Analog sensing / ADC integration
- **ASIC flash read/write** (MCU pass-through covers flash)
- Copying TinyDMA-2C architecture or RTL (prior art only; see [`../../llm/prior-art/tinydma-2c.md`](../../llm/prior-art/tinydma-2c.md))

Shipped RTL is this V1 feature set only. Historical cut decisions live in [`../../llm/07-decision-log.md`](../../llm/07-decision-log.md).

## Inspiration boundary

Per TinyDMA-2C prior art (Andrew Kim, TT 296), a 2-channel byte DMA over SPI PSRAM can fit in 1x2 tiles with aggressive width cuts. That is a feasibility existence proof only. This project intentionally uses descriptor-based scatter-gather and must not inherit that codebase's internal structure.

## See also

- Limits: [`limitations.md`](limitations.md)
- System map: [`system.md`](system.md)
- Index: [`00-index.md`](00-index.md)
