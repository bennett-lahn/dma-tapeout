# Project Brief

## Name / working title

**Zero-Overhead Scatter-Gather DMA Engine** (Tiny Tapeout ASIC)

## One-sentence purpose

A descriptor-driven DMA engine that bulk-moves bytes across dual external QSPI PSRAM with almost no host babysitting after START - a learning / resume vehicle and demoboard bulk mover.

## Goals

Primary goals (in priority order):

1. **Ship a working Tiny Tapeout design** within a ~2-tile budget that can be demonstrated on the demoboard with real dual PSRAM.
2. **Learn industrially useful skills**: SPI/QSPI controllers, bus mastering, CDC-adjacent timing discipline, descriptor engines, Cocotb BFMs, CI-backed verification, post-synthesis / GDS awareness.
3. **Produce a resume / interview artifact** that demonstrates systems-level hardware (memory hierarchies, firmware boundary, concurrency), not just an isolated math block.

Secondary goals:

- Credible demoboard story: chained TCDs copying same-device and **cross-device A↔B**.
- Leave architectural headroom for post-V1 features (ALU, cond-stop, ring, flash) without blocking V1 tapeout - see `10-post-v1-features.md`.

## Non-goals (V1)

- Copying TinyDMA-2C RTL or microarchitecture.
- Multi-tile / expensive builds beyond the 2-tile budget.
- In-flight ALU, ring wrap, conditional-stop / until loops.
- Building a general-purpose CPU.
- ADC / live sensor integration as a V1 requirement.
- **ASIC flash read/write in V1** (MCU pass-through covers flash; ASIC flash is last on the post-V1 ladder).

## Platform

- **Tapeout vehicle**: Tiny Tapeout
- **Process**: Sky130 open PDK (standard cells)
- **Budget**: **2 tiles max**
- **Host**: RP2040 / RP2350-class MCU on TT demoboard
- **External memory target**: **2x** AP Memory APS6404L-3SQR (64 Mbit QSPI PSRAM each) on QSPI PMOD; both dies are DMA endpoints. Flash on the same PMOD is MCU pass-through only for V1.
- **Shuttle pressure**: next shuttle ~50 days from planning discussions; slipping to the following shuttle is acceptable but should not be the default plan. Treat the deadline as a real constraint and scope ruthlessly.

## Core product idea

Classic fixed-channel DMA wastes DFFs storing source/destination/length for every channel on-chip. This design stores Transfer Control Descriptors (TCDs) in external PSRAM and keeps only the **active working set** on-chip.

That trades expensive sequential state for FSM complexity (cheaper in standard-cell area) and enables:

- Linked-list scatter-gather across fragmented memory on either PSRAM
- Cross-device moves between the two PSRAM dies on the PMOD

## Success criteria (planning-level)

A successful project can claim all of the following:

1. Host can install a TCD chain in PSRAM (via pass-through or equivalent programming path).
2. Host can start DMA; ASIC masters QSPI and executes the chain without further host SPI traffic.
3. End-to-end demoboard demo: bulk copy in PSRAM (same-device and cross-device A↔B).
4. Cocotb suite covers happy path + key fault/edge cases (null next pointer, zero length, CE# refresh slicing, invalid/incomplete host sequences, dual-CS selection, abort).
5. Design closes in OpenLane / TT flow within 2 tiles with known DFF budget.
6. Idle pass-through still lets MCU access flash on the PMOD; ASIC never asserts flash CS in V1.

## Interview narrative (target)

> I built a Tiny Tapeout scatter-gather DMA that treats external QSPI PSRAM as the configuration store. The ASIC only keeps the active descriptor working set on-chip, so the gate budget goes into the QSPI master, arbitration with the host, and the transfer FSM. On the demoboard it bulk-moves data across both PSRAM dies - including cross-device copies - from a linked list of descriptors the MCU installs through shared-bus pass-through.
