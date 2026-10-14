#!/usr/bin/env python3
"""Hermetic regression test for the game-side **FULL-mode** pipeline, end to end.

Background. The audit ``--mode full`` fix (commit ``21bc161``) was the *first* game-side bug that
only manifests in full mode; ``03_game_demand --mode full`` then crashed with

    TypeError: resolve() takes from 2 to 3 positional arguments but 4 were given

from ``ceilings_path()`` — ``common.resolve`` accepts at most one ``extra`` component, but full
mode joined a subdir **and** a file. The sample/synthetic paths never call ``ceilings_path``, so
the whole ``--mode full`` game chain had never been executed end to end.

This test makes that class of failure loud:

  * a **static guard** — no ``common.resolve(...)`` call anywhere in ``src/`` may pass more than
    three positional arguments (the exact shape of the crash above);
  * a **unit check** — ``ceilings_path`` resolves to ``outputs/<source_subdir>/<file>`` from config
    (and does not raise) — RED before the fix (``TypeError``), GREEN after;
  * a **full-mode end-to-end smoke run** on tiny per-season stubs, driving the WHOLE game-side
    chain in ``--mode full``::

        01_audit → 00_thresholds → 03_game_demand → 04_models → 05_validate

Hermetic by construction:
  * inputs are built ONLY from the committed synthetic fixtures (``fixtures/synthetic/binding/``);
  * nothing is read from ``data/raw`` and nothing is written under ``data/`` or ``fixtures/``
    (``tempfile`` dirs only; ``*.csv`` is gitignored anyway);
  * the Phase-2 ceiling table that ``03`` legitimately consumes in full mode is derived from the
    fixture Combine tracking with the shared Phase-2 fitter (``game_features.combine_ceilings``);
  * the bootstrap/permutation rep counts are reduced in the test config purely for runtime — the
    full code path is unchanged.

Runnable without pytest (``python src/tests/test_full_mode_e2e.py``) and collected by pytest
(functions named ``test_*``). Failures are loud (AssertionError).
"""
from __future__ import annotations

import ast
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
SRC = REPO / "src"
sys.path.insert(0, str(SRC))
import common  # noqa: E402
import game_features as gf  # noqa: E402

FIX = REPO / "fixtures" / "synthetic" / "binding"
STAGES = ["01_audit", "00_thresholds", "03_game_demand", "04_models", "05_validate"]
GT_HEADER = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis", "o", "dir", "event"]
SEASONS = ["2023", "2024", "2025"]
# fields whose emitted CSV must lead with the house `provenance` column
GAME_CSVS = ["efforts.csv", "top_k_efforts.csv", "player_summary.csv", "thresholds_applied.csv"]
MODEL_CSVS = ["model_coefficients.csv", "exceedance_utilization.csv", "fallbacks_fired.csv"]
VALID_CSVS = ["cv_results.csv", "permutation.csv", "measurement_error.csv", "sensitivity.csv"]


# --------------------------------------------------------------------------- #
# (A) static arity guard — the exact shape of the `ceilings_path` crash
# --------------------------------------------------------------------------- #
def _common_resolve_arities(py: Path) -> list[tuple[int, int]]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    hits: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "resolve" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "common"):
            hits.append((node.lineno, len(node.args)))
    return hits


def test_no_oversupplied_resolve_calls():
    """RED before the fix: src/03_game_demand.py:121 called common.resolve with 4 positional args."""
    offenders: list[str] = []
    for py in sorted(SRC.glob("*.py")):
        for lineno, nargs in _common_resolve_arities(py):
            if nargs > 3:  # common.resolve(cfg, key, extra=None) accepts at most 3
                offenders.append(f"{py.name}:{lineno} passed {nargs}")
    assert not offenders, (
        "common.resolve() takes at most 3 positional args; offenders: " + "; ".join(offenders))


