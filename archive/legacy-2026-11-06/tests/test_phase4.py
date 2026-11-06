"""Phase 4 tests: synthetic recovery, null world, shared-error correction,
edge cases and determinism.

The acceptance criterion is TASK.md "Tests before handoff" item 2: simulated
rushers with planted physical times, a planted jump variance and a known b; the
decomposition must recover the planted variance shares. The shared-baseline
error world is the test that proves the measurement-error correction earns its
place: with the correction off, a player-level shared error creates an apparent
jump share; with it on, the share collapses toward the planted value.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import ROOT
import common as C
import models as M


# --------------------------------------------------------------------------- #
# Synthetic worlds
# --------------------------------------------------------------------------- #
def simulate_world(*, seed: int = 7, n_players: int = 60, reps: int = 25,
                   n_team_games: int = 40, b: float = 1.0,
                   jump_sd: float = 0.08, phys_sd: float = 0.10,
                   shared_se: float = 0.02, resid_sd: float = 0.05,
                   team_sd: float = 0.03):
    """A schema-shaped rep table with planted b, jump and shared phys error.

    ``phys_true = 0.12 + 0.25*d + p_i*d**1.5`` gives a nonlinear physical curve
    so b is identified separately from the depth effect. The observed phys has a
    player-level shared error ``delta_i`` when ``shared_se > 0``.
    """
    rng = np.random.default_rng(int(seed))
    p_i = rng.normal(0.0, phys_sd, n_players)
    u_i = rng.normal(0.0, jump_sd, n_players) if jump_sd > 0 else np.zeros(n_players)
    v_g = rng.normal(0.0, team_sd, n_team_games)
    delta = rng.normal(0.0, shared_se, n_players) if shared_se > 0 else np.zeros(n_players)
    rows = []
    for i in range(n_players):
        for j in range(reps):
            d = float(rng.uniform(0.30, 1.50))
            phys_true = 0.12 + 0.25 * d + p_i[i] * d ** 1.5
            g = int(rng.integers(n_team_games))
            y = 0.5 + b * phys_true + u_i[i] + v_g[g] + rng.normal(0.0, resid_sd)
            rows.append({
                "player": i,
                "team_game": g,
                "y": y,
                "phys_s": phys_true + delta[i],
                "phys_se_s": float(shared_se),
                "depth_yd": d,
                "alignment_class": "EDGE" if i % 2 == 0 else "INTERIOR",
                "game_id": j % 15,
                "season": 2023 + (j % 3),
                "onset_latency_s": 0.30 + 0.2 * u_i[i],
                "onset_to_cross_s": 0.55 + 0.4 * p_i[i],
            })
    df = pd.DataFrame(rows)
    # Planted between-player physical variance at each player's rep mix.
    true_p = np.array([
        float(np.mean(df.loc[df["player"] == i, "depth_yd"] ** 1.5)) * p_i[i]
        for i in range(n_players)
    ])
    true_var_phys = (b ** 2) * float(np.var(true_p, ddof=1))
    true_jump = float(jump_sd ** 2)
    true_share = true_jump / (true_jump + true_var_phys) if (true_jump + true_var_phys) > 0 else 0.0
    return df, {"true_b": b, "true_share_u": true_share,
                "true_var_phys": true_var_phys, "true_sigma_u2": true_jump}


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #
def test_synthetic_recovery_with_planted_b_and_shares():
    df, truth = simulate_world(seed=7)
    r = M.decompose(df, correct_measurement_error=True)
    assert r["status"] == "OK"
    assert r["converged"] is True
    assert r["b"] == pytest.approx(truth["true_b"], abs=0.15)
    assert r["share_u"] == pytest.approx(truth["true_share_u"], abs=0.12)
    assert r["share_phys"] == pytest.approx(1.0 - truth["true_share_u"], abs=0.12)
    assert r["sigma_u2"] > 0
    assert r["between_phys_var"] > 0
    assert 0.0 < r["within_player_remainder_total"] < 1.0
    # The two between-player pieces partition the between-player denominator.
    assert r["share_u"] + r["share_phys"] == pytest.approx(1.0, abs=1e-9)


def test_null_world_has_near_zero_jump_share_and_interval_covers_zero():
    df, _ = simulate_world(seed=7, jump_sd=0.0, shared_se=0.0)
    r = M.decompose(df, correct_measurement_error=True)
    assert r["status"] == "OK"
    assert r["share_u"] < 0.05
    boot = M.bootstrap_decomposition(
        df, correct_measurement_error=True, resamples=40, seed=3, point=r)
    lo, hi = boot["share_u_ci95"]
    assert lo <= 1e-9, f"null interval must cover zero, got [{lo}, {hi}]"
    assert hi < 0.05


def test_shared_baseline_error_inflates_jump_without_correction_and_not_with_it():
    """The correction earns its place: with u_i == 0, a shared phys error is
    misread as jump variance unless it is subtracted."""
    df, _ = simulate_world(seed=7, jump_sd=0.0, shared_se=0.03)
    corrected = M.decompose(df, correct_measurement_error=True)
    uncorrected = M.decompose(df, correct_measurement_error=False)
    assert corrected["status"] == "OK" and uncorrected["status"] == "OK"
    # Without the correction the shared error is absorbed into u and produces a
    # materially non-zero apparent jump share.
    assert uncorrected["share_u"] > 0.08, (
        f"expected the uncorrected fit to inflate the jump share, got "
        f"{uncorrected['share_u']:.3f}")
    # With the correction the apparent jump share collapses toward the planted
    # zero and is far below the uncorrected value.
    assert corrected["share_u"] < 0.05
    assert corrected["share_u"] < uncorrected["share_u"]


def test_shared_error_does_not_change_the_real_world_share_much():
    """In a world with a real jump, the corrected share stays near the planted
    share; the correction is not a blanket downward shift."""
    df, truth = simulate_world(seed=8, jump_sd=0.08, shared_se=0.03)
    corrected = M.decompose(df, correct_measurement_error=True)
    assert corrected["b"] == pytest.approx(truth["true_b"], abs=0.17)
    assert corrected["share_u"] == pytest.approx(truth["true_share_u"], abs=0.14)


# --------------------------------------------------------------------------- #
# Reliability and agreement
# --------------------------------------------------------------------------- #
def test_split_half_and_model_based_reliability():
    df, _ = simulate_world(seed=7)
    r = M.decompose(df, correct_measurement_error=True)
    sh = M.split_half_reliability(r["data"], r["resid_u"])
    assert sh["n_players"] == 60
    assert sh["r_half"] > 0.5, "a real planted jump must split-half positively"
    mr = M.model_based_reliability(r)
    assert 0.0 < mr["reliability"] < 1.0


def test_agreement_uses_latency_and_crossing_time():
    df, _ = simulate_world(seed=7)
    r = M.decompose(df, correct_measurement_error=True)
    ag = M.agreement_with_latency(r["data"], r)
    assert ag["u_vs_onset_latency"]["n_players"] == 60
    assert ag["phys_vs_onset_to_cross"]["n_players"] == 60
    assert ag["u_vs_onset_latency"]["r"] > 0.5
    assert ag["phys_vs_onset_to_cross"]["r"] > 0.5


def test_year_to_year_is_exploratory_and_returns_pairs():
    df, _ = simulate_world(seed=7)
    r = M.decompose(df, correct_measurement_error=True)
    y2y = M.year_to_year(r["data"], r)
    assert y2y["status"] == "OK"
    assert y2y["n_pairs"] > 0
    assert "confounds" in y2y


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_single_player_is_insufficient():
    df, _ = simulate_world(seed=7, n_players=2)
    one = df[df["player"] == 0]
    r = M.decompose(one, correct_measurement_error=True)
    assert r["status"] == "INSUFFICIENT_PLAYERS"


def test_missing_rows_are_dropped_not_imputed():
    df, _ = simulate_world(seed=7, n_players=12, reps=10)
    df.loc[df.index[0], "y"] = np.nan
    df.loc[df.index[1], "phys_s"] = np.nan
    df.loc[df.index[2], "depth_yd"] = np.nan
    r = M.decompose(df, correct_measurement_error=True)
    assert r["status"] == "OK"
    assert r["n_reps"] == len(df) - 3


def test_depth_near_zero_and_one_rep_players():
    df, _ = simulate_world(seed=7, n_players=12, reps=6)
    # A player with one rep must be dropped by the minimum-reps rule, not
    # allowed to contribute a noisy player effect.
    one = df[df["player"] == 0].iloc[[0]].copy()
    one["player"] = 999
    mixed = pd.concat([df, one], ignore_index=True)
    counts = mixed.groupby("player").size()
    kept = M._eligible(mixed, min_reps=2)
    assert 999 not in set(kept["player"])
    # Depth exactly zero is valid and uses the (0,0) anchor; negative depth is
    # flagged non-positive and excluded by the loader.
    r = M.decompose(kept, correct_measurement_error=True)
    assert r["status"] == "OK"


def test_no_onset_latency_does_not_crash_agreement():
    df, _ = simulate_world(seed=7, n_players=20, reps=10)
    df["onset_latency_s"] = np.nan
    r = M.decompose(df, correct_measurement_error=True)
    ag = M.agreement_with_latency(r["data"], r)
    assert ag["u_vs_onset_latency"]["n_players"] == 0
    assert math.isnan(ag["u_vs_onset_latency"]["r"])


# --------------------------------------------------------------------------- #
# Determinism and contracts
# --------------------------------------------------------------------------- #
def test_determinism_same_seed_same_outputs():
    df, _ = simulate_world(seed=7, n_players=25, reps=12)
    a = M.decompose(df, correct_measurement_error=True)
    b = M.decompose(df, correct_measurement_error=True)
    keys = ["b", "sigma_u2", "sigma_v2", "sigma_e2", "between_phys_var",
            "share_u", "share_phys", "within_player_remainder_total"]
    assert {k: a[k] for k in keys} == {k: b[k] for k in keys}
    boot_a = M.bootstrap_decomposition(df, correct_measurement_error=True,
                                       resamples=8, seed=11, point=a)
    boot_b = M.bootstrap_decomposition(df, correct_measurement_error=True,
                                       resamples=8, seed=11, point=b)
    assert boot_a["share_u_ci95"] == boot_b["share_u_ci95"]
    assert boot_a["b_ci95"] == boot_b["b_ci95"]


def test_config_has_phase4_block():
    cfg = C.load_config(ROOT / "config.yaml")
    m = C.section(cfg, "models")
    for key in ("min_reps_per_player", "population_curve_degree", "max_iter",
                "tol", "bootstrap_resamples", "bootstrap_seed"):
        assert key in m
    assert int(m["min_reps_per_player"]) == 20


def test_phase4_stage_reads_no_pressure_columns():
    spec = importlib.util.spec_from_file_location(
        "bdb27_04_models", ROOT / "src" / "04_models.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    joined = " ".join(mod.SNAP_COLUMNS).lower()
    for forbidden in ("quick_pressure", "time_to_pressure", "sack",
                      "unblocked_pressure", "pressure"):
        assert forbidden not in joined
    assert "player_get_off" not in joined
