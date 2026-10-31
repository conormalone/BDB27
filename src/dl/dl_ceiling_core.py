"""dl_ceiling_core — DL Combine ceiling estimation (TASK.md Phase 2).

The ceiling model is fixed by ``TASK.md`` Phase 2 and by ``notes/dl-design.md``
§5.2:

* smooth the sampled speed curve, replace impossible speeds by interpolation,
  and fit ``v(t) = vmax * (1 - exp(-t / tau))`` per straight-line attempt on the
  *smoothed speed*, never on differentiated raw positions;
* derive ``a0 = vmax / tau`` from the fitted curve, with a delta-method SE that
  treats ``log vmax`` and ``log tau`` as independent (conservative; documented
  in ``notes/decisions.md``);
* pool attempts per player with empirical-Bayes shrinkage on the log scale
  (positivity), reporting the natural-scale estimate and SE; never a best
  attempt;
* carry a standard error with every estimate;
* screen candidate features by test-retest ICC(1) and VIF, selecting at most
  ``thresholds.max_features``;
* describe shuttle / three-cone mechanics (entry speed, peak, exit speed, total
  time, braking distance) as exploratory features.

Everything here is a pure function of in-memory arrays/frames plus the
``bdb27_dl`` config block.  The only I/O is :func:`read_attempt_frames`,
:func:`read_players` and :func:`read_ten_yd_splits`, which query the Combine
CSVs through ``dl_common`` (DuckDB, explicit column lists).  Game tracking is
never read: this module is the ceiling only.

Units: speeds yd/s, accelerations yd/s², times s, distances yd.  ``s`` is the
provider's speed column; ``a`` the provider's acceleration column.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

# --------------------------------------------------------------------------- #
# Numerical constants (solver settings and tolerances, not design parameters)
# --------------------------------------------------------------------------- #
#: Bound on the bounded-least-squares evaluations per attempt.  A numeric
#: safety valve, mirroring ``lc_common``'s ceiling fit; it never binds on a
#: well-formed Combine series (converges in a few dozen evaluations).
_MAX_NFEV = 500
#: Bisection iterations for inverting ``distance(t)`` to a split time.
_BISECT_ITERS = 200
#: Relative tolerance for calling a fitted parameter "at its bound".
_BOUND_REL_TOL = 1e-3
#: Fixed-point iterations for the inverse-variance grand mean.  Converges to
#: machine precision in a handful of passes.
_GRAND_ITERS = 20
#: Attempts per player needed before a *within-player* correlation is defined.
#: With two attempts per player the correlation is identically ±1, so the
#: statistic is an artefact of the attempt count, not evidence (D69(4)).
_MIN_ATTEMPTS_WITHIN_CORR = 3

#: ``players.nfl_position`` -> position group.  This is the data dictionary's
#: vocabulary (DE / DT / NT), not a tunable: the config's ``positions.dl`` list
#: is checked against it so an unexpected position fails loudly.
_POSITION_GROUP = {"DE": "edge", "DT": "interior", "NT": "interior"}

#: Short feature prefixes for the lateral drills in ``ceiling.lateral_drills``.
_LATERAL_PREFIX = {"SHORT_SHUTTLE": "shuttle", "THREE_CONE_DRILL": "cone"}

#: Candidate features whose test-retest ICC cannot be estimated from the
#: Combine tracking: ``ten_yd_split`` is a single reported value per player, so
#: copying it across attempts gives zero within-player variance and a spurious
#: ICC of 1.0.  Reported as NaN ("not estimable") instead.
_ICC_NOT_ESTIMABLE = {
    "ten_yd_split": ("single reported value per player; no repeated "
                     "measurement in the Combine tracking files"),
}

#: The primary estimand of Part 1 (``TASK.md``).  It is admitted to the model
#: unconditionally when it clears ``thresholds.icc_min``; the screen may rank
#: alternates, but it may not remove the estimand (D69(2)).
PRIMARY_FEATURE = "a0"

#: Ordered menu of ceiling operationalisations the ICC/VIF screen may rank.
#: ``a0 = vmax / tau`` by definition, so these are *alternatives* used one per
#: model in the Part 1 specification curve, never joint regressors (D69(3)).
SELECTION_POOL = ["a0", "vmax", "tau", "speed_at_1_0", "speed_at_2_0",
                  "peak_accel", "ten_yd_split"]

_REQUIRED_FRAME_COLUMNS = ("nfl_id", "drill_name", "attempt", "event_id",
                           "ms", "s", "x", "y")


# --------------------------------------------------------------------------- #
# Config accessors
# --------------------------------------------------------------------------- #
def block(cfg: Mapping) -> dict:
    """Return the ``bdb27_dl`` block, failing loudly when it is absent."""
    try:
        return cfg["bdb27_dl"]
    except KeyError as exc:  # pragma: no cover - dl_common.load_config gates this
        raise KeyError("config has no 'bdb27_dl' block") from exc


def thresholds(cfg: Mapping) -> dict:
    return block(cfg)["thresholds"]


def ceiling(cfg: Mapping) -> dict:
    return block(cfg)["ceiling"]


# --------------------------------------------------------------------------- #
# Speed handling
# --------------------------------------------------------------------------- #
def _rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    """Centred rolling median with truncated windows at both ends."""
    values = np.asarray(values, dtype=float)
    n = values.size
    if window <= 1 or n == 0:
        return values.copy()
    half = window // 2
    out = np.empty(n, dtype=float)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = float(np.median(values[lo:hi]))
    return out


def _interpolate_nonfinite(values: np.ndarray) -> tuple[np.ndarray, int]:
    """Linear-in-index fill of NaN/inf; returns the array and the fill count."""
    out = np.asarray(values, dtype=float).copy()
    bad = ~np.isfinite(out)
    n_bad = int(bad.sum())
    if n_bad == 0:
        return out, 0
    good = ~bad
    if not good.any():  # pragma: no cover - a series with no usable speed
        return out, n_bad
    idx = np.arange(out.size)
    out[bad] = np.interp(idx[bad], idx[good], out[good])
    return out, n_bad


def smooth_speed(s: Sequence[float], cfg: Mapping) -> tuple[np.ndarray, list[str], int]:
    """Clean and smooth a sampled speed series.

    Order matters and follows ``TASK.md`` Phase 2: flag speeds above
    ``thresholds.speed_cap_yds``, *replace* them by interpolation (never clip
    or differentiate), then apply a centred rolling median whose width comes
    from ``thresholds.smoothing.window_frames``.

    Returns ``(smoothed, flags, n_above_cap)``.
    """
    th = thresholds(cfg)
    cap = float(th["speed_cap_yds"])
    window = int(th["smoothing"]["window_frames"])
    flags: list[str] = []

    raw = np.asarray(s, dtype=float).copy()
    above = np.isfinite(raw) & (raw > cap)
    n_above = int(above.sum())
    if n_above:
        flags.append("speed_above_cap")
        raw[above] = np.nan
    raw, _ = _interpolate_nonfinite(raw)
    return _rolling_median(raw, window), flags, n_above


def _interpolate_gaps(t: np.ndarray, v: np.ndarray, peak_t: float,
                      peak_pad: float, max_gap: float, step: float,
                      tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Linearly fill sub-``max_gap`` gaps, but never inside the peak window.

    ``TASK.md`` permits interpolation under 0.5 s only outside the peak-speed
    window.  Longer gaps, and any gap touching the peak, are left alone: they
    are reported by the caller (``peak_window_gap``), never silently bridged.
    """
    new_t = [float(t[0])]
    new_v = [float(v[0])]
    for i in range(1, len(t)):
        dt = float(t[i] - t[i - 1])
        left = float(t[i - 1])
        outside_peak = not (peak_t - peak_pad <= left <= peak_t + peak_pad)
        if (step + tol) < dt <= max_gap and outside_peak:
            n = max(1, int(round(dt / step)))
            for k in range(1, n):
                frac = k / n
                new_t.append(left + frac * dt)
                new_v.append(float(v[i - 1] + frac * (v[i] - v[i - 1])))
        new_t.append(float(t[i]))
        new_v.append(float(v[i]))
    return np.asarray(new_t), np.asarray(new_v)


# --------------------------------------------------------------------------- #
# Curve
# --------------------------------------------------------------------------- #
def curve_speed(vmax: float, tau: float, t: float | np.ndarray):
    """``v(t) = vmax * (1 - exp(-t / tau))``."""
    return vmax * (1.0 - np.exp(-np.asarray(t, dtype=float) / tau))


