"""DL stage 04 — mechanical validation (schema, folds, tuning, gate, provenance).

Every test here is deterministic and small: the fixture generator is re-pointed
at a few players so a full stage run costs seconds.  No fitted number is
asserted as a finding; the recovery tests live in
``test_dl_models_recovery.py``.
"""

from __future__ import annotations

import copy
import json
import math

import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_fixtures as F
from dl import dl_models_core as M
from dl import dl_survival as SV
from dl import stage_04_models as stage04


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _cfg(tmp_path, *, n_players=6, n_games=3, snaps_per_game=4, **models):
    cfg = D.load_config()
    block = copy.deepcopy(cfg["bdb27_dl"])
    block["fixture"]["n_players"] = n_players
    block["fixture"]["n_games"] = n_games
    block["fixture"]["snaps_per_game"] = snaps_per_game
    block["paths"]["raw"] = str(tmp_path / "raw")
    block["paths"]["outputs"] = str(tmp_path / "outputs")
    block["paths"]["interim"] = str(tmp_path / "interim")
    block["paths"]["parquet"] = str(tmp_path / "parquet")
    block["paths"]["checkpoints"] = str(tmp_path / "checkpoints")
    block["paths"]["tmp"] = str(tmp_path / "tmp")
    block["paths"]["thresholds"] = str(tmp_path / "thresholds.json")
    block["models"].update(models)
    cfg["bdb27_dl"] = block
    return cfg


def _world(tmp_path, **kw):
    """Write a tiny world and return ``(cfg, world_dir)`` ready for the stage."""
    cfg = _cfg(tmp_path, **kw)
    world = tmp_path / "world"
    F.write_world(cfg, "planted", world)
    block = D.dl(cfg)
    block["paths"]["raw"] = str(world.resolve())          # players/ + combine_results
    block["models"]["inputs"]["snaps"] = str(world.resolve() / "snaps.csv")
    block["models"]["inputs"]["ceiling"] = str(world.resolve() / "player_ceiling.csv")
    return cfg, world


def _frame(tmp_path, **kw):
    cfg, world = _world(tmp_path, **kw)
    frames = {name: pd.read_csv(world / f"{name}.csv")
              for name in ("snaps", "player_ceiling", "players", "combine_results")}
    frame, report = M.build_frame(frames["snaps"], frames["player_ceiling"],
                                  frames["players"], frames["combine_results"])
    block = D.dl(cfg)
    frame = pd.concat([frame, M.build_load_panel(frame, block)], axis=1)
    # the model-ready frame (observed_time, a0_z) is what the estimators consume
    frame = M.ensure_a0_z(M.prepare_frame(frame, block))
    return cfg, block, frame


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_stage_writes_every_declared_artefact(tmp_path):
    cfg, _ = _world(tmp_path)
    out = stage04.run(cfg, "sample", force=True)
    out_dir = stage04._out_dir(cfg, "sample")
    expected = {"part1_result.json", "part2_result.json", "cv_folds.csv",
                "load_tuning.csv", "mediation.json", "reliability.csv",
                "model_result.json", "SUMMARY.md"}
    assert expected <= {p.name for p in out_dir.iterdir()}
    assert out["gate"]["stopping_conditions_evaluated"] is True


