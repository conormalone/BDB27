"""Phase 3d — warm-up / potentiation phenotype for the BDB27 combine-fatigue study.

HEADLINE PIVOT. Phase 3 reported a *null* on the primary fatigue metric (D20) and the
D21 metric panel showed the null is **metric-robust for the fatigue direction**. The only
cells that cleared reliability >= 0.2 were the **accel** cells ``prior_load_accel x
peak_accel_z``, ``prior_load_efforts x peak_accel_z`` and ``prior_load_efforts x t90_z``
— all with a **POSITIVE** load coefficient, i.e. *more prior effort -> faster/harder
explosive output* = **warm-up / post-activation potentiation**, not fatigue.

This module implements the **pre-registered** Phase-3d protocol (``config.yaml``
``warmup:`` block + ``notes/decisions.md`` **D23**), written before any Phase-3d result
was computed (no HARKing). All thresholds/params are FIXED there; nothing here is tuned
against results. No Phase-4/5 work; ``01``/``02``/``03``/``03b`` behaviour and outputs
must remain **byte-identical** (this module is import-only w.r.t. them).

What it does
------------
1. **Deterministic study frame.** Rebuilds the Phase-2/3b frame (DB-only) exactly as
   ``03b_metric_panel.py`` does (DB-only ``combine_features_study.parquet``; ordering
   reproduced and **validated** by requiring ``cumsum(effort_cost_yd) == prior_load_yd``)
   and adds, per attempt: ``session_rank`` (1..K within the player's single session, by
   real timestamps), ``drill_type`` (present) and **within-drill accumulated prior
   effort** (cumulative effort/efforts/accel of earlier reps of the **same** ``drill_type``).
2. **Outcome O4 ``peak_accel_sg_z``** (D22 item 4 / D23 §2): Savitzky-Golay smooth x/y
   (``accel_smooth_window_frames``, ``accel_smooth_polyorder``), signed accel from the
   smoothed speed, clip to ``accel_clip_yds2``, peak, standardise within
   ``(drill_type, combine_position)`` over observed attempts (Phase-2 rule). Imputed rows
   get no performance value.
3. **Cells** = ``warmup.load_metrics`` (L1 yd, L5 efforts, L4 accel) x
   ``warmup.outcome_metrics`` (O1 perf_z, O2 peak_accel_z, O4 peak_accel_sg_z). Each cell
   runs the **SAME** Phase-3 fallback chain + reliability + stop rule as Phase 3/3b
   (delegating to ``03b_metric_panel.run_cell``); per-cell permutation ``meta_slope``.
4. **(1) DE-CONFOUNDING** (the make-or-break, D23 §1). For the accel-outcome cells we
   report the **raw** population slope, the **drill + session_rank-ADJUSTED** slope
   (``C(drill_type)`` fixed effects + explicit ``session_rank``; the EB analogue is
   within-drill centring — both reported) and the **within-drill (W) rep-1 -> rep-2
   battery contrast**. ``deconf_pass`` follows the pre-registered PASS/FAIL rule.
   Raw/adj/within numbers and the flag are recorded **even when it FAILS**.
5. **(2) Per-player phenotype** (``phenotype_min_attempts`` observed): shrunken
   ``warmup_slope`` per cell (exactly as D20 §8); ``session_position_of_peak`` (rank 1..K
   of the player's max outcome) on ``primary_outcome`` and on ``perf_z``; a saturating-curve
   fit ``perf = ceil - (ceil-base)*exp(-k*load)`` (+ hyperbolic Michaelis-Menten alt) with
   ``sat_ceiling``, ``sat_rate_k`` and ``sat_reps_to_peak`` in reps and session-position terms.
6. **(3) Reliability** of EVERY phenotype on the SAME ``R = tau2/(tau2 + mean_i(SE_i^2))``
   formula; the position/curve phenotypes use per-player **deterministic bootstrap** SEs
   (``phenotype_boot_reps``, seed ``phenotype_boot_seed``).
7. **Headline (D24).** The **governing** de-confounding verdict is the **strict** reading of
   D23 §1 (``warmup.deconf_governing_reading: strict_all_accel``): PASS only if the rule holds for
   **every** accel-outcome cell (L1/L4/L5 × O2/O4). Under it ``deconf_pass = False``, so the
   pre-registered gates do not both pass and the warm-up phenotype is **NOT claimed**; the finding
   is the **metric-robust fatigue null** (D20/D21) and the warm-up signal is reported as
   **exploratory only**. The primary-cell and robustness readings remain available for
   transparency (see the summary's verdict-sensitivity list). `deconf_pass` in the headline dict
   carries the governing (strict) value; `deconf_pass_primary` carries the primary-cell literal.

Outputs (``outputs/model/``): ``warmup_{cells,deconfounding,phenotypes,reliability,summary}``
(``.csv`` + ``.json``) + ``SUMMARY_3d.md`` + ``PROVENANCE_3d.txt``. Sample outputs carry
``UNVALIDATED SAMPLE OUTPUT``.

Run:
    python src/03d_warmup_phenotype.py --mode sample
    python src/03d_warmup_phenotype.py --mode sample --force
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    append_run_log,
    connect,
    get_logger,
    load_config,
    peak_ram_mb,
    provenance,
    resolve,
    seed_everything,
    timed_stage,
)

_HERE = Path(__file__).resolve().parent
CORE_PATH = _HERE / "03_combine_model.py"
PANEL_PATH = _HERE / "03b_metric_panel.py"

# Required per-cell record fields (mirrors 03b.PANEL_FIELDS; provenance first in the CSV).
CELL_FIELDS: list[str] = [
    "load_code", "load_metric", "outcome_code", "outcome_metric",
    "estimator", "optimizer", "load_coef", "load_se", "load_p", "perm_p",
    "tau2", "mean_se2", "reliability", "reliability_met", "stop_triggered",
    "skip_phase5_link", "n_players", "n_obs", "load2_included",
    "load_n_unique", "load_iqr", "perm_stat_observed",
]


def _load_module(name: str, path: Path):
    """Load a sibling script as a module (its filename is not importable as-is)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Pure helpers (importable by tests)
# --------------------------------------------------------------------------- #
def o4_thresholds(cfg: dict[str, Any]) -> dict[str, float]:
    """Yard-space thresholds for the O4 smoothed peak-accel outcome (D22 item 4 / D23 §2).

    Reuses the panel's SI->yard conversions but overrides the Savitzky-Golay window /
    polyorder and the accel clip with the ``warmup:`` declarations. No magic numbers.
    """
    p = cfg["panel"]
    w = cfg["warmup"]
    mpy = float(p["m_per_yd"])
    assert mpy > 0
    return {
        "m_per_yd": mpy,
        "g": float(p["g_mps2"]),
        "v_hs_yds": float(p["speed_hs_mps"]) / mpy,
        "a_th_yds2": float(p["accel_th_mps2"]) / mpy,
        "v_clip_yds": float(p["speed_clip_mps"]) / mpy,
        "a_clip_yds2": float(w["accel_clip_yds2"]),
        "mp_threshold_wkg": float(p["mp_threshold_wkg"]),
        "window": int(w["accel_smooth_window_frames"]),
        "polyorder": int(w["accel_smooth_polyorder"]),
    }


