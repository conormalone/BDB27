#!/usr/bin/env python3
"""Tests for the DB-decay Phase 1 audit (`src/01_db_audit.py`).

Hermetic: every stage run uses the tiny committed fixture (`fixtures/synthetic/db_decay`)
copied into a temp directory, with `power.reps` reduced so the whole file finishes < 90 s.
The one exception is `test_drill_family_completeness_real_file`, which reads the real
Combine CSV (only the DB drill names) — no game data and no outputs are touched.

Covers the 10 required behaviours:
  1. drill-family completeness (real file) + loud failure on an unmapped drill;
  2. schema mismatch names the missing column;
  3. ICC(1,1) planted recovery + UNESTIMABLE for single-attempt players;
  4. attempt-numbering unit detection;
  5. gate logic (each stop condition forces NO_GO; pending never yields bare GO);
  6. determinism (two sample runs -> byte-identical a_*.csv);
  7. checkpoint skip + --force rebuild;
  8. provenance string in CSV / MD title / JSON;
  9. empty game tracking degrades without crashing;
 10. ICC helper agrees with the legacy `01_audit._icc_1_1`.
Plus an end-to-end `--synthetic` run.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
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

AUDIT = REPO / "src" / "01_db_audit.py"
FIX = REPO / "fixtures" / "synthetic" / "db_decay"
SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = _load("db_audit_mod", AUDIT)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fixture_dir(tmp: Path) -> Path:
    dst = tmp / "fixture"
    shutil.copytree(FIX, dst)
    return dst


def _cfg_path(tmp: Path, fixture: Path, reps: int = 0) -> Path:
    cfg = common.load_config()
    cfg.pop("_config_path", None)
    cfg["paths"]["raw"] = str(fixture)
    cfg["paths"]["synthetic"] = str(fixture)
    cfg["paths"]["outputs"] = str(tmp / "outputs")
    cfg["paths"]["interim"] = str(tmp / "interim")
    cfg["paths"]["parquet"] = str(tmp / "interim" / "parquet")
    cfg["logging"]["run_log"] = str(tmp / "run_log.csv")
    cfg["files"]["game_tracking_sample"] = "game_tracking.csv"
    cfg["seed"] = 12345
    # reps=0 makes the MC power block return UNESTIMABLE before importing statsmodels;
    # the gate falls back to the closed-form MDE. These tests check code paths, not power.
    cfg["db_decay"]["power"]["reps"] = reps
    cfg["db_decay"]["power"]["effect_grid"] = [0.005, 0.01, 0.02]
    # Test speed only: fewer efforts per DB keeps the MC fit small. Not a finding.
    cfg["db_decay"]["power"]["assumed_efforts_per_db"] = 8
    p = tmp / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _run(cfg: Path, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(AUDIT), "--config", str(cfg), *args]
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)


def _ctx(cfg_path: Path, fixture: Path | None = None):
    cfg = common.load_config(cfg_path)
    mode, synthetic = "sample", False
    paths = m.resolve_inputs(cfg, mode, synthetic)
    con = common.connect(threads=1)
    ctx = dict(cfg=cfg, dd=cfg["db_decay"], mode=mode, synthetic=synthetic,
               prov=SAMPLE_PROV, out=Path(tempfile.mkdtemp()), con=con,
               log=common.get_logger())
    ctx["sql"] = dict(
        combine_tracking=m.read_sql(paths["combine_tracking"]),
        players=m.read_sql(paths["players"]),
        games=m.read_sql(paths["games"]),
        player_play=m.read_sql(paths["player_play"]),
        game_tracking=m.game_tracking_sql(paths["game_tracking"]),
    )
    return ctx


def _rows(p: Path) -> list[dict]:
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# 1. drill families
# --------------------------------------------------------------------------- #
def test_drill_family_completeness_real_file():
    """Every DB drill in the REAL combine file resolves to a configured family."""
    con = duckdb.connect()
    con.execute("PRAGMA threads=1")
    raw = common.resolve(common.load_config(), "raw")
    real = raw / "combine_tracking.csv"
    pl = raw / "players.csv"
    if not real.exists():
        return  # data is device-local; the fixture test still covers the code path
    rows = con.execute(f"""
        SELECT DISTINCT c.drill_type, c.drill_name
        FROM read_csv_auto('{real}') c
        JOIN read_csv_auto('{pl}') p
          USING(nfl_id)
        WHERE c.entity_type='PLAYER' AND p.nfl_position IN ('CB','SS','FS','DB')
    """).fetchall()
    assert rows, "no DB drills found in the real combine file"
    fam = common.load_config()["db_decay"]["audit"]["drill_families"]
    missing = [(t, n) for t, n in rows if not (fam.get(n) or fam.get(t))]
    assert not missing, f"unmapped DB drills: {missing}"


def test_unmapped_drill_fails_loudly():
    """An unmapped drill makes item_a raise, naming the drill (monkeypatched config)."""
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    cfg_path = _cfg_path(tmp, fixture)
    ctx = _ctx(cfg_path)
    ctx["dd"]["audit"]["drill_families"].pop("BOX_DRILL")
    try:
        m.item_a(ctx)
    except m.SchemaError as exc:
        assert "BOX_DRILL" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("item_a did not fail on an unmapped drill")


# --------------------------------------------------------------------------- #
# 2. schema assertion
# --------------------------------------------------------------------------- #
def test_schema_mismatch_names_missing_column():
    tmp = Path(tempfile.mkdtemp())
    src = FIX / "players.csv"
    with open(src, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fields = [f for f in rows[0] if f != "college_name"]
    bad = tmp / "players_missing.csv"
    with open(bad, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    try:
        m.assert_schema(duckdb.connect(), bad, "players")
    except m.SchemaError as exc:
        assert "college_name" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("assert_schema accepted a missing column")


# --------------------------------------------------------------------------- #
# 3. ICC planted recovery + UNESTIMABLE
# --------------------------------------------------------------------------- #
def test_icc_planted_recovery_and_unestimable():
    rng = np.random.default_rng(11)
    mu = rng.normal(10.0, 0.5, 20)
    groups = {i: [float(mu[i] + rng.normal(0, 0.25)) for _ in range(2)] for i in range(20)}
    r = m.icc_1_1(groups, 5)
    assert r["status"] == "ESTIMATED"
    assert abs(r["icc"] - 0.8) < 0.1, f"planted ICC 0.8 recovered as {r['icc']:.3f}"
    assert r["ci_low"] <= r["icc"] <= r["ci_high"]
    # single-attempt players carry no test-retest information
    singles = {i: [float(rng.normal(10, 1))] for i in range(10)}
    r2 = m.icc_1_1(singles, 5)
    assert r2["status"].startswith("UNESTIMABLE")
    assert r2["icc"] is None


# --------------------------------------------------------------------------- #
# 4. attempt-numbering unit
# --------------------------------------------------------------------------- #
def test_attempt_unit_detection():
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    ctx = _ctx(_cfg_path(tmp, fixture))
    res = m.item_b(ctx)
    assert res["chosen_attempt_unit"].startswith("H1")
    assert res["n_duplicates_chosen"] == 0
    assert res["n_duplicates_H2"] > 0
    rows = _rows(ctx["out"] / "b_attempt_unit.csv")
    assert {r["hypothesis"] for r in rows} >= {"H1_within_player_drilltype_drillname",
                                               "H2_within_player_drilltype"}


# --------------------------------------------------------------------------- #
# 5. gate logic
# --------------------------------------------------------------------------- #
def _selected(feature, icc, required=False, ci=(0.8, 0.95)):
    return dict(feature=feature, drill="FORTY_YARD_DASH", metric=feature, icc=icc,
                ci_low=ci[0], ci_high=ci[1], icc_status="ESTIMATED",
                passed_icc=icc >= 0.6, required=required, vif=1.0,
                selection=("required" if required else "icc_then_vif"))


def _excluded(feature, icc, reason="icc_below_min", ci=(0.05, 0.45)):
    return dict(feature=feature, drill="FORTY_YARD_DASH", icc=icc, ci_low=ci[0],
                ci_high=ci[1], reason=reason, vif=None)


def _selection(selected, excluded):
    return dict(rule="icc_then_vif",
                thresholds=dict(icc_min=0.6, vif_max=5.0, max_features=3),
                required=["vmax"], selected=[s["feature"] for s in selected],
                selected_detail=selected, excluded=excluded,
                vif={"vmax": 1.0}, n_players_vif=97, vif_status="ESTIMATED")


def _gate_items(**over):
    items = {
        "a": dict(status="EVALUATED", n_dbs_in_combine=118),
        "b": dict(status="EVALUATED"),
        "c": dict(status="PARTIAL", n_in_player_play=115, n_in_game_tracking=115),
        "d": dict(status="EVALUATED"),
        "e": dict(status="EVALUATED"),
        "f": dict(status="EVALUATED"),
        "g": dict(status="EVALUATED", vmax_icc=0.9, a0_icc=0.85, max_decel_icc=0.2,
                  selection=_selection([_selected("vmax", 0.9, required=True)],
                                       [_excluded("a0", 0.85)]),
                  icc_sensitivity=dict(gate_variant="both", rows=[])),
        "h": dict(status="PARTIAL"),
        "i": dict(status="PARTIAL", mc_mde=0.005, closed_form_mde=0.003),
    }
    for k, v in over.items():
        items[k].update(v)
    return items


def _gate_ctx(tmp: Path):
    cfg = common.load_config()
    cfg["paths"]["outputs"] = str(tmp / "outputs")
    cfg["logging"]["run_log"] = str(tmp / "run_log.csv")
    cfg["files"]["game_tracking_sample"] = "game_tracking.csv"
    out = tmp / "outputs" / "audit" / "db_decay"
    out.mkdir(parents=True, exist_ok=True)
    return dict(cfg=cfg, dd=cfg["db_decay"], mode="sample", prov=SAMPLE_PROV,
                synthetic=False, out=out)


def test_gate_logic_all_conditions():
    tmp = Path(tempfile.mkdtemp())
    ctx = _gate_ctx(tmp)

    checks = m.gate_checks(ctx, _gate_items())
    d = m.write_decision(ctx, checks, _gate_items())
    assert d["decision"] == "GO_PENDING_FULL_RUN"
    assert "GO" != d["decision"]  # never a bare GO
    assert d["pending_full_run"], "a pending item must be reported"

    flips = [
        ("a", dict(n_dbs_in_combine=0)),
        ("c", dict(n_in_player_play=10)),
        ("g", dict(selection=_selection([_selected("vmax", 0.2, required=True)],
                                        [_excluded("a0", 0.2)]))),
        ("i", dict(mc_mde=0.05)),
    ]
    for item, over in flips:
        it = _gate_items(**{item: over})
        ck = m.gate_checks(ctx, it)
        dd = m.write_decision(ctx, ck, it)
        assert dd["decision"] == "NO_GO", f"stop condition not enforced: {item} {over}"
        assert dd["failures"]
    # every check object has the required shape
    for c in checks:
        assert {"name", "value", "threshold", "status", "item"} <= set(c)
        assert c["status"] in ("pass", "fail", "pending", "excluded_by_selection")
        extra = set(c) - {"name", "value", "threshold", "status", "item"}
        assert not extra or c["name"].startswith(("icc_", "power_"))


def test_required_feature_failure_forces_no_go():
    """A required feature below icc_min is a stop condition, not an exclusion."""
    ctx = _gate_ctx(Path(tempfile.mkdtemp()))
    it = _gate_items()
    it["g"]["selection"] = _selection(
        [_selected("vmax", 0.30, required=True, ci=(0.10, 0.50))],
        [_excluded("a0", 0.28)])
    d = m.decide(ctx, it)
    assert d["decision"] == "NO_GO"
    assert "icc_selected_vmax" in d["failures"]


def test_candidate_icc_failure_is_excluded_not_fatal():
    """a0 failing ICC is recorded as excluded_by_selection; the gate stays open."""
    tmp = Path(tempfile.mkdtemp())
    ctx = _gate_ctx(tmp)
    it = _gate_items()
    it["g"]["selection"] = _selection(
        [_selected("vmax", 0.686, required=True, ci=(0.548, 0.788))],
        [_excluded("a0", 0.288, ci=(0.071, 0.480))])
    d = m.write_decision(ctx, None, it)
    assert d["decision"] == "GO_PENDING_FULL_RUN"
    assert d["failures"] == []
    payload = json.loads((tmp / "outputs" / "audit" / "db_decay" / "audit_decision.json")
                         .read_text(encoding="utf-8"))
    assert payload["selection"]["selected"] == ["vmax"]
    assert [e["feature"] for e in payload["selection"]["excluded"]] == ["a0"]
    row = next(c for c in payload["checks"] if c["name"] == "icc_excluded_a0")
    assert row["status"] == "excluded_by_selection"
    assert abs(row["value"] - 0.288) < 1e-9
    assert row["ci_low"] is not None and row["ci_high"] is not None


def test_required_failure_not_laundered_by_selection():
    """Even with a populated selection object, a failing required feature stays NO_GO."""
    ctx = _gate_ctx(Path(tempfile.mkdtemp()))
    it = _gate_items()
    it["g"]["selection"] = _selection(
        [_selected("vmax", 0.20, required=True, ci=(0.0, 0.4))],
        [_excluded("a0", 0.90, reason="max_features_reached", ci=(0.8, 0.95))])
    d = m.decide(ctx, it)
    assert d["decision"] == "NO_GO"  # never GO / GO_PENDING
    assert "vmax" in d["selection"]["selected"]
    assert "vmax" not in [e["feature"] for e in d["selection"]["excluded"]]
    assert "icc_selected_vmax" in d["failures"]


def test_icc_sensitivity_variants_and_gate_source():
    """Exactly 4 variants x 2 features; the gate ICC is the `both` variant."""
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    ctx = _ctx(_cfg_path(tmp, fixture))
    r = m.item_g(ctx)
    rows = _rows(ctx["out"] / "g_feature_icc_sensitivity.csv")
    assert len(rows) == 8
    assert {x["variant"] for x in rows} == {"none", "vmax_bound_only",
                                            "over_peak_only", "both"}
    assert {x["feature"] for x in rows} == {"vmax", "a0"}
    for x in rows:
        assert x["is_gate_variant"] == ("True" if x["variant"] == "both" else "False")
        assert x["n_players"] and x["n_attempts_used"] is not None
    both = {x["feature"]: x for x in rows if x["variant"] == "both"}
    assert abs(float(both["vmax"]["icc"]) - r["vmax_icc"]) < 1e-5  # CSV is 6 dp
    assert abs(float(both["a0"]["icc"]) - r["a0_icc"]) < 1e-5
    icc_rows = _rows(ctx["out"] / "g_feature_icc.csv")
    vmax = next(x for x in icc_rows
                if x["drill"] == "FORTY_YARD_DASH" and x["feature"] == "vmax")
    assert abs(float(vmax["icc"]) - float(both["vmax"]["icc"])) < 1e-5
    statuses = {x["selection_status"] for x in icc_rows}
    assert "selected" in statuses and "excluded_by_selection" in statuses


def test_missing_sanity_key_fails_loudly_without_default():
    """Deleting a sanity key must fail loudly; there is no 12.5/1.5 fallback."""
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    cfg_path = _cfg_path(tmp, fixture)
    cfg = common.load_config(cfg_path)
    del cfg["db_decay"]["ceiling"]["sanity"]["vmax_yds_max"]
    bad = tmp / "config_missing_sanity.yaml"
    bad.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    try:
        m.ceiling_config(common.load_config(bad)["db_decay"])
    except m.GateError as exc:
        assert "sanity.vmax_yds_max" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("ceiling_config silently defaulted a missing sanity key")
    r = _run(bad, "--mode", "sample")
    assert r.returncode == 2, r.stderr[-2000:]
    assert "sanity.vmax_yds_max" in r.stderr


# --------------------------------------------------------------------------- #
# 5b. gate/power units, config fail-fast, decision isolation (regressions)
# --------------------------------------------------------------------------- #
def test_power_gate_compares_in_soi_units():
    """The MDE is per 1 SD of load; the smallest effect of interest is per 10 episodes.

    REGRESSION: the gate used to compare the two scales directly, so an MDE of 0.015 per SD
    (0.0075 per 10 episodes at load_sd_per10 = 2.0) was failed against a 0.01 per-10 target.
    """
    ctx = _gate_ctx(Path(tempfile.mkdtemp()))
    load_sd = float(ctx["dd"]["power"]["assumed_load_sd_per10"])
    soi = float(ctx["dd"]["power"]["smallest_effect_of_interest"])
    it = _gate_items()
    it["i"]["mc_mde"] = 0.015            # per 1 SD -> below the per-10 target after conversion
    assert 0.015 / load_sd <= soi < 0.015  # the case only exists because the scales differ
    chk = next(c for c in m.gate_checks(ctx, it)
               if c["name"] == "power_mde_vs_smallest_effect_of_interest")
    assert chk["status"] == "pass"
    assert abs(chk["value"] - 0.015 / load_sd) < 1e-12
    assert abs(chk["mde_per_sd_load"] - 0.015) < 1e-12
    assert m.decide(ctx, it)["decision"] == "GO_PENDING_FULL_RUN"

    # ... and a genuinely underpowered design still fails on the same scale
    it2 = _gate_items()
    it2["i"]["mc_mde"] = 0.05
    d2 = m.decide(ctx, it2)
    assert d2["decision"] == "NO_GO"
    assert "power_mde_vs_smallest_effect_of_interest" in d2["failures"]


def test_missing_db_decay_keys_fail_loudly():
    """Every key the audit consumes is required by name; no silent defaults anywhere."""
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    cases = [
        (("audit", "icc_ci_alpha"), "db_decay.audit.icc_ci_alpha"),
        (("audit", "provenance_thresholds"), "db_decay.audit.provenance_thresholds"),
        (("audit", "alignment", "slot_max_lateral_yd"), "db_decay.audit.alignment.slot_max_lateral_yd"),
        (("power", "assumed_load_sd_per10"), "db_decay.power.assumed_load_sd_per10"),
        (("power", "assumed_random_slope_sd"), "db_decay.power.assumed_random_slope_sd"),
        (("ceiling", "smooth_window_frames"), "db_decay.ceiling.smooth_window_frames"),
        (("ceiling", "cut_window_s"), "db_decay.ceiling.cut_window_s"),
        (("ceiling", "heading_window_frames"), "db_decay.ceiling.heading_window_frames"),
    ]
    for path, dotted in cases:
        cfg = common.load_config(_cfg_path(tmp, fixture))
        node = cfg
        for part in ("db_decay",) + path[:-1]:
            node = node[part]
        node.pop(path[-1])
        bad = tmp / "config_missing.yaml"
        bad.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        try:
            m.validate_db_decay_config(common.load_config(bad)["db_decay"])
        except m.GateError as exc:
            assert dotted in str(exc), (dotted, str(exc))
        else:  # pragma: no cover
            raise AssertionError(f"missing {dotted} was accepted silently")
        r = _run(bad, "--mode", "sample")
        assert r.returncode == 2, r.stderr[-2000:]
        assert dotted in r.stderr, r.stderr[-2000:]

    # an invalid VALUE (not just a missing key) is also loud
    cfg = common.load_config(_cfg_path(tmp, fixture))
    cfg["db_decay"]["audit"]["icc_ci_alpha"] = 1.5
    bad = tmp / "config_bad_value.yaml"
    bad.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    r = _run(bad, "--mode", "sample")
    assert r.returncode == 2, r.stderr[-2000:]
    assert "icc_ci_alpha" in r.stderr


def test_decision_written_to_db_tree_and_legacy_preserved():
    """The DB decision goes to audit/db_decay/; another stage's decision is never clobbered."""
    tmp = Path(tempfile.mkdtemp())
    ctx = _gate_ctx(tmp)
    legacy = tmp / "outputs" / "audit" / "audit_decision.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"stage": "01_audit", "decision": "see gate values"}),
                      encoding="utf-8")
    before = legacy.read_text(encoding="utf-8")
    m.write_decision(ctx, None, _gate_items())
    assert legacy.read_text(encoding="utf-8") == before, "legacy decision was overwritten"
    assert (ctx["out"] / "audit_decision.json").exists()
    # absent legacy path -> mirrored for gate.py compatibility
    ctx2 = _gate_ctx(Path(tempfile.mkdtemp()))
    m.write_decision(ctx2, None, _gate_items())
    assert (tmp / "outputs" / "audit" / "db_decay" / "audit_decision.json").exists()
    legacy2 = Path(ctx2["cfg"]["paths"]["outputs"]) / "audit" / "audit_decision.json"
    assert legacy2.exists()


