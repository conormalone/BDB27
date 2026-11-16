"""Fallback validation: band reliability, battery discriminant validity, speed coverage.

Estimators and label rules specified in ``notes/fallback-validation-spec.md``
(committed at c95c983, **before** any fit in this cycle).

Three legs, all on quantities that need no opponent identifier:

1. **Reliability** — ICC(1) of ``apex_retention`` within player across the
   30-60 degree game band; split-half (odd/even) + Spearman-Brown; 2024 vs 2025
   per-player season means; ``R_mean`` and the implied attenuation.
2. **Discriminant validity** — per Combine drill, univariate OLS of the player's
   shallow-break mean (S) and deep-plant mean (P) on the standardised drill, with
   HC1 errors; the paired player-level bootstrap of ``Delta = b_P - b_S``; the
   pre-declared quarter-SD materiality bar; the post-hoc MDE.
3. **Speed coverage** — descriptive only: what entry speed the Combine ever
   records against what the band requires.

Two rules hold everywhere in this module (Conor, 2026-11-15, D202):

* **No arbitrary gate.** No function here branches on a computed number to skip,
  stop or raise. Every threshold returns a *label* and the denominator it was
  computed on. The only exceptions are a missing/unreadable input file and a
  missing required column — and those name everything they tried.
* **Saturated display.** Where ``n observations <= n parameters`` the interval is
  printed as ``not estimable (saturated)`` with the cell's counts, the point
  estimate is still printed where one exists, and no zero-width interval is ever
  emitted. The estimator's maths, identification guard and point estimate are not
  changed by this rule (spec §7).

Nothing here reads the tracking file, and nothing here uses a position group
other than DB. Every number is ``[FULL]``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

# ------------------------------------------------------------------- constants
SPEC = "notes/fallback-validation-spec.md"
PROVENANCE = "FULL"
STAMP = "[FULL]"

#: Seed for every bootstrap in this cycle (spec §1.3, §2.3).
SEED = 20261115
#: Player-level bootstrap replications (spec §1.3, §2.3).
BOOTSTRAP_REPS = 2000

#: The band under test, and the deep-plant band the battery over-supplies.
BAND_LO, BAND_HI = 30.0, 60.0
DEEP_LO, DEEP_HI = 120.0, 150.0
DEEP_SENS_LO, DEEP_SENS_HI = 150.0, 180.0

#: Materiality bar, fixed before the fit: a quarter of the between-player SD.
MATERIAL_SHARE = 0.25

#: The four Combine drills, and the two that contain an actual turning test.
DRILLS: tuple[str, ...] = ("three_cone", "short_shuttle", "forty", "ten_yd_split")
TURNING_DRILLS: tuple[str, ...] = ("three_cone", "short_shuttle")
SPEED_DRILLS: tuple[str, ...] = ("forty", "ten_yd_split")

#: Every drill in this table is a time in seconds: **lower is better**, so the
#: sign is flipped before standardising and a positive coefficient always means
#: "better drill value -> more retention".
LOWER_IS_BETTER: frozenset[str] = frozenset(DRILLS)

#: Leg 1 verdict labels (§1.4) and leg 2 labels (§2.4).
LEG1_LABELS = ("STABLE", "WEAK", "VOID", "UNDETERMINED")
LEG2_ORDER = ("TRANSFERS", "SPEED-TRANSFERS", "NULL-BATTERY", "DISCRIMINANT",
              "UNDERPOWERED")

#: Display literals. `SATURATED` is the exact string the spec requires (§7).
SATURATED = "not estimable (saturated)"
DEGENERATE = "not estimable (degenerate)"
NOT_FITTED = "not fitted"

#: Entry-speed classes (yd/s) and turn-angle classes for the §3.5 crosstab.
SPEED_CLASS_EDGES: tuple[tuple[str, float, float], ...] = (
    ("below 3", -math.inf, 3.0),
    ("3-5", 3.0, 5.0),
    ("5-7", 5.0, 7.0),
    ("above 7", 7.0, math.inf),
)
ANGLE_CLASSES: tuple[tuple[str, float, float], ...] = (
    ("30-60", 30.0, 60.0),
    ("60-90", 60.0, 90.0),
    ("90-120", 90.0, 120.0),
    ("120-180", 120.0, 180.0),
)

#: Drill-level entry-speed medians quoted from TASK.md §3, per spec §3.3. These
#: are **quoted**, not recomputed, and carry that provenance in the artefact.
TASK_MD_DRILL_ENTRY_MEDIAN = {
    "three_cone": {"median": 3.98, "source": "TASK.md §3", "provenance": PROVENANCE},
    "short_shuttle": {"median": 3.55, "source": "TASK.md §3", "provenance": PROVENANCE},
}

#: Independent, reproducible RNG streams, all rooted in :data:`SEED`. A stream
#: per estimator keeps one estimator's draw count from shifting another's, so a
#: partial re-run still reproduces the same numbers.
_STREAMS = {
    "icc1": 101,
    "split_half": 102,
    "split_half_min4": 103,
    "discriminant": 104,
    "descriptive_share": 105,
}


def rng_for(name: str) -> np.random.Generator:
    """The seeded generator for one estimator (deterministic across runs)."""
    return np.random.default_rng(np.random.SeedSequence([SEED, _STREAMS[name]]))


# --------------------------------------------------------------- display rules
def saturated(n_obs: float, n_params: int) -> bool:
    """``n observations <= n parameters`` — the spec's §7 saturation test."""
    return (not np.isfinite(n_obs)) or float(n_obs) <= float(n_params)


def interval_text(lo: float, hi: float, *, n_obs: float, n_params: int) -> str:
    """The interval as the notebook prints it, honouring spec §7.

    Three cases, in this order:

    * ``n`` observations at or below the parameter count -> ``SATURATED``;
    * a non-finite or zero-width interval -> ``DEGENERATE`` (never a zero-width
      interval is emitted);
    * otherwise ``[lo, hi]`` to three decimals.
    """
    if saturated(n_obs, n_params):
        return SATURATED
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return DEGENERATE
    return f"[{lo:.3f}, {hi:.3f}]"


def interval_status(lo: float, hi: float, *, n_obs: float, n_params: int) -> str:
    """The machine-readable companion of :func:`interval_text`."""
    if saturated(n_obs, n_params):
        return "saturated"
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return "degenerate"
    return "ok"


