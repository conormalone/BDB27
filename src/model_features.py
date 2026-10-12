#!/usr/bin/env python3
"""model_features.py — shared quantile-regression + covariate helpers for Phases 4-5.

Used by `04_models.py` (Phase 4) and `05_validate.py` (Phase 5). Keeps the statistical
plumbing (design matrix, quantile fit, player-clustered bootstrap, player-grouped folds,
pinball loss) in one config-driven place — no magic numbers in the stages themselves.

Design rules (TASK.md):
* config-driven — every numeric knob lives in `config.yaml` (`models.*`, `validate.*`);
* numeric covariates stay in their natural units (interpretable slopes): in-game effort
  speed (yd/s) ~ Combine ceiling (yd/s) + controls;
* the **primary** Combine feature is never dropped by collinearity pruning — controls are
  dropped first, and any dropped column is reported (never a silent failure).
"""
from __future__ import annotations

import warnings
from typing import Any, Sequence

import numpy as np

import common

# Categorical control columns are detected from the data (any non-numeric value => categorical);
# this map only pins the *intended* type so a degenerate all-numeric level set is still treated
# as a category (e.g. a numeric-looking draft tier is left as given by the config list).
KNOWN_CATEGORICAL = {"position", "draft_position"}


# --------------------------------------------------------------------------- #
# per-player covariates
# --------------------------------------------------------------------------- #
def bmi(height_in: float, weight_lb: float, constant: float) -> float:
    """Body-mass index: ``constant * weight_lb / height_in**2`` (constant from config)."""
    if not (np.isfinite(height_in) and np.isfinite(weight_lb)) or height_in <= 0:
        return float("nan")
    return float(constant) * float(weight_lb) / (float(height_in) ** 2)


def _year_of(birth_date: Any) -> float:
    """Parse the (leading) 4-digit year from a birth-date string; NaN when unusable."""
    if birth_date is None:
        return float("nan")
    s = str(birth_date).strip()
    if len(s) < 4 or not s[:4].isdigit():
        return float("nan")
    return float(int(s[:4]))


def covariate_table(cr_records: Sequence[dict], player_records: Sequence[dict], cfg: dict) -> dict[int, dict]:
    """Build the per-player covariate table from `combine_results` + `players` records.

    Returns ``{nfl_id: {position, height, weight, age, draft_position, bmi, optout}}``.
    ``age`` is ``draft_year - birth_year`` (age at the Combine); ``draft_position`` is the draft
    round tier (``R1..R7``) with **``undrafted`` as an explicit level** (prereg §8); ``bmi`` uses
    ``models.bmi_constant``. ``optout`` flags a player missing any headline Combine field
    (`validate.optout_fields`) — reported and usable by the `exclude_optouts` sensitivity.
    """
    m = cfg["models"]
    optout_fields = list(cfg["validate"]["optout_fields"])
    draft_year_by: dict[int, float] = {}
    birth_by: dict[int, Any] = {}
    round_by: dict[int, Any] = {}
    for r in player_records:
        try:
            nid = int(r["nfl_id"])
        except (KeyError, TypeError, ValueError):
            continue
        draft_year_by[nid] = _num(r.get("draft_year"))
        birth_by[nid] = r.get("birth_date")
        round_by[nid] = r.get("draft_round")

    out: dict[int, dict] = {}
    for r in cr_records:
        try:
            nid = int(r["nfl_id"])
        except (KeyError, TypeError, ValueError):
            continue
        height = _num(r.get("combine_height"))
        weight = _num(r.get("combine_weight"))
        dy = draft_year_by.get(nid, float("nan"))
        by = _year_of(birth_by.get(nid))
        age = dy - by if (np.isfinite(dy) and np.isfinite(by)) else float("nan")
        dr = round_by.get(nid)
        if dr is None or str(dr).strip() == "" or str(dr).upper() == "NA":
            draft_position = "undrafted"
        else:
            try:
                draft_position = f"R{int(float(dr))}"
            except (TypeError, ValueError):
                draft_position = "undrafted"
        optout = any(_is_missing(r.get(f)) for f in optout_fields)
        out[nid] = dict(
            position=(str(r.get("combine_position")) if r.get("combine_position") is not None else None),
            height=height, weight=weight, age=age, draft_position=draft_position,
            bmi=bmi(height, weight, float(m["bmi_constant"])), optout=bool(optout),
        )
    return out


