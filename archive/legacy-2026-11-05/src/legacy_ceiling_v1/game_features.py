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


def _despike_scan_sql(gt_expr: str, cap: float) -> str:
    """`WITH RECURSIVE ... scan AS (...)` prefix for the frame-level **greedy forward despike**.

    Revision 3 of the game-side speed QC (see `notes/decisions.md` D35): rather than clamping a
    spike *to* the cap (which keeps a fabricated value) or dropping the whole effort (which loses a
    genuine effort), we **discard only the impossible per-frame speed sample** and re-measure the
    next frame from the last *retained* frame.

    Each effort (one player on one play) is scanned in time order, carrying the **last retained
    frame** forward:

    * the **first** frame is always retained (it has no incoming sample);
    * for each later frame the sample = ``step distance / dt`` from the last retained frame;
    * if that sample is ``<= cap`` (``game.speed_cap_yds``) — or ``dt <= 0`` (undefined, kept as
      NULL, *not* a violation) — the frame is **retained** and becomes the new last-good frame;
    * otherwise the frame is **discarded** and the last-good frame is left unchanged, so the *next*
      frame is re-measured from the same last-good frame (the spike is skipped, its valid neighbour
      is not contaminated by it and is **not** dropped).

    By construction every retained sample is ``<= cap``, so no emitted ``peak_speed`` can exceed the
    cap; a clean effort below the cap is left byte-for-byte unchanged. A truly degenerate effort
    (no post-first frame can be retained) is still **kept** with ``peak_speed = 0.0`` (the caller
    ``COALESCE``s the empty max). The prefix exposes the ``scan`` CTE (one row per frame, with
    ``kept`` / ``spd`` / the running last-good coords ``ct,cx,cy``); callers append their own SELECT.
    """
    f = _kinematics_cte(gt_expr)
    cap_sql = float(cap)
    # distance from the last retained frame (o) to the candidate frame (s carries last-good coords)
    dist = "sqrt((o.x-s.cx)*(o.x-s.cx) + (o.y-s.cy)*(o.y-s.cy))"
    dt_ms = "(o.t - s.ct)"
    spd = f"CASE WHEN o.t > s.ct THEN {dist} / ({dt_ms}/1000.0) END"
    # NOTE: a recursive term cannot reference its own select-list aliases, so <kept> is repeated.
    kept = (
        "CASE WHEN s.ct IS NULL THEN TRUE "
        "WHEN o.t <= s.ct THEN TRUE "
        f"WHEN {dist} / ({dt_ms}/1000.0) <= {cap_sql} THEN TRUE "
        "ELSE FALSE END"
    )
    return f"""
        WITH RECURSIVE f AS ({f}),
        ord AS (
            SELECT game_id, play_id, nfl_id, t, x, y,
                   row_number() OVER (PARTITION BY game_id, play_id, nfl_id ORDER BY t) AS rn
            FROM f
        ),
        scan AS (
            -- anchor: the first frame is always retained; no incoming sample (spd NULL)
            SELECT game_id, play_id, nfl_id, rn, t, x, y,
                   CAST(NULL AS BIGINT) AS pt, CAST(NULL AS REAL) AS px, CAST(NULL AS REAL) AS py,
                   TRUE AS kept, CAST(NULL AS DOUBLE) AS spd,
                   t AS ct, x AS cx, y AS cy
            FROM ord WHERE rn = 1
            UNION ALL
            -- step: measure the next frame from the last retained frame (s), then decide kept
            SELECT o.game_id, o.play_id, o.nfl_id, o.rn, o.t, o.x, o.y,
                   s.ct, s.cx, s.cy,
                   {kept},
                   {spd},
                   CASE WHEN {kept} THEN o.t ELSE s.ct END,
                   CASE WHEN {kept} THEN o.x ELSE s.cx END,
                   CASE WHEN {kept} THEN o.y ELSE s.cy END
            FROM scan s
            JOIN ord o
              ON o.game_id = s.game_id AND o.play_id = s.play_id AND o.nfl_id = s.nfl_id
             AND o.rn = s.rn + 1
        )
    """


