#!/usr/bin/env python3
"""synthetic_fixture.py — build a SMALL, schema-compliant synthetic BDB27 fixture.

Why this exists
---------------
`TASK.md` (Environment / engineering) requires a synthetic, schema-compliant set with a
**planted binding ceiling** whenever the real data cannot exercise a code path. On this Pi the
game-tracking data is a non-representative 2^20-row prefix, so the game-side audit items
(c/h/i) cannot run on real data. This fixture lets those paths be executed and tested.

Guarantees
----------
* Same *schemas* (columns / dtypes / units / frames) as the real files, so the identical code
  path runs. Game `x,y` use the NFL 120 x 53.3 yd frame; Combine `x,y` use the local frame.
* A **planted ceiling**: each player has a planted Combine maximum speed `vmax`; in-game
  route-effort peak speeds are drawn below it (share `1 - planted_exceedance_rate`) or just
  above it. In `--regime null` the relationship is inverted (no binding) — the Phase-5 null case.
* Deterministic given the seed. NEVER present these numbers as findings; fixture outputs are
  stamped `SYNTHETIC FIXTURE (not a finding)`.

Usage
-----
    python src/synthetic_fixture.py                 # writes binding + null under fixtures/synthetic/
    python src/synthetic_fixture.py --regime binding --out fixtures/synthetic/binding
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

# --------------------------------------------------------------------------- #
# kinematic helpers
# --------------------------------------------------------------------------- #
def _exp_speed(t: np.ndarray, vmax: float, tau: float) -> np.ndarray:
    return vmax * (1.0 - np.exp(-t / tau))


def _walk_polyline(waypoints: np.ndarray, vmax: float, tau: float, dt: float, tmax: float = 9.0):
    """Move along a polyline at the exponential speed profile v(t)=vmax(1-e^{-t/tau}).

    Returns arrays x, y, s (speed), a (accel), t for one attempt (10 Hz, dt seconds).
    """
    seg = np.diff(waypoints, axis=0)
    seglen = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seglen)])
    total = cum[-1]
    t = np.arange(0.0, tmax, dt)
    # closed-form distance travelled: integral of v(t) = vmax*(t - tau*(1-e^{-t/tau}))
    s_travel = vmax * (t - tau * (1.0 - np.exp(-t / tau)))
    keep = s_travel <= total
    if keep.sum() < 3:
        keep = np.ones_like(t, dtype=bool)
    t, s_travel = t[keep], s_travel[keep]
    # interpolate position along the polyline at arc-length s_travel
    xs = np.interp(s_travel, cum, waypoints[:, 0])
    ys = np.interp(s_travel, cum, waypoints[:, 1])
    v = _exp_speed(t, vmax, tau)
    a = np.gradient(v, dt)
    return xs, ys, v, a, t


# --------------------------------------------------------------------------- #
# fixture builder
# --------------------------------------------------------------------------- #
def build_fixture(
    out_dir: Path,
    regime: str,
    seed: int,
    n_wr: int = 5,
    n_te: int = 3,
    n_plays_per_player: int = 8,
    frames_per_play: int = 15,
    planted_exceedance_rate: float = 0.10,
    dt: float = 0.1,
) -> dict[str, Path]:
    """Write combine_tracking / combine_results / game_tracking / player_play CSVs to `out_dir`."""
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    positions = ["WR"] * n_wr + ["TE"] * n_te
    n_players = len(positions)
    # planted per-player Combine ceiling (local Combine frame, yards/s)
    vmax = 8.6 + 0.20 * np.arange(n_players) + rng.normal(0, 0.05, n_players)
    nfl_ids = np.arange(900001, 900001 + n_players)
    draft_year = np.full(n_players, 2025)

    ct_rows: list[dict] = []
    cr_rows: list[dict] = []
    gt_rows: list[dict] = []
    pp_rows: list[dict] = []

    t0 = pd.Timestamp("2025-03-01T15:00:00")

    for pi in range(n_players):
        pid = int(nfl_ids[pi])
        pos = positions[pi]
        ceiling = float(vmax[pi])

        # ---------- Combine: two 40-yd dash attempts (straight) ----------
        for attempt in (1, 2):
            lane = 1.0 + 3.0 * pi
            wps = np.array([[2.0, lane], [42.0, lane]])
            tau = 1.0 + float(rng.normal(0, 0.05))
            xs, ys, s, a, tt = _walk_polyline(wps, ceiling, tau, dt)
            # tiny frame-to-frame measurement noise -> makes test-retest ICC estimable
            xs = xs + rng.normal(0, 0.004, xs.shape)
            s = s + rng.normal(0, 0.02, s.shape)
            event_id = f"SYN25{pi:03d}{attempt}"
            tstart = t0 + pd.Timedelta(seconds=60 * pi + 30 * attempt)
            dis = np.concatenate([[0.0], np.hypot(np.diff(xs), np.diff(ys))])
            for k in range(len(xs)):
                ct_rows.append(
                    dict(
                        draft_year=int(draft_year[pi]),
                        event_id=event_id,
                        nfl_id=pid,
                        entity_type="PLAYER",
                        time=(tstart + pd.Timedelta(seconds=float(tt[k]))).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
                        drill_type="FORTY_YARD_DASH",
                        drill_name="FORTY_YARD_DASH",
                        attempt=attempt,
                        x=round(float(xs[k]), 6),
                        y=round(float(ys[k]), 6),
                        s=round(float(max(s[k], 0.0)), 6),
                        a=round(float(a[k]), 6),
                        dis=round(float(dis[k]), 6),
                        dir=0.0,
                    )
                )

        # ---------- Combine: two three-cone attempts (cutting) ----------
        for attempt in (1, 2):
            lane = 10.0 + 3.0 * pi
            wps = np.array([[2.0, lane], [7.0, lane], [7.0, lane + 5.0], [12.0, lane + 5.0]])
            tau = 0.7 + float(rng.normal(0, 0.05))
            xs, ys, s, a, tt = _walk_polyline(wps, 0.75 * ceiling, tau, dt)
            xs = xs + rng.normal(0, 0.004, xs.shape)
            s = s + rng.normal(0, 0.02, s.shape)
            event_id = f"SYN25C{pi:03d}{attempt}"
            tstart = t0 + pd.Timedelta(seconds=60 * pi + 30 * attempt + 5)
            dis = np.concatenate([[0.0], np.hypot(np.diff(xs), np.diff(ys))])
            for k in range(len(xs)):
                ct_rows.append(
                    dict(
                        draft_year=int(draft_year[pi]),
                        event_id=event_id,
                        nfl_id=pid,
                        entity_type="PLAYER",
                        time=(tstart + pd.Timedelta(seconds=float(tt[k]))).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
                        drill_type="THREE_CONE_DRILL",
                        drill_name="THREE_CONE_DRILL",
                        attempt=attempt,
                        x=round(float(xs[k]), 6),
                        y=round(float(ys[k]), 6),
                        s=round(float(max(s[k], 0.0)), 6),
                        a=round(float(a[k]), 6),
                        dis=round(float(dis[k]), 6),
                        dir=0.0,
                    )
                )

        cr_rows.append(
            dict(
                draft_year=int(draft_year[pi]),
                nfl_id=pid,
                combine_position=pos,
                combine_height=round(float(rng.uniform(68, 76)), 3),
                combine_weight=int(rng.integers(180, 230)),
                hand_size=9.0,
                arm_length=32.0,
                wing_span=77.0,
                ten_yd_split=1.55,
                forty=round(float(40.0 / ceiling * 1.05), 3),
                vertical=35.0,
                broad_jump=120.0,
                three_cone=7.0,
                short_shuttle=4.3,
                bench_reps="NA",
                ngs_athleticism_score=70.0,
                ngs_college_production_score="NA",
                ngs_final_score="NA",
            )
        )

        # ---------- Game side: route plays ----------
        for pl in range(n_plays_per_player):
            game_id = 2025123400 + pl
            play_id = 100 + pi * 1000 + pl
            is_deep = bool(pl % 2 == 0)
            pass_length = int(rng.integers(16, 30)) if is_deep else int(rng.integers(1, 10))
            route_type = str(rng.choice(["GO", "POST", "CROSS", "IN", "OUT", "HITCH", "SLANT"]))
            targeted = bool(rng.random() < 0.25)
            # planted ceiling binding. Exceedance is assigned on a fixed cycle so the planted
            # rate is reproduced exactly (a fixture must be reliable, not draw-dependent).
            gidx = pi * n_plays_per_player + pl
            every = max(1, int(round(1.0 / max(planted_exceedance_rate, 1e-9))))
            is_exceed = (gidx % every == 0)
            if regime == "binding":
                frac = rng.uniform(1.06, 1.12) if is_exceed else rng.uniform(0.80, 0.97)
            elif regime == "null":
                frac = rng.uniform(1.12, 1.28)          # routinely exceeds -> no binding
            else:
                raise ValueError(f"unknown regime: {regime}")
            peak = frac * ceiling

            # simple route path in the NFL frame (x along field length, y across)
            x0 = float(rng.uniform(20, 40))
            y0 = float(rng.uniform(5, 45))
            if is_deep:
                wps = np.array([[x0, y0], [x0 + 18.0, y0 + 6.0]])
            else:
                wps = np.array([[x0, y0], [x0 + 10.0, y0 - 6.0]])
            tau = 0.2  # fast ramp so the planted peak is actually reached within the play
            xs, ys, s, a, tt = _walk_polyline(wps, peak, tau, dt, tmax=frames_per_play * dt)
            n = min(len(xs), frames_per_play)
            xs, ys, s, a, tt = xs[:n], ys[:n], s[:n], a[:n], tt[:n]
            tstart = pd.Timestamp("2025-09-07T17:00:00") + pd.Timedelta(seconds=120 * pl + 4 * pi)
            dis = np.concatenate([[0.0], np.hypot(np.diff(xs), np.diff(ys))])
            for k in range(n):
                gt_rows.append(
                    dict(
                        game_id=game_id,
                        play_id=play_id,
                        nfl_id=pid,
                        time=(tstart + pd.Timedelta(seconds=float(tt[k]))).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
                        x=round(float(xs[k]), 2),
                        y=round(float(ys[k]), 2),
                        s=round(float(max(s[k], 0.0)), 3),
                        a=round(float(a[k]), 3),
                        dis=round(float(dis[k]), 3),
                        o=0.0,
                        dir=0.0,
                        event="",
                    )
                )
            pp_rows.append(
                dict(
                    game_id=game_id,
                    play_id=play_id,
                    team_abbr="SYN",
                    nfl_id=pid,
                    route_ran=route_type,
                    pass_length=pass_length,
                    target=targeted,
                    separation_at_pass_forward=(round(float(rng.uniform(0.3, 3.0)), 4) if targeted else "NA"),
                    coverage_assignment="MAN",
                    coverage_assignment_at_snap="MAN",
                    team_coverage_man_zone="MAN_COVERAGE",
                    team_coverage_type="COVER_1_MAN",
                    dropback_duration=round(float(rng.uniform(2.0, 3.0)), 4),
                    down=int(rng.integers(1, 4)),
                    play_direction="right",
                )
            )

    # ---------- Prospect identity (`players.csv`; schema-compliant; NO rng draws) ----------
    # Deterministic per index so regeneration never perturbs the other files; supplies the
    # Combine-era controls `age` (draft_year - birth_year) and `draft_position` (round tier,
    # with `NA` -> the explicit `undrafted` level).
    pl_rows: list[dict] = []
    for pi in range(n_players):
        age_at_combine = 21 + (pi % 4)              # 21..24
        birth_year = int(draft_year[pi]) - age_at_combine
        drafted = (pi % 3 != 0)                     # every 3rd prospect undrafted (explicit level)
        rnd = (pi % 5) + 1 if drafted else None
        pick = (pi % 32) + 1 if drafted else None
        overall = ((rnd - 1) * 32 + pick) if drafted else None
        pl_rows.append(dict(
            nfl_id=int(nfl_ids[pi]),
            display_name=f"Synthetic Prospect {pi:02d}",
            draft_year=int(draft_year[pi]),
            nfl_position=positions[pi],
            birth_date=f"{birth_year}-04-15",
            college_name="SYN University",
            college_conference="SYN",
            draft_round=(rnd if drafted else "NA"),
            draft_pick_within_round=(pick if drafted else "NA"),
            draft_overall_pick=(overall if drafted else "NA"),
        ))
    players = pd.DataFrame(pl_rows)

    games = pd.DataFrame(
        [dict(game_id=2025123400 + pl, game_key=90000 + pl, season=2025,
              season_type="REG", week=pl + 1, home_team_abbr="SYN", visitor_team_abbr="OPP")
         for pl in range(n_plays_per_player)]
    )

    ct = pd.DataFrame(ct_rows)
    cr = pd.DataFrame(cr_rows)
    gt = pd.DataFrame(gt_rows)
    pp = pd.DataFrame(pp_rows)

    paths = {
        "combine_tracking": out_dir / "combine_tracking.csv",
        "combine_results": out_dir / "combine_results.csv",
        "game_tracking": out_dir / "game_tracking.csv",
        "player_play": out_dir / "player_play.csv",
        "games": out_dir / "games.csv",
        "players": out_dir / "players.csv",
    }
    ct.to_csv(paths["combine_tracking"], index=False)
    cr.to_csv(paths["combine_results"], index=False)
    gt.to_csv(paths["game_tracking"], index=False)
    pp.to_csv(paths["player_play"], index=False)
    games.to_csv(paths["games"], index=False)
    players.to_csv(paths["players"], index=False)
    return paths


def main() -> None:
    cfg = common.load_config()
    ap = argparse.ArgumentParser(description="Build the synthetic BDB27 fixture")
    ap.add_argument("--out", default=None, help="output dir (default: repo fixtures/synthetic/binding)")
    ap.add_argument("--regime", choices=["binding", "null"], default=None)
    ap.add_argument("--all", action="store_true", help="write both binding and null regimes")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    if args.config:
        cfg = common.load_config(args.config)

    syn = cfg["audit"]["synthetic"]
    root = common.resolve(cfg, "synthetic")
    common.seed_everything(cfg["seed"])

    def _one(regime: str, out: Path) -> None:
        paths = build_fixture(
            out_dir=out,
            regime=regime,
            seed=int(syn.get("seed", cfg["seed"])),
            n_wr=int(syn["n_wr"]),
            n_te=int(syn["n_te"]),
            n_plays_per_player=int(syn["n_plays_per_player"]),
            frames_per_play=int(syn["frames_per_play"]),
            planted_exceedance_rate=float(syn["planted_exceedance_rate"]),
        )
        for k, v in paths.items():
            print(f"  wrote {v}  ({v.stat().st_size} bytes)")

    if args.all or args.out is None and args.regime is None:
        for regime in ("binding", "null"):
            print(f"[{regime}]")
            _one(regime, root / regime)
    else:
        regime = args.regime or syn["regime"]
        out = Path(args.out) if args.out else root / regime
        print(f"[{regime}]")
        _one(regime, out)


if __name__ == "__main__":
    main()
