"""dl_load: recency, ordering, threshold parity, grids, posterior, budget.

Every test here is mechanical — it pins prereg §7 (load definitions and tuning)
and design §5.4 (snap columns), not any modelling result.  No fitted numbers
are asserted anywhere.
"""

from __future__ import annotations

import copy
import math

import pandas as pd
import pytest

import dl.dl_load as L

HALF_LIFE = 300.0


def _snaps(times, speeds, durations=None):
    """Snap dicts in chronological order (earliest first)."""
    if durations is None:
        durations = [1.0] * len(times)
    return [
        {"seconds_ago": s, "speed_yds": v, "duration_s": d}
        for s, v, d in zip(times, speeds, durations)
    ]


def _with_distance(snaps, key, threshold):
    out = []
    for snap in snaps:
        clone = dict(snap)
        clone[key] = max(0.0, snap["speed_yds"] - threshold) * snap["duration_s"]
        out.append(clone)
    return out


# --------------------------------------------------------------------------- #
# 1. Recency weighting
# --------------------------------------------------------------------------- #
def test_recency_weight_halves_at_one_half_life():
    assert L.recency_weight(0.0, HALF_LIFE) == 1.0
    assert L.recency_weight(HALF_LIFE, HALF_LIFE) == pytest.approx(0.5)
    assert L.recency_weight(2 * HALF_LIFE, HALF_LIFE) == pytest.approx(0.25)
    assert L.recency_weight(90.0, HALF_LIFE) == pytest.approx(0.5 ** 0.3)


def test_recency_weight_rejects_bad_inputs():
    with pytest.raises(ValueError):
        L.recency_weight(10.0, 0.0)
    with pytest.raises(ValueError):
        L.recency_weight(-1.0, HALF_LIFE)


# --------------------------------------------------------------------------- #
# 2. Absolute load: edges, ordering, hand arithmetic
# --------------------------------------------------------------------------- #
def test_empty_snaps_return_empty_load():
    assert L.absolute_load([], 1.5, HALF_LIFE) == []
    assert L.relative_load([], 0.7, HALF_LIFE, 9.0) == []


def test_single_snap_load_is_zero_not_na():
    snaps = _with_distance(_snaps([120.0], [6.0]), L.ABS_DISTANCE_KEY, 1.5)
    assert L.absolute_load(snaps, 1.5, HALF_LIFE) == [0.0]


def test_first_snap_zero_and_earlier_only_contribution():
    snaps = _with_distance(
        _snaps([300.0, 200.0, 100.0], [3.0, 4.0, 6.0]), L.ABS_DISTANCE_KEY, 1.5
    )
    load = L.absolute_load(snaps, 1.5, HALF_LIFE)
    w300 = 0.5
    w200 = 0.5 ** (200.0 / HALF_LIFE)
    d = [snap[L.ABS_DISTANCE_KEY] for snap in snaps]
    assert load[0] == 0.0
    assert load[1] == pytest.approx(w300 * d[0])
    assert load[2] == pytest.approx(w300 * d[0] + w200 * d[1])
    # the current snap never contributes to its own load
    assert load[2] < w300 * d[0] + w200 * d[1] + d[2]


@pytest.mark.parametrize(
    "times",
    [
        [100.0, 300.0],   # reversed
        [300.0, 300.0],   # tie
        [300.0, 200.0, 250.0],
    ],
)
def test_unsorted_input_raises(times):
    snaps = _with_distance(_snaps(times, [4.0] * len(times)), L.ABS_DISTANCE_KEY, 1.5)
    with pytest.raises(ValueError):
        L.absolute_load(snaps, 1.5, HALF_LIFE)


def test_negative_distance_raises():
    snaps = [
        {"seconds_ago": 300.0, L.ABS_DISTANCE_KEY: 1.0},
        {"seconds_ago": 100.0, L.ABS_DISTANCE_KEY: -0.5},
    ]
    with pytest.raises(ValueError):
        L.absolute_load(snaps, 1.5, HALF_LIFE)