def effort_kinematics_sql(gt_expr: str, cap: float) -> str:
    """Per (game_id, play_id, nfl_id): peak x/y-derived speed and peak x/y-derived acceleration.

    Speed = step distance / dt (yd/s); accel = d(speed)/dt (yd/s^2). Both are recomputed from
    x,y — the vendor `s`/`a` columns are NOT used (audit f).

    ``cap`` is the **data-QC speed bound** (yd/s; ``game.speed_cap_yds``, 12.5 = 25.6 mph). It is
    enforced by the frame-level **greedy despike** in :func:`_despike_scan_sql`: impossible
    per-frame speed samples (> cap) are **discarded** and the peak/accel are recomputed from the
    retained (valid) frames only. The effort is always kept; a clean effort below the cap is
    untouched. Peak accel is ``d(speed)/dt`` over the retained frames only (derived from the
    retained speed series), and the empty case is coalesced to ``0.0``.

    ``n_frames`` keeps its pre-change meaning = the number of retained frames carrying an incoming
    sample (retained frames other than the anchor), so a clean effort is byte-identical to the
    previous behaviour. Null-safety: a ``dt=0`` frame yields a NULL speed; it stays NULL (never a
    sample, never a fabricated value) — the first frame anchors the effort.
    """
    scan = _despike_scan_sql(gt_expr, cap)
    return scan + f"""
        , va AS (
            SELECT game_id, play_id, nfl_id, t, rn, spd,
                   (spd - lag(spd) OVER w2) / NULLIF((t - lag(t) OVER w2)/1000.0, 0) AS acc
            FROM scan WHERE kept
            WINDOW w2 AS (PARTITION BY game_id, play_id, nfl_id ORDER BY t, rn)
        )
        SELECT game_id, play_id, nfl_id,
               count(*) FILTER (WHERE rn > 1) AS n_frames,
               COALESCE(max(spd), 0.0) AS peak_speed,
               COALESCE(max(acc), 0.0) AS peak_accel
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
    kin = effort_kinematics_sql(gt, cfg["game"]["speed_cap_yds"])
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
    """Per-player route-snap counts (``route_ran IS NOT NULL``) — ungrouped (all games)."""
    rows = con.execute(
        f"SELECT nfl_id, count(*) FROM {common.pq(pp_path)} "
        f"WHERE route_ran IS NOT NULL GROUP BY 1").fetchall()
    return {int(n): int(c) for n, c in rows}


def route_snap_counts_by_type(con, pp_path: Path, games_path: Path) -> list[dict]:
    """Per-player route-snap counts split by the TRUE ``season`` / ``season_type`` (D49).

    One build carries every scope: the modelling stages sum the rows whose ``season_type`` is in
    the active scope, so the snap-count denominator, ``snap_share`` and the ``min_snaps`` gate are
    all filtered by the same rule. A game_id present in ``player_play`` but absent from
    ``games.csv`` is a hard error (never a silent guess).
    """
    missing = [int(r[0]) for r in con.execute(f"""
        SELECT DISTINCT pp.game_id
        FROM {common.pq(pp_path)} pp
        LEFT JOIN {common.pq(games_path)} g ON g.game_id = pp.game_id
        WHERE pp.route_ran IS NOT NULL AND g.game_id IS NULL
    """).fetchall()]
    if missing:
        ids = sorted(set(missing))
        raise common.SeasonLookupError(
            f"{len(ids)} game_id(s) with route snaps are absent from games.csv "
            f"(games.csv IS present): {ids}")
    rows = con.execute(f"""
        SELECT pp.nfl_id, g.season, g.season_type, count(*) AS n_route_snaps
        FROM {common.pq(pp_path)} pp
        JOIN {common.pq(games_path)} g ON g.game_id = pp.game_id
        WHERE pp.route_ran IS NOT NULL
        GROUP BY 1, 2, 3
    """).fetchall()
    return [dict(nfl_id=int(nid), season=(int(season) if season is not None else None),
                 season_type=(str(stype) if stype is not None else None),
                 n_route_snaps=int(n))
            for nid, season, stype, n in sorted(
                rows, key=lambda r: (int(r[0]), r[1] or 0, r[2] or ""))]


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