def _num(value: Any, digits: int = 4) -> float | None:
    """A JSON-safe float: ``None`` where a value is not finite."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if np.isfinite(value) else None


# ---------------------------------------------------------------- leg 1: ICC(1)
def icc1(y: Sequence[float], groups: Sequence[Any]) -> dict:
    """One-way random-effects ICC(1), unbalanced design (spec §1.3 item 1).

    Model ``y_ij = mu + a_i + e_ij``; ANOVA moments with
    ``k0 = (N - sum(k_i^2)/N) / (g - 1)``, ``sigma2_a = (MSB - MSW)/k0``,
    ``sigma2_e = MSW``, ``ICC = sigma2_a / (sigma2_a + sigma2_e)``.

    If ``MSB < MSW`` the point estimate is reported as ``0.000`` with
    ``clamped=True``: that is a **display convention** on the square-root-free
    ratio, and the mean squares are returned unchanged beside it.

    Never raises on small or degenerate input: it returns NaNs and a
    ``saturated`` flag instead.
    """
    df = pd.DataFrame({"y": np.asarray(y, float),
                       "g": np.asarray(groups)}).dropna()
    n_obs = int(len(df))
    exists = df.groupby("g")["y"]
    k = exists.size().to_numpy(float)
    mean = exists.mean().to_numpy(float)
    g = int(len(k))
    N = float(k.sum())

    out: dict[str, Any] = {
        "method": "ICC(1), one-way random effects, unbalanced (ANOVA moments)",
        "n_obs": n_obs, "n_groups": g, "k0": None, "msb": None, "msw": None,
        "df_between": max(g - 1, 0), "df_within": max(int(N) - g, 0),
        "sigma2_a": None, "sigma2_e": None, "icc": None, "clamped": False,
        "saturated": saturated(n_obs, 3),
        "min_turns_per_group": float(k.min()) if g else None,
        "median_turns_per_group": float(np.median(k)) if g else None,
        "max_turns_per_group": float(k.max()) if g else None,
        "kbar_arithmetic": float(k.mean()) if g else None,
        "kbar_harmonic": float(g / np.sum(1.0 / k)) if g and (k > 0).all() else None,
    }
    if g < 2 or N <= g:
        out["status"] = "not estimable: fewer than two groups or no within-group df"
        return out

    grand = float(df["y"].mean())
    ss_between = float(np.sum(k * (mean - grand) ** 2))
    ss_within = float(sum(((sub - sub.mean()) ** 2).sum()
                          for _, sub in df.groupby("g")["y"]))
    msb = ss_between / (g - 1)
    msw = ss_within / (N - g)
    k0 = (N - np.sum(k ** 2) / N) / (g - 1)

    out.update({"k0": float(k0), "msb": float(msb), "msw": float(msw),
                "sigma2_e": float(msw)})
    if k0 <= 0:
        out["status"] = "not estimable: k0 == 0 (every group holds every observation)"
        return out
    sigma2_a = (msb - msw) / k0
    clamped = sigma2_a < 0
    out["clamped"] = bool(clamped)
    out["sigma2_a_raw"] = float(sigma2_a)
    out["sigma2_a"] = float(max(sigma2_a, 0.0))
    denom = out["sigma2_a"] + msw
    out["icc"] = float(out["sigma2_a"] / denom) if denom > 0 else None
    out["status"] = "ok"
    return out


def icc1_analytic_ci(msb: float, msw: float, *, groups: int, n_obs: float,
                     k0: float, level: float = 0.95) -> dict:
    """Analytic F-based interval for ICC(1), printed as a cross-check only."""
    from scipy import stats

    if not (groups >= 2 and n_obs > groups and msw and msw > 0 and k0 > 0):
        return {"lo": None, "hi": None, "status": "not estimable",
                "method": "F-based (Searle/Donner) with k0 substitution"}
    alpha = 1.0 - level
    F = msb / msw
    f_hi = float(stats.f.ppf(1.0 - alpha / 2.0, groups - 1, n_obs - groups))
    f_lo = float(stats.f.ppf(alpha / 2.0, groups - 1, n_obs - groups))
    lo = (F / f_hi - 1.0) / (F / f_hi + k0 - 1.0) if f_hi > 0 else math.nan
    hi = (F / f_lo - 1.0) / (F / f_lo + k0 - 1.0) if f_lo > 0 else math.nan
    return {"lo": _num(lo), "hi": _num(hi), "status": "ok",
            "method": "F-based (Searle/Donner) with k0 substitution",
            "note": "cross-check only; the quoted interval is the player bootstrap"}


def icc1_bootstrap_ci(y: Sequence[float], groups: Sequence[Any], *,
                      reps: int = BOOTSTRAP_REPS, level: float = 0.95,
                      stream: str = "icc1") -> dict:
    """Player-level cluster bootstrap of ICC(1), percentile interval.

    Players are resampled with replacement; every drawn player contributes **all**
    of his turns in draw order, so the unbalanced design is preserved exactly as
    it is (spec §1.3). The per-player moments are pre-computed and the resampled
    sums of squares are formed from them, so a replicate costs O(g) rather than
    O(N).

    The replicate estimator is the **unclamped** ANOVA ratio — the clamp in
    :func:`icc1` is a display convention and is not allowed to change the maths.
    """
    df = pd.DataFrame({"y": np.asarray(y, float), "g": np.asarray(groups)}).dropna()
    if len(df) == 0:
        return {"lo": None, "hi": None, "reps": int(reps), "seed": SEED,
                "stream": stream, "level": level, "n_obs": 0,
                "n_estimable_reps": 0, "n_players": 0,
                "status": "not estimable: no observations"}
    ids, inv = np.unique(df["g"].to_numpy(), return_inverse=True)
    k = np.bincount(inv, minlength=len(ids)).astype(float)
    s1 = np.bincount(inv, weights=df["y"].to_numpy(float), minlength=len(ids))
    s2 = np.bincount(inv, weights=df["y"].to_numpy(float) ** 2, minlength=len(ids))
    mean = np.divide(s1, k, out=np.zeros_like(s1), where=k > 0)
    g = len(ids)
    rng = rng_for(stream)
    draws = np.full(int(reps), np.nan)
    if g >= 2:
        for r in range(int(reps)):
            idx = rng.integers(0, g, size=g)
            kk = k[idx]
            N = float(kk.sum())
            if N <= g:
                continue
            mi = mean[idx]
            grand = float((s1[idx]).sum() / N)
            ssb = float(np.sum(kk * (mi - grand) ** 2))
            ssw = float((s2[idx]).sum() - np.sum(kk * mi ** 2))
            msb, msw = ssb / (g - 1), ssw / (N - g)
            k0 = (N - float(np.sum(kk ** 2)) / N) / (g - 1)
            if k0 <= 0 or msw <= 0:
                continue
            sigma2_a = (msb - msw) / k0
            draws[r] = sigma2_a / (sigma2_a + msw)
    good = draws[np.isfinite(draws)]
    out = {"reps": int(reps), "seed": SEED, "stream": stream,
           "n_players": int(g), "n_obs": int(len(df)),
           "n_estimable_reps": int(good.size),
           "method": "player-level percentile bootstrap (unclamped ratio)",
           "level": level}
    if good.size == 0:
        out.update({"lo": None, "hi": None, "status": "no estimable replicate"})
        return out
    alpha = (1.0 - level) / 2.0
    lo, hi = np.quantile(good, [alpha, 1.0 - alpha])
    out.update({"lo": _num(lo), "hi": _num(hi), "status": "ok"})
    return out


# --------------------------------------------------- leg 1: split-half + S-B
def split_half_halves(turns: pd.DataFrame, *, value: str = "apex_retention",
                      player: str = "nfl_id") -> dict:
    """Assign each player's band turns alternately to half A / half B.

    Order is deterministic: ``(season, game_id, play_id, frame_start)`` within the
    player, ties broken by the input row order (the parquet is read ordered by
    ``game_id, play_id, nfl_id, frame_start``). Index 0, 2, 4 ... go to A.
    """
    cols = [player, "season", "game_id", "play_id", "frame_start", value]
    missing = [c for c in cols if c not in turns.columns]
    if missing:
        raise KeyError(f"split_half_halves: required column(s) missing: {missing}")
    t = turns[cols].dropna(subset=[value]).copy()
    t = t.sort_values([player, "season", "game_id", "play_id", "frame_start"],
                      kind="mergesort")
    t["_ord"] = t.groupby(player, sort=True).cumcount()
    t["half"] = np.where(t["_ord"] % 2 == 0, "A", "B")

    agg = (t.groupby([player, "half"], sort=True)[value]
             .agg(["count", "mean"]).reset_index())
    wide = agg.pivot(index=player, columns="half",
                     values=["count", "mean"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide = wide.reindex(columns=["count_A", "count_B", "mean_A", "mean_B"])
    wide[["count_A", "count_B"]] = wide[["count_A", "count_B"]].fillna(0.0)
    wide = wide.reset_index()
    wide["in_both"] = (wide["count_A"] > 0) & (wide["count_B"] > 0)
    return {"players": wide, "turns": t,
            "n_players": int(len(wide)),
            "n_players_in_both": int(wide["in_both"].sum()),
            "n_players_saturated": int((~wide["in_both"]).sum())}


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson r, NaN where it is undefined (never an exception)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if a.size < 3:
        return math.nan
    sa, sb = float(a.std(ddof=1)), float(b.std(ddof=1))
    if sa == 0.0 or sb == 0.0:
        return math.nan
    return float(np.corrcoef(a, b)[0, 1])


def spearman_brown(r: float) -> float:
    """``R_sb = 2r / (1 + r)`` — the split-half correction (spec §1.3 item 2)."""
    if not np.isfinite(r) or (1.0 + r) == 0:
        return math.nan
    return float(2.0 * r / (1.0 + r))


def split_half_reliability(turns: pd.DataFrame, *, min_per_half: int = 0,
                           reps: int = BOOTSTRAP_REPS,
                           stream: str = "split_half") -> dict:
    """Split-half correlation, Spearman-Brown, and its player bootstrap CI.

    Players with no turn in one half are **not estimable (saturated)** and are
    reported as such with their counts (spec §1.3 item 2, §7). ``min_per_half``
    > 0 gives the pre-declared sensitivity (>= 4 turns in each half).
    """
    halves = split_half_halves(turns)
    players = halves["players"]
    used = players[players["in_both"]].copy()
    if min_per_half > 0:
        used = used[(used["count_A"] >= min_per_half)
                    & (used["count_B"] >= min_per_half)].copy()

    a = used["mean_A"].to_numpy(float) if len(used) else np.array([], float)
    b = used["mean_B"].to_numpy(float) if len(used) else np.array([], float)
    r = _pearson(a, b)
    n = int(len(used))

    draws = np.full(int(reps), np.nan)
    if n >= 3:
        rng = rng_for(stream)
        for i in range(int(reps)):
            idx = rng.integers(0, n, size=n)
            draws[i] = _pearson(a[idx], b[idx])
    good = draws[np.isfinite(draws)]
    ci = {"lo": None, "hi": None, "n_estimable_reps": int(good.size)}
    if good.size:
        lo, hi = np.quantile(good, [0.025, 0.975])
        ci.update({"lo": _num(lo), "hi": _num(hi)})

    return {
        "method": "split-half (odd/even by season, game, play, frame) + Spearman-Brown",
        "r": _num(r), "spearman_brown": _num(spearman_brown(r)),
        "n_players_used": n,
        "n_players_dropped_one_sided": int(halves["n_players_saturated"]),
        "min_turns_per_half": int(min_per_half),
        "n_obs": n,                     # one (mean_A, mean_B) pair per player
        "n_params": 2,
        "saturated": saturated(n, 2),
        "ci": ci, "reps": int(reps), "seed": SEED, "stream": stream,
        "one_sided_players": [
            {"nfl_id": int(row[players.columns[0]]),
             "count_A": int(row["count_A"]), "count_B": int(row["count_B"]),
             "saturated": True}
            for _, row in players[~players["in_both"]].iterrows()
        ][:20],
        "n_players_total": int(halves["n_players"]),
        "n_turns": int(len(halves["turns"])),
    }


def season_means(turns: pd.DataFrame, *, value: str = "apex_retention",
                 player: str = "nfl_id") -> pd.DataFrame:
    """Per-player per-season means and counts, sorted deterministically."""
    out = (turns[[player, "season", value]].dropna()
             .groupby([player, "season"], sort=True)[value]
             .agg(["count", "mean"]).reset_index()
             .rename(columns={"count": "k", "mean": "mean"}))
    return out.sort_values([player, "season"], kind="mergesort").reset_index(drop=True)


def season_correlation(turns: pd.DataFrame, season_a: int = 2024,
                       season_b: int = 2025, *, min_turns: int = 1) -> dict:
    """Pearson r between a player's mean retention in two seasons.

    Both n variants are printed by the caller with both numbers (spec §1.3 item 3):
    all players with >= 1 turn in each season, and those with >= 10. This is a
    **within-player consistency check**, not a rate comparison — season rates are
    not comparable (``TASK.md`` honesty limit 9).
    """
    m = season_means(turns)
    a = m[m["season"] == season_a].set_index("nfl_id")
    b = m[m["season"] == season_b].set_index("nfl_id")
    both = a.join(b, how="inner", lsuffix="_a", rsuffix="_b")
    both = both[(both["k_a"] >= min_turns) & (both["k_b"] >= min_turns)]
    r = _pearson(both["mean_a"].to_numpy(float), both["mean_b"].to_numpy(float))
    n = int(len(both))
    return {
        "method": f"Pearson r of per-player season means, {season_a} vs {season_b}",
        "season_a": int(season_a), "season_b": int(season_b),
        "min_turns_per_season": int(min_turns),
        "r": _num(r), "n_players": n, "n_obs": n, "n_params": 2,
        "saturated": saturated(n, 2),
        "n_players_season_a": int(len(a)), "n_players_season_b": int(len(b)),
        "seasons_available": sorted(int(s) for s in turns["season"].dropna().unique()),
    }


def r_mean(icc: float, kbar: float) -> float:
    """Reliability of a player's *mean*: ``k*ICC / (1 + (k-1)*ICC)`` (spec §1.3)."""
    if not (np.isfinite(icc) and np.isfinite(kbar)) or kbar <= 0:
        return math.nan
    denom = 1.0 + (kbar - 1.0) * icc
    return float(kbar * icc / denom) if denom != 0 else math.nan


