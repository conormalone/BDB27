"""Tests for the Phase-3b pre-registered metric panel (``src/03b_metric_panel.py``).

Standalone (no pytest required): ``python src/tests/test_metric_panel.py``
Also pytest-compatible (functions are named ``test_*``).

Covers: panel schema (CSV/JSON), HMLD/HSD/accel computation on a crafted frame
(incl. clipping + the HMLD = HSD + accel identity), the di Prampero EC formula,
determinism, a synthetic-recovery check that a planted per-player slope is recovered
under at least one load metric, edge cases, and a regression check that the
``03_combine_model`` defaults are unchanged by the ``outcome_col`` generalisation.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from common import load_config, resolve, seed_everything  # noqa: E402

PASS, FAIL = "PASS", "FAIL"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PANEL = _load("panel03b", REPO / "src" / "03b_metric_panel.py")
CORE = _load("combine03", REPO / "src" / "03_combine_model.py")
CFG = load_config()


def _out() -> Path:
    return resolve(CFG, "outputs", CFG["panel"]["out_subdir"])


def _crafted(rows: list[dict]) -> pd.DataFrame:
    """Minimal study-schema frame (Phase-3 columns + new load-metric columns)."""
    base = {
        "provenance": "UNVALIDATED SAMPLE OUTPUT", "is_imputed": 0, "drill_name": "d",
        "attempt_unit": "drill_type", "combine_position": "DB", "draft_year": 2025,
        "effort_cost_yd": 8.0, "rest_s": 30.0, "prior_load_observed_yd": None,
        "prior_load_efforts": 0, "prior_load_observed_efforts": 0, "elapsed_session_s": 0.0,
        "first_attempt": 0,
    }
    out = []
    for r in rows:
        d = dict(base)
        d.update(r)
        if d.get("prior_load_observed_yd") is None:
            d["prior_load_observed_yd"] = d.get("prior_load_yd", 0.0)
        out.append(d)
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# 1. Panel schema (CSV / JSON)
# --------------------------------------------------------------------------- #
def test_panel_schema() -> str:
    csv = _out() / "metric_panel.csv"
    js = _out() / "metric_panel.json"
    assert csv.exists() and js.exists(), "panel outputs missing (run src/03b_metric_panel.py)"
    df = pd.read_csv(csv)
    assert df.columns[0] == "provenance", "metric_panel.csv must carry provenance FIRST"
    for c in PANEL.PANEL_FIELDS:
        assert c in df.columns, f"metric_panel.csv missing required field {c}"
    assert len(df) == 21, f"expected 21 (L1-L7 x O1-O3) cells, got {len(df)}"
    assert df["provenance"].notna().all() and (df["provenance"] == df["provenance"].iloc[0]).all()
    # reliability bounds + gate consistency + back-reference columns
    assert (df["reliability"] >= 0).all() and (df["reliability"] <= 1).all(), "reliability out of [0,1]"
    assert (df["reliability_met"] == (df["reliability"] >= 0.2)).all(), "reliability_met inconsistent"
    assert (df["stop_triggered"] == (df["reliability"] < 0.2)).all(), "stop_triggered inconsistent"
    assert (df["load_n_unique"] >= 0).all() and np.isfinite(df["load_iqr"]).all()
    # load2 rule (panel): included iff n_unique>=20 AND IQR>0
    exp_l2 = (df["load_n_unique"] >= CFG["panel"]["load2_min_unique"]) & \
             (df["load_iqr"] > CFG["panel"]["load2_min_iqr"])
    assert (df["load2_included"] == exp_l2).all(), "load2 rule not applied per the panel declaration"
    # 7 loads x 3 outcomes, no duplicates
    assert df["load_code"].nunique() == 7 and df["outcome_code"].nunique() == 3
    assert len(df.drop_duplicates(["load_code", "outcome_code"])) == 21
    summ = json.loads(js.read_text())
    assert summ["n_cells"] == 21 and len(summ["cells"]) == 21
    assert "metrics_lifting_reliability_by_outcome" in summ
    assert summ["reliability_stop_threshold"] == CFG["panel"]["reliability_stop_threshold"]
    diag = pd.read_csv(_out() / "metric_panel_diagnostics.csv")
    assert diag.columns[0] == "provenance", "diagnostics CSV must carry provenance FIRST"
    return PASS


# --------------------------------------------------------------------------- #
# 2. HMLD / HSD / accel computation on a crafted frame (incl. clipping)
# --------------------------------------------------------------------------- #
def test_hmld_hsd_accel_crafted() -> str:
    thr = PANEL.panel_thresholds(CFG)
    # (a) constant HIGH speed (8 yd/s > v_hs 6.01), zero accel => hsd>0, accel≈0, hmld=hsd
    t = np.arange(11) * 0.1
    x = np.arange(11) * 0.8
    y = np.zeros(11)
    m = PANEL.attempt_metrics(t, x, y, thr, CFG["panel"]["ec_coef"], 0.5)
    assert m["hsd_yd"] > 0, "high-speed distance not counted"
    assert abs(m["hmld_yd"] - (m["hsd_yd"] + m["accel_yd"])) < 1e-9, "HMLD != HSD + accel"
    assert m["accel_yd"] < 1e-6, f"constant-velocity accel should be ~0 (got {m['accel_yd']})"

    # (b) LOW speed but a hard acceleration burst => accel/decel distance counted
    t = np.arange(11) * 0.1
    x = np.array([0, 0.05, 0.2, 0.5, 0.9, 1.4, 2.0, 2.7, 2.75, 2.8, 2.85])  # accel then stop
    y = np.zeros(11)
    mb = PANEL.attempt_metrics(t, x, y, thr, CFG["panel"]["ec_coef"], 0.5)
    assert mb["accel_yd"] > 0, "accel/decel distance-equivalent not counted"
    assert abs(mb["hmld_yd"] - (mb["hsd_yd"] + mb["accel_yd"])) < 1e-9

    # (c) clipping is applied BEFORE thresholds: a 30 yd/s teleport is clipped + counted
    t2 = np.array([0.0, 0.1])
    x2 = np.array([0.0, 3.0])  # 30 yd/s
    y2 = np.zeros(2)
    mc = PANEL.attempt_metrics(t2, x2, y2, thr, CFG["panel"]["ec_coef"], 0.5)
    assert mc["n_clip_speed"] >= 1, "speed clip not detected"
    # clipped speed (12.03 yd/s) still exceeds v_hs => counted as high speed
    assert mc["hsd_yd"] > 0

    # (d) large gap (dt > distance_gap_max_s) is excluded from the distance metrics
    t3 = np.array([0.0, 1.0])  # dt=1.0 > 0.5
    x3 = np.array([0.0, 5.0])
    y3 = np.zeros(2)
    md = PANEL.attempt_metrics(t3, x3, y3, thr, CFG["panel"]["ec_coef"], 0.5)
    assert md["hsd_yd"] == 0.0 and md["accel_yd"] == 0.0 and md["mp_yd"] == 0.0
    return PASS


# --------------------------------------------------------------------------- #
# 3. di Prampero / Osgnach energy-cost formula
# --------------------------------------------------------------------------- #
def test_ec_formula() -> str:
    coef = CFG["panel"]["ec_coef"]
    g = CFG["panel"]["g_mps2"]
    # at ES = 0 (a = 0) => EC = 3.6 (the constant term)
    assert abs(float(PANEL.ec_energy_cost(np.array([0.0]), g, coef)[0]) - 3.6) < 1e-12
    # manual polynomial check at a = g (ES = arctan(1) = pi/4)
    a = g
    es = np.arctan(1.0)
    manual = 155.4 * es ** 5 - 30.4 * es ** 4 - 43.3 * es ** 3 + 46.3 * es ** 2 + 19.5 * es + 3.6
    got = float(PANEL.ec_energy_cost(np.array([a]), g, coef)[0])
    assert abs(got - manual) < 1e-9, f"EC mismatch {got} vs {manual}"
    # acceleration raises the cost above the ES=0 baseline
    assert float(PANEL.ec_energy_cost(np.array([g]), g, coef)[0]) > 3.6
    return PASS


# --------------------------------------------------------------------------- #
# 4. Synthetic recovery: a planted per-player slope is recovered under >=1 metric
# --------------------------------------------------------------------------- #
def test_synthetic_recovery_new_metric() -> str:
    seed_everything(1234)
    rng = np.random.default_rng(1234)
    rows = []
    planted = np.linspace(-0.004, 0.002, 40)  # per-player load slopes (varying => tau2>0)
    for p in range(40):
        n = int(rng.integers(4, 9))
        b = float(planted[p])
        x = np.sort(rng.uniform(0.0, 500.0, size=n))
        for a in range(n):
            load = float(x[a])
            rows.append({
                "nfl_id": 700000 + p, "drill_type": "D", "attempt": a + 1,
                "event_id": f"{p}-{a}", "session_id": f"{p}:1", "first_attempt": 1 if a == 0 else 0,
                "prior_load_hsd": load, "prior_load_yd": load, "prior_load_efforts": a,
                "perf_z": b * load + float(rng.normal(0, 0.15)),
                "peak_accel_z": b * load + float(rng.normal(0, 0.15)),
                "t90_z": b * load + float(rng.normal(0, 0.15)),
                "prior_load_hmld": load, "prior_load_accel": load, "prior_load_mp": load,
                "elapsed_session_s": float(a) * 60.0, "effort_cost_yd": 8.0,
                "attempt_start_time": pd.Timestamp("2025-01-01") + pd.Timedelta(seconds=60 * a),
            })
    df = _crafted(rows)
    cfg = copy.deepcopy(CFG)
    cfg["model"] = dict(cfg["model"])
    cfg["model"]["perm_n"] = 100
    chosen, _ = CORE.fit_chain(df, cfg, include_load2=True, load_col="prior_load_hsd",
                               outcome_col="perf_z")
    assert chosen["reliability"] >= 0.2, \
        f"planted-slope recovery failed: reliability={chosen['reliability']} (est={chosen['estimator']})"
    # recovered per-player slopes correlate with planted slopes (sign/magnitude)
    d = CORE.build_design(df, cfg, include_load2=True, load_col="prior_load_hsd")
    slopes = CORE.per_player_slopes(d, "load_c", int(cfg["model"]["eb_min_attempts"]),
                                    outcome_col="perf_z")
    mrg = slopes.merge(pd.DataFrame({"nfl_id": [700000 + i for i in range(40)], "b": planted}),
                       on="nfl_id")
    from scipy import stats
    r = float(stats.pearsonr(mrg["slope"], mrg["b"])[0])
    assert r >= 0.7, f"recovered-vs-planted slope correlation too low: r={r}"
    return PASS


# --------------------------------------------------------------------------- #
# 5. Edge cases (constant load -> load2 dropped; NaN load rows excluded)
# --------------------------------------------------------------------------- #
def test_edge_cases() -> str:
    thr = PANEL.panel_thresholds(CFG)
    # constant load column -> IQR 0 -> panel load2 rule excludes
    df = _crafted([{"nfl_id": 1, "drill_type": "D", "prior_load_hmld": 5.0, "perf_z": 0.1,
                    "attempt": 1, "event_id": "a", "session_id": "1:1",
                    "attempt_start_time": pd.Timestamp("2025-01-01")} for _ in range(30)])
    cfg = copy.deepcopy(CFG)
    cfg["model"] = dict(cfg["model"])
    cfg["model"]["perm_n"] = 20
    rec = PANEL.run_cell(df, cfg, CORE, "prior_load_hmld", "perf_z")
    assert rec["load2_included"] is False and rec["load_n_unique"] == 1
    # 1-frame attempt -> all-NaN metrics (guarded)
    m = PANEL.attempt_metrics(np.array([0.0]), np.array([0.0]), np.array([0.0]), thr,
                              CFG["panel"]["ec_coef"], 0.5)
    assert np.isnan(m["hsd_yd"]) and m["n_frames"] == 1
    # very short attempt (< window) -> smoothing shrinks, still finite
    m2 = PANEL.attempt_metrics(np.arange(4) * 0.1, np.arange(4) * 0.5, np.zeros(4), thr,
                               CFG["panel"]["ec_coef"], 0.5)
    assert np.isfinite(m2["hsd_yd"])
    return PASS


# --------------------------------------------------------------------------- #
# 6. Determinism (helper + one cell, same seed twice)
# --------------------------------------------------------------------------- #
def test_determinism() -> str:
    seed_everything(int(CFG["seed"]))
    study = CORE.load_study(resolve(CFG, "outputs", "features") / "combine_features_study.parquet")
    con = CORE.connect(threads=1)
    frames = PANEL.load_frames(con, f"read_parquet('{resolve(CFG, 'parquet') / 'combine_tracking.parquet'}')")
    obs = set(study.loc[study["is_imputed"] == 0, "event_id"].astype(str))
    m1, d1 = PANEL.compute_new_metrics(frames, obs, CFG)
    m2, d2 = PANEL.compute_new_metrics(frames, obs, CFG)
    assert m1.equals(m2), "compute_new_metrics not deterministic"
    assert d1 == d2, "metric diagnostics not deterministic"
    f1, v1 = PANEL.build_panel_frame(study, m1, CFG)
    f2, v2 = PANEL.build_panel_frame(study, m2, CFG)
    cols = ["prior_load_hsd", "prior_load_accel", "prior_load_hmld", "prior_load_mp"]
    assert np.allclose(f1[cols].to_numpy(), f2[cols].to_numpy(), equal_nan=True), "panel frame not deterministic"
    # one cell, same seed twice -> identical key numbers
    cfg = copy.deepcopy(CFG)
    cfg["model"] = dict(cfg["model"])
    cfg["model"]["perm_n"] = 100
    seed_everything(int(CFG["seed"]))
    r1 = PANEL.run_cell(f1, cfg, CORE, "prior_load_hsd", "perf_z")
    seed_everything(int(CFG["seed"]))
    r2 = PANEL.run_cell(f2, cfg, CORE, "prior_load_hsd", "perf_z")
    for k in ["load_coef", "load_se", "load_p", "perm_p", "reliability", "tau2"]:
        assert (r1[k] == r2[k]) or (np.isnan(r1[k]) and np.isnan(r2[k])), f"cell {k} not deterministic"
    return PASS


# --------------------------------------------------------------------------- #
# 7. Regression: 03_combine_model defaults unchanged by the outcome_col generalisation
# --------------------------------------------------------------------------- #
def test_defaults_unchanged() -> str:
    study = CORE.load_study(resolve(CFG, "outputs", "features") / "combine_features_study.parquet")
    model = study[(study["is_imputed"] == 0) & study["perf_z"].notna()].copy()
    model["first_attempt"] = model["first_attempt"].fillna(0).astype(int)
    crit = CORE.load2_criterion(model[CFG["model"]["load"]].to_numpy(dtype=np.float64), CFG)
    chosen, _ = CORE.fit_chain(model, CFG, include_load2=bool(crit["include"]))
    summ = json.loads((_out() / "combine_model_summary.json").read_text())
    assert chosen["estimator"] == summ["estimator_chosen"], "default estimator drifted"
    assert abs(float(chosen["load_coef"]) - float(summ["population_load_coef"])) < 1e-12, "default load coef drifted"
    assert abs(float(chosen["reliability"]) - float(summ["reliability"])) < 1e-12, "default reliability drifted"
    return PASS


def main() -> int:
    tests = [test_panel_schema, test_hmld_hsd_accel_crafted, test_ec_formula,
             test_synthetic_recovery_new_metric, test_edge_cases, test_determinism,
             test_defaults_unchanged]
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
