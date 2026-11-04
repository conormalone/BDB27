#!/usr/bin/env python3
"""IDR Part 2 — time-to-pressure survival comparison (TASK.md, 2026-11-04).

Binding spec
------------
*"Part 2 (Deficit to Pressure): Time-to-pressure survival model (Cox or AFT).
Include frailties by rusher, crossed effects for blocker/team, and fixed
controls (alignment, blitz, down/distance, score margin)."*

*"Comparison: Hold-out log score comparing the dynamic deficit model vs.
absolute cumulative load model via player-grouped cross-validation."*

Design of the comparison
------------------------
Two Weibull AFT models are fitted **on exactly the same rows, folds, control
set and rung ladder**:

* ``deficit``  — exposure ``deficit_ratio`` (the Phase-3 per-snap dynamic
  deficit; ``a0_snap / baseline_a0``);
* ``absolute`` — exposure ``absolute_load_2p5`` (the cumulative, recency-
  weighted absolute load at the a-priori midpoint of the declared speed grid).

Tuning freedom is equal and degenerate: each arm has a **one-point exposure
grid** declared before any result is seen (``idr_part2.grid_budget: 1``).  The
absolute threshold is the midpoint of ``load.absolute_speed_grid_yds``, not a
value selected against a score; the deficit is used as measured.  One fixed
candidate per arm means neither arm can win through tuning, while both arms
still run the same fold-fitting code path, the same convergence ladder
(``dl_models_core.fit_p2``) and the same per-snap log-score metric.

The two arms are fitted on the common estimation sample — rows that carry
*both* exposures — so every held-out score is paired snap by snap.  Without
that, the paired bootstrap would compare different snaps.

``blitzing`` is the per-player rush/blitz flag carried from ``player_play``
through Phase 3.  It is part of the fixed control list; the artefact reports
its distribution on the modelled snap set (including NA).
"""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from dl import dl_models_core as M

__all__ = ["run_idr_part2", "cross_validate_arm", "idr_part2_verdict",
           "blitzing_distribution"]

#: The per-player blitz/rush-assignment control carried from player_play.
BLITZING_COLUMN = "blitzing"


def blitzing_distribution(frame: pd.DataFrame) -> dict:
    """Counts of ``blitzing`` by value (True/False/NA) on the modelled snaps."""
    column = BLITZING_COLUMN
    n = int(len(frame))
    if column not in frame.columns:
        return {"column": column, "status": "COLUMN_ABSENT", "n": n,
                "counts": {"True": 0, "False": 0, "NA": n},
                "na_share": 1.0 if n else None, "material_na": bool(n),
                "identified": False}
    values = frame[column]
    true_n = int((values == True).sum())  # noqa: E712 - pandas boolean dtype
    false_n = int((values == False).sum())  # noqa: E712
    na_n = int(values.isna().sum())
    na_share = float(na_n / n) if n else None
    return {
        "column": column, "status": "OK", "n": n,
        "counts": {"True": true_n, "False": false_n, "NA": na_n},
        "na_share": na_share,
        "material_na": bool(na_share is not None and na_share >= 0.05),
        # A control with one observed level contributes no design column.
        "identified": bool(true_n > 0 and false_n > 0),
    }


def _reason(code: str, message: str, *, provenance: Any = None) -> dict[str, str]:
    return M._reason(code, message, provenance=provenance)


def _not_estimable(reasons: dict[str, dict[str, str]], code: str,
                   message: str, *, provenance: Any = None) -> None:
    for key in ("deficit", "absolute", "comparison"):
        reasons[key] = _reason(code, message, provenance=provenance)


def _spec(cfg_block: Mapping) -> Mapping:
    spec = cfg_block.get("idr_part2")
    if not isinstance(spec, Mapping):
        raise KeyError("config is missing the bdb27_dl.idr_part2 block")
    return spec


