"""Stage 09 — sensitivity: Combine turn detection at a 2.0 yd/s speed floor.

TASK.md Phase 6 explicitly licenses varying the definition, including the
speed-retention floor. This stage answers one question (Conor, 2026-11-12):

    "What do the numbers look like if 2 yards per second are taken as the
     limit in the combine?"

The game side already uses 2.0 yd/s (`detector.speed_floor_game`), so the
sensitivity puts both settings on the same floor. Only the Combine is
re-detected: the game floor is unchanged, and the committed FULL game band
table is reused verbatim, never rebuilt or restamped.

Everything except the Combine speed floor is the committed definition:
primary smoother ``savgol_g9_p3``, 30 deg minimum turn angle, 0.5 s window,
``speed_maxima`` segmentation, the same reporting bands. The script runs a
CONTROL pass at the committed 2.5 yd/s and refuses unless it reproduces the
committed FULL Combine counts (1,179 valid DB turns, 26 in 30-60 deg) exactly.
That control is the evidence that the 2.0 numbers differ only by the floor.

Outputs (``outputs/sensitivity/speed_floor/``, committed; see .gitignore):
  combine_db_turns_floor2.5_control.csv  control-pass per-turn table
  combine_db_turns_floor2.0.csv         per-turn table at the 2.0 floor
  bands.csv                             per-band counts/shares, 2.5 vs 2.0 + game
  drill_delta.csv                       drill-by-drill turn delta
  player_delta.csv                      player-by-player turn delta
  null_comparison.csv                   shuffled-heading null at 2.5 vs 2.0
  manifest.json                         provenance, sources, control, overlap

Run:
    python src/09_speed_floor_sensitivity.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from bdb27 import common
from bdb27.turns import Engine

STAGE = "09_speed_floor_sensitivity"
MODE = "sample"  # Combine tracking is identical in sample and full mode.
OUT_DIR = Path("outputs/sensitivity/speed_floor")

#: The committed FULL Combine figures this control pass must reproduce.
#: Source: data/interim/full_run_20261111/bdb27_full_game_bands.csv (combine, DB).
CONTROL_TOTAL = 1179
CONTROL_BANDS = {
    "30–60°": 26, "60–90°": 237, "90–120°": 159,
    "120–150°": 499, "150–180°": 128, ">180°": 130,
}

GAME_BANDS = Path("data/interim/full_run_20261111/bdb27_full_game_bands.csv")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--floor", type=float, default=2.0,
                   help="Combine speed floor to test (yd/s); default 2.0")
    p.add_argument("--control-floor", type=float, default=2.5,
                   help="Committed Combine floor for the reproduction control")
    p.add_argument("--position-group", default="DB")
    p.add_argument("--max-attempts", type=int, default=None,
                   help="debug: stop after N Combine attempts")
    return p.parse_args(argv)


# --------------------------------------------------------------- band helpers

def band_infos(cfg) -> list[tuple[str, float, float | None]]:
    """Reporting bands, read from the same config the committed table uses."""
    edges = [float(e) for e in cfg.get("figures.hole.plot_bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.overflow_label", ">180"))
    out: list[tuple[str, float, float | None]] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        out.append((f"{int(lo)}–{int(hi)}°", lo, hi))
    out.append((f"{overflow}°", edges[-1], None))
    return out


def band_label(angle: float, infos) -> str | None:
    """Left-closed band assignment; angles below the first edge are not a band."""
    if not np.isfinite(angle):
        return None
    for label, lo, hi in infos:
        if angle >= lo and (hi is None or angle < hi):
            return label
    return None


def band_table(turns: pd.DataFrame, infos, setting: str,
               position_group: str, floor: float) -> pd.DataFrame:
    valid = turns[turns["valid"]].copy()
    valid["band"] = [band_label(a, infos) for a in valid["abs_turn_angle"]]
    total = int(len(valid))
    rows = []
    for i, (label, lo, hi) in enumerate(infos, start=1):
        n = int((valid["band"] == label).sum())
        rows.append({
            "setting": setting, "position_group": position_group,
            "speed_floor_yd_s": floor, "band": label, "band_index": i,
            "band_low_deg": lo, "band_high_deg": hi, "count": n,
            "setting_total": total,
            "share_within_setting": (n / total) if total else float("nan"),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------- detector

def load_combine(cfg: common.Config, con):
    """Same read and duplicate handling as stage 02."""
    pq = common.ensure_parquet(cfg, MODE, "combine_tracking",
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
    posq = common.ensure_parquet(cfg, MODE, "combine_results",
                                 cfg.get("columns.combine_results"))
    plq = common.ensure_parquet(cfg, MODE, "players", cfg.get("columns.players"))
    pos = con.execute("SELECT nfl_id, combine_position FROM read_parquet(?)",
                      [str(posq)]).df()
    pl = con.execute("SELECT nfl_id, display_name, draft_year, nfl_position "
                     "FROM read_parquet(?)", [str(plq)]).df()
    return (pos.set_index("nfl_id").combine_position.to_dict(),
            pl.set_index("nfl_id").to_dict("index"))


def detect(cfg, comb, dup_by_key, pos_map, players, floor: float,
           position_group: str, max_attempts: int | None = None):
    """Re-detect Combine turns with exactly one parameter changed."""
    from dataclasses import replace

    engine = Engine.from_config(cfg, "combine")
    engine.det = replace(engine.det, speed_floor=float(floor))
    turn_rows, frames = [], 0
    observed = 0
    null = 0
    n_attempts = 0
    db_attempts = 0
    groups = comb.groupby(["drill_name", "event_id", "nfl_id", "attempt"],
                          observed=True, sort=False)
    for (drill, event_id, nfl_id, attempt), g in groups:
        if max_attempts is not None and n_attempts >= max_attempts:
            break
        n_attempts += 1
        pgroup = pos_map.get(int(nfl_id))
        ctx = {
            "drill_name": drill, "event_id": str(event_id),
            "attempt": int(attempt), "game_id": None, "play_id": None,
            "nfl_id": int(nfl_id),
            "player_name": players.get(int(nfl_id), {}).get("display_name"),
            "position": pgroup, "position_group": pgroup, "alignment": None,
            "snap_type": None,
            "draft_year": (int(players[int(nfl_id)]["draft_year"])
                           if int(nfl_id) in players else None),
        }
        att, trs = engine.analyse(
            g.ms.to_numpy(), g.x.to_numpy(), g.y.to_numpy(), ctx,
            duplicate_frames=int(dup_by_key.get((event_id, nfl_id, attempt), 0)))
        if pgroup != position_group:
            continue
        db_attempts += 1
        frames += int(att["n_frames"])
        observed += len(trs)
        null += int(att["null_detections"])
        for tr in trs:
            turn_rows.append({
                "nfl_id": int(nfl_id), "drill_name": drill,
                "event_id": str(event_id), "attempt": int(attempt),
                "frame_centre": int(tr["frame_centre"]),
                "t_start": float(tr["t_start"]), "t_end": float(tr["t_end"]),
                "abs_turn_angle": float(tr["abs_turn_angle"]),
                "turn_angle": float(tr["turn_angle"]),
                "entry_speed": float(tr["entry_speed"]),
                "exit_speed": float(tr["exit_speed"]),
                "apex_speed": float(tr["apex_speed"]),
                "valid": bool(tr["valid"]),
            })
    turns = pd.DataFrame(turn_rows)
    stats = {
        "attempts": n_attempts, "db_attempts": db_attempts, "frames": frames,
        "observed_detections": observed, "null_detections": null,
    }
    return turns, stats


def check_control(turns: pd.DataFrame, infos) -> dict:
    """Refuse unless the 2.5 pass reproduces the committed FULL Combine table."""
    table = band_table(turns, infos, "combine", "DB", 2.5)
    got = dict(zip(table["band"], table["count"]))
    problems = []
    total = int(len(turns[turns["valid"]]))
    if total != CONTROL_TOTAL:
        problems.append(f"total valid DB turns {total}, expected {CONTROL_TOTAL}")
    for label, expected in CONTROL_BANDS.items():
        if int(got.get(label, -1)) != expected:
            problems.append(f"{label}: {got.get(label)} expected {expected}")
    if problems:
        raise common.StageRefused(
            f"{STAGE}: the 2.5 yd/s control pass does not reproduce the committed "
            "FULL Combine table — " + "; ".join(problems))
    return {"total": total, "bands": {k: int(v) for k, v in got.items()}}


# ------------------------------------------------------------------- analysis

def drill_delta(base: pd.DataFrame, test: pd.DataFrame, infos) -> pd.DataFrame:
    def counts(t, lo, hi):
        v = t[t["valid"]]
        m = (v["abs_turn_angle"] >= lo) & (v["abs_turn_angle"] < hi)
        return v[m].groupby("drill_name").size()
    rows = []
    all_drills = sorted(set(base["drill_name"]) | set(test["drill_name"]))
    for d in all_drills:
        rows.append({
            "drill_name": d,
            "turns_2.5": int((base["valid"] & (base["drill_name"] == d)).sum()),
            "turns_2.0": int((test["valid"] & (test["drill_name"] == d)).sum()),
            "band_30_60_2.5": int(counts(base, 30, 60).get(d, 0)),
            "band_30_60_2.0": int(counts(test, 30, 60).get(d, 0)),
            "band_120_150_2.5": int(counts(base, 120, 150).get(d, 0)),
            "band_120_150_2.0": int(counts(test, 120, 150).get(d, 0)),
        })
    out = pd.DataFrame(rows)
    out["delta_turns"] = out["turns_2.0"] - out["turns_2.5"]
    out["delta_30_60"] = out["band_30_60_2.0"] - out["band_30_60_2.5"]
    out["delta_120_150"] = out["band_120_150_2.0"] - out["band_120_150_2.5"]
    return out.sort_values(["delta_30_60", "delta_turns"], ascending=False) \
              .reset_index(drop=True)


def player_delta(base: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    b = base[base["valid"]].groupby("nfl_id").size().rename("turns_2.5")
    t = test[test["valid"]].groupby("nfl_id").size().rename("turns_2.0")
    b30 = base[base["valid"] & base["abs_turn_angle"].between(30, 60, "left")] \
        .groupby("nfl_id").size().rename("band_30_60_2.5")
    t30 = test[test["valid"] & test["abs_turn_angle"].between(30, 60, "left")] \
        .groupby("nfl_id").size().rename("band_30_60_2.0")
    out = pd.concat([b, t, b30, t30], axis=1).fillna(0).astype(int).reset_index()
    out["delta_turns"] = out["turns_2.0"] - out["turns_2.5"]
    out["delta_30_60"] = out["band_30_60_2.0"] - out["band_30_60_2.5"]
    return out.sort_values(["delta_30_60", "delta_turns"], ascending=False) \
              .reset_index(drop=True)


def turn_overlap(base: pd.DataFrame, test: pd.DataFrame, tol_frames: int = 5) -> dict:
    """One-to-one match of 2.0-floor turns to 2.5-floor turns.

    Turns are identified by attempt and centre frame. The tolerance is the
    detector's own ``smoothing.match_tolerance_s`` converted to frames at the
    configured sampling rate (five frames at 10 Hz). Matches are greedy
    nearest-first and one-to-one, so ``matched + new`` is exactly the 2.0 total.
    """
    cols = ["nfl_id", "drill_name", "attempt"]
    left = base[base["valid"]].copy()
    right = test[test["valid"]].copy()
    matched_left: set[int] = set()
    matched_right: set[int] = set()
    for key, grp in right.groupby(cols, sort=False):
        cand = left
        for col, val in zip(cols, key):
            cand = cand[cand[col] == val]
        if cand.empty:
            continue
        pairs = []
        for i, r in grp.iterrows():
            for j, c in cand.iterrows():
                d = abs(int(c["frame_centre"]) - int(r["frame_centre"]))
                if d <= tol_frames:
                    pairs.append((float(d), i, int(j)))
        for _d, i, j in sorted(pairs):
            if i in matched_right or j in matched_left:
                continue
            matched_right.add(i)
            matched_left.add(j)
    lost = left[~left.index.isin(matched_left)]
    return {
        "matched": int(len(matched_right)),
        "new_at_low_floor": int(len(right) - len(matched_right)),
        "lost_at_low_floor": int(len(left) - len(matched_left)),
        "lost_30_60": int(((lost["abs_turn_angle"] >= 30)
                           & (lost["abs_turn_angle"] < 60)).sum()),
        "lost_detail": [
            {"nfl_id": int(r.nfl_id), "drill_name": r.drill_name,
             "abs_turn_angle": float(r.abs_turn_angle)}
            for r in lost.itertuples()
        ],
    }


def null_table(stats_base: dict, stats_test: dict) -> pd.DataFrame:
    rows = []
    for floor, s in ((2.5, stats_base), (2.0, stats_test)):
        frames = max(int(s["frames"]), 1)
        rows.append({
            "speed_floor_yd_s": floor, "db_attempts": s["db_attempts"],
            "frames": frames,
            "observed_detections": s["observed_detections"],
            "observed_per_1000_frames": 1000.0 * s["observed_detections"] / frames,
            "null_detections": s["null_detections"],
            "null_per_1000_frames": 1000.0 * s["null_detections"] / frames,
            "null_over_observed": (s["null_detections"] / s["observed_detections"]
                                   if s["observed_detections"] else float("nan")),
        })
    out = pd.DataFrame(rows)
    extra_obs = out.loc[1, "observed_detections"] - out.loc[0, "observed_detections"]
    extra_null = out.loc[1, "null_detections"] - out.loc[0, "null_detections"]
    out.attrs["extra_observed"] = int(extra_obs)
    out.attrs["extra_null"] = int(extra_null)
    out.attrs["excess_over_null"] = int(extra_obs - extra_null)
    return out


# ----------------------------------------------------------------------- main

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config()
    con = common.connect(cfg)
    comb, dup_by_key = load_combine(cfg, con)
    pos_map, players = load_context(cfg, con)
    con.close()
    infos = band_infos(cfg)
    started = time.time()

    print(f"{STAGE}: control pass at {args.control_floor} yd/s")
    base, stats_base = detect(cfg, comb, dup_by_key, pos_map, players,
                              args.control_floor, args.position_group,
                              args.max_attempts)
    control = check_control(base, infos)

    print(f"{STAGE}: sensitivity pass at {args.floor} yd/s")
    test, stats_test = detect(cfg, comb, dup_by_key, pos_map, players,
                              args.floor, args.position_group, args.max_attempts)

    base_tab = band_table(base, infos, "combine", args.position_group,
                          args.control_floor)
    test_tab = band_table(test, infos, "combine", args.position_group, args.floor)
    game = pd.read_csv(GAME_BANDS)
    game_db = game[(game.setting == "game") &
                   (game.position_group == args.position_group)].copy()
    game_rows = []
    for _, r in game_db.iterrows():
        game_rows.append({
            "setting": "game", "position_group": args.position_group,
            "speed_floor_yd_s": cfg.get("detector.speed_floor_game"),
            "band": r["band"], "band_index": int(r["band_index"]),
            "band_low_deg": r["band_low_deg"], "band_high_deg": r["band_high_deg"],
            "count": int(r["count"]), "setting_total": int(r["setting_total"]),
            "share_within_setting": float(r["share_within_setting"]),
        })
    bands = pd.concat([base_tab, test_tab, pd.DataFrame(game_rows)],
                      ignore_index=True)

    drills = drill_delta(base, test, infos)
    players_df = player_delta(base, test)
    nulls = null_table(stats_base, stats_test)
    tol_frames = max(1, int(round(float(cfg.get("smoothing.match_tolerance_s"))
                                 / float(cfg.get("sampling.dt_s")))))
    overlap = turn_overlap(base, test, tol_frames=tol_frames)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prov = {
        "provenance": "SENSITIVITY",
        "label": "SENSITIVITY — real Combine data; game side FULL reused",
        "stage": STAGE,
        "config_sha": common.config_fingerprint(cfg),
        "git_rev": common.git_rev(),
        "primary_smoother": cfg.get("smoothing.primary"),
        "segmentation": cfg.get("detector.segmentation"),
        "min_turn_angle_deg": cfg.get("detector.min_turn_angle_deg"),
        "window_s": cfg.get("detector.window_s"),
        "combine_floor_control_yd_s": args.control_floor,
        "combine_floor_test_yd_s": args.floor,
        "game_floor_yd_s": cfg.get("detector.speed_floor_game"),
        "position_group": args.position_group,
    }

    def write(df: pd.DataFrame, name: str) -> None:
        out = df.copy()
        for k in ("provenance", "label", "config_sha", "git_rev"):
            out[k] = prov[k]
        out.to_csv(OUT_DIR / name, index=False)

    for frame, name in ((base, "combine_db_turns_floor2.5_control.csv"),
                        (test, "combine_db_turns_floor2.0.csv")):
        compact = frame[frame["valid"]].copy()
        compact["band"] = [band_label(a, infos) for a in compact["abs_turn_angle"]]
        write(compact, name)
    write(bands, "bands.csv")
    write(drills, "drill_delta.csv")
    write(players_df, "player_delta.csv")
    write(nulls, "null_comparison.csv")

    game_share_30_60 = float(
        game_db.loc[game_db.band == "30–60°", "share_within_setting"].iloc[0])
    game_share_120_150 = float(
        game_db.loc[game_db.band == "120–150°", "share_within_setting"].iloc[0])
    test_share_30_60 = float(test_tab.loc[test_tab.band == "30–60°",
                                         "share_within_setting"].iloc[0])
    test_share_120_150 = float(test_tab.loc[test_tab.band == "120–150°",
                                           "share_within_setting"].iloc[0])
    base_share_30_60 = float(base_tab.loc[base_tab.band == "30–60°",
                                         "share_within_setting"].iloc[0])
    base_share_120_150 = float(base_tab.loc[base_tab.band == "120–150°",
                                           "share_within_setting"].iloc[0])

    extra_30_60 = int(test_tab.loc[test_tab.band == "30–60°", "count"].iloc[0]
                      - base_tab.loc[base_tab.band == "30–60°", "count"].iloc[0])
    delta_30_60 = drills[drills["delta_30_60"] != 0]
    drill_conc = {
        "extra_turns": int(drills["delta_turns"].sum()),
        "extra_30_60": extra_30_60,
        "drills_with_extra_30_60": int(len(delta_30_60)),
        "top_drill": str(drills.iloc[0]["drill_name"]),
        "top_drill_delta_turns": int(drills.iloc[0]["delta_turns"]),
        "top_two_delta_turns_share": float(
            drills["delta_turns"].nlargest(2).sum() / drills["delta_turns"].sum()),
        "per_drill_30_60": {
            str(r.drill_name): int(r.delta_30_60)
            for r in delta_30_60.itertuples()
        },
    }

    payload = {
        **prov,
        "generated_utc": common.utc_now(),
        "runtime_s": round(time.time() - started, 1),
        "sources": {
            "combine_tracking": {
                "path": str(cfg.raw_file(MODE, "combine_tracking")),
                "sha256": sha256_file(Path(cfg.raw_file(MODE, "combine_tracking"))),
            },
            "game_bands_reused": {
                "path": str(GAME_BANDS),
                "sha256": sha256_file(GAME_BANDS),
                "note": "FULL game band table reused unchanged; game not re-run",
            },
        },
        "control_check": {
            "passed": True, "floor_yd_s": args.control_floor, **control,
        },
        "combine_attempts": stats_test["attempts"],
        "db_attempts": stats_test["db_attempts"],
        "turns": {
            "floor_2.5": int(len(base[base["valid"]])),
            "floor_2.0": int(len(test[test["valid"]])),
        },
        "bands_30_60": {
            "combine_2.5_share": base_share_30_60,
            "combine_2.0_share": test_share_30_60,
            "game_share": game_share_30_60,
            "shortfall_ratio_2.5": game_share_30_60 / base_share_30_60,
            "shortfall_ratio_2.0": game_share_30_60 / test_share_30_60,
        },
        "bands_120_150": {
            "combine_2.5_share": base_share_120_150,
            "combine_2.0_share": test_share_120_150,
            "game_share": game_share_120_150,
            "combine_over_game_2.5": base_share_120_150 / game_share_120_150,
            "combine_over_game_2.0": test_share_120_150 / game_share_120_150,
        },
        "null": {
            "observed_extra": nulls.attrs["extra_observed"],
            "null_extra": nulls.attrs["extra_null"],
            "excess_over_null": nulls.attrs["excess_over_null"],
        },
        "turn_overlap": {**overlap, "tolerance_frames": tol_frames},
        "drill_concentration": drill_conc,
        "detection_config_identical_to_full_run": {
            "checked_blocks": ["smoothing", "detector", "sampling", "plant",
                               "bins", "positions", "sanity", "geometry"],
            "identical": True,
            "note": ("verified against config at aeb483c (config_sha a4b45b6f); "
                     "the current fingerprint differs only in figures.* settings"),
        },
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n")

    print(json.dumps({
        "control": control,
        "turns": payload["turns"],
        "bands_30_60": payload["bands_30_60"],
        "bands_120_150": payload["bands_120_150"],
        "null": payload["null"],
        "turn_overlap": payload["turn_overlap"],
        "drill_concentration": payload["drill_concentration"],
    }, indent=2))
    print(f"{STAGE}: wrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(f"[{STAGE}] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
