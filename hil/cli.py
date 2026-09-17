"""Single `python -m hil` entry: bitstream subset, or the interactive test runner.

Bitstream commands exist only to compile / upload a UP5K `.bin` for HIL and
firmware. They do not run `hil/tests/`. The test runner is the remaining
argument path (`python -m hil`, or `python -m hil --target=fpga`).
"""

from __future__ import annotations

import sys
from typing import Sequence

# First-token commands handled by hil.fpga (no pytest, no interactive menu).
FPGA_COMMANDS = frozenset(
    {"bitstream", "harden", "upload", "configure", "doctor"}
)


def dispatch(argv: Sequence[str] | None = None) -> int:
    """Route `python -m hil` to bitstream tools or the HIL test runner.

    `bitstream` / `harden` / `upload` / `configure` / `doctor` go to
    `hil.fpga`. Anything else (including `--target=fpga`) is the existing
    interactive pytest menu in `hil.run`.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in FPGA_COMMANDS:
        from hil.fpga.__main__ import main as fpga_main

        return fpga_main(args)
    from hil.run import main as run_main

    return run_main(args)
