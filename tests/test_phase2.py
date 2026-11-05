"""Phase 2 (Combine physical time): contract, known answers, QC and pooling.

Every test either checks a known-answer series (so the expected value is
independently derivable) or checks that a named failure mode is *reported*
instead of silently absorbed. Nothing here asserts a football number.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

import common as C
import physical as P
import schema as S
import stats as ST

from conftest import load_stage


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_cfg(path: Path, cfg: dict) -> Path:
    payload = {C.TASK_KEY: C.block(cfg)}
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _clone_fixture(cfg_for_fixture: dict, src: Path, dst: Path) -> dict:
    """Copy the fixture and point a cloned config at the copy."""
    shutil.copytree(src, dst)
    cfg = copy.deepcopy(cfg_for_fixture)
    b = C.block(cfg)
    b["paths"]["raw"] = str(dst)
    b["paths"]["interim"] = str(dst / "interim")
    b["paths"]["parquet"] = str(dst / "interim" / "parquet")
    b["paths"]["checkpoints"] = str(dst / "interim" / "checkpoints")
    b["paths"]["tmp"] = str(dst / "interim" / "tmp")
    b["paths"]["outputs"] = str(dst / "outputs")
    b["paths"]["phase2"] = str(dst / "outputs" / "phase2")
    b["paths"]["calibration"] = str(dst / "outputs" / "calibration.json")
    b["paths"]["run_log"] = str(dst / "outputs" / "run_log.csv")
    return cfg


def _frames(nfl_id=1, attempt=1, xs=None, speeds=None, dt=0.1):
    import pandas as pd
    from datetime import datetime, timedelta

    base = datetime(2024, 3, 1, 18, 0, 0)
    rows = []
    for i, (x, s) in enumerate(zip(xs, speeds)):
        rows.append({
            "nfl_id": nfl_id, "attempt": attempt, "event_id": f"E{nfl_id}",
            "draft_year": 2024, "time": base + timedelta(seconds=i * dt),
            "x": float(x), "y": 0.0, "s": float(s), "a": 0.0,
            "dis": float(s) * dt, "dir": 90.0,
            "combine_position": "DL", "display_name": "T",
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Schema / units fail loudly
# --------------------------------------------------------------------------- #
def test_missing_combine_column_raises(cfg, cfg_for_fixture, fixture_dir, tmp_path):
    import duckdb
    dst = tmp_path / "missing"
    cfg2 = _clone_fixture(cfg_for_fixture, fixture_dir, dst)
    src = dst / "combine_tracking.csv"
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    fields = [f for f in rows[0] if f != "s"]
    with open(src, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fields})
    con = C.duckdb_connect(cfg2)
    try:
        with pytest.raises(C.SchemaError, match="missing column 's'"):
            S.assert_schema(con, cfg2, "combine_tracking")
    finally:
        con.close()


def test_wrong_speed_units_raise(cfg_for_fixture, fixture_dir, tmp_path):
    import duckdb
    dst = tmp_path / "units"
    cfg2 = _clone_fixture(cfg_for_fixture, fixture_dir, dst)
    src = dst / "combine_tracking.csv"
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    for r in rows:
        r["s"] = str(float(r["s"]) * 10.0)  # feet/s read as yd/s
    with open(src, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    con = C.duckdb_connect(cfg2)
    try:
        with pytest.raises(C.SchemaError, match="UNIT CHECK FAILED"):
            S.assert_units(con, cfg2, "combine_tracking")
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Known-answer interpolation and the gap policy
# --------------------------------------------------------------------------- #
def test_interpolation_is_exact_on_a_linear_ramp(cfg):
    times = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    along = [2.0 * t for t in times]
    r = P.time_at_distance_qc(times, along, 1.25, first_index=0, onset_s=0.0,
                              nominal_dt=0.1, max_gap_s=0.5)
    assert r["ok"] and r["interpolated"]
    assert r["time_s"] == pytest.approx(0.625)
    assert r["gap_policy"] == "none"


def test_gap_inside_first_second_is_not_interpolated(cfg):
    times = [0.0, 0.1, 0.2, 0.5, 0.6]
    along = [2.0 * t for t in times]
    r = P.time_at_distance_qc(times, along, 0.9, first_index=1, onset_s=0.1,
                              nominal_dt=0.1, max_gap_s=0.5)
    assert r["crossed"] is True
    assert r["ok"] is False and r["time_s"] is None
    assert r["gap_policy"] == "forbidden_first_second"


def test_gap_under_cap_outside_first_second_is_bridged(cfg):
    times = [0.0, 0.1, 1.1, 1.3, 1.4]
    along = [2.0 * t for t in times]
    r = P.time_at_distance_qc(times, along, 2.4, first_index=0, onset_s=0.1,
                              nominal_dt=0.1, max_gap_s=0.5)
    assert r["ok"] is True
    assert r["gap_policy"] == "bridged"
    assert r["time_s"] == pytest.approx(1.2)


def test_gap_over_cap_is_never_bridged(cfg):
    times = [0.0, 0.1, 1.1, 1.3, 1.4]
    along = [2.0 * t for t in times]
    r = P.time_at_distance_qc(times, along, 2.4, first_index=0, onset_s=0.1,
                              nominal_dt=0.1, max_gap_s=0.1)
    assert r["crossed"] is True
    assert r["ok"] is False
    assert r["gap_policy"] == "too_long"


def test_unreached_distance_reports_no_time(cfg):
    r = P.time_at_distance_qc([0.0, 0.1, 0.2], [0.0, 0.1, 0.2], 5.0,
                              first_index=0, onset_s=0.0, nominal_dt=0.1,
                              max_gap_s=0.5)
    assert r["crossed"] is False and r["ok"] is False


# --------------------------------------------------------------------------- #
# Onset determinism and backward starts
# --------------------------------------------------------------------------- #
def test_onset_is_deterministic(cfg):
    times = [i / 10 for i in range(20)]
    speeds = [0.2] * 6 + [1.2, 1.4] + [3.0] * 12
    a = P.detect_onset(times, speeds, cfg)
    b = P.detect_onset(times, speeds, cfg)
    assert a == b
    assert a["found"] and a["onset_s"] == pytest.approx(0.6)


def test_backward_start_is_flagged_not_reorigin_ed(cfg):
    """A segment that walks back >0.5 yd is not a sprint measurement."""
    mod = load_stage("02_combine_physical_time")
    xs = [12.0 - 0.1 * i for i in range(11)]          # 12.0 -> 11.0
    xs += [11.0 + 0.45 * i for i in range(1, 21)]     # 11.0 -> 20.0
    speeds = [1.2] * 10 + [2.0 + 0.3 * i for i in range(1, 22)]
    df = _frames(xs=xs, speeds=speeds[:len(xs)])
    row, _, _ = mod.measure_attempt(df, cfg, set())
    assert row["ok"] is True
    assert row["backward_excursion_yd"] > 0.5
    assert row["qc_exclude"] is True
    assert row["qc_reason"] == "backward_start"


def test_forward_attempt_is_not_flagged(cfg):
    mod = load_stage("02_combine_physical_time")
    xs = [0.0 + 0.45 * i for i in range(30)]
    speeds = [1.2] * 3 + [2.0 + 0.2 * i for i in range(27)]
    df = _frames(xs=xs, speeds=speeds)
    row, _, _ = mod.measure_attempt(df, cfg, set())
    assert row["ok"] is True
    assert row["qc_exclude"] is False


# --------------------------------------------------------------------------- #
# Sanity checks as data
# --------------------------------------------------------------------------- #
def test_duplicate_report_finds_both_meanings(cfg):
    mod = load_stage("02_combine_physical_time")
    import pandas as pd
    df = _frames(xs=[0, 1, 2, 3], speeds=[1, 2, 3, 4])
    other = df.copy()
    other["event_id"] = "SECOND-EVENT"             # same attempt number, new event
    both = pd.concat([df, other], ignore_index=True)
    rep = mod.duplicate_report(both)
    assert rep["attempt_keys"] == 1
    assert rep["frame_rows"] == 4                  # every frame is present twice


def test_speed_cap_flags_twelve_plus(cfg):
    assert P.speed_cap_flags([1.0, 12.0, 12.0001], cfg) == 1
    assert P.speed_cap_flags([1.0, 11.9], cfg) == 0


def test_combine_sampling_gate_passes_at_10hz_and_fails_at_5hz(cfg):
    mod = load_stage("02_combine_physical_time")
    df = _frames(xs=list(range(10)), speeds=[1.0] * 10)
    assert mod.assert_sampling_rate(df, cfg)["hz"] == 10.0
    from datetime import datetime, timedelta
    base = datetime(2024, 3, 1, 18, 0, 0)
    bad = df.copy()
    bad["time"] = [base + timedelta(seconds=0.2 * i) for i in range(len(bad))]
    with pytest.raises(C.SchemaError, match="SAMPLING GATE FAILED"):
        mod.assert_sampling_rate(bad, cfg)


# --------------------------------------------------------------------------- #
# Hierarchical pooling
# --------------------------------------------------------------------------- #
def test_one_attempt_player_is_shrunk_toward_the_grand_mean():
    fit = ST.hierarchical_normal([(1, [0.50, 0.52]), (2, [0.90]),
                                 (3, [0.55, 0.54])])
    assert fit["pooled"] is True
    rec = {r["subject"]: r for r in fit["records"]}
    assert rec[2]["n"] == 1
    assert rec[2]["posterior_mean"] < 0.90
    assert rec[2]["posterior_mean"] > fit["mu"]
    assert rec[2]["posterior_sd"] > 0
    assert rec[2]["shrinkage_weight"] > rec[1]["shrinkage_weight"]


def test_no_repeat_attempts_reports_no_pooling_and_no_invented_se():
    fit = ST.hierarchical_normal([(1, [0.5]), (2, [0.6]), (3, [0.7])])
    assert fit["pooled"] is False
    assert "not estimable" in fit["reason"]
    for rec in fit["records"]:
        assert rec["posterior_sd"] is None


def test_zero_between_variance_does_not_pool():
    fit = ST.hierarchical_normal([(1, [0.500, 0.500]), (2, [0.700, 0.700])])
    # Identical repeats inside a player, different means between players ->
    # tau2 > 0, so this should pool. The no-pool branch is the reverse case:
    fit2 = ST.hierarchical_normal([(1, [0.40, 0.80]), (2, [0.45, 0.75])])
    assert fit["pooled"] is True
    assert fit2["pooled"] is False


# --------------------------------------------------------------------------- #
# Calibration contract
# --------------------------------------------------------------------------- #
def test_full_mode_refuses_a_placeholder_calibration(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    C.block(c)["paths"]["calibration"] = str(tmp_path / "cal.json")
    C.write_json(tmp_path / "cal.json",
                 {"status": "PLACEHOLDER", "estimates": {}},
                 mode="sample", cfg=cfg)
    with pytest.raises(C.GateRefused, match="not FULL"):
        C.require_calibration_full(c, "full", "04_models")


def test_full_mode_refuses_a_non_estimable_calibration(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    C.block(c)["paths"]["calibration"] = str(tmp_path / "cal.json")
    C.write_json(tmp_path / "cal.json",
                 {"status": "NOT_ESTIMABLE", "estimates": {}},
                 mode="full", cfg=cfg)
    with pytest.raises(C.GateRefused, match="not ESTIMATED"):
        C.require_calibration_full(c, "full", "04_models")


def test_full_mode_accepts_an_estimated_full_calibration(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    C.block(c)["paths"]["calibration"] = str(tmp_path / "cal.json")
    C.write_json(tmp_path / "cal.json",
                 {"status": "ESTIMATED", "estimates": {"start_type": 0.0}},
                 mode="full", cfg=cfg)
    out = C.require_calibration_full(c, "full", "04_models")
    assert out["status"] == "ESTIMATED"


def test_phase2_passes_a_frozen_prereg_and_refuses_missing_full_tracking(cfg):
    mod = load_stage("02_combine_physical_time")
    with pytest.raises(C.GateRefused, match="tracking input missing"):
        mod.main(["--mode", "full"])


# --------------------------------------------------------------------------- #
# End-to-end on the synthetic fixture
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def phase2_run(cfg, fixture_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("phase2")
    cfg2 = copy.deepcopy(cfg)
    b = C.block(cfg2)
    b["paths"]["raw"] = str(fixture_dir)
    b["paths"]["interim"] = str(fixture_dir / "interim")
    b["paths"]["parquet"] = str(fixture_dir / "interim" / "parquet")
    b["paths"]["checkpoints"] = str(fixture_dir / "interim" / "checkpoints")
    b["paths"]["tmp"] = str(fixture_dir / "interim" / "tmp")
    b["files"]["game_tracking_sample"] = "game_tracking.csv"
    b["paths"]["phase2"] = str(out / "phase2")
    b["paths"]["calibration"] = str(out / "calibration.json")
    b["paths"]["run_log"] = str(out / "run_log.csv")
    cfg_path = _write_cfg(tmp_path_factory.mktemp("cfg") / "config.yaml", cfg2)
    mod = load_stage("02_combine_physical_time")
    assert mod.main(["--mode", "sample", "--force", "--config",
                     str(cfg_path)]) == 0
    return out


def test_phase2_writes_every_contract_artefact(phase2_run):
    p = phase2_run / "phase2"
    for name in ("combine_attempts.csv", "combine_attempt_times.csv",
                 "combine_attempt_curves.csv", "physical_time_by_player.csv",
                 "hierarchical_fit.csv", "position_conflicts.csv",
                 "phase2_summary.json"):
        assert (p / name).exists(), name
    assert (phase2_run / "calibration.json").exists()


def test_every_phase2_row_is_stamped(phase2_run):
    for name in ("combine_attempts.csv", "combine_attempt_times.csv",
                 "physical_time_by_player.csv", "hierarchical_fit.csv"):
        rows = list(csv.DictReader(open(phase2_run / "phase2" / name,
                                        encoding="utf-8")))
        assert rows, name
        for r in rows:
            assert C.SAMPLE_STAMP in r["provenance"], (name, r)


def test_per_player_table_schema_and_coverage(phase2_run):
    rows = list(csv.DictReader(open(
        phase2_run / "phase2" / "physical_time_by_player.csv", encoding="utf-8")))
    required = {"nfl_id", "pooling_group", "distance_yd", "n_attempts_used",
                "posterior_mean_s", "posterior_sd_s", "posterior_var_s2",
                "raw_mean_s", "shrinkage_weight", "pooled", "attempt_numbers",
                "primary_analysis_set"}
    assert required <= set(rows[0])
    players = {r["nfl_id"] for r in rows}
    distances = {float(r["distance_yd"]) for r in rows}
    assert len(rows) == len(players) * len(distances)
    # In the fixture every player has two attempts and reaches the in-game range.
    near = [r for r in rows if float(r["distance_yd"]) <= 5.0]
    assert all(float(r["n_attempts_used"]) >= 1 for r in near)
    assert any(float(r["posterior_sd_s"] or 0) > 0 for r in near)
    assert all(r["primary_analysis_set"] == "True" for r in rows)


def test_phase2_summary_records_qc_and_q3(phase2_run):
    s = json.loads((phase2_run / "phase2" / "phase2_summary.json").read_text())
    assert s["combine"]["attempts"] == 20        # 10 players x 2 attempts
    assert s["combine"]["attempts_measured_ok"] == 20
    assert s["onset"]["hz"] == 10.0
    assert s["gap_policy"]["interpolate_max_gap_s"] == 0.5
    assert s["positions_q3"]["edge_attended_db_players"] == 1
    assert s["positions_q3"]["interior_attended_db_players"] == 1
    assert "smallest_detectable_share" in s["design"]
    assert "never use" in s["crosscheck"]["note"]


def test_calibration_is_a_stamped_placeholder(phase2_run):
    cal = json.loads((phase2_run / "calibration.json").read_text())
    assert cal["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert cal["status"] == "PLACEHOLDER"
    assert cal["estimated"] is False
    assert cal["estimates"]["stance"] is None
    assert "forbids" in cal["reason"]


def test_phase2_is_byte_identical_on_rerun(cfg_for_fixture, fixture_dir,
                                           tmp_path):
    cfg2 = copy.deepcopy(cfg_for_fixture)
    b = C.block(cfg2)
    b["paths"]["phase2"] = str(tmp_path / "phase2")
    b["paths"]["calibration"] = str(tmp_path / "calibration.json")
    b["paths"]["run_log"] = str(tmp_path / "run_log.csv")
    cfg_path = _write_cfg(tmp_path / "config.yaml", cfg2)
    mod = load_stage("02_combine_physical_time")
    assert mod.main(["--mode", "sample", "--force", "--config",
                     str(cfg_path)]) == 0
    first = {p.name: _sha(p) for p in (tmp_path / "phase2").iterdir()}
    first["calibration.json"] = _sha(tmp_path / "calibration.json")
    assert mod.main(["--mode", "sample", "--force", "--config",
                     str(cfg_path)]) == 0
    second = {p.name: _sha(p) for p in (tmp_path / "phase2").iterdir()}
    second["calibration.json"] = _sha(tmp_path / "calibration.json")
    assert first == second
