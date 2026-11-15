"""Pins for the band-reliability artefact (D206).

* **recovery** — synthetic players with a known between/within variance ratio are
  recovered by the ICC(1) within sampling tolerance;
* **unbalanced groups** — the imported estimator's ANOVA moments and ``k0``;
* **determinism** — ``build`` twice on the same frames is byte-identical JSON;
* **the ``valid`` filter** — one ``valid == False`` row is dropped and cannot
  move the estimate, which is what reconciles 7,778 band rows with 7,777;
* **must-fail** — a missing required column and a missing file both raise, and
  the message names the path and the column (the only failures the module has);
* **derived quantities** — the formulas and the headline arithmetic;
* **one implementation** — ``band_reliability.icc1`` *is*
  ``fallback_validation.icc1``, not a copy.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from bdb27 import band_reliability as br
from bdb27 import fallback_validation as fv


def _frame(n_players: int = 120, k: int = 15, *, seed: int = 7,
           rho: float = 0.08) -> pd.DataFrame:
    """Synthetic band frame with a known ICC and the module's required columns."""
    rng = np.random.default_rng(seed)
    sigma_a2 = rho / (1.0 - rho)
    rows = []
    for p in range(n_players):
        a = rng.normal(0.0, np.sqrt(sigma_a2))
        for j in range(k):
            rows.append({
                "setting": "game", "position_group": "DB", "valid": True,
                "game_id": 1000 + p // 3, "play_id": 100 + j, "nfl_id": p,
                "frame_start": 20 + j, "abs_turn_angle": 30.0 + 29.0 * (j % 5) / 4.0,
                "entry_speed": 3.2 + 1.6 * (j % 4) / 3.0,
                "apex_retention": 0.6 + a + rng.normal(0.0, 1.0),
            })
    return pd.DataFrame(rows)


def _context(frame: pd.DataFrame) -> pd.DataFrame:
    """One play_type row per turn key; every other play is a pass."""
    ctx = frame[list(br.KEY)].drop_duplicates().copy()
    ctx["play_type"] = ["pass" if i % 2 == 0 else "run" for i in range(len(ctx))]
    return ctx


def test_icc1_is_the_fallback_validation_implementation():
    assert br.icc1 is fv.icc1


def test_recovers_a_known_icc_on_synthetic_players():
    rho = 0.25
    frame = _frame(n_players=200, k=25, rho=rho, seed=11)
    ic = br.icc1(frame["apex_retention"], frame["nfl_id"])
    assert ic["n_groups"] == 200 and ic["n_obs"] == 5000
    assert ic["icc"] == pytest.approx(rho, abs=0.04)


def test_unbalanced_groups_use_the_anova_moments():
    y = [1, 2, 3, 4, 2, 3, 5, 6, 7]
    g = [1, 1, 1, 1, 2, 2, 3, 3, 3]
    out = br.icc1(y, g)
    # N=9, sum k^2 = 16+4+9 = 29 -> k0 = (9 - 29/9)/2
    assert out["k0"] == pytest.approx((9 - 29 / 9) / 2)
    assert out["kbar_arithmetic"] == pytest.approx(3.0)
    assert out["min_turns_per_group"] == 2 and out["max_turns_per_group"] == 4
    assert out["icc"] == pytest.approx(out["sigma2_a"] / (out["sigma2_a"] + out["msw"]))


def test_build_is_deterministic():
    frame = _frame(seed=3)
    ctx = _context(frame)
    a = br.build(frame, ctx)
    b = br.build(frame.copy(), ctx.copy())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_valid_filter_drops_the_invalid_row_and_cannot_move_the_estimate():
    frame = _frame(n_players=40, k=8, seed=5)
    ctx = _context(frame)

    clean = br.build(frame, ctx)

    dirty = pd.concat([frame, pd.DataFrame([{
        "setting": "game", "position_group": "DB", "valid": False,
        "game_id": 9999, "play_id": 1, "nfl_id": 9999, "frame_start": 1,
        "abs_turn_angle": 38.0, "entry_speed": 13.5, "apex_retention": 0.01,
    }])], ignore_index=True)
    dirty_ctx = pd.concat([ctx, pd.DataFrame([{
        "game_id": 9999, "play_id": 1, "nfl_id": 9999,
        "frame_start": 1, "play_type": "pass"}])], ignore_index=True)
    got = br.build(dirty, dirty_ctx)

    assert got["sample"]["band_rows_before_valid"] == clean["sample"]["band_turns"] + 1
    assert got["sample"]["band_rows_invalid_dropped"] == 1
    assert got["sample"]["band_turns"] == clean["sample"]["band_turns"]
    assert [s["icc"] for s in got["scenarios"]] == [s["icc"] for s in clean["scenarios"]]


def test_missing_required_column_names_the_file_and_column(tmp_path):
    frame = _frame(n_players=5, k=3).drop(columns=["apex_retention"])
    with pytest.raises(KeyError) as err:
        br.build(frame, _context(frame))
    assert "apex_retention" in str(err.value)


def test_missing_turn_file_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError) as err:
        br.load_turns(tmp_path / "absent.parquet")
    assert "absent.parquet" in str(err.value)


def test_missing_context_file_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError) as err:
        br.load_context(tmp_path / "absent_context.parquet")
    assert "absent_context.parquet" in str(err.value)


def test_scenario_filters_and_join_do_not_fan_out():
    frame = _frame(n_players=20, k=6, seed=9)
    ctx = _context(frame)
    built, diag = br.build_turn_frame(frame, ctx)
    assert len(built) == len(frame)
    assert diag["join_rows"] == len(frame)
    assert diag["turns_without_context"] == 0

    entry = br.scenario_frame(built, {"entry_3_5": True, "pass_only": False})
    both = br.scenario_frame(built, {"entry_3_5": True, "pass_only": True})
    assert (entry["entry_speed"].between(3.0, 5.0)).all()
    assert set(both["play_type"]) == {"pass"}
    assert len(both) <= len(entry)


def test_derived_formulas_and_headline_arithmetic():
    assert br.reliability_of_mean(30, 0.014054573049674082) == pytest.approx(0.299547, rel=1e-5)
    assert br.reliability_of_mean(30, 0.009321248672369884) == pytest.approx(0.220132, rel=1e-5)
    assert br.attenuation_ceiling(0.2995470290456922) == pytest.approx(0.547309, rel=1e-5)
    assert br.rho_for_reliability(0.70, 30) == pytest.approx(0.0721649, rel=1e-6)
    assert br.k_for_reliability(0.70, 0.014054573049674082) == pytest.approx(163.686, rel=1e-4)

    calc = br.clusters_for_effect(-0.38, (0.33 + 1.08) / (2 * 1.959964), 84)
    assert calc["clusters_required"] == pytest.approx(590.747, rel=1e-4)
