# Verification

Condensed map of the V1 verification plan. Verbose catalogs live in [`../../llm/verification/`](../../llm/verification/00-index.md). Per [`../../README.md`](../../README.md), human docs stay condensed but must still carry durable requirements; llm should not be the only place a fact exists.

## Reading order

1. [`strategy.md`](strategy.md) - venues, DUT levels, milestones, and evidence boundaries
2. [`signoff.md`](signoff.md) - RTL and shuttle freeze criteria
3. [`../../llm/verification/00-index.md`](../../llm/verification/00-index.md) - full reading order, catalogs, and status vocabulary

## Stable map

| Area | Stable IDs | Primary venue | Detail |
|---|---|---|---|
| QSPI behavior and modeled timing | `Q-*` | Simulation | [`04-timing-in-sim.md`](../../llm/verification/04-timing-in-sim.md) |
| Runtime invariants | `CHK-*` | Simulation | groups below; full catalog [`06-checkers.md`](../../llm/verification/06-checkers.md) |
| Directed tests | `TC-*` | Simulation | [`08-stimulus-and-coverage.md`](../../llm/verification/08-stimulus-and-coverage.md) |
| Functional coverage | `COV-*` | Simulation | [`08-stimulus-and-coverage.md`](../../llm/verification/08-stimulus-and-coverage.md) |
| Control-plane proofs and covers | `FP-*` | Formal | [`07-formal.md`](../../llm/verification/07-formal.md) |
| Firmware-driven hardware regression | `TC-*` subset | FPGA on the carrier board (M7) | [`../../llm/verification/01-strategy.md`](../../llm/verification/01-strategy.md) |
| Nanosecond and physical closure | `T-*` | STA and demoboard | [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md) |

Published IDs keep their meanings even if implementation files change. Required results use `todo`, `wip`, `pass`, `fail`, `blocked`, or `na`, and every `pass` retains its revision, configuration, tool, and artifact.

### Always-on checkers (`CHK-*`, condensed)

Monitors run every applicable L0/L1 test; missing hierarchy → `blocked`, never silent skip. Ordinary dispose prefers pin-axis `dispose_run`.

