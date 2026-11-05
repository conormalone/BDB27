#!/usr/bin/env python3
"""04_models — NOT IMPLEMENTED.

Phase 4 — Models.

This file exists so that the layout and the dispatcher in TASK.md
("## Layout and run order") are real and addressable. It is deliberately a
stub: this run's scope is Phase 0 + Phase 1 only (see notes/decisions.md).

It exits with code 3 and writes nothing. It never creates a checkpoint, so a
later run cannot mistake the stub for completed work.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402

STAGE = "04_models"


def main(argv=None) -> int:
    return C.fail_not_implemented(
        STAGE,
        "Phase 4 — Models is out of scope for the Phase 0+1 run. Implement it against "
        "TASK.md before the first full-data run; see notes/pending_full_run.md.",
    )


if __name__ == "__main__":
    sys.exit(main())
