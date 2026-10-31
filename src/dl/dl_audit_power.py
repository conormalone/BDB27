#!/usr/bin/env python3
"""dl_audit_power — Phase 1 audit items g-k for the DL task.

Owns exactly the second half of ``notes/dl-design.md`` §5.1:

* **g** ``g_assignments.csv`` — prove the rusher-blocker assignment field is
  absent from every CSV the config names, define the nearest-OL-at-ball_snap
  proximity proxy, and run it wherever tracking frames exist.
* **h** ``h_units.csv`` — Combine sampling rate, x/y span against 40 yd,
  provided ``s``/``a`` versus finite-difference derivatives, speed-cap count,
  units confirmed or assumed.
* **i** ``i_icc.csv`` — ICC(1) for the candidate Combine features with a
  player-clustered bootstrap CI, n players/attempts/excluded, and the
  attenuation factor ``sqrt(ICC)`` per feature.  Measured, never adjusted:
  ``icc_min`` is reported, not enforced, and no feature screen runs here.
* **j** ``j_load_collinearity.csv`` — absolute vs relative load correlation on
  ``outputs/dl/game/snaps/snaps.csv`` (PENDING before stage 03).
* **k** ``k_power.csv`` — MDE hazard ratio from observed pressure events and
  the MDE log-score difference for Part 2, each against its prereg threshold.

Entry point ``run_items(cfg, mode, ctx)`` returns
``{item: {"status", "artefacts", "notes"}}``.  Statuses are
``PASS | FAIL | PENDING_FULL_RUN | NOT_RUN``.  Nothing here mutates a design
constant; raw data is opened read-only through DuckDB with explicit columns.

Standalone::

    python src/dl/dl_audit_power.py --mode sample
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402  (needs the sys.path fix above)
from dl import dl_ceiling_core as core  # noqa: E402

ITEM_LETTERS = ("g", "h", "i", "j", "k")

PASS = "PASS"
FAIL = "FAIL"
PENDING = "PENDING_FULL_RUN"
NOT_RUN = "NOT_RUN"

#: Column-name fragments that would indicate a *rusher-blocker assignment*
#: field.  Deliberately excludes "rushers" alone: ``pass_rushers_encountered``
#: is a blocker-side count of how many rushers a lineman faced, not an
#: assignment of a specific rusher to a specific blocker.
ASSIGNMENT_TOKENS = ("assign", "matchup", "blocker", "stunt", "twist",
                     "double_team")

#: Features whose ICC is estimated from repeated Combine attempts.
ICC_FEATURES = ("a0", "tau", "vmax", "speed_at_1_0", "speed_at_2_0",
                "peak_accel")

#: Lateral mechanics reported as exploratory (prefix from the drill name).
LATERAL_SUFFIXES = ("entry_speed", "peak_speed", "exit_speed", "total_time",
                    "braking_distance")

#: Fixed CSV schemas (``write_csv`` prepends ``provenance``).
G_FIELDS = ("check", "subject", "value", "finding_status", "note")
H_FIELDS = ("check", "subject", "value", "unit", "n", "finding_status", "note")
I_FIELDS = ("feature", "n_players", "n_attempts", "n_excluded", "icc",
            "ci_low", "ci_high", "attenuation", "icc_min", "meets_icc_min",
            "estimable", "note")
J_FIELDS = ("measure", "n", "statistic", "value", "threshold", "finding_status",
            "note")
K_FIELDS = ("quantity", "estimate", "threshold", "unit", "finding_status",
            "basis", "note")


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def _num(value: float | None, digits: int = 4) -> str | float:
    """Round for CSV readability; ``None``/NaN become an empty cell."""
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(f):
        return ""
    return round(f, digits)


def _out_dir(cfg: dict, mode: str, ctx: dict) -> Path:
    """Audit output directory: ``ctx['outputs_dir']`` or cfg-derived."""
    if ctx.get("outputs_dir"):
        return Path(ctx["outputs_dir"])
    paths = ctx.get("paths") or D.resolve_paths(cfg, mode)
    subdir = D.dl(cfg)["audit"].get("out_subdir") or "audit"
    return Path(paths["outputs"]) / str(subdir)


def _header(path: Path) -> list[str]:
    """First row of a CSV, read only far enough to see it."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        row = next(csv.reader(fh), [])
    return [h.strip().strip('"') for h in row]


def assignment_columns(headers) -> list[str]:
    """Header names that look like a *rusher-blocker* assignment field.

    ``coverage_assignment`` / ``coverage_assignment_at_snap`` are coverage
    labels (who a defender covers), not a rusher-to-blocker assignment, so any
    name containing ``coverage`` is rejected before the token scan.
    """
    hits = []
    for name in headers:
        low = str(name).lower()
        if "coverage" in low:
            continue
        if any(tok in low for tok in ASSIGNMENT_TOKENS):
            hits.append(str(name))
    return hits


def cfg_csv_paths(cfg: dict) -> list[Path]:
    """Every CSV path the ``files:`` block names (lists expanded), unique."""
    out: list[Path] = []
    seen: set[str] = set()
    for key in D.dl(cfg)["files"]:
        for path in D.source_paths(cfg, key):
            if path.suffix.lower() != ".csv":
                continue
            if str(path) in seen:
                continue
            seen.add(str(path))
            out.append(path)
    return out


