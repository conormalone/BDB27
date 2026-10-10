"""Phase 3 — Combine fatigue model for the BDB27 combine-fatigue study.

Fits the pre-registered within-player fatigue model on the DB-only Combine study
population and extracts shrunken per-player fatigue slopes for the Phase-5 link.

Model observations = study rows with ``is_imputed = 0 AND perf_z IS NOT NULL``.
Imputed rows still contribute to ``prior_load_yd`` (baked in by Phase 2) but are
NOT model observations.

Methodology is fixed by the PM brief (see ``TASK.md`` Phase 3 + ``notes/decisions.md``
D20) and implemented exactly:

1. **Baseline** — within-player late-minus-early differencing on comparable efforts
   (per ``(player, drill_type)`` ordered by session time), aggregated to a per-player
   mean, one-sample t-test vs 0.
2. **load² criterion** — include ``load²`` iff ``n_unique(load) >= load2_min_unique``
   **and** ``IQR(load) >= load2_min_iqr_yd`` (documented in config, no magic numbers).
3. **Convergence fallback chain** (``model.fallback_order``): ``lmm_random_slopes`` →
   ``lmm_uncorrelated_re`` → ``per_player_eb`` → ``random_intercept``. Every LMM fit
   tries ``model.lmm_optimizers`` in order; a fit is **converged** only if
   ``res.converged is True`` **and** no :class:`ConvergenceWarning` is outstanding.
4. **Robustness** refits (observed-only load; with/without ``first_attempt``).
5. **Diagnostics** — residual/convergence/random-effect summaries.
6. **Reliability** (model-based, spec) = ``tau2 / (tau2 + mean_i(SE_i^2))`` for the
   estimator actually used; the final estimator's reliability drives the stop rule.
7. **Cross-drill-group slope correlation** among players with enough attempts in ≥2 drills.
8. **Permutation test** on the final estimator's population load slope using the
   inverse-variance-weighted mean per-player slope (``meta_slope``) as the statistic;
   the ``(load, load2, first_attempt, drill)`` block is shuffled within each player.
9. **Stop rule** — reliability < ``reliability_stop_threshold`` → report the NULL.
10. **Phase-5 handoff** — shrunken (empirical-Bayes) per-player slopes with CIs.

Every artefact carries the ``common.provenance(cfg, mode)`` label; CSVs carry it FIRST.
Combine data is COMPLETE, so the model is fit for real in BOTH modes — ``--mode`` only
changes the provenance label (exactly like ``02_features.py``).

Run:
    python src/03_combine_model.py --mode sample
    python src/03_combine_model.py --mode sample --force

Outputs (``outputs/model/``):
    combine_player_slopes.parquet + .csv     per-player shrunken slopes (Phase-5 handoff)
    combine_model_fixed_effects.csv          final estimator fixed effects
    combine_model_random_effects.parquet     BLUP / EB per-player random effects
    combine_model_baseline.csv               late-minus-early differencing detail
    combine_model_permutation.csv            per-permutation statistic
    combine_model_xdrill.csv                 cross-drill slope correlations
    combine_model_diagnostics.csv            long key/value diagnostics
    combine_model_summary.json               machine-readable headline numbers
    SUMMARY.md, PROVENANCE.txt
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.tools.sm_exceptions import ConvergenceWarning

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    append_run_log,
    connect,
    get_logger,
    load_config,
    peak_ram_mb,
    provenance,
    resolve,
    seed_everything,
    timed_stage,
)

# --------------------------------------------------------------------------- #
# Schema constants (single source of truth for written dtypes)
# --------------------------------------------------------------------------- #
STUDY_COLUMNS = [
    "provenance", "nfl_id", "draft_year", "combine_position", "drill_type",
    "drill_name", "attempt", "attempt_unit", "event_id", "is_imputed", "session_id",
    "attempt_start_time", "attempt_end_time", "elapsed_session_s", "effort_cost_yd",
    "prior_load_yd", "prior_load_efforts", "prior_load_observed_yd",
    "prior_load_observed_efforts", "perf_z", "first_attempt", "rest_s",
]

PLAYER_SLOPE_COLUMNS: list[tuple[str, str]] = [
    ("provenance", "VARCHAR"),
    ("nfl_id", "INTEGER"),
    ("draft_year", "INTEGER"),
    ("n_attempts_observed", "INTEGER"),
    ("n_attempts_model", "INTEGER"),
    ("slope_raw", "FLOAT"),
    ("slope_shrunk", "FLOAT"),
    ("slope_se", "FLOAT"),
    ("ci_lo", "FLOAT"),
    ("ci_hi", "FLOAT"),
    ("shrinkage_weight", "FLOAT"),
    ("method", "VARCHAR"),
    ("reliability", "FLOAT"),
    ("meets_link_min_attempts", "BOOLEAN"),
]

RANDOM_EFFECT_COLUMNS: list[tuple[str, str]] = [
    ("provenance", "VARCHAR"),
    ("nfl_id", "INTEGER"),
    ("method", "VARCHAR"),
    ("re_intercept", "FLOAT"),
    ("re_slope", "FLOAT"),
    ("se_intercept", "FLOAT"),
    ("se_slope", "FLOAT"),
]

ESTIMATORS = ["lmm_random_slopes", "lmm_uncorrelated_re", "per_player_eb", "random_intercept"]


# --------------------------------------------------------------------------- #
# Pure helpers (importable by tests — no pipeline state)
# --------------------------------------------------------------------------- #
def per_player_slopes(df: pd.DataFrame, load_col: str, min_attempts: int,
                      outcome_col: str = "perf_z") -> pd.DataFrame:
    """Per-player OLS slope of ``outcome_col`` on ``load_col`` (spec item 3 fallback 2).

    ``outcome_col`` defaults to ``perf_z`` (Phase-3 behaviour unchanged); the metric
    panel overrides it to fit other standardised outcomes. A player contributes iff it
    has ``>= min_attempts`` rows **and** load variation (``n_unique >= 2``) — otherwise
    the slope is not identified. Returns columns ``nfl_id, n, slope, se`` (empty frame
    if no player qualifies).
    """
    rows: list[dict[str, Any]] = []
    for pid, g in df.groupby("nfl_id", sort=True):
        x = g[load_col].to_numpy(dtype=np.float64)
        y = g[outcome_col].to_numpy(dtype=np.float64)
        n = int(x.size)
        if n < int(min_attempts) or np.unique(x).size < 2:
            continue
        if np.var(y) == 0.0:  # constant performance -> slope unidentifiable (no numerical-noise slopes)
            continue
        X = np.column_stack([np.ones(n, dtype=np.float64), x])
        try:
            xtx_inv = np.linalg.inv(X.T @ X)
        except np.linalg.LinAlgError:
            continue
        beta = xtx_inv @ (X.T @ y)
        resid = y - X @ beta
        dof = n - 2
        s2 = float(resid @ resid / dof) if dof > 0 else 0.0
        var = s2 * float(xtx_inv[1, 1])
        if not np.isfinite(var) or var < 0:
            continue
        rows.append({"nfl_id": int(pid), "n": n, "slope": float(beta[1]), "se": float(math.sqrt(var))})
    out = pd.DataFrame(rows, columns=["nfl_id", "n", "slope", "se"])
    if len(out) == 0:
        out = out.astype({"nfl_id": "int64", "n": "int64", "slope": "float64", "se": "float64"})
    return out


def inv_var_mean(slopes: pd.DataFrame) -> tuple[float, float, int]:
    """Inverse-variance-weighted mean of per-player slopes.

    Returns ``(mu, se, k)`` where ``k`` is the number of players with a usable
    (finite, strictly positive) SE. ``(nan, nan, 0)`` if none qualify.
    """
    if len(slopes) == 0:
        return float("nan"), float("nan"), 0
    ok = np.isfinite(slopes["se"].to_numpy(dtype=np.float64)) & (slopes["se"].to_numpy(dtype=np.float64) > 0) \
        & np.isfinite(slopes["slope"].to_numpy(dtype=np.float64))
    sub = slopes.loc[ok]
    k = int(len(sub))
    if k == 0:
        return float("nan"), float("nan"), 0
    w = 1.0 / sub["se"].to_numpy(dtype=np.float64) ** 2
    mu = float((w * sub["slope"].to_numpy(dtype=np.float64)).sum() / w.sum())
    se = float(math.sqrt(1.0 / w.sum()))
    return mu, se, k


def dl_tau2(slopes: pd.DataFrame) -> float:
    """Between-player slope variance per the spec: ``max(0, Var_obs(slopes) - mean_i(SE_i^2))``.

    (Spec item 6; labelled ``eb_tau2_method: dl``. The Q-based DerSimonian–Laird
    estimator is additionally reported in diagnostics for comparison.)
    """
    s = slopes["slope"].to_numpy(dtype=np.float64)
    se = slopes["se"].to_numpy(dtype=np.float64)
    ok = np.isfinite(s) & np.isfinite(se)
    if ok.sum() < 2:
        return 0.0
    var_obs = float(np.var(s[ok], ddof=1))
    mean_se2 = float(np.mean(se[ok] ** 2))
    return float(max(0.0, var_obs - mean_se2))


def dl_tau2_q(slopes: pd.DataFrame) -> float:
    """Q-based DerSimonian–Laird tau^2 (reference/comparison only)."""
    s = slopes["slope"].to_numpy(dtype=np.float64)
    se = slopes["se"].to_numpy(dtype=np.float64)
    ok = np.isfinite(s) & np.isfinite(se) & (se > 0)
    if ok.sum() < 2:
        return 0.0
    w = 1.0 / se[ok] ** 2
    mu = float((w * s[ok]).sum() / w.sum())
    q = float((w * (s[ok] - mu) ** 2).sum())
    denom = float(w.sum() - (w ** 2).sum() / w.sum())
    if denom <= 0:
        return 0.0
    return float(max(0.0, (q - (ok.sum() - 1)) / denom))


def eb_shrink(slopes: pd.DataFrame, tau2: float, mu: float) -> pd.DataFrame:
    """Empirical-Bayes shrinkage of per-player slopes toward ``mu``.

    ``w_i = tau2 / (tau2 + SE_i^2)`` (0 when ``tau2 == 0``); shrunken slope
    ``w_i*slope_i + (1-w_i)*mu``; posterior SE ``SE_i * sqrt(w_i)``.
    """
    out = slopes.copy()
    se2 = out["se"].to_numpy(dtype=np.float64) ** 2
    if tau2 > 0:
        w = tau2 / (tau2 + se2)
    else:
        w = np.zeros_like(se2)
    w = np.where(np.isfinite(w), w, 0.0)
    out["shrinkage_weight"] = w
    out["slope_shrunk"] = w * out["slope"].to_numpy(dtype=np.float64) + (1.0 - w) * mu
    out["posterior_se"] = out["se"].to_numpy(dtype=np.float64) * np.sqrt(w)
    return out


def baseline_difference(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Within-player late-minus-early differencing on comparable efforts.

    Per ``(player, drill_type)`` ordered by session time, ``diff = mean(last w
    attempts' perf_z) - mean(first w attempts' perf_z)`` requiring
    ``>= baseline_min_attempts`` attempts. Returns one row per qualifying
    ``(player, drill_type)`` with ``nfl_id, drill_type, n_attempts, first_mean,
    last_mean, baseline_diff``.
    """
    m = cfg["model"]
    w = int(m["baseline_window"])
    min_n = int(m["baseline_min_attempts"])
    d = df.copy()
    d["_t"] = pd.to_datetime(d["attempt_start_time"], errors="coerce")
    rows: list[dict[str, Any]] = []
    for (pid, drill), g in d.groupby(["nfl_id", "drill_type"], sort=True):
        g = g.sort_values(["_t", "attempt", "event_id"], na_position="last", kind="mergesort")
        n = len(g)
        if n < min_n:
            continue
        first = g["perf_z"].iloc[:w].mean()
        last = g["perf_z"].iloc[-w:].mean()
        rows.append({
            "nfl_id": int(pid), "drill_type": str(drill), "n_attempts": int(n),
            "first_mean": float(first), "last_mean": float(last),
            "baseline_diff": float(last - first),
        })
    return pd.DataFrame(rows, columns=[
        "nfl_id", "drill_type", "n_attempts", "first_mean", "last_mean", "baseline_diff"])


