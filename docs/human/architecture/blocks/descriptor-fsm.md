# Descriptor FSM

Status: implemented in `sys_controller.sv` (V1 feature path covered by M0–M3). Host/mode control and descriptor sequencing are in the same module; this document describes the descriptor-control behavior, not a separate RTL block. Idle / DONE / quit-TCD / zero-length rules follow D14 / D18 / D19 / D23. Bus yield via `BUS_REQ`/`BUS_GNT` follows D22. Kill mid-run with `rst_n` only (D23/D34).

## Role

Within `sys_controller`, orchestrate descriptor fetch and byte moves. Issue transaction requests to the separate QSPI engine; do not own bit-level QPI timing.

## `uio_oe` arbitration (D26)

The descriptor FSM **arbitrates** ASIC `uio_oe`. The ASIC is the **bus keeper** whenever `~BUS_GNT` (including IDLE and between transactions). It releases the bus only for grant / reset.

| Owner | When | `uio_oe` behavior |
|---|---|---|
| **Controller (park)** | `~BUS_GNT`, no live QPI txn | Drive **flash CS + RAM A CS + RAM B CS high**, **SCK low**, **SIO don't-care driven** - never left floating |
| **QSPI engine (live txn)** | Transaction in flight and `~BUS_GNT` | Phase-accurate mask: SCK + both RAM CS (one low / one high) + flash CS high; SIO drives cmd/addr/write; **float on read dummy/data and one-`clk` post-CE# on reads**; writes keep SIO driven through CE# rise |
| **Released** | `BUS_GNT` or asserted active-low reset (`rst_n=0`) | Force all shared `uio_oe = 0` combinationally with `rst_n` (D26); MCU may master only in the `BUS_GNT` case |

Do **not** float CS/SCK between DMA transactions. Do **not** float SIO on writes post-CE#. On reads, ASIC reclaims SIO OE one `clk` after CE# rise (~15.2 ns @ 66 MHz vs `tHZ` max 5.5 ns). **MCU priority (D22):** while `BUS_REQ` is high, do **not** pulse `txn_valid`; STALL is entered only when the engine is idle (`SYS_CTRL_IDLE`, `NEW_FETCH`, `NEW_OP`, `UPDATE`) - atomicity by construction, not software-only. Ownership matrix: [`host-interface.md`](host-interface.md); agent detail: [`../../../llm/03-architecture.md`](../../../llm/03-architecture.md). Board **10 kΩ** CS pull-ups cover reset / pre-enable only ([`firmware.md`](../firmware.md)).

## V1 states