# --------------------------------------------------------------------------- #
# 5c. item (e): the per-quarter snap distribution is over DB-GAMES (P27 regression)
# --------------------------------------------------------------------------- #
def test_snap_distribution_is_per_db_game():
    """REGRESSION (P27): grouping by (position, quarter) alone summed every DB-game into one
    row, so `n_db_games` was 1 and `snaps_median` was the population total."""
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    cfg = _cfg_path(tmp, fixture)
    r = _run(cfg, "--mode", "sample")
    assert r.returncode == 0, r.stderr[-2000:]
    out = tmp / "outputs" / "audit" / "db_decay"
    per_game = _rows(out / "e_snaps_by_db_game.csv")
    dist = _rows(out / "e_snap_distribution_by_quarter.csv")
    assert per_game and dist
    expected: dict[tuple[str, int], list[int]] = {}
    for row in per_game:
        for q in (1, 2, 3, 4):
            v = int(row[f"q{q}"])
            if v > 0:
                expected.setdefault((row["position_group"], q), []).append(v)
    for row in dist:
        key = (row["position_group"], int(row["quarter"]))
        vals = expected.get(key, [])
        assert int(row["n_db_games"]) == len(vals), (key, row["n_db_games"], len(vals))
        assert abs(float(row["snaps_median"]) - float(np.median(vals))) < 1e-9, key
        assert float(row["snaps_median"]) <= float(row["snaps_max"]), key
    assert max(int(r["n_db_games"]) for r in dist) > 1, "every row collapsed to one DB-game"


