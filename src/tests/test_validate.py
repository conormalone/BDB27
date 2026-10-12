#!/usr/bin/env python3
"""Behaviour tests for Phase 5 — validation (`src/05_validate.py`).

Runnable **without pytest** (`python src/tests/test_validate.py`) and collected by pytest
(functions named `test_*`). Covers:
  * the committed synthetic fixture end-to-end for BOTH regimes: the binding **verdict** is
    `binding` (planted ceiling recovered) and the null is reported as
    `not_binding_use_utilization` (reframed to utilization — prereg §6);
  * player-grouped CV rows (full vs controls-only baseline, mean + median) present and finite;
  * the permutation p-value is a valid probability;
  * `icc_attenuation`: the corrected slope equals slope ÷ reliability;
  * every pre-declared sensitivity analysis is emitted (alt_quantiles, drop_thin_repeats,
    exclude_optouts, margin_grid);
  * gate wiring / full-run discipline: BLOCKED without the freeze / on non-`FULL` thresholds;
  * sample mode emits a labelled `PENDING FULL RUN` placeholder;
  * determinism.
"""
from __future__ import annotations

import copy
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402

THR = REPO / "src" / "00_thresholds.py"
GAM = REPO / "src" / "03_game_demand.py"
VAL = REPO / "src" / "05_validate.py"
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"
SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(out: Path, notes: Path | None = None, fast: bool = True) -> Path:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(out)
    if notes is not None:
        cfg["paths"]["notes"] = str(notes)
    if fast:
        cfg["validate"]["perm_reps"] = 100
    p = out.parent / "config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _run(stage: Path, args: list[str], cfg: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(stage)] + args + ["--config", str(cfg)],
                          cwd=str(REPO), capture_output=True, text=True)


def _frozen_prereg(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "prereg.md").write_text(
        "# Pre-registration — BDB27 ceiling-binding task (**FROZEN**)\n\n"
        "> **Status: FROZEN** (2026-10-12). Values fixed before the full runs.\n\n"
        "All values are frozen; no residual placeholders.\n", encoding="utf-8")
    return d


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _first_col(path: Path) -> str:
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")[0]


def _synthetic(tmp: Path, regime: str) -> Path:
    out = tmp / "out"
    cfg = _cfg(out, notes=tmp / "notes")
    for stage in (THR, GAM, VAL):
        r = _run(stage, ["--synthetic", "--synthetic-regime", regime, "--force"], cfg)
        assert r.returncode == 0, f"{stage.name} failed:\n{r.stdout}\n{r.stderr[-2000:]}"
    return out / "validate" / "synthetic" / regime


# --------------------------------------------------------------------------- #
# synthetic end-to-end: binding vs null verdicts
# --------------------------------------------------------------------------- #
def test_synthetic_binding_and_null_verdicts():
    b = _synthetic(Path(tempfile.mkdtemp()), "binding")
    n = _synthetic(Path(tempfile.mkdtemp()), "null")
    sb = json.loads((b / "SUMMARY.json").read_text())
    sn = json.loads((n / "SUMMARY.json").read_text())
    assert sb["binding_verdict"]["verdict"] == "binding", sb["binding_verdict"]
    assert sn["binding_verdict"]["verdict"] == "not_binding_use_utilization", sn["binding_verdict"]
    for ind in (b, n):
        for f in ("cv_results.csv", "permutation.csv", "measurement_error.csv",
                  "sensitivity.csv", "SUMMARY.md"):
            assert (ind / f).exists(), f"missing {f}"
        for f in ind.glob("*.csv"):
            assert _first_col(f) == "provenance", f"{f.name}: first column not provenance"
            assert _rows(f)[0]["provenance"] == SYNTH_PROV, f"{f.name}: wrong provenance"


# --------------------------------------------------------------------------- #
# CV structure
# --------------------------------------------------------------------------- #
def test_cv_vs_baseline_rows():
    d = _synthetic(Path(tempfile.mkdtemp()), "binding")
    rows = _rows(d / "cv_results.csv")
    taus = {float(r["tau"]) for r in rows}
    q = {float(x) for x in common.load_config()["models"]["quantiles"]}
    assert taus == q, (taus, q)
    models = {r["model"] for r in rows}
    assert {"full", "controls_only", "improvement"} <= models, models
    agg = {r["fold"] for r in rows}
    assert {"mean", "median"} <= agg, agg
    for r in rows:
        if r["fold"] == "mean":
            assert r["pinball"] not in ("", None), r


