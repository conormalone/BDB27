"""Figure 16 contract: schema, interval behaviour, the floor, determinism.

These are unit-level tests — no stage run. The fixture is a small, fully
deterministic DB turn table in both settings, plus non-DB rows whose speed
columns are NaN (they must be filtered out before the NaN refusal).
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
    "band_index", "band", "band_low_deg", "band_high_deg", "setting",
    "setting_label", "speed_kind", "n_turns", "n_players", "median_yd_s",
    "ci_low_yd_s", "ci_high_yd_s", "ci_level", "ci_method", "estimable",
    "drawn", "is_focus_band", "min_n_for_estimate", "bootstrap_reps",
    "bootstrap_seed",
]


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
        for setting in ("combine", "game"):
            # The Combine 30-45 bucket is deliberately below the floor: 4
            # turns from 3 players. Every other cell is 10 turns from 4
            # players (turns repeat within a player, as in the real table).
            if setting == "combine" and i == 0:
                n, players = 4, [9001, 9002, 9003, 9001]
            else:
                base = 8000 + (0 if setting == "combine" else 1000) + i * 10
                n, players = 10, [base, base + 1, base + 2, base + 3]
            angles = np.linspace(lo + 0.2, hi - 0.2, n)
            for j, angle in enumerate(angles):
                entry = 3.0 + 0.20 * i + 0.05 * j + (
                    0.01 if setting == "combine" else 0.0)
                rows.append({
                    "setting": setting,
                    "position_group": "DB",
                    "abs_turn_angle": float(angle),
                    "entry_speed": float(entry),
                    "exit_speed": float(entry + 0.30 + 0.01 * ((j * 3 + i) % 3)),
                    "nfl_id": int(players[j % len(players)]),
                    "duration_s": 1.0 if i == 0 else 2.0,
                    "valid": True,
                })
    # Non-DB rows may lack the speed columns; the filter must drop them first.
    rows.append({
        "setting": "game", "position_group": "WR",
        "abs_turn_angle": 40.0, "entry_speed": np.nan, "exit_speed": np.nan,
        "nfl_id": 7001, "duration_s": np.nan, "valid": True,
    })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def built(cfg, frame):
    return figures.entry_exit_speed_table(frame, cfg)


@pytest.fixture(scope="module")
def table(built):
    return built[0]


@pytest.fixture(scope="module")
def context(built):
    return built[1]


def test_csv_schema_and_band_order(table, cfg, frame):
    csv = figures.entry_exit_speed_csv_frame(table, {})
    assert list(csv.columns) == CSV_COLUMNS
    assert len(csv) == 28
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    assert csv["band_index"].tolist() == [i // 4 for i in range(28)]
    assert csv["setting"].unique().tolist() == ["combine", "game"]
    assert csv["speed_kind"].unique().tolist() == ["entry", "exit"]
    for i in range(len(edges) - 1):
        part = csv[csv["band_index"] == i]
        assert (part["band_low_deg"] == edges[i]).all()
        assert (part["band_high_deg"] == edges[i + 1]).all()
    last = csv[csv["band_index"] == len(edges) - 1]
    assert last["band_high_deg"].isna().all()
    assert last["band"].iloc[0].endswith(
        str(cfg.get("figures.hole.bands.overflow_label")) + "°")
    assert csv.loc[csv["band_index"] == 0, "is_focus_band"].all()
    assert not csv.loc[csv["band_index"] > 0, "is_focus_band"].any()


def test_ci_contains_the_median_or_is_absent(table):
    for _, row in table.iterrows():
        if bool(row["estimable"]):
            assert np.isfinite(row["median_yd_s"])
            assert np.isfinite(row["ci_low_yd_s"])
            assert np.isfinite(row["ci_high_yd_s"])
            assert row["ci_low_yd_s"] <= row["median_yd_s"] <= row["ci_high_yd_s"]
            assert bool(row["drawn"]) is True
        else:
            assert pd.isna(row["median_yd_s"])
            assert pd.isna(row["ci_low_yd_s"])
            assert pd.isna(row["ci_high_yd_s"])
            assert bool(row["drawn"]) is False


def test_the_floor_is_a_floor_not_a_warning(table):
    combine = table[table["setting"] == "combine"]
    band0 = combine[combine["band_index"] == 0]
    assert band0["n_turns"].iloc[0] == 4
    assert band0["n_players"].iloc[0] == 3
    assert not band0["estimable"].any()
    assert not band0["drawn"].any()
    above = combine[combine["n_turns"] >= 10]
    assert above["estimable"].all()
    assert above["drawn"].all()


def test_no_ci_is_drawn_on_the_four_turn_bucket(table, context, cfg):
    import matplotlib.pyplot as plt
    from matplotlib.container import ErrorbarContainer

    fig = figures.plot_entry_exit_speed(table, context, cfg)
    try:
        fig.canvas.draw()
        ax = fig.axes[0]
        xs: set[float] = set()
        for container in ax.containers:
            if isinstance(container, ErrorbarContainer):
                xs |= {round(float(x), 6)
                       for x in np.ravel(container.lines[0].get_xdata())}
        assert 0.07 not in xs, "a CI was drawn at the Combine entry offset"
        assert 0.21 not in xs, "a CI was drawn at the Combine exit offset"
        texts = " ".join(t.get_text() for t in fig.findobj(plt.Text))
        assert "n=4, no estimate" in texts
    finally:
        plt.close(fig)


def test_caption_and_manifest_carry_the_honesty_clauses(table, context, cfg):
    text = " ".join(" ".join(figures._entry_exit_speed_caption(context)).split())
    assert "exit speed is measured at the end of the detector window, not at a fixed distance, so exit > entry mostly means the athlete was accelerating when the window closed" in text
    shallow = context["duration"]["shallow_median_s"]
    other = context["duration"]["other_median_s"]
    assert (f"the shallow 30–45° bucket holds short turns (median "
            f"{shallow:.1f} s vs {other:.1f} s), so a fixed-length window "
            "flatters sharp cuts and the shallow bucket differently") in text
    assert "Combine confidence intervals are drawn only where a band holds at least 10 turns" in text
    assert "30–45° Combine band is annotated, never drawn" in text
    for _, row in table[(table["setting"] == "combine")
                        & (table["speed_kind"] == "entry")
                        & table["estimable"]].iterrows():
        assert (f"{row['band']} entry {row['median_yd_s']:.2f}") in text

    payload = figures.entry_exit_speed_manifest_payload(table, context, cfg)
    caveats = " ".join(payload["caveats"])
    assert "exit speed is measured at the end of the detector window" in caveats
    assert "flatters sharp cuts and the shallow bucket differently" in caveats
    assert "Combine confidence intervals are drawn only where a band holds at least 10 turns" in caveats
    assert "exit median exceeds the entry median" in payload["claim"]


def test_two_renders_are_byte_identical(table, context, cfg, tmp_path):
    digests = []
    for i in range(2):
        fig = figures.plot_entry_exit_speed(table, context, cfg)
        out = tmp_path / f"ees_{i}.png"
        figures.render_entry_exit_speed(fig, cfg, out)
        digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


def test_reference_cases_from_the_brief(monkeypatch):
    """Hand-checked bootstrap fixtures; the seed scheme and medians are exact."""
    monkeypatch.setattr(stats, "MIN_BOOTSTRAP_REPS", 100)
    first = stats.cluster_bootstrap_median(
        [1, 2, 3, 4, 5, 6], [1, 1, 2, 2, 3, 3], reps=200, ci_level=0.90,
        label="fixture|entry|30-45", base_seed=20270106)
    assert first == {
        "median": 3.5, "ci_low": 2.0, "ci_high": 5.0, "ci_level": 0.90,
        "reps": 200, "seed": 4182329864, "n_turns": 6, "n_players": 3,
        "estimable": True,
    }
    second = stats.cluster_bootstrap_median(
        [1.0, 1.5, 2.0, 3.0, 4.0, 4.5, 9.0], [7, 7, 7, 8, 8, 9, 9],
        reps=500, ci_level=0.95, label="fixture|exit|45-60",
        base_seed=20270106)
    assert second == {
        "median": 3.0, "ci_low": 1.5, "ci_high": 6.75, "ci_level": 0.95,
        "reps": 500, "seed": 2969794065, "n_turns": 7, "n_players": 3,
        "estimable": True,
    }


def test_bootstrap_floor_and_length_checks():
    with pytest.raises(ValueError):
        stats.cluster_bootstrap_median(
            [1.0, 2.0], [1, 2], reps=stats.MIN_BOOTSTRAP_REPS - 1,
            ci_level=0.95, label="x", base_seed=1)
    with pytest.raises(ValueError):
        stats.cluster_bootstrap_median(
            [1.0, 2.0], [1], reps=stats.MIN_BOOTSTRAP_REPS,
            ci_level=0.95, label="x", base_seed=1)


def test_bands_are_the_shared_reporting_bands(table, cfg):
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    assert table["band"].unique().tolist() == figures.band_labels(edges, overflow)
    assert table["band_low_deg"].drop_duplicates().tolist() == edges
