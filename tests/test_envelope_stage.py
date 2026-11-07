"""Stage 03 — the real envelope stage on synthetic turn tables.

Every test below runs the **actual** stage in a subprocess:
``python src/03_envelope.py --mode sample --config <tmp> --force`` on a turn
table written to ``<tmp>/outputs/turns/turns.parquet``. Nothing is mocked except
in the two unit-level tests that deliberately break the method (H, I).

Worlds are kept small (20-40 players, 5-40 turns each) and the bootstrap / CV
replicates are overridden through the temp config so the suite stays quick.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
import yaml

from bdb27 import common, envelope

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

FLOAT_COLUMNS = [
    "entry_speed", "exit_speed", "apex_speed", "turn_angle", "abs_turn_angle",
    "speed_retention", "regain_entry_speed_s", "tangential_accel_entry",
    "tangential_accel_exit", "peak_lateral_accel_yd_s2", "lateral_accel_v2_over_r",
]
SMOOTHER_COLUMNS = [
    "peak_lateral_accel_smoother_min", "peak_lateral_accel_smoother_max",
    "speed_retention_smoother_min", "speed_retention_smoother_max",
]


# --------------------------------------------------------------------- worlds

def _turn_row(*, setting="combine", nfl_id, name, group, apex, demand,
              retention=0.95, regain=0.35, plant=True, valid=True,
              turn_type=None, smoother=True):
    row = {
        "setting": setting,
        "nfl_id": np.int64(nfl_id),
        "player_name": name,
        "position": group,
        "position_group": group,
        "entry_speed": np.float32(apex + 0.6),
        "exit_speed": np.float32((apex + 0.6) * retention),
        "apex_speed": np.float32(apex),
        "turn_angle": np.float32(60.0),
        "abs_turn_angle": np.float32(60.0),
        "speed_retention": np.float32(retention),
        "regain_entry_speed_s": np.float32(regain),
        "regain_exhausted": np.bool_(False),
        "tangential_accel_entry": np.float32(0.0),
        "tangential_accel_exit": np.float32(0.0),
        "peak_lateral_accel_yd_s2": np.float32(demand),
        "lateral_accel_v2_over_r": np.float32(demand),
        "turn_type": turn_type or ("plant" if plant else "arc"),
        "plant": np.bool_(plant),
        "valid": np.bool_(valid),
    }
    if smoother:
        row["peak_lateral_accel_smoother_min"] = np.float32(demand)
        row["peak_lateral_accel_smoother_max"] = np.float32(demand)
        row["speed_retention_smoother_min"] = np.float32(retention)
        row["speed_retention_smoother_max"] = np.float32(retention)
        row["n_smoothers_matched"] = np.int16(3)
    return row


def zero_world(n_players=20, n_turns=12, smoother=True):
    """Identical players: y = 7.0 + 0.4*(v-4), same speed grid for everyone."""
    speeds = np.linspace(2.0, 7.0, n_turns)
    rows = []
    for j in range(n_players):
        for v in speeds:
            rows.append(_turn_row(
                nfl_id=1000 + j, name=f"P{j}", group="WR", apex=float(v),
                demand=7.0 + 0.4 * (v - 4.0), smoother=smoother))
    return pd.DataFrame(rows)


def hetero_world(n_players=40, n_turns=40, sd=1.0, seed=7, noise=0.05,
                 smoother=True):
    """Between-player capacity SD = ``sd`` yd/s^2, small within-player noise."""
    rng = np.random.default_rng(seed)
    speeds = np.linspace(2.0, 7.0, n_turns)
    a0 = rng.normal(7.0, sd, n_players)
    rows = []
    for j in range(n_players):
        for v in speeds:
            demand = a0[j] + 0.4 * (v - 4.0) + rng.normal(0.0, noise)
            rows.append(_turn_row(
                nfl_id=1000 + j, name=f"P{j}", group="WR", apex=float(v),
                demand=float(demand), smoother=smoother))
    return pd.DataFrame(rows), a0


def write_turns(tmp: Path, turns: pd.DataFrame) -> Path:
    out = tmp / "outputs" / "turns" / "turns.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    turns.to_parquet(out, index=False)
    return out


def write_config(tmp: Path, **envelope_overrides) -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    block = cfg["turn_coverage"]
    for key in ("outputs", "envelope", "audit", "figures", "parquet",
                "checkpoints", "tmp"):
        block["paths"][key] = str(tmp / key)
    block["paths"]["turns"] = str(tmp / "outputs" / "turns")
    block["paths"]["envelope"] = str(tmp / "outputs" / "envelope")
    block["paths"]["run_log"] = str(tmp / "run_log.csv")
    block["paths"]["thresholds"] = str(tmp / "thresholds.json")
    overrides = {
        "bootstrap_reps": 20,
        "tau2_quantile_bootstrap_reps": 50,
        "tau2_bootstrap_reps": 50,
        "tau2_heldout_folds": 4,
    }
    overrides.update(envelope_overrides)
    for key, value in overrides.items():
        block["envelope"][key] = value
    path = tmp / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def run_stage(cfg_path: Path, mode="sample") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SRC / "03_envelope.py"), "--mode", mode,
         "--config", str(cfg_path), "--force"],
        capture_output=True, text=True, cwd=str(ROOT))


def run_world(tmp: Path, turns: pd.DataFrame, **overrides):
    write_turns(tmp, turns)
    cfg_path = write_config(tmp, **overrides)
    result = run_stage(cfg_path)
    assert result.returncode == 0, result.stderr + result.stdout
    out = tmp / "outputs" / "envelope"
    return {
        "tmp": tmp, "cfg": cfg_path, "result": result, "dir": out,
        "manifest": json.loads((out / "manifest.json").read_text()),
        "variance": pd.read_csv(out / "variance_components.csv"),
        "fit": pd.read_csv(out / "envelope_fit.csv"),
        "players": pd.read_parquet(out / "envelope_players.parquet"),
        "stdout": result.stdout,
    }


def spearman(x, y) -> float:
    return float(pd.Series(np.asarray(x)).rank().corr(pd.Series(np.asarray(y)).rank()))


# ------------------------------------------------------------------ fixtures

@pytest.fixture(scope="module")
def zero_stage(tmp_path_factory):
    return run_world(tmp_path_factory.mktemp("zero"), zero_world())


@pytest.fixture(scope="module")
def hetero_stage(tmp_path_factory):
    turns, planted = hetero_world(sd=1.0)
    stage = run_world(tmp_path_factory.mktemp("hetero"), turns)
    stage["planted"] = planted
    return stage


@pytest.fixture(scope="module")
def disagree_stage(tmp_path_factory):
    # Large spread: the moment estimate is clearly INTERIOR (> 1) while the
    # held-out loss is minimised at 0, because a new player's random effect is
    # unknown and pooled estimation targets the marginal quantile (D150).
    turns, planted = hetero_world(sd=1.3)
    stage = run_world(tmp_path_factory.mktemp("disagree"), turns)
    stage["planted"] = planted
    return stage


# ------------------------------------------------------ A. zero heterogeneity

def test_zero_world_has_exactly_zero_tau2_and_at_boundary(zero_stage):
    variance = zero_stage["variance"]
    for measure in ("arc_capacity", "plant_retention"):
        sub = variance[variance.measure == measure]
        assert len(sub) == 1
        assert float(sub.tau2.iloc[0]) == 0.0
        assert sub.tau2_status.iloc[0] == "AT_BOUNDARY"
        assert float(sub.tau2_se.iloc[0]) == 0.0
    # AT_BOUNDARY is a reported finding, not an error: the stage exits 0 and
    # says so.
    assert "AT_BOUNDARY" in zero_stage["stdout"]


def test_zero_world_does_not_manufacture_player_differences(zero_stage):
    players = zero_stage["players"]
    players = players[players.setting == "combine"]
    assert len(players) == 20
    assert np.ptp(players.capacity_at_4.to_numpy()) < 1e-9
    assert np.ptp(players.retention_ref.to_numpy()) < 1e-9
    assert bool(players.tau2_at_boundary.all())


# -------------------------------------------------- B. large heterogeneity

def test_large_heterogeneity_recovers_between_player_sd(hetero_stage):
    # Tolerance note: J=40 gives a sampling SD of Var(q_j) of about
    # Var*sqrt(2/(J-1)) ~ 0.23, so a +/-0.6 band on the recovered SD is roughly
    # 2 SE. D144: the Phase-2 fixture's Bezier corners demand ~1.27x nominal
    # capacity, so a fixture-derived absolute level gate would be ordering-only;
    # this test plants the turn table directly, so the level gate is meaningful.
    component = hetero_stage["variance"]
    component = component[component.measure == "arc_capacity"].iloc[0]
    sd = float(np.sqrt(float(component.tau2)))
    assert 0.6 <= sd <= 1.6, f"recovered between-player SD {sd:.3f} outside [0.6, 1.6]"
    assert component.tau2_status == "INTERIOR"


def test_large_heterogeneity_preserves_player_ordering(hetero_stage):
    players = hetero_stage["players"]
    players = players[players.setting == "combine"].sort_values("nfl_id")
    rho = spearman(players.capacity_at_4.to_numpy(), hetero_stage["planted"])
    assert rho >= 0.8, f"Spearman between estimated capacity_at_4 and planted a0 is {rho}"


# -------------------------------------------------------- C. disagreement world

def test_disagreement_world_reports_both_values_and_flags(disagree_stage):
    variance = disagree_stage["variance"]
    component = variance[variance.measure == "arc_capacity"].iloc[0]
    assert float(component.tau2) > 1.0
    assert component.tau2_status == "INTERIOR"
    assert component.routes_agree is False or str(component.routes_agree) == "False"
    assert pd.notna(component.tau2_heldout_optimal)

    manifest = disagree_stage["manifest"]
    assert manifest["variance"]["n_disagreements"] >= 1
    records = [c for c in manifest["variance"]["components"]
               if c["measure"] == "arc_capacity"]
    assert records and records[0]["routes_agree"] is False

    # Both values are printed; neither is silently chosen.
    assert "moment_tau2=" in disagree_stage["stdout"]
    assert "heldout_optimal=" in disagree_stage["stdout"]
    assert "DISAGREEMENT" in disagree_stage["stdout"]

    players = disagree_stage["players"]
    assert any("variance_route_disagreement" in str(flags)
               for flags in players["flags"])


# ------------------------------------------------------------- D. edge cases

def test_single_turn_player_is_excluded_counted_and_flagged(tmp_path):
    speeds = np.linspace(2.0, 7.0, 6)
    rows = []
    for j in range(10):
        for v in speeds:
            rows.append(_turn_row(nfl_id=2000 + j, name=f"P{j}", group="WR",
                                  apex=float(v), demand=7.0 + 0.4 * (v - 4.0)))
    rows.append(_turn_row(nfl_id=2999, name="ONE", group="WR", apex=4.5,
                          demand=8.0))
    turns = pd.DataFrame(rows)
    stage = run_world(tmp_path, turns)
    component = stage["variance"]
    component = component[component.measure == "arc_capacity"].iloc[0]
    assert int(component.n_players) == 10
    assert int(component.n_players_excluded) >= 1
    one = stage["players"][stage["players"].nfl_id == 2999]
    assert len(one) == 1
    assert "insufficient_turns" in str(one["flags"].iloc[0])


def test_no_plant_turns_warns_and_still_exits_zero(tmp_path):
    speeds = np.linspace(2.0, 7.0, 5)
    rows = []
    for j in range(6):
        for v in speeds:
            rows.append(_turn_row(
                nfl_id=3000 + j, name=f"P{j}", group="WR", apex=float(v),
                demand=7.0 + 0.4 * (v - 4.0), plant=False, retention=float("nan"),
                regain=float("nan")))
    turns = pd.DataFrame(rows)
    stage = run_world(tmp_path, turns)
    assert any("no plant turns" in w for w in stage["manifest"]["warnings"])
    assert stage["manifest"]["settings_fitted"] == ["combine"]
    flags = set(";".join(stage["players"]["flags"].astype(str)).split(";"))
    assert "no_plant_turns" in flags
    assert "all_arc" in flags
    feasible = pd.read_parquet(stage["dir"] / "feasible_region.parquet",
                               columns=["setting"])
    assert len(feasible) == 0


def test_single_setting_is_not_double_counted_as_combined(zero_stage):
    assert zero_stage["manifest"]["settings_fitted"] == ["combine"]


def test_missing_smoother_columns_warns_but_still_fits(tmp_path):
    stage = run_world(tmp_path, zero_world(n_players=6, smoother=False))
    assert stage["manifest"]["smoother_spread_available"] is False
    assert stage["manifest"]["missing_smoother_columns"]
    assert any("missing_smoother_spread" in str(f)
               for f in stage["players"]["flags"])
    assert len(stage["fit"]) > 0


# ----------------------------------------------------------- E. determinism

def test_repeated_force_runs_are_byte_identical(tmp_path):
    turns = zero_world(n_players=8)
    write_turns(tmp_path, turns)
    cfg_path = write_config(tmp_path)
    first = run_stage(cfg_path)
    assert first.returncode == 0, first.stderr
    out = tmp_path / "outputs" / "envelope"
    parquet_first = (out / "envelope_players.parquet").read_bytes()
    fit_first = (out / "envelope_fit.csv").read_bytes()
    second = run_stage(cfg_path)
    assert second.returncode == 0, second.stderr
    assert (out / "envelope_players.parquet").read_bytes() == parquet_first
    assert (out / "envelope_fit.csv").read_bytes() == fit_first


# ------------------------------------------------------ F. full-mode refusal

def test_full_mode_refuses_and_names_thresholds_and_prereg(tmp_path):
    cfg_path = write_config(tmp_path)
    result = run_stage(cfg_path, mode="full")
    assert result.returncode != 0
    text = result.stderr + result.stdout
    assert "thresholds" in text
    assert "prereg" in text
    assert not (tmp_path / "outputs" / "envelope").exists()


# ------------------------------------------------- G. artefact contract

def test_artifact_schema_and_provenance(zero_stage):
    out = zero_stage["dir"]
    table = pq.read_table(out / "envelope_players.parquet")
    assert table.column_names == [f.name for f in envelope.ENVELOPE_PLAYER_SCHEMA]
    metadata = table.schema.metadata or {}
    for key in (b"provenance", b"label", b"config_sha"):
        assert key in metadata

    manifest = zero_stage["manifest"]
    assert manifest["provenance"] == "PLACEHOLDER_SAMPLE"
    assert manifest["artefact_version"] == 2
    variance = manifest["variance"]
    for key in ("method", "formula", "floor", "route", "crosscheck",
                "tau2_quantile_bootstrap_reps", "tau2_min_turns_per_player",
                "tau2_bootstrap_reps", "grid_scale_sd", "grid_sd_fractions",
                "heldout_folds", "agree_tol_sd_frac", "n_components",
                "n_at_boundary", "n_disagreements", "components"):
        assert key in variance, f"manifest variance block missing {key}"
    assert variance["method"] == "moment_matched"
    assert manifest["estimator"]["name"] == "fixed_tau2_penalised_quantile_regression"
    assert manifest["estimator"]["posterior"] is False
    assert manifest["estimator"]["uncertainty"] == "player_weighted_bootstrap"

    csv = pd.read_csv(out / "variance_components.csv")
    for column in envelope.VARIANCE_COLUMNS:
        assert column in csv.columns
    assert csv.tau2_status.notna().all()

    fit = zero_stage["fit"]
    assert fit.tau2_status.notna().all()
    for key in ("tau2", "tau2_se", "tau2_p05", "tau2_p95", "tau2_status",
                "tau2_route", "tau2_heldout_optimal", "routes_agree",
                "tau2_source_measure"):
        assert key in fit.columns


# ------------------------------------------------- H. the must-fail check

def _assert_zero_world(result) -> None:
    variance = result.variance
    capacity = variance[variance.measure == "arc_capacity"]
    assert len(capacity) == 1
    assert float(capacity.tau2.iloc[0]) == 0.0
    assert capacity.tau2_status.iloc[0] == "AT_BOUNDARY"
    retention = variance[variance.measure == "plant_retention"]
    assert float(retention.tau2.iloc[0]) == 0.0
    assert retention.tau2_status.iloc[0] == "AT_BOUNDARY"


def test_zero_world_assertion_fails_when_the_moment_is_broken(tmp_path, monkeypatch):
    cfg_path = write_config(tmp_path)
    cfg = common.load_config(cfg_path)
    turns = zero_world()

    honest = envelope.Envelope(turns, cfg).run()
    _assert_zero_world(honest)  # the honest path passes first

    monkeypatch.setattr(envelope, "_moment_tau2",
                        lambda q, s: (0.75, "INTERIOR"))
    broken = envelope.Envelope(turns, cfg).run()
    with pytest.raises(AssertionError):
        _assert_zero_world(broken)


# ------------------------------------------------------------- I. no EM path

def test_fit_runs_exactly_one_irls_solve(monkeypatch):
    rng = np.random.default_rng(0)
    players = np.repeat(np.arange(10), 4)
    x = rng.normal(size=len(players))
    X = np.column_stack([np.ones(len(players)), x])
    y = 5.0 + 0.5 * x + rng.normal(0.0, 0.2, len(players))
    calls = {"n": 0}
    original = envelope._solve_re_irls

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(envelope, "_solve_re_irls", counted)
    fit = envelope.fit_re_quantile(
        X, y, players, tau=0.75, tau2=0.5, irls_max_iter=20, tol=1e-6,
        obj_tol=1e-7, eps=1e-6)
    assert calls["n"] == 1
    assert fit.tau2 == 0.5


def test_production_fits_never_report_em_not_settled(zero_stage, hetero_stage):
    for stage in (zero_stage, hetero_stage):
        assert "em_not_settled" not in set(stage["fit"].stop_reason.astype(str))
