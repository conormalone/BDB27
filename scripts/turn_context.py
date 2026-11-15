"""Build the turn-level context table for the 30-60 degree DB band.

One row per band turn: the tracking-derived turn measures joined to the play
context in ``player_play.csv`` on the turn's own ``(game_id, play_id, nfl_id)``
row. Writes ``outputs/turn_context/`` with a manifest of sha256 digests.

No gate: every field and every subset is reported with its denominator, and a
thin subset is printed rather than withheld.

Usage:
    .venv/bin/python scripts/turn_context.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bdb27 import outcome_link as ol  # noqa: E402
from bdb27 import turn_context as tc  # noqa: E402

TURNS = ROOT / "data" / "interim" / "full_run_20261111" / "bdb27_full_db_turns.parquet"
PLAYER_PLAY = ROOT / "data" / "raw" / "player_play.csv"
OUT = ROOT / "outputs" / "turn_context"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    con = ol.connect()
    table = tc.build(con, TURNS, PLAYER_PLAY)

    parquet = OUT / "turn_context.parquet"
    table.to_parquet(parquet, index=False)

    split = tc.play_type_split(con)
    split.to_csv(OUT / "play_type_split.csv", index=False)

    availability = tc.field_availability(con)
    availability.to_csv(OUT / "field_availability.csv", index=False)

    scope = tc.demand_scope(con, TURNS)
    scope.to_csv(OUT / "demand_scope.csv", index=False)

    med = tc.medians(con)
    med.to_csv(OUT / "medians.csv", index=False)

    desc = con.execute("""
    SELECT play_type, description_check, COUNT(*) AS n
    FROM turn_context GROUP BY 1, 2 ORDER BY 1, 2
    """).df()
    desc.to_csv(OUT / "description_check.csv", index=False)

    keys = con.execute("""
    SELECT COUNT(*) AS band_turns,
           COUNT(DISTINCT (game_id, play_id, nfl_id, frame_start)) AS distinct_turn_keys,
           COUNT(*) - COUNT(DISTINCT (game_id, play_id, nfl_id, frame_start))
             AS duplicate_key_rows,
           COUNT(DISTINCT (game_id, play_id, nfl_id)) AS turn_players,
           COUNT(DISTINCT (game_id, play_id)) AS band_plays,
           COUNT(DISTINCT nfl_id) AS band_players,
           COUNT(DISTINCT game_id) AS band_games,
           COUNT(*) FILTER (WHERE NOT pp_row_present) AS no_player_play_row
    FROM turn_context
    """).df().iloc[0]

    summary = {
        "band": {"lo_deg": tc.BAND_LO, "hi_deg": tc.BAND_HI,
                 "interval": "left-closed, right-open",
                 "setting": "game", "position_group": "DB", "valid": True},
        "key": list(tc.KEY),
        "counts": {k: int(keys[k]) for k in keys.index},
        "play_type": {t: int(split.loc[split.play_type == t, "n"].sum())
                      for t in ("pass", "run")},
        "play_type_share": {
            t: round(float(split.loc[split.play_type == t, "n"].sum()
                           / split.loc[split.play_type == t, "denominator"].iloc[0]), 6)
            for t in ("pass", "run")},
        "pass_scramble_turns": int(split.loc[split.bucket == "scramble", "n"].sum()),
        "pass_dropback_only_turns": int(
            split.loc[(split.play_type == "pass") & (split.bucket == "not_scramble"), "n"].sum()),
        "field_availability": {
            r.field: {"present_band": int(r.present_band), "n_band": int(r.n_band),
                      "share_band": round(float(r.share_band), 6),
                      "present_pass": int(r.present_pass), "n_pass": int(r.n_pass),
                      "present_run": int(r.present_run), "n_run": int(r.n_run)}
            for r in availability.itertuples()},
        "demand_scope": {r.scope: {"turns": int(r.turns), "band_turns": int(r.band_turns),
                                   "band_share_of_scope": (
                                       round(float(r.band_share_of_scope), 6)
                                       if r.turns else None)}
                         for r in scope.itertuples()},
        "medians": {r.play_type: {"n": int(r.n),
                                  "median_cushion": None if pd.isna(r.median_cushion)
                                  else round(float(r.median_cushion), 4),
                                  "n_cushion": int(r.n_cushion),
                                  "median_abs_turn_angle": round(float(r.median_abs_turn_angle), 4)}
                    for r in med.itertuples()},
        "description_check": {f"{r.play_type}/{r.description_check}": int(r.n)
                              for r in desc.itertuples()},
        "note": ("no gate: every count is printed against its denominator; a thin "
                 "subset is reported, never blocked"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                     encoding="utf-8")

    produced = [parquet, OUT / "play_type_split.csv", OUT / "field_availability.csv",
                OUT / "demand_scope.csv", OUT / "medians.csv",
                OUT / "description_check.csv", OUT / "summary.json"]
    manifest = tc.write_manifest(
        OUT, [TURNS, PLAYER_PLAY], produced,
        config={"band": summary["band"], "key": summary["key"],
                "run_look_words": list(tc.RUN_LOOK_WORDS),
                "pass_scramble": "pass_result == 'R' kept in play_type='pass', flagged"},
        extra={"counts": summary["counts"], "play_type": summary["play_type"],
               "field_availability": summary["field_availability"],
               "demand_scope": summary["demand_scope"]})

    print(json.dumps({"rows": int(len(table)), "counts": summary["counts"],
                      "play_type": summary["play_type"],
                      "pass_scramble_turns": summary["pass_scramble_turns"],
                      "manifest": str(OUT / "manifest.json"),
                      "inputs": list(manifest["inputs"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
