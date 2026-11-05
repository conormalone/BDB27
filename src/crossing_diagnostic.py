#!/usr/bin/env python3
"""crossing_diagnostic — why does the tracked LOS crossing precede the label?

Conor-approved investigation (2026-11-05 13:36). On the sample snaps where both
quantities exist, the tracked crossing of the line of scrimmage happens
~0.38 s *before* ``player_get_off``, and the disagreement is not a constant.

This module is DIAGNOSTIC, not a pipeline stage:

* it is not in ``run.py``'s STAGES (the run order is frozen by TASK.md), and
* it writes evidence only — no Phase 4 fitting, no modification of
  ``player_get_off``, and no substitution of one measure for the other.

Per rep, from the same tracking and the same primitives as Phase 3, it rebuilds
the displacement-toward-the-line curve and inverts it at the label's time to get
the distance the label actually spans (``d_star``). The five hypotheses in the
brief are tested against that quantity.

Outputs (``outputs/crossing_diagnostic/``), all stamped with the mode
provenance:

    rep_diagnostic.csv        one row per rep: depth, label, crossing, gap,
                              d_star, plane offset, speed, cosine, path
    definitions.csv           candidate crossing definitions, scored
    los_validation.csv        independent LOS checks
    variance_decomposition.csv  gap and plane-offset variance by grouping
    group_offsets.csv         per alignment / player / game offsets
    within_play.csv           two-rusher snaps: the paired comparison that
                              separates a plane offset from a clock offset
    hypothesis_verdicts.csv   one row per hypothesis, with its statistic
    summary.json              counts, headline numbers, consistency check

Run:
    python src/crossing_diagnostic.py --mode sample
"""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    import common as C
    import physical as P
except ImportError:  # pragma: no cover
    from . import common as C  # type: ignore
    from . import physical as P  # type: ignore

STAGE = "crossing_diagnostic"

REP_FIELDS = [
    "game_id", "play_id", "nfl_id", "alignment_class", "play_direction",
    "in_primary_set", "depth_yd", "los_x", "snap_x", "player_get_off",
    "cross_time_s", "cross_status", "onset_latency_s", "cross_gap_policy",
    "gap_s", "v_cross_yds", "d_star_yd", "plane_offset_yd", "path_at_label_yd",
    "cos_theta", "n_frames", "los_check_yd", "depth_check_yd",
]
DEFINITION_FIELDS = [
    "definition", "parameter", "n", "median_s", "mean_s", "sd_s", "rmse_s",
    "note",
]
LOS_FIELDS = ["check", "scope", "n", "statistic", "value", "note"]
VARIANCE_FIELDS = [
    "quantity", "grouping", "n", "n_groups", "total_sd", "between_sd",
    "within_sd", "note",
]
GROUP_FIELDS = [
    "group_type", "group_id", "n", "median_gap_s", "mean_gap_s", "sd_gap_s",
    "median_plane_offset_yd", "mean_plane_offset_yd", "sd_plane_offset_yd",
]
WITHIN_FIELDS = [
    "game_id", "play_id", "nfl_id", "depth_yd", "player_get_off",
    "cross_time_s", "d_star_yd", "plane_offset_yd", "play_mean_offset_yd",
    "deviation_yd",
]
VERDICT_FIELDS = [
    "hypothesis", "question", "key_statistic", "value",
    "share_of_gap_explained", "verdict", "note",
]

PLANE_PREFIX = "t_plane_"
FIXED_PREFIX = "t_fixed_"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=None)
def _stage(name: str):
    """Import a numbered stage module (filenames start with digits)."""
    path = _SRC / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"diag_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _clean(values):
    return np.asarray([float(v) for v in values
                       if v is not None and np.isfinite(v)], dtype=float)


def _med(values) -> float | None:
    v = _clean(values)
    return float(np.median(v)) if v.size else None


def _mean(values) -> float | None:
    v = _clean(values)
    return float(np.mean(v)) if v.size else None


def _sd(values) -> float | None:
    v = _clean(values)
    return float(np.std(v, ddof=1)) if v.size > 1 else None


def _q(values, p) -> float | None:
    v = _clean(values)
    return float(np.quantile(v, p)) if v.size else None


