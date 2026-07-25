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

**Decision (current, updated by D18 / D19):** **24-bit** internal pointers in TCD / working regs (no head register). QSPI address phase uses device `A[22:0]` from `ptr[22:0]`. ~~Device select in `CTRL_FLAGS` (D13/D18).~~ **Superseded by D19:** `ptr[23]` selects die. ~~`0x000000` reserved null.~~ **Superseded by D18:** address 0 is valid (fixed head on PSRAM 0). ~~End-of-chain = both-devices stop.~~ **Superseded by D19:** `CTRL_FLAGS.QUIT`.

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

**Encoding:** ~~superseded by D13 (`CTRL_FLAGS` device bits).~~ **Superseded by D19:** device in `ptr[23]`.

## D12 - V1 = dual-PSRAM bulk mover; telemetry extras post-V1

**Decision:**

1. **V1 product:** isolated descriptor DMA for **learning / resume** and **bulk moves between PSRAM A and B** (same-device and cross-device). ADC / live sensor ingress is not a V1 commitment.
2. **V1 TCD:** **11-byte** memmove record (`SRC`, `DEST`, `LEN`, `NEXT`, `CTRL_FLAGS`). ~~Flags carry device select (D13).~~ **D19:** flags carry **`QUIT` only** in V1; device is `ptr[23]`. No ALU, ring, or conditional stop.
3. **V1 host:** START / DONE / abort behavior in D14; pin indices and fixed head in **D18** (`ui_in[0]=START`, `ui_in[1]=ABORT`).
4. **Post-V1 add order** (documented in `10-post-v1-features.md`):
   1. In-flight byte ALU (extend reserved `CTRL_FLAGS` bits + `IMM`, `STATE_PROCESS`)
   2. Conditional stop (LT/Z/NZ after READ; `LEN==0` until; needs abort)
   3. Ring / modulo addressing
   4. ASIC flash read, then maybe flash write (extends D11 stretch)

**Why:**

- Matches the realistic demoboard story without ADC integration.
- Shrinks verification surface for the shuttle vs telemetry extras.
- Keeps a clear implement-later path so telemetry-shaped ideas are not lost.

**DFF / tile impact:** V1 pays **8 DFFs** for a reserved `CTRL_FLAGS` byte (**1 used** for `QUIT` per D19). Post-V1 ALU/IMM/ring reintroduce further cost in the order above.

## D13 - `CTRL_FLAGS` device select (reject pointer MSB)

**Decision (superseded for device/stop encoding by D19):**

1. Re-add a full **`CTRL_FLAGS` byte** to the V1 TCD (**11 bytes** total; working metadata **88 DFFs**) - **still binding**.
2. ~~V1 device fields in `CTRL_FLAGS`.~~ **Superseded by D19:** device is `ptr[23]`; `CTRL_FLAGS` holds `QUIT` + reserved.
3. ~~Do not steal `ptr[23]` for device select.~~ **Superseded by D19.**
4. **`TRANSFER_LEN == 0`:** no-op descriptor - skip data moves; immediately follow `NEXT_TCD` (device from `NEXT_TCD[23]`) - **still binding**.
5. ~~`NEXT_TCD == 0x000000` end-of-chain.~~ **Superseded by D18** (valid address); end-of-chain is **`QUIT`** per D19.

~~**Rejected then:** `ptr[23]` device-select lean.~~ **Re-adopted by D19** (APS6404L already uses only `A[22:0]`; MSB is free for die select).

~~**Still open:** head die at START.~~ **Superseded by D18** (fixed head at address 0 / PSRAM 0).

## D14 - Host protocol: idle / START / DONE / abort / pass-through

**Decision:**

| Rule | Behavior |
|---|---|
| Idle wait | From **IDLE**, wait for **START**; accept START only in IDLE |
| START while busy | **Ignored** until the ASIC returns to IDLE |
| End of chain | ~~`NEXT_TCD == 0`.~~ ~~Both-devices stop (D18).~~ **Superseded by D19:** `CTRL_FLAGS.QUIT=1` after fetch → **IDLE** (no execute) |
| DONE | Asserted **whenever** the ASIC is IDLE (including after reset / before first START) |
| Pass-through | ~~Enabled iff DONE (idle); disabled while not idle (DMA active).~~ **Superseded by D22:** MCU may drive `uio` only while `BUS_GNT` is high (request/grant). |
| Abort | If **ABORT** asserted while active: finish the **current QPI transaction**, then transition to IDLE (DONE; pass-through via D22 grant if requested) |

