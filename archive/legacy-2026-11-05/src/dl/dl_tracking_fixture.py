"""dl_tracking_fixture — synthesise ``game_tracking`` rows from a snap-truth frame.

The DL chain needs a *tracking-shaped* table whose ground truth is known, so
that later stages (snap extraction, blocker assignment, get-off, load) can be
checked against the numbers that generated them.  ``write_game_tracking``
turns one row per (play, player) of ``snap_truth.csv`` into the 12-column
``game_tracking`` contract (``lc_common.EXPECTED_SCHEMAS``).

Physics
-------
Every skill-position player follows a first-order acceleration profile
``a(t) = a0 * exp(-t / tau)`` measured from the snap frame, so

    v(t)    = a0 * tau * (1 - exp(-t / tau))
    dist(t) = a0 * tau * (t - tau * (1 - exp(-t / tau)))

Defensive linemen start on the line of scrimmage (``x = 0``) and rush toward
the quarterback, i.e. ``x(t) = -dist(t)`` clamped at the QB's drop depth so a
rusher stops when he arrives.  The line recoils 0.1-0.4 yd, the QB drops to
5-7 yd, skill players run a short route.

Determinism: all noise comes from ``numpy.random.default_rng`` seeded from
``cfg['bdb27_dl']['seeds']['fixture']`` plus a stable hash of the play key, so
two runs over the same truth frame are byte-identical.

Memory: plays are generated and written in chunks (``chunk_plays``); no
whole-table pandas frame is ever built.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

import dl_common as D

#: ``game_tracking`` column order (``lc_common.EXPECTED_SCHEMAS``).
TRACKING_COLUMNS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
                    "dis", "o", "dir", "event"]

TRUTH_COLUMNS = ["game_id", "play_id", "nfl_id", "position_group", "true_vmax",
                 "true_tau", "true_a0", "snap_frame_time", "pressure_time",
                 "dropback_duration", "event", "load_before_absolute",
                 "load_before_relative", "world"]

DL_POSITIONS = frozenset({"DE", "DT", "NT", "EDGE", "INTERIOR_LINE", "DL"})
OL_POSITIONS = frozenset({"G", "T", "C", "OL", "OT", "OG", "OC"})
QB_POSITIONS = frozenset({"QB"})
SKILL_POSITIONS = frozenset({"WR", "TE", "RB", "FB", "HB", "SLOT"})

#: Base of the synthetic clock: every play starts at t = 0.0 on this date, so
#: seconds-within-play are exactly ``i * dt`` and the column still dtypes as a
#: timestamp (as the real tracking file does).
EPOCH = datetime(1970, 1, 1, 0, 0, 0)


def _role(position_group: Any) -> str:
    key = str(position_group).strip().upper()
    if key in DL_POSITIONS:
        return "dl"
    if key in OL_POSITIONS:
        return "ol"
    if key in QB_POSITIONS:
        return "qb"
    if key in SKILL_POSITIONS:
        return "skill"
    return "skill"


def _play_seed(base_seed: int, game_id: Any, play_id: Any) -> int:
    """Stable per-play seed: independent of row order and chunking."""
    digest = D.stable_hash({"g": int(game_id), "p": int(play_id)})
    return (int(base_seed) + int(digest[:8], 16)) % (2 ** 32)


def _frame_times(frames: int, hz: int) -> list[str]:
    """ISO timestamps for one block of frames (0.0, dt, 2*dt, ...)."""
    step = 1.0 / float(hz)
    out = []
    for i in range(frames):
        moment = EPOCH + timedelta(seconds=round(i * step, 6))
        out.append(moment.strftime("%Y-%m-%dT%H:%M:%S.") +
                   f"{moment.microsecond // 1000:03d}")
    return out


def _heading(dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """Compass-style heading in degrees (0 = +x, counter-clockwise)."""
    return np.degrees(np.arctan2(dy, dx)) % 360.0


def _play_block(play: Mapping[str, np.ndarray], *, hz: int, frames: int,
                cap: float) -> np.ndarray:
    """One play's rows, shape ``(n_players, frames)`` per column, dict."""
    n = len(play["nfl_id"])
    dt = 1.0 / float(hz)
    t = np.arange(frames, dtype=np.float64) * dt            # (frames,)
    snap_t = float(play["snap_frame_time"][0])
    t_rel = np.maximum(t - snap_t, 0.0)[None, :]            # (1, frames)

    role = np.array([_role(p) for p in play["position_group"]], dtype=object)
    tau = np.clip(play["true_tau"].astype(float), 0.3, 3.0)[:, None]
    a0 = np.clip(play["true_a0"].astype(float), 0.5, 25.0)[:, None]
    vmax = np.clip(play["true_vmax"].astype(float), 1.0, cap)[:, None]

    exp_term = np.exp(-t_rel / tau)                          # (n, frames)
    speed = a0 * tau * (1.0 - exp_term)                      # (n, frames)
    speed = np.minimum(speed, vmax)
    dist = a0 * tau * (t_rel - tau * (1.0 - exp_term))
    accel = a0 * exp_term

    is_dl = role == "dl"
    is_ol = role == "ol"
    is_qb = role == "qb"
    is_skill = role == "skill"

    # --- x: distance travelled along the play axis (negative = into backfield)
    qb_depth = 5.0 + 2.0 * _per_player(play, "qb_depth", n)
    rush_cap = (qb_depth + 0.5)[:, None]
    dist_dl = np.minimum(dist, rush_cap)
    recoil = (0.1 + 0.3 * _per_player(play, "recoil", n))[:, None]       # (n,1)
    x = np.zeros((n, frames))
    x = np.where(is_dl[:, None], -dist_dl, x)
    x = np.where(is_ol[:, None], recoil * (1.0 - np.exp(-t_rel / 0.35)), x)
    x = np.where(is_qb[:, None], -qb_depth[:, None] * (1.0 - np.exp(-t_rel / 0.9)), x)
    route_reach = (4.0 + 3.0 * _per_player(play, "route", n))[:, None]
    route_tau = (0.55 + 0.25 * _per_player(play, "route_t", n))[:, None]
    x = np.where(is_skill[:, None],
                 -route_reach * (1.0 - np.exp(-t_rel / route_tau)), x)
    x = np.where((t_rel <= 0.0), 0.0, x)

    # --- y: lateral position about a deterministic alignment
    y0 = _alignment(play, role)[:, None]
    lateral = (1.2 * _per_player(play, "lat", n))[:, None]
    y = y0 + np.where(is_dl[:, None], 0.15 * np.sin(t_rel), lateral *
                      (1.0 - np.exp(-t_rel / 0.8)))
    y = np.where(t_rel <= 0.0, y0, y)

    # --- kinematics columns
    s = np.minimum(speed, cap)
    a = np.clip(accel, 0.0, cap)
    dis = np.where(is_dl[:, None], dist_dl, dist)
    dir_ = _heading(np.gradient(x, dt, axis=1), np.gradient(y, dt, axis=1))
    o = (dir_ + 90.0) % 360.0

    return {"x": x, "y": y, "s": s, "a": a, "dis": dis, "o": o, "dir": dir_,
            "t": np.broadcast_to(t[None, :], (n, frames))}


