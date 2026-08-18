# Project Brief

## Name / working title

**Zero-Overhead Scatter-Gather DMA Engine** (Tiny Tapeout ASIC)

## One-sentence purpose

A descriptor-driven DMA engine that bulk-moves bytes across dual external QSPI PSRAM with almost no host babysitting after START - a learning / resume vehicle and demoboard bulk mover.

## Goals

Primary goals (in priority order):

1. **Ship a working Tiny Tapeout design** within a **1x1** (one-tile) budget that can be demonstrated on the demoboard with real dual PSRAM.
2. **Learn industrially useful skills**: SPI/QSPI controllers, bus mastering, CDC-adjacent timing discipline, descriptor engines, Cocotb BFMs, CI-backed verification, post-synthesis / GDS awareness.
3. **Produce a resume / interview artifact** that demonstrates systems-level hardware (memory hierarchies, firmware boundary, concurrency), not just an isolated math block.

Secondary goals:

- Credible demoboard story: chained TCDs copying same-device and **cross-device A↔B**.

## Non-goals

- Copying TinyDMA-2C RTL or microarchitecture.
- Multi-tile / `1x2` builds (out of budget; D36).
- In-flight ALU, ring wrap, conditional-stop / until loops.
- Building a general-purpose CPU.
- ADC / live sensor integration.
- **ASIC flash read/write** (MCU pass-through covers flash).

Shipped RTL is this V1 feature set only. Historical cut decisions: `07-decision-log.md` (D11/D12).

## Platform

- **Tapeout vehicle**: Tiny Tapeout **TTIHP26b** (IHP shuttle)
- **Process**: **IHP SG13G2** open PDK (`ihp-sg13g2`; 1.2 V digital core, 3.3 V I/O pads with on-pad level shifters)
- **Budget**: **1x1 only** (IHP 1x1 ≈ 202.08 × 154.98 µm; `1x2` is out of budget - D36; see `02-constraints.md`)
- **Host**: RP2040 / RP2350-class MCU on TT demoboard
- **External memory target**: **2x** AP Memory APS6404L-3SQR (64 Mbit QSPI PSRAM each) on QSPI PMOD; both devices are DMA endpoints. Flash on the same PMOD is MCU pass-through only.
- **Shuttle pressure**: treat the active IHP shuttle deadline as a real constraint and scope ruthlessly; slipping a run is acceptable but should not be the default plan.
- **Local PDK / template clones** (workspace, not sources of truth for architecture): `IHP-Open-PDK/`, `ttihp-verilog-template/`
- **Local harden runbook:** [`13-hardening-librelane.md`](13-hardening-librelane.md) (Nix LibreLane + `tt_tool`; human twin under `docs/human/architecture/hardening.md`)

## Core product idea

Classic fixed-channel DMA wastes DFFs storing source/destination/length for every channel on-chip. This design stores Transfer Control Descriptors (TCDs) in external PSRAM and keeps only the **active working set** on-chip.

That trades expensive sequential state for FSM complexity (cheaper in standard-cell area) and enables:

- Linked-list scatter-gather across fragmented memory on either PSRAM
- Cross-device moves between the two PSRAM devices on the PMOD

## Success criteria

A successful project can claim all of the following:

1. Host can install a TCD chain in PSRAM (via pass-through or equivalent programming path).
2. Host can start DMA; ASIC masters QSPI and executes the chain without further host SPI traffic.
3. End-to-end demoboard demo: bulk copy in PSRAM (same-device and cross-device A↔B).
4. Cocotb suite covers happy path + key fault/edge cases (`QUIT` TCD, zero length, invalid/incomplete host sequences, dual-CS selection, `rst_n` kill).
5. Design closes in LibreLane / TT IHP flow on **1x1** (`tt_block_1x1`) with known DFF caution (~200 warning; hard gate is fit + timing).
6. Idle pass-through still lets MCU access flash on the PMOD; ASIC parks flash CS high and never asserts it low in V1 (D26).

## Interview narrative (target)

> I built a Tiny Tapeout scatter-gather DMA that treats external QSPI PSRAM as the configuration store. The ASIC only keeps the active descriptor working set on-chip, so the gate budget goes into the QSPI master, arbitration with the host, and the transfer FSM. On the demoboard it bulk-moves data across both PSRAM devices - including cross-device copies - from a linked list of descriptors the MCU installs through shared-bus pass-through.
