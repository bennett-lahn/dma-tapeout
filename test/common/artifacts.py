"""Per-run artifact paths: logs, manifests, traces, waveforms.

Layout: ``test/runs/<level>/<sim>/n<depth>/<timing>/seed-<seed>/`` per
``02-platform.md``. The Makefile already computes and exports ``RUN_DIR``;
this falls back to reconstructing the same path when it is absent (e.g. a
config built outside the Makefile flow).
"""

import os


def run_dir(config: dict) -> str:
    """Return the artifact directory for *config*."""
    existing = config.get("run_dir")
    if existing:
        return existing
    return os.path.join(
        "runs",
        str(config.get("level", "top")),
        str(config.get("sim", "icarus")),
        f"n{config.get('dma_buf_depth', 1)}",
        str(config.get("timing_profile", "ideal")),
        f"seed-{config.get('seed', 1)}",
    )
