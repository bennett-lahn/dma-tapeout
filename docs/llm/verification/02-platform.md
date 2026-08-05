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
    test_dma_directed.py
    test_dma_random.py
    test_reset_and_bus.py
    test_gate_level.py
  common/
    __init__.py
    config.py
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
- `tests/` contains cocotb test entry points. Test names carry `TC-*` IDs in docstrings or metadata, not in Python identifiers.
- `common/` contains host actions, clock/reset helpers, run configuration, deterministic random support, and artifact naming.
- `models/` contains the two independent APS6404L instances and delay layer.
- `reference/` contains the pure-Python TCD encoder/decoder, chain interpreter, the modularized legal-chain generator class described in `08-stimulus-and-coverage.md`, and scoreboards. None of these may call DUT internals, and the generator is kept in its own module (`generator.py`) separate from the golden interpreter (`chain.py`) it depends on, since generating stimulus and interpreting it are distinct responsibilities that must stay separately testable.
- `monitors/` contains passive protocol decoders and always-on `CHK-*` checks.
- `formal/` contains `.sby` jobs, harnesses, and bind files. It shares RTL sources and constants conceptually, but does not import cocotb code.

Do not make one monolithic `test.py`. Model, monitor, reference, and stimulus code must remain independently testable and reusable across levels.

## DUT-level selection

The stable command-line selector is:

| Selector | Verification level | HDL top | DUT |
|---|---|---|---|
| `LEVEL=engine` | L0 | `tb_engine` | `qspi_engine` |
| `LEVEL=top` | L1 | `tb_top` | `tt_um_lahnb_sgdma` |
| `LEVEL=gl` | L2 | `tb_gl` | gate-level `tt_um_lahnb_sgdma` |

Default: `LEVEL=top`.

`LEVEL=gl` implies `GATES=yes`. Supplying `GATES=yes` with `LEVEL=top` selects the same L2 flow for Tiny Tapeout compatibility. Other conflicting combinations must fail with a clear Make error instead of silently selecting a DUT.

RTL source order must place `src/rtl/types.svh` before modules that import its packages, then compile `qspi_engine.sv`, `sys_controller.sv`, and `top.v`. L0 compiles only the package and engine sources it needs. L1 compiles the integrated source set. L2 uses the final gate-level netlist and IHP models instead of RTL.

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
| `make directed` | Run directed tests assigned to the selected level |
| `make random` | Run constrained-random tests for one seed |
| `make regression` | Run the configured seed list and simulator matrix |
| `make formal` | Run the required SymbiYosys jobs |
| `make waves` | Open or print the path to the waveform from a selected prior run |
| `make clean` | Remove generated simulation build and run artifacts only |

`make` may alias `make test`, matching the TT template.

### Stable variables

| Variable | Default | Meaning |
|---|---|---|
| `LEVEL` | `top` | `engine`, `top`, or `gl` |
| `SIM` | `icarus` | cocotb simulator selector |
| `GATES` | unset | TT-compatible gate-level selector; `yes` implies `LEVEL=gl` |
| `SEED` | `1` | unsigned test seed printed at start and failure |
| `TEST_FILTER` | empty | cocotb test-name regular expression |
| `DMA_BUF_DEPTH` | `1` | Compile-time module parameter override for the 1/2/4/8 sweep (`-GDMA_BUF_DEPTH=N`) |
| `TIMING_PROFILE` | `ideal` | named timing parameter set |
| `WAVES` | `auto` | `auto`, `always`, or `never` |
| `SDF` | unset | optional SDF path for L2 |
| `NETLIST` | `gate_level_netlist.v` | L2 netlist path |
| `RUN_DIR` | generated | per-configuration output directory |

Examples (after `source test/env.sh`):

```sh
test/scripts/run_test.sh LEVEL=engine SIM=icarus TEST_FILTER=qspi SEED=17
test/scripts/run_test.sh LEVEL=top SIM=verilator SEED=4231 DMA_BUF_DEPTH=1
cd test && make test LEVEL=gl SIM=icarus GATES=yes NETLIST=gate_level_netlist.v
cd test && make random LEVEL=top SIM=verilator SEED=4231 TIMING_PROFILE=nominal
```

`DMA_BUF_DEPTH` is a module parameter on `tt_um_lahnb_sgdma` / `sys_controller` (default 1). Package `DMA_BUF_DEPTH_MAX` in `src/rtl/types.svh` sizes `qpi_byte_len_t` / cycle counters for the 1..8 sweep. The Makefile should pass `-GDMA_BUF_DEPTH=$(DMA_BUF_DEPTH)` (or the simulator equivalent) and reject values outside `1..DMA_BUF_DEPTH_MAX`.

The Makefile maps `TEST_FILTER` to cocotb 2.x `COCOTB_TEST_FILTER` and lists modules through `COCOTB_TEST_MODULES`. Do not use removed legacy environment names.

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
REPRO: source <repo>/test/env.sh && cd <repo>/test && make test LEVEL=top SIM=verilator TEST_FILTER=test_dma_random SEED=4231 DMA_BUF_DEPTH=1 TIMING_PROFILE=nominal
REPRO: source <repo>/test/env.sh && <repo>/test/scripts/run_test.sh LEVEL=top SIM=verilator TEST_FILTER=test_dma_random SEED=4231 DMA_BUF_DEPTH=1 TIMING_PROFILE=nominal
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
