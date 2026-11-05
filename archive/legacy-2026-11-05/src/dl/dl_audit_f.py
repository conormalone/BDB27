#!/usr/bin/env python3
"""dl_audit_f — item f: DL alignment / ``blitzing`` semantics audit.

Writes ``<outputs>/audit/f_alignment.csv`` (long format: item, group, value,
verdict, evidence).  Read-only DuckDB over ``player_play`` + ``players``; no
tracking is read for the numbers, only headers are inspected for item 2.

Standalone::

    python src/dl/dl_audit_f.py --mode sample
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402

F_FIELDS = ("item", "group", "value", "verdict", "evidence")
DL_POSITIONS = ("DE", "DT", "NT")
DEPTH_TOKENS = ("depth", "off_ball", "yard_off", "stance", "align", "technique")
#: Contain "align"/"assign" but are not pre-snap depth for a rusher.
NON_DEPTH_EXACT = {"receiver_alignment", "coverage_assignment",
                   "coverage_assignment_at_snap"}
NULLSTR = "['NA']"
#: "~all" for the blitzing->get_off test: 0.911 observed vs 0.006 on FALSE rows.
GET_OFF_HIGH = 0.85


def _out_dir(cfg, mode) -> Path:
    sub = D.dl(cfg)["audit"].get("out_subdir") or "audit"
    return Path(D.resolve_paths(cfg, mode)["outputs"]) / str(sub)


def _reader(path: Path) -> str:
    return f"read_csv_auto('{Path(path).as_posix()}', nullstr={NULLSTR})"


def _csv_files(cfg) -> list[Path]:
    raw = Path(D.resolve_paths(cfg, "sample")["raw"])
    return sorted(p for p in raw.rglob("*.csv") if "external" not in p.parts)


def _depth_scan(cfg) -> tuple[str, str]:
    """Header scan for a pre-snap depth/alignment field."""
    hits, excluded, tracking_xy = [], [], False
    files = _csv_files(cfg)
    con = D.duckdb_connect(cfg)
    try:
        for f in files:
            cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {_reader(f)}").fetchall()]
            low = {c.lower(): c for c in cols}
            if {"x", "y"} <= set(low) and "tracking" in f.name:
                tracking_xy = True
            for col in cols:
                c = col.lower()
                if c in NON_DEPTH_EXACT:
                    excluded.append(f"{f.name}:{col}")
                    continue
                if any(tok in c for tok in DEPTH_TOKENS):
                    hits.append(f"{f.name}:{col}")
    finally:
        con.close()
    seen = f"scanned {len(files)} raw csv headers"
    if hits:
        return "AVAILABLE", f"{seen}; depth-like fields: {sorted(set(hits))}"
    verdict = "DERIVABLE_FROM_TRACKING" if tracking_xy else "ABSENT"
    return verdict, (f"{seen}; no depth/alignment field outside "
                     f"{sorted(set(excluded))}; tracking x/y present={tracking_xy} "
                     f"-> depth must come from snap-time x/y vs ball_snap")


def run(cfg, mode) -> dict:
    t0 = time.perf_counter()
    paths = D.resolve_paths(cfg, mode)
    out_dir = _out_dir(cfg, mode)
    out_dir.mkdir(parents=True, exist_ok=True)
    pp = _reader(D.source_paths(cfg, "player_play")[0])
    pl = _reader(D.source_paths(cfg, "players")[0])
    pos = ", ".join(f"'{p}'" for p in DL_POSITIONS)
    con = D.duckdb_connect(cfg)
    rows: list[dict] = []
    try:
        con.execute(f"""
            CREATE VIEW pp AS SELECT * FROM {pp};
            CREATE VIEW dl AS
              SELECT p.game_id, p.play_id, p.nfl_id, p.lined_up_position,
                     p.blitzing, p.player_get_off
              FROM pp p
              JOIN (SELECT nfl_id FROM {pl} WHERE nfl_position IN ({pos})) d
                USING (nfl_id);
            CREATE VIEW dpp AS
              SELECT DISTINCT game_id, play_id FROM pp
              WHERE dropback_duration IS NOT NULL;
            CREATE VIEW dld AS
              SELECT d.* FROM dl d JOIN dpp USING (game_id, play_id);
        """)
        total = con.execute("SELECT count(*) FROM dl").fetchone()[0]
        # 1. lined_up_position coverage on DL rows
        counts = con.execute(
            "SELECT COALESCE(lined_up_position, '<NULL>') g, count(*) n "
            "FROM dl GROUP BY 1 ORDER BY n DESC").fetchall()
        nulls = dict(counts).get("<NULL>", 0)
        for g, n in counts:
            rows.append({"item": "f1_lined_up_position", "group": g,
                         "value": f"{n}", "verdict": "OBSERVED",
                         "evidence": f"n={n:,}/{total:,} ({n / total:.1%}); "
                                     f"null_rate={nulls / total:.1%}"})
        rows.append({"item": "f1_lined_up_position", "group": "COVERAGE",
                     "value": f"null_rate={nulls / total:.3f}",
                     "verdict": "COVERED" if nulls / total < 0.01 else "PARTIAL",
                     "evidence": f"DL rows={total:,}; values={[g for g, _ in counts]}"})
        # 2. pre-snap depth availability
        verdict, ev = _depth_scan(cfg)
        rows.append({"item": "f2_pre_snap_depth", "group": "depth_field",
                     "value": "none_in_tabular_data" if verdict != "AVAILABLE" else "present",
                     "verdict": verdict, "evidence": ev})
        # 3. blitzing semantics on DL dropback rows
        ct = con.execute(
            "SELECT blitzing, (player_get_off IS NOT NULL) present, count(*) n "
            "FROM dld GROUP BY 1, 2").fetchall()
        n_dl_drop, n_dl_drop_plays = con.execute(
            "SELECT count(*), count(DISTINCT (game_id, play_id)) FROM dld").fetchone()
        cell = {(bool(b) if b is not None else None, bool(p)): int(n) for b, p, n in ct}
        t_p, t_a = cell.get((True, True), 0), cell.get((True, False), 0)
        f_p, f_a = cell.get((False, True), 0), cell.get((False, False), 0)
        t_share = t_p / (t_p + t_a) if (t_p + t_a) else float("nan")
        f_share = f_p / (f_p + f_a) if (f_p + f_a) else float("nan")
        for label, n in (("TRUE_get_off_present", t_p), ("TRUE_get_off_null", t_a),
                         ("FALSE_get_off_present", f_p), ("FALSE_get_off_null", f_a)):
            rows.append({"item": "f3_blitzing_semantics", "group": label,
                         "value": f"{n}", "verdict": "OBSERVED",
                         "evidence": f"DL dropback rows={n_dl_drop:,} "
                                     f"on {n_dl_drop_plays:,} plays"})
        if t_share >= GET_OFF_HIGH and f_share < 0.5:
            sem = "PER_PLAYER_RUSH_ASSIGNMENT"
        elif t_share >= GET_OFF_HIGH and f_share >= GET_OFF_HIGH:
            sem = "TEAM_LEVEL_FLAG"
        else:
            sem = "AMBIGUOUS"
        rows.append({"item": "f3_blitzing_semantics", "group": "VERDICT",
                     "value": f"TRUE_get_off={t_share:.3f}; FALSE_get_off={f_share:.3f}",
                     "verdict": sem,
                     "evidence": "TRUE rows ~all get_off-present and FALSE rows not "
                                 "=> blitzing on a DL row means this defender rushed; "
                                 f"not a team blitz flag (TRUE residual nulls={t_a:,})"})
        # 4. team blitz not derivable from the cohort table
        ids, pp_rows, pp_plays = con.execute(
            "SELECT count(DISTINCT nfl_id), count(*), count(DISTINCT (game_id, play_id)) "
            "FROM pp").fetchone()
        rpp = pp_rows / pp_plays
        rows.append({"item": "f4_team_blitz", "group": "cohort",
                     "value": f"distinct_nfl_id={ids}",
                     "verdict": "PENDING_FULL_RUN",
                     "evidence": f"player_play holds a {ids}-player prospect cohort "
                                 f"({pp_rows:,} rows / {pp_plays:,} plays, mean "
                                 f"{rpp:.2f} rows/play), not 11 defenders per snap; "
                                 f"rushers>4 needs full tracking"})
        # 5. DL rows per play distribution
        per = con.execute(
            "WITH per AS (SELECT game_id, play_id, count(*) n FROM dl GROUP BY 1, 2) "
            "SELECT n, count(*) plays, sum(n) rws FROM per GROUP BY 1 ORDER BY 1").fetchall()
        for n, plays, rws in per:
            rows.append({"item": "f5_dl_rows_per_play", "group": f"{n}_DL_rows",
                         "value": f"{plays}", "verdict": "OBSERVED",
                         "evidence": f"{plays:,} plays ({rws:,} rows); max={per[-1][0]}"})
    finally:
        con.close()
    out = D.write_csv(out_dir / "f_alignment.csv", rows, F_FIELDS, mode=mode, cfg=cfg)
    return {
        "file": str(out),
        "runtime_s": round(time.perf_counter() - t0, 2),
        "crosstab": {"TRUE_present": t_p, "TRUE_null": t_a,
                     "FALSE_present": f_p, "FALSE_null": f_a},
        "distinct_nfl_id": int(ids),
        "rows_per_play_mean": round(rpp, 3),
        "verdicts": {"f2": verdict, "f3": sem},
        "n_rows_written": len(rows),
    }


if __name__ == "__main__":
    args = D.parse_args()
    cfg = D.load_config(args.config)
    print(run(cfg, args.mode))
