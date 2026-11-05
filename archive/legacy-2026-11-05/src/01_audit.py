#!/usr/bin/env python3
"""01_audit.py — Phase 1 audit (items a-j) and the go/no-go gate.

Every item records ``status in {EVALUATED, PARTIAL, PENDING FULL RUN, BLOCKED}``,
its measured value(s), the query/command that produced them, and the artefact
under ``outputs/audit/lc/``.  Item (h) calls :mod:`ceiling_core` directly, the
same code path script 02 uses, so the audit never depends on 02 having run.

The gate is written to its own path, ``outputs/audit/lc/decision.json``.  It
never touches ``outputs/audit/db_decay/`` or any legacy artefact.

Sample mode cannot answer the effort-level items (g, the load half of i, the
in-game half of h, and the empirical half of j) because the provided tracking
sample has ~1.16 players per play.  Those are marked **PENDING FULL RUN** and
are never guessed.  The verdict in sample mode is therefore at best
``GO_PENDING_FULL_RUN``, never ``GO``.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import lc_common as C
import ceiling_core

STAGE = "01_audit"

CSV_FIELDS = {
    "a_drills.csv": ["position", "drill_name", "drill_type", "n_players",
                     "n_attempts", "median_sinuosity", "straight_line", "status"],
    "b_attempts.csv": ["position", "drill_name", "n_players", "n_one_attempt",
                       "n_two_attempts", "n_three_plus", "median_attempts",
                       "max_attempts", "class_years", "status"],
    "c_matched.csv": ["position", "combine_players", "matched_players",
                      "matched_pct", "games", "plays", "route_snaps",
                      "median_route_snaps_per_player", "frame_level_matched",
                      "status", "note"],
    "d_leverage.csv": ["metric", "value", "n", "status", "note"],
    "e_seasons.csv": ["season", "season_type", "weeks", "games", "min_week",
                      "max_week", "receivers", "status"],
    "f_routes.csv": ["route_ran", "rows", "players", "targets",
                     "pass_length_nonnull", "dropback_nonnull", "status", "note"],
    "g_effort_counts.csv": ["nfl_id", "position", "leverage_bin", "n_efforts",
                            "status", "note"],
    "h_icc.csv": ["feature", "icc", "reliability_mean", "n_players",
                  "n_attempts", "selected", "status", "note"],
    "i_confound.csv": ["metric", "value", "n", "status", "note"],
    "j_power.csv": ["metric", "value", "status", "note"],
}

PENDING_NOTE = ("PENDING FULL RUN: the provided tracking sample has ~1.16 "
                "tracked players per play, so effort-level counts cannot be "
                "estimated from it. No value is guessed.")


# --------------------------------------------------------------------------- #
# SQL helpers
# --------------------------------------------------------------------------- #
def _reader(path: Path) -> str:
    return f"read_csv_auto('{Path(path).as_posix()}', nullstr=['NA'])"


def _q(con, sql: str):
    return con.execute(sql)


def _dicts(con, sql: str) -> list[dict]:
    frame = con.execute(sql).df()
    return frame.to_dict(orient="records")


def _one(con, sql: str):
    row = con.execute(sql).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #
def item_a(con, cfg, mode) -> tuple[list[dict], dict]:
    trk = _reader(C.source_paths(cfg, "combine_tracking")[0])
    players = _reader(C.source_paths(cfg, "players")[0])
    block = C.lc(cfg)["audit"]
    positions = "', '".join(block["positions"])
    straight = {str(d) for d in block["straight_line_drills"]}
    sql = f"""
        WITH f AS (
          SELECT t.nfl_id, t.drill_name, t.drill_type, t.attempt, t.event_id,
                 t.x, t.y, t.time, p.nfl_position
          FROM {trk} t JOIN {players} p ON t.nfl_id = p.nfl_id
          WHERE t.entity_type = 'PLAYER' AND p.nfl_position IN ('{positions}')
        ), steps AS (
          SELECT *,
                 sqrt(pow(x - lag(x) OVER (PARTITION BY nfl_id, drill_name,
                          attempt, event_id ORDER BY time), 2)
                    + pow(y - lag(y) OVER (PARTITION BY nfl_id, drill_name,
                          attempt, event_id ORDER BY time), 2)) AS step
          FROM f
        ), att AS (
          SELECT nfl_id, drill_name, attempt, event_id,
                 sum(step) AS path,
                 sqrt(pow(max(x) - min(x), 2) + pow(max(y) - min(y), 2)) AS disp
          FROM steps GROUP BY 1, 2, 3, 4
        )
        SELECT f.nfl_position AS position, f.drill_name,
               any_value(f.drill_type) AS drill_type,
               count(DISTINCT f.nfl_id) AS n_players,
               count(DISTINCT f.nfl_id || '|' || f.attempt || '|' || f.event_id) AS n_attempts,
               median(a.path / nullif(a.disp, 0)) AS median_sinuosity
        FROM f JOIN att a USING (nfl_id, drill_name, attempt, event_id)
        GROUP BY 1, 2 ORDER BY 2, 1
    """
    rows = []
    for r in _dicts(con, sql):
        sinuosity = float(r["median_sinuosity"] or 0.0)
        is_straight = (r["drill_name"] in straight
                       and sinuosity <= float(block["straight_line_sinuosity_max"]))
        rows.append({**r, "straight_line": bool(is_straight),
                     "status": "EVALUATED"})
    forty = [r for r in rows if r["drill_name"] in straight]
    evidence = {
        "has_straight_line_drill": bool(forty),
        "max_straight_sinuosity": max([r["median_sinuosity"] for r in forty], default=None),
        "n_straight_players": sum(r["n_players"] for r in forty),
    }
    return rows, evidence


def item_b(con, cfg, mode) -> tuple[list[dict], dict]:
    trk = _reader(C.source_paths(cfg, "combine_tracking")[0])
    players = _reader(C.source_paths(cfg, "players")[0])
    positions = "', '".join(C.lc(cfg)["audit"]["positions"])
    sql = f"""
        WITH per AS (
          SELECT p.nfl_position AS position, t.drill_name, t.nfl_id,
                 count(DISTINCT t.attempt || '|' || t.event_id) AS n
          FROM {trk} t JOIN {players} p ON t.nfl_id = p.nfl_id
          WHERE t.entity_type = 'PLAYER' AND p.nfl_position IN ('{positions}')
          GROUP BY 1, 2, 3
        )
        SELECT position, drill_name, count(*) AS n_players,
               count(*) FILTER (WHERE n = 1) AS n_one_attempt,
               count(*) FILTER (WHERE n = 2) AS n_two_attempts,
               count(*) FILTER (WHERE n >= 3) AS n_three_plus,
               median(n) AS median_attempts, max(n) AS max_attempts
        FROM per GROUP BY 1, 2 ORDER BY 2, 1
    """
    rows = _dicts(con, sql)
    years_sql = f"""
        SELECT p.nfl_position AS position,
               string_agg(DISTINCT CAST(t.draft_year AS VARCHAR), '/'
                          ORDER BY CAST(t.draft_year AS VARCHAR)) AS class_years
        FROM {trk} t JOIN {players} p ON t.nfl_id = p.nfl_id
        WHERE t.entity_type = 'PLAYER' AND p.nfl_position IN ('{positions}')
        GROUP BY 1
    """
    years = {r["position"]: r["class_years"] for r in _dicts(con, years_sql)}
    for r in rows:
        r["class_years"] = years.get(r["position"], "")
        r["status"] = "EVALUATED"
    min_attempts = int(C.lc(cfg)["thresholds"]["min_attempts_per_player"])
    evidence = {
        "min_attempts_per_player": min_attempts,
        "n_players_at_or_above_min": sum(
            r["n_players"] for r in rows
            if r["drill_name"] in C.lc(cfg)["audit"]["straight_line_drills"]
            and r["median_attempts"] >= min_attempts),
    }
    return rows, evidence


def item_c(con, cfg, mode) -> tuple[list[dict], dict]:
    players = _reader(C.source_paths(cfg, "players")[0])
    trk = _reader(C.source_paths(cfg, "combine_tracking")[0])
    pp = _reader(C.source_paths(cfg, "player_play")[0])
    positions = "', '".join(C.lc(cfg)["audit"]["positions"])
    sql = f"""
        WITH cp AS (
          SELECT nfl_id, nfl_position AS position FROM {players}
        ), comb AS (
          SELECT DISTINCT nfl_id FROM {trk} WHERE entity_type = 'PLAYER'
        ), route AS (
          SELECT nfl_id, count(*) AS route_snaps,
                 count(DISTINCT game_id) AS games,
                 count(DISTINCT game_id || '|' || play_id) AS plays
          FROM {pp} WHERE route_ran IS NOT NULL GROUP BY 1
        )
        SELECT cp.position, count(*) AS combine_players,
               count(route.nfl_id) AS matched_players,
               coalesce(sum(route.games), 0) AS games,
               coalesce(sum(route.plays), 0) AS plays,
               coalesce(sum(route.route_snaps), 0) AS route_snaps,
               median(route.route_snaps) AS median_route_snaps_per_player
        FROM cp JOIN comb ON cp.nfl_id = comb.nfl_id
        LEFT JOIN route ON cp.nfl_id = route.nfl_id
        WHERE cp.position IN ('{positions}')
        GROUP BY 1 ORDER BY 1
    """
    rows = _dicts(con, sql)
    # The sample tracking file has a different files key; build it explicitly.
    tracking_path = C.tracking_source(cfg, mode)[0]
    tracked_sql = f"""
        SELECT p.nfl_position AS position, count(DISTINCT t.nfl_id) AS n
        FROM {_reader(tracking_path)} t JOIN {players} p ON t.nfl_id = p.nfl_id
        WHERE p.nfl_position IN ('{positions}')
        GROUP BY 1
    """
    tracked = {r["position"]: int(r["n"]) for r in _dicts(con, tracked_sql)}
    frame_level_available = mode == "full"
    if mode == "full":
        files = C.tracking_source(cfg, "full")
        missing = [str(f) for f in files if not f.exists()]
        if missing:
            frame_level_available = False
        else:
            listing = "[" + ", ".join(f"'{f.as_posix()}'" for f in files) + "]"
            full_sql = f"""
                SELECT p.nfl_position AS position, count(DISTINCT t.nfl_id) AS n
                FROM read_csv_auto({listing}, nullstr=['NA'],
                                   union_by_name=true) t
                JOIN {players} p ON t.nfl_id = p.nfl_id
                WHERE p.nfl_position IN ('{positions}')
                GROUP BY 1
            """
            tracked = {r["position"]: int(r["n"]) for r in _dicts(con, full_sql)}
    min_matched = C.lc(cfg)["audit"]["min_matched"]
    for r in rows:
        r["matched_pct"] = (100.0 * r["matched_players"] / r["combine_players"]
                            if r["combine_players"] else 0.0)
        r["frame_level_matched"] = tracked.get(r["position"], 0)
        r["status"] = "EVALUATED" if frame_level_available else "PARTIAL"
        r["note"] = ("Player-level match and route snaps are complete from "
                     "player_play; frame-level match counted on full tracking."
                     if frame_level_available else
                     "Player-level match and route snaps are complete from "
                     "player_play. Frame-level match in the sample tracking is "
                     "non-representative; authoritative count PENDING FULL RUN.")
    evidence = {
        "matched": {r["position"]: int(r["matched_players"]) for r in rows},
        "threshold": dict(min_matched),
        "frame_level_available": frame_level_available,
        "passes": all(int(r["matched_players"]) >= int(min_matched.get(r["position"], 0))
                      for r in rows if r["position"] in min_matched),
    }
    return rows, evidence


def item_d(con, cfg, mode) -> tuple[list[dict], dict]:
    pp = _reader(C.source_paths(cfg, "player_play")[0])
    wp = "pre_snap_home_team_win_probability"
    base = _dicts(con, f"""
        SELECT count(*) AS rows,
               count(DISTINCT game_id || '|' || play_id) AS plays,
               count(DISTINCT CASE WHEN {wp} IS NOT NULL
                     THEN game_id || '|' || play_id END) AS wp_plays,
               count(*) FILTER (WHERE {wp} IS NOT NULL) AS wp_nonnull,
               min({wp}) AS wp_min, max({wp}) AS wp_max,
               count(*) FILTER (WHERE expected_points IS NOT NULL) AS ep_nonnull,
               count(*) FILTER (WHERE quarter IS NULL) AS quarter_null,
               count(*) FILTER (WHERE down IS NULL) AS down_null,
               count(*) FILTER (WHERE yards_to_go IS NULL) AS distance_null,
               count(*) FILTER (WHERE yardline_number IS NULL) AS yardline_null,
               count(*) FILTER (WHERE possession_team IS NULL) AS possession_null,
               count(*) FILTER (WHERE game_clock IS NULL) AS clock_null,
               count(*) FILTER (WHERE pre_snap_home_score IS NULL
                                  OR pre_snap_visitor_score IS NULL) AS score_null
        FROM {pp}
    """)[0]
    spread = _dicts(con, f"""
        WITH p AS (
          SELECT game_id, play_id, max({wp}) - min({wp}) AS spread
          FROM {pp} WHERE {wp} IS NOT NULL GROUP BY 1, 2
        )
        SELECT max(spread) AS max_within_play_spread,
               count(*) FILTER (WHERE spread > 1e-12) AS plays_not_constant,
               count(*) AS plays
        FROM p
    """)[0]
    lev = _dicts(con, f"""
        WITH p AS (
          SELECT game_id, play_id, any_value({wp}) AS wp
          FROM {pp} WHERE {wp} IS NOT NULL GROUP BY 1, 2
        )
        SELECT quantile_cont(4 * wp * (1 - wp), [0.01, 0.25, 0.50, 0.75, 0.99]) AS q
        FROM p
    """)[0]["q"]
    rows = [
        {"metric": "player_play_rows", "value": base["rows"], "n": base["rows"],
         "status": "EVALUATED", "note": ""},
        {"metric": "distinct_plays", "value": base["plays"], "n": base["plays"],
         "status": "EVALUATED", "note": ""},
        {"metric": "win_probability_nonnull", "value": base["wp_nonnull"],
         "n": base["rows"], "status": "EVALUATED",
         "note": "Provided pre-snap WP; no external model needed."},
        {"metric": "plays_with_win_probability", "value": base["wp_plays"],
         "n": base["plays"], "status": "EVALUATED",
         "note": f"coverage {base['wp_plays'] / base['plays']:.4f} of plays"},
        {"metric": "win_probability_min", "value": base["wp_min"],
         "n": base["wp_nonnull"], "status": "EVALUATED", "note": ""},
        {"metric": "win_probability_max", "value": base["wp_max"],
         "n": base["wp_nonnull"], "status": "EVALUATED", "note": ""},
        {"metric": "expected_points_nonnull", "value": base["ep_nonnull"],
         "n": base["rows"], "status": "EVALUATED",
         "note": "Provided expected points; not required by the leverage formula."},
        {"metric": "plays_with_inconsistent_wp", "value": spread["plays_not_constant"],
         "n": spread["plays"], "status": "EVALUATED",
         "note": f"max within-play spread {spread['max_within_play_spread']}"},
        {"metric": "null_quarter", "value": base["quarter_null"],
         "n": base["rows"], "status": "EVALUATED", "note": ""},
        {"metric": "null_down", "value": base["down_null"],
         "n": base["rows"], "status": "EVALUATED", "note": ""},
        {"metric": "null_yards_to_go", "value": base["distance_null"],
         "n": base["rows"], "status": "EVALUATED", "note": ""},
        {"metric": "null_yardline", "value": base["yardline_null"],
         "n": base["rows"], "status": "EVALUATED", "note": ""},
        {"metric": "null_possession", "value": base["possession_null"],
         "n": base["rows"], "status": "EVALUATED", "note": ""},
        {"metric": "null_game_clock", "value": base["clock_null"],
         "n": base["rows"], "status": "EVALUATED", "note": ""},
        {"metric": "null_score", "value": base["score_null"],
         "n": base["rows"], "status": "EVALUATED", "note": ""},
        {"metric": "leverage_q01", "value": lev[0], "n": spread["plays"],
         "status": "EVALUATED", "note": "4*p*(1-p)"},
        {"metric": "leverage_q25", "value": lev[1], "n": spread["plays"],
         "status": "EVALUATED", "note": ""},
        {"metric": "leverage_median", "value": lev[2], "n": spread["plays"],
         "status": "EVALUATED", "note": ""},
        {"metric": "leverage_q75", "value": lev[3], "n": spread["plays"],
         "status": "EVALUATED", "note": ""},
        {"metric": "leverage_q99", "value": lev[4], "n": spread["plays"],
         "status": "EVALUATED", "note": ""},
        {"metric": "timeouts_field", "value": 0, "n": 0, "status": "EVALUATED",
         "note": ("No timeout column exists in any provided file; accepted gap "
                  "because pre-snap WP already embeds timeout state (D59).")},
    ]
    coverage = base["wp_plays"] / base["plays"] if base["plays"] else 0.0
    evidence = {
        "wp_available": base["wp_nonnull"] > 0,
        "wp_coverage_of_plays": coverage,
        "max_within_play_spread": spread["max_within_play_spread"],
        "leverage_derivable_without_external_model": True,
    }
    return rows, evidence


def item_e(con, cfg, mode) -> tuple[list[dict], dict]:
    games = _reader(C.source_paths(cfg, "games")[0])
    pp = _reader(C.source_paths(cfg, "player_play")[0])
    sql = f"""
        WITH recv AS (
          SELECT DISTINCT game_id, nfl_id FROM {pp} WHERE route_ran IS NOT NULL
        )
        SELECT g.season, g.season_type, count(DISTINCT g.week) AS weeks,
               count(DISTINCT g.game_id) AS games,
               min(g.week) AS min_week, max(g.week) AS max_week,
               count(DISTINCT recv.nfl_id) AS receivers
        FROM {games} g LEFT JOIN recv USING (game_id)
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    rows = _dicts(con, sql)
    for r in rows:
        r["status"] = "EVALUATED"
    seasons = sorted({int(r["season"]) for r in rows})
    evidence = {"seasons": seasons,
                "season_types": sorted({r["season_type"] for r in rows}),
                "coverage_years": C.lc(cfg)["audit"]["coverage_years"],
                "covers_all_configured": set(C.lc(cfg)["audit"]["coverage_years"]
                                             ).issubset(set(seasons))}
    return rows, evidence


