#!/usr/bin/env python3
"""dl_phase3 — Phase 3 derived columns for the IDR spec (TASK.md, 2026-11-04).

The three columns the spec's Phase 3 requires, computed **additively** on top
of the existing snap table:

``inter_snap_rest_s``
    Seconds between the **last recorded timestamp of the team's previous
    defensive play** and the snap of the current play.  Play-team level: it is
    computed once per ``(game_id, defensive team, play_id)`` and joined to
    every rusher on that play, so two rushers on the same play cannot disagree.
    The first own snap of a player-game is ``NA`` (counted, never imputed).
    If the player's previous own snap belongs to an earlier team play than the
    one immediately before this snap (``rest_gap_plays > max_gap_plays``), rest
    is ``NA``: the interval would have to be imputed across snaps we did not
    observe.  ``rest_sequence_complete`` carries the per-player-game flag.

``rolling_kinematic_load_w_per_kg``
    Sum over the prior ``n`` included snaps (config ``phase3.load.window_snaps``)
    of ``integral(a * v dt)`` over each rep.  With ``a`` in m/s^2 and ``v`` in
    m/s the integrand is mechanical power per unit mass: **W/kg**
    (m^2/s^3).  Tracking gives yd/s and yd/s^2, so the yard-space integral is
    multiplied by ``yard_to_metre**2`` (0.9144^2 = 0.83612736).  The per-rep
    value is emitted too: ``rep_kinematic_load_w_per_kg``.

``deficit_ratio``
    ``a0_snap_yds2 / baseline_a0_yds2`` — the current snap's fitted initial
    acceleration as a fraction of the player's shrunken Combine ``a0`` (the
    Phase-2 ceiling table).  Below 1 is a deficit.  ``baseline_a0_se`` is
    carried so downstream can propagate the baseline's uncertainty.

The snap acceleration is **fitted**, not twice-differentiated:
``v(t) = vmax * (1 - exp(-t/tau))`` fitted to the post-snap speed trace, with
``a0_snap = vmax / tau``.  ``a0_snap_raw_yds2`` keeps the cheap raw
double-difference as a clearly-labelled robustness column only.

Coverage is never assumed.  Every row carries ``rows_rusher_tracked``,
``frames_used``, ``fit_ok``, ``rep_too_short``, and an ``exclusion_reason``
when it contributes nothing to the load; the summary block reports the totals
and shares for the whole table.

Binding spec: ``TASK.md``.  Decisions: ``notes/decisions.md`` D80.
"""

from __future__ import annotations

import importlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from dl import dl_common as D

__all__ = ["PHASE3_COLUMNS", "PHASE3_TEXT_COLUMNS", "add_phase3_columns",
           "fit_rep", "power_integral"]

#: Columns appended to ``dl_snaps.SNAP_COLUMNS`` (declared order).
PHASE3_COLUMNS: tuple[str, ...] = (
    "inter_snap_rest_s",
    "rest_gap_plays",
    "rest_sequence_complete",
    "rep_kinematic_load_w_per_kg",
    "rolling_kinematic_load_w_per_kg",
    "load_window_n",
    "rows_rusher_tracked",
    "frames_used",
    "a0_snap_yds2",
    "tau_snap_s",
    "fit_ok",
    "fit_at_bound",
    "rep_too_short",
    "fit_r2",
    "a0_snap_raw_yds2",
    "baseline_a0_yds2",
    "baseline_a0_se",
    "deficit_ratio",
    "excluded_from_load",
    "exclusion_reason",
)

PHASE3_TEXT_COLUMNS: tuple[str, ...] = ("exclusion_reason",)

_FLOAT_COLUMNS = (
    "inter_snap_rest_s", "rep_kinematic_load_w_per_kg",
    "rolling_kinematic_load_w_per_kg", "a0_snap_yds2", "tau_snap_s",
    "fit_r2", "a0_snap_raw_yds2", "baseline_a0_yds2", "baseline_a0_se",
    "deficit_ratio",
)
_INT_COLUMNS = ("rest_gap_plays", "load_window_n", "frames_used")
_BOOL_COLUMNS = ("rest_sequence_complete", "rows_rusher_tracked", "fit_ok",
                 "fit_at_bound", "rep_too_short", "excluded_from_load")

