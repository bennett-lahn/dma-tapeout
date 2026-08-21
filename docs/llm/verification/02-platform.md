# Verification Platform

## Scope and source of truth

The platform is a cocotb 2.0.1 environment under repository-root `test/`, compatible with the Tiny Tapeout make flow. Icarus is the primary simulator. Verilator **5.051** (OSS CAD Suite as installed on this machine) is the secondary fast-regression simulator. SymbiYosys uses native WSL executables from the same suite.

This document specifies the implementation contract and the durable agent toolpath (`test/env.sh`, `test/scripts/*.sh`). Do not invent PowerShell one-liners or `/tmp` log hacks when these entry points exist.

Template references:

- `../../../ttihp-verilog-template/test/Makefile`
- `../../../ttihp-verilog-template/test/requirements.txt`
- `../../../ttihp-verilog-template/test/tb.v`
- `../../../ttihp-verilog-template/test/test.py`

The template establishes `SIM=icarus`, `GATES=yes`, `TOPLEVEL=tb`, cocotb makefiles, separate RTL/gate build directories, IHP cell-model sources, and the current Python pins. This project extends that shape for L0/L1/L2 without changing Tiny Tapeout's gate-level entry point.

## Planned `test/` layout

```text
test/
  Makefile
  env.sh                 # sourceable: OSS CAD on PATH + dma-venv + PYTHON=python3
  requirements.txt
  conftest.py
  scripts/
    doctor.sh            # toolchain health (fails if venv/cocotb/suite broken)
    run_smoke.sh         # M0 L1 Icarus smoke
    run_test.sh          # pass-through make test LEVEL=... SIM=...
    run_gl.sh            # L2: copy N=5 unpowered nl view, GATES=yes subset
    wrappers/
      iverilog           # suite libexec/iverilog; shebang -> wrappers/vvp
      vvp                # cocotb-friendly suite libexec/vvp (on PATH via env.sh)
      common.sh
  tb/
    tb_engine.sv
    tb_top.sv
    tb_gl.sv
  tests/
    __init__.py
    test_smoke.py
    test_qspi.py
    test_qspi_negative.py
    test_qspi_ownership.py
    test_qspi_timing.py
    test_qspi_timing_delay.py
    test_qspi_timing_launch_rx.py
    test_qspi_cleanup.py
    test_qspi_reset_protocol.py
    test_qspi_pin_disposition.py
    test_dma_directed.py
    test_dma_random.py
    test_reset_and_bus.py
    test_reference_*.py
    test_gate_level.py
  common/
    __init__.py
    bringup.py
    dispose.py
    lifecycle.py
    directed.py
    engine_bfm.py
    config.py
    constants.py
    clocks.py
    host.py
    seeds.py
    artifacts.py
  models/
    __init__.py
    psram.py
    psram_timing.py
  reference/
    __init__.py
    constants.py
    tcd.py
    chain.py
    generator.py
    scoreboard.py
  monitors/
    __init__.py
    qspi.py
    arbitration.py
    handshake.py
    timing.py
  formal/
    engine/
    integration/
    bind/
```

Responsibilities:

- `tb/` contains only HDL wrappers, shared-bus resolution, dump setup, and visibility needed by a DUT level.
- `tests/` contains cocotb test entry points. Test names carry `TC-*` IDs in docstrings or metadata, not in Python identifiers. L0 CE#/SCK idle self-check lives inside `bring_up_engine` (former `test_engine_attach` deleted).
- `common/` contains host actions, clock/reset helpers, shared bring-up / dispose / directed plumbing, the pending-item lifecycle (`lifecycle.py`: `PendingLedger` / `finalize_all`), the blessed write BFM, run configuration, sim-only shared constants (`constants.py`), deterministic random support, and artifact naming. Cleanup contract detail: `06-checkers.md`.
- `models/` contains the two independent APS6404L instances and delay layer.
- `reference/` contains architecture constants (`constants.py`; mechanical twin of `firmware/constants.py`), the pure-Python TCD encoder/decoder, chain interpreter, the modularized legal-chain generator class described in `08-stimulus-and-coverage.md`, and scoreboards. None of these may call DUT internals, and the generator is kept in its own module (`generator.py`) separate from the golden interpreter (`chain.py`) it depends on, since generating stimulus and interpreting it are distinct responsibilities that must stay separately testable.
- `monitors/` contains passive protocol decoders and always-on `CHK-*` checks.
- `formal/` contains `.sby` jobs, harnesses, and bind files. It shares RTL sources and constants conceptually, but does not import cocotb code.

