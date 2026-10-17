#!/usr/bin/env python3
"""security_features.py — shared board + security-exposure logic (BDB27 security task).

This module is the single home for reading the **EDP draft-board** external file and turning it
into the pre-Combine-only **security exposures** used by the Phase-4 security stage
(`src/06_security.py`) and the Phase-1 board audit extension (`src/security_audit.py`).

Rules enforced here (binding spec `TASK.md`, Phase 4):
  * **join on `nfl_id` only** — never fuzzy-match names;
  * **exposures are pre-Combine only** — the 2-weeks-before and 1-week-before snapshots; the
    primary exposure is ``log(EDP)`` at the **1-week-before** snapshot. ``Combine Week`` and the
    post-Combine snapshots are **never** exposures (they may reflect Combine results and are used
    only as **outcomes**, e.g. the Analysis-A pre→post EDP change);
  * a pre-Combine snapshot counts only when its ``snapshot_date`` is **strictly before the player's
    own Combine testing day** (derived from ``combine_tracking.time``); where the testing day is
    unknown there is no Combine record anyway;
  * **drill participation** (which speed drills have tracking) is computed separately and kept
    **apart** from security (skipping is not security by itself).

Paths, thresholds and shapes come from ``config.yaml`` (``external.board`` / ``security``); no magic
numbers. The module never loads the full game CSVs; the board is small and read via DuckDB.
"""
from __future__ import annotations

import hashlib
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common


# --------------------------------------------------------------------------- #
# board file: path, integrity, permission
# --------------------------------------------------------------------------- #
def board_path(cfg: dict) -> Path:
    """Absolute path to the EDP board CSV (under ``data/raw/``)."""
    return common.resolve(cfg, "raw", cfg["external"]["board"]["file"])


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streaming sha256 of a file (no full read into memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def board_read_sql(path: Path, cfg: dict) -> str:
    """DuckDB ``read_csv`` call string with explicit column selection + DD/MM/YYYY dates."""
    b = cfg["external"]["board"]
    cols = ", ".join(f'"{c}"' for c in b["columns"])
    return (f"read_csv('{path}', header=true, columns={{{_columns_ddl(b['columns'])}}}, "
            f"dateformat='{b['date_format']}')")


def _columns_ddl(columns: list[str]) -> str:
    """DuckDB named-column DDL for the board (types pinned so dates parse DD/MM/YYYY)."""
    types = {
        "draft_year": "BIGINT", "name": "VARCHAR", "position": "VARCHAR", "school": "VARCHAR",
        "type": "VARCHAR", "nfl_id": "BIGINT", "n_total": "BIGINT", "n": "BIGINT",
        "edp": "DOUBLE", "edp_rank": "BIGINT", "pos_rank": "VARCHAR", "snapshot_date": "DATE",
        "snapshot_note": "VARCHAR",
    }
    return ", ".join(f"'{c}': '{types[c]}'" for c in columns)


def verify_board(cfg: dict, path: Path, log=None) -> dict:
    """Check the board exists / is permitted / matches the pinned sha256 + row count.

    Returns a dict ``{ok, permitted, exists, sha256_ok, n_rows, reasons}``. Never raises; callers
    decide whether a failure is a hard stop (full run) or a fixture fallback (sample).
    """
    b = cfg["external"]["board"]
    rep: dict[str, Any] = {"path": str(path), "permitted": bool(b.get("permitted")),
                           "exists": path.exists(), "sha256_ok": None, "n_rows": None, "reasons": []}
    if not b.get("permitted"):
        rep["reasons"].append("external board data is not marked permitted in config "
                              "(external.board.permitted=false) -> see notes/blockers.md")
    if not path.exists():
        rep["reasons"].append(f"board file missing at {path}")
        rep["ok"] = False
        return rep
    got = sha256_file(path)
    rep["sha256"] = got
    rep["sha256_ok"] = (got == str(b["sha256"]))
    if not rep["sha256_ok"]:
        rep["reasons"].append(f"board sha256 mismatch: got {got[:12]}… expected {str(b['sha256'])[:12]}…")
    con = common.connect(cfg["runtime"]["duckdb_threads"])
    try:
        rep["n_rows"] = int(con.execute(f"SELECT count(*) FROM {board_read_sql(path, cfg)}").fetchone()[0])
    finally:
        con.close()
    exp = b.get("n_rows_expected")
    rows_ok = (exp is None) or (rep["n_rows"] == int(exp))
    if not rows_ok:
        rep["reasons"].append(f"board row count {rep['n_rows']} != expected {exp}")
    rep["ok"] = bool(rep["permitted"] and rep["sha256_ok"] and rows_ok)
    if log:
        log.info("board verify: %s", {k: rep[k] for k in ("ok", "permitted", "sha256_ok", "n_rows")})
    return rep


def record_blocker(cfg: dict, msg: str, log=None) -> None:
    """Append a blocker to notes/blockers.md (house convention)."""
    path = common.resolve(cfg, "notes") / "blockers.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- **BDB27 security BLOCKER** ({stamp}): {msg}\n")
    if log:
        log.error("BLOCKER: %s", msg)


