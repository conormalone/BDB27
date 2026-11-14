"""Outcome-link feasibility for DB 30-60 degree turns.

Feasibility, not analysis: this script counts what can be attributed to a DB's
30-60 degree turn, by which route, at what sample size, and with what
attribution ambiguity. It fits nothing and reports no association between turn
quality and any outcome.

Two separate questions are measured and never merged:

* **candidate availability** — how many band turns have an opposing
  route-runner on the play at all. Read from ``player_play.csv`` alone, over the
  whole band population, with no frame-level data (``src/bdb27/outcome_link.py``).
  Its pre-flight (P1-P5) is the notebook's stop-check: it stops on a real
  condition only, and it has no rows-per-play threshold of any kind.
* **geometric ambiguity** — how many turns resolve to exactly one opponent
  inside the match radius. Measured on the local 2023 tracking *sample file*
  only, and labelled as a property of that file.

Provenance
----------
Turn-level numbers are SAMPLE: ``outputs/turns/turns.parquet`` carries
``provenance: PLACEHOLDER_SAMPLE``. FULL numbers are read only from
``data/interim/full_run_20261111/bdb27_full_db_turns.parquet`` and are counts of
turns and plays - no outcome-bearing FULL artefact exists on this machine.

``player_play.csv`` carries rows only for the draft-prospect cohort named in
``players.csv``; that is a property of the file, not of this script, and it is
the reason some outcomes are structurally unavailable for a given pairing. It
also makes every candidate count in the pre-flight a lower bound.

Every geometric DB-to-receiver link computed here is an INFERENCE from
positions. It is labelled as such in the per-turn table and never written into
the project claim.

Usage:
    .venv/bin/python scripts/outcome_link_feasibility.py

Outputs (all tagged SAMPLE unless the column name says full_):
    outputs/outcome_link/feasibility_routes.csv
    outputs/outcome_link/feasibility_turns.csv
    outputs/outcome_link/feasibility_candidates.csv
    outputs/outcome_link/feasibility_preflight.csv
    outputs/outcome_link/feasibility_ambiguity_candidates.csv
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
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bdb27 import outcome_link as ol  # noqa: E402  (needs ROOT/src on the path)

TURNS = ROOT / "outputs" / "turns" / "turns.parquet"
PLAYER_PLAY = ROOT / "data" / "raw" / "player_play.csv"
TRACKING = ROOT / "data" / "raw" / "game_tracking_2023_sample.csv"
PLAYERS = ROOT / "bdb27_run" / "players.parquet"
PLAYERS_CSV = ROOT / "data" / "raw" / "players.csv"
FULL_TURNS = ROOT / "data" / "interim" / "full_run_20261111" / "bdb27_full_db_turns.parquet"
OUT = ROOT / "outputs" / "outcome_link"

STAMP_SAMPLE = "SAMPLE"
STAMP_FULL = "FULL"

# The band and the frozen primary radius live in the shared module so the script
# and the pre-flight notebook cannot drift apart.
BAND_LO, BAND_HI = ol.BAND_LO, ol.BAND_HI
DB_ALIGN = ("CB", "SLOT_CB", "HIGH_SAFETY", "BOX_SAFETY")
# The radius sweep: the match is reported at every radius so the choice is
# visible. PRIMARY_RADIUS is the declared design choice, not a result.
RADII = (1, 2, 3, 4, 5, 6, 8)
PRIMARY_RADIUS = ol.PRIMARY_RADIUS_YD
SECONDARY_RADII = (2, 5)
#: The rule, recorded verbatim in the artefact and the note.
MATCH_RULE = (
    f"at the break frame, a turn is attributed to the single opposing route-runner within "
    f"{PRIMARY_RADIUS} yd; turns with two or more candidates are ambiguous and dropped; turns "
    f"with none are unmatched and dropped; no tie is broken by radius, distance order or row "
    f"order. INFERENCE: the dataset records no opponent identifier.")

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
    route_counts = ",\n".join(
        "COUNT(k.cand_id) FILTER (WHERE k.rel = 'opp' AND k.cand_route IS NOT NULL"
        f" AND k.dist <= {r}) AS n_route_{r}" for r in RADII)
    opp_counts = ",\n".join(
        "COUNT(k.cand_id) FILTER (WHERE k.rel = 'opp'"
        f" AND k.dist <= {r}) AS n_opp_{r}" for r in RADII)
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
           {opp_counts},
           {route_counts},
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
    """.replace("{opp_counts}", opp_counts).replace("{route_counts}", route_counts))
    # The single matched receiver at the primary radius, recorded only where the
    # match is unique. Ties are dropped, not resolved.
    con.execute(f"""
    CREATE OR REPLACE TABLE match_primary AS
    SELECT game_id, play_id, nfl_id, frame_start, COUNT(*) n,
           MIN(cand_id) AS matched_receiver_nfl_id,
           MIN(dist) AS match_distance_yd,
           MIN(cand_sep) AS matched_receiver_separation
    FROM cand
    WHERE rel = 'opp' AND cand_route IS NOT NULL AND dist <= {PRIMARY_RADIUS}
    GROUP BY 1, 2, 3, 4
    """)
    return con.execute("SELECT * FROM turn_rows ORDER BY game_id, play_id, nfl_id, frame_start").df()


