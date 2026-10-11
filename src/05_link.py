"""Phase 5 — link reliable combine WARM-UP phenotypes to in-game ramping (BDB27).

Reframe (D25, 2026-10-11). The pre-registered fatigue-slope -> game-decay link is
**GATED OFF** (D20: combine fatigue-slope reliability 0.0). The viable link is Conor's
pivot: **reliable combine warm-up phenotypes -> in-game ramping/decay** (early-game
output, snaps-to-peak, ramp/decay slope). The warm-up phenotype de-confounding was
**NOT robust** (D24), so this link is **EXPLORATORY and clearly labelled**.

Design
------
- **Predictor** = the per-player combine warm-up slope for the declared primary cell
  ``L5(prior_load_efforts) x O2(peak_accel_z)`` (reliability 0.5934; D21/D23), with the
  ``L4(prior_load_accel) x O2`` cell as the declared secondary/robustness phenotype.
- **Outcome** = the per-player in-game **ramp slope** from ``04_game_features.py``.
- **Controls**: baseline speed (combine peak speed), height, weight, and draft position
  (``players.csv``; **undrafted is its own level**, D13/A12).
- **Error-in-both-slopes**: inverse-variance weighting of the OUTCOME regression
  (weights ``1/SE_game^2``) + a **reliability-corrected predictor effect** (regression
  calibration: ``beta_corrected = beta / R_predictor``); reported **alongside** the naive OLS.
- **Player-grouped CV** with held-out **delta-R^2** + bootstrap CIs.
- **Placebo**: the combine phenotype must NOT predict **early-game** output.
- **Sensitivity**: alternate cells + excluding low-snap players.
- **Selection limitation**: only players who reached NFL games have game data; reported
  plainly, with the matched Combine<->NFL counts (audit e / P1 / P8).

Outputs (``outputs/link/``): ``link_results.csv``, ``link_cv.csv``, ``link_placebo.csv``,
``link_sensitivity.csv``, ``link_matched_counts.csv``, ``SUMMARY.md``, ``PROVENANCE.txt``.
Sample outputs carry ``UNVALIDATED SAMPLE OUTPUT``.

Run:
    python src/05_link.py --mode sample
    python src/05_link.py --mode full
"""

from __future__ import annotations

import argparse
import math
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    append_run_log,
    get_logger,
    load_config,
    peak_ram_mb,
    provenance,
    resolve,
    seed_everything,
    timed_stage,
)

RESULT_COLS = ["provenance", "model", "outcome", "predictor", "n", "coef", "se", "p",
               "r2", "delta_r2", "coef_corrected", "reliability_predictor", "note"]


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def _int_key(df: pd.DataFrame, col: str = "nfl_id") -> pd.DataFrame:
    df = df.copy()
    df[col] = pd.to_numeric(df[col], errors="coerce").astype("int64")
    return df


def load_phenotype(cfg: dict[str, Any], load_code: str, outcome_code: str) -> pd.DataFrame:
    """Per-player warm-up slope for one cell (load_code, outcome_code)."""
    path = Path(cfg["_config_path"]).parent / cfg["link"]["phenotype_source"]
    if not path.exists():
        raise FileNotFoundError(f"missing phenotype source: {path} (run src/03d_warmup_phenotype.py)")
    df = pd.read_csv(path)
    sub = df[(df["load_code"] == load_code) & (df["outcome_code"] == outcome_code)].copy()
    sub = _int_key(sub)
    sub = sub[['nfl_id', 'slope_raw', 'warmup_slope_se', 'shrinkage_weight', 'n_attempts']].rename(
        columns={'slope_raw': 'warmup', 'warmup_slope_se': 'warmup_se',
                 'shrinkage_weight': 'warmup_shrink_w', 'n_attempts': 'combine_n_attempts'})
    return sub


