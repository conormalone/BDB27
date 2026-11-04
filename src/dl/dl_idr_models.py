#!/usr/bin/env python3
"""dl_idr_models — Phase 4 **Part 1** (IDR spec): load x rest -> deficit.

Binding spec: ``TASK.md`` Phase 4, Part 1 —

    "Mixed-effects model predicting current snap acceleration based on prior
     load and rest duration."

and the confirmatory estimand is the **rolling-load x inter-snap-rest
interaction on the per-snap acceleration deficit**.

Model
-----
::

    log(deficit_ratio_ij) = b0
        + b_load * z_load_ij + b_rest * z_rest_ij + b_int * (z_load*z_rest)_ij
        + controls
        + u_0j + u_1j * z_load_ij + eps_ij          (rusher j)

* ``deficit_ratio = a0_snap / baseline_a0`` (Phase 3, D80): below 1 is a
  deficit, so ``log(deficit)`` is negative when the rusher is below his
  Combine baseline and a *larger* deficit is a *more negative* outcome.
* ``z_load`` is the rolling kinematic power load (W/kg) over the prior
  ``window`` included snaps; ``z_rest`` is ``inter_snap_rest_s``.  Both are
  standardised on the estimation sample, so ``b_int`` is "the change in
  log-deficit per +1 SD of load at +1 SD of rest, minus the same at -1 SD".
* Sign of a *deficit-worsening* interaction (more load, less rest -> larger
  deficit) is **positive** under this parameterisation when the load main
  effect is negative, because the interaction is a product of z-scores.  The
  reported ``direction`` block states the implied high-load/low-rest contrast
  explicitly so the sign is never read off the raw coefficient alone.

Controls (the spec's fixed list): alignment, blitz, down/distance, score
margin.  A declared control that the product cannot supply is **reported as
unavailable, never silently dropped from the specification** — on the
prospect-only tracking product ``blitz`` is structurally absent (D78).
Continuous controls are standardised on the estimation sample; categorical
controls enter as treatment dummies with the most frequent level as reference.

Inference
---------
The interaction's standard error is **player-clustered** (CR1 sandwich on the
fixed effects, the rusher being the random-effect grouping), with a
game-clustered SE reported where the number of games is estimable.  The naive
i.i.d. SE is reported only as a diagnostic (``se_iid`` /
``design_effect``); it is never the inference.

Convergence ladder (declared order, logged rung by rung)
--------------------------------------------------------
1. ``correlated_random_slopes`` — (1 + z_load | rusher), ``lbfgs``.
2. ``uncorrelated_re_alt_optimiser`` — random intercept + uncorrelated load
   slope (``exog_vc``), ``powell``.
3. ``random_intercept`` — (1 | rusher), ``lbfgs``.
4. ``fixed_effects_only`` — population-level OLS, player-clustered SE.

Every rung carries the interaction term.  A rung is only "used" when it
converged *and* every variance component is strictly above the configured
floor.  If no rung can estimate the interaction the result is
``NOT_ESTIMABLE`` with the reason — the interaction is never dropped.
"""

from __future__ import annotations

import math
import warnings
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats

__all__ = [
    "IDR_DEFAULTS",
    "build_idr_frame",
    "cluster_cov",
    "fit_idr_ladder",
    "rolling_load",
    "run_idr",
]

IDR_DEFAULTS = {
    "windows": {"primary": 3, "sensitivity": [1, 5]},
    "outcome": "log_deficit_ratio",
    "load_column": "rep_kinematic_load_w_per_kg",
    "rest_column": "inter_snap_rest_s",
    "random_effect": "rusher",
    "rungs": ["correlated_random_slopes", "uncorrelated_re_alt_optimiser",
              "random_intercept", "fixed_effects_only"],
    "mixed_max_iter": 500,
    "variance_component_floor": 1.0e-08,
    "min_rows": 100,
    "min_players": 8,
    "min_clusters_game": 10,
    "min_level_n": 20,
    "min_control_coverage": 0.5,
    "alpha": 0.05,
}


