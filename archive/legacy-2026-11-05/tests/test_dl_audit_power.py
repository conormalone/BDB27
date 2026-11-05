"""Phase 1 audit items g-k: formulas, proxy, ICC CI, field detection.

Pure-function tests build tiny in-memory frames and hand-computed answers.
One integration test runs the real items g and h and skips cleanly when
``data/raw`` is absent.  Nothing here writes outside ``tmp_path``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from dl import dl_audit_power as A
from dl import dl_common as D

Z = NormalDist().inv_cdf(1 - 0.05 / 2) + NormalDist().inv_cdf(0.80)


# --------------------------------------------------------------------------- #
# Power formulas — known answers
# --------------------------------------------------------------------------- #
def test_mde_log_hr_matches_closed_form():
    expected = Z / math.sqrt(100)          # design_effect = 1
    assert A.mde_log_hr(100, 0.05, 0.80, 1.0) == pytest.approx(expected, abs=1e-12)
    assert A.mde_hr(100, 0.05, 0.80, 1.0) == pytest.approx(math.exp(expected))


def test_mde_increases_with_design_effect():
    # DE inflates the variance, so it multiplies the SE and the MDE.
    # (The pre-fix blob divided by sqrt(DE) and failed this test.)
    base = A.mde_log_hr(200, 0.05, 0.80, 1.0)
    assert A.mde_log_hr(200, 0.05, 0.80, 1.3) == pytest.approx(
        base * math.sqrt(1.3))
    values = [A.mde_log_hr(200, 0.05, 0.80, de)
              for de in (1.0, 1.3, 2.0, 4.0, 9.0)]
    assert all(b > a for a, b in zip(values, values[1:]))


def test_mde_design_effect_four_is_exactly_double():
    # sqrt(4) = 2: a four-fold variance inflation doubles the MDE.
    de1 = A.mde_log_hr(150, 0.05, 0.80, 1.0)
    de4 = A.mde_log_hr(150, 0.05, 0.80, 4.0)
    assert de4 == pytest.approx(2.0 * de1, rel=1e-12)
    assert A.mde_hr(150, 0.05, 0.80, 4.0) == pytest.approx(
        A.mde_hr(150, 0.05, 0.80, 1.0) ** 2, rel=1e-12)


def test_realised_mde_uses_the_fitted_log_se():
    for se in (0.05, 0.10, 0.1361):
        assert A.mde_log_hr_realised(se, 0.05, 0.80) == pytest.approx(Z * se)
    # Doubling the log-SE doubles the realised MDE (no design effect applied).
    one = A.mde_log_hr_realised(0.07, 0.05, 0.80)
    two = A.mde_log_hr_realised(0.14, 0.05, 0.80)
    assert two == pytest.approx(2.0 * one)
    assert A.mde_hr_realised(0.07, 0.05, 0.80) == pytest.approx(math.exp(one))
    # Degenerate inputs are not silently plausible.
    assert A.mde_log_hr_realised(0.0, 0.05, 0.80) == float("inf")
    assert A.mde_log_hr_realised(float("nan"), 0.05, 0.80) == float("inf")
    assert A.mde_hr_realised(None, 0.05, 0.80) == float("inf")


def test_implied_design_effect_hand_calculation():
    se, events = 0.136, 1135
    expected = (se / (2 / math.sqrt(events))) ** 2
    assert A.implied_design_effect(se, events) == pytest.approx(expected)
    assert A.implied_design_effect(se, events) == pytest.approx(5.24, abs=0.02)
    assert math.isnan(A.implied_design_effect(0.0, events))
    assert math.isnan(A.implied_design_effect(se, 0))
    assert math.isnan(A.implied_design_effect(float("nan"), events))


def test_mde_requires_events():
    assert A.mde_log_hr(0, 0.05, 0.80, 1.3) == float("inf")
    assert A.mde_hr(0, 0.05, 0.80, 1.3) == float("inf")
    with pytest.raises(ValueError):
        A.mde_log_hr(10, 0.05, 0.80, 0.0)


def test_mde_logscore_known_value():
    expected = Z * 0.3 / math.sqrt(400)
    assert A.mde_logscore(0.3, 400, 0.05, 0.80) == pytest.approx(expected)
    assert A.mde_logscore(0.3, 0, 0.05, 0.80) == float("inf")
    # More snaps shrink the detectable difference.
    assert A.mde_logscore(0.3, 4000, 0.05, 0.80) < A.mde_logscore(0.3, 400, 0.05, 0.80)


# --------------------------------------------------------------------------- #
# Attenuation
# --------------------------------------------------------------------------- #
def test_attenuation_is_sqrt_icc_and_monotonic():
    log_hr = 0.05
    for icc in (0.2, 0.4, 0.6, 0.8):
        assert A.attenuation_factor(icc) == pytest.approx(math.sqrt(icc))
    inflated = [A.attenuated_log_hr(log_hr, icc) for icc in (0.2, 0.4, 0.6, 0.8, 1.0)]
    assert all(b < a for a, b in zip(inflated, inflated[1:]))
    assert A.attenuated_log_hr(log_hr, 0.25) == pytest.approx(2 * log_hr)
    assert A.attenuated_log_hr(log_hr, 1.0) == pytest.approx(log_hr)


def test_attenuation_undefined_icc_is_infinite_not_silent():
    assert A.attenuated_log_hr(0.05, None) == float("inf")
    assert A.attenuated_log_hr(0.05, float("nan")) == float("inf")
    assert A.attenuated_log_hr(0.05, 0.0) == float("inf")
    assert math.isnan(A.attenuation_factor(None))


# --------------------------------------------------------------------------- #
# Rusher-blocker field detection and proximity proxy
# --------------------------------------------------------------------------- #
def test_assignment_field_present():
    heads = ["game_id", "nfl_id", "rusher_blocker_id", "block_assignment"]
    assert A.assignment_columns(heads) == ["rusher_blocker_id", "block_assignment"]


def test_assignment_field_absent_and_coverage_rejected():
    heads = ["game_id", "play_id", "nfl_id", "team_abbr", "lined_up_position",
             "coverage_assignment", "coverage_assignment_at_snap",
             "pass_rushers_encountered", "blocked"]
    assert A.assignment_columns(heads) == []


def _snap_frame():
    """One DL and three OL at ball_snap in a single play."""
    return pd.DataFrame({
        "game_id": [1, 1, 1, 1],
        "play_id": [7, 7, 7, 7],
        "nfl_id": [100, 201, 202, 203],
        "x": [0.0, 1.0, 3.0, 0.0],
        "y": [0.0, 0.0, 0.0, 1.5],
    })


def test_proximity_proxy_nearest_ol():
    rows = A.nearest_ol_assignment(_snap_frame(), dl_ids={100},
                                   ol_ids={201, 202, 203}, radius=2.0)
    assert len(rows) == 1
    row = rows[0]
    assert row["nfl_id"] == 100
    assert row["nearest_ol_id"] == 201
    assert row["nearest_ol_dist_yd"] == pytest.approx(1.0)
    assert row["n_ol_within_radius"] == 2      # (1,0) and (0,1.5)
    assert row["assigned"] is True


def test_proximity_proxy_outside_radius_is_unassigned():
    rows = A.nearest_ol_assignment(_snap_frame(), dl_ids={100},
                                   ol_ids={201, 202, 203}, radius=0.5)
    assert rows[0]["assigned"] is False
    assert rows[0]["nearest_ol_dist_yd"] == pytest.approx(1.0)


def test_proximity_proxy_ignores_other_plays():
    frame = _snap_frame()
    extra = pd.DataFrame({"game_id": [1], "play_id": [8], "nfl_id": [202],
                          "x": [0.0], "y": [0.0]})
    rows = A.nearest_ol_assignment(pd.concat([frame, extra], ignore_index=True),
                                   dl_ids={100}, ol_ids={201, 202, 203},
                                   radius=2.0)
    assert rows[0]["nearest_ol_id"] == 201
    assert rows[0]["nearest_ol_dist_yd"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# ICC CI determinism
# --------------------------------------------------------------------------- #
def _planted_groups():
    rng = np.random.default_rng(11)
    groups = {}
    for i in range(10):
        mean = 4.0 + 0.8 * i
        groups[1000 + i] = list(mean + rng.normal(0, 0.15, 3))
    return groups


def test_bootstrap_icc_ci_is_deterministic():
    groups = _planted_groups()
    a = A.bootstrap_icc_ci(groups, reps=60, seed=20270108)
    b = A.bootstrap_icc_ci(groups, reps=60, seed=20270108)
    assert a == b
    assert math.isfinite(a[0]) and math.isfinite(a[1])
    assert 0.0 <= a[0] <= a[1] <= 1.0


def test_bootstrap_icc_ci_needs_two_players():
    lo, hi = A.bootstrap_icc_ci({1: [1.0, 2.0]}, reps=10, seed=1)
    assert math.isnan(lo) and math.isnan(hi)


def test_icc_row_counts_exclusions_and_attenuation():
    groups = {1: [1.0, 1.1, 0.9], 2: [2.0, 2.2, 1.9], 3: [1.5]}  # player 3 has 1
    row = A.icc_row("a0", groups, n_attempts=7, icc_min=0.5, min_attempts=2,
                    reps=20, seed=7)
    assert row["n_players"] == 2
    assert row["n_attempts"] == 6
    assert row["n_excluded"] == 1
    assert row["estimable"] is True
    assert row["attenuation"] == pytest.approx(math.sqrt(float(row["icc"])),
                                                abs=1e-4)
    assert row["meets_icc_min"] is True


def test_icc_row_marks_zero_icc_unattenuable():
    groups = {1: [1.0, 9.0], 2: [5.0, 5.2]}
    row = A.icc_row("x", groups, n_attempts=4, icc_min=0.5, min_attempts=2,
                    reps=10, seed=3)
    assert float(row["icc"]) >= 0.0
    assert A.attenuated_log_hr(0.05, 0.0) == float("inf")


# --------------------------------------------------------------------------- #
# Item k wiring — realised vs prospective MDE
# --------------------------------------------------------------------------- #
def _k_fixture(tmp_path, monkeypatch, events, log_se=None):
    cfg = D.load_config()
    out = tmp_path / "audit"
    out.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    if log_se is not None:
        (models / "part1_result.json").write_text(
            json.dumps({"rungs": [{"used": True, "converged": True,
                                   "estimate": {"log_se": log_se}}]}),
            encoding="utf-8")
    monkeypatch.setattr(A, "_snaps_stats",
                        lambda *a, **k: {"n_snaps": 4000, "events": events})
    res = A.item_k(cfg, "sample", out,
                   {"outputs_dir": out, "models_dir": models}, {"a0": 1.0})
    rows = pd.read_csv(out / "k_power.csv").set_index("quantity")
    return cfg, res, rows


def test_item_k_realised_mde_drives_the_status(tmp_path, monkeypatch):
    cfg, res, rows = _k_fixture(tmp_path, monkeypatch, events=1135,
                                log_se=0.136)
    pblk = D.dl(cfg)["power"]
    alpha, power = float(D.dl(cfg)["audit"]["alpha"]), 0.80
    # Both bases are reported; the realised one is the fitted-SE number.
    assert {"part1_mde_hr_prospective", "part1_mde_hr",
            "part1_mde_hr_realised"} <= set(rows.index)
    assert rows.loc["part1_mde_hr_realised", "estimate"] == pytest.approx(
        A.mde_hr_realised(0.136, alpha, power), rel=1e-4)
    assert rows.loc["part1_design_effect_configured", "estimate"] == \
        pytest.approx(float(pblk["design_effect"]))
    assert rows.loc["part1_design_effect_implied", "estimate"] == \
        pytest.approx(5.25, abs=0.05)
    # Realised MDE (HR ~1.46) exceeds audit.max_mde_hr (1.15) → FAIL, said
    # plainly rather than reworded into a pass.
    assert rows.loc["part1_mde_hr_realised", "finding_status"] == A.FAIL
    assert res["status"] == A.FAIL
    assert any("underpowered at this sample size" in n for n in res["notes"])


def test_item_k_without_a_fit_reports_realised_as_not_run(tmp_path,
                                                          monkeypatch):
    _cfg, res, rows = _k_fixture(tmp_path, monkeypatch, events=3000,
                                 log_se=None)
    assert rows.loc["part1_mde_hr_realised", "finding_status"] == A.NOT_RUN
    assert pd.isna(rows.loc["part1_mde_hr_realised", "estimate"])  # no guess
    assert any("realised not run" in str(n) for n in res["notes"])


# --------------------------------------------------------------------------- #
# Integration (skips without data/raw)
# --------------------------------------------------------------------------- #
def test_integration_items_g_and_h(tmp_path):
    cfg = D.load_config()
    raw = D.resolve_paths(cfg, "sample")["raw"]
    if not Path(raw).exists() or not any(Path(raw).glob("*.csv")):
        pytest.skip("data/raw absent")

    res = A.run_items(cfg, "sample", {"outputs_dir": tmp_path},
                      items=("g", "h"))
    assert set(res) == {"g", "h"}
    for letter in ("g", "h"):
        assert res[letter]["status"] in (A.PASS, A.FAIL, A.PENDING, A.NOT_RUN)
        assert res[letter]["artefacts"], letter
        assert Path(res[letter]["artefacts"][0]).exists()
        assert isinstance(res[letter]["notes"], list)

    g_rows = pd.read_csv(tmp_path / "g_assignments.csv")
    assert set(g_rows["finding_status"]) >= {A.PASS}
    assert not (g_rows["finding_status"] == A.FAIL).any()
    h_rows = pd.read_csv(tmp_path / "h_units.csv")
    assert (h_rows["check"] == "sampling_dt").any()
