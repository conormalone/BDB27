#!/usr/bin/env python3
"""03_game_snaps — Phase 3: one row per DL pass-rush snap.

Implements, verbatim from TASK.md:

  "**3. Game snaps** (full data only for final; scripted and unit-tested now)."

  "Primary snap set: pass-rush snaps by DL (non-blitz). Blitzers and non-DL are
   excluded from primary analyses."

  "Per snap: `player_get_off`, pre-snap depth to the line, alignment (edge vs.
   interior, shade), stance (if labeled or inferable), blitz flag, down and
   distance, score margin, quarter, game, team, opponent."

  "**Direct reaction measure:** snap-to-onset latency from tracking, with the
   Phase 1 onset definition. Also onset-to-cross time (the in-game counterpart
   of Combine physical time)."

  "Outcomes: `quick_pressure` (null -> false per Q2/D101 ...), time to pressure
   after crossing the line (time_to_pressure minus get-off, censored at dropback
   end, using `dropback_duration` only for censoring), sack (exploratory). Never
   analyze `time_to_pressure` only among pressured snaps."

  "Write one row per snap; modeling stages read only these tables."

Four spec items cannot be implemented as literally written against this release.
Each is reported, not papered over:

1. **Blitzers.** There is no blitz-call column. ``player_play.blitzing`` is true
   on 92% of DL pass-rush reps, false on 99.6% of the CB rows of the same plays,
   and null exactly on run plays (``outputs/phase3/blitz_diagnostic.csv`` and
   ``notes/blockers.md`` B9). It reads as a per-player *pass-rush participation*
   flag, not "the defence sent an extra rusher". Excluding ``blitzing IS TRUE``
   would delete the primary set (27 rows left in the complete label file), so
   the exclusion is **not applied**; the raw flag is carried so a future rule
   can filter without a re-run. Row-level ``blitz_exclusion_status`` records it.
2. **Stance.** No column anywhere labels three-point vs stand-up, and no labelled
   subset exists to validate an inference. ``stance`` is null with
   ``stance_status = NOT_AVAILABLE_NO_LABEL``.
3. **Shade.** No shade label exists. ``shade`` is null with
   ``shade_status = NOT_AVAILABLE_NO_LABEL``.
4. **Assigned blockers / blocker count.** Removed by the Q4 amendment (D98).
   ``pass_rushers_encountered`` is descriptive only: it is a *per-blocker* count
   (0-3), not a count of rushers on the play, and it never enters a model.

Memory contract
---------------
No raw CSV is loaded into pandas. Both inputs are converted once to Parquet with
an explicit column list (tracking partitioned by ``game_id``); the measurement
loop reads one game at a time (a few thousand rows) and writes a part file per
batch of games. The label-side base table (44,002 rows in the complete release)
is the only table held in memory.

Outputs (under outputs/phase3/, all carrying the mode provenance)
-----------------------------------------------------------------
snaps.parquet                  DELIVERABLE: one row per (game, play, rusher)
snaps_schema.csv               column, dtype, unit, description
phase3_summary.json            counts Phase 4 depends on + sample limits
exclusion_counts.csv           rows removed per reason (no silent drops)
null_provenance.csv            quick_pressure / time_to_pressure null rates
null_cooccurrence.csv          quick_pressure x time_to_pressure presence
snap_counts_by_player.csv      snaps, games, edge/interior per rusher
blitz_diagnostic.csv           why the blitz exclusion was not applied
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402
import physical as P  # noqa: E402
import schema as S  # noqa: E402
import stats as ST  # noqa: E402

STAGE = "03_game_snaps"

BLITZ_EXCLUSION_STATUS = "NOT_APPLIED_FLAG_IS_NOT_A_BLITZ_INDICATOR"

#: Columns read from player_play.csv. Explicit, never ``SELECT *``.
PP_COLUMNS = [
    "game_id", "play_id", "team_abbr", "nfl_id", "lined_up_position",
    "quarter", "down", "yards_to_go", "possession_team", "yardline_side",
    "yardline_number", "pre_snap_home_score", "pre_snap_visitor_score",
    "play_direction", "pass_result", "dropback_duration",
    "pass_rushers_encountered", "pressure_allowed", "sack_allowed",
    "time_to_pressure_allowed", "quick_pressure", "unblocked_pressure",
    "blitzing", "player_get_off", "sack", "time_to_pressure",
]

#: Columns read from game tracking. Explicit, never ``SELECT *``.
TRACK_COLUMNS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "event"]

#: Explicit casts make the cache schema stable even for a header-only input,
#: where DuckDB would otherwise infer every column as VARCHAR.
_TRACK_CASTS = {
    "game_id": "BIGINT", "play_id": "BIGINT", "nfl_id": "BIGINT",
    "time": "TIMESTAMP", "x": "DOUBLE", "y": "DOUBLE", "s": "DOUBLE",
    "event": "VARCHAR",
}
_PP_CASTS = {
    "game_id": "BIGINT", "play_id": "BIGINT", "team_abbr": "VARCHAR",
    "nfl_id": "BIGINT", "lined_up_position": "VARCHAR", "quarter": "INTEGER",
    "down": "INTEGER", "yards_to_go": "INTEGER", "possession_team": "VARCHAR",
    "yardline_side": "VARCHAR", "yardline_number": "INTEGER",
    "pre_snap_home_score": "INTEGER", "pre_snap_visitor_score": "INTEGER",
    "play_direction": "VARCHAR", "pass_result": "VARCHAR",
    "dropback_duration": "DOUBLE", "pass_rushers_encountered": "INTEGER",
    "pressure_allowed": "BOOLEAN", "sack_allowed": "DOUBLE",
    "time_to_pressure_allowed": "DOUBLE", "quick_pressure": "BOOLEAN",
    "unblocked_pressure": "BOOLEAN", "blitzing": "BOOLEAN",
    "player_get_off": "DOUBLE", "sack": "DOUBLE", "time_to_pressure": "DOUBLE",
}

#: The deliverable per-snap table: (column, DuckDB type, unit, description).
#: Single source of truth for the writer, the schema assertion, snaps_schema.csv
#: and the empty-table fallback.
SNAP_FIELDS: list[tuple[str, str, str, str]] = [
    # identity / context -----------------------------------------------------
    ("game_id", "BIGINT", "id", "game identifier"),
    ("play_id", "BIGINT", "id", "play identifier"),
    ("nfl_id", "BIGINT", "id", "rusher (defensive player) identifier"),
    ("season", "INTEGER", "year", "season from games.csv"),
    ("season_type", "VARCHAR", "category", "REG / PRE / POST"),
    ("week", "INTEGER", "week", "week from games.csv"),
    ("team_abbr", "VARCHAR", "category", "defensive team (the rusher's team)"),
    ("opponent_team", "VARCHAR", "category", "offensive team (possession_team)"),
    ("possession_team", "VARCHAR", "category", "offensive team"),
    ("quarter", "INTEGER", "quarter", "period"),
    ("down", "INTEGER", "down", "down (1-4)"),
    ("yards_to_go", "INTEGER", "yd", "distance to the first-down marker"),
    ("score_margin_def", "INTEGER", "points",
     "defensive team score minus opponent score, pre-snap"),
    ("play_direction", "VARCHAR", "category", "offence direction: left / right"),
    # alignment --------------------------------------------------------------
    ("lined_up_position", "VARCHAR", "category",
     "player_play.lined_up_position (the Q3 authority)"),
    ("alignment_class", "VARCHAR", "category",
     "EDGE / INTERIOR / NON_DL, from lined_up_position"),
    ("shade", "VARCHAR", "category",
     "always null: no shade label exists in the release"),
    ("shade_status", "VARCHAR", "status", "NOT_AVAILABLE_NO_LABEL"),
    ("stance", "VARCHAR", "category",
     "always null: no stance label exists and none is inferred"),
    ("stance_status", "VARCHAR", "status", "NOT_AVAILABLE_NO_LABEL"),
    # play type / blitz ------------------------------------------------------
    ("is_pass_play", "BOOLEAN", "flag",
     "pass_result non-empty (C/I/S/R/IN); '' is a run"),
    ("pass_result", "VARCHAR", "category", "C / I / S / R / IN / '' (run)"),
    ("blitz_flag", "VARCHAR", "category",
     "raw player_play.blitzing as TRUE / FALSE / NULL (tri-state preserved)"),
    ("blitz_flag_status", "VARCHAR", "status", "SET / NULL"),
    ("blitz_exclusion_status", "VARCHAR", "status",
     "NOT_APPLIED_FLAG_IS_NOT_A_BLITZ_INDICATOR (notes/blockers.md B9)"),
    ("pass_rushers_encountered_max", "INTEGER", "count",
     "descriptive only: max per-blocker pass_rushers_encountered on the play"),
    ("pass_rushers_encountered_rows", "INTEGER", "count",
     "how many offensive rows carried pass_rushers_encountered"),
    # label ------------------------------------------------------------------
    ("player_get_off", "DOUBLE", "s",
     "as published in player_play; never recomputed"),
    ("get_off_status", "VARCHAR", "status", "OK / OUT_OF_RANGE"),
    # outcomes ---------------------------------------------------------------
    ("quick_pressure_raw", "VARCHAR", "category", "TRUE / FALSE / NULL"),
    ("quick_pressure_binary", "BOOLEAN", "flag",
     "quick_pressure IS TRUE; null -> false (Q2/D101, an ASSUMPTION)"),
    ("time_to_pressure_raw", "DOUBLE", "s",
     "as published; null = no pressure event"),
    ("post_cross_pressure_time_s", "DOUBLE", "s",
     "time_to_pressure - player_get_off, observed events only"),
    ("post_cross_event", "BOOLEAN", "flag",
     "1 when a pressure time is observed, 0 otherwise (never condition on it)"),
    ("post_cross_censor_time_s", "DOUBLE", "s",
     "dropback_duration - player_get_off, used ONLY for censoring"),
    ("post_cross_status", "VARCHAR", "status",
     "OBSERVED / OBSERVED_DROPBACK_TOO_SHORT / CENSORED / "
     "CENSORED_DROPBACK_TOO_SHORT / CENSOR_MISSING / NEGATIVE_OBSERVED"),
    ("dropback_duration", "DOUBLE", "s",
     "play-level, verified consistent within play"),
    ("sack", "DOUBLE", "credit", "0 / 0.5 / 1.0; exploratory"),
    # tracking-derived -------------------------------------------------------
    ("tracking_present", "BOOLEAN", "flag", "the rusher has frames for this play"),
    ("snap_event_status", "VARCHAR", "status", "OK / CONFLICTING / MISSING"),
    ("snap_event_spread_s", "DOUBLE", "s",
     "max-min of ball_snap timestamps on the play"),
    ("snap_x", "DOUBLE", "yd", "rusher x at the first frame at/after the snap"),
    ("los_x", "DOUBLE", "yd", "line of scrimmage x (notes/schema.md section 6)"),
    ("depth_yd", "DOUBLE", "yd",
     "signed pre-snap depth to the line (positive = lined up onside)"),
    ("depth_status", "VARCHAR", "status",
     "OK / DEPTH_NONPOSITIVE / DEPTH_EXCEEDS_MAX / PLAY_METADATA_MISSING / "
     "NO_SNAP_EVENT / NO_POST_SNAP_FRAMES / NO_TRACKING"),
    ("n_pre_snap_frames", "INTEGER", "frames", "frames before the snap"),
    ("n_post_snap_frames", "INTEGER", "frames", "frames at/after the snap"),
    ("baseline_speed_presnap", "DOUBLE", "yd/s",
     "median pre-snap speed in the baseline window (context only)"),
    ("baseline_status", "VARCHAR", "status", "OK / INSUFFICIENT_PRE_SNAP_FRAMES"),
    ("onset_status", "VARCHAR", "status",
     "FOUND / NOT_DETECTED / NO_TRACKING / NO_SNAP_EVENT / NO_POST_SNAP_FRAMES"),
    ("onset_latency_s", "DOUBLE", "s",
     "snap-to-onset latency (same onset definition as Phase 2)"),
    ("onset_reason", "VARCHAR", "text", "the onset detector's own reason string"),
    ("max_speed_post_snap", "DOUBLE", "yd/s", "max provided speed after the snap"),
    ("speed_cap_flag", "BOOLEAN", "flag",
     "max_speed_post_snap above onset.speed_cap_yds"),
    ("cross_status", "VARCHAR", "status",
     "OK / CROSSED_BEFORE_ONSET / NOT_REACHED / NOT_INTERPOLABLE / NO_ONSET / "
     "DEPTH_NONPOSITIVE / NO_TRACKING / NO_SNAP_EVENT / NO_POST_SNAP_FRAMES"),
    ("cross_time_s", "DOUBLE", "s", "tracked LOS crossing, seconds from the snap"),
    ("onset_to_cross_s", "DOUBLE", "s", "cross_time_s - onset_latency_s"),
    ("cross_interpolated", "BOOLEAN", "flag", "crossing interpolated between frames"),
    ("cross_gap_policy", "VARCHAR", "status",
     "none / bridged / forbidden_first_second / too_long"),
    ("cross_minus_get_off_s", "DOUBLE", "s",
     "tracked crossing minus the published label (evidence, not a correction)"),
    # set flags --------------------------------------------------------------
    ("in_primary_set", "BOOLEAN", "flag",
     "DL alignment AND pass play AND get_off in range (blitz not excludable, B9)"),
    ("exclusion_reason", "VARCHAR", "status",
     "NON_DL_ALIGNMENT / RUN_PLAY / GET_OFF_OUT_OF_RANGE / PASS_RESULT_MISSING; "
     "null = in primary set"),
    ("analysis_ready", "BOOLEAN", "flag",
     "in_primary_set AND tracking AND usable depth (what Phase 4 can fit)"),
    ("reaction_ready", "BOOLEAN", "flag",
     "analysis_ready AND onset found AND crossing interpolable"),
    ("provenance", "VARCHAR", "stamp", "mode provenance"),
]

SNAP_COLUMNS = [f[0] for f in SNAP_FIELDS]


# --------------------------------------------------------------------------- #
# Small typed coercions (pandas hands back NaN / numpy scalars)
# --------------------------------------------------------------------------- #
def _is_null(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return False


def _opt_float(v):
    return None if _is_null(v) else float(v)


def _opt_int(v):
    return None if _is_null(v) else int(v)


def _opt_bool(v):
    return None if _is_null(v) else bool(v)


def _median(values):
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else 0.5 * (vals[mid - 1] + vals[mid])


# --------------------------------------------------------------------------- #
# Parquet cache
# --------------------------------------------------------------------------- #
def _parquet_glob(directory: Path) -> str:
    return str(Path(directory).as_posix()) + "/**/*.parquet"


def _source_fingerprint(cfg, key: str, mode: str) -> list[dict]:
    out = []
    for f in S.resolve_files(cfg, key, mode):
        st = Path(f).stat()
        out.append({"path": str(Path(f).resolve()), "size": int(st.st_size),
                    "mtime_ns": int(st.st_mtime_ns)})
    return out


def ensure_parquet(con, cfg, key: str, mode: str, columns, casts,
                   *, partition_by: str | None = None,
                   force: bool = False) -> Path:
    """Convert a raw CSV (or CSV list) to Parquet with explicit columns.

    Idempotent: a manifest records the source fingerprint, the column list and
    the parsed row count; a matching cache is reused unless ``force``.
    """
    root = C.paths(cfg, mode)["parquet"]
    out = root / (f"{key}_{mode}" if partition_by else f"{key}_{mode}.parquet")
    manifest_path = root / f"{key}_{mode}.manifest.json"
    fingerprint = _source_fingerprint(cfg, key, mode)

    if not force and manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            m = None
        if (m and m.get("sources") == fingerprint
                and m.get("columns") == list(columns)
                and m.get("partition_by") == partition_by):
            _assert_cache_complete(con, out, m, partition_by)
            return out

    root.mkdir(parents=True, exist_ok=True)
    if out.is_dir():
        import shutil
        shutil.rmtree(out)
    elif out.exists():
        out.unlink()

    compression = C.section(cfg, "runtime")["parquet_compression"]
    select = ", ".join(f"CAST({c} AS {casts[c]}) AS {c}" for c in columns)
    src = C.read_csv_sql(S.resolve_files(cfg, key, mode), cfg)
    options = ["FORMAT PARQUET", f"COMPRESSION '{compression}'"]
    if partition_by:
        options += [f"PARTITION_BY ({partition_by})", "OVERWRITE_OR_IGNORE"]
    con.execute(f"COPY (SELECT {select} FROM {src}) TO '{out.as_posix()}' "
                f"({', '.join(options)})")

    rel = (f"read_parquet('{_parquet_glob(out)}', hive_partitioning=true)"
           if partition_by else f"read_parquet('{out.as_posix()}')")
    rows = int(con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0])
    manifest = {"sources": fingerprint, "columns": list(columns),
                "partition_by": partition_by, "parsed_rows": rows}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    _assert_cache_complete(con, out, manifest, partition_by)
    return out


def _assert_cache_complete(con, out: Path, manifest: dict,
                           partition_by: str | None) -> None:
    """A killed conversion must not be mistaken for a complete cache.

    The row count is re-derived from the Parquet metadata (cheap: no column
    read) and, for a partitioned cache, every ``game_id=`` directory must be
    represented. This is assumption A18 in notes/assumptions.md.
    """
    if partition_by:
        rel = f"read_parquet('{_parquet_glob(out)}', hive_partitioning=true)"
        rows = int(con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0])
        games = int(con.execute(
            f"SELECT count(DISTINCT {partition_by}) FROM {rel}").fetchone()[0])
        dirs = len([p for p in Path(out).glob(f"{partition_by}=*") if p.is_dir()])
        if rows != int(manifest["parsed_rows"]) or games != dirs:
            raise C.SchemaError(
                f"PARQUET CACHE INCOMPLETE for {out}: {rows} rows / {games} games "
                f"in Parquet vs {manifest['parsed_rows']} recorded and {dirs} "
                f"partition directories. Re-run with --force.")
    else:
        rel = f"read_parquet('{out.as_posix()}')"
        rows = int(con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0])
        if rows != int(manifest["parsed_rows"]):
            raise C.SchemaError(
                f"PARQUET CACHE INCOMPLETE for {out}: {rows} rows vs "
                f"{manifest['parsed_rows']} recorded. Re-run with --force.")


def _pp_relation(cfg, mode: str) -> str:
    out = C.paths(cfg, mode)["parquet"] / f"player_play_{mode}.parquet"
    return f"read_parquet('{out.as_posix()}')"


def _tracking_relation(cfg, mode: str) -> str:
    out = C.paths(cfg, mode)["parquet"] / f"game_tracking_{mode}"
    return f"read_parquet('{_parquet_glob(out)}', hive_partitioning=true)"


# --------------------------------------------------------------------------- #
# Label side
# --------------------------------------------------------------------------- #
def load_base(con, cfg, mode: str):
    """Every row carrying a ``player_get_off``, with play and game context.

    The base set includes the CB/SLOT_CB rows that Q3 excludes: they stay in the
    table with ``exclusion_reason = NON_DL_ALIGNMENT`` so the exclusion is
    countable and no row disappears silently.
    """
    pp = _pp_relation(cfg, mode)
    games = C.read_csv_sql(C.source_path(cfg, "games"), cfg)

    # dropback_duration and pass_result are verified one-value-per-play in the
    # complete file. pass_rushers_encountered is per-blocker, so it is carried
    # as max + row count and never treated as a play-level rusher count.
    return con.execute(f"""
        WITH pp AS (SELECT * FROM {pp}),
        plays AS (
            SELECT game_id, play_id,
                   max(pass_result) AS pass_result,
                   max(dropback_duration) AS dropback_duration,
                   max(pass_rushers_encountered) AS pre_max,
                   count(pass_rushers_encountered) AS pre_rows,
                   count(DISTINCT pass_result) AS n_pass_result,
                   count(DISTINCT dropback_duration) AS n_dropback
            FROM pp GROUP BY 1, 2)
        SELECT d.game_id, d.play_id, d.nfl_id, d.team_abbr, d.possession_team,
               d.lined_up_position, d.quarter, d.down, d.yards_to_go,
               d.yardline_side, d.yardline_number, d.play_direction,
               d.pre_snap_home_score, d.pre_snap_visitor_score,
               d.player_get_off, d.quick_pressure, d.time_to_pressure,
               d.sack, d.blitzing,
               p.pass_result, p.dropback_duration, p.pre_max, p.pre_rows,
               p.n_pass_result, p.n_dropback,
               g.season, g.season_type, g.week,
               g.home_team_abbr, g.visitor_team_abbr
        FROM pp d
        JOIN plays p USING (game_id, play_id)
        LEFT JOIN {games} g USING (game_id)
        WHERE d.player_get_off IS NOT NULL
        ORDER BY d.game_id, d.play_id, d.nfl_id""").df()


def load_snap_times(con, cfg, mode: str):
    """One row per play carrying a ball_snap event, with its timestamp spread."""
    tr = _tracking_relation(cfg, mode)
    return con.execute(f"""
        SELECT game_id, play_id, min(time) AS snap_time,
               max(time) AS snap_time_max, count(*) AS n_events
        FROM {tr}
        WHERE event = 'ball_snap'
        GROUP BY 1, 2
        ORDER BY 1, 2""").df()


def assert_input_assumptions(con, cfg, mode: str, base) -> dict:
    """Runtime assertions for the assumptions Phase 3 relies on.

    Each one is listed in ``notes/assumptions.md`` with the failure it prevents.
    These are structural gates: a mismatch stops the stage instead of producing a
    plausible table built on a wrong vocabulary or a play-level field that is not
    play-level.
    """
    snaps_cfg = C.section(cfg, "snaps")
    pp = _pp_relation(cfg, mode)

    # A14 — pass_result vocabulary is the verified one.
    seen = {str(r[0]) for r in con.execute(
        f"SELECT DISTINCT pass_result FROM {pp}").fetchall()
        if r[0] is not None}
    allowed = set(snaps_cfg["pass_results"]) | {""}
    unexpected = sorted(seen - allowed)
    if unexpected:
        raise C.SchemaError(
            f"PASS-RESULT VOCABULARY CHANGED: {unexpected} not in {sorted(allowed)}; "
            f"the pass-play definition would silently misclassify them")

    # A15 — the per-snap key is unique in player_play.
    rows = int(con.execute(f"SELECT count(*) FROM {pp}").fetchone()[0])
    keys = int(con.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT game_id, play_id, nfl_id "
        f"FROM {pp})").fetchone()[0])
    if rows != keys:
        raise C.SchemaError(
            f"PLAYER_PLAY KEY NOT UNIQUE: {rows - keys} duplicate "
            f"(game_id, play_id, nfl_id) rows")

    # A16 — dropback_duration and pass_result are play-level, so the label-side
    # join cannot silently pick one of several values.
    if len(base):
        bad = base[(base["n_pass_result"] > 1) | (base["n_dropback"] > 1)]
        if len(bad):
            raise C.SchemaError(
                f"PLAY-LEVEL FIELD NOT PLAY-LEVEL: {len(bad)} play(s) carry more "
                f"than one pass_result or dropback_duration")

    # A17 — blitzing behaves as documented in D104 (participation, not a blitz
    # call). If the full release changes this, the B9 question must be re-opened.
    min_pass = float(snaps_cfg["blitz_flag_min_share_on_pass"])
    max_run = float(snaps_cfg["blitz_flag_max_share_on_run"])
    edge = ", ".join(f"'{p}'" for p in C.section(cfg, "positions")["dl_linedup"])
    stats = con.execute(f"""
        SELECT
          count(*) FILTER (WHERE pass_result <> '') AS n_pass,
          count(*) FILTER (WHERE pass_result <> '' AND blitzing) AS n_pass_set,
          count(*) FILTER (WHERE pass_result = '') AS n_run,
          count(*) FILTER (WHERE pass_result = '' AND blitzing) AS n_run_set
        FROM {pp}
        WHERE player_get_off IS NOT NULL AND lined_up_position IN ({edge})""").fetchone()
    n_pass, n_pass_set, n_run, n_run_set = [int(v or 0) for v in stats]
    pass_share = (n_pass_set / n_pass) if n_pass else 0.0
    run_share = (n_run_set / n_run) if n_run else 0.0
    if (n_pass and pass_share < min_pass) or run_share > max_run:
        raise C.SchemaError(
            f"BLITZ-FLAG SEMANTICS CHANGED: set on {pass_share:.3f} of pass reps "
            f"(need >= {min_pass}) and {run_share:.3f} of run reps (need <= "
            f"{max_run}). Re-open notes/blockers.md B9 before trusting the "
            f"primary set.")

    return {"pass_result_values": sorted(seen),
            "player_play_rows": rows,
            "blitz_set_share_pass": round(pass_share, 6),
            "blitz_set_share_run": round(run_share, 6)}


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def line_of_scrimmage(yardline_side, yardline_number, possession_team,
                      play_direction):
    """LOS x in field coordinates. Formula validated in notes/schema.md section 6.

    Returns ``None`` when an input is missing or ``play_direction`` is neither
    left nor right; the caller writes an explicit status instead of guessing.
    """
    if (_is_null(yardline_side) or _is_null(yardline_number)
            or _is_null(possession_team) or play_direction not in ("left", "right")):
        return None
    own_side_right = (yardline_side == possession_team and play_direction == "right")
    opp_side_left = (yardline_side != possession_team and play_direction == "left")
    if own_side_right or opp_side_left:
        return 10.0 + float(yardline_number)
    return 110.0 - float(yardline_number)


def depth_to_line(x_at_snap: float, los_x: float, play_direction: str) -> float:
    """Signed distance from the rusher to the line, positive when onside.

    The defence lines up on the far side of the ball from the offence's
    direction of travel, so for a right-moving offence the rusher sits at
    ``x > LOS`` and must move toward decreasing x.
    """
    sign = 1.0 if play_direction == "right" else -1.0
    return sign * (float(x_at_snap) - float(los_x))


# --------------------------------------------------------------------------- #
# One rep
# --------------------------------------------------------------------------- #
def measure_rep(rec: dict, frames: list, snap, cfg) -> dict:
    """Tracking-derived columns for one (game, play, rusher).

    ``frames`` are ``(time, x, y, s)`` tuples sorted by time; ``snap`` is the
    ball_snap timestamp or ``None``. Never raises for a degenerate trace: every
    failure is a status string, and no failure is written as a zero.
    """
    o = C.section(cfg, "onset")
    snaps_cfg = C.section(cfg, "snaps")
    phys = C.section(cfg, "physical")
    nominal = 1.0 / float(o["hz"])
    out = {
        "tracking_present": bool(frames),
        "snap_event_status": "MISSING",
        "snap_event_spread_s": None,
        "snap_x": None, "los_x": None, "depth_yd": None,
        "depth_status": "NO_TRACKING",
        "n_pre_snap_frames": 0, "n_post_snap_frames": 0,
        "baseline_speed_presnap": None, "baseline_status": "NO_TRACKING",
        "onset_status": "NO_TRACKING", "onset_latency_s": None,
        "onset_reason": "", "max_speed_post_snap": None, "speed_cap_flag": None,
        "cross_status": "NO_TRACKING", "cross_time_s": None,
        "onset_to_cross_s": None, "cross_interpolated": None,
        "cross_gap_policy": None, "cross_minus_get_off_s": None,
    }
    if not frames:
        return out
    if snap is None:
        out["depth_status"] = "NO_SNAP_EVENT"
        out["onset_status"] = "NO_SNAP_EVENT"
        out["cross_status"] = "NO_SNAP_EVENT"
        return out

    times = [(f[0] - snap).total_seconds() for f in frames]
    xs = [float(f[1]) for f in frames]
    ss = [_opt_float(f[3]) for f in frames]

    post_idx = [i for i, t in enumerate(times) if t >= 0.0]
    out["n_pre_snap_frames"] = sum(1 for t in times if t < 0.0)
    out["n_post_snap_frames"] = len(post_idx)
    if not post_idx:
        out["depth_status"] = "NO_POST_SNAP_FRAMES"
        out["onset_status"] = "NO_POST_SNAP_FRAMES"
        out["cross_status"] = "NO_POST_SNAP_FRAMES"
        return out
    i0 = post_idx[0]
    out["snap_x"] = xs[i0]

    los = line_of_scrimmage(rec["yardline_side"], rec["yardline_number"],
                            rec["possession_team"], rec["play_direction"])
    if los is None:
        out["depth_status"] = "PLAY_METADATA_MISSING"
        out["onset_status"] = "NO_SNAP_EVENT"
        out["cross_status"] = "NO_SNAP_EVENT"
        return out
    out["los_x"] = los
    depth = depth_to_line(xs[i0], los, rec["play_direction"])
    out["depth_yd"] = depth
    max_depth = float(snaps_cfg["max_depth_yd"])
    if depth <= 0:
        out["depth_status"] = "DEPTH_NONPOSITIVE"
    elif depth > max_depth:
        out["depth_status"] = "DEPTH_EXCEEDS_MAX"
    else:
        out["depth_status"] = "OK"

    # Baseline is context only: the onset threshold is absolute, so a
    # contaminated baseline would be a misleading number, not a wrong onset.
    win = float(o["baseline_window_s"])
    min_b = int(o["min_baseline_frames"])
    base = [ss[i] for i, t in enumerate(times)
            if -win <= t < 0.0 and ss[i] is not None]
    if len(base) >= min_b:
        out["baseline_speed_presnap"] = _median(base)
        out["baseline_status"] = "OK"
    else:
        out["baseline_status"] = "INSUFFICIENT_PRE_SNAP_FRAMES"

    post_speeds = [ss[i] for i in post_idx if ss[i] is not None]
    if post_speeds:
        out["max_speed_post_snap"] = max(post_speeds)
        out["speed_cap_flag"] = bool(max(post_speeds) > float(o["speed_cap_yds"]))

    speeds = [0.0 if s is None else s for s in ss]
    onset = P.detect_onset(times, speeds, cfg, search_from=0.0,
                           baseline_from=-win)
    out["onset_reason"] = onset["reason"]
    if not onset["found"]:
        out["onset_status"] = "NOT_DETECTED"
        out["cross_status"] = "NO_ONSET"
        return out
    out["onset_status"] = "FOUND"
    out["onset_latency_s"] = float(onset["onset_s"])

    if out["depth_status"] != "OK":
        out["cross_status"] = "DEPTH_NONPOSITIVE"
        return out

    sign = 1.0 if rec["play_direction"] == "right" else -1.0
    # axis increases toward the line: the rusher moves against the offence.
    axis = [-sign * (x - xs[i0]) for x in xs]
    cr = P.time_at_distance_qc(times, axis, depth, first_index=i0,
                               onset_s=onset["onset_s"], nominal_dt=nominal,
                               max_gap_s=float(o["interpolate_max_gap_s"]),
                               first_second_s=float(phys["first_second_s"]))
    out["cross_interpolated"] = bool(cr["interpolated"])
    out["cross_gap_policy"] = cr["gap_policy"]
    if not cr["ok"]:
        out["cross_status"] = ("NOT_INTERPOLABLE" if cr["crossed"]
                               else "NOT_REACHED")
        return out
    out["cross_time_s"] = float(cr["time_s"])
    out["cross_minus_get_off_s"] = (float(cr["time_s"])
                                    - float(rec["player_get_off"]))
    if float(cr["time_s"]) < float(onset["onset_s"]):
        # The rusher was already across the line when the sustained-speed
        # threshold fired (slow start, then acceleration). The crossing is
        # kept as evidence but "time from onset to cross" is not a duration.
        out["cross_status"] = "CROSSED_BEFORE_ONSET"
        return out
    out["cross_status"] = "OK"
    out["onset_to_cross_s"] = float(cr["time_s"]) - float(onset["onset_s"])
    return out


# --------------------------------------------------------------------------- #
# Label-derived statuses
# --------------------------------------------------------------------------- #
def _tri_bool(v):
    if _is_null(v):
        return None
    return bool(v)


def _blitz_flag(value) -> tuple[str, str]:
    b = _tri_bool(value)
    if b is None:
        return "NULL", "NULL"
    return ("TRUE" if b else "FALSE"), "SET"


def _get_off_status(value: float, cfg) -> str:
    if _is_null(value):
        return "MISSING"
    lo, hi = C.section(cfg, "schema")["get_off_range"]
    return "OK" if float(lo) <= float(value) <= float(hi) else "OUT_OF_RANGE"


def _post_cross_outcome(rec: dict) -> dict:
    """Survival-ready post-crossing pressure row.

    ``time_to_pressure - player_get_off`` is the observed time; the event flag is
    "a pressure time exists". ``dropback_duration - player_get_off`` is the
    censoring time and never defines the observed time. Rows without a pressure
    time are censored rows, not dropped rows.
    """
    ttp = _opt_float(rec["time_to_pressure"])
    dbd = _opt_float(rec["dropback_duration"])
    go = float(rec["player_get_off"])
    out = {"post_cross_pressure_time_s": None, "post_cross_event": False,
           "post_cross_censor_time_s": None, "post_cross_status": "CENSOR_MISSING"}
    censor = None if dbd is None else dbd - go
    if censor is not None:
        out["post_cross_censor_time_s"] = censor
    if ttp is not None:
        t = ttp - go
        out["post_cross_pressure_time_s"] = t
        out["post_cross_event"] = True
        if t < 0:
            out["post_cross_status"] = "NEGATIVE_OBSERVED"
        elif censor is not None and censor <= 0:
            out["post_cross_status"] = "OBSERVED_DROPBACK_TOO_SHORT"
        else:
            out["post_cross_status"] = "OBSERVED"
    elif censor is None:
        out["post_cross_status"] = "CENSOR_MISSING"
    elif censor <= 0:
        out["post_cross_status"] = "CENSORED_DROPBACK_TOO_SHORT"
    else:
        out["post_cross_status"] = "CENSORED"
    return out


def _alignment_class(position, cfg) -> str:
    pos = C.section(cfg, "positions")
    if position in pos["edge_linedup"]:
        return "EDGE"
    if position in pos["interior_linedup"]:
        return "INTERIOR"
    return "NON_DL"


def _is_pass_play(value, cfg) -> bool:
    """Pass play = pass_result in the configured vocabulary.

    Verified categories are C / I / S / R / IN; ``""`` (run) is not in the list
    and is therefore a run by exclusion, which is what the data shows.
    """
    if value is None or _is_null(value):
        return False
    return str(value) in set(C.section(cfg, "snaps")["pass_results"])


def _score_margin(rec) -> int | None:
    """Defensive score minus opponent score, pre-snap.

    The row's team is matched against the game's home/visitor abbreviations.
    Null when the join did not supply them rather than guessed.
    """
    home = _opt_float(rec.get("pre_snap_home_score"))
    vis = _opt_float(rec.get("pre_snap_visitor_score"))
    if home is None or vis is None:
        return None
    team = rec.get("team_abbr")
    if team is not None and team == rec.get("home_team_abbr"):
        return int(home - vis)
    if team is not None and team == rec.get("visitor_team_abbr"):
        return int(vis - home)
    return None


def _exclusion_reason(row: dict) -> str | None:
    if row["alignment_class"] == "NON_DL":
        return "NON_DL_ALIGNMENT"
    if row["pass_result"] is None or _is_null(row["pass_result"]):
        return "PASS_RESULT_MISSING"
    if not row["is_pass_play"]:
        return "RUN_PLAY"
    if row["get_off_status"] != "OK":
        return "GET_OFF_OUT_OF_RANGE"
    return None


# --------------------------------------------------------------------------- #
# Per-game loop
# --------------------------------------------------------------------------- #
def _tracking_game_relation(cfg, mode: str, game_id: int) -> str | None:
    """Parquet relation for exactly one game's partition, or ``None``.

    Reading the per-game directory avoids re-expanding a 500+ file glob on every
    statement, which costs about a second per game on this filesystem.
    """
    directory = (C.paths(cfg, mode)["parquet"] / f"game_tracking_{mode}"
                 / f"game_id={int(game_id)}")
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("*.parquet"))
    if not files:
        return None
    glob = "[" + ", ".join(f"'{f.as_posix()}'" for f in files) + "]"
    return f"read_parquet({glob})"


def _fetch_frames(con, cfg, mode: str, game_id: int, pairs):
    """Frames for the requested (play_id, nfl_id) pairs of one game."""
    rel = _tracking_game_relation(cfg, mode, game_id)
    if rel is None:
        return {}
    values = ", ".join(f"({int(p)}, {int(n)})" for p, n in sorted(set(pairs)))
    rows = con.execute(f"""
        SELECT play_id, nfl_id, time, x, y, s
        FROM {rel}
        WHERE (play_id, nfl_id) IN ({values})
        ORDER BY play_id, nfl_id, time""").fetchall()
    grouped: dict[tuple[int, int], list] = {}
    for play_id, nfl_id, t, x, y, s in rows:
        grouped.setdefault((int(play_id), int(nfl_id)), []).append((t, x, y, s))
    return grouped


def build_game_rows(con, cfg, mode: str, game_base, game_snaps) -> list[dict]:
    """All per-snap rows for one game (label side joined to tracking)."""
    game_id = int(game_base.iloc[0]["game_id"])
    pairs = list(zip(game_base["play_id"].astype(int),
                     game_base["nfl_id"].astype(int)))
    frames = _fetch_frames(con, cfg, mode, game_id, pairs)
    out = []
    for rec in game_base.to_dict("records"):
        play_id = int(rec["play_id"])
        nfl_id = int(rec["nfl_id"])
        snap_info = game_snaps.get(play_id)
        snap = None if snap_info is None else snap_info["snap_time"]

        quick_raw = _tri_bool(rec["quick_pressure"])
        row = {
            "game_id": game_id,
            "play_id": play_id,
            "nfl_id": nfl_id,
            "season": _opt_int(rec["season"]),
            "season_type": rec["season_type"],
            "week": _opt_int(rec["week"]),
            "team_abbr": rec["team_abbr"],
            "opponent_team": rec["possession_team"],
            "possession_team": rec["possession_team"],
            "quarter": _opt_int(rec["quarter"]),
            "down": _opt_int(rec["down"]),
            "yards_to_go": _opt_int(rec["yards_to_go"]),
            "score_margin_def": _score_margin(rec),
            "play_direction": rec["play_direction"],
            "lined_up_position": rec["lined_up_position"],
            "alignment_class": _alignment_class(rec["lined_up_position"], cfg),
            "shade": None, "shade_status": "NOT_AVAILABLE_NO_LABEL",
            "stance": None, "stance_status": "NOT_AVAILABLE_NO_LABEL",
            "is_pass_play": _is_pass_play(rec["pass_result"], cfg),
            "pass_result": rec["pass_result"],
            "blitz_exclusion_status": BLITZ_EXCLUSION_STATUS,
            "pass_rushers_encountered_max": _opt_int(rec["pre_max"]),
            "pass_rushers_encountered_rows": int(rec["pre_rows"] or 0),
            "player_get_off": float(rec["player_get_off"]),
            "get_off_status": _get_off_status(rec["player_get_off"], cfg),
            "quick_pressure_raw": ("NULL" if quick_raw is None
                                   else ("TRUE" if quick_raw else "FALSE")),
            "quick_pressure_binary": bool(quick_raw is True),
            "time_to_pressure_raw": _opt_float(rec["time_to_pressure"]),
            "dropback_duration": _opt_float(rec["dropback_duration"]),
            "sack": _opt_float(rec["sack"]),
            "provenance": C.provenance_stamp(cfg, mode),
        }
        blitz, blitz_status = _blitz_flag(rec["blitzing"])
        row["blitz_flag"] = blitz
        row["blitz_flag_status"] = blitz_status
        row.update(_post_cross_outcome(rec))
        row.update(measure_rep(rec, frames.get((play_id, nfl_id), []), snap, cfg))
        if snap_info is not None:
            spread = (snap_info["snap_time_max"] - snap_info["snap_time"]).total_seconds()
            row["snap_event_spread_s"] = spread
            row["snap_event_status"] = "OK" if spread == 0 else "CONFLICTING"
        row["exclusion_reason"] = _exclusion_reason(row)
        row["in_primary_set"] = row["exclusion_reason"] is None
        row["analysis_ready"] = bool(row["in_primary_set"]
                                     and row["tracking_present"]
                                     and row["depth_status"] == "OK")
        row["reaction_ready"] = bool(row["analysis_ready"]
                                     and row["onset_status"] == "FOUND"
                                     and row["cross_status"] == "OK")
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def _snap_select(rel: str) -> str:
    return ", ".join(f"CAST({name} AS {typ}) AS {name}"
                     for name, typ, _, _ in SNAP_FIELDS)


def _empty_select() -> str:
    return ("SELECT " + ", ".join(f"CAST(NULL AS {typ}) AS {name}"
                                  for name, typ, _, _ in SNAP_FIELDS)
            + " LIMIT 0")


def write_part(con, path: Path, rows: list[dict], cfg, mode: str) -> None:
    """Write one batch part with explicit types (an empty batch stays typed).

    Rows go in through a registered DataFrame rather than ``executemany``:
    DuckDB's row-at-a-time insert took 2.7 s per ~800-row part on the Pi, while
    the columnar insert takes 0.04 s. Nulls and types round-trip exactly (asserted
    in tests/test_phase3.py).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    compression = C.section(cfg, "runtime")["parquet_compression"]
    con.execute("CREATE OR REPLACE TEMP TABLE _part ("
                + ", ".join(f"{n} {t}" for n, t, _, _ in SNAP_FIELDS) + ")")
    if rows:
        import pandas as pd
        df = pd.DataFrame([tuple(r.get(n) for n in SNAP_COLUMNS) for r in rows],
                          columns=SNAP_COLUMNS)
        con.register("_part_df", df)
        con.execute("INSERT INTO _part SELECT " + ", ".join(SNAP_COLUMNS)
                    + " FROM _part_df")
        con.unregister("_part_df")
        target = "(SELECT " + _snap_select("_part") + " FROM _part)"
    else:
        target = f"({_empty_select()})"
    con.execute(f"COPY {target} TO '{path.as_posix()}' "
                f"(FORMAT PARQUET, COMPRESSION '{compression}')")
    con.execute("DROP TABLE _part")


