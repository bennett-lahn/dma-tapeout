# TinyDMA read-only review, 2026-08-21

Read-only agent review of TinyDMA (`C:\hw_projects\dma-tapeout`) dated **2026-08-21**. The review did not change RTL, firmware, or tests. This folder is the durable catalog so a later pass can implement fixes without the original chat.

This catalog does **not** claim M5 closed. It does **not** claim M4 pass. TinyDMA-2C is not used as a source here.

## What this pass was

A frozen snapshot of findings from a 2026-08-21 read-only sweep across engine, controller, top wrapper, formal job stubs, cocotb / coverage / PSRAM model, and firmware / MCU / board path. Follow-up confirmation exists only for the engine nibble-width wrap (`rtl-engine-01-verdict`). Controller was dual-reviewed and both verdicts are kept.

Shipped V1 is a dual-QSPI PSRAM bulk mover. Tapeout `DMA_BUF_DEPTH` `N` (on-chip scratch bytes) is **5**. Formal M4 (`FP-*`, formal properties) is not a V1 freeze gate (**D33**). Unused host pins are tied 0 (**D34**). `ptr[23]` is don't-care (**D35**). Target clock is 66 MHz with rising-edge RX (**D16**). Engine/controller handshake is D21. ASIC is bus keeper while `~BUS_GNT` (**D26**).

## Models used

| Area | Review | Notes |
|---|---|---|
| QSPI engine + `types.svh` | 2026-08-21 engine pass, then follow-up `rtl-engine-01-verdict` | Wrap at `byte_len << 1` confirmed HIGH. Icarus/Verilator promote; IEEE 1800 does not. Yosys/gate of the shift was **not** run. |
| Controller + `types.svh` | Composer `8968ec70` and grok `107e52e5` | Last-nibble latch: Composer medium risk, grok SAFE with current engine busy. Nibble-width: controller counters are widened; wrap is engine-only. Both verdicts recorded. |
| Top wrapper `src/top.v` / `info.yaml` | 2026-08-21 | Reset/START/OE/grant path. Passing pin-map notes kept. |
| Formal stubs | 2026-08-21 | Engine properties empty (vacuous PASS). Integration wires real `qspi_engine`. Detail in [`testbench.md`](testbench.md); RTL-facing items also in [`rtl.md`](rtl.md). |
| Firmware / MCU / board | 2026-08-21 | Includes firmware-vs-sim copy drift. |
| Sim / TB / coverage | 2026-08-21 | Includes merge of `test/runs/m5_coverage_closure.json`. |

## Merge result (do not treat as M5 close)

File: `test/runs/m5_coverage_closure.json`.

| Field | Value |
|---|---|
| `closed` | **false** |
| Fragments | **18** (Icarus n1..n8 seed-1, Icarus n5 seeds 1/2/3/5/8, Verilator n5 seeds 1/2/3/5/8, plus one L2 Icarus n5 seed-1) |
| Depths | 1 through 8 |
| Exclusions | 13 (STALL crossing plus N=1/2 length-class collapse). Structural kind is valid. Citation still says "Wave 3 to pin section"; reviewer stamp `M5-close` on an **open** merge. |
| Windows | `windows_counted` = 384, `windows_rejected` = 0 |

`COV-*` means functional coverage point IDs. Required IDs with missing bins (some have partial hits):

- `COV-BUS-STATE`
- `COV-BUS-PHASE`
- `COV-BUS-RESUME`
- `COV-START-PHASE`
- `COV-START-RESULT`
- `COV-RESET-STATE`
- `COV-RESET-PHASE`

Existing project docs that still say M5 `closed=true` are stale relative to this file. Sign-off language is out of scope for this folder; do not "fix" those docs as part of filing the review.

## How to read the three catalogs

| File | Contains |
|---|---|
| [`rtl.md`](rtl.md) | Every RTL finding: engine, controller (both reviews + nibble-width), top wrapper, and formal-stub items that are about RTL or jobs attached to RTL. |
| [`testbench.md`](testbench.md) | Every sim / TB / coverage / formal-stub finding, including merge gaps. |
| [`firmware.md`](firmware.md) | Every firmware / MCU / board-path finding, including firmware-vs-sim copy drift. |

