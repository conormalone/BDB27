"""Pins for ``notes/fallback-validation-spec.md`` (D203).

The spec was committed at ``c95c983`` before any fit in this cycle. These tests hold
the things that were fixed in advance:

* the ICC(1) estimator on hand-computed small cases, balanced and unbalanced;
* the clamp-at-zero *display* convention (the maths is not changed);
* split-half odd/even determinism, Spearman-Brown arithmetic, and the sensitivity;
* the paired bootstrap of ``Delta = b_P - b_S`` on synthetic data with a known
  difference, and its symmetry under swapping the two outcomes;
* every §1.4 and §2.4 label rule, and the §4 precedence order, on synthetic input;
* the saturated display where ``n <= params`` and **nowhere else** (spec §7.4);
* that no label rule, estimator or renderer can raise on degenerate input (D202);
* the notebook is committed unexecuted, UTF-8 and POSIX-free on Windows;
* the artefacts are byte-identical across two consecutive runs;
* ``TASK.md``, ``outputs/figures/`` and ``notes/report-draft.md`` are untouched.
"""

from __future__ import annotations

import ast
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdb27 import fallback_validation as fv

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_fallback_validation.ipynb"
#: Paths this cycle may not modify. Two carve-outs, both recorded in D211:
#: (a) untracked ADDITIONS are not modifications — an approved figure cycle adds
#: its own new files here while every committed figure stays byte-identical;
#: (b) the manifest itself is the registry a new figure must be listed in, so
#: adding an entry rewrites it. Modified or deleted tracked figure files, and
#: any change to TASK.md or the draft, still fail.
UNTOUCHED = ["TASK.md", "outputs/figures", "notes/report-draft.md"]
MANIFEST_REGISTRY = "outputs/figures/figures_manifest.json"
#: Committed figure files a recorded decision moved on purpose. D212
#: (2026-11-23) moved figure 2's proposed-drill marker 40 -> 45 so it matches
#: the schematic's `break_angles_deg: [45]`; those two files are the only
#: committed figures that decision rewrites. D214 (2026-11-23) moved figure 21's
#: protractor onto the 0-360 scale the other plots use; the display scale only,
#: no measured value. Every other figure is still off-limits to an unrecorded
#: edit.
DECISION_MOVED_FIGURES = {
    "outputs/figures/fig_drill_coverage.csv",
    "outputs/figures/fig_drill_coverage.png",
    "outputs/figures/fig_backpedal_polar.csv",
    "outputs/figures/fig_backpedal_polar.png",
    "outputs/figures/fig_backpedal_polar.drawn_text.json",
}

ENCODED_CALLS = {"to_csv", "read_csv", "read_text", "write_text"}
SQL_PATH = re.compile(r"(?:read_csv_auto|read_parquet|read_json_auto)\('\{|TO '\{")


# ------------------------------------------------------------------ ICC(1)
def test_icc1_matches_the_hand_computed_balanced_case():
    """Three groups of three: means 2, 3, 6, grand 3.667."""
    y = [1, 2, 3, 2, 3, 4, 5, 6, 7]
    g = [1, 1, 1, 2, 2, 2, 3, 3, 3]
    out = fv.icc1(y, g)

    assert out["n_obs"] == 9 and out["n_groups"] == 3
    assert out["msb"] == pytest.approx(13.0)          # SSB 26.0 / df 2
    assert out["msw"] == pytest.approx(1.0)           # SSW 6.0 / df 6
    assert out["k0"] == pytest.approx(3.0)
    assert out["sigma2_a"] == pytest.approx(4.0)      # (13 - 1) / 3
    assert out["sigma2_e"] == pytest.approx(1.0)
    assert out["icc"] == pytest.approx(4.0 / 5.0)
    assert out["clamped"] is False


def test_icc1_matches_the_hand_computed_unbalanced_case():
    """Groups of 4, 2 and 3, the case k0 exists for."""
    y = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    g = [1, 1, 1, 1, 2, 2, 3, 3, 3]
    out = fv.icc1(y, g)

    # grand 5, SSB 52.5 / df 2, SSW 7.5 / df 6,
    # k0 = (9 - (16 + 4 + 9)/9) / 2 = 2.8889
    assert out["msb"] == pytest.approx(26.25)
    assert out["msw"] == pytest.approx(1.25)
    assert out["k0"] == pytest.approx((9 - 29 / 9) / 2)
    assert out["sigma2_a"] == pytest.approx(25.0 / ((9 - 29 / 9) / 2))
    assert out["icc"] == pytest.approx(8.653846153846154 / (8.653846153846154 + 1.25))
    assert out["kbar_harmonic"] == pytest.approx(3 / (1 / 4 + 1 / 2 + 1 / 3))


def test_icc1_clamps_the_point_estimate_and_keeps_the_mean_squares():
    """MSB < MSW: the point estimate is 0.000 (clamped); the maths is not changed."""
    out = fv.icc1([0, 10, 0, 10], [1, 1, 2, 2])

    assert out["msb"] == pytest.approx(0.0)
    assert out["msw"] == pytest.approx(50.0)
    assert out["clamped"] is True
    assert out["icc"] == 0.0
    assert out["sigma2_a_raw"] < 0          # the raw moment estimate is kept
    assert out["sigma2_a"] == 0.0           # the display value is clamped


