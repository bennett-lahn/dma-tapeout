# FPGA bitstream (M7)

One-time walkthrough plus the HIL commands that compile a Tiny Tapeout FPGA bitstream and load it for firmware / `hil/tests/`. Verbose twin: [`../../llm/verification/10-fpga-bitstream.md`](../../llm/verification/10-fpga-bitstream.md). Upstream: [FPGA ASIC simulator breakout](https://www.tinytapeout.com/guides/fpga-asic-simulator-breakout/).

This is **not** LibreLane / IHP GDS harden (`architecture/hardening.md`). It is also **not** part of `test/Makefile` or GitHub Actions. There is no separate FPGA makefile. Compile and upload exist only so HIL / firmware can run on the UP5K stand-in. An FPGA pass is firmware and functional confidence only. It closes **no** physical timing `T-*` rows (nanosecond STA / demoboard measurements).

## What you get

| Piece | Role |
|---|---|
| Lattice UP5K on a TT-compatible breakout | Stands in for the ASIC in the same connector |
| `fabricfox` (default) | TT **ETR** demoboard FPGA |
| `classic` | TT04-era UP5K ASIC-simulator breakout from the Tiny Tapeout guide |
| `hil/fpga/build/tt_um_lahnb_sgdma.bin` | Host bitstream (`info.yaml` `top_module`) |
| `/bitstreams/tt_um_lahnb_sgdma.bin` on the MCU | `tt.shuttle.tt_um_lahnb_sgdma.enable()` loads it |

One HIL entry (`python -m hil`). Bitstream verbs do not run tests:

```sh
source test/env.sh
python -m hil doctor
python -m hil bitstream
python -m hil upload --port /dev/ttyACM0
```

Then run tests. `--target=fpga` compiles a fresh bitstream and uploads it by itself, so that is the whole command:

```sh
pytest hil/tests/ --target=fpga
# or interactive: python -m hil --target=fpga
```

`--target` has no default. Loopback is fake hardware, so it must be asked for by name (`--target=loopback`), and a loopback run says so in the pytest header and again after the results.

To skip the rebuild, `--bitstream=reuse` installs the existing `hil/fpga/build/tt_um_lahnb_sgdma.bin` but refuses it once `info.yaml` or any `src/` file is newer. `--bitstream=stale` is the only way to install an out-of-date bitstream.

`--breakout-target classic` on `bitstream`, or `FPGA_BREAKOUT=classic` on a `--target=fpga` run.

## One-time setup (do this once)

Work in **WSL bash**, not PowerShell. OSS CAD Suite on this machine is already at `~/tools/oss-cad-suite` and is pulled in by `source test/env.sh`.

### 1. OSS CAD Suite on PATH

```sh
source ~/tools/oss-cad-suite/environment
source /mnt/c/hw_projects/dma-tapeout/test/env.sh
command -v yosys nextpnr-ice40 icepack
```

`nextpnr-ice40` is the iCE40 place-and-route tool. `icepack` packs the ASCII bitstream into the `.bin` the demoboard feeds over SPI. If those names are missing, the suite environment was not sourced.

### 2. Tiny Tapeout support tools

The official guide clones tools to `~/tt`. This repo also accepts the gitignored `ttihp-verilog-template/tt` copy, or `TT_SUPPORT_TOOLS`.

```sh
git clone https://github.com/TinyTapeout/tt-support-tools.git ~/tt
# Optional: install the official Python extras into the existing TT venv
source ~/ttsetup/venv/bin/activate
pip install -r ~/tt/requirements.txt
deactivate
source /mnt/c/hw_projects/dma-tapeout/test/env.sh
```

Sanity:

```sh
python ~/tt/tt_fpga.py --help
python -m hil doctor
```

### 3. Serial / USB (for upload only)

Linux / WSL needs permission on the demoboard CDC port (usually `/dev/ttyACM0`). Check the group with `ls -l /dev/ttyACM0` and add yourself (`dialout` or `uucp`): `sudo adduser $USER dialout`. Log out or reboot once.

**WSL 2:** attach the USB device into Linux with [usbipd-win](https://learn.microsoft.com/windows/wsl/connect-usb). WSL 1 maps `COMx` to `/dev/ttySx`.

Need `mpremote`: `python3 -m pip install mpremote` (already in `~/ttsetup/venv`).

### 4. First official harden (optional familiarity)

The Tiny Tapeout guide uses a factory-test clone. Do this once so you see Yosys / nextpnr / icepack run end to end, then come back to this repo.

```sh
source ~/tools/oss-cad-suite/environment
git clone https://github.com/TinyTapeout/ttsky25b-factory-test.git /tmp/ftest
cd /tmp/ftest
python ~/tt/tt_fpga.py harden
# expect: /tmp/ftest/build/tt_um_factory_test.bin
```

Without `info.yaml` the same script needs `--top_module`, `--source_dir`, and one `--source` per file.

## Compile / upload (no tests)

```sh
source test/env.sh
python -m hil bitstream
python -m hil bitstream --breakout-target classic
python -m hil upload --port /dev/ttyACM0
```

`bitstream` copies `info.yaml` sources into `hil/fpga/stage/` (so `_tt_fpga_top.v` never lands in `src/`), then runs Yosys **slang** (`read_slang`) plus nextpnr-ice40 / icepack. Official `tt_fpga.py harden` uses `read_verilog -sv`, which cannot parse the `import` in `types.svh` (`sys_control_pkg` imports `qspi_pkg`). Publishes `hil/fpga/build/tt_um_lahnb_sgdma.bin`.

`upload` copies to `/bitstreams/` and resets the demoboard so its SDK scans and registers the new bitstream. It then waits until `mpremote` can open the CDC port again (the reset drops USB; Windows `COM*` / Linux `ttyACM*` take a few seconds to reappear). It does not itself enable `tt.shuttle` or clock the project. It refuses a `.bin` older than `info.yaml` or any `src/` source unless you pass `--stale`.

`TT_FPGA_SEED` / `TT_FPGA_FREQ` are nextpnr knobs (default seed `10`, P&R target `12` MHz). The FPGA HIL profile uses that same constraint for its demoboard project clock, rather than the ASIC's 66 MHz (D16). A first local harden on this machine reported about **34 MHz** FPGA `clk` versus that 12 MHz constraint (PASS). That number is not IHP pad timing.

Optional official `config.ini` extras:

```sh
python -m hil configure --upload --set-default --clockrate 66000000 \
  --port /dev/ttyACM0 --engine official
```

## Get it running

1. Plug the FPGA breakout into the demoboard like an ASIC.
2. Compile and upload (commands above), or skip straight to step 4, which does both.
3. REPL: `tt.shuttle.tt_um_lahnb_sgdma.enable()`, then `import firmware.session as session; session.init()`.
4. Or let HIL build, upload, and enable the design:

```sh
pytest hil/tests/ --target=fpga
```

Host-only loopback (`pytest hil/tests/ --target=loopback`) needs no board and is still not a GitHub Action; do not fold it into `test/Makefile`. Pure host unit tests that never touch a target (the checkers, link parsing, the FPGA tool) still run with no flags at all.

## Evidence boundary

M7 FPGA hardware validation (`TC-*` subset on real dual PSRAM) is firmware and integration confidence. FPGA I/O is not IHP SG13G2. Do not treat a bitstream pass as STA, pad, mux, or `T-*` close. ASIC GDS stays on the LibreLane path.
