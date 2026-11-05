#!/usr/bin/env python3
"""combine_features.py — Combine-side kinematics shared by the Phase-2 envelope stage.

Kinematics are recomputed from `x,y` only (TASK.md / audit f: the provided `s`/`a`/`dis`
are unreliable, especially on cutting drills). Every numeric parameter comes from
`config.yaml` (`envelope.*`, `audit.*`) — no magic numbers.

Public API
----------
* ``attempt_straight_features(x, y, t, cfg)`` -> dict of straight-line features, incl. the
  exponential envelope ``v(t)=vmax*(1-exp(-t/tau))`` -> ``env_vmax``, ``env_tau``, ``env_a0=vmax/tau``.
* ``attempt_cut_features(x, y, t, cfg)`` -> dict of change-of-direction metrics
  (``entry_speed``, ``exit_speed``, ``cod_deficit``) for cutting drills.
* ``icc_1_1(groups, min_players)`` -> test-retest ICC(1,1) over repeated attempts.
* ``vif_values(X)`` / ``vif_table(features)`` -> variance-inflation factors.
* ``shrink_hierarchical(per_player, ...)`` -> empirical-Bayes (two-stage) hierarchical shrinkage.
"""
from __future__ import annotations

import numpy as np

# --------------------------------------------------------------------------- #
# kinematics from x,y
# --------------------------------------------------------------------------- #
def _speed_path(x: np.ndarray, y: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (speed yd/s per frame, cumulative path distance yd) recomputed from x,y."""
    dt = np.diff(t)
    dt[dt <= 0] = np.nan
    step = np.hypot(np.diff(x), np.diff(y))
    v = np.nan_to_num(np.concatenate([[0.0], step / dt]))
    dist = np.concatenate([[0.0], np.cumsum(step)])
    return v, dist


def _interp(arr_x: np.ndarray, arr_y: np.ndarray, q: float) -> float:
    """Linear interpolation of arr_y at arr_x==q; NaN when q is beyond the sample."""
    if len(arr_x) == 0 or not np.isfinite(q):
        return float("nan")
    if q < arr_x[0]:
        return float("nan")
    if arr_x[-1] < q:
        return float("nan")
    pos = max(int(np.searchsorted(arr_x, q)), 1)
    denom = arr_x[pos] - arr_x[pos - 1]
    f = 0.0 if denom <= 0 else (q - arr_x[pos - 1]) / denom
    return float(arr_y[pos - 1] + f * (arr_y[pos] - arr_y[pos - 1]))


def fit_envelope(t_rel: np.ndarray, v: np.ndarray, cfg: dict) -> tuple[float, float, float]:
    """Fit ``v(t) = vmax*(1 - exp(-t/tau))``; return (vmax, tau, a0=vmax/tau).

    Returns (nan, nan, nan) when the fit cannot be made (too few positive-speed frames
    or a solver failure) — never a silent zero.
    """
    from scipy.optimize import curve_fit

    fit = cfg["envelope"]["fit"]
    pos = v > float(fit["min_speed_yds"])
    if int(pos.sum()) < int(fit["min_points"]):
        return float("nan"), float("nan"), float("nan")
    tt, vv = t_rel[pos], v[pos]
    # curve_fit bounds are (lower_per_param, upper_per_param); config stores (lower, upper) per param.
    lo = [fit["vmax_bounds"][0], fit["tau_bounds"][0]]
    hi = [fit["vmax_bounds"][1], fit["tau_bounds"][1]]
    try:
        popt, _ = curve_fit(
            lambda t, vmax, tau: vmax * (1.0 - np.exp(-t / tau)),
            tt, vv,
            p0=[float(np.max(vv)), float(fit["p0_tau_s"])],
            bounds=(lo, hi),
            maxfev=int(fit["maxfev"]),
        )
        vmax, tau = float(popt[0]), float(popt[1])
        return vmax, tau, float(vmax / tau)
    except Exception:
        return float("nan"), float("nan"), float("nan")


# --------------------------------------------------------------------------- #
# per-attempt features
# --------------------------------------------------------------------------- #
def attempt_straight_features(x, y, t, cfg: dict) -> dict[str, float]:
    """Model-free split / speed-at-time features + the fitted envelope for one straight-line attempt."""
    env, fit = cfg["envelope"], cfg["envelope"]["fit"]
    x = np.asarray(x, float); y = np.asarray(y, float); t = np.asarray(t, float)
    v, dist = _speed_path(x, y, t)

    feats: dict[str, float] = {"peak_speed": float(np.max(v)) if len(v) else float("nan")}

    onset = float(fit["onset_speed_yds"])
    idx = np.where(v >= onset)[0]
    i0 = int(idx[0]) if len(idx) else 0
    t_rel, d_rel = t - t[i0], dist - dist[i0]

    for d in env["split_distances_yd"]:
        feats[f"time_to_{d}yd"] = _interp(d_rel, t_rel, float(d))
        feats[f"speed_at_{d}yd"] = _interp(d_rel, v, float(d))
    for ts in env["speed_at_times_s"]:
        feats[f"speed_at_t{float(ts)}s"] = _interp(t_rel, v, float(ts))

    if len(t) >= 3:
        dv = np.gradient(v, t)
        feats["max_decel"] = float(-np.nanmin(dv)) if np.isfinite(np.nanmin(dv)) else float("nan")
    else:
        feats["max_decel"] = float("nan")

    if len(v) >= 3:
        ipk = int(np.argmax(v))
        feats["min_speed_after_peak"] = float(np.min(v[ipk:]))
        feats["speed_deficit_after_peak"] = float(feats["peak_speed"] - feats["min_speed_after_peak"])
    else:
        feats["min_speed_after_peak"] = float("nan")
        feats["speed_deficit_after_peak"] = float("nan")

    vmax, tau, a0 = fit_envelope(t_rel, v, cfg)
    feats["env_vmax"], feats["env_tau"], feats["env_a0"] = vmax, tau, a0
    return feats


def attempt_cut_features(x, y, t, cfg: dict) -> dict[str, float]:
    """Change-of-direction metrics for one cutting-drill attempt.

    Definitions (documented, config-driven):
    * the change-of-direction (COD) frame is the frame of maximum heading change over a
      window of ``audit.decel_window_frames`` frames;
    * ``entry_speed`` = peak speed in the window *before* the COD frame;
    * ``exit_speed``  = minimum speed in the window *after* the COD frame (the turn apex);
    * ``cod_deficit`` = ``entry_speed - exit_speed`` (speed shed through the cut; yd/s).
    """
    w = int(cfg["audit"]["decel_window_frames"])
    x = np.asarray(x, float); y = np.asarray(y, float); t = np.asarray(t, float)
    v, _ = _speed_path(x, y, t)
    nan = float("nan")
    if len(x) <= 2 * w + 1:
        return {"entry_speed": nan, "exit_speed": nan, "cod_deficit": nan}

    heading = np.arctan2(np.diff(y), np.diff(x))
    dh = np.abs(np.angle(np.exp(1j * (heading[w:] - heading[:-w]))))
    j = int(np.argmax(dh)) + w  # frame index of the heading change (in speed coordinates)
    lo, hi = max(0, j - w), min(len(v), j + w + 1)
    entry = float(np.max(v[lo:j + 1])) if j >= lo else nan
    exit_ = float(np.min(v[j:hi])) if hi > j else nan
    deficit = float(entry - exit_) if np.isfinite(entry) and np.isfinite(exit_) else nan
    return {"entry_speed": entry, "exit_speed": exit_, "cod_deficit": deficit}


# --------------------------------------------------------------------------- #
# reliability + collinearity
# --------------------------------------------------------------------------- #
def icc_1_1(groups: dict, min_players: int) -> dict:
    """Test-retest ICC(1,1) over repeated attempts within players.

    ``groups`` maps player id -> list of per-attempt feature values. Only players with >=2
    attempts contribute (a test-retest needs within-player replication). Always returns an
    explicit status; thin repeats yield an ``UNESTIMABLE`` status, never a silent blank.
    """
    clean = {k: [v for v in vals if not (isinstance(v, float) and np.isnan(v))] for k, vals in groups.items()}
    all_groups = {k: vals for k, vals in clean.items() if len(vals) >= 1}
    rep = {k: vals for k, vals in all_groups.items() if len(vals) >= 2}
    n_with_repeat = len(rep)
    k = len(rep)
    N = sum(len(v) for v in rep.values())
    res = {"n_players_total": len(all_groups), "n_players_with_repeat": n_with_repeat,
           "n_attempts_used": N, "icc": None, "status": None}
    if n_with_repeat < min_players:
        res["status"] = f"UNESTIMABLE: only {n_with_repeat} players with >=2 attempts (<{min_players})"
        return res
    if k < 2 or N <= k:
        res["status"] = f"UNESTIMABLE: too few groups/observations (k={k}, N={N})"
        return res
    allv = np.concatenate([np.asarray(vals, float) for vals in rep.values()])
    grand = float(np.mean(allv))
    ssb = sum(len(vals) * (np.mean(vals) - grand) ** 2 for vals in rep.values())
    ssw = sum(float(np.sum((np.asarray(vals, float) - np.mean(vals)) ** 2)) for vals in rep.values())
    dfb, dfw = k - 1, N - k
    if dfw <= 0:
        res["status"] = f"UNESTIMABLE: no within-player df (dfw={dfw})"
        return res
    msb, msw = ssb / dfb, ssw / dfw
    k0 = (N - sum(len(v) ** 2 for v in rep.values()) / N) / (k - 1)
    if msw <= 1e-12:
        res["status"] = "UNESTIMABLE: zero within-player variance"
        return res
    res["icc"] = float((msb - msw) / (msb + (k0 - 1) * msw))
    res["msb"], res["msw"] = float(msb), float(msw)
    res["mean_attempts_k0"] = float(k0)
    res["status"] = "ESTIMATED"
    return res


def vif_values(X: np.ndarray) -> list[float]:
    """VIF_j = 1/(1-R^2_j) from regressing feature j on the others (with intercept)."""
    X = np.asarray(X, float)
    n, p = X.shape
    out: list[float] = []
    if n == 0 or not np.all(np.isfinite(X)):
        return [float("nan")] * p
    for j in range(p):
        y = X[:, j]
        Z = np.delete(X, j, axis=1)
        Z = np.column_stack([np.ones(n), Z])
        beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
        resid = y - Z @ beta
        ss_res = float(resid @ resid)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        out.append(float("inf") if r2 >= 1.0 - 1e-12 else 1.0 / (1.0 - r2))
    return out


def vif_table(feature_matrix: dict[str, np.ndarray], order: list[str]) -> dict[str, float]:
    """Convenience: VIF for the features in ``order`` present in ``feature_matrix``."""
    present = [f for f in order if f in feature_matrix]
    if len(present) < 2:
        return {f: float("nan") for f in present}
    X = np.column_stack([feature_matrix[f] for f in present])
    return dict(zip(present, vif_values(X)))


# --------------------------------------------------------------------------- #
# hierarchical shrinkage (two-stage empirical Bayes)
# --------------------------------------------------------------------------- #
def shrink_hierarchical(values_by_player: dict, min_attempts: int) -> dict:
    """Shrink per-player attempt means toward the grand mean (James-Stein / EB).

    Model ``y_ai = theta_a + e_ai``, ``e ~ N(0, sigma2)``, ``theta_a ~ N(mu, tau2)``.
    Variance components are estimated by one-way random-effects moments:
      ``sigma2 = MSE``; ``tau2 = max(0, (MSB - MSE)/k0)``, ``k0 = (N - sum n_a^2/N)/(a-1)``.
    The shrunk estimate is the precision-weighted mean
      ``theta_a_hat = (n_a/sigma2 * ybar_a + 1/tau2 * mu) / (n_a/sigma2 + 1/tau2)``
    with ``SE = 1/sqrt(n_a/sigma2 + 1/tau2)``. When ``tau2 <= 0`` the estimate falls back to
    the raw per-player mean (no shrinkage) so a degenerate sample is reported, not silently pooled.

    Returns per-player dict: ``{mean, se (shrunk), raw_mean, raw_se, n, shrink_weight}`` and an
    aggregate entry ``__components__`` with (mu, sigma2, tau2, k0, n_players).
    """
    players = {k: [float(v) for v in vals if np.isfinite(v)] for k, vals in values_by_player.items()}
    players = {k: v for k, v in players.items() if len(v) >= max(1, int(min_attempts))}
    a = len(players)
    if a == 0:
        return {"__components__": {"mu": None, "sigma2": None, "tau2": None, "k0": None, "n_players": 0}}
    N = sum(len(v) for v in players.values())
    grand = float(np.mean([np.mean(v) for v in players.values()])) if a else float("nan")
    mu = float(np.sum([np.sum(v) for v in players.values()]) / N)

    out: dict = {}
    if a < 2 or N <= a:
        for k, v in players.items():
            m = float(np.mean(v))
            out[k] = {"mean": m, "se": float(np.std(v, ddof=1) / np.sqrt(len(v))) if len(v) > 1 else float("nan"),
                      "raw_mean": m, "raw_se": float("nan"), "n": len(v), "shrink_weight": 1.0}
        out["__components__"] = {"mu": mu, "sigma2": None, "tau2": None, "k0": None, "n_players": a}
        return out

    ssb = sum(len(v) * (np.mean(v) - grand) ** 2 for v in players.values())
    ssw = sum(float(np.sum((np.asarray(v, float) - np.mean(v)) ** 2)) for v in players.values())
    dfb, dfw = a - 1, N - a
    msb = ssb / dfb if dfb > 0 else float("nan")
    mse = ssw / dfw if dfw > 0 else float("nan")
    k0 = (N - sum(len(v) ** 2 for v in players.values()) / N) / (a - 1)
    tau2 = max(0.0, (msb - mse) / k0) if np.isfinite(msb) and np.isfinite(mse) and k0 > 0 else 0.0
    sigma2 = mse if np.isfinite(mse) and mse > 0 else 0.0

    for k, v in players.items():
        n_a = len(v)
        ybar = float(np.mean(v))
        raw_se = float(np.std(v, ddof=1) / np.sqrt(n_a)) if n_a > 1 else float("nan")
        if tau2 > 0 and sigma2 > 0:
            w_obs = n_a / sigma2
            w_prior = 1.0 / tau2
            theta = (w_obs * ybar + w_prior * mu) / (w_obs + w_prior)
            se = 1.0 / np.sqrt(w_obs + w_prior)
            sw = float(w_obs / (w_obs + w_prior))
        else:  # degenerate between-player variance: report the raw mean, no fabricated shrinkage
            theta, se, sw = ybar, raw_se, 1.0
        out[k] = {"mean": float(theta), "se": float(se), "raw_mean": ybar, "raw_se": raw_se,
                  "n": n_a, "shrink_weight": sw}
    out["__components__"] = {"mu": mu, "sigma2": float(sigma2), "tau2": float(tau2),
                             "k0": float(k0), "n_players": a}
    return out


def delta_se_ratio(num: float, den: float, num_se: float, den_se: float) -> float:
    """First-order (delta-method) SE of a0 = vmax/tau, assuming num_se/den_se independent."""
    if not (np.isfinite(num) and np.isfinite(den) and np.isfinite(num_se) and np.isfinite(den_se)) or den == 0:
        return float("nan")
    var = (num_se / den) ** 2 + (num * den_se / den ** 2) ** 2
    return float(np.sqrt(var))