def test_icc1_bootstrap_interval_is_reproducible_and_needs_no_clamp():
    y = [1, 2, 3, 2, 3, 4, 5, 6, 7] * 3
    g = ([1] * 3 + [2] * 3 + [3] * 3) * 3
    a = fv.icc1_bootstrap_ci(y, g, reps=200, stream="icc1")
    b = fv.icc1_bootstrap_ci(y, g, reps=200, stream="icc1")
    assert a["lo"] == b["lo"] and a["hi"] == b["hi"]
    assert a["n_estimable_reps"] > 0
    assert a["lo"] <= a["hi"]


# ------------------------------------------------------------- display rules
def test_the_saturated_literal_is_exactly_the_spec_string():
    assert fv.SATURATED == "not estimable (saturated)"


def test_saturated_fires_at_n_at_or_below_the_parameter_count():
    assert fv.interval_text(0.1, 0.4, n_obs=2, n_params=2) == fv.SATURATED
    assert fv.interval_text(0.1, 0.4, n_obs=1, n_params=2) == fv.SATURATED
    assert fv.interval_text(0.1, 0.4, n_obs=3, n_params=3) == fv.SATURATED


def test_saturated_does_not_fire_above_the_parameter_count():
    assert fv.interval_text(0.1, 0.4, n_obs=4, n_params=2) == "[0.100, 0.400]"
    assert fv.interval_text(-0.002, 0.011, n_obs=7777, n_params=3) == "[-0.002, 0.011]"


def test_a_zero_width_interval_is_never_emitted():
    text = fv.interval_text(0.3, 0.3, n_obs=50, n_params=2)
    assert text != "[0.300, 0.300]"
    assert text == fv.DEGENERATE


def test_the_saturated_label_never_appears_on_a_healthy_cell():
    for n in (10, 100, 7777):
        assert fv.SATURATED not in fv.interval_text(0.0, 0.2, n_obs=n, n_params=2)


# ------------------------------------------------------- split-half + S-B
def turn_frame(players=(1, 2, 3, 4), per_player=4) -> pd.DataFrame:
    """A deterministic turn frame: unique (season, game, play, frame) keys."""
    rows = []
    for p in players:
        for j in range(per_player):
            rows.append({
                "nfl_id": p, "season": 2024 + (j % 2),
                "game_id": p, "play_id": 100 + j, "frame_start": 10 * j,
                "apex_retention": 0.5 + 0.1 * p + 0.01 * j,
                "abs_turn_angle": 45.0, "entry_speed": 4.0,
            })
    return pd.DataFrame(rows)


def test_split_half_alternates_odd_even_on_the_specified_key():
    df = turn_frame(players=(7,), per_player=6)
    halves = fv.split_half_halves(df)
    turns = halves["turns"].sort_values("_ord")

    assert list(turns["half"]) == ["A", "B", "A", "B", "A", "B"]
    # order is (season, game_id, play_id, frame_start): season sorts first, so the
    # three 2024 turns come before the three 2025 ones, alternating within each.
    assert list(turns["season"]) == [2024, 2024, 2024, 2025, 2025, 2025]
    assert turns[turns["half"] == "A"]["_ord"].tolist() == [0, 2, 4]


def test_split_half_is_deterministic_under_row_order():
    df = turn_frame(players=(1, 2, 3, 4), per_player=8)
    first = fv.split_half_halves(df)["players"]
    second = fv.split_half_halves(df.sample(frac=1.0, random_state=11))["players"]
    pd.testing.assert_frame_equal(first.sort_values("nfl_id").reset_index(drop=True),
                                  second.sort_values("nfl_id").reset_index(drop=True))


def test_spearman_brown_arithmetic():
    assert fv.spearman_brown(0.4) == pytest.approx(0.8 / 1.4)
    assert fv.spearman_brown(0.5) == pytest.approx(2.0 / 3.0)
    assert fv.spearman_brown(0.0) == 0.0
    assert math.isnan(fv.spearman_brown(math.nan))


def test_a_player_with_no_turn_in_one_half_is_saturated_not_dropped_silently():
    df = pd.concat([turn_frame(players=(1, 2, 3), per_player=4),
                    turn_frame(players=(9,), per_player=1)], ignore_index=True)
    out = fv.split_half_reliability(df, reps=50, stream="split_half")

    assert out["n_players_total"] == 4
    assert out["n_players_used"] == 3
    assert out["n_players_dropped_one_sided"] == 1
    assert out["one_sided_players"][0]["saturated"] is True
    assert out["one_sided_players"][0]["count_A"] + \
        out["one_sided_players"][0]["count_B"] == 1