# --------------------------------------------------------------------------- #
# board ingest
# --------------------------------------------------------------------------- #
def load_board_rows(con: duckdb.DuckDBPyConnection, cfg: dict, path: Path) -> list[dict]:
    """Read the whole board (small; explicit columns) into typed row dicts."""
    b = cfg["external"]["board"]
    cols = b["columns"]
    sql = f"SELECT {', '.join(cols)} FROM {board_read_sql(path, cfg)} ORDER BY draft_year, name, snapshot_date"
    rows = con.execute(sql).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(zip(cols, r))
        if d.get("snapshot_date") is not None and not isinstance(d["snapshot_date"], date):
            d["snapshot_date"] = datetime.strptime(str(d["snapshot_date"])[:10], "%Y-%m-%d").date()
        out.append(d)
    return out


def position_group(cfg: dict, pos: Any) -> str:
    """Map a board position string to the competition position group (config-driven)."""
    return str(cfg["external"]["board"]["position_map"].get(str(pos), "OTHER"))


def board_players(board_rows: list[dict]) -> list[dict]:
    """One record per (nfl_id) with its modal metadata; drops rows with a blank ``nfl_id``.

    Returns list of ``{nfl_id, draft_year, name, position, school, n_rows}``.
    """
    by_id: dict[int, dict] = {}
    for r in board_rows:
        nid = r.get("nfl_id")
        if nid is None:
            continue
        nid = int(nid)
        rec = by_id.setdefault(nid, {"nfl_id": nid, "draft_year": int(r["draft_year"]),
                                     "name": str(r["name"]), "position": str(r["position"]),
                                     "school": str(r["school"]), "n_rows": 0})
        rec["n_rows"] += 1
    return sorted(by_id.values(), key=lambda x: x["nfl_id"])


def board_blank_id_count(board_rows: list[dict]) -> int:
    """Number of board rows with a blank ``nfl_id`` (cannot be joined; counted, never invented)."""
    return int(sum(1 for r in board_rows if r.get("nfl_id") is None))


# --------------------------------------------------------------------------- #
# Combine testing day (per player) — from complete combine_tracking
# --------------------------------------------------------------------------- #
def combine_testing_days(con: duckdb.DuckDBPyConnection, ct_parquet: Path) -> dict[int, date]:
    """Per-player Combine testing day = earliest ``time`` date in ``combine_tracking`` (PLAYER)."""
    rows = con.execute(f"""
        SELECT nfl_id, CAST(min(time) AS DATE) d
        FROM {common.pq(ct_parquet)} WHERE entity_type='PLAYER' GROUP BY 1
    """).fetchall()
    return {int(n): d for n, d in rows}


def combine_testing_window(con: duckdb.DuckDBPyConnection, ct_parquet: Path) -> dict[int, tuple[date, date]]:
    """Per-class Combine testing window (min/max testing day) from combine_tracking."""
    rows = con.execute(f"""
        SELECT draft_year, CAST(min(time) AS DATE), CAST(max(time) AS DATE)
        FROM {common.pq(ct_parquet)} WHERE entity_type='PLAYER' GROUP BY 1 ORDER BY 1
    """).fetchall()
    return {int(y): (a, b) for y, a, b in rows}