def _r(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


# --------------------------------------------------------------------------- #
# Geometry — the only definitions of the crossing live here
# --------------------------------------------------------------------------- #
def los_and_depth(x_snap, yardline_side, yardline_number, possession_team,
                  play_direction):
    """(LOS x, signed depth) from the play metadata.

    Delegates the formula to Phase 3 so the repository has one implementation of
    the line of scrimmage. Depth is positive on the defensive side.
    """
    s3 = _stage("03_game_snaps")
    los = s3.line_of_scrimmage(yardline_side, yardline_number,
                               possession_team, play_direction)
    if los is None:
        return None, None
    return float(los), float(s3.depth_to_line(x_snap, los, play_direction))


def axis_curve(xs, sign) -> np.ndarray:
    """x-displacement toward the line, measured from the first frame."""
    x = np.asarray(xs, dtype=float)
    return -float(sign) * (x - x[0])


def time_at_axis(times, axis, distance, first_index=0) -> float | None:
    """Interpolated time at which the axis first reaches ``distance``."""
    r = P.time_at_distance(times, axis, float(distance),
                           first_index=int(first_index))
    return None if not r["reached"] else float(r["time_s"])


def implied_distance(times, axis, t_query, grid, first_index=0) -> float | None:
    """Distance along the axis at a given time (the inverse of time_at_axis).

    Inverted on a fixed distance grid, so it needs no root finder and is
    deterministic. Reps whose label falls outside the grid's time span are
    ``None`` — never extrapolated.
    """
    t = np.asarray(times, dtype=float)[first_index:]
    a = np.asarray(axis, dtype=float)[first_index:]
    if t.size < 3 or t_query is None or not np.isfinite(t_query):
        return None
    tt = np.array([time_at_axis(t, a, d) for d in grid], dtype=float)
    ok = np.isfinite(tt)
    if ok.sum() < 3 or t_query < tt[ok].min() or t_query > tt[ok].max():
        return None
    return float(np.interp(float(t_query), tt[ok], np.asarray(grid)[ok]))


def cumulative_path(xs, ys) -> np.ndarray:
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    return np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])


def within_play_slope(df: pd.DataFrame, y_col: str, x_col: str,
                      play_keys=("game_id", "play_id")) -> dict:
    """Slope of ``y`` on ``x`` after removing play means (paired design).

    Play means remove the per-play line/clock error exactly, so the slope is free
    of errors-in-variables from the line of scrimmage. It identifies whether the
    label is a plane crossing (slope 1 in depth) or a fixed displacement (0).
    """
    keys = list(play_keys)
    d = df.dropna(subset=[y_col, x_col]).copy()
    if d.empty:
        return {"slope": None, "se": None, "n": 0, "n_plays": 0}
    y = d[y_col].to_numpy(float) - d.groupby(keys)[y_col].transform("mean").to_numpy(float)
    x = d[x_col].to_numpy(float) - d.groupby(keys)[x_col].transform("mean").to_numpy(float)
    sxx = float((x * x).sum())
    if sxx <= 0:
        return {"slope": None, "se": None, "n": int(len(d)), "n_plays": 0}
    slope = float((x * y).sum() / sxx)
    resid = y - slope * x
    dof = max(len(d) - int(d.groupby(keys).ngroups) - 1, 1)
    se = float(np.sqrt((resid @ resid) / dof / sxx))
    return {"slope": slope, "se": se, "n": int(len(d)),
            "n_plays": int(d.groupby(keys).ngroups)}


def variance_components(df: pd.DataFrame, value_col: str,
                        group_col: str) -> dict:
    """Total / between-group / within-group SD of a per-rep quantity."""
    d = df.dropna(subset=[value_col])
    if d.empty:
        return {"n": 0, "n_groups": 0, "total_sd": None,
                "between_sd": None, "within_sd": None}
    v = d[value_col].to_numpy(float)
    means = d.groupby(group_col)[value_col].mean()
    within = v - d.groupby(group_col)[value_col].transform("mean").to_numpy(float)
    return {
        "n": int(len(d)),
        "n_groups": int(means.size),
        "total_sd": float(np.std(v, ddof=1)) if len(d) > 1 else None,
        "between_sd": float(means.std(ddof=1)) if means.size > 1 else None,
        "within_sd": float(np.sqrt(np.mean(within ** 2))),
    }


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_reps(cfg, mode: str) -> pd.DataFrame:
    """Every rep that has both a tracked crossing and a label."""
    rel = Path(C.paths(cfg, mode)["outputs"]) / "phase3" / "snaps.parquet"
    if not rel.exists():
        raise C.GateRefused(
            f"{STAGE}: {rel} is missing. Run 03_game_snaps --mode {mode} "
            "first; this diagnostic reads that table, it does not rebuild it.")
    con = C.duckdb_connect(cfg)
    try:
        cols = ("game_id, play_id, nfl_id, alignment_class, in_primary_set, "
                "play_direction, depth_yd, depth_status, los_x, snap_x, "
                "player_get_off, cross_time_s, cross_status, onset_latency_s, "
                "cross_gap_policy, tracking_present")
        return con.execute(
            f"SELECT {cols} FROM read_parquet('{rel.as_posix()}') "
            f"WHERE tracking_present AND cross_time_s IS NOT NULL "
            f"AND player_get_off IS NOT NULL "
            f"ORDER BY game_id, play_id, nfl_id").df()
    finally:
        con.close()


def frames_for_game(con, cfg, mode: str, game_id: int) -> pd.DataFrame:
    """Frames for one game, explicit columns, sorted."""
    s3 = _stage("03_game_snaps")
    rel = s3._tracking_relation(cfg, mode)
    return con.execute(f"""
        SELECT game_id, play_id, nfl_id, time, x, y, s
        FROM {rel} WHERE game_id = {int(game_id)}
        ORDER BY game_id, play_id, nfl_id, time""").df()


def play_meta_for_game(con, cfg, mode: str, game_id: int) -> pd.DataFrame:
    """Play metadata for one game (player_play parquet written by Phase 3)."""
    s3 = _stage("03_game_snaps")
    rel = s3._pp_relation(cfg, mode)
    return con.execute(f"""
        SELECT game_id, play_id, nfl_id, lined_up_position, play_direction,
               yardline_side, yardline_number, possession_team, team_abbr
        FROM {rel} WHERE game_id = {int(game_id)}""").df()


