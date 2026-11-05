"""Power calculation: the 50 ms jump SD, its floor, and the frozen prereg gate.

The statistical tests use synthetic player-level data with a planted jump SD.
The design tests use the committed Phase 3b evidence (provenance FULL) and lock
the evidence the prereg reports as a resolution statement: no candidate
selection cut resolves 50 ms. The prereg uses no confirmatory threshold.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from conftest import ROOT
import common as C
import power as P
import run as RUN
import stats as ST


# --------------------------------------------------------------------------- #
# Estimator algebra
# --------------------------------------------------------------------------- #
def test_share_of_variance_is_the_squared_sd_ratio():
    """50 ms against a 0.1004 s between-player SD is 24.8% of the variance."""
    share = ST.variance_share_from_sd(0.050, 0.1004)
    assert share == pytest.approx((0.050 / 0.1004) ** 2)
    assert share == pytest.approx(0.248, abs=0.001)
    assert ST.sd_from_variance_share(share, 0.1004) == pytest.approx(0.050)


def test_players_required_inverts_the_floor():
    share = ST.variance_share_from_sd(0.050, 0.1004)
    required = ST.players_required_for_share(share)
    assert required == 125
    # P = 125 is the first integer whose floor does not exceed the share;
    # 124 is the last that fails.
    assert ST.min_detectable_share(required)["min_share"] <= share
    assert ST.min_detectable_share(required - 1)["min_share"] > share


def test_effective_players_matches_the_phase3b_kish_count():
    """The pure stats copy and the numpy reliability copy must agree."""
    import reliability as REL
    rels = [0.9, 0.9, 0.8, 0.5, 1.0]
    assert ST.effective_players(rels) == pytest.approx(REL.effective_players(rels))


def test_detectability_at_125_players_resolves_50ms():
    """At P_eff = 125 the floor is just below 50 ms; at 124 it is above."""
    at_125 = P.detectability(0.050, 0.1004, 125.0)
    at_124 = P.detectability(0.050, 0.1004, 124.0)
    assert at_125["detectable"] is True
    assert at_125["smallest_detectable_jump_sd_s"] <= 0.050
    assert at_124["detectable"] is False
    assert at_124["smallest_detectable_jump_sd_s"] > 0.050


def test_table_cut_rows_use_fittable_players_only():
    players = [
        {"reps": 100, "reliability": 1.0, "has_forty_attempt": True},
        {"reps": 100, "reliability": 1.0, "has_forty_attempt": False},
        {"reps": 5, "reliability": 0.9, "has_forty_attempt": True},
    ]
    row = P.summarise_cut(players, 20, between_sd_s=0.1004,
                          jump_sd_s=0.050, z=1.96)
    assert row["players_admitted"] == 2
    assert row["players_fittable"] == 1
    # One fittable player cannot answer the power question; the row must say so
    # rather than invent a floor.
    assert math.isnan(row["smallest_detectable_jump_sd_ms_effective"])


# --------------------------------------------------------------------------- #
# Synthetic recovery
# --------------------------------------------------------------------------- #
def test_planted_jump_sd_is_recovered_within_tolerance():
    """200 players, 100 reps, planted jump SD 50 ms, within SD 250 ms.

    The one-way method-of-moments estimator used throughout the repo must
    recover the planted between-player SD. This is the statistical quantity the
    power table turns into a floor, so it is tested before the floor is trusted.
    """
    rng = random.Random(11)
    tau, sigma, n_players, n_reps = 0.050, 0.250, 200, 100
    groups = []
    for _ in range(n_players):
        u = rng.gauss(0.0, tau)
        groups.append([u + rng.gauss(0.0, sigma) for _ in range(n_reps)])
    res = ST.icc_oneway(groups)
    tau2 = max(0.0, (res["msb"] - res["msw"]) / res["n0"])
    recovered = math.sqrt(tau2)
    assert recovered == pytest.approx(tau, rel=0.15)


def test_null_world_returns_the_design_floor():
    """With no jump variance the estimated jump is ~0 and the floor is unchanged.

    The floor is a property of the design (P and the between-player SD), not of
    the realised jump. A null world must not produce a smaller 'detectable'
    threshold: it returns the same floor and calls the jump undetectable.
    """
    rng = random.Random(12)
    tau, sigma, n_players, n_reps = 0.0, 0.250, 400, 100
    groups = []
    for _ in range(n_players):
        u = rng.gauss(0.0, tau)
        groups.append([u + rng.gauss(0.0, sigma) for _ in range(n_reps)])
    res = ST.icc_oneway(groups)
    tau2 = max(0.0, (res["msb"] - res["msw"]) / res["n0"])
    assert math.sqrt(tau2) < 0.020  # ~0 against a 50 ms effect

    p_eff = 125.0
    det = P.detectability(0.0, 0.1004, p_eff)
    expected = ST.sd_from_variance_share(
        ST.min_detectable_share(p_eff)["min_share"], 0.1004)
    assert det["detectable"] is False
    assert det["smallest_detectable_jump_sd_s"] == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# The committed evidence and the DRAFT gate
# --------------------------------------------------------------------------- #
def test_power_artefact_locks_the_finding():
    path = ROOT / "outputs" / "prereg" / "power.json"
    if not path.exists():
        pytest.skip("power.json not generated")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["provenance"] == "FULL"
    answers = payload["answers"]
    assert answers["detectable_at_any_candidate_cut"] is False
    assert answers["smallest_cut_resolving_50ms"] is None
    assert answers["players_required_for_50ms"] == 125
    assert answers["fittable_players_available"] == 123
    assert all(row["resolves_50ms"] is False for row in payload["table"])
    assert [int(row["min_reps_per_player"]) for row in payload["table"]] == [
        20, 25, 30, 40, 50, 60]


def test_prereg_is_frozen_and_the_full_mode_gate_passes(cfg):
    path = C.abs_path(C.block(cfg)["paths"]["prereg"])
    assert path.exists(), "notes/prereg.md must exist"
    text = path.read_text(encoding="utf-8")
    assert "**Status: FROZEN.**" in text
    assert C.prereg_is_frozen(text) is True
    # The prereg gate returns the path; full mode then fails on the missing
    # full tracking files, not on the prereg.
    assert C.require_prereg(cfg, "full", "01_audit") == path


def test_full_mode_cli_passes_the_prereg_gate_and_refuses_tracking(capsys):
    code = RUN.main(["01_audit", "--mode", "full"])
    err = capsys.readouterr().err
    assert code == 4
    assert err.startswith("REFUSED:")
    assert "tracking input missing" in err


def test_task_md_amendments_are_present():
    text = (ROOT / "TASK.md").read_text(encoding="utf-8")
    assert "tracked line-of-scrimmage crossing" in text
    assert "DROPPED (Conor, 2026-11-05)" in text
    assert "max 4 figures" in text
    # The run order contract is untouched.
    assert "01_audit -> 02_combine_physical_time" in text
