"""dl_snaps — the DL pass-rush snap table (design §5.4, prereg §4–§6).

One row per DL pass-rush snap of a dropback play.  The module is the single
code path for both ``--mode sample`` and ``--mode full``: every count it
excludes is reported back in the summary mapping, and every field the data
cannot support is left ``NA`` (never zero-filled) with an explicit
``not_derivable_reason``.

Load-bearing rules, each enforced here rather than merely documented
--------------------------------------------------------------------
* **Primary set** = DL rows (``players.nfl_position`` in DE/DT/NT) with
  ``blitzing == 'TRUE'`` — a per-player rush assignment, *not* a team blitz
  flag (``notes/dl-evidence.md`` §3).  ``blitzing == 'FALSE'`` rows are the
  coverage exploratory stratum: kept out of the table, counted in the summary.
* **Play facts are OL-side columns.**  ``dropback_duration`` and
  ``pressure_allowed`` are 100 % null on DL rows and populated on the OL rows,
  so they are joined at ``(game_id, play_id)`` *after* de-duplicating to one
  row per play.  A naive join to ``player_play`` inflates DL rows
  (17,481 -> 25,088 on the sample); :func:`build_snaps` asserts the row count is
  unchanged by the join.
* **Censoring** (prereg §5): ``censor_time = dropback_duration``; a snap with a
  null censor time is excluded and counted; ``time_to_pressure >
  censor_time`` is set to ``event = 0`` and counted as an inconsistency.
* **Covariates that need tracking** (pre-snap depth, in-game speed at
  snap+1.0 s / +2.0 s) are derived per game chunk where frames exist, else
  ``NA`` + counted.
* **Not derivable in the sample** — team ``blitz``, ``n_rushers``,
  ``n_blockers``, ``stunt`` — stay ``NA`` with a reason string.
* **Load columns** are the load module's job.  ``src/dl/dl_load.py`` exposes
  ``add_load_columns(frame, cfg, mode)``, which owns ``LOAD_COLUMNS``; this
  module only delegates.  The flat ``absolute_load`` / ``relative_load`` stay
  ``NA`` until a threshold is frozen inside a training fold (prereg §7), which
  the load module enforces.  The hook is looked for lazily, never imported at
  module import time, so a broken load module cannot break import of this one.

Determinism: rows are sorted by ``(game_id, play_id, nfl_id)`` and every value
comes from a single-threaded DuckDB pass with an explicit column list.  No
whole-file pandas read.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from dl import dl_common as D

__all__ = ["build_snaps", "SNAP_COLUMNS", "LOAD_COLUMNS", "DERIVED_CONSTANTS"]

#: Column order of the emitted table.  ``LOAD_COLUMNS`` is appended only when
#: the load module supplies them (see :func:`_apply_load_module`).
SNAP_COLUMNS: tuple[str, ...] = (
    "game_id", "play_id", "nfl_id", "position_group", "season", "week",
    "opponent_team", "event", "time_to_pressure", "censor_time",
    "quick_pressure", "sack", "unblocked_pressure", "player_get_off",
    "alignment", "pre_snap_depth_yd", "blitz", "n_rushers", "n_blockers",
    "stunt", "in_game_speed_1_0", "in_game_speed_2_0", "absolute_load",
    "relative_load", "total_distance_before", "snaps_in_drive",
    "seconds_since_last_snap", "down", "yards_to_go", "quarter",
    "game_clock_s", "score_margin", "garbage_time",
)

#: Columns the load module owns (never computed here).
LOAD_COLUMNS: tuple[str, ...] = (
    "absolute_load", "relative_load", "total_distance_before",
    "snaps_in_drive", "seconds_since_last_snap",
)

#: Derived-feature constants.  These are *measurement* constants (frame
#: matching geometry), not analysis knobs: they are named here so the summary
#: and the tests can cite them, and none of them is tuned to a result.
DERIVED_CONSTANTS: dict[str, Any] = {
    # distance from the snap frame within which a frame counts as "at +t s"
    # 10 Hz tracking => 0.1 s spacing; 0.15 s admits one dropped frame, no more.
    "frame_match_tol_s": 0.15,
    # games processed per DuckDB pass (per-game chunking, bounded memory)
    "games_per_chunk": 50,
    # x-coordinate of the line of scrimmage: the field frame is 0-120 yd with
    # 10-yd end zones.  ``yardline_side`` names the team whose goal line the
    # ball is measured from, and that goal line sits at x=110 when the team is
    # the HOME team and at x=10 when it is the VISITOR; ``x`` therefore runs
    # 10..110 downfield.  Validated against snap-time tracking (median 0.5 yd
    # from the defenders' snap x on the sample).
    "field_goal_line_yd": 10.0,
    "field_far_goal_line_yd": 110.0,
}

#: Long-form reasons for the columns the provided data cannot support.
NOT_DERIVABLE: dict[str, str] = {
    "blitz": ("player_play.blitzing is a per-player rush assignment, not a team "
              "blitz flag (notes/dl-evidence.md §3); team blitz needs all 11 "
              "defenders at ball_snap, which player_play does not enumerate"),
    "n_rushers": ("needs every defender on the play from tracking; the sample "
                  "tracks only a subset of players per play"),
    "n_blockers": ("needs rusher-blocker assignment, which no file provides "
                   "(notes/dl-evidence.md §5)"),
    "stunt": ("no stunt label exists in the provided data and none is inferable "
              "without full-frame rusher/blocker tracks"),
    "absolute_load": "computed by the load module (dl_load), not by this one",
    "relative_load": "computed by the load module (dl_load), not by this one",
}

_ALIGNMENT_MAP = {"EDGE": "edge", "INTERIOR_LINE": "interior"}

_PP_COLUMNS = (
    "game_id", "play_id", "nfl_id", "team_abbr", "possession_team",
    "lined_up_position", "quarter", "down", "yards_to_go", "game_clock",
    "pre_snap_home_score", "pre_snap_visitor_score", "yardline_side",
    "yardline_number", "play_direction", "dropback_duration",
    "pressure_allowed", "quick_pressure", "unblocked_pressure", "blitzing",
    "player_get_off", "sack", "time_to_pressure",
)


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #
def _csv(paths: Sequence[Path], columns: Iterable[str],
         null_marker: str = "NA") -> str:
    """A ``read_csv_auto`` subquery projecting an explicit column list."""
    files = [Path(p) for p in paths]
    lst = "[" + ", ".join(f"'{p.as_posix()}'" for p in files) + "]"
    if len(files) == 1:
        base = f"read_csv_auto({lst[1:-1]}, nullstr=['{null_marker}'])"
    else:
        base = (f"read_csv_auto({lst}, nullstr=['{null_marker}'], "
                f"union_by_name=true)")
    select = ", ".join(f'"{c}"' for c in columns)
    return f"(SELECT {select} FROM {base})"


def _one(cfg: Mapping, key: str, files: Mapping | None, mode: str) -> list[Path]:
    """Resolve a single-file source, honouring a ``files`` override."""
    if files is not None and key in files:
        value = files[key]
        return [] if value in (None, [], ()) else [Path(value)]
    return list(D.source_paths(cfg, key, mode))


def _tracking(cfg: Mapping, files: Mapping | None, mode: str) -> list[Path]:
    if files is not None and "tracking" in files:
        value = files["tracking"]
        if value in (None, [], ()):
            return []
        return [Path(value)] if isinstance(value, (str, Path)) else [
            Path(p) for p in value]
    return [p for p in D.tracking_sources(cfg, mode)]


def _clock_seconds_sql(con, pplay_sql: str) -> str:
    """SQL expression for ``game_clock`` (``mm:ss`` remaining) in seconds.

    The column is **minutes:seconds**, but ``read_csv_auto`` types ``"08:42"``
    as the TIME ``08:42:00`` — so the TIME *hour* part carries minutes and the
    TIME *minute* part carries seconds.  Reading it as hours (``*3600``)
    inflates the clock by 60x, which would also inflate every rest gap and
    silently break the 300 s load half-life.  A TEXT fallback is handled so the
    expression survives a schema sniff that guesses VARCHAR.
    """
    rows = con.execute(f"DESCRIBE SELECT * FROM {pplay_sql}").fetchall()
    types = {r[0]: str(r[1]).upper() for r in rows}
    kind = types.get("game_clock", "")
    if "TIME" in kind and "STAMP" not in kind:
        return ("(date_part('hour', game_clock) * 60.0 "
                "+ date_part('minute', game_clock) "
                "+ date_part('second', game_clock) / 60.0)")
    return ("TRY_CAST(date_part('hour', TRY_CAST(game_clock AS TIME)) * 60.0 "
            "+ date_part('minute', TRY_CAST(game_clock AS TIME)) "
            "+ date_part('second', TRY_CAST(game_clock AS TIME)) / 60.0 "
            "AS DOUBLE)")


# --------------------------------------------------------------------------- #
# Main builder
# --------------------------------------------------------------------------- #
def build_snaps(cfg: Mapping, mode: str = "sample", *,
                files: Mapping | None = None,
                con=None,
                with_summary: bool = False):
    """Build the DL snap table for ``mode``.

    Parameters
    ----------
    cfg : mapping
        Parsed ``config.yaml`` (the ``bdb27_dl`` block plus top-level ``game``).
    mode : ``sample`` | ``full``
        Provenance level; the event-rate assertion binds in sample mode.
    files : mapping, optional
        Source overrides keyed ``players``/``player_play``/``games``/
        ``tracking`` (a path, a list of paths, or ``None``/``[]`` for "absent").
        Used by the tests to point the same code path at tmp CSVs.
    con : duckdb connection, optional
        Reuse an existing connection (ownership stays with the caller).
    with_summary : bool
        Return ``(frame, summary)`` instead of just the frame.
    """
    block = D.dl(cfg)
    rt = block["runtime"]
    null = rt.get("csv_null_marker", "NA")
    q4_rule = block["game"]["garbage_time"]
    q4 = int(q4_rule["q4"])
    q4_margin = float(q4_rule["q4_margin"])
    any_margin = float(q4_rule["any_quarter_margin"])
    dl_positions = tuple(block["positions"]["dl"])

    own = con is None
    con = con if con is not None else D.duckdb_connect(cfg)
    summary: dict[str, Any] = {"mode": mode, "derived_constants": DERIVED_CONSTANTS,
                               "not_derivable": dict(NOT_DERIVABLE),
                               "omitted_columns": {}, "counts": {}}
    try:
        pp_src = _csv(_one(cfg, "player_play", files, mode), _PP_COLUMNS, null)
        pl_src = _csv(_one(cfg, "players", files, mode),
                      ("nfl_id", "nfl_position"), null)
        gm_src = _csv(_one(cfg, "games", files, mode),
                      ("game_id", "season", "week", "home_team_abbr",
                       "visitor_team_abbr"), null)
        pos = ", ".join(f"'{p}'" for p in dl_positions)

        con.execute(f"CREATE OR REPLACE TEMP VIEW pp AS SELECT * FROM {pp_src}")
        con.execute(f"CREATE OR REPLACE TEMP VIEW pl AS SELECT * FROM {pl_src}")
        con.execute(f"CREATE OR REPLACE TEMP VIEW gm AS SELECT * FROM {gm_src}")

        # -- play-level facts: ONE row per play, from the OL-side rows --------
        con.execute("""
            CREATE OR REPLACE TEMP VIEW plays AS
            SELECT game_id, play_id,
                   max(dropback_duration) AS dropback_duration,
                   max(CASE WHEN pressure_allowed THEN 1 ELSE 0 END)
                       AS pressure_allowed
            FROM pp
            WHERE dropback_duration IS NOT NULL
            GROUP BY game_id, play_id
        """)
        n_plays = con.execute("SELECT count(*) FROM plays").fetchone()[0]
        n_play_keys = con.execute(
            "SELECT count(*) FROM (SELECT DISTINCT game_id, play_id FROM plays)"
        ).fetchone()[0]
        if n_plays != n_play_keys:  # pragma: no cover - defensive
            raise RuntimeError(f"play de-duplication failed: {n_plays} rows for "
                               f"{n_play_keys} play keys")
        summary["counts"]["n_plays_with_dropback"] = int(n_plays)

        # -- every DL row, then the primary rush set ------------------------
        con.execute(f"""
            CREATE OR REPLACE TEMP VIEW dl_rows AS
            SELECT p.game_id, p.play_id, p.nfl_id,
                   u.nfl_position AS position_group,
                   p.blitzing, p.time_to_pressure, p.quick_pressure, p.sack,
                   p.unblocked_pressure, p.player_get_off, p.lined_up_position,
                   p.possession_team, p.team_abbr, p.quarter, p.down,
                   p.yards_to_go, p.game_clock, p.yardline_side,
                   p.yardline_number, p.play_direction,
                   p.pre_snap_home_score, p.pre_snap_visitor_score
            FROM pp p JOIN pl u USING (nfl_id)
            WHERE u.nfl_position IN ({pos})
        """)
        n_dl, n_rush, n_cov = con.execute(
            "SELECT count(*), "
            "sum(CASE WHEN blitzing THEN 1 ELSE 0 END), "
            "sum(CASE WHEN NOT blitzing THEN 1 ELSE 0 END) FROM dl_rows"
        ).fetchone()
        summary["counts"].update({
            "n_dl_rows": int(n_dl),
            "n_rush_rows": int(n_rush or 0),
            "n_coverage_rows_excluded": int(n_cov or 0),
        })

        con.execute("""
            CREATE OR REPLACE TEMP VIEW prim AS
            SELECT d.*, pl.dropback_duration AS censor_time,
                   pl.pressure_allowed
            FROM dl_rows d
            LEFT JOIN plays pl USING (game_id, play_id)
            WHERE d.blitzing
        """)
        n_prim = con.execute("SELECT count(*) FROM prim").fetchone()[0]
        # The play join must never multiply DL rows.
        if int(n_prim) != int(n_rush or 0):
            raise AssertionError(
                "play-fact join changed the DL row count: "
                f"{n_rush} rush rows -> {n_prim} after the (game_id, play_id) "
                "join; the play table was not de-duplicated")
        summary["counts"]["n_rush_rows_after_play_join"] = int(n_prim)

        n_censor_null = con.execute(
            "SELECT count(*) FROM prim WHERE censor_time IS NULL"
        ).fetchone()[0]
        summary["counts"]["n_excluded_null_censor"] = int(n_censor_null)
        summary["counts"]["n_excluded_coverage"] = int(n_cov or 0)

        clock = _clock_seconds_sql(con, "pp")
        q4_sql = str(q4)

        con.execute(f"""
            CREATE OR REPLACE TEMP VIEW base AS
            SELECT
                p.game_id, p.play_id, p.nfl_id,
                p.position_group,
                g.season, g.week,
                p.possession_team AS opponent_team,
                CASE WHEN p.time_to_pressure IS NOT NULL
                      AND p.time_to_pressure <= p.censor_time
                     THEN 1 ELSE 0 END AS event,
                p.time_to_pressure,
                p.censor_time,
                p.quick_pressure, p.sack, p.unblocked_pressure,
                p.player_get_off,
                CASE p.lined_up_position
                     WHEN 'EDGE' THEN 'edge'
                     WHEN 'INTERIOR_LINE' THEN 'interior'
                END AS alignment,
                p.down, p.yards_to_go, p.quarter,
                CAST({clock} AS DOUBLE) AS game_clock_s,
                CASE WHEN p.team_abbr = g.home_team_abbr
                     THEN p.pre_snap_home_score - p.pre_snap_visitor_score
                     ELSE p.pre_snap_visitor_score - p.pre_snap_home_score
                END AS score_margin,
                CASE WHEN (p.quarter >= {q4_sql} AND abs(
                          CASE WHEN p.team_abbr = g.home_team_abbr
                               THEN p.pre_snap_home_score - p.pre_snap_visitor_score
                               ELSE p.pre_snap_visitor_score - p.pre_snap_home_score
                          END) >= {q4_margin})
                       OR abs(
                          CASE WHEN p.team_abbr = g.home_team_abbr
                               THEN p.pre_snap_home_score - p.pre_snap_visitor_score
                               ELSE p.pre_snap_visitor_score - p.pre_snap_home_score
                          END) >= {any_margin}
                     THEN TRUE ELSE FALSE END AS garbage_time,
                p.yardline_side, p.yardline_number, p.play_direction
            FROM prim p LEFT JOIN gm g USING (game_id)
            WHERE p.censor_time IS NOT NULL
        """)
        n_base = con.execute("SELECT count(*) FROM base").fetchone()[0]
        summary["counts"]["n_rows"] = int(n_base)

        inconsistent = con.execute(
            "SELECT count(*) FROM base WHERE time_to_pressure > censor_time"
        ).fetchone()[0]
        summary["counts"]["n_ttp_gt_censor_event_set_to_0"] = int(inconsistent)
        n_unmapped = con.execute(
            "SELECT count(*) FROM base WHERE alignment IS NULL").fetchone()[0]
        summary["counts"]["n_alignment_unmapped"] = int(n_unmapped)

        df = con.execute(f"SELECT * FROM base ORDER BY game_id, play_id, nfl_id"
                         ).fetch_df()

        # -- tracking-derived columns ---------------------------------------
        track = _tracking_columns(cfg, mode, files, con, summary)
        df = df.merge(track, on=["game_id", "play_id", "nfl_id"], how="left") \
            if len(track) else df
        # helper columns used to derive the tracking features, never emitted
        df = df.drop(columns=[c for c in ("yardline_side", "yardline_number",
                                          "play_direction") if c in df.columns])

        # -- load module (lazy; omitted when it exposes no table hook) -------
        df = _apply_load_module(df, cfg, mode, summary)

        # -- types, NA columns, ordering ------------------------------------
        df = _finalise(df, summary)
        summary["rows"] = int(len(df))
        summary["event_rate"] = float(df["event"].mean()) if len(df) else float("nan")
        summary["na_rate"] = {
            c: (float(df[c].isna().mean()) if len(df) else float("nan"))
            for c in df.columns
        }
        summary["columns"] = list(df.columns)
        if mode == "sample":
            rate = summary["event_rate"]
            assert rate < 0.5, (
                f"event rate {rate:.3f} is not below 0.5 on {len(df)} snaps; "
                "prereg §5 forbids fitting on pressured snaps only")
        summary["checks"] = {
            "join_rowcount_unchanged": True,
            "event_rate_below_half": bool(summary["event_rate"] < 0.5),
            "columns_in_declared_order": list(df.columns) == [
                c for c in SNAP_COLUMNS if c in df.columns],
        }
    finally:
        if own:
            con.close()
    return (df, summary) if with_summary else df


# --------------------------------------------------------------------------- #
# Tracking features (per-game chunked)
# --------------------------------------------------------------------------- #
def _tracking_columns(cfg: Mapping, mode: str, files: Mapping | None, con,
                      summary: dict) -> pd.DataFrame:
    """``pre_snap_depth_yd``, ``in_game_speed_1_0``, ``in_game_speed_2_0``.

    Returns an empty frame (and a counted reason) when no tracking input is
    available or no DL snap has a snap frame.
    """
    sources = _tracking(cfg, files, mode)
    missing = [str(p) for p in sources if not Path(p).exists()]
    if not sources or missing:
        summary["omitted_columns"]["tracking_features"] = (
            "no tracking input" if not sources
            else f"tracking input missing: {missing}")
        return pd.DataFrame(columns=["game_id", "play_id", "nfl_id",
                                     "pre_snap_depth_yd", "in_game_speed_1_0",
                                     "in_game_speed_2_0"])

    null = D.dl(cfg)["runtime"].get("csv_null_marker", "NA")
    tol = float(DERIVED_CONSTANTS["frame_match_tol_s"])
    chunk = int(DERIVED_CONSTANTS["games_per_chunk"])
    goal = float(DERIVED_CONSTANTS["field_goal_line_yd"])
    far = float(DERIVED_CONSTANTS["field_far_goal_line_yd"])
    pos = ", ".join(f"'{p}'" for p in D.dl(cfg)["positions"]["dl"])

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE track AS
        SELECT game_id, play_id, nfl_id, time, x, y, s, event
        FROM {_csv(sources, ("game_id", "play_id", "nfl_id", "time", "x", "y",
                             "s", "event"), null)}
        WHERE time IS NOT NULL
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW snap AS
        SELECT game_id, play_id, nfl_id, min(time) AS t_snap,
               arg_min(x, time) AS x, arg_min(y, time) AS y
        FROM track WHERE event = 'ball_snap'
        GROUP BY game_id, play_id, nfl_id
    """)

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW tgt AS
        SELECT b.game_id, b.play_id, b.nfl_id, s.t_snap, s.x, b.yardline_side,
               b.yardline_number, b.play_direction
        FROM base b JOIN snap s USING (game_id, play_id, nfl_id)
        JOIN pl u ON u.nfl_id = b.nfl_id
        WHERE u.nfl_position IN ({pos}) AND s.x IS NOT NULL
    """)
    games = [r[0] for r in con.execute(
        "SELECT DISTINCT game_id FROM tgt ORDER BY game_id").fetchall()]
    summary["counts"]["n_tracking_plays_available"] = int(con.execute(
        "SELECT count(DISTINCT (game_id, play_id)) FROM tgt").fetchone()[0])

    parts: list[pd.DataFrame] = []
    for start in range(0, len(games), chunk):
        chunk_games = games[start:start + chunk]
        id_list = ", ".join(str(int(g)) for g in chunk_games)
        if not id_list:
            continue
        parts.append(con.execute(f"""
            WITH t AS (
                SELECT * FROM tgt WHERE game_id IN ({id_list})
            ),
            f AS (
                SELECT tr.game_id, tr.play_id, tr.nfl_id,
                       epoch(tr.time) - epoch(t.t_snap) AS dt, tr.s
                FROM track tr JOIN t USING (game_id, play_id, nfl_id)
            ),
            n1 AS (
                SELECT game_id, play_id, nfl_id,
                       arg_min(s, abs(dt - 1.0)) AS s1,
                       min(abs(dt - 1.0)) AS d1
                FROM f WHERE s IS NOT NULL GROUP BY 1, 2, 3
            ),
            n2 AS (
                SELECT game_id, play_id, nfl_id,
                       arg_min(s, abs(dt - 2.0)) AS s2,
                       min(abs(dt - 2.0)) AS d2
                FROM f WHERE s IS NOT NULL GROUP BY 1, 2, 3
            )
            SELECT t.game_id, t.play_id, t.nfl_id, t.x, t.yardline_number,
                   t.play_direction, t.yardline_side, g.home_team_abbr,
                   CASE WHEN n1.d1 <= {tol} THEN n1.s1 END AS in_game_speed_1_0,
                   CASE WHEN n2.d2 <= {tol} THEN n2.s2 END AS in_game_speed_2_0
            FROM t
            JOIN gm g USING (game_id)
            LEFT JOIN n1 USING (game_id, play_id, nfl_id)
            LEFT JOIN n2 USING (game_id, play_id, nfl_id)
        """).fetch_df())

    if not parts:
        summary["omitted_columns"]["tracking_features"] = "no tracked DL snaps"
        return pd.DataFrame(columns=["game_id", "play_id", "nfl_id",
                                     "pre_snap_depth_yd", "in_game_speed_1_0",
                                     "in_game_speed_2_0"])
    out = pd.concat(parts, ignore_index=True)
    out["pre_snap_depth_yd"] = _depth_from_candidates(out, goal, far)
    out = out.drop(columns=["x", "yardline_number", "play_direction",
                            "yardline_side", "home_team_abbr"])
    summary["counts"]["n_snaps_with_depth"] = int(out["pre_snap_depth_yd"].notna().sum())
    summary["counts"]["n_snaps_with_speed_1_0"] = int(out["in_game_speed_1_0"].notna().sum())
    summary["counts"]["n_snaps_with_speed_2_0"] = int(out["in_game_speed_2_0"].notna().sum())
    return out


def _depth_from_candidates(frame: pd.DataFrame, goal: float,
                           far: float) -> pd.Series:
    """Signed distance behind the line of scrimmage, in yards.

    ``yardline_side`` names the team the ball is measured from, but a team
    defends opposite end zones in different halves, so the line of scrimmage
    is one of exactly two x positions: ``goal + yardline_number`` or
    ``far - yardline_number`` (the two are identical at midfield).  The
    candidate is chosen per play as the one the tracked rushers actually line
    up at — defensive linemen are on the line, so the choice is unambiguous
    whenever the candidates are more than a few yards apart.  Selection is
    deterministic (argmin of the median absolute deviation; ties resolve to
    the first candidate in numeric order).
    """
    num = frame["yardline_number"]
    c1 = goal + num
    c2 = far - num
    x = frame["x"]
    dev1 = (x - c1).abs()
    dev2 = (x - c2).abs()
    key = frame["game_id"].astype(str) + "|" + frame["play_id"].astype(str)
    pick = (pd.DataFrame({"k": key, "d1": dev1, "d2": dev2})
            .groupby("k", sort=True)
            .median(numeric_only=True))
    use_c1 = (pick["d1"] <= pick["d2"])
    los = c1.where(key.map(use_c1), c2)
    sign = frame["play_direction"].map({"right": 1.0, "left": -1.0})
    depth = sign * (x - los)
    return depth.astype("float64")


# --------------------------------------------------------------------------- #
# Load module hook
# --------------------------------------------------------------------------- #
def _apply_load_module(df: pd.DataFrame, cfg: Mapping, mode: str,
                       summary: dict) -> pd.DataFrame:
    """Delegate the load / sequence columns to ``dl_load`` when it has a hook.

    The import is deliberately lazy and failure-tolerant: another job owns that
    module and may be mid-write.  Without ``add_load_columns`` the columns are
    omitted (and named in the summary) rather than filled with zeros.
    """
    hook = "add_load_columns"
    try:
        mod = importlib.import_module("dl.dl_load")
    except Exception as exc:  # noqa: BLE001 - missing/broken module => omit
        summary["omitted_columns"].update({
            c: f"dl_load unavailable ({type(exc).__name__}); load columns omitted "
               f"pending that module" for c in LOAD_COLUMNS})
        for c in ("absolute_load", "relative_load"):
            summary["omitted_columns"].setdefault(c, NOT_DERIVABLE[c])
        return df
    fn = getattr(mod, hook, None)
    if fn is None:
        summary["omitted_columns"].update({
            "total_distance_before": (f"dl_load has no table-level {hook}(); snap "
                                      f"sequences are not wired yet"),
            "snaps_in_drive": f"dl_load has no table-level {hook}()",
            "seconds_since_last_snap": f"dl_load has no table-level {hook}()",
        })
        for c in ("absolute_load", "relative_load"):
            summary["omitted_columns"].setdefault(c, NOT_DERIVABLE[c])
        return df
    out = fn(df.copy(), cfg, mode)
    missing = [c for c in LOAD_COLUMNS if c not in out.columns]
    if missing:
        raise RuntimeError(f"dl_load.{hook} returned no columns {missing}")
    return out


# --------------------------------------------------------------------------- #
# Finalisation
# --------------------------------------------------------------------------- #
_NA_COLUMNS = ("blitz", "n_rushers", "n_blockers", "stunt",
               "absolute_load", "relative_load", "pre_snap_depth_yd",
               "in_game_speed_1_0", "in_game_speed_2_0")


def _numeric(series: pd.Series, dtype: str) -> pd.Series:
    """Coerce to a pandas numeric dtype, treating every NA flavour as missing."""
    obj = series.astype(object)
    obj = obj.where(pd.notna(obj), np.nan)
    num = pd.to_numeric(obj, errors="coerce")
    return num.astype(dtype)


def _finalise(df: pd.DataFrame, summary: dict) -> pd.DataFrame:
    """Add NA columns, fix dtypes, and restore the declared column order."""
    for col in _NA_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
        else:
            df[col] = _numeric(df[col], "float64")
    for col in ("game_id", "play_id", "nfl_id", "season", "week", "event",
                "down", "yards_to_go", "quarter"):
        if col in df.columns:
            df[col] = _numeric(df[col], "Int64")
    for col in ("time_to_pressure", "censor_time", "player_get_off", "sack",
                "quick_pressure", "unblocked_pressure", "game_clock_s",
                "score_margin", "pre_snap_depth_yd", "in_game_speed_1_0",
                "in_game_speed_2_0"):
        if col in df.columns:
            df[col] = _numeric(df[col], "float64")
    if "garbage_time" in df.columns:
        df["garbage_time"] = df["garbage_time"].astype("boolean")
    order = [c for c in SNAP_COLUMNS if c in df.columns]
    order += [c for c in df.columns if c not in order]
    df = df[order].sort_values(["game_id", "play_id", "nfl_id"],
                               kind="mergesort").reset_index(drop=True)
    summary["columns_omitted_from_declared"] = [
        c for c in SNAP_COLUMNS if c not in df.columns]
    return df