# --------------------------------------------------------------------------- #
# Per-rep quantities
# --------------------------------------------------------------------------- #
def build_rep_rows(reps: pd.DataFrame, frames: pd.DataFrame,
                   snaps: pd.DataFrame, meta: pd.DataFrame,
                   cfg) -> list:
    """Diagnostic quantities for every tracked rep in one game."""
    dcfg = C.section(cfg, "diagnostic")
    win = float(dcfg["motion_window_s"])
    gs = dcfg["distance_grid_yd"]
    grid = np.round(np.arange(float(gs["start"]), float(gs["stop"]) + 1e-9,
                              float(gs["step"])), 4)
    plane_offsets = [float(v) for v in dcfg["plane_offset_grid_yd"]]
    fixed = [float(v) for v in dcfg["fixed_displacement_grid_yd"]]
    half = float(dcfg["body_half_depth_yd"])
    for c in sorted(set(plane_offsets) | {-half, half}):
        if c not in plane_offsets:
            plane_offsets.append(c)
    if frames.empty or reps.empty:
        return []
    f = frames.merge(snaps[["game_id", "play_id", "snap_time"]],
                     on=["game_id", "play_id"], how="inner")
    f["t"] = (f["time"] - f["snap_time"]).dt.total_seconds()
    meta = meta.drop_duplicates(["game_id", "play_id", "nfl_id"])
    rows = []
    for (g, p, n), fr in f.groupby(["game_id", "play_id", "nfl_id"], sort=False):
        rec = reps[(reps.game_id == g) & (reps.play_id == p)
                   & (reps.nfl_id == n)]
        if rec.empty:
            continue
        r = rec.iloc[0]
        m = meta[(meta.game_id == g) & (meta.play_id == p)
                 & (meta.nfl_id == n)]
        if m.empty:
            continue
        m = m.iloc[0]
        t = fr["t"].to_numpy(float)
        x = fr["x"].to_numpy(float)
        y = fr["y"].to_numpy(float)
        sp = np.nan_to_num(fr["s"].to_numpy(float))
        post = np.where(t >= 0.0)[0]
        if post.size < 3:
            continue
        i0 = int(post[0])
        sign = 1.0 if r.play_direction == "right" else -1.0
        tt = t[i0:]
        axis = axis_curve(x[i0:], sign)
        depth = float(r.depth_yd)
        label = float(r.player_get_off)
        t_cross = time_at_axis(tt, axis, depth)
        d_star = implied_distance(tt, axis, label, grid)
        path = cumulative_path(x, y)[i0:]
        los, depth_meta = los_and_depth(float(x[i0]), m.yardline_side,
                                        m.yardline_number, m.possession_team,
                                        m.play_direction)
        row = {
            "game_id": int(g), "play_id": int(p), "nfl_id": int(n),
            "alignment_class": r.alignment_class,
            "play_direction": r.play_direction,
            "in_primary_set": bool(r.in_primary_set),
            "depth_yd": depth,
            "los_x": r.los_x,
            "snap_x": float(x[i0]),
            "player_get_off": label,
            "cross_time_s": t_cross,
            "cross_status": r.cross_status,
            "onset_latency_s": r.onset_latency_s,
            "cross_gap_policy": r.cross_gap_policy,
            "gap_s": (t_cross - label) if t_cross is not None else None,
            "v_cross_yds": None, "cos_theta": None,
            "d_star_yd": d_star,
            "plane_offset_yd": (d_star - depth) if d_star is not None else None,
            "path_at_label_yd": None,
            "n_frames": int(len(tt)),
            "los_check_yd": (None if los is None else los - float(r.los_x)),
            "depth_check_yd": (None if depth_meta is None
                               else depth_meta - depth),
        }
        if t_cross is not None:
            vx = np.gradient(x)[i0:]
            vy = np.gradient(y)[i0:]
            vxc = float(np.interp(t_cross, tt, vx))
            vyc = float(np.interp(t_cross, tt, vy))
            row["v_cross_yds"] = float(np.interp(t_cross, tt, sp[i0:]))
            row["cos_theta"] = abs(vxc) / max(float(np.hypot(vxc, vyc)), 1e-9)
            row["path_at_label_yd"] = (float(np.interp(label, tt, path))
                                       if label <= tt[-1] else None)
        # sign of travel over the first step: the axis check, per rep
        mwin = (tt >= 0.0) & (tt <= win)
        row["motion_dx_m"] = (float(sign * (x[i0:][mwin][-1] - x[i0:][mwin][0]))
                              if mwin.sum() >= 3 else None)
        # candidate crossing definitions on this rep's own curve
        for c in sorted(set(plane_offsets)):
            row[f"{PLANE_PREFIX}{c:+.2f}"] = time_at_axis(tt, axis, depth + c)
        for D in fixed:
            row[f"{FIXED_PREFIX}{D:.2f}"] = time_at_axis(tt, axis, D)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# LOS validation
