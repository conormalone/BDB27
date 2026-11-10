#!/usr/bin/env python3
"""CLI: scan every stage-06 figure's drawn text for placeholder hedging (D180).

Thin wrapper around ``bdb27.figure_text_scan``; see that module's docstring.
Nothing is written into ``outputs/`` — the figures are rebuilt into a temp dir.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27.figure_text_scan import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