def curve_distance(vmax: float, tau: float, t: float) -> float:
    """Distance covered by the fitted curve from rest: ``vmax*(t - tau*(1-e^-t/tau))``."""
    return float(vmax * (t - tau * (1.0 - math.exp(-t / tau))))


def curve_split_time(vmax: float, tau: float, distance: float) -> float | None:
    """Time at which the fitted curve has covered ``distance`` (monotone in t)."""
    if not (vmax > 0 and tau > 0 and distance > 0):
        return None
    upper = max(tau, 0.1)
    for _ in range(64):
        if curve_distance(vmax, tau, upper) >= distance:
            break
        upper *= 2.0
    else:  # pragma: no cover - curve_distance is unbounded above
        return None
    lo, hi = 0.0, upper
    for _ in range(_BISECT_ITERS):
        mid = 0.5 * (lo + hi)
        if curve_distance(vmax, tau, mid) < distance:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _se_log_speed(vmax: float, tau: float, t: float,
                  se_log_vmax: float, se_log_tau: float) -> float:
    """Delta-method SE of ``log v(t)`` with independent ``log vmax``/``log tau``.

    ``log v = log vmax + log(1 - exp(-t/tau))``; the second term's derivative
    with respect to ``log tau`` is ``g = e^{-t/tau} / (tau * (1 - e^{-t/tau}))``.
    """
    if not (math.isfinite(se_log_vmax) and math.isfinite(se_log_tau)):
        return float("nan")
    x = t / tau
    if x <= 0:
        return float("nan")
    denom = 1.0 - math.exp(-x)
    if denom <= 0:
        return float("nan")
    g = math.exp(-x) / (tau * denom)
    return float(math.sqrt(se_log_vmax ** 2 + (g * se_log_tau) ** 2))


@dataclass
class CurveFit:
    """Bounded least-squares fit of one attempt's speed curve."""

    vmax: float
    tau: float
    r2: float
    se_log_vmax: float
    se_log_tau: float
    n_frames: int
    flags: list[str] = field(default_factory=list)

    @property
    def a0(self) -> float:
        """Initial acceleration implied by the curve: ``vmax / tau``."""
        return self.vmax / self.tau

    @property
    def se_log_a0(self) -> float:
        """Delta-method SE of ``log a0``.

        Conservative independence assumption on ``log vmax`` and ``log tau``
        (they are negatively correlated in the fit, so ignoring the covariance
        over-states the SE rather than under-stating it).
        """
        if not (math.isfinite(self.se_log_vmax) and math.isfinite(self.se_log_tau)):
            return float("nan")
        return float(math.sqrt(self.se_log_vmax ** 2 + self.se_log_tau ** 2))


def fit_curve(t: Sequence[float], v: Sequence[float], cfg: Mapping) -> CurveFit:
    """Fit ``v(t) = vmax*(1-exp(-t/tau))`` by bounded least squares.

    Bounds and the initial guess come from ``ceiling.bounds`` / ``ceiling.initial``.
    Standard errors come from the Jacobian at the solution,
    ``cov = pinv(J'J) * rss / (n - 2)``, and are reported on the log scale
    (delta method), which is the scale the pooling works on.
    """
    from scipy.optimize import least_squares

    tb = np.asarray(t, dtype=float)
    vb = np.asarray(v, dtype=float)
    if tb.size != vb.size or tb.size < 3:
        raise ValueError("fit_curve needs at least three (t, v) pairs")
    if not (np.all(np.isfinite(tb)) and np.all(np.isfinite(vb))):
        raise ValueError("fit_curve received non-finite input")

    blk = ceiling(cfg)
    vmax_lo, vmax_hi = (float(x) for x in blk["bounds"]["vmax_yds"])
    tau_lo, tau_hi = (float(x) for x in blk["bounds"]["tau_s"])
    span_v = vmax_hi - vmax_lo
    span_t = tau_hi - tau_lo
    vmax0 = float(np.clip(blk["initial"]["vmax_frac_of_peak"] * vb.max(),
                          vmax_lo + 1e-6 * span_v, vmax_hi - 1e-6 * span_v))
    tau0 = float(np.clip(blk["initial"]["tau_s"],
                         tau_lo + 1e-6 * span_t, tau_hi - 1e-6 * span_t))

    def residual(p):
        return p[0] * (1.0 - np.exp(-tb / p[1])) - vb

    sol = least_squares(residual, x0=[vmax0, tau0],
                        bounds=([vmax_lo, tau_lo], [vmax_hi, tau_hi]),
                        method="trf", max_nfev=_MAX_NFEV)
    vmax, tau = float(sol.x[0]), float(sol.x[1])

    rss = float(np.sum(sol.fun ** 2))
    tss = float(np.sum((vb - vb.mean()) ** 2))
    r2 = 1.0 - rss / tss if tss > 0 else 0.0

    dof = max(1, vb.size - 2)
    try:
        cov = np.linalg.pinv(sol.jac.T @ sol.jac) * (rss / dof)
        se_vmax = float(math.sqrt(max(cov[0, 0], 0.0)))
        se_tau = float(math.sqrt(max(cov[1, 1], 0.0)))
    except Exception:  # pragma: no cover - numerical fallback
        se_vmax = se_tau = float("nan")
    se_log_vmax = se_vmax / vmax if vmax > 0 and math.isfinite(se_vmax) else float("nan")
    se_log_tau = se_tau / tau if tau > 0 and math.isfinite(se_tau) else float("nan")

    flags: list[str] = []
    if abs(vmax - vmax_lo) <= _BOUND_REL_TOL * max(1.0, abs(vmax_lo)) or \
            abs(vmax - vmax_hi) <= _BOUND_REL_TOL * max(1.0, abs(vmax_hi)):
        flags.append("vmax_at_bound")
    if abs(tau - tau_lo) <= _BOUND_REL_TOL * max(1.0, abs(tau_lo)) or \
            abs(tau - tau_hi) <= _BOUND_REL_TOL * max(1.0, abs(tau_hi)):
        flags.append("tau_at_bound")

    return CurveFit(vmax=vmax, tau=tau, r2=float(r2),
                    se_log_vmax=float(se_log_vmax),
                    se_log_tau=float(se_log_tau),
                    n_frames=int(vb.size), flags=flags)


# --------------------------------------------------------------------------- #
# Per-attempt records
# --------------------------------------------------------------------------- #
@dataclass
class AttemptFit:
    """One fitted straight-line attempt plus its derived features."""

    nfl_id: int
    draft_year: int
    drill_name: str
    attempt: int
    event_id: str
    n_frames: int
    duration_s: float
    peak_speed: float
    vmax: float
    tau: float
    a0: float
    r2: float
    se_log_vmax: float
    se_log_tau: float
    se_log_speed_at_1_0: float
    se_log_speed_at_2_0: float
    ten_yd_split: float | None
    se_ten_yd_split: float
    peak_accel: float | None
    se_peak_accel: float
    flags: list[str] = field(default_factory=list)

    @property
    def se_log_a0(self) -> float:
        if not (math.isfinite(self.se_log_vmax) and math.isfinite(self.se_log_tau)):
            return float("nan")
        return float(math.sqrt(self.se_log_vmax ** 2 + self.se_log_tau ** 2))

    def speed_at(self, t: float) -> float:
        return float(curve_speed(self.vmax, self.tau, t))

    def feature_value(self, feature: str) -> float | None:
        """Natural-scale value of ``feature`` for this attempt (None = absent)."""
        if feature == "a0":
            return self.a0
        if feature == "vmax":
            return self.vmax
        if feature == "tau":
            return self.tau
        if feature == "speed_at_1_0":
            return self.speed_at(1.0)
        if feature == "speed_at_2_0":
            return self.speed_at(2.0)
        if feature == "ten_yd_split":
            return self.ten_yd_split
        if feature == "peak_accel":
            return self.peak_accel
        raise KeyError(f"unknown ceiling feature {feature!r}")

    def feature_se(self, feature: str) -> float:
        """Natural-scale standard error of :meth:`feature_value` (NaN = unknown)."""
        value = self.feature_value(feature)
        if value is None or not np.isfinite(value) or value <= 0:
            return float("nan")
        if feature == "a0":
            return float(value * self.se_log_a0) if math.isfinite(self.se_log_a0) else float("nan")
        if feature == "vmax":
            return float(value * self.se_log_vmax)
        if feature == "tau":
            return float(value * self.se_log_tau)
        if feature == "speed_at_1_0":
            return float(value * self.se_log_speed_at_1_0)
        if feature == "speed_at_2_0":
            return float(value * self.se_log_speed_at_2_0)
        if feature == "ten_yd_split":
            return float(self.se_ten_yd_split)
        if feature == "peak_accel":
            return float(self.se_peak_accel)
        raise KeyError(f"unknown ceiling feature {feature!r}")

    def as_row(self) -> dict:
        """Flat mapping for ``attempt_fits.csv``."""
        return {
            "nfl_id": self.nfl_id,
            "drill_name": self.drill_name,
            "attempt": self.attempt,
            "event_id": self.event_id,
            "kind": "fit",
            "n_frames": self.n_frames,
            "duration_s": self.duration_s,
            "peak_speed": self.peak_speed,
            "vmax": self.vmax,
            "tau": self.tau,
            "a0": self.a0,
            "r2": self.r2,
            "se_log_vmax": self.se_log_vmax,
            "se_log_tau": self.se_log_tau,
            "se_log_a0": self.se_log_a0,
            "speed_at_1_0": self.speed_at(1.0),
            "speed_at_2_0": self.speed_at(2.0),
            "entry_speed": "",
            "exit_speed": "",
            "total_time_s": "",
            "braking_distance_yds": "",
            "ten_yd_split": "" if self.ten_yd_split is None else self.ten_yd_split,
            "peak_accel": "" if self.peak_accel is None else self.peak_accel,
            "flags": "|".join(self.flags),
        }


