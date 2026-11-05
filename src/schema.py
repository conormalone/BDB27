"""schema — the verified data contract.

Every column/type/unit here was read off the actual files with DuckDB (see
notes/schema.md §4 for the query). These are *requirements*, not a full column
inventory: a file may carry extra columns, but every column listed here must
exist with the listed type. Anything else raises :class:`common.SchemaError`
before a stage does any work.

Unit notes learned from the data (all verified, not assumed):

* ``dis`` is **per-frame displacement in yards**, not cumulative distance.
  Over a 40-yard dash it stays in [0.0, 1.44]; multiplying by 10 Hz reproduces
  ``s``. Cumulative distance must be reconstructed from ``x``/``y``.
* ``dir`` and ``o`` are degrees; ``dir`` wraps (e.g. 275 -> 89 across the 0/360
  boundary), so direction of travel must be estimated from the coordinate
  path, not read from ``dir``.
* ``sack`` is 0 / 0.5 / 1.0 (split credit), not a boolean.
* ``player_get_off`` is seconds and is capped at 2.0 s in the data.
* ``player_play.lined_up_position`` uses EDGE / INTERIOR_LINE, never DE/DT/NT.
"""

from __future__ import annotations

from typing import Mapping, Sequence

try:  # script execution from src/ and package execution both work
    import common as C
except ImportError:  # pragma: no cover
    from . import common as C  # type: ignore


#: key -> {column: dtype family}. Dtype families map to DuckDB type names.
REQUIRED: dict[str, dict[str, str]] = {
    "games": {
        "game_id": "int", "game_key": "int", "season": "int",
        "season_type": "str", "week": "int",
        "home_team_abbr": "str", "visitor_team_abbr": "str",
    },
    "players": {
        "nfl_id": "int", "display_name": "str", "draft_year": "int",
        "nfl_position": "str",
    },
    "combine_results": {
        "draft_year": "int", "nfl_id": "int", "combine_position": "str",
        "combine_height": "float", "combine_weight": "int",
        "ten_yd_split": "float", "forty": "float", "vertical": "float",
        "broad_jump": "int", "three_cone": "float", "short_shuttle": "float",
        "bench_reps": "int",
    },
    "combine_tracking": {
        "draft_year": "int", "event_id": "str", "nfl_id": "int",
        "entity_type": "str", "time": "timestamp", "drill_type": "str",
        "drill_name": "str", "attempt": "int", "x": "float", "y": "float",
        "s": "float", "a": "float", "dis": "float", "dir": "float",
    },
    "player_play": {
        "game_id": "int", "play_id": "int", "team_abbr": "str",
        "nfl_id": "int", "lined_up_position": "str", "quarter": "int",
        "down": "int", "yards_to_go": "int", "possession_team": "str",
        "yardline_side": "str", "yardline_number": "int", "game_clock": "time",
        "pre_snap_home_score": "int", "pre_snap_visitor_score": "int",
        "play_direction": "str", "dropback_duration": "float",
        "pass_result": "str",
        "pass_rushers_encountered": "int", "pressure_allowed": "bool",
        "sack_allowed": "float", "time_to_pressure_allowed": "float",
        "quick_pressure": "bool", "unblocked_pressure": "bool",
        "blitzing": "bool", "player_get_off": "float", "sack": "float",
        "time_to_pressure": "float", "time_to_qb_hurry": "float",
    },
    "game_tracking": {
        "game_id": "int", "play_id": "int", "nfl_id": "int",
        "time": "timestamp", "x": "float", "y": "float", "s": "float",
        "a": "float", "dis": "float", "o": "float", "dir": "float",
        "event": "str",
    },
}

