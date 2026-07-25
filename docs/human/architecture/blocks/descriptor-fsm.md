# Descriptor FSM

Status: skeleton. State names and whether UPDATE folds into WRITE are open. Idle / DONE / abort / quit-TCD / zero-length rules follow D14 / D18 / D19.

## Role

Orchestrate descriptor fetch and byte moves. Issues transaction requests to the QSPI engine; does not own bit-level SPI timing.

## `uio_oe` arbitration

The descriptor FSM **arbitrates** which block drives ASIC `uio_oe`:

| Owner | When | `uio_oe` behavior |
|---|---|---|
| **Descriptor FSM** (default) | Idle, between QSPI transactions, and any time the engine is not live | Force pass-through / release: typically `uio_oe = 0` (DONE / idle); may hold a static master mask only if explicitly required between engine grants |
| **QSPI engine** | While a granted transaction is in flight | Phase-accurate per-pin mask (SCK + selected RAM CS; flash CS OE off; SIO drive on cmd/addr/write, float on dummy/read) |

FSM grants OE to the engine only for the duration of a requested QPI transaction, then reclaims it. Never leave both sources driving conflicting OE without a single mux select. Bus handoff phases: [`host-interface.md`](host-interface.md).

## Planned states (V1)

1. `IDLE` - DONE high; pass-through enabled (`uio_oe=0`); wait for **START** (`ui_in[0]`). START ignored in every other state.
2. `STATE_FETCH` - QPI read **11 bytes** into working regs. First fetch: `0x000000` / PSRAM 0; later: `NEXT_TCD` (die from bit 23). If `QUIT=1` → **IDLE.**
3. `STATE_READ` - read up to buffer depth `N` source bytes from `SRC_PTR` into the data buffer (V1: `N=1`; skipped if `TRANSFER_LEN == 0`)
4. `STATE_WRITE` - write buffered bytes to `DEST_PTR` (same `N`)
5. `STATE_UPDATE` - decrement `TRANSFER_LEN`; increment SRC/DEST address bits (keep die MSB); if length remains, loop to READ; if length hits 0, go FETCH for next TCD

No `STATE_PROCESS` / ALU in V1. Post-V1 may insert process / cond-stop after READ: [`../post-v1.md`](../post-v1.md).

## QSPI engine requests (D21)

FSM issues **transaction requests** (not raw TCDs): `{cmd, addr, die_sel, byte_len}` (`qspi_pkg` types in `qspi.svh`). Engine does **not** latch the request. `byte_len` is `logic [QSPI_BYTE_LEN_W-1:0]` with `QSPI_BYTE_LEN_W = $clog2(QSPI_MAX_BYTES + 1)` and `QSPI_MAX_BYTES = max(DMA_BUF_DEPTH, QSPI_TCD_BYTES)`.

| FSM use | Engine txn |
|---|---|
| `STATE_FETCH` | `QSPI_CMD_FAST_READ`, len=`QSPI_TCD_BYTES` (11), addr/`die_sel` from head or `NEXT_TCD` |
| `STATE_READ` | `QSPI_CMD_FAST_READ`, len=`k`, from `SRC_PTR` |
| `STATE_WRITE` | `QSPI_CMD_WRITE`, len=`k`, to `DEST_PTR`; first write nibble on `wdata` in the same cycle as `txn_valid` |

Handshake summary: 1-cycle `txn_valid` only when `~busy` (no `txn_ready`); FSM holds `{cmd, addr, die_sel, byte_len}` stable for the whole txn; write first nibble on `wdata` with `txn_valid`; sink `rdata_valid` pulses (rising-SCK captures); on `wdata_next` (falling-SCK pulse) present next write nibble; engine ends write after `2 * byte_len` SCK beats (no `wdone`); wait for `busy` low before reclaiming OE / starting next. SCK = clk/2. Engine never stalls QPI for the FSM. Full contract: [`qspi-engine.md`](qspi-engine.md) (Descriptor FSM interface).