# --------------------------------------------------------------------------- #
# 6. determinism
# --------------------------------------------------------------------------- #
def test_determinism_two_sample_runs():
    tmp = Path(tempfile.mkdtemp())
    outs = []
    for k in (1, 2):
        t = tmp / f"run{k}"
        t.mkdir()
        fixture = _fixture_dir(t)
        cfg = _cfg_path(t, fixture)
        r = _run(cfg, "--mode", "sample")
        assert r.returncode == 0, r.stderr[-2000:]
        outs.append(t / "outputs" / "audit" / "db_decay")
    files = sorted(p.name for p in outs[0].glob("a_*.csv"))
    assert files
    for name in files:
        a = (outs[0] / name).read_bytes()
        b = (outs[1] / name).read_bytes()
        assert a == b, f"{name} is not deterministic"


# --------------------------------------------------------------------------- #
# 7. checkpoint
# --------------------------------------------------------------------------- #
def test_checkpoint_skip_and_force():
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    cfg = _cfg_path(tmp, fixture)
    r1 = _run(cfg, "--mode", "sample")
    assert r1.returncode == 0, r1.stderr[-2000:]
    r2 = _run(cfg, "--mode", "sample")
    assert r2.returncode == 0
    assert "checkpoint hit" in r2.stdout
    log = Path(tmp / "run_log.csv").read_text(encoding="utf-8").strip().splitlines()
    assert "checkpoint=True" in log[-1]
    r3 = _run(cfg, "--mode", "sample", "--force")
    assert r3.returncode == 0
    assert "decision=" in r3.stdout
    log = Path(tmp / "run_log.csv").read_text(encoding="utf-8").strip().splitlines()
    assert "checkpoint" not in log[-1]


