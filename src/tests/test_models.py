#!/usr/bin/env python3
"""Behaviour tests for Phase 4 — quantile models (`src/04_models.py`).

Runnable **without pytest** (`python src/tests/test_models.py`) and collected by pytest
(functions named `test_*`). Covers:
  * the committed synthetic fixture end-to-end for BOTH regimes on the *identical* code path:
    the **planted ceiling is recovered** (`binding` → low exceedance, verdict `binding`,
    utilization `u` < 1) and the **null is reported as null** (`null` → exceedance 1.0, verdict
    `not_binding_use_utilization`, u > 1);
  * the pre-registered **fallback chain** actually fires and a fallback **nulls** the reported
    estimate (never a forced number);
  * output schema (`provenance` FIRST column) + provenance labelling;
  * gate wiring / full-run discipline: BLOCKED without the freeze, and BLOCKED when the thresholds
    provenance is not `FULL`;
  * sample mode emits a labelled `PENDING FULL RUN` placeholder (never fabricated numbers);
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
import common  # noqa: E402  (import clean after installing requirements)

THR = REPO / "src" / "00_thresholds.py"
GAM = REPO / "src" / "03_game_demand.py"
MODELS = REPO / "src" / "04_models.py"
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
    if fast:  # exercise the code path with a small bootstrap (the frozen default is 1000)
        cfg["models"]["bootstrap_reps"] = 30
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
    """Run the full synthetic chain (thresholds → demand → models) and return the models dir."""
    out = tmp / "out"
    cfg = _cfg(out, notes=tmp / "notes")
    for stage, extra in ((THR, []), (GAM, []), (MODELS, [])):
        r = _run(stage, ["--synthetic", "--synthetic-regime", regime, "--force", *extra], cfg)
        assert r.returncode == 0, f"{stage.name} failed:\n{r.stdout}\n{r.stderr[-2000:]}"
    return out / "models" / "synthetic" / regime


# --------------------------------------------------------------------------- #
# synthetic end-to-end: planted ceiling recovered (binding) vs null reported (null)
# --------------------------------------------------------------------------- #
def test_synthetic_binding_recovers_planted_ceiling():
    d = _synthetic(Path(tempfile.mkdtemp()), "binding")
    s = json.loads((d / "SUMMARY.json").read_text())
    rate = s["overall_exceedance_rate"]
    assert 0.03 <= rate <= 0.25, f"binding exceedance {rate} not recovered (~planted 0.10)"
    assert s["overall_verdict"] == "binding", s["overall_verdict"]
    pooled = next(r for r in s["exceedance_utilization"] if r["pooling"] == "pooled")
    assert pooled["u_median"] is not None and pooled["u_median"] < 1.0, pooled


def test_synthetic_null_reports_null():
    d = _synthetic(Path(tempfile.mkdtemp()), "null")
    s = json.loads((d / "SUMMARY.json").read_text())
    assert s["overall_exceedance_rate"] >= 0.6, s["overall_exceedance_rate"]
    assert s["overall_verdict"] == "not_binding_use_utilization", s["overall_verdict"]
    pooled = next(r for r in s["exceedance_utilization"] if r["pooling"] == "pooled")
    assert pooled["u_median"] > 1.0, pooled


# --------------------------------------------------------------------------- #
# schema + provenance
# --------------------------------------------------------------------------- #
def test_outputs_schema_and_provenance():
    d = _synthetic(Path(tempfile.mkdtemp()), "binding")
    for f in ("model_coefficients.csv", "exceedance_utilization.csv", "fallbacks_fired.csv",
              "SUMMARY.json", "SUMMARY.md"):
        assert (d / f).exists(), f"missing {f}"
    for f in (d / "model_coefficients.csv", d / "exceedance_utilization.csv", d / "fallbacks_fired.csv"):
        assert _first_col(f) == "provenance", f"{f.name}: first column not provenance"
        assert _rows(f)[0]["provenance"] == SYNTH_PROV, f"{f.name}: wrong provenance"
    coeff = _rows(d / "model_coefficients.csv")
    assert coeff and {"pooling", "controls", "tau", "estimate", "reported", "boot_se"} <= set(coeff[0])
    taus = sorted({float(r["tau"]) for r in coeff})
    q = [float(x) for x in common.load_config()["models"]["quantiles"]]
    assert taus == sorted(q), (taus, q)


# --------------------------------------------------------------------------- #
# fallback chain: fires and nulls the reported estimate
# --------------------------------------------------------------------------- #
def test_fallback_chain_fires_and_nulls():
    d = _synthetic(Path(tempfile.mkdtemp()), "binding")
    fb = _rows(d / "fallbacks_fired.csv")
    assert fb, "no fallback fired on the thin fixture"
    allowed = set(common.load_config()["models"]["fallbacks"])
    assert all(r["fallback"] in allowed for r in fb), {r["fallback"] for r in fb} - allowed
    # every row whose action is a nulling fallback must have a blank reported estimate
    for r in _rows(d / "model_coefficients.csv"):
        if r["action"] in ("report_null", "adjacent_tau_report"):
            assert r["reported"] == "", f"action={r['action']} but reported={r['reported']!r}"


# --------------------------------------------------------------------------- #
# full-run discipline: gate + thresholds provenance
# --------------------------------------------------------------------------- #
def test_full_run_blocked_without_freeze():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    notes.mkdir(parents=True)
    (notes / "prereg.md").write_text("# Pre-registration (skeleton)\n\n> Status: skeleton\n\n- TBD\n")
    cfg = _cfg(tmp / "out", notes=notes)
    r = _run(MODELS, ["--mode", "full", "--force"], cfg)
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
    r = _run(MODELS, ["--mode", "full", "--force"], cfg)
    assert r.returncode == 2, f"non-FULL thresholds not blocked:\n{r.stdout}\n{r.stderr}"
    assert "04_models BLOCKER" in (notes / "blockers.md").read_text()


# --------------------------------------------------------------------------- #
# sample placeholder
# --------------------------------------------------------------------------- #
def test_sample_pending_placeholder():
    tmp = Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r = _run(MODELS, ["--mode", "sample", "--force"], cfg)
    assert r.returncode == 0, r.stderr[-2000:]
    out = Path(yaml.safe_load(cfg.read_text())["paths"]["outputs"]) / "models"
    s = _rows(out / "models_summary.csv")[0]
    assert s["provenance"] == SAMPLE_PROV and s["status"] == "PENDING FULL RUN"


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_determinism():
    a = _synthetic(Path(tempfile.mkdtemp()), "binding")
    b = _synthetic(Path(tempfile.mkdtemp()), "binding")
    for name in ("model_coefficients.csv", "exceedance_utilization.csv", "fallbacks_fired.csv"):
        assert (a / name).read_text() == (b / name).read_text(), f"non-deterministic: {name}"


def test_checkpoint_skips_finished_run():
    tmp = Path(tempfile.mkdtemp())
    _synthetic(tmp, "binding")
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r = _run(MODELS, ["--synthetic", "--synthetic-regime", "binding"], cfg)  # no --force
    assert r.returncode == 0 and "checkpoint hit" in r.stderr, r.stderr[-500:]


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_synthetic_binding_recovers_planted_ceiling, test_synthetic_null_reports_null,
             test_outputs_schema_and_provenance, test_fallback_chain_fires_and_nulls,
             test_full_run_blocked_without_freeze, test_full_blocked_on_non_full_thresholds,
             test_sample_pending_placeholder, test_determinism, test_checkpoint_skips_finished_run]
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