Do not make one monolithic `test.py`. Model, monitor, reference, and stimulus code must remain independently testable and reusable across levels.

## Planned housekeeping

Not a shuttle freeze gate. Condensed: [`../../human/roadmap.md`](../../human/roadmap.md), [`../../human/verification/00-index.md`](../../human/verification/00-index.md). Firmware twin: [`../12-firmware.md`](../12-firmware.md).

### Centralize constants

Three leaf modules (no imports of `tcd` / `psram` / models / monitors / cocotb). Firmware still must not import `test/` (D30). Shared numeric truth stays a **mechanical copy** between `firmware/constants.py` and `test/reference/constants.py`, not a cross-import. When a shared architecture number changes, edit both files. Python remains a commented copy of RTL (`src/types.svh`); do not parse SystemVerilog.

| File | Role |
|---|---|
| `firmware/constants.py` | MCU + architecture numbers used in 2+ firmware modules |
| `test/reference/constants.py` | Mechanical twin of the overlapping architecture subset (TCD, opcodes, dummy/nibble counts, head, buffer depth) |
| `test/common/constants.py` | Sim-only shared numbers (DONE **mask**, timeouts, `FILL`, dispose strings, RTL FSM encodings, host pin indices) |

Pin-monitor independence (`05-reference-model.md`) is about not reading the PSRAM model's access log. Sharing opcode and dummy-cycle numbers from `test/reference/constants.py` is allowed; `test/monitors/qspi.py` must still not import `test/models/psram.py`.

`test/common/config.py` stays the run-configuration parser (`LEVEL`, `SIM`, `SEED`, `DMA_BUF_DEPTH`, `TIMING_PROFILE`). It imports tapeout depth from `test/reference/constants.py` instead of hard-coding `5`.

Local values that are truly one-test (a directed negative opcode such as `0x38`, a single fixture address, the independent `MANDATORY_BYTES` restatement of `TC-TCD-BE`) stay local. `CHK-*` / most `Q-*` / `COV-*` ID strings stay in their catalog owner. `Q-*` IDs are simulation-provable QSPI protocol and edge checks; `CHK-*` IDs are always-on cocotb runtime monitors; `COV-*` IDs are functional coverage points.

Firmware `asic.py` `DONE_BIT` is a `uo_out` **index**. Test `DONE_MASK` (`0x1`) is a **mask**. Do not unify them with `test_qspi_negative.py` `_BUS_GNT_BIT` (UIO **index**).

### Complete function comments and a repo commenting standard

Planned as one change, applied first to `test/` (and the matching firmware work in `../12-firmware.md`):

1. **Write the standard** in docs (human condensed in [`../../human/roadmap.md`](../../human/roadmap.md); llm examples here and in the firmware doc). Cover Python first. SystemVerilog (`src/`, `test/tb/`) and shell (`test/scripts/`) follow the same intent on later edits: every function or module entry point states purpose, inputs, outputs, and side effects.
2. **Apply it to testbench source:** every function in `test/common/`, `test/models/`, `test/reference/`, `test/monitors/`, and `test/tests/` gets a complete comment. Cocotb tests keep `TC-*` IDs in the docstring or metadata (IDs name required behavior, not Python identifiers).
3. **Review and update verification docs** so public helpers (bring-up, host, dispose, lifecycle, BFM, reference pack/unpack/`interpret_chain`, scoreboard, monitors) have complete descriptions matching the source comments. Catalogs (`TC-*`, `CHK-*`, `Q-*`, `COV-*`) stay ID-owned; function comments must not invent a parallel ID scheme.

Do not treat a one-line name restatement as complete. A complete comment says what the function guarantees, what it refuses, and which frozen rule or verification ID it implements when that is not obvious from the name.

### Centralize testbench interaction and make output easier to read

Planned as one change in `test/` (not a shuttle freeze gate). Firmware REPL / `print` paths are out of scope.