def test_split_half_reports_r_and_the_brown_correction_together():
    df = turn_frame(players=range(1, 9), per_player=8)
    out = fv.split_half_reliability(df, reps=50, stream="split_half")
    assert out["r"] == pytest.approx(fv.spearman_brown(out["r"]) * 0 + out["r"])
    assert out["spearman_brown"] == pytest.approx(
        2 * out["r"] / (1 + out["r"]))


def test_split_half_of_two_players_is_saturated():
    df = turn_frame(players=(1, 2), per_player=4)
    out = fv.split_half_reliability(df, reps=20, stream="split_half")
    assert out["n_players_used"] == 2
    assert out["saturated"] is True


def test_season_correlation_reports_both_n_variants():
    df = turn_frame(players=range(1, 7), per_player=24)
    wide = fv.season_correlation(df, 2024, 2025, min_turns=1)
    narrow = fv.season_correlation(df, 2024, 2025, min_turns=10)
    assert wide["n_players"] == 6 and narrow["n_players"] == 6
    assert wide["seasons_available"] == [2024, 2025]


def test_r_mean_follows_the_spec_formula():
    assert fv.r_mean(0.5, 4) == pytest.approx(4 * 0.5 / (1 + 3 * 0.5))
    assert fv.r_mean(0.0, 10) == 0.0
    assert math.isnan(fv.r_mean(math.nan, 4))


# ------------------------------------------------------------- leg 2 estimators
def test_ols_hc1_matches_a_hand_computed_slope_and_hc1_variance():
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    y = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    out = fv.ols_hc1(x, y)

    # slope = Sxy/Sxx with x centred: Sxy 8, Sxx 10 -> 0.8
    assert out["b"] == pytest.approx(0.8)
    assert out["n"] == 5
    # HC1 meat for a simple regression: sum(x_c^2 e^2) / (Sxx^2) * n/(n-2)
    xc = x - x.mean()
    resid = y - (y.mean() + 0.8 * xc)
    se = math.sqrt((np.sum(xc ** 2 * resid ** 2) / np.sum(xc ** 2) ** 2)
                   * (5 / 3))
    assert out["se_hc1"] == pytest.approx(se, abs=1e-4)   # artefact keeps 4 dp
    assert out["lo"] < out["b"] < out["hi"]


def test_mde_is_the_post_hoc_80_percent_power_quantity():
    from scipy import stats
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([1.0, 3.0, 2.0, 5.0, 4.0, 6.0])
    out = fv.ols_hc1(x, y)
    expected = (stats.t.ppf(0.975, 4) + stats.t.ppf(0.80, 4)) * out["se_hc1"]
    assert out["mde_80"] == pytest.approx(expected, abs=1e-4)


def test_zscore_flips_so_positive_means_faster():
    times = np.array([4.50, 4.40, 4.60])
    z = fv.zscore_flipped(times, "forty")
    assert z[1] > z[0] > z[2]        # the fastest time has the highest z


def test_a_constant_drill_yields_zeros_and_never_raises():
    z = fv.zscore_flipped(np.array([4.5, 4.5, 4.5]), "forty")
    assert np.all(z == 0.0)


def synthetic_discriminant_frame(n: int = 80, slope_p: float = 1.0,
                                 slope_s: float = 0.0, seed: int = 5) -> pd.DataFrame:
    """P tracks the drill, S does not: a known, engineered Delta."""
    rng = np.random.default_rng(seed)
    raw = rng.normal(7.0, 0.4, size=n)
    z = (raw - raw.mean()) / raw.std(ddof=1)
    p = 0.9 * z + rng.normal(0, 0.15, size=n)
    s = 0.9 * 0.05 * z + rng.normal(0, 0.09, size=n)
    return pd.DataFrame({"nfl_id": np.arange(1, n + 1), "s": s, "p": p,
                         "raw": raw, "n_s": 30, "n_p": 30})


def test_paired_bootstrap_recovers_a_known_difference():
    work = synthetic_discriminant_frame(slope_p=1.0, slope_s=0.0)
    out = fv._paired_delta(work, drill="forty", reps=400)

    assert out["n"] == 80
    # the drill is sign-flipped (positive = faster) before standardising, so the
    # engineered difference appears as -0.9 + 0.045; the magnitude is the check.
    assert out["delta"] == pytest.approx(-0.855, abs=0.1)
    assert out["lo"] is not None and out["hi"] is not None
    assert out["hi"] < 0 and out["hi"] > out["lo"]
    assert out["n_estimable_reps"] > 0.9 * out["reps"]


def test_the_paired_bootstrap_is_symmetric_under_swapping_the_outcomes():
    work = synthetic_discriminant_frame(slope_p=1.0, slope_s=0.0)
    forward = fv._paired_delta(work, drill="forty", reps=400)
    backward = fv._paired_delta(work.rename(columns={"s": "_p", "p": "s"})
                                .rename(columns={"_p": "p"}), drill="forty",
                                reps=400)

    assert backward["delta"] == pytest.approx(-forward["delta"], abs=1e-9)
    assert backward["lo"] == pytest.approx(-forward["hi"], abs=0.02)
    assert backward["hi"] == pytest.approx(-forward["lo"], abs=0.02)