#: key -> {column: unit string}. Rendered into notes/schema.md.
UNITS: dict[str, dict[str, str]] = {
    "combine_tracking": {
        "time": "timestamp (wall clock)", "x": "yd", "y": "yd", "s": "yd/s",
        "a": "yd/s^2", "dis": "yd (PER FRAME, not cumulative)",
        "dir": "degrees",
    },
    "game_tracking": {
        "time": "timestamp (wall clock)", "x": "yd (0-120 field)", "y": "yd",
        "s": "yd/s", "a": "yd/s^2", "dis": "yd (PER FRAME, not cumulative)",
        "o": "degrees (orientation)", "dir": "degrees",
    },
    "player_play": {
        "player_get_off": "s (capped at 2.0)",
        "time_to_pressure": "s (event-only; null unless pressure recorded)",
        "time_to_pressure_allowed": "s (event-only)",
        "time_to_qb_hurry": "s (event-only)",
        "dropback_duration": "s",
        "game_clock": "clock time (MM:SS, a TIME in DuckDB)",
        "quick_pressure": "bool (event-only; null is NOT 'no pressure')",
        "unblocked_pressure": "bool",
        "blitzing": "bool",
        "sack": "0 / 0.5 / 1.0 (split credit)",
    },
    "combine_results": {
        "combine_height": "inches", "combine_weight": "lb",
        "ten_yd_split": "s", "forty": "s", "vertical": "inches",
        "broad_jump": "inches", "three_cone": "s", "short_shuttle": "s",
        "bench_reps": "reps",
    },
}

_DTYPE_FAMILIES = {
    "int": {"BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT",
            "UINTEGER", "USMALLINT", "UTINYINT"},
    "float": {"DOUBLE", "FLOAT", "REAL", "DECIMAL"},
    "str": {"VARCHAR", "STRING", "TEXT"},
    "bool": {"BOOLEAN", "BOOL"},
    "timestamp": {"TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_NS",
                  "TIMESTAMP_MS", "TIMESTAMP_S"},
    "date": {"DATE"},
    "time": {"TIME"},
}


def family(duckdb_type: str) -> str | None:
    t = str(duckdb_type).upper()
    base = t.split("(")[0].strip()
    for name, members in _DTYPE_FAMILIES.items():
        if base in members:
            return name
    return None


def resolve_files(cfg: Mapping, key: str, mode: str = "sample"):
    """File paths for a schema key; ``game_tracking`` is mode-dependent."""
    if key == "game_tracking":
        return C.tracking_sources(cfg, mode)
    return C.source_path(cfg, key)


def describe(con, cfg: Mapping, key: str, mode: str = "sample") -> dict[str, str]:
    """Actual {column: duckdb_type} for a file key."""
    files = resolve_files(cfg, key, mode)
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"{key}: source missing: {missing}")
    rows = con.execute(
        f"DESCRIBE SELECT * FROM {C.read_csv_sql(files, cfg)}").fetchall()
    return {r[0]: r[1] for r in rows}


def assert_schema(con, cfg: Mapping, key: str,
                  required: Mapping[str, str] | None = None,
                  mode: str = "sample") -> dict[str, str]:
    """Fail loudly if a required column is missing or has the wrong type family."""
    required = required or REQUIRED[key]
    actual = describe(con, cfg, key, mode)
    problems: list[str] = []
    for col, want in required.items():
        if col not in actual:
            problems.append(f"missing column {col!r} (expected {want})")
            continue
        got = actual[col]
        if family(got) != want:
            problems.append(
                f"column {col!r} has type {got} (family {family(got)}), "
                f"expected family {want}")
    if problems:
        raise C.SchemaError(
            f"SCHEMA MISMATCH for {key}:\n  " + "\n  ".join(problems) +
            f"\n  actual columns: {sorted(actual)}")
    return actual