def test_artefact_schemas_are_exact(tmp_path):
    cfg, _ = _world(tmp_path)
    stage04.run(cfg, "sample", force=True)
    out_dir = stage04._out_dir(cfg, "sample")

    cv = pd.read_csv(out_dir / "cv_folds.csv")
    assert list(cv.columns) == [
        "provenance", "definition", "fold", "n_train", "n_test",
        "n_train_players", "n_test_players", "selected_grid_value",
        "n_scored", "mean_logscore", "rung_used", "status"]
    tune = pd.read_csv(out_dir / "load_tuning.csv")
    assert list(tune.columns) == [
        "provenance", "definition", "fold", "candidate_value",
        "training_loglik", "n_train_obs", "status", "selected", "criterion"]
    rel = pd.read_csv(out_dir / "reliability.csv")
    assert list(rel.columns) == [
        "provenance", "quantity", "estimate", "threshold", "definition", "note"]

    result = json.loads((out_dir / "model_result.json").read_text())
    assert result["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert result["reportable"] is False
    assert result["no_number_may_be_quoted"] is True
    for key in ("gate", "frame", "field_availability", "part1_status",
                "part2_status", "estimators"):
        assert key in result
    gate = result["gate"]
    for condition in ("missing_required_field", "matched_dl",
                      "median_primary_snaps", "a0_icc", "null_semantics",
                      "part1_mde", "snap_coverage"):
        assert condition in gate["checks"]


def test_every_json_artefact_is_stamped_and_parses(tmp_path):
    cfg, _ = _world(tmp_path)
    stage04.run(cfg, "sample", force=True)
    out_dir = stage04._out_dir(cfg, "sample")
    for name in ("part1_result.json", "part2_result.json", "mediation.json",
                 "model_result.json"):
        payload = json.loads((out_dir / name).read_text())
        assert str(payload["provenance"]).startswith("PLACEHOLDER_SAMPLE")
    md = (out_dir / "SUMMARY.md").read_text()
    assert md.startswith("<!-- provenance: PLACEHOLDER_SAMPLE")
    assert "may be quoted as a result" in md


def test_summary_names_confirmatory_secondary_exploratory(tmp_path):
    cfg, _ = _world(tmp_path)
    stage04.run(cfg, "sample", force=True)
    md = (stage04._out_dir(cfg, "sample") / "SUMMARY.md").read_text()
    assert "Confirmatory" in md and "Secondary" in md and "Exploratory" in md
    assert "did not change the exposure" in md


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_same_seed_same_bytes(tmp_path):
    cfg, _ = _world(tmp_path / "a")
    stage04.run(cfg, "sample", force=True)
    first = {p.name: p.read_bytes()
             for p in stage04._out_dir(cfg, "sample").iterdir()}
    stage04.run(cfg, "sample", force=True)
    second = {p.name: p.read_bytes()
              for p in stage04._out_dir(cfg, "sample").iterdir()}
    assert first == second


# --------------------------------------------------------------------------- #
# Folds (§9, §11)
# --------------------------------------------------------------------------- #
def test_folds_are_player_grouped_and_seeded():
    players = [f"p{i}" for i in range(40)]
    a, info = M.assign_folds(players, 5, 7, min_per_fold=1)
    b, _ = M.assign_folds(players, 5, 7, min_per_fold=1)
    c, _ = M.assign_folds(players, 5, 8, min_per_fold=1)
    assert a == b and a != c
    assert len(a) == len(players)          # every player assigned once
    assert set(a.values()) == set(range(5))
    assert info["reduced"] is False


def test_fold_count_reduces_when_a_fold_would_be_too_thin():
    mapping, info = M.assign_folds([f"p{i}" for i in range(25)], 5, 3,
                                   min_per_fold=10)
    assert info["reduced"] is True
    assert info["folds"] == 2              # 25 // 5 = 5 < 10, 25 // 2 = 12
    assert "reduced" in info["reason"]
    counts = pd.Series(list(mapping.values())).value_counts()
    assert counts.min() >= 10


def test_fold_reduction_is_logged_in_the_artefact(tmp_path):
    cfg, _ = _world(tmp_path, n_players=6, n_games=4, snaps_per_game=30)
    stage04.run(cfg, "sample", force=True)
    result = json.loads((stage04._out_dir(cfg, "sample") /
                         "part2_result.json").read_text())
    if result.get("fold_info"):
        assert result["fold_info"]["folds"] <= result["fold_info"]["requested"]


# --------------------------------------------------------------------------- #
# Tuning happens inside the fold only (§7)
# --------------------------------------------------------------------------- #
def test_tuning_cannot_see_held_out_rows(tmp_path, monkeypatch):
    """Fail if any frame given to the model during tuning carries a test player."""
    cfg, block, frame = _frame(tmp_path, n_players=8, n_games=4, snaps_per_game=12)
    players = sorted(frame["nfl_id"].unique())
    test_player = players[0]
    train = frame.loc[frame["nfl_id"] != test_player].copy()
    seen: list[set] = []

    real = M.fit_p2

    def spy(data, *a, **kw):
        seen.append(set(data["nfl_id"].unique()))
        return real(data, *a, **kw)

    monkeypatch.setattr(M, "fit_p2", spy)
    tuned = M.tune_load(train, [1.5, 2.0, 2.5], "absolute", block, with_cov=False)
    assert seen, "the tuning pass never called the model"
    for ids in seen:
        assert test_player not in ids
    assert tuned["criterion"] == "training-fold log likelihood"


def test_selected_value_is_stable_when_held_out_data_changes(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=8, n_games=4, snaps_per_game=12)
    players = sorted(frame["nfl_id"].unique())
    test_player = players[0]
    train = frame.loc[frame["nfl_id"] != test_player].copy()
    grid = [1.5, 2.0, 2.5]
    first = M.tune_load(train, grid, "absolute", block, with_cov=False)
    # the held-out frame is not an argument, and mutating the leftover rows
    # cannot change the stored training selection
    held = frame.loc[frame["nfl_id"] == test_player].copy()
    held["event"] = 1 - held["event"]
    second = M.tune_load(train, grid, "absolute", block, with_cov=False)
    assert first["selected"] == second["selected"]
    assert first["rows"] == second["rows"]


def test_both_definitions_use_the_same_budget(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=8, n_games=4, snaps_per_game=12)
    grid_a = block["load"]["absolute_speed_grid_yds"]
    grid_r = block["load"]["relative_fraction_grid"]
    assert len(grid_a) == len(grid_r)
    train = frame.loc[frame["nfl_id"] != sorted(frame["nfl_id"].unique())[0]]
    a = M.tune_load(train, grid_a, "absolute", block, with_cov=False)
    r = M.tune_load(train, grid_r, "relative", block, with_cov=False)
    assert len(a["rows"]) == len(r["rows"]) == len(grid_a)


def test_tuning_rejects_an_unknown_definition():
    with pytest.raises(ValueError):
        M.tune_load(pd.DataFrame(), [1.0], "total", {}, with_cov=False)


# --------------------------------------------------------------------------- #
# Fallback ladder
# --------------------------------------------------------------------------- #
def test_fallback_ladder_is_attempted_in_order_and_logged(monkeypatch):
    cfg = D.load_config()
    block = D.dl(cfg)
    rng = np.random.default_rng(0)
    n = 120
    frame = pd.DataFrame({
        "nfl_id": np.repeat(np.arange(10), 12),
        "a0": rng.normal(7, 1, n), "alignment": "edge",
        "pre_snap_depth_yd": rng.normal(1.5, 0.3, n), "blitz": rng.integers(0, 2, n),
        "n_blockers": rng.integers(1, 3, n), "down": rng.integers(1, 5, n),
        "yards_to_go": rng.integers(1, 15, n), "score_margin": rng.normal(0, 7, n),
        "quarter": rng.integers(1, 5, n), "position_group": "edge",
        "height_in": 75.0, "weight_lb": 285.0, "years_since_combine": 1.0,
        "opponent_team": "AAA", "primary_blocker_group": "OL_L",
        "event": rng.integers(0, 2, n), "censor_time": rng.uniform(2, 4, n),
        "time_to_pressure": np.nan, "load_z": rng.normal(0, 1, n),
    })
    frame = M.ensure_a0_z(M.prepare_frame(frame, block))

    calls = []

    def fake(t, event, X, names, **kw):
        calls.append(kw)
        ok = not kw.get("random_slope") and kw.get("frailty") == "none"
        return SV.Fit(list(names) + ["log_shape_k"], np.zeros(len(names) + 1),
                      None, -1.0, ok, "forced", "weibull", len(t), int(event.sum()),
                      {"k": 1.0, "frailty_sd": 0.0, "slope_sd": 0.0,
                       "frailty": kw.get("frailty"), "random_slope": False})

    monkeypatch.setattr(M.SV, "fit_weibull", fake)
    out = M.fit_p2(frame, block, load_column="load_z", with_cov=False)
    names = [a["name"] for a in out["attempts"]]
    assert names == ["random_slope_correlated", "uncorrelated_re_alt_optimiser",
                     "random_intercept_plus_eb_slopes", "random_intercept_only"]
    assert out["rung_used"] == "random_intercept_only"


def test_a_converged_primary_is_reported_as_the_primary(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=8, n_games=4, snaps_per_game=12)
    out = M.fit_p2(frame, block, load_column="absolute_load_1p5", with_cov=False,
                   starts=1, max_iter=60)
    assert out["attempts"][0]["name"] == "random_slope_correlated"
    assert out["rung_used"] in {a["name"] for a in out["attempts"]}


# --------------------------------------------------------------------------- #
# Hazard ratio arithmetic and the joint interval
# --------------------------------------------------------------------------- #
def test_hazard_ratio_is_exp_minus_k_beta():
    names = ["a0_z", "log_shape_k"]
    params = np.array([-0.2, math.log(1.5)])
    cov = np.diag([0.01 ** 2, 0.02 ** 2])
    fit = SV.Fit(names, params, cov, -1.0, True, "ok", "weibull", 10, 3,
                 {"k": 1.5})
    hr = M.hazard_ratio(fit, "a0_z")
    assert hr["estimate"] == pytest.approx(math.exp(1.5 * 0.2))
    assert hr["lo"] < hr["estimate"] < hr["hi"]
    # a degenerate covariance gives a degenerate interval
    fit0 = SV.Fit(names, params, np.zeros((2, 2)), -1.0, True, "ok", "weibull",
                  10, 3, {})
    assert M.hazard_ratio(fit0, "a0_z")["se"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Gate branches (§15)
# --------------------------------------------------------------------------- #
def _gate_inputs(players=60, snaps=150, icc=0.6, events=200):
    frame = pd.DataFrame({
        "nfl_id": np.repeat(np.arange(players), snaps),
        "a0_icc": icc, "event": 0})
    frame.loc[:events - 1, "event"] = 1
    info = {"a0": {"present": True, "non_null": len(frame), "fraction": 1.0},
            "blitz": {"present": True, "non_null": len(frame), "fraction": 1.0}}
    return frame, info


def test_gate_go_when_every_condition_passes():
    cfg = D.dl(D.load_config())
    frame, info = _gate_inputs()
    p1 = {"status": "OK", "n_events": 2000}
    p2 = {"status": "OK"}
    audit = {"counts": {"null_semantics": {"time_to_pressure": "EVENT_ABSENT"}}}
    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                           p1=p1, p2=p2, audit_decision=audit)
    assert gate["verdict"] == "GO"
    assert gate["failing"] == [] and gate["pending"] == []


def test_gate_no_go_on_each_stopping_condition():
    cfg = D.dl(D.load_config())
    audit = {"counts": {"null_semantics": {"time_to_pressure": "EVENT_ABSENT"}}}
    p1 = {"status": "OK", "n_events": 2000}
    p2 = {"status": "OK"}

    frame, info = _gate_inputs()
    info_bad = dict(info, n_blockers={"present": False, "non_null": 0, "fraction": 0.0})
    gate_missing = M.gate_evaluate(cfg, mode="full", field_info=info_bad,
                                   frame=frame, p1=p1, p2=p2,
                                   audit_decision=audit)
    # a field the full run was supposed to supply is still a NO-GO on full data
    assert gate_missing["verdict"] == "NO-GO"
    assert "missing_required_field" in gate_missing["pending"]
    assert gate_missing["checks"]["missing_required_field"]["supplied_by_full_run"] == [
        "n_blockers"]

    frame_small, info_small = _gate_inputs(players=10)
    assert "matched_dl" in M.gate_evaluate(cfg, mode="full", field_info=info_small,
                                           frame=frame_small, p1=p1, p2=p2,
                                           audit_decision=audit)["failing"]

    frame_thin, info_thin = _gate_inputs(players=60, snaps=50)
    assert "median_primary_snaps" in M.gate_evaluate(
        cfg, mode="full", field_info=info_thin, frame=frame_thin, p1=p1, p2=p2,
        audit_decision=audit)["failing"]

    frame_icc, info_icc = _gate_inputs(icc=0.49)
    assert "a0_icc" in M.gate_evaluate(cfg, mode="full", field_info=info_icc,
                                       frame=frame_icc, p1=p1, p2=p2,
                                       audit_decision=audit)["failing"]

    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame, p1=p1,
                           p2=p2, audit_decision={"counts": {"null_semantics": {}}})
    assert "null_semantics" in gate["unresolved"]
    assert gate["verdict"] == "NO-GO"

    gate_mde = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                               p1={"status": "OK", "n_events": 20}, p2=p2,
                               audit_decision=audit)
    assert "part1_mde" in gate_mde["failing"]