def baseline_ttest(baseline: pd.DataFrame) -> dict[str, float]:
    """Aggregate per-(player,drill) baseline diffs to a per-player mean; 1-sample t-test."""
    if len(baseline) == 0:
        return {"mean": np.nan, "sd": np.nan, "t": np.nan, "p": np.nan, "n": 0}
    per_player = baseline.groupby("nfl_id", sort=True)["baseline_diff"].mean()
    arr = per_player.to_numpy(dtype=np.float64)
    n = int(arr.size)
    if n < 2:
        return {"mean": float(arr.mean()) if n else np.nan, "sd": np.nan,
                "t": np.nan, "p": np.nan, "n": n}
    sd = float(arr.std(ddof=1))
    if sd == 0.0:
        return {"mean": float(arr.mean()), "sd": 0.0, "t": np.nan, "p": np.nan, "n": n}
    t, p = stats.ttest_1samp(arr, 0.0)
    return {"mean": float(arr.mean()), "sd": sd, "t": float(t), "p": float(p), "n": n}


def load2_criterion(load: np.ndarray, cfg: dict[str, Any]) -> dict[str, Any]:
    """load² inclusion criterion (spec item 2): n_unique AND IQR thresholds."""
    m = cfg["model"]
    v = np.asarray(load, dtype=np.float64)
    v = v[np.isfinite(v)]
    n_unique = int(np.unique(v).size)
    iqr = float(np.percentile(v, 75) - np.percentile(v, 25)) if v.size else float("nan")
    include = bool(n_unique >= int(m["load2_min_unique"]) and iqr >= float(m["load2_min_iqr_yd"]))
    return {"n_unique": n_unique, "iqr_yd": iqr, "include": include,
            "min_unique": int(m["load2_min_unique"]), "min_iqr_yd": float(m["load2_min_iqr_yd"])}


# --------------------------------------------------------------------------- #
# Design matrix + LMM fitting
# --------------------------------------------------------------------------- #
def build_design(df: pd.DataFrame, cfg: dict[str, Any], include_load2: bool,
                 load_col: str, use_first_attempt: bool = True) -> pd.DataFrame:
    """Add centred ``load_c`` (and ``load2_c`` when included) and a ``drill`` column."""
    m = cfg["model"]
    out = df.reset_index(drop=True).copy()
    load = out[load_col].to_numpy(dtype=np.float64)
    if bool(m["load_center"]):
        out["load_c"] = load - float(np.nanmean(load))
    else:
        out["load_c"] = load
    lc = out["load_c"].to_numpy(dtype=np.float64)
    out["load2_c"] = lc ** 2 - float(np.nanmean(lc ** 2))
    sd = float(np.nanstd(lc))
    out["load_s"] = lc / sd if sd > 0 else np.zeros_like(lc)
    out["drill"] = out[m["drill"]].astype(str)
    out["_use_first_attempt"] = bool(use_first_attempt)
    return out


def _formula(cfg: dict[str, Any], include_load2: bool, use_first_attempt: bool,
             outcome_col: str | None = None) -> str:
    m = cfg["model"]
    outcome = outcome_col or str(m["outcome"])
    terms = ["load_c"]
    if include_load2:
        terms.append("load2_c")
    if use_first_attempt:
        terms.append(str(m["first_attempt"]))
    terms.append("C(drill)")
    return f"{outcome} ~ " + " + ".join(terms)


def _fit_optimizers(model, optimizers: list[str], maxiter: int, reml: bool) -> tuple[list[dict], Any]:
    """Try optimizers in order; return ``(attempts, first_clean_result_or_None)``.

    A fit is *clean* iff ``res.converged is True`` and no :class:`ConvergenceWarning`
    was raised (spec: "no ConvergenceWarning is outstanding"). Stops at the first
    clean fit; every attempt is logged.
    """
    attempts: list[dict[str, Any]] = []
    chosen = None
    for opt in optimizers:
        res = None
        err = ""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                res = model.fit(method=opt, reml=reml, maxiter=int(maxiter))
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
        conv = bool(res is not None and getattr(res, "converged", False))
        cw = [str(x.message) for x in caught if issubclass(x.category, ConvergenceWarning)]
        clean = bool(conv and not cw and not err)
        attempts.append({"optimizer": opt, "converged": conv, "warnings": cw,
                         "error": err, "clean": clean})
        if clean:
            chosen = res
            break
    return attempts, chosen