def _peak_accel(a: np.ndarray, v: np.ndarray, t: np.ndarray,
                cfg: Mapping) -> tuple[float | None, float]:
    """Max of the provided ``a`` column plus a sampling SE around the peak.

    ``TASK.md`` defines ``peak_accel`` as the maximum of the provider's
    acceleration column; that value duplicates ``a0`` mechanically and is
    exploratory.  Its SE is the standard error of the mean acceleration inside
    the configured peak-speed window (``thresholds.peak_window``): a
    frame-count-driven measure of how well the peak region is sampled, falling
    back to the whole series when the window holds a single frame.
    """
    th = thresholds(cfg)
    frac = float(th["peak_window"]["frac_of_max"])
    pad = float(th["peak_window"]["pad_s"])
    finite = np.isfinite(a)
    if not finite.any():
        return None, float("nan")
    value = float(np.max(a[finite]))
    i_peak = int(np.argmax(v))
    t_peak = float(t[i_peak])
    window = finite & (v >= frac * v[i_peak]) & (np.abs(t - t_peak) <= pad)
    vals = a[window]
    if vals.size < 2:
        vals = a[finite]
    if vals.size >= 2:
        se = float(np.std(vals, ddof=1) / math.sqrt(vals.size))
    else:  # pragma: no cover - a one-frame attempt is rejected earlier
        se = float("nan")
    return value, se


def fit_frames(frames: Any, cfg: Mapping,
               splits: Mapping[int, float] | None = None
               ) -> tuple[list[AttemptFit], dict]:
    """Fit every straight-line attempt in ``frames``.

    ``frames`` needs ``nfl_id, drill_name, attempt, event_id, ms, s, x, y``
    (plus optional ``draft_year`` and ``a``).  Elapsed time is measured from
    the first frame of the same ``(nfl_id, drill_name, attempt, event_id)``
    series: identical to the task's per-``(nfl_id, drill_name, attempt)``
    definition whenever the attempt label is unique, and the only definition
    that stays correct for a re-run that duplicates a label (which is flagged
    ``duplicate_attempt``).

    Rejections are counted, never silently dropped: ``min_frames`` and
    ``min_r2`` come from ``thresholds``.
    """
    th = thresholds(cfg)
    min_frames = int(th["min_frames"])
    min_r2 = float(th["min_r2"])
    step = 1.0 / float(th["combine_sampling_hz"])
    tol = float(th["combine_dt_tolerance_s"])
    gap_max = float(th["gap"]["interp_max_s"])
    pad = float(th["peak_window"]["pad_s"])

    summary = {
        "n_frame_rows": 0 if frames is None else int(len(frames)),
        "n_attempt_series": 0,
        "n_fit": 0,
        "n_too_short": 0,
        "n_low_r2": 0,
        "n_speed_above_cap": 0,
        "n_peak_window_gap": 0,
        "n_gap_interpolated": 0,
        "n_duplicate_attempt": 0,
        "n_bound_hit": 0,
        "n_missing_split": 0,
        "n_missing_peak_accel": 0,
        "n_players": 0,
    }
    if frames is None or len(frames) == 0:
        return [], summary

    missing = [c for c in _REQUIRED_FRAME_COLUMNS if c not in frames.columns]
    if missing:
        raise KeyError(f"attempt frames missing columns {missing}")
    splits = dict(splits or {})

    dup = frames.groupby(["nfl_id", "drill_name", "attempt"])["event_id"].nunique()
    dup_keys = {tuple(k) for k, v in dup.items() if int(v) > 1}

    has_a = "a" in frames.columns
    has_year = "draft_year" in frames.columns

    fits: list[AttemptFit] = []
    keys = ["nfl_id", "drill_name", "attempt", "event_id"]
    for key, grp in frames.groupby(keys, sort=True):
        nfl_id, drill_name, attempt, event_id = key
        summary["n_attempt_series"] += 1
        grp = grp.sort_values("ms", kind="mergesort")
        if len(grp) < min_frames:
            summary["n_too_short"] += 1
            continue
        ms = grp["ms"].to_numpy(dtype=float)
        t = (ms - ms.min()) / 1000.0
        if not np.isfinite(t).all() or t.max() <= 0:
            summary["n_too_short"] += 1
            continue

        raw_s = grp["s"].to_numpy(dtype=float)
        v, flags, n_above = smooth_speed(raw_s, cfg)
        if n_above:
            summary["n_speed_above_cap"] += 1

        # Place the peak window on the cleaned, smoothed curve: the raw argmax
        # can land on a speed-cap spike, which would misplace the window that
        # protects the true peak from interpolation.
        peak_t = float(t[int(np.argmax(v))])
        dt = np.diff(t)
        in_peak = (t[:-1] >= peak_t - pad) & (t[:-1] <= peak_t + pad)
        if np.any(in_peak & (dt > step + tol)):
            flags.append("peak_window_gap")
            summary["n_peak_window_gap"] += 1
        t_fit, v_fit = _interpolate_gaps(t, v, peak_t, pad, gap_max, step, tol)
        summary["n_gap_interpolated"] += int(t_fit.size - t.size)

        if (int(nfl_id), str(drill_name), int(attempt)) in dup_keys:
            flags.append("duplicate_attempt")
            summary["n_duplicate_attempt"] += 1

        cf = fit_curve(t_fit, v_fit, cfg)
        if cf.r2 < min_r2:
            summary["n_low_r2"] += 1
            continue
        if cf.flags:
            summary["n_bound_hit"] += 1
        flags.extend(cf.flags)

        a_col = (grp["a"].to_numpy(dtype=float) if has_a
                 else np.full(t.size, np.nan))
        peak_accel, se_peak_accel = _peak_accel(a_col, v, t, cfg)
        if peak_accel is None:
            summary["n_missing_peak_accel"] += 1

        split = splits.get(int(nfl_id))
        if split is None or not np.isfinite(split) or split <= 0:
            split = None
            summary["n_missing_split"] += 1

        fits.append(AttemptFit(
            nfl_id=int(nfl_id),
            draft_year=int(grp["draft_year"].iloc[0]) if has_year else 0,
            drill_name=str(drill_name),
            attempt=int(attempt),
            event_id=str(event_id),
            n_frames=int(t.size),
            duration_s=float(t.max() - t.min()),
            peak_speed=float(np.max(v)),
            vmax=cf.vmax,
            tau=cf.tau,
            a0=cf.a0,
            r2=cf.r2,
            se_log_vmax=cf.se_log_vmax,
            se_log_tau=cf.se_log_tau,
            se_log_speed_at_1_0=_se_log_speed(cf.vmax, cf.tau, 1.0,
                                              cf.se_log_vmax, cf.se_log_tau),
            se_log_speed_at_2_0=_se_log_speed(cf.vmax, cf.tau, 2.0,
                                              cf.se_log_vmax, cf.se_log_tau),
            ten_yd_split=split,
            se_ten_yd_split=float("nan"),
            peak_accel=peak_accel,
            se_peak_accel=se_peak_accel,
            flags=flags,
        ))

    summary["n_fit"] = len(fits)
    summary["n_players"] = len({f.nfl_id for f in fits})
    return fits, summary