def load_covariates(con, cr_path, players_path, cfg: dict) -> dict[int, dict]:
    """Query `combine_results` + `players` (Parquet) and return the covariate table."""
    cr = con.execute(
        f"SELECT nfl_id, combine_position, combine_height, combine_weight, "
        f"{', '.join('\"%s\"' % f for f in cfg['validate']['optout_fields'])} "
        f"FROM {common.pq(cr_path)}").fetchall()
    cols = ["nfl_id", "combine_position", "combine_height", "combine_weight"] + list(cfg["validate"]["optout_fields"])
    cr_records = [dict(zip(cols, row)) for row in cr]
    pl = con.execute(
        f"SELECT nfl_id, draft_year, birth_date, draft_round FROM {common.pq(players_path)}").fetchall()
    player_records = [dict(zip(["nfl_id", "draft_year", "birth_date", "draft_round"], row)) for row in pl]
    return covariate_table(cr_records, player_records, cfg)


# --------------------------------------------------------------------------- #
# design matrix
# --------------------------------------------------------------------------- #
def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.upper() == "NA"


def build_design(rows: Sequence[dict], primary_key: str | None, control_keys: Sequence[str], cfg: dict):
    """Return ``(y, Z, names, dropped, primary_index)`` for a quantile design.

    ``Z`` columns are ``[const, primary, <controls>]`` (or ``[const, <controls>]`` when
    ``primary_key is None`` — the **controls-only baseline**). Numeric-looking controls enter as a
    single column; category-like controls enter one-hot with the alphabetically-first level as
    the reference (the intercept absorbs it). Constant columns and any column that would make the
    design rank-deficient are dropped **from the end** — so the primary feature is protected —
    and every drop is recorded in ``dropped`` (name, reason). Raises ``ValueError`` when the
    primary feature itself has no variation.
    """
    m = cfg["models"]
    n = len(rows)
    if n == 0:
        raise ValueError("empty design")
    y = np.array([_num(r["y"]) for r in rows], float)
    if not np.all(np.isfinite(y)):
        raise ValueError("non-finite response")

    if primary_key is None:
        names = ["const"]
        cols = [np.ones(n)]
        pidx: int | None = None
    else:
        prim = np.array([_num(r[primary_key]) for r in rows], float)
        if not np.all(np.isfinite(prim)) or np.allclose(prim, prim[0]):
            raise ValueError(f"primary feature '{primary_key}' has no usable variation")
        names = ["const", primary_key]
        cols = [np.ones(n), prim]
        pidx = 1
    ctrl_start = len(names)  # first control column index
    dropped: list[tuple[str, str]] = []
    for key in control_keys:
        vals = [r.get(key, None) for r in rows]
        categorical = key in KNOWN_CATEGORICAL or any(
            isinstance(v, str) and not _looks_numeric(v) for v in vals)
        if not categorical:
            col = np.array([_num(v) for v in vals], float)
            if not np.all(np.isfinite(col)):
                dropped.append((key, "missing values"))
                continue
            if np.allclose(col, col[0]):
                dropped.append((key, "constant"))
                continue
            names.append(key)
            cols.append(col)
        else:
            levels = sorted({_cat(v) for v in vals})
            if len(levels) <= 1:
                dropped.append((key, "constant"))
                continue
            for lv in levels[1:]:
                names.append(f"{key}={lv}")
                cols.append(np.array([1.0 if _cat(v) == lv else 0.0 for v in vals], float))

    Z = np.column_stack(cols)
    # rank-prune from the end (never below the intercept + protected primary)
    while Z.shape[1] > ctrl_start and np.linalg.matrix_rank(Z) < Z.shape[1]:
        dropped.append((names[-1], "rank-deficient"))
        names = names[:-1]
        Z = Z[:, :-1]
    if np.linalg.matrix_rank(Z) < Z.shape[1]:  # const (+ primary) degenerate
        raise ValueError("design not full rank after pruning")
    # VIF-prune near-collinear CONTROLS (never the primary): a player-level control set can be
    # nearly redundant with the ceiling on a thin fixture, making the slope unstable. Reuse the
    # pre-registered redundancy ceiling (envelope.vif_max) unless `models.control_vif_max` is set.
    vif_max = float(m.get("control_vif_max", cfg["envelope"]["vif_max"]))
    Z, names, pdrops = _prune_controls(Z, names, vif_max, ctrl_start)
    dropped += pdrops
    return y, Z, names, dropped, pidx


def _control_vifs(Z: np.ndarray, ctrl_start: int) -> list[float]:
    """VIF for each control column (index >= ``ctrl_start``) given the other columns."""
    n, p = Z.shape
    out: list[float] = []
    for j in range(ctrl_start, p):
        ycol = Z[:, j]
        rest = np.delete(Z, j, axis=1)
        beta, *_ = np.linalg.lstsq(rest, ycol, rcond=None)
        resid = ycol - rest @ beta
        sst = float(np.sum((ycol - ycol.mean()) ** 2))
        r2 = 1.0 - float(resid @ resid) / sst if sst > 0 else 0.0
        out.append(float("inf") if r2 >= 1.0 - 1e-12 else 1.0 / (1.0 - r2))
    return out


