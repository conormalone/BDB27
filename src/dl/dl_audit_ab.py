"""dl_audit_ab — Phase 1 evidence audit (combine drills + DL game participation).

Reproduces the expected numbers in ``notes/dl-evidence.md`` sections 1-2 and
writes two audit tables to ``<outputs>/audit/``:

* ``a_drills.csv``  — one row per combine ``drill_name`` for DL players, then
  one row per ``combine_results`` column (``field`` / ``non_null_rate``).
* ``b_matched.csv`` — one row per DL player: volume, rush rows, participation.

Read-only with respect to raw data: the game tracking file is never opened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import duckdb

from dl import dl_common

DL_POSITIONS = ("DE", "DT", "NT")
A_DRILL_FIELDS = [
    "drill_name", "n_rows", "n_players", "n_two_attempt_players",
    "n_one_attempt_players", "frames_min", "frames_median", "frames_max",
    "class_years", "field", "non_null_rate",
]
B_FIELDS = [
    "nfl_id", "nfl_position", "position_group", "n_rows", "n_rush_rows",
    "n_games", "n_plays", "team_def_plays", "participation_share",
    "pressure_rows",
]


def _csv(path: Path) -> str:
    return (f"read_csv_auto('{path.as_posix()}', nullstr=['NA'], header=true)")


def _dl_ids_sql(players: str) -> str:
    pos = ", ".join(f"'{p}'" for p in DL_POSITIONS)
    return f"SELECT nfl_id, nfl_position FROM {players} WHERE nfl_position IN ({pos})"


def _write_a(con: duckdb.DuckDBPyConnection, cfg: Mapping, mode: str,
             src: Mapping[str, Path], out: Path) -> dict[str, Any]:
    players, tracking, results = (
        _csv(src["players"]), _csv(src["combine_tracking"]),
        _csv(src["combine_results"]),
    )
    dl = _dl_ids_sql(players)

    # Attempt-level frame counts, then drill-level roll-up.
    sql = f"""
    WITH dl AS ({dl}),
         t AS (
           SELECT c.nfl_id, c.drill_name, c.attempt,
                  CAST(c.draft_year AS INTEGER) AS draft_year,
                  COUNT(*) AS frames
           FROM {tracking} c JOIN dl USING (nfl_id)
           WHERE c.drill_name IS NOT NULL
           GROUP BY 1, 2, 3, 4
         ),
         a AS (
           SELECT nfl_id, drill_name, COUNT(*) AS n_attempts, SUM(frames) AS n_rows
           FROM t GROUP BY 1, 2
         ),
         agg AS (
           SELECT drill_name,
                  SUM(n_rows)::BIGINT AS n_rows,
                  COUNT(*)::BIGINT AS n_players,
                  SUM(CASE WHEN n_attempts >= 2 THEN 1 ELSE 0 END)::BIGINT
                      AS n_two_attempt_players,
                  SUM(CASE WHEN n_attempts = 1 THEN 1 ELSE 0 END)::BIGINT
                      AS n_one_attempt_players
           FROM a GROUP BY 1
         ),
         ff AS (
           SELECT drill_name,
                  MIN(frames)::BIGINT AS frames_min,
                  approx_quantile(frames, 0.5)::BIGINT AS frames_median,
                  MAX(frames)::BIGINT AS frames_max
           FROM t GROUP BY 1
         ),
         cy AS (
           SELECT drill_name,
                  string_agg(DISTINCT CAST(draft_year AS VARCHAR), ','
                             ORDER BY CAST(draft_year AS VARCHAR)) AS class_years
           FROM t GROUP BY 1
         )
    SELECT agg.drill_name, agg.n_rows, agg.n_players, agg.n_two_attempt_players,
           agg.n_one_attempt_players, ff.frames_min, ff.frames_median,
           ff.frames_max, cy.class_years
    FROM agg JOIN ff USING (drill_name) JOIN cy USING (drill_name)
    ORDER BY agg.drill_name
    """
    drill_rows = con.execute(sql).fetchall()
    drill_cols = [d[0] for d in con.description]

    rows: list[dict[str, Any]] = []
    for r in drill_rows:
        rec = dict(zip(drill_cols, r))
        rec.update(field="", non_null_rate="")
        rows.append(rec)

    # One row per combine_results column, restricted to the DL cohort.
    cols = [d[0] for d in con.execute(f"DESCRIBE SELECT * FROM {results}").fetchall()]
    for col in cols:
        rate = con.execute(
            f"""SELECT AVG(CASE WHEN r.{col} IS NOT NULL THEN 1.0 ELSE 0 END)
                FROM {results} r JOIN ({dl}) d USING (nfl_id)"""
        ).fetchone()[0]
        rows.append({"drill_name": "", "field": col,
                     "non_null_rate": round(float(rate), 4) if rate is not None else ""})

    dl_common.write_csv(out / "a_drills.csv", rows, A_DRILL_FIELDS, mode=mode, cfg=cfg)
    return {"drill_rows": len(drill_rows), "result_cols": len(cols)}


def _write_b(con: duckdb.DuckDBPyConnection, cfg: Mapping, mode: str,
             src: Mapping[str, Path], out: Path) -> dict[str, Any]:
    players, pp = _csv(src["players"]), _csv(src["player_play"])
    dl = _dl_ids_sql(players)

    sql = f"""
    WITH dl AS ({dl}),
         p AS (
           SELECT CAST(game_id AS VARCHAR) || '-' || CAST(play_id AS VARCHAR) AS pk,
                  game_id, play_id, team_abbr, possession_team, nfl_id,
                  blitzing, time_to_pressure
           FROM {pp} WHERE nfl_id IN (SELECT nfl_id FROM dl)
         ),
         team AS (SELECT nfl_id, any_value(team_abbr) AS team_abbr FROM p GROUP BY 1),
         tdp AS (
           SELECT t.nfl_id, COUNT(DISTINCT q.pk)::BIGINT AS team_def_plays
           FROM team t JOIN p q ON q.team_abbr = t.team_abbr
                            AND q.team_abbr <> q.possession_team
           GROUP BY 1
         ),
         agg AS (
           SELECT nfl_id,
                  COUNT(*)::BIGINT AS n_rows,
                  SUM(CASE WHEN blitzing IS TRUE THEN 1 ELSE 0 END)::BIGINT
                      AS n_rush_rows,
                  COUNT(DISTINCT game_id)::BIGINT AS n_games,
                  COUNT(DISTINCT pk)::BIGINT AS n_plays,
                  SUM(CASE WHEN time_to_pressure IS NOT NULL THEN 1 ELSE 0 END)::BIGINT
                      AS pressure_rows
           FROM p GROUP BY 1
         )
    SELECT d.nfl_id, d.nfl_position,
           CASE WHEN d.nfl_position = 'DE' THEN 'edge' ELSE 'interior' END,
           COALESCE(a.n_rows, 0), COALESCE(a.n_rush_rows, 0),
           COALESCE(a.n_games, 0), COALESCE(a.n_plays, 0),
           COALESCE(tdp.team_def_plays, 0),
           CASE WHEN COALESCE(tdp.team_def_plays, 0) = 0 THEN NULL
                ELSE ROUND(COALESCE(a.n_rows, 0)::DOUBLE / tdp.team_def_plays, 4) END,
           COALESCE(a.pressure_rows, 0)
    FROM dl d
    LEFT JOIN agg a USING (nfl_id)
    LEFT JOIN tdp USING (nfl_id)
    ORDER BY d.nfl_id
    """
    rows = [dict(zip(B_FIELDS, r)) for r in con.execute(sql).fetchall()]
    dl_common.write_csv(out / "b_matched.csv", rows, B_FIELDS, mode=mode, cfg=cfg)

    matched = [r for r in rows if r["n_rows"] > 0]
    rush = sorted(r["n_rush_rows"] for r in matched)
    n = len(rush)
    if n == 0:
        med = 0
    elif n % 2:
        med = rush[n // 2]
    else:
        med = (rush[n // 2 - 1] + rush[n // 2]) / 2
    return {
        "n_dl": len(rows),
        "n_matched": len(matched),
        "median_rush_rows": med,
        "min_rush_rows": rush[0] if rush else 0,
        "max_rush_rows": rush[-1] if rush else 0,
        "n_with_100plus_rush_rows": sum(1 for x in rush if x >= 100),
    }


def run(cfg: Mapping, mode: str) -> dict:
    """Run both audits and return the summary dict."""
    src = {k: dl_common.source_paths(cfg, k)[0]
           for k in ("players", "combine_tracking", "combine_results", "player_play")}
    paths = dl_common.resolve_paths(cfg, mode)
    out = Path(paths["outputs"]) / "audit"
    out.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        a = _write_a(con, cfg, mode, src, out)
        b = _write_b(con, cfg, mode, src, out)
    finally:
        con.close()
    return {**b, **a}