def test_a_two_player_delta_keeps_the_point_estimate_and_saturates_the_interval():
    work = synthetic_discriminant_frame(n=2, slope_p=1.0, slope_s=0.0)
    out = fv._paired_delta(work, drill="forty", reps=20)
    assert out["n"] == 2
    assert out["interval"] == fv.SATURATED
    assert out["lo"] is None and out["hi"] is None


def test_a_drill_with_no_player_returns_a_status_and_not_an_exception():
    work = synthetic_discriminant_frame(n=10).assign(raw=np.nan)
    fit = fv._drill_fit(work, outcome="s", weight_col="n_s", drill="forty")
    assert fit["n"] == 0
    assert "not fitted" in fit["status"]
    assert fit["b"] is None


def test_the_raw_unit_coefficient_is_the_z_coefficient_over_the_drill_sd():
    """Spec §2.3: both the z-scale and the raw-unit coefficient go to the artefact."""
    work = synthetic_discriminant_frame(n=70)
    fit = fv._drill_fit(work, outcome="p", weight_col="n_p", drill="forty")
    sd = float(work["raw"].std(ddof=1))

    assert fit["sd_drill"] == pytest.approx(sd, abs=1e-4)
    # the drill is scored in seconds and flipped, so dz/dt = -1/sd
    assert fit["b_per_second"] == pytest.approx(-fit["b"] / sd, abs=1e-4)
    # a negative scale must swap the bounds, never invert the interval
    assert fit["lo_per_second"] < fit["hi_per_second"]
    assert fit["lo_per_second"] <= fit["b_per_second"] <= fit["hi_per_second"]


# ------------------------------------------------------------- label rules
@pytest.mark.parametrize("icc,lo,hi,rsb,expected", [
    (0.62, 0.44, 0.75, 0.70, "STABLE"),
    (0.55, 0.31, 0.70, 0.51, "STABLE"),
    (0.62, 0.25, 0.75, 0.70, "WEAK"),      # STABLE fails on the lower bound
    (0.62, 0.44, 0.75, 0.40, "WEAK"),      # STABLE fails on Spearman-Brown
    (0.35, 0.10, 0.55, 0.30, "WEAK"),
    (0.19, 0.01, 0.39, 0.20, "VOID"),
    (0.05, -0.02, 0.15, -0.10, "VOID"),
    (0.19, 0.01, 0.45, 0.20, "UNDETERMINED"),  # the table's fourth cell
])
def test_leg1_label_rules(icc, lo, hi, rsb, expected):
    out = fv.leg1_label(icc=icc, ci_lo=lo, ci_hi=hi, r_sb=rsb)
    assert out["label"] == expected
    assert out["why"]


def test_leg1_label_never_raises_on_missing_numbers():
    for args in [dict(icc=math.nan, ci_lo=math.nan, ci_hi=math.nan, r_sb=math.nan),
                 dict(icc=None, ci_lo=None, ci_hi=None, r_sb=None),
                 dict(icc=0.3, ci_lo=0.2, ci_hi=0.4, r_sb=math.nan)]:
        out = fv.leg1_label(**args)
        assert out["label"] in fv.LEG1_LABELS


def drill_row(drill, b_s, b_p, delta, *, sd_s=0.06, sd_p=0.07,
              delta_lo=0.02, delta_hi=0.09, b_s_lo=0.001, b_s_hi=0.03,
              b_p_lo=-0.002, b_p_hi=0.004):
    return {"drill": drill, "b_s": b_s, "b_s_lo": b_s_lo, "b_s_hi": b_s_hi,
            "b_p": b_p, "b_p_lo": b_p_lo, "b_p_hi": b_p_hi,
            "delta": delta, "delta_lo": delta_lo, "delta_hi": delta_hi,
            "sd_s": sd_s, "sd_p": sd_p}


def test_leg2_discriminant_fires_when_delta_clears_the_bar_and_s_does_not():
    rows = [drill_row("three_cone", 0.004, 0.03, 0.026)]
    out = fv.leg2_labels(rows)
    assert out["label"] == "DISCRIMINANT"
    assert "DISCRIMINANT" in out["fired"]


def test_leg2_transfers_fires_when_a_turning_drill_predicts_the_shallow_break():
    # bar_s = 0.25*0.06 = 0.015; |b_S| 0.03 with a CI excluding 0
    rows = [drill_row("short_shuttle", 0.03, 0.005, -0.025,
                      b_s_lo=0.01, b_s_hi=0.05)]
    out = fv.leg2_labels(rows)
    assert out["label"] == "TRANSFERS"


def test_leg2_transfers_outranks_discriminant_across_drills():
    rows = [drill_row("three_cone", 0.030, 0.005, -0.025, b_s_lo=0.01, b_s_hi=0.05),
            drill_row("short_shuttle", 0.004, 0.03, 0.026)]
    out = fv.leg2_labels(rows)
    assert out["label"] == "TRANSFERS"
    assert "DISCRIMINANT" in out["fired"]
    assert out["fired"][0] == "TRANSFERS"


