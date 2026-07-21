# Decision Log

Chronological distillation of the idea -> proposal -> selection process. Verbose on purpose so future agents do not relitigate settled choices without new evidence.

## D1 - Project class selection

**Context:** Wanted a Tiny Tapeout RTL project that is resume-relevant, fits few tiles, and is more than a toy FSM.

**Candidates explored:** MEMS PDM decimator, stepper profiler, Simon/Speck, I2C sniffer, Rule 30 PRNG, IMU helper, ML accelerator, capacitive touch, 7400 clone, DMA over SPI PSRAM, lightweight hash MAC, motion interpolator, async FIFO, SipHash/ASCON/PHOTON family.

**Rejected early:**

- Capacitive touch: analog in nature; poor fit for digital TT.
- Basic logic IC clones: too trivial.
- Rule 30 PRNG alone: elegant but weak systems story.
- Fat ML accelerators: gate hungry unless extremely serialized.

**Short list:** MEMS decimator, DMA+PSRAM, HalfSipHash-style MAC.

## D2 - DMA vs Hash final selection

**Decision:** Proceed with **Scatter-Gather DMA** as the main project.

**Why DMA won:**

1. Stronger systems / firmware boundary story (descriptors, bus mastership, memory hierarchy).
2. Better interview surface for concurrency and verification with BFMs, not only golden vectors.
3. Clear path to a physical demoboard demo with QSPI PSRAM PMOD hardware.
4. Aligns with "do something useful" goal (sensor telemetry manager) better than an isolated MAC.

**Why Hash remains respectable but secondary:**

- Excellent micro-arch / area-vs-time lesson (serialized ARX ALU).
- More deterministic verification and safer 50-day schedule.
- Weaker systems integration narrative; less dynamic interaction with a memory ecosystem.

**Counterarguments acknowledged (from hash proposal):**

- DMA depends on external memory timing models and PMOD realities.
- Hash extracts more "math work per DFF" and is easier to CI.
- These are real tradeoffs; they did not outweigh systems-learning and resume goals for this repo.

## D3 - Not copying TinyDMA-2C

**Decision:** Keep TinyDMA-2C (Andrew Kim, TT 296) as a **separate prior-art context** for feasibility evidence and contrast only. Full dump: `prior-art/tinydma-2c.md`. Any use must be explicitly attributed.

**Why:**

- It proves a SPI PSRAM DMA can fit in 2 tiles.
- Its static 2-channel register programming model is exactly what this project intends to replace with external TCDs.
- Starting from scratch avoids IP/ethical issues and forces original architecture work.

## D4 - Descriptor-based "zero-overhead" configuration

**Decision:** Store TCDs in PSRAM; on-chip retain only active working set (**88 DFFs** TCD metadata at 24-bit pointers + `CTRL_FLAGS` byte + engine overhead; see D13).

**Why:**

- DFFs are the limiting resource.
- Enables long software-defined chains without N-channel register files.
- Moves complexity into FSM sequencing (cheaper than SRAM/DFF arrays in this context).

## D5 - Feature bundle (superseded by D12)

**Original decision:** scatter-gather + optional ring + optional ALU, justified by ADC telemetry.

**Superseded:** ADC integration is unlikely for V1. See **D12** for the current cut (bulk mover V1; ALU / cond-stop / ring / flash post-V1).

## D6 - Schedule posture

**Decision:** Treat ~50-day shuttle target as real; cut scope before cutting verification.

**Implied V1 triage order (highest keep-priority first) - updated by D12:**