Today each cocotb module repeats the same conversation with the bench: a local `_repro()` string (often `make` in one file and `run_test.sh` in another), `dut._log.info` of that string, then `bring_up_*` / host pulses / `dispose_run`. `dispose_run` `_log_report` then prints one `DISPOSE test=... id=... result=... count=...` INFO line per catalog ID (`CHK-*` always-on monitors and `Q-*` simulation-provable QSPI protocol/edge checks). Passing tests often log another pipe-joined `report.summary()` on the same stream as Make banners, toolchain versions, and Icarus `sorry:` notes.

Planned:

1. **One reproduction helper** in `test/common/` that builds the copy-paste `REPRO:` line from `parse_run_config()` plus module / `TEST_FILTER`. Tests stop copying the template. Keep both `make test ...` and `test/scripts/run_test.sh ...` forms if both remain supported; generate them in one place.
2. **One run-log helper** (thin, next to bring-up / dispose, not a new monitor) owns the human-facing lines: config and `SEED` at start, compact pass banner at end. Tests still call `dispose_run` for the pass/fail contract; they stop formatting the narrative themselves.
3. **Readable pass output.** A clean pass collapses per-ID `DISPOSE` lines into one compact summary (pass / `na` / `blocked` counts, and any non-pass IDs). Per-ID lines remain on fail, when any row is `fail` / `blocked`, or when a verbosity override is set. The contract "every applicable ID is disposed, never a silent skip" stays; what changes is how a clean pass is printed.
4. **Do not** fold models, pin monitors, or the scoreboard into the logger. **Do not** change dispose semantics (`expect_fail`, `RESET-TRUNCATED` review/require, pin vs model `via=`). **Do not** treat quieter Make/Icarus compiler noise as this change; `run.log` isolation already exists.

Local per-test messages that name a unique directed fault (for example which SIO bit was X) may stay in the test. Anything every suite repeats (`REPRO`, SEED/config, "passed: N transactions", the all-pass ID dump) moves.

## DUT-level selection

The stable command-line selector is:

| Selector | Verification level | HDL top | DUT |
|---|---|---|---|
| `LEVEL=engine` | L0 | `tb_engine` | `qspi_engine` |
| `LEVEL=top` | L1 | `tb_top` | `tt_um_lahnb_sgdma` |
| `LEVEL=gl` | L2 | `tb_gl` | gate-level `tt_um_lahnb_sgdma` |

Default: `LEVEL=top`.

`LEVEL=gl` implies `GATES=yes`. Supplying `GATES=yes` with `LEVEL=top` selects the same L2 flow for Tiny Tapeout compatibility. Other conflicting combinations must fail with a clear Make error instead of silently selecting a DUT.

RTL source order must place `src/types.svh` before modules that import its packages, then compile `qspi_engine.sv`, `sys_controller.sv`, and `top.v`. L0 compiles only the package and engine sources it needs. L1 compiles the integrated source set. L2 uses the final gate-level netlist and IHP models instead of RTL.

## Simulator matrix

| Simulator | L0 | L1 | L2 | Assigned role |
|---|---|---|---|---|
| Icarus | Required | Required | Required | Primary correctness, TT-compatible flow, four-state behavior, gate-level sign-off subset |
| Verilator 5.051 (OSS CAD Suite) | Required | Required | Optional diagnostic only | Fast directed and constrained-random RTL regression, X experiments |

Both simulators must pass the M1 directed protocol set and the assigned M2 behavioral set. High-volume M5 random tests may run primarily on Verilator, but every failure must reproduce or be classified on Icarus before closure.

### Known tool differences to isolate

- Icarus does not provide the concurrent SVA flow required for this plan. Runtime `CHK-*` checks live in cocotb; SVA belongs to SymbiYosys bind files.
- Keep synthesizable DUT sources separate from simulator-specific testbench code. Avoid depending on unsupported class, interface, or advanced assertion features in Icarus wrappers.
- Verilator requires timing support for HDL `#` delays and cocotb clocks. The Makefile must supply the cocotb-supported timing arguments for the installed OSS CAD Suite Verilator (5.051). Do not prefer a stale `/usr/local` Verilator 5.034 when the suite binary is available.
- Verilator X behavior is configuration-dependent and is not equivalent to Icarus four-state propagation. X-focused runs explicitly record `--x-assign` and `--x-initial` settings and are interpreted under `09-gate-level-and-x.md`.
- Waveform formats and hierarchy names differ. Tests and scoreboards must not use waveform format or generated hierarchy names as functional input.
- A simulator-specific pass is insufficient when the matrix marks both simulators required. Reduce and document divergences rather than adding silent conditional expectations.