def combine_drills_by_player(con: duckdb.DuckDBPyConnection, ct_parquet: Path,
                             cfg: dict) -> dict[int, set[str]]:
    """Per-player set of speed-relevant ``drill_type``s present in combine tracking."""
    fam = set(cfg["audit"]["straight_line_drills"]) | set(cfg["audit"]["cutting_drills"])
    dlist = ", ".join(f"'{d}'" for d in sorted(fam))
    rows = con.execute(f"""
        SELECT DISTINCT nfl_id, drill_type FROM {common.pq(ct_parquet)}
        WHERE entity_type='PLAYER' AND drill_type IN ({dlist})
    """).fetchall()
    out: dict[int, set[str]] = {}
    for nid, dt in rows:
        out.setdefault(int(nid), set()).add(str(dt))
    return out


def participation_pattern(cfg: dict, drills: set[str]) -> tuple[str, int]:
    """Classify a player's speed-drill participation; **separate** from security.

    Categories: ``dash_and_cut`` (both families), ``dash_only``, ``cut_only``,
    ``no_speed_drills`` (has Combine tracking but no speed drill), ``no_combine_tracking``.
    Returns ``(pattern, n_speed_drills)`` where n_speed_drills counts distinct speed drills (0-3).
    """
    if not drills:
        return "no_combine_tracking", 0
    straight = set(cfg["audit"]["straight_line_drills"])
    cutting = set(cfg["audit"]["cutting_drills"])
    has_dash = bool(drills & straight)
    has_cut = bool(drills & cutting)
    n_speed = len(drills & (straight | cutting))
    if has_dash and has_cut:
        return "dash_and_cut", n_speed
    if has_dash:
        return "dash_only", n_speed
    if has_cut:
        return "cut_only", n_speed
    return "no_speed_drills", n_speed


# --------------------------------------------------------------------------- #
# snapshot -> EDP (strictly pre-Combine where required)
# --------------------------------------------------------------------------- #
def snapshot_edp(board_rows: list[dict], note: str, cfg: dict,
                 testing_days: dict[int, date] | None = None, strict_pre: bool = False) -> dict[int, float]:
    """Mean ``edp`` per ``nfl_id`` over rows with ``snapshot_note == note``.

    When ``strict_pre`` is set, a row counts only if its ``snapshot_date`` is strictly before the
    player's own testing day (``testing_days``); players without a known testing day are **not**
    silently accepted as pre-Combine (they have no Combine record).
    """
    acc: dict[int, list[float]] = {}
    for r in board_rows:
        if r.get("nfl_id") is None or str(r.get("snapshot_note")) != note:
            continue
        nid = int(r["nfl_id"])
        if strict_pre:
            td = (testing_days or {}).get(nid)
            if td is None or not (r["snapshot_date"] < td):
                continue
        acc.setdefault(nid, []).append(float(r["edp"]))
    return {nid: float(sum(v) / len(v)) for nid, v in acc.items()}


def pre_post_edp(board_rows: list[dict], cfg: dict, testing_days: dict[int, date]) -> dict[int, dict]:
    """Per-player ``{edp_pre, edp_post, edp_trend, has_primary, has_trend, has_post}``.

    ``edp_pre`` = 1-week-pre (strictly before testing); ``edp_post`` = 1-week-post (an **outcome**);
    ``edp_trend`` = 1-week-pre − 2-weeks-pre (both strictly pre-Combine). Pre-Combine is defined as
    *strictly before the player's own testing day*.
    """
    b = cfg["external"]["board"]
    pre_primary = snapshot_edp(board_rows, b["snapshot_primary"], cfg, testing_days, strict_pre=True)
    pre_base = snapshot_edp(board_rows, b["snapshot_trend_base"], cfg, testing_days, strict_pre=True)
    post = snapshot_edp(board_rows, b["snapshot_post"][0], cfg)  # post = outcome; no strict filter
    out: dict[int, dict] = {}
    for nid in set(pre_primary) | set(pre_base) | set(post):
        p, q, s = pre_primary.get(nid), pre_base.get(nid), post.get(nid)
        out[nid] = {"edp_pre": p, "edp_post": s, "edp_trend": (p - q) if (p is not None and q is not None) else None,
                    "has_primary": p is not None, "has_trend": (p is not None and q is not None),
                    "has_post": s is not None}
    return out


