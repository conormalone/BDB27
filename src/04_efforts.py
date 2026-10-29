#!/usr/bin/env python3
"""04_efforts.py — one row per max-effort route (TASK.md Phase 3).

Reads the WR/TE route snaps from ``player_play``, the tracking for those snaps
(game by game, never the whole file in pandas) and the **already-written**
route-depth table from ``03_route_depth``.  It writes ``efforts.csv`` — the only
input the model stage reads — plus an audit summary.

Per effort the table carries (TASK.md Phase 3): max speed, peak acceleration,
route family, D61 break depth and ``break_found``, time to throw, cumulative
load before the effort, game clock/elapsed, quarter, score margin,
down/distance and field position.  The outcome is ``log(max_speed)``; leverage
is ``4 p (1-p)`` from the **pre-snap** win probability and nothing post-snap
enters either definition.

The effort gate (minimum route extension and duration) is applied before
writing; rows it removes are counted, never silently dropped.  Rows with no
tracking or no depth row are reported in the summary.

Run:
    python src/04_efforts.py --mode sample [--force] [--limit-games N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import lc_common as C
import lc_efforts as E
import lc_route_depth as RD

STAGE = "04_efforts"

PLAYER_PLAY_COLUMNS = [
    "game_id", "play_id", "nfl_id", "lined_up_position", "route_ran",
    "quarter", "down", "yards_to_go", "play_direction", "game_clock",
    "pre_snap_home_score", "pre_snap_visitor_score",
    "pre_snap_home_team_win_probability",
    "yardline_side", "yardline_number", "possession_team",
    "dropback_duration",
]
PLAYERS_COLUMNS = ["nfl_id", "nfl_position", "draft_year", "display_name"]

DEPTH_COLUMNS = ["game_id", "play_id", "nfl_id", "break_found",
                 "break_depth_yd", "break_time_from_snap_s",
                 "break_after_throw", "throw_known", "flags"]

OUTPUT_FIELDS = [
    "game_id", "play_id", "nfl_id", "effort_seq", "route_family",
    "quarter", "down", "yards_to_go", "score_margin", "field_position_yd",
    "time_to_throw_s", "wp_pre", "leverage", "leverage_z", "leverage_bin",
    "max_speed_yds", "peak_accel_yds2", "log_max_speed",
    "route_displacement_yd", "route_path_yd", "duration_s", "n_frames",
    "break_found", "break_depth_yd", "break_time_from_snap_s",
    "break_after_throw", "throw_known", "prior_efforts", "prior_distance_yd",
    "game_clock_s", "game_elapsed_s", "time_z", "load_z", "one_bin_player",
    "start_time",
]


def _output_dir(cfg: dict, mode: str) -> Path:
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["efforts"].get("out_subdir", "game/lc/efforts")
    out = paths["outputs"] / sub
    out.mkdir(parents=True, exist_ok=True)
    return out


def _route_depth_path(cfg: dict, mode: str) -> Path:
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["route_depth"].get("out_subdir", "game/lc/route_depth")
    return paths["outputs"] / sub / "route_depth.csv"


def _read_route_depth(cfg: dict, mode: str) -> pd.DataFrame:
    path = _route_depth_path(cfg, mode)
    if not path.exists():
        raise RuntimeError(
            f"STAGE {STAGE!r}: {path} is absent. Run 03_route_depth first — "
            "the effort table takes its D61 depth control from that table "
            "instead of re-deriving it.")
    frame = pd.read_csv(path, low_memory=False)
    missing = [c for c in DEPTH_COLUMNS if c not in frame.columns]
    if missing:
        raise RuntimeError(f"route_depth.csv lacks column(s) {missing}")
    if mode == "full":
        values = {str(v) for v in frame["provenance"].dropna().unique()}
        if values != {"FULL"}:
            raise RuntimeError(
                f"STAGE {STAGE!r}: mode=full but route_depth.csv provenance is "
                f"{sorted(values)}, not ['FULL']. Rebuild 03_route_depth on the "
                "full tracking input.")
    return frame[DEPTH_COLUMNS].copy()


def _route_keys(cfg: dict, mode: str, force: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    pp = C.to_parquet(cfg, "player_play", "lc_effort_player_play",
                      columns=PLAYER_PLAY_COLUMNS, mode=mode, force=force)
    players_path = C.to_parquet(cfg, "players", "lc_players",
                                columns=PLAYERS_COLUMNS, mode=mode, force=force)
    players = pd.read_parquet(players_path, columns=PLAYERS_COLUMNS)
    con = C.duckdb_connect(cfg)
    try:
        frame = con.execute(f"""
            SELECT pp.game_id, pp.play_id, pp.nfl_id, pp.lined_up_position,
                   pp.route_ran, pp.quarter, pp.down, pp.yards_to_go,
                   pp.play_direction, pp.game_clock,
                   pp.pre_snap_home_score, pp.pre_snap_visitor_score,
                   pp.pre_snap_home_team_win_probability,
                   pp.yardline_side, pp.yardline_number, pp.possession_team,
                   pp.dropback_duration
            FROM read_parquet('{pp.as_posix()}') pp
            JOIN read_parquet('{players_path.as_posix()}') p USING (nfl_id)
            WHERE pp.route_ran IS NOT NULL AND pp.route_ran <> ''
              AND p.nfl_position IN ('WR', 'TE')
            ORDER BY pp.game_id, pp.play_id, pp.nfl_id
        """).fetchdf()
    finally:
        con.close()
    return frame, players


def _tracking_parquet(cfg: dict, mode: str, force: bool) -> Path:
    key = C.lc(cfg)["mode"][mode]["tracking_key"]
    return C.to_parquet(cfg, key, "lc_game_tracking",
                        columns=list(RD.TRACKING_COLUMNS), mode=mode,
                        force=force)


def _game_tracking(con, tracking_pq: Path, game_id: int) -> pd.DataFrame:
    return con.execute(f"""
        SELECT game_id, play_id, nfl_id, time, x, y, s, a
        FROM read_parquet('{tracking_pq.as_posix()}')
        WHERE game_id = ?
        ORDER BY game_id, play_id, nfl_id, time
    """, [int(game_id)]).fetchdf()


def _empty_table() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_FIELDS)


def compute(cfg: dict, mode: str, force: bool = False,
            limit_games: int | None = None
            ) -> tuple[pd.DataFrame, dict]:
    block = C.lc(cfg)["efforts"]
    keys, players = _route_keys(cfg, mode, force)
    depth = _read_route_depth(cfg, mode)
    tracking_pq = _tracking_parquet(cfg, mode, force)

    games = sorted(int(g) for g in keys["game_id"].unique())
    if limit_games is not None:
        games = games[:int(limit_games)]
        keys = keys[keys["game_id"].isin(games)]

    diag = {
        "n_route_key_rows": int(len(keys)),
        "n_route_key_players": int(keys["nfl_id"].nunique()),
        "n_route_key_games": int(len(games)),
        "n_missing_wp": 0,
        "n_without_tracking": 0,
        "n_without_depth": 0,
        "n_before_effort_gate": 0,
        "n_dropped_min_depth": 0,
        "n_break_found": 0,
        "n_no_break": 0,
        "n_break_after_throw": 0,
        "games_without_tracking": [],
    }
    records: list[pd.DataFrame] = []
    con = C.duckdb_connect(cfg)
    try:
        for index, game_id in enumerate(games, start=1):
            game_keys = keys[keys["game_id"] == game_id].copy()
            game_depth = depth[depth["game_id"] == game_id].copy()
            tracking = _game_tracking(con, tracking_pq, game_id)
            if tracking.empty:
                diag["games_without_tracking"].append(game_id)
                continue
            wanted = set(zip(game_keys["play_id"], game_keys["nfl_id"]))
            tracking = tracking[[(pl, nf) in wanted for pl, nf
                                 in zip(tracking["play_id"], tracking["nfl_id"])]]
            if tracking.empty:
                diag["games_without_tracking"].append(game_id)
                continue
            table, part = E.extract_efforts(
                game_keys, tracking, players=players, cfg=cfg,
                min_depth=float(block["min_route_depth_yd"]),
                min_duration=float(block["min_duration_s"]),
                depth_table=game_depth, finalise=False)
            for key in ("n_missing_wp", "n_without_tracking", "n_without_depth",
                        "n_before_effort_gate", "n_dropped_min_depth",
                        "n_break_found", "n_no_break", "n_break_after_throw"):
                diag[key] += int(part.get(key, 0))
            if not table.empty:
                records.append(table)
            if index % 50 == 0:
                print(f"  {index}/{len(games)} games", file=sys.stderr)
    finally:
        con.close()

    if not records:
        result = _empty_table()
    else:
        result = E.finalise_efforts(pd.concat(records, ignore_index=True), cfg)
    if result.empty:
        return result, diag
    result["game_id"] = result["game_id"].astype("int64")
    result["play_id"] = result["play_id"].astype("int64")
    result["nfl_id"] = result["nfl_id"].astype("int64")
    result["effort_seq"] = result["effort_seq"].astype("int64")
    result["n_frames"] = result["n_frames"].astype("int64")
    result = result.sort_values(
        ["game_id", "play_id", "nfl_id", "effort_seq"]).reset_index(drop=True)
    return result[[c for c in OUTPUT_FIELDS if c in result.columns]], diag


def summarise(table: pd.DataFrame, diag: dict, mode: str, cfg: dict) -> dict:
    thresholds = C.lc(cfg)["thresholds"]
    min_bin = int(thresholds.get("min_efforts_per_bin", 5))
    min_player = int(thresholds.get("min_efforts_per_player", 20))
    per_player = (table.groupby("nfl_id").size()
                  if not table.empty else pd.Series(dtype=int))
    bin_counts: dict[str, dict] = {}
    if not table.empty and "leverage_bin" in table.columns:
        for label, group in table.groupby("leverage_bin"):
            players = group.groupby("nfl_id").size()
            bin_counts[str(label)] = {
                "n_efforts": int(len(group)),
                "n_players": int(players.size),
                "n_players_ge_min_bin": int((players >= min_bin).sum()),
            }
    both = 0
    if not table.empty and {"leverage_bin", "nfl_id"} <= set(table.columns):
        counts = (table[table["leverage_bin"].isin(["low", "high"])]
                  .groupby(["nfl_id", "leverage_bin"]).size().unstack(fill_value=0))
        if {"low", "high"} <= set(counts.columns):
            both = int(((counts["low"] >= min_bin) & (counts["high"] >= min_bin)).sum())
    return {
        "stage": STAGE,
        "mode": mode,
        "definition": C.lc(cfg)["efforts"].get("definition_id"),
        "n_efforts": int(len(table)),
        "n_players": int(table["nfl_id"].nunique()) if not table.empty else 0,
        "n_games": int(table["game_id"].nunique()) if not table.empty else 0,
        "n_route_key_rows": diag["n_route_key_rows"],
        "n_route_key_players": diag["n_route_key_players"],
        "n_route_key_games": diag["n_route_key_games"],
        "n_missing_wp": diag["n_missing_wp"],
        "n_without_tracking": diag["n_without_tracking"],
        "n_without_depth": diag["n_without_depth"],
        "n_before_effort_gate": diag["n_before_effort_gate"],
        "n_dropped_by_effort_gate": diag["n_dropped_min_depth"],
        "n_break_found": diag["n_break_found"],
        "n_no_break": diag["n_no_break"],
        "n_break_after_throw": diag["n_break_after_throw"],
        "effort_gate": {
            "min_route_displacement_yd": float(C.lc(cfg)["efforts"]["min_route_depth_yd"]),
            "min_duration_s": float(C.lc(cfg)["efforts"]["min_duration_s"]),
            "note": ("route extension is the inclusion gate; the depth CONTROL is "
                     "break_depth_yd from 03_route_depth, never displacement"),
        },
        "leverage_bins": bin_counts,
        "min_efforts_per_bin": min_bin,
        "min_efforts_per_player": min_player,
        "n_players_both_extremes": both,
        "per_player_efforts": {
            "mean": float(per_player.mean()) if len(per_player) else 0.0,
            "median": float(per_player.median()) if len(per_player) else 0.0,
            "min": int(per_player.min()) if len(per_player) else 0,
            "max": int(per_player.max()) if len(per_player) else 0,
            "p10": float(per_player.quantile(0.10)) if len(per_player) else 0.0,
            "p90": float(per_player.quantile(0.90)) if len(per_player) else 0.0,
            "n_single_effort_players": int((per_player == 1).sum()) if len(per_player) else 0,
            "n_players_below_min_efforts": int((per_player < min_player).sum())
            if len(per_player) else 0,
        },
        "edge_cases": {
            "n_players_one_leverage_bin": int(
                table.loc[table["one_bin_player"], "nfl_id"].nunique())
            if not table.empty else 0,
            "n_single_effort_players": int((per_player == 1).sum())
            if len(per_player) else 0,
            "n_players_dropped_no_effort": int(
                diag["n_route_key_players"] - table["nfl_id"].nunique())
            if not table.empty else diag["n_route_key_players"],
            "n_games_without_tracking": len(diag["games_without_tracking"]),
            "games_without_tracking": diag["games_without_tracking"][:50],
        },
        "route_depth_source": str(_route_depth_path(cfg, mode)),
    }


def _markdown(summary: dict) -> str:
    lines = [
        "# Effort table — one row per max-effort route (TASK.md Phase 3)",
        "",
        f"- Efforts: **{summary['n_efforts']:,}**  ·  players: "
        f"**{summary['n_players']}**  ·  games: **{summary['n_games']:,}**",
        f"- Route-snap key rows read: {summary['n_route_key_rows']:,} "
        f"({summary['n_route_key_players']} players, "
        f"{summary['n_route_key_games']} games)",
        f"- Dropped: no pre-snap win probability {summary['n_missing_wp']:,}; "
        f"no tracking {summary['n_without_tracking']:,}; no depth row "
        f"{summary['n_without_depth']:,}; effort gate "
        f"{summary['n_dropped_by_effort_gate']:,}",
        "",
        "## Leverage bins (counts; never a finding)",
        "",
        "| bin | efforts | players | players with >= "
        f"{summary['min_efforts_per_bin']} efforts |",
        "|---|---:|---:|---:|",
    ]
    for label, block in sorted(summary["leverage_bins"].items()):
        lines.append(f"| {label} | {block['n_efforts']:,} | "
                     f"{block['n_players']} | {block['n_players_ge_min_bin']} |")
    lines += [
        "",
        f"- Players with >= {summary['min_efforts_per_bin']} efforts in **both** "
        f"low and high leverage: **{summary['n_players_both_extremes']}**",
        f"- Per-player efforts: mean {summary['per_player_efforts']['mean']:.1f}, "
        f"median {summary['per_player_efforts']['median']:.0f}, "
        f"range {summary['per_player_efforts']['min']}–"
        f"{summary['per_player_efforts']['max']} "
        f"(p10 {summary['per_player_efforts']['p10']:.0f}, "
        f"p90 {summary['per_player_efforts']['p90']:.0f})",
        "",
        "## Edge cases (reported, never dropped silently)",
        "",
        f"- Players with efforts in only one leverage bin: "
        f"{summary['edge_cases']['n_players_one_leverage_bin']}",
        f"- Single-effort players: {summary['edge_cases']['n_single_effort_players']}",
        f"- Key-table players with no usable effort: "
        f"{summary['edge_cases']['n_players_dropped_no_effort']}",
        f"- Games searched with no tracking: "
        f"{summary['edge_cases']['n_games_without_tracking']}",
        "",
        "Depth is `break_depth_yd` from `03_route_depth` (D61); routes with no "
        "detected break keep a MISSING depth and `break_found = false`. Nothing "
        "is imputed. The outcome is `log(max_speed)`; leverage uses only the "
        "pre-snap win probability.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sample", "full"), default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    parser.add_argument("--limit-games", type=int, default=None)
    args = parser.parse_args(argv)
    cfg = C.load_config(args.config)
    mode = args.mode or C.lc(cfg)["mode"]["default"]
    paths = C.resolve_paths(cfg, mode)
    C.assert_disk_space(paths, cfg)
    C.require_full_provenance(cfg, mode, STAGE)
    if C.is_done(cfg, STAGE, mode, force=args.force):
        print(f"[{STAGE}] checkpoint present — skipping (use --force to rebuild)")
        return 0
    out = _output_dir(cfg, mode)

    with C.timed_stage(cfg, STAGE, mode,
                       note="one row per max-effort route") as stage:
        table, diag = compute(cfg, mode, args.force, args.limit_games)
        summary = summarise(table, diag, mode, cfg)
        stage.note(f"efforts={len(table)} players={summary['n_players']}")
        stage.note(f"both_extremes={summary['n_players_both_extremes']}")

    fields = [c for c in OUTPUT_FIELDS if c in table.columns]
    C.write_csv(out / "efforts.csv", table.to_dict("records"), fields,
                mode=mode, cfg=cfg)
    C.write_json(out / "efforts_summary.json", summary, mode=mode, cfg=cfg)
    C.write_markdown(out / "SUMMARY.md", _markdown(summary), mode=mode, cfg=cfg)
    C.mark_done(cfg, STAGE, mode, extra={
        "n_efforts": int(len(table)),
        "n_players": summary["n_players"],
        "n_players_both_extremes": summary["n_players_both_extremes"],
    })
    print(f"[{STAGE}] {len(table)} efforts -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