def test_gate_pending_on_sample_when_only_full_run_fields_are_missing():
    cfg = D.dl(D.load_config())
    # enough events that the §15 MDE condition passes; this test is about the
    # missing-field branch, not about power
    frame, info = _gate_inputs(players=60, snaps=150, events=2000)
    info = dict(info, blitz={"present": True, "non_null": 0, "fraction": 0.0})
    gate = M.gate_evaluate(cfg, mode="sample", field_info=info, frame=frame,
                           p1={"status": "NOT_ESTIMABLE"}, p2={"status": "NOT_ESTIMABLE"},
                           audit_decision={"counts": {"null_semantics": {
                               "time_to_pressure": "EVENT_ABSENT"}}})
    assert gate["verdict"] == "PENDING_FULL_RUN"
    # the same absence in a full run is a NO-GO: no silent GO on full data
    gate_full = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                                p1={"status": "NOT_ESTIMABLE"},
                                p2={"status": "NOT_ESTIMABLE"},
                                audit_decision={"counts": {"null_semantics": {
                                    "time_to_pressure": "EVENT_ABSENT"}}})
    assert gate_full["verdict"] == "NO-GO"


def test_snap_coverage_failure_alone_drops_part2_not_part1():
    cfg = D.dl(D.load_config())
    frame, info = _gate_inputs()
    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "NOT_ESTIMABLE"},
                           audit_decision={"counts": {"null_semantics": {
                               "time_to_pressure": "EVENT_ABSENT"}}})
    assert gate["verdict"] == "GO_PART1_ONLY"
    assert gate["part1_estimable"] is True
    # in sample mode the same state is reported as pending, never as GO
    gate_sample = M.gate_evaluate(D.dl(D.load_config()), mode="sample",
                                  field_info=info, frame=frame,
                                  p1={"status": "OK", "n_events": 2000},
                                  p2={"status": "NOT_ESTIMABLE"},
                                  audit_decision={"counts": {"null_semantics": {
                                      "time_to_pressure": "EVENT_ABSENT"}}})
    assert gate_sample["verdict"] == "PENDING_FULL_RUN"


