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

1. Pass-through + START/DONE bus ownership (MCU can still reach flash + both PSRAMs when idle; ~~abort~~ revoked by D23 → use `rst_n`)
2. QSPI engine with **RAM A/B CS mux** (V1: short CE# pulses from `N=1` / 11-byte fetch; no dedicated `tCEM` slicer)
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

**Superseded for restore timing by D14 / drive legality by D22:** OE clears on idle or yield; MCU drives only under `BUS_GNT`.
**Superseded for idle OE by D26:** ASIC is the bus keeper whenever `~BUS_GNT` (parks CS high / SCK low); OE clears only for grant / reset.

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

**Decision (current, updated by D18 / D19 / D24):** **24-bit** internal pointers in TCD / working regs (no head register). QSPI address phase uses device `A[22:0]` from `ptr[22:0]`. ~~Device select in `CTRL_FLAGS` (D13/D18).~~ ~~**Superseded by D19:** `ptr[23]` selects device.~~ **Superseded by D24:** device selects back in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`); `ptr[23]` unused. ~~`0x000000` reserved null.~~ **Superseded by D18:** address 0 is valid (fixed head on PSRAM 0). ~~End-of-chain = both-devices stop.~~ **Superseded by D19:** `CTRL_FLAGS.QUIT`.

## D11 - Dual PSRAM in scope; flash out of ASIC V1

**Decision:**

1. **Both PSRAMs (RAM A + RAM B)** are first-class DMA endpoints. ASIC may read and write either device, including **cross-device** transfers (e.g. read A write B, read B write A, same-device A↔A / B↔B).
2. **Flash is not an ASIC DMA target for V1 (or planned V1.x).** No flash opcodes, erase/program FSM, or flash CS assert from the QSPI engine.
3. **Pass-through remains complete for the whole PMOD:** when ASIC grants the bus (`BUS_GNT`, `uio_oe=0`), the MCU may master **flash and both PSRAMs** (including flash firmware/storage experiments).
4. During DMA mastership, ASIC may drive **RAM A CS and/or RAM B CS** (one active CE# per transaction on the shared SIO bus). **Flash is never selected by ASIC** (flash CS never driven low). ~~Flash CS stays OE-off~~ **Superseded by D26:** while `~BUS_GNT`, ASIC **parks flash CS high** (same bus-keeper policy as the RAM CS lines). Board **10 kΩ** CS pull-ups remain the keeper during reset / pre-enable.
5. **Super stretch (post-shuttle / explicit cut only):** ASIC **flash read**; **maybe** flash write later. Not on the V1 ship ladder. NOR erase/BUSY/page rules make write a separate product-sized effort (see W25Q128JV notes in `05-qspi-psram.md` / datasheets).

**Why:**

- Dual PSRAM is already on the TT QSPI PMOD and costs little in gates (CS mux + a couple of TCD device-select bits) while unlocking a clear demoboard story (copy / ping-pong between devices).
- Flash read/write on-ASIC fights the 2-tile / schedule cut; MCU pass-through already covers flash without silicon risk.
- Keeps the product framing as a **PSRAM memory orchestrator**, not a NOR programmer.

**DFF / tile impact:** low for dual-PSRAM (CS select mux; ~2 flag bits or equivalent for src/dest device). Flash stretch is deferred precisely because write path is medium–high FSM/DFF cost.

**Encoding:** ~~superseded by D13 (`CTRL_FLAGS` device bits).~~ ~~**Superseded by D19:** device in `ptr[23]`.~~ **Superseded by D24:** device selects in `CTRL_FLAGS`.

## D12 - V1 = dual-PSRAM bulk mover; telemetry extras post-V1

**Decision:**

1. **V1 product:** isolated descriptor DMA for **learning / resume** and **bulk moves between PSRAM A and B** (same-device and cross-device). ADC / live sensor ingress is not a V1 commitment.
2. **V1 TCD:** **11-byte** memmove record (`SRC`, `DEST`, `LEN`, `NEXT`, `CTRL_FLAGS`). ~~Flags carry device select (D13).~~ ~~**D19:** flags carry **`QUIT` only**; device is `ptr[23]`.~~ **D24:** flags carry **`QUIT` + `SRC_DEVICE` + `DEST_DEVICE` + `NEXT_DEVICE`**; pointers are addresses only. No ALU, ring, or conditional stop.
3. **V1 host:** START / DONE behavior in D14; pin indices and fixed head in **D18**; **no ABORT pin** (**D23**: use `rst_n` to kill a run). ~~`ui_in[1]=ABORT`~~ superseded by D23 (`ui_in[1]` reserved).
4. **Post-V1 add order** (documented in `10-post-v1-features.md`):
   1. In-flight byte ALU (extend reserved `CTRL_FLAGS` bits + `IMM`, `STATE_PROCESS`)
   2. Conditional stop (LT/Z/NZ after READ; `LEN==0` until; needs `rst_n` or a future soft-abort)
   3. Ring / modulo addressing
   4. ASIC flash read, then maybe flash write (extends D11 stretch)

**Why:**

- Matches the realistic demoboard story without ADC integration.
- Shrinks verification surface for the shuttle vs telemetry extras.
- Keeps a clear implement-later path so telemetry-shaped ideas are not lost.

**DFF / tile impact:** V1 pays **8 DFFs** for a `CTRL_FLAGS` byte (**4 used** for `QUIT` + three device selects per D24; `[7:4]` reserved). Post-V1 ALU/IMM/ring reintroduce further cost in the order above.

## D13 - `CTRL_FLAGS` device select (reject pointer MSB)

**Decision (superseded for device/stop encoding by D19):**

1. Re-add a full **`CTRL_FLAGS` byte** to the V1 TCD (**11 bytes** total; working metadata **88 DFFs**) - **still binding**.
2. ~~V1 device fields in `CTRL_FLAGS`.~~ **Superseded by D19:** device is `ptr[23]`; `CTRL_FLAGS` holds `QUIT` + reserved.
3. ~~Do not steal `ptr[23]` for device select.~~ **Superseded by D19.**
4. **`TRANSFER_LEN == 0`:** no-op descriptor - skip data moves; immediately follow `NEXT_TCD` (device from `NEXT_TCD[23]`) - **still binding**.
5. ~~`NEXT_TCD == 0x000000` end-of-chain.~~ **Superseded by D18** (valid address); end-of-chain is **`QUIT`** per D19.

~~**Rejected then:** `ptr[23]` device-select lean.~~ **Re-adopted by D19** (APS6404L already uses only `A[22:0]`; MSB is free for device select).

~~**Still open:** head device at START.~~ **Superseded by D18** (fixed head at address 0 / PSRAM 0).

## D14 - Host protocol: idle / START / DONE / abort / pass-through

**Decision:**

| Rule | Behavior |
|---|---|
| Idle wait | From **IDLE**, accept the post-sync, rising-edge-detected one-`clk` **START** pulse |
| START while busy | Pulse is **ignored and not queued**; a later command requires a new rising edge after IDLE returns |
| End of chain | ~~`NEXT_TCD == 0`.~~ ~~Both-devices stop (D18).~~ **Superseded by D19:** `CTRL_FLAGS.QUIT=1` after fetch → **IDLE** (no execute). **D23:** next START always refetches fixed head at `0x000000` / PSRAM 0 |
| DONE | Asserted **whenever** the ASIC is IDLE (including after reset / before first START) |
| Pass-through | ~~Enabled iff DONE (idle); disabled while not idle (DMA active).~~ **Superseded by D22:** MCU may drive `uio` only while `BUS_GNT` is high (request/grant). |
| Abort | ~~If **ABORT** asserted while active: finish the **current QPI transaction**, then transition to IDLE~~ **Superseded by D23:** no ABORT pin; use **`rst_n`** to stop a runaway DMA |

~~Pin indices open for ABORT / head.~~ **Superseded by D18:** `ui_in[0]=START`, ~~`ui_in[1]=ABORT`~~, `uo_out[0]=DONE`; no head-pointer pins. **D23:** ABORT removed; `ui_in[1]` reserved. **D22:** `ui_in[2]=BUS_REQ`, `uo_out[1]=BUS_GNT`; `ui_in[7:3]` / `uo_out[7:2]` still open for status/DFT.

**Supersedes Q4 (partial):** no host-ACK gate for IDLE restore. **Superseded for drive legality by D22:** MCU drives only under `BUS_GNT`, not merely when DONE.

## D15 - QPI default for DMA data path

**Decision:**

1. **QPI is the default** for all ASIC PSRAM **data** read/write (descriptor fetch and byte copy).
2. **SPI is never used** for reading or writing payload / TCD data.
3. ~~SPI may be used for config / Enter Quad on the ASIC.~~ **Superseded by D17:** ASIC emits **no** SPI and **no** Enter/Exit Quad; MCU owns mode bring-up via pass-through.
4. ~~Primary QPI read opcode open.~~ **Superseded by D17:** sole QPI read opcode is **`0xEB`**.

## D16 - Clock target 66 MHz; rising-edge RX sample

**Decision:**

1. Design / demoboard **system `clk`:** **66 MHz**.
2. QSPI engine generates pad **SCK as a registered toggle** while enabled → **SCK = clk/2** (≈ 33 MHz); SCK held low in pad/idle states. Do **not** mux/gate `clk` onto the SCK pad.
3. Sample PSRAM read data on the **rising** edge of SCK (captured into `clk`-domain `rdata`; pulse `rdata_valid`). No falling-edge RX path in V1.
4. DLL / pattern-based eye training remains a V1 non-goal.
5. Phase 3 must **re-check** `tACLK` / board / TT / RP2040 clocking against **66 MHz clk / 33 MHz SCK** rising-edge before shuttle freeze.
6. Treat **66 MHz as the maximum system clock**, not merely a nominal target. **Amended by D27:** the original sky130 GPIO rating justification (66 MHz in / 33 MHz out) **no longer applies**. Keep the same numbers because (a) the TT demoboard clock generator tops out around **66.5 MHz**, (b) half-rate registered SCK eases APS6404L `tACLK` vs a full-rate 66 MHz pad clock, and (c) SCK=clk/2 avoids fragile clock-gate/mux of `clk` onto the pad. Close I/O with IHP pad delay + TT mux STA + demoboard, not a published pad MHz ceiling.

**Why (amended D27):** Demoboard / PSRAM / implementation simplicity, not sky130 pad ratings. Half-rate SCK remains within APS6404L Linear Burst capability. IHP Open PDK `sg13g2_io` provides delay / load / transition limits only - **no MHz toggle rating** found in typ/fast/slow IO PDF datasheets or liberty.

## D17 - MCU owns QPI enter/exit; sole QPI read is `0xEB`

**Decision:**

1. **MCU** (via pass-through while DONE) owns PSRAM reset / Enter Quad (`0x35`) / Exit Quad (`0xF5`) for **each device** DMA will touch. ASIC does **not** emit `0x35`, `0xF5`, `0x66`, or `0x99`.
2. Before START, ASIC **expects** both devices already in **QPI mode**. After DONE, MCU may Exit Quad (or reset) if firmware needs SPI again.
3. ASIC QPI data opcodes only: Fast Read Quad **`0xEB`** (sole read; 6 wait cycles) and Write **`0x02`**. No `0x0B` path.
4. Closes Q2 and Q8.

**Why:** Cuts SPI config FSM and dual wait-length read paths from the 2-tile budget; mode bring-up is already natural MCU firmware work during pass-through.

## D18 - Fixed head; ABORT pin; both-devices stop TCD

**Decision (stop encoding superseded by D19; ABORT pin superseded by D23; fixed head / address-0 still binding):**

1. ~~**`ui_in[1] = ABORT`** (next free `ui_in` after START). Behavior unchanged from D14 (finish current QPI txn → IDLE).~~ **Superseded by D23:** no ABORT; `ui_in[1]` reserved; stop a run with `rst_n`.
2. **No head-pointer / arm input vector.** Remove on-chip head register (~24 DFFs saved).
3. On START, always fetch the first TCD from **address `0x000000` on PSRAM 0** (device 0; pointer encoding `0x000000` with `ptr[23]=0`). Applies after reset **and** after a prior `QUIT` return to IDLE (D23).
4. **Address `0x000000` is a valid TCD/buffer address** (null-at-zero revoked). `NEXT_TCD` with address bits 0 is a normal link (device from `NEXT_TCD[23]`), not end-of-chain.
5. ~~**End-of-chain / DONE:** both-devices one-hot `2'b11`.~~ **Superseded by D19:** `CTRL_FLAGS.QUIT`.
6. ~~**Device-select encoding** in `CTRL_FLAGS`.~~ **Superseded by D19:** `ptr[23]`.

7. Closes Q3 pin packing for START/~~ABORT~~/head; closes Q5 null-head / null-next items; closes Q13 head-device open (device 0). **ABORT pin revoked by D23.**

**Why:** Eliminates host pin bandwidth for a 24-bit head; fixed entry point is enough for V1 demos. Address 0 remains usable for the head TCD on device 0. Empty run: place a `QUIT` TCD at `0x000000` on PSRAM 0 (D19).

## D19 - `ptr[23]` device select; `QUIT` flag (replace both-devices stop)

**Decision:**

1. **Remove** `SRC_DEV` / `DEST_DEV` / `NEXT_DEV` from `CTRL_FLAGS`.
2. **Device select** is the **MSB of each pointer**:
   - `ptr[23] = 0` → PSRAM 0
   - `ptr[23] = 1` → PSRAM 1
   - QSPI address phase drives **`ptr[22:0]`** as device `A[22:0]` (full 8 MB per device; MSB is not a byte address bit on APS6404L).
3. Pointer increments bump only the address field and **preserve `ptr[23]`** for the life of that TCD's SRC/DEST (device does not change mid-descriptor).
4. **`CTRL_FLAGS` V1 map:**

| Bits | Name | Encoding |
|---|---|---|
| 0 | `QUIT` | `1` = after FETCH, go IDLE / DONE **without** executing this TCD; `0` = run (or no-op if `LEN==0`) |
| 7:1 | reserved | Write 0; post-V1 (ALU / cond-stop / ring) |

~~5. Device select on all three pointers including `NEXT_TCD[23]`.~~ **Superseded by D24:** device selects are **`CTRL_FLAGS.SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`**; pointers are addresses only; reserved becomes `[7:4]`.

5. **End of chain / DONE:** after `STATE_FETCH`, if `QUIT==1`, transition to IDLE / DONE (no copy). Never assert both CS lines for a quit TCD.
6. **Fixed head unchanged (D18):** first TCD still at address **`0x000000` on PSRAM 0**. Address **0 remains allowed** for TCDs/buffers (`NEXT_TCD` address bits may be 0). Empty run: `QUIT=1` TCD at head.
7. Re-closes Q1 / Q5 / Q13 for the new encoding. Supersedes D13 device-in-flags and D18 both-devices stop.

**Why:** One flag bit is enough for chain end once address 0 is valid; packing device into `ptr[23]` removes six device bits from flags and matches the unused protocol/address MSB on this PSRAM class.

**DFF / tile impact:** still **11-byte** TCD / **88 DFFs** working metadata; CS mux keys off pointer MSBs instead of flag fields.

## D20 - 1-byte data buffer; depth-agnostic correctness

**Decision:**

1. V1 on-chip RX→TX **data buffer depth is 1 byte** (`N=1`, 8 DFFs).
2. Descriptor FSM and QSPI engine **must not depend on a specific `N` for correctness**. Treat buffer depth as module parameter `DMA_BUF_DEPTH` (default 1) on `tt_um_lahnb_sgdma` / `sys_controller`: each copy step moves `k = min(N, TRANSFER_LEN)` bytes, then advances SRC/DEST/`TRANSFER_LEN` by `k`. Package `DMA_BUF_DEPTH_MAX` sizes QPI interface widths for the verification sweep ceiling.
3. Changing `N` later (deeper scratch for fewer cmd+addr reissues) is a **performance / DFF trade only** - no TCD format, host protocol, or cross-device CS rule changes.
4. At V1 `N=1` (and 11-byte TCD fetch), `tCEM` and Linear Burst one-page-cross are **not binding** - no CE# refresh timer or page slicer. **Thresholds at 33 MHz SCK** if a later design holds CE# for a full `N`-byte payload: first `tCEM` (4 us extended) violation at **`N ≥ 60`** on `0xEB` read (**`N ≥ 63`** on `0x02` write); first possible two-page-cross at **`N ≥ 1026`**. Page limit is unreachable before `tCEM` fails. See human `descriptor-fsm.md`.

**Why:** Keeps V1 DFF cost minimal while avoiding a byte-hardcoded datapath that would need a redesign to widen. Soft 2-tile budget (~500 DFFs) cannot host a `tCEM`-sized scratch (~59 B read budget at 33 MHz SCK) anyway; with `N=1`, refresh/page physics are satisfied by construction.

**DFF / tile impact:** **+8 DFFs** for the V1 hold (already assumed in working-reg notes). Larger `N` costs `~8*N` DFFs plus a small fill/count; not planned for V1.

## D21 - Descriptor FSM ↔ QSPI engine handshake

**Decision:**

1. FSM issues a **transaction request** (not a TCD slice): `cmd`, `addr`, `device_sel`, exact `byte_len` (`qspi_pkg` types; `device_sel` ≠ pad CE#). `byte_len` width is `QPI_BYTE_LEN_W = $clog2(QPI_MAX_BYTES + 1)` with `QPI_MAX_BYTES = max(DMA_BUF_DEPTH_MAX, QPI_TCD_BYTES)`. Actual buffer depth `N` is module parameter `DMA_BUF_DEPTH` (default 1) on `tt_um_lahnb_sgdma` / `sys_controller`.
2. Start is a **1-cycle `txn_valid` pulse**, legal only when **`~busy`**. There is **no `txn_ready`** port (`busy` is the start qualifier; CE# pad + `tCPH` are folded into `busy` / idle sequencing).
3. Engine does **not** latch the request; FSM must hold `{cmd, addr, device_sel, byte_len}` stable from `txn_valid` until `busy` low.
4. Data path is nibble-wide (`rdata`/`wdata` `[3:0]`); two SCK beats per payload byte.
5. Read: on each rising SCK in the data phase, engine captures `sio_in` → `rdata` and pulses **`rdata_valid`** one `clk`. FSM always sinks; engine transfers exactly `2 * byte_len` nibbles.
6. Write: first nibble on `wdata` with `txn_valid`. Engine pulses **`wdata_next`** on **falling SCK** iff another nibble is required to finish the accepted transaction; FSM must place the next nibble on `wdata` **before the next `clk` cycle** (same-cycle response) so setup time into the SPI/SIO path is preserved for the following rising SCK. This produces exactly `2 * byte_len - 1` pulses and no extraneous pulse after the final nibble or outside the active write. **No `wdone`:** engine ends the write after `2 * byte_len` SCK beats, then end-pad / raise CE#.
7. Engine **never stalls** SCK/CE# for the FSM; owns CE# start (`CS_ON`) / end (`SCLK_OFF` then `CS_OFF`) pad and ≥2-`clk` `tCPH` (`CS_OFF` + `IDLE`).
8. FSM grants `uio_oe` while `busy`; reclaims when `busy` clears.

**Why:** Minimal FSM↔engine surface (`busy` / pulsed nibble beats / length-driven end). Dropping `txn_ready` and `wdone` avoids redundant handshake state; half-rate SCK + edge-timed `wdata_next` keeps TX setup clean without gating `clk` onto the pad.

**DFF / tile impact:** SCK toggle + edge detects + `rdata` hold / valid pulse + beat counters; no request-shadow flops; no extra buffer beyond D20.

## D22 - Pass-through request / grant (`BUS_REQ` / `BUS_GNT`)

**Decision:**

1. **Pins:**
   - `ui_in[2]` = **`BUS_REQ`** (MCU → ASIC): MCU wants the shared bidirectional QSPI `uio` bus.
   - `uo_out[1]` = **`BUS_GNT`** (ASIC → MCU): MCU has been given control of that bus (ASIC drivers released).
2. **MCU drive rule:** MCU keeps its QSPI GPIOs Hi-Z unless **`BUS_GNT` is high**. While `BUS_GNT` is low, MCU lets the ASIC drive (or float) the bidirectional nets.
3. **ASIC priority:** MCU bus request has priority over DMA. While `BUS_REQ` is high, the descriptor FSM must **not start a new QPI transaction**. If a QPI transaction is already in flight, it completes atomically (`busy` → 0), then ASIC **releases** `uio_oe` and asserts `BUS_GNT`. Single QPI operations remain atomic (no mid-command tear).
4. **Grant / release sequence (release before seize):**
   1. MCU asserts `BUS_REQ` (drivers still Hi-Z).
   2. ASIC finishes any in-flight QPI txn, releases `uio_oe = 0`, asserts `BUS_GNT`.
   3. MCU sees `BUS_GNT`, enables its QSPI drivers, runs SPI/QSPI as needed.
   4. MCU finishes, Hi-Zs its QSPI GPIOs, then deasserts `BUS_REQ`.
   5. ASIC deasserts `BUS_GNT` and **resumes bus-keeper drive** (D26). If not IDLE, DMA may resume (next txn after grant falls).
5. **Idle:** when IDLE/`DONE`, grant follows request promptly (`BUS_GNT` tracks `BUS_REQ` once OE is clear). Idle alone does **not** authorize MCU drive without grant. While idle and `~BUS_GNT`, ASIC still parks the bus (D26).
6. **START:** the top level synchronizes the raw level and rising-edge detects it into a one-`clk` pulse. The pulse is accepted only in IDLE with **`~BUS_REQ`** (hence `~BUS_GNT`); otherwise it is ignored and not queued. MCU must drop request, see grant low, and issue a new START rising edge.
7. **`BUS_REQ` vs kill:** `BUS_REQ` **pauses** an active run between atomic txns and yields the bus; DMA resumes when request is released (unless quit / `rst_n` also applies). ~~ABORT~~ removed by **D23**; use **`rst_n`** to stop a runaway chain.
8. Supersedes D14 "pass-through iff DONE" for drive legality. Closes Q3/Q4 remainder for this handshake.

**Why:** Explicit request/grant removes the race of MCU assuming DONE ≡ safe to drive, and lets firmware reclaim flash/PSRAM mid-DMA without ending the whole chain, while keeping QPI CE# windows intact.

**DFF / tile impact:** ~1 registered `BUS_GNT` (plus FSM "yield / no new txn while REQ" gating). Negligible vs 2-tile budget.

**Host CDC:** MCU `ui_in` levels (incl. `BUS_REQ`) are async to design `clk`. The **top-level** module two-flop-synchronizes START and BUS_REQ, then rising-edge detects synchronized START into the one-`clk` pulse consumed by `sys_controller`; BUS_REQ remains a level (~5 DFFs total). See `03-architecture.md` § Top module / host-input synchronizers.

## D23 - No ABORT pin; quit → IDLE; next START from fixed head

**Decision:**

1. **Remove host ABORT.** There is no `ui_in` ABORT strobe and no `dma_abort` / soft-abort path in V1. ~~D14/D18 abort behavior~~ revoked.
2. **Kill a runaway DMA with `rst_n`.** Asserting active-low reset (`rst_n=0`) returns the ASIC to IDLE (`DONE` high) and the top level forces every shared `uio_oe` bit low. Board CS pull-ups (D26) keep CE# high while reset holds. After `rst_n` deasserts with `~BUS_GNT`, ASIC resumes bus-keeper parking. Firmware must re-establish PSRAM QPI mode after reset if needed (D17).
3. **`ui_in[1]`** (formerly ABORT) is **reserved** / open with `ui_in[7:3]`. **BUS_REQ** stays at **`ui_in[2]`** (D22).
4. **`QUIT` end-of-chain (clarifies D19):** when the FSM fetches a TCD with **`CTRL_FLAGS.QUIT=1`**, it returns to **IDLE** / asserts **DONE** without executing that TCD. The next accepted **START** always begins a new run by fetching from **address `0x000000` on PSRAM 0** again (fixed head; D18) - it does not resume from a saved `NEXT_TCD` or mid-chain pointer.
5. Mid-run bus yield remains **BUS_REQ** / **BUS_GNT** only (D22).

**Why:** Saves a host pin, ~2 sync DFFs, and abort FSM paths inside the 2-tile budget. Normal chain end is `QUIT`; emergency stop is reset. Fixed-head restart after quit keeps firmware and hardware simple.

**DFF / tile impact:** removes ~2 sync DFFs and abort-path control; negligible but favorable vs prior D14/D18 abort plan.

## D24 - Device selects in `CTRL_FLAGS` (`SRC_DEVICE` / `DEST_DEVICE` / `NEXT_DEVICE`)

**Decision:**

1. **Revoke pointer-MSB device select (D19) for V1.** `SRC_PTR` / `DEST_PTR` / `NEXT_TCD` are **byte addresses** only. QSPI address phase uses `ptr[22:0]`; `ptr[23]` is unused (drive 0).
2. **Three device-select flags** live in **`CTRL_FLAGS`**, taking former reserved bits so the TCD stays **11 bytes / 88 bits**:

| Bits | Name | Encoding |
|---|---|---|
| 0 | `QUIT` | unchanged (D19/D23) |
| 1 | `SRC_DEVICE` | device for reads of `SRC_PTR` (`0`=PSRAM 0, `1`=PSRAM 1) |
| 2 | `DEST_DEVICE` | device for writes of `DEST_PTR` |
| 3 | `NEXT_DEVICE` | device for the next FETCH of `NEXT_TCD` |
| 7:4 | reserved | Write 0; post-V1 (was `[7:1]`; three bits claimed for device selects) |

3. While bytes remain after a completed chunk, pointer increments bump only the address field (`[22:0]`); device flags are sticky for the life of that TCD (device does not change mid-descriptor). After the final chunk makes `TRANSFER_LEN=0`, the working pointers need not be incremented because that descriptor no longer consumes them.
4. After a data (or no-op) TCD completes, the next FETCH uses `{NEXT_DEVICE, NEXT_TCD[22:0]}`.
5. Working metadata remains **88 DFFs** / **11-byte** TCD (repack inside `CTRL_FLAGS`; no width growth).

**Why:** One explicit device flag per pointer keeps SRC/DEST/NEXT independently selectable for cross-device chains without encoding device in address MSBs, while preserving the 88-bit working set.

**DFF / tile impact:** none vs prior 11-byte TCD (flags repack only). Supersedes D19 pointer-MSB device select and the earlier D24 draft that only moved `NEXT_DEVICE`.

## D25 - Big-endian TCD pointer fields

**Decision:**

1. The three 24-bit TCD pointer fields (`SRC_PTR`, `DEST_PTR`, and `NEXT_TCD`) use **big-endian byte order** in PSRAM: the most-significant byte is stored at the lowest address. Pointer `0x123456` is serialized as bytes `12 34 56`.
2. The byte offsets and 11-byte TCD size do not change. `TRANSFER_LEN` remains at offset 6 and `CTRL_FLAGS` remains at offset 10.
3. `CTRL_FLAGS` bit numbering does not change: `QUIT` is bit 0, followed by `SRC_DEVICE`, `DEST_DEVICE`, and `NEXT_DEVICE` in bits 1 through 3.
4. Firmware must explicitly serialize pointer bytes into an 11-byte buffer. It must not copy native little-endian MCU integers or a padded C structure directly into PSRAM.
5. This decision changes the firmware-visible memory format and documentation only. The existing RTL fetch ordering remains unchanged. Payload data remains byte-preserving and receives no endian conversion.

**Why:** The existing QPI fetch and packed working-register order already decode most-significant bytes first. Keeping that order avoids pointer byte-reversal logic in the ASIC. Since C has no standard 24-bit integer type and the 11-byte TCD already requires explicit packing, big-endian pointer serialization adds little firmware complexity while favoring the two-tile implementation budget.

**DFF / tile impact:** none. This freezes the existing RTL interpretation and places byte-order handling in firmware.

## D26 - ASIC bus keeper; board CS pull-ups

**Decision:**

1. **ASIC is the shared-bus keeper whenever it has not granted the bus.** While `~BUS_GNT` (and outside hard reset), the ASIC actively drives:
   - **Flash CS**, **RAM A CS**, and **RAM B CS** high (deselected)
   - **SCK** low
   - **SIO0..3** according to QPI phase when a transaction is live: drive on cmd / addr / write; **float only on dummy/wait and read-data** (listen / sample `uio_in`). ~~Between transactions and in IDLE, SIO may float or drive a don't-care~~ **Resolved in RTL:** SIO drives a don't-care (`0`) between transactions and in IDLE rather than floating, so no shared `uio` pin is ever left undriven while the ASIC is bus keeper; CS and SCK stay driven throughout.
2. **Release for grant or reset.** On `BUS_GNT`, ASIC forces **all** shared `uio_oe` bits off so the MCU can master the bus. While active-low reset is asserted (`rst_n=0`), the top level also forces every shared output enable low, but reset does not itself grant MCU ownership. Resume parking after grant falls or reset deasserts when `~BUS_GNT`.
3. **Flash never selected by ASIC.** Parking flash CS high is not flash DMA; the ASIC never drives flash CS low and emits no flash opcodes (D11 still binding for flash-out-of-V1).
4. **Inter-transaction OE:** do **not** release CS/SCK between DMA transactions. That preserves CE# high and avoids floating selects without depending on firmware latency.
5. **Board hardware:** the QSPI PMOD / demoboard path in use has a **10 kΩ pull-up on each CS** (flash, RAM A, RAM B). Those resistors are the keeper while `rst_n=0`, during power-up, and in any window before the TT mux enables this design. They are a backup, not a substitute for ASIC parking while the design is live and `~BUS_GNT`.
6. **Handoff:** release-before-seize remains required. Overlap on CS-high / SCK-low idle levels is benign if it occurs briefly; firmware must still Hi-Z before dropping `BUS_REQ` and before START.
7. Supersedes D8/D11/D22 language that idle or between-txn means full `uio_oe=0`, and the D11 claim that flash CS OE stays off for the whole DMA run.

**Why:** Without continuous ASIC drive of CS/SCK while not granted, CE# can float into the low region long enough to approach `tCEM` and block PSRAM refresh, and a floating flash CS while SCK toggles is unsafe. Board 10 kΩ pull-ups cover reset-scale gaps; ASIC parking covers run-time gaps at zero extra DFFs.

**DFF / tile impact:** none beyond existing OE mux logic (combinational park vs grant).

Detail: `docs/human/architecture/blocks/host-interface.md`, `docs/human/architecture/firmware.md`, `docs/human/architecture/blocks/descriptor-fsm.md`.

## D27 - Target IHP SG13G2 shuttle (TTIHP26b); retire sky130 GPIO ceilings

**Decision:**

1. Tapeout vehicle is **Tiny Tapeout IHP** (**TTIHP26b** class), PDK **`ihp-sg13g2`** (IHP Open PDK), hardened with **LibreLane** via `ttihp-verilog-template` / `tt-gds-action@ttihp26b`. Drop sky130 / TTSKY26c as the planning PDK.
2. Keep the digital **user-module port list** unchanged (`clk`, `ena`, `rst_n`, `ui_in[7:0]`, `uo_out[7:0]`, `uio_in`/`uio_out`/`uio_oe[7:0]`) - identical across TT PDKs.
3. Electrical / pad model for planning:
   - Core **1.2 V**, I/O **3.3 V** with on-pad level shifters (`sg13g2_IOPadIn`, `*Out*`, `*InOut*`).
   - Chip-level TT wrapper uses **`sg13g2_IOPadIn`** for `clk`/`rst_n`/`ui_in`, **`sg13g2_IOPadOut30mA`** for `uo_out`, **`sg13g2_IOPadInOut30mA`** for `uio` (not the 4 mA variants).
4. **I/O speed:** after searching local `IHP-Open-PDK` IO library docs (`sg13g2_io_*.pdf`) and liberty (`sg13g2_io_typ_1p2V_3p3V_25C.lib`), **no published maximum toggle frequency** exists for these pads. Cite delay / `max_capacitance` / `max_transition` + TT mux `signoff.sdc` budgets instead. **Do not** treat sky130's 66 MHz input / 33 MHz output / 4 mA pad ratings as binding on this project.
5. **Clock policy (D16) stays numerically the same** (66 MHz `clk`, SCK=clk/2, rising-edge RX) with the amended justification in D16. Phase 3 checks `T-GPIO-IN` / `T-GPIO-OUT` / `T-GPIO-LIB` in `11-timing-analysis.md`.
6. **Tiles:** use IHP tile boxes (1x1 ≈ 202.08 × 154.98 µm; 1x2 ≈ 202.08 × 313.74 µm). Soft ~500 DFF / 2-tile ceiling retained until first IHP synthesis; do not assume sky130 DFF density.
7. Wrapper / flow notes: chip top is `tt_ihp_wrapper` (not caravel openframe); project GL netlists are unpowered (`nl`); template default `CLOCK_PERIOD` is 20 ns (50 MHz) - raise to ~15.15 ns when targeting 66 MHz in `src/config.json` / `info.yaml`.

**Why:** User selected the IHP shuttle / template. SG13G2 3.3 V I/O keeps the APS6404L / QSPI PMOD plan intact. Stronger TT pad drive (30 mA) removes the sky130 output-slew argument that forced SCK≤33 MHz; half-rate SCK is kept for other reasons (D16).

**DFF / tile impact:** none to RTL; area/DFF heuristics need an IHP harden before any budget relaxation.

Detail: `02-constraints.md`, `11-timing-analysis.md`, human `architecture/limitations.md`.

## D28 - FPGA hardware validation before shuttle freeze

**Decision:**

1. Add **M7 - FPGA hardware validation** to the verification ladder (`verification/01-strategy.md`): before RTL is frozen for the shuttle, load the synthesizable RTL onto an FPGA that occupies the ASIC's position on the same carrier board and MCU the eventual demoboard will use, with real dual PSRAM devices.
2. M7 exercises a high-value `TC-*` hardware regression subset (same-device copies, both cross-device directions, chaining, `QUIT`, zero length, bus handoff, and reset recovery) driven by real MCU firmware rather than cocotb.
3. M7 may require adapting existing testbench-derived stimulus and writing new MCU firmware test code outside the cocotb `test/` tree; that firmware is retained and tied to the RTL revision it validated.
4. M7 gates shuttle freeze alongside M6. It does not replace M6 (which requires the actual synthesized ASIC netlist) and closes no `T-*` row, since FPGA I/O electrical characteristics differ from IHP pads.

**Why:** Catches firmware and system-integration bugs that a cocotb PSRAM model, an idealized clock, or a symbolic formal environment cannot expose, using the cheapest available real-hardware checkpoint before an irreversible shuttle commit.

**DFF / tile impact:** none; process/verification decision only.

## D29 - SCK parked while deselected is a checked protocol requirement, not an architecture preference

**Context:** `verification/04-timing-in-sim.md` and `verification/03-psram-model.md` previously stated that SCK toggling while every RAM CE# is high is "not a universal device-protocol error" and only required by this design's own `CS_ON`/`SCLK_OFF`/`CS_OFF` padding choice.

**Decision:** Treat SCK toggling while no device is selected as an erroneous SCK cycle in every case, not only for this design's own padding style. APS6404L-class devices define clocked behavior only while CE# is low; the shared bus adds flash CS and both RAM CE#s, so the check is: SCK stays low for the entire interval during which flash CS, RAM A CE#, and RAM B CE# are all high (both engine CS outputs at L0, where flash CS is not an engine port). This is checked by new stable IDs `Q-SCKIDLE` (`verification/04-timing-in-sim.md`) and `CHK-PIN-SCK-PARK` (`verification/06-checkers.md`), and applies regardless of which side of the shared bus (ASIC or MCU pass-through) currently owns drive.

**Relationship to existing checks:** `CHK-ARB-PARK` is unchanged and remains scoped to the ASIC's own driven value while `~BUS_GNT`. `Q-SCKIDLE` / `CHK-PIN-SCK-PARK` are a stricter, always-on, resolved-bus-level check that also catches an errant MCU-side SCK toggle with no device selected.

**Why:** The datasheet only defines SCK behavior while CE# is low; treating an idle-time SCK toggle as benign would hide a real firmware or arbitration bug on real hardware even though the current architecture's own padding never produces one.

**DFF / tile impact:** none; verification-catalog decision only. No RTL change is implied because `qspi_engine`'s existing padding already satisfies the stricter check.
