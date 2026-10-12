#!/usr/bin/env python3
"""game_features.py — game-side kinematics / effort extraction shared by
`00_thresholds.py` and `03_game_demand.py`.

Discipline (TASK.md):
* kinematics are **recomputed from `x,y`** (the provided `s`/`a` are unreliable on cutting/
  breaking routes — audit f), never taken from the vendor columns;
* a **route snap** = `player_play.route_ran IS NOT NULL` (it is *categorical* route type, NOT a
  boolean flag — `notes/schema.md` §7, audit d);
* everything config-driven (`game.*`, `audit.*`, `envelope.*`) — no magic numbers;
* memory-safe: one streaming DuckDB pass (aggregate per `game/play/player`), no full CSV in pandas;
  the percentile cut-off is accumulated in a fixed **histogram**, so only O(#route-snaps) values live
  in memory, not O(#frames).

Public API
----------
* ``pq_list(paths)`` -> DuckDB ``read_parquet([...])`` call string for one/many Parquet files.
* ``extract_efforts(con, gt_paths, pp_path, cr_path, cfg)`` -> per route-snap effort rows
  (peak speed + peak accel recomputed from x,y), joined to the route context.
* ``route_snap_counts(con, pp_path)`` -> per-player route-snap counts (the fixed denominator).
* ``Histogram`` / ``derive_thresholds(efforts, cfg)`` -> streamed percentile cut-offs.
* ``top_k_efforts(efforts, k, metric)`` -> top-k efforts with ties at the k-th value kept.
* ``load_ceilings(envelope_csv, speed_col, accel_col)`` -> per-player Phase-2 ceiling.
* ``combine_ceilings(con, ct_paths, cfg)`` -> per-player ceiling computed from the Combine side with
  the shared Phase-2 fitter (used by the synthetic fixture path; the full run reads the Phase-2 file).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

import common
import combine_features as cf


# --------------------------------------------------------------------------- #
# DuckDB path helpers
# --------------------------------------------------------------------------- #
def pq_list(paths: str | Path | Sequence[str | Path]) -> str:
    """Return a DuckDB ``read_parquet(['a','b'])`` call string for one or more Parquet files."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    items = ", ".join(f"'{p}'" for p in paths)
    return f"read_parquet([{items}])"


# --------------------------------------------------------------------------- #
# per-play/-player kinematics recomputed from x,y (single streaming pass)
# --------------------------------------------------------------------------- #
def _kinematics_cte(gt_expr: str) -> str:
    """Frame CTE with the previous frame within each (game, play, player), x/y downcast to REAL."""
    return f"""
        SELECT game_id, play_id, nfl_id, epoch_ms(time) AS t,
               CAST(x AS REAL) AS x, CAST(y AS REAL) AS y,
               lag(x)          OVER w AS lx,
               lag(y)          OVER w AS ly,
               lag(epoch_ms(time)) OVER w AS lt
        FROM {gt_expr}
        WINDOW w AS (PARTITION BY game_id, play_id, nfl_id ORDER BY time)
    """


def effort_kinematics_sql(gt_expr: str) -> str:
    """Per (game_id, play_id, nfl_id): peak x/y-derived speed and peak x/y-derived acceleration.

    Speed = step distance / dt (yd/s); accel = d(speed)/dt (yd/s^2). Both are recomputed from
    x,y — the vendor `s`/`a` columns are NOT used (audit f).
    """
    f = _kinematics_cte(gt_expr)
    return f"""
        WITH f AS ({f}),
        v AS (
            SELECT game_id, play_id, nfl_id, t,
                   sqrt((x-lx)*(x-lx) + (y-ly)*(y-ly)) / NULLIF((t-lt)/1000.0, 0) AS spd
            FROM f WHERE lx IS NOT NULL
        ),
        va AS (
            SELECT game_id, play_id, nfl_id, t, spd,
                   (spd - lag(spd) OVER w2) / NULLIF((t - lag(t) OVER w2)/1000.0, 0) AS acc
            FROM v
            WINDOW w2 AS (PARTITION BY game_id, play_id, nfl_id ORDER BY t)
        )
        SELECT game_id, play_id, nfl_id,
               count(*) AS n_frames,
               max(spd) AS peak_speed,
               max(acc) AS peak_accel
        FROM va GROUP BY 1, 2, 3
    """