def _fixed_effects_table(res) -> pd.DataFrame:
    """Fixed-effects table (drops the covariance/variance parameters)."""
    k_fe = int(res.model.exog.shape[1])
    names = list(res.params.index)[:k_fe]
    est = np.asarray(res.params.values)[:k_fe]
    se = np.asarray(res.bse.values)[:k_fe]
    z = np.asarray(res.tvalues.values)[:k_fe]
    p = np.asarray(res.pvalues.values)[:k_fe]
    return pd.DataFrame({"term": names, "estimate": est, "se": se, "z": z, "p": p})


def _lmm_reliability(res, slope_index: int) -> tuple[float, float, float, int]:
    """tau2 (random-slope variance), mean posterior slope SE^2, reliability, n players.

    ``SE_i`` = posterior (BLUP) SE of player i's slope = sqrt diag of
    ``(Z_i' V_i^{-1} Z_i + G^{-1})^{-1}`` (statsmodels ``random_effects_cov``).
    """
    cov_re = res.cov_re
    if slope_index < cov_re.shape[0]:
        tau2 = float(cov_re.iloc[slope_index, slope_index])
    else:
        tau2 = float(res.model.k_vc and np.min(res.vcomp)) if getattr(res.model, "k_vc", 0) else 0.0
    rec = res.random_effects_cov
    se2 = []
    for cov in rec.values():
        try:
            v = float(cov.iloc[slope_index, slope_index])
        except Exception:  # noqa: BLE001
            v = np.nan
        if np.isfinite(v) and v >= 0:
            se2.append(v)
    mean_se2 = float(np.mean(se2)) if se2 else float("nan")
    rel = tau2 / (tau2 + mean_se2) if (np.isfinite(mean_se2) and (tau2 + mean_se2) > 0) else 0.0
    return tau2, mean_se2, float(rel), int(len(se2))


def _random_effects_table(res, slope_name: str) -> pd.DataFrame:
    re = res.random_effects
    cov = res.random_effects_cov
    rows = []
    for pid in sorted(re.keys()):
        s = re[pid]
        c = cov.get(pid)
        ri = float(s.loc["Group"]) if "Group" in s.index else np.nan
        rs = float(s.loc[slope_name]) if slope_name in s.index else np.nan
        sei = float(np.sqrt(c.iloc[0, 0])) if c is not None else np.nan
        ses = float(np.sqrt(c.iloc[1, 1])) if c is not None else np.nan
        rows.append({"nfl_id": int(pid), "re_intercept": ri, "re_slope": rs,
                     "se_intercept": sei, "se_slope": ses})
    return pd.DataFrame(rows, columns=["nfl_id", "re_intercept", "re_slope", "se_intercept", "se_slope"])


def _pop_from_fixed(fe: pd.DataFrame, term: str) -> tuple[float, float, float]:
    row = fe.loc[fe["term"] == term]
    if len(row) == 0:
        return float("nan"), float("nan"), float("nan")
    r = row.iloc[0]
    return float(r["estimate"]), float(r["se"]), float(r["p"])


def fit_lmm_random_slopes(df: pd.DataFrame, cfg: dict[str, Any], include_load2: bool,
                          use_first_attempt: bool = True, load_col: str | None = None,
                          outcome_col: str | None = None) -> dict[str, Any]:
    """Primary spec LMM: ``(1 + load_c | player)`` (correlated random slopes)."""
    m = cfg["model"]
    load_col = load_col or str(m["load"])
    d = build_design(df, cfg, include_load2, load_col, use_first_attempt)
    formula = _formula(cfg, include_load2, use_first_attempt, outcome_col)
    try:
        model = smf.mixedlm(formula, d, groups=d[m["player"]], re_formula="1 + load_c")
    except Exception as exc:  # noqa: BLE001
        return {"estimator": "lmm_random_slopes", "accepted": False, "optimizer": None,
                "attempts": [{"optimizer": None, "converged": False, "warnings": [],
                              "error": f"{type(exc).__name__}: {exc}", "clean": False}],
                "load_coef": float("nan"), "load_se": float("nan"), "load_p": float("nan"),
                "reliability": float("nan"), "tau2": float("nan"), "mean_se2": float("nan"),
                "n_players": 0, "fixed": pd.DataFrame(), "random": pd.DataFrame(),
                "skip_phase5_link": False}
    attempts, res = _fit_optimizers(model, list(m["lmm_optimizers"]), int(m["lmm_maxiter"]), bool(m["lmm_reml"]))
    if res is None:
        return {"estimator": "lmm_random_slopes", "accepted": False, "optimizer": None,
                "attempts": attempts, "load_coef": float("nan"), "load_se": float("nan"),
                "load_p": float("nan"), "reliability": float("nan"), "tau2": float("nan"),
                "mean_se2": float("nan"), "n_players": 0, "fixed": pd.DataFrame(),
                "random": pd.DataFrame(), "skip_phase5_link": False}
    fe = _fixed_effects_table(res)
    tau2, mean_se2, rel, npl = _lmm_reliability(res, slope_index=1)
    coef, se, p = _pop_from_fixed(fe, "load_c")
    rand = _random_effects_table(res, slope_name="load_c")
    opt = next(a["optimizer"] for a in attempts if a["clean"])
    return {"estimator": "lmm_random_slopes", "accepted": True, "optimizer": opt,
            "attempts": attempts, "load_coef": coef, "load_se": se, "load_p": p,
            "reliability": rel, "tau2": tau2, "mean_se2": mean_se2, "n_players": npl,
            "fixed": fe, "random": rand, "skip_phase5_link": False}


def fit_lmm_uncorrelated_re(df: pd.DataFrame, cfg: dict[str, Any], include_load2: bool,
                            use_first_attempt: bool = True, load_col: str | None = None,
                            outcome_col: str | None = None) -> dict[str, Any]:
    """Fallback (1): uncorrelated random effects ``(1|player) + (0+load|player)``.

    Implemented via statsmodels ``vc_formula`` (a separate variance component for the
    centred/rescaled load), i.e. a genuinely diagonal random-effects covariance.
    """
    m = cfg["model"]
    load_col = load_col or str(m["load"])
    d = build_design(df, cfg, include_load2, load_col, use_first_attempt)
    formula = _formula(cfg, include_load2, use_first_attempt, outcome_col)
    try:
        model = smf.mixedlm(formula, d, groups=d[m["player"]], re_formula="1",
                            vc_formula={str(m["player"]): "0 + load_s"})
    except Exception as exc:  # noqa: BLE001
        return {"estimator": "lmm_uncorrelated_re", "accepted": False, "optimizer": None,
                "attempts": [{"optimizer": None, "converged": False, "warnings": [],
                              "error": f"{type(exc).__name__}: {exc}", "clean": False}],
                "load_coef": float("nan"), "load_se": float("nan"), "load_p": float("nan"),
                "reliability": float("nan"), "tau2": float("nan"), "mean_se2": float("nan"),
                "n_players": 0, "fixed": pd.DataFrame(), "random": pd.DataFrame(),
                "skip_phase5_link": False}
    attempts, res = _fit_optimizers(model, list(m["lmm_optimizers"]), int(m["lmm_maxiter"]), bool(m["lmm_reml"]))
    if res is None:
        return {"estimator": "lmm_uncorrelated_re", "accepted": False, "optimizer": None,
                "attempts": attempts, "load_coef": float("nan"), "load_se": float("nan"),
                "load_p": float("nan"), "reliability": float("nan"), "tau2": float("nan"),
                "mean_se2": float("nan"), "n_players": 0, "fixed": pd.DataFrame(),
                "random": pd.DataFrame(), "skip_phase5_link": False}
    vcomp = np.asarray(res.vcomp, dtype=np.float64)
    tau2 = float(min(vcomp)) if vcomp.size else 0.0
    rec = res.random_effects_cov
    se2 = []
    for cov in rec.values():
        try:
            v = float(cov.iloc[1, 1])
        except Exception:  # noqa: BLE001
            v = np.nan
        if np.isfinite(v) and v >= 0:
            se2.append(v)
    mean_se2 = float(np.mean(se2)) if se2 else float("nan")
    rel = tau2 / (tau2 + mean_se2) if (np.isfinite(mean_se2) and (tau2 + mean_se2) > 0) else 0.0
    fe = _fixed_effects_table(res)
    coef, se, p = _pop_from_fixed(fe, "load_c")
    slope_names = [n for n in res.random_effects[list(res.random_effects)[0]].index if n != "Group"]
    rand = _random_effects_table(res, slope_name=slope_names[0] if slope_names else "Group")
    opt = next(a["optimizer"] for a in attempts if a["clean"])
    return {"estimator": "lmm_uncorrelated_re", "accepted": True, "optimizer": opt,
            "attempts": attempts, "load_coef": coef, "load_se": se, "load_p": p,
            "reliability": rel, "tau2": tau2, "mean_se2": mean_se2, "n_players": int(len(se2)),
            "fixed": fe, "random": rand, "skip_phase5_link": False}


