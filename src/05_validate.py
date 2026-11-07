"""Stage 05 — transfer, feasibility and outcome tests (TASK.md Phase 6). LOUD STUB."""

from __future__ import annotations

import argparse

from bdb.stub import refuse


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    p.parse_args()
    refuse("05_validate", "Phase 6 (transfer, feasibility, DL outcome models)",
           "the coverage table from 04_demand_coverage and the outcome labels")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
