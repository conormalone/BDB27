"""Schema + synthetic-recovery + fallback + edge-case + stop-rule + determinism tests
for the Phase-3 combine fatigue model (``src/03_combine_model.py``).

Standalone (no pytest required): ``python src/tests/test_combine_model.py``
Also pytest-compatible (functions are named ``test_*``).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from common import load_config, resolve  # noqa: E402

PASS, FAIL = "PASS", "FAIL"


def _load_module():
    spec = importlib.util.spec_from_file_location("combine03", REPO / "src" / "03_combine_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COMBINE = _load_module()
CFG = load_config()


def _out() -> Path:
    return resolve(CFG, "outputs", CFG["model"]["out_subdir"])


def _pq(name: str) -> str:
    return f"read_parquet('{_out() / name}')"


def _real_study() -> pd.DataFrame:
    return COMBINE.load_study(resolve(CFG, "outputs", "features") / "combine_features_study.parquet")


def _small_cfg(perm_n: int = 40) -> dict:
    c = copy.deepcopy(CFG)
    c["model"] = dict(c["model"])
    c["model"]["perm_n"] = int(perm_n)
    return c


def _crafted_frame(rows: list[dict]) -> pd.DataFrame:
    """Minimal study-schema frame from row dicts (Phase-3 columns only)."""
    base = {
        "provenance": "UNVALIDATED SAMPLE OUTPUT", "is_imputed": 0, "drill_name": "d",
        "attempt_unit": "drill_type", "combine_position": "DB", "draft_year": 2025,
        "effort_cost_yd": 8.0, "rest_s": 30.0, "prior_load_observed_yd": None,
        "prior_load_efforts": 0, "prior_load_observed_efforts": 0, "elapsed_session_s": 0.0,
    }
    out = []
    for r in rows:
        d = dict(base)
        d.update(r)
        if d.get("prior_load_observed_yd") is None:
            d["prior_load_observed_yd"] = d["prior_load_yd"]
        out.append(d)
    return pd.DataFrame(out)


def _short_series_frame(n_players: int = 60, max_att: int = 2, signal: bool = False,
                        seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_players):
        n = int(rng.integers(2, max_att + 1))
        for a in range(n):
            load = float(rng.uniform(0.0, 300.0))
            perf = (-0.005 * load if signal else 0.0) + float(rng.normal(0, 1.0))
            rows.append({
                "nfl_id": 900000 + p, "drill_type": "D", "perf_z": perf,
                "prior_load_yd": load, "first_attempt": 1 if a == 0 else 0,
                "attempt": a + 1, "event_id": f"{p}-{a}", "session_id": f"{p}:1",
                "attempt_start_time": pd.Timestamp("2025-01-01") + pd.Timedelta(seconds=60 * a),
            })
    return _crafted_frame(rows)


# --------------------------------------------------------------------------- #
# 1. Schema / dtype / provenance
# --------------------------------------------------------------------------- #
def test_schema() -> str:
    con = duckdb.connect()
    got = dict(zip(*con.execute(f"DESCRIBE SELECT * FROM {_pq('combine_player_slopes.parquet')}")
                     .df()[["column_name", "column_type"]].to_numpy().T))
    expected = dict(COMBINE.PLAYER_SLOPE_COLUMNS)
    assert list(got.keys()) == list(expected.keys()), "player-slope column order/name mismatch"
    for c, t in expected.items():
        assert got[c] == t, f"combine_player_slopes.{c}: dtype {got[c]} != {t}"
    got_re = dict(zip(*con.execute(f"DESCRIBE SELECT * FROM {_pq('combine_model_random_effects.parquet')}")
                       .df()[["column_name", "column_type"]].to_numpy().T))
    assert list(got_re.keys()) == [c for c, _ in COMBINE.RANDOM_EFFECT_COLUMNS], "RE column mismatch"
    # every CSV carries provenance FIRST
    for name in ["combine_player_slopes.csv", "combine_model_fixed_effects.csv",
                 "combine_model_baseline.csv", "combine_model_permutation.csv",
                 "combine_model_xdrill.csv", "combine_model_diagnostics.csv"]:
        head = (_out() / name).read_text().splitlines()[0]
        assert head.split(",")[0] == "provenance", f"{name} missing provenance first column"
    # parquet provenance present + non-null
    for name in ["combine_player_slopes.parquet", "combine_model_random_effects.parquet"]:
        n_null = con.execute(f"SELECT count(*) FROM {_pq(name)} WHERE provenance IS NULL").fetchone()[0]
        assert n_null == 0, f"{name} has NULL provenance"
    # summary json keys
    summ = json.loads((_out() / "combine_model_summary.json").read_text())
    for k in ["estimator_chosen", "reliability", "stop_rule_triggered", "load2_included",
              "population_load_coef", "permutation", "n_players", "n_obs", "seed"]:
        assert k in summ, f"summary.json missing {k}"
    return PASS


# --------------------------------------------------------------------------- #
# 2. Real outputs consistency
# --------------------------------------------------------------------------- #
def test_real_outputs() -> str:
    con = duckdb.connect()
    ps = con.execute(f"SELECT * FROM {_pq('combine_player_slopes.parquet')}").df()
    study = _real_study()
    model = study[(study["is_imputed"] == 0) & study["perf_z"].notna()]
    assert len(ps) == study["nfl_id"].nunique(), "one row per study player expected"
    assert int(ps["meets_link_min_attempts"].sum()) == int(
        (model.groupby("nfl_id").size() >= CFG["model"]["link_min_attempts"]).sum())
    assert set(ps["method"]) <= set(COMBINE.ESTIMATORS)
    # CI ordering + reliability bounds
    fin = ps.dropna(subset=["ci_lo", "ci_hi"])
    assert (fin["ci_lo"] <= fin["ci_hi"]).all(), "ci_lo > ci_hi"
    assert (ps["reliability"] >= 0).all() and (ps["reliability"] <= 1).all(), "reliability out of [0,1]"
    # stop rule consistent with reliability vs threshold
    summ = json.loads((_out() / "combine_model_summary.json").read_text())
    thr = CFG["model"]["reliability_stop_threshold"]
    assert summ["stop_rule_triggered"] == (summ["reliability"] < thr), "stop rule inconsistent"
    assert summ["n_obs"] == int(len(model)), "summary n_obs mismatch"
    assert summ["n_players"] == int(model["nfl_id"].nunique()), "summary n_players mismatch"
    # load2 criterion evidence recorded
    diag = pd.read_csv(_out() / "combine_model_diagnostics.csv")
    dv = dict(zip(diag["metric"], diag["value"].astype(str)))
    assert dv["load2_included"].lower() in {"true", "false"}
    assert "load_n_unique" in dv and "load_iqr_yd" in dv
    return PASS


# --------------------------------------------------------------------------- #
# 3. Synthetic recovery (real timings + deleted attempts + imputed load)
# --------------------------------------------------------------------------- #
def test_synthetic_recovery() -> str:
    real = _real_study()
    rng = np.random.default_rng(int(CFG["model"]["synth_seed"]))
    syn = COMBINE.simulate_synthetic(real, CFG, rng)
    with tempfile.TemporaryDirectory() as td:
        COMBINE.run_pipeline(syn, _small_cfg(perm_n=40), "sample",
                             "UNVALIDATED SAMPLE OUTPUT", Path(td))
        slopes = pd.read_parquet(Path(td) / "combine_player_slopes.parquet")
    truth = syn[["nfl_id", "true_slope"]].drop_duplicates()
    r = COMBINE.synthetic_recovery_correlation(slopes, truth)
    tol = float(CFG["model"]["synth_slope_tol"])
    assert np.isfinite(r) and r >= 1.0 - tol, f"recovered-vs-planted slope correlation {r} < {1 - tol}"
    return PASS


# --------------------------------------------------------------------------- #
# 4. Fallback path (primary random-slopes LMM cannot be fit)
# --------------------------------------------------------------------------- #
def test_fallback_path() -> str:
    df = _short_series_frame(n_players=60, max_att=2, signal=False, seed=5)
    chosen, results = COMBINE.fit_chain(df, CFG, include_load2=True)
    assert not results[0]["accepted"], "primary LMM unexpectedly accepted on short-series data"
    assert chosen["estimator"] != "lmm_random_slopes", "did not fall back"
    with tempfile.TemporaryDirectory() as td:
        summ = COMBINE.run_pipeline(df, _small_cfg(perm_n=40), "sample",
                                    "UNVALIDATED SAMPLE OUTPUT", Path(td))
        slopes = pd.read_parquet(Path(td) / "combine_player_slopes.parquet")
        diag = pd.read_csv(Path(td) / "combine_model_diagnostics.csv")
    assert summ["estimator_chosen"] in COMBINE.ESTIMATORS
    assert summ["estimator_chosen"] != "lmm_random_slopes", "pipeline did not log/use a fallback"
    dv = dict(zip(diag["metric"], diag["value"].astype(str)))
    assert dv["converged_lmm_random_slopes"] == "failed", "fallback not logged"
    assert len(slopes) > 0, "no slopes emitted on fallback"
    assert np.isfinite(summ["reliability"]), "no reliability value emitted on fallback"
    return PASS


# --------------------------------------------------------------------------- #
# 5. Edge cases
# --------------------------------------------------------------------------- #
def test_edge_cases() -> str:
    # (a) one-attempt players are excluded from per-player slopes but emitted (NaN) downstream
    df = _crafted_frame([
        {"nfl_id": 1, "drill_type": "D", "perf_z": 0.3, "prior_load_yd": 0.0,
         "first_attempt": 1, "attempt": 1, "event_id": "a", "session_id": "1:1",
         "attempt_start_time": pd.Timestamp("2025-01-01")},
        {"nfl_id": 2, "drill_type": "D", "perf_z": 0.1, "prior_load_yd": 0.0,
         "first_attempt": 1, "attempt": 1, "event_id": "b", "session_id": "2:1",
         "attempt_start_time": pd.Timestamp("2025-01-01")},
        {"nfl_id": 2, "drill_type": "D", "perf_z": -0.1, "prior_load_yd": 20.0,
         "first_attempt": 0, "attempt": 2, "event_id": "c", "session_id": "2:1",
         "attempt_start_time": pd.Timestamp("2025-01-01 00:01:00")},
    ])
    sp = COMBINE.per_player_slopes(df.assign(load_c=df["prior_load_yd"] - df["prior_load_yd"].mean()), "load_c", 2)
    assert set(sp["nfl_id"]) == {2}, "1-attempt player should have no slope"
    chosen = COMBINE.fit_per_player_eb(df, CFG, include_load2=False)
    ptab = COMBINE.player_slope_table(df, df, chosen, CFG, "UNVALIDATED SAMPLE OUTPUT")
    r1 = ptab.loc[ptab["nfl_id"] == 1].iloc[0]
    assert pd.isna(r1["slope_raw"]) and not bool(r1["meets_link_min_attempts"]), "1-attempt player mishandled"

    # (b) numbering gaps: imputed rows are NOT model observations
    gap = _crafted_frame([
        {"nfl_id": 5, "drill_type": "D", "perf_z": 0.2, "prior_load_yd": 0.0, "first_attempt": 1,
         "attempt": 1, "event_id": "g1", "session_id": "5:1", "is_imputed": 0,
         "attempt_start_time": pd.Timestamp("2025-01-01")},
        {"nfl_id": 5, "drill_type": "D", "perf_z": np.nan, "prior_load_yd": 10.0, "first_attempt": 0,
         "attempt": 2, "event_id": None, "session_id": "5:1", "is_imputed": 1,
         "attempt_start_time": pd.NaT},
        {"nfl_id": 5, "drill_type": "D", "perf_z": 0.4, "prior_load_yd": 20.0, "first_attempt": 0,
         "attempt": 3, "event_id": "g3", "session_id": "5:1", "is_imputed": 0,
         "attempt_start_time": pd.Timestamp("2025-01-01 00:02:00")},
    ])
    with tempfile.TemporaryDirectory() as td:
        summ = COMBINE.run_pipeline(gap, _small_cfg(perm_n=20), "sample",
                                    "UNVALIDATED SAMPLE OUTPUT", Path(td))
    assert summ["n_obs"] == 2, f"imputed row counted as a model observation (n_obs={summ['n_obs']})"

    # (c) ties in order are deterministic (tie-break by attempt, event_id)
    tied = _crafted_frame([
        {"nfl_id": 7, "drill_type": "D", "perf_z": 0.5, "prior_load_yd": 0.0, "first_attempt": 1,
         "attempt": 1, "event_id": "t1", "session_id": "7:1", "attempt_start_time": pd.Timestamp("2025-01-01")},
        {"nfl_id": 7, "drill_type": "D", "perf_z": -0.5, "prior_load_yd": 10.0, "first_attempt": 0,
         "attempt": 2, "event_id": "t2", "session_id": "7:1", "attempt_start_time": pd.Timestamp("2025-01-01")},
    ])
    b1 = COMBINE.baseline_difference(tied, CFG)
    b2 = COMBINE.baseline_difference(tied.iloc[::-1].reset_index(drop=True), CFG)
    assert abs(b1["baseline_diff"].iloc[0] - (-1.0)) < 1e-9, b1.to_dict("records")
    assert abs(b1["baseline_diff"].iloc[0] - b2["baseline_diff"].iloc[0]) < 1e-12, "tie-break not deterministic"

    # (d) constant performance -> slopes unidentifiable -> zero reliability (stop rule fires)
    const = _short_series_frame(n_players=40, max_att=3, signal=False, seed=9)
    const["perf_z"] = 0.25
    ebr = COMBINE.fit_per_player_eb(const, CFG, include_load2=True)
    assert ebr["tau2"] == 0.0, f"constant performance should give tau2=0 (got {ebr['tau2']})"
    assert ebr["reliability"] < CFG["model"]["reliability_stop_threshold"], \
        f"constant performance should give low reliability (got {ebr['reliability']})"

    # (e) (near-)zero-variance load -> load2 dropped; build_design safe
    crit = COMBINE.load2_criterion(np.full(50, 3.0), CFG)
    assert crit["include"] is False and crit["n_unique"] == 1
    dz = COMBINE.build_design(const.assign(prior_load_yd=1.0), CFG, include_load2=False,
                              load_col="prior_load_yd")
    assert np.allclose(dz["load_s"].to_numpy(), 0.0), "zero-variance load rescale not guarded"
    return PASS


# --------------------------------------------------------------------------- #
# 6. Stop rule: low-reliability data -> stop_rule_triggered + NULL finding
# --------------------------------------------------------------------------- #
def test_stop_rule() -> str:
    df = _short_series_frame(n_players=60, max_att=2, signal=False, seed=5)  # no slope signal
    chosen, _ = COMBINE.fit_chain(df, CFG, include_load2=True)
    with tempfile.TemporaryDirectory() as td:
        summ = COMBINE.run_pipeline(df, _small_cfg(perm_n=40), "sample",
                                    "UNVALIDATED SAMPLE OUTPUT", Path(td))
        summary_md = (Path(td) / "SUMMARY.md").read_text()
    assert summ["stop_rule_triggered"] is True, f"stop rule not triggered (est={summ['estimator_chosen']}, rel={summ['reliability']})"
    assert summ["reliability"] < CFG["model"]["reliability_stop_threshold"]
    assert "NULL" in summary_md, "stop-rule summary does not report the null as the finding"
    return PASS


# --------------------------------------------------------------------------- #
# 7. Determinism
# --------------------------------------------------------------------------- #
def _hash_outputs() -> dict[str, str]:
    out = _out()
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(out.glob("*")) if f.is_file()}


def test_determinism() -> str:
    run = [sys.executable, str(REPO / "src" / "03_combine_model.py"), "--mode", "sample", "--force"]
    subprocess.run(run, cwd=REPO, check=True, capture_output=True)
    h1 = _hash_outputs()
    subprocess.run(run, cwd=REPO, check=True, capture_output=True)
    h2 = _hash_outputs()
    assert h1 == h2, "combine-model outputs are not deterministic across runs"
    return PASS


def main() -> int:
    tests = [test_schema, test_real_outputs, test_synthetic_recovery, test_fallback_path,
             test_edge_cases, test_stop_rule, test_determinism]
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
