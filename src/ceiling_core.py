"""ceiling_core — Combine ceiling estimation shared by 01_audit and 02.

The ceiling model is fixed by ``TASK.md`` Phase 2:

* fit ``v(t) = vmax * (1 - exp(-t / tau))`` per straight-line attempt,
* derive ``a0 = vmax / tau`` from the fitted curve (never differentiate raw
  positions),
* pool attempts per player with hierarchical (empirical-Bayes) shrinkage,
* carry a standard error with every estimate and never use a best attempt,
* select at most three features by test-retest ICC and VIF.

Nothing in this module reads game tracking.  It is a pure function of the
Combine files plus ``config.yaml`` (``bdb27_lc.ceiling`` / ``thresholds``), so
item (h) of the audit and script 02 run the *same* code path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

import lc_common as C

# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _read_attempt_frames(cfg: dict) -> Any:
    """Load PLAYER frames for the configured fit drills as a pandas frame."""
    files = C.source_paths(cfg, "combine_tracking")
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"Combine tracking missing: {missing}")
    block = C.lc(cfg)["ceiling"]
    drills = block["fit_drills"]
    drill_sql = ", ".join(f"'{d}'" for d in drills)
    con = C.duckdb_connect(cfg)
    try:
        path = files[0].as_posix()
        df = con.execute(f"""
            SELECT nfl_id, draft_year, drill_type, drill_name, attempt,
                   event_id, epoch_ms(time) AS epoch_ms, s, x, y
            FROM read_csv_auto('{path}', nullstr=['NA'])
            WHERE entity_type = 'PLAYER'
              AND drill_name IN ({drill_sql})
              AND s IS NOT NULL AND x IS NOT NULL AND y IS NOT NULL
            ORDER BY nfl_id, drill_name, attempt, event_id, epoch_ms
        """).df()
    finally:
        con.close()
    if df.empty:
        raise RuntimeError(f"no PLAYER rows for drills {drills}")
    return df


def _positions(cfg: dict) -> dict[int, dict]:
    files = C.source_paths(cfg, "players")
    con = C.duckdb_connect(cfg)
    try:
        rows = con.execute(f"""
            SELECT nfl_id, nfl_position, draft_year
            FROM read_csv_auto('{files[0].as_posix()}', nullstr=['NA'])
        """).fetchall()
    finally:
        con.close()
    return {int(r[0]): {"position": r[1], "draft_year": int(r[2])} for r in rows}


# --------------------------------------------------------------------------- #
# Per-attempt fitting
# --------------------------------------------------------------------------- #
@dataclass
class AttemptFit:
    nfl_id: int
    draft_year: int
    drill_name: str
    attempt: int
    event_id: str
    n_frames: int
    sinuosity: float
    peak_speed: float
    vmax: float
    tau: float
    a0: float
    r2: float
    se_log_vmax: float
    se_log_tau: float
    flags: list[str] = field(default_factory=list)

    @property
    def se_log_a0(self) -> float:
        # a0 = vmax / tau; treat the two estimate errors as independent.  This
        # is conservative enough for a feature screen and is documented in
        # notes/decisions.md (D60).
        return math.sqrt(self.se_log_vmax ** 2 + self.se_log_tau ** 2)


def _smooth_and_clean(s: np.ndarray, cfg: dict) -> tuple[np.ndarray, list[str]]:
    """Flag raw sub-frame spikes, replace them, then median-smooth."""
    import pandas as pd

    block = C.lc(cfg)["thresholds"]
    cap = float(block["speed_cap_yds"])
    window = int(block["smoothing"]["window_frames"])
    flags: list[str] = []
    raw = pd.Series(s, dtype="float64")
    if bool((raw > cap).any()):
        flags.append("speed_above_cap")
        raw = raw.mask(raw > cap)
        raw = raw.interpolate(limit_direction="both")
    smoothed = raw.rolling(window=window, center=True, min_periods=1).median()
    return smoothed.to_numpy(dtype=float), flags


def _fit_one(t: np.ndarray, v: np.ndarray, cfg: dict) -> tuple[float, float, float, float, float, float]:
    from scipy.optimize import least_squares

    block = C.lc(cfg)["ceiling"]
    lo = block["bounds"]["vmax_yds"]
    hi = block["bounds"]["tau_s"]
    vmax0 = float(np.clip(block["initial"]["vmax_frac_of_peak"] * v.max(),
                          lo[0] + 1e-6, lo[1] - 1e-6))
    tau0 = float(np.clip(block["initial"]["tau_s"], hi[0] + 1e-6, hi[1] - 1e-6))

    def residual(p):
        vmax, tau = p
        return vmax * (1.0 - np.exp(-t / tau)) - v

    fit = least_squares(residual, x0=[vmax0, tau0],
                        bounds=([lo[0], hi[0]], [lo[1], hi[1]]),
                        method="trf", max_nfev=500)
    vmax, tau = (float(fit.x[0]), float(fit.x[1]))
    rss = float(np.sum(fit.fun ** 2))
    tss = float(np.sum((v - v.mean()) ** 2))
    r2 = 1.0 - rss / tss if tss > 0 else 0.0

    dof = max(1, len(v) - 2)
    try:
        jtj = fit.jac.T @ fit.jac
        cov = np.linalg.pinv(jtj) * (rss / dof)
        se_vmax = float(np.sqrt(max(cov[0, 0], 0.0)))
        se_tau = float(np.sqrt(max(cov[1, 1], 0.0)))
    except Exception:  # pragma: no cover - numerical fallback
        se_vmax = se_tau = float("nan")
    se_log_vmax = se_vmax / vmax if vmax > 0 and math.isfinite(se_vmax) else float("nan")
    se_log_tau = se_tau / tau if tau > 0 and math.isfinite(se_tau) else float("nan")
    return vmax, tau, vmax / tau, r2, se_log_vmax, se_log_tau


def _interpolate_small_gaps(t: np.ndarray, v: np.ndarray, peak_t: float,
                            peak_pad: float, max_gap: float) -> tuple[np.ndarray, np.ndarray]:
    """Linearly fill 0.1-0.5 s gaps, but never inside the peak-speed window.

    ``TASK.md`` permits interpolation under 0.5 s only outside the peak window.
    Longer gaps (or any gap touching the peak) are left alone and reported by
    the caller; they are never silently bridged.
    """
    step = 0.1
    new_t = [float(t[0])]
    new_v = [float(v[0])]
    for i in range(1, len(t)):
        dt = float(t[i] - t[i - 1])
        left = float(t[i - 1])
        outside_peak = not (peak_t - peak_pad <= left <= peak_t + peak_pad)
        if step < dt < max_gap and outside_peak:
            n = max(1, int(round(dt / step)))
            for k in range(1, n):
                frac = k / n
                new_t.append(left + frac * dt)
                new_v.append(float(v[i - 1] + frac * (v[i] - v[i - 1])))
        new_t.append(float(t[i]))
        new_v.append(float(v[i]))
    return np.asarray(new_t), np.asarray(new_v)


def fit_attempts(cfg: dict) -> tuple[list[AttemptFit], dict]:
    """Fit every straight-line attempt; return fits plus a sanity summary."""
    import pandas as pd

    block = C.lc(cfg)["ceiling"]
    th = C.lc(cfg)["thresholds"]
    min_frames = int(block["min_frames"])
    min_r2 = float(block["min_r2"])
    max_sinuosity = float(C.lc(cfg)["audit"]["straight_line_sinuosity_max"])
    max_speed_over = float(block["max_speed_over_vmax"])
    gap_s = float(th["gap"]["interp_max_s"])
    peak_frac = float(th["peak_window"]["frac_of_max"])
    peak_pad = float(th["peak_window"]["pad_s"])

    frames = _read_attempt_frames(cfg)
    # Attempt instance = (nfl_id, drill, attempt, event_id).  The event id
    # separates a duplicated attempt label from the trial it duplicates.
    dup_groups = (frames.groupby(["nfl_id", "drill_name", "attempt"])["event_id"]
                  .nunique())
    duplicates = set(dup_groups[dup_groups > 1].index)

    fits: list[AttemptFit] = []
    summary = {
        "n_frame_rows": int(len(frames)),
        "n_attempt_series": 0,
        "n_fit": 0,
        "n_too_short": 0,
        "n_low_r2": 0,
        "n_nonstraight": 0,
        "n_speed_above_cap": 0,
        "n_duplicate_attempt": 0,
        "n_peak_window_gap": 0,
        "n_gap_interpolated": 0,
        "n_speed_over_vmax": 0,
    }

    for (nfl_id, drill, attempt, event_id), grp in frames.groupby(
            ["nfl_id", "drill_name", "attempt", "event_id"], sort=True):
        grp = grp.sort_values("epoch_ms")
        summary["n_attempt_series"] += 1
        if len(grp) < min_frames:
            summary["n_too_short"] += 1
            continue
        t = (grp["epoch_ms"].to_numpy(dtype=float) - grp["epoch_ms"].min()) / 1000.0
        if t.max() <= 0:
            summary["n_too_short"] += 1
            continue
        x = grp["x"].to_numpy(dtype=float)
        y = grp["y"].to_numpy(dtype=float)
        step = np.hypot(np.diff(x), np.diff(y))
        path = float(np.nansum(step))
        disp = float(math.hypot(x[-1] - x[0], y[-1] - y[0]))
        sinuosity = path / disp if disp > 0 else float("inf")
        if sinuosity > max_sinuosity:
            summary["n_nonstraight"] += 1
            continue

        raw_s = grp["s"].to_numpy(dtype=float)
        v, flags = _smooth_and_clean(raw_s, cfg)
        if "speed_above_cap" in flags:
            summary["n_speed_above_cap"] += 1

        dt = np.diff(t)
        # Use the raw peak to place the window: median smoothing can move the
        # post-clean argmax by a frame, which would hide a planted gap.
        peak_t = float(t[int(np.argmax(raw_s))])
        peak_pad = float(th["peak_window"]["pad_s"])
        gap_s = float(th["gap"]["interp_max_s"])
        in_window = (t[:-1] >= peak_t - peak_pad) & (t[:-1] <= peak_t + peak_pad)
        if np.any(in_window & (dt > gap_s)):
            flags.append("peak_window_gap")
            summary["n_peak_window_gap"] += 1
        t_fit, v_fit = _interpolate_small_gaps(t, v, peak_t, peak_pad, gap_s)
        summary["n_gap_interpolated"] += int(len(t_fit) - len(t))
        if (int(nfl_id), str(drill), int(attempt)) in duplicates:
            flags.append("duplicate_attempt")
            summary["n_duplicate_attempt"] += 1

        vmax, tau, a0, r2, se_lv, se_lt = _fit_one(t_fit, v_fit, cfg)
        if r2 < min_r2:
            summary["n_low_r2"] += 1
            continue
        if v.max() > max_speed_over * vmax:
            summary["n_speed_over_vmax"] += 1
            flags.append("speed_over_vmax")

        fits.append(AttemptFit(
            nfl_id=int(nfl_id), draft_year=int(grp["draft_year"].iloc[0]),
            drill_name=str(drill), attempt=int(attempt), event_id=str(event_id),
            n_frames=int(len(grp)), sinuosity=sinuosity, peak_speed=float(v.max()),
            vmax=vmax, tau=tau, a0=a0, r2=r2,
            se_log_vmax=se_lv, se_log_tau=se_lt, flags=flags))
        summary["n_fit"] += 1

    return fits, summary


# --------------------------------------------------------------------------- #
# Reliability / shrinkage
# --------------------------------------------------------------------------- #
def icc_oneway(groups: Iterable[np.ndarray]) -> float:
    """One-way random-effects ICC(1) for unbalanced groups.

    ``ICC = (MSB - MSW) / (MSB + (n0 - 1) * MSW)`` with the standard
    ``n0 = (N - sum(n_i^2)/N) / (k - 1)`` adjustment.  Clipped to [0, 1].
    """
    arrays = [np.asarray(g, dtype=float) for g in groups if len(g) >= 1]
    k = len(arrays)
    if k < 2:
        return 0.0
    n = np.array([len(a) for a in arrays], dtype=float)
    N = float(n.sum())
    if N <= k:
        return 0.0
    means = np.array([a.mean() for a in arrays])
    grand = float(np.sum(means * n) / N)
    ssb = float(np.sum(n * (means - grand) ** 2))
    ssw = float(sum(float(np.sum((a - m) ** 2)) for a, m in zip(arrays, means)))
    msb = ssb / (k - 1)
    msw = ssw / max(1.0, N - k)
    if msb + (n.mean() - 1) * msw == 0:
        return 0.0
    n0 = (N - float(np.sum(n ** 2)) / N) / (k - 1)
    value = (msb - msw) / (msb + (n0 - 1) * msw)
    return float(min(1.0, max(0.0, value)))


def _feature_value(fit: AttemptFit, feature: str) -> float:
    if feature == "vmax":
        return math.log(fit.vmax)
    if feature == "tau":
        return math.log(fit.tau)
    if feature == "a0":
        return math.log(fit.a0)
    raise KeyError(feature)


def _feature_se(fit: AttemptFit, feature: str) -> float:
    if feature == "vmax":
        return fit.se_log_vmax
    if feature == "tau":
        return fit.se_log_tau
    if feature == "a0":
        return fit.se_log_a0
    raise KeyError(feature)


def _expit_feature(feature: str, value: float) -> float:
    return math.exp(value)


def pool_feature(fits: list[AttemptFit], feature: str, cfg: dict) -> tuple[dict, float]:
    """Empirical-Bayes shrink one feature; return per-player rows and ICC."""
    block = C.lc(cfg)["ceiling"]
    by_player: dict[int, list[AttemptFit]] = {}
    for fit in fits:
        by_player.setdefault(fit.nfl_id, []).append(fit)

    raw: list[dict] = []
    for pid, rows in sorted(by_player.items()):
        ys = np.array([_feature_value(r, feature) for r in rows], dtype=float)
        ses = np.array([_feature_se(r, feature) for r in rows], dtype=float)
        good = np.isfinite(ses) & (ses > 0)
        if good.any():
            w = 1.0 / ses[good] ** 2
            mean = float(np.sum(w * ys[good]) / np.sum(w))
            se_mean = float(math.sqrt(1.0 / np.sum(w)))
        else:
            mean = float(np.mean(ys))
            se_mean = float(np.std(ys, ddof=1) / math.sqrt(len(ys))) if len(ys) > 1 else float("nan")
        raw.append({"nfl_id": pid, "n_attempts": len(rows), "mean": mean,
                    "se_mean": se_mean, "ys": ys,
                    "draft_year": rows[0].draft_year})

    icc = icc_oneway([r["ys"] for r in raw])
    finite_se = np.array([r["se_mean"] for r in raw if np.isfinite(r["se_mean"])],
                         dtype=float)
    within_var = float(np.nanmean(finite_se ** 2)) if len(finite_se) else 0.0
    between_var = float(np.var([r["mean"] for r in raw], ddof=1)) if len(raw) > 1 else 0.0
    tau2 = max(0.0, between_var - within_var)

    # Grand mean: inverse-variance weighted, iterated once after tau2.
    def grand_mean(t2: float) -> float:
        num = den = 0.0
        for r in raw:
            w = 1.0 / (t2 + (r["se_mean"] ** 2 if np.isfinite(r["se_mean"]) else within_var))
            num += w * r["mean"]
            den += w
        return num / den if den else 0.0

    grand = grand_mean(tau2)
    rows_out: list[dict] = []
    for r in raw:
        se2 = r["se_mean"] ** 2 if np.isfinite(r["se_mean"]) else within_var
        if tau2 > 0:
            weight = tau2 / (tau2 + se2)
            post_se = math.sqrt((tau2 * se2) / (tau2 + se2))
        else:
            weight, post_se = 0.0, math.sqrt(se2) if se2 > 0 else float("nan")
        shrunk_log = grand + weight * (r["mean"] - grand)
        estimate = _expit_feature(feature, shrunk_log)
        se_natural = estimate * post_se if math.isfinite(post_se) else float("nan")
        rows_out.append({
            "nfl_id": r["nfl_id"], "draft_year": r["draft_year"],
            "feature": feature, "estimate": estimate, "se": se_natural,
            "n_attempts": r["n_attempts"], "icc": icc,
            "reliability": weight,
        })
    return {"rows": rows_out, "icc": icc, "tau2": tau2,
            "within_var": within_var, "grand_log": grand}, icc


def _vif(values: np.ndarray) -> np.ndarray:
    """VIF for columns of a design matrix (intercept added internally)."""
    X = np.column_stack([np.ones(len(values)), values])
    out = []
    for j in range(1, X.shape[1]):
        y = X[:, j]
        Z = np.delete(X, j, axis=1)
        beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
        resid = y - Z @ beta
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        out.append(float("inf") if r2 >= 1.0 else 1.0 / (1.0 - r2))
    return np.array(out, dtype=float)


def compute_ceiling_features(cfg: dict) -> dict:
    """Full ceiling pipeline: fits -> shrinkage -> feature screen.

    Returns ``{"rows": [...], "icc": {...}, "selected": [...], "summary": {...}}``.
    """
    block = C.lc(cfg)["ceiling"]
    th = C.lc(cfg)["thresholds"]
    positions = set(C.lc(cfg)["audit"]["positions"])
    fits, fit_summary = fit_attempts(cfg)
    if not fits:
        raise RuntimeError("no usable straight-line attempts after sanity filters")

    pos = _positions(cfg)
    fits = [f for f in fits if pos.get(f.nfl_id, {}).get("position") in positions]
    if not fits:
        raise RuntimeError("no WR/TE attempts after position filter")

    iccs: dict[str, float] = {}
    feature_rows: dict[str, list[dict]] = {}
    for feature in block["candidate_features"]:
        pooled, icc = pool_feature(fits, feature, cfg)
        iccs[feature] = icc
        feature_rows[feature] = pooled["rows"]

    icc_min = float(block["icc_min"])
    max_features = int(block["max_features"])
    vif_max = float(block["vif_max"])
    selected: list[str] = [f for f in block["candidate_features"]
                           if iccs.get(f, 0.0) >= icc_min][:max_features]

    # VIF screen on the player-level (log) estimates of the survivors.
    vif_values: dict[str, float] = {}
    if len(selected) > 1:
        by_player: dict[int, dict[str, float]] = {}
        for feature in selected:
            for row in feature_rows[feature]:
                by_player.setdefault(row["nfl_id"], {})[feature] = math.log(row["estimate"])
        common = [pid for pid, vals in by_player.items()
                  if all(f in vals for f in selected)]
        if len(common) > len(selected) + 1:
            matrix = np.array([[by_player[pid][f] for f in selected] for pid in common])
            vifs = _vif(matrix)
            vif_values = dict(zip(selected, [float(v) for v in vifs]))
            keep: list[str] = []
            for feature, value in vif_values.items():
                if value <= vif_max or not keep:
                    keep.append(feature)
            selected = keep
            vif_values = {f: vif_values[f] for f in selected}
    if len(selected) == 1:
        vif_values = {selected[0]: 1.0}

    selected_set = set(selected)
    rows: list[dict] = []
    for feature in block["candidate_features"]:
        for row in feature_rows[feature]:
            pid = row["nfl_id"]
            rows.append({
                **row,
                "position": pos[pid]["position"],
                "selected": feature in selected_set,
                "vif": vif_values.get(feature, ""),
            })
    rows.sort(key=lambda r: (r["nfl_id"], r["feature"]))

    summary = {
        "fit": fit_summary,
        "n_players_with_fits": len({f.nfl_id for f in fits}),
        "n_attempts_used": len(fits),
        "icc": iccs,
        "vif": vif_values,
        "selected_features": selected,
        "icc_min": icc_min,
        "vif_max": vif_max,
        "min_r2": float(block["min_r2"]),
        "reliable": bool(selected),
    }
    return {"rows": rows, "icc": iccs, "selected": selected,
            "summary": summary, "fits": fits}
