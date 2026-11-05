"""DL ceiling core: planted-curve recovery, shrinkage, ICC, VIF, lateral, determinism.

Every test builds small in-memory frames and a local ``bdb27_dl`` config dict;
nothing here reads the raw files (the single integration test skips cleanly when
the real Combine file is absent).  The config values mirror ``config.yaml`` —
they are the a-priori design constants, not tuned numbers.
"""

from __future__ import annotations

import copy
import json
import math

import numpy as np
import pandas as pd
import pytest

from dl import dl_ceiling_core as core
from dl import stage_02_ceiling as stage02

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
CFG = {
    "bdb27_dl": {
        "positions": {"dl": ["DE", "DT", "NT"], "edge": ["EDGE"],
                      "interior": ["INTERIOR_LINE"], "ol": ["G", "T", "C"],
                      "qb": ["QB"]},
        "thresholds": {
            "speed_cap_yds": 12.0,
            "combine_sampling_hz": 10,
            "combine_dt_tolerance_s": 0.02,
            "smoothing": {"method": "centered_rolling_median", "window_frames": 5},
            "peak_window": {"frac_of_max": 0.9, "pad_s": 0.2},
            "gap": {"interp_max_s": 0.5, "interpolate_inside_peak_window": False},
            "icc_min": 0.50,
            "vif_max": 5.0,
            "max_features": 3,
            "min_attempts_for_icc": 2,
            "min_frames": 20,
            "min_r2": 0.90,
        },
        "ceiling": {
            "fit_drills": ["FORTY_YARD_DASH"],
            "lateral_drills": ["SHORT_SHUTTLE", "THREE_CONE_DRILL"],
            "model": "vmax * (1 - exp(-t / tau))",
            "a0_definition": "vmax_over_tau",
            "bounds": {"vmax_yds": [4.0, 13.0], "tau_s": [0.3, 3.0]},
            "initial": {"vmax_frac_of_peak": 1.05, "tau_s": 1.4},
            "candidate_features": ["a0", "vmax", "tau", "speed_at_1_0",
                                   "speed_at_2_0", "ten_yd_split", "peak_accel"],
            "primary_feature": "a0",
            "fallback_feature": "speed_at_1_0",
            "lateral": {"entry_window_s": 0.3, "exit_window_s": 0.3,
                        "braking_min_drop_yds": 1.0},
        },
    }
}


def cfg(**overrides) -> dict:
    """Deep copy of the test config with ``section__key`` overrides."""
    clone = copy.deepcopy(CFG)
    for key, value in overrides.items():
        section, _, leaf = key.partition("__")
        clone["bdb27_dl"].setdefault(section, {})[leaf] = value
    return clone


def curve(t, vmax=9.0, tau=1.2):
    return vmax * (1.0 - np.exp(-np.asarray(t, dtype=float) / tau))


def frames_for(series, drill="FORTY_YARD_DASH", start_ms=0.0):
    """``series``: list of (nfl_id, attempt, vmax, tau, n_frames, noise_sd)."""
    rows = []
    for nfl_id, attempt, vmax, tau, n_frames, noise in series:
        rng = np.random.default_rng(1000 + nfl_id * 7 + attempt)
        t = np.arange(n_frames, dtype=float) / 10.0
        v = curve(t, vmax, tau) + rng.normal(0.0, noise, n_frames)
        # Straight-line positions consistent with the speed curve.
        d = vmax * (t - tau * (1.0 - np.exp(-t / tau)))
        rows.append(pd.DataFrame({
            "nfl_id": nfl_id,
            "draft_year": 2025,
            "drill_name": drill,
            "attempt": attempt,
            "event_id": f"ev{nfl_id}_{attempt}",
            "ms": start_ms + t * 1000.0,
            "s": v,
            "a": np.gradient(v, t) if n_frames > 1 else np.zeros(n_frames),
            "x": d,
            "y": np.zeros(n_frames),
        }))
    return pd.concat(rows, ignore_index=True)


# --------------------------------------------------------------------------- #
# Curve recovery
# --------------------------------------------------------------------------- #
def test_recovers_planted_curve():
    df = frames_for([(1, 1, 9.0, 1.2, 60, 0.0)])
    fits, summary = core.fit_frames(df, CFG)
    assert summary["n_fit"] == 1
    fit = fits[0]
    assert abs(fit.vmax - 9.0) / 9.0 < 0.02
    assert abs(fit.tau - 1.2) / 1.2 < 0.02
    assert fit.r2 > 0.99
    # speed_at_1_0 must come from the fitted curve, not from the samples
    assert fit.speed_at(1.0) == pytest.approx(
        curve(1.0, fit.vmax, fit.tau), rel=1e-12)
    assert abs(fit.speed_at(1.0) - curve(1.0)) < 0.02