# --------------------------------------------------------------------------- #
# permutation + measurement error
# --------------------------------------------------------------------------- #
def test_permutation_pvalue_valid():
    d = _synthetic(Path(tempfile.mkdtemp()), "binding")
    p = _rows(d / "permutation.csv")[0]
    assert p["p_two_sided"] not in ("", None), p
    assert 0.0 <= float(p["p_two_sided"]) <= 1.0, p
    assert int(p["n_ok"]) > 0, p


def test_measurement_error_correction():
    d = _synthetic(Path(tempfile.mkdtemp()), "binding")
    rows = _rows(d / "measurement_error.csv")
    assert rows, "no measurement-error rows"
    for r in rows:
        icc = r["reliability_icc"]
        assert icc not in ("", None), r
        if r["slope_uncorrected"] not in ("", None) and float(icc) > 0:
            exp = float(r["slope_uncorrected"]) / float(icc)
            assert abs(exp - float(r["slope_corrected"])) < 1e-3, r


# --------------------------------------------------------------------------- #
# sensitivity analyses
# --------------------------------------------------------------------------- #
def test_sensitivity_all_declared_analyses():
    d = _synthetic(Path(tempfile.mkdtemp()), "binding")
    rows = _rows(d / "sensitivity.csv")
    present = {r["analysis"] for r in rows}
    declared = set(common.load_config()["validate"]["sensitivity"])
    assert declared <= present, (declared, present)
    mg = [r for r in rows if r["analysis"] == "margin_grid"]
    assert mg and all(r["verdict"] == "binding" for r in mg), mg


# --------------------------------------------------------------------------- #
# full-run discipline
# --------------------------------------------------------------------------- #
def test_full_run_blocked_without_freeze():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    notes.mkdir(parents=True)
    (notes / "prereg.md").write_text("# Pre-registration (skeleton)\n\n> Status: skeleton\n\n- TBD\n")
    cfg = _cfg(tmp / "out", notes=notes)
    r = _run(VAL, ["--mode", "full", "--force"], cfg)
    assert r.returncode == 2, f"full run not blocked:\n{r.stdout}\n{r.stderr}"
    assert "BLOCKED" in r.stderr


def test_full_blocked_on_non_full_thresholds():
    tmp = Path(tempfile.mkdtemp())
    notes = _frozen_prereg(tmp / "notes")
    out = tmp / "out"
    (out / "audit").mkdir(parents=True, exist_ok=True)
    (out / "audit" / "audit_decision.json").write_text(
        json.dumps({"decision": "GO — matched WR OK, route snaps OK, fields present, ICC OK"}))
    (out / "thresholds.json").write_text(json.dumps({"provenance": "PLACEHOLDER"}))
    cfg = _cfg(out, notes=notes)
    r = _run(VAL, ["--mode", "full", "--force"], cfg)
    assert r.returncode == 2, f"non-FULL thresholds not blocked:\n{r.stdout}\n{r.stderr}"
    assert "05_validate BLOCKER" in (notes / "blockers.md").read_text()


# --------------------------------------------------------------------------- #
# sample placeholder + determinism
# --------------------------------------------------------------------------- #
def test_sample_pending_placeholder():
    tmp = Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r = _run(VAL, ["--mode", "sample", "--force"], cfg)
    assert r.returncode == 0, r.stderr[-2000:]
    out = Path(yaml.safe_load(cfg.read_text())["paths"]["outputs"]) / "validate"
    s = _rows(out / "validation_summary.csv")[0]
    assert s["provenance"] == SAMPLE_PROV and s["status"] == "PENDING FULL RUN"


def test_determinism():
    a = _synthetic(Path(tempfile.mkdtemp()), "binding")
    b = _synthetic(Path(tempfile.mkdtemp()), "binding")
    for name in ("cv_results.csv", "permutation.csv", "measurement_error.csv", "sensitivity.csv"):
        assert (a / name).read_text() == (b / name).read_text(), f"non-deterministic: {name}"


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_synthetic_binding_and_null_verdicts, test_cv_vs_baseline_rows,
             test_permutation_pvalue_valid, test_measurement_error_correction,
             test_sensitivity_all_declared_analyses, test_full_run_blocked_without_freeze,
             test_full_blocked_on_non_full_thresholds, test_sample_pending_placeholder,
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