# --------------------------------------------------------------------------- #
# 8. provenance
# --------------------------------------------------------------------------- #
def test_provenance_in_csv_md_json():
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    cfg = _cfg_path(tmp, fixture)
    r = _run(cfg, "--mode", "sample")
    assert r.returncode == 0, r.stderr[-2000:]
    out = tmp / "outputs" / "audit" / "db_decay"
    csvs = sorted(out.glob("*.csv"))
    assert csvs
    for p in csvs:
        first = p.read_text(encoding="utf-8").splitlines()[0]
        assert first.split(",")[0] == "provenance", p.name
        assert SAMPLE_PROV in p.read_text(encoding="utf-8"), p.name
    md = (out / "SUMMARY_db_decay.md").read_text(encoding="utf-8")
    assert SAMPLE_PROV in md.splitlines()[0]
    meta = json.loads((out / "audit_items.json").read_text(encoding="utf-8"))
    assert meta["provenance"] == SAMPLE_PROV
    dec = json.loads((tmp / "outputs" / "audit" / "db_decay" / "audit_decision.json")
                     .read_text(encoding="utf-8"))
    assert dec["provenance"] == SAMPLE_PROV


# --------------------------------------------------------------------------- #
# 9. empty game tracking
# --------------------------------------------------------------------------- #
def test_empty_game_tracking_degrades():
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    gt = fixture / "game_tracking.csv"
    header = gt.read_text(encoding="utf-8").splitlines()[0]
    gt.write_text(header + "\n", encoding="utf-8")
    cfg = _cfg_path(tmp, fixture)
    r = _run(cfg, "--mode", "sample")
    assert r.returncode == 0, r.stderr[-3000:]
    meta = json.loads((tmp / "outputs" / "audit" / "db_decay" / "audit_items.json")
                      .read_text(encoding="utf-8"))
    assert meta["items"]["c"]["n_in_game_tracking"] == 0
    assert meta["items"]["h"]["n_units"] == 0
    assert meta["items"]["c"]["status"] == "PARTIAL"
    assert meta["items"]["h"]["status"] == "PARTIAL"