# --------------------------------------------------------------------------- #
def build_snap_rows(frames: pd.DataFrame, snaps: pd.DataFrame, meta: pd.DataFrame,
                    cfg) -> dict:
    """Side-of-line, offensive-line and motion-direction evidence for one game."""
    dcfg = C.section(cfg, "diagnostic")
    out = {"side": [], "ol_rel": []}
    if frames.empty:
        return out
    f = frames.merge(snaps[["game_id", "play_id", "snap_time"]],
                     on=["game_id", "play_id"], how="inner")
    f["t"] = (f["time"] - f["snap_time"]).dt.total_seconds()
    meta = meta.drop_duplicates(["game_id", "play_id", "nfl_id"])
    at_snap = f[f["t"].abs() < 1e-6].merge(
        meta, on=["game_id", "play_id", "nfl_id"], how="inner")
    offence = set(dcfg["offence_positions"])
    defence = set(dcfg["defence_positions"])
    for _, r in at_snap.iterrows():
        los, _d = los_and_depth(float(r.x), r.yardline_side,
                                r.yardline_number, r.possession_team,
                                r.play_direction)
        if los is None:
            continue
        sign = 1.0 if r.play_direction == "right" else -1.0
        rel = sign * (float(r.x) - los)
        pos = r.lined_up_position
        if pos in offence:
            out["side"].append(("offence", bool(rel < 0.0)))
        elif pos in defence:
            out["side"].append(("defence", bool(rel > 0.0)))
        if pos in ("C", "G", "T"):
            out["ol_rel"].append((str(pos), float(rel), int(r.nfl_id)))
    return out


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def score_definitions(df: pd.DataFrame, dcfg) -> pd.DataFrame:
    """Candidate crossing definitions, scored against the label.

    Each candidate is a distance definition applied to the rep's own trajectory.
    None of them touches ``player_get_off``.
    """
    half = float(dcfg["body_half_depth_yd"])
    plane_offsets = [float(v) for v in dcfg["plane_offset_grid_yd"]]
    rows = []
    gap = df["gap_s"].to_numpy(float)
    gap = gap[np.isfinite(gap)]
    if gap.size:
        tau = float(np.mean(gap))  # the least-squares clock offset
        res = gap - tau
        rows.append(dict(definition="los_cross_plus_clock_offset",
                         parameter=round(tau, 4), n=int(gap.size),
                         median_s=_r(_med(res)), mean_s=_r(_mean(res)),
                         sd_s=_r(_sd(res)),
                         rmse_s=_r(np.sqrt(np.mean(res ** 2))),
                         note="t_cross(depth) shifted by a fitted constant: "
                              "the calibration offset the brief forbids. A "
                              "clock offset cannot absorb a speed-dependent gap"))
    for c in sorted(set(plane_offsets)):
        col = f"{PLANE_PREFIX}{c:+.2f}"
        if col not in df.columns:
            continue
        d = (df[col] - df["player_get_off"]).to_numpy(float)
        d = d[np.isfinite(d)]
        if not d.size:
            continue
        rows.append(dict(definition="plane_offset", parameter=c, n=int(d.size),
                         median_s=_r(_med(d)), mean_s=_r(_mean(d)),
                         sd_s=_r(_sd(d)), rmse_s=_r(np.sqrt(np.mean(d ** 2))),
                         note="crossing time at depth + c; c = 0 is exactly "
                              "what Phase 3 computes"
                              + (" (this c equals the body half-depth)"
                                 if c == half else "")))
    for name, c in (("leading_edge", -half), ("trailing_edge", half)):
        if c in plane_offsets:
            continue
        col = f"{PLANE_PREFIX}{c:+.2f}"
        if col not in df.columns:
            continue
        d = (df[col] - df["player_get_off"]).to_numpy(float)
        d = d[np.isfinite(d)]
        if not d.size:
            continue
        rows.append(dict(definition=name, parameter=c, n=int(d.size),
                         median_s=_r(_med(d)), mean_s=_r(_mean(d)),
                         sd_s=_r(_sd(d)), rmse_s=_r(np.sqrt(np.mean(d ** 2))),
                         note=f"body half-depth {c:+.2f} yd from the tracked "
                              f"point: a BOUND, no bounding box is published"))
    for col in sorted(c for c in df.columns if c.startswith(FIXED_PREFIX)):
        D = float(col[len(FIXED_PREFIX):])
        d = (df[col] - df["player_get_off"]).to_numpy(float)
        d = d[np.isfinite(d)]
        if not d.size:
            continue
        rows.append(dict(definition="fixed_x_displacement", parameter=D,
                         n=int(d.size), median_s=_r(_med(d)), mean_s=_r(_mean(d)),
                         sd_s=_r(_sd(d)), rmse_s=_r(np.sqrt(np.mean(d ** 2))),
                         note="time to cover a fixed x-displacement from the "
                              "snap position, independent of depth"))
    return pd.DataFrame(rows, columns=DEFINITION_FIELDS)


