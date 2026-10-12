#!/usr/bin/env python3
"""Behaviour tests for Phase 3b — in-game demand (`src/03_game_demand.py`).

Runnable **without pytest** (`python src/tests/test_game_demand.py`) and collected by pytest
(functions named `test_*`). Covers:
  * the top-k tie rule (ties at the k-th value kept — prereg §2);
  * the committed synthetic fixture end-to-end for BOTH regimes: the **planted ceiling binds**
    (low exceedance) in `binding` and **does not** in `null` (high exceedance), with the envelope
    fraction = effort speed / the Phase-2 ceiling;
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
import common  # noqa: E402
import game_features as gf  # noqa: E402

STAGE = REPO / "src" / "03_game_demand.py"
THR = REPO / "src" / "00_thresholds.py"
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"
SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"


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


def _synthetic(tmp: Path, regime: str):
    out = tmp / "out"
    cfg = _cfg(out, notes=tmp / "notes")
    r0 = _run(THR, ["--synthetic", "--synthetic-regime", regime, "--force"], cfg)
    assert r0.returncode == 0, r0.stderr[-2000:]
    r1 = _run(STAGE, ["--synthetic", "--synthetic-regime", regime, "--force"], cfg)
    assert r1.returncode == 0, r1.stderr[-2000:]
    return out / "game" / "synthetic" / regime


# --------------------------------------------------------------------------- #
# unit: top-k tie rule
# --------------------------------------------------------------------------- #
def test_top_k_ties_kept():
    eff = [dict(peak_speed=v, game_id=1, play_id=i) for i, v in enumerate([9.0, 8.0, 8.0, 8.0, 7.0, 6.0])]
    kept = gf.top_k_efforts(eff, k=2, metric="peak_speed")
    # top value 9.0, then a three-way tie at 8.0 -> all three kept (never truncated to 1)
    assert len(kept) == 4, kept
    assert [e["peak_speed"] for e in kept] == [9.0, 8.0, 8.0, 8.0]
    # fewer efforts than k -> all returned
    assert len(gf.top_k_efforts(eff[:1], k=5)) == 1


# --------------------------------------------------------------------------- #
# synthetic end-to-end: planted ceiling binds (binding) vs not (null)
# --------------------------------------------------------------------------- #
def test_synthetic_binding_and_null():
    tmpb, tmpn = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    mind = _synthetic(tmpb, "binding")
    nind = _synthetic(tmpn, "null")

    for ind in (mind, nind):
        for f in ("efforts.csv", "top_k_efforts.csv", "player_summary.csv",
                  "thresholds_applied.csv", "SUMMARY.json"):
            assert (ind / f).exists(), f"missing {f} in {ind}"
        for f in ind.glob("*.csv"):
            assert _first_col(f) == "provenance", f"{f.name}: first column not provenance"
            assert _rows(f)[0]["provenance"] == SYNTH_PROV, f"{f.name}: wrong provenance"

    sb = json.loads((mind / "SUMMARY.json").read_text())
    sn = json.loads((nind / "SUMMARY.json").read_text())
    rb, rn = sb["overall_exceedance_rate"], sn["overall_exceedance_rate"]
    assert 0.03 <= rb <= 0.25, f"binding: recovered exceedance {rb} not low (planted ~0.10)"
    assert 0.6 <= rn <= 1.0, f"null: recovered exceedance {rn} not high"
    assert rb < rn, (rb, rn)

    # ceiling / envelope fraction consistency: max_over_ceiling == peak_speed / ceiling_speed
    eff = _rows(mind / "efforts.csv")
    for r in eff:
        if r["max_over_ceiling"] and r["ceiling_speed_yds"]:
            exp = float(r["peak_speed"]) / float(r["ceiling_speed_yds"])
            assert abs(exp - float(r["max_over_ceiling"])) < 1e-3, r

    # top-k: every player contributes exactly k=5 rows (8 route snaps each, no ties at the cut)
    k = int(common.load_config()["game"]["top_k_efforts"])
    per = {}
    for r in _rows(mind / "top_k_efforts.csv"):
        per[r["nfl_id"]] = per.get(r["nfl_id"], 0) + 1
    assert per and all(v == k for v in per.values()), per
    # players with < route_snap_count snaps are flagged ineligible (prereg §2), not pooled
    ps = _rows(mind / "player_summary.csv")
    assert all(r["eligible"] == "False" for r in ps)


# --------------------------------------------------------------------------- #
# full-run discipline: gate + thresholds provenance
# --------------------------------------------------------------------------- #
def test_full_run_blocked_without_freeze():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    notes.mkdir(parents=True)
    (notes / "prereg.md").write_text("# Pre-registration (skeleton)\n\n> Status: skeleton\n\n- TBD\n")
    cfg = _cfg(tmp / "out", notes=notes)
    r = _run(STAGE, ["--mode", "full", "--force"], cfg)
    assert r.returncode == 2, f"full run not blocked:\n{r.stdout}\n{r.stderr}"
    assert "BLOCKED" in r.stderr


def test_full_blocked_on_non_full_thresholds():
    tmp = Path(tempfile.mkdtemp())
    notes = _frozen_prereg(tmp / "notes")
    out = tmp / "out"
    (out / "audit").mkdir(parents=True, exist_ok=True)
    (out / "audit" / "audit_decision.json").write_text(
        json.dumps({"decision": "GO — matched WR OK, route snaps OK, fields present, ICC OK"}))
    (out / "thresholds.json").write_text(json.dumps({"provenance": "PLACEHOLDER",
                                                     "high_speed_threshold_yds": 8.0,
                                                     "accel_threshold_yds2": 3.0}))
    cfg = _cfg(out, notes=notes)
    r = _run(STAGE, ["--mode", "full", "--force"], cfg)
    assert r.returncode == 2, f"non-FULL thresholds not blocked:\n{r.stdout}\n{r.stderr}"
    blocker = (notes / "blockers.md")
    assert blocker.exists() and "03_game_demand BLOCKER" in blocker.read_text()
    assert "thresholds provenance" in r.stderr.lower() or "not FULL" in r.stderr


# --------------------------------------------------------------------------- #
# sample placeholder
# --------------------------------------------------------------------------- #
def test_sample_pending_placeholder():
    tmp = Path(tempfile.mkdtemp())
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r = _run(STAGE, ["--mode", "sample", "--force"], cfg)
    assert r.returncode == 0, r.stderr[-2000:]
    out = Path(yaml.safe_load(cfg.read_text())["paths"]["outputs"]) / "game"
    s = _rows(out / "game_demand_summary.csv")[0]
    assert s["provenance"] == SAMPLE_PROV and s["status"] == "PENDING FULL RUN"


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_checkpoint_skips_finished_run():
    tmp = Path(tempfile.mkdtemp())
    _synthetic(tmp, "binding")  # runs with --force
    cfg = _cfg(tmp / "out", notes=tmp / "notes")
    r = _run(STAGE, ["--synthetic", "--synthetic-regime", "binding"], cfg)  # no --force
    assert r.returncode == 0 and "checkpoint hit" in r.stderr, r.stderr[-500:]


def test_determinism():
    a = _synthetic(Path(tempfile.mkdtemp()), "binding")
    b = _synthetic(Path(tempfile.mkdtemp()), "binding")
    for name in ("efforts.csv", "top_k_efforts.csv", "player_summary.csv"):
        assert (a / name).read_text() == (b / name).read_text(), f"non-deterministic: {name}"


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_top_k_ties_kept, test_synthetic_binding_and_null, test_full_run_blocked_without_freeze,
             test_full_blocked_on_non_full_thresholds, test_sample_pending_placeholder,
             test_checkpoint_skips_finished_run, test_determinism]
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