_REST_RULE = (
    "inter_snap_rest_s = t_snap(current play) - max(time) of the defensive "
    "team's previous play, computed once per (game_id, team, play_id) and "
    "joined to every rusher; NA on the first own snap of a player-game and "
    "whenever the previous play has no recorded end timestamp. A gap in the "
    "player's own snap sequence does not create a value: it is measured from "
    "the play sequence and the player-game is flagged in "
    "rest_sequence_complete; nothing is ever filled, averaged or carried "
    "forward")

_LOAD_RULE = (
    "rolling_kinematic_load_w_per_kg = sum over the prior n included snaps of "
    "integral(max(0, a*v) dt); a in m/s^2, v in m/s => W/kg; yard-space "
    "integral x 0.9144^2; penalty-nullified and no-play rows are excluded "
    "from the accumulation and counted")

_FIT_RULE = (
    "v(t) = vmax*(1-exp(-t/tau)) fitted per in-game rep on post-snap speed; "
    "a0_snap = vmax/tau; raw double-difference kept as a robustness column")


# --------------------------------------------------------------------------- #
# Pure numerics (unit-testable without DuckDB)
# --------------------------------------------------------------------------- #
def power_integral(times: np.ndarray, speed: np.ndarray,
                   accel: np.ndarray, *, clip_negative: bool,
                   max_dt_s: float) -> tuple[float, int]:
    """``integral(a*v dt)`` in yard space, plus the segments used.

    Trapezoid over consecutive finite samples.  A segment longer than
    ``max_dt_s`` is a tracking gap, not a pause: it is skipped rather than
    integrated across.  With ``clip_negative`` only positive (accelerative)
    power accumulates, so a deceleration cannot subtract from a rep's load.
    """
    t = np.asarray(times, dtype=float)
    v = np.asarray(speed, dtype=float)
    a = np.asarray(accel, dtype=float)
    ok = np.isfinite(t) & np.isfinite(v) & np.isfinite(a)
    if ok.sum() < 2:
        return float("nan"), 0
    t, v, a = t[ok], v[ok], a[ok]
    p = a * v
    if clip_negative:
        p = np.maximum(p, 0.0)
    dt = np.diff(t)
    good = dt > 0.0
    if max_dt_s and max_dt_s > 0:
        good &= dt <= float(max_dt_s)
    if not good.any():
        return float("nan"), 0
    seg = 0.5 * (p[:-1] + p[1:]) * dt
    return float(seg[good].sum()), int(good.sum())


def fit_rep(times: np.ndarray, speed: np.ndarray, cfg: Mapping
            ) -> dict[str, Any]:
    """Fit ``v(t) = vmax*(1-exp(-t/tau))``; return vmax/tau/a0/r2 + status.

    Status is one of ``fit_ok``, ``rep_too_short``, ``fit_failed``,
    ``fit_low_r2``.  Nothing is extrapolated: too few frames is a refusal.
    """
    fit = cfg["phase3"]["fit"]
    out: dict[str, Any] = {
        "vmax": float("nan"), "tau": float("nan"), "a0": float("nan"),
        "r2": float("nan"), "status": "rep_too_short", "at_bound": False,
        "n_window_frames": 0,
    }
    t = np.asarray(times, dtype=float)
    v = np.asarray(speed, dtype=float)
    window = float(fit.get("fit_window_s", 0.0) or 0.0)
    ok = np.isfinite(t) & np.isfinite(v) & (t >= 0.0)
    if window > 0:
        ok &= t <= window
    t, v = t[ok], v[ok]
    out["n_window_frames"] = int(t.size)
    if t.size < int(fit["min_frames"]):
        return out
    order = np.argsort(t, kind="mergesort")
    t, v = t[order], v[order]
    keep = np.concatenate(([True], np.diff(t) > 0.0))
    t, v = t[keep], v[keep]
    if t.size < int(fit["min_frames"]):
        return out
    if float(t[-1] - t[0]) < float(fit["min_duration_s"]):
        return out
    out["status"] = "fit_failed"
    try:
        from scipy.optimize import curve_fit
    except Exception:  # pragma: no cover - scipy is a hard dependency
        return out

    def _curve(tt, vmax, tau):
        return vmax * (1.0 - np.exp(-tt / tau))

    p0_vmax = float(np.nanmax(v))
    if not math.isfinite(p0_vmax) or p0_vmax <= 0.0:
        return out
    bounds = ([float(fit["vmax_bounds_yds"][0]), float(fit["tau_bounds_s"][0])],
              [float(fit["vmax_bounds_yds"][1]), float(fit["tau_bounds_s"][1])])
    try:
        popt, _ = curve_fit(_curve, t, v, p0=(p0_vmax, float(fit["p0_tau_s"])),
                            bounds=bounds, maxfev=int(fit["maxfev"]))
    except Exception:
        return out
    vmax, tau = float(popt[0]), float(popt[1])
    if not (math.isfinite(vmax) and math.isfinite(tau)) or tau <= 0:
        return out
    resid = v - _curve(t, vmax, tau)
    ss_tot = float(((v - v.mean()) ** 2).sum())
    r2 = float(1.0 - (resid ** 2).sum() / ss_tot) if ss_tot > 0 else float("nan")
    at_bound = bool(
        math.isclose(vmax, bounds[0][0], abs_tol=1e-6)
        or math.isclose(vmax, bounds[1][0], abs_tol=1e-6)
        or math.isclose(tau, bounds[0][1], abs_tol=1e-6)
        or math.isclose(tau, bounds[1][1], abs_tol=1e-6))
    out.update({"vmax": vmax, "tau": tau, "a0": vmax / tau, "r2": r2,
                "at_bound": at_bound})
    if math.isfinite(r2) and r2 < float(fit["min_r2"]):
        out["status"] = "fit_low_r2"
    else:
        out["status"] = "fit_ok"
    return out


