"""Deterministic ``SEED`` contract and derived child ``random.Random`` streams.

See ``02-platform.md`` and ``08-stimulus-and-coverage.md`` (Determinism section).
"""

import hashlib
import random


def child_random(base_seed: int, stream_name: str) -> random.Random:
    """Return a child ``random.Random`` derived from *base_seed* and *stream_name*.

    Deterministic and independent per *stream_name*: adding a draw on one
    stream never perturbs another stream derived from the same base seed.
    """
    digest = hashlib.sha256(f"{base_seed}:{stream_name}".encode("utf-8")).digest()
    child_seed = int.from_bytes(digest[:8], byteorder="big")
    return random.Random(child_seed)
