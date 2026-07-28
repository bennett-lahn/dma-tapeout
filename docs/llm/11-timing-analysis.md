# Timing Analysis

Post-RTL checks before shuttle freeze (Phase 3). Protocol limits and opcode policy stay in `05-qspi-psram.md`; this file is the **what / where / pass** checklist.

**When:** RTL feature-complete (Phase 2 done), before freezing for tapeout.  
**Goal:** confirm APS6404L AC timing, SkyWater 130 GPIO limits, and on-chip paths at the frozen **66 MHz `clk` max / SCK=clk/2 (≈33 MHz) / rising-edge RX** policy (D16). The primary clock ceilings are **66 MHz input I/O** and **33 MHz output I/O**. Fallback only if a check fails: lower `clk` and/or falling-edge RX.

## Where to test

| Venue | Use for |
|---|---|
| **Cocotb / sim** | CE# sequencing vs SCK; burst gaps; no mid-command tear; byte counts |
| **STA / synth reports** (when flow exists) | Pad / net delays vs `tSP`/`tHD`/`tACLK`; SCK duty |
| **Demoboard (Phase 3)** | Real `tACLK` + board flight + TT I/O; long copy correctness at 66 MHz `clk` / 33 MHz SCK |

Engine **SCK = clk/2** (registered toggle when enabled; idle low when disabled). Timing is upheld by **enable/disable of that toggle and ordering CE#**, not by muxing `clk` onto the pad or an async SPI clock.

## How to extend

1. Add a subsection under **Checks** for the new domain (e.g. host pins, CDC, DFT).
2. Append rows to that subsection’s table: `ID | Constraint | Where | Pass | Status`.
3. Keep `Status` as `todo` / `wip` / `pass` / `fail` (note fallback if fail).
4. Do not duplicate datasheet tables here - link `05-qspi-psram.md` / `docs/datasheets/`.

---

## Checks

### PSRAM / QSPI (APS6404L) - architecture / sim

Encode in the QSPI engine; prove in Cocotb waveforms. Same-domain SCK is fine.

| ID | Constraint | Where | Pass | Status |
|---|---|---|---|---|
| Q-CEM | `tCEM` max CE# low (4 µs ext / 8 µs std) | sim (all txn types) | Every CE# low pulse under limit (V1 `N=1` / 11 B fetch expected) | todo |
| Q-CPH | `tCPH` ≥ 18 ns CE# high between bursts | sim | ≥2 `clk` CE#-high (`CS_OFF`+`IDLE`) before next assert @ 66 MHz | todo |
| Q-CSP | `tCSP` ≥ 2.5 ns: CE# low **before** first rising SCK | sim | CE# asserted with SCK idle low; first rise only after that | todo |
| Q-CHD | `tCHD` ≥ 3.0 ns: CE# stays low **after** last rising SCK | sim | CE# rises only after last beat’s rise (+ hold); SCK idle | todo |
| Q-TERM | Read terminate latch window (`tCHD > tACLK+tCLK` advice) | sim | After last read rise: SCK held low, sample committed, **then** CE# high - no extra SCK (no extra byte) | todo |
| Q-MUX | Only one RAM CE# low; flash CS OE off | sim | A/B mux + flash OE rules on every beat | todo |
| Q-RST | `rst_n` mid-run returns to idle | sim | CE# high / OE clear after reset; no soft abort | todo |

**Sequencing note:** extra hold for Q-TERM is **CE# low + SCK frozen**, not another clocked data beat.

### PSRAM / QSPI - post-RTL timing / board

Nanosecond closure; not separate FSM timers.

| ID | Constraint | Where | Pass | Status |
|---|---|---|---|---|
| T-ACLK | `tACLK` 2–5.5 ns vs rising-edge RX @ ≈33 MHz SCK | STA + demoboard | Stable reads (TCD + payload); margin OK with pad/board | todo |
| T-SP-HD | `tSP`/`tHD` ≥ 2 ns (host drive vs SCK) | STA / board | Cmd/addr/write data meet setup/hold at device | todo |
| T-CLKQ | `tCH`/`tCL` ~0.45–0.55 `tCLK`; `tKHKL` ≤ 1.5 ns | STA / scope if needed | Clock quality within table | todo |
| T-HZ | `tHZ` ≤ 5.5 ns to High-Z after CE# high | sim OE + board | Safe turnaround before other device / pass-through | todo |
| T-66 | Linear Burst / page-cross freq cap | policy + demoboard | Stay at **66 MHz `clk` / SCK=clk/2** for V1 path | todo |
| T-GPIO-IN | SkyWater 130 input I/O max **66 MHz** | policy + STA | System `clk` does not exceed 66 MHz | todo |
| T-GPIO-OUT | SkyWater 130 output I/O max **33 MHz** | policy + STA / board | Registered SCK and other high-rate pad outputs do not exceed 33 MHz | todo |

### Bring-up (MCU; not ASIC datapath)

| ID | Constraint | Where | Pass | Status |
|---|---|---|---|---|
| B-PU | `tPU` ≥ 150 µs, CE# high | firmware | Both devices after power-up | todo |
| B-RST | `tRST` ≥ 50 ns after `0x99` | firmware | Delay before next cmd | todo |

### Future (placeholders)

Add rows when RTL exposes the path:

| ID | Constraint | Where | Pass | Status |
|---|---|---|---|---|
| F-HOST | START/DONE/`BUS_REQ` pin timing vs clk | sim / STA | TBD | todo |
| F-INT | Critical on-chip paths (FSM ↔ QSPI ↔ regs) | STA | TBD | todo |

---

## Related

- Protocol / AC table context: `05-qspi-psram.md`
- Human summary: `../human/architecture/timing.md`
- QSPI block: `../human/architecture/blocks/qspi-engine.md`
- Roadmap Phase 3: `../human/roadmap.md`
- Datasheet: `../datasheets/pdfs/APS6404L_3SQR.pdf`
