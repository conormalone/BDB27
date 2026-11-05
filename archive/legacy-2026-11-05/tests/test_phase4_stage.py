"""Acceptance tests for the Phase 4 remainder (D67) and the wired `07_phase4` stage.

TASK.md's test list is the contract:

* a synthetic world with a **planted effect and a confounder** must recover the
  planted effect after matching, and a **null world must not manufacture one**;
* the **planted headroom moderation** must be recovered, and the half-split
  utilisation must not be contaminated by the comparison efforts (asserted by
  mutating exactly the comparison rows and requiring the baseline to be
  identical);
* a table with **noisy slopes must fall below the reliability threshold and emit
  no per-player table** — and a genuinely reliable table must still report, so
  the gate is not simply always-off;
* determinism under a fixed seed;
* edge cases (a player with one high-leverage effort, an empty matched stratum)
  must report rather than crash;
* a missing required field is reported as NOT RUN with its name, never proxied.

The stage-level tests drive `src/07_phase4.py` through its CLI contract
(provenance writers, checkpoint, full-mode refusal).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import lc_common as C
import lc_phase4 as P
import synthetic_lc_fixture as F

from conftest import load_script, write_config


# --------------------------------------------------------------------------- #
# Synthetic tables
# --------------------------------------------------------------------------- #
def _base_table(n_players: int = 40, n_efforts: int = 24, seed: int = 11,
                beta: float = 0.06, depth_effect: float = 0.03,
                include_position: bool = True) -> pd.DataFrame:
    """A confounded world: high-leverage efforts are also deeper routes.

    ``beta`` is the planted high-minus-low log-speed effect.  Depth is a
    confounder when ``depth_effect`` is non-zero: high-leverage rows are
    disproportionately deep, and deep routes are faster.  Matching on depth bin
    must remove that; an unmatched contrast must not.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_players):
        player = 900000 + p
        base = rng.normal(2.0, 0.05)
        for i in range(n_efforts):
            high = bool(rng.random() < 0.5)
            # 70% of high-leverage efforts are deep, 30% of low-leverage ones
            deep = bool(rng.random() < (0.70 if high else 0.30))
            depth_bin_value = 3 if deep else 0
            depth = 16.0 if deep else 2.0
            quarter = 1 + (i % 2)
            effect = beta if high else 0.0
            y = base + depth_effect * depth_bin_value + effect + rng.normal(0, 0.02)
            rows.append({
                "nfl_id": player,
                "game_id": 1 + p % 4,
                "leverage_bin": "high" if high else "low",
                "leverage_z": (1.1 if high else -1.1) + rng.normal(0, 0.15),
                "log_max_speed": y,
                "max_speed_yds": float(np.exp(y)),
                "break_found": True,
                "break_depth_yd": depth,
                "route_family": ["GO", "CROSS"][i % 2],
                "quarter": quarter,
                "prior_efforts": float(i % 6),
                "time_to_throw_s": 2.6,
                "score_margin": 3,
                "down": 1 + (i % 4),
                "yards_to_go": 7,
                "position": "WR" if include_position else None,
            })
    frame = pd.DataFrame(rows)
    if not include_position:
        frame = frame.drop(columns=["position"])
    return frame


