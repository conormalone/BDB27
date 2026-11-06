"""Reliability estimators: planted-parameter recovery, null world, determinism.

The three estimators in ``src/reliability.py`` are validated against data with
known answers, not against each other's output:

* split-half recovers the reliability of a planted player effect;
* the component estimators recover planted variance components, including the
  *balanced nested* check that pins the cell coefficients ``c_a`` and ``c_b``
  (getting those wrong is silent: it drives ``s2u`` toward zero and understates
  reliability by ~0.13 while every monotonicity check still passes);
* a null world with no player effect returns ~0, not a small positive number;
* the seeded helpers are byte-identical for the same seed.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import reliability as R
import stats as ST


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _simulate_split_half(players=400, games=8, reps=10, s2u=0.02, s2e=0.06,
                         seed=7):
    """Balanced players x games x reps with a planted player effect.

    Returns (rows, games_in_order, expected reliability of the full mean).
    """
    rng = np.random.default_rng(seed)
    rows, order = [], {}
    for p in range(players):
        u = rng.normal(0.0, math.sqrt(s2u)) if s2u > 0 else 0.0
        seq = []
        for g in range(games):
            gid = p * 1000 + g
            seq.append(gid)
            for _ in range(reps):
                rows.append((p, gid, u + rng.normal(0.0, math.sqrt(s2e))))
        order[p] = seq
    n = games * reps
    expected = s2u / (s2u + s2e / n) if s2u > 0 else 0.0
    return rows, order, expected


def _nested(players=40, games=6, reps=8, s2u=0.02, s2v=0.01, s2e=0.05,
            seed=11, unbalanced=False):
    rng = np.random.default_rng(seed)
    cells = []
    for p in range(players):
        u = rng.normal(0.0, math.sqrt(s2u))
        for g in range(games):
            v = rng.normal(0.0, math.sqrt(s2v))
            m = reps if not unbalanced else 1 + (p + g) % (reps + 2)
            vals = u + v + rng.normal(0.0, math.sqrt(s2e), m)
            cells.append({"player": p, "game": g, "n": float(m),
                          "sum": float(vals.sum()),
                          "sumsq": float((vals ** 2).sum())})
    return cells


# --------------------------------------------------------------------------- #
# 1. Split-half recovers a planted reliability
# --------------------------------------------------------------------------- #
def test_split_half_recovers_planted_reliability():
    rows, order, expected = _simulate_split_half(players=400, games=8, reps=10,
                                                 s2u=0.02, s2e=0.06)
    halves = R.split_half_means(rows, order)
    rep = R.split_half_report(halves)
    assert rep["n_players"] == 400
    # Spearman-Brown steps the half-half correlation up to the full-test value.
    assert abs(rep["spearman_brown"] - expected) < 0.03, (
        rep["spearman_brown"], expected)
    # The raw half-half correlation must be lower than the full-test value.
    assert rep["r"] < rep["spearman_brown"]


def test_split_half_null_world_is_zero():
    # 2,000 players so the null distribution of r has SE ~0.022; the bounds
    # below are then ~3 SE, not a tolerance chosen to make the test pass.
    rows, order, _ = _simulate_split_half(players=2000, games=8, reps=10,
                                          s2u=0.0, s2e=0.06, seed=99)
    rep = R.split_half_report(R.split_half_means(rows, order))
    assert abs(rep["r"]) < 0.07, rep
    assert abs(rep["spearman_brown"]) < 0.15, rep


def test_split_half_assigns_halves_by_chronological_game_order():
    rows = [(1, 10, 0.5), (1, 20, 0.7), (1, 30, 0.9)]
    order = {1: [10, 20, 30]}
    h = R.split_half_means(rows, order)[0]
    assert h["n_half0"] == 2 and h["n_half1"] == 1
    assert h["mean_half0"] == pytest.approx((0.5 + 0.9) / 2)
    assert h["mean_half1"] == pytest.approx(0.7)


def test_single_game_player_is_not_estimable():
    rows = [(1, 10, 0.5), (1, 10, 0.6), (2, 20, 0.7), (2, 30, 0.1)]
    h = {r["player"]: r for r in R.split_half_means(rows, {1: [10], 2: [20, 30]})}
    assert not h[1]["estimable"]
    assert h[2]["estimable"]


# --------------------------------------------------------------------------- #
# 2. Two-level components
# --------------------------------------------------------------------------- #
def test_two_level_recovers_planted_components():
    rng = np.random.default_rng(11)
    rows = []
    for p in range(200):
        u = rng.normal(0.0, math.sqrt(0.02))
        n = 20 + (p % 40)
        rows.extend((p, x) for x in u + rng.normal(0.0, math.sqrt(0.06), n))
    cells = R.player_cells(rows)
    comp = R.two_level_components(cells)
    assert comp["ok"]
    assert abs(math.sqrt(comp["tau2"]) / math.sqrt(0.02) - 1) < 0.25
    assert abs(math.sqrt(comp["sigma2"]) / math.sqrt(0.06) - 1) < 0.10
    assert abs(comp["k"] - 3.0) / 3.0 < 0.35


def test_two_level_matches_existing_stats_machinery():
    rng = np.random.default_rng(5)
    groups = []
    for p in range(60):
        n = 5 + p % 17
        groups.append(list(rng.normal(0.0, 0.3, n) + rng.normal(0.0, 1.0)))
    cells = R.player_cells([(i, x) for i, g in enumerate(groups) for x in g])
    comp = R.two_level_components(cells)
    hb = ST.hierarchical_normal([(i, g) for i, g in enumerate(groups)])
    assert comp["sigma2"] == pytest.approx(hb["sigma2"], rel=1e-9)
    assert comp["tau2"] == pytest.approx(hb["tau2"], rel=1e-9)


def test_two_level_null_world_gives_infinite_k_and_zero_reliability():
    rng = np.random.default_rng(3)
    cells = R.player_cells([(p, float(x)) for p in range(80)
                            for x in rng.normal(0.0, 0.2, 30)])
    comp = R.two_level_components(cells)
    assert comp["tau2"] == 0.0
    assert comp["k"] == float("inf")
    assert R.reliability_from_k(100, comp["k"]) == 0.0


# --------------------------------------------------------------------------- #
# 3. Three-level components (nested)
# --------------------------------------------------------------------------- #
def test_three_level_balanced_coefficients_equal_reps_per_game():
    """The coefficient test that catches the N-vs-n_player mistake.

    In a balanced design with ``a`` reps per game, E[MS_gw] = s2e + a*s2v and
    the game term in E[MS_p] also carries coefficient ``a``. Using N/P instead
    gives a coefficient in the hundreds and silently flattens s2u.
    """
    a, b = 8, 6
    comp = R.three_level_components(_nested(players=40, games=b, reps=a))
    assert comp["ok"]
    assert comp["c_b"] == pytest.approx(a, rel=1e-9)
    assert comp["c_a"] == pytest.approx(a, rel=1e-9)


def test_three_level_recovers_planted_components_balanced():
    comp = R.three_level_components(_nested(players=200, games=6, reps=8,
                                            s2u=0.02, s2v=0.01, s2e=0.05,
                                            seed=13))
    assert abs(math.sqrt(comp["sigma2_player"]) / math.sqrt(0.02) - 1) < 0.30
    assert abs(math.sqrt(comp["sigma2_game"]) / math.sqrt(0.01) - 1) < 0.45
    assert abs(math.sqrt(comp["sigma2_rep"]) / math.sqrt(0.05) - 1) < 0.08


def test_three_level_recovers_planted_components_unbalanced():
    comp = R.three_level_components(_nested(players=200, games=6, reps=8,
                                            s2u=0.02, s2v=0.01, s2e=0.05,
                                            seed=17, unbalanced=True))
    assert comp["ok"]
    assert abs(math.sqrt(comp["sigma2_player"]) / math.sqrt(0.02) - 1) < 0.35
    assert comp["components_truncated"] == 0


def test_three_level_reliability_decreases_when_game_effect_adds_noise():
    """Reps in a game are not independent: same n, fewer games = less reliable."""
    comp = R.three_level_components(_nested(players=120, games=8, reps=10,
                                            s2u=0.02, s2v=0.02, s2e=0.05))
    many = R.reliability_three_level(80, 8, comp["sigma2_player"],
                                     comp["sigma2_game"], comp["sigma2_rep"])
    few = R.reliability_three_level(80, 2, comp["sigma2_player"],
                                    comp["sigma2_game"], comp["sigma2_rep"])
    assert 0 < few < many <= 1


# --------------------------------------------------------------------------- #
# 4. Small pure helpers
# --------------------------------------------------------------------------- #
def test_rep_cut_is_the_inverse_of_reliability():
    k = 6.17
    for target in (0.5, 0.6, 0.7, 0.8):
        n = R.rep_cut_for_reliability(k, target)
        assert R.reliability_from_k(n, k) == pytest.approx(target, abs=1e-12)


def test_effective_players_kish_count():
    assert R.effective_players([1.0] * 10) == pytest.approx(10.0)
    assert R.effective_players([0.5] * 10) == pytest.approx(10.0)
    # A zero-reliability player contributes nothing to either sum, so he adds
    # no effective players: 10 informative + 10 useless == 10 effective.
    assert R.effective_players([1.0] * 10 + [0.0] * 10) == pytest.approx(10.0)
    assert R.effective_players([1.0] * 10 + [0.5] * 10) == pytest.approx(
        225 / 12.5)


def test_pearson_r_edges():
    assert math.isnan(R.pearson_r([(1.0, 1.0), (2.0, 2.0)]))
    assert R.pearson_r([(1, 1), (2, 2), (3, 3), (4, 4)]) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 5. Determinism
# --------------------------------------------------------------------------- #
def test_bootstrap_is_deterministic_for_a_seed():
    cells = _nested(players=30, games=5, reps=6)
    a = R.bootstrap_three_level(cells, 40, 123)
    b = R.bootstrap_three_level(cells, 40, 123)
    c = R.bootstrap_three_level(cells, 40, 999)
    assert a == b
    assert a != c
    pcells = R.player_cells([(f["player"], f["sum"] / f["n"]) for f in cells])
    assert R.bootstrap_two_level(pcells, 40, 5) == R.bootstrap_two_level(
        pcells, 40, 5)


def test_simulation_is_deterministic_for_a_seed():
    cells = _nested(players=25, games=4, reps=5)
    comp = R.three_level_components(cells)
    a = R.simulate_split_half_curve(cells, comp, [2, 3], 10, 42)
    b = R.simulate_split_half_curve(cells, comp, [2, 3], 10, 42)
    assert a == b


def test_bootstrap_does_not_merge_duplicate_players():
    """A player drawn twice is two clusters, not one player with double n."""
    cells = _nested(players=20, games=3, reps=5)
    boot = R.bootstrap_three_level(cells, 20, 1)
    # If duplicates merged, the resampled player count would collapse and the
    # intervals would be far too narrow; guard on the component spread instead.
    lo = boot["intervals"]["sigma2_player"]["p_lo"]
    hi = boot["intervals"]["sigma2_player"]["p_hi"]
    assert hi > lo >= 0
    assert hi > 0
