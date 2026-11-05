"""synthetic — schema-compliant fixtures with planted burst and jump effects.

Purpose (TASK.md "Engineering"): the provided game tracking is a sample, so code
paths that need many DL must be exercised on a synthetic set whose true
parameters are known. Every fixture here is deterministic given a seed, writes
real CSVs with the real column names, and records the planted values in
``truth.json`` so a test can check recovery rather than just shape.

Planted structure
-----------------
Each player i has:

* a physical burst parameter ``vmax_i`` (yd/s) and ``tau_i`` (s), and
* a jump ``u_i ~ N(0, jump_sd)`` that shifts get-off *after* the physical part.

Get-off is then ``base + scale * phys_i(d) + u_i + e`` where ``phys`` is the
model-free time to cover the pre-snap depth ``d``. A decomposition of get-off on
``phys`` should therefore recover the planted share of between-player variance
attributable to ``u`` within sampling error.

Nothing here is a model of football. It is a fixture.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Mapping, Sequence

try:
    import common as C
except ImportError:  # pragma: no cover
    from . import common as C  # type: ignore

HZ = 10
FRAME_S = 1.0 / HZ

#: Fixture clocks. The real files carry TIMESTAMP columns, so the fixtures must
#: too — otherwise DuckDB infers DOUBLE and the schema gate (correctly) fails.
COMBINE_BASE = "2024-03-01T18:00:00.000"
GAME_BASE = "2024-09-08T18:00:00.000"


def _stamp(base: str, offset_s: float) -> str:
    """ISO timestamp = base + offset_s, to millisecond precision."""
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(base)
    return (t0 + timedelta(seconds=offset_s)).isoformat(timespec="milliseconds")


def relative_seconds(values) -> list[float]:
    """Turn a sequence of ISO timestamp strings into seconds from the first."""
    from datetime import datetime
    ts = [datetime.fromisoformat(str(v)) for v in values]
    return [(x - ts[0]).total_seconds() for x in ts]


def _speed(vmax: float, tau: float, t: float) -> float:
    """Exponential approach to top speed (the Phase 2 cross-check form)."""
    if t <= 0:
        return 0.0
    return vmax * (1.0 - math.exp(-t / tau))


def _distance(vmax: float, tau: float, t: float) -> float:
    if t <= 0:
        return 0.0
    return vmax * (t - tau * (1.0 - math.exp(-t / tau)))


def physical_time(vmax: float, tau: float, distance: float) -> float:
    """Time to cover ``distance`` yd from rest under the planted profile.

    Solved by bisection on the closed-form distance curve: exact to 1e-6 s and
    free of any dependence on the measurement code under test.
    """
    lo, hi = 0.0, 12.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _distance(vmax, tau, mid) < distance:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------- #
# Combine
# --------------------------------------------------------------------------- #
def make_combine_frames(seed: int = 7, n_players: int = 24,
                        attempts_per: int = 2, rest_s: float = 0.6,
                        noise_yd: float = 0.03) -> tuple[list[dict], dict]:
    """40-yard dash attempts at 10 Hz with per-attempt noise, no jump effect."""
    rng = random.Random(seed)
    rows, truth = [], {"players": {}}
    for i in range(n_players):
        nfl_id = 900000 + i
        vmax = rng.uniform(8.5, 11.0)
        tau = rng.uniform(0.6, 1.4)
        truth["players"][str(nfl_id)] = {"vmax": vmax, "tau": tau,
                                         "t_to_1yd": physical_time(vmax, tau, 1.0),
                                         "t_to_2yd": physical_time(vmax, tau, 2.0)}
        for attempt in range(1, attempts_per + 1):
            attempt_vmax = vmax * rng.gauss(1.0, 0.02)
            attempt_tau = tau * rng.gauss(1.0, 0.05)
            n_rest = int(round(rest_s / FRAME_S))
            total = n_rest + 50
            for k in range(total):
                t = k * FRAME_S
                run_t = max(0.0, t - rest_s)
                s = _speed(attempt_vmax, attempt_tau, run_t)
                x = _distance(attempt_vmax, attempt_tau, run_t)
                rows.append({
                    "draft_year": 2024, "event_id": f"E{i}", "nfl_id": nfl_id,
                    "entity_type": "PLAYER", "time": _stamp(COMBINE_BASE, t),
                    "drill_type": "FORTY_YARD_DASH",
                    "drill_name": "FORTY_YARD_DASH", "attempt": attempt,
                    "x": round(10.0 + x + rng.gauss(0, noise_yd), 4),
                    "y": round(0.0 + rng.gauss(0, noise_yd), 4),
                    "s": round(max(0.0, s + rng.gauss(0, 0.03)), 4),
                    "a": 0.0,
                    "dis": round(max(0.0, s * FRAME_S), 4),
                    "dir": 90.0,
                })
    return rows, truth


# --------------------------------------------------------------------------- #
# Game tracking
# --------------------------------------------------------------------------- #
def make_game_frames(seed: int = 11, n_players: int = 12, snaps_per: int = 30,
                     jump_sd: float = 0.08, burst_sd: float = 0.10,
                     noise_yd: float = 0.04) -> tuple[list[dict], list[dict],
                                                      list[dict], dict]:
    """Per-snap trajectories with a planted jump and a planted physical part.

    Returns (tracking_rows, player_play_rows, games_rows, truth).
    """
    rng = random.Random(seed)
    tracking, player_play, games = [], [], []
    truth = {"players": {}, "snaps": 0}

    games.append({"game_id": 2024000001, "game_key": 1, "season": 2024,
                  "season_type": "REG", "week": 1,
                  "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"})

    play_id = 100
    for i in range(n_players):
        nfl_id = 800000 + i
        vmax = 8.5 + rng.gauss(0.0, burst_sd)
        tau = max(0.3, 0.9 + rng.gauss(0.0, burst_sd))
        jump = rng.gauss(0.0, jump_sd)
        truth["players"][str(nfl_id)] = {
            "vmax": vmax, "tau": tau, "jump": jump,
            "edge": i % 2 == 0,
        }
        for _ in range(snaps_per):
            play_id += 1
            depth = rng.uniform(0.5, 1.5)
            # planted get-off: physical time for that depth + jump + noise
            phys = physical_time(vmax, tau, depth)
            get_off = round(max(0.2, phys + jump + rng.gauss(0, 0.03)), 3)
            los_x = 60.0
            x0 = los_x - depth
            out_dir = 1.0                      # offence moves +x; rusher moves +x
            n_pre = 10
            n_total = n_pre + int(round(2.0 / FRAME_S))
            for k in range(n_total):
                t = (k - n_pre) * FRAME_S      # 0 at the snap
                if t < 0:
                    x = x0 + rng.gauss(0, noise_yd)
                else:
                    # constant acceleration that reaches depth at get_off
                    a = 2.0 * depth / max(get_off, 1e-6) ** 2
                    tt = min(t, get_off)
                    x = x0 + 0.5 * a * tt ** 2
                    if t > get_off:
                        x = x0 + depth + 0.5 * a * get_off * (t - get_off) * 0.2
                    x += rng.gauss(0, noise_yd)
                v = 0.0 if t <= 0 else (2.0 * depth / max(get_off, 1e-6) ** 2) * min(t, get_off)
                tracking.append({
                    "game_id": 2024000001, "play_id": play_id, "nfl_id": nfl_id,
                    "time": _stamp(GAME_BASE, t), "x": round(x, 4), "y": 26.0,
                    "s": round(max(0.0, v + rng.gauss(0, 0.02)), 4),
                    "a": 0.0, "dis": round(v * FRAME_S, 4), "o": 90.0,
                    "dir": 90.0 if out_dir > 0 else 270.0,
                    "event": "ball_snap" if k == n_pre else "",
                })
            press = rng.random() < 0.05
            player_play.append({
                "game_id": 2024000001, "play_id": play_id, "team_abbr": "BBB",
                "nfl_id": nfl_id,
                "lined_up_position": "EDGE" if i % 2 == 0 else "INTERIOR_LINE",
                "quarter": 1, "down": 1, "yards_to_go": 10,
                "possession_team": "AAA", "yardline_side": "AAA",
                "yardline_number": int(round(los_x - 10)), "game_clock": "15:00",
                "pre_snap_home_score": 0, "pre_snap_visitor_score": 0,
                "play_direction": "right", "dropback_duration": 2.8,
                "pass_rushers_encountered": 1,
                "pressure_allowed": (press or None),
                "sack_allowed": (0.0 if press else None),
                "time_to_pressure_allowed": (2.4 if press else None),
                "quick_pressure": (False if press else None),
                "unblocked_pressure": (False if press else None),
                "blitzing": False, "player_get_off": get_off, "sack": 0.0,
                "time_to_pressure": (2.4 if press else None),
                "time_to_qb_hurry": (2.6 if press else None),
            })
            truth["snaps"] += 1
    return tracking, player_play, games, truth


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path: Path, rows: Sequence[Mapping], columns: Sequence[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("NA" if r.get(k) is None else r.get(k))
                        for k in columns})
    return path


GAME_TRACKING_COLS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
                      "dis", "o", "dir", "event"]
PLAYER_PLAY_COLS = ["game_id", "play_id", "team_abbr", "nfl_id",
                    "lined_up_position", "quarter", "down", "yards_to_go",
                    "possession_team", "yardline_side", "yardline_number",
                    "game_clock", "pre_snap_home_score", "pre_snap_visitor_score",
                    "play_direction", "dropback_duration",
                    "pass_rushers_encountered", "pressure_allowed",
                    "sack_allowed", "time_to_pressure_allowed", "quick_pressure",
                    "unblocked_pressure", "blitzing", "player_get_off", "sack",
                    "time_to_pressure", "time_to_qb_hurry"]
GAMES_COLS = ["game_id", "game_key", "season", "season_type", "week",
              "home_team_abbr", "visitor_team_abbr"]
COMBINE_COLS = ["draft_year", "event_id", "nfl_id", "entity_type", "time",
                "drill_type", "drill_name", "attempt", "x", "y", "s", "a",
                "dis", "dir"]


def build_fixture(outdir: str | Path, *, seed: int = 11, n_players: int = 12,
                  snaps_per: int = 30, jump_sd: float = 0.08,
                  burst_sd: float = 0.10) -> dict:
    """Write a complete synthetic dataset and its truth file."""
    out = Path(outdir)
    tracking, player_play, games, truth = make_game_frames(
        seed=seed, n_players=n_players, snaps_per=snaps_per, jump_sd=jump_sd,
        burst_sd=burst_sd)
    comb, comb_truth = make_combine_frames(seed=seed + 1, n_players=n_players)
    _write_csv(out / "game_tracking.csv", tracking, GAME_TRACKING_COLS)
    _write_csv(out / "player_play.csv", player_play, PLAYER_PLAY_COLS)
    _write_csv(out / "games.csv", games, GAMES_COLS)
    _write_csv(out / "combine_tracking.csv", comb, COMBINE_COLS)
    truth["combine"] = comb_truth
    truth["params"] = {"seed": seed, "n_players": n_players,
                       "snaps_per": snaps_per, "jump_sd": jump_sd,
                       "burst_sd": burst_sd}
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n",
                                    encoding="utf-8")
    return truth