def extract_efforts(
    con,
    gt_paths: Sequence[Path] | Path,
    pp_path: Path,
    cr_path: Path | None,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return one row per **route snap** (``route_ran IS NOT NULL``) that has in-game tracking.

    A route snap is joined to its per-play x/y-derived kinematics; the Combine position (from
    `combine_results`, when provided) is attached for reporting. Only the columns needed are
    selected; the aggregation streams over the Parquet (no full CSV into pandas).
    """
    gt = pq_list(gt_paths)
    pp = common.pq(pp_path)
    kin = effort_kinematics_sql(gt)
    if cr_path is not None:
        cr_join = f"LEFT JOIN {common.pq(cr_path)} cr ON cr.nfl_id = pp.nfl_id"
        cr_sel = "cr.combine_position"
    else:
        cr_join, cr_sel = "", "CAST(NULL AS VARCHAR) AS combine_position"
    sql = f"""
        WITH e AS ({kin})
        SELECT pp.nfl_id, {cr_sel},
               pp.game_id, pp.play_id, pp.route_ran, pp.pass_length, pp.target,
               e.n_frames, e.peak_speed, e.peak_accel
        FROM {pp} pp
        JOIN e ON e.game_id = pp.game_id AND e.play_id = pp.play_id AND e.nfl_id = pp.nfl_id
        {cr_join}
        WHERE pp.route_ran IS NOT NULL
    """
    out: list[dict[str, Any]] = []
    for nid, pos, gid, pid, rr, plen, tgt, nf, spd, acc in con.execute(sql).fetchall():
        out.append(dict(
            nfl_id=int(nid),
            combine_position=(str(pos) if pos is not None else None),
            game_id=int(gid), play_id=int(pid),
            route_ran=(str(rr) if rr is not None else None),
            pass_length=(int(plen) if plen is not None else None),
            target=(bool(tgt) if tgt is not None else None),
            n_frames=int(nf) if nf is not None else 0,
            peak_speed=(float(spd) if spd is not None else float("nan")),
            peak_accel=(float(acc) if acc is not None else float("nan")),
        ))
    return out


def route_snap_counts(con, pp_path: Path) -> dict[int, int]:
    """Per-player route-snap counts (``route_ran IS NOT NULL``) — the fixed denominator."""
    rows = con.execute(
        f"SELECT nfl_id, count(*) FROM {common.pq(pp_path)} "
        f"WHERE route_ran IS NOT NULL GROUP BY 1").fetchall()
    return {int(n): int(c) for n, c in rows}


# --------------------------------------------------------------------------- #
# streamed percentile cut-offs
# --------------------------------------------------------------------------- #
class Histogram:
    """Fixed-width 1-D histogram with linear interpolation inside the hit bin.

    Memory-bounded: only ``nbins`` integer counters are kept regardless of the number of values,
    so a full-run percentile is derived in one streaming pass (no values materialised).
    """

    def __init__(self, bin_width: float, max_value: float):
        self.bin_width = float(bin_width)
        self.max_value = float(max_value)
        self.nbins = int(math.ceil(self.max_value / self.bin_width)) + 1  # +1 overflow bin
        self.counts = np.zeros(self.nbins, dtype=np.int64)
        self.total = 0

    def add(self, values: Iterable[float]) -> None:
        arr = np.asarray(list(values), dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return
        idx = np.clip((arr / self.bin_width).astype(np.int64), 0, self.nbins - 1)
        self.counts += np.bincount(idx, minlength=self.nbins)
        self.total += int(arr.size)

    def percentile(self, q: float) -> float | None:
        """Linear-interpolation percentile over the histogram bin centres; None when empty."""
        if self.total <= 0:
            return None
        target = float(q) * self.total
        cum = np.cumsum(self.counts)
        i = int(np.searchsorted(cum, target, side="left"))
        i = min(i, self.nbins - 1)
        prev = int(cum[i - 1]) if i > 0 else 0
        denom = int(self.counts[i])
        frac = ((target - prev) / denom) if denom > 0 else 0.0
        return float((i + frac) * self.bin_width)


def derive_thresholds(efforts: Sequence[dict], cfg: dict[str, Any]) -> dict[str, Any]:
    """Derive the route-effort cut-offs from per-effort peaks (percentile method).

    Two cut-offs at the frozen percentiles: in-game **peak speed** (yd/s) and **peak acceleration**
    (yd/s^2), accumulated in fixed histograms so the full run stays memory-bounded.
    """
    g = cfg["game"]["thresholds"]
    h = g["histogram"]
    hs = Histogram(h["speed_bin_yds"], h["max_speed_yds"])
    ac = Histogram(h["accel_bin_yds2"], h["max_accel_yds2"])
    hs.add([e["peak_speed"] for e in efforts])
    ac.add([e["peak_accel"] for e in efforts])
    hs_v, ac_v = hs.percentile(g["high_speed_percentile"]), ac.percentile(g["accel_percentile"])
    return dict(
        high_speed_threshold_yds=(round(hs_v, 4) if hs_v is not None else None),
        accel_threshold_yds2=(round(ac_v, 4) if ac_v is not None else None),
        n_efforts=int(len(efforts)),
        speed_values_total=int(hs.total),
        accel_values_total=int(ac.total),
    )


# --------------------------------------------------------------------------- #
# top-k efforts (ties at the k-th value kept)
# --------------------------------------------------------------------------- #
def top_k_efforts(efforts: Sequence[dict], k: int, metric: str = "peak_speed") -> list[dict]:
    """Top-`k` efforts for one player, ranked by `metric` descending.

    Ties at the k-th value are **kept** (never silently truncated) — prereg §2. Ordering is
    fully deterministic (value desc, then game_id, play_id).
    """
    srt = sorted(efforts, key=lambda e: (-_val(e, metric), e["game_id"], e["play_id"]))
    if len(srt) <= int(k):
        return srt
    cut = _val(srt[int(k) - 1], metric)
    return [e for e in srt if _val(e, metric) >= cut]


def _val(e: dict, metric: str) -> float:
    v = e.get(metric, float("nan"))
    try:
        v = float(v)
    except (TypeError, ValueError):
        return float("-inf")
    return v if np.isfinite(v) else float("-inf")


# --------------------------------------------------------------------------- #
# Combine ceilings (Phase 2)
# --------------------------------------------------------------------------- #
def load_ceilings(envelope_csv: Path, speed_col: str, accel_col: str) -> dict[int, dict[str, float]]:
    """Load the per-player Phase-2 ceiling table (``env_vmax`` / ``env_a0``) keyed by nfl_id."""
    import csv as _csv

    out: dict[int, dict[str, float]] = {}
    if not Path(envelope_csv).exists():
        return out
    with open(envelope_csv, newline="", encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            try:
                nid = int(r["nfl_id"])
            except (KeyError, TypeError, ValueError):
                continue
            def _n(key: str) -> float:
                try:
                    return float(r.get(key, ""))
                except (TypeError, ValueError):
                    return float("nan")
            out[nid] = {"vmax": _n(speed_col), "a0": _n(accel_col)}
    return out


def combine_attempt_env(con, ct_paths: Sequence[Path] | Path, cfg: dict[str, Any]) -> dict[int, dict]:
    """Per-player per-attempt Combine envelope values (straight-line drill).

    Returns ``{nfl_id: {"vmax": [per-attempt env_vmax], "a0": [...], "n_attempts": int}}`` using the
    shared Phase-2 fitter (``combine_features.fit_envelope``). Used by Phase-5 for the feature
    reliability (ICC) and the thin-repeat sensitivity — never a finding on its own.
    """
    env = cfg["envelope"]
    fit_ctx = env["fit"]
    drills = env["fit_drills"]
    drills = drills if isinstance(drills, list) else [drills]
    dlist = ",".join(f"'{d}'" for d in drills)
    arr = con.execute(f"""
        SELECT nfl_id, event_id, x, y,
               (epoch_ms(time) - min(epoch_ms(time)) OVER (PARTITION BY event_id)) / 1000.0 AS t
        FROM {pq_list(ct_paths)}
        WHERE entity_type = 'PLAYER' AND drill_type IN ({dlist})
        ORDER BY nfl_id, event_id, t
    """).fetchnumpy()

    def _s(v):
        return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)

    per: dict[int, dict[str, list[float]]] = {}
    n = len(arr["nfl_id"])
    key, cur = None, None

    def flush(k, c):
        if c is None or len(c["x"]) < 2:
            return
        nid = int(k)
        x = np.asarray(c["x"], float); y = np.asarray(c["y"], float); t = np.asarray(c["t"], float)
        v, _ = cf._speed_path(x, y, t)
        onset = float(fit_ctx["onset_speed_yds"])
        idx = np.where(v >= onset)[0]
        i0 = int(idx[0]) if len(idx) else 0
        vmax, _tau, a0 = cf.fit_envelope(t - t[i0], v, cfg)
        d = per.setdefault(nid, {"vmax": [], "a0": []})
        d["vmax"].append(float(vmax) if np.isfinite(vmax) else float("nan"))
        d["a0"].append(float(a0) if np.isfinite(a0) else float("nan"))

    for i in range(n):
        nid = int(arr["nfl_id"][i])
        ev = _s(arr["event_id"][i])
        if (nid, ev) != key:
            flush(key[0] if key else None, cur)
            key, cur = (nid, ev), {"x": [], "y": [], "t": []}
        cur["x"].append(float(arr["x"][i]))
        cur["y"].append(float(arr["y"][i]))
        cur["t"].append(float(arr["t"][i]))
    if key is not None:
        flush(key[0], cur)

    return {nid: {"vmax": d["vmax"], "a0": d["a0"], "n_attempts": len(d["vmax"])} for nid, d in per.items()}


def combine_ceilings(con, ct_paths: Sequence[Path] | Path, cfg: dict[str, Any]) -> dict[int, dict[str, float]]:
    """Per-player Combine ceiling computed with the shared Phase-2 fitter (fixture path).

    Fits ``v(t)=vmax*(1-exp(-t/tau))`` (``combine_features.fit_envelope``) on the straight-line
    drill attempts and averages ``env_vmax`` / ``env_a0`` over a player's attempts. Used to drive
    the synthetic fixture end-to-end; a full run reads the committed Phase-2
    ``per_player_envelope.csv`` instead.
    """
    env = cfg["envelope"]
    fit_ctx = env["fit"]
    drills = env["fit_drills"]
    drills = drills if isinstance(drills, list) else [drills]
    dlist = ",".join(f"'{d}'" for d in drills)
    arr = con.execute(f"""
        SELECT nfl_id, event_id, x, y,
               (epoch_ms(time) - min(epoch_ms(time)) OVER (PARTITION BY event_id)) / 1000.0 AS t
        FROM {pq_list(ct_paths)}
        WHERE entity_type = 'PLAYER' AND drill_type IN ({dlist})
        ORDER BY nfl_id, event_id, t
    """).fetchnumpy()

    def _s(x):
        return x.decode() if isinstance(x, (bytes, bytearray)) else str(x)

    per: dict[int, dict[str, list[float]]] = {}
    n = len(arr["nfl_id"])
    key, cur = None, None

    def flush(k, c):
        if c is None or len(c["x"]) < 2:
            return
        nid = int(k)
        x = np.asarray(c["x"], float); y = np.asarray(c["y"], float); t = np.asarray(c["t"], float)
        v, _ = cf._speed_path(x, y, t)
        onset = float(fit_ctx["onset_speed_yds"])
        idx = np.where(v >= onset)[0]
        i0 = int(idx[0]) if len(idx) else 0
        vmax, tau, a0 = cf.fit_envelope(t - t[i0], v, cfg)
        if np.isfinite(vmax):
            per.setdefault(nid, {"vmax": [], "a0": []})
            per[nid]["vmax"].append(float(vmax))
            if np.isfinite(a0):
                per[nid]["a0"].append(float(a0))

    for i in range(n):
        nid = int(arr["nfl_id"][i])
        ev = _s(arr["event_id"][i])
        if (nid, ev) != key:
            flush(key[0] if key else None, cur)
            key, cur = (nid, ev), {"x": [], "y": [], "t": []}
        cur["x"].append(float(arr["x"][i]))
        cur["y"].append(float(arr["y"][i]))
        cur["t"].append(float(arr["t"][i]))
    if key is not None:
        flush(key[0], cur)

    return {
        nid: {"vmax": float(np.mean(d["vmax"])) if d["vmax"] else float("nan"),
              "a0": float(np.mean(d["a0"])) if d["a0"] else float("nan")}
        for nid, d in per.items()
    }