# --------------------------------------------------------------------------- #
# Rollıng load (production semantics, parameterised by window)
# --------------------------------------------------------------------------- #
def rolling_load(frame: pd.DataFrame, window: int) -> pd.Series:
    """Sum of ``rep_kinematic_load_w_per_kg`` over the prior ``window``
    *included* own snaps (Phase 3, D80 semantics).

    Reimplements the rule of ``dl_phase3._load_columns`` with the window as a
    parameter so 1 / 3 / 5 are the *same* code path (a test asserts the
    window-3 result equals the Phase-3 column where both exist):

    * order within a player-game is the snaps table order (``game_id``,
      ``nfl_id``, ``play_id``; the merge is stable);
    * a row with ``excluded_from_load`` true is skipped, never summed;
    * a window that contains an excluded snap, or has fewer than ``window``
      prior own snaps, yields NA (never a silently shortened sum).
    """
    window = int(window)
    if window < 1:
        raise ValueError("window must be >= 1")
    out = np.full(len(frame), np.nan, dtype=float)
    if not len(frame):
        return pd.Series(out, index=frame.index, dtype=float)
    rep = pd.to_numeric(frame["rep_kinematic_load_w_per_kg"],
                        errors="coerce").to_numpy(dtype=float)
    if "excluded_from_load" in frame.columns:
        excluded = frame["excluded_from_load"].fillna(True).to_numpy(dtype=bool)
    else:
        excluded = ~np.isfinite(rep)
    order = np.lexsort((frame["play_id"].to_numpy(),
                        frame["nfl_id"].to_numpy(),
                        frame["game_id"].to_numpy()))
    browsed: dict[tuple, list[int]] = {}
    for i in order:
        browsed.setdefault((frame["game_id"].iat[i], frame["nfl_id"].iat[i]),
                           []).append(int(i))
    for key in browsed:
        seq = browsed[key]
        for pos, i in enumerate(seq):
            if excluded[i] or pos < window:
                continue
            win = seq[pos - window:pos]
            if any(excluded[j] for j in win):
                continue
            values = rep[win]
            if not np.all(np.isfinite(values)):
                continue
            out[i] = float(values.sum())
    return pd.Series(out, index=frame.index, dtype=float)


# --------------------------------------------------------------------------- #
# Frame
# --------------------------------------------------------------------------- #
def _cfg(block: Mapping | None) -> dict:
    merged = dict(IDR_DEFAULTS)
    if block:
        merged.update({k: v for k, v in dict(block).items() if v is not None})
    return merged


def _control_plan(work: pd.DataFrame, cfg: Mapping) -> tuple[list[str], list[str], dict]:
    """Split the declared controls into fitted and unavailable, with reasons."""
    declared = dict(cfg.get("controls") or {})
    categorical_all = [str(c) for c in declared.get("categorical", [])]
    continuous_all = [str(c) for c in declared.get("continuous", [])]
    min_share = float(cfg["min_control_coverage"])
    report: dict[str, dict] = {}
    fitted_cat: list[str] = []
    fitted_cont: list[str] = []
    for name in categorical_all + continuous_all:
        if name not in work.columns:
            report[name] = {"status": "ABSENT", "coverage": 0.0,
                            "reason": "declared control is not present in the "
                                      "snap table; reported, not invented"}
            continue
        share = float(work[name].notna().mean()) if len(work) else 0.0
        if share >= min_share:
            (fitted_cat if name in categorical_all else fitted_cont).append(name)
            report[name] = {"status": "fitted", "coverage": share}
        else:
            report[name] = {"status": "UNAVAILABLE", "coverage": share,
                            "reason": ("declared control has fewer than "
                                       f"{min_share:.0%} non-null values on the "
                                       "estimation sample; reported, not fitted"
                                       f" (n_non_null={int(work[name].notna().sum())})")}
    for name in declared.get("absent_ok", []):
        if name not in report:
            report[name] = {"status": "ABSENT",
                            "coverage": 0.0,
                            "reason": "column is not present in the snap table"}
    return fitted_cat, fitted_cont, report


