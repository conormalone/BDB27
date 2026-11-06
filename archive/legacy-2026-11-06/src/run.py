#!/usr/bin/env python3
"""run.py — dispatcher for the BDB27 burst-vs-jump chain.

    python src/run.py 01_audit --mode sample
    python src/run.py --help

Stages run in order and share one code path for --mode sample|full. A stage that
exists only as a layout stub exits non-zero with STAGE NOT IMPLEMENTED — it never
returns success, so a stubbed layout can never be mistaken for a finished run.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

STAGES = [
    "01_audit",
    "02_combine_physical_time",
    "03_game_snaps",
    "03b_reliability",
    "04_models",
    "05_validate",
]


def _load(stage: str):
    path = _SRC / f"{stage}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"bdb27_{stage}", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="BDB27 burst-vs-jump pipeline dispatcher")
    parser.add_argument("stage", nargs="?", default=None)
    parser.add_argument("--mode", choices=("sample", "full"), default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.list or args.stage is None:
        print("stages (run in this order):")
        for s in STAGES:
            print(f"  {s}")
        return 0 if args.list else 2

    if args.stage not in STAGES:
        print(f"unknown stage {args.stage!r}; valid: {', '.join(STAGES)}",
              file=sys.stderr)
        return 2

    mod = _load(args.stage)
    if mod is None or not hasattr(mod, "main"):
        print(f"STAGE NOT IMPLEMENTED: {args.stage} has no module in src/",
              file=sys.stderr)
        return 3

    forwarded = ["--mode", args.mode] if args.mode else []
    if args.force:
        forwarded.append("--force")
    if args.config:
        forwarded += ["--config", args.config]
    import common as C
    try:
        return int(mod.main(forwarded) or 0)
    except C.GateRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 4
    except C.SchemaError as exc:
        print(f"SCHEMA GATE FAILED: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
