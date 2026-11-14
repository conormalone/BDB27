"""Run the pre-registered outcome test and write outputs/outcome_test/.

Spec: notes/outcome-test-spec.md (committed before any fit).
Usage: ./.venv/bin/python scripts/outcome_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import outcome_test as ot  # noqa: E402

TURNS = ROOT / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet"
PLAYER_PLAY = ROOT / "data/raw/player_play.csv"
OUT = ROOT / "outputs/outcome_test"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    con = ot.connect()
    df = ot.build_sample(con, TURNS, PLAYER_PLAY)
    summary = ot.sample_summary(df)

    fits, boot = ot.run_all(df)
    verdict = ot.verdict(fits, boot)

    keep = [
        "game_id", "play_id", "nfl_id", "frame_start", "position", "season",
        "abs_turn_angle", "entry_speed", "apex_speed", "apex_retention",
        "speed_retention", "plant", "turn_type", "own_team", "receiver_nfl_id",
        "receiver_position", "receiver_route", "separation_yd", "receiver_target",
        "coverage_assignment", "coverage_family", "cushion", "down", "yards_to_go",
        "team_coverage_man_zone", "pass_result", "one_turn_per_play",
    ]
    df[keep].to_csv(OUT / "attributed_turns.csv", index=False)
    pd.DataFrame([f.as_dict() for f in fits]).to_csv(OUT / "estimates.csv", index=False)

    payload = {
        "provenance": "FULL",
        "attribution": "INFERENCE — elimination matching (single visible opposing "
                       "route-runner); NOT the frozen 3-yd rule, which needs the "
                       "truncated tracking file",
        "spec": "notes/outcome-test-spec.md",
        "inputs": {
            "turns": "data/interim/full_run_20261111/bdb27_full_db_turns.parquet",
            "player_play": "data/raw/player_play.csv",
        },
        "sample": summary,
        "diagnostics": ot.diagnostics(df),
        "estimates": [f.as_dict() for f in fits],
        "bootstrap": boot,
        "verdict": verdict,
        "limits": [
            "attribution is an inference: the single visible opponent need not be the man covered",
            "prospect-versus-prospect only: both players must be in the 497-row cohort",
            "102 players is the independent unit; a sample of careers, not of the league",
            "no causal reading: the break is not randomised",
            "the frozen 3-yd proximity rule is unevaluable on this machine (1,048,575-row truncation)",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")

    print(json.dumps({"sample": summary, "verdict": verdict}, indent=2))
    print(pd.DataFrame([f.as_dict() for f in fits]).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