# --------------------------------------------------------------------------- #
# 10. cross-check against the legacy ICC helper
# --------------------------------------------------------------------------- #
def test_icc_matches_legacy_audit_helper():
    legacy = _load("audit_legacy_mod", REPO / "src" / "01_audit.py")
    rng = np.random.default_rng(3)
    groups = {i: [float(rng.normal(5, 1)) for _ in range(2 + (i % 2))] for i in range(12)}
    a = m.icc_1_1(groups, 5)
    b = legacy._icc_1_1(groups, 5)
    assert a["status"] == b["status"] == "ESTIMATED"
    assert abs(a["icc"] - b["icc"]) < 1e-12
    assert abs(a["msb"] - b["msb"]) < 1e-12
    assert abs(a["msw"] - b["msw"]) < 1e-12


# --------------------------------------------------------------------------- #
# end-to-end synthetic
# --------------------------------------------------------------------------- #
def test_synthetic_end_to_end():
    tmp = Path(tempfile.mkdtemp())
    fixture = _fixture_dir(tmp)
    cfg = _cfg_path(tmp, fixture)
    r = _run(cfg, "--mode", "sample", "--synthetic")
    assert r.returncode == 0, r.stderr[-3000:]
    out = tmp / "outputs" / "audit" / "db_decay"
    meta = json.loads((out / "audit_items.json").read_text(encoding="utf-8"))
    assert meta["synthetic"] is True
    assert meta["provenance"] == SYNTH_PROV
    assert set(meta["items"]) == set("abcdefghi")
    assert (out / "audit_decision.json").exists()
    assert (out / "SUMMARY_db_decay.md").exists()