def match_facts(con: duckdb.DuckDBPyConnection, turns: pd.DataFrame):
    """Classify every turn at every radius; attach the unique primary match.

    Status, per turn:
      unique        exactly one opposing route-runner inside r  -> attributed
      multiple      two or more                                  -> dropped as ambiguous
      none_observed opponents are tracked in the frame, none is a route-runner inside r
      unobservable  no opposing player is tracked in the frame at all

    ``unobservable`` is not evidence that no receiver was near; the sample tracks
    1.16 players per play. The distinction matters and is never collapsed.
    """
    turns = turns.copy()
    turns["match_status"] = "unobservable"
    for r in RADII:
        n = turns[f"n_route_{r}"].fillna(0).astype(int)
        status = pd.Series("none_observed", index=turns.index)
        status[n >= 2] = "multiple"
        status[n == 1] = "unique"
        status[turns.n_opp_any.fillna(0).astype(int) == 0] = "unobservable"
        turns[f"match_status_r{r}"] = status

    turns["match_status"] = turns[f"match_status_r{PRIMARY_RADIUS}"]
    matched = con.execute("SELECT * FROM match_primary").df()
    turns = turns.merge(
        matched[["game_id", "play_id", "nfl_id", "frame_start", "n",
                 "matched_receiver_nfl_id", "match_distance_yd",
                 "matched_receiver_separation"]],
        on=["game_id", "play_id", "nfl_id", "frame_start"], how="left")
    # A tie is never resolved: the id is kept only on a unique match.
    tied = turns["n"].fillna(0) > 1
    turns.loc[tied, ["matched_receiver_nfl_id", "match_distance_yd",
                     "matched_receiver_separation"]] = pd.NA
    turns = turns.drop(columns=["n"])
    turns["match_inference"] = True
    turns["match_rule"] = MATCH_RULE
    turns["match_radius_yd"] = PRIMARY_RADIUS

    rows = []
    total = len(turns)
    for r in RADII:
        for definition, counts in (
                ("route_runner", f"n_route_{r}"),
                ("any_opponent", f"n_opp_{r}")):
            k = turns[counts].fillna(0).astype(int)
            observable = turns.n_opp_any.fillna(0).astype(int) > 0
            status = pd.Series("unobservable", index=turns.index)
            status[observable & (k == 0)] = "none_observed"
            status[k == 1] = "unique"
            status[k >= 2] = "multiple"
            for name in ("unique", "multiple", "none_observed", "unobservable"):
                n = int((status == name).sum())
                rows.append({"radius_yd": r, "candidate_def": definition,
                             "status": name, "n_turns": n,
                             "share_of_turns": round(n / total, 6),
                             "total_turns": total})
    ambiguity = pd.DataFrame(rows)

    primary = turns[turns.match_status == "unique"]
    facts = {
        "radius_primary_yd": PRIMARY_RADIUS,
        "radius_secondary_yd": list(SECONDARY_RADII),
        "radii_reported": list(RADII),
        "rule": MATCH_RULE,
        "turns_total": total,
        "unique": int(len(primary)),
        "multiple": int((turns.match_status == "multiple").sum()),
        "none_observed": int((turns.match_status == "none_observed").sum()),
        "unobservable": int((turns.match_status == "unobservable").sum()),
        "attributed_fraction": round(len(primary) / total, 6),
        "unique_with_separation": int(primary.matched_receiver_separation.notna().sum()),
        "attributed_and_outcome_observed_fraction": round(
            primary.matched_receiver_separation.notna().sum() / total, 6),
        "median_match_distance_yd": (round(float(primary.match_distance_yd.median()), 3)
                                     if len(primary) else None),
    }
    return turns, ambiguity, facts


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


