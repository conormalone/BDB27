"""Schema assertions must fail loudly, and the documented schema must match the
files this repo actually ships with.

The spec claims the Combine data has "only x, y, dir and dis (plus identifiers)".
That claim is tested here against the file header, not repeated from the spec.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdb27 import schema

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def header(name: str) -> list[str]:
    with open(RAW / name, newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


@pytest.mark.parametrize("fname,required", [
    ("combine_tracking.csv", ["draft_year", "event_id", "nfl_id", "entity_type", "time",
                              "drill_type", "drill_name", "attempt", "x", "y", "dir", "dis"]),
    ("combine_results.csv", ["draft_year", "nfl_id", "combine_position", "combine_height",
                             "combine_weight", "three_cone", "short_shuttle", "forty"]),
    ("game_tracking_2023_sample.csv", ["game_id", "play_id", "nfl_id", "time", "x", "y",
                                       "s", "a", "dis", "o", "dir", "event"]),
    ("player_play.csv", ["game_id", "play_id", "nfl_id", "route_ran", "quick_pressure",
                         "unblocked_pressure", "time_to_pressure", "player_get_off"]),
])
def test_documented_columns_exist(fname, required):
    cols = header(fname)
    missing = [c for c in required if c not in cols]
    assert not missing, f"{fname} is missing {missing}"


def test_spec_claim_about_combine_kinematics_is_refuted():
    """The spec says Combine has no speed/acceleration. The file has both."""
    cols = header("combine_tracking.csv")
    assert "s" in cols and "a" in cols, "spec claim changed: s/a absent"
    assert "o" not in cols, "orientation must not appear in the Combine file"


def test_game_has_orientation_combine_does_not():
    assert "o" in header("game_tracking_2023_sample.csv")
    assert "o" not in header("combine_tracking.csv")


def test_assert_columns_names_every_missing_column():
    df = pd.DataFrame({"x": [0], "y": [0]})
    with pytest.raises(schema.SchemaError) as exc:
        schema.assert_columns(df, ["x", "y", "dir", "dis"], name="toy")
    msg = str(exc.value)
    assert "dir" in msg and "dis" in msg and "toy" in msg


def test_assert_columns_passes_when_complete():
    df = pd.DataFrame({"x": [0], "y": [0], "dir": [90.0], "dis": [0.0]})
    schema.assert_columns(df, ["x", "y", "dir", "dis"], name="toy")


def test_assert_angle_range_rejects_out_of_range():
    ok = pd.DataFrame({"dir": [0.0, 359.9, 90.0]})
    schema.assert_angle_range(ok, "dir", name="toy")
    bad = pd.DataFrame({"dir": [0.0, 361.0]})
    with pytest.raises(schema.SchemaError):
        schema.assert_angle_range(bad, "dir", name="toy")


def test_real_angle_columns_are_degrees_0_360():
    """Read a small slice of the shipped files and check the convention holds."""
    comb = pd.read_csv(RAW / "combine_tracking.csv", usecols=["dir", "s"], nrows=5000)
    schema.assert_angle_range(comb, "dir", name="combine")
    game = pd.read_csv(RAW / "game_tracking_2023_sample.csv", usecols=["dir", "o"], nrows=5000)
    schema.assert_angle_range(game, "dir", name="game")
    schema.assert_angle_range(game, "o", name="game")


def test_route_ran_is_a_route_name_not_a_boolean():
    """player_play.route_ran holds route labels (GO/HITCH/...), not TRUE/FALSE."""
    pp = pd.read_csv(RAW / "player_play.csv", usecols=["route_ran"])
    values = set(pp.route_ran.dropna().unique())
    assert values, "route_ran is empty"
    assert not values <= {"True", "False", "TRUE", "FALSE"}, (
        f"route_ran looks boolean after all: {sorted(values)[:5]}")
