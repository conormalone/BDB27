"""Run the fallback validation and write outputs/fallback_validation/.

Spec: notes/fallback-validation-spec.md (committed at c95c983, before any fit).
Usage: ./.venv/bin/python scripts/fallback_validation.py

Three legs, one verdict, five artefacts. Nothing here reads the tracking file,
and nothing here uses a position group other than DB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import fallback_validation as fv  # noqa: E402

GAME_TURNS = ROOT / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet"
COMBINE_TURNS = ROOT / "outputs/sensitivity/speed_floor/combine_db_turns_floor2.5_control.csv"
COMBINE_RESULTS = ROOT / "data/raw/combine_results.csv"
OUT = ROOT / "outputs/fallback_validation"

PATTERNS = {
    "game_turns": ("bdb27_full_db_turns.parquet", "*full_db_turns*.parquet"),
    "combine_turns": ("combine_db_turns_floor2.5_control.csv",
                      "combine_db_turns*.csv"),
    "combine_results": ("combine_results.csv",),
}
HINTS = {
    "game_turns": "set GAME_TURNS in this script (or in the notebook's config cell) "
                  "to the full path of bdb27_full_db_turns.parquet",
    "combine_turns": "set COMBINE_TURNS to outputs/sensitivity/speed_floor/"
                     "combine_db_turns_floor2.5_control.csv",
    "combine_results": "set COMBINE_RESULTS to the full path of combine_results.csv",
}
#: The FULL run's folder, and the D190 control pass. Without these a search can
#: silently open a sample copy with the same filename (a scratch shard, or the
#: floor2.0 scan) — the ranking prints what it chose either way.
PREFER = {
    "game_turns": ("full_run_20261111", "data/interim"),
    "combine_turns": ("floor2.5_control", "speed_floor"),
    "combine_results": ("data/raw",),
}


def resolve() -> dict[str, Path]:
    roots = fv.search_roots(ROOT)
    wanted = {"game_turns": GAME_TURNS, "combine_turns": COMBINE_TURNS,
              "combine_results": COMBINE_RESULTS}
    return {name: fv.require_input(path, label=name, patterns=PATTERNS[name],
                                   roots=roots, hint=HINTS[name],
                                   prefer=PREFER[name])
            for name, path in wanted.items()}


def main() -> int:
    paths = resolve()
    con = fv.connect()
    game = fv.load_game_turns(paths["game_turns"], con=con)
    combine = fv.load_combine_turns(paths["combine_turns"], con=con)
    results = fv.load_combine_results(paths["combine_results"], con=con)

    print(f"inputs: game turns {len(game):,} rows {fv.STAMP}; "
          f"Combine turns {len(combine):,}; Combine DB drill rows {len(results)}")

    bundle = fv.analyse(game, combine, results)
    manifest = fv.write_outputs(OUT, bundle, inputs=paths, repo_root=ROOT)

    for lines in (fv.render_reliability(bundle.reliability),
                  fv.render_discriminant(bundle.discriminant, bundle.discriminant_meta),
                  fv.render_speed(bundle.speed)):
        print()
        print("\n".join(lines))
    print()
    print(bundle.text)
    print(json.dumps({"written": manifest["outputs"], "git_head": manifest["git_head"]},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
