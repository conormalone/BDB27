#!/usr/bin/env python3
"""Behaviour tests for the Phase-4 security stage — BDB27 stakes task.

Covers ``src/06_security.py`` (security exposure table + Analysis A), the shared board/exposure logic
``src/security_features.py``, and the synthetic security fixture ``src/synthetic_security_fixture.py``.

Runnable **without pytest** (``python src/tests/test_security.py``) and collected by pytest
(functions named ``test_*``). Behaviours asserted:

  * **schema** — the exposure table has EXACTLY ``security.schema`` columns/order and the pinned dtypes;
    ``assert_exposure_schema`` fails loudly on any mismatch; units sanity (log(EDP) = ln(EDP), share in
    [0,1], drill count in 0..3);
  * **pre-Combine-only exposures** — the 1-week-pre primary is used **strictly before the player's own
    testing day**; ``Combine Week`` and the post-Combine snapshots are **never** exposures (used only as
    outcomes); players with no 1-week-pre snapshot are flagged and dropped from Analysis A;
  * **synthetic recovery** — the planted speed × EDP effect is recovered (negative speed response,
    stronger for at-risk players) and the null case does **not** recover it (null-size ≈ nominal);
  * **edge cases** — players absent from the board, no 1-week-pre rows, duplicate snapshot rows, blank
    ``nfl_id``;
  * **determinism** — same seed → byte-identical fixture and identical Analysis A;
  * **board integrity** — sha256 / row-count / permission checks in ``verify_board``;
  * **sign convention** — ``edp_change`` = post − pre (positive = fell / less secure), so a rewarded
    speed effect is **negative**;
  * the single-run CLI stage — provenance labelling + checkpoint/skip.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402
import security_features as sf  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SEC = _load("_sec06", REPO / "src" / "06_security.py")
SSF = _load("_secfix", REPO / "src" / "synthetic_security_fixture.py")

SCRIPT = REPO / "src" / "06_security.py"
SCHEMA = ["nfl_id", "draft_year", "position", "position_group", "edp_pre", "log_edp_pre", "edp_trend",
          "consensus_share", "n_snapshots", "has_primary", "has_trend", "participation_pattern",
          "n_drills"]
DTYPES = {"nfl_id": "int64", "draft_year": "int32", "position": "string", "position_group": "string",
          "edp_pre": "double", "log_edp_pre": "double", "edp_trend": "double", "consensus_share": "double",
          "n_snapshots": "int32", "has_primary": "bool", "has_trend": "bool",
          "participation_pattern": "string", "n_drills": "int32"}
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(tmp: Path, fixtures: Path | None = None, raw: Path | None = None,
         outputs: Path | None = None, **overrides) -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(outputs or (tmp / "out"))
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
    cfg["_config_path"] = str(tmp / "config.yaml")
    return cfg


def _write_cfg(tmp: Path, cfg: dict) -> Path:
    p = tmp / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({k: v for k, v in cfg.items() if k != "_config_path"},
                                sort_keys=False), encoding="utf-8")
    return p


def _fixture_root(tmp: Path, tag: str = "") -> Path:
    """A fresh fixtures root under ``tmp`` (build_fixture writes under synthetic/security/<regime>)."""
    root = tmp / (f"fixtures{tag}") if tag else (tmp / "fixtures")
    return root


def _build(tmp: Path, regime: str, seed: int, n: int = 60, tag: str = "") -> Path:
    root = _fixture_root(tmp, tag)
    SSF.build_fixture(out_dir=root / "synthetic" / "security" / regime, seed=seed, n_players=n,
                      years=[2023, 2024, 2025], planted=(regime == "planted"))
    return root


def _analysis(tmp: Path, regime: str, seed: int, cfg: dict | None = None):
    """Build the fixture + run Analysis A on the identical code path; return (summary, parts)."""
    root = _build(tmp, regime, seed)
    cfg = cfg or _cfg(tmp, fixtures=root)
    con = common.connect(1)
    try:
        rows, testing, drills, meta = SEC.load_board_con(con, cfg, True, regime, common.get_logger())
        env = SEC.load_envelope(con, cfg, True, regime)
        a = SEC.analysis_a(rows, testing, env, cfg)
    finally:
        con.close()
    return a, dict(rows=rows, testing=testing, drills=drills, meta=meta)


def _run(args: list[str], cfg: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)] + args + ["--config", str(cfg)],
                          cwd=str(REPO), capture_output=True, text=True)


def _pq_schema(path: Path):
    import pyarrow.parquet as pq
    s = pq.read_schema(path)
    return list(s.names), {f.name: str(f.type) for f in s}


def _board_rows(rows: list[dict]) -> list[dict]:
    """Build board rows from compact tuples (nfl_id, year, pos, note, edp, iso_date)."""
    out = []
    for nid, year, pos, note, edp, iso in rows:
        out.append(dict(draft_year=year, name=f"P{nid}", position=pos, school="X", type="All",
                        nfl_id=nid, n_total=100, n=50, edp=edp, edp_rank=1, pos_rank=f"{pos}1",
                        snapshot_date=date.fromisoformat(iso), snapshot_note=note))
    return out


# --------------------------------------------------------------------------- #
# schema / dtypes / units
# --------------------------------------------------------------------------- #
def test_exposure_schema_and_dtypes():
    tmp = Path(tempfile.mkdtemp())
    root = _build(tmp, "planted", common.load_config()["seed"])
    cfg = _cfg(tmp, fixtures=root)
    assert list(cfg["security"]["schema"]) == SCHEMA  # config schema is the spec schema
    con = common.connect(1)
    rows, testing, drills, _ = SEC.load_board_con(con, cfg, True, "planted", common.get_logger())
    exp = sf.build_exposures(rows, testing, drills, cfg)
    pq = tmp / "exp.parquet"
    sf.write_exposure_parquet(pq, exp, cfg)
    names, types = _pq_schema(pq)
    assert names == SCHEMA, names
    assert types == DTYPES, (types, DTYPES)
    assert len(exp) == len(sf.board_players(rows))
    for r in exp:
        assert list(r.keys()) == SCHEMA, list(r.keys())
        # units / ranges
        if r["edp_pre"] is not None:
            assert r["edp_pre"] > 0
            assert abs(r["log_edp_pre"] - math.log(r["edp_pre"])) < 1e-9
        if r["consensus_share"] is not None:
            assert 0.0 <= r["consensus_share"] <= 1.0
        assert r["n_drills"] in (0, 1, 2, 3), r["n_drills"]
        assert r["position_group"] in ("WR", "TE", "DB", "DL", "OL", "LB", "QB", "RB", "ST", "OTHER")


def test_exposure_schema_assertion_fails_loudly():
    cfg = common.load_config()
    sf.assert_exposure_schema(SCHEMA, cfg)  # exact -> ok
    bad = [
        SCHEMA[:-1],                                                     # missing column
        SCHEMA + ["extra"],                                              # extra column
        ["log_edp_pre"] + SCHEMA[1:],                                    # wrong name/order (first col)
        SCHEMA[::-1],                                                    # reversed order
    ]
    for b in bad:
        try:
            sf.assert_exposure_schema(b, cfg)
        except AssertionError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"schema assertion accepted {b}")


# --------------------------------------------------------------------------- #
# pre-Combine-only exposures + strict before-testing rule
# --------------------------------------------------------------------------- #
def test_strict_pre_testing_rule_and_no_post_exposure():
    cfg = common.load_config()
    b = cfg["external"]["board"]
    rows = _board_rows([
        # primary snapshot BEFORE testing -> counts
        (1, 2023, "WR", b["snapshot_primary"], 20.0, "2023-02-20"),
        # primary snapshot AFTER testing -> must NOT count as a pre-Combine exposure
        (2, 2023, "WR", b["snapshot_primary"], 20.0, "2023-03-10"),
        # trend base before testing -> counts
        (1, 2023, "WR", b["snapshot_trend_base"], 25.0, "2023-02-13"),
        # post-Combine snapshots exist but are NEVER exposures
        (1, 2023, "WR", b["snapshot_post"][0], 12.0, "2023-03-07"),
        (3, 2023, "TE", b["snapshot_combine_week"], 30.0, "2023-02-28"),
    ])
    testing = {1: date(2023, 3, 3), 2: date(2023, 3, 1), 3: date(2023, 3, 2)}
    pre = sf.snapshot_edp(rows, b["snapshot_primary"], cfg, testing, strict_pre=True)
    assert pre == {1: 20.0}, pre                                   # after-testing row 2 dropped
    # combine-week / post are not the primary label
    assert sf.snapshot_edp(rows, b["snapshot_post"][0], cfg, testing, strict_pre=True) == {}
    pp = sf.pre_post_edp(rows, cfg, testing)
    assert pp[1]["has_primary"] and pp[1]["has_post"] and pp[1]["has_trend"]
    assert 2 not in pp, "snapshot on/after the testing day must not be a pre-Combine exposure"
    assert 3 not in pp, "Combine-Week-only player has no pre/post exposure"
    # the exposure table still carries the (non-primary) player, flagged has_primary=False
    exp = {r["nfl_id"]: r for r in sf.build_exposures(rows, testing, {}, cfg)}
    assert exp[2]["has_primary"] is False and exp[2]["edp_pre"] is None
    assert exp[1]["has_primary"] is True and exp[1]["log_edp_pre"] == math.log(20.0)
    # edp_trend = 1wk-pre minus 2wk-pre (both strictly pre-Combine)
    assert abs(pp[1]["edp_trend"] - (20.0 - 25.0)) < 1e-9


def test_build_exposures_flags_and_participation():
    tmp = Path(tempfile.mkdtemp())
    root = _build(tmp, "planted", common.load_config()["seed"])
    cfg = _cfg(tmp, fixtures=root)
    con = common.connect(1)
    rows, testing, drills, _ = SEC.load_board_con(con, cfg, True, "planted", common.get_logger())
    exp = sf.build_exposures(rows, testing, drills, cfg)
    assert all(r["has_primary"] for r in exp), "every synthetic player has a 1wk-pre snapshot"
    assert any(r["has_trend"] for r in exp)
    pats = {r["participation_pattern"] for r in exp}
    assert pats <= {"dash_and_cut", "dash_only", "cut_only", "no_speed_drills", "no_combine_tracking"}
    # the fixture plants both-family runners and dash skippers (players with no drill rows -> no tracking)
    assert "dash_and_cut" in pats and "cut_only" in pats
    assert "no_combine_tracking" in pats
    assert all(r["consensus_share"] is None or 0.0 <= r["consensus_share"] <= 1.0 for r in exp)


def test_participation_pattern_classification():
    cfg = common.load_config()
    dash = set(cfg["audit"]["straight_line_drills"])
    cut = set(cfg["audit"]["cutting_drills"])
    assert sf.participation_pattern(cfg, set())[0] == "no_combine_tracking"
    assert sf.participation_pattern(cfg, dash | cut)[0] == "dash_and_cut"
    assert sf.participation_pattern(cfg, dash)[0] == "dash_only"
    assert sf.participation_pattern(cfg, cut)[0] == "cut_only"
    # has *some* combine tracking (a skill drill) but no speed drill -> no_speed_drills, NOT no_combine
    assert sf.participation_pattern(cfg, {"SKILL_DRILLS_WR"})[0] == "no_speed_drills"
    assert sf.participation_pattern(cfg, dash)[1] == 1


# --------------------------------------------------------------------------- #
# synthetic recovery: planted vs null
# --------------------------------------------------------------------------- #
def test_planted_effect_recovered():
    tmp = Path(tempfile.mkdtemp())
    a, _ = _analysis(tmp, "planted", common.load_config()["seed"])
    assert a["n"] >= a["min_n"], a["n"]
    sc, ic = a["speed_coef"], a["interaction_coef"]
    assert sc["coef"] < 0 and sc["pvalue"] < a["alpha"], sc       # market rewarded speed
    assert ic["coef"] < 0 and ic["pvalue"] < a["alpha"], ic       # stronger for at-risk (high EDP)
    assert a["gate_pass"] is True and a["market_responded_to_speed"] is True
    assert a["rewarded_more_for_at_risk"] is True


def test_null_case_does_not_recover():
    """The pre-registered gate (speed response) must be null in the null regime."""
    tmp = Path(tempfile.mkdtemp())
    a, _ = _analysis(tmp, "null", common.load_config()["seed"] + 1)
    assert a["gate_pass"] is False and a["market_responded_to_speed"] is False, a
    assert a["speed_coef"]["pvalue"] >= a["alpha"], a["speed_coef"]


def test_synthetic_recovery_size_and_power():
    """Across seeds, planted is recovered ~always while the null finds the primary effect ~nominal.

    A single fixed-seed null cannot demonstrate 'no effect' robustly (a ~5% false positive is the
    *correct* behaviour of any valid test); this checks the size/power of the planted-vs-null contrast.
    """
    tmp = Path(tempfile.mkdtemp())
    base = common.load_config()["seed"]
    k = 16
    planted_hits = null_hits = 0
    for j in range(k):
        ap, _ = _analysis(tmp / f"p{j}", "planted", base + j)
        an, _ = _analysis(tmp / f"n{j}", "null", base + j)
        planted_hits += bool(ap["gate_pass"])
        null_hits += bool(an["gate_pass"])
    assert planted_hits == k, f"planted recovered {planted_hits}/{k}"
    assert null_hits <= max(2, k // 4), f"null false-positive rate too high: {null_hits}/{k}"


def test_sign_convention_and_population():
    tmp = Path(tempfile.mkdtemp())
    root = _build(tmp, "planted", common.load_config()["seed"])
    cfg = _cfg(tmp, fixtures=root)
    con = common.connect(1)
    rows, testing, drills, _ = SEC.load_board_con(con, cfg, True, "planted", common.get_logger())
    env = SEC.load_envelope(con, cfg, True, "planted")
    a = SEC.analysis_a(rows, testing, env, cfg)
    assert a["primary"]["n"] <= len(sf.board_players(rows))
    # outcome is post-pre; a rewarded speed term is negative by construction of the planted DGP
    assert a["speed_coef"]["coef"] < 0


# --------------------------------------------------------------------------- #
# edge cases
# --------------------------------------------------------------------------- #
def test_edge_no_primary_rows_dropped_from_analysis():
    cfg = common.load_config()
    b = cfg["external"]["board"]
    rows = _board_rows([(9, 2024, "WR", b["snapshot_post"][0], 40.0, "2024-03-05")])
    testing = {9: date(2024, 3, 1)}
    env = {9: {"env_vmax": 8.8}}
    a = SEC.analysis_a(rows, testing, env, cfg)
    assert a["n"] == 0 and a["gate_pass"] is False, a
    pp = sf.pre_post_edp(rows, cfg, testing)
    assert pp[9]["has_primary"] is False


def test_edge_player_absent_from_board_ignored():
    cfg = common.load_config()
    b = cfg["external"]["board"]
    rows = _board_rows([
        (1, 2023, "WR", b["snapshot_primary"], 20.0, "2023-02-20"),
        (1, 2023, "WR", b["snapshot_post"][0], 12.0, "2023-03-07"),
    ])
    testing = {1: date(2023, 3, 3)}
    # envelope has an extra player (99) that is NOT on the board -> must be ignored
    env = {1: {"env_vmax": 8.9}, 99: {"env_vmax": 9.9}}
    a = SEC.analysis_a(rows, testing, env, cfg)
    assert a["n"] == 1, a["n"]


def test_edge_duplicate_snapshot_rows_average():
    cfg = common.load_config()
    b = cfg["external"]["board"]
    rows = _board_rows([
        (5, 2025, "TE", b["snapshot_primary"], 30.0, "2025-02-18"),
        (5, 2025, "TE", b["snapshot_primary"], 40.0, "2025-02-18"),   # duplicate cell
    ])
    testing = {5: date(2025, 2, 28)}
    pre = sf.snapshot_edp(rows, b["snapshot_primary"], cfg, testing, strict_pre=True)
    assert abs(pre[5] - 35.0) < 1e-9, pre                          # mean of the duplicates
    players = sf.board_players(rows)
    assert len(players) == 1 and players[0]["n_rows"] == 2
    assert sf.board_blank_id_count(rows) == 0


def test_edge_blank_nfl_id_counted_not_invented():
    cfg = common.load_config()
    b = cfg["external"]["board"]
    rows = _board_rows([(7, 2023, "WR", b["snapshot_primary"], 10.0, "2023-02-20")])
    rows.append(dict(rows[0], nfl_id=None))                        # blank join key
    assert sf.board_blank_id_count(rows) == 1
    assert len(sf.board_players(rows)) == 1                        # blank id never invents an id


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_determinism_same_seed_identical():
    tmp = Path(tempfile.mkdtemp())
    seed = common.load_config()["seed"]
    r1 = _build(tmp / "a", "planted", seed)
    r2 = _build(tmp / "b", "planted", seed)
    for name in ("board.csv", "combine_envelope.csv", "combine_testing.csv", "combine_drills.csv"):
        assert (r1 / "synthetic/security/planted" / name).read_bytes() == \
               (r2 / "synthetic/security/planted" / name).read_bytes(), name
    a1, _ = _analysis(tmp / "c", "planted", seed)
    a2, _ = _analysis(tmp / "d", "planted", seed)
    assert json.dumps(a1.get("speed_coef"), sort_keys=True) == json.dumps(a2.get("speed_coef"), sort_keys=True)
    assert a1["gate_pass"] == a2["gate_pass"]


# --------------------------------------------------------------------------- #
# board integrity
# --------------------------------------------------------------------------- #
def test_board_verify_sha256_rows_permission():
    tmp = Path(tempfile.mkdtemp())
    raw = tmp / "raw"
    (raw / "external").mkdir(parents=True)
    p = raw / "external" / "b.csv"
    p.write_text("draft_year,name,position,school,type,nfl_id,n_total,n,edp,edp_rank,pos_rank,"
                 "snapshot_date,snapshot_note\n"
                 "2023,A B,WR,Alabama,All,1,100,50,20.5,1,WR1,20/02/2023,1 Week Pre-Combine\n",
                 encoding="utf-8")
    good = sf.sha256_file(p)
    cfg = _cfg(tmp, raw=raw, **{"external.board.file": "external/b.csv",
                                "external.board.sha256": good, "external.board.n_rows_expected": 1})
    rep = sf.verify_board(cfg, p)
    assert rep["ok"] and rep["sha256_ok"] and rep["n_rows"] == 1, rep
    # wrong sha256 -> not ok, recorded reason
    rep = sf.verify_board(_cfg(tmp, raw=raw, **{"external.board.file": "external/b.csv",
                                                "external.board.sha256": "0" * 64,
                                                "external.board.n_rows_expected": 1}), p)
    assert rep["ok"] is False and any("sha256" in r for r in rep["reasons"])
    # not permitted -> not ok
    rep = sf.verify_board(_cfg(tmp, raw=raw, **{"external.board.file": "external/b.csv",
                                                "external.board.sha256": good,
                                                "external.board.permitted": False}), p)
    assert rep["ok"] is False and any("permitted" in r for r in rep["reasons"])


# --------------------------------------------------------------------------- #
# CLI stage: provenance + checkpoint
# --------------------------------------------------------------------------- #
def test_cli_synthetic_provenance_and_checkpoint():
    tmp = Path(tempfile.mkdtemp())
    cfg = _write_cfg(tmp, _cfg(tmp, fixtures=REPO / "fixtures"))
    r = _run(["--synthetic", "--synthetic-regime", "planted", "--force"], cfg)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-1500:]}"
    summ = json.loads((tmp / "out" / "security" / "SUMMARY.json").read_text())
    assert summ["provenance"] == SYNTH_PROV
    assert summ["analysis_a"]["gate_pass"] is True
    # checkpoint: a second run without --force is skipped
    r2 = _run(["--synthetic", "--synthetic-regime", "planted"], cfg)
    assert r2.returncode == 0 and "checkpoint hit" in r2.stderr, r2.stderr[-500:]


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_exposure_schema_and_dtypes, test_exposure_schema_assertion_fails_loudly,
             test_strict_pre_testing_rule_and_no_post_exposure, test_build_exposures_flags_and_participation,
             test_participation_pattern_classification,
             test_planted_effect_recovered, test_null_case_does_not_recover,
             test_synthetic_recovery_size_and_power, test_sign_convention_and_population,
             test_edge_no_primary_rows_dropped_from_analysis, test_edge_player_absent_from_board_ignored,
             test_edge_duplicate_snapshot_rows_average, test_edge_blank_nfl_id_counted_not_invented,
             test_determinism_same_seed_identical, test_board_verify_sha256_rows_permission,
             test_cli_synthetic_provenance_and_checkpoint]
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
