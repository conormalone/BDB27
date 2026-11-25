"""Tests for ``bdb27.turn_importance`` (D223).

Static and synthetic tests always run. Data-dependent tests are skipped when the
FULL turn table is absent, matching the repo's convention for local artefacts.

No test asserts a substantive result: they check band boundaries, aggregation,
covariance algebra, coefficient recovery, determinism, the no-gate rule, and
that ``separation_at_pass_forward`` never enters the code path.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdb27 import turn_importance as ti

ROOT = Path(__file__).resolve().parents[1]
TURNS = ROOT / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet"
NOTEBOOK = ROOT / "notebooks" / "bdb27_turn_importance.ipynb"


# ------------------------------------------------------------------- bands
def test_band_boundaries_are_half_open_until_the_top_band():
    assert ti.band_of(29.999) is None
    assert ti.band_of(30.0) == "b30_60"
    assert ti.band_of(59.999) == "b30_60"
    assert ti.band_of(60.0) == "b60_90"
    assert ti.band_of(89.999) == "b60_90"
    assert ti.band_of(90.0) == "b90_120"
    assert ti.band_of(150.0) == "b150_180"
    assert ti.band_of(180.0) == "b180p"
    assert ti.band_of(373.35) == "b180p"
    assert ti.band_of(np.nan) is None
    assert ti.band_of(None) is None


def test_band_labels_cover_every_key():
    for key in ti.BAND_KEYS:
        assert ti.band_label(key) and ti.band_label(key) != key
    assert ti.band_label(None) == "No turn"
    assert ti.band_label("none") == "No turn"


def test_max_angle_partition_gives_one_band_per_player_play():
    turns = pd.DataFrame({
        "game_id": [1, 1, 2, 2],
        "play_id": [10, 10, 20, 20],
        "nfl_id": [7, 7, 8, 8],
        "abs_turn_angle": [42.0, 140.0, 33.0, 51.0],
    })
    turns["band"] = ti.band_of_vec(turns["abs_turn_angle"].to_numpy(dtype=float))
    agg = (turns.groupby(["game_id", "play_id", "nfl_id"])["abs_turn_angle"].max()
           .reset_index())
    agg["band"] = ti.band_of_vec(agg["abs_turn_angle"].to_numpy(dtype=float))
    assert agg.loc[0, "band"] == "b120_150"   # 42 and 140 -> sharpest wins
    assert agg.loc[1, "band"] == "b30_60"
    assert len(agg) == 2                      # one row per player-play


# ------------------------------------------------------------ no-gate rule
def _tiny_turns() -> pd.DataFrame:
    row = dict(
        provenance="FULL", setting="game", game_id=1, play_id=1, nfl_id=1,
        position_group="DB", valid=True, abs_turn_angle=45.0,
        entry_speed=3.0, apex_speed=2.5, exit_speed=3.2, speed_retention=1.05,
        apex_retention=0.83, peak_omega_deg_s=80.0,
        peak_lateral_accel_yd_s2=5.0, path_radius_yd=2.0,
        circle_fit_usable=True, regain_entry_speed_s=1.0,
        regain_exhausted=False, duration_s=0.6, plant=False,
        abs_turn_angle_smoother_min=44.0, abs_turn_angle_smoother_max=46.0,
    )
    return pd.DataFrame([row])


def test_cost_table_never_raises_on_a_one_row_band():
    turns = _tiny_turns()
    turns["band"] = ti.band_of_vec(turns["abs_turn_angle"].to_numpy(dtype=float))
    table = ti.cost_table(turns)
    wide = ti.cost_wide(turns)
    ranks = ti.rank_table(turns)
    label = ti.severity_label(ranks)
    assert len(wide) == len(ti.BANDS)
    assert (table["n_turns"] >= 0).all()
    assert label["label"] in {"physically mild", "physically demanding", "intermediate"}
    # the empty bands report zero, they do not abort
    assert (wide["n_turns"] == 0).sum() == len(ti.BANDS) - 1


def test_cost_table_has_no_minimum_count_constant():
    source = Path(ti.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    for banned in ("min_players", "min_turns", "minimum_n", "raise if n <"):
        assert banned not in lowered


# ------------------------------------------------------------- covariance
def _toy_design(n=400, k=4, seed=7):
    rng = np.random.default_rng(seed)
    x = np.column_stack([np.ones(n), rng.normal(size=(n, k - 1))])
    y = x @ np.array([1.0, 0.5, -0.25, 2.0]) + rng.normal(size=n)
    return x, y


def test_cluster_cov_matches_statsmodels_cr1():
    import statsmodels.api as sm

    x, y = _toy_design()
    groups = np.repeat(np.arange(40), 10)
    beta = np.linalg.pinv(x.T @ x) @ x.T @ y
    resid = y - x @ beta
    mine, g = ti.cluster_cov(x, resid, groups, np.linalg.pinv(x.T @ x),
                             np.linalg.matrix_rank(x.T @ x))
    ref = sm.OLS(y, x).fit(cov_type="cluster",
                           cov_kwds={"groups": groups}).cov_params()
    assert g == 40
    assert np.allclose(mine, np.asarray(ref), rtol=1e-9, atol=1e-10)


def test_two_way_cov_is_symmetric_and_psd_on_the_diagonal():
    x, y = _toy_design()
    a = np.repeat(np.arange(40), 10)
    b = np.tile(np.arange(20), 20)
    beta = np.linalg.pinv(x.T @ x) @ x.T @ y
    resid = y - x @ beta
    cov, info = ti.two_way_cluster_cov(x, resid, a, b,
                                       np.linalg.pinv(x.T @ x),
                                       np.linalg.matrix_rank(x.T @ x))
    assert np.allclose(cov, cov.T, atol=1e-12)
    assert (np.diag(cov) >= 0).all()
    assert info["n_a"] == 40 and info["n_b"] == 20


def test_two_way_cov_matches_stacked_one_way_statsmodels():
    """CGM == Va + Vb - Vab, each V from statsmodels' own cluster estimator.

    Spec §6 asks for a direct stacked computation, not just a symmetry check:
    dropping the ``-Vab`` term would pass a symmetry check and fail this one.
    """
    import statsmodels.api as sm

    x, y = _toy_design(n=600, k=4, seed=19)
    a = np.repeat(np.arange(60), 10)
    b = np.tile(np.arange(30), 20)
    xtx_inv = np.linalg.pinv(x.T @ x)
    rank = np.linalg.matrix_rank(x.T @ x)
    beta = xtx_inv @ x.T @ y
    mine, info = ti.two_way_cluster_cov(x, y - x @ beta, a, b, xtx_inv, rank)

    def one_way(groups):
        fit = sm.OLS(y, x).fit(cov_type="cluster", cov_kwds={"groups": groups})
        return np.asarray(fit.cov_params())

    inter = np.char.add(np.char.add(a.astype(str), "|"), b.astype(str))
    reference = one_way(a) + one_way(b) - one_way(inter)
    assert np.allclose(mine, reference, rtol=1e-9, atol=1e-10)
    assert info["n_ab"] == len(np.unique(inter))


# --------------------------------------------------------- model plumbing
def _toy_panel(n_players=30, n_plays=1500, seed=11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_plays):
        player = i % n_players
        band = rng.choice(list(ti.BAND_KEYS) + ["none"],
                          p=[0.05, 0.10, 0.13, 0.12, 0.09, 0.06, 0.45])
        effect = {"none": 0.0, "b30_60": 0.30, "b60_90": 0.45,
                  "b90_120": 0.60, "b120_150": 0.20, "b150_180": 0.05,
                  "b180p": 0.25}[band]
        rows.append(dict(
            game_id=i // 20, play_id=i, nfl_id=player,
            play_key=f"{i // 20}-{i}", band=band,
            down=str(rng.integers(0, 5)), yards_to_go=float(rng.integers(1, 16)),
            coverage=rng.choice(["COVER_1_MAN", "COVER_3_ZONE", "COVERAGE_MISSING"]),
            pos_group=rng.choice(["CB", "SAFETY", "SLOT_CB"]),
            season=str(rng.choice([2023, 2024, 2025])),
            epa_allowed=effect + rng.normal(0, 1.0),
            is_pass=True, is_penalty=False, is_completion=False,
            yac_allowed=np.nan, max_abs_turn_angle=np.nan,
        ))
    panel = pd.DataFrame(rows)
    panel["player_key"] = panel["nfl_id"].astype(str)
    panel["play_key"] = panel["play_id"].astype(str)
    return panel


def test_fit_recovers_a_known_coefficient():
    panel = _toy_panel()
    fit = ti.fit_model(panel, "epa_allowed", ti.PRIMARY_RHS,
                       ("player_key", "game_id"))
    terms = ti.band_terms(fit)
    assert set(terms) == set(ti.BAND_KEYS)
    b = fit.params[terms["b30_60"]]
    se = fit.bse[terms["b30_60"]]
    assert abs(b - 0.30) < 0.15, f"recovered {b:.3f}, expected ~0.30"
    assert 0 < se < 0.5


def test_fit_is_deterministic():
    panel = _toy_panel()
    a = ti.fit_model(panel, "epa_allowed", ti.PRIMARY_RHS, ("player_key", "game_id"))
    b = ti.fit_model(panel, "epa_allowed", ti.PRIMARY_RHS, ("player_key", "game_id"))
    assert np.array_equal(a.params.to_numpy(), b.params.to_numpy())
    assert np.array_equal(a.cov.to_numpy(), b.cov.to_numpy())


def test_prep_drops_penalties_and_respects_samples():
    panel = _toy_panel()
    panel.loc[0, "is_penalty"] = True
    panel.loc[1, "is_pass"] = False
    passed = ti._prep(panel, "pass")
    assert not passed["is_penalty"].any()
    assert passed["is_pass"].all()
    allp = ti._prep(panel, "all")
    assert len(allp) == len(panel) - 1
    within = ti._prep(panel, "pass", drop_no_turn=True)
    assert (within["band"] != "none").all()


def test_outcome_definitions():
    df = pd.DataFrame({
        "pass_result": ["C", "I", "S", "IN", "R", None],
        "yards_gained": [12, 0, -7, 0, 25, 3],
        "expected_points_added": [1.0, -0.5, -1.2, -2.0, 0.8, 0.0],
        "play_nullified_by_penalty": ["N", "N", "N", "N", "N", "Y"],
        "yards_to_go": [10] * 6,
        "max_abs_turn_angle": [45.0] * 6,
        "yac_play": [np.nan] * 6,
        "team_coverage_type": [None] * 6,
        "lined_up_position": ["CB"] * 6,
        "down": [1] * 6,
        "season": [2024] * 6,
        "nfl_id": [1] * 6,
        "game_id": [1] * 6,
        "play_id": list(range(6)),
    })
    out = ti.add_outcomes_and_controls(df)
    assert out["pass_success_allowed"].tolist()[:5] == [1, 0, 0, 0, 0]
    assert out["explosive_allowed"].tolist() == [0, 0, 0, 0, 1, 0]
    assert out["is_pass"].tolist() == [True, True, True, True, False, False]
    assert out["is_penalty"].tolist() == [False] * 5 + [True]


def test_cluster_key_counts_are_reported():
    panel = _toy_panel()
    fit = ti.fit_model(panel, "epa_allowed", ti.PRIMARY_RHS, ("player_key", "game_id"))
    assert fit.clusters["player_key"] == panel["nfl_id"].nunique()
    assert fit.clusters["game_id"] == panel["game_id"].nunique()


# ------------------------------------------------------- static contract
def test_separation_is_never_used():
    """The hard constraint: no separation column anywhere in the code path."""
    import ast

    module = Path(ti.__file__).read_text(encoding="utf-8")
    tree = ast.parse(module)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    code_strings = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    assert not any("separation" in s.lower() for s in code_strings), "module code"

    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cells = "\n".join("".join(c["source"]) for c in notebook["cells"]
                      if c["cell_type"] == "code")
    assert "separation" not in cells.lower(), "notebook code"


def test_notebook_is_committed_without_outputs():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell.get("outputs", []) == [], f"cell {index} has baked outputs"
        assert cell.get("execution_count") is None, f"cell {index} has an execution count"


# ------------------------------------------------------- data-dependent
requires_turns = pytest.mark.skipif(
    not TURNS.exists(), reason="FULL DB turn table absent (local artefact)")


@requires_turns
def test_full_band_counts_reproduce_the_committed_band_table():
    import duckdb
    con = ti.connect()
    turns = ti.load_turns(con, TURNS)
    counts = turns["band"].value_counts().to_dict()
    assert len(turns) == 58_664
    assert counts == {"b30_60": 7_777, "b60_90": 13_077, "b90_120": 13_016,
                      "b120_150": 11_444, "b150_180": 8_137, "b180p": 5_213}


@requires_turns
def test_test1_tables_are_deterministic_and_shares_sum_to_one():
    import duckdb
    con = ti.connect()
    turns = ti.load_turns(con, TURNS)
    a = ti.cost_wide(turns)
    b = ti.cost_wide(turns)
    assert a.equals(b)
    assert abs(a["share_of_turns"].sum() - 1.0) < 1e-12
    long = ti.cost_table(turns)
    assert (long["n_nonnull"] <= long["n_turns"]).all()


@requires_turns
def test_circle_fit_and_regain_denominators_are_explicit():
    import duckdb
    con = ti.connect()
    turns = ti.load_turns(con, TURNS)
    long = ti.cost_table(turns)
    radius = long[long["measure"] == "path_radius_yd"]
    regain = long[long["measure"] == "regain_entry_speed_s"]
    # unusable circle fits are excluded and named
    assert (radius["n_nonnull"] < radius["n_turns"]).all()
    assert radius["denominator_note"].str.contains("usable").all()
    # exhausted regain windows are excluded and named
    assert (regain["n_nonnull"] < regain["n_turns"]).all()
    assert regain["denominator_note"].str.contains("regained").all()


@requires_turns
def test_spearman_uses_the_same_denominator_as_the_radius_table():
    import duckdb
    con = ti.connect()
    turns = ti.load_turns(con, TURNS)
    rho = ti.spearman_table(turns).set_index("measure")
    usable = int(turns["circle_fit_usable"].astype(bool).sum())
    assert rho.loc["path_radius_yd", "n_nonnull"] == usable
    assert usable == 48_929


@pytest.mark.skipif(
    not __import__("os").environ.get("BDB27_DETERMINISM"),
    reason="set BDB27_DETERMINISM=1 to run the two-full-run CSV comparison")
def test_two_full_runs_write_byte_identical_csvs(tmp_path):
    """Spec §6: two consecutive runs produce byte-identical CSVs."""
    root = ROOT
    args = (
        TURNS,
        root / "data/interim/parquet/player_play.parquet",
        root / "data/interim/parquet/games.parquet",
    )
    if not all(Path(p).exists() for p in args):
        pytest.skip("full inputs absent")
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    for out in (out_a, out_b):
        ti.run(*args, out, root / "outputs/turns/turns.parquet")
    files = sorted(p.name for p in out_a.glob("*.csv"))
    assert files, "no CSVs written"
    for name in files:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name