1. Pass-through + START/DONE/**abort** bus ownership (MCU can still reach flash + both PSRAMs when idle)
2. QSPI engine with CE# refresh slicing + **RAM A/B CS mux**
3. Single TCD memmove (same-device)
4. Cross-device PSRAM copy (A↔B)
5. TCD chaining (scatter-gather)

Post-V1 ladder (not V1): ALU → conditional stop → ring wrap → ASIC flash read → maybe flash write. See `10-post-v1-features.md`.

## D7 - Documentation structure

**Decision:** Maintain dual docs in-repo:

- `docs/llm/`: verbose agent context
- `docs/human/`: condensed human docs

Obsidian vault notes remain handwritten and external; agents may read, must not modify.

## D8 - Shared-bus pass-through via `uio_oe`

**Decision:** Implement MCU pass-through as **OE arbitration on shared demoboard `uio` nets** (ASIC / RP2040 / QSPI PMOD), not as a pin proxy that brings MCU QSPI in on `ui_in` and redrives a separate PSRAM port.

**Why:**

- TT only has eight bidirectional pins; a full second QSPI path does not fit.
- Demoboard already ties RP2040 GPIOs to the same `uio` headers as the PMOD.
- Idle = ASIC `uio_oe=0` + MCU master; DMA = MCU GPIO Hi-Z + ASIC master with phase-accurate SIO OE.
- Contends if both enable; protocol must release-before-seize.

**Superseded for restore timing by D14:** pass-through tracks idle/`DONE` (auto on idle; no host ACK).

Detail: `docs/human/architecture/blocks/host-interface.md`, `docs/llm/03-architecture.md`.

## D9 - V1 I/O assignments (partial)

**Decision (superseded for CS usage by D11):**

- `uio[7:0]`: TT QSPI PMOD map; SCK + SIO0..3 + RAM A CS; flash CS and RAM B CS unused (OE off)
- `ui_in[0]`: **START**
- `uo_out[0]`: **DONE**
- Remaining `ui_in` / `uo_out` bits reserved for head/arm/status/DFT

**Still binding from D9:** START/DONE bit indices; QSPI on `uio` per PMOD map.

## D10 - V1 address width and null

**Decision (superseded lean):** earlier lean was 16-bit pointers with upper byte zero.

**Decision (current, updated by D13):** **24-bit** internal pointers in TCD / working regs / head; **`0x000000` reserved null** (end of chain). QSPI address phase uses full device `A[22:0]` (`ptr[22:0]`; `ptr[23]` unused / must be 0). Device select lives in `CTRL_FLAGS` (D13), not pointer MSB - preserves the full 8 MB APS6404L window.

## D11 - Dual PSRAM in scope; flash out of ASIC V1

**Decision:**

1. **Both PSRAMs (RAM A + RAM B)** are first-class DMA endpoints. ASIC may read and write either device, including **cross-device** transfers (e.g. read A write B, read B write A, same-device A↔A / B↔B).
2. **Flash is not an ASIC DMA target for V1 (or planned V1.x).** No flash opcodes, erase/program FSM, or flash CS assert from the QSPI engine.
3. **Pass-through remains complete for the whole PMOD:** when ASIC `uio_oe=0`, the MCU may master **flash and both PSRAMs** (including flash firmware/storage experiments).
4. During DMA mastership, ASIC may drive **RAM A CS and/or RAM B CS** (one active CE# per transaction on the shared SIO bus). **Flash CS stays OE-off** so the ASIC never contends on that net; board pull-up / MCU release keeps flash deselected while DMA runs.
5. **Super stretch (post-shuttle / explicit cut only):** ASIC **flash read**; **maybe** flash write later. Not on the V1 ship ladder. NOR erase/BUSY/page rules make write a separate product-sized effort (see W25Q128JV notes in `05-qspi-psram.md` / datasheets).

**Why:**

- Dual PSRAM is already on the TT QSPI PMOD and costs little in gates (CS mux + a couple of TCD device-select bits) while unlocking a clear demoboard story (copy / ping-pong between dies).
- Flash read/write on-ASIC fights the 2-tile / schedule cut; MCU pass-through already covers flash without silicon risk.
- Keeps the product framing as a **PSRAM memory orchestrator**, not a NOR programmer.

**DFF / tile impact:** low for dual-PSRAM (CS select mux; ~2 flag bits or equivalent for src/dest device). Flash stretch is deferred precisely because write path is medium–high FSM/DFF cost.

**Encoding:** superseded by D13 (`CTRL_FLAGS` device bits).

## D12 - V1 = dual-PSRAM bulk mover; telemetry extras post-V1

**Decision:**

1. **V1 product:** isolated descriptor DMA for **learning / resume** and **bulk moves between PSRAM A and B** (same-device and cross-device). ADC / live sensor ingress is not a V1 commitment.
2. **V1 TCD:** **11-byte** memmove record (`SRC`, `DEST`, `LEN`, `NEXT`, `CTRL_FLAGS`). Flags carry device select only in V1 (D13); no ALU, ring, or conditional stop.
3. **V1 host:** START / DONE / abort behavior frozen in D14 (exact abort pin index still open).
4. **Post-V1 add order** (documented in `10-post-v1-features.md`):
   1. In-flight byte ALU (extend reserved `CTRL_FLAGS` bits + `IMM`, `STATE_PROCESS`)
   2. Conditional stop (LT/Z/NZ after READ; `LEN==0` until; needs abort)
   3. Ring / modulo addressing
   4. ASIC flash read, then maybe flash write (extends D11 stretch)

**Why:**

- Matches the realistic demoboard story without ADC integration.
- Shrinks verification surface for the shuttle vs telemetry extras.
- Keeps a clear implement-later path so telemetry-shaped ideas are not lost.

**DFF / tile impact:** V1 pays **8 DFFs** for a reserved `CTRL_FLAGS` byte (3 used). Post-V1 ALU/IMM/ring reintroduce further cost in the order above.

## D13 - `CTRL_FLAGS` device select (reject pointer MSB)

**Decision:**

1. Re-add a full **`CTRL_FLAGS` byte** to the V1 TCD (**11 bytes** total; working metadata **88 DFFs**).
2. V1 uses only **three 1-bit flags** (rest of byte reserved for post-V1):
   - `SRC_DEV` - source buffer in PSRAM 0 vs 1
   - `DEST_DEV` - dest buffer in PSRAM 0 vs 1
   - `NEXT_DEV` - next TCD (fetch target) in PSRAM 0 vs 1
3. **Do not** steal `ptr[23]` for device select. Pointers remain full device addresses; QSPI drives `A[22:0]`; device bits stay in flags and are **preserved** for the life of that TCD (pointer increments do not change device).
4. **`TRANSFER_LEN == 0`:** no-op descriptor - skip data moves; immediately follow `NEXT_TCD` (using `NEXT_DEV`).
5. **`NEXT_TCD == 0x000000`:** null / end-of-chain (see D14 for idle transition).

**Why:** keeps the full 8 MB window per die; gives an explicit next-TCD device (cross-die chains); reserves flag room for post-V1 without another layout break.

**Rejected:** `ptr[23]` device-select lean (Q13) - halves usable address space per die encoding and couples increment wrap to die identity.

**Still open:** which die holds the **head** at START (lean: PSRAM 0 until head protocol adds a device bit).

## D14 - Host protocol: idle / START / DONE / abort / pass-through

**Decision:**

| Rule | Behavior |
|---|---|
| Idle wait | From **IDLE**, wait for **START**; accept START only in IDLE |
| START while busy | **Ignored** until the ASIC returns to IDLE |
| Null TCD | `NEXT_TCD == 0x000000` → transition to **IDLE** |
| DONE | Asserted **whenever** the ASIC is IDLE (including after reset / before first START) |
| Pass-through | Enabled iff DONE (idle); disabled while not idle (DMA active) |
| Abort | If **ABORT** asserted while active: finish the **current QPI transaction**, then transition to IDLE (DONE, pass-through on) |

**Pin indices still open:** ABORT on `ui_in[7:1]`; head-pointer / arm programming; optional ERROR / DFT on `uo_out[7:1]`. Frozen indices remain `ui_in[0]=START`, `uo_out[0]=DONE`.

**Supersedes Q4:** no host-ACK gate for bus restore; restore tracks idle/`DONE`.

## D15 - QPI default for DMA data path

**Decision:**

1. **QPI is the default** for all ASIC PSRAM **data** read/write (descriptor fetch and byte copy).
2. **SPI is never used** for reading or writing payload / TCD data.
3. **SPI may be used only for config / mode bring-up** that enables QPI (explicitly: Enter Quad `0x35`; Reset Enable/Reset `0x66`/`0x99` when issued in SPI before QPI). Document every SPI opcode the ASIC emits in `05-qspi-psram.md`.
4. Primary QPI read opcode choice (`0x0B` vs `0xEB`) remains open (clock / wait tradeoff).