# --------------------------------------------------------------------------- #
# Exposure availability
# --------------------------------------------------------------------------- #
def _with_absolute(frame: pd.DataFrame, cfg: Mapping,
                   spec: Mapping) -> tuple[pd.DataFrame, str]:
    """Return a frame carrying the declared absolute-load column."""
    column = str(spec["absolute_column"])
    if column in frame.columns:
        return frame, column
    panel = M.build_load_panel(frame, cfg)
    if column not in panel.columns:
        raise KeyError(f"load panel does not define {column!r}")
    return pd.concat([frame, panel], axis=1), column


def _finite(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").notna()


# --------------------------------------------------------------------------- #
# One arm, player-grouped CV (same folds for both arms)
# --------------------------------------------------------------------------- #
def cross_validate_arm(data: pd.DataFrame, cfg: Mapping,
                       folds: Mapping[Any, int], *, arm: str,
                       column: str, grid_value: float,
                       provenance: Any = None) -> dict:
    """Held-out log score for one fixed exposure, same shape as the old P2.

    The exposure is fixed a priori (one candidate), so there is no tuning
    search.  The training-fold fit still uses the shared ladder in
    :func:`dl_models_core.fit_p2`; the held-out fold is scored with the
    training moments.
    """
    models = cfg["models"]
    fold_rows: list[dict] = []
    scores: list[pd.DataFrame] = []
    for fold in sorted(set(folds.values())):
        test_mask = data["nfl_id"].map(
            lambda p, f=fold: folds.get(str(p)) == f).to_numpy()
        train = data.loc[~test_mask].copy()
        test = data.loc[test_mask].copy()
        row = {"fold": int(fold), "n_train": int(len(train)),
               "n_test": int(len(test)),
               "n_train_players": int(train["nfl_id"].nunique()),
               "n_test_players": int(test["nfl_id"].nunique()),
               "selected": float(grid_value), "candidates": []}
        if len(train) == 0 or len(test) == 0:
            row.update({"status": "EMPTY_FOLD", "status_code": "EMPTY_FOLD"})
            fold_rows.append(row)
            continue
        fitted = M.fit_p2(train, cfg, load_column=column, with_cov=False,
                          starts=int(models.get("cv_starts", 1)),
                          max_iter=int(models.get("cv_max_iter", 250)))
        row["candidates"] = [{
            "value": float(grid_value),
            "loglik": (float(fitted["fit"].loglik)
                       if fitted["converged"] else None),
            "n_obs": int(fitted["n_obs"]),
            "status": "OK" if fitted["converged"] else "NOT_CONVERGED",
            "rung": fitted["rung_used"],
        }]
        if not fitted["converged"]:
            row.update({"status": "NOT_CONVERGED", "status_code": "NOT_CONVERGED"})
            fold_rows.append(row)
            continue
        try:
            Xte = M.align_test_design(test, fitted["design"].names,
                                      fitted["standardisation"])
            ll = M.per_snap_scores(fitted["fit"], test, Xte)
        except (ValueError, KeyError) as exc:
            row.update({"status": f"SCORING_FAILED: {exc}",
                        "status_code": "SCORING_FAILED"})
            fold_rows.append(row)
            continue
        row.update({"n_scored": int(len(ll)),
                    "mean_logscore": float(np.mean(ll)),
                    "rung": fitted["rung_used"], "status": "OK",
                    "status_code": "OK"})
        fold_rows.append(row)
        payload: dict[str, Any] = {"nfl_id": test["nfl_id"].to_numpy(),
                                   "fold": int(fold), "logscore": ll}
        for key in ("game_id", "play_id"):
            if key in test.columns:
                payload[key] = test[key].to_numpy()
        scores.append(pd.DataFrame(payload))
    combined = (pd.concat(scores, ignore_index=True) if scores
                else pd.DataFrame(columns=["nfl_id", "fold", "logscore"]))
    n_scored = int(len(combined))
    mean_score = float(combined["logscore"].mean()) if n_scored else None
    if n_scored:
        reason = _reason(
            "OK", f"{arm} model scored {n_scored} held-out snaps across "
                  f"{int(combined['fold'].nunique())} player-grouped folds",
            provenance=provenance)
    else:
        reason = _reason(
            "NO_SCORED_FOLD", f"the {arm} model produced no usable held-out "
            "snap in any fold", provenance=provenance)
    return {"arm": arm, "column": column, "grid": [float(grid_value)],
            "selected": float(grid_value), "folds": fold_rows,
            "scores": combined, "n_scored": n_scored,
            "mean_logscore": mean_score, "reason": reason}


def idr_part2_verdict(point: float | None, lo: float | None, hi: float | None,
                      soi: float, margin: float) -> str:
    """Verdict with ``delta = deficit - absolute`` (positive favours deficit)."""
    if point is None or lo is None or hi is None:
        return "INCONCLUSIVE"
    if lo > 0 and point > soi:
        return "DEFICIT_BETTER"
    if hi < 0 and -point > soi:
        return "ABSOLUTE_BETTER"
    if lo > -margin and hi < margin:
        return "EQUIVALENT"
    return "INCONCLUSIVE"


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run_idr_part2(frame: pd.DataFrame, cfg: Mapping, *,
                  provenance: Any = None) -> dict:
    """Fit both arms on a common sample and compare held-out log scores."""
    reasons: dict[str, dict[str, str]] = {
        "deficit": _reason("NOT_RUN", "the deficit model was not reached",
                           provenance=provenance),
        "absolute": _reason("NOT_RUN", "the absolute-load model was not reached",
                            provenance=provenance),
        "comparison": _reason("NOT_RUN", "the comparison was not reached",
                              provenance=provenance),
    }
    spec = _spec(cfg)
    models = cfg["models"]
    fixed_controls = list(models["controls"])
    base = {
        "stage": "idr_part2", "estimand": "time-to-pressure survival",
        "exposures": {"deficit": str(spec["deficit_column"]),
                      "absolute": str(spec["absolute_column"])},
        "absolute_threshold_yds": float(spec["absolute_threshold_yds"]),
        "grid_budget": int(spec.get("grid_budget", 1)),
        "equal_tuning_budget": True,
        "controls": {"fixed": fixed_controls + [BLITZING_COLUMN],
                     "crossed_fixed_effects": list(
                         models.get("crossed_fixed_effects", [])),
                     "plus": ["a0_z (Combine baseline level)"],
                     "blitzing": None},
        "frailty": "log-normal random effect by rusher (ladder: random slope -> "
                   "uncorrelated RE -> random intercept + EB slopes -> RE only)",
        "metric": "held-out per-snap log predictive density (nats/snap)",
        "delta_sign": "deficit - absolute; positive favours the deficit model",
    }

    try:
        work_frame, absolute_column = _with_absolute(frame, cfg, spec)
    except KeyError as exc:
        _not_estimable(reasons, "MISSING_EXPOSURE_COLUMN", str(exc),
                       provenance=provenance)
        base.update({"status": "NOT_ESTIMABLE", "reason": str(exc)})
        base.update({f"reason_{k}": v for k, v in reasons.items()})
        return base

    deficit_column = str(spec["deficit_column"])
    needed = M.required_fields(cfg)
    prepared = M.ensure_a0_z(M._prepare(work_frame, cfg, needed))
    if len(prepared) == 0:
        message = ("no row is a complete case for the §8 control list; the "
                   "time-to-pressure model cannot be fitted")
        _not_estimable(reasons, "NO_COMPLETE_CASES", message,
                       provenance=provenance)
        base.update({"status": "NOT_ESTIMABLE", "reason": message})
        base.update({f"reason_{k}": v for k, v in reasons.items()})
        return base

    deficit_ok = _finite(prepared[deficit_column]) if deficit_column in prepared else None
    absolute_ok = (_finite(prepared[absolute_column])
                   if absolute_column in prepared else None)
    if deficit_ok is None or absolute_ok is None:
        missing = [c for c, ok in ((deficit_column, deficit_ok),
                                   (absolute_column, absolute_ok)) if ok is None]
        message = (f"the snap frame lacks exposure column(s) "
                   f"{', '.join(missing)}; the paired comparison needs both")
        _not_estimable(reasons, "MISSING_EXPOSURE_COLUMN", message,
                       provenance=provenance)
        base.update({"status": "NOT_ESTIMABLE", "reason": message,
                     "n_complete_case_rows": int(len(prepared))})
        base.update({f"reason_{k}": v for k, v in reasons.items()})
        return base

    data = prepared.loc[deficit_ok & absolute_ok].copy()
    blitz = blitzing_distribution(data)
    base["controls"]["blitzing"] = blitz
    n_complete = int(len(prepared))
    coverage = {
        "n_complete_case_rows": n_complete,
        "n_with_deficit": int(deficit_ok.sum()),
        "n_with_absolute": int(absolute_ok.sum()),
        "n_with_both": int(len(data)),
        "share_with_both": float(len(data) / n_complete) if n_complete else 0.0,
    }
    min_rows = int(spec.get("min_rows", 100))
    min_players = int(spec.get("min_players", 8))
    min_events = int(spec.get("min_events", 20))
    if (len(data) < min_rows or data["nfl_id"].nunique() < min_players
            or int(data["event"].sum()) < min_events):
        message = (
            f"the common estimation sample has {len(data)} snaps from "
            f"{data['nfl_id'].nunique()} rushers and "
            f"{int(data['event'].sum())} pressure events; a paired survival "
            f"comparison needs >= {min_rows} snaps, >= {min_players} rushers "
            f"and >= {min_events} events (deficit coverage "
            f"{coverage['n_with_deficit']}/{n_complete}, absolute coverage "
            f"{coverage['n_with_absolute']}/{n_complete}, both "
            f"{coverage['n_with_both']}/{n_complete})")
        _not_estimable(reasons, "INSUFFICIENT_COMMON_SAMPLE", message,
                       provenance=provenance)
        base.update({"status": "NOT_ESTIMABLE", "reason": message,
                     "coverage": coverage})
        base.update({f"reason_{k}": v for k, v in reasons.items()})
        return base

    mapping, info = M.assign_folds(
        data["nfl_id"].unique(), int(models["cv_folds"]),
        int(cfg["seeds"]["model"]), int(models.get("min_rushers_per_fold", 10)))
    data["fold"] = data["nfl_id"].map(lambda p: mapping.get(str(p)))
    grid_value_abs = float(spec["absolute_threshold_yds"])
    grid_value_def = float(spec.get("deficit_grid_value", 1.0))

    # Both arms get the identical control list, which includes the per-player
    # blitzing flag when the snap table carries it (it does after Phase 3).
    cfg_arm = copy.deepcopy(cfg)
    if "blitzing" in data.columns:
        cfg_arm["models"]["controls"] = list(models["controls"]) + [BLITZING_COLUMN]
        cfg_arm["models"]["categorical_controls"] = list(
            models.get("categorical_controls", [])) + [BLITZING_COLUMN]

    deficit_cv = cross_validate_arm(data, cfg_arm, mapping, arm="deficit",
                                    column=deficit_column,
                                    grid_value=grid_value_def,
                                    provenance=provenance)
    absolute_cv = cross_validate_arm(data, cfg_arm, mapping, arm="absolute",
                                     column=absolute_column,
                                     grid_value=grid_value_abs,
                                     provenance=provenance)
    reasons["deficit"] = deficit_cv["reason"]
    reasons["absolute"] = absolute_cv["reason"]

    base.update({
        "status": "OK", "fold_info": info, "coverage": coverage,
        "n_snaps": int(len(data)), "n_players": int(data["nfl_id"].nunique()),
        "n_events": int(data["event"].sum()),
        "deficit": {k: v for k, v in deficit_cv.items() if k != "scores"},
        "absolute": {k: v for k, v in absolute_cv.items() if k != "scores"},
        "grids": {"deficit": deficit_cv["grid"],
                  "absolute": absolute_cv["grid"]},
    })

    bad = [name for name, cv in (("deficit", deficit_cv),
                                 ("absolute", absolute_cv))
           if cv["mean_logscore"] is None
           or not math.isfinite(float(cv["mean_logscore"]))]
    if bad:
        which = " and ".join(bad)
        if info.get("folds", 0) < 2:
            detail = (f"the player-grouped folds collapsed to {info.get('folds')} "
                      f"(only {info.get('n_players')} rushers carry both "
                      f"exposures; §9 needs >= "
                      f"{2 * int(models.get('min_rushers_per_fold', 10))} for two "
                      "folds), so there is no held-out fold to score")
        else:
            detail = (f"the {which} model produced no usable held-out score in "
                      f"{info.get('folds')} fold(s)")
        message = (f"the paired comparison was not reached: {detail}; common "
                   f"sample {coverage['n_with_both']}/{n_complete} complete-case "
                   f"snaps (deficit {coverage['n_with_deficit']}, absolute "
                   f"{coverage['n_with_absolute']})")
        reasons["comparison"] = _reason("UPSTREAM_NA", message,
                                        provenance=provenance)
        base.update({"status": "NOT_ESTIMABLE", "reason": message,
                     "n_snaps": int(len(data)),
                     "n_players": int(data["nfl_id"].nunique()),
                     "n_events": int(data["event"].sum())})
        base.update({f"reason_{k}": v for k, v in reasons.items()})
        return base

    # Reuse the D78-hardened snap-by-snap join (one-to-one, inflation guard).
    merged = M.join_definition_scores(absolute_cv["scores"], deficit_cv["scores"])
    if merged.empty:
        message = ("the two arms share no scored snap on "
                   f"{list(M.SCORE_JOIN_KEYS)}; the paired bootstrap is undefined")
        reasons["comparison"] = _reason("NO_SHARED_SCORED_SNAPS", message,
                                        provenance=provenance)
        base.update({"status": "NOT_ESTIMABLE", "reason": message,
                     "n_scored_snaps": 0,
                     "difference_nats_per_snap": None,
                     "difference_interval": [None, None]})
        base.update({f"reason_{k}": v for k, v in reasons.items()})
        return base

    # Hard guards: the join must stay one-to-one and must not inflate.
    keys = list(M.SCORE_JOIN_KEYS)
    assert not merged.duplicated(subset=keys, keep=False).any(), \
        "IDR Part 2 join is not one-to-one"
    assert len(merged) <= min(len(absolute_cv["scores"]),
                              len(deficit_cv["scores"])), \
        "IDR Part 2 join inflated the scored rows"

    boot = M.bootstrap_delta(merged, int(models["bootstrap_reps"]),
                             int(cfg["seeds"]["bootstrap"]))
    soi = float(cfg["validate"]["equivalence"]["soi_logscore"])
    margin = float(cfg["validate"]["equivalence"]["margin_logscore"])
    point = boot["estimate"]
    if point is None or not math.isfinite(float(point)):
        message = (f"the paired comparison has {int(len(merged))} shared scored "
                   "snaps but its mean log-score difference is not finite")
        reasons["comparison"] = _reason("NONFINITE_DELTA", message,
                                        provenance=provenance)
        base.update({"status": "NOT_ESTIMABLE", "reason": message,
                     "n_scored_snaps": int(len(merged))})
        base.update({f"reason_{k}": v for k, v in reasons.items()})
        return base

    verdict = idr_part2_verdict(point, boot["lo"], boot["hi"], soi, margin)
    reasons["comparison"] = _reason(
        "OK", f"paired comparison estimated on {int(len(merged))} snaps scored "
              f"by both arms across {int(len(set(merged['fold'])))} fold(s)",
        provenance=provenance)
    base.update({
        "status": "OK",
        "mean_logscore": {"deficit": deficit_cv["mean_logscore"],
                          "absolute": absolute_cv["mean_logscore"]},
        "difference_nats_per_snap": float(point),
        "difference_interval": [boot["lo"], boot["hi"]],
        "bootstrap": boot, "soi": soi, "equivalence_margin": margin,
        "excludes_zero": bool(boot["lo"] > 0 or boot["hi"] < 0),
        "exceeds_soi": bool(abs(float(point)) > soi),
        "better_arm": ("deficit" if float(point) > 0 else "absolute"),
        "verdict": verdict,
        "n_scored_snaps": int(len(merged)),
        "n_scored_players": int(merged["nfl_id"].nunique()),
    })
    base.update({f"reason_{k}": v for k, v in reasons.items()})
    return base