def raw_initial_accel(times: np.ndarray, speed: np.ndarray,
                      window_s: float) -> float:
    """Robustness only: max ``ds/dt`` over the first ``window_s`` post-snap."""
    t = np.asarray(times, dtype=float)
    v = np.asarray(speed, dtype=float)
    ok = np.isfinite(t) & np.isfinite(v) & (t >= 0.0) & (t <= float(window_s))
    t, v = t[ok], v[ok]
    if t.size < 2:
        return float("nan")
    order = np.argsort(t, kind="mergesort")
    t, v = t[order], v[order]
    dt = np.diff(t)
    keep = dt > 0.0
    if not keep.any():
        return float("nan")
    return float(np.max(np.diff(v)[keep] / dt[keep]))


# --------------------------------------------------------------------------- #
# Tracking read
# --------------------------------------------------------------------------- #
def _tracking_files(cfg: Mapping, mode: str, files: Mapping | None) -> list[Path]:
    if files is not None and "tracking" in files:
        value = files["tracking"]
        if value in (None, [], ()):
            return []
        return [Path(value)] if isinstance(value, (str, Path)) else [
            Path(p) for p in value]
    return [p for p in D.tracking_sources(cfg, mode)]


def _csv(paths: Sequence[Path], columns: Sequence[str], null: str) -> str:
    files = [Path(p) for p in paths]
    lst = "[" + ", ".join(f"'{p.as_posix()}'" for p in files) + "]"
    if len(files) == 1:
        base = f"read_csv_auto({lst[1:-1]}, nullstr=['{null}'])"
    else:
        base = (f"read_csv_auto({lst}, nullstr=['{null}'], "
                f"union_by_name=true)")
    return f"(SELECT {', '.join(f'\"{c}\"' for c in columns)} FROM {base})"