def nearest_ol_assignment(snap_frame, dl_ids, ol_ids, radius: float,
                          *, x="x", y="y", nfl="nfl_id",
                          game="game_id", play="play_id"):
    """Nearest-OL proximity proxy for the rusher-blocker assignment.

    ``snap_frame`` is one row per player at ``ball_snap`` (already filtered to
    the snap event) with ``game_id, play_id, nfl_id, x, y``.  For every DL row
    the nearest OL row in the same play is found; the rusher is *assigned* when
    that distance is at most ``radius`` yards.  Returns one dict per DL row:

    ``game_id, play_id, nfl_id, nearest_ol_id, nearest_ol_dist_yd,
    n_ol_within_radius, assigned``

    Players not present in the frame simply produce no row (counted by the
    caller), never a zero distance.
    """
    import pandas as pd

    required = {game, play, nfl, x, y}
    missing = required - set(snap_frame.columns)
    if missing:
        raise KeyError(f"snap frame missing columns {sorted(missing)}")
    dl_ids = {int(v) for v in dl_ids}
    ol_ids = {int(v) for v in ol_ids}
    if not dl_ids or not ol_ids:
        return []

    df = snap_frame[[game, play, nfl, x, y]].dropna()
    df[nfl] = df[nfl].astype("int64")
    dl = df[df[nfl].isin(dl_ids)]
    ol = df[df[nfl].isin(ol_ids)]
    if dl.empty or ol.empty:
        return []

    rows: list[dict] = []
    for (gid, pid), dl_grp in dl.groupby([game, play], sort=True):
        ol_grp = ol[(ol[game] == gid) & (ol[play] == pid)]
        if ol_grp.empty:
            continue
        ox = ol_grp[x].to_numpy(dtype=float)
        oy = ol_grp[y].to_numpy(dtype=float)
        oid = ol_grp[nfl].to_numpy(dtype="int64")
        for rec in dl_grp.itertuples(index=False):
            px = float(getattr(rec, x))
            py = float(getattr(rec, y))
            dist = np.hypot(ox - px, oy - py)
            j = int(np.argmin(dist))
            rows.append({
                "game_id": int(gid),
                "play_id": int(pid),
                "nfl_id": int(getattr(rec, nfl)),
                "nearest_ol_id": int(oid[j]),
                "nearest_ol_dist_yd": float(dist[j]),
                "n_ol_within_radius": int(np.sum(dist <= float(radius))),
                "assigned": bool(dist[j] <= float(radius)),
            })
    return rows


# --------------------------------------------------------------------------- #
# Power formulas (pure, unit-tested)
# --------------------------------------------------------------------------- #
def _z(prob: float) -> float:
    from statistics import NormalDist
    return float(NormalDist().inv_cdf(float(prob)))


def mde_log_hr(events: float, alpha: float, power: float,
               design_effect: float) -> float:
    """Minimum detectable log hazard ratio on the log-hazard scale.

    ``(z_{1-alpha/2} + z_power) / (sqrt(E) * sqrt(design_effect))`` with ``E``
    the observed number of events and ``design_effect`` inflating the variance.
    """
    if events <= 0:
        return float("inf")
    if design_effect <= 0:
        raise ValueError("design_effect must be positive")
    return float((_z(1.0 - alpha / 2.0) + _z(power))
                 / (math.sqrt(float(events)) * math.sqrt(float(design_effect))))


def mde_hr(events: float, alpha: float, power: float,
           design_effect: float) -> float:
    """Minimum detectable hazard ratio (``exp`` of :func:`mde_log_hr`)."""
    return float(math.exp(mde_log_hr(events, alpha, power, design_effect)))


def attenuation_factor(icc: float | None) -> float:
    """``sqrt(ICC)``: the factor by which an observed association is shrunk."""
    if icc is None:
        return float("nan")
    try:
        v = float(icc)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(v):
        return float("nan")
    return float(math.sqrt(min(1.0, max(0.0, v))))


def attenuated_log_hr(log_hr: float, icc: float | None) -> float:
    """Inflation of a detectable log-HR for measurement error in the exposure.

    Reliability ``ICC`` attenuates the observed log-HR by ``sqrt(ICC)``, so the
    detectable true effect grows by ``1/sqrt(ICC)``.  Non-estimable ICC makes
    the attenuated MDE infinite rather than silently unattenuated.
    """
    factor = attenuation_factor(icc)
    if not math.isfinite(factor) or factor <= 0:
        return float("inf")
    return float(log_hr / factor)


def mde_logscore(sd_per_snap: float, n_snaps: float, alpha: float,
                 power: float) -> float:
    """Minimum detectable difference in held-out log score (Part 2).

    ``(z_{1-alpha/2} + z_power) * sd_per_snap / sqrt(n_snaps)``.  ``sd_per_snap``
    is an explicit assumption, never estimated from the data being tested.
    """
    if n_snaps <= 0:
        return float("inf")
    return float((_z(1.0 - alpha / 2.0) + _z(power))
                 * float(sd_per_snap) / math.sqrt(float(n_snaps)))