# ------------------------------------------------------------- leg 2: OLS/HC1
def ols_hc1(x: Sequence[float], y: Sequence[float],
            weights: Sequence[float] | None = None) -> dict:
    """Univariate OLS (or WLS) of ``y`` on ``x`` with HC1 standard errors.

    Two parameters (intercept, slope), so ``n <= 2`` is saturated and reported as
    such. The MDE is **post-hoc** (spec §2.3): the smallest ``|b|`` detectable at
    80% power, two-sided alpha = 0.05, given the achieved ``n`` and this slope's
    standard error — ``(t_{0.975,n-2} + t_{0.80,n-2}) * se``.
    """
    from scipy import stats

    x = np.asarray(x, float)
    y = np.asarray(y, float)
    w = np.ones_like(x) if weights is None else np.asarray(weights, float)
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(w) & (w > 0)
    x, y, w = x[ok], y[ok], w[ok]
    n = int(x.size)
    out: dict[str, Any] = {
        "method": "WLS with HC1" if weights is not None else "OLS with HC1",
        "n": n, "n_params": 2, "saturated": saturated(n, 2),
        "b": None, "se_hc1": None, "lo": None, "hi": None, "p_hc1": None,
        "r2": None, "resid_sd": None, "mde_80": None, "status": "ok",
        "sd_x": _num(np.std(x, ddof=1)) if n > 1 else None,
    }
    if n <= 2:
        # The point estimate exists at n = 2 (a line through two points); only its
        # standard error does not. §7 says print the point estimate and label the
        # interval saturated, so the slope is computed and kept.
        if n == 2 and float(np.ptp(x)) != 0.0:
            out["b"] = _num(float((y[1] - y[0]) / (x[1] - x[0])))
        out["status"] = "not estimable: n <= 2 (saturated)"
        return out
    if float(np.ptp(x)) == 0.0:
        out["status"] = "not estimable: no spread in the predictor"
        return out
    X = np.column_stack([np.ones(n), x])
    sw = np.sqrt(w)
    Xw, yw = X * sw[:, None], y * sw
    beta = np.linalg.lstsq(Xw, yw, rcond=None)[0]
    resid = y - X @ beta
    XtX_inv = np.linalg.pinv(Xw.T @ Xw)
    scores = X * (w * resid)[:, None]
    cov = (n / (n - 2.0)) * (XtX_inv @ (scores.T @ scores) @ XtX_inv)
    se = float(np.sqrt(max(cov[1, 1], 0.0)))
    b = float(beta[1])
    df = n - 2
    crit = float(stats.t.ppf(0.975, df))
    t_80 = float(stats.t.ppf(0.80, df))
    ss_res = float(resid @ resid)
    ss_tot = float(((y - np.average(y, weights=w)) ** 2 * w).sum())
    p = float(2 * stats.t.sf(abs(b / se), df)) if se > 0 else None
    out.update({
        "b": _num(b), "se_hc1": _num(se),
        "lo": _num(b - crit * se), "hi": _num(b + crit * se),
        "p_hc1": _num(p, 6), "r2": _num(1 - ss_res / ss_tot) if ss_tot else None,
        "resid_sd": _num(math.sqrt(ss_res / df)),
        "mde_80": _num((crit + t_80) * se),
        "t_crit_975": _num(crit, 3), "df": int(df),
    })
    out["interval"] = interval_text(out["lo"], out["hi"], n_obs=n, n_params=2)
    out["interval_status"] = interval_status(out["lo"], out["hi"],
                                             n_obs=n, n_params=2)
    return out


def zscore_flipped(values: Sequence[float], drill: str) -> np.ndarray:
    """z-score a drill so that **positive = faster/better**.

    Every drill in this table is a time in seconds (lower is better), so the sign
    is flipped before standardising. A constant drill yields zeros (no spread),
    never a division error.
    """
    v = np.asarray(values, float)
    factor = -1.0 if drill in LOWER_IS_BETTER else 1.0
    v = v * factor
    sd = float(np.std(v, ddof=1)) if v.size > 1 else 0.0
    if not np.isfinite(sd) or sd == 0.0:
        return np.zeros_like(v)
    return (v - float(np.mean(v))) / sd


# ------------------------------------------------------------ label rules
def leg1_label(*, icc: float, ci_lo: float, ci_hi: float,
               r_sb: float) -> dict:
    """Apply spec §1.4 verbatim. A label, never control flow.

    The table leaves one cell unlisted (``ICC < 0.20`` with an upper bound at or
    above 0.40). That cell is reported as ``UNDETERMINED`` with the reason, so no
    condition can fall through to an exception and no number is silently
    discarded.
    """
    def ok(value):
        return value is not None and np.isfinite(value)

    stable = (ok(icc) and icc >= 0.50
              and ok(ci_lo) and ci_lo >= 0.30
              and ok(r_sb) and r_sb >= 0.50)
    weak = (not stable) and ok(icc) and icc >= 0.20
    void = ok(icc) and icc < 0.20 and ok(ci_hi) and ci_hi < 0.40
    if stable:
        label = "STABLE"
    elif void:
        label = "VOID"
    elif weak:
        label = "WEAK"
    else:
        label = "UNDETERMINED"
    return {
        "label": label,
        "consequence": {
            "STABLE": "the band is a trait; leg 2 decides whether the battery already reaches it",
            "WEAK": ("trait-like enough to be worth naming, too weak to carry a "
                     "recommendation on its own"),
            "VOID": ("the recommendation is void; leg 2 is reported descriptively "
                     "only"),
            "UNDETERMINED": ("no label in the §1.4 table covers this cell; it is "
                             "reported, not resolved"),
        }[label],
        "thresholds": {"icc_stable": 0.50, "icc_lower_stable": 0.30,
                       "r_sb_stable": 0.50, "icc_weak": 0.20, "icc_void": 0.20,
                       "icc_upper_void": 0.40},
        "inputs": {"icc": _num(icc), "ci_lo": _num(ci_lo), "ci_hi": _num(ci_hi),
                   "r_sb": _num(r_sb)},
        "conditions": {
            "stable_icc_ge_0.50": bool(ok(icc) and icc >= 0.50),
            "stable_lower_ge_0.30": bool(ok(ci_lo) and ci_lo >= 0.30),
            "stable_r_sb_ge_0.50": bool(ok(r_sb) and r_sb >= 0.50),
            "weak_icc_ge_0.20": bool(ok(icc) and icc >= 0.20),
            "void_icc_lt_0.20": bool(ok(icc) and icc < 0.20),
            "void_upper_lt_0.40": bool(ok(ci_hi) and ci_hi < 0.40),
        },
        "why": {
            "STABLE": "ICC >= 0.50, lower bound >= 0.30, Spearman-Brown >= 0.50",
            "WEAK": "not STABLE and ICC >= 0.20",
            "VOID": "ICC < 0.20 and upper bound < 0.40",
            "UNDETERMINED": ("ICC < 0.20 but its upper 95% bound is not below 0.40 — "
                             "the fourth cell of the §1.4 table; reported, not resolved"),
        }[label],
    }


def _material(b: float, sd: float, share: float = MATERIAL_SHARE) -> bool:
    """``|b| >= share * SD`` — the pre-declared materiality bar (§2.3)."""
    if b is None or sd is None or not np.isfinite(b) or not np.isfinite(sd):
        return False
    return abs(b) >= share * sd


def leg2_labels(rows: Sequence[dict]) -> dict:
    """Apply spec §2.4. Every rule is evaluated; the label follows §4's order.

    ``rows`` are per-drill dicts with ``drill``, ``b_s``/``b_s_lo``/``b_s_hi``,
    ``b_p``/``b_p_lo``/``b_p_hi``, ``delta``/``delta_lo``/``delta_hi``, ``sd_s``
    and ``sd_p``. A drill with no fit simply fails every comparison — it never
    raises.
    """
    by_drill = {r["drill"]: r for r in rows}

    def excludes_zero(lo, hi):
        return (lo is not None and hi is not None
                and np.isfinite(lo) and np.isfinite(hi) and (lo > 0 or hi < 0))

    details: dict[str, Any] = {"per_drill": {}, "fired": []}

    for drill, r in by_drill.items():
        sd_s = r.get("sd_s")
        bar_s = MATERIAL_SHARE * sd_s if sd_s is not None and np.isfinite(sd_s) else None
        delta = r.get("delta")
        details["per_drill"][drill] = {
            "material_s": _material(r.get("b_s"), sd_s),
            "material_p": _material(r.get("b_p"), r.get("sd_p")),
            "b_s_bar": _num(bar_s),
            "b_s_ci_excludes_zero": excludes_zero(r.get("b_s_lo"), r.get("b_s_hi")),
            "delta": _num(delta),
            "delta_bar": _num(bar_s),
            "delta_meets_bar": bool(delta is not None and bar_s is not None
                                    and np.isfinite(delta) and delta >= bar_s),
            "delta_ci_excludes_zero": excludes_zero(r.get("delta_lo"),
                                                    r.get("delta_hi")),
        }

    def fires(label: str) -> bool:
        d = details["per_drill"]
        if label == "DISCRIMINANT":
            return any(
                d[x]["delta_meets_bar"] and d[x]["delta_ci_excludes_zero"]
                and not d[x]["material_s"]
                for x in TURNING_DRILLS if x in d)
        if label == "TRANSFERS":
            return any(d[x]["material_s"] and d[x]["b_s_ci_excludes_zero"]
                       for x in TURNING_DRILLS if x in d)
        if label == "SPEED-TRANSFERS":
            if not all(x in d for x in SPEED_DRILLS):
                return False
            return all(d[x]["material_s"] and d[x]["b_s_ci_excludes_zero"]
                       and not d[x]["delta_meets_bar"] for x in SPEED_DRILLS)
        if label == "NULL-BATTERY":
            fitted = [x for x in d
                      if by_drill[x].get("b_p") is not None
                      and np.isfinite(by_drill[x].get("b_p"))]
            if not fitted:
                return False
            return all(not d[x]["material_p"] for x in fitted)
        return False

    fired = [label for label in LEG2_ORDER[:-1] if fires(label)]
    label = fired[0] if fired else "UNDERPOWERED"
    details["fired"] = fired
    details["thresholds"] = {"material_share_of_sd": MATERIAL_SHARE}
    details["bars"] = {
        "sd_s": _num(rows[0].get("sd_s")) if rows else None,
        "sd_p": _num(rows[0].get("sd_p")) if rows else None,
        "b_s_bar": _num(MATERIAL_SHARE * rows[0]["sd_s"]) if rows
                    and rows[0].get("sd_s") is not None else None,
        "b_p_bar": _num(MATERIAL_SHARE * rows[0]["sd_p"]) if rows
                    and rows[0].get("sd_p") is not None else None,
    }
    details["label"] = label
    details["consequence"] = {
        "TRANSFERS": ("the battery already predicts the shallow break — weakens the "
                      "recommendation"),
        "SPEED-TRANSFERS": ("linear speed already reaches the band — weakens the "
                            "recommendation"),
        "NULL-BATTERY": ("the drills do not even predict the deep plant in this cohort: "
                         "the discriminant logic has no purchase and the leg is "
                         "uninformative, not support"),
        "DISCRIMINANT": ("the battery reaches the deep plant and not the shallow break — "
                         "supports the recommendation"),
        "UNDERPOWERED": "none of the §2.4 conditions fired; the MDEs are printed",
    }[label]
    details["why"] = {
        "TRANSFERS": "a turning drill predicts the shallow break materially with a CI excluding 0",
        "SPEED-TRANSFERS": "both speed drills predict the shallow break materially and Delta does not exceed the bar",
        "NULL-BATTERY": "no drill predicts the deep plant materially — the discriminant logic has no purchase",
        "DISCRIMINANT": "a turning drill tracks the deep plant more than the shallow break",
        "UNDERPOWERED": "none of the §2.4 conditions fired; the MDEs are printed with the table",
    }[label]
    return details


