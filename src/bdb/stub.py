"""Loud stubs for stages 02-06.

A stub must be impossible to mistake for a finished run: it prints
``STAGE NOT IMPLEMENTED``, exits non-zero, writes no output and creates no
checkpoint.
"""

from __future__ import annotations

import sys


def refuse(stage: str, phase: str, needs: str) -> None:
    print(
        f"[{stage}] STAGE NOT IMPLEMENTED — {phase} is not built yet.\n"
        f"          This stage needs: {needs}\n"
        f"          Nothing was written and no checkpoint was created.",
        file=sys.stderr,
    )
    raise SystemExit(2)
