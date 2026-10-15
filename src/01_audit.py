#!/usr/bin/env python3
"""01_audit.py — Phase 1 Audit (the go/no-go gate) for the BDB27 ceiling-binding task.

Implements evidence for audit items (a)-(i) from `TASK.md`:

  (a) WR/TE Combine drills present; straight-line vs cut/route classification.
  (b) Attempts per player per drill; Combine class years covered.
  (c) Matched Combine->NFL players per position; route snaps per matched player. [game side]
  (d) Route type/depth availability; trajectory-derived depth/break; 22-player tracking; ball/TTT.
  (e) Which plays are included; confirm NON-targeted routes are covered.
  (f) Reliability of the provided Combine `dis`/`a` vs x/y-derived; rate/units/frames.
  (g) Test-retest ICC of candidate Combine features (thin repeats -> explicit UNESTIMABLE). [Combine]
  (h) Exceedance rate: in-game speed/accel beating the Combine ceiling. [game side]
  (i) Combine peak speed vs in-game top speed on deep routes. [game side]

Game-side items (c/d/e/h/i) need the full game-tracking files, which are absent on this Pi; they
are SCRIPTED here and emit `PENDING FULL RUN` placeholders in sample mode. A schema-compliant
synthetic fixture with a planted binding ceiling (src/synthetic_fixture.py) exercises those paths
(fixture outputs stamped `SYNTHETIC FIXTURE (not a finding)` and never interpreted).

Discipline (TASK.md): one code path for --mode sample|full + --force; config-driven thresholds;
Parquet + DuckDB (never load full CSVs into pandas); downcast; wall/RAM logged to run_log.csv;
every CSV has `provenance` as its FIRST column; sample outputs stamped `UNVALIDATED SAMPLE OUTPUT`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import combine_features as cf
import game_features as gf
import gate

PENDING = "PENDING FULL RUN"
PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"

REQUIRED_GAME_FIELDS = [
    "route_ran",
    "pass_length",
    "target",
    "separation_at_pass_forward",
    "coverage_assignment",
    "coverage_assignment_at_snap",
    "team_coverage_man_zone",
    "team_coverage_type",
]
GAME_TRACKING_COLS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis", "o", "dir", "event"]
COMBINE_TRACKING_COLS = [
    "draft_year", "event_id", "nfl_id", "entity_type", "time", "drill_type", "drill_name",
    "attempt", "x", "y", "s", "a", "dis", "dir",
]


# --------------------------------------------------------------------------- #
# ctx / plumbing
# --------------------------------------------------------------------------- #
def _pq(path: Path | Sequence[Path]) -> str:
    """DuckDB ``read_parquet`` call string for a single Parquet path or a list of them.

    Full mode exposes a *union* of the per-season `game_tracking` parquets, so callers may pass a
    sequence (see `game_features.pq_list`)."""
    if isinstance(path, (list, tuple)):
        return gf.pq_list(path)
    return common.pq(path)


def _parquet_full(con, cfg: dict, name: str, force: bool, log) -> list[Path]:
    """Materialise every raw file listed under ``cfg['files'][name]`` and return the Parquet paths.

    Mirrors the helper used by `00_thresholds.py` / `03_game_demand.py` so full-mode multi-file
    inputs share one code path; a missing raw file is a blocker."""
    parq = common.resolve(cfg, "parquet")
    out = []
    for fn in cfg["files"][name]:
        src = common.resolve(cfg, "raw", fn)
        if not src.exists():
            raise FileNotFoundError(f"full mode requires {src} (absent on this Pi) -> notes/blockers.md")
        dst = parq / (Path(fn).stem + ".parquet")
        out.append(common.to_parquet(con, src, dst, force=force, logger=log))
    return out


def emit(ctx: dict, name: str, rows: Iterable[dict], subdir: str | None = None) -> Path:
    """Write a CSV with `provenance` as the FIRST column (ordered union of all fields)."""
    rows = [dict(r) for r in rows] or [{}]
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    fields = ["provenance"] + [f for f in fields if f != "provenance"]

    def _clean(v):
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            return ""
        return v

    norm = []
    for r in rows:
        d = {"provenance": ctx["prov"]}
        for k, v in r.items():
            if k == "provenance":
                continue
            d[k] = _clean(v)
        norm.append(d)
    sub = subdir if subdir is not None else ctx.get("subdir")
    out = (ctx["audit_dir"] / sub) if sub else ctx["audit_dir"]
    path = out / name
    common.write_csv(path, norm, fieldnames=fields)
    try:
        shown = path.relative_to(common.REPO_ROOT)
    except ValueError:
        shown = path
    ctx["logger"].info("wrote %s (%d rows)", shown, len(norm))
    return path


def _scalar(con: duckdb.DuckDBPyConnection, sql: str):
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


# --------------------------------------------------------------------------- #
# schema assertions (fail loudly)
# --------------------------------------------------------------------------- #
def assert_schemas(ctx: dict) -> None:
    con, cfg = ctx["con"], ctx["cfg"]
    log = ctx["logger"]
    audit = cfg["audit"]

    def describe(p: Path) -> dict[str, str]:
        return {r[0]: r[1] for r in con.execute(f"describe select * from {_pq(p)}").fetchall()}

    # --- combine_tracking ---
    ct = describe(ctx["tables"]["combine_tracking"])
    assert list(ct.keys()) == COMBINE_TRACKING_COLS, f"combine_tracking columns changed: {list(ct.keys())}"
    for c in ("x", "y", "s", "a", "dis", "dir"):
        assert ct[c] in ("DOUBLE", "FLOAT"), f"combine_tracking.{c} not float: {ct[c]}"
    assert ct["nfl_id"] == "BIGINT", f"combine_tracking.nfl_id not BIGINT: {ct['nfl_id']}"

    # --- combine_results ---
    cr = describe(ctx["tables"]["combine_results"])
    for c in ("nfl_id", "combine_position", "forty", "ten_yd_split"):
        assert c in cr, f"combine_results missing {c}"

    # --- player_play required fields ---
    pp = describe(ctx["tables"]["player_play"])
    missing = [c for c in REQUIRED_GAME_FIELDS if c not in pp]
    assert not missing, f"player_play missing required fields: {missing}"

    # --- game tracking schema ---
    gt = describe(ctx["tables"]["game_tracking"])
    missing_gt = [c for c in GAME_TRACKING_COLS if c not in gt]
    assert not missing_gt, f"game tracking missing columns: {missing_gt}"

    # --- units / sampling rate / frames (combine) ---
    CT = _pq(ctx["tables"]["combine_tracking"])
    med_dt = _scalar(con, f"""
        WITH f AS (SELECT event_id, epoch_ms(time) t FROM {CT} WHERE entity_type='PLAYER'),
             d AS (SELECT (t - lag(t) OVER (PARTITION BY event_id ORDER BY t))/1000.0 dt FROM f)
        SELECT median(dt) FROM d WHERE dt IS NOT NULL""")
    assert med_dt is not None and abs(med_dt - 1.0 / audit["sampling_rate_hz"]) < 1e-6, \
        f"combine sampling rate {med_dt}s != 1/{audit['sampling_rate_hz']}Hz"
    xr = con.execute(f"SELECT min(x),max(x),min(y),max(y) FROM {CT}").fetchone()
    assert xr[0] >= 0 and xr[1] <= 110 and xr[3] <= 70, f"combine x/y out of local-frame bounds: {xr}"

    # --- no residual literal 'NA' in numeric columns (A11) ---
    na_ct = _scalar(con, f"SELECT count(*) FROM {CT} WHERE CAST(s AS VARCHAR)='NA'")
    assert na_ct == 0, "residual literal 'NA' in combine_tracking.s"

    # --- A1: combine_tracking complete vs combine_results ---
    n1 = _scalar(con, f"SELECT count(DISTINCT nfl_id) FROM {CT}")
    n2 = _scalar(con, f"SELECT count(DISTINCT nfl_id) FROM {_pq(ctx['tables']['combine_results'])}")
    assert n1 == n2, f"A1 violated: combine_tracking nfl_ids {n1} != combine_results {n2}"

    log.info("schema assertions PASS (combine cols, PP fields, game cols, 10Hz, frames, no-NA, A1)")


# --------------------------------------------------------------------------- #
# (a) drills present for WR/TE + family classification
# --------------------------------------------------------------------------- #
def item_a(ctx: dict) -> list[dict]:
    con, cfg = ctx["con"], ctx["cfg"]
    audit = cfg["audit"]
    CT, CR = _pq(ctx["tables"]["combine_tracking"]), _pq(ctx["tables"]["combine_results"])
    pos = ",".join(f"'{p}'" for p in audit["positions"])
    straight = set(audit["straight_line_drills"])
    cutting = set(audit["cutting_drills"])
    prefix = audit["skill_drill_prefix"]

    def family(dt: str) -> str:
        if dt in straight:
            return "straight_line"
        if dt in cutting:
            return "cutting"
        if dt.startswith(prefix):
            return "skill_block"
        return "other"

    base = f"""
        WITH wt AS (SELECT nfl_id, combine_position FROM {CR} WHERE combine_position IN ({pos}))
        SELECT wt.combine_position, ct.drill_type,
               count(DISTINCT ct.nfl_id) n_players, count(DISTINCT ct.event_id) n_attempts
        FROM {CT} ct JOIN wt USING(nfl_id)
        GROUP BY 1,2 ORDER BY 1,2"""
    out = []
    for position, drill, np_, na_ in _rows(con, base):
        out.append(dict(item="a", combine_position=position, drill_type=drill,
                        family=family(drill), n_players=np_, n_attempts=na_))
    emit(ctx, "a_drills_wr_te.csv", out)

    names = f"""
        WITH wt AS (SELECT nfl_id, combine_position FROM {CR} WHERE combine_position IN ({pos}))
        SELECT wt.combine_position, ct.drill_type, ct.drill_name,
               count(DISTINCT ct.nfl_id) n_players, count(DISTINCT ct.event_id) n_attempts
        FROM {CT} ct JOIN wt USING(nfl_id)
        GROUP BY 1,2,3 ORDER BY 1,2,5 DESC"""
    emit(ctx, "a_drill_names.csv",
         [dict(item="a", combine_position=p, drill_type=d, drill_name=dn, n_players=np_, n_attempts=na_)
          for p, d, dn, np_, na_ in _rows(con, names)])
    return out


# --------------------------------------------------------------------------- #
# (b) attempts per player per drill; class years
# --------------------------------------------------------------------------- #
def item_b(ctx: dict) -> list[dict]:
    con, cfg = ctx["con"], ctx["cfg"]
    audit = cfg["audit"]
    CT, CR = _pq(ctx["tables"]["combine_tracking"]), _pq(ctx["tables"]["combine_results"])
    pos = ",".join(f"'{p}'" for p in audit["positions"])

    per = f"""
        WITH wt AS (SELECT nfl_id, combine_position FROM {CR} WHERE combine_position IN ({pos})),
             a AS (SELECT ct.nfl_id, wt.combine_position pos, ct.drill_type,
                          count(DISTINCT ct.event_id) n
                   FROM {CT} ct JOIN wt USING(nfl_id) GROUP BY 1,2,3)
        SELECT pos, drill_type, count(*) n_players,
               sum(CASE WHEN n=1 THEN 1 ELSE 0 END) n_with_1,
               sum(CASE WHEN n>=2 THEN 1 ELSE 0 END) n_with_ge2,
               min(n), median(n), avg(n), max(n), sum(n) total_attempts
        FROM a GROUP BY 1,2 ORDER BY 1,2"""
    out = []
    for pos_, drill, npl, n1, n2, mn, med, av, mx, tot in _rows(con, per):
        out.append(dict(item="b", combine_position=pos_, drill_type=drill, n_players=npl,
                        n_with_1_attempt=n1, n_with_ge2_attempts=n2, min_attempts=mn,
                        median_attempts=med, mean_attempts=round(float(av), 3), max_attempts=mx,
                        total_attempts=tot))
    emit(ctx, "b_attempts_per_player.csv", out)

    years = f"""
        WITH wt AS (SELECT nfl_id, combine_position FROM {CR} WHERE combine_position IN ({pos})),
             t AS (SELECT ct.nfl_id, ct.draft_year FROM {CT} ct JOIN wt USING(nfl_id) GROUP BY 1,2)
        SELECT t.draft_year, count(DISTINCT t.nfl_id) n_players,
               (SELECT count(DISTINCT ct.event_id) FROM {CT} ct JOIN wt USING(nfl_id)
                 WHERE ct.draft_year=t.draft_year) n_attempts
        FROM t GROUP BY 1 ORDER BY 1"""
    emit(ctx, "b_class_years.csv",
         [dict(item="b", draft_year=y, n_players_wrte=npl, n_attempts=na_) for y, npl, na_ in _rows(con, years)])

    # class years overall (all positions) for coverage statement
    allc = _rows(con, f"SELECT draft_year, count(DISTINCT nfl_id) FROM {CT} GROUP BY 1 ORDER BY 1")
    emit(ctx, "b_class_years_all_positions.csv",
         [dict(item="b", draft_year=y, n_players_all=npl) for y, npl in allc])
    return out


# --------------------------------------------------------------------------- #
# (c) matched Combine->NFL players per position + route snaps per player
# --------------------------------------------------------------------------- #
def item_c(ctx: dict) -> list[dict]:
    con, cfg = ctx["con"], ctx["cfg"]
    log = ctx["logger"]
    CR = _pq(ctx["tables"]["combine_results"])
    GT = _pq(ctx["tables"]["game_tracking"])
    PP = _pq(ctx["tables"]["player_play"])

    # sample-partial matched count (game tracking available now)
    matched = _rows(con, f"""
        WITH gt AS (SELECT DISTINCT nfl_id FROM {GT})
        SELECT cr.combine_position, count(DISTINCT cr.nfl_id) n_prospects,
               count(DISTINCT CASE WHEN gt.nfl_id IS NOT NULL THEN cr.nfl_id END) n_matched
        FROM {CR} cr LEFT JOIN gt USING(nfl_id)
        GROUP BY 1 ORDER BY 1""")
    status = ctx["game_side_status"]  # PENDING / SAMPLE_PARTIAL / AVAILABLE
    rows = [dict(item="c", check="matched_combine_to_game_tracking", combine_position=p,
                 n_prospects=np_, n_matched=nm, status=status,
                 note="sample-prefix tracking is non-representative" if status != "AVAILABLE" else "")
            for p, np_, nm in matched]
    emit(ctx, "c_matched_sample.csv", rows)

    # PREVIEW: matching against the (complete) player_play file, which lists every game appearance
    # by nfl_id. This is the strongest match currently computable here; the authoritative frame-level
    # match still needs the full game-tracking files (PENDING FULL RUN).
    pmat = _rows(con, f"""
        WITH pp AS (SELECT DISTINCT nfl_id FROM {PP})
        SELECT cr.combine_position, count(DISTINCT cr.nfl_id) n_prospects,
               count(DISTINCT CASE WHEN pp.nfl_id IS NOT NULL THEN cr.nfl_id END) n_matched
        FROM {CR} cr LEFT JOIN pp USING(nfl_id) GROUP BY 1 ORDER BY 1""")
    emit(ctx, "c_matched_player_play_preview.csv",
         [dict(item="c", check="matched_via_player_play", combine_position=p, n_prospects=np_,
               n_matched=nm, status=PENDING,
               note="preview from complete player_play.csv; authoritative match pending frame-level tracking")
          for p, np_, nm in pmat])

    # route snaps per matched player, from the (complete) player_play route_ran field
    rsp = _rows(con, f"""
        WITH rs AS (SELECT nfl_id, count(*) route_snaps
                    FROM {PP} WHERE route_ran IS NOT NULL GROUP BY 1)
        SELECT cr.combine_position, cr.nfl_id, rs.route_snaps
        FROM {CR} cr JOIN rs USING(nfl_id)
        WHERE cr.combine_position IN ('WR','TE') ORDER BY 1,3 DESC""")
    emit(ctx, "c_route_snaps_per_matched_player.csv",
         [dict(item="c", combine_position=p, nfl_id=nid, route_snaps=rs,
               status=PENDING, note="authoritative route-snap gate needs full game-tracking match")
          for p, nid, rs in rsp])

    # summary
    summ = _rows(con, f"""
        WITH rs AS (SELECT nfl_id, count(*) route_snaps FROM {PP} WHERE route_ran IS NOT NULL GROUP BY 1),
             j AS (SELECT cr.combine_position pos, rs.route_snaps
                   FROM {CR} cr JOIN rs USING(nfl_id) WHERE cr.combine_position IN ('WR','TE'))
        SELECT pos, count(*) n_players, min(route_snaps), median(route_snaps), avg(route_snaps), max(route_snaps)
        FROM j GROUP BY 1 ORDER BY 1""")
    emit(ctx, "c_route_snaps_summary.csv",
         [dict(item="c", combine_position=p, n_players=npl, min_route_snaps=mn, median_route_snaps=med,
               mean_route_snaps=round(float(av), 2), max_route_snaps=mx, status=PENDING)
          for p, npl, mn, med, av, mx in summ])
    log.info("(c) matched (sample) + route-snap preview written; authoritative count = %s", PENDING)
    return rows


# --------------------------------------------------------------------------- #
# (d) route type/depth, derivable trajectory metrics, 22-player tracking, ball/TTT
# --------------------------------------------------------------------------- #
def item_d(ctx: dict) -> list[dict]:
    con, cfg = ctx["con"], ctx["cfg"]
    PP = _pq(ctx["tables"]["player_play"])
    GT = _pq(ctx["tables"]["game_tracking"])
    pp = {r[0] for r in con.execute(f"describe select * from {PP}").fetchall()}

    use = {
        "route_ran": "route snap + route type (categorical)",
        "pass_length": "route depth proxy (yards)",
        "target": "whether the route was targeted",
        "separation_at_pass_forward": "separation at pass-forward",
        "coverage_assignment": "per-play coverage assignment",
        "coverage_assignment_at_snap": "coverage assignment at snap",
        "team_coverage_man_zone": "man/zone",
        "team_coverage_type": "coverage family",
        "dropback_duration": "time-to-throw proxy",
    }
    nrows = _scalar(con, f"SELECT count(*) FROM {PP}")
    out = []
    for f in list(use) + ["cushion", "receiver_alignment", "player_get_off"]:
        present = f in pp
        if present:
            nn = _scalar(con, f'SELECT count(*) FROM {PP} WHERE "{f}" IS NOT NULL')
            nd = _scalar(con, f'SELECT count(DISTINCT "{f}") FROM {PP}')
        else:
            nn = nd = None
        out.append(dict(item="d", check="field_available", field=f, present=present,
                        used_for=use.get(f, "context"), n_nonnull=nn, n_distinct=nd,
                        pct_nonnull=(round(100.0 * nn / nrows, 2) if nn is not None else None)))
    emit(ctx, "d_field_profile.csv", out)

    # route_ran is CATEGORICAL (route type) -> rt snap = NOT NULL
    rr = _rows(con, f"""SELECT route_ran, count(*) n FROM {PP} GROUP BY 1
                        ORDER BY (route_ran IS NULL) DESC, n DESC LIMIT 15""")
    emit(ctx, "d_route_type_values.csv",
         [dict(item="d", route_ran=(v if v is not None else "<NULL:no route>"), n=n) for v, n in rr])

    pl = _rows(con, f"""SELECT pass_length, count(*) n FROM {PP} WHERE pass_length IS NOT NULL
                        GROUP BY 1 ORDER BY 1""")
    emit(ctx, "d_pass_length_values.csv", [dict(item="d", pass_length=v, n=n) for v, n in pl])

    # players tracked per play (sample partial; full needed for 22-player verification)
    ppp = con.execute(f"""
        WITH p AS (SELECT game_id, play_id, count(DISTINCT nfl_id) n FROM {GT} GROUP BY 1,2)
        SELECT min(n), median(n), max(n), count(*) FROM p""").fetchone()
    emit(ctx, "d_players_per_play.csv",
         [dict(item="d", check="n_players_tracked_per_play", dataset=ctx["game_dataset"],
               min_players=ppp[0], median_players=ppp[1], max_players=ppp[2], n_plays=ppp[3],
               status=ctx["game_side_status"],
               note="22-player tracking (nearest-defender/ball-distance controls) verifiable only on FULL data")])

    # qualitative answers to (d)
    emit(ctx, "d_answers.csv", [
        dict(item="d", question="Is route type provided?", answer="YES",
             evidence="player_play.route_ran is a categorical route string (GO/HITCH/CROSS/...); NULL = no route run"),
        dict(item="d", question="Is route depth provided?", answer="YES",
             evidence="player_play.pass_length (integer yards) provided; deep threshold in config"),
        dict(item="d", question="Can depth/break angle be derived from trajectories?",
             answer=PENDING, evidence="needs full game-tracking x,y per frame (derivable in principle)"),
        dict(item="d", question="Are all 22 players tracked per play?",
             answer=PENDING, evidence="sample is a truncated prefix; full-run check required"),
        dict(item="d", question="Ball location available?",
             answer=PENDING, evidence="combine_tracking has a BALL entity; game ball location needs full tracking"),
        dict(item="d", question="Time-to-throw available?",
             answer="PROXY", evidence="player_play.dropback_duration (+ time_to_pressure/time_to_qb_hurry) = TTT proxy"),
    ])
    return out


# --------------------------------------------------------------------------- #
# (e) plays included; NON-targeted route coverage
# --------------------------------------------------------------------------- #
def item_e(ctx: dict) -> list[dict]:
    con, cfg = ctx["con"], ctx["cfg"]
    PP = _pq(ctx["tables"]["player_play"])
    GAMES = _pq(ctx["tables"]["games"])
    CR = _pq(ctx["tables"]["combine_results"])

    ct = con.execute(f"""
        SELECT
          count(*) FILTER (WHERE route_ran IS NOT NULL) route_snaps,
          count(*) FILTER (WHERE route_ran IS NOT NULL AND target) targeted,
          count(*) FILTER (WHERE route_ran IS NOT NULL AND NOT target) non_targeted,
          count(*) FILTER (WHERE route_ran IS NULL AND target) target_no_route,
          count(*) FILTER (WHERE target) all_targets,
          count(*) FILTER (WHERE route_ran IS NOT NULL AND pass_length IS NOT NULL) with_length
        FROM {PP}""").fetchone()
    rs, tg, nt, tnr, at, wl = ct
    out = [dict(item="e", metric="route_snaps", value=rs),
           dict(item="e", metric="route_snaps_targeted", value=tg),
           dict(item="e", metric="route_snaps_non_targeted", value=nt),
           dict(item="e", metric="share_route_snaps_non_targeted", value=round(nt / rs, 4) if rs else None),
           dict(item="e", metric="all_targets_in_file", value=at),
           dict(item="e", metric="targets_without_route_ran", value=tnr),
           dict(item="e", metric="route_snaps_with_pass_length", value=wl)]
    emit(ctx, "e_route_target_crosstab.csv", out)

    # by WR/TE position
    byp = _rows(con, f"""
        SELECT cr.combine_position, count(*) route_snaps,
               count(*) FILTER (WHERE pp.target) targeted,
               count(*) FILTER (WHERE NOT pp.target) non_targeted
        FROM {PP} pp JOIN {CR} cr USING(nfl_id)
        WHERE pp.route_ran IS NOT NULL AND cr.combine_position IN ('WR','TE')
        GROUP BY 1 ORDER BY 1""")
    emit(ctx, "e_route_target_by_position.csv",
         [dict(item="e", combine_position=p, route_snaps=rs_, targeted=t,
               non_targeted=nt_, share_non_targeted=(round(nt_ / rs_, 4) if rs_ else None))
          for p, rs_, t, nt_ in byp])

    # coverage: seasons/games/plays included
    cov = _rows(con, f"""
        SELECT g.season, count(DISTINCT pp.game_id) n_games,
               count(DISTINCT (pp.game_id||'-'||pp.play_id)) n_plays,
               count(*) FILTER (WHERE pp.route_ran IS NOT NULL) route_snaps
        FROM {PP} pp JOIN {GAMES} g USING(game_id)
        GROUP BY 1 ORDER BY 1""")
    emit(ctx, "e_coverage_by_season.csv",
         [dict(item="e", season=s, n_games=ng, n_plays=npl, route_snaps=rs_) for s, ng, npl, rs_ in cov])
    tot = con.execute(f"""SELECT count(DISTINCT game_id), count(DISTINCT (game_id||'-'||play_id))
                          FROM {PP}""").fetchone()
    emit(ctx, "e_coverage_total.csv",
         [dict(item="e", metric="distinct_games", value=tot[0]),
          dict(item="e", metric="distinct_plays", value=tot[1])])
    return out


# --------------------------------------------------------------------------- #
# (f) provided dis/a reliability + sampling rate/units/frames
# --------------------------------------------------------------------------- #
def item_f(ctx: dict) -> list[dict]:
    con, cfg = ctx["con"], ctx["cfg"]
    rtol = cfg["audit"]["distance_rtol"]
    out = []
    for label, tbl in (("combine", _pq(ctx["tables"]["combine_tracking"])),):
        r = con.execute(f"""SELECT min(x),max(x),min(y),max(y),min(s),max(s),min(a),max(a),min(dis),max(dis)
                            FROM {tbl} WHERE entity_type='PLAYER'""").fetchone()
        dt = _scalar(con, f"""WITH f AS (SELECT event_id, epoch_ms(time) t FROM {tbl} WHERE entity_type='PLAYER'),
            d AS (SELECT (t-lag(t) OVER (PARTITION BY event_id ORDER BY t))/1000.0 dt FROM f)
            SELECT median(dt) FROM d WHERE dt IS NOT NULL""")
        out.append(dict(item="f", dataset=label, axis="x/y/s/a/dis ranges",
                        x_min=r[0], x_max=r[1], y_min=r[2], y_max=r[3], s_min=r[4], s_max=r[5],
                        a_min=r[6], a_max=r[7], dis_min=r[8], dis_max=r[9], median_dt_s=dt,
                        frame="local Combine frame"))
    # game sample frame ranges (units)
    GT = _pq(ctx["tables"]["game_tracking"])
    rg = con.execute(f"SELECT min(x),max(x),min(y),max(y),min(s),max(s),min(a),max(a) FROM {GT}").fetchone()
    out.append(dict(item="f", dataset=ctx["game_dataset"], axis="x/y/s/a ranges",
                    x_min=rg[0], x_max=rg[1], y_min=rg[2], y_max=rg[3], s_min=rg[4], s_max=rg[5],
                    a_min=rg[6], a_max=rg[7], median_dt_s=None, frame="NFL 120x53.3 yd game frame"))
    emit(ctx, "f_sampling_units.csv", out)

    CT = _pq(ctx["tables"]["combine_tracking"])
    dis_rows = con.execute(f"""
        WITH f AS (
          SELECT event_id, drill_type, x, y, dis,
                 lag(x) OVER w lx, lag(y) OVER w ly
          FROM {CT} WHERE entity_type='PLAYER'
          WINDOW w AS (PARTITION BY event_id ORDER BY time)),
        per AS (
          SELECT event_id, drill_type,
                 sum(dis) sum_dis,
                 sum(CASE WHEN lx IS NOT NULL THEN sqrt((x-lx)^2+(y-ly)^2) ELSE 0 END) path_xy
          FROM f GROUP BY 1,2)
        SELECT drill_type, count(*) n_attempts,
               median(under) median_under_count,
               avg(CASE WHEN under > {rtol} THEN 1.0 ELSE 0.0 END) share_under_more_than_rtol
        FROM (SELECT drill_type, event_id, 1.0 - sum_dis/path_xy AS under
              FROM per WHERE path_xy > 0) GROUP BY 1 ORDER BY 1""").fetchall()
    emit(ctx, "f_dis_reliability.csv",
         [dict(item="f", drill_type=d, n_attempts=na, median_under_count_fraction=(
             round(float(mu), 4) if mu is not None else None),
             rtol=rtol, share_attempts_beyond_rtol=(round(float(sh), 4) if sh is not None else None))
          for d, na, mu, sh in dis_rows])

    acc_rows = con.execute(f"""
        WITH f AS (
          SELECT event_id, drill_type, s, a, epoch_ms(time) t,
                 lag(s) OVER w ls, lag(epoch_ms(time)) OVER w lt
          FROM {CT} WHERE entity_type='PLAYER'
          WINDOW w AS (PARTITION BY event_id ORDER BY time))
        SELECT drill_type, count(*) n,
               corr(a, (s-ls)/((t-lt)/1000.0)) corr_a_vs_dvdt,
               median(a / NULLIF((s-ls)/((t-lt)/1000.0),0)) median_ratio
        FROM f WHERE ls IS NOT NULL AND t>lt GROUP BY 1 ORDER BY 1""").fetchall()
    emit(ctx, "f_accel_reliability.csv",
         [dict(item="f", drill_type=d, n=n, corr_provided_a_vs_derived_dvdt=(
             round(float(c), 4) if c is not None else None),
             median_ratio_provided_over_derived=(round(float(mr), 4) if mr is not None else None))
          for d, n, c, mr in acc_rows])

    fr = con.execute(f"""
        WITH per AS (SELECT event_id, drill_type, count(*) nf FROM {CT} WHERE entity_type='PLAYER' GROUP BY 1,2)
        SELECT drill_type, count(*) n_attempts, avg(nf), median(nf), max(nf) FROM per GROUP BY 1 ORDER BY 1""").fetchall()
    frames_rows = [dict(item="f", drill_type=d, n_attempts=na, mean_frames=round(float(a0), 2),
                        median_frames=m0, max_frames=mx) for d, na, a0, m0, mx in fr]
    emit(ctx, "f_frames_per_attempt.csv", frames_rows)
    out += [dict(item="f", drill_type=d, n_attempts=na,
                 median_under_count_fraction=(round(float(mu), 4) if mu is not None else None),
                 rtol=rtol, share_attempts_beyond_rtol=(round(float(sh), 4) if sh is not None else None))
            for d, na, mu, sh in dis_rows]
    return out


# --------------------------------------------------------------------------- #
# (g) test-retest ICC of candidate Combine features
# --------------------------------------------------------------------------- #
def _features_for_attempt(x, y, s, a, t, cfg) -> dict[str, float]:
    audit = cfg["audit"]
    x = np.asarray(x, float); y = np.asarray(y, float); s = np.asarray(s, float); t = np.asarray(t, float)
    dt = np.diff(t)
    dt[dt <= 0] = np.nan
    step = np.hypot(np.diff(x), np.diff(y))
    v = np.nan_to_num(np.concatenate([[0.0], step / dt]))
    dist = np.concatenate([[0.0], np.cumsum(step)])
    feats: dict[str, float] = {}
    feats["peak_speed"] = float(np.max(v))
    feats["peak_speed_provided_s"] = float(np.max(s))
    # anchor splits at MOVEMENT ONSET (first frame above the onset speed), because the
    # 40-yd dash starts from a stance and the pre-movement delay varies between attempts.
    onset = audit["movement_onset_speed_yds"]
    idx = np.where(v >= onset)[0]
    i0 = int(idx[0]) if len(idx) else 0
    t_rel, d_rel = t - t[i0], dist - dist[i0]

    def _interp(arr_x, arr_y, q):
        if arr_x[-1] < q:
            return np.nan
        pos = max(int(np.searchsorted(arr_x, q)), 1)
        denom = arr_x[pos] - arr_x[pos - 1]
        f = 0.0 if denom <= 0 else (q - arr_x[pos - 1]) / denom
        return float(arr_y[pos - 1] + f * (arr_y[pos] - arr_y[pos - 1]))

    for d in audit["split_distances_yd"]:
        feats[f"time_to_{d}yd"] = _interp(d_rel, t_rel, d)
        feats[f"speed_at_{d}yd"] = _interp(d_rel, v, d)
    for ts in audit.get("speed_at_times_s", []):
        feats[f"speed_at_t{ts}s"] = _interp(t_rel, v, ts)
    dv = np.gradient(v, t)
    feats["min_accel"] = float(np.nanmin(dv))
    feats["max_decel"] = float(-np.nanmin(dv))
    if len(v) >= 3:
        ipk = int(np.argmax(v))
        feats["min_speed_after_peak"] = float(np.min(v[ipk:]))
        feats["speed_deficit_after_peak"] = float(feats["peak_speed"] - feats["min_speed_after_peak"])
    else:
        feats["min_speed_after_peak"] = np.nan
        feats["speed_deficit_after_peak"] = np.nan
    # exponential envelope fit v(t)=vmax*(1-exp(-t/tau)) -> vmax, tau, a0=vmax/tau.
    # Single config-driven fitter shared with Phase 2 (src/combine_features.py; no magic numbers).
    feats["env_vmax"], feats["env_tau"], feats["env_a0"] = cf.fit_envelope(t_rel, v, cfg)
    return feats


def _icc_1_1(groups: dict[int, list[float]], min_players: int) -> dict:
    """Test-retest ICC(1,1) over repeated attempts within players.

    Only players with >=2 attempts contribute (a repeat/paired test-retest requires within-player
    replication); players with a single attempt carry no test-retest information.
    """
    clean = {k: [v for v in vals if not (isinstance(v, float) and np.isnan(v))] for k, vals in groups.items()}
    all_groups = {k: vals for k, vals in clean.items() if len(vals) >= 1}
    rep = {k: vals for k, vals in all_groups.items() if len(vals) >= 2}
    n_with_repeat = len(rep)
    k = len(rep)
    N = sum(len(v) for v in rep.values())
    res = {"n_players_total": len(all_groups), "n_players_with_repeat": n_with_repeat, "n_attempts_used": N,
           "icc": None, "status": None}
    if n_with_repeat < min_players:
        res["status"] = f"UNESTIMABLE: only {n_with_repeat} players with >=2 attempts (<{min_players})"
        return res
    if k < 2 or N <= k:
        res["status"] = f"UNESTIMABLE: too few groups/observations (k={k}, N={N})"
        return res
    allv = np.concatenate([np.asarray(vals) for vals in rep.values()])
    grand = float(np.mean(allv))
    ssb = sum(len(vals) * (np.mean(vals) - grand) ** 2 for vals in rep.values())
    ssw = sum(float(np.sum((np.asarray(vals) - np.mean(vals)) ** 2)) for vals in rep.values())
    dfb, dfw = k - 1, N - k
    if dfw <= 0:
        res["status"] = f"UNESTIMABLE: no within-player df (dfw={dfw})"
        return res
    msb, msw = ssb / dfb, ssw / dfw
    k0 = (N - sum(len(v) ** 2 for v in rep.values()) / N) / (k - 1)
    if msw <= 1e-12:
        res["status"] = "UNESTIMABLE: zero within-player variance"
        return res
    res["icc"] = float((msb - msw) / (msb + (k0 - 1) * msw))
    res["msb"] = float(msb)
    res["msw"] = float(msw)
    res["mean_attempts_k0"] = float(k0)
    res["status"] = "ESTIMATED"
    return res


def item_g(ctx: dict) -> list[dict]:
    con, cfg = ctx["con"], ctx["cfg"]
    audit = cfg["audit"]
    CT = _pq(ctx["tables"]["combine_tracking"])
    drills = audit["icc_repeat_feature_drills"]
    dlist = ",".join(f"'{d}'" for d in drills)
    arr = con.execute(f"""
        SELECT nfl_id, event_id, drill_type, x, y, s, a,
               (epoch_ms(time) - min(epoch_ms(time)) OVER (PARTITION BY event_id))/1000.0 t
        FROM {CT} WHERE entity_type='PLAYER' AND drill_type IN ({dlist})
        ORDER BY nfl_id, event_id, t""").fetchnumpy()

    groups: dict[str, dict[str, dict[int, list[float]]]] = {}  # drill -> feature -> player -> vals
    n_attempts: dict[tuple[str, str], int] = {}
    feats_all = []
    n = len(arr["nfl_id"])
    cur: dict | None = None
    key = None
    def _s(x) -> str:
        return x.decode() if isinstance(x, (bytes, bytearray)) else str(x)

    for i in range(n):
        k2 = (int(arr["nfl_id"][i]), _s(arr["event_id"][i]), _s(arr["drill_type"][i]))
        if key != k2:
            if cur is not None:
                feats = _features_for_attempt(cur["x"], cur["y"], cur["s"], cur["a"], cur["t"], cfg)
                for fname, fval in feats.items():
                    groups.setdefault(key[2], {}).setdefault(fname, {}).setdefault(key[0], []).append(fval)
                n_attempts[(key[2], str(key[0]))] = n_attempts.get((key[2], str(key[0])), 0) + 1
                feats_all.append(dict(drill_type=key[2], nfl_id=key[0], **feats))
            key = k2
            cur = {"x": [], "y": [], "s": [], "a": [], "t": []}
        cur["x"].append(float(arr["x"][i]))
        cur["y"].append(float(arr["y"][i]))
        cur["s"].append(float(arr["s"][i]))
        cur["a"].append(float(arr["a"][i]))
        cur["t"].append(float(arr["t"][i]))
    if cur is not None:
        feats = _features_for_attempt(cur["x"], cur["y"], cur["s"], cur["a"], cur["t"], cfg)
        for fname, fval in feats.items():
            groups.setdefault(key[2], {}).setdefault(fname, {}).setdefault(key[0], []).append(fval)
        n_attempts[(key[2], str(key[0]))] = n_attempts.get((key[2], str(key[0])), 0) + 1
        feats_all.append(dict(drill_type=key[2], nfl_id=key[0], **feats))

    rows = []
    for drill in drills:
        for fname in sorted(groups.get(drill, {})):
            r = _icc_1_1(groups[drill][fname], audit["icc_min_players_with_repeat"])
            rows.append(dict(item="g", drill_type=drill, feature=fname,
                             repeat_source="distinct event_id (attempts) of same (nfl_id, drill_type)",
                             icc=(round(r["icc"], 4) if r["icc"] is not None else None),
                             status=r["status"],
                             n_players_total=r["n_players_total"],
                             n_players_with_repeat=r["n_players_with_repeat"],
                             n_attempts_used=r["n_attempts_used"]))
    emit(ctx, "g_feature_icc.csv", rows)
    # per-attempt feature dump (transparency)
    emit(ctx, "g_attempt_features.csv",
         [dict(item="g", **{k: (round(v, 4) if isinstance(v, float) and not np.isnan(v) else v)
                            for k, v in fa.items()}) for fa in feats_all])
    return rows


# --------------------------------------------------------------------------- #
# (h)/(i) game-side exceedance & scatter (full-run; scripted)
# --------------------------------------------------------------------------- #
def _combine_ceiling(ctx: dict) -> dict[int, float]:
    CT = _pq(ctx["tables"]["combine_tracking"])
    rows = ctx["con"].execute(f"""
        WITH f AS (SELECT nfl_id, event_id, x, y, epoch_ms(time) t,
                          lag(x) OVER w lx, lag(y) OVER w ly, lag(epoch_ms(time)) OVER w lt
                   FROM {CT} WHERE entity_type='PLAYER' AND drill_type='FORTY_YARD_DASH'
                   WINDOW w AS (PARTITION BY event_id ORDER BY time)),
             per AS (SELECT nfl_id, event_id,
                          max(sqrt((x-lx)^2+(y-ly)^2)/NULLIF((t-lt)/1000.0,0)) peak
                     FROM f WHERE lx IS NOT NULL GROUP BY 1,2)
        SELECT nfl_id, max(peak) FROM per GROUP BY 1""").fetchall()
    return {int(nid): float(p) for nid, p in rows}


def _game_effort_peaks(ctx: dict) -> list[tuple]:
    """Per (game,play,player) peak x/y-derived **game** speed under the frame-level despike.

    Shares the SAME greedy forward despike as ``game_features.effort_kinematics_sql`` (revision 3,
    D35): an impossible per-frame speed sample (> ``game.speed_cap_yds``) is **discarded** and the
    peak is recomputed from the **retained** frames, so the audit exceedance (items h/i) is not
    contaminated by impossible tracking spikes. The effort is never dropped; a degenerate effort
    (<2 retained frames) is kept with peak ``0.0`` (the empty max is coalesced). The Combine side
    (``_combine_ceiling``) is deliberately NOT bounded — the speed bound is game-side only.
    """
    GT = _pq(ctx["tables"]["game_tracking"])
    cap = float(ctx["cfg"]["game"]["speed_cap_yds"])
    scan = gf._despike_scan_sql(GT, cap)
    return ctx["con"].execute(scan + """
        SELECT game_id, play_id, nfl_id, COALESCE(max(spd), 0.0) peak_speed
        FROM scan WHERE kept GROUP BY 1, 2, 3""").fetchall()


def _route_efforts(ctx: dict) -> dict[int, list[tuple]]:
    """Per-player list of (peak_speed, pass_length) over route snaps, joining
    frame-derived per-play peaks to the route/pass context in player_play."""
    PP = _pq(ctx["tables"]["player_play"])
    peaks = {}
    for gid, pid, nid, peak in _game_effort_peaks(ctx):
        peaks[(int(gid), int(pid), int(nid))] = float(peak)
    ctxmap = {}
    for gid, pid, nid, plen in ctx["con"].execute(
            f"SELECT game_id, play_id, nfl_id, pass_length FROM {PP} WHERE route_ran IS NOT NULL").fetchall():
        ctxmap[(int(gid), int(pid), int(nid))] = (int(plen) if plen is not None else None)
    out: dict[int, list[tuple]] = {}
    for k, plen in ctxmap.items():
        if k in peaks:
            out.setdefault(k[2], []).append((peaks[k], plen))
    return out


def item_h(ctx: dict) -> list[dict]:
    if not ctx["game_side_available"]:
        rows = [dict(item="h", check="exceedance_rate", status=PENDING,
                     note="in-game speed/accel vs Combine envelope; needs full game-tracking (see notes/pending_full_run.md P3)")]
        emit(ctx, "h_exceedance.csv", rows)
        return rows
    ceiling = _combine_ceiling(ctx)
    margin = ctx["cfg"]["audit"]["exceedance_margin_rel"]
    efforts = _route_efforts(ctx)
    rows, tot_eff, tot_exc = [], 0, 0
    for nid, effs in sorted(efforts.items()):
        if nid not in ceiling:
            continue
        c = ceiling[nid]
        ne = len(effs)
        nex = sum(1 for pk, _ in effs if pk > c * (1 + margin))
        tot_eff += ne
        tot_exc += nex
        rows.append(dict(item="h", nfl_id=nid, combine_ceiling_yds=round(c, 4), n_efforts=ne,
                         n_exceed=nex, exceedance_rate=(round(nex / ne, 4) if ne else None)))
    rows.append(dict(item="h", nfl_id="OVERALL", n_efforts=tot_eff, n_exceed=tot_exc,
                     exceedance_rate=(round(tot_exc / tot_eff, 4) if tot_eff else None),
                     note=f"margin_rel={margin}"))
    emit(ctx, "h_exceedance.csv", rows, subdir=ctx.get("subdir"))
    return rows


def item_i(ctx: dict) -> list[dict]:
    if not ctx["game_side_available"]:
        rows = [dict(item="i", check="combine_vs_ingame_deep", status=PENDING,
                     note="scatter of Combine peak speed vs in-game top speed on deep routes; needs full game-tracking")]
        emit(ctx, "i_scatter.csv", rows)
        return rows
    ceiling = _combine_ceiling(ctx)
    deep = ctx["cfg"]["audit"]["deep_pass_length_yd"]
    top: dict[int, float] = {}
    ndeep: dict[int, int] = {}
    for nid, effs in _route_efforts(ctx).items():
        if nid not in ceiling:
            continue
        for pk, plen in effs:
            if plen is not None and plen >= deep:
                top[nid] = max(top.get(nid, 0.0), pk)
                ndeep[nid] = ndeep.get(nid, 0) + 1
    rows = [dict(item="i", nfl_id=nid, combine_peak_speed_yds=round(ceiling[nid], 4),
                 in_game_top_speed_deep_yds=round(top[nid], 4), n_deep_efforts=ndeep[nid])
            for nid in sorted(top)]
    emit(ctx, "i_scatter.csv", rows, subdir=ctx.get("subdir"))
    return rows


# --------------------------------------------------------------------------- #
# go/no-go decision
# --------------------------------------------------------------------------- #
def decide(ctx: dict, results: dict) -> dict:
    cfg = ctx["cfg"]
    audit = cfg["audit"]
    gates = {
        "min_matched_wr": audit.get("min_matched_wr"),
        "min_route_snaps_per_player": audit.get("min_route_snaps_per_player"),
        "min_icc": audit.get("min_icc"),
    }
    frozen = [k for k, v in gates.items() if v is not None]
    icc_rows = results.get("g", [])
    icc_est = [r for r in icc_rows if str(r.get("status", "")).startswith("ESTIMATED")]
    icc_unest = [r for r in icc_rows if str(r.get("status", "")).startswith("UNESTIMABLE")]
    missing_fields = [r for r in results.get("d", []) if r.get("check") == "field_available" and not r.get("present")]

    if not frozen:
        decision = "CONDITIONAL — thresholds not frozen (prereg skeleton); sample cannot exercise the game-side gate"
    elif missing_fields:
        decision = "NO-GO — required Combine/game field missing (see notes/blockers.md)"
    else:
        decision = "see gate values"
    return dict(decision=decision, gates=gates, frozen_gates=frozen, missing_fields=[r["field"] for r in missing_fields],
                n_icc_estimated=len(icc_est), n_icc_unestimable=len(icc_unest),
                game_side_status=ctx["game_side_status"])


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="BDB27 Phase 1 audit (go/no-go gate)")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore checkpoints / rebuild Parquet")
    ap.add_argument("--synthetic", action="store_true",
                    help="run the game-side items against the committed synthetic fixture")
    ap.add_argument("--synthetic-regime", choices=["binding", "null"], default="binding")
    ap.add_argument("--config", default=None)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()
    con = common.connect(cfg["runtime"]["duckdb_threads"])

    audit_dir = common.resolve(cfg, "outputs", cfg["audit"]["out_subdir"])
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Pre-registration freeze gate: a --mode full audit may not proceed on an unfrozen/missing prereg.
    # (The audit itself produces the go/no-go decision, so only the freeze is required here.)
    gate.enforce(cfg, mode, "01_audit", requires_game_side=False, logger=log)

    prov = PROV_SYNTH if args.synthetic else common.provenance(cfg, mode)
    ctx: dict[str, Any] = dict(cfg=cfg, mode=mode, prov=prov, con=con, logger=log, audit_dir=audit_dir)

    # ---- resolve tables (identical code path; synthetic swaps the game + combine inputs) ----
    tables = {}
    with common.timed_stage(cfg, f"01_audit[{mode}{'-synthetic' if args.synthetic else ''}]", mode,
                            synthetic=args.synthetic) as _:
        parq = common.resolve(cfg, "parquet")
        if args.synthetic:
            fixdir = common.resolve(cfg, "synthetic") / args.synthetic_regime
            cache = audit_dir / "_synthetic_cache" / args.synthetic_regime
            for name, fn in (("combine_tracking", "combine_tracking.csv"), ("combine_results", "combine_results.csv"),
                             ("game_tracking", "game_tracking.csv"), ("player_play", "player_play.csv"),
                             ("games", "games.csv")):
                dst = cache / (name + ".parquet")
                common.to_parquet(con, fixdir / fn, dst, force=args.force, logger=None)
                tables[name] = dst
            ctx["game_dataset"] = f"synthetic:{args.synthetic_regime}"
            ctx["game_side_status"] = "AVAILABLE"
            ctx["game_side_available"] = True
            ctx["subdir"] = "synthetic"
        else:
            for name in ("combine_tracking", "combine_results", "player_play", "games"):
                src = common.csv_path(cfg, name)
                dst = parq / (Path(cfg["files"][name]).stem + ".parquet")
                common.to_parquet(con, src, dst, force=args.force, logger=log)
                tables[name] = dst
            if mode == "full":
                # Expose ONE normalised `game_tracking` entry = the union of the per-season parquets,
                # so assert_schemas + items c/d/f/h/i see all seasons through the single key.
                tables["game_tracking"] = _parquet_full(con, cfg, "game_tracking_full", args.force, log)
                ctx["game_dataset"] = "game_tracking_2023/24/25 (full)"
                ctx["game_side_status"] = "AVAILABLE"
                ctx["game_side_available"] = True
            else:
                key = cfg["mode"]["sample"]["game_tracking"]
                src = common.csv_path(cfg, key)
                dst = parq / (Path(cfg["files"][key]).stem + ".parquet")
                common.to_parquet(con, src, dst, force=args.force, logger=log)
                tables["game_tracking"] = dst
                ctx["game_dataset"] = "game_tracking_2023_sample (2^20-row prefix, non-representative)"
                ctx["game_side_status"] = "SAMPLE_PARTIAL"
                ctx["game_side_available"] = False
        ctx["tables"] = tables

        assert_schemas(ctx)
        results = {}
        results["a"] = item_a(ctx)
        results["b"] = item_b(ctx)
        results["c"] = item_c(ctx)
        results["d"] = item_d(ctx)
        results["e"] = item_e(ctx)
        results["f"] = item_f(ctx)
        results["g"] = item_g(ctx)
        results["h"] = item_h(ctx)
        results["i"] = item_i(ctx)
        dec = decide(ctx, results)

    # ---- SUMMARY.md ----
    out_dir = (audit_dir / ctx["subdir"]) if ctx.get("subdir") else audit_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = render_summary(ctx, results, dec)
    (out_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")
    (out_dir / "audit_decision.json").write_text(json.dumps(dec, indent=2), encoding="utf-8")
    log.info("audit complete: decision=%s", dec["decision"])


def _fmt_rows(rows: list[dict], cols: list[str], limit: int | None = None) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in (rows[:limit] if limit else rows):
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render_summary(ctx: dict, results: dict, dec: dict) -> str:
    a, b, d, e, g = results["a"], results["b"], results["d"], results["e"], results["g"]
    f = results["f"]
    lines = []
    lines.append(f"# Phase 1 Audit — SUMMARY ({ctx['prov']})")
    lines.append("")
    lines.append(f"- **mode:** `{ctx['mode']}`  ·  **synthetic:** {ctx.get('subdir') == 'synthetic'}  ·  "
                 f"**game dataset:** {ctx['game_dataset']}")
    lines.append(f"- **game-side status:** {ctx['game_side_status']}  ·  "
                 f"frame-level game side (speed/accel) = `PENDING FULL RUN`")
    lines.append("- **sources:** (a,b,f,g) `combine_tracking.csv` **complete**; (d,e) `player_play.csv` "
                 "**complete** (present here — not sample-limited); (c) match preview from the complete "
                 "`player_play.csv` + non-representative tracking sample; (h,i) full game tracking = "
                 "`PENDING FULL RUN`. (All tables carry the run-mode provenance label regardless of source.)")
    lines.append(f"- **go/no-go:** {dec['decision']}")
    lines.append("")
    lines.append("## (a) WR/TE Combine drills")
    lines.append(_fmt_rows(a, ["combine_position", "drill_type", "family", "n_players", "n_attempts"]))
    lines.append("")
    lines.append("## (b) attempts per player per drill (WR/TE)")
    lines.append(_fmt_rows(b, ["combine_position", "drill_type", "n_players", "n_with_1_attempt",
                               "n_with_ge2_attempts", "median_attempts", "max_attempts", "total_attempts"]))
    lines.append("")
    lines.append("## (d) field availability / answers")
    lines.append(_fmt_rows([r for r in d if r.get("check") == "field_available"],
                           ["field", "present", "pct_nonnull", "used_for"]))
    lines.append("")
    lines.append("## (e) route vs target (non-targeted coverage)")
    lines.append(_fmt_rows(e, ["metric", "value"]))
    lines.append("")
    lines.append("## (f) provided dis / a reliability (by drill)")
    disrows = [r for r in f if "median_under_count_fraction" in r]
    lines.append(_fmt_rows(disrows, ["drill_type", "n_attempts", "median_under_count_fraction",
                                     "share_attempts_beyond_rtol"]))
    lines.append("")
    lines.append("## (g) test-retest ICC of candidate Combine features")
    lines.append(_fmt_rows(g, ["drill_type", "feature", "icc", "status", "n_players_with_repeat",
                               "n_attempts_used"]))
    lines.append("")
    lines.append("## go/no-go decision")
    lines.append(f"- decision: **{dec['decision']}**")
    lines.append(f"- gates: `{dec['gates']}`  (frozen: {dec['frozen_gates'] or 'none'})")
    lines.append(f"- missing required fields: {dec['missing_fields'] or 'none'}")
    lines.append(f"- ICC: {dec['n_icc_estimated']} estimated, {dec['n_icc_unestimable']} unestimable")
    lines.append("")
    lines.append("## Note on synthetic fixtures")
    lines.append("Game-side items that need full data are `PENDING FULL RUN`. If run with `--synthetic`, "
                 "outputs go to `outputs/audit/synthetic/` and are stamped "
                 f"`{PROV_SYNTH}` — they exercise the code path and are NEVER presented as findings.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