def test_leg2_speed_transfers_needs_both_speed_drills():
    one = [drill_row("forty", 0.03, 0.005, 0.001, b_s_lo=0.01, b_s_hi=0.05)]
    both = one + [drill_row("ten_yd_split", 0.03, 0.005, 0.001,
                            b_s_lo=0.01, b_s_hi=0.05)]
    assert "SPEED-TRANSFERS" not in fv.leg2_labels(one)["fired"]
    assert fv.leg2_labels(both)["label"] == "SPEED-TRANSFERS"


def test_leg2_speed_transfers_does_not_fire_when_delta_clears_the_bar():
    rows = [drill_row("forty", 0.03, 0.09, 0.06, b_s_lo=0.01, b_s_hi=0.05),
            drill_row("ten_yd_split", 0.03, 0.09, 0.06, b_s_lo=0.01, b_s_hi=0.05)]
    assert "SPEED-TRANSFERS" not in fv.leg2_labels(rows)["fired"]


def test_leg2_null_battery_fires_when_no_drill_reaches_the_deep_plant():
    rows = [drill_row("three_cone", 0.001, 0.001, 0.0),
            drill_row("short_shuttle", 0.002, -0.002, -0.004),
            drill_row("forty", 0.016, -0.001, -0.017),
            drill_row("ten_yd_split", 0.010, 0.003, -0.007)]
    out = fv.leg2_labels(rows)
    assert out["label"] == "NULL-BATTERY"


def test_leg2_is_underpowered_when_nothing_fires():
    rows = [drill_row("three_cone", 0.004, 0.030, 0.005, delta_lo=-0.02)]
    out = fv.leg2_labels(rows)
    assert out["label"] == "UNDERPOWERED"


def test_leg2_null_battery_never_fires_vacuously_with_no_fitted_drill():
    rows = [{"drill": "three_cone", "b_s": None, "b_s_lo": None, "b_s_hi": None,
             "b_p": None, "b_p_lo": None, "b_p_hi": None, "delta": None,
             "delta_lo": None, "delta_hi": None, "sd_s": 0.06, "sd_p": 0.07}]
    out = fv.leg2_labels(rows)
    assert out["label"] == "UNDERPOWERED"
    assert "NULL-BATTERY" not in out["fired"]


def test_leg2_labels_never_raise_on_junk():
    for rows in ([], [drill_row("three_cone", math.nan, math.nan, math.nan)],
                 [drill_row("unknown_drill", 0.03, 0.03, 0.0)]):
        out = fv.leg2_labels(rows)
        assert out["label"] in fv.LEG2_ORDER


# ------------------------------------------------------------------ verdict
@pytest.mark.parametrize("leg1,leg2,rule,label", [
    ("VOID", "DISCRIMINANT", 1, "VOID"),
    ("VOID", "TRANSFERS", 1, "VOID"),            # rule 1 outranks rule 2
    ("STABLE", "TRANSFERS", 2, "WEAKENED"),
    ("WEAK", "SPEED-TRANSFERS", 2, "WEAKENED"),
    ("STABLE", "NULL-BATTERY", 3, "UNINFORMATIVE"),
    ("VOID", "NULL-BATTERY", 1, "VOID"),
    ("STABLE", "DISCRIMINANT", 4, "SUPPORTED"),
    ("WEAK", "DISCRIMINANT", 5, "PARTIALLY SUPPORTED"),
    ("UNDETERMINED", "DISCRIMINANT", 6, "INCONCLUSIVE / UNDERPOWERED"),
    ("WEAK", "UNDERPOWERED", 6, "INCONCLUSIVE / UNDERPOWERED"),
    ("WEAK", "NULL-BATTERY", 3, "UNINFORMATIVE"),
])
def test_verdict_rules_and_their_precedence(leg1, leg2, rule, label):
    out = fv.verdict(leg1, leg2)
    assert out["rule"] == rule
    assert out["label"] == label
    assert out["headline"].startswith(f"RULE {rule} FIRED")


def test_verdict_prints_every_rule_whatever_fires():
    out = fv.verdict("STABLE", "DISCRIMINANT")
    assert [r["rule"] for r in out["rules"]] == [1, 2, 3, 4, 5, 6]
    # rules 1-5 are the substantive ones; exactly one of them is true, and rule 6
    # is the unconditional fallback that only gives the headline when none does.
    assert sum(1 for r in out["rules"][:5] if r["condition"]) == 1
    assert out["rules"][3]["condition"] is True


def test_no_verdict_rule_can_raise():
    junk = [None, "", "nonsense", "STABLE ", "void", math.nan, 3, object()]
    for a in junk:
        for b in junk:
            out = fv.verdict(a, b)
            assert out["label"] in {"VOID", "WEAKENED", "UNINFORMATIVE",
                                    "SUPPORTED", "PARTIALLY SUPPORTED",
                                    "INCONCLUSIVE / UNDERPOWERED"}