### Compile-error diagnostics

Whenever a build fails to compile or elaborate on either simulator, rerun the exact same failing configuration under Verilator specifically before triage, even if Icarus was the simulator that originally failed. Verilator's elaboration diagnostics (width mismatches, undeclared or unconnected signals, parameter errors, and unsupported constructs) are typically more specific than Icarus's and shorten root-cause time. Retain the full compile log from both simulators for the failing configuration, not only the one that first reported the error.

If Icarus and Verilator disagree, whether one compiles a configuration the other rejects, or both compile but disagree on a required test result, report this explicitly as a tool-divergence finding rather than silently trusting whichever simulator happened to pass. Record the exact diverging construct or behavior, both tool versions, and classify the divergence per the failure-handling contract in `01-strategy.md` before either result is used as evidence.

## Makefile interface

### Agent / human entry (preferred)

From the repository root in WSL bash (interactive shells already have OSS CAD Suite on PATH via `~/.bashrc`):

```sh
source test/env.sh
test/scripts/doctor.sh
test/scripts/run_smoke.sh
test/scripts/run_test.sh LEVEL=engine SIM=icarus SEED=17 TEST_FILTER=qspi
test/scripts/run_gl.sh
```

`test/env.sh` activates repo-root `dma-venv`, exports `PYTHON=python3`, unsets `PYTHONHOME` if the suite set it (required so cocotb uses the venv interpreter, not suite Python), and warns if `iverilog` is missing or resolves under `~/.nix-profile`. Non-interactive agent shells that skip `.bashrc` may get a one-shot soft source of `~/tools/oss-cad-suite/environment` when `iverilog` is absent. Hook scripts log under `test/runs/`, not `/tmp`.

Do **not** use a Nix Icarus/`vvp` plus `LD_LIBRARY_PATH` libexpat shim when suite tools are on PATH. Suite Icarus is authoritative; `test/env.sh` prepends `test/scripts/wrappers/vvp`, which runs suite `libexec/vvp` without the stock `bin/vvp` `PYTHONHOME` override (that override breaks cocotb + `dma-venv`).

Equivalent Make targets (after `source test/env.sh` and `cd test`):

### Primary targets

| Target | Purpose |
|---|---|
| `make doctor` | Toolchain health check (suite tools, python3, venv, cocotb) |
| `make test` | Run the selected level, simulator, and test filter once |
| `make smoke` | Run the M0 L1 same-device smoke with a fixed default seed |
| `make directed` | Run M2 directed modules; default `TEST_FILTER` enumerates the 13 `tests.test_dma_directed` cases and excludes skipped `dma_buf_depth_sweep` |
| `make random` | Run constrained-random tests for one seed |
| `make regression` | Run the configured seed list and simulator matrix |
| `make formal` | Run the SymbiYosys jobs (`FP-*`; D33; not a V1 freeze gate) |
| `make waves` | Open or print the path to the waveform from a selected prior run |
| `make gl_test` | L2 Icarus directed subset at flattened `DMA_BUF_DEPTH=5` / `TIMING_PROFILE=ideal` (zero TB placeholders) |
| `make clean` | Remove generated simulation build and run artifacts only |

`.DEFAULT_GOAL` is `test`, so bare `make` and Tiny Tapeout `GATES=yes make` run the test recipe rather than an earlier helper such as `_ensure_run_dir`. `GATES=yes make` (TT `gl_test` action) selects `LEVEL=gl` and the L2 module `tests.test_gate_level`, not full random. After every `test` target, `RUN_DIR/results.xml` is copied to `test/results.xml` and `dump.fst` / `dump.vcd` to `test/tb.fst` / `test/tb.vcd` so the official action can grep and upload those paths.

### Stable variables

