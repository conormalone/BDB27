"""models — Phase 4 variance decomposition (burst vs. jump).

This module implements the *estimator* for TASK.md Phase 4. It is deliberately
free of data loading and file writing so the synthetic tests can call it with
planted parameters.

Model fitted
------------
For rep ``j`` of player ``i`` in team-game ``g``::

    y_ij = a + b * phys_ij + alignment_ij + depth_ij + u_i + v_g + e_ij
    u_i ~ N(0, sigma_u^2),  v_g ~ N(0, sigma_v^2),  e_ij ~ N(0, sigma_e^2)

``y`` is the tracked LOS crossing (``cross_time_s``), ``phys`` is the
player-specific Combine physical time interpolated at that rep's pre-snap depth.
Stance is not a term: no stance label exists (B8); stance variation is absorbed
into ``u_i`` and ``e_ij``. No pressure field and no blocker term enter.

Estimator
---------
The mixed model is fitted by **expectation-maximisation on Henderson's mixed
model equations** (two crossed random intercepts). This is a method-of-moments /
maximum-likelihood hybrid, not a Bayesian posterior: PyMC/Stan are not available
on this host, so the Bayesian fit is deferred to the bigger machine. Every
number produced here is labelled with the estimator that produced it.

Measurement error
-----------------
``phys`` is latent: Phase 2 carries a posterior SD for each player-distance
point. The shared part of that error (the piece common to all of a player's
reps) is handled by **regression calibration**:

    s2_i = (mean_j se_ij)^2                     shared error variance, player i
    V    = between-player variance of phys, net of the population depth curve
    sigma_u^2_cal = max(0, sigma_u^2_fit - b^2 * mean_i s2_i)
    Var(b*phys)   = b^2 * max(0, V - mean_i s2_i)

Subtracting the shared error from both components is what stops a player-level
measurement error from masquerading as jump variance. The uncorrected fit is
reported side by side (the fallback ladder's rung 1).

Uncertainty
-----------
95% intervals are **player-cluster bootstrap percentile intervals**: players are
resampled with replacement, their reps carried along, and the whole estimator
refitted. This is deterministic given the configured seed. It is not a posterior
interval; the Bayesian fit is deferred.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

import stats as ST


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _onehot(codes: np.ndarray) -> np.ndarray:
    """Dense one-hot matrix for non-negative integer codes."""
    codes = np.asarray(codes, dtype=np.int64)
    q = int(codes.max()) + 1
    Z = np.zeros((len(codes), q), dtype=float)
    Z[np.arange(len(codes)), codes] = 1.0
    return Z


def _variance(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return float("nan")
    return float(np.var(values, ddof=1))


def _pct(values: Sequence[float], q: float) -> float:
    vals = np.asarray([v for v in values if v is not None and np.isfinite(v)],
                      dtype=float)
    if len(vals) == 0:
        return float("nan")
    return float(np.percentile(vals, q))


# --------------------------------------------------------------------------- #
# Linear mixed model: two crossed random intercepts, EM on Henderson equations
# --------------------------------------------------------------------------- #
def fit_lmm(y, X, player_codes, team_game_codes, *, max_iter: int = 100,
            tol: float = 1e-6) -> dict:
    """Fit ``y = X beta + Z1 u + Z2 v + e`` by EM.

    ``X`` includes the intercept. ``player_codes`` and ``team_game_codes`` are
    integer arrays 0..q-1. Returns the fixed effects, BLUPs, variance
    components, iterations, convergence flag and the posterior variance of each
    player effect (``u_var``).

    This is the estimator named in the module docstring. It is deterministic: no
    random initialisation, no sampler.
    """
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, p = X.shape
    if n < 3 or p < 1:
        raise ValueError("fit_lmm needs at least 3 observations and 1 covariate")
    g1 = np.asarray(player_codes, dtype=np.int64)
    g2 = np.asarray(team_game_codes, dtype=np.int64)
    Z1, Z2 = _onehot(g1), _onehot(g2)
    q1, q2 = Z1.shape[1], Z2.shape[1]
    if q1 < 2:
        raise ValueError("fit_lmm needs at least two players")

    XtX = X.T @ X
    XtZ1, XtZ2 = X.T @ Z1, X.T @ Z2
    Z1tZ1, Z2tZ2 = Z1.T @ Z1, Z2.T @ Z2
    Z1tZ2 = Z1.T @ Z2
    rhs = np.concatenate([X.T @ y, Z1.T @ y, Z2.T @ y])
    total = p + q1 + q2

    vy = float(np.var(y))
    su2 = max(1e-8, 0.3 * vy)
    sv2 = max(1e-8, 0.2 * vy)
    se2 = max(1e-8, 0.4 * vy)
    converged = False
    delta = float("inf")
    it = 0
    C = None
    sol = None

    for it in range(1, int(max_iter) + 1):
        A = np.zeros((total, total), dtype=float)
        A[:p, :p] = XtX
        A[:p, p:p + q1] = XtZ1
        A[:p, p + q1:] = XtZ2
        A[p:p + q1, :p] = XtZ1.T
        A[p:p + q1, p:p + q1] = Z1tZ1 + np.eye(q1) * (se2 / su2)
        A[p:p + q1, p + q1:] = Z1tZ2
        A[p + q1:, :p] = XtZ2.T
        A[p + q1:, p:p + q1] = Z1tZ2.T
        A[p + q1:, p + q1:] = Z2tZ2 + np.eye(q2) * (se2 / sv2)
        try:
            C = np.linalg.inv(A)
        except np.linalg.LinAlgError:  # pragma: no cover - defensive
            C = np.linalg.pinv(A)
        sol = C @ rhs
        beta = sol[:p]
        u = sol[p:p + q1]
        v = sol[p + q1:]
        resid = y - X @ beta - Z1 @ u - Z2 @ v
        tu = float(np.trace(C[p:p + q1, p:p + q1]))
        tv = float(np.trace(C[p + q1:, p + q1:]))
        su2_new = max(1e-10, (float(u @ u) + se2 * tu) / q1)
        sv2_new = max(1e-10, (float(v @ v) + se2 * tv) / q2)
        # Residual variance: E[e'e | y] = r'r + se2 * trace(I - W C W').
        # The trace term below is the standard EM correction; dividing by
        # (n - p) keeps the update stable when the random-effect blocks are
        # nearly singular.
        se2_new = max(
            1e-10,
            (float(resid @ resid) + se2 * (tu * se2 / su2 + tv * se2 / sv2))
            / max(1.0, n - p),
        )
        delta = max(
            abs(su2_new - su2) / max(su2, 1e-12),
            abs(sv2_new - sv2) / max(sv2, 1e-12),
            abs(se2_new - se2) / max(se2, 1e-12),
        )
        su2, sv2, se2 = su2_new, sv2_new, se2_new
        if delta < tol:
            converged = True
            break

    # One final solve at the converged components, for the BLUPs and their
    # posterior variance. (The loop's last solve used the previous components.)
    A = np.zeros((total, total), dtype=float)
    A[:p, :p] = XtX
    A[:p, p:p + q1] = XtZ1
    A[:p, p + q1:] = XtZ2
    A[p:p + q1, :p] = XtZ1.T
    A[p:p + q1, p:p + q1] = Z1tZ1 + np.eye(q1) * (se2 / su2)
    A[p:p + q1, p + q1:] = Z1tZ2
    A[p + q1:, :p] = XtZ2.T
    A[p + q1:, p:p + q1] = Z1tZ2.T
    A[p + q1:, p + q1:] = Z2tZ2 + np.eye(q2) * (se2 / sv2)
    try:
        C = np.linalg.inv(A)
    except np.linalg.LinAlgError:  # pragma: no cover - defensive
        C = np.linalg.pinv(A)
    sol = C @ rhs
    beta = sol[:p]
    u = sol[p:p + q1]
    v = sol[p + q1:]
    u_var = se2 * np.diag(C[p:p + q1, p:p + q1])
    return {
        "beta": beta,
        "u": u,
        "v": v,
        "u_var": u_var,
        "sigma_u2": float(su2),
        "sigma_v2": float(sv2),
        "sigma_e2": float(se2),
        "iters": int(it),
        "converged": bool(converged),
        "delta": float(delta),
        "n": int(n),
        "p": int(p),
        "q_players": int(q1),
        "q_team_games": int(q2),
    }


# --------------------------------------------------------------------------- #
# Population depth curve and design
# --------------------------------------------------------------------------- #
def population_phys_curve(depth: np.ndarray, phys: np.ndarray,
                          degree: int) -> np.ndarray:
    """Pooled polynomial fit of physical time on depth.

    This is a nuisance curve used only to net depth out of the player-level
    physical deviation. It is *not* the model's depth effect: the mixed model
    has its own depth term. The degree is reduced deterministically when there
    are too few distinct depths, and never exceeds the configured value.
    """
    depth = np.asarray(depth, dtype=float)
    phys = np.asarray(phys, dtype=float)
    uniq = np.unique(depth[np.isfinite(depth)])
    deg = int(max(0, min(int(degree), len(uniq) - 1)))
    if deg == 0:
        return np.full_like(phys, float(np.mean(phys)))
    X = np.column_stack([depth ** k for k in range(deg + 1)])
    beta, *_ = np.linalg.lstsq(X, phys, rcond=None)
    return X @ beta


def build_design(df: pd.DataFrame) -> tuple[np.ndarray, int]:
    """Fixed-effect design: intercept, depth, phys, edge indicator if present.

    Columns are dropped if they are constant. ``phys`` is mandatory; if the
    remaining design is rank-deficient the caller receives an error rather than
    a silent reparameterisation.
    """
    cols = [np.ones(len(df), dtype=float)]
    names = ["intercept"]

    def add(values, name):
        v = np.asarray(values, dtype=float)
        if np.isfinite(v).all() and np.ptp(v) > 0:
            cols.append(v)
            names.append(name)

    add(df["depth_yd"].to_numpy(), "depth")
    phys_idx = len(cols)
    cols.append(np.asarray(df["phys_s"], dtype=float))
    names.append("phys")
    if df["alignment_class"].nunique(dropna=True) > 1:
        edge = (df["alignment_class"].astype(str).str.upper() == "EDGE")
        add(edge.astype(float).to_numpy(), "edge")
    X = np.column_stack(cols)
    if np.linalg.matrix_rank(X) < X.shape[1]:
        raise ValueError("fixed-effect design is rank-deficient")
    return X, phys_idx


# --------------------------------------------------------------------------- #
# Variance decomposition
# --------------------------------------------------------------------------- #
def decompose(df: pd.DataFrame, *, correct_measurement_error: bool,
              population_degree: int = 3, max_iter: int = 100,
              tol: float = 1e-6) -> dict:
    """Fit the Phase 4 mixed model and return the variance decomposition.

    ``df`` must contain: ``player``, ``team_game``, ``y`` (tracked crossing),
    ``phys_s``, ``phys_se_s``, ``depth_yd``, ``alignment_class``.

    ``correct_measurement_error=False`` is the fallback ladder's rung 1: the
    same model with phys as an ordinary fixed covariate and no measurement-error
    correction. Both are returned by the stage; the primary is the corrected
    fit.
    """
    need = {"player", "team_game", "y", "phys_s", "phys_se_s", "depth_yd",
            "alignment_class"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"decompose missing columns: {sorted(missing)}")
    d = df.copy()
    d = d[np.isfinite(d["y"].to_numpy(dtype=float))]
    d = d[np.isfinite(d["phys_s"].to_numpy(dtype=float))]
    d = d[np.isfinite(d["phys_se_s"].to_numpy(dtype=float))]
    d = d[np.isfinite(d["depth_yd"].to_numpy(dtype=float))]
    if d["player"].nunique() < 2:
        return {"status": "INSUFFICIENT_PLAYERS", "n_players": int(d["player"].nunique())}

    X, phys_idx = build_design(d)
    y = d["y"].to_numpy(dtype=float)
    pcode, _ = pd.factorize(d["player"], sort=True)
    gcode, _ = pd.factorize(d["team_game"], sort=True)
    fit = fit_lmm(y, X, pcode, gcode, max_iter=max_iter, tol=tol)
    b = float(fit["beta"][phys_idx])

    # Population depth curve, then the player-level physical deviation.
    mu = population_phys_curve(d["depth_yd"].to_numpy(), d["phys_s"].to_numpy(),
                               population_degree)
    d["phys_dev"] = d["phys_s"].to_numpy(dtype=float) - mu
    player = d.groupby("player", sort=True).agg(
        n_reps=("y", "size"),
        n_games=("team_game", "nunique"),
        phys_dev=("phys_dev", "mean"),
        phys_se=("phys_se_s", "mean"),
        n_attempts_used=("n_attempts_used", "max")
        if "n_attempts_used" in d.columns else ("y", "size"),
    )
    V = _variance(player["phys_dev"].to_numpy(dtype=float))
    s2 = float(np.mean(player["phys_se"].to_numpy(dtype=float) ** 2))

    # Measurement-error regression calibration (shared player-level error).
    if correct_measurement_error:
        su2 = max(0.0, float(fit["sigma_u2"]) - b * b * s2)
        var_phys = b * b * max(0.0, V - s2)
    else:
        su2 = float(fit["sigma_u2"])
        var_phys = b * b * max(0.0, V)
    se2 = float(fit["sigma_e2"])
    sv2 = float(fit["sigma_v2"])
    denom_between = su2 + var_phys
    denom_total = denom_between + se2
    share_u = su2 / denom_between if denom_between > 0 else float("nan")
    share_phys = var_phys / denom_between if denom_between > 0 else float("nan")
    within_total = se2 / denom_total if denom_total > 0 else float("nan")
    u_total = su2 / denom_total if denom_total > 0 else float("nan")
    phys_total = var_phys / denom_total if denom_total > 0 else float("nan")

    u = fit["u"]
    u_se = np.sqrt(np.maximum(fit["u_var"], 0.0))
    player["u_s"] = u[:len(player)]
    player["u_se_s"] = u_se[:len(player)]

    # Residuals for the split-half reliability (u_i + e_ij, after all fixed
    # effects and both random effects).
    Z1 = _onehot(pcode)
    Z2 = _onehot(gcode)
    resid = y - X @ fit["beta"] - Z1 @ fit["u"] - Z2 @ fit["v"]
    # For the split-half reliability we need u_i + e_ij, i.e. the part *before*
    # the player BLUP is removed. Removing u would leave only e and destroy the
    # between-player signal the split-half is meant to measure.
    resid_u = y - X @ fit["beta"] - Z2 @ fit["v"]

    return {
        "status": "OK",
        "n_reps": int(len(d)),
        "n_players": int(d["player"].nunique()),
        "n_team_games": int(d["team_game"].nunique()),
        "n_singleton_team_games": int((d.groupby("team_game").size() == 1).sum()),
        "b": b,
        "b_fit": b,
        "sigma_u2": su2,
        "sigma_u2_fit": float(fit["sigma_u2"]),
        "sigma_v2": sv2,
        "sigma_e2": se2,
        "between_phys_var": var_phys,
        "between_player_phys_var_observed": V,
        "shared_me_var": s2,
        "share_u": share_u,
        "share_phys": share_phys,
        "share_u_total": u_total,
        "share_phys_total": phys_total,
        "within_player_remainder_total": within_total,
        "jump_sd_s": math.sqrt(max(0.0, su2)),
        "jump_sd_ms": 1000.0 * math.sqrt(max(0.0, su2)),
        "converged": bool(fit["converged"]),
        "iters": int(fit["iters"]),
        "delta": float(fit["delta"]),
        "fit": fit,
        "player_table": player.reset_index(),
        "resid": resid,
        "resid_u": resid_u,
        "data": d,
    }


# --------------------------------------------------------------------------- #
# Bootstrap intervals (player-cluster)
# --------------------------------------------------------------------------- #
def bootstrap_decomposition(df: pd.DataFrame, *, correct_measurement_error: bool,
                            resamples: int, seed: int, population_degree: int = 3,
                            max_iter: int = 100, tol: float = 1e-6,
                            min_ok_share: float = 0.5,
                            point: Mapping | None = None) -> dict:
    """Player-cluster bootstrap for b, the shares and the variance components.

    Reported intervals are **basic (pivotal) bootstrap** intervals:
    ``[2*hat - q97.5, 2*hat - q2.5]``. Unlike a percentile interval they can
    extend below zero, which matters for a variance share whose null value is
    zero. Percentile intervals are also written for transparency.
    """
    rng = np.random.default_rng(int(seed))
    players = np.array(sorted(df["player"].dropna().unique()))
    k = len(players)
    if k < 2:
        return {"n_requested": int(resamples), "n_ok": 0, "status": "INSUFFICIENT_PLAYERS"}
    draws = {
        "b": [], "share_u": [], "share_phys": [],
        "sigma_u2": [], "sigma_v2": [], "sigma_e2": [],
        "between_phys_var": [], "within_player_remainder_total": [],
    }
    if point is None:
        point = decompose(df, correct_measurement_error=correct_measurement_error,
                          population_degree=population_degree,
                          max_iter=max_iter, tol=tol)
    n_ok = 0
    for _ in range(int(resamples)):
        pick = rng.choice(players, size=k, replace=True)
        parts = []
        for new_id, old_id in enumerate(pick):
            sub = df[df["player"] == old_id].copy()
            if sub.empty:
                continue
            sub["player"] = int(new_id)
            parts.append(sub)
        if not parts:
            continue
        boot = pd.concat(parts, ignore_index=True)
        try:
            r = decompose(boot, correct_measurement_error=correct_measurement_error,
                          population_degree=population_degree,
                          max_iter=max_iter, tol=tol)
        except (ValueError, np.linalg.LinAlgError):
            continue
        if r.get("status") != "OK":
            continue
        n_ok += 1
        for key in draws:
            val = r.get(key)
            if val is not None and np.isfinite(val):
                draws[key].append(float(val))
    out = {"n_requested": int(resamples), "n_ok": int(n_ok),
           "status": "OK" if n_ok / max(1, int(resamples)) >= min_ok_share else "FAILED"}
    for key, vals in draws.items():
        if not vals:
            out[f"{key}_ci95"] = [float("nan"), float("nan")]
            out[f"{key}_ci95_percentile"] = [float("nan"), float("nan")]
            continue
        lo, hi = _pct(vals, 2.5), _pct(vals, 97.5)
        out[f"{key}_ci95_percentile"] = [lo, hi]
        hat = point.get(key) if isinstance(point, Mapping) else None
        try:
            hat = float(hat)
        except (TypeError, ValueError):
            hat = float("nan")
        if not np.isfinite(hat):
            out[f"{key}_ci95"] = [lo, hi]
            continue
        basic = [2.0 * hat - hi, 2.0 * hat - lo]
        # A variance / share cannot be negative; preserve the zero boundary.
        # b is a coefficient and may legitimately be negative.
        if key in ("sigma_u2", "sigma_v2", "sigma_e2", "between_phys_var",
                   "share_u", "share_phys", "within_player_remainder_total"):
            basic[0] = max(0.0, basic[0])
        if key in ("share_u", "share_phys", "within_player_remainder_total"):
            basic[1] = min(1.0, basic[1])
        out[f"{key}_ci95"] = basic
    return out


# --------------------------------------------------------------------------- #
# Reliability and agreement
# --------------------------------------------------------------------------- #
def split_half_reliability(df: pd.DataFrame, resid: np.ndarray) -> dict:
    """Split-half across games (odd vs even) of the player-level residual.

    The residual is ``u_i + e_ij`` after the fixed and random effects. Within a
    player, games are ordered by game id and alternate odd/even; the two half
    means are correlated across players. Spearman-Brown steps the half
    correlation up to the full-game reliability.
    """
    d = df.copy()
    d["_resid"] = np.asarray(resid, dtype=float)
    halves = []
    for pid, sub in d.groupby("player", sort=True):
        games = np.array(sorted(sub["game_id"].dropna().unique())) if "game_id" in sub else np.array([])
        if len(games) < 2:
            continue
        odd = set(games[0::2])
        even = set(games[1::2])
        o = sub[sub["game_id"].isin(odd)]["_resid"]
        e = sub[sub["game_id"].isin(even)]["_resid"]
        if len(o) and len(e):
            halves.append((float(o.mean()), float(e.mean())))
    r = ST.pearson([a for a, _ in halves], [b for _, b in halves]) if len(halves) >= 3 else float("nan")
    return {
        "n_players": int(len(halves)),
        "r_half": r,
        "spearman_brown": ST.spearman_brown(r, 2) if np.isfinite(r) else float("nan"),
    }


def model_based_reliability(result: Mapping) -> dict:
    """sigma_u^2 / (sigma_u^2 + mean posterior variance of u_i)."""
    pt = result.get("player_table")
    if pt is None or pt.empty:
        return {"reliability": float("nan"), "n_players": 0}
    se2 = np.asarray(pt["u_se_s"], dtype=float) ** 2
    su2 = float(result.get("sigma_u2", float("nan")))
    denom = su2 + float(np.mean(se2)) if len(se2) else float("nan")
    return {
        "reliability": su2 / denom if denom and np.isfinite(denom) and denom > 0 else float("nan"),
        "sigma_u2": su2,
        "mean_u_se2": float(np.mean(se2)) if len(se2) else float("nan"),
        "n_players": int(len(se2)),
    }


def agreement_with_latency(df: pd.DataFrame, result: Mapping) -> dict:
    """Correlations with the direct reaction measures.

    * player-level ``u`` vs mean snap-to-onset latency;
    * player-level ``phys`` vs mean onset-to-crossing time.

    Disagreement is a finding, not a failure. Pearson only; n is reported.
    """
    pt = result.get("player_table")
    if pt is None or pt.empty:
        return {"status": "NO_PLAYER_TABLE"}
    d = df.groupby("player", sort=True).agg(
        onset_latency=("onset_latency_s", "mean"),
        onset_to_cross=("onset_to_cross_s", "mean"),
        phys=("phys_s", "mean"),
    ).reset_index()
    m = pt.merge(d, on="player", how="inner")
    a = m.dropna(subset=["u_s", "onset_latency"])
    b = m.dropna(subset=["phys", "onset_to_cross"])
    r_u = ST.pearson(a["u_s"].tolist(), a["onset_latency"].tolist()) if len(a) >= 3 else float("nan")
    r_p = ST.pearson(b["phys"].tolist(), b["onset_to_cross"].tolist()) if len(b) >= 3 else float("nan")
    return {
        "u_vs_onset_latency": {"r": r_u, "n_players": int(len(a))},
        "phys_vs_onset_to_cross": {"r": r_p, "n_players": int(len(b))},
        "note": "Disagreement is a finding, not a failure.",
    }


def year_to_year(df: pd.DataFrame, result: Mapping) -> dict:
    """Exploratory within-player year-to-year decomposition.

    For each player with at least two seasons, compare consecutive seasons:
    ``delta_y = b * delta_phys + delta_u``. Weight and role changes are
    confounds and are not adjusted.
    """
    if "season" not in df.columns:
        return {"status": "NO_SEASON_COLUMN"}
    b = float(result.get("b", float("nan")))
    d = df.groupby(["player", "season"], sort=True).agg(
        y=("y", "mean"), phys=("phys_s", "mean"), n=("y", "size")).reset_index()
    rows = []
    for pid, sub in d.groupby("player", sort=True):
        sub = sub.sort_values("season")
        for (_, a), (_, c) in zip(sub.iloc[:-1].iterrows(), sub.iloc[1:].iterrows()):
            dy = float(c["y"] - a["y"])
            dp = float(c["phys"] - a["phys"])
            rows.append({
                "player": pid,
                "season_from": int(a["season"]),
                "season_to": int(c["season"]),
                "delta_y_s": dy,
                "delta_phys_s": dp,
                "b_times_delta_phys_s": b * dp if np.isfinite(b) else float("nan"),
                "delta_u_s": dy - b * dp if np.isfinite(b) else float("nan"),
                "n_reps_from": int(a["n"]),
                "n_reps_to": int(c["n"]),
            })
    if not rows:
        return {"status": "INSUFFICIENT_SEASONS", "n_pairs": 0}
    r = pd.DataFrame(rows)
    return {
        "status": "OK",
        "n_players": int(r["player"].nunique()),
        "n_pairs": int(len(r)),
        "mean_delta_y_s": float(r["delta_y_s"].mean()),
        "mean_b_times_delta_phys_s": float(r["b_times_delta_phys_s"].mean()),
        "mean_delta_u_s": float(r["delta_u_s"].mean()),
        "corr_delta_y_delta_phys": ST.pearson(r["delta_y_s"].tolist(),
                                              r["delta_phys_s"].tolist()),
        "confounds": "weight and role changes are not adjusted; exploratory only",
    }


# --------------------------------------------------------------------------- #
# Fallback ladder
# --------------------------------------------------------------------------- #
def empirical_bayes(result: Mapping) -> dict:
    """Rung 2: per-player empirical-Bayes shrinkage of u_i."""
    pt = result.get("player_table")
    if pt is None or pt.empty or result.get("status") != "OK":
        return {"status": "UNAVAILABLE"}
    se2 = float(result.get("sigma_e2", float("nan")))
    su2 = float(result.get("sigma_u2_fit", float("nan")))
    if not np.isfinite(se2) or not np.isfinite(su2) or su2 <= 0:
        return {"status": "UNAVAILABLE"}
    n = pt["n_reps"].to_numpy(dtype=float)
    w = (n / se2) / (n / se2 + 1.0 / su2)
    u_eb = w * pt["u_s"].to_numpy(dtype=float)
    su2_eb = _variance(u_eb)
    var_phys = (float(result["b"]) ** 2) * float(result["between_player_phys_var_observed"])
    denom = su2_eb + var_phys
    return {
        "status": "OK",
        "sigma_u2": su2_eb,
        "between_phys_var": var_phys,
        "share_u": su2_eb / denom if denom > 0 else float("nan"),
        "share_phys": var_phys / denom if denom > 0 else float("nan"),
        "mean_shrinkage": float(np.mean(w)),
    }


def random_intercept_only(df: pd.DataFrame, *, max_iter: int = 100,
                          tol: float = 1e-6) -> dict:
    """Rung 3: random intercept only (no phys term)."""
    d = df.copy()
    cols = [np.ones(len(d))]
    if np.ptp(d["depth_yd"].to_numpy(dtype=float)) > 0:
        cols.append(d["depth_yd"].to_numpy(dtype=float))
    if d["alignment_class"].nunique(dropna=True) > 1:
        cols.append((d["alignment_class"].astype(str).str.upper() == "EDGE").astype(float).to_numpy())
    X = np.column_stack(cols)
    y = d["y"].to_numpy(dtype=float)
    pcode, _ = pd.factorize(d["player"], sort=True)
    gcode, _ = pd.factorize(d["team_game"], sort=True)
    try:
        fit = fit_lmm(y, X, pcode, gcode, max_iter=max_iter, tol=tol)
    except (ValueError, np.linalg.LinAlgError):
        return {"status": "UNAVAILABLE"}
    su2, se2 = float(fit["sigma_u2"]), float(fit["sigma_e2"])
    return {
        "status": "OK",
        "b": float("nan"),
        "sigma_u2": su2,
        "sigma_e2": se2,
        "var_phys": 0.0,
        "share_u": 1.0 if su2 > 0 else float("nan"),
        "share_phys": 0.0,
        "within_player_remainder_total": se2 / (su2 + se2) if su2 + se2 > 0 else float("nan"),
    }


# --------------------------------------------------------------------------- #
# Orchestration for one fitting set
# --------------------------------------------------------------------------- #
def _eligible(df: pd.DataFrame, min_reps: int) -> pd.DataFrame:
    counts = df.groupby("player").size()
    keep = counts[counts >= int(min_reps)].index
    return df[df["player"].isin(keep)].copy()


def fit_set(df: pd.DataFrame, *, label: str, cfg: Mapping, min_reps: int,
            bootstrap_resamples: int, bootstrap_seed: int,
            population_degree: int, max_iter: int, tol: float,
            min_players: int, accept_delta: float = 1e-4,
            min_bootstrap_ok_share: float = 0.5) -> dict:
    """Fit one set (pooled / edge / interior) with the full ladder.

    Rung 0 is the measurement-error-corrected fit; rung 1 is the same model with
    no correction; rung 2 is empirical Bayes; rung 3 is random-intercept-only.
    The rung actually used is rung 0 unless it is not estimable.
    """
    d = _eligible(df, min_reps)
    n_players = int(d["player"].nunique())
    if n_players < int(min_players):
        return {
            "label": label,
            "status": "INSUFFICIENT_PLAYERS",
            "n_reps": int(len(d)),
            "n_players": n_players,
            "min_reps_per_player": int(min_reps),
            "min_players": int(min_players),
        }

    corrected = decompose(d, correct_measurement_error=True,
                          population_degree=population_degree,
                          max_iter=max_iter, tol=tol)
    naive = decompose(d, correct_measurement_error=False,
                      population_degree=population_degree,
                      max_iter=max_iter, tol=tol)

    def _usable(res: Mapping) -> bool:
        return (res.get("status") == "OK"
                and (bool(res.get("converged"))
                     or float(res.get("delta", float("inf"))) <= float(accept_delta)))

    rung = 0
    used = corrected
    if not _usable(corrected):
        rung = 1
        used = naive
    if not _usable(naive):
        eb = empirical_bayes(naive)
        if eb.get("status") == "OK" and naive.get("player_table") is not None:
            rung = 2
            used = dict(naive)
            used.update({
                "status": "OK",
                "sigma_u2": eb["sigma_u2"],
                "between_phys_var": eb["between_phys_var"],
                "share_u": eb["share_u"],
                "share_phys": eb["share_phys"],
                "rung": 2,
            })
            denom = (used["sigma_u2"] + used["between_phys_var"]
                     + float(used.get("sigma_e2", 0.0)))
            used["within_player_remainder_total"] = (
                float(used.get("sigma_e2", 0.0)) / denom if denom > 0 else float("nan"))
            used["jump_sd_ms"] = 1000.0 * math.sqrt(max(0.0, used["sigma_u2"]))
        else:
            rung = 3
            used = random_intercept_only(d, max_iter=max_iter, tol=tol)

    boot = {"status": "SKIPPED", "n_requested": 0, "n_ok": 0}
    if rung == 3:
        boot = {
            "status": "NOT_APPLICABLE_RUNG3",
            "n_requested": 0, "n_ok": 0,
            "note": "The random-intercept-only rung has share_u = 1 and no b by "
                    "construction; a bootstrap of the full model would not "
                    "describe this rung.",
        }
    elif rung == 2:
        boot = {
            "status": "NOT_APPLICABLE_RUNG2",
            "n_requested": 0, "n_ok": 0,
            "note": "Empirical-Bayes rung: the point estimate is the shrinkage "
                    "estimate; a bootstrap of the full model would not describe "
                    "this rung.",
        }
    elif used.get("status") == "OK" and int(bootstrap_resamples) > 0:
        boot = bootstrap_decomposition(
            d, correct_measurement_error=(rung == 0),
            resamples=int(bootstrap_resamples), seed=int(bootstrap_seed),
            population_degree=population_degree,
            max_iter=max_iter, tol=tol,
            point=used,
            min_ok_share=min_bootstrap_ok_share,
        )

    out = {k: v for k, v in used.items()
           if k not in ("fit", "player_table", "resid", "resid_u", "data")}
    out["label"] = label
    out["rung_used"] = int(rung)
    out["min_reps_per_player"] = int(min_reps)
    out["bootstrap"] = boot
    out["boundary"] = {
        "sigma_u_zero": bool(float(used.get("sigma_u2", 1.0)) <= 0.0),
        "between_phys_var_zero": bool(float(used.get("between_phys_var", 1.0)) <= 0.0),
        "note": "A zero component is a boundary estimate, reported as such; "
                "it is not replaced by a small positive number.",
    }
    out["naive_rung1"] = {
        k: naive.get(k) for k in
        ("status", "b", "sigma_u2", "sigma_u2_fit", "between_phys_var",
         "share_u", "share_phys", "within_player_remainder_total",
         "converged", "iters", "delta")
        if k in naive
    }
    out["ladder"] = {
        "rung_0_regression_calibration": {
            "status": corrected.get("status"),
            "share_u": corrected.get("share_u"),
            "share_phys": corrected.get("share_phys"),
            "b": corrected.get("b"),
        },
        "rung_1_no_me_correction": {
            "status": naive.get("status"),
            "share_u": naive.get("share_u"),
            "share_phys": naive.get("share_phys"),
            "b": naive.get("b"),
        },
        "rung_2_empirical_bayes": empirical_bayes(naive),
        "rung_3_random_intercept_only": random_intercept_only(
            d, max_iter=max_iter, tol=tol) if rung == 3 else {"status": "NOT_NEEDED"},
    }

    # Reliability and agreement are reported for the fit actually used.
    if used.get("status") == "OK":
        out["reliability"] = {
            "split_half": split_half_reliability(used.get("data", d),
                                                 used.get("resid_u",
                                                          np.zeros(len(d)))),
            "model_based": model_based_reliability(used),
            "note": "Reliability of u; the split-half is across odd/even games.",
        }
        out["agreement"] = agreement_with_latency(used.get("data", d), used)
        out["year_to_year"] = year_to_year(used.get("data", d), used)

    # Keep the player table for the stage's per-player CSV.
    out["_player_table"] = used.get("player_table")
    out["_data"] = used.get("data")
    return out
