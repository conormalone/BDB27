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
* **``drive_id``** is derived here, from the possession sequence of the play
  list (``player_play``'s distinct plays), never from play order inside the
  snap table (D71/D72).  Plays are ordered by ``play_id`` within a game, which
  is validated against the quarter/clock order (the inversions are counted).
  A drive starts on the first observed play, on a change of
  ``possession_team``, after a play whose possession is unknown, and after a
  score change (any score ends a possession, so a same-possession score change
  proves at least one unobserved drive in between).  A play whose possession
  is missing or conflicting gets ``drive_id = NA`` and is counted; the next
  play starts a new drive.  The column feeds ``dl_load.add_load_columns`` and
  is dropped before emission — the emitted schema is unchanged.

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
from dl import dl_blockers as B

__all__ = ["build_snaps", "SNAP_COLUMNS", "LOAD_COLUMNS", "DERIVED_CONSTANTS"]

#: Column order of the emitted table.  ``LOAD_COLUMNS`` is appended only when
#: the load module supplies them (see :func:`_apply_load_module`).
SNAP_COLUMNS: tuple[str, ...] = (
    "game_id", "play_id", "nfl_id", "position_group", "season", "week",
    "opponent_team", "event", "time_to_pressure", "censor_time",
    "quick_pressure", "sack", "unblocked_pressure", "player_get_off",
    "alignment", "pre_snap_depth_yd", "blitz", "n_rushers", "n_blockers",
    "stunt", "primary_blocker_group", "primary_blocker_group_source",
    "in_game_speed_1_0", "in_game_speed_2_0", "absolute_load",
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

#: Long-form reasons for the columns the provided data cannot support.  The
#: first four are now *derived* from all-22 tracking (``notes/dl-design.md``
#: §5.4a): they stay ``NA`` only where the tracking does not carry enough
#: players, and the sample cannot exercise them.
NOT_DERIVABLE: dict[str, str] = {
    "blitz": ("derived from all-22 tracking as n_rushers > "
              "blitz_rusher_threshold (§5.4a); NA wherever the tracking does "
              "not carry at least all22_min_players distinct players at the "
              "snap frame. Never from player_play.blitzing (a per-player rush "
              "assignment, notes/dl-evidence.md §3). The sample tracks 1-4 "
              "players per play, so it cannot exercise this column."),
    "n_rushers": ("derived from all-22 tracking: defensive players advancing "
                  ">= rush_advance_min_yd inside post_snap_window_s (§5.4a); "
                  "NA where the tracking does not carry enough players at the "
                  "snap frame. The sample cannot exercise it."),
    "n_blockers": ("derived from all-22 tracking: stay-in protectors engaged "
                   "with a rusher (§5.4a); NA where the tracking does not "
                   "carry enough players at the snap frame. The sample cannot "
                   "exercise it."),
    "primary_blocker_group": (
        "derived from all-22 tracking: the plurality primary blocker's group "
        "per play (§5.4a), roster position first and alignment geometry "
        "otherwise; NA where the tracking does not carry enough players at "
        "the snap frame. The sample cannot exercise it."),
    "stunt": ("no stunt label exists in the provided data and none is inferable "
              "without full-frame rusher/blocker tracks"),
    "absolute_load": "computed by the load module (dl_load), not by this one",
    "relative_load": "computed by the load module (dl_load), not by this one",
}

#: Reason strings for the drive key, carried in the summary.
DRIVE_RULE: str = (
    "per game, in play_id order (validated against quarter/clock order): a new "
    "drive starts on the first observed play, on a change of possession_team, "
    "after a play whose possession is unknown, and after a score change"
)

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
# Drive key (possession sequence over the play list)
# --------------------------------------------------------------------------- #
def _drive_sequence(con, summary: dict) -> None:
    """Create the per-play ``play_drive`` view and record its diagnostics.

    The input is ``pp`` (every ``player_play`` row), so the play list is the
    cohort-observed list — the only play list the provided data contains.  A
    play can therefore be absent, and a possession change inside that gap is
    invisible; the score-change boundary below catches every gap that contains
    a score, and the remaining exposure is counted and reported, never guessed.
    """
    con.execute("""
        CREATE OR REPLACE TEMP VIEW play_poss AS
        SELECT game_id, play_id,
               min(possession_team) AS poss,
               count(DISTINCT possession_team) AS n_poss,
               min(quarter) AS play_quarter,
               min(game_clock) AS play_clock,
               min(pre_snap_home_score) AS play_home_score,
               min(pre_snap_visitor_score) AS play_visitor_score,
               count(*) AS n_rows
        FROM pp
        GROUP BY game_id, play_id
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW play_drive AS
        WITH marked AS (
            SELECT *,
                   CASE WHEN n_poss = 1 THEN poss END AS known_poss,
                   lag(CASE WHEN n_poss = 1 THEN poss END)
                       OVER (PARTITION BY game_id ORDER BY play_id)
                       AS prev_poss,
                   lag(play_home_score)
                       OVER (PARTITION BY game_id ORDER BY play_id)
                       AS prev_home,
                   lag(play_visitor_score)
                       OVER (PARTITION BY game_id ORDER BY play_id)
                       AS prev_visitor,
                   row_number() OVER (PARTITION BY game_id ORDER BY play_id)
                       AS rn
            FROM play_poss
        ),
        flagged AS (
            SELECT *,
                   CASE
                     WHEN rn = 1 THEN 'first_play'
                     WHEN n_poss <> 1 THEN 'unknown_possession'
                     WHEN prev_poss IS NULL THEN 'after_unknown_possession'
                     WHEN known_poss IS DISTINCT FROM prev_poss
                       THEN 'possession_change'
                     WHEN play_home_score IS DISTINCT FROM prev_home
                       OR play_visitor_score IS DISTINCT FROM prev_visitor
                       THEN 'score_change_same_possession'
                     ELSE NULL
                   END AS boundary
            FROM marked
        )
        SELECT *,
               CASE WHEN n_poss <> 1 THEN NULL
                    ELSE sum(CASE WHEN boundary IS NOT NULL THEN 1 ELSE 0 END)
                         OVER (PARTITION BY game_id ORDER BY play_id) - 1
               END AS drive_id
        FROM flagged
    """)

    stats = con.execute("""
        SELECT count(*) AS n_plays,
               count(DISTINCT game_id) AS n_games,
               count(*) FILTER (WHERE n_poss = 0) AS n_missing_possession,
               count(*) FILTER (WHERE n_poss > 1) AS n_conflicting_possession,
               count(*) FILTER (WHERE play_home_score IS NULL
                                   OR play_visitor_score IS NULL)
                   AS n_missing_score,
               count(*) FILTER (WHERE play_quarter IS NULL
                                   OR play_clock IS NULL)
                   AS n_missing_clock
        FROM play_poss
    """).fetchone()
    boundaries = dict(con.execute("""
        SELECT boundary, count(*) FROM play_drive
        WHERE boundary IS NOT NULL GROUP BY 1 ORDER BY 1
    """).fetchall())
    n_drives = con.execute("""
        SELECT count(*) FROM (
            SELECT DISTINCT game_id, drive_id FROM play_drive
            WHERE drive_id IS NOT NULL)
    """).fetchone()[0]
    per_game = con.execute("""
        SELECT min(n), median(n), max(n) FROM (
            SELECT game_id, count(DISTINCT drive_id) AS n FROM play_drive
            WHERE drive_id IS NOT NULL GROUP BY 1)
    """).fetchone()

    # ``_clock_seconds_sql`` reads a column named ``game_clock``; the play
    # table names it ``play_clock`` to stay unambiguous next to ``prim``.
    play_src = "(SELECT *, play_clock AS game_clock FROM play_poss)"
    clock = _clock_seconds_sql(con, play_src)
    inversions = con.execute(f"""
        WITH c AS (
            SELECT game_id, play_id, play_quarter,
                   CAST({clock} AS DOUBLE) AS clk
            FROM {play_src}
        ),
        x AS (
            SELECT game_id, play_id, clk,
                   lag(play_id) OVER (PARTITION BY game_id
                                      ORDER BY play_quarter, clk DESC NULLS LAST,
                                               play_id) AS prev_pid,
                   lag(clk) OVER (PARTITION BY game_id
                                  ORDER BY play_quarter, clk DESC NULLS LAST,
                                           play_id) AS prev_clk
            FROM c
        )
        SELECT count(*) FROM x
        WHERE prev_pid IS NOT NULL AND play_id < prev_pid
          AND clk IS NOT NULL AND prev_clk IS NOT NULL
    """).fetchone()[0]

    summary["drive"] = {
        "rule": DRIVE_RULE,
        "clock_order": "play_id ascending within each game",
        "n_plays_in_list": int(stats[0]),
        "n_games_in_list": int(stats[1]),
        "n_plays_missing_possession": int(stats[2]),
        "n_plays_conflicting_possession": int(stats[3]),
        "n_plays_missing_score": int(stats[4]),
        "n_plays_missing_clock": int(stats[5]),
        "n_clock_order_inversions": int(inversions),
        "boundaries": {k: int(v) for k, v in boundaries.items()},
        "n_drives": int(n_drives),
        "drives_per_game": {"min": int(per_game[0] or 0),
                            "median": float(per_game[1] or 0.0),
                            "max": int(per_game[2] or 0)},
    }


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

        # -- the drive key: possession sequence over the whole play list ------
        _drive_sequence(con, summary)

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
                CASE WHEN TRY_CAST(p.time_to_pressure AS DOUBLE) IS NOT NULL
                      AND TRY_CAST(p.time_to_pressure AS DOUBLE)
                          <= TRY_CAST(p.censor_time AS DOUBLE)
                     THEN 1 ELSE 0 END AS event,
                -- A column that is null on every row would otherwise be typed
                -- VARCHAR by the CSV sniffer and the comparison above would not
                -- even bind; the cast is the numeric contract, not a coercion
                -- of a value (a null stays a null).
                TRY_CAST(p.time_to_pressure AS DOUBLE) AS time_to_pressure,
                TRY_CAST(p.censor_time AS DOUBLE) AS censor_time,
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
                p.yardline_side, p.yardline_number, p.play_direction,
                d.drive_id
            FROM prim p LEFT JOIN gm g USING (game_id)
            LEFT JOIN play_drive d USING (game_id, play_id)
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
        # -- all-22 rusher / blocker derivation (§5.4a) ----------------------
        blockers = _blocker_columns(cfg, mode, files, con, "base", summary)
        df = df.merge(blockers, on=["game_id", "play_id", "nfl_id"], how="left")
        blocker_cols = list(B.BLOCKER_COLUMNS)
        summary["blockers"].update({
            "n_snaps_with_n_rushers": int(df["n_rushers"].notna().sum()),
            "n_snaps_with_primary_blocker_group": int(
                df["primary_blocker_group"].notna().sum()),
            "n_snaps_blockers_na": int(
                df[blocker_cols].isna().any(axis=1).sum()),
        })
        # helper columns used to derive the tracking features, never emitted
        df = df.drop(columns=[c for c in ("yardline_side", "yardline_number",
                                          "play_direction") if c in df.columns])

        # drive coverage, recorded before the helper column is dropped
        if "drive_id" in df.columns:
            summary["counts"]["n_snaps_with_drive_id"] = int(
                df["drive_id"].notna().sum())
            summary["counts"]["n_snaps_drive_na"] = int(df["drive_id"].isna().sum())

        # -- load module (lazy; omitted when it exposes no table hook) -------
        df = _apply_load_module(df, cfg, mode, summary)

        # -- types, NA columns, ordering ------------------------------------
        df = _finalise(df, summary)
        summary["crosscheck"] = _crosscheck(df, con, positions=dl_positions)
        summary["rows"] = int(len(df))
        summary["event_rate"] = float(df["event"].mean()) if len(df) else float("nan")
        summary["na_rate"] = {
            c: (float(df[c].isna().mean()) if len(df) else float("nan"))
            for c in df.columns
        }
        summary["columns"] = list(df.columns)
        # An empty table has no event rate to test (no game data is an edge
        # case the stage writes as a zero-row artefact, never a crash).
        if mode == "sample" and len(df):
            rate = summary["event_rate"]
            assert rate < 0.5, (
                f"event rate {rate:.3f} is not below 0.5 on {len(df)} snaps; "
                "prereg §5 forbids fitting on pressured snaps only")
        summary["checks"] = {
            "join_rowcount_unchanged": True,
            "event_rate_below_half": bool(
                len(df) and summary["event_rate"] < 0.5),
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


_BLOCKER_KEYS = ["game_id", "play_id", "nfl_id"]


def _blocker_columns(cfg: Mapping, mode: str, files: Mapping | None, con,
                     base: str, summary: dict) -> pd.DataFrame:
    """``n_rushers``, ``blitz``, ``n_blockers`` and the blocker group.

    Delegates the geometry to :func:`dl.dl_blockers.derive_blockers`; this
    function owns source resolution, the SQL coverage guard (a play is
    derivable only with ``>= all22_min_players`` distinct players at the snap
    frame), per-game chunking and the coverage accounting.  Returns an empty
    frame with the five columns when no tracking input exists or no play
    qualifies, and records why.
    """
    cols = list(B.BLOCKER_COLUMNS)
    empty = pd.DataFrame(columns=[*_BLOCKER_KEYS, *cols])
    block = D.dl(cfg)
    k = block["game"]
    min_players = int(k["all22_min_players"])
    tol = float(DERIVED_CONSTANTS["frame_match_tol_s"])
    chunk = int(DERIVED_CONSTANTS["games_per_chunk"])
    goal = float(DERIVED_CONSTANTS["field_goal_line_yd"])
    far = float(DERIVED_CONSTANTS["field_far_goal_line_yd"])
    null = block["runtime"].get("csv_null_marker", "NA")

    n_table_plays = int(con.execute(
        "SELECT count(*) FROM (SELECT DISTINCT game_id, play_id FROM "
        f"{base})").fetchone()[0])

    def _no_tracking(reason: str) -> pd.DataFrame:
        summary["omitted_columns"]["blockers"] = reason
        summary["blockers"] = {
            "n_plays_in_table": n_table_plays,
            "n_plays_with_all22": 0,
            "n_plays_scored": 0,
            "n_plays_derivable": 0,
            "n_plays_na": 0,
            "n_snaps_with_n_rushers": 0,
            "n_snaps_with_primary_blocker_group": 0,
            "n_snaps_blockers_na": 0,
            "reason_counts": {},
            "coverage_reason": reason,
            "sample_cannot_exercise": (
                "no tracking input: the derivation cannot be exercised at all"),
            "n_players_at_snap": {"min": 0, "median": 0.0, "max": 0},
            "label_source_counts": {"roster": 0, "geometry": 0},
            "qb_rule_misses": 0,
            "constants": {"all22_min_players": min_players,
                          "frame_tol_s": tol},
            "rule": ("§5.4a geometric proxy; columns stay NA without "
                     "all-22 tracking"),
        }
        return empty

    sources = _tracking(cfg, files, mode)
    missing = [str(p) for p in sources if not Path(p).exists()]
    if not sources:
        return _no_tracking("no tracking input")
    if missing:
        return _no_tracking(f"tracking input missing: {missing}")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE bframes AS
        SELECT game_id, play_id, nfl_id, time, x, y, event
        FROM {_csv(sources, ("game_id", "play_id", "nfl_id", "time", "x",
                             "y", "event"), null)}
        WHERE time IS NOT NULL AND x IS NOT NULL AND y IS NOT NULL
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW bsnaptime AS
        SELECT game_id, play_id, min(time) AS t_snap
        FROM bframes WHERE event = 'ball_snap'
        GROUP BY game_id, play_id
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE bframes_rel AS
        SELECT f.game_id, f.play_id, f.nfl_id,
               epoch(f.time) - epoch(s.t_snap) AS t_rel, f.x, f.y
        FROM bframes f JOIN bsnaptime s USING (game_id, play_id)
    """)
    # Coverage guard, in SQL, before any per-player geometry: a team-level
    # count is only meaningful when the snap frame carries the whole unit.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE bcover AS
        SELECT game_id, play_id, count(DISTINCT nfl_id) AS n_at_snap
        FROM bframes_rel WHERE abs(t_rel) <= {tol}
        GROUP BY game_id, play_id
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW bqual AS
        SELECT DISTINCT b.game_id, b.play_id
        FROM {base} b JOIN bcover c USING (game_id, play_id)
        WHERE c.n_at_snap >= {min_players}
    """)
    n_all22 = int(con.execute("SELECT count(*) FROM bqual").fetchone()[0])
    cover_stats = con.execute(f"""
        SELECT min(n_at_snap), median(n_at_snap), max(n_at_snap)
        FROM bcover c JOIN (SELECT DISTINCT game_id, play_id FROM {base}) b
          USING (game_id, play_id)
    """).fetchone()
    at_snap = {"min": int(cover_stats[0] or 0),
               "median": float(cover_stats[1] or 0.0),
               "max": int(cover_stats[2] or 0)}

    if n_all22 == 0:
        reason = (f"no play in the snap table carries >= {min_players} "
                  f"distinct tracked players at the snap frame "
                  f"(all22_min_players); the sample tracks 1-4 players per "
                  f"play, so the derivation cannot be exercised")
        summary["blockers"] = {
            "n_plays_in_table": n_table_plays,
            "n_plays_with_all22": 0,
            "n_plays_scored": 0,
            "n_plays_derivable": 0,
            "n_plays_na": 0,
            "n_snaps_with_n_rushers": 0,
            "n_snaps_with_primary_blocker_group": 0,
            "n_snaps_blockers_na": 0,
            "reason_counts": {},
            "coverage_reason": reason,
            "sample_cannot_exercise": "sample_cannot_exercise: " + reason,
            "n_players_at_snap": at_snap,
            "label_source_counts": {"roster": 0, "geometry": 0},
            "qb_rule_misses": 0,
            "constants": {"all22_min_players": min_players,
                          "frame_tol_s": tol},
            "rule": ("§5.4a geometric proxy; every play is NA because the "
                     "coverage guard rejected it"),
        }
        return empty

    plays = con.execute(f"""
        SELECT DISTINCT b.game_id, b.play_id,
               {goal} + b.yardline_number AS los_c1,
               {far} - b.yardline_number AS los_c2,
               b.play_direction
        FROM {base} b JOIN bqual q USING (game_id, play_id)
    """).fetch_df()
    players = con.execute(f"""
        SELECT DISTINCT b.game_id, b.play_id, b.nfl_id
        FROM {base} b JOIN bqual q USING (game_id, play_id)
    """).fetch_df()
    roster = con.execute("SELECT nfl_id, nfl_position FROM pl").fetch_df()

    games = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT game_id FROM bqual ORDER BY game_id").fetchall()]
    totals = {"n_plays_scored": 0, "n_plays_derivable": 0, "n_plays_na": 0,
              "qb_rule_misses": 0}
    reasons: dict[str, int] = {}
    sources_count = {"roster": 0, "geometry": 0}
    weighted_mean = 0.0
    n_weight = 0
    r_min: int | None = None
    r_max: int | None = None
    rule = ""
    constants: dict[str, Any] = {}
    parts: list[pd.DataFrame] = []

    for start in range(0, len(games), chunk):
        chunk_games = games[start:start + chunk]
        id_list = ", ".join(str(g) for g in chunk_games)
        if not id_list:
            continue
        chunk_set = set(chunk_games)
        players_chunk = players.loc[players["game_id"].isin(chunk_set)]
        if players_chunk.empty:
            continue
        frames = con.execute(f"""
            SELECT f.game_id, f.play_id, f.nfl_id, f.t_rel, f.x, f.y
            FROM bframes_rel f JOIN bqual q USING (game_id, play_id)
            WHERE f.game_id IN ({id_list})
        """).fetch_df()
        res, dsum = B.derive_blockers(
            block["game"], frames=frames, players=players_chunk, plays=plays,
            frame_tol_s=tol, roster=roster)
        parts.append(res[[*_BLOCKER_KEYS, *cols]])
        for key in totals:
            totals[key] += int(dsum.get(key, 0) or 0)
        for key, value in (dsum.get("reason_counts") or {}).items():
            reasons[key] = reasons.get(key, 0) + int(value)
        for key, value in (dsum.get("label_source_counts") or {}).items():
            sources_count[key] = sources_count.get(key, 0) + int(value)
        dist = dsum.get("n_rushers_distribution") or {}
        n_ok = int(dsum.get("n_plays_derivable", 0) or 0)
        if n_ok and dist.get("mean") is not None:
            weighted_mean += float(dist["mean"]) * n_ok
            n_weight += n_ok
            r_min = (int(dist["min"]) if r_min is None
                     else min(r_min, int(dist["min"])))
            r_max = (int(dist["max"]) if r_max is None
                     else max(r_max, int(dist["max"])))
        rule = dsum.get("rule", rule)
        constants = dsum.get("constants", constants)

    out = pd.concat(parts, ignore_index=True) if parts else empty
    out = out[[*_BLOCKER_KEYS, *cols]]
    for col in ("n_rushers", "blitz", "n_blockers"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    for col in ("primary_blocker_group", "primary_blocker_group_source"):
        out[col] = out[col].astype(object)

    summary["blockers"] = {
        **totals,
        "n_plays_in_table": n_table_plays,
        "n_plays_with_all22": n_all22,
        "reason_counts": dict(sorted(reasons.items())),
        "n_players_at_snap": at_snap,
        "n_rushers_distribution": {
            "mean": (weighted_mean / n_weight) if n_weight else None,
            "min": r_min,
            "max": r_max,
        },
        "label_source_counts": sources_count,
        "constants": constants,
        "rule": rule,
    }
    return out


_CROSSCHECK_FIELDS = ["check", "stratum", "value", "n", "status", "reason",
                      "note"]


def _crosscheck(df: pd.DataFrame, con=None,
                positions: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Cross-check rows built from the snap table and the ``pp`` view.

    Every entry is ``{"check", "stratum", "value", "n", "status", "reason",
    "note"}``; ``value`` is a share or mean, ``n`` the denominator, and
    ``status`` is ``OK`` or ``NOT_RUN`` with a reason.  There is no verdict
    column: a disagreement is reported, never adjudicated here.

    ``alignment_split`` compares **the same population on both sides**: the
    emitted DL pass-rush snaps (stage 03: ``players.nfl_position`` in the
    configured DL positions, ``blitzing`` TRUE, play carries a dropback)
    against that population rebuilt from ``player_play`` JOIN ``players.csv``
    on ``nfl_id``.  ``lined_up_position`` alone is a formation label, so
    filtering by it counts linebackers and other non-prospect defenders
    labelled EDGE; the ``players.csv`` join is what defines the DL cohort.
    The player_play side reports EDGE, INTERIOR_LINE and an explicit
    ``unmapped`` stratum (anything outside that pair), so no row is dropped
    silently; the check then verifies stage 03 preserved the population rather
    than comparing two different ones.  Shares on both sides use the total
    row count as the denominator, which makes the rows directly comparable.
    """
    rows: list[dict[str, Any]] = []

    def add(check: str, stratum: str, value, n: int, *,
            status: str = "OK", reason: str | None = None,
            note: str = "") -> None:
        rows.append({"check": check, "stratum": stratum, "value": value,
                     "n": int(n), "status": status, "reason": reason,
                     "note": note})

    if "alignment" not in df.columns:
        return rows
    n_total = int(len(df))
    snap_note = ("share of emitted DL pass-rush snaps by alignment (players."
                 "nfl_position in the configured DL positions, blitzing TRUE, "
                 "play carries a dropback); 'unmapped' is a lined_up_position "
                 "outside EDGE/INTERIOR_LINE and keeps alignment NA")
    align_masks = (
        ("edge", df["alignment"] == "edge"),
        ("interior", df["alignment"] == "interior"),
        ("unmapped", df["alignment"].isna()),
    )
    for align, mask in align_masks:
        n = int(mask.sum())
        add("alignment_split", f"snap_table:{align}",
            (n / n_total) if n_total else None, n,
            status="OK" if n_total else "NOT_RUN",
            reason=None if n_total else "the snap table has no rows",
            note=snap_note)

    cohort_note = ("same population rebuilt from player_play JOIN players.csv "
                   "on nfl_id (DL positions only, blitzing TRUE, play carries "
                   "a dropback); unmapped = lined_up_position outside "
                   "EDGE/INTERIOR_LINE, never dropped")
    cohort: dict[str, int] = {}
    cohort_error = None
    if con is None:
        cohort_error = "pp view not supplied to the cross-check helper"
    elif not positions:
        cohort_error = ("the DL position list was not supplied to the "
                        "cross-check helper")
    else:
        pos = ", ".join(f"'{p}'" for p in positions)
        try:
            # ``dplays`` is one row per play that carries a dropback, so the
            # semi join cannot multiply DL rows (the same play-level fact
            # stage 03 joins after de-duplicating to one row per play).
            for stratum, n in con.execute(f"""
                    WITH dplays AS (
                        SELECT DISTINCT game_id, play_id FROM pp
                        WHERE dropback_duration IS NOT NULL
                    )
                    SELECT CASE WHEN p.lined_up_position IN
                                     ('EDGE', 'INTERIOR_LINE')
                                THEN p.lined_up_position
                                ELSE 'unmapped' END AS stratum,
                           count(*)
                    FROM pp p
                    JOIN pl u USING (nfl_id)
                    JOIN dplays d USING (game_id, play_id)
                    WHERE u.nfl_position IN ({pos})
                      AND p.blitzing IS TRUE
                    GROUP BY 1 ORDER BY 1
                """).fetchall():
                cohort[str(stratum)] = int(n)
        except Exception as exc:  # noqa: BLE001 - views are optional here
            cohort_error = f"pp/pl views unavailable ({type(exc).__name__})"
    n_cohort = sum(cohort.values())
    for pos_name in ("EDGE", "INTERIOR_LINE", "unmapped"):
        n = int(cohort.get(pos_name, 0))
        add("alignment_split", f"player_play_rush_rows:{pos_name}",
            (n / n_cohort) if n_cohort else None, n,
            status="OK" if n_cohort else "NOT_RUN",
            reason=None if n_cohort else (cohort_error or
                                          "no player_play rush rows carry a "
                                          "dropback duration"),
            note=cohort_note)

    for align in ("edge", "interior"):
        sub = df.loc[df["alignment"] == align]
        n = int(len(sub))
        if not n:
            add("unblocked_pressure_by_alignment", align, None, 0,
                status="NOT_RUN", reason="no snaps in this alignment",
                note="share of rows with unblocked_pressure == 1")
            continue
        vals = pd.to_numeric(sub["unblocked_pressure"], errors="coerce")
        if vals.notna().any():
            add("unblocked_pressure_by_alignment", align,
                float((vals == 1).mean()), n,
                note="share of rows with unblocked_pressure == 1 "
                     "(player_play crowded-pressure flag)")
        else:
            add("unblocked_pressure_by_alignment", align, None, n,
                status="NOT_RUN",
                reason="unblocked_pressure is NA on every row in this cell",
                note="share of rows with unblocked_pressure == 1")

    # Unblocked pressures are the ones not attributable to the blocker, so
    # they should be rare in every position group; a group-level rate that is
    # not rare would point at a different flag being copied into the column.
    pg_note = ("share of rows with unblocked_pressure == 1; unblocked "
               "pressures are the ones not attributable to the blocker and "
               "are expected to be rare in every position group")
    if "position_group" not in df.columns:
        for group in (tuple(positions) if positions else ("DE", "DT", "NT")):
            add("unblocked_pressure_share_by_position_group", group, None, 0,
                status="NOT_RUN",
                reason="the snap table has no position_group column",
                note=pg_note)
    else:
        pg = df["position_group"].astype("string")
        for group in (tuple(positions) if positions else ("DE", "DT", "NT")):
            mask = (pg == group).fillna(False).to_numpy(dtype=bool)
            sub = df.loc[mask]
            n = int(len(sub))
            if not n:
                add("unblocked_pressure_share_by_position_group", group, None,
                    0, status="NOT_RUN",
                    reason="no snaps in this position group", note=pg_note)
                continue
            vals = pd.to_numeric(sub["unblocked_pressure"], errors="coerce")
            if vals.notna().any():
                add("unblocked_pressure_share_by_position_group", group,
                    float((vals == 1).mean()), n, note=pg_note)
            else:
                add("unblocked_pressure_share_by_position_group", group, None,
                    n, status="NOT_RUN",
                    reason="unblocked_pressure is NA on every row in this "
                           "cell", note=pg_note)

    blitz = pd.to_numeric(df.get("blitz"), errors="coerce")
    if blitz is None or blitz.notna().sum() == 0:
        reason = ("derived blitz is NA in sample mode (1-4 tracked players "
                  "per play)")
        for level in (0, 1):
            add("pressure_rate_by_blitz", f"blitz={level}", None, 0,
                status="NOT_RUN", reason=reason,
                note="mean(event) by derived team blitz")
    else:
        event = pd.to_numeric(df.get("event"), errors="coerce")
        for level in (0, 1):
            mask = blitz == level
            n = int(mask.sum())
            if not n:
                add("pressure_rate_by_blitz", f"blitz={level}", None, 0,
                    status="NOT_RUN", reason="no snaps in this stratum",
                    note="mean(event) by derived team blitz")
                continue
            add("pressure_rate_by_blitz", f"blitz={level}",
                float(event[mask].mean()), n,
                note="mean(event) by derived team blitz")
    return rows


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

#: String columns whose missing value must be ``pd.NA`` (never ``"NA"``/``""``).
_TEXT_NA_COLUMNS = ("primary_blocker_group", "primary_blocker_group_source")


def _numeric(series: pd.Series, dtype: str) -> pd.Series:
    """Coerce to a pandas numeric dtype, treating every NA flavour as missing."""
    obj = series.astype(object)
    obj = obj.where(pd.notna(obj), np.nan)
    num = pd.to_numeric(obj, errors="coerce")
    return num.astype(dtype)


def _finalise(df: pd.DataFrame, summary: dict) -> pd.DataFrame:
    """Add NA columns, fix dtypes, and restore the declared column order."""
    # ``drive_id`` is an internal key: it feeds the load module's
    # ``snaps_in_drive`` and is never part of the emitted schema.
    if "drive_id" in df.columns:
        df = df.drop(columns=["drive_id"])
    for col in _NA_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
        else:
            df[col] = _numeric(df[col], "float64")
    for col in _TEXT_NA_COLUMNS:
        if col not in df.columns:
            df[col] = pd.Series(pd.NA, index=df.index, dtype=object)
            continue
        obj = df[col].astype(object)
        obj = obj.where(~obj.isna(), pd.NA)
        obj = obj.map(lambda v: pd.NA if isinstance(v, str)
                      and v.strip() in ("", "NA", "nan", "None") else v)
        df[col] = obj.astype(object)
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
