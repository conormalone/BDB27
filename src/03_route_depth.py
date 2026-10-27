#!/usr/bin/env python3
"""03_route_depth.py — realise route depth as *depth at the break* (D61).

Reads the tracking for every WR/TE route snap that `player_play` marks with a
route, rotates each play into the play frame, and applies the frozen definition
in `bdb27_lc.route_depth` (implemented in ``lc_route_depth``).  It writes one row
per route snap plus a coverage summary and the declared threshold sensitivity
curve.

This is the stage that supplies the depth control the model needs.  It is *not*
the model: depth enters later as a control (family + centred depth + indicator)
and is reported, never interpreted.

Scale discipline: the route keys are read from Parquet, and tracking is fetched
one game at a time with DuckDB.  The full tracking file is never loaded into
pandas.  Sample outputs are stamped ``UNVALIDATED SAMPLE OUTPUT`` by the shared
writers and are not reportable.

Run:
    python src/03_route_depth.py --mode sample [--force] [--limit-games N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import lc_common as C
import lc_route_depth as RD

STAGE = "03_route_depth"

PLAYER_PLAY_COLUMNS = {
    "game_id": "game_id", "play_id": "play_id", "nfl_id": "nfl_id",
    "route_family": "route_ran", "play_direction": "play_direction",
    "dropback_duration": "dropback_duration", "quarter": "quarter",
    "down": "down", "yards_to_go": "yards_to_go",
}
PLAYERS_COLUMNS = ["nfl_id", "nfl_position", "draft_year", "display_name"]

OUTPUT_FIELDS = [
    "game_id", "play_id", "nfl_id", "position", "route_family",
    "play_direction", "snap_time_s", "throw_time_s",
    "break_found", "break_depth_yd", "break_time_from_snap_s",
    "break_after_throw", "throw_known", "heading_before_deg",
    "heading_after_deg", "turn_deg", "n_frames", "median_gap_s",
    "start_offset_s", "flags",
]


def _output_dir(cfg: dict, mode: str) -> Path:
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["route_depth"].get("out_subdir", "game/lc/route_depth")
    out = paths["outputs"] / sub
    out.mkdir(parents=True, exist_ok=True)
    return out


def _route_keys(cfg: dict, mode: str, force: bool) -> pd.DataFrame:
    """WR/TE route snaps with direction and position, from Parquet mirrors."""
    pp = C.to_parquet(cfg, "player_play", "lc_player_play",
                      columns=PLAYER_PLAY_COLUMNS, mode=mode, force=force)
    players = C.to_parquet(cfg, "players", "lc_players",
                           columns=PLAYERS_COLUMNS, mode=mode, force=force)
    con = C.duckdb_connect(cfg)
    try:
        frame = con.execute(f"""
            SELECT pp.game_id, pp.play_id, pp.nfl_id,
                   pp.route_family, pp.play_direction,
                   pp.dropback_duration, pp.quarter, pp.down, pp.yards_to_go,
                   p.nfl_position AS position
            FROM read_parquet('{pp.as_posix()}') pp
            JOIN read_parquet('{players.as_posix()}') p USING (nfl_id)
            WHERE pp.route_family IS NOT NULL
              AND p.nfl_position IN ('WR', 'TE')
            ORDER BY pp.game_id, pp.play_id, pp.nfl_id
        """).fetchdf()
    finally:
        con.close()
    return frame


def _tracking_parquet(cfg: dict, mode: str, force: bool) -> Path:
    key = C.lc(cfg)["mode"][mode]["tracking_key"]
    return C.to_parquet(cfg, key, "lc_game_tracking",
                        columns=list(RD.TRACKING_COLUMNS), mode=mode,
                        force=force)


def _game_tracking(con, tracking_pq: Path, game_id: int) -> pd.DataFrame:
    path = tracking_pq.as_posix()
    return con.execute(f"""
        SELECT game_id, play_id, nfl_id, time, x, y, s, a, event
        FROM read_parquet('{path}')
        WHERE game_id = ?
        ORDER BY game_id, play_id, nfl_id, time
    """, [int(game_id)]).fetchdf()


def _game_events(con, tracking_pq: Path, game_id: int, cfg: dict) -> dict:
    p = RD.params(cfg)
    path = tracking_pq.as_posix()
    frame = con.execute(f"""
        SELECT game_id, play_id,
               min(time) FILTER (WHERE event = ?) AS snap_time,
               min(time) FILTER (WHERE event = ?) AS throw_time
        FROM read_parquet('{path}')
        WHERE game_id = ?
        GROUP BY game_id, play_id
    """, [str(p["snap_event"]), str(p["throw_event"]), int(game_id)]).fetchdf()
    events = {}
    for row in frame.itertuples(index=False):
        events[(int(row.game_id), int(row.play_id))] = {
            "snap": row.snap_time if pd.notna(row.snap_time) else None,
            "throw": row.throw_time if pd.notna(row.throw_time) else None,
        }
    return events


def compute(cfg: dict, mode: str, force: bool = False,
            limit_games: int | None = None
            ) -> tuple[pd.DataFrame, dict, dict]:
    p = RD.params(cfg)
    keys = _route_keys(cfg, mode, force)
    tracking_pq = _tracking_parquet(cfg, mode, force)
    games = sorted(int(g) for g in keys["game_id"].unique())
    if limit_games is not None:
        games = games[:int(limit_games)]
        keys = keys[keys["game_id"].isin(games)]
    diag = {
        "n_route_snaps": int(len(keys)),
        "n_players": int(keys["nfl_id"].nunique()),
        "n_games": int(len(games)),
        "n_play_direction_missing": int(keys["play_direction"].isna().sum()),
    }
    sens_limit = int(RD.params(cfg).get("sensitivity_max_tracks", 500))
    sens_keys = keys.sort_values(["game_id", "play_id", "nfl_id"]).head(sens_limit)
    sens_pairs = set(zip(sens_keys["game_id"], sens_keys["play_id"],
                         sens_keys["nfl_id"]))
    sens_frames: list[pd.DataFrame] = []
    sens_events: dict = {}

    records = []
    con = C.duckdb_connect(cfg)
    try:
        for index, game_id in enumerate(games, start=1):
            game_keys = keys[keys["game_id"] == game_id].copy()
            tracking = _game_tracking(con, tracking_pq, game_id)
            if tracking.empty:
                diag.setdefault("games_without_tracking", []).append(game_id)
                continue
            wanted = set(zip(game_keys["play_id"], game_keys["nfl_id"]))
            tracking = tracking[[(pl, nf) in wanted for pl, nf
                                 in zip(tracking["play_id"], tracking["nfl_id"])]]
            if tracking.empty:
                continue
            direction_map = dict(zip(
                zip(game_keys["game_id"], game_keys["play_id"]),
                game_keys["play_direction"]))
            events = _game_events(con, tracking_pq, game_id, cfg)
            sens_events.update({key: value for key, value in events.items()
                                if key[0] == game_id})
            subset = tracking[[(g, pl, nf) in sens_pairs for g, pl, nf in zip(
                tracking["game_id"], tracking["play_id"], tracking["nfl_id"])]]
            if not subset.empty:
                sens_frames.append(subset)
            table = RD.route_depth_table(tracking, direction_map, cfg,
                                         play_events=events)
            join_cols = ["game_id", "play_id", "nfl_id", "position",
                         "route_family", "dropback_duration"]
            merged = game_keys[join_cols].merge(
                table, on=["game_id", "play_id", "nfl_id"], how="left")
            records.append(merged)
            if index % 50 == 0:
                print(f"  {index}/{len(games)} games", file=sys.stderr)
    finally:
        con.close()

    if not records:
        raise RuntimeError("no tracking rows matched any WR/TE route snap")
    result = pd.concat(records, ignore_index=True)
    # Route snaps with no tracking never appear in the depth table: count them
    # and report; never silently drop.
    matched = result["break_found"].notna()
    diag["n_route_snaps_without_tracking"] = int(len(keys) - int(matched.sum()))
    diag["n_route_snaps_analysed"] = int(matched.sum())
    result = result[matched].copy()
    result["break_found"] = result["break_found"].astype(bool)
    result["break_after_throw"] = result["break_after_throw"].astype(bool)
    result["throw_known"] = result["throw_known"].astype(bool)
    result = result.sort_values(["game_id", "play_id", "nfl_id"]).reset_index(drop=True)
    sens_input = {
        "tracking": pd.concat(sens_frames, ignore_index=True)
        if sens_frames else pd.DataFrame(columns=list(RD.TRACKING_COLUMNS)),
        "direction": dict(zip(
            zip(sens_keys["game_id"], sens_keys["play_id"]),
            sens_keys["play_direction"])),
        "events": sens_events,
    }
    return result[OUTPUT_FIELDS], diag, sens_input


def sensitivity(cfg: dict, sens_input: dict) -> list[dict]:
    """Run the declared specification curve on a deterministic subsample."""
    tracking = sens_input["tracking"]
    output = []
    for spec in RD.sensitivity_grid(cfg):
        table = RD.route_depth_table(tracking, sens_input["direction"], cfg,
                                     overrides=spec,
                                     play_events=sens_input["events"])
        found = table["break_found"].astype(bool)
        depths = table.loc[found, "break_depth_yd"]
        output.append({
            **spec,
            "n_routes": int(len(table)),
            "break_rate": float(found.mean()) if len(table) else float("nan"),
            "median_break_depth_yd": float(depths.median())
            if not depths.dropna().empty else float("nan"),
        })
    return output


def _markdown(summary: dict, sens: list[dict], mode: str) -> str:
    lines = [
        "# Route depth at the break — coverage (D61)",
        "",
        f"Definition: `{summary['definition']}`. Depth is a CONTROL; these are "
        "coverage diagnostics, not findings.",
        "",
        f"- Route snaps with tracking analysed: **{summary['n_route_snaps_analysed']}**",
        f"- Route snaps without tracking: {summary['n_route_snaps_without_tracking']} "
        "(the provided tracking sample covers only a subset of games)",
        f"- Players: {summary['n_players']}  ·  games: {summary['n_games']}",
        f"- Break detected: **{summary['coverage']['n_break_found']}** "
        f"({summary['coverage']['break_rate']:.1%})  ·  of which before the "
        f"throw: **{summary['coverage']['n_break_before_throw']}** "
        f"({summary['coverage']['break_rate_before_throw']:.1%} of routes)",
        f"- Break after throw, among breaks with a known throw: "
        f"**{summary['coverage']['break_after_throw_rate']:.1%}**",
        "",
        "## By position",
        "",
        "| position | routes | breaks | break rate | break after throw | throw known |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for position, block in sorted(summary["coverage"]["by_position"].items()):
        lines.append(
            f"| {position} | {block['n_routes']} | {block['n_break_found']} | "
            f"{block['break_rate']:.3f} | {block['n_break_after_throw']} | "
            f"{block['n_throw_known']} |")
    lines += ["", "## Break depth by route family (detected breaks only)", "",
              "| family | routes | breaks | rate | median yd | IQR yd |",
              "|---|---:|---:|---:|---:|---:|"]
    for row in summary["coverage"]["by_route_family"]:
        lines.append(
            f"| {row['route_family']} | {row['n_routes']} | {row['n_break_found']} | "
            f"{row['break_rate']:.3f} | {row['median_break_depth_yd']:.2f} | "
            f"{row['iqr_low_yd']:.2f}–{row['iqr_high_yd']:.2f} |")
    lines += ["", "## Threshold sensitivity (declared curve)", "",
              "| angle° | window s | heading s | routes | break rate | median yd |",
              "|---:|---:|---:|---:|---:|---:|"]
    for row in sens:
        lines.append(
            f"| {row['break_angle_deg']:.0f} | {row['break_window_s']:.1f} | "
            f"{row['heading_window_s']:.2f} | {row['n_routes']} | "
            f"{row['break_rate']:.3f} | {row['median_break_depth_yd']:.2f} |")
    lines += ["", "## Flags (plays the definition could not use)", ""]
    for flag, count in sorted(summary["coverage"]["flag_counts"].items()):
        lines.append(f"- `{flag}`: {count}")
    lines += ["", "`break_found = false` routes carry NO depth. Nothing is imputed.",
              "See `notes/blockers.md` for the counts reported here."]
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
    out = _output_dir(cfg, mode)

    with C.timed_stage(cfg, STAGE, mode,
                       note="depth at the break (D61)") as stage:
        table, diag, sens_input = compute(cfg, mode, args.force,
                                          args.limit_games)
        stage.note(f"analysed={len(table)}")
        work = table.reset_index(drop=True)
        coverage = RD.coverage_summary(
            work, positions=work["position"],
            route_family=work["route_family"])
        summary = {
            "definition": RD.params(cfg)["definition_id"],
            "n_route_snaps": diag["n_route_snaps"],
            "n_route_snaps_analysed": diag["n_route_snaps_analysed"],
            "n_route_snaps_without_tracking": diag["n_route_snaps_without_tracking"],
            "n_players": diag["n_players"],
            "n_games": diag["n_games"],
            "n_play_direction_missing": diag["n_play_direction_missing"],
            "coverage": coverage,
        }
        sens = sensitivity(cfg, sens_input)
        stage.note(f"break_rate={coverage['break_rate']:.3f}")

    C.write_csv(out / "route_depth.csv", table.to_dict("records"),
                OUTPUT_FIELDS, mode=mode, cfg=cfg)
    C.write_json(out / "route_depth_summary.json", summary, mode=mode, cfg=cfg)
    C.write_csv(out / "route_depth_sensitivity.csv", sens,
                list(sens[0].keys()) if sens else ["break_angle_deg"],
                mode=mode, cfg=cfg)
    C.write_markdown(out / "SUMMARY.md", _markdown(summary, sens, mode),
                     mode=mode, cfg=cfg)
    print(f"[{STAGE}] {len(table)} route snaps -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