def fit_per_player_eb(df: pd.DataFrame, cfg: dict[str, Any], include_load2: bool,
                      use_first_attempt: bool = True, load_col: str | None = None,
                      outcome_col: str | None = None) -> dict[str, Any]:
    """Fallback (2): per-player OLS slopes + empirical-Bayes shrinkage (always fits)."""
    m = cfg["model"]
    load_col = load_col or str(m["load"])
    oc = outcome_col or str(m["outcome"])
    d = build_design(df, cfg, include_load2, load_col, use_first_attempt)
    slopes = per_player_slopes(d, "load_c", int(m["eb_min_attempts"]), outcome_col=oc)
    tau2 = dl_tau2(slopes)
    mu, mu_se, k = inv_var_mean(slopes)
    mean_se2 = float(np.mean(slopes["se"].to_numpy() ** 2)) if len(slopes) else float("nan")
    rel = tau2 / (tau2 + mean_se2) if (np.isfinite(mean_se2) and (tau2 + mean_se2) > 0) else 0.0
    if k < int(m["reliability_min_players"]):
        rel = 0.0
    p = float(2.0 * stats.norm.sf(abs(mu / mu_se))) if (np.isfinite(mu) and np.isfinite(mu_se) and mu_se > 0) else float("nan")
    fe = pd.DataFrame({"term": ["Intercept", "load_c"], "estimate": [float(d[oc].mean()), mu],
                       "se": [float("nan"), mu_se], "z": [float("nan"), (mu / mu_se if mu_se else float("nan"))],
                       "p": [float("nan"), p]})
    sh = eb_shrink(slopes, tau2, mu)
    pmean = d.groupby("nfl_id", sort=True)[oc].mean().rename("pmean")
    pstd = d.groupby("nfl_id", sort=True)[oc].std(ddof=1).rename("psd")
    pn = d.groupby("nfl_id", sort=True).size().rename("pn")
    gmean = float(d[oc].mean())
    rm = sh[['nfl_id', 'se', 'slope_shrunk']].copy()
    rm['se_slope'] = rm['se'].to_numpy(dtype=np.float64)
    rm['re_slope'] = rm['slope_shrunk'] - mu
    rm['re_intercept'] = (rm['nfl_id'].map(pmean) - gmean).to_numpy(dtype=np.float64)
    rm['se_intercept'] = (rm['nfl_id'].map(pstd) / np.sqrt(rm['nfl_id'].map(pn))).to_numpy(dtype=np.float64)
    rand = rm[['nfl_id', 're_intercept', 're_slope', 'se_intercept', 'se_slope']].copy()
    return {"estimator": "per_player_eb", "accepted": True, "optimizer": "ols",
            "attempts": [{"optimizer": "ols", "converged": True, "warnings": [], "error": "", "clean": True}],
            "load_coef": mu, "load_se": mu_se, "load_p": p, "reliability": float(rel),
            "tau2": tau2, "mean_se2": mean_se2, "n_players": k, "fixed": fe, "random": rand,
            "skip_phase5_link": False, "slopes": slopes, "tau2_q": dl_tau2_q(slopes)}


def fit_random_intercept(df: pd.DataFrame, cfg: dict[str, Any], include_load2: bool,
                         use_first_attempt: bool = True, load_col: str | None = None,
                         outcome_col: str | None = None) -> dict[str, Any]:
    """Fallback (3): ``(1 | player)`` only — population-level results, skip Phase-5 link."""
    m = cfg["model"]
    load_col = load_col or str(m["load"])
    d = build_design(df, cfg, include_load2, load_col, use_first_attempt)
    formula = _formula(cfg, include_load2, use_first_attempt, outcome_col)
    try:
        model = smf.mixedlm(formula, d, groups=d[m["player"]], re_formula="1")
    except Exception as exc:  # noqa: BLE001
        return {"estimator": "random_intercept", "accepted": False, "optimizer": None,
                "attempts": [{"optimizer": None, "converged": False, "warnings": [],
                              "error": f"{type(exc).__name__}: {exc}", "clean": False}],
                "load_coef": float("nan"), "load_se": float("nan"), "load_p": float("nan"),
                "reliability": float("nan"), "tau2": 0.0, "mean_se2": float("nan"),
                "n_players": 0, "fixed": pd.DataFrame(), "random": pd.DataFrame(),
                "skip_phase5_link": True}
    attempts, res = _fit_optimizers(model, list(m["lmm_optimizers"]), int(m["lmm_maxiter"]), bool(m["lmm_reml"]))
    if res is None:
        return {"estimator": "random_intercept", "accepted": False, "optimizer": None,
                "attempts": attempts, "load_coef": float("nan"), "load_se": float("nan"),
                "load_p": float("nan"), "reliability": 0.0, "tau2": 0.0, "mean_se2": float("nan"),
                "n_players": 0, "fixed": pd.DataFrame(), "random": pd.DataFrame(),
                "skip_phase5_link": True}
    fe = _fixed_effects_table(res)
    coef, se, p = _pop_from_fixed(fe, "load_c")
    rand = _random_effects_table(res, slope_name="Group")
    opt = next(a["optimizer"] for a in attempts if a["clean"])
    # no random slope => no slope variance => reliability 0 (stop rule will fire)
    return {"estimator": "random_intercept", "accepted": True, "optimizer": opt,
            "attempts": attempts, "load_coef": coef, "load_se": se, "load_p": p,
            "reliability": 0.0, "tau2": 0.0, "mean_se2": float("nan"),
            "n_players": 0, "fixed": fe, "random": rand, "skip_phase5_link": True}


FITTERS = {
    "lmm_random_slopes": fit_lmm_random_slopes,
    "lmm_uncorrelated_re": fit_lmm_uncorrelated_re,
    "per_player_eb": fit_per_player_eb,
    "random_intercept": fit_random_intercept,
}


def fit_chain(df: pd.DataFrame, cfg: dict[str, Any], include_load2: bool,
              load_col: str | None = None,
              outcome_col: str | None = None) -> tuple[dict, list[dict]]:
    """Run ``model.fallback_order``; return ``(chosen, all_results)``.

    The first estimator that is *accepted* is chosen. ``per_player_eb`` is always
    accepted (deterministic OLS), so a choice is guaranteed. Optional ``load_col`` /
    ``outcome_col`` overrides support the metric panel (defaults unchanged).
    """
    results: list[dict] = []
    chosen = None
    for name in cfg["model"]["fallback_order"]:
        r = FITTERS[str(name)](df, cfg, include_load2, load_col=load_col,
                               outcome_col=outcome_col)
        results.append(r)
        if r["accepted"] and chosen is None:
            chosen = r
            break
    if chosen is None:
        chosen = results[-1]
    return chosen, results


