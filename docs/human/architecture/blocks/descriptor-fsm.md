# Descriptor FSM

Status: rough RTL in `sys_controller.sv`. Host/mode control and descriptor sequencing are implemented in the same module; this document describes the descriptor-control behavior, not a separate RTL block. Idle / DONE / quit-TCD / zero-length rules follow D14 / D18 / D19 / D23. Bus yield via `BUS_REQ`/`BUS_GNT` follows D22. No soft abort (kill with `rst_n`, D23).

## Role

Within `sys_controller`, orchestrate descriptor fetch and byte moves. Issue transaction requests to the separate QSPI engine; do not own bit-level QPI timing.

## `uio_oe` arbitration

The descriptor FSM **arbitrates** which block drives ASIC `uio_oe`:

| Owner | When | `uio_oe` behavior |
|---|---|---|
| **Descriptor FSM** (default) | Idle, between QSPI transactions, yielding for `BUS_GNT`, and any time the engine is not live | Force release: `uio_oe = 0` |
| **QSPI engine** | While a granted transaction is in flight and `~BUS_GNT` | Phase-accurate per-pin mask (SCK + selected RAM CS; flash CS OE off; SIO drive on cmd/addr/write, float on dummy/read) |

FSM grants OE to the engine only for the duration of a requested QPI transaction, then reclaims it. **MCU priority (D22):** while `BUS_REQ` is high, do **not** pulse `txn_valid`; if `busy`, wait for the current QPI txn to finish (atomic), then keep OE off and assert `BUS_GNT`. Never leave both sources driving conflicting OE without a single mux select. Bus handoff phases: [`host-interface.md`](host-interface.md).

## Planned states (V1)

1. `IDLE` - DONE high; `uio_oe=0`; accept the top-level-qualified one-`clk` **START** pulse with **`~BUS_REQ`**. A START pulse in every other state is ignored and not queued.
2. `STATE_FETCH` - QPI read **11 bytes** into working regs. First fetch (every START): `0x000000` / PSRAM 0; later: `NEXT_TCD` on `NEXT_DEVICE`. If `QUIT=1` → **IDLE** (next START starts at fixed head again).
3. `STATE_READ` - read up to buffer depth `N` source bytes from `SRC_PTR` into the data buffer (V1: `N=1`; skipped if `TRANSFER_LEN == 0`)
4. `STATE_WRITE` - write buffered bytes to `DEST_PTR` (same `N`)
5. `STATE_UPDATE` - decrement `TRANSFER_LEN`; increment SRC/DEST address bits (device flags sticky); if length remains, loop to READ; if length hits 0, go FETCH for next TCD on `NEXT_DEVICE`

No `STATE_PROCESS` / ALU in V1. Post-V1 may insert process / cond-stop after READ: [`../post-v1.md`](../post-v1.md).

## QSPI engine requests (D21)

FSM issues **transaction requests** (not raw TCDs): `{cmd, addr, device_sel, byte_len}` (`qspi_pkg` types in `types.svh`). Engine does **not** latch the request. `byte_len` is `qpi_byte_len_t`, with `QPI_BYTE_LEN_W = $clog2(QPI_MAX_BYTES + 1)` and `QPI_MAX_BYTES = max(DMA_BUF_DEPTH, QPI_TCD_BYTES)`.

| FSM use | Engine txn |
|---|---|
| `STATE_FETCH` | `QSPI_CMD_FAST_READ`, len=`QPI_TCD_BYTES` (11), addr from head or `NEXT_TCD`, `device_sel` from PSRAM0 (first) or `NEXT_DEVICE` |
| `STATE_READ` | `QSPI_CMD_FAST_READ`, len=`k`, from `SRC_PTR`, `device_sel`=`SRC_DEVICE` |
| `STATE_WRITE` | `QSPI_CMD_WRITE`, len=`k`, to `DEST_PTR`, `device_sel`=`DEST_DEVICE`; first write nibble on `wdata` in the same cycle as `txn_valid` |

Handshake summary: 1-cycle `txn_valid` only when `~busy` (no `txn_ready`); FSM holds `{cmd, addr, device_sel, byte_len}` stable for the whole txn; write first nibble on `wdata` with `txn_valid`; sink `rdata_valid` pulses (rising-SCK captures); on `wdata_next` (falling-SCK pulse) present next write nibble; engine ends write after `2 * byte_len` SCK beats (no `wdone`); wait for `busy` low before reclaiming OE / starting next. SCK = clk/2. Engine never stalls QPI for the FSM. Full contract: [`qspi-engine.md`](qspi-engine.md) (Descriptor FSM interface).

## Notes

- Zero-length TCD: after FETCH (and quit check), skip READ/WRITE and immediately follow `NEXT_TCD`
- Data moves stay QPI byte-oriented in V1 for simplicity (D15)
- Buffer depth `N=1` for V1; do not hard-code depth into correctness (D20)
- **No ABORT** (D23): kill mid-run with **`rst_n`**
- **QUIT:** after FETCH, if `QUIT=1` → IDLE / DONE; next START always refetches `0x000000` / PSRAM 0
- **BUS_REQ** (`ui_in[2]`): finish current QPI transaction if any, assert `BUS_GNT`, pause (no new `txn_valid`); when REQ drops, deassert `BUS_GNT` and resume if not IDLE (D22)
- After quit / return to IDLE / yield / reset, FSM must reclaim `uio_oe` and clear it (`BUS_GNT` may then follow REQ)

## CE# refresh and Linear Burst page boundaries

APS6404L-class parts require CE# high within **`tCEM`** so DRAM refresh can run, and Linear Burst may cross a **1K page at most once** per CE# pulse. V1 does **not** need a CE# timer or page-boundary slicer: held payloads are tiny because the on-chip buffer is **`N=1`** (TCD fetch is a fixed **11-byte** hold).

### Why V1 is safe (66 MHz clk / 33 MHz SCK)

Each data txn is: CE# low → cmd + 24-bit addr (+ 6 dummy on `0xEB` read) → **`N` data bytes** → CE# high. Long `TRANSFER_LEN` is many such pulses (read `N`, CE# high, write `N`, …), not one long hold. Cross-device already raises CE# between devices. Beat counts below are **SCK** cycles at **≈33 MHz** (SCK = clk/2).

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

