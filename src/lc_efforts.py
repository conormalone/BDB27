"""lc_efforts — route-effort extraction shared by the synthetic recovery tests.

This is the single implementation of the effort table described in ``TASK.md``
Phase 3, kept deliberately small so it can run on a fixture in a test and be
reused by the future full-data stage.  It reads only pre-snap columns for
leverage and computes the outcome from tracking speed.

Definitions (design placeholders from ``config.yaml``):

* an *effort* is a pass-route snap by a WR/TE with a non-null ``route_ran``;
* ``max_speed`` is the maximum tracked player speed on that snap;
* ``leverage`` is ``4 * wp * (1 - wp)`` from the provided pre-snap home win
  probability (symmetric, so the team perspective is irrelevant);
* ``prior_efforts`` counts earlier route efforts by the same player in the same
  game -- every effort in this table already satisfies the max-effort route
  definition, so this is the "prior high-intensity efforts" load measure;
* ``route_displacement_yd`` is start-to-end displacement.  It is descriptive,
  not a control: over a fixed route duration it is mechanically a function of
  speed.  The full-data stage derives designed route depth at the break.

No post-snap field (target, result, completion) enters the effort or the
leverage definition.

Scale note: the tracking summary loops per player-play, which is fine for the
fixture and the recovery tests.  The real `03_game_efforts` stage must aggregate
`max(s)`, duration and path per `(game_id, play_id, nfl_id)` inside DuckDB per
game, then pass that summary to the same model functions; it must not call this
Python loop on the full tracking file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def leverage_from_wp(wp) -> np.ndarray:
    """Leverage index ``4 p (1-p)`` in [0, 1]."""
    p = np.asarray(wp, dtype=float)
    return 4.0 * p * (1.0 - p)


def _clock_seconds(value) -> float:
    """Parse ``MM:SS`` (or a datetime.time) into seconds remaining."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return float("nan")
    if hasattr(value, "minute") and hasattr(value, "second"):
        return float(value.minute * 60 + value.second)
    text = str(value).strip()
    if ":" not in text:
        return float("nan")
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
    except ValueError:
        return float("nan")
    return float("nan")


def _route_family(route: str) -> str:
    text = str(route).strip().upper()
    return text if text and text != "NAN" else "UNKNOWN"


def _position_mask(pp: pd.DataFrame, players: pd.DataFrame | None) -> pd.Series:
    positions = {"WR", "TE"}
    if players is not None and "nfl_position" in players.columns:
        lookup = dict(zip(players["nfl_id"], players["nfl_position"]))
        return pp["nfl_id"].map(lookup).isin(positions)
    lined = pp.get("lined_up_position", pd.Series("", index=pp.index)).astype(str)
    return lined.str.upper().str.contains("WR|TE", regex=True)