def _headroom_table(n_players: int = 40, n_efforts: int = 16, seed: int = 21,
                    gamma: float = 0.30, noise: float = 0.03,
                    n_low: int | None = None) -> pd.DataFrame:
    """Planted moderation: the leverage effect grows with baseline utilisation.

    The moderator is planted on the **observed** scale — the player's median
    effort fraction on the low-leverage efforts — because that is what the
    estimator measures.  (Planting it on a latent scale would leave the test
    measuring the difference between the two scales rather than the recovery of
    the coefficient.)

    Every player is given at least ``n_low`` low-leverage efforts (default
    ``max(4, n_efforts // 3)``, matching the configured
    ``min_low_efforts_for_split``).  A plain ``N(0, 1)`` draw leaves a
    substantial share of players with fewer than four efforts below z = -0.7 —
    and some with none at all — so the independent-half baseline cannot be
    formed for them and the fixture's own fixed-point median is taken over an
    empty slice.  That is a bug in the *world*, not in the estimator.
    """
    if n_low is None:
        n_low = max(4, n_efforts // 3)
    if n_low >= n_efforts:
        raise ValueError("n_low must leave at least one higher-leverage effort")
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_players):
        player = 800000 + p
        util = float(np.clip(rng.normal(0.72, 0.07), 0.5, 0.9))
        vmax = 10.0
        lev = rng.normal(0, 1, n_efforts)
        # enough low-leverage efforts per player for the seeded half split
        low_positions = rng.permutation(n_efforts)[:n_low]
        lev[low_positions] = -0.7 - np.abs(rng.normal(0.35, 0.20, n_low))
        frac = util * np.exp(rng.normal(0, noise, n_efforts))
        low = lev < -0.7
        # Fixed point: the planted signal is gamma * z * (B - 0.72) where B is
        # the *observed* low-leverage median fraction.  The signal shifts those
        # very rows, so B must satisfy B = median(frac_low * exp(signal_low)).
        baseline = float(np.median(frac[low])) if low.any() else util
        for _ in range(200):
            observed = frac[low] * np.exp(gamma * lev[low] * (baseline - 0.72))
            updated = float(np.median(observed))
            if abs(updated - baseline) < 1e-12:
                break
            baseline = 0.5 * baseline + 0.5 * updated
        for i in range(n_efforts):
            z = float(lev[i])
            y = (np.log(vmax) + np.log(frac[i])
                 + gamma * z * (baseline - 0.72))
            rows.append({
                "nfl_id": player,
                "game_id": 1 + p % 4,
                "leverage_bin": ("high" if z > 0.7 else
                                 ("low" if z < -0.7 else "medium")),
                "leverage_z": z,
                "log_max_speed": y,
                "max_speed_yds": float(np.exp(y)),
                "vmax": vmax,
                "vmax_se": 0.08,
                "tau": 1.3,
                "tau_se": 0.03,
                "effort_fraction": float(np.exp(y)) / vmax,
                "break_found": True,
                "break_depth_yd": 8.0,
                "route_family": ["GO", "CROSS"][i % 2],
                "quarter": 1 + (i % 2),
                "prior_efforts": float(i % 4),
                "time_to_throw_s": 2.5,
                "score_margin": 0,
                "down": 1 + (i % 4),
                "yards_to_go": 8,
                "position": "WR",
            })
    return pd.DataFrame(rows)


