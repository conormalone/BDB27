"""IDR Part 2 — synthetic recovery and mechanical guards (TASK.md 2026-11-04).

Three deterministic worlds exercise the production comparison path
(:func:`dl_idr_part2.run_idr_part2`):

* ``deficit``  — pressure time depends on the per-snap deficit only;
* ``absolute`` — pressure time depends on absolute cumulative load only;
* ``null``     — neither exposure drives pressure.

The fitted models are the real ones (Weibull AFT, log-normal frailty, the
shared convergence ladder), on frames generated in-test with fixed seeds.  No
network, no committed fixture, no sample numbers.
"""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_idr_part2 as IDR2


def _block() -> dict:
    """The real DL block, with CV cost trimmed only (same code path)."""
    cfg = D.load_config()
    block = copy.deepcopy(cfg["bdb27_dl"])
    block["models"]["cv_folds"] = 3
    block["models"]["bootstrap_reps"] = 300
    return block


def _world(kind: str, *, seed: int = 11, n_players: int = 48,
           snaps_per_player: int = 30) -> pd.DataFrame:
    """A survival world with both exposures measured on every row."""
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_players):
        player = 10_000 + p
        frailty = rng.normal(0.0, 0.35)
        for j in range(snaps_per_player):
            deficit_z = rng.normal()
            absolute_z = rng.normal()
            linear = frailty
            if kind == "deficit":
                linear -= 0.8 * deficit_z
            elif kind == "absolute":
                linear -= 0.8 * absolute_z
            elif kind != "null":
                raise ValueError(kind)
            time = float(np.exp(linear)
                         * (-np.log(rng.uniform())) ** (1.0 / 1.5))
            censor = 2.0
            event = int(time <= censor)
            rows.append({
                "nfl_id": player, "game_id": 1000 + p // 4, "play_id": j,
                "position_group": "EDGE" if p % 2 else "INTERIOR_LINE",
                "team": f"T{p % 8}", "season": 2023 + (p % 3),
                "alignment": "LEFT" if j % 2 else "RIGHT",
                "down": 1 + (j % 4), "yards_to_go": float(1 + (j % 10)),
                "quarter": 1 + (j % 4),
                "score_margin": float(rng.integers(-14, 15)),
                "seconds_since_last_snap": float(20 + rng.integers(0, 30)),
                "snaps_in_drive": int(j % 8),
                "height_in": float(72 + rng.integers(0, 6)),
                "weight_lb": float(240 + rng.integers(0, 60)),
                "years_since_combine": float(rng.integers(0, 4)),
                "a0": float(8 + rng.normal(0, 1)),
                "event": event, "censor_time": censor,
                "time_to_pressure": (time if event else np.nan),
                # Per-player blitz flag, independent of the outcome.
                "blitzing": bool(rng.uniform() < 0.5),
                "deficit_ratio": 1.0 + 0.15 * deficit_z,
                "absolute_load_2p5": float(np.exp(0.8 * absolute_z)),
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def block() -> dict:
    return _block()


@pytest.fixture(scope="module", params=("deficit", "absolute", "null"))
def world_result(request, block):
    frame = _world(request.param)
    return request.param, IDR2.run_idr_part2(frame, block, provenance="TEST")


def test_deficit_world_favours_the_deficit_model(world_result):
    """The deficit drives pressure and absolute load does not."""
    kind, result = world_result
    if kind != "deficit":
        pytest.skip("only the deficit world")
    assert result["status"] == "OK", result.get("reason")
    assert result["verdict"] == "DEFICIT_BETTER", result
    assert result["difference_nats_per_snap"] > result["soi"]
    assert result["difference_interval"][0] > 0
    assert result["exceeds_soi"] and result["excludes_zero"]


def test_absolute_world_favours_absolute_load(world_result):
    """The absolute cumulative load drives pressure and the deficit does not."""
    kind, result = world_result
    if kind != "absolute":
        pytest.skip("only the absolute world")
    assert result["status"] == "OK", result.get("reason")
    assert result["verdict"] == "ABSOLUTE_BETTER", result
    assert result["difference_nats_per_snap"] < -result["soi"]
    assert result["difference_interval"][1] < 0


def test_null_world_is_inconclusive_not_a_winner(world_result):
    """Neither exposure drives pressure: no winner is declared."""
    kind, result = world_result
    if kind != "null":
        pytest.skip("only the null world")
    assert result["status"] == "OK", result.get("reason")
    assert result["verdict"] not in ("DEFICIT_BETTER", "ABSOLUTE_BETTER"), result
    lo, hi = result["difference_interval"]
    assert lo <= 0.0 <= hi


def test_both_arms_are_scored_on_the_same_snaps(world_result):
    """The paired join is complete, one-to-one and does not inflate."""
    kind, result = world_result
    assert result["status"] == "OK", result.get("reason")
    # Every row is scored by both arms in these worlds; the join keeps them.
    assert result["n_scored_snaps"] == result["n_snaps"]
    assert result["n_scored_players"] == result["n_players"]
    assert result["equal_tuning_budget"] is True
    assert result["grids"]["deficit"] and result["grids"]["absolute"]
    assert len(result["grids"]["deficit"]) == len(result["grids"]["absolute"])


def test_blitzing_is_in_the_control_set_and_reported(block):
    """The per-player blitz flag enters both arms and its counts are reported."""
    frame = _world("deficit")
    result = IDR2.run_idr_part2(frame, block, provenance="TEST")
    assert result["status"] == "OK", result.get("reason")
    assert "blitzing" in result["controls"]["fixed"]
    dist = result["controls"]["blitzing"]
    assert dist["status"] == "OK"
    counts = dist["counts"]
    assert counts["True"] + counts["False"] + counts["NA"] == result["n_snaps"]
    assert dist["identified"] is True
    assert dist["na_share"] == 0.0


def test_missing_deficit_coverage_is_not_estimable(block):
    """A frame with no dynamic deficit must say why, not fit a reduced model."""
    frame = _world("deficit").assign(deficit_ratio=np.nan)
    result = IDR2.run_idr_part2(frame, block, provenance="TEST")
    assert result["status"] == "NOT_ESTIMABLE"
    assert "deficit coverage 0/" in result["reason"]
    assert result["reason_comparison"]["code"] == "INSUFFICIENT_COMMON_SAMPLE"
    assert result["coverage"]["n_with_both"] == 0


def test_equal_folds_are_shared_by_both_arms(world_result):
    """Both arms carry the same fold sizes and player counts."""
    kind, result = world_result
    if result["status"] != "OK":
        pytest.skip(result.get("reason"))
    left = [(f["fold"], f["n_train"], f["n_test"],
             f["n_train_players"], f["n_test_players"])
            for f in result["deficit"]["folds"]]
    right = [(f["fold"], f["n_train"], f["n_test"],
              f["n_train_players"], f["n_test_players"])
             for f in result["absolute"]["folds"]]
    assert left == right
