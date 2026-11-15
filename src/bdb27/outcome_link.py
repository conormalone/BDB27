"""Outcome-link candidate availability and pre-flight — shared by script and notebook.

Why this module exists
----------------------
The feasibility of route R1 (geometric proximity attribution) contains two
independent questions, and they were once conflated:

* **Can this dataset put an opposing route-runner on the same play as the turning
  DB at all?** — answerable from ``player_play.csv`` alone, over the full band
  population, with no frame-level data;
* **Is exactly one such opponent inside the match radius at the break frame?** —
  needs frame-level tracking and cannot be measured on this machine.

Everything here answers only the first question. It reads no tracking file,
accepts no tracking argument and imports no geometry: the outputs are counts of
rows in ``player_play``, turned into P1-P5 verdicts. The second question is the
notebook's §6; the frozen rule (the single opposing route-runner within 3 yd at
the break frame, two or more candidates dropped, no tie broken by radius,
distance or row order) is unchanged and is not applied here.

``player_play.csv`` carries rows only for the players named in ``players.csv``
(a draft-prospect cohort), so a play has as many rows as it has prospects on the
field. A low rows-per-play figure is a property of that cohort, not a threshold,
and no function here applies one.

Turn key
--------
A turn is ``(game_id, play_id, nfl_id, frame_start)``. ``frame_start`` is part of
the key because one DB can turn twice on one play: grouping on the three player
columns silently drops those second turns. :func:`check_turn_key` reports the
difference on a real table.

Public API
----------
:func:`connect`, :func:`load_player_play`, :func:`register_band_turns`,
:func:`candidate_table`, :func:`candidate_detail`, :func:`preflight`,
:func:`availability`, :func:`ambiguity_counts`, :func:`check_turn_key`.

No function here writes a file, and none reads a tracking file.
"""

from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any, Sequence

import duckdb
import pandas as pd

# ------------------------------------------------------------------ constants
#: The band, as in the rest of the project: degrees, left-closed.
BAND_LO, BAND_HI = 30.0, 60.0

#: The turn key. ``frame_start`` is not optional — see :func:`check_turn_key`.
TURN_KEY: tuple[str, ...] = ("game_id", "play_id", "nfl_id", "frame_start")

#: The candidate-status vocabulary, fixed by the spec so the artefacts agree.
CANDIDATE_STATUS: tuple[str, ...] = ("none_on_play", "single", "multiple")

#: The frozen match rule's radius, in yards. A design choice, not a result.
PRIMARY_RADIUS_YD = 3

#: The two gate thresholds the note pre-declared. Anchored here only so the
#: pre-flight and the notebook cannot disagree about them; neither is changed.
GATE_MIN_ATTRIBUTED_TURNS = 500
GATE_MIN_SHARE = 0.30

#: Default table names. Callers may override each one.
PP_TABLE = "ol_player_play"
BAND_TABLE = "ol_band_turns"
CANDIDATE_TABLE = "ol_candidates"
DETAIL_TABLE = "ol_candidate_detail"

#: Columns read from ``player_play.csv``. Explicit, never ``SELECT *`` — the raw
#: file is 147 MB and is never loaded whole into pandas.
PP_COLUMNS: tuple[str, ...] = (
    "game_id", "play_id", "nfl_id", "team_abbr", "lined_up_position",
    "route_ran", "target", "separation_at_pass_forward", "possession_team",
)

#: P5's wording. The counts are filled in from the measurement, never typed.
P5_WORDING = (
    "Cohort ceiling: a candidate is visible only if the player has a row in "
    "player_play.csv, and that file carries rows for the draft-prospect cohort in "
    "players.csv alone, so an opposing route-runner outside the cohort is invisible. "
    "{no_row:,} of {total:,} turns ({no_row_share:.1%}) have no opposing player row at all, "
    "and {no_route:,} ({no_route_share:.1%}) have opposing players recorded but no "
    "route-runner among them; on those turns the candidate count is limited by the "
    "cohort, not by the play. Every candidate count here is therefore a lower bound on "
    "what the full frame-level data would show, and the ceiling below is exact only for "
    "this file."
)


