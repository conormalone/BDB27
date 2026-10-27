#!/usr/bin/env python3
"""synthetic_lc_fixture.py — schema-compliant fixtures with PLANTED truth.

These files are **not data**.  They exist so the same code path that reads the
real Kaggle files can be exercised on a machine with a row-capped sample, and so
tests can check that a planted ceiling and a planted leverage effect are
recoverable.

Combine fixture (``combine/``): 60 players (40 WR, 12 TE, 8 CB) over draft years
2023-2025, 40-yard-dash frames at 100 ms generated from
``v(t) = vmax * (1 - exp(-t / tau))`` plus sensor noise.  Deliberately planted:

* players with 1 attempt and with 2 attempts;
* a handful of frames above the 12 yd/s sanity cap;
* one duplicated attempt number (same nfl_id + drill + attempt, two time blocks);
* one gap > 0.5 s inside the peak-speed window;
* BALL entity rows (the real file has them too).

Game fixture (``game/``): 6 games x 16 plays, WR/TE route efforts tracked at
10 Hz with a planted within-player effect of leverage on log max route speed
(``beta = 0.05`` per 1 SD leverage).  ``game/null/`` repeats the same schedule
with ``beta = 0``; ``game/confounded/`` makes leverage strongly correlated with
game time and cumulative load and sets ``beta = 0``.

Every file matches the real column names and units; the same readers work on
both.  Generation is deterministic for a given seed (byte-identical re-runs).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_SEED = 20270107
DRILL = "FORTY_YARD_DASH"
ROUTES = ["GO", "HITCH", "CROSS", "IN", "OUT", "POST", "CORNER", "SLANT",
          "FLAT", "WHEEL"]
TEAMS = ["AAA", "BBB", "CCC", "DDD"]
COLLEGES = ["Alabama", "Ohio State", "Georgia", "LSU", "Michigan", "Clemson",
            "Texas", "Oregon", "Penn State", "USC"]

COMBINE_TRACKING_COLS = ["draft_year", "event_id", "nfl_id", "entity_type",
                         "time", "drill_type", "drill_name", "attempt", "x", "y",
                         "s", "a", "dis", "dir"]
COMBINE_RESULTS_COLS = ["draft_year", "nfl_id", "combine_position",
                        "combine_height", "combine_weight", "hand_size",
                        "arm_length", "wing_span", "ten_yd_split", "forty",
                        "vertical", "broad_jump", "three_cone", "short_shuttle",
                        "bench_reps", "ngs_athleticism_score",
                        "ngs_college_production_score", "ngs_final_score"]
PLAYERS_COLS = ["nfl_id", "display_name", "draft_year", "nfl_position",
                "birth_date", "college_name", "college_conference",
                "draft_round", "draft_pick_within_round", "draft_overall_pick"]
GAMES_COLS = ["game_id", "game_key", "season", "season_type", "week",
              "home_team_abbr", "visitor_team_abbr"]
TRACKING_COLS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
                 "dis", "o", "dir", "event"]
PLAYER_PLAY_COLS = [
    "game_id", "play_id", "team_abbr", "nfl_id", "lined_up_position",
    "play_description", "quarter", "down", "yards_to_go", "possession_team",
    "yardline_side", "yardline_number", "game_clock", "pre_snap_home_score",
    "pre_snap_visitor_score", "play_direction",
    "pre_snap_home_team_win_probability",
    "pre_snap_visitor_team_win_probability",
    "home_team_win_probability_added", "visitor_team_win_probility_added",
    "expected_points_added", "expected_points", "offense_formation",
    "receiver_alignment", "pass_result", "pass_length",
    "play_nullified_by_penalty", "penalty_yards", "pre_penalty_yards_gained",
    "yards_gained", "team_coverage_man_zone", "team_coverage_type",
    "in_motion_at_ball_snap", "target", "rec_yards", "rush_yards",
    "yards_after_catch", "fumble", "fumble_lost", "route_ran",
    "separation_at_pass_forward", "cushion", "expected_yards_after_catch",
    "dropback_duration", "extended_sack_allowed", "pass_rushers_encountered",
    "peak_pressure_probability_allowed", "pressure_allowed", "sack_allowed",
    "time_to_pressure_allowed", "quick_pressure", "unblocked_pressure",
    "blitzing", "player_get_off", "sack", "tackle", "assist",
    "caused_forced_fumble", "recovered_fumble", "tackle_for_loss",
    "time_to_pressure", "time_to_qb_hurry", "coverage_assignment",
    "coverage_assignment_at_snap",
]


# --------------------------------------------------------------------------- #
# Small deterministic helpers
# --------------------------------------------------------------------------- #
def _f(value: float, nd: int = 3) -> str:
    return f"{float(value):.{nd}f}"


def _ts(moment: datetime) -> str:
    """Real CSV format: 2025-03-02T18:56:38.200 (millisecond precision)."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _write_csv(path: Path, fieldnames, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    tmp.replace(path)