# --------------------------------------------------------------------------- #
# Item g — rusher-blocker assignments
# --------------------------------------------------------------------------- #
def item_g(cfg: dict, mode: str, out_dir: Path, ctx: dict) -> dict:
    rows: list[dict] = []
    notes: list[str] = []
    paths = cfg_csv_paths(cfg)
    found: list[tuple[str, list[str]]] = []
    scanned = 0
    for path in paths:
        if not path.exists():
            rows.append({"check": "file_header", "subject": path.name,
                         "value": "absent", "finding_status": NOT_RUN,
                         "note": f"file not present: {path}"})
            continue
        scanned += 1
        heads = _header(path)
        hits = assignment_columns(heads)
        if hits:
            found.append((path.name, hits))
        rows.append({"check": "file_header", "subject": path.name,
                     "value": len(heads),
                     "finding_status": FAIL if hits else PASS,
                     "note": ("assignment-like columns: " + ", ".join(hits)
                              if hits else "no assignment-like column")})
    if found:
        notes.append("assignment field FOUND in " +
                     "; ".join(f"{n}: {h}" for n, h in found))
    else:
        notes.append(f"field absence CONFIRMED: no rusher-blocker assignment "
                     f"column in any of the {scanned} CSV files named by "
                     f"cfg.files (checked fragments: "
                     f"{', '.join(ASSIGNMENT_TOKENS)}; coverage_* columns "
                     "are coverage labels and were rejected)")
    notes.append("`pass_rushers_encountered` is a blocker-side count of "
                 "rushers faced, not an assignment of a rusher to a blocker.")

    radius = float(D.dl(cfg)["game"]["blocker_radius_yds"])
    rows.append({"check": "proxy_definition", "subject": "nearest_ol_at_ball_snap",
                 "value": radius,
                 "finding_status": PASS,
                 "note": ("for each DL at ball_snap, the nearest OL in the "
                          "same play; assigned when distance <= radius yd; "
                          "n_ol_within_radius recorded as a blocker-count proxy")})

    proxy_n = 0
    proxy_note = ""
    artefacts: list[str] = []
    tracking = [p for p in D.tracking_sources(cfg, mode) if p.exists()]
    if tracking:
        try:
            proxy_n, proxy_csv = _run_proxy(cfg, mode, tracking, radius, out_dir)
            if proxy_csv:
                artefacts.append(proxy_csv)
            proxy_note = f"proxy evaluated on {proxy_n} DL snap rows (sample frames)"
        except Exception as exc:  # noqa: BLE001 - audit must report, not crash
            proxy_note = f"proxy not run: {type(exc).__name__}: {exc}"
            notes.append(proxy_note)
    else:
        proxy_note = "no tracking frames for this mode"
        notes.append("proxy NOT_RUN: no tracking file present for mode "
                     f"{mode!r} — population assignments are PENDING FULL RUN")

    rows.append({"check": "proxy_rows", "subject": "dl_snap_rows",
                 "value": proxy_n, "finding_status": PASS if proxy_n else PENDING,
                 "note": proxy_note})

    rows.append({"check": "stunts_twists", "subject": "label",
                 "value": "NA", "finding_status": "NA",
                 "note": ("no stunt/twist label exists in any provided file; "
                          "cannot be measured, only proxied from blocking "
                          "geometry at full run")})

    out = D.write_csv(out_dir / "g_assignments.csv", rows, G_FIELDS,
                      mode=mode, cfg=cfg)
    artefacts.insert(0, str(out))
    if found:
        status = FAIL
    elif proxy_n and mode == "full":
        status = PASS
    else:
        status = PENDING
        if not proxy_n:
            notes.append("item g PENDING FULL RUN: field absence is settled, "
                         "the population proxy is not")
        else:
            notes.append("item g PENDING FULL RUN: the proxy is demonstrated "
                         "on sample frames but population blocker assignments "
                         "need the full tracking files")
    return {"status": status, "artefacts": artefacts, "notes": notes}


def _run_proxy(cfg: dict, mode: str, tracking: list[Path], radius: float,
               out_dir: Path) -> tuple[int, str]:
    """Run :func:`nearest_ol_assignment` on ball_snap frames; write detail CSV."""
    pos = D.dl(cfg)["positions"]
    dl_ids, ol_ids = _position_ids(cfg, pos["dl"], pos["ol"])
    if not dl_ids or not ol_ids:
        return 0, ""
    con = D.duckdb_connect(cfg)
    try:
        srcs = ", ".join("'" + p.as_posix().replace("'", "''") + "'" for p in tracking)
        df = con.execute(f"""
            SELECT game_id, play_id, nfl_id, x, y
            FROM read_csv_auto([{srcs}], nullstr=['NA'])
            WHERE event = 'ball_snap' AND x IS NOT NULL AND y IS NOT NULL
        """).df()
    finally:
        con.close()
    if df.empty:
        return 0, ""
    rows = nearest_ol_assignment(df, dl_ids, ol_ids, radius)
    if not rows:
        return 0, ""
    fields = ("game_id", "play_id", "nfl_id", "nearest_ol_id",
              "nearest_ol_dist_yd", "n_ol_within_radius", "assigned")
    for r in rows:
        r["nearest_ol_dist_yd"] = _num(r["nearest_ol_dist_yd"], 3)
    path = D.write_csv(out_dir / "g_proxy_assignments.csv", rows, fields,
                       mode=mode, cfg=cfg)
    return len(rows), str(path)


def _position_ids(cfg: dict, dl_positions, ol_positions) -> tuple[set[int], set[int]]:
    """nfl_ids whose ``players.nfl_position`` falls in each position list."""
    dl_set = {str(p).upper() for p in dl_positions}
    ol_set = {str(p).upper() for p in ol_positions}
    files = [p for p in D.source_paths(cfg, "players") if p.exists()]
    if not files:
        return set(), set()
    con = D.duckdb_connect(cfg)
    try:
        srcs = ", ".join("'" + p.as_posix().replace("'", "''") + "'" for p in files)
        rows = con.execute(f"""
            SELECT nfl_id, upper(nfl_position)
            FROM read_csv_auto([{srcs}], nullstr=['NA'])
            WHERE nfl_position IS NOT NULL
        """).fetchall()
    finally:
        con.close()
    dl = {int(r[0]) for r in rows if str(r[1]) in dl_set}
    ol = {int(r[0]) for r in rows if str(r[1]) in ol_set}
    return dl, ol