| Variable | Default | Meaning |
|---|---|---|
| `LEVEL` | `top` | `engine`, `top`, or `gl` |
| `SIM` | `icarus` | cocotb simulator selector |
| `GATES` | unset | TT-compatible gate-level selector; `yes` implies `LEVEL=gl` |
| `SEED` | `1` | unsigned test seed printed at start and failure |
| `TEST_FILTER` | empty | cocotb test-name regular expression |
| `DMA_BUF_DEPTH` | `5` | L1 compile-time override (`-G` / `-Ptb_top.DMA_BUF_DEPTH=N`; any integer `1..DMA_BUF_DEPTH_MAX`). L2 must be 5 to match the flattened netlist; the Makefile does not pass `-Ptb_gl.DMA_BUF_DEPTH` (that cannot resynthesize the gate DUT) |
| `TIMING_PROFILE` | `ideal` | named timing parameter set |
| `WAVES` | `auto` | `auto`, `always`, or `never` |
| `SDF` | unset | optional SDF path for L2 |
| `NETLIST` | `gate_level_netlist.v` | L2 netlist path |
| `RUN_DIR` | generated | per-configuration output directory |

Examples (after `source test/env.sh`):

```sh
test/scripts/run_test.sh LEVEL=engine SIM=icarus TEST_FILTER=qspi SEED=17
test/scripts/run_test.sh LEVEL=top SIM=verilator SEED=4231 DMA_BUF_DEPTH=5
cd test && make gl_test
cd test && make test LEVEL=gl SIM=icarus GATES=yes NETLIST=gate_level_netlist.v
cd test && make random LEVEL=top SIM=verilator SEED=4231 TIMING_PROFILE=nominal
```

`DMA_BUF_DEPTH` is a module parameter on `tt_um_lahnb_sgdma` / `sys_controller` (V1 tapeout and default sim: **5**). Package `DMA_BUF_DEPTH_MAX` in `src/types.svh` sizes `qpi_byte_len_t` / cycle counters for elaboration `1..8`. At L1 the Makefile passes `-GDMA_BUF_DEPTH=$(DMA_BUF_DEPTH)` (or `-Ptb_top.DMA_BUF_DEPTH`) and rejects values outside `1..DMA_BUF_DEPTH_MAX`. At L2 the gate instance is flattened at N=5; Python still reads `DMA_BUF_DEPTH=5` for the scoreboard.

The Makefile maps `TEST_FILTER` to cocotb 2.x `COCOTB_TEST_FILTER` and lists modules through `COCOTB_TEST_MODULES`. Do not use removed legacy environment names.

`make directed` sets `COCOTB_TEST_MODULES=tests.test_smoke,tests.test_qspi,tests.test_dma_directed,tests.test_reset_and_bus` and, unless the caller overrides `TEST_FILTER`, applies a quoted regex of the 13 descriptor/data directed function names (not a bare substring `directed`, which would dishonestly miss or mis-select cases). `TC-DEPTH` / `dma_buf_depth_sweep` and `COV-DEPTH*` remain deferred until M5 harness wiring lands - do not claim those IDs pass yet. Ownership negatives use `TEST_FILTER=ownership_shared_bus_negatives` only; `TC-OWN-*` are sub-steps inside that one test.

## Build and artifact isolation

Each run gets a directory keyed by level, simulator, gate mode, buffer depth, timing profile, and seed. A suitable logical form is:

```text
test/runs/<level>/<sim>/n<depth>/<timing>/seed-<seed>/
```

The directory contains:

- compile and run log
- normalized run configuration
- test result XML
- failure summary
- waveform when enabled
- randomized stimulus manifest
- scoreboard transaction trace on mismatch

Simulator build products must also be isolated by compile-time configuration. A Verilator build for `DMA_BUF_DEPTH=4` must never be reused for depth 1. Generated files stay outside source packages.

## Seed and reproduction contract

`SEED` is the single user-facing random seed.

At test start:

1. parse `SEED` as an unsigned integer,
2. construct local `random.Random(SEED)` instances or deterministically derived child streams,
3. record the seed and all configuration dimensions,
4. avoid module-global random state and wall-clock seeding.

The same seed plus the same RTL revision, simulator/version, level, buffer depth, and timing profile must generate the same descriptors, memory initialization, host-request schedule, and reset schedule.

Every failure prints one copy-paste reproduction command. For example:

