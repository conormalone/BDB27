#!/usr/bin/env python3
"""synthetic_db_decay_fixture.py — tiny schema-compliant fixture for `01_db_audit.py`.

The real game-tracking sample cannot exercise the DB-decay audit end-to-end in a unit test
(~4 MB / 1 M rows). This generator writes a deterministic, tiny, schema-exact fixture so the
whole stage (items a-i, gate, checkpointing) runs in seconds.

Planted truth (never interpreted as a finding):
  * 6 DBs (CB x3, SS, FS, DB), 2 Combine classes (2023/2024);
  * two FORTY_YARD_DASH attempts per DB with a planted between-player vmax SD and a smaller
    within-player attempt noise -> planted test-retest ICC ~= 0.85;
  * 3 games x 16 plays, quarters 1-4, one DB row + one opponent row per play;
  * 30-frame (10 Hz) game-tracking series per DB play, with a `ball_snap` event and a depth
    behind the line of scrimmage consistent with the `lined_up_position` label.

Usage:
    python src/synthetic_db_decay_fixture.py --out fixtures/synthetic/db_decay
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

PLAYERS_COLS = ["nfl_id", "display_name", "draft_year", "nfl_position", "birth_date",
                "college_name", "college_conference", "draft_round",
                "draft_pick_within_round", "draft_overall_pick"]
GAMES_COLS = ["game_id", "game_key", "season", "season_type", "week",
              "home_team_abbr", "visitor_team_abbr"]
COMBINE_COLS = ["draft_year", "event_id", "nfl_id", "entity_type", "time", "drill_type",
                "drill_name", "attempt", "x", "y", "s", "a", "dis", "dir"]
GAME_TRACKING_COLS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis",
                      "o", "dir", "event"]
PLAYER_PLAY_COLS = [
    "game_id", "play_id", "team_abbr", "nfl_id", "lined_up_position", "play_description",
    "quarter", "down", "yards_to_go", "possession_team", "yardline_side", "yardline_number",
    "game_clock", "pre_snap_home_score", "pre_snap_visitor_score", "play_direction",
    "pre_snap_home_team_win_probability", "pre_snap_visitor_team_win_probability",
    "home_team_win_probability_added", "visitor_team_win_probility_added",
    "expected_points_added", "expected_points", "offense_formation", "receiver_alignment",
    "pass_result", "pass_length", "play_nullified_by_penalty", "penalty_yards",
    "pre_penalty_yards_gained", "yards_gained", "team_coverage_man_zone", "team_coverage_type",
    "in_motion_at_ball_snap", "target", "rec_yards", "rush_yards", "yards_after_catch",
    "fumble", "fumble_lost", "route_ran", "separation_at_pass_forward", "cushion",
    "expected_yards_after_catch", "dropback_duration", "extended_sack_allowed",
    "pass_rushers_encountered", "peak_pressure_probability_allowed", "pressure_allowed",
    "sack_allowed", "time_to_pressure_allowed", "quick_pressure", "unblocked_pressure",
    "blitzing", "player_get_off", "sack", "tackle", "assist", "caused_forced_fumble",
    "recovered_fumble", "tackle_for_loss", "time_to_pressure", "time_to_qb_hurry",
    "coverage_assignment", "coverage_assignment_at_snap",
]


def _iso(day: str, second: float) -> str:
    """10 Hz timestamp string on a fixed date (deterministic)."""
    h = int(second // 3600)
    m = int((second % 3600) // 60)
    s = second % 60
    return f"{day}T{h:02d}:{m:02d}:{s:06.3f}"


def _write(path: Path, cols: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def _straight_attempt(rng, vmax_att: float, tau: float, n_frames: int = 45):
    """Frames for a straight-line drill; returns x, y, s, a, dis per frame."""
    dt = 0.1
    t = np.arange(n_frames) * dt + 0.2
    v = vmax_att * (1.0 - np.exp(-t / tau))
    step = v * dt
    x = np.cumsum(step)
    y = np.zeros_like(x)
    a = np.gradient(v, dt)
    return x, y, v, a, step


def _cut_attempt(rng, v_entry: float, n_frames: int = 40):
    """A plausible 2-segment change-of-direction path (speed drops through the cut)."""
    dt = 0.1
    i = n_frames // 2
    v = np.empty(n_frames)
    v[:i] = np.linspace(v_entry, v_entry * 0.75, i)
    v[i:] = np.linspace(v_entry * 0.55, v_entry * 0.9, n_frames - i)
    v = np.maximum(v, 0.2) + rng.normal(0, 0.02, n_frames)
    ang = np.where(np.arange(n_frames) < i, 0.0, math.pi / 2)
    x = np.concatenate([[0.0], np.cumsum(v[:-1] * dt * np.cos(ang[:-1]))])
    y = np.concatenate([[0.0], np.cumsum(v[:-1] * dt * np.sin(ang[:-1]))])
    a = np.gradient(v, dt)
    return x, y, v, a, v * dt


def generate(out: Path, seed: int = 424242) -> Path:
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)

    # ---------------- players ----------------
    db_specs = [("CB", 2023), ("CB", 2023), ("CB", 2024), ("SS", 2023), ("FS", 2024), ("DB", 2024)]
    players: list[dict] = []
    for k, (pos, yr) in enumerate(db_specs):
        players.append(dict(nfl_id=900001 + k, display_name=f"DB{k+1}", draft_year=yr,
                            nfl_position=pos, birth_date="2000-01-01", college_name="Fixture U",
                            college_conference="FIX", draft_round="1", draft_pick_within_round="1",
                            draft_overall_pick=str(k + 1)))
    for k, pos in enumerate(["WR", "WR", "TE", "WR"]):
        players.append(dict(nfl_id=910001 + k, display_name=f"OFF{k+1}", draft_year=2023,
                            nfl_position=pos, birth_date="2000-01-01", college_name="Fixture U",
                            college_conference="FIX", draft_round="2", draft_pick_within_round="1",
                            draft_overall_pick=str(40 + k)))
    _write(out / "players.csv", PLAYERS_COLS, players)

    # ---------------- games ----------------
    games = [dict(game_id=2023000001 + g, game_key=2023000001 + g, season=2023,
                  season_type="REG", week=g + 1, home_team_abbr="HOM", visitor_team_abbr="VIS")
             for g in range(3)]
    _write(out / "games.csv", GAMES_COLS, games)

    # ---------------- combine tracking ----------------
    combine: list[dict] = []
    day = "2023-03-01"
    ev = 0
    player_vmax: dict[int, float] = {}
    for k, (pos, yr) in enumerate(db_specs):
        nid = 900001 + k
        vmax_p = float(9.0 + rng.normal(0, 0.45))
        player_vmax[nid] = vmax_p
        for attempt in (1, 2):
            ev += 1
            eid = f"{nid}-40-{attempt}"
            x, y, v, a, dis = _straight_attempt(rng, vmax_p + rng.normal(0, 0.18), 0.8)
            for i in range(len(x)):
                combine.append(dict(draft_year=yr, event_id=eid, nfl_id=nid, entity_type="PLAYER",
                                    time=_iso(day, ev * 300 + i * 0.1), drill_type="FORTY_YARD_DASH",
                                    drill_name="FORTY_YARD_DASH", attempt=attempt,
                                    x=round(float(x[i]), 3), y=round(float(y[i]), 3),
                                    s=round(float(v[i]), 4), a=round(float(a[i]), 4),
                                    dis=round(float(dis[i]), 4), dir=90.0))
        cut_n = 2 if k < 2 else 1
        for attempt in range(1, cut_n + 1):
            ev += 1
            eid = f"{nid}-cone-{attempt}"
            x, y, v, a, dis = _cut_attempt(rng, 6.5 + rng.normal(0, 0.2))
            for i in range(len(x)):
                combine.append(dict(draft_year=yr, event_id=eid, nfl_id=nid, entity_type="PLAYER",
                                    time=_iso(day, ev * 300 + i * 0.1), drill_type="THREE_CONE_DRILL",
                                    drill_name="THREE_CONE_DRILL", attempt=attempt,
                                    x=round(float(x[i]), 3), y=round(float(y[i]), 3),
                                    s=round(float(v[i]), 4), a=round(float(a[i]), 4),
                                    dis=round(float(dis[i]), 4), dir=90.0))
        for drill in ("BOX_DRILL", "GAUNTLET_DRILL"):
            ev += 1
            eid = f"{nid}-{drill}-1"
            x, y, v, a, dis = _cut_attempt(rng, 5.5 + rng.normal(0, 0.2))
            for i in range(len(x)):
                combine.append(dict(draft_year=yr, event_id=eid, nfl_id=nid, entity_type="PLAYER",
                                    time=_iso(day, ev * 300 + i * 0.1), drill_type="SKILL_DRILLS_DB",
                                    drill_name=drill, attempt=1,
                                    x=round(float(x[i]), 3), y=round(float(y[i]), 3),
                                    s=round(float(v[i]), 4), a=round(float(a[i]), 4),
                                    dis=round(float(dis[i]), 4), dir=90.0))
    # one BALL entity series so entity_type filtering is exercised
    for i in range(6):
        combine.append(dict(draft_year=2023, event_id="ball-1", nfl_id=0, entity_type="BALL",
                            time=_iso(day, 9000 + i * 0.1), drill_type="FORTY_YARD_DASH",
                            drill_name="FORTY_YARD_DASH", attempt=1, x=1.0, y=0.0,
                            s=0.0, a=0.0, dis=0.0, dir=0.0))
    _write(out / "combine_tracking.csv", COMBINE_COLS, combine)

    # ---------------- player_play + game tracking ----------------
    pp: list[dict] = []
    gt: list[dict] = []
    lup = ["CB", "SLOT_CB", "HIGH_SAFETY", "BOX_SAFETY"]
    depth = {"CB": 5.0, "SLOT_CB": 4.0, "HIGH_SAFETY": 13.0, "BOX_SAFETY": 9.0, "DB": 14.0}
    los_x = 80.0  # own 30 -> x = 10 + (100-30) = 80 for direction=right
    for g, game in enumerate(games):
        gid = game["game_id"]
        for p in range(16):
            pid = 100 + p
            quarter = 1 + (p // 4) % 4
            db = players[p % len(db_specs)]
            off = players[len(db_specs) + (p % 4)]
            pos_label = lup[p % len(lup)]
            is_pass = (p % 3) != 0
            base = dict(game_id=gid, play_id=pid, quarter=quarter, down=1 + (p % 4),
                        yards_to_go=10, possession_team="OFF", yardline_side="DEF",
                        yardline_number=30, game_clock=f"0{15 - quarter}:00",
                        pre_snap_home_score=0, pre_snap_visitor_score=0,
                        play_direction="right", play_description="Fixture play",
                        play_nullified_by_penalty="N", pass_length=10 if is_pass else None,
                        pass_result="C" if is_pass else None,
                        dropback_duration=2.5 if is_pass else None,
                        target=False, yards_gained=4, team_coverage_type="COVER_1",
                        team_coverage_man_zone="MAN" if p % 2 == 0 else "ZONE")
            pp.append(dict(base, team_abbr="DEF", nfl_id=db["nfl_id"],
                           lined_up_position=pos_label,
                           coverage_assignment="MAN" if p % 2 == 0 else "ZONE",
                           coverage_assignment_at_snap="MAN" if p % 2 == 0 else "ZONE"))
            pp.append(dict(base, team_abbr="OFF", nfl_id=off["nfl_id"],
                           lined_up_position="WR", route_ran="GO" if is_pass else None,
                           target=True if is_pass and p % 4 == 0 else False))
            # 30 frames at 10 Hz, depth behind the LOS
            d0 = depth.get(pos_label, 8.0)
            y0 = 20.0 + 3.0 * (p % 3)
            for i in range(30):
                x = los_x + d0 + 0.05 * i
                y = y0 + 0.02 * i
                s = 4.0 + 0.05 * i
                a = 0.5 if i < 10 else 0.0
                step = math.hypot(0.05, 0.02)
                event = "ball_snap" if i == 5 else ("tackle" if i == 29 else "NA")
                gt.append(dict(game_id=gid, play_id=pid, nfl_id=db["nfl_id"],
                               time=_iso(f"2023-09-{10+g:02d}", p * 20 + i * 0.1),
                               x=round(x, 3), y=round(y, 3), s=round(s, 3), a=a,
                               dis=round(step, 4), o=180.0, dir=180.0, event=event))
    _write(out / "player_play.csv", PLAYER_PLAY_COLS, pp)
    _write(out / "game_tracking.csv", GAME_TRACKING_COLS, gt)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "fixtures" / "synthetic" / "db_decay"))
    ap.add_argument("--seed", type=int, default=424242)
    a = ap.parse_args()
    p = generate(Path(a.out), seed=a.seed)
    print(f"wrote synthetic db_decay fixture -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
