"""Turn-level context: band turns joined to ``player_play.csv`` play context.

The turn measures (angle, speeds, retention) come from tracking and live in the
turn table. The play context (pass/run, coverage label, cushion, defensive
events) lives in ``player_play.csv``. This module joins the two on the turn key
``(game_id, play_id, nfl_id)`` and writes one row per band turn.

Nothing here infers an opponent, a radius or an attribution: the join key is the
turning player's own row, and every band turn has exactly one such row. The band
and its edges are taken from :mod:`bdb27.outcome_link` so this table cannot drift
from the rest of the project.

``play_type`` is derived from ``pass_result`` presence — a play with a
``pass_result`` is a pass, one without is a run — and is checked against
``play_description``. ``pass_result = 'R'`` rows are kept in ``pass`` (the play
was a dropback that ended in a scramble) and flagged separately in
``pass_scramble``; the three-way breakdown is reported alongside the two-way one
and is never merged into it.

No count here gates anything. Every field is reported with the denominator it
was measured against.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import duckdb
import pandas as pd

from bdb27 import outcome_link as ol
from bdb27.geometric_match import file_sha256, git_head

BAND_LO, BAND_HI = ol.BAND_LO, ol.BAND_HI
KEY: tuple[str, ...] = ("game_id", "play_id", "nfl_id", "frame_start")

#: Extra ``player_play`` columns beyond the base set in
#: :func:`bdb27.outcome_link.load_player_play`, with their cast types.
#: ``tackle`` / ``assist`` are counts of 0/1/2 in the raw file, so they are
#: integers, not booleans; ``caused_forced_fumble`` / ``tackle_for_loss`` hold
#: ``TRUE``/``FALSE``/``0``/``1`` and cast as booleans.
PP_EXTRA: tuple[tuple[str, str], ...] = (
    ("pass_result", "VARCHAR"),
    ("play_description", "VARCHAR"),
    ("team_coverage_man_zone", "VARCHAR"),
    ("team_coverage_type", "VARCHAR"),
    ("coverage_assignment", "VARCHAR"),
    ("coverage_assignment_at_snap", "VARCHAR"),
    ("cushion", "DOUBLE"),
    ("tackle", "INTEGER"),
    ("assist", "INTEGER"),
    ("tackle_for_loss", "BOOLEAN"),
    ("caused_forced_fumble", "BOOLEAN"),
    ("down", "INTEGER"),
    ("yards_to_go", "INTEGER"),
)

#: The coverage-context fields the availability table is built from, in the
#: order the note reports them.
CONTEXT_FIELDS: tuple[str, ...] = (
    "team_coverage_man_zone",
    "team_coverage_type",
    "coverage_assignment",
    "coverage_assignment_at_snap",
    "pass_result",
    "cushion",
    "separation_at_pass_forward",
    "down",
    "yards_to_go",
    "tackle",
    "assist",
    "tackle_for_loss",
    "caused_forced_fumble",
    "route_ran",
    "target",
)

#: Wordings that mark a description as a designed run. The check is one-way: a
#: run description matching these is evidence the turn is not a coverage rep; a
#: description that does not match is not evidence of the opposite.
RUN_LOOK_WORDS: tuple[str, ...] = (
    "left tackle", "right tackle", "left guard", "right guard",
    "left end", "right end", "up the middle",
)

RULE = (
    "one row per band turn (30 <= |abs_turn_angle| < 60, setting='game', "
    "position_group='DB', valid) joined on the turn's own (game_id, play_id, "
    "nfl_id) row in player_play.csv; play_type = 'pass' where pass_result is "
    "present, 'run' where it is absent; no inference, no radius, no attribution")
PROVENANCE = "FULL"


# ------------------------------------------------------------------ loading
def load_player_play_context(con: duckdb.DuckDBPyConnection,
                             path: str | Path,
                             table: str = "tc_player_play") -> str:
    """``player_play.csv`` as a typed table with the context columns attached."""
    return ol.load_player_play(con, path, table=table, extra=PP_EXTRA)


def build(con: duckdb.DuckDBPyConnection,
          turns_path: str | Path,
          pp_path: str | Path,
          table: str = "turn_context") -> pd.DataFrame:
    """The band turns of ``turns_path`` joined to their play context.

    The band is taken from :func:`bdb27.outcome_link.register_band_turns`, which
    is the project's one band definition. That helper keeps five columns, so the
    full turn rows are read back from the same parquet with the same predicate
    and the resulting key multiset is checked against the helper's — a mismatch
    is a disagreement between two readings of the frozen band, not a small
    subset, and it raises rather than being papered over.

    A row of the turn table is a turn: two turns can share ``frame_start`` and
    differ in ``frame_end`` (overlapping segmentations), and the context row is
    then attached to both. No row is deduplicated.
    """
    keys = "tc_band_keys"
    n_keys = ol.register_band_turns(con, turns_path, keys)
    load_player_play_context(con, pp_path)
    run_look = " OR ".join(
        f"lower(p.play_description) LIKE '%{w}%'" for w in RUN_LOOK_WORDS)
    con.execute(f"""
    CREATE OR REPLACE TABLE tc_band_rows AS
    SELECT * FROM read_parquet('{Path(turns_path).as_posix()}')
    WHERE valid AND setting = 'game' AND position_group = 'DB'
      AND abs_turn_angle >= {BAND_LO} AND abs_turn_angle < {BAND_HI}
    """)
    unmatched = int(con.execute(f"""
    SELECT COUNT(*) FROM (
      (SELECT game_id, play_id, nfl_id, frame_start, COUNT(*) AS c
       FROM tc_band_rows GROUP BY 1, 2, 3, 4
       EXCEPT
       SELECT game_id, play_id, nfl_id, frame_start, COUNT(*) AS c
       FROM {keys} GROUP BY 1, 2, 3, 4)
      UNION ALL
      (SELECT game_id, play_id, nfl_id, frame_start, COUNT(*) AS c
       FROM {keys} GROUP BY 1, 2, 3, 4
       EXCEPT
       SELECT game_id, play_id, nfl_id, frame_start, COUNT(*) AS c
       FROM tc_band_rows GROUP BY 1, 2, 3, 4))
    """).fetchone()[0])
    n_rows = int(con.execute("SELECT COUNT(*) FROM tc_band_rows").fetchone()[0])
    if unmatched or n_rows != n_keys:
        raise ValueError(
            f"band readings disagree: {n_rows} rows from the parquet predicate, "
            f"{n_keys} from outcome_link.register_band_turns, {unmatched} key "
            "multisets differ")
    con.execute(f"""
    CREATE OR REPLACE TABLE {table} AS
    SELECT
      t.*,
      p.team_abbr AS pp_team_abbr,
      p.possession_team AS pp_possession_team,
      p.pass_result,
      p.play_description,
      p.team_coverage_man_zone,
      p.team_coverage_type,
      p.coverage_assignment,
      p.coverage_assignment_at_snap,
      p.cushion,
      p.separation_at_pass_forward,
      p.down,
      p.yards_to_go,
      p.tackle,
      p.assist,
      p.tackle_for_loss,
      p.caused_forced_fumble,
      p.route_ran,
      p.target,
      p.nfl_id IS NOT NULL AS pp_row_present,
      CASE WHEN p.pass_result IS NULL THEN 'run' ELSE 'pass' END AS play_type,
      p.pass_result = 'R' AS pass_scramble,
      ({run_look}) AS run_look_description,
      CASE WHEN p.play_description IS NULL THEN 'unknown'
           WHEN (p.pass_result IS NULL) = ({run_look}) THEN 'agree'
           ELSE 'disagree' END AS description_check
    FROM tc_band_rows t
    LEFT JOIN tc_player_play p
      ON t.game_id = p.game_id AND t.play_id = p.play_id AND t.nfl_id = p.nfl_id
    ORDER BY t.game_id, t.play_id, t.nfl_id, t.frame_start
    """)
    df = con.execute(f"SELECT * FROM {table}").df()
    if len(df) != n_rows:  # a fan-out would mean a duplicate player_play key
        raise ValueError(f"join fan-out: {len(df)} rows for {n_keys} band turns")
    return df


# ------------------------------------------------------------------ counts
def play_type_split(con: duckdb.DuckDBPyConnection,
                    table: str = "turn_context") -> pd.DataFrame:
    """Band turns by ``play_type``, with the scramble split shown separately."""
    rows = con.execute(f"""
    WITH b AS (SELECT COUNT(*) AS n FROM {table})
    SELECT play_type, CASE WHEN pass_scramble THEN 'scramble' ELSE 'not_scramble' END AS bucket,
           COUNT(*) AS n, (SELECT n FROM b) AS denominator
    FROM {table} GROUP BY 1, 2 ORDER BY 1, 2
    """).df()
    rows["share"] = rows["n"] / rows["denominator"]
    return rows


def field_availability(con: duckdb.DuckDBPyConnection,
                       table: str = "turn_context",
                       fields: Sequence[str] = CONTEXT_FIELDS) -> pd.DataFrame:
    """Present count for every context field, over the band and per ``play_type``.

    ``present`` is ``NOT NULL`` on the turn's own ``player_play`` row; a turn with
    no row counts as absent for every field. Nothing is dropped for being thin.
    """
    selects = []
    for f in fields:
        selects.append(f"COUNT({f}) AS \"{f}\"")
    selects.append("COUNT(*) AS n_all")
    selects.append("COUNT(*) FILTER (WHERE play_type = 'pass') AS n_pass")
    selects.append("COUNT(*) FILTER (WHERE play_type = 'run') AS n_run")
    agg = con.execute(
        f"SELECT {', '.join(selects)} FROM {table}").df().iloc[0]
    out = pd.DataFrame({
        "field": list(fields),
        "present_band": [int(agg[f]) for f in fields],
        "n_band": int(agg["n_all"]),
        "present_pass": [
            int(con.execute(f"SELECT COUNT({f}) FROM {table} WHERE play_type = 'pass'")
                .fetchone()[0]) for f in fields],
        "n_pass": int(agg["n_pass"]),
        "present_run": [
            int(con.execute(f"SELECT COUNT({f}) FROM {table} WHERE play_type = 'run'")
                .fetchone()[0]) for f in fields],
        "n_run": int(agg["n_run"]),
    })
    out["share_band"] = out["present_band"] / out["n_band"]
    out["share_pass"] = out["present_pass"] / out["n_pass"]
    out["share_run"] = out["present_run"] / out["n_run"]
    return out


def demand_scope(con: duckdb.DuckDBPyConnection,
                 turns_path: str | Path,
                 table: str = "tc_player_play") -> pd.DataFrame:
    """The same pass/run split over *all* valid game DB turns, for the claim.

    Reported as context for what the split does to a band-share claim: it gives
    the band share over every turn, over pass turns only and over run turns only.
    The denominators are printed with every row.
    """
    con.execute(f"""
    CREATE OR REPLACE TABLE tc_all_turns AS
    SELECT game_id, play_id, nfl_id, abs_turn_angle
    FROM read_parquet('{Path(turns_path).as_posix()}')
    WHERE valid AND setting = 'game' AND position_group = 'DB'
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE tc_all_split AS
    SELECT t.game_id, t.play_id, t.nfl_id, t.abs_turn_angle,
           CASE WHEN p.pass_result IS NULL THEN 'run' ELSE 'pass' END AS play_type,
           p.nfl_id IS NOT NULL AS pp_row_present,
           (t.abs_turn_angle >= {BAND_LO} AND t.abs_turn_angle < {BAND_HI}) AS in_band
    FROM tc_all_turns t
    LEFT JOIN {table} p
      ON t.game_id = p.game_id AND t.play_id = p.play_id AND t.nfl_id = p.nfl_id
    """)
    rows = con.execute("""
    SELECT 'all' AS scope, COUNT(*) AS turns, COUNT(*) FILTER (WHERE in_band) AS band_turns
    FROM tc_all_split
    UNION ALL
    SELECT play_type, COUNT(*), COUNT(*) FILTER (WHERE in_band)
    FROM tc_all_split GROUP BY 1
    UNION ALL
    SELECT 'no_player_play_row', COUNT(*), COUNT(*) FILTER (WHERE in_band)
    FROM tc_all_split WHERE NOT pp_row_present
    """).df()
    rows["band_share_of_scope"] = rows["band_turns"] / rows["turns"]
    return rows


def medians(con: duckdb.DuckDBPyConnection,
            table: str = "turn_context") -> pd.DataFrame:
    """Median cushion and median |turn angle| per ``play_type``.

    Descriptive only: a thin subset still gets a median, printed with its n.
    """
    return con.execute(f"""
    SELECT play_type, COUNT(*) AS n,
           MEDIAN(cushion) AS median_cushion,
           COUNT(cushion) AS n_cushion,
           MEDIAN(abs_turn_angle) AS median_abs_turn_angle,
           COUNT(abs_turn_angle) AS n_abs_turn_angle
    FROM {table} GROUP BY 1 ORDER BY 1
    """).df()


# ------------------------------------------------------------------ outputs
def _display(path: Path, root: Path) -> str:
    """Repo-relative label for a path, without resolving symlinks (data/raw is one)."""
    try:
        return str(Path(path).absolute().relative_to(root))
    except ValueError:  # pragma: no cover - only for paths outside the repo
        return str(Path(path).absolute())


def write_manifest(out_dir: str | Path, inputs: Sequence[str | Path],
                   outputs: Sequence[str | Path], config: dict,
                   extra: dict | None = None) -> dict:
    """sha256 and size for every input and output, plus git HEAD and a stamp."""
    out = Path(out_dir)
    root = Path(__file__).resolve().parents[2]
    manifest: dict[str, Any] = {
        "written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "git_head": git_head(root),
        "rule": RULE,
        "provenance": PROVENANCE,
        "config": config,
        "inputs": {_display(Path(p), root): {"sha256": file_sha256(p),
                                             "bytes": Path(p).stat().st_size}
                   for p in inputs if Path(p).exists()},
        "outputs": {_display(Path(p), root): {"sha256": file_sha256(p),
                                              "bytes": Path(p).stat().st_size}
                    for p in outputs if Path(p).exists()},
    }
    if extra:
        manifest.update(extra)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                      encoding="utf-8")
    return manifest