def merge_parts(con, cfg, mode: str, parts: list[Path], out: Path) -> int:
    """Merge part files into the deliverable, ordered."""
    out.parent.mkdir(parents=True, exist_ok=True)
    compression = C.section(cfg, "runtime")["parquet_compression"]
    existing = [p for p in parts if p.exists()]
    if not existing:
        target = f"({_empty_select()})"
    else:
        glob = "[" + ", ".join(f"'{p.as_posix()}'" for p in existing) + "]"
        target = (f"(SELECT {_snap_select('t')} FROM read_parquet({glob}) t "
                  f"ORDER BY game_id, play_id, nfl_id)")
    con.execute(f"COPY {target} TO '{out.as_posix()}' "
                f"(FORMAT PARQUET, COMPRESSION '{compression}')")
    return int(con.execute(
        f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0])


# --------------------------------------------------------------------------- #
# Output schema and value assertions
# --------------------------------------------------------------------------- #
def assert_output_schema(con, path: Path, cfg=None) -> dict:
    """Fail loudly if the deliverable does not match SNAP_FIELDS.

    Column set and type family first, then the values where a wrong value would
    be a silent unit or geometry bug.
    """
    actual = {r[0]: r[1] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()}
    problems = []
    for name, typ, _, _ in SNAP_FIELDS:
        if name not in actual:
            problems.append(f"missing column {name!r}")
        elif S.family(actual[name]) != S.family(typ):
            problems.append(f"{name}: type {actual[name]} != {typ}")
    extra = sorted(set(actual) - set(SNAP_COLUMNS))
    if extra:
        problems.append(f"unexpected columns {extra}")
    if problems:
        raise C.SchemaError("SNAP TABLE SCHEMA FAILED:\n  " + "\n  ".join(problems))

    rel = f"read_parquet('{path.as_posix()}')"
    b = C.section(cfg, "schema") if cfg else {
        "depth_range": [-2.0, 8.0], "get_off_range": [0.0, 5.0]}
    checks = {
        "depth_out_of_range":
            f"count(*) FILTER (WHERE depth_yd IS NOT NULL AND (depth_yd < "
            f"{b['depth_range'][0]} OR depth_yd > {b['depth_range'][1]}))",
        "get_off_out_of_range":
            f"count(*) FILTER (WHERE player_get_off < {b['get_off_range'][0]} "
            f"OR player_get_off > {b['get_off_range'][1]})",
        "negative_onset": "count(*) FILTER (WHERE onset_latency_s < 0)",
        "cross_before_snap": "count(*) FILTER (WHERE cross_time_s < 0)",
        "negative_onset_to_cross":
            "count(*) FILTER (WHERE cross_status = 'OK' AND "
            "(onset_to_cross_s IS NULL OR onset_to_cross_s < 0))",
        "missing_provenance": "count(*) FILTER (WHERE provenance IS NULL)",
        "onset_to_cross_mismatch":
            "count(*) FILTER (WHERE onset_to_cross_s IS NOT NULL AND "
            "onset_latency_s IS NOT NULL AND abs(onset_to_cross_s - "
            "(cross_time_s - onset_latency_s)) > 1e-9)",
        "primary_with_reason":
            "count(*) FILTER (WHERE in_primary_set AND exclusion_reason IS NOT NULL)",
        "not_primary_without_reason":
            "count(*) FILTER (WHERE NOT in_primary_set AND exclusion_reason IS NULL)",
        "analysis_ready_not_primary":
            "count(*) FILTER (WHERE analysis_ready AND NOT in_primary_set)",
    }
    got = dict(zip(checks, con.execute(
        f"SELECT {', '.join(checks.values())} FROM {rel}").fetchone()))
    bad = {k: int(v) for k, v in got.items() if v}
    if bad:
        raise C.SchemaError(f"SNAP TABLE VALUE CHECKS FAILED: {bad}")
    return {"rows": int(con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]),
            "columns": len(actual)}


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def exclusion_counts(con, rel: str):
    return con.execute(f"""
        SELECT coalesce(exclusion_reason, 'IN_PRIMARY_SET') AS reason,
               count(*) AS rows
        FROM {rel} GROUP BY 1 ORDER BY 2 DESC, 1""").df()


