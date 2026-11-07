"""Stage 02 — Phase 2: kinematics and turn detection (TASK.md Phase 2).

Both settings run through one code path: smoothed x/y/time -> velocity, speed,
heading, tangential acceleration and angular rate -> turn candidates -> one row
per turn in Parquet. Provided ``s``/``a``/``dir`` are never inputs; game ``o`` is
carried only as a contamination flag.

Outputs (all under ``outputs/turns/``, gitignored):
  turns.parquet             one row per detected turn (the only table Phase 3+ reads)
  attempts.parquet          one row per attempt/snap incl. per-smoother counts
  summary.parquet           small per-drill and per-player summary
  sanity.csv                speed cap, duplicate frames, accel cap, shuffled null
  smoother_counts.csv       detections per smoother, per setting and drill
  segmentation_sensitivity.csv  combine turn counts under both segmentation rules
  geometry_recovery.csv     precision/recall vs estimated cones, BOTH target models
  manifest.json             provenance, config fingerprint, counts, provenance gate

Run:
    python src/02_turns.py --mode sample [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import replace

import numpy as np
import pandas as pd
import pyarrow as pa

from bdb27 import common, geometry
from bdb27.turns import (Engine, SUMMARY_SCHEMA, TURN_COLUMNS, TURN_SCHEMA,
                         attempt_schema, frame_of)

STAGE = "02_turns"


# ------------------------------------------------------------------ sql helpers

def snap_type_sql(cfg: common.Config, prefix: str = "") -> str:
    """Build the snap-type CASE expression from config (single source of truth)."""
    parts = []
    for rule in cfg.get("snap_types.rules"):
        test = rule["test"]
        col = rule.get("column")
        if test == "notnull":
            cond = f"{prefix}{col} IS NOT NULL"
        elif test == "truthy":
            cond = f"{prefix}{col}"
        elif test == "notnull_excluding":
            vals = ", ".join("'" + str(v) + "'" for v in rule["exclude"])
            cond = f"({prefix}{col} IS NOT NULL AND {prefix}{col} NOT IN ({vals}))"
        else:
            cond = "TRUE"
        parts.append(f"WHEN {cond} THEN '{rule['name']}'")
    return "CASE " + " ".join(parts) + " ELSE 'OTHER' END"


def position_family(cfg: common.Config, nfl_position) -> str:
    fam = cfg.get("positions.game_families")
    for group, members in fam.items():
        if nfl_position in members:
            return group
    return "OTHER"


# ------------------------------------------------------------------- data loads

def load_combine(cfg: common.Config, con):
    pq = common.ensure_parquet(cfg, _MODE, "combine_tracking",
                               cfg.get("columns.combine_tracking"))
    df = con.execute(
        """
        SELECT drill_name, event_id, nfl_id, attempt, epoch_ms(time) AS ms, x, y
        FROM read_parquet(?)
        WHERE entity_type = 'PLAYER'
        ORDER BY drill_name, event_id, nfl_id, attempt, ms
        """, [str(pq)]).df()
    dup = df.duplicated(["nfl_id", "event_id", "attempt", "ms"])
    dup_by_key = (df[dup].groupby(["event_id", "nfl_id", "attempt"]).size()
                  if bool(dup.any()) else pd.Series(dtype=int))
    if cfg.get("sampling.drop_duplicate_frames"):
        df = df[~dup]
    return df, dup_by_key


def load_context(cfg: common.Config, con):
    posq = common.ensure_parquet(cfg, _MODE, "combine_results", cfg.get("columns.combine_results"))
    plq = common.ensure_parquet(cfg, _MODE, "players", cfg.get("columns.players"))
    pos = con.execute("SELECT nfl_id, combine_position FROM read_parquet(?)", [str(posq)]).df()
    pl = con.execute("SELECT nfl_id, display_name, draft_year, nfl_position FROM read_parquet(?)",
                     [str(plq)]).df()
    return (pos.set_index("nfl_id").combine_position.to_dict(),
            pl.set_index("nfl_id").to_dict("index"))


def game_batches(cfg: common.Config, con, mode: str):
    """Yield (game_chunk, player_play_chunk) with the same chunking as stage 01."""
    pq = common.ensure_parquet(cfg, mode, "game_tracking", cfg.get("columns.game_tracking"))
    ids = con.execute("SELECT DISTINCT game_id FROM read_parquet(?) ORDER BY 1",
                      [str(pq)]).fetchnumpy()["game_id"]
    size = int(cfg.get("resources.game_chunk_games"))
    ppq = common.ensure_parquet(cfg, mode, "player_play", cfg.get("columns.player_play"))
    for i in range(0, len(ids), size):
        batch = [int(g) for g in ids[i:i + size]]
        ph = ",".join("?" * len(batch))
        df = con.execute(
            f"""SELECT game_id, play_id, nfl_id, epoch_ms(time) AS ms, x, y, o, dir
                FROM read_parquet(?) WHERE game_id IN ({ph})""", [str(pq), *batch]).df()
        df = df.sort_values(["game_id", "play_id", "nfl_id", "ms"], kind="stable")
        pp = con.execute(
            f"""SELECT game_id, play_id, nfl_id, lined_up_position,
                       {snap_type_sql(cfg)} AS snap_type
                FROM read_parquet(?) WHERE game_id IN ({ph})""", [str(ppq), *batch]).df()
        yield df, pp


# ------------------------------------------------------------------------ stage

_MODE = "sample"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    return p.parse_args(argv)


def check_provenance(cfg: common.Config, mode: str) -> None:
    if mode != "full":
        return
    tp = cfg.thresholds_path()
    if not tp.exists():
        raise common.StageRefused(
            "02_turns --mode full: outputs/thresholds.json missing; run "
            "00_thresholds --mode full first")
    prov = json.loads(tp.read_text()).get("provenance")
    if prov != common.PROVENANCE_FULL:
        raise common.StageRefused(
            f"02_turns --mode full: thresholds provenance is {prov!r}, expected FULL "
            "— refusing to build a turn table on placeholder thresholds")


def checkpoint_ok(cfg: common.Config, mode: str, outdir) -> bool:
    man = outdir / "manifest.json"
    if not man.exists() or not (outdir / "turns.parquet").exists():
        return False
    try:
        d = json.loads(man.read_text())
    except Exception:  # pragma: no cover - corrupt manifest means rebuild
        return False
    return (d.get("config_sha") == common.config_fingerprint(cfg)
            and d.get("mode") == mode
            and d.get("provenance") == common.provenance(mode))


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    global _MODE
    _MODE = args.mode
    check_provenance(cfg, args.mode)

    outdir = cfg.repo_path("turns")
    if checkpoint_ok(cfg, args.mode, outdir) and not args.force:
        print(f"{STAGE}: checkpoint reused ({outdir / 'turns.parquet'})")
        common.log_run(STAGE, args.mode, 0.0, 0, "OK",
                       "checkpoint reused, nothing rebuilt", cfg=cfg)
        return 0

    if args.mode == "full":
        common.require_inputs([cfg.raw_file("full", "combine_tracking"),
                               cfg.raw_file("full", "game_tracking")],
                              stage="02_turns --mode full")

    engine_c = Engine.from_config(cfg, "combine")
    engine_g = Engine.from_config(cfg, "game")
    seg_options = list(cfg.get("detector.segmentation_options"))
    aschema = attempt_schema(engine_c.smooth_names, seg_options)
    outdir.mkdir(parents=True, exist_ok=True)

    con = common.connect(cfg)
    counters: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    smoother_counts: dict[tuple, int] = defaultdict(int)
    seg_counts: dict[tuple, int] = defaultdict(int)

    with common.StageTimer(STAGE, args.mode, cfg) as timer:
        turn_sink = common.ParquetSink(outdir / "turns.parquet", TURN_SCHEMA, args.mode, cfg)
        att_sink = common.ParquetSink(outdir / "attempts.parquet", aschema, args.mode, cfg)
        try:
            # ---- Combine --------------------------------------------------
            pos_map, players = load_context(cfg, con)
            comb, dup_by_key = load_combine(cfg, con)
            timer.rows = len(comb)
            turn_buf, att_buf = [], []

            def flush():
                nonlocal turn_buf, att_buf
                if att_buf:
                    att_sink.write(frame_of(att_buf, aschema))
                if turn_buf:
                    turn_sink.write(frame_of(turn_buf, TURN_SCHEMA))
                timer.rows += len(turn_buf)
                turn_buf, att_buf = [], []

            for (drill, event_id, nfl_id, attempt), g in comb.groupby(
                    ["drill_name", "event_id", "nfl_id", "attempt"], observed=True, sort=False):
                ctx = {
                    "drill_name": drill, "event_id": str(event_id),
                    "attempt": int(attempt), "game_id": None, "play_id": None,
                    "nfl_id": int(nfl_id),
                    "player_name": players.get(int(nfl_id), {}).get("display_name"),
                    "position": pos_map.get(int(nfl_id)),
                    "position_group": pos_map.get(int(nfl_id)),
                    "alignment": None, "snap_type": None,
                    "draft_year": (int(players[int(nfl_id)]["draft_year"])
                                   if int(nfl_id) in players else None),
                }
                att, trs = engine_c.analyse(
                    g.ms.to_numpy(), g.x.to_numpy(), g.y.to_numpy(), ctx,
                    duplicate_frames=int(dup_by_key.get((event_id, nfl_id, attempt), 0)))
                att_buf.append(att)
                turn_buf.extend(trs)
                _account(counters[("combine", str(drill))], att, trs, engine_c, g)
                for name in engine_c.smooth_names:
                    smoother_counts[("combine", name)] += int(att.get(f"turns_{name}", 0))
                for opt in seg_options:
                    key = f"turns_seg_{opt}"
                    if key in att:
                        seg_counts[(drill, opt)] += int(att[key])
                if len(att_buf) >= 2000:
                    flush()
            flush()

            # ---- Game -----------------------------------------------------
            for chunk, pp in game_batches(cfg, con, args.mode):
                pp_idx = pp.set_index(["game_id", "play_id", "nfl_id"]).to_dict("index")
                timer.rows += len(chunk)
                for (gid, pid, nid), g in chunk.groupby(
                        ["game_id", "play_id", "nfl_id"], observed=True, sort=True):
                    info = pp_idx.get((gid, pid, nid), {})
                    npos = players.get(int(nid), {}).get("nfl_position")
                    ctx = {
                        "drill_name": None, "event_id": None, "attempt": None,
                        "game_id": int(gid), "play_id": int(pid), "nfl_id": int(nid),
                        "player_name": players.get(int(nid), {}).get("display_name"),
                        "position": npos, "position_group": position_family(cfg, npos),
                        "alignment": info.get("lined_up_position"),
                        "snap_type": info.get("snap_type"),
                        "draft_year": (int(players[int(nid)]["draft_year"])
                                       if int(nid) in players else None),
                    }
                    att, trs = engine_g.analyse(
                        g.ms.to_numpy(), g.x.to_numpy(), g.y.to_numpy(), ctx,
                        o=g.o.to_numpy(), dirv=g.dir.to_numpy())
                    att_buf.append(att)
                    turn_buf.extend(trs)
                    _account(counters[("game", str(ctx.get("snap_type") or "UNMATCHED"))],
                             att, trs, engine_g, g)
                    for name in engine_g.smooth_names:
                        smoother_counts[("game", name)] += int(att.get(f"turns_{name}", 0))
                    if len(att_buf) >= 4000:
                        flush()
            flush()
        finally:
            turn_sink.close()
            att_sink.close()
            con.close()

        _write_summaries(cfg, args.mode, outdir, con=None)
        _write_smoother_tables(cfg, args.mode, outdir, smoother_counts, seg_counts)
        _write_sanity(cfg, args.mode, outdir, counters)
        _write_geometry(cfg, args.mode, outdir)
        _write_manifest(cfg, args.mode, outdir)
    print(f"{STAGE}: wrote {outdir / 'turns.parquet'}")
    return 0


def _account(acc: dict, att: dict, trs: list[dict], engine: Engine, g) -> None:
    acc["attempts"] += 1
    acc["frames"] += int(att["n_frames"])
    acc["frames_speed_above_cap"] += int(att.get("frames_speed_above_cap", 0))
    acc["frames_accel_above_cap"] += int(att.get("frames_accel_above_cap", 0))
    acc["duplicate_frames"] += int(att["duplicate_frames"])
    acc["interpolated_frames"] += int(att["interpolated_frames"])
    acc["gaps_over_limit"] += int(att["gaps_over_limit"])
    acc["detections"] += len(trs)
    acc["null_detections"] += int(att["null_detections"])
    acc["turns_invalid"] += sum(1 for t in trs if not t["valid"])


def _write_summaries(cfg, mode, outdir, con=None) -> None:
    import duckdb
    c = duckdb.connect()
    try:
        turns = str(outdir / "turns.parquet")
        atts = str(outdir / "attempts.parquet")
        drill_units = c.execute(
            """SELECT setting, coalesce(position_group,'') AS position_group,
                      coalesce(drill_name,'') AS drill_name, coalesce(snap_type,'') AS snap_type,
                      count(*) AS n_units
               FROM read_parquet(?) GROUP BY ALL""", [atts]).df()
        drill_turns = c.execute(
            """SELECT setting, coalesce(position_group,'') AS position_group,
                      coalesce(drill_name,'') AS drill_name, coalesce(snap_type,'') AS snap_type,
                      count(*) AS n_turns, median(entry_speed) AS median_entry_speed,
                      median(exit_speed) AS median_exit_speed, median(apex_speed) AS median_apex_speed,
                      median(speed_retention) AS median_speed_retention,
                      median(abs_turn_angle) AS median_turn_angle,
                      avg(CASE WHEN plant THEN 1 ELSE 0 END) AS share_plant,
                      avg(CASE WHEN valid THEN 1 ELSE 0 END) AS share_valid
               FROM read_parquet(?) GROUP BY ALL""", [turns]).df()
        drill = drill_units.merge(
            drill_turns, on=["setting", "position_group", "drill_name", "snap_type"], how="left")
        drill["level"] = "drill"
        drill["nfl_id"] = None

        pl_units = c.execute(
            """SELECT setting, nfl_id, count(*) AS n_units FROM read_parquet(?) GROUP BY ALL""",
            [atts]).df()
        pl_turns = c.execute(
            """SELECT setting, nfl_id, count(*) AS n_turns, median(entry_speed) AS median_entry_speed,
                      median(exit_speed) AS median_exit_speed, median(apex_speed) AS median_apex_speed,
                      median(speed_retention) AS median_speed_retention,
                      median(abs_turn_angle) AS median_turn_angle,
                      avg(CASE WHEN plant THEN 1 ELSE 0 END) AS share_plant,
                      avg(CASE WHEN valid THEN 1 ELSE 0 END) AS share_valid
               FROM read_parquet(?) GROUP BY ALL""", [turns]).df()
        player = pl_units.merge(pl_turns, on=["setting", "nfl_id"], how="left")
        player["level"] = "player"
        player["position_group"] = None
        player["drill_name"] = None
        player["snap_type"] = None
    finally:
        c.close()
    out = pd.concat([drill, player], ignore_index=True)
    out["turns_per_unit"] = (out.n_turns.fillna(0) / out.n_units).replace([np.inf, -np.inf], np.nan)
    for c_ in [c for c in out.columns if c not in ("level", "setting", "position_group",
                                                   "drill_name", "snap_type", "nfl_id",
                                                   "n_units", "n_turns", "turns_per_unit")]:
        out[c_] = pd.to_numeric(out[c_], errors="coerce")
    common.write_parquet(out, outdir / "summary.parquet", SUMMARY_SCHEMA, mode, cfg)


def _write_smoother_tables(cfg, mode, outdir, smoother_counts, seg_counts) -> None:
    rows = []
    for (setting, name), n in sorted(smoother_counts.items()):
        rows.append({"setting": setting, "smoother": name, "turns": int(n),
                     "primary": name == cfg.get("smoothing.primary")})
    common.write_csv(pd.DataFrame(rows), outdir / "smoother_counts.csv", mode, cfg)
    if seg_counts:
        rows = [{"drill_name": k[0], "segmentation": k[1], "turns": int(v)}
                for k, v in sorted(seg_counts.items())]
        common.write_csv(pd.DataFrame(rows), outdir / "segmentation_sensitivity.csv", mode, cfg)


def _write_sanity(cfg, mode, outdir, counters) -> None:
    rows = []
    for setting in ("combine", "game"):
        keys = sorted(k for k in counters if k[0] == setting)
        if not keys:
            rows.append({"setting": setting, "group": None,
                         "status": "NOT RUN — setting absent"})
            continue
        total = defaultdict(float)
        for key in keys:
            a = counters[key]
            for name, value in a.items():
                total[name] += value
            if a.get("frames"):
                rows.append(_sanity_row(cfg, setting, key[1], a))
        rows.append(_sanity_row(cfg, setting, "TOTAL", total))
    common.write_csv(pd.DataFrame(rows), outdir / "sanity.csv", mode, cfg)


def _sanity_row(cfg, setting: str, group: str, a: dict) -> dict:
    if not a or not a.get("frames"):
        return {"setting": setting, "group": group, "status": "NOT RUN — no frames"}
    frames = max(int(a["frames"]), 1)
    return {
        "setting": setting, "group": group, "status": "OK",
        "attempts": int(a["attempts"]), "frames": int(a["frames"]),
        "frames_speed_above_cap": int(a["frames_speed_above_cap"]),
        "share_speed_above_cap": round(a["frames_speed_above_cap"] / frames, 6),
        "frames_accel_above_cap": int(a["frames_accel_above_cap"]),
        "share_accel_above_cap": round(a["frames_accel_above_cap"] / frames, 6),
        "duplicate_frames": int(a["duplicate_frames"]),
        "interpolated_frames": int(a["interpolated_frames"]),
        "gaps_over_limit": int(a["gaps_over_limit"]),
        "detections": int(a["detections"]),
        "detections_per_1000_frames": round(1000.0 * a["detections"] / frames, 3),
        "null_detections": int(a["null_detections"]),
        "null_per_1000_frames": round(1000.0 * a["null_detections"] / frames, 3),
        "turns_invalid": int(a["turns_invalid"]),
        "speed_cap_yd_s": cfg.get("sanity.speed_cap_yd_s"),
        "accel_cap_yd_s2": cfg.get("smoothing.accel_cap_yd_s2"),
    }


def _write_geometry(cfg, mode, outdir) -> None:
    import duckdb
    c = duckdb.connect()
    try:
        turns = c.execute(
            """SELECT drill_name, event_id, attempt, frame_centre, x_centre, y_centre, turn_angle
               FROM read_parquet(?) WHERE setting = 'combine'""",
            [str(outdir / "turns.parquet")]).df()
    finally:
        c.close()
    rec = geometry.score_geometry(turns, cfg)
    common.write_csv(rec, outdir / "geometry_recovery.csv", mode, cfg)


def _write_manifest(cfg, mode, outdir) -> None:
    payload = {
        "stage": STAGE,
        "mode": mode,
        "provenance": common.provenance(mode),
        "config_sha": common.config_fingerprint(cfg),
        "spec_commit": cfg.get("spec_commit"),
        "git_rev": common.git_rev(),
        "config_git_rev": common.git_rev(),
        "primary_smoother": cfg.get("smoothing.primary"),
        "smoothers": [c["name"] for c in cfg.get("smoothing.candidates")],
        "segmentation": cfg.get("detector.segmentation"),
        "segmentation_options": cfg.get("detector.segmentation_options"),
        "speed_floor_combine": cfg.get("detector.speed_floor_combine"),
        "speed_floor_game": cfg.get("detector.speed_floor_game"),
        "geometry_models_reported": cfg.get("geometry.report_models"),
        "geometry_gate_model": cfg.get("geometry.drill_model"),
        "open_decisions": ["D124 segmentation rule", "D125 drill geometry model"],
        "note": ("Sample-mode artefacts are PLACEHOLDER_SAMPLE / UNVALIDATED SAMPLE OUTPUT. "
                 "D124 and D125 are implemented as parameters and remain OPEN with Conor."),
    }
    common.write_json(payload, outdir / "manifest.json", mode, cfg)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(f"[{STAGE}] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
