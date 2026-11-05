"""stats — small, dependency-free reliability estimators.

Only what Phase 1 needs: intraclass correlations for test-retest and split-half,
plus a design-power approximation. Nothing here is estimated from data at import
time; every function is pure.

Formulas are stated in the docstrings so the audit can be checked by hand. No
SciPy/statsmodels dependency: the Pi venv does not have them, and these are
closed-form.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def _mean(v: Sequence[float]) -> float:
    return sum(v) / len(v)


def _var(v: Sequence[float]) -> float:
    if len(v) < 2:
        return float("nan")
    m = _mean(v)
    return sum((x - m) ** 2 for x in v) / (len(v) - 1)


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 3:
        return float("nan")
    mx, my = _mean(x), _mean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def spearman_brown(r: float, n_items: int = 2) -> float:
    """Step-up a split-half correlation to the reliability of the full test."""
    if r is None or math.isnan(r) or (1 + (n_items - 1) * r) == 0:
        return float("nan")
    return (n_items * r) / (1 + (n_items - 1) * r)


def icc_oneway(groups: Iterable[Sequence[float]]) -> dict:
    """ICC(1,1) for unbalanced one-way random effects (Shrout & Fleiss 1979).

        ICC = (MSB - MSW) / (MSB + (n0 - 1) * MSW)
        n0  = (1/(k-1)) * (N - sum(n_i^2)/N)

    ``groups`` is one sequence of measurements per subject (player). Subjects
    with fewer than two measurements carry no within information and are
    dropped; the count dropped is returned.
    """
    gs = [list(map(float, g)) for g in groups if len(g) >= 2]
    k = len(gs)
    if k < 2:
        return {"icc": float("nan"), "k_subjects": k, "n_obs": 0,
                "msb": float("nan"), "msw": float("nan"), "dropped": 0}
    N = sum(len(g) for g in gs)
    grand = sum(sum(g) for g in gs) / N
    ssb = sum(len(g) * (_mean(g) - grand) ** 2 for g in gs)
    ssw = sum(sum((x - _mean(g)) ** 2 for x in g) for g in gs)
    dfb, dfw = k - 1, N - k
    if dfw <= 0:
        return {"icc": float("nan"), "k_subjects": k, "n_obs": N,
                "msb": float("nan"), "msw": float("nan"), "dropped": 0}
    msb, msw = ssb / dfb, ssw / dfw
    n0 = (N - sum(len(g) ** 2 for g in gs) / N) / (k - 1)
    denom = msb + (n0 - 1) * msw
    icc = (msb - msw) / denom if denom > 0 else float("nan")
    return {"icc": icc, "k_subjects": k, "n_obs": N,
            "msb": msb, "msw": msw, "n0": n0,
            "mean_obs_per_subject": N / k, "dropped": 0}


def icc_two_way(matrix: Sequence[Sequence[float]]) -> dict:
    """ICC(2,1) for a complete subjects x raters matrix (absolute agreement).

    Used for split-half, where the two "raters" are the odd-game mean and the
    even-game mean for each player.
    """
    rows = [r for r in matrix if all(v is not None and not math.isnan(v) for v in r)]
    n, k = len(rows), (len(rows[0]) if rows else 0)
    if n < 3 or k < 2:
        return {"icc": float("nan"), "n_subjects": n, "n_raters": k}
    grand = sum(sum(r) for r in rows) / (n * k)
    row_means = [_mean(r) for r in rows]
    col_means = [_mean([rows[i][j] for i in range(n)]) for j in range(k)]
    ss_total = sum((rows[i][j] - grand) ** 2 for i in range(n) for j in range(k))
    ss_row = k * sum((m - grand) ** 2 for m in row_means)
    ss_col = n * sum((m - grand) ** 2 for m in col_means)
    ss_err = ss_total - ss_row - ss_col
    msr = ss_row / (n - 1)
    msc = ss_col / (k - 1)
    mse = ss_err / ((n - 1) * (k - 1))
    denom = msr + (k - 1) * mse + (k / n) * (msc - mse)
    icc = (msr - mse) / denom if denom > 0 else float("nan")
    return {"icc": icc, "n_subjects": n, "n_raters": k,
            "msr": msr, "msc": msc, "mse": mse}


def split_half_icc(player_halves: Sequence[tuple[float, float]]) -> dict:
    """Pearson r, Spearman-Brown and ICC(2,1) for (odd_mean, even_mean) pairs."""
    pairs = [(a, b) for a, b in player_halves
             if a is not None and b is not None
             and not (isinstance(a, float) and math.isnan(a))
             and not (isinstance(b, float) and math.isnan(b))]
    if len(pairs) < 3:
        return {"n_players": len(pairs), "r": float("nan"),
                "spearman_brown": float("nan"), "icc_2_1": float("nan")}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    r = pearson(xs, ys)
    return {"n_players": len(pairs), "r": r,
            "spearman_brown": spearman_brown(r, 2),
            "icc_2_1": icc_two_way(pairs)["icc"]}


def min_detectable_share(n_players: int, z: float = 1.96) -> dict:
    """Sampling-error floor on a between-player variance share.

    With P players, Var(observed player mean) = sigma_u^2 + sigma_e^2/n. The
    variance-share estimator has approximate sampling SD sqrt(2/P) times the
    total (normal approximation to the sampling distribution of a variance).
    A share must therefore exceed z * sqrt(2/P) to be distinguishable from zero.

    This is a DESIGN approximation, not a data estimate: it says what the design
    can resolve, and is reported as such.
    """
    if n_players < 2:
        return {"n_players": n_players, "min_share": float("nan")}
    return {"n_players": n_players,
            "min_share": z * math.sqrt(2.0 / n_players),
            "method": "z*sqrt(2/P), normal approximation to variance sampling SD"}


def reliability_from_share(share: float, snaps_per_player: float) -> float:
    """Model-based reliability of a player mean: s2u / (s2u + s2e/n)."""
    if share is None or math.isnan(share) or not 0 <= share < 1:
        return float("nan")
    if snaps_per_player <= 0:
        return float("nan")
    s2u = share
    s2e = 1 - share
    return s2u / (s2u + s2e / snaps_per_player)


def describe(values: Sequence[float]) -> dict:
    v = [float(x) for x in values if x is not None and not math.isnan(float(x))]
    if not v:
        return {"n": 0}
    v.sort()
    n = len(v)

    def q(p):
        """Linear-interpolated quantile (the numpy default)."""
        if n == 1:
            return v[0]
        pos = p * (n - 1)
        lo = int(math.floor(pos))
        hi = min(n - 1, lo + 1)
        frac = pos - lo
        return v[lo] * (1 - frac) + v[hi] * frac
    return {"n": n, "mean": _mean(v), "sd": math.sqrt(_var(v)),
            "min": v[0], "p05": q(0.05), "median": q(0.5), "p95": q(0.95),
            "max": v[-1], "sum": sum(v)}