Each finding has: stable id, severity, category, location (`file:line`), **Problem**, **Scope**, **Acceptance**. Related duplicates are grouped once with an **Also filed as** line so no original id is dropped.

ID namespaces used in this folder (defined here; later files redefine on first use):

- `Q-*` - simulation-provable QSPI checks
- `CHK-*` - always-on monitors
- `TC-*` - directed cases
- `COV-*` - functional coverage points
- `FP-*` - formal properties
- Datasheet AC: `tACLK` (read data valid after falling SCK), `tCEM` (max CE# low), `tSP` / `tHD` (SIO setup / hold vs rising SCK), `tCSP` (CE# setup before first rising SCK), `tCHD` (CE# hold after last SCK)

## Severity scale

As used in the reviews:

| Severity | Meaning in this catalog |
|---|---|
| **blocker** | Board or silicon path is wrong as written; do not treat a green mock as coverage. |
| **high** | Likely silent miss, wrong default vs tapeout, or a check that cannot fire. |
| **medium** | Real gap, but either residual, STA-only, or limited to a subset of levels. |
| **low** | Edge, comment, unused type, or weak diagnostic. |
| **note** | Passing contract, doc drift, or residual that is currently safe. |
| **info** | Two handshake items were filed as `info` (same weight as note). Kept as filed. |

## What was not done

- No STA of the combinational `wdata` path at 66 MHz (D16).
- No board bring-up, no RP2040 PIO on hardware, no wall-clock `tCEM` measurement.
- No formal prove or cover that implements `FP-*`. Engine `.sby` jobs would PASS vacuously. `make formal` is `TODO` + exit 1. D33 still applies: M4 is not a V1 freeze gate.
- No Yosys, gate, or other IEEE-width elaboration of `byte_len << 1`. Sim M0-M5 and `CHK-HS-RDATA-COUNT` (completed Fast Read `0xEB` must emit `2*byte_len` `rdata_valid` pulses) would catch wrap **if** sim were IEEE-correct; Icarus and Verilator promote the shift, so they are not.

## Highest-priority items

| ID | Sev | Catalog | One line |
|---|---|---|---|
| `fw-board-01` | blocker | [firmware.md](firmware.md) | `rst_n_low` reads `tt._in_reset`; DemoBoard has no such attribute. |
| `fw-board-02` | blocker | [firmware.md](firmware.md) | CE# stays low for a Python `put` loop; `tCEM` violated on wall clock. |
| `rtl-engine-01` | high | [rtl.md](rtl.md) | IEEE `4'(11)<<1` is 6, not 22. FETCH is always 11 bytes. Tapeout payload N=5 does not wrap. Sim hides it. |
| `fw-chain-01` | high | [firmware.md](firmware.md) | `DEFAULT_DMA_BUF_DEPTH=1` labeled tapeout; overlap N=1 vs N=5 differ. |
| `cov-fun-01` | high | [testbench.md](testbench.md) | Merge `closed=false`; `make directed` is only `test_dma_directed`; docs still claim M5 closed. |
| `fw-demo-02` | high | [firmware.md](firmware.md) | Empty `expected_writes` makes dump a no-op and `ok=True`. |
| `fw-demo-05` | high | [firmware.md](firmware.md) | `debug.py` has no `Host` / `request_bus`. |
| `fw-board-03` | high | [firmware.md](firmware.md) | `qpi_read_cpha0` samples on SCK low, not rising (D16). |
| `fw-board-04` | high | [firmware.md](firmware.md) | `request_bus` always `OE_QPI=0xFF`; SIO may stay driven during read. |
| `cov-fun-03` | high | [testbench.md](testbench.md) | Random never `record_bus_resume` even when STALL is reached. |
| `tb-life-01` | high | [testbench.md](testbench.md) | `dispose_run` is opt-in; pytest never auto-disposes. |

Next: read the area file for the id, then implement against **Acceptance**. Do not treat a finding as closed because Icarus M0-M5 was green.
