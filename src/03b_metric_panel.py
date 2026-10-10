"""Phase 3b — pre-registered metric panel for the BDB27 combine-fatigue study.

Tests whether a *better load metric* rescues per-player slope **reliability >= 0.2**
(the Phase-5 gate) versus the flat, metric-robust Phase-3 null (D20). The panel is
**pre-registered** in ``config.yaml`` (``panel:`` block) and ``notes/decisions.md``
**D21**, written before any panel result was computed (no HARKing). Thresholds,
weights and formulas are FIXED.

Design (see D21 for the verbatim declaration)
----------------------------------------------
- **Load metrics** (cumulative *prior* load since session start, mirroring Phase-2
  ``prior_load_yd`` semantics incl. the observed+imputed rule — imputed rows carry no
  performance but DO contribute cumulative load):
    * L1 ``prior_load_yd`` (distance, yd) — existing primary
    * L2 ``prior_load_hmld`` (HMLD threshold-sum, yd = HSD + accel/decel-equivalent)
    * L3 ``prior_load_hsd`` (high-speed distance, yd)
    * L4 ``prior_load_accel`` (accel/decel distance-equivalent, yd)
    * L5 ``prior_load_efforts`` (count) — existing
    * L6 ``elapsed_session_s`` (s) — existing
    * L7 ``prior_load_mp`` (di Prampero metabolic-power distance, yd, P > 25.5 W/kg)
- **Outcome metrics** (standardised within (drill_type, combine_position) by Phase 2):
    * O1 ``perf_z`` · O2 ``peak_accel_z`` · O3 ``t90_z``
- **Panel** = every (L, O) cell (L1–L7 × O1–O3 = 21). Cell rule = the SAME Phase-3
  fallback chain + reliability formula + stop rule (``model.reliability_stop_threshold``).

New per-attempt metrics (L2/L3/L4/L7) are computed from the 10 Hz frames in
``data/interim/parquet/combine_tracking.parquet`` for the DB-only study population
(``outputs/features/combine_features_study.parquet``):

1. Savitzky–Golay smoothing of ``x``/``y`` (``panel.smooth_window_frames`` /
   ``smooth_polyorder``); speed/accel from the smoothed positions.
2. Clip speed/accel to the declared physical maxima BEFORE thresholding; count clips.
3. Per segment (``0 < dt <= features.distance_gap_max_s`` — the same distance basis as
   ``effort_cost_yd``): distance, ``v`` (yd/s), ``a`` (yd/s^2) from the smoothed
   trajectory; thresholds converted from SI (``m_per_yd``).
4. ``hsd = Sum d*1(v>v_hs)``; ``accel = Sum d*1(|a|>a_th AND v<=v_hs)``;
   ``hmld = hsd + accel``; ``mp = Sum d*1(P>mp_threshold_wkg)`` with
   ``P = EC(arctan(a/g))*v`` (di Prampero/Osgnach; coefficients in ``panel.ec_coef``).

Imputed attempts take the player-drill median (fallback: drill-level median) of each new
metric, matching Phase-2 ``_impute_rows``. The Phase-2 within-session ordering is
**reproduced** deterministically and **validated** by requiring
``cumsum(effort_cost_yd) == prior_load_yd``.

This module does NOT touch ``01``–``03`` behaviour or outputs; it only ADDS new files in
``outputs/model/`` (``metric_panel.csv`` / ``metric_panel_summary.md`` / ``metric_panel.json``).

Run:
    python src/03b_metric_panel.py --mode sample
    python src/03b_metric_panel.py --mode sample --force
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
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

CORE = Path(__file__).resolve().parent / "03_combine_model.py"


def _load_core():
    """Load ``03_combine_model`` as a module (its filename is not importable as-is)."""
    spec = importlib.util.spec_from_file_location("combine03", CORE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Required per-cell record fields (D21). Order: provenance first (CSV), then these.
PANEL_FIELDS: list[str] = [
    "load_code", "load_metric", "outcome_code", "outcome_metric",
    "estimator", "optimizer", "load_coef", "load_se", "load_p", "perm_p",
    "tau2", "mean_se2", "reliability", "reliability_met", "stop_triggered",
    "skip_phase5_link", "n_players", "n_obs", "load2_included",
    "load_n_unique", "load_iqr",
]

NEW_BASE_COLS = ["hsd_yd", "accel_yd", "hmld_yd", "mp_yd"]
NEW_LOAD_COLS = {
    "prior_load_hsd": "hsd_yd",
    "prior_load_accel": "accel_yd",
    "prior_load_hmld": "hmld_yd",
    "prior_load_mp": "mp_yd",
}


# --------------------------------------------------------------------------- #
# Pure helpers (importable by tests)
# --------------------------------------------------------------------------- #
def panel_thresholds(cfg: dict[str, Any]) -> dict[str, float]:
    """Convert the declared SI thresholds to the yard-space thresholds used on x/y.

    ``features.*`` positions are in yards, so ``v``/``a`` derived from them are
    yd/s and yd/s^2; SI thresholds divide by ``m_per_yd`` to match. No magic numbers.
    """
    p = cfg["panel"]
    mpy = float(p["m_per_yd"])
    assert mpy > 0
    return {
        "m_per_yd": mpy,
        "g": float(p["g_mps2"]),
        "v_hs_yds": float(p["speed_hs_mps"]) / mpy,
        "a_th_yds2": float(p["accel_th_mps2"]) / mpy,
        "v_clip_yds": float(p["speed_clip_mps"]) / mpy,
        "a_clip_yds2": float(p["accel_clip_mps2"]) / mpy,
        "mp_threshold_wkg": float(p["mp_threshold_wkg"]),
        "window": int(p["smooth_window_frames"]),
        "polyorder": int(p["smooth_polyorder"]),
    }


def ec_energy_cost(a_mps2: np.ndarray, g: float, coef: list[float]) -> np.ndarray:
    """di Prampero/Osgnach energy cost EC (J/kg/m) from acceleration (m/s^2).

    ``ES = arctan(a/g)``; ``EC = polyval(coef, ES)`` with ``coef`` descending in ES.
    """
    es = np.arctan(np.asarray(a_mps2, dtype=np.float64) / float(g))
    return np.polyval(np.asarray(coef, dtype=np.float64), es)


def smooth_positions(x: np.ndarray, y: np.ndarray, window: int, polyorder: int):
    """Savitzky–Golay smoothing of x,y. Window shrinks (odd, > polyorder) for short attempts."""
    n = int(min(x.size, y.size))
    w = int(window)
    if w > n:
        w = n if n % 2 == 1 else n - 1
    if w < 3 or w <= int(polyorder):
        return x.copy(), y.copy()
    return (savgol_filter(np.asarray(x, float), w, int(polyorder)),
            savgol_filter(np.asarray(y, float), w, int(polyorder)))


def attempt_metrics(t: np.ndarray, x: np.ndarray, y: np.ndarray,
                    thr: dict[str, float], ec_coef: list[float],
                    dist_gap_max_s: float) -> dict[str, Any]:
    """Per-attempt high-intensity metrics from one attempt's frames (smoothed kinematics).

    Returns ``hsd_yd, accel_yd, hmld_yd, mp_yd`` (yards) plus clip counters, an
    interp-array of the smoothed speed and the pooled cross-check pairs
    ``(v_yds, s_provided)`` / ``(a_yds2, a_provided)`` for the provided s/a agreement.
    """
    t = np.asarray(t, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    nan = float("nan")
    out: dict[str, Any] = {"n_frames": int(t.size), "hsd_yd": nan, "accel_yd": nan,
                           "hmld_yd": nan, "mp_yd": nan, "n_clip_speed": 0,
                           "n_clip_accel": 0, "v_yds": np.array([]), "s_prov": np.array([]),
                           "a_yds2": np.array([]), "a_prov": np.array([])}
    if t.size < 2:
        return out
    xs, ys = smooth_positions(x, y, thr["window"], thr["polyorder"])
    dt = np.diff(t)
    dist = np.hypot(np.diff(xs), np.diff(ys))
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(dt > 0, dist / dt, nan)
    a = np.full(dt.shape, nan, dtype=np.float64)
    if dt.size >= 2:
        denom = (dt[1:] + dt[:-1]) / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            a[1:] = np.where(denom > 0, (v[1:] - v[:-1]) / denom, nan)

    # ---- clip BEFORE thresholds (physical artifacts) ----
    vclip = thr["v_clip_yds"]
    aclip = thr["a_clip_yds2"]
    n_clip_v = int(np.sum(np.isfinite(v) & (np.abs(v) > vclip)))
    n_clip_a = int(np.sum(np.isfinite(a) & (np.abs(a) > aclip)))
    v = np.clip(v, -vclip, vclip)
    a = np.clip(a, -aclip, aclip)

    # ---- segments on the effort-cost distance basis (0 < dt <= dist_gap_max_s) ----
    seg = np.isfinite(v) & np.isfinite(dist) & (dt > 0) & (dt <= float(dist_gap_max_s))
    v_hs = thr["v_hs_yds"]
    a_th = thr["a_th_yds2"]
    fast = seg & (v > v_hs)
    slow = seg & (v <= v_hs)
    hard = seg & np.isfinite(a) & (np.abs(a) > a_th)
    hsd = float(np.sum(dist[fast]))
    accel = float(np.sum(dist[slow & hard]))
    hmld = hsd + accel
    # di Prampero metabolic power (SI units), over the same segments
    a_si = a * thr["m_per_yd"]
    v_si = v * thr["m_per_yd"]
    ec = ec_energy_cost(a_si, thr["g"], ec_coef)
    power = ec * v_si
    mp = float(np.sum(dist[seg & np.isfinite(power) & (power > thr["mp_threshold_wkg"])]))

    out.update({"hsd_yd": hsd, "accel_yd": accel, "hmld_yd": hmld, "mp_yd": mp,
                "n_clip_speed": n_clip_v, "n_clip_accel": n_clip_a,
                "v_yds": v, "a_yds2": a})
    return out


def compute_new_metrics(frames: dict[str, np.ndarray], study_events: set[str],
                        cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Per-observed-attempt HSD/accel/HMLD/MP + pooled provided-s/a cross-check stats."""
    thr = panel_thresholds(cfg)
    ec_coef = list(cfg["panel"]["ec_coef"])
    dist_gap_max_s = float(cfg["features"]["distance_gap_max_s"])

    ev = frames["event_id"]
    ts = frames["t_s"]
    xs = frames["x"]
    ys = frames["y"]
    ss = frames["s"]
    aa = frames["a"]

    uniq, starts, counts = np.unique(ev, return_index=True, return_counts=True)
    order = np.argsort(starts)
    uniq, starts, counts = uniq[order], starts[order], counts[order]
    ends = starts + counts

    rows: list[dict[str, Any]] = []
    v_all: list[np.ndarray] = []
    s_all: list[np.ndarray] = []
    a_all: list[np.ndarray] = []
    a_pr_all: list[np.ndarray] = []
    n_clip_v = n_clip_a = 0
    for g in range(uniq.size):
        eid = str(uniq[g])
        if eid not in study_events:
            continue
        a0, b0 = int(starts[g]), int(ends[g])
        m = attempt_metrics(ts[a0:b0], xs[a0:b0], ys[a0:b0], thr, ec_coef, dist_gap_max_s)
        rows.append({"event_id": eid, "hsd_yd": m["hsd_yd"], "accel_yd": m["accel_yd"],
                     "hmld_yd": m["hmld_yd"], "mp_yd": m["mp_yd"]})
        n_clip_v += m["n_clip_speed"]
        n_clip_a += m["n_clip_accel"]
        if m["v_yds"].size:
            k = m["v_yds"].size
            # v[i] is the segment ending at frame i+1 -> pair with provided s at frame i+1
            prov_s = ss[a0:b0][1:1 + k]
            v_all.append(m["v_yds"])
            s_all.append(np.asarray(prov_s, dtype=np.float64))
            if m["a_yds2"].size:
                ka = m["a_yds2"].size
                # a[i] is centred on frame i -> pair with provided a at frame i
                a_all.append(m["a_yds2"])
                a_pr_all.append(np.asarray(aa[a0:b0][0:ka], dtype=np.float64))

    metrics = pd.DataFrame(rows, columns=["event_id", "hsd_yd", "accel_yd", "hmld_yd", "mp_yd"])

    def _agree(preds: list[np.ndarray], prov: list[np.ndarray], name: str) -> dict[str, Any]:
        if not preds:
            return {f"xcheck_{name}_n": 0, f"xcheck_{name}_mean_abs_diff": float("nan"),
                    f"xcheck_{name}_pearson_r": float("nan")}
        p = np.concatenate(preds)
        q = np.concatenate(prov)
        ok = np.isfinite(p) & np.isfinite(q)
        n = int(ok.sum())
        mad = float(np.mean(np.abs(p[ok] - q[ok]))) if n else float("nan")
        r = float("nan")
        if n >= 3 and np.unique(p[ok]).size > 1 and np.unique(q[ok]).size > 1:
            r = float(stats.pearsonr(p[ok], q[ok])[0])
        return {f"xcheck_{name}_n": n, f"xcheck_{name}_mean_abs_diff": mad,
                f"xcheck_{name}_pearson_r": r}

    diag = {"n_clipped_speed": int(n_clip_v), "n_clipped_accel": int(n_clip_a),
            "n_attempts_metrics": int(len(metrics)), **thr}
    diag.update(_agree(v_all, s_all, "speed"))
    diag.update(_agree(a_all, a_pr_all, "accel"))
    return metrics, diag


