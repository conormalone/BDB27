"""Tests for the Phase-3d warm-up phenotype (``src/03d_warmup_phenotype.py``).

Standalone (no pytest required): ``python src/tests/test_warmup_phenotype.py``
Also pytest-compatible (functions are named ``test_*``).

Covers: output schema (CSV/JSON), synthetic warm-up recovery (a planted POSITIVE
per-player slope is recovered and the saturating-curve fit recovers a known curve),
the de-confounding FAIL path (a pure drill-order artefact must NOT pass), edge cases
(1-attempt players, no load spread, constant outcome) and determinism of the pure
helpers.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from common import load_config, resolve  # noqa: E402

PASS, FAIL = "PASS", "FAIL"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = _load("warmup03d", REPO / "src" / "03d_warmup_phenotype.py")
CORE = _load("combine03", REPO / "src" / "03_combine_model.py")
CFG = load_config()


def _out() -> Path:
    return resolve(CFG, "outputs", CFG["warmup"]["out_subdir"])


def _crafted(rows: list[dict]) -> pd.DataFrame:
    """Minimal frame spanning every column the tested helpers read."""
    base = {
        "provenance": "UNVALIDATED SAMPLE OUTPUT", "nfl_id": 0, "drill_type": "D",
        "drill_name": "d", "attempt": 1, "is_imputed": 0, "event_id": "e",
        "session_id": "0:1", "combine_position": "DB", "first_attempt": 0,
        "attempt_start_time": pd.Timestamp("2025-01-01"),
        "prior_load_yd": 0.0, "prior_load_efforts": 0, "prior_load_accel": 0.0,
        "within_drill_prior_load_yd": 0.0, "within_drill_prior_load_efforts": 0.0,
        "within_drill_prior_load_accel": 0.0,
        "session_rank": 1.0, "within_drill_rep_rank": 1.0,
        "perf_z": 0.0, "peak_accel_z": 0.0, "peak_accel_sg_z": 0.0, "effort_cost_yd": 8.0,
        "accel_yd": 1.0,
    }
    out = []
    for r in rows:
        d = dict(base)
        d.update(r)
        out.append(d)
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# 1. Output schema
# --------------------------------------------------------------------------- #
def test_schema() -> str:
    for name in ["warmup_cells", "warmup_deconfounding", "warmup_phenotypes",
                 "warmup_reliability", "warmup_summary"]:
        csv = _out() / f"{name}.csv"
        js = _out() / f"{name}.json"
        assert csv.exists() and js.exists(), f"missing {name}.csv/.json (run src/03d_warmup_phenotype.py)"
        df = pd.read_csv(csv)
        assert df.columns[0] == "provenance", f"{name}.csv must carry provenance FIRST"
        assert df["provenance"].notna().all()

    cells = pd.read_csv(_out() / "warmup_cells.csv")
    for c in W.CELL_FIELDS:
        assert c in cells.columns, f"warmup_cells.csv missing {c}"
    nL = len(CFG["warmup"]["load_metrics"])
    nO = len(CFG["warmup"]["outcome_metrics"])
    assert len(cells) == nL * nO, f"expected {nL * nO} cells, got {len(cells)}"
    assert (cells["reliability"] >= 0).all() and (cells["reliability"] <= 1).all()
    assert (cells["reliability_met"] == (cells["reliability"] >= 0.2)).all()
    assert (cells["stop_triggered"] == (cells["reliability"] < 0.2)).all()

    deconf = pd.read_csv(_out() / "warmup_deconfounding.csv")
    for c in ["raw_coef", "adj_coef", "adj_retain_frac", "adj_eb_within_coef",
              "within_contrast_battery", "deconf_pass", "pass_positive", "pass_retain",
              "pass_within", "is_primary"]:
        assert c in deconf.columns, f"warmup_deconfounding.csv missing {c}"
    assert int(deconf["is_primary"].sum()) == 1, "exactly one primary cell expected"

    reliab = pd.read_csv(_out() / "warmup_reliability.csv")
    for phen in ["warmup_slope", "session_position_of_peak", "sat_reps_to_peak"]:
        assert (reliab["phenotype"] == phen).any(), f"reliability missing phenotype {phen}"
    assert (reliab["reliability"] >= 0).all() and (reliab["reliability"] <= 1).all()
    assert (reliab["reliability_met"] == (reliab["reliability"] >= 0.2)).all()

    summ = json.loads((_out() / "warmup_summary.json").read_text())
    assert "headline" in summ and "deconf_pass" in summ["headline"]
    assert (summ["provenance"] == "UNVALIDATED SAMPLE OUTPUT")
    assert (_out() / "SUMMARY_3d.md").exists()
    assert (_out() / "PROVENANCE_3d.txt").exists()
    return PASS


# --------------------------------------------------------------------------- #
# 2. Synthetic warm-up recovery (planted POSITIVE per-player slope)
# --------------------------------------------------------------------------- #
def test_synthetic_warmup_recovery() -> str:
    rng = np.random.default_rng(2024)
    rows = []
    planted = np.linspace(0.02, 0.20, 40)          # positive per-player slopes (varying => tau2>0)
    for p in range(40):
        n = int(rng.integers(5, 10))
        b = float(planted[p])
        load = np.sort(rng.uniform(0.0, 10.0, size=n))
        for a in range(n):
            rows.append({
                "nfl_id": 500000 + p, "drill_type": "D", "attempt": a + 1,
                "event_id": f"{p}-{a}", "session_id": f"{p}:1",
                "prior_load_efforts": float(load[a]),
                "peak_accel_z": b * load[a] + float(rng.normal(0, 0.15)),
            })
    d = _crafted(rows)
    sl = W.player_warmup_slopes(d, CFG, CORE, "prior_load_efforts", "peak_accel_z")
    assert len(sl) == 40, f"expected 40 player slopes, got {len(sl)}"
    mrg = sl.merge(pd.DataFrame({"nfl_id": [500000 + i for i in range(40)], "b": planted}),
                   on="nfl_id")
    from scipy import stats
    r = float(stats.pearsonr(mrg["slope_raw"], mrg["b"])[0])
    assert r >= 0.7, f"planted-slope recovery too low: r={r}"
    # reliability of the planted slopes should be > 0 (between-player variance is real)
    rel = W.reliability_from_est_se(mrg["warmup_slope"].to_numpy(),
                                    mrg["warmup_slope_se"].to_numpy(),
                                    int(CFG["warmup"]["reliability_min_players"]))
    assert rel["reliability"] > 0.0, "planted heterogeneous slopes gave zero reliability"
    # the saturating-curve fit recovers a KNOWN exponential curve
    L = np.linspace(0, 10, 12)
    y = 1.0 - (1.0 - (-1.0)) * np.exp(-0.5 * L)    # ceil=1, base=-1, k=0.5 -> rtp=ln10/0.5=4.605
    grid = np.geomspace(float(CFG["warmup"]["sat_fit"]["rtp_grid_min"]),
                        float(CFG["warmup"]["sat_fit"]["rtp_grid_max"]),
                        int(CFG["warmup"]["sat_fit"]["rtp_grid_n"]))
    fit = W.fit_saturation(L, y, float(CFG["warmup"]["sat_reach_frac"]), "exponential", grid)
    assert abs(fit["ceiling"] - 1.0) < 0.05, f"ceiling not recovered: {fit}"
    assert abs(fit["rate"] - 0.5) < 0.05, f"rate/k not recovered: {fit}"
    assert abs(fit["reps_to_peak"] - 4.605) < 0.2, f"reps_to_peak not recovered: {fit}"
    return PASS


# --------------------------------------------------------------------------- #
# 3. De-confounding FAIL path (pure drill-order artefact)
# --------------------------------------------------------------------------- #
def test_deconf_fail_path() -> str:
    """Outcome driven ONLY by drill identity (later drill = higher) with load collinear
    with drill order and NO within-drill warm-up => the drill+session-adjusted coefficient
    must collapse (fails the retain rule) and the within-drill contrast is zero."""
    rng = np.random.default_rng(7)
    rows = []
    for p in range(30):
        for k, (drill, load0, out0, rank0) in enumerate([
                ("EARLY", 0.0, 0.0, 1), ("LATE", 100.0, 2.0, 2)]):
            for a in range(2):
                load = load0 + 5.0 * a
                rows.append({
                    "nfl_id": 600000 + p, "drill_type": drill, "attempt": k * 2 + a + 1,
                    "event_id": f"{p}-{k}-{a}", "session_id": f"{p}:1",
                    "session_rank": float(rank0), "within_drill_rep_rank": float(a + 1),
                    "prior_load_efforts": load,
                    "within_drill_prior_load_efforts": float(a),
                    "peak_accel_z": out0 + float(rng.normal(0, 0.01)),   # no within-drill trend
                })
    d = _crafted(rows)
    raw = CORE.inv_var_mean(
        CORE.per_player_slopes(CORE.build_design(d, CFG, include_load2=True,
                                                 load_col="prior_load_efforts"),
                               "load_c", 2, outcome_col="peak_accel_z"))[0]
    adj = W.adjusted_ols(d, "prior_load_efforts", "peak_accel_z", include_load2=True)
    bat = W.within_battery_contrast(d, CFG, "peak_accel_z", battery_only=False)
    assert np.isfinite(raw) and raw > 0, f"raw slope should be positive here (got {raw})"
    retain = abs(adj["coef"]) / abs(raw) if raw else float("nan")
    assert (not np.isfinite(retain)) or retain < 0.5, \
        f"drill-order artefact must NOT retain >=0.5 of raw (retain={retain}, adj={adj['coef']})"
    assert (not np.isfinite(bat["contrast"])) or abs(bat["contrast"]) < 0.05, \
        f"within-drill contrast should be ~0 for a pure drill-order artefact (got {bat['contrast']})"
    return PASS


# --------------------------------------------------------------------------- #
# 4. Edge cases
# --------------------------------------------------------------------------- #
def test_edge_cases() -> str:
    # (a) 1-attempt player excluded from session_position_of_peak (needs min_attempts)
    d = _crafted([
        {"nfl_id": 1, "session_rank": 1.0, "peak_accel_z": 1.0, "event_id": "1"},
        {"nfl_id": 2, "session_rank": 1.0, "peak_accel_z": 0.1, "event_id": "2a"},
        {"nfl_id": 2, "session_rank": 2.0, "peak_accel_z": 0.5, "event_id": "2b"},
        {"nfl_id": 2, "session_rank": 3.0, "peak_accel_z": 0.9, "event_id": "2c"},
    ])
    pos = W.session_position_of_peak(d, CFG, "peak_accel_z", boot_reps=50, boot_seed=1,
                                     min_attempts=3)
    assert 1 not in set(pos["nfl_id"]), "1-attempt player must be excluded"
    assert int(pos.loc[pos["nfl_id"] == 2, "session_position_of_peak"].iloc[0]) == 3

    # (b) constant load -> no spread -> saturating phenotype NaN
    d2 = _crafted([{"nfl_id": 9, "prior_load_efforts": 5.0, "peak_accel_z": float(i),
                    "event_id": f"9-{i}"} for i in range(6)])
    sat = W.player_saturation(d2, CFG, "prior_load_efforts", "peak_accel_z",
                              boot_reps=20, boot_seed=1)
    assert np.isnan(sat["sat_reps_to_peak"].iloc[0]), "no load spread => sat fit must be NaN"

    # (c) constant outcome -> sat curve NaN; per-player slope skipped
    d3 = _crafted([{"nfl_id": 10, "prior_load_efforts": float(i), "peak_accel_z": 0.0,
                    "event_id": f"10-{i}"} for i in range(6)])
    sat3 = W.player_saturation(d3, CFG, "prior_load_efforts", "peak_accel_z",
                               boot_reps=20, boot_seed=1)
    assert np.isnan(sat3["sat_reps_to_peak"].iloc[0]), "constant outcome => sat fit NaN"
    sl3 = W.player_warmup_slopes(d3, CFG, CORE, "prior_load_efforts", "peak_accel_z")
    assert len(sl3) == 0, "constant outcome must yield no per-player slope"

    # (d) constant-outcome sat fit guarded at the pure-helper level too
    assert np.isnan(W.fit_saturation(np.arange(6.0), np.zeros(6), 0.9, "exponential",
                                     np.geomspace(0.25, 200, 100))["reps_to_peak"])
    return PASS


# --------------------------------------------------------------------------- #
# 5. Determinism of the pure helpers (same input -> identical output)
# --------------------------------------------------------------------------- #
def test_determinism() -> str:
    rng = np.random.default_rng(11)
    L = rng.uniform(0, 10, 12)
    y = 0.5 + 0.3 * L + rng.normal(0, 0.1, 12)
    grid = np.geomspace(0.25, 200.0, 200)
    a = W.fit_saturation(L, y, 0.9, "exponential", grid)
    b = W.fit_saturation(L, y, 0.9, "exponential", grid)
    assert a == b, "fit_saturation not deterministic"
    h1 = W.fit_saturation(L, y, 0.9, "hyperbolic", grid)
    h2 = W.fit_saturation(L, y, 0.9, "hyperbolic", grid)
    assert h1 == h2, "hyperbolic fit not deterministic"

    d = _crafted([{"nfl_id": p, "prior_load_efforts": float(i),
                   "peak_accel_z": 0.1 * i + float(rng.normal(0, 0.1)),
                   "event_id": f"{p}-{i}", "session_id": f"{p}:1",
                   "session_rank": float(i + 1), "attempt_start_time": pd.Timestamp("2025-01-01")
                   + pd.Timedelta(seconds=60 * i)} for p in range(5) for i in range(6)])
    s1 = W.session_position_of_peak(d, CFG, "peak_accel_z", 50, 31337, 3)
    s2 = W.session_position_of_peak(d, CFG, "peak_accel_z", 50, 31337, 3)
    assert s1.equals(s2), "session_position_of_peak bootstrap not deterministic"
    sa1 = W.player_saturation(d, CFG, "prior_load_efforts", "peak_accel_z", 40, 31337)
    sa2 = W.player_saturation(d, CFG, "prior_load_efforts", "peak_accel_z", 40, 31337)
    assert sa1.equals(sa2), "player_saturation bootstrap not deterministic"
    return PASS


def main() -> int:
    tests = [test_schema, test_synthetic_warmup_recovery, test_deconf_fail_path,
             test_edge_cases, test_determinism]
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