def test_mismatched_threshold_stamp_raises():
    snaps = _with_distance(_snaps([300.0, 100.0], [5.0, 5.0]), L.ABS_DISTANCE_KEY, 2.0)
    snaps[0]["threshold_yds"] = 3.0
    with pytest.raises(ValueError):
        L.absolute_load(snaps, 2.0, HALF_LIFE)
    # the stamp agreeing is fine
    snaps[0]["threshold_yds"] = 2.0
    assert len(L.absolute_load(snaps, 2.0, HALF_LIFE)) == 2


# --------------------------------------------------------------------------- #
# 3. Absolute vs relative: identical arithmetic path
# --------------------------------------------------------------------------- #
def test_absolute_and_relative_identical_when_thresholds_agree():
    base = _snaps([400.0, 300.0, 180.0, 60.0], [4.2, 5.1, 5.8, 6.4], [0.4, 0.5, 0.6, 0.3])
    threshold = 0.7 * 9.0
    abs_snaps = _with_distance(base, L.ABS_DISTANCE_KEY, threshold)
    rel_snaps = _with_distance(base, L.REL_DISTANCE_KEY, threshold)
    abs_load = L.absolute_load(abs_snaps, threshold, HALF_LIFE)
    rel_load = L.relative_load(rel_snaps, 0.7, HALF_LIFE, 9.0)
    assert abs_load == rel_load  # same code path => bit-identical


def test_relative_rejects_fraction_above_one():
    snaps = _with_distance(_snaps([100.0], [6.0]), L.REL_DISTANCE_KEY, 3.0)
    with pytest.raises(ValueError):
        L.relative_load(snaps, 1.1, HALF_LIFE, 5.0)


# --------------------------------------------------------------------------- #
# 4. Combine curve
# --------------------------------------------------------------------------- #
def test_speed_at_and_ceiling_alias():
    expected = 10.0 * (1.0 - math.exp(-1.5 / 1.2))
    assert L.speed_at(10.0, 1.2, 1.5) == pytest.approx(expected)
    assert L.ceiling_speed(10.0, 1.2, 1.5) == pytest.approx(expected)
    assert L.speed_at(10.0, 1.2, 0.0) == 0.0
    assert L.ceiling_speed(10.0, 1.2, 0.0) == 0.0
    # monotonically increasing, saturating below vmax
    assert L.speed_at(10.0, 1.2, 1.0) < L.speed_at(10.0, 1.2, 2.0) < 10.0


def test_ceiling_speed_rejects_negative_time():
    with pytest.raises(ValueError):
        L.ceiling_speed(10.0, 1.2, -0.1)


# --------------------------------------------------------------------------- #
# 5. Threshold grids and the flat column
# --------------------------------------------------------------------------- #
def test_grid_column_naming_from_cfg(cfg):
    load_cfg = cfg["bdb27_dl"]["load"]
    snaps = _snaps([300.0, 200.0, 100.0], [4.0, 5.0, 6.0])
    grid = load_cfg["absolute_speed_grid_yds"]
    cols = L.threshold_grid_loads(snaps, grid, "absolute", HALF_LIFE)
    assert list(cols) == ["absolute_load_1p5", "absolute_load_2p0",
                          "absolute_load_2p5", "absolute_load_3p0",
                          "absolute_load_3p5"]
    assert len(cols) == len(grid)
    assert all(len(values) == len(snaps) for values in cols.values())


def test_relative_grid_naming_and_first_value_zero(cfg):
    grid = cfg["bdb27_dl"]["load"]["relative_fraction_grid"]
    snaps = _snaps([300.0, 200.0, 100.0], [4.0, 5.0, 6.0])
    cols = L.threshold_grid_loads(snaps, grid, "relative", HALF_LIFE, v_ceiling_yds=9.0)
    assert list(cols) == ["relative_load_0p5", "relative_load_0p6",
                          "relative_load_0p7", "relative_load_0p8",
                          "relative_load_0p9"]
    assert all(values[0] == 0.0 for values in cols.values())


