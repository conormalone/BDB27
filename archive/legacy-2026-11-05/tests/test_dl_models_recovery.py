"""DL stage 04 — synthetic recovery in the four committed worlds (design §6).

These tests assert **direction and ranking**, not sample numbers, except where a
world's planted truth is the whole point (the null world must not reproduce the
planted effect).  The fixtures are committed CSVs, so the same seed always
produces the same frame; a regeneration that changed a verdict would be a
deliberate fixture change, not flakiness.

Worlds (``fixtures/synthetic/dl/<world>/``):

* ``planted``   — a0 raises the pressure hazard; decay follows **relative** load.
  Because relative load is measured against the player's own ceiling, a higher
  a0 lowers relative load, so the world's *total* a0 effect is partly
  self-cancelling: it exists to test the load-definition comparison, not the
  P1 hazard size.
* ``absolute``  — a0 raises the hazard; decay follows **absolute** load, which
  is independent of a0, so this is the clean P1 recovery world
* ``null``      — no a0 effect, no load effect
* ``censoring`` — the a0 effect is present but pressures are rare (~2%)

The load comparison uses two folds so the suite stays tractable; the fold rule
(§9) still applies and is exercised by ``test_dl_models.py``.
"""

from __future__ import annotations

import copy
import json
import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_fixtures as F
from dl import dl_models_core as M

FIXTURES = D.REPO_ROOT / "fixtures" / "synthetic" / "dl"
WORLDS = ("planted", "absolute", "null", "censoring")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _block(cfg_override: dict | None = None) -> dict:
    cfg = D.load_config()
    block = copy.deepcopy(cfg["bdb27_dl"])
    if cfg_override:
        for key, value in cfg_override.items():
            if isinstance(value, dict) and isinstance(block.get(key), dict):
                block[key].update(value)
            else:
                block[key] = value
    return block


def _frame(world: str, block: dict) -> pd.DataFrame:
    root = FIXTURES / world
    frame, _ = M.build_frame(pd.read_csv(root / "snaps.csv"),
                             pd.read_csv(root / "player_ceiling.csv"),
                             pd.read_csv(root / "players.csv"),
                             pd.read_csv(root / "combine_results.csv"))
    panel = M.build_load_panel(frame, block)
    return pd.concat([frame, panel], axis=1)


def _meta(world: str) -> dict:
    return json.loads((FIXTURES / world / "meta.json").read_text())


@pytest.fixture(scope="module", params=WORLDS)
def world_result(request):
    world = request.param
    block = _block()
    frame = _frame(world, block)
    return world, _meta(world), M.fit_p1(frame, block)


# --------------------------------------------------------------------------- #
# Part 1 recovery
# --------------------------------------------------------------------------- #
def test_every_world_fits_part1(world_result):
    world, meta, p1 = world_result
    assert p1["status"] == "OK", f"{world}: {p1.get('reason')}"
    assert p1["rung_used"] == "aft_frailty_lognormal"      # the primary, not a rung
    assert p1["event_rate"] < 0.5                          # §5 guard
    assert p1["n_obs"] > 0


def test_null_world_is_near_null():
    block = _block()
    null = M.fit_p1(_frame("null", block), block)["estimate"]
    observed = M.fit_p1(_frame("absolute", block), block)["estimate"]
    assert abs(np.log(null["estimate"])) < np.log(1.25), (
        "the null world produced an effect larger than 1.25")
    assert abs(np.log(null["estimate"])) < abs(np.log(observed["estimate"]))


def test_absolute_world_recovers_a_positive_a0_hazard():
    """The clean recovery world: absolute load is independent of a0."""
    block = _block()
    est = M.fit_p1(_frame("absolute", block), block)["estimate"]
    assert est["estimate"] > 1.5, "the planted a0 effect was not recovered"
    assert est["lo"] > 1.0


def test_censoring_world_fits_with_rare_events():
    block = _block()
    p1 = M.fit_p1(_frame("censoring", block), block)
    assert p1["status"] == "OK"
    assert p1["event_rate"] < 0.05, "this world is meant to be heavily censored"
    assert p1["estimate"]["estimate"] > 1.0


def test_secondary_models_run_in_every_world(world_result):
    world, meta, p1 = world_result
    sec = p1["secondary"]
    assert sec["player_get_off"]["status"] in ("OK", "NOT_CONVERGED")