def verdict(leg1: str, leg2: str, *, mde_note: str = "") -> dict:
    """Apply the spec §4 order. The first rule that fires gives the headline.

    Never raises: a missing or unrecognised label simply fails its comparison and
    falls through to the final rule.
    """
    rules = [
        (1, leg1 == "VOID", "VOID",
         "Leg 1 = VOID — no stable band capacity in this cohort to measure"),
        (2, leg2 in ("TRANSFERS", "SPEED-TRANSFERS"), "WEAKENED",
         "Leg 2 = TRANSFERS or SPEED-TRANSFERS — the battery's measures already reach the band"),
        (3, leg2 == "NULL-BATTERY", "UNINFORMATIVE",
         "Leg 2 = NULL-BATTERY — the discriminant test has no purchase"),
        (4, leg1 == "STABLE" and leg2 == "DISCRIMINANT", "SUPPORTED",
         "Leg 1 = STABLE and Leg 2 = DISCRIMINANT — stable band trait the battery reaches only at the deep plant"),
        (5, leg1 == "WEAK" and leg2 == "DISCRIMINANT", "PARTIALLY SUPPORTED",
         "Leg 1 = WEAK and Leg 2 = DISCRIMINANT — direction consistent, trait strength below the bar"),
        (6, True, "INCONCLUSIVE / UNDERPOWERED",
         "no earlier rule fired — the MDEs are printed with the table"),
    ]
    fired = next(r for r in rules if r[1])
    return {
        "leg1": leg1, "leg2": leg2,
        "rule": int(fired[0]), "label": fired[2], "because": fired[3],
        "rules": [{"rule": n, "condition": bool(c), "label": lbl, "text": txt}
                  for n, c, lbl, txt in rules],
        "headline": f"RULE {fired[0]} FIRED — {fired[2]}",
        "mde_note": mde_note,
    }


# ------------------------------------------------------------------ data load
def connect(memory_limit: str = "1400MB", threads: int = 2):
    import duckdb
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'; SET threads={int(threads)}")
    return con


def _posix(path: str | Path) -> str:
    return Path(path).as_posix()