def null_provenance(con, rel: str):
    """Null rates of the two pressure fields by season / game / team / down.

    Scope is the primary set, which is where the secondary outcome is defined.
    Each row carries the co-occurrence pattern with explicit-false rows so the
    null->false assumption can be defended or corrected on full data.
    """
    blocks = [("season", "CAST(season AS VARCHAR)"),
              ("game", "CAST(game_id AS VARCHAR)"),
              ("team", "team_abbr"),
              ("down", "CAST(down AS VARCHAR)")]
    parts = [f"""
        SELECT '{name}' AS grouping, {expr} AS level,
               count(*) AS n_snaps,
               count(*) FILTER (WHERE quick_pressure_raw = 'NULL') AS n_qp_null,
               count(*) FILTER (WHERE quick_pressure_raw = 'TRUE') AS n_qp_true,
               count(*) FILTER (WHERE quick_pressure_raw = 'FALSE') AS n_qp_false,
               count(*) FILTER (WHERE time_to_pressure_raw IS NULL) AS n_ttp_null,
               count(*) FILTER (WHERE time_to_pressure_raw IS NOT NULL) AS n_ttp_present,
               count(*) FILTER (WHERE time_to_pressure_raw IS NOT NULL
                                  AND quick_pressure_raw = 'FALSE') AS n_time_present_flag_false,
               count(*) FILTER (WHERE time_to_pressure_raw IS NOT NULL
                                  AND quick_pressure_raw = 'NULL') AS n_time_present_flag_null,
               count(*) FILTER (WHERE time_to_pressure_raw IS NULL
                                  AND quick_pressure_raw = 'NULL') AS n_both_null
        FROM {rel} WHERE in_primary_set GROUP BY 2""" for name, expr in blocks]
    parts.append(f"""
        SELECT 'overall' AS grouping, 'ALL' AS level,
               count(*) AS n_snaps,
               count(*) FILTER (WHERE quick_pressure_raw = 'NULL') AS n_qp_null,
               count(*) FILTER (WHERE quick_pressure_raw = 'TRUE') AS n_qp_true,
               count(*) FILTER (WHERE quick_pressure_raw = 'FALSE') AS n_qp_false,
               count(*) FILTER (WHERE time_to_pressure_raw IS NULL) AS n_ttp_null,
               count(*) FILTER (WHERE time_to_pressure_raw IS NOT NULL) AS n_ttp_present,
               count(*) FILTER (WHERE time_to_pressure_raw IS NOT NULL
                                  AND quick_pressure_raw = 'FALSE') AS n_time_present_flag_false,
               count(*) FILTER (WHERE time_to_pressure_raw IS NOT NULL
                                  AND quick_pressure_raw = 'NULL') AS n_time_present_flag_null,
               count(*) FILTER (WHERE time_to_pressure_raw IS NULL
                                  AND quick_pressure_raw = 'NULL') AS n_both_null
        FROM {rel} WHERE in_primary_set""")
    df = con.execute(" UNION ALL ".join(parts)).df()
    df["null_rate_quick_pressure"] = (df["n_qp_null"] / df["n_snaps"]).round(6)
    df["null_rate_time_to_pressure"] = (df["n_ttp_null"] / df["n_snaps"]).round(6)
    order = {"overall": 0, "season": 1, "game": 2, "team": 3, "down": 4}
    df["_o"] = df["grouping"].map(order)
    df = df.sort_values(["_o", "level"]).drop(columns=["_o"])
    return df[["grouping", "level", "n_snaps", "n_qp_null", "n_qp_true",
               "n_qp_false", "n_ttp_null", "n_ttp_present",
               "n_time_present_flag_false", "n_time_present_flag_null",
               "n_both_null", "null_rate_quick_pressure",
               "null_rate_time_to_pressure"]]


