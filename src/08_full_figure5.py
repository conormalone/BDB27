#!/usr/bin/env python3
"""Stage 08 — redraw figure 5 from the uploaded full-data band table.

The drawing and provenance live in ``bdb27.full_run_positions``; this file is
the CLI, mirroring ``07_full_figure1.py``.

Usage:
    python src/08_full_figure5.py
    python src/08_full_figure5.py --source-dir data/interim/full_run_20261111
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import common, full_run_positions  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--source-dir", default=None,
                        help="the uploaded full-run folder "
                             f"(default: {full_run_positions.full_run.ARTEFACT_DIR})")
    parser.add_argument("--combine-turns", default=None,
                        help="turn table holding the Combine rows "
                             f"(default: {full_run_positions.COMBINE_TURNS})")
    parser.add_argument("--out-dir", default=None,
                        help="figures directory (default: config paths.figures)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    result = full_run_positions.run(
        cfg,
        source_dir=Path(args.source_dir) if args.source_dir else None,
        combine_turns=Path(args.combine_turns) if args.combine_turns else None,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        root=ROOT)

    print(f"[{full_run_positions.STAGE}] figure 5 redrawn from the full run")
    print(f"  heading : {result['title']}")
    for position in result["context"]["positions"]:
        part = result["table"][result["table"]["position"] == position]
        band0 = part[part["band"] == part["band"].iloc[0]]
        shares = {}
        for _, row in band0.iterrows():
            shares[row["setting"]] = (int(row["count"]),
                                      int(row["setting_total"]),
                                      float(row["share_within_setting"]) * 100)
        combine = shares.get("combine")
        game = shares.get("game")
        c_text = (f"{combine[0]:,}/{combine[1]:,} = {combine[2]:.2f}%"
                  if combine else "no Combine turns")
        print(f"  {position:5s} : Combine {c_text}  vs  game "
              f"{game[0]:,}/{game[1]:,} = {game[2]:.2f}%")
    print(f"  rows    : {len(result['table'])} "
          f"({len(result['context']['positions'])} positions)")
    print(f"  png     : {result['png']}")
    print(f"  csv     : {result['csv']}")
    print(f"  manifest: {result['manifest']}")
    print(f"  prov    : {result['provenance']}")
    print(f"  text    : {result['drawn_text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
