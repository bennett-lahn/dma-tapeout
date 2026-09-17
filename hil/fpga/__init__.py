"""FPGA bitstream toolchain for TinyDMA M7 (iCE40 UP5K Tiny Tapeout breakouts).

This package wraps Tiny Tapeout `tt_fpga.py` (Yosys + nextpnr-ice40 + icepack)
and stages `info.yaml` sources so the official flow never writes into `src/`.
Harden artifacts land under `hil/fpga/build/`. Upload puts the `.bin` on the
demoboard at `/bitstreams/<top_module>.bin` so `tt.shuttle` can enable it.

Import `hil.fpga.tool` for the library API. Operators use
`python -m hil bitstream` / `python -m hil upload` (no tests), or just
`pytest hil/tests/ --target=fpga`, which builds and uploads before the first
test unless `--bitstream=reuse` / `--bitstream=stale` says otherwise.
"""
