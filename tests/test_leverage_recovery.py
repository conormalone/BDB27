"""Synthetic recovery: planted leverage, true null, and the time/load confound.

These tests run the same effort-extraction and within-player estimator the full
pipeline will use.  They are the TASK.md Phase-6 test requirement made concrete:
the planted world must recover its effect, the null must not fire, and the
confounded world's spurious effect must vanish under the orthogonalised model.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import lc_efforts as E
import lc_models as M
import synthetic_lc_fixture as F

NAIVE_CONTROLS = ("route_family", "time_to_throw_s", "score_margin", "down",
                  "yards_to_go")


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    root = tmp_path_factory.mktemp("worlds") / "lc"
    F.generate(root, seed=20270107)
    return root


def _load(root: Path, world: str = ""):
    base = root / "game" / world if world else root / "game"
    pp = pd.read_csv(base / "player_play.csv")
    tr = pd.read_csv(base / "game_tracking.csv")
    pl = pd.read_csv(base / "players.csv")
    efforts, diag = E.extract_efforts(pp, tr, pl)
    return efforts, diag


def test_planted_world_recovers_effect(worlds):
    efforts, diag = _load(worlds, "")
    assert diag["n_efforts"] == 288
    fit = M.fit_within_player(efforts)
    assert fit["n_players"] == 52
    assert abs(fit["beta"] - 0.05) < 0.015, fit
    assert fit["p"] < 0.01
    orth = M.fit_orthogonalised(efforts)
    assert abs(orth["beta"] - 0.05) < 0.015


def test_null_world_does_not_fire(worlds):
    efforts, _ = _load(worlds, "null")
    fit = M.fit_within_player(efforts)
    assert abs(fit["beta"]) < 0.005, fit
    assert fit["p"] > 0.05


def test_confounded_world_spurious_effect_is_removed(worlds):
    efforts, _ = _load(worlds, "confounded")
    naive = M.fit_within_player(efforts, controls=NAIVE_CONTROLS)
    assert naive["beta"] < -0.03, "the confound should look like a real effect"
    assert naive["p"] < 0.01
    orth = M.fit_orthogonalised(efforts, controls=NAIVE_CONTROLS)
    assert abs(orth["beta"]) < 0.015, orth
    assert orth["p"] > 0.05, "orthogonalised model must not keep the confound"


def test_missing_wp_rows_are_dropped_not_imputed(worlds):
    efforts, diag = _load(worlds, "")
    base = worlds / "game"
    pp = pd.read_csv(base / "player_play.csv")
    tr = pd.read_csv(base / "game_tracking.csv")
    pl = pd.read_csv(base / "players.csv")
    route_rows = pp[pp["route_ran"].notna()].index
    pp.loc[route_rows[0], "pre_snap_home_team_win_probability"] = np.nan
    out, diag2 = E.extract_efforts(pp, tr, pl)
    assert diag2["n_missing_wp"] >= 1
    assert len(out) == len(efforts) - 1


def test_one_bin_player_is_flagged_and_harmless(worlds):
    efforts, _ = _load(worlds, "")
    pid = int(efforts["nfl_id"].iloc[0])
    work = efforts.copy()
    work.loc[work["nfl_id"] == pid, "leverage"] = 0.5
    work.loc[work["nfl_id"] == pid, "leverage_z"] = 0.0
    work.loc[work["nfl_id"] == pid, "one_bin_player"] = True
    assert work.loc[work["nfl_id"] == pid, "leverage_z"].abs().max() == 0.0
    fit = M.fit_within_player(work)
    assert np.isfinite(fit["beta"]) and fit["n_players"] == 52


def test_short_tracking_effort_is_excluded(worlds):
    base = worlds / "game"
    pp = pd.read_csv(base / "player_play.csv")
    tr = pd.read_csv(base / "game_tracking.csv")
    pl = pd.read_csv(base / "players.csv")
    efforts_all, _ = E.extract_efforts(pp, tr, pl)
    first = efforts_all.iloc[0]
    mask = ((tr["game_id"] == first["game_id"])
            & (tr["play_id"] == first["play_id"])
            & (tr["nfl_id"] == first["nfl_id"]))
    positions = tr.groupby(["game_id", "play_id", "nfl_id"]).cumcount()
    tr = tr[~mask | (positions < 1)]
    efforts, diag = E.extract_efforts(pp, tr, pl)
    assert diag["n_efforts"] == len(efforts_all) - 1


def test_extraction_is_deterministic(worlds):
    a, _ = _load(worlds, "")
    b, _ = _load(worlds, "")
    pd.testing.assert_frame_equal(a, b)


def test_no_post_snap_field_enters_leverage(worlds):
    efforts, _ = _load(worlds, "")
    # leverage is a pure function of the pre-snap WP column
    recomputed = E.leverage_from_wp(efforts["wp_pre"])
    assert np.allclose(recomputed, efforts["leverage"])
    assert {"target", "pass_result", "yards_gained"}.isdisjoint(efforts.columns)