def load_game_turns(path: str | Path, *, con=None) -> pd.DataFrame:
    """Every valid DB game turn from 30 to under 180 degrees, from the FULL parquet.

    That span holds the 30-60 band, the 120-150 deep-plant band, the 150-180
    sensitivity band, and the four angle classes leg 3's crosstab needs. Only DB,
    only ``valid`` (the parquet carries one invalid row); the tracking file is not
    read and no opponent id is used.
    """
    con = con or connect()
    q = f"""
        SELECT setting, nfl_id, game_id, play_id, season, position_group,
               abs_turn_angle, apex_retention, entry_speed, plant, frame_start
        FROM read_parquet('{_posix(path)}')
        WHERE setting = 'game' AND position_group = 'DB' AND valid
          AND abs_turn_angle >= {BAND_LO} AND abs_turn_angle < {DEEP_SENS_HI}
        ORDER BY game_id, play_id, nfl_id, frame_start"""
    df = con.execute(q).df()
    missing = [c for c in ("nfl_id", "abs_turn_angle", "apex_retention")
               if c not in df.columns]
    if missing:
        raise KeyError(f"load_game_turns: required column(s) missing from "
                       f"{path}: {missing}")
    # Leg 3 reports how many turns fall outside its four angle classes. Those rows
    # are excluded here, so the count is taken at the same place the filter is.
    excluded = con.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{_posix(path)}')
        WHERE setting = 'game' AND position_group = 'DB' AND valid
          AND (abs_turn_angle < {BAND_LO} OR abs_turn_angle >= {DEEP_SENS_HI})""").fetchone()[0]
    df.attrs["excluded_outside_30_180"] = int(excluded)
    return df


def load_combine_turns(path: str | Path, *, con=None) -> pd.DataFrame:
    """The FULL Combine control pass (1,179 DB turns, 10 drills)."""
    con = con or connect()
    q = f"""
        SELECT nfl_id, drill_name, abs_turn_angle, entry_speed, band, valid
        FROM read_csv('{_posix(path)}', header=true, all_varchar=true,
                      nullstr=['NA',''])
        WHERE TRY_CAST(valid AS BOOLEAN)"""
    df = con.execute(q).df()
    for col in ("entry_speed", "abs_turn_angle"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_combine_results(path: str | Path, *, con=None,
                         position: str = "DB") -> pd.DataFrame:
    """Combine drill times for one position group, ``NA`` read as missing."""
    con = con or connect()
    cols = ", ".join(f"TRY_CAST({d} AS DOUBLE) AS {d}" for d in DRILLS)
    q = f"""
        SELECT TRY_CAST(nfl_id AS BIGINT) AS nfl_id, combine_position, {cols}
        FROM read_csv('{_posix(path)}', header=true, all_varchar=true,
                      nullstr=['NA',''])
        WHERE combine_position = '{position}'"""
    return con.execute(q).df()


# ------------------------------------------------------------------ leg 1
def reliability(game: pd.DataFrame, *, value: str = "apex_retention") -> dict:
    """Leg 1 in full: ICC(1) + CI, split-half, seasons, R_mean, label."""
    band = game[(game["abs_turn_angle"] >= BAND_LO)
                & (game["abs_turn_angle"] < BAND_HI)].copy()
    y = band[value].to_numpy(float)
    groups = band["nfl_id"].to_numpy()

    icc = icc1(y, groups)
    boot = icc1_bootstrap_ci(y, groups)
    analytic = icc1_analytic_ci(icc["msb"], icc["msw"], groups=icc["n_groups"],
                                n_obs=icc["n_obs"], k0=icc["k0"] or 0.0)
    icc.update({
        "ci_bootstrap": {"lo": boot["lo"], "hi": boot["hi"],
                         "n_estimable_reps": boot["n_estimable_reps"],
                         "reps": boot["reps"], "seed": boot["seed"]},
        "ci_analytic": analytic,
        "ci_quoted": "bootstrap (player-level, percentile); analytic printed as a cross-check",
    })
    icc["interval"] = interval_text(boot["lo"] if boot["lo"] is not None else math.nan,
                                    boot["hi"] if boot["hi"] is not None else math.nan,
                                    n_obs=icc["n_obs"], n_params=3)

    split = split_half_reliability(band, min_per_half=0, stream="split_half")
    split4 = split_half_reliability(band, min_per_half=4, stream="split_half_min4")

    seasons = season_means(band)
    season_counts = (seasons.groupby("season")["k"].agg(["sum", "size"])
                     .reset_index().rename(columns={"sum": "turns", "size": "players"}))
    season_corr = season_correlation(band, 2024, 2025, min_turns=1)
    season_corr10 = season_correlation(band, 2024, 2025, min_turns=10)

    kbar_w = float(np.mean(band.groupby("nfl_id").size()))
    k_harm = float(icc["kbar_harmonic"]) if icc["kbar_harmonic"] else math.nan
    icc_point = icc["icc"] if icc["icc"] is not None else math.nan
    means = {
        "kbar_sample_size_weighted": _num(kbar_w),
        "kbar_harmonic": _num(k_harm),
        "r_mean_sample_size_weighted": _num(r_mean(icc_point, kbar_w)),
        "r_mean_harmonic": _num(r_mean(icc_point, k_harm)),
    }
    sigma2_e = icc["sigma2_e"] if icc["sigma2_e"] is not None else math.nan
    k_i = band.groupby("nfl_id").size().to_numpy(float)
    se_player = (np.sqrt(sigma2_e / k_i) if np.isfinite(sigma2_e) and k_i.size
                 else np.array([math.nan]))
    se_finite = se_player[np.isfinite(se_player)]
    means.update({
        "se_of_player_mean_formula": "sqrt(sigma2_e / k_i), per player",
        "se_of_player_mean_n_players": int(se_finite.size),
        "se_of_player_mean_median": _num(np.median(se_finite)) if se_finite.size else None,
        "se_of_player_mean_min": _num(se_finite.min()) if se_finite.size else None,
        "se_of_player_mean_max": _num(se_finite.max()) if se_finite.size else None,
        "attenuation_factor_on_player_level_slope": _num(
            r_mean(icc_point, kbar_w)),
        "note": ("leg 2 regresses on player means, so its slope is attenuated by "
                 "R_mean: b_observed = b_true * R_mean"),
    })

    label = leg1_label(
        icc=icc_point,
        ci_lo=boot["lo"] if boot["lo"] is not None else math.nan,
        ci_hi=boot["hi"] if boot["hi"] is not None else math.nan,
        r_sb=split["spearman_brown"] if split["spearman_brown"] is not None else math.nan,
    )

    return {
        "provenance": PROVENANCE,
        "spec": SPEC,
        "stamp": STAMP,
        "band": {"lo": BAND_LO, "hi": BAND_HI, "setting": "game",
                 "position_group": "DB", "valid_only": True},
        "sample": {
            "n_turns": int(len(band)),
            "n_players": int(band["nfl_id"].nunique()),
            "n_plays": int(band[["game_id", "play_id"]].drop_duplicates().shape[0]),
            "n_games": int(band["game_id"].nunique()),
            "min_turns_per_player": int(k_i.min()) if k_i.size else 0,
            "median_turns_per_player": float(np.median(k_i)) if k_i.size else 0.0,
            "max_turns_per_player": int(k_i.max()) if k_i.size else 0,
            "players_le_4_turns": int((k_i <= 4).sum()),
            "value": value,
            "provenance": PROVENANCE,
        },
        "icc1": icc,
        "split_half": split,
        "split_half_min4": split4,
        "season_counts": [{"season": int(r["season"]), "turns": int(r["turns"]),
                           "players": int(r["players"])} for _, r in season_counts.iterrows()],
        "season_2024_2025": season_corr,
        "season_2024_2025_min10": season_corr10,
        "r_mean": means,
        "label": label,
    }


# ------------------------------------------------------------------ leg 2
def player_outcomes(game: pd.DataFrame) -> pd.DataFrame:
    """One row per player: mean retention and turn count in each primary band.

    S = 30-60 (the band under test); P = 120-150 (the band the battery
    over-supplies); P150 = 150-180, carried as a declared sensitivity.
    """
    def band_mask(lo, hi):
        return (game["abs_turn_angle"] >= lo) & (game["abs_turn_angle"] < hi)

    frames = []
    for name, (lo, hi) in {"s": (BAND_LO, BAND_HI),
                           "p": (DEEP_LO, DEEP_HI),
                           "p150": (DEEP_SENS_LO, DEEP_SENS_HI)}.items():
        sub = game[band_mask(lo, hi)]
        agg = (sub.groupby("nfl_id")["apex_retention"]
                  .agg(["count", "mean"]).rename(columns={"count": f"n_{name}",
                                                          "mean": name}))
        frames.append(agg)
    out = pd.concat(frames, axis=1).reset_index().sort_values("nfl_id")
    return out.reset_index(drop=True)


def discriminant(game: pd.DataFrame, combine_results: pd.DataFrame, *,
                 reps: int = BOOTSTRAP_REPS) -> tuple[pd.DataFrame, dict]:
    """Leg 2: per-drill S and P fits, the paired bootstrap of Delta, the labels.

    Returns ``(table, meta)`` where ``table`` has one row per drill and ``meta``
    carries the SDs, the bars, the multiplicity note and the leg-2 label.
    """
    players = player_outcomes(game)
    merged = players.merge(combine_results.drop(columns=["combine_position"],
                                                errors="ignore"),
                           on="nfl_id", how="left")

    sd_s = float(merged["s"].std(ddof=1)) if merged["s"].notna().sum() > 1 else math.nan
    sd_p = float(merged["p"].std(ddof=1)) if merged["p"].notna().sum() > 1 else math.nan
    sd_p150 = (float(merged["p150"].std(ddof=1))
               if merged["p150"].notna().sum() > 1 else math.nan)

    rows: list[dict] = []
    for drill in DRILLS:
        work = merged[["nfl_id", "s", "p", "p150", "n_s", "n_p", "n_p150",
                       drill]].copy()
        work = work.rename(columns={drill: "raw"})

        fit_s = _drill_fit(work, outcome="s", weight_col="n_s", drill=drill)
        fit_p = _drill_fit(work, outcome="p", weight_col="n_p", drill=drill)
        fit_p150 = _drill_fit(work, outcome="p150", weight_col="n_p150",
                              drill=drill)
        fit_s_w = _drill_fit(work, outcome="s", weight_col="n_s", drill=drill,
                             weighted=True)
        fit_p_w = _drill_fit(work, outcome="p", weight_col="n_p", drill=drill,
                             weighted=True)
        delta = _paired_delta(work, drill=drill, reps=reps)

        rows.append({
            "drill": drill,
            "n_s": fit_s["n"], "n_p": fit_p["n"], "n_delta": delta["n"],
            "sd_s": _num(sd_s), "sd_p": _num(sd_p),
            "b_s_per_s": fit_s["b_per_second"],
            "b_s_per_s_lo": fit_s["lo_per_second"],
            "b_s_per_s_hi": fit_s["hi_per_second"],
            "b_p_per_s": fit_p["b_per_second"],
            "b_p_per_s_lo": fit_p["lo_per_second"],
            "b_p_per_s_hi": fit_p["hi_per_second"],
            "sd_drill_s": fit_s["sd_drill"], "sd_drill_p": fit_p["sd_drill"],
            "b_s": fit_s["b"], "b_s_se_hc1": fit_s["se_hc1"],
            "b_s_lo": fit_s["lo"], "b_s_hi": fit_s["hi"], "b_s_p": fit_s["p_hc1"],
            "b_s_r2": fit_s["r2"], "b_s_mde_80": fit_s["mde_80"],
            "b_s_bar": _num(MATERIAL_SHARE * sd_s),
            "b_s_material": _material(fit_s["b"], sd_s),
            "b_p": fit_p["b"], "b_p_se_hc1": fit_p["se_hc1"],
            "b_p_lo": fit_p["lo"], "b_p_hi": fit_p["hi"], "b_p_p": fit_p["p_hc1"],
            "b_p_r2": fit_p["r2"], "b_p_mde_80": fit_p["mde_80"],
            "b_p_bar": _num(MATERIAL_SHARE * sd_p),
            "b_p_material": _material(fit_p["b"], sd_p),
            "b_p150": fit_p150["b"] if fit_p150 else None,
            "b_p150_lo": fit_p150["lo"] if fit_p150 else None,
            "b_p150_hi": fit_p150["hi"] if fit_p150 else None,
            "b_p150_material": _material(fit_p150["b"] if fit_p150 else None, sd_p150),
            "b_s_weighted": fit_s_w["b"], "b_s_weighted_lo": fit_s_w["lo"],
            "b_s_weighted_hi": fit_s_w["hi"],
            "b_p_weighted": fit_p_w["b"], "b_p_weighted_lo": fit_p_w["lo"],
            "b_p_weighted_hi": fit_p_w["hi"],
            "delta": delta["delta"], "delta_lo": delta["lo"],
            "delta_hi": delta["hi"],
            "delta_bar": _num(MATERIAL_SHARE * sd_s),
            "delta_meets_bar": bool(delta["delta"] is not None
                                    and np.isfinite(delta["delta"])
                                    and sd_s == sd_s
                                    and delta["delta"] >= MATERIAL_SHARE * sd_s),
            "delta_ci_excludes_zero": bool(
                delta["lo"] is not None and delta["hi"] is not None
                and (delta["lo"] > 0 or delta["hi"] < 0)),
            "delta_reps": delta["reps"], "delta_n_estimable_reps": delta["n_estimable_reps"],
            "b_s_status": fit_s["status"], "b_p_status": fit_p["status"],
            "delta_status": delta["status"],
            "stamp": STAMP,
        })

    labels = leg2_labels(rows)
    disagreement = []
    for r in rows:
        for name, left, right, bar_key in (("b_S", r["b_s"], r["b_s_weighted"], "b_s_bar"),
                                           ("b_P", r["b_p"], r["b_p_weighted"], "b_p_bar")):
            if left is None or right is None:
                continue
            if not (np.isfinite(left) and np.isfinite(right)):
                continue
            bar = r[bar_key]
            if np.sign(left) != np.sign(right):
                disagreement.append({"drill": r["drill"], "coefficient": name,
                                     "kind": "sign", "unweighted": left,
                                     "weighted": right})
            elif bar is not None and (abs(left) >= bar) != (abs(right) >= bar):
                disagreement.append({"drill": r["drill"], "coefficient": name,
                                     "kind": "materiality", "unweighted": left,
                                     "weighted": right, "bar": bar})
    n_intervals = 8 + len(rows)
    meta = {
        "provenance": PROVENANCE, "spec": SPEC,
        "sd_s": _num(sd_s), "sd_p": _num(sd_p), "sd_p150": _num(sd_p150),
        "bar_s": _num(MATERIAL_SHARE * sd_s), "bar_p": _num(MATERIAL_SHARE * sd_p),
        "material_share_of_sd": MATERIAL_SHARE,
        "multiplicity": {
            "n_coefficients": 8, "n_deltas": len(rows),
            "n_intervals": n_intervals,
            "expected_intervals_excluding_zero_by_chance_at_95pct": _num(
                0.05 * n_intervals),
            "correction_applied": "none — the verdict does not turn on a single marginal interval",
        },
        "join_coverage": {
            "game_dbs_s": int(merged["s"].notna().sum()),
            "game_dbs_p": int(merged["p"].notna().sum()),
            "game_dbs_p150": int(merged["p150"].notna().sum()),
            "drills": {d: int(merged[d].notna().sum()) for d in DRILLS},
        },
        "labels": labels,
        "label": labels["label"],
        "weighted_sensitivity": {
            "note": ("primary fit is unweighted; the n_i-weighted fit is printed beside "
                     "it, and on a sign or materiality disagreement the unweighted fit "
                     "is the one quoted"),
            "disagreements": disagreement,
            "n_disagreements": len(disagreement),
        },
        "estimator": ("univariate OLS on player means, HC1 SE; drill z-scored and "
                      "sign-flipped so positive = faster; Delta = b_P - b_S by a "
                      "paired player-level bootstrap that refits both equations on "
                      "the same resampled players"),
        "raw_unit_note": ("`b_*_per_s` is the same coefficient in raw units: retention "
                          "change per +1.0 s of drill time (slower). `b_*` is per one "
                          "between-player SD of the drill (z-scale)."),
        "provenance_note": ("FULL game turns; Combine drill times are the raw "
                            "combine_results.csv rows joined on nfl_id where "
                            "combine_position == 'DB'"),
    }
    return pd.DataFrame(rows), meta


def _drill_fit(work: pd.DataFrame, *, outcome: str, weight_col: str,
               drill: str, weighted: bool = False) -> dict:
    """One univariate fit of an outcome on the standardised drill."""
    cols = [outcome, "raw", weight_col] if weight_col in work else [outcome, "raw"]
    sub = work[cols].dropna(subset=[outcome, "raw"])
    if sub.empty:
        return {"n": 0, "b": None, "se_hc1": None, "lo": None, "hi": None,
                "p_hc1": None, "r2": None, "mde_80": None, "n_obs": 0,
                "sd_drill": None, "b_per_second": None,
                "lo_per_second": None, "hi_per_second": None,
                "raw_unit": "retention change per +1.0 s of drill time (slower)",
                "status": "not fitted: no player carries both the drill and the outcome"}
    z = zscore_flipped(sub["raw"].to_numpy(float), drill)
    w = sub[weight_col].to_numpy(float) if weighted and weight_col in sub else None
    fit = ols_hc1(z, sub[outcome].to_numpy(float), weights=w)
    fit["drill"] = drill
    fit["n_obs"] = int(len(sub))
    fit["interval"] = interval_text(_as_float(fit["lo"]), _as_float(fit["hi"]),
                                    n_obs=fit["n"], n_params=2)
    # The spec asks for the z-scale **and** the raw-unit coefficient in the
    # artefact (§2.3). z = factor * (t - mean) / sd, so dz/dt = factor / sd and a
    # raw coefficient reads "retention change per one extra second on the drill"
    # (i.e. per second slower, for a drill scored in seconds).
    raw = sub["raw"].to_numpy(float)
    sd_raw = float(np.std(raw, ddof=1)) if raw.size > 1 else math.nan
    factor = -1.0 if drill in LOWER_IS_BETTER else 1.0
    fit["sd_drill"] = _num(sd_raw)
    fit["raw_unit"] = "retention change per +1.0 s of drill time (slower)"
    if np.isfinite(sd_raw) and sd_raw > 0:
        scale = factor / sd_raw
        b, lo, hi = fit.get("b"), fit.get("lo"), fit.get("hi")
        fit["b_per_second"] = _num(b * scale) if b is not None else None
        # a negative scale flips the interval, so the bounds swap (lo < hi always)
        if lo is not None and hi is not None:
            a, z = lo * scale, hi * scale
            fit["lo_per_second"] = _num(min(a, z))
            fit["hi_per_second"] = _num(max(a, z))
        else:
            fit["lo_per_second"] = fit["hi_per_second"] = None
    else:
        fit.update({"b_per_second": None, "lo_per_second": None,
                    "hi_per_second": None})
    return fit


def _paired_delta(work: pd.DataFrame, *, drill: str,
                  reps: int = BOOTSTRAP_REPS) -> dict:
    """``Delta = b_P - b_S`` on the players carrying both outcomes, bootstrapped.

    Each replicate draws players with replacement and **refits both equations on
    the same drawn players**, recomputing the drill's z-score inside the draw, so
    the two coefficients always refer to the same standardised scale.
    """
    sub = work.dropna(subset=["s", "p", "raw"])
    n = int(len(sub))
    out = {"n": n, "delta": None, "lo": None, "hi": None,
           "reps": int(reps), "n_estimable_reps": 0,
           "status": "ok", "interval": SATURATED if n <= 2 else None}

    def refit(frame: pd.DataFrame) -> float:
        z = zscore_flipped(frame["raw"].to_numpy(float), drill)
        if np.ptp(z) == 0:
            return math.nan
        b_s = ols_hc1(z, frame["s"].to_numpy(float))["b"]
        b_p = ols_hc1(z, frame["p"].to_numpy(float))["b"]
        if b_s is None or b_p is None:
            return math.nan
        return float(b_p - b_s)

    if n >= 2:
        out["delta"] = _num(refit(sub))
    if n <= 2:
        # §7: the point estimate is printed where one exists (a line through two
        # points has a slope); only its interval does not exist.
        out["status"] = "not fitted: fewer than three players carry both outcomes"
        return out
    rng = rng_for("discriminant")
    draws = np.full(int(reps), np.nan)
    s = sub["s"].to_numpy(float)
    p = sub["p"].to_numpy(float)
    raw = sub["raw"].to_numpy(float)
    for i in range(int(reps)):
        idx = rng.integers(0, n, size=n)
        draws[i] = refit(pd.DataFrame({"s": s[idx], "p": p[idx], "raw": raw[idx]}))
    good = draws[np.isfinite(draws)]
    out["n_estimable_reps"] = int(good.size)
    if good.size:
        lo, hi = np.quantile(good, [0.025, 0.975])
        out.update({"lo": _num(lo), "hi": _num(hi)})
    else:
        out["status"] = "no estimable replicate"
    out["interval"] = interval_text(
        out["lo"] if out["lo"] is not None else math.nan,
        out["hi"] if out["hi"] is not None else math.nan,
        n_obs=n, n_params=2)
    return out


# ------------------------------------------------------------------ leg 3
def _speed_class(values: np.ndarray) -> np.ndarray:
    out = np.full(len(values), "", dtype=object)
    v = np.asarray(values, float)
    for name, lo, hi in SPEED_CLASS_EDGES:
        out[(v >= lo) & (v < hi)] = name
    return out


def _angle_class(values: np.ndarray) -> np.ndarray:
    out = np.full(len(values), "", dtype=object)
    v = np.asarray(values, float)
    for name, lo, hi in ANGLE_CLASSES:
        out[(v >= lo) & (v < hi)] = name
    return out


def speed_coverage(game: pd.DataFrame, combine: pd.DataFrame) -> dict:
    """Leg 3: descriptive only. No criterion, no verdict, no inference."""
    band = game[(game["abs_turn_angle"] >= BAND_LO)
                & (game["abs_turn_angle"] < BAND_HI)]
    g_speed = band["entry_speed"].to_numpy(float)
    c_band = combine[(combine["abs_turn_angle"] >= BAND_LO)
                     & (combine["abs_turn_angle"] < BAND_HI)]
    c_speed = c_band["entry_speed"].to_numpy(float)
    all_c_speed = combine["entry_speed"].to_numpy(float)

    pct = (5, 10, 25, 50, 75, 90, 95)

    def percentiles(values: np.ndarray) -> dict:
        v = values[np.isfinite(values)]
        if v.size == 0:
            return {"n": 0, "percentiles": {f"p{p}": None for p in pct}}
        return {"n": int(v.size),
                "percentiles": {f"p{p}": _num(np.percentile(v, p), 3) for p in pct},
                "min": _num(v.min(), 3), "max": _num(v.max(), 3),
                "mean": _num(v.mean(), 3)}

    def shares(values: np.ndarray) -> dict:
        v = values[np.isfinite(values)]
        n = int(v.size)
        out = {"n": n}
        for name, lo, hi in SPEED_CLASS_EDGES:
            k = int(((v >= lo) & (v < hi)).sum())
            out[name] = {"turns": k, "share": _num(k / n) if n else None}
        return out

    c_max = float(np.nanmax(all_c_speed)) if np.isfinite(all_c_speed).any() else math.nan
    above = int((g_speed > c_max).sum()) if np.isfinite(c_max) else 0

    by_drill = []
    drill_pcts = (5, 25, 50, 75, 95)
    for drill, sub in c_band.groupby("drill_name", sort=True):
        v = sub["entry_speed"].to_numpy(float)
        v = v[np.isfinite(v)]
        by_drill.append({
            "drill": str(drill), "n": int(v.size),
            "percentiles": ({f"p{p}": _num(np.percentile(v, p), 3) for p in drill_pcts}
                            if v.size else {f"p{p}": None for p in drill_pcts}),
            "median": _num(np.median(v), 3) if v.size else None,
            "min": _num(v.min(), 3) if v.size else None,
            "max": _num(v.max(), 3) if v.size else None,
        })
    by_drill.sort(key=lambda r: (-r["n"], r["drill"]))

    cells = []
    for setting, frame in (("game", game), ("combine", combine)):
        cls = _angle_class(frame["abs_turn_angle"].to_numpy(float))
        spd = _speed_class(frame["entry_speed"].to_numpy(float))
        keep = cls != ""
        tab = (pd.DataFrame({"angle": cls[keep], "speed": spd[keep]})
               .groupby(["angle", "speed"]).size())
        for angle, _, _ in ANGLE_CLASSES:
            for speed, _, _ in SPEED_CLASS_EDGES:
                cells.append({
                    "setting": setting, "angle_class": angle,
                    "speed_class": speed,
                    "turns": int(tab.get((angle, speed), 0)),
                })
    n_outside = int(game.attrs.get("excluded_outside_30_180", 0))
    n_outside_c = int(sum(1 for s in combine["abs_turn_angle"]
                          if not (BAND_LO <= float(s) < DEEP_SENS_HI)))
    cross = pd.DataFrame(cells)

    # The Combine's own band labels use an en dash and one open-ended class; the
    # numeric classes above are derived from abs_turn_angle. Report the agreement.
    label_map = {"30–60°": "30-60", "60–90°": "60-90", "90–120°": "90-120",
                 "120–150°": "120-180", "150–180°": "120-180"}
    if "band" in combine.columns:
        derived = _angle_class(combine["abs_turn_angle"].to_numpy(float))
        expected = combine["band"].map(label_map).fillna("").to_numpy()
        disagree = int(((expected != "") & (derived != expected)).sum())
    else:
        disagree = None

    return {
        "provenance": PROVENANCE, "spec": SPEC,
        "status": "descriptive — no criterion, no verdict, no inference",
        "game_band_entry_speed": {
            **percentiles(g_speed), "shares": shares(g_speed),
            "unit": "yd/s", "n_turns": int(g_speed.size),
            "n_players": int(band["nfl_id"].nunique()),
        },
        "combine_band_entry_speed": {
            **percentiles(c_speed),
            "values_sorted": [_num(v, 3) for v in np.sort(c_speed[np.isfinite(c_speed)])],
            "n_turns": int(np.isfinite(c_speed).sum()),
            "n_players": int(c_band["nfl_id"].nunique()),
            "note": "the 26 turns are small enough to print whole (spec §3.2)",
        },
        "combine_by_drill_in_band": by_drill,
        "task_md_drill_medians_quoted": TASK_MD_DRILL_ENTRY_MEDIAN,
        "combine_max_entry_speed": {
            "max": _num(c_max, 3), "n_turns_scanned": int(np.isfinite(all_c_speed).sum()),
            "n_players_scanned": int(combine["nfl_id"].nunique()),
            "game_band_turns_above": above,
            "game_band_turns": int(g_speed.size),
            "game_band_share_above": _num(above / g_speed.size) if g_speed.size else None,
            "note": ("the single number the leg exists for: the fastest entry speed the "
                     "Combine DB set ever records, and how much of the game band runs "
                     "faster than it"),
        },
        "crosstab": {
            "speed_classes": [c[0] for c in SPEED_CLASS_EDGES],
            "angle_classes": [c[0] for c in ANGLE_CLASSES],
            "grid": "4 speed classes x 4 angle classes x 2 settings (the spec's '4 x 2')",
            "turns_outside_the_four_angle_classes": {
                "game": n_outside, "combine": n_outside_c,
                "note": ("game: valid DB turns below 30 deg or at/above 180 deg, "
                         "excluded at load; combine: the >180 deg band"),
            },
            "cells": cells,
            "combine_label_agreement": {
                "rows_compared": int(len(combine)) if disagree is not None else 0,
                "disagreements": disagree,
                "note": ("derived classes from abs_turn_angle vs the CSV's own en-dash "
                         "band label; >180° is outside the four classes"),
            },
        },
        "figure_policy": "no figure — TASK.md allows exactly four and forbids a fifth",
    }


# --------------------------------------------------------------- orchestration
@dataclass
class Bundle:
    reliability: dict
    discriminant: pd.DataFrame
    discriminant_meta: dict
    speed: dict
    verdict: dict
    text: str
    extra: dict = field(default_factory=dict)


def assemble(rel: dict, table: pd.DataFrame, meta: dict, speed: dict, *,
             reps: int = BOOTSTRAP_REPS) -> Bundle:
    """The verdict and the written text from the three legs, already computed.

    Used by :func:`analyse` and by the notebook, so the notebook and the headless
    runner reach the same label through the same code.
    """
    mde_lines = "; ".join(
        f"{r['drill']} n_S={r['n_s']} MDE_S={r['b_s_mde_80']} "
        f"n_P={r['n_p']} MDE_P={r['b_p_mde_80']}"
        for _, r in table.iterrows())
    v = verdict(rel["label"]["label"], meta["label"], mde_note=mde_lines)
    v["leg1_why"] = rel["label"]["why"]
    v["leg2_why"] = meta["labels"]["why"]
    v["leg2_fired"] = meta["labels"]["fired"]
    text = render_verdict(rel, meta, v)
    return Bundle(reliability=rel, discriminant=table, discriminant_meta=meta,
                  speed=speed, verdict=v, text=text,
                  extra={"reps": reps, "seed": SEED})


def analyse(game: pd.DataFrame, combine: pd.DataFrame,
            combine_results: pd.DataFrame, *, reps: int = BOOTSTRAP_REPS) -> Bundle:
    """Run all three legs and the verdict rule, in the spec's order."""
    rel = reliability(game)
    table, meta = discriminant(game, combine_results, reps=reps)
    speed = speed_coverage(game, combine)
    return assemble(rel, table, meta, speed, reps=reps)


