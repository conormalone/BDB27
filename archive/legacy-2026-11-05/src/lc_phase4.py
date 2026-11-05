"""lc_phase4 — the remaining TASK.md Phase 4 analyses (D67).

Everything here reads the *already written* effort table (``04_efforts``) plus,
where an analysis genuinely needs it, the per-player Combine ceiling table
(``02_combine_ceiling``) and the players table (for position).  Nothing here
re-derives route depth, leverage, thresholds or the primary specification.

The six analyses, exactly as TASK.md Phase 4 lists them:

1. **Matched design (sensitivity).** Within each player, coarsened exact
   matching of high- vs low-leverage efforts on route family, depth bin,
   quarter and load bin, compared with a player-clustered bootstrap.
2. **Headroom moderation (secondary).** ``leverage x baseline utilization``,
   where utilisation is the player's median effort fraction on an *independent
   half* of that player's low-leverage efforts (the split is fixed by seed and
   logged).  Also ``leverage x vmax`` and ``leverage x tau``.  Both a naive and
   an errors-in-variables corrected estimate are reported.
3. **Ceiling bound check.** Predicted effort fraction under high leverage must
   not exceed the plausible ceiling; exceedance rates are reported by leverage
   bin.
4. **Dose-response.** The effect across leverage bins, with an explicit
   monotonicity statement.
5. **Per-player leverage slopes.** Reported only when model-based reliability
   clears the configured threshold.  No odd/even split-half is used.
6. **TE replication.** The primary specification on TE, labelled exploratory
   when the sample is small — with the n it is labelled on.

Honesty rules enforced here rather than in prose:

* a required field that is absent is reported as **NOT RUN** with the exact
  field name — never proxied from post-snap data;
* a matched sample that collapses is reported as a finding about the design,
  not patched into a result;
* every estimate carries its n and its player count, and every correction is
  named in the output row.

No design constant lives in this module: cut edges, seeds, thresholds and
tolerances all come from ``config.yaml`` under ``bdb27_lc.phase4``.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

import lc_common as C
import lc_models as M

#: Columns every effort row already carries (04_efforts).  Used only to give a
#: clear error instead of a ``KeyError`` deep inside pandas.
EFFORT_REQUIRED = ("nfl_id", "log_max_speed", "leverage_z", "leverage_bin",
                   "max_speed_yds")

#: The correction string written next to every corrected estimate, so a reader
#: never has to guess what "corrected" meant.
EIV_CORRECTION = (
    "classical errors-in-variables (regression dilution) correction: "
    "lambda = 1 - mean(SE^2)/var(moderator), corrected = naive / lambda; "
    "first-order and approximate, applied to the interaction coefficient and "
    "its clustered SE alike")

DEFENDER_FIELD_DEFAULT = "defender_distance_yd"


# --------------------------------------------------------------------------- #
# Config access — a missing block raises instead of silently producing nothing
# --------------------------------------------------------------------------- #
def block(cfg: Mapping) -> dict:
    """The ``bdb27_lc.phase4`` block; a missing/empty block is a config error."""
    try:
        value = C.lc(cfg)["phase4"]
    except (KeyError, TypeError) as exc:  # pragma: no cover - config guard
        raise RuntimeError(
            "config bdb27_lc.phase4 block is missing; the stage would otherwise "
            "run nothing and call it a success (the D66 defect)") from exc
    if not isinstance(value, Mapping) or not value:
        raise RuntimeError("config bdb27_lc.phase4 block is missing or empty")
    return dict(value)


def _need(mapping: Mapping, key: str, where: str):
    if key not in mapping:
        raise RuntimeError(f"config bdb27_lc.phase4.{where}.{key} is not declared")
    return mapping[key]


def _seed(cfg: Mapping, key: str) -> int:
    seeds = C.lc(cfg)["seeds"]
    if key not in seeds:
        raise RuntimeError(f"config bdb27_lc.seeds.{key} is not declared")
    return int(seeds[key])


def _numeric(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


# --------------------------------------------------------------------------- #
# Input enrichment (position + ceiling) — done by the stage, tolerated absent
# --------------------------------------------------------------------------- #
def ceiling_table(cfg: Mapping, mode: str) -> pd.DataFrame:
    """Wide per-player ceiling table: ``nfl_id, vmax, vmax_se, tau, tau_se``.

    Read from the long-format ``player_ceiling.csv`` written by
    ``02_combine_ceiling``.  Missing file -> empty frame (the caller reports the
    dependent checks as NOT RUN); it is never imputed.
    """
    block_cfg = block(cfg)
    sub = C.lc(cfg)["ceiling"].get("out_subdir", "ceiling/lc")
    name = block_cfg.get("ceiling_file", "player_ceiling.csv")
    path = C.resolve_paths(cfg, mode)["outputs"] / sub / name
    columns = ["nfl_id", "vmax", "vmax_se", "tau", "tau_se"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    long = pd.read_csv(path, low_memory=False)
    if not {"nfl_id", "feature", "estimate"} <= set(long.columns):
        return pd.DataFrame(columns=columns)
    long = long[long["feature"].isin(("vmax", "tau"))].copy()
    estimate = long.pivot_table(index="nfl_id", columns="feature",
                                values="estimate", aggfunc="first")
    se = (long.pivot_table(index="nfl_id", columns="feature", values="se",
                           aggfunc="first")
          if "se" in long.columns else estimate * np.nan)
    out = pd.DataFrame(index=estimate.index)
    for feature in ("vmax", "tau"):
        out[feature] = estimate[feature] if feature in estimate.columns else np.nan
        out[f"{feature}_se"] = se[feature] if feature in se.columns else np.nan
    return out.reset_index()[columns]


def position_table(cfg: Mapping, mode: str) -> pd.DataFrame:
    """``nfl_id, position`` from the players parquet mirror (read-only join)."""
    block_cfg = block(cfg)
    name = block_cfg.get("players_parquet", "lc_players")
    path = C.resolve_paths(cfg, mode)["parquet"] / f"{name}.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["nfl_id", "position"])
    players = pd.read_parquet(path, columns=["nfl_id", "nfl_position"])
    return players.rename(columns={"nfl_position": "position"})


def enrich(table: pd.DataFrame, cfg: Mapping, mode: str) -> tuple[pd.DataFrame, dict]:
    """Attach position and ceiling, and derive the effort fraction.

    Every attachment is a left join on ``nfl_id``; players without a ceiling
    keep NaN rather than a substitute, and the counts are returned so the stage
    can report ceiling coverage honestly.
    """
    work = table.copy()
    work["nfl_id"] = work["nfl_id"].astype(str)
    meta: dict[str, Any] = {}

    if "position" not in work.columns:
        positions = position_table(cfg, mode)
        if len(positions):
            positions["nfl_id"] = positions["nfl_id"].astype(str)
            work = work.merge(positions, on="nfl_id", how="left")
            meta["position_source"] = "players parquet mirror (nfl_position)"
        else:
            meta["position_source"] = None
    else:
        meta["position_source"] = "column already present"

    if "vmax" not in work.columns:
        ceiling = ceiling_table(cfg, mode)
        if len(ceiling):
            ceiling["nfl_id"] = ceiling["nfl_id"].astype(str)
            work = work.merge(ceiling, on="nfl_id", how="left")
            meta["ceiling_source"] = "02_combine_ceiling player_ceiling.csv"
        else:
            meta["ceiling_source"] = None
    else:
        meta["ceiling_source"] = "column already present"

    if "vmax" in work.columns:
        for column in ("vmax", "vmax_se", "tau", "tau_se"):
            if column not in work.columns:
                work[column] = np.nan
        work["effort_fraction"] = (
            _numeric(work["max_speed_yds"]) / _numeric(work["vmax"]))
    else:
        work["effort_fraction"] = np.nan

    meta["n_efforts"] = int(len(work))
    meta["n_players"] = int(work["nfl_id"].nunique()) if len(work) else 0
    meta["n_players_with_ceiling"] = int(
        work.loc[work["vmax"].notna(), "nfl_id"].nunique()) if "vmax" in work else 0
    meta["n_efforts_with_ceiling"] = int(work["vmax"].notna().sum())
    if "position" in work.columns:
        counts = work.groupby("position")["nfl_id"].nunique().to_dict()
        meta["positions"] = {str(k): int(v) for k, v in counts.items()}
    return work, meta


# --------------------------------------------------------------------------- #
# 1. Coarsened exact matching, high vs low leverage, within player
# --------------------------------------------------------------------------- #
def coarsen(table: pd.DataFrame, cfg: Mapping) -> pd.DataFrame:
    """Add the coarsening columns used as exact-match strata.

    ``depth_bin`` is a coarsened ``break_depth_yd``; a route with no detected
    break gets its own ``nobreak`` level rather than an imputed depth (D61:
    missing depth is never imputed).  ``load_bin`` coarsens ``prior_efforts``.
    """
    block_cfg = _need(block(cfg), "matched", "matched")
    edges = dict(_need(block_cfg, "coarsen", "matched") or {})
    depth_edges = [float(v) for v in _need(edges, "depth_bin_edges", "matched.coarsen")]
    load_edges = [float(v) for v in _need(edges, "load_bin_edges", "matched.coarsen")]

    work = table.copy()
    found = (work["break_found"].fillna(False).astype(bool)
             if "break_found" in work.columns
             else pd.Series(False, index=work.index))
    depth = _numeric(work.get("break_depth_yd", pd.Series(np.nan, index=work.index)))
    labels = pd.Series("nobreak", index=work.index, dtype=object)
    inside = found & depth.notna()
    if inside.any():
        bins = np.digitize(depth[inside].astype(float).to_numpy(),
                           depth_edges[1:-1], right=False)
        labels[inside] = [f"d{int(b)}" for b in bins]
    work["depth_bin"] = labels

    load = _numeric(work["prior_efforts"]).fillna(0.0)
    load_bins = np.digitize(load.to_numpy(dtype=float), load_edges[1:-1],
                            right=False)
    work["load_bin"] = [f"l{int(b)}" for b in load_bins]
    work["quarter_bin"] = _numeric(work["quarter"]).fillna(1).astype(int).astype(str)
    work["route_stratum"] = work["route_family"].astype(str)
    work["stratum"] = (work["route_stratum"] + "|" + work["depth_bin"].astype(str)
                       + "|" + work["quarter_bin"] + "|"
                       + work["load_bin"].astype(str))
    return work


def matched_design(table: pd.DataFrame, cfg: Mapping) -> dict:
    """CEM within player: pair high- and low-leverage efforts inside a stratum.

    Pairs are drawn inside each ``(player, stratum)`` cell; the pairing among
    interchangeable rows is a seeded random permutation, so the result is
    deterministic for a given seed but not tied to row order.  Unequal counts
    leave rows unmatched, which is counted rather than forced.
    """
    block_cfg = _need(block(cfg), "matched", "matched")
    treatment = str(_need(block_cfg, "treatment_bin", "matched"))
    control = str(_need(block_cfg, "control_bin", "matched"))
    min_pairs = int(_need(block_cfg, "min_pairs", "matched"))
    seed = _seed(cfg, str(block_cfg.get("seed_key", "bootstrap")))
    rng = np.random.default_rng(seed)

    work = coarsen(table, cfg)
    work["_row"] = np.arange(len(work))
    work["leverage_bin"] = work["leverage_bin"].astype(str)
    usable = work[work["leverage_bin"].isin((treatment, control))
                  & work["log_max_speed"].notna()].copy()

    pair_records: list[dict] = []
    cells = 0
    empty_cells = 0
    unmatched_t = 0
    unmatched_c = 0
    for (player, stratum), group in usable.groupby(["nfl_id", "stratum"],
                                                   sort=True):
        treated = group[group["leverage_bin"] == treatment]
        control_rows = group[group["leverage_bin"] == control]
        if len(treated) == 0 or len(control_rows) == 0:
            empty_cells += 1
            continue
        cells += 1
        k = int(min(len(treated), len(control_rows)))
        unmatched_t += int(len(treated) - k)
        unmatched_c += int(len(control_rows) - k)
        if k == 0:
            continue
        t_idx = rng.permutation(len(treated))[:k]
        c_idx = rng.permutation(len(control_rows))[:k]
        t_rows = treated.iloc[np.sort(t_idx)]
        c_rows = control_rows.iloc[np.sort(c_idx)]
        for (t_pos, a), (c_pos, b) in zip(t_rows.iterrows(), c_rows.iterrows()):
            pair_records.append({
                "nfl_id": str(player),
                "stratum": str(stratum),
                "route_family": str(a.get("route_stratum", "")),
                "depth_bin": str(a.get("depth_bin", "")),
                "quarter": str(a.get("quarter_bin", "")),
                "load_bin": str(a.get("load_bin", "")),
                "row_high": int(a["_row"]),
                "row_low": int(b["_row"]),
                "leverage_z_high": float(a["leverage_z"]),
                "leverage_z_low": float(b["leverage_z"]),
                "y_high": float(a["log_max_speed"]),
                "y_low": float(b["log_max_speed"]),
                "diff": float(a["log_max_speed"]) - float(b["log_max_speed"]),
            })

    pairs = pd.DataFrame(pair_records, columns=[
        "nfl_id", "stratum", "route_family", "depth_bin", "quarter", "load_bin",
        "row_high", "row_low", "leverage_z_high", "leverage_z_low", "y_high",
        "y_low", "diff"])
    n_pairs = int(len(pairs))
    n_players = int(pairs["nfl_id"].nunique()) if n_pairs else 0
    # Players with usable rows but no matched pair are named, never silently
    # dropped: a one-sided stratum (only high or only low efforts) cannot form a
    # within-player contrast, and that has to be visible next to the estimate.
    eligible_players = int(usable["nfl_id"].nunique()) if len(usable) else 0
    paired_players = set(pairs["nfl_id"].unique()) if n_pairs else set()
    without_pair = sorted(
        str(p) for p in usable["nfl_id"].astype(str).unique()
        if str(p) not in paired_players)
    collapsed = n_pairs < min_pairs or n_players == 0
    status = "collapsed" if collapsed else "ok"
    note = ("the matched sample collapsed: fewer than the configured minimum "
            "pairs survive exact matching within player. That is a finding "
            "about the design (rare high-leverage efforts in common strata), "
            "not a failed run."
            if collapsed else
            "matched pairs are within-player, within-stratum high-minus-low "
            "differences in log max speed")
    return {
        "status": status,
        "pairs": pairs,
        "n_pairs": n_pairs,
        "n_players_contributing": n_players,
        "n_players_eligible": eligible_players,
        "n_players_without_pair": len(without_pair),
        "players_without_pair": without_pair,
        "n_strata_matched": int(cells),
        "n_strata_one_sided": int(empty_cells),
        "n_unmatched_high": int(unmatched_t),
        "n_unmatched_low": int(unmatched_c),
        "min_pairs": min_pairs,
        "note": note,
        "coarsening": {
            "treatment_bin": treatment, "control_bin": control,
            "exact_on": ["route_family", "depth_bin", "quarter", "load_bin"],
            "pairing": "seeded random permutation within (player, stratum)",
            "seed": seed,
        },
    }


def matched_bootstrap(pairs: pd.DataFrame, cfg: Mapping,
                      reps: int | None = None) -> dict:
    """Player-clustered bootstrap of the mean matched pair difference."""
    block_cfg = _need(block(cfg), "matched", "matched")
    if pairs is None or len(pairs) == 0:
        return {"status": "no_pairs", "estimate": None, "se": None,
                "ci_low": None, "ci_high": None, "p": None, "reps": 0}
    n_reps = int(reps if reps is not None else
                 _need(block_cfg, "bootstrap_reps", "matched"))
    seed = _seed(cfg, str(block_cfg.get("seed_key", "bootstrap")))
    rng = np.random.default_rng(seed + 1)

    groups = [g["diff"].to_numpy(dtype=float)
              for _, g in pairs.groupby("nfl_id", sort=True)]
    n_players = len(groups)
    estimate = float(pairs["diff"].mean())
    draws = np.empty(n_reps, dtype=float)
    for index in range(n_reps):
        picked = rng.integers(0, n_players, n_players)
        sample = np.concatenate([groups[j] for j in picked])
        draws[index] = float(sample.mean())
    se = float(np.std(draws, ddof=1)) if n_reps > 1 else float("nan")
    lower, upper = np.percentile(draws, [2.5, 97.5])
    p_value = float(2.0 * min((draws <= 0).mean(), (draws >= 0).mean()))
    return {
        "status": "ok",
        "estimate": estimate,
        "se": se,
        "ci_low": float(lower),
        "ci_high": float(upper),
        "p": min(1.0, p_value),
        "reps": n_reps,
        "n_pairs": int(len(pairs)),
        "n_players": n_players,
        "cluster": "player",
        "estimator": "mean within-pair difference in log max speed",
    }


def matched_regression(table: pd.DataFrame, design: dict, cfg: Mapping) -> dict:
    """The regression estimate on the matched rows, for the side-by-side table.

    The matched estimate is a high-minus-low difference in log speed; the
    regression coefficient is per 1 within-player SD of leverage.  To compare
    them honestly the regression is converted with the observed high-low
    difference in ``leverage_z`` *inside the matched sample*, and that
    conversion factor is reported next to it.
    """
    pairs = design.get("pairs")
    if pairs is None or len(pairs) == 0:
        return {"status": "no_pairs"}
    block_cfg = _need(block(cfg), "matched", "matched")
    models = C.lc(cfg)["models"]
    outcome = str(models.get("outcome", "log_max_speed"))
    leverage_col = str(models.get("leverage_column", "leverage_z"))
    controls = tuple(models["controls"])

    rows = pd.unique(np.concatenate([pairs["row_high"].to_numpy(),
                                     pairs["row_low"].to_numpy()]))
    work = table.iloc[np.sort(rows)].copy()
    work["nfl_id"] = work["nfl_id"].astype(str)
    fit = M.fit_within_player(work, outcome, leverage_col, controls, cfg=cfg)
    delta = float(pairs["leverage_z_high"].mean()
                  - pairs["leverage_z_low"].mean())
    beta = fit.get("beta")
    return {
        "status": "ok" if np.isfinite(beta) else "not_estimable",
        "beta_per_sd": beta,
        "se_per_sd": fit.get("se"),
        "p_per_sd": fit.get("p"),
        "ci_low_per_sd": fit.get("ci_low"),
        "ci_high_per_sd": fit.get("ci_high"),
        "mean_leverage_z_gap": delta,
        "beta_high_vs_low": (float(beta) * delta
                             if beta is not None and np.isfinite(beta) else None),
        "se_high_vs_low": (float(fit.get("se", np.nan)) * abs(delta)
                           if np.isfinite(fit.get("se", np.nan)) else None),
        "n_matched_rows": int(len(work)),
        "n_players": int(work["nfl_id"].nunique()),
        "controls": list(controls),
        "note": ("both sides are a high-minus-low contrast in log speed; the "
                 "regression side is the primary specification refitted on the "
                 "matched rows and converted with the matched-sample leverage_z "
                 "gap. The matched side is unadjusted beyond the exact strata."),
    }


def matched_block(table: pd.DataFrame, cfg: Mapping,
                  reps: int | None = None) -> dict:
    design = matched_design(table, cfg)
    boot = matched_bootstrap(design["pairs"], cfg, reps=reps)
    regression = matched_regression(table, design, cfg)
    return {
        "status": design["status"],
        "n_pairs": design["n_pairs"],
        "n_players_contributing": design["n_players_contributing"],
        "n_players_eligible": design["n_players_eligible"],
        "n_players_without_pair": design["n_players_without_pair"],
        "players_without_pair": design["players_without_pair"],
        "n_strata_matched": design["n_strata_matched"],
        "n_strata_one_sided": design["n_strata_one_sided"],
        "n_unmatched_high": design["n_unmatched_high"],
        "n_unmatched_low": design["n_unmatched_low"],
        "min_pairs": design["min_pairs"],
        "coarsening": design["coarsening"],
        "note": design["note"],
        "matched": boot,
        "regression_on_matched": regression,
        "_pairs": design["pairs"],
    }


# --------------------------------------------------------------------------- #
# 2. Headroom moderation with an independent-half utilisation baseline
# --------------------------------------------------------------------------- #
def half_split_utilisation(table: pd.DataFrame, cfg: Mapping) -> dict:
    """Estimate baseline utilisation on an independent half of low-leverage rows.

    For each player the low-leverage efforts are permuted with a fixed seed and
    split into halves A and B.  Utilisation is the **median effort fraction on
    half A**; the moderation analysis uses the **other** rows only (all
    high-leverage efforts plus half B).  The two index sets are asserted
    disjoint here, so the comparison can never contaminate its own baseline.
    """
    head = _need(block(cfg), "headroom", "headroom")
    seed = _seed(cfg, str(head.get("half_split_seed_key", "model")))
    min_low = int(_need(head, "min_low_efforts_for_split", "headroom"))
    low_bin = str(_need(head, "baseline_bin", "headroom"))

    if "effort_fraction" not in table.columns:
        return {"status": "NOT RUN",
                "reason": "effort fraction needs the ceiling (vmax) join, "
                          "which is absent from this table; utilisation is not "
                          "proxied from raw speed",
                "missing_field": "vmax (outputs/ceiling/lc/player_ceiling.csv)"}

    work = table.copy()
    work["nfl_id"] = work["nfl_id"].astype(str)
    work["_row"] = np.arange(len(work))
    work["leverage_bin"] = work["leverage_bin"].astype(str)
    rng = np.random.default_rng(seed)

    util_rows: list[dict] = []
    util_index: list[int] = []
    excluded: list[dict] = []
    for player, group in work.groupby("nfl_id", sort=True):
        low = group[(group["leverage_bin"] == low_bin)
                    & group["effort_fraction"].notna()]
        if len(low) < min_low:
            # Not estimable for this player, and reported as such: silently
            # dropping them would leave the reader unable to tell a design
            # exclusion from a player who was never in the table.
            excluded.append({
                "nfl_id": str(player),
                "n_low_efforts": int(len(low)),
                "min_low_efforts": min_low,
                "status": "not estimable",
                "reason": (f"only {len(low)} low-leverage efforts with a "
                           f"ceiling estimate; the independent half split needs "
                           f">= {min_low}"),
            })
            continue
        order = rng.permutation(len(low))
        half = len(order) // 2
        half_a = low.iloc[np.sort(order[:half])]
        values = half_a["effort_fraction"].to_numpy(dtype=float)
        n_a = int(len(values))
        sd = float(np.std(values, ddof=1)) if n_a > 1 else float("nan")
        se = float(1.2533 * sd / np.sqrt(n_a)) if n_a > 1 else float("nan")
        util_rows.append({
            "nfl_id": str(player),
            "baseline_utilization": float(np.median(values)),
            "utilization_se": se,
            "n_half_a": n_a,
            "n_low_total": int(len(low)),
        })
        util_index.extend(int(v) for v in half_a["_row"].to_numpy())

    util = pd.DataFrame(util_rows, columns=["nfl_id", "baseline_utilization",
                                            "utilization_se", "n_half_a",
                                            "n_low_total"])
    if util.empty:
        return {"status": "no_players", "n_players": 0,
                "reason": (f"no player had >= {min_low} low-leverage efforts "
                           "with a ceiling estimate, so no baseline could be "
                           "estimated on an independent half"),
                "n_players_excluded": len(excluded),
                "excluded_players": excluded,
                "_utilization": util}

    work = work.merge(util, on="nfl_id", how="left")
    analysis = work[work["baseline_utilization"].notna()
                    & work["effort_fraction"].notna()].copy()
    # the comparison rows exclude every row used to estimate the baseline
    analysis = analysis[~analysis["_row"].isin(util_index)].copy()

    util_set = set(util_index)
    analysis_set = set(int(v) for v in analysis["_row"].to_numpy())
    if util_set & analysis_set:  # pragma: no cover - defensive self-check
        raise RuntimeError("headroom split is contaminated: a utilisation row "
                           "also appears in the comparison sample")
    return {
        "status": "ok",
        "n_players": int(util["nfl_id"].nunique()),
        "n_util_rows": int(len(util_index)),
        "n_analysis_rows": int(len(analysis)),
        "n_analysis_players": int(analysis["nfl_id"].nunique()),
        "n_players_excluded": len(excluded),
        "excluded_players": excluded,
        "split": {
            "seed": seed,
            "rule": ("within player, low-leverage efforts permuted with the "
                     "configured seed and split in half; median effort fraction "
                     "on half A; comparison uses all high-leverage efforts plus "
                     "half B, never half A"),
            "min_low_efforts": min_low,
            "disjoint": True,
            "n_util_rows": int(len(util_index)),
            "n_analysis_rows": int(len(analysis)),
            "n_excluded_half_a_rows": int(len(util_index)),
            "n_players_excluded_few_low_efforts": len(excluded),
        },
        "_utilization": util,
        "_analysis": analysis,
    }


def _eiv(moderator_values: np.ndarray, moderator_se: np.ndarray) -> dict:
    """Reliability ratio lambda for a moderator measured with error."""
    values = np.asarray(moderator_values, dtype=float)
    se = np.asarray(moderator_se, dtype=float)
    values = values[np.isfinite(values)]
    se = se[np.isfinite(se)]
    if len(values) < 3 or len(se) == 0:
        return {"lambda": float("nan"), "var_observed": float("nan"),
                "mean_se_squared": float("nan")}
    var_observed = float(np.var(values, ddof=1))
    mean_se2 = float(np.mean(se ** 2))
    if var_observed <= 0:
        return {"lambda": float("nan"), "var_observed": var_observed,
                "mean_se_squared": mean_se2}
    return {"lambda": float(max(0.0, 1.0 - mean_se2 / var_observed)),
            "var_observed": var_observed, "mean_se_squared": mean_se2}


def _interaction_fit(frame: pd.DataFrame, moderator: str, cfg: Mapping,
                     outcome: str) -> dict:
    """Fit ``outcome ~ leverage * moderator + controls`` with the FE estimator.

    The interaction column is handed to ``fit_within_player`` as its estimand
    column, so the returned beta and its player-clustered SE are exactly the
    interaction coefficient; the main effects ride along as controls in the same
    design (linear reparameterisation, same span, unchanged interaction).
    """
    controls = list(C.lc(cfg)["models"]["controls"])
    work = frame.copy()
    values = _numeric(work[moderator]).astype(float)
    centre = float(np.nanmean(values.to_numpy(dtype=float))) if values.notna().any() else 0.0
    work["_moderator_c"] = values - centre
    work["_lev_x_mod"] = (_numeric(work["leverage_z"]).astype(float)
                          * work["_moderator_c"])
    work["_leverage_main"] = _numeric(work["leverage_z"]).astype(float)
    design_controls = tuple(["_leverage_main", "_moderator_c", *controls])
    fit = M.fit_within_player(work, outcome, "_lev_x_mod", design_controls, cfg=cfg)
    fit["moderator_centre"] = centre
    fit["moderator_n"] = int(values.notna().sum())
    return fit


def headroom_moderation(table: pd.DataFrame, cfg: Mapping) -> dict:
    """Moderation of the leverage effect by headroom (utilisation, vmax, tau)."""
    head = _need(block(cfg), "headroom", "headroom")
    outcome = str(C.lc(cfg)["models"].get("outcome", "log_max_speed"))

    split = half_split_utilisation(table, cfg)
    if split["status"] != "ok":
        return {"status": split["status"], "reason": split.get("reason"),
                "missing_field": split.get("missing_field"), "rows": [],
                "_split": split, "_utilization": split.get("_utilization")}

    analysis = split["_analysis"]
    rows: list[dict] = []

    # --- leverage x baseline utilization (secondary, pre-declared) -----------
    util_se = (_numeric(analysis["utilization_se"]).astype(float)
               .groupby(analysis["nfl_id"]).first())
    reliability = _eiv(analysis["baseline_utilization"].to_numpy(dtype=float),
                       util_se.reindex(analysis["nfl_id"]).to_numpy(dtype=float))
    fit = _interaction_fit(analysis, "baseline_utilization", cfg, outcome)
    lam = reliability["lambda"]
    corrected_beta = (float(fit["beta"]) / lam
                      if lam is not None and np.isfinite(lam) and lam > 0
                      else None)
    corrected_se = (float(fit["se"]) / lam
                    if lam is not None and np.isfinite(lam) and lam > 0
                    else None)
    terms = {t["term"]: t for t in fit.get("terms", [])}
    main = terms.get("_leverage_main", {})
    rows.append({
        "model": "leverage_x_baseline_utilization",
        "moderator": "baseline_utilization",
        "status": "ok" if np.isfinite(fit.get("beta", np.nan)) else "not_estimable",
        "beta": fit.get("beta"),
        "se": fit.get("se"),
        "ci_low": fit.get("ci_low"),
        "ci_high": fit.get("ci_high"),
        "p": fit.get("p"),
        "naive_beta": fit.get("beta"),
        "naive_se": fit.get("se"),
        "lambda": lam,
        "var_moderator_observed": reliability["var_observed"],
        "mean_moderator_se_squared": reliability["mean_se_squared"],
        "corrected_beta": corrected_beta,
        "corrected_se": corrected_se,
        "correction": EIV_CORRECTION,
        "moderator_scale": "effort fraction (max speed / vmax), player median",
        "main_leverage_beta": main.get("estimate"),
        "main_leverage_se": main.get("se"),
        "n": fit.get("n"),
        "n_players": fit.get("n_players"),
        "note": ("the utilisation baseline comes from an independent half of "
                 "the player's low-leverage efforts; the comparison rows never "
                 "include that half"),
    })

    # --- leverage x ceiling (vmax, tau) --------------------------------------
    for moderator, label in (("vmax", "leverage_x_vmax"), ("tau", "leverage_x_tau")):
        if moderator not in analysis.columns:
            rows.append({"model": label, "moderator": moderator,
                         "status": "NOT RUN",
                         "note": f"{moderator} is not in the ceiling join"})
            continue
        frame = analysis[analysis[moderator].notna()].copy()
        if frame["nfl_id"].nunique() < 3 or len(frame) < 20:
            rows.append({"model": label, "moderator": moderator,
                         "status": "insufficient_sample",
                         "n": int(len(frame)),
                         "n_players": int(frame["nfl_id"].nunique()),
                         "note": "too few players or efforts to identify an "
                                 "interaction with a player-constant moderator"})
            continue
        se_column = f"{moderator}_se"
        per_player = (frame.groupby("nfl_id")[moderator].first())
        per_player_se = (frame.groupby("nfl_id")[se_column].first()
                         if se_column in frame.columns
                         else per_player * np.nan)
        rel = _eiv(per_player.to_numpy(dtype=float),
                   per_player_se.to_numpy(dtype=float))
        fit_m = _interaction_fit(frame, moderator, cfg, outcome)
        lam_m = rel["lambda"]
        terms_m = {t["term"]: t for t in fit_m.get("terms", [])}
        main_m = terms_m.get("_leverage_main", {})
        rows.append({
            "model": label,
            "moderator": moderator,
            "status": ("ok" if np.isfinite(fit_m.get("beta", np.nan))
                       else "not_estimable"),
            "beta": fit_m.get("beta"),
            "se": fit_m.get("se"),
            "ci_low": fit_m.get("ci_low"),
            "ci_high": fit_m.get("ci_high"),
            "p": fit_m.get("p"),
            "naive_beta": fit_m.get("beta"),
            "naive_se": fit_m.get("se"),
            "lambda": lam_m,
            "var_moderator_observed": rel["var_observed"],
            "mean_moderator_se_squared": rel["mean_se_squared"],
            "corrected_beta": (float(fit_m["beta"]) / lam_m
                               if np.isfinite(lam_m) and lam_m > 0 else None),
            "corrected_se": (float(fit_m["se"]) / lam_m
                             if np.isfinite(lam_m) and lam_m > 0 else None),
            "correction": EIV_CORRECTION,
            "moderator_scale": f"Combine {moderator} (player-constant, centred)",
            "main_leverage_beta": main_m.get("estimate"),
            "main_leverage_se": main_m.get("se"),
            "n": fit_m.get("n"),
            "n_players": fit_m.get("n_players"),
            "note": ("the moderator is player-constant, so its main effect is "
                     "absorbed by the player fixed effects; only the "
                     "interaction is identified"),
        })

    return {"status": "ok", "rows": rows, "split": split["split"],
            "n_players": int(split["n_players"]),
            "n_players_excluded": int(split.get("n_players_excluded", 0)),
            "excluded_players": split.get("excluded_players", []),
            "n_analysis_rows": int(split["n_analysis_rows"]),
            "n_utilisation_rows": int(split["n_util_rows"]),
            "_split": split, "_utilization": split.get("_utilization")}


# --------------------------------------------------------------------------- #
# 3. Ceiling bound check
# --------------------------------------------------------------------------- #
def ceiling_bound(table: pd.DataFrame, cfg: Mapping) -> dict:
    """Predicted effort fraction under high leverage vs the plausible ceiling.

    Two rates per leverage bin: the *observed* share of efforts whose max speed
    exceeds the Combine ceiling, and the share of **counterfactual predictions**
    at high leverage that exceed it.  The counterfactual is the primary model
    refit on this table with each row's leverage set to its player's high-bin
    mean; no post-snap information enters.
    """
    head = _need(block(cfg), "ceiling_bound", "ceiling_bound")
    ratio = float(_need(head, "exceedance_ratio", "ceiling_bound"))
    reference_bin = str(_need(head, "leverage_bin", "ceiling_bound"))
    outcome = str(C.lc(cfg)["models"].get("outcome", "log_max_speed"))
    leverage_col = str(C.lc(cfg)["models"].get("leverage_column", "leverage_z"))
    controls = tuple(C.lc(cfg)["models"]["controls"])

    if "vmax" not in table.columns or table["vmax"].isna().all():
        return {"status": "NOT RUN", "rows": [],
                "missing_field": "vmax (outputs/ceiling/lc/player_ceiling.csv)",
                "reason": ("the ceiling bound needs the per-player Combine "
                           "ceiling; it is not proxied from in-game speed")}

    work = table[table["vmax"].notna()].copy()
    work["nfl_id"] = work["nfl_id"].astype(str)
    work = work[_numeric(work[outcome]).notna()].reset_index(drop=True)
    if len(work) < 10 or work["nfl_id"].nunique() < 2:
        return {"status": "insufficient_sample", "rows": [],
                "n": int(len(work)),
                "n_players": int(work["nfl_id"].nunique())}

    fit = M.fit_within_player(work, outcome, leverage_col, controls, cfg=cfg)
    X, names, index = M.design_matrix(work, leverage_col, controls,
                                      cfg=cfg)
    beta, *_ = np.linalg.lstsq(X, work[outcome].to_numpy(dtype=float),
                               rcond=None)
    fitted = X @ beta

    high = work[work["leverage_bin"].astype(str) == reference_bin]
    per_player_high = high.groupby("nfl_id")[leverage_col].mean()
    pooled_high = float(high[leverage_col].mean()) if len(high) else float(
        work[leverage_col].mean())
    counterfactual = work[leverage_col].copy()
    mapped = work["nfl_id"].map(per_player_high)
    counterfactual = mapped.fillna(pooled_high).astype(float)
    X_cf = X.copy()
    X_cf[:, index] = counterfactual.to_numpy(dtype=float)
    predicted_log = X_cf @ beta

    work["_observed_fraction"] = (work["max_speed_yds"].astype(float)
                                  / work["vmax"].astype(float))
    work["_fitted_fraction"] = np.exp(fitted) / work["vmax"].astype(float)
    work["_predicted_high_fraction"] = (np.exp(predicted_log)
                                        / work["vmax"].astype(float))
    work["_observed_exceed"] = work["_observed_fraction"] > ratio
    work["_fitted_exceed"] = work["_fitted_fraction"] > ratio
    work["_predicted_exceed"] = work["_predicted_high_fraction"] > ratio

    rows: list[dict] = []
    for label, group in work.groupby(work["leverage_bin"].astype(str), sort=True):
        rows.append({
            "leverage_bin": str(label),
            "n": int(len(group)),
            "n_players": int(group["nfl_id"].nunique()),
            "mean_observed_fraction": float(group["_observed_fraction"].mean()),
            "observed_exceedance_rate": float(group["_observed_exceed"].mean()),
            "mean_fitted_fraction": float(group["_fitted_fraction"].mean()),
            "fitted_exceedance_rate": float(group["_fitted_exceed"].mean()),
            "mean_predicted_fraction_at_high": float(
                group["_predicted_high_fraction"].mean()),
            "predicted_exceedance_rate_at_high": float(
                group["_predicted_exceed"].mean()),
        })
    all_rows = {
        "leverage_bin": "all",
        "n": int(len(work)),
        "n_players": int(work["nfl_id"].nunique()),
        "mean_observed_fraction": float(work["_observed_fraction"].mean()),
        "observed_exceedance_rate": float(work["_observed_exceed"].mean()),
        "mean_fitted_fraction": float(work["_fitted_fraction"].mean()),
        "fitted_exceedance_rate": float(work["_fitted_exceed"].mean()),
        "mean_predicted_fraction_at_high": float(
            work["_predicted_high_fraction"].mean()),
        "predicted_exceedance_rate_at_high": float(
            work["_predicted_exceed"].mean()),
    }
    return {
        "status": "ok",
        "rows": rows,
        "all_rows": all_rows,
        "exceedance_ratio": ratio,
        "reference_bin": reference_bin,
        "max_predicted_fraction_at_high": float(
            work["_predicted_high_fraction"].max()),
        "max_observed_fraction": float(work["_observed_fraction"].max()),
        "n_efforts_with_ceiling": int(len(work)),
        "n_efforts_without_ceiling": int(len(table) - len(work)),
        "specification": f"{outcome} ~ {leverage_col} + " + " + ".join(controls),
        "note": ("the counterfactual replaces each row's leverage with its "
                 "player's high-bin mean and holds every other control fixed; "
                 "the fitted values are the same design matrix as the primary "
                 "estimand"),
        "estimator": "player fixed effects (lc_models.design_matrix + OLS), "
                     "the same design matrix as the primary estimand",
        "model_beta": fit.get("beta"),
        "model_se": fit.get("se"),
    }


# --------------------------------------------------------------------------- #
# 4. Dose-response across leverage bins
# --------------------------------------------------------------------------- #
def dose_response(table: pd.DataFrame, cfg: Mapping) -> dict:
    """Effect by leverage bin and an explicit monotonicity statement."""
    head = _need(block(cfg), "dose_response", "dose_response")
    bins = [str(v) for v in _need(head, "bins", "dose_response")]
    outcome = str(C.lc(cfg)["models"].get("outcome", "log_max_speed"))
    controls = [c for c in C.lc(cfg)["models"]["controls"]
                if c not in ("leverage", "leverage_z")]

    work = table.copy()
    work["nfl_id"] = work["nfl_id"].astype(str)
    work["leverage_bin"] = work["leverage_bin"].astype(str)
    present = [b for b in bins if b in set(work["leverage_bin"])]
    if len(present) < 2:
        return {"status": "insufficient_bins", "bins_present": present,
                "rows": [], "monotone": None, "direction": "not evaluable",
                "note": "fewer than two leverage bins are present in the table"}
    reference = present[0]
    work = work[work["leverage_bin"].isin(present)].copy()
    for label in present[1:]:
        work[f"_bin_{label}"] = (work["leverage_bin"] == label).astype(float)
    work["_bin_index"] = work["leverage_bin"].map(
        {label: float(i) for i, label in enumerate(present)})

    rows: list[dict] = []
    rows.append({"term": reference, "bin": reference, "role": "reference",
                 "status": "ok",
                 "beta": 0.0, "se": 0.0, "ci_low": None, "ci_high": None,
                 "p": None, "n": int((work["leverage_bin"] == reference).sum())})
    for label in present[1:]:
        estimand = f"_bin_{label}"
        others = [c for c in (f"_bin_{x}" for x in present[1:]) if c != estimand]
        fit = M.fit_within_player(work, outcome, estimand,
                                  tuple([*others, *controls]), cfg=cfg)
        beta = fit.get("beta")
        estimable = beta is not None and np.isfinite(float(beta))
        rows.append({
            "term": f"{label} vs {reference}",
            "bin": label,
            "role": "bin contrast",
            "status": "ok" if estimable else "not estimable",
            "beta": beta if estimable else None,
            "se": fit.get("se"),
            "ci_low": fit.get("ci_low"),
            "ci_high": fit.get("ci_high"),
            "p": fit.get("p"),
            "n": fit.get("n"),
            "n_players": fit.get("n_players"),
            "note": ("" if estimable else
                     f"the {label} contrast is not estimable: too few players "
                     "or efforts with within-player variation in this bin, so "
                     "the bin estimate is reported as missing rather than as a "
                     "number"),
        })
    trend = M.fit_within_player(work, outcome, "_bin_index", tuple(controls),
                                cfg=cfg)

    missing_bins = [r["bin"] for r in rows[1:] if r.get("status") != "ok"]
    estimates = [0.0] + [float(r["beta"]) for r in rows[1:]
                         if r.get("status") == "ok"]
    tol = float(head.get("monotonicity_tolerance", 1e-9))
    diffs = np.diff(np.asarray(estimates, dtype=float)) if len(estimates) > 1 else np.array([])
    if missing_bins:
        # monotonicity over a subset of the bins would be a silent claim about
        # bins that were never estimated
        monotone, direction = None, "not evaluable"
    elif len(diffs) == 0:
        monotone, direction = None, "not evaluable"
    elif bool(np.all(diffs > tol)):
        monotone, direction = True, "increasing"
    elif bool(np.all(diffs < -tol)):
        monotone, direction = True, "decreasing"
    else:
        monotone, direction = False, "not monotone"

    return {
        "status": "ok",
        "bins_present": present,
        "reference_bin": reference,
        "rows": rows,
        "monotone": monotone,
        "direction": direction,
        "not_estimable_bins": missing_bins,
        "monotone_tolerance": tol,
        "trend": {
            "term": "leverage bin index (0,1,2...)",
            "beta": trend.get("beta"),
            "se": trend.get("se"),
            "ci_low": trend.get("ci_low"),
            "ci_high": trend.get("ci_high"),
            "p": trend.get("p"),
            "n": trend.get("n"),
            "n_players": trend.get("n_players"),
        },
        "note": ("bin contrasts are differences from the lowest bin, on the "
                 "log-speed scale; the trend row is the same design collapsed "
                 "to a linear index. Monotone means the bin estimates move in "
                 "one direction only"
                 + ("" if not missing_bins else
                    f"; not evaluable because the {', '.join(missing_bins)} "
                    "contrast(s) are not estimable — monotonicity is never "
                    "asserted over a subset of the bins")),
    }


# --------------------------------------------------------------------------- #
# 5. Per-player leverage slopes, reliability-gated
# --------------------------------------------------------------------------- #
def _ols_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")
    x_mean, y_mean = float(np.mean(x)), float(np.mean(y))
    sxx = float(np.sum((x - x_mean) ** 2))
    if sxx <= 0:
        return float("nan"), float("nan")
    beta = float(np.sum((x - x_mean) * (y - y_mean)) / sxx)
    resid = y - (y_mean + beta * (x - x_mean))
    dof = n - 2
    s2 = float(np.sum(resid ** 2) / dof) if dof > 0 else float("nan")
    se = float(np.sqrt(s2 / sxx)) if np.isfinite(s2) and s2 >= 0 else float("nan")
    return beta, se


def per_player_slopes(table: pd.DataFrame, cfg: Mapping) -> dict:
    """Model-based reliability gate, then (only then) per-player slopes.

    reliability = tau^2 / (tau^2 + mean(SE^2)) with
    tau^2 = max(0, var(slopes) - mean(SE^2)).  No odd/even split-half is used.
    """
    head = _need(block(cfg), "per_player", "per_player")
    threshold = float(_need(head, "reliability_threshold", "per_player"))
    min_efforts = int(_need(head, "min_efforts", "per_player"))
    min_players = int(_need(head, "min_players", "per_player"))
    outcome = str(C.lc(cfg)["models"].get("outcome", "log_max_speed"))
    leverage_col = str(C.lc(cfg)["models"].get("leverage_column", "leverage_z"))

    work = table.copy()
    work["nfl_id"] = work["nfl_id"].astype(str)
    slopes: list[dict] = []
    for player, group in work.groupby("nfl_id", sort=True):
        frame = group[[outcome, leverage_col]].apply(
            pd.to_numeric, errors="coerce").dropna()
        if len(frame) < min_efforts or frame[leverage_col].nunique() < 2:
            continue
        beta, se = _ols_slope(frame[leverage_col].to_numpy(dtype=float),
                              frame[outcome].to_numpy(dtype=float))
        if not np.isfinite(beta) or not np.isfinite(se) or se <= 0:
            continue
        slopes.append({"nfl_id": str(player), "slope": beta, "se": se,
                       "n_efforts": int(len(frame)),
                       "ci_low": beta - 1.96 * se,
                       "ci_high": beta + 1.96 * se})
    frame = pd.DataFrame(slopes, columns=["nfl_id", "slope", "se", "n_efforts",
                                          "ci_low", "ci_high"])
    if len(frame) < 3:
        return {"status": "withheld", "rows": [],
                "reason": f"only {len(frame)} players had >= {min_efforts} "
                          "usable efforts and a non-degenerate within-player "
                          "leverage spread; reliability is not estimable",
                "reliability_threshold": threshold,
                "n_players_eligible": int(len(frame))}

    mean_se2 = float(np.mean(frame["se"].to_numpy(dtype=float) ** 2))
    var_slopes = float(np.var(frame["slope"].to_numpy(dtype=float), ddof=1))
    tau2 = max(0.0, var_slopes - mean_se2)
    reliability = (tau2 / (tau2 + mean_se2)) if (tau2 + mean_se2) > 0 else float("nan")
    passed = bool(np.isfinite(reliability) and reliability >= threshold
                  and len(frame) >= min_players)
    reason = ("reliability clears the configured threshold"
              if passed else
              f"model-based reliability {reliability:.3f} is below the "
              f"configured threshold {threshold:.2f} (or fewer than "
              f"{min_players} eligible players), so no per-player table is "
              "reported — a noisy slope is not evidence about a player")
    return {
        "status": "reported" if passed else "withheld",
        "rows": frame.to_dict("records") if passed else [],
        "n_players_eligible": int(len(frame)),
        "mean_squared_se": mean_se2,
        "var_slopes": var_slopes,
        "tau_squared": tau2,
        "reliability": reliability,
        "reliability_threshold": threshold,
        "min_efforts": min_efforts,
        "min_players": min_players,
        "reliability_rule": ("tau^2 / (tau^2 + mean(SE^2)) with "
                             "tau^2 = max(0, var(slopes) - mean(SE^2)); "
                             "model-based, not an odd/even split-half"),
        "reason": reason,
        "estimator": "uncontrolled within-player OLS of log max speed on leverage",
    }


# --------------------------------------------------------------------------- #
# 6. TE replication
# --------------------------------------------------------------------------- #
def replication(table: pd.DataFrame, cfg: Mapping) -> dict:
    """The primary specification for the replication position (TE)."""
    head = _need(block(cfg), "replication", "replication")
    position = str(_need(head, "position", "replication"))
    threshold = int(_need(head, "exploratory_below_players", "replication"))
    outcome = str(C.lc(cfg)["models"].get("outcome", "log_max_speed"))
    leverage_col = str(C.lc(cfg)["models"].get("leverage_column", "leverage_z"))
    controls = tuple(C.lc(cfg)["models"]["controls"])

    if "position" not in table.columns:
        return {"status": "NOT RUN", "position": position,
                "missing_field": "position (nfl_position)",
                "reason": ("the effort table carries no position column and "
                           "the players parquet mirror was not readable, so the "
                           "replication sample cannot be identified")}

    work = table[table["position"].astype(str) == position].copy()
    work["nfl_id"] = work["nfl_id"].astype(str)
    n_players = int(work["nfl_id"].nunique())
    if len(work) == 0 or n_players < 2:
        return {"status": "no_sample", "position": position,
                "n_efforts": int(len(work)), "n_players": n_players,
                "label": "not estimated",
                "reason": f"no usable {position} efforts in this table"}
    fit = M.fit_within_player(work, outcome, leverage_col, controls, cfg=cfg)
    label = "exploratory" if n_players < threshold else "confirmatory-scale"
    per_player = len(work) / max(n_players, 1)
    mde = M.analytic_mde(per_player, cfg)
    return {
        "status": "ok",
        "position": position,
        "label": label,
        "labelled_on_n_players": n_players,
        "exploratory_below_players": threshold,
        "beta": fit.get("beta"),
        "se": fit.get("se"),
        "ci_low": fit.get("ci_low"),
        "ci_high": fit.get("ci_high"),
        "p": fit.get("p"),
        "n_efforts": int(fit.get("n", len(work))),
        "n_players": int(fit.get("n_players", n_players)),
        "efforts_per_player": float(per_player),
        "mde": mde["mde"],
        "fallback_used": fit.get("fallback_used"),
        "specification": f"{outcome} ~ {leverage_col} + " + " + ".join(controls),
        "note": (f"labelled {label} on n = {n_players} {position} players "
                 f"(threshold {threshold}); the primary claim stays WR-only"),
    }


# --------------------------------------------------------------------------- #
# Tactical demand: what can run, and what is NOT RUN and why
# --------------------------------------------------------------------------- #
def tactical_demand(table: pd.DataFrame, cfg: Mapping) -> dict:
    """Time-to-throw contrast (available) and defender distance (absent)."""
    head = _need(block(cfg), "tactical_demand", "tactical_demand")
    field_name = str(head.get("defender_distance_field", DEFENDER_FIELD_DEFAULT))
    window = float(_need(head, "comparable_window_s", "tactical_demand"))
    outcome = str(C.lc(cfg)["models"].get("outcome", "log_max_speed"))
    leverage_col = str(C.lc(cfg)["models"].get("leverage_column", "leverage_z"))
    controls = tuple(C.lc(cfg)["models"]["controls"])
    time_col = "time_to_throw_s"

    out: dict[str, Any] = {"status": "ok"}

    if time_col not in table.columns:
        out["time_to_throw"] = {
            "status": "NOT RUN", "missing_field": time_col,
            "reason": "the effort table has no time-to-throw column"}
    else:
        work = table.copy()
        work["nfl_id"] = work["nfl_id"].astype(str)
        values = _numeric(work[time_col]).astype(float)
        median = float(values.median()) if values.notna().any() else float("nan")
        restricted = work[(values - median).abs() <= window].copy()
        full = M.fit_within_player(work, outcome, leverage_col, controls, cfg=cfg)
        comparable_ok = (np.isfinite(median) and len(restricted) >= 3
                         and restricted["nfl_id"].nunique() >= 2)
        narrow = (M.fit_within_player(restricted, outcome, leverage_col, controls,
                                      cfg=cfg)
                  if comparable_ok else
                  {"beta": None, "se": None, "ci_low": None, "ci_high": None,
                   "p": None, "n": int(len(restricted)), "n_players": 0})
        out["time_to_throw"] = {
            "status": "ok" if comparable_ok else "not estimable",
            "median_s": median if np.isfinite(median) else None,
            "window_s": window,
            "reason": (None if comparable_ok else
                       "the comparable time-to-throw window is empty or has "
                       "too little within-player variation to fit"),
            "all": {"beta": full.get("beta"), "se": full.get("se"),
                    "ci_low": full.get("ci_low"), "ci_high": full.get("ci_high"),
                    "p": full.get("p"), "n": full.get("n"),
                    "n_players": full.get("n_players")},
            "comparable": {"beta": narrow.get("beta"), "se": narrow.get("se"),
                           "ci_low": narrow.get("ci_low"),
                           "ci_high": narrow.get("ci_high"),
                           "p": narrow.get("p"), "n": narrow.get("n"),
                           "n_players": narrow.get("n_players")},
            "note": ("the restricted fit keeps efforts within "
                     f"+/-{window:g}s of the pooled median time to throw, so "
                     "the comparison holds throw timing roughly fixed; it "
                     "cannot separate defensive adjustment from effort"
                     if comparable_ok else
                     "the comparable-time-to-throw window is empty or has no "
                     "within-player variation, so that contrast is reported as "
                     "not estimable rather than as a coefficient"),
        }

    if field_name not in table.columns:
        out["defender_distance"] = {
            "status": "NOT RUN",
            "missing_field": field_name,
            "reason": ("no defender-distance field is written by 04_efforts, "
                       "and the tracking table carries positions only — no "
                       "defender attribution. The check is reported as NOT RUN "
                       "rather than proxied from post-snap positions."),
            "required_action": ("write a per-effort defender distance in "
                                "04_efforts from a permitted pre-snap "
                                "attribution, then re-run this stage"),
        }
    else:
        work = table.copy()
        work["nfl_id"] = work["nfl_id"].astype(str)
        distance = _numeric(work[field_name]).astype(float)
        median = float(distance.median()) if distance.notna().any() else float("nan")
        work["_close"] = (distance <= median).astype(float)
        work["_lev_x_close"] = (work["leverage_z"].astype(float)
                                * (work["_close"] - work["_close"].mean()))
        fit = M.fit_within_player(work, outcome, "_lev_x_close",
                                  tuple(["_close", *controls]), cfg=cfg)
        out["defender_distance"] = {
            "status": "ok", "field": field_name, "median": median,
            "interaction_beta": fit.get("beta"), "se": fit.get("se"),
            "p": fit.get("p"), "n": fit.get("n"),
            "note": "interaction of leverage with close (<= median) defender distance",
        }
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_phase4(table: pd.DataFrame, cfg: Mapping, mode: str = "sample",
               bootstrap_reps: int | None = None) -> dict:
    """Run every Phase 4 remainder check on one effort table."""
    missing = [c for c in EFFORT_REQUIRED if c not in table.columns]
    if missing:
        raise RuntimeError(
            f"07_phase4: the effort table is missing required columns {missing}; "
            "rebuild 04_efforts rather than filling them here")
    if table.empty:
        raise RuntimeError("07_phase4: the effort table is empty; nothing to analyse")

    matched = matched_block(table, cfg, reps=bootstrap_reps)
    headroom = headroom_moderation(table, cfg)
    bound = ceiling_bound(table, cfg)
    dose = dose_response(table, cfg)
    slopes = per_player_slopes(table, cfg)
    repl = replication(table, cfg)
    tactical = tactical_demand(table, cfg)

    return {
        "stage": "07_phase4",
        "mode": mode,
        "definition": block(cfg).get("definition_id", "phase4_remainder_v1"),
        "sample_sizes": {
            "n_efforts": int(len(table)),
            "n_players": int(table["nfl_id"].nunique()),
            "n_games": int(table["game_id"].nunique())
            if "game_id" in table.columns else None,
        },
        "matched_design": {k: v for k, v in matched.items() if k != "_pairs"},
        "headroom": {k: v for k, v in headroom.items()
                     if k not in ("_split", "_utilization")},
        "ceiling_bound": bound,
        "dose_response": dose,
        "per_player_slopes": slopes,
        "replication": repl,
        "tactical_demand": tactical,
        "checks_not_run": _not_run(tactical, headroom),
        "_pairs": matched.get("_pairs"),
        "_utilization": headroom.get("_utilization"),
    }


def _not_run(tactical: Mapping, headroom: Mapping) -> list[dict]:
    rows: list[dict] = []
    for key in ("time_to_throw", "defender_distance"):
        entry = tactical.get(key) or {}
        if entry.get("status") == "NOT RUN":
            rows.append({"check": key,
                         "missing_field": entry.get("missing_field"),
                         "reason": entry.get("reason")})
    if headroom.get("status") == "NOT RUN":
        rows.append({"check": "headroom_moderation",
                     "missing_field": headroom.get("missing_field"),
                     "reason": headroom.get("reason")})
    return rows
