"""Stage 06 — the five deliverable figures (TASK.md Phase 7). LOUD STUB."""

from __future__ import annotations

import argparse

from bdb27.stub import refuse


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    p.parse_args()
    refuse("06_figures", "Phase 7 (Cut Card + four result figures)",
           "validated outputs from stages 03-05")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