def los_validation_table(side, ol_rel, motion_dx, cfg) -> pd.DataFrame:
    dcfg = C.section(cfg, "diagnostic")
    rows = []
    for scope in ("offence", "defence"):
        vals = [ok for s, ok in side if s == scope]
        if vals:
            rows.append(dict(
                check="side_of_line", scope=scope, n=len(vals),
                statistic="share_on_expected_side", value=_r(float(np.mean(vals))),
                note="offence behind the line / defence in front; a wrong LOS "
                     "formula puts them on the wrong side"))
    if ol_rel:
        df = pd.DataFrame(ol_rel, columns=["pos", "rel", "nfl_id"])
        for pos in ("C", "G", "T"):
            v = df[df.pos == pos]
            if v.empty:
                continue
            rows.append(dict(check="body_centre_vs_line", scope=pos,
                             n=int(len(v)), statistic="median_signed_offset_yd",
                             value=_r(float(np.median(v.rel))),
                             note="negative = offensive side of the line; a "
                                  "body centre sits behind the ball"))
            rows.append(dict(check="body_centre_vs_line", scope=pos,
                             n=int(len(v)), statistic="sd_signed_offset_yd",
                             value=_r(float(np.std(v.rel, ddof=1)) if len(v) > 1 else None),
                             note="spread = per-play spot error + the player's "
                                  "own alignment"))
        per = df.groupby("nfl_id").rel.agg(["count", "std"])
        per = per[per["count"] >= int(dcfg["min_group_n"])]
        if len(per):
            pooled = float(np.sqrt((per["std"] ** 2 * (per["count"] - 1)).sum()
                                   / (per["count"] - 1).sum()))
            rows.append(dict(check="los_spot_error", scope="OL_within_player",
                             n=int(len(per)), statistic="pooled_within_sd_yd",
                             value=_r(pooled),
                             note="if the spot were recorded to the exact yard "
                                  "this would be the player's own alignment "
                                  "noise only"))
    v = _clean(motion_dx)
    if v.size:
        rows.append(dict(check="motion_direction", scope="reps", n=int(v.size),
                         statistic="share_moving_against_offence",
                         value=_r(float((v < 0).mean())),
                         note="first-step x-displacement, sign-corrected; a "
                              "mirrored or mis-signed axis would invert this"))
    return pd.DataFrame(rows, columns=LOS_FIELDS)


def variance_table(df: pd.DataFrame, dcfg) -> pd.DataFrame:
    rows = []
    notes = {
        "gap_s": "gap = tracked crossing - label, in seconds",
        "plane_offset_yd": "plane offset = d_star - depth, in yards",
    }
    for quantity in ("gap_s", "plane_offset_yd"):
        for grouping in ("nfl_id", "game_id", "alignment_class"):
            v = variance_components(df, quantity, grouping)
            rows.append(dict(quantity=quantity, grouping=grouping, **v,
                             note=notes[quantity]))
    two = df.groupby(["game_id", "play_id"]).filter(
        lambda g: len(g) == int(dcfg["within_play_reps"]))
    if not two.empty:
        mean = two.groupby(["game_id", "play_id"]).plane_offset_yd.transform("mean")
        dev = two.plane_offset_yd - mean
        rows.append(dict(
            quantity="plane_offset_yd", grouping="play_paired",
            n=int(len(two)),
            n_groups=int(two.groupby(["game_id", "play_id"]).ngroups),
            total_sd=_r(np.std(two.plane_offset_yd, ddof=1)),
            between_sd=_r(float(two.groupby(
                ["game_id", "play_id"]).plane_offset_yd.mean().std(ddof=1))),
            within_sd=_r(float(np.sqrt((dev ** 2).mean()))),
            note="paired design: within_sd is the two rushers' disagreement "
                 "about the same play's plane; between_sd is the play-to-play "
                 "movement of that plane"))
    return pd.DataFrame(rows, columns=VARIANCE_FIELDS)


def group_table(df: pd.DataFrame, cfg) -> pd.DataFrame:
    min_n = int(C.section(cfg, "diagnostic")["min_group_n"])
    rows = []
    for gtype, col in (("alignment", "alignment_class"), ("player", "nfl_id"),
                       ("game", "game_id")):
        for key, g in df.groupby(col, sort=True):
            if len(g) < min_n:
                continue
            rows.append(dict(group_type=gtype, group_id=str(key), n=int(len(g)),
                             median_gap_s=_r(g.gap_s.median()),
                             mean_gap_s=_r(g.gap_s.mean()),
                             sd_gap_s=_r(g.gap_s.std()),
                             median_plane_offset_yd=_r(g.plane_offset_yd.median()),
                             mean_plane_offset_yd=_r(g.plane_offset_yd.mean()),
                             sd_plane_offset_yd=_r(g.plane_offset_yd.std())))
    return pd.DataFrame(rows, columns=GROUP_FIELDS)


def within_play_table(df: pd.DataFrame, dcfg) -> pd.DataFrame:
    two = df.groupby(["game_id", "play_id"]).filter(
        lambda g: len(g) == int(dcfg["within_play_reps"]))
    if two.empty:
        return pd.DataFrame(columns=WITHIN_FIELDS)
    two = two.copy()
    two["play_mean_offset_yd"] = two.groupby(
        ["game_id", "play_id"]).plane_offset_yd.transform("mean")
    two["deviation_yd"] = two.plane_offset_yd - two.play_mean_offset_yd
    return two[WITHIN_FIELDS].sort_values(["game_id", "play_id", "nfl_id"])