def test_higher_threshold_gives_lower_load():
    grid = [1.5, 2.0, 2.5, 3.0, 3.5]
    snaps = _snaps([400.0, 300.0, 200.0, 100.0], [6.0, 6.5, 6.2, 6.8])
    cols = L.threshold_grid_loads(snaps, grid, "absolute", HALF_LIFE)
    finals = [cols[f"absolute_load_{L.grid_label(g)}"][-1] for g in grid]
    assert all(a > b for a, b in zip(finals, finals[1:]))


def test_flat_column_stays_none_until_threshold_frozen(cfg):
    snaps = _snaps([300.0, 100.0], [5.0, 6.0])
    assert L.flat_load_column("absolute", None, snaps, HALF_LIFE) is None
    assert L.flat_load_column("relative", None, snaps, HALF_LIFE,
                              v_ceiling_yds=9.0) is None

    frozen = 2.0
    stamped = [
        {**snap, "threshold_yds": frozen,
         L.ABS_DISTANCE_KEY: max(0.0, snap["speed_yds"] - frozen)}
        for snap in snaps
    ]
    col = L.flat_load_column("absolute", frozen, stamped, HALF_LIFE)
    grid = L.threshold_grid_loads(snaps, [1.5, 2.0, 2.5], "absolute", HALF_LIFE)
    assert col == grid["absolute_load_2p0"]


def test_grid_requires_v_ceiling_for_relative_and_rejects_unsorted_grid():
    snaps = _snaps([100.0], [6.0])
    with pytest.raises(ValueError):
        L.threshold_grid_loads(snaps, [0.5, 0.6], "relative", HALF_LIFE)
    with pytest.raises(ValueError):
        L.threshold_grid_loads(snaps, [0.6, 0.5], "relative", HALF_LIFE,
                               v_ceiling_yds=9.0)


# --------------------------------------------------------------------------- #
# 6. Posterior load
# --------------------------------------------------------------------------- #
def _posterior_snaps():
    return _snaps([400.0, 300.0, 200.0, 100.0], [3.9, 4.6, 5.2, 5.8],
                  [0.4, 0.5, 0.5, 0.6])


def test_posterior_mean_converges_to_point_estimate_as_se_to_zero():
    snaps = _posterior_snaps()
    vmax, tau, frac, t_match = 9.4, 1.15, 0.7, 1.0
    mean, sd = L.posterior_load(snaps, frac, HALF_LIFE, vmax, tau, 0.0, 0.0,
                               draws=25, seed=11, t_match_s=t_match)
    ceiling = L.speed_at(vmax, tau, t_match)
    stamped = _with_distance(snaps, L.REL_DISTANCE_KEY, frac * ceiling)
    point = L.relative_load(stamped, frac, HALF_LIFE, ceiling)
    assert mean == pytest.approx(point, abs=1e-6)
    assert max(sd) < 1e-12


def test_posterior_deterministic_under_seed_and_distinct_across_seeds():
    snaps = _posterior_snaps()
    args = (snaps, 0.7, HALF_LIFE, 9.4, 1.15, 0.08, 0.10)
    a = L.posterior_load(*args, draws=40, seed=7)
    b = L.posterior_load(*args, draws=40, seed=7)
    c = L.posterior_load(*args, draws=40, seed=8)
    assert a == b
    assert a[1] != c[1]
    assert max(a[1]) > 0.0  # snap 0 has sd exactly 0 by construction


def test_posterior_first_snap_zero_and_shape():
    snaps = _posterior_snaps()
    mean, sd = L.posterior_load(snaps, 0.7, HALF_LIFE, 9.4, 1.15, 0.05, 0.05,
                                draws=10, seed=3)
    assert len(mean) == len(snaps) == len(sd)
    assert mean[0] == 0.0 and sd[0] == 0.0


