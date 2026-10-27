#!/usr/bin/env python3
"""Behaviour tests for Phase 3a — route-effort cut-offs (`src/00_thresholds.py`).

Runnable **without pytest** (`python src/tests/test_thresholds.py`) and collected by pytest
(functions named `test_*`). Covers:
  * sample mode writes **a-priori PLACEHOLDER** values (never derived from the sample);
  * the synthetic fixture derives cut-offs on the identical code path, deterministically, and the
    derived values differ from the placeholders (i.e. they are actually derived);
  * the histogram percentile helper is exact on a known distribution;
  * the gate wiring: a `--mode full` run is BLOCKED on an unfrozen pre-registration.
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
import game_features as gf  # noqa: E402

STAGE = REPO / "src" / "00_thresholds.py"
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(out: Path, notes: Path | None = None) -> Path:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(out)
    if notes is not None:
        cfg["paths"]["notes"] = str(notes)
    p = out.parent / "config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _run(args: list[str], cfg: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STAGE)] + args + ["--config", str(cfg)],
                          cwd=str(REPO), capture_output=True, text=True)


def _outputs(cfg: Path) -> Path:
    return Path(yaml.safe_load(cfg.read_text())["paths"]["outputs"])


# --------------------------------------------------------------------------- #
# unit: histogram percentile
# --------------------------------------------------------------------------- #
def test_histogram_percentile_exact():
    vals = np.arange(0.0, 10.0, 0.1)  # 0 .. 9.9
    h = gf.Histogram(bin_width=0.01, max_value=20.0)
    h.add(vals)
    assert h.total == len(vals)
    p50 = h.percentile(0.5)
    assert abs(p50 - float(np.percentile(vals, 50))) < 0.05, p50
    p90 = h.percentile(0.9)
    assert abs(p90 - float(np.percentile(vals, 90))) < 0.05, p90
    assert gf.Histogram(0.05, 10.0).percentile(0.9) is None  # empty -> None, never a silent 0


# --------------------------------------------------------------------------- #
# sample: a-priori placeholders only
# --------------------------------------------------------------------------- #
def test_sample_writes_placeholders(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r = _run(["--mode", "sample", "--force"], cfg)
    assert r.returncode == 0, r.stderr[-2000:]
    out = _outputs(cfg)
    payload = json.loads((out / "thresholds.json").read_text())
    assert payload["provenance"] == "PLACEHOLDER"
    assert payload["label"] == "UNVALIDATED SAMPLE OUTPUT"
    ph = common.load_config()["game"]["sample_placeholders"]
    assert payload["high_speed_threshold_yds"] == ph["high_speed_threshold_yds"]
    assert payload["accel_threshold_yds2"] == ph["accel_threshold_yds2"]
    assert "a-priori" in payload["source"]
    return out


# --------------------------------------------------------------------------- #
# synthetic: derivation on the identical code path
# --------------------------------------------------------------------------- #
def test_synthetic_derivation(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r = _run(["--synthetic", "--synthetic-regime", "binding", "--force"], cfg)
    assert r.returncode == 0, r.stderr[-2000:]
    out = _outputs(cfg)
    p = out / "game" / "synthetic" / "binding" / "thresholds.json"
    payload = json.loads(p.read_text())
    assert payload["provenance"] == SYNTH_PROV
    assert payload["method"] == "percentile"
    assert payload["n_efforts"] == 64  # 8 players x 8 route snaps
    # derived, not the placeholder
    ph = common.load_config()["game"]["sample_placeholders"]
    assert payload["high_speed_threshold_yds"] != ph["high_speed_threshold_yds"]
    # in-game effort peak speeds are ~8-12 yd/s -> a sane p90 cut-off
    assert 6.0 < payload["high_speed_threshold_yds"] < 14.0, payload["high_speed_threshold_yds"]
    return out


def test_checkpoint_skips_finished_run():
    tmp = Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r1 = _run(["--mode", "sample", "--force"], cfg)
    assert r1.returncode == 0
    r2 = _run(["--mode", "sample"], cfg)  # no --force -> checkpoint skip
    assert r2.returncode == 0 and "checkpoint hit" in r2.stderr, r2.stderr[-500:]


def test_synthetic_determinism():
    t1, t2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    a = _run(["--synthetic", "--synthetic-regime", "binding", "--force"], _cfg(t1 / "out", t1 / "notes"))
    b = _run(["--synthetic", "--synthetic-regime", "binding", "--force"], _cfg(t2 / "out", t2 / "notes"))
    assert a.returncode == 0 and b.returncode == 0
    pa = t1 / "out" / "game" / "synthetic" / "binding" / "thresholds.json"
    pb = t2 / "out" / "game" / "synthetic" / "binding" / "thresholds.json"
    assert pa.read_text() == pb.read_text(), "non-deterministic thresholds"


# --------------------------------------------------------------------------- #
# gate wiring
# --------------------------------------------------------------------------- #
def test_full_run_blocked_without_freeze():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    notes.mkdir(parents=True)
    (notes / "prereg.md").write_text("# Pre-registration (skeleton)\n\n> Status: skeleton\n\n- TBD\n")
    cfg = _cfg(tmp / "out", notes=notes)
    r = _run(["--mode", "full", "--force"], cfg)
    assert r.returncode == 2, f"full run not blocked:\n{r.stdout}\n{r.stderr}"
    assert "BLOCKED" in r.stderr


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_histogram_percentile_exact, test_sample_writes_placeholders,
             test_synthetic_derivation, test_checkpoint_skips_finished_run,
             test_synthetic_determinism, test_full_run_blocked_without_freeze]
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