def _read_tracking(frame: pd.DataFrame, cfg: Mapping, mode: str,
                   files: Mapping | None, con) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Play timing and per-rep post-snap traces, restricted to table keys."""
    sources = _tracking_files(cfg, mode, files)
    counts: dict[str, Any] = {}
    missing = [str(p) for p in sources if not Path(p).exists()]
    if not sources or missing:
        counts["tracking_missing"] = missing or "no tracking input"
        return pd.DataFrame(), pd.DataFrame(), counts

    null = D.dl(cfg)["runtime"].get("csv_null_marker", "NA")
    keys = frame[["game_id", "play_id", "nfl_id"]].drop_duplicates().copy()
    keys["game_id"] = keys["game_id"].astype("int64")
    keys["play_id"] = keys["play_id"].astype("int64")
    keys["nfl_id"] = keys["nfl_id"].astype("int64")
    reg = f"phase3_keys_{id(keys)}"
    games = keys[["game_id"]].drop_duplicates()
    greg = f"phase3_games_{id(games)}"
    con.register(reg, keys)
    con.register(greg, games)
    src = _csv(sources, ("game_id", "play_id", "nfl_id", "time", "s", "a",
                         "event"), null)
    try:
        # Every tracked frame of every game in the table: play END times need
        # all-22 tracking, not only the tracked rusher's own frames.
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE p3_track AS
            SELECT t.game_id, t.play_id, t.nfl_id, t.time, t.s, t.a, t.event
            FROM {src} t
            WHERE t.time IS NOT NULL
              AND EXISTS (SELECT 1 FROM {greg} g
                          WHERE g.game_id = t.game_id)
        """)
        con.execute("""
            CREATE OR REPLACE TEMP TABLE p3_plays AS
            SELECT game_id, play_id,
                   min(CASE WHEN event = 'ball_snap' THEN time END) AS t_snap,
                   max(time) AS t_end,
                   count(*) AS n_frames_play
            FROM p3_track GROUP BY 1, 2
        """)
        plays = con.execute("SELECT * FROM p3_plays").fetch_df()
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE p3_rep AS
            SELECT t.game_id, t.play_id, t.nfl_id, t.time, t.s, t.a
            FROM p3_track t
            JOIN p3_plays p USING (game_id, play_id)
            JOIN {reg} k USING (game_id, play_id, nfl_id)
            WHERE p.t_snap IS NOT NULL AND t.time >= p.t_snap
        """)
        reps = con.execute(
            "SELECT * FROM p3_rep ORDER BY game_id, play_id, nfl_id, time"
        ).fetch_df()
    finally:
        for tbl in ("p3_track", "p3_rep", "p3_plays"):
            con.execute(f"DROP TABLE IF EXISTS {tbl}")
        con.unregister(reg)
        con.unregister(greg)
    return plays, reps, counts


# --------------------------------------------------------------------------- #
# Penalty flags (read here so stage 03's own projection is untouched)
# --------------------------------------------------------------------------- #
def _defensive_plays(frame: pd.DataFrame, cfg: Mapping, mode: str,
                     files: Mapping | None, con) -> tuple[pd.DataFrame, dict]:
    """One row per (game, defensive team, play): the sequence rest is lagged on.

    ``player_play`` carries the play's participants, so the defensive team is
    the team on the play that is not the possession team.  The sequence is
    **every defensive play**, not only the pass-rush snaps in the table: a run
    between two rushes is part of the rest interval and must not be skipped.
    """
    path: Path | None = None
    if files is not None and "player_play" in files:
        value = files["player_play"]
        path = None if value in (None, [], ()) else Path(value)
    else:
        src = D.source_paths(cfg, "player_play", mode)
        path = Path(src[0]) if src else None
    if path is None or not path.exists():
        return pd.DataFrame(columns=["game_id", "team", "play_id", "penalty"]), {
            "player_play_missing": str(path)}
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            header = {p.strip().strip('"') for p in
                      handle.readline().rstrip("\r\n").split(",")}
    except OSError:
        return pd.DataFrame(columns=["game_id", "team", "play_id", "penalty"]), {
            "player_play_unreadable": str(path)}
    null = D.dl(cfg)["runtime"].get("csv_null_marker", "NA")
    cols = ("game_id", "play_id", "team_abbr", "possession_team")
    penalty_col = ("play_nullified_by_penalty"
                   if "play_nullified_by_penalty" in header else None)
    projected = list(cols) + ([penalty_col] if penalty_col else [])
    src = _csv([path], projected, null)
    games = frame[["game_id"]].drop_duplicates()
    reg = f"phase3_games_pp_{id(games)}"
    con.register(reg, games)
    try:
        flag_expr = ("max(play_nullified_by_penalty)"
                     if penalty_col else "CAST(NULL AS VARCHAR)")
        rows = con.execute(f"""
            SELECT game_id, play_id,
                   max(team_abbr) FILTER (WHERE team_abbr <> possession_team)
                       AS team,
                   {flag_expr} AS flag
            FROM {src}
            WHERE EXISTS (SELECT 1 FROM {reg} g WHERE g.game_id = game_id)
            GROUP BY 1, 2
        """).fetch_df()
    finally:
        con.unregister(reg)
    rows = rows.dropna(subset=["team"]).copy()
    flag = rows["flag"].astype("string").str.strip().str.upper()
    rows["penalty"] = (flag == "Y").fillna(False).astype(bool)
    rows = rows[["game_id", "team", "play_id", "penalty"]]
    return rows, {"n_defensive_plays": int(len(rows))}


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def add_phase3_columns(frame: pd.DataFrame, cfg: Mapping, mode: str = "sample",
                       *, files: Mapping | None = None,
                       baseline: Mapping | None = None,
                       con=None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Append the Phase-3 columns and return ``(frame, summary_block)``."""
    block = D.dl(cfg)["phase3"]
    out = frame.copy()
    n = len(out)
    summary: dict[str, Any] = {
        "n_rows": int(n),
        "rest_rule": _REST_RULE,
        "load_rule": _LOAD_RULE,
        "fit_rule": _FIT_RULE,
        "load_units": "W/kg (m^2/s^3); yard-space integral x 0.9144^2",
        "audit": {"rest_half_life_note": None},
    }
    _blank(out)
    if n == 0:
        summary["skipped"] = "empty snap table"
        return out, summary

    own = con is None
    con = con if con is not None else D.duckdb_connect(cfg)
    try:
        plays, reps, tcounts = _read_tracking(out, cfg, mode, files, con)
        seq_plays, pcounts = _defensive_plays(out, cfg, mode, files, con)
        ceiling = (dict(baseline) if baseline is not None
                   else _baseline_table(cfg, mode))
    finally:
        if own:
            con.close()

    summary["tracking"] = dict(tcounts)
    summary["plays"] = dict(pcounts)
    if plays.empty:
        summary["not_computed"] = (
            tcounts.get("tracking_missing") or "no tracking rows for any snap")
        _fill_exclusion_column(out)
        return out, summary

    rest_stats = _rest_columns(out, plays, seq_plays, block)
    summary["rest"] = rest_stats
    fit_stats = _fit_columns(out, reps, block)
    summary["fit"] = fit_stats
    penalties = {(int(g), int(p)): bool(f) for g, p, f in
                 zip(seq_plays["game_id"], seq_plays["play_id"],
                     seq_plays["penalty"])} if len(seq_plays) else {}
    load_stats = _load_columns(out, penalties, block)
    summary["load"] = load_stats
    base_stats = _baseline_columns(out, ceiling, block)
    summary["baseline"] = base_stats
    _fill_exclusion_column(out)
    summary["columns_added"] = list(PHASE3_COLUMNS)
    out = out.drop(columns=[c for c in ("_has_play_timing", "_seq_idx")
                            if c in out.columns])
    return out, summary