# --------------------------------------------------------------------------- #
# 7. Equal tuning budget
# --------------------------------------------------------------------------- #
def test_equal_tuning_budget_reads_cfg(cfg):
    assert L.equal_tuning_budget(cfg) == 5
    assert L.equal_tuning_budget(cfg["bdb27_dl"]) == 5


def test_equal_tuning_budget_raises_when_lengths_differ(cfg):
    clone = copy.deepcopy(cfg)
    clone["bdb27_dl"]["load"]["relative_fraction_grid"] = [0.5, 0.6, 0.7, 0.8]
    with pytest.raises(ValueError):
        L.equal_tuning_budget(clone)


# --------------------------------------------------------------------------- #
# 8. Table-level hook: add_load_columns
# --------------------------------------------------------------------------- #
def _frame(rows):
    """Build a snap table with the columns the hook requires."""
    base = {
        "game_id": 1, "play_id": 1, "nfl_id": 7, "quarter": 1,
        "game_clock_s": 900.0, "in_game_speed_1_0": float("nan"),
        "in_game_speed_2_0": float("nan"),
    }
    out = []
    for row in rows:
        out.append({**base, **row})
    return pd.DataFrame(out, columns=list(base))


def test_add_load_columns_orders_by_clock_and_measures_rest(cfg):
    table = _frame([
        {"play_id": 30, "game_clock_s": 600.0},
        {"play_id": 10, "game_clock_s": 840.0},
        {"play_id": 20, "game_clock_s": 720.0},
    ])
    out = L.add_load_columns(table, cfg)
    got = out.set_index("play_id")["seconds_since_last_snap"]
    # clock counts down, so play 10 is first: 0:14:00, 20 is 0:12:00, 30 0:10:00
    assert math.isnan(got.loc[10])
    assert got.loc[20] == pytest.approx(120.0)
    assert got.loc[30] == pytest.approx(120.0)


def test_add_load_columns_rest_crosses_the_quarter_boundary(cfg):
    table = _frame([
        {"play_id": 1, "quarter": 1, "game_clock_s": 10.0},   # 890 s elapsed
        {"play_id": 2, "quarter": 2, "game_clock_s": 890.0},  # 910 s elapsed
    ])
    out = L.add_load_columns(table, cfg)
    assert out.sort_values("play_id")["seconds_since_last_snap"].iloc[1] == \
        pytest.approx(20.0)


def test_add_load_columns_distance_skips_missing_speed_not_zero(cfg):
    table = _frame([
        {"play_id": 1, "game_clock_s": 840.0,
         "in_game_speed_1_0": 2.0, "in_game_speed_2_0": 2.4},   # 2.2 yd
        {"play_id": 2, "game_clock_s": 720.0},                  # unmeasured
        {"play_id": 3, "game_clock_s": 600.0,
         "in_game_speed_1_0": 3.0, "in_game_speed_2_0": 3.4},   # 3.2 yd
        {"play_id": 4, "game_clock_s": 480.0},
    ])
    out = L.add_load_columns(table, cfg).set_index("play_id")
    assert out.loc[1, "total_distance_before"] == 0.0
    assert out.loc[2, "total_distance_before"] == pytest.approx(2.2)
    # the unmeasured snap contributes nothing -- it is not read as zero work
    assert out.loc[3, "total_distance_before"] == pytest.approx(2.2)
    assert out.loc[4, "total_distance_before"] == pytest.approx(5.4)


def test_add_load_columns_flat_loads_na_until_threshold_frozen(cfg):
    table = _frame([
        {"play_id": 1, "game_clock_s": 840.0,
         "in_game_speed_1_0": 5.0, "in_game_speed_2_0": 5.0},
        {"play_id": 2, "game_clock_s": 720.0,
         "in_game_speed_1_0": 5.0, "in_game_speed_2_0": 5.0},
    ])
    out = L.add_load_columns(table, cfg)
    assert out["absolute_load"].isna().all()
    assert out["relative_load"].isna().all()
    assert out["snaps_in_drive"].isna().all()


