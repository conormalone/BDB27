"""dl_models_core — Part 1 / Part 2 machinery (prereg §7–§14, design §5.5).

Pure functions over in-memory frames: no file paths, no provenance.  The stage
(:mod:`dl.stage_04_models`) owns the I/O, the gate verdict and the artefacts;
this module owns the statistics, so it can be tested on tiny frames without
touching ``outputs/``.

Design rules encoded here (each one is a prereg clause, not a preference)
-------------------------------------------------------------------------
* §7 load: both definitions use the same recency half-life, the same *number*
  of tuned parameters (5 and 5) and the same optimiser settings.  Tuning is
  performed **inside the training fold only**, by training-fold log likelihood,
  and applied unchanged to the held-out fold.  :func:`tune_load` cannot see the
  held-out frame — it is not passed it.
* §8 controls: one fixed list, identical in P1 and in both load definitions.
  ``player_get_off`` is never a P1 control.
* §9 minimum snaps: a rusher enters a model only with ≥ ``min_snaps_per_player``
  primary snaps; a fold needs ≥ ``min_rushers_per_fold`` such rushers or the
  fold count is reduced (logged; a reduction is not a deviation).
* §10 models: Weibull AFT with a log-normal frailty (GH quadrature) is P1's
  primary.  The rungs are fitted **only** when the primary fails to converge,
  and the rung used is reported.  The point estimate is never chosen by which
  rung looks best.
* §11 metric: mean held-out log score per snap (higher better); the difference
  is relative minus absolute in nats/snap; the interval is a player-clustered
  bootstrap over players (all their snaps resampled together).
* §12 SOI/equivalence and §13 thresholds are read from ``config.yaml``; nothing
  here may lower them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from dl import dl_load
from dl import dl_survival as SV

__all__ = [
    "Design",
    "align_test_design",
    "assign_folds",
    "ensure_a0_z",
    "bootstrap_delta",
    "build_design",
    "build_frame",
    "build_load_panel",
    "complete_case_mask",
    "cross_validate",
    "derivation_gate",
    "design_columns_missing",
    "eb_slopes",
    "field_availability",
    "fit_e1",
    "fit_p1",
    "fit_p2",
    "fit_s1",
    "gate_evaluate",
    "hazard_ratio",
    "missing_fields",
    "per_snap_scores",
    "prepare_frame",
    "required_fields",
    "run_part2",
    "standardise_in_fold",
    "tune_load",
]

_Z95 = 1.959963984540054


# --------------------------------------------------------------------------- #
# Frame construction
# --------------------------------------------------------------------------- #
def build_frame(snaps: pd.DataFrame, ceiling: pd.DataFrame,
                players: pd.DataFrame, combine: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Join the snap table to the ceiling, size and draft-year sources.

    Nothing is imputed.  A rusher without a shrunken ``a0`` keeps ``NaN`` and is
    counted; the caller decides (and reports) what to do with those rows.
    """
    frame = snaps.copy()
    a0 = ceiling[ceiling["feature"] == "a0"][["nfl_id", "estimate", "se", "icc"]]
    a0 = a0.rename(columns={"estimate": "a0", "se": "a0_se", "icc": "a0_icc"})
    frame = frame.merge(a0, on="nfl_id", how="left")

    curve = ceiling[ceiling["feature"].isin(["vmax", "tau"])][
        ["nfl_id", "feature", "estimate", "se"]]
    for feature, name in (("vmax", "vmax"), ("tau", "tau")):
        part = curve[curve["feature"] == feature][["nfl_id", "estimate", "se"]]
        part = part.rename(columns={"estimate": name, "se": f"{name}_se"})
        frame = frame.merge(part, on="nfl_id", how="left")

    draft = players[["nfl_id", "draft_year"]].drop_duplicates("nfl_id")
    frame = frame.merge(draft, on="nfl_id", how="left")
    size = combine[["nfl_id", "draft_year", "combine_height", "combine_weight"]]
    size = size.drop_duplicates(["nfl_id", "draft_year"])
    frame = frame.merge(size, on=["nfl_id", "draft_year"], how="left")
    frame = frame.rename(columns={"combine_height": "height_in",
                                  "combine_weight": "weight_lb"})
    frame["years_since_combine"] = (
        pd.to_numeric(frame["season"], errors="coerce")
        - pd.to_numeric(frame["draft_year"], errors="coerce"))
    frame.loc[frame["years_since_combine"] < 0, "years_since_combine"] = np.nan

    report = {
        "n_snaps": int(len(frame)),
        "n_players": int(frame["nfl_id"].nunique()),
        "snaps_without_a0": int(frame["a0"].isna().sum()),
        "players_without_a0": int(frame.loc[frame["a0"].isna(), "nfl_id"].nunique()),
        "snaps_without_size": int(frame["height_in"].isna().sum()),
        "snaps_without_years": int(frame["years_since_combine"].isna().sum()),
        "columns": list(frame.columns),
    }
    return frame, report


def ensure_a0_z(frame: pd.DataFrame) -> pd.DataFrame:
    """Carry raw ``a0`` in the ``a0_z`` column.

    The design matrix standardises every continuous column **inside the
    training fold**, so the column that reaches the fit is a within-fold
    z-score: "per 1 SD of shrunken a0 in the estimation sample", exactly the
    prereg's estimand.  Keeping the raw value here is what lets the fold
    standardisation be the only scaling step.
    """
    if "a0_z" not in frame.columns:
        frame = frame.copy()
        frame["a0_z"] = pd.to_numeric(frame["a0"], errors="coerce")
    return frame


def field_availability(frame: pd.DataFrame, fields: Sequence[str]) -> dict[str, dict]:
    """Per-field presence and coverage — the evidence behind the §15 check."""
    out: dict[str, dict] = {}
    n = int(len(frame))
    for field in fields:
        if field not in frame.columns:
            out[field] = {"present": False, "non_null": 0, "fraction": 0.0}
            continue
        non_null = int(frame[field].notna().sum())
        out[field] = {"present": True, "non_null": non_null,
                      "fraction": float(non_null / n) if n else 0.0}
    return out


def required_fields(cfg: Mapping) -> list[str]:
    """The §8 control list plus the keys the model cannot run without."""
    models = cfg["models"]
    fields = list(models["controls"])
    # NB: time_to_pressure is deliberately NOT required — it is null exactly
    # when the snap is censored (§4/§5).  Requiring it would fit on pressured
    # snaps only, which the prereg forbids.
    fields += ["nfl_id", "event", "censor_time", "a0"]
    fields += [c for c in models.get("crossed_fixed_effects", []) if c not in fields]
    return fields


def missing_fields(frame: pd.DataFrame, fields: Sequence[str]) -> list[str]:
    """Fields that are absent, or present with no usable value at all."""
    avail = field_availability(frame, fields)
    return [f for f, info in avail.items()
            if (not info["present"]) or info["non_null"] == 0]


def design_columns_missing(frame: pd.DataFrame, cfg: Mapping) -> list[str]:
    """Design columns the frame cannot supply (reported, never silently dropped)."""
    wanted = ["a0"] + list(cfg["models"]["controls"]) + \
        list(cfg["models"].get("crossed_fixed_effects", []))
    return [c for c in wanted if c not in frame.columns]


def complete_case_mask(frame: pd.DataFrame, fields: Sequence[str]) -> pd.Series:
    present = [f for f in fields if f in frame.columns]
    mask = frame[present].notna().all(axis=1)
    mask &= pd.to_numeric(frame["censor_time"], errors="coerce") > 0
    return mask


# --------------------------------------------------------------------------- #
# Load panel (§7)
# --------------------------------------------------------------------------- #
def _grid_label(value: float) -> str:
    return dl_load.grid_label(value)


# --------------------------------------------------------------------------- #
# Part-2 NA self-documentation
# --------------------------------------------------------------------------- #
# A Part-2 result of NA is only useful if it says *which* stage refused and
# *why*.  Every exit path of :func:`run_part2` therefore carries three reason
# records -- one per definition and one for the paired comparison.  Each record
# is a machine-readable ``code`` plus one human sentence.  When the run has
# FULL provenance the sentence may not defer to "sample mode", a re-run or a
# larger sample: those are not available to the reader of a full run.
_FULL_FORBIDDEN = ("sample", "re-run", "rerun", "full data", "larger sample",
                   "bigger sample", "more data")


def _is_full(provenance: Any) -> bool:
    text = str(provenance or "").strip().upper()
    return text == "FULL" or text.startswith("FULL:")


def _reason(code: str, message: str, *, provenance: Any = None) -> dict[str, str]:
    """One machine-readable code plus a single-line human sentence."""
    text = " ".join(str(message).split())
    if not _is_full(provenance):
        text = (text + " Sample-mode diagnostic only; no number here is a "
                "finding.").strip()
    return {"code": str(code), "message": text}


def _assert_reason_is_full_safe(reason: Mapping[str, str], provenance: Any) -> None:
    """Guard the rule that a FULL run never defers to a bigger/sample run."""
    if not _is_full(provenance):
        return
    text = str(reason.get("message", "")).lower()
    hit = next((token for token in _FULL_FORBIDDEN if token in text), None)
    if hit:
        raise ValueError(
            f"FULL provenance reason {reason.get('code')!r} defers to {hit!r}: "
            f"{reason.get('message')!r}")


def _usable(frame: pd.DataFrame, column: str) -> int:
    """Non-null, finite count for one column (0 when the column is absent)."""
    if column not in frame.columns:
        return 0
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
    return int(np.isfinite(values).sum())


def _load_diagnostics(data: pd.DataFrame, kind: str,
                      grid: Sequence[float]) -> dict[str, Any]:
    """Whether one definition's load columns can carry any value at all.

    ``build_load_panel`` leaves a load column NA when the snap has no measured
    in-game speed, when its game clock cannot sequence the player-game, or --
    for the relative definition only -- when the rusher has no usable ceiling
    (``vmax``/``tau``).  Counting the inputs separates "the model failed" from
    "the exposure was never built", which is the difference a reader needs.
    """
    columns = [f"{kind}_load_{_grid_label(g)}" for g in grid]
    present = [c for c in columns if c in data.columns]
    non_null = {c: int(pd.to_numeric(data[c], errors="coerce").notna().sum())
                for c in present}
    out: dict[str, Any] = {
        "columns": columns, "present": present, "non_null": non_null,
        "any_value": int(sum(non_null.values())), "kind": kind,
    }
    if kind == "absolute":
        out["measured_speed_rows"] = int(
            (np.isfinite(pd.to_numeric(data.get("in_game_speed_1_0"),
                                       errors="coerce").to_numpy(float))
             & np.isfinite(pd.to_numeric(data.get("in_game_speed_2_0"),
                                         errors="coerce").to_numpy(float))).sum())
        out["clock_rows"] = int(
            (pd.to_numeric(data.get("quarter"), errors="coerce").notna()
             & pd.to_numeric(data.get("game_clock_s"), errors="coerce").notna())
            .sum())
    else:
        out["ceiling_rows"] = int(
            (np.isfinite(pd.to_numeric(data.get("vmax"), errors="coerce")
                         .to_numpy(float))
             & np.isfinite(pd.to_numeric(data.get("tau"), errors="coerce")
                           .to_numpy(float))).sum())
    return out


