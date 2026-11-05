#!/usr/bin/env python3
"""Behaviour tests for the game-side **frame-level speed despike** (`game.speed_cap_yds`, D35).

**Revision 3 of the game-side speed QC** (Conor 2026-10-15): the *final* mechanism is a frame-level
**despike**, not a clamp (revision 1, `LEAST(spd, cap)`) and not a row drop (revision 2, discard the
whole effort). `peak_speed` is a ``max()`` over per-frame speed (``step distance / dt``, recomputed
from ``x,y``); a single noisy/duplicated frame can define a physically-impossible peak.

The despike is a **greedy forward scan** within each effort (one player on one play):

* the first frame is always retained and becomes the "last good frame";
* each later frame's sample is ``step distance / dt`` from the last *retained* frame;
* a sample ``<= cap`` (or ``dt <= 0``, which stays NULL and is not a sample) **retains** the frame
  and makes it the new last-good frame; a sample ``> cap`` **discards** only that sample — the next
  frame is re-measured from the same last-good frame (the spike is skipped, its valid neighbour is
  re-measured from the last good frame and is **not** dropped);
* the effort is always **kept**; ``peak_speed``/``peak_accel`` are recomputed from the retained
  frames only, so no emitted peak can exceed the cap. A degenerate effort (no post-first frame can
  be retained) is kept with ``peak 0.0``.

The cap (DEFAULT 12.5 yd/s = 25.6 mph, above every real in-game route speed; NGS in-game tops ~21-22
mph) is **game-side only** (the Combine side is untouched) and **config-driven** — no fixture
override, no magic numbers.

Runnable **without pytest** (`python src/tests/test_speed_despike.py`) and collected by pytest
(functions named `test_*`). Failures are loud (AssertionError). Covers:
  * a planted ~50 yd/s single-frame spike is **discarded** and the emitted peak is the genuine
    sub-cap value (never 50, never the cap 12.5); the effort is still emitted (row count unchanged);
  * **neighbour recovery** — A -> spike S -> genuine fast B: S is discarded and B is retained with
    its speed re-measured from A (B is not dropped);
  * a clean sub-cap effort is byte-identical to the pre-change (unspliced) behaviour;
  * no emitted ``efforts.csv`` peak exceeds the cap (synthetic binding + null + full-mode e2e);
  * the despike is config-driven (a lower cap discards more; a cap above the spike leaves the raw peak);
  * a ``dt=0`` frame stays NULL (never a sample, never a fabricated value);
  * a degenerate effort (<2 retained frames) is KEPT with peak 0.0;
  * `01_audit._game_effort_peaks` obeys the same despike (peak <= cap; lower cap => lower peak);
  * determinism (same seed => identical outputs);
  * fixture non-collision: the binding fixture's planted speeds (max ~10.97) stay below the cap; the
    null fixture's planted exceedance verdict is unchanged (still ~1.0).
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import yaml

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))
import common  # noqa: E402
import game_features as gf  # noqa: E402

THR = SRC / "00_thresholds.py"
GAME = SRC / "03_game_demand.py"
GT_HEADER = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis", "o", "dir", "event"]
CAP = float(common.load_config()["game"]["speed_cap_yds"])
BASE = datetime(2025, 9, 7, 17, 0, 0)

# effort keys in the tiny fixture (game_id, play_id, nfl_id)
E_SPIKE = (1, 1, 101)        # steady ~8 yd/s + one planted ~50 yd/s spike
E_RECOVER = (1, 2, 102)      # A -> spike S -> genuine fast B (neighbour recovery)
E_CLEAN = (1, 3, 103)        # clean ~8 yd/s effort (below cap)
E_DT0 = (1, 4, 104)          # duplicated (dt=0) frame
E_DEGEN = (1, 5, 105)        # impossible after frame 1 (<2 retained)
E_SINGLE = (1, 6, 106)       # a single frame (no incoming sample)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(out: Path, notes: Path | None = None, cap: float | None = None) -> Path:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(out)
    if notes is not None:
        cfg["paths"]["notes"] = str(notes)
    if cap is not None:
        cfg["game"]["speed_cap_yds"] = cap
    p = out.parent / "config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _run(stage: Path, args: list[str], cfg: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(stage)] + args + ["--config", str(cfg)],
                          cwd=str(REPO), capture_output=True, text=True)


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _close(a, b, tol: float = 1e-4) -> bool:
    """Approximate equality: game x/y are CAST to REAL (float32) before the step, so a clean
    sub-cap speed carries ~1e-7 relative rounding; the cap boundary itself is exact."""
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def _con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("PRAGMA threads=1")
    return c


def _ts(seconds: float) -> str:
    return (BASE + timedelta(seconds=float(seconds))).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _frame(gid: int, pid: int, nid: int, t: str, x: float, y: float) -> list:
    return [gid, pid, nid, t, repr(float(x)), repr(float(y)), "0.0", "0.0", "0.0", "0.0", "0.0", ""]


def _tiny_csv(path: Path) -> Path:
    """A tiny game_tracking CSV with six efforts exercising every despike branch:

    * ``E_SPIKE``   (play 1): steady 0.8 yd / 0.1 s (8 yd/s) with ONE planted 5 yd / 0.1 s = 50 yd/s
      spike frame => the spike sample is discarded, the genuine peak (~8) is emitted and the effort
      is retained;
    * ``E_RECOVER`` (play 2): A -> spike S (50 yd/s) -> genuine fast B => S discarded, B retained and
      re-measured from A (10 yd/s), never dropped;
    * ``E_CLEAN``   (play 3): a clean 8 yd/s effort (true peak < cap) => byte-identical to pre-change;
    * ``E_DT0``     (play 4): a duplicated (dt=0) frame => that sample stays NULL, never fabricated;
    * ``E_DEGEN``   (play 5): every post-first frame impossible => <2 retained, kept with peak 0.0;
    * ``E_SINGLE``  (play 6): a single frame (no incoming sample) => kept with peak 0.0.
    """
    rows = [GT_HEADER]
    # E_SPIKE: steps 0.8, 0.8, 0.8, 5.0, 0.8 -> speeds 8, 8, 8, 50, 8
    for i, x in enumerate([0.0, 0.8, 1.6, 2.4, 7.4, 8.2]):
        rows.append(_frame(1, 1, 101, _ts(0.1 * i), x, 10.0))
    # E_RECOVER: A(0) t0, S(5) t0.1 (50 spike), B(2.0) t0.2 (10 from A), C(3.0) t0.3 (10)
    for t, x in [(0.0, 0.0), (0.1, 5.0), (0.2, 2.0), (0.3, 3.0)]:
        rows.append(_frame(1, 2, 102, _ts(t), x, 40.0))
    # E_CLEAN: 0.8 yd per 0.1 s (8 yd/s), distinct timestamps
    for i, x in enumerate([0.0, 0.8, 1.6, 2.4, 3.2, 4.0]):
        rows.append(_frame(1, 3, 103, _ts(0.1 * i), x, 20.0))
    # E_DT0: duplicated (dt=0) frame at t=0.2
    for t, x in [(0.0, 0.0), (0.1, 1.0), (0.2, 2.0), (0.2, 2.0), (0.3, 3.0)]:
        rows.append(_frame(1, 4, 104, _ts(t), x, 30.0))
    # E_DEGEN: 2 frames, the second impossible (50 yd / 0.1 s = 500 yd/s)
    for t, x in [(0.0, 0.0), (0.1, 50.0)]:
        rows.append(_frame(1, 5, 105, _ts(t), x, 50.0))
    # E_SINGLE: one frame only
    rows.append(_frame(1, 6, 106, _ts(0.0), 0.0, 60.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return path


def _peaks(csv_path: Path, cap: float) -> dict[tuple[int, int, int], tuple[int, float | None, float | None]]:
    """Run the shared kinematics SQL on a CSV; return {(game,play,nfl): (n_frames, peak_speed, peak_accel)}."""
    con = _con()
    try:
        sql = gf.effort_kinematics_sql(f"read_csv_auto('{csv_path}')", cap)
        out = {}
        for gid, pid, nid, nf, spd, acc in con.execute(sql).fetchall():
            out[(int(gid), int(pid), int(nid))] = (
                int(nf),
                (float(spd) if spd is not None else None),
                (float(acc) if acc is not None else None),
            )
        return out
    finally:
        con.close()


def _scan_frames(csv_path: Path, cap: float, key: tuple[int, int, int]) -> dict[int, tuple[bool, float | None]]:
    """Per-frame despike state for one effort: {rn: (kept, spd_sample)} from the shared scan CTE."""
    gid, pid, nid = key
    con = _con()
    try:
        sql = gf._despike_scan_sql(f"read_csv_auto('{csv_path}')", cap) + (
            f" SELECT rn, kept, spd FROM scan WHERE game_id={gid} AND play_id={pid} AND nfl_id={nid}"
            " ORDER BY rn")
        return {int(rn): (bool(k), (float(s) if s is not None else None))
                for rn, k, s in con.execute(sql).fetchall()}
    finally:
        con.close()


def _raw_peaks(csv_path: Path) -> dict[tuple[int, int, int], tuple[int, float | None, float | None]]:
    """The PRE-change (revision-0) per-effort peak: max over *every* raw frame step, no QC."""
    con = _con()
    try:
        sql = f"""
            WITH f AS (SELECT game_id, play_id, nfl_id, epoch_ms(time) t,
                              CAST(x AS REAL) x, CAST(y AS REAL) y,
                              lag(x) OVER w lx, lag(y) OVER w ly, lag(epoch_ms(time)) OVER w lt
                       FROM read_csv_auto('{csv_path}')
                       WINDOW w AS (PARTITION BY game_id, play_id, nfl_id ORDER BY time)),
                 v AS (SELECT game_id, play_id, nfl_id, t,
                              sqrt((x-lx)*(x-lx) + (y-ly)*(y-ly)) / NULLIF((t-lt)/1000.0, 0) spd
                       FROM f WHERE lx IS NOT NULL),
                 va AS (SELECT game_id, play_id, nfl_id, t, spd,
                              (spd - lag(spd) OVER w2) / NULLIF((t - lag(t) OVER w2)/1000.0, 0) acc
                        FROM v WINDOW w2 AS (PARTITION BY game_id, play_id, nfl_id ORDER BY t))
            SELECT game_id, play_id, nfl_id, count(*) n_frames, max(spd) peak_speed, max(acc) peak_accel
            FROM va GROUP BY 1, 2, 3"""
        out = {}
        for gid, pid, nid, nf, spd, acc in con.execute(sql).fetchall():
            out[(int(gid), int(pid), int(nid))] = (
                int(nf), (float(spd) if spd is not None else None),
                (float(acc) if acc is not None else None))
        return out
    finally:
        con.close()


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("bdb27_01_audit", SRC / "01_audit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _synthetic(regime: str, cap: float | None = None, tmp: Path | None = None) -> Path:
    """Run 00_thresholds + 03_game_demand on a synthetic regime; return the game/<synthetic>/<regime> dir."""
    tmp = tmp or Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out", notes=tmp / "notes", cap=cap)
    r0 = _run(THR, ["--synthetic", "--synthetic-regime", regime, "--force"], cfg)
    assert r0.returncode == 0, r0.stderr[-2000:]
    r1 = _run(GAME, ["--synthetic", "--synthetic-regime", regime, "--force"], cfg)
    assert r1.returncode == 0, r1.stderr[-2000:]
    return tmp / "out" / "game" / "synthetic" / regime


# --------------------------------------------------------------------------- #
# unit: the despike discards the spike and emits the genuine peak
# --------------------------------------------------------------------------- #
def test_despike_spike_discarded_peak_genuine(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")
    pk = _peaks(csv_path, CAP)

    # the effort is RETAINED (row count unchanged: one emitted row per effort under both cap regimes)
    assert set(pk) == {E_SPIKE, E_RECOVER, E_CLEAN, E_DT0, E_DEGEN, E_SINGLE}, sorted(pk)
    assert len(pk) == len(_peaks(csv_path, 1.0e9)), "despike changed the emitted effort count"

    # E_SPIKE: the 50 yd/s single-frame spike is DISCARDED; the genuine ~8 yd/s peak is emitted
    nf, spd, _ = pk[E_SPIKE]
    assert nf >= 1, nf
    assert _close(spd, 8.0), f"spike effort peak {spd} != genuine 8"
    assert not _close(spd, 50.0) and not _close(spd, CAP), f"spike survived/ clamped: {spd}"

    # per-frame: the spike frame (rn 5) is not retained; its neighbours are
    frames = _scan_frames(csv_path, CAP, E_SPIKE)
    assert frames[5][0] is False and _close(frames[5][1], 50.0), frames
    assert frames[2][0] is True and _close(frames[2][1], 8.0), frames


# --------------------------------------------------------------------------- #
# unit: neighbour recovery (S discarded, B retained and re-measured from A)
# --------------------------------------------------------------------------- #
def test_despike_neighbour_recovery(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")
    frames = _scan_frames(csv_path, CAP, E_RECOVER)

    # rn1 = A: the anchor, retained, no incoming sample
    assert frames[1][0] is True and frames[1][1] is None, frames
    # rn2 = spike S: discarded (> cap); its sample WAS 50 yd/s
    assert frames[2][0] is False and _close(frames[2][1], 50.0), frames
    # rn3 = genuine fast B: RETAINED, re-measured from A (2 yd over 0.2 s = 10 yd/s), NOT dropped
    assert frames[3][0] is True and _close(frames[3][1], 10.0), frames
    # rn4 = C: retained too
    assert frames[4][0] is True and _close(frames[4][1], 10.0), frames

    # the effort's emitted peak is the recovered genuine value
    nf, spd, _ = _peaks(csv_path, CAP)[E_RECOVER]
    assert _close(spd, 10.0), spd


# --------------------------------------------------------------------------- #
# unit: a clean sub-cap effort is byte-identical to the pre-change behaviour
# --------------------------------------------------------------------------- #
def test_despike_clean_effort_untouched(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")
    pk = _peaks(csv_path, CAP)
    raw = _raw_peaks(csv_path)
    big = _peaks(csv_path, 1.0e9)  # a non-binding cap => pure greedy retain-all

    nf, spd, acc = pk[E_CLEAN]
    rnf, rspd, racc = raw[E_CLEAN]
    assert nf == rnf, (nf, rnf)
    assert _close(spd, rspd) and _close(spd, 8.0), (spd, rspd)
    assert _close(acc, racc), (acc, racc)
    # and identical under an effectively-infinite cap (nothing to despike)
    assert pk[E_CLEAN] == big[E_CLEAN], (pk[E_CLEAN], big[E_CLEAN])


# --------------------------------------------------------------------------- #
# unit: no emitted peak exceeds the cap (tiny fixture)
# --------------------------------------------------------------------------- #
def test_despike_no_peak_above_cap(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")
    for key, (nf, spd, acc) in _peaks(csv_path, CAP).items():
        if spd is not None:
            assert spd <= CAP + 1e-9, (key, spd)


# --------------------------------------------------------------------------- #
# unit: a dt=0 frame stays NULL (never a sample, never fabricated)
# --------------------------------------------------------------------------- #
def test_despike_dt0_frame_stays_null(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")
    frames = _scan_frames(csv_path, CAP, E_DT0)
    # rn4 is the duplicated (dt=0) frame: retained as a frame, but its sample is NULL
    assert frames[4][0] is True and frames[4][1] is None, frames
    # the effort's peak is the genuine 10 yd/s — not a fabricated value from the dt=0 frame
    nf, spd, _ = _peaks(csv_path, CAP)[E_DT0]
    assert nf == 4 and _close(spd, 10.0), (nf, spd)


# --------------------------------------------------------------------------- #
# unit: a degenerate effort (<2 retained frames) is KEPT with peak 0.0
# --------------------------------------------------------------------------- #
def test_despike_degenerate_kept_zero(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")
    pk = _peaks(csv_path, CAP)
    for key in (E_DEGEN, E_SINGLE):
        assert key in pk, f"{key} was dropped by the despike"
        nf, spd, acc = pk[key]
        assert _close(spd, 0.0) and _close(acc, 0.0), (key, pk[key])


# --------------------------------------------------------------------------- #
# unit: config-driven (a lower cap discards more; a cap above the spike is inert)
# --------------------------------------------------------------------------- #
def test_despike_is_config_driven(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")

    hi = _peaks(csv_path, 60.0)      # cap above the 50 yd/s spike => the raw peak survives
    mid = _peaks(csv_path, CAP)      # default cap discards the spike => genuine 8
    lo = _peaks(csv_path, 4.0)       # a lower cap discards even the genuine 8 yd/s frames => 0

    assert _close(hi[E_SPIKE][1], 50.0), hi[E_SPIKE]
    assert _close(mid[E_SPIKE][1], 8.0), mid[E_SPIKE]
    assert _close(lo[E_SPIKE][1], 0.0), lo[E_SPIKE]
    assert lo[E_SPIKE][1] < mid[E_SPIKE][1] < hi[E_SPIKE][1], (
        lo[E_SPIKE][1], mid[E_SPIKE][1], hi[E_SPIKE][1])


# --------------------------------------------------------------------------- #
# audit despike: 01_audit._game_effort_peaks
# --------------------------------------------------------------------------- #
def test_audit_game_effort_peaks_despike(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    csv_path = _tiny_csv(tmp / "gt.csv")
    pq = tmp / "gt.parquet"
    con = _con()
    common.to_parquet(con, csv_path, pq, force=True)

    audit = _load_audit_module()
    cfg = common.load_config()
    ctx = dict(cfg=cfg, con=con, tables={"game_tracking": pq})
    by = {}
    for gid, pid, nid, peak in audit._game_effort_peaks(ctx):
        by[(int(gid), int(pid), int(nid))] = (float(peak) if peak is not None else None)
    # the audit spike effort reports the genuine 8 (not 12.5, not 50); neighbour recovery holds
    assert _close(by[E_SPIKE], 8.0), by[E_SPIKE]
    assert _close(by[E_RECOVER], 10.0), by[E_RECOVER]
    assert _close(by[E_CLEAN], 8.0), by[E_CLEAN]
    # the degenerate effort is KEPT with 0.0 (never dropped, never NULL)
    assert _close(by[E_DEGEN], 0.0), by[E_DEGEN]

    # a lower cap also drives the audit despike (config-driven, no hard-coding)
    cfg_lo = copy.deepcopy(cfg)
    cfg_lo["game"]["speed_cap_yds"] = 4.0
    ctx_lo = dict(cfg=cfg_lo, con=con, tables={"game_tracking": pq})
    lo = {(int(g), int(p), int(n)): float(peak) for g, p, n, peak in audit._game_effort_peaks(ctx_lo)}
    assert _close(lo[E_SPIKE], 0.0) and lo[E_SPIKE] < by[E_SPIKE], lo[E_SPIKE]
    con.close()


# --------------------------------------------------------------------------- #
# emitted efforts.csv <= cap (synthetic binding + null)
# --------------------------------------------------------------------------- #
def test_synthetic_efforts_within_cap_and_verdict_unchanged(tmp: Path | None = None):
    for regime, lo, hi in (("binding", 0.03, 0.25), ("null", 0.6, 1.0)):
        d = _synthetic(regime)
        eff = _rows(d / "efforts.csv")
        assert eff, f"{regime}: no efforts"
        for r in eff:
            if r["peak_speed"]:
                assert float(r["peak_speed"]) <= CAP + 1e-9, (
                    f"{regime}: peak_speed {r['peak_speed']} exceeds cap {CAP}")
        rate = json.loads((d / "SUMMARY.json").read_text())["overall_exceedance_rate"]
        assert lo <= rate <= hi, f"{regime}: verdict moved to {rate} (expected [{lo},{hi}])"


# --------------------------------------------------------------------------- #
# fixture non-collision (binding planted speeds stay below the cap)
# --------------------------------------------------------------------------- #
def test_fixture_binding_unchanged(tmp: Path | None = None):
    """The despike is config-driven; the binding fixture needs no fixture-specific override.

    Running the binding fixture with the default cap and with an effectively-infinite cap must
    produce identical ``efforts.csv`` — i.e. the planted speeds (max ~10.97 yd/s) never touch the cap.
    """
    a = _synthetic("binding", cap=CAP)
    b = _synthetic("binding", cap=1.0e9)
    assert (a / "efforts.csv").read_text() == (b / "efforts.csv").read_text(), (
        "binding fixture is affected by the despike (planted speeds are not all below the cap)")

    peaks = [float(r["peak_speed"]) for r in _rows(a / "efforts.csv") if r["peak_speed"]]
    assert peaks and max(peaks) < CAP, f"binding planted max {max(peaks)} not below cap {CAP}"


def test_fixture_null_verdict_unchanged(tmp: Path | None = None):
    """The null fixture's planted exceedance verdict is unchanged by the despike (still ~1.0)."""
    d = _synthetic("null")
    rate = json.loads((d / "SUMMARY.json").read_text())["overall_exceedance_rate"]
    assert 0.6 <= rate <= 1.0, rate
    eff = _rows(d / "efforts.csv")
    tracked = [r for r in eff if r["peak_speed"] and r["ceiling_speed_yds"]]
    n_exc = sum(1 for r in tracked if r["is_exceed"] == "True")
    assert tracked and n_exc / len(tracked) >= 0.6, (n_exc, len(tracked))
    assert abs(n_exc / len(tracked) - rate) < 1e-3, (n_exc, len(tracked), rate)