def standardize_within(d: pd.DataFrame, raw_col: str, out_col: str,
                       level_col: str, pos_col: str,
                       min_n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Z-score ``raw_col`` within ``(level_col, pos_col)`` over OBSERVED rows (ddof=0).

    Mirrors Phase-2 ``_standardize``: a group with ``n < min_n`` or zero sd -> NaN.
    Returns ``(frame_with_out_col, group_parameters_table)``.
    """
    z = np.full(len(d), np.nan, dtype=np.float64)
    rows: list[dict[str, Any]] = []
    obs = (d["is_imputed"] == 0).to_numpy()
    for (lvl, pos), g in d.loc[obs].groupby([level_col, pos_col], sort=True):
        v = g[raw_col].to_numpy(dtype=np.float64)
        vf = v[np.isfinite(v)]
        n = int(vf.size)
        mu = float(vf.mean()) if n else float("nan")
        sd = float(vf.std(ddof=0)) if n else float("nan")
        ok = bool(n >= int(min_n) and np.isfinite(sd) and sd > 0)
        if ok:
            z[g.index.to_numpy()] = (v - mu) / sd
        rows.append({"level": str(lvl), "position": str(pos), "n": n, "mean": mu,
                     "sd": sd, "ok": ok})
    d = d.copy()
    d[out_col] = z
    params = pd.DataFrame(rows, columns=["level", "position", "n", "mean", "sd", "ok"])
    return d, params


def compute_o4(frames: dict[str, np.ndarray], study_events: set[str],
               cfg: dict[str, Any], panel) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Per-observed-attempt raw smoothed peak acceleration (yd/s^2) for outcome O4.

    Savitzky-Golay smooth x/y (warmup window/polyorder), signed acceleration from the
    smoothed speed, clipped to ``accel_clip_yds2`` BEFORE taking the peak (D22 item 4).
    Delegates the frame-level kinematics to ``03b_metric_panel.attempt_metrics`` so the
    construction is identical to the panel's accel machinery (only the window differs).
    """
    thr = o4_thresholds(cfg)
    ec_coef = list(cfg["panel"]["ec_coef"])
    dist_gap_max_s = float(cfg["warmup"]["accel_dist_gap_max_s"])

    ev = frames["event_id"]
    ts, xs, ys = frames["t_s"], frames["x"], frames["y"]
    uniq, starts, counts = np.unique(ev, return_index=True, return_counts=True)
    order = np.argsort(starts)
    uniq, starts, counts = uniq[order], starts[order], counts[order]
    ends = starts + counts

    rows: list[dict[str, Any]] = []
    for g in range(uniq.size):
        eid = str(uniq[g])
        if eid not in study_events:
            continue
        a0, b0 = int(starts[g]), int(ends[g])
        m = panel.attempt_metrics(ts[a0:b0], xs[a0:b0], ys[a0:b0], thr, ec_coef,
                                  dist_gap_max_s)
        aa = m["a_yds2"]
        peak = float(np.nanmax(aa)) if (aa.size and np.isfinite(aa).any()) else float("nan")
        rows.append({"event_id": eid, "peak_accel_sg_yds2": peak})
    out = pd.DataFrame(rows, columns=["event_id", "peak_accel_sg_yds2"])
    diag = {"o4_n_attempts": int(len(out)),
            "o4_window": thr["window"], "o4_polyorder": thr["polyorder"],
            "o4_clip_yds2": thr["a_clip_yds2"],
            "o4_n_finite": int(np.isfinite(out["peak_accel_sg_yds2"].to_numpy()).sum())}
    return out, diag


def add_session_rank(d: pd.DataFrame) -> pd.DataFrame:
    """``session_rank`` = rank 1..K of OBSERVED attempts by real timestamps within session.

    Imputed rows (no timestamp) receive NaN. Ties broken by ``attempt`` then ``event_id``
    (deterministic ``mergesort``).
    """
    d = d.copy()
    obs = d["is_imputed"] == 0
    t = pd.to_datetime(d["attempt_start_time"], errors="coerce")
    d["_start"] = t
    key = d.loc[obs].sort_values(["nfl_id", "session_id", "_start", "attempt", "event_id"],
                                 kind="mergesort")
    rank = key.groupby(["nfl_id", "session_id"], sort=False).cumcount() + 1
    d["session_rank"] = np.nan
    d.loc[key.index, "session_rank"] = rank.to_numpy(dtype=np.float64)
    d = d.drop(columns=["_start"])
    return d


def add_within_drill_loads(d: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Within-drill accumulated prior effort (earlier reps of the SAME ``drill_type``).

    Computed per ``(session_id, drill_type)`` group in the frame's (validated) order, for
    the three declared load axes (yd, efforts, accel). Imputed reps contribute load
    (Phase-2 semantics) but carry no performance value.
    """
    d = d.copy()
    g = [d["session_id"], d["drill_type"]]
    eff_yd = d["effort_cost_yd"].fillna(0.0)
    d["within_drill_prior_load_yd"] = eff_yd.groupby(g, sort=False).cumsum() - eff_yd
    eff_ac = d["accel_yd"].fillna(0.0)
    d["within_drill_prior_load_accel"] = eff_ac.groupby(g, sort=False).cumsum() - eff_ac
    cnt = d.groupby(g, sort=False).cumcount()
    d["within_drill_prior_load_efforts"] = cnt.to_numpy(dtype=np.float64)
    # within-drill rep rank among OBSERVED reps (by real timestamp) — for the battery contrast
    obs = d["is_imputed"] == 0
    d["_start"] = pd.to_datetime(d["attempt_start_time"], errors="coerce")
    key = d.loc[obs].sort_values(["nfl_id", "session_id", "drill_type", "_start",
                                  "attempt", "event_id"], kind="mergesort")
    rr = key.groupby(["nfl_id", "session_id", "drill_type"], sort=False).cumcount() + 1
    d["within_drill_rep_rank"] = np.nan
    d.loc[key.index, "within_drill_rep_rank"] = rr.to_numpy(dtype=np.float64)
    d = d.drop(columns=["_start"])
    return d


def build_warmup_frame(study: pd.DataFrame, frames: dict[str, np.ndarray],
                       cfg: dict[str, Any], panel) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Rebuild the study frame (03b machinery) + O4 + session_rank + within-drill loads.

    Uses ``03b_metric_panel.compute_new_metrics`` for the accel load metric (L4) and
    ``03b_metric_panel.build_panel_frame`` for the deterministic order reproduction,
    load-only imputation and cumulative prior loads — including its own validation that
    the reproduced order matches the Phase-2 ``prior_load_yd``.
    """
    obs_events = set(study.loc[study["is_imputed"] == 0, "event_id"].astype(str))
    metrics, mdiag = panel.compute_new_metrics(frames, obs_events, cfg)
    o4, odiag = compute_o4(frames, obs_events, cfg, panel)
    metrics = metrics.merge(o4, on="event_id", how="left")

    d, val = panel.build_panel_frame(study, metrics, cfg)
    d, stdp = standardize_within(d, "peak_accel_sg_yds2", "peak_accel_sg_z",
                                 str(cfg["features"]["standardize_level"]),
                                 "combine_position", int(cfg["features"]["min_std_group_n"]))
    d = add_session_rank(d)
    d = add_within_drill_loads(d, cfg)

    obs = d["is_imputed"] == 0
    diag = {**val, **mdiag, **odiag}
    diag["o4_std_groups"] = int(len(stdp))
    diag["o4_std_groups_ok"] = int(stdp["ok"].sum()) if len(stdp) else 0
    diag["n_observed_missing_session_rank"] = int(d.loc[obs, "session_rank"].isna().sum())
    diag["n_observed_missing_o4"] = int(d.loc[obs, "peak_accel_sg_z"].isna().sum())
    diag["n_players"] = int(d["nfl_id"].nunique())
    diag["n_observed"] = int(obs.sum())
    diag["n_imputed"] = int((~obs).sum())
    return d, diag


# --------------------------------------------------------------------------- #
# Cells (delegates to 03b's runner so the fallback chain / reliability / stop
# rule / permutation are IDENTICAL to Phase 3b)
# --------------------------------------------------------------------------- #
def run_cells(d: pd.DataFrame, cfg: dict[str, Any], panel, core,
              known: dict[str, dict]) -> pd.DataFrame:
    """Run every (L, O) warm-up cell via ``03b_metric_panel.run_cell``.

    ``known`` memoises cells already computed (avoids re-running the fallback chain when
    the same cell is needed by the deconfounding step).
    """
    rows: list[dict[str, Any]] = []
    for lcode, load_col in cfg["warmup"]["load_metrics"].items():
        for ocode, outcome_col in cfg["warmup"]["outcome_metrics"].items():
            key = f"{lcode}|{ocode}"
            if key not in known:
                known[key] = panel.run_cell(d, cfg, core, load_col, outcome_col)
            rec = dict(known[key])
            rec["load_code"] = lcode
            rec["outcome_code"] = ocode
            rows.append(rec)
    # re-sort deterministically and select the declared fields
    panel_df = pd.DataFrame(rows)
    cols = ["load_code", "load_metric", "outcome_code", "outcome_metric"] + \
        [c for c in CELL_FIELDS if c not in ("load_code", "load_metric", "outcome_code", "outcome_metric")]
    return panel_df[cols]


# --------------------------------------------------------------------------- #
# (1) De-confounding
# --------------------------------------------------------------------------- #
def adjusted_ols(d: pd.DataFrame, load_col: str, outcome_col: str,
                 include_load2: bool) -> dict[str, Any]:
    """Drill + session_rank-ADJUSTED population coefficient (deterministic pooled OLS).

    Model = the Phase-3 population fixed part (``load``, optionally ``load^2``,
    ``first_attempt``) PLUS ``C(drill_type)`` fixed effects and an explicit ``session_rank``
    term (D23 §1(S)). Load is centred. ``include_load2`` toggles the curvature term
    (reported both ways as a pre-declared robustness, since it materially changes the
    retained fraction). Returns coefficients/SE/p on the linear load term.
    """
    dfm = d[(d["is_imputed"] == 0) & d[outcome_col].notna() &
            d[load_col].notna() & d["session_rank"].notna()].copy()
    nan = float("nan")
    if len(dfm) < 10 or dfm["drill_type"].nunique() < 2:
        return {"coef": nan, "se": nan, "p": nan, "n_obs": int(len(dfm))}
    dfm["load_c"] = dfm[load_col].to_numpy(float) - float(dfm[load_col].mean())
    dfm["load2_c"] = dfm["load_c"] ** 2
    dfm["drill"] = dfm["drill_type"].astype(str)
    dfm["first_attempt"] = dfm["first_attempt"].fillna(0).astype(int)
    terms = "load_c + " + (("load2_c + ") if include_load2 else "") + \
        "first_attempt + C(drill) + session_rank"
    fit = smf.ols(f"{outcome_col} ~ {terms}", data=dfm).fit()
    return {"coef": float(fit.params["load_c"]), "se": float(fit.bse["load_c"]),
            "p": float(fit.pvalues["load_c"]), "n_obs": int(len(dfm))}


def within_drill_eb_slope(d: pd.DataFrame, cfg: dict[str, Any], load_col: str,
                          outcome_col: str, core) -> dict[str, Any]:
    """EB analogue of the drill adjustment: within-drill centred load -> EB meta-slope.

    Within-drill centring removes drill identity by construction, so the resulting
    population (inverse-variance-weighted) per-player slope is the warm-up term net of
    drill. Deterministic (per-player OLS + EB), matching D20 §8's machinery.
    """
    nan = float("nan")
    wd_col = "within_drill_" + load_col if load_col != "prior_load_yd" else "within_drill_prior_load_yd"
    if wd_col not in d.columns:
        wd_col = "within_drill_prior_load_" + load_col.replace("prior_load_", "")
    dfm = d[(d["is_imputed"] == 0) & d[outcome_col].notna() & d[wd_col].notna()].copy()
    if len(dfm) < 10:
        return {"coef": nan, "se": nan, "p": nan, "reliability": nan,
                "n_players": 0, "n_obs": int(len(dfm)), "load_col": wd_col}
    dfm["load_c"] = dfm[wd_col].to_numpy(float)
    slopes = core.per_player_slopes(dfm, "load_c", int(cfg["model"]["eb_min_attempts"]),
                                    outcome_col=outcome_col)
    mu, mu_se, k = core.inv_var_mean(slopes)
    tau2 = core.dl_tau2(slopes)
    mean_se2 = float(np.mean(slopes["se"].to_numpy() ** 2)) if len(slopes) else nan
    rel = tau2 / (tau2 + mean_se2) if (np.isfinite(mean_se2) and (tau2 + mean_se2) > 0) else 0.0
    if k < int(cfg["model"]["reliability_min_players"]):
        rel = 0.0
    p = float(2.0 * stats.norm.sf(abs(mu / mu_se))) if (np.isfinite(mu) and np.isfinite(mu_se) and mu_se > 0) else nan
    return {"coef": float(mu), "se": float(mu_se), "p": p, "reliability": float(rel),
            "n_players": int(k), "n_obs": int(len(dfm)), "load_col": wd_col}


def within_battery_contrast(d: pd.DataFrame, cfg: dict[str, Any], outcome_col: str,
                            battery_only: bool) -> dict[str, Any]:
    """Paired within-(player, drill_type) rep-1 -> rep-2 contrast on the outcome (D23 §1).

    ``battery_only`` restricts to the declared timed battery (40/3-cone/shuttle). Positive
    = the later rep has higher explosive output = warm-up.
    """
    nan = float("nan")
    sub = d[(d["is_imputed"] == 0) & d[outcome_col].notna() &
            d["within_drill_rep_rank"].notna()].copy()
    if battery_only:
        sub = sub[sub["drill_type"].isin(list(cfg["audit"]["families"]["timed_battery"]))]
    piv = sub.pivot_table(index=["nfl_id", "session_id", "drill_type"],
                          columns="within_drill_rep_rank", values=outcome_col, aggfunc="first")
    if 1 in piv.columns and 2 in piv.columns:
        diff = (piv[2] - piv[1]).dropna()
        if len(diff):
            return {"contrast": float(diff.mean()), "n": int(len(diff)),
                    "sd": float(diff.std(ddof=1)) if len(diff) > 1 else nan}
    return {"contrast": nan, "n": 0, "sd": nan}


def deconfounding(d: pd.DataFrame, cfg: dict[str, Any], cells: pd.DataFrame,
                  core) -> pd.DataFrame:
    """Build the de-confounding table for the accel-outcome cells (O2, O4)."""
    W = cfg["warmup"]
    accel_outcomes = {c: m for c, m in W["outcome_metrics"].items() if c == "O2" or "accel" in str(m)}
    retain = float(W["deconf_retain_frac"])
    rows: list[dict[str, Any]] = []
    for lcode, load_col in W["load_metrics"].items():
        for ocode, outcome_col in accel_outcomes.items():
            cell = cells[(cells["load_code"] == lcode) & (cells["outcome_code"] == ocode)]
            raw_coef = float(cell["load_coef"].iloc[0]) if len(cell) else float("nan")
            raw_se = float(cell["load_se"].iloc[0]) if len(cell) else float("nan")
            raw_p = float(cell["load_p"].iloc[0]) if len(cell) else float("nan")
            raw_est = str(cell["estimator"].iloc[0]) if len(cell) else ""
            raw_rel = float(cell["reliability"].iloc[0]) if len(cell) else float("nan")
            a2 = adjusted_ols(d, load_col, outcome_col, include_load2=True)
            a0 = adjusted_ols(d, load_col, outcome_col, include_load2=False)
            ebw = within_drill_eb_slope(d, cfg, load_col, outcome_col, core)
            bat = within_battery_contrast(d, cfg, outcome_col, battery_only=True)
            allbat = within_battery_contrast(d, cfg, outcome_col, battery_only=False)

            def _retain(x: float) -> float:
                return abs(x) / abs(raw_coef) if (np.isfinite(x) and np.isfinite(raw_coef)
                                                   and raw_coef != 0) else float("nan")

            pass_pos = bool(np.isfinite(a2["coef"]) and a2["coef"] > 0)
            pass_ret = bool(np.isfinite(_retain(a2["coef"])) and _retain(a2["coef"]) >= retain)
            pass_within = bool(np.isfinite(bat["contrast"]) and bat["contrast"] > 0)
            rows.append({
                "load_code": lcode, "load_metric": load_col,
                "outcome_code": ocode, "outcome_metric": outcome_col,
                "is_primary": bool(load_col == W["primary_load"] and outcome_col == W["primary_outcome"]),
                "raw_coef": raw_coef, "raw_se": raw_se, "raw_p": raw_p,
                "raw_estimator": raw_est, "raw_reliability": raw_rel,
                "adj_coef": a2["coef"], "adj_se": a2["se"], "adj_p": a2["p"],
                "adj_retain_frac": _retain(a2["coef"]),
                "adj_coef_no_load2": a0["coef"], "adj_se_no_load2": a0["se"],
                "adj_p_no_load2": a0["p"], "adj_retain_frac_no_load2": _retain(a0["coef"]),
                "adj_eb_within_coef": ebw["coef"], "adj_eb_within_se": ebw["se"],
                "adj_eb_within_p": ebw["p"],
                "adj_eb_within_retain_frac": _retain(ebw["coef"]),
                "adj_eb_within_reliability": ebw["reliability"],
                "within_contrast_battery": bat["contrast"], "within_contrast_battery_n": bat["n"],
                "within_contrast_battery_sd": bat["sd"],
                "within_contrast_all": allbat["contrast"], "within_contrast_all_n": allbat["n"],
                "pass_positive": pass_pos, "pass_retain": pass_ret,
                "pass_within": pass_within,
                "deconf_pass": bool(pass_pos and pass_ret and pass_within),
            })
    df = pd.DataFrame(rows)
    return df.sort_values(["load_code", "outcome_code"], kind="mergesort").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# (2) Per-player phenotype
# --------------------------------------------------------------------------- #
def player_warmup_slopes(d: pd.DataFrame, cfg: dict[str, Any], core, load_col: str,
                         outcome_col: str) -> pd.DataFrame:
    """Shrunken per-player warm-up slope for one (load, outcome) cell (D20 §8 machinery)."""
    W = cfg["warmup"]
    dfm = d[(d["is_imputed"] == 0) & d[outcome_col].notna() & d[load_col].notna()].copy()
    if len(dfm) == 0:
        return pd.DataFrame(columns=["nfl_id", "n_attempts", "slope_raw", "warmup_slope",
                                     "warmup_slope_se", "ci_lo", "ci_hi", "shrinkage_weight"])
    dfm["first_attempt"] = dfm["first_attempt"].fillna(0).astype(int)
    dd = core.build_design(dfm, cfg, include_load2=True, load_col=load_col)
    slopes = core.per_player_slopes(dd, "load_c", int(W["phenotype_min_attempts"]),
                                    outcome_col=outcome_col)
    if len(slopes) == 0:
        return pd.DataFrame(columns=["nfl_id", "n_attempts", "slope_raw", "warmup_slope",
                                     "warmup_slope_se", "ci_lo", "ci_hi", "shrinkage_weight"])
    tau2 = core.dl_tau2(slopes)
    mu, _, _ = core.inv_var_mean(slopes)
    sh = core.eb_shrink(slopes, tau2, mu)
    level = float(cfg["model"]["player_slope_ci_level"])
    zc = float(stats.norm.ppf(1.0 - (1.0 - level) / 2.0))
    out = pd.DataFrame({
        "nfl_id": sh["nfl_id"].astype(int),
        "n_attempts": sh["n"].astype(int),
        "slope_raw": sh["slope"].astype(float),
        "warmup_slope": sh["slope_shrunk"].astype(float),
        "warmup_slope_se": sh["se"].astype(float),
        "shrinkage_weight": sh["shrinkage_weight"].astype(float),
    })
    out["ci_lo"] = out["warmup_slope"] - zc * out["warmup_slope_se"]
    out["ci_hi"] = out["warmup_slope"] + zc * out["warmup_slope_se"]
    return out.sort_values("nfl_id", kind="mergesort").reset_index(drop=True)


def session_position_of_peak(d: pd.DataFrame, cfg: dict[str, Any], outcome_col: str,
                             boot_reps: int, boot_seed: int,
                             min_attempts: int) -> pd.DataFrame:
    """Per-player session-position (rank 1..K) of the player's maximum outcome + boot SE."""
    sub = d[(d["is_imputed"] == 0) & d[outcome_col].notna() & d["session_rank"].notna()]
    rows: list[dict[str, Any]] = []
    for pid, g in sub.groupby("nfl_id", sort=True):
        r = g["session_rank"].to_numpy(dtype=np.float64)
        y = g[outcome_col].to_numpy(dtype=np.float64)
        n = int(y.size)
        if n < int(min_attempts):
            continue
        pos = float(r[int(np.argmax(y))])
        rng = np.random.default_rng(int(boot_seed) + int(pid))
        bs = np.empty(int(boot_reps), dtype=np.float64)
        for b in range(int(boot_reps)):
            idx = rng.integers(0, n, n)
            bs[b] = float(r[idx][int(np.argmax(y[idx]))])
        rows.append({"nfl_id": int(pid), "outcome_metric": outcome_col, "n_attempts": n,
                     "session_position_of_peak": pos,
                     "pos_se": float(np.std(bs, ddof=1)),
                     "pos_boot_mean": float(np.mean(bs))})
    return pd.DataFrame(rows, columns=["nfl_id", "outcome_metric", "n_attempts",
                                       "session_position_of_peak", "pos_se", "pos_boot_mean"])


# --------------------------------------------------------------------------- #
# Saturating-curve fit (profiled / vectorised; deterministic)
# --------------------------------------------------------------------------- #
def _profile_two_param(U: np.ndarray, V: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised linear LS of ``y ~ c*U + b*V`` for each grid column; returns (c, b, sse)."""
    a11 = (U * U).sum(0)
    a12 = (U * V).sum(0)
    a22 = (V * V).sum(0)
    r1 = (U * y[:, None]).sum(0)
    r2 = (V * y[:, None]).sum(0)
    det = a11 * a22 - a12 * a12
    ok = np.abs(det) > 1e-12
    c = np.full(U.shape[1], np.nan)
    b = np.full(U.shape[1], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        c[ok] = (a22[ok] * r1[ok] - a12[ok] * r2[ok]) / det[ok]
        b[ok] = (a11[ok] * r2[ok] - a12[ok] * r1[ok]) / det[ok]
    sse = (y @ y) - (c * r1 + b * r2)
    sse[~ok] = np.inf
    return c, b, sse


def sat_reach_load(reach: float, curve: str, param: float) -> float:
    """Load at which the fitted rise reaches ``reach`` of ``(ceil-base)`` (D23 §2)."""
    if not np.isfinite(param) or param <= 0:
        return float("nan")
    if curve == "exponential":
        return float(math.log(1.0 / (1.0 - reach)) / param)      # k
    if curve == "hyperbolic":
        return float(reach * param / (1.0 - reach))              # k_m
    raise ValueError(f"unknown curve {curve!r}")


def fit_saturation(load: np.ndarray, y: np.ndarray, reach: float, curve: str,
                   rtp_grid: np.ndarray) -> dict[str, float]:
    """Fit a saturating curve and return ceiling, rate and reps/session-position to peak.

    Exponential: ``perf = ceil - (ceil-base)*exp(-k*load)``.
    Hyperbolic (Michaelis-Menten): ``perf = base + (ceil-base)*load/(k_m+load)``.
    The profile grid is expressed directly in candidate values of ``reps_to_peak``
    (the interpretable quantity), from which ``k`` / ``k_m`` follow; no magic numbers.
    """
    L = np.asarray(load, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    nan = float("nan")
    if L.size < 3 or np.var(y) == 0 or not np.isfinite(L).all():
        return {"ceiling": nan, "rate": nan, "reps_to_peak": nan}
    rtp = np.asarray(rtp_grid, dtype=np.float64)
    if curve == "exponential":
        k = math.log(1.0 / (1.0 - reach)) / rtp               # (n_grid,)
        U = 1.0 - np.exp(-np.outer(L, k))                     # (n, grid)
        V = 1.0 - U
    elif curve == "hyperbolic":
        km = rtp * (1.0 - reach) / reach
        U = L[:, None] / (km[None, :] + L[:, None])
        V = 1.0 - U
    else:
        raise ValueError(f"unknown curve {curve!r}")
    c, _b, sse = _profile_two_param(U, V, y)
    sse = np.where(np.isfinite(sse), sse, np.inf)
    if not np.isfinite(sse).any() or np.isneginf(sse.max()):
        return {"ceiling": nan, "rate": nan, "reps_to_peak": nan}
    j = int(np.nanargmin(sse))
    r = float(rtp[j])
    rate = (math.log(1.0 / (1.0 - reach)) / r) if curve == "exponential" else (r * (1.0 - reach) / reach)
    return {"ceiling": float(c[j]), "rate": float(rate), "reps_to_peak": float(r)}


def player_saturation(d: pd.DataFrame, cfg: dict[str, Any], load_col: str, outcome_col: str,
                      boot_reps: int, boot_seed: int) -> pd.DataFrame:
    """Per-player saturating-curve phenotype (exponential + hyperbolic) with bootstrap SEs."""
    W = cfg["warmup"]
    reach = float(W["sat_reach_frac"])
    min_att = int(W["sat_min_attempts"])
    min_spread = float(W["sat_min_load_sd_frac"])
    rtp_grid = np.geomspace(float(W["sat_fit"]["rtp_grid_min"]),
                            float(W["sat_fit"]["rtp_grid_max"]),
                            int(W["sat_fit"]["rtp_grid_n"]))
    sub = d[(d["is_imputed"] == 0) & d[load_col].notna() & d[outcome_col].notna()]
    rows: list[dict[str, Any]] = []
    for pid, g in sub.groupby("nfl_id", sort=True):
        L = g[load_col].to_numpy(dtype=np.float64)
        y = g[outcome_col].to_numpy(dtype=np.float64)
        n = int(L.size)
        rec = {"nfl_id": int(pid), "load_metric": load_col, "outcome_metric": outcome_col,
               "n_attempts": n, "load_spread": float("nan"),
               "sat_ceiling": float("nan"), "sat_rate_k": float("nan"),
               "sat_reps_to_peak": float("nan"), "sat_reps_to_peak_se": float("nan"),
               "sat_ceiling_se": float("nan"), "sat_rate_k_se": float("nan"),
               "sat_ceiling_hyp": float("nan"), "sat_rate_k_hyp": float("nan"),
               "sat_reps_to_peak_hyp": float("nan"), "sat_reps_to_peak_hyp_se": float("nan")}
        mu = float(L.mean())
        rec["load_spread"] = float(np.std(L) / abs(mu)) if mu != 0 else float("nan")
        if n < min_att or not (np.isfinite(rec["load_spread"]) and rec["load_spread"] >= min_spread):
            rows.append(rec)
            continue
        fe = fit_saturation(L, y, reach, "exponential", rtp_grid)
        fh = fit_saturation(L, y, reach, "hyperbolic", rtp_grid)
        rec.update({"sat_ceiling": fe["ceiling"], "sat_rate_k": fe["rate"],
                    "sat_reps_to_peak": fe["reps_to_peak"],
                    "sat_ceiling_hyp": fh["ceiling"], "sat_rate_k_hyp": fh["rate"],
                    "sat_reps_to_peak_hyp": fh["reps_to_peak"]})
        rng = np.random.default_rng(int(boot_seed) + int(pid))
        bce, bke, bre = [], [], []
        bh = []
        for b in range(int(boot_reps)):
            idx = rng.integers(0, n, n)
            be = fit_saturation(L[idx], y[idx], reach, "exponential", rtp_grid)
            bce.append(be["ceiling"]); bke.append(be["rate"]); bre.append(be["reps_to_peak"])
            bh.append(fit_saturation(L[idx], y[idx], reach, "hyperbolic", rtp_grid)["reps_to_peak"])
        rec["sat_ceiling_se"] = float(np.nanstd(bce, ddof=1))
        rec["sat_rate_k_se"] = float(np.nanstd(bke, ddof=1))
        rec["sat_reps_to_peak_se"] = float(np.nanstd(bre, ddof=1))
        rec["sat_reps_to_peak_hyp_se"] = float(np.nanstd(bh, ddof=1))
        rows.append(rec)
    cols = list(rows[0].keys()) if rows else ["nfl_id"]
    return pd.DataFrame(rows, columns=cols).sort_values("nfl_id", kind="mergesort").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# (3) Reliability of every phenotype
# --------------------------------------------------------------------------- #
def reliability_from_est_se(est: np.ndarray, se: np.ndarray, min_players: int) -> dict[str, float]:
    """``R = tau2/(tau2 + mean_i(SE_i^2))`` with ``tau2 = max(0, Var(est) - mean SE^2)`` (DL)."""
    e = np.asarray(est, dtype=np.float64)
    s = np.asarray(se, dtype=np.float64)
    ok = np.isfinite(e) & np.isfinite(s)
    k = int(ok.sum())
    if k < 2:
        return {"tau2": 0.0, "mean_se2": float(np.mean(s[ok] ** 2)) if k else float("nan"),
                "reliability": 0.0, "n_players": k}
    mean_se2 = float(np.mean(s[ok] ** 2))
    tau2 = max(0.0, float(np.var(e[ok], ddof=1)) - mean_se2)
    rel = tau2 / (tau2 + mean_se2) if (tau2 + mean_se2) > 0 else 0.0
    if k < int(min_players):
        rel = 0.0
    return {"tau2": tau2, "mean_se2": mean_se2, "reliability": float(rel), "n_players": k}


def build_reliability(d: pd.DataFrame, cfg: dict[str, Any], panel_cells: pd.DataFrame,
                      slopes_by_cell: dict[str, pd.DataFrame],
                      pos_tables: dict[str, pd.DataFrame],
                      sat_tables: dict[str, pd.DataFrame], core) -> pd.DataFrame:
    """Reliability table for every phenotype (warm-up slope; position-of-peak; saturating)."""
    W = cfg["warmup"]
    thr = float(W["reliability_stop_threshold"])
    minpl = int(W["reliability_min_players"])
    rows: list[dict[str, Any]] = []

    def add(phenotype: str, detail: str, rec: dict[str, Any], source: str):
        rows.append({
            "phenotype": phenotype, "detail": detail, "source": source,
            "n_players": int(rec.get("n_players", 0)),
            "tau2": rec.get("tau2", float("nan")),
            "mean_se2": rec.get("mean_se2", float("nan")),
            "reliability": rec.get("reliability", float("nan")),
            "reliability_met": bool(np.isfinite(rec.get("reliability", float("nan"))) and
                                    rec.get("reliability", float("nan")) >= thr),
            "min_players_met": bool(int(rec.get("n_players", 0)) >= minpl),
            "reliable": bool(np.isfinite(rec.get("reliability", float("nan"))) and
                             rec.get("reliability", float("nan")) >= thr and
                             int(rec.get("n_players", 0)) >= minpl),
        })

    # warm-up slope: per cell, selected-estimator reliability (D23: tau2 from the selected estimator)
    for _, cell in panel_cells.iterrows():
        detail = f"{cell['load_code']}({cell['load_metric']}) x {cell['outcome_code']}({cell['outcome_metric']})"
        add("warmup_slope", detail,
            {"tau2": cell["tau2"], "mean_se2": cell["mean_se2"],
             "reliability": cell["reliability"], "n_players": cell["n_players"]},
            f"selected:{cell['estimator']}")
        # EB-path analogue (deterministic) for the same cell
        key = f"{cell['load_code']}|{cell['outcome_code']}"
        sl = slopes_by_cell.get(key)
        if sl is not None and len(sl):
            rel = reliability_from_est_se(sl["warmup_slope"].to_numpy(),
                                          sl["warmup_slope_se"].to_numpy(), minpl)
            add("warmup_slope_eb", detail, rel, "per_player_eb")
    # session position of peak
    for ocol, tbl in pos_tables.items():
        rec = reliability_from_est_se(tbl["session_position_of_peak"].to_numpy(),
                                      tbl["pos_se"].to_numpy(), minpl)
        add("session_position_of_peak", ocol, rec, "bootstrap")
    # saturating-curve parameters (per load variant)
    for load_col, tbl in sat_tables.items():
        for est_col, se_col, name in [
            ("sat_reps_to_peak", "sat_reps_to_peak_se", "sat_reps_to_peak"),
            ("sat_ceiling", "sat_ceiling_se", "sat_ceiling"),
            ("sat_rate_k", "sat_rate_k_se", "sat_rate_k"),
            ("sat_reps_to_peak_hyp", "sat_reps_to_peak_hyp_se", "sat_reps_to_peak_hyperbolic"),
        ]:
            rec = reliability_from_est_se(tbl[est_col].to_numpy(), tbl[se_col].to_numpy(), minpl)
            row = {"phenotype": name, "detail": load_col, "source": "bootstrap",
                   "n_players": rec["n_players"], "tau2": rec["tau2"], "mean_se2": rec["mean_se2"],
                   "reliability": rec["reliability"],
                   "reliability_met": bool(rec["reliability"] >= thr),
                   "min_players_met": bool(rec["n_players"] >= minpl),
                   "reliable": bool(rec["reliability"] >= thr and rec["n_players"] >= minpl)}
            rows.append(row)
    cols = ["phenotype", "detail", "source", "n_players", "tau2", "mean_se2",
            "reliability", "reliability_met", "min_players_met", "reliable"]
    return pd.DataFrame(rows, columns=cols)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _headline(cfg: dict[str, Any], deconf: pd.DataFrame, reliab: pd.DataFrame,
              sat_tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Headline decision: claim the reps-to-peak median only if pre-registered gates pass.

    The pre-registered rule (D23 §7) gates the claim on ``deconf_pass`` AND the relevant
    phenotype reliability >= 0.2. Per the D24 adjudication the **governing** de-confounding
    verdict is the **strict** reading of D23 §1 (``warmup.deconf_governing_reading:``
    ``strict_all_accel``): the rule must hold for **every** accel-outcome cell, not only the
    declared primary cell. The primary-cell literal and the other robustness variants are
    reported alongside (``deconf_pass_primary``/``deconf_pass_no_load2``/
    ``deconf_pass_eb_within``/``deconf_pass_all_accel``); the saturating-curve grid censoring
    is also reported. See ``notes/decisions.md`` D24 and ``notes/blockers.md``.
    """
    W = cfg["warmup"]
    primary = deconf[deconf["is_primary"]]
    # governing reading of D23 §1 (D24 adjudication): the STRICT all-accel-cells reading
    reading = str(W.get("deconf_governing_reading", ""))
    assert reading == "strict_all_accel", \
        f"unsupported warmup.deconf_governing_reading: {reading!r}"
    deconf_pass_primary = bool(primary["deconf_pass"].iloc[0]) if len(primary) else False
    # robustness variants (explicitly pre-declared in the summary, not used to select the claim)
    pr = primary.iloc[0] if len(primary) else None
    deconf_pass_no_load2 = bool(pr["pass_positive"] and
                                np.isfinite(pr["adj_retain_frac_no_load2"]) and
                                pr["adj_retain_frac_no_load2"] >= float(W["deconf_retain_frac"]) and
                                bool(pr["pass_within"])) if pr is not None else False
    deconf_pass_eb_within = bool(pr["pass_positive"] and
                                 np.isfinite(pr["adj_eb_within_retain_frac"]) and
                                 pr["adj_eb_within_retain_frac"] >= float(W["deconf_retain_frac"]) and
                                 bool(pr["pass_within"])) if pr is not None else False
    deconf_pass_all_accel = bool(len(deconf) and deconf["deconf_pass"].all())
    # GOVERNING verdict (D24): the strict all-accel-cells reading
    deconf_pass = deconf_pass_all_accel
    # chosen headline phenotype reliability: sat_reps_to_peak on the primary load
    prim_load = str(W["primary_load"])
    rt = reliab[(reliab["phenotype"] == "sat_reps_to_peak") & (reliab["detail"] == prim_load)]
    rel_ok = bool(rt["reliable"].iloc[0]) if len(rt) else False
    rel_val = float(rt["reliability"].iloc[0]) if len(rt) else float("nan")
    sat = sat_tables.get(prim_load)
    median = iqr_lo = iqr_hi = median_id = float("nan")
    n_fit = n_censored = 0
    rtp_max = float(W["sat_fit"]["rtp_grid_max"])
    if sat is not None and len(sat):
        vals = sat["sat_reps_to_peak"].to_numpy(dtype=np.float64)
        vals = vals[np.isfinite(vals)]
        n_fit = int(vals.size)
        if n_fit:
            median = float(np.median(vals))
            iqr_lo = float(np.percentile(vals, 25))
            iqr_hi = float(np.percentile(vals, 75))
            identified = vals[vals < rtp_max - 1e-9]
            n_censored = int(n_fit - identified.size)
            if identified.size:
                median_id = float(np.median(identified))
    claim = bool(deconf_pass and rel_ok)
    return {
        "deconf_pass": deconf_pass,
        "deconf_pass_governing_reading": reading,
        "deconf_pass_primary": deconf_pass_primary,
        "deconf_pass_no_load2": deconf_pass_no_load2,
        "deconf_pass_eb_within": deconf_pass_eb_within,
        "deconf_pass_all_accel": deconf_pass_all_accel,
        "headline_phenotype": "sat_reps_to_peak",
        "headline_load": prim_load,
        "headline_reliability": rel_val,
        "headline_reliability_met": rel_ok,
        "n_players_fit": n_fit,
        "n_players_censored_at_grid_max": n_censored,
        "frac_censored_at_grid_max": (float(n_censored) / n_fit) if n_fit else float("nan"),
        "sat_rtp_grid_max": rtp_max,
        "median_reps_to_peak": median,
        "median_reps_to_peak_identified": median_id,
        "iqr_lo": iqr_lo, "iqr_hi": iqr_hi,
        "claim": claim,
        "claim_robust": bool(claim and deconf_pass_no_load2),
    }


def _summary_md(summary: dict[str, Any], deconf: pd.DataFrame, cells: pd.DataFrame,
                reliab: pd.DataFrame) -> str:
    W = summary["warmup_params"]
    hl = summary["headline"]
    lines = [
        "# Phase 3d — warm-up / potentiation phenotype (pre-registered D23)",
        "",
        f"- provenance: **{summary['provenance']}**",
        f"- cells: **{summary['n_cells']}** "
        f"({len(W['load_metrics'])} loads × {len(W['outcome_metrics'])} outcomes); "
        f"primary cell = `{W['primary_load']} × {W['primary_outcome']}`",
        f"- reliability gate **≥{W['reliability_stop_threshold']}**; "
        f"min players **{W['reliability_min_players']}**",
        "",
        "## (1) De-confounding — the make-or-break (D23 §1)",
        "",
        f"**Governing verdict (D24 — strict `{hl['deconf_pass_governing_reading']}` reading): "
        f"`deconf_pass = {hl['deconf_pass']}`** (PASS only if the rule holds for **every** "
        "accel-outcome cell).",
        "",
        "| load | outcome | raw | adj (+drill, +session_rank) | retain | adj no-load² | EB within-drill | within rep1→rep2 (battery) | pass |",
        "|---|---|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    for _, r in deconf.iterrows():
        star = " **(primary)**" if r["is_primary"] else ""
        lines.append(
            f"| {r['load_metric']}{star} | {r['outcome_metric']} | {r['raw_coef']:.5g} | "
            f"{r['adj_coef']:.5g} | {r['adj_retain_frac']:.2f} | {r['adj_coef_no_load2']:.5g} "
            f"({r['adj_retain_frac_no_load2']:.2f}) | {r['adj_eb_within_coef']:.5g} "
            f"({r['adj_eb_within_retain_frac']:.2f}) | {r['within_contrast_battery']:.4g} "
            f"(n={int(r['within_contrast_battery_n'])}) | {bool(r['deconf_pass'])} |")
    lines += [
        "",
        "*Rule (D23 §1, fixed): PASS iff the drill+session_rank-adjusted coefficient is "
        "**positive** AND `|adjusted| ≥ 0.5·|raw|` AND the within-drill rep-1→rep-2 "
        "battery contrast is **positive**.* Raw/adj/within coefficients are reported even "
        "when the rule fails. The `adj no-load²` column is a pre-declared robustness "
        "(the load² term materially changes the retained fraction — see `notes/blockers.md`). "
        f"The declared *primary* cell reads `deconf_pass = {hl['deconf_pass_primary']}` (literal "
        "primary-cell reading); the **governing** value above is the strict all-accel-cells "
        "reading (see the verdict-sensitivity list in §3).",
        "",
        "## (2) Reliability of every phenotype (D23 §3)",
        "",
        "| phenotype | detail | source | n | reliability | ≥0.2 | reliable |",
        "|---|---|---|---:|---:|:--:|:--:|",
    ]
    for _, r in reliab.iterrows():
        lines.append(f"| {r['phenotype']} | {r['detail']} | {r['source']} | {r['n_players']} | "
                     f"{r['reliability']:.4f} | {bool(r['reliability_met'])} | {bool(r['reliable'])} |")
    _bat = deconf["within_contrast_battery"].to_numpy(dtype=np.float64)
    _bat = _bat[np.isfinite(_bat)]
    bat_lo = float(_bat.min()) if _bat.size else float("nan")
    bat_hi = float(_bat.max()) if _bat.size else float("nan")
    lines += [
        "",
        "## (3) Headline — the finding is the metric-robust fatigue null; warm-up is exploratory",
        "",
        "**FINDING (D20/D21): the metric-robust fatigue null.** Phase 3 found no reliable "
        "between-player fatigue-slope variation (the Phase-5 reliability gate is not met), and "
        "the D21 metric panel shows this null is **metric-robust for the fatigue direction** "
        "across all declared load and outcome metrics.",
        "",
        "**Warm-up / potentiation = EXPLORATORY only (NOT claimed; D24).** Under the governing "
        f"strict D23 §1 reading (`deconf_pass = {hl['deconf_pass']}`, all accel-outcome cells) "
        "the pre-registered gates do **not** both pass, so the warm-up phenotype is **not "
        "claimed**. The single surviving signal is the consistently **positive within-drill "
        f"rep-1→rep-2 battery contrast** (≈ {bat_lo:+.2f}…{bat_hi:+.2f} z on the accel "
        "outcomes). The `prior_load_accel` accel cells have a **NEGATIVE** drill+session-adjusted "
        "coefficient, so that D21 gate cell does not survive the adjustment.",
        "",
        f"**Exploratory phenotype number — `{hl['headline_phenotype']}` on "
        f"{hl['headline_load']} (censoring-flagged).** Identified-only median = "
        f"**{hl['median_reps_to_peak_identified']:.3g} reps** "
        f"(all-player IQR {hl['iqr_lo']:.3g}–{hl['iqr_hi']:.3g}, cap-inflated). "
        f"**{hl['n_players_censored_at_grid_max']}/{hl['n_players_fit']} "
        f"({100.0 * hl['frac_censored_at_grid_max']:.1f}%)** players are pinned at the grid cap "
        f"{hl['sat_rtp_grid_max']:.0f} (no identifiable peak), so the all-player median "
        f"({hl['median_reps_to_peak']:.3g}) is **cap-driven and must not be read as the "
        f"phenotype**. Phenotype reliability = {hl['headline_reliability']:.4f} — "
        "**exploratory/borderline**.",
        "",
        "### Verdict sensitivity (READ THIS)",
        "",
        f"- **governing** (strict, all accel-outcome cells; D24): **{hl['deconf_pass']}**",
        f"- primary cell (declared adjustment incl. load²): **{hl['deconf_pass_primary']}**",
        f"- no-load² robustness: **{hl['deconf_pass_no_load2']}**",
        f"- EB within-drill centring analogue: **{hl['deconf_pass_eb_within']}**",
        "",
        "The **governing** verdict is the strict reading (D24): the rule must hold for *every* "
        "accel-outcome cell. It fails (`prior_load_accel` × accel cells have a negative adjusted "
        "coefficient), so the warm-up phenotype is not claimed and the finding is the metric-robust "
        "fatigue null. The other readings are listed for transparency only. The saturating-curve "
        "phenotype is **boundary-censored** (above), so its median must be read with that censoring "
        "in mind. Recorded in `notes/decisions.md` D24 and `notes/blockers.md` B6/B7.",
    ]
    lines += [
        "",
        "## Cells",
        "",
        "| load | outcome | estimator | load_coef | load_p | perm_p | reliability | met | stop |",
        "|---|---|---|---:|---:|---:|---:|:--:|:--:|",
    ]
    for _, r in cells.iterrows():
        lines.append(f"| {r['load_metric']} | {r['outcome_metric']} | {r['estimator']} | "
                     f"{r['load_coef']:.5g} | {r['load_p']:.4g} | {r['perm_p']:.4g} | "
                     f"{r['reliability']:.4f} | {bool(r['reliability_met'])} | "
                     f"{bool(r['stop_triggered'])} |")
    lines += ["", "*(All CSVs carry a `provenance` first column. Sample outputs are UNVALIDATED SAMPLE OUTPUT.)*"]
    return "\n".join(lines) + "\n"


def load_study(path: Path, panel) -> pd.DataFrame:
    con = connect(threads=1)
    return con.execute(f"SELECT * FROM read_parquet('{path}')").fetchdf()


def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    prov = provenance(cfg, mode)
    W = cfg["warmup"]
    out_dir = resolve(cfg, "outputs", W["out_subdir"])

    logger.info("=" * 72)
    logger.info("BDB27 Phase 3d warm-up phenotype | mode=%s | provenance=%s", mode, prov)
    logger.info("=" * 72)

    summary_path = out_dir / "warmup_summary.json"
    if summary_path.exists() and not force:
        logger.info("checkpoint hit: %s exists (use --force to rebuild)", summary_path.name)
        append_run_log(cfg, "03d_warmup_phenotype:checkpoint", mode, 0.0, peak_ram_mb(), "",
                       {"skipped": True, "reason": "warmup_summary.json exists; use --force"})
        return 0

    core = _load_module("combine03", CORE_PATH)
    panel = _load_module("panel03b", PANEL_PATH)

    study_path = resolve(cfg, "outputs", "features") / "combine_features_study.parquet"
    if not study_path.exists():
        raise FileNotFoundError(f"missing Phase-2 handoff: {study_path} (run src/02_features.py first)")

    with timed_stage(cfg, "03d_warmup_phenotype:load", mode):
        study = panel.load_study(study_path)
        con = connect(threads=int(cfg.get("runtime", {}).get("duckdb_threads", 1)))
        T = f"read_parquet('{resolve(cfg, 'parquet') / 'combine_tracking.parquet'}')"
        frames = panel.load_frames(con, T)
    logger.info("loaded study rows=%d, frames=%d", len(study), frames["event_id"].size)

    with timed_stage(cfg, "03d_warmup_phenotype:build", mode):
        d, diag = build_warmup_frame(study, frames, cfg, panel)
    logger.info("order reproduction valid=%s (max|diff|=%.3g); O4 obs missing=%d",
                diag["order_repro_valid"], diag["order_repro_max_abs_diff_vs_prior_load_yd"],
                diag["n_observed_missing_o4"])
    if not diag["order_repro_valid"]:
        raise AssertionError("warmup ordering reproduction does not match Phase-2 prior_load_yd")

    with timed_stage(cfg, "03d_warmup_phenotype:cells", mode):
        known: dict[str, dict] = {}
        cells = run_cells(d, cfg, panel, core, known)
    n_met = int(cells["reliability_met"].sum())
    logger.info("cells: %d; reliability>=%.2f: %d", len(cells), float(cfg["panel"]["reliability_stop_threshold"]), n_met)

    with timed_stage(cfg, "03d_warmup_phenotype:deconf", mode):
        deconf = deconfounding(d, cfg, cells, core)
    logger.info("deconfounding rows=%d; primary deconf_pass=%s",
                len(deconf), bool(deconf[deconf["is_primary"]]["deconf_pass"].iloc[0])
                if (deconf["is_primary"]).any() else None)

    with timed_stage(cfg, "03d_warmup_phenotype:phenotypes", mode):
        slopes_by_cell: dict[str, pd.DataFrame] = {}
        slope_rows: list[pd.DataFrame] = []
        for lcode, load_col in W["load_metrics"].items():
            for ocode, outcome_col in W["outcome_metrics"].items():
                sl = player_warmup_slopes(d, cfg, core, load_col, outcome_col)
                key = f"{lcode}|{ocode}"
                slopes_by_cell[key] = sl
                if len(sl):
                    t = sl.copy()
                    t.insert(0, "load_code", lcode)
                    t.insert(1, "load_metric", load_col)
                    t.insert(2, "outcome_code", ocode)
                    t.insert(3, "outcome_metric", outcome_col)
                    slope_rows.append(t)
        warmup_slopes = pd.concat(slope_rows, ignore_index=True) if slope_rows else pd.DataFrame()

        boot_reps = int(W["phenotype_boot_reps"])
        boot_seed = int(W["phenotype_boot_seed"])
        min_att = int(W["phenotype_min_attempts"])
        pos_tables: dict[str, pd.DataFrame] = {}
        for ocol in sorted(set([W["primary_outcome"], str(cfg["model"]["outcome"])])):
            pos_tables[ocol] = session_position_of_peak(d, cfg, ocol, boot_reps, boot_seed, min_att)

        sat_tables: dict[str, pd.DataFrame] = {}
        for load_col in [W["primary_load"], "session_rank"]:
            sat_tables[load_col] = player_saturation(d, cfg, load_col,
                                                     W["primary_outcome"], boot_reps, boot_seed)

    with timed_stage(cfg, "03d_warmup_phenotype:reliability", mode):
        reliab = build_reliability(d, cfg, cells, slopes_by_cell, pos_tables, sat_tables, core)

    headline = _headline(cfg, deconf, reliab, sat_tables)
    logger.info("headline: claim=%s deconf_pass=%s median_reps_to_peak=%.4g (rel=%.4f)",
                headline["claim"], headline["deconf_pass"], headline["median_reps_to_peak"],
                headline["headline_reliability"])

    with timed_stage(cfg, "03d_warmup_phenotype:write", mode):
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_outputs(cfg, prov, out_dir, cells, deconf, warmup_slopes, pos_tables,
                       sat_tables, reliab, diag, headline)

    logger.info("peak RAM: %.1f MB", peak_ram_mb())
    return 0


def _labelled(df: pd.DataFrame, prov: str) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, "provenance", prov)
    return out


def _write_outputs(cfg: dict[str, Any], prov: str, out_dir: Path, cells: pd.DataFrame,
                   deconf: pd.DataFrame, warmup_slopes: pd.DataFrame,
                   pos_tables: dict[str, pd.DataFrame], sat_tables: dict[str, pd.DataFrame],
                   reliab: pd.DataFrame, diag: dict[str, Any], headline: dict[str, Any]) -> None:
    label = "UNVALIDATED SAMPLE OUTPUT" if prov == "UNVALIDATED SAMPLE OUTPUT" else prov

    _labelled(cells, prov).to_csv(out_dir / "warmup_cells.csv", index=False)
    (out_dir / "warmup_cells.json").write_text(
        json.dumps({"provenance": prov, "n_cells": int(len(cells)),
                    "cells": cells.to_dict(orient="records")}, indent=2, sort_keys=True,
                   default=str) + "\n", encoding="utf-8")

    _labelled(deconf, prov).to_csv(out_dir / "warmup_deconfounding.csv", index=False)
    (out_dir / "warmup_deconfounding.json").write_text(
        json.dumps({"provenance": prov, "rows": deconf.to_dict(orient="records")},
                   indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    _labelled(warmup_slopes, prov).to_csv(out_dir / "warmup_phenotypes.csv", index=False)
    pos_all = pd.concat([t.assign(outcome_metric=k) for k, t in pos_tables.items()],
                        ignore_index=True) if pos_tables else pd.DataFrame()
    _labelled(pos_all, prov).to_csv(out_dir / "warmup_phenotype_positions.csv", index=False)
    sat_all = pd.concat([t for t in sat_tables.values()], ignore_index=True) if sat_tables else pd.DataFrame()
    _labelled(sat_all, prov).to_csv(out_dir / "warmup_phenotype_saturation.csv", index=False)
    (out_dir / "warmup_phenotypes.json").write_text(json.dumps({
        "provenance": prov,
        "warmup_slopes": warmup_slopes.to_dict(orient="records"),
        "session_position_of_peak": pos_all.to_dict(orient="records"),
        "saturation": sat_all.to_dict(orient="records"),
    }, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    _labelled(reliab, prov).to_csv(out_dir / "warmup_reliability.csv", index=False)
    (out_dir / "warmup_reliability.json").write_text(
        json.dumps({"provenance": prov, "rows": reliab.to_dict(orient="records")},
                   indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    summary = {
        "provenance": prov,
        "n_cells": int(len(cells)),
        "warmup_params": cfg["warmup"],
        "diagnostics": diag,
        "headline": headline,
        "deconfounding": deconf.to_dict(orient="records"),
        "cells_reliability_met": int(cells["reliability_met"].sum()),
    }
    (out_dir / "warmup_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    # a long key/value CSV view of the headline + de-confounding verdicts (spec: summary .{csv,json})
    flat = {"provenance": prov, "n_cells": int(len(cells)),
            "cells_reliability_met": int(cells["reliability_met"].sum())}
    flat.update({f"headline_{k}": v for k, v in headline.items()})
    flat.update({f"diag_{k}": v for k, v in diag.items() if not isinstance(v, dict)})
    kv = pd.DataFrame({"key": list(flat.keys()), "value": [str(v) for v in flat.values()]})
    kv.insert(0, "provenance", prov)
    kv.to_csv(out_dir / "warmup_summary.csv", index=False)

    (out_dir / "SUMMARY_3d.md").write_text(_summary_md(summary, deconf, cells, reliab),
                                           encoding="utf-8")
    (out_dir / "PROVENANCE_3d.txt").write_text(
        f"provenance: {prov}\nscript: 03d_warmup_phenotype.py\n"
        f"note: {label}\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 3d — warm-up phenotype (D23)")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild outputs (ignore checkpoint)")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or cfg["mode"]["default"]
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