# --------------------------------------------------------------------------- #
# Reliability-driven per-player slope output (Phase-5 handoff)
# --------------------------------------------------------------------------- #
def player_slope_table(df_model: pd.DataFrame, df_all: pd.DataFrame, chosen: dict,
                       cfg: dict[str, Any], prov: str) -> pd.DataFrame:
    """Shrunken (EB) per-player slope table for the Phase-5 handoff.

    Per-player slopes are **empirical-Bayes shrunk** (the spec's handoff requirement)
    toward the inverse-variance-weighted population mean; the ``method`` column records
    the population estimator that was fitted and whose ``reliability`` is reported.
    """
    m = cfg["model"]
    d = build_design(df_model, cfg, include_load2=True, load_col=str(m["load"]))
    slopes = per_player_slopes(d, "load_c", int(m["eb_min_attempts"]))
    tau2 = dl_tau2(slopes)
    mu, _, _ = inv_var_mean(slopes)
    if not np.isfinite(mu):
        mu = float(d["perf_z"].mean()) * 0.0  # degenerate: shrink to 0 if no usable slopes
    sh = eb_shrink(slopes, tau2, mu)
    n_obs = (df_all[df_all["is_imputed"] == 0].groupby("nfl_id").size().rename("n_attempts_observed"))
    n_mod = df_model.groupby("nfl_id").size().rename("n_attempts_model")
    dys = df_all.groupby("nfl_id")["draft_year"].first().rename("draft_year")
    players = sorted(set(df_model["nfl_id"].unique()) | set(df_all["nfl_id"].unique()))
    base = pd.DataFrame({"nfl_id": [int(p) for p in players]})
    base = base.merge(dys.reset_index(), on="nfl_id", how="left")
    base = base.merge(n_obs.reset_index(), on="nfl_id", how="left")
    base = base.merge(n_mod.reset_index(), on="nfl_id", how="left")
    base = base.merge(sh[["nfl_id", "slope", "se", "slope_shrunk", "posterior_se", "shrinkage_weight"]],
                      on="nfl_id", how="left")
    level = float(m["player_slope_ci_level"])
    zc = float(stats.norm.ppf(1.0 - (1.0 - level) / 2.0))
    out = pd.DataFrame({
        "provenance": prov,
        "nfl_id": base["nfl_id"].astype("Int64"),
        "draft_year": base["draft_year"],
        "n_attempts_observed": base["n_attempts_observed"].fillna(0).astype("Int64"),
        "n_attempts_model": base["n_attempts_model"].fillna(0).astype("Int64"),
        "slope_raw": base["slope"],
        "slope_shrunk": base["slope_shrunk"],
        # slope_se = per-player OLS (sampling) SE of slope_raw; the CI widens the EB
        # shrunken estimate by it (avoids degenerate zero-width intervals when tau2=0).
        "slope_se": base["se"],
        "ci_lo": base["slope_shrunk"] - zc * base["se"],
        "ci_hi": base["slope_shrunk"] + zc * base["se"],
        "shrinkage_weight": base["shrinkage_weight"],
        "method": chosen["estimator"],
        "reliability": float(chosen["reliability"]),
        "meets_link_min_attempts": base["n_attempts_model"].fillna(0) >= int(m["link_min_attempts"]),
    })
    return out.sort_values("nfl_id", kind="mergesort").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Cross-drill-group slope correlation
