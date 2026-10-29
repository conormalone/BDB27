"""Acceptance tests for Phase 5 validation (D66) and the wired `06_validation` stage.

The TASK.md Phase-6 requirement is that the *wired* stage, not just the library,
must remove a spurious confounded effect.  These tests therefore:

* run the confounded synthetic world through `src/06_validation.py` (CLI contract,
  provenance writers, checkpoint) and show the orthogonalised estimate collapses;
* require a small permutation p on a planted world and no systematic small p on a
  null world;
* pin determinism under a fixed seed;
* pin the declared sensitivity grid and garbage-time rule to config;
* check the edge cases report rather than crash;
* pin `fit_design` to `lc_models.fit_within_player` so there is one estimator.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import lc_common as C
import lc_efforts as E
import lc_models as M
import lc_validation as V
import synthetic_lc_fixture as F

from conftest import load_script, write_config


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _efforts(world_dir, cfg):
    pp = pd.read_csv(world_dir / "player_play.csv")
    tr = pd.read_csv(world_dir / "game_tracking.csv")
    pl = pd.read_csv(world_dir / "players.csv")
    table, _ = E.extract_efforts(pp, tr, pl, cfg=cfg)
    return table


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    root = tmp_path_factory.mktemp("validation_worlds") / "lc"
    F.generate(root, seed=20270107)
    return root


@pytest.fixture(scope="module")
def planted(worlds, cfg):
    return _efforts(worlds / "game", cfg)


@pytest.fixture(scope="module")
def null_world(worlds, cfg):
    return _efforts(worlds / "game" / "null", cfg)


@pytest.fixture(scope="module")
def confounded_strong(tmp_path_factory, cfg):
    """The confounded world with a confound strong enough to fire in the
    confirmatory specification (default fixture: beta_time=-0.06, corr=0.85)."""
    root = tmp_path_factory.mktemp("validation_confounded") / "lc"
    F.generate(root, seed=20270107, confounded_beta_time=-0.18,
               confounded_corr=0.9)
    return _efforts(root / "game" / "confounded", cfg)


# --------------------------------------------------------------------------- #
# One estimator, not two
# --------------------------------------------------------------------------- #
def test_design_estimator_is_lc_models_fit_within_player(planted, cfg):
    controls = tuple(C.lc(cfg)["models"]["controls"])
    design = V.build_design(planted, "log_max_speed", "leverage_z", controls, cfg=cfg)
    fast = V.fit_design(design, design["leverage"])
    slow = M.fit_within_player(planted, "log_max_speed", "leverage_z", controls,
                               cfg=cfg)
    assert fast["n"] == slow["n"]
    assert fast["beta"] == pytest.approx(slow["beta"], abs=1e-10)
    assert fast["se"] == pytest.approx(slow["se"], abs=1e-10)


# --------------------------------------------------------------------------- #
# Permutation
# --------------------------------------------------------------------------- #
def test_permutation_finds_planted_effect_and_not_a_null(planted, null_world, cfg):
    found = V.permutation_test(planted, cfg, "player_game", reps=200)
    assert found["status"] == "ok"
    assert found["blocks"] > 40
    assert found["observed"]["beta"] == pytest.approx(0.05, abs=0.02)
    assert found["p_value"] < 0.01
    assert len(found["null"]) == 200
    assert np.isfinite(found["null"]).all()

    absent = V.permutation_test(null_world, cfg, "player_game", reps=200)
    assert absent["status"] == "ok"
    assert absent["p_value"] > 0.05, "a true null must not fire at 5%"


def test_permutation_blocks_are_within_player_and_within_family(planted, cfg):
    within_game = V.permutation_test(planted, cfg, "player_game", reps=20)
    within_family = V.permutation_test(planted, cfg, "route_family", reps=20)
    assert within_game["block_columns"] == ["nfl_id", "game_id"]
    assert within_family["block_columns"] == ["nfl_id", "route_family"]

    # The block count is exactly the number of >=2-row groups under that key;
    # singleton groups cannot be shuffled and are reported separately.
    def expected(keys):
        sizes = planted.groupby(keys).size()
        return int((sizes >= 2).sum()), int(sizes[sizes == 1].sum())

    game_blocks, game_singletons = expected(["nfl_id", "game_id"])
    fam_blocks, fam_singletons = expected(["nfl_id", "route_family"])
    assert within_game["blocks"] == game_blocks
    assert within_game["singleton_rows"] == game_singletons
    assert within_family["blocks"] == fam_blocks
    assert within_family["singleton_rows"] == fam_singletons
    # both schemes are within-player: no block ever spans two players
    assert within_family["blocks"] <= planted["nfl_id"].nunique() * \
        planted["route_family"].nunique()


def test_permutation_is_deterministic_under_a_fixed_seed(planted, cfg):
    first = V.permutation_test(planted, cfg, "player_game", reps=100, seed=11)
    again = V.permutation_test(planted, cfg, "player_game", reps=100, seed=11)
    other = V.permutation_test(planted, cfg, "player_game", reps=100, seed=12)
    assert first["p_value"] == again["p_value"]
    np.testing.assert_array_equal(first["null"], again["null"])
    assert not np.array_equal(first["null"], other["null"])


def test_missing_block_columns_is_a_config_error(planted, cfg):
    broken = C.load_config()
    broken["bdb27_lc"]["validation"]["permutations"]["block_columns"] = {}
    with pytest.raises(ValueError, match="block_columns"):
        V.permutation_test(planted, broken, "player_game", reps=5)


# --------------------------------------------------------------------------- #
# Orthogonalisation
# --------------------------------------------------------------------------- #
def test_planted_effect_survives_orthogonalisation(planted, cfg):
    check = V.orthogonalisation_check(planted, cfg)
    assert check["status"] == "ok"
    assert check["orthogonalised"]["beta"] == pytest.approx(0.05, abs=0.02)
    assert check["effect_survives"] is True


def test_strong_confound_is_removed_by_orthogonalisation(confounded_strong, cfg):
    check = V.orthogonalisation_check(confounded_strong, cfg)
    naive, orth = check["naive"], check["orthogonalised"]
    assert naive["beta"] < -0.012 and naive["p"] < 0.05, naive
    assert abs(orth["beta"]) < 0.005, orth
    assert orth["p"] > 0.3, orth
    assert check["attenuation_fraction"] > 0.6


# --------------------------------------------------------------------------- #
# Garbage time
# --------------------------------------------------------------------------- #
def test_garbage_time_rule_matches_the_frozen_config(cfg):
    rule = C.lc(cfg)["leverage"]["garbage_time"]
    table = pd.DataFrame({
        "quarter": [1, 1, 4, 4, 5, 2],
        "score_margin": [rule["any_quarter_margin"] - 1,
                         rule["any_quarter_margin"],
                         rule["q4_margin"] - 1,
                         rule["q4_margin"],
                         rule["q4_margin"],
                         rule["any_quarter_margin"]],
    })
    mask, recorded = V.garbage_time_mask(table, cfg)
    assert mask.tolist() == [False, True, False, True, True, True]
    assert recorded["q4_margin"] == rule["q4_margin"]
    assert recorded["any_quarter_margin"] == rule["any_quarter_margin"]


def test_garbage_time_margins_cannot_be_overridden(cfg):
    broken = C.load_config()
    broken["bdb27_lc"]["validation"]["garbage_time"]["q4_margin"] = 10
    with pytest.raises(RuntimeError, match="exactly one place"):
        V.garbage_time_mask(pd.DataFrame({"quarter": [4], "score_margin": [30]}),
                            broken)


def test_missing_garbage_time_columns_is_reported_not_silently_false(cfg):
    table = pd.DataFrame({"log_max_speed": [1.0], "leverage_z": [0.0],
                          "nfl_id": [1]})
    result = V.garbage_time_check(table, cfg)
    assert result["status"].startswith("garbage-time rule not evaluable")
    assert result["n_garbage_time"] is None


# --------------------------------------------------------------------------- #
# Declared sensitivity grid
# --------------------------------------------------------------------------- #
def test_sensitivity_grid_is_the_declared_one(cfg):
    block = C.lc(cfg)["validation"]["sensitivity"]
    specs = V.sensitivity_grid(cfg)
    assert specs[0]["spec"] == "primary" and specs[0]["primary"] is True
    names = [spec["spec"] for spec in specs]
    assert names.count("primary") == 1
    # every non-primary, non-labelled spec is traceable to a configured list
    declared = set()
    declared |= {f"leverage={d}" for d in block["leverage_definitions"]}
    declared |= {f"bin_cutoff={c}" for c in block["bin_cutoffs"]}
    declared |= {f"effort_threshold={float(t):g}yd" for t in block["effort_thresholds"]}
    declared |= {f"min_efforts={int(m)}" for m in block["min_efforts_grid"]}
    declared |= {f"ceiling_feature={f}" for f in block["ceiling_features"]}
    fixed = {"primary", "exclude_final_two_minutes", "time_to_throw_iqr",
             "matching_tolerances"}
    for name in names:
        assert name in declared or name in fixed, f"invented spec {name!r}"
    # the primary effort threshold is the effort stage's own constant, not a new one
    assert float(block["effort_thresholds"][0]) == C.lc(cfg)["efforts"]["min_route_depth_yd"]
    assert float(block["effort_thresholds"][1]) == \
        C.lc(cfg)["efforts"]["min_route_depth_yd_sensitivity"]
    assert int(block["min_efforts_grid"][1]) == C.lc(cfg)["gate"]["min_efforts_per_player"]


def test_high_vs_low_spec_is_a_bin_contrast(planted, cfg):
    spec = next(s for s in V.sensitivity_grid(cfg) if s["spec"] == "bin_cutoff=quartile")
    frame = V.apply_spec(planted, spec, cfg)
    assert set(frame["_leverage_high_vs_low"].unique()) == {0.0, 1.0}
    rows, _ = V.sensitivity_curve(planted, cfg)
    row = next(r for r in rows if r["spec"] == "bin_cutoff=quartile")
    assert row["comparison"] == "high_vs_low"
    assert row["beta_per_within_player_sd"] is None


# --------------------------------------------------------------------------- #
# Equivalence
# --------------------------------------------------------------------------- #
def test_equivalence_verdicts(cfg):
    informative = V.equivalence_test(0.0, 0.002, 60, cfg)
    assert informative["equivalent_at_margin"] is True
    assert informative["null_is_informative"] is True
    assert informative["verdict"].startswith("informative null")

    vague = V.equivalence_test(0.0, 0.02, 60, cfg)
    assert vague["excludes_smallest_effect"] is False
    assert vague["verdict"].startswith("uninformative null")

    detected = V.equivalence_test(0.05, 0.005, 60, cfg)
    assert detected["verdict"].startswith("effect detected")
    assert detected["null_is_informative"] is False

    undefined = V.equivalence_test(float("nan"), 0.0, 60, cfg)
    assert undefined["status"] == "undefined"


def test_equivalence_rejects_drift_from_the_power_block(cfg):
    broken = C.load_config()
    broken["bdb27_lc"]["validation"]["equivalence"]["margin"] = 0.05
    with pytest.raises(RuntimeError, match="disagrees"):
        V.equivalence_test(0.0, 0.002, 60, broken)


# --------------------------------------------------------------------------- #
# Edge cases report, never crash
# --------------------------------------------------------------------------- #
def _toy_table(n_players: int = 6, per_player: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    rows = []
    for player in range(n_players):
        for i in range(per_player):
            rows.append({
                "nfl_id": 100 + player,
                "game_id": 1 + (i // 3),
                "log_max_speed": float(rng.normal(2.0, 0.05)),
                "leverage_z": float(rng.normal()),
                "route_family": ["GO", "HITCH", "CROSS"][i % 3],
                "break_found": bool(i % 2),
                "break_depth_yd": float(5 + i) if i % 2 else np.nan,
                "time_to_throw_s": 2.5,
                "prior_efforts": float(i),
                "quarter": 1,
                "score_margin": 3,
                "down": 1,
                "yards_to_go": 10,
                "game_elapsed_s": 60.0 * i,
                "leverage_bin": ["low", "medium", "high"][i % 3],
            })
    return pd.DataFrame(rows)


def test_edge_cases_report_rather_than_crash(cfg):
    table = _toy_table()
    # one single-effort player, one zero-variance player
    single = table.iloc[[0]].copy()
    single["nfl_id"] = 999
    constant = table[table["nfl_id"] == 100].copy()
    constant["nfl_id"] = 998
    constant["leverage_z"] = 0.0
    table = pd.concat([table, single, constant], ignore_index=True)

    result = V.run_validation(table, cfg, "sample", primary=None, perm_reps=20)
    assert result["stage"] == "06_validation"
    assert result["edge_cases"]["n_single_effort_players"] == 1
    assert result["edge_cases"]["n_players_zero_leverage_variance"] == 1
    assert result["equivalence"]["status"] == "primary model result absent"
    assert result["permutation"]["schemes"]["player_game"]["status"] == "ok"


def test_all_garbage_time_is_labelled(cfg):
    table = _toy_table()
    table["quarter"] = 4
    table["score_margin"] = 30
    garbage = V.garbage_time_check(table, cfg)
    assert garbage["status"] == "ok"
    assert garbage["n_garbage_time"] == len(table)
    assert garbage["without_garbage_time"]["status"] == "all efforts are garbage time"
    assert np.isnan(garbage["without_garbage_time"]["beta"])


# --------------------------------------------------------------------------- #
# The WIRED stage: confounded world through 06_validation.py
# --------------------------------------------------------------------------- #
def _prepare_run(tmp_path, tmp_cfg, table, reps: int = 200):
    """Write an efforts table + a 05_models result into a temp run root."""
    stage_a = load_script("05_models")
    tmp_cfg["bdb27_lc"]["validation"]["permutations"]["reps"] = reps
    cfg_path = write_config(tmp_path / "config.yaml", tmp_cfg)
    loaded = C.load_config(cfg_path)

    out_eff = tmp_path / "outputs" / "game" / "lc" / "efforts"
    out_mod = tmp_path / "outputs" / "game" / "lc" / "models"
    out_eff.mkdir(parents=True, exist_ok=True)
    out_mod.mkdir(parents=True, exist_ok=True)

    cleaned = table.drop(columns=[c for c in ("provenance",) if c in table.columns])
    C.write_csv(out_eff / "efforts.csv", cleaned.to_dict("records"),
                list(cleaned.columns), mode="sample", cfg=loaded)

    summary = stage_a.fit_and_summarise(table, loaded, "sample")
    summary.pop("_coefficients", None)
    C.write_json(out_mod / "model_result.json", summary, mode="sample", cfg=loaded)
    return cfg_path, out_eff / "efforts.csv"


def test_confounded_world_through_the_wired_stage(tmp_path, tmp_cfg,
                                                  confounded_strong):
    cfg_path, efforts_path = _prepare_run(tmp_path, tmp_cfg, confounded_strong,
                                          reps=200)
    stage = load_script("06_validation")
    assert stage.main(["--mode", "sample", "--config", str(cfg_path)]) == 0

    out = tmp_path / "outputs" / "game" / "lc" / "validation"
    result = json.loads((out / "validation_result.json").read_text())
    assert result["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert result["permutation"]["reps"] == 200
    assert (out / "permutation_null.csv").exists()
    assert (out / "SUMMARY.md").exists()

    naive = result["orthogonalisation"]["naive"]
    orth = result["orthogonalisation"]["orthogonalised"]
    assert naive["beta"] < -0.012 and naive["p"] < 0.05, naive
    assert abs(orth["beta"]) < 0.005, orth
    assert orth["p"] > 0.3, orth
    assert result["orthogonalisation"]["attenuation_fraction"] > 0.6

    # the whole null distribution was written, not just its summary
    nulls = pd.read_csv(out / "permutation_null.csv")
    assert len(nulls) == 2 * 200
    assert set(nulls["scheme"].unique()) == {"player_game", "route_family"}

    # sample artefact is stamped on every row
    assert set(nulls["provenance"].unique()) == {
        "PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT"}

    # a second run is a checkpoint skip, not a silent recompute
    assert stage.main(["--mode", "sample", "--config", str(cfg_path)]) == 0


def test_stage_refuses_non_full_efforts_in_full_mode(tmp_path, tmp_cfg,
                                                     confounded_strong):
    cfg_path, efforts_path = _prepare_run(tmp_path, tmp_cfg, confounded_strong,
                                          reps=10)
    stage = load_script("06_validation")
    loaded = C.load_config(cfg_path)
    # the table on disk is stamped sample; asking for full mode must refuse it
    with pytest.raises(RuntimeError, match="not \\['FULL'\\]"):
        stage.load_efforts(loaded, "full")
    # and the stage's own provenance gate fires before reading anything
    with pytest.raises(RuntimeError, match="provenance"):
        stage.main(["--mode", "full", "--config", str(cfg_path)])


def test_stage_requires_the_primary_model_result(tmp_path, tmp_cfg):
    cfg_path = write_config(tmp_path / "config.yaml", tmp_cfg)
    stage = load_script("06_validation")
    loaded = C.load_config(cfg_path)
    with pytest.raises(RuntimeError, match="05_models"):
        stage.load_primary(loaded, "sample")