def cooccurrence(con, rel: str):
    return con.execute(f"""
        SELECT quick_pressure_raw AS quick_pressure,
               CASE WHEN time_to_pressure_raw IS NULL THEN 'null' ELSE 'present'
               END AS time_to_pressure,
               count(*) AS n_snaps
        FROM {rel} WHERE in_primary_set
        GROUP BY 1, 2 ORDER BY 1, 2""").df()


def player_counts(con, rel: str):
    return con.execute(f"""
        SELECT nfl_id,
               count(*) FILTER (WHERE in_primary_set) AS snaps_primary,
               count(*) FILTER (WHERE analysis_ready) AS snaps_analysis_ready,
               count(*) FILTER (WHERE reaction_ready) AS snaps_reaction_ready,
               count(DISTINCT game_id) FILTER (WHERE in_primary_set) AS games_primary,
               count(*) FILTER (WHERE in_primary_set AND alignment_class = 'EDGE') AS snaps_edge,
               count(*) FILTER (WHERE in_primary_set AND alignment_class = 'INTERIOR') AS snaps_interior,
               count(*) FILTER (WHERE in_primary_set AND blitz_flag = 'TRUE') AS snaps_blitz_flag_true
        FROM {rel} GROUP BY 1
        HAVING count(*) FILTER (WHERE in_primary_set) > 0
        ORDER BY 1""").df()