def test_a0_is_vmax_over_tau_with_independent_errors():
    df = frames_for([(2, 1, 9.5, 1.1, 70, 0.02)])
    fit = core.fit_frames(df, CFG)[0][0]
    assert fit.a0 == pytest.approx(fit.vmax / fit.tau, rel=1e-12)
    assert math.isfinite(fit.se_log_a0) and fit.se_log_a0 > 0
    assert fit.se_log_a0 == pytest.approx(
        math.hypot(fit.se_log_vmax, fit.se_log_tau), rel=1e-12)


def test_standard_errors_shrink_with_more_frames():
    short = core.fit_frames(frames_for([(3, 1, 9.0, 1.2, 30, 0.05)]), CFG)[0][0]
    long = core.fit_frames(frames_for([(4, 1, 9.0, 1.2, 120, 0.05)]), CFG)[0][0]
    assert short.se_log_vmax > long.se_log_vmax
    assert short.se_log_tau > long.se_log_tau
    assert short.se_log_a0 > long.se_log_a0
    for se in (short.se_log_vmax, short.se_log_tau, long.se_log_vmax,
               long.se_log_tau):
        assert se > 0


def test_r2_rejection_and_min_frames_rejection():
    # A rise-then-fall profile cannot be described by a saturating curve.
    n = 40
    t = np.arange(n) / 10.0
    bad = pd.DataFrame({
        "nfl_id": 5, "draft_year": 2025, "drill_name": "FORTY_YARD_DASH",
        "attempt": 1, "event_id": "b1", "ms": t * 1000.0,
        "s": 9.0 * np.sin(np.pi * t / 4.0),
        "a": np.gradient(9.0 * np.sin(np.pi * t / 4.0), t),
        "x": np.cumsum(9.0 * np.sin(np.pi * t / 4.0)) * 0.1,
        "y": np.zeros(n),
    })
    short = pd.DataFrame({
        "nfl_id": 6, "draft_year": 2025, "drill_name": "FORTY_YARD_DASH",
        "attempt": 1, "event_id": "s1", "ms": np.arange(5) * 100.0,
        "s": curve(np.arange(5) / 10.0), "a": np.ones(5),
        "x": np.arange(5) / 10.0, "y": np.zeros(5),
    })
    fits, summary = core.fit_frames(pd.concat([bad, short], ignore_index=True), CFG)
    assert fits == []
    assert summary["n_low_r2"] == 1
    assert summary["n_too_short"] == 1


def test_speed_cap_is_replaced_not_clipped():
    df = frames_for([(7, 1, 9.0, 1.2, 60, 0.0)])
    df.loc[30, "s"] = 40.0                      # impossible spike
    v, flags, n_above = core.smooth_speed(df["s"].to_numpy(), CFG)
    assert n_above == 1 and "speed_above_cap" in flags
    assert v[30] < 13.0                          # interpolated, not clamped at cap
    assert abs(v[30] - v[29]) < 1.0
    fits, summary = core.fit_frames(df, CFG)
    assert summary["n_speed_above_cap"] == 1
    assert abs(fits[0].vmax - 9.0) / 9.0 < 0.02


def test_gap_inside_peak_window_is_flagged_not_bridged():
    df = frames_for([(8, 1, 9.0, 1.2, 60, 0.0)])
    keep = np.ones(len(df), dtype=bool)
    keep[58] = False                             # 0.2 s hole at the peak (t≈5.9)
    holed = df.loc[keep].reset_index(drop=True)
    _, summary = core.fit_frames(holed, CFG)
    assert summary["n_peak_window_gap"] == 1


def test_determinism_identical_input_identical_fits():
    df = frames_for([(9, 1, 9.3, 1.25, 55, 0.03), (9, 2, 9.1, 1.3, 58, 0.03),
                     (10, 1, 8.4, 1.4, 50, 0.03)])
    first, _ = core.fit_frames(df.copy(), CFG)
    second, _ = core.fit_frames(df.copy(), CFG)
    assert [f.as_row() for f in first] == [f.as_row() for f in second]


# --------------------------------------------------------------------------- #
# Pooling / shrinkage
# --------------------------------------------------------------------------- #
def _population(n_players=20, attempts=2, between=0.10, noise=0.02, seed=7):
    rng = np.random.default_rng(seed)
    out = {}
    for pid in range(1, n_players + 1):
        theta = 8.0 * math.exp(rng.normal(0.0, between))
        pairs = []
        for _ in range(attempts):
            pairs.append((theta * math.exp(rng.normal(0.0, noise)), 0.05))
        out[pid] = pairs
    return out