# --------------------------------------------------------------------------- #
# full-mode e2e harness output respects the cap
# --------------------------------------------------------------------------- #
def test_full_mode_e2e_efforts_within_cap():
    spec = importlib.util.spec_from_file_location("bdb27_e2e_for_cap", SRC / "tests" / "test_full_mode_e2e.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    c = mod._pipeline()
    failed = {s: p for s, p in c["procs"].items() if p.returncode != 0}
    assert not failed, "; ".join(f"{s} exit {p.returncode}: {p.stderr[-1500:]}" for s, p in failed.items())
    eff = _rows(c["outputs"] / "game" / "efforts.csv")
    assert eff, "full-mode e2e produced no efforts"
    for r in eff:
        if r["peak_speed"]:
            assert float(r["peak_speed"]) <= CAP + 1e-9, (
                f"full-mode e2e peak_speed {r['peak_speed']} exceeds cap {CAP}")


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_determinism(tmp: Path | None = None):
    a = _synthetic("binding", tmp=Path(tempfile.mkdtemp()))
    b = _synthetic("binding", tmp=Path(tempfile.mkdtemp()))
    for name in ("efforts.csv", "top_k_efforts.csv"):
        assert (a / name).read_text() == (b / name).read_text(), f"non-deterministic: {name}"
    # the tiny-fixture despike is deterministic too
    t = Path(tempfile.mkdtemp())
    c1 = _tiny_csv(t / "a.csv")
    c2 = t / "b.csv"
    c2.write_bytes(c1.read_bytes())
    assert _peaks(c1, CAP) == _peaks(c2, CAP)


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_despike_spike_discarded_peak_genuine, test_despike_neighbour_recovery,
             test_despike_clean_effort_untouched, test_despike_no_peak_above_cap,
             test_despike_dt0_frame_stays_null, test_despike_degenerate_kept_zero,
             test_despike_is_config_driven, test_audit_game_effort_peaks_despike,
             test_synthetic_efforts_within_cap_and_verdict_unchanged, test_fixture_binding_unchanged,
             test_fixture_null_verdict_unchanged, test_full_mode_e2e_efforts_within_cap,
             test_determinism]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
