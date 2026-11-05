"""stats — known-answer reliability estimators."""

from __future__ import annotations

import math

import pytest

import stats as ST


def test_icc_oneway_is_one_for_perfect_agreement():
    r = ST.icc_oneway([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0]])
    assert r["icc"] == pytest.approx(1.0, abs=1e-9)
    assert r["k_subjects"] == 4


def test_icc_oneway_decreases_as_within_noise_grows():
    """Known answer: with groups [0,2],[2,4],[4,6], MSB=8, MSW=2, so ICC=0.6."""
    r = ST.icc_oneway([[0.0, 2.0], [2.0, 4.0], [4.0, 6.0]])
    assert r["msb"] == pytest.approx(8.0)
    assert r["msw"] == pytest.approx(2.0)
    assert r["icc"] == pytest.approx(0.6, abs=1e-9)
    noisier = ST.icc_oneway([[0.0, 4.0], [2.0, 6.0], [4.0, 8.0]])
    assert noisier["icc"] < r["icc"]


def test_icc_oneway_can_be_negative_for_disagreeing_repeats():
    r = ST.icc_oneway([[1.0, 9.0], [2.0, 9.0], [9.0, 1.0], [9.0, 2.0]])
    assert r["icc"] < 0


def test_icc_oneway_drops_single_measurement_subjects():
    r = ST.icc_oneway([[1.0, 1.1], [5.0], [2.0, 2.1]])
    assert r["k_subjects"] == 2


def test_icc_two_way_perfect_agreement():
    r = ST.icc_two_way([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    assert r["icc"] == pytest.approx(1.0, abs=1e-9)


def test_pearson_known_value():
    r = ST.pearson([1, 2, 3, 4], [2, 4, 6, 8])
    assert r == pytest.approx(1.0)
    r2 = ST.pearson([1, 2, 3, 4], [4, 3, 2, 1])
    assert r2 == pytest.approx(-1.0)


def test_spearman_brown_steps_up():
    assert ST.spearman_brown(0.5, 2) == pytest.approx(2 * 0.5 / 1.5)


def test_split_half_requires_three_players():
    assert math.isnan(ST.split_half_icc([(1.0, 2.0)])["r"])


def test_min_detectable_share_falls_with_more_players():
    a = ST.min_detectable_share(50)["min_share"]
    b = ST.min_detectable_share(200)["min_share"]
    assert a > b > 0


def test_reliability_increases_with_snaps():
    low = ST.reliability_from_share(0.2, 5)
    high = ST.reliability_from_share(0.2, 200)
    assert 0 < low < high < 1


def test_describe_handles_empty():
    assert ST.describe([]) == {"n": 0}


def test_describe_ignores_none_and_nan():
    d = ST.describe([1.0, None, float("nan"), 3.0])
    assert d["n"] == 2
    assert d["median"] == pytest.approx(2.0)
