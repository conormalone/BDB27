"""Geometric attribution of a DB turn to an opposing receiver at the break frame.

Specification: ``notes/geometric-attribution-spec.md`` (committed before any fit).
Frozen rule (D197): a turn is attributed to **the single opposing route-runner
within 3 yd** of the turning DB at the break frame. Two or more candidates inside
the radius are ambiguous and dropped; none is dropped; no tie is ever broken by
radius choice, distance order or row order.

The match is an **INFERENCE**. The dataset records no opponent identifier and no
assignment, so a receiver within 3 yd at the break is only *plausibly* the man
covered. Every artefact carries that label.

This module also measures the **misattribution rate** of the weaker elimination
rule used in D199: on turns where both rules can be evaluated, how often does
elimination name a different player from the geometric match?

Nothing here samples at random, and nothing fits a model: the estimator is
``outcome_test``'s, called unchanged on the geometric sample.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import duckdb
import numpy as np
import pandas as pd

# ------------------------------------------------------------------ constants
#: Radii reported. 3 yd decides; 2 and 5 are the pre-declared sensitivity.
RADII_YD: tuple[float, ...] = (2.0, 3.0, 5.0)
PRIMARY_RADIUS_YD = 3.0

#: The break frame is recovered from the turn table's ``(x_centre, y_centre)``
#: against the DB's own frames; a turn whose nearest frame is further than this
#: is dropped, never interpolated.
ALIGN_GATE_YD = 0.25

#: Half a frame at 10 Hz: the candidate slice is the break frame, not a window.
FRAME_TOL_S = 0.05

#: 2**20 - 1. A tracking file with exactly this many data rows is the export
#: truncation. It is *reported*, never a stop: the run continues on what is
#: there and every number carries the truncation stamp.
TRUNCATION_ROWS = 1_048_575

#: Report-only reference points. They are printed beside the measured numbers
#: and never used as a branch: a thin or cohort-shaped file still runs.
MIN_JUDGEABLE = 50
UNOBSERVED_SHARE_REFERENCE = 0.5

TURN_KEY: tuple[str, ...] = ("game_id", "play_id", "nfl_id", "frame_start")

#: The band this project reports: game, DB, 30 <= |angle| < 60.
BAND_LO, BAND_HI = 30.0, 60.0

RULE_STRING = (
    "unique opposing route-runner within 3 yd at the break frame; >=2 candidates "
    "ambiguous and dropped; none dropped; ties never broken by radius, distance "
    "or row order"
)

MATCH_STATUSES: tuple[str, ...] = (
    "unique", "multiple", "none", "unobservable", "untracked",
    "break_frame_unrecovered", "untagged",
)

PROVENANCE_INFERENCE = (
    "INFERENCE — geometric proximity at the break frame; the dataset records no "
    "opponent identifier, so a receiver within the radius is not proof of coverage"
)

TRACKING_COLUMNS: tuple[str, ...] = (
    "game_id", "play_id", "nfl_id", "time", "x", "y", "event",
)


# ------------------------------------------------------------------ plumbing
def connect(memory_limit: str = "1200MB", threads: int = 2) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'; SET threads={int(threads)}")
    return con


def _posix(path: str | Path) -> str:
    return Path(path).as_posix()


def file_sha256(path: str | Path, head_bytes: int | None = None) -> str:
    """sha256 of a file, or of its first ``head_bytes`` if given (resume key)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        if head_bytes is None:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
        else:
            h.update(fh.read(head_bytes))
    return h.hexdigest()


