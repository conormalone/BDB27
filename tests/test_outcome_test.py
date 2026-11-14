"""Tests for the outcome test (notes/outcome-test-spec.md).

The sample tests recompute the attributed set from the raw files, so a change to
the matching rule or a stale artefact fails the suite. The estimator tests use
synthetic data where the right answer is known.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import outcome_test as ot  # noqa: E402

TURNS = ROOT / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet"
PLAYER_PLAY = ROOT / "data/raw/player_play.csv"
TRACKING = ROOT / "data/raw/game_tracking_2023_sample.csv"
NOTEBOOK = ROOT / "notebooks/bdb27_full_game_turns.ipynb"
SPEC = ROOT / "notes/outcome-test-spec.md"


@pytest.fixture(scope="module")
def sample() -> pd.DataFrame:
    con = ot.connect()
    return ot.build_sample(con, TURNS, PLAYER_PLAY)


# ------------------------------------------------------------------ sample
def test_sample_size_matches_the_result_artefact(sample):
    assert len(sample) == 1731
    assert sample["nfl_id"].nunique() == 102
    assert sample[["game_id", "play_id"]].drop_duplicates().shape[0] == 1598


def test_turn_key_is_unique_within_the_sample(sample):
    key = sample[list(ot.TURN_KEY)]
    assert key.duplicated().sum() == 0


def test_every_turn_has_exactly_one_visible_opposing_route_runner(sample):
    assert (sample["n_opp_route"] == 1).all()
    assert (sample["n_opp_route_sep"] == 1).all()


def test_matching_rule_is_elimination_not_radius(sample):
    """No turn with two or more visible route-runners may enter, at any n."""
    import duckdb

    con = duckdb.connect()
    con.register("s", sample[["game_id", "play_id", "nfl_id"]].drop_duplicates())
    n_bad = con.execute(f"""
        WITH own AS (
          SELECT CAST(game_id AS BIGINT) g, CAST(play_id AS BIGINT) p,
                 CAST(nfl_id AS BIGINT) n, MIN(team_abbr) t
          FROM read_csv('{PLAYER_PLAY.as_posix()}', header=true, all_varchar=true,
                        nullstr=['NA',''])
          GROUP BY 1, 2, 3),
        c AS (
          SELECT o.g, o.p, o.n,
                 COUNT(*) FILTER (WHERE x.team_abbr IS NOT NULL AND x.team_abbr <> o.t
                                    AND x.route_ran IS NOT NULL) k
          FROM own o
          JOIN read_csv('{PLAYER_PLAY.as_posix()}', header=true, all_varchar=true,
                        nullstr=['NA','']) x
            ON CAST(x.game_id AS BIGINT) = o.g AND CAST(x.play_id AS BIGINT) = o.p
           AND CAST(x.nfl_id AS BIGINT) <> o.n
          GROUP BY 1, 2, 3)
        SELECT COUNT(*) FROM (SELECT game_id AS g, play_id AS p, nfl_id AS n FROM s) t
        JOIN c USING (g, p, n) WHERE c.k <> 1""").fetchone()[0]
    assert n_bad == 0


def test_outcome_and_exposure_are_present_and_plausible(sample):
    assert sample["separation_yd"].notna().all()
    assert sample["apex_retention"].notna().all()
    assert (sample["separation_yd"] > 0).all()
    assert sample["apex_retention"].between(0, 3.5).all()
    assert sample["abs_turn_angle"].between(30, 60).all()


def test_attribution_is_labelled_an_inference(sample):
    summary = ot.sample_summary(sample)
    assert "INFERENCE" in summary["attribution"]
    assert "not the frozen" in summary["attribution"]


# ------------------------------------------------------------------ estimator
def test_cluster_ols_recovers_a_known_slope():
    rng = np.random.default_rng(7)
    g = np.repeat(np.arange(40), 25)
    x = rng.normal(size=len(g)) + 0.2 * g
    y = 1.5 - 0.8 * x + rng.normal(scale=0.3, size=len(g))
    X = np.column_stack([np.ones(len(g)), x])
    res = ot.cluster_ols(X, y, g)
    assert res["beta"] == pytest.approx(-0.8, abs=0.05)
    assert res["n_clusters"] == 40
    assert res["se"] > 0


def test_fit_is_deterministic(sample):
    a = ot.fit(sample, "primary")
    b = ot.fit(sample, "primary")
    assert a.beta == b.beta and a.se == b.se and a.p == b.p


def test_bootstrap_is_seeded(sample):
    a = ot.wild_cluster_bootstrap(sample, reps=49)
    b = ot.wild_cluster_bootstrap(sample, reps=49)
    assert a["p_wild_cluster"] == b["p_wild_cluster"]
    assert a["seed"] == b["seed"] == ot.BOOTSTRAP_SEED


def test_effect_floor_matches_the_written_spec():
    text = SPEC.read_text()
    assert "|β₁| ≥ 2.0 yd per unit retention" in text
    assert f"MIN_EFFECT_YD_PER_UNIT = {ot.MIN_EFFECT_YD_PER_UNIT:.1f}" in (
        ROOT / "src/bdb27/outcome_test.py").read_text()
    assert "0.20 yd less" in text


def _fake(beta, lo, hi):
    return ot.Fit("x", beta, 0.0, lo, hi, 0.5, 100, 10, 0.0)


def test_verdict_rule_follows_the_spec_in_order():
    boot = {"p_wild_cluster": 0.5}
    finding = [_fake(-3.0, -4.0, -2.0), _fake(-3.0, -4.0, -2.0),
               _fake(-2.0, -3.0, -1.0), _fake(-1.0, -2.0, -0.1)]
    assert ot.verdict(finding, boot)["label"] == "FINDING"

    small = [_fake(-0.5, -0.9, -0.1), _fake(-0.5, -0.9, -0.1),
             _fake(-0.4, -0.8, -0.1), _fake(-0.3, -0.7, -0.1)]
    assert ot.verdict(small, boot)["label"] == (
        "DETECTABLE BUT TOO SMALL TO BE A FINDING")

    null = [_fake(0.05, -0.36, 0.46), _fake(0.06, -0.36, 0.48),
            _fake(-0.46, -1.34, 0.41), _fake(0.11, -0.30, 0.51)]
    v = ot.verdict(null, boot)
    assert v["label"] == "ESTIMATE ONLY — OPPOSITE TO THE STATED EXPECTATION"
    assert v["a_direction_negative"] is False
    assert v["e_effect_at_least_2yd_per_unit"] is False


def test_verdict_does_not_upgrade_a_wide_interval():
    boot = {"p_wild_cluster": 0.9}
    wide = [_fake(-0.1, -3.0, 2.8), _fake(-0.1, -3.0, 2.8),
            _fake(-0.2, -3.0, 2.6), _fake(-0.1, -3.0, 2.8)]
    assert ot.verdict(wide, boot)["label"] == "ESTIMATE ONLY — NO CLAIM"


# ------------------------------------------------------------------ artefact
def test_committed_summary_matches_a_fresh_fit(sample):
    path = ROOT / "outputs/outcome_test/summary.json"
    payload = json.loads(path.read_text())
    assert payload["provenance"] == "FULL"
    assert payload["sample"]["n_turns"] == len(sample)
    fresh = ot.fit(sample, "primary")
    committed = payload["estimates"][0]
    assert committed["beta_yd_per_unit_retention"] == pytest.approx(
        round(fresh.beta, 4), abs=1e-4)
    assert committed["n_turns"] == len(sample)
    assert payload["limits"] and "inference" in payload["limits"][0]


# ------------------------------------------------------------------ notebook fix
def test_notebook_states_the_true_play_count():
    nb = json.loads(NOTEBOOK.read_text())
    text = "".join("".join(c["source"]) for c in nb["cells"])
    assert "12,638 distinct plays" in text or "12,638" in text
    assert "4,228 plays" not in text


def test_true_play_count_recomputed_from_the_tracking_file():
    import duckdb

    con = duckdb.connect()
    plays_gp, plays_p = con.execute(
        f"SELECT COUNT(DISTINCT (game_id, play_id)), COUNT(DISTINCT play_id) "
        f"FROM read_csv('{TRACKING.as_posix()}', header=true)").fetchone()
    assert int(plays_gp) == 12638
    assert int(plays_p) == 4228  # the figure the notebook used to call "plays"
