"""Scale probe for stage 02: the synthetic fixture concatenated N times.

Each copy renames nfl_ids, event_ids, game_ids and play_ids, so the stage sees N
independent "players" and games rather than duplicates. The probe runs the real
stage CLI through a scratch config and reports wall time and peak RSS from
``outputs/run_log.csv``. It writes only under a temporary directory.

Usage:
    python scripts/scale_probe.py --copies 1 2 4 8 16
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "synthetic"


def build_world(dest: Path, copies: int) -> None:
    comb = pd.read_csv(FIXTURE / "combine_tracking.csv", parse_dates=["time"])
    res = pd.read_csv(FIXTURE / "combine_results.csv")
    players = pd.read_csv(FIXTURE / "players.csv")
    game = pd.read_csv(FIXTURE / "game_tracking.csv", parse_dates=["time"])
    pp = pd.read_csv(FIXTURE / "player_play.csv")
    games = pd.read_csv(FIXTURE / "games.csv")

    c_parts, r_parts, pl_parts, g_parts, pp_parts, gs_parts = [], [], [], [], [], []
    for k in range(copies):
        off = 1_000_000 * k
        c = comb.copy()
        c["nfl_id"] += off
        c["event_id"] = c["event_id"].astype(str) + f"-x{k}"
        c_parts.append(c)
        r = res.copy(); r["nfl_id"] += off; r_parts.append(r)
        p = players.copy(); p["nfl_id"] += off; p["display_name"] += f"-x{k}"; pl_parts.append(p)
        g = game.copy(); g["nfl_id"] += off; g["game_id"] += 1000 * k; g["play_id"] += 1000 * k
        g_parts.append(g)
        q = pp.copy(); q["nfl_id"] += off; q["game_id"] += 1000 * k; q["play_id"] += 1000 * k
        pp_parts.append(q)
        s = games.copy(); s["game_id"] += 1000 * k; gs_parts.append(s)

    dest.mkdir(parents=True, exist_ok=True)
    pd.concat(c_parts).to_csv(dest / "combine_tracking.csv", index=False)
    pd.concat(r_parts).to_csv(dest / "combine_results.csv", index=False)
    pd.concat(pl_parts).to_csv(dest / "players.csv", index=False)
    pd.concat(g_parts).to_csv(dest / "game_tracking.csv", index=False)
    pd.concat(pp_parts).to_csv(dest / "player_play.csv", index=False)
    pd.concat(gs_parts).to_csv(dest / "games.csv", index=False)
    pd.DataFrame(columns=["nfl_id", "career_offensive_snaps", "career_defensive_snaps",
                          "career_games_active"]).to_csv(
        dest / "player_career_successes.csv", index=False)


def make_config(work: Path) -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    b = cfg["turn_coverage"]
    b["paths"]["raw"] = str(work / "raw")
    b["paths"]["parquet"] = str(work / "parquet")
    b["paths"]["checkpoints"] = str(work / "checkpoints")
    b["paths"]["outputs"] = str(work / "outputs")
    b["paths"]["audit"] = str(work / "outputs" / "audit")
    b["paths"]["turns"] = str(work / "outputs" / "turns")
    b["paths"]["thresholds"] = str(work / "outputs" / "thresholds.json")
    b["paths"]["run_log"] = str(work / "outputs" / "run_log.csv")
    b["sample_files"] = {k: v for k, v in b["files"].items()}
    p = work / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--copies", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    args = ap.parse_args()
    rows = []
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        for n in args.copies:
            work = base / f"n{n}"
            build_world(work / "raw", n)
            cfgp = make_config(work)
            res = subprocess.run([sys.executable, str(ROOT / "src" / "02_turns.py"),
                                  "--mode", "sample", "--force", "--config", str(cfgp)],
                                 cwd=str(ROOT), capture_output=True, text=True)
            log = work / "outputs" / "run_log.csv"
            if res.returncode != 0:
                print(res.stderr[-2000:])
                return res.returncode
            row = pd.read_csv(log).iloc[-1]
            turns = pd.read_parquet(work / "outputs" / "turns" / "turns.parquet")
            rows.append({"copies": n, "combine_rows": n * 1277, "game_rows": n * 147,
                         "turns": len(turns), "wall_s": float(row.wall_s),
                         "peak_rss_mb": float(row.peak_rss_mb)})
            print(rows[-1])
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
