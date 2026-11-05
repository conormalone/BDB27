"""Route depth at the break (D61) — recovery, robustness, no-imputation.

These tests exercise the *real* detector (`lc_route_depth`) on the synthetic
worlds, where the geometric break depth is known.  The frozen definition is:

* play frame from `play_direction`, origin at the snap;
* centred rolling-median smoothing (the existing ceiling smoother);
* heading over a 0.2 s window;
* a >= 0.8 s stem;
* break = cumulative |dtheta| >= 35 deg in 0.4 s (D62 relaxation of 45 deg in
  0.3 s), new heading within 20 deg for 0.3 s; timestamp = first frame of the
  sustained change;
* depth = downfield displacement at that frame;
* no break => `break_found = False`, depth MISSING (never imputed);
* a break after `pass_forward` is flagged, not deleted.

The fixture plants exact two-segment routes; `planted_break_depth_yd` is the
geometric truth, so recovery is a real check, not a tautology.  Two families
plant *shallow* corners (SLANT 38 deg, CORNER -42 deg) inside the 35-45 deg
band the relaxation exists to resolve; the rest are 60-120 deg.  The shallow
band is reported separately because that is where the estimator's resolution
limit bites.
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

#: Planted corners in the shallow 35-45 deg band (the D62 resolution band).
SHALLOW_BAND_DEG = 45.0


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


def _planted(table, world):
    """Attach the planted depth and |angle| and keep planted-break rows."""
    truth = _truth_map(world)
    merged = table.copy()
    merged["truth_depth"] = [truth[(g, p, n)]["planted_break_depth_yd"]
                             for g, p, n in zip(merged["game_id"],
                                                merged["play_id"],
                                                merged["nfl_id"])]
    merged["truth_angle"] = [
        abs(truth[(g, p, n)]["planted_post_break_angle_deg"])
        for g, p, n in zip(merged["game_id"], merged["play_id"],
                           merged["nfl_id"])]
    return merged[merged["truth_depth"].notna()]


def test_config_break_constants_are_the_chosen_relaxation(cfg):
    """D62 changed exactly two constants: 35 deg and 0.4 s."""
    p = RD.params(cfg)
    assert p["break_angle_deg"] == 35.0
    assert p["break_window_s"] == 0.4
    # Everything else stays where D61 put it.
    assert p["heading_window_s"] == 0.2
    assert p["stem_min_s"] == 0.8
    assert p["sustain_window_s"] == 0.3
    assert p["sustain_angle_deg"] == 20.0
    assert p["min_speed_yds"] == 2.0
    assert int(p["smoothing_window_frames"]) == int(
        C.lc(cfg)["thresholds"]["smoothing"]["window_frames"]) == 5


def test_planted_break_depths_recovered(world, table):
    """The detector recovers planted breaks within 1 yd, shallow band included.

    The 35-45 deg band is reported separately and honestly: it is the band the
    D62 relaxation exists to resolve, and it is where the estimator's
    resolution limit bites.  At 35 deg/0.4 s the fixture recovers 53/54 shallow
    corners (98.1%); the single miss is a 38 deg corner whose measured
    cumulative turn peaks at ~33 deg, just under the threshold.  Depth error in
    the shallow band is roughly 4x the high-angle error (median 0.36 vs 0.08
    yd), still inside the 1 yd tolerance.  Neither number is hidden.
    """
    planted = _planted(table, world)
    assert len(planted) > 100, "fixture must plant a meaningful number of breaks"

    shallow = planted[planted["truth_angle"] < SHALLOW_BAND_DEG]
    clear = planted[planted["truth_angle"] >= 60.0]
    assert len(shallow) > 40, "fixture must exercise the 35-45 deg band"
    assert len(clear) > 100

    # High-angle corners: every one detected, comfortably inside tolerance.
    assert clear["break_found"].all(), (
        "planted high-angle breaks missed: "
        f"{clear.loc[~clear['break_found'], ['game_id', 'play_id']].to_dict('records')[:5]}")
    clear_errors = (clear["break_depth_yd"] - clear["truth_depth"]).abs()
    assert clear_errors.max() <= TOLERANCE_YD, clear_errors.describe()
    assert clear_errors.median() <= 0.5, clear_errors.median()

    # Shallow band: near-total, and the floor is asserted rather than assumed.
    shallow_rate = float(shallow["break_found"].mean())
    assert shallow_rate >= 0.95, (
        f"shallow 35-45 deg recovery {shallow_rate:.3f} below the 0.95 floor")
    shallow_errors = (shallow.loc[shallow["break_found"], "break_depth_yd"]
                      - shallow.loc[shallow["break_found"], "truth_depth"]).abs()
    assert shallow_errors.max() <= TOLERANCE_YD, shallow_errors.describe()
    assert shallow_errors.median() <= 0.5, shallow_errors.median()
    # A detected break always carries a depth; a missed one never does.
    assert planted.loc[planted["break_found"], "break_depth_yd"].notna().all()
    assert planted.loc[~planted["break_found"], "break_depth_yd"].isna().all()


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


def test_pre_throw_count_requires_a_known_throw_frame():
    """D64: unknown-throw breaks are not pre-throw breaks.

    Reproduces the sample counts the defect was found on: 3,054 analysed
    snaps, 448 found breaks, 401 with a known throw frame, 164 of them
    before the throw.  The 47 found breaks with no known throw frame are a
    third category; the old mask (``found & ~break_after_throw``) counted
    them as pre-throw and reported 211.
    """
    n_pre, n_after, n_unknown, n_no_break = 164, 237, 47, 2606
    n_found = n_pre + n_after + n_unknown
    rows = pd.DataFrame({
        "break_found": [True] * n_found + [False] * n_no_break,
        "throw_known": ([True] * (n_pre + n_after)
                        + [False] * (n_unknown + n_no_break)),
        "break_after_throw": ([False] * n_pre + [True] * n_after
                              + [False] * (n_unknown + n_no_break)),
        "break_depth_yd": np.arange(n_found + n_no_break, dtype=float),
        "flags": [""] * (n_found + n_no_break),
    })
    # Sanity: the old expression is the 211 the defect reported.
    old_mask = rows["break_found"] & ~rows["break_after_throw"]
    assert int(old_mask.sum()) == 211

    summary = RD.coverage_summary(rows)
    assert summary["n_break_before_throw"] == n_pre == 164
    assert summary["n_break_after_throw"] == n_after
    # The partition is exhaustive and disjoint.
    unknown = int((rows["break_found"] & ~rows["throw_known"]).sum())
    assert (summary["n_break_before_throw"] + summary["n_break_after_throw"]
            + unknown) == summary["n_break_found"] == n_found
    # Denominator is unchanged (all analysed snaps), so the rate is stable.
    assert summary["break_rate_before_throw"] == pytest.approx(164 / 3054)


def test_threshold_specification_curve_is_stable_on_truth(world, cfg, table):
    """The chosen 35 deg/0.4 s row is primary and is not threshold art.

    The declared curve is +/-10 deg on the angle crossed with +/-0.1 s on the
    window.  A planted corner whose angle is below a spec's threshold is
    *definitionally* invisible to that spec, so recovery is judged against the
    breaks that spec can see (planted angle >= threshold).  On that basis:

    * the primary row (35 deg/0.4 s, flagged ``primary``) has the highest
      overall recovery -- 203/204 planted (99.5%), the one miss a 38 deg
      corner whose measured turn peaks just under 35 deg;
    * every resolvable planted break is found at 45 and 55 deg (the shallow
      band falls below those thresholds by construction);
    * the short window is the fragile axis at the relaxed angle: 35 deg/0.2 s
      recovers 86.8%, so the +0.1 s window is doing real work rather than
      propping up an arbitrary angle;
    * depths of the breaks that ARE found stay put (max error <= 1 yd, median
      spread <= 0.75 yd): the answer is not manufactured by the threshold.
    """
    truth = _truth_map(world)
    grid_specs = RD.sensitivity_grid(cfg)
    primary = [s for s in grid_specs if s["primary"]]
    assert len(primary) == 1, "exactly one declared row is the primary"
    assert primary[0]["break_angle_deg"] == 35.0
    assert primary[0]["break_window_s"] == 0.4

    rows = []
    depths_by_spec = {}
    for spec in grid_specs:
        grid = RD.route_depth_table(world["tracking"], world["direction"], cfg,
                                    overrides=spec)
        planted = _planted(grid, world)
        found = planted["break_found"].astype(bool)
        angle = planted["truth_angle"].to_numpy(dtype=float)
        resolvable = angle >= float(spec["break_angle_deg"])
        error = (planted["break_depth_yd"] - planted["truth_depth"]).abs()
        rows.append({
            "spec": spec,
            "angle": float(spec["break_angle_deg"]),
            "window": float(spec["break_window_s"]),
            "primary": bool(spec["primary"]),
            "n_planted": int(len(planted)),
            "recovered": float(found.mean()),
            "resolvable_recovered": float(found[resolvable].mean())
            if resolvable.any() else float("nan"),
            "n_resolvable": int(resolvable.sum()),
            "n_lost": int((~found).sum()),
            "break_rate": float(grid["break_found"].mean()),
            "median_depth": float(planted.loc[found, "break_depth_yd"].median()),
            "max_abs_error": float(error.max()),
        })
        keys = list(zip(planted.loc[found, "game_id"], planted.loc[found, "play_id"],
                        planted.loc[found, "nfl_id"]))
        depths_by_spec[(float(spec["break_angle_deg"]),
                        float(spec["break_window_s"]))] = dict(zip(
                            keys, planted.loc[found, "break_depth_yd"]))
    frame = pd.DataFrame(rows)

    chosen = frame[frame["primary"]].iloc[0]
    assert chosen["recovered"] >= 0.99, chosen
    assert chosen["n_lost"] <= 1, chosen
    assert chosen["max_abs_error"] <= TOLERANCE_YD, chosen
    # The chosen row must not be dominated by any other declared row.
    assert np.isclose(frame["recovered"].max(), chosen["recovered"]), frame

    # Among breaks a spec can see, the angle-only relaxations lose nothing
    # (the 0.2 s window is the fragile axis and is checked separately).
    wide = frame[(frame["angle"] >= 45.0) & (frame["window"] >= 0.3)]
    assert wide["resolvable_recovered"].min() >= 0.99, wide
    # The window axis is real: the same angle in 0.2 s loses much more.
    short = frame[(frame["angle"] == 35.0) & (frame["window"] == 0.2)].iloc[0]
    assert short["recovered"] < chosen["recovered"] - 0.10, short
    assert frame["recovered"].min() >= 0.65, frame
    assert frame["max_abs_error"].max() <= TOLERANCE_YD
    # Depth does not MOVE with the threshold: on the breaks every spec detects,
    # the median depth spread across the whole curve stays under 0.75 yd.  (Per
    # spec medians differ mainly because a stricter spec drops different
    # routes, which is a coverage effect, not a depth shift.)
    common = set.intersection(*(set(d) for d in depths_by_spec.values()))
    assert len(common) > 100
    common_medians = [float(np.median([d[key] for key in common]))
                      for d in depths_by_spec.values()]
    assert max(common_medians) - min(common_medians) <= 0.75, common_medians


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
    # 3 of 288 tracks (1.0%) flip at the relaxed threshold: two 38 deg corners
    # sitting on the 35 deg line, and one 120 deg HITCH whose sustained-heading
    # test is crossed by the jitter.  That is the price of resolving the shallow
    # band; the bound is stated, not hidden.
    assert flips <= 0.02 * len(base_frame), (
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
