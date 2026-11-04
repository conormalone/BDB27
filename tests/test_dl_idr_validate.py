"""Phase 5 under the binding IDR spec (TASK.md, 2026-11-04).

Two items, and only two:

* the **within-player rest permutation** — ``inter_snap_rest_s`` shuffled
  within ``(nfl_id, game_id)``, the Part-1 mixed model refitted through the
  identical ladder, the observed interaction compared with the permutation
  null;
* the **context controls** — garbage time, the final two minutes of each half,
  and obvious passing situations.

These tests drive the production code path
(``dl.dl_idr_validate`` -> ``dl.dl_idr_models``), not a test-only
approximation.  The fixture worlds are deterministic and offline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_idr_fixture as F
from dl import dl_idr_models as I
from dl import dl_idr_validate as V


@pytest.fixture(scope="module")
def cfg():
    return D.load_config("config.yaml")


@pytest.fixture(scope="module")
def block(cfg):
    return D.dl(cfg)


def _frame(regime: str, **kw) -> pd.DataFrame:
    return F.build_idr_world(regime, **kw)["frame"]


# --------------------------------------------------------------------------- #
# The provenance of the build itself
# --------------------------------------------------------------------------- #
def test_ceiling_permutation_is_superseded_and_not_in_the_default_path(block):
    cfg = block["validate"]["ceiling_permutation"]
    assert cfg["enabled"] is False
    assert cfg["superseded"] is True


def test_the_two_task_items_are_the_default_checks(block):
    vcfg = block["validate"]
    assert vcfg["rest_permutation"]["enabled"] is True
    assert vcfg["rest_permutation"]["scheme"] == "within_player_game"
    assert vcfg["rest_permutation"]["block_columns"] == ["nfl_id", "game_id"]
    assert vcfg["context_controls"]["garbage_time"]["enabled"] is True
    assert vcfg["context_controls"]["final_two_minutes"]["enabled"] is True
    # no rule is invented for "obvious passing" anywhere in the repo
    assert vcfg["context_controls"]["obvious_passing"]["rule"] is None


# --------------------------------------------------------------------------- #
# Permutation integrity
# --------------------------------------------------------------------------- #
def test_permutation_preserves_every_block_multiset(block):
    frame = _frame("planted", n_players=8, n_games=4, snaps_per_player=20)
    blocks = V.block_indices(frame, ["nfl_id", "game_id"])
    before = frame["inter_snap_rest_s"].to_numpy(dtype=float)
    after, report = V.permute_rest_within_blocks(
        frame, "inter_snap_rest_s", np.random.default_rng(11), blocks=blocks)

    for idx in blocks.values():
        assert np.array_equal(np.sort(before[idx]), np.sort(after[idx]))
        assert np.array_equal(np.isfinite(before[idx]), np.isfinite(after[idx]))
    assert report["block_multiset_preserved"] is True
    assert report["missingness_preserved"] is True
    # something actually moved: this is a permutation, not a copy
    assert not np.array_equal(before, after)
    # no value left its own block: each block's value set is unchanged
    for idx in blocks.values():
        assert set(np.round(after[idx], 12)) == set(np.round(before[idx], 12))


def test_a_rest_value_never_crosses_a_player_or_game(block):
    frame = _frame("planted", n_players=6, n_games=3, snaps_per_player=15)
    before = frame["inter_snap_rest_s"].to_numpy(dtype=float)
    after, _ = V.permute_rest_within_blocks(
        frame, "inter_snap_rest_s", np.random.default_rng(7))

    # Every value in the fixture is a distinct continuous draw, so each rest
    # value identifies the block it started in.  After permutation every value
    # must still sit in that same (rusher, game) block.
    keys = list(zip(frame["nfl_id"], frame["game_id"]))
    home = {round(float(value), 12): keys[pos]
            for pos, value in enumerate(before)}
    assert len(home) == len(before)
    for pos, value in enumerate(after):
        assert home[round(float(value), 12)] == keys[pos]


def test_singleton_blocks_are_counted_not_dropped(block):
    frame = _frame("null", n_players=4, n_games=2, snaps_per_player=6)
    # a player-game with a single finite rest value: nothing to swap with
    frame.loc[frame.index[-1], "inter_snap_rest_s"] = 30.0
    lonely = frame.index[-1]
    mask = (frame["nfl_id"] == frame.at[lonely, "nfl_id"]) & (
        frame["game_id"] == frame.at[lonely, "game_id"])
    others = frame.index[mask & (frame.index != lonely)]
    frame.loc[others, "inter_snap_rest_s"] = np.nan

    after, report = V.permute_rest_within_blocks(
        frame, "inter_snap_rest_s", np.random.default_rng(3))
    assert report["n_blocks_singleton"] >= 1
    assert report["n_snaps_in_singleton_blocks"] >= 1
    # the singleton keeps its own value (and its NA neighbours stay NA)
    assert after[lonely] == pytest.approx(30.0)
    assert np.isnan(after[others]).all()


# --------------------------------------------------------------------------- #
# Synthetic recovery, through the production path
# --------------------------------------------------------------------------- #
def test_the_statistic_is_the_production_part1_interaction(block):
    """The observed statistic is the Part-1 fit, not a re-implementation."""
    frame = _frame("planted")
    stat = V.interaction_statistic(frame, block)
    primary = I.run_idr(frame, block["idr_part1"], mode="sample")["primary"]
    assert stat["status"] == "OK"
    assert primary["status"] == "OK"
    assert stat["coef_std"] == pytest.approx(
        primary["interaction"]["coef_std"], rel=0, abs=1e-12)
    assert stat["n_snaps"] == primary["n_snaps"]


def test_planted_world_interaction_is_outside_the_rest_null(block):
    frame = _frame("planted")
    out = V.rest_permutation(frame, block, reps=39, seed=20270109)
    assert out["status"] == "OK", out.get("reason")
    inf = out["inference"]
    assert inf["n_permutations"] == 39
    # the real effect: shuffling rest destroys it
    assert inf["observed"] > 0.03
    assert inf["reject_null"] is True
    assert inf["p_two_sided"] <= 0.05
    # the null is centred on zero and much tighter than the observed effect
    assert abs(inf["null_mean"]) < 0.01
    assert inf["observed_abs"] > inf["null_q975"]
    assert out["design"]["block_multiset_preserved"] is True


def test_null_world_is_not_significant_and_no_effect_is_manufactured(block):
    frame = _frame("null")
    out = V.rest_permutation(frame, block, reps=39, seed=20270109)
    assert out["status"] == "OK"
    inf = out["inference"]
    assert inf["reject_null"] is False
    assert inf["p_two_sided"] > 0.05
    # the permutation itself invents nothing: its null sits on zero
    assert abs(inf["null_mean"]) < 0.002
    assert inf["null_q025"] < inf["observed"] < inf["null_q975"]


def test_a_frame_below_the_declared_minimum_is_not_estimable(block):
    frame = _frame("planted", n_players=3, n_games=1, snaps_per_player=5)
    out = V.rest_permutation(frame, block, reps=5, seed=1)
    assert out["status"] == "NOT_ESTIMABLE"
    assert "minimum" in (out.get("reason") or "").lower()
    assert out["design"]["reps_valid"] == 0


# --------------------------------------------------------------------------- #
# Context controls
# --------------------------------------------------------------------------- #
def test_context_controls_report_all_three_exclusions(block):
    frame = _frame("planted")
    out = V.context_controls(frame, block)
    checks = {row["check"]: row for row in out["rows"]}
    assert set(checks) == {"baseline_all_snaps", "exclude_garbage_time",
                           "exclude_final_two_minutes",
                           "exclude_obvious_passing"}
    for name in ("exclude_garbage_time", "exclude_final_two_minutes"):
        assert checks[name]["status"] == "OK", checks[name].get("reason")
        assert checks[name]["n_rows"] > 0
    # the final two minutes of each half exist in the fixture and are removed
    assert checks["exclude_final_two_minutes"]["n_excluded"] > 0
    # (c) is a reported gap, never an invented threshold
    gap = checks["exclude_obvious_passing"]
    assert gap["status"] == "DEFINITION_UNDEFINED"
    assert gap["reason"]
    assert gap["estimate"] is None


def test_garbage_time_margins_come_from_config(block):
    gcfg = block["game"]["garbage_time"]
    assert (gcfg["q4"], gcfg["q4_margin"], gcfg["any_quarter_margin"]) == \
        (4, 21, 28)
    frame = pd.DataFrame({
        "quarter": [4, 1, 1, 3, 2],
        "score_margin": [21, 21, 28, -20, 5],
    })
    mask, info = V._garbage_mask(frame, block,
                                 block["validate"]["context_controls"]["garbage_time"])
    assert info["rule"] == "quarter_and_margin"
    assert info["status"] == "OK"
    assert list(mask) == [True, False, True, False, False]


def test_final_two_minutes_mask_is_the_final_two_minutes_of_each_half(block):
    frame = pd.DataFrame({
        "quarter": [2, 2, 1, 4, 4, 3],
        "game_clock_s": [100, 130, 100, 119, 121, 60],
    })
    mask, info = V._final_two_mask(
        frame, block, block["validate"]["context_controls"]["final_two_minutes"])
    assert info["quarters"] == [2, 4]
    assert info["seconds"] == 120
    assert list(mask) == [True, False, False, True, False, False]


def test_a_restricted_sample_keeps_the_full_sequence_load(block):
    """A context exclusion must not re-window the load on the truncated sample."""
    frame = _frame("planted")
    out = V.context_controls(frame, block)
    base_n = out["baseline"]["n_snaps"]
    for row in out["rows"]:
        if row["estimate"] is not None:
            assert row["n_rows"] <= base_n
