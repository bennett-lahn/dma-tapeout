"""Run configuration: LEVEL, SIM, SEED, DMA_BUF_DEPTH, TIMING_PROFILE, RUN_DIR.

Milestone M0+ fills parsing and normalization per ``02-platform.md``. Values
are read from the environment the Makefile exports (``SEED``, ``LEVEL``,
``DUT_LEVEL``, ``DMA_BUF_DEPTH``, ``TIMING_PROFILE``, ``WAVES``, ``RUN_DIR``);
``SIM`` is not exported by the Makefile today, so it falls back to the
platform default (``icarus``) when absent.
"""

import os

from reference.constants import DMA_BUF_DEPTH_TAPEOUT


_TIMING_ENV_NAMES = (
    "PSRAM_TACLK_NS",
    "PSRAM_TCSP_NS",
    "PSRAM_TCHD_NS",
    "PSRAM_TCPH_NS",
    "PSRAM_THZ_NS",
    "PSRAM_TSP_NS",
    "PSRAM_THD_NS",
    "PSRAM_TCEM_US_EXT",
    "PSRAM_TCEM_US_STD",
    "PSRAM_TCH_MIN_RATIO",
    "PSRAM_TCL_MIN_RATIO",
    "PSRAM_TCH_MAX_RATIO",
    "PSRAM_TCL_MAX_RATIO",
    "PSRAM_TKHKL_NS",
    "TB_TCO_NS",
    "TB_FLIGHT_OUT_NS",
    "TB_FLIGHT_IN_NS",
    "TB_TCO_CE_NS",
    "TB_TCO_SCK_NS",
    "TB_TCO_SIO_NS",
    "TB_TCO_OE_NS",
    "TB_FLIGHT_OUT_CE_NS",
    "TB_FLIGHT_OUT_SCK_NS",
    "TB_FLIGHT_OUT_SIO_NS",
    "TB_FLIGHT_OUT_OE_NS",
    "TB_FLIGHT_IN_SIO_NS",
    "TB_TCO_CE_N_NS",
    "TB_FLIGHT_OUT_CE_N_NS",
)


def timing_env_overrides() -> dict[str, str]:
    """Return supplied ``resolve_timing_params`` PSRAM/TB environment knobs."""
    return {
        name: os.environ[name]
        for name in _TIMING_ENV_NAMES
        if name in os.environ
    }


def parse_run_config() -> dict:
    """Return normalized run configuration from environment set by the Makefile."""
    return {
        "seed": int(os.environ.get("SEED", "1")),
        "level": os.environ.get("LEVEL", "top"),
        "dut_level": os.environ.get("DUT_LEVEL", "L1"),
        "sim": os.environ.get("SIM", "icarus"),
        "dma_buf_depth": int(os.environ.get("DMA_BUF_DEPTH", str(DMA_BUF_DEPTH_TAPEOUT))),
        "timing_profile": os.environ.get("TIMING_PROFILE", "ideal"),
        "waves": os.environ.get("WAVES", "auto"),
        "run_dir": os.environ.get("RUN_DIR", ""),
    }
