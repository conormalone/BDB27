"""Schema + invariant + edge-case + determinism tests for Phase 2 combine features.

Standalone (no pytest required): ``python src/tests/test_features.py``
Also pytest-compatible (functions are named ``test_*``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from common import load_config, resolve  # noqa: E402

PASS, FAIL = "PASS", "FAIL"


def _load_module():
    spec = importlib.util.spec_from_file_location("features02", REPO / "src" / "02_features.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FEAT = _load_module()


def _pq(name: str) -> str:
    return f"read_parquet('{resolve(load_config(), 'outputs', load_config()['features']['out_subdir']) / name}')"


def _out() -> Path:
    return resolve(load_config(), "outputs", load_config()["features"]["out_subdir"])


def test_schema() -> str:
    con = duckdb.connect()
    full = con.execute(f"DESCRIBE SELECT * FROM {_pq('combine_features.parquet')}").df()
    got = dict(zip(full["column_name"], full["column_type"]))
    expected = dict(FEAT.COLUMNS)
    assert list(got.keys()) == list(expected.keys()), "column order/name mismatch"
    for c, t in expected.items():
        assert got[c] == t, f"{c}: dtype {got[c]} != expected {t}"
    # study subset has the same schema
    stud = con.execute(f"DESCRIBE SELECT * FROM {_pq('combine_features_study.parquet')}").df()
    assert list(stud["column_name"]) == list(expected.keys())
    # player summary schema
    pl = con.execute(f"DESCRIBE SELECT * FROM {_pq('combine_features_player.parquet')}").df()
    assert list(pl["column_name"]) == [c for c, _ in FEAT.PLAYER_COLUMNS]
    # every diagnostics/params/vif CSV carries provenance as the FIRST column
    for name in ["feature_diagnostics.csv", "standardization_params.csv", "vif_report.csv"]:
        head = (_out() / name).read_text().splitlines()[0]
        assert head.split(",")[0] == "provenance", f"{name} missing provenance first column"
    return PASS


def test_invariants() -> str:
    con = duckdb.connect()
    F = _pq("combine_features.parquet")
    # is_imputed => no performance / clock / frame values
    bad = con.execute(f"""SELECT count(*) FROM {F} WHERE is_imputed=1 AND
        (perf_z IS NOT NULL OR peak_speed_yds IS NOT NULL OR peak_accel_yds2 IS NOT NULL
         OR t90_s IS NOT NULL OR best_speed_yds IS NOT NULL OR attempt_start_time IS NOT NULL
         OR elapsed_session_s IS NOT NULL OR n_frames IS NOT NULL)""").fetchone()[0]
    assert bad == 0, f"{bad} imputed rows carry performance/clock values"
    # imputed carry load and effort only
    assert con.execute(f"SELECT count(*) FROM {F} WHERE is_imputed=1 AND effort_cost_yd IS NULL").fetchone()[0] == 0
    # first_attempt (D19): 1 iff OBSERVED and the player's earliest observed rep of that
    # drill_name by attempt_start_time (ties -> min attempt); imputed rows = 0.
    assert con.execute(f"SELECT count(*) FROM {F} WHERE first_attempt=1 AND is_imputed=1").fetchone()[0] == 0
    n_flag = con.execute(f"SELECT count(*) FROM {F} WHERE first_attempt=1").fetchone()[0]
    n_groups = con.execute(f"SELECT count(DISTINCT (nfl_id, drill_name)) FROM {F} WHERE is_imputed=0").fetchone()[0]
    assert n_flag == n_groups, f"first_attempt flags {n_flag} != observed (player,drill_name) groups {n_groups}"
    fa_bad = con.execute(f"""
        WITH r AS (SELECT first_attempt,
                          row_number() OVER (PARTITION BY nfl_id, drill_name
                                             ORDER BY attempt_start_time, attempt) rn
                   FROM {F} WHERE is_imputed=0)
        SELECT count(*) FROM r WHERE (rn=1) <> (first_attempt=1)""").fetchone()[0]
    assert fa_bad == 0, f"{fa_bad} rows disagree with the D19 first_attempt definition"
    # no negative effort
    assert con.execute(f"SELECT count(*) FROM {F} WHERE effort_cost_yd < 0").fetchone()[0] == 0
    # observed-only load never exceeds total load
    assert con.execute(f"SELECT count(*) FROM {F} WHERE prior_load_observed_yd > prior_load_yd + 1e-3").fetchone()[0] == 0
    # duplicate-attempt rows come in duplicated tuples (2 event_ids each)
    dup = con.execute(f"""WITH d AS (SELECT nfl_id, drill_name, attempt, count(DISTINCT event_id) k
        FROM {F} WHERE flag_duplicate_attempt=1 GROUP BY 1,2,3)
        SELECT count(*), min(k), max(k) FROM d""").fetchone()
    assert dup[0] > 0 and dup[1] == 2 and dup[2] == 2, f"duplicate flag tuples not paired: {dup}"
    # standardisation: within (drill_type, position) observed groups, mean(z)~0 & sd(z)~1
    z = con.execute(f"""SELECT drill_type, combine_position, count(*) n, avg(perf_z) m, stddev_pop(perf_z) s
        FROM {F} WHERE is_imputed=0 AND perf_z IS NOT NULL GROUP BY 1,2""").df()
    assert (z["m"].abs() < 1e-3).all(), "group mean(perf_z) != 0"
    assert (z["s"].sub(1).abs() < 2e-3).all(), "group sd(perf_z) != 1"
    # prior_load_yd non-decreasing within a session in the D19 unit-rank/attempt order
    # (rank recomputed here independently from raw observed times + emitted attempt_unit)
    viol = con.execute(f"""
        WITH firsts AS (SELECT nfl_id, session_id,
                    CASE WHEN attempt_unit='drill_name' THEN drill_name ELSE drill_type END AS uk,
                    min(attempt_start_time) AS mn
                    FROM {F} WHERE is_imputed=0 GROUP BY 1,2,3),
             rk AS (SELECT nfl_id, session_id, uk,
                    row_number() OVER (PARTITION BY nfl_id, session_id ORDER BY mn, uk) r
                    FROM firsts),
             o AS (SELECT f.prior_load_yd,
                   lag(f.prior_load_yd) OVER (PARTITION BY f.nfl_id, f.session_id
                       ORDER BY rk.r, f.attempt, f.is_imputed, f.event_id) prev
                   FROM {F} f JOIN rk ON rk.nfl_id=f.nfl_id AND rk.session_id=f.session_id
                       AND rk.uk = (CASE WHEN f.attempt_unit='drill_name' THEN f.drill_name ELSE f.drill_type END))
        SELECT count(*) FROM o WHERE prev IS NOT NULL AND prior_load_yd < prev - 1e-3""").fetchone()[0]
    assert viol == 0, f"{viol} prior_load_yd monotonicity violations"
    return PASS


def test_attempt_unit_detection() -> str:
    """D19: empirical numbering-unit detection on synthetic blocks."""
    # (a) per-drill_name restart block: A and B each start at attempt 1 -> drill_name
    a = pd.DataFrame([
        {"nfl_id": 1, "drill_type": "D", "drill_name": "A", "attempt": 1},
        {"nfl_id": 1, "drill_type": "D", "drill_name": "A", "attempt": 2},
        {"nfl_id": 1, "drill_type": "D", "drill_name": "B", "attempt": 1},
        {"nfl_id": 1, "drill_type": "D", "drill_name": "B", "attempt": 2},
    ])
    r = FEAT.detect_attempt_unit(a, 2)
    assert len(r) == 1
    row = r.iloc[0]
    assert row["attempt_unit"] == "drill_name", row.to_dict()
    assert row["n_drill_names"] == 2 and row["n_drill_names_starting_at_1"] == 2

    # (b) per-drill_type block counter: only the first sub-drill starts at 1 -> drill_type
    b = pd.DataFrame([
        {"nfl_id": 2, "drill_type": "D", "drill_name": "A", "attempt": 1},
        {"nfl_id": 2, "drill_type": "D", "drill_name": "B", "attempt": 2},
        {"nfl_id": 2, "drill_type": "D", "drill_name": "B", "attempt": 3},
        {"nfl_id": 2, "drill_type": "D", "drill_name": "C", "attempt": 4},
    ])
    r = FEAT.detect_attempt_unit(b, 2)
    assert r.iloc[0]["attempt_unit"] == "drill_type", r.iloc[0].to_dict()
    assert r.iloc[0]["n_drill_names_starting_at_1"] == 1

    # (c) single-drill_name block is unit-invariant -> drill_type
    c = pd.DataFrame([
        {"nfl_id": 3, "drill_type": "D", "drill_name": "A", "attempt": 1},
        {"nfl_id": 3, "drill_type": "D", "drill_name": "A", "attempt": 2},
        {"nfl_id": 3, "drill_type": "D", "drill_name": "A", "attempt": 3},
    ])
    assert FEAT.detect_attempt_unit(c, 2).iloc[0]["attempt_unit"] == "drill_type"

    # (d) attempt numbers overlap across drill_names only via a repeated sub-drill:
    #     D1=[1], D2=[2,3] (repeated), D3=[3] (overlaps D2 at 3) -> still drill_type
    d = pd.DataFrame([
        {"nfl_id": 4, "drill_type": "D", "drill_name": "D1", "attempt": 1},
        {"nfl_id": 4, "drill_type": "D", "drill_name": "D2", "attempt": 2},
        {"nfl_id": 4, "drill_type": "D", "drill_name": "D2", "attempt": 3},
        {"nfl_id": 4, "drill_type": "D", "drill_name": "D3", "attempt": 3},
    ])
    assert FEAT.detect_attempt_unit(d, 2).iloc[0]["attempt_unit"] == "drill_type"

    # threshold is honored: with a higher min, a 2-restart block is no longer 'drill_name'
    assert FEAT.detect_attempt_unit(a, 3).iloc[0]["attempt_unit"] == "drill_type"
    return PASS


def test_2025_numbering_fix() -> str:
    """D19: the literal drill_name premise's phantom 2025 imputation is eliminated."""
    con = duckdb.connect()
    F = _pq("combine_features.parquet")
    S = _pq("combine_features_study.parquet")
    by_year = dict(con.execute(
        f"SELECT draft_year, count(*) FROM {F} WHERE is_imputed=1 GROUP BY 1").fetchall())
    assert by_year.get(2023) == 144, by_year
    assert by_year.get(2024) == 118, by_year
    assert by_year.get(2025) == 148, by_year
    assert sum(by_year.values()) == 410, by_year
    assert con.execute(f"SELECT count(*) FROM {F} WHERE is_imputed=0").fetchone()[0] == 6310
    # DB study population
    db = dict(con.execute(
        f"SELECT draft_year, count(*) FROM {S} WHERE is_imputed=1 GROUP BY 1").fetchall())
    assert db.get(2023) == 49 and db.get(2024) == 32 and db.get(2025) == 28, db
    assert sum(db.values()) == 109, db
    # nfl_id 58968 (2025 WR): block counter, zero phantom imputation
    imp, au = con.execute(
        f"SELECT count(*) FILTER (WHERE is_imputed=1), any_value(attempt_unit) FROM {F} WHERE nfl_id=58968").fetchone()
    assert imp == 0, f"58968 has {imp} imputed rows"
    assert au == "drill_type", au
    # detected block counts
    blocks = dict(con.execute(
        f"SELECT attempt_unit, count(*) FROM (SELECT DISTINCT nfl_id, drill_type, attempt_unit FROM {F}) GROUP BY 1").fetchall())
    assert blocks.get("drill_name") == 337 and blocks.get("drill_type") == 921, blocks
    return PASS