def _frozen_cfg(cfg, **chosen):
    clone = copy.deepcopy(cfg)
    clone["bdb27_dl"]["load"]["chosen"] = dict(chosen)
    return clone


def test_add_load_columns_absolute_load_is_causal_and_weighted(cfg):
    """Load at s excludes s and discounts earlier work by its true age."""
    table = _frame([
        {"play_id": 1, "game_clock_s": 840.0,
         "in_game_speed_1_0": 2.2, "in_game_speed_2_0": 2.2},   # 0.2 above 2.0
        {"play_id": 2, "game_clock_s": 720.0,
         "in_game_speed_1_0": 3.2, "in_game_speed_2_0": 3.2},   # 1.2 above
        {"play_id": 3, "game_clock_s": 600.0,
         "in_game_speed_1_0": 4.0, "in_game_speed_2_0": 4.0},   # 2.0 above
    ])
    out = L.add_load_columns(
        table, _frozen_cfg(cfg, absolute_speed_yds=2.0)).set_index("play_id")
    w120 = 0.5 ** (120.0 / 300.0)
    w240 = 0.5 ** (240.0 / 300.0)
    assert out.loc[1, "absolute_load"] == 0.0
    assert out.loc[2, "absolute_load"] == pytest.approx(0.2 * w120)
    assert out.loc[3, "absolute_load"] == pytest.approx(
        0.2 * w240 + 1.2 * w120)


def test_add_load_columns_relative_load_needs_a_ceiling_per_row(cfg):
    table = _frame([
        {"play_id": 1, "game_clock_s": 840.0,
         "in_game_speed_1_0": 6.0, "in_game_speed_2_0": 6.0},
        {"play_id": 2, "game_clock_s": 720.0,
         "in_game_speed_1_0": 6.0, "in_game_speed_2_0": 6.0},
    ])
    frozen = _frozen_cfg(cfg, relative_fraction=0.5)
    assert L.add_load_columns(table, frozen)["relative_load"].isna().all()

    with_ceiling = table.assign(v_ceiling_yds=[8.0, 8.0])
    out = L.add_load_columns(with_ceiling, frozen).set_index("play_id")
    w120 = 0.5 ** (120.0 / 300.0)
    # threshold = 0.5 * 8 = 4; each snap is 2.0 yd above it
    assert out.loc[2, "relative_load"] == pytest.approx(2.0 * w120)


def test_add_load_columns_snaps_in_drive_requires_a_drive_key(cfg):
    table = _frame([
        {"play_id": 1, "game_clock_s": 840.0},
        {"play_id": 2, "game_clock_s": 720.0},
        {"play_id": 3, "game_clock_s": 600.0},
    ])
    assert L.add_load_columns(table, cfg)["snaps_in_drive"].isna().all()

    with_drive = table.assign(drive_id=[1, 2, 2])
    out = L.add_load_columns(with_drive, cfg).set_index("play_id")
    assert out.loc[1, "snaps_in_drive"] == 0.0
    assert out.loc[2, "snaps_in_drive"] == 0.0   # first snap of drive 2
    assert out.loc[3, "snaps_in_drive"] == 1.0