~~Pin indices open for ABORT / head.~~ **Superseded by D18:** `ui_in[0]=START`, `ui_in[1]=ABORT`, `uo_out[0]=DONE`; no head-pointer pins. ~~`ui_in[7:2]` / `uo_out[7:1]` still open.~~ **D22:** `ui_in[2]=BUS_REQ`, `uo_out[1]=BUS_GNT`; `ui_in[7:3]` / `uo_out[7:2]` still open for status/DFT.

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

**Why:** Avoids fragile clock-gate/mux of `clk` onto SCK while keeping a simple rising-edge RX path. Half-rate SCK eases `tACLK` margin vs a full-rate 66 MHz pad clock; still within APS6404L Linear Burst capability.

## D17 - MCU owns QPI enter/exit; sole QPI read is `0xEB`

**Decision:**

1. **MCU** (via pass-through while DONE) owns PSRAM reset / Enter Quad (`0x35`) / Exit Quad (`0xF5`) for **each die** DMA will touch. ASIC does **not** emit `0x35`, `0xF5`, `0x66`, or `0x99`.
2. Before START, ASIC **expects** both dies already in **QPI mode**. After DONE, MCU may Exit Quad (or reset) if firmware needs SPI again.
3. ASIC QPI data opcodes only: Fast Read Quad **`0xEB`** (sole read; 6 wait cycles) and Write **`0x02`**. No `0x0B` path.
4. Closes Q2 and Q8.

**Why:** Cuts SPI config FSM and dual wait-length read paths from the 2-tile budget; mode bring-up is already natural MCU firmware work during pass-through.

## D18 - Fixed head; ABORT pin; both-devices stop TCD

**Decision (stop encoding superseded by D19; fixed head / ABORT / address-0 still binding):**

1. **`ui_in[1] = ABORT`** (next free `ui_in` after START). Behavior unchanged from D14 (finish current QPI txn → IDLE).
2. **No head-pointer / arm input vector.** Remove on-chip head register (~24 DFFs saved).
3. On START, always fetch the first TCD from **address `0x000000` on PSRAM 0** (device 0; pointer encoding `0x000000` with `ptr[23]=0`).
4. **Address `0x000000` is a valid TCD/buffer address** (null-at-zero revoked). `NEXT_TCD` with address bits 0 is a normal link (die from `NEXT_TCD[23]`), not end-of-chain.
5. ~~**End-of-chain / DONE:** both-devices one-hot `2'b11`.~~ **Superseded by D19:** `CTRL_FLAGS.QUIT`.
6. ~~**Device-select encoding** in `CTRL_FLAGS`.~~ **Superseded by D19:** `ptr[23]`.

7. Closes Q3 pin packing for START/ABORT/head; closes Q5 null-head / null-next items; closes Q13 head-device open (device 0).

**Why:** Eliminates host pin bandwidth for a 24-bit head; fixed entry point is enough for V1 demos. Address 0 remains usable for the head TCD on device 0. Empty run: place a `QUIT` TCD at `0x000000` on PSRAM 0 (D19).

## D19 - `ptr[23]` device select; `QUIT` flag (replace both-devices stop)

**Decision:**

1. **Remove** `SRC_DEV` / `DEST_DEV` / `NEXT_DEV` from `CTRL_FLAGS`.
2. **Device select** is the **MSB of each pointer**:
   - `ptr[23] = 0` → PSRAM 0
   - `ptr[23] = 1` → PSRAM 1
   - QSPI address phase drives **`ptr[22:0]`** as device `A[22:0]` (full 8 MB per die; MSB is not a byte address bit on APS6404L).
3. Pointer increments bump only the address field and **preserve `ptr[23]`** for the life of that TCD's SRC/DEST (device does not change mid-descriptor).
4. **`CTRL_FLAGS` V1 map:**

