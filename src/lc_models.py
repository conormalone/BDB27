"""lc_models — within-player leverage model used by the synthetic recovery tests.

The confirmatory estimand in ``TASK.md`` is the population coefficient on
leverage in a within-player model.  This module implements that model with
player fixed effects (the third, most conservative convergence fallback) and
player-clustered standard errors.  It also implements the validation refit in
which leverage is orthogonalised to game time and cumulative load -- the check
that decides whether a leverage effect is really a fatigue/game-clock effect.

The point here is *correctness of the estimator*, not feature richness: the same
functions are what the future 03/04 stages call, so the synthetic worlds test
the real code path rather than a test-only approximation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

# `route_depth_yd` is deliberately *not* a default control.  In tracking it is
# total start-to-end displacement, which is mechanically a function of speed over
# a fixed route duration and therefore post-treatment.  The full-data stage must
# use the designed route depth at the break; until that column exists, route
# family is the depth control and the realized displacement stays descriptive.
DEFAULT_CONTROLS = ("route_family", "time_to_throw_s", "score_margin", "down",
                    "yards_to_go", "prior_efforts")
TIME_LOAD_CONTROLS = ("game_elapsed_s", "prior_efforts")


def _numeric_controls(df: pd.DataFrame, controls) -> tuple[list[np.ndarray], list[str]]:
    cols, names = [], []
    for name in controls:
        if name == "route_family":
            dummies = pd.get_dummies(df["route_family"].astype(str), drop_first=True,
                                     dtype=float)
            for col in dummies.columns:
                cols.append(dummies[col].to_numpy(dtype=float))
                names.append(f"route={col}")
        elif name in df.columns:
            values = pd.to_numeric(df[name], errors="coerce").astype(float)
            values = values.fillna(values.median() if values.notna().any() else 0.0)
            sd = values.std(ddof=0)
            cols.append(((values - values.mean()) / (sd if sd > 0 else 1.0)).to_numpy())
            names.append(name)
    return cols, names


def design_matrix(df: pd.DataFrame, leverage_col: str, controls=DEFAULT_CONTROLS,
                  player_col: str = "nfl_id") -> tuple[np.ndarray, list[str], int]:
    """Return (X, names, leverage_index) with player fixed effects."""
    players = pd.get_dummies(df[player_col].astype(str), drop_first=True, dtype=float)
    cols = [np.ones(len(df)), df[leverage_col].to_numpy(dtype=float)]
    names = ["intercept", leverage_col]
    numeric, numeric_names = _numeric_controls(df, controls)
    cols.extend(numeric)
    names.extend(numeric_names)
    for col in players.columns:
        cols.append(players[col].to_numpy(dtype=float))
        names.append(f"player={col}")
    X = np.column_stack(cols)
    return X, names, 1


def _ols_cluster(df: pd.DataFrame, y: np.ndarray, X: np.ndarray,
                 leverage_idx: int, player_col: str) -> dict:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    xtx_inv = np.linalg.pinv(X.T @ X)

    clusters = df[player_col].astype(str).to_numpy()
    meat = np.zeros((k, k), dtype=float)
    for cluster in np.unique(clusters):
        mask = clusters == cluster
        xg = X[mask]
        eg = resid[mask]
        score = xg.T @ eg
        meat += np.outer(score, score)
    g = int(len(np.unique(clusters)))
    if g > 1:
        correction = (g / (g - 1.0)) * ((n - 1.0) / max(1.0, n - k))
    else:
        correction = 1.0
    cov = xtx_inv @ meat @ xtx_inv * correction
    se = float(np.sqrt(max(cov[leverage_idx, leverage_idx], 0.0)))
    b = float(beta[leverage_idx])
    dof = max(1, g - 1)
    t_stat = b / se if se > 0 else float("nan")
    p_value = float(2.0 * stats.t.sf(abs(t_stat), dof)) if np.isfinite(t_stat) else float("nan")
    crit = float(stats.t.ppf(0.975, dof))
    return {
        "beta": b, "se": se, "t": float(t_stat), "p": p_value,
        "ci_low": b - crit * se, "ci_high": b + crit * se,
        "n": int(n), "n_players": int(g), "n_params": int(k),
        "resid_sd": float(np.std(resid, ddof=1)) if n > 1 else float("nan"),
        "cluster": "player",
    }


def fit_within_player(df: pd.DataFrame, outcome: str = "log_max_speed",
                      leverage_col: str = "leverage_z",
                      controls=DEFAULT_CONTROLS,
                      player_col: str = "nfl_id") -> dict:
    """Player-FE OLS with player-clustered SE on the leverage coefficient."""
    needed = [outcome, leverage_col, player_col]
    work = df.dropna(subset=needed).copy()
    if len(work) < 3 or work[player_col].nunique() < 2:
        return {"beta": float("nan"), "se": float("nan"), "t": float("nan"),
                "p": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"),
                "n": int(len(work)), "n_players": int(work[player_col].nunique()),
                "n_params": 0, "resid_sd": float("nan"), "cluster": "player"}
    X, _, idx = design_matrix(work, leverage_col, controls, player_col)
    result = _ols_cluster(work, work[outcome].to_numpy(dtype=float), X, idx, player_col)
    result["model"] = "within_player_fe"
    result["leverage_col"] = leverage_col
    result["controls"] = list(controls)
    return result


def orthogonalise_leverage(df: pd.DataFrame, leverage_col: str = "leverage_z",
                           controls=TIME_LOAD_CONTROLS,
                           player_col: str = "nfl_id") -> pd.Series:
    """Residual of leverage after game time, load and player means are removed.

    This is the time-and-load confound refit required by ``TASK.md`` Phase 5:
    if an apparent leverage effect vanishes when the residual is used, it was a
    fatigue or game-clock effect.
    """
    work = df.copy()
    y = work[leverage_col].astype(float)
    # Demean leverage *and* every control within player.  A global slope plus
    # player intercepts does not remove player-specific time trends; the
    # within-player projection does, which is the confound the design claims to
    # separate.
    groups = work[player_col]
    y_dm = y - y.groupby(groups).transform("mean")
    control_cols = []
    for name in controls:
        if name in work.columns:
            values = pd.to_numeric(work[name], errors="coerce").astype(float)
            values = values.fillna(values.median() if values.notna().any() else 0.0)
            means = values.groupby(groups).transform("mean")
            sds = values.groupby(groups).transform("std")
            sds = sds.where(sds > 0, 1.0)
            control_cols.append((values - means) / sds)
    if not control_cols:
        return y_dm.rename(f"{leverage_col}_orth")
    X = np.column_stack(control_cols)
    beta, *_ = np.linalg.lstsq(X, y_dm.to_numpy(dtype=float), rcond=None)
    resid = y_dm.to_numpy(dtype=float) - X @ beta
    return pd.Series(resid, index=work.index, name=f"{leverage_col}_orth")


def fit_orthogonalised(df: pd.DataFrame, outcome: str = "log_max_speed",
                       leverage_col: str = "leverage_z",
                       controls=DEFAULT_CONTROLS,
                       player_col: str = "nfl_id") -> dict:
    work = df.copy()
    work["_lev_orth"] = orthogonalise_leverage(work, leverage_col, player_col=player_col)
    result = fit_within_player(work, outcome, "_lev_orth", controls, player_col)
    result["model"] = "within_player_fe_orthogonalised"
    result["orthogonalised_on"] = list(TIME_LOAD_CONTROLS)
    return result