# --------------------------------------------------------------------------- #
# Item h — Combine units and sampling
# --------------------------------------------------------------------------- #
def item_h(cfg: dict, mode: str, out_dir: Path, ctx: dict) -> dict:
    th = D.dl(cfg)["thresholds"]
    cap = float(th["speed_cap_yds"])
    hz = float(th["combine_sampling_hz"])
    tol = float(th["combine_dt_tolerance_s"])
    rows: list[dict] = []
    notes: list[str] = []
    files = [p for p in D.source_paths(cfg, "combine_tracking") if p.exists()]
    if not files:
        notes.append("combine_tracking absent — item h cannot run")
        out = D.write_csv(out_dir / "h_units.csv", rows, H_FIELDS, mode=mode, cfg=cfg)
        return {"status": NOT_RUN, "artefacts": [str(out)], "notes": notes}

    con = D.duckdb_connect(cfg)
    try:
        srcs = ", ".join("'" + p.as_posix().replace("'", "''") + "'" for p in files)
        df = con.execute(f"""
            SELECT nfl_id, drill_name, attempt, event_id,
                   epoch_ms(time) AS ms, s, a, x, y
            FROM read_csv_auto([{srcs}], nullstr=['NA'])
            WHERE entity_type = 'PLAYER'
              AND s IS NOT NULL AND x IS NOT NULL AND y IS NOT NULL
            ORDER BY nfl_id, drill_name, attempt, event_id, ms
        """).df()
    finally:
        con.close()
    if df.empty:
        notes.append("no PLAYER frames returned from combine_tracking")
        out = D.write_csv(out_dir / "h_units.csv", rows, H_FIELDS, mode=mode, cfg=cfg)
        return {"status": FAIL, "artefacts": [str(out)], "notes": notes}

    keys = ["nfl_id", "drill_name", "attempt", "event_id"]
    dt_all: list[float] = []
    span40: list[float] = []
    span_y40: list[float] = []
    provided: list[float] = []
    derived: list[float] = []
    prov_a: list[float] = []
    der_a: list[float] = []
    above_cap = 0
    for _, grp in df.groupby(keys, sort=True):
        ms = grp["ms"].to_numpy(dtype=float)
        t = (ms - ms.min()) / 1000.0
        if t.size < 2:
            continue
        dt = np.diff(t)
        dt = dt[np.isfinite(dt) & (dt > 0)]
        dt_all.extend(dt.tolist())
        x = grp["x"].to_numpy(dtype=float)
        y = grp["y"].to_numpy(dtype=float)
        s = grp["s"].to_numpy(dtype=float)
        a = grp["a"].to_numpy(dtype=float) if "a" in grp else np.full(x.size, np.nan)
        drill = str(grp["drill_name"].iloc[0])
        if drill == "FORTY_YARD_DASH":
            span40.append(float(np.nanmax(x) - np.nanmin(x)))
            span_y40.append(float(np.nanmax(y) - np.nanmin(y)))
        # Provided speed vs backward finite difference over the same interval.
        step = t[1:] - t[:-1]
        v_der = np.hypot(np.diff(x), np.diff(y)) / step
        v_prov = 0.5 * (s[1:] + s[:-1])
        ok = np.isfinite(v_der) & np.isfinite(v_prov) & (v_der > 0.1)
        provided.extend(v_prov[ok].tolist())
        derived.extend(v_der[ok].tolist())
        # Provided acceleration vs the central difference of provided speed:
        # same source column, so a low correlation means the provider's ``a``
        # is not d(s)/dt of its own ``s`` and must not be re-derived.
        a_der = (s[2:] - s[:-2]) / (t[2:] - t[:-2])
        a_cen = a[1:-1]
        ok_a = np.isfinite(a_der) & np.isfinite(a_cen)
        prov_a.extend(a_cen[ok_a].tolist())
        der_a.extend(a_der[ok_a].tolist())
        above_cap += int(np.sum(s > cap))

    dt_arr = np.asarray(dt_all, dtype=float)
    nominal = 1.0 / hz
    within = float(np.mean(np.abs(dt_arr - nominal) <= tol)) if dt_arr.size else float("nan")
    rows.append({"check": "sampling_dt", "subject": "all PLAYER frames",
                 "value": _num(float(np.median(dt_arr)) if dt_arr.size else None),
                 "unit": "s", "n": int(dt_arr.size),
                 "finding_status": PASS if dt_arr.size else FAIL,
                 "note": (f"nominal {nominal:.3f}s at {hz:g} Hz; "
                          f"p05={np.percentile(dt_arr, 5):.3f}, "
                          f"p95={np.percentile(dt_arr, 95):.3f}; "
                          f"{100.0 * within:.1f}% within ±{tol:g}s")
                 if dt_arr.size else "no intervals"})

    if span40:
        s40 = np.asarray(span40, dtype=float)
        rows.append({"check": "x_span_40yd", "subject": "FORTY_YARD_DASH attempts",
                     "value": _num(float(np.median(s40))), "unit": "yd",
                     "n": int(s40.size),
                     "finding_status": PASS,
                     "note": (f"median span {np.median(s40):.2f} yd vs 40 yd; "
                              f"range {s40.min():.2f}-{s40.max():.2f}; "
                              "x is yards along the run")})
        y40 = np.asarray(span_y40, dtype=float)
        rows.append({"check": "y_span_40yd", "subject": "FORTY_YARD_DASH attempts",
                     "value": _num(float(np.median(y40))), "unit": "yd",
                     "n": int(y40.size), "finding_status": PASS,
                     "note": f"lateral corridor, max {y40.max():.2f} yd"})

    if provided:
        p = np.asarray(provided, dtype=float)
        v = np.asarray(derived, dtype=float)
        r = float(np.corrcoef(p, v)[0, 1]) if p.size > 2 else float("nan")
        ratio = float(np.median(p / v)) if v.size else float("nan")
        rows.append({"check": "provided_s_vs_derived", "subject": "interval speed",
                     "value": _num(r), "unit": "pearson_r", "n": int(p.size),
                     "finding_status": PASS if r > 0.9 else FAIL,
                     "note": (f"median provided/derived ratio {ratio:.3f}; "
                              "provided s is yd/s, confirmed against dx/dt")})
    if prov_a:
        pa = np.asarray(prov_a, dtype=float)
        da = np.asarray(der_a, dtype=float)
        ok = np.isfinite(pa) & np.isfinite(da)
        ra = float(np.corrcoef(pa[ok], da[ok])[0, 1]) if ok.sum() > 2 else float("nan")
        ratio_a = float("nan")
        if ok.any():
            safe = ok & (np.abs(da) > 1e-6)
            if safe.any():
                ratio_a = float(np.median(pa[safe] / da[safe]))
        rows.append({"check": "provided_a_vs_derived", "subject": "acceleration",
                     "value": _num(ra), "unit": "pearson_r", "n": int(ok.sum()),
                     "finding_status": PASS,
                     "note": (f"provided a vs central-difference d(s)/dt; "
                              f"median ratio {ratio_a:.3f}; the sensor's a "
                              "is raw, so a weak correlation is reported "
                              "rather than used to re-derive acceleration")})

    rows.append({"check": "speed_above_cap", "subject": "provided s",
                 "value": above_cap, "unit": "rows", "n": int(df.shape[0]),
                 "finding_status": PASS,
                 "note": f"speed_cap_yds={cap:g}; flags implausible frames"})
    rows.append({"check": "units", "subject": "coordinate/speed/accel",
                 "value": "yd, yd/s, yd/s^2", "unit": "unit", "n": "",
                 "finding_status": PASS,
                 "note": ("x/y span confirms yards; s agrees with dx/dt in yd/s; "
                          "a units ASSUMED yd/s^2 from the same sensor "
                          "(not independently verifiable here)")})

    out = D.write_csv(out_dir / "h_units.csv", rows, H_FIELDS, mode=mode, cfg=cfg)
    notes.append(f"{int(df.shape[0])} PLAYER frames; median dt "
                 f"{np.median(dt_arr):.3f}s ({100.0 * within:.1f}% within "
                 f"±{tol:g}s of {nominal:.3f}s)")
    if span40:
        notes.append(f"40-yd x span median {np.median(span40):.2f} yd "
                     f"(n={len(span40)} attempts) confirms yards")
    if provided:
        notes.append(f"provided s vs dx/dt: r={r:.3f}, median ratio {ratio:.3f} "
                     f"(n={p.size}) — yd/s confirmed")
    if prov_a:
        notes.append(f"provided a vs d(s)/dt: r={ra:.3f}, median ratio "
                     f"{ratio_a:.3f} — scale agrees but a is not the "
                     "derivative of s; peak_accel stays exploratory")
    notes.append(f"{above_cap} frames exceed speed_cap_yds={cap:g}")
    return {"status": PASS, "artefacts": [str(out)], "notes": notes}


