"""Run configuration: LEVEL, SIM, SEED, DMA_BUF_DEPTH, TIMING_PROFILE, RUN_DIR.

Milestone M0+ fills parsing and normalization per ``02-platform.md``. Values
are read from the environment the Makefile exports (``SEED``, ``LEVEL``,
``DUT_LEVEL``, ``DMA_BUF_DEPTH``, ``TIMING_PROFILE``, ``WAVES``, ``RUN_DIR``);
``SIM`` is not exported by the Makefile today, so it falls back to the
platform default (``icarus``) when absent.
"""

import os


def parse_run_config() -> dict:
    """Return normalized run configuration from environment set by the Makefile."""
    return {
        "seed": int(os.environ.get("SEED", "1")),
        "level": os.environ.get("LEVEL", "top"),
        "dut_level": os.environ.get("DUT_LEVEL", "L1"),
        "sim": os.environ.get("SIM", "icarus"),
        "dma_buf_depth": int(os.environ.get("DMA_BUF_DEPTH", "1")),
        "timing_profile": os.environ.get("TIMING_PROFILE", "ideal"),
        "waves": os.environ.get("WAVES", "auto"),
        "run_dir": os.environ.get("RUN_DIR", ""),
    }