def test_add_load_columns_is_order_invariant_and_does_not_mutate(cfg):
    table = _frame([
        {"play_id": 1, "game_clock_s": 840.0,
         "in_game_speed_1_0": 3.0, "in_game_speed_2_0": 3.2},
        {"play_id": 2, "game_clock_s": 720.0,
         "in_game_speed_1_0": 4.0, "in_game_speed_2_0": 4.1},
        {"play_id": 3, "game_clock_s": 600.0,
         "in_game_speed_1_0": 2.0, "in_game_speed_2_0": 2.1},
    ])
    frozen = _frozen_cfg(cfg, absolute_speed_yds=2.0)
    first = L.add_load_columns(table, frozen)
    second = L.add_load_columns(table.sample(frac=1.0, random_state=3), frozen)
    assert list(first.columns) == list(table.columns) + list(L.LOAD_COLUMN_ORDER)
    pd.testing.assert_frame_equal(
        first.sort_values("play_id").reset_index(drop=True),
        second.sort_values("play_id").reset_index(drop=True))
    # input untouched: no load column leaked into the caller's frame
    assert "absolute_load" not in table.columns


def test_add_load_columns_without_a_clock_leaves_sequence_na(cfg):
    table = _frame([
        {"play_id": 1, "game_clock_s": float("nan"),
         "in_game_speed_1_0": 4.0, "in_game_speed_2_0": 4.0},
        {"play_id": 2, "game_clock_s": 720.0,
         "in_game_speed_1_0": 4.0, "in_game_speed_2_0": 4.0},
    ])
    out = L.add_load_columns(table, _frozen_cfg(cfg, absolute_speed_yds=2.0))
    for col in ("seconds_since_last_snap", "total_distance_before",
                "absolute_load", "relative_load", "snaps_in_drive"):
        assert out[col].isna().all(), col


def test_add_load_columns_empty_frame_has_every_column(cfg):
    out = L.add_load_columns(_frame([]), cfg)
    assert len(out) == 0
    for col in L.LOAD_COLUMN_ORDER:
        assert col in out.columns


def test_add_load_columns_rejects_a_table_without_required_columns(cfg):
    with pytest.raises(ValueError, match="required columns"):
        L.add_load_columns(pd.DataFrame({"game_id": [1]}), cfg)
    with pytest.raises(ValueError):
        L.add_load_columns(_frame([{"play_id": 1}]), cfg, mode="demo")


def test_load_thresholds_from_cfg(cfg):
    assert L.load_thresholds_from_cfg(cfg) == {
        "absolute_speed_yds": None, "relative_fraction": None}
    frozen = _frozen_cfg(cfg, absolute_speed_yds=2.5, relative_fraction=0.7)
    assert L.load_thresholds_from_cfg(frozen) == {
        "absolute_speed_yds": 2.5, "relative_fraction": 0.7}
    with pytest.raises(ValueError):
        L.load_thresholds_from_cfg(_frozen_cfg(cfg, relative_fraction=1.4))


def test_add_load_columns_zero_before_first_snap_na_when_history_unmeasured(cfg):
    """An empty prefix is 0.0; an unmeasured prefix is NA, not 0.0."""
    table = _frame([
        {"play_id": 1, "game_clock_s": 840.0},                  # unmeasured
        {"play_id": 2, "game_clock_s": 720.0},                  # no measured history
        {"play_id": 3, "game_clock_s": 600.0,
         "in_game_speed_1_0": 4.0, "in_game_speed_2_0": 4.0},
        {"play_id": 4, "game_clock_s": 480.0,
         "in_game_speed_1_0": 4.0, "in_game_speed_2_0": 4.0},
    ])
    out = L.add_load_columns(
        table, _frozen_cfg(cfg, absolute_speed_yds=2.0)).set_index("play_id")
    assert out.loc[1, "total_distance_before"] == 0.0
    assert out.loc[1, "absolute_load"] == 0.0
    assert math.isnan(out.loc[2, "total_distance_before"])
    assert math.isnan(out.loc[2, "absolute_load"])
    # play 3 is the first *measured* snap, so its history is still unmeasured
    assert math.isnan(out.loc[3, "total_distance_before"])
    assert math.isnan(out.loc[3, "absolute_load"])
    assert out.loc[4, "total_distance_before"] == pytest.approx(4.0)
    assert out.loc[4, "absolute_load"] == pytest.approx(
        2.0 * 0.5 ** (120.0 / 300.0))
