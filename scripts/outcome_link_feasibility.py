"""Outcome-link feasibility for DB 30-60 degree turns.

Feasibility, not analysis: this script counts what can be attributed to a DB's
30-60 degree turn, by which route, at what sample size, and with what
attribution ambiguity. It fits nothing and reports no association between turn
quality and any outcome.

Provenance
----------
Turn-level numbers are SAMPLE: ``outputs/turns/turns.parquet`` carries
``provenance: PLACEHOLDER_SAMPLE``. FULL numbers are read only from
``data/interim/full_run_20261111/bdb27_full_db_turns.parquet`` and are counts of
turns and plays - no outcome-bearing FULL artefact exists on this machine.

``player_play.csv`` carries rows only for the 497 players named in
``players.csv``; that is a property of the file, not of this script, and it is
the reason some outcomes are structurally unavailable for a given pairing.

Every geometric DB-to-receiver link computed here is an INFERENCE from
positions. It is labelled as such in the per-turn table and never written into
the project claim.

Usage:
    .venv/bin/python scripts/outcome_link_feasibility.py

Outputs (all tagged SAMPLE unless the column name says full_):
    outputs/outcome_link/feasibility_routes.csv
    outputs/outcome_link/feasibility_turns.csv
    outputs/outcome_link/feasibility_summary.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

TURNS = ROOT / "outputs" / "turns" / "turns.parquet"
PLAYER_PLAY = ROOT / "data" / "raw" / "player_play.csv"
TRACKING = ROOT / "data" / "raw" / "game_tracking_2023_sample.csv"
PLAYERS = ROOT / "bdb27_run" / "players.parquet"
FULL_TURNS = ROOT / "data" / "interim" / "full_run_20261111" / "bdb27_full_db_turns.parquet"
OUT = ROOT / "outputs" / "outcome_link"

STAMP_SAMPLE = "SAMPLE"
STAMP_FULL = "FULL"

BAND_LO, BAND_HI = 30.0, 60.0
DB_ALIGN = ("CB", "SLOT_CB", "HIGH_SAFETY", "BOX_SAFETY")
RADII = (2.0, 3.0, 5.0, 8.0)

# Explicit column selection: the raw file is 147 MB and never loaded whole into
# pandas. DuckDB reads it and we keep only the columns used below.
PP_COLUMNS = [
    "game_id", "play_id", "nfl_id", "team_abbr", "lined_up_position", "route_ran",
    "target", "separation_at_pass_forward", "cushion", "coverage_assignment",
    "coverage_assignment_at_snap", "pass_result", "yards_gained",
    "expected_points_added", "possession_team", "play_description", "down",
    "yards_to_go", "tackle", "assist", "caused_forced_fumble", "tackle_for_loss",
    "recovered_fumble", "fumble",
]

TURN_COLUMNS = [
    "game_id", "play_id", "nfl_id", "position", "position_group", "abs_turn_angle",
    "apex_retention", "speed_retention", "entry_speed", "exit_speed", "apex_speed",
    "plant", "frame_start", "frame_centre", "frame_end", "t_start", "t_end",
    "interpolated_in_turn", "x_centre", "y_centre", "valid", "setting",
]

INJURY_RE = re.compile(
    r"\b([A-Z]{2,3})-([A-Z])\.([A-Za-z'\-]+)\s+was injured during the play")
RETURN_RE = re.compile(r"Injury Update:\s*([A-Z]{2,3})-([A-Z])\.([A-Za-z'\-]+)")
RETURN_STATUS_RE = re.compile(r"(?:His|Her) return is (Questionable|Doubtful|Out)")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET memory_limit='1200MB'; SET threads=2")
    return con


def load_inputs(con: duckdb.DuckDBPyConnection) -> None:
    """Materialise the three local inputs as typed tables.

    Two steps on purpose: a bare ``SELECT *`` with ``all_varchar`` avoids
    DuckDB pushing a cast down into the CSV reader (which re-infers column
    types and fails on ``NA`` / ``0.5`` values).
    """
    con.execute(
        f"CREATE OR REPLACE TABLE raw_pp AS SELECT * FROM read_csv("
        f"'{PLAYER_PLAY.as_posix()}', header=true, all_varchar=true, nullstr=['NA',''])")
    con.execute(
        f"CREATE OR REPLACE TABLE raw_tk AS SELECT * FROM read_csv("
        f"'{TRACKING.as_posix()}', header=true, all_varchar=true, nullstr=['NA',''])")

    con.execute("""
    CREATE OR REPLACE TABLE pp AS
    SELECT
      CAST(game_id AS BIGINT) game_id, CAST(play_id AS BIGINT) play_id,
      CAST(nfl_id AS BIGINT) nfl_id, team_abbr, lined_up_position, route_ran,
      TRY_CAST(target AS BOOLEAN) target,
      TRY_CAST(separation_at_pass_forward AS DOUBLE) separation_at_pass_forward,
      TRY_CAST(cushion AS DOUBLE) cushion,
      coverage_assignment, coverage_assignment_at_snap, pass_result,
      TRY_CAST(yards_gained AS DOUBLE) yards_gained,
      TRY_CAST(expected_points_added AS DOUBLE) expected_points_added,
      possession_team, play_description, TRY_CAST(down AS INTEGER) down,
      TRY_CAST(yards_to_go AS INTEGER) yards_to_go,
      TRY_CAST(tackle AS BOOLEAN) tackle, TRY_CAST(assist AS BOOLEAN) assist,
      TRY_CAST(caused_forced_fumble AS BOOLEAN) caused_forced_fumble,
      TRY_CAST(tackle_for_loss AS BOOLEAN) tackle_for_loss,
      TRY_CAST(recovered_fumble AS BOOLEAN) recovered_fumble,
      TRY_CAST(fumble AS BOOLEAN) fumble
    FROM raw_pp
    """)
    con.execute("""
    CREATE OR REPLACE TABLE tk AS
    SELECT CAST(game_id AS BIGINT) game_id, CAST(play_id AS BIGINT) play_id,
           CAST(nfl_id AS BIGINT) nfl_id, CAST(time AS TIMESTAMP) time,
           CAST(x AS DOUBLE) x, CAST(y AS DOUBLE) y, event
    FROM raw_tk
    """)
    cols = ", ".join(TURN_COLUMNS)
    con.execute(
        f"CREATE OR REPLACE TABLE turns AS SELECT {cols} FROM read_parquet("
        f"'{TURNS.as_posix()}') WHERE setting='game' AND position_group='DB' AND valid")


def build_turns(con: duckdb.DuckDBPyConnection) -> None:
    """Turns in the band, with their break frame recovered from geometry."""
    con.execute(f"""
    CREATE OR REPLACE TABLE t60 AS
    SELECT * FROM turns
    WHERE abs_turn_angle >= {BAND_LO} AND abs_turn_angle < {BAND_HI}
    """)
    # Break frame: the player's own tracked row closest to (x_centre, y_centre)
    # inside the turn window. Frame indices cannot be reused directly because
    # the turn pipeline fills small gaps, which shifts indices.
    con.execute("""
    CREATE OR REPLACE TABLE own AS
    SELECT t.game_id, t.play_id, t.nfl_id, t.frame_start,
           o.time,
           SQRT((o.x - t.x_centre)^2 + (o.y - t.y_centre)^2) AS align_dist,
           ROW_NUMBER() OVER (
             PARTITION BY t.game_id, t.play_id, t.nfl_id, t.frame_start
             ORDER BY (o.x - t.x_centre)^2 + (o.y - t.y_centre)^2) AS rn,
           COUNT(*) OVER (
             PARTITION BY t.game_id, t.play_id, t.nfl_id, t.frame_start) AS n_rows
    FROM t60 t
    JOIN tk o
      ON t.game_id = o.game_id AND t.play_id = o.play_id AND o.nfl_id = t.nfl_id
     AND epoch(o.time) - epoch(
           (SELECT min(z.time) FROM tk z
            WHERE z.game_id = t.game_id AND z.play_id = t.play_id AND z.nfl_id = t.nfl_id)
         ) BETWEEN t.t_start - 0.06 AND t.t_end + 0.06
    """)
    con.execute("""
    CREATE OR REPLACE TABLE turn_ctx AS
    SELECT t.*, p.team_abbr AS own_team, p.lined_up_position AS alignment,
           p.cushion, p.coverage_assignment, p.pass_result, p.yards_gained,
           p.expected_points_added, p.tackle, p.assist, p.caused_forced_fumble,
           p.tackle_for_loss, p.down, p.yards_to_go,
           o.time AS break_time, o.align_dist, o.n_rows
    FROM t60 t
    LEFT JOIN own o
      ON t.game_id = o.game_id AND t.play_id = o.play_id AND t.nfl_id = o.nfl_id
     AND t.frame_start = o.frame_start AND o.rn = 1
    LEFT JOIN pp p
      ON t.game_id = p.game_id AND t.play_id = p.play_id AND t.nfl_id = p.nfl_id
    """)


def build_candidates(con: duckdb.DuckDBPyConnection) -> None:
    """Every tracked player at the break frame, with distance and team side."""
    con.execute("""
    CREATE OR REPLACE TABLE cand AS
    SELECT c.game_id, c.play_id, c.nfl_id, c.frame_start,
           o.nfl_id AS cand_id,
           SQRT((o.x - c.x_centre)^2 + (o.y - c.y_centre)^2) AS dist,
           op.team_abbr AS cand_team,
           CASE WHEN op.team_abbr IS NULL THEN 'unknown'
                WHEN op.team_abbr = c.own_team THEN 'same' ELSE 'opp' END AS rel,
           op.lined_up_position AS cand_pos,
           op.route_ran AS cand_route,
           op.target AS cand_target,
           op.separation_at_pass_forward AS cand_sep
    FROM turn_ctx c
    JOIN tk o
      ON c.game_id = o.game_id AND c.play_id = o.play_id
     AND o.time = c.break_time AND o.nfl_id <> c.nfl_id
    LEFT JOIN pp op
      ON o.game_id = op.game_id AND o.play_id = op.play_id AND o.nfl_id = op.nfl_id
    WHERE c.break_time IS NOT NULL
    """)


def build_turn_table(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    # Separation is only observable on plays where a sampled receiver has a row.
    con.execute("""
    CREATE OR REPLACE TABLE playsep AS
    SELECT game_id, play_id, COUNT(*) AS n_sep_rows
    FROM pp WHERE separation_at_pass_forward IS NOT NULL
    GROUP BY 1, 2
    """)
    con.execute("""
    CREATE OR REPLACE TABLE turn_rows AS
    SELECT c.game_id, c.play_id, c.nfl_id, c.frame_start,
           c.position, c.abs_turn_angle, c.apex_retention, c.speed_retention,
           c.entry_speed, c.exit_speed, c.apex_speed, c.plant,
           c.alignment, c.own_team, c.cushion, c.coverage_assignment,
           c.pass_result, c.tackle, c.assist, c.caused_forced_fumble,
           c.tackle_for_loss, c.break_time, c.align_dist,
           s.n_sep_rows IS NOT NULL AS sep_available,
           COUNT(k.cand_id) FILTER (WHERE k.rel = 'opp') AS n_opp_any,
           COUNT(k.cand_id) FILTER (WHERE k.rel = 'opp' AND k.dist <= 2) AS n_opp_2,
           COUNT(k.cand_id) FILTER (WHERE k.rel = 'opp' AND k.dist <= 3) AS n_opp_3,
           COUNT(k.cand_id) FILTER (WHERE k.rel = 'opp' AND k.dist <= 5) AS n_opp_5,
           COUNT(k.cand_id) FILTER (WHERE k.rel = 'opp' AND k.dist <= 8) AS n_opp_8,
           COUNT(k.cand_id) FILTER (
             WHERE k.rel = 'opp' AND k.dist <= 5 AND k.cand_route IS NOT NULL) AS n_route_5,
           COUNT(k.cand_id) FILTER (
             WHERE k.rel = 'opp' AND k.dist <= 5 AND k.cand_route IS NOT NULL
               AND k.cand_sep IS NOT NULL) AS n_route_5_with_sep,
           MIN(k.dist) FILTER (WHERE k.rel = 'opp') AS nearest_opp_dist,
           MIN(k.dist) FILTER (WHERE k.rel = 'opp' AND k.cand_route IS NOT NULL)
             AS nearest_route_dist
    FROM turn_ctx c
    LEFT JOIN cand k
      ON c.game_id = k.game_id AND c.play_id = k.play_id
     AND c.nfl_id = k.nfl_id AND c.frame_start = k.frame_start
    LEFT JOIN playsep s
      ON c.game_id = s.game_id AND c.play_id = s.play_id
    GROUP BY ALL
    """)
    return con.execute("SELECT * FROM turn_rows ORDER BY game_id, play_id, nfl_id, frame_start").df()


def injury_facts(con: duckdb.DuckDBPyConnection) -> dict:
    """Injury mentions in play_description, parsed and resolved where unique."""
    dist = con.execute("""
    SELECT DISTINCT game_id, play_id, play_description FROM pp
    WHERE lower(play_description) LIKE '%injur%' OR lower(play_description) LIKE '%hurt%'
    """).df()

    mentions, returned, status = [], [], 0
    for row in dist.itertuples(index=False):
        text = row.play_description
        for m in INJURY_RE.finditer(text):
            mentions.append((row.game_id, row.play_id, m.group(1), m.group(2), m.group(3)))
        for m in RETURN_RE.finditer(text):
            returned.append((row.game_id, row.play_id, m.group(1), m.group(2), m.group(3)))
        status += 1 if RETURN_STATUS_RE.search(text) else 0

    inj = pd.DataFrame(mentions, columns=["game_id", "play_id", "team", "init", "surname"])
    inj = inj.drop_duplicates()
    players = con.execute(
        f"SELECT nfl_id, display_name FROM read_parquet('{PLAYERS.as_posix()}')").df()
    players["init"] = players["display_name"].str[0].str.upper()
    players["surname"] = (players["display_name"].str.split().str[-1]
                          .str.replace(r"[^A-Za-z'\-]", "", regex=True))
    uniq = players.groupby(["init", "surname"])["nfl_id"].nunique()

    def resolve(r):
        n = int(uniq.get((r.init, r.surname), 0))
        if n != 1:
            return None
        return int(players[(players.init == r.init) & (players.surname == r.surname)].nfl_id.iloc[0])

    inj["injured_nfl_id"] = [resolve(r) for r in inj.itertuples(index=False)]
    resolved = inj[inj.injured_nfl_id.notna()]

    turns = con.execute("""
    SELECT game_id, play_id, nfl_id FROM turn_rows
    """).df()
    key = set(zip(turns.game_id, turns.play_id, turns.nfl_id))
    inj_keys = set(zip(inj.game_id, inj.play_id))
    on_inj = sum(1 for k in key if (k[0], k[1]) in inj_keys)
    named = sum(1 for _, r in resolved.iterrows()
                if (r.game_id, r.play_id, r.injured_nfl_id) in key)

    pos = con.execute(f"""
    SELECT p.nfl_position, COUNT(*) n
    FROM (SELECT * FROM (VALUES {",".join(f"({i})" for i in resolved.injured_nfl_id.astype(int))})
          AS v(nfl_id)) r
    JOIN read_parquet('{PLAYERS.as_posix()}') p USING (nfl_id)
    GROUP BY 1 ORDER BY 2 DESC
    """).df()

    ambiguous = int(inj["injured_nfl_id"].isna().sum())
    return {
        "plays_with_injury_text": int(len(dist)),
        "plays_total": int(con.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT game_id, play_id FROM pp)").fetchone()[0]),
        "injury_mentions": int(len(inj)),
        "returned_mentions": int(len(pd.DataFrame(returned).drop_duplicates())),
        "return_status_mentions": int(status),
        "mentions_resolved_unique": int(len(resolved)),
        "mentions_unresolved_or_ambiguous": ambiguous,
        "resolved_by_position": pos.set_index("nfl_position")["n"].to_dict(),
        "db_position_mentions": int(pos[pos.nfl_position.isin(["CB", "SS", "FS", "S"])]["n"].sum()),
        "turns_on_injury_play": int(on_inj),
        "turns_where_turning_db_named": int(named),
    }


def full_denominators(con: duckdb.DuckDBPyConnection) -> dict:
    q = lambda s: con.execute(s).fetchone()
    f = FULL_TURNS.as_posix()
    n_valid, n_players = q(
        f"SELECT COUNT(*), COUNT(DISTINCT nfl_id) FROM read_parquet('{f}') WHERE valid")
    n_band, band_players, band_games = q(
        f"SELECT COUNT(*), COUNT(DISTINCT nfl_id), COUNT(DISTINCT game_id) "
        f"FROM read_parquet('{f}') WHERE valid "
        f"AND abs_turn_angle >= {BAND_LO} AND abs_turn_angle < {BAND_HI}")
    band_plays, band_plays_one = q(f"""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE n = 1) FROM (
          SELECT game_id, play_id, COUNT(*) n FROM read_parquet('{f}')
          WHERE valid AND abs_turn_angle >= {BAND_LO} AND abs_turn_angle < {BAND_HI}
          GROUP BY 1, 2)""")
    plays_any = q(f"""SELECT COUNT(*) FROM (
        SELECT DISTINCT game_id, play_id FROM read_parquet('{f}') WHERE valid)""")[0]
    return {
        "full_valid_db_turns": int(n_valid),
        "full_db_players": int(n_players),
        "full_band_turns": int(n_band),
        "full_band_players": int(band_players),
        "full_band_games": int(band_games),
        "full_band_plays": int(band_plays),
        "full_band_plays_one_turn": int(band_plays_one),
        "full_plays_any_db_turn": int(plays_any),
    }


def sample_facts(con: duckdb.DuckDBPyConnection) -> dict:
    q = lambda s: con.execute(s).fetchone()
    db_rows, db_sep = q(f"""
        SELECT COUNT(*), COUNT(separation_at_pass_forward) FROM pp
        WHERE lined_up_position IN ({",".join("'" + p + "'" for p in DB_ALIGN)})""")
    sep_rows, sep_plays, target_plays = q("""
        SELECT COUNT(*), COUNT(DISTINCT game_id || '-' || play_id),
               COUNT(DISTINCT game_id || '-' || play_id) FILTER (WHERE target)
        FROM pp WHERE separation_at_pass_forward IS NOT NULL""")
    plays_total = q("SELECT COUNT(*) FROM (SELECT DISTINCT game_id, play_id FROM pp)")[0]
    align = con.execute(f"""
        SELECT lined_up_position pos, COUNT(*) n, COUNT(separation_at_pass_forward) sep
        FROM pp WHERE lined_up_position IN ({",".join("'" + p + "'" for p in DB_ALIGN)})
        GROUP BY 1 ORDER BY 2 DESC""").df()
    return {
        "sample_pp_rows": int(q("SELECT COUNT(*) FROM pp")[0]),
        "sample_pp_players": int(q("SELECT COUNT(DISTINCT nfl_id) FROM pp")[0]),
        "sample_pp_games": int(q("SELECT COUNT(DISTINCT game_id) FROM pp")[0]),
        "sample_plays": int(plays_total),
        "db_alignment_rows": int(db_rows),
        "db_alignment_rows_with_separation": int(db_sep),
        "receiver_separation_rows": int(sep_rows),
        "plays_with_separation": int(sep_plays),
        "plays_with_target": int(target_plays),
        "db_alignment_breakdown": align.set_index("pos")[["n", "sep"]]
                                   .rename(columns={"n": "rows", "sep": "sep_rows"})
                                   .to_dict("index"),
        "sample_tracking_rows": int(q("SELECT COUNT(*) FROM tk")[0]),
        "sample_tracking_plays": int(
            q("SELECT COUNT(DISTINCT game_id || '-' || play_id) FROM tk")[0]),
        "sample_tracking_players": int(q("SELECT COUNT(DISTINCT nfl_id) FROM tk")[0]),
        "sample_tracking_play_player_pairs": int(
            q("SELECT COUNT(*) FROM (SELECT DISTINCT game_id, play_id, nfl_id FROM tk)")[0]),
        "sample_tracking_players_per_play_mean": float(q("""
            SELECT COUNT(*) * 1.0 / COUNT(DISTINCT game_id || '-' || play_id)
            FROM (SELECT DISTINCT game_id, play_id, nfl_id FROM tk)""")[0]),
    }


def route_table(turns: pd.DataFrame, full: dict, inj: dict) -> pd.DataFrame:
    n = len(turns)
    plays = turns[["game_id", "play_id"]].drop_duplicates()
    per_play = turns.groupby(["game_id", "play_id"]).size()
    pass_turns = int(turns["pass_result"].notna().sum())
    man = turns[turns.coverage_assignment == "MAN"]

    def cnt(mask):
        return int(mask.sum())

    usable_r1 = turns[turns.n_route_5_with_sep > 0]
    man_usable = man[man.n_route_5_with_sep > 0]

    rows = [
        {
            "route_id": "R1",
            "route": "geometric proximity match to receiver separation",
            "outcome": "separation_at_pass_forward (receiver row)",
            "attribution": "nearest opponent at break frame - INFERENCE",
            "sample_turns_available": n,
            "sample_turns_usable": cnt(turns.n_route_5_with_sep > 0),
            "sample_turns_usable_with_sep": int(len(usable_r1)),
            "full_turns_available": full["full_band_turns"],
            "full_plays_available": full["full_band_plays"],
            "ambiguous_sample_n": cnt(turns.n_route_5 >= 2),
            "ambiguous_sample_of": cnt(turns.n_route_5 > 0),
            "measurable_on_local_sample": False,
            "needs_full_tracking": True,
            "controls": "entry_speed, position, alignment, coverage_assignment, cushion",
        },
        {
            "route_id": "R2",
            "route": "man-restricted proximity match (subset of R1)",
            "outcome": "separation_at_pass_forward (receiver row)",
            "attribution": "nearest opponent at break frame, MAN prior - INFERENCE",
            "sample_turns_available": int(len(man)),
            "sample_turns_usable": int((man.n_route_5_with_sep > 0).sum()),
            "sample_turns_usable_with_sep": int(len(man_usable)),
            "full_turns_available": None,
            "full_plays_available": None,
            "ambiguous_sample_n": int((man.n_route_5 >= 2).sum()),
            "ambiguous_sample_of": int((man.n_route_5 > 0).sum()),
            "measurable_on_local_sample": False,
            "needs_full_tracking": True,
            "controls": "entry_speed, position, alignment, cushion",
        },
        {
            "route_id": "R3",
            "route": "same-player event outcomes",
            "outcome": "tackle, assist, caused_forced_fumble, tackle_for_loss",
            "attribution": "exact: same nfl_id, same play",
            "sample_turns_available": n,
            "sample_turns_usable": n,
            "sample_turns_usable_with_sep": None,
            "full_turns_available": full["full_band_turns"],
            "full_plays_available": full["full_band_plays"],
            "ambiguous_sample_n": 0,
            "ambiguous_sample_of": n,
            "measurable_on_local_sample": True,
            "needs_full_tracking": False,
            "controls": "entry_speed, position, alignment, down, distance",
        },
        {
            "route_id": "R4",
            "route": "play-level coverage-unit outcomes",
            "outcome": "pass_result, yards_gained, expected_points_added",
            "attribution": "the play; player only if exactly one DB turned",
            "sample_turns_available": n,
            "sample_turns_usable": pass_turns,
            "sample_turns_usable_with_sep": int(
                (turns.pass_result.notna() & turns.sep_available).sum()),
            "full_turns_available": full["full_band_turns"],
            "full_plays_available": full["full_band_plays"],
            "ambiguous_sample_n": int((per_play >= 2).sum()),
            "ambiguous_sample_of": int(len(per_play)),
            "measurable_on_local_sample": True,
            "needs_full_tracking": False,
            "controls": "entry_speed, position, alignment, coverage_assignment, down, distance",
        },
        {
            "route_id": "R5",
            "route": "injury",
            "outcome": "injury mention in play_description",
            "attribution": "free-text named player, initial+surname resolution",
            "sample_turns_available": n,
            "sample_turns_usable": inj["turns_where_turning_db_named"],
            "sample_turns_usable_with_sep": None,
            "full_turns_available": full["full_band_turns"],
            "full_plays_available": full["full_band_plays"],
            "ambiguous_sample_n": inj["mentions_unresolved_or_ambiguous"],
            "ambiguous_sample_of": inj["injury_mentions"],
            "measurable_on_local_sample": False,
            "needs_full_tracking": False,
            "controls": "none",
        },
    ]
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)

    con = connect()
    load_inputs(con)
    build_turns(con)
    build_candidates(con)
    turns = build_turn_table(con)

    full = full_denominators(con)
    sample = sample_facts(con)
    inj = injury_facts(con)
    routes = route_table(turns, full, inj)

    # Alignment proof: the recovered break frame must land on the stored centre.
    align_max = float(turns["align_dist"].max())
    if not align_max < 0.25:
        raise SystemExit(f"break-frame alignment failed: max {align_max:.3f} yd")

    per_play = turns.groupby(["game_id", "play_id"]).size()
    summary = {
        "provenance": STAMP_SAMPLE,
        "label": "UNVALIDATED SAMPLE OUTPUT - feasibility counts only",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "scripts/outcome_link_feasibility.py",
        "band": {"lo": BAND_LO, "hi": BAND_HI, "closed": "left"},
        "inputs": {
            "turns": {"path": str(TURNS.relative_to(ROOT)), "sha256": sha256(TURNS),
                      "stamp": "PLACEHOLDER_SAMPLE"},
            "player_play": {"path": str(PLAYER_PLAY.relative_to(ROOT)),
                            "sha256": sha256(PLAYER_PLAY)},
            "tracking": {"path": str(TRACKING.relative_to(ROOT)),
                         "sha256": sha256(TRACKING)},
            "full_turns": {"path": str(FULL_TURNS.relative_to(ROOT)),
                           "sha256": sha256(FULL_TURNS), "stamp": STAMP_FULL},
        },
        "sample": sample,
        "full": full,
        "turns": {
            "sample_db_turns_all": int(con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]),
            "sample_band_turns": int(len(turns)),
            "sample_band_players": int(turns.nfl_id.nunique()),
            "sample_band_games": int(turns.game_id.nunique()),
            "sample_band_plays": int(len(per_play)),
            "sample_band_plays_one_turn": int((per_play == 1).sum()),
            "sample_band_plays_two_turns": int((per_play == 2).sum()),
            "break_frame_alignment_max_yd": align_max,
            "break_frame_recovered": int(turns["break_time"].notna().sum()),
            "turns_with_player_play_row": int(turns["own_team"].notna().sum()),
            "pass_play_turns": int(turns["pass_result"].notna().sum()),
            "tackles": int(turns["tackle"].fillna(False).sum()),
            "assists": int(turns["assist"].fillna(False).sum()),
            "forced_fumbles": int(turns["caused_forced_fumble"].fillna(False).sum()),
            "tackles_for_loss": int(turns["tackle_for_loss"].fillna(False).sum()),
            "cushion_available": int(turns["cushion"].notna().sum()),
            "coverage_assignment_available": int(turns["coverage_assignment"].notna().sum()),
            "man_turns": int((turns.coverage_assignment == "MAN").sum()),
            "turns_with_separation_available": int(turns["sep_available"].sum()),
            "plays_with_separation_available": int(
                turns[turns.sep_available][["game_id", "play_id"]].drop_duplicates().shape[0]),
            "apex_retention_iqr": [float(x) for x in
                                   turns["apex_retention"].quantile([.25, .5, .75]).tolist()],
            "entry_speed_iqr": [float(x) for x in
                                turns["entry_speed"].quantile([.25, .5, .75]).tolist()],
        },
        "geometry": {
            "tracked_players_per_play_median": float(
                con.execute("""SELECT median(n) FROM (
                    SELECT game_id, play_id, COUNT(DISTINCT nfl_id) n FROM tk GROUP BY 1,2)""")
                .fetchone()[0]),
            "turns_with_any_opponent_tracked": int((turns.n_opp_any > 0).sum()),
            "turns_with_opponent_within_2yd": int((turns.n_opp_2 > 0).sum()),
            "turns_with_opponent_within_3yd": int((turns.n_opp_3 > 0).sum()),
            "turns_with_opponent_within_5yd": int((turns.n_opp_5 > 0).sum()),
            "turns_with_opponent_within_8yd": int((turns.n_opp_8 > 0).sum()),
            "turns_with_route_runner_within_5yd": int((turns.n_route_5 > 0).sum()),
            "turns_with_route_runner_within_5yd_and_sep": int(
                (turns.n_route_5_with_sep > 0).sum()),
            "turns_with_two_plus_route_runners_within_5yd": int((turns.n_route_5 >= 2).sum()),
        },
        "injury": inj,
    }

    # Per-turn table, for the record. No outcome association is computed here.
    turns_cols = [
        "game_id", "play_id", "nfl_id", "frame_start", "position", "alignment",
        "abs_turn_angle", "apex_retention", "speed_retention", "entry_speed",
        "coverage_assignment", "cushion", "pass_result", "tackle", "assist",
        "sep_available",
        "n_opp_any", "n_opp_2", "n_opp_3", "n_opp_5", "n_route_5",
        "n_route_5_with_sep",
        "nearest_opp_dist", "nearest_route_dist", "align_dist",
    ]
    turns_out = turns[[c for c in turns_cols if c in turns.columns]].copy()
    turns_out.insert(0, "stamp", STAMP_SAMPLE)

    turns_out.to_csv(OUT / "feasibility_turns.csv", index=False, float_format="%.6g")
    routes.to_csv(OUT / "feasibility_routes.csv", index=False)
    (OUT / "feasibility_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n")

    print(json.dumps({
        "sample_band_turns": summary["turns"]["sample_band_turns"],
        "sample_band_plays": summary["turns"]["sample_band_plays"],
        "tackles": summary["turns"]["tackles"],
        "turns_with_route_runner_within_5yd": summary["geometry"]
        ["turns_with_route_runner_within_5yd"],
        "full_band_turns": full["full_band_turns"],
        "injury_mentions": inj["injury_mentions"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