# --------------------------------------------------------------------------- #
# Part 2: the right definition should win
# --------------------------------------------------------------------------- #
_PART2_CACHE: dict[str, dict] = {}


def _part2(world: str) -> dict:
    """One CV comparison per world, computed once for the whole module."""
    if world not in _PART2_CACHE:
        block = _block({"models": {"cv_folds": 2}})
        _PART2_CACHE[world] = M.run_part2(_frame(world, block), block)
    return _PART2_CACHE[world]


def test_part2_prefers_relative_load_in_the_relative_world():
    out = _part2("planted")
    assert out["status"] == "OK", out.get("reason")
    assert out["difference_nats_per_snap"] > 0, (
        "relative load should score better where relative load drives decay")
    assert out["better_definition"] == "relative"


def test_part2_prefers_absolute_load_in_the_absolute_world():
    out = _part2("absolute")
    assert out["status"] == "OK", out.get("reason")
    assert out["difference_nats_per_snap"] < 0
    assert out["better_definition"] == "absolute"


def test_part2_uses_the_same_folds_for_both_definitions():
    out = _part2("planted")
    abs_folds = [(f["fold"], f["n_test"], f["n_test_players"])
                 for f in out["absolute"]["folds"]]
    rel_folds = [(f["fold"], f["n_test"], f["n_test_players"])
                 for f in out["relative"]["folds"]]
    assert abs_folds == rel_folds


def test_part2_tuning_stays_inside_each_definition_grid():
    out = _part2("planted")
    assert all(f["selected"] in out["grids"]["absolute"]
               for f in out["absolute"]["folds"])
    assert all(f["selected"] in out["grids"]["relative"]
               for f in out["relative"]["folds"])


def test_part2_verdict_wording_follows_the_prereg_rule():
    from dl.dl_models_core import part2_verdict
    assert part2_verdict(0.010, {"lo": 0.002, "hi": 0.018}, 0.005, 0.002) == "RELATIVE_BETTER"
    assert part2_verdict(-0.010, {"lo": -0.018, "hi": -0.002}, 0.005, 0.002) == "ABSOLUTE_BETTER"
    assert part2_verdict(0.001, {"lo": -0.001, "hi": 0.0015}, 0.005, 0.002) == "EQUIVALENT"
    # a point above zero but an interval crossing it is inconclusive, not a finding
    assert part2_verdict(0.004, {"lo": -0.002, "hi": 0.010}, 0.005, 0.002) == "INCONCLUSIVE"
    # interval excludes zero but the point is below the smallest effect of interest
    assert part2_verdict(0.003, {"lo": 0.001, "hi": 0.005}, 0.005, 0.002) == "INCONCLUSIVE"


# --------------------------------------------------------------------------- #
# End-to-end stage on a fixture world
# --------------------------------------------------------------------------- #
def test_stage_runs_end_to_end_on_a_fixture_world(tmp_path):
    from dl import stage_04_models as stage04

    cfg = D.load_config()
    world = FIXTURES / "planted"
    scratch = tmp_path / "scratch"
    cfg = F.world_config(cfg, world, scratch)
    cfg["bdb27_dl"]["models"]["cv_folds"] = 2
    out = stage04.run(cfg, "sample", force=True)
    assert out["skipped"] is False
    out_dir = stage04._out_dir(cfg, "sample")
    part2 = json.loads((out_dir / "part2_result.json").read_text())
    assert part2["status"] == "OK"
    assert part2["equal_tuning_budget"] is True
    cv = pd.read_csv(out_dir / "cv_folds.csv")
    assert set(cv["definition"]) == {"absolute", "relative"}
    tune = pd.read_csv(out_dir / "load_tuning.csv")
    # five candidates per definition per fold, and exactly one winner per fold
    assert tune.groupby(["definition", "fold"])["candidate_value"].nunique().eq(5).all()
    assert tune["selected"].sum() == len(cv)


def test_config_fixture_truths_match_the_generator():
    """A config value that contradicts PLANT is a documentation bug."""
    block = _block()
    fixture = block["fixture"]
    assert F.PLANT["planted"]["hr_a0"] == fixture["planted_hr_a0"]
    assert F.PLANT["planted"]["gamma"] == fixture["planted_decay_relative"]
    assert F.PLANT["absolute"]["gamma"] == fixture["planted_decay_absolute"]
    assert F.PLANT["censoring"]["rare"] == fixture["rare_pressure_rate"]