| Bits | Name | Encoding |
|---|---|---|
| 0 | `QUIT` | `1` = after FETCH, go IDLE / DONE **without** executing this TCD; `0` = run (or no-op if `LEN==0`) |
| 7:1 | reserved | Write 0; post-V1 (ALU / cond-stop / ring) |

5. **End-of-chain / DONE:** after `STATE_FETCH`, if `QUIT==1`, transition to IDLE / DONE (no copy). Never assert both CS lines for a quit TCD.
6. **Fixed head unchanged (D18):** first TCD still at address **`0x000000` on PSRAM 0**. Address **0 remains allowed** for TCDs/buffers (`NEXT_TCD` address bits may be 0). Empty run: `QUIT=1` TCD at head.
7. Re-closes Q1 / Q5 / Q13 for the new encoding. Supersedes D13 device-in-flags and D18 both-devices stop.

**Why:** One flag bit is enough for chain end once address 0 is valid; packing die into `ptr[23]` removes six device bits from flags and matches the unused protocol/address MSB on this PSRAM class.

**DFF / tile impact:** still **11-byte** TCD / **88 DFFs** working metadata; CS mux keys off pointer MSBs instead of flag fields.

## D20 - 1-byte data buffer; depth-agnostic correctness

**Decision:**

1. V1 on-chip RX→TX **data buffer depth is 1 byte** (`N=1`, 8 DFFs).
2. Descriptor FSM and QSPI engine **must not depend on a specific `N` for correctness**. Treat buffer depth as a parameter: each copy step moves `k = min(N, TRANSFER_LEN)` bytes, then advances SRC/DEST/`TRANSFER_LEN` by `k`.
3. Changing `N` later (deeper scratch for fewer cmd+addr reissues) is a **performance / DFF trade only** - no TCD format, host protocol, or cross-device CS rule changes.
4. At V1 `N=1` (and 11-byte TCD fetch), `tCEM` and Linear Burst one-page-cross are **not binding** - no CE# refresh timer or page slicer. **Thresholds at 33 MHz SCK** if a later design holds CE# for a full `N`-byte payload: first `tCEM` (4 us extended) violation at **`N ≥ 60`** on `0xEB` read (**`N ≥ 63`** on `0x02` write); first possible two-page-cross at **`N ≥ 1026`**. Page limit is unreachable before `tCEM` fails. See human `descriptor-fsm.md`.

**Why:** Keeps V1 DFF cost minimal while avoiding a byte-hardcoded datapath that would need a redesign to widen. Soft 2-tile budget (~500 DFFs) cannot host a `tCEM`-sized scratch (~59 B read budget at 33 MHz SCK) anyway; with `N=1`, refresh/page physics are satisfied by construction.

**DFF / tile impact:** **+8 DFFs** for the V1 hold (already assumed in working-reg notes). Larger `N` costs `~8*N` DFFs plus a small fill/count; not planned for V1.

## D21 - Descriptor FSM ↔ QSPI engine handshake

**Decision:**