# --------------------------------------------------------------------------- #
# Unit sanity
# --------------------------------------------------------------------------- #
def assert_units(con, cfg: Mapping, key: str, mode: str = "sample") -> dict[str, float]:
    """Range gut-checks that catch a unit error (feet vs yards, ms vs s).

    Scoping is deliberate:

    * ``combine_tracking`` is checked on the straight-line sprint
      (``physical.drill``) and on ``entity_type = 'PLAYER'`` only. The file also
      carries a ``BALL`` entity whose speeds reach 25.8 yd/s and whose y ranges
      over 60 yd; that is a projectile, not a person, and it must never enter a
      physical-time measurement.
    * ``game_tracking`` is checked on the whole file. Both trackers are on a
      football field, so field bounds apply there; they do not apply to drill
      coordinates, which are local to the drill setup.
    """
    b = C.section(cfg, "schema")
    if key == "combine_tracking":
        drill = C.section(cfg, "physical")["drill"]
        where = f"entity_type = 'PLAYER' AND drill_name = '{drill}'"
        field_bounds = False
    elif key == "game_tracking":
        where = "TRUE"
        field_bounds = True
    else:
        raise KeyError(f"no unit checks defined for {key!r}")

    files = resolve_files(cfg, key, mode)
    rel = C.read_csv_sql(files, cfg)
    lo, hi = b["x_y_range"]
    amax, amin = b["accel_range"][1], b["accel_range"][0]
    ahi = b["angle_range"][1]
    smax = b["speed_max_yds"]
    q = f"""
      SELECT min(x) xmin, max(x) xmax, min(y) ymin, max(y) ymax,
             min(s) smin, max(s) smax, min(a) amin, max(a) amax,
             min(dis) dmin, max(dis) dmax, max(dir) dirmax, min(dir) dirmin,
             count(*) n
      FROM {rel} WHERE {where}"""
    v = con.execute(q).fetchone()
    got = dict(zip(["xmin", "xmax", "ymin", "ymax", "smin", "smax", "amin",
                    "amax", "dmin", "dmax", "dirmax", "dirmin", "n"], v))
    if not got["n"]:
        raise C.SchemaError(f"UNIT CHECK FAILED for {key}: no rows matched {where!r}")
    problems = []
    if field_bounds:
        for c, lim, op in (("xmin", lo, "<"), ("ymin", lo, "<"),
                           ("xmax", hi, ">"), ("ymax", hi, ">")):
            if got[c] is not None and ((op == "<" and got[c] < lim) or
                                       (op == ">" and got[c] > lim)):
                problems.append(f"{c}={got[c]} {op} {lim} (unit error?)")
    if got["smax"] is not None and got["smax"] > smax:
        problems.append(f"max speed {got['smax']} yd/s > {smax} (unit error?)")
    if got["smin"] is not None and got["smin"] < 0:
        problems.append(f"negative speed {got['smin']}")
    if got["amin"] is not None and got["amin"] < amin:
        problems.append(f"min accel {got['amin']} < {amin}")
    if got["amax"] is not None and got["amax"] > amax:
        problems.append(f"max accel {got['amax']} > {amax}")
    if got["dmin"] is not None and got["dmin"] < 0:
        problems.append(f"negative per-frame displacement {got['dmin']}")
    for c in ("dirmin", "dirmax"):
        if got[c] is not None and (got[c] < 0 or got[c] > ahi):
            problems.append(f"{c}={got[c]} outside [0,360]")
    if problems:
        raise C.SchemaError(f"UNIT CHECK FAILED for {key} ({where}):\n  " +
                            "\n  ".join(problems))
    return {k: (float(v) if v is not None else float("nan")) for k, v in got.items()}


def assert_columns_present(con, cfg: Mapping, key: str,
                           columns: Sequence[str], mode: str = "sample") -> None:
    actual = describe(con, cfg, key, mode)
    missing = [c for c in columns if c not in actual]
    if missing:
        raise C.SchemaError(f"{key}: missing columns {missing}")


# --------------------------------------------------------------------------- #
# Convention checks (vocabulary, not just type)
# --------------------------------------------------------------------------- #
def assert_vocabulary(con, cfg: Mapping) -> dict[str, list[str]]:
    """Verify the position vocabularies the config relies on actually occur."""
    pos = C.section(cfg, "positions")
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    seen = [r[0] for r in con.execute(
        f"SELECT DISTINCT lined_up_position FROM {pp}").fetchall()]
    seen_set = {s for s in seen if s}
    missing = [p for p in pos["dl_linedup"] if p not in seen_set]
    if missing:
        raise C.SchemaError(
            f"player_play.lined_up_position is missing configured DL values "
            f"{missing}; actual values are {sorted(seen_set)}")
    legacy = {"DE", "DT", "NT"} & seen_set
    if legacy:
        raise C.SchemaError(
            f"player_play.lined_up_position contains {sorted(legacy)}; this file "
            f"uses EDGE/INTERIOR_LINE, so a DE/DT/NT assumption is wrong")
    return {"lined_up_position": sorted(seen_set)}