def blitz_diagnostic(con, rel: str):
    """Evidence that ``blitzing`` is not a blitz-call indicator."""
    return con.execute(f"""
        SELECT 'dl_pass_rush_primary' AS scope, blitz_flag AS value, count(*) AS rows
        FROM {rel} WHERE in_primary_set GROUP BY 1, 2
        UNION ALL
        SELECT 'all_get_off_rows', blitz_flag, count(*)
        FROM {rel} GROUP BY 1, 2
        ORDER BY 1, 2""").df()


# --------------------------------------------------------------------------- #
# Design sensitivity: reps per player vs the detectable floor (D106)
# --------------------------------------------------------------------------- #
def combine_forty_players(con, cfg) -> set[int]:
    """Players with a tracked 40-yard attempt (``entity_type = 'PLAYER'``).

    Reads the Combine tracking file directly rather than Phase 2's outputs, so
    the table can be produced even if Phase 2 has not run. The drill and the
    entity filter are the ones Phase 2 measures.
    """
    ct = C.read_csv_sql(C.source_path(cfg, "combine_tracking"), cfg)
    drill = C.section(cfg, "physical")["drill"]
    rows = con.execute(
        f"SELECT DISTINCT nfl_id FROM {ct} WHERE drill_name = '{drill}' "
        f"AND entity_type = 'PLAYER'").fetchall()
    return {int(r[0]) for r in rows}


