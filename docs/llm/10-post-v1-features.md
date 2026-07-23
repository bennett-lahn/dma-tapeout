# Post-V1 Features (Add Later)

Status: **not in V1**. Implementation sketches only. Do not grow V1 RTL or TCD layout for these until an explicit post-V1 cut decides otherwise.

**Suggested add order** (cheapest / most coherent first):

1. In-flight byte ALU
2. Conditional stop (`COND_STOP`)
3. Ring / modulo addressing
4. ASIC flash read, then maybe flash write

V1 product framing (for contrast): isolated descriptor DMA / **bulk mover between dual PSRAM**. ADC telemetry is unlikely for the first shuttle; these features restore telemetry-oriented behavior later.

Human index: `docs/human/architecture/post-v1.md`.

---

## 1. In-flight byte ALU

### Intent

Optional conditioning on the RX-hold → TX path: pass, invert, XOR immediate, ADD/SUB immediate. Telemetry helpers (polarity, dark offset, cheap mask) - not crypto.

### Why deferred

Bulk A↔B memcpy does not need it. Costs TCD bits or an `IMM` byte, working-reg DFFs, FSM/`STATE_PROCESS`, and verification matrix.

### Implement later

1. **Add module** `dma_byte_alu` (combinational 8-bit): ops pass / invert / XOR imm / ADD / SUB.
2. **Extend existing `CTRL_FLAGS`** (V1 uses bit 0 for `QUIT`; reserves `[7:1]`) with ALU op select / ADD vs SUB, and prefer a dedicated **`IMM` byte** (12-byte TCD if flags+imm; or pack tiny imm into flags).
3. **Working registers:** already latch `CTRL_FLAGS`; add `IMM` (+8 DFFs).
4. **FSM:** restore `STATE_PROCESS` between `STATE_READ` and `STATE_WRITE` (or fold combo ALU into the READ→WRITE path with a registered hold). Descriptor **fetch bypasses** the ALU.
5. **Datapath:** `byte = ALU(rx_hold, op, IMM)` then WRITE.
6. **Verify:** each op vs software model; fetch path unchanged; IMM=0 edge cases.

### DFF / tile impact

Low: ~8 DFFs for IMM + a few flag bits in the working TCD set; ALU itself is combo. Flag packing competes with `QUIT` / ring / COND bits if those also return.

### Depends on / enables

- Natural precursor to `COND_STOP` (shared `IMM`).
- Independent of ring.

---

## 2. Conditional stop (`COND_STOP`)

### Intent

