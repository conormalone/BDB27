"""Acceptance tests for the wired effort table (04) and model stage (05).

These cover the three new requirements from D65:

* the effort table has exactly one row per effort key — no duplicate
  ``(game_id, play_id, nfl_id, effort_seq)`` and no duplicate player-play;
* the model stage refuses non-FULL provenance in full mode;
* the convergence fallback ladder is exercised in the declared order.

Plus the D60 MDE identity and the degenerate-table edge case, so the stages
report those instead of crashing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import lc_common as C
import lc_efforts as E
import lc_models as M
import synthetic_lc_fixture as F


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("efforts_stage") / "lc"
    F.generate(root, seed=20270107)
    return root / "game"


@pytest.fixture(scope="module")
def efforts(world, cfg):
    pp = pd.read_csv(world / "player_play.csv")
    tr = pd.read_csv(world / "game_tracking.csv")
    pl = pd.read_csv(world / "players.csv")
    table, _ = E.extract_efforts(pp, tr, pl, cfg=cfg)
    return table


def test_effort_table_has_one_row_per_effort_key(efforts):
    keys = ["game_id", "play_id", "nfl_id", "effort_seq"]
    assert not efforts.duplicated(keys).any()
    # A player can run at most one route per play, so the player-play key is
    # unique too; effort_seq disambiguates the general case.
    assert not efforts.duplicated(["game_id", "play_id", "nfl_id"]).any()
    # effort_seq is a dense 0..n-1 index within each player-game.
    grouped = efforts.groupby(["game_id", "nfl_id"])["effort_seq"]
    assert (grouped.min() == 0).all()
    assert (grouped.max() == grouped.size() - 1).all()


def _load_stage(name: str):
    root = Path(__file__).resolve().parents[1] / "src"
    spec = importlib.util.spec_from_file_location(f"stage_{name}", root / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_stage_refuses_non_full_provenance_in_full_mode(tmp_path, cfg,
                                                              monkeypatch):
    script = _load_stage("05_models")
    sample = pd.read_csv(tmp_path / "efforts.csv") if False else pd.DataFrame({
        "provenance": [f"PLACEHOLDER_SAMPLE: {M.SAMPLE_STAMP}"] * 3,
        "game_id": [1, 1, 2], "play_id": [1, 2, 3], "nfl_id": [10, 10, 11],
        "log_max_speed": [1.0, 1.1, 0.9], "leverage_z": [-1.0, 1.0, 0.0],
    })
    path = tmp_path / "efforts.csv"
    sample.to_csv(path, index=False)
    monkeypatch.setattr(script, "_efforts_path", lambda cfg, mode: path)
    with pytest.raises(RuntimeError, match="not \\['FULL'\\]"):
        script.load_efforts(cfg, "full")
    # Sample mode is allowed and returns the non-reportable table.
    assert len(script.load_efforts(cfg, "sample")) == 3
    # A genuinely FULL table passes the full-mode check.
    full = sample.assign(provenance="FULL")
    full.to_csv(path, index=False)
    assert len(script.load_efforts(cfg, "full")) == 3
    # And the primitive itself records the level.
    assert M.assert_effort_provenance(sample, "sample") == "PLACEHOLDER_SAMPLE"


def _toy_frame(n: int = 20) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    return pd.DataFrame({
        "nfl_id": np.repeat(np.arange(4), n // 4),
        "log_max_speed": rng.normal(size=n),
        "leverage_z": rng.normal(size=n),
        "route_family": ["GO"] * n,
        "break_found": [False] * n,
        "break_depth_yd": [np.nan] * n,
        "time_to_throw_s": rng.normal(size=n),
        "score_margin": rng.normal(size=n),
        "down": [1] * n,
        "yards_to_go": [10] * n,
        "prior_efforts": rng.integers(0, 5, n).astype(float),
        "quarter": [1] * n,
    })


def test_fallback_ladder_is_exercised_in_order(monkeypatch):
    calls: list[str] = []

    def ok(rung):
        def _fit(df, outcome, leverage_col, controls, player_col, cfg=None):
            calls.append(rung)
            return {"beta": 0.1, "se": 0.02, "t": 5.0, "p": 0.001,
                    "ci_low": 0.06, "ci_high": 0.14, "n": len(df),
                    "n_players": df[player_col].nunique(), "n_params": 3,
                    "resid_sd": 0.1, "cluster": "player", "terms": []}
        return _fit

    def bad(rung):
        def _fit(df, outcome, leverage_col, controls, player_col, cfg=None):
            calls.append(rung)
            raise RuntimeError(f"{rung} did not converge")
        return _fit

    monkeypatch.setitem(M._RUNG_FITTERS, "uncorrelated_re", bad("uncorrelated_re"))
    monkeypatch.setitem(M._RUNG_FITTERS, "player_fixed_effects", ok("player_fixed_effects"))
    result = M.fit_ladder(_toy_frame())
    assert calls == ["uncorrelated_re", "player_fixed_effects"]
    assert result["fallback_used"] == "player_fixed_effects"
    assert [step["rung"] for step in result["ladder"]] == [
        "uncorrelated_re", "player_fixed_effects"]
    assert result["ladder"][0]["status"] == "failed"
    assert result["ladder"][1]["status"] == "used"

    calls.clear()
    monkeypatch.setitem(M._RUNG_FITTERS, "player_fixed_effects", bad("player_fixed_effects"))
    monkeypatch.setitem(M._RUNG_FITTERS, "random_intercept", ok("random_intercept"))
    result = M.fit_ladder(_toy_frame())
    assert calls == ["uncorrelated_re", "player_fixed_effects", "random_intercept"]
    assert result["fallback_used"] == "random_intercept"


def test_mde_reproduces_d60(cfg):
    mde = M.analytic_mde(40, cfg)
    assert mde["mde"] == pytest.approx(0.0443, abs=5e-5)
    assert mde["mde_at_configured"] == pytest.approx(0.0443, abs=5e-5)


def test_degenerate_leverage_table_is_labelled_not_crashed(cfg):
    frame = pd.DataFrame({
        "leverage": [0.5, 0.5, 0.5],
        "nfl_id": [1, 2, 3],
    })
    out = E.assign_leverage_bins(frame, cfg)
    assert (out["leverage_bin"] == "single").all()
