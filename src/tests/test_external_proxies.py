#!/usr/bin/env python3
"""Behaviour tests for Phase 0.5 — pre-Combine expectation proxies (`src/00b_external_proxies.py`).

Runnable **without pytest** (`python src/tests/test_external_proxies.py`) and collected by pytest
(functions named ``test_*``). Covers:
  * the committed synthetic GTM fixture end-to-end on the identical code path: ingest → February
    filter → EDP aggregation → fuzzy match → strict parquet (EXACTLY the spec schema) + unmatched CSV;
  * the **February filter** actually removes non-February (Jan/Mar) snapshots and so controls the EDP;
  * **fuzzy matching** resolves near-miss spellings one-to-one and leaves truly-unmatched prospects out;
  * the **spec match-rate gate** halts (+ writes `notes/blockers.md`) when the drafted WR/TE match rate
    is not above `external.min_match_rate`;
  * the **strict schema assertion** rejects any wrong column set/order;
  * the **Phase-4 gate** (existence + schema) blocks/passes correctly and never gates `--mode sample`;
  * `--mode full` with **no cached GTM export** (GTM unobtainable) stops with a data-availability blocker
    (never fabricates/substitutes a source);
  * sample-mode provenance labelling, determinism, and checkpoint/skip.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402
import gate  # noqa: E402

SCRIPT = REPO / "src" / "00b_external_proxies.py"
FIXTURE_DIR = REPO / "fixtures" / "synthetic" / "gtm"
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"
SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"
SCHEMA = ["nflId", "Pre_Combine_EDP", "match_confidence_score"]


def _load_mod():
    spec = importlib.util.spec_from_file_location("_ext_prox", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXT = _load_mod()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(tmp: Path, notes: Path | None = None, fixtures: Path | None = None, raw: Path | None = None,
         outputs: Path | None = None, **overrides) -> Path:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(outputs or (tmp / "out"))
    cfg["paths"]["notes"] = str(notes or (tmp / "notes"))
    if fixtures is not None:
        cfg["paths"]["fixtures"] = str(fixtures)
    if raw is not None:
        cfg["paths"]["raw"] = str(raw)
    for dotted, val in overrides.items():
        node = cfg
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = val
    p = tmp / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _run(args: list[str], cfg: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)] + args + ["--config", str(cfg)],
                          cwd=str(REPO), capture_output=True, text=True)


def _frozen_prereg(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "prereg.md").write_text(
        "# Pre-registration — BDB27 ceiling-binding task (**FROZEN**)\n\n"
        "> **Status: FROZEN** (2026-10-12). Values fixed before the full runs.\n\n"
        "All values are frozen; no residual placeholders.\n", encoding="utf-8")
    return d


def _audit_go(outputs: Path) -> None:
    d = outputs / "audit"
    d.mkdir(parents=True, exist_ok=True)
    (d / "audit_decision.json").write_text(
        json.dumps({"decision": "GO — matched WR OK, route snaps OK, fields present, ICC OK"}))


def _schema_of(path: Path) -> list[str]:
    import pyarrow.parquet as pq
    return list(pq.read_schema(path).names)


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# end-to-end on the committed fixture
# --------------------------------------------------------------------------- #
def test_synthetic_pipeline_end_to_end():
    tmp = Path(tempfile.mkdtemp())
    cfg = _cfg(tmp, fixtures=REPO / "fixtures")
    r = _run(["--synthetic", "--force"], cfg)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2000:]}"
    pp = tmp / "out" / "external" / "synthetic" / "pre_combine_expectation.parquet"
    assert pp.exists()
    assert _schema_of(pp) == SCHEMA, _schema_of(pp)
    import pyarrow.parquet as pq
    t = pq.read_table(pp)
    assert [str(f.type) for f in t.schema] == ["int64", "double", "double"]
    assert t.num_rows == 12, t.num_rows
    assert all(0.0 < c <= 1.0 for c in t.column("match_confidence_score").to_pylist())
    assert pp.with_suffix(pp.suffix + ".provenance.txt").read_text().strip() == SYNTH_PROV
    up = tmp / "out" / "external" / "synthetic" / "unmatched_external_ids.csv"
    rows = _rows(up)
    assert rows and rows[0]["provenance"] == SYNTH_PROV and list(rows[0])[0] == "provenance"
    assert len(rows) == 3  # the three deliberately-unmatched GTM prospects


# --------------------------------------------------------------------------- #
# February filter controls the EDP
# --------------------------------------------------------------------------- #
def test_february_filter_controls_edp():
    con = common.connect(1)
    rows = EXT.ingest_gtm_feb(con, [FIXTURE_DIR / "gtm_mock_history.csv"], common.load_config())
    by_name = {r["name"]: r for r in rows}
    # an exact-name prospect (index >= n_near_miss) — recompute the median independently in pure python
    raw = _rows(FIXTURE_DIR / "gtm_mock_history.csv")
    target = "Malik Okafor"
    feb = sorted(int(r["projected_pick"]) for r in raw
                 if r["prospect_name"] == target and r["snapshot_date"].split("-")[1] == "02")
    import statistics
    assert by_name[target]["edp"] == float(statistics.median(feb)), (by_name[target], feb)
    assert by_name[target]["n_snaps"] == len(feb) == 6  # Jan + Mar snapshots excluded
    # the excluded March snapshot (base-15) would LOWER the median if it leaked in
    leaked = sorted(feb + [max(1, min(int(r["projected_pick"]) for r in raw if r["prospect_name"] == target
                                       and r["snapshot_date"].split("-")[1] == "03"))])
    assert float(statistics.median(leaked)) != by_name[target]["edp"]


# --------------------------------------------------------------------------- #
# fuzzy matching: near-miss resolves, unknown stays unmatched, one-to-one
# --------------------------------------------------------------------------- #
def test_fuzzy_matching_near_miss_and_unmatched():
    cfg = common.load_config()
    con = common.connect(1)
    players = EXT.load_players(con, cfg, synthetic=True)
    gtm = EXT.ingest_gtm_feb(con, [FIXTURE_DIR / "gtm_mock_history.csv"], cfg)
    matched, unmatched = EXT.fuzzy_match(players, gtm, cfg)
    ids = [m["nflId"] for m in matched]
    assert len(ids) == len(set(ids)) == 12, "matching must be one-to-one"
    # the two SEEDED near-miss prospects (nfl_ids 900001/900002) still match, below a perfect score
    near = {m["nflId"]: m["match_confidence_score"] for m in matched}
    assert near[900001] < 1.0 and near[900002] < 1.0, near
    assert len(unmatched) == 3 and all("Nowhere" in u["college"] for u in unmatched)
    rate, hits, denom = EXT.match_rate(players, matched)
    assert rate == 1.0 and (hits, denom) == (9, 9), (rate, hits, denom)


# --------------------------------------------------------------------------- #
# spec match-rate gate: halt + blocker
# --------------------------------------------------------------------------- #
def test_match_rate_gate_halts_and_writes_blocker():
    tmp = Path(tempfile.mkdtemp())
    # a fixture whose GTM prospects resemble NOBODY in the players table
    fix = tmp / "fixtures" / "gtm"
    fix.mkdir(parents=True)
    (fix / "kaggle_players.csv").write_text(
        "nfl_id,display_name,draft_year,nfl_position,college_name,draft_round,draft_overall_pick\n"
        "800001,Jordan Blake,2023,WR,Alabama,1,5\n"
        "800002,Xavier Whitfield,2023,TE,Ohio State,2,40\n")
    (fix / "gtm_mock_history.csv").write_text(
        "mock_id,mock_source,snapshot_date,draft_year,prospect_name,position,college,projected_pick\n"
        "G1,A,2023-02-05,2023,Zzzz Qqqq,WR,Nowhere State,10\n"
        "G2,A,2023-02-06,2023,Zzzz Qqqq,WR,Nowhere State,12\n"
        "G3,A,2023-02-05,2023,Wwww Eeee,TE,Nowhere State,99\n")
    notes = tmp / "notes"
    cfg = _cfg(tmp, notes=notes, fixtures=tmp / "fixtures", raw=tmp / "raw",
               **{"external.synthetic.fixture_dir": "gtm"})
    r = _run(["--synthetic", "--force"], cfg)
    assert r.returncode == 2, f"low match rate not halted:\n{r.stdout}\n{r.stderr}"
    text = (notes / "blockers.md").read_text()
    assert "00b_external_proxies BLOCKER" in text and "match rate" in text, text


# --------------------------------------------------------------------------- #
# strict schema assertion
# --------------------------------------------------------------------------- #
def test_proxy_schema_assertion():
    EXT.assert_proxy_schema(SCHEMA)  # exact -> ok
    for bad in (["nfl_id", "Pre_Combine_EDP", "match_confidence_score"],
                ["nflId", "Pre_Combine_EDP"],
                ["nflId", "match_confidence_score", "Pre_Combine_EDP"]):
        try:
            EXT.assert_proxy_schema(bad)
        except AssertionError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"schema assertion accepted {bad}")
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "p.parquet"
    EXT.write_proxy_parquet(p, [{"nflId": 1, "Pre_Combine_EDP": 2.5, "match_confidence_score": 0.9}])
    assert _schema_of(p) == SCHEMA


# --------------------------------------------------------------------------- #
# Phase-4 external-proxy gate
# --------------------------------------------------------------------------- #
def test_gate_requires_external_proxy():
    tmp = Path(tempfile.mkdtemp())
    notes = _frozen_prereg(tmp / "notes")
    out = tmp / "out"
    _audit_go(out)
    cfg = copy.deepcopy(common.load_config())
    cfg["external"]["enabled"] = True          # exercise the gate itself (config default is retired)
    cfg["external"]["require_for_phase4"] = True
    cfg["paths"]["notes"] = str(notes)
    cfg["paths"]["outputs"] = str(out)

    # 1) missing proxy -> blocked, with a clear reason
    rep = gate.evaluate(cfg, "full", "04_models", requires_game_side=True, requires_external_proxy=True)
    assert rep["external_proxy_gate_ok"] is False and not rep["passed"]
    assert any("external proxy missing" in r for r in rep["reasons"]), rep["reasons"]

    # 2) valid proxy -> passes (frozen prereg + audit GO already satisfy the other layers)
    EXT.write_proxy_parquet(out / cfg["external"]["proxy_file"],
                            [{"nflId": 1, "Pre_Combine_EDP": 5.0, "match_confidence_score": 0.95}])
    rep = gate.evaluate(cfg, "full", "04_models", requires_game_side=True, requires_external_proxy=True)
    assert rep["external_proxy_gate_ok"] is True and rep["passed"], rep["reasons"]

    # 3) wrong schema -> blocked
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"nfl_id": [1]}), out / cfg["external"]["proxy_file"])
    rep = gate.evaluate(cfg, "full", "04_models", requires_game_side=True, requires_external_proxy=True)
    assert rep["external_proxy_gate_ok"] is False and any("schema mismatch" in r for r in rep["reasons"])

    # 4) config kill-switch -> no-op
    cfg2 = copy.deepcopy(cfg)
    cfg2["external"]["require_for_phase4"] = False
    ok, _ = gate.external_proxy_gate_ok(cfg2)
    assert ok

    # 5) sample mode is never gated (even with a missing proxy)
    rep = gate.evaluate(cfg, "sample", "04_models", requires_external_proxy=True)
    assert rep["passed"]


def test_models_not_gated_on_proxy():
    """D43: 04_models no longer requires the retired GTM proxy; the config knob is off."""
    src = (REPO / "src" / "04_models.py").read_text(encoding="utf-8")
    assert "requires_external_proxy" not in src
    ext = common.load_config()["external"]
    assert ext["enabled"] is False and ext["require_for_phase4"] is False


# --------------------------------------------------------------------------- #
# disabled external acquisition (D43) -> no-op, NO network access at all
# --------------------------------------------------------------------------- #
def test_external_disabled_no_network_exit_zero():
    tmp = Path(tempfile.mkdtemp())
    notes = _frozen_prereg(tmp / "notes")
    out = tmp / "out"
    raw = tmp / "raw"
    raw.mkdir()
    # config default is enabled:false; keep endpoints in place so a live attempt would be possible
    cfg = _cfg(tmp, notes=notes, raw=raw, outputs=out, **{"external.enabled": False})

    # Deny every socket in the child interpreter: any network access becomes a hard failure.
    blocker = tmp / "blocknet"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(
        "import socket\n"
        "def _deny(*a, **k):\n"
        "    raise RuntimeError('network access attempted while external.enabled=false')\n"
        "socket.socket.connect = _deny\n"
        "socket.socket.connect_ex = _deny\n"
        "socket.create_connection = _deny\n"
        "socket.getaddrinfo = _deny\n"
        "socket.gethostbyname = _deny\n", encoding="utf-8")
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(blocker) + (os.pathsep + env["PYTHONPATH"]
                                        if env.get("PYTHONPATH") else "")
    r = subprocess.run([sys.executable, str(SCRIPT), "--mode", "full", "--force",
                        "--config", str(cfg)], cwd=str(REPO), capture_output=True, text=True,
                       env=env)
    assert r.returncode == 0, f"disabled 00b did not exit 0:\n{r.stdout}\n{r.stderr[-2000:]}"
    combined = r.stdout + r.stderr
    assert "external proxy acquisition disabled (board export in use)" in combined, combined[-800:]
    assert "network access attempted" not in combined, combined[-800:]
    # nothing written, and no blocker recorded for a disabled stage
    assert not (out / "pre_combine_expectation.parquet").exists()
    assert not (notes / "blockers.md").exists() or "UNAVAILABLE" not in (
        notes / "blockers.md").read_text()


# --------------------------------------------------------------------------- #
# full mode with no cached GTM -> data-availability blocker (no fabrication)
# --------------------------------------------------------------------------- #
def test_full_mode_blocked_without_gtm_cache():
    tmp = Path(tempfile.mkdtemp())
    notes = _frozen_prereg(tmp / "notes")
    out = tmp / "out"
    _audit_go(out)
    raw = tmp / "raw"
    raw.mkdir()
    cfg = _cfg(tmp, notes=notes, raw=raw, outputs=out, **{
        "external.enabled": True,             # revive the retired fallback for this blocker test
        "external.require_for_phase4": True,
        "external.gtm.endpoints": [],         # no live calls in the test -> no cached raw either
    })
    r = _run(["--mode", "full", "--force"], cfg)
    assert r.returncode == 2, f"full run not blocked:\n{r.stdout}\n{r.stderr}"
    text = (notes / "blockers.md").read_text()
    assert "GTM raw export UNAVAILABLE" in text, text
    # nothing fabricated: no proxy parquet written on the canonical full path
    assert not (out / "pre_combine_expectation.parquet").exists()


# --------------------------------------------------------------------------- #
# sample-mode provenance labelling + determinism + checkpoint
# --------------------------------------------------------------------------- #
def test_sample_mode_labelled_and_deterministic():
    tmp = Path(tempfile.mkdtemp())
    # sample mode still exercises the LEGACY fixture fallback -> opt the retired pipeline back in
    cfg = _cfg(tmp, fixtures=REPO / "fixtures", raw=tmp / "raw", **{"external.enabled": True})
    r = _run(["--mode", "sample", "--force"], cfg)
    assert r.returncode == 0, r.stderr[-1500:]
    pp = tmp / "out" / "external" / "sample" / "pre_combine_expectation.parquet"
    assert pp.with_suffix(pp.suffix + ".provenance.txt").read_text().strip() == SAMPLE_PROV
    import pyarrow.parquet as pq
    a = pq.read_table(pp).to_pydict()
    r2 = _run(["--mode", "sample", "--force"], cfg)
    assert r2.returncode == 0
    b = pq.read_table(pp).to_pydict()
    assert a == b, "sample run is not deterministic"


def test_checkpoint_skips_finished_run():
    tmp = Path(tempfile.mkdtemp())
    cfg = _cfg(tmp, fixtures=REPO / "fixtures")
    assert _run(["--synthetic", "--force"], cfg).returncode == 0
    r = _run(["--synthetic"], cfg)  # no --force
    assert r.returncode == 0 and "checkpoint hit" in r.stderr, r.stderr[-500:]


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_synthetic_pipeline_end_to_end, test_february_filter_controls_edp,
             test_fuzzy_matching_near_miss_and_unmatched, test_match_rate_gate_halts_and_writes_blocker,
             test_proxy_schema_assertion, test_gate_requires_external_proxy,
             test_models_not_gated_on_proxy, test_external_disabled_no_network_exit_zero,
             test_full_mode_blocked_without_gtm_cache,
             test_sample_mode_labelled_and_deterministic, test_checkpoint_skips_finished_run]
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