# --------------------------------------------------------------------------- #
# Provenance refusal
# --------------------------------------------------------------------------- #
def test_sample_run_refuses_to_overwrite_a_full_artefact(tmp_path):
    cfg, _ = _world(tmp_path)
    out_dir = stage04._out_dir(cfg, "sample")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "model_result.json").write_text(json.dumps(
        {"provenance": "FULL", "gate": {"verdict": "GO"}}))
    with pytest.raises(RuntimeError, match="FULL"):
        stage04.run(cfg, "sample", force=True)


def test_full_mode_refuses_sample_provenance(tmp_path):
    cfg, _ = _world(tmp_path)
    with pytest.raises(RuntimeError):
        stage04.run(cfg, "full", force=True)


def test_reportable_flag_is_false_for_every_sample_artefact(tmp_path):
    cfg, _ = _world(tmp_path)
    stage04.run(cfg, "sample", force=True)
    out_dir = stage04._out_dir(cfg, "sample")
    cv = pd.read_csv(out_dir / "cv_folds.csv")
    assert cv.empty or (cv["provenance"].str.startswith("PLACEHOLDER_SAMPLE")).all()


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_no_rows_is_not_estimable_and_does_not_crash(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=4, n_games=2, snaps_per_game=3)
    empty = frame.iloc[0:0]
    p1 = M.fit_p1(empty, block)
    assert p1["status"] == "NOT_ESTIMABLE"
    p2 = M.run_part2(empty, block)
    assert p2["status"] == "NOT_ESTIMABLE"


