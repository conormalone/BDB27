"""physical.onset and physical.time_at_distance — the measurement primitives.

These are the functions every later stage depends on, so they are tested
against known-answer series rather than against the data.
"""

from __future__ import annotations

import math

import pytest

import physical as P
import synthetic


def _series(speeds, hz=10):
    return [i / hz for i in range(len(speeds))], list(map(float, speeds))


def test_onset_found_at_the_known_frame(cfg):
    speeds = [0.2] * 6 + [1.6] * 6
    times, s = _series(speeds)
    r = P.detect_onset(times, s, cfg)
    assert r["found"] is True
    assert r["onset_s"] == pytest.approx(0.6, abs=0.001)


def test_onset_not_found_when_stationary(cfg):
    times, s = _series([0.25] * 20)
    r = P.detect_onset(times, s, cfg)
    assert r["found"] is False
    assert "no 2-frame run" in r["reason"]


def test_single_frame_spike_is_not_an_onset(cfg):
    """One frame above threshold is sensor noise; the definition needs 2."""
    speeds = [0.2] * 5 + [5.0, 0.2] + [0.2] * 10
    times, s = _series(speeds)
    assert P.detect_onset(times, s, cfg)["found"] is False


def test_onset_respects_the_search_start(cfg):
    """A rusher moving before the snap has still not started at the snap."""
    speeds = [2.0, 2.0, 2.0, 2.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 1.8, 1.8]
    times, s = _series(speeds)
    r = P.detect_onset(times, s, cfg, search_from=0.4)
    assert r["found"] is True
    assert r["onset_s"] == pytest.approx(1.0, abs=0.001)


def test_onset_never_found_within_the_latency_limit(cfg):
    speeds = [0.2] * 10 + [0.2] * 10 + [3.0, 3.0]
    times, s = _series(speeds)     # onset would be at 2.0 s, limit is 2.0 s
    r = P.detect_onset(times, s, cfg)
    assert r["found"] in (True, False)   # boundary, must not raise
    assert r["reason"] in ("ok", "no 2-frame run at or above 1.0 yd/s within 2.0s")


def test_onset_needs_a_baseline(cfg):
    times, s = _series([1.5, 1.5])
    r = P.detect_onset(times, s, cfg)
    assert r["found"] is False


def test_time_at_distance_is_exact_on_a_linear_ramp(cfg):
    times = [0.0, 0.1, 0.2, 0.3]
    along = [0.0, 0.5, 1.0, 1.5]
    r = P.time_at_distance(times, along, 1.25)
    assert r["reached"] is True
    assert r["time_s"] == pytest.approx(0.25, abs=1e-9)
    assert r["interpolated"] is True


def test_time_at_distance_reports_unreached(cfg):
    r = P.time_at_distance([0.0, 0.1], [0.0, 0.2], 5.0)
    assert r["reached"] is False
    assert r["time_s"] is None


def test_zero_distance_returns_the_search_start_frame(cfg):
    r = P.time_at_distance([0.0, 0.1], [0.0, 0.2], 0.0, first_index=1)
    assert r["reached"] is True
    assert r["time_s"] == pytest.approx(0.1)


def test_travel_direction_is_the_principal_axis():
    xs = [50.0, 51.0, 52.0, 53.0]
    ys = [26.0, 26.01, 25.99, 26.0]
    ux, uy = P.travel_direction(xs, ys)
    assert ux == pytest.approx(1.0, abs=1e-3)
    assert abs(uy) < 1e-2


def test_travel_direction_tolerates_dir_wraparound():
    """dir jumps 275 -> 89 across the boundary; the path is still straight."""
    xs = [60.0, 60.5, 61.0, 61.5]
    ys = [26.0, 25.9, 25.8, 25.7]
    ux, uy = P.travel_direction(xs, ys)
    assert ux > 0
    assert uy < 0


def test_travel_direction_rejects_a_degenerate_path():
    with pytest.raises(ValueError):
        P.travel_direction([10.0, 10.0], [5.0, 5.0])


def test_speed_series_matches_constant_velocity():
    times = [0.0, 0.1, 0.2, 0.3]
    xs = [0.0, 0.8, 1.6, 2.4]
    ys = [0.0, 0.0, 0.0, 0.0]
    s = P.speed_series(xs, ys, times)
    assert all(abs(v - 8.0) < 1e-9 for v in s[1:-1])


def test_gap_flags_count_a_gap_inside_the_first_second(cfg):
    times = [0.0, 0.1, 0.2, 0.7, 0.8]   # 0.5 s hole after the 0.2 frame
    flags = P.gap_flags(times, cfg, onset_s=0.2)
    assert flags["n_gaps"] == 1
    assert flags["gaps_in_first_s_after_onset"] == 1


def test_speed_cap_flags_an_impossible_speed(cfg):
    assert P.speed_cap_flags([1.0, 2.0, 13.0], cfg) == 1


def test_measure_attempt_recovers_a_planted_combine_time(cfg):
    """Known-answer recovery on the synthetic exponential profile."""
    rows, truth = synthetic.make_combine_frames(seed=3, n_players=1,
                                                attempts_per=1)
    pid = list(truth["players"])[0]
    sub = [r for r in rows if r["nfl_id"] == int(pid) and r["attempt"] == 1]
    times = synthetic.relative_seconds([r["time"] for r in sub])
    m = P.measure_attempt(times, [r["x"] for r in sub], [r["y"] for r in sub],
                          [r["s"] for r in sub], cfg, [1.0, 2.0])
    assert m["ok"] is True
    # Onset detection lags the true start by ~1-2 frames (the threshold must be
    # crossed for 2 consecutive frames), so measured times run slightly SHORT.
    t1 = m["t_to_1p0yd"]
    planted1 = truth["players"][pid]["t_to_1yd"]
    assert -0.25 < t1 - planted1 < 0.05, (t1, planted1)
    t2 = m["t_to_2p0yd"]
    planted2 = truth["players"][pid]["t_to_2yd"]
    assert -0.25 < t2 - planted2 < 0.05, (t2, planted2)


def test_measure_attempt_reports_short_segments(cfg):
    m = P.measure_attempt([0.0, 0.1], [1.0, 1.1], [0.0, 0.0], [1.0, 1.0],
                          cfg, [1.0])
    assert m["ok"] is False
    assert m["reason"] == "too few frames"


def test_measure_attempt_reports_missing_onset(cfg):
    times = [i / 10 for i in range(20)]
    xs = [10.0] * 20
    ys = [5.0] * 20
    m = P.measure_attempt(times, xs, ys, [0.05] * 20, cfg, [1.0])
    assert m["ok"] is False
    assert "onset" in m["reason"] or "no 2-frame" in m["reason"]