```text
REPRO: source <repo>/test/env.sh && cd <repo>/test && make test LEVEL=top SIM=verilator TEST_FILTER=test_dma_random SEED=4231 DMA_BUF_DEPTH=5 TIMING_PROFILE=nominal
REPRO: source <repo>/test/env.sh && <repo>/test/scripts/run_test.sh LEVEL=top SIM=verilator TEST_FILTER=test_dma_random SEED=4231 DMA_BUF_DEPTH=5 TIMING_PROFILE=nominal
```

Pytest collection order is not a source of randomness. When one pytest process executes multiple random cases, each case derives a child seed from the base seed and stable test identity, and reports both values.

## Waveform policy

Waveforms are diagnostic artifacts, not pass evidence by themselves.

- `WAVES=auto` is the default. Generate or preserve a waveform for a failing run and omit it for a passing high-volume random run.
- `WAVES=always` is used for directed timing work, reduced reproducers, and L2.
- `WAVES=never` is allowed only where logs and transaction traces remain sufficient.
- CI uploads waveforms, logs, configuration, and transaction traces for failed jobs.
- The failure log reports the waveform path and first failing ID.
- Prefer FST with Icarus, consistent with the TT template. Verilator may use its supported trace format.

If the simulator cannot enable tracing after a failure, `auto` may execute a deterministic second run with the same seed and `WAVES=always`. The original failure remains authoritative, and the rerun result is recorded separately.

## Toolchain contract

### HDL, formal, and waveform tools

Interactive WSL shells on the development machine already put OSS CAD Suite on PATH (typically `source ~/tools/oss-cad-suite/environment` from `~/.bashrc`). Agents and scripts must treat that suite as the authoritative binary bundle for:

| Tool | Recorded version (this machine) |
|---|---|
| Icarus Verilog | 14.0 (devel) `(s20260301-328-geda9fdcd1-dirty)` |
| Verilator | **5.051** devel `rev v5.050-116-g9cddf4693 (mod)` |
| Yosys | 0.67+122 `(git sha1 9bc23d383-dirty)` |
| SymbiYosys (sby) | v0.67-4-gfea6e46 |
| Bitwuzla | 0.9.1 |
| Yices | 2.7.0 |
| Z3 | 4.15.5 |
| GTKWave | suite-provided |

Do not commit extracted binaries or user-specific absolute paths. Formal jobs invoke the native WSL `yosys`, `sby`, and solver executables from that environment.

**Verilator:** use the OSS CAD Suite 5.051 binary. Do not prefer an older `/usr/local` Verilator 5.034 when the suite is on PATH. If a future suite upgrade changes the Verilator version, record and qualify it before using results as required evidence (same bar as any other tool bump).

**Icarus / vvp:** suite Icarus is authoritative. `test/env.sh` prepends `test/scripts/wrappers/{iverilog,vvp}` so (1) compile bakes the cocotb-friendly `vvp` into `sim.vvp`'s shebang and (2) run uses suite `libexec/vvp` with `PYGPI_PYTHON_BIN` / `LIBPYTHON_LOC` aimed at `dma-venv`. Do not call raw `oss-cad-suite/bin/vvp` for cocotb runs (it forces suite `PYTHONHOME`). After changing wrappers, delete `test/sim_build/` so shebangs are rewritten. A Nix-profile Icarus plus `LD_LIBRARY_PATH` libexpat shim is obsolete and must not be the supported agent path.

### Python environment

System Python is `python3` (not `python`). Use the existing virtual environment at **repository root** `dma-venv` (`/mnt/c/hw_projects/dma-tapeout/dma-venv` on this machine). Activate it via `source test/env.sh` (or `source dma-venv/bin/activate`). The required pins match `ttihp-verilog-template/test/requirements.txt`:

```text
cocotb==2.0.1
pytest==8.4.2
```

Setup from the repository root in WSL:

```sh
python3 -m venv dma-venv
source dma-venv/bin/activate
python3 -m pip install -r test/requirements.txt
```

No dependency is left unpinned in `test/requirements.txt`. Add a package only when platform implementation actually uses it.

### Version capture

Every CI run and sign-off bundle records (use `python3` / active venv):

