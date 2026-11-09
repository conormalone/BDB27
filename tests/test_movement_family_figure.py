"""Figure 17 contract: ratio-of-medians, bootstrap truth, schema, log axis.

Unit-level tests — no stage run. The fixture is a deterministic game-only DB
turn table with one deliberately thin bucket (band 5: three turns from two
players) so the "no estimate" path is exercised, and one band where the median
of per-turn ratios is provably different from a ratio of medians.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd
import pytest

from bdb27 import common, figures, stats

ROOT = Path(__file__).resolve().parents[1]

CSV_COLUMNS = [
    "panel", "panel_index", "band_index", "band", "band_low_deg",
    "band_high_deg", "n_turns", "n_players", "statistic", "value", "ci_low",
    "ci_high", "ci_level", "ci_method", "estimable", "is_focus_band",
    "log_axis", "bootstrap_reps", "bootstrap_seed",
]
PANELS = ["apex_share", "peak_lateral_accel", "circle_fit_share", "peak_omega"]


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(ROOT / "config.yaml")


@pytest.fixture(scope="module")
def frame(cfg):
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    rows = []
    for i in range(len(edges)):
        lo = edges[i]
        hi = edges[i + 1] if i + 1 < len(edges) else lo + 60.0
        # Band 5 is below the ten-turn floor on purpose; every other band has
        # twenty turns from four players.
        n = 3 if i == 5 else 20
        players = [1000 + i * 10 + k for k in range(2 if i == 5 else 4)]
        for j in range(n):
            angle = lo + (hi - lo) * (j + 0.5) / n
            if i == 1:
                # Ten low-entry (ratio 0.2) and ten high-entry (ratio 0.9)
                # turns: median of ratios = 0.55, ratio of medians = 0.725.
                entry = 2.0 if j < 10 else 6.0
                apex = 0.4 if j < 10 else 5.4
            else:
                entry = 3.0 + 0.20 * i + 0.01 * j
                apex = entry * (0.90 - 0.05 * i + 0.001 * j)
            rows.append({
                "setting": "game",
                "position_group": "DB",
                "valid": True,
                "abs_turn_angle": float(angle),
                "nfl_id": int(players[j % len(players)]),
                "apex_speed": float(apex),
                "entry_speed": float(entry),
                "peak_lateral_accel_yd_s2": float(
                    4.0 + 0.5 * i + 0.01 * j),
                "circle_fit_usable": bool(j % 4 != 0),
                "peak_omega_deg_s": float(70.0 + 20.0 * i + 0.5 * j),
            })
    # Rows the population filter must remove: Combine, non-DB, invalid.
    rows.append({
        "setting": "combine", "position_group": "DB", "valid": True,
        "abs_turn_angle": 40.0, "nfl_id": 9991, "apex_speed": 1.0,
        "entry_speed": 2.0, "peak_lateral_accel_yd_s2": 5.0,
        "circle_fit_usable": True, "peak_omega_deg_s": 90.0,
    })
    rows.append({
        "setting": "game", "position_group": "WR", "valid": True,
        "abs_turn_angle": 40.0, "nfl_id": 9992, "apex_speed": 1.0,
        "entry_speed": 2.0, "peak_lateral_accel_yd_s2": 5.0,
        "circle_fit_usable": True, "peak_omega_deg_s": 90.0,
    })
    rows.append({
        "setting": "game", "position_group": "DB", "valid": False,
        "abs_turn_angle": 40.0, "nfl_id": 9993, "apex_speed": 1.0,
        "entry_speed": 2.0, "peak_lateral_accel_yd_s2": 5.0,
        "circle_fit_usable": True, "peak_omega_deg_s": 90.0,
    })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def built(cfg, frame):
    return figures.movement_family_table(frame, cfg)


@pytest.fixture(scope="module")
def table(built):
    return built[0]


@pytest.fixture(scope="module")
def context(built):
    return built[1]


def _reference_bootstrap(values, clusters, *, reps, ci_level, label,
                         base_seed, statistic):
    """Independent re-implementation of the seeded player-clustered loop."""
    values = np.asarray(values, dtype=float)
    clusters = np.asarray(clusters)
    players = pd.unique(clusters)
    seed = stats.seed_from_label(label, base_seed)
    rng = np.random.default_rng(seed)
    groups = {p: np.flatnonzero(clusters == p) for p in players}
    draws = np.empty(reps, dtype=float)
    for r in range(reps):
        pick = rng.choice(players, size=len(players), replace=True)
        idx = np.concatenate([groups[p] for p in pick])
        draws[r] = (np.median(values[idx]) if statistic == "median"
                    else np.mean(values[idx]))
    alpha = (1.0 - ci_level) / 2.0
    low, high = np.quantile(draws, [alpha, 1.0 - alpha])
    point = (np.median(values) if statistic == "median"
             else np.mean(values))
    return float(point), float(low), float(high), int(seed)


def _bucket(frame, cfg, band_index):
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    valid = frame["valid"].fillna(False).astype(bool)
    df = frame[valid & (frame["position_group"] == "DB")
               & (frame["setting"] == "game")].copy()
    angle = df["abs_turn_angle"]
    if band_index < len(edges) - 1:
        mask = (angle >= edges[band_index]) & (angle < edges[band_index + 1])
    else:
        mask = angle >= edges[band_index]
    return df[mask]


def test_csv_schema_and_row_order(table, cfg):
    csv = figures.movement_family_csv_frame(table, {})
    assert list(csv.columns) == CSV_COLUMNS
    assert len(csv) == 28
    assert csv["panel"].unique().tolist() == PANELS
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    labels = figures.band_labels(edges, overflow)
    for panel in PANELS:
        part = csv[csv["panel"] == panel]
        assert part["band_index"].tolist() == list(range(7))
        assert part["band"].tolist() == labels
        assert part["bootstrap_reps"].tolist() == [2000] * 7
    assert csv["panel_index"].tolist() == [p for p in range(4) for _ in range(7)]
    assert csv["ci_method"].unique().tolist() == [
        "percentile_cluster_bootstrap_by_player"]
    assert csv["log_axis"].sum() == 7
    assert csv.loc[csv["panel"] == "peak_omega", "log_axis"].all()
    assert not csv.loc[csv["panel"] != "peak_omega", "log_axis"].any()
    assert csv.loc[csv["band_index"] == 0, "is_focus_band"].all()
    assert not csv.loc[csv["band_index"] != 0, "is_focus_band"].any()


def test_median_of_per_turn_ratios_not_ratio_of_medians(table, frame, cfg):
    part = _bucket(frame, cfg, 1)
    ratios = (part["apex_speed"] / part["entry_speed"]).to_numpy(dtype=float)
    per_turn_median = float(np.median(ratios))
    ratio_of_medians = float(np.median(part["apex_speed"])
                             / np.median(part["entry_speed"]))
    assert per_turn_median != ratio_of_medians, (
        "fixture must distinguish the two estimators")
    row = table[(table["panel"] == "apex_share")
                & (table["band_index"] == 1)].iloc[0]
    assert float(row["value"]) == per_turn_median
    assert float(row["value"]) != ratio_of_medians


def test_hand_checked_median_bootstrap_is_exact(table, frame, cfg, context):
    for panel, column in (("apex_share", None),
                          ("peak_lateral_accel", "peak_lateral_accel_yd_s2"),
                          ("peak_omega", "peak_omega_deg_s")):
        part = _bucket(frame, cfg, 1)
        if column is None:
            values = (part["apex_speed"] / part["entry_speed"]).to_numpy(
                dtype=float)
        else:
            values = part[column].to_numpy(dtype=float)
        clusters = part["nfl_id"].to_numpy()
        label = f"movement_family|{panel}|1"
        point, low, high, _ = _reference_bootstrap(
            values, clusters, reps=2000, ci_level=0.95, label=label,
            base_seed=context["seed_base"], statistic="median")
        row = table[(table["panel"] == panel)
                    & (table["band_index"] == 1)].iloc[0]
        assert float(row["value"]) == point
        assert float(row["ci_low"]) == low
        assert float(row["ci_high"]) == high
        assert int(row["bootstrap_seed"]) == stats.seed_from_label(
            label, context["seed_base"])


def test_proportion_bootstrap_matches_independent_loop(table, frame, cfg,
                                                       context):
    part = _bucket(frame, cfg, 2)
    values = part["circle_fit_usable"].astype(bool).astype(float).to_numpy()
    clusters = part["nfl_id"].to_numpy()
    label = "movement_family|circle_fit_share|2"
    point, low, high, _ = _reference_bootstrap(
        values, clusters, reps=2000, ci_level=0.95, label=label,
        base_seed=context["seed_base"], statistic="proportion")
    row = table[(table["panel"] == "circle_fit_share")
                & (table["band_index"] == 2)].iloc[0]
    assert float(row["value"]) == point
    assert float(row["ci_low"]) == low
    assert float(row["ci_high"]) == high


def test_ci_columns_and_non_estimable_bucket(table):
    for _, row in table.iterrows():
        if bool(row["estimable"]):
            assert np.isfinite(row["value"])
            assert np.isfinite(row["ci_low"])
            assert np.isfinite(row["ci_high"])
            assert float(row["ci_low"]) <= float(row["value"]) <= float(row["ci_high"])
        else:
            assert pd.isna(row["value"])
            assert pd.isna(row["ci_low"])
            assert pd.isna(row["ci_high"])
            assert int(row["band_index"]) == 5
            assert int(row["n_turns"]) == 3


def test_circle_fit_share_bounded_on_fixture_csv(table, context):
    csv = figures.movement_family_csv_frame(table, context)
    part = csv[csv["panel"] == "circle_fit_share"]
    values = part["value"].dropna()
    lows = part["ci_low"].dropna()
    highs = part["ci_high"].dropna()
    for series in (values, lows, highs):
        assert (series >= 0.0).all() and (series <= 1.0).all()
    # A proportion bootstrap is not a ratio of two noisy quantities: it
    # cannot leave [0, 1] even though the fixture repeats within players.
    assert len(values) == 6


def test_population_is_game_db_only(table, context):
    assert context["population_turns"] == 6 * 20 + 3
    assert context["population_players"] == 26
    # The thin bucket counts are carried once, for the footer and caveat (c).
    row = table[(table["panel"] == "apex_share")
                & (table["band_index"] == 5)].iloc[0]
    assert int(row["n_turns"]) == 3
    assert int(row["n_players"]) == 2


def test_exactly_four_axes_one_log_axis(table, context, cfg):
    fig = figures.plot_movement_family(table, context, cfg)
    try:
        assert len(fig.axes) == 4
        assert [ax.get_yscale() for ax in fig.axes] == [
            "linear", "linear", "linear", "log"]
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)


def test_two_renders_are_byte_identical(table, context, cfg, tmp_path):
    digests = []
    for i in range(2):
        fig = figures.plot_movement_family(table, context, cfg)
        out = tmp_path / f"mf_{i}.png"
        figures.render_movement_family(fig, cfg, out)
        digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


def test_caption_does_not_claim_shallow_band_is_arc_extreme(context):
    text = " ".join(figures._mf_caption_lines(context)).lower()
    assert "the three arc buckets are one family" in text
    assert "ordinary member of the arc family, not its extreme" in text
    assert "plant family starts above" in text
    assert "break comes at" in text
    for forbidden in (
        "shallow band is kinematically distinct",
        "kinematically distinct from 45",
        "30–45° is the extreme",
        "30-45° is the extreme",
        "30–45° bucket is the arc extreme",
        "30-45° bucket is the arc extreme",
    ):
        assert forbidden not in text


def test_manifest_claim_states_two_family_structure(context, cfg):
    payload = figures.movement_family_manifest_payload(
        context["table"], context, cfg)
    claim = payload["claim"].lower()
    assert "two families, not a gradient" in claim
    assert "ordinary member of the arc family, not its extreme" in claim
    assert "plant family starts above" in claim
    assert "30–45° bucket is the arc extreme" not in claim
    assert "30-45° bucket is the arc extreme" not in claim


def test_zero_entry_speed_is_refused_not_divided(cfg, frame):
    bad = frame.copy()
    bad.loc[bad.index[0], "entry_speed"] = 0.0
    with pytest.raises(common.StageRefused, match="entry_speed"):
        figures.movement_family_table(bad, cfg)
