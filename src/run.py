"""Stage runner: 00_thresholds -> 01_audit -> 02_turns -> 03_envelope
-> 04_demand_coverage -> 05_validate -> 06_figures.

One code path for both modes. Each stage runs in its own process so the run log
records that stage's own peak RSS. A failing stage stops the chain.

Usage:
    python src/run.py --mode sample
    python src/run.py 01_audit --mode sample --force
    python src/run.py --mode full            # refuses, naming missing inputs
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

STAGES = [
    "00_thresholds",
    "01_audit",
    "02_turns",
    "03_envelope",
    "04_demand_coverage",
    "05_validate",
    "06_figures",
]

SRC = Path(__file__).resolve().parent


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", nargs="?", default=None,
                   help=f"one of {', '.join(STAGES)}; omit to run the chain in order")
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.stage is not None and args.stage not in STAGES:
        print(f"run: unknown stage {args.stage!r}; expected one of {STAGES}", file=sys.stderr)
        return 2
    stages = [args.stage] if args.stage else STAGES
    for stage in stages:
        cmd = [sys.executable, str(SRC / f"{stage}.py"), "--mode", args.mode]
        if args.force:
            cmd.append("--force")
        if args.config:
            cmd += ["--config", args.config]
        print(f"\n=== {stage} ({args.mode}) ===", flush=True)
        t0 = time.perf_counter()
        rc = subprocess.call(cmd)
        print(f"--- {stage}: exit {rc} in {time.perf_counter() - t0:.1f}s", flush=True)
        if rc != 0:
            print(f"run: stopped at {stage} (exit {rc})", file=sys.stderr)
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