# --------------------------------------------------------------------------- #
# (B) unit check for the fixed helper
# --------------------------------------------------------------------------- #
def test_ceilings_path_matches_config():
    """`ceilings_path` must join `game.ceiling.source_subdir` + `game.ceiling.file` under outputs."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("bdb27_03_game_demand", SRC / "03_game_demand.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # RED before the fix: import is fine, but calls raise TypeError

    cfg = common.load_config()
    c = cfg["game"]["ceiling"]
    got = mod.ceilings_path(cfg)  # RED before the fix: TypeError (4 positional args)
    want = common.resolve(cfg, "outputs") / c["source_subdir"] / c["file"]
    assert got == want, f"ceilings_path() = {got}, expected {want}"
    assert got.name == c["file"] and got.parent.name == c["source_subdir"], (
        f"ceilings_path() lost a component: {got}")


# --------------------------------------------------------------------------- #
# input construction (hermetic — committed fixtures only)
# --------------------------------------------------------------------------- #
def _read_fixture_rows() -> list[list[str]]:
    with open(FIX / "game_tracking.csv", newline="", encoding="utf-8") as fh:
        rdr = csv.reader(fh)
        header = next(rdr)
        assert header == GT_HEADER, f"fixture game_tracking header changed: {header}"
        return [row for row in rdr if row]


# full-run player_play context fields the committed fixture omits (the real full file has them);
# the audit go/no-go treats an absent required field as NO-GO, so a *full-mode* input must be
# schema-complete. Filled here with schema-typed values in the temp copy only.
_PP_EXTRA_FIELDS = {"cushion": "5.0", "receiver_alignment": "0.0", "player_get_off": "0.0"}


def _complete_player_play(raw: Path) -> None:
    """Add the missing full-run context columns to the temp player_play copy (fixture untouched)."""
    p = raw / "player_play.csv"
    with open(p, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        fields = list(rdr.fieldnames)
        rows = list(rdr)
    for k in _PP_EXTRA_FIELDS:
        if k not in fields:
            fields.append(k)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            for k, v in _PP_EXTRA_FIELDS.items():
                if not r.get(k):
                    r[k] = v
            w.writerow(r)


def _write_ceiling_table(raw: Path, out_env: Path, cfg: dict) -> None:
    """Derive the Phase-2 ceiling table from the fixture Combine tracking (shared Phase-2 fitter)."""
    out_env.mkdir(parents=True, exist_ok=True)
    parquet = raw.parent / "parquet"
    parquet.mkdir(parents=True, exist_ok=True)
    ct_pq = parquet / "combine_tracking.parquet"
    con = duckdb.connect()
    try:
        con.execute("PRAGMA threads=1")
        common.to_parquet(con, raw / "combine_tracking.csv", ct_pq, force=True)
        ceilings = gf.combine_ceilings(con, [ct_pq], cfg)
    finally:
        con.close()
    rows = [dict(provenance="PHASE2 FIXTURE", nfl_id=nid,
                 env_vmax=round(d["vmax"], 6), env_a0=round(d["a0"], 6))
            for nid, d in sorted(ceilings.items())]
    common.write_csv(out_env / "per_player_envelope.csv", rows,
                     fieldnames=["provenance", "nfl_id", "env_vmax", "env_a0"])


def _build_inputs(tmp: Path) -> Path:
    raw, parquet, outputs = tmp / "raw", tmp / "parquet", tmp / "outputs"
    for d in (raw, parquet, outputs):
        d.mkdir(parents=True, exist_ok=True)

    # non-game raw inputs straight from the committed fixture (tiny -> fast)
    for fn in ("combine_tracking.csv", "combine_results.csv", "players.csv",
               "player_play.csv", "games.csv"):
        shutil.copyfile(FIX / fn, raw / fn)
    _complete_player_play(raw)

    # partition the fixture tracking into three per-season stubs (by nfl_id % 3); the union
    # covers every player, so no single-season read can reproduce the chain inputs
    rows = _read_fixture_rows()
    buckets: dict[int, list[list[str]]] = {0: [], 1: [], 2: []}
    for r in rows:
        buckets[int(r[2]) % 3].append(r)
    for i, season in enumerate(SEASONS):
        with open(raw / f"game_tracking_{season}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(GT_HEADER)
            w.writerows(buckets[i])

    cfg = common.load_config()
    cfg.pop("_config_path", None)
    cfg["paths"]["raw"] = str(raw)
    cfg["paths"]["parquet"] = str(parquet)
    cfg["paths"]["outputs"] = str(outputs)
    cfg["logging"]["run_log"] = str(tmp / "run_log.csv")
    # smoke test: fewer bootstrap / permutation reps keeps the run fast; the code path is identical
    cfg["models"]["bootstrap_reps"] = 25
    cfg["validate"]["perm_reps"] = 25
    cfg_path = tmp / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    _write_ceiling_table(raw, outputs / "envelope", cfg)
    return cfg_path


# --------------------------------------------------------------------------- #
# one cached end-to-end run shared by the assertions
# --------------------------------------------------------------------------- #
_CACHE: dict = {}


def _pipeline() -> dict:
    if not _CACHE:
        tmp = Path(tempfile.mkdtemp(prefix="bdb27_full_e2e_"))
        cfg_path = _build_inputs(tmp)
        procs: dict[str, subprocess.CompletedProcess] = {}
        for stage in STAGES:
            proc = subprocess.run(
                [sys.executable, str(SRC / f"{stage}.py"), "--mode", "full", "--force",
                 "--config", str(cfg_path)],
                cwd=str(REPO), capture_output=True, text=True)
            procs[stage] = proc
            if proc.returncode != 0:
                break
        _CACHE.update(tmp=tmp, cfg_path=cfg_path, procs=procs, outputs=tmp / "outputs")
    return _CACHE


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _assert_provenance_first(path: Path) -> None:
    assert path.exists(), f"{path} not written"
    with open(path, newline="", encoding="utf-8") as fh:
        first = fh.readline().split(",")[0]
    assert first == "provenance", f"{path.name}: first column is {first!r}, not 'provenance'"


# --------------------------------------------------------------------------- #
# (C) full-mode end-to-end smoke run
# --------------------------------------------------------------------------- #
def test_full_mode_chain_exit_zero():
    c = _pipeline()
    failed = {s: p for s, p in c["procs"].items() if p.returncode != 0}
    assert not failed, "; ".join(
        f"{s} exit {p.returncode}:\n{p.stderr[-2500:]}" for s, p in failed.items())
    assert set(c["procs"]) == set(STAGES), (
        f"only ran {sorted(c['procs'])}; expected all of {STAGES}")


def test_full_mode_thresholds_are_full():
    c = _pipeline()
    thr = json.loads((c["outputs"] / "thresholds.json").read_text(encoding="utf-8"))
    assert thr.get("provenance") == "FULL", f"thresholds provenance is {thr.get('provenance')!r}"


def test_full_mode_outputs_and_provenance():
    c = _pipeline()
    out = c["outputs"]
    for name in GAME_CSVS:
        _assert_provenance_first(out / "game" / name)
    for name in MODEL_CSVS:
        _assert_provenance_first(out / "models" / name)
    for name in VALID_CSVS:
        _assert_provenance_first(out / "validate" / name)
    for f in sorted((out / "audit").glob("*.csv")):
        _assert_provenance_first(f)
    # SUMMARY documents for the game side exist and are non-empty
    for sub in ("game", "models", "validate"):
        md = out / sub / "SUMMARY.md"
        assert md.exists() and md.stat().st_size > 0, f"{sub}/SUMMARY.md missing/empty"


def test_full_mode_ceilings_applied():
    """The Phase-2 ceiling must actually reach the efforts table (the `ceilings_path` fix)."""
    c = _pipeline()
    efforts = _rows(c["outputs"] / "game" / "efforts.csv")
    assert efforts, "efforts.csv has no data rows (full game side produced nothing)"
    good = 0
    for r in efforts:
        try:
            ceiling = float(r["ceiling_speed_yds"])
            frac = float(r["max_over_ceiling"])
        except (KeyError, TypeError, ValueError):
            continue
        assert ceiling > 0, f"ceiling not loaded for nfl_id={r['nfl_id']}: {r.get('ceiling_speed_yds')}"
        assert frac == frac and frac > 0, f"non-finite envelope fraction for nfl_id={r['nfl_id']}"
        good += 1
    assert good == len(efforts), f"only {good}/{len(efforts)} efforts had a usable ceiling"

    # and the models/validation stages consumed them
    assert _rows(c["outputs"] / "models" / "exceedance_utilization.csv"), "no model rows"
    assert _rows(c["outputs"] / "validate" / "cv_results.csv"), "no CV rows"


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_no_oversupplied_resolve_calls, test_ceilings_path_matches_config,
             test_full_mode_chain_exit_zero, test_full_mode_thresholds_are_full,
             test_full_mode_outputs_and_provenance, test_full_mode_ceilings_applied]
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