def test_shrinkage_pulls_noisy_player_toward_population_mean():
    pairs = _population()
    pairs[1] = [(13.0, 3.0), (11.0, 3.0)]        # huge SE, far from the mean
    pooled = core.pool_feature(pairs, "vmax", CFG)
    row = next(r for r in pooled["rows"] if r["nfl_id"] == 1)
    raw_mean = math.sqrt(13.0 * 11.0)             # geometric mean of the attempts
    assert row["reliability"] < 0.5
    assert (abs(math.log(row["estimate"]) - pooled["grand"])
            < abs(math.log(raw_mean) - pooled["grand"]))
    assert row["se"] > 0
    # A precise player is barely moved.
    precise = next(r for r in pooled["rows"] if r["nfl_id"] != 1 and r["n_attempts"] == 2)
    assert precise["reliability"] > 0.5


def test_single_attempt_player_still_gets_an_estimate():
    pairs = _population(n_players=10)
    pairs[99] = [(9.0, float("nan"))]             # one attempt, no SE
    pooled = core.pool_feature(pairs, "vmax", CFG)
    row = next(r for r in pooled["rows"] if r["nfl_id"] == 99)
    assert math.isfinite(row["estimate"]) and row["estimate"] > 0
    assert math.isfinite(row["se"]) and row["se"] > 0
    assert 0.0 <= row["reliability"] < 1.0
    # ... but it must be excluded from the ICC, which needs repeats.
    assert pooled["n_players_excluded_icc"] == 1
    assert pooled["n_players_icc"] == 10
    assert pooled["n_attempts_icc"] == 20


def test_icc_recovers_planted_between_player_variance():
    between, within = 0.25, 0.60
    icc_true = between ** 2 / (between ** 2 + within ** 2)
    rng = np.random.default_rng(11)
    pairs = {}
    for pid in range(1, 61):
        theta = 8.0 + rng.normal(0.0, between)   # one-way random-effects model
        pairs[pid] = [(theta + rng.normal(0.0, within), float("nan"))
                      for _ in range(3)]
    pooled = core.pool_feature(pairs, "vmax", CFG)
    assert pooled["icc"] == pytest.approx(icc_true, abs=0.15)
    assert 0.0 <= pooled["icc"] <= 1.0


def test_icc_needs_two_groups_and_two_attempts():
    assert core.icc_oneway([[1.0], [2.0]]) == 0.0
    assert core.icc_oneway([[1.0, 1.0], [2.0, 2.0]]) == pytest.approx(1.0)
    assert core.icc_oneway([[1.0, 1.0, 1.0]]) == 0.0


def test_ten_yd_split_icc_is_reported_as_not_estimable():
    pairs = {pid: [(1.60, 0.06), (1.60, 0.06)] for pid in range(1, 6)}
    pooled = core.pool_feature(pairs, "ten_yd_split", CFG, icc_estimable=False)
    assert math.isnan(pooled["icc"])


# --------------------------------------------------------------------------- #
# VIF screen
# --------------------------------------------------------------------------- #
def _pooled_from_estimates(estimates, icc):
    rows = [{"nfl_id": pid, "feature": "x", "estimate": v, "se": 0.01,
             "n_attempts": 2, "icc": icc, "reliability": 0.5}
            for pid, v in estimates.items()]
    return {"rows": rows, "icc": icc}


def test_primary_is_admitted_first_and_never_displaced_by_a_secondary():
    rng = np.random.default_rng(3)
    base = {pid: 9.0 * math.exp(rng.normal(0.0, 0.1)) for pid in range(1, 21)}
    # a0 (the primary) is perfectly collinear with speed_at_2_0 and has the
    # LOWEST ICC of the pool: it must still be admitted and kept.
    pooled = {
        "a0": _pooled_from_estimates({p: v * 1.2 for p, v in base.items()}, 0.55),
        "speed_at_2_0": _pooled_from_estimates(base, 0.90),
        "speed_at_1_0": _pooled_from_estimates(
            {p: v * 0.80 for p, v in base.items()}, 0.80),
        "ten_yd_split": _pooled_from_estimates(
            {p: 1.7 - 0.01 * v for p, v in base.items()}, 0.70),
    }
    screen = core.select_features(pooled, core.SELECTION_POOL, CFG)
    assert screen["primary"] == "a0"
    assert screen["primary_selected"] is True
    assert screen["selected"][0] == "a0"
    assert "a0" in screen["selected"]
    assert screen["dropped"]["speed_at_2_0"] == "vif_at_admission"
    assert all(v <= CFG["bdb27_dl"]["thresholds"]["vif_max"]
               for v in screen["vif"].values())