# ------------------------------------------------------------- gate-free contract
def tiny_game() -> pd.DataFrame:
    rows = []
    for p in (1, 2, 3, 4):
        for j in range(3):
            rows.append({"setting": "game", "nfl_id": p, "game_id": p,
                         "play_id": 10 + j, "season": 2024 + (j % 2),
                         "position_group": "DB", "abs_turn_angle": 45.0,
                         "apex_retention": 0.6 + 0.05 * p + 0.01 * j,
                         "entry_speed": 3.0 + j, "plant": j > 0,
                         "frame_start": 10 * j})
    return pd.DataFrame(rows)


def tiny_combine() -> pd.DataFrame:
    return pd.DataFrame({
        "nfl_id": [1, 2, 3, 4], "drill_name": ["THREE_CONE_DRILL"] * 4,
        "abs_turn_angle": [45.0, 45.0, 120.0, 150.0],
        "entry_speed": [3.2, 3.6, 3.9, 4.4],
        "band": ["30–60°", "30–60°", "120–150°", "150–180°"],
        "valid": ["True"] * 4,
    })


def tiny_results() -> pd.DataFrame:
    return pd.DataFrame({"nfl_id": [1, 2, 3, 4], "combine_position": ["DB"] * 4,
                         "three_cone": [7.0, 6.9, 7.1, 6.8],
                         "short_shuttle": [4.3, 4.4, 4.35, 4.25],
                         "forty": [4.5, 4.55, 4.6, 4.45],
                         "ten_yd_split": [1.55, 1.57, 1.6, 1.52]})


def test_a_four_player_band_runs_to_numbers_and_never_raises():
    bundle = fv.analyse(tiny_game(), tiny_combine(), tiny_results(), reps=50)
    assert bundle.verdict["label"]
    assert bundle.reliability["sample"]["n_players"] == 4
    assert len(bundle.discriminant) == len(fv.DRILLS)
    assert fv.STAMP in bundle.text


def test_a_one_player_band_reports_saturated_cells_and_continues():
    game = tiny_game().assign(nfl_id=1)
    bundle = fv.analyse(game, tiny_combine(), tiny_results(), reps=20)
    assert bundle.reliability["icc1"]["status"].startswith("not estimable")
    assert bundle.verdict["label"]


def test_an_empty_band_does_not_raise(): 
    game = tiny_game().iloc[0:0]
    bundle = fv.analyse(game, tiny_combine(), tiny_results(), reps=20)
    assert bundle.reliability["sample"]["n_turns"] == 0
    assert bundle.speed["game_band_entry_speed"]["n_turns"] == 0
    assert bundle.verdict["label"]


def test_every_renderer_handles_the_degenerate_bundle():
    bundle = fv.analyse(tiny_game().iloc[0:0], tiny_combine(), tiny_results(), reps=10)
    for lines in (fv.render_reliability(bundle.reliability),
                  fv.render_discriminant(bundle.discriminant, bundle.discriminant_meta),
                  fv.render_speed(bundle.speed)):
        assert lines and all(isinstance(x, str) for x in lines)
    assert fv.STAMP in "\n".join(fv.render_reliability(bundle.reliability) + ["[FULL]"])