def test_study_subset() -> str:
    con = duckdb.connect()
    n_all = con.execute(f"SELECT count(*) FROM {_pq('combine_features.parquet')} WHERE in_study_population").fetchone()[0]
    n_study = con.execute(f"SELECT count(*) FROM {_pq('combine_features_study.parquet')}").fetchone()[0]
    assert n_all == n_study, f"study filter {n_all} != study parquet rows {n_study}"
    assert con.execute(f"SELECT count(*) FROM {_pq('combine_features_study.parquet')} WHERE NOT in_study_population").fetchone()[0] == 0
    # provenance column present + non-null
    assert con.execute(f"SELECT count(*) FROM {_pq('combine_features.parquet')} WHERE provenance IS NULL").fetchone()[0] == 0
    return PASS


def test_player_summary() -> str:
    con = duckdb.connect()
    F = _pq("combine_features.parquet")
    P = _pq("combine_features_player.parquet")
    n_players = con.execute(f"SELECT count(DISTINCT nfl_id) FROM {F}").fetchone()[0]
    assert con.execute(f"SELECT count(*) FROM {P}").fetchone()[0] == n_players
    # per-player totals reconcile with the attempt table
    chk = con.execute(f"""
        WITH a AS (SELECT nfl_id, count(*) tot, sum((is_imputed=0)::INT) obs, sum((is_imputed=1)::INT) imp
                   FROM {F} GROUP BY 1)
        SELECT count(*) FROM a JOIN {P} p USING(nfl_id)
        WHERE a.tot <> p.n_attempts_total OR a.obs <> p.n_attempts_observed OR a.imp <> p.n_attempts_imputed""").fetchone()[0]
    assert chk == 0, f"{chk} player-summary rows disagree with the attempt table"
    return PASS