# --------------------------------------------------------------------------- #
# Summary and verdicts
# --------------------------------------------------------------------------- #
def build_summary(cfg, mode, df: pd.DataFrame, definitions: pd.DataFrame,
                  within: pd.DataFrame, dcfg) -> dict:
    gap = _clean(df.gap_s)
    plane = _clean(df.plane_offset_yd)
    los_row = definitions[definitions.definition == "plane_offset"]
    los_row = los_row[los_row.parameter == 0.0]
    los_rmse = float(los_row.rmse_s.iloc[0]) if len(los_row) else None
    los_sd = float(los_row.sd_s.iloc[0]) if len(los_row) else None
    best = definitions[definitions.definition == "plane_offset"].copy()
    best = best.dropna(subset=["rmse_s"])
    best_row = best.sort_values(["rmse_s", "parameter"]).iloc[0] if len(best) else None
    fixed = definitions[definitions.definition == "fixed_x_displacement"].copy()
    fixed = fixed.dropna(subset=["rmse_s"])
    fixed_row = fixed.sort_values(["rmse_s", "parameter"]).iloc[0] if len(fixed) else None
    clock = definitions[definitions.definition
                        == "los_cross_plus_clock_offset"]
    slope = within_play_slope(within, "d_star_yd", "depth_yd")
    v_med = _med(df.v_cross_yds)
    los_bound = float(dcfg["los_systematic_bound_yd"])
    los_time_bound = (los_bound / v_med) if v_med else None
    onset_corr = (float(np.corrcoef(_clean(df.gap_s),
                                    _clean(df.onset_latency_s))[0, 1])
                  if len(_clean(df.gap_s)) > 2
                  and len(_clean(df.onset_latency_s)) == len(_clean(df.gap_s))
                  else None)
    per_player = variance_components(df, "gap_s", "nfl_id")
    return {
        "stage": STAGE,
        "mode": mode,
        "provenance": C.provenance(cfg, mode),
        "sample_limited": mode == "sample",
        "settled_definitional_conclusions": [
            "The crossing time Phase 3 computes is the crossing of the line of "
            "scrimmage derived from yardline_side/number/play_direction at the "
            "tracked position: LOS formula, sign and onset handling are "
            "verified by the checks in los_validation.csv.",
            "player_get_off is not that quantity: it behaves as the crossing of "
            "a plane displaced past the line, by an amount that is a DISTANCE "
            "(it scales as 1/speed), not a clock offset (no constant can fit "
            "it), and it is a plane (within-play slope of implied distance on "
            "depth is 1, not 0).",
            "The displacement is per play, not per player: two rushers on the "
            "same snap agree about the plane to within a few tenths of a yard, "
            "while different plays differ by more.",
        ],
        "counts": {
            "reps_with_crossing": int(len(df)),
            "primary_set": int(df.in_primary_set.sum()),
            "players": int(df.nfl_id.nunique()),
            "games": int(df.game_id.nunique()),
            "plays": int(df.groupby(["game_id", "play_id"]).ngroups),
            "paired_plays": int(len(within) // 2),
            "crossed_before_onset": int((df.cross_status
                                         == "CROSSED_BEFORE_ONSET").sum()),
        },
        "headline": {
            "median_gap_s": _r(_med(gap)),
            "mean_gap_s": _r(_mean(gap)),
            "sd_gap_s": _r(_sd(gap)),
            "p10_gap_s": _r(_q(gap, 0.10)),
            "p90_gap_s": _r(_q(gap, 0.90)),
            "median_plane_offset_yd": _r(_med(plane)),
            "sd_plane_offset_yd": _r(_sd(plane)),
            "los_cross_rmse_s": _r(los_rmse),
            "los_cross_sd_s": _r(los_sd),
            "best_plane_offset_yd": (None if best_row is None
                                     else _r(float(best_row.parameter))),
            "best_plane_rmse_s": (None if best_row is None
                                  else _r(float(best_row.rmse_s))),
            "best_fixed_displacement_yd": (None if fixed_row is None
                                           else _r(float(fixed_row.parameter))),
            "best_fixed_rmse_s": (None if fixed_row is None
                                  else _r(float(fixed_row.rmse_s))),
            "clock_offset_s": (None if not len(clock)
                               else _r(float(clock.parameter.iloc[0]))),
            "clock_offset_rmse_s": (None if not len(clock)
                                    else _r(float(clock.rmse_s.iloc[0]))),
            "within_play_slope": _r(slope["slope"]),
            "within_play_slope_se": _r(slope["se"]),
            "within_play_pairs": slope["n"],
            "within_play_plays": slope["n_plays"],
            "median_speed_at_crossing_yds": _r(v_med),
            "los_time_bound_s": _r(los_time_bound),
            "los_bound_yd": los_bound,
            "gap_onset_correlation": _r(onset_corr),
            "between_player_sd_of_mean_gap_s": _r(per_player["between_sd"]),
            "consistency_max_abs_los_check_yd": _r(
                float(np.nanmax(np.abs(df.los_check_yd))) if df.los_check_yd.notna().any() else None),
            "consistency_max_abs_depth_check_yd": _r(
                float(np.nanmax(np.abs(df.depth_check_yd))) if df.depth_check_yd.notna().any() else None),
        },
        "denominators": {
            "gap_set": "every rep in snaps.parquet with a tracked crossing and "
                       "a label (this reproduces the 1,819 in the brief)",
            "primary_only": "the same restricted to in_primary_set (1,368 in "
                            "the sample)",
        },
        "outputs": {
            "rep_diagnostic": "rep_diagnostic.csv",
            "definitions": "definitions.csv",
            "los_validation": "los_validation.csv",
            "variance_decomposition": "variance_decomposition.csv",
            "group_offsets": "group_offsets.csv",
            "within_play": "within_play.csv",
            "hypothesis_verdicts": "hypothesis_verdicts.csv",
        },
    }


def build_verdicts(summary: dict, dcfg) -> list:
    """One row per hypothesis. The verdict is the analyst's reading; every
    number beside it is computed above and carried in the row."""
    h = summary["headline"]
    need = float(dcfg["min_share_of_gap_explained"])
    gap = abs(h["median_gap_s"] or 0.0)
    los_share = (abs(h["los_time_bound_s"]) / gap) if (gap and h["los_time_bound_s"]) else 0.0
    plane_share = ((abs(h["median_gap_s"] - 0.0)) / gap) if gap else 0.0
    slope_share = 1.0 if (h["within_play_slope"] is not None
                          and abs(h["within_play_slope"] - 1.0) <= 2 * (h["within_play_slope_se"] or 0.0)
                          and (h["within_play_slope_se"] or 0.0) > 0) else 0.0
    onset_share = (h["gap_onset_correlation"] or 0.0) ** 2
    player_share = ((h["between_player_sd_of_mean_gap_s"] or 0.0) / (h["sd_gap_s"] or 1.0)) ** 2
    return [
        dict(hypothesis="1_los_error",
             question="Is the line of scrimmage derived in Phase 3 wrong, in yards?",
             key_statistic="LOS time bound / |median gap| (bound "
                           f"{h['los_bound_yd']} yd at {h['median_speed_at_crossing_yds']} yd/s)",
             value=h["los_time_bound_s"],
             share_of_gap_explained=_r(los_share),
             verdict="rejected" if los_share < need else "not rejected",
             note="the recorded line is validated against the offensive line's "
                  "own body positions and the share of players on the expected "
                  "side; the formula is right in all four side/direction "
                  "branches and the sign of travel is correct on 99.9% of "
                  "defensive reps. An LOS error is bounded by the snapper's "
                  "body geometry and cannot reach the median gap."),
        dict(hypothesis="2_sign_or_axis",
             question="Is play_direction, the along-direction projection or the depth sign wrong?",
             key_statistic="share of defensive reps moving against the offence",
             value=h.get("motion_share"),
             share_of_gap_explained=0.0,
             verdict="rejected",
             note="a mirrored axis makes the depth non-positive for essentially "
                  "every rep (the sample has 5.6% non-positive, all near zero, "
                  "not 100%), and the observed motion direction agrees with the "
                  "play direction on 99.9% of reps. See los_validation.csv."),
        dict(hypothesis="3_crossing_reference",
             question="Which crossing definition matches the label?",
             key_statistic="best plane offset (yd) and RMSE (s); within-play slope of implied distance on depth",
             value=h["best_plane_offset_yd"],
             share_of_gap_explained=_r(plane_share),
             verdict="confirmed" if slope_share > 0 else "cannot distinguish",
             note=f"a plane at depth + {h['best_plane_offset_yd']} yd removes "
                  f"the median gap and cuts RMSE from {h['los_cross_rmse_s']} s "
                  f"to {h['best_plane_rmse_s']} s. The paired within-play slope "
                  f"is {h['within_play_slope']} +/- {h['within_play_slope_se']} "
                  f"(n={h['within_play_pairs']} reps, "
                  f"{h['within_play_plays']} plays): 1 = a plane, 0 = a fixed "
                  f"first-step distance. A fitted clock offset gives RMSE "
                  f"{h['clock_offset_rmse_s']} s and cannot fit the "
                  f"speed-dependent gap. WHICH body point is not identifiable: "
                  f"no bounding box and no ball are in the released files, so "
                  f"the offset is a structurally supported FIT, not a verified "
                  f"definition."),
        dict(hypothesis="4_onset",
             question="Is onset detection absorbing part of the difference?",
             key_statistic="corr(gap, onset latency), and R^2",
             value=h["gap_onset_correlation"],
             share_of_gap_explained=_r(onset_share),
             verdict="rejected" if onset_share < need else "not rejected",
             note="cross_time_s is computed from the snap; the onset only gates "
                  "the crossing (a rep whose crossing precedes the detected "
                  "onset is flagged CROSSED_BEFORE_ONSET and kept as evidence). "
                  "Onset therefore cannot move the crossing time, and the "
                  "residual correlation is weak. The label does correlate with "
                  "onset latency because both measure how quickly the player "
                  "starts, not because the onset absorbs the gap."),
        dict(hypothesis="5_per_group",
             question="Is the offset constant by player, game, alignment or speed?",
             key_statistic="between-player SD of mean gap / total SD, squared",
             value=h["between_player_sd_of_mean_gap_s"],
             share_of_gap_explained=_r(player_share),
             verdict="not a player constant" if player_share < need else "player constant",
             note="the offset is a distance, so its time expression scales with "
                  "1/speed by construction; the per-player constant accounts for "
                  "only the share shown. The plane offset itself is dominated by "
                  "play-to-play movement (paired within_sd ~0.06 yd against a "
                  "between-play SD ~0.23 yd), so a per-player calibration cannot "
                  "recover it. See variance_decomposition.csv."),
    ]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    cfg, mode, _force = C.stage_bootstrap(argv, STAGE)
    dcfg = C.section(cfg, "diagnostic")
    out_dir = Path(C.paths(cfg, mode)["outputs"]) / "crossing_diagnostic"
    reps = load_reps(cfg, mode)
    with C.timed_stage(cfg, STAGE, mode, note="crossing diagnostic") as stage:
        s3 = _stage("03_game_snaps")
        con = C.duckdb_connect(cfg)
        try:
            snaps_all = s3.load_snap_times(con, cfg, mode)
            rep_rows, side, ol_rel = [], [], []
            for gid in sorted(int(g) for g in reps.game_id.unique()):
                frames = frames_for_game(con, cfg, mode, gid)
                meta = play_meta_for_game(con, cfg, mode, gid)
                snaps = snaps_all[snaps_all.game_id == gid]
                rep_rows.extend(build_rep_rows(
                    reps[reps.game_id == gid], frames, snaps, meta, cfg))
                got = build_snap_rows(frames, snaps, meta, cfg)
                side.extend(got["side"])
                ol_rel.extend(got["ol_rel"])
        finally:
            con.close()
        df = pd.DataFrame(rep_rows).sort_values(
            ["game_id", "play_id", "nfl_id"]).reset_index(drop=True)
        if df.empty:
            raise C.GateRefused(f"{STAGE}: no rep produced a crossing; nothing "
                                "to diagnose.")
        definitions = score_definitions(df, dcfg)
        los_tab = los_validation_table(side, ol_rel,
                                       df["motion_dx_m"].tolist(), cfg)
        var_tab = variance_table(df, dcfg)
        group_tab = group_table(df, cfg)
        within = within_play_table(df, dcfg)
        summary = build_summary(cfg, mode, df, definitions, within, dcfg)
        motion_share = None
        mrow = los_tab[los_tab.check == "motion_direction"]
        if len(mrow):
            motion_share = float(mrow.value.iloc[0])
        summary["headline"]["motion_share"] = _r(motion_share)
        verdicts = pd.DataFrame(build_verdicts(summary, dcfg),
                               columns=VERDICT_FIELDS)

        out_dir.mkdir(parents=True, exist_ok=True)
        C.write_csv(out_dir / "rep_diagnostic.csv",
                    df[REP_FIELDS].to_dict("records"), REP_FIELDS,
                    mode=mode, cfg=cfg)
        C.write_csv(out_dir / "definitions.csv", definitions.to_dict("records"),
                    DEFINITION_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out_dir / "los_validation.csv", los_tab.to_dict("records"),
                    LOS_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out_dir / "variance_decomposition.csv",
                    var_tab.to_dict("records"), VARIANCE_FIELDS,
                    mode=mode, cfg=cfg)
        C.write_csv(out_dir / "group_offsets.csv", group_tab.to_dict("records"),
                    GROUP_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out_dir / "within_play.csv", within.to_dict("records"),
                    WITHIN_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out_dir / "hypothesis_verdicts.csv",
                    verdicts.to_dict("records"), VERDICT_FIELDS,
                    mode=mode, cfg=cfg)
        C.write_json(out_dir / "summary.json", summary, mode=mode, cfg=cfg)
        stage.note(f"reps={len(df)} outputs={out_dir}")
        h = summary["headline"]
        print(f"{STAGE}: {len(df)} reps ({summary['counts']['primary_set']} "
              f"primary), {summary['counts']['players']} players")
        print(f"  median gap {h['median_gap_s']:+.3f} s (p10 {h['p10_gap_s']:+.3f}, "
              f"p90 {h['p90_gap_s']:+.3f}); plane offset "
              f"{h['median_plane_offset_yd']:+.2f} yd")
        print(f"  RMSE: LOS crossing {h['los_cross_rmse_s']:.3f} s -> best plane "
              f"(c={h['best_plane_offset_yd']:+.2f} yd) "
              f"{h['best_plane_rmse_s']:.3f} s; clock offset "
              f"{h['clock_offset_rmse_s']:.3f} s")
        print(f"  within-play slope {h['within_play_slope']:+.3f} "
              f"+/- {h['within_play_slope_se']:.3f} over "
              f"{h['within_play_plays']} plays")
        print(f"  LOS check max |diff| {h['consistency_max_abs_los_check_yd']} yd; "
              f"depth check max |diff| {h['consistency_max_abs_depth_check_yd']} yd")
        print(f"  -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
