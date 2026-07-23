# System Use Case: Dual-PSRAM Bulk Mover

## Anchor scenario (V1)

Firmware on the RP2 wants to move large or fragmented buffers between the two APS6404L dies (or within one die) without bit-banging every SPI/QSPI beat in software.

Without a DMA helper, the MCU repeatedly:

1. issues QSPI read/write sequences
2. tracks source/dest pointers and lengths
3. respects CE# refresh limits
4. stitches fragmented regions by hand

## How this ASIC changes the story

The ASIC is an **autonomous descriptor DMA** between the RP2 and the two PSRAM dies.

Firmware builds a TCD linked list once (or occasionally), stages payloads during pass-through, then lets hardware:

1. **Memcopy** bytes from SRC to DEST (same-device or cross-device)
2. **Scatter-gather** across fragmented PSRAM regions as if they were contiguous
3. **Return the bus** on DONE (or abort) so the MCU can inspect results

Flash on the PMOD stays MCU-managed via pass-through in V1.

## Topology

```
RP2 MCU -- host control --> DMA ASIC -- QSPI --> PSRAM A + PSRAM B
MCU pass-through (idle) ----------------------> flash + PSRAM A/B
```

V1 ingress: **pre-staged buffers in PSRAM** (MCU wrote them before START). Live ADC streaming is not a V1 requirement (see Q10 lean).

## Concrete behaviors mapped to V1 features

### 1. Scatter-gather bulk copy

Need: move N bytes across multiple extents / dies.

Mechanism: when `TRANSFER_LEN` hits 0, fetch next TCD and continue until a `QUIT=1` TCD (`CTRL_FLAGS` bit 0).

### 2. Cross-device A↔B

Need: ping-pong or evacuate one die to the other on the shared QSPI bus.

Mechanism: device select via `ptr[23]` on SRC/DEST/NEXT; read-then-write with one CE# low at a time.

### 3. Long transfers with refresh

Need: copies larger than a single CE# low window.

Mechanism: QSPI engine inserts CE# high gaps (`tCEM` / `tCPH`).

## What V1 can demo

Minimum persuasive demo:

1. MCU writes a 2-TCD chain through pass-through.
2. MCU stages a known pattern in RAM A (and/or B).
3. START runs chain: copy A→B (and/or B→A / same-device).
4. DONE fires; MCU reads back and matches expected bytes.

Stretch demo (still V1):

- Long transfer that proves CE# slicing did not corrupt PSRAM
- Abort mid-transfer restores pass-through cleanly

## Post-V1 use cases (not V1)

Telemetry-shaped behaviors (ring last-*N*, in-flight calibrate/mask, until-threshold loops, ASIC flash) are sketched in [`10-post-v1-features.md`](10-post-v1-features.md). They are optional later additions if a sensor workload returns.

## Interview framing (V1)

> This is a descriptor-based dual-PSRAM DMA for Tiny Tapeout. Descriptors live in PSRAM so the on-chip budget goes to QSPI mastering and host bus arbitration. The demoboard story is bulk scatter-gather copies across both dies, including cross-device moves, without the MCU SPI-bitbanging every byte.
