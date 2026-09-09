# Host side of the demoboard HIL stack (CPython, not copied to the MCU).
#
# link  - single-line envelope client plus transports (mpremote serial, or an
#         in-process loopback fake for unit tests)
# session - Pythonic wrapper over the remote firmware.session entry points
# fake_hw - fake DemoBoard / QSPI PMOD / DMA engine backing the loopback
#
# Import submodules explicitly. `firmware/` must never import this package.
