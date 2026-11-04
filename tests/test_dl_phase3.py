"""Phase-3 IDR derived columns: units, rest rules, and synthetic recovery.

The recovery tests are the point: a planted rest x load interaction on the
deficit must come back through the production path (power integral -> rolling
window -> envelope fit -> ratio), and a null world must not invent one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_phase3 as P
from dl import dl_phase3_fixture as F


@pytest.fixture(scope="module")
def cfg():
    return D.load_config("config.yaml")


def _write(tmp_path, world):
    track = tmp_path / "tracking.csv"
    pp = tmp_path / "player_play.csv"
    world["tracking"].to_csv(track, index=False)
    world["player_play"].to_csv(pp, index=False)
    return {"tracking": track, "player_play": pp}


def _run(world, cfg, tmp_path):
    files = _write(tmp_path, world)
    frame = world["frame"][["game_id", "team", "play_id", "nfl_id"]].copy()
    out, summary = P.add_phase3_columns(
        frame, cfg, "sample", files=files, baseline=world["baseline"])
    return out, summary


def _interaction_coef(out: pd.DataFrame) -> tuple[float, int]:
    rows = out.dropna(subset=["deficit_ratio",
                              "rolling_kinematic_load_w_per_kg",
                              "inter_snap_rest_s"]).copy()
    if len(rows) < 20:
        return float("nan"), len(rows)
    load = rows["rolling_kinematic_load_w_per_kg"].to_numpy(dtype=float)
    rest = rows["inter_snap_rest_s"].to_numpy(dtype=float)
    z_load = (load - load.mean()) / load.std(ddof=0)
    z_rest = (rest - rest.mean()) / rest.std(ddof=0)
    y = np.log(rows["deficit_ratio"].to_numpy(dtype=float))
    X = np.column_stack([np.ones(len(rows)), z_load, z_rest, z_load * z_rest])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(beta[3]), len(rows)


# --------------------------------------------------------------------------- #
# Units and pure numerics
# --------------------------------------------------------------------------- #
def test_power_integral_is_a_times_v_in_yard_space():
    t = np.array([0.0, 0.5, 1.0])
    v = np.array([3.0, 3.0, 3.0])
    a = np.array([2.0, 2.0, 2.0])
    value, segments = P.power_integral(t, v, a, clip_negative=True, max_dt_s=0.5)
    assert value == pytest.approx(6.0)          # integral of 2*3 over 1 s
    assert segments == 2


def test_power_integral_skips_a_tracking_gap():
    t = np.array([0.0, 0.1, 2.0, 2.1])
    v = np.full(4, 3.0)
    a = np.full(4, 2.0)
    value, segments = P.power_integral(t, v, a, clip_negative=True, max_dt_s=0.5)
    assert value == pytest.approx(2 * 3 * 0.1 * 2)  # 1.9 s gap contributes 0
    assert segments == 2


def test_fit_recovers_a_planted_envelope(cfg):
    t = np.arange(0.0, 1.5 + 1e-9, 0.1)
    vmax, tau = 8.0, 0.9
    v = vmax * (1.0 - np.exp(-t / tau))
    fit = P.fit_rep(t, v, {"phase3": {"fit": dict(D.dl(cfg)["phase3"]["fit"])}})
    assert fit["status"] == "fit_ok"
    assert fit["a0"] == pytest.approx(vmax / tau, rel=0.05)


def test_fit_refuses_a_short_rep(cfg):
    fit = P.fit_rep(np.array([0.0, 0.1]), np.array([0.1, 0.2]),
                    {"phase3": {"fit": dict(D.dl(cfg)["phase3"]["fit"])}})
    assert fit["status"] == "rep_too_short"
    assert np.isnan(fit["a0"])


# --------------------------------------------------------------------------- #
# Rest rules
# --------------------------------------------------------------------------- #
def test_rotation_is_flagged_and_rest_is_never_filled(cfg, tmp_path):
    world = F.build_world(seed=11, regime="null", n_games=1, n_teams=1,
                          n_players_per_team=6, n_plays=8)
    out, summary = _run(world, cfg, tmp_path)
    gapped = out[out["rest_gap_plays"].fillna(0) > 1]
    assert len(gapped) > 0
    # A rotational gap is measured, not imputed: every gapped row still has
    # the real play-to-snap interval.
    assert gapped["inter_snap_rest_s"].notna().all()
    # And the player-game is flagged as a broken sequence.
    assert summary["rest"]["n_rows_in_incomplete_sequence"] >= len(gapped)
    # No rest is ever filled where the previous play has no end timestamp.
    assert out.loc[out["rest_gap_plays"].isna(), "inter_snap_rest_s"].isna().all()


def test_first_snap_has_no_rest_and_is_counted(cfg, tmp_path):
    world = F.build_world(seed=12, regime="null", n_games=1, n_teams=1,
                          n_players_per_team=2, n_plays=6)
    out, summary = _run(world, cfg, tmp_path)
    first = out[out["rest_gap_plays"].isna()]
    assert len(first) == summary["rest"]["n_first_snap_na"]
    assert first["inter_snap_rest_s"].isna().all()


def test_penalty_nullified_rows_are_excluded_and_counted(cfg, tmp_path):
    world = F.build_world(seed=13, regime="null", n_games=1, n_teams=1,
                          n_players_per_team=2, n_plays=8)
    pp = world["player_play"].copy()
    nullified = int(pp["play_id"].iloc[0])
    pp.loc[(pp["play_id"] == nullified)
           & (pp["team_abbr"].str.startswith("T")),
           "play_nullified_by_penalty"] = "Y"
    files = _write(tmp_path, {**world, "player_play": pp})
    out, summary = P.add_phase3_columns(
        world["frame"][["game_id", "team", "play_id", "nfl_id"]].copy(), cfg,
        "sample", files=files, baseline=world["baseline"])
    flagged = out[out["exclusion_reason"].eq("penalty_nullified")]
    assert len(flagged) > 0
    assert flagged["rep_kinematic_load_w_per_kg"].notna().all()  # rep still known
    assert summary["load"]["n_excluded_penalty_nullified"] == len(flagged)


# --------------------------------------------------------------------------- #
# Synthetic recovery
# --------------------------------------------------------------------------- #
def test_pipeline_recovers_the_planted_rest_load_interaction(cfg, tmp_path):
    world = F.build_world(seed=20270107, regime="planted")
    out, summary = _run(world, cfg, tmp_path)
    assert summary["fit"]["n_fit_ok"] > 0
    coef, n = _interaction_coef(out)
    assert n > 100, f"only {n} complete rows"
    assert coef == pytest.approx(world["truth"]["b_int"], abs=0.02), (
        f"recovered {coef:.4f} against planted {world['truth']['b_int']:.4f}")


def test_null_world_does_not_invent_an_interaction(cfg, tmp_path):
    world = F.build_world(seed=20270107, regime="null")
    out, _ = _run(world, cfg, tmp_path)
    coef, n = _interaction_coef(out)
    assert n > 100
    assert abs(coef) < 0.02, f"null world produced {coef:.4f}"


def test_columns_are_additive_and_declared(cfg):
    from dl import dl_snaps as S
    for column in P.PHASE3_COLUMNS:
        assert column in S.SNAP_COLUMNS