def build_idr_frame(frame: pd.DataFrame, cfg: Mapping, window: int
                    ) -> tuple[pd.DataFrame, dict]:
    """Complete-case frame for one load window, plus the coverage report.

    Nothing is imputed.  A row is used only when the outcome, the windowed
    load and the rest are all measured.
    """
    cfg = _cfg(cfg)
    work = frame.copy()
    # A Phase-3 build always writes these columns.  If one is absent (a snap
    # table from an older build, or Phase 3 did not run) the honest result is
    # NOT_ESTIMABLE with a reason — never a bare KeyError from deep inside the
    # load/rest arithmetic.
    required = [str(cfg["load_column"]), str(cfg["rest_column"]),
                "deficit_ratio"]
    missing = [name for name in required if name not in work.columns]
    if missing:
        report = {
            "window": int(window),
            "n_rows_in": int(len(work)),
            "n_complete": 0,
            "n_players": 0,
            "n_games": 0,
            "missing_columns": missing,
            "status": "NOT_ESTIMABLE",
            "reason": ("snap table is missing the Phase-3 column(s) "
                       f"{', '.join(missing)}; the Part-1 model cannot be "
                       "estimated on this table (Phase 3 did not run)"),
        }
        return work.iloc[0:0].copy(), report
    work["_idr_load"] = rolling_load(work, window)
    work["_idr_rest"] = pd.to_numeric(work[str(cfg["rest_column"])],
                                      errors="coerce")
    ratio = pd.to_numeric(work["deficit_ratio"], errors="coerce")
    usable_ratio = np.isfinite(ratio.to_numpy(dtype=float)) & (ratio > 0)
    work["_idr_y"] = np.where(usable_ratio, np.log(ratio.where(ratio > 0)),
                              np.nan)

    n_total = int(len(work))
    mask = (np.isfinite(work["_idr_y"].to_numpy(dtype=float))
            & np.isfinite(work["_idr_load"].to_numpy(dtype=float))
            & np.isfinite(work["_idr_rest"].to_numpy(dtype=float)))
    work = work.loc[mask].copy()

    report = {
        "window": int(window),
        "n_rows_in": n_total,
        "n_complete": int(len(work)),
        "n_dropped_missing_outcome": int((~np.isfinite(
            pd.to_numeric(frame["deficit_ratio"], errors="coerce")
            .to_numpy(dtype=float))).sum()),
        "n_players": int(work["nfl_id"].nunique()) if len(work) else 0,
        "n_games": int(work["game_id"].nunique()) if len(work) else 0,
    }
    if not len(work):
        report["status"] = "NOT_ESTIMABLE"
        report["reason"] = "no row has a measured outcome, windowed load and rest"
        return work, report

    sd_load = float(work["_idr_load"].std(ddof=0))
    sd_rest = float(work["_idr_rest"].std(ddof=0))
    if not (np.isfinite(sd_load) and sd_load > 0
            and np.isfinite(sd_rest) and sd_rest > 0):
        report["status"] = "NOT_ESTIMABLE"
        report["reason"] = "load or rest has zero variance on the estimation sample"
        return work, report
    work["_z_load"] = (work["_idr_load"] - work["_idr_load"].mean()) / sd_load
    work["_z_rest"] = (work["_idr_rest"] - work["_idr_rest"].mean()) / sd_rest
    work["_z_int"] = work["_z_load"] * work["_z_rest"]
    report["scale"] = {
        "load_mean": float(work["_idr_load"].mean()), "load_sd": sd_load,
        "rest_mean": float(work["_idr_rest"].mean()), "rest_sd": sd_rest,
    }
    report["status"] = "OK" if len(work) >= int(cfg["min_rows"]) else "NOT_ESTIMABLE"
    if report["status"] != "OK":
        report["reason"] = (f"{len(work)} complete rows is below the declared "
                            f"minimum of {int(cfg['min_rows'])}")
    return work, report


# --------------------------------------------------------------------------- #
# Design matrix
# --------------------------------------------------------------------------- #
class Design:
    def __init__(self, X, names, index, scale, work):
        self.X = X
        self.names = names
        self.index = index
        self.scale = scale
        self.work = work

    def column(self, name: str) -> int:
        return self.names.index(name)