def required_players_for_share(share: float) -> int | None:
    """Smallest player count whose detectable floor is at or below ``share``.

    Derived by evaluating ``stats.min_detectable_share`` itself, never by
    re-deriving the formula here.
    """
    for n in range(2, 100001):
        if ST.min_detectable_share(n)["min_share"] <= float(share):
            return n
    return None


def min_reps_sensitivity(con, rel: str, cfg):
    """Player counts and detectable floors at each candidate rep threshold.

    The fittable set is the players who clear the rep threshold *and* have a
    Combine 40-yard attempt: Phase 4 needs both. The floor is computed with
    ``stats.min_detectable_share`` so it cannot drift from the design formula.
    """
    audit = C.section(cfg, "audit")
    soi = float(audit["smallest_effect_of_interest"])
    have_forty = combine_forty_players(con, cfg)
    per_player = player_counts(con, rel)
    ids = per_player["nfl_id"].astype(int).tolist()
    snaps = per_player["snaps_primary"].astype(int).tolist()

    rows = []
    for threshold in [int(t) for t in audit["min_reps_candidates"]]:
        sel = [n for i, n in zip(ids, snaps) if n >= threshold]
        n_all = len(sel)
        n_fit = sum(1 for i, n in zip(ids, snaps)
                    if n >= threshold and i in have_forty)
        floor_all = ST.min_detectable_share(n_all)["min_share"] if n_all >= 2 else None
        floor_fit = ST.min_detectable_share(n_fit)["min_share"] if n_fit >= 2 else None
        rows.append({
            "min_reps_per_player": threshold,
            "players_primary": n_all,
            "players_with_forty_attempt": n_fit,
            "detectable_floor_all_players": (None if floor_all is None
                                             else round(floor_all, 6)),
            "detectable_floor_fittable": (None if floor_fit is None
                                          else round(floor_fit, 6)),
            "smallest_effect_of_interest": soi,
            "meets_soi_all_players": (None if floor_all is None
                                      else bool(floor_all <= soi)),
            "meets_soi_fittable": (None if floor_fit is None
                                   else bool(floor_fit <= soi)),
            "active_threshold": threshold == int(
                audit["min_pass_rush_snaps_per_rusher"]),
        })
    import pandas as pd
    return pd.DataFrame(rows)


