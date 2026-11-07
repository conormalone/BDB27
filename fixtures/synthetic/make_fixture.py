"""Generate the deterministic synthetic fixture for the Turn Coverage pipeline.

Small, schema-compliant, and planted: the generator knows every turn it puts in,
so Phases 2-3 can be tested by recovery rather than by eyeballing. Written to
``fixtures/synthetic/`` and committed (it is data, but it is synthetic and tiny).

What is planted
---------------
* two Combine-like drills with real geometry: ``THREE_CONE_DRILL`` (3 cones, an
  L) and ``SHORT_SHUTTLE`` (2 turn lines 10 yd apart);
* a per-player lateral acceleration capacity, which sets the corner speed and
  therefore the speed-retention of every turn — the quantity Phase 3 estimates;
* one sub-maximal attempt per player (speed scaled by 0.8) and 5% dropped frames;
* 10 Hz sampling, position noise, and a ``dis`` column encoded exactly like the
  real file (dis = (whole_yards/30)^2, see notes/schema.md);
* a game fixture with a planted cut at a known location and an ``o`` column.

Run:  python fixtures/synthetic/make_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20270106
DT = 0.1
OUT = Path(__file__).resolve().parent


def densify(waypoints: np.ndarray, radii: np.ndarray, step: float = 0.05) -> np.ndarray:
    """Polyline with corners rounded by a quadratic Bezier of the given radius."""
    pts = [waypoints[0]]
    for i in range(1, len(waypoints) - 1):
        p0, p1, p2 = waypoints[i - 1], waypoints[i], waypoints[i + 1]
        u = p0 - p1
        v = p2 - p1
        lu, lv = np.linalg.norm(u), np.linalg.norm(v)
        if lu < 1e-9 or lv < 1e-9:
            continue
        u, v = u / lu, v / lv
        d = min(radii[i] * 1.2, 0.45 * lu, 0.45 * lv)
        a, b = p1 + u * d, p1 + v * d
        for t in np.linspace(0, 1, 12):
            pts.append((1 - t) ** 2 * a + 2 * (1 - t) * t * p1 + t ** 2 * b)
    pts.append(waypoints[-1])
    pts = np.array(pts)
    # resample uniformly along the polyline
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    grid = np.arange(0, s[-1], step)
    return np.column_stack([np.interp(grid, s, pts[:, 0]), np.interp(grid, s, pts[:, 1])]), grid


def speed_profile(arc: np.ndarray, limits: np.ndarray, a_max: float) -> np.ndarray:
    """Trapezoidal speed profile along arc length, respecting local speed limits."""
    ds = np.diff(arc, prepend=arc[0])
    v = limits.copy()
    for i in range(1, len(v)):
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * a_max * ds[i]))
    for i in range(len(v) - 2, -1, -1):
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * a_max * ds[i + 1]))
    return np.maximum(v, 0.2)


def integrate_time(arc: np.ndarray, v: np.ndarray) -> np.ndarray:
    ds = np.diff(arc, prepend=arc[0])
    return np.cumsum(ds / v)


def trajectory(waypoints, radii, v_max, a_max, a_lat, scale=1.0):
    path, arc = densify(np.asarray(waypoints, float), np.asarray(radii, float))
    # local speed limit at each corner: v = sqrt(a_lat * r), blended over the arc
    limits = np.full(len(arc), v_max)
    for i in range(1, len(waypoints) - 1):
        corner_r = radii[i]
        v_corner = min(v_max, np.sqrt(a_lat * corner_r * scale ** 2))
        d = np.linalg.norm(path - waypoints[i], axis=1)
        limits = np.minimum(limits, np.maximum(v_corner, 0.3) + 0.12 * d)
    v = speed_profile(arc, limits, a_max) * scale
    t = integrate_time(arc, v)
    return path, t, v


def encode_dis(path: np.ndarray) -> np.ndarray:
    """Mimic the real file: cumulative whole-yard distance, stored as (n/30)^2."""
    step = np.linalg.norm(np.diff(path, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(step)])
    whole = np.floor(cum)  # whole yards, as the vendor encoding implies
    return (whole / 30.0) ** 2


def sample_at(path, t, v, rate_dt=DT, rng=None, drop=0.0, noise=0.05):
    grid = np.arange(0, t[-1], rate_dt)
    x = np.interp(grid, t, path[:, 0])
    y = np.interp(grid, t, path[:, 1])
    sp = np.interp(grid, t, v)
    if noise:
        x = x + rng.normal(0, noise, len(x))
        y = y + rng.normal(0, noise, len(y))
    dx, dy = np.gradient(x), np.gradient(y)
    heading = np.degrees(np.arctan2(dx, dy)) % 360.0
    a = np.gradient(sp, rate_dt)
    keep = np.ones(len(grid), bool)
    if drop:
        keep &= rng.random(len(grid)) > drop
        keep[0] = keep[-1] = True
    return x[keep], y[keep], sp[keep], heading[keep], a[keep], grid[keep]


PLAYERS = [
    # nfl_id, position, height, weight, lateral capacity (yd/s^2)
    (900001, "DL", 75.0, 285.0, 16.0),
    (900002, "DL", 74.0, 270.0, 15.0),
    (900003, "DL", 76.0, 300.0, 14.0),
    (900004, "WR", 72.0, 195.0, 18.0),
    (900005, "WR", 71.0, 185.0, 16.0),
    (900006, "TE", 77.0, 250.0, 15.0),
]

# a 2-yd straight run-up so the first frames are not a heading transient
SHUTTLE = dict(waypoints=[[-2, 0], [5, 0], [-5, 0], [0, 0]], radii=[0, 0.8, 0.8, 0])
THREE_CONE = dict(waypoints=[[0, 0], [5, 0], [5, 5], [0, 0]], radii=[0, 0.8, 0.8, 0])


def build_combine(rng):
    rows, truth = [], {"players": [], "attempts": []}
    for nfl_id, pos, h, w, a_lat in PLAYERS:
        truth["players"].append({"nfl_id": nfl_id, "combine_position": pos,
                                 "lateral_capacity_yd_s2": a_lat})
        for drill, geo, vmax in [("THREE_CONE_DRILL", THREE_CONE, 5.5),
                                 ("SHORT_SHUTTLE", SHUTTLE, 7.0)]:
            for attempt, scale in [(1, 1.0), (2, 0.85)]:  # attempt 2 is sub-maximal
                path, t, v = trajectory(geo["waypoints"], geo["radii"], vmax, 6.0, a_lat, scale)
                x, y, sp, hd, acc, tg = sample_at(path, t, v, rng=rng, drop=0.05)
                event = f"{nfl_id}-{drill}"
                dis = encode_dis(np.column_stack([x, y]))
                n = len(tg)
                rows.append(pd.DataFrame({
                    "draft_year": 2025,
                    "event_id": event,
                    "nfl_id": nfl_id,
                    "entity_type": "PLAYER",
                    "time": pd.to_datetime("2025-03-01 10:00:00") + pd.to_timedelta(tg, unit="s"),
                    "drill_type": "SYNTHETIC",
                    "drill_name": drill,
                    "attempt": attempt,
                    "x": np.round(x, 4), "y": np.round(y, 4),
                    "s": np.round(sp, 4), "a": np.round(acc, 4),
                    "dis": np.round(dis, 6), "dir": np.round(hd, 3),
                }))
                truth["attempts"].append({
                    "event_id": event, "nfl_id": nfl_id, "drill_name": drill,
                    "attempt": attempt, "sub_maximal": attempt == 2,
                    "speed_scale": scale, "frames": int(n),
                    "geometry": geo["waypoints"], "corner_radii": geo["radii"],
                })
    return pd.concat(rows, ignore_index=True), truth


def build_results():
    rows = []
    for nfl_id, pos, h, w, _ in PLAYERS:
        rows.append({"draft_year": 2025, "nfl_id": nfl_id, "combine_position": pos,
                     "combine_height": h, "combine_weight": w,
                     "ten_yd_split": 1.6, "forty": 4.7, "vertical": 33.0,
                     "broad_jump": 115.0, "three_cone": 7.1, "short_shuttle": 4.3,
                     "bench_reps": 20})
    return pd.DataFrame(rows)


def build_players():
    return pd.DataFrame([{"nfl_id": n, "display_name": f"Fixture {n}", "draft_year": 2025,
                          "nfl_position": p, "draft_round": 3, "draft_overall_pick": 80}
                         for n, p, *_ in PLAYERS])


def build_game(rng):
    """Four synthetic game plays with one planted cut each."""
    rows, truth = [], []
    for k in range(4):
        nfl_id = PLAYERS[k][0]
        path = np.array([[0, 0], [8, 0], [8, 6]], float)
        radii = np.array([0, 1.2, 0])
        p, t, v = trajectory(path, radii, 7.5, 5.0, 9.0)
        x, y, sp, hd, acc, tg = sample_at(p, t, v, rng=rng, drop=0.02)
        dis = encode_dis(np.column_stack([x, y]))
        o = (hd + rng.normal(0, 6, len(hd))) % 360.0
        rows.append(pd.DataFrame({
            "game_id": 2023999000 + k, "play_id": 100 + k, "nfl_id": nfl_id,
            "time": pd.to_datetime("2023-09-10 18:00:00") + pd.to_timedelta(tg, unit="s"),
            "x": np.round(x, 3), "y": np.round(y, 3), "s": np.round(sp, 3),
            "a": np.round(acc, 3), "dis": np.round(dis, 6),
            "o": np.round(o, 3), "dir": np.round(hd, 3), "event": "NA",
        }))
        truth.append({"game_id": 2023999000 + k, "play_id": 100 + k, "nfl_id": nfl_id,
                      "cut_at": [8.0, 0.0], "cut_angle_deg": 90.0})
    return pd.concat(rows, ignore_index=True), truth


def main():
    rng = np.random.default_rng(SEED)
    comb, truth = build_combine(rng)
    game, gtruth = build_game(rng)
    comb.to_csv(OUT / "combine_tracking.csv", index=False, float_format="%.6f")
    build_results().to_csv(OUT / "combine_results.csv", index=False)
    build_players().to_csv(OUT / "players.csv", index=False)
    game.to_csv(OUT / "game_tracking.csv", index=False, float_format="%.6f")
    pd.DataFrame([{"game_id": 2023999000 + k, "play_id": 100 + k, "nfl_id": PLAYERS[k][0],
                   "route_ran": "OUT", "player_get_off": np.nan, "coverage_assignment": np.nan}
                  for k in range(4)]).to_csv(OUT / "player_play.csv", index=False)
    pd.DataFrame([{"game_id": 2023999000 + k, "season": 2023, "season_type": "REG",
                   "week": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"}
                  for k in range(4)]).to_csv(OUT / "games.csv", index=False)
    truth["game_plays"] = gtruth
    truth["seed"] = SEED
    (OUT / "truth.json").write_text(json.dumps(truth, indent=2))
    print(f"wrote {len(comb)} combine rows, {len(game)} game rows to {OUT}")


if __name__ == "__main__":
    main()
