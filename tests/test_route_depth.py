"""Route depth at the break (D61) — recovery, robustness, no-imputation.

These tests exercise the *real* detector (`lc_route_depth`) on the synthetic
worlds, where the geometric break depth is known.  The frozen definition is:

* play frame from `play_direction`, origin at the snap;
* centred rolling-median smoothing (the existing ceiling smoother);
* heading over a 0.2 s window;
* a >= 0.8 s stem;
* break = cumulative |dtheta| >= 45 deg in 0.3 s, new heading within 20 deg for
  0.3 s; timestamp = first frame of the sustained change;
* depth = downfield displacement at that frame;
* no break => `break_found = False`, depth MISSING (never imputed);
* a break after `pass_forward` is flagged, not deleted.

The fixture plants exact two-segment routes; `planted_break_depth_yd` is the
geometric truth, so recovery is a real check, not a tautology.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import lc_common as C
import lc_efforts as E
import lc_models as M
import lc_route_depth as RD
import synthetic_lc_fixture as F

TOLERANCE_YD = 1.0


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("route_depth") / "lc"
    F.generate(root, seed=20270107)
    base = root / "game"
    tracking = pd.read_csv(base / "game_tracking.csv")
    player_play = pd.read_csv(base / "player_play.csv")
    truth = json.loads((base / "ground_truth.json").read_text())
    direction = (player_play.drop_duplicates(["game_id", "play_id"])
                 .set_index(["game_id", "play_id"])["play_direction"].to_dict())
    return {"tracking": tracking, "player_play": player_play,
            "truth": truth, "direction": direction}


@pytest.fixture(scope="module")
def table(world, cfg):
    return RD.route_depth_table(world["tracking"], world["direction"], cfg)


def _truth_map(world):
    return {(e["game_id"], e["play_id"], e["nfl_id"]): e
            for e in world["truth"]["efforts"]}


def test_planted_break_depths_recovered(world, table):
    """RED -> GREEN: the detector recovers every planted break within 1 yd."""
    truth = _truth_map(world)
    merged = table.copy()
    merged["truth_depth"] = [truth[(g, p, n)]["planted_break_depth_yd"]
                             for g, p, n in zip(merged["game_id"],
                                                merged["play_id"],
                                                merged["nfl_id"])]
    planted = merged[merged["truth_depth"].notna()]
    assert len(planted) > 100, "fixture must plant a meaningful number of breaks"
    assert planted["break_found"].all(), (
        "planted breaks missed: "
        f"{planted.loc[~planted['break_found'], ['game_id', 'play_id']].to_dict('records')[:5]}")
    errors = (planted["break_depth_yd"] - planted["truth_depth"]).abs()
    assert errors.max() <= TOLERANCE_YD, errors.describe()
    # The median error should be comfortably tighter than the tolerance.
    assert errors.median() <= 0.5, errors.median()


def test_no_break_routes_are_not_given_a_depth(world, table):
    truth = _truth_map(world)
    no_break = table[[truth[(g, p, n)]["planted_break_depth_yd"] is None
                      for g, p, n in zip(table["game_id"], table["play_id"],
                                         table["nfl_id"])]]
    assert len(no_break) > 50
    assert not no_break["break_found"].any(), "false positive on a straight route"
    assert no_break["break_depth_yd"].isna().all(), "depth was fabricated"


def test_break_after_throw_is_flagged(world, table):
    truth = _truth_map(world)
    merged = table.copy()
    merged["late"] = [truth[(g, p, n)]["planted_break_after_throw"]
                      for g, p, n in zip(merged["game_id"], merged["play_id"],
                                         merged["nfl_id"])]
    late = merged[merged["late"]]
    assert len(late) > 20
    assert late["break_found"].all()
    assert late["break_after_throw"].all(), (
        "a break planted after the throw was not flagged")
    # The throw frame is the tracking event, not a re-derived time.
    assert (late["break_time_from_snap_s"]
            > late["throw_time_s"] - 1e-9).all()


def test_threshold_specification_curve_is_stable_on_truth(world, cfg, table):
    """+/-10 deg and +/-0.1 s must not make the planted answers threshold art.

    The frozen specification recovers every planted break, and every
    *one-step* perturbation (+/-10 deg at the frozen window, +/-0.1 s at the
    frozen angle) recovers >= 99%.  Only the strictest double perturbation
    (55 deg in a 0.2 s window) loses a visible share, because a 60 deg fixture
    break spends most of a 0.2 s window below the threshold.  Depths of the
    breaks that ARE found stay put -- the answer is not manufactured by the
    threshold.
    """
    base = table.set_index(["game_id", "play_id", "nfl_id"])["break_depth_yd"]
    truth = _truth_map(world)
    rows = []
    for spec in RD.sensitivity_grid(cfg):
        grid = RD.route_depth_table(world["tracking"], world["direction"], cfg,
                                    overrides=spec)
        grid = grid.set_index(["game_id", "play_id", "nfl_id"])
        planted = grid[[truth[key]["planted_break_depth_yd"] is not None
                        for key in grid.index]]
        found = planted["break_found"].astype(bool)
        recovered = float(found.mean())
        error = (planted["break_depth_yd"]
                 - [truth[key]["planted_break_depth_yd"] for key in planted.index])
        rows.append({
            "spec": spec,
            "recovered": recovered,
            "n_lost": int((~found).sum()),
            "break_rate": float(grid["break_found"].mean()),
            "median_depth": float(planted["break_depth_yd"].median()),
            "max_abs_error": float(error.abs().max()),
        })
    frame = pd.DataFrame(rows)
    frame["angle"] = frame["spec"].apply(lambda s: s["break_angle_deg"])
    frame["window"] = frame["spec"].apply(lambda s: s["break_window_s"])
    frozen = frame[(frame["spec"].apply(lambda s: s["break_angle_deg"]) == 45.0)
                   & (frame["spec"].apply(lambda s: s["break_window_s"]) == 0.3)]
    assert frozen["n_lost"].sum() == 0, "frozen spec lost a planted break"
    one_step = frame[(frame["window"] == 0.3) | (frame["angle"] == 45.0)]
    assert one_step["recovered"].min() >= 0.99, one_step
    assert frame["recovered"].min() >= 0.84, frame
    assert frame["max_abs_error"].max() <= TOLERANCE_YD
    assert (frame["median_depth"].max() - frame["median_depth"].min()) <= 0.75


def test_detector_ignores_speed_noise_and_survives_jitter(world, cfg, table):
    """The existing despike/speed-cap regime is irrelevant; jitter is bounded."""
    base = table.set_index(["game_id", "play_id", "nfl_id"])["break_depth_yd"]
    # 1. Impossible speed samples (the despike regime's target) must not move
    #    geometry: the definition reads positions, and the speed cap is applied
    #    where speed is consumed, not here.
    spiked = world["tracking"].copy()
    spiked.loc[spiked.index[::7], "s"] = 19.5
    spiked.loc[spiked.index[::11], "a"] = 55.0
    out = RD.route_depth_table(spiked, world["direction"], cfg)
    out = out.set_index(["game_id", "play_id", "nfl_id"])["break_depth_yd"]
    pd.testing.assert_series_equal(base, out, check_names=False)

    # 2. Small position jitter must leave the recovered depth within tolerance.
    rng = np.random.default_rng(20270107)
    jittered = world["tracking"].copy()
    jittered["x"] = jittered["x"] + rng.normal(0.0, 0.05, len(jittered))
    jittered["y"] = jittered["y"] + rng.normal(0.0, 0.05, len(jittered))
    out = RD.route_depth_table(jittered, world["direction"], cfg)
    out_frame = out.set_index(["game_id", "play_id", "nfl_id"])
    base_frame = table.set_index(["game_id", "play_id", "nfl_id"])
    flips = int((base_frame["break_found"].astype(bool)
                 != out_frame["break_found"].astype(bool)).sum())
    assert flips <= 0.01 * len(base_frame), (
        f"{flips} detections flipped under 0.05 yd position jitter")
    out = out_frame["break_depth_yd"]
    delta = (out - base).abs().dropna()
    assert delta.max() <= 0.75, delta.describe()


def test_direction_missing_is_flagged_not_guessed(world, cfg):
    tracking = world["tracking"]
    keys = tracking[["game_id", "play_id", "nfl_id"]].drop_duplicates().head(5)
    direction = dict(world["direction"])
    for game, play in zip(keys["game_id"], keys["play_id"]):
        direction.pop((game, play), None)
    out = RD.route_depth_table(tracking, direction, cfg)
    touched = out[[(g, p) in [(k[0], k[1]) for k in
                              zip(keys["game_id"], keys["play_id"])]
                   for g, p in zip(out["game_id"], out["play_id"])]]
    assert len(touched) > 0
    assert not touched["break_found"].any()
    assert touched["flags"].str.contains("play_direction_missing").all()


def test_detection_is_deterministic(world, cfg):
    a = RD.route_depth_table(world["tracking"], world["direction"], cfg)
    b = RD.route_depth_table(world["tracking"], world["direction"], cfg)
    pd.testing.assert_frame_equal(a, b)


def test_efforts_carry_depth_and_never_impute(world, cfg):
    efforts, diag = E.extract_efforts(world["player_play"], world["tracking"])
    assert diag["n_break_found"] > 0
    assert {"break_found", "break_depth_yd", "break_after_throw"} <= set(efforts)
    assert efforts.loc[~efforts["break_found"], "break_depth_yd"].isna().all()
    assert efforts.loc[efforts["break_found"], "break_depth_yd"].notna().all()


def test_model_depth_control_reports_and_sensitises(world, cfg):
    efforts, _ = E.extract_efforts(world["player_play"], world["tracking"])
    fits = M.fit_route_depth_control(efforts, cfg=cfg)
    headline = fits["including_post_throw_breaks"]
    sensitivity = fits["excluding_post_throw_breaks"]
    assert np.isfinite(headline["beta"])
    assert np.isfinite(headline["depth_coef"])
    assert np.isfinite(headline["break_found_coef"])
    assert headline["depth_control"]["note"].startswith("control only")
    assert sensitivity["n"] <= headline["n"]
    assert sensitivity["dropped_post_throw_breaks"] == int(
        efforts["break_after_throw"].sum())
    # The control must not change the estimand: the leverage coefficient stays
    # in the planted range.
    assert abs(headline["beta"] - 0.05) < 0.02
