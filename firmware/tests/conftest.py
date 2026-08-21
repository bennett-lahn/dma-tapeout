"""Make the repo root importable so tests use `firmware.*` (never `test.*`)."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_root = str(_REPO_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)