1. `IDLE` - DONE high; ASIC parks bus (`~BUS_GNT`: CS high / SCK low); accept the top-level-qualified one-`clk` **START** pulse with **`~BUS_REQ`**. A START pulse in every other state is ignored and not queued.
2. `STATE_FETCH` - QPI read **11 bytes** into working regs (nibble shift register; all 22 wire nibbles latched, including `CTRL_FLAGS[3:0]` reserved). First fetch (every START): `0x000000` / PSRAM 0; later: `NEXT_TCD` on `NEXT_DEVICE`. If `QUIT=1` → **IDLE** (next START starts at fixed head again).
3. `STATE_READ` - read up to buffer depth `N` source bytes from `SRC_PTR` into the data buffer (tapeout: **`N=5`**; skipped if `TRANSFER_LEN == 0`)
4. `STATE_WRITE` - write buffered bytes to `DEST_PTR` (same `N`)
5. `STATE_UPDATE` - decrement `TRANSFER_LEN`; pointers already advanced by `N` on READ/WRITE exit (don't-care on the final chunk). If length remains, loop to READ. If length hits 0, go FETCH for the next TCD on `NEXT_DEVICE`

In `NEW_FETCH`, controller latches `active_fetch_addr` / `active_fetch_device` from `next_tcd` / `next_tcd_device` so the in-flight FETCH shift does not clobber the fetch pointer mid-burst. On quit (`NEW_OP` with `QUIT`), hardware zeros `next_tcd` / `next_tcd_device` to PSRAM0 for the next START.

No `STATE_PROCESS` / ALU.

## QSPI engine requests (D21)

FSM issues **transaction requests** (not raw TCDs): `{cmd, addr, device_sel, byte_len}` (`qspi_pkg` types in `types.svh`). Engine does **not** latch the request. **`byte_len >= 1`** on every `txn_valid` (controller skips data txns when `transfer_len==0`). `byte_len` is `qpi_byte_len_t`, with `QPI_BYTE_LEN_W = $clog2(QPI_MAX_BYTES + 1)` and `QPI_MAX_BYTES = max(DMA_BUF_DEPTH_MAX, QPI_TCD_BYTES)`. Buffer depth `N` is module parameter `DMA_BUF_DEPTH` (**default 5**; tapeout N=5).

| FSM use | Engine txn |
|---|---|
| `STATE_FETCH` | `QSPI_CMD_FAST_READ`, len=`QPI_TCD_BYTES` (11), addr from head or `NEXT_TCD`, `device_sel` from PSRAM0 (first) or `NEXT_DEVICE` |
| `STATE_READ` | `QSPI_CMD_FAST_READ`, len=`k`, from `SRC_PTR`, `device_sel`=`SRC_DEVICE` |
| `STATE_WRITE` | `QSPI_CMD_WRITE`, len=`k`, to `DEST_PTR`, `device_sel`=`DEST_DEVICE`; first write nibble on `wdata` in the same cycle as `txn_valid` |

Handshake summary: 1-cycle `txn_valid` only when `~busy` (no `txn_ready`); FSM holds `{cmd, addr, device_sel, byte_len}` stable for the whole txn; write first nibble on `wdata` with `txn_valid`; exactly `2 * byte_len - 1` `wdata_next` pulses with same-cycle next-nibble mux (buffer shift is NBA); sink `rdata_valid` pulses (rising-SCK captures); engine ends write after `2 * byte_len` SCK beats (no `wdone`); wait for `busy` low before starting next / parking between txns. SCK = clk/2. Engine never stalls QPI for the FSM. Full contract: [`qspi-engine.md`](qspi-engine.md) (Descriptor FSM interface).

## Notes

- Zero-length TCD: after FETCH (and quit check), skip READ/WRITE and immediately follow `NEXT_TCD`
- Data moves stay QPI byte-oriented in V1 for simplicity (D15)
- Buffer depth **`N=5`** for V1 tapeout (default `DMA_BUF_DEPTH=5`); correctness must not depend on a specific `N` in `1..8` (D20)
- **Kill mid-run:** assert **`rst_n`** (D23/D34)
- **QUIT:** after FETCH, if `QUIT=1` → IDLE / DONE; next START always refetches `0x000000` / PSRAM 0
- **BUS_REQ** (`ui_in[2]`): finish current QPI transaction if any, assert `BUS_GNT`, pause (no new `txn_valid`); when REQ drops, deassert `BUS_GNT` and resume if not IDLE (D22)
- After quit / return to IDLE: resume parking (`rst_n && ~BUS_GNT`). On yield: release OE then assert `BUS_GNT`. While active-low reset is asserted (`rst_n=0`), force every shared `uio_oe` low; board CS pull-ups hold CE# high until reset is deasserted and the design parks again

## CE# refresh and Linear Burst page boundaries

APS6404L-class parts require CE# high within **`tCEM`** (max CE# low) so DRAM refresh can run, and Linear Burst may cross a **1K page at most once** per CE# pulse. V1 does **not** need a CE# timer or page-boundary slicer: held payloads are tiny because tapeout on-chip buffer is **`N=5`** (TCD fetch is a fixed **11-byte** hold). Same non-binding conclusion holds at **`N=1`**.

### Why V1 is safe (66 MHz clk / 33 MHz SCK)

Each data txn is: CE# low → cmd + 24-bit addr (+ 6 dummy on `0xEB` read) → **`N` data bytes** → CE# high. Long `TRANSFER_LEN` is many such pulses (read `N`, CE# high, write `N`, …), not one long hold. Cross-device already raises CE# between devices. Beat counts below are **SCK** cycles at **≈33 MHz** (SCK = clk/2).

| Held CE# phase | Payload | SCK beats @ 33 MHz (approx) | CE# low time |
|---|---|---|---|
| `STATE_READ` (`0xEB`) | **5 B** (tapeout) | 14 + 10 = **24** | **~0.73 us** |
| `STATE_WRITE` (`0x02`) | **5 B** (tapeout) | 8 + 10 = **18** | **~0.55 us** |
| `STATE_FETCH` | **11 B** TCD | 14 + 22 = **36** | **~1.09 us** |

(At **`N=1`**, read is 16 SCK / ~0.48 us, write is 10 SCK / ~0.30 us - still far under `tCEM`.)

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