def test_primary_below_icc_floor_is_not_selected_and_no_fallback_is_adopted():
    rng = np.random.default_rng(5)
    base = {pid: 9.0 * math.exp(rng.normal(0.0, 0.1)) for pid in range(1, 21)}
    pooled = {
        "a0": _pooled_from_estimates(base, 0.30),
        "speed_at_1_0": _pooled_from_estimates(
            {p: v * 0.80 for p, v in base.items()}, 0.85),
        "speed_at_2_0": _pooled_from_estimates(
            {p: v * 0.95 for p, v in base.items()}, 0.80),
        "ten_yd_split": _pooled_from_estimates(
            {p: 1.7 - 0.01 * v for p, v in base.items()}, 0.70),
    }
    screen = core.select_features(pooled, core.SELECTION_POOL, CFG)
    assert screen["primary_selected"] is False
    assert "a0" not in screen["selected"]
    assert screen["dropped"]["a0"] == "icc_below_floor"
    # Whether a secondary clears the screen is reported, but it never becomes
    # the primary: the gate is what carries the failure.
    assert screen["selected"]


def test_vif_screen_respects_max_features_and_icc_floor():
    rng = np.random.default_rng(4)
    pooled = {}
    order = ["a0", "vmax", "tau", "speed_at_1_0"]
    for i, name in enumerate(order):
        pooled[name] = _pooled_from_estimates(
            {pid: 5.0 + 2.0 * i + float(rng.normal()) for pid in range(1, 31)},
            icc=0.9 - 0.1 * i)
    screen = core.select_features(pooled, order, cfg(thresholds__max_features=2))
    assert len(screen["selected"]) == 2
    # A feature below the ICC floor is never admitted.
    pooled["tau"] = _pooled_from_estimates(
        {pid: 1.2 for pid in range(1, 31)}, icc=0.10)
    screen = core.select_features(pooled, order, CFG)
    assert "tau" in screen["ineligible"]
    assert "tau" not in screen["selected"]


def test_within_player_correlation_is_not_estimable_at_two_attempts():
    # Two attempts per player: a within-player correlation is identically +-1,
    # so the only honest report is "not estimable" (D69(4)).
    df = frames_for([(20, 1, 8.0, 1.4, 60, 0.0), (20, 2, 10.0, 1.4, 60, 0.0),
                     (21, 1, 7.5, 1.4, 60, 0.0), (21, 2, 9.5, 1.4, 60, 0.0)])
    fits, _ = core.fit_frames(df, CFG)
    corr = core.within_player_corr(fits, "vmax", "a0")
    assert corr["estimable"] is False
    assert corr["n_players"] == 0
    assert corr["n_players_total"] == 2
    assert math.isnan(corr["mean_within_r"])
    assert ">=" in corr["reason"] and "attempts" in corr["reason"]


def test_within_player_correlation_uses_only_players_with_three_attempts():
    # Player 20 has 3 attempts (usable); player 21 has 2 (identically +-1, so
    # it must be excluded from the estimate rather than averaged in).
    df = frames_for([(20, 1, 8.0, 1.4, 60, 0.05), (20, 2, 9.0, 1.4, 60, 0.05),
                     (20, 3, 10.0, 1.4, 60, 0.05),
                     (21, 1, 7.5, 1.4, 60, 0.05), (21, 2, 9.5, 1.4, 60, 0.05)])
    fits, _ = core.fit_frames(df, CFG)
    corr = core.within_player_corr(fits, "vmax", "a0")
    assert corr["estimable"] is True
    assert corr["n_players"] == 1
    assert corr["n_players_total"] == 2
    assert corr["mean_within_r"] > 0.9


def test_between_player_corr_recovers_planted_correlation():
    # 20 players, planted r = 0.8 between the shrunken estimates of two
    # features (natural scale, as the statistic is defined).
    planted = 0.8
    rng = np.random.default_rng(21)
    z = rng.normal(0.0, 1.0, 20)
    eps = rng.normal(0.0, 1.0, 20)
    x = {pid: 8.0 * math.exp(0.10 * z[i]) for i, pid in enumerate(range(1, 21))}
    y = {pid: 3.0 * math.exp(0.10 * (planted * z[i]
                                     + math.sqrt(1.0 - planted ** 2) * eps[i]))
         for i, pid in enumerate(range(1, 21))}
    pooled = {"a0": _pooled_from_estimates(x, 0.6),
              "vmax": _pooled_from_estimates(y, 0.9)}
    corr = core.between_player_corr(pooled, "vmax", "a0")
    assert corr["method"] == "between_player_shrunken"
    assert corr["n_players"] == 20
    assert abs(corr["r"] - planted) < 0.15