1. FSM issues a **transaction request** (not a TCD slice): `cmd`, `addr`, `die_sel`, exact `byte_len` (`qspi_pkg` types; `die_sel` ≠ pad CE#). `byte_len` width is `QSPI_BYTE_LEN_W = $clog2(QSPI_MAX_BYTES + 1)` with `QSPI_MAX_BYTES = max(DMA_BUF_DEPTH, QSPI_TCD_BYTES)`.
2. Start is a **1-cycle `txn_valid` pulse**, legal only when **`~busy`**. There is **no `txn_ready`** port (`busy` is the start qualifier; CE# pad + `tCPH` are folded into `busy` / idle sequencing).
3. Engine does **not** latch the request; FSM must hold `{cmd, addr, die_sel, byte_len}` stable from `txn_valid` until `busy` low.
4. Data path is nibble-wide (`rdata`/`wdata` `[3:0]`); two SCK beats per payload byte.
5. Read: on each rising SCK in the data phase, engine captures `sio_in` → `rdata` and pulses **`rdata_valid`** one `clk`. FSM always sinks; engine transfers exactly `2 * byte_len` nibbles.
6. Write: first nibble on `wdata` with `txn_valid`. Engine pulses **`wdata_next`** on **falling SCK** when the next nibble is required; FSM updates `wdata` for the following rise. **No `wdone`:** engine ends the write after `2 * byte_len` SCK beats, then end-pad / raise CE#.
7. Engine **never stalls** SCK/CE# for the FSM; owns CE# start (`CS_ON`) / end (`SCLK_OFF` then `CS_OFF`) pad and ≥2-`clk` `tCPH` (`CS_OFF` + `IDLE`).
8. FSM grants `uio_oe` while `busy`; reclaims when `busy` clears. ABORT waits for current txn (`busy`→0).

**Why:** Minimal FSM↔engine surface (`busy` / pulsed nibble beats / length-driven end). Dropping `txn_ready` and `wdone` avoids redundant handshake state; half-rate SCK + edge-timed `wdata_next` keeps TX setup clean without gating `clk` onto the pad.

**DFF / tile impact:** SCK toggle + edge detects + `rdata` hold / valid pulse + beat counters; no request-shadow flops; no extra buffer beyond D20.

## D22 - Pass-through request / grant (`BUS_REQ` / `BUS_GNT`)

**Decision:**

1. **Pins:**
   - `ui_in[2]` = **`BUS_REQ`** (MCU → ASIC): MCU wants the shared bidirectional QSPI `uio` bus.
   - `uo_out[1]` = **`BUS_GNT`** (ASIC → MCU): MCU has been given control of that bus (ASIC drivers released).
2. **MCU drive rule:** MCU keeps its QSPI GPIOs Hi-Z unless **`BUS_GNT` is high**. While `BUS_GNT` is low, MCU lets the ASIC drive (or float) the bidirectional nets.
3. **ASIC priority:** MCU bus request has priority over DMA. While `BUS_REQ` is high, the descriptor FSM must **not start a new QPI transaction**. If a QPI transaction is already in flight, it completes atomically (`busy` → 0), then ASIC clears `uio_oe` and asserts `BUS_GNT`. Single QPI operations remain atomic (no mid-command tear).
4. **Grant / release sequence (release before seize):**
   1. MCU asserts `BUS_REQ` (drivers still Hi-Z).
   2. ASIC finishes any in-flight QPI txn, holds `uio_oe = 0`, asserts `BUS_GNT`.
   3. MCU sees `BUS_GNT`, enables its QSPI drivers, runs SPI/QSPI as needed.
   4. MCU finishes, Hi-Zs its QSPI GPIOs, then deasserts `BUS_REQ`.
   5. ASIC deasserts `BUS_GNT`. If not IDLE, DMA may resume (next txn after grant falls).
5. **Idle:** when IDLE/`DONE`, grant follows request promptly (`BUS_GNT` tracks `BUS_REQ` once OE is clear). Idle alone does **not** authorize MCU drive without grant.
6. **START:** accepted only in IDLE with **`~BUS_REQ`** (hence `~BUS_GNT`). MCU must drop request and see grant low before START.
7. **ABORT vs `BUS_REQ`:** ABORT still ends the DMA run (finish current QPI txn → IDLE). `BUS_REQ` **pauses** an active run between atomic txns and yields the bus; DMA resumes when request is released (unless ABORT / quit / reset also applies).
8. Supersedes D14 "pass-through iff DONE" for drive legality. Closes Q3/Q4 remainder for this handshake.

**Why:** Explicit request/grant removes the race of MCU assuming DONE ≡ safe to drive, and lets firmware reclaim flash/PSRAM mid-DMA without aborting the whole chain, while keeping QPI CE# windows intact.

**DFF / tile impact:** ~1 registered `BUS_GNT` (plus FSM "yield / no new txn while REQ" gating). Negligible vs 2-tile budget.

**Host CDC:** MCU `ui_in` levels (incl. `BUS_REQ`) are async to design `clk`. The **top-level** module two-flop-synchronizes `START` / `ABORT` / `BUS_REQ` before `sys_controller` / the descriptor FSM sample them (~2 DFFs per bit). See `03-architecture.md` § Top module / host-input synchronizers.