def split_noise_se(fits: Iterable[AttemptFit],
                   splits: Mapping[int, float]) -> float:
    """Cohort-level instrument noise for the reported 10-yard split (seconds).

    ``ten_yd_split`` is measured once per player by the Combine's own timing
    system and is not repeated in the tracking files, so its error cannot be
    estimated from repeated trials here.  We compare the reported split with
    the split implied by the fitted curve (integrating the curve to 10 yd),
    remove the mean offset — the two clocks start on different triggers, which
    is a systematic ~0.35 s, not noise — and take the residual SD as an
    *upper bound* on the instrument noise, since it also absorbs curve error.
    Fewer than three comparable players returns NaN (pooling then falls back to
    a between-attempt SE and finally to the cohort's within-player variance).
    """
    per_player: dict[int, list[float]] = {}
    for fit in fits:
        reported = splits.get(fit.nfl_id)
        if reported is None or not np.isfinite(reported) or reported <= 0:
            continue
        t10 = curve_split_time(fit.vmax, fit.tau, 10.0)
        if t10 is None:  # pragma: no cover - guarded by positive bounds
            continue
        per_player.setdefault(fit.nfl_id, []).append(reported - t10)
    diffs = np.array([float(np.mean(v)) for v in per_player.values()], dtype=float)
    if diffs.size < 3:
        return float("nan")
    return float(np.std(diffs, ddof=1))


# --------------------------------------------------------------------------- #
# Reliability and pooling
# --------------------------------------------------------------------------- #
def icc_oneway(groups: Iterable[Sequence[float]]) -> float:
    """One-way random-effects ICC(1) for unbalanced groups.

    ``ICC = (MSB - MSW) / (MSB + (n0 - 1) * MSW)`` with the standard
    ``n0 = (N - sum(n_i^2)/N) / (k - 1)`` adjustment, clipped to [0, 1].
    Requires at least two groups; otherwise 0.0.
    """
    arrays = [np.asarray(g, dtype=float) for g in groups if len(g) >= 1]
    k = len(arrays)
    if k < 2:
        return 0.0
    n = np.array([a.size for a in arrays], dtype=float)
    total = float(n.sum())
    if total <= k:
        return 0.0
    means = np.array([float(a.mean()) for a in arrays])
    grand = float(np.sum(means * n) / total)
    ssb = float(np.sum(n * (means - grand) ** 2))
    ssw = float(sum(float(np.sum((a - m) ** 2)) for a, m in zip(arrays, means)))
    msb = ssb / (k - 1)
    msw = ssw / max(1.0, total - k)
    n0 = (total - float(np.sum(n ** 2)) / total) / (k - 1)
    denom = msb + (n0 - 1.0) * msw
    if denom == 0:  # pragma: no cover - zero variance everywhere
        return 0.0
    return float(min(1.0, max(0.0, (msb - msw) / denom)))


def pool_feature(pairs_by_player: Mapping[int, Sequence[tuple[float, float]]],
                 feature: str, cfg: Mapping, *,
                 icc_estimable: bool = True) -> dict:
    """Empirical-Bayes pool attempts per player for one feature.

    ``pairs_by_player`` maps ``nfl_id -> [(value, se), ...]`` on the natural
    scale.  Work happens on ``log(value)`` so the shrunken estimate stays
    positive; the reported estimate and SE are converted back with the delta
    method (``se_natural = estimate * se_log``).

    Model: ``y_ij = theta_i + e_ij``, ``e_ij ~ N(0, se_ij^2)``,
    ``theta_i ~ N(mu, tau2)``.  ``tau2`` is method-of-moments
    (``var(means) - mean(se^2)``, floored at 0); ``mu`` is the
    inverse-variance grand mean; the posterior is
    ``theta_hat = mu + B (ybar - mu)`` with ``B = tau2 / (tau2 + se_ybar^2)``.
    The posterior variance adds the uncertainty in ``mu`` so an SE is always
    positive and never claims perfect knowledge.

    The ICC is computed on the *natural-scale* attempt values (the scale the
    feature is reported on); only the pooling runs on logs, for positivity.

    ``icc_estimable=False`` reports the ICC as NaN (see ``_ICC_NOT_ESTIMABLE``).
    """
    th = thresholds(cfg)
    min_att = int(th["min_attempts_for_icc"])

    entries: list[dict] = []
    for pid in sorted(pairs_by_player):
        pairs = [(float(v), float(s)) for v, s in pairs_by_player[pid]
                 if v is not None and np.isfinite(v) and v > 0]
        if not pairs:
            continue
        ys = np.array([math.log(v) for v, _ in pairs], dtype=float)
        ses = np.array([(s / v) if (np.isfinite(s) and s > 0) else np.nan
                        for v, s in pairs], dtype=float)
        good = np.isfinite(ses) & (ses > 0)
        if good.any():
            w = 1.0 / ses[good] ** 2
            mean = float(np.sum(w * ys[good]) / np.sum(w))
            se_mean = float(math.sqrt(1.0 / np.sum(w)))
        elif ys.size > 1:
            mean = float(np.mean(ys))
            sd = float(np.std(ys, ddof=1))
            se_mean = sd / math.sqrt(ys.size) if sd > 0 else float("nan")
        else:
            mean = float(ys[0])
            se_mean = float("nan")
        if not np.isfinite(se_mean) or se_mean <= 0:
            se_mean = float("nan")  # unknown, resolved against the cohort below
        entries.append({
            "nfl_id": pid,
            "n_attempts": int(ys.size),
            "ys": ys,
            "ys_nat": np.array([v for v, _ in pairs], dtype=float),
            "mean": mean,
            "se_mean": se_mean,
        })
    if not entries:
        return {"rows": [], "icc": float("nan"), "tau2": float("nan"),
                "grand": float("nan"), "n_players": 0, "n_attempts": 0,
                "n_players_icc": 0, "n_attempts_icc": 0,
                "n_players_excluded_icc": 0}

    icc_groups = [e["ys_nat"] for e in entries if e["n_attempts"] >= min_att]
    excluded = sum(1 for e in entries if e["n_attempts"] < min_att)
    icc = icc_oneway(icc_groups) if icc_estimable else float("nan")
    # Delete-one-player jackknife SE of the ICC: a cheap, honest standard error
    # until the player-clustered bootstrap the prereg requires (D69(5)) exists.
    # NaN when the ICC is not estimable or when fewer than 3 players contribute.
    icc_se = float("nan")
    if np.isfinite(icc) and len(icc_groups) >= 3:
        jack = np.array([icc_oneway(icc_groups[:i] + icc_groups[i + 1:])
                         for i in range(len(icc_groups))], dtype=float)
        icc_se = float(math.sqrt((jack.size - 1.0) / jack.size
                                 * float(np.sum((jack - float(icc)) ** 2))))

    finite_se = np.array([e["se_mean"] for e in entries
                          if np.isfinite(e["se_mean"])], dtype=float)
    within_var = float(np.mean(finite_se ** 2)) if finite_se.size else 0.0
    between_var = (float(np.var([e["mean"] for e in entries], ddof=1))
                   if len(entries) > 1 else 0.0)
    tau2 = max(0.0, between_var - within_var)

    fallback = math.sqrt(within_var) if within_var > 0 else (
        math.sqrt(between_var) if between_var > 0 else float("nan"))
    for e in entries:
        if not np.isfinite(e["se_mean"]) and np.isfinite(fallback):
            e["se_mean"] = fallback

    weighted = [e for e in entries if np.isfinite(e["se_mean"])]
    grand = float(np.mean([e["mean"] for e in weighted])) if weighted else float("nan")
    var_grand = float("nan")
    for _ in range(_GRAND_ITERS):
        w = np.array([1.0 / (tau2 + e["se_mean"] ** 2) for e in weighted])
        grand = float(np.sum(w * np.array([e["mean"] for e in weighted]))
                      / np.sum(w))
        var_grand = float(1.0 / np.sum(w))

    rows: list[dict] = []
    for e in entries:
        se2 = e["se_mean"] ** 2 if np.isfinite(e["se_mean"]) else float("nan")
        if not np.isfinite(se2) or not np.isfinite(grand):
            rows.append({"nfl_id": e["nfl_id"], "feature": feature,
                         "estimate": float("nan"), "se": float("nan"),
                         "n_attempts": e["n_attempts"], "icc": icc,
                         "reliability": 0.0})
            continue
        reliability = tau2 / (tau2 + se2) if (tau2 + se2) > 0 else 0.0
        post_log = grand + reliability * (e["mean"] - grand)
        post_var = (reliability * se2) + var_grand
        estimate = float(math.exp(post_log))
        rows.append({
            "nfl_id": e["nfl_id"],
            "feature": feature,
            "estimate": estimate,
            "se": float(estimate * math.sqrt(post_var)) if post_var > 0 else float("nan"),
            "n_attempts": e["n_attempts"],
            "icc": icc,
            "reliability": float(reliability),
        })

    return {
        "rows": rows,
        "icc": float(icc),
        "icc_se": icc_se,
        "tau2": float(tau2),
        "grand": float(grand),
        "n_players": len(entries),
        "n_attempts": int(sum(e["n_attempts"] for e in entries)),
        "n_players_icc": len(icc_groups),
        "n_attempts_icc": int(sum(e["n_attempts"] for e in entries
                                  if e["n_attempts"] >= min_att)),
        "n_players_excluded_icc": int(excluded),
    }


