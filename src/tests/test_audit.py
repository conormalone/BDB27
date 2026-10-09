"""Schema assertions + audit-invariant checks + determinism for the Phase 1 audit.

Standalone (no pytest required): ``python src/tests/test_audit.py``
Also pytest-compatible (functions are named ``test_*``).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from common import load_config, resolve  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


def _pq(name: str) -> str:
    return f"read_parquet('{resolve(load_config(), 'parquet', name)}')"


def test_parquet_schema() -> str:
    con = duckdb.connect()
    desc = con.execute(f"DESCRIBE SELECT * FROM {_pq('combine_results.parquet')}").df()
    types = dict(zip(desc["column_name"], desc["column_type"]))
    # numeric split columns must be DOUBLE (i.e. NA was mapped to NULL, not kept as text)
    # measurement columns must be numeric (NA mapped to NULL), never VARCHAR
    for c in ["forty", "three_cone", "short_shuttle", "vertical", "broad_jump", "ten_yd_split", "bench_reps"]:
        assert types[c] != "VARCHAR", f"{c} is {types[c]}, expected numeric (NA->NULL failed)"
    # real missingness must be visible
    nulls = con.execute(f"SELECT count(*) FILTER (WHERE three_cone IS NULL) FROM {_pq('combine_results.parquet')}").fetchone()[0]
    assert nulls > 0, "expected opt-out NULLs in three_cone after NA->NULL mapping"
    # combine_tracking schema
    t = con.execute(f"DESCRIBE SELECT * FROM {_pq('combine_tracking.parquet')}").df()
    tcols = set(t["column_name"])
    assert {"event_id", "time", "drill_type", "drill_name", "attempt", "x", "y", "s"} <= tcols
    # no literal 'NA' left in a numeric column
    bad = con.execute(f"SELECT count(*) FROM {_pq('combine_results.parquet')} WHERE CAST(forty AS VARCHAR) = 'NA'").fetchone()[0]
    assert bad == 0, "literal 'NA' survived conversion"
    return PASS


def test_audit_invariants() -> str:
    cfg = load_config()
    out = resolve(cfg, "outputs", "audit")
    summ = json.loads((out / "audit_summary.json").read_text())
    assert summ["a"]["timing_field_present"] is True
    assert summ["a"]["order_agree_frac"] >= 0.99
    assert summ["gate"]["verdict"] in {"GO", "NO-GO"}
    assert summ["recommendation"]["position_group"] in {"DB", "DL", "OL", "WR", "TE"}
    # every audit CSV carries the provenance column
    for csvf in out.glob("*.csv"):
        head = csvf.read_text().splitlines()[0]
        assert head.split(",")[0] == "provenance", f"{csvf.name} missing provenance column"
    return PASS


def _hash_outputs() -> dict[str, str]:
    cfg = load_config()
    out = resolve(cfg, "outputs", "audit")
    h = {}
    for f in sorted(out.glob("*")):
        if f.is_file():
            h[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return h


def test_determinism() -> str:
    run = [sys.executable, str(REPO / "src" / "01_audit.py"), "--mode", "sample", "--force"]
    subprocess.run(run, cwd=REPO, check=True, capture_output=True)
    h1 = _hash_outputs()
    subprocess.run(run, cwd=REPO, check=True, capture_output=True)
    h2 = _hash_outputs()
    assert h1 == h2, "audit outputs are not deterministic across runs"
    return PASS


def main() -> int:
    tests = [test_parquet_schema, test_audit_invariants, test_determinism]
    rc = 0
    for t in tests:
        try:
            result = t()
            print(f"[{result}] {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            rc = 1
            print(f"[{FAIL}] {t.__name__}: {type(exc).__name__}: {exc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