def test_select_features_is_deterministic():
    rng = np.random.default_rng(9)
    base = {pid: 9.0 * math.exp(rng.normal(0.0, 0.1)) for pid in range(1, 26)}
    pooled = {
        "a0": _pooled_from_estimates(base, 0.60),
        "vmax": _pooled_from_estimates(
            {p: v * 1.3 for p, v in base.items()}, 0.92),
        "tau": _pooled_from_estimates(
            {p: v * (1.0 + 0.05 * math.sin(p)) for p, v in base.items()}, 0.55),
        "peak_accel": _pooled_from_estimates(
            {p: 20.0 + 3.0 * math.cos(p) for p in base}, 0.62),
    }
    first = core.select_features(pooled, core.SELECTION_POOL, CFG)
    second = core.select_features(pooled, core.SELECTION_POOL, CFG)
    assert first == second


# --------------------------------------------------------------------------- #
# Lateral mechanics
# --------------------------------------------------------------------------- #
def _lateral_frames(nfl_id=30, attempt=1, peak=8.0, trough=4.0):
    """Plant: accelerate to ``peak``, brake to ``trough`` over 2.0 yd of path.

    ``x`` is a monotone path coordinate whose per-frame stride is 0.02 yd,
    except across the braking segment (frames 39-64) where each stride is
    0.08 yd, so the path between the speed maximum and the next local minimum
    is exactly 2.0 yd.
    """
    t = np.arange(80) / 10.0
    v = np.concatenate([
        np.linspace(1.0, peak, 40),
        np.linspace(peak, trough, 25),
        np.full(15, trough),
    ])
    i_peak, i_min = 39, 64
    strides = np.full(len(v) - 1, 0.02)
    strides[i_peak:i_min] = 2.0 / (i_min - i_peak)
    x = np.concatenate([[0.0], np.cumsum(strides)])
    return pd.DataFrame({
        "nfl_id": nfl_id, "draft_year": 2025, "drill_name": "SHORT_SHUTTLE",
        "attempt": attempt, "event_id": f"lat{nfl_id}", "ms": t * 1000.0,
        "s": v, "a": np.gradient(v, t), "x": x, "y": np.zeros(len(v)),
    })


def test_lateral_mechanics_recover_planted_braking_distance():
    df = _lateral_frames()
    out = core.lateral_mechanics(df, CFG)
    assert len(out["attempts"]) == 1
    rec = out["attempts"][0]
    assert rec["peak_speed"] == pytest.approx(8.0, abs=0.4)
    assert rec["braking_distance_yds"] == pytest.approx(2.0, rel=0.25)
    assert rec["entry_speed"] > 0 and rec["exit_speed"] > 0
    assert rec["total_time_s"] == pytest.approx(7.9, abs=0.2)
    assert "shuttle_braking_distance" in out["pairs"]
    assert out["coverage"]["shuttle_braking_distance"]["n_attempts"] == 1


def test_lateral_mechanics_null_braking_when_drop_is_too_small():
    df = _lateral_frames(peak=8.0, trough=7.6)   # only 0.4 yd/s of braking
    out = core.lateral_mechanics(df, CFG)
    rec = out["attempts"][0]
    assert rec["braking_distance_yds"] is None
    assert rec["se_braking_distance_yds"] != rec["se_braking_distance_yds"]  # NaN
    cov = out["coverage"]["shuttle_braking_distance"]
    assert cov["n_missing"] == 1 and cov["n_attempts"] == 0


def test_lateral_mechanics_skips_short_series():
    df = _lateral_frames().head(10)
    out = core.lateral_mechanics(df, CFG)
    assert out["attempts"] == []


# --------------------------------------------------------------------------- #
# Integration (real file, skipped when absent)
# --------------------------------------------------------------------------- #
def test_real_combine_file_smoke():
    from dl import dl_common as D

    real = D.load_config()
    if not D.source_paths(real, "combine_tracking")[0].exists():
        pytest.skip("real Combine tracking file not present")
    frames = core.read_attempt_frames(real, ["FORTY_YARD_DASH"])
    assert {"nfl_id", "ms", "s", "x", "y"} <= set(frames.columns)
    fits, summary = core.fit_frames(frames.head(6000), real)
    assert summary["n_attempt_series"] > 0
    if fits:
        assert all(math.isfinite(f.vmax) and f.vmax > 0 for f in fits)
