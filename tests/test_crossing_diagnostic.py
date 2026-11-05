"""Crossing diagnostic: known-answer regressions for the LOS crossing, and the
recovery of a label's crossing definition from synthetic reps.

These are the red-to-green tests for the geometry the diagnostic relies on. They
are written so a change to the line-of-scrimmage formula, the depth sign, the
axis orientation, or the interpolation would fail loudly — the failure mode this
project has to rule out before it can call `player_get_off` a different
quantity.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import common as C
import physical as P
from conftest import load_stage

diag = load_stage("crossing_diagnostic")
import run as RUN  # noqa: E402


GRID = np.round(np.arange(0.50, 2.60 + 1e-9, 0.05), 4)


# --------------------------------------------------------------------------- #
# Line of scrimmage and depth sign — all four side/direction branches
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("side_is_possession,direction,expected_los", [
    (True, "right", 40.0),     # own 30 on the left half  -> 10 + 30
    (True, "left", 80.0),      # own 30 on the right half -> 110 - 30
    (False, "left", 40.0),     # opponent's 30, moving left -> 10 + 30
    (False, "right", 80.0),    # opponent's 30, moving right -> 110 - 30
])
def test_los_formula_four_branches(side_is_possession, direction,
                                   expected_los):
    side = "AAA" if side_is_possession else "BBB"
    los, _ = diag.los_and_depth(0.0, side, 30, "AAA", direction)
    assert los == pytest.approx(expected_los)


@pytest.mark.parametrize("side_is_possession,direction", [
    (True, "right"), (True, "left"), (False, "left"), (False, "right")])
def test_defender_depth_is_positive_on_the_defensive_side(side_is_possession,
                                                          direction):
    """A defender one yard on the defensive side must have depth +1."""
    side = "AAA" if side_is_possession else "BBB"
    los, _ = diag.los_and_depth(0.0, side, 30, "AAA", direction)
    sign = 1.0 if direction == "right" else -1.0
    defender_x = los + sign * 1.0          # one yard to the defensive side
    attacker_x = los - sign * 0.5          # half a yard behind the line
    _, depth_def = diag.los_and_depth(defender_x, side, 30, "AAA", direction)
    _, depth_off = diag.los_and_depth(attacker_x, side, 30, "AAA", direction)
    assert depth_def == pytest.approx(1.0)
    assert depth_off == pytest.approx(-0.5)


def test_los_is_missing_when_the_metadata_is_incomplete():
    assert diag.los_and_depth(50.0, None, 30, "AAA", "right") == (None, None)
    assert diag.los_and_depth(50.0, "AAA", 30, "AAA", "sideways") == (None, None)


# --------------------------------------------------------------------------- #
# The crossing itself on a series with a known answer
# --------------------------------------------------------------------------- #
def test_crossing_is_exact_for_constant_speed():
    times = np.round(np.arange(0.0, 2.0 + 1e-9, 0.1), 3)
    axis = 2.0 * times                     # 2 yd/s toward the line
    assert diag.time_at_axis(times, axis, 1.0) == pytest.approx(0.5, abs=1e-9)
    assert diag.time_at_axis(times, axis, 1.5) == pytest.approx(0.75, abs=1e-9)


def test_crossing_matches_the_analytic_time_under_acceleration():
    """x = 0.5*a*t^2; the interpolated crossing must match sqrt(2d/a)."""
    a = 3.0
    times = np.round(np.arange(0.0, 2.0 + 1e-9, 0.1), 3)
    axis = 0.5 * a * times ** 2
    for d in (0.25, 0.5, 1.0):
        expected = math.sqrt(2.0 * d / a)
        assert diag.time_at_axis(times, axis, d) == pytest.approx(
            expected, abs=0.01)


def test_mirrored_axis_never_reaches_the_line():
    """The sign test: with the axis flipped the rusher runs away from the line."""
    times = np.round(np.arange(0.0, 1.0 + 1e-9, 0.1), 3)
    toward = 2.0 * times
    away = -2.0 * times
    assert diag.time_at_axis(times, toward, 0.6) is not None
    assert diag.time_at_axis(times, away, 0.6) is None


def test_axis_curve_is_oriented_toward_the_line():
    xs = np.array([70.6, 70.5, 70.4, 70.3])
    assert np.all(np.diff(diag.axis_curve(xs, +1.0)) > 0)   # right-moving offence
    assert np.all(np.diff(diag.axis_curve(xs, -1.0)) < 0)   # left-moving offence


# --------------------------------------------------------------------------- #
# Recovering a label's crossing definition from synthetic reps
# --------------------------------------------------------------------------- #
def _synthetic_rep(vmax, tau, depth, label_distance, sign=+1.0):
    """One rep whose analytic trajectory and label are known exactly.

    Returns (times, axis, label) where ``label`` is the time at which the
    displacement reaches ``label_distance``.
    """
    times = np.round(np.arange(0.0, 3.0 + 1e-9, 0.1), 3)
    axis = np.array([P.exponential_distance(vmax, tau, t) for t in times])
    label = P.exponential_time_to_distance(vmax, tau, label_distance)
    assert label is not None and label < times[-1]
    return times, axis, float(label)


def _synthetic_world(plane_offset, n_plays=40, fixed=None):
    """A DataFrame of reps whose label is a plane crossing (or fixed distance)."""
    rng = np.random.default_rng(20270106)
    rows = []
    for play in range(n_plays):
        for k in range(2):
            vmax = float(rng.uniform(4.5, 8.0))
            tau = float(rng.uniform(0.10, 0.30))
            depth = float(np.round(rng.uniform(0.30, 1.20), 2))
            D = (depth + plane_offset) if fixed is None else fixed
            times, axis, label = _synthetic_rep(vmax, tau, depth, D)
            d_star = diag.implied_distance(times, axis, label, GRID)
            rows.append(dict(game_id=1, play_id=play, nfl_id=100 + k,
                             depth_yd=depth, player_get_off=label,
                             d_star_yd=d_star,
                             plane_offset_yd=(None if d_star is None
                                              else d_star - depth),
                             cross_time_s=diag.time_at_axis(times, axis, depth),
                             t_plane=diag.time_at_axis(times, axis, D)))
    import pandas as pd
    return pd.DataFrame(rows)


def test_plane_label_is_recovered_and_the_paired_slope_is_one():
    df = _synthetic_world(plane_offset=0.50)
    assert df.d_star_yd.notna().sum() == len(df)
    # the implied distance is the plane the label was built from
    assert df.plane_offset_yd.median() == pytest.approx(0.50, abs=0.06)
    out = diag.within_play_slope(df, "d_star_yd", "depth_yd")
    assert out["n_plays"] == 40
    assert out["slope"] == pytest.approx(1.0, abs=0.10)
    assert out["se"] < 0.05


def test_fixed_distance_label_gives_a_zero_paired_slope():
    """The alternative hypothesis must fail the same test: slope 0, not 1."""
    df = _synthetic_world(plane_offset=None, fixed=1.50)
    out = diag.within_play_slope(df, "d_star_yd", "depth_yd")
    assert abs(out["slope"]) < 0.25
    assert out["se"] < 0.05


def test_clock_offset_leaves_a_distance_dependent_residual():
    """A constant cannot absorb a per-rep distance; the plane definition can."""
    df = _synthetic_world(plane_offset=0.50)
    gap = (df.cross_time_s - df.player_get_off).to_numpy(float)
    rms_clock = float(np.sqrt(np.mean((gap - gap.mean()) ** 2)))
    # the label was built at depth + 0.50, so this definition reconstructs it
    rms_plane = float(np.sqrt(np.mean((df.t_plane - df.player_get_off) ** 2)))
    assert rms_plane < 5e-3      # 10 Hz interpolation error only
    assert rms_clock > 0.01
    assert rms_clock > 5.0 * rms_plane


def test_variance_components_add_up():
    import pandas as pd
    df = pd.DataFrame({"g": [1, 1, 2, 2, 3, 3], "v": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]})
    out = diag.variance_components(df, "v", "g")
    assert out["n"] == 6 and out["n_groups"] == 3
    # group means are 0.5, 2.5, 4.5 -> SD(ddof=1) = 2.0
    assert out["between_sd"] == pytest.approx(2.0)
    # every group is (m-0.5, m+0.5) -> within SD is 0.5
    assert out["within_sd"] == pytest.approx(0.5)
    assert out["total_sd"] == pytest.approx(float(np.std(df.v.to_numpy(),
                                                          ddof=1)))


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
def test_diagnostic_is_not_a_pipeline_stage():
    """Documented decision: the diagnostic must not enter the frozen run order."""
    assert diag.STAGE not in RUN.STAGES
    assert "crossing_diagnostic" not in RUN.STAGES


def test_verdicts_cover_all_five_hypotheses():
    summary = {"headline": {
        "median_gap_s": -0.38, "los_time_bound_s": 0.15,
        "los_bound_yd": 0.3, "median_speed_at_crossing_yds": 2.0,
        "within_play_slope": 0.99, "within_play_slope_se": 0.11,
        "within_play_pairs": 44, "within_play_plays": 22,
        "best_plane_offset_yd": 0.75, "los_cross_rmse_s": 0.45,
        "best_plane_rmse_s": 0.17, "clock_offset_rmse_s": 0.20,
        "gap_onset_correlation": -0.13,
        "between_player_sd_of_mean_gap_s": 0.08, "sd_gap_s": 0.20,
        "motion_share": 0.999,
    }}
    rows = diag.build_verdicts(summary, C.section(
        C.load_config(), "diagnostic"))
    assert len(rows) == 5
    ids = [r["hypothesis"] for r in rows]
    assert ids == ["1_los_error", "2_sign_or_axis", "3_crossing_reference",
                   "4_onset", "5_per_group"]
    assert rows[0]["verdict"] == "rejected"          # LOS bound < half the gap
    assert rows[2]["verdict"] == "confirmed"         # slope is 1