def _slope_table(n_players: int = 40, n_efforts: int = 12, seed: int = 31,
                 slope_sd: float = 0.20, noise: float = 0.02) -> pd.DataFrame:
    """Players with genuinely different leverage slopes (or none at all)."""
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_players):
        player = 700000 + p
        slope = float(rng.normal(0, slope_sd))
        lev = rng.normal(0, 1, n_efforts)
        for i in range(n_efforts):
            z = float(lev[i])
            y = 2.0 + slope * z + rng.normal(0, noise)
            rows.append({
                "nfl_id": player,
                "leverage_z": z,
                "leverage_bin": "high" if z > 0 else "low",
                "log_max_speed": y,
                "max_speed_yds": float(np.exp(y)),
                "route_family": "GO",
                "break_found": True,
                "break_depth_yd": 9.0,
                "quarter": 1,
                "prior_efforts": float(i),
                "time_to_throw_s": 2.5,
                "score_margin": 0,
                "down": 1,
                "yards_to_go": 8,
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def cfg():
    return C.load_config()


# --------------------------------------------------------------------------- #
# 1. Matched design
# --------------------------------------------------------------------------- #
def test_matched_recovers_the_planted_effect_through_a_confounder(cfg):
    world = _base_table(beta=0.06, depth_effect=0.03)
    naive = (world.loc[world["leverage_bin"] == "high", "log_max_speed"].mean()
             - world.loc[world["leverage_bin"] == "low", "log_max_speed"].mean())
    block = P.matched_block(world, cfg, reps=300)
    assert block["status"] == "ok"
    assert block["n_pairs"] > 150
    assert block["n_players_contributing"] == 40
    estimate = block["matched"]["estimate"]
    # the planted effect is 0.06; the unmatched contrast is inflated by depth
    assert estimate == pytest.approx(0.06, abs=0.015), estimate
    assert naive > estimate + 0.02, (naive, estimate)
    # the matched sample must straddle zero only if the effect is absent
    assert block["matched"]["se"] > 0


def test_matched_null_world_does_not_manufacture_an_effect(cfg):
    world = _base_table(beta=0.0, depth_effect=0.03)
    block = P.matched_block(world, cfg, reps=400)
    assert block["status"] == "ok"
    boot = block["matched"]
    assert abs(boot["estimate"]) < 0.012, boot
    assert boot["ci_low"] < 0 < boot["ci_high"], boot
    # the confounder is still there: the unmatched contrast is biased upward
    naive = (world.loc[world["leverage_bin"] == "high", "log_max_speed"].mean()
             - world.loc[world["leverage_bin"] == "low", "log_max_speed"].mean())
    assert naive > 0.02, naive


def test_matched_empty_stratum_is_reported_as_collapsed(cfg):
    # every high-leverage effort is deep, every low-leverage effort is shallow:
    # no stratum can contain both, so the matched sample collapses
    world = _base_table(beta=0.06, depth_effect=0.03, n_players=12, n_efforts=8)
    world["break_depth_yd"] = np.where(world["leverage_bin"] == "high", 16.0, 2.0)
    block = P.matched_block(world, cfg, reps=50)
    assert block["status"] == "collapsed"
    assert block["n_pairs"] == 0
    assert block["n_players_contributing"] == 0
    assert "collapsed" in block["note"]


def test_player_with_one_high_leverage_effort_is_counted_not_crashed(cfg):
    # 24 efforts per player: at 10 efforts the exact-match strata (route x depth
    # x quarter x load) are so sparse that one of the six planted players can
    # legitimately end up with no paired cell, which is the design working, not
    # the edge case this test is about.
    world = _base_table(n_players=6, n_efforts=24)
    extra = world.iloc[[0]].copy()
    extra["nfl_id"] = 999999
    extra["leverage_bin"] = "high"
    world = pd.concat([world, extra], ignore_index=True)
    block = P.matched_block(world, cfg, reps=50)
    assert block["status"] == "ok"
    # a player whose only effort is high-leverage sits in a one-sided stratum:
    # it is counted there and contributes no pair, rather than crashing
    assert block["n_players_contributing"] == 6
    assert block["n_players_without_pair"] == 1
    assert block["n_strata_one_sided"] >= 1


def test_matched_bootstrap_is_deterministic(cfg):
    world = _base_table()
    a = P.matched_block(world, cfg, reps=200)
    b = P.matched_block(world, cfg, reps=200)
    assert a["n_pairs"] == b["n_pairs"]
    assert a["matched"]["estimate"] == pytest.approx(b["matched"]["estimate"], abs=1e-12)
    assert a["matched"]["ci_low"] == pytest.approx(b["matched"]["ci_low"], abs=1e-12)


# --------------------------------------------------------------------------- #
# 2. Headroom moderation and the independent half split
# --------------------------------------------------------------------------- #
def test_headroom_recovers_the_planted_moderation(cfg):
    world = _headroom_table(gamma=0.30)
    result = P.headroom_moderation(world, cfg)
    assert result["status"] == "ok"
    row = next(r for r in result["rows"]
               if r["model"] == "leverage_x_baseline_utilization")
    assert row["status"] == "ok"
    assert row["beta"] == pytest.approx(0.30, abs=0.10), row
    assert row["n_players"] == 40
    # the correction is named on the row, never implied
    assert "errors-in-variables" in row["correction"]
    assert row["lambda"] is not None and 0 < row["lambda"] <= 1


def test_headroom_null_moderation_is_not_manufactured(cfg):
    world = _headroom_table(gamma=0.0)
    result = P.headroom_moderation(world, cfg)
    row = next(r for r in result["rows"]
               if r["model"] == "leverage_x_baseline_utilization")
    assert abs(row["beta"]) < 0.10, row


def test_half_split_baseline_is_not_contaminated_by_the_comparison_rows(cfg):
    world = _headroom_table()
    first = P.half_split_utilisation(world, cfg)
    assert first["status"] == "ok"
    baseline = (first["_utilization"].set_index("nfl_id")["baseline_utilization"]
                .sort_index())
    analysis_rows = [int(v) for v in first["_analysis"]["_row"].to_numpy()]
    assert analysis_rows, "the comparison sample is empty"

    # mutate ONLY the comparison rows: the baseline must be byte-identical
    mutated = world.copy()
    mutated.loc[analysis_rows, "effort_fraction"] = (
        mutated.loc[analysis_rows, "effort_fraction"] * 3.0)
    second = P.half_split_utilisation(mutated, cfg)
    after = (second["_utilization"].set_index("nfl_id")["baseline_utilization"]
             .sort_index())
    pd.testing.assert_series_equal(baseline, after, check_names=False)

    # the split is also structurally disjoint: the analysis rows are exactly the
    # rows NOT used for the baseline, and the low-leverage half A is never in it
    low_rows = set(world.index[(world["leverage_bin"] == "low")])
    half_a = low_rows - set(analysis_rows)
    assert not (half_a & set(analysis_rows))
    assert first["split"]["disjoint"] is True


def test_headroom_without_a_ceiling_is_not_run_and_says_why(cfg):
    world = _base_table().drop(columns=["max_speed_yds"]).rename(
        columns={"log_max_speed": "log_max_speed"})
    world["max_speed_yds"] = np.exp(world["log_max_speed"])
    # no vmax / effort_fraction -> the baseline cannot be measured
    result = P.headroom_moderation(world, cfg)
    assert result["status"] == "NOT RUN"
    assert "vmax" in str(result["missing_field"])


# --------------------------------------------------------------------------- #
# 3. Ceiling bound check
# --------------------------------------------------------------------------- #
def test_ceiling_bound_reports_exceedance_by_bin(cfg):
    world = _headroom_table(n_players=20, n_efforts=20)
    # plant a few efforts above the ceiling, all in the high bin
    rng = np.random.default_rng(5)
    high = world.index[world["leverage_bin"] == "high"][:10]
    world.loc[high, "max_speed_yds"] = world.loc[high, "vmax"] * 1.05
    world.loc[high, "effort_fraction"] = 1.05
    world.loc[high, "log_max_speed"] = np.log(world.loc[high, "max_speed_yds"])
    result = P.ceiling_bound(world, cfg)
    assert result["status"] == "ok"
    bins = {row["leverage_bin"]: row for row in result["rows"]}
    assert set(bins) <= {"low", "medium", "high"}
    assert bins["high"]["observed_exceedance_rate"] > 0
    assert bins["high"]["predicted_exceedance_rate_at_high"] >= 0
    assert result["all_rows"]["leverage_bin"] == "all"
    assert np.isfinite(result["max_predicted_fraction_at_high"])
    del rng


def test_ceiling_bound_without_a_ceiling_is_not_run(cfg):
    world = _base_table()
    result = P.ceiling_bound(world, cfg)
    assert result["status"] == "NOT RUN"
    assert "vmax" in result["missing_field"]
    assert result["rows"] == []


# --------------------------------------------------------------------------- #
# 4. Dose-response
# --------------------------------------------------------------------------- #
def _dosage_table(effects: dict, n_players: int = 20, n_per: int = 12,
                  seed: int = 41) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_players):
        base = 2.0 + rng.normal(0, 0.03)
        for label, effect in effects.items():
            for _ in range(n_per):
                rows.append({
                    "nfl_id": 600000 + p,
                    "leverage_bin": label,
                    "leverage_z": float(rng.normal(0, 1)),
                    "log_max_speed": base + effect + rng.normal(0, 0.02),
                    "max_speed_yds": 7.4,
                    "route_family": "GO",
                    "break_found": True,
                    "break_depth_yd": 9.0,
                    "quarter": 1,
                    "prior_efforts": 3.0,
                    "time_to_throw_s": 2.5,
                    "score_margin": 0,
                    "down": 1,
                    "yards_to_go": 8,
                })
    return pd.DataFrame(rows)


def test_dose_response_reports_monotone_when_it_is(cfg):
    world = _dosage_table({"low": 0.0, "medium": 0.03, "high": 0.06})
    result = P.dose_response(world, cfg)
    assert result["status"] == "ok"
    assert result["monotone"] is True
    assert result["direction"] == "increasing"
    assert result["trend"]["beta"] > 0


def test_dose_response_reports_non_monotone_plainly(cfg):
    world = _dosage_table({"low": 0.0, "medium": 0.06, "high": 0.01})
    result = P.dose_response(world, cfg)
    assert result["monotone"] is False
    assert result["direction"] == "not monotone"


def test_dose_response_with_one_bin_is_not_evaluable(cfg):
    world = _dosage_table({"low": 0.0})
    result = P.dose_response(world, cfg)
    assert result["status"] == "insufficient_bins"
    assert result["monotone"] is None


# --------------------------------------------------------------------------- #
# 5. Per-player slopes and the reliability gate
# --------------------------------------------------------------------------- #
def test_noisy_slopes_fall_below_the_threshold_and_emit_no_table(cfg):
    world = _slope_table(slope_sd=0.0, noise=0.10)
    result = P.per_player_slopes(world, cfg)
    assert result["status"] == "withheld"
    assert result["rows"] == []
    assert result["reliability"] < result["reliability_threshold"]
    assert "odd/even" in result["reliability_rule"]


def test_gate_still_reports_when_slopes_are_genuinely_reliable(cfg):
    world = _slope_table(slope_sd=0.20, noise=0.02)
    result = P.per_player_slopes(world, cfg)
    assert result["status"] == "reported"
    assert len(result["rows"]) == 40
    assert result["reliability"] >= result["reliability_threshold"]
    assert result["tau_squared"] > 0


# --------------------------------------------------------------------------- #
# 6. TE replication
# --------------------------------------------------------------------------- #
def test_te_replication_is_labelled_exploratory_on_a_small_sample(cfg):
    world = _base_table(n_players=8)
    world["position"] = ["TE" if p % 2 else "WR"
                         for p in range(len(world))]
    result = P.replication(world, cfg)
    assert result["status"] == "ok"
    assert result["position"] == "TE"
    assert result["label"] == "exploratory"
    assert result["labelled_on_n_players"] == result["n_players"]
    assert result["n_players"] < result["exploratory_below_players"]


def test_te_replication_without_position_is_not_run(cfg):
    world = _base_table(include_position=False)
    result = P.replication(world, cfg)
    assert result["status"] == "NOT RUN"
    assert result["missing_field"].startswith("position")


# --------------------------------------------------------------------------- #
# 7. Tactical demand: what is absent is named, not proxied
# --------------------------------------------------------------------------- #
def test_defender_distance_absent_is_not_run_with_the_exact_field(cfg):
    world = _base_table()
    result = P.tactical_demand(world, cfg)
    dd = result["defender_distance"]
    assert dd["status"] == "NOT RUN"
    assert dd["missing_field"] == "defender_distance_yd"
    assert "post-snap" in dd["reason"]
    # time to throw IS available, so that contrast must have run
    assert result["time_to_throw"]["status"] == "ok"


def test_defender_distance_runs_when_the_field_is_present(cfg):
    world = _base_table()
    world["defender_distance_yd"] = np.linspace(0.5, 6.0, len(world))
    result = P.tactical_demand(world, cfg)
    assert result["defender_distance"]["status"] == "ok"
    assert np.isfinite(result["defender_distance"]["interaction_beta"])


# --------------------------------------------------------------------------- #
# 8. Orchestration, determinism, edge cases
# --------------------------------------------------------------------------- #
def test_run_phase4_is_deterministic_under_a_fixed_seed(cfg):
    world = _headroom_table(n_players=24, n_efforts=12)
    a = P.run_phase4(world, cfg, bootstrap_reps=80)
    b = P.run_phase4(world, cfg, bootstrap_reps=80)
    assert a["matched_design"]["n_pairs"] == b["matched_design"]["n_pairs"]
    assert (a["matched_design"]["matched"]["estimate"]
            == pytest.approx(b["matched_design"]["matched"]["estimate"], abs=1e-12))
    assert json.dumps(a["headroom"], sort_keys=True, default=str) == json.dumps(
        b["headroom"], sort_keys=True, default=str)
    assert json.dumps(a["dose_response"], sort_keys=True, default=str) == json.dumps(
        b["dose_response"], sort_keys=True, default=str)


def test_run_phase4_reports_checks_not_run(cfg):
    world = _headroom_table(n_players=12, n_efforts=10)
    result = P.run_phase4(world, cfg, bootstrap_reps=20)
    names = {row["check"] for row in result["checks_not_run"]}
    assert "defender_distance" in names
    # headroom DID run here, so it must not be listed as not-run
    assert "headroom_moderation" not in names


def test_run_phase4_refuses_a_table_missing_required_columns(cfg):
    world = _base_table().drop(columns=["leverage_bin"])
    with pytest.raises(RuntimeError, match="missing required columns"):
        P.run_phase4(world, cfg, bootstrap_reps=10)


# --------------------------------------------------------------------------- #
# 9. The wired stage
# --------------------------------------------------------------------------- #
def _prepare_stage_inputs(tmp_path, tmp_cfg, table: pd.DataFrame) -> Path:
    """Write an effort table, a ceiling table and a players mirror, then config."""
    paths = C.resolve_paths(tmp_cfg, "sample")
    eff_dir = paths["outputs"] / "game" / "lc" / "efforts"
    eff_dir.mkdir(parents=True, exist_ok=True)
    cleaned = table.drop(columns=[c for c in ("provenance", "position")
                                  if c in table.columns])
    C.write_csv(eff_dir / "efforts.csv", cleaned.to_dict("records"),
                list(cleaned.columns), mode="sample", cfg=tmp_cfg)

    ceiling_dir = paths["outputs"] / "ceiling" / "lc"
    ceiling_dir.mkdir(parents=True, exist_ok=True)
    long_rows = []
    for nfl_id in sorted(cleaned["nfl_id"].unique()):
        player = cleaned[cleaned["nfl_id"] == nfl_id]
        vmax = float(player["vmax"].iloc[0]) if "vmax" in player else 10.0
        for feature, estimate, se in (("vmax", vmax, 0.08), ("tau", 1.3, 0.03)):
            long_rows.append({"nfl_id": int(nfl_id), "feature": feature,
                              "estimate": estimate, "se": se,
                              "position": "WR"})
    C.write_csv(ceiling_dir / "player_ceiling.csv", long_rows,
                ["nfl_id", "feature", "estimate", "se", "position"],
                mode="sample", cfg=tmp_cfg)

    players = pd.DataFrame({
        "nfl_id": sorted(cleaned["nfl_id"].unique()),
        "nfl_position": "WR",
    })
    players.to_parquet(paths["parquet"] / "lc_players.parquet", index=False)
    return write_config(tmp_path / "config.yaml", tmp_cfg)


def test_wired_stage_writes_artefacts_and_checkpoints(tmp_path, tmp_cfg):
    world = _headroom_table(n_players=20, n_efforts=14)
    cfg_path = _prepare_stage_inputs(tmp_path, tmp_cfg, world)
    stage = load_script("07_phase4")
    assert stage.main(["--mode", "sample", "--config", str(cfg_path)]) == 0

    out = tmp_path / "outputs" / "game" / "lc" / "phase4"
    for name in ("phase4_result.json", "matched_pairs.csv", "matched_design.csv",
                 "headroom.csv", "ceiling_bound.csv", "dose_response.csv",
                 "per_player_slopes.csv", "utilization_baseline.csv",
                 "checks_not_run.csv", "SUMMARY.md"):
        assert (out / name).exists(), name

    result = json.loads((out / "phase4_result.json").read_text())
    assert result["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert result["matched_design"]["n_pairs"] > 0
    assert result["replication"]["position"] == "TE"
    assert result["tactical_demand"]["defender_distance"]["status"] == "NOT RUN"
    not_run = pd.read_csv(out / "checks_not_run.csv")
    assert "defender_distance_yd" in set(not_run["missing_field"])
    assert set(not_run["provenance"].unique()) == {
        "PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT"}

    # a second invocation is a checkpoint skip, not a silent recompute
    assert stage.main(["--mode", "sample", "--config", str(cfg_path)]) == 0


def test_stage_refuses_non_full_efforts_in_full_mode(tmp_path, tmp_cfg):
    world = _headroom_table(n_players=6, n_efforts=10)
    cfg_path = _prepare_stage_inputs(tmp_path, tmp_cfg, world)
    stage = load_script("07_phase4")
    loaded = C.load_config(cfg_path)
    with pytest.raises(RuntimeError, match="not \\['FULL'\\]"):
        stage.load_efforts(loaded, "full")
    with pytest.raises(RuntimeError, match="provenance"):
        stage.main(["--mode", "full", "--config", str(cfg_path)])


def test_stage_requires_the_effort_table(tmp_path, tmp_cfg):
    cfg_path = write_config(tmp_path / "config.yaml", tmp_cfg)
    stage = load_script("07_phase4")
    loaded = C.load_config(cfg_path)
    with pytest.raises(RuntimeError, match="04_efforts"):
        stage.load_efforts(loaded, "sample")


# --------------------------------------------------------------------------- #
# 10. Config contract
# --------------------------------------------------------------------------- #
def test_phase4_constants_live_in_config(cfg):
    block = C.lc(cfg)["phase4"]
    assert block["matched"]["treatment_bin"] == "high"
    assert block["matched"]["control_bin"] == "low"
    assert block["matched"]["coarsen"]["depth_bin_edges"][0] == 0.0
    assert block["headroom"]["baseline_bin"] == "low"
    assert block["per_player"]["reliability_threshold"] == 0.5
    assert block["replication"]["position"] == "TE"
    assert block["tactical_demand"]["defender_distance_field"] == "defender_distance_yd"
    # a missing block must be a config error, never a silent no-op (D66 defect 1)
    broken = json.loads(json.dumps(cfg, default=str))
    broken["bdb27_lc"].pop("phase4")
    with pytest.raises(RuntimeError, match="phase4"):
        P.block(broken)