def item_f(con, cfg, mode) -> tuple[list[dict], dict]:
    pp = _reader(C.source_paths(cfg, "player_play")[0])
    rows = _dicts(con, f"""
        SELECT route_ran, count(*) AS rows, count(DISTINCT nfl_id) AS players,
               count(*) FILTER (WHERE target) AS targets,
               count(*) FILTER (WHERE pass_length IS NOT NULL) AS pass_length_nonnull,
               count(*) FILTER (WHERE dropback_duration IS NOT NULL) AS dropback_nonnull
        FROM {pp} WHERE route_ran IS NOT NULL
        GROUP BY 1 ORDER BY rows DESC, route_ran
    """)
    total = _dicts(con, f"""
        SELECT count(*) AS rows, count(DISTINCT nfl_id) AS players,
               count(*) FILTER (WHERE target) AS targets,
               count(*) FILTER (WHERE pass_length IS NOT NULL) AS pass_length_nonnull,
               count(*) FILTER (WHERE dropback_duration IS NOT NULL) AS dropback_nonnull,
               count(*) FILTER (WHERE NOT target) AS non_targeted
        FROM {pp} WHERE route_ran IS NOT NULL
    """)[0]
    for r in rows:
        r["status"] = "EVALUATED"
        r["note"] = ""
    rows.append({"route_ran": "ALL", **total, "status": "PARTIAL",
                 "note": ("Route type and pass_length are provided. Route depth "
                          "from trajectory, ball location, 22-player completeness "
                          "and time-to-throw beyond dropback_duration are "
                          "PENDING FULL RUN.")})
    evidence = {
        "route_types": len(rows) - 1,
        "targeted": int(total["targets"]),
        "non_targeted": int(total["non_targeted"]),
        "non_targeted_share": (total["non_targeted"] / total["rows"]
                               if total["rows"] else 0.0),
        "pass_length_coverage": (total["pass_length_nonnull"] / total["rows"]
                                 if total["rows"] else 0.0),
    }
    return rows, evidence


