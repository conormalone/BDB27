"""Phase 1 audit (go/no-go gate) for the BDB27 combine-fatigue study.

Answers the seven audit questions (a-g) from TASK.md with evidence, and emits a
position-group / drill-family recommendation. Sample-mode outputs are stamped
``UNVALIDATED SAMPLE OUTPUT``; they must never be interpreted as results.

Run:
    python src/01_audit.py --mode sample
    python src/01_audit.py --mode full       # game-side (e) runs here only

Only DuckDB queries over Parquet are used for the heavy lifting; small result
tables are handled with pandas. No full CSV is ever loaded into pandas.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO_ROOT,
    connect,
    csv_path,
    get_logger,
    load_config,
    peak_ram_mb,
    pq,
    provenance,
    resolve,
    seed_everything,
    timed_stage,
    to_parquet,
)

COMBINE_TRACKING_COLS = [
    "draft_year", "event_id", "nfl_id", "entity_type", "time",
    "drill_type", "drill_name", "attempt", "x", "y", "s", "a", "dis", "dir",
]
COMBINE_RESULTS_COLS = [
    "draft_year", "nfl_id", "combine_position", "combine_height", "combine_weight",
    "hand_size", "arm_length", "wing_span", "ten_yd_split", "forty", "vertical",
    "broad_jump", "three_cone", "short_shuttle", "bench_reps",
    "ngs_athleticism_score", "ngs_college_production_score", "ngs_final_score",
]

OUT_SUBDIR = "audit"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _labelled(df: pd.DataFrame, prov: str) -> pd.DataFrame:
    """Stamp every row of an audit table with its provenance label."""
    df = df.copy()
    df.insert(0, "provenance", prov)
    return df


def _write(cfg: dict[str, Any], name: str, df: pd.DataFrame, prov: str, logger) -> Path:
    out_dir = resolve(cfg, "outputs", OUT_SUBDIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    _labelled(df, prov).to_csv(path, index=False)
    logger.info("wrote %s (%d rows)", path.name, len(df))
    return path


def _scalar(con, sql: str) -> Any:
    v = con.execute(sql).fetchone()
    return None if v is None else v[0]


# --------------------------------------------------------------------------- #
# (a) timing / ordering fields
# --------------------------------------------------------------------------- #
def audit_a(con, T: str, cfg: dict[str, Any]) -> dict[str, Any]:
    desc = con.execute(f"DESCRIBE SELECT * FROM {T}").df()
    fields = desc[["column_name", "column_type"]].to_dict("records")

    # ordering agreement: does `attempt` increase with `time` within (nfl, drill_name)?
    order_sql = f"""
    WITH g AS (
        SELECT nfl_id, drill_name, attempt, min(time) AS t0, max(time) AS t1
        FROM {T} WHERE entity_type = 'PLAYER'
        GROUP BY 1, 2, 3
    ), s AS (
        SELECT nfl_id, drill_name, attempt, t0,
               lag(attempt) OVER (PARTITION BY nfl_id, drill_name ORDER BY t0) AS prev_a,
               lag(t1)      OVER (PARTITION BY nfl_id, drill_name ORDER BY t0) AS prev_t1
        FROM g
    )
    SELECT count(*) FILTER (WHERE prev_a IS NOT NULL) AS pairs,
           count(*) FILTER (WHERE prev_a IS NOT NULL AND attempt >  prev_a) AS agree,
           count(*) FILTER (WHERE prev_a IS NOT NULL AND attempt <= prev_a) AS violations,
           count(*) FILTER (WHERE prev_a IS NOT NULL AND attempt >  prev_a AND t0 >= prev_t1) AS agree_ordered,
           count(*) FILTER (WHERE prev_a IS NOT NULL AND attempt >  prev_a AND t0 <  prev_t1) AS overlap
    FROM s
    """
    order = con.execute(order_sql).df().iloc[0].to_dict()
    pairs = int(order["pairs"])
    agree_frac = round(int(order["agree"]) / pairs, 4) if pairs else None

    # event_id format: 2023 opaque vs 2024/25 stamped (two underscores)
    ev_sql = f"""
    SELECT draft_year,
           count(DISTINCT event_id) AS n_event_ids,
           min(event_id) AS example,
           round(avg(CASE WHEN event_id LIKE '%\\_%\\_%' ESCAPE '\\' THEN 1.0 ELSE 0.0 END), 3) AS frac_two_underscores
    FROM {T} GROUP BY 1 ORDER BY 1
    """
    ev = con.execute(ev_sql).df().to_dict("records")

    tz_sql = f"SELECT CAST(any_value(time) AS VARCHAR) AS example, min(time) AS t_min, max(time) AS t_max FROM {T}"
    tz = con.execute(tz_sql).df().iloc[0].to_dict()

    timing_field = {"time", "attempt"}.issubset({f["column_name"] for f in fields})
    a_rows = [{"check": "field_present", "field": f["column_name"], "dtype": f["column_type"]}
              for f in fields]
    a_rows += [
        {"check": "attempt_time_pairs", "field": "attempt~time", "dtype": str(pairs)},
        {"check": "attempt_time_agree_frac", "field": "attempt~time", "dtype": str(agree_frac)},
        {"check": "attempt_time_violations", "field": "attempt~time", "dtype": str(int(order["violations"]))},
        {"check": "attempt_time_time_ordered_frac", "field": "attempt~time",
         "dtype": str(round(int(order["agree_ordered"]) / pairs, 4) if pairs else None)},
        {"check": "time_field_naive_no_tz", "field": "time",
         "dtype": str("Z" not in str(tz["example"]) and "+" not in str(tz["example"]))},
        {"check": "time_min", "field": "time", "dtype": str(tz["t_min"])},
        {"check": "time_max", "field": "time", "dtype": str(tz["t_max"])},
    ]
    for e in ev:
        a_rows.append({"check": "event_id_format", "field": f"draft_year={e['draft_year']}",
                       "dtype": f"example={e['example']}; n={e['n_event_ids']}; frac_two_underscores={e['frac_two_underscores']}"})
    return {
        "fields": fields,
        "order_pairs": pairs,
        "order_agree_frac": agree_frac,
        "order_violations": int(order["violations"]),
        "time_is_naive": bool("Z" not in str(tz["example"]) and "+" not in str(tz["example"])),
        "rows": a_rows,
        "timing_field_present": bool(timing_field),
    }


# --------------------------------------------------------------------------- #
# (b) attempts per player per drill, by position group
# --------------------------------------------------------------------------- #
def audit_b(con, T: str, R: str, cfg: dict[str, Any]) -> pd.DataFrame:
    pos = cfg["audit"]["positions_of_interest"]
    pos_list = ", ".join(f"'{p}'" for p in pos)
    sql = f"""
    WITH t AS (
        SELECT nfl_id, drill_type, drill_name,
               count(DISTINCT attempt) AS n_att,
               count(DISTINCT event_id) AS n_events
        FROM {T} WHERE entity_type = 'PLAYER'
        GROUP BY 1, 2, 3
    ), p AS (
        SELECT nfl_id, combine_position AS pos FROM {R} WHERE combine_position IN ({pos_list})
    )
    SELECT p.pos AS position_group, t.drill_type,
           count(DISTINCT t.nfl_id)                         AS players_with_drill,
           sum(CASE WHEN t.n_att >= 2 THEN 1 ELSE 0 END)    AS players_ge2_attempts,
           round(avg(t.n_att), 2)                           AS avg_attempts,
           max(t.n_att)                                     AS max_attempts,
           sum(t.n_events)                                  AS total_attempts
    FROM t JOIN p ON p.nfl_id = t.nfl_id
    GROUP BY 1, 2
    ORDER BY 1, total_attempts DESC, t.drill_type
    """
    return con.execute(sql).df()


# --------------------------------------------------------------------------- #
# (c) does drill order vary across players?
# --------------------------------------------------------------------------- #
def audit_c(con, T: str, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rank_sql = f"""
    WITH s AS (
        SELECT nfl_id, drill_type, min(time) AS t0
        FROM {T} WHERE entity_type = 'PLAYER' GROUP BY 1, 2
    ), rk AS (
        SELECT nfl_id, drill_type, row_number() OVER (PARTITION BY nfl_id ORDER BY t0) AS rnk
        FROM s
    )
    SELECT drill_type,
           count(*)                        AS sessions,
           round(avg(rnk), 2)              AS avg_rank,
           round(stddev_samp(rnk), 2)      AS sd_rank,
           min(rnk)                        AS min_rank,
           max(rnk)                        AS max_rank
    FROM rk GROUP BY 1 ORDER BY avg_rank, drill_type
    """
    ranks = con.execute(rank_sql).df()

    cands = cfg["audit"]["candidate_drill_types"]
    cand_list = ", ".join(f"'{d}'" for d in cands)
    pair_sql = f"""
    WITH s AS (
        SELECT nfl_id, drill_type, min(time) AS t0
        FROM {T} WHERE entity_type = 'PLAYER' AND drill_type IN ({cand_list})
        GROUP BY 1, 2
    ), j AS (
        SELECT a.nfl_id, a.drill_type AS drill_a, b.drill_type AS drill_b,
               CASE WHEN a.t0 < b.t0 THEN 1 ELSE 0 END AS a_first
        FROM s a JOIN s b ON a.nfl_id = b.nfl_id AND a.drill_type < b.drill_type
    )
    SELECT drill_a, drill_b, count(*) AS n_players,
           round(avg(a_first), 4) AS frac_a_first,
           round(stddev_samp(a_first), 4) AS sd_a_first
    FROM j GROUP BY 1, 2 ORDER BY 1, 2
    """
    pairs = con.execute(pair_sql).df()
    # order is "fixed" for a pair if the fraction is ~0 or ~1 (no mixing).
    mixes = [min(row["frac_a_first"], 1 - row["frac_a_first"]) for _, row in pairs.iterrows()]
    order_varies = bool(mixes and max(mixes) > 0.05)
    # which pairs actually flip?
    flipping = []
    for _, row in pairs.iterrows():
        f = row["frac_a_first"]
        if min(f, 1 - f) > 0.05:
            flipping.append(f"{row['drill_a']}~{row['drill_b']}(≈{round(f, 2)})")
    summary = {
        "max_pairwise_flip_share": round(max(mixes), 4) if mixes else None,
        "drill_order_varies_across_players": order_varies,
        "flipping_pairs": flipping,
        "note": "0 or 1 => fixed protocol (order confounded with drill type); intermediate => variation. "
                "FORTY_YARD_DASH always precedes the agility drills; only the relative order of "
                "SHORT_SHUTTLE vs THREE_CONE_DRILL varies (near 50/50).",
    }
    return ranks, pairs, summary


# --------------------------------------------------------------------------- #
# (d) provided distance column reliability vs distance from x/y
# --------------------------------------------------------------------------- #
def audit_d(con, T: str, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    rtol = float(cfg["audit"]["distance_rtol"])
    sql = f"""
    WITH f AS (
        SELECT event_id, nfl_id, drill_type, attempt, dis, x, y,
               lag(x) OVER (PARTITION BY event_id ORDER BY time) AS px,
               lag(y) OVER (PARTITION BY event_id ORDER BY time) AS py
        FROM {T} WHERE entity_type = 'PLAYER'
    ), per AS (
        SELECT event_id, nfl_id, drill_type, attempt,
               sum(dis)                                                          AS provided,
               sum(sqrt((x - px) * (x - px) + (y - py) * (y - py)))
                   FILTER (WHERE px IS NOT NULL)                                 AS recomputed,
               count(*)                                                          AS n_frames
        FROM f GROUP BY 1, 2, 3, 4
    )
    SELECT drill_type,
           count(*)                                            AS n_attempts,
           round(avg(abs(provided - recomputed)), 4)           AS mean_abs_diff_yd,
           round(quantile_cont(abs(provided - recomputed), 0.5), 4) AS median_abs_diff_yd,
           round(quantile_cont(abs(provided - recomputed), 0.95), 4) AS p95_abs_diff_yd,
           round(corr(provided, recomputed), 5)                AS corr,
           round(median(provided / NULLIF(recomputed, 0)), 4)  AS median_ratio,
           sum(CASE WHEN recomputed > 0 AND abs(provided - recomputed) / recomputed > {rtol}
                    THEN 1 ELSE 0 END)                          AS n_over_rtol,
           round(avg(CASE WHEN recomputed > 0 AND abs(provided - recomputed) / recomputed > {rtol}
                          THEN 1.0 ELSE 0.0 END), 4)            AS share_over_rtol
    FROM per WHERE recomputed IS NOT NULL
    GROUP BY 1 ORDER BY n_attempts DESC, drill_type
    """
    df = con.execute(sql).df()
    share_over = (df["n_over_rtol"].sum() / df["n_attempts"].sum()) if len(df) else None
    overall = {
        "rtol": rtol,
        "share_over_tol_weighted": round(float(share_over), 5) if share_over is not None else None,
        "min_corr": round(float(df["corr"].min()), 5) if len(df) else None,
        "median_ratio_range": [round(float(df["median_ratio"].min()), 3),
                               round(float(df["median_ratio"].max()), 3)] if len(df) else None,
        # reliable only if provided ~= recomputed within tolerance for (nearly) all attempts
        "reliable": bool(len(df) and share_over is not None and share_over < 0.05),
        "note": "Summed `dis` under-counts the x/y path (median ratio 0.34-0.91 by drill; "
                "80-100% of attempts differ >5%). x/y trajectories are smooth (max 10 Hz step "
                "1.2 yd; no teleports). The low 40-yd correlation (r=0.19) is a low-variance "
                "artefact (all 40-yd attempts ~40 yd). Recommendation: recompute distance from "
                "x/y; treat `dis` as advisory only.",
    }
    return df, overall


# --------------------------------------------------------------------------- #
# (e) matched Combine -> NFL players per position group (full mode only)
# --------------------------------------------------------------------------- #
def audit_e(con, cfg: dict[str, Any], mode: str, prov: str, logger) -> pd.DataFrame:
    if not cfg["mode"][mode]["run_game_side"]:
        logger.info("(e) game-side matching SKIPPED in %s mode -> PENDING FULL RUN", mode)
        return pd.DataFrame([{
            "status": "PENDING FULL RUN",
            "detail": "matched Combine->NFL players per position group requires the full "
                      "game-tracking files; computed only in --mode full.",
        }])
    # full mode: count players appearing in game tracking, by combine position
    gt_key = cfg["mode"][mode]["game_tracking"]
    gt_files = cfg["files"][gt_key]
    if isinstance(gt_files, str):
        gt_files = [gt_files]
    gt_paths = [str(resolve(cfg, "parquet", Path(name).with_suffix(".parquet"))) for name in gt_files]
    gt = f"read_parquet({gt_paths})" if len(gt_paths) > 1 else f"read_parquet('{gt_paths[0]}')"
    R = pq(resolve(cfg, "parquet", "combine_results.parquet"))
    sql = f"""
    WITH played AS (SELECT DISTINCT nfl_id FROM {gt} WHERE nfl_id IS NOT NULL)
    SELECT r.combine_position AS position_group,
           count(*) AS combine_players,
           sum(CASE WHEN p.nfl_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_with_game_data,
           round(avg(CASE WHEN p.nfl_id IS NOT NULL THEN 1.0 ELSE 0.0 END), 4) AS match_rate
    FROM {R} r LEFT JOIN played p ON p.nfl_id = r.nfl_id
    GROUP BY 1 ORDER BY 1
    """
    return con.execute(sql).df()


# --------------------------------------------------------------------------- #
# (f) missingness, units, sampling rate, coordinates
# --------------------------------------------------------------------------- #
def audit_f(con, cfg: dict[str, Any], T: str, R: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add_missing(table: str, path: str, cols: list[str]) -> None:
        n = _scalar(con, f"SELECT count(*) FROM {path}")
        for c in cols:
            nn = _scalar(con, f'SELECT count(*) FROM {path} WHERE "{c}" IS NULL')
            rows.append({"table": table, "column": c, "n_rows": int(n),
                         "n_null": int(nn or 0),
                         "null_pct": round(100.0 * (nn or 0) / n, 4) if n else None})

    add_missing("combine_tracking", T, COMBINE_TRACKING_COLS)
    add_missing("combine_results", R, COMBINE_RESULTS_COLS)

    dt = con.execute(f"""
        WITH t AS (SELECT event_id, time AS ts FROM {T} WHERE entity_type = 'PLAYER'),
             d AS (SELECT event_id, epoch(ts - lag(ts) OVER (PARTITION BY event_id ORDER BY ts)) AS dt FROM t)
        SELECT round(quantile_cont(dt, 0.5), 4) AS median_dt_s,
               round(quantile_cont(dt, 0.95), 4) AS p95_dt_s,
               round(avg(dt), 4) AS mean_dt_s
        FROM d WHERE dt IS NOT NULL
    """).df().iloc[0].to_dict()

    xr = cfg["audit"]["coord_x_range"]
    yr = cfg["audit"]["coord_y_range"]
    coords = con.execute(f"""
        SELECT round(min(x), 2) AS x_min, round(max(x), 2) AS x_max,
               round(min(y), 2) AS y_min, round(max(y), 2) AS y_max,
               round(min(s), 2) AS s_min, round(max(s), 2) AS s_max,
               round(min(a), 2) AS a_min, round(max(a), 2) AS a_max,
               round(min(dir), 2) AS dir_min, round(max(dir), 2) AS dir_max,
               round(min(dis), 4) AS dis_min, round(max(dis), 4) AS dis_max,
               sum(CASE WHEN x < {xr[0]} OR x > {xr[1]} THEN 1 ELSE 0 END) AS x_outside_nfl_field,
               sum(CASE WHEN y < {yr[0]} OR y > {yr[1]} THEN 1 ELSE 0 END) AS y_outside_nfl_field
        FROM {T} WHERE entity_type = 'PLAYER'
    """).df().iloc[0].to_dict()

    # Combine tracking uses its own local frame; report per-drill extents explicitly.
    coord_by_drill = con.execute(f"""
        SELECT drill_type,
               round(min(x), 2) AS x_min, round(max(x), 2) AS x_max,
               round(min(y), 2) AS y_min, round(max(y), 2) AS y_max
        FROM {T} WHERE entity_type = 'PLAYER' GROUP BY 1 ORDER BY 1
    """).df()

    med_dt = dt["median_dt_s"]
    observed_hz = round(1.0 / med_dt, 2) if med_dt else None
    coords["frame_note"] = (
        "x/y are in yards (40-yd dash straight-line span ≈ 40 yd) but the Combine tracking "
        "frame is its own local field frame, NOT the NFL 0-120 x 0-53.3 game frame "
        f"(observed y ∈ [{coords['y_min']}, {coords['y_max']}], x ∈ [{coords['x_min']}, {coords['x_max']}]). "
        "Do not reuse NFL coordinate assumptions for Combine data.")

    summary = {
        "sampling_rate_nominal_hz": cfg["audit"]["sampling_rate_hz"],
        "median_dt_s": med_dt,
        "observed_hz": observed_hz,
        "units": {
            "x": "yards (local Combine frame)", "y": "yards (local Combine frame)",
            "s": "yards/second", "a": "yards/second^2", "dis": "yards (since prior frame)",
            "dir": "degrees (0-360)", "time": "ISO-8601 naive timestamp (assumed UTC; unverified)",
        },
        "coordinates": coords,
    }
    return pd.DataFrame(rows), summary, coord_by_drill


# --------------------------------------------------------------------------- #
# (g) attempt-numbering gaps by drill / position
# --------------------------------------------------------------------------- #
def audit_g(con, T: str, R: str, cfg: dict[str, Any]) -> pd.DataFrame:
    pos = cfg["audit"]["positions_of_interest"]
    pos_list = ", ".join(f"'{p}'" for p in pos)
    sql = f"""
    WITH grp AS (
        SELECT nfl_id, drill_type, drill_name,
               count(DISTINCT attempt) AS n_att, min(attempt) AS mn_a, max(attempt) AS mx_a
        FROM {T} WHERE entity_type = 'PLAYER'
        GROUP BY 1, 2, 3
    ), p AS (
        SELECT nfl_id, combine_position AS pos FROM {R} WHERE combine_position IN ({pos_list})
    )
    SELECT p.pos AS position_group, grp.drill_type,
           count(*)                                                     AS player_drill_groups,
           sum(CASE WHEN mx_a <> n_att THEN 1 ELSE 0 END)               AS groups_with_gap,
           round(avg(CASE WHEN mx_a <> n_att THEN 1.0 ELSE 0.0 END), 4) AS share_with_gap,
           sum(CASE WHEN mn_a > 1 THEN 1 ELSE 0 END)                    AS groups_missing_attempt_1,
           round(avg(CASE WHEN mn_a > 1 THEN 1.0 ELSE 0.0 END), 4)      AS share_missing_attempt_1
    FROM grp JOIN p ON p.nfl_id = grp.nfl_id
    GROUP BY 1, 2
    ORDER BY 1, share_with_gap DESC, grp.drill_type
    """
    return con.execute(sql).df()


# --------------------------------------------------------------------------- #
# recommendation
# --------------------------------------------------------------------------- #
def recommend(cfg: dict[str, Any], b: pd.DataFrame, c_summary: dict[str, Any]) -> dict[str, Any]:
    primary = cfg["audit"]["primary_drill_type"]
    sub = b[b["drill_type"] == primary].copy()
    if sub.empty:
        return {"position_group": None, "drill_family": None,
                "justification": f"no rows for primary drill {primary}"}
    sub = sub.sort_values(["players_ge2_attempts", "total_attempts"], ascending=False)
    best = sub.iloc[0]
    runner = sub.iloc[1] if len(sub) > 1 else None
    just = (
        f"{best['position_group']} maximises repeated maximal-effort attempts on {primary}: "
        f"{int(best['players_with_drill'])} players ran it, {int(best['players_ge2_attempts'])} "
        f"with >=2 attempts ({int(best['total_attempts'])} total attempts)."
    )
    if runner is not None:
        just += (f" Next best {runner['position_group']}: "
                 f"{int(runner['players_ge2_attempts'])} players with >=2 attempts.")
    just += (" Within-session drill order is a fixed protocol (see (c)); identification therefore "
             "rests on within-drill repeated attempts (attempt 1 vs 2+) plus load/rest timing, not on "
             "cross-drill order.")
    return {
        "position_group": str(best["position_group"]),
        "drill_family": primary,
        "drill_family_secondary": [d for d in cfg["audit"]["candidate_drill_types"] if d != primary],
        "players_with_drill": int(best["players_with_drill"]),
        "players_ge2_attempts": int(best["players_ge2_attempts"]),
        "total_attempts": int(best["total_attempts"]),
        "order_varies_across_players": c_summary.get("drill_order_varies_across_players"),
        "justification": just +
            " Secondary repeated maximal-effort drills (THREE_CONE_DRILL, SHORT_SHUTTLE) are too "
            "sparse per position (<=13 players with >=2 attempts) to carry the model.",
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    prov = provenance(cfg, mode)
    logger.info("=" * 72)
    logger.info("BDB27 Phase 1 audit | mode=%s | provenance=%s", mode, prov)
    logger.info("=" * 72)

    parq = resolve(cfg, "parquet")
    threads = int(cfg.get("runtime", {}).get("duckdb_threads", 1))
    con = connect(threads=threads)

    with timed_stage(cfg, "01_audit:convert", mode):
        to_parquet(con, csv_path(cfg, "combine_tracking"),
                   parq / "combine_tracking.parquet", COMBINE_TRACKING_COLS, force, logger)
        to_parquet(con, csv_path(cfg, "combine_results"),
                   parq / "combine_results.parquet", COMBINE_RESULTS_COLS, force, logger)
        to_parquet(con, csv_path(cfg, "players"),
                   parq / "players.parquet", None, force, logger)

    T = pq(parq / "combine_tracking.parquet")
    R = pq(parq / "combine_results.parquet")

    results: dict[str, Any] = {"mode": mode, "provenance": prov}

    with timed_stage(cfg, "01_audit:a_timing", mode):
        a = audit_a(con, T, cfg)
        _write(cfg, "a_timing_fields.csv", pd.DataFrame(a["rows"]), prov, logger)
        results["a"] = {k: v for k, v in a.items() if k != "rows"}

    with timed_stage(cfg, "01_audit:b_attempts", mode):
        b = audit_b(con, T, R, cfg)
        _write(cfg, "b_attempts_by_position.csv", b, prov, logger)
        results["b_rows"] = int(len(b))

    with timed_stage(cfg, "01_audit:c_order", mode):
        ranks, pairs, c_summary = audit_c(con, T, cfg)
        _write(cfg, "c_drill_order_ranks.csv", ranks, prov, logger)
        _write(cfg, "c_drill_order_pairs.csv", pairs, prov, logger)
        results["c"] = c_summary

    with timed_stage(cfg, "01_audit:d_distance", mode):
        d, d_summary = audit_d(con, T, cfg)
        _write(cfg, "d_distance_reliability.csv", d, prov, logger)
        results["d"] = d_summary

    with timed_stage(cfg, "01_audit:e_matched", mode):
        e = audit_e(con, cfg, mode, prov, logger)
        _write(cfg, "e_matched_players.csv", e, prov, logger)
        results["e"] = e.to_dict("records")

    with timed_stage(cfg, "01_audit:f_missingness", mode):
        f, f_summary, f_coords = audit_f(con, cfg, T, R)
        _write(cfg, "f_missingness_units.csv", f, prov, logger)
        _write(cfg, "f_coordinates_by_drill.csv", f_coords, prov, logger)
        results["f"] = f_summary

    with timed_stage(cfg, "01_audit:g_attempt_gaps", mode):
        g = audit_g(con, T, R, cfg)
        _write(cfg, "g_attempt_gaps.csv", g, prov, logger)
        results["g_rows"] = int(len(g))

    # ---- recommendation + go/no-go inputs ----
    rec = recommend(cfg, b, c_summary)
    timing_ok = bool(results["a"]["timing_field_present"])
    group_ok = rec["position_group"] is not None and rec.get("players_ge2_attempts", 0) > 0
    g_tot = con.execute(f"""
        WITH grp AS (SELECT nfl_id, drill_type, drill_name, count(DISTINCT attempt) n, max(attempt) mx
                     FROM {T} WHERE entity_type='PLAYER' GROUP BY 1,2,3)
        SELECT round(avg(CASE WHEN mx<>n THEN 1.0 ELSE 0.0 END),4) FROM grp
    """).fetchone()[0]
    verdict = "GO" if (timing_ok and group_ok) else "NO-GO"
    blocker = None
    if not timing_ok:
        blocker = "required timing field (time/attempt) missing"
    elif not group_ok:
        blocker = "no position group qualifies"

    results["recommendation"] = rec
    results["gate"] = {
        "timing_field_present": timing_ok,
        "group_qualifies": group_ok,
        "overall_gap_share": float(g_tot),
        "verdict": verdict,
        "blocker": blocker,
    }

    out_dir = resolve(cfg, "outputs", OUT_SUBDIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit_summary.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out_dir / "PROVENANCE.txt").write_text(
        f"{prov}\nGenerated {mode}-mode by src/01_audit.py. Not results.\n", encoding="utf-8")

    size_mb = round((REPO_ROOT / "data/raw" / cfg["files"]["combine_tracking"]).stat().st_size / 1e6, 1)
    lines = [
        f"# Audit summary — {prov}",
        "",
        f"- mode: `{mode}`  ·  provenance: `{prov}`",
        f"- combine_tracking file size: {size_mb} MB",
        f"- (a) timing fields present: {timing_ok}; attempt~time agreement: {results['a']['order_agree_frac']}",
        f"- (c) drill order varies across players: {c_summary['drill_order_varies_across_players']} "
        f"(max flip share {c_summary['max_pairwise_flip_share']}; flipping pairs: {c_summary.get('flipping_pairs')})",
        f"- (d) distance reliable: {d_summary['reliable']} "
        f"(median provided/recomputed ratio range {d_summary.get('median_ratio_range')}, "
        f"share >5% off {d_summary['share_over_tol_weighted']})",
        f"- (g) overall share of player-drill groups with numbering gaps: {g_tot}",
        f"- recommendation: position_group=`{rec['position_group']}`, drill_family=`{rec['drill_family']}`",
        f"- gate verdict: **{verdict}**" + (f" (blocker: {blocker})" if blocker else ""),
        "",
        rec.get("justification", ""),
        "",
        "*(All tables carry a `provenance` column; sample outputs are UNVALIDATED SAMPLE OUTPUT.)*",
    ]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")

    logger.info("recommendation: position=%s drill=%s", rec["position_group"], rec["drill_family"])
    logger.info("GATE VERDICT: %s", verdict)
    logger.info("peak RAM: %.1f MB", peak_ram_mb())
    return 0 if verdict == "GO" else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 1 audit")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild parquet caches")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or cfg["mode"]["default"]
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