# --------------------------------------------------------------------------- #
# VIF screen and greedy selection
# --------------------------------------------------------------------------- #
def vif_matrix(columns: Mapping[str, Mapping[int, float]]) -> dict[str, float]:
    """VIF of each column against the others, on the players present in all.

    ``columns`` maps a feature to ``{nfl_id: log estimate}``.  With fewer
    observations than columns the VIF is not estimable and is reported as inf.
    """
    names = list(columns)
    if not names:
        return {}
    common = set(columns[names[0]])
    for name in names[1:]:
        common &= set(columns[name])
    pids = sorted(common)
    if len(pids) <= len(names):
        return {name: float("inf") for name in names}
    X = np.column_stack([np.ones(len(pids))]
                        + [[columns[name][pid] for pid in pids] for name in names])
    out: dict[str, float] = {}
    for j, name in enumerate(names, start=1):
        y = X[:, j]
        Z = np.delete(X, j, axis=1)
        beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
        resid = y - Z @ beta
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        # "Constant" has to be judged at machine precision: summing identical
        # floats can leave a spread of a few ulp, which would otherwise divide
        # into a meaningless R^2.
        floor = (1e-12 * max(abs(float(y.mean())), 1.0)) ** 2 * y.size
        if ss_tot <= floor:
            # Zero between-player variance: the column is collinear with the
            # intercept and carries no information.  Report it as unusable
            # rather than pretending it is orthogonal.
            out[name] = float("inf")
            continue
        r2 = 1.0 - ss_res / ss_tot
        out[name] = (float("inf") if r2 >= 1.0
                     else float(max(1.0, 1.0 / (1.0 - r2))))
    return out


def select_features(pooled: Mapping[str, Mapping[str, Any]],
                    order: Sequence[str], cfg: Mapping) -> dict:
    """ICC/VIF screen that *ranks alternates* without removing the estimand.

    ``order`` is the ordered menu of candidate features (``SELECTION_POOL``).
    The primary (:data:`PRIMARY_FEATURE`, ``a0``) is admitted first,
    unconditionally, whenever its ICC clears ``thresholds.icc_min`` — it is
    never displaced by a higher-ICC secondary and never dropped for
    ``max_features`` (D69(2)).  Every secondary is then offered in descending
    ICC order and measured for VIF *against the set it joins* (primary
    included); a feature collinear with ``a0`` is dropped with reason
    ``vif_at_admission``.

    If the primary's ICC is below the floor it is reported as
    ``dropped["a0"] = "icc_below_floor"`` and is not selected; secondaries may
    still be admitted and reported, but nothing is renamed to primary and no
    fallback is adopted here — the gate carries the failure.

    ``max_features`` caps the total selected set *including* the primary.  The
    reported ``vif`` block never exceeds ``vif_max``: joint inflation is pruned
    by dropping co-regressors, never the primary.
    """
    th = thresholds(cfg)
    icc_min = float(th["icc_min"])
    vif_max = float(th["vif_max"])
    max_features = int(th["max_features"])
    primary = PRIMARY_FEATURE

    index = {name: i for i, name in enumerate(order)}
    eligible: list[str] = []
    ineligible: list[str] = []
    dropped: dict[str, str] = {}
    admission: dict[str, float] = {}
    for name in order:
        if name not in pooled:
            ineligible.append(name)
            dropped[name] = "not_estimated"
            continue
        icc = pooled[name].get("icc", float("nan"))
        if np.isfinite(icc) and icc >= icc_min:
            eligible.append(name)
        else:
            ineligible.append(name)
            dropped[name] = "icc_not_estimable" if not np.isfinite(icc) else "icc_below_floor"

    def log_estimates(feature: str) -> dict[int, float]:
        return {row["nfl_id"]: math.log(row["estimate"])
                for row in pooled[feature]["rows"]
                if np.isfinite(row["estimate"]) and row["estimate"] > 0}

    selected: list[str] = []
    if primary in eligible and max_features >= 1:
        selected.append(primary)
        admission[primary] = 1.0
    elif primary in eligible:
        dropped[primary] = "max_features"

    secondaries = sorted(
        (n for n in eligible if n != primary),
        key=lambda n: (-float(pooled[n]["icc"]), index.get(n, 0)))
    for name in secondaries:
        if len(selected) >= max_features:
            dropped[name] = "max_features"
            continue
        if not selected:
            # No primary in the set: the first admitted secondary joins an
            # empty set, where VIF is 1.0 by definition.
            selected.append(name)
            admission[name] = 1.0
            continue
        trial = selected + [name]
        value = vif_matrix({f: log_estimates(f) for f in trial}).get(
            name, float("inf"))
        admission[name] = value
        if value <= vif_max:
            selected.append(name)
        else:
            dropped[name] = "vif_at_admission"

    # Greedy admission measures each candidate against the set it joins; adding
    # a later feature can push an earlier one over the ceiling.  Prune until the
    # *reported* set satisfies vif_max.  The primary is never dropped: when it
    # is the offender, remove the co-regressor whose removal most reduces its
    # inflation; otherwise drop the worst offender (lower ICC on a tie).  Never
    # back-fill: the ranking is by ICC.
    while len(selected) > 1:
        joint = vif_matrix({f: log_estimates(f) for f in selected})
        offenders = {f: v for f, v in joint.items() if v > vif_max}
        if not offenders:
            break
        removable = [f for f in selected if f != primary]
        if primary in offenders:
            def primary_vif_without(drop: str) -> float:
                trial = [f for f in selected if f != drop]
                return vif_matrix({f: log_estimates(f) for f in trial}).get(
                    primary, float("inf"))
            worst = min(removable, key=lambda f: (primary_vif_without(f),
                                                  -float(pooled[f]["icc"])))
        else:
            worst = max(removable, key=lambda f: (offenders.get(f, 0.0),
                                                  -float(pooled[f]["icc"])))
        selected.remove(worst)
        dropped[worst] = "vif_joint"

    vifs: dict[str, float] = {}
    if len(selected) == 1:
        vifs = {selected[0]: 1.0}
    elif selected:
        cols = {f: log_estimates(f) for f in selected}
        vifs = vif_matrix(cols)
    return {"selected": selected, "vif": vifs, "eligible": eligible,
            "ineligible": ineligible, "admission_vif": admission,
            "dropped": dropped, "primary": primary,
            "primary_selected": primary in selected}