# --------------------------------------------------------------------------- #
# exposure table
# --------------------------------------------------------------------------- #
def assert_exposure_schema(cols: list[str], cfg: dict) -> None:
    """Fail loudly when the security exposure table does not match ``security.schema`` exactly."""
    expected = list(cfg["security"]["schema"])
    if list(cols) != expected:
        raise AssertionError(f"security exposure schema mismatch: got {list(cols)} expected {expected}")


def build_exposures(board_rows: list[dict], testing_days: dict[int, date],
                    drills_by_player: dict[int, set[str]], cfg: dict) -> list[dict]:
    """Build one security-exposure row per board player (``nfl_id`` present).

    Exposures (all pre-Combine): ``log(EDP)`` at the 1-week-pre snapshot (primary), the EDP trend
    (1wk-pre − 2wk-pre), and the consensus share ``n / n_total`` at the primary snapshot. Drill
    participation is attached **separately** (``participation_pattern``/``n_drills``).
    """
    b = cfg["external"]["board"]
    sec = cfg["security"]
    players = board_players(board_rows)
    pre_post = pre_post_edp(board_rows, cfg, testing_days)
    # consensus share at the primary snapshot (mean of n/n_total over its rows)
    con_acc: dict[int, list[float]] = {}
    nsnap: dict[int, int] = {}
    for r in board_rows:
        if r.get("nfl_id") is None:
            continue
        nid = int(r["nfl_id"])
        if str(r.get("snapshot_note")) in b["snapshot_pre"]:
            nsnap[nid] = nsnap.get(nid, 0) + 1
        if str(r.get("snapshot_note")) == b["snapshot_primary"] and r.get("n_total"):
            con_acc.setdefault(nid, []).append(float(r["n"]) / float(r["n_total"]))

    rows: list[dict] = []
    for p in players:
        nid = p["nfl_id"]
        pp = pre_post.get(nid, {})
        edp_pre = pp.get("edp_pre")
        share = (sum(con_acc[nid]) / len(con_acc[nid])) if con_acc.get(nid) else None
        pattern, n_drills = participation_pattern(cfg, drills_by_player.get(nid, set()))
        rows.append({
            "nfl_id": nid,
            "draft_year": p["draft_year"],
            "position": p["position"],
            "position_group": position_group(cfg, p["position"]),
            "edp_pre": edp_pre,
            "log_edp_pre": (float(math.log(edp_pre)) if edp_pre else None),
            "edp_trend": pp.get("edp_trend"),
            "consensus_share": share,
            "n_snapshots": nsnap.get(nid, 0),
            "has_primary": bool(pp.get("has_primary")),
            "has_trend": bool(pp.get("has_trend")),
            "participation_pattern": pattern,
            "n_drills": n_drills,
        })
    rows.sort(key=lambda r: r["nfl_id"])
    assert_exposure_schema(list(rows[0].keys()) if rows else list(sec["schema"]), cfg)
    return rows


def write_exposure_parquet(path: Path, rows: list[dict], cfg: dict) -> None:
    """Write the exposure table with the EXACT config schema + dtypes, asserted by read-back."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema_cols = list(cfg["security"]["schema"])
    types = {
        "nfl_id": pa.int64(), "draft_year": pa.int32(), "position": pa.string(),
        "position_group": pa.string(), "edp_pre": pa.float64(), "log_edp_pre": pa.float64(),
        "edp_trend": pa.float64(), "consensus_share": pa.float64(), "n_snapshots": pa.int32(),
        "has_primary": pa.bool_(), "has_trend": pa.bool_(), "participation_pattern": pa.string(),
        "n_drills": pa.int32(),
    }
    schema = pa.schema([(c, types[c]) for c in schema_cols])
    tbl = pa.Table.from_pylist([{c: r.get(c) for c in schema_cols} for r in rows], schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tbl, path, compression="zstd")
    assert_exposure_schema(list(pq.read_schema(path).names), cfg)