# --------------------------------------------------------------------------- #
# Ordering reproduction + cumulative loads
# --------------------------------------------------------------------------- #
def reproduce_order(df: pd.DataFrame) -> pd.DataFrame:
    """Reproduce Phase-2 ``_order_and_load`` ordering (unit_first_start_rank, ...).

    ``unit_first_start_rank`` (D19) = within-(player, session) rank of the numbering
    unit's min observed start time (unit = drill_name for restart units, else drill_type).
    """
    d = df.copy().reset_index(drop=True)
    d["attempt_start_time"] = pd.to_datetime(d["attempt_start_time"])
    d["unit_key"] = np.where(d["attempt_unit"].to_numpy() == "drill_name",
                             d["drill_name"].to_numpy(), d["drill_type"].to_numpy())
    observed = d["is_imputed"] == 0
    firsts = (d.loc[observed]
              .groupby(["nfl_id", "session_id", "unit_key"], sort=True)["attempt_start_time"]
              .min().reset_index())
    firsts["unit_first_start_rank"] = (
        firsts.sort_values(["nfl_id", "session_id", "attempt_start_time", "unit_key"])
        .groupby(["nfl_id", "session_id"]).cumcount() + 1)
    rank_map = firsts.set_index(["nfl_id", "session_id", "unit_key"])["unit_first_start_rank"]
    d["unit_first_start_rank"] = [
        rank_map.get((int(n), s, u), np.nan)
        for n, s, u in zip(d["nfl_id"], d["session_id"], d["unit_key"])
    ]
    d = d.sort_values(["nfl_id", "session_id", "unit_first_start_rank", "attempt",
                       "is_imputed", "event_id"], kind="mergesort").reset_index(drop=True)
    return d


