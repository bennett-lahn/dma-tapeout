# Testbench findings (2026-08-21)

Every sim / TB / coverage / formal-stub finding from the 2026-08-21 read-only review. RTL-facing wrap and busy timing are in [`rtl.md`](rtl.md); this file keeps the TB, merge, and job-stub detail.

ID namespaces on first use: `Q-*` are simulation-provable QSPI checks; `CHK-*` are always-on monitors; `TC-*` are directed cases; `COV-*` are functional coverage points; `FP-*` are formal properties. Datasheet AC: `tACLK` (read data valid after falling SCK), `tCEM` (max CE# low), `tSP`/`tHD` (SIO setup/hold vs rising SCK), `tCSP` (CE# setup before first rising SCK), `tCHD` (CE# hold after last SCK). `DMA_BUF_DEPTH` `N` is on-chip scratch bytes. **D35** `ptr[23]` don't-care. **D16** 66 MHz / rising-edge RX. **D21** handshake. **D26** bus keeper. **D33** M4 not a V1 freeze gate. **D34** unused pins tied 0.

Merge snapshot: `test/runs/m5_coverage_closure.json` has `closed=false`, 18 fragments, depths 1-8, 13 exclusions, 384 windows counted, 0 rejected. **Do not claim M5 closed. Do not claim M4 pass.**

`TIMING_PROFILE` values: `ideal` (zero TB placeholders only; datasheet `PSRAM_*` AC stay live), `nominal` (documented APS6404L min/max AC), `sweep` (CI currently no-op).

---

## Handshake / arb

### tb-hs-01 (high) - L2 handshake all `blocked`, not `na`

- **Category:** L2 dispose
- **Location:** L2 handshake disposition vs controller/engine internals
- **Also filed as:** `cov-gl-09`

**Problem:** L2 cannot see RTL-hierarchy handshake monitors. Rows are `blocked`, not `na`. That is a fail-shaped classification for something the level cannot check.

**Scope:** L2 gate. Pin-axis checks should still run.

**Acceptance:** Hierarchy-only `CHK-HS-*` / `CHK-CTRL-*` are `na` at L2 with a reason. `blocked` is reserved for allowlisted checks that ran and could have fired.

### tb-hs-02 (medium) - `dispose_run` does not fail on `blocked`

- **Category:** dispose
- **Location:** dispose implementation in test Python (handshake/pin tables)

**Problem:** A full table of `blocked` still passes `dispose_run`.

**Scope:** L2 (`tb-hs-01`) and any mis-wired monitor.

**Acceptance:** Unexpected `blocked` fails unless the test lists those ids as expected-blocked.

### tb-hs-03 (medium) - wrap WOULD fire `CHK-HS-RDATA-COUNT` if sim were IEEE

- **Category:** engine wrap vs checkers
- **Location:** `CHK-HS-RDATA-COUNT` (completed `0xEB` must emit `2*byte_len` `rdata_valid`); L1 `CHK-CTRL-FETCH-HEAD` (fetch pin length 11 bytes / 22 nibbles)
- **Also filed as:** `rtl-engine-01`

**Problem:** IEEE wrap (11<<1 -> 6) would fail `CHK-HS-RDATA-COUNT` (not WDATA). L1 FETCH-HEAD pins length 3 if only 6 nibbles (3 bytes) appear. Icarus/Verilator promote, so M0-M5 stay green. Not a WDATA-count catch (writes of N=5 do not wrap).

**Scope:** Sim hides the RTL bug. Checker is capable; simulator width is not IEEE.

**Acceptance:** Same as `rtl-engine-01` (d): Yosys/IEEE-width sim fails FETCH-HEAD / RDATA-COUNT until the RTL compare is widened; after the fix, 22 `rdata_valid` and pin length 11.

### tb-hs-04 (medium) - hang (`busy` never falls) has no count compare

- **Category:** diagnostics
- **Location:** handshake count vs `busy` falling

**Problem:** If `busy` never falls, count compare does not run. Diagnostic only.

**Scope:** Deadlock-shaped DUT bugs look like "no row" rather than count fail.

**Acceptance:** Timeout with `busy` still high fails the count check (expected beats vs observed, observed may be short).

### tb-hs-05 (low) - `CHK-CTRL-DATA-CNT` always `na` (retired D31)

- **Category:** retired
- **Location:** dispose tables

**Problem:** Always `na`. Retired with D31.

**Acceptance:** Keep `na` with D31 citation, or drop from the required list so dispose stays short.

### tb-hs-07 (medium) - `CHK-RST-OE` only on `value_change`; bring-up reset window vacuous

- **Category:** reset OE
- **Location:** `CHK-RST-OE` (OE during reset)

**Problem:** Samples on `value_change`. A reset window with no OE change contributes nothing.

**Acceptance:** Sample OE on reset assert/deassert edges, not only value_change. Bring-up reset must produce a row.

### tb-hs-08 (medium) - unresolved addr/`device_sel` stored None; REQ-STABLE only later change

- **Category:** request stability
- **Location:** handshake monitor storage

**Problem:** Unresolved addr/`device_sel` stored as None. `CHK` request-stable only looks at a later change from a known value.

**Acceptance:** X/Z on addr/`device_sel` during an accepted request is a fail, not None.

### tb-hs-10 (low) - START with no following txn is not FETCH-HEAD fail

- **Category:** START
- **Location:** `CHK-CTRL-FETCH-HEAD`

**Problem:** START that never produces a fetch is not a FETCH-HEAD failure (no frame to measure).

**Acceptance:** Accepted START with no 11-byte fetch within a bound fails FETCH-HEAD or a START-accept check. Vacuous ignore-while-busy stays a different id.

### tb-hs-11 (low) - L0 `pin_monitor=False` OPCODE blocked by design

- **Category:** L0
- **Location:** L0 engine TB

**Problem:** OPCODE handshake is `blocked` because pin_monitor is off by design.

**Acceptance:** Document as expected-blocked at L0, or run a pin-light OPCODE check on engine pads.

### tb-hs-12 (low) - only declared negative is `CHK-CTRL-DATA-PAIR` pending-at-stop

- **Category:** negatives
- **Location:** handshake negatives

**Problem:** The only declared CTRL/HS/ARB/RST negative is `CHK-CTRL-DATA-PAIR` pending-at-stop (read/write pairing left pending).
- **Also filed as:** `cov-qspi-05` (private poke)

**Acceptance:** Either more negatives, or document that handshake checkers are proved only by this one poke plus happy paths.

### tb-hs-13 (info) - handshake `RESET-TRUNCATED` unused

- **Category:** classification
- **Location:** handshake path; `RESET-TRUNCATED` means a sample taken while in reset / truncated window and is not a fail

**Problem:** Handshake never uses that classification.

**Acceptance:** Use it for in-reset handshake samples, or drop the unused enum on that path.

### tb-hs-14 (info) - DATA-PAIR length==11 as fetch tag; N=11 block never runs at V1

- **Category:** fetch tag
- **Location:** DATA-PAIR / fetch classification

**Problem:** Length==11 tags a fetch. A payload of 11 bytes cannot happen at V1 (`N<=8`). The N=11 payload block never runs.

**Acceptance:** Keep 11 as fetch-only with a comment, or tag fetch by controller state/opcode+addr at head, not length.

---

## Reference oracle (sim `test/reference`)

### tb-ref-01 (high) - `DEFAULT_DMA_BUF_DEPTH=1` vs tapeout 5

- **Location:** `test/reference/chain.py:74`
- **Also filed as:** `fw-chain-01`, `fw-sim-drift-03`, `cov-dma-07`

**Problem:** Same wrong default as firmware. Tapeout N=5.

**Acceptance:** Default 5, or every test passes depth explicitly (`align_chain_depth`). Overlap tests at N=5.

### tb-ref-02 (high) - overlap vs memmove only when `transfer_len>N`

- **Category:** `TC-OVERLAP`
- **Location:** overlap directed; generator

**Problem:** Overlap vs memmove differs only when `transfer_len>N`. `TC-OVERLAP` with len=6 at `N>=6` is one chunk (not a sliding overwrite).

**Acceptance:** Overlap vector with `transfer_len>N` at tapeout N=5, plus a one-chunk overlap that documents "same as non-overlap read-then-write."

### tb-ref-03 (medium) - `device_tuple` and `next_device` share `STREAM_DEVICES`

- **Category:** generator streams
- **Location:** `test/reference/generator.py`
- **Also filed as:** `cov-refu-05`

**Problem:** Device pair and next_device share one stream. Isolation tests are weaker than they look.

**Acceptance:** Independent streams, or isolation tests that extra device entropy changes dest independently of NEXT.

### tb-ref-04 (medium) - axis 2 optional; sparse addresses; 2-byte dest guards

- **Category:** scoreboard
- **Location:** reference compare

**Problem:** Axis 2 optional. Sparse addresses. 2-byte dest guards limit what compare can see.

**Acceptance:** Required axes documented. Dest guards parametrized. Sparse vs dense fill tested.

### tb-ref-05 (medium) - generator excludes illegal / self-pointing / quit-nonzero-fields

- **Category:** stimulus
- **Location:** generator

**Problem:** Excludes illegal, self-pointing (D35 legal), and quit-with-nonzero-fields. Those shapes are unrandomized.

**Acceptance:** Self-pointing included (D35). Quit-nonzero either generated as legal terminator or covered by a directed case (`fw-chain-03`).

### tb-ref-06 (low) - memory report no chunk index; `AXIS_REFERENCE` unused

- **Location:** reference report helpers

**Problem:** Reports lack chunk index. `AXIS_REFERENCE` unused.

**Acceptance:** Chunk index in mismatch text. Use or delete `AXIS_REFERENCE`.

### tb-ref-07 (low) - depth only `>=1` allows N=9; budget 4096 tight at N=1 max chain

- **Location:** depth validate; `DEFAULT_TXN_BUDGET = 4096`

**Problem:** Depth `>=1` allows N=9 (`DMA_BUF_DEPTH_MAX` is 8 in RTL). Budget 4096 is tight for N=1 max-length chains.

**Acceptance:** Depth `1..8` (or `DMA_BUF_DEPTH_MAX`). Budget documented vs worst-case N=1 255-byte copies times chain length.

---

## Lifecycle / dispose

### tb-life-01 (high) - `dispose_run` opt-in; pytest never auto-dispose

- **Category:** lifecycle
- **Location:** cocotb test teardown vs `dispose_run`

**Problem:** Pytest never auto-disposes. Last test in a module never hits `_stop_previous` assert.

**Scope:** All cocotb modules. Monitors from test N can leak into N+1 unless someone calls dispose.

**Acceptance:** Autouse fixture disposes at test end, or session fail if a test returns without dispose. Last-in-module is covered.

### tb-life-02 (high) - L0 `pin_monitor` and `ce_monitor` default False; docs wrong

- **Category:** defaults
- **Location:** L0 bring-up; docs in `docs/llm/verification/02-platform.md` (claimed `ce_monitor` True)

**Problem:** Both default False on L0. Docs wrongly say both helpers default `ce_monitor` True.

**Acceptance:** Code and docs match. If L0 needs CE# framing, default True or tests pass True.

### tb-life-03 (high) - `BringUp.clear` wipes `agent.violations`; model carryover not collected

- **Category:** violations
- **Location:** BringUp clear vs PSRAM agent

**Problem:** Clear wipes `agent.violations`. Model carryover is not collected into the next dispose.

**Acceptance:** Snapshot violations before clear, or clear only after dispose copies them.

### tb-life-05 (medium) - `expect("ID")` means `>=1` not exact count; truncated never increment counts

- **Category:** expect API
- **Location:** expect helpers

**Problem:** `>=1` not exact. Truncated samples never increment counts.

**Acceptance:** Exact-count API or document `>=1`. Truncated events counted under `RESET-TRUNCATED`, not silent.

### tb-life-06 (medium) - `REVIEW` never fails; `REQUIRE` only `len>=1`; `clear()` drops truncated

- **Category:** audit
- **Location:** classification helpers

**Problem:** REVIEW cannot fail. REQUIRE is existence-only. `clear()` drops truncated.

**Acceptance:** REVIEW fails on unexpected ids, or is renamed informational. Truncated preserved until dispose.

### tb-life-07 (medium) - audit classification is in-reset **now**; handshake diagnostics not in DISPOSE table

- **Category:** audit vs dispose
- **Location:** audit vs handshake

**Problem:** Classification uses in-reset **now**, not at event time. Handshake diagnostics are not in the DISPOSE table.

**Acceptance:** Classify with the event's in-reset flag. Handshake diagnostics as dispose rows or an attached log.

### tb-life-08 (medium) - L0 OPCODE `blocked` even though allowlist ran

- **Also filed as:** `tb-hs-11`

**Problem:** Allowlist ran but OPCODE is still blocked.

**Acceptance:** If allowlist includes OPCODE at L0, it must not be blocked; else remove it from the L0 allowlist.

### tb-life-09 (medium) - unstarted monitors contribute no rows; `pin_disposition` never `dispose_run`

- **Category:** unstarted
- **Location:** pin_disposition vs dispose_run

**Problem:** Unstarted monitors add no rows. `pin_disposition` never goes through `dispose_run`.

**Acceptance:** Unstarted required monitors are `blocked`/`na`, not absent. Pin path uses dispose_run.

### tb-life-10 (low) - dispose does not cancel delayed timing tasks

- **Category:** cocotb tasks
- **Location:** delayed timing callbacks

**Problem:** Delayed tasks can fire after dispose.

**Acceptance:** Cancel/join timing tasks in dispose.

---

## PSRAM model

**tb-model-12 (note):** SCK/CE# edge-driven framing, six dummies for `0xEB`, and dual-device routing remain model contracts.

---

## Helpers (`test/common/host.py` and coverage helpers)

### tb-help-01 (high) - `pulse_start` `hold_cycles=2` vs capture-required 3 periods

- **Category:** START capture
- **Location:** `test/common/host.py:32-40`
- **Also filed as:** `cov-dma-03`

**Problem:** Holds 2 cycles. Capture-required is 3 periods through the two-flop + detect. Does not wait `BUS_GNT`/`DONE`.

**Scope:** May still work because extra edges exist around the helper. 1-cycle NEW_FETCH/UPDATE windows can miss (`cov-dma-03`).

**Acceptance:** Default hold meets capture-required (3+). Optional wait for DONE-low ACK. Directed 1-cycle window has a dedicated helper.

### tb-help-02 (medium) - `jitter_start` does not fail capture-required with 0 edges

- **Category:** jitter
- **Location:** jitter_start helper

**Problem:** Zero captured edges is not a fail of capture-required.

**Acceptance:** 0-edge result is `idle_uncaptured` (or fail if the test required capture).

### tb-help-03 (high) - `assert_bus_req` one `RisingEdge`; sync still 0 after return

- **Category:** BUS_REQ sync
- **Location:** host `assert_bus_req`

**Problem:** Waits one rising edge. Two-flop sync can still be 0 when the helper returns.

**Acceptance:** Wait until synchronized `bus_req` is 1 (or 2 extra edges). Test: helper return implies controller sees REQ.

### tb-help-04 (high) - `inject_bus_req` late on one-cycle NEW_FETCH/NEW_OP/UPDATE/CS_ON

- **Category:** injection
- **Location:** inject helper vs `inject_bus_req_at_new_fetch`
- **Also filed as:** `cov-gl-02`, `cov-fun-07`

**Problem:** Generic inject is late for one-cycle states. `inject_bus_req_at_new_fetch` exists because of this, and it stamps `observed_state` NEW_FETCH (`cov-fun-07`).

**Acceptance:** Generic inject is cycle-accurate or is not used for one-cycle states. Stamped state matches DUT, not the helper name.

### tb-help-05 (medium) - missed slot `break` then return still asserts

- **Category:** injection
- **Location:** inject miss path

**Problem:** Missed slot breaks then returns and still asserts success.

**Acceptance:** Missed slot fails the helper.

### tb-help-06 (medium) - complete cycle waits GNT not busy/OE; LANDING_MIDDLE/FINAL fixed +2/+6

- **Category:** landing
- **Location:** complete-cycle helper

**Problem:** Waits GNT, not `busy`/OE. Landing constants +2/+6 are fixed.

**Acceptance:** Wait `~busy` and OE park, or document GNT as proxy. Landing offsets derived from DUT or timed.

### tb-help-07 (medium) - L0 BFM vs controller combo `wdata` mux off-by-one invisible at LEVEL=engine

- **Category:** D21 / mux
- **Location:** L0 engine BFM vs `sys_controller` `qspi_wdata` mux

**Problem:** Controller combo mux off-by-one is invisible at `LEVEL=engine`.

**Scope:** L0. L1 uses the real controller.

**Acceptance:** L0 does not claim D21 controller-mux coverage. An L1 write nibble sequence check exists.

### tb-help-08 (medium) - BFM extra IDLE clk after busy; read no `ReadOnly`; timeout 512

- **Category:** L0 BFM
- **Location:** engine BFM

**Problem:** Extra IDLE clock after `busy` falls. Read samples without `ReadOnly`. Timeout 512 may be short for 11-byte FETCH.

**Acceptance:** Timeout sized for 11-byte FETCH+payload. Read uses `ReadOnly` or equivalent. Extra IDLE documented.

### tb-help-09 (medium) - `coverage.json` last-writer; random overwrites without `absorb_fragment`

- **Category:** merge
- **Location:** coverage write
- **Also filed as:** `cov-fun-04`

**Problem:** Last writer wins. Random overwrites without absorb.

**Acceptance:** Every writer `absorb_fragment` into a named run file. Merge is order-independent.

### tb-help-10 (low) - `record_chain` bins golden not pin; `COV-DEPTH` counts windows

- **Category:** coverage source
- **Location:** record_chain
- **Also filed as:** `cov-fun-05`, `cov-dma-01`

**Problem:** Bins from golden chain, not pin log. `COV-DEPTH` counts windows.

**Acceptance:** Document golden vs pin, or sample from pin. Depth bins from elaboration `N`, not window count alone.

### tb-help-11 (low) - duplicate START/BUS_REQ helpers; dead `accepted_start`

- **Location:** host.py

**Problem:** Duplicate helpers. `accepted_start` dead.

**Acceptance:** One helper each. Remove dead names.

---

## Coverage DMA directed TCs

### cov-dma-01 (medium) - extra asserts read golden not pin

- **Category:** `TC-SAME-*`, `TC-CROSS-*`, `TC-CHAIN`
- **Location:** dma directed extra asserts
- **Also filed as:** `tb-help-10`

**Problem:** Counts come from golden, not pin.

**Acceptance:** Pin-log counts, or extra asserts labeled golden-only.

### cov-dma-02 (medium) - `TC-ADDR-WIDE` three classes; no A[22:0] walk; no `ptr[23]`

- **Category:** `TC-ADDR-WIDE`
- **Location:** address directed
- **Also filed as:** `fw-tcd-02`

**Problem:** Three address classes only. No A[22:0] walk. No `ptr[23]` (D35).

**Acceptance:** Include `ptr[23]=1`. Optional A[22:0] corner walk remains a catalog choice, but bit 23 must appear.

### cov-dma-03 (high) - `TC-START-ACTIVE` vacuous on 1-cycle NEW_FETCH/UPDATE

- **Category:** `TC-START-ACTIVE`
- **Location:** start-while-active
- **Also filed as:** `tb-help-01`

**Problem:** Vacuous on 1-cycle NEW_FETCH/UPDATE (pulse too coarse).

**Acceptance:** Inject START in those states (cycle-accurate) and show ignore. Non-vacuous `COV-START-RESULT` `active_ignored`.

### cov-dma-04 (medium) - `COV-BUS`/`COV-START` recorded from intent labels; merge `closed=false`

- **Category:** merge
- **Location:** coverage stamps vs `test/runs/m5_coverage_closure.json`

**Problem:** Recorded from intent labels, not always observed DUT. Merge is open (see cov-fun section).

**Acceptance:** Stamps from DUT state. Merge `closed=true` only when required bins are hit or excluded with a live citation.

### cov-dma-05 (medium) - no `expect_fail` in `dma_directed` / `reset_and_bus` / smoke

- **Category:** negatives
- **Location:** those test modules

**Problem:** No `expect_fail`. Happy path only.

**Acceptance:** At least one negative per module, or document that negatives live only under `test_qspi_*`.

### cov-dma-06 (medium) - `TC-RESET-ACTIVE` omits CS_ON and SEND_CMD_2

- **Category:** `TC-RESET-ACTIVE`
- **Location:** reset-while-active
- **Also filed as:** `cov-gl-03` (no injection reset)

**Problem:** Omits CS_ON and SEND_CMD_2.

**Acceptance:** Reset those engine states, or exclude with a reason in the catalog.

### cov-dma-07 (low) - generator default N=1 if forget `align_chain_depth`

- **Also filed as:** `tb-ref-01`

**Acceptance:** Generator default 5 or refuse unset depth.

### cov-dma-08 (low) - `TC-BUS-IDLE` START only at STALL+GNT together

- **Category:** `TC-BUS-IDLE`
- **Location:** idle+grant start

**Problem:** START only when STALL and GNT together, not other idle-grant shapes.

**Acceptance:** START while GNT in IDLE (no DMA) is covered, distinct from STALL.

### cov-dma-09 (low) - `TC-TCD-BE` `dest_device=0` in vector

- **Also filed as:** `fw-tcd-03`, `fw-tcd-06`

**Acceptance:** Cross dest=1 in a TCD unit vector (sim or firmware). Frozen A0 vector may stay; add a second vector.

---

## COV sampler vs merge (critical)

File: `test/runs/m5_coverage_closure.json`. `closed=false`. 18 fragments. Depths 1-8. 13 exclusions (STALL crossing; N=1/2 length-class collapse). 384 windows counted, 0 rejected.

Missing required bins (partial hits exist for some ids):

| ID | Hits in merge | Missing bins |
|---|---|---|
| `COV-BUS-STATE` | FETCH | IDLE, NEW_FETCH, NEW_OP, READ, WRITE, UPDATE |
| `COV-BUS-PHASE` | address, command | CS_ON, wait, read_data, write_data, SCLK_OFF, CS_OFF |
| `COV-BUS-RESUME` | (none) | IDLE, NEW_FETCH, NEW_OP, UPDATE |
| `COV-START-PHASE` | early, late, near_edge_after | near_edge_before, on_edge |
| `COV-START-RESULT` | idle_accepted | idle_uncaptured, active_ignored, req_gnt_ignored, held_high_single |
| `COV-RESET-STATE` | SYS_CTRL_IDLE | NEW_FETCH, FETCH, NEW_OP, READ, WRITE, UPDATE, STALL |
| `COV-RESET-PHASE` | idle_pad | command, address, wait, read_data, write_data, termination |

The 13 exclusions themselves (STALL not a fresh REQ assertion state; N=1/2 length bins collapsing onto 0/1/N+1) are a **valid structural kind**. They do not close the missing BUS/START/RESET bins above.

### cov-fun-01 (high) - Make directed vs docs vs merge

- **Category:** M5 close claim
- **Location:** Makefile directed target; `docs/llm/verification/02-platform.md`; merge JSON; human/llm signoff that still say `closed=true`

**Problem:** `make directed` runs only `test_dma_directed`. Depth loops that. Regression runs only `test_dma_random`. `02-platform.md` still says directed includes `reset_and_bus`. Docs claim M5 `closed=true`. Merge is `closed=false`.

**Scope:** Process/docs/CI. Does not by itself prove RTL wrong. **Do not claim M5 closed.**

**Acceptance:** Merge `closed=true` only after missing bins are hit or excluded with a current citation. Makefile directed includes the documented modules, or the doc lists only `test_dma_directed`. Signoff text matches the JSON.

### cov-fun-02 (high) - random BUS_REQ at FETCH only; START capture_required IDLE; reset SYS_CTRL_IDLE after DONE

- **Category:** random stimulus
- **Location:** random BUS/START/RESET injectors

**Problem:** Random REQ is FETCH-only (matches the sole `COV-BUS-STATE` hit). START capture_required only in IDLE. Reset after DONE in SYS_CTRL_IDLE only.

**Acceptance:** Random (or directed) injects REQ in IDLE/NEW_FETCH/NEW_OP/READ/WRITE/UPDATE; START phases including on_edge; reset in non-idle controller and non-idle_pad QPI phases.

### cov-fun-03 (high) - random never `record_bus_resume` despite STALL reached

- **Category:** `COV-BUS-RESUME`
- **Location:** random coverage stamps
- **Also filed as:** `cov-gl-05`

**Problem:** STALL is reached (`COV-CTRL-STATE` STALL has hits) but resume is never recorded. All `COV-BUS-RESUME` bins missing.

**Acceptance:** On leaving STALL, `record_bus_resume` with the origin state. Merge hits IDLE/NEW_FETCH/NEW_OP/UPDATE (or excludes with a real unreachability).

### cov-fun-04 (high) - random `write_fragment` no absorb; order-dependent merge

- **Also filed as:** `tb-help-09`

**Acceptance:** Absorb into per-seed fragments. Merge permutation-stable.

### cov-fun-05 (medium) - `COV-LEN`..`COV-DEPTH` from golden chain not pin log

- **Also filed as:** `tb-help-10`

**Acceptance:** Document or sample from pin.

### cov-fun-06 (medium) - exclusions keyed `(id,bin)` not per-depth; N-1 exclusion could hide other depths later

- **Category:** exclusion key
- **Location:** merge exclusions (depth field exists on each row, but keying risk remains)

**Problem:** If a later tool keys only `(id,bin)`, an N=1 `N-1` exclusion could hide N-1 at other depths.

**Acceptance:** Key `(id, bin, depth)`. Test that N=5 `N-1` is still required.

### cov-fun-07 (medium) - `inject_bus_req_at_new_fetch` stamps `observed_state` NEW_FETCH

- **Also filed as:** `tb-help-04`, `cov-gl-02`

**Problem:** Stamp is the helper's intent, not necessarily DUT.

**Acceptance:** Stamp `curr_state` from DUT at the REQ-sync sample.

### cov-fun-08 (medium) - `COV-START-RESULT` mix of intent and capture

- **Category:** `COV-START-RESULT`

**Problem:** Mix of intended pulse class and captured result.

**Acceptance:** Separate intent vs result, or only record result. Missing result bins actually hit.

### cov-fun-09 (medium) - process-global pending; fail-then-pass can promote pending; some windows `scoreboard_ok=True` without compare

- **Category:** window scoring
- **Location:** coverage pending flags

**Problem:** Process-global pending. Fail-then-pass can promote. Some windows marked `scoreboard_ok=True` without a compare.

**Acceptance:** Per-test pending. `scoreboard_ok` only after compare. Fail sticks.

### cov-fun-10 (medium) - catalog `COV-BUS-RESUME` / `BUS-PHASE` described as crosses; impl 1-D enums

- **Category:** catalog vs impl
- **Location:** `docs/llm/verification/08-stimulus-and-coverage.md`

**Problem:** Written as crosses; implementation is 1-D enums.

**Acceptance:** Catalog matches impl (1-D), or impl becomes a real cross.

### cov-fun-11 (low) - `sample_l1_states` swallows `CoverageError`; first encoding only

- **Location:** L1 state sampler

**Problem:** Swallows `CoverageError`. First encoding only.

**Acceptance:** Do not swallow. Re-sample on state change.

### cov-fun-12 (low) - `EXCLUSION_CITATION` still "Wave 3 to pin section"; stamps `M5-close` on an open merge

- **Location:** `test/runs/m5_coverage_closure.json` exclusion `architecture_citation` / `reviewer`

**Problem:** Citation is a placeholder. Reviewer stamp `M5-close` on `closed=false`.

**Acceptance:** Real section/decision id. Reviewer stamp is not `M5-close` until `closed=true`.

---

## Timing tests

### cov-tim-04 (high) - CI `run_timing.sh` / `timing.yaml` nominal only; sweep IDs no-op

- **Category:** `TIMING_PROFILE=sweep`
- **Location:** CI timing scripts

**Problem:** CI runs `nominal` only. Sweep IDs are no-ops.

**Acceptance:** Sweep actually varies listed AC, or CI does not claim sweep. Nominal remains a real min/max run.

### cov-tim-05 (medium) - CSP/CHD/TERM MCU pass-through not ASIC

- **Category:** `tCSP`/`tCHD` / termination
- **Location:** timing directed

**Problem:** Those IDs are exercised on MCU pass-through frames, not ASIC engine frames.

**Acceptance:** ASIC-generated frames for CSP/CHD/TERM, or catalog says MCU-only.

### cov-tim-06 (medium) - CEM/CPH profile no-op; directed overrides

- **Category:** `tCEM` / `tCPH` (CE# high between txns)
- **Location:** profile vs directed

**Problem:** Profile knobs for CEM/CPH are no-ops; only directed overrides do anything.

**Acceptance:** Profile applies, or knobs removed from the profile table.

### cov-tim-07 (medium) - no `tACLK` past capture edge; clk 10 ns not 15.15 ns

- **Category:** D16 period
- **Location:** sim clk vs 66 MHz (15.15 ns)

**Problem:** No `tACLK` stress past the capture edge. Clock is 10 ns, not 15.15 ns.

**Scope:** Functional sim speed. Not STA.

**Acceptance:** At least one timed read with `tACLK` near the rising SCK. Optional 66 MHz period run; document 10 ns as a convenience.

### cov-tim-09 (low) - L0 QRST `ce_monitor=False`

- **Also filed as:** `tb-life-02`

**Acceptance:** L0 reset-timing tests enable `ce_monitor` or mark CE# IDs `na`.

---

## Reference unit tests

### cov-refu-01 (high) - no N=5 overlap unit vector (only N=1/2)

- **Also filed as:** `tb-ref-02`, `fw-chain-05`

**Acceptance:** Unit overlap at N=5 with `transfer_len>5`.

### cov-refu-02 (high) - no `dest_device=1` x `ptr[23]` cross

- **Also filed as:** `fw-tcd-02`, `fw-tcd-03`

**Acceptance:** One unit vector with dest=1 and `ptr[23]=1`.

### cov-refu-03 (medium) - scoreboard `dest_device=1` is `compare(oracle,oracle)`

- **Category:** tautology
- **Location:** scoreboard unit

**Problem:** dest=1 path compares oracle to itself.

**Acceptance:** Compare oracle to a fixture image, or to a second independent interpreter instance with a seeded mutation.

### cov-refu-04 (medium) - generator/injection mostly convenience locks

- **Location:** generator tests

**Problem:** Mostly lock convenience APIs, not isolation of entropy axes.

**Acceptance:** Isolation tests that extra entropy on one axis does not change another (`cov-refu-05/06`).

### cov-refu-05 (high) - stream isolation test only extra `transfer_length` vs `address_class`; devices stream shared

- **Also filed as:** `tb-ref-03`

**Acceptance:** Independent device stream; isolation test includes dest_device vs next_device.

### cov-refu-06 (medium) - injection isolation only extra `start.random()`; `range(1)` replay

- **Location:** injection tests

**Problem:** Weak isolation. Replay length 1.

**Acceptance:** Replay `range` >1. Isolation on more than `start.random()`.

### cov-refu-07 (medium) - firmware vs reference duplicate vector `dest_device=0`

- **Also filed as:** `fw-tcd-03`, `cov-dma-09`

**Acceptance:** Same as those.

### cov-refu-08 (low) - no `LAYOUT_OVERLAP_BACKWARD` generator test; no PSRAM1 overlap

- **Location:** generator / overlap units

**Problem:** No backward-overlap generator test. No PSRAM1 overlap.

**Acceptance:** One backward overlap; one dest-on-PSRAM1 overlap.

---

## QSPI protocol tests

### cov-qspi-01 (medium) - `TC-QPI` dummy from model; L0 `pin_monitor` off; `CHK-HS-OPCODE` wait blocked

- **Category:** dummy / opcode
- **Location:** QPI directed; `CHK-HS-OPCODE` (opcode vs wait cycles)

**Problem:** Dummy count comes from the model. L0 pin_monitor off. OPCODE wait blocked.

**Acceptance:** Pin-count dummy=6 on an ASIC `0xEB`. L0 either monitors or `na`. OPCODE wait not blocked if listed.

### cov-qspi-03 (high) - `TC-OWN-SIO-DUAL` while deselected; expects `Q-DRIVE-DESEL` not `Q-SIO-OWN`

- **Category:** ownership
- **Location:** dual-select ownership test; `Q-DRIVE-DESEL` (drive while deselected) vs `Q-SIO-OWN`

**Problem:** Stimulus is while deselected. Expects `Q-DRIVE-DESEL`, not `Q-SIO-OWN`.

**Acceptance:** Dual-select-while-selected expects `Q-SIO-OWN` / `Q-MUX`. Deselected drive keeps `Q-DRIVE-DESEL`. Names match stimulus.

### cov-qspi-05 (high) - `TC-CTRL-DATA-PAIR-PENDING-AT-STOP` pokes private `_check_data_pair`

- **Also filed as:** `tb-hs-12`
- **Location:** that TC

**Problem:** Pokes a private method. Not an end-to-end pending-at-stop.

**Acceptance:** Public stop/dispose path leaves pairing pending and fails the CHK without poking privates.

### cov-qspi-06 (medium) - `TC-LIVE-CE-FRAME-AT-STOP` diagnostic; no `expect_fail` `Q-PHASE`

- **Category:** live CE# at stop
- **Location:** that TC

**Problem:** Diagnostic only. No `expect_fail Q-PHASE`.

**Acceptance:** `expect_fail` on `Q-PHASE` (or the documented diagnostic id), or drop the TC.

### cov-qspi-08 (medium) - negatives are MCU/`fault_uio`, not ASIC-illegal selected frames; monitors detached

- **Category:** negative quality
- **Location:** QSPI negatives

**Problem:** Stimulus is MCU/fault_uio, not an ASIC-illegal **selected** frame. Monitors often detached.

**Acceptance:** At least one ASIC-master illegal selected frame with monitors attached.

### cov-qspi-09 (low) - ownership counts `>=1` vs exact

- **Also filed as:** `tb-life-05`

**Acceptance:** Exact counts for mutex/ownership.

---

## GL / injection (L2)

### cov-gl-01 (high) - `test_injection_dut` never records coverage; not in Makefile

- **Category:** injection tests
- **Location:** `test/tests` injection module vs Makefile

**Problem:** Never records coverage. Not in Makefile.

**Acceptance:** Add to directed/regression **or** stop treating injection as M5 evidence. Record coverage when run.

### cov-gl-02 (high) - `inject_bus_req_at_new_fetch` stamps NEW_FETCH

- **Also filed as:** `cov-fun-07`, `tb-help-04`

**Acceptance:** Same as those.

### cov-gl-03 (high) - no injection reset case; random IDLE only

- **Also filed as:** `cov-fun-02`, `cov-dma-06`

**Acceptance:** Injection reset in non-idle states.

### cov-gl-04 (medium) - one `CAPTURE_UNCERTAIN` fixture; no `record_start_*`

- **Category:** START coverage
- **Location:** injection/start fixtures

**Problem:** One uncertain-capture fixture. No `record_start_*` into merge.

**Acceptance:** Record START phase/result from injection. More than one uncertain case.

### cov-gl-05 (medium) - injection/random never `record_bus_resume`

- **Also filed as:** `cov-fun-03`

**Acceptance:** Same as `cov-fun-03`.

### cov-gl-06 (high) - L2 reuses L1 `TC-BUS-ACTIVE` / `TC-RESET-ACTIVE` IDs with weaker pin-only stimulus

- **Category:** ID reuse
- **Location:** L2 tests vs L1 ids

**Problem:** Same TC ids, weaker pin-only stimulus. L1 pass is not L2 pass.

**Acceptance:** L2-specific ids, or L2 rows tagged `level=L2` and not counted as L1 close.

### cov-gl-07 (high) - L2 reset no recovery START/head fetch; release on rising edge

- **Category:** L2 reset
- **Location:** L2 reset tests

**Problem:** No recovery START / head fetch after reset. Release on rising edge (not the RTL deassert contract).

**Acceptance:** After L2 reset, START fetches head 11 bytes. Reset release matches `rst_n` polarity.

### cov-gl-08 (high) - `_require_l2` no netlist SHA; old ~153-DFF nl vs N=5

- **Category:** netlist identity
- **Location:** L2 require helper; `docs/llm/verification/09-gate-level-and-x.md` notes sha256 of an earlier ~153-DFF harden

**Problem:** No netlist SHA gate. An old ~153-DFF netlist can run against N=5 tests.

**Acceptance:** Pin SHA (or DFF count / `DMA_BUF_DEPTH` stamp) of the tapeout N=5 netlist. Mismatch fails `_require_l2`.

### cov-gl-09 (medium) - L2 HS `blocked` not `na`; controller NAs pin-axis `CHK-CTRL-*` too

- **Also filed as:** `tb-hs-01`

**Problem:** Handshake blocked. Controller also NAs pin-axis `CHK-CTRL-*` that could have been checked.

**Acceptance:** Hierarchy-only -> `na`. Pin-axis CTRL checks stay live at L2.

### cov-gl-10 (medium) - M6 still open

- **Category:** M6
- **Location:** `docs/llm/verification/09-gate-level-and-x.md`; human wip

**Problem:** No Verilator X. SDF blocked. No randomized L2 reset. Human wip is accurate. **Do not claim M6 pass.**

**Acceptance:** M6 remains open until those items exist. This review does not close them.

### cov-gl-11 (low) - extra L2 asserts on golden not pin

- **Also filed as:** `cov-dma-01`

**Acceptance:** Pin-based extra asserts at L2, or labeled golden-only.

---

## Formal engine stubs

RTL wrap catchability is also under [`rtl.md`](rtl.md) `rtl-engine-01`.

### form-eng-01 (medium) - empty properties, runnable sby PASS

- **Location:** `test/formal/bind/qspi_engine_properties.sv` (empty module); `test/formal/engine/*.sby`

**Problem:** Vacuous PASS if someone runs sby.

**Scope:** D33: not a freeze gate. Dangerous if cited as prove.

**Acceptance:** Properties exist, or `make formal` stays exit 1 and CI never parses vacuous PASS as green. Do not claim M4.

### form-eng-02 (low) - nine `FP-*` listed unimplemented

- **Location:** properties file header (`FP-RST-QSPI`, `FP-QSPI-STATE`, `FP-CS-MUTEX`, `FP-RVALID-SCOPE`, `FP-WNEXT-SCOPE`, `FP-RVALID-COUNT`, `FP-WNEXT-COUNT`, `FP-QSPI-COUNT`, `FP-DLK-QSPI`)

**Problem:** Listed, not implemented.

**Acceptance:** Implement or mark `unimplemented` in the catalog without present-tense "asserts."

### form-eng-03 (medium) - harness comments `FA-RST` / `FA-REQ-LEGAL` as if present

- **Location:** `test/formal/engine/qspi_engine_harness.sv:3-12`

**Problem:** Comments name `FA-RST-INIT`, `FA-RST-RUN`, `FA-REQ-LEGAL` as M4 assumptions. Not present.

**Acceptance:** Comments say TODO, or assumptions are real `assume` statements.

### form-eng-04 (medium) - wrap detection depends on COUNT fill using wide multiply

- **Also filed as:** `rtl-engine-01` formal subsection
- **Location:** planned `FP-QSPI-COUNT` / `FP-RVALID-COUNT`

**Problem:** If COUNT is coded as `cycle_cnt == (byte_len << 1)` it will not catch wrap. Must use integer `2*accepted_byte_len`.

**Acceptance:** COUNT property uses a widened `2*` of the accepted request length, independent of the DUT compare expression.

### form-eng-05 (medium) - BMC depth 32 vs catalog 128

- **Location:** `test/formal/engine/qspi_engine_bmc.sby:7` (`depth 32`); cover job depth 128

**Problem:** Depth 32 is too short for an 11-byte txn. Even a correct COUNT would not see FETCH complete.

**Acceptance:** BMC depth covers 11-byte FETCH (catalog 128 or a measured bound). Cover job depth stays sufficient.

### form-eng-06 (low) - engine cover cites `FP-COV-*` not in engine catalog

- **Location:** `test/formal/engine/qspi_engine_cover.sby:3`

**Problem:** Cites `FP-COV-*` not listed in the engine planned IDs.

**Acceptance:** Align names with `07-formal.md` / bind header.

### form-eng-07 (low) - properties not bound

- **Location:** empty bind module; no `bind` statement

**Problem:** Not bound to DUT.

**Acceptance:** `bind qspi_engine qspi_engine_properties ...` with ports, or harness instantiates checkers.

### form-eng-08 (low) - no engine deadlock sby

- **Location:** no `qspi_engine_deadlock.sby`; `FP-DLK-QSPI` listed in bind header

**Problem:** No engine deadlock job. Integration deadlock sby exists but properties empty.

**Acceptance:** Engine deadlock job with a watchdog that does **not** pass on early `busy` low from wrap (`rtl-engine-01`).

---

## Formal controller / integration stubs

### form-ctrl-01 (medium) - `control_deadlock.sby` present tense; depth 96 only QSPI not YIELD 112 / CHUNK 192

- **Location:** `test/formal/integration/control_deadlock.sby` (header present-tense `FP-DLK-*`; `depth 96`)

**Problem:** Speaks as if `FP-DLK-*` exist. Depth 96 is QSPI-scale, not YIELD 112 / CHUNK 192 from the formal catalog.

**Acceptance:** TODO wording until properties exist. Depths match `07-formal.md` per property.

### form-ctrl-02 (medium) - all `control_*.sby` top `sys_qspi_harness`; never `top.v` / `top_harness`

- **Location:** `test/formal/integration/control_*.sby` `prep -top sys_qspi_harness`; `test/formal/integration/top_harness.sv` unused by sby
- **Also filed as:** `rtl-top-05`

**Problem:** Top wrapper OE/grant (`rtl-top-01..04`) is not in the formal cone.

**Acceptance:** A job with `top_harness` / `tt_um_lahnb_sgdma` for `FP-RST-OE` / `FP-GNT-RELEASE`, or catalog says top is sim-only (D33).

### form-ctrl-03 (low) - planned IDs omit `FP-COV-*` and `FP-DATA-COUNT`

- **Location:** `test/formal/bind/sys_controller_properties.sv` header vs catalog

**Problem:** Planned list omits cover IDs and `FP-DATA-COUNT`.

**Acceptance:** Lists match `07-formal.md`.

### form-ctrl-04 (low) - no ports/bind/assert

- **Location:** empty `sys_controller_properties.sv`

**Problem:** No ports, bind, or assert.

**Acceptance:** Same as `form-eng-07` for the controller.

### form-ctrl-05 (low) - bmc/prove vacuous PASS

- **Also filed as:** `form-eng-01` for the integration jobs
- **Location:** `control_bmc.sby`, `control_prove.sby`

**Problem:** Vacuous PASS.

**Acceptance:** Same policy as `form-eng-01`. Integration **does** instantiate real `qspi_engine` (good); still no properties.

### form-ctrl-06 (low) - sby `[script]` relative `../../../src` may not resolve in workdir

- **Location:** `[script] read -formal -sv ../../../src/...` vs `[files]` list

**Problem:** SymbiYosys copies `[files]` into the workdir; relative `../../../src` in `[script]` may not resolve there. `[files]` already lists the RTL.

**Acceptance:** `read` the basenames that sby copied, or use the documented sby path form. A dry run from `test/formal/integration/` must find `qspi_engine.sv`.

---

## `make formal`

`test/Makefile` `formal` target prints `TODO(M4)` and `exit 1`. That is the only safe CI behavior until properties exist (D33).
