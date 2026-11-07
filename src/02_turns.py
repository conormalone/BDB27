"""Stage 02 — kinematics and turn detection (TASK.md Phase 2). LOUD STUB."""

from __future__ import annotations

import argparse

from bdb27.stub import refuse


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    p.parse_args()
    refuse("02_turns", "Phase 2 (smoothing + turn table)",
           "the audit's smoother choice frozen in notes/prereg.md")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
