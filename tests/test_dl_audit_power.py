"""Phase 1 audit items g-k: formulas, proxy, ICC CI, field detection.

Pure-function tests build tiny in-memory frames and hand-computed answers.
One integration test runs the real items g and h and skips cleanly when
``data/raw`` is absent.  Nothing here writes outside ``tmp_path``.
"""

from __future__ import annotations

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


def test_mde_hr_includes_design_effect():
    # Literal spec form: (z_a + z_b) / (sqrt(E) * sqrt(design_effect)).
    expected = math.exp(Z / math.sqrt(100 * 1.3))
    assert A.mde_hr(100, 0.05, 0.80, 1.3) == pytest.approx(expected)
    assert A.mde_hr(100, 0.05, 0.80, 2.0) == pytest.approx(
        math.exp(Z / math.sqrt(200)))


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
