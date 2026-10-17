#!/usr/bin/env python3
"""Behaviour tests for Phase 2 — the Combine envelope + feature selection (`src/02_combine_envelope.py`).

Runnable **without pytest** (`python src/tests/test_envelope.py`) and collected by pytest (functions
named `test_*`). Covers:
  * kinematics / envelope-fit recovery on a synthetic trajectory (unit);
  * ICC `UNESTIMABLE` handling on thin repeats; VIF behaviour; hierarchical shrinkage;
  * the stage output schema (`provenance` FIRST column), the pre-declared retained feature set, the
    residual-VIF flag, and deterministic re-runs;
  * the freeze gate wiring: a `--mode full` run is BLOCKED on an unfrozen pre-registration.
"""
from __future__ import annotations

import copy
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402
import combine_features as cf  # noqa: E402

STAGE = REPO / "src" / "02_combine_envelope.py"
SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(out: Path) -> Path:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(out)
    p = out.parent / "config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _run(args: list[str], cfg: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STAGE)] + args + ["--config", str(cfg)],
                          cwd=str(REPO), capture_output=True, text=True)


def _first_col(path: Path) -> str:
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")[0]


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _stage(cfg: Path) -> Path:
    r = _run(["--mode", "sample"], cfg)
    assert r.returncode == 0, f"stage failed:\n{r.stderr[-3000:]}"
    return Path(yaml.safe_load(cfg.read_text())["paths"]["outputs"]) / "envelope"


# --------------------------------------------------------------------------- #
# unit: kinematics / envelope recovery
# --------------------------------------------------------------------------- #
def test_envelope_recovers_planted_curve():
    cfg = common.load_config()
    vmax0, tau0, dt = 10.0, 1.1, 0.1
    t = np.arange(0.0, 8.0, dt)  # long enough for the asymptote to be identifiable
    v = vmax0 * (1.0 - np.exp(-t / tau0))
    # build x so the x,y-derived speed (recovered by _speed_path) equals the planted curve exactly
    x = np.zeros_like(v)
    x[1:] = np.cumsum(v[1:] * dt)
    y = np.zeros_like(x)
    feats = cf.attempt_straight_features(x, y, t, cfg)

    # the fitted envelope must reproduce the observed trajectory (this is what 'fit' means)
    onset = float(cfg["envelope"]["fit"]["onset_speed_yds"])
    i0 = int(np.where(v >= onset)[0][0])
    t_rel, vv = t - t[i0], v
    pred = feats["env_vmax"] * (1.0 - np.exp(-t_rel / feats["env_tau"]))
    mask = v >= onset
    rmse = float(np.sqrt(np.mean((pred[mask] - vv[mask]) ** 2)))
    # the fitted envelope must reproduce the observed trajectory within a few percent of peak speed
    # (the residual reflects the movement-onset time origin used by the pipeline, not a fit failure)
    assert rmse / vmax0 < 0.03, rmse
    # the asymptote is at/near the observed peak; tau is finite and plausible; a0 = vmax/tau exactly
    assert feats["env_vmax"] > 0.95 * feats["peak_speed"]
    assert abs(feats["env_vmax"] - vmax0) / vmax0 < 0.03
    assert 0.5 < feats["env_tau"] < 3.0
    assert np.isclose(feats["env_a0"], feats["env_vmax"] / feats["env_tau"])
    assert abs(feats["peak_speed"] - vmax0) < 0.15


def test_fit_envelope_too_short_is_nan():
    cfg = common.load_config()
    t = np.array([0.0, 0.1, 0.2])
    v = np.array([0.0, 0.2, 0.4])
    vmax, tau, a0 = cf.fit_envelope(t, v, cfg)
    assert np.isnan(vmax) and np.isnan(tau) and np.isnan(a0)


