"""Stage 03 — turn envelope / capacity (TASK.md Phase 3). LOUD STUB."""

from __future__ import annotations

import argparse

from bdb27.stub import refuse


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    p.parse_args()
    refuse("03_envelope", "Phase 3 (hierarchical upper-envelope capacity)",
           "the per-turn table written by 02_turns")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
