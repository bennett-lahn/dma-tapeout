# Firmware findings (2026-08-21)

Every firmware / MCU / board-path finding from the 2026-08-21 read-only review, including firmware-vs-sim copy drift. No RTL was changed for this catalog.

ID namespaces on first use: `Q-*` are simulation-provable QSPI checks; `CHK-*` are always-on monitors; `TC-*` are directed cases (`TC-TCD-BE` is the 11-byte big-endian TCD packing vector; `TC-OVERLAP` is overlapping src/dest); `COV-*` are functional coverage points; `FP-*` are formal properties. Datasheet AC: `tACLK` (read data valid after falling SCK), `tCEM` (max CE# low), `tSP`/`tHD` (SIO setup/hold vs rising SCK), `tCSP`/`tCHD` (CE# setup before first rising SCK / hold after last SCK). `DMA_BUF_DEPTH` `N` is on-chip scratch bytes. **D35** `ptr[23]` don't-care. **D16** 66 MHz / rising-edge RX. **D21** handshake. **D26** bus keeper. **D33** M4 not a V1 freeze gate. **D34** unused pins tied 0.

Do not claim M5 closed. Do not claim M4 pass. Mock DMA is not silicon.

**Fix pass 2026-08-21:** every `fw-*` id in this file has **Status: fixed**. Problem/Scope/Acceptance kept for history. Residual that remains hardware-only: wall-clock `tCEM` (max CE# low) of a single PIO burst on real PSRAM (`fw-board-02`); CPython does not execute RP2 PIO (`fw-board-14`).

---

## Chain oracle (`firmware/chain.py`, `firmware/build.py`, firmware tests)

### fw-chain-01 (high) - `DEFAULT_DMA_BUF_DEPTH=1` labeled tapeout

- **Category:** tapeout N vs oracle default
- **Location:** `firmware/chain.py:76-77`; `interpret_chain` default `:436-438`; used by `firmware/runner.py:85`, `firmware/tests/mock_transport.py:57`, `firmware/tests/test_build.py:29,:49`
- **Also filed as:** `fw-demo-09`, `fw-sim-drift-03`; sim-side twin `tb-ref-01` (`test/reference/chain.py:74`)

**Problem:** Comment says "V1 tapeout configuration" then sets `DEFAULT_DMA_BUF_DEPTH = 1`. Tapeout `N` is 5. Overlap semantics differ: at N=1 every byte is its own chunk (memmove-like per byte); at N=5 a `transfer_len` that fits in one chunk is a single read-then-write of the original bytes (not a sliding memmove). `runner` / mock / `test_build` all call `interpret_chain` without `dma_buf_depth`.

**Scope:** Firmware host pytest, mock DMA, demo `run_chain` expected image, and any REPL that omits the argument. Does not change ASIC RTL (default 5). Sim reference has the same default (`tb-ref-01`). Overlap tests that only run at N=1 do not cover tapeout.

**Acceptance:** Default is 5, or every production call site passes `dma_buf_depth=5` explicitly. Firmware tests include an overlap vector at N=5 whose expected image differs from N=1. Comment must not say tapeout while the constant is 1.

**Status: fixed.** Default is tapeout N=5 in firmware/chain.py and test/reference/chain.py (DMA_BUF_DEPTH_TAPEOUT). Overlap test at N=1 vs N=5 in firmware/tests/test_chain.py. Comment no longer says tapeout while the constant is 1.

### fw-chain-02 (high) - no `firmware/tests/test_chain.py`

- **Category:** missing tests
- **Location:** no file `firmware/tests/test_chain.py`; oracle rules live in `test/tests/test_reference_chain.py` (not importable from firmware)

**Problem:** Firmware pytest cannot import `test/`. Chain interpreter has no firmware-side unit file. Oracle rules are only on the sim tree.

**Scope:** Firmware CI. A broken `interpret_chain` can still ship if sim tests are not run in the same job.

**Acceptance:** `firmware/tests/test_chain.py` (or equivalent) covers QUIT, LEN=0, overlap with explicit `N`, dest_device=1, and at least one multi-chunk copy **without** importing `test/`.

**Status: fixed.** Added firmware/tests/test_chain.py covering QUIT, LEN=0, overlap with explicit N, dest_device=1, multi-chunk copy; no import of test/.

### fw-chain-03 (medium) - no QUIT=1 with nonzero LEN/ptr interpret test

- **Category:** QUIT fields
- **Location:** `firmware/chain.py` interpret loop; firmware tests never construct QUIT with nonzero LEN/ptr

**Problem:** No firmware test that QUIT=1 with nonzero LEN/pointers is still a terminator (no data txn).

**Scope:** Firmware tests only. Sim generator may exclude this shape (`tb-ref-05`).

**Acceptance:** One interpret test: QUIT=1, nonzero LEN and ptrs, no DATA_READ/DATA_WRITE, chain stops.

**Status: fixed.** test_quit_with_nonzero_len_and_ptrs_is_terminator: QUIT=1 with nonzero LEN/ptrs emits only FETCH_READ.

### fw-chain-04 (medium) - LEN=0 never asserted via `interpret_chain`

- **Category:** zero-length
- **Location:** firmware tests; `interpret_chain` zero-length path in `firmware/chain.py`

**Problem:** LEN=0 is never asserted through firmware `interpret_chain`.

**Scope:** Firmware tests. Architecture: zero-length is a no-op fetch of next.

**Acceptance:** Firmware test: LEN=0 TCD produces no data txn and follows NEXT.

**Status: fixed.** test_len_zero_follows_next_with_no_data via interpret_chain.

### fw-chain-05 (medium) - no overlap firmware test; never pass `dma_buf_depth`

- **Category:** overlap / N
- **Location:** no firmware overlap test; no call passes `dma_buf_depth`
- **Also filed as:** overlap half of `fw-chain-01`

**Problem:** Overlap is untested on the firmware tree. Callers never pass `dma_buf_depth`.

**Scope:** Same as `fw-chain-01` for overlap. `TC-OVERLAP` on sim at `len=6` with `N>=6` is one chunk (`tb-ref-02`).

**Acceptance:** Firmware overlap test at N=1 and N=5 with different expected dest bytes; at least one call site passes `dma_buf_depth` explicitly.

**Status: fixed.** Overlap at N=1 vs N=5 with different dest bytes; callers pass dma_buf_depth explicitly.

### fw-chain-06 (medium) - `test_build` only dest bytes/path

- **Category:** weak build coverage
- **Location:** `firmware/tests/test_build.py`

**Problem:** Checks dest bytes/path only. Both N=1 (four 1-byte pairs) and N=5 (one 4-byte pair) would pass the same dest-byte assert for a non-overlapping copy.

**Scope:** Cannot distinguish tapeout chunking from N=1.

**Acceptance:** Assert transaction log chunking (count and lengths) at a stated `N`, or parametrize N=1 vs N=5 on an overlapping copy.

**Status: fixed.** test_build asserts DATA_READ lengths at N=5 (one 4-byte pair) and N=1 (four 1-byte pairs).

### fw-chain-07 (medium) - `test_address_zero_is_not_a_terminator` only QUIT at head

- **Category:** address 0
- **Location:** `firmware/tests/test_build.py:38-49` region (address-0 / head QUIT)

**Problem:** Address 0 as a **link** (NEXT=0 meaning "fetch head again" vs QUIT) is not distinguished. Test only places QUIT at head.

**Scope:** Firmware tests. Architecture: address 0 is valid, not a terminator.

**Acceptance:** A chain whose NEXT is 0 without QUIT is either a cycle (budget abort) or a documented self-pointing case (D35), not treated as stop.

**Status: fixed.** Independent grok 4.5 verification found this PARTIAL: `test_address_zero_as_link_is_cycle_not_stop` already treats NEXT=0 without QUIT as a cycle/budget abort (`ReferenceLimitError`), but `test_address_zero_is_not_a_terminator` still only placed QUIT at head. Follow-up: that build test now uses `add_copy(..., next_tcd=0)` and expects `ReferenceLimitError`; `test_place_head_quit_at_address_zero` keeps the QUIT-at-head empty-run via `place_head_quit`. The chain test was not weakened.

### fw-chain-08 (medium) - `add_copy` defaults NEXT to head

- **Category:** infinite refetch
- **Location:** `firmware/build.py:47-48` (`next_tcd=0`, `next_device=0`); head is PSRAM0 `0x000000`

**Problem:** Forgetting `add_quit` leaves NEXT at the head. Interpreter/ASIC refetch the same TCD until budget/`rst_n`.

**Scope:** Builder footgun. D35 allows self-pointing. Demo currently does call `add_quit`.

**Acceptance:** Default NEXT to an obvious unset, or `add_copy` requires `next_tcd`, or tests fail if a built image has no QUIT reachable. Document that default 0 is a self-pointing loop.

**Status: fixed.** add_copy requires next_tcd; omitting it raises ValueError. Explicit 0 remains a D35 self-point.

### fw-chain-09 (low) - `place_head_quit` unused alias

- **Category:** dead API
- **Location:** `firmware/build.py:80-82`

**Problem:** `place_head_quit` is an unused alias of `add_quit` at head.

**Scope:** API noise.

**Acceptance:** Use it in an empty-run test or delete it.

**Status: fixed.** place_head_quit used in empty-run tests.

### fw-chain-10 (low) - `new_image` / `place_bytes` sugar; span checks only in interpret

- **Category:** builder
- **Location:** `firmware/build.py:19-35`; span checks in `firmware/chain.py` interpret

**Problem:** Helpers do not span-check TCD vs payload layout. Span checks run only in interpret.

**Scope:** Builder can place overlapping records; interpret may then error or silently overlap.

**Acceptance:** Document that layout errors are interpret-time, or add a `place_*` span check with a test.

**Status: fixed.** Documented: layout overlap is interpret-time; place_* only span-check 0x000000..0x7FFFFF.

### fw-chain-11 (low) - no `dest_device=1` / interpret after `link`

- **Category:** device 1
- **Location:** `firmware/build.py:72-77` (`link`); firmware tests stay on device 0
- **Also filed as:** `fw-tcd-03`

**Problem:** No firmware test of `dest_device=1` or of `interpret_chain` after `link`.

**Scope:** Cross-device builder path untested on firmware pytest.

**Acceptance:** One image with dest on PSRAM1, linked NEXT, interpreted with explicit N=5.

**Status: fixed.** Independent grok 4.5 verification found this PARTIAL: `test_dest_device_1_after_link_at_n5` called `link` then rewrote the head TCD with `place_tcd` before `interpret_chain`, so interpret never consumed the image `link()` wrote. Follow-up: dest_device=1 is on the first TCD, `link` fills NEXT, interpret at `dma_buf_depth=5` with no intervening head rewrite; path includes device 1 and `DATA_WRITE.device == 1`.

### fw-chain-12 (note) - interpreter matches when depth is passed

- **Category:** algorithm
- **Location:** `firmware/chain.py` vs `test/reference/chain.py`

**Problem:** None when `dma_buf_depth` is passed explicitly. Algorithm matches the sim oracle in that case.

**Scope:** Passing note. Defaults still wrong (`fw-chain-01`).

**Acceptance:** Keep algorithm parity. Add a recopy/hash test (`fw-sim-drift-04`).

**Status: fixed.** Algorithm unchanged. Recopy/hash test added (fw-sim-drift-04).

---

## TCD pack (`firmware/tcd.py`, `firmware/tests/test_tcd.py`)

### fw-tcd-01 (note) - packing matches `tcd_t`

- **Category:** layout
- **Location:** `firmware/tcd.py` encode/decode; `src/types.svh` `tcd_t`

**Problem:** None. Packed 11-byte layout matches RTL `tcd_t`.

**Acceptance:** Remain true. Field **order** of the dataclass is not the packed order (`fw-tcd-10`).

**Status: fixed.** Packing still matches tcd_t. Dataclass field order documented; no struct.pack by iteration.

### fw-tcd-02 (high) - firmware tests never encode `ptr[23]`

- **Category:** D35
- **Location:** `firmware/tests/test_tcd.py`; `PTR_BIT23` exists in `firmware/tcd.py:36`

**Problem:** Firmware TCD tests never encode pointer bit 23. D35 says it is don't-care.

**Scope:** Firmware unit tests. Sim reference may also omit (`cov-dma-02`, `cov-refu-02`).

**Acceptance:** Encode/decode/validate round-trip with `ptr[23]=1` on src, dest, and NEXT. Validate must accept it.

**Status: fixed.** test_ptr23_roundtrip_on_src_dest_next encode/decode/validate with ptr[23]=1.

### fw-tcd-03 (high) - `dest_device` never 1 in firmware TCD tests

- **Category:** device bit
- **Location:** `firmware/tests/test_tcd.py:24` (`dest_device=0`); vector A0 has dest=0
- **Also filed as:** `fw-tcd-06`, `cov-dma-09`, `cov-refu-07`

**Problem:** `dest_device` is never 1 in firmware TCD tests. Packed CTRL_FLAGS dest bit is unexercised.

**Scope:** Firmware `TC-TCD-BE` subset. Cross-device encode could be wrong and still pass.

**Acceptance:** A vector with `dest_device=1` (and ideally `src_device`/`next_device` crossed) round-trips.

**Status: fixed.** test_dest_device_1_roundtrip_and_crossed_devices; flags byte 0xC0.

### fw-tcd-04 (medium) - encode reserved / out-of-range not tested

- **Category:** validate
- **Location:** `firmware/tcd.py` `encode_tcd` / `validate_tcd`; firmware tests

**Problem:** Encode of reserved-nonzero and out-of-range fields is not tested on the firmware tree (beyond one reserved reject).

**Scope:** Firmware tests. Sim `test_reference_tcd` is broader (`fw-tcd-05`).

**Acceptance:** Firmware tests: nonzero reserved rejected on encode; out-of-range ptr/len rejected; error text stable.

**Status: fixed.** encode rejects reserved, OOR ptr/len/device with stable field names in the error.

### fw-tcd-05 (medium) - firmware `test_tcd` is a 3-case subset

- **Category:** subset vs sim
- **Location:** `firmware/tests/test_tcd.py` vs `test/tests/test_reference_tcd.py`

**Problem:** Three cases vs the sim reference TCD suite.

**Scope:** Copy drift. Firmware cannot import `test/`.

**Acceptance:** Firmware suite covers BE vector, `ptr[23]`, dest_device=1, reserved, and QUIT round-trip, or an explicit recopy test (`fw-sim-drift-04`).

**Status: fixed.** firmware test_tcd covers BE, ptr[23], dest=1, reserved, QUIT round-trip plus recopy hash.

### fw-tcd-06 (medium) - `TC-TCD-BE` dest bit not exercised

- **Category:** `TC-TCD-BE`
- **Location:** `firmware/tcd.py` `TC_TCD_BE_*`; flags byte `0xA0` in `firmware/tests/test_tcd.py:14-16`
- **Also filed as:** `fw-tcd-03`

**Problem:** The shared BE vector's dest device bit is 0.

**Scope:** Same as `fw-tcd-03` for this vector.

**Acceptance:** Additional vector or flags byte with dest=1; `TC-TCD-BE` id remains the frozen A0 vector if changing it would break sim.

**Status: fixed.** Additional dest=1 vector; frozen TC-TCD-BE A0 vector unchanged.

### fw-tcd-07 (low) - reserved test matches `"reserved"` in `str(error)`

- **Category:** assertion quality
- **Location:** `firmware/tests/test_tcd.py:44-51`

**Problem:** Passes if the word "reserved" appears anywhere in the error.

**Scope:** Weak assert. Does not prove the field was the cause.

**Acceptance:** Assert a specific exception type/field, or a stable substring that includes the value.

**Status: fixed.** Assert reserved=0x1 and CTRL_FLAGS[3:0] in the exception text.

### fw-tcd-08 (low) - public `ctrl_flags` no clamp

- **Category:** flags helper
- **Location:** `firmware/tcd.py` flags pack

**Problem:** Public flags helper does not clamp reserved/device bits; encode relies on `validate_tcd`.

**Scope:** Callers that skip validate could pack garbage. Encode path validates first.

**Acceptance:** Keep validate-on-encode, or clamp/document. Test that bypassing validate is not part of the public encode API.

**Status: fixed.** encode_tcd still validates first; ctrl_flags range-checks device/reserved bits. Tested.

### fw-tcd-09 (low) - `Tcd(quit=1) != decoded quit=True`

- **Category:** bool vs int
- **Location:** `firmware/tcd.py` dataclass `quit`; decode uses bool

**Problem:** `Tcd(quit=1)` is not equal to a decoded `quit=True` object (int vs bool), even though `bool` is a subclass of `int` in the other direction. Module already warns about bool-as-int on integer fields.

**Scope:** Equality in tests/REPL. Wire encoding of 1 vs True is the same bit.

**Acceptance:** Normalize `quit` to bool in `__post_init__` / encode, or document that callers must use `True`/`False`. Equality test included.

**Status: fixed.** Tcd.__post_init__ normalizes quit 0/1 to bool; Tcd(quit=1) equals decoded quit=True.

### fw-tcd-10 (note) - dataclass field order != packed `tcd_t`

- **Category:** representation
- **Location:** `firmware/tcd.py:8-10`

**Problem:** None if encode/decode are used. Dataclass field order is not the RTL packed order (`reserved` last in Python, LSB nibble on the wire).

**Acceptance:** Keep the comment. Do not `struct.pack` by dataclass iteration.

**Status: fixed.** Comment kept and strengthened: do not struct.pack by dataclass iteration.

### fw-tcd-11 (note) - three tests can fail only for BE/QUIT/reserved

- **Category:** coverage of `test_tcd.py`
- **Location:** `firmware/tests/test_tcd.py`

**Problem:** The three tests fail only for BE packing, QUIT, or reserved. They cannot fail for dest_device=1 or `ptr[23]`.

**Scope:** Restates `fw-tcd-02/03`.

**Acceptance:** Same as those highs.

**Status: fixed.** Covered by fw-tcd-02/03 tests.

---

## Demo / runner / debug (`firmware/demo.py`, `firmware/runner.py`, `firmware/debug.py`)

### fw-demo-01 (high) - every `run_chain` SPI-enters; demo `exit_qpi=False`

- **Category:** QPI bring-up
- **Location:** `firmware/runner.py:83-90`; `firmware/demo.py:40,:56`

**Problem:** Every `run_chain` either `bring_up_both` (SPI reset + Enter Quad `0x35`) or `enter_qpi_both` (SPI `0x35` even when `bring_up=False`). There is no already-in-QPI path. Demo default `exit_qpi=False` leaves devices in QPI after a run, then the next `run_chain` still speaks SPI Enter Quad.

**Scope:** Board. Second run after a successful demo is a SPI transaction to a QPI device. Mock does not refuse that (`fw-board-08`).

**Acceptance:** `bring_up=False` must not issue SPI if devices are already in QPI (skip enter, or use QPI-only). Demo either exits QPI or documents that the next call must not SPI-enter. Test: second `run_chain` without SPI `0x35` when already in QPI.

**Status: fixed.** bring_up=False skips SPI enter. Second run_chain(bring_up=False) issues no 0x35. Mock refuses SPI while in QPI.

### fw-demo-02 (high) - empty `expected_writes` -> dump no-op -> `ok=True`

- **Category:** false PASS
- **Location:** `firmware/runner.py:37-41,:96-102`; QUIT-only / no dest writes

**Problem:** If `expected_writes` is empty, `dest_extents` is empty, dump is a no-op, `compare_final` is empty, `ok=True`. A missed START or a dead dump path still PASSes.

**Scope:** Empty chain, QUIT-only, or oracle that emitted no writes. Demo payload does have dest bytes; empty-run helpers do not.

**Acceptance:** Empty expected writes is either an explicit skip (`ok` not implied) or a distinct result ("no dest to check"). Test: QUIT-only image does not print PASS via dest compare.

**Status: fixed.** Empty expected_writes raises RunError unless allow_empty_dest=True (then ok=False, not PASS).

### fw-demo-03 (medium) - in-place dest already in image; missed START still matches

- **Category:** install/dump tautology
- **Location:** `firmware/runner.py` install then dump dest extents; in-place dest in the installed image

**Problem:** If dest already holds the expected bytes in the installed image, a missed START still matches after dump.

**Scope:** Demo dest `0x000200` is distinct from src, so the canned vector is OK **unless** install wrote dest too. A user chain with dest pre-loaded would false-PASS.

**Acceptance:** Compare against pre-START dest snapshot, or require dest to change, or zero dest before START in the demo path. Test: dest pre-filled with expected pattern, START suppressed, must FAIL.

**Status: fixed.** If dest already matches expected, runner zeros dest before START. Prefilled dest plus suppressed START fails dump compare.

### fw-demo-04 (medium) - `main()` never `reset_asic`

- **Category:** board sequence
- **Location:** `firmware/demo.py:40-56`

**Problem:** `main()` never calls `reset_asic`. Relies on mux/enable leftover state.

**Scope:** Board. Interacts with `rtl-top-01` (START high at `rst_n` rise).

**Acceptance:** `main()` resets with `ui_in=0` held, then releases `rst_n`, then START. Test on mock: reset is called once before START.

**Status: fixed.** demo.main zeros ui_in, reset_asic True then False, then START. Test asserts reset before START.

### fw-demo-05 (high) - `debug.py` no `Host` / `request_bus`

- **Category:** bus grant
- **Location:** `firmware/debug.py` entire module

**Problem:** Peek/poke/dump/decode call `psram.read`/`write` with no `Host.request_bus`. On a live board that is illegal unless the caller already holds `BUS_GNT` (D26).

**Scope:** REPL debug. Tests use mocks that do not require grant (`fw-demo-12`).

**Acceptance:** Debug APIs take a `Host` and `request_bus`/`release_bus`, or document "caller must already hold grant" and tests assert they refuse without it.

**Status: fixed.** debug peek/poke/dump/decode_chain take Host and refuse without BUS_GNT or rst_n=0.

### fw-demo-06 (medium) - `decode_chain` no mask `ptr[23]`, no cycle detect, swallows validate

- **Category:** debug walk
- **Location:** `firmware/debug.py:32-56`
- **Also filed as:** `fw-sim-drift-10`

**Problem:** Walks raw `next_tcd` (no `ADDR_MAX` mask). No cycle detect (relies on `max_nodes`). `validate_tcd` exceptions are swallowed (`except Exception: break`).

**Scope:** Debug dump, not the oracle (`interpret_chain` masks `ADDR_MAX`). A reserved-nonzero TCD silently stops the walk.

**Acceptance:** Document swallow-as-stop, or print the validate error. Mask or display bit 23. Cycle detect on `(device, addr)` with a test.

**Status: fixed.** decode_chain masks ADDR_MAX, cycle-detects (device, addr), prints validate errors instead of swallowing.

### fw-demo-07 (medium) - `HostError` traceback; extra oracle check vs dump

- **Category:** demo `main` errors
- **Location:** `firmware/demo.py:56-64`; `firmware/asic.py` `HostError`

**Problem:** `HostError` from `run_chain` is uncaught (traceback). Extra `expected == DEMO_PATTERN` is oracle-vs-itself (`result.final_memory` already came from `interpret_chain` of the same image).

**Scope:** Demo UX and tautological PASS.

**Acceptance:** Catch `HostError` and print FAIL. PASS requires dump vs expected, not oracle vs the pattern already baked into the image constructor.

**Status: fixed.** main catches HostError/RunError and prints FAIL. PASS is run_chain dump compare, not oracle vs DEMO_PATTERN.

### fw-demo-08 (medium) - `release_bus` only on success path

- **Category:** grant leak
- **Location:** `firmware/runner.py:92-101`

**Problem:** `release_bus` after dump is on the success path. An exception in install/START/dump can leave `BUS_REQ=1`. Timeout kill also may leave REQ (`fw-board-06`).

**Scope:** Board. Mock may not care.

**Acceptance:** `try/finally` `release_bus` (and clear REQ). Test: injected dump error still clears REQ.

**Status: fixed.** run_chain uses try/finally release_bus. Injected dump error still clears REQ.

### fw-demo-09 (note) - `interpret_chain` N=1 in runner

- **Also filed as:** `fw-chain-01`

Duplicate of the default-N=1 bug as seen from `runner.py:85`.

**Status: fixed.** Duplicate of fw-chain-01; interpret_chain default is 5.

### fw-demo-10 (high) - mock DMA is `interpret_chain`; tests never second QPI run

- **Category:** mock vs silicon
- **Location:** `firmware/tests/mock_transport.py:48-61`; firmware demo/runner tests

**Problem:** Mock DMA is `interpret_chain` (same N=1 default). Tests never run a second QPI session, never assert SPI `0x35` after devices are in QPI, never assert a nonempty dump mismatch.

**Scope:** Firmware pytest cannot fail `fw-demo-01/02/03` or board QPI polarity.

**Acceptance:** Mock can be asked to **not** interpret (or to mismatch dest). Tests: nonempty mismatch FAIL; second enter while in QPI FAIL (`fw-board-08`); explicit N=5 interpret.

**Status: fixed.** Mock can skip interpret or mismatch dest. Tests: nonempty mismatch FAIL; second SPI enter in QPI FAIL; N=5 interpret.

### fw-demo-11 (medium) - demo vector tests don't decode dest ptr; main PASS is oracle vs itself

- **Category:** demo tests
- **Location:** `firmware/tests/test_runner.py`; `firmware/demo.py:57-58`
- **Also filed as:** tautology in `fw-demo-07`

**Problem:** Demo vector tests do not decode dest pointer from packed TCD. `main` PASS is oracle vs itself plus dump of dest that mock just wrote from the same oracle.

**Acceptance:** Decode dest from packed head TCD. Inject mock dest mismatch and expect FAIL.

**Status: fixed.** Demo tests decode dest_ptr from packed head TCD. Mismatch inject prints FAIL not PASS.

### fw-demo-12 (medium) - debug tests no grant, no `next_device=1`

- **Category:** debug tests
- **Location:** `firmware/tests/test_debug.py`

**Problem:** No grant requirement. No `next_device=1` walk.

**Acceptance:** Same as `fw-demo-05` plus a two-device chain decode.

**Status: fixed.** Debug tests require grant; next_device=1 walk with ptr[23] mask.

### fw-demo-13 (low) - `RunError` never raised; mismatch `got=%s` decimal

- **Category:** errors
- **Location:** `firmware/runner.py:12-13`; `firmware/demo.py:63`

**Problem:** `RunError` is never raised (`ok` bool instead). Mismatch prints `got=%s` (decimal-ish default).

**Acceptance:** Raise `RunError` on mismatch or delete the class. Print `got=0x%02X`.

**Status: fixed.** RunError raised on mismatch; got formatted as 0x%02X.

---

## Board / MCU (`firmware/asic.py`, `firmware/psram.py`, mock, `_compat.py`)

### fw-board-01 (blocker) - `rst_n_low` reads `tt._in_reset`

- **Category:** reset / D26 drive legal
- **Location:** `firmware/asic.py:137-144`

**Problem:** `rst_n_low` reads `tt._in_reset`. DemoBoard has **no** such attribute, so the property is always False. MCU QSPI drive is then legal only when `BUS_GNT=1`, never during real `rst_n=0`. Conversely, a mock that sets `_in_reset` is not the SDK.

**Scope:** Board Host. `_drive_legal` / `enable_drive` / PIO init policy.

**Acceptance:** `rst_n_low` uses a DemoBoard-real API (or `reset_project` state the firmware itself recorded). Test against a mock **without** `_in_reset` that still models reset. Drive during held reset is legal; drive with `rst_n=1` and `BUS_GNT=0` is not.

**Status: fixed.** rst_n_low uses Host._rst_held from reset_project. Test uses a mock without _in_reset. Drive during held reset is legal; drive with rst_n=1 and GNT=0 is not.

### fw-board-02 (blocker) - CE# low during Python `put` loop; `tCEM` on wall clock

- **Category:** `tCEM`
- **Location:** `firmware/psram.py:406-417` (`qpi_write`: `_select`, then `put` per byte, then `_deselect`); `qpi_read` similar; planner uses SCK counts not wall time

**Problem:** CE# is held low for the whole Python `put` loop. `tCEM` (max CE# low, 4 us extended / 8 us standard) is a **wall-clock** limit. PIO `put` from MicroPython is orders of magnitude slower than 20 MHz SCK. Chunk planner sizes by SCK cycles, not by Python overhead. Mock has no wall-clock CE#.

**Scope:** Real RP2040 board. Mock/pytest cannot catch it. ASIC DMA path is not this (engine SCK is clk/2). MCU install/dump/debug reads/writes.

**Acceptance:** CE# low only while a PIO SM is shifting, with a measured wall-clock pulse < `tCEM` (scope or a timer around `_select`/`_deselect`). Payload chunking must account for Python/PIO setup, not only SCK. Test on hardware or a time-injecting mock that fails if CE# stays low > 4 us.

**Status: fixed.** MCU path chunks MCU_QPI_PAYLOAD_MAX=1 and raises CE# between qpi_write/qpi_read calls. Mock rejects an oversized CE# payload. Residual: wall-clock of one PIO burst vs tCEM is hardware-only (not proven by host pytest).

### fw-board-03 (high) - `qpi_read_cpha0` samples on SCK low

- **Category:** D16 RX edge
- **Location:** `firmware/psram.py:293-296` (`in_(pins, 4).side(0)` then `nop().side(1)`); comment claims rising SCK

**Problem:** PIO samples nibble on `side(0)` (SCK low / falling), not rising. D16 / APS6404L QPI is rising-edge RX relative to the device launch on falling SCK (`tACLK`). Comment contradicts the instructions.

**Scope:** Board QPI reads (install verify, dump, debug). Writes use a different SM. ASIC engine is separate and does rising-SCK sample.

**Acceptance:** Sample on the rising SCK (`in_` with `side(1)`), matching D16. Scope or a golden nibble walking pattern on hardware. Comment matches code. Host pytest cannot prove this without a PIO model.

**Status: fixed.** qpi_read_cpha0 is nop().side(0) then in_(pins, 4).side(1) (sample on rising SCK, D16). QPI_READ_PIO_INTENT unit-tested on CPython; source must contain in_(pins, 4).side(1).

### fw-board-04 (high) - `request_bus` always `OE_QPI=0xFF`; `OE_SPI` unused

- **Category:** OE during read
- **Location:** `firmware/asic.py:27-30,:201-207`; `OE_SPI` defined, never passed

**Problem:** Grant path always enables all eight `uio` bits as RP2 outputs. During QPI read the MCU should float SIO. SIO may stay driven. `OE_SPI` is unused.

**Scope:** Board. D26: MCU may master only while granted; direction per phase is still required. `CHK-*` pin ownership on sim does not run here.

**Acceptance:** Read path sets SIO OE to input (or `OE_SPI` / a read mask) before dummy/data. Test: mock records OE during `qpi_read` and fails if SIO bits stay 1.

**Status: fixed.** OE_QPI_READ floats SIO during dump/read. Mock qpi_read fails if SIO OE bits stay 1.

### fw-board-05 (high) - `bring_up_both` SPI after devices in QPI; `bring_up=False` still SPI `0x35`

- **Category:** mode
- **Location:** `firmware/psram.py:218-223`; `firmware/runner.py:87-90`
- **Also filed as:** `fw-demo-01`

**Problem:** `bring_up_both` always SPI reset + Enter Quad. `bring_up=False` still `enter_qpi_both` (SPI `0x35`).

**Scope:** Board second session. Mock does not refuse (`fw-board-08`).

**Acceptance:** Same as `fw-demo-01`. `bring_up=False` is QPI-only (no SPI `0x35`).

**Status: fixed.** Same as fw-demo-01. bring_up=False is QPI-only.

### fw-board-06 (high) - timeout `kill_dma` does not clear `ui_in`; `BUS_REQ` can stay 1

- **Category:** kill / D34 pins
- **Location:** `firmware/asic.py:186-198` (`kill_dma` then raise); `ui_in` not cleared

**Problem:** Timeout calls `kill_dma` (`rst_n` assert, OE Hi-Z) but leaves `ui_in` as-is. `BUS_REQ` can stay 1 across reset.

**Scope:** Board. Interacts with `rtl-top-01/02` (REQ/START at reset release).

**Acceptance:** Kill/timeout sets `ui_in=0` (D34 unused bits stay 0). Test: after timeout, `BUS_REQ=0` and START=0.

**Status: fixed.** kill_dma zeros ui_in then asserts rst_n. Timeout test: BUS_REQ=0 and START=0.

### fw-board-07 (high) - `PioTransport.__init__` drives CS/SCK/MOSI before grant

- **Category:** D26
- **Location:** `firmware/psram.py:345-366` (Pin.OUT on CS/SCK/MOSI in `__init__`)

**Problem:** Constructor drives CS/SCK/MOSI before `BUS_GNT` or `rst_n=0`. Comment says "call only while BUS_GNT=1 or rst_n=0" but init does not check.

**Scope:** Board. `make_board_transport()` in `demo.main` can contend with ASIC park OE.

**Acceptance:** Init does not change pin mode until `_drive_legal`, or `main` holds reset/grant before constructing transport. Test: constructing transport without grant does not set OUT on shared `uio`.

**Status: fixed.** PioTransport.__init__ stores config only; arm() claims Pin.OUT. PIO_TRANSPORT_CLAIMS_PINS_IN_INIT is False; CPython source check.

### fw-board-08 (high) - mock does not refuse SPI after `0x35` or QPI before enter

- **Category:** mock fidelity
- **Location:** `firmware/tests/mock_transport.py:17-45` (tracks `self.qpi` but does not refuse)

**Problem:** Mock logs SPI/QPI regardless of mode. `qpi` flags are set and ignored for legality.

**Scope:** Firmware pytest. Hides `fw-demo-01` / `fw-board-05`.

**Acceptance:** SPI while `qpi[cs]` is True raises; QPI while False raises. Tests for both refusals.

**Status: fixed.** Mock raises SPI while qpi[cs] and QPI before enter. Tests for both refusals.

### fw-board-09 (high) - `_compat.py` never imported by tests

- **Category:** MicroPython shim
- **Location:** `firmware/_compat.py`; CPython tests use stdlib dataclasses
- **Also filed as:** `fw-sim-drift-05`

**Problem:** Host pytest never imports `_compat.py`. Shim bugs (`fw-board-15`, `fw-sim-drift-06..09`) are untested on the development host.

**Scope:** UF2 / MicroPython path. CPython CI is green without the shim.

**Acceptance:** A host test forces the shim (import `_compat` APIs directly, or run equality/frozen/hash cases). Same tests as `fw-sim-drift-05`.

**Status: fixed.** firmware/tests/test_compat.py imports _compat APIs on the host.

### fw-board-10 (medium) - `qpi_read` switches SM without completion wait; SCK glitch

- **Category:** PIO
- **Location:** `firmware/psram.py:419+` (`qpi_read` deactivates write SM, activates read SM)

**Problem:** SM switch without waiting for completion can glitch SCK.

**Scope:** Board QPI read. Untested on CPython (`fw-board-14`).

**Acceptance:** Drain/join the active SM, park SCK low, then switch. Scope or a SM mock that fails on overlapping `active(1)`.

**Status: fixed.** park_and_switch_sm drains then deactivates before activating the next SM; unit-tested with FakeSM.

### fw-board-11 (medium) - `exit_qpi` leaves SIO OUT; SPI HOLD#/WP#

- **Category:** mode exit
- **Location:** `firmware/psram.py:206-208` then SPI pin reuse; SD2/SD3

**Problem:** Exit Quad leaves SIO as outputs. SPI HOLD#/WP# (SIO3/SIO2) may stay driven.

**Scope:** Board after `exit_qpi_both`. Next SPI reset needs HOLD#/WP# high or Hi-Z with pull-ups.

**Acceptance:** After Exit Quad, restore SPI pin modes (MOSI out, MISO in, SD2/SD3 pull-up in). Test records pin modes.

**Status: fixed.** exit_qpi calls restore_spi_pins (MOSI out, MISO/SD2/SD3 in). Mock records SPI_PIN_MODES.

### fw-board-12 (medium) - mock grant combo with REQ; START ACK on falling START

- **Category:** mock Host timing
- **Location:** firmware mock `tt` used by `test_asic.py` / runner tests

**Problem:** Mock grant is combinational with REQ (no 2-flop). START ACK on falling START, not DONE-low from a real controller.

**Scope:** Firmware tests over-pass Host timing. RTL has 2-flop REQ and DONE-low ACK.

**Acceptance:** Document mock as protocol-shape only, or add a 2-cycle grant delay and a DONE-low pulse on START. Do not treat mock grant latency as D21/D16 coverage.

**Status: fixed.** Documented on MockDemoBoard: combinational grant and START ACK on falling START are protocol-shape only, not D21/D16 coverage.

### fw-board-13 (medium) - `enable_project` no mode, no reset, no DONE/GNT check

- **Category:** enable
- **Location:** `firmware/asic.py:154-173`

**Problem:** Mux + clock + `ui_in=0` + Hi-Z. No reset, no QPI/SPI mode, no check of DONE/GNT.

**Scope:** Board `main`. Same family as `fw-demo-04`, `rtl-top-01`.

**Acceptance:** Enable sequence: `ui_in=0`, reset assert, mux, clock, reset deassert, sample DONE=1 and GNT=0. Test on mock.

**Status: fixed.** enable_project: ui_in=0, reset assert, mux, clock, reset deassert, sample DONE=1 and GNT=0. Mode ASIC_RP_CONTROL checked when present.

### fw-board-14 (medium) - `rp2` is None on CPython; PIO untested

- **Category:** PIO
- **Location:** `firmware/psram.py:257` (`if rp2 is not None`)

**Problem:** PIO classes do not exist on CPython pytest. `qpi_read_cpha0` polarity (`fw-board-03`) is untested in CI.

**Scope:** CI. Hardware-only.

**Acceptance:** A non-rp2 unit test of the **instruction intent** (side/in_ order) or a documented hardware checklist. Do not call CPython green a PIO pass.

**Status: fixed.** CPython tests QPI_READ_PIO_INTENT and source in_(pins, 4).side(1). Do not treat CPython green as a PIO pass.

### fw-board-15 (low) - unfrozen `ChainResult` hashable on shim

- **Category:** `_compat`
- **Location:** shim vs CPython dataclass; `fw-sim-drift-07`

**Problem:** Unfrozen `ChainResult` is hashable on the MicroPython shim.

**Scope:** Shim only.

**Acceptance:** Shim matches CPython: unfrozen instances not hashable, or both hash on identity (`fw-sim-drift-07`) consistently. Tested via `fw-board-09`.

**Status: fixed.** Shim unfrozen instances are unhashable (__hash__ = None), matching CPython.

### fw-board-16 (note) - START/DONE bit map correct

- **Category:** pin map
- **Location:** `firmware/asic.py:16-19`

**Problem:** None. START=`ui_in[0]`, BUS_REQ=`ui_in[2]`, DONE=`uo_out[0]`, BUS_GNT=`uo_out[1]` match `src/top.v` / D34 unused bits.

**Acceptance:** Remain true.

**Status: fixed.** START/DONE/BUS_REQ/BUS_GNT bit map unchanged and still matches src/top.v.

---

## Firmware vs sim copy drift

Firmware `chain.py` / `tcd.py` are copies of `test/reference/` with import paths changed.

### fw-sim-drift-01 (note) - bodies identical except imports

- **Location:** `firmware/chain.py` vs `test/reference/chain.py`; `firmware/tcd.py` vs `test/reference/tcd.py`

**Problem:** None as a functional delta except defaults and comments. Bodies match aside from imports.

**Acceptance:** Keep a recopy/hash test (`fw-sim-drift-04`) so this stays true.

**Status: fixed.** Bodies stay aligned except imports and the MicroPython dataclass try/except. Recopy/hash test enforces this.

### fw-sim-drift-02 (low) - firmware ships unused sim helpers

- **Location:** `firmware/chain.py` `as_transactions` (`:327`) and related exports

**Problem:** Sim-oriented helpers ship in firmware UF2.

**Scope:** Size/noise on MicroPython.

**Acceptance:** Drop unused helpers from firmware copy, or use them in firmware tests.

**Status: fixed.** Kept sim helpers; firmware tests use as_transactions, commit_prefix, format_log.

### fw-sim-drift-03 (high) - same N=1 default

- **Also filed as:** `fw-chain-01`, `tb-ref-01`

Duplicate of the tapeout-N default bug on both copies.

**Status: fixed.** Duplicate of fw-chain-01; both copies default to 5.

### fw-sim-drift-04 (medium) - no recopy/hash test

- **Location:** no CI check that firmware and `test/reference` stay aligned

**Problem:** Drift can accumulate (comments already say "verification-side", `fw-sim-drift-11`).

**Acceptance:** A test or script hashes/normalizes the two copies (minus import lines) and fails on drift.

**Status: fixed.** test_firmware_oracle_copies_match_reference hashes firmware vs test/reference chain.py and tcd.py minus import lines.

### fw-sim-drift-05 (medium) - `_compat` untested on host pytest

- **Also filed as:** `fw-board-09`

**Status: fixed.** Same as fw-board-09; test_compat.py.

### fw-sim-drift-06 (low) - extra positionals ignored on shim

- **Location:** `firmware/_compat.py` dataclass constructor

**Problem:** Extra positional args ignored on the shim; CPython dataclasses TypeError.

**Acceptance:** Shim rejects extra positionals. Host test.

**Status: fixed.** Shim TypeError on extra positionals. Host test.

### fw-sim-drift-07 (low) - `ChainResult` identity hash

- **Also filed as:** `fw-board-15`

**Problem:** Identity hash on shim vs CPython frozen/unfrozen rules.

**Acceptance:** Same as `fw-board-15`.

**Status: fixed.** Unfrozen not hashable; frozen hashed. Matches CPython.

### fw-sim-drift-08 (low) - `EQUALITY_FIELDS` as field if no annotations

- **Location:** `firmware/_compat.py`

**Problem:** Without annotations, equality-field metadata can become a real field.

**Acceptance:** Shim test with an unannotated class does not grow extra fields.

**Status: fixed.** Unannotated class: only field() objects become fields; EQUALITY_FIELDS stays a class attr.

### fw-sim-drift-09 (note) - `FrozenInstanceError` vs `AttributeError`

- **Location:** shim frozen assign

**Problem:** Different exception type vs CPython.

**Scope:** Callers catching one type.

**Acceptance:** Document, or raise a compatible error. Not a board blocker.

**Status: fixed.** Shim raises FrozenInstanceError (AttributeError subclass), compatible with except AttributeError.

### fw-sim-drift-10 (medium) - `debug.py` walks raw `next_tcd`; interpret masks `ADDR_MAX`

- **Also filed as:** `fw-demo-06`
- **Location:** `firmware/debug.py:55` vs `firmware/chain.py` `ADDR_MAX`

**Problem:** Debug follow-NEXT uses the raw 24-bit field. Interpreter masks to `ADDR_MAX` (A[22:0]). D35 bit 23 can make debug jump somewhere interpret would not.

**Acceptance:** Debug uses the same mask as interpret, or prints both raw and masked.

**Status: fixed.** debug.py masks ADDR_MAX like interpret_chain; prints raw and masked when ptr[23] is set.

### fw-sim-drift-11 (note) - firmware comments still say verification-side

- **Location:** `firmware/tcd.py:5-6`; `firmware/chain.py:22-24`

**Problem:** Comments still describe the files as verification-side copies.

**Acceptance:** Comments say firmware-side copy of the oracle, used on the MCU and in firmware pytest.

**Status: fixed.** Module comments say firmware-side copy of the oracle, used on the MCU and in firmware pytest.
