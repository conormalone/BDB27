#!/usr/bin/env python3
"""Schema + behaviour tests for the Phase 1 audit (`src/01_audit.py`).

Runnable **without pytest** (`python src/tests/test_audit.py`) and also collected by pytest
(functions named `test_*`). Failures are loud (AssertionError).

Covers (TASK.md "tests: schema assertions on columns/dtypes/units, fail loudly"):
  * raw-file schema/dtype/unit/sampling assertions;
  * every audit CSV has `provenance` as its FIRST column, labelled per mode;
  * key audit findings are present and sane (non-targeted routes covered; ICC status explicit);
  * synthetic fixture is schema-compliant and the planted ceiling is recovered (binding + null);
  * determinism: two identical runs produce byte-identical ICC output.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402

AUDIT = REPO / "src" / "01_audit.py"
PARQ = common.resolve(common.load_config(), "parquet")
SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("PRAGMA threads=1")
    return c


def _temp_cfg(tmp: Path) -> Path:
    cfg = common.load_config()
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(tmp / "outputs")
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _run_audit(args: list[str], cfg_path: Path | None = None) -> None:
    cmd = [sys.executable, str(AUDIT)] + args
    if cfg_path is not None:
        cmd += ["--config", str(cfg_path)]
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"audit failed ({cmd}):\n{r.stderr[-3000:]}"


def _first_col(path: Path) -> str:
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")[0]


def _rows(path: Path) -> list[dict]:
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# raw schema / units (fail loudly)
# --------------------------------------------------------------------------- #
def test_raw_schemas():
    con = _con()
    ct = {r[0]: r[1] for r in con.execute(
        f"describe select * from {common.pq(PARQ / 'combine_tracking.parquet')}").fetchall()}
    assert list(ct) == ["draft_year", "event_id", "nfl_id", "entity_type", "time", "drill_type",
                        "drill_name", "attempt", "x", "y", "s", "a", "dis", "dir"]
    for c in ("x", "y", "s", "a", "dis"):
        assert ct[c] == "DOUBLE", f"{c} dtype {ct[c]} != DOUBLE"
    assert ct["nfl_id"] == "BIGINT"

    pp = {r[0] for r in con.execute(
        f"describe select * from {common.pq(PARQ / 'player_play.parquet')}").fetchall()}
    for c in ["route_ran", "pass_length", "target", "separation_at_pass_forward",
              "coverage_assignment", "coverage_assignment_at_snap", "team_coverage_man_zone",
              "team_coverage_type"]:
        assert c in pp, f"player_play missing required field {c}"

    gt = {r[0] for r in con.execute(
        f"describe select * from {common.pq(PARQ / 'game_tracking_2023_sample.parquet')}").fetchall()}
    for c in ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis", "o", "dir", "event"]:
        assert c in gt, f"game tracking missing {c}"


def test_units_and_sampling():
    con = _con()
    CT = common.pq(PARQ / "combine_tracking.parquet")
    med = con.execute(f"""WITH f AS (SELECT event_id, epoch_ms(time) t FROM {CT} WHERE entity_type='PLAYER'),
        d AS (SELECT (t-lag(t) OVER (PARTITION BY event_id ORDER BY t))/1000.0 dt FROM f)
        SELECT median(dt) FROM d WHERE dt IS NOT NULL""").fetchone()[0]
    assert abs(med - 0.1) < 1e-6, f"combine sampling {med}s != 0.1s (10 Hz)"
    # combine local frame bounds (never the NFL 120x53.3 frame)
    xr = con.execute(f"SELECT min(x),max(x),min(y),max(y) FROM {CT}").fetchone()
    assert 0 <= xr[0] and xr[1] <= 110 and xr[3] <= 70, f"combine frame bounds off: {xr}"
    # no residual literal NA
    assert con.execute(f"SELECT count(*) FROM {CT} WHERE CAST(s AS VARCHAR)='NA'").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# sample audit outputs
# --------------------------------------------------------------------------- #
def test_sample_outputs(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    cfg = _temp_cfg(tmp)
    _run_audit(["--mode", "sample", "--force"], cfg)
    ad = tmp / "outputs" / "audit"
    csvs = sorted(ad.glob("*.csv"))
    assert (ad / "SUMMARY.md").exists()
    assert len(csvs) >= 15, f"too few audit tables: {len(csvs)}"
    for f in csvs:
        assert _first_col(f) == "provenance", f"{f.name}: first column is not provenance"
        assert _rows(f)[0]["provenance"] == SAMPLE_PROV, f"{f.name}: wrong provenance label"

    # (e) non-targeted routes ARE covered
    crosstab = {r["metric"]: r["value"] for r in _rows(ad / "e_route_target_crosstab.csv")}
    assert int(crosstab["route_snaps_non_targeted"]) > 0
    assert 0.5 < float(crosstab["share_route_snaps_non_targeted"]) < 1.0

    # (d) route type is categorical -> recorded
    assert any("GO" == r["route_ran"] for r in _rows(ad / "d_route_type_values.csv"))

    # (g) every ICC row has an explicit status (never a silent blank)
    icc = _rows(ad / "g_feature_icc.csv")
    assert icc, "no ICC rows"
    assert all(r["status"].strip() for r in icc), "an ICC row has an empty status"
    peak = [r for r in icc if r["feature"] == "peak_speed" and r["drill_type"] == "FORTY_YARD_DASH"]
    assert peak and peak[0]["status"] == "ESTIMATED" and float(peak[0]["icc"]) > 0.7
    # unestimable features must say so explicitly (thin repeats)
    assert any(r["status"].startswith("UNESTIMABLE") for r in icc)

    # (h)/(i) are PENDING in sample mode (never fabricated numbers)
    h = _rows(ad / "h_exceedance.csv")[0]
    i = _rows(ad / "i_scatter.csv")[0]
    assert h["status"] == "PENDING FULL RUN" and i["status"] == "PENDING FULL RUN"

    # (f) the provided `dis` is unreliable (under-counts the x/y path)
    dis = {r["drill_type"]: r for r in _rows(ad / "f_dis_reliability.csv")}
    assert float(dis["FORTY_YARD_DASH"]["median_under_count_fraction"]) > 0.05
    return ad


# --------------------------------------------------------------------------- #
# synthetic fixture + planted-ceiling recovery
# --------------------------------------------------------------------------- #
def test_synthetic_fixture(tmp: Path | None = None):
    tmp = tmp or Path(tempfile.mkdtemp())
    fix = REPO / "fixtures" / "synthetic" / "binding"
    for fn in ("combine_tracking.csv", "combine_results.csv", "game_tracking.csv",
               "player_play.csv", "games.csv"):
        assert (fix / fn).exists(), f"fixture missing {fn}"
    con = _con()
    gt = {r[0] for r in con.execute(
        f"describe select * from read_csv_auto('{fix / 'game_tracking.csv'}')").fetchall()}
    assert {"game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis", "o", "dir", "event"} <= gt

    # regenerate to a temp dir to prove determinism of the generator
    out = tmp / "regen"
    r = subprocess.run([sys.executable, str(REPO / "src" / "synthetic_fixture.py"),
                        "--regime", "binding", "--out", str(out)], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    a = (fix / "game_tracking.csv").read_text()
    b = (out / "game_tracking.csv").read_text()
    assert a == b, "synthetic fixture generator is not deterministic"

    # planted-ceiling recovery via the audit's own game-side code path
    for regime, lo, hi in (("binding", 0.03, 0.25), ("null", 0.6, 1.0)):
        cfg = _temp_cfg(tmp / regime)
        _run_audit(["--synthetic", "--synthetic-regime", regime, "--force"], cfg)
        h = _rows(tmp / regime / "outputs" / "audit" / "synthetic" / "h_exceedance.csv")
        assert all(r["provenance"] == SYNTH_PROV for r in h), "synthetic output not labelled"
        overall = [r for r in h if r["nfl_id"] == "OVERALL"][0]
        rate = float(overall["exceedance_rate"])
        assert lo <= rate <= hi, f"{regime}: recovered exceedance {rate} not in [{lo},{hi}]"
    return tmp


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_determinism():
    t1, t2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    cfg1, cfg2 = _temp_cfg(t1), _temp_cfg(t2)
    _run_audit(["--mode", "sample", "--force"], cfg1)
    _run_audit(["--mode", "sample", "--force"], cfg2)
    for name in ("g_feature_icc.csv", "e_route_target_crosstab.csv", "f_dis_reliability.csv"):
        a = (t1 / "outputs" / "audit" / name).read_text()
        b = (t2 / "outputs" / "audit" / name).read_text()
        assert a == b, f"non-deterministic output: {name}"


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_raw_schemas, test_units_and_sampling, test_sample_outputs,
             test_synthetic_fixture, test_determinism]
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