def _load_reason(diag: Mapping[str, Any], n_rows: int, *,
                 provenance: Any = None) -> dict[str, str]:
    """The reason a definition's load column is entirely NA (or present)."""
    kind = str(diag["kind"])
    if diag["any_value"] > 0:
        return _reason(
            "OK",
            f"{kind} load carries {diag['any_value']} non-null model rows across "
            f"{len(diag['present'])} grid values", provenance=provenance)
    if not diag["present"]:
        return _reason(
            "MISSING_LOAD_COLUMN",
            f"the snap frame has none of the {kind}-load panel columns "
            f"({', '.join(diag['columns'])}), so the {kind} model has no exposure "
            "to fit", provenance=provenance)
    if kind == "absolute":
        detail = (f"measured in-game speed at 1.0/2.0 s on "
                  f"{diag['measured_speed_rows']} of {n_rows} rows; a usable game "
                  f"clock on {diag['clock_rows']} rows")
    else:
        detail = (f"a usable vmax/tau ceiling on {diag['ceiling_rows']} of "
                  f"{n_rows} rows (relative load also needs measured in-game "
                  f"speed and a usable game clock)")
    return _reason(
        "NO_LOAD_VALUES",
        f"every {kind}-load grid column is NA on all {n_rows} model rows: "
        f"{detail}; the {kind} model has no exposure and is not estimable",
        provenance=provenance)


def _folds_reason(fold_rows: Sequence[Mapping], n_scored: int, n_folds: int,
                  mean_score: float | None, *, kind: str,
                  provenance: Any = None) -> dict[str, str]:
    """Why one definition's cross-validation did or did not score a snap."""
    if n_scored > 0 and (mean_score is None or not np.isfinite(mean_score)):
        return _reason(
            "NONFINITE_SCORE",
            f"the {kind} model scored {n_scored} held-out snaps but the mean log "
            "score is not finite; the paired comparison cannot use it",
            provenance=provenance)
    if n_scored > 0:
        return _reason(
            "OK",
            f"{kind} model scored {n_scored} held-out snaps across "
            f"{n_folds} player-grouped folds", provenance=provenance)
    statuses: dict[str, int] = {}
    for row in fold_rows:
        key = str(row.get("status_code") or row.get("status", "UNKNOWN")).split(":")[0]
        statuses[key] = statuses.get(key, 0) + 1
    detail = ", ".join(f"{k} x{v}" for k, v in sorted(statuses.items())) or "no fold"
    return _reason(
        "NO_SCORED_FOLDS",
        f"the {kind} model scored no held-out snap in {n_folds} fold(s); fold "
        f"outcomes: {detail}", provenance=provenance)


def _ceiling_draw_mean(vmax: float, tau: float, vmax_se: float, tau_se: float,
                       t_match: Sequence[float], draws: int, rng) -> float:
    """Draw-mean Combine speed at the matched window (prereg §7).

    The draws are log-normal on each parameter with the delta-method relative
    SE (``se / estimate``); the two parameters are drawn independently, which is
    the assumption the ceiling table supports (it carries marginal SEs only).
    """
    if not (np.isfinite(vmax) and np.isfinite(tau) and vmax > 0 and tau > 0):
        return float("nan")
    sd_v = (vmax_se / vmax) if (np.isfinite(vmax_se) and vmax > 0) else 0.0
    sd_t = (tau_se / tau) if (np.isfinite(tau_se) and tau > 0) else 0.0
    vm = vmax * np.exp(rng.normal(0.0, sd_v, size=int(draws)))
    ta = tau * np.exp(rng.normal(0.0, sd_t, size=int(draws)))
    speeds = [vm * (1.0 - np.exp(-float(t) / ta)) for t in t_match]
    return float(np.mean(np.concatenate(speeds)))


def build_load_panel(frame: pd.DataFrame, cfg: Mapping) -> pd.DataFrame:
    """One load column per grid point, per definition, on the causal rule.

    The arithmetic mirrors ``dl_load.add_load_columns`` exactly — the same
    per-snap distance, the same recency half-life and the same causal prefix
    sum — so the panel column at the frozen threshold equals the flat column
    the snap stage would have written.
    """
    load_cfg = cfg["load"]
    game_cfg = cfg["game"]
    half_life = float(load_cfg["recency_half_life_s"])
    window = game_cfg["post_snap_window_s"]
    window_s = float(window[1]) - float(window[0])
    abs_grid = [float(g) for g in load_cfg["absolute_speed_grid_yds"]]
    rel_grid = [float(g) for g in load_cfg["relative_fraction_grid"]]
    draws = int(load_cfg.get("posterior_draws", 200))
    seed = int(cfg["seeds"]["model"])
    t_match = [float(t) for t in load_cfg.get("t_match_s", window)]

    out = pd.DataFrame(index=frame.index)
    for g in abs_grid:
        out[f"absolute_load_{_grid_label(g)}"] = np.nan
    for g in rel_grid:
        out[f"relative_load_{_grid_label(g)}"] = np.nan
    if len(frame) == 0:
        return out

    s1 = pd.to_numeric(frame["in_game_speed_1_0"], errors="coerce").to_numpy(float)
    s2 = pd.to_numeric(frame["in_game_speed_2_0"], errors="coerce").to_numpy(float)
    measured = np.isfinite(s1) & np.isfinite(s2)
    rep_speed = 0.5 * (s1 + s2)

    def _numeric(name: str) -> np.ndarray:
        if name not in frame.columns:
            return np.full(len(frame), np.nan)
        return pd.to_numeric(frame[name], errors="coerce").to_numpy(float)

    vmax_arr, tau_arr = _numeric("vmax"), _numeric("tau")
    vmax_se_arr, tau_se_arr = _numeric("vmax_se"), _numeric("tau_se")
    rng = np.random.default_rng(seed)
    v_ceiling = np.full(len(frame), np.nan)
    for pid in pd.unique(frame["nfl_id"]):
        idx = np.flatnonzero((frame["nfl_id"] == pid).to_numpy())
        first = idx[0]
        v_ceiling[idx] = _ceiling_draw_mean(
            vmax_arr[first], tau_arr[first], vmax_se_arr[first],
            tau_se_arr[first], t_match, draws, rng)

    elapsed = np.array([
        dl_load.elapsed_game_seconds(q, c)
        for q, c in zip(frame["quarter"].to_numpy(), frame["game_clock_s"].to_numpy())
    ], dtype=float)
    play_ids = pd.to_numeric(frame["play_id"], errors="coerce").to_numpy(float)

    order = sorted(range(len(frame)),
                   key=lambda i: (0 if np.isfinite(elapsed[i]) else 1,
                                  elapsed[i] if np.isfinite(elapsed[i]) else 0.0,
                                  play_ids[i] if np.isfinite(play_ids[i]) else np.inf, i))
    groups: dict[tuple, list[int]] = {}
    for i in order:
        groups.setdefault((frame["game_id"].iloc[i], frame["nfl_id"].iloc[i]), []).append(i)
    positions = np.array(frame.index)

    for key in sorted(groups, key=lambda k: (str(k[0]), str(k[1]))):
        idx = groups[key]
        times = [elapsed[i] for i in idx]
        if not all(np.isfinite(t) for t in times):
            continue          # cannot sequence without a clock; stays NA
        for g in abs_grid:
            values = [np.nan if not measured[i] else max(0.0, rep_speed[i] - g) * window_s
                      for i in idx]
            loads = dl_load._causal_prefix_sum(values, times, half_life)
            out.loc[positions[idx], f"absolute_load_{_grid_label(g)}"] = loads
        ceil = v_ceiling[idx[0]]
        if np.isfinite(ceil) and ceil > 0:
            for g in rel_grid:
                values = [np.nan if not measured[i]
                          else max(0.0, rep_speed[i] - g * ceil) * window_s
                          for i in idx]
                loads = dl_load._causal_prefix_sum(values, times, half_life)
                out.loc[positions[idx], f"relative_load_{_grid_label(g)}"] = loads
    return out


# --------------------------------------------------------------------------- #
# Design matrix
# --------------------------------------------------------------------------- #
@dataclass
class Design:
    X: np.ndarray
    names: list[str]
    continuous: list[int]
    a0_index: int
    load_index: int | None


def build_design(frame: pd.DataFrame, cfg: Mapping, *,
                 load_column: str | None = None) -> Design:
    """Design matrix: ``a0_z`` first, then controls, then crossed effects."""
    models = cfg["models"]
    categorical = set(models.get("categorical_controls", []))
    crossed = list(models.get("crossed_fixed_effects", []))
    # A survival model needs a baseline: without a constant the Weibull scale is
    # forced through the covariates and the shape parameter compensates.
    cols: list[str] = ["a0_z"] + list(models["controls"])
    if load_column:
        cols.append(load_column)
    for col in crossed:
        if col not in cols:
            cols.append(col)
    blocks: list[np.ndarray] = [np.ones(len(frame))]
    names: list[str] = ["const"]
    continuous: list[int] = []
    for col in cols:
        if col in categorical or col in crossed:
            series = frame[col].astype("object")
            levels = sorted({str(v) for v in series.dropna().unique()})
            for level in levels[1:]:
                blocks.append((series.astype(str) == level).to_numpy(float))
                names.append(f"{col}={level}")
        else:
            values = pd.to_numeric(frame[col], errors="coerce").to_numpy(float)
            continuous.append(len(blocks))
            blocks.append(values)
            names.append(str(col))
    if not blocks:
        raise ValueError("design matrix would have no columns")
    X = np.column_stack(blocks).astype(float)
    return Design(X, names, continuous, names.index("a0_z"),
                  names.index(load_column) if load_column else None)