def _stat(value: Any, n_obs: float, n_params: int = 2, digits: int = 3) -> str:
    """A statistic as printed: the number, or the saturated label with its n."""
    if saturated(n_obs, n_params) or value is None or not np.isfinite(_as_float(value)):
        return f"{SATURATED} (n={int(n_obs) if np.isfinite(n_obs) else 0}, params={n_params})"
    return f"{float(value):.{digits}f}"


def render_reliability(rel: dict) -> list[str]:
    """Leg 1 as printed. Every line carries its n or its provenance stamp."""
    icc = rel["icc1"]
    sp = rel["split_half"]
    sp4 = rel["split_half_min4"]
    s24 = rel["season_2024_2025"]
    s24b = rel["season_2024_2025_min10"]
    rm = rel["r_mean"]
    sample = rel["sample"]
    lines = [
        f"LEG 1 — reliability of in-game 30-60 deg apex_retention {STAMP}",
        f"  sample: {sample['n_turns']:,} turns, {sample['n_players']} players, "
        f"{sample['n_plays']:,} plays, {sample['n_games']} games {STAMP}",
        f"  turns per player: min {sample['min_turns_per_player']}, "
        f"median {sample['median_turns_per_player']:.0f}, "
        f"max {sample['max_turns_per_player']}; "
        f"{sample['players_le_4_turns']} player(s) with <= 4 turns {STAMP}",
        f"  ICC(1): {_stat(icc['icc'], icc['n_obs'], 3)}"
        + (" (clamped)" if icc["clamped"] else "")
        + f" {icc['interval']}  [MSB={_fmt(icc['msb'], 4)}, MSW={_fmt(icc['msw'], 4)}, "
          f"k0={_fmt(icc['k0'], 2)}, g={icc['n_groups']}, N={icc['n_obs']:,}] {STAMP}",
        f"  ICC(1) analytic CI (cross-check, not quoted): "
        f"[{_fmt(icc['ci_analytic']['lo'])}, {_fmt(icc['ci_analytic']['hi'])}] "
        f"n={icc['n_obs']:,} {STAMP}",
        f"  split-half r = {_stat(sp['r'], sp['n_players_used'])}  "
        f"R_sb = {_stat(sp['spearman_brown'], sp['n_players_used'])}  "
        f"n_players={sp['n_players_used']} "
        f"({sp['n_players_dropped_one_sided']} one-sided, saturated) "
        f"CI [{sp['ci']['lo']}, {sp['ci']['hi']}] {STAMP}",
        f"  split-half sensitivity, >= 4 turns per half: "
        f"r = {_stat(sp4['r'], sp4['n_players_used'])}  "
        f"R_sb = {_stat(sp4['spearman_brown'], sp4['n_players_used'])}  "
        f"n_players={sp4['n_players_used']} {STAMP}",
        f"  2024 vs 2025 r = {_stat(s24['r'], s24['n_players'])}  "
        f"n={s24['n_players']} players (>= 1 turn each) {STAMP}",
        f"  2024 vs 2025 r = {_stat(s24b['r'], s24b['n_players'])}  "
        f"n={s24b['n_players']} players (>= 10 turns each) {STAMP}",
        f"  R_mean (harmonic kbar {_fmt(rm['kbar_harmonic'], 2)}) = "
        f"{_fmt(rm['r_mean_harmonic'])}; "
        f"R_mean (weighted kbar {_fmt(rm['kbar_sample_size_weighted'], 2)}) = "
        f"{_fmt(rm['r_mean_sample_size_weighted'])} {STAMP}",
        f"  SE of a player's mean: median {_fmt(rm['se_of_player_mean_median'])} "
        f"(range {_fmt(rm['se_of_player_mean_min'])} to "
        f"{_fmt(rm['se_of_player_mean_max'])}, n={rm['se_of_player_mean_n_players']} "
        f"players) {STAMP}",
        f"  LEG 1 LABEL: {rel['label']['label']} — {rel['label']['why']}",
        f"  consequence: {rel['label']['consequence']}",
    ]
    return lines