# ------------------------------------------------------------------ plumbing
def connect(memory_limit: str = "1200MB", threads: int = 2) -> duckdb.DuckDBPyConnection:
    """A scratch DuckDB connection under the project's memory rule."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'; SET threads={int(threads)}")
    return con


def _as_posix(path: str | Path) -> str:
    return Path(path).as_posix()


def load_player_play(con: duckdb.DuckDBPyConnection,
                     path: str | Path,
                     table: str = PP_TABLE,
                     extra: Sequence[tuple[str, str]] = ()) -> str:
    """Register ``player_play.csv`` as a typed table; return the table name.

    Two steps on purpose: a bare ``SELECT *`` with ``all_varchar`` stops DuckDB
    pushing a cast into the CSV reader (which re-infers types and fails on
    ``NA``); every column is then cast with ``TRY_CAST``.

    ``extra`` adds columns as ``(name, sql_type)`` pairs, each cast with
    ``TRY_CAST``. It is empty by default, so the base column set — and every
    caller that relies on it — is unchanged.
    """
    con.execute(
        f"CREATE OR REPLACE TABLE raw_{table} AS SELECT * FROM read_csv("
        f"'{_as_posix(path)}', header=true, all_varchar=true, nullstr=['NA',''])")
    extra_sql = "".join(f",\n      TRY_CAST({name} AS {sql_type}) AS {name}"
                        for name, sql_type in extra)
    con.execute(f"""
    CREATE OR REPLACE TABLE {table} AS
    SELECT
      CAST(game_id AS BIGINT) AS game_id, CAST(play_id AS BIGINT) AS play_id,
      CAST(nfl_id AS BIGINT) AS nfl_id, team_abbr, lined_up_position, route_ran,
      TRY_CAST(target AS BOOLEAN) AS target,
      TRY_CAST(separation_at_pass_forward AS DOUBLE) AS separation_at_pass_forward,
      possession_team{extra_sql}
    FROM raw_{table}
    """)
    return table


def register_band_turns(con: duckdb.DuckDBPyConnection,
                        parquet_path: str | Path,
                        table: str = BAND_TABLE,
                        *,
                        band: tuple[float, float] = (BAND_LO, BAND_HI),
                        setting: str = "game",
                        position_group: str = "DB") -> int:
    """Register the band turns of one turn table, on the four-column key."""
    lo, hi = band
    con.execute(f"""
    CREATE OR REPLACE TABLE {table} AS
    SELECT game_id, play_id, nfl_id, frame_start, abs_turn_angle
    FROM read_parquet('{_as_posix(parquet_path)}')
    WHERE valid AND setting = '{setting}' AND position_group = '{position_group}'
      AND abs_turn_angle >= {float(lo)} AND abs_turn_angle < {float(hi)}
    """)
    return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def candidate_table(con: duckdb.DuckDBPyConnection,
                    turns_table: str = BAND_TABLE,
                    table: str = CANDIDATE_TABLE,
                    population: str = "SAMPLE",
                    pp_table: str = PP_TABLE) -> pd.DataFrame:
    """One row per band turn: how many opposing route-running candidates it has.

    A **candidate** is a ``player_play`` row on the same ``(game_id, play_id)``
    whose ``team_abbr`` differs from the turning DB's own ``team_abbr`` and whose
    ``route_ran`` is not null. The count is a lower bound (see ``P5_WORDING``).

    ``pp_own_rows`` is 0 when the turn's own DB has no row on the play, and
    ``n_own_teams`` > 1 when the file labels one player with two teams; both are
    P1 conditions, and both leave the turn unsided.
    """
    con.execute(f"""
    CREATE OR REPLACE TABLE {table} AS
    WITH own AS (
      SELECT game_id, play_id, nfl_id,
             COUNT(*) AS pp_own_rows,
             MIN(team_abbr) FILTER (WHERE team_abbr IS NOT NULL) AS own_team,
             COUNT(DISTINCT team_abbr) AS n_own_teams
      FROM {pp_table}
      GROUP BY 1, 2, 3
    ),
    keys AS (
      SELECT DISTINCT game_id, play_id, nfl_id FROM {turns_table}
    ),
    cand AS (
      SELECT k.game_id, k.play_id, k.nfl_id,
             COUNT(c.nfl_id) FILTER (WHERE c.team_abbr IS NOT NULL
                                     AND c.team_abbr <> o.own_team) AS pp_opp_rows,
             COUNT(c.nfl_id) FILTER (WHERE c.team_abbr IS NOT NULL
                                     AND c.team_abbr <> o.own_team
                                     AND c.route_ran IS NOT NULL) AS pp_opp_route_rows,
             COUNT(c.nfl_id) FILTER (WHERE c.team_abbr IS NOT NULL
                                     AND c.team_abbr <> o.own_team
                                     AND c.route_ran IS NOT NULL
                                     AND c.separation_at_pass_forward IS NOT NULL)
               AS pp_opp_route_sep_rows
      FROM keys k
      JOIN own o
        ON k.game_id = o.game_id AND k.play_id = o.play_id AND k.nfl_id = o.nfl_id
      JOIN {pp_table} c
        ON k.game_id = c.game_id AND k.play_id = c.play_id AND c.nfl_id <> k.nfl_id
      GROUP BY 1, 2, 3
    )
    SELECT
      '{population}' AS population,
      t.game_id, t.play_id, t.nfl_id, t.frame_start,
      COALESCE(o.pp_own_rows, 0) AS pp_own_rows,
      o.own_team,
      COALESCE(o.n_own_teams, 0) AS n_own_teams,
      COALESCE(k.pp_opp_rows, 0) AS pp_opp_rows,
      COALESCE(k.pp_opp_route_rows, 0) AS pp_opp_route_rows,
      COALESCE(k.pp_opp_route_sep_rows, 0) AS pp_opp_route_sep_rows,
      CASE WHEN COALESCE(k.pp_opp_route_rows, 0) = 0 THEN 'none_on_play'
           WHEN COALESCE(k.pp_opp_route_rows, 0) = 1 THEN 'single'
           ELSE 'multiple' END AS pp_candidate_status
    FROM {turns_table} t
    LEFT JOIN own o
      ON t.game_id = o.game_id AND t.play_id = o.play_id AND t.nfl_id = o.nfl_id
    LEFT JOIN cand k
      ON t.game_id = k.game_id AND t.play_id = k.play_id AND t.nfl_id = k.nfl_id
    ORDER BY t.game_id, t.play_id, t.nfl_id, t.frame_start
    """)
    return con.execute(
        f"SELECT * FROM {table} ORDER BY game_id, play_id, nfl_id, frame_start").df()


def candidate_detail(con: duckdb.DuckDBPyConnection,
                     candidates_table: str = CANDIDATE_TABLE,
                     table: str = DETAIL_TABLE,
                     pp_table: str = PP_TABLE) -> pd.DataFrame:
    """One row per **candidate** (not per turn), in a fixed order.

    This is the candidate-level table the tie counts are read from: every
    candidate's position, route, separation and target flag sit beside the turn's
    status, so an ambiguity can be inspected without re-deriving it. Nothing here
    ranks, selects or prefers a candidate.
    """
    # Turn-level facts ride on every candidate row, so the tie counts are readable
    # from this file without regrouping.
    con.execute(f"""
    CREATE OR REPLACE TABLE {table} AS
    SELECT
      c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start,
      c.own_team, c.pp_candidate_status,
      ROW_NUMBER() OVER (
        PARTITION BY c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start
        ORDER BY p.nfl_id) AS candidate_rank,
      p.nfl_id AS candidate_nfl_id,
      p.lined_up_position AS candidate_position,
      p.route_ran AS candidate_route,
      p.separation_at_pass_forward AS candidate_separation,
      p.target AS candidate_target,
      COUNT(*) OVER (
        PARTITION BY c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start)
        AS n_candidates_on_turn,
      COUNT(p.separation_at_pass_forward) OVER (
        PARTITION BY c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start)
        AS n_candidates_with_separation,
      CASE WHEN COUNT(p.separation_at_pass_forward) OVER (
             PARTITION BY c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start) >= 2
           THEN MAX(p.separation_at_pass_forward) OVER (
             PARTITION BY c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start)
                - MIN(p.separation_at_pass_forward) OVER (
             PARTITION BY c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start)
      END AS turn_separation_gap_yd
    FROM {candidates_table} c
    JOIN {pp_table} p
      ON p.game_id = c.game_id AND p.play_id = c.play_id AND p.nfl_id <> c.nfl_id
    WHERE p.team_abbr IS NOT NULL AND p.team_abbr <> c.own_team
      AND p.route_ran IS NOT NULL
    ORDER BY c.population, c.game_id, c.play_id, c.nfl_id, c.frame_start,
             candidate_nfl_id
    """)
    return con.execute(f"SELECT * FROM {table} ORDER BY population, game_id, play_id, "
                       f"nfl_id, frame_start, candidate_nfl_id").df()


def check_turn_key(con: duckdb.DuckDBPyConnection,
                   turns_table: str = BAND_TABLE) -> dict:
    """Rows on the four-column key vs. the three-column key.

    The difference is the number of second turns a three-column grouping would
    silently drop. Never group on ``(game_id, play_id, nfl_id)``.
    """
    n4, n3 = con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT (game_id, play_id, nfl_id))
        FROM {turns_table}""").fetchone()
    return {"rows_turn_key": int(n4), "rows_player_key": int(n3),
            "second_turns_dropped_by_player_key": int(n4) - int(n3)}


