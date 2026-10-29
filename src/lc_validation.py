"""lc_validation — Phase 5 validation of the within-player leverage estimand.

The confirmatory fit lives in ``05_models`` (``lc_models.fit_ladder``).  This
module *validates* that fit; it never re-specifies it: the outcome, leverage
column and control list all come from ``bdb27_lc.models`` and the control
columns are built by ``lc_models._numeric_controls``, the same builder the
confirmatory fit uses.

Every refit here uses the declared convergence fallback
``player_fixed_effects`` (rung 2 of the TASK.md Phase 4 ladder) so that the same
estimator is applied to the observed data, the permutation nulls, the
orthogonalised refit, the placebos, the garbage-time contrast and the
sensitivity curve.  The mixed-model rung is not re-run 1,000 times: a
permutation null is only coherent if the statistic is computed the same way in
every replicate, and the FE estimator is the conservative rung the ladder falls
back to.  ``fit_design`` is a pre-demeaned fast path for that rung; a test
asserts it reproduces ``lc_models.fit_within_player`` to machine precision, so
there is one estimator, not two.

Every threshold, block structure and margin is read from config; the only
numeric literals here are the definitions behind the declared bin names
(tercile = 1/3 and 2/3, quartile = 1/4 and 3/4) and the ``+1`` of the exact
randomisation p-value.

Contents (TASK.md Phase 5, wired by D66):

* :func:`permutation_test` — shuffle leverage within player-game and within
  route family.  The shuffle count comes from ``bdb27_lc.validation``.
* :func:`orthogonalisation_check` — refit with leverage orthogonalised to game
  time and cumulative load (``lc_models.orthogonalise_leverage``).  An effect
  that dies here was a game-clock/fatigue effect.
* :func:`placebo_checks` — leverage must not predict route depth, the break
  indicator or time to throw.
* :func:`garbage_time_mask` — the configured quarter-and-margin rule.
* :func:`sensitivity_grid` — the declared curve, built like
  ``lc_route_depth.sensitivity_grid`` (primary first, flagged).
* :func:`equivalence_test` — TOST against the configured margin, with the
  "informative null" rule from TASK.md.

No number in this module is a design constant: every threshold is read from
``config.yaml`` under ``bdb27_lc``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

import lc_common as C
import lc_models as M

#: The estimator used by every validation refit (a declared Phase 4 fallback).
VALIDATION_ESTIMATOR = "player_fixed_effects"

#: Default placebo outcomes, used only when ``validation.placebo_outcomes`` is
#: absent.  Deliberately *not* quarter / score margin / down / yards to go:
#: those enter the leverage definition itself (leverage is a function of the
#: pre-snap win probability, which is a function of them).  A "placebo" they
#: predict would be a tautology, not a falsification test.
PLACEBO_OUTCOMES = ("break_depth_yd", "break_found", "time_to_throw_s")

ORTH_NOTE = ("a leverage effect that dies here is a fatigue or game-clock "
             "effect, not an effort effect")


def validation_block(cfg: dict) -> dict:
    try:
        return dict(C.lc(cfg).get("validation") or {})
    except (KeyError, TypeError):  # pragma: no cover - config always has it
        return {}


def _require_block(cfg: dict) -> dict:
    """The validation block is mandatory: a missing one would silently no-op."""
    block = validation_block(cfg)
    if not block:
        raise RuntimeError(
            "config bdb27_lc.validation is absent; Phase 5 has no declared "
            "permutation, orthogonalisation or equivalence settings to run.")
    return block


def models_block(cfg: dict) -> dict:
    return dict(C.lc(cfg)["models"])


def _numeric(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float)


# --------------------------------------------------------------------------- #
# Fast within-player fixed-effects OLS (Frisch-Waugh, clustered SE)
# --------------------------------------------------------------------------- #
def _demean(values: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """Subtract the group mean (one pass, no pandas overhead)."""
    values = np.asarray(values, dtype=float)
    sums = np.zeros(codes.max() + 1, dtype=float)
    counts = np.zeros(codes.max() + 1, dtype=float)
    np.add.at(sums, codes, np.nan_to_num(values, nan=0.0))
    np.add.at(counts, codes, np.isfinite(values).astype(float))
    means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    return values - means[codes]


def build_design(df: pd.DataFrame, outcome: str, leverage_col: str,
                 controls, player_col: str = "nfl_id",
                 cfg: dict | None = None) -> dict:
    """Prepare the within-player design once so permutations are cheap.

    Every control is built by ``lc_models._numeric_controls`` -- the exact same
    builder the confirmatory fit uses -- then demeaned within player.  By
    Frisch-Waugh the leverage coefficient and its clustered SE from this design
    are identical to ``lc_models.fit_within_player`` on the same rows.
    """
    work = df.copy()
    if outcome not in work.columns:
        raise KeyError(f"validation needs outcome column {outcome!r}")
    if leverage_col not in work.columns:
        raise KeyError(f"validation needs leverage column {leverage_col!r}")
    work[outcome] = _numeric(work[outcome])
    work[leverage_col] = _numeric(work[leverage_col])
    work = work.dropna(subset=[outcome, leverage_col, player_col]).reset_index(drop=True)
    if work.empty:
        raise ValueError("no rows with a finite outcome and leverage")
    work[player_col] = work[player_col].astype(str)
    codes, uniques = pd.factorize(work[player_col], sort=True)
    columns, names = M._numeric_controls(work, controls, cfg=cfg)
    y = _demean(work[outcome].to_numpy(dtype=float), codes)
    return {
        "work": work,
        "codes": codes,
        "n_players": int(len(uniques)),
        "y": y,
        "leverage": work[leverage_col].to_numpy(dtype=float),
        "controls": [_demean(np.asarray(c, dtype=float), codes) for c in columns],
        "control_names": list(names),
    }


def _empty_fit(n: int, n_players: int, status: str) -> dict:
    nan = float("nan")
    return {"beta": nan, "se": nan, "t": nan, "p": nan, "ci_low": nan,
            "ci_high": nan, "n": int(n), "n_players": int(n_players),
            "status": status}


def fit_design(design: dict, leverage_values=None) -> dict:
    """FE OLS + player-clustered SE on the (possibly shuffled) leverage column."""
    if leverage_values is None:
        leverage_values = design["leverage"]
    lev = _demean(np.asarray(leverage_values, dtype=float), design["codes"])
    n = int(len(lev))
    if n == 0:
        return _empty_fit(0, 0, "no rows")
    if not np.isfinite(lev).all() or float(np.max(np.abs(lev))) <= 1e-12:
        return _empty_fit(n, design["n_players"], "zero within-player variance")
    columns = [lev, *design["controls"]]
    X = np.column_stack(columns)
    if not np.isfinite(X).all() or not np.isfinite(design["y"]).all():
        return _empty_fit(n, design["n_players"], "non-finite design")
    beta, *_ = np.linalg.lstsq(X, design["y"], rcond=None)
    resid = design["y"] - X @ beta
    k = X.shape[1]
    # The full FE design also carries an intercept and (P-1) player dummies; the
    # small-sample correction must use that count, as lc_models._ols_cluster does.
    k_full = k + design["n_players"]
    g = design["n_players"]
    bread = np.linalg.pinv(X.T @ X)
    meat = np.zeros((k, k), dtype=float)
    for code in range(int(design["codes"].max()) + 1):
        mask = design["codes"] == code
        score = X[mask].T @ resid[mask]
        meat += np.outer(score, score)
    correction = ((g / (g - 1.0)) * ((n - 1.0) / max(1.0, n - k_full))
                  if g > 1 else 1.0)
    cov = bread @ meat @ bread * correction
    se = float(np.sqrt(max(cov[0, 0], 0.0)))
    b = float(beta[0])
    dof = max(1, g - 1)
    t_stat = b / se if se > 0 else float("nan")
    p_value = float(2.0 * stats.t.sf(abs(t_stat), dof)) if np.isfinite(t_stat) else float("nan")
    crit = float(stats.t.ppf(0.975, dof))
    return {
        "beta": b, "se": se, "t": float(t_stat), "p": p_value,
        "ci_low": b - crit * se, "ci_high": b + crit * se,
        "n": n, "n_players": int(g), "dof": dof,
        "status": "ok" if np.isfinite(b) and np.isfinite(se) and se > 0
        else "undefined",
    }


def block_indices(keys: pd.Series) -> tuple[list[np.ndarray], int]:
    """Group rows into shuffle blocks once; blocks of size 1 cannot move."""
    frame = pd.DataFrame({"k": keys.to_numpy()})
    blocks: list[np.ndarray] = []
    n_singleton = 0
    for index in frame.groupby("k", sort=True).indices.values():
        index = np.asarray(index, dtype=int)
        if len(index) < 2:
            n_singleton += int(len(index))
            continue
        blocks.append(index)
    return blocks, n_singleton


def _shuffle(values: np.ndarray, blocks: list[np.ndarray], rng) -> np.ndarray:
    """Permute ``values`` inside each pre-computed block."""
    out = np.asarray(values, dtype=float).copy()
    for index in blocks:
        out[index] = out[rng.permutation(index)]
    return out


# --------------------------------------------------------------------------- #
# 1. Leverage permutation
# --------------------------------------------------------------------------- #
def permutation_test(table: pd.DataFrame, cfg: dict, scheme: str,
                     reps: int | None = None, seed: int | None = None,
                     outcome: str | None = None,
                     leverage_col: str | None = None,
                     controls=None) -> dict:
    """Shuffle leverage within the scheme's blocks and refit each time.

    ``scheme`` is a key of ``bdb27_lc.validation.permutations.block_columns``:
    ``player_game`` (within player and game) or ``route_family`` (within player
    and route family).  A missing block definition is a config error and raises;
    a degenerate *table* is reported with a status instead of crashing.

    The p-value is two-sided: ``(1 + #{|null| >= |observed|}) / (reps + 1)``.
    The null distribution is written out in full (the stage saves it), and it is
    not forced to mean zero: permutation preserves each block's leverage mean,
    so between-block variation within a player survives in the null.
    """
    block = _require_block(cfg)
    perm = dict(block.get("permutations") or {})
    models = models_block(cfg)
    outcome = outcome or str(models.get("outcome", "log_max_speed"))
    leverage_col = leverage_col or str(models.get("leverage_column", "leverage_z"))
    controls = tuple(controls if controls is not None else models["controls"])
    reps = int(perm.get("reps", 1000) if reps is None else reps)
    base_seed = int(C.lc(cfg)["seeds"]["permutation"])
    seed = base_seed + int(perm.get("seed_offset", 0)) if seed is None else int(seed)

    columns = list((perm.get("block_columns") or {}).get(scheme) or [])
    if not columns:
        raise ValueError(
            f"validation.permutations.block_columns has no entry for scheme "
            f"{scheme!r}; declared schemes are "
            f"{sorted((perm.get('block_columns') or {}))}")

    design = build_design(table, outcome, leverage_col, controls, cfg=cfg)
    observed = fit_design(design, design["leverage"])
    available = [c for c in columns if c in design["work"].columns]
    missing = [c for c in columns if c not in design["work"].columns]
    if missing:
        return {"scheme": scheme, "status": f"block columns absent: {missing}",
                "reps": reps, "observed": observed, "null": np.array([]),
                "p_value": float("nan"), "blocks": 0, "singleton_rows": 0,
                "quantiles": {}, "mean": float("nan"), "sd": float("nan"),
                "share_at_least_as_extreme": float("nan")}

    keys = design["work"][available].astype(str).agg("|".join, axis=1)
    if observed["status"] != "ok":
        return {"scheme": scheme, "status": observed["status"], "reps": reps,
                "observed": observed, "null": np.array([]),
                "p_value": float("nan"), "blocks": int(keys.nunique()),
                "singleton_rows": 0, "quantiles": {}, "mean": float("nan"),
                "sd": float("nan"), "share_at_least_as_extreme": float("nan")}

    blocks, singleton = block_indices(keys)
    rng = np.random.default_rng(seed)
    null = np.empty(reps, dtype=float)
    for i in range(reps):
        shuffled = _shuffle(design["leverage"], blocks, rng)
        null[i] = fit_design(design, shuffled)["beta"]
    finite = null[np.isfinite(null)]
    observed_beta = float(observed["beta"])
    if finite.size:
        extreme = int(np.sum(np.abs(finite) >= abs(observed_beta) - 1e-12))
        p_value = (1.0 + extreme) / (1.0 + float(finite.size))
        quantiles = {"q025": float(np.quantile(finite, 0.025)),
                     "q50": float(np.quantile(finite, 0.50)),
                     "q975": float(np.quantile(finite, 0.975))}
        mean, sd = float(finite.mean()), float(finite.std(ddof=1))
        share = float(extreme / finite.size)
    else:  # pragma: no cover - defensive
        p_value, quantiles, mean, sd, share = (float("nan"), {}, float("nan"),
                                               float("nan"), float("nan"))
    return {
        "scheme": scheme, "status": "ok", "reps": reps, "seed": seed,
        "block_columns": available, "blocks": len(blocks),
        "singleton_rows": int(singleton), "observed": observed,
        "null": null, "p_value": float(p_value),
        "quantiles": quantiles, "mean": mean, "sd": sd,
        "share_at_least_as_extreme": share,
        "n_finite_reps": int(finite.size),
        "estimator": VALIDATION_ESTIMATOR,
    }


# --------------------------------------------------------------------------- #
# 2. Time-and-load orthogonalisation
# --------------------------------------------------------------------------- #
def _within_player_residual_r2(frame: pd.DataFrame, column: str,
                               resid: pd.Series) -> float:
    """Share of within-player variance of ``column`` the residual removed."""
    values = _numeric(frame[column])
    groups = frame["nfl_id"].astype(str)
    demeaned = values - values.groupby(groups).transform("mean")
    sst = float(np.nansum(demeaned.to_numpy(dtype=float) ** 2))
    if not np.isfinite(sst) or sst <= 0:
        return float("nan")
    ssr = float(np.nansum(np.asarray(resid, dtype=float) ** 2))
    return float(1.0 - ssr / sst)


def _within_player_corr(frame: pd.DataFrame, left: str, right: str) -> float:
    """Correlation of two columns after removing player means."""
    if left not in frame.columns or right not in frame.columns:
        return float("nan")
    groups = frame["nfl_id"].astype(str)
    a = _numeric(frame[left])
    b = _numeric(frame[right])
    a = (a - a.groupby(groups).transform("mean")).to_numpy(dtype=float)
    b = (b - b.groupby(groups).transform("mean")).to_numpy(dtype=float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3 or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
        return float("nan")
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


def orthogonalisation_check(table: pd.DataFrame, cfg: dict,
                            outcome: str | None = None,
                            leverage_col: str | None = None,
                            controls=None) -> dict:
    """Naive FE fit vs FE fit on leverage residualised on game time and load.

    The orthogonalised fit is ``lc_models.fit_orthogonalised`` -- the single
    implementation of the time/load refit.  This function adds only the
    diagnostics: how much within-player leverage variance the time/load
    controls explain, and the within-player correlations that make the
    confound concrete.
    """
    models = models_block(cfg)
    block = _require_block(cfg)
    orth = dict(block.get("orthogonalisation") or {})
    outcome = outcome or str(models.get("outcome", "log_max_speed"))
    leverage_col = leverage_col or str(models.get("leverage_column", "leverage_z"))
    controls = tuple(controls if controls is not None else models["controls"])
    orth_controls = tuple(orth.get("controls") or M.TIME_LOAD_CONTROLS)

    work = table.copy()
    for column in (outcome, leverage_col):
        work[column] = _numeric(work[column])
    work = work.dropna(subset=[outcome, leverage_col, "nfl_id"]).reset_index(drop=True)
    if len(work) < 3 or work["nfl_id"].nunique() < 2:
        empty = _empty_fit(len(work), int(work["nfl_id"].nunique()),
                           "too few rows or players")
        return {"status": "too few rows or players", "naive": empty,
                "orthogonalised": empty, "orthogonalised_on": list(orth_controls),
                "attenuation_fraction": None, "absolute_change": None,
                "r2_leverage_on_time_load_within_player": float("nan"),
                "within_player_corr_time": float("nan"),
                "within_player_corr_load": float("nan"),
                "note": ORTH_NOTE}

    try:
        naive = M.fit_within_player(work, outcome, leverage_col, controls, cfg=cfg)
        orth_fit = M.fit_orthogonalised(work, outcome, leverage_col, controls,
                                        cfg=cfg, orth_controls=orth_controls)
    except Exception as exc:  # noqa: BLE001 - report, never crash a stage
        empty = _empty_fit(len(work), int(work["nfl_id"].nunique()),
                           f"fit failed: {exc}")
        return {"status": f"fit failed: {exc}", "naive": empty,
                "orthogonalised": empty, "orthogonalised_on": list(orth_controls),
                "attenuation_fraction": None, "absolute_change": None,
                "r2_leverage_on_time_load_within_player": float("nan"),
                "within_player_corr_time": float("nan"),
                "within_player_corr_load": float("nan"),
                "note": ORTH_NOTE}

    resid = M.orthogonalise_leverage(work, leverage_col, orth_controls)
    r2 = _within_player_residual_r2(work, leverage_col, resid)

    nb, ob = naive.get("beta"), orth_fit.get("beta")
    attenuation = None
    change = None
    if nb is not None and ob is not None and np.isfinite(nb) and np.isfinite(ob):
        change = float(abs(nb) - abs(ob))
        if abs(nb) > 1e-12:
            attenuation = float(change / abs(nb))
    alpha = float(C.lc(cfg)["power"]["alpha"])
    orth_p = orth_fit.get("p")
    survives = None
    if (np.isfinite(nb) and np.isfinite(ob) and orth_p is not None
            and np.isfinite(orth_p) and np.sign(nb) == np.sign(ob)):
        survives = bool(float(orth_p) < alpha)
    return {
        "status": "ok",
        "naive": naive,
        "orthogonalised": orth_fit,
        "orthogonalised_on": list(orth_controls),
        "attenuation_fraction": attenuation,
        "absolute_change": change,
        "effect_survives": survives,
        "r2_leverage_on_time_load_within_player": r2,
        "within_player_corr_time": _within_player_corr(
            work, leverage_col, str(orth.get("time_column", "game_elapsed_s"))),
        "within_player_corr_load": _within_player_corr(
            work, leverage_col, str(orth.get("load_column", "prior_efforts"))),
        "note": ORTH_NOTE,
    }


# --------------------------------------------------------------------------- #
# 3. Placebos
# --------------------------------------------------------------------------- #
def placebo_checks(table: pd.DataFrame, cfg: dict,
                   leverage_col: str | None = None,
                   controls=None) -> list[dict]:
    """Regress each placebo outcome on leverage with the primary controls.

    ``break_depth_yd`` is fitted among detected breaks only (the depth is
    missing otherwise and is never imputed).  ``break_found`` is a linear
    probability model.  The matched (CEM) version of this placebo is a Phase 4
    item and is not wired this cycle; this is the regression-adjusted version
    with the same controls as the primary model.
    """
    models = models_block(cfg)
    block = _require_block(cfg)
    leverage_col = leverage_col or str(models.get("leverage_column", "leverage_z"))
    controls = list(controls if controls is not None else models["controls"])
    outcomes = tuple(block.get("placebo_outcomes") or PLACEBO_OUTCOMES)
    rows = []
    for outcome in outcomes:
        if outcome not in table.columns:
            rows.append({"outcome": outcome, "status": "column absent",
                         "beta": None, "se": None, "p": None, "n": 0,
                         "n_players": 0,
                         "note": "outcome not in the effort table"})
            continue
        frame = table
        status = "ok"
        note = ("regression-adjusted placebo (CEM-matched version is a Phase 4 "
                "item, not wired)")
        if outcome == "break_depth_yd":
            if "break_found" in table.columns:
                found = table["break_found"]
                if found.dtype == object:
                    found = found.astype(str).str.lower().isin(("true", "1"))
                frame = table[found.astype(bool)]
            note = ("placebo among detected breaks only; depth is missing "
                    "elsewhere and is never imputed")
            if frame.empty:
                rows.append({"outcome": outcome, "status": "no detected breaks",
                             "beta": None, "se": None, "p": None, "n": 0,
                             "n_players": 0, "note": note})
                continue
        used = tuple(c for c in controls if c != outcome)
        try:
            result = M.fit_within_player(frame, outcome, leverage_col, used, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 - report, never crash a stage
            rows.append({"outcome": outcome, "status": f"fit failed: {exc}",
                         "beta": None, "se": None, "p": None, "n": 0,
                         "n_players": 0, "note": note})
            continue
        if not np.isfinite(result.get("beta", np.nan)):
            status = "undefined (no within-player variance)"
        rows.append({
            "outcome": outcome, "status": status,
            "beta": result.get("beta"), "se": result.get("se"),
            "p": result.get("p"), "n": result.get("n"),
            "n_players": result.get("n_players"), "note": note,
        })
    return rows


# --------------------------------------------------------------------------- #
# 4. Garbage time
# --------------------------------------------------------------------------- #
def garbage_time_block(cfg: dict) -> dict:
    """The frozen garbage-time rule.

    The margins live in exactly one place -- ``bdb27_lc.leverage.garbage_time``
    -- so validation cannot drift from the definition frozen before the
    leverage bins were built.  ``validation.garbage_time`` supplies only the
    quarter at which "fourth quarter or later" starts; a conflicting margin in
    that block is an error, not a silent override.
    """
    frozen = dict((C.lc(cfg).get("leverage") or {}).get("garbage_time") or {})
    local = dict(_require_block(cfg).get("garbage_time") or {})
    for key in ("q4_margin", "any_quarter_margin"):
        if key not in frozen:
            raise RuntimeError(
                f"bdb27_lc.leverage.garbage_time.{key} is missing; the rule is "
                "frozen there and must not be defaulted in code")
        if key in local and float(local[key]) != float(frozen[key]):
            raise RuntimeError(
                f"validation.garbage_time.{key}={local[key]} conflicts with the "
                f"frozen leverage.garbage_time.{key}={frozen[key]}; the margins "
                "live in exactly one place")
    merged = dict(frozen)
    merged.update({k: v for k, v in local.items()
                   if k not in ("q4_margin", "any_quarter_margin")})
    if "q4" not in merged:
        raise RuntimeError("validation.garbage_time.q4 is missing from config")
    return merged


def garbage_time_mask(table: pd.DataFrame, cfg: dict) -> tuple[pd.Series, dict]:
    """The configured quarter-and-margin garbage-time rule.

    A play is garbage time when the (absolute) score margin is at least
    ``q4_margin`` in the fourth quarter or later, or at least
    ``any_quarter_margin`` in any quarter.  A missing input column raises: a
    silent all-False mask would claim "no garbage time" without evidence.
    """
    block = garbage_time_block(cfg)
    rule = str(block.get("rule", "quarter_and_margin"))
    q4 = int(block["q4"])
    q4_margin = float(block["q4_margin"])
    any_margin = float(block["any_quarter_margin"])
    if rule != "quarter_and_margin":
        raise ValueError(f"unknown garbage-time rule {rule!r}")
    absent = [c for c in ("quarter", "score_margin") if c not in table.columns]
    if absent:
        raise ValueError(f"garbage-time rule needs column(s) {absent}")
    quarter = _numeric(table["quarter"])
    margin = _numeric(table["score_margin"]).abs()
    mask = ((quarter >= q4) & (margin >= q4_margin)) | (margin >= any_margin)
    mask = mask.fillna(False).astype(bool)
    return mask, {"rule": rule, "q4": q4, "q4_margin": q4_margin,
                  "any_quarter_margin": any_margin}


def garbage_time_check(table: pd.DataFrame, cfg: dict,
                       outcome: str | None = None,
                       leverage_col: str | None = None,
                       controls=None) -> dict:
    """Fit the primary specification with and without garbage-time plays."""
    models = models_block(cfg)
    outcome = outcome or str(models.get("outcome", "log_max_speed"))
    leverage_col = leverage_col or str(models.get("leverage_column", "leverage_z"))
    controls = tuple(controls if controls is not None else models["controls"])
    try:
        mask, rule = garbage_time_mask(table, cfg)
    except (ValueError, RuntimeError) as exc:
        return {"status": f"garbage-time rule not evaluable: {exc}",
                "rule": None, "n_total": int(len(table)), "n_garbage_time": None,
                "share_garbage_time": None, "n_retained": None,
                "with_garbage_time": _empty_fit(len(table), 0, str(exc)),
                "without_garbage_time": _empty_fit(0, 0, str(exc)),
                "by_quarter": {}, "share_by_leverage_bin": {}}
    keep = ~mask
    with_fit = M.fit_within_player(table, outcome, leverage_col, controls, cfg=cfg)
    if int(keep.sum()) == 0:
        without = _empty_fit(0, 0, "all efforts are garbage time")
    else:
        without = M.fit_within_player(table.loc[keep], outcome, leverage_col,
                                      controls, cfg=cfg)
    by_quarter: dict[str, int] = {}
    if mask.any():
        by_quarter = {str(int(q)): int(n) for q, n in
                      _numeric(table["quarter"])[mask].fillna(0)
                      .value_counts().sort_index().items()}
    share_by_bin: dict[str, float] = {}
    if "leverage_bin" in table.columns and len(table):
        share_by_bin = {str(k): float(v) for k, v in
                        mask.groupby(table["leverage_bin"].astype(str)).mean().items()}
    return {
        "status": "ok", "rule": rule,
        "n_total": int(len(table)),
        "n_garbage_time": int(mask.sum()),
        "share_garbage_time": float(mask.mean()) if len(table) else 0.0,
        "n_retained": int(keep.sum()),
        "with_garbage_time": with_fit,
        "without_garbage_time": without,
        "by_quarter": by_quarter,
        "share_by_leverage_bin": share_by_bin,
    }


# --------------------------------------------------------------------------- #
# 5. Sensitivity curve
# --------------------------------------------------------------------------- #
def sensitivity_grid(cfg: dict) -> list[dict]:
    """The declared Phase 5 curve, built like ``lc_route_depth.sensitivity_grid``.

    Every row comes from ``bdb27_lc.validation.sensitivity`` (plus the
    tactical-demand block).  Code adds no spec of its own: if a key is absent
    the curve is shorter, not invented.  The primary specification is first and
    flagged ``primary``.  Each spec names how it filters the effort table and
    which leverage column it uses; :func:`apply_spec` performs the filter so a
    spec is data, not code.
    """
    models = models_block(cfg)
    block = _require_block(cfg)
    sens = dict(block.get("sensitivity") or {})
    tac = dict(block.get("tactical_demand") or {})
    primary_leverage = str(models.get("leverage_column", "leverage_z"))
    primary_displacement = float(C.lc(cfg)["efforts"]["min_route_depth_yd"])
    specs: list[dict] = [{
        "spec": "primary", "leverage": primary_leverage, "contrast": "continuous",
        "bins": None, "min_displacement_yd": primary_displacement, "min_efforts": 0,
        "exclude_final_two_minutes": False, "time_to_throw_iqr": False,
        "primary": True, "note": "the confirmatory specification (D65)",
    }]

    for definition in sens.get("leverage_definitions") or []:
        if str(definition) == primary_leverage:
            continue
        specs.append({
            "spec": f"leverage={definition}", "leverage": str(definition),
            "contrast": "continuous", "bins": None,
            "min_displacement_yd": primary_displacement, "min_efforts": 0,
            "exclude_final_two_minutes": False, "time_to_throw_iqr": False,
            "primary": False,
            "note": ("alternate leverage definition (raw 4p(1-p)); the coefficient "
                     "is per unit of that scale, so it is not comparable to the "
                     "per-SD primary estimate"),
        })

    for cutoff in sens.get("bin_cutoffs") or []:
        specs.append({
            "spec": f"bin_cutoff={cutoff}", "leverage": primary_leverage,
            "contrast": "high_vs_low", "bins": str(cutoff),
            "min_displacement_yd": primary_displacement, "min_efforts": 0,
            "exclude_final_two_minutes": False, "time_to_throw_iqr": False,
            "primary": False,
            "note": ("high-vs-low contrast under this cut: the coefficient is the "
                     "high-bin minus low-bin difference, not a per-SD slope"),
        })

    for threshold in sens.get("effort_thresholds") or []:
        if float(threshold) == primary_displacement:
            continue
        specs.append({
            "spec": f"effort_threshold={float(threshold):g}yd",
            "leverage": primary_leverage, "contrast": "continuous", "bins": None,
            "min_displacement_yd": float(threshold), "min_efforts": 0,
            "exclude_final_two_minutes": False, "time_to_throw_iqr": False,
            "primary": False,
            "note": "stricter route-extension effort threshold",
        })

    for minimum in sens.get("min_efforts_grid") or []:
        if int(minimum) == 0:
            continue
        specs.append({
            "spec": f"min_efforts={int(minimum)}", "leverage": primary_leverage,
            "contrast": "continuous", "bins": None,
            "min_displacement_yd": primary_displacement, "min_efforts": int(minimum),
            "exclude_final_two_minutes": False, "time_to_throw_iqr": False,
            "primary": False,
            "note": ("players with fewer efforts are dropped (the primary spec "
                     "imposes no floor)"),
        })

    if sens.get("exclude_final_two_minutes"):
        specs.append({
            "spec": "exclude_final_two_minutes", "leverage": primary_leverage,
            "contrast": "continuous", "bins": None,
            "min_displacement_yd": primary_displacement, "min_efforts": 0,
            "exclude_final_two_minutes": True, "time_to_throw_iqr": False,
            "primary": False,
            "note": "drops the last two minutes of each half (Q2 and Q4)",
        })

    if tac.get("time_to_throw_restriction"):
        specs.append({
            "spec": "time_to_throw_iqr", "leverage": primary_leverage,
            "contrast": "continuous", "bins": None,
            "min_displacement_yd": primary_displacement, "min_efforts": 0,
            "exclude_final_two_minutes": False, "time_to_throw_iqr": True,
            "primary": False,
            "note": ("tactical-demand check: keep the interquartile time-to-throw "
                     "range" + ("" if tac.get("defender_distance_available")
                                else "; defender distance is not in the effort table")),
        })

    # Ceiling features: the D65 specification contains none (the ceiling is
    # absorbed by the within-player design), so this declared sensitivity is
    # vacuous.  It is recorded, not silently dropped.
    for feature in sens.get("ceiling_features") or []:
        specs.append({
            "spec": f"ceiling_feature={feature}", "leverage": primary_leverage,
            "contrast": "continuous", "bins": None,
            "min_displacement_yd": primary_displacement, "min_efforts": 0,
            "exclude_final_two_minutes": False, "time_to_throw_iqr": False,
            "primary": False, "status": "not_applicable",
            "note": ("no ceiling feature enters the D65 specification; adding "
                     "one is headroom moderation (Phase 4, not wired)"),
        })

    if "matching_tolerances" in sens and not sens.get("matching_tolerances"):
        specs.append({
            "spec": "matching_tolerances", "leverage": primary_leverage,
            "contrast": "continuous", "bins": None,
            "min_displacement_yd": primary_displacement, "min_efforts": 0,
            "exclude_final_two_minutes": False, "time_to_throw_iqr": False,
            "primary": False, "status": "not_applicable",
            "note": ("the CEM high-vs-low matched design is a Phase 4 item (D65) "
                     "and is not wired this cycle, so it has no tolerances to vary"),
        })
    return specs


#: Declared bin cuts.  A cut is a pair of quantiles on the leverage column.
BIN_CUTS = {"tercile": (1 / 3, 2 / 3), "quartile": (0.25, 0.75)}


def bin_contrast(values: pd.Series, cutoff: str,
                 leverage_bin: pd.Series | None = None) -> pd.Series:
    """+1 in the high bin, 0 in the low bin, NaN elsewhere for the named cut."""
    if cutoff == "tercile" and leverage_bin is not None:
        labels = leverage_bin.astype(str)
        out = pd.Series(np.nan, index=values.index, dtype=float)
        out[labels == "high"] = 1.0
        out[labels == "low"] = 0.0
        return out
    if cutoff not in BIN_CUTS:
        raise ValueError(f"unknown bin cut {cutoff!r}; declared: {sorted(BIN_CUTS)}")
    low_q, high_q = BIN_CUTS[cutoff]
    low = float(values.quantile(low_q))
    high = float(values.quantile(high_q))
    out = pd.Series(np.nan, index=values.index, dtype=float)
    out[values <= low] = 0.0
    out[values >= high] = 1.0
    return out


def apply_spec(table: pd.DataFrame, spec: dict, cfg: dict) -> pd.DataFrame:
    """Filter the effort table for one sensitivity spec.

    Returns the frame (possibly empty).  A high-vs-low spec adds the binary
    column ``_leverage_high_vs_low`` and keeps only the two extreme bins.
    """
    frame = table
    if spec.get("min_displacement_yd") is not None:
        if "route_displacement_yd" not in frame.columns:
            return frame.iloc[0:0]
        frame = frame[_numeric(frame["route_displacement_yd"])
                      >= float(spec["min_displacement_yd"])]
    if spec.get("min_efforts"):
        counts = frame.groupby("nfl_id").size()
        keep = counts[counts >= int(spec["min_efforts"])].index
        frame = frame[frame["nfl_id"].isin(keep)]
    if spec.get("exclude_final_two_minutes"):
        sens = dict(_require_block(cfg).get("sensitivity") or {})
        seconds = float(sens["final_two_minutes_s"])
        quarter = _numeric(frame.get("quarter", pd.Series(np.nan, index=frame.index)))
        clock = _numeric(frame.get("game_clock_s", pd.Series(np.nan, index=frame.index)))
        drop = quarter.isin([2, 4]) & (clock <= seconds)
        frame = frame[~drop.fillna(False)]
    if spec.get("time_to_throw_iqr"):
        if "time_to_throw_s" not in frame.columns:
            return frame.iloc[0:0]
        values = _numeric(frame["time_to_throw_s"])
        low, high = float(values.quantile(0.25)), float(values.quantile(0.75))
        frame = frame[(values >= low) & (values <= high)]
    if spec.get("contrast") == "high_vs_low" and len(frame):
        frame = frame.copy()
        frame["_leverage_high_vs_low"] = bin_contrast(
            _numeric(frame[str(spec["leverage"])]), str(spec["bins"]),
            frame["leverage_bin"] if "leverage_bin" in frame.columns else None)
        frame = frame[frame["_leverage_high_vs_low"].notna()]
    return frame


def sensitivity_curve(table: pd.DataFrame, cfg: dict) -> tuple[list[dict], list[dict]]:
    """Fit every declared spec.  Returns (rows, specs) for the writers."""
    models = models_block(cfg)
    outcome = str(models.get("outcome", "log_max_speed"))
    controls = tuple(models["controls"])
    specs = sensitivity_grid(cfg)
    rows: list[dict] = []
    for spec in specs:
        base = {"spec": spec["spec"], "beta": None, "se": None, "p": None,
                "beta_per_within_player_sd": None, "n": 0, "n_players": 0,
                "leverage": spec["leverage"], "comparison": spec.get("contrast"),
                "note": spec["note"]}
        if spec.get("status") == "not_applicable":
            rows.append({**base, "status": "not_applicable"})
            continue
        frame = apply_spec(table, spec, cfg)
        leverage_col = ("_leverage_high_vs_low"
                        if spec.get("contrast") == "high_vs_low" else str(spec["leverage"]))
        if spec.get("contrast") == "high_vs_low":
            base["leverage"] = leverage_col
        if frame.empty:
            rows.append({**base, "status": "no rows after filter"})
            continue
        if leverage_col not in frame.columns:
            rows.append({**base, "status": f"column absent: {leverage_col}"})
            continue
        try:
            result = M.fit_within_player(frame, outcome, leverage_col, controls, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            rows.append({**base, "status": f"fit failed: {exc}"})
            continue
        beta = result.get("beta")
        codes = pd.factorize(frame["nfl_id"].astype(str), sort=True)[0]
        within_sd = float(np.std(_demean(
            _numeric(frame[leverage_col]).to_numpy(float), codes)))
        scaled = None
        if (spec.get("contrast") == "continuous" and beta is not None
                and np.isfinite(beta) and np.isfinite(within_sd) and within_sd > 1e-12):
            scaled = float(beta) * within_sd
        ok = (beta is not None and np.isfinite(beta)
              and np.isfinite(result.get("se", np.nan)))
        rows.append({
            **base,
            "status": "ok" if ok else "undefined",
            "beta": beta, "se": result.get("se"), "p": result.get("p"),
            "beta_per_within_player_sd": scaled,
            "n": result.get("n"), "n_players": result.get("n_players"),
            "within_player_sd": within_sd,
        })
    return rows, specs


# --------------------------------------------------------------------------- #
# 6. Equivalence test
# --------------------------------------------------------------------------- #
def equivalence_test(beta: float, se: float, dof: int, cfg: dict) -> dict:
    """TOST against the configured margin, with the TASK.md informativeness rule.

    A null is *informative* only when the whole interval lies inside
    (-smallest_effect_of_interest, +smallest_effect_of_interest): then effects
    as large as the smallest effect of interest are ruled out.  Equivalence at
    the (stricter) configured margin is reported separately.
    """
    block = dict(_require_block(cfg).get("equivalence") or {})
    power = C.lc(cfg)["power"]
    margin = float(block.get("margin", power["equivalence_margin"]))
    sei = float(block.get("smallest_effect_of_interest",
                          power["smallest_effect_of_interest"]))
    alpha = float(block.get("alpha", power["alpha"]))
    # The duplicated config constants must agree; drift here would silently
    # change the test the report claims to have run.
    if (abs(margin - float(power["equivalence_margin"])) > 1e-12
            or abs(sei - float(power["smallest_effect_of_interest"])) > 1e-12
            or abs(alpha - float(power["alpha"])) > 1e-12):
        raise RuntimeError(
            "validation.equivalence disagrees with bdb27_lc.power "
            f"(margin {margin} vs {power['equivalence_margin']}, sei {sei} vs "
            f"{power['smallest_effect_of_interest']}, alpha {alpha} vs "
            f"{power['alpha']})")
    if not (np.isfinite(beta) and np.isfinite(se) and se > 0):
        return {"status": "undefined", "margin": margin,
                "smallest_effect_of_interest": sei, "p_tost": None,
                "equivalent_at_margin": False, "excludes_smallest_effect": False,
                "null_is_informative": False,
                "verdict": "undefined (no finite estimate)"}
    dof = max(1, int(dof))
    crit = float(stats.t.ppf(1.0 - alpha / 2.0, dof))
    ci_low, ci_high = beta - crit * se, beta + crit * se
    # TOST: H0_lower beta <= -margin, H0_upper beta >= +margin.
    p_lower = float(stats.t.sf((beta + margin) / se, dof))
    p_upper = float(stats.t.cdf((beta - margin) / se, dof))
    p_tost = max(p_lower, p_upper)
    equivalent = bool(p_tost < alpha)
    excludes_sei = bool(ci_low > -sei and ci_high < sei)
    detected = bool(ci_low > 0.0 or ci_high < 0.0)
    if detected:
        verdict = "effect detected (interval excludes 0)"
    elif excludes_sei:
        verdict = "informative null (interval inside +/- smallest effect of interest)"
    else:
        verdict = "uninformative null (interval contains the smallest effect of interest)"
    return {
        "status": "ok", "beta": float(beta), "se": float(se), "dof": dof,
        "ci_low": ci_low, "ci_high": ci_high, "margin": margin,
        "smallest_effect_of_interest": sei, "alpha": alpha,
        "p_tost_lower": p_lower, "p_tost_upper": p_upper, "p_tost": p_tost,
        "equivalent_at_margin": equivalent,
        "excludes_smallest_effect": excludes_sei,
        "null_is_informative": bool(excludes_sei and not detected),
        "verdict": verdict,
    }


# --------------------------------------------------------------------------- #
# Orchestration (the stage calls this; the tests call it too)
# --------------------------------------------------------------------------- #
def run_validation(table: pd.DataFrame, cfg: dict, mode: str,
                   primary: dict | None = None,
                   perm_reps: int | None = None,
                   perm_seed: int | None = None) -> dict:
    """Run every Phase 5 check on one effort table.

    ``primary`` is the ``05_models`` result (for the equivalence test).  The
    stage requires it; the library tolerates its absence and reports that the
    equivalence test was skipped rather than re-specifying the model.
    """
    models = models_block(cfg)
    block = _require_block(cfg)
    outcome = str(models.get("outcome", "log_max_speed"))
    leverage_col = str(models.get("leverage_column", "leverage_z"))
    controls = tuple(models["controls"])
    perm = dict(block.get("permutations") or {})
    schemes = [str(s) for s in (perm.get("schemes") or
                                (perm.get("block_columns") or {}))]

    permutation = {}
    for scheme in schemes:
        permutation[scheme] = permutation_test(
            table, cfg, scheme, reps=perm_reps, seed=perm_seed,
            outcome=outcome, leverage_col=leverage_col, controls=controls)

    orthogonalisation = orthogonalisation_check(
        table, cfg, outcome=outcome, leverage_col=leverage_col, controls=controls)
    placebo = placebo_checks(table, cfg, leverage_col=leverage_col,
                             controls=controls)
    garbage = garbage_time_check(table, cfg, outcome=outcome,
                                 leverage_col=leverage_col, controls=controls)
    sensitivity_rows, specs = sensitivity_curve(table, cfg)

    dof = int(primary.get("n_players", 0)) - 1 if primary else 0
    equivalence = (equivalence_test(float(primary.get("beta", float("nan"))),
                                    float(primary.get("se", float("nan"))), dof, cfg)
                   if primary else {"status": "primary model result absent",
                                    "verdict": "not tested (no 05_models result)"})

    counts = table.groupby("nfl_id").size() if len(table) else pd.Series(dtype=int)
    if len(table) and "game_id" in table.columns:
        n_single_pg = int((table.groupby(["nfl_id", "game_id"]).size() == 1).sum())
    else:
        n_single_pg = 0
    if len(table) and leverage_col in table.columns:
        grouped = table.groupby("nfl_id")[leverage_col]
        zero_var = int(sum(1 for _, values in grouped
                           if len(values.dropna()) >= 2
                           and float(values.std(ddof=0)) <= 1e-12))
    else:
        zero_var = 0
    edge_cases = {
        "n_single_effort_players": int((counts == 1).sum()) if len(counts) else 0,
        "n_single_effort_player_games": n_single_pg,
        "n_players_zero_leverage_variance": zero_var,
        "zero_variance_rule": "players with >= 2 efforts and within-player SD <= 1e-12",
    }

    per_scheme = {k: {"status": v["status"], "p_value": v["p_value"],
                      "reps": v["reps"], "blocks": v["blocks"],
                      "singleton_rows": v["singleton_rows"],
                      "observed_beta": v["observed"]["beta"],
                      "observed_se": v["observed"]["se"],
                      "null_mean": v["mean"], "null_sd": v["sd"],
                      "null_min": float(np.nanmin(v["null"])) if len(v["null"]) else None,
                      "null_max": float(np.nanmax(v["null"])) if len(v["null"]) else None,
                      "quantiles": v["quantiles"]}
                  for k, v in permutation.items()}

    n_efforts = int(len(table))
    n_players = int(table["nfl_id"].nunique()) if len(table) else 0
    mde = M.analytic_mde(n_efforts / max(n_players, 1), cfg) if n_players else None
    tactical = dict(block.get("tactical_demand") or {})
    return {
        "stage": "06_validation",
        "mode": mode,
        "estimator": {
            "name": VALIDATION_ESTIMATOR,
            "reason": ("the declared TASK.md Phase 4 fallback, applied to every "
                       "refit so the observed statistic and its permutation null "
                       "use the same estimator; the mixed-model rung is not "
                       "refit 1,000 times. fit_design is the pre-demeaned fast "
                       "path for that rung and a test pins it to "
                       "lc_models.fit_within_player."),
        },
        "specification_under_test": {
            "outcome": outcome, "leverage_column": leverage_col,
            "controls": list(controls),
            "note": "unchanged from 05_models; validation never re-specifies it",
            "primary_model_result": ({
                "beta": primary.get("beta"), "se": primary.get("se"),
                "ci_low": primary.get("ci_low"), "ci_high": primary.get("ci_high"),
                "p": primary.get("p"), "fallback_used": primary.get("fallback_used"),
                "n_efforts": primary.get("n_efforts"),
                "n_players": primary.get("n_players"),
            } if primary else None),
        },
        "sample_sizes": {
            "n_efforts": n_efforts, "n_players": n_players,
            "efforts_per_player": (float(n_efforts / n_players) if n_players else None),
            "mde": mde,
        },
        "permutation": {"reps": int(perm.get("reps", 1000)),
                        "schemes": per_scheme,
                        "block_columns": perm.get("block_columns", {}),
                        "p_value_rule": perm.get("p_value_rule")},
        "orthogonalisation": orthogonalisation,
        "placebo": {
            "checks": placebo,
            "not_available": [
                {"check": "non_max_effort_contrast",
                 "reason": ("the effort table carries only rows that passed the "
                            "max-effort gate; the rows the gate removed are "
                            "counted in efforts_summary.json but not written. The "
                            "contrast needs a new extraction pass, not a refit of "
                            "this table — it is not faked here.")},
                {"check": "matched_placebo",
                 "reason": ("the CEM high-vs-low matched design is a Phase 4 item "
                            "(D65) and is not wired this cycle; the placebos "
                            "above are regression-adjusted with the primary "
                            "controls")},
            ],
        },
        "tactical_demand": {
            "time_to_throw_restriction": bool(tactical.get("time_to_throw_restriction")),
            "defender_distance_available": bool(
                tactical.get("defender_distance_available")),
            "note": ("defender distance is not in the effort table, so the "
                     "tactical-demand check is the time-to-throw restriction "
                     "plus the stated limitation that defensive adjustments "
                     "and play-calling cannot be separated from effort"),
        },
        "garbage_time": garbage,
        "sensitivity": {
            "n_specs": len(specs),
            "primary_row": next((r for r in sensitivity_rows
                                 if r["spec"] == "primary"), None),
            "rows": sensitivity_rows,
        },
        "equivalence": equivalence,
        "edge_cases": edge_cases,
        "not_wired": [
            "Phase 4 matched design (CEM high vs low leverage, clustered bootstrap)",
            "Phase 4 headroom moderation (leverage x baseline utilization)",
            "Phase 4 ceiling bound check (predicted effort fraction vs ceiling)",
            "Phase 4 dose-response across leverage bins",
            "Phase 4 per-player leverage slopes + reliability screen",
            "Phase 4 TE replication",
            "Phase 5 non-max-effort placebo (needs the gate-rejected rows written)",
        ],
        "_permutation_null": {
            scheme: value["null"] for scheme, value in permutation.items()
        },
        "_garbage_rows": (
            [{"sample": "with_garbage_time", **{k: garbage["with_garbage_time"].get(k)
                                                for k in ("beta", "se", "ci_low",
                                                          "ci_high", "p", "n", "n_players")}},
             {"sample": "without_garbage_time",
              **{k: garbage["without_garbage_time"].get(k)
                 for k in ("beta", "se", "ci_low", "ci_high", "p", "n", "n_players")}}]
        ),
        "_orth_rows": [
            {"model": name, **{k: fit.get(k) for k in
                               ("beta", "se", "ci_low", "ci_high", "p", "n", "n_players")}}
            for name, fit in (("naive", orthogonalisation["naive"]),
                              ("orthogonalised", orthogonalisation["orthogonalised"]))
        ],
    }
