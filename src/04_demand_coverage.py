"""Stage 04 — demand map and Turn Coverage (TASK.md Phase 4-5). LOUD STUB."""

from __future__ import annotations

import argparse

from bdb27.stub import refuse


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    p.parse_args()
    refuse("04_demand_coverage", "Phases 4-5 (demand map, coverage, Cut Card data)",
           "the turn table and the per-player envelope from 03_envelope")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