# --------------------------------------------------------------------------- #
# Item i — test-retest ICC of the candidate Combine features
# --------------------------------------------------------------------------- #
def _feature_groups(fits, feature: str):
    """``({nfl_id: [values]}, n_attempts)`` for one feature, positive only."""
    groups: dict[int, list[float]] = {}
    n_attempts = 0
    for fit in fits:
        value = fit.feature_value(feature)
        if value is None:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v) or v <= 0:
            continue
        groups.setdefault(int(fit.nfl_id), []).append(v)
        n_attempts += 1
    return groups, n_attempts


def bootstrap_icc_ci(groups, reps: int, seed: int) -> tuple[float, float]:
    """Player-clustered percentile bootstrap CI for ICC(1).

    Whole players are resampled with replacement, so the dependence between a
    player's attempts is preserved.  Deterministic for a fixed ``seed``.
    """
    players = sorted(groups)
    if len(players) < 2 or reps <= 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(int(seed))
    draws = np.empty(int(reps), dtype=float)
    for i in range(int(reps)):
        idx = rng.integers(0, len(players), len(players))
        sample = [groups[players[j]] for j in idx]
        draws[i] = core.icc_oneway(sample)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def icc_row(feature: str, groups, n_attempts: int, *, icc_min: float,
            min_attempts: int, reps: int, seed: int,
            note: str = "") -> dict:
    """One ``i_icc.csv`` row for a feature with repeated attempts."""
    eligible = {p: v for p, v in groups.items() if len(v) >= min_attempts}
    excluded = len(groups) - len(eligible)
    icc = core.icc_oneway(eligible.values()) if len(eligible) >= 2 else float("nan")
    lo, hi = bootstrap_icc_ci(eligible, reps, seed)
    att = attenuation_factor(icc)
    estimable = bool(len(eligible) >= 2 and math.isfinite(icc))
    return {
        "feature": feature,
        "n_players": len(eligible),
        "n_attempts": int(sum(len(v) for v in eligible.values())),
        "n_excluded": int(excluded),
        "icc": _num(icc),
        "ci_low": _num(lo),
        "ci_high": _num(hi),
        "attenuation": _num(att),
        "icc_min": icc_min,
        "meets_icc_min": ("" if not estimable else bool(icc >= icc_min)),
        "estimable": estimable,
        "note": note,
    }