def predictor_reliability(cfg: dict[str, Any], load_code: str, outcome_code: str) -> float:
    """Reliability (R) of the warm-up slope for a cell, from warmup_reliability.csv."""
    path = Path(cfg["_config_path"]).parent / cfg["link"]["reliability_source"]
    if not path.exists():
        return float("nan")
    df = pd.read_csv(path)
    sub = df[(df["phenotype"] == "warmup_slope")]
    if "source" in sub.columns:
        sel = sub[sub["source"].astype(str).str.startswith("selected")]
        if len(sel):
            sub = sel
    det = sub["detail"].astype(str)
    m = sub[det.str.startswith(f"{load_code}(") & det.str.contains(f"{outcome_code}(", regex=False)]
    if len(m):
        return float(m["reliability"].iloc[0])
    return float("nan")


def load_analysis_frame(cfg: dict[str, Any], logger) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Merge warm-up phenotype + game ramp slope + controls; DB study population only."""
    L = cfg["link"]
    root = Path(cfg["_config_path"]).parent
    prov = str(cfg["mode"][str(cfg["mode"]["default"])]["provenance"])

    phen = load_phenotype(cfg, L["primary_load"], L["primary_outcome"])
    game = _int_key(pd.read_parquet(root / L["game_slopes_source"]))
    game = game[["nfl_id", "n_plays", "n_games", "ramp_slope_shrunk", "ramp_slope_se",
                 "early_output_z", "meets_min_snaps", "reliability"]].rename(
        columns={"ramp_slope_shrunk": "game_ramp", "ramp_slope_se": "game_se",
                 "reliability": "game_reliability"})
    pf = _int_key(pd.read_parquet(root / L["player_frame_source"]))
    pf = pf[["nfl_id", "combine_position", "in_study_population", L["baseline_speed_col"]]].rename(
        columns={L["baseline_speed_col"]: "baseline_speed"})

    cr_path = Path(cfg["_config_path"]).parent / cfg["paths"]["raw"] / cfg["files"]["combine_results"]
    cr = pd.read_csv(cr_path)[["nfl_id", "combine_height", "combine_weight"]]
    cr = _int_key(cr)

    pl_path = Path(cfg["_config_path"]).parent / cfg["paths"]["raw"] / cfg["files"]["players"]
    pl = pd.read_csv(pl_path)[["nfl_id", "draft_overall_pick", "draft_year"]]
    pl = _int_key(pl)
    pl["is_undrafted"] = pl["draft_overall_pick"].isna().astype(int)
    pl["draft_position"] = pl["draft_overall_pick"]

    study = pf[pf["in_study_population"].astype(bool)][["nfl_id"]]
    af = study.merge(phen, on="nfl_id", how="left") \
             .merge(game, on="nfl_id", how="left") \
             .merge(pf[["nfl_id", "baseline_speed"]], on="nfl_id", how="left") \
             .merge(cr, on="nfl_id", how="left") \
             .merge(pl[["nfl_id", "draft_position", "is_undrafted", "draft_year"]], on="nfl_id", how="left")

    counts = {
        "n_study_players": int(study["nfl_id"].nunique()),
        "n_with_warmup_phenotype": int(af["warmup"].notna().sum()),
        "n_with_game_data": int(af["game_ramp"].notna().sum()),
        "n_matched": int((af["warmup"].notna() & af["game_ramp"].notna()).sum()),
        "n_matched_meets_min_snaps": int((af["warmup"].notna() & af["game_ramp"].notna()
                                          & af["meets_min_snaps"].fillna(False).astype(bool)).sum()),
        "min_link_n": int(L["min_link_n"]),
    }
    logger.info("matched counts: %s", counts)
    return af, counts


# --------------------------------------------------------------------------- #
# Regression helpers
# --------------------------------------------------------------------------- #
def _control_matrix(df: pd.DataFrame, controls: list[str]) -> pd.DataFrame:
    """Build the control design (undrafted handled as its own level via indicator)."""
    X = pd.DataFrame(index=df.index)
    for c in controls:
        if c == "draft_position":
            v = df["draft_position"].to_numpy(dtype=np.float64)
            m = float(np.nanmean(v)) if np.isfinite(v).any() else 0.0
            X["draft_position"] = np.where(np.isfinite(v), v, m)
            X["is_undrafted"] = df["is_undrafted"].to_numpy(dtype=np.float64)
        else:
            v = df[c].to_numpy(dtype=np.float64)
            m = float(np.nanmean(v)) if np.isfinite(v).any() else 0.0
            X[c] = np.where(np.isfinite(v), v, m)
    return X.reset_index(drop=True)


def _fit(df: pd.DataFrame, outcome: str, predictor: str, controls: list[str],
         weights: np.ndarray | None = None) -> dict[str, Any]:
    """OLS/WLS of ``outcome`` on ``predictor`` + controls; return the predictor row + R^2.

    A zero-variance predictor is unidentifiable (EB-shrunken warm-up slopes collapse to
    the population mean when tau2=0) -> the fit returns NaN rather than a spurious slope.
    """
    pv = df[predictor].to_numpy(dtype=np.float64)
    df = df.reset_index(drop=True)
    pv = df[predictor].to_numpy(dtype=np.float64)
    nan = {"coef": float("nan"), "se": float("nan"), "p": float("nan"),
           "r2": float("nan"), "r2_controls": float("nan"), "delta_r2": float("nan"),
           "n": int(len(df))}
    if not np.isfinite(pv).all() or np.unique(pv[np.isfinite(pv)]).size < 2:
        return nan
    y = df[outcome].to_numpy(dtype=np.float64)
    Xc = _control_matrix(df, controls)
    Xf = sm.add_constant(pd.DataFrame({"predictor": df[predictor].to_numpy(dtype=np.float64)}).join(Xc),
                         has_constant="add")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = sm.WLS(y, Xf.astype(np.float64), weights=weights).fit() if weights is not None \
            else sm.OLS(y, Xf.astype(np.float64)).fit()
        r2 = float(res.rsquared)
        pidx = list(Xf.columns).index("predictor")
        Xc2 = sm.add_constant(Xc, has_constant="add")
        res0 = sm.WLS(y, Xc2.astype(np.float64), weights=weights).fit() if weights is not None \
            else sm.OLS(y, Xc2.astype(np.float64)).fit()
    return {
        "coef": float(res.params.iloc[pidx]), "se": float(res.bse.iloc[pidx]),
        "p": float(res.pvalues.iloc[pidx]), "r2": r2, "r2_controls": float(res0.rsquared),
        "delta_r2": r2 - float(res0.rsquared), "n": int(len(df)),
    }


def _oof_delta_r2(df: pd.DataFrame, outcome: str, predictor: str, controls: list[str],
                  n_folds: int, seed: int) -> float:
    """Player-grouped CV: held-out delta-R^2 = R^2(full) - R^2(controls-only), pooled OOF."""
    rng = np.random.default_rng(seed)
    df = df.reset_index(drop=True)
    ids = df["nfl_id"].to_numpy()
    uniq = np.unique(ids)
    if len(uniq) < n_folds:
        n_folds = max(2, len(uniq))
    order = rng.permutation(len(uniq))
    fold_of = {uniq[order[i]]: i % n_folds for i in range(len(uniq))}
    fold = np.array([fold_of[i] for i in ids])

    yhat_f = np.full(len(df), np.nan)
    yhat_c = np.full(len(df), np.nan)
    for f in range(n_folds):
        tr = np.where(fold != f)[0]
        te = np.where(fold == f)[0]
        if tr.size < 2 or te.size < 1:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                y = df[outcome].to_numpy(dtype=np.float64)
                dtr = df.iloc[tr].reset_index(drop=True)
                dte = df.iloc[te].reset_index(drop=True)
                Xc_tr = _control_matrix(dtr, controls)
                Xf_tr = sm.add_constant(pd.DataFrame({"predictor": dtr[predictor].to_numpy(dtype=np.float64)}).join(Xc_tr), has_constant="add")
                mf = sm.OLS(y[tr], Xf_tr.astype(np.float64)).fit()
                Xc_te = _control_matrix(dte, controls)
                Xf_te = sm.add_constant(pd.DataFrame({"predictor": dte[predictor].to_numpy(dtype=np.float64)}).join(Xc_te), has_constant="add")
                Xf_te = Xf_te.reindex(columns=Xf_tr.columns, fill_value=0.0)
                yhat_f[te] = mf.predict(Xf_te.astype(np.float64))
                Xc2_tr = sm.add_constant(Xc_tr, has_constant="add")
                mc = sm.OLS(y[tr], Xc2_tr.astype(np.float64)).fit()
                Xc2_te = sm.add_constant(Xc_te, has_constant="add")
                Xc2_te = Xc2_te.reindex(columns=Xc2_tr.columns, fill_value=0.0)
                yhat_c[te] = mc.predict(Xc2_te.astype(np.float64))
        except Exception:  # noqa: BLE001
            continue
    ok = np.isfinite(yhat_f) & np.isfinite(yhat_c)
    if ok.sum() < 3:
        return float("nan")
    y = df[outcome].to_numpy(dtype=np.float64)[ok]
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot <= 0:
        return float("nan")
    r2_f = 1.0 - float(np.sum((y - yhat_f[ok]) ** 2)) / ss_tot
    r2_c = 1.0 - float(np.sum((y - yhat_c[ok]) ** 2)) / ss_tot
    return float(r2_f - r2_c)


def _bootstrap_delta_r2(df: pd.DataFrame, outcome: str, predictor: str, controls: list[str],
                        n_folds: int, seed: int, reps: int) -> tuple[float, float, float]:
    """Bootstrap CI for the OOF delta-R^2 (resample players with replacement)."""
    rng = np.random.default_rng(seed)
    ids = np.unique(df["nfl_id"].to_numpy())
    vals: list[float] = []
    for _ in range(int(reps)):
        pick = rng.choice(ids, size=len(ids), replace=True)
        idx = np.concatenate([np.where(df["nfl_id"].to_numpy() == p)[0] for p in pick])
        sub = df.iloc[idx].copy()
        # break duplicate-player grouping deterministically for resampled duplicates
        d = _oof_delta_r2(sub, outcome, predictor, controls, n_folds, int(rng.integers(0, 2**31 - 1)))
        if np.isfinite(d):
            vals.append(d)
    v = np.array(vals, dtype=np.float64)
    if v.size == 0:
        return float("nan"), float("nan"), float("nan")
    lo, hi = np.percentile(v, [2.5, 97.5])
    return float(np.mean(v)), float(lo), float(hi)


# --------------------------------------------------------------------------- #
# Result builders
# --------------------------------------------------------------------------- #
def _row(prov: str, model: str, outcome: str, predictor: str, r: dict[str, Any],
         coef_corrected: float, rel: float, note: str) -> dict[str, Any]:
    return {"provenance": prov, "model": model, "outcome": outcome, "predictor": predictor,
            "n": r.get("n"), "coef": r.get("coef"), "se": r.get("se"), "p": r.get("p"),
            "r2": r.get("r2"), "delta_r2": r.get("delta_r2"),
            "coef_corrected": coef_corrected, "reliability_predictor": rel, "note": note}


def build_results(af: pd.DataFrame, cfg: dict[str, Any], prov: str, logger) -> pd.DataFrame:
    """Naive OLS + error-in-both-slopes (WLS + reliability-corrected) rows."""
    L = cfg["link"]
    controls = list(L["controls"])
    d = af[af["warmup"].notna() & af["game_ramp"].notna()].copy()
    d = d[np.isfinite(d["game_se"].to_numpy(dtype=np.float64)) & (d["game_se"].to_numpy(dtype=np.float64) > 0)]
    rows: list[dict[str, Any]] = []
    if len(d) < 3:
        logger.warning("too few matched players (%d) for the link — reporting underpowered", len(d))
        return pd.DataFrame([_row(prov, "NAIVE_OLS", "game_ramp", "warmup",
                                 {"n": len(d), "coef": np.nan, "se": np.nan, "p": np.nan,
                                  "r2": np.nan, "delta_r2": np.nan}, np.nan, np.nan,
                                 "UNDERPOWERED: <3 matched players")], columns=RESULT_COLS)

    rel = predictor_reliability(cfg, L["primary_load"], L["primary_outcome"])
    note_pred = ("predictor = per-player RAW warm-up slope (slope_raw); the EB-shrunken "
                 "warmup_slope collapses to the population mean (tau2=0 under the EB path)")
    # naive OLS
    rn = _fit(d, "game_ramp", "warmup", controls)
    rows.append(_row(prov, "NAIVE_OLS", "game_ramp", "warmup", rn, rn["coef"], rel,
                     "naive (no error correction); " + note_pred))
    # WLS: inverse-variance weighting of the outcome
    w = 1.0 / d["game_se"].to_numpy(dtype=np.float64) ** 2
    rw = _fit(d, "game_ramp", "warmup", controls, weights=w)
    corr = rw["coef"] / rel if (np.isfinite(rel) and rel > 0) else np.nan
    corr_se = rw["se"] / rel if (np.isfinite(rel) and rel > 0) else np.nan
    rows.append(_row(prov, "EIV_CORRECTED", "game_ramp", "warmup",
                     {**rw, "se": corr_se}, corr, rel,
                     f"WLS(1/game_se^2) + regression-calibration /R_predictor (R={rel:.4f}); " + note_pred))
    return pd.DataFrame(rows, columns=RESULT_COLS)


def build_cv(af: pd.DataFrame, cfg: dict[str, Any], prov: str, logger) -> pd.DataFrame:
    L = cfg["link"]
    controls = list(L["controls"])
    d = af[af["warmup"].notna() & af["game_ramp"].notna()].copy()
    if len(d) < 4:
        return pd.DataFrame([{"provenance": prov, "outcome": "game_ramp", "predictor": "warmup",
                              "cv_folds": L["cv_folds"], "delta_r2_point": np.nan,
                              "delta_r2_boot_mean": np.nan, "ci_lo": np.nan, "ci_hi": np.nan,
                              "n": len(d), "note": "UNDERPOWERED"}])
    pt = _oof_delta_r2(d, "game_ramp", "warmup", controls, int(L["cv_folds"]), int(L["cv_seed"]))
    mean, lo, hi = _bootstrap_delta_r2(d, "game_ramp", "warmup", controls, int(L["cv_folds"]),
                                       int(L["bootstrap_seed"]), int(L["bootstrap_reps"]))
    return pd.DataFrame([{"provenance": prov, "outcome": "game_ramp", "predictor": "warmup",
                          "cv_folds": int(L["cv_folds"]), "delta_r2_point": pt,
                          "delta_r2_boot_mean": mean, "ci_lo": lo, "ci_hi": hi,
                          "n": int(len(d)),
                          "note": "player-grouped CV, held-out delta-R^2 (full - controls-only)"}])


def build_placebo(af: pd.DataFrame, cfg: dict[str, Any], prov: str, logger) -> pd.DataFrame:
    L = cfg["link"]
    controls = list(L["controls"])
    d = af[af["warmup"].notna() & af[L["placebo_outcome"]].notna()].copy()
    rows: list[dict[str, Any]] = []
    if len(d) < 3:
        return pd.DataFrame([{"provenance": prov, "outcome": L["placebo_outcome"],
                              "predictor": "warmup", "n": len(d), "coef": np.nan, "se": np.nan,
                              "p": np.nan, "r2": np.nan, "delta_r2": np.nan,
                              "note": "UNDERPOWERED"}])
    r = _fit(d, L["placebo_outcome"], "warmup", controls)
    rows.append({"provenance": prov, "outcome": L["placebo_outcome"], "predictor": "warmup",
                 "n": r["n"], "coef": r["coef"], "se": r["se"], "p": r["p"],
                 "r2": r["r2"], "delta_r2": r["delta_r2"],
                 "note": "PLACEBO: phenotype must NOT predict early-game output"})
    return pd.DataFrame(rows)


def build_sensitivity(af: pd.DataFrame, cfg: dict[str, Any], prov: str, logger) -> pd.DataFrame:
    L = cfg["link"]
    controls = list(L["controls"])
    rows: list[dict[str, Any]] = []
    # primary
    d = af[af["warmup"].notna() & af["game_ramp"].notna()].copy()
    if len(d) >= 3:
        r = _fit(d, "game_ramp", "warmup", controls)
        rows.append({"provenance": prov, "variant": "primary_L5_O2_all", "n": r["n"],
                     "coef": r["coef"], "se": r["se"], "p": r["p"], "r2": r["r2"],
                     "delta_r2": r["delta_r2"]})
    # exclude low-snap players
    if bool(L["sens_exclude_low_snaps"]):
        dh = d[d["meets_min_snaps"].fillna(False).astype(bool)]
        if len(dh) >= 3:
            r = _fit(dh, "game_ramp", "warmup", controls)
            rows.append({"provenance": prov, "variant": "exclude_low_snaps", "n": r["n"],
                         "coef": r["coef"], "se": r["se"], "p": r["p"], "r2": r["r2"],
                         "delta_r2": r["delta_r2"]})
    # alternate cells
    for cell in L["sens_cells"]:
        lc, oc = str(cell[0]), str(cell[1])
        try:
            ph = load_phenotype(cfg, lc, oc)
        except Exception:  # noqa: BLE001
            continue
        dd = af.drop(columns=[c for c in ["warmup", "warmup_se", "warmup_shrink_w",
                                          "combine_n_attempts"] if c in af.columns]).merge(
            ph.rename(columns={"warmup": "w2", "warmup_se": "w2_se"}), on="nfl_id", how="left")
        dd = dd[dd["w2"].notna() & dd["game_ramp"].notna()]
        if len(dd) >= 3:
            r = _fit(dd, "game_ramp", "w2", controls)
            rows.append({"provenance": prov, "variant": f"cell_{lc}_{oc}", "n": r["n"],
                         "coef": r["coef"], "se": r["se"], "p": r["p"], "r2": r["r2"],
                         "delta_r2": r["delta_r2"]})
    return pd.DataFrame(rows)


def build_matched_counts(counts: dict[str, Any], cfg: dict[str, Any], prov: str) -> pd.DataFrame:
    rows = [{"provenance": prov, "metric": k, "value": v} for k, v in counts.items()]
    if counts["n_matched"] < int(cfg["link"]["min_link_n"]):
        rows.append({"provenance": prov, "metric": "underpowered_flag", "value": 1})
        rows.append({"provenance": prov, "metric": "finding",
                     "value": "matched Combine<->NFL sample too small for the link (real finding)"})
    else:
        rows.append({"provenance": prov, "metric": "underpowered_flag", "value": 0})
    return pd.DataFrame(rows)


def _summary_md(results: pd.DataFrame, cv: pd.DataFrame, placebo: pd.DataFrame,
                counts: dict[str, Any], cfg: dict[str, Any], prov: str) -> str:
    L = cfg["link"]
    lines = [
        "# Phase 5 — warm-up phenotype -> in-game ramping (link SUMMARY)",
        "",
        f"**Provenance:** `{prov}`  ",
        "**EXPLORATORY** — the pre-registered fatigue-slope->decay link is GATED OFF (D20); "
        "the warm-up de-confounding was NOT robust (D24).",
        "",
        f"**Primary predictor:** `warmup_slope` {L['primary_load']}×{L['primary_outcome']}  ",
        f"**Outcome:** in-game ramp slope (`game_ramp`)  ",
        f"**Controls:** {', '.join(L['controls'])} (undrafted = own level)",
        "",
        "## Matched Combine<->NFL counts (audit e / P1 / P8)",
        "",
    ]
    for k in sorted(counts):
        lines.append(f"- `{k}`: {counts[k]}")
    lines += ["", "## Link regression (see `link_results.csv`)", ""]
    lines.append("```")
    lines.append(results.to_string(index=False))
    lines.append("```")
    lines += ["", "## Cross-validation (see `link_cv.csv`)", ""]
    lines.append("```")
    lines.append(cv.to_string(index=False))
    lines.append("```")
    lines += ["", "## Placebo (see `link_placebo.csv`)", ""]
    lines.append("```")
    lines.append(placebo.to_string(index=False))
    lines.append("```")
    lines += [
        "",
        "## Selection limitation",
        "",
        "Only players who reached NFL games have game data; the link is therefore "
        "conditional on (combine participant AND drafted/signed AND on-field tracked). "
        "Interpret with the matched-N above.",
        "",
    ]
    if prov == "UNVALIDATED SAMPLE OUTPUT":
        lines.insert(1, "\n> ⚠️ **UNVALIDATED SAMPLE OUTPUT** — never interpret or tune against this.\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    prov = provenance(cfg, mode)
    out_dir = resolve(cfg, "outputs", cfg["link"]["out_subdir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 72)
    logger.info("BDB27 Phase 5 link | mode=%s | provenance=%s", mode, prov)
    logger.info("=" * 72)

    res_path = out_dir / "link_results.csv"
    if res_path.exists() and not force:
        logger.info("checkpoint hit: %s exists (use --force)", res_path.name)
        append_run_log(cfg, "05_link:checkpoint", mode, 0.0, peak_ram_mb(), "",
                       {"skipped": True, "reason": "link_results.csv exists"})
        return 0

    with timed_stage(cfg, "05_link:build", mode):
        af, counts = load_analysis_frame(cfg, logger)

    with timed_stage(cfg, "05_link:results", mode):
        results = build_results(af, cfg, prov, logger)
    with timed_stage(cfg, "05_link:cv", mode):
        cv = build_cv(af, cfg, prov, logger)
    with timed_stage(cfg, "05_link:placebo", mode):
        placebo = build_placebo(af, cfg, prov, logger)
    with timed_stage(cfg, "05_link:sensitivity", mode):
        sens = build_sensitivity(af, cfg, prov, logger)
    matched = build_matched_counts(counts, cfg, prov)

    with timed_stage(cfg, "05_link:write", mode):
        results.to_csv(out_dir / "link_results.csv", index=False)
        cv.to_csv(out_dir / "link_cv.csv", index=False)
        placebo.to_csv(out_dir / "link_placebo.csv", index=False)
        sens.to_csv(out_dir / "link_sensitivity.csv", index=False)
        matched.to_csv(out_dir / "link_matched_counts.csv", index=False)
        (out_dir / "SUMMARY.md").write_text(
            _summary_md(results, cv, placebo, counts, cfg, prov), encoding="utf-8")
        (out_dir / "PROVENANCE.txt").write_text(
            f"provenance: {prov}\nmode: {mode}\nstage: 05_link\n"
            f"primary_predictor: warmup_slope L{cfg['link']['primary_load']}x{cfg['link']['primary_outcome']}\n"
            f"outcome: game ramp slope\nnote: EXPLORATORY (D20 gate; D24 de-confounding not robust)\n",
            encoding="utf-8")

    logger.info("link done: n_matched=%d (min_link_n=%d)",
                counts["n_matched"], counts["min_link_n"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 5 — link")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild outputs (ignore checkpoint)")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or str(cfg["mode"]["default"])
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