def item_g(con, cfg, mode) -> tuple[list[dict], dict]:
    """Effort counts per receiver per leverage bin -- full tracking only."""
    if mode != "full":
        return ([{"nfl_id": "", "position": "", "leverage_bin": "",
                  "n_efforts": "", "status": "PENDING FULL RUN",
                  "note": PENDING_NOTE}], {"status": "PENDING FULL RUN"})
    tracking = C.tracking_source(cfg, "full")
    missing = [str(f) for f in tracking if not f.exists()]
    if missing:
        return ([{"nfl_id": "", "position": "", "leverage_bin": "",
                  "n_efforts": "", "status": "PENDING FULL RUN",
                  "note": f"full tracking missing: {missing}"}],
                {"status": "PENDING FULL RUN"})
    return ([{"nfl_id": "", "position": "", "leverage_bin": "",
              "n_efforts": "", "status": "PENDING FULL RUN",
              "note": ("requires the effort summary that 03_game_efforts "
                       "writes; re-run 01 after 03 on full data")}],
            {"status": "PENDING FULL RUN"})


def item_h(con, cfg, mode) -> tuple[list[dict], dict]:
    result = ceiling_core.compute_ceiling_features(cfg)
    summary = result["summary"]
    rows = []
    for feature, icc in summary["icc"].items():
        rel = [r["reliability"] for r in result["rows"] if r["feature"] == feature]
        rows.append({
            "feature": feature, "icc": icc,
            "reliability_mean": (sum(rel) / len(rel)) if rel else 0.0,
            "n_players": summary["n_players_with_fits"],
            "n_attempts": summary["n_attempts_used"],
            "selected": feature in result["selected"],
            "status": "PARTIAL",
            "note": ("Test-retest ICC from repeat Combine attempts (EVALUATED). "
                     "In-game exceedance rate needs game tracking: PENDING FULL RUN."),
        })
    rows.append({"feature": "in_game_exceedance", "icc": "", "reliability_mean": "",
                 "n_players": "", "n_attempts": "", "selected": False,
                 "status": "PENDING FULL RUN", "note": PENDING_NOTE})
    evidence = {"icc": summary["icc"], "selected": result["selected"],
                "icc_min": summary["icc_min"], "vif": summary["vif"],
                "reliable": summary["reliable"]}
    return rows, evidence