def within_player_corr(fits: Iterable[AttemptFit],
                       first: str = "vmax", second: str = "a0") -> dict:
    """Correlation of two features *within* players, on the log scale.

    A within-player correlation needs at least :data:`_MIN_ATTEMPTS_WITHIN_CORR`
    (3) attempts: with two attempts it is identically ±1 for every player, so
    the statistic reports the attempt count rather than the data (D69(4)).
    When no player has 3+ attempts the function says so —
    ``estimable=False`` with a reason and a NaN estimate — instead of returning
    the degenerate value.
    """
    by_player: dict[int, list[AttemptFit]] = {}
    for fit in fits:
        by_player.setdefault(fit.nfl_id, []).append(fit)
    usable = {pid: rows for pid, rows in by_player.items()
              if len(rows) >= _MIN_ATTEMPTS_WITHIN_CORR}
    out = {
        "first": first,
        "second": second,
        "scale": "log-log",
        "min_attempts": _MIN_ATTEMPTS_WITHIN_CORR,
        "n_players_total": len(by_player),
    }
    if not usable:
        return {**out,
                "n_players": 0,
                "mean_within_r": float("nan"),
                "median_within_r": float("nan"),
                "pooled_within_r": float("nan"),
                "estimable": False,
                "reason": (f"requires >={_MIN_ATTEMPTS_WITHIN_CORR} attempts "
                           "per player; none in this cohort")}

    per_player: list[float] = []
    xs: list[float] = []
    ys: list[float] = []
    for pid in sorted(usable):
        rows = usable[pid]
        pairs = [(r.feature_value(first), r.feature_value(second)) for r in rows]
        if any(v is None or not np.isfinite(v) or v <= 0 for pair in pairs
               for v in pair):
            continue
        x = np.log(np.array([pair[0] for pair in pairs], dtype=float))
        y = np.log(np.array([pair[1] for pair in pairs], dtype=float))
        if x.std() <= 0 or y.std() <= 0:
            continue
        per_player.append(float(np.corrcoef(x, y)[0, 1]))
        xs.extend(x - x.mean())
        ys.extend(y - y.mean())
    pooled = float("nan")
    if len(xs) > 2 and np.std(xs) > 0 and np.std(ys) > 0:
        pooled = float(np.corrcoef(np.asarray(xs), np.asarray(ys))[0, 1])
    return {**out,
            "n_players": len(per_player),
            "mean_within_r": float(np.mean(per_player)) if per_player else float("nan"),
            "median_within_r": float(np.median(per_player)) if per_player else float("nan"),
            "pooled_within_r": pooled,
            "estimable": bool(per_player),
            "reason": "" if per_player else "no player had usable variance"}


def between_player_corr(pooled_or_fits: Any, first: str, second: str,
                        cfg: Mapping | None = None) -> dict:
    """Pearson correlation of two features *between* players (natural scale).

    Computed on the shrunken per-player estimates — the quantity that enters
    the model — joined on ``nfl_id`` and reported with ``n_players``.  This is
    the diagnostic D69(4) substitutes for the degenerate within-player
    correlation, which is not estimable at 2 attempts per player.

    ``pooled_or_fits`` is either the pooled mapping from
    :func:`compute_ceiling_features` (``feature -> pool_feature(...)``) or an
    iterable of :class:`AttemptFit`, in which case ``cfg`` is required and the
    fits are pooled here first.
    """
    out = {
        "first": first,
        "second": second,
        "method": "between_player_shrunken",
        "scale": "natural",
    }
    if isinstance(pooled_or_fits, Mapping):
        blocks = pooled_or_fits
    else:
        if cfg is None:
            raise ValueError("between_player_corr needs cfg to pool raw fits")
        fits = list(pooled_or_fits)
        blocks = {f: pool_feature(_pairs_from_fits(fits, f), f, cfg)
                  for f in (first, second)}

    xs: dict[int, float] = {}
    ys: dict[int, float] = {}
    missing = [f for f in (first, second) if f not in blocks]
    if missing:
        return {**out, "n_players": 0, "r": float("nan"), "estimable": False,
                "reason": f"feature(s) not pooled: {missing}"}
    for row in blocks[first]["rows"]:
        value = row.get("estimate")
        if value is not None and np.isfinite(value):
            xs[int(row["nfl_id"])] = float(value)
    for row in blocks[second]["rows"]:
        value = row.get("estimate")
        if value is not None and np.isfinite(value):
            ys[int(row["nfl_id"])] = float(value)
    common = sorted(set(xs) & set(ys))
    if len(common) < 3:
        return {**out, "n_players": len(common), "r": float("nan"),
                "estimable": False, "reason": "fewer than 3 common players"}
    a = np.array([xs[pid] for pid in common], dtype=float)
    b = np.array([ys[pid] for pid in common], dtype=float)
    if a.std() <= 0 or b.std() <= 0:
        return {**out, "n_players": len(common), "r": float("nan"),
                "estimable": False, "reason": "zero variance in a feature"}
    return {**out, "n_players": len(common),
            "r": float(np.corrcoef(a, b)[0, 1]), "estimable": True,
            "reason": ""}


# --------------------------------------------------------------------------- #
# Lateral mechanics (exploratory)
# --------------------------------------------------------------------------- #
def _window_mean_se(values: np.ndarray) -> float:
    """Standard error of the mean of a window of frames (NaN if < 2 frames)."""
    if values.size < 2:
        return float("nan")
    return float(np.std(values, ddof=1) / math.sqrt(values.size))


def lateral_attempt(series: Any, cfg: Mapping) -> dict | None:
    """Entry / peak / exit speed, total time and braking distance for one attempt.

    Braking distance is the path length between the first speed maximum and the
    first subsequent local minimum whose drop from that peak reaches
    ``ceiling.lateral.braking_min_drop_yds``.  Local minima smaller than the
    threshold are skipped rather than ending the search: median smoothing
    leaves plateaus at the peak whose "drop" is zero, and on a noisy curve the
    literally-first local minimum is almost always a one-frame wobble.  No
    qualifying minimum means None (flagged, never zero-filled).  Every metric
    carries a frame-level sampling SE; these features are exploratory.

    "First speed maximum" is the first *local* maximum of the smoothed curve
    (the end of the opening acceleration), not the global maximum: in a
    5-10-5 shuttle the global maximum is usually the long middle leg, whose
    braking happens at the end of the recording or not at all, whereas the
    first local maximum is the first plant — the deceleration the drill is
    designed to measure.  ``peak_speed`` remains the global maximum.
    """
    th = thresholds(cfg)
    lat = ceiling(cfg)["lateral"]
    entry_window = float(lat["entry_window_s"])
    exit_window = float(lat["exit_window_s"])
    min_drop = float(lat["braking_min_drop_yds"])
    step = 1.0 / float(th["combine_sampling_hz"])
    min_frames = int(th["min_frames"])

    series = series.sort_values("ms", kind="mergesort")
    if len(series) < min_frames:
        return None
    ms = series["ms"].to_numpy(dtype=float)
    t = (ms - ms.min()) / 1000.0
    if t.max() <= 0:
        return None
    x = series["x"].to_numpy(dtype=float)
    y = series["y"].to_numpy(dtype=float)
    v, flags, _ = smooth_speed(series["s"].to_numpy(dtype=float), cfg)

    entry = v[t <= entry_window]
    exit_ = v[t >= t.max() - exit_window]
    i_peak = -1
    for k in range(1, v.size - 1):
        if v[k] >= v[k - 1] and v[k] >= v[k + 1]:
            i_peak = k
            break
    if i_peak < 0:
        flags.append("no_local_maximum")
        i_peak = int(np.argmax(v))
    i_global = int(np.argmax(v))
    peak_window = v[np.abs(t - t[i_global]) <= float(th["peak_window"]["pad_s"])]

    braking: float | None = None
    braking_se = float("nan")
    j_min = None
    saw_minimum = False
    for k in range(i_peak + 1, v.size - 1):
        if v[k] <= v[k - 1] and v[k] <= v[k + 1]:
            saw_minimum = True
            if (v[i_peak] - v[k]) >= min_drop:
                j_min = k
                break
    if j_min is not None:
        steps = np.hypot(np.diff(x[i_peak:j_min + 1]), np.diff(y[i_peak:j_min + 1]))
        if steps.size:
            braking = float(np.sum(steps))
            braking_se = (float(np.std(steps, ddof=1) * math.sqrt(steps.size))
                          if steps.size >= 2 else float("nan"))
    else:
        flags.append("braking_below_threshold" if saw_minimum
                     else "no_local_minimum")

    return {
        "nfl_id": int(series["nfl_id"].iloc[0]),
        "drill_name": str(series["drill_name"].iloc[0]),
        "attempt": int(series["attempt"].iloc[0]),
        "event_id": str(series["event_id"].iloc[0]),
        "n_frames": int(t.size),
        "duration_s": float(t.max() - t.min()),
        "entry_speed": float(np.mean(entry)) if entry.size else None,
        "se_entry_speed": _window_mean_se(entry),
        "peak_speed": float(v[i_global]),
        "se_peak_speed": _window_mean_se(peak_window),
        "exit_speed": float(np.mean(exit_)) if exit_.size else None,
        "se_exit_speed": _window_mean_se(exit_),
        "total_time_s": float(t.max() - t.min()),
        "se_total_time_s": float(step / math.sqrt(12.0)),
        "braking_distance_yds": braking,
        "se_braking_distance_yds": braking_se,
        "flags": flags,
    }