# --------------------------------------------------------------------------- #
# Column blocks
# --------------------------------------------------------------------------- #
def _blank(out: pd.DataFrame) -> None:
    for col in _FLOAT_COLUMNS:
        out[col] = np.nan
    for col in _INT_COLUMNS:
        out[col] = pd.array([pd.NA] * len(out), dtype="Int64")
    for col in _BOOL_COLUMNS:
        out[col] = pd.array([pd.NA] * len(out), dtype="boolean")
    out["exclusion_reason"] = pd.array([pd.NA] * len(out), dtype="object")


def _rest_columns(out: pd.DataFrame, plays: pd.DataFrame,
                  seq_plays: pd.DataFrame, block: Mapping) -> dict[str, Any]:
    """Play-team rest, joined once per play; NA across any unobserved gap."""
    max_gap = int(block["rest"].get("max_gap_plays", 1))
    if seq_plays.empty:
        return {"not_computed": "player_play unavailable: no defensive play "
                                "sequence, so rest is NA rather than lagged "
                                "over a guessed order"}
    pt = seq_plays[["game_id", "team", "play_id"]].copy()
    pt = pt.merge(plays, on=["game_id", "play_id"], how="left")
    pt = pt.sort_values(["game_id", "team", "play_id"], kind="mergesort")
    grp = pt.groupby(["game_id", "team"], sort=False)
    pt["seq_idx"] = grp.cumcount()
    pt["has_timing"] = pt["t_snap"].notna() & pt["t_end"].notna()
    prev_end = grp["t_end"].shift(1)
    rest = (pt["t_snap"] - prev_end).dt.total_seconds()
    n_negative = int((rest < 0).sum())
    rest = rest.where(rest >= 0)
    pt["team_rest_s"] = rest

    lookup = {(int(g), int(p), str(t)): (float(s), float(r), bool(h))
              for g, p, t, s, r, h in zip(
                  pt["game_id"], pt["play_id"], pt["team"], pt["seq_idx"],
                  pt["team_rest_s"], pt["has_timing"])}
    fallback = (np.nan, np.nan, False)
    rec = [lookup.get((int(g), int(p), str(t)), fallback)
           for g, p, t in zip(out["game_id"], out["play_id"], out["team"])]
    seq = np.array([r[0] for r in rec], dtype=float)
    out["inter_snap_rest_s"] = np.array([r[1] for r in rec], dtype=float)
    has_timing = np.array([r[2] for r in rec], dtype=bool)
    out["_has_play_timing"] = has_timing
    out["_seq_idx"] = seq

    # Gap in the player's own sequence, measured in team plays.
    n_rows = len(out)
    gap = np.full(n_rows, np.nan)
    order = np.lexsort((out["play_id"].to_numpy(), seq,
                        out["nfl_id"].to_numpy(), out["game_id"].to_numpy()))
    keys = out[["game_id", "nfl_id"]].to_numpy()
    for a, b in zip(order[:-1], order[1:]):
        if keys[a][0] == keys[b][0] and keys[a][1] == keys[b][1]:
            if np.isfinite(seq[a]) and np.isfinite(seq[b]):
                gap[b] = seq[b] - seq[a]
    out["rest_gap_plays"] = pd.array(
        [pd.NA if not np.isfinite(g) else int(round(g)) for g in gap],
        dtype="Int64")

    first = ~np.isfinite(gap) & has_timing
    rest_arr = out["inter_snap_rest_s"].to_numpy(dtype=float)
    prev_end_missing = np.isfinite(gap) & has_timing & ~np.isfinite(rest_arr)
    # The lag belongs to the play sequence, not to the player: a rotational
    # player who sat out the previous play still has a measured interval from
    # that play's end to this snap.  Rest is NA only when the previous play has
    # no recorded end (no tracking) or on the player's first snap -- never
    # filled, averaged or carried forward.
    ok = has_timing & ~first & np.isfinite(rest_arr)
    out.loc[~ok, "inter_snap_rest_s"] = np.nan

    # Per-player-game completeness: every own snap after the first is adjacent
    # in the team's defensive sequence.  Unknown (NA) when the player has no
    # timed snap at all in that game.
    complete = ~(np.isfinite(gap) & (gap > max_gap))
    in_incomplete = np.zeros(n_rows, dtype=bool)
    flag = pd.Series(pd.NA, index=range(n_rows), dtype="boolean")
    for _, idx in out.groupby(["game_id", "nfl_id"], sort=False).indices.items():
        rows = list(idx)
        if not has_timing[rows].any():
            continue
        good = bool(complete[rows].all())
        flag.iloc[rows] = good
        if not good:
            in_incomplete[rows] = True
    out["rest_sequence_complete"] = pd.array(flag.tolist(), dtype="boolean")

    n_present = int(out["inter_snap_rest_s"].notna().sum())
    n_player_games = int(out.groupby(["game_id", "nfl_id"]).ngroups)
    n_complete = int(out.groupby(["game_id", "nfl_id"])[
        "rest_sequence_complete"].first().fillna(False).sum())
    return {
        "n_rest_present": n_present,
        "share_rest_present": (n_present / n_rows) if n_rows else None,
        "n_first_snap_na": int(first.sum()),
        "n_rows_in_incomplete_sequence": int(in_incomplete.sum()),
        "n_prev_play_without_end_timestamp": int(prev_end_missing.sum()),
        "n_negative_rest_nulled": n_negative,
        "n_rows_without_play_timing": int((~has_timing).sum()),
        "n_defensive_plays_in_sequence": int(len(pt)),
        "n_player_games": n_player_games,
        "n_player_games_complete": n_complete,
        "share_player_games_complete": (
            n_complete / n_player_games) if n_player_games else None,
        "max_gap_plays": max_gap,
    }