After **READ**, before ALU/WRITE: if enabled and `predicate(byte, IMM)` is true, **do not** finish the beat (skip ALU, WRITE, pointer update, length decrement); cleanly end the QSPI beat (raise CE#), then proceed to `NEXT_TCD` (or DONE if next is a `QUIT` TCD).

Predicates (minimal useful set):

| Pred | Meaning |
|---|---|
| `LT` | unsigned `byte < IMM` |
| `Z` | `byte == 0` (`IMM` ignored) |
| `NZ` | `byte != 0` (`IMM` ignored) |

**`TRANSFER_LEN == 0` + `COND_STOP`:** run until predicate (infinite until).  
**`TRANSFER_LEN == 0` without `COND_STOP`:** must remain illegal / no-op / error in whatever policy V1 already froze - do not redefine as until.

**Abort:** V1 should already expose host abort; `COND_STOP` infinite waits rely on it (plus `rst_n`). On abort: raise CE#, release `uio_oe`, sticky ERROR vs DONE policy TBD.

**Terminating byte is not written.** Firmware that needs the trigger sample must re-read it in a following TCD.

### Why deferred

Needs live or changing data to be interesting. On a shared bus, the MCU cannot update a PSRAM mailbox while the ASIC is ACTIVE - so “wait for ADC sample” does not work without a separate ingress path. For an isolated bulk mover, finite `TRANSFER_LEN` is enough.

### Implement later

1. Require **ALU/`IMM` path** (or at least an `IMM` byte + compare) from feature 1.
2. **`CTRL_FLAGS`:** `COND_STOP` enable + 2-bit pred select (`LT` / `Z` / `NZ`).
3. **FSM:** after READ, branch: if cond taken → terminate CE# → FETCH `NEXT_TCD` (or DONE); else existing PROCESS/WRITE/UPDATE.
4. **Policy:** allow cyclic / self `NEXT_TCD` only with abort (and preferably only with `COND_STOP` for until-shaped loops).
5. **Verify:** each pred; skip-write on taken; `LEN==0` until; abort mid-until; CE# high before next FETCH; self-`NEXT` livelock + abort.

### DFF / tile impact

Mostly combo compare + flag bits; `IMM` already counted under ALU. Small FSM edge cost. High verification cost.

### Notes from design discussion

- Cheapest useful control feature is **early exit**, not a second `ALT_NEXT` (+24 DFFs) or `DESC_PC` (+24 DFFs).
- Multi-TCD loop-with-exit to a third descriptor needs `DESC_PC` or `ALT_NEXT` - defer further.
- With memory R/W + until + cyclic TCDs this approaches a tiny while-machine (TC modulo finite RAM); still not a product goal.

---

## 3. Ring / modulo addressing

### Intent

On pointer update, wrap inside an aligned power-of-two window so firmware can keep last-*N* samples without CPU modulo. Dest wrap is the primary ask; source wrap optional.

Suggested update:

```text
DEST_PTR <- (DEST_PTR & ~MASK) | ((DEST_PTR + 1) & MASK)
```

Hardware watermarks / half-full IRQs stay non-goals unless separately scoped.

### Why deferred

Bulk linear A↔B copies do not need wrap. Mask encoding fights `CTRL_FLAGS` budget (`QUIT`, ALU, COND). Software can modulo offline between DMA runs.

### Implement later

1. Add **`CTRL_FLAGS`** bits: dest-ring enable (and optional src-ring); mask size field or fixed mask set.
2. Require buffer **alignment** to window base (simplifies hardware).
3. **UPDATE path:** replace linear `ptr+1` with masked wrap when enabled.
4. **Compose with COND_STOP:** on cond taken, no write → ring pointer does not advance that beat.
5. **Verify:** wrap at every boundary; misaligned base rejected or defined; interaction with cross-device CS.

### DFF / tile impact

Low if mask is a small decoded width on the existing 24-bit pointer update mux. Flag bits are the scarce resource.

### Contrast with `COND_STOP`

| | Ring | `COND_STOP` |
|---|---|---|
| Axis | *Where* next access goes | *When* to leave the TCD |
| Lifetime | Usually finite `TRANSFER_LEN` | May be until-pred |
| Bulk-mover value | Low | Low |

They compose; neither replaces the other.

---

## 4. ASIC flash read / write

### Intent

Let the DMA (or a narrow flash helper FSM) assert **flash CS** (`uio[0]`) and issue Winbond-class opcodes so flash is an ASIC endpoint, not only MCU pass-through.

Order inside this bucket:

1. **Flash read** (lower risk): opcode + dummy + data; still CE#/timing discipline.
2. **Flash write (maybe):** WEL, page program, erase, BUSY poll - NOR semantics; large FSM/DFF cost.

### Why deferred (D11)

MCU pass-through already covers flash. Flash write is a product-sized effort and fights the 2-tile schedule. V1 keeps flash CS **OE-off** from the ASIC at all times during DMA.

### Implement later

1. Allow flash as a device-select value (extend beyond A/B pointer MSB encoding, or add flag bits); never assert two CE#s.
2. QSPI engine: flash opcode set, dummy cycles, (for write) status poll FSM.
3. TCD or host policy: erase/program length rules; refuse illegal lengths.
4. Verification: models for BUSY, page boundaries, erase time (sim shortcuts).
5. Keep pass-through path for MCU-only flash experiments.

### DFF / tile impact

Read: medium (opcodes + timing). Write: medium–high (program/erase/BUSY). Last on the add-later ladder on purpose.

---

## Interaction with V1 baseline

V1 TCD is an **11-byte** memmove record with device in **`ptr[23]`** and `CTRL_FLAGS` holding **`QUIT`** plus reserved bits `[7:1]`. Post-V1 features that need more flags/`IMM` **extend** that byte (and optionally grow the TCD); plan a single compatible extension rather than a parallel layout.

Device select stays in pointer MSBs (D19); do not reintroduce per-field device flags unless flash or a third target forces it.

Host **abort** exists in V1 (behavior frozen D14); post-V1 `COND_STOP` depends on it for safe until-loops.
