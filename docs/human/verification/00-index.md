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
| Pin / ownership (`CHK-PIN-*`) | CS mutex, flash CS park, SIO dual-drive, SCK park, addr[23]=0, known SIO | `pass` (`via=pin` when monitor ran) |
| Arbitration / reset (`CHK-ARB-*`, `CHK-RST-*`) | Grant OE quiet, park, busy/grant rules, reset OE/status/internal | `pass` on L1 DMA paths |
| Handshake (`CHK-HS-*`) | txn start, req stable, WDATA/RDATA counts and known, pulse width, opcode | `pass` on DMA paths; off for MCU pass-through negatives |
| Controller (`CHK-CTRL-*`) | req gate/shape, fetch head, data pair, state valid, data_cnt | `pass` on DMA paths; off for MCU pass-through negatives |

Residuals: BUS_GNT-aware CTRL/HS so pass-through suites need not detach those monitors; model-plane dispose contract retained only in `test_qspi_pin_disposition`.

### Pending-item lifecycle (M3 cleanup contract)

Single mechanism in `test/common/lifecycle.py`: `PendingLedger` + `finalize_all`. Severities at open time: fail / diagnostic / ignore. Reasons: dispose, window-clear, monitor-stop, scope-close, reset. Triggers: `dispose.collect`, `BringUp.clear`, `BringUp.stop` / `_stop_previous`. Carryover survives clear; CE# fall uses `close_scope`. Timed wrappers cancel tasks on stop.

Intentional non-fails: Handshake incomplete-window is diagnostic only (no new catalog ID; no `CHK-HS-*-COUNT` reuse); `ControllerMonitor._pending_start` is ignore; do not manufacture cleanup-only `Q-TERM`. Optional `@tb_test` finally-hook deferred (no true cocotb test-end hook). Full contract: [`06-checkers.md`](../../llm/verification/06-checkers.md).

## Toolchain (how to run)

WSL: OSS CAD Suite is already on PATH in interactive shells. Activate the repo-root `dma-venv` and use the hook scripts (not ad-hoc one-liners):

```sh
source test/env.sh
test/scripts/doctor.sh
test/scripts/run_smoke.sh
```

System Python is `python3`. Prefer suite Verilator 5.051 over older `/usr/local` builds. `env.sh` puts a cocotb-friendly `vvp` wrapper first on PATH (raw suite `bin/vvp` breaks the venv). Full contract: [`../../llm/verification/02-platform.md`](../../llm/verification/02-platform.md).

## Current status

| Gate | Status | Note |
|---|---|---|
| Platform scaffold + toolchain hooks | `pass` | `test/env.sh`, wrappers, doctor/run scripts; suite Verilator 5.051 |
| M0 exit (`TC-SMOKE`) | `pass` | L1 same-device length-1 + quit; run via `test/scripts/run_smoke.sh` |
| SCK-accurate PSRAM model + M1 policing | `pass` | Dual models, negatives, shared-bus ownership; Icarus ≡ Verilator on directed set |
| M1 exit (behavioral `Q-*`) | `pass` | `Q-CEM/CPH/MUX/SIO-OWN/RST/SCKIDLE` under `ideal`; residual gaps below |
| M2 exit (reference / scoreboard / directed / `CHK-*`) | `pass` | 2026-08-08 L1 Icarus: smoke; 13 directed + skipped `TC-DEPTH`; reset/bus 11/11; migrated M1 suites |
| M3 exit (delay / launch / RX / lifecycle) | `pass` | 2026-08-10: `nominal` Icarus ≡ Verilator on timing + ownership + launch_rx; cleanup `TC-*`; see residuals |
| `Q-LAUNCH`, `Q-RXEDGE`, `Q-CSP`/`Q-CHD`/`Q-TERM` | `pass` | M3 directed evidence; delay-rerun CEM/CPH/SIO-OWN also green |
| M4-M6 implementation | `todo` | Next: M4 formal `FP-*` |
| SDF run | `blocked` | No compatible final-netlist SDF artifact is available yet |
| `DMA_BUF_DEPTH=1,2,4,8` sweep | `blocked` | `TC-DEPTH` skipped in default directed module; harness must select depths for M5 |
| M7 FPGA hardware validation | `todo` | Not started; requires an FPGA-synthesizable build and carrier-board bring-up with real MCU firmware |
| `T-*` closure | `todo` | STA and demoboard evidence follow M6 |
| CI smoke job | `todo` | Local smoke green; CI job still open |
| Independent `QspiPinMonitor` | live | CE#-framed decode; pin ADDR23/KNOWN dispose `via=pin`; ordinary paths use `dispose_run` |
| Model-plane Z→0 idealization | open | Floating SIO still forced to 0 for the parser; float-as-known remains limited |
| Model-plane pin dispose contract | retained | Only `test_qspi_pin_disposition` uses `assert_model_pin_disposition` |

### M2 / M3 residuals (honest deferrals; do not reopen closed gates)

- CI L1 Icarus smoke job
- Physical `T-HZ` and other `T-*` (STA / demoboard)
- Delayed post-rise `Q-RXEDGE` under non-zero `D_OUT_*` after CE# rise cleanup
- Suites with `ce_monitor=True` + default `reset_truncated=FORBID` may need REVIEW like smoke after `RESET-TRUNCATED` `Q-LAUNCH`
- Margin gate: asserts present legal-baseline fields; write-path may omit CEM/CSP/CHD mins; boundary-pass ≈0 by construction
- Broader `PSRAM_TACLK_NS` / path sweep beyond nominal + documented endpoints is post-M3 if not already covered
- Handshake incomplete-window stays diagnostic (no new ID); `_pending_start` ignore; no cleanup-only `Q-TERM`; `@tb_test` finally deferred
- M4 formal `FP-*`
- M5 random / `COV-*` and `TC-DEPTH` harness wiring
- Optional Z→0 retirement
- Ownership per-case re-split (`TC-OWN-*` stay sub-steps)
- BUS_GNT-aware CTRL/HS checkers (MCU pass-through negatives currently detach those monitors)

**Roadblocks already hit:** nix Icarus vs OSS CAD Suite / cocotb GPI; suite `bin/vvp` `PYTHONHOME` vs `dma-venv`; early PSRAM clk-polling + 5-dummy hack (fixed). Avoid by always using `source test/env.sh` and keeping the PSRAM model SCK-edge driven. See the verification execution plan.

Do not treat delay-annotated simulation, zero-delay gate simulation, or missing future artifacts as physical signoff.
