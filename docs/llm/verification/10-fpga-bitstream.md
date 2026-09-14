# FPGA bitstream toolchain (M7)

Verbose twin of [`../../human/verification/fpga.md`](../../human/verification/fpga.md). That human file is the one-time walkthrough operators should follow. This file is the agent contract.

Upstream Tiny Tapeout guide: [FPGA ASIC simulator breakout](https://www.tinytapeout.com/guides/fpga-asic-simulator-breakout/). Official tool: `tt_fpga.py` in [TinyTapeout/tt-support-tools](https://github.com/TinyTapeout/tt-support-tools). This is **not** the IHP LibreLane GDS flow (`../13-hardening-librelane.md`).

## Boundary vs sim / CI

Bitstream compile / upload exists only so HIL tests and demoboard firmware can run on the UP5K stand-in. There is no separate FPGA makefile or `hil/scripts` workflow. Do not add these verbs to `test/Makefile`, `test/scripts/doctor.sh`, or GitHub Actions.

| Surface | What it is | Hardware |
|---|---|---|
| `test/Makefile`, `test/scripts/*` | Cocotb sim (GHA-eligible) | No |
| `pytest hil/tests/ --target=loopback` | Host-only HIL fake-hardware path | No (still not a GHA job) |
| `python -m hil bitstream` / `upload` | Compile / copy `.bin` (no tests) | Synth local; upload needs board |
| `pytest hil/tests/ --target=fpga` | Demoboard HIL, fresh bitstream built and uploaded first | Yes |

`test/env.sh` may still be sourced so OSS CAD and `dma-venv` are on PATH.

## Purpose

Before shuttle freeze, M7 loads synthesizable `tt_um_lahnb_sgdma` RTL onto a Lattice UP5K that occupies the ASIC connector on the same ETR demoboard and MCU, with the real dual-PSRAM PMOD (D28). The bitstream is the artifact that makes `tt.shuttle.tt_um_lahnb_sgdma.enable()` program that FPGA.

A bitstream pass plus `hil/` `--target=fpga` is firmware and functional confidence. It closes **no** `T-*` row (nanosecond physical timing).

## Layout

```text
hil/cli.py                   # python -m hil dispatch
hil/__main__.py
hil/fpga/
  tool.py                    # stage, Yosys slang, nextpnr, icepack, mpremote
  __main__.py                # bitstream / upload / configure / doctor
  test_tool.py
  stage/                     # gitignored
  build/                     # gitignored <top_module>.bin
```

`info.yaml` remains the source list and top name. Staging exists because official `tt_fpga.py` writes `_tt_fpga_top.v` into `source_dir`.

## Commands

One entry: `python -m hil`. First-token verbs are bitstream-only (no pytest):

```sh
source test/env.sh
python -m hil doctor
python -m hil bitstream
python -m hil bitstream --breakout-target classic
python -m hil upload --port /dev/ttyACM0
python -m hil configure --upload --set-default --clockrate 66000000 --engine official
```

HIL tests (same package). `--target=fpga` compiles and uploads on its own, once per pytest session, so the FPGA run is one flag:

```sh
pytest hil/tests/ --target=fpga
python -m hil --target=fpga
```

`--bitstream` exists only to opt out of that rebuild; see the HIL hook below.

`harden` is an alias of `bitstream`. `test/scripts/doctor.sh` does not run the FPGA doctor.

### Harden engines

| Engine | Behavior |
|---|---|
| `native` (default) | Yosys `read_verilog -sv` for the TT `SB_IO` wrapper, `read_slang` for project RTL, then `synth_ice40` / `nextpnr-ice40 --up5k --package sg48` / `icepack`. Slang is required because `types.svh` has `import` (`sys_control_pkg` imports `qspi_pkg`) |
| `official` | `tt_fpga.py harden --project-dir hil/fpga/stage`. Stock script uses `read_verilog -sv` and fails on those packages; the wrapper then falls back to native |

### Configure engines

| Engine | Behavior |
|---|---|
| `mpremote` (default) | Copies to `/bitstreams/<top_module>.bin`, then resets so the SDK scans and registers the new bitstream |
| `official` | `tt_fpga.py configure` (required for `--set-default` / `--clockrate`) |

## Breakout targets

| Value | PCF | Board |
|---|---|---|
| `fabricfox` (default, `FPGA_BREAKOUT`) | `tt_fpga_fabricfoxv2.pcf` | TT ETR demoboard FPGA |
| `classic` | `tt_fpga_top.pcf` | TT04-era UP5K ASIC-simulator breakout |

## Tool discovery

`TT_SUPPORT_TOOLS` / `TT_FPGA_DIR`, then `~/tt`, `~/tt-support-tools`, `ttihp-verilog-template/tt`, `tools/tt-support-tools`. Official Python is the current interpreter if it imports, else `~/ttsetup/venv/bin/python`.

nextpnr defaults: `TT_FPGA_SEED=10`, `TT_FPGA_FREQ=12`. The FPGA HIL profile drives the project clock at that same 12 MHz constraint by default, not the ASIC's 66 MHz (D16). First local native harden (2026-09-08) wrote `hil/fpga/build/tt_um_lahnb_sgdma.bin` and reported about 34 MHz FPGA `clk` versus the 12 MHz constraint (PASS). It is not a 66 MHz FPGA timing close and not IHP pad evidence.

## HIL hook

`hil/conftest.py` owns two flags for the whole FPGA path:

| Flag | Effect |
|---|---|
| `--target=fpga` | Session-scoped `_prepare_fpga_bitstream` builds and uploads before any test. `--target` has **no default**: loopback is fake hardware and must be named, and a run without it is a pytest usage error (exit 4) for any test taking `target_profile` / `link` / `session` |
| `--bitstream={build,reuse,stale}` | `build` (default) runs `hil.fpga.tool.harden`; `reuse` keeps `hil/fpga/build/tt_um_lahnb_sgdma.bin` but **refuses** it when any build input is newer; `stale` installs it anyway. Rejected outside `--target=fpga` |

Staleness is `hil.fpga.tool.stale_inputs`: mtime of the `.bin` against `info.yaml` plus every `source_files` entry in `src/`. Conservative by design, so a touched source forces a rebuild rather than a silent old-RTL run. The breakout PCF lives in the tt-support-tools clone and is not tracked; changing breakout needs an explicit rebuild. `python -m hil upload` applies the same refusal and takes `--stale` to override it.

Every policy ends in an upload, so `/bitstreams` and `hil/fpga/build/` cannot disagree. `--port` keeps its `/dev/ttyACM0` default and only needs passing on a different port. The upload reset drops USB CDC; `upload_bitstream` waits until `mpremote` can open the port again before pytest starts `session.init`, so Windows `COM*` / Linux `ttyACM*` re-enumeration is not a "failed to access" race.

A loopback run announces itself twice: a `pytest_report_header` line at the top and a `pytest_terminal_summary` separator after the results.

MCU firmware is unchanged. Per TinyDMA-2C prior art, UART FPGA scripts exist in a separate dump; they are not this path.

## Related

- Strategy / M7 exit: `01-strategy.md`
- Sim platform (do not add FPGA verbs there): `02-platform.md`
- Firmware enable / `/bitstreams`: `../12-firmware.md`
- LibreLane GDS: `../13-hardening-librelane.md`