def _clip(value, low, high):
    return max(low, min(high, value))


def _wp_from_leverage(lev: float) -> float:
    """Solve 4 p (1-p) = lev for p in [0, 0.5]."""
    return 0.5 * (1.0 - math.sqrt(max(0.0, 1.0 - float(lev))))


def _fmt_clock(seconds_remaining: int) -> str:
    seconds_remaining = max(0, min(900, int(seconds_remaining)))
    return f"{seconds_remaining // 60:02d}:{seconds_remaining % 60:02d}"


# --------------------------------------------------------------------------- #
# Roster
# --------------------------------------------------------------------------- #
def build_roster(n_players: int, seed: int):
    """60 players: per 15-player team 10 WR, 3 TE, 2 CB."""
    rng = random.Random(seed)
    positions = ["WR"] * 10 + ["TE"] * 3 + ["CB"] * 2
    means = {"WR": 9.55, "TE": 8.85, "CB": 9.40}
    sds = {"WR": 0.55, "TE": 0.50, "CB": 0.50}
    heights = {"WR": (72.0, 2.0), "TE": (76.0, 1.5), "CB": (71.0, 1.5)}
    weights = {"WR": (200, 12), "TE": (245, 10), "CB": (190, 8)}
    players = []
    idx = 0
    while idx < n_players:
        team = TEAMS[len(players) // 15 % len(TEAMS)]
        pos = positions[idx % 15]
        idx += 1
        nfl_id = 900000 + idx
        vmax = _clip(rng.gauss(means[pos], sds[pos]), 6.5, 11.7)
        tau = _clip(math.exp(rng.gauss(math.log(1.35), 0.18)), 0.7, 2.6)
        h_mean, h_sd = heights[pos]
        w_mean, w_sd = weights[pos]
        undrafted = idx % 5 == 0
        players.append({
            "nfl_id": nfl_id,
            "team": team,
            "position": pos,
            "draft_year": 2023 + (idx % 3),
            "vmax": round(vmax, 6),
            "tau": round(tau, 6),
            "height": round(_clip(rng.gauss(h_mean, h_sd), 66, 80), 3),
            "weight": int(_clip(round(rng.gauss(w_mean, w_sd)), 160, 280)),
            "hand_size": round(_clip(rng.gauss(9.3, 0.6), 7.5, 11.5), 2),
            "arm_length": round(_clip(rng.gauss(31.5, 1.5), 27, 36), 2),
            "wing_span": round(_clip(rng.gauss(76.5, 2.0), 68, 84), 2),
            "forty": round(_clip(4.60 + (9.55 - vmax) * 0.22
                                 + rng.gauss(0, 0.08), 4.20, 5.60), 2),
            "college": COLLEGES[idx % len(COLLEGES)],
            "birth": date(2000, 1, 1) + timedelta(days=(idx * 37) % 900),
            "round": "NA" if undrafted else str(1 + idx % 7),
            "pick": "NA" if undrafted else str(1 + (idx * 13) % 32),
            "overall": "NA" if undrafted else str(1 + idx),
        })
    return players


# --------------------------------------------------------------------------- #
# Combine fixture
# --------------------------------------------------------------------------- #
def _simulate_dash(vmax: float, tau: float, n_frames: int | None = None):
    """Return (t, x, v, a) samples at 100 ms until 40 yd is reached."""
    dt = 0.1
    t = 0.0
    out = []
    while True:
        v = vmax * (1.0 - math.exp(-t / tau))
        x = vmax * (t - tau * (1.0 - math.exp(-t / tau)))
        a = (vmax / tau) * math.exp(-t / tau)
        out.append((t, x, v, a))
        if (x >= 40.0 and t > 1.0) or (n_frames and len(out) >= n_frames):
            break
        t = round(t + dt, 3)
    return out


def generate_combine(out_dir: Path, players, seed: int) -> dict:
    rng = random.Random(seed)
    tracking_rows, result_rows, truth_rows = [], [], []
    base_time = datetime(2025, 2, 28, 18, 0, 0)

    # Attempt plan: first 14 players get one attempt, the rest get two.
    plans = []
    for i, p in enumerate(players):
        count = 1 if i < 14 else 2
        labels = sorted(rng.sample([1, 2, 3, 4], count))
        plans.append(labels)
    # Explicit duplicate: player index 1 repeats its final attempt number.
    duplicate_player = players[1]["nfl_id"]
    duplicate_label = plans[1][-1]

    for i, p in enumerate(players):
        for j, attempt in enumerate(plans[i]):
            reps = [(i, j, attempt, False)]
            if p["nfl_id"] == duplicate_player and attempt == duplicate_label:
                reps.append((i, j, attempt, True))
            for (_, _, att, is_dup) in reps:
                t0 = base_time + timedelta(
                    seconds=(p["nfl_id"] - 900000) * 47 + j * 900
                    + (30.0 if is_dup else 0.0))
                frames = _simulate_dash(p["vmax"], p["tau"])
                # Planted spike: three specific players, one peak frame > 12 yd/s.
                spike_idx = None
                if p["nfl_id"] in (900003, 900017, 900041):
                    peak = max(range(len(frames)), key=lambda k: frames[k][2])
                    spike_idx = peak
                # Planted gap > 0.5 s inside the peak window.
                gap_idx = None
                if p["nfl_id"] == 900009:
                    peak = max(range(len(frames)), key=lambda k: frames[k][2])
                    # Place the gap just before the peak so it is inside the
                    # peak window (the raw peak is the final frame of a dash).
                    gap_idx = max(0, peak - 2)

                shift = 0.0
                for k, (t, x, v, a) in enumerate(frames):
                    if gap_idx is not None and k == gap_idx + 1:
                        shift = 0.6
                    moment = t0 + timedelta(seconds=round(t + shift, 3))
                    s_val = v + rng.gauss(0, 0.06)
                    if spike_idx is not None and k == spike_idx:
                        s_val = 12.6 + rng.random() * 0.8
                    tracking_rows.append({
                        "draft_year": p["draft_year"],
                        "event_id": f"{t0:%Y%m%d%H%M%S}_{int(t0.timestamp()*1000)}_{i:03d}",
                        "nfl_id": p["nfl_id"], "entity_type": "PLAYER",
                        "time": _ts(moment), "drill_type": DRILL,
                        "drill_name": DRILL, "attempt": att,
                        "x": _f(x + rng.gauss(0, 0.05)),
                        "y": _f(0.02 * math.sin(t) + rng.gauss(0, 0.04)),
                        "s": _f(max(0.0, s_val)),
                        "a": _f(max(0.0, a + rng.gauss(0, 0.08))),
                        "dis": _f(x / 36.7),   # mimics the real file's unresolved scale
                        "dir": _f(90.0 + rng.gauss(0, 0.8)),
                    })
                # BALL entity rows (real file contains them; never filter-free).
                for k in range(4):
                    moment = t0 + timedelta(seconds=round(0.2 * k, 3))
                    tracking_rows.append({
                        "draft_year": p["draft_year"],
                        "event_id": f"{t0:%Y%m%d%H%M%S}_{int(t0.timestamp()*1000)}_{i:03d}",
                        "nfl_id": p["nfl_id"], "entity_type": "BALL",
                        "time": _ts(moment), "drill_type": DRILL,
                        "drill_name": DRILL, "attempt": att,
                        "x": _f(0.0), "y": _f(0.0),
                        "s": _f(min(25.5, 2.2 * k + rng.gauss(0, 0.2))),
                        "a": _f(min(44.0, 4.0 * k + rng.gauss(0, 0.4))),
                        "dis": _f(0.0), "dir": _f(0.0),
                    })
                truth_rows.append({
                    "nfl_id": p["nfl_id"], "draft_year": p["draft_year"],
                    "position": p["position"], "attempt": att,
                    "true_vmax_yds": _f(p["vmax"], 6),
                    "true_tau_s": _f(p["tau"], 6),
                    "n_frames": len(frames),
                    "flags": "duplicate_attempt" if is_dup else "",
                })

    # combine_results (table-stakes drill results)
    for p in players:
        result_rows.append({
            "draft_year": p["draft_year"], "nfl_id": p["nfl_id"],
            "combine_position": p["position"],
            "combine_height": _f(p["height"], 3),
            "combine_weight": p["weight"], "hand_size": _f(p["hand_size"], 2),
            "arm_length": _f(p["arm_length"], 2), "wing_span": _f(p["wing_span"], 2),
            "ten_yd_split": _f(1.55 + (9.55 - p["vmax"]) * 0.06, 2),
            "forty": _f(p["forty"], 2),
            "vertical": _f(_clip(rng.gauss(34, 3), 24, 45), 1),
            "broad_jump": str(int(_clip(rng.gauss(120, 8), 95, 145))),
            "three_cone": "NA" if p["nfl_id"] % 4 == 0 else _f(rng.gauss(7.0, 0.3), 2),
            "short_shuttle": "NA" if p["nfl_id"] % 3 == 0 else _f(rng.gauss(4.3, 0.2), 2),
            "bench_reps": "NA" if p["position"] == "CB" else str(int(_clip(rng.gauss(15, 5), 0, 30))),
            "ngs_athleticism_score": int(_clip(rng.gauss(70, 12), 20, 99)),
            "ngs_college_production_score": str(int(_clip(rng.gauss(65, 15), 1, 99))),
            "ngs_final_score": str(int(_clip(rng.gauss(68, 12), 1, 99))),
        })

    combine = out_dir / "combine"
    _write_csv(combine / "combine_tracking.csv", COMBINE_TRACKING_COLS, tracking_rows)
    _write_csv(combine / "combine_results.csv", COMBINE_RESULTS_COLS, result_rows)
    _write_csv(combine / "players.csv", PLAYERS_COLS, _player_rows(players))
    _write_csv(combine / "ground_truth.csv",
               ["nfl_id", "draft_year", "position", "attempt", "true_vmax_yds",
                "true_tau_s", "n_frames", "flags"], truth_rows)
    return {"tracking_rows": len(tracking_rows), "players": len(players),
            "attempts": len(truth_rows)}


def _player_rows(players):
    rows = []
    for p in players:
        rows.append({
            "nfl_id": p["nfl_id"],
            "display_name": f"Synthetic {p['position']} {p['nfl_id'] - 900000:03d}",
            "draft_year": p["draft_year"], "nfl_position": p["position"],
            "birth_date": p["birth"].isoformat(), "college_name": p["college"],
            "college_conference": "Synthetic Conference",
            "draft_round": p["round"], "draft_pick_within_round": p["pick"],
            "draft_overall_pick": p["overall"],
        })
    return rows


# --------------------------------------------------------------------------- #
# Game fixture
# --------------------------------------------------------------------------- #
def _tracking_for_effort(game_id, play_id, nfl_id, route, vmax_eff, t0,
                         frames_n, hz, rng):
    """Smooth acceleration to a planted max speed along a route direction."""
    dt = 1.0 / hz
    tau = 0.55
    ux, uy = {
        "GO": (1.0, 0.06), "HITCH": (1.0, 0.0), "CROSS": (0.72, 0.69),
        "IN": (0.60, -0.80), "OUT": (0.60, 0.80), "POST": (0.80, 0.60),
        "CORNER": (0.80, -0.60), "SLANT": (0.75, -0.66), "FLAT": (0.35, 0.94),
        "WHEEL": (0.50, 0.87),
    }[route]
    norm = math.hypot(ux, uy)
    ux, uy = ux / norm, uy / norm
    x0, y0 = 50.0, 26.65
    rows = []
    for k in range(frames_n):
        t = k * dt
        v = vmax_eff * (1.0 - math.exp(-t / tau))
        dist = vmax_eff * (t - tau * (1.0 - math.exp(-t / tau)))
        a = (vmax_eff / tau) * math.exp(-t / tau)
        moment = t0 + timedelta(seconds=round(t, 3))
        event = "ball_snap" if k == 0 else ("pass_forward" if k == frames_n - 1 else "")
        rows.append({
            "game_id": game_id, "play_id": play_id, "nfl_id": nfl_id,
            "time": _ts(moment),
            "x": _f(x0 + ux * dist + rng.gauss(0, 0.03)),
            "y": _f(y0 + uy * dist + rng.gauss(0, 0.03)),
            "s": _f(max(0.0, v + rng.gauss(0, 0.02))),
            "a": _f(max(0.0, a + rng.gauss(0, 0.05))),
            "dis": _f(dist / 36.7),
            "o": _f(dist), "dir": _f((math.degrees(math.atan2(uy, ux))) % 360, 2),
            "event": event,
        })
    return rows


def _build_world(out_root: Path, players, seed: int, *, world: str, beta: float,
                 confounded: bool, n_games: int, plays_per_game: int,
                 frames_n: int, hz: int, within_sd: float,
                 confounded_corr: float, beta_time: float = 0.0) -> dict:
    """Two-phase generation so the null/confounded worlds are true nulls.

    Phase 1 draws the play schedule and receiver efforts.  Phase 2 removes any
    within-player correlation between leverage and the noise term when
    ``beta == 0``, so the null world has *exactly* no planted effect to find.
    The confounded world additionally plants a within-player decline in speed
    with elapsed game time (``beta_time``); because leverage is strongly
    correlated with time there, a naive leverage model sees a spurious effect
    that the time/load-orthogonalised model must remove.
    """
    rng = random.Random(seed)
    by_team = {}
    for p in players:
        by_team.setdefault(p["team"], []).append(p)
    receivers = {t: [p for p in ps if p["position"] in ("WR", "TE")]
                 for t, ps in by_team.items()}
    defenders = {t: [p for p in ps if p["position"] == "CB"]
                 for t, ps in by_team.items()}

    pp_rows, specs = [], []
    base = datetime(2023, 9, 7, 17, 0, 0)
    for g in range(n_games):
        game_id = 2023090700 + g * 7
        home, away = TEAMS[g % 4], TEAMS[(g + 1) % 4]
        for play in range(plays_per_game):
            play_id = 100 + play
            time_score = play / max(1, plays_per_game - 1)
            if confounded:
                lev = _clip(0.10 + 0.85 * time_score + rng.gauss(0, 0.13),
                            0.01, 0.99)
            else:
                lev = _clip(0.5 + 0.28 * rng.gauss(0, 1), 0.01, 0.99)
            p_home = _wp_from_leverage(lev)
            if rng.random() < 0.5:
                p_home = 1.0 - p_home
            quarter = 1 + min(3, play * 4 // plays_per_game)
            secs_left = 900 - (play % max(1, plays_per_game // 4)) * 170 \
                - rng.randint(0, 100)
            down = 1 + play % 4
            possession = home if play % 2 == 0 else away
            other = away if possession == home else home
            off_players = receivers[possession]
            three = [off_players[(play * 3 + k) % len(off_players)]
                     for k in range(3)]
            defender = defenders[other][play % len(defenders[other])]
            route = ROUTES[(g * plays_per_game + play) % len(ROUTES)]
            score_home = 10 + (play * 3) % 21
            score_vis = 7 + (play * 5) % 21
            clock = _fmt_clock(secs_left)
            t0 = base + timedelta(days=g, minutes=play * 3, seconds=5)
            common = {
                "game_id": game_id, "play_id": play_id,
                "quarter": quarter, "down": down,
                "yards_to_go": [3, 5, 7, 10][play % 4],
                "possession_team": possession, "yardline_side": possession,
                "yardline_number": 20 + (play * 7) % 60,
                "game_clock": clock,
                "pre_snap_home_score": score_home,
                "pre_snap_visitor_score": score_vis,
                "play_direction": "right" if play % 2 == 0 else "left",
                "pre_snap_home_team_win_probability": _f(p_home, 12),
                "pre_snap_visitor_team_win_probability": _f(1.0 - p_home, 12),
                "home_team_win_probability_added": _f(0.0, 12),
                "visitor_team_win_probility_added": _f(0.0, 12),
                "expected_points_added": _f(0.0, 12),
                "expected_points": _f(1.0 + (play % 5) * 0.5, 12),
                "offense_formation": ["SHOTGUN", "SINGLEBACK", "PISTOL"][play % 3],
                "play_nullified_by_penalty": "N", "penalty_yards": 0,
                "pre_penalty_yards_gained": 0, "yards_gained": 0,
                "team_coverage_man_zone": "MAN_COVERAGE" if play % 2 else "ZONE_COVERAGE",
                "team_coverage_type": "COVER_1_MAN" if play % 2 else "COVER_3_ZONE",
                "in_motion_at_ball_snap": "FALSE",
            }

            for k, p in enumerate(three):
                is_target = (k == 0 and play % 3 != 0)
                specs.append({
                    "game_id": game_id, "play_id": play_id, "p": p,
                    "route": route, "quarter": quarter,
                    "seconds_remaining": int(secs_left), "lev": lev,
                    "ceiling": 0.92 * p["vmax"],
                    "eps": rng.gauss(0, within_sd), "is_target": is_target,
                    "wp_home": p_home,
                    "t0": t0, "play_index": play,
                })
                pp_rows.append(dict(common, **{
                    "team_abbr": possession, "nfl_id": p["nfl_id"],
                    "lined_up_position": "SLOT_WR" if p["position"] == "WR"
                    and k == 1 else p["position"],
                    "play_description": f"Synthetic {route} route, play {play}",
                    "receiver_alignment": "SLOT" if k == 1 else "OUTSIDE",
                    "pass_result": "C" if is_target and play % 2 == 0 else (
                        "I" if is_target else ""),
                    "pass_length": str(5 + (play * 3) % 20) if is_target else "NA",
                    "target": "TRUE" if is_target else "FALSE",
                    "route_ran": route,
                    "separation_at_pass_forward": _f(rng.gauss(2.2, 0.6), 2),
                    "cushion": _f(_clip(rng.gauss(5.0, 1.2), 0.5, 12), 2),
                    "expected_yards_after_catch": 0.0,
                    "dropback_duration": _f(_clip(rng.gauss(2.6, 0.4), 1.5, 4.5), 2),
                    "extended_sack_allowed": 0.0,
                    "pass_rushers_encountered": str(4 + play % 3),
                    "peak_pressure_probability_allowed": 0.0,
                    "pressure_allowed": "FALSE", "sack_allowed": 0.0,
                    "time_to_pressure_allowed": 0.0, "quick_pressure": "FALSE",
                    "unblocked_pressure": "FALSE", "blitzing": "FALSE",
                    "player_get_off": _f(_clip(rng.gauss(0.9, 0.15), 0.4, 1.6), 2),
                    "sack": 0.0, "tackle": 0, "assist": 0,
                    "caused_forced_fumble": 0, "recovered_fumble": "FALSE",
                    "tackle_for_loss": "FALSE", "time_to_pressure": 0.0,
                    "time_to_qb_hurry": 0.0, "coverage_assignment": "NA",
                    "coverage_assignment_at_snap": "NA",
                }, **{
                    "rec_yards": 0, "rush_yards": 0, "yards_after_catch": 0,
                    "fumble": 0, "fumble_lost": 0,
                }))
            pp_rows.append(dict(common, **{
                "team_abbr": other, "nfl_id": defender["nfl_id"],
                "lined_up_position": "CB",
                "play_description": f"Synthetic coverage, play {play}",
                "receiver_alignment": "NA", "pass_result": "", "pass_length": "NA",
                "target": "FALSE", "route_ran": "NA",
                "separation_at_pass_forward": "NA", "cushion": "NA",
                "expected_yards_after_catch": "NA", "dropback_duration": "NA",
                "extended_sack_allowed": "NA", "pass_rushers_encountered": "NA",
                "peak_pressure_probability_allowed": "NA",
                "pressure_allowed": "NA", "sack_allowed": "NA",
                "time_to_pressure_allowed": "NA", "quick_pressure": "NA",
                "unblocked_pressure": "NA", "blitzing": "NA",
                "player_get_off": "NA", "sack": "NA", "tackle": "NA",
                "assist": "NA", "caused_forced_fumble": "NA",
                "recovered_fumble": "NA", "tackle_for_loss": "NA",
                "time_to_pressure": "NA", "time_to_qb_hurry": "NA",
                "coverage_assignment": "NA", "coverage_assignment_at_snap": "NA",
                "rec_yards": "NA", "rush_yards": "NA", "yards_after_catch": "NA",
                "fumble": "NA", "fumble_lost": "NA",
            }))

    # Phase 2: standardise leverage within player and (for true nulls) remove
    # any within-player leverage/noise correlation, so beta=0 really is zero.
    by_player = {}
    for s in specs:
        by_player.setdefault(s["p"]["nfl_id"], []).append(s)
    for rows in by_player.values():
        levs = [r["lev"] for r in rows]
        mean_lev = sum(levs) / len(levs)
        var = sum((x - mean_lev) ** 2 for x in levs)
        sd = math.sqrt(var / len(levs)) if var > 0 else 0.0
        slope = 0.0
        if beta == 0.0 and var > 0:
            slope = sum((r["lev"] - mean_lev) * r["eps"] for r in rows) / var
        for r in rows:
            minutes = (r["quarter"] - 1) * 900 + (900 - r["seconds_remaining"])
            r["_minutes"] = minutes
        times = [r["_minutes"] for r in rows]
        mean_time = sum(times) / len(times)
        time_sd = math.sqrt(sum((x - mean_time) ** 2 for x in times) / len(times))
        for r in rows:
            r["z_std"] = ((r["lev"] - mean_lev) / sd) if sd > 0 else 0.0
            z_time = ((r["_minutes"] - mean_time) / time_sd) if time_sd > 0 else 0.0
            r["z_time_std"] = z_time
            r["signal"] = beta * r["z_std"] + beta_time * z_time
            r["eps_used"] = r["eps"] - slope * (r["lev"] - mean_lev)
            r["true_log"] = math.log(r["ceiling"]) + r["signal"] + r["eps_used"]

    # Phase 3: realise the tracking from the planted max speeds.
    tracking_rows, efforts = [], []
    for s in specs:
        max_speed = _clip(math.exp(s["true_log"]), 3.0, 12.0)
        tracking_rows.extend(_tracking_for_effort(
            s["game_id"], s["play_id"], s["p"]["nfl_id"], s["route"],
            max_speed, s["t0"], frames_n, hz, rng))
        efforts.append({
            "game_id": s["game_id"], "play_id": s["play_id"],
            "nfl_id": s["p"]["nfl_id"], "position": s["p"]["position"],
            "route": s["route"], "quarter": s["quarter"],
            "seconds_remaining": s["seconds_remaining"],
            "leverage": round(s["lev"], 8),
            "leverage_z_std": round(s["z_std"], 8),
            "wp_home_pre": round(float(s["wp_home"]), 12),
            "ceiling_speed_yds": round(s["ceiling"], 8),
            "planted_signal": round(s["signal"], 8),
            "planted_time_signal": round(beta_time * s["z_time_std"], 8),
            "max_speed_yds": round(max_speed, 8),
            "true_log_speed": round(s["true_log"], 8),
            "is_target": bool(s["is_target"]),
            "play_index": s["play_index"],
        })

    game_dir = out_root / "game" if world == "planted" else out_root / "game" / world
    _write_csv(game_dir / "player_play.csv", PLAYER_PLAY_COLS, pp_rows)
    _write_csv(game_dir / "game_tracking.csv", TRACKING_COLS, tracking_rows)
    _write_csv(game_dir / "players.csv", PLAYERS_COLS, _player_rows(players))
    _write_csv(game_dir / "games.csv", GAMES_COLS, [
        {"game_id": 2023090700 + g * 7, "game_key": 60000 + g, "season": 2023,
         "season_type": "POST" if g == n_games - 1 else "REG", "week": g + 1,
         "home_team_abbr": TEAMS[g % 4],
         "visitor_team_abbr": TEAMS[(g + 1) % 4]}
        for g in range(n_games)])

    levs = [e["leverage"] for e in efforts]
    times = [(e["quarter"] - 1) * 900 + (900 - e["seconds_remaining"])
             for e in efforts]
    loads = [e["play_index"] for e in efforts]

    def _corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        return cov / math.sqrt(va * vb) if va and vb else 0.0

    corr_time = _corr(levs, times)
    corr_load = _corr(levs, loads)
    truth = {
        "world": world,
        "not_data": "Synthetic fixture. Never interpret as a result.",
        "beta_leverage_log_speed_per_sd": beta,
        "beta_time_log_speed_per_sd": beta_time,
        "within_player_sd": within_sd,
        "n_efforts": len(efforts),
        "n_players": len(players),
        "n_games": n_games,
        "plays_per_game": plays_per_game,
        "leverage_definition": "4 * wp_pre * (1 - wp_pre)",
        "realised_corr_leverage_time": round(corr_time, 6),
        "realised_corr_leverage_load": round(corr_load, 6),
        "planted_ceiling_frac_of_combine_vmax": 0.92,
        "players": {str(p["nfl_id"]): {
            "position": p["position"], "combine_vmax_yds": p["vmax"],
            "planted_ceiling_yds": round(0.92 * p["vmax"], 6)}
            for p in players if p["position"] in ("WR", "TE")},
        "efforts": efforts,
    }
    if confounded and corr_time < confounded_corr - 0.10:
        raise AssertionError(
            f"confounded fixture corr={corr_time:.3f} below target "
            f"{confounded_corr:.3f}; adjust the generator")
    (game_dir / "ground_truth.json").write_text(
        json.dumps(truth, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"world": world, "efforts": len(efforts),
            "tracking_rows": len(tracking_rows),
            "corr_leverage_time": round(corr_time, 6),
            "corr_leverage_load": round(corr_load, 6)}


# Entry points
# --------------------------------------------------------------------------- #
def generate(out_root: str | Path, seed: int = DEFAULT_SEED, n_players: int = 60,
             n_games: int = 6, plays_per_game: int = 16,
             frames_n: int = 28, hz: int = 10,
             beta: float = 0.05, within_sd: float = 0.04,
             confounded_corr: float = 0.85,
             confounded_beta_time: float = -0.06) -> dict:
    out_root = Path(out_root)
    players = build_roster(n_players, seed + 1)
    summary = {}
    summary["combine"] = generate_combine(out_root, players, seed + 2)
    common = dict(players=players, seed=seed + 3, n_games=n_games,
                  plays_per_game=plays_per_game, frames_n=frames_n, hz=hz,
                  within_sd=within_sd, confounded_corr=confounded_corr)
    summary["planted"] = _build_world(out_root, beta=beta, confounded=False,
                                      world="planted", **common)
    summary["null"] = _build_world(out_root, beta=0.0, confounded=False,
                                   world="null", **common)
    summary["confounded"] = _build_world(out_root, beta=0.0, confounded=True,
                                         world="confounded",
                                         beta_time=confounded_beta_time, **common)
    _write_readme(out_root, summary)
    return summary


def _write_readme(out_root: Path, summary: dict) -> None:
    text = f"""# Synthetic LC fixtures — NOT DATA

Generated by `src/synthetic_lc_fixture.py` (deterministic; same seed =>
byte-identical files). Do not interpret any number here as a result.

## Combine (`combine/`)

* `combine_tracking.csv` — 40-yard-dash frames at 100 ms, `entity_type` PLAYER
  and BALL. True per-attempt `vmax`/`tau` are in `ground_truth.csv`.
* `combine_results.csv`, `players.csv` — schema-identical to the real files.
* Planted on purpose: {summary['combine']['players']} players
  ({summary['combine']['attempts']} attempts incl. one duplicate), players with
  1 and 2 attempts, a few frames above 12 yd/s, one duplicated attempt number,
  one gap > 0.5 s inside the peak window, BALL rows.

## Game worlds

* `game/` — planted world: `beta = 0.05` log-speed per 1 SD leverage.
* `game/null/` — same schedule, `beta = 0`.
* `game/confounded/` — `beta = 0` but speed declines with game time
  (`beta_time = {summary['confounded'].get('beta_time', '')}`), and leverage is
  strongly correlated with game time and cumulative load. A naive leverage
  model sees a spurious effect; the orthogonalised model must remove it.

Each world has `player_play.csv`, `game_tracking.csv`, `players.csv`,
`games.csv`, `ground_truth.json`. `ground_truth.json` lists every effort's
leverage, planted ceiling and realised max speed.

Regenerate: `python src/synthetic_lc_fixture.py --force`.
"""
    (out_root / "README.md").write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None,
                        help="output root (default: config bdb27_lc.paths.synthetic)")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--players", type=int, default=None)
    parser.add_argument("--games", type=int, default=None)
    parser.add_argument("--plays-per-game", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        import lc_common as C
        cfg = C.load_config(args.config)
        fx = C.lc(cfg)["fixture"]
        default_out = C.resolve_paths(cfg, "sample")["synthetic"]
    except Exception:  # standalone use outside the repo
        fx, default_out = {}, Path(__file__).resolve().parents[1] / \
            "fixtures" / "synthetic" / "lc"
    out = Path(args.out) if args.out else default_out
    summary = generate(
        out,
        seed=args.seed if args.seed is not None else fx.get("seed", DEFAULT_SEED),
        n_players=args.players if args.players is not None else fx.get("n_players", 60),
        n_games=args.games if args.games is not None else fx.get("n_games", 6),
        plays_per_game=(args.plays_per_game if args.plays_per_game is not None
                        else fx.get("plays_per_game", 16)),
        frames_n=fx.get("frames_per_effort", 28),
        hz=fx.get("tracking_hz", 10),
        beta=fx.get("planted_beta_leverage", 0.05),
        within_sd=fx.get("planted_within_player_sd", 0.04),
        confounded_corr=fx.get("confounded_leverage_time_corr", 0.85),
        confounded_beta_time=fx.get("confounded_beta_time_log_speed_per_sd", -0.06),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
