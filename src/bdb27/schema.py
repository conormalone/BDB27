"""Schema verification against the actual files (TASK.md Phase 0 and item b).

Nothing here is copied from memory: every number is read from the CSV on disk.
The point is to catch the places where the *spec's* description of the data and
the *files* disagree, and to state units and angle conventions with evidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from typing import Sequence

from . import kinematics as kin
from .common import Config, connect, raw_csv_sql


def file_inventory(cfg: Config, mode: str) -> pd.DataFrame:
    """Size, row count and column list for every configured raw file."""
    rows = []
    for key, fname in {**cfg.block["files"], **cfg.block.get("sample_files", {})}.items():
        p = cfg.repo_path("raw") / fname
        rec = {
            "file_key": key,
            "filename": fname,
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else None,
        }
        if p.exists():
            con = connect(cfg)
            try:
                info = con.execute(f"SELECT * FROM {raw_csv_sql(cfg, mode, key)} LIMIT 0").description
                rec["n_columns"] = len(info)
                rec["columns"] = "|".join(d[0] for d in info)
                rec["n_rows"] = con.execute(
                    f"SELECT count(*) FROM {raw_csv_sql(cfg, mode, key)}").fetchone()[0]
            finally:
                con.close()
        rows.append(rec)
    return pd.DataFrame(rows)


def _profile(cfg: Config, mode: str, key: str, columns: list[str],
             where: str = "") -> pd.DataFrame:
    """dtype + null + range profile for named columns, read via DuckDB."""
    con = connect(cfg)
    try:
        sql = raw_csv_sql(cfg, mode, key)
        parts = []
        for c in columns:
            parts.append(
                f"SELECT '{c}' AS column, any_value(typeof({c})) AS dtype, "
                f"sum(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS n_null, "
                f"min({c}) AS vmin, max({c}) AS vmax, "
                f"count(DISTINCT {c}) AS n_distinct "
                f"FROM {sql} {where}")
        df = con.execute(" UNION ALL ".join(parts)).df()
    finally:
        con.close()
    return df


def combine_profile(cfg: Config, mode: str) -> tuple[pd.DataFrame, dict]:
    cols = cfg.get("columns.combine_tracking")
    prof = _profile(cfg, mode, "combine_tracking", cols)
    con = connect(cfg)
    try:
        sql = raw_csv_sql(cfg, mode, "combine_tracking")
        entity = con.execute(
            f"SELECT entity_type, count(*) n, count(DISTINCT nfl_id) players FROM {sql} GROUP BY 1"
        ).df()
        per_attempt = con.execute(f"""
            SELECT count(*) AS n_attempts,
                   quantile_cont(n_frames, [0.05, 0.5, 0.95]) AS frames,
                   min(n_frames) AS min_frames, max(n_frames) AS max_frames
            FROM (SELECT nfl_id, event_id, attempt, count(*) n_frames FROM {sql}
                  WHERE entity_type = 'PLAYER' GROUP BY 1,2,3)""").df()
        tspan = con.execute(f"""
            SELECT quantile_cont(span_s, [0.05, 0.5, 0.95]) span_s FROM (
              SELECT nfl_id, event_id, attempt,
                     epoch_ms(max(time)) / 1000.0 - epoch_ms(min(time)) / 1000.0 AS span_s
              FROM {sql} WHERE entity_type = 'PLAYER' GROUP BY 1,2,3)""").df()
    finally:
        con.close()
    meta = {
        "entity_types": entity.to_dict("records"),
        "attempts": per_attempt.to_dict("records")[0],
        "attempt_duration_s": tspan.iloc[0].to_dict(),
    }
    return prof, meta


def game_profile(cfg: Config, mode: str) -> tuple[pd.DataFrame, dict]:
    cols = cfg.get("columns.game_tracking")
    prof = _profile(cfg, mode, "game_tracking", cols)
    con = connect(cfg)
    try:
        sql = raw_csv_sql(cfg, mode, "game_tracking")
        meta = con.execute(f"""
            SELECT count(*) n_rows, count(DISTINCT game_id) games,
                   count(DISTINCT play_id) plays, count(DISTINCT nfl_id) players,
                   min(time) t0, max(time) t1 FROM {sql}""").df().to_dict("records")[0]
        pairs = con.execute(f"""SELECT count(*) n_pairs FROM (
            SELECT DISTINCT game_id, play_id, nfl_id FROM {sql})""").fetchone()[0]
        meta["play_player_pairs"] = int(pairs)
    finally:
        con.close()
    return prof, meta


def wrap_and_zero(profile: pd.DataFrame, column: str) -> dict:
    row = profile[profile.column == column].iloc[0]
    return {
        "column": column,
        "min": None if pd.isna(row.vmin) else float(row.vmin),
        "max": None if pd.isna(row.vmax) else float(row.vmax),
        "wraps_0_360": bool(pd.notna(row.vmin) and float(row.vmin) >= 0
                            and float(row.vmax) <= 360.0),
    }


def heading_offset(cfg: Config, mode: str) -> dict:
    """Empirical test of dir's zero reference, rotation and offset vs velocity.

    Uses the provided speed to select moving frames, but derives heading from
    finite differences of x/y (this is a convention probe, not kinematics).
    """
    con = connect(cfg)
    try:
        if mode == "sample":
            sql = f"""SELECT nfl_id, event_id, attempt, epoch_ms(time) ms, x, y, dir, s
                      FROM {raw_csv_sql(cfg, mode, 'combine_tracking')}
                      WHERE entity_type='PLAYER' AND drill_name='FORTY_YARD_DASH'
                        AND nfl_id IN (SELECT nfl_id FROM {raw_csv_sql(cfg, mode, 'combine_tracking')}
                                       WHERE drill_name='FORTY_YARD_DASH' GROUP BY 1 LIMIT 40)"""
        else:  # pragma: no cover - full mode not available here
            sql = f"""SELECT nfl_id, event_id, attempt, epoch_ms(time) ms, x, y, dir, s
                      FROM {raw_csv_sql(cfg, mode, 'combine_tracking')}
                      WHERE entity_type='PLAYER' AND drill_name='FORTY_YARD_DASH'"""
        df = con.execute(sql).df()
    finally:
        con.close()
    diffs, n = [], 0
    for _, g in df.sort_values(["nfl_id", "event_id", "attempt", "ms"]).groupby(
            ["nfl_id", "event_id", "attempt"], observed=True):
        x, y = g.x.to_numpy(float), g.y.to_numpy(float)
        if len(g) < 6:
            continue
        vx, vy = np.gradient(x, 0.1), np.gradient(y, 0.1)
        h = kin.heading_deg(vx, vy)
        m = g.s.to_numpy(float) > 5.0
        if m.sum() < 3:
            continue
        d = np.asarray(kin.wrap180(g.dir.to_numpy(float)[m] - h[m]))
        diffs.append(d)
        n += len(d)
    if not diffs:
        return {"n_frames": 0, "offset_deg": None}
    all_d = np.concatenate(diffs)
    mean_vec = np.exp(1j * np.radians(all_d))
    offset = float(np.degrees(np.angle(mean_vec.mean())))
    return {
        "n_frames": int(n),
        "offset_deg": offset,
        "circular_sd_deg": float(np.degrees(np.sqrt(-2 * np.log(max(abs(mean_vec.mean()), 1e-12))))),
        "share_within_5deg": float(np.mean(np.abs(kin.wrap180(all_d)) <= 5.0)),
        "share_within_15deg": float(np.mean(np.abs(kin.wrap180(all_d)) <= 15.0)),
    }


def dt_profile(cfg: Config, mode: str) -> dict:
    """Frame spacing: rate, irregularity, duplicates, dropped frames."""
    con = connect(cfg)
    try:
        c_sql = raw_csv_sql(cfg, mode, "combine_tracking")
        g_sql = raw_csv_sql(cfg, mode, "game_tracking")
        out = {}
        out["combine"] = con.execute(f"""
            WITH t AS (SELECT epoch_ms(time) ms, nfl_id, event_id, attempt FROM {c_sql}
                       WHERE entity_type='PLAYER'),
                 d AS (SELECT (ms - lag(ms) OVER (PARTITION BY nfl_id,event_id,attempt ORDER BY ms))/1000.0 dt
                       FROM t)
            SELECT count(*) n, quantile_cont(dt, [0.01,0.25,0.5,0.75,0.99]) q,
                   sum(CASE WHEN abs(dt-0.1)<1e-6 THEN 1 ELSE 0 END) n_exact_0p1,
                   sum(CASE WHEN abs(dt-0.1)>1e-6 THEN 1 ELSE 0 END) n_off_grid,
                   sum(CASE WHEN dt>0.2 THEN 1 ELSE 0 END) n_gaps_over_0p2
            FROM d WHERE dt IS NOT NULL""").df().to_dict("records")[0]
        out["game"] = con.execute(f"""
            WITH t AS (SELECT epoch_ms(time) ms, game_id, play_id, nfl_id FROM {g_sql}),
                 d AS (SELECT (ms - lag(ms) OVER (PARTITION BY game_id,play_id,nfl_id ORDER BY ms))/1000.0 dt
                       FROM t)
            SELECT count(*) n, quantile_cont(dt, [0.01,0.25,0.5,0.75,0.99]) q,
                   sum(CASE WHEN abs(dt-0.1)<1e-6 THEN 1 ELSE 0 END) n_exact_0p1,
                   sum(CASE WHEN abs(dt-0.1)>1e-6 THEN 1 ELSE 0 END) n_off_grid,
                   sum(CASE WHEN dt>0.2 THEN 1 ELSE 0 END) n_gaps_over_0p2
            FROM d WHERE dt IS NOT NULL""").df().to_dict("records")[0]
        out["duplicate_frames_game"] = con.execute(f"""
            SELECT count(*) n FROM (SELECT game_id,play_id,nfl_id,time,count(*) c FROM {g_sql}
            GROUP BY 1,2,3,4 HAVING c>1)""").fetchone()[0]
        # entity_type='PLAYER' only: the BALL rows share the player id and would
        # otherwise be counted as duplicate player frames.
        out["duplicate_frames_combine"] = con.execute(f"""
            SELECT count(*) n FROM (SELECT nfl_id,event_id,attempt,time,count(*) c FROM {c_sql}
            WHERE entity_type='PLAYER' GROUP BY 1,2,3,4 HAVING c>1)""").fetchone()[0]
        out["ball_rows_combine"] = con.execute(
            f"SELECT count(*) FROM {c_sql} WHERE entity_type='BALL'").fetchone()[0]
    finally:
        con.close()
    return out


def compare_settings(combine_prof: pd.DataFrame, game_prof: pd.DataFrame) -> pd.DataFrame:
    """Column-by-column comparison, with the known unit differences spelled out."""
    c = {r.column: r for r in combine_prof.itertuples()}
    g = {r.column: r for r in game_prof.itertuples()}
    rows = []
    for col in sorted(set(c) | set(g)):
        in_c, in_g = col in c, col in g
        rows.append({
            "column": col,
            "in_combine": in_c,
            "in_game": in_g,
            "dtype_combine": c[col].dtype if in_c else None,
            "dtype_game": g[col].dtype if in_g else None,
            "range_combine": f"{c[col].vmin}..{c[col].vmax}" if in_c else None,
            "range_game": f"{g[col].vmin}..{g[col].vmax}" if in_g else None,
            "mismatch": (in_c != in_g)
                        or (in_c and in_g and (c[col].dtype != g[col].dtype)),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ assertions


class SchemaError(AssertionError):
    """Raised when a file does not have the columns the pipeline requires."""


def assert_columns(df: pd.DataFrame, required: Sequence[str], *, name: str) -> None:
    """Fail loudly, naming every missing column and what was found instead."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SchemaError(
            f"{name}: missing required column(s) {missing}; found {list(df.columns)}")


def assert_angle_range(df: pd.DataFrame, column: str, *, name: str) -> None:
    """Angles must be degrees in [0, 360) — checked, not assumed."""
    v = df[column].dropna()
    if len(v) == 0:
        raise SchemaError(f"{name}.{column}: no values")
    lo, hi = float(v.min()), float(v.max())
    if lo < -1e-9 or hi > 360.0 + 1e-9:
        raise SchemaError(f"{name}.{column}: expected degrees in [0, 360], found [{lo}, {hi}]")