# ------------------------------------------------------------ determinism/paths
def test_two_consecutive_runs_produce_identical_artefacts(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    bundle_a = fv.analyse(tiny_game(), tiny_combine(), tiny_results(), reps=100)
    bundle_b = fv.analyse(tiny_game(), tiny_combine(), tiny_results(), reps=100)
    for out in (first, second):
        out.mkdir()
        out.joinpath("turns.parquet").write_bytes(b"x")
    fv.write_outputs(first, bundle_a, inputs={}, repo_root=None,
                     generated_utc="2026-11-15T00:00:00Z")
    fv.write_outputs(second, bundle_b, inputs={}, repo_root=None,
                     generated_utc="2026-11-15T00:00:00Z")
    for name in ("reliability.json", "discriminant.csv", "speed_coverage.json",
                 "verdict.txt", "manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_the_manifest_carries_the_seed_the_head_and_every_hash(tmp_path):
    bundle = fv.analyse(tiny_game(), tiny_combine(), tiny_results(), reps=20)
    (tmp_path / "in.csv").write_text("a\n1\n", encoding="utf-8")
    manifest = fv.write_outputs(tmp_path / "out", bundle,
                                inputs={"combined": tmp_path / "in.csv"},
                                repo_root=ROOT)
    assert manifest["seed"] == 20261115
    assert set(manifest["outputs"]) == {"reliability.json", "discriminant.csv",
                                        "speed_coverage.json", "verdict.txt"}
    assert manifest["inputs"]["combined"]["sha256"] == fv.sha256_file(tmp_path / "in.csv")
    assert set(manifest["config"]) >= {"band", "material_share_of_sd", "drills"}


def test_a_missing_input_names_every_path_it_tried(tmp_path):
    from pathlib import Path as P
    with pytest.raises(FileNotFoundError) as exc:
        fv.require_input(None, label="GAME_TURNS",
                         patterns=("no_such_turns_file.parquet",),
                         roots=[tmp_path], hint="set GAME_TURNS")
    text = str(exc.value)
    assert str(tmp_path) in text
    assert "set GAME_TURNS" in text
    assert "no_such_turns_file.parquet" in text


def test_an_explicit_missing_path_is_named(tmp_path):
    target = tmp_path / "nope.parquet"
    with pytest.raises(FileNotFoundError) as exc:
        fv.require_input(target, label="GAME_TURNS", patterns=("*.parquet",),
                         roots=[tmp_path], hint="set GAME_TURNS")
    assert str(target) in str(exc.value)


def test_ranking_prefers_the_full_run_folder_over_a_sample_copy(tmp_path):
    for folder, token in ((tmp_path / "bdb27_run" / "artefacts", {}),
                          (tmp_path / "data" / "interim" / "full_run_20261111", {})):
        folder.mkdir(parents=True)
        (folder / "bdb27_full_db_turns.parquet").write_bytes(b"x")
    hit = fv.resolve_input(None, label="GAME_TURNS",
                            patterns=("bdb27_full_db_turns.parquet",),
                            roots=[tmp_path], hint="h",
                            prefer=("full_run_20261111",))
    assert "full_run_20261111" in hit["chosen"].as_posix()
    assert len(hit["candidates"]) == 2


# --------------------------------------------------------------------- notebook
def notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def code_cells() -> list[str]:
    return ["".join(c["source"]) for c in notebook()["cells"]
            if c["cell_type"] == "code"]


def all_source() -> str:
    return "\n".join("".join(c["source"]) for c in notebook()["cells"])


def test_the_notebook_is_committed_unexecuted():
    nb = notebook()
    assert nb["cells"], "notebook is empty"
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell.get("outputs") == [], f"code cell {i} carries baked outputs"
        assert cell.get("execution_count") is None, f"code cell {i} has an execution count"


def test_the_config_cell_is_the_first_code_cell_and_holds_every_knob():
    config = code_cells()[0]
    for knob in ("GAME_TURNS", "COMBINE_TURNS", "COMBINE_RESULTS", "OUT_DIR",
                 "N_BOOTSTRAP", "STAMP"):
        assert knob in config, knob
    # nothing below §1 needs editing: no other cell assigns these knobs
    for source in code_cells()[1:]:
        for knob in ("GAME_TURNS =", "COMBINE_TURNS =", "COMBINE_RESULTS =",
                     "OUT_DIR =", "N_BOOTSTRAP =", "STAMP ="):
            assert knob not in source, f"{knob} is set outside the config cell"


def test_every_code_cell_compiles():
    for i, source in enumerate(code_cells()):
        compile(source, f"<cell {i}>", "exec")


def test_every_text_read_and_write_is_utf8():
    bad = []
    for i, source in enumerate(code_cells()):
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in ENCODED_CALLS:
                ok = any(kw.arg == "encoding"
                         and getattr(kw.value, "value", None) == "utf-8"
                         for kw in node.keywords)
                if not ok:
                    bad.append(f"cell {i}: {name} without encoding='utf-8'")
    assert not bad, bad


def test_no_sql_path_is_interpolated_without_posix():
    bad = [f"cell {i}: {line.strip()}"
           for i, source in enumerate(code_cells())
           for line in source.splitlines()
           if SQL_PATH.search(line) and ".as_posix()" not in line]
    assert not bad, bad


def test_the_notebook_has_no_memory_probe_and_no_pip_install():
    forbidden = re.compile(r"\bimport resource\b|\bresource\.|\bpsutil\b|"
                           r"peak_rss|getrusage|pip install")
    for i, source in enumerate(code_cells()):
        hit = forbidden.search(source)
        assert hit is None, f"cell {i} contains {hit.group(0)!r}"


def test_the_notebook_uses_the_shared_module_not_a_copy():
    source = all_source()
    assert "from bdb27 import fallback_validation as fv" in source
    for call in ("fv.reliability(", "fv.discriminant(", "fv.speed_coverage(",
                 "fv.assemble(", "fv.write_outputs("):
        assert call in source, call
    assert "def icc1(" not in source, "the notebook re-implements an estimator"


def test_the_notebook_sections_are_in_the_spec_order():
    text = "\n".join("".join(c["source"]) for c in notebook()["cells"])
    order = [text.index(token) for token in
             ("Leg 1 — reliability", "Leg 2 — discriminant", "Leg 3 — speed coverage",
              "The verdict (spec §4)")]
    assert order == sorted(order)


def _write_fixture(tmp_path: Path) -> dict:
    """A four-player, two-season fixture that exercises every leg."""
    rows = []
    for p in (1, 2, 3, 4):
        for j in range(8):
            angle = [45.0, 45.0, 130.0, 160.0, 70.0, 100.0, 45.0, 45.0][j]
            rows.append({
                "setting": "game", "nfl_id": p, "game_id": 100 + p,
                "play_id": 10 + j, "season": 2024 + (j % 2),
                "position_group": "DB", "abs_turn_angle": angle,
                "apex_retention": 0.55 + 0.05 * p + 0.01 * j,
                "entry_speed": 2.5 + 0.6 * j, "plant": j % 3 == 0,
                "frame_start": 10 * j, "valid": True,
            })
    turns = tmp_path / "bdb27_full_db_turns.parquet"
    pd.DataFrame(rows).to_parquet(turns, index=False)

    combine = tmp_path / "combine_db_turns_floor2.5_control.csv"
    pd.DataFrame({
        "nfl_id": [1, 2, 3, 4, 1, 2],
        "drill_name": ["THREE_CONE_DRILL", "THREE_CONE_DRILL", "SHORT_SHUTTLE",
                       "FORTY_YARD_DASH", "W_DRILL", "BOX_DRILL"],
        "abs_turn_angle": [45.0, 48.0, 52.0, 130.0, 44.0, 55.0],
        "entry_speed": [3.2, 3.6, 3.9, 4.4, 5.0, 2.9],
        "band": ["30–60°", "30–60°", "30–60°", "120–150°", "30–60°", "30–60°"],
        "valid": ["True"] * 6,
    }).to_csv(combine, index=False, encoding="utf-8")

    results = tmp_path / "combine_results.csv"
    pd.DataFrame({
        "nfl_id": [1, 2, 3, 4], "combine_position": ["DB"] * 4,
        "three_cone": [7.0, 6.9, 7.1, 6.8],
        "short_shuttle": [4.3, 4.4, 4.35, 4.25],
        "forty": [4.5, 4.55, 4.6, 4.45],
        "ten_yd_split": [1.55, 1.57, 1.6, 1.52],
    }).to_csv(results, index=False, encoding="utf-8")
    return {"turns": turns, "combine": combine, "results": results}


def _run_notebook(tmp_path: Path, paths: dict) -> str:
    import contextlib
    import io
    import os

    config = f'''GAME_TURNS = {str(paths["turns"])!r}
COMBINE_TURNS = {str(paths["combine"])!r}
COMBINE_RESULTS = {str(paths["results"])!r}
OUT_DIR = {str(tmp_path / "out")!r}
N_BOOTSTRAP = 50
STAMP = "FULL"
'''
    ns: dict = {}
    buf = io.StringIO()
    previous = Path.cwd()
    os.chdir(ROOT)
    try:
        with contextlib.redirect_stdout(buf):
            for i, cell in enumerate(notebook()["cells"]):
                if cell["cell_type"] != "code":
                    continue
                source = "".join(cell["source"])
                if "CONFIG — the only cell to edit" in source:
                    source = config
                exec(compile(source, f"<cell {i}>", "exec"), ns)
    finally:
        os.chdir(previous)
    return buf.getvalue()


def test_the_notebook_runs_end_to_end_on_a_tiny_fixture(tmp_path):
    paths = _write_fixture(tmp_path)
    text = _run_notebook(tmp_path, paths)
    for expected in ("LEG 1 — reliability", "LEG 2 — discriminant",
                     "LEG 3 — speed coverage", "RULE", "[FULL]",
                     "not estimable (saturated)"):
        assert expected in text, expected
    assert (tmp_path / "out" / "manifest.json").exists()
    assert (tmp_path / "out" / "verdict.txt").exists()


def test_the_notebook_outputs_match_the_module_artefacts(tmp_path):
    paths = _write_fixture(tmp_path)
    _run_notebook(tmp_path, paths)
    payload = json.loads((tmp_path / "out" / "reliability.json")
                         .read_text(encoding="utf-8"))
    game = fv.load_game_turns(paths["turns"])
    expected = fv.reliability(game)
    assert payload["sample"]["n_turns"] == expected["sample"]["n_turns"]
    assert payload["label"]["label"] == expected["label"]["label"]


# -------------------------------------------------------- untouched guard rails
def test_the_cycle_did_not_touch_the_task_the_figures_or_the_draft():
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", *UNTOUCHED],
            capture_output=True, text=True, check=False)
    except OSError:  # pragma: no cover - git absent
        pytest.skip("git is not available to check the untouched files")
    if out.returncode != 0:  # pragma: no cover - not a repo
        pytest.skip("not a git checkout")
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    modified = [line for line in lines
                if not line.startswith("??")
                and line[3:].strip() not in (MANIFEST_REGISTRY,
                                             *DECISION_MOVED_FIGURES)]
    assert modified == [], (
        "TASK.md, outputs/figures/ or notes/report-draft.md were modified:\n"
        + "\n".join(modified))


def test_the_band_table_and_the_spec_itself_are_also_untouched():
    spec = ROOT / "notes" / "fallback-validation-spec.md"
    assert spec.exists()
    body = spec.read_text(encoding="utf-8")
    # the spec was committed before the fit; its decision rules are still there
    for token in ("STABLE", "WEAK", "VOID", "DISCRIMINANT", "TRANSFERS",
                  "SPEED-TRANSFERS", "NULL-BATTERY", "UNDERPOWERED"):
        assert token in body, token