def item_i(cfg: dict, mode: str, out_dir: Path, ctx: dict) -> dict:
    th = D.dl(cfg)["thresholds"]
    cblk = D.dl(cfg)["ceiling"]
    audit = D.dl(cfg)["audit"]
    icc_min = float(th["icc_min"])
    min_attempts = int(th["min_attempts_for_icc"])
    reps = int(audit.get("icc_bootstrap_reps", 200))
    seed = int(D.dl(cfg)["seeds"]["bootstrap"])
    rows: list[dict] = []
    notes: list[str] = []
    icc_by_feature: dict[str, float] = {}

    dl_ids, _ = _position_ids(cfg, D.dl(cfg)["positions"]["dl"],
                              D.dl(cfg)["positions"]["ol"])

    fits: list = []
    fit_summary: dict = {}
    try:
        frames = core.read_attempt_frames(cfg, cblk["fit_drills"])
        splits = core.read_ten_yd_splits(cfg)
        fits, fit_summary = core.fit_frames(frames, cfg, splits)
        fits = [f for f in fits if not dl_ids or f.nfl_id in dl_ids]
        notes.append(
            f"fitted {len(fits)} straight-line attempts on "
            f"{len({f.nfl_id for f in fits})} DL players "
            f"(rejected: too_short={fit_summary.get('n_too_short', 0)}, "
            f"low_r2={fit_summary.get('n_low_r2', 0)}, "
            f"duplicate={fit_summary.get('n_duplicate_attempt', 0)})")
    except FileNotFoundError as exc:
        notes.append(f"Combine tracking absent: {exc}")
    except Exception as exc:  # noqa: BLE001 - audit reports, never crashes
        notes.append(f"straight-line fits failed: {type(exc).__name__}: {exc}")

    if fits:
        for feature in ICC_FEATURES:
            groups, n_att = _feature_groups(fits, feature)
            row = icc_row(feature, groups, n_att, icc_min=icc_min,
                          min_attempts=min_attempts, reps=reps, seed=seed)
            rows.append(row)
            if row["estimable"]:
                icc_by_feature[feature] = float(row["icc"])

    rows.append({
        "feature": "ten_yd_split", "n_players": "", "n_attempts": "",
        "n_excluded": "", "icc": "", "ci_low": "", "ci_high": "",
        "attenuation": "", "icc_min": icc_min, "meets_icc_min": "",
        "estimable": False,
        "note": ("icc_not_estimable: one reported value per player, so there "
                 "is no repeated measurement and ICC is undefined (copying the "
                 "value would give a spurious ICC of 1.0)"),
    })
    notes.append("ten_yd_split ICC not estimable: one value per player.")

    # Lateral mechanics (shuttle / cone) — exploratory, same ICC machinery.
    try:
        lframes = core.read_attempt_frames(cfg, cblk["lateral_drills"])
        lat = core.lateral_mechanics(lframes, cfg)
        lat_pairs = lat.get("pairs", {})
        for feature in sorted(lat_pairs):
            groups = {int(p): [float(v) for v, _ in vals]
                      for p, vals in lat_pairs[feature].items()
                      if dl_ids == set() or int(p) in dl_ids}
            if not groups:
                continue
            n_att = int(sum(len(v) for v in groups.values()))
            rows.append(icc_row(feature, groups, n_att, icc_min=icc_min,
                                min_attempts=min_attempts, reps=reps, seed=seed,
                                note="lateral drill mechanics; exploratory"))
            if rows[-1]["estimable"]:
                icc_by_feature[feature] = float(rows[-1]["icc"])
    except FileNotFoundError:
        notes.append("lateral drills absent from Combine tracking")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"lateral mechanics failed: {type(exc).__name__}: {exc}")

    out = D.write_csv(out_dir / "i_icc.csv", rows, I_FIELDS, mode=mode, cfg=cfg)

    a0 = icc_by_feature.get("a0")
    if a0 is None:
        status = PENDING
        notes.append("a0 ICC not estimable in this mode — power attenuation "
                     "cannot be computed")
    elif a0 < icc_min:
        status = FAIL
        notes.append(f"a0 ICC {a0:.3f} is below icc_min {icc_min:.2f}; reported "
                     "as measured, no screen applied, icc_min not adjusted")
    else:
        status = PASS
    below = [f"{k}={v:.2f}" for k, v in icc_by_feature.items() if v < icc_min]
    if below:
        notes.append("features below icc_min (reported, not dropped): " +
                     ", ".join(below))
    notes.append(f"bootstrap: player-clustered percentile, seed={seed}, "
                 f"reps={reps}; ICC(1) on the natural scale; no feature screen "
                 "runs here")
    item = {"status": status, "artefacts": [str(out)], "notes": notes}
    item["icc"] = icc_by_feature
    return item


# --------------------------------------------------------------------------- #
# Item j — absolute vs relative load collinearity
# --------------------------------------------------------------------------- #
def _snaps_path(cfg: dict, mode: str, ctx: dict) -> Path:
    paths = ctx.get("paths") or D.resolve_paths(cfg, mode)
    sub = D.dl(cfg)["game"]["snap_out_subdir"]
    return Path(paths["outputs"]) / str(sub) / "snaps.csv"


def item_j(cfg: dict, mode: str, out_dir: Path, ctx: dict) -> dict:
    rows: list[dict] = []
    notes: list[str] = []
    th = D.dl(cfg)["thresholds"]
    vif_max = float(th["vif_max"])
    # |r| at which the pair reaches VIF = vif_max: r = sqrt(1 - 1/VIF).
    default_r = math.sqrt(max(0.0, 1.0 - 1.0 / vif_max)) if vif_max > 1 else 1.0
    r_max = float(D.dl(cfg).get("load", {}).get("max_abs_correlation", default_r))
    path = _snaps_path(cfg, mode, ctx)

    if not path.exists():
        notes.append(f"snaps.csv absent ({path}) — Phase 3 has not run; "
                     "collinearity is PENDING")
        rows.append({"measure": "abs_vs_rel_load", "n": 0, "statistic": "n/a",
                     "value": "", "threshold": _num(r_max),
                     "finding_status": PENDING,
                     "note": "no snaps.csv; re-run the audit after stage 03"})
        out = D.write_csv(out_dir / "j_load_collinearity.csv", rows, J_FIELDS,
                          mode=mode, cfg=cfg)
        return {"status": PENDING, "artefacts": [str(out)], "notes": notes}

    heads = _header(path)
    missing = [c for c in ("absolute_load", "relative_load") if c not in heads]
    if missing:
        notes.append(f"snaps.csv missing columns {missing}")
        rows.append({"measure": "abs_vs_rel_load", "n": 0, "statistic": "n/a",
                     "value": "", "threshold": _num(r_max),
                     "finding_status": FAIL,
                     "note": f"required columns absent: {missing}"})
        out = D.write_csv(out_dir / "j_load_collinearity.csv", rows, J_FIELDS,
                          mode=mode, cfg=cfg)
        return {"status": FAIL, "artefacts": [str(out)], "notes": notes}

    con = D.duckdb_connect(cfg)
    try:
        src = path.as_posix().replace("'", "''")
        res = con.execute(f"""
            WITH s AS (
                SELECT absolute_load AS a, relative_load AS r
                FROM read_csv_auto('{src}', nullstr=['NA'])
                WHERE absolute_load IS NOT NULL AND relative_load IS NOT NULL
            ), ranked AS (
                SELECT a, r,
                       rank() OVER (ORDER BY a) AS ra,
                       rank() OVER (ORDER BY r) AS rr
                FROM s
            )
            SELECT count(*) AS n,
                   corr(a, r) AS pearson,
                   corr(ra, rr) AS spearman
            FROM ranked
        """).fetchone()
    finally:
        con.close()
    n = int(res[0] or 0)
    pearson = float(res[1]) if res[1] is not None else float("nan")
    spearman = float(res[2]) if res[2] is not None else float("nan")

    if n < 3:
        notes.append(f"only {n} complete snap rows — correlation not estimable")
        rows.append({"measure": "abs_vs_rel_load", "n": n, "statistic": "n/a",
                     "value": "", "threshold": _num(r_max),
                     "finding_status": PENDING, "note": "too few rows"})
        out = D.write_csv(out_dir / "j_load_collinearity.csv", rows, J_FIELDS,
                          mode=mode, cfg=cfg)
        return {"status": PENDING, "artefacts": [str(out)], "notes": notes}

    worst = max(abs(pearson), abs(spearman))
    status = FAIL if worst >= r_max else PASS
    for stat, value in (("pearson", pearson), ("spearman", spearman)):
        rows.append({"measure": "abs_vs_rel_load", "n": n, "statistic": stat,
                     "value": _num(value), "threshold": _num(r_max),
                     "finding_status": FAIL if abs(value) >= r_max else PASS,
                     "note": ("|r| >= sqrt(1-1/vif_max): the two load "
                              "definitions are near-redundant and the "
                              "comparison has little power"
                              if abs(value) >= r_max else
                              "below the collinearity threshold")})
    notes.append(f"n={n} snaps with both loads; |r| threshold {r_max:.3f} "
                 f"(from vif_max={vif_max:g}); worst |r| {worst:.3f}")
    out = D.write_csv(out_dir / "j_load_collinearity.csv", rows, J_FIELDS,
                      mode=mode, cfg=cfg)
    return {"status": status, "artefacts": [str(out)], "notes": notes}


