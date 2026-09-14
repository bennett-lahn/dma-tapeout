# RTL findings (2026-08-21)

Every RTL finding from the 2026-08-21 read-only review. Formal job detail that is about stimulus/depth/vacuity lives in [`testbench.md`](testbench.md); items that are about DUT compares, busy timing, or jobs attached to this RTL are here too.

ID namespaces on first use: `Q-*` are simulation-provable QSPI checks; `CHK-*` are always-on monitors; `TC-*` are directed cases; `COV-*` are functional coverage points; `FP-*` are formal properties. Datasheet AC: `tACLK` (read data valid after falling SCK), `tCEM` (max CE# low), `tSP`/`tHD` (SIO setup/hold vs rising SCK), `tCSP` (CE# setup before first rising SCK), `tCHD` (CE# hold after last SCK). `DMA_BUF_DEPTH` `N` is on-chip scratch bytes. **D35** `ptr[23]` don't-care. **D16** 66 MHz / rising-edge RX. **D21** handshake (next write nibble before the following clk). **D26** bus keeper. **D33** M4 not a V1 freeze gate. **D34** unused pins tied 0.

Do not claim M5 closed. Do not claim M4 pass.

---

## Engine (`src/qspi_engine.sv`, `src/types.svh`)

### rtl-engine-01 (high) - IEEE width wrap on `byte_len << 1`

- **Category:** payload nibble count / FETCH length
- **Location:** `src/qspi_engine.sv:114` (READ_DATA), `:120` (WRITE_DATA); type `qpi_byte_len_t` in `src/types.svh:50-55`
- **Also filed as:** `rtl-engine-01-verdict` (CONFIRMED HIGH); formal catchability `form-eng-04`, `form-eng-05` (detail in [`testbench.md`](testbench.md))

**Problem:** Exit from READ_DATA / WRITE_DATA is `cycle_cnt == (byte_len << 1)`. `qpi_byte_len_t` is 4 bits (`QPI_BYTE_LEN_W = $clog2(12)` because `QPI_MAX_BYTES = 11`). IEEE 1800 `<<` result width is the left operand width, so `4'(11) << 1` is **6**, not 22. The same wrap hits every `byte_len >= 8` (`8 << 1` = 0 in 4 bits). FETCH is always an 11-byte TCD (`QPI_TCD_BYTES`). WAIT already widens: `cycle_cnt == QPI_CYCLE_CNT_W'(qspi_wait_cycles(...))` at `:107`. SEND_ADDR uses `'d6` at `:97`. The DATA path has no widen.

Icarus and Verilator promote `byte_len` before `<<` (Verilator C++: `0x1f & ((IData)byte_len << 1)`, so 11 becomes 22). M0-M5 and `CHK-HS-RDATA-COUNT` (completed Fast Read `0xEB` must emit `2*byte_len` `rdata_valid` pulses) would catch wrap **if** sim were IEEE-correct; they are not. Yosys / gate of this compare was **not** run.

Tapeout payload `N=5` does not wrap (`1..5 << 1` fits in 4 bits). The FETCH path is the production hole.

**Formal would / would-not catch (under this id):**

- `FP-QSPI-COUNT` (engine payload-cycle bound) **would not** catch wrap if filled as an upper bound: 6 SCK is still "in range."
- `FP-DLK-QSPI` (engine leaves busy in bounded time) **would not**: wrap makes `busy` fall *early*, which satisfies a naive liveness bound.
- A COUNT property that uses integer `2*accepted_byte_len` (not the DUT shift) **would** catch 11-byte FETCH emitting 6 `rdata_valid` instead of 22. That is `form-eng-04`: wrap detection depends on filling COUNT with a wide multiply, not `byte_len << 1`.
- Engine BMC depth 32 (`test/formal/engine/qspi_engine_bmc.sby`) is too short for an 11-byte transaction (catalog target 128). Even a correct COUNT fill would not see FETCH complete at depth 32 (`form-eng-05`).

**Scope:** Silicon / Yosys / any IEEE-width elaboration: FETCH of 11 bytes ends after 6 payload nibbles. Payload writes/reads of length 8..11 would also wrap; V1 controller only asks FETCH=11 and payload `k = min(N, remaining)` with tapeout `N=5`, so payload wrap is not in the V1 FSM request set unless `N` or a future caller raises `byte_len` to 8+. Sim (Icarus/Verilator) currently hides it. L0 engine tests and L1 FETCH pin length (`CHK-CTRL-FETCH-HEAD`, fetch must be 11 bytes / 22 nibbles on the pin) only catch wrap if the simulator does not promote. L2 netlist would follow Yosys width, not Icarus. Controller `data_cnt` uses `qpi_payload_nibble_cnt_t'(2 * ...)` (widened multiply) and is **not** this bug.

**Acceptance:** Fix accepted when (a) source compare is width-safe for `byte_len` 1..11 (5-bit nibble count: `qpi_payload_nibble_cnt_t`, or `{byte_len,1'b0}`, or `QPI_CYCLE_CNT_W'(byte_len)<<1`), (b) L0 11-byte `0xEB` emits 22 `rdata_valid`, (c) L1 FETCH still 11-byte pin length (N=8 directed still 13/13), (d) Yosys or equivalent IEEE-width elaboration of the compare is shown not wrapping `11<<1` to 6. Icarus **and** that Yosys/IEEE check must agree.

### rtl-engine-02 (medium) - combinational `wdata` into `sio_out`

- **Category:** D21 timing / STA
- **Location:** `src/qspi_engine.sv:166-168` (`sio_out <= wdata` in WRITE_DATA on `sclk_will_fall`); port comment `:16-18`, `:185-189`; controller mux `src/sys_controller.sv:107-109`

**Problem:** Same-cycle combinational `wdata` is sampled into `sio_out` on the WRITE_DATA falling-SCK flop. D21 requires the next nibble on `wdata` before the following clk. That is a 66 MHz combinational path from controller buffer mux through engine `wdata` into the SIO flop.

**Scope:** Functional sim at L0/L1 can pass with zero delay. STA / 66 MHz (D16) may fail setup into `sio_out`. Not a protocol-length bug. This review did not run STA.

**Acceptance:** STA (or a timed gate sim with SDF once unblocked) shows the controller `qspi_wdata` to engine `sio_out` flop meets 66 MHz setup, or RTL is recut so `wdata` is captured on a flop before the SIO path. D21 same-cycle contract must remain documented if kept.

### rtl-engine-03 (medium) - unused `qpi_payload_nibble_cnt_t`

- **Category:** type / in-module property
- **Location:** `src/types.svh:57-59`; engine never uses the type; compare at `src/qspi_engine.sv:114,:120`

**Problem:** Package already defines a wide payload nibble count. Engine does not use it for DATA exit. There is no in-module count property on `cycle_cnt` vs `2*byte_len`.

**Scope:** Documentation/type hygiene plus the wrap in `rtl-engine-01`. Using the type in the compare is one accepted repair for 01.

**Acceptance:** DATA exit uses `qpi_payload_nibble_cnt_t` (or an equally wide expression). Optional: a comment-level or SVA count that `rdata_valid`/`wdata_next` beats match `2*byte_len` / `2*byte_len-1`.

### rtl-engine-04 (low) - `byte_len=0` still one SCK / one `rdata_valid`

- **Category:** zero-length engine txn
- **Location:** `src/qspi_engine.sv:114,:120` (exit `cycle_cnt == (byte_len << 1)`); `:239-262` (`cycle_cnt` starts 0, increments on `sclk_will_rise` at `:256`, state advances on `sclk_will_fall` when `sclk_en`)

**Problem:** With `byte_len=0` the compare is `cycle_cnt == 0`. `cycle_cnt` is already 0 on entry, but READ_DATA/WRITE_DATA still take the enabled-SCK path long enough for one rising SCK (and therefore one `rdata_valid` on READ_DATA at `:230-232`) before falling-edge exit to SCLK_OFF.

**Scope:** V1 controller does not issue `byte_len=0` data txns (zero-length TCD skips READ/WRITE). A future or L0-direct caller can. Not the FETCH wrap.

**Acceptance:** `byte_len=0` produces zero payload SCK and zero `rdata_valid` / `wdata_next`, or the engine documents and tests that 0 is illegal and the controller remains the only legal requester with `byte_len` in `1..N` or 11.

### rtl-engine-05 (low) - `QPI_CYCLE_CNT_W` sized from payload only

- **Category:** counter width coupling
- **Location:** `src/types.svh:51-52`; SEND_ADDR / WAIT compare to 6 at `src/qspi_engine.sv:97,:107`

**Problem:** `QPI_CYCLE_CNT_W = $clog2((2 * QPI_MAX_BYTES) + 1)` is payload-nibble sized. SEND_ADDR/WAIT `== 6` needs more than 2 bits. Today `QPI_MAX_BYTES=11` makes the width 5, which covers 6. The 6 is a hidden coupling to "TCD=11 implies width>=3." If `QPI_MAX_BYTES` were ever 1, addr/wait compares would wrap.

**Scope:** Comment/elaboration coupling. V1 numbers are safe. Not the 4-bit `byte_len` shift.

**Acceptance:** Addr/wait compares use a width that is explicitly `max(payload_nibbles, 6)`, or a named `QPI_ADDR_CYCLES=6` with an elaboration check that `QPI_CYCLE_CNT_W` covers it.

### rtl-engine-06 (low) - port comment "valid on `txn_valid`" weaker than spec

- **Category:** comment vs D21
- **Location:** `src/qspi_engine.sv:16-18`

**Problem:** Comment says write nibble is "valid on `txn_valid`". Spec/first sample is at WRITE_DATA entry after cmd+addr (and wait=0 for `0x02`). First nibble must still be stable from `txn_valid` through that point (engine does not latch the request).

**Scope:** Comment only. Behavior is hold-until-`~busy`.

**Acceptance:** Port comment states: hold `wdata[3:0]` from `txn_valid` until `~busy`; first SIO sample is the WRITE_DATA entry beat after cmd+addr; later nibbles same-cycle on `wdata_next` (D21).

### rtl-engine-07 (low) - `cmd != 0x02` goes to READ_DATA with wait=0

- **Category:** opcode decode
- **Location:** `src/qspi_engine.sv:97-100,:107-108`; `qspi_wait_cycles` default 0 at `src/types.svh:24-29`

**Problem:** Any `cmd` other than Write `0x02` that has `qspi_wait_cycles==0` enters READ_DATA with zero wait. Opcode `0x0B` (SPI Fast Read, not a V1 ASIC command) would be a zero-wait read. Legality relies on the FSM only issuing `0xEB` / `0x02`.

**Scope:** Illegal if a caller issues a non-V1 opcode. Controller only issues `QSPI_CMD_FAST_READ` / `QSPI_CMD_WRITE`. Formal `FA-REQ-LEGAL` is comments-only (see `form-eng-03`).

**Acceptance:** Either unknown cmds are ignored / stay IDLE, or a bound/assume documents that `cmd` is only `0xEB`/`0x02` and L0 negatives exist. No silent `0x0B` path in a legal V1 request set.

### rtl-engine-08 (note) - last-beat CE hold vs preferred `tCHD`

- **Category:** termination AC
- **Location:** SCLK_OFF then CS_OFF, `src/qspi_engine.sv:125-129`; two clk at 66 MHz is ~30 ns

**Problem:** Last-beat CE# hold is 2 clk, about 30 ns. Preferred `tCHD > tACLK + tCLK` is about 35.5 ns. Datasheet **min** `tCHD` 3 ns is met. CE# mutex (never both RAM CE# low) is OK. Dummy 6 for `0xEB` is OK.

**Scope:** Margin vs a preferred inequality, not a min-spec fail. `tCSP` (CE# setup before first rising SCK) is a CS_ON story, not this hold. MCU pass-through frames are out of ASIC engine scope.

**Acceptance:** Keep as note unless a timed board/STA review requires an extra SCLK_OFF beat. If changed, re-check `tCEM` (max CE# low) on 11-byte FETCH.

### rtl-engine-09 (note) - human engine doc still says `N=1` for no `tCEM` slicer

- **Category:** doc drift
- **Location:** `docs/human/architecture/blocks/qspi-engine.md:21,:166`; descriptor default in `docs/human/architecture/blocks/descriptor-fsm.md:33` (`DMA_BUF_DEPTH` default 1)

**Problem:** Human qspi-engine text still says V1 `N=1` as the reason there is no `tCEM` slicer. Tapeout `N=5`. 11-byte FETCH is still far under 4 us `tCEM`.

**Scope:** Docs only. RTL default is 5 (`src/sys_controller.sv:41`, `src/top.v:24`). Not a slicer bug at N=5.

**Acceptance:** Human engine/FSM text says tapeout `N=5`, that 11-byte FETCH and 5-byte payload remain under `tCEM` at 33 MHz SCK, and that a slicer is still not required until the documented failing-N thresholds.

---

## Controller (`src/sys_controller.sv`, `src/types.svh`)

Composer review `8968ec70` vs grok `107e52e5`. Both recorded. Nibble-width: controller `data_cnt` / `2 * transfer_len` uses `qpi_payload_nibble_cnt_t` (wide). The IEEE wrap is engine-only (`rtl-engine-01`). Chunking `k = min(N, remaining)`, QUIT, zero-length skip, and `write_pending` sequencing: **no issue** in either review.

### rtl-ctrl-01 (medium, Composer) / SAFE (grok) - last nibble vs `~busy`

- **Category:** FETCH/READ latch
- **Location:** `src/sys_controller.sv:278-286` (latch only when `qspi_rdata_valid && next_state == FETCH/READ`); engine `busy = (curr_state != QSPI_IDLE)` at `src/qspi_engine.sv:184`; last `rdata_valid` is a 1-clk pulse on rising SCK in READ_DATA (`:230-232`); engine then SCLK_OFF then CS_OFF before IDLE (`:125-129`)

**Problem (Composer):** FETCH/READ latch only while `next_state` is still FETCH/READ. If the last `rdata_valid` were coincident with `~busy` (controller would already be leaving FETCH/READ), the last nibble would be dropped.

**Verdict (grok):** **SAFE** with the current engine: `busy` stays high through SCLK_OFF and CS_OFF after the last `rdata_valid`, so the controller is still in FETCH/READ on that pulse. Residual risk if engine busy timing changes.

**Scope:** Current `qspi_engine` + `sys_controller` pairing is safe. A busy-falls-with-last-rdata_valid engine change would drop the last of 22 TCD nibbles and the last payload nibble. Sim M0-M5 passing is not a proof if tests never inspect the last nibble vs the busy-fall cycle.

**Acceptance:** Prove the last of 22 TCD nibbles and the last payload nibble are latched (waveform or property), **including the cycle `busy` falls**. If engine termination is recut, re-prove. Formal `FP-RVALID-COUNT` / a FETCH-nibble property may be used; D33 means this is not a freeze gate.

### rtl-ctrl-02 (low, Composer) - NEW_FETCH uses `next_tcd`, not hardcoded head

- **Category:** D23 head
- **Location:** `src/sys_controller.sv:114,:156,:267-275`; QUIT clear of NEXT to `0` / PSRAM0 at `:272-275`

**Problem:** NEW_FETCH addresses `task_ctrl_desc.next_tcd`, not a hardcoded `0x000000`/PSRAM0. Cleared on `rst_n` or QUIT. A future IDLE path that does not clear NEXT would break D23 (next START must fetch the fixed head).

**Scope:** Current RTL is OK: reset zeros the TCD, QUIT writes NEXT to 0/PSRAM0 before IDLE. Not a present bug.

**Acceptance:** Remain true that every path into IDLE (QUIT, `rst_n`) leaves NEXT at 0/PSRAM0, or NEW_FETCH special-cases the first fetch after START as head. Directed: QUIT then START fetches `0x000000` / PSRAM0 (`TC-*` chain-quit already exists on the sim side).

### rtl-ctrl-03 (note, Composer) - 24-bit `+N` with no `[22:0]` mask

- **Category:** D35
- **Location:** `src/sys_controller.sv:91-94`

**Problem:** Pointer increment is 24-bit `+ N` with no `[22:0]` mask. D35 says `ptr[23]` is don't-care (may be 1). Carry into bit 23 is architectural don't-care.

**Scope:** Not a spec violation. Firmware that treats bit 23 as a device select would be wrong (device is `CTRL_FLAGS`).

**Acceptance:** Keep D35. Do not add an SVA "bit 23 always 0." Optional: document that increment may set/clear bit 23 and the engine still presents all 24 address nibbles.

### rtl-ctrl-04 (note, Composer) - properties are comments only

- **Category:** SVA / D33
- **Location:** `src/sys_controller.sv:343-371`; bind stub `test/formal/bind/sys_controller_properties.sv`

**Problem:** Listed assertions are comments. No SVA. Bind module is empty.

**Scope:** M4 not a V1 freeze gate (D33). Does not make RTL wrong.

**Acceptance:** Either implement bind properties or keep D33 and stop wording comments as if they were live asserts. See `form-ctrl-*` in [`testbench.md`](testbench.md).

### rtl-ctrl-05 (note, Composer) - human FSM default `N=1` vs RTL default 5

- **Category:** doc drift
- **Location:** `docs/human/architecture/blocks/descriptor-fsm.md:25-33`; RTL `src/sys_controller.sv:41`

**Problem:** Human descriptor-fsm still says V1 `N=1` and module default 1. RTL default is 5.

**Scope:** Docs. Same family as `rtl-engine-09`.

**Acceptance:** Human FSM text says tapeout and RTL default `N=5`; verification sweep remains `1..DMA_BUF_DEPTH_MAX` (8).

### rtl-ctrl-06 (note, Composer) - D34 unused pins not this module; START only in IDLE with `~bus_req`

- **Category:** host protocol
- **Location:** START gate `src/sys_controller.sv:186-192`; unused pins `src/top.v:133`

**Problem:** None as a bug. D34 unused `ui_in`/`uo_out` bits are tied at top, not in this module. START is accepted only in IDLE with `~bus_req`. That matches spec.

**Scope:** Passing note.

**Acceptance:** Keep START-in-IDLE-and-`~bus_req`. Do not add ERROR pins (D34).

### rtl-ctrl-01-grok (medium, coverage/comment) - stale "highest bit of addresses should always be 0"

- **Also filed as:** grok `rtl-ctrl-01` (distinct from Composer `rtl-ctrl-01` last-nibble latch)
- **Category:** D35 / retired checkers
- **Location:** `src/sys_controller.sv:353`

**Problem:** Comment still says the highest address bit should always be 0. D35 retired `CHK-PIN-ADDR23-ZERO` (always-on monitor that A[23] is 0), `Q-ADDR23` (sim check that A[23] is 0), and `FP-ADDR-MSB` (formal property that address MSB is 0). Implementing that comment as SVA would **fail** legal `ptr[23]=1`.

**Scope:** Comment / future SVA only. RTL does not force bit 23 to 0.

**Acceptance:** Delete or invert the comment to D35 don't-care. No new assert that bit 23 is 0. Dispose tables may list the retired IDs as `na` (see `tb-pin-08`).

### Controller residuals (grok; notes, not defects)

These are current behavior, called out so a later change does not "fix" them by accident.

| Residual | Location | What to keep |
|---|---|---|
| `BUS_REQ` wins over QUIT so `done` stays low until REQ drops | `src/sys_controller.sv:209-212` vs `:320-323` | Legal: grant while a QUIT TCD is decoded. `done` is idle-or-idle-stall. |
| NEW_FETCH waits `~busy` then stalls if `bus_req` | `:194-200` | Does not pulse `txn_valid` while `bus_req` (line 88). |
| QUIT does not pulse `txn_valid` | `:88` with `:210-212` | No data txn after QUIT. Correct. |
| Self-pointing NEXT has no HW reject | D35 | Allowed; spin until `rst_n`. |

Chunking `k=min(N,remaining)`, QUIT, zero-length, `write_pending`: **no issue**.

---

## Top wrapper (`src/top.v`, `info.yaml`)

### rtl-top-01 (medium) - START high when `rst_n` rises

- **Category:** reset / START pulse
- **Location:** two-flop START sync `src/top.v:72-84`; firmware `enable_project` muxes then `ui_in=0` in `firmware/asic.py:154-171`

**Problem:** If START (`ui_in[0]`) is high when `rst_n` rises, the two-flop fills `00 -> 01 -> 11` and `start_sync[1] & ~start_sync_d` produces **one** start pulse. `enable_project` mux-selects the design **before** it writes `ui_in=0`, so a high leftover START can be sampled across reset release.

**Scope:** Board/MCU sequencing. Sim usually drives `ui_in=0` through reset. One unintended DMA start after enable+reset.

**Acceptance:** Either firmware asserts `ui_in=0` (and holds it) **before** releasing `rst_n` after mux, or RTL treats the first two post-reset START samples as a fill and does not edge-detect until the synchronizer has seen a stable 0. Directed: START held high through `rst_n` rise does not start a chain, or is documented as a required host constraint with a test.

### rtl-top-02 (medium) - `uio_oe` combo vs BUS_REQ two-flop lag

- **Category:** D26 / grant lag
- **Location:** `uio_oe` combo `src/top.v:147-161` (`bus_park & rst_n`); `bus_gnt` registered in controller `:316-324`; `bus_req` two-flop `:76-85`

**Problem:** `uio_oe` turns on combinationally as soon as `rst_n=1` and `~bus_gnt`. BUS_REQ has a 2-flop lag into the controller. MCU may still be driving from the `rst_n=0` window when ASIC OE comes on.

**Scope:** Reset-release contention on `uio`. D26 wants MCU drive only while `BUS_GNT` or `rst_n=0`. The gap is the cycles after `rst_n` rises before a granted MCU has released, and before a pending REQ has produced `bus_gnt`.

**Acceptance:** Host sequence: Hi-Z MCU OE, then release `rst_n`, then REQ. RTL optional: delay ASIC park OE until the BUS_REQ synchronizer has produced a known value. Contention checker (`CHK-ARB-*` / `Q-MUX`, mux ownership) must be able to see the window (today SV resolves Z to 0; see `tb-sv-01`).

### rtl-top-03 (low) - SIO OE gated by engine `sio_oe_reg`; CS/SCK OE on immediately

- **Category:** park / float
- **Location:** `src/top.v:155-159` vs `:154,:157,:160-161`; `sio_oe_reg` reset 0 at `src/qspi_engine.sv:172-174`

**Problem:** After `rst_n=1`, CS and SCK OE go on immediately (`bus_park & rst_n`). SIO OE is AND of that with `sio_oe_reg`, which stays 0 until the first clk in idle sets it to 1 (`:175-180` else branch). Brief SIO float. `CHK-ARB-PARK` (ASIC parks CS high / SCK low while keeper) rearms on first clk.

**Scope:** One-cycle (or until first clk) SIO float after reset release. CS/SCK park is immediate. Board pull-ups on CS.

**Acceptance:** Document as acceptable don't-care, or reset `sio_oe_reg` to 1 so SIO park drive matches CS/SCK on the same `rst_n` edge. Checker should not require SIO drive in the first pre-clk window unless that is made a requirement.

### rtl-top-04 (low) - `done`/`bus_gnt` registered; not combo-cleared with `rst_n`

- **Category:** status lag
- **Location:** `src/top.v:129-132`; controller flops `:316-324`

**Problem:** `done`/`bus_gnt` are registered. They do not combinationally clear with `rst_n`. `CHK-RST-STATUS` (status pins during reset) allows lag. `BUS_GNT` can read 1 for one clk while OE is already 0 (`uio_oe` uses combo `~bus_gnt & rst_n`, so `rst_n=0` drops OE immediately while `uo_out[1]` waits a flop).

**Scope:** One-cycle pin inconsistency on reset assert. Allowed by current RST checker policy.

**Acceptance:** Keep with an explicit RST-checker allowance, or combo-force `uo_out[1]=0` while `rst_n=0`. Test: `rst_n=0` implies `uio_oe==0` immediately; `BUS_GNT` lag is either 0 or documented.

### rtl-top-05 (low) - top M4 stubs

- **Category:** D33
- **Location:** `test/formal/bind/top_properties.sv`; `test/formal/integration/top_harness.sv`; no `.sby` selects `top_harness`

**Problem:** Top properties/harness are M4 stubs. D33: M4 is not a V1 freeze gate.

**Scope:** Formal only. See `form-ctrl-02`.

**Acceptance:** Do not claim top-level `FP-RST-OE` / `FP-GNT-RELEASE` pass. Either fill jobs or keep D33.

### rtl-top-06 (note) - `CHK-ARB-GNT-OE` true by construction

- **Category:** checker tautology
- **Location:** same `bus_gnt` net to `uo_out[1]` and `bus_park` in `src/top.v:132,:152`

**Problem:** `CHK-ARB-GNT-OE` (grant implies OE released) is true by construction: one `bus_gnt` net. Not an independent pin check.

**Scope:** Checker quality, not RTL bug.

**Acceptance:** Keep as a netlist-tied check, or move the check to "MCU must not drive when `bus_gnt==0`" which is not true-by-construction.

### rtl-top-07 (note) - `qspi_byte_len` hardcoded `[3:0]`

- **Category:** width coupling
- **Location:** `src/top.v:55`; `QPI_BYTE_LEN_W` in `src/types.svh:50`

**Problem:** Top cannot `import` `qspi_pkg` (yowasp port check; `info.yaml` lists `top.v` first). Width `[3:0]` matches current `QPI_BYTE_LEN_W`. Raising `DMA_BUF_DEPTH_MAX` above 15 would truncate `qspi_byte_len`.

**Scope:** Future depth growth. V1 max 8 is fine.

**Acceptance:** Elaboration check or a comment that `[3:0]` must equal `QPI_BYTE_LEN_W`. If max bytes exceeds 15, widen the wrapper net (still package-free).

### rtl-top-08 (note) - `src/rtl/` not the source of truth

- **Category:** tree
- **Location:** `test/Makefile` `SRC_RTL := $(REPO_ROOT)/src` (line 53). Move recorded as 294b161.

**Problem:** Canonical RTL is `src/`. Makefile aliases `SRC_RTL` to `src/`. Do not compile an old `src/rtl/` tree as DUT.

**Scope:** Build paths. Review treated `src/` as DUT.

**Acceptance:** Sim, formal `.sby`, and harden read `src/`. No second DUT copy on the compile line.

### rtl-top-09..13 (note) - passing contracts

| ID | Location | Passing claim |
|---|---|---|
| rtl-top-09 | `src/top.v:72-85` | Two-flop START/BUS_REQ sync is the correct async-input shape. |
| rtl-top-10 | `src/top.v:147-161` | D26 OE mux: park while `rst_n && ~bus_gnt`; SIO follows engine mask. |
| rtl-top-11 | `src/top.v:138` | Flash CS data is constant 1; ASIC never drives flash CS low. |
| rtl-top-12 | `src/top.v:133` | `uo_out[7:2]=0` (D34). |
| rtl-top-13 | `src/top.v:9-16` vs `info.yaml` | Pin names match `info.yaml` / host-interface map. |

**Scope:** Not defects. Recorded so a later refactor does not drop them.

**Acceptance:** Remain true after any top.v edit.

---

## Formal jobs attached to this RTL (brief)

Detail: [`testbench.md`](testbench.md) `form-eng-*` / `form-ctrl-*`.

- Engine bind `test/formal/bind/qspi_engine_properties.sv` is empty. Runnable `.sby` would **PASS vacuously**. Do not treat that as engine prove.
- Integration `.sby` files wire the **real** `qspi_engine` (good). They do not substitute a stub engine.
- `test/formal/integration/control_deadlock.sby` speaks of `FP-DLK-*` in the present tense; properties are not implemented. Depth 96 covers QSPI-scale, not YIELD 112 / CHUNK 192.
- No `.sby` selects `top_harness` / `top.v`.
- `test/Makefile` `formal` target prints TODO and `exit 1`.

D33: none of this is a V1 freeze gate. The wrap in `rtl-engine-01` is still a **source** bug even if M4 stays open.