def _impute_new_metrics(d: pd.DataFrame) -> pd.DataFrame:
    """Impute new-metric effort on imputed rows: player-drill median (drill-level fallback)."""
    obs = d["is_imputed"] == 0
    for base in NEW_BASE_COLS:
        lut_pd = d.loc[obs].groupby(["nfl_id", "drill_name"])[base].median()
        lut_dn = d.loc[obs].groupby("drill_name")[base].median()
        lut_dt = d.loc[obs].groupby("drill_type")[base].median()
        need = d[base].isna()
        if not bool(need.any()):
            continue
        sub = d.loc[need, ["nfl_id", "drill_name", "drill_type"]]
        fill = pd.Series(sub.set_index(["nfl_id", "drill_name"]).index.map(lut_pd).to_numpy(dtype=float),
                         index=sub.index)
        m = fill.isna()
        if m.any():
            fill.loc[m] = sub.loc[m, "drill_name"].map(lut_dn)
        m = fill.isna()
        if m.any():
            fill.loc[m] = sub.loc[m, "drill_type"].map(lut_dt)
        d.loc[need, base] = fill.to_numpy(dtype=float)
    return d


def build_panel_frame(study: pd.DataFrame, metrics: pd.DataFrame,
                      cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach new metrics, impute, reproduce order, compute cumulative prior loads.

    Validates the ordering reproduction by recomputing ``cumsum(effort_cost_yd)`` and
    requiring it to equal the Phase-2 ``prior_load_yd`` (max abs diff reported).
    """
    d = study.copy()
    d["event_id"] = d["event_id"].astype("object")
    d = d.merge(metrics, on="event_id", how="left")
    d = _impute_new_metrics(d)
    d = reproduce_order(d)

    # cumulative prior load (exclusive) per session, for every load metric
    for outcol, base in NEW_LOAD_COLS.items():
        eff = d[base].fillna(0.0)
        d[outcol] = eff.groupby(d["session_id"], sort=False).cumsum() - eff

    # ---- validate the reproduced order via the existing L1 ----
    # NOTE: prior_load_yd is stored as float32 in the parquet, so allow a small absolute
    # tolerance for the float32 storage rounding of an otherwise float64 cumulative sum.
    ORDER_REPRO_ATOL = 1e-3
    eff1 = d["effort_cost_yd"].fillna(0.0)
    rec1 = eff1.groupby(d["session_id"], sort=False).cumsum() - eff1
    max_diff = float(np.nanmax(np.abs(rec1.to_numpy(dtype=float) - d["prior_load_yd"].to_numpy(dtype=float))))
    obs = d["is_imputed"] == 0
    n_obs_missing_metric = int(d.loc[obs, "hsd_yd"].isna().sum())
    val = {"order_repro_max_abs_diff_vs_prior_load_yd": max_diff,
           "order_repro_atol": ORDER_REPRO_ATOL,
           "order_repro_valid": bool(max_diff <= ORDER_REPRO_ATOL),
           "n_observed_missing_new_metrics": n_obs_missing_metric,
           "n_imputed_rows": int((~obs).sum())}
    return d, val


# --------------------------------------------------------------------------- #
# Panel cells
# --------------------------------------------------------------------------- #
def run_cell(d: pd.DataFrame, cfg: dict[str, Any], core, load_col: str,
             outcome_col: str) -> dict[str, Any]:
    """Run one (L, O) cell: load² criterion + Phase-3 fallback chain + permutation."""
    m = cfg["model"]
    p = cfg["panel"]
    df_model = d[(d["is_imputed"] == 0) & d[outcome_col].notna() & d[load_col].notna()].copy()
    # Canonical Phase-2/3 row order (the study parquet's write order) so the panel's
    # L1/O1 cell reproduces ``03_combine_model.py`` exactly (incl. the permutation
    # Monte-Carlo realisation; the statistic is order-invariant in expectation).
    df_model = df_model.sort_values(
        ["nfl_id", "drill_name", "attempt", "is_imputed", "event_id"],
        kind="mergesort").reset_index(drop=True)
    df_model["first_attempt"] = df_model["first_attempt"].fillna(0).astype(int)

    load = df_model[load_col].to_numpy(dtype=np.float64)
    load = load[np.isfinite(load)]
    n_unique = int(np.unique(load).size)
    iqr = float(np.percentile(load, 75) - np.percentile(load, 25)) if load.size else float("nan")
    include_load2 = bool(n_unique >= int(p["load2_min_unique"]) and iqr > float(p["load2_min_iqr"]))

    chosen, _chain = core.fit_chain(df_model, cfg, include_load2=include_load2,
                                    load_col=load_col, outcome_col=outcome_col)
    stat_obs, perm_p, _ = core.permutation_test(df_model, cfg, load_col=load_col,
                                                outcome_col=outcome_col)

    rel = float(chosen["reliability"])
    stop = bool(not (rel >= float(m["reliability_stop_threshold"])))
    skip = bool(stop or chosen["estimator"] == "random_intercept")
    return {
        "load_metric": load_col, "outcome_metric": outcome_col,
        "estimator": chosen["estimator"], "optimizer": chosen["optimizer"],
        "load_coef": chosen["load_coef"], "load_se": chosen["load_se"],
        "load_p": chosen["load_p"], "perm_p": perm_p,
        "tau2": chosen["tau2"], "mean_se2": chosen["mean_se2"],
        "reliability": rel, "reliability_met": bool(rel >= 0.2),
        "stop_triggered": stop, "skip_phase5_link": skip,
        "n_players": int(df_model["nfl_id"].nunique()), "n_obs": int(len(df_model)),
        "load2_included": include_load2, "load_n_unique": n_unique, "load_iqr": iqr,
        "perm_stat_observed": stat_obs,
    }


def run_panel(d: pd.DataFrame, cfg: dict[str, Any], prov: str, out_dir: Path,
              logger=None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run every (L, O) cell and write ``metric_panel.csv`` / ``.json`` / ``_summary.md``."""
    core = _load_core()
    pops = list(cfg["panel"]["load_metrics"].items())      # [(L1, prior_load_yd), ...]
    outs = list(cfg["panel"]["outcome_metrics"].items())   # [(O1, perf_z), ...]

    rows: list[dict[str, Any]] = []
    for lcode, load_col in pops:
        for ocode, outcome_col in outs:
            rec = run_cell(d, cfg, core, load_col, outcome_col)
            rec["load_code"] = lcode
            rec["outcome_code"] = ocode
            rows.append(rec)
            if logger:
                logger.info("cell %s/%s rel=%.4f met=%s est=%s perm_p=%.4g",
                            lcode, ocode, rec["reliability"], rec["reliability_met"],
                            rec["estimator"], rec["perm_p"])

    panel = pd.DataFrame(rows)
    panel = panel[["load_code", "load_metric", "outcome_code", "outcome_metric"] +
                  [c for c in PANEL_FIELDS if c not in
                   ("load_code", "load_metric", "outcome_code", "outcome_metric")] +
                  ["perm_stat_observed"]]

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_df = panel.copy()
    csv_df.insert(0, "provenance", prov)
    csv_df.to_csv(out_dir / "metric_panel.csv", index=False)

    summary = {
        "provenance": prov,
        "n_cells": int(len(panel)),
        "reliability_stop_threshold": float(cfg["panel"]["reliability_stop_threshold"]),
        "n_cells_reliability_met": int(panel["reliability_met"].sum()),
        "metrics_lifting_reliability_by_outcome": {
            ocode: sorted(set(panel.loc[(panel["outcome_code"] == ocode) &
                                        panel["reliability_met"], "load_metric"]))
            for ocode, _ in outs
        },
        "cells": panel.to_dict(orient="records"),
    }
    (out_dir / "metric_panel.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out_dir / "metric_panel_summary.md").write_text(
        _summary_md(panel, cfg), encoding="utf-8")
    return panel, summary


def _summary_md(panel: pd.DataFrame, cfg: dict[str, Any]) -> str:
    thr = float(cfg["panel"]["reliability_stop_threshold"])
    met = panel[panel["reliability_met"]]
    lines = [
        "# Metric panel summary — pre-registered (D21)",
        "",
        f"- cells: **{len(panel)}** (L1–L7 × O1–O3); reliability gate **≥{thr}**",
        f"- cells meeting the ≥{thr} gate: **{len(met)}** of {len(panel)}",
        "",
        "## Does any metric rescue per-player slope reliability?",
        "",
    ]
    if len(met) == 0:
        lines.append("**No.** No (load, outcome) cell reaches reliability ≥ "
                     f"{thr}: the Phase-3 flat null is **metric-robust** — swapping the "
                     "load metric (distance / HSD / accel / HMLD / efforts / elapsed time / "
                     "metabolic power) or the outcome (peak speed / peak accel / t90) does "
                     "not rescue per-player slope reliability.")
    else:
        for _, r in met.iterrows():
            lines.append(f"- **{r['load_metric']} × {r['outcome_metric']}**: reliability "
                         f"{r['reliability']:.4f} (estimator `{r['estimator']}`, "
                         f"load coef {r['load_coef']:.6g}, p {r['load_p']:.4g})")
    lines += ["", "## Panel table", "",
              "| load | outcome | estimator | load_coef | load_p | perm_p | reliability | met | stop | n_obs | load2 |",
              "|---|---|---|---:|---:|---:|---:|:--:|:--:|---:|:--:|"]
    for _, r in panel.iterrows():
        lines.append(
            f"| {r['load_metric']} | {r['outcome_metric']} | {r['estimator']} | "
            f"{r['load_coef']:.6g} | {r['load_p']:.4g} | {r['perm_p']:.4g} | "
            f"{r['reliability']:.4f} | {bool(r['reliability_met'])} | {bool(r['stop_triggered'])} | "
            f"{int(r['n_obs'])} | {bool(r['load2_included'])} |")
    neg = panel["load_coef"] < 0
    lines += ["", "## Population negative load effect held across metrics?",
              "",
              f"- cells with negative population load coef: **{int(neg.sum())}/{len(panel)}** "
              f"(coef sign is population-level; reliability concerns between-player slope variance).",
              "",
              "*(All CSVs carry a `provenance` first column. Sample outputs are UNVALIDATED SAMPLE OUTPUT.)*"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def load_study(path: Path) -> pd.DataFrame:
    con = connect(threads=1)
    return con.execute(f"SELECT * FROM read_parquet('{path}')").fetchdf()


def load_frames(con, T: str) -> dict[str, np.ndarray]:
    sql = f"""
        SELECT event_id, epoch_ms(time) / 1000.0 AS t_s, x, y, s, a
        FROM {T} WHERE entity_type = 'PLAYER' ORDER BY event_id, t_s
    """
    return con.execute(sql).fetchnumpy()


def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    prov = provenance(cfg, mode)
    out_dir = resolve(cfg, "outputs", cfg["panel"]["out_subdir"])
    json_path = out_dir / "metric_panel.json"

    logger.info("=" * 72)
    logger.info("BDB27 Phase 3b metric panel | mode=%s | provenance=%s", mode, prov)
    logger.info("=" * 72)

    if json_path.exists() and not force:
        logger.info("checkpoint hit: %s exists (use --force to rebuild)", json_path.name)
        append_run_log(cfg, "03b_metric_panel:checkpoint", mode, 0.0, peak_ram_mb(), "",
                       {"skipped": True, "reason": "metric_panel.json exists; use --force"})
        return 0

    study_path = resolve(cfg, "outputs", "features") / "combine_features_study.parquet"
    if not study_path.exists():
        raise FileNotFoundError(f"missing Phase-2 handoff: {study_path} (run src/02_features.py first)")

    with timed_stage(cfg, "03b_metric_panel:load", mode):
        study = load_study(study_path)
        parq = resolve(cfg, "parquet")
        con = connect(threads=int(cfg.get("runtime", {}).get("duckdb_threads", 1)))
        T = f"read_parquet('{parq / 'combine_tracking.parquet'}')"
        frames = load_frames(con, T)
    logger.info("loaded study rows=%d, frames=%d", len(study), frames["event_id"].size)

    with timed_stage(cfg, "03b_metric_panel:metrics", mode):
        obs_events = set(study.loc[study["is_imputed"] == 0, "event_id"].astype(str))
        metrics, mdiag = compute_new_metrics(frames, obs_events, cfg)
    logger.info("new metrics: %d attempts, clipped speed/accel=%d/%d, xcheck speed MAD=%.4g r=%.4g",
                mdiag["n_attempts_metrics"], mdiag["n_clipped_speed"], mdiag["n_clipped_accel"],
                mdiag["xcheck_speed_mean_abs_diff"], mdiag["xcheck_speed_pearson_r"])

    with timed_stage(cfg, "03b_metric_panel:build", mode):
        d, val = build_panel_frame(study, metrics, cfg)
    logger.info("order reproduction valid=%s (max|diff|=%.3g vs prior_load_yd)",
                val["order_repro_valid"], val["order_repro_max_abs_diff_vs_prior_load_yd"])
    if not val["order_repro_valid"]:
        raise AssertionError("panel ordering reproduction does not match Phase-2 prior_load_yd")

    with timed_stage(cfg, "03b_metric_panel:panel", mode):
        panel, summary = run_panel(d, cfg, prov, out_dir, logger=logger)

    # ---- diagnostics sidecar (metric construction + validation) ----
    mdiag_df = pd.DataFrame({"metric": list({**val, **mdiag}.keys()),
                             "value": list({**val, **mdiag}.values())})
    mdiag_df.insert(0, "provenance", prov)
    mdiag_df.to_csv(out_dir / "metric_panel_diagnostics.csv", index=False)
    summary["diagnostics"] = {**val, **mdiag}
    (out_dir / "metric_panel.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    logger.info("panel: %d cells, %d meet reliability>=%.2f", len(panel),
                int(panel["reliability_met"].sum()), float(cfg["panel"]["reliability_stop_threshold"]))
    logger.info("peak RAM: %.1f MB", peak_ram_mb())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 3b — pre-registered metric panel")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild outputs (ignore checkpoint)")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or cfg["mode"]["default"]
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
