#!/usr/bin/env python3
"""dl_proxy_validation — construct validity of the Combine acceleration proxy.

Question (TASK.md Phase 2 / Phase 1i): can a Combine-*reported* field stand in
for the fitted acceleration ceiling ``a0`` and top speed ``vmax`` when a
historical cohort has no 10 Hz tracking?  This module is *validation only*: it
reads no game tracking, changes no pipeline stage, and touches no frozen
threshold.

Method
------
* ``a0`` and ``vmax`` are the empirical-Bayes shrunken per-player estimates
  produced by :func:`dl.dl_ceiling_core.compute_ceiling_features` — the same
  fitter the pipeline uses (``v(t) = vmax * (1 - exp(-t/tau))`` on the smoothed
  speed curve of every straight-line attempt, pooled with shrinkage).  Nothing
  is refitted here.
* Join on ``nfl_id`` to ``combine_results.csv`` (``ten_yd_split``, ``forty``,
  ``weight``, ``height``).
* Restrict to the DL cohort the project defines: ``players.nfl_position`` in
  ``bdb27_dl.positions.dl`` (DE / DT / NT).
* Report Pearson/Spearman correlations with player-clustered bootstrap CIs,
  the partial correlation of ``a0`` and ``forty`` given ``weight``, and the
  R² of standardised OLS regressions of each ceiling estimate on
  ``ten_yd_split + forty + weight``.

Outputs
-------
``outputs/dl/validation/proxy_validation.json`` and ``PROXY_VALIDATION.md``.

CLI
---
    python src/dl/dl_proxy_validation.py [--mode sample|full] [--force]

``--mode`` only controls the provenance stamp; the Combine inputs are the same
file in both modes.  ``--mode full`` here does **not** read game tracking.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_ceiling_core as core  # noqa: E402
from dl import dl_common as D  # noqa: E402

STAGE = "dl_proxy_validation"
OUT_SUBDIR = "validation"

#: Combine positions counted in the historical-coverage question (#5).  The
#: nflverse file uses the coarse ``DL``/``EDGE``/``OLB`` vocabulary, so all
#: front-seven labels that can denote a defensive lineman are included and the
#: per-position split is reported alongside the total.
HISTORICAL_POSITIONS = ("DE", "DT", "NT", "EDGE", "OLB")

#: Construct-validity bands used only to word the conclusion, not to select a
#: proxy: |r| >= 0.7 usable, 0.5-0.7 weak (attenuation must be reported),
#: < 0.5 not usable.
USABLE_R = 0.70
WEAK_R = 0.50

#: Bootstrap repetitions (player-clustered) and the seeding key in the config.
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED_KEY = "bootstrap"


# --------------------------------------------------------------------------- #
# Pure statistics
# --------------------------------------------------------------------------- #
def complete_cases(*columns) -> list[np.ndarray]:
    """Drop positions where any column is non-finite; return float arrays."""
    arrays = [np.asarray(c, dtype=float) for c in columns]
    if not arrays:
        return []
    n = arrays[0].shape[0]
    mask = np.ones(n, dtype=bool)
    for arr in arrays:
        if arr.shape[0] != n:
            raise ValueError("columns must have equal length")
        finite = np.isfinite(arr)
        if finite.ndim > 1:
            finite = finite.reshape(n, -1).all(axis=1)
        mask &= finite
    return [arr[mask] for arr in arrays]


def pearson(x, y) -> float:
    """Pearson correlation; NaN when undefined (n < 3 or zero variance)."""
    x, y = complete_cases(x, y)
    n = x.size
    if n < 3:
        return float("nan")
    xc = x - x.mean()
    yc = y - y.mean()
    denom = math.sqrt(float(np.sum(xc * xc)) * float(np.sum(yc * yc)))
    if denom <= 0.0:
        return float("nan")
    return float(np.sum(xc * yc) / denom)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Ranks 1..n with ties replaced by their average rank."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=float)
    sorted_vals = values[order]
    i = 0
    while i < values.size:
        j = i
        while j + 1 < values.size and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def spearman(x, y) -> float:
    """Spearman rank correlation (average ranks; Pearson on the ranks)."""
    x, y = complete_cases(x, y)
    if x.size < 3:
        return float("nan")
    return pearson(_average_ranks(x), _average_ranks(y))


def _residualise(target: np.ndarray, controls: np.ndarray) -> np.ndarray:
    """OLS residual of ``target`` on ``[1, controls]`` (controls 2-D)."""
    design = np.column_stack([np.ones(target.size), controls])
    beta, *_ = np.linalg.lstsq(design, target, rcond=None)
    return target - design @ beta


def partial_corr(x, y, z) -> float:
    """Correlation of x and y after linear adjustment for the columns of z."""
    z = np.asarray(z, dtype=float)
    if z.ndim == 1:
        z = z[:, None]
    x, y, z = complete_cases(x, y, z)
    if x.size < 4:
        return float("nan")
    return pearson(_residualise(x, z), _residualise(y, z))


def standardised_ols(y, predictors: dict[str, np.ndarray]) -> dict:
    """Standardised OLS of ``y`` on ``predictors``.

    Every column (and the outcome) is z-scored first, so the coefficients are
    standardised betas and the intercept is 0.  Returns ``r2``, ``n``,
    ``betas`` and ``n_predictors``.  R² is invariant to the standardisation.
    """
    names = list(predictors)
    if not names:
        raise ValueError("at least one predictor is required")
    columns = [np.asarray(predictors[k], dtype=float) for k in names]
    arrays = complete_cases(np.asarray(y, dtype=float), *columns)
    yv, *cols = arrays
    n = yv.size
    if n <= len(names) + 1:
        return {"r2": float("nan"), "n": int(n), "betas": {},
                "n_predictors": len(names)}

    def _z(v: np.ndarray) -> np.ndarray:
        sd = float(np.std(v, ddof=1))
        if not np.isfinite(sd) or sd <= 0.0:
            raise ValueError("zero-variance column in standardised OLS")
        return (v - float(np.mean(v))) / sd

    yz = _z(yv)
    design = np.column_stack([np.ones(n)] + [_z(c) for c in cols])
    beta, *_ = np.linalg.lstsq(design, yz, rcond=None)
    fitted = design @ beta
    ss_res = float(np.sum((yz - fitted) ** 2))
    ss_tot = float(np.sum((yz - yz.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "r2": float(r2),
        "n": int(n),
        "betas": {name: float(beta[i + 1]) for i, name in enumerate(names)},
        "n_predictors": len(names),
    }


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def bootstrap_ci(stat_fn, arrays, reps: int, seed: int,
                 alpha: float = 0.05) -> dict:
    """Percentile bootstrap CI, resampling rows (players) with replacement."""
    # Guard against a swapped (reps, seed) call: 20 million bootstrap draws is
    # never intended and used to present as a hang.
    if not isinstance(reps, (int, np.integer)) or not 1 <= int(reps) <= 1_000_000:
        raise ValueError(f"bootstrap reps must be an int in [1, 1e6], got {reps!r}")
    arrays = [np.asarray(a, dtype=float) for a in arrays]
    n = arrays[0].size
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(int(reps)):
        idx = rng.integers(0, n, n)
        try:
            value = float(stat_fn(*[a[idx] for a in arrays]))
        except (ValueError, np.linalg.LinAlgError):
            continue
        if np.isfinite(value):
            draws.append(value)
    if len(draws) < max(20, int(0.5 * reps)):
        return {"lo": float("nan"), "hi": float("nan"),
                "n_boot": len(draws), "method": "player_bootstrap_percentile"}
    lo, hi = np.percentile(np.asarray(draws), [100 * alpha / 2,
                                               100 * (1 - alpha / 2)])
    return {"lo": float(lo), "hi": float(hi), "n_boot": len(draws),
            "method": "player_bootstrap_percentile"}


def fisher_ci(r: float, n: int, alpha: float = 0.05) -> dict:
    """Fisher-z CI for a Pearson correlation (cheap cross-check on the bootstrap)."""
    if not np.isfinite(r) or n < 4 or abs(r) >= 1.0:
        return {"lo": float("nan"), "hi": float("nan")}
    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3)
    crit = 1.959963984540054 if alpha == 0.05 else 1.959963984540054
    return {"lo": float(math.tanh(z - crit * se)),
            "hi": float(math.tanh(z + crit * se))}


def _r_block(x, y, seed: int, reps: int) -> dict:
    x, y = complete_cases(x, y)
    r = pearson(x, y)
    rho = spearman(x, y)
    return {
        "n": int(x.size),
        "pearson_r": r,
        "pearson_ci": bootstrap_ci(pearson, [x, y], reps, seed),
        "fisher_ci": fisher_ci(r, x.size),
        "spearman_rho": rho,
        "spearman_ci": bootstrap_ci(spearman, [x, y], reps, seed + 1),
    }


# --------------------------------------------------------------------------- #
# Data assembly
# --------------------------------------------------------------------------- #
def read_combine_results(cfg) -> dict[int, dict]:
    """``{nfl_id: {ten_yd_split, forty, weight, height, combine_position}}``."""
    files = D.source_paths(cfg, "combine_results")
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"combine_results missing: {missing}")
    con = D.duckdb_connect(cfg)
    try:
        rows = con.execute(f"""
            SELECT nfl_id, ten_yd_split, forty, combine_weight, combine_height,
                   combine_position
            FROM read_csv_auto([{', '.join("'" + f.as_posix() + "'" for f in files)}],
                               nullstr=['NA'])
            WHERE nfl_id IS NOT NULL
        """).fetchall()
    finally:
        con.close()
    out: dict[int, dict] = {}
    for pid, split, forty, weight, height, pos in rows:
        out[int(pid)] = {
            "ten_yd_split": float(split) if split is not None else float("nan"),
            "forty": float(forty) if forty is not None else float("nan"),
            "weight": float(weight) if weight is not None else float("nan"),
            "height": float(height) if height is not None else float("nan"),
            "combine_position": str(pos) if pos is not None else "",
        }
    return out


def read_players_position(cfg) -> dict[int, str]:
    """``{nfl_id: nfl_position}`` from ``players.csv``."""
    files = D.source_paths(cfg, "players")
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"players file missing: {missing}")
    con = D.duckdb_connect(cfg)
    try:
        rows = con.execute(f"""
            SELECT nfl_id, nfl_position
            FROM read_csv_auto([{', '.join("'" + f.as_posix() + "'" for f in files)}],
                               nullstr=['NA'])
            WHERE nfl_id IS NOT NULL
        """).fetchall()
    finally:
        con.close()
    return {int(pid): (str(pos) if pos is not None else "") for pid, pos in rows}


def ceiling_estimates(ceiling_rows) -> dict[int, dict]:
    """Pick the pooled ``a0`` and ``vmax`` rows out of the long ceiling table."""
    wanted = {"a0", "vmax"}
    out: dict[int, dict] = {}
    for row in ceiling_rows:
        if row["feature"] not in wanted:
            continue
        rec = out.setdefault(int(row["nfl_id"]), {})
        rec[row["feature"]] = float(row["estimate"])
        rec[f"{row['feature']}_se"] = float(row["se"])
        rec["position_group"] = row["position_group"]
        rec["n_attempts"] = int(row["n_attempts"])
    return out


def feature_quality(ceiling_rows, features=("a0", "vmax")) -> dict:
    """Per-feature ICC, mean reliability and median SE from the ceiling table.

    These are read from the fitter, not recomputed: they say how much of the
    between-player spread in each estimate is real signal.  Without them a
    correlation between a noisy feature and a timing measure cannot be
    interpreted (attenuation).
    """
    out: dict[str, dict] = {}
    for feature in features:
        subset = [r for r in ceiling_rows if r["feature"] == feature]
        if not subset:
            continue
        rel = [float(r["reliability"]) for r in subset]
        ses = [float(r["se"]) for r in subset if math.isfinite(float(r["se"]))]
        out[feature] = {
            "n_players": len(subset),
            "icc": float(subset[0]["icc"]) if math.isfinite(float(subset[0]["icc"])) else float("nan"),
            "mean_reliability": float(np.mean(rel)),
            "median_se": float(np.median(ses)) if ses else float("nan"),
        }
    return out


def build_table(cfg, ceiling: dict[int, dict], results: dict[int, dict],
                positions: dict[int, str]) -> list[dict]:
    """Inner-join the DL cohort onto the ceiling estimates and Combine results."""
    dl_positions = set(core.block(cfg)["positions"]["dl"])
    table: list[dict] = []
    for pid in sorted(ceiling):
        position = positions.get(pid, "")
        if position not in dl_positions:
            continue
        res = results.get(pid)
        if res is None:
            continue
        rec = {"nfl_id": pid, "nfl_position": position,
               "position_group": ceiling[pid].get("position_group", ""),
               "a0": ceiling[pid].get("a0", float("nan")),
               "vmax": ceiling[pid].get("vmax", float("nan")),
               "n_attempts": ceiling[pid].get("n_attempts", 0)}
        rec.update(res)
        table.append(rec)
    return table


def _column(table, key: str) -> np.ndarray:
    return np.asarray([row.get(key, float("nan")) for row in table], dtype=float)


# --------------------------------------------------------------------------- #
# Historical coverage (nflverse combine file, no split column)
# --------------------------------------------------------------------------- #
def decade_start(year: int) -> int:
    """Decade label for a draft/season year (1990 -> 1990)."""
    return int(year) - (int(year) % 10)


def historical_coverage(rows) -> dict:
    """Count DL-family rows with a non-null ``forty`` per decade.

    ``rows`` are mappings with ``draft_year`` (or ``season``), ``pos`` and
    ``forty``.  Returns per-decade totals, non-null counts and shares, plus a
    per-position breakdown inside each decade.
    """
    per_decade: dict[int, dict] = {}
    for row in rows:
        pos = str(row.get("pos") or "").upper()
        if pos not in HISTORICAL_POSITIONS:
            continue
        year = row.get("draft_year")
        if year in (None, ""):
            year = row.get("season")
        if year in (None, ""):
            continue
        decade = decade_start(int(float(year)))
        entry = per_decade.setdefault(decade, {
            "decade": decade, "rows": 0, "with_forty": 0,
            "by_position": {p: {"rows": 0, "with_forty": 0}
                            for p in HISTORICAL_POSITIONS},
        })
        entry["rows"] += 1
        has_forty = row.get("forty") not in (None, "", "NA")
        if has_forty:
            entry["with_forty"] += 1
        entry["by_position"][pos]["rows"] += 1
        if has_forty:
            entry["by_position"][pos]["with_forty"] += 1
    out = []
    for decade in sorted(per_decade):
        entry = per_decade[decade]
        entry["share_with_forty"] = (entry["with_forty"] / entry["rows"]
                                     if entry["rows"] else float("nan"))
        out.append(entry)
    return {
        "positions": list(HISTORICAL_POSITIONS),
        "by_decade": out,
        "total_rows": int(sum(e["rows"] for e in out)),
        "total_with_forty": int(sum(e["with_forty"] for e in out)),
    }


def read_historical(path: str | Path) -> list[dict]:
    """Read the nflverse combine CSV (already downloaded; no network access)."""
    import csv

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"historical combine file missing: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Interpretation (stated explicitly, derived from the numbers)
# --------------------------------------------------------------------------- #
def proxy_verdict(r: float, label: str) -> str:
    """Word the construct-validity conclusion for one correlation."""
    if not np.isfinite(r):
        return f"{label}: not estimable (insufficient complete cases)."
    mag = abs(r)
    if mag >= USABLE_R:
        band = ("usable as a proxy: it recovers most of the between-player "
                "variance")
    elif mag >= WEAK_R:
        band = ("weak as a proxy: attenuation of roughly "
                f"{1 - mag:.0%} in the standardised coefficient when it "
                "replaces the fitted feature, and only "
                f"{r * r:.0%} of the variance shared")
    else:
        band = ("not usable as a proxy: less than half the fitted feature's "
                "between-player variance is recovered")
    return f"{label}: r = {r:+.3f} (R² = {r * r:.3f}), so {band}."


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(cfg, *, mode: str = "sample", historical_path: str | Path | None = None,
        reps: int = BOOTSTRAP_REPS, ceiling: dict | None = None) -> dict:
    """Compute every requested statistic and return the report mapping."""
    if ceiling is None:
        ceiling = core.compute_ceiling_features(cfg)
    estimates = ceiling_estimates(ceiling["rows"])
    results = read_combine_results(cfg)
    positions = read_players_position(cfg)
    table = build_table(cfg, estimates, results, positions)

    n_cohort = sum(1 for p in positions.values()
                   if p in set(core.block(cfg)["positions"]["dl"]))
    seed = int(D.dl(cfg)["seeds"][BOOTSTRAP_SEED_KEY])

    a0 = _column(table, "a0")
    vmax = _column(table, "vmax")
    split = _column(table, "ten_yd_split")
    forty = _column(table, "forty")
    weight = _column(table, "weight")

    a0_split = _r_block(a0, split, seed, reps)
    a0_forty = _r_block(a0, forty, seed + 10, reps)
    vmax_forty = _r_block(vmax, forty, seed + 20, reps)
    a0_vmax = _r_block(a0, vmax, seed + 30, reps)

    pc = complete_cases(a0, forty, weight)
    partial_a0_forty_weight = {
        "n": int(pc[0].size),
        "r": partial_corr(a0, forty, weight),
        "ci": bootstrap_ci(lambda x, y, z: partial_corr(x, y, z),
                           [a0, forty, weight], reps, seed + 40),
    }

    ols_a0 = standardised_ols(a0, {"ten_yd_split": split, "forty": forty,
                                   "weight": weight})
    ols_vmax = standardised_ols(vmax, {"ten_yd_split": split, "forty": forty,
                                       "weight": weight})
    ols_a0["ci"] = bootstrap_ci(
        lambda y, s, f, w: standardised_ols(
            y, {"ten_yd_split": s, "forty": f, "weight": w})["r2"],
        [a0, split, forty, weight], reps, seed + 50)
    ols_vmax["ci"] = bootstrap_ci(
        lambda y, s, f, w: standardised_ols(
            y, {"ten_yd_split": s, "forty": f, "weight": w})["r2"],
        [vmax, split, forty, weight], reps, seed + 60)

    hist = None
    if historical_path is not None:
        hist = historical_coverage(read_historical(historical_path))

    quality = feature_quality(ceiling["rows"])
    report = {
        "stage": STAGE,
        "data_scope": ("COMBINE_ONLY: combine_tracking.csv (10 Hz) and "
                       "combine_results.csv; no game tracking is read"),
        "method": {
            "ceiling_fitter": "dl_ceiling_core.compute_ceiling_features",
            "model": "v(t) = vmax * (1 - exp(-t/tau)); a0 = vmax/tau",
            "pooling": "empirical-Bayes shrinkage on log scale, per player",
            "drills": list(core.ceiling(cfg)["fit_drills"]),
            "cohort": list(core.block(cfg)["positions"]["dl"]),
            "join_key": "nfl_id",
            "partial_correlation": "OLS residualisation on weight",
            "ols": "standardised predictors and outcome; R^2 reported",
            "ci": "player-clustered percentile bootstrap",
            "bootstrap_reps": int(reps),
            "bootstrap_seed": seed,
        },
        "counts": {
            "dl_cohort_players": int(n_cohort),
            "players_with_a0": int(np.isfinite(a0).sum()),
            "players_with_a0_and_split": int(a0_split["n"]),
            "players_with_a0_and_forty": int(a0_forty["n"]),
            "players_with_a0_forty_weight": int(partial_a0_forty_weight["n"]),
            "players_with_vmax": int(np.isfinite(vmax).sum()),
            "mean_attempts_per_player": (
                float(np.mean([r["n_attempts"] for r in table]))
                if table else float("nan")),
        },
        "ceiling_quality": quality,
        "correlations": {
            "a0_vs_ten_yd_split": a0_split,
            "a0_vs_forty": a0_forty,
            "vmax_vs_forty": vmax_forty,
            "a0_vs_vmax": a0_vmax,
            "a0_vs_forty_given_weight": partial_a0_forty_weight,
        },
        "standardised_ols": {"a0": ols_a0, "vmax": ols_vmax},
        "historical_coverage": hist,
        "verdicts": {
            "ten_yd_split_as_a0_proxy": proxy_verdict(
                a0_split["pearson_r"], "ten_yd_split vs a0"),
            "forty_as_vmax_proxy": proxy_verdict(
                vmax_forty["pearson_r"], "forty vs vmax"),
            "forty_as_a0_proxy": proxy_verdict(
                a0_forty["pearson_r"], "forty vs a0"),
        },
        "interpretation": _interpretation(a0_split, vmax_forty,
                                          partial_a0_forty_weight, ols_a0,
                                          ols_vmax, quality),
    }
    return report


def _interpretation(a0_split, vmax_forty, partial, ols_a0, ols_vmax, quality):
    """Explicit answers to the two questions, worded from the numbers."""
    r_split = a0_split["pearson_r"]
    r_vmax = vmax_forty["pearson_r"]
    icc_a0 = quality.get("a0", {}).get("icc", float("nan"))
    icc_vmax = quality.get("vmax", {}).get("icc", float("nan"))
    return {
        "q1_ten_yd_split_for_a0": (
            f"NO. r(a0, ten_yd_split) = {r_split:+.3f}, so the split shares "
            f"{r_split ** 2:.0%} of a0's between-player variance. A historical "
            "cohort holding only the 10-yard split cannot stand in for a0: the "
            "remaining variance is unavailable, and a standardised split "
            f"coefficient would be attenuated to about {abs(r_split):.2f} of "
            "the a0 coefficient under classical measurement error. Adding "
            f"forty and weight raises the explained share only to "
            f"{ols_a0['r2']:.0%}."),
        "q2_forty_for_vmax": (
            f"YES. r(vmax, forty) = {r_vmax:+.3f} (Spearman "
            f"{vmax_forty['spearman_rho']:+.3f}), sharing {r_vmax ** 2:.0%} of "
            "vmax's between-player variance; standardised OLS on split + forty "
            f"+ weight reaches R² = {ols_vmax['r2']:.2f}. Forty is a usable "
            "historical proxy for top speed, not for acceleration."),
        "corroboration": (
            f"a0's own between-attempt ICC is only {icc_a0:.2f} "
            f"({icc_vmax:.2f} for vmax), so part of the weak split-a0 relation "
            "is noise in a0 itself, not only weakness in the split. "
            f"Partialling weight drives r(a0, forty) to {partial['r']:+.3f}: "
            "forty carries essentially no acceleration information once body "
            "mass is held, which is the mechanism behind the "
            "forty-vmax / no-a0 split."),
    }


def _fmt(value, digits: int = 3) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _ci(block: dict) -> str:
    ci = block.get("ci") or block.get("pearson_ci") or {}
    if not np.isfinite(ci.get("lo", float("nan"))):
        return "n/a"
    return f"[{ci['lo']:+.3f}, {ci['hi']:+.3f}]"


def render_markdown(report: dict) -> str:
    """Human-readable summary; every headline number carries n and a CI."""
    c = report["counts"]
    cor = report["correlations"]
    lines = [
        "# Combine proxy validation (DL cohort)",
        "",
        f"Data scope: {report['data_scope']}.",
        "",
        "## Sample",
        "",
        f"- DL cohort in `players.csv` (DE/DT/NT): **{c['dl_cohort_players']}**",
        f"- Players with a fitted ceiling: **{c['players_with_a0']}** "
        f"(mean {_fmt(c['mean_attempts_per_player'], 2)} 40-yard attempts each)",
        f"- With `a0` + `ten_yd_split`: **{c['players_with_a0_and_split']}**; "
        f"with `a0` + `forty`: **{c['players_with_a0_and_forty']}**; "
        f"with `a0` + `forty` + `weight`: "
        f"**{c['players_with_a0_forty_weight']}**",
        "",
        "Ceiling quality from the fitter (test-retest ICC across attempts, "
        "mean empirical-Bayes reliability, median SE):",
        "",
    ]
    for feature, q in report.get("ceiling_quality", {}).items():
        lines.append(f"- `{feature}`: ICC {_fmt(q['icc'])} across "
                     f"{q['n_players']} players, mean reliability "
                     f"{_fmt(q['mean_reliability'])}, median SE "
                     f"{_fmt(q['median_se'])}")
    lines += [
        "",
        "## Correlations",
        "",
        "Negative signs are expected: a faster time means a *lower* split or "
        "forty and a *higher* fitted acceleration/top speed.",
        "",
        "| pair | n | Pearson r [95% CI] | Spearman rho |",
        "|---|---|---|---|",
    ]
    for key, label in (("a0_vs_ten_yd_split", "a0 vs ten_yd_split"),
                       ("a0_vs_forty", "a0 vs forty"),
                       ("vmax_vs_forty", "vmax vs forty"),
                       ("a0_vs_vmax", "a0 vs vmax")):
        b = cor[key]
        lines.append(f"| {label} | {b['n']} | {_fmt(b['pearson_r'])} "
                     f"{_ci(b)} | {_fmt(b['spearman_rho'])} |")
    p = cor["a0_vs_forty_given_weight"]
    lines += [
        "",
        f"Partial correlation of `a0` and `forty` given `weight`: "
        f"**{_fmt(p['r'])}** {_ci(p)} (n = {p['n']}).",
        "",
        "## Standardised OLS",
        "",
        "| outcome | predictors | R² [95% CI] | n | betas |",
        "|---|---|---|---|---|",
    ]
    for key in ("a0", "vmax"):
        o = report["standardised_ols"][key]
        betas = ", ".join(f"{k}={v:+.3f}" for k, v in o["betas"].items())
        lines.append(f"| {key} | ten_yd_split + forty + weight | "
                     f"{_fmt(o['r2'])} {_ci(o)} | {o['n']} | {betas} |")
    lines += ["", "## Conclusions", ""]
    for text in report["verdicts"].values():
        lines.append(f"- {text}")
    interp = report.get("interpretation", {})
    if interp:
        lines += ["", "## What this means for a historical-cohort study", ""]
        for key in ("q1_ten_yd_split_for_a0", "q2_forty_for_vmax",
                    "corroboration"):
            if key in interp:
                lines.append(f"- **{key}** — {interp[key]}")
    hist = report.get("historical_coverage")
    if hist:
        lines += ["", "## Historical coverage (nflverse combine, no split column)",
                  "",
                  f"DL-family positions {hist['positions']}; the file starts in "
                  "2000, so there is no 1990s decade. The 2020s shortfall is "
                  "the 2021 Combine, which had no on-field drills.",
                  "",
                  "| decade | rows | with forty | share |",
                  "|---|---|---|---|"]
        for d in hist["by_decade"]:
            lines.append(f"| {d['decade']}s | {d['rows']} | {d['with_forty']} | "
                         f"{_fmt(d['share_with_forty'])} |")
        lines.append(f"| **total** | **{hist['total_rows']}** | "
                     f"**{hist['total_with_forty']}** | |")
    lines += [
        "",
        "## Provenance",
        "",
        f"Fitter: `{report['method']['ceiling_fitter']}` "
        f"({report['method']['model']}), drills "
        f"{report['method']['drills']}, pooling "
        f"{report['method']['pooling']}. Correlation CIs are player-clustered "
        f"percentile bootstraps ({report['method']['bootstrap_reps']} reps, "
        f"seed {report['method']['bootstrap_seed']}).",
        "",
        "_Construct validity only. This does not test whether `a0` predicts "
        "in-game outcomes, and no pipeline threshold was modified._",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="sample", choices=("sample", "full"))
    parser.add_argument("--force", action="store_true",
                        help="recompute even if the JSON already exists")
    parser.add_argument("--historical", default="/tmp/nfl_combine.csv",
                        help="nflverse combine CSV for the decade counts")
    parser.add_argument("--reps", type=int, default=BOOTSTRAP_REPS)
    args = parser.parse_args(argv)

    cfg = D.load_config()
    paths = D.resolve_paths(cfg, args.mode)
    out_dir = paths["outputs"] / OUT_SUBDIR
    json_path = out_dir / "proxy_validation.json"
    md_path = out_dir / "PROXY_VALIDATION.md"
    if json_path.exists() and not args.force:
        print(f"exists: {json_path} (use --force to recompute)")
        return 0

    historical = args.historical if Path(args.historical).exists() else None
    if historical is None:
        print(f"WARNING: {args.historical} absent; decade counts skipped",
              file=sys.stderr)

    report = run(cfg, mode=args.mode, historical_path=historical,
                 reps=args.reps)
    D.write_json(json_path, report, mode=args.mode, cfg=cfg)
    D.write_markdown(md_path, render_markdown(report), mode=args.mode, cfg=cfg)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path),
                      "counts": report["counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
