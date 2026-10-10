"""Phase 2 — Combine features for the BDB27 combine-fatigue study.

Per attempt (one observed ``event_id`` = one attempt), computed from ``x``/``y``/
``time`` only (the provided ``s``/``a``/``dis`` are kept as *advisory*
cross-checks, never trusted):

- peak speed, peak signed acceleration, time above ``t90_frac`` of the player's
  best (within the configured baseline level), and effort cost (path distance);
- session splitting, within-session ordering, cumulative prior load (distance +
  count of maximal efforts; observed + imputed);
- rest time from the clock (accounting for imputed attempts);
- lost-attempt (numbering-gap) imputation — load only, flagged, no performance;
- within-(level, position) standardisation of the primary metric and companions;
- ``first_attempt`` and its collinearity with load (VIF).

Design is locked by decisions **D16-D18** (``notes/decisions.md``); every
threshold/path/param comes from ``config.yaml`` (no magic numbers). Combine data
is complete and fits in RAM, so features are computed for real in BOTH modes; the
mode only changes the provenance label. Features are computed for ALL positions
(cheap) and the DB study subset is emitted separately for the Phase-3 handoff.

Run:
    python src/02_features.py --mode sample
    python src/02_features.py --mode sample --force

Outputs (``outputs/features/``):
    combine_features.parquet          per attempt (observed + imputed), ALL positions
    combine_features_study.parquet    in-study-population subset (Phase-3 handoff)
    combine_features_player.parquet   per-player summary
    feature_diagnostics.csv           long key/value diagnostics
    standardization_params.csv        (n, mean, sd) per (level, position, metric)
    vif_report.csv                    VIF of {prior_load_yd, first_attempt}
    PROVENANCE.txt, SUMMARY.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    append_run_log,
    connect,
    csv_path,
    get_logger,
    load_config,
    peak_ram_mb,
    pq,
    provenance,
    resolve,
    seed_everything,
    timed_stage,
    to_parquet,
)

COMBINE_TRACKING_COLS = [
    "draft_year", "event_id", "nfl_id", "entity_type", "time",
    "drill_type", "drill_name", "attempt", "x", "y", "s", "a", "dis", "dir",
]
COMBINE_RESULTS_COLS = [
    "draft_year", "nfl_id", "combine_position", "combine_height", "combine_weight",
    "hand_size", "arm_length", "wing_span", "ten_yd_split", "forty", "vertical",
    "broad_jump", "three_cone", "short_shuttle", "bench_reps",
    "ngs_athleticism_score", "ngs_college_production_score", "ngs_final_score",
]

# Output column order — MUST match the Phase-2 spec exactly (and provenance first
# for the CSVs; the parquet also carries the ``provenance`` column).
COLUMNS: list[tuple[str, str]] = [
    ("provenance", "VARCHAR"),
    ("nfl_id", "INTEGER"),
    ("draft_year", "INTEGER"),
    ("combine_position", "VARCHAR"),
    ("in_study_population", "BOOLEAN"),
    ("drill_type", "VARCHAR"),
    ("drill_name", "VARCHAR"),
    ("attempt", "INTEGER"),
    ("attempt_unit", "VARCHAR"),
    ("event_id", "VARCHAR"),
    ("is_imputed", "INTEGER"),
    ("session_id", "VARCHAR"),
    ("attempt_start_time", "TIMESTAMP"),
    ("attempt_end_time", "TIMESTAMP"),
    ("elapsed_session_s", "FLOAT"),
    ("n_frames", "INTEGER"),
    ("n_gaps", "INTEGER"),
    ("max_gap_s", "FLOAT"),
    ("peak_speed_yds", "FLOAT"),
    ("peak_accel_yds2", "FLOAT"),
    ("t90_s", "FLOAT"),
    ("effort_cost_yd", "FLOAT"),
    ("best_speed_yds", "FLOAT"),
    ("prior_load_yd", "FLOAT"),
    ("prior_load_efforts", "INTEGER"),
    ("prior_load_observed_yd", "FLOAT"),
    ("prior_load_observed_efforts", "INTEGER"),
    ("perf_z", "FLOAT"),
    ("peak_accel_z", "FLOAT"),
    ("t90_z", "FLOAT"),
    ("effort_cost_z", "FLOAT"),
    ("first_attempt", "INTEGER"),
    ("rest_s", "FLOAT"),
    ("rest_spans_imputed", "INTEGER"),
    ("impute_source", "VARCHAR"),
    ("flag_gap_in_peak_speed_window", "INTEGER"),
    ("flag_gap_in_peak_accel_window", "INTEGER"),
    ("flag_speed_outlier", "INTEGER"),
    ("flag_duplicate_attempt", "INTEGER"),
    ("flag_out_of_order", "INTEGER"),
    ("flag_large_gap", "INTEGER"),
    ("flag_std_group_too_small", "INTEGER"),
    ("advisory_provided_dis_sum", "FLOAT"),
    ("advisory_provided_peak_s", "FLOAT"),
    ("advisory_provided_peak_a", "FLOAT"),
]

PLAYER_COLUMNS: list[tuple[str, str]] = [
    ("provenance", "VARCHAR"),
    ("nfl_id", "INTEGER"),
    ("draft_year", "INTEGER"),
    ("combine_position", "VARCHAR"),
    ("in_study_population", "BOOLEAN"),
    ("n_attempts_total", "INTEGER"),
    ("n_attempts_observed", "INTEGER"),
    ("n_attempts_imputed", "INTEGER"),
    ("n_sessions", "INTEGER"),
    ("n_drill_types", "INTEGER"),
    ("n_drill_names", "INTEGER"),
    ("max_peak_speed_yds", "FLOAT"),
    ("total_effort_yd_observed", "FLOAT"),
    ("first_session_start", "TIMESTAMP"),
    ("last_attempt_end", "TIMESTAMP"),
]

METRICS = ["peak_speed_yds", "peak_accel_yds2", "t90_s", "effort_cost_yd"]
Z_COLUMN = {
    "peak_speed_yds": "perf_z",
    "peak_accel_yds2": "peak_accel_z",
    "t90_s": "t90_z",
    "effort_cost_yd": "effort_cost_z",
}


# --------------------------------------------------------------------------- #
# Pure helpers (importable by tests — no pipeline state)
# --------------------------------------------------------------------------- #
def zscore_group(values, min_n: int):
    """Z-score (ddof=0) within a group. Returns (z, ok).

    ``ok`` is False (and ``z`` all-NaN) when the group is too small
    (``n < min_n``) or degenerate (sd == 0 — standardisation is undefined).
    """
    v = np.asarray(values, dtype=np.float64)
    mask = np.isfinite(v)
    n = int(mask.sum())
    if n < int(min_n):
        return np.full(v.shape, np.nan, dtype=np.float64), False
    mu = float(v[mask].mean())
    sd = float(v[mask].std(ddof=0))
    if not np.isfinite(sd) or sd == 0.0:
        return np.full(v.shape, np.nan, dtype=np.float64), False
    z = np.full(v.shape, np.nan, dtype=np.float64)
    z[mask] = (v[mask] - mu) / sd
    return z, True


def missing_attempt_numbers(observed, max_attempt: int) -> list[int]:
    """Attempt numbers in {1..max_attempt} that were NOT observed (lost data)."""
    have = {int(a) for a in observed}
    return [a for a in range(1, int(max_attempt) + 1) if a not in have]


def detect_attempt_unit(attempts: pd.DataFrame, min_restart_drill_names: int) -> pd.DataFrame:
    """Empirically detect the `attempt` numbering unit per `(nfl_id, drill_type)` block (D19).

    A block is a **per-`drill_name` restart** unit (`attempt_unit='drill_name'`) iff it has
    **>= 2 distinct `drill_name`s AND >= ``min_restart_drill_names`` of them have
    ``min(attempt) == 1``**; otherwise it is a **per-`drill_type` block counter**
    (`attempt_unit='drill_type'`). Single-`drill_name` blocks are unit-invariant
    (grouping by `drill_name` == grouping by `drill_type`) and are reported as
    ``'drill_type'``.

    ``attempts`` is the observed attempt-level frame with
    ``nfl_id, drill_type, drill_name, attempt``. Returns one row per
    ``(nfl_id, drill_type)`` with columns
    ``nfl_id, drill_type, attempt_unit, n_drill_names, n_drill_names_starting_at_1``.
    """
    thr = int(min_restart_drill_names)
    rows: list[dict[str, Any]] = []
    for (nfl, dtyp), grp in attempts.groupby(["nfl_id", "drill_type"], sort=True):
        starts = grp.groupby("drill_name")["attempt"].min()
        n_dn = int(starts.size)
        n_start1 = int((starts.astype(int) == 1).sum())
        unit = "drill_name" if (n_dn >= 2 and n_start1 >= thr) else "drill_type"
        rows.append({
            "nfl_id": int(nfl),
            "drill_type": str(dtyp),
            "attempt_unit": unit,
            "n_drill_names": n_dn,
            "n_drill_names_starting_at_1": n_start1,
        })
    return pd.DataFrame(rows, columns=[
        "nfl_id", "drill_type", "attempt_unit", "n_drill_names", "n_drill_names_starting_at_1"])


def _imputed_drill_name(grp: pd.DataFrame, m: int) -> str:
    """The `drill_name` for an imputed attempt `m`: of the observed rep with the
    largest ``attempt < m`` (fallback: smallest ``attempt > m``; final fallback: the
    group's `drill_name`)."""
    below = grp[grp["attempt"].astype(int) < int(m)]
    if len(below):
        return str(below.loc[below["attempt"].idxmax(), "drill_name"])
    above = grp[grp["attempt"].astype(int) > int(m)]
    if len(above):
        return str(above.loc[above["attempt"].idxmin(), "drill_name"])
    return str(grp["drill_name"].iloc[0])


def resolve_attempt_units(attempts: pd.DataFrame, cfg: dict[str, Any]) -> dict[tuple[int, str], str]:
    """Map every ``(nfl_id, drill_type)`` block to its numbering unit.

    ``features.attempt_level``: ``empirical`` (default) runs the D19 detection;
    ``drill_name`` / ``drill_type`` force a fixed unit for every block (tests /
    sensitivity). The detection is always run once so diagnostics can report the
    *detected* block counts; only the returned assignment depends on the mode.
    """
    level = str(cfg["features"]["attempt_level"])
    det = detect_attempt_unit(attempts, int(cfg["features"]["attempt_restart_min_drill_names"]))
    unit_map = {(int(n), str(t)): str(u)
                for n, t, u in zip(det["nfl_id"], det["drill_type"], det["attempt_unit"])}
    if level != "empirical":
        assert level in ("drill_name", "drill_type"), f"unknown features.attempt_level: {level}"
        unit_map = {k: level for k in unit_map}
    return unit_map


def kinematics_for_attempt(t, x, y, params: dict) -> dict:
    """Per-attempt kinematics from x/y/time (observed frames only).

    Frames must be ordered by time. Returns per-attempt metrics, the sanity /
    gap flags, and the refined segment arrays (``seg_dt``, ``seg_dist``,
    ``seg_v``, ``seg_a``, ``seg_counted``) used for the t90 pass.

    Frame gaps: a segment is a *gap* when ``dt > gap_detect_factor*expected_dt``.
    Gaps inside the peak-speed segment or the peak-accel window are flagged and
    left RAW (never interpolated). ELSEWHERE, gaps shorter than
    ``interp_max_gap_s`` are linearly interpolated onto the expected grid; gaps
    >= ``interp_max_gap_s`` are NOT interpolated — their segment is excluded from
    distance/speed/accel and sets ``flag_large_gap``.
    """
    t = np.asarray(t, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = t.size
    res = {
        "n_frames": int(n),
        "n_gaps": np.nan,
        "max_gap_s": np.nan,
        "peak_speed_yds": np.nan,
        "peak_accel_yds2": np.nan,
        "effort_cost_yd": np.nan,
        "flag_gap_in_peak_speed_window": 0,
        "flag_gap_in_peak_accel_window": 0,
        "flag_large_gap": 0,
        "seg_dt": np.array([], dtype=np.float64),
        "seg_dist": np.array([], dtype=np.float64),
        "seg_v": np.array([], dtype=np.float64),
        "seg_a": np.array([], dtype=np.float64),
        "seg_counted": np.array([], dtype=bool),
    }
    if n < 2:
        return res

    exp = float(params["expected_dt_s"])
    gap_thr = exp * float(params["gap_detect_factor"])
    interp_max = float(params["interp_max_gap_s"])

    dt = np.diff(t)
    dist = np.hypot(np.diff(x), np.diff(y))
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(dt > 0, dist / dt, np.nan)
    a = np.full(dt.shape, np.nan, dtype=np.float64)
    if dt.size >= 2:
        denom = (dt[1:] + dt[:-1]) / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            a[1:] = np.where(denom > 0, (v[1:] - v[:-1]) / denom, np.nan)

    gap = dt > gap_thr
    res["n_gaps"] = int(gap.sum())
    res["max_gap_s"] = float(dt.max())

    fv = np.isfinite(v)
    ips = int(np.argmax(np.where(fv, v, -np.inf))) if fv.any() else -1
    fa = np.isfinite(a)
    ipa = int(np.argmax(np.where(fa, a, -np.inf))) if fa.any() else -1

    peak_segs: set[int] = set()
    accel_window: set[int] = set()
    if ips >= 0:
        peak_segs.add(ips)
    if ipa >= 0:
        accel_window.add(ipa)
        if ipa - 1 >= 0:
            accel_window.add(ipa - 1)
    res["flag_gap_in_peak_speed_window"] = int(ips >= 0 and bool(gap[ips]))
    res["flag_gap_in_peak_accel_window"] = int(
        len(accel_window) > 0 and any(bool(gap[k]) for k in accel_window)
    )

    # ---- rebuild the trajectory: interpolate acceptable gaps elsewhere ----
    peakwin = peak_segs | accel_window
    rt: list[float] = [float(t[0])]
    rx: list[float] = [float(x[0])]
    ry: list[float] = [float(y[0])]
    counted: list[bool] = [True]  # placeholder for point 0 (no segment)
    for k in range(dt.size):
        dk = float(dt[k])
        if dk >= interp_max:
            rt.append(float(t[k + 1])); rx.append(float(x[k + 1])); ry.append(float(y[k + 1]))
            counted.append(False)
            res["flag_large_gap"] = 1
        elif gap[k]:
            if k in peakwin:
                # never interpolate inside the peak window: keep the raw segment
                rt.append(float(t[k + 1])); rx.append(float(x[k + 1])); ry.append(float(y[k + 1]))
                counted.append(True)
            else:
                m = int(round(dk / exp)) if exp > 0 else 1
                if m <= 1:
                    rt.append(float(t[k + 1])); rx.append(float(x[k + 1])); ry.append(float(y[k + 1]))
                    counted.append(True)
                else:
                    for j in range(1, m):
                        frac = j / m
                        rt.append(float(t[k]) + frac * dk)
                        rx.append(float(x[k]) + frac * (float(x[k + 1]) - float(x[k])))
                        ry.append(float(y[k]) + frac * (float(y[k + 1]) - float(y[k])))
                        counted.append(True)
                    rt.append(float(t[k + 1])); rx.append(float(x[k + 1])); ry.append(float(y[k + 1]))
                    counted.append(True)
        else:
            rt.append(float(t[k + 1])); rx.append(float(x[k + 1])); ry.append(float(y[k + 1]))
            counted.append(True)

    rt_a = np.asarray(rt, dtype=np.float64)
    rx_a = np.asarray(rx, dtype=np.float64)
    ry_a = np.asarray(ry, dtype=np.float64)
    seg_counted = np.asarray(counted, dtype=bool)[1:]

    rdt = np.diff(rt_a)
    rdist = np.hypot(np.diff(rx_a), np.diff(ry_a))
    with np.errstate(divide="ignore", invalid="ignore"):
        rv = np.where(rdt > 0, rdist / rdt, np.nan)
    ra = np.full(rdt.shape, np.nan, dtype=np.float64)
    if rdt.size >= 2:
        rden = (rdt[1:] + rdt[:-1]) / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            ra[1:] = np.where(rden > 0, (rv[1:] - rv[:-1]) / rden, np.nan)

    # exclude uncounted segments from speed/accel/distance
    rv = np.where(seg_counted, rv, np.nan)
    valid = seg_counted & np.isfinite(rv)
    if ra.size:
        ok = valid.copy()
        ok[0] = False
        ok[1:] = ok[1:] & valid[:-1]
        ra = np.where(ok, ra, np.nan)

    fv2 = np.isfinite(rv)
    res["peak_speed_yds"] = float(np.max(rv[fv2])) if fv2.any() else np.nan
    fa2 = np.isfinite(ra)
    res["peak_accel_yds2"] = float(np.max(ra[fa2])) if fa2.any() else np.nan
    # effort counts only segments with dt <= distance_gap_max_s (post-interpolation)
    distmax = float(params["distance_gap_max_s"])
    res["effort_cost_yd"] = float(np.sum(rdist[seg_counted & (rdt <= distmax)]))
    res["seg_dt"] = rdt
    res["seg_dist"] = rdist
    res["seg_v"] = rv
    res["seg_a"] = ra
    res["seg_counted"] = seg_counted
    return res


# --------------------------------------------------------------------------- #
# Data loading (DuckDB over Parquet; explicit columns)
# --------------------------------------------------------------------------- #
def _frame_params(cfg: dict[str, Any]) -> dict[str, Any]:
    f = cfg["features"]
    return {
        "expected_dt_s": float(f["expected_dt_s"]),
        "gap_detect_factor": float(f["gap_detect_factor"]),
        "interp_max_gap_s": float(f["interp_max_gap_s"]),
        "distance_gap_max_s": float(f["distance_gap_max_s"]),
        "peak_speed_sanity_yds": float(f["peak_speed_sanity_yds"]),
    }


def load_frames(con, T: str) -> dict[str, np.ndarray]:
    """Fetch PLAYER frames (x/y/time + ids) ordered by (event_id, time)."""
    sql = f"""
        SELECT event_id, nfl_id, draft_year, drill_type, drill_name, attempt,
               epoch_ms(time) / 1000.0 AS t_s, x, y, s, a, dis
        FROM {T}
        WHERE entity_type = 'PLAYER'
        ORDER BY event_id, t_s
    """
    return con.execute(sql).fetchnumpy()


def compute_attempt_kinematics(frames: dict[str, np.ndarray], params: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Loop over observed attempts; return (attempt-level, segment-level) frames."""
    ev = frames["event_id"]
    nfl = frames["nfl_id"]
    dy = frames["draft_year"]
    dtyp = frames["drill_type"]
    dnam = frames["drill_name"]
    att = frames["attempt"]
    ts = frames["t_s"]
    xs = frames["x"]
    ys = frames["y"]

    uniq, starts, counts = np.unique(ev, return_index=True, return_counts=True)
    # np.unique returns first-occurrence indices; ev is sorted so groups are contiguous
    order = np.argsort(starts)
    uniq = uniq[order]
    starts = starts[order]
    counts = counts[order]
    ends = starts + counts

    att_rows: list[dict[str, Any]] = []
    seg_ev: list[np.ndarray] = []
    seg_ix: list[np.ndarray] = []
    seg_nfl: list[np.ndarray] = []
    seg_dt: list[np.ndarray] = []
    seg_di: list[np.ndarray] = []
    seg_v: list[np.ndarray] = []
    seg_a: list[np.ndarray] = []
    seg_co: list[np.ndarray] = []

    for g in range(uniq.size):
        a = int(starts[g]); b = int(ends[g])
        k = kinematics_for_attempt(ts[a:b], xs[a:b], ys[a:b], params)
        att_rows.append({
            "event_id": str(uniq[g]),
            "nfl_id": int(nfl[a]),
            "draft_year": int(dy[a]),
            "drill_type": str(dtyp[a]),
            "drill_name": str(dnam[a]),
            "attempt": int(att[a]),
            "n_frames": k["n_frames"],
            "n_gaps": k["n_gaps"],
            "max_gap_s": k["max_gap_s"],
            "peak_speed_yds": k["peak_speed_yds"],
            "peak_accel_yds2": k["peak_accel_yds2"],
            "effort_cost_yd": k["effort_cost_yd"],
            "flag_gap_in_peak_speed_window": k["flag_gap_in_peak_speed_window"],
            "flag_gap_in_peak_accel_window": k["flag_gap_in_peak_accel_window"],
            "flag_large_gap": k["flag_large_gap"],
            "flag_speed_outlier": int(
                np.isfinite(k["peak_speed_yds"])
                and k["peak_speed_yds"] > float(params["peak_speed_sanity_yds"])
            ),
        })
        ns = k["seg_dt"].size
        if ns:
            seg_ev.append(np.repeat(str(uniq[g]), ns))
            seg_ix.append(np.arange(ns, dtype=np.int32))
            seg_nfl.append(np.repeat(int(nfl[a]), ns))
            seg_dt.append(k["seg_dt"])
            seg_di.append(k["seg_dist"])
            seg_v.append(k["seg_v"])
            seg_a.append(k["seg_a"])
            seg_co.append(k["seg_counted"])

    attempt = pd.DataFrame(att_rows)
    if seg_ev:
        seg = pd.DataFrame({
            "event_id": np.concatenate(seg_ev),
            "seg_index": np.concatenate(seg_ix),
            "nfl_id": np.concatenate(seg_nfl),
            "dt_s": np.concatenate(seg_dt),
            "dist_yd": np.concatenate(seg_di),
            "v_yds": np.concatenate(seg_v),
            "a_yds2": np.concatenate(seg_a),
            "counted": np.concatenate(seg_co),
        })
    else:
        seg = pd.DataFrame(columns=["event_id", "seg_index", "nfl_id", "dt_s", "dist_yd", "v_yds", "a_yds2", "counted"])
    return attempt, seg


# --------------------------------------------------------------------------- #
# Stage: sessions / ordering / prior load / rest / imputation
# --------------------------------------------------------------------------- #
def _assign_sessions(obs: pd.DataFrame, gap_s: float) -> pd.Series:
    """session_id = f'{nfl_id}:{k}', k by start time, split on > gap_s inactivity."""
    out = pd.Series(index=obs.index, dtype="object")
    for nfl, grp in obs.sort_values(["nfl_id", "attempt_start_time"]).groupby("nfl_id", sort=True):
        t0 = grp["attempt_start_time"]
        gap = t0.diff().dt.total_seconds()
        k = (gap > gap_s).cumsum() + 1
        out.loc[grp.index] = [f"{int(nfl)}:{int(x)}" for x in k]
    return out


def _order_and_load(combined: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Within-(player, session) ordering + cumulative prior load + rest time.

    Numbering unit (D19) is column ``attempt_unit``: `drill_name` for per-`drill_name`
    restart blocks, else `drill_type`. Order key = (``unit_first_start_rank``, ``attempt``),
    where ``unit_first_start_rank`` = within-(player, session) rank of the unit's min
    observed start time (ties by unit key).
    """
    session_gap_s = float(cfg["features"]["session_gap_s"])
    combined = combined.copy()
    observed = combined["is_imputed"] == 0
    combined["attempt_start_time"] = pd.to_datetime(combined["attempt_start_time"])
    combined["attempt_end_time"] = pd.to_datetime(combined["attempt_end_time"])

    # ---- numbering-unit key (D19): drill_name for restart units, else drill_type ----
    combined["unit_key"] = np.where(
        combined["attempt_unit"].to_numpy() == "drill_name",
        combined["drill_name"].to_numpy(), combined["drill_type"].to_numpy())

    # ---- session assignment (from observed start times) ----
    obs = combined.loc[observed, ["nfl_id", "attempt_start_time"]].copy()
    obs = obs.sort_values(["nfl_id", "attempt_start_time"])
    obs["session_id"] = _assign_sessions(obs, session_gap_s)
    combined["session_id"] = obs["session_id"]

    # imputed rows inherit the session of their (nfl_id, unit_key)'s observed attempts
    key_session = (combined.loc[observed]
                   .groupby(["nfl_id", "unit_key"], sort=False)["session_id"].first())
    imp_mask = ~observed
    if imp_mask.any():
        keys = list(zip(combined.loc[imp_mask, "nfl_id"], combined.loc[imp_mask, "unit_key"]))
        combined.loc[imp_mask, "session_id"] = [key_session.get(kk, None) for kk in keys]
    combined = combined[combined["session_id"].notna()].copy()
    observed = (combined["is_imputed"] == 0).to_numpy()

    # ---- unit_first_start_rank (D19) per (player, session) ----
    firsts = (combined.loc[observed]
              .groupby(["nfl_id", "session_id", "unit_key"], sort=True)["attempt_start_time"]
              .min().reset_index())
    firsts["unit_first_start_rank"] = (firsts
                                       .sort_values(["nfl_id", "session_id", "attempt_start_time", "unit_key"])
                                       .groupby(["nfl_id", "session_id"]).cumcount() + 1)
    rank_map = firsts.set_index(["nfl_id", "session_id", "unit_key"])["unit_first_start_rank"]
    combined["unit_first_start_rank"] = [
        rank_map.get((int(n), s, u), np.nan)
        for n, s, u in zip(combined["nfl_id"], combined["session_id"], combined["unit_key"])
    ]

    # ---- order within session and cumulative prior load ----
    # Key = (unit_first_start_rank, attempt, is_imputed, event_id) per spec (D19).
    combined = combined.sort_values(
        ["nfl_id", "session_id", "unit_first_start_rank", "attempt", "is_imputed", "event_id"]).copy()

    # ---- first_attempt (D19): 1 iff OBSERVED and the player's earliest observed rep
    # of that drill_name by attempt_start_time (ties -> min attempt); imputed rows = 0.
    obs_sorted = (combined.loc[combined["is_imputed"] == 0]
                  .sort_values(["nfl_id", "drill_name", "attempt_start_time", "attempt"]))
    first_idx = obs_sorted.groupby(["nfl_id", "drill_name"], sort=False).head(1).index
    combined["first_attempt"] = 0
    combined.loc[first_idx, "first_attempt"] = 1

    eff = combined["effort_cost_yd"].fillna(0.0)
    cum = eff.groupby(combined["session_id"], sort=False).cumsum()
    combined["prior_load_yd"] = cum - eff
    combined["prior_load_efforts"] = combined.groupby("session_id", sort=False).cumcount()
    obs_eff = pd.Series(np.where(combined["is_imputed"] == 0, eff.to_numpy(), 0.0), index=combined.index)
    combined["prior_load_observed_yd"] = obs_eff.groupby(combined["session_id"], sort=False).cumsum() - obs_eff
    obs_ind = (combined["is_imputed"] == 0).astype(int)
    combined["prior_load_observed_efforts"] = (
        obs_ind.groupby(combined["session_id"], sort=False).cumsum() - obs_ind)

    # ---- elapsed session time (observed only) ----
    obs_bool = combined["is_imputed"] == 0
    sess_start = combined.loc[obs_bool].groupby("session_id")["attempt_start_time"].min()
    combined["elapsed_session_s"] = np.where(
        obs_bool.to_numpy(),
        (combined["attempt_start_time"] - combined["session_id"].map(sess_start)).dt.total_seconds(),
        np.nan,
    )

    # ---- rest time (from the clock; accounting for imputed) ----
    combined["rest_s"] = np.nan
    combined["rest_spans_imputed"] = 0
    combined["order_index"] = np.arange(len(combined))
    for _, idx in combined.groupby("session_id", sort=False).groups.items():
        sub = combined.loc[idx]
        obs_rows = sub.loc[sub["is_imputed"] == 0].sort_values("attempt_start_time")
        imp_positions = set(sub.loc[sub["is_imputed"] == 1, "order_index"].tolist())
        prev_end = None
        prev_pos = None
        for _, row in obs_rows.iterrows():
            pos = int(row["order_index"])
            if prev_end is None:
                combined.at[row.name, "rest_s"] = np.nan
                combined.at[row.name, "rest_spans_imputed"] = 1  # session-first: no predecessor
            else:
                combined.at[row.name, "rest_s"] = float((row["attempt_start_time"] - prev_end).total_seconds())
                spans = any(prev_pos < p < pos for p in imp_positions)
                combined.at[row.name, "rest_spans_imputed"] = int(spans)
            prev_end = row["attempt_end_time"]
            prev_pos = pos
    combined = combined.drop(columns=["order_index"])
    return combined


def _impute_rows(attempts: pd.DataFrame, cfg: dict[str, Any],
                 unit_map: dict[tuple[int, str], str]) -> pd.DataFrame:
    """Lost-attempt (numbering-gap) rows: load only, flagged, no performance.

    The grouping (numbering) unit is per ``(nfl_id, drill_type)`` block and is chosen
    empirically per D19 (``unit_map`` from `resolve_attempt_units`): `drill_name` for
    per-`drill_name` restart blocks, else `drill_type`. Missing numbers
    ``m in {1..max(attempt)} \\ observed`` within the unit become rows with
    ``is_imputed=1``; `drill_name` of the imputed row = that of the nearest observed
    rep (largest ``attempt < m``, else smallest ``attempt > m``).
    """
    stat = str(cfg["features"].get("impute_stat", "median"))
    assert stat == "median", f"only impute_stat=median is implemented (got {stat})"
    rows: list[dict[str, Any]] = []
    # global (unit-level) medians (fallback) across all players
    med_dn = attempts.groupby("drill_name")["effort_cost_yd"].median()
    med_dt = attempts.groupby("drill_type")["effort_cost_yd"].median()
    for (nfl, dtyp), block in attempts.groupby(["nfl_id", "drill_type"], sort=True):
        unit = unit_map.get((int(nfl), str(dtyp)), "drill_type")
        if unit == "drill_name":
            groups = [(None, g) for _, g in block.groupby("drill_name", sort=True)]
        else:
            groups = [(None, block)]  # whole block = one numbering unit
        for _, grp in groups:
            have = grp["attempt"].astype(int).tolist()
            mx = int(max(have))
            for m in missing_attempt_numbers(have, mx):
                player_med = grp["effort_cost_yd"].median()
                if np.isfinite(player_med):
                    cost, src = float(player_med), "player_drill"
                else:
                    if unit == "drill_name":
                        g = med_dn.get(grp["drill_name"].iloc[0], np.nan)
                    else:
                        g = med_dt.get(str(dtyp), np.nan)
                    cost, src = (float(g) if np.isfinite(g) else np.nan), "drill_global"
                rows.append({
                    "event_id": None,
                    "nfl_id": int(nfl),
                    "draft_year": int(grp["draft_year"].iloc[0]),
                    "combine_position": grp["combine_position"].iloc[0],
                    "drill_type": grp["drill_type"].iloc[0],
                    "drill_name": _imputed_drill_name(grp, m),
                    "attempt": int(m),
                    "attempt_unit": unit,
                    "is_imputed": 1,
                    "effort_cost_yd": cost,
                    "impute_source": src,
                    "n_frames": np.nan, "n_gaps": np.nan, "max_gap_s": np.nan,
                    "peak_speed_yds": np.nan, "peak_accel_yds2": np.nan, "t90_s": np.nan,
                    "best_speed_yds": np.nan,
                    "flag_gap_in_peak_speed_window": 0, "flag_gap_in_peak_accel_window": 0,
                    "flag_large_gap": 0, "flag_speed_outlier": 0,
                    "flag_duplicate_attempt": 0, "flag_out_of_order": 0,
                    "flag_std_group_too_small": 0,
                    "advisory_provided_dis_sum": np.nan,
                    "advisory_provided_peak_s": np.nan,
                    "advisory_provided_peak_a": np.nan,
                    "attempt_start_time": pd.NaT, "attempt_end_time": pd.NaT,
                    "elapsed_session_s": np.nan, "rest_s": np.nan, "rest_spans_imputed": 0,
                })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Stage: standardisation
# --------------------------------------------------------------------------- #
def _standardize(obs: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Within (standardize_level, position) z-scores over observed attempts."""
    level = str(cfg["features"]["standardize_level"])
    min_n = int(cfg["features"]["min_std_group_n"])
    primary = str(cfg["features"]["performance_metric"])
    zmap = dict(Z_COLUMN)
    zmap[primary] = "perf_z"  # the configured primary metric maps to perf_z
    obs = obs.copy()
    obs["flag_std_group_too_small"] = 0
    params_rows: list[dict[str, Any]] = []
    for metric in METRICS:
        zcol = zmap[metric]
        obs[zcol] = np.nan
        for (lvl, pos), idx in obs.groupby([level, "combine_position"], sort=True).groups.items():
            vals = obs.loc[idx, metric].to_numpy()
            z, ok = zscore_group(vals, min_n)
            obs.loc[idx, zcol] = z
            if not ok:
                obs.loc[idx, "flag_std_group_too_small"] = 1
            finite = vals[np.isfinite(vals)]
            params_rows.append({
                "metric": metric,
                "standardize_level": level,
                "standardize_level_value": lvl,
                "position": pos,
                "n": int(finite.size),
                "mean": float(finite.mean()) if finite.size else np.nan,
                "sd": float(finite.std(ddof=0)) if finite.size else np.nan,
                "min_std_group_n": min_n,
                "group_ok": int(ok),
            })
    return obs, pd.DataFrame(params_rows)


# --------------------------------------------------------------------------- #
# Stage: VIF
# --------------------------------------------------------------------------- #
def _vif(study_obs: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    thr = float(cfg["features"]["vif_flag_threshold"])
    d = study_obs[["prior_load_yd", "first_attempt"]].dropna()
    n = int(len(d))
    rows: list[dict[str, Any]] = []
    if n < 3 or d["prior_load_yd"].nunique() < 2 or d["first_attempt"].nunique() < 2:
        return pd.DataFrame([{
            "term": t, "vif": np.nan, "exceeds_threshold": 0, "n_obs": n,
            "vif_flag_threshold": thr,
        } for t in ["const", "prior_load_yd", "first_attempt"]] + [{
            "term": "corr(prior_load_yd,first_attempt)", "vif": np.nan,
            "exceeds_threshold": 0, "n_obs": n, "vif_flag_threshold": thr,
        }])
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    X = np.column_stack([np.ones(n), d["prior_load_yd"].to_numpy(), d["first_attempt"].to_numpy()])
    for i, term in enumerate(["const", "prior_load_yd", "first_attempt"]):
        v = float(variance_inflation_factor(X, i))
        rows.append({"term": term, "vif": v, "exceeds_threshold": int(v > thr),
                     "n_obs": n, "vif_flag_threshold": thr})
    corr = float(np.corrcoef(d["prior_load_yd"], d["first_attempt"])[0, 1])
    rows.append({"term": "corr(prior_load_yd,first_attempt)", "vif": corr,
                 "exceeds_threshold": 0, "n_obs": n, "vif_flag_threshold": thr})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def _cast_select(columns: list[tuple[str, str]]) -> str:
    parts = []
    for name, typ in columns:
        if typ in ("INTEGER", "FLOAT"):
            parts.append(f'TRY_CAST("{name}" AS {typ}) AS "{name}"')
        else:
            parts.append(f'CAST("{name}" AS {typ}) AS "{name}"')
    return ",\n  ".join(parts)


def _write_parquet(con, df: pd.DataFrame, columns: list[tuple[str, str]], path: Path, order_by: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    view = "_feat_view"
    con.register(view, df[[c for c, _ in columns]])
    sql = (f"COPY (SELECT\n  {_cast_select(columns)}\n FROM {view}\n ORDER BY {order_by}) "
           f"TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(sql)
    con.unregister(view)


def _labelled_csv(df: pd.DataFrame, prov: str, path: Path) -> None:
    out = df.copy()
    out.insert(0, "provenance", prov)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #
def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    prov = provenance(cfg, mode)
    out_dir = resolve(cfg, "outputs", cfg["features"]["out_subdir"])
    feat_path = out_dir / "combine_features.parquet"

    logger.info("=" * 72)
    logger.info("BDB27 Phase 2 features | mode=%s | provenance=%s", mode, prov)
    logger.info("=" * 72)

    if feat_path.exists() and not force:
        logger.info("checkpoint hit: %s exists (use --force to rebuild)", feat_path.name)
        append_run_log(cfg, "02_features:checkpoint", mode, 0.0, peak_ram_mb(), "",
                       {"skipped": True, "reason": "combine_features.parquet exists; use --force"})
        return 0

    parq = resolve(cfg, "parquet")
    threads = int(cfg.get("runtime", {}).get("duckdb_threads", 1))
    con = connect(threads=threads)
    params = _frame_params(cfg)
    study_pop = str(cfg["audit"]["study_population"])
    t90_level = str(cfg["features"]["t90_baseline_level"])
    t90_frac = float(cfg["features"]["t90_frac"])

    with timed_stage(cfg, "02_features:convert", mode):
        to_parquet(con, csv_path(cfg, "combine_tracking"), parq / "combine_tracking.parquet",
                   COMBINE_TRACKING_COLS, force, logger)
        to_parquet(con, csv_path(cfg, "combine_results"), parq / "combine_results.parquet",
                   COMBINE_RESULTS_COLS, force, logger)
    T = pq(parq / "combine_tracking.parquet")
    R = pq(parq / "combine_results.parquet")

    # ---- position map + advisory + duplicate/out-of-order flags ----
    pos_map = (con.execute(f"SELECT nfl_id, any_value(combine_position) FROM {R} GROUP BY 1")
               .fetchdf().set_index("nfl_id")["any_value(combine_position)"].to_dict())
    advisory = (con.execute(f"""
        SELECT event_id, sum(dis) AS dis_sum, max(s) AS peak_s, max(a) AS peak_a
        FROM {T} WHERE entity_type='PLAYER' GROUP BY 1""").fetchdf().set_index("event_id"))
    dup_tuples = set(con.execute(f"""
        WITH g AS (SELECT nfl_id, drill_name, attempt, count(DISTINCT event_id) k
                   FROM {T} WHERE entity_type='PLAYER' GROUP BY 1,2,3)
        SELECT nfl_id, drill_name, attempt FROM g WHERE k > 1""").fetchall())
    ooo_tuples = set(con.execute(f"""
        WITH a AS (SELECT nfl_id, drill_name, attempt, min(time) t0
                   FROM {T} WHERE entity_type='PLAYER' GROUP BY 1,2,3),
             b AS (SELECT nfl_id, drill_name, attempt,
                          lag(attempt) OVER (PARTITION BY nfl_id, drill_name ORDER BY t0) pa
                   FROM a)
        SELECT DISTINCT nfl_id, drill_name FROM b
        WHERE pa IS NOT NULL AND attempt <= pa""").fetchall())
    se = con.execute(f"""SELECT event_id, min(time) AS st, max(time) AS en
                         FROM {T} WHERE entity_type='PLAYER' GROUP BY 1""").fetchdf().set_index("event_id")

    # ---- kinematics ----
    with timed_stage(cfg, "02_features:kinematics", mode):
        frames = load_frames(con, T)
        attempt, seg = compute_attempt_kinematics(frames, params)
        attempt["combine_position"] = attempt["nfl_id"].map(pos_map)
        attempt["attempt_start_time"] = attempt["event_id"].map(se["st"])
        attempt["attempt_end_time"] = attempt["event_id"].map(se["en"])
        attempt["advisory_provided_dis_sum"] = attempt["event_id"].map(advisory["dis_sum"])
        attempt["advisory_provided_peak_s"] = attempt["event_id"].map(advisory["peak_s"])
        attempt["advisory_provided_peak_a"] = attempt["event_id"].map(advisory["peak_a"])
        attempt["flag_duplicate_attempt"] = [
            int((int(n), d, int(a)) in dup_tuples) for n, d, a in zip(attempt["nfl_id"], attempt["drill_name"], attempt["attempt"])]
        attempt["flag_out_of_order"] = [
            int((int(n), d) in ooo_tuples) for n, d in zip(attempt["nfl_id"], attempt["drill_name"])]
        # best speed = player x baseline-level max observed peak speed
        best = attempt.groupby(["nfl_id", t90_level])["peak_speed_yds"].max().rename("best_speed_yds")
        attempt = attempt.merge(best, left_on=["nfl_id", t90_level], right_index=True, how="left")
        # t90 = sum dt over counted segments with v >= frac * best
        seg2 = seg.merge(attempt[["event_id", "nfl_id", t90_level, "best_speed_yds"]],
                         left_on=["event_id", "nfl_id"], right_on=["event_id", "nfl_id"], how="left")
        thr = t90_frac * seg2["best_speed_yds"]
        hit = seg2["counted"] & seg2["v_yds"].notna() & (seg2["v_yds"] >= thr) & seg2["best_speed_yds"].notna()
        t90 = seg2.loc[hit].groupby("event_id")["dt_s"].sum().rename("t90_s")
        attempt = attempt.merge(t90, left_on="event_id", right_index=True, how="left")
        attempt["t90_s"] = attempt["t90_s"].fillna(0.0)
        attempt.loc[~attempt["best_speed_yds"].notna(), "t90_s"] = np.nan
        attempt["is_imputed"] = 0
        attempt["impute_source"] = ""

    # ---- imputation + sessions/ordering/load/rest ----
    with timed_stage(cfg, "02_features:impute_load", mode):
        # D19: detect the attempt-numbering unit per (nfl_id, drill_type) block
        unit_map = resolve_attempt_units(attempt, cfg)
        attempt["attempt_unit"] = [
            unit_map.get((int(n), str(t)), "drill_type")
            for n, t in zip(attempt["nfl_id"], attempt["drill_type"])
        ]
        imputed = _impute_rows(attempt, cfg, unit_map)
        combined = pd.concat([attempt, imputed], ignore_index=True, sort=False)
        combined["in_study_population"] = (combined["combine_position"] == study_pop)
        combined = _order_and_load(combined, cfg)

    # ---- standardisation (observed only) ----
    with timed_stage(cfg, "02_features:standardize", mode):
        obs_mask = combined["is_imputed"] == 0
        obs_s, std_params = _standardize(combined.loc[obs_mask], cfg)
        for c in ["perf_z", "peak_accel_z", "t90_z", "effort_cost_z", "flag_std_group_too_small"]:
            combined[c] = np.nan
        combined.loc[obs_mask, ["perf_z", "peak_accel_z", "t90_z", "effort_cost_z", "flag_std_group_too_small"]] = \
            obs_s[["perf_z", "peak_accel_z", "t90_z", "effort_cost_z", "flag_std_group_too_small"]].to_numpy()
        combined["flag_std_group_too_small"] = combined["flag_std_group_too_small"].fillna(0).astype(int)

    # ---- VIF ----
    with timed_stage(cfg, "02_features:vif", mode):
        study_obs = combined[(combined["in_study_population"]) & (combined["is_imputed"] == 0)]
        vif = _vif(study_obs, cfg)

    # ---- write ----
    with timed_stage(cfg, "02_features:write", mode):
        combined["provenance"] = prov
        combined["first_attempt"] = combined["first_attempt"].astype(int)
        for c in ["flag_gap_in_peak_speed_window", "flag_gap_in_peak_accel_window", "flag_speed_outlier",
                  "flag_duplicate_attempt", "flag_out_of_order", "flag_large_gap"]:
            combined[c] = combined[c].fillna(0).astype(int)
        _write_parquet(con, combined, COLUMNS, feat_path,
                       '"nfl_id", "drill_name", "attempt", "is_imputed", "event_id" NULLS LAST')
        study_df = combined[combined["in_study_population"]].copy()
        _write_parquet(con, study_df, COLUMNS, out_dir / "combine_features_study.parquet",
                       '"nfl_id", "drill_name", "attempt", "is_imputed", "event_id" NULLS LAST')

        # per-player summary
        g = combined.groupby("nfl_id", sort=True)
        player = pd.DataFrame({
            "provenance": prov,
            "nfl_id": g["nfl_id"].first().astype(int),
            "draft_year": g["draft_year"].first().astype(int),
            "combine_position": g["combine_position"].first(),
            "in_study_population": g["in_study_population"].first(),
            "n_attempts_total": g.size(),
            "n_attempts_observed": g["is_imputed"].apply(lambda s: int((s == 0).sum())),
            "n_attempts_imputed": g["is_imputed"].apply(lambda s: int((s == 1).sum())),
            "n_sessions": g["session_id"].nunique(),
            "n_drill_types": g["drill_type"].nunique(),
            "n_drill_names": g["drill_name"].nunique(),
            "max_peak_speed_yds": g["peak_speed_yds"].max(),
            "total_effort_yd_observed": combined.loc[combined["is_imputed"] == 0]
                .groupby("nfl_id")["effort_cost_yd"].sum(),
            "first_session_start": g["attempt_start_time"].min(),
            "last_attempt_end": g["attempt_end_time"].max(),
        }).reset_index(drop=True)
        _write_parquet(con, player, PLAYER_COLUMNS, out_dir / "combine_features_player.parquet",
                       '"in_study_population" DESC, "nfl_id"')

        # ---- diagnostics (long key/value) ----
        diag = _diagnostics(combined, study_obs, vif, cfg, prov, study_pop)
        if int(dict(zip(diag["metric"], diag["value"])).get("attempt_numbering_restart_warning", 0)):
            logger.warning(
                "ATTEMPT-NUMBERING WARNING: imputed rows (%d) exceed %.0f%% of observed rows. "
                "The detected numbering unit may be wrong (D19); re-check "
                "features.attempt_level / attempt_restart_min_drill_names and notes/decisions.md.",
                int((combined["is_imputed"] == 1).sum()),
                100.0 * float(cfg["features"].get("impute_warn_share", 0.5)),
            )
        _labelled_csv(diag, prov, out_dir / "feature_diagnostics.csv")
        sp = std_params.copy()
        sp.insert(0, "provenance", prov)
        out_dir.mkdir(parents=True, exist_ok=True)
        sp.to_csv(out_dir / "standardization_params.csv", index=False)
        vif_out = vif.copy()
        vif_out.insert(0, "provenance", prov)
        vif_out.to_csv(out_dir / "vif_report.csv", index=False)

        (out_dir / "PROVENANCE.txt").write_text(
            f"{prov}\nGenerated {mode}-mode by src/02_features.py. Combine features, not results.\n",
            encoding="utf-8")
        (out_dir / "SUMMARY.md").write_text(_summary(combined, study_obs, vif, diag, cfg, prov, study_pop, mode),
                                            encoding="utf-8")

    logger.info("wrote %s (%d rows total, %d study)", feat_path.name, len(combined), len(study_df))
    logger.info("peak RAM: %.1f MB", peak_ram_mb())
    return 0


def _diagnostics(combined, study_obs, vif, cfg, prov, study_pop) -> pd.DataFrame:
    obs = combined[combined["is_imputed"] == 0]
    rest_obs = obs[obs["rest_s"].notna()]
    corr_rest = np.nan
    if len(rest_obs) >= 3 and rest_obs["prior_load_yd"].nunique() >= 2 and rest_obs["rest_s"].nunique() >= 2:
        corr_rest = float(np.corrcoef(rest_obs["rest_s"], rest_obs["prior_load_yd"])[0, 1])
    load = study_obs["prior_load_yd"]
    # ---- numbering-unit sanity (D19): imputation volume by draft_year + detected units ----
    # The attempt-numbering unit is detected empirically per (nfl_id, drill_type) block
    # (D19); imputed rows are generated at that unit. Detect and REPORT (non-fatal).
    imp_by_year = (combined[combined["is_imputed"] == 1].groupby("draft_year").size()
                   .reindex([2023, 2024, 2025], fill_value=0))
    _blocks = combined.drop_duplicates(["nfl_id", "drill_type"])
    _unit_counts = _blocks["attempt_unit"].value_counts()
    warn_share = float(cfg["features"].get("impute_warn_share", 0.5))
    n_obs = int((combined["is_imputed"] == 0).sum())
    n_imp = int((combined["is_imputed"] == 1).sum())
    numbering_warn = int(n_obs > 0 and (n_imp / n_obs) > warn_share)
    rows = [
        ("rows_total", len(combined)),
        ("rows_observed", int((combined["is_imputed"] == 0).sum())),
        ("rows_imputed", int((combined["is_imputed"] == 1).sum())),
        ("rows_study_population", int(combined["in_study_population"].sum())),
        ("rows_study_observed", len(study_obs)),
        ("n_players", combined["nfl_id"].nunique()),
        ("n_players_study", combined.loc[combined["in_study_population"], "nfl_id"].nunique()),
        ("n_sessions", combined["session_id"].nunique()),
        ("n_drill_types", combined["drill_type"].nunique()),
        ("n_drill_names", combined["drill_name"].nunique()),
        ("imputed_source_player_drill", int((combined["impute_source"] == "player_drill").sum())),
        ("imputed_source_drill_global", int((combined["impute_source"] == "drill_global").sum())),
        ("flag_gap_in_peak_speed_window_n", int(combined["flag_gap_in_peak_speed_window"].sum())),
        ("flag_gap_in_peak_accel_window_n", int(combined["flag_gap_in_peak_accel_window"].sum())),
        ("flag_speed_outlier_n", int(combined["flag_speed_outlier"].sum())),
        ("flag_duplicate_attempt_n", int(combined["flag_duplicate_attempt"].sum())),
        ("flag_out_of_order_n", int(combined["flag_out_of_order"].sum())),
        ("flag_large_gap_n", int(combined["flag_large_gap"].sum())),
        ("flag_std_group_too_small_n", int(combined["flag_std_group_too_small"].sum())),
        ("imputed_rows_2023", int(imp_by_year.get(2023, 0))),
        ("imputed_rows_2024", int(imp_by_year.get(2024, 0))),
        ("imputed_rows_2025", int(imp_by_year.get(2025, 0))),
        ("imputed_share_of_observed", round(n_imp / n_obs, 4) if n_obs else np.nan),
        ("impute_warn_share", warn_share),
        ("attempt_numbering_restart_warning", numbering_warn),
        ("attempt_unit_blocks_drill_name", int(_unit_counts.get("drill_name", 0))),
        ("attempt_unit_blocks_drill_type", int(_unit_counts.get("drill_type", 0))),
        ("rest_first_observed_n", int(obs["rest_s"].isna().sum())),
        ("rest_spans_imputed_n", int(obs["rest_spans_imputed"].sum())),
        ("rest_spans_imputed_in_between_n", int(((obs["rest_spans_imputed"] == 1) & obs["rest_s"].notna()).sum())),
        ("rest_s_p50", float(rest_obs["rest_s"].median()) if len(rest_obs) else np.nan),
        ("rest_s_min", float(rest_obs["rest_s"].min()) if len(rest_obs) else np.nan),
        ("rest_s_max", float(rest_obs["rest_s"].max()) if len(rest_obs) else np.nan),
        ("corr_rest_s_prior_load_yd", corr_rest),
        ("rest_use", str(cfg["features"]["rest_use"])),
        ("study_population", study_pop),
        ("prior_load_study_p50", float(load.median()) if len(load) else np.nan),
        ("prior_load_study_max", float(load.max()) if len(load) else np.nan),
        ("prior_load_study_sd", float(load.std(ddof=1)) if load.notna().sum() > 1 else np.nan),
        ("prior_load_study_n_unique", int(load.nunique())),
        ("first_attempt_study_share", float(study_obs["first_attempt"].mean()) if len(study_obs) else np.nan),
        ("vif_prior_load_yd", float(vif.loc[vif["term"] == "prior_load_yd", "vif"].iloc[0])),
        ("vif_first_attempt", float(vif.loc[vif["term"] == "first_attempt", "vif"].iloc[0])),
        ("vif_flag_threshold", float(cfg["features"]["vif_flag_threshold"])),
    ]
    return pd.DataFrame(rows, columns=["metric", "value"])


def _summary(combined, study_obs, vif, diag, cfg, prov, study_pop, mode) -> str:
    d = dict(zip(diag["metric"], diag["value"]))
    vif_load = vif.loc[vif["term"] == "prior_load_yd", "vif"].iloc[0]
    vif_first = vif.loc[vif["term"] == "first_attempt", "vif"].iloc[0]
    lines = [
        f"# Combine features summary — {prov}",
        "",
        f"- mode: `{mode}`  ·  provenance: `{prov}`",
        f"- attempts: {d['rows_total']} total ({d['rows_observed']} observed + {d['rows_imputed']} imputed); "
        f"{d['n_players']} players; {d['n_sessions']} sessions; {d['n_drill_types']} drill_types",
        f"- study population `{study_pop}`: {d['rows_study_observed']} observed attempt rows "
        f"({d['n_players_study']} players); full subset {d['rows_study_population']} rows",
        f"- standardised within (`{cfg['features']['standardize_level']}`, position); primary metric "
        f"`{cfg['features']['performance_metric']}` (t90 baseline level `{cfg['features']['t90_baseline_level']}`)",
        f"- flags: gap-in-peak-speed {d['flag_gap_in_peak_speed_window_n']}, "
        f"gap-in-peak-accel {d['flag_gap_in_peak_accel_window_n']}, speed-outlier {d['flag_speed_outlier_n']}, "
        f"duplicate-attempt {d['flag_duplicate_attempt_n']}, out-of-order {d['flag_out_of_order_n']}, "
        f"large-gap {d['flag_large_gap_n']}, std-group-too-small {d['flag_std_group_too_small_n']}",
        f"- imputation: player_drill {d['imputed_source_player_drill']}, drill_global {d['imputed_source_drill_global']} "
        f"(by year 2023/2024/2025 = {d['imputed_rows_2023']}/{d['imputed_rows_2024']}/{d['imputed_rows_2025']}; "
        f"share of observed {d['imputed_share_of_observed']})",
        f"- numbering-level warning (imputed share > {d['impute_warn_share']}): "
        f"{'YES — see notes/blockers.md' if d['attempt_numbering_restart_warning'] else 'no'}",
        f"- rest_s: POLICY `{d['rest_use']}` — kept as a column and reported "
        f"(corr(rest_s, prior_load_yd)={d['corr_rest_s_prior_load_yd']}); NOT in the pre-registered "
        f"Phase-3 primary LMM, declared a Phase-3 robustness covariate (USED, not ignored)",
        f"- VIF: prior_load_yd={vif_load}, first_attempt={vif_first} "
        f"(flag threshold {cfg['features']['vif_flag_threshold']})",
        "",
        "*(All CSVs carry a `provenance` first column; parquet tables carry a `provenance` column. "
        "Sample outputs are UNVALIDATED SAMPLE OUTPUT.)*",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 2 — Combine features")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild outputs (ignore checkpoint)")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or cfg["mode"]["default"]
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