def git_head(repo: str | Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:  # pragma: no cover - only when git is absent
        return "unknown"


def load_band(turns_path: str | Path, *, band: tuple[float, float] = (BAND_LO, BAND_HI),
              setting: str = "game", position_group: str = "DB") -> pd.DataFrame:
    """The band turns of a turn table, filtered by the project's own rule."""
    df = pd.read_parquet(turns_path)
    lo, hi = band
    keep = (df["valid"].astype(bool)
            & df["setting"].eq(setting)
            & df["position_group"].eq(position_group)
            & (df["abs_turn_angle"] >= lo)
            & (df["abs_turn_angle"] < hi))
    return df.loc[keep].reset_index(drop=True)


# ------------------------------------------------------------------ pre-flight
def preflight_counts(path: str | Path, chunk_rows: int = 2_000_000,
                     progress: Callable[[dict], None] | None = None) -> dict:
    """Stream one tracking CSV and measure what the full-file check needs.

    Reads only ``game_id``, ``play_id`` and ``nfl_id``. Memory is bounded by the
    chunk: players-per-play is accumulated in a dict, not in a frame of rows.
    """
    path = Path(path)
    counts: dict[str, int] = {}
    games: set[int] = set()
    players: set[int] = set()
    plays: set[tuple[int, int]] = set()
    rows = 0
    started = time.time()
    expected = None
    if progress is not None:
        expected = _csv_data_rows(path)

    reader = pd.read_csv(
        path, usecols=["game_id", "play_id", "nfl_id"], chunksize=chunk_rows,
        dtype={"game_id": "int64", "play_id": "int64", "nfl_id": "int64"},
        encoding="utf-8", low_memory=False)

    for chunk in reader:
        rows += len(chunk)
        games.update(chunk["game_id"].unique().tolist())
        players.update(chunk["nfl_id"].unique().tolist())
        keys = chunk[["game_id", "play_id"]].drop_duplicates()
        for g, p in keys.itertuples(index=False, name=None):
            plays.add((int(g), int(p)))
        per_play = chunk.groupby(["game_id", "play_id"])["nfl_id"].nunique()
        for k, v in per_play.items():
            counts[k] = max(counts.get(k, 0), int(v))
        if progress is not None:
            frac = (rows / expected) if expected else 0.0
            elapsed = time.time() - started
            rate = rows / elapsed if elapsed > 0 else 0.0
            progress({
                "path": str(path), "rows_read": rows, "file_frac": frac,
                "eta_s": ((expected - rows) / rate) if rate > 0 and expected else None,
            })

    per_play_values = list(counts.values()) or [0]
    return {
        "path": str(path),
        "rows": int(rows),
        "games": int(len(games)),
        "plays": int(len(plays)),
        "players": int(len(players)),
        "max_players_on_play": int(max(per_play_values)),
        "median_players_on_play": float(np.median(per_play_values)),
        "rows_per_play": float(rows / len(plays)) if plays else float("nan"),
        "file_bytes": int(path.stat().st_size),
    }


def _csv_data_rows(path: str | Path) -> int | None:
    """Cheap data-row estimate for a progress bar: bytes / mean line length.

    Never used for a verdict; only for an ETA. The exact count comes from the
    streaming pass.
    """
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(65536)
        lines = head.count(b"\n")
        if lines < 2:
            return None
        mean = len(head) / lines
        return int(size / mean)
    except OSError:  # pragma: no cover - defensive
        return None


def preflight_report(counts: dict, band_plays: int | None = None) -> dict:
    """Describe one tracking file. Nothing here stops, raises or narrows a sample.

    Pure function: same dict in, same report out. The exact row count is the one
    discriminator this project trusts for the export truncation, because a
    cohort-only file and a truncated export are indistinguishable on player
    counts and players-per-play. Everything else is printed as structure, with
    its denominator, and the run continues on whatever the file holds.
    """
    name = Path(counts.get("path", "tracking")).name
    rows = int(counts.get("rows", 0))
    games = int(counts.get("games", 0))
    plays = int(counts.get("plays", 0))
    players = int(counts.get("players", 0))
    median = float(counts.get("median_players_on_play", 0.0))
    widest = int(counts.get("max_players_on_play", 0))
    truncated = rows == TRUNCATION_ROWS

    findings = [
        f"{name}: {rows:,} rows · {games:,} games · {plays:,} plays · "
        f"{players:,} players · median {median:.1f} players/play "
        f"(widest {widest})",
    ]
    if truncated:
        findings.append(
            f"{name} has exactly {TRUNCATION_ROWS:,} data rows (2^20-1): this is "
            f"the known export truncation. The run continues on it; every number "
            f"below is conditional on this subset.")
    else:
        findings.append(
            f"{name}: row count is not the 1,048,575-row truncation "
            f"({rows:,} rows read).")

    coverage = None
    if band_plays:
        coverage = plays / int(band_plays) if band_plays else None
        findings.append(
            f"{name}: {plays:,} distinct plays in the file against "
            f"{int(band_plays):,} band plays ({coverage:.1%} as many). The file "
            f"spans all plays, not just the band, so this is not a coverage "
            f"measure; the measured coverage of the band is the §5 table.")

    findings.append(
        f"{name}: players and players-per-play are reported as structure, not "
        f"judged: a cohort-only file holds a few hundred players at one or two "
        f"rows per play by design, and a truncated export can look the same.")

    return {"ok": True, "stops": [], "findings": findings, "truncated": truncated,
            "checked": {
                "rows": rows, "games": games, "players": players,
                "median_players_on_play": median,
                "max_players_on_play": widest,
                "plays": plays, "band_plays": band_plays,
                "band_plays_present_share": coverage,
            }}


# ------------------------------------------------------------------ extraction
def _needed_key(needed_plays: pd.DataFrame) -> pd.DataFrame:
    need = needed_plays[["game_id", "play_id"]].drop_duplicates().copy()
    need["game_id"] = need["game_id"].astype("int64")
    need["play_id"] = need["play_id"].astype("int64")
    return need


def extract_plays(tracking_paths: Sequence[str | Path],
                  needed_plays: pd.DataFrame,
                  out_dir: str | Path,
                  chunk_rows: int = 2_000_000,
                  resume: bool = True,
                  expected_rows: dict[str, int] | None = None,
                  progress: Callable[[dict], None] | None = None) -> list[dict]:
    """Stream each tracking file, keep rows for the band's plays, write a shard.

    One Parquet shard per input file, plus a sidecar ``.meta.json`` holding the
    resume key (path, size, mtime, chunk size, rows read/kept, head hash). A
    matching sidecar with a present shard is reused, so a run that dies resumes
    at the next file.
    """
    out = Path(out_dir)
    shard_dir = out / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    need = _needed_key(needed_plays)
    expected_rows = expected_rows or {}
    results: list[dict] = []

    for path in tracking_paths:
        path = Path(path).resolve()
        stem = path.stem
        shard = shard_dir / f"{stem}.parquet"
        meta_path = shard_dir / f"{stem}.meta.json"
        size = path.stat().st_size
        mtime = path.stat().st_mtime_ns

        if resume and shard.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            if (meta.get("path") == str(path) and meta.get("size") == size
                    and meta.get("mtime_ns") == mtime
                    and meta.get("chunk_rows") == chunk_rows
                    and int(meta.get("rows_kept", 0)) >= 0):
                results.append({**meta, "shard": str(shard), "reused": True})
                if progress is not None:
                    progress({"path": str(path), "rows_read": meta.get("rows_read", 0),
                              "rows_kept": meta.get("rows_kept", 0), "file_frac": 1.0,
                              "eta_s": 0.0, "reused": True})
                continue

        rows_read = 0
        kept_parts: list[pd.DataFrame] = []
        kept = 0
        started = time.time()
        expected = expected_rows.get(str(path)) or _csv_data_rows(path)

        reader = pd.read_csv(
            path, usecols=list(TRACKING_COLUMNS), chunksize=chunk_rows,
            dtype={"game_id": "int64", "play_id": "int64", "nfl_id": "int64"},
            encoding="utf-8", na_values=["NA"], low_memory=False)

        for chunk in reader:
            rows_read += len(chunk)
            hit = chunk.merge(need, on=["game_id", "play_id"], how="inner")
            if len(hit):
                kept_parts.append(hit)
                kept += len(hit)
            if progress is not None:
                elapsed = time.time() - started
                rate = rows_read / elapsed if elapsed > 0 else 0.0
                progress({
                    "path": str(path), "rows_read": rows_read, "rows_kept": kept,
                    "file_frac": (rows_read / expected) if expected else 0.0,
                    "eta_s": ((expected - rows_read) / rate)
                             if rate > 0 and expected else None,
                    "reused": False,
                })

        frame = (pd.concat(kept_parts, ignore_index=True) if kept_parts
                 else pd.DataFrame(columns=list(TRACKING_COLUMNS)))
        frame = frame.sort_values(["game_id", "play_id", "time", "nfl_id"],
                                  kind="mergesort").reset_index(drop=True)
        frame.to_parquet(shard, index=False)

        meta = {
            "path": str(path), "size": size, "mtime_ns": mtime,
            "chunk_rows": chunk_rows, "rows_read": int(rows_read),
            "rows_kept": int(kept), "sha256_head": file_sha256(path, 4 * 1024 * 1024),
            "shard": str(shard), "reused": False,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        results.append(meta)

    return results


def _shard_list(shards: Sequence[dict]) -> str:
    paths = [f"'{_posix(s['shard'])}'" for s in shards]
    return "[" + ", ".join(paths) + "]"


def _register_shards(con: duckdb.DuckDBPyConnection, shards: Sequence[dict],
                     view: str = "gm_tracking") -> None:
    con.execute(f"""
        CREATE OR REPLACE VIEW {view} AS
        SELECT CAST(game_id AS BIGINT) AS game_id,
               CAST(play_id AS BIGINT) AS play_id,
               CAST(nfl_id AS BIGINT) AS nfl_id,
               CAST(time AS TIMESTAMP) AS time,
               CAST(x AS DOUBLE) AS x, CAST(y AS DOUBLE) AS y,
               event
        FROM read_parquet({_shard_list(shards)})""")


# ------------------------------------------------------------------ break frame
def recover_break_frames(con: duckdb.DuckDBPyConnection, turns: pd.DataFrame,
                         shards: Sequence[dict]) -> pd.DataFrame:
    """The DB's frame nearest ``(x_centre, y_centre)``, with the 0.25-yd gate."""
    _register_shards(con, shards)
    con.register("gm_turns", turns[list(TURN_KEY) + ["x_centre", "y_centre"]])
    con.execute("""
        CREATE OR REPLACE TABLE gm_bp AS
        WITH n AS (
          SELECT t.game_id, t.play_id, t.nfl_id, t.frame_start,
                 COUNT(k.nfl_id) AS db_rows
          FROM gm_turns t
          LEFT JOIN gm_tracking k
            ON k.game_id = t.game_id AND k.play_id = t.play_id AND k.nfl_id = t.nfl_id
          GROUP BY 1, 2, 3, 4
        ),
        d AS (
          SELECT t.game_id, t.play_id, t.nfl_id, t.frame_start,
                 k.time AS break_time,
                 sqrt(pow(k.x - t.x_centre, 2) + pow(k.y - t.y_centre, 2)) AS align_yd,
                 row_number() OVER (
                   PARTITION BY t.game_id, t.play_id, t.nfl_id, t.frame_start
                   ORDER BY sqrt(pow(k.x - t.x_centre, 2) + pow(k.y - t.y_centre, 2)),
                            k.time) AS rn
          FROM gm_turns t
          JOIN gm_tracking k
            ON k.game_id = t.game_id AND k.play_id = t.play_id AND k.nfl_id = t.nfl_id
        )
        SELECT d.game_id, d.play_id, d.nfl_id, d.frame_start, d.break_time,
               d.align_yd, n.db_rows
        FROM d JOIN n USING (game_id, play_id, nfl_id, frame_start)
        WHERE d.rn = 1""")
    bp = con.execute("SELECT * FROM gm_bp").df()
    bp = turns[list(TURN_KEY) + ["x_centre", "y_centre"]].merge(
        bp, on=list(TURN_KEY), how="left")
    bp["db_rows"] = bp["db_rows"].fillna(0).astype(int)
    bp["break_frame_ok"] = bp["align_yd"] <= ALIGN_GATE_YD
    return bp


def candidate_frame(con: duckdb.DuckDBPyConnection, breakpoints: pd.DataFrame,
                    shards: Sequence[dict]) -> pd.DataFrame:
    """Every tracked player within +/- 0.05 s of each turn's break time."""
    _register_shards(con, shards)
    bp = breakpoints.loc[breakpoints["break_frame_ok"],
                         list(TURN_KEY) + ["break_time", "x_centre", "y_centre"]]
    con.register("gm_bp_ok", bp)
    df = con.execute(f"""
        SELECT b.game_id, b.play_id, b.nfl_id, b.frame_start,
               k.nfl_id AS cand_nfl_id, k.x, k.y,
               sqrt(pow(k.x - b.x_centre, 2) + pow(k.y - b.y_centre, 2)) AS dist_yd
        FROM gm_bp_ok b
        JOIN gm_tracking k
          ON k.game_id = b.game_id AND k.play_id = b.play_id
         AND abs(epoch(k.time) - epoch(b.break_time)) <= {FRAME_TOL_S}
        WHERE k.nfl_id <> b.nfl_id""").df()
    return df.sort_values(list(TURN_KEY) + ["dist_yd", "cand_nfl_id"],
                          kind="mergesort").reset_index(drop=True)


def pass_forward_points(con: duckdb.DuckDBPyConnection,
                        shards: Sequence[dict]) -> pd.DataFrame:
    """The pass-forward timestamp per play, from the tracking ``event`` column."""
    _register_shards(con, shards)
    df = con.execute("""
        SELECT game_id, play_id, min(time) AS pass_forward_time
        FROM gm_tracking WHERE event = 'pass_forward'
        GROUP BY 1, 2""").df()
    return df


def players_at_pass_forward(con: duckdb.DuckDBPyConnection,
                            pass_forward: pd.DataFrame,
                            shards: Sequence[dict]) -> pd.DataFrame:
    """Every tracked player in the play's pass-forward frame (+/- 0.05 s)."""
    _register_shards(con, shards)
    con.register("gm_pf", pass_forward)
    return con.execute(f"""
        SELECT p.game_id, p.play_id, k.nfl_id, k.x, k.y
        FROM gm_pf p
        JOIN gm_tracking k
          ON k.game_id = p.game_id AND k.play_id = p.play_id
         AND abs(epoch(k.time) - epoch(p.pass_forward_time)) <= {FRAME_TOL_S}
        ORDER BY p.game_id, p.play_id, k.nfl_id""").df()


# ------------------------------------------------------------------ matching
def _player_play_frame(player_play: str | Path | pd.DataFrame) -> pd.DataFrame:
    if isinstance(player_play, pd.DataFrame):
        pp = player_play.copy()
    else:
        con = connect()
        con.execute(f"""
            CREATE TEMP TABLE raw_pp AS SELECT * FROM read_csv(
              '{_posix(player_play)}', header=true, all_varchar=true,
              nullstr=['NA',''])""")
        pp = con.execute("""
            SELECT CAST(game_id AS BIGINT) AS game_id,
                   CAST(play_id AS BIGINT) AS play_id,
                   CAST(nfl_id AS BIGINT) AS nfl_id,
                   team_abbr, lined_up_position, route_ran,
                   TRY_CAST(target AS BOOLEAN) AS target,
                   TRY_CAST(separation_at_pass_forward AS DOUBLE) AS separation_at_pass_forward,
                   TRY_CAST(cushion AS DOUBLE) AS cushion,
                   coverage_assignment, TRY_CAST(down AS INTEGER) AS down,
                   TRY_CAST(yards_to_go AS DOUBLE) AS yards_to_go,
                   team_coverage_man_zone, possession_team, pass_result
            FROM raw_pp""").df()
        con.close()
    return pp


def match_turns(turns: pd.DataFrame, frames: pd.DataFrame,
                breakpoints: pd.DataFrame,
                player_play: str | Path | pd.DataFrame) -> pd.DataFrame:
    """One row per band turn: the frozen match rule, at 2, 3 and 5 yd."""
    pp = _player_play_frame(player_play)
    keys = turns[list(TURN_KEY)].drop_duplicates()

    own = (pp[["game_id", "play_id", "nfl_id", "team_abbr"]]
           .drop_duplicates().rename(columns={"team_abbr": "own_team"}))
    cand_pp = (pp[["game_id", "play_id", "nfl_id", "team_abbr", "route_ran"]]
               .drop_duplicates()
               .rename(columns={"nfl_id": "cand_nfl_id",
                                "team_abbr": "cand_team"}))

    fr = frames.merge(cand_pp, on=["game_id", "play_id", "cand_nfl_id"],
                      how="left")
    fr = fr.merge(own, on=["game_id", "play_id", "nfl_id"], how="left")
    fr["is_opp"] = (fr["cand_team"].notna() & fr["own_team"].notna()
                    & (fr["cand_team"] != fr["own_team"]))
    fr["is_cand"] = fr["is_opp"] & fr["route_ran"].notna()

    out = keys.copy()
    out = out.merge(breakpoints[list(TURN_KEY) + ["align_yd", "break_frame_ok",
                                                  "db_rows"]],
                    on=list(TURN_KEY), how="left")
    out["db_rows"] = out["db_rows"].fillna(0).astype(int)
    out = out.merge(own, on=["game_id", "play_id", "nfl_id"], how="left")

    g_all = fr.groupby(list(TURN_KEY), sort=False)
    n_players = g_all.size()
    n_opp = fr[fr["is_opp"]].groupby(list(TURN_KEY), sort=False).size()
    cand = fr[fr["is_cand"]]
    n_cand = cand.groupby(list(TURN_KEY), sort=False).size()

    out = out.set_index(list(TURN_KEY))
    out["n_players_in_frame"] = n_players.reindex(out.index).fillna(0).astype(int)
    out["n_opposing_players"] = n_opp.reindex(out.index).fillna(0).astype(int)
    out["n_candidates_in_frame"] = n_cand.reindex(out.index).fillna(0).astype(int)

    for radius in RADII_YD:
        inside = cand[cand["dist_yd"] <= radius]
        cnt = inside.groupby(list(TURN_KEY), sort=False).size()
        col = f"n_in_radius_{int(radius)}" if float(radius).is_integer() else f"n_in_radius_{radius}"
        out[col] = cnt.reindex(out.index).fillna(0).astype(int)

    # Nearest and second-nearest candidate distances: the ambiguity is visible.
    ordered = cand.sort_values(list(TURN_KEY) + ["dist_yd", "cand_nfl_id"],
                               kind="mergesort")
    first = ordered.groupby(list(TURN_KEY), sort=False).first()
    second = ordered.groupby(list(TURN_KEY), sort=False).nth(1)
    out["min_distance_yd"] = first["dist_yd"].reindex(out.index)
    out["second_distance_yd"] = second["dist_yd"].reindex(out.index)
    out["nearest_gap_yd"] = out["second_distance_yd"] - out["min_distance_yd"]

    # The unique match: exactly one candidate inside 3 yd, no tie-breaking.
    r3 = int(PRIMARY_RADIUS_YD)
    inside3 = cand[cand["dist_yd"] <= PRIMARY_RADIUS_YD]
    n3 = out[f"n_in_radius_{r3}"]
    exactly_one = inside3.groupby(list(TURN_KEY), sort=False).filter(
        lambda d: len(d) == 1)
    one_id = exactly_one.set_index(list(TURN_KEY))["cand_nfl_id"]
    out["matched_receiver_nfl_id"] = one_id.reindex(out.index)

    ids = inside3.groupby(list(TURN_KEY), sort=False)["cand_nfl_id"].apply(
        lambda s: ";".join(str(int(v)) for v in sorted(s)))
    out["all_in_radius_ids"] = ids.reindex(out.index)

    # Any-opponent fallback at 3 yd (sensitivity only; never the primary).
    anyopp = fr[fr["is_opp"]]
    fb_inside = anyopp[anyopp["dist_yd"] <= PRIMARY_RADIUS_YD]
    out["fb_n_in_radius_3"] = (fb_inside.groupby(list(TURN_KEY), sort=False).size()
                               .reindex(out.index).fillna(0).astype(int))

    out = out.reset_index()

    def status(row: pd.Series) -> str:
        if int(row["db_rows"]) == 0:
            return "untracked"
        ok = row["break_frame_ok"]
        if pd.isna(ok) or not bool(ok):
            return "break_frame_unrecovered"
        if pd.isna(row["own_team"]):
            return "untagged"
        # ``n_players_in_frame`` excludes the turning DB by construction, so a
        # zero here means no opponent is in the frame: unobservable, not untracked.
        if int(row["n_opposing_players"]) == 0:
            return "unobservable"
        if int(row[f"n_in_radius_{r3}"]) == 1:
            return "unique"
        if int(row[f"n_in_radius_{r3}"]) >= 2:
            return "multiple"
        return "none"

    out["match_status"] = out.apply(status, axis=1)
    out["is_geometric_match"] = out["match_status"].eq("unique")
    out["provenance"] = PROVENANCE_INFERENCE
    out["rule"] = RULE_STRING
    out = out.sort_values(list(TURN_KEY), kind="mergesort").reset_index(drop=True)
    return out


# ------------------------------------------------------------------ tables
def coverage_table(match_df: pd.DataFrame, band_turns: int) -> pd.DataFrame:
    """Coverage of the band at each radius, plus the any-opponent fallback."""
    rows = []
    for radius in RADII_YD:
        col = f"n_in_radius_{int(radius)}" if float(radius).is_integer() else f"n_in_radius_{radius}"
        unique = int((match_df[col] == 1).sum())
        multiple = int((match_df[col] >= 2).sum())
        rows.append({
            "radius_yd": float(radius),
            "band_turns": int(band_turns),
            "unique": unique,
            "multiple": multiple,
            "none": int(((match_df[col] == 0)
                         & (match_df["n_opposing_players"] > 0)
                         & match_df["match_status"].ne("break_frame_unrecovered")).sum()),
            "unobservable": int(match_df["match_status"].eq("unobservable").sum()),
            "untracked": int(match_df["match_status"].eq("untracked").sum()),
            "break_frame_unrecovered": int(match_df["match_status"].eq("break_frame_unrecovered").sum()),
            "untagged": int(match_df["match_status"].eq("untagged").sum()),
            "attributed_fraction": round(unique / band_turns, 4) if band_turns else float("nan"),
            "fallback_any_opponent_unique_3yd": int((match_df["fb_n_in_radius_3"] == 1).sum()),
            "primary": bool(float(radius) == PRIMARY_RADIUS_YD),
        })
    return pd.DataFrame(rows)


def compare_elimination(match_df: pd.DataFrame,
                        elimination_df: pd.DataFrame) -> dict:
    """How often elimination names a different player from the geometric match."""
    elim = elimination_df[list(TURN_KEY) + ["receiver_nfl_id"]].drop_duplicates(
        subset=list(TURN_KEY))
    m = match_df[list(TURN_KEY) + ["match_status", "matched_receiver_nfl_id",
                                   "min_distance_yd"]]
    df = elim.merge(m, on=list(TURN_KEY), how="left")

    def label(row: pd.Series) -> str:
        st = row["match_status"]
        if st == "unique":
            if pd.isna(row["matched_receiver_nfl_id"]):
                return "unmeasurable"
            same = int(row["matched_receiver_nfl_id"]) == int(row["receiver_nfl_id"])
            return "agree" if same else "disagree"
        if st == "multiple":
            return "ambiguous"
        if st == "none":
            return "no_receiver_in_radius"
        return "unmeasurable"

    df["comparison"] = df.apply(label, axis=1)
    counts = df["comparison"].value_counts().to_dict()
    agree = int(counts.get("agree", 0))
    disagree = int(counts.get("disagree", 0))
    judgeable = agree + disagree
    rate = (disagree / judgeable) if judgeable else None
    # Always a number with its denominator. A small denominator is a wide
    # interval and a descriptive-only label downstream, never a stop.
    rate_label = "MISATTRIBUTION RATE" if judgeable else "NO JUDGEABLE TURNS"
    return {
        "rows": df,
        "agree": agree,
        "disagree": disagree,
        "ambiguous": int(counts.get("ambiguous", 0)),
        "none": int(counts.get("no_receiver_in_radius", 0)),
        "unmeasurable": int(counts.get("unmeasurable", 0)),
        "judgeable": judgeable,
        "judgeable_share": round(judgeable / len(df), 4) if len(df) else None,
        "elimination_turns": int(len(df)),
        "misattribution_rate": rate,
        "label": rate_label,
    }


def persistence_check(match_df: pd.DataFrame, pass_forward: pd.DataFrame,
                      players_at_pf: pd.DataFrame,
                      player_play: str | Path | pd.DataFrame) -> pd.DataFrame:
    """Rank of the matched DB among defenders at the pass-forward frame.

    Diagnostic, not a filter: the pre-registered estimator has no persistence
    step (spec §14.7). ``persist`` iff the DB is rank 1 by distance to the
    matched receiver at that frame.
    """
    pp = _player_play_frame(player_play)
    teams = pp[["game_id", "play_id", "nfl_id", "team_abbr"]].drop_duplicates()
    uniq = match_df[match_df["match_status"] == "unique"].copy()
    uniq = uniq.merge(pass_forward, on=["game_id", "play_id"], how="left")
    uniq = uniq.merge(teams.rename(columns={"nfl_id": "matched_receiver_nfl_id",
                                            "team_abbr": "recv_team"}),
                      on=["game_id", "play_id", "matched_receiver_nfl_id"],
                      how="left")
    uniq = uniq.merge(teams.rename(columns={"team_abbr": "db_team"}),
                      on=["game_id", "play_id", "nfl_id"], how="left")

    pf = players_at_pf.copy()
    pf = pf.merge(teams.rename(columns={"team_abbr": "pf_team"}),
                  on=["game_id", "play_id", "nfl_id"], how="left")

    statuses = []
    for _, row in uniq.iterrows():
        if pd.isna(row.get("pass_forward_time")):
            statuses.append("no_pass_forward_event")
            continue
        if pd.isna(row.get("recv_team")):
            statuses.append("receiver_untagged")
            continue
        sub = pf[(pf["game_id"] == row["game_id"]) & (pf["play_id"] == row["play_id"])]
        recv = sub[sub["nfl_id"] == row["matched_receiver_nfl_id"]]
        if recv.empty:
            statuses.append("receiver_not_tracked")
            continue
        rx, ry = float(recv.iloc[0]["x"]), float(recv.iloc[0]["y"])
        # Defenders are the DB's own team. Using the receiver's team here would
        # compare the DB against the offence and call every pair non-persistent.
        defenders = sub[sub["pf_team"] == row["db_team"]].copy()
        defenders["d"] = np.hypot(defenders["x"] - rx, defenders["y"] - ry)
        defenders = defenders.sort_values(["d", "nfl_id"], kind="mergesort")
        if defenders.empty or int(defenders.iloc[0]["nfl_id"]) != int(row["nfl_id"]):
            statuses.append("not_nearest")
        else:
            statuses.append("persist")
    uniq["persistence"] = statuses
    keep = list(TURN_KEY) + ["matched_receiver_nfl_id", "persistence"]
    return uniq[keep].reset_index(drop=True)


# ------------------------------------------------------------------ sample
def geometric_sample(match_df: pd.DataFrame, player_play: str | Path | pd.DataFrame,
                     turns: pd.DataFrame) -> pd.DataFrame:
    """The estimator's frame: unique matches whose receiver has a separation value.

    Column names are ``outcome_test.build_sample``'s, so the estimator is the
    same code path, not a re-implementation.
    """
    from . import outcome_test

    pp = _player_play_frame(player_play)
    keys = match_df.loc[match_df["match_status"] == "unique", list(TURN_KEY) +
                        ["matched_receiver_nfl_id"]].copy()
    if keys.empty:
        return pd.DataFrame()

    turn_cols = ["game_id", "play_id", "nfl_id", "frame_start", "position", "season",
                 "abs_turn_angle", "entry_speed", "apex_speed", "apex_retention",
                 "speed_retention", "plant", "turn_type"]
    base = turns[[c for c in turn_cols if c in turns.columns]].merge(
        keys, on=list(TURN_KEY), how="inner")

    recv = pp[["game_id", "play_id", "nfl_id", "lined_up_position", "route_ran",
               "separation_at_pass_forward", "target"]].rename(columns={
                   "nfl_id": "matched_receiver_nfl_id",
                   "lined_up_position": "receiver_position",
                   "route_ran": "receiver_route",
                   "separation_at_pass_forward": "separation_yd",
                   "target": "receiver_target"})
    db = pp[["game_id", "play_id", "nfl_id", "coverage_assignment", "cushion",
             "down", "yards_to_go", "team_coverage_man_zone", "pass_result"]]

    df = base.merge(recv, on=["game_id", "play_id", "matched_receiver_nfl_id"],
                    how="left")
    df = df.merge(db, on=["game_id", "play_id", "nfl_id"], how="left")
    df = df[df["separation_yd"].notna()].copy()
    df["attribution"] = PROVENANCE_INFERENCE
    return outcome_test.derive_columns(df)


def compare_with_d199(geom_fits: Sequence[Any],
                      elim_estimates_csv: str | Path) -> pd.DataFrame:
    """Elimination and geometric estimates side by side, row for row.

    Rows are aligned on the ``spec`` name where both sides carry one, else by
    position when the lengths agree. A mismatch is reported in the returned
    frame's ``note`` column instead of raising: the comparison is a diagnostic,
    never a reason to lose the run.
    """
    elim = pd.read_csv(elim_estimates_csv)
    note = ""
    pairs: list[tuple[Any, Any]] = []
    if "spec" in elim.columns:
        by_name = {str(r["spec"]): r for _, r in elim.iterrows()}
        for f in geom_fits:
            row = by_name.get(str(f.name))
            if row is not None:
                pairs.append((f, row))
        if not pairs:
            note = (f"no shared spec name between {len(geom_fits)} geometric "
                    f"fits and {len(elim)} D199 rows; nothing aligned")
    elif len(elim) == len(geom_fits):
        pairs = list(zip(geom_fits, (r for _, r in elim.iterrows())))
    else:
        note = (f"{len(elim)} D199 rows against {len(geom_fits)} geometric fits, "
                f"no spec column to align on; nothing compared")

    rows = []
    for f, e in pairs:
        rows.append({
            "spec": f.name,
            "elimination_beta": float(e["beta_yd_per_unit_retention"]),
            "elimination_lo": float(e["ci95_lo"]),
            "elimination_hi": float(e["ci95_hi"]),
            "elimination_n": int(e["n_turns"]),
            "geometric_beta": round(float(f.beta), 4),
            "geometric_lo": round(float(f.lo), 4),
            "geometric_hi": round(float(f.hi), 4),
            "geometric_n": int(f.n),
            "geometric_clusters": int(f.n_clusters),
            "change": round(float(f.beta) - float(e["beta_yd_per_unit_retention"]), 4),
            "note": note,
        })
    return pd.DataFrame(rows)


def empty_match(turns: pd.DataFrame) -> pd.DataFrame:
    """A match frame for the case where the tracking covers none of the band.

    Every turn is ``untracked`` and every count is zero. The coverage table, the
    misattribution counts and the verdict all work on it, so a tracking file
    that misses the band produces zeros and a label, never an exception.
    """
    out = turns[list(TURN_KEY)].drop_duplicates().reset_index(drop=True)
    out["match_status"] = "untracked"
    out["db_rows"] = 0
    out["n_players_in_frame"] = 0
    out["n_opposing_players"] = 0
    out["n_candidates_in_frame"] = 0
    for radius in RADII_YD:
        out[f"n_in_radius_{int(radius)}"] = 0
    out["min_distance_yd"] = np.nan
    out["second_distance_yd"] = np.nan
    out["nearest_gap_yd"] = np.nan
    out["matched_receiver_nfl_id"] = np.nan
    out["all_in_radius_ids"] = None
    out["fb_n_in_radius_3"] = 0
    return out


# ------------------------------------------------------------------ manifest
def _display_path(path: str | Path, root: Path) -> str:
    """Repo-relative when the file lives under the repo, absolute otherwise."""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return str(p)


def write_manifest(out_dir: str | Path, inputs: Sequence[str | Path],
                   outputs: Sequence[str | Path], config: dict,
                   extra: dict | None = None) -> dict:
    """sha256 for every input and output, plus git HEAD and a UTC timestamp."""
    out = Path(out_dir)
    root = Path(__file__).resolve().parents[2]
    manifest = {
        "written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "git_head": git_head(Path(__file__).resolve().parents[2]),
        "rule": RULE_STRING,
        "provenance": PROVENANCE_INFERENCE,
        "config": config,
        "inputs": {_display_path(p, root): {"sha256": file_sha256(p),
                                            "bytes": Path(p).stat().st_size}
                   for p in inputs if Path(p).exists()},
        "outputs": {_display_path(p, root): {"sha256": file_sha256(p),
                                             "bytes": Path(p).stat().st_size}
                    for p in outputs if Path(p).exists()},
    }
    if extra:
        manifest.update(extra)
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(path)
    return manifest


# ------------------------------------------------------------------ verdict text
def plain_english_verdict(summary: dict) -> list[str]:
    """The final cell's plain-English verdict, built only from measured fields.

    It never upgrades a null into a claim, and it never reports a threshold as a
    refusal: every rate is printed with its denominator, and a thin subset is
    labelled descriptive rather than withheld.
    """
    lines: list[str] = []
    miss = summary.get("misattribution", {})
    gate = summary.get("gate", {})
    est = summary.get("estimate", {})
    rate = miss.get("misattribution_rate")
    judgeable = int(miss.get("judgeable", 0) or 0)

    if rate is None:
        lines.append(
            f"Attribution: 0 of {miss.get('elimination_turns', 0):,} "
            f"elimination-attributed turns could be judged on both rules "
            f"(agree 0, disagree 0), so the rate is 0 of 0 and nothing is claimed.")
    else:
        lines.append(
            f"Attribution: of {judgeable:,} turns where both rules "
            f"could be evaluated, the geometric match named the same player as the "
            f"elimination rule on {miss.get('agree', 0):,} and a different player on "
            f"{miss.get('disagree', 0):,} — a misattribution rate of {rate:.1%} "
            f"({miss.get('disagree', 0):,} of {judgeable:,}"
            + (f"; {judgeable:,} judgeable is a small denominator, so this is "
               f"descriptive only" if judgeable < MIN_JUDGEABLE else "")
            + ").")

    if not gate.get("gate_met", False):
        lines.append(
            f"Gate: NOT MET — {gate.get('attributed_turns', 0):,} attributed turns "
            f"and {gate.get('attributed_fraction', 0):.1%} of the band "
            f"(needs {gate.get('min_turns', 500):,} and "
            f"{gate.get('min_share', 0.30):.0%}). Any coefficient is descriptive only.")
    else:
        lines.append(
            f"Gate: MET — {gate.get('attributed_turns', 0):,} attributed turns, "
            f"{gate.get('attributed_fraction', 0):.1%} of the band.")

    if not est:
        lines.append(
            "Estimate: not fitted — "
            + str(summary.get("estimate_status")
                  or "no attributed turn carries the outcome column")
            + ". The counts and intervals above are the result; no statement "
              "about the coefficient is made.")
        return lines

    b, lo, hi = est.get("beta"), est.get("lo"), est.get("hi")
    elim = est.get("elimination_beta")
    if b is None or not np.isfinite(b):
        lines.append(
            f"Estimate: not estimable — the {est.get('n', 0):,}-turn subset "
            f"produced no finite coefficient. That is a thin subset, not a "
            f"failure: the counts above stand and no coefficient is claimed.")
        return lines
    ci = (f"{lo:+.2f} to {hi:+.2f}" if np.isfinite(lo) and np.isfinite(hi)
          else "not finite — the subset is too thin for a usable interval")
    lines.append(
        f"Estimate: {b:+.2f} yd of separation per unit of apex retention "
        f"(95% CI {ci}), n={est.get('n', 0):,} turns in "
        f"{est.get('clusters', 0):,} player clusters, on "
        f"{est.get('attributed_fraction', gate.get('attributed_fraction', 0)):.1%} "
        f"of the band" +
        (f", against D199's elimination-matched {elim:+.2f} yd. "
         + ("The geometric estimate moved away from zero, consistent with the "
            "elimination null having been attenuated by misattribution."
            if gate.get("gate_met", False)
            and abs(b) > abs(elim) * 1.5 and b < 0 else
            ("The subset is descriptive only, so no attenuation claim is made."
             if not gate.get("gate_met", False) else
             "The geometric estimate did not move materially toward the stated "
             "direction."))
         if elim is not None and np.isfinite(elim) else "."))
    lines.append(f"Pre-stated criterion: {est.get('label', 'not evaluated')}. " +
                 ("The criterion is met; this is the finding the spec permits."
                  if est.get("label") == "FINDING" else
                  "The criterion is not met, so no claim is made beyond the number "
                  "and its interval."))
    return lines