def _per_player(play: Mapping[str, np.ndarray], salt: str, n: int) -> np.ndarray:
    """Uniform [0, 1) draw per player, keyed by a stable hash of the salt."""
    rng = np.random.default_rng(_play_seed(0, play["game_id"][0], help_salt(play, salt)))
    return rng.random(n)


def help_salt(play: Mapping[str, np.ndarray], salt: str) -> int:
    return int(D.stable_hash({"p": int(play["play_id"][0]), "s": salt})[:8], 16)


def _alignment(play: Mapping[str, np.ndarray], role: np.ndarray) -> np.ndarray:
    """Deterministic y alignment: DL/OL on the ball, QB behind, skill wide."""
    n = len(role)
    y = np.zeros(n)
    y[role == "dl"] = np.linspace(-2.4, 2.4, max(1, int((role == "dl").sum())))
    y[role == "ol"] = np.linspace(-2.0, 2.0, max(1, int((role == "ol").sum())))
    y[role == "qb"] = 0.0
    wide = np.array([-18.0, -11.0, 11.0, 18.0, -6.0, 6.0, -14.0, 14.0])
    idx = 0
    for i in range(n):
        if role[i] == "skill":
            y[i] = wide[idx % len(wide)]
            idx += 1
    return y


def write_game_tracking(truth_df: Any, out_path: str | Path, *, hz: int,
                        frames_per_snap: int, cfg: Mapping) -> int:
    """Write ``game_tracking`` rows for every (play, player) in ``truth_df``."""
    block = D.dl(cfg)
    cap = float(block["thresholds"]["speed_cap_yds"])
    frames = int(frames_per_snap)
    times = _frame_times(frames, hz)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = _plays(truth_df)
    n_rows = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(TRACKING_COLUMNS)
        for play in rows:
            cols = _play_block(play, hz=hz, frames=frames, cap=cap)
            writer.writerows(_rows(play, cols, times))
            n_rows += len(play["nfl_id"]) * frames
    return n_rows


def _plays(truth_df: Any) -> Iterable[dict[str, np.ndarray]]:
    """Group the truth frame by play, preserving first-seen order."""
    frame = truth_df
    if hasattr(frame, "to_dict"):  # pandas / polars frame
        frame = frame.to_dict("records") if hasattr(frame, "columns") else frame
    play_keys: list[tuple[int, int]] = []
    buckets: dict[tuple[int, int], list[Mapping]] = {}
    for rec in frame:
        key = (int(float(rec["game_id"])), int(float(rec["play_id"])))
        if key not in buckets:
            buckets[key] = []
            play_keys.append(key)
        buckets[key].append(rec)
    for key in play_keys:
        recs = buckets[key]
        yield {c: np.array([float(r[c]) if c not in ("position_group", "world")
                            else r[c] for r in recs], dtype=object)
               for c in TRUTH_COLUMNS}


def _rows(play: Mapping[str, np.ndarray], cols: Mapping[str, np.ndarray],
          times: list[str]) -> Iterable[list[Any]]:
    """Yield CSV rows for one play (player-major, then frame)."""
    n = len(play["nfl_id"])
    frames = len(times)
    gid, pid = int(play["game_id"][0]), int(play["play_id"][0])
    for i in range(n):
        nfl = int(float(play["nfl_id"][i]))
        for f in range(frames):
            yield [gid, pid, nfl, times[f],
                   _f(cols["x"][i, f]), _f(cols["y"][i, f]), _f(cols["s"][i, f]),
                   _f(cols["a"][i, f]), _f(cols["dis"][i, f]), _f(cols["o"][i, f]),
                   _f(cols["dir"][i, f]), ""]


def _f(value: Any) -> str:
    return f"{float(value):.3f}"