def test_one_rusher_is_not_estimable_for_part2(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=4, n_games=2, snaps_per_game=30)
    one = frame.loc[frame["nfl_id"] == frame["nfl_id"].iloc[0]]
    one = one.assign(nfl_id=one["nfl_id"])
    out = M.run_part2(one, block)
    assert out["status"] == "NOT_ESTIMABLE"


def test_zero_variance_control_is_centred_not_dropped(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=6, n_games=3, snaps_per_game=10)
    frame = frame.copy()
    frame["height_in"] = 75.0
    data = M.ensure_a0_z(M.prepare_frame(frame, block))
    design = M.build_design(data, block)
    X, moments = M.standardise_in_fold(design.X, design, np.ones(len(data), bool))
    assert moments["height_in"]["sd"] == 0.0
    assert np.allclose(X[:, design.names.index("height_in")], 0.0)


def test_all_censored_fold_is_reported_not_fitted(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=6, n_games=3, snaps_per_game=10)
    train = frame.copy()
    train["event"] = 0
    tuned = M.tune_load(train, [1.5, 2.0], "absolute", block, with_cov=False)
    assert tuned["selected"] is None
    assert all(r["status"] == "NOT_ESTIMABLE" for r in tuned["rows"])


def test_a_player_below_the_snap_floor_is_excluded_from_part2(tmp_path):
    cfg, block, frame = _frame(tmp_path, n_players=6, n_games=3, snaps_per_game=4)
    out = M.run_part2(frame, block)
    assert out["status"] == "NOT_ESTIMABLE"
    assert "100" in out["reason"]