```sh
source test/env.sh
test/scripts/doctor.sh
python3 --version
python3 -m pip freeze
iverilog -V
verilator --version
yosys -V
sby --version
```

Solver versions are also recorded for M4. A tool upgrade invalidates affected cached evidence until the assigned smoke and regression set reruns. The Makefile exports `COCOTB_RESULTS_FILE` into `RUN_DIR` and moves `dump.fst` into `RUN_DIR` after each run.

## Cocotb 2.x API rules

Code targets cocotb 2.0.1 directly. Do not add compatibility branches for cocotb 1.x.

- Use singular time keywords such as `unit="ns"` for `Clock`, `Timer`, and time conversion APIs.
- Start background coroutines with `cocotb.start_soon(...)`.
- Use `COCOTB_TEST_MODULES` and `COCOTB_TEST_FILTER` for module and test selection.
- Use `handle.value` and explicit integer or logic conversions. Do not depend on deprecated handle assignment shortcuts.
- Treat unresolved values deliberately. A conversion failure is not silently coerced to zero.
- Use cocotb triggers and simulation time for protocol behavior. Do not use host `sleep()` for simulated delays.
- Keep pytest responsible for Python-side collection/configuration and cocotb responsible for simulator scheduling. Do not assume pytest fixtures can cross the simulator boundary without an explicit adapter.

## CI matrix

The minimum pre-merge matrix after the corresponding milestones exist is:

| Job | Level | Simulator | Scope |
|---|---|---|---|
| L0 protocol | L0 | Icarus | Full directed engine suite |
| L0 cross-sim | L0 | Verilator | Required directed protocol subset |
| L1 functional | L1 | Icarus | Full directed DMA and arbitration suite |
| L1 fast regression | L1 | Verilator | Random seed batch and coverage |
| Formal | integration | SymbiYosys | Required `FP-*` jobs |
| L2 sign-off | L2 | Icarus | Selected gate-level suite when netlist exists |

M0 starts with only the L1 Icarus smoke. Jobs are added as their milestone becomes implementable. A missing future artifact such as the gate netlist is reported as `blocked`, not treated as a passing skip.

Shipped GitHub Actions (`.github/workflows/`):

| Workflow | What it runs | Notes |
|---|---|---|
| `test.yaml` | L1 Icarus `make smoke` at `TIMING_PROFILE=ideal` (zero TB placeholders) | Writes `test/results.xml` |
| `timing.yaml` | `bash test/scripts/run_timing.sh` at `TIMING_PROFILE=nominal` (documented APS6404L min/max AC with zero TB placeholders) | Invoke via `bash` so a missing git execute bit is not a 126. Suites: `test_qspi_timing`, `test_qspi_timing_delay`, `test_qspi_timing_launch_rx`, ownership negatives |
| `gds.yaml` | `tt-gds-action@ttihp26b` (harden, precheck, `gl_test`, viewer) | Port check reads `info.yaml` `source_files[0]` with yowasp Yosys; `top.v` must be first and package-free. Synth still uses `USE_SLANG`. `gl_test` copies the GDS netlist to `test/gate_level_netlist.v` and runs `GATES=yes make`, which must produce `test/results.xml` |
| `docs.yaml` | `tt-gds-action/docs@ttihp26b` | Datasheet render |

Hook scripts under `test/scripts/` should be git `+x`, but CI must not depend on that bit.

## Platform acceptance checklist

- One command selects each DUT level without source edits.
- The same Python PSRAM model and pin monitor serve L0 and L1.
- Icarus and Verilator builds are isolated.
- Every run prints versions, configuration, seed, and reproduction command.
- Always-on checkers start before reset release.
- Failure artifacts include a transaction trace and waveform according to policy.
- No test relies on an Obsidian file, machine-specific absolute path, or uncommitted tool binary.
- The TT-compatible `GATES=yes` path remains available for L2.

## Related

- Strategy, levels, and milestones: `01-strategy.md`
- Stable IDs and reading order: `00-index.md`
- RTL architecture and top name: `../03-architecture.md`
- QPI constants and timing: `../05-qspi-psram.md`
- Gate-level and physical timing checklist: `../11-timing-analysis.md`
- Firmware housekeeping twin: `../12-firmware.md`
- Human checklist: `../../human/roadmap.md`