# --------------------------------------------------------------------------- #
# unit: ICC / VIF / shrinkage
# --------------------------------------------------------------------------- #
def test_icc_unestimable_on_thin_repeats():
    groups = {1: [1.0, 1.1], 2: [2.0, 2.05]}  # only 2 players with repeats
    r = cf.icc_1_1(groups, min_players=5)
    assert r["icc"] is None and r["status"].startswith("UNESTIMABLE")
    r2 = cf.icc_1_1({i: [float(i), float(i) + 0.01] for i in range(6)}, min_players=5)
    assert r2["status"] == "ESTIMATED" and r2["icc"] is not None and r2["icc"] > 0.9


def test_vif_detects_collinearity():
    rng = np.random.default_rng(0)
    base = rng.normal(size=200)
    X = np.column_stack([base, base * 2.0, rng.normal(size=200)])
    v = cf.vif_values(X)
    assert not np.isfinite(v[0]) and not np.isfinite(v[1]), v  # perfectly collinear -> inf
    assert v[2] < 2.0, v  # independent column -> ~1


def test_hierarchical_shrinkage_pulls_toward_grand_mean():
    vals = {"a": [10.0, 10.2], "b": [8.0, 8.1], "c": [12.0, 12.1], "d": [9.0, 8.9], "e": [11.0, 11.1]}
    out = cf.shrink_hierarchical(vals, min_attempts=1)
    comp = out.pop("__components__")
    assert comp["n_players"] == 5 and comp["tau2"] is not None
    for k, r in out.items():
        assert np.isfinite(r["mean"]) and np.isfinite(r["se"])
        # shrunk estimate lies between the raw mean and the grand mean (or equals one at w=1/0)
        lo, hi = sorted([r["raw_mean"], comp["mu"]])
        assert lo - 1e-9 <= r["mean"] <= hi + 1e-9, (k, r, comp["mu"])
        assert 0.0 <= r["shrink_weight"] <= 1.0


