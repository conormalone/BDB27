"""DL proxy validation: pure statistics and the historical decade tally.

No raw files are read; the ceiling fitter is exercised by
``test_dl_ceiling.py`` and is not re-tested here.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from dl import dl_proxy_validation as V


def test_pearson_matches_scipy_and_ignores_nan_rows():
    rng = np.random.default_rng(7)
    x = rng.normal(size=200)
    y = 0.4 * x + rng.normal(size=200)
    x[3] = np.nan
    y[10] = np.nan
    expected = stats.pearsonr(x[~np.isnan(x) & ~np.isnan(y)],
                              y[~np.isnan(x) & ~np.isnan(y)]).statistic
    assert V.pearson(x, y) == pytest.approx(expected, abs=1e-12)
    assert math.isnan(V.pearson([1.0, 2.0], [1.0, 2.0]))


def test_spearman_averages_ties_and_matches_scipy():
    x = np.array([1, 2, 2, 3, 4, 5, 5, 5, 6], dtype=float)
    y = np.array([2, 1, 3, 4, 4, 6, 5, 7, 8], dtype=float)
    assert V.spearman(x, y) == pytest.approx(
        stats.spearmanr(x, y).statistic, abs=1e-12)


def test_partial_corr_recovers_planted_conditional_signal():
    rng = np.random.default_rng(11)
    z = rng.normal(size=500)
    x = z + rng.normal(size=500)
    y = 0.5 * z + rng.normal(size=500)
    # Both share z, so the raw correlation is positive but the partial one is
    # near zero; check it against the residual correlation computed by hand.
    design = np.column_stack([np.ones_like(z), z])
    rx = x - design @ np.linalg.lstsq(design, x, rcond=None)[0]
    ry = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
    assert V.partial_corr(x, y, z) == pytest.approx(
        V.pearson(rx, ry), abs=1e-12)
    assert abs(V.partial_corr(x, y, z)) < 0.15


def test_standardised_ols_is_scale_invariant_and_reports_betas():
    rng = np.random.default_rng(3)
    n = 300
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = 2.0 * x1 - 0.5 * x2 + rng.normal(size=n)
    base = V.standardised_ols(y, {"x1": x1, "x2": x2})
    scaled = V.standardised_ols(y * 10.0 + 3.0,
                                {"x1": x1 * 1e3, "x2": x2 - 50.0})
    assert base["r2"] == pytest.approx(scaled["r2"], rel=1e-10)
    assert base["betas"]["x1"] == pytest.approx(base["betas"]["x1"])
    assert base["betas"]["x1"] > 0.7
    assert base["betas"]["x2"] < 0.0
    assert base["n"] == n


def test_standardised_ols_rejects_zero_variance_predictor():
    with pytest.raises(ValueError):
        V.standardised_ols(np.arange(10.0), {"const": np.ones(10)})


def test_decade_start():
    assert V.decade_start(1997) == 1990
    assert V.decade_start(2000) == 2000
    assert V.decade_start(2023) == 2020


def test_historical_coverage_counts_only_dl_family_and_non_null_forty():
    rows = [
        {"draft_year": "1997", "pos": "DE", "forty": "4.60"},
        {"draft_year": "1997", "pos": "DT", "forty": ""},
        {"draft_year": "1999", "pos": "OLB", "forty": "4.75"},
        {"draft_year": "1999", "pos": "WR", "forty": "4.40"},   # excluded
        {"draft_year": "2023", "pos": "NT", "forty": "5.10"},
        {"draft_year": "", "season": "2001", "pos": "EDGE", "forty": "4.80"},
    ]
    out = V.historical_coverage(rows)
    by = {d["decade"]: d for d in out["by_decade"]}
    assert set(by) == {1990, 2000, 2020}
    assert by[1990]["rows"] == 3 and by[1990]["with_forty"] == 2
    assert by[2000]["rows"] == 1 and by[2000]["with_forty"] == 1
    assert by[2020]["rows"] == 1 and by[2020]["with_forty"] == 1
    assert out["total_rows"] == 5 and out["total_with_forty"] == 4


def test_ceiling_estimates_reads_only_a0_and_vmax():
    rows = [
        {"nfl_id": 1, "feature": "a0", "estimate": 8.0, "se": 0.2,
         "position_group": "edge", "n_attempts": 2},
        {"nfl_id": 1, "feature": "vmax", "estimate": 9.0, "se": 0.3,
         "position_group": "edge", "n_attempts": 2},
        {"nfl_id": 1, "feature": "tau", "estimate": 1.1, "se": 0.1,
         "position_group": "edge", "n_attempts": 2},
    ]
    out = V.ceiling_estimates(rows)
    assert set(out) == {1}
    assert out[1]["a0"] == 8.0 and out[1]["vmax"] == 9.0
    assert out[1]["a0_se"] == 0.2 and out[1]["n_attempts"] == 2


def test_bootstrap_ci_covers_the_point_estimate_and_is_seeded():
    rng = np.random.default_rng(5)
    x = rng.normal(size=80)
    y = 0.6 * x + rng.normal(size=80)
    first = V.bootstrap_ci(V.pearson, [x, y], reps=200, seed=123)
    second = V.bootstrap_ci(V.pearson, [x, y], reps=200, seed=123)
    assert first == second
    assert first["lo"] <= V.pearson(x, y) <= first["hi"]