# ------------------------------------------------------------------ measures
def availability(con: duckdb.DuckDBPyConnection,
                 pp_table: str = PP_TABLE,
                 players_table: str | None = None) -> dict:
    """What ``player_play.csv`` holds, as row counts — availability, not results.

    ``players_table``, when given, is a table with an ``nfl_id`` column read from
    ``players.csv``; it only adds how much of the cohort ``player_play`` covers.
    """
    rows, plays, players, games = con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT (game_id, play_id)),
               COUNT(DISTINCT nfl_id), COUNT(DISTINCT game_id)
        FROM {pp_table}""").fetchone()
    max_rows = con.execute(f"""
        SELECT MAX(n) FROM (
          SELECT COUNT(*) AS n FROM {pp_table} GROUP BY game_id, play_id)""").fetchone()[0]
    both = con.execute(f"""
        SELECT COUNT(*) FROM (
          SELECT game_id, play_id FROM {pp_table} GROUP BY 1, 2
          HAVING COUNT(*) FILTER (WHERE team_abbr = possession_team) > 0
             AND COUNT(*) FILTER (
                   WHERE team_abbr IS NOT NULL AND team_abbr <> possession_team) > 0)
        """).fetchone()[0]
    out: dict[str, Any] = {
        "pp_rows": int(rows),
        "pp_plays": int(plays),
        "pp_players": int(players),
        "pp_games": int(games),
        "pp_rows_per_play_mean": round(int(rows) / int(plays), 6) if plays else None,
        "pp_rows_per_play_max": int(max_rows),
        "pp_plays_with_both_sides": int(both),
        "pp_plays_with_both_sides_share": round(int(both) / int(plays), 6) if plays else None,
    }
    if players_table is not None:
        p_rows = int(con.execute(
            f"SELECT COUNT(DISTINCT nfl_id) FROM {players_table}").fetchone()[0])
        missing = int(con.execute(f"""
            SELECT COUNT(DISTINCT nfl_id) FROM {pp_table}
            WHERE nfl_id NOT IN (SELECT nfl_id FROM {players_table})""").fetchone()[0])
        out.update({"players_file_players": p_rows,
                    "pp_players_not_in_players_file": missing})
    return out


def preflight(con: duckdb.DuckDBPyConnection,
              candidates_table: str = CANDIDATE_TABLE,
              population: str = "SAMPLE") -> dict:
    """P1-P5 and both gate tests, from the candidate table alone.

    **Takes no tracking-file argument, by design.** Every input is a row count in
    ``player_play``; no frame-level data can change any number here.

    ``stop`` is True only for a real condition (P1 unsided, P2 no candidate at
    all). ``estimator_blocked`` marks the gates the note pre-declared: P3 (fewer
    than :data:`GATE_MIN_ATTRIBUTED_TURNS` candidate turns) and P4 (fewer than
    :data:`GATE_MIN_SHARE` of band turns), which refuse an estimate but do not
    stop a measurement.
    """
    (total, p1_missing, p1_no_team, p1_conflict, ceiling, no_row, opp_no_route,
     with_sep, single_sep, multi_sep) = con.execute(f"""
        SELECT COUNT(*),
          COUNT(*) FILTER (WHERE pp_own_rows = 0),
          COUNT(*) FILTER (WHERE own_team IS NULL),
          COUNT(*) FILTER (WHERE n_own_teams > 1),
          COUNT(*) FILTER (WHERE pp_opp_route_rows >= 1),
          COUNT(*) FILTER (WHERE pp_opp_rows = 0),
          COUNT(*) FILTER (WHERE pp_opp_rows > 0 AND pp_opp_route_rows = 0),
          COUNT(*) FILTER (WHERE pp_opp_route_sep_rows >= 1),
          COUNT(*) FILTER (WHERE pp_opp_route_sep_rows = 1),
          COUNT(*) FILTER (WHERE pp_opp_route_sep_rows >= 2)
        FROM {candidates_table}""").fetchone()

    p1 = int(p1_missing) + int(p1_no_team) + int(p1_conflict)
    keys = []
    if p1:
        rows = con.execute(f"""
            SELECT game_id, play_id, nfl_id, frame_start FROM {candidates_table}
            WHERE pp_own_rows = 0 OR own_team IS NULL OR n_own_teams > 1
            ORDER BY game_id, play_id, nfl_id, frame_start""").fetchall()
        keys = ["-".join(str(v) for v in r) for r in rows]

    ceiling = int(ceiling)
    total = int(total)
    ceiling_share = round(ceiling / total, 6) if total else None
    required_turns = math.ceil(GATE_MIN_SHARE * total)
    p3_cleared = ceiling >= GATE_MIN_ATTRIBUTED_TURNS
    p4_cleared = total > 0 and ceiling >= required_turns

    # Verdict precedence: a real stop (P1, P2) first, then the structural statement
    # (P4: the ceiling itself is under the share gate), then the count gate (P3).
    # Both gates are reported as their own flags, so P3 firing on a small
    # population is never hidden by the P4 wording — the notebook prints each.
    blocks = [name for name, cleared in (("P3", p3_cleared), ("P4", p4_cleared))
              if not cleared]
    if p1:
        verdict, stop = "STOP — unsided turns (P1)", True
    elif ceiling == 0:
        verdict, stop = "STOP — no opposing route-runner exists in this population (P2)", True
    elif not p4_cleared:
        verdict, stop = "GATE UNREACHABLE — descriptive only", False
    elif not p3_cleared:
        verdict, stop = "STOP ESTIMATOR — count gate unreachable (P3)", False
    else:
        verdict, stop = "GATE LIVE", False

    return {
        "population": population,
        "band_turns": total,
        "p1_turns_without_player_play_row": int(p1_missing),
        "p1_turns_without_team_label": int(p1_no_team),
        "p1_turns_with_conflicting_team_label": int(p1_conflict),
        "p1_unsided_turns": p1,
        "p1_unsided_turn_keys": keys,
        "p2_turns_with_candidate": ceiling,
        "p2_ceiling_share": ceiling_share,
        "p3_gate_min_turns": GATE_MIN_ATTRIBUTED_TURNS,
        "p3_cleared": bool(p3_cleared),
        "p4_gate_min_share": GATE_MIN_SHARE,
        "p4_required_turns": required_turns,
        "p4_cleared": bool(p4_cleared),
        "p5_turns_no_opposing_row": int(no_row),
        "p5_turns_no_opposing_route": int(opp_no_route),
        "p5_share_no_opposing_row": round(int(no_row) / total, 6) if total else None,
        "p5_share_no_opposing_route": round(int(opp_no_route) / total, 6) if total else None,
        "p5_wording": P5_WORDING.format(
            no_row=int(no_row), total=total,
            no_row_share=int(no_row) / total if total else 0.0,
            no_route=int(opp_no_route),
            no_route_share=int(opp_no_route) / total if total else 0.0),
        "turns_with_candidate_separation": int(with_sep),
        "turns_with_exactly_one_candidate_with_separation": int(single_sep),
        "turns_with_two_or_more_candidates_with_separation": int(multi_sep),
        "share_of_ceiling_needed_for_500_turns": (
            round(GATE_MIN_ATTRIBUTED_TURNS / ceiling, 6) if ceiling else None),
        "share_of_ceiling_needed_for_30pct": (
            round(required_turns / ceiling, 6) if ceiling else None),
        "verdict": verdict,
        "stop": bool(stop),
        "estimator_blocks": blocks,
        "estimator_blocked": bool(blocks),
    }


def ambiguity_counts(detail: pd.DataFrame) -> dict:
    """Tie counts and separation gaps, from the candidate-level table.

    A "tie" is a turn with two or more candidates. Neither the count nor the gap
    resolves one: which receiver the ball went to is a post-hoc outcome and may
    not break a tie.
    """
    key = ["game_id", "play_id", "nfl_id", "frame_start"]
    both_sep, targets, distinct, n_two, n_three = 0, 0, 0, 0, 0
    gaps: list[float] = []
    plays: list[tuple[int, int]] = []
    with_candidate = 0
    for (game_id, play_id, _nfl_id, _frame), grp in detail.groupby(key, sort=True):
        n = len(grp)
        with_candidate += 1
        if n == 2:
            n_two += 1
            plays.append((int(game_id), int(play_id)))
            seps = grp["candidate_separation"].dropna()
            if len(seps) == 2:
                both_sep += 1
                gaps.append(float(seps.max() - seps.min()))
            if bool(grp["candidate_target"].fillna(False).any()):
                targets += 1
            if grp["candidate_nfl_id"].nunique() == 2:
                distinct += 1
        elif n >= 3:
            n_three += 1
    median_gap = round(float(statistics.median(gaps)), 3) if gaps else None
    return {
        "candidate_rows": int(len(detail)),
        "turns_with_candidate": int(with_candidate),
        "turns_with_two_candidates": int(n_two),
        "turns_with_three_or_more_candidates": int(n_three),
        "two_candidate_turns_with_both_separations": int(both_sep),
        "two_candidate_turns_with_a_target_row": int(targets),
        "two_candidate_separation_gap_min_yd": round(min(gaps), 3) if gaps else None,
        "two_candidate_separation_gap_median_yd": median_gap,
        "two_candidate_separation_gap_max_yd": round(max(gaps), 3) if gaps else None,
        "two_candidate_distinct_receivers": int(distinct),
        "two_candidate_distinct_plays": int(len(set(plays))),
    }


def status_totals(candidates: pd.DataFrame,
                  population: str | None = None) -> list[dict]:
    """Turn counts and shares for each candidate status, in the fixed vocabulary."""
    df = candidates if population is None else candidates[
        candidates["population"] == population]
    total = int(len(df))
    out = []
    for status in CANDIDATE_STATUS:
        n = int((df["pp_candidate_status"] == status).sum())
        out.append({"population": population, "pp_candidate_status": status,
                    "n_turns": n,
                    "share_of_turns": round(n / total, 6) if total else None,
                    "total_turns": total})
    return out


def ordered_columns(frame: pd.DataFrame, first: Sequence[str]) -> list[str]:
    """``first`` in order, then every remaining column in its own order.

    Keeps CSV column order fixed without a set anywhere near an output path.
    """
    head = [c for c in first if c in frame.columns]
    return head + [c for c in frame.columns if c not in head]