# --------------------------------------------------------------------------- #
def xdrill_correlation(df_model: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Pairwise Pearson correlation of per-drill per-player slopes across shared players."""
    m = cfg["model"]
    min_att = int(m["xdrill_min_attempts"])
    min_players = int(m["xdrill_min_players"])
    d = build_design(df_model, cfg, include_load2=True, load_col=str(m["load"]))
    slopes: dict[str, pd.Series] = {}
    for drill, g in d.groupby("drill_type", sort=True):
        sp = per_player_slopes(g, "load_c", min_att)
        slopes[str(drill)] = sp.set_index("nfl_id")["slope"]
    drills = sorted(slopes.keys())
    rows: list[dict[str, Any]] = []
    for i in range(len(drills)):
        for j in range(i + 1, len(drills)):
            a, b = drills[i], drills[j]
            sa, sb = slopes[a], slopes[b]
            shared = sa.index.intersection(sb.index)
            n = int(len(shared))
            r = float("nan")
            p = float("nan")
            if n >= 3:
                xa = sa.loc[shared].to_numpy(dtype=np.float64)
                xb = sb.loc[shared].to_numpy(dtype=np.float64)
                if np.unique(xa).size > 1 and np.unique(xb).size > 1:
                    r, p = stats.pearsonr(xa, xb)
                    r, p = float(r), float(p)
            rows.append({"drill_a": a, "drill_b": b, "n_players": n, "pearson_r": r,
                         "p_value": p, "meets_min_players": bool(n >= min_players)})
    return pd.DataFrame(rows, columns=["drill_a", "drill_b", "n_players", "pearson_r",
                                       "p_value", "meets_min_players"])


# --------------------------------------------------------------------------- #
# Permutation test (meta_slope statistic)
# --------------------------------------------------------------------------- #
def _player_slope_blocks(df: pd.DataFrame, load_col: str,
                         outcome_col: str = "perf_z") -> list[dict[str, Any]]:
    """Precompute per-player blocks for the fast permutation statistic."""
    blocks = []
    for pid, g in df.groupby("nfl_id", sort=True):
        x = g[load_col].to_numpy(dtype=np.float64)
        y = g[outcome_col].to_numpy(dtype=np.float64)
        n = int(x.size)
        if n < 2 or np.unique(x).size < 2:
            continue
        if np.var(y) == 0.0:  # constant performance -> no slope information
            continue
        xbar = float(x.mean())
        sxx = float(((x - xbar) ** 2).sum())
        if sxx <= 0:
            continue
        blocks.append({"idx": g.index.to_numpy(), "x": x, "y": y, "n": n,
                       "xbar": xbar, "sxx": sxx, "yc": y - float(y.mean())})
    return blocks


def _meta_slope_from_blocks(blocks: list[dict[str, Any]], load_perm: np.ndarray) -> float:
    """Inverse-variance-weighted mean per-player slope given (possibly permuted) load."""
    num = 0.0
    den = 0.0
    for b in blocks:
        xp = load_perm[b["idx"]]
        beta = float(((xp - b["xbar"]) * b["yc"]).sum() / b["sxx"])
        resid = b["yc"] - beta * (xp - b["xbar"])
        dof = b["n"] - 2
        if dof <= 0:
            continue
        s2 = float(resid @ resid / dof)
        var = s2 / b["sxx"]
        if not np.isfinite(var) or var <= 0:
            continue
        w = 1.0 / var
        num += w * beta
        den += w
    return float(num / den) if den > 0 else float("nan")


def permutation_test(df_model: pd.DataFrame, cfg: dict[str, Any],
                     load_col: str | None = None,
                     outcome_col: str | None = None) -> tuple[float, float, pd.DataFrame]:
    """Shuffle the ``(load, load2, first_attempt, drill)`` block within each player.

    Target = population load slope; statistic = inverse-variance-weighted mean
    per-player load slope (``meta_slope`` — fast + deterministic; documented choice).
    Only the permuted ``load`` enters the statistic, so shuffling the block jointly is
    equivalent to shuffling load alone for this statistic. ``load_col``/``outcome_col``
    override the defaults (metric panel); returns ``(stat_obs, p, per_permutation_frame)``.
    """
    m = cfg["model"]
    load_col = load_col or str(m["load"])
    oc = outcome_col or "perf_z"
    d = build_design(df_model, cfg, include_load2=True, load_col=load_col)
    blocks = _player_slope_blocks(d, "load_c", oc)
    if not blocks:
        return float("nan"), float("nan"), pd.DataFrame(columns=["perm_index", "stat"])
    load_full = d["load_c"].to_numpy(dtype=np.float64)
    stat_obs = _meta_slope_from_blocks(blocks, load_full)
    perm_n = int(m["perm_n"])
    rng = np.random.default_rng(int(cfg["seed"]) + int(m["perm_seed_offset"]))
    # permute the (load_c, load2_c, first_attempt, drill) block jointly within each player
    block_cols = [c for c in ["load_c", "load2_c", str(m["first_attempt"]), "drill"] if c in d.columns]
    arrs = {c: d[c].to_numpy(copy=True) for c in block_cols}
    work = {c: v.copy() for c, v in arrs.items()}
    stats_out = np.empty(perm_n, dtype=np.float64)
    for b in range(perm_n):
        for blk in blocks:
            idx = blk["idx"]
            perm = rng.permutation(idx.size)
            for c in block_cols:
                work[c][idx] = arrs[c][idx][perm]
        stats_out[b] = _meta_slope_from_blocks(blocks, work["load_c"])
    stat_obs_f = stat_obs if np.isfinite(stat_obs) else 0.0
    finite = np.isfinite(stats_out)
    if finite.sum() == 0:
        p = float("nan")
    else:
        extreme = int((np.abs(stats_out[finite]) >= abs(stat_obs_f)).sum())
        p = float((1 + extreme) / (perm_n + 1))
    perm_df = pd.DataFrame({"perm_index": np.arange(1, perm_n + 1, dtype=np.int64), "stat": stats_out})
    return stat_obs, p, perm_df


# --------------------------------------------------------------------------- #
# Synthetic fixtures (used by tests; real attempt timings + deleted attempts)
# --------------------------------------------------------------------------- #
def simulate_synthetic(real_df: pd.DataFrame, cfg: dict[str, Any], rng: np.random.Generator,
                       n_players: int | None = None, planted_slopes: list[float] | None = None,
                       noise_sd: float = 0.6) -> pd.DataFrame:
    """Simulate players with known fatigue slopes on real attempt timings.

    Each synthetic player borrows a real player's attempt structure (drill order,
    timings, effort costs), is assigned a planted slope (cycled from
    ``model.synth_slopes``), loses ``model.synth_delete_frac`` of attempts (imputed
    load only when ``model.synth_impute``), and gets ``perf_z`` generated from the
    post-imputation cumulative load. Returns a study-schema frame with ``true_slope``.
    """
    m = cfg["model"]
    if n_players is None:
        n_players = int(m["synth_n_players"])
    if planted_slopes is None:
        planted_slopes = list(m["synth_slopes"])
    lo, hi = m["synth_attempts_range"]
    real = real_df[real_df["is_imputed"] == 0].copy()
    real["_t"] = pd.to_datetime(real["attempt_start_time"], errors="coerce")
    templates = []
    for _, g in real.groupby("nfl_id", sort=True):
        g = g.sort_values(["_t", "attempt", "event_id"], kind="mergesort")
        if len(g) >= 2:
            templates.append(g)
    if not templates:
        raise ValueError("simulate_synthetic: no real player templates with >= 2 attempts")
    rows: list[dict[str, Any]] = []
    for k in range(int(n_players)):
        tmpl = templates[k % len(templates)]
        b = float(planted_slopes[k % len(planted_slopes)])
        n_take = int(np.clip(len(tmpl), lo, hi))
        tl = tmpl.iloc[:n_take].copy()
        pid = 900000 + k
        tl["nfl_id"] = pid
        # randomly delete attempts (lost numbering slot -> imputed load only)
        keep = rng.random(len(tl)) >= float(m["synth_delete_frac"])
        if not keep.any():
            keep[0] = True
        obs = tl.loc[keep].copy()
        lost = tl.loc[~keep]
        obs["is_imputed"] = 0
        parts = [obs]
        if bool(m["synth_impute"]) and len(lost) > 0:
            med = float(obs["effort_cost_yd"].median()) if obs["effort_cost_yd"].notna().any() else 0.0
            imp = lost.copy()
            imp["is_imputed"] = 1
            imp["perf_z"] = np.nan
            imp["effort_cost_yd"] = med
            parts.append(imp)
        pl = pd.concat(parts, ignore_index=True)
        pl["_t"] = pd.to_datetime(pl["attempt_start_time"], errors="coerce")
        pl = pl.sort_values(["_t", "attempt", "is_imputed", "event_id"], na_position="last",
                            kind="mergesort")
        eff = pl["effort_cost_yd"].fillna(0.0).to_numpy(dtype=np.float64)
        pl["prior_load_yd"] = np.concatenate([[0.0], np.cumsum(eff)[:-1]])
        pl["prior_load_observed_yd"] = np.where(pl["is_imputed"].to_numpy() == 0, pl["prior_load_yd"], np.nan)
        pl["prior_load_efforts"] = np.arange(len(pl), dtype=np.int64)
        pl["prior_load_observed_efforts"] = np.arange(len(pl), dtype=np.int64)
        pl["combine_position"] = "DB"
        pl["draft_year"] = 2025
        pl["true_slope"] = b
        rows.append(pl)
    out = pd.concat(rows, ignore_index=True)
    # generate perf_z from the post-imputation cumulative load
    load = out["prior_load_yd"].to_numpy(dtype=np.float64)
    sd = float(np.std(load))
    x = (load - float(np.mean(load))) / sd if sd > 0 else np.zeros_like(load)
    intercepts = rng.normal(0.0, 0.4, size=int(out["nfl_id"].nunique()))
    imap = {p: intercepts[i] for i, p in enumerate(sorted(out["nfl_id"].unique()))}
    out["perf_z"] = out["true_slope"].to_numpy() * x \
        + out["nfl_id"].map(imap).to_numpy() \
        + rng.normal(0.0, float(noise_sd), size=len(out))
    out.loc[out["is_imputed"] == 1, "perf_z"] = np.nan  # imputed rows carry no performance
    return out


def synthetic_recovery_correlation(slopes: pd.DataFrame, truth: pd.DataFrame) -> float:
    """Pearson r between recovered (shrunken) slopes and the planted slopes."""
    key = "slope_shrunk" if "slope_shrunk" in slopes.columns else "slope"
    mrg = slopes.merge(truth[["nfl_id", "true_slope"]].drop_duplicates(), on="nfl_id", how="inner")
    mrg = mrg[np.isfinite(mrg[key].to_numpy(dtype=np.float64))]
    if len(mrg) < 3 or mrg["true_slope"].nunique() < 2:
        return float("nan")
    r, _ = stats.pearsonr(mrg[key].to_numpy(dtype=np.float64), mrg["true_slope"].to_numpy(dtype=np.float64))
    return float(r)


# --------------------------------------------------------------------------- #
# Writing helpers
# --------------------------------------------------------------------------- #
def _cast_select(columns: list[tuple[str, str]]) -> str:
    parts = []
    for name, typ in columns:
        if typ in ("INTEGER", "FLOAT", "BOOLEAN"):
            parts.append(f'TRY_CAST("{name}" AS {typ}) AS "{name}"')
        else:
            parts.append(f'CAST("{name}" AS {typ}) AS "{name}"')
    return ",\n  ".join(parts)


def _write_parquet(con, df: pd.DataFrame, columns: list[tuple[str, str]], path: Path, order_by: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    view = "_mdl_view"
    con.register(view, df[[c for c, _ in columns]])
    sql = (f"COPY (SELECT\n  {_cast_select(columns)}\n FROM {view}\n ORDER BY {order_by}) "
           f"TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(sql)
    con.unregister(view)


def _labelled_csv(df: pd.DataFrame, prov: str, path: Path) -> None:
    out = df.copy()
    out.insert(0, "provenance", prov)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# Core pipeline (callable with any study-schema frame — tests pass synthetic data)
# --------------------------------------------------------------------------- #
def run_pipeline(df_all: pd.DataFrame, cfg: dict[str, Any], mode: str, prov: str,
                 out_dir: Path, logger=None) -> dict[str, Any]:
    """Fit the Phase-3 model on ``df_all`` (study-schema rows) and write artefacts."""
    m = cfg["model"]
    threads = int(cfg.get("runtime", {}).get("duckdb_threads", 1))
    con = connect(threads=threads)
    seed_everything(int(cfg["seed"]))
    out_dir.mkdir(parents=True, exist_ok=True)

    df_all = df_all.copy()
    df_all["is_imputed"] = df_all["is_imputed"].fillna(0).astype(int)
    df_model = df_all[(df_all["is_imputed"] == 0) & df_all["perf_z"].notna()].copy()
    df_model["first_attempt"] = df_model["first_attempt"].fillna(0).astype(int)

    n_players = int(df_model["nfl_id"].nunique()) if len(df_model) else 0
    n_obs = int(len(df_model))

    # ---- stage: load / criterion ----
    with timed_stage(cfg, "03_combine_model:load", mode):
        crit = load2_criterion(df_model[m["load"]].to_numpy(dtype=np.float64), cfg)
        include_load2 = bool(crit["include"])
        if logger:
            logger.info("load2 included=%s (n_unique=%d, IQR=%.4g yd)", include_load2,
                        crit["n_unique"], crit["iqr_yd"])
    if not include_load2 and logger:
        logger.info("load2 DROPPED: n_unique(load)=%d (min %d) and IQR=%.4g yd (min %.4g)",
                    crit["n_unique"], crit["min_unique"], crit["iqr_yd"], crit["min_iqr_yd"])

    # ---- stage: baseline ----
    with timed_stage(cfg, "03_combine_model:baseline", mode):
        baseline = baseline_difference(df_model, cfg)
        base_tt = baseline_ttest(baseline)

    # ---- stage: primary LMM ----
    with timed_stage(cfg, "03_combine_model:lmm", mode):
        primary = fit_lmm_random_slopes(df_model, cfg, include_load2)

    # ---- stage: fallbacks ----
    with timed_stage(cfg, "03_combine_model:fallbacks", mode):
        chain: list[dict] = [primary]
        chosen = primary if primary["accepted"] else None
        for name in list(m["fallback_order"])[1:]:
            if chosen is not None:
                break
            r = FITTERS[str(name)](df_model, cfg, include_load2)
            chain.append(r)
            if r["accepted"]:
                chosen = r
        if chosen is None:
            chosen = chain[-1]
        # if the primary was accepted, the remaining fallbacks were not run
        ran_names = {r["estimator"] for r in chain}

    # ---- stage: robustness ----
    with timed_stage(cfg, "03_combine_model:robustness", mode):
        robust: dict[str, dict[str, Any]] = {}
        variants = {
            "observed_only_load": {"load_col": str(m["load_observed"]), "use_first_attempt": True},
            "without_first_attempt": {"load_col": str(m["load"]), "use_first_attempt": False},
            "observed_only_load_no_first_attempt": {"load_col": str(m["load_observed"]), "use_first_attempt": False},
        }
        for vname, v in variants.items():
            if str(v["load_col"]) not in df_model.columns:
                continue
            c, _ = fit_chain_variant(df_model, cfg, include_load2, v)
            robust[vname] = {"estimator": c["estimator"], "load_coef": c["load_coef"],
                             "load_se": c["load_se"], "load_p": c["load_p"],
                             "reliability": c["reliability"]}

    # ---- stage: permutation ----
    with timed_stage(cfg, "03_combine_model:permutation", mode):
        stat_obs, perm_p, perm_df = permutation_test(df_model, cfg)

    # ---- per-player slopes (handoff) + xdrill + diagnostics ----
    with timed_stage(cfg, "03_combine_model:diagnostics", mode):
        players = player_slope_table(df_model, df_all, chosen, cfg, prov)
        xdrill = xdrill_correlation(df_model, cfg)
        stop_triggered = bool(not (chosen["reliability"] >= float(m["reliability_stop_threshold"])))
        chosen["skip_phase5_link"] = bool(stop_triggered or chosen.get("skip_phase5_link", False))
        diagnostics = _diagnostics(cfg, mode, prov, chosen, chain, ran_names, include_load2, crit,
                                   base_tt, stat_obs, perm_p, players, n_players, n_obs, robust,
                                   stop_triggered)
        summary = _summary_json(cfg, mode, prov, chosen, chain, include_load2, crit, base_tt, stat_obs,
                                perm_p, players, n_players, n_obs, robust, stop_triggered)

    # ---- stage: write ----
    with timed_stage(cfg, "03_combine_model:write", mode):
        _write_parquet(con, players, PLAYER_SLOPE_COLUMNS, out_dir / "combine_player_slopes.parquet",
                       '"nfl_id"')
        plev = players.copy()
        if "provenance" in plev.columns:
            plev = plev.drop(columns=["provenance"])
        plev = plev[[c for c, _ in PLAYER_SLOPE_COLUMNS if c != "provenance"]]
        _labelled_csv(plev, prov, out_dir / "combine_player_slopes.csv")

        fe = chosen["fixed"].copy()
        fe.insert(0, "estimator", chosen["estimator"])
        fe.insert(0, "provenance", prov)
        fe = fe.sort_values("term", kind="mergesort")
        fe.to_csv(out_dir / "combine_model_fixed_effects.csv", index=False)

        rand = chosen["random"].copy()
        rand["method"] = chosen["estimator"]
        rand["provenance"] = prov
        rand = rand[["provenance", "nfl_id", "method", "re_intercept", "re_slope",
                     "se_intercept", "se_slope"]]
        _write_parquet(con, rand, RANDOM_EFFECT_COLUMNS, out_dir / "combine_model_random_effects.parquet",
                       '"nfl_id"')

        bl = baseline.copy().sort_values(["nfl_id", "drill_type"], kind="mergesort")
        _labelled_csv(bl, prov, out_dir / "combine_model_baseline.csv")

        _labelled_csv(perm_df, prov, out_dir / "combine_model_permutation.csv")
        _labelled_csv(xdrill, prov, out_dir / "combine_model_xdrill.csv")
        _labelled_csv(diagnostics, prov, out_dir / "combine_model_diagnostics.csv")

        (out_dir / "combine_model_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        (out_dir / "PROVENANCE.txt").write_text(
            f"{prov}\nGenerated {mode}-mode by src/03_combine_model.py. Combine fatigue model.\n",
            encoding="utf-8")
        (out_dir / "SUMMARY.md").write_text(_summary_md(summary, cfg), encoding="utf-8")

    if logger:
        logger.info("chosen estimator=%s reliability=%.4f stop=%s load_coef=%.6g p=%.4g perm_p=%.4g",
                    chosen["estimator"], chosen["reliability"], stop_triggered,
                    chosen["load_coef"], chosen["load_p"], perm_p)
    return summary


def fit_chain_variant(df: pd.DataFrame, cfg: dict[str, Any], include_load2: bool,
                      variant: dict[str, Any],
                      outcome_col: str | None = None) -> tuple[dict, list[dict]]:
    """Run the fallback chain for a robustness variant (load_col / first_attempt toggles)."""
    results: list[dict] = []
    chosen = None
    for name in cfg["model"]["fallback_order"]:
        r = FITTERS[str(name)](df, cfg, include_load2, use_first_attempt=bool(variant["use_first_attempt"]),
                               load_col=str(variant["load_col"]), outcome_col=outcome_col)
        results.append(r)
        if r["accepted"] and chosen is None:
            chosen = r
            break
    if chosen is None:
        chosen = results[-1]
    return chosen, results


def _diagnostics(cfg, mode, prov, chosen, chain, ran_names, include_load2, crit, base_tt,
                 stat_obs, perm_p, players, n_players, n_obs, robust, stop_triggered) -> pd.DataFrame:
    m = cfg["model"]

    def conv(estimator: str) -> Any:
        r = next((x for x in chain if x["estimator"] == estimator), None)
        if r is None:
            return "not_run"
        if not r["accepted"]:
            return "failed"
        return f"accepted({r['optimizer']})"

    rows = [
        ("mode", mode),
        ("estimator_chosen", chosen["estimator"]),
        ("optimizer_used", chosen["optimizer"]),
        ("n_estimators_run", len(chain)),
        ("n_players", n_players),
        ("n_obs", n_obs),
        ("seed", int(cfg["seed"])),
        ("converged_lmm_random_slopes", conv("lmm_random_slopes")),
        ("converged_lmm_uncorrelated_re", conv("lmm_uncorrelated_re")),
        ("converged_per_player_eb", conv("per_player_eb")),
        ("converged_random_intercept", conv("random_intercept")),
        ("load2_included", bool(include_load2)),
        ("load_n_unique", crit["n_unique"]),
        ("load_iqr_yd", crit["iqr_yd"]),
        ("load2_min_unique", crit["min_unique"]),
        ("load2_min_iqr_yd", crit["min_iqr_yd"]),
        ("tau2_final", chosen["tau2"]),
        ("mean_se2_final", chosen["mean_se2"]),
        ("reliability", chosen["reliability"]),
        ("reliability_min_players", int(m["reliability_min_players"])),
        ("reliability_stop_threshold", float(m["reliability_stop_threshold"])),
        ("stop_rule_triggered", bool(stop_triggered)),
        ("skip_phase5_link", bool(chosen.get("skip_phase5_link", False))),
        ("population_load_coef", chosen["load_coef"]),
        ("population_load_se", chosen["load_se"]),
        ("population_load_p", chosen["load_p"]),
        ("baseline_mean", base_tt["mean"]),
        ("baseline_sd", base_tt["sd"]),
        ("baseline_t", base_tt["t"]),
        ("baseline_p", base_tt["p"]),
        ("baseline_n_players", base_tt["n"]),
        ("perm_stat", str(m["perm_stat"])),
        ("perm_stat_observed", stat_obs),
        ("perm_p", perm_p),
        ("perm_n", int(m["perm_n"])),
        ("perm_seed_offset", int(m["perm_seed_offset"])),
        ("link_min_attempts", int(m["link_min_attempts"])),
        ("n_players_meeting_link_min", int(players["meets_link_min_attempts"].sum())),
    ]
    for vname, v in robust.items():
        rows += [(f"robust_{vname}_estimator", v["estimator"]),
                 (f"robust_{vname}_load_coef", v["load_coef"]),
                 (f"robust_{vname}_load_p", v["load_p"]),
                 (f"robust_{vname}_reliability", v["reliability"])]
    return pd.DataFrame(rows, columns=["metric", "value"])


def _nanrun(x: Any) -> Any:
    if isinstance(x, float) and not np.isfinite(x):
        return None
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def _summary_json(cfg, mode, prov, chosen, chain, include_load2, crit, base_tt, stat_obs, perm_p,
                  players, n_players, n_obs, robust, stop_triggered) -> dict[str, Any]:
    m = cfg["model"]
    return {
        "provenance": prov,
        "mode": mode,
        "estimator_chosen": chosen["estimator"],
        "optimizer_used": chosen["optimizer"],
        "convergence": {r["estimator"]: (r["optimizer"] if r["accepted"] else False) for r in chain},
        "n_players": n_players,
        "n_obs": n_obs,
        "seed": int(cfg["seed"]),
        "load2_included": bool(include_load2),
        "load_n_unique": crit["n_unique"],
        "load_iqr_yd": _nanrun(crit["iqr_yd"]),
        "reliability": _nanrun(chosen["reliability"]),
        "reliability_stop_threshold": float(m["reliability_stop_threshold"]),
        "stop_rule_triggered": bool(stop_triggered),
        "skip_phase5_link": bool(chosen.get("skip_phase5_link", False)),
        "population_load_coef": _nanrun(chosen["load_coef"]),
        "population_load_se": _nanrun(chosen["load_se"]),
        "population_load_p": _nanrun(chosen["load_p"]),
        "baseline": {k: _nanrun(v) for k, v in base_tt.items()},
        "permutation": {"stat": str(m["perm_stat"]), "stat_observed": _nanrun(stat_obs),
                        "p": _nanrun(perm_p), "n": int(m["perm_n"])},
        "robustness": {k: {kk: _nanrun(vv) for kk, vv in v.items()} for k, v in robust.items()},
        "n_players_meeting_link_min": int(players["meets_link_min_attempts"].sum()),
        "link_min_attempts": int(m["link_min_attempts"]),
    }


def _summary_md(summary: dict[str, Any], cfg: dict[str, Any]) -> str:
    m = cfg["model"]
    s = summary
    null = bool(s["stop_rule_triggered"])
    lines = [
        f"# Combine fatigue model summary — {s['provenance']}",
        "",
        f"- mode: `{s['mode']}`  ·  provenance: `{s['provenance']}`",
        f"- observations: **{s['n_obs']}** model rows across **{s['n_players']}** players "
        f"(seed `{s['seed']}`)",
        f"- load² included: **{s['load2_included']}** "
        f"(n_unique(load)={s['load_n_unique']} ≥ {m['load2_min_unique']}, "
        f"IQR(load)={s['load_iqr_yd']} ≥ {m['load2_min_iqr_yd']})",
        f"- **estimator chosen: `{s['estimator_chosen']}`** (optimiser `{s['optimizer_used']}`)",
        f"- **reliability: {s['reliability']}** "
        f"(stop threshold {s['reliability_stop_threshold']}) → "
        f"**stop_rule_triggered = {s['stop_rule_triggered']}**",
        f"- population load coefficient: {s['population_load_coef']} "
        f"(SE {s['population_load_se']}, p {s['population_load_p']})",
        f"- baseline (late−early, per-player mean): {s['baseline']['mean']} "
        f"(sd {s['baseline']['sd']}, t {s['baseline']['t']}, p {s['baseline']['p']}, "
        f"n {s['baseline']['n']})",
        f"- permutation ({s['permutation']['stat']}): observed {s['permutation']['stat_observed']}, "
        f"p {s['permutation']['p']} ({s['permutation']['n']} permutations)",
        f"- players meeting ≥{s['link_min_attempts']} observed attempts: "
        f"{s['n_players_meeting_link_min']}",
        "",
    ]
    if null:
        lines += [
            "## Finding: NULL (stop rule triggered)",
            "",
            f"The final estimator's model-based slope reliability "
            f"({s['reliability']}) is below the pre-registered stop threshold "
            f"({s['reliability_stop_threshold']}). **Per the pre-registered stop rule "
            "(TASK.md Phase 3), the finding is reported as a NULL**: the Combine data do "
            "not support a reliable per-player fatigue slope at this population, so no "
            "Combine-fatigue→in-game-decay link can be estimated from these slopes. "
            "Per-player slopes are still emitted (flagged) for completeness.",
            "",
        ]
    else:
        lines += [
            "## Finding",
            "",
            f"Slope reliability ({s['reliability']}) clears the stop threshold "
            f"({s['reliability_stop_threshold']}); the population load slope is "
            f"{s['population_load_coef']} (p {s['population_load_p']}) with permutation "
            f"p {s['permutation']['p']}.",
            "",
        ]
    lines += [
        "## Robustness",
        "",
    ]
    for k, v in s["robustness"].items():
        lines.append(f"- `{k}`: estimator `{v['estimator']}`, load coef {v['load_coef']} "
                     f"(p {v['load_p']}), reliability {v['reliability']}")
    lines += [
        "",
        "*(All CSVs carry a `provenance` first column; parquet tables carry a `provenance` "
        "column. Sample outputs are UNVALIDATED SAMPLE OUTPUT.)*",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def load_study(path: Path) -> pd.DataFrame:
    """Read the study parquet with explicit columns (never full-CSV pandas)."""
    con = connect(threads=1)
    return con.execute(f"SELECT {', '.join(STUDY_COLUMNS)} FROM read_parquet('{path}')").fetchdf()


def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    prov = provenance(cfg, mode)
    out_dir = resolve(cfg, "outputs", cfg["model"]["out_subdir"])
    main_out = out_dir / "combine_player_slopes.parquet"

    logger.info("=" * 72)
    logger.info("BDB27 Phase 3 combine model | mode=%s | provenance=%s", mode, prov)
    logger.info("=" * 72)

    if main_out.exists() and not force:
        logger.info("checkpoint hit: %s exists (use --force to rebuild)", main_out.name)
        append_run_log(cfg, "03_combine_model:checkpoint", mode, 0.0, peak_ram_mb(), "",
                       {"skipped": True, "reason": "combine_player_slopes.parquet exists; use --force"})
        return 0

    study = resolve(cfg, "outputs", "features") / "combine_features_study.parquet"
    if not study.exists():
        raise FileNotFoundError(f"missing Phase-2 handoff: {study} (run src/02_features.py first)")

    with timed_stage(cfg, "03_combine_model:load", mode):
        df_all = load_study(study)
    logger.info("loaded study rows: %d (players=%d)", len(df_all), df_all["nfl_id"].nunique())

    summary = run_pipeline(df_all, cfg, mode, prov, out_dir, logger=logger)
    logger.info("wrote %s", out_dir)
    logger.info("peak RAM: %.1f MB", peak_ram_mb())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 3 — Combine fatigue model")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild outputs (ignore checkpoint)")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or cfg["mode"]["default"]
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