def render_discriminant(table: pd.DataFrame, meta: dict) -> list[str]:
    lines = [f"LEG 2 — discriminant validity {STAMP}",
             f"  SD(S) = {meta['sd_s']}  bar = {meta['bar_s']};  "
             f"SD(P) = {meta['sd_p']}  bar = {meta['bar_p']} "
             f"(a quarter of the between-player SD) {STAMP}",
             f"  join coverage: {meta['join_coverage']}"]
    for _, r in table.iterrows():
        lines.append(
            f"  {r['drill']}"
            f"\n    S: n={int(r['n_s'])}  b={_fmt(r['b_s'])} "
            f"{_ci(r['b_s_lo'], r['b_s_hi'], n_obs=r['n_s'])} "
            f"p(HC1)={_fmt(r['b_s_p'])}  MDE80={_fmt(r['b_s_mde_80'])}  "
            f"{'material' if r['b_s_material'] else 'not material'} "
            f"(bar {_fmt(r['b_s_bar'])})"
            f"\n    P: n={int(r['n_p'])}  b={_fmt(r['b_p'])} "
            f"{_ci(r['b_p_lo'], r['b_p_hi'], n_obs=r['n_p'])} "
            f"p(HC1)={_fmt(r['b_p_p'])}  MDE80={_fmt(r['b_p_mde_80'])}  "
            f"{'material' if r['b_p_material'] else 'not material'} "
            f"(bar {_fmt(r['b_p_bar'])})"
            f"\n    Delta = b_P - b_S: n={int(r['n_delta'])}  b={_fmt(r['delta'])} "
            f"{_ci(r['delta_lo'], r['delta_hi'], n_obs=r['n_delta'])} "
            f"{'meets' if r['delta_meets_bar'] else 'below'} the bar "
            f"({_fmt(r['delta_bar'])}); CI "
            f"{'excludes' if r['delta_ci_excludes_zero'] else 'includes'} 0"
            f"\n    150-180 deg sensitivity: b={_fmt(r['b_p150'])} "
            f"{_ci(r['b_p150_lo'], r['b_p150_hi'], n_obs=r['n_p'])} "
            f"{'material' if r['b_p150_material'] else 'not material'}"
            f"\n    raw units (per +1.0 s of drill time, i.e. slower): "
            f"b_S={_fmt(r['b_s_per_s'], 4)} "
            f"{_ci(r['b_s_per_s_lo'], r['b_s_per_s_hi'], n_obs=r['n_s'])}  "
            f"b_P={_fmt(r['b_p_per_s'], 4)} "
            f"{_ci(r['b_p_per_s_lo'], r['b_p_per_s_hi'], n_obs=r['n_p'])} "
            f"(SD of the drill: S {_fmt(r['sd_drill_s'], 3)} s, "
            f"P {_fmt(r['sd_drill_p'], 3)} s)"
            f"\n    n-weighted: b_S={_fmt(r['b_s_weighted'])} "
            f"{_ci(r['b_s_weighted_lo'], r['b_s_weighted_hi'], n_obs=r['n_s'])}  "
            f"b_P={_fmt(r['b_p_weighted'])} "
            f"{_ci(r['b_p_weighted_lo'], r['b_p_weighted_hi'], n_obs=r['n_p'])}")
    m = meta["multiplicity"]
    lines.append(f"  multiplicity: {m['n_intervals']} intervals, "
                 f"~{m['expected_intervals_excluding_zero_by_chance_at_95pct']} "
                 f"expected to exclude zero by chance at 95%; no correction applied")
    lines.append(f"  LEG 2 LABEL: {meta['label']} — {meta['labels']['why']}"
                 + (f" (also fired: {', '.join(x for x in meta['labels']['fired'] if x != meta['label'])})"
                    if len(meta["labels"]["fired"]) > 1 else ""))
    lines.append(f"  consequence: {meta['labels']['consequence']}")
    ws = meta["weighted_sensitivity"]
    if ws["disagreements"]:
        for d in ws["disagreements"]:
            lines.append(f"  WEIGHTED DISAGREEMENT ({d['kind']}): {d['drill']} "
                         f"{d['coefficient']} unweighted {_fmt(d['unweighted'])} vs "
                         f"n_i-weighted {_fmt(d['weighted'])} — the unweighted fit is "
                         f"quoted")
    else:
        lines.append("  weighted sensitivity: no sign or materiality disagreement")
    return lines


