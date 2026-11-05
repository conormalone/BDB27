"""lc_models — within-player leverage model used by the synthetic recovery tests.

The confirmatory estimand in ``TASK.md`` is the population coefficient on
leverage in a within-player model.  This module implements that model with
player fixed effects (the third, most conservative convergence fallback) and
player-clustered standard errors.  It also implements the validation refit in
which leverage is orthogonalised to game time and cumulative load -- the check
that decides whether a leverage effect is really a fatigue/game-clock effect.

The point here is *correctness of the estimator*, not feature richness: the same
functions are what the future 03/04 stages call, so the synthetic worlds test
the real code path rather than a test-only approximation.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import stats

import lc_common as C

# `route_displacement_yd` is deliberately *not* a control.  In tracking it is
# total start-to-end displacement, which is mechanically a function of speed over
# a fixed route duration and therefore post-treatment.  The depth control is
# `break_depth_yd` (geometric depth at the break, `lc_route_depth`, D61) plus the
# `break_found` indicator.  Depth is a control: report it, never interpret it.
DEFAULT_CONTROLS = ("route_family", "time_to_throw_s", "score_margin", "down",
                    "yards_to_go", "prior_efforts", "quarter",
                    "break_depth_yd", "break_found")
TIME_LOAD_CONTROLS = ("game_elapsed_s", "prior_efforts")

#: Convergence fallbacks in the order TASK.md Phase 4 declares them.  The
#: primary fit is rung 0 (uncorrelated random intercept + random slope); each
#: later rung is strictly more conservative.
FALLBACK_ORDER = ("uncorrelated_re", "player_fixed_effects", "random_intercept")

#: Exact stamp written by lc_common in sample mode.  The stage refuses a
#: reportable full-mode fit if the effort table carries this instead of FULL.
SAMPLE_STAMP = "UNVALIDATED SAMPLE OUTPUT"


def _model_control_block(cfg: dict | None) -> dict:
    if cfg is None:
        return {"centre": True}
    try:
        return dict(C.lc(cfg)["route_depth"]["model_control"])
    except (KeyError, TypeError):
        return {"centre": True}


def _models_block(cfg: dict | None) -> dict:
    """Stage tunables (mixed-model iteration cap, variance floor)."""
    if cfg is None:
        return {}
    try:
        return dict(C.lc(cfg).get("models") or {})
    except (KeyError, TypeError):
        return {}


def _numeric_controls(df: pd.DataFrame, controls,
                      cfg: dict | None = None) -> tuple[list[np.ndarray], list[str]]:
    cols, names = [], []
    control_block = _model_control_block(cfg)
    depth_col = control_block.get("depth_column", "break_depth_yd")
    indicator_col = control_block.get("indicator_column", "break_found")
    for name in controls:
        if name == "route_family":
            dummies = pd.get_dummies(df["route_family"].astype(str), drop_first=True,
                                     dtype=float)
            for col in dummies.columns:
                cols.append(dummies[col].to_numpy(dtype=float))
                names.append(f"route={col}")
        elif name == depth_col:
            # Reference coding, not imputation: a route with no detected break
            # contributes 0 on the centred depth term and is carried entirely by
            # the indicator below.  No depth is invented for it.
            if indicator_col not in df.columns:
                continue
            found = df[indicator_col].fillna(False).astype(bool)
            values = pd.to_numeric(df[depth_col], errors="coerce").astype(float)
            values = values.fillna(0.0)
            if bool(control_block.get("centre", True)) and found.any():
                values = values - float(values[found].mean())
            cols.append(values.to_numpy(dtype=float))
            names.append(f"{depth_col}_centred")
        elif name == indicator_col:
            indicator = df[indicator_col].fillna(False).astype(bool)
            cols.append(indicator.astype(float).to_numpy())
            names.append(indicator_col)
        elif name in df.columns:
            values = pd.to_numeric(df[name], errors="coerce").astype(float)
            values = values.fillna(values.median() if values.notna().any() else 0.0)
            sd = values.std(ddof=0)
            cols.append(((values - values.mean()) / (sd if sd > 0 else 1.0)).to_numpy())
            names.append(name)
    return cols, names


def design_matrix(df: pd.DataFrame, leverage_col: str, controls=DEFAULT_CONTROLS,
                  player_col: str = "nfl_id",
                  cfg: dict | None = None) -> tuple[np.ndarray, list[str], int]:
    """Return (X, names, leverage_index) with player fixed effects."""
    players = pd.get_dummies(df[player_col].astype(str), drop_first=True, dtype=float)
    cols = [np.ones(len(df)), df[leverage_col].to_numpy(dtype=float)]
    names = ["intercept", leverage_col]
    numeric, numeric_names = _numeric_controls(df, controls, cfg=cfg)
    cols.extend(numeric)
    names.extend(numeric_names)
    for col in players.columns:
        cols.append(players[col].to_numpy(dtype=float))
        names.append(f"player={col}")
    X = np.column_stack(cols)
    return X, names, 1


def _ols_cluster(df: pd.DataFrame, y: np.ndarray, X: np.ndarray,
                 leverage_idx: int, player_col: str,
                 names: list[str] | None = None) -> dict:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    xtx_inv = np.linalg.pinv(X.T @ X)

    clusters = df[player_col].astype(str).to_numpy()
    meat = np.zeros((k, k), dtype=float)
    for cluster in np.unique(clusters):
        mask = clusters == cluster
        xg = X[mask]
        eg = resid[mask]
        score = xg.T @ eg
        meat += np.outer(score, score)
    g = int(len(np.unique(clusters)))
    if g > 1:
        correction = (g / (g - 1.0)) * ((n - 1.0) / max(1.0, n - k))
    else:
        correction = 1.0
    cov = xtx_inv @ meat @ xtx_inv * correction
    se = float(np.sqrt(max(cov[leverage_idx, leverage_idx], 0.0)))
    b = float(beta[leverage_idx])
    dof = max(1, g - 1)
    t_stat = b / se if se > 0 else float("nan")
    p_value = float(2.0 * stats.t.sf(abs(t_stat), dof)) if np.isfinite(t_stat) else float("nan")
    crit = float(stats.t.ppf(0.975, dof))
    result = {
        "beta": b, "se": se, "t": float(t_stat), "p": p_value,
        "ci_low": b - crit * se, "ci_high": b + crit * se,
        "n": int(n), "n_players": int(g), "n_params": int(k),
        "resid_sd": float(np.std(resid, ddof=1)) if n > 1 else float("nan"),
        "cluster": "player",
    }
    if names is not None:
        result["coefficients"] = {str(name): float(value)
                                  for name, value in zip(names, beta)}
        result["terms"] = _term_table(beta, cov, names, leverage_idx)
    return result


def _term_table(beta: np.ndarray, cov: np.ndarray, names: list[str],
                leverage_idx: int) -> list[dict]:
    """Per-term estimate with the same clustered covariance used for the CI."""
    rows = []
    for index, (name, value) in enumerate(zip(names, beta)):
        if str(name).startswith("player="):
            continue  # fixed-effect dummies are absorbed, not reported
        se = float(np.sqrt(max(cov[index, index], 0.0)))
        rows.append({
            "term": str(name),
            "estimate": float(value),
            "se": se,
            "ci_low": float(value - 1.96 * se),
            "ci_high": float(value + 1.96 * se),
            "role": "estimand" if index == leverage_idx else "control",
        })
    return rows


def _cluster_cov(X: np.ndarray, resid: np.ndarray, clusters: np.ndarray) -> np.ndarray:
    """Player-clustered sandwich covariance (same correction as _ols_cluster)."""
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


def fit_within_player(df: pd.DataFrame, outcome: str = "log_max_speed",
                      leverage_col: str = "leverage_z",
                      controls=DEFAULT_CONTROLS,
                      player_col: str = "nfl_id",
                      cfg: dict | None = None) -> dict:
    """Player-FE OLS with player-clustered SE on the leverage coefficient."""
    needed = [outcome, leverage_col, player_col]
    work = df.dropna(subset=needed).copy()
    if len(work) < 3 or work[player_col].nunique() < 2:
        return {"beta": float("nan"), "se": float("nan"), "t": float("nan"),
                "p": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"),
                "n": int(len(work)), "n_players": int(work[player_col].nunique()),
                "n_params": 0, "resid_sd": float("nan"), "cluster": "player"}
    X, names, idx = design_matrix(work, leverage_col, controls, player_col, cfg=cfg)
    result = _ols_cluster(work, work[outcome].to_numpy(dtype=float), X, idx,
                          player_col, names=names)
    result["model"] = "within_player_fe"
    result["leverage_col"] = leverage_col
    result["controls"] = list(controls)
    return result


def fit_route_depth_control(df: pd.DataFrame, outcome: str = "log_max_speed",
                            leverage_col: str = "leverage_z",
                            controls=DEFAULT_CONTROLS,
                            player_col: str = "nfl_id",
                            cfg: dict | None = None) -> dict:
    """Fit the depth control both INCLUDING and EXCLUDING post-throw breaks.

    The headline fit keeps every detected break; the sensitivity fit drops the
    breaks that happen after the ball is thrown.  The depth coefficient is
    returned for reporting only -- it is a control, never interpreted.
    """
    block = _model_control_block(cfg)
    depth_name = f"{block.get('depth_column', 'break_depth_yd')}_centred"
    indicator_name = block.get("indicator_column", "break_found")

    def _fit(frame: pd.DataFrame) -> dict:
        result = fit_within_player(frame, outcome, leverage_col, controls,
                                   player_col, cfg=cfg)
        coefficients = result.get("coefficients", {})
        result["depth_coef"] = coefficients.get(depth_name, float("nan"))
        result["break_found_coef"] = coefficients.get(indicator_name, float("nan"))
        result["depth_control"] = {
            "depth_column": block.get("depth_column", "break_depth_yd"),
            "indicator_column": indicator_name,
            "centre": bool(block.get("centre", True)),
            "note": "control only; reported, never interpreted",
        }
        return result

    headline = _fit(df)
    if "break_after_throw" in df.columns:
        keep = ~df["break_after_throw"].fillna(False).astype(bool)
        sensitivity = _fit(df.loc[keep])
        sensitivity["dropped_post_throw_breaks"] = int((~keep).sum())
    else:
        sensitivity = _fit(df)
        sensitivity["dropped_post_throw_breaks"] = 0
    return {
        "including_post_throw_breaks": headline,
        "excluding_post_throw_breaks": sensitivity,
    }


def orthogonalise_leverage(df: pd.DataFrame, leverage_col: str = "leverage_z",
                           controls=TIME_LOAD_CONTROLS,
                           player_col: str = "nfl_id") -> pd.Series:
    """Residual of leverage after game time, load and player means are removed.

    This is the time-and-load confound refit required by ``TASK.md`` Phase 5:
    if an apparent leverage effect vanishes when the residual is used, it was a
    fatigue or game-clock effect.
    """
    work = df.copy()
    y = work[leverage_col].astype(float)
    # Demean leverage *and* every control within player.  A global slope plus
    # player intercepts does not remove player-specific time trends; the
    # within-player projection does, which is the confound the design claims to
    # separate.
    groups = work[player_col]
    y_dm = y - y.groupby(groups).transform("mean")
    control_cols = []
    for name in controls:
        if name in work.columns:
            values = pd.to_numeric(work[name], errors="coerce").astype(float)
            values = values.fillna(values.median() if values.notna().any() else 0.0)
            means = values.groupby(groups).transform("mean")
            sds = values.groupby(groups).transform("std")
            sds = sds.where(sds > 0, 1.0)
            control_cols.append((values - means) / sds)
    if not control_cols:
        return y_dm.rename(f"{leverage_col}_orth")
    X = np.column_stack(control_cols)
    beta, *_ = np.linalg.lstsq(X, y_dm.to_numpy(dtype=float), rcond=None)
    resid = y_dm.to_numpy(dtype=float) - X @ beta
    return pd.Series(resid, index=work.index, name=f"{leverage_col}_orth")


def analytic_mde(efforts_per_player: float, cfg: dict | None = None) -> dict:
    """Analytic minimum detectable effect at a given efforts/player.

    ``MDE = (z_{1-alpha/2} + z_{power}) * sigma / sqrt(n_per_player)``.  At the
    configured 40 efforts/player, sigma=0.10 it reproduces D60's **0.0443**
    log-speed.  Reported alongside every interval; never a gate.
    """
    if cfg is None:
        cfg = C.load_config()
    power = C.lc(cfg)["power"]
    alpha = float(power["alpha"])
    beta = 1.0 - float(power["power"])
    z = float(stats.norm.ppf(1.0 - alpha / 2.0) + stats.norm.ppf(1.0 - beta))
    sigma = float(power["assumed_within_player_sd_log_speed"])
    configured_n = float(power["assumed_efforts_per_player"])
    n = max(float(efforts_per_player), 1e-9)
    return {
        "efforts_per_player": float(n),
        "sigma_log_speed": sigma,
        "alpha": alpha,
        "power": float(power["power"]),
        "z": z,
        "mde": z * sigma / np.sqrt(n),
        "configured_efforts_per_player": configured_n,
        "mde_at_configured": z * sigma / np.sqrt(configured_n),
        "smallest_effect_of_interest": float(power["smallest_effect_of_interest"]),
    }


def assert_effort_provenance(df: pd.DataFrame, mode: str,
                             stage: str = "05_models") -> str:
    """Refuse a reportable full-mode fit on a non-FULL effort table.

    Sample mode is allowed (outputs are stamped and non-reportable).  In full
    mode the table must carry ``provenance == "FULL"`` on every row; anything
    else — a sample stamp copied into the full path, a missing column — raises.
    """
    if "provenance" not in df.columns:
        raise RuntimeError(
            f"STAGE {stage!r}: effort table has no provenance column; refusing "
            "to fit (the writer always stamps it).")
    values = {str(v) for v in df["provenance"].dropna().unique()}
    if mode == "full":
        if values != {"FULL"}:
            raise RuntimeError(
                f"STAGE {stage!r}: mode=full but the effort table provenance is "
                f"{sorted(values)}, not ['FULL']. Rebuild 04_efforts on the full "
                "tracking input before fitting.")
        return "FULL"
    if not any(SAMPLE_STAMP in value for value in values):
        raise RuntimeError(
            f"STAGE {stage!r}: sample mode but the effort table is not stamped "
            f"{SAMPLE_STAMP!r} (found {sorted(values)}).")
    return "PLACEHOLDER_SAMPLE"


def _depth_control_columns(work: pd.DataFrame, cfg: dict | None) -> pd.DataFrame:
    """Add the reference-coded depth control used by the mixed-model formula."""
    block = _model_control_block(cfg)
    depth_col = block.get("depth_column", "break_depth_yd")
    indicator_col = block.get("indicator_column", "break_found")
    found = (work[indicator_col].fillna(False).astype(bool)
             if indicator_col in work.columns
             else pd.Series(False, index=work.index))
    depth = (pd.to_numeric(work[depth_col], errors="coerce").astype(float)
             if depth_col in work.columns else pd.Series(np.nan, index=work.index))
    depth = depth.fillna(0.0)
    if bool(block.get("centre", True)) and found.any():
        depth = depth - float(depth[found].mean())
    work["_break_depth_centred"] = depth
    work["_break_found"] = found.astype(float)
    return work


def _formula_terms(controls, cfg: dict | None) -> list[str]:
    block = _model_control_block(cfg)
    depth_col = block.get("depth_column", "break_depth_yd")
    indicator_col = block.get("indicator_column", "break_found")
    terms = []
    for name in controls:
        if name == "route_family":
            continue  # expanded by _prepare_mixed_frame into rf_* columns
        elif name == depth_col:
            terms.append("_break_depth_centred")
        elif name == indicator_col:
            terms.append("_break_found")
        elif name in ("leverage", "leverage_z"):
            continue  # added explicitly by the caller
        else:
            terms.append(name)
    return terms


def _prepare_mixed_frame(df: pd.DataFrame, outcome: str, leverage_col: str,
                         controls, player_col: str,
                         cfg: dict | None) -> pd.DataFrame:
    work = _depth_control_columns(df.copy(), cfg)
    work = work.dropna(subset=[outcome, leverage_col, player_col]).copy()
    for name in controls:
        if name in work.columns and name not in ("route_family",):
            values = pd.to_numeric(work[name], errors="coerce").astype(float)
            work[name] = values.fillna(values.median() if values.notna().any() else 0.0)
    work[player_col] = work[player_col].astype(str)
    if "route_family" in work.columns:
        work["route_family"] = work["route_family"].fillna("UNKNOWN").astype(str)
        # Pre-build the dummies: `C(...)` in a patsy formula would resolve to
        # the lc_common module imported as `C` in this file, not patsy's C().
        dummies = pd.get_dummies(work["route_family"], drop_first=True,
                                 dtype=float)
        for column in dummies.columns:
            work[f"rf_{column}"] = dummies[column].to_numpy(dtype=float)
    return work


def _mixed_result(res, work: pd.DataFrame, outcome: str, leverage_col: str,
                  player_col: str, model_name: str) -> dict:
    """Wrap a MixedLM result with player-clustered SEs on the fixed effects."""
    X = np.asarray(res.model.exog, dtype=float)
    beta = np.asarray(res.fe_params, dtype=float)
    names = list(res.model.exog_names)
    leverage_idx = names.index(leverage_col)
    resid = work[outcome].to_numpy(dtype=float) - X @ beta
    clusters = work[player_col].astype(str).to_numpy()
    cov = _cluster_cov(X, resid, clusters)
    se = float(np.sqrt(max(cov[leverage_idx, leverage_idx], 0.0)))
    b = float(beta[leverage_idx])
    g = int(len(np.unique(clusters)))
    dof = max(1, g - 1)
    t_stat = b / se if se > 0 else float("nan")
    p_value = float(2.0 * stats.t.sf(abs(t_stat), dof)) if np.isfinite(t_stat) else float("nan")
    crit = float(stats.t.ppf(0.975, dof))
    return {
        "beta": b, "se": se, "t": float(t_stat), "p": p_value,
        "ci_low": b - crit * se, "ci_high": b + crit * se,
        "n": int(len(work)), "n_players": g, "n_params": int(X.shape[1]),
        "resid_sd": float(np.std(resid, ddof=1)) if len(work) > 1 else float("nan"),
        "cluster": "player", "model": model_name,
        "leverage_col": leverage_col,
        "terms": _term_table(beta, cov, names, leverage_idx),
    }


def fit_uncorrelated_re(df: pd.DataFrame, outcome: str = "log_max_speed",
                        leverage_col: str = "leverage_z",
                        controls=DEFAULT_CONTROLS,
                        player_col: str = "nfl_id",
                        cfg: dict | None = None) -> dict:
    """Rung 0: (1|player) + (0+leverage|player), uncorrelated, clustered SE."""
    import statsmodels.formula.api as smf

    work = _prepare_mixed_frame(df, outcome, leverage_col, controls, player_col, cfg)
    terms = [leverage_col, *_formula_terms(controls, cfg),
             *[c for c in work.columns if c.startswith("rf_")]]
    formula = f"{outcome} ~ " + " + ".join(terms)
    block = _models_block(cfg)
    max_iter = int(block.get("mixed_max_iter", 500))
    floor = float(block.get("variance_component_floor", 1.0e-8))
    model = smf.mixedlm(formula, work, groups=work[player_col],
                        vc_formula={"leverage_slope": f"0 + {leverage_col}"})
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        res = model.fit(reml=False, method="lbfgs", maxiter=max_iter)
    if not bool(getattr(res, "converged", False)):
        raise RuntimeError("mixed model did not converge")
    vcomp = np.asarray(getattr(res, "vcomp", []), dtype=float)
    if vcomp.size and (not np.all(np.isfinite(vcomp)) or float(np.min(vcomp)) <= floor):
        raise RuntimeError("random-slope variance on the boundary (0)")
    return _mixed_result(res, work, outcome, leverage_col, player_col,
                         "uncorrelated_re")


def fit_random_intercept(df: pd.DataFrame, outcome: str = "log_max_speed",
                         leverage_col: str = "leverage_z",
                         controls=DEFAULT_CONTROLS,
                         player_col: str = "nfl_id",
                         cfg: dict | None = None) -> dict:
    """Rung 2: random intercept only (no random slope), clustered SE."""
    import statsmodels.formula.api as smf

    work = _prepare_mixed_frame(df, outcome, leverage_col, controls, player_col, cfg)
    terms = [leverage_col, *_formula_terms(controls, cfg),
             *[c for c in work.columns if c.startswith("rf_")]]
    formula = f"{outcome} ~ " + " + ".join(terms)
    max_iter = int(_models_block(cfg).get("mixed_max_iter", 500))
    model = smf.mixedlm(formula, work, groups=work[player_col])
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        res = model.fit(reml=False, method="lbfgs", maxiter=max_iter)
    if not bool(getattr(res, "converged", False)):
        raise RuntimeError("random-intercept model did not converge")
    return _mixed_result(res, work, outcome, leverage_col, player_col,
                         "random_intercept")


def fit_ladder(df: pd.DataFrame, outcome: str = "log_max_speed",
               leverage_col: str = "leverage_z",
               controls=DEFAULT_CONTROLS, player_col: str = "nfl_id",
               cfg: dict | None = None) -> dict:
    """Try the declared convergence fallbacks IN ORDER and record what was used."""
    ladder: list[dict] = []
    for rung in FALLBACK_ORDER:
        try:
            result = _RUNG_FITTERS[rung](df, outcome, leverage_col, controls,
                                         player_col, cfg=cfg)
            if not (np.isfinite(result.get("beta", np.nan))
                    and np.isfinite(result.get("se", np.nan))
                    and result.get("se", 0) > 0):
                raise RuntimeError("non-finite estimate or zero clustered SE")
        except Exception as exc:  # noqa: BLE001 - any failed rung falls through
            ladder.append({"rung": rung, "status": "failed",
                           "reason": f"{type(exc).__name__}: {exc}"})
            continue
        ladder.append({"rung": rung, "status": "used"})
        result["fallback_used"] = rung
        result["ladder"] = ladder
        result["controls"] = list(controls)
        return result
    ladder.append({"rung": "none", "status": "failed",
                   "reason": "all declared fallbacks failed"})
    return {"beta": float("nan"), "se": float("nan"), "t": float("nan"),
            "p": float("nan"), "ci_low": float("nan"),
            "ci_high": float("nan"), "n": int(len(df)),
            "n_players": int(df[player_col].nunique()) if len(df) else 0,
            "n_params": 0, "resid_sd": float("nan"), "cluster": "player",
            "fallback_used": None, "ladder": ladder,
            "controls": list(controls)}


#: Rung name -> fitter.  A test monkeypatches this mapping to prove the order.
_RUNG_FITTERS = {
    "uncorrelated_re": fit_uncorrelated_re,
    "player_fixed_effects": fit_within_player,
    "random_intercept": fit_random_intercept,
}


def fit_orthogonalised(df: pd.DataFrame, outcome: str = "log_max_speed",
                       leverage_col: str = "leverage_z",
                       controls=DEFAULT_CONTROLS,
                       player_col: str = "nfl_id",
                       cfg: dict | None = None,
                       orth_controls=None) -> dict:
    """Refit with leverage residualised on game time and cumulative load.

    ``orth_controls`` lets the caller pass the declared configuration list
    (``bdb27_lc.validation.orthogonalisation.controls``); the default keeps the
    historical behaviour for callers that have no config.
    """
    work = df.copy()
    on = tuple(orth_controls) if orth_controls is not None else TIME_LOAD_CONTROLS
    work["_lev_orth"] = orthogonalise_leverage(work, leverage_col, on,
                                               player_col=player_col)
    result = fit_within_player(work, outcome, "_lev_orth", controls, player_col,
                               cfg=cfg)
    result["model"] = "within_player_fe_orthogonalised"
    result["orthogonalised_on"] = list(on)
    return result