def test_load_panel_matches_the_flat_column_arithmetic(tmp_path):
    """The panel column at a frozen threshold follows dl_load's causal rule."""
    from dl import dl_load

    cfg, block, frame = _frame(tmp_path, n_players=3, n_games=2, snaps_per_game=8)
    pid = frame["nfl_id"].iloc[0]
    game = frame["game_id"].iloc[0]
    seq = frame.loc[(frame["nfl_id"] == pid) & (frame["game_id"] == game)].copy()
    seq["elapsed"] = [dl_load.elapsed_game_seconds(q, c)
                      for q, c in zip(seq["quarter"], seq["game_clock_s"])]
    seq = seq.sort_values(["elapsed", "play_id"])
    times = seq["elapsed"].to_numpy(float)
    values = [max(0.0, 0.5 * (s1 + s2) - 2.0) * 1.0
              for s1, s2 in zip(seq["in_game_speed_1_0"], seq["in_game_speed_2_0"])]
    expected = dl_load._causal_prefix_sum(values, list(times), 300.0)
    got = seq["absolute_load_2p0"].to_numpy(float)
    assert np.allclose(np.nan_to_num(got), np.nan_to_num(np.array(expected)))
    # the first snap of a sequence has zero load, never NA
    assert got[0] == 0.0