def item_i(con, cfg, mode) -> tuple[list[dict], dict]:
    pp = _reader(C.source_paths(cfg, "player_play")[0])
    wp = "pre_snap_home_team_win_probability"
    sql = f"""
        WITH plays AS (
          SELECT game_id, play_id, any_value({wp}) AS wp,
                 any_value(quarter) AS quarter, any_value(game_clock) AS clock
          FROM {pp} WHERE {wp} IS NOT NULL GROUP BY 1, 2
        ), t AS (
          SELECT *, (quarter - 1) * 900
                    + (900 - (date_part('minute', clock) * 60
                              + date_part('second', clock))) AS elapsed
          FROM plays
        )
        SELECT corr(4 * wp * (1 - wp), elapsed) AS corr_leverage_time,
               count(*) AS n
        FROM t
    """
    overall = _dicts(con, sql)[0]
    by_q = _dicts(con, f"""
        WITH plays AS (
          SELECT game_id, play_id, any_value({wp}) AS wp,
                 any_value(quarter) AS quarter, any_value(game_clock) AS clock
          FROM {pp} WHERE {wp} IS NOT NULL GROUP BY 1, 2
        )
        SELECT quarter,
               corr(4 * wp * (1 - wp),
                    (quarter - 1) * 900
                    + (900 - (date_part('minute', clock) * 60
                              + date_part('second', clock)))) AS corr,
               count(*) AS n
        FROM plays GROUP BY 1 ORDER BY 1
    """)
    rows = [{"metric": "play_level_corr_leverage_time",
             "value": overall["corr_leverage_time"], "n": overall["n"],
             "status": "PARTIAL",
             "note": ("Play-level diagnostic. The gate criterion is residual "
                      "collinearity after load is accounted for, which needs "
                      "effort-level tracking: PENDING FULL RUN.")}]
    for r in by_q:
        rows.append({"metric": f"play_level_corr_leverage_time_q{int(r['quarter'])}",
                     "value": r["corr"], "n": r["n"], "status": "PARTIAL",
                     "note": ""})
    rows.append({"metric": "effort_level_partial_corr_after_load", "value": "",
                 "n": "", "status": "PENDING FULL RUN", "note": PENDING_NOTE})
    threshold = float(C.lc(cfg)["gate"]["max_leverage_time_partial_corr"])
    evidence = {"play_level_corr": overall["corr_leverage_time"],
                "threshold": threshold,
                "effort_level_after_load": "PENDING FULL RUN"}
    return rows, evidence