#: Lateral metric: (feature suffix, value column, SE column).
_LATERAL_METRICS = (
    ("entry_speed", "entry_speed", "se_entry_speed"),
    ("peak_speed", "peak_speed", "se_peak_speed"),
    ("exit_speed", "exit_speed", "se_exit_speed"),
    ("total_time", "total_time_s", "se_total_time_s"),
    ("braking_distance", "braking_distance_yds", "se_braking_distance_yds"),
)


def lateral_mechanics(frames: Any, cfg: Mapping) -> dict:
    """Per-attempt lateral mechanics plus per-player feature pairs and coverage.

    Returns ``{"attempts": [...], "pairs": {feature: {nfl_id: [(v, se)]}},
    "coverage": {...}}``.  Feature names are ``shuttle_*`` / ``cone_*``.
    """
    out = {"attempts": [], "pairs": {}, "coverage": {}}
    if frames is None or len(frames) == 0:
        return out
    prefixes = ceiling(cfg)["lateral_drills"]
    keys = ["nfl_id", "drill_name", "attempt", "event_id"]
    coverage: dict[str, dict[str, int]] = {}
    pairs: dict[str, dict[int, list[tuple[float, float]]]] = {}

    for key, grp in frames.groupby(keys, sort=True):
        rec = lateral_attempt(grp, cfg)
        if rec is None:
            continue
        out["attempts"].append(rec)
        prefix = _LATERAL_PREFIX.get(str(rec["drill_name"]),
                                     str(rec["drill_name"]).lower())
        for suffix, value_col, se_col in _LATERAL_METRICS:
            feature = f"{prefix}_{suffix}"
            value = rec[value_col]
            cov = coverage.setdefault(feature, {"n_attempts": 0, "n_players": 0,
                                                "n_missing": 0})
            if value is None or not np.isfinite(value) or value <= 0:
                cov["n_missing"] += 1
                continue
            cov["n_attempts"] += 1
            pairs.setdefault(feature, {}).setdefault(rec["nfl_id"], []).append(
                (float(value), float(rec[se_col])))
    for feature, by in pairs.items():
        coverage[feature]["n_players"] = len(by)
    for prefix in {_LATERAL_PREFIX.get(str(d), str(d).lower()) for d in prefixes}:
        for suffix, _, _ in _LATERAL_METRICS:
            coverage.setdefault(f"{prefix}_{suffix}",
                                {"n_attempts": 0, "n_players": 0, "n_missing": 0})
    out["pairs"] = pairs
    out["coverage"] = coverage
    return out


# --------------------------------------------------------------------------- #
# I/O (Combine files only)
# --------------------------------------------------------------------------- #
def read_attempt_frames(cfg: Mapping, drills: Sequence[str]) -> Any:
    """PLAYER frames for ``drills`` as a pandas frame, ordered by time.

    DuckDB with an explicit column list: the 84 MB file is never loaded whole
    into pandas.
    """
    from dl import dl_common as D

    files = D.source_paths(cfg, "combine_tracking")
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"Combine tracking missing: {missing}")
    paths = ", ".join("'" + f.as_posix().replace("'", "''") + "'" for f in files)
    thumbs = ", ".join("'" + str(d).replace("'", "''") + "'" for d in drills)
    con = D.duckdb_connect(cfg)
    try:
        df = con.execute(f"""
            SELECT nfl_id, draft_year, drill_type, drill_name, attempt,
                   event_id, epoch_ms(time) AS ms, s, a, x, y
            FROM read_csv_auto([{paths}], nullstr=['NA'])
            WHERE entity_type = 'PLAYER'
              AND drill_name IN ({thumbs})
              AND s IS NOT NULL AND x IS NOT NULL AND y IS NOT NULL
            ORDER BY nfl_id, drill_name, attempt, event_id, ms
        """).df()
    finally:
        con.close()
    if df.empty:
        raise RuntimeError(f"no PLAYER frames for drills {list(drills)}")
    return df


def read_players(cfg: Mapping) -> dict[int, dict]:
    """``{nfl_id: {"position": nfl_position, "draft_year": int}}``."""
    from dl import dl_common as D

    files = D.source_paths(cfg, "players")
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"players file missing: {missing}")
    con = D.duckdb_connect(cfg)
    try:
        rows = con.execute(f"""
            SELECT nfl_id, nfl_position, draft_year
            FROM read_csv_auto([{', '.join("'" + f.as_posix() + "'" for f in files)}],
                               nullstr=['NA'])
        """).fetchall()
    finally:
        con.close()
    return {int(r[0]): {"position": str(r[1]) if r[1] is not None else "",
                        "draft_year": int(r[2]) if r[2] is not None else 0}
            for r in rows}


def read_ten_yd_splits(cfg: Mapping) -> dict[int, float]:
    """``{nfl_id: ten_yd_split}`` from ``combine_results`` (seconds)."""
    from dl import dl_common as D

    files = D.source_paths(cfg, "combine_results")
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"combine_results missing: {missing}")
    con = D.duckdb_connect(cfg)
    try:
        rows = con.execute(f"""
            SELECT nfl_id, ten_yd_split
            FROM read_csv_auto([{', '.join("'" + f.as_posix() + "'" for f in files)}],
                               nullstr=['NA'])
            WHERE ten_yd_split IS NOT NULL
        """).fetchall()
    finally:
        con.close()
    return {int(r[0]): float(r[1]) for r in rows}


# --------------------------------------------------------------------------- #
# Full ceiling pipeline
# --------------------------------------------------------------------------- #
def _pairs_from_fits(fits: Iterable[AttemptFit], feature: str) -> dict[int, list[tuple[float, float]]]:
    by: dict[int, list[tuple[float, float]]] = {}
    for fit in fits:
        value = fit.feature_value(feature)
        if value is None or not np.isfinite(value) or value <= 0:
            continue
        by.setdefault(fit.nfl_id, []).append((float(value), fit.feature_se(feature)))
    return by


def _lateral_row(rec: Mapping) -> dict:
    """Map a lateral-attempt record onto the ``attempt_fits.csv`` schema."""
    return {
        "nfl_id": rec["nfl_id"],
        "drill_name": rec["drill_name"],
        "attempt": rec["attempt"],
        "event_id": rec["event_id"],
        "kind": "lateral",
        "n_frames": rec["n_frames"],
        "duration_s": rec["total_time_s"],
        "peak_speed": rec["peak_speed"],
        "vmax": "",
        "tau": "",
        "a0": "",
        "r2": "",
        "se_log_vmax": "",
        "se_log_tau": "",
        "se_log_a0": "",
        "speed_at_1_0": "",
        "speed_at_2_0": "",
        "entry_speed": rec["entry_speed"] if rec["entry_speed"] is not None else "",
        "exit_speed": rec["exit_speed"] if rec["exit_speed"] is not None else "",
        "total_time_s": rec["total_time_s"],
        "braking_distance_yds": (rec["braking_distance_yds"]
                                 if rec["braking_distance_yds"] is not None else ""),
        "ten_yd_split": "",
        "peak_accel": "",
        "flags": "|".join(rec["flags"]),
    }


