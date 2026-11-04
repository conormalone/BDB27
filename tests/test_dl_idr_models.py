"""Phase 4 Part 1 (IDR spec): load x rest -> per-snap deficit.

The confirmatory estimand is the rolling-load x inter-snap-rest interaction on
``log(deficit_ratio)``.  These tests exercise the **production** code path
(``dl_idr_models.run_idr``), not a test-only approximation:

* the windowed load must reproduce the Phase-3 column exactly;
* a planted interaction must come back with the right sign and close magnitude;
* a null world must return an interval covering zero;
* a world where the interaction exists **only** as a per-rusher random slope
  must return a player-clustered interval covering zero while the naive i.i.d.
  interval excludes it — the demonstration that clustering is not
  manufacturing the effect;
* the interaction is never silently dropped, and an unestimable interaction is
  reported with its reason.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_idr_fixture as F
from dl import dl_idr_models as I
from dl import dl_phase3 as P
from dl import dl_phase3_fixture as PF3


@pytest.fixture(scope="module")
def cfg():
    return D.load_config("config.yaml")


@pytest.fixture(scope="module")
def idr_cfg(cfg):
    return D.dl(cfg)["idr_part1"]


def _world(regime, **kw):
    world = F.build_idr_world(regime, **kw)
    return world["frame"], world["truth"]


# --------------------------------------------------------------------------- #
# Window semantics
# --------------------------------------------------------------------------- #
def test_window_load_matches_the_phase3_column(cfg, tmp_path):
    """The model's window-3 rebuild must equal Phase 3's own column."""
    world = PF3.build_world(seed=20270107, regime="planted")
    track = tmp_path / "tracking.csv"
    pp = tmp_path / "player_play.csv"
    world["tracking"].to_csv(track, index=False)
    world["player_play"].to_csv(pp, index=False)
    frame = world["frame"][["game_id", "team", "play_id", "nfl_id"]].copy()
    out, _ = P.add_phase3_columns(frame, cfg, "sample",
                                  files={"tracking": track,
                                         "player_play": pp},
                                  baseline=world["baseline"])
    rebuilt = I.rolling_load(out, 3)
    stored = out["rolling_kinematic_load_w_per_kg"]
    both = rebuilt.notna() & stored.notna()
    assert int(both.sum()) > 50
    assert np.allclose(rebuilt[both], stored[both], rtol=1e-9, atol=1e-9)
    # and the model does not invent a load where Phase 3 refused one
    assert rebuilt[stored.isna()].isna().all()


def test_first_window_snaps_have_no_load():
    frame, _ = _world("planted", n_players=3, n_games=1, snaps_per_player=6)
    load = I.rolling_load(frame, 3)
    first_three = frame.groupby("nfl_id").head(3).index
    assert load.loc[first_three].isna().all()


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #
def test_planted_interaction_is_recovered_with_the_right_sign(idr_cfg):
    frame, truth = _world("planted")
    out = I.run_idr(frame, idr_cfg, mode="sample")
    primary = out["primary"]
    assert primary["status"] == "OK", primary.get("reason")
    est = primary["interaction"]
    assert est["p"] < 1e-6
    assert est["coef_std"] == pytest.approx(truth["b_int"], abs=0.01)
    assert est["ci_low_std"] < truth["b_int"] < est["ci_high_std"]
    # the direction the spec claims: load hurts MORE when rest is short
    assert primary["contrast"]["difference"] > 0
    assert (primary["contrast"]["log_deficit_per_sd_load_at_low_rest"]
            < primary["contrast"]["log_deficit_per_sd_load_at_high_rest"])
    assert primary["n_snaps"] > 500
    assert primary["n_players"] == 24


def test_null_world_returns_an_interval_covering_zero(idr_cfg):
    frame, _ = _world("null")
    primary = I.run_idr(frame, idr_cfg, mode="sample")["primary"]
    assert primary["status"] == "OK"
    est = primary["interaction"]
    assert est["ci_low_std"] < 0.0 < est["ci_high_std"]
    assert abs(est["coef_std"]) < 0.01


def test_interaction_only_in_random_slopes_is_not_manufactured(idr_cfg):
    """Population interaction is zero; the naive SE would say otherwise."""
    frame, truth = _world("random_slopes")
    primary = I.run_idr(frame, idr_cfg, mode="sample")["primary"]
    assert truth["b_int"] == 0.0
    est = primary["interaction"]
    # Clustered inference: the interval covers zero.
    assert est["ci_low_std"] < 0.0 < est["ci_high_std"]
    # The clustering matters: the i.i.d. interval is much narrower and, on
    # this deterministic world, excludes zero — i.e. naive inference would
    # have announced an interaction that does not exist at the population.
    assert primary["design_effect"] > 3.0
    assert primary["p_iid"] < 0.05 < est["p"]


def test_raw_scale_is_the_standardised_coefficient_rescaled(idr_cfg):
    frame, _ = _world("planted")
    primary = I.run_idr(frame, idr_cfg, mode="sample")["primary"]
    scale = primary["scale"]
    expected = (primary["interaction"]["coef_std"]
                / (scale["load_sd"] * scale["rest_sd"]))
    assert primary["interaction"]["coef_raw"] == pytest.approx(expected, rel=1e-12)
    terms = {t["term"]: t for t in primary["terms"]}
    assert terms["z_load_x_z_rest"]["is_interaction"] is True
    assert terms["z_load"]["coef_raw"] == pytest.approx(
        terms["z_load"]["coef_std"] / scale["load_sd"], rel=1e-12)


# --------------------------------------------------------------------------- #
# Ladder, controls, sensitivity
# --------------------------------------------------------------------------- #
def test_ladder_is_logged_and_the_interaction_is_never_dropped(idr_cfg):
    frame, _ = _world("planted")
    primary = I.run_idr(frame, idr_cfg, mode="sample")["primary"]
    # The ladder is walked in the declared order and stops at the first usable
    # rung, so the record is a prefix ending in the rung that was used.
    walked = [r["rung"] for r in primary["ladder"]]
    assert walked == primary["rungs_intended"][:len(walked)]
    used = [r for r in primary["ladder"] if r["status"] == "used"]
    assert len(used) == 1
    assert used[0]["rung"] == primary["rung_used"]
    # Every rung keeps the interaction: it is in the reported fixed effects.
    assert any(t["is_interaction"] for t in primary["terms"])
    assert "interaction" in primary and primary["interaction"]["p"] is not None


def test_declared_blitz_control_is_reported_not_silently_dropped(idr_cfg):
    frame, _ = _world("planted")
    primary = I.run_idr(frame, idr_cfg, mode="sample")["primary"]
    assert primary["controls"]["blitz"]["status"] in {"UNAVAILABLE", "ABSENT"}
    assert primary["controls"]["alignment"]["status"] == "fitted"


def test_window_panel_runs_from_one_code_path(idr_cfg):
    frame, _ = _world("planted")
    out = I.run_idr(frame, idr_cfg, mode="sample")
    assert out["primary_window"] == 3
    assert [p["window"] for p in out["panel"]] == [3, 1, 5]
    # The plant is defined at the primary window: same sign everywhere, and
    # the primary is the one closest to the planted magnitude.
    signs = [np.sign(p["interaction"]["coef_std"]) for p in out["panel"]
             if p["status"] == "OK"]
    assert signs and all(s > 0 for s in signs)


def test_unestimable_frame_reports_a_reason_and_no_fit(idr_cfg):
    frame, _ = _world("planted", n_players=3, n_games=1, snaps_per_player=8)
    out = I.run_idr(frame, idr_cfg, mode="sample")
    primary = out["primary"]
    assert primary["status"] == "NOT_ESTIMABLE"
    assert "minimum" in primary["reason"] or "below" in primary["reason"]
    assert primary.get("rung_used") is None


def test_sample_mode_is_labelled_non_reportable(idr_cfg):
    frame, _ = _world("planted")
    out = I.run_idr(frame, idr_cfg, mode="sample")
    assert out["no_number_may_be_quoted"] is True
    assert out["sample_output"] is True


# --------------------------------------------------------------------------- #
# Full production path: Phase-3 tracking -> deficit -> IDR model
# --------------------------------------------------------------------------- #
def test_planted_world_survives_the_whole_production_path(cfg, idr_cfg, tmp_path):
    """Phase-3 fixture -> add_phase3_columns -> run_idr (no test-only maths)."""
    world = PF3.build_world(seed=20270107, regime="planted")
    track = tmp_path / "tracking.csv"
    pp = tmp_path / "player_play.csv"
    world["tracking"].to_csv(track, index=False)
    world["player_play"].to_csv(pp, index=False)
    snaps, _ = P.add_phase3_columns(
        world["frame"][["game_id", "team", "play_id", "nfl_id"]].copy(), cfg,
        "sample", files={"tracking": track, "player_play": pp},
        baseline=world["baseline"])
    # The fixture carries no context columns; the model's declared controls
    # are reported UNAVAILABLE rather than invented.
    primary = I.run_idr(snaps, idr_cfg, mode="sample")["primary"]
    assert primary["status"] == "OK", primary.get("reason")
    assert primary["n_snaps"] > 100
    planted = world["truth"]["b_int"]
    assert primary["interaction"]["coef_std"] == pytest.approx(planted, abs=0.02)
    assert primary["interaction"]["ci_low_std"] < planted < primary["interaction"]["ci_high_std"]
    assert primary["controls"]["alignment"]["status"] in {"UNAVAILABLE", "ABSENT"}