def _fit_columns(out: pd.DataFrame, reps: pd.DataFrame,
                 block: Mapping) -> dict[str, Any]:
    """Per-rep envelope fit: a0_snap, tau, fit flags, raw robustness value."""
    fit_cfg = block["fit"]
    fit_view = block_wrapper(fit_cfg)
    yard = float(block["load"].get("yard_to_metre", 0.9144))
    clip = bool(block["load"].get("clip_negative_power", True))
    max_dt = float(block["load"].get("max_dt_s", 0.5))
    raw_window = float(fit_cfg.get("raw_window_s", 0.5))

    results: dict[tuple, dict[str, Any]] = {}
    if not reps.empty:
        for key, grp in reps.groupby(["game_id", "play_id", "nfl_id"],
                                     sort=False):
            t0 = grp["time"].iloc[0]
            times = (grp["time"] - grp["time"].min()).dt.total_seconds(
            ).to_numpy(dtype=float)
            speed = grp["s"].to_numpy(dtype=float)
            accel = grp["a"].to_numpy(dtype=float)
            fit = fit_rep(times, speed, fit_view)
            power, segs = power_integral(times, speed, accel,
                                         clip_negative=clip,
                                         max_dt_s=max_dt)
            results[(int(key[0]), int(key[1]), int(key[2]))] = {
                "frames": int(len(grp)),
                "vmax": fit["vmax"], "tau": fit["tau"], "a0": fit["a0"],
                "r2": fit["r2"], "status": fit["status"],
                "at_bound": bool(fit.get("at_bound", False)),
                "raw_a0": raw_initial_accel(times, speed, raw_window),
                "rep_load": power * yard * yard if math.isfinite(power)
                else float("nan"),
                "segments": segs,
            }

    keys = list(zip(out["game_id"].astype("int64"),
                    out["play_id"].astype("int64"),
                    out["nfl_id"].astype("int64")))
    tracked = [k in results for k in keys]
    get = lambda k, f: results[k].get(f, float("nan")) if k in results else float("nan")
    out["rows_rusher_tracked"] = pd.array(tracked, dtype="boolean")
    out["frames_used"] = pd.array(
        [int(results[k]["frames"]) if k in results else pd.NA for k in keys],
        dtype="Int64")
    out["a0_snap_yds2"] = [get(k, "a0") for k in keys]
    out["tau_snap_s"] = [get(k, "tau") for k in keys]
    out["fit_r2"] = [get(k, "r2") for k in keys]
    out["a0_snap_raw_yds2"] = [get(k, "raw_a0") for k in keys]
    out["rep_kinematic_load_w_per_kg"] = [
        get(k, "rep_load") for k in keys]
    status = [results[k]["status"] if k in results else "not_tracked"
              for k in keys]
    out["fit_ok"] = pd.array([s == "fit_ok" for s in status], dtype="boolean")
    out["fit_at_bound"] = pd.array(
        [bool(results[k]["at_bound"]) if k in results else pd.NA
         for k in keys], dtype="boolean")
    out["rep_too_short"] = pd.array(
        [s == "rep_too_short" for s in status], dtype="boolean")
    out.loc[~np.asarray(tracked, dtype=bool), ["fit_ok", "rep_too_short"]] = (
        pd.NA)

    n_tracked = int(sum(tracked))
    n_fit = int(sum(s == "fit_ok" for s in status))
    n_short = int(sum(s == "rep_too_short" for s in status))
    n_bound = int(sum(bool(results[k]["at_bound"]) for k in keys
                       if k in results))
    n_failed = int(sum(s in ("fit_failed", "fit_low_r2") for s in status))
    r2s = [get(k, "r2") for k in keys]
    r2s = [r for r in r2s if math.isfinite(r)]
    return {
        "n_rows_rusher_tracked": n_tracked,
        "share_rows_rusher_tracked": (n_tracked / len(out)) if len(out) else None,
        "n_fit_ok": n_fit,
        "share_fit_ok_of_tracked": (n_fit / n_tracked) if n_tracked else None,
        "n_rep_too_short": n_short,
        "n_fit_at_bound": n_bound,
        "n_fit_failed_or_low_r2": n_failed,
        "mean_fit_r2": (float(np.mean(r2s)) if r2s else None),
        "status_counts": {s: status.count(s) for s in sorted(set(status))},
    }