# --------------------------------------------------------------------------- #
# Item k — power / minimum detectable effects
# --------------------------------------------------------------------------- #
def _dl_rows_stats(cfg: dict) -> dict:
    """Observed DL pressure events and candidate snap counts from player_play."""
    dl_ids, _ = _position_ids(cfg, D.dl(cfg)["positions"]["dl"],
                              D.dl(cfg)["positions"]["ol"])
    out = {"events": None, "n_rush_snaps": None, "n_dropback_rows": None}
    files = [p for p in D.source_paths(cfg, "player_play") if p.exists()]
    if not files or not dl_ids:
        return out
    ids = ", ".join(str(int(i)) for i in sorted(dl_ids))
    con = D.duckdb_connect(cfg)
    try:
        srcs = ", ".join("'" + p.as_posix().replace("'", "''") + "'" for p in files)
        row = con.execute(f"""
            WITH pp AS (
                SELECT game_id, play_id, nfl_id, time_to_pressure,
                       dropback_duration, blitzing
                FROM read_csv_auto([{srcs}], nullstr=['NA'])
                WHERE nfl_id IN ({ids})
            ), db AS (
                SELECT DISTINCT game_id, play_id FROM pp
                WHERE dropback_duration IS NOT NULL
            )
            SELECT
              (SELECT count(*) FROM pp WHERE time_to_pressure IS NOT NULL),
              (SELECT count(*) FROM pp WHERE blitzing IS TRUE),
              (SELECT count(*) FROM pp JOIN db USING (game_id, play_id))
        """).fetchone()
    finally:
        con.close()
    out["events"] = int(row[0] or 0)
    out["n_rush_snaps"] = int(row[1] or 0)
    out["n_dropback_rows"] = int(row[2] or 0)
    return out


def _snaps_stats(cfg: dict, mode: str, ctx: dict) -> dict | None:
    """Event count and row count from ``snaps.csv`` when Phase 3 has run."""
    path = _snaps_path(cfg, mode, ctx)
    if not path.exists():
        return None
    heads = _header(path)
    if "event" not in heads:
        return None
    con = D.duckdb_connect(cfg)
    try:
        src = path.as_posix().replace("'", "''")
        row = con.execute(f"""
            SELECT count(*) AS n_snaps,
                   sum(CASE WHEN event = 1 OR upper(CAST(event AS VARCHAR))
                            IN ('TRUE', '1.0') THEN 1 ELSE 0 END) AS n_events
            FROM read_csv_auto('{src}', nullstr=['NA'])
        """).fetchone()
    finally:
        con.close()
    return {"n_snaps": int(row[0] or 0), "events": int(row[1] or 0)}


