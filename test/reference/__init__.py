"""Pure-Python TCD oracle: encode, decode, validate, chain interpreter, scoreboard.

Must not import cocotb. See ``docs/llm/verification/05-reference-model.md``.

Modules:

* :mod:`reference.tcd` - 11-byte serialization and validation
* :mod:`reference.constants` - architecture numbers (mechanical twin of ``firmware/constants.py``)
* :mod:`reference.chain` - memory image API and the golden chain interpreter
* :mod:`reference.scoreboard` - dual-axis compare, reset prefix, two-epoch equality
* :mod:`reference.generator` - legal chain construction for directed and random use
* :mod:`reference.coverage` - ``COV-*`` sampler and fragment aggregator (no cocotb)
"""

__all__ = [
    "constants",
    "tcd",
    "chain",
    "scoreboard",
    "generator",
    "coverage",
]