def reps_histogram(con, rel: str, cfg):
    """Distribution of primary reps per player, in configured bins."""
    edges = [int(b) for b in C.section(cfg, "audit")["reps_histogram_bins"]]
    snaps = [int(v) for v in _col(
        con, rel, "count(*)", "in_primary_set GROUP BY nfl_id")]
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        n = sum(1 for s in snaps if lo <= s < hi)
        rows.append({"bin_lower": lo, "bin_upper_exclusive": str(hi),
                     "players": n})
    n = sum(1 for s in snaps if s >= edges[-1])
    rows.append({"bin_lower": edges[-1], "bin_upper_exclusive": "inf",
                 "players": n})
    import pandas as pd
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Summary helpers
# --------------------------------------------------------------------------- #
def _dist(values) -> dict:
    vals = sorted(float(v) for v in values if v is not None and not _is_null(v))
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "min": vals[0], "p10": _quantile(vals, 0.10),
            "median": statistics.median(vals), "p90": _quantile(vals, 0.90),
            "max": vals[-1], "mean": sum(vals) / len(vals)}


def _quantile(sorted_vals, q: float):
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _col(con, rel: str, column: str, where: str = "TRUE"):
    return [r[0] for r in con.execute(
        f"SELECT {column} FROM {rel} WHERE {where}").fetchall()]


def _group_counts(con, rel: str, column: str, where: str = "TRUE") -> dict:
    return {str(k): int(v) for k, v in con.execute(
        f"SELECT {column}, count(*) FROM {rel} WHERE {where} "
        f"GROUP BY 1 ORDER BY 1").fetchall()}


def build_summary(con, rel: str, cfg, mode: str, base_rows: int,
                  tracked_plays: int, parts: int) -> dict:
    snaps_cfg = C.section(cfg, "snaps")
    minimum = int(C.section(cfg, "audit")["min_pass_rush_snaps_per_rusher"])
    min_games = int(C.section(cfg, "audit")["min_games_per_rusher"])

    def scalar(sql):
        return con.execute(f"SELECT {sql} FROM {rel}").fetchone()[0]

    per_player = player_counts(con, rel)
    prim = per_player["snaps_primary"].tolist()
    games = per_player["games_primary"].tolist()

    scope_tracking = "SAMPLE_TRACKING" if mode == "sample" else "FULL_TRACKING"
    summary = {
        "stage": STAGE,
        "mode": mode,
        "rows_total": int(scalar("count(*)")),
        "base_rows_from_label_file": int(base_rows),
        "parts": int(parts),
        "plays_with_ball_snap_seen": int(tracked_plays),
        "label_side_counts": {
            "scope": "FULL_RAW_LABEL_FILE",
            "dl_pass_rush_snaps": int(scalar(
                "count(*) FILTER (WHERE in_primary_set)")),
            "edge": int(scalar("count(*) FILTER (WHERE in_primary_set "
                               "AND alignment_class = 'EDGE')")),
            "interior": int(scalar("count(*) FILTER (WHERE in_primary_set "
                                   "AND alignment_class = 'INTERIOR')")),
            "players": int(len(prim)),
            "players_ge_min_snaps": int(sum(1 for v in prim if v >= minimum)),
            "min_snaps_per_player": minimum,
            "players_ge_min_games": int(sum(1 for v in games if v >= min_games)),
            "min_games_per_player": min_games,
            "snaps_per_player": _dist(prim),
            "games_per_player": _dist(games),
            "players_ge_min_snaps_and_games": int(sum(
                1 for s, g in zip(prim, games) if s >= minimum and g >= min_games)),
            "players_all_primary": int(len(prim)),
        },
        "exclusions": {
            "scope": "FULL_RAW_LABEL_FILE",
            "rows_removed": {str(r["reason"]): int(r["rows"])
                             for r in exclusion_counts(con, rel).to_dict("records")
                             if r["reason"] != "IN_PRIMARY_SET"},
        },
        "blitz": {
            "scope": "FULL_RAW_LABEL_FILE",
            "exclusion_applied": False,
            "status": BLITZ_EXCLUSION_STATUS,
            "flag_counts_in_primary": _group_counts(con, rel, "blitz_flag",
                                                    "in_primary_set"),
            "literal_rule_would_leave": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND blitz_flag <> 'TRUE')")),
            "literal_rule_would_remove": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND blitz_flag = 'TRUE')")),
            "pressure_labels_on_flag_true": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND blitz_flag = 'TRUE' "
                "AND quick_pressure_raw <> 'NULL')")),
            "pressure_labels_total": int(scalar(
                "count(*) FILTER (WHERE in_primary_set "
                "AND quick_pressure_raw <> 'NULL')")),
        },
        "tracking_scope": {
            "scope": scope_tracking,
            "rows_with_tracking": int(scalar(
                "count(*) FILTER (WHERE tracking_present)")),
            "primary_with_tracking": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND tracking_present)")),
            "primary_with_usable_depth": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND depth_status = 'OK')")),
            "analysis_ready": int(scalar("count(*) FILTER (WHERE analysis_ready)")),
            "reaction_ready": int(scalar("count(*) FILTER (WHERE reaction_ready)")),
            "onset_status": _group_counts(con, rel, "onset_status", "in_primary_set"),
            "cross_status": _group_counts(con, rel, "cross_status", "in_primary_set"),
            "depth_status": _group_counts(con, rel, "depth_status", "in_primary_set"),
            "snap_event_status": _group_counts(con, rel, "snap_event_status",
                                               "in_primary_set"),
            "gap_policy": _group_counts(con, rel, "cross_gap_policy",
                                        "cross_status = 'OK'"),
        },
        "reaction_measures": {
            "scope": scope_tracking,
            "snap_to_onset_latency_s": _dist(_col(
                con, rel, "onset_latency_s", "reaction_ready")),
            "onset_to_cross_s": _dist(_col(
                con, rel, "onset_to_cross_s", "reaction_ready")),
            "cross_minus_get_off_s": _dist(_col(
                con, rel, "cross_minus_get_off_s",
                "in_primary_set AND cross_status IN ('OK','CROSSED_BEFORE_ONSET')")),
            "max_speed_flags": int(scalar(
                "count(*) FILTER (WHERE speed_cap_flag)")),
        },
        "outcomes": {
            "scope": "FULL_RAW_LABEL_FILE",
            "quick_pressure_true": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND quick_pressure_binary)")),
            "quick_pressure_false_incl_null": int(scalar(
                "count(*) FILTER (WHERE in_primary_set "
                "AND NOT quick_pressure_binary)")),
            "null_to_false_assumption": True,
            "post_cross_status": _group_counts(con, rel, "post_cross_status",
                                               "in_primary_set"),
            "sack_nonnull": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND sack IS NOT NULL)")),
            "sack_positive": int(scalar(
                "count(*) FILTER (WHERE in_primary_set AND sack > 0)")),
        },
        "spec_items_not_implementable": {
            "blitz_exclusion": BLITZ_EXCLUSION_STATUS,
            "stance": "NOT_AVAILABLE_NO_LABEL",
            "shade": "NOT_AVAILABLE_NO_LABEL",
            "blocker_assignment": "REMOVED_BY_Q4_D98",
        },
        "sample_cannot_exercise": _sample_limits(con, rel, mode),
    }
    return summary