# --------------------------------------------------------------------------- #
# stage: schema + pre-declared selection + determinism
# --------------------------------------------------------------------------- #
def test_stage_outputs_and_selection(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out")
    env = _stage(cfg)

    for f in ("per_player_envelope.csv", "attempt_features.csv", "cut_metrics.csv",
              "feature_selection.csv", "selection.json", "SUMMARY.md"):
        assert (env / f).exists(), f"missing {f}"
    for f in env.glob("*.csv"):
        assert _first_col(f) == "provenance", f"{f.name}: first column is not provenance"
        assert _rows(f)[0]["provenance"] == SAMPLE_PROV, f"{f.name}: wrong provenance"

    # per-player table: SEs present, envelope + model-free features
    pp = _rows(env / "per_player_envelope.csv")
    assert len(pp) > 300
    for c in ("env_vmax", "env_vmax_se", "env_tau", "env_tau_se", "env_a0", "env_a0_se",
              "raw_env_vmax", "peak_speed", "peak_speed_se", "speed_at_20yd"):
        assert c in pp[0], f"per-player table missing {c}"

    # pre-declared reliable feature set is REPRODUCED by ICC -> VIF
    sel = {r["feature"]: r for r in _rows(env / "feature_selection.csv")}
    retained = sorted(f for f, r in sel.items() if r["retained"] == "True")
    assert retained == ["env_vmax", "peak_speed", "speed_at_20yd"], retained
    # ICC gate: env_tau/env_a0/time splits are dropped below the floor
    for f in ("env_tau", "env_a0", "time_to_10yd"):
        assert sel[f]["retained"] == "False" and "ICC below" in sel[f]["decision"]
    # VIF pruning drops the collinear raw-speed features, leaving the two flagged as residual
    for f in ("speed_at_10yd", "min_speed_after_peak", "speed_at_t2.0s"):
        assert "VIF pruning" in sel[f]["decision"], (f, sel[f]["decision"])

    # cut metrics table exists with the frozen cut metrics
    cut = _rows(env / "cut_metrics.csv")
    assert cut and {"entry_speed", "exit_speed", "cod_deficit"} <= set(cut[0])
    return env


def test_icc_matches_audit_sample(tmp: Path | None = None):
    """Phase-2 ICC must reproduce the audit's 40-yd-dash ICC (same feature definitions)."""
    audit = REPO / "outputs" / "audit" / "g_feature_icc.csv"
    if not audit.exists():
        return  # audit outputs are gitignored; skip when absent
    tmp = tmp or Path(tempfile.mkdtemp())
    env = _stage(_cfg(tmp / "out"))
    mine = {r["feature"]: float(r["icc"]) for r in _rows(env / "feature_selection.csv")
            if r["icc"] not in ("", None)}
    audit_rows = {r["feature"]: float(r["icc"]) for r in _rows(audit)
                  if r["drill_type"] == "FORTY_YARD_DASH" and r["icc"] not in ("", None)}
    for f in ("peak_speed", "speed_at_10yd", "speed_at_20yd", "env_vmax", "env_tau", "env_a0"):
        assert f in mine and f in audit_rows
        assert abs(mine[f] - audit_rows[f]) < 0.02, (f, mine[f], audit_rows[f])


def test_determinism():
    t1, t2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    e1 = _stage(_cfg(t1 / "out"))
    e2 = _stage(_cfg(t2 / "out"))
    for name in ("per_player_envelope.csv", "feature_selection.csv", "cut_metrics.csv"):
        assert (e1 / name).read_text() == (e2 / name).read_text(), f"non-deterministic: {name}"


# --------------------------------------------------------------------------- #
# gate wiring: full run blocked on an unfrozen prereg
# --------------------------------------------------------------------------- #
def test_full_run_blocked_without_freeze():
    tmp = Path(tempfile.mkdtemp())
    cfg = common.load_config()
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(tmp / "out")
    notes = tmp / "notes"
    notes.mkdir(parents=True)
    (notes / "prereg.md").write_text("# Pre-registration (skeleton)\n\n> Status: skeleton\n\n- TBD\n")
    cfg["paths"]["notes"] = str(notes)
    p = tmp / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    r = _run(["--mode", "full", "--force"], p)
    assert r.returncode == 2, f"full run not blocked:\n{r.stdout}\n{r.stderr}"
    assert "BLOCKED" in r.stderr


def test_checkpoint_is_mode_aware():
    """REGRESSION: a sample ``_DONE`` must never make a ``--mode full`` request skip.

    The notebook test run once left a sample marker in ``outputs/security``; stages that trusted a
    bare marker existence would silently reuse sample/synthetic outputs for a full run. Every stage
    checkpoint now requires the recorded mode (and provenance) to match the request exactly.
    """
    tmp = Path(tempfile.mkdtemp())
    cfg_path = _cfg(tmp / "out")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    # Fast, self-contained full run: 02 is Combine-only, and the binding fixture has both tables.
    cfg["paths"]["raw"] = str(REPO / "fixtures" / "synthetic" / "binding")
    cfg["paths"]["parquet"] = str(tmp / "parquet")
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    out_dir = tmp / "out" / "envelope"
    marker = out_dir / "_DONE"

    r = _run(["--mode", "sample"], cfg_path)
    assert r.returncode == 0, r.stderr[-3000:]
    assert json.loads(marker.read_text(encoding="utf-8"))["mode"] == "sample"

    # A full request sees the sample marker and MUST rebuild (and say so).
    r2 = _run(["--mode", "full"], cfg_path)
    assert r2.returncode == 0, r2.stderr[-3000:]
    assert json.loads(marker.read_text(encoding="utf-8"))["mode"] == "full"
    assert "checkpoint hit" not in r2.stderr, r2.stderr[-1500:]
    assert "rebuilding" in r2.stderr, r2.stderr[-1500:]

    # Only now does a repeated full run legitimately skip.
    r3 = _run(["--mode", "full"], cfg_path)
    assert r3.returncode == 0, r3.stderr[-1500:]
    assert "checkpoint hit" in r3.stderr, r3.stderr[-1500:]


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_envelope_recovers_planted_curve, test_fit_envelope_too_short_is_nan,
             test_icc_unestimable_on_thin_repeats, test_vif_detects_collinearity,
             test_hierarchical_shrinkage_pulls_toward_grand_mean, test_stage_outputs_and_selection,
             test_icc_matches_audit_sample, test_determinism, test_full_run_blocked_without_freeze,
             test_checkpoint_is_mode_aware]
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