| Group | What it guards (summary) | M2 status |
|---|---|---|
| Pin / ownership (`CHK-PIN-*`) | CS mutex (incl. CE# X/Z and OE dual-select), flash CS park, SIO dual-drive, SCK park while keeper (CE# X/Z still judged), known SIO. Dual dispose rows for `Q-MUX` / `Q-SIO-OWN` / `Q-SCKIDLE` / `Q-SIO-X`. Selected dual-OE is `Q-SIO-OWN` only; selected dual CE# is `Q-MUX` plus two `Q-PHASE`. `CHK-PIN-ADDR23-ZERO` retired D35 (no live fail, no required `na` row). L0 `pin_monitor=False` → `CHK-PIN-KNOWN` / pin `Q-SIO-X` are `na`. | `pass` (`via=pin` when monitor ran) |
| Arbitration / reset (`CHK-ARB-*`, `CHK-RST-*`) | Grant OE quiet, park, busy/grant rules, reset OE/status/internal | `pass` on L1 DMA paths |
| Handshake (`CHK-HS-*`) | txn start, req stable, WDATA/RDATA counts and known, pulse width, opcode (L0 `TC-QPI-*` enables pin wait-cycle evidence) | `pass` on DMA paths; off for MCU pass-through negatives |
| Controller (`CHK-CTRL-*`) | req gate/shape, fetch head, data pair, state valid; `CHK-CTRL-DATA-CNT` retired (D31) and removed from code | `pass` on DMA paths; off for MCU pass-through negatives |

Residuals: BUS_GNT-aware CTRL/HS so pass-through suites need not detach those monitors; model-plane `Q-SIO-X` evidence (no CHK mapping) in `test_qspi_pin_disposition`.

### Pending-item lifecycle (M3 cleanup contract)

Single mechanism in `test/common/lifecycle.py`: `PendingLedger` + `finalize_all`. Severities at open time: fail / diagnostic / ignore. Reasons: dispose, window-clear, monitor-stop, scope-close, reset. Triggers: `dispose.collect`, `BringUp.clear`, `BringUp.stop` / `_stop_previous`. Carryover survives clear; CE# fall uses `close_scope`; delayed device-plane CE# commit (`ce-rise-committed`) may `close_scope` again for race-window opens. Timed wrappers cancel tasks on stop.

Dispose of an open handshake txn with `busy` still high fails `CHK-HS-RDATA-COUNT` / `CHK-HS-WDATA-COUNT` (expected vs observed beats). Reset abort of that txn is `RESET-TRUNCATED` with partial counts, not an ordinary fail. An accepted START with no following head fetch fails `CHK-CTRL-FETCH-HEAD` (bounded START-to-fetch); reset abort of that wait is `RESET-TRUNCATED`. Do not manufacture cleanup-only `Q-TERM`. Optional `@tb_test` finally-hook deferred (no true cocotb test-end hook). Full contract: [`06-checkers.md`](../../llm/verification/06-checkers.md).

**`reset_truncated` rule:** any `dispose_run` window with a forced `rst_n=0` interval and a live CE monitor (`ce_monitor=True`) must pass `reset_truncated=REVIEW` or `REQUIRE` - never rely on default `FORBID`. Do not loosen where reset was not asserted. `RESET-TRUNCATED` means a timing observation explained by reset OE/state convergence while `rst_n==0` (not an ordinary `Q-*` fail); after reset release the same pattern is an ordinary fail. `Q-LAUNCH` (`Q-LAUNCH`: driven SIO/OE changes only while SCK is low, with modeled setup/hold) is the usual ID during that window, and only while ASIC drives SCK (`asic_sck_oe==1`) so grant/park OE clear is not a launch event.

## Toolchain (how to run)

WSL: OSS CAD Suite is already on PATH in interactive shells. Activate the repo-root `dma-venv` and use the hook scripts (not ad-hoc one-liners):

```sh
source test/env.sh
test/scripts/doctor.sh
test/scripts/run_smoke.sh
test/scripts/run_gl.sh
```

System Python is `python3`. Prefer suite Verilator 5.051 over older `/usr/local` builds. `env.sh` puts a cocotb-friendly `vvp` wrapper first on PATH (raw suite `bin/vvp` breaks the venv). Shared SV harness: `tb_uio_bus.svh` (physical Hi-Z SIO/SCK; CS pull-ups 0/6/7); `tb_top` default `DMA_BUF_DEPTH=5`; `tb_gl` `$error` if depth != 5; L0 `WAVES_DISABLE` skips dumps. Full contract: [`../../llm/verification/02-platform.md`](../../llm/verification/02-platform.md).

GitHub Actions: `test.yaml` (L1 Icarus smoke), `timing.yaml` (`bash test/scripts/run_timing.sh` so a missing git execute bit is not a 126), `gds.yaml` (`tt-gds-action@ttihp26b`; `info.yaml` lists `top.v` first because yowasp Yosys port-checks only that file). The CI smoke job stays `todo` until those jobs are green.

## Current status

| Gate | Status | Note |
|---|---|---|
| Platform scaffold + toolchain hooks | `pass` | `test/env.sh`, wrappers, doctor/run scripts; suite Verilator 5.051 |
| M0 exit (`TC-SMOKE`) | `pass` | L1 same-device length-1 + quit; run via `test/scripts/run_smoke.sh` |
| SCK-accurate PSRAM model + M1 policing | `pass` | Dual models, negatives, shared-bus ownership; Icarus ≡ Verilator on directed set |
| M1 exit (behavioral `Q-*`) | `pass` | `Q-CEM/CPH/MUX/SIO-OWN/RST/SCKIDLE` under `TIMING_PROFILE=ideal` (zero TB placeholders only); M3 timing rows closed separately |
| M2 exit (reference / scoreboard / directed / `CHK-*`) | `pass` | 2026-08-08 L1 Icarus: smoke; 13 directed + skipped `TC-DEPTH`; reset/bus 11/11; migrated M1 suites |
| M3 exit (delay / launch / RX / lifecycle) | `pass` | 2026-08-10: `nominal` Icarus ≡ Verilator on timing + ownership + launch_rx; cleanup `TC-*`; see residuals |
| `Q-LAUNCH`, `Q-RXEDGE`, `Q-CSP`/`Q-CHD`/`Q-TERM` | `pass` | M3 directed evidence; delay-rerun CEM/CPH/SIO-OWN also green |
| M4 formal (`FP-*`) | `todo` | Deferred (D33); not a V1 freeze gate; stubs under `test/formal/` untouched - do not claim pass |
| M5 random / `COV-*` / depth sweep | `pass` | Regenerated 2026-08-25: `test/runs/m5_coverage_closure.json` `closed=true` (`missing={}`; depths 1..8; reviewer `tb-closure-2026-08-25`). `TC-DEPTH` Icarus 15/15 per N=1..8 (`run_depth_sweep-20260825-190207.log`). Firmware oracle-hash drift remains a firmware follow-up. |
| L2 functional infra | `wip` | `tests.test_gate_level` (`TC-GL-*`) + `test/scripts/run_gl.sh`; designated 189-DFF N=5 netlist SHA256 `9a769ad4bcc09d7cff699e8f178acab4fb5b7228e242cfdf7d027ed2274beb7a`; SDF blocked; `make verilator_x` isolates `x-unique` and is not four-state (`run_verilator_x-20260825-191818.log`); M6 open |
| SDF run | `blocked` | No compatible final-netlist SDF artifact; a zero-delay GL run is not an SDF pass |
| `TC-DEPTH` (`1..8`) | `pass` | 2026-08-25: Icarus 15/15 directed suite per compile-time `DMA_BUF_DEPTH` via `make depth` / `run_depth_sweep.sh` (`run_depth_sweep-20260825-190207.log`) |
| `DMA_BUF_DEPTH` elaboration `1..8` | `pass` | Tapeout and default sim/Make depth **N=5**; sweep N=1..8 green for directed suite |
| M7 FPGA hardware validation | `todo` | Not started; requires an FPGA-synthesizable build and carrier-board bring-up with real MCU firmware |
| `T-*` closure | `todo` | STA and demoboard evidence follow M6 |
| CI smoke job | `todo` | Local smoke green; CI job still open |
| Independent `QspiPinMonitor` | live | CE#-framed decode; pin KNOWN dispose `via=pin` with `Q-SIO-X` twin row (`ADDR23` retired D35); L0 default `pin_monitor=False` leaves those IDs `na`; ordinary paths use `dispose_run` |
| Physical SIO/SCK Z | pass | Parser sees Hi-Z; `Q-SIO-X` is host-driven only; no Z-to-0 overlay |
| Model-plane `Q-SIO-X` | retained | `test_qspi_pin_disposition` asserts model `Q-SIO-X` only; does not map onto `CHK-PIN-KNOWN` |

### M2 / M3 residuals (honest deferrals; do not reopen closed gates)

- CI L1 Icarus smoke job
- Physical `T-HZ` and other `T-*` (STA / demoboard)
- Closed (2026-08-10 residual wave): delayed post-rise `Q-RXEDGE` under non-zero `D_OUT_*` after CE# rise cleanup - `ce-rise-committed` + `TC-RXEDGE-RACE-DEVICE-PLANE`
- L1 `Q-RXEDGE` (`Q-RXEDGE`: each launched read nibble captured on the following rising SCK) uses the armed rising SCK as capture (no `rdata_valid` alias); write-only / no timed stream is `na`/`blocked`, not pass
- Margin gate: asserts present legal-baseline fields; write-path may omit CEM/CSP/CHD mins; boundary-pass ≈0 by construction
- Broader `PSRAM_TACLK_NS` / path sweep beyond nominal + documented endpoints is post-M3 if not already covered
- Open handshake txn at dispose fails RDATA/WDATA counts; `_pending_start` fails `CHK-CTRL-FETCH-HEAD`; reset abort of either is `RESET-TRUNCATED`; no cleanup-only `Q-TERM`; `@tb_test` finally deferred
- M4 formal `FP-*` (deferred (D33); do not claim pass)
- L2 X-on-float allowance is reachable on the physical bus; not cited as L2 X coverage without a directed L2 test
- Ownership per-case re-split (`TC-OWN-*` stay sub-steps)
- BUS_GNT-aware CTRL/HS checkers (MCU pass-through negatives currently detach those monitors)

**Roadblocks already hit:** nix Icarus vs OSS CAD Suite / cocotb GPI; suite `bin/vvp` `PYTHONHOME` vs `dma-venv`; early PSRAM clk-polling + 5-dummy hack (fixed). Avoid by always using `source test/env.sh` and keeping the PSRAM model SCK-edge driven. See the verification execution plan.

Do not treat delay-annotated simulation, zero-delay gate simulation, or missing future artifacts as physical signoff.

## Planned housekeeping

Not a shuttle freeze gate. Full text: [`../../llm/verification/02-platform.md`](../../llm/verification/02-platform.md). Firmware twin: [`../architecture/firmware.md`](../architecture/firmware.md). Checklist: [`../roadmap.md`](../roadmap.md).

1. **Centralize constants.** Architecture numbers live in `test/reference/constants.py` (mechanical twin of `firmware/constants.py`). Sim-only shared numbers (DONE mask, timeouts, `FILL`, FSM encodings) live in `test/common/constants.py`. `Q-*` IDs are simulation-provable QSPI protocol and edge checks; `CHK-*` IDs are always-on cocotb runtime monitors; `COV-*` IDs are functional coverage points and stay in their catalog owner. Firmware still must not import `test/` (D30).
2. **Complete function comments, plus a repo commenting standard.** Review verification docs and add a complete comment on every testbench function. Write the commenting standard in that same change; later RTL and scripts follow it.
3. **Centralize testbench interaction and make output easier to read.** One shared `REPRO` / run-log helper so tests stop copying `_repro()` and formatting `dut._log` themselves. Passing dispose output is a compact summary; a fail still prints every `CHK-*` / `Q-*` ID. Checker pass/fail semantics stay the same.