def _load_columns(out: pd.DataFrame, penalties: Mapping, block: Mapping
                  ) -> dict[str, Any]:
    """Rolling power load over the prior n included reps, per player-game."""
    window = int(block["load"].get("window_snaps", 3))
    reason = pd.array([pd.NA] * len(out), dtype="object")
    key_mask = [
        bool(penalties.get((int(g), int(p)), False))
        for g, p in zip(out["game_id"], out["play_id"])]
    penalty = np.asarray(key_mask, dtype=bool)
    no_snap = ~out["_has_play_timing"].to_numpy(dtype=bool)
    not_tracked = ~out["rows_rusher_tracked"].fillna(False).to_numpy(
        dtype=bool)
    rep_load = out["rep_kinematic_load_w_per_kg"].to_numpy(dtype=float)
    no_load = ~np.isfinite(rep_load)

    for i in np.where(penalty)[0]:
        reason[i] = "penalty_nullified"
    for i in np.where(~penalty & no_snap)[0]:
        reason[i] = "no_play_timing"
    for i in np.where(~penalty & ~no_snap & not_tracked)[0]:
        reason[i] = "not_tracked"
    for i in np.where(~penalty & ~no_snap & ~not_tracked & no_load)[0]:
        reason[i] = "rep_too_short"
    excluded = penalty | no_snap | no_load

    rolling = np.full(len(out), np.nan)
    used = pd.array([pd.NA] * len(out), dtype="Int64")
    gaps = 0
    incomplete = 0
    excluded_window = 0
    order = np.lexsort((out["play_id"].to_numpy(),
                        out["_seq_idx"].to_numpy(dtype=float),
                        out["nfl_id"].to_numpy(),
                        out["game_id"].to_numpy()))
    browse: dict[tuple, list[int]] = {}
    for i in order:
        key = (out["game_id"].iat[i], out["nfl_id"].iat[i])
        browse.setdefault(key, []).append(i)
    for key in sorted(browse, key=lambda k: (str(k[0]), str(k[1]))):
        seq = browse[key]
        for pos, i in enumerate(seq):
            if excluded[i]:
                continue
            if pos < window:
                incomplete += 1
                continue
            win = seq[pos - window:pos]
            if any(excluded[j] for j in win):
                excluded_window += 1
                continue
            if any(not np.isfinite(rep_load[j]) for j in win):
                gaps += 1
                continue
            rolling[i] = float(sum(rep_load[j] for j in win))
            used[i] = window
    out["rolling_kinematic_load_w_per_kg"] = rolling
    out["load_window_n"] = used
    out["excluded_from_load"] = pd.array(excluded, dtype="boolean")
    out["exclusion_reason"] = reason
    return {
        "window_snaps": window,
        "n_rolling_present": int(np.isfinite(rolling).sum()),
        "n_window_incomplete": int(incomplete),
        "n_window_gap": int(gaps),
        "n_window_contains_excluded_snap": int(excluded_window),
        "n_excluded_penalty_nullified": int(penalty.sum()),
        "n_excluded_no_play_timing": int((~penalty & no_snap).sum()),
        "n_excluded_not_tracked": int((~penalty & ~no_snap & not_tracked).sum()),
        "n_excluded_rep_too_short": int(
            (~penalty & ~no_snap & ~not_tracked & no_load).sum()),
    }