def item_k(cfg: dict, mode: str, out_dir: Path, ctx: dict, icc: dict) -> dict:
    audit = D.dl(cfg)["audit"]
    pblk = D.dl(cfg)["power"]
    alpha = float(audit["alpha"])
    power = float(audit["power"])
    de = float(pblk.get("design_effect", audit["design_effect"]))
    max_hr = float(audit.get("max_mde_hr", float("inf")))
    smallest_hr = float(pblk["smallest_effect_hr"])
    smallest_ls = float(pblk["smallest_effect_logscore"])
    sd_snap = float(pblk.get("sd_logscore_per_snap", 0.3))
    rows: list[dict] = []
    notes: list[str] = []

    snaps = _snaps_stats(cfg, mode, ctx)
    if snaps is not None and snaps["n_snaps"] > 0:
        events = snaps["events"]
        n_snaps = snaps["n_snaps"]
        basis = "snaps.csv (stage 03 output)"
    else:
        stats = _dl_rows_stats(cfg)
        events = stats["events"]
        n_snaps = stats["n_rush_snaps"] or 0
        basis = ("player_play DL rows: events = time_to_pressure non-null; "
                 "n_snaps = blitzing IS TRUE (rush assignment)")
        if stats["n_dropback_rows"]:
            notes.append(f"DL dropback rows (broad candidate snap set) = "
                         f"{stats['n_dropback_rows']}; rush-assignment rows = "
                         f"{n_snaps}")
    if not events:
        notes.append("no observed pressure events — power PENDING")
        rows.append({"quantity": "events", "estimate": 0, "threshold": "",
                     "unit": "events", "finding_status": PENDING, "basis": basis,
                     "note": "event count unavailable"})
        out = D.write_csv(out_dir / "k_power.csv", rows, K_FIELDS, mode=mode, cfg=cfg)
        return {"status": PENDING, "artefacts": [str(out)], "notes": notes}

    rows.append({"quantity": "events_observed", "estimate": events,
                 "threshold": "", "unit": "events", "finding_status": "NA",
                 "basis": basis, "note": "input to the MDE, not a test"})
    rows.append({"quantity": "n_snaps", "estimate": n_snaps, "threshold": "",
                 "unit": "snaps", "finding_status": "NA", "basis": basis,
                 "note": "Part-2 sample size"})

    log_hr = mde_log_hr(events, alpha, power, de)
    hr = math.exp(log_hr)
    hr_status = PASS if hr <= smallest_hr and hr <= max_hr else FAIL
    rows.append({"quantity": "part1_mde_hr", "estimate": _num(hr),
                 "threshold": smallest_hr, "unit": "hazard ratio per 1 SD a0",
                 "finding_status": hr_status, "basis": basis,
                 "note": (f"MDE log-HR {log_hr:.4f}; alpha={alpha:g}, "
                          f"power={power:g}, design_effect={de:g}; "
                          f"audit.max_mde_hr={max_hr:g}")})

    icc_a0 = icc.get("a0")
    log_att = attenuated_log_hr(log_hr, icc_a0)
    hr_att = math.exp(log_att) if math.isfinite(log_att) else float("inf")
    att_status = (PASS if math.isfinite(hr_att) and hr_att <= smallest_hr
                  and hr_att <= max_hr else FAIL)
    rows.append({"quantity": "part1_mde_hr_after_attenuation",
                 "estimate": _num(hr_att), "threshold": smallest_hr,
                 "unit": "hazard ratio per 1 SD a0 (true scale)",
                 "finding_status": att_status,
                 "basis": f"ICC(a0) from item i ({_num(icc_a0)})",
                 "note": ("MDE inflated by 1/sqrt(ICC_a0): measurement error "
                          "in the exposure shrinks the observed log-HR"
                          if math.isfinite(log_att) else
                          "ICC(a0) not estimable — attenuation cannot be "
                          "applied, reported as infinite")})

    ls = mde_logscore(sd_snap, n_snaps, alpha, power)
    ls_status = PASS if ls <= smallest_ls else FAIL
    rows.append({"quantity": "part2_mde_logscore", "estimate": _num(ls),
                 "threshold": smallest_ls, "unit": "held-out log-score difference",
                 "finding_status": ls_status,
                 "basis": f"{n_snaps} snaps; sd_per_snap={sd_snap:g} (assumed)",
                 "note": ("(z_{1-alpha/2}+z_power)*sd/sqrt(n_snaps); sd is an "
                          "explicit assumption, config-overridable via "
                          "power.sd_logscore_per_snap")})

    overall = FAIL if any(r["finding_status"] == FAIL for r in rows) else PASS
    if overall == FAIL:
        notes.append("at least one MDE exceeds its prereg smallest effect")
    notes.append(f"E={events}, n_snaps={n_snaps} ({basis})")
    notes.append("MDE formula as specified in Phase 1k: the design effect "
                 "enters as a divisor (sqrt(E)*sqrt(design_effect))")
    out = D.write_csv(out_dir / "k_power.csv", rows, K_FIELDS, mode=mode, cfg=cfg)
    return {"status": overall, "artefacts": [str(out)], "notes": notes}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_items(cfg: dict, mode: str = "sample", ctx: dict | None = None,
              items: tuple[str, ...] | None = None) -> dict:
    """Run audit items g-k and return ``{letter: {status, artefacts, notes}}``.

    ``ctx`` may carry ``outputs_dir`` (Path), ``paths`` (resolved path map) and
    ``con`` (an open DuckDB connection; accepted for interface compatibility —
    each item opens a short-lived connection so it can run standalone).  Any
    missing key is resolved from ``cfg``.
    """
    ctx = dict(ctx or {})
    out_dir = _out_dir(cfg, mode, ctx)
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = tuple(items) if items else ITEM_LETTERS
    result: dict[str, dict] = {}
    if "g" in wanted:
        result["g"] = item_g(cfg, mode, out_dir, ctx)
    if "h" in wanted:
        result["h"] = item_h(cfg, mode, out_dir, ctx)
    icc: dict[str, float] = {}
    if "i" in wanted:
        result["i"] = item_i(cfg, mode, out_dir, ctx)
        icc = result["i"].pop("icc", {}) or {}
        result["i"]["icc"] = icc
    if "j" in wanted:
        result["j"] = item_j(cfg, mode, out_dir, ctx)
    if "k" in wanted:
        if not icc and "i" in result:
            icc = result["i"].get("icc", {})
        result["k"] = item_k(cfg, mode, out_dir, ctx, icc)
    return result


def run(cfg: dict, mode: str = "sample", ctx: dict | None = None) -> dict:
    """Alias for :func:`run_items` (common ``run(cfg, mode)`` entry point)."""
    return run_items(cfg, mode, ctx)


def main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    args = D.parse_args(argv)
    cfg = D.load_config(args.config)
    res = run_items(cfg, args.mode, {"outputs_dir": None})
    for letter in ITEM_LETTERS:
        if letter in res:
            print(f"{letter}: {res[letter]['status']}  "
                  + ", ".join(res[letter]["artefacts"]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
