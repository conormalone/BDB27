"""Stage 09 speed-floor sensitivity: band assignment, deltas and the control.

The engine itself is already covered by test_turns_stage.py; these tests pin the
pieces this stage adds — the reporting-band assignment, the per-drill/per-player
deltas, and the refusal when the 2.5 yd/s control pass does not reproduce the
committed FULL Combine table.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from bdb27 import common

ROOT = Path(__file__).resolve().parents[1]


def _load_stage():
    spec = importlib.util.spec_from_file_location(
        "speed_floor_sensitivity", ROOT / "src" / "09_speed_floor_sensitivity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stage():
    return _load_stage()


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(ROOT / "config.yaml")


def test_bands_follow_the_committed_reporting_bands(stage, cfg):
    infos = stage.band_infos(cfg)
    labels = [i[0] for i in infos]
    assert labels == ["30–60°", "60–90°", "90–120°", "120–150°",
                      "150–180°", ">180°"]


def test_band_assignment_is_left_closed(stage, cfg):
    infos = stage.band_infos(cfg)
    assert stage.band_label(29.99, infos) is None
    assert stage.band_label(30.0, infos) == "30–60°"
    assert stage.band_label(59.99, infos) == "30–60°"
    assert stage.band_label(60.0, infos) == "60–90°"
    assert stage.band_label(179.99, infos) == "150–180°"
    assert stage.band_label(180.0, infos) == ">180°"
    assert stage.band_label(270.0, infos) == ">180°"
    assert stage.band_label(float("nan"), infos) is None


def test_band_table_counts_valid_turns_only_and_shares_sum_to_one(stage, cfg):
    infos = stage.band_infos(cfg)
    turns = pd.DataFrame({
        "abs_turn_angle": [30.0, 45.0, 61.0, 120.0, 200.0, 90.0],
        "valid": [True, True, True, True, True, False],
    })
    table = stage.band_table(turns, infos, "combine", "DB", 2.0)
    assert table["setting_total"].unique().tolist() == [5]
    counts = dict(zip(table["band"], table["count"]))
    assert counts == {"30–60°": 2, "60–90°": 1, "90–120°": 0,
                      "120–150°": 1, "150–180°": 0, ">180°": 1}
    assert table["share_within_setting"].sum() == pytest.approx(1.0)


def test_drill_and_player_deltas_reconstruct_the_floor_change(stage):
    base = pd.DataFrame({
        "nfl_id": [1, 1, 2], "drill_name": ["BOX_DRILL", "W_DRILL", "BOX_DRILL"],
        "abs_turn_angle": [40.0, 90.0, 70.0], "valid": [True, True, True],
    })
    test = pd.DataFrame({
        "nfl_id": [1, 1, 1, 2],
        "drill_name": ["BOX_DRILL", "W_DRILL", "W_DRILL", "BOX_DRILL"],
        "abs_turn_angle": [40.0, 90.0, 45.0, 70.0],
        "valid": [True, True, True, True],
    })
    drills = stage.drill_delta(base, test, stage.band_infos(common.load_config(
        ROOT / "config.yaml"))).set_index("drill_name")
    assert drills.loc["W_DRILL", "delta_turns"] == 1
    assert drills.loc["W_DRILL", "delta_30_60"] == 1
    assert drills.loc["BOX_DRILL", "delta_turns"] == 0
    players = stage.player_delta(base, test).set_index("nfl_id")
    assert players.loc[1, "delta_turns"] == 1
    assert players.loc[1, "delta_30_60"] == 1
    assert players["delta_turns"].sum() == 1


def test_turn_overlap_is_one_to_one(stage):
    base = pd.DataFrame({
        "nfl_id": [1, 1], "drill_name": ["W_DRILL", "W_DRILL"],
        "attempt": [1, 1], "frame_centre": [100, 200],
        "abs_turn_angle": [40.0, 80.0], "valid": [True, True],
    })
    test = pd.DataFrame({
        "nfl_id": [1, 1, 1], "drill_name": ["W_DRILL"] * 3,
        "attempt": [1, 1, 1], "frame_centre": [101, 199, 300],
        "abs_turn_angle": [40.0, 80.0, 50.0], "valid": [True, True, True],
    })
    out = stage.turn_overlap(base, test, tol_frames=5)
    assert out["matched"] == 2
    assert out["new_at_low_floor"] == 1
    assert out["lost_at_low_floor"] == 0
    assert out["matched"] + out["new_at_low_floor"] == len(test)


def test_control_refuses_when_counts_do_not_match(stage, cfg):
    infos = stage.band_infos(cfg)
    turns = pd.DataFrame({"abs_turn_angle": [40.0, 70.0], "valid": [True, True]})
    with pytest.raises(common.StageRefused):
        stage.check_control(turns, infos)


def test_null_table_reports_the_extra_detections(stage):
    base = {"db_attempts": 10, "frames": 1000, "observed_detections": 100,
            "null_detections": 20}
    test = {"db_attempts": 10, "frames": 1000, "observed_detections": 140,
            "null_detections": 30}
    table = stage.null_table(base, test)
    assert table.attrs["extra_observed"] == 40
    assert table.attrs["extra_null"] == 10
    assert table.attrs["excess_over_null"] == 30
    assert table.loc[0, "null_over_observed"] == pytest.approx(0.2)
