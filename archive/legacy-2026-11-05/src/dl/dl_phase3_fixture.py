#!/usr/bin/env python3
"""dl_phase3_fixture — synthetic world for the Phase-3 IDR columns.

The fixture exists to answer one question mechanically: **does the pipeline
recover a planted rest x load interaction on the deficit, and does it refuse to
invent one when the world is null?**  It never reports a finding.

Design
------
* Two games, two defensive teams per game, six rushers per team, sixteen plays
  per team.  Each play carries four rushers, so a player's own snap sequence
  has genuine gaps (rotation) and genuine adjacency.
* Every rep is emitted as a real 10 Hz speed trace,
  ``v(t) = vmax * (1 - exp(-t/tau))`` with a known ``(vmax, tau)`` — exactly
  the curve the pipeline fits — plus small measurement noise.
* The planted model is on the log deficit ratio::

      log(deficit) = b0 + b_load*z_load + b_rest*z_rest + b_int*z_load*z_rest + eps

  with ``deficit = a0_snap / baseline_a0``.  ``b_int = 0`` in the null world.
* Load is **derived by the production code** from the emitted traces, not
  planted as a column, so the interaction really has to survive
  ``power_integral`` -> rolling window -> fit.

The generator iterates the trace/load pair three times: changing ``a0`` changes
the power integral, which changes the load the next trace is conditioned on.
Three passes are enough for the emitted world to be self-consistent to well
inside the recovery tolerance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BASE_TIME = pd.Timestamp("2023-09-10 13:00:00")
HZ = 10.0
REP_WINDOW_S = 1.5
N_RUSHERS_PER_PLAY = 4

__all__ = ["build_world", "PLANTED", "NULL"]


PLANTED = {"b0": 0.0, "b_load": -0.05, "b_rest": 0.02, "b_int": 0.040,
           "sd_noise": 0.01}
NULL = {"b0": 0.0, "b_load": 0.0, "b_rest": 0.0, "b_int": 0.0,
        "sd_noise": 0.01}


def _schedule(rng, n_plays: int) -> pd.DataFrame:
    """Team play schedule: snap instant and play end, in seconds from kickoff."""
    rows = []
    t = 0.0
    for play in range(n_plays):
        duration = float(rng.uniform(3.5, 6.5))
        rows.append({"play_id": play + 1, "t_snap_s": t,
                     "t_end_s": t + duration})
        t += duration + float(rng.uniform(22.0, 40.0))
    return pd.DataFrame(rows)


def build_world(*, seed: int = 20270107, regime: str = "planted",
                n_games: int = 2, n_teams: int = 2,
                n_players_per_team: int = 6, n_plays: int = 16
                ) -> dict[str, object]:
    """Return ``{frame, tracking, player_play, baseline, truth}``."""
    truth = dict(PLANTED if regime == "planted" else NULL)
    rng = np.random.default_rng(seed)

    players = []
    baseline: dict[int, tuple[float, float]] = {}
    for g in range(n_games):
        for team_i in range(n_teams):
            team = f"T{g}{team_i}"
            for p in range(n_players_per_team):
                nfl_id = 1000 * (g + 1) + 100 * team_i + p + 1
                a0 = float(rng.uniform(4.0, 8.0))
                baseline[nfl_id] = (a0, 0.25)
                players.append({"game_id": 100 + g, "team": team,
                                "team_i": team_i, "nfl_id": nfl_id,
                                "a0": a0})
    roster = pd.DataFrame(players)

    # -- team schedules --------------------------------------------------- #
    schedule: dict[tuple[str, str], pd.DataFrame] = {}
    for (game_id, team), _ in roster.groupby(["game_id", "team"]):
        schedule[(str(game_id), str(team))] = _schedule(rng, n_plays)

    # -- reps: which player rushes on which play --------------------------- #
    reps = []
    for (game_id, team), group in roster.groupby(["game_id", "team"]):
        key = (str(game_id), str(team))
        sched = schedule[key]
        pool = list(group["nfl_id"])
        team_i = int(group["team_i"].iloc[0])
        for _, play in sched.iterrows():
            size = min(N_RUSHERS_PER_PLAY, len(pool))
            chosen = rng.choice(pool, size=size, replace=False)
            for nfl_id in chosen:
                # play_id is unique per GAME (the two defences must not share
                # a play key, or their schedules collide in the play table).
                reps.append({"game_id": int(game_id), "team": team,
                             "play_id": int(play["play_id"]) + 100 * team_i,
                             "nfl_id": int(nfl_id),
                             "t_snap_s": float(play["t_snap_s"]),
                             "t_end_s": float(play["t_end_s"])})
    frame = pd.DataFrame(reps)

    # -- rest (team-level, exactly the production definition) -------------- #
    frame = frame.sort_values(["game_id", "team", "play_id", "nfl_id"])
    plays = frame[["game_id", "team", "play_id", "t_snap_s", "t_end_s"]] \
        .drop_duplicates().sort_values(["game_id", "team", "play_id"])
    plays["prev_end_s"] = plays.groupby(["game_id", "team"])["t_end_s"].shift()
    plays["rest_s"] = plays["t_snap_s"] - plays["prev_end_s"]
    frame = frame.merge(plays[["game_id", "team", "play_id", "rest_s"]],
                        on=["game_id", "team", "play_id"], how="left")

    # -- per-rep parameters ------------------------------------------------ #
    tau = rng.uniform(0.55, 1.30, size=len(frame))
    frame["tau"] = tau
    frame["a0_base"] = frame["nfl_id"].map(lambda n: baseline[int(n)][0])
    frame["rest_z"] = np.nan_to_num(_z(frame["rest_s"].to_numpy(dtype=float)))

    # -- iterate trace <-> load to self-consistency ------------------------ #
    eps = rng.normal(0.0, truth["sd_noise"], size=len(frame))

    def _apply(load_vector):
        z = np.nan_to_num(_z(load_vector))
        log_def = (truth["b0"] + truth["b_load"] * z
                   + truth["b_rest"] * frame["rest_z"].to_numpy()
                   + truth["b_int"] * z * frame["rest_z"].to_numpy()
                   + eps)
        frame["a0_true"] = frame["a0_base"] * np.exp(log_def)
        frame["vmax"] = frame["a0_true"] * frame["tau"]

    load = np.zeros(len(frame))
    for _ in range(2):
        _apply(load)
        load = _recompute_load(frame)
    # Final pass: the emitted trace is generated FROM the emitted load, so the
    # planted model holds in the world the pipeline actually sees.
    _apply(load)
    frame["load_truth"] = load
    frame["deficit_truth"] = frame["a0_true"] / frame["a0_base"]
    tracking = _tracking(frame, rng)
    player_play = _player_play(frame)
    return {"frame": frame, "tracking": tracking, "player_play": player_play,
            "baseline": baseline, "truth": truth}


def _z(values: np.ndarray) -> np.ndarray:
    """NaN-aware z-score: stats over observed values, NaN preserved.

    Standardising over a zero-filled column would inflate the SD by the
    missing-row share and silently shrink every planted coefficient.
    """
    values = np.asarray(values, dtype=float)
    sd = float(np.nanstd(values))
    if not np.isfinite(sd) or sd == 0.0:
        return np.zeros_like(values)
    return (values - float(np.nanmean(values))) / sd


def _recompute_load(frame: pd.DataFrame) -> np.ndarray:
    """Rolling power load over the prior 3 own snaps (production arithmetic)."""
    from dl.dl_phase3 import power_integral  # local: avoid an import cycle

    powers: dict[tuple, float] = {}
    t = np.arange(int(REP_WINDOW_S * HZ) + 1) / HZ
    for row in frame.itertuples(index=False):
        v = row.vmax * (1.0 - np.exp(-t / row.tau))
        a = (row.vmax / row.tau) * np.exp(-t / row.tau)
        power, _ = power_integral(t, v, a, clip_negative=True, max_dt_s=0.5)
        powers[(int(row.game_id), int(row.nfl_id), int(row.play_id))] = power

    out = np.full(len(frame), np.nan)
    for _, idx in frame.groupby(["game_id", "nfl_id"], sort=False).indices.items():
        rows = list(idx)
        for pos, i in enumerate(rows):
            if pos < 3:
                continue
            window = [powers[(int(frame.at[j, "game_id"]),
                              int(frame.at[j, "nfl_id"]),
                              int(frame.at[j, "play_id"]))]
                      for j in rows[pos - 3:pos]]
            out[i] = float(np.nansum(window))
    return out


def _tracking(frame: pd.DataFrame, rng) -> pd.DataFrame:
    t = np.arange(int(REP_WINDOW_S * HZ) + 1) / HZ
    rows = []
    for row in frame.itertuples(index=False):
        v = row.vmax * (1.0 - np.exp(-t / row.tau))
        a = (row.vmax / row.tau) * np.exp(-t / row.tau)
        v = v + rng.normal(0.0, 0.04, size=t.size)
        start = BASE_TIME + pd.to_timedelta(row.t_snap_s, unit="s")
        for k, tk in enumerate(t):
            rows.append({
                "game_id": int(row.game_id), "play_id": int(row.play_id),
                "nfl_id": int(row.nfl_id),
                "time": start + pd.to_timedelta(float(tk), unit="s"),
                "s": float(max(0.0, v[k])), "a": float(a[k]),
                "event": "ball_snap" if k == 0 else "",
            })
    return pd.DataFrame(rows)


def _player_play(frame: pd.DataFrame) -> pd.DataFrame:
    """Two rows per play (offense + defense) so the defensive team is derivable."""
    plays = frame[["game_id", "team", "play_id", "t_snap_s"]].drop_duplicates()
    rows = []
    for row in plays.itertuples(index=False):
        offense = "OFF" + str(row.game_id)
        rows.append({"game_id": int(row.game_id), "play_id": int(row.play_id),
                     "team_abbr": str(row.team),
                     "possession_team": offense,
                     "play_nullified_by_penalty": "N"})
        rows.append({"game_id": int(row.game_id), "play_id": int(row.play_id),
                     "team_abbr": offense,
                     "possession_team": offense,
                     "play_nullified_by_penalty": "N"})
    return pd.DataFrame(rows)