def compute_ceiling_features(cfg: Mapping) -> dict:
    """Run the whole Phase 2 ceiling: fits -> shrinkage -> ICC/VIF screen.

    Returns ``{"rows", "attempt_rows", "fits", "selected", "summary"}`` where
    ``rows`` is the long per-player-per-feature table written to
    ``player_ceiling.csv``.
    """
    blk = ceiling(cfg)
    dl_block = block(cfg)
    dl_positions = list(dl_block["positions"]["dl"])

    players = read_players(cfg)
    dl_ids = {pid for pid, rec in players.items()
              if rec["position"] in dl_positions}
    if not dl_ids:
        raise RuntimeError(f"no DL players with nfl_position in {dl_positions}")
    unknown = {p["position"] for p in players.values()
               if p["position"] in dl_positions} - set(_POSITION_GROUP)
    if unknown:  # pragma: no cover - data dictionary guard
        raise RuntimeError(f"unmapped DL positions {sorted(unknown)}: add them to "
                           "_POSITION_GROUP with an edge/interior label")

    frames = read_attempt_frames(cfg, blk["fit_drills"])
    frames = frames[frames["nfl_id"].isin(dl_ids)]
    splits = {pid: v for pid, v in read_ten_yd_splits(cfg).items() if pid in dl_ids}

    fits, fit_summary = fit_frames(frames, cfg, splits=splits)
    if not fits:
        raise RuntimeError("no usable FORTY_YARD_DASH attempts after r2/min_frames "
                           "screens")

    split_se = split_noise_se(fits, splits)
    for fit in fits:
        fit.se_ten_yd_split = split_se

    try:
        lateral_frames = read_attempt_frames(cfg, blk["lateral_drills"])
        lateral = lateral_mechanics(
            lateral_frames[lateral_frames["nfl_id"].isin(dl_ids)], cfg)
    except (RuntimeError, FileNotFoundError):
        # No lateral drills for this cohort/world: an empty block, never fake
        # rows.  The coverage table then reports zero attempts everywhere.
        lateral = {"attempts": [], "pairs": {}, "coverage": {}}

    pooled: dict[str, dict] = {}
    for feature in blk["candidate_features"]:
        pairs = _pairs_from_fits(fits, feature)
        if not pairs:
            continue
        pooled[feature] = pool_feature(
            pairs, feature, cfg,
            icc_estimable=feature not in _ICC_NOT_ESTIMABLE)

    screen = select_features(pooled, SELECTION_POOL, cfg)

    lateral_pooled: dict[str, dict] = {}
    for feature in sorted(lateral["pairs"]):
        lateral_pooled[feature] = pool_feature(lateral["pairs"][feature], feature, cfg)

    selected = set(screen["selected"])
    rows: list[dict] = []
    for feature in blk["candidate_features"]:
        if feature not in pooled:
            continue
        for row in pooled[feature]["rows"]:
            pid = row["nfl_id"]
            rows.append({
                "nfl_id": pid,
                "position_group": _POSITION_GROUP[players[pid]["position"]],
                "feature": feature,
                "estimate": row["estimate"],
                "se": row["se"],
                "n_attempts": row["n_attempts"],
                "icc": row["icc"],
                "reliability": row["reliability"],
                "selected": feature in selected,
                "vif": screen["vif"].get(feature, ""),
            })
    for feature in sorted(lateral_pooled):
        for row in lateral_pooled[feature]["rows"]:
            pid = row["nfl_id"]
            rows.append({
                "nfl_id": pid,
                "position_group": _POSITION_GROUP[players[pid]["position"]],
                "feature": feature,
                "estimate": row["estimate"],
                "se": row["se"],
                "n_attempts": row["n_attempts"],
                "icc": row["icc"],
                "reliability": row["reliability"],
                "selected": False,
                "vif": "",
            })
    rows.sort(key=lambda r: (r["nfl_id"], r["feature"]))

    attempt_rows = [f.as_row() for f in fits]
    attempt_rows.sort(key=lambda r: (r["nfl_id"], r["attempt"], r["event_id"]))
    attempt_rows.extend(_lateral_row(rec) for rec in lateral["attempts"])

    primary = str(blk["primary_feature"])
    fallback = str(blk.get("fallback_feature", ""))
    if primary != PRIMARY_FEATURE:  # pragma: no cover - config guard
        raise RuntimeError(
            f"ceiling.primary_feature is {primary!r} but the screen admits "
            f"{PRIMARY_FEATURE!r} first (D69); fix config.yaml")
    primary_selected = bool(screen["primary_selected"])

    def _mean(values: Sequence[float]) -> float:
        clean = [float(v) for v in values if np.isfinite(v)]
        return float(np.mean(clean)) if clean else float("nan")

    def _median(values: Sequence[float]) -> float:
        clean = [float(v) for v in values if np.isfinite(v)]
        return float(np.median(clean)) if clean else float("nan")

    alternates: list[dict] = []
    for feature in SELECTION_POOL:
        block_f = pooled.get(feature)
        f_rows = block_f["rows"] if block_f else []
        alternates.append({
            "feature": feature,
            "role": "primary" if feature == primary else "alternate",
            "icc": (float(block_f["icc"]) if block_f
                    else float("nan")),
            # Delete-one-player jackknife SE.  A player-clustered bootstrap CI
            # is still required before the prereg freezes the reliability
            # claim (D69(5)); this SE is the honest interim number, never a
            # substitute for that bootstrap.
            "icc_se": (float(block_f.get("icc_se", float("nan"))) if block_f
                       else float("nan")),
            "icc_se_method": "delete_one_player_jackknife",
            "icc_ci_status": "pending_player_clustered_bootstrap",
            "se_median_natural": _median([row["se"] for row in f_rows]),
            "reliability_mean": _mean([row["reliability"] for row in f_rows]),
            "n_players": len(f_rows),
            "n_attempts": int(block_f["n_attempts"]) if block_f else 0,
            "selected": feature in selected,
            "vif": screen["vif"].get(feature, float("nan")),
            "drop_reason": screen["dropped"].get(feature, ""),
        })

    between = {
        f"{first}_vs_{second}": between_player_corr(
            pooled, first, second) if first in pooled and second in pooled
        else {"first": first, "second": second,
              "method": "between_player_shrunken", "scale": "natural",
              "n_players": 0, "r": float("nan"), "estimable": False,
              "reason": "feature not pooled in this cohort"}
        for first, second in (("vmax", "a0"), ("vmax", "tau"),
                              ("vmax", "speed_at_1_0"))
    }

    gate_reasons: list[str] = []
    if not primary_selected:
        gate_reasons.append(screen["dropped"].get(primary, "not_selected"))
    summary = {
        "fit": fit_summary,
        "n_players_total": len(dl_ids),
        "n_players_with_fits": len({f.nfl_id for f in fits}),
        "n_attempts_used": len(fits),
        "n_lateral_attempts": len(lateral["attempts"]),
        "specification_curve": True,
        "icc": {f: pooled[f]["icc"] for f in pooled},
        "icc_se": {f: float(pooled[f].get("icc_se", float("nan")))
                   for f in pooled},
        "icc_se_method": "delete_one_player_jackknife",
        "icc_excluded_players": {f: pooled[f]["n_players_excluded_icc"] for f in pooled},
        "feature_counts": {f: {"n_players": pooled[f]["n_players"],
                               "n_attempts": pooled[f]["n_attempts"]}
                           for f in pooled},
        "icc_not_estimable": {f: _ICC_NOT_ESTIMABLE[f] for f in _ICC_NOT_ESTIMABLE
                              if f in blk["candidate_features"]},
        "vif": screen["vif"],
        "vif_at_admission": screen["admission_vif"],
        "feature_drop_reasons": screen["dropped"],
        "selected_features": screen["selected"],
        "alternates": alternates,
        "eligible_features": screen["eligible"],
        "ineligible_features": screen["ineligible"],
        "primary_feature": primary,
        "primary_selected": primary_selected,
        "fallback_feature": fallback,
        "fallback_selected": fallback in selected,
        "feature_gate": "PASS" if primary_selected else "FAIL_PRIMARY_ICC_BELOW_FLOOR",
        "gate_reasons": gate_reasons,
        "icc_min": float(thresholds(cfg)["icc_min"]),
        "vif_max": float(thresholds(cfg)["vif_max"]),
        "max_features": int(thresholds(cfg)["max_features"]),
        "min_frames": int(thresholds(cfg)["min_frames"]),
        "min_r2": float(thresholds(cfg)["min_r2"]),
        "split_noise_s": split_se,
        "corr_vmax_a0": within_player_corr(fits, "vmax", "a0"),
        "corr_vmax_tau": within_player_corr(fits, "vmax", "tau"),
        "corr_vmax_speed_at_1_0": within_player_corr(fits, "vmax", "speed_at_1_0"),
        "between_player_corr": between,
        "lateral_coverage": lateral["coverage"],
        "position_groups": {
            group: sum(1 for pid in dl_ids
                       if _POSITION_GROUP[players[pid]["position"]] == group)
            for group in ("edge", "interior")
        },
        "note": ("Ceiling features only. Lateral mechanics are exploratory; "
                 "sample-mode numbers are not results."),
    }
    return {"rows": rows, "attempt_rows": attempt_rows, "fits": fits,
            "selected": screen["selected"], "summary": summary}