def candidate_block(con: duckdb.DuckDBPyConnection, turns: pd.DataFrame) -> dict:
    """Candidate availability and the P1-P5 pre-flight, for both populations.

    Reads ``player_play`` only. No tracking file is opened, passed or accepted
    here, and no rows-per-play threshold of any kind is applied: the rows-per-play
    figure is a property of the draft-prospect cohort, not a stop condition.
    """
    key = list(ol.TURN_KEY)
    ol.register_band_turns(con, TURNS, "ol_band_sample")
    ol.register_band_turns(con, FULL_TURNS, "ol_band_full")
    keys = {STAMP_SAMPLE: ol.check_turn_key(con, "ol_band_sample"),
            STAMP_FULL: ol.check_turn_key(con, "ol_band_full")}

    cand, detail = {}, {}
    for stamp, band in ((STAMP_SAMPLE, "ol_band_sample"), (STAMP_FULL, "ol_band_full")):
        slug = stamp.lower()
        cand[stamp] = ol.candidate_table(
            con, band, f"ol_cand_{slug}", stamp, pp_table="pp")
        detail[stamp] = ol.candidate_detail(
            con, f"ol_cand_{slug}", f"ol_detail_{slug}", pp_table="pp")
    pre = {stamp: ol.preflight(con, f"ol_cand_{stamp.lower()}", stamp)
           for stamp in (STAMP_SAMPLE, STAMP_FULL)}

    # R2's own ceiling: the same candidate rule restricted to MAN turns.
    man_keys = turns.loc[turns["coverage_assignment"] == "MAN", key]
    man_cand = cand[STAMP_SAMPLE].merge(man_keys, on=key, how="inner")

    avail = {"players_file": None}
    players_table = None
    if PLAYERS_CSV.exists():
        con.execute(
            f"CREATE OR REPLACE TABLE ol_players AS SELECT TRY_CAST(nfl_id AS BIGINT) "
            f"AS nfl_id FROM read_csv('{PLAYERS_CSV.as_posix()}', header=true, "
            f"all_varchar=true, nullstr=['NA',''])")
        players_table = "ol_players"
    avail = ol.availability(con, "pp", players_table=players_table)

    return {
        "candidates": pd.concat([cand[STAMP_SAMPLE], cand[STAMP_FULL]],
                                ignore_index=True),
        "detail": pd.concat([detail[STAMP_SAMPLE], detail[STAMP_FULL]],
                            ignore_index=True),
        "preflight": pre,
        "turn_keys": keys,
        "tie_counts": {stamp: ol.ambiguity_counts(detail[stamp])
                       for stamp in (STAMP_SAMPLE, STAMP_FULL)},
        "status_totals": {stamp: ol.status_totals(cand[stamp], stamp)
                          for stamp in (STAMP_SAMPLE, STAMP_FULL)},
        "availability": avail,
        "man_turns": int(len(man_keys)),
        "man_candidate_ceiling": int((man_cand["pp_opp_route_rows"] >= 1).sum()),
    }