def _pct(value: Any, digits: int = 1) -> str:
    """A share as a percentage string, or an em dash where it does not exist."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{digits}%}" if np.isfinite(value) else "—"


def render_speed(speed: dict) -> list[str]:
    g = speed["game_band_entry_speed"]
    c = speed["combine_band_entry_speed"]
    mx = speed["combine_max_entry_speed"]
    lines = [
        f"LEG 3 — speed coverage (descriptive; no criterion, no verdict) {STAMP}",
        f"  game band entry speed, n = {g['n_turns']:,} turns / {g['n_players']} players: "
        + " ".join(f"{k}={_fmt(v)}" for k, v in g["percentiles"].items()) + f" {STAMP}",
        "  game band shares: " + "; ".join(
            f"{k} {v['turns']:,} ({_pct(v['share'])})" for k, v in g["shares"].items()
            if isinstance(v, dict)),
        f"  Combine band entry speed, n = {c['n_turns']} turns / {c['n_players']} players: "
        f"min={_fmt(c['min'])} median={_fmt(c['percentiles']['p50'])} "
        f"max={_fmt(c['max'])} {STAMP}",
        f"  Combine band values (sorted): {c['values_sorted']}",
        f"  Combine max entry speed anywhere in the {mx['n_turns_scanned']:,}-turn DB set: "
        f"{_fmt(mx['max'])} yd/s (n={mx['n_turns_scanned']} turns, "
        f"{mx['n_players_scanned']} players); game band turns ABOVE it: "
        f"{mx['game_band_turns_above']:,} of {mx['game_band_turns']:,} "
        f"({_pct(mx['game_band_share_above'])}) {STAMP}",
        "  Combine by drill, band turns (n, p25/median/p75): " + ("; ".join(
            f"{d['drill']} n={d['n']} "
            f"{_fmt(d['percentiles']['p25'])}/{_fmt(d['percentiles']['p50'])}/"
            f"{_fmt(d['percentiles']['p75'])}"
            for d in speed["combine_by_drill_in_band"]) or "none"),
        f"  drill-level medians quoted from TASK.md §3 (not recomputed): "
        f"3-cone {TASK_MD_DRILL_ENTRY_MEDIAN['three_cone']['median']}, "
        f"short shuttle {TASK_MD_DRILL_ENTRY_MEDIAN['short_shuttle']['median']} {STAMP}",
        "  crosstab (counts) — setting / angle class / speed class:",
    ]
    grid = {}
    for cell in speed["crosstab"]["cells"]:
        grid.setdefault(cell["setting"], {})[(cell["angle_class"], cell["speed_class"])] = cell["turns"]
    for setting, table in grid.items():
        lines.append(f"    {setting}:")
        for angle in speed["crosstab"]["angle_classes"]:
            row = " ".join(f"{s}={table.get((angle, s), 0):>4}"
                           for s in speed["crosstab"]["speed_classes"])
            lines.append(f"      {angle:>8} : {row}")
    outside = speed["crosstab"]["turns_outside_the_four_angle_classes"]
    lines.append(f"    outside the four angle classes: game {outside['game']:,} / "
                 f"combine {outside['combine']:,}")
    return lines


def render_verdict(rel: dict, meta: dict, v: dict) -> str:
    lines = [
        "FALLBACK VALIDATION — VERDICT",
        f"provenance: {PROVENANCE} {STAMP}   spec: {SPEC}",
        "",
        f"Leg 1 (reliability of the 30-60 deg band): {rel['label']['label']} {STAMP}",
        f"  {rel['label']['consequence']}",
        f"  ICC(1) {_stat(rel['icc1']['icc'], rel['icc1']['n_obs'], 3)}"
        + (" (clamped)" if rel["icc1"]["clamped"] else "")
        + f" {rel['icc1']['interval']}; split-half R_sb "
          f"{_stat(rel['split_half']['spearman_brown'], rel['split_half']['n_players_used'])}"
          f" (n={rel['split_half']['n_players_used']}) {STAMP}",
        f"Leg 2 (discriminant validity of the battery): {meta['label']} {STAMP}",
        f"  {meta['labels']['why']}",
        f"  {meta['labels']['consequence']}",
        "Leg 3 (speed coverage): descriptive only — see speed_coverage.json",
        "",
        f"{v['headline']}",
        f"because: {v['because']}",
        "",
        "rules evaluated in order (the first that fires gives the headline):",
    ]
    for rule in v["rules"]:
        mark = "FIRED" if rule["rule"] == v["rule"] else "no"
        lines.append(f"  {rule['rule']}. {mark:<5} {rule['label']} — {rule['text']}")
    lines += [
        "",
        "MDEs at 80% power (post-hoc, printed so a null is read as 'not detectable', "
        "never 'not present'):",
        f"  {v['mde_note']}",
        "",
        "This is a diagnostic note, not a deliverable. It does not enter TASK.md, the "
        "band table, the four figures or the report draft.",
    ]
    return "\n".join(lines) + "\n"


def _fmt(value: Any, digits: int = 3) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{digits}f}" if np.isfinite(value) else "—"


def _ci(lo: Any, hi: Any, *, n_obs: float, n_params: int = 2) -> str:
    return interval_text(_as_float(lo), _as_float(hi), n_obs=n_obs, n_params=n_params)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


# ------------------------------------------------------------------ outputs
def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head(repo_root: str | Path) -> str | None:
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=False)
    except OSError:
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def write_outputs(out_dir: str | Path, bundle: Bundle, *,
                  inputs: dict[str, str | Path], repo_root: str | Path | None = None,
                  generated_utc: str | None = None) -> dict:
    """Write the five artefacts and return the manifest.

    The four data artefacts carry no timestamps, so two consecutive runs are
    byte-identical. The manifest carries its own timestamp and its own hashes,
    which is what "honouring the manifest's own timestamps" excepts (spec §9.2).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    reliability_path = out / "reliability.json"
    discriminant_path = out / "discriminant.csv"
    speed_path = out / "speed_coverage.json"
    verdict_path = out / "verdict.txt"

    reliability_path.write_text(
        json.dumps(bundle.reliability, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    bundle.discriminant.to_csv(discriminant_path, index=False, encoding="utf-8")
    speed_payload = dict(bundle.speed)
    speed_payload["discriminant_labels"] = bundle.discriminant_meta["labels"]
    speed_path.write_text(
        json.dumps(speed_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    verdict_path.write_text(bundle.text, encoding="utf-8")

    outputs = {p.name: sha256_file(p) for p in
               (reliability_path, discriminant_path, speed_path, verdict_path)}
    manifest = {
        "provenance": PROVENANCE,
        "spec": SPEC,
        "generated_utc": generated_utc or _utc_now(),
        "git_head": git_head(repo_root) if repo_root else None,
        "seed": SEED,
        "bootstrap_reps": bundle.extra.get("reps", BOOTSTRAP_REPS),
        "config": {
            "band": [BAND_LO, BAND_HI],
            "deep_plant": [DEEP_LO, DEEP_HI],
            "deep_sensitivity": [DEEP_SENS_LO, DEEP_SENS_HI],
            "material_share_of_sd": MATERIAL_SHARE,
            "drills": list(DRILLS),
            "turning_drills": list(TURNING_DRILLS),
            "speed_drills": list(SPEED_DRILLS),
            "position_group": "DB",
            "rng_streams": dict(_STREAMS),
            "lower_is_better": sorted(LOWER_IS_BETTER),
        },
        "inputs": {name: {"path": str(Path(p)), "sha256": sha256_file(p)}
                   for name, p in inputs.items()},
        "outputs": outputs,
        "verdict": {"rule": bundle.verdict["rule"], "label": bundle.verdict["label"],
                    "leg1": bundle.verdict["leg1"], "leg2": bundle.verdict["leg2"]},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------- path resolution (D201)
_SEARCH_DEPTH = 4
_SEARCH_MAX_DIRS = 6000
_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv",
              "venv", ".mypy_cache", ".pytest_cache", "site-packages"}


def search_roots(repo: Path, home: Path | None = None) -> list[Path]:
    """The bounded list of roots a missing input is searched under (D201)."""
    home = home or Path.home()
    roots = [repo, repo / "data", repo / "data" / "raw", repo / "data" / "interim"]
    roots += [home, Path.cwd()]
    for name in ("Downloads", "Documents", "Desktop"):
        roots.append(home / name)
        roots.append(home / "OneDrive" / name)
    seen, unique = set(), []
    for root in roots:
        try:
            key = root.resolve()
        except OSError:
            continue
        if key in seen or not root.exists():
            continue
        seen.add(key)
        unique.append(root)
    return unique


def search_for(patterns: Sequence[str], roots: Sequence[Path], *,
               depth: int = _SEARCH_DEPTH,
               max_dirs: int = _SEARCH_MAX_DIRS) -> tuple[list[Path], list[str]]:
    """Breadth-first, bounded search; returns ``(matches, paths_tried)``."""
    import fnmatch

    matches: list[Path] = []
    tried: list[str] = []
    visited = 0
    for root in roots:
        queue = [(root, 0)]
        while queue and visited < max_dirs:
            folder, level = queue.pop(0)
            visited += 1
            tried.append(str(folder))
            try:
                entries = sorted(folder.iterdir())
            except (OSError, PermissionError):
                continue
            for entry in entries:
                if entry.is_file() and any(fnmatch.fnmatch(entry.name, p)
                                           for p in patterns):
                    matches.append(entry)
                elif (entry.is_dir() and level < depth
                      and entry.name not in _SKIP_DIRS
                      and not entry.name.startswith(".")):
                    queue.append((entry, level + 1))
    return matches, tried


def rank_candidates(matches: Sequence[Path], patterns: Sequence[str],
                    prefer: Sequence[str] = ()) -> list[Path]:
    """Rank search hits deterministically: preferred folder, then exact name, then root.

    Without this the notebook could silently open a *sample* copy of the full
    parquet (an identical filename under a scratch folder such as
    ``bdb27_run/artefacts/``) instead of the FULL run. The chosen file and every
    candidate are printed, so a wrong pick is visible rather than silent.
    """
    import fnmatch

    def key(path: Path) -> tuple:
        posix = path.as_posix()
        prefer_rank = next((i for i, token in enumerate(prefer) if token in posix),
                           len(prefer))
        name_rank = next((i for i, pat in enumerate(patterns)
                          if fnmatch.fnmatch(path.name, pat)), len(patterns))
        return (prefer_rank, name_rank, len(path.parts), posix)

    return sorted(matches, key=key)


def resolve_input(value, *, label: str, patterns: Sequence[str],
                  roots: Sequence[Path], hint: str,
                  prefer: Sequence[str] = ()) -> dict:
    """Resolve one required input and report every candidate considered."""
    if value is not None:
        path = Path(value).expanduser()
        if path.is_dir():
            found = sorted(p for p in path.iterdir()
                           if p.is_file() and any(p.match(pat) for pat in patterns))
            if found:
                return {"chosen": found[0], "candidates": [str(p) for p in found],
                        "how": "explicit folder"}
            raise FileNotFoundError(
                f"{label}: no file matching {list(patterns)} in {path}\n  {hint}")
        if path.exists():
            return {"chosen": path, "candidates": [str(path)],
                    "how": "explicit path"}
        raise FileNotFoundError(f"{label}: {path} does not exist\n  {hint}")
    matches, tried = search_for(patterns, roots)
    ranked = rank_candidates(matches, patterns, prefer)
    if ranked:
        return {"chosen": ranked[0], "candidates": [str(p) for p in ranked],
                "how": "search (preferred folder, then exact name, then depth)"}
    listing = "\n  ".join(tried[:40])
    raise FileNotFoundError(
        f"{label}: not found. Looked for {list(patterns)} under:\n  {listing}\n"
        f"  {hint}")


def require_input(value, *, label: str, patterns: Sequence[str],
                  roots: Sequence[Path], hint: str,
                  prefer: Sequence[str] = ()) -> Path:
    """Resolve one required input, naming **every** path tried when it is missing.

    This is one of the three legitimate failures in the spec (§6): a required file
    that cannot be found. Nothing about a file's *contents* ever lands here.
    """
    return resolve_input(value, label=label, patterns=patterns, roots=roots,
                         hint=hint, prefer=prefer)["chosen"]