# --------------------------------------------------------------------------- #
# Tracking contract (runtime assertions on assumptions about full data)
# --------------------------------------------------------------------------- #
def tracking_contract(con, cfg: Mapping, mode: str = "sample") -> dict:
    """Check the structural assumptions later stages rely on.

    Hard checks (raise ``SchemaError``):
      * the modal frame interval equals 1/onset.hz within 20 ms;
      * a ``ball_snap`` event exists on at least 95% of plays;
      * no duplicate (game, play, nfl_id, time) rows.

    In ``full`` mode it additionally requires >= 20 tracked players per play.
    The sample cannot meet that (it is a 1-4 player-per-play draw), so the check
    is reported there and enforced only on full data.
    """
    files = resolve_files(cfg, "game_tracking", mode)
    rel = C.read_csv_sql(files, cfg)
    hz = float(C.section(cfg, "onset")["hz"])
    nominal = 1.0 / hz

    dt = con.execute(f"""
        WITH t AS (SELECT time,
                          lag(time) OVER (PARTITION BY game_id, play_id, nfl_id
                                          ORDER BY time) pt
                   FROM {rel})
        SELECT median(date_diff('millisecond', pt, time)) / 1000.0,
               quantile_cont(date_diff('millisecond', pt, time) / 1000.0, 0.99)
        FROM t WHERE pt IS NOT NULL""").fetchone()

    snaps = con.execute(f"""
        WITH p AS (SELECT game_id, play_id,
                          bool_or(event = 'ball_snap') has
                   FROM {rel} GROUP BY 1,2)
        SELECT count(*), count(*) FILTER (WHERE has) FROM p""").fetchone()

    ppp = con.execute(f"""
        WITH p AS (SELECT game_id, play_id, count(DISTINCT nfl_id) n
                   FROM {rel} GROUP BY 1,2)
        SELECT min(n), median(n), max(n) FROM p""").fetchone()

    dups = con.execute(f"""
        SELECT count(*) FROM (
            SELECT game_id, play_id, nfl_id, time, count(*) c
            FROM {rel} GROUP BY 1,2,3,4 HAVING count(*) > 1)""").fetchone()[0]

    problems = []
    if dt[0] is None:
        problems.append("no consecutive frames: cannot establish the sampling rate")
    elif abs(float(dt[0]) - nominal) > 0.02:
        problems.append(
            f"modal frame interval {dt[0]:.3f}s != expected {nominal:.3f}s")
    snap_share = (snaps[1] / snaps[0]) if snaps[0] else 0.0
    if snap_share < 0.95:
        problems.append(
            f"ball_snap present on only {snap_share:.1%} of plays (need >= 95%)")
    if dups:
        problems.append(f"{dups} duplicate (game, play, nfl_id, time) keys")
    if mode == "full" and (ppp[0] or 0) < 20:
        problems.append(
            f"full tracking has only {ppp[0]} players on the sparsest play; "
            f"expected ~22. This input is not full game tracking.")
    if problems:
        raise C.SchemaError(
            f"TRACKING CONTRACT FAILED ({mode}):\n  " + "\n  ".join(problems))

    return {
        "mode": mode,
        "nominal_hz": hz,
        "median_frame_interval_s": dt[0],
        "p99_frame_interval_s": dt[1],
        "plays": int(snaps[0]),
        "plays_with_ball_snap": int(snaps[1]),
        "ball_snap_share": round(snap_share, 6),
        "players_per_play_min": int(ppp[0]),
        "players_per_play_median": float(ppp[1]),
        "players_per_play_max": int(ppp[2]),
        "duplicate_keys": int(dups),
    }
