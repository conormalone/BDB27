#!/usr/bin/env python3
"""Hermetic regression test for the Phase 1 audit **full mode** (`src/01_audit.py --mode full`).

Reproduces the ``KeyError: 'game_tracking'`` regression. Full mode used to write the per-season
game-tracking parquets under keys ``game_tracking_0/1/2`` and **never** set ``tables['game_tracking']``,
so ``assert_schemas()`` (and items c/d/f/h/i via ``_game_effort_peaks``) crashed on the missing key.
The fix exposes a single normalised ``game_tracking`` entry that is the **union** of the per-season
parquets, so the single-key lookups work across all seasons.

Runnable without pytest (``python src/tests/test_audit_full_mode.py``) and also collected by pytest
(functions named ``test_*``). Failures are loud (AssertionError).

Hermetic by construction:
  * inputs are built ONLY from the committed synthetic fixtures (``fixtures/synthetic/binding/``);
  * nothing is read from ``data/raw`` and nothing is written under ``data/`` or ``fixtures/``
    (``tempfile`` dirs only; ``*.csv`` is gitignored anyway);
  * the fixture ``game_tracking.csv`` is partitioned into three per-season stubs and each stub gets one
    sentinel frame, so the **union** has a unique global min-x (season 1) and max-x (season 3) that no
    single stub reproduces — a single-file read therefore cannot satisfy the x-range assertion.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402

AUDIT = REPO / "src" / "01_audit.py"
FIX = REPO / "fixtures" / "synthetic" / "binding"
PENDING = "PENDING FULL RUN"

GT_HEADER = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis", "o", "dir", "event"]
# file order -> which nfl_id % 3 bucket lands in that season file (2023, 2024, 2025)
SEASONS = ["2023", "2024", "2025"]


# --------------------------------------------------------------------------- #
# input construction
# --------------------------------------------------------------------------- #
def _read_fixture_rows() -> list[list[str]]:
    with open(FIX / "game_tracking.csv", newline="", encoding="utf-8") as fh:
        rdr = csv.reader(fh)
        header = next(rdr)
        assert header == GT_HEADER, f"fixture game_tracking header changed: {header}"
        return [row for row in rdr if row]


def _sentinel_frame(game_id: int, play_id: int, nfl_id: int, x: float) -> list[str]:
    """A lone frame in its own (game, play, player) group (so it contributes no lagged effort)."""
    return [str(game_id), str(play_id), str(nfl_id), "2025-09-07T17:00:00.000",
            repr(float(x)), "10.0", "0.0", "0.0", "0.0", "0.0", "0.0", ""]


def _build_inputs(tmp: Path) -> tuple[Path, tuple[float, float]]:
    raw, parquet, outputs = tmp / "raw", tmp / "parquet", tmp / "outputs"
    for d in (raw, parquet, outputs):
        d.mkdir(parents=True, exist_ok=True)

    # non-game raw inputs straight from the committed fixture (tiny -> fast)
    for fn in ("combine_tracking.csv", "combine_results.csv", "player_play.csv", "games.csv"):
        shutil.copyfile(FIX / fn, raw / fn)

    rows = _read_fixture_rows()
    xs = [float(r[4]) for r in rows]
    true_min, true_max = min(xs), max(xs)

    buckets: dict[int, list[list[str]]] = {0: [], 1: [], 2: []}
    for r in rows:
        buckets[int(r[2]) % 3].append(r)

    # one sentinel per season file: unique global min (2023) / interior (2024) / unique global max (2025)
    sentinels = {
        0: _sentinel_frame(99990001, 9001, 9999001, true_min - 5.0),
        1: _sentinel_frame(99990002, 9002, 9999002, (true_min + true_max) / 2.0),
        2: _sentinel_frame(99990003, 9003, 9999003, true_max + 5.0),
    }
    for i, season in enumerate(SEASONS):
        body = buckets[i] + [sentinels[i]]
        with open(raw / f"game_tracking_{season}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(GT_HEADER)
            w.writerows(body)

    cfg = common.load_config()
    cfg.pop("_config_path", None)
    cfg["paths"]["raw"] = str(raw)
    cfg["paths"]["parquet"] = str(parquet)
    cfg["paths"]["outputs"] = str(outputs)
    cfg["logging"]["run_log"] = str(tmp / "run_log.csv")
    cfg_path = tmp / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return cfg_path, (true_min - 5.0, true_max + 5.0)


# --------------------------------------------------------------------------- #
# one cached subprocess run shared by the assertions
# --------------------------------------------------------------------------- #
_CACHE: dict = {}


def _full_run() -> dict:
    if not _CACHE:
        tmp = Path(tempfile.mkdtemp(prefix="bdb27_audit_full_"))
        cfg_path, truth = _build_inputs(tmp)
        cmd = [sys.executable, str(AUDIT), "--mode", "full", "--force", "--config", str(cfg_path)]
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        _CACHE.update(tmp=tmp, truth=truth, proc=proc, audit=tmp / "outputs" / "audit")
    return _CACHE


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# assertions
# --------------------------------------------------------------------------- #
def test_full_mode_exit_zero():
    """Pre-fix this fails: `assert_schemas` -> `KeyError: 'game_tracking'` (returncode 1)."""
    c = _full_run()
    assert c["proc"].returncode == 0, (
        "full-mode audit failed "
        f"(exit {c['proc'].returncode}):\n{c['proc'].stderr[-3000:]}")


def test_full_mode_outputs_and_provenance():
    c = _full_run()
    ad = c["audit"]
    dec_path = ad / "audit_decision.json"
    assert dec_path.exists(), "audit_decision.json not written"
    json.loads(dec_path.read_text(encoding="utf-8"))  # parses
    assert (ad / "SUMMARY.md").exists(), "SUMMARY.md not written"
    csvs = sorted(ad.glob("*.csv"))
    assert csvs, "no audit CSVs emitted"
    for f in csvs:
        first = f.read_text(encoding="utf-8").splitlines()[0].split(",")[0]
        assert first == "provenance", f"{f.name}: first column is {first!r}, not 'provenance'"


def test_full_mode_game_union_xrange():
    """The full game dataset x-range must be the union across ALL three season stubs.

    A single-file read reproduces only one end of the range, so this FAILS for any per-file read.
    """
    c = _full_run()
    rows = _rows(c["audit"] / "f_sampling_units.csv")
    game = [r for r in rows if str(r.get("dataset", "")).startswith("game_tracking")]
    assert game, f"no game dataset row in f_sampling_units.csv: {[r.get('dataset') for r in rows]}"
    g = game[0]

    raw = c["tmp"] / "raw"
    files = [str(raw / f"game_tracking_{s}.csv") for s in SEASONS]
    con = duckdb.connect()
    try:
        con.execute("PRAGMA threads=1")
        got = con.execute(
            f"SELECT min(x), max(x) FROM read_csv_auto({files!r})").fetchone()
    finally:
        con.close()
    truth = c["truth"]
    assert (float(g["x_min"]), float(g["x_max"])) == (float(got[0]), float(got[1])), (
        f"audit game x-range {(g['x_min'], g['x_max'])} != union truth {got}")
    assert (float(got[0]), float(got[1])) == (truth[0], truth[1]), (
        f"stub inputs drifted from expected planted range: {got} vs {truth}")


def test_full_mode_game_items_not_pending():
    """Full mode must actually run the game-side path (items h/i), not emit PENDING placeholders."""
    c = _full_run()
    for name in ("h_exceedance.csv", "i_scatter.csv"):
        p = c["audit"] / name
        assert p.exists(), f"{name} not written"
        text = p.read_text(encoding="utf-8")
        assert PENDING not in text, f"{name} still contains the {PENDING!r} placeholder"
        rows = _rows(p)
        assert rows, f"{name} has no data rows"


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_full_mode_exit_zero, test_full_mode_outputs_and_provenance,
             test_full_mode_game_union_xrange, test_full_mode_game_items_not_pending]
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