def _tracking_summary(tracking: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, play_id, nfl_id) with speed/duration/path."""
    tr = tracking.copy()
    tr["time"] = pd.to_datetime(tr["time"], errors="coerce")
    tr = tr.dropna(subset=["time", "x", "y", "s"])
    tr = tr.sort_values(["game_id", "play_id", "nfl_id", "time"])
    grp = tr.groupby(["game_id", "play_id", "nfl_id"], sort=False)
    rows = []
    for key, g in grp:
        if len(g) < 2:
            continue
        dt = g["time"].diff().dt.total_seconds().to_numpy()
        dx = g["x"].diff().to_numpy()
        dy = g["y"].diff().to_numpy()
        step = np.hypot(dx, dy)
        path = float(np.nansum(step))
        disp = float(np.hypot(g["x"].iloc[-1] - g["x"].iloc[0],
                              g["y"].iloc[-1] - g["y"].iloc[0]))
        duration = float((g["time"].iloc[-1] - g["time"].iloc[0]).total_seconds())
        rows.append({
            "game_id": key[0], "play_id": key[1], "nfl_id": key[2],
            "max_speed_yds": float(g["s"].max()),
            "start_time": g["time"].iloc[0], "duration_s": duration,
            "n_frames": int(len(g)), "route_path_yd": path,
            "route_displacement_yd": disp,
            "median_gap_s": float(np.nanmedian(np.diff(dt))) if len(g) > 2 else np.nan,
        })
    return pd.DataFrame(rows)


def extract_efforts(player_play: pd.DataFrame, tracking: pd.DataFrame,
                    players: pd.DataFrame | None = None, cfg: dict | None = None,
                    min_depth: float = 0.0, min_duration: float = 0.0,
                    min_frames: int = 5) -> tuple[pd.DataFrame, dict]:
    """Build the one-row-per-effort table plus extraction diagnostics."""
    pp = player_play.copy()
    diag = {"n_player_play_rows": int(len(pp))}

    if "route_ran" not in pp.columns:
        raise KeyError("player_play has no route_ran column")
    pp = pp[pp["route_ran"].notna() & (pp["route_ran"].astype(str) != "")]
    diag["n_route_rows"] = int(len(pp))
    pp = pp[_position_mask(pp, players)]
    diag["n_wr_te_route_rows"] = int(len(pp))

    wp = pp.get("pre_snap_home_team_win_probability")
    missing_wp = int(pd.to_numeric(wp, errors="coerce").isna().sum()) if wp is not None else len(pp)
    diag["n_missing_wp"] = missing_wp
    if wp is not None:
        pp = pp[pd.to_numeric(wp, errors="coerce").notna()]

    tr = _tracking_summary(tracking)
    diag["n_tracked_player_plays"] = int(len(tr))
    merged = pp.merge(tr, on=["game_id", "play_id", "nfl_id"], how="left")
    diag["n_without_tracking"] = int(merged["max_speed_yds"].isna().sum())
    merged = merged[merged["max_speed_yds"].notna() & (merged["n_frames"] >= min_frames)]
    merged = merged[(merged["route_displacement_yd"] >= min_depth) &
                    (merged["duration_s"] >= min_duration)]
    diag["n_efforts"] = int(len(merged))
    if merged.empty:
        return merged, diag

    out = pd.DataFrame({
        "game_id": merged["game_id"].astype("int64"),
        "play_id": merged["play_id"].astype("int64"),
        "nfl_id": merged["nfl_id"].astype("int64"),
        "route_family": merged["route_ran"].map(_route_family),
        "quarter": pd.to_numeric(merged.get("quarter"), errors="coerce"),
        "down": pd.to_numeric(merged.get("down"), errors="coerce"),
        "yards_to_go": pd.to_numeric(merged.get("yards_to_go"), errors="coerce"),
        "score_margin": (pd.to_numeric(merged.get("pre_snap_home_score"), errors="coerce")
                         - pd.to_numeric(merged.get("pre_snap_visitor_score"), errors="coerce")).abs(),
        "time_to_throw_s": pd.to_numeric(merged.get("dropback_duration"), errors="coerce"),
        "wp_pre": pd.to_numeric(merged["pre_snap_home_team_win_probability"], errors="coerce"),
        "max_speed_yds": merged["max_speed_yds"].astype(float),
        "route_displacement_yd": merged["route_displacement_yd"].astype(float),
        "route_path_yd": merged["route_path_yd"].astype(float),
        "duration_s": merged["duration_s"].astype(float),
        "n_frames": merged["n_frames"].astype(int),
        "start_time": merged["start_time"],
    })
    out["leverage"] = leverage_from_wp(out["wp_pre"])
    clock = merged["game_clock"].map(_clock_seconds) if "game_clock" in merged else np.nan
    out["game_clock_s"] = clock
    out["quarter"] = out["quarter"].fillna(1)
    out["game_elapsed_s"] = ((out["quarter"] - 1).clip(lower=0) * 900.0
                            + (900.0 - out["game_clock_s"]))
    out.loc[out["quarter"] >= 5, "game_elapsed_s"] = (
        3600.0 + (900.0 - out["game_clock_s"]))
    out["log_max_speed"] = np.log(out["max_speed_yds"].clip(lower=1e-6))

    # Cumulative load: only earlier efforts by the same player in the same game.
    out = out.sort_values(["nfl_id", "game_id", "start_time", "play_id"]).reset_index(drop=True)
    grp = out.groupby(["nfl_id", "game_id"], sort=False)
    out["prior_efforts"] = grp.cumcount().astype(float)
    out["prior_distance_yd"] = grp["route_path_yd"].cumsum() - out["route_path_yd"]

    # Standardise within player so the coefficient is "per 1 SD of leverage".
    def _z(col: pd.Series) -> pd.Series:
        g = out.groupby("nfl_id")[col]
        mean = g.transform("mean")
        sd = g.transform("std").replace(0, np.nan)
        return ((out[col] - mean) / sd).fillna(0.0)

    out["leverage_z"] = _z("leverage")
    out["time_z"] = _z("game_elapsed_s")
    out["load_z"] = _z("prior_efforts")

    # Players with no leverage variation (one bin) contribute no within-player
    # information; keep the rows but mark them so the model can report it.
    spread = out.groupby("nfl_id")["leverage"].transform("std").fillna(0.0)
    out["one_bin_player"] = spread <= 1e-12
    diag["n_one_bin_players"] = int(out.loc[out["one_bin_player"], "nfl_id"].nunique())
    diag["n_players"] = int(out["nfl_id"].nunique())
    out["time_to_throw_s"] = out["time_to_throw_s"].fillna(
        out["time_to_throw_s"].median() if out["time_to_throw_s"].notna().any() else 0.0)
    out["score_margin"] = out["score_margin"].fillna(out["score_margin"].median())
    out["down"] = out["down"].fillna(0)
    out["yards_to_go"] = out["yards_to_go"].fillna(out["yards_to_go"].median())
    return out.reset_index(drop=True), diag