def route_table(turns: pd.DataFrame, full: dict, inj: dict, cand: dict) -> pd.DataFrame:
    n = len(turns)
    plays = turns[["game_id", "play_id"]].drop_duplicates()
    per_play = turns.groupby(["game_id", "play_id"]).size()
    pass_turns = int(turns["pass_result"].notna().sum())
    man = turns[turns.coverage_assignment == "MAN"]
    pre_s, pre_f = cand["preflight"][STAMP_SAMPLE], cand["preflight"][STAMP_FULL]
    # The geometric counts below stay exactly what they were: they measure the
    # local 2023 tracking sample file. The candidate ceiling sits beside them and
    # measures the full band population in player_play.
    r1_ceiling = {
        "sample_candidate_ceiling_turns": pre_s["p2_turns_with_candidate"],
        "sample_candidate_ceiling_share": pre_s["p2_ceiling_share"],
        "full_candidate_ceiling_turns": pre_f["p2_turns_with_candidate"],
        "full_candidate_ceiling_share": pre_f["p2_ceiling_share"],
    }
    r2_ceiling = {
        "sample_candidate_ceiling_turns": cand["man_candidate_ceiling"],
        "sample_candidate_ceiling_share": (
            round(cand["man_candidate_ceiling"] / cand["man_turns"], 6)
            if cand["man_turns"] else None),
        "full_candidate_ceiling_turns": None,
        "full_candidate_ceiling_share": None,
    }

    def cnt(mask):
        return int(mask.sum())

    usable_r1 = turns[turns.match_status == "unique"]
    matched_with_sep = turns[(turns.match_status == "unique")
                             & turns.matched_receiver_separation.notna()]
    man_usable = man[man.match_status == "unique"]

    rows = [
        {
            "route_id": "R1",
            "route": "geometric proximity match to receiver separation",
            "outcome": "separation_at_pass_forward (receiver row)",
            "attribution": ("unique opposing route-runner within 3 yd at the break frame "
                            "- INFERENCE (no opponent IDs in the data)"),
            "sample_turns_available": n,
            "sample_turns_usable": int(len(usable_r1)),
            "sample_turns_usable_with_sep": int(len(matched_with_sep)),
            "sample_attributed_fraction": round(len(usable_r1) / n, 6),
            "full_turns_available": full["full_band_turns"],
            "full_plays_available": full["full_band_plays"],
            "ambiguous_sample_n": cnt(turns.match_status == "multiple"),
            "ambiguous_sample_of": n,
            **r1_ceiling,
            "sample_turns_usable_source": (
                "geometric sweep at the primary radius on the 2023 tracking sample file"),
            "measurable_on_local_sample": False,
            "needs_full_tracking": True,
            "controls": "entry_speed, position, alignment, coverage_assignment, cushion",
        },
        {
            "route_id": "R2",
            "route": "man-restricted proximity match (subset of R1)",
            "outcome": "separation_at_pass_forward (receiver row)",
            "attribution": ("unique opposing route-runner within 3 yd at the break frame, "
                            "MAN prior - INFERENCE (no opponent IDs in the data)"),
            "sample_turns_available": int(len(man)),
            "sample_turns_usable": int((man.match_status == "unique").sum()),
            "sample_turns_usable_with_sep": int(len(man_usable)),
            "sample_attributed_fraction": round(len(man_usable) / max(len(man), 1), 6),
            "full_turns_available": None,
            "full_plays_available": None,
            "ambiguous_sample_n": int((man.match_status == "multiple").sum()),
            "ambiguous_sample_of": int(len(man)),
            **r2_ceiling,
            "sample_turns_usable_source": (
                "geometric sweep on MAN turns of the 2023 tracking sample file"),
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
            "sample_candidate_ceiling_turns": None,
            "sample_candidate_ceiling_share": None,
            "full_candidate_ceiling_turns": None,
            "full_candidate_ceiling_share": None,
            "sample_turns_usable_source": "exact same-player join; no geometry",
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
            "sample_candidate_ceiling_turns": None,
            "sample_candidate_ceiling_share": None,
            "full_candidate_ceiling_turns": None,
            "full_candidate_ceiling_share": None,
            "sample_turns_usable_source": "the play; no player-level geometry",
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
            "sample_candidate_ceiling_turns": None,
            "sample_candidate_ceiling_share": None,
            "full_candidate_ceiling_turns": None,
            "full_candidate_ceiling_share": None,
            "sample_turns_usable_source": "free-text name resolution; no geometry",
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
    turns, ambiguity, match = match_facts(con, turns)

    full = full_denominators(con)
    sample = sample_facts(con)
    inj = injury_facts(con)
    cand = candidate_block(con, turns)
    routes = route_table(turns, full, inj, cand)

    # What the geometric sweep actually measured: the local 2023 tracking *sample
    # file*, not data availability. Recorded beside the sweep so the two are never
    # read as one.
    tk_rows, tk_players, tk_plays, tk_max = con.execute("""
        SELECT (SELECT COUNT(*) FROM tk),
               (SELECT COUNT(DISTINCT nfl_id) FROM tk),
               (SELECT COUNT(*) FROM (SELECT DISTINCT game_id, play_id FROM tk)),
               (SELECT MAX(n) FROM (
                  SELECT COUNT(DISTINCT nfl_id) AS n FROM tk GROUP BY game_id, play_id))
        """).fetchone()
    geometry_scope_note = {
        "rows_in_file": int(tk_rows),
        "players_tracked": int(tk_players),
        "max_tracked_players_per_play": int(tk_max),
        "plays_covered": int(tk_plays),
        "players_tracked_share_of_player_play": round(
            int(tk_players) / sample["sample_pp_players"], 6),
        "note": ("this table measures the sample file, not data availability; "
                 "multiple = 0 here is structural."),
    }

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
        "match": match,
        "availability": cand["availability"],
        "preflight": cand["preflight"],
        "candidate_status": cand["status_totals"],
        "ambiguity_candidates": cand["tie_counts"],
        "candidate_turn_keys": cand["turn_keys"],
        "geometry_scope_note": geometry_scope_note,
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
            "turns_with_opponent_within_3yd": int((turns.n_opp_3 > 0).sum()),
            "turns_with_route_runner_within_3yd": int((turns.n_route_3 > 0).sum()),
        },
        "injury": inj,
    }

    # Per-turn table, for the record. No outcome association is computed here.
    turns_cols = [
        "game_id", "play_id", "nfl_id", "frame_start", "position", "alignment",
        "abs_turn_angle", "apex_retention", "speed_retention", "entry_speed",
        "coverage_assignment", "cushion", "pass_result", "tackle", "assist",
        "sep_available",
        "n_opp_any",
        *[f"n_opp_{r}" for r in RADII],
        *[f"n_route_{r}" for r in RADII],
        "nearest_opp_dist", "nearest_route_dist", "align_dist",
        "match_status", "match_radius_yd", "match_inference", "match_rule",
        "matched_receiver_nfl_id", "match_distance_yd", "matched_receiver_separation",
    ]
    turns_out = turns[[c for c in turns_cols if c in turns.columns]].copy()
    turns_out.insert(0, "stamp", STAMP_SAMPLE)

    turns_out.to_csv(OUT / "feasibility_turns.csv", index=False, float_format="%.6g")
    ambiguity.to_csv(OUT / "feasibility_ambiguity.csv", index=False)
    routes.to_csv(OUT / "feasibility_routes.csv", index=False)

    # Candidate availability: one row per band turn per population. Sorted, so a
    # second run is byte-identical.
    cand_out = cand["candidates"][ol.ordered_columns(cand["candidates"], [
        "population", *ol.TURN_KEY, "own_team", "pp_opp_rows", "pp_opp_route_rows",
        "pp_opp_route_sep_rows", "pp_candidate_status"])].sort_values(
        ["population", *ol.TURN_KEY], kind="mergesort")
    cand_out.to_csv(OUT / "feasibility_candidates.csv", index=False)

    # The pre-flight, one row per population: P1-P5, the ceiling, both gates and
    # the verdict string.
    pre_rows = []
    for stamp in (STAMP_SAMPLE, STAMP_FULL):
        pre_rows.append({k: (";".join(v) if isinstance(v, list) else v)
                         for k, v in cand["preflight"][stamp].items()})
    pd.DataFrame(pre_rows).to_csv(OUT / "feasibility_preflight.csv", index=False)

    detail_out = cand["detail"][ol.ordered_columns(cand["detail"], [
        "population", *ol.TURN_KEY, "candidate_rank", "candidate_nfl_id",
        "candidate_position", "candidate_route", "candidate_separation",
        "candidate_target", "own_team", "pp_candidate_status",
        "n_candidates_on_turn", "n_candidates_with_separation",
        "turn_separation_gap_yd"])].sort_values(
        ["population", *ol.TURN_KEY, "candidate_nfl_id"], kind="mergesort")
    detail_out.to_csv(OUT / "feasibility_ambiguity_candidates.csv", index=False)

    (OUT / "feasibility_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")

    report = {
        "sample_band_turns": summary["turns"]["sample_band_turns"],
        "sample_band_plays": summary["turns"]["sample_band_plays"],
        "tackles": summary["turns"]["tackles"],
        "match": {k: match[k] for k in
                  ("radius_primary_yd", "unique", "multiple", "none_observed",
                   "unobservable", "attributed_fraction", "unique_with_separation")},
        "full_band_turns": full["full_band_turns"],
        "injury_mentions": inj["injury_mentions"],
        "preflight": {},
    }
    for stamp in (STAMP_SAMPLE, STAMP_FULL):
        p = cand["preflight"][stamp]
        report["preflight"][stamp] = {
            "band_turns": p["band_turns"],
            "p1_unsided_turns": p["p1_unsided_turns"],
            "p2_turns_with_candidate": p["p2_turns_with_candidate"],
            "p2_ceiling_share": p["p2_ceiling_share"],
            "p3_cleared": p["p3_cleared"],
            "p4_cleared": p["p4_cleared"],
            "verdict": p["verdict"],
        }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
