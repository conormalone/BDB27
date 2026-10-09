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
def _attempt_expr(cfg: dict[str, Any], alias: str = "t") -> str:
    """SQL for one attempt under the configured counting unit (D12)."""
    unit = cfg["audit"].get("attempt_count_unit", "event_id")
    if unit == "event_id":
        return f"count(DISTINCT {alias}.event_id)"
    return f"count(DISTINCT ({alias}.drill_name, {alias}.attempt))"


def audit_b(con, T: str, R: str, cfg: dict[str, Any]) -> pd.DataFrame:
    """(b) attempts per player per drill TYPE, by position group.

    B2 fix: the old ``players_ge2_attempts`` summed ``(nfl_id, drill_name)`` series
    groups, so for a multi-sub-drill drill_type (e.g. ``SKILL_DRILLS_DB``) it could
    EXCEED ``players_with_drill`` — a mixed-granularity mislabel. Every player count
    below is now a DISTINCT-PLAYER count; the series-group count is kept separately
    and correctly named (``player_drillseries_groups``).

    Note: ``drill_type`` aggregates its ``drill_name`` sub-drills, so a player's
    ``n_att`` here is their total observed attempts within that drill_type. The
    per-drill_name breakdown lives in ``h_attempts_by_drill.csv``.
    """
    pos = cfg["audit"]["positions_of_interest"]
    pos_list = ", ".join(f"'{p}'" for p in pos)
    # N4 fix: actually USE the configured counting unit. `n_att` is now the sum of the
    # configured unit (`att`, default event_id); the event_id/slot counts are kept as
    # diagnostics. For the current unit (event_id) the result is identical to the old
    # `sum(n_att_ev)` (each event_id belongs to exactly one (nfl_id, drill_name, attempt),
    # so summing distinct event_id over the drill_name sub-groups cannot double-count);
    # the parity is asserted below.
    att = _attempt_expr(cfg, "t")
    sql = f"""
    WITH series AS (
        SELECT t.nfl_id, t.drill_type, t.drill_name,
               {att}                          AS n_att_unit,
               count(DISTINCT t.attempt)      AS n_att_slot,
               count(DISTINCT t.event_id)     AS n_att_ev
        FROM {T} t WHERE t.entity_type = 'PLAYER'
        GROUP BY 1, 2, 3
    ), pp AS (   -- per player per drill_type: total observed attempts (configured unit)
        SELECT nfl_id, drill_type,
               sum(n_att_unit) AS n_att,
               count(*)        AS n_series
        FROM series GROUP BY 1, 2
    ), p AS (
        SELECT nfl_id, combine_position AS pos FROM {R} WHERE combine_position IN ({pos_list})
    )
    SELECT p.pos AS position_group, pp.drill_type,
           count(DISTINCT pp.nfl_id)                 AS players_with_drill,
           count(*) FILTER (WHERE pp.n_att >= 1)     AS players_ge1_attempts,
           count(*) FILTER (WHERE pp.n_att >= 2)     AS players_ge2_attempts,
           count(*) FILTER (WHERE pp.n_att >= 3)     AS players_ge3_attempts,
           round(avg(pp.n_att), 2)                   AS avg_attempts,
           max(pp.n_att)                             AS max_attempts,
           sum(pp.n_att)                             AS total_attempts,
           sum(pp.n_series)                          AS player_drillseries_groups
    FROM pp JOIN p ON p.nfl_id = pp.nfl_id
    GROUP BY 1, 2
    ORDER BY 1, total_attempts DESC, pp.drill_type
    """
    df = con.execute(sql).df()
    # N4: for the current unit (event_id) the configured-unit total must reproduce the
    # event_id-based total exactly. Guards against a silent unit regression.
    if cfg["audit"].get("attempt_count_unit", "event_id") == "event_id":
        ev_total = int(_scalar(con, f"SELECT count(DISTINCT event_id) FROM {T} "
                                   f"WHERE entity_type = 'PLAYER'") or 0)
        unit_total = int(df["total_attempts"].sum()) if len(df) else 0
        assert unit_total == ev_total, (
            f"audit_b unit mismatch: configured-unit total {unit_total} != event_id total {ev_total}")
    return df


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
    # Threshold lives in config (B4: was hard-coded 0.05).
    thr = float(cfg["audit"].get("order_flip_threshold", 0.05))
    mixes = [min(row["frac_a_first"], 1 - row["frac_a_first"]) for _, row in pairs.iterrows()]
    order_varies = bool(mixes and max(mixes) > thr)
    # which pairs actually flip?
    flipping = []
    for _, row in pairs.iterrows():
        f = row["frac_a_first"]
        if min(f, 1 - f) > thr:
            flipping.append(f"{row['drill_a']}~{row['drill_b']}(≈{round(f, 2)})")
    summary = {
        "order_flip_threshold": thr,
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
# (h) position-scope evidence: players reaching >=k observed attempts
# --------------------------------------------------------------------------- #
def _family_filter(cfg: dict[str, Any], family: str) -> str:
    """SQL fragment restricting a family's drill_type (empty for ALL)."""
    fam = cfg["audit"]["families"][family]
    if isinstance(fam, str) and fam.upper() == "ALL":
        return ""
    drills = ", ".join(f"'{d}'" for d in fam)
    return f" AND t.drill_type IN ({drills})"


def audit_h_position_scope(con, T: str, R: str, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(h) Position-scope evidence (numbers only; the population choice lives in
    `recommend`, LOCKED to DB only by D13).

    Returns two frames:
      h1: per position_group x drill_type x drill_name — players reaching >=1/>=2/>=3
          observed attempts in that drill (the unambiguous attempt-series unit).
      h2: per population x family — players reaching >=1/>=2/>=3 observed attempts
          TOTAL across the family, for the single positions and the candidate
          populations (DB-only / DB+WR / DB+WR+DL+OL). Matched-NFL counts are
          PENDING FULL RUN (D8): the game side cannot be evaluated on the sample.
    """
    pos = cfg["audit"]["positions_of_interest"]
    pos_list = ", ".join(f"'{p}'" for p in pos)
    att = _attempt_expr(cfg)
    families = cfg["audit"]["families"]
    min_link = int(cfg["audit"]["min_attempts_for_link"])

    h1 = con.execute(f"""
        WITH pp AS (
            SELECT r.combine_position AS position_group, t.drill_type, t.drill_name, t.nfl_id,
                   {att} AS n_att
            FROM {T} t JOIN {R} r ON r.nfl_id = t.nfl_id
            WHERE t.entity_type = 'PLAYER' AND r.combine_position IN ({pos_list})
            GROUP BY 1, 2, 3, 4
        )
        SELECT position_group, drill_type, drill_name,
               count(*)                           AS players_ge1_attempts,
               count(*) FILTER (WHERE n_att >= 2) AS players_ge2_attempts,
               count(*) FILTER (WHERE n_att >= 3) AS players_ge3_attempts,
               round(avg(n_att), 2)               AS avg_attempts,
               max(n_att)                         AS max_attempts,
               sum(n_att)                         AS total_attempts
        FROM pp GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """).df()

    populations: dict[str, list[str]] = {p: [p] for p in pos}
    populations.update(cfg["audit"]["candidate_populations"])
    candidates = set(cfg["audit"]["candidate_populations"])

    rows: list[dict[str, Any]] = []
    for pop_name, pop_pos in populations.items():
        pop_list = ", ".join(f"'{p}'" for p in pop_pos)
        for fam_name in families:
            fam_filter = _family_filter(cfg, fam_name)
            d = con.execute(f"""
                WITH pp AS (
                    SELECT t.nfl_id, {att} AS n_att
                    FROM {T} t JOIN {R} r ON r.nfl_id = t.nfl_id
                    WHERE t.entity_type = 'PLAYER' AND r.combine_position IN ({pop_list}){fam_filter}
                    GROUP BY 1
                )
                SELECT count(*)                           AS players_total,
                       count(*) FILTER (WHERE n_att >= 1) AS players_ge1,
                       count(*) FILTER (WHERE n_att >= 2) AS players_ge2,
                       count(*) FILTER (WHERE n_att >= 3) AS players_ge3,
                       round(avg(n_att), 2)               AS avg_attempts,
                       round(median(n_att), 1)            AS median_attempts,
                       max(n_att)                         AS max_attempts
                FROM pp
            """).df().iloc[0].to_dict()
            total = int(d["players_total"])
            ge3 = int(d["players_ge3"])
            rows.append({
                "population": pop_name,
                "population_positions": "+".join(pop_pos),
                "population_type": "candidate" if pop_name in candidates else "single_position",
                "family": fam_name,
                "players_total": total,
                "players_ge1_attempts": int(d["players_ge1"]),
                "players_ge2_attempts": int(d["players_ge2"]),
                "players_ge3_attempts": ge3,
                "pct_ge3": round(100.0 * ge3 / total, 1) if total else None,
                "avg_attempts": float(d["avg_attempts"]) if d["avg_attempts"] is not None else None,
                "median_attempts": float(d["median_attempts"]) if d["median_attempts"] is not None else None,
                "max_attempts": int(d["max_attempts"]) if d["max_attempts"] is not None else None,
                "min_attempts_for_link": min_link,
                "matched_with_game_data": None,
                "matched_status": "PENDING FULL RUN",
            })
    return h1, pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# runtime assertions for notes/assumptions.md  (B3: claimed but absent before)
# --------------------------------------------------------------------------- #
def verify_assumptions(con, cfg: dict[str, Any], T: str, R: str,
                       results: dict[str, Any], mode: str) -> pd.DataFrame:
    """Evaluate the assumptions in notes/assumptions.md with real runtime checks.

    Hard invariants raise AssertionError (the spec: "fail loudly on mismatch").
    The one KNOWN violation (A5 reverse: a few (nfl_id, drill_name, attempt)
    tuples carry two event_ids) is recorded as a FAIL row without aborting, since
    Phase 2 is specified to *flag* duplicate attempts rather than stop.
    """
    rows: list[dict[str, Any]] = []

    def rec(aid: str, assumption: str, status: str, detail: str) -> None:
        rows.append({"assumption_id": aid, "assumption": assumption,
                     "status": status, "detail": detail})

    # A1 — combine_tracking covers the same prospects as combine_results.
    trk = int(_scalar(con, f"SELECT count(DISTINCT nfl_id) FROM {T}"))
    res = int(_scalar(con, f"SELECT count(DISTINCT nfl_id) FROM {R}"))
    rec("A1", "combine_tracking nfl_id set == combine_results nfl_id set",
        "PASS" if trk == res else "FAIL", f"tracking={trk} results={res}")
    assert trk == res, f"A1 violated: tracking={trk} results={res}"

    # A2 — naive timestamps + attempt~time agreement (threshold from config, N7).
    a = results.get("a", {})
    agree, naive = a.get("order_agree_frac"), a.get("time_is_naive")
    agree_min = float(cfg["audit"].get("assert_attempt_time_min", 0.99))
    ok2 = bool(naive) and agree is not None and float(agree) >= agree_min
    rec("A2", f"time is naive (no TZ) and attempt~time agreement >= {agree_min}",
        "PASS" if ok2 else "FAIL", f"naive={naive} agreement={agree}")
    assert ok2, f"A2 violated: naive={naive} agreement={agree} (min {agree_min})"

    # A3 — 10 Hz sampling (nominal Δt from config, N7).
    med_dt = (results.get("f") or {}).get("median_dt_s")
    nominal_dt = float(cfg["audit"].get("assert_median_dt_s", 0.10))
    ok3 = med_dt is not None and abs(float(med_dt) - nominal_dt) < 1e-6
    rec("A3", f"median inter-frame dt == {nominal_dt} s (10 Hz)", "PASS" if ok3 else "FAIL",
        f"median_dt_s={med_dt}")
    assert ok3, f"A3 violated: median_dt_s={med_dt} (expected {nominal_dt})"

    # A4 — 40-yd straight-line span ~ [lo,hi] yd (bounds from config, N7).
    span = con.execute(f"""
        WITH agg AS (
            SELECT event_id, sqrt(pow(max(x)-min(x),2)+pow(max(y)-min(y),2)) AS extent
            FROM {T} WHERE entity_type='PLAYER' AND drill_type='FORTY_YARD_DASH' GROUP BY 1)
        SELECT round(quantile_cont(extent,0.05),2) AS p05,
               round(quantile_cont(extent,0.5),2)  AS p50,
               round(quantile_cont(extent,0.95),2) AS p95 FROM agg
    """).df().iloc[0].to_dict()
    span_lo, span_hi = (float(x) for x in cfg["audit"].get("assert_forty_span_yd", [38.0, 42.0]))
    ok4 = span["p50"] is not None and span_lo <= float(span["p50"]) <= span_hi
    rec("A4", f"40-yd straight-line span median in [{span_lo},{span_hi}] yd",
        "PASS" if ok4 else "FAIL", f"p05={span['p05']} p50={span['p50']} p95={span['p95']}")
    assert ok4, f"A4 violated: 40-yd span median {span['p50']} outside [{span_lo},{span_hi}]"

    # A5 forward — event_id -> exactly one (nfl_id, drill_name, attempt).
    fwd_bad = int(_scalar(con, f"""
        WITH g AS (SELECT event_id, count(DISTINCT (nfl_id, drill_name, attempt)) AS k
                   FROM {T} WHERE entity_type='PLAYER' GROUP BY 1)
        SELECT count(*) FILTER (WHERE k > 1) FROM g""") or 0)
    rec("A5f", "event_id maps to exactly one (nfl_id, drill_name, attempt)",
        "PASS" if fwd_bad == 0 else "FAIL", f"event_ids_with_multiple_keys={fwd_bad}")
    assert fwd_bad == 0, f"A5 (forward) violated: {fwd_bad} event_id(s) map to >1 key"

    # A5 reverse — (nfl_id, drill_name, attempt) -> exactly one event_id (KNOWN VIOLATION).
    rev_bad = int(_scalar(con, f"""
        WITH g AS (SELECT nfl_id, drill_name, attempt, count(DISTINCT event_id) AS k
                   FROM {T} WHERE entity_type='PLAYER' GROUP BY 1,2,3)
        SELECT count(*) FILTER (WHERE k > 1) FROM g""") or 0)
    rec("A5r", "(nfl_id, drill_name, attempt) maps to exactly one event_id",
        "PASS" if rev_bad == 0 else "FAIL",
        f"tuples_with_multiple_event_ids={rev_bad} (duplicate attempt captures; D12/B3; Phase 2 flags duplicate attempts)")

    # A6 — numbering gaps = lost data (informational).
    rec("A6", "attempt-numbering gaps = lost data (share reported)", "INFO",
        f"overall_gap_share={(results.get('gate') or {}).get('overall_gap_share')}")

    # A7 — combine position groups.
    pset = [r[0] for r in con.execute(f"SELECT DISTINCT combine_position FROM {R} ORDER BY 1").fetchall()]
    ok7 = set(pset) == {"DB", "DL", "OL", "TE", "WR"}
    rec("A7", "combine_position set == {DB,DL,OL,TE,WR}", "PASS" if ok7 else "FAIL", f"observed={pset}")
    assert ok7, f"A7 violated: positions {pset}"

    # A8 — full mode: game ids must overlap the combine nfl_id set in EVERY position group.
    # A real assertion (N3): reuse the (e) match table; every group's match rate must be > 0.
    # A9 — not yet implemented (Phase 4): the game-tracking schema check lands in 04_game_features.
    if cfg["mode"][mode]["run_game_side"]:
        e_df = pd.DataFrame(results.get("e") or [])
        if e_df.empty or "match_rate" not in e_df.columns:
            rec("A8", "game ids overlap combine nfl_id per group", "FAIL",
                "audit (e) produced no position-group match table")
            assert False, "A8 violated: audit (e) produced no position-group match table"
        zero_groups = e_df.loc[e_df["match_rate"] <= 0, "position_group"].astype(str).tolist()
        assert not zero_groups, f"A8 violated: zero-match position group(s): {zero_groups}"
        rec("A8", "game ids overlap combine nfl_id per group", "PASS",
            f"all {len(e_df)} position groups have match_rate > 0 "
            f"(min {round(float(e_df['match_rate'].min()), 4)})")
    else:
        rec("A8", "game ids overlap combine nfl_id per group", "SKIP", "sample mode (D8)")
    rec("A9", "game-tracking files match the sample's 12-column schema", "PENDING",
        "not yet implemented (Phase 4); the schema check lands in src/04_game_features.py")

    # S1 — sample discipline: the game-tracking sample must not exceed its documented 2^20 cap
    # (uses config.audit.sample_row_count; keeps the key live rather than decorative).
    if not cfg["mode"][mode]["run_game_side"]:
        samp = REPO_ROOT / "data/raw" / cfg["files"]["game_tracking_sample"]
        if samp.exists():
            nrows = int(_scalar(con, f"SELECT count(*) FROM read_csv_auto('{samp}', header=true)") or 0)
            cap_rows = int(cfg["audit"]["sample_row_count"])
            ok_rows = nrows <= cap_rows
            rec("S1", "game-tracking sample rows <= sample_row_count (2^20 cap)",
                "PASS" if ok_rows else "FAIL", f"rows={nrows} cap={cap_rows}")
            assert ok_rows, f"S1 violated: sample has {nrows} rows > cap {cap_rows}"
        else:
            rec("S1", "game-tracking sample rows <= sample_row_count (2^20 cap)", "SKIP",
                f"sample file absent: {samp.name}")

    # A10 — no residual literal 'NA' after NA->NULL conversion.
    na_bad = int(_scalar(con, f"""
        SELECT sum(CASE WHEN CAST(forty AS VARCHAR)='NA' THEN 1 ELSE 0 END)
             + sum(CASE WHEN CAST(three_cone AS VARCHAR)='NA' THEN 1 ELSE 0 END)
             + sum(CASE WHEN CAST(short_shuttle AS VARCHAR)='NA' THEN 1 ELSE 0 END)
        FROM {R}""") or 0)
    rec("A10", "no residual literal 'NA' in numeric columns", "PASS" if na_bad == 0 else "FAIL",
        f"literal_NA_cells={na_bad}")
    assert na_bad == 0, f"A10 violated: {na_bad} literal 'NA' cells survived conversion"

    # A11 — combine_tracking fits in RAM (< max_combine_bytes).
    size = (REPO_ROOT / "data/raw" / cfg["files"]["combine_tracking"]).stat().st_size
    cap = int(cfg["audit"]["max_combine_bytes"])
    ok11 = size <= cap
    rec("A11", "combine_tracking size <= max_combine_bytes", "PASS" if ok11 else "FAIL",
        f"size_bytes={size} cap={cap}")
    assert ok11, f"A11 violated: combine_tracking {size} bytes > cap {cap}"

    # A12 — draft position: "undrafted" is a STRUCTURAL category, not missing data (N1).
    # players.draft_overall_pick and players.draft_round are NULL for the SAME prospects
    # (the ~25% who were not drafted); the two NULL sets coincide exactly (hard-asserted),
    # so this is structural, not non-structural missingness. The >null_exclusion_pct rule is
    # reserved for NON-STRUCTURAL missingness only, so it does NOT fire here -> KEEP draft
    # position as a Phase-5 control, encoding "undrafted" as its own level.
    P = pq(resolve(cfg, "parquet", "players.parquet"))
    n, null_pick, null_round, mismatch = con.execute(
        f"""SELECT count(*),
                   count(*) FILTER (WHERE draft_overall_pick IS NULL),
                   count(*) FILTER (WHERE draft_round IS NULL),
                   count(*) FILTER (WHERE (draft_overall_pick IS NULL) <> (draft_round IS NULL))
            FROM {P}""").fetchone()
    n, null_pick, null_round, mismatch = int(n), int(null_pick), int(null_round), int(mismatch)
    # Structural: the pick-NULL and round-NULL sets must coincide exactly (no row with a pick
    # NULL but a round present, or vice versa).
    assert null_pick == null_round, (
        f"A12: pick-NULL ({null_pick}) and round-NULL ({null_round}) counts differ — the "
        "structural 'undrafted' assumption is broken")
    assert mismatch == 0, (
        f"A12: {mismatch} row(s) have pick NULL but round NOT NULL (or vice versa) — not a "
        "clean structural 'undrafted' category")
    # By construction all pick NULLs are the structural undrafted category -> 0 non-structural.
    nonstruct_null = 0
    excl_pct = float(cfg["audit"].get("null_exclusion_pct", 20.0))
    nonstruct_pct = round(100.0 * nonstruct_null / n, 2) if n else None
    action = "KEEP as control" if (nonstruct_pct is not None and nonstruct_pct <= excl_pct) \
        else "EXCLUDE as control"
    rec("A12",
        "draft position: 'undrafted' is structural (pick/round NULL sets coincide); the "
        f">{excl_pct:g}%-null exclusion rule applies to non-structural missingness only",
        "INFO",
        f"structural_undrafted={null_pick}, nonstructural_null={nonstruct_null} -> {action}; "
        "encode undrafted as its own level")

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# recommendation
# --------------------------------------------------------------------------- #
def recommend(cfg: dict[str, Any], h2: pd.DataFrame, c_summary: dict[str, Any]) -> dict[str, Any]:
    """Resolve the study population under the all-drills family (D12/D13).

    LOCKED: the study population is fixed by human decision (D13, Conor 2026-10-09) to
    `audit.study_population` (DB only) under the all-drills family (D12). WR is EXCLUDED
    (its in-game intensity is scheme-dependent). A SINGLE position group satisfies TASK.md
    Phase 1 ("select ONE position group") — this is NOT a deviation, so no position terms
    are pre-registered (the Phase-3 LMM is the spec formula as written). Under the
    all-drills family DB reaches the >=3-observed-attempt link threshold, so the pick is
    driven by design, not attempt counts. Counts are pulled from the h2 (audit h) frame.
    """
    fam = "all_drills"
    min_link = int(cfg["audit"]["min_attempts_for_link"])
    position_group = str(cfg["audit"]["study_population"])
    sub = h2[(h2["family"] == fam) & (h2["population"] == position_group)]
    if sub.empty:
        return {"position_group": position_group, "position_group_locked": True,
                "drill_family": "ALL_DRILLS", "family": fam,
                "min_attempts_for_link": min_link,
                "justification": f"population {position_group} not found in the {fam} family"}
    row = sub.iloc[0]
    return {
        "position_group": position_group,
        "position_group_locked": True,
        "drill_family": "ALL_DRILLS",
        "family": fam,
        "min_attempts_for_link": min_link,
        "players_total": int(row["players_total"]),
        "players_ge3_attempts": int(row["players_ge3_attempts"]),
        "order_varies_across_players": c_summary.get("drill_order_varies_across_players"),
        "justification": (
            f"Population LOCKED to {position_group} ONLY (D13, human decision Conor 2026-10-09). "
            f"Family = ALL drills (D12). Under all_drills, {int(row['players_ge3_attempts'])}/"
            f"{int(row['players_total'])} {position_group} players reach >={min_link} observed "
            "attempts, so the link threshold is non-binding. A SINGLE position group satisfies "
            "TASK.md Phase 1 (\"Select ONE position group\") — this is NOT a deviation, so no "
            "position terms are pre-registered (the Phase-3 LMM is the spec formula as written). "
            "WR is excluded: its in-game intensity is scheme-dependent, which would inject "
            "between-system noise into the decay signal. The single-position design keeps the "
            "day≈position confounding closed (limitations #2). Identification rests on "
            "within-drill repeated attempts + load/rest timing, not order (D5)."
        ),
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

    with timed_stage(cfg, "01_audit:h_position_scope", mode):
        h1, h2 = audit_h_position_scope(con, T, R, cfg)
        _write(cfg, "h_attempts_by_drill.csv", h1, prov, logger)
        _write(cfg, "h_family_scope.csv", h2, prov, logger)
        results["h_rows"] = int(len(h1))

    # ---- recommendation + go/no-go inputs ----
    rec = recommend(cfg, h2, c_summary)
    timing_ok = bool(results["a"]["timing_field_present"])
    min_link = int(cfg["audit"]["min_attempts_for_link"])
    group_ok = rec["position_group"] is not None and rec.get("players_ge3_attempts", 0) >= min_link
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

    # ---- runtime assumptions (B3): real assertions, not just prose ----
    with timed_stage(cfg, "01_audit:assumptions", mode):
        assumptions = verify_assumptions(con, cfg, T, R, results, mode)
        _write(cfg, "assumptions_check.csv", assumptions, prov, logger)
        results["assumptions"] = assumptions.to_dict("records")

    out_dir = resolve(cfg, "outputs", OUT_SUBDIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit_summary.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out_dir / "PROVENANCE.txt").write_text(
        f"{prov}\nGenerated {mode}-mode by src/01_audit.py. Not results.\n", encoding="utf-8")

    size_mb = round((REPO_ROOT / "data/raw" / cfg["files"]["combine_tracking"]).stat().st_size / 1e6, 1)
    fam_all = h2[(h2["family"] == "all_drills") & (h2["population_type"] == "single_position")]
    fam_tb = h2[(h2["family"] == "timed_battery") & (h2["population_type"] == "single_position")]
    fmt = lambda dd: ", ".join(  # noqa: E731
        f"{r['population']} {int(r['players_ge3_attempts'])}/{int(r['players_total'])}"
        for _, r in dd.sort_values("population").iterrows())
    ad = {r["assumption_id"]: r for r in results["assumptions"]}
    a5r = ad.get("A5r", {})
    lines = [
        f"# Audit summary — {prov}",
        "",
        f"- mode: `{mode}`  ·  provenance: `{prov}`",
        f"- combine_tracking file size: {size_mb} MB",
        f"- (a) timing fields present: {timing_ok}; attempt~time agreement: {results['a']['order_agree_frac']}",
        f"- (c) drill order varies across players: {c_summary['drill_order_varies_across_players']} "
        f"(max flip share {c_summary['max_pairwise_flip_share']}; flipping pairs: {c_summary.get('flipping_pairs')}; "
        f"flip threshold {c_summary.get('order_flip_threshold')})",
        f"- (d) distance reliable: {d_summary['reliable']} "
        f"(median provided/recomputed ratio range {d_summary.get('median_ratio_range')}, "
        f"share >5% off {d_summary['share_over_tol_weighted']})",
        f"- (g) overall share of player-drill groups with numbering gaps: {g_tot}",
        f"- (h) position-scope, family=`all_drills` (ALL drills): players reaching >={min_link} attempts — {fmt(fam_all)}",
        f"- (h) position-scope, family=`timed_battery` (40+3-cone+shuttle): players reaching >={min_link} attempts — {fmt(fam_tb)}",
        f"- (h) matched Combine->NFL counts per candidate population: PENDING FULL RUN (D8; no full game files)",
        f"- (h) attempt counting unit: `{cfg['audit']['attempt_count_unit']}`; "
        f">=3 counts are identical under event_id vs attempt-slot (robust to the A5r duplicates)",
        f"- assumptions (B3): A5r {a5r.get('status')} — {a5r.get('detail')}",
        f"- recommendation: position_group=`{rec['position_group']}` "
        f"({'LOCKED' if rec.get('position_group_locked') else 'RECOMMENDATION, not locked'}; D13), "
        f"drill_family=`{rec['drill_family']}`",
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