def test_edge_cases() -> str:
    p = {"expected_dt_s": 0.1, "gap_detect_factor": 1.5, "interp_max_gap_s": 0.5,
         "distance_gap_max_s": 0.5, "peak_speed_sanity_yds": 12.0}
    # (a) single-frame attempt -> all kinematics NaN, no crash
    k = FEAT.kinematics_for_attempt(np.array([0.0]), np.array([1.0]), np.array([1.0]), p)
    assert k["n_frames"] == 1 and np.isnan(k["peak_speed_yds"]) and np.isnan(k["effort_cost_yd"])
    # (b) clean 40-yd-like straight run at 10 yd/s for 1 s -> peak ~10, effort ~10
    t = np.arange(0, 1.01, 0.1); x = 10.0 * t; y = np.zeros_like(t)
    k = FEAT.kinematics_for_attempt(t, x, y, p)
    assert abs(k["peak_speed_yds"] - 10.0) < 1e-6, k["peak_speed_yds"]
    assert abs(k["effort_cost_yd"] - 10.0) < 1e-6, k["effort_cost_yd"]
    # (c) missing frame >= 0.5 s -> large gap, segment excluded
    t = np.array([0.0, 0.1, 0.7, 0.8]); x = np.array([0.0, 1.0, 7.0, 8.0]); y = np.zeros(4)
    k = FEAT.kinematics_for_attempt(t, x, y, p)
    assert k["flag_large_gap"] == 1 and k["n_gaps"] >= 1 and abs(k["max_gap_s"] - 0.6) < 1e-9
    # the excluded segment must not contribute distance: ~ the two 0.1 s steps only
    assert abs(k["effort_cost_yd"] - 2.0) < 1e-6, k["effort_cost_yd"]
    # (d) short gap (<0.5 s) is interpolated -> no large-gap flag
    t = np.array([0.0, 0.1, 0.4, 0.5, 0.6]); x = np.array([0.0, 0.5, 2.0, 2.5, 3.5]); y = np.zeros(5)
    k = FEAT.kinematics_for_attempt(t, x, y, p)
    assert k["flag_large_gap"] == 0 and k["n_gaps"] >= 1 and abs(k["effort_cost_yd"] - 3.5) < 1e-6
    # (e) z-score group too small / degenerate -> NaN + not ok
    z, ok = FEAT.zscore_group([5.0, 5.0, 5.0], 3); assert not ok and np.isnan(z).all()
    z, ok = FEAT.zscore_group([1.0, 2.0], 3); assert not ok
    z, ok = FEAT.zscore_group([1.0, 2.0, 3.0], 3); assert ok and abs(z.mean()) < 1e-9
    # (f) numbering gaps: {1,3}->{2}; {2,3}->{1} (min>1); {2,3} with max 5 -> {1,4,5}
    assert FEAT.missing_attempt_numbers([1, 3], 3) == [2]
    assert FEAT.missing_attempt_numbers([2, 3], 3) == [1]
    assert FEAT.missing_attempt_numbers([2, 3], 5) == [1, 4, 5]
    # (g) constant performance group handled by the standardiser, not a crash
    z, ok = FEAT.zscore_group([2.0, 2.0, 2.0, 2.0], 2); assert not ok and np.isnan(z).all()
    return PASS


def _hash_outputs() -> dict[str, str]:
    out = _out()
    h = {}
    for f in sorted(out.glob("*")):
        if f.is_file():
            h[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return h


def test_determinism() -> str:
    run = [sys.executable, str(REPO / "src" / "02_features.py"), "--mode", "sample", "--force"]
    subprocess.run(run, cwd=REPO, check=True, capture_output=True)
    h1 = _hash_outputs()
    subprocess.run(run, cwd=REPO, check=True, capture_output=True)
    h2 = _hash_outputs()
    assert h1 == h2, "feature outputs are not deterministic across runs"
    return PASS


def main() -> int:
    tests = [test_schema, test_attempt_unit_detection, test_invariants, test_study_subset,
             test_player_summary, test_edge_cases, test_2025_numbering_fix, test_determinism]
    rc = 0
    for t in tests:
        try:
            print(f"[{t()}] {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            rc = 1
            print(f"[{FAIL}] {t.__name__}: {type(exc).__name__}: {exc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
