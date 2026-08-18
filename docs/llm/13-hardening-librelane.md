# Local LibreLane hardening (TTIHP26b / ihp-sg13g2)

How this repo runs **manual** ASIC harden / area / GDS flows for Tiny Tapeout **TTIHP26b** with PDK **`ihp-sg13g2`**. Canonical TT overview: [Hardening Tiny Tapeout Projects Locally](https://www.tinytapeout.com/guides/local-hardening/). Shuttle decision: **D27** in [`07-decision-log.md`](07-decision-log.md). V1 tile target is **1x1 only** (D36); `1x2` is out of budget.

This document records the **working Nix path** used on the project machine (WSL). Docker remains the default TT path; we used Nix when Docker was unavailable / awkward.

## Two toolchains (do not mix)

| Role | Stack | Where |
|---|---|---|
| RTL sim, cocotb, formal | OSS CAD Suite + repo `dma-venv` via `test/env.sh` | Main repo `dma-tapeout/` |
| ASIC harden / GDS | LibreLane + IHP PDK + `ttihp-verilog-template` | Template tree + Nix / optional Docker |

OSS CAD Suite is **not** a LibreLane substitute. Harden in a **separate shell** from cocotb (roadmap lesson: do not mix nix Icarus into sim; keep harden Nix/LibreLane out of `test/env.sh` shells).

## Layout and source of truth

| Path | Role |
|---|---|
| `src/` (main repo) | **RTL source of truth** |
| `ttihp-verilog-template/` | TT project root for harden (`tt_tool`, LibreLane, GDS) |
| `ttihp-verilog-template/src/` | Copied RTL + `config.json` / `config_merged.json` |
| `ttihp-verilog-template/info.yaml` | TT metadata: `tiles`, `top_module`, `source_files` |
| `ttihp-verilog-template/tt/` | Vendored `tt-support-tools` (includes local `project.py` patch) |
| `IHP-Open-PDK/` | Optional local PDK clone; harden normally uses `ciel` under `PDK_ROOT` |
| `~/ttsetup/` (WSL home) | TT Python venv + PDK root (`~/ttsetup/pdk`) |
| `~/librelane/` (WSL home) | LibreLane flake; enter with `nix develop` |
| `~/tools/oss-cad-suite/` (WSL home) | Host Yosys + **slang** for TT port check / SV packages |

Top module: **`tt_um_lahnb_sgdma`**. Sources listed in `info.yaml` must exist as bare filenames under `ttihp-verilog-template/src/` (TT does not read `../../src/` automatically).

### Sync RTL into the template

```bash
cd /mnt/c/hw_projects/dma-tapeout
cp src/types.svh src/qspi_engine.sv src/sys_controller.sv src/top.v \
  ttihp-verilog-template/src/
```

Prefer copies over symlinks if Docker harden is used later (Docker often mounts only the TT project dir).

## One-time setup (WSL)

Run harden steps in **WSL bash**, not PowerShell. Paths like `~/librelane` are under the Linux home (`/home/<user>/…`), not `C:\Users\…`.

### 1. TT Python venv (system Python only)

Do **not** create this venv with OSS CAD’s `tabbypy3` (breaks LibreLane `tkinter` / `libtk8.6.so`).

```bash
sudo apt install -y python3-tk

deactivate 2>/dev/null || true
rm -rf ~/ttsetup/venv
/usr/bin/python3 -m venv ~/ttsetup/venv
source ~/ttsetup/venv/bin/activate
python -m pip install -U pip
pip install -r /mnt/c/hw_projects/dma-tapeout/ttihp-verilog-template/tt/requirements.txt
```

Sanity:

```bash
python -c "import sys, chevron; print(sys.executable); print(chevron.__file__)"
# executable must be ~/ttsetup/venv/... and must NOT mention oss-cad-suite
```

You do **not** need `pip install librelane` in this venv when hardening via Nix (LibreLane comes from `nix develop`).

### 2. PDK (`ciel` under `PDK_ROOT`)

```bash
export PDK_ROOT=~/ttsetup/pdk
export PDK=ihp-sg13g2
# First TT harden / LibreLane run typically pulls the PDK via ciel into $PDK_ROOT.
# Observed layout: ~/ttsetup/pdk/ciel/ihp-sg13g2/versions/<rev>/ihp-sg13g2/...
```

### 3. LibreLane via Nix

Clone / enter the LibreLane flake (example on this machine: `/home/<user>/librelane`):

```bash
cd ~/librelane
nix develop
python -c "import librelane; print(librelane.__version__)"   # expect ~3.0.5 class matching tt-gds-action ttihp26b
```

### 4. OSS CAD Suite (Yosys + slang for SV)

Needed for `tt_tool --create-user-config` port checks on SystemVerilog packages (`types.svh`). Local patch in `ttihp-verilog-template/tt/project.py` prefers host Yosys with slang (`find_host_yosys`, including `~/tools/oss-cad-suite/bin/yosys`) over `yowasp-yosys`.

Also required in LibreLane config: **`"USE_SLANG": true`** in `ttihp-verilog-template/src/config.json` (propagates into `config_merged.json`).

## Project knobs that matter

Edit before regenerating merged config:

| Knob | File | Notes |
|---|---|---|
| Tile size | `info.yaml` → `project.tiles` | V1 is **`"1x1"` only** (D36). Selects `tt/tech/ihp-sg13g2/def/tt_block_1x1_pgvdd.def`. Do not set `"1x2"` as an N=5 or area escape |
| Top / sources | `info.yaml` | Must match files under `src/` |
| Clock period | `src/config.json` → `CLOCK_PERIOD` | ns. **15.15** ≈ **66 MHz** (D16). I/O delays in SDC scale from this |
| Density | `src/config.json` → `PL_TARGET_DENSITY_PCT` | Default 60; GPL may auto-bump (e.g. to 0.69) on tight 1x1 |
| Hold margins | `PL_RESIZER_HOLD_SLACK_MARGIN`, `GRT_RESIZER_HOLD_SLACK_MARGIN` | Raise if hold fails |
| SV packages | `src/config.json` → `USE_SLANG` | Must stay **true** |

**Critical:** `DIE_AREA` / `FP_DEF_TEMPLATE` live in **`src/config_merged.json`**, generated from `info.yaml` by `--create-user-config`. If you change `tiles` (or other floorplan inputs) and then run `python -m librelane … src/config_merged.json` **without** regenerating, you harden the **old** die box. Symptom: `info.yaml` says `1x1` but logs show `tt_block_1x2_pgvdd.def` / die height 313.74 µm.

## Recommended run sequence

### A. Generate / refresh LibreLane config (TT venv)

```bash
source ~/ttsetup/venv/bin/activate
cd /mnt/c/hw_projects/dma-tapeout/ttihp-verilog-template
export PDK_ROOT=~/ttsetup/pdk
export PDK=ihp-sg13g2

# After RTL sync and after any info.yaml / config.json change:
./tt/tt_tool.py --ihp --create-user-config
```

Confirm `src/config_merged.json` has the **1x1** `DIE_AREA` and `FP_DEF_TEMPLATE` (`tt_block_1x1_pgvdd.def`). A 1x2 DEF means a stale merge, not a permitted V1 tile size.

### B. Harden (Nix LibreLane shell)

```bash
cd ~/librelane
nix develop
cd /mnt/c/hw_projects/dma-tapeout/ttihp-verilog-template
export PDK_ROOT=~/ttsetup/pdk
export PDK=ihp-sg13g2

python -m librelane \
  --pdk-root "$PDK_ROOT" \
  --pdk ihp-sg13g2 \
  --run-tag wokwi \
  --force-run-dir runs/wokwi \
  src/config_merged.json
```

Notes:

- `--force-run-dir runs/wokwi` reuses / overwrites the classic TT run tag directory (step numbers accumulate across re-runs in the same dir).
- Equivalent TT wrapper (only if that Python can import both LibreLane and TT deps):  
  `./tt/tt_tool.py --ihp --harden --no-docker`
- Default without `--no-docker` is Dockerized harden (`LIBRELANE_TAG` pin from `tt-gds-action@ttihp26b`, historically **3.0.5**).

### C. Reports (TT venv shell after harden)

Inside `nix develop`, `./tt/tt_tool.py` often picks Nix Python and loses `chevron`. Prefer the TT venv for reports:

```bash
source ~/ttsetup/venv/bin/activate
cd /mnt/c/hw_projects/dma-tapeout/ttihp-verilog-template

./tt/tt_tool.py --ihp --print-warnings
./tt/tt_tool.py --ihp --print-stats
./tt/tt_tool.py --ihp --print-cell-category
# optional: ./tt/tt_tool.py --ihp --create-png   # needs librsvg2-bin + pngquant
```

Optional GUIs from a shell that has the tools (`nix develop` + deps):

```bash
./tt/tt_tool.py --ihp --open-in-openroad --no-docker
./tt/tt_tool.py --ihp --open-in-klayout --no-docker
```

## Where to look after a run

| Artifact | Meaning |
|---|---|
| `runs/wokwi/final/` | Signed-off views LibreLane saved |
| `runs/wokwi/final/nl/tt_um_lahnb_sgdma.nl.v` | **Unpowered** netlist for gate-level (D27 / M6) - prefer over powered `pnl` |
| `runs/wokwi/*-yosys-synthesis/` | Synth logs + cell stats |
| `runs/wokwi/*-odb-cellfrequencytables/odb-cellfrequencytables.log` | Final per-master cell counts (incl. fill/decap) |
| `runs/wokwi/*-openroad-floorplan/openroad-floorplan.log` | Die/core area, pre-PnR utilization |
| `runs/wokwi/*-openroad-stapostpnr/` | Post-route STA corners |
| `runs/wokwi/*-misc-reportmanufacturability/` | Antenna / LVS / DRC summary |
| `gds_render.png` | Quick layout preview if `--create-png` was run |

Useful log greps: `DIEAREA`, `Effective utilization`, `Inserted .* hold`, `sg13g2_dfrbpq_1`, `Setup Worst`, `Hold Worst`, `Circuits match uniquely`.

## First successful area audits (2026-08)

Clock target **66 MHz** (`CLOCK_PERIOD` 15.15 ns). The first two rows are the same early RTL / same synth (**158** × `sg13g2_dfrbpq_1`, likely `DMA_BUF_DEPTH` **N=1**). The third row is the 2026-08-17 tapeout **N=5** close.

| Run | Floorplan | Core area | Pre-fill logic area | Pre-fill util | DFFs (`sg13g2_dfrbpq_1`) | Hold delay cells | Final instances (w/ fill) | Signoff |
|---|---|---|---|---|---|---|---|---|
| Accidental 1x2 (stale merged config; not a V1 option) | `tt_block_1x2_pgvdd.def`, die 202.08 × 313.74 µm | ~60,109 µm² | ~23,892 µm² | ~40% | 158 | 236 × `sg13g2_dlygate4sd3_1` | 5,264 | DRC/LVS/timing pass |
| True 1x1 (first audit, likely N=1) | `tt_block_1x1_pgvdd.def`, die 202.08 × 154.98 µm | ~28,941 µm² | ~24,066 µm² | ~60% → GPL density ~0.69 | 158 | 248 × `dlygate4sd3_1` | 2,626 | DRC/LVS/timing pass |
| 2026-08-17 1x1 N=5 (tapeout) | `tt_block_1x1_pgvdd.def`, die 202.08 × 154.98 µm | - | - | tighter than DFF count | 189 | - | - | DRC/LVS/timing pass; Setup Worst 5.8299 ns; Hold Worst 0.1265 ns (0 violations); 1 max-fanout in metrics |

Takeaways:

- First harden **fits 1x1** at 66 MHz with **158** mapped flops, positive setup slack (~6 ns post-route), and zero hold violations after repair. Combinational masters matched between the accidental 1x2 and first 1x1 runs; deltas were hold buffers and fill/decap.
- Tapeout **N=5** (`DMA_BUF_DEPTH=5`, on-chip RX-TX scratch depth) also closes **1x1** at 66 MHz: **189** × `sg13g2_dfrbpq_1`. `T-66` (user-tile setup/hold at 15.15 ns) is closed on this run (Setup Worst **5.8299 ns**, Hold Worst **0.1265 ns**, 0 violations). This does not close pad/board `T-*` rows.
- Hold slack ~0.13 ns is thin. Utilization is tighter than DFF count. Soft ~200 DFF caution on 1x1 (D36): **189** is under the warning line; hard gate remains fit + timing on `tt_block_1x1`. If a later harden fails, do not grow to 1x2; cut density, hold repair, or `DMA_BUF_DEPTH`. The old ~500 DFF / 2-tile figure is historical.
- Unpowered netlist: `ttihp-verilog-template/runs/wokwi/final/nl/tt_um_lahnb_sgdma.nl.v`. Post-route STA: `ttihp-verilog-template/runs/wokwi/602-openroad-stapostpnr/summary.rpt`. PDK: ciel `ihp-sg13g2` rev `c4b8b4e5e7a05f375cca3815d51b3a37721fbf5c`.
- Metrics recorded 1 max-fanout violation; DRC/LVS still clean.
- Sky130 rehardens are **not** predictive for IHP tile fit or pad timing (D27); use a Sky130 TT template only if targeting that shuttle.

## Pitfalls (already hit)

1. **`chevron` / TT deps missing** - use `~/ttsetup/venv` from **system** `/usr/bin/python3`, not OSS CAD Python.
2. **`libtk8.6.so` / tkinter** - same root cause (venv built with suite Python). Recreate venv; install `python3-tk`.
3. **`TOK_CONSTVAL` on `types.svh`** - yowasp Yosys cannot parse SV packages. Need host Yosys + slang + `USE_SLANG: true`, and the local `tt/project.py` host-yosys preference.
4. **Sourcing OSS CAD after the TT venv** - suite Python can shadow `chevron` again. For `--create-user-config`, TT venv alone is enough once `find_host_yosys` resolves `~/tools/oss-cad-suite/bin/yosys`.
5. **Stale `config_merged.json`** - changing `info.yaml` tiles without `--create-user-config` hardens the wrong die.
6. **Reports inside `nix develop`** - use TT venv Python for `tt_tool` print-* helpers.
7. **Fill/decap inflate instance count** - compare logic / DFF counts, not total instances after fill.
8. **Sky130 vs IHP** - different template, tiles, cells, pads; do not cite sky130 66/33 MHz GPIO ratings as binding.

## CI / shuttle path

GitHub harden for this project class uses `tt-gds-action` on the **ttihp26b** branch (LibreLane + ihp-sg13g2). Local Nix runs are for iteration and area learning; shuttle submission still expects the TT CI / template contract. Keep `ttihp-verilog-template` RTL copied from `src/` before relying on local harden GDS.

## Buffer-depth study (optional; not normal harden)

[`scripts/buffer-study/README.md`](../../scripts/buffer-study/README.md) wraps this runbook for an optional `DMA_BUF_DEPTH` / `N` (on-chip RX-TX scratch depth) area vs QPI throughput study (`harden`, `max-fit`, `throughput`). Patched RTL copies set depth and `DMA_BUF_DEPTH_MAX` to the same `N`; canonical `src/` stays at tapeout depth. LibreLane runs use unique tags such as `runs/bufstudy_1x1_n8_*` and must never clobber `runs/wokwi`. `test/` is unused except import-only reference scoring. Do **not** treat study max-fit as shuttle signoff; keep using the sequence in this document for V1.

## Related docs

- Constraints / tile geometry: [`02-constraints.md`](02-constraints.md)
- Gate-level / unpowered netlist: [`verification/09-gate-level-and-x.md`](verification/09-gate-level-and-x.md)
- Human condensed twin: [`../human/architecture/hardening.md`](../human/architecture/hardening.md)
- Upstream: https://www.tinytapeout.com/guides/local-hardening/
- LibreLane docs: https://librelane.readthedocs.io/
