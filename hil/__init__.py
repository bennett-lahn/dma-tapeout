# Host side of the demoboard HIL stack (CPython, not copied to the MCU).
#
# link  - single-line envelope client plus transports (mpremote serial, or an
#         in-process loopback fake for unit tests)
# session - Pythonic wrapper over the remote firmware.session entry points
# fake_hw - fake DemoBoard / QSPI PMOD / DMA engine backing the loopback
#
# Bitstream compile / upload: `python -m hil bitstream` / `python -m hil upload`
# (subset of this package; does not run tests). Functional HIL is
# `pytest hil/tests/ --target=...` or `python -m hil --target=...`.
#
# `--target` has no default: loopback is fake hardware and must be named.
# `--target=fpga` compiles and uploads a fresh bitstream by itself.
#
# Import submodules explicitly. `firmware/` must never import this package.