def standardise_in_fold(X: np.ndarray, design: Design, train_mask: np.ndarray,
                        apply_mask: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Standardise continuous columns with **training** moments only."""
    X = X.copy()
    idx = np.flatnonzero(apply_mask) if apply_mask is not None else np.arange(len(X))
    moments: dict[str, dict[str, float]] = {}
    for j in design.continuous:
        train = X[train_mask, j]
        mean = float(np.mean(train))
        sd = float(np.std(train))
        if not np.isfinite(sd) or sd <= 1e-12:
            sd = 0.0
        X[idx, j] = (X[idx, j] - mean) / sd if sd > 0 else (X[idx, j] - mean)
        moments[design.names[j]] = {"mean": mean, "sd": sd}
    return X, moments


def align_test_design(frame: pd.DataFrame, names: Sequence[str],
                      moments: Mapping[str, Mapping[str, float]]) -> np.ndarray:
    """Build a held-out matrix with the *training* column layout and moments."""
    blocks: list[np.ndarray] = []
    for name in names:
        if name == "const":
            blocks.append(np.ones(len(frame)))
        elif "=" in name:
            col, level = name.split("=", 1)
            blocks.append((frame[col].astype(str) == level).to_numpy(float))
        else:
            values = pd.to_numeric(frame[name], errors="coerce").to_numpy(float)
            if name in moments:
                mean = moments[name]["mean"]
                sd = moments[name]["sd"]
                values = (values - mean) / sd if sd > 0 else (values - mean)
            blocks.append(values)
    return np.column_stack(blocks).astype(float)


# --------------------------------------------------------------------------- #
# Folds (§9, §11)
# --------------------------------------------------------------------------- #
def assign_folds(players: Sequence[Any], k: int, seed: int,
                 min_per_fold: int = 1) -> tuple[dict[Any, int], dict]:
    """Player-grouped fold assignment with the §9 reduction rule."""
    uniq = sorted({str(p) for p in players})
    n = len(uniq)
    if n == 0:
        return {}, {"folds": 0, "requested": int(k), "reduced": False,
                    "reason": "no players"}
    folds = int(min(int(k), n))
    reduced = folds != int(k)
    while folds > 1 and (n // folds) < int(min_per_fold):
        folds -= 1
        reduced = True
    rng = np.random.default_rng(int(seed))
    order = list(uniq)
    rng.shuffle(order)
    mapping = {pid: i % folds for i, pid in enumerate(order)}
    info = {
        "folds": folds, "requested": int(k), "reduced": bool(reduced),
        "n_players": n,
        "min_players_per_fold": int(min(n // folds, n)) if folds else 0,
        "reason": ("fold count reduced so every fold has >= "
                   f"{min_per_fold} players") if reduced else "",
    }
    return mapping, info


# --------------------------------------------------------------------------- #
# Hazard ratio
# --------------------------------------------------------------------------- #
def hazard_ratio(fit: SV.Fit, name: str) -> dict:
    """HR per unit of ``name`` with a delta-method interval.

    ``HR = exp(-k * beta)``; the interval uses the joint covariance of ``beta``
    and ``log k``.
    """
    j_b = fit.names.index(name)
    j_k = fit.names.index("log_shape_k")
    beta = float(fit.params[j_b])
    k = float(np.exp(fit.params[j_k]))
    value = float(np.exp(-k * beta))
    log_value = float(-k * beta)
    cov = fit.cov
    if cov is None or not np.isfinite(cov[j_b, j_b]):
        return {"estimate": value, "se": float("nan"), "lo": float("nan"),
                "hi": float("nan"), "log_estimate": log_value,
                "log_se": float("nan")}
    g_b = -k * value
    g_k = -k * beta * value
    var = ((g_b ** 2) * cov[j_b, j_b] + (g_k ** 2) * cov[j_k, j_k]
           + 2.0 * g_b * g_k * cov[j_b, j_k])
    se = float(np.sqrt(max(var, 0.0)))
    return {"estimate": value, "se": se, "lo": float(value - _Z95 * se),
            "hi": float(value + _Z95 * se), "log_estimate": log_value,
            "log_se": float(se / value) if value else float("nan")}


def cox_hr(fit: SV.Fit, name: str) -> dict:
    j = fit.names.index(name)
    beta = float(fit.params[j])
    se = fit.se(name)
    return {"estimate": float(np.exp(beta)),
            "se": float(np.exp(beta) * se) if np.isfinite(se) else float("nan"),
            "lo": float(np.exp(beta - _Z95 * se)),
            "hi": float(np.exp(beta + _Z95 * se)),
            "log_estimate": beta, "log_se": se}


# --------------------------------------------------------------------------- #
# Part 1
# --------------------------------------------------------------------------- #
def _prepare(frame: pd.DataFrame, cfg: Mapping, fields: Sequence[str]) -> pd.DataFrame:
    out = frame.loc[complete_case_mask(frame, fields)].copy()
    out["event"] = pd.to_numeric(out["event"], errors="coerce").fillna(0).astype(int)
    censor = pd.to_numeric(out["censor_time"], errors="coerce").to_numpy(float)
    ttp = pd.to_numeric(out["time_to_pressure"], errors="coerce").to_numpy(float)
    late = (out["event"].to_numpy() == 1) & (~np.isfinite(ttp) | (ttp > censor))
    if np.any(late):
        out.loc[late, "event"] = 0
    ev = out["event"].to_numpy() == 1
    out["observed_time"] = np.where(ev, np.where(np.isfinite(ttp), ttp, censor), censor)
    out = out.loc[out["observed_time"] > 0]
    return out.reset_index(drop=True)


def prepare_frame(frame: pd.DataFrame, cfg: Mapping) -> pd.DataFrame:
    """The P1 analysis frame: complete cases on the §8 control list (§5 rule)."""
    return _prepare(frame, cfg, required_fields(cfg))


def fit_p1(frame: pd.DataFrame, cfg: Mapping) -> dict:
    """P1: Weibull AFT with a log-normal frailty, plus the declared rungs."""
    models = cfg["models"]
    fields = required_fields(cfg)
    absent = design_columns_missing(frame, cfg)
    empty = [f for f in fields if f in frame.columns
             and int(frame[f].notna().sum()) == 0]
    if absent or empty:
        parts = []
        if absent:
            parts.append("columns absent from the snap table: " + ", ".join(absent))
        if empty:
            parts.append("columns with no usable value: " + ", ".join(empty))
        return {"status": "NOT_ESTIMABLE",
                "reason": ("; ".join(parts) + " — the §8 controls are part of the "
                           "specification and are not dropped, so no reduced model "
                           "is substituted"),
                "missing_columns": absent, "empty_fields": empty,
                "n_obs": int(len(frame)), "n_events": 0, "rungs": []}
    data = ensure_a0_z(_prepare(frame, cfg, fields))
    if len(data) == 0:
        return {"status": "NOT_ESTIMABLE",
                "reason": "no complete-case rows on the required field set",
                "n_obs": 0, "n_events": 0, "rungs": []}
    design = build_design(data, cfg)
    X, moments = standardise_in_fold(design.X, design, np.ones(len(data), bool))
    mean = float(moments["a0_z"]["mean"])
    sd = float(moments["a0_z"]["sd"])
    t = data["observed_time"].to_numpy(float)
    event = data["event"].to_numpy(float)
    groups = data["nfl_id"].to_numpy()

    nodes = int(models["frailty_quadrature_nodes"])
    max_iter = int(models["max_iter"])
    seed = int(cfg["seeds"]["model"])
    chunk = int(models.get("quadrature_chunk_rows", 4000))

    rungs: list[dict] = []
    primary = SV.fit_weibull(t, event, X, design.names, frailty="lognormal",
                             nodes=nodes, max_iter=max_iter, seed=seed,
                             chunk_rows=chunk)
    rungs.append({"name": "aft_frailty_lognormal", "used": bool(primary.converged),
                  "converged": bool(primary.converged), "message": primary.message,
                  "loglik": primary.loglik,
                  "estimate": hazard_ratio(primary, "a0_z"),
                  "frailty_sd": primary.extras.get("frailty_sd"),
                  "k": primary.extras.get("k"),
                  "n_iter": primary.extras.get("n_iter")})
    used = "aft_frailty_lognormal" if primary.converged else None
    main_fit = primary if primary.converged else None

    if main_fit is None:                      # fallback ladder, in declared order
        cox = SV.fit_cox(t, event, X, design.names, cluster_groups=groups,
                         max_iter=max_iter)
        rungs.append({"name": "cox_cluster", "used": bool(cox.converged),
                      "converged": bool(cox.converged), "message": cox.message,
                      "loglik": cox.loglik, "estimate": cox_hr(cox, "a0_z")})
        if cox.converged:
            used, main_fit = "cox_cluster", cox
        else:
            aft = SV.fit_weibull(t, event, X, design.names, frailty="none",
                                 cluster_groups=groups, max_iter=max_iter,
                                 seed=seed, chunk_rows=chunk)
            rungs.append({"name": "aft_cluster", "used": bool(aft.converged),
                          "converged": bool(aft.converged), "message": aft.message,
                          "loglik": aft.loglik,
                          "estimate": hazard_ratio(aft, "a0_z")})
            if aft.converged:
                used, main_fit = "aft_cluster", aft

    result: dict[str, Any] = {
        "status": "OK" if main_fit is not None else "FAILED_ALL_RUNGS",
        "rung_used": used,
        "rungs": rungs,
        "n_obs": int(len(data)),
        "n_events": int(event.sum()),
        "event_rate": float(event.mean()),
        "event_rate_below_half": bool(event.mean() < 0.5),
        "n_players": int(data["nfl_id"].nunique()),
        "a0_mean": mean, "a0_sd": sd,
        "standardisation": moments,
        "design_columns": design.names,
        "estimand": "hazard ratio per 1 SD of shrunken a0",
    }
    if main_fit is not None:
        est = (cox_hr(main_fit, "a0_z") if used == "cox_cluster"
               else hazard_ratio(main_fit, "a0_z"))
        result["estimate"] = est
        result["time_ratio"] = float(np.exp(main_fit.params[design.a0_index]))
    result["secondary"] = _p1_secondary(data, design, cfg, moments)
    return result


def _p1_secondary(data: pd.DataFrame, design: Design, cfg: Mapping,
                  moments: Mapping[str, Mapping[str, float]]) -> dict:
    """Logistic mixed model for ``quick_pressure``; LMM for ``player_get_off``."""
    models = cfg["models"]
    nodes = int(models["frailty_quadrature_nodes"])
    max_iter = int(models["max_iter"])
    out: dict[str, Any] = {}

    if "quick_pressure" in data.columns:
        qp = data.loc[data["quick_pressure"].notna()]
        if len(qp) >= 2 and qp["quick_pressure"].nunique() == 2:
            Xq = align_test_design(qp, design.names, moments)
            fit = SV.fit_logit_ri(qp["quick_pressure"].to_numpy(float), Xq,
                                  design.names, qp["nfl_id"].to_numpy(),
                                  nodes=nodes, max_iter=max_iter)
            out["quick_pressure"] = {
                "model": "logistic random intercept",
                "status": "OK" if fit.converged else "NOT_CONVERGED",
                "n_obs": int(len(qp)), "n_events": int(fit.n_events),
                "odds_ratio_per_sd": float(np.exp(fit.param("a0_z"))),
                "se": fit.se("a0_z"),
                "ci": [float(np.exp(fit.param("a0_z") - _Z95 * fit.se("a0_z"))),
                       float(np.exp(fit.param("a0_z") + _Z95 * fit.se("a0_z")))],
                "dropped_columns": fit.extras.get("dropped_columns", []),
                "note": ("non-null quick_pressure rows only (prereg §4: null = "
                         "no pressure charged, not zero)"),
                "message": fit.message,
            }
        else:
            out["quick_pressure"] = {"status": "NOT_ESTIMABLE",
                                     "reason": "fewer than 2 rows or a single outcome level"}

    if "player_get_off" in data.columns:
        go = data.loc[data["player_get_off"].notna()]
        if len(go) >= 2 and go["player_get_off"].nunique() > 1:
            Xg = align_test_design(go, design.names, moments)
            fit = SV.fit_lmm_ri(go["player_get_off"].to_numpy(float), Xg,
                                design.names, go["nfl_id"].to_numpy(),
                                max_iter=max_iter)
            out["player_get_off"] = {
                "model": "linear mixed model, random intercept by rusher",
                "status": "OK" if fit.converged else "NOT_CONVERGED",
                "n_obs": int(len(go)),
                "coef_per_sd": float(fit.param("a0_z")),
                "se": fit.se("a0_z"),
                "ci": [float(fit.param("a0_z") - _Z95 * fit.se("a0_z")),
                       float(fit.param("a0_z") + _Z95 * fit.se("a0_z"))],
                "dropped_columns": fit.extras.get("dropped_columns", []),
                "group_var": fit.extras.get("group_var"),
                "message": fit.message,
            }
        else:
            out["player_get_off"] = {"status": "NOT_ESTIMABLE",
                                     "reason": "fewer than 2 rows or no variance"}
    return out


# --------------------------------------------------------------------------- #
# Part 2: tuning, ladder, CV
# --------------------------------------------------------------------------- #
def _grad_hess_1d(fit: SV.Fit, data: pd.DataFrame, j: int) -> tuple[float, float]:
    """Gradient and curvature of the existing fit in one coefficient."""
    t = data["observed_time"].to_numpy(float)
    event = data["event"].to_numpy(float)
    X = np.asarray(list(data["_X"]), dtype=float) if "_X" in data else None
    if X is None:
        raise ValueError("_grad_hess_1d needs the design matrix in data['_X']")
    h = 1e-4
    ex = fit.extras

    def grad_at(theta: float):
        params = fit.params.copy()
        params[j] = theta
        _, g, _ = SV.weibull_eval(
            params, t, event, X, frailty=ex.get("frailty", "lognormal"),
            random_slope=bool(ex.get("random_slope")),
            correlated=bool(ex.get("correlated")),
            nodes=int(ex.get("nodes", 20)), load=ex.get("_load"),
            need_grad=True)
        return float(g[j])

    theta0 = float(fit.params[j])
    g0 = grad_at(theta0)
    return g0, -(grad_at(theta0 + h) - grad_at(theta0 - h)) / (2.0 * h)


def fit_p2(data: pd.DataFrame, cfg: Mapping, *, load_column: str | None,
           with_cov: bool = True, design: Design | None = None,
           X: np.ndarray | None = None, starts: int | None = None,
           max_iter: int | None = None) -> dict:
    """P2 model for one load definition, with the declared fallback ladder.

    Ladder (prereg §10), attempted in order and logged:
    1. random slope on load by rusher (correlated random effects);
    2. uncorrelated random effects with an alternate optimiser;
    3. per-player slopes with empirical-Bayes shrinkage;
    4. random intercept only (population-level results only).
    """
    models = cfg["models"]
    nodes = int(models["frailty_quadrature_nodes"])
    slope_nodes = int(models.get("slope_quadrature_nodes", nodes))
    cap = int(max_iter if max_iter is not None else models["max_iter"])
    n_starts = int(starts if starts is not None else models.get("p2_starts", 2))
    seed = int(cfg["seeds"]["model"])
    chunk = int(models.get("quadrature_chunk_rows", 4000))
    work = ensure_a0_z(data.copy())
    if design is None or X is None:
        design = build_design(work, cfg, load_column=load_column)
        X, moments = standardise_in_fold(design.X, design, np.ones(len(work), bool))
    else:
        moments = {}
    work["_X"] = list(X)
    t = work["observed_time"].to_numpy(float)
    event = work["event"].to_numpy(float)
    groups = work["nfl_id"].to_numpy()
    attempts: list[dict] = []

    def attempt(name: str, **kw) -> SV.Fit:
        fit = SV.fit_weibull(t, event, X, design.names, nodes=nodes,
                             max_iter=cap, seed=seed, chunk_rows=chunk,
                             with_cov=with_cov, starts=n_starts,
                             slope_nodes=slope_nodes, **kw)
        attempts.append({"name": name, "converged": bool(fit.converged),
                         "message": fit.message,
                         "load_coef": (float(fit.params[design.load_index])
                                       if design.load_index is not None else None),
                         "slope_sd": fit.extras.get("slope_sd")})
        return fit

    if load_column is not None:
        best = attempt("random_slope_correlated", frailty="lognormal",
                       random_slope=True, correlated=True, slope_column=load_column)
        used = "random_slope_correlated"
        if not best.converged:
            best = attempt("uncorrelated_re_alt_optimiser", frailty="lognormal",
                           random_slope=True, correlated=False,
                           slope_column=load_column, method="TNC")
            used = "uncorrelated_re_alt_optimiser"
        eb = None
        if not best.converged:
            best = attempt("random_intercept_plus_eb_slopes", frailty="lognormal",
                           random_slope=False)
            used = "random_intercept_plus_eb_slopes"
            eb = eb_slopes(best, work, cfg) if best.converged else None
        if not best.converged:
            best = attempt("random_intercept_only", frailty="none",
                           cluster_groups=groups)
            used = "random_intercept_only"
            eb = None
    else:
        best = attempt("random_intercept_only", frailty="lognormal")
        used = "random_intercept_only"
        eb = None

    j = design.load_index
    se = best.se(load_column) if j is not None else float("nan")
    k = float(best.extras.get("k", 1.0))
    out = {
        "definition": load_column,
        "rung_used": used,
        "attempts": attempts,
        "converged": bool(best.converged),
        "n_obs": int(len(work)), "n_events": int(event.sum()),
        "load_coef": float(best.params[j]) if (best.converged and j is not None)
        else float("nan"),
        "load_se": se,
        "load_ci": ([float(best.params[j] - _Z95 * se),
                     float(best.params[j] + _Z95 * se)]
                    if (j is not None and np.isfinite(se)) else [None, None]),
        "hazard_ratio_per_sd": (float(np.exp(-k * best.params[j]))
                                if (best.converged and j is not None)
                                else float("nan")),
        "frailty_sd": best.extras.get("frailty_sd"),
        "slope_sd": best.extras.get("slope_sd"),
        "k": k,
        "standardisation": moments,
        "eb_slopes": eb,
        "fit": best,
        "design": design,
        "X": X,
        "data": work,
    }
    return out


def eb_slopes(fit: SV.Fit, data: pd.DataFrame, cfg: Mapping) -> dict:
    """Per-player slopes with empirical-Bayes shrinkage (P2 rung 3)."""
    j = None
    for name in ("load_z",):
        if name in fit.names:
            j = fit.names.index(name)
    if j is None:
        return {"status": "NOT_ESTIMABLE", "reason": "no load coefficient"}
    base = float(fit.params[j])
    min_snaps = int(cfg["models"].get("min_snaps_per_player",
                                      cfg["game"]["min_snaps_per_player"]))
    rows = []
    for pid, grp in data.groupby("nfl_id"):
        if len(grp) < min_snaps:
            continue
        g, h = _grad_hess_1d(fit, grp, j)
        if not np.isfinite(h) or h <= 0:
            continue
        rows.append({"nfl_id": pid, "n_snaps": int(len(grp)),
                     "slope": float(base + g / h),
                     "se": float(np.sqrt(1.0 / h))})
    if not rows:
        return {"status": "NOT_ESTIMABLE", "population_slope": base}
    slopes = np.array([r["slope"] for r in rows])
    ses = np.array([r["se"] for r in rows])
    tau2 = float(max(np.var(slopes, ddof=1) - np.mean(ses ** 2), 0.0))
    for row in rows:
        w = tau2 / (tau2 + row["se"] ** 2) if tau2 > 0 else 0.0
        row["shrunken_slope"] = float(base + w * (row["slope"] - base))
    return {"status": "OK", "population_slope": base, "tau2": tau2,
            "tau": float(np.sqrt(tau2)), "n_players": len(rows), "slopes": rows}


def tune_load(train: pd.DataFrame, grid: Sequence[float], kind: str,
              cfg: Mapping, *, with_cov: bool = False) -> dict:
    """Select one grid value **inside the training fold** by log likelihood.

    The held-out frame is not an argument: this function cannot see it.
    """
    if kind not in ("absolute", "relative"):
        raise ValueError(f"kind must be absolute|relative, got {kind!r}")
    rows = []
    for value in grid:
        column = f"{kind}_load_{_grid_label(value)}"
        if column not in train.columns:
            raise KeyError(f"load panel has no column {column!r}")
        sub = train.loc[train[column].notna()].copy()
        if len(sub) < 10 or sub["event"].sum() == 0:
            rows.append({"value": float(value), "loglik": None,
                         "n_obs": int(len(sub)), "status": "NOT_ESTIMABLE",
                         "rung": None})
            continue
        sub = sub.rename(columns={column: "load_z"})
        fit = fit_p2(sub, cfg, load_column="load_z", with_cov=with_cov,
                     starts=int(cfg["models"].get("cv_starts", 1)),
                     max_iter=int(cfg["models"].get("cv_max_iter", 300)))
        rows.append({"value": float(value),
                     "loglik": float(fit["fit"].loglik) if fit["converged"] else None,
                     "n_obs": int(len(sub)),
                     "status": "OK" if fit["converged"] else "NOT_CONVERGED",
                     "rung": fit["rung_used"]})
    usable = [r for r in rows if r["loglik"] is not None]
    if not usable:
        return {"selected": None, "rows": rows,
                "reason_code": "NO_CANDIDATE_CONVERGED",
                "reason": "no candidate converged in this training fold"}
    best = max(usable, key=lambda r: r["loglik"])
    return {"selected": float(best["value"]), "rows": rows,
            "criterion": "training-fold log likelihood", "kind": kind}


def per_snap_scores(fit: SV.Fit, data: pd.DataFrame, X: np.ndarray) -> np.ndarray:
    """Held-out log predictive density per snap."""
    return SV.log_score(data["observed_time"].to_numpy(float),
                        data["event"].to_numpy(float), X, fit)


def bootstrap_delta(per_snap: pd.DataFrame, reps: int, seed: int) -> dict:
    """Player-clustered bootstrap of the mean per-snap log-score difference."""
    if per_snap.empty:
        # same schema as the estimated case, so a NOT_ESTIMABLE Part 2 still
        # reports a bootstrap block with n_snaps == 0
        return {"estimate": None, "lo": None, "hi": None, "reps": 0,
                "method": "player-clustered bootstrap (players resampled with all snaps)",
                "n_players": 0, "n_snaps": 0}
    point = float(per_snap["delta"].mean())
    by_player = {pid: grp["delta"].to_numpy(float)
                 for pid, grp in per_snap.groupby("nfl_id")}
    keys = sorted(by_player, key=str)
    rng = np.random.default_rng(int(seed))
    draws = np.empty(int(reps))
    for b in range(int(reps)):
        pick = rng.integers(0, len(keys), size=len(keys))
        values = np.concatenate([by_player[keys[i]] for i in pick])
        draws[b] = float(values.mean())
    return {"estimate": point, "lo": float(np.quantile(draws, 0.025)),
            "hi": float(np.quantile(draws, 0.975)), "reps": int(reps),
            "method": "player-clustered bootstrap (players resampled with all snaps)",
            "n_players": len(keys), "n_snaps": int(len(per_snap))}


def cross_validate(data: pd.DataFrame, cfg: Mapping, folds: Mapping[Any, int],
                   *, kind: str, grid: Sequence[float],
                   provenance: Any = None) -> dict:
    """Player-grouped CV for one load definition.

    Tuning happens inside each training fold; the selected value is applied
    unchanged to the held-out fold.  The same folds serve both definitions.

    The return value carries a ``reason`` record (code + sentence) so a
    definition that produces no held-out score says why, rather than leaving
    ``mean_logscore = None`` to be rendered as a bare ``NA``.
    """
    fold_rows = []
    scores = []
    fold_ids = sorted(set(folds.values()))
    for fold in sorted(set(folds.values())):
        test_mask = data["nfl_id"].map(lambda p, f=fold: folds.get(str(p)) == f).to_numpy()
        train = data.loc[~test_mask].copy()
        test = data.loc[test_mask].copy()
        if len(test) == 0 or len(train) == 0:
            fold_rows.append({"fold": int(fold), "n_train": int(len(train)),
                              "n_test": int(len(test)),
                              "n_train_players": int(train["nfl_id"].nunique()),
                              "n_test_players": int(test["nfl_id"].nunique()),
                              "selected": None, "status": "EMPTY_FOLD",
                              "status_code": "EMPTY_FOLD", "candidates": []})
            continue
        tuned = tune_load(train, grid, kind, cfg, with_cov=False)
        row = {"fold": int(fold), "n_train": int(len(train)), "n_test": int(len(test)),
               "n_train_players": int(train["nfl_id"].nunique()),
               "n_test_players": int(test["nfl_id"].nunique()),
               "selected": tuned["selected"], "status": tuned.get("reason", "OK"),
               "status_code": tuned.get("reason_code", "OK"),
               "candidates": tuned["rows"]}
        if tuned["selected"] is None:
            fold_rows.append(row)
            continue
        column = f"{kind}_load_{_grid_label(tuned['selected'])}"
        tr = train.loc[train[column].notna()].rename(columns={column: "load_z"})
        te = test.loc[test[column].notna()].rename(columns={column: "load_z"})
        if len(tr) == 0 or len(te) == 0:
            row["status"] = "NO_ROWS_AT_SELECTED_VALUE"
            row["status_code"] = "NO_ROWS_AT_SELECTED_VALUE"
            fold_rows.append(row)
            continue
        fitted = fit_p2(tr, cfg, load_column="load_z", with_cov=False,
                        starts=int(cfg["models"].get("cv_starts", 1)),
                        max_iter=int(cfg["models"].get("cv_max_iter", 300)))
        if not fitted["converged"]:
            row["status"] = "NOT_CONVERGED"
            row["status_code"] = "NOT_CONVERGED"
            fold_rows.append(row)
            continue
        Xte = align_test_design(te, fitted["design"].names,
                                fitted["standardisation"])
        try:
            ll = per_snap_scores(fitted["fit"], te, Xte)
        except ValueError as exc:
            row["status"] = f"SCORING_FAILED: {exc}"
            row["status_code"] = "SCORING_FAILED"
            fold_rows.append(row)
            continue
        row.update({"n_scored": int(len(ll)), "mean_logscore": float(np.mean(ll)),
                    "rung": fitted["rung_used"]})
        fold_rows.append(row)
        # One row per *scored snap*, carrying the snap keys: the two definitions
        # are compared snap by snap in :func:`run_part2`, so the merge there
        # needs a key finer than (player, fold).  Without it, two snaps of the
        # same player merge into a cross product and the bootstrap interval is
        # computed over pairs of different snaps (defect found and fixed in
        # D78 while exercising Part 2 on the sample).
        payload: dict[str, Any] = {"nfl_id": te["nfl_id"].to_numpy(),
                                   "fold": int(fold), "logscore": ll}
        for key in ("game_id", "play_id"):
            if key in te.columns:
                payload[key] = te[key].to_numpy()
        scores.append(pd.DataFrame(payload))
    combined = (pd.concat(scores, ignore_index=True) if scores
                else pd.DataFrame(columns=["nfl_id", "fold", "logscore"]))
    n_scored = int(len(combined))
    mean_score = float(combined["logscore"].mean()) if n_scored else None
    return {"folds": fold_rows, "scores": combined,
            "mean_logscore": mean_score,
            "reason": _folds_reason(fold_rows, n_scored, len(fold_ids),
                                    mean_score, kind=kind,
                                    provenance=provenance)}


# the finest key a scored snap is identified by — one row per player-snap
SCORE_JOIN_KEYS = ("nfl_id", "fold", "game_id", "play_id")


def _empty_joined_scores() -> pd.DataFrame:
    """The empty result of :func:`join_definition_scores`, with real columns."""
    return pd.DataFrame({
        "nfl_id": pd.Series(dtype="object"),
        "fold": pd.Series(dtype="int64"),
        "game_id": pd.Series(dtype="object"),
        "play_id": pd.Series(dtype="object"),
        "absolute": pd.Series(dtype="float64"),
        "relative": pd.Series(dtype="float64"),
        "delta": pd.Series(dtype="float64"),
    })


def join_definition_scores(absolute: pd.DataFrame,
                           relative: pd.DataFrame) -> pd.DataFrame:
    """Pair the two definitions' held-out scores **snap by snap** (prereg §11).

    Each definition scores the snaps it can score; the comparison is only
    defined on snaps both definitions scored.  The join key is the finest
    shared identity of a scored snap — ``(nfl_id, fold, game_id, play_id)`` —
    never ``(nfl_id, fold)``: a player contributes many snaps to a fold, so
    the coarse key yields a cross product and the bootstrap then compares
    *different* snaps with each other (the D78 defect).

    Sign convention, unchanged from prereg §11: ``delta = relative - absolute``
    in nats per snap; positive favours the relative-load definition.

    The join is required to be one-to-one.  Any row inflation raises instead
    of silently widening the sample, so the defect cannot return through a
    future key or dtype change.  Degenerate inputs (empty frames, missing key
    columns, no shared snaps) return an empty frame with the right columns;
    ``run_part2`` turns that into NOT_ESTIMABLE rather than a crash.
    """
    empty = _empty_joined_scores()
    if absolute is None or relative is None:
        return empty
    if len(absolute) == 0 or len(relative) == 0:
        return empty
    keys = list(SCORE_JOIN_KEYS)
    lost = [k for k in keys
            if k not in absolute.columns or k not in relative.columns]
    if lost:
        # no snap-safe key: refuse the merge rather than fall back to a coarse
        # one and re-create the cross product
        return empty
    if ("logscore" not in absolute.columns) or ("logscore" not in relative.columns):
        return empty

    left = absolute[keys + ["logscore"]].rename(columns={"logscore": "absolute"})
    right = relative[keys + ["logscore"]].rename(columns={"logscore": "relative"})
    for name, frame in (("absolute", left), ("relative", right)):
        repeated = frame.duplicated(subset=keys, keep=False)
        if repeated.any():
            raise ValueError(
                f"{name} scores are not one row per snap: "
                f"{int(repeated.sum())} rows share a {keys} key")
    try:
        merged = left.merge(right, on=keys, how="inner",
                            validate="one_to_one", sort=False)
    except pd.errors.MergeError as exc:  # pragma: no cover - guarded above
        raise ValueError(f"the definition scores do not join one-to-one: {exc}") from exc
    if len(merged) > min(len(left), len(right)):
        raise AssertionError(
            f"join inflated {len(left)} x {len(right)} into {len(merged)} rows")
    merged = merged.reset_index(drop=True)
    merged["delta"] = merged["relative"] - merged["absolute"]
    return merged


def run_part2(frame: pd.DataFrame, cfg: Mapping, *,
              provenance: Any = None) -> dict:
    """Full Part-2 comparison: same folds, both definitions, one bootstrap.

    Every return path carries ``part2_reason_absolute``,
    ``part2_reason_relative`` and ``part2_reason_comparison``: a short code and
    one human sentence saying why that piece does or does not have a number.
    ``provenance`` only controls wording: a FULL-provenance sentence never
    defers to sample mode, a re-run or a larger sample.
    """
    reasons: dict[str, dict[str, str]] = {
        "absolute": _reason("NOT_RUN", "the absolute model was not reached",
                            provenance=provenance),
        "relative": _reason("NOT_RUN", "the relative model was not reached",
                            provenance=provenance),
        "comparison": _reason("NOT_RUN", "the comparison was not reached",
                              provenance=provenance),
    }
    # A raise propagates (a config fault is not an NA), but a returned payload
    # can never lack its three reasons.
    result = _run_part2(frame, cfg, provenance=provenance, reasons=reasons)
    for key, code_key in (("absolute", "part2_reason_absolute"),
                          ("relative", "part2_reason_relative"),
                          ("comparison", "part2_reason_comparison")):
        _assert_reason_is_full_safe(reasons[key], provenance)
        result[code_key] = reasons[key]
    return result


def _not_estimable(reasons: dict[str, dict[str, str]], code: str,
                   message: str, *, provenance: Any = None) -> None:
    """Stamp one root cause on all three reason slots."""
    for key in ("absolute", "relative", "comparison"):
        reasons[key] = _reason(code, message, provenance=provenance)


def _run_part2(frame: pd.DataFrame, cfg: Mapping, *,
               provenance: Any,
               reasons: dict[str, dict[str, str]]) -> dict:
    """The body of :func:`run_part2`; mutates ``reasons`` on every return."""
    models = cfg["models"]
    absent = design_columns_missing(frame, cfg)
    if absent:
        _not_estimable(
            reasons, "MISSING_DESIGN_COLUMNS",
            "the snap frame lacks design column(s) "
            f"{', '.join(absent)}; the §8 specification does not drop controls, "
            "so neither load model can be fitted",
            provenance=provenance)
        return {"status": "NOT_ESTIMABLE",
                "reason": ("columns absent from the snap table: "
                           + ", ".join(absent) + " — the §8 controls are part of the "
                           "specification and are not dropped")}
    prepared = ensure_a0_z(_prepare(frame, cfg, required_fields(cfg)))
    if len(prepared) == 0:
        fields = required_fields(cfg)
        avail = field_availability(frame, fields)
        empty = [f for f, i in avail.items() if i["non_null"] == 0]
        n_rows = int(len(frame))
        n_censor = int((pd.to_numeric(frame.get("censor_time"), errors="coerce")
                        > 0).sum()) if "censor_time" in frame.columns else 0
        if empty:
            why = (f"{', '.join(empty)} ha{'s' if len(empty) == 1 else 've'} no "
                   f"usable value on any of {n_rows} rows")
        else:
            why = (f"every row fails the positive censoring-time requirement "
                   f"({n_censor} of {n_rows} rows have censor_time > 0)")
        _not_estimable(
            reasons, "NO_COMPLETE_CASES",
            f"no row is a complete case for the §8 control list: {why}; Part 2 "
            "needs one row carrying every control and is dropped (prereg §15)",
            provenance=provenance)
        return {"status": "NOT_ESTIMABLE",
                "reason": ("no complete-case rows for the §8 control list; Part 2 "
                           "is dropped and P1 continues (prereg §15)")}
    min_snaps = int(cfg["game"]["min_snaps_per_player"])
    counts = prepared.groupby("nfl_id").size()
    eligible = counts[counts >= min_snaps].index
    data = prepared.loc[prepared["nfl_id"].isin(eligible)].copy()
    if len(data) == 0:
        best = int(counts.max()) if len(counts) else 0
        _not_estimable(
            reasons, "BELOW_MIN_SNAPS",
            f"no rusher reaches the §9 floor of {min_snaps} primary snaps "
            f"(largest rusher: {best} snaps) among {int(counts.size)} rushers; "
            "Part 2 has no estimable player sample",
            provenance=provenance)
        return {"status": "NOT_ESTIMABLE",
                "reason": f"no rusher has >= {min_snaps} primary snaps",
                "n_players": 0}
    mapping, info = assign_folds(data["nfl_id"].unique(), int(models["cv_folds"]),
                                 int(cfg["seeds"]["model"]),
                                 int(models.get("min_rushers_per_fold", 10)))
    data["fold"] = data["nfl_id"].map(lambda p: mapping.get(str(p)))
    grid_abs = [float(g) for g in cfg["load"]["absolute_speed_grid_yds"]]
    grid_rel = [float(g) for g in cfg["load"]["relative_fraction_grid"]]
    if len(grid_abs) != len(grid_rel):
        raise RuntimeError("prereg §7 requires equal tuning budgets: "
                           f"{len(grid_abs)} absolute vs {len(grid_rel)} relative")

    abs_diag = _load_diagnostics(data, "absolute", grid_abs)
    rel_diag = _load_diagnostics(data, "relative", grid_rel)
    abs_cv = cross_validate(data, cfg, mapping, kind="absolute", grid=grid_abs,
                            provenance=provenance)
    rel_cv = cross_validate(data, cfg, mapping, kind="relative", grid=grid_rel,
                            provenance=provenance)
    # A definition whose exposure is entirely NA has a load-build root cause,
    # not a modelling failure; report that instead of "no scored folds".
    for diag, cv in ((abs_diag, abs_cv), (rel_diag, rel_cv)):
        if diag["any_value"] == 0:
            cv["reason"] = _load_reason(diag, int(len(data)),
                                        provenance=provenance)
    reasons["absolute"] = abs_cv["reason"]
    reasons["relative"] = rel_cv["reason"]
    result: dict[str, Any] = {
        "status": "OK", "fold_info": info,
        "n_players": int(data["nfl_id"].nunique()),
        "n_snaps": int(len(data)), "min_snaps_per_player": min_snaps,
        "grids": {"absolute": grid_abs, "relative": grid_rel},
        "equal_tuning_budget": len(grid_abs) == len(grid_rel),
        "absolute": abs_cv, "relative": rel_cv,
    }
    bad = []
    for name, cv in (("absolute", abs_cv), ("relative", rel_cv)):
        mean = cv["mean_logscore"]
        if (mean is None) or (not np.isfinite(float(mean))):
            bad.append(name)
    if bad:
        reasons["comparison"] = _reason(
            "UPSTREAM_NA",
            f"the paired comparison was not reached because the "
            f"{' and '.join(bad)} model produced no usable held-out score",
            provenance=provenance)
        result["status"] = "NOT_ESTIMABLE"
        result["reason"] = "at least one definition produced no held-out scores"
        return result

    merged = join_definition_scores(abs_cv["scores"], rel_cv["scores"])
    if merged.empty:
        n_abs = int(len(abs_cv["scores"]))
        n_rel = int(len(rel_cv["scores"]))
        keys_lost = [k for k in SCORE_JOIN_KEYS
                     if k not in abs_cv["scores"].columns
                     or k not in rel_cv["scores"].columns]
        if keys_lost:
            code = "JOIN_KEYS_MISSING"
            why = (f"the scored frames lack the snap key(s) "
                   f"{', '.join(keys_lost)}")
        else:
            code = "NO_SHARED_SCORED_SNAPS"
            why = (f"the two definitions scored {n_abs} and {n_rel} held-out "
                   "snaps with no snap key in common")
        reasons["comparison"] = _reason(
            code, f"{why}; the paired absolute-vs-relative bootstrap is "
            "undefined", provenance=provenance)
        boot = bootstrap_delta(merged, int(models["bootstrap_reps"]),
                               int(cfg["seeds"]["bootstrap"]))
        result["status"] = "NOT_ESTIMABLE"
        result["reason"] = ("the two definitions share no scored snap on "
                            f"{list(SCORE_JOIN_KEYS)}; the paired bootstrap is "
                            "undefined")
        result.update({"n_scored_snaps": 0, "bootstrap": boot,
                       "difference_nats_per_snap": None,
                       "difference_interval": [None, None]})
        return result
    boot = bootstrap_delta(merged, int(models["bootstrap_reps"]),
                           int(cfg["seeds"]["bootstrap"]))
    soi = float(cfg["validate"]["equivalence"]["soi_logscore"])
    margin = float(cfg["validate"]["equivalence"]["margin_logscore"])
    point = boot["estimate"]
    if point is None or not np.isfinite(float(point)):
        reasons["comparison"] = _reason(
            "NONFINITE_DELTA",
            f"the paired comparison has {int(len(merged))} shared scored snaps "
            "but its mean log-score difference is not finite",
            provenance=provenance)
        result["status"] = "NOT_ESTIMABLE"
        result["reason"] = ("the paired difference is not finite; the "
                            "comparison is not estimable")
        result.update({"n_scored_snaps": int(len(merged)), "bootstrap": boot,
                       "difference_nats_per_snap": None,
                       "difference_interval": [None, None]})
        return result
    reasons["comparison"] = _reason(
        "OK",
        f"paired comparison estimated on {int(len(merged))} snaps that both "
        f"definitions scored, across {int(len(set(merged['fold'])))} fold(s)",
        provenance=provenance)
    excludes_zero = bool(boot["lo"] is not None and boot["hi"] is not None
                         and (boot["lo"] > 0 or boot["hi"] < 0))
    result.update({
        "difference_nats_per_snap": point,
        "difference_interval": [boot["lo"], boot["hi"]],
        "bootstrap": boot, "soi": soi, "equivalence_margin": margin,
        "excludes_zero": excludes_zero,
        "exceeds_soi": bool(point is not None and abs(point) > soi),
        "better_definition": ("relative" if (point or 0) > 0 else "absolute"),
        "verdict": part2_verdict(point, boot, soi, margin),
        "n_scored_snaps": int(len(merged)),
    })
    return result


def part2_verdict(point, boot, soi: float, margin: float) -> str:
    """Prereg §12: a null is informative only if the interval excludes the SOI."""
    if point is None or boot.get("lo") is None:
        return "INCONCLUSIVE"
    lo, hi = boot["lo"], boot["hi"]
    if lo > 0 and point > soi:
        return "RELATIVE_BETTER"
    if hi < 0 and -point > soi:
        return "ABSOLUTE_BETTER"
    if lo > -margin and hi < margin:
        return "EQUIVALENT"
    return "INCONCLUSIVE"


# --------------------------------------------------------------------------- #
# S1 and E1
# --------------------------------------------------------------------------- #
def fit_s1(data: pd.DataFrame, cfg: Mapping, *, load_column: str,
           draws: int | None = None) -> dict:
    """Population load coefficient + load x a0 interaction, naive and corrected."""
    models = cfg["models"]
    n_draws = int(draws if draws is not None else cfg["load"].get("posterior_draws", 200))
    seed = int(cfg["seeds"]["model"])
    work = ensure_a0_z(data.loc[data[load_column].notna()].copy().reset_index(drop=True))
    work["load_z"] = work[load_column]
    if len(work) < 20 or work["event"].sum() == 0:
        return {"status": "NOT_ESTIMABLE", "reason": "too few rows with load"}
    design = build_design(work, cfg, load_column="load_z")
    X, moments = standardise_in_fold(design.X, design, np.ones(len(work), bool))
    inter = X[:, design.a0_index] * X[:, design.load_index]
    Xa = np.column_stack([X, inter])
    names = design.names + ["load_x_a0"]
    t = work["observed_time"].to_numpy(float)
    event = work["event"].to_numpy(float)
    fit = SV.fit_weibull(t, event, Xa, names, frailty="lognormal",
                         nodes=int(models["frailty_quadrature_nodes"]),
                         max_iter=int(models["max_iter"]), seed=seed,
                         chunk_rows=int(models.get("quadrature_chunk_rows", 4000)))
    naive = {
        "load_coef": float(fit.params[design.load_index]),
        "load_se": fit.se("load_z"),
        "interaction_coef": float(fit.params[-1]),
        "interaction_se": fit.se("load_x_a0"),
        "converged": bool(fit.converged),
    }

    a0_se = float(pd.to_numeric(work.get("a0_se"), errors="coerce").mean()) \
        if "a0_se" in work else float("nan")
    corrected: dict[str, Any]
    coefs: list[float] = []
    if np.isfinite(a0_se) and a0_se > 0:
        rng = np.random.default_rng(seed + 1)
        work["_X"] = list(Xa)
        for _ in range(n_draws):
            a0_draw = work["a0"].to_numpy(float) + rng.normal(scale=a0_se,
                                                              size=len(work))
            z = (a0_draw - moments["a0_z"]["mean"]) / (moments["a0_z"]["sd"] or 1.0)
            Xd = Xa.copy()
            Xd[:, design.a0_index] = z
            Xd[:, -1] = z * Xd[:, design.load_index]
            work["_X"] = list(Xd)
            g, h = _grad_hess_1d(fit, work, len(names) - 1)
            if np.isfinite(h) and h > 0:
                coefs.append(float(fit.params[-1] + g / h))
    if coefs:
        corrected = {"interaction_coef": float(np.mean(coefs)),
                     "interaction_sd": float(np.std(coefs, ddof=1)) if len(coefs) > 1 else None,
                     "n_draws": len(coefs),
                     "method": "one-step Newton refit under posterior a0 draws"}
    else:
        corrected = {"status": "NOT_ESTIMABLE",
                     "reason": "a0 SE unavailable or no valid draw"}

    # Model-based slope reliability: the per-rusher slope distribution from the
    # random-intercept model (the same EB step the P2 ladder uses).
    ri_fit = SV.fit_weibull(t, event, Xa[:, :-1], design.names, frailty="lognormal",
                            nodes=int(models["frailty_quadrature_nodes"]),
                            max_iter=int(models["max_iter"]), seed=seed,
                            chunk_rows=int(models.get("quadrature_chunk_rows", 4000)),
                            with_cov=False)
    eb = eb_slopes(ri_fit, work.assign(_X=list(Xa[:, :-1])), cfg)
    reliability = None
    if eb.get("status") == "OK" and eb["slopes"]:
        slopes = np.array([r["slope"] for r in eb["slopes"]])
        ses = np.array([r["se"] for r in eb["slopes"]])
        var = float(np.var(slopes, ddof=1))
        denom = var + float(np.mean(ses ** 2))
        reliability = float(var / denom) if denom > 0 else None
    return {
        "status": "OK" if fit.converged else "NOT_CONVERGED",
        "definition": load_column,
        "naive": naive, "corrected": corrected,
        "reliability": reliability,
        "reliability_definition": "var(slope)/(var(slope)+mean(SE^2))",
        "n_obs": int(len(work)), "n_events": int(event.sum()),
        "n_players": int(work["nfl_id"].nunique()),
        "note": "secondary, not confirmatory",
    }


def fit_e1(data: pd.DataFrame, cfg: Mapping) -> dict:
    """Product-of-coefficients decomposition through ``player_get_off``.

    Descriptive only.  The interval is a player-clustered **multiplier
    bootstrap**: per-player scores from both equations are multiplied by the
    same exponential(1) weights, so the covariance between the two paths is
    preserved without refitting either model.
    """
    models = cfg["models"]
    reps = int(models["bootstrap_reps"])
    seed = int(cfg["seeds"]["bootstrap"]) + 7
    work = ensure_a0_z(data.loc[data["player_get_off"].notna()].copy().reset_index(drop=True))
    if len(work) < 20:
        return {"status": "NOT_ESTIMABLE", "reason": "too few rows with player_get_off"}
    design = build_design(work, cfg)
    X, _ = standardise_in_fold(design.X, design, np.ones(len(work), bool))
    y = work["player_get_off"].to_numpy(float)
    groups = work["nfl_id"].to_numpy()
    lmm = SV.fit_lmm_ri(y, X, design.names, groups,
                        max_iter=int(models["max_iter"]))
    if not lmm.converged or lmm.cov is None:
        return {"status": "NOT_ESTIMABLE",
                "reason": "get-off model did not converge", "message": lmm.message}
    if not np.all(np.isfinite(lmm.cov)):
        return {"status": "NOT_ESTIMABLE",
                "reason": "get-off model covariance is not finite"}

    Xs = np.column_stack([X, y - y.mean()])
    names_s = design.names + ["player_get_off_c"]
    t = work["observed_time"].to_numpy(float)
    event = work["event"].to_numpy(float)
    surv_fit = SV.fit_weibull(t, event, Xs, names_s, frailty="lognormal",
                              nodes=int(models["frailty_quadrature_nodes"]),
                              max_iter=int(models["max_iter"]), seed=seed,
                              chunk_rows=int(models.get("quadrature_chunk_rows", 4000)))
    if not surv_fit.converged or surv_fit.cov is None or not surv_fit.extras.get("hessian_pd", False):
        return {"status": "NOT_ESTIMABLE",
                "reason": ("survival equation has no positive-definite information "
                           "matrix; a multiplier bootstrap would be meaningless"),
                "message": surv_fit.message}
    alpha = float(lmm.param("a0_z"))
    beta = float(surv_fit.params[-1])
    indirect = alpha * beta

    _, _, scores_s = SV.weibull_eval(
        surv_fit.params, t, event, Xs, frailty="lognormal",
        nodes=int(models["frailty_quadrature_nodes"]), need_grad=True,
        need_scores=True)
    Xl = X[:, [design.names.index(n) for n in lmm.names]]
    scores_l = _lmm_scores(lmm, Xl, y)
    hess_l = np.linalg.pinv(lmm.cov)
    hess_s = (np.linalg.pinv(surv_fit.cov) if surv_fit.cov is not None
              else np.zeros((scores_s.shape[1], scores_s.shape[1])))
    uniq = sorted(np.unique(groups), key=str)
    idx = {g: np.flatnonzero(groups == g) for g in uniq}
    rng = np.random.default_rng(seed)
    alphas = np.empty(reps)
    betas = np.empty(reps)
    j_a = lmm.names.index("a0_z")
    j_b = len(names_s) - 1
    for b in range(reps):
        w = rng.exponential(1.0, size=len(uniq))
        s_l = np.zeros(scores_l.shape[1])
        s_s = np.zeros(scores_s.shape[1])
        for k, g in enumerate(uniq):
            rows = idx[g]
            s_l += w[k] * scores_l[rows].sum(axis=0)
            s_s += w[k] * scores_s[rows].sum(axis=0)
        d_l = hess_l @ s_l
        d_s = hess_s @ s_s
        alphas[b] = alpha + d_l[j_a]
        betas[b] = beta + d_s[j_b]
    draws = alphas * betas
    spread = float(np.std(draws))
    if not np.isfinite(spread) or spread > 10.0 * max(abs(indirect), 1e-6) + 1.0:
        return {"status": "NOT_ESTIMABLE",
                "reason": ("multiplier-bootstrap spread is implausible "
                           f"({spread:g}); the decomposition is not reported"),
                "alpha_a0_to_getoff": alpha,
                "beta_getoff_to_hazard": beta,
                "indirect_product": indirect}
    return {
        "status": "OK" if surv_fit.converged else "NOT_CONVERGED",
        "label": "DESCRIPTIVE",
        "alpha_a0_to_getoff": alpha,
        "beta_getoff_to_hazard": beta,
        "indirect_product": indirect,
        "interval": [float(np.quantile(draws, 0.025)),
                     float(np.quantile(draws, 0.975))],
        "reps": reps,
        "method": "player-clustered multiplier bootstrap (same weights, both equations)",
        "n_obs": int(len(work)), "n_players": int(work["nfl_id"].nunique()),
        "assumptions": ("no unmeasured mediator-outcome confounding; get-off measured "
                        "without error; the product is a descriptive decomposition, "
                        "not a causal claim"),
        "note": "exploratory; may not be quoted as a finding",
    }


def _lmm_scores(fit: SV.Fit, X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-observation scores of the LMM fixed effects (Gaussian, known scale)."""
    resid = y - X @ fit.params
    sigma2 = float(np.var(resid, ddof=1)) or 1.0
    return X * (resid / sigma2)[:, None]


# --------------------------------------------------------------------------- #
# Gate (§13, §15)
# --------------------------------------------------------------------------- #
# The four columns §5.4a derives from all-22 tracking.  ``primary_blocker_group``
# is categorical, so it is only ever checked for coverage, never averaged.
_DERIVATION_RANGE_CHECKS = (
    ("derivation_n_rushers_mean", "n_rushers", "n_rushers_mean",
     "§8 derivations: mean n_rushers inside the declared range — a mean outside "
     "it means the geometric rusher rule is misclassifying the defence"),
    ("derivation_n_blockers_mean", "n_blockers", "n_blockers_mean",
     "§8 derivations: mean n_blockers inside the declared range — five linemen "
     "plus any eligible who stays in"),
    ("derivation_blitz_share", "blitz", "blitz_share",
     "§8 derivations: blitz share inside the declared range — blitz is "
     "n_rushers above the declared rusher threshold, not a per-player flag"),
)

_DERIVATION_COLUMNS = ("n_rushers", "blitz", "n_blockers",
                       "primary_blocker_group")

# §5.4a: the sample tracks a handful of players per play, so the derivation has
# no play it can classify.  Every sample-mode item carries this reason verbatim.
_SAMPLE_DERIVATION_REASON = (
    "sample tracking carries 1-4 players per play, so the derivation is NA by "
    "construction (see snaps_summary.json)")

#: D78 (2026-11-02): the 2027 tracking product carries combine prospects only
#: (1.5 players/play on full data).  A play can therefore never reach
#: ``all22_min_players``, so the team-level derivation is not *pending* on a
#: bigger machine — it is unreachable on this dataset.  Saying
#: ``PENDING_FULL_RUN`` would promise a number the full run cannot produce.
_PROSPECT_ONLY_DERIVATION_REASON = (
    "the dataset tracks combine prospects only (1.5 players per play on full "
    "data), so no play carries all22_min_players distinct players at the snap "
    "frame; team-level rush/protection counts are unobservable and this "
    "derivation is not applicable to this product (prereg §8 amendment, D78)")

#: The status a derivation check takes when the product cannot support it.
_NOT_APPLICABLE = "NOT_APPLICABLE"


def _frame_has_derivation_values(frame: pd.DataFrame) -> bool:
    """True when the frame actually carries a usable derived value.

    The product declaration must never excuse a frame that *does* have values:
    data beats the declaration, so a declared prospect-only input that turns out
    to carry all-22 derivations is evaluated on its numbers.
    """
    for column in _DERIVATION_COLUMNS:
        if column in frame.columns and bool(frame[column].notna().any()):
            return True
    return False


def _in_closed_range(value: float | None, bounds: Sequence[float]) -> bool | None:
    """Closed-interval membership; ``None`` when there is no value to place."""
    if value is None or not np.isfinite(value):
        return None
    low, high = (float(b) for b in bounds)
    return bool(low <= value <= high)


def _column_mean(frame: pd.DataFrame, column: str) -> float | None:
    """Mean of one derived column; ``None`` when it is absent or entirely NA."""
    if column not in frame.columns or not len(frame):
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return None if values.empty else float(values.mean())


def _na_share(frame: pd.DataFrame, column: str) -> tuple[float, int]:
    """NA share and non-null count of one column (1.0 / 0 when it is absent)."""
    if column not in frame.columns or not len(frame):
        return 1.0, 0
    non_null = int(frame[column].notna().sum())
    return float(1.0 - non_null / len(frame)), non_null


#: String forms :func:`_expected_mask` accepts as ``True``; everything else is
#: ``False``.  Only the spellings a boolean column actually takes survive a
#: round trip through CSV/parquet are listed, so a stray word cannot silently
#: inflate the gate denominator.
_TRUE_STRINGS: frozenset[str] = frozenset({"true", "1"})


def _expected_mask(series: pd.Series) -> np.ndarray:
    """Boolean mask of the ``derivation_expected`` rows, as a numpy array.

    The column is a boolean in principle, but it can arrive as ``object`` after
    a CSV/parquet round trip.  ``astype(bool)`` is wrong for that case — every
    non-empty string, ``"False"`` included, is truthy — so the coercion is done
    explicitly: bools are used as-is, numeric ``0``/``1`` are read as booleans,
    and the strings ``"true"``/``"false"``/``"1"``/``"0"``
    (case-insensitive, whitespace-stripped) are parsed by value.  Every other
    value, and every NA, is ``False``: an unparseable flag cannot be allowed to
    enlarge the gate denominator.
    """
    if series.dtype == bool:
        return series.to_numpy(dtype=bool, na_value=False)
    numeric = pd.to_numeric(series, errors="coerce")
    mask = numeric.to_numpy(dtype=float, na_value=0.0) == 1.0
    text = series.astype("string").str.strip().str.lower()
    mask |= text.isin(_TRUE_STRINGS).to_numpy(dtype=bool, na_value=False)
    return mask


def derivation_gate(cfg: Mapping, frame: pd.DataFrame, mode: str) -> dict:
    """Distribution checks on the derived columns (§5.4a, §15 vocabulary).

    ``n_rushers``, ``n_blockers`` and ``blitz`` must average inside the ranges in
    ``cfg["game"]["derivation_gate"]`` over **all rows**; the coverage check is
    different: its denominator is the rows that actually *needed* a derivation,
    ``derivation_expected == True``.  A non-rusher is a correct classification,
    not a missing one, so counting it against the NA share would let a table of
    pure non-rushers "pass".  The raw all-row share stays visible, ungated, in
    the ``coverage`` block.

    In ``sample`` mode every item is ``PENDING_FULL_RUN`` with the §5.4a reason,
    even when the frame happens to carry values — the sample tracks 1-4 players
    per play, so the derivation cannot be evaluated and must never read ``PASS``.
    In ``full`` mode a missing, all-NA or out-of-range column is ``FAIL``.

    **Prospect-only product (D78).**  When ``cfg["game"]["tracking_product"]``
    is ``"prospect_only"`` and the frame carries no derived value at all, every
    item reads ``NOT_APPLICABLE`` with the dataset reason: the tracking carries
    combine prospects only, no play can reach ``all22_min_players`` and the
    team-level counts are unobservable.  That is not a waiver of the model's
    control list — a *required* §8 control that is absent, all-NA or empty
    still fails ``gate_evaluate``'s ``missing_required_field`` check.  If a
    frame declared prospect-only nevertheless carries a derived value, the data
    wins and the normal evaluation runs.

    Two vacuity rules, both deliberate: a full-mode frame with no
    ``derivation_expected == True`` row FAILs (an empty expected set can never
    PASS), and a zero-row frame FAILs both the coverage and the side-conflict
    check (no rows cannot establish that nothing went wrong).

    ``derivation_side_conflicts`` counts rows whose derivation reason is
    ``side_inference_ambiguous`` — the residual where only the motion rule would
    have made a player a rusher.  Its ceiling is
    ``max_side_inference_rows`` (declared 0).

    The denominator flag is read by value, not by truthiness: :func:`_expected_mask`
    parses bools, numeric ``0``/``1`` and the strings ``"true"``/``"false"``/
    ``"1"``/``"0"``, so an object-dtype column that arrives from a CSV round
    trip cannot turn the string ``"False"`` into an expected row.
    """
    gate_cfg = cfg["game"]["derivation_gate"]
    max_na = float(gate_cfg["max_na_share"])
    max_side = int(gate_cfg["max_side_inference_rows"])
    n_rows = int(len(frame))
    checks: dict[str, dict] = {}
    product = str(cfg["game"]["tracking_product"])
    # A zero-row frame can establish nothing, including "the product is
    # prospect-only", so it keeps the vacuity FAIL path below.
    prospect_only = (product == "prospect_only" and n_rows > 0
                     and not _frame_has_derivation_values(frame))

    for name, column, key, clause in _DERIVATION_RANGE_CHECKS:
        bounds = list(gate_cfg[key])
        if prospect_only:
            checks[name] = {"status": _NOT_APPLICABLE, "value": None,
                            "threshold": bounds, "clause": clause,
                            "reason": _PROSPECT_ONLY_DERIVATION_REASON,
                            "tracking_product": product, "n": n_rows,
                            "denominator": "all_rows"}
            continue
        if mode == "sample":
            checks[name] = {"status": "PENDING_FULL_RUN", "value": None,
                            "threshold": bounds, "clause": clause,
                            "reason": _SAMPLE_DERIVATION_REASON, "n": n_rows,
                            "denominator": "all_rows"}
            continue
        value = _column_mean(frame, column)
        ok = _in_closed_range(value, bounds)
        check = {"status": "PASS" if ok else "FAIL", "value": value,
                 "threshold": bounds, "clause": clause, "n": n_rows,
                 "denominator": "all_rows"}
        if not ok:
            if column not in frame.columns:
                check["reason"] = (f"column {column!r} is absent from the frame, "
                                   f"so its mean cannot be computed")
            elif value is None:
                check["reason"] = (f"column {column!r} carries no non-NA value "
                                   f"in {n_rows} rows")
            else:
                check["reason"] = (f"mean {value:.4f} of {column!r} lies outside "
                                   f"the closed range [{bounds[0]:g}, {bounds[1]:g}]")
        checks[name] = check

    # Raw all-row coverage, always reported, never the gating denominator.
    all_shares: dict[str, float] = {}
    all_non_null: dict[str, int] = {}
    for column in _DERIVATION_COLUMNS:
        all_shares[column], all_non_null[column] = _na_share(frame, column)
    coverage = {"n_rows_all": n_rows, "na_share_all": all_shares,
                "non_null_all": all_non_null}
    na_clause = ("§8 derivations: every row that required a derivation is "
                 "classified (NA share within the declared bound over "
                 "derivation_expected rows)")

    if prospect_only:
        checks["derivation_na_share"] = {
            "status": _NOT_APPLICABLE, "value": None, "threshold": max_na,
            "clause": na_clause,
            "columns": {c: _NOT_APPLICABLE for c in _DERIVATION_COLUMNS},
            "non_null": {c: int(frame[c].notna().sum())
                         if c in frame.columns else 0
                         for c in _DERIVATION_COLUMNS},
            "denominator": "derivation_expected", "coverage": coverage,
            "reason": _PROSPECT_ONLY_DERIVATION_REASON,
            "tracking_product": product, "n": n_rows}
    elif mode == "sample":
        checks["derivation_na_share"] = {
            "status": "PENDING_FULL_RUN", "value": None, "threshold": max_na,
            "clause": na_clause, "columns": {}, "non_null": {},
            "denominator": "derivation_expected", "coverage": coverage,
            "reason": _SAMPLE_DERIVATION_REASON, "n": n_rows}
    elif "derivation_expected" not in frame.columns:
        checks["derivation_na_share"] = {
            "status": "FAIL", "value": None, "threshold": max_na,
            "clause": na_clause, "columns": {}, "non_null": {},
            "denominator": "derivation_expected", "coverage": coverage,
            "n": 0,
            "reason": ("column 'derivation_expected' is absent, so the gate "
                       "denominator is undefined")}
    else:
        expected = _expected_mask(frame["derivation_expected"])
        n_expected = int(expected.sum())
        shares: dict[str, float] = {}
        non_null: dict[str, int] = {}
        for column in _DERIVATION_COLUMNS:
            if column not in frame.columns:
                shares[column], non_null[column] = 1.0, 0
                continue
            sub = frame.loc[expected, column]
            non_null[column] = int(sub.notna().sum())
            shares[column] = (1.0 - non_null[column] / n_expected
                              if n_expected else 1.0)
        worst = max(shares.values())
        failed = [c for c in _DERIVATION_COLUMNS if shares[c] > max_na]
        check = {
            "status": "FAIL" if failed else "PASS", "value": worst,
            "threshold": max_na, "clause": na_clause,
            "columns": shares, "non_null": non_null,
            "denominator": "derivation_expected", "coverage": coverage,
            "n": n_expected}
        if not n_rows:
            check["status"] = "FAIL"
            check["reason"] = ("the frame carries 0 rows, so there is no "
                               "expected row to evaluate; the gate must not "
                               "pass by vacuity")
        elif not n_expected:
            check["status"] = "FAIL"
            check["reason"] = (
                f"no row carries derivation_expected=True (0 of {n_rows} "
                f"rows); the gate must not pass by vacuity")
        elif failed:
            check["reason"] = (
                f"NA share above the bound {max_na:g} for: "
                + ", ".join(f"{c} ({shares[c]:.3f})" for c in failed))
        checks["derivation_na_share"] = check

    side_clause = ("§5.4a: no row may be made a rusher by the motion rule alone "
                   "(side_inference_ambiguous)")
    if prospect_only:
        # No play is derivable, so the motion residual cannot arise.  Reporting
        # PASS here would be a vacuous pass; it is not applicable for the same
        # reason as the counts.
        checks["derivation_side_conflicts"] = {
            "status": _NOT_APPLICABLE, "value": None, "threshold": max_side,
            "clause": side_clause, "reason": _PROSPECT_ONLY_DERIVATION_REASON,
            "tracking_product": product, "n": n_rows}
    elif mode == "sample":
        checks["derivation_side_conflicts"] = {
            "status": "PENDING_FULL_RUN", "value": None, "threshold": max_side,
            "clause": side_clause, "reason": _SAMPLE_DERIVATION_REASON,
            "n": n_rows}
    elif not n_rows:
        checks["derivation_side_conflicts"] = {
            "status": "FAIL", "value": 0, "threshold": max_side,
            "clause": side_clause, "n": 0,
            "reason": ("the frame carries 0 rows, so the absence of "
                       "side-inference conflicts cannot be established")}
    elif "derivation_reason" not in frame.columns:
        checks["derivation_side_conflicts"] = {
            "status": "FAIL", "value": None, "threshold": max_side,
            "clause": side_clause, "n": n_rows,
            "reason": ("column 'derivation_reason' is absent, so the "
                       "side-inference conflict count is undefined")}
    else:
        count = int(frame["derivation_reason"]
                    .eq("side_inference_ambiguous").sum())
        check = {"status": "PASS" if count <= max_side else "FAIL",
                 "value": count, "threshold": max_side, "clause": side_clause,
                 "n": n_rows}
        if count > max_side:
            check["reason"] = (
                f"{count} row(s) carry derivation_reason "
                f"'side_inference_ambiguous', above the declared maximum "
                f"{max_side}")
        checks["derivation_side_conflicts"] = check

    failing = [k for k, v in checks.items() if v["status"] == "FAIL"]
    pending = [k for k, v in checks.items() if v["status"] == "PENDING_FULL_RUN"]
    not_applicable = [k for k, v in checks.items()
                      if v["status"] == _NOT_APPLICABLE]
    if failing:
        statement = (f"{len(failing)} of {len(checks)} derivation checks fail: "
                     + ", ".join(failing) + ".")
    elif not_applicable:
        statement = (f"Not applicable: {len(not_applicable)} of {len(checks)} "
                     f"derivation checks — " + _PROSPECT_ONLY_DERIVATION_REASON
                     + ". These checks cannot fail a gate that no longer requires "
                       "the columns they guard; a required §8 control that is "
                       "absent still fails the model gate.")
    elif pending:
        statement = (f"Sample mode: {len(pending)} derivation checks are "
                     f"PENDING_FULL_RUN — " + _SAMPLE_DERIVATION_REASON + ".")
    else:
        statement = (f"All {len(checks)} derivation checks pass over {n_rows} "
                     f"rows.")
    if failing:
        status = "BLOCKED"
    elif pending:
        status = "PENDING"
    elif not_applicable:
        status = _NOT_APPLICABLE
    else:
        status = "OK"
    return {"checks": checks, "status": status, "failing": failing,
            "pending": pending, "not_applicable": not_applicable,
            "tracking_product": product, "statement": statement}


def gate_evaluate(cfg: Mapping, *, mode: str, field_info: Mapping[str, dict],
                  frame: pd.DataFrame, p1: Mapping | None,
                  p2: Mapping | None,
                  audit_decision: Mapping | None = None,
                  derivation: Mapping | None = None) -> dict:
    """Evaluate every §15 stopping condition and the §13 thresholds.

    ``derivation`` is the optional output of :func:`derivation_gate`.  When it
    is ``None`` nothing about the derived columns is evaluated, so a caller with
    a frame that carries no derived columns gets exactly the previous result;
    when it is supplied its checks join this dict and its ``FAIL`` /
    ``PENDING_FULL_RUN`` statuses flow through the existing precedence rules.
    """
    audit = cfg["audit"]
    models = cfg["models"]
    checks: dict[str, dict] = {}

    absent = [f for f, info in field_info.items()
              if (not info["present"]) or info["non_null"] == 0]
    full_run_fields = list(models.get("full_run_fields", []))
    deferred = [f for f in absent if f in full_run_fields]
    hard_absent = [f for f in absent if f not in full_run_fields]
    checks["missing_required_field"] = {
        "status": ("PENDING_FULL_RUN" if (deferred and not hard_absent)
                   else ("FAIL" if absent else "PASS")),
        "absent": absent, "supplied_by_full_run": deferred,
        "clause": "§15: missing required field",
    }

    matched = int(frame["nfl_id"].nunique()) if len(frame) else 0
    checks["matched_dl"] = {
        "status": "PASS" if matched >= int(audit["min_matched_dl"]) else "FAIL",
        "value": matched, "threshold": int(audit["min_matched_dl"]),
        "clause": "§15: matched DL < 40",
    }

    median = (float(frame.groupby("nfl_id").size().median()) if len(frame) else 0.0)
    checks["median_primary_snaps"] = {
        "status": "PASS" if median >= float(cfg["game"]["min_snaps_per_player"]) else "FAIL",
        "value": median, "threshold": float(cfg["game"]["min_snaps_per_player"]),
        "clause": "§15: median primary snaps < 100",
    }

    icc = (float(pd.to_numeric(frame["a0_icc"], errors="coerce").dropna().iloc[0])
           if ("a0_icc" in frame and pd.to_numeric(frame["a0_icc"],
                                                   errors="coerce").notna().any())
           else None)
    checks["a0_icc"] = {
        "status": ("PASS" if (icc is not None and icc >= float(cfg["thresholds"]["icc_min"]))
                   else "FAIL"),
        "value": icc, "threshold": float(cfg["thresholds"]["icc_min"]),
        "clause": "§13: icc_min = 0.50 on a0 (never lowered)",
    }

    nulls = (audit_decision or {}).get("counts", {}).get("null_semantics", {})
    checks["null_semantics"] = {
        "status": ("PASS" if nulls.get("time_to_pressure") == "EVENT_ABSENT"
                   else ("PENDING" if not nulls else "FAIL")),
        "value": nulls, "clause": "§15: Part-1 null semantics unresolved",
        "note": "only the P1 primary outcome binds; sack/unblocked are exploratory",
    }

    # §15's MDE condition is evaluable whenever the event count is known.  When
    # P1 could not be fitted the count comes from the snap table and is labelled
    # indicative — the condition is still evaluated rather than skipped.
    mde, basis, events = None, None, 0
    if p1 and p1.get("n_events"):
        events, basis = int(p1["n_events"]), "P1 analysis frame"
    elif len(frame) and "event" in frame.columns:
        events = int(pd.to_numeric(frame["event"], errors="coerce").fillna(0).sum())
        basis = "snap table (indicative: P1 not estimable)"
    if events:
        from dl import dl_audit_power as power
        log_hr = power.mde_log_hr(events, float(audit["alpha"]),
                                  float(audit["power"]),
                                  float(audit["design_effect"]))
        mde = float(np.exp(log_hr))
    checks["part1_mde"] = {
        "status": "PASS" if (mde is not None and mde <= float(audit["max_mde_hr"]))
                  else ("FAIL" if mde is not None else "PENDING"),
        "value": mde, "threshold": float(audit["max_mde_hr"]),
        "events": events, "basis": basis,
        "clause": "§15: Part-1 MDE worse than HR 1.15",
    }

    coverage_ok = bool(p2 and p2.get("status") == "OK")
    checks["snap_coverage"] = {
        "status": "PASS" if coverage_ok else "PENDING_FULL_RUN",
        "clause": ("§15: if only snap coverage fails, Part 2 is dropped and "
                   "P1 continues"),
        "part2_status": (p2 or {}).get("status"),
    }

    # §14: a sample artefact may never be quoted as a result, so sample mode can
    # never reach GO even when every numeric condition happens to pass.  This is
    # what keeps the amendment's NOT_APPLICABLE derivation items from turning a
    # sample run green.
    if mode != "full":
        checks["reportable_provenance"] = {
            "status": "PENDING_FULL_RUN", "value": mode,
            "clause": ("§14: every number in a report comes from a FULL "
                       "artefact; sample output may not be quoted"),
        }

    if derivation is not None:
        checks.update(derivation.get("checks", {}))

    failing = [k for k, v in checks.items() if v["status"] == "FAIL"]
    pending = [k for k, v in checks.items() if v["status"] == "PENDING_FULL_RUN"]
    not_applicable = [k for k, v in checks.items()
                      if v["status"] == _NOT_APPLICABLE]
    # A condition that cannot be evaluated at all (no audit evidence, no events)
    # blocks a confirmatory full run: the prereg's stop rules must be checkable.
    unresolved = [k for k, v in checks.items() if v["status"] == "PENDING"]
    if failing or (mode == "full" and unresolved):
        verdict = "NO-GO"
    elif pending:
        if mode == "sample":
            verdict = "PENDING_FULL_RUN"
        elif set(pending) == {"snap_coverage"}:
            # §15: "if only snap coverage fails, Part 2 is dropped and P1
            # continues" — not a NO-GO, and not a silent GO either.
            verdict = "GO_PART1_ONLY"
        else:
            verdict = "NO-GO"
    else:
        verdict = "GO"
    return {"verdict": verdict, "checks": checks,
            "status": "OK" if not failing else "BLOCKED",
            "failing": failing, "pending": pending, "unresolved": unresolved,
            "not_applicable": not_applicable,
            "part1_estimable": bool(p1 and p1.get("status") == "OK"),
            "part2_estimable": coverage_ok,
            "stopping_conditions_evaluated": True}