def _baseline_table(cfg: Mapping, mode: str) -> dict[int, tuple[float, float]]:
    """Shrunken Combine ``a0`` + SE per player from the Phase-2 ceiling table."""
    block = D.dl(cfg)["phase3"]["baseline"]
    paths = D.resolve_paths(cfg, mode)
    path = paths["outputs"] / str(block["source_subdir"]) / str(block["file"])
    if not path.exists():
        return {}
    try:
        table = pd.read_csv(path)
    except Exception:
        return {}
    need = {str(block["estimate_column"]), str(block["se_column"])}
    if "feature" not in table.columns or not need.issubset(table.columns):
        return {}
    table = table[table["feature"] == block["feature"]]
    out: dict[int, tuple[float, float]] = {}
    for nfl_id, est, se in zip(table["nfl_id"], table[block["estimate_column"]],
                               table[block["se_column"]]):
        out[int(nfl_id)] = (float(est), float(se) if pd.notna(se) else float("nan"))
    return out


def _baseline_columns(out: pd.DataFrame, ceiling: Mapping,
                      block: Mapping) -> dict[str, Any]:
    base = np.array([ceiling.get(int(p), (np.nan, np.nan))[0]
                     for p in out["nfl_id"]], dtype=float)
    se = np.array([ceiling.get(int(p), (np.nan, np.nan))[1]
                   for p in out["nfl_id"]], dtype=float)
    out["baseline_a0_yds2"] = base
    out["baseline_a0_se"] = se
    a0 = out["a0_snap_yds2"].to_numpy(dtype=float)
    ratio = np.where(np.isfinite(a0) & np.isfinite(base) & (base > 0.0),
                     a0 / base, np.nan)
    out["deficit_ratio"] = ratio
    return {
        "n_rows_with_baseline": int(np.isfinite(base).sum()),
        "n_deficit_computed": int(np.isfinite(ratio).sum()),
        "n_players_with_baseline": len(ceiling),
    }


def _fill_exclusion_column(out: pd.DataFrame) -> None:
    if "exclusion_reason" not in out.columns:  # pragma: no cover
        out["exclusion_reason"] = pd.array([pd.NA] * len(out), dtype="object")


def block_wrapper(fit_cfg: Mapping) -> dict[str, Any]:
    """``fit_rep`` reads ``cfg['phase3']['fit']``; hand it that shape."""
    return {"phase3": {"fit": dict(fit_cfg)}}
