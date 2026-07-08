"""Unit tests for finance-assistant MCP scripts."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"

for _path in (_SCRIPTS, _ROOT):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str) -> unittest.TestSuite:
    """Discover test modules as ``tests.test_*`` so ``scripts`` is on ``sys.path``."""
    suite = unittest.TestSuite()
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        suite.addTests(loader.loadTestsFromName(f"tests.{path.stem}"))
    return suite
