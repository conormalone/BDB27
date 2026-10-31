#!/usr/bin/env python3
"""run.py — dispatcher for the DL chain.

Stage names are exactly the six in the design contract; modules are imported
lazily so a not-yet-written stage fails with a clear "not implemented" message
instead of an ImportError at start-up.

Supported invocations
---------------------
    cd src && python -m dl.run 01_audit --mode sample
    python src/dl/run.py 01_audit --mode sample          # from the repo root
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - depends on invocation style
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402  (needs the sys.path fix above)

#: Stage name -> importing module.  Order matches TASK.md's run order.
STAGES: dict[str, str] = {
    "01_audit": "dl.stage_01_audit",
    "02_combine_ceiling": "dl.stage_02_ceiling",
    "00_thresholds": "dl.stage_00_thresholds",
    "03_game_snaps": "dl.stage_03_snaps",
    "04_models": "dl.stage_04_models",
    "05_validate": "dl.stage_05_validate",
}


def _fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def _stage_list() -> str:
    return ", ".join(STAGES)


def main(argv: list[str] | None = None) -> int:
    """Parse the stage + shared flags and hand off to the stage module."""
    args = D.parse_args(argv, with_stage=True)
    stage = args.stage
    if stage is None:
        return _fail(
            "usage: python -m dl.run <stage> [--mode sample|full] [--force] "
            f"[--config PATH]\nvalid stages: {_stage_list()}")
    if stage not in STAGES:
        return _fail(
            f"unknown stage {stage!r}\nvalid stages: {_stage_list()}")

    module_name = STAGES[stage]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == module_name or missing.startswith("dl.stage_"):
            return _fail(
                f"STAGE NOT IMPLEMENTED: {stage!r} needs {module_name}, which "
                f"does not exist yet. Other valid stages: {_stage_list()}")
        raise

    entry = getattr(module, "main", None)
    if entry is None:
        return _fail(
            f"STAGE NOT IMPLEMENTED: {module_name} defines no main(argv); "
            f"stages must expose ``main(argv=None) -> int``.")

    stage_argv = ["--mode", args.mode]
    if args.force:
        stage_argv.append("--force")
    if args.config:
        stage_argv.extend(["--config", args.config])
    return int(entry(stage_argv) or 0)


if __name__ == "__main__":
    sys.exit(main())