def _design(work: pd.DataFrame, cfg: Mapping, report: Mapping) -> Design:
    """Fixed-effects design: intercept, z_load, z_rest, z_int, controls."""
    cfg = _cfg(cfg)
    cat, cont, _ = _control_plan(work, cfg)
    columns = [np.ones(len(work)), work["_z_load"].to_numpy(dtype=float),
               work["_z_rest"].to_numpy(dtype=float),
               work["_z_int"].to_numpy(dtype=float)]
    names = ["intercept", "z_load", "z_rest", "z_load_x_z_rest"]
    scale = {"z_load": 1.0 / report["scale"]["load_sd"],
             "z_rest": 1.0 / report["scale"]["rest_sd"],
             "z_load_x_z_rest": 1.0 / (report["scale"]["load_sd"]
                                       * report["scale"]["rest_sd"])}
    for name in cont:
        values = pd.to_numeric(work[name], errors="coerce").astype(float)
        sd = float(values.std(ddof=0))
        if not np.isfinite(sd) or sd == 0.0:
            continue
        z = (values - values.mean()) / sd
        columns.append(z.to_numpy(dtype=float))
        names.append(f"z_{name}")
        scale[f"z_{name}"] = 1.0 / sd
    for name in cat:
        values = work[name].astype("string").fillna("UNKNOWN")
        counts = values.value_counts()
        levels = [str(level) for level in counts.index
                  if int(counts[level]) >= int(cfg["min_level_n"])]
        if len(levels) < 2:
            continue
        reference = levels[0]
        for level in levels[1:]:
            columns.append((values == level).to_numpy(dtype=float))
            names.append(f"{name}={level}")
            scale[f"{name}={level}"] = 1.0
    X = np.column_stack(columns) if columns else np.empty((len(work), 0))
    return Design(X, names, work.index, scale, work)


# --------------------------------------------------------------------------- #
# Clustered covariance
# --------------------------------------------------------------------------- #
def cluster_cov(X: np.ndarray, resid: np.ndarray, clusters: np.ndarray) -> np.ndarray:
    """CR1 sandwich covariance of the fixed effects (the lc_models correction)."""
    n, k = X.shape
    xtx_inv = np.linalg.pinv(X.T @ X)
    meat = np.zeros((k, k), dtype=float)
    for cluster in np.unique(clusters):
        mask = clusters == cluster
        score = X[mask].T @ resid[mask]
        meat += np.outer(score, score)
    g = int(len(np.unique(clusters)))
    correction = ((g / (g - 1.0)) * ((n - 1.0) / max(1.0, n - k))
                  if g > 1 else 1.0)
    return xtx_inv @ meat @ xtx_inv * correction