def _sample_limits(con, rel: str, mode: str) -> dict:
    seasons = [int(x[0]) for x in con.execute(
        f"SELECT DISTINCT season FROM {rel} WHERE tracking_present "
        f"ORDER BY 1").fetchall()]
    return {
        "mode": mode,
        "seasons_with_tracking": seasons,
        "games_with_tracking": int(con.execute(
            f"SELECT count(DISTINCT game_id) FROM {rel} "
            f"WHERE tracking_present").fetchone()[0]),
        "players_with_tracking": int(con.execute(
            f"SELECT count(DISTINCT nfl_id) FROM {rel} "
            f"WHERE tracking_present").fetchone()[0]),
        "primary_players": int(con.execute(
            f"SELECT count(DISTINCT nfl_id) FROM {rel} "
            f"WHERE in_primary_set").fetchone()[0]),
        "cannot_exercise": [] if mode == "full" else [
            "season-over-season change (single season only)",
            "per-game aggregates for all 22 players (1-4 players per play)",
            "full-season snap counts per player (partial play coverage per game)",
            "the full-file chunk loop at production scale",
        ],
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    cfg, mode, force = C.stage_bootstrap(argv, STAGE)
    if C.is_done(cfg, STAGE, mode) and not force:
        print(f"{STAGE}: checkpoint present and complete; skipping "
              f"(use --force to rebuild).")
        return 0

    p = C.paths(cfg, mode)
    out_dir = p["outputs"] / "phase3"
    out_dir.mkdir(parents=True, exist_ok=True)
    snaps_cfg = C.section(cfg, "snaps")

    summary = None
    n_rows = 0
    with C.timed_stage(cfg, STAGE, mode) as st:
        con = C.duckdb_connect(cfg)
        try:
            # 1. input schema gate (fails loudly, before any work)
            S.assert_schema(con, cfg, "player_play")
            S.assert_schema(con, cfg, "game_tracking", mode=mode)
            S.assert_vocabulary(con, cfg)

            # 2. Parquet caches (explicit columns, tracking partitioned by game)
            ensure_parquet(con, cfg, "player_play", mode, PP_COLUMNS, _PP_CASTS,
                           force=force)
            ensure_parquet(con, cfg, "game_tracking", mode, TRACK_COLUMNS,
                           _TRACK_CASTS, partition_by="game_id", force=force)

            # 3. label side + snap times
            base = load_base(con, cfg, mode)
            assumptions = assert_input_assumptions(con, cfg, mode, base)
            st.note(f"assumptions ok: blitz set pass {assumptions['blitz_set_share_pass']:.3f}")
            snap_df = load_snap_times(con, cfg, mode)
            snap_by_play = {(int(r["game_id"]), int(r["play_id"])): r
                            for r in snap_df.to_dict("records")}
            by_game: dict[int, dict[int, dict]] = {}
            for (gid, pid), r in snap_by_play.items():
                by_game.setdefault(gid, {})[pid] = r

            # 4. per-game measurement, batched into part files
            parts_dir = p["interim"] / "phase3_parts"
            parts_dir.mkdir(parents=True, exist_ok=True)
            per_part = max(1, int(snaps_cfg["games_per_part"]))
            games = sorted({int(g) for g in base["game_id"].tolist()}) if len(base) else []
            batches = [games[i:i + per_part] for i in range(0, len(games), per_part)]
            parts: list[Path] = []
            for bi, batch in enumerate(batches):
                part = parts_dir / f"part_{bi:05d}.parquet"
                parts.append(part)
                if part.exists() and not force:
                    continue
                rows: list[dict] = []
                for gid in batch:
                    game_base = base[base["game_id"] == gid]
                    if game_base.empty:
                        continue
                    rows.extend(build_game_rows(con, cfg, mode, game_base,
                                                by_game.get(gid, {})))
                write_part(con, part, rows, cfg, mode)
            st.note(f"{len(batches)} part(s), {len(base)} base rows")

            # 5. merge -> deliverable
            snaps_path = out_dir / "snaps.parquet"
            n_rows = merge_parts(con, cfg, mode, parts, snaps_path)
            schema_info = assert_output_schema(con, snaps_path, cfg)
            rel = f"read_parquet('{snaps_path.as_posix()}')"

            # 6. evidence artefacts
            ex = exclusion_counts(con, rel)
            C.write_csv(out_dir / "exclusion_counts.csv",
                        ex.to_dict("records"), list(ex.columns),
                        mode=mode, cfg=cfg)
            np_df = null_provenance(con, rel)
            C.write_csv(out_dir / "null_provenance.csv", np_df.to_dict("records"),
                        list(np_df.columns), mode=mode, cfg=cfg)
            co = cooccurrence(con, rel)
            C.write_csv(out_dir / "null_cooccurrence.csv", co.to_dict("records"),
                        list(co.columns), mode=mode, cfg=cfg)
            pc = player_counts(con, rel)
            C.write_csv(out_dir / "snap_counts_by_player.csv",
                        pc.to_dict("records"), list(pc.columns), mode=mode, cfg=cfg)
            bd = blitz_diagnostic(con, rel)
            C.write_csv(out_dir / "blitz_diagnostic.csv", bd.to_dict("records"),
                        list(bd.columns), mode=mode, cfg=cfg)
            sens = min_reps_sensitivity(con, rel, cfg)
            C.write_csv(out_dir / "min_reps_sensitivity.csv",
                        sens.to_dict("records"), list(sens.columns),
                        mode=mode, cfg=cfg)
            hist = reps_histogram(con, rel, cfg)
            C.write_csv(out_dir / "reps_per_player_histogram.csv",
                        hist.to_dict("records"), list(hist.columns),
                        mode=mode, cfg=cfg)
            C.write_csv(out_dir / "snaps_schema.csv",
                        [{"column": n, "dtype": t, "unit": u, "description": d}
                         for n, t, u, d in SNAP_FIELDS],
                        ["column", "dtype", "unit", "description"],
                        mode=mode, cfg=cfg)

            summary = build_summary(con, rel, cfg, mode, len(base),
                                    len(snap_by_play), len(batches))
            summary["output"] = {
                "path": _display_path(snaps_path),
                "rows": int(n_rows), "columns": schema_info["columns"]}
            summary["frame_sampling"] = frame_sampling_profile(con, cfg, mode)
            summary["input_assumptions"] = assumptions
            soi = float(C.section(cfg, "audit")["smallest_effect_of_interest"])
            summary["design_sensitivity"] = {
                "scope": "FULL_RAW_LABEL_FILE",
                "smallest_effect_of_interest": soi,
                "required_players_for_soi": required_players_for_share(soi),
                "active_min_reps_per_player": int(
                    C.section(cfg, "audit")["min_pass_rush_snaps_per_rusher"]),
                "threshold_chosen": False,
                "table": sens.to_dict("records"),
                "reps_histogram": hist.to_dict("records"),
            }
            C.write_json(out_dir / "phase3_summary.json", summary,
                         mode=mode, cfg=cfg)
        finally:
            con.close()

    C.mark_done(cfg, STAGE, mode, {"rows": int(n_rows)})
    if summary is not None:
        print_summary(summary)
    return 0


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise (tests use temp dirs)."""
    try:
        return str(Path(path).relative_to(C.REPO_ROOT))
    except ValueError:
        return str(path)


def frame_sampling_profile(con, cfg, mode: str) -> dict:
    """10 Hz assumption check on the tracking cache actually read."""
    o = C.section(cfg, "onset")
    nominal = 1.0 / float(o["hz"])
    tol = float(o["sampling_tolerance_s"])
    tr = _tracking_relation(cfg, mode)
    row = con.execute(f"""
        WITH t AS (SELECT time, lag(time) OVER (PARTITION BY game_id, play_id, nfl_id
                                                ORDER BY time) pt FROM {tr})
        SELECT median(date_diff('millisecond', pt, time)) / 1000.0,
               count(*) FILTER (WHERE abs(date_diff('millisecond', pt, time) / 1000.0
                                          - {nominal}) <= {tol}),
               count(*)
        FROM t WHERE pt IS NOT NULL""").fetchone()
    return {"nominal_frame_s": nominal,
            "median_frame_s": (None if row[0] is None else float(row[0])),
            "share_within_tolerance": (None if not row[2] else round(row[1] / row[2], 6)),
            "n_intervals": int(row[2] or 0)}


def print_summary(summary: dict) -> None:
    lab = summary["label_side_counts"]
    tr = summary["tracking_scope"]
    print(f"{STAGE}: {summary['output']['rows']} rows -> {summary['output']['path']}")
    print(f"  primary DL pass-rush snaps: {lab['dl_pass_rush_snaps']:,} "
          f"(edge {lab['edge']:,} / interior {lab['interior']:,}); "
          f"players {lab['players']}, >= {lab['min_snaps_per_player']} snaps: "
          f"{lab['players_ge_min_snaps']}")
    print(f"  exclusions: {summary['exclusions']['rows_removed']}")
    print(f"  tracking: analysis_ready {tr['analysis_ready']:,} of "
          f"{tr['primary_with_tracking']:,} primary snaps with tracking; "
          f"reaction_ready {tr['reaction_ready']:,}")
    print(f"  blitz exclusion NOT applied ({summary['blitz']['status']}); "
          f"literal rule would leave "
          f"{summary['blitz']['literal_rule_would_leave']} rows")
    ds = summary.get("design_sensitivity")
    if ds:
        active = [r for r in ds["table"] if r["active_threshold"]][0]
        print(f"  design: SOI {ds['smallest_effect_of_interest']} needs "
              f"{ds['required_players_for_soi']} fittable players; at "
              f"{active['min_reps_per_player']} reps there are "
              f"{active['players_with_forty_attempt']} "
              f"(floor {active['detectable_floor_fittable']}) - threshold not "
              f"chosen")


if __name__ == "__main__":
    sys.exit(main())
