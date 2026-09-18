"""Make the repo root importable so tests use `firmware.*` (never `test.*`)."""

import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_root = str(_REPO_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)


class _RPMode:
    """Current SDK mode values needed by CPython-only board mocks."""

    ASIC_RP_CONTROL = 1


_ttboard = types.ModuleType("ttboard")
_mode = types.ModuleType("ttboard.mode")
_mode.RPMode = _RPMode
_ttboard.mode = _mode
sys.modules.setdefault("ttboard", _ttboard)
sys.modules.setdefault("ttboard.mode", _mode)