def _prune_controls(Z: np.ndarray, names: list[str], vif_max: float, ctrl_start: int
                    ) -> tuple[np.ndarray, list[str], list[tuple[str, str]]]:
    """Iteratively drop the control (index >= ``ctrl_start``) with the largest VIF above ``vif_max``.

    The intercept and the primary feature (index 1) are never dropped; at least one control is
    kept. Returns the pruned ``(Z, names, drops)``.
    """
    names = list(names)
    dropped: list[tuple[str, str]] = []
    while Z.shape[1] > ctrl_start + 1:  # keep the protected prefix + >=1 control
        vifs = _control_vifs(Z, ctrl_start)
        worst = int(np.argmax(vifs))
        if not (vifs[worst] > vif_max):
            break
        j = worst + ctrl_start
        dropped.append((names[j], f"VIF>{vif_max:g} (vif={vifs[worst]:.2f})"))
        names.pop(j)
        Z = np.delete(Z, j, axis=1)
    # allow a final drop down to the protected prefix if the sole control is still redundant
    if Z.shape[1] == ctrl_start + 1:
        vifs = _control_vifs(Z, ctrl_start)
        if vifs and vifs[0] > vif_max:
            dropped.append((names[ctrl_start], f"VIF>{vif_max:g} (vif={vifs[0]:.2f})"))
            names.pop(ctrl_start)
            Z = np.delete(Z, ctrl_start, axis=1)
    return Z, names, dropped


def _looks_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _cat(v: Any) -> str:
    return "<NA>" if _is_missing(v) else str(v)


# --------------------------------------------------------------------------- #
# quantile regression + player-clustered bootstrap
# --------------------------------------------------------------------------- #
def qr_fit(y: np.ndarray, Z: np.ndarray, tau: float, cfg: dict, max_iter: int):
    """Fit a quantile regression with statsmodels; return params array or ``None`` on failure."""
    from statsmodels.regression.quantile_regression import QuantReg

    if y.size <= Z.shape[1]:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = QuantReg(y, Z).fit(q=float(tau), max_iter=int(max_iter),
                                     p_tol=float(cfg["models"]["fit_p_tol"]))
        p = np.asarray(res.params, float)
        return p if np.all(np.isfinite(p)) else None
    except Exception:
        return None


def cluster_bootstrap(y: np.ndarray, Z: np.ndarray, groups: np.ndarray, coef_index: int,
                      tau: float, reps: int, seed: int, cfg: dict) -> dict:
    """Player-clustered bootstrap for one coefficient: resample **players** with replacement.

    The design is fixed (identical columns), so each replicate keeps the same column layout.
    Returns the replicate coefficients plus the bootstrap SE and percentile CI (`models.bootstrap_ci`).
    """
    rng = np.random.default_rng(int(seed))
    uniq = sorted(set(groups.tolist()))
    by_group = {g: np.where(groups == g)[0] for g in uniq}
    lo_q, hi_q = [float(x) for x in cfg["models"]["bootstrap_ci"]]
    coefs: list[float] = []
    n_fail = 0
    for _ in range(int(reps)):
        draw = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([by_group[g] for g in draw])
        p = qr_fit(y[idx], Z[idx], tau, cfg, cfg["models"]["fit_max_iter"])
        if p is None:
            n_fail += 1
            continue
        coefs.append(float(p[coef_index]))
    arr = np.asarray(coefs, float)
    if arr.size == 0:
        return dict(coefs=[], se=None, ci_lo=None, ci_hi=None, n_ok=0, n_fail=n_fail)
    return dict(coefs=[float(x) for x in arr], se=float(np.std(arr, ddof=1)) if arr.size > 1 else None,
                ci_lo=float(np.quantile(arr, lo_q)), ci_hi=float(np.quantile(arr, hi_q)),
                n_ok=int(arr.size), n_fail=int(n_fail))


def pinball_loss(y: np.ndarray, yhat: np.ndarray, tau: float) -> float:
    """Mean pinball (quantile) loss at level ``tau`` — the proper scoring rule for QR."""
    d = np.asarray(y, float) - np.asarray(yhat, float)
    return float(np.mean(np.maximum(tau * d, (tau - 1.0) * d)))


# --------------------------------------------------------------------------- #
# player-grouped folds
# --------------------------------------------------------------------------- #
def player_folds(players: Sequence[int], k: int, seed: int) -> list[list[int]]:
    """Deterministically partition sorted unique players into ``k`` folds (grouped CV)."""
    uniq = sorted({int(p) for p in players})
    rng = np.random.default_rng(int(seed))
    perm = rng.permutation(len(uniq))
    folds: list[list[int]] = [[] for _ in range(int(k))]
    for pos, i in enumerate(perm):
        folds[pos % int(k)].append(uniq[int(i)])
    return [sorted(f) for f in folds]