## Notes

- Zero-length TCD: after FETCH (and quit check), skip READ/WRITE and immediately follow `NEXT_TCD`
- Data moves stay QPI byte-oriented in V1 for simplicity (D15)
- Buffer depth `N=1` for V1; do not hard-code depth into correctness (D20)
- **ABORT** (`ui_in[1]`): finish current QPI transaction (`busy`→0), then IDLE / DONE / pass-through
- After abort / quit / return to IDLE, FSM must reclaim `uio_oe` and clear it for MCU pass-through

## CE# refresh and Linear Burst page boundaries

APS6404L-class parts require CE# high within **`tCEM`** so DRAM refresh can run, and Linear Burst may cross a **1K page at most once** per CE# pulse. V1 does **not** need a CE# timer or page-boundary slicer: held payloads are tiny because the on-chip buffer is **`N=1`** (TCD fetch is a fixed **11-byte** hold).

### Why V1 is safe (66 MHz clk / 33 MHz SCK)

Each data txn is: CE# low → cmd + 24-bit addr (+ 6 dummy on `0xEB` read) → **`N` data bytes** → CE# high. Long `TRANSFER_LEN` is many such pulses (read `N`, CE# high, write `N`, …), not one long hold. Cross-device already raises CE# between dies. Beat counts below are **SCK** cycles at **≈33 MHz** (SCK = clk/2).

| Held CE# phase | Payload | SCK beats @ 33 MHz (approx) | CE# low time |
|---|---|---|---|
| `STATE_READ` (`0xEB`) | **1 B** (V1) | 14 + 2 = **16** | **~0.48 us** |
| `STATE_WRITE` (`0x02`) | **1 B** (V1) | 8 + 2 = **10** | **~0.30 us** |
| `STATE_FETCH` | **11 B** TCD | 14 + 22 = **36** | **~1.09 us** |

`tCEM` is **4 us** (extended) / **8 us** (standard) → **≈132** / **≈264** SCK at 33 MHz. V1 pulses are still well under the tighter limit.

### Lowest `N` that can break device rules

Assume one held CE# pulse carries the full buffer (`k = N`), **33 MHz SCK**, overhead as above (`0xEB` read: 14 SCK; `0x02` write: 8 SCK; 2 SCK/byte).

| Limit | First failing depth | Notes |
|---|---|---|
| **`tCEM` 4 us (extended)** | **`N ≥ 60` on read**; **`N ≥ 63` on write** | Max safe: read **59 B**, write **62 B**. Binding limit if planning for extended-grade parts. |
| **`tCEM` 8 us (standard)** | **`N ≥ 126` on read**; **`N ≥ 129` on write** | Max safe: read **125 B**, write **128 B**. |
| **Linear Burst 1K page** | **`N ≥ 1026`** | `≤1025` bytes always ≤1 page cross. At **1026**, a start near the end of a page (e.g. `addr & 0x3FF == 1023`) can cross **two** page boundaries in one CE# pulse. |

**Bottom line:** the first physical issue as `N` grows is **`tCEM`**, not pages. Lowest depth that can corrupt via refresh block on extended-grade parts: **`N = 60`** (full-depth `0xEB` hold with no margin). Page-cross only becomes reachable at **`N = 1026`**, which already exceeds both `tCEM` budgets.

Until `N` approaches those numbers, treat refresh/page slicing as non-goals for this FSM (D20). Prefer leaving margin below 59 if `N` is ever enlarged (e.g. target ≤48).

## Related

- TCD: [`tcd.md`](tcd.md)
- QSPI: [`qspi-engine.md`](qspi-engine.md)
- Agent detail: [`../../../llm/03-architecture.md`](../../../llm/03-architecture.md), [`../../../llm/04-tcd-and-datapath.md`](../../../llm/04-tcd-and-datapath.md)