def item_j(con, cfg, mode) -> tuple[list[dict], dict]:
    from scipy.stats import norm

    power = C.lc(cfg)["power"]
    gate = C.lc(cfg)["gate"]
    alpha = float(power["alpha"])
    target_power = float(power["power"])
    sd = float(power["assumed_within_player_sd_log_speed"])
    n_assumed = int(power["assumed_efforts_per_player"])
    mde_target = float(gate["min_mde"])
    z = float(norm.ppf(1 - alpha / 2) + norm.ppf(target_power))
    mde_at_assumed = z * sd / (n_assumed ** 0.5)
    n_required = int(-(-((z * sd / mde_target) ** 2) // 1))
    rows = [
        {"metric": "assumed_within_player_sd_log_speed", "value": sd,
         "status": "EVALUATED", "note": "config placeholder; not estimated"},
        {"metric": "assumed_efforts_per_player", "value": n_assumed,
         "status": "EVALUATED", "note": "config placeholder"},
        {"metric": "analytic_mde_at_assumed_efforts", "value": mde_at_assumed,
         "status": "PARTIAL", "note": "two-sided alpha, 80% power, leverage z"},
        {"metric": "efforts_per_player_required_for_mde", "value": n_required,
         "status": "PARTIAL",
         "note": f"to detect {mde_target} at the configured SD and power"},
        {"metric": "actual_mde_from_effort_counts", "value": "",
         "status": "PENDING FULL RUN",
         "note": "requires item (g) counts; PENDING FULL RUN."},
    ]
    evidence = {"mde_at_assumed": mde_at_assumed,
                "n_required": n_required, "min_mde": mde_target,
                "power": target_power, "alpha": alpha,
                "sufficient_at_assumption": mde_at_assumed <= mde_target}
    return rows, evidence


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #
def decide(checks: list[dict]) -> str:
    """GO / GO_PENDING_FULL_RUN / NO-GO from per-item verdicts."""
    if any(c["status"] == "BLOCKED" or c["verdict"] == "FAIL" for c in checks):
        return "NO-GO"
    if any(c["verdict"] == "PENDING" for c in checks):
        return "GO_PENDING_FULL_RUN"
    return "GO"


def build_checks(a, b, c, d, e, f, g, h, i, j, cfg) -> list[dict]:
    audit = C.lc(cfg)["audit"]
    gate = C.lc(cfg)["gate"]
    attempts_ok = b["n_players_at_or_above_min"] > 0
    c_pass = bool(c["passes"])
    d_pass = bool(d["wp_available"]) and d["wp_coverage_of_plays"] >= 0.99
    e_pass = bool(e["covers_all_configured"])
    f_pass = f["route_types"] > 0 and f["pass_length_coverage"] > 0.0
    h_pass = bool(h["reliable"]) and h["icc"].get("vmax", 0.0) >= float(gate["min_icc"])
    checks = [
        {"id": "a", "name": "WR/TE Combine drills; straight-line identified",
         "status": "EVALUATED", "verdict": "PASS" if a["has_straight_line_drill"] else "FAIL",
         "value": f"{a['n_straight_players']} players, max sinuosity "
                  f"{a['max_straight_sinuosity']}",
         "threshold": f"sinuosity <= {audit['straight_line_sinuosity_max']}",
         "basis": "a_drills.csv"},
        {"id": "b", "name": "Attempts per player / class years",
         "status": "EVALUATED", "verdict": "PASS" if attempts_ok else "FAIL",
         "value": f"{b['n_players_at_or_above_min']} players with >= "
                  f"{b['min_attempts_per_player']} straight-line attempt",
         "threshold": f">= {b['min_attempts_per_player']}",
         "basis": "b_attempts.csv"},
        {"id": "c", "name": "Matched Combine-to-NFL receivers",
         "status": "EVALUATED" if c.get("frame_level_available") else "PARTIAL",
         "verdict": ("FAIL" if not c_pass else
                     "PASS" if c.get("frame_level_available") else "PENDING"),
         "value": c["matched"], "threshold": c["threshold"],
         "basis": "c_matched.csv",
         "note": ("player-level pass; frame-level match PENDING FULL RUN"
                  if not c.get("frame_level_available") else "")},
        {"id": "d", "name": "Leverage inputs / win probability",
         "status": "EVALUATED", "verdict": "PASS" if d_pass else "FAIL",
         "value": f"WP coverage {d['wp_coverage_of_plays']:.4f} of plays",
         "threshold": ">= 0.99 and derivable without external data",
         "basis": "d_leverage.csv"},
        {"id": "e", "name": "Season / week coverage",
         "status": "EVALUATED", "verdict": "PASS" if e_pass else "FAIL",
         "value": f"seasons {e['seasons']} ({', '.join(e['season_types'])})",
         "threshold": f"all of {e['coverage_years']}",
         "basis": "e_seasons.csv"},
        {"id": "f", "name": "Route type / depth / non-targeted routes",
         "status": "PARTIAL", "verdict": "PASS" if f_pass else "FAIL",
         "value": f"{f['route_types']} route types, "
                  f"{f['non_targeted_share']:.1%} non-targeted",
         "threshold": "route type and depth provided",
         "basis": "f_routes.csv",
         "note": "trajectory depth and 22-player completeness PENDING FULL RUN"},
        {"id": "g", "name": "Efforts per receiver per leverage bin",
         "status": "PENDING FULL RUN", "verdict": "PENDING",
         "value": "not estimable from sample tracking", "threshold": "see gate",
         "basis": "g_effort_counts.csv"},
        {"id": "h", "name": "Combine ceiling reliability (ICC)",
         "status": "PARTIAL",
         "verdict": "PENDING" if h_pass else "FAIL",
         "value": {k: round(v, 3) for k, v in h["icc"].items()},
         "threshold": f"ICC(vmax) >= {gate['min_icc']}",
         "basis": "h_icc.csv",
         "note": "ICC evaluated; in-game exceedance PENDING FULL RUN"},
        {"id": "i", "name": "Leverage vs game time / load",
         "status": "PARTIAL", "verdict": "PENDING",
         "value": f"play-level corr {i['play_level_corr']:.3f}",
         "threshold": f"residual corr <= {i['threshold']}",
         "basis": "i_confound.csv",
         "note": "load separation needs effort-level tracking"},
        {"id": "j", "name": "Power / minimum detectable effect",
         "status": "PENDING FULL RUN", "verdict": "PENDING",
         "value": f"MDE {j['mde_at_assumed']:.4f} at "
                  f"{C.lc(cfg)['power']['assumed_efforts_per_player']} efforts; "
                  f"{j['n_required']} efforts needed for {j['min_mde']}",
         "threshold": f"MDE <= {j['min_mde']}",
         "basis": "j_power.csv",
         "note": "actual MDE requires item (g) counts"},
    ]
    return checks


# --------------------------------------------------------------------------- #
# Summary / run
# --------------------------------------------------------------------------- #
def _summary_markdown(cfg, mode, decision, checks, evidence) -> str:
    stamp = C.provenance_stamp(cfg, mode)
    lines = [
        "# Phase 1 audit — leverage x ceiling (WR primary, TE replication)",
        "",
        f"**PROVENANCE: {stamp}**",
        "",
        "This is a data audit and gate decision, not a result. Sample-mode "
        "numbers describe the supplied files and must never be read as findings.",
        "",
        f"- mode: `{mode}`",
        f"- generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- **verdict: `{decision['verdict']}`**",
        "",
        "## Gate",
        "",
        "| item | check | status | verdict | value |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        lines.append(f"| {c['id']} | {c['name']} | {c['status']} | "
                     f"{c['verdict']} | {c['value']} |")
    lines += ["", "## Pending full run", ""]
    for c in checks:
        if c["verdict"] == "PENDING":
            lines.append(f"- **{c['id']}** {c['name']}: "
                         f"{c.get('note', 'requires full tracking')}")
    lines += ["", "## Evaluated evidence", ""]
    lines.append("```json")
    import json
    lines.append(json.dumps(evidence, indent=2, default=str))
    lines.append("```")
    lines += [
        "",
        "## Why the sample cannot close the gate",
        "",
        "The provided tracking sample is row-capped at 1,048,575 rows and "
        "contains ~1.16 tracked players per play. It cannot produce per-receiver "
        "effort counts, leverage-bin occupancy, matched pairs, or a "
        "leverage/load separation. Those items stay PENDING FULL RUN and no "
        "value is imputed.",
        "",
        "## Artefacts",
        "",
    ]
    for name in CSV_FIELDS:
        lines.append(f"- `{name}`")
    lines += ["- `decision.json`", ""]
    return "\n".join(lines)


def collect(cfg, mode="sample") -> dict:
    paths = C.resolve_paths(cfg, mode)
    con = C.duckdb_connect(cfg)
    try:
        a_rows, a_ev = item_a(con, cfg, mode)
        b_rows, b_ev = item_b(con, cfg, mode)
        c_rows, c_ev = item_c(con, cfg, mode)
        d_rows, d_ev = item_d(con, cfg, mode)
        e_rows, e_ev = item_e(con, cfg, mode)
        f_rows, f_ev = item_f(con, cfg, mode)
        g_rows, g_ev = item_g(con, cfg, mode)
        h_rows, h_ev = item_h(con, cfg, mode)
        i_rows, i_ev = item_i(con, cfg, mode)
        j_rows, j_ev = item_j(con, cfg, mode)
    finally:
        con.close()

    checks = build_checks(a_ev, b_ev, c_ev, d_ev, e_ev, f_ev, g_ev, h_ev,
                          i_ev, j_ev, cfg)
    verdict = decide(checks)
    decision = {
        "stage": STAGE,
        "mode": mode,
        "verdict": verdict,
        "gate_evaluated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checks": checks,
        "pending_full_run": [c["id"] for c in checks if c["verdict"] == "PENDING"],
        "blockers": [c["id"] for c in checks if c["verdict"] == "FAIL"],
        "note": ("GO_PENDING_FULL_RUN is the strongest verdict sample mode can "
                 "support. GO requires items c/g/h/i/j to be evaluated on full "
                 "tracking."),
    }
    return {
        "rows": {"a_drills.csv": a_rows, "b_attempts.csv": b_rows,
                 "c_matched.csv": c_rows, "d_leverage.csv": d_rows,
                 "e_seasons.csv": e_rows, "f_routes.csv": f_rows,
                 "g_effort_counts.csv": g_rows, "h_icc.csv": h_rows,
                 "i_confound.csv": i_rows, "j_power.csv": j_rows},
        "decision": decision,
        "evidence": {"a": a_ev, "b": b_ev, "c": c_ev, "d": d_ev, "e": e_ev,
                     "f": f_ev, "g": g_ev, "h": h_ev, "i": i_ev, "j": j_ev},
        "paths": paths,
    }


def write_outputs(cfg, mode, collected) -> dict:
    paths = collected["paths"]
    out_dir = paths["outputs"] / C.lc(cfg)["audit"]["out_subdir"]
    written = []
    for name, rows in collected["rows"].items():
        path = C.write_csv(out_dir / name, rows, CSV_FIELDS[name],
                           mode=mode, cfg=cfg)
        written.append(str(path))
    decision_path = C.write_json(out_dir / "decision.json",
                                 collected["decision"], mode=mode, cfg=cfg)
    summary = _summary_markdown(cfg, mode, collected["decision"],
                                collected["decision"]["checks"],
                                collected["evidence"])
    summary_path = out_dir / C.lc(cfg)["audit"]["summary_filename"]
    C.write_markdown(summary_path, summary + "\n", mode=mode, cfg=cfg)
    written.append(str(decision_path))
    written.append(str(summary_path))
    return {"out_dir": str(out_dir), "files": written}


def run(cfg, mode="sample", force=False) -> dict:
    if C.is_done(cfg, STAGE, mode, force=force):
        return {"skipped": True}
    C.assert_disk_space(C.resolve_paths(cfg, mode), cfg)
    collected = collect(cfg, mode)
    written = write_outputs(cfg, mode, collected)
    C.mark_done(cfg, STAGE, mode, extra={
        "verdict": collected["decision"]["verdict"],
        "pending": collected["decision"]["pending_full_run"],
    })
    return {"skipped": False, **collected, "written": written}


def main(argv=None) -> int:
    args = C.mode_from_argv(argv)
    cfg = C.load_config(args.config)
    C.seed_everything(int(C.lc(cfg)["seeds"]["base"]))
    try:
        with C.timed_stage(cfg, STAGE, args.mode) as st:
            outcome = run(cfg, args.mode, force=args.force)
            if outcome.get("skipped"):
                st.note("skipped: checkpoint current")
                print("01_audit: up to date (use --force to rebuild)")
                return 0
            decision = outcome["decision"]
            st.note(f"verdict={decision['verdict']} "
                    f"pending={','.join(decision['pending_full_run']) or 'none'}")
            print(f"01_audit: verdict {decision['verdict']}")
            for check in decision["checks"]:
                print(f"  {check['id']} {check['status']:<17} "
                      f"{check['verdict']:<8} {check['value']}")
            print(f"  wrote {outcome['written']['out_dir']}")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
