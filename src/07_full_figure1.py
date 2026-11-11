#!/usr/bin/env python3
"""Stage 07 — redraw figure 1 from the uploaded full-data run (D185).

The renderer, its refusals and its provenance record live in
``bdb27.full_run``; this file is the CLI.

Usage:
    python src/07_full_figure1.py
    python src/07_full_figure1.py --source-dir data/interim/full_run_20261111
    python src/07_full_figure1.py --config /path/to/config.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import common, full_run  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--source-dir", default=None,
                        help="the uploaded full-run folder "
                             f"(default: {full_run.ARTEFACT_DIR})")
    parser.add_argument("--combine-turns", default=None,
                        help="turn table holding the Combine DB rows "
                             f"(default: {full_run.COMBINE_TURNS})")
    parser.add_argument("--out-dir", default=None,
                        help="figures directory (default: config paths.figures)")
    parser.add_argument("--force", action="store_true",
                        help="accepted for symmetry; this renderer always writes")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    result = full_run.run(
        cfg,
        source_dir=Path(args.source_dir) if args.source_dir else None,
        combine_turns=Path(args.combine_turns) if args.combine_turns else None,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        root=ROOT)

    combine, game = result["combine"], result["game"]
    print(f"[{full_run.STAGE}] figure 1 redrawn from the full run")
    print(f"  heading : {result['title']}")
    print(f"  30-60°  : Combine {int(combine['count'])}/"
          f"{int(combine['setting_total'])} = "
          f"{float(combine['share_within_setting']) * 100:.2f}%  vs  "
          f"game {int(game['count'])}/{int(game['setting_total'])} = "
          f"{float(game['share_within_setting']) * 100:.2f}%  "
          f"({result['ratio']:.3f}x)")
    print(f"  players : Combine {result['context']['players_by_setting']['combine']}"
          f", game {result['context']['players_by_setting']['game']} × "
          f"{result['context']['player_games']} player-games")
    print(f"  bands   : {result['crosscheck']['rows_checked']} rows match the "
          "uploaded band table")
    print(f"  png     : {result['png']}")
    print(f"  csv     : {result['csv']}")
    print(f"  manifest: {result['manifest']}")
    print(f"  prov    : {result['provenance']}")
    print(f"  text    : {result['drawn_text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