def _ols(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta, y - X @ beta


def _inference(beta: np.ndarray, cov: np.ndarray, idx: int, g: int,
               alpha: float) -> dict:
    se = float(np.sqrt(max(cov[idx, idx], 0.0)))
    b = float(beta[idx])
    dof = max(1, g - 1)
    t_stat = b / se if se > 0 else float("nan")
    p_value = float(2.0 * stats.t.sf(abs(t_stat), dof)) if np.isfinite(t_stat) else float("nan")
    crit = float(stats.t.ppf(1.0 - alpha / 2.0, dof))
    return {"coef": b, "se": se, "t": float(t_stat), "p": p_value,
            "ci_low": b - crit * se, "ci_high": b + crit * se, "dof": dof}


# --------------------------------------------------------------------------- #
# Rungs
# --------------------------------------------------------------------------- #
def _mixed_fit(work, design: Design, y: np.ndarray, rung: str, cfg: Mapping):
    from statsmodels.regression.mixed_linear_model import MixedLM

    groups = work["nfl_id"].to_numpy()
    exog = design.X
    floor = float(cfg["variance_component_floor"])
    max_iter = int(cfg["mixed_max_iter"])
    z_load = work["_z_load"].to_numpy(dtype=float)[:, None]
    ones = np.ones((len(work), 1), dtype=float)

    if rung == "correlated_random_slopes":
        model = MixedLM(y, exog, groups=groups,
                        exog_re=np.column_stack([ones[:, 0], z_load[:, 0]]))
        methods = ["lbfgs", "bfgs", "powell"]
    elif rung == "uncorrelated_re_alt_optimiser":
        model = MixedLM(y, exog, groups=groups, exog_re=ones,
                        exog_vc=z_load)
        methods = ["powell", "bfgs", "lbfgs"]
    elif rung == "random_intercept":
        model = MixedLM(y, exog, groups=groups, exog_re=ones)
        methods = ["lbfgs", "bfgs", "powell"]
    else:  # pragma: no cover - guarded by the ladder
        raise ValueError(rung)

    with warnings.catch_warnings(), np.errstate(all="ignore"):
        # MixedLM emits ConvergenceWarning / SingularMatrixWarning through the
        # optimiser retries; the retry loop below is the handler, so the
        # warning text would only duplicate the logged rung record.
        warnings.simplefilter("ignore")
        res, method, failures = None, None, []
        # An optimiser retry inside a rung is not a change of specification:
        # the model is identical, only the numerical path differs.  Which one
        # succeeded is logged in the rung record.
        for attempt in methods:
            try:
                candidate = model.fit(reml=False, method=attempt,
                                      maxiter=max_iter)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{attempt}: {type(exc).__name__}: {exc}")
                continue
            if bool(getattr(candidate, "converged", False)):
                res, method = candidate, attempt
                break
            failures.append(f"{attempt}: not converged (maxiter={max_iter})")
    if res is None:
        raise RuntimeError("MixedLM did not converge on any declared optimiser ("
                           + "; ".join(failures) + ")")
    cov_re = np.asarray(getattr(res, "cov_re", np.zeros((0, 0))), dtype=float)
    if cov_re.size:
        diag = np.diag(cov_re)
        if not np.all(np.isfinite(diag)) or float(np.min(diag)) <= floor:
            raise RuntimeError(
                "random-effect variance on the boundary or non-finite "
                f"(min diag {float(np.nanmin(diag)):.3e} <= floor {floor:.1e})")
    beta = np.asarray(res.fe_params, dtype=float)
    if not np.all(np.isfinite(beta)):
        raise RuntimeError("non-finite fixed-effect estimates")
    return beta, exog, {"optimiser": method, "converged": True,
                        "optimiser_failures": failures,
                        "cov_re_diag": [float(v) for v in np.diag(cov_re)]
                        if cov_re.size else []}


def fit_idr_ladder(work: pd.DataFrame, report: Mapping, cfg: Mapping
                   ) -> dict:
    """Fit the declared rungs in order; the first usable rung is the estimate."""
    cfg = _cfg(cfg)
    design = _design(work, cfg, report)
    names = design.names
    idx = design.column("z_load_x_z_rest")
    y = work["_idr_y"].to_numpy(dtype=float)
    players = work["nfl_id"].to_numpy()
    g_players = int(work["nfl_id"].nunique())
    games = work["game_id"].to_numpy()
    g_games = int(work["game_id"].nunique())
    alpha = float(cfg["alpha"])
    floor = float(cfg["variance_component_floor"])
    min_rows = max(int(cfg["min_rows"]), design.X.shape[1] + 10)

    ladder: list[dict] = []
    if len(work) < min_rows:
        return {"status": "NOT_ESTIMABLE", "rung_used": None, "ladder": ladder,
                "reason": f"{len(work)} complete rows < required {min_rows}",
                "window": int(report["window"])}
    if g_players < int(cfg["min_players"]):
        return {"status": "NOT_ESTIMABLE", "rung_used": None, "ladder": ladder,
                "reason": f"{g_players} rushers < declared minimum "
                          f"{int(cfg['min_players'])}",
                "window": int(report["window"])}

    used = None
    for rung in [str(r) for r in cfg["rungs"]]:
        try:
            if rung == "fixed_effects_only":
                beta, resid = _ols(design.X, y)
                info = {"optimiser": "lstsq", "converged": True,
                        "cov_re_diag": []}
                if not np.all(np.isfinite(beta)):
                    raise RuntimeError("non-finite OLS estimates")
                rank = int(np.linalg.matrix_rank(design.X))
                if rank < design.X.shape[1]:
                    raise RuntimeError("design matrix is rank deficient")
            else:
                beta, exog, info = _mixed_fit(work, design, y, rung, cfg)
                resid = y - exog @ beta
            cov = cluster_cov(design.X, resid, players)
            est = _inference(beta, cov, idx, g_players, alpha)
            if not (np.isfinite(est["coef"]) and np.isfinite(est["se"])
                    and est["se"] > 0):
                raise RuntimeError("non-finite interaction estimate or zero clustered SE")
        except Exception as exc:  # noqa: BLE001 - any failed rung falls through
            ladder.append({"rung": rung, "status": "failed",
                           "reason": f"{type(exc).__name__}: {exc}"})
            continue
        ladder.append({"rung": rung, "status": "used", **info})
        used = {"rung": rung, "beta": beta, "cov": cov, "est": est,
                "resid": resid, "design": design}
        break

    if used is None:
        return {"status": "NOT_ESTIMABLE", "rung_used": None, "ladder": ladder,
                "reason": "every declared rung failed; the interaction was "
                          "never dropped to force a fit",
                "window": int(report["window"]), "n_snaps": int(len(work)),
                "n_players": g_players}

    design = used["design"]
    beta, cov = used["beta"], used["cov"]
    idx = design.column("z_load_x_z_rest")
    est = used["est"]

    # --- clustered inference: player (primary) and game (where estimable) ---
    per_cluster = {}
    for name, clusters, g in (("player", players, g_players),
                              ("game", games, g_games)):
        if name == "game" and g < int(cfg["min_clusters_game"]):
            per_cluster[name] = {
                "status": "NOT_ESTIMABLE", "n_clusters": g,
                "reason": f"{g} clusters < declared minimum "
                          f"{int(cfg['min_clusters_game'])}: a cluster-robust "
                          f"SE with too few clusters is not reported as if it were"}
            continue
        c_cov = cluster_cov(design.X, used["resid"],
                            np.asarray(clusters))
        per_cluster[name] = {"status": "OK", "n_clusters": g,
                             **_inference(beta, c_cov, idx, g, alpha)}

    # --- naive i.i.d. SE (diagnostic only) ---------------------------------
    sigma2 = float(np.sum(used["resid"] ** 2) / max(1, len(work) - design.X.shape[1]))
    naive_cov = np.linalg.pinv(design.X.T @ design.X) * sigma2
    iid = _inference(beta, naive_cov, idx, len(work), alpha)
    design_effect = (per_cluster["player"]["se"] / iid["se"]
                     if iid["se"] > 0 and per_cluster["player"]["status"] == "OK"
                     else float("nan"))

    scale = design.scale
    terms = []
    for j, name in enumerate(design.names):
        if name == "intercept":
            continue
        factor = float(scale.get(name, 1.0))
        se = float(np.sqrt(max(cov[j, j], 0.0)))
        dof = max(1, g_players - 1)
        t_stat = float(beta[j]) / se if se > 0 else float("nan")
        crit = float(stats.t.ppf(1.0 - alpha / 2.0, dof))
        terms.append({
            "term": name,
            "coef_std": float(beta[j]), "se_std": se,
            "p_std": float(2.0 * stats.t.sf(abs(t_stat), dof))
            if np.isfinite(t_stat) else None,
            "coef_raw": float(beta[j]) * factor, "se_raw": se * factor,
            "ci_low_raw": (float(beta[j]) - crit * se) * factor,
            "ci_high_raw": (float(beta[j]) + crit * se) * factor,
            "raw_unit": _raw_unit(name),
            "is_interaction": name == "z_load_x_z_rest",
        })

    # The implied contrast a reader actually cares about: change in log-deficit
    # per +1 SD of load, at low (-1 SD) vs high (+1 SD) rest.
    b_load = float(beta[design.column("z_load")])
    b_int = float(beta[idx])
    contrast = {
        "definition": ("interaction effect: (slope of log-deficit on load at "
                       "+1 SD rest) - (same at -1 SD rest) = 2 * b_int"),
        "log_deficit_per_sd_load_at_low_rest": b_load - b_int,
        "log_deficit_per_sd_load_at_high_rest": b_load + b_int,
        "difference": 2.0 * b_int,
        "interpretation": ("a NEGATIVE difference means load hurts the deficit "
                           "MORE when rest is short (the fatigue direction)"),
    }

    result = {
        "status": "OK",
        "window": int(report["window"]),
        "rung_used": used["rung"],
        "rungs_intended": [str(r) for r in cfg["rungs"]],
        "ladder": ladder,
        "n_snaps": int(len(work)),
        "n_players": g_players,
        "n_games": g_games,
        "scale": dict(report["scale"]),
        "interaction": {
            "coef_std": est["coef"], "se_std": est["se"], "t": est["t"],
            "p": est["p"], "ci_low_std": est["ci_low"],
            "ci_high_std": est["ci_high"],
            "coef_raw": est["coef"] / (report["scale"]["load_sd"]
                                       * report["scale"]["rest_sd"]),
            "se_raw": est["se"] / (report["scale"]["load_sd"]
                                   * report["scale"]["rest_sd"]),
            "raw_unit": "log-deficit per (W/kg x s)",
            "cluster": "player", "dof": est["dof"],
        },
        "contrast": contrast,
        "cluster": per_cluster,
        "se_iid": iid["se"], "p_iid": iid["p"],
        "design_effect": float(design_effect),
        "design_effect_note": ("player-clustered SE / naive i.i.d. SE; >1 means "
                               "the i.i.d. inference would over-state precision"),
        "terms": terms,
    }
    return result


def _raw_unit(name: str) -> str:
    if name == "z_load":
        return "log-deficit per W/kg"
    if name == "z_rest":
        return "log-deficit per s"
    if name == "z_load_x_z_rest":
        return "log-deficit per (W/kg x s)"
    if name.startswith("z_"):
        return f"log-deficit per {name[2:]} unit"
    return "log-deficit (categorical contrast)"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def run_idr(frame: pd.DataFrame, block: Mapping | None, mode: str = "sample"
            ) -> dict[str, Any]:
    """Fit the primary window and the sensitivity windows from one code path."""
    cfg = _cfg(block)
    windows = dict(cfg.get("windows") or IDR_DEFAULTS["windows"])
    primary = int(windows.get("primary", 3))
    sensitivity = [int(w) for w in windows.get("sensitivity", [1, 5])]

    def _one(window: int) -> dict:
        if "deficit_ratio" not in frame.columns:
            return {"status": "NOT_ESTIMABLE", "window": window,
                    "reason": "snap table has no deficit_ratio column "
                              "(Phase 3 did not run)"}
        work, report = build_idr_frame(frame, cfg, window)
        if report.get("status") != "OK":
            out = {"status": "NOT_ESTIMABLE", "window": window,
                   "reason": report.get("reason"), "frame": report}
            return out
        fit = fit_idr_ladder(work, report, cfg)
        fit["frame"] = report
        fit["controls"] = _control_plan(work, cfg)[2]
        return fit

    primary_fit = _one(primary)
    panel = [primary_fit] + [_one(w) for w in sensitivity if w != primary]
    return {
        "estimand": ("rolling kinematic load x inter-snap rest interaction on "
                     "log(deficit_ratio)"),
        "spec": ("TASK.md Phase 4 Part 1: mixed-effects model of current-snap "
                 "acceleration on prior load and rest"),
        "outcome": "log(deficit_ratio)",
        "primary_window": primary,
        "sensitivity_windows": [w for w in sensitivity if w != primary],
        "window_choice": ("primary window fixed at 3 prior snaps by config "
                          "(Phase 3 D80); 1 and 5 are a declared sensitivity "
                          "panel, never selected by looking at the data"),
        "primary": primary_fit,
        "panel": panel,
        "mode": mode,
        "sample_output": mode != "full",
        "no_number_may_be_quoted": mode != "full",
    }
