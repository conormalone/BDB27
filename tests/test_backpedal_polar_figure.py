"""Figure 21 — "every backpedal turn on one protractor, zero down".

Conor, 2026-11-23 19:41: "Ok maybe draw a circle where the line down is zero and
then plot all the angles in the backpedal 45 on it" (confirmed: "That ok").

What is tested here, and why:

* the population: one point per valid turn of the drill, the count and the
  players recomputed from ``turns.parquet`` rather than trusted from the figure;
* the geometry: zero at the bottom and positive running clockwise are properties
  of the DRAWN AXES, checked by transforming probe points to display pixels —
  not by trusting prose. A positive turn angle is the clockwise rotation of the
  travel direction (heading 0 = +y, increasing clockwise), so a point's bearing
  from the down line is the exit travel direction when the entry points down;
* the lobes: counts and medians recomputed from the committed turn table;
* the drawn text: the title (no digits), the count line, both lobe labels, the
  honesty line — no caption, no subtitle, no legend, no banned token;
* no gate: a two-row input still renders, and a 200° angle is drawn at its
  wrapped bearing and flagged;
* the artefacts: CSV schema, manifest entry, byte-identical renders.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bdb27 import common, figures  # noqa: E402

TURNS = ROOT / "outputs" / "turns" / "turns.parquet"
FIGURE = figures.FIGURE_KEY_BACKPEDAL_POLAR
FIGS = ROOT / "outputs" / "figures"

REQUIRED_COLUMNS = [
    "provenance", "stamp", "config_sha", "figure", "event_id", "nfl_id",
    "attempt", "frame_start", "drill_name", "turn_angle_deg",
    "abs_turn_angle_deg", "side", "bearing_deg", "bearing_wrapped",
    "radius_drawn",
]


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(str(ROOT / "config.yaml"))


@pytest.fixture(scope="module")
def built(cfg):
    """Figure 20's route table: the same 133 turns, already reconstructed."""
    return figures.backpedal_route_table(cfg, "sample", TURNS)


@pytest.fixture(scope="module")
def polar(built):
    route_table, route_context = built
    return figures.backpedal_polar_table(
        route_table, route_context,
        position_groups=figures.backpedal_position_counts(pd.read_parquet(TURNS)))


@pytest.fixture(scope="module")
def drawn(polar, cfg):
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        return figures.backpedal_polar_drawn_text(fig, cfg, "sample")
    finally:
        plt.close(fig)


@pytest.fixture(scope="module")
def valid_turns():
    table = pd.read_parquet(TURNS)
    valid = table["valid"].fillna(False).astype(bool)
    return table[(table["setting"] == "combine")
                 & (table["drill_name"] == figures.BACKPEDAL_DRILL) & valid]


def _synthetic(angles, players=None):
    """A minimal route table + context, to test the drawing without a file."""
    rows = []
    for i, angle in enumerate(angles):
        rows.append({
            "event_id": f"E{i}",
            "nfl_id": (players or {}).get(i, 50_000 + i),
            "attempt": 1,
            "frame_start": 10 + i,
            "drill_name": figures.BACKPEDAL_DRILL,
            "turn_angle_deg": float(angle),
            "abs_turn_angle_deg": float(abs(angle)),
        })
    table = pd.DataFrame(rows)
    context = {"drill_name": figures.BACKPEDAL_DRILL,
               "n_players_tracked": 3,
               "n_attempts": len(rows),
               "n_turns_in_turn_table": len(rows)}
    return figures.backpedal_polar_table(table, context)


# ------------------------------------------------------------- 1. population
def test_every_drawn_turn_is_a_defensive_back(polar, valid_turns, cfg):
    """Standing policy is DB only: the figure draws a DB drill and says how many."""
    table, context = polar
    assert set(valid_turns["position_group"]) == {"DB"}
    assert len(valid_turns) == len(table)
    counts = figures.backpedal_position_counts(pd.read_parquet(TURNS))
    assert counts == {"DB": len(table)}
    assert context["position_groups"] == counts


def test_one_point_per_valid_turn(polar, valid_turns):
    table, context = polar
    assert len(table) == len(valid_turns) > 0
    assert context["n_turns"] == len(valid_turns)
    assert context["n_players"] == int(valid_turns["nfl_id"].nunique())
    assert table["drill_name"].eq(figures.BACKPEDAL_DRILL).all()
    # one point per turn, a turn being (event, player, attempt, frame_start):
    # a player-attempt can carry more than one detected turn.
    keys = table[["event_id", "nfl_id", "attempt", "frame_start"]].astype(
        str).agg("|".join, axis=1)
    assert keys.is_unique
    assert table["frame_start"].ge(0).all()


def test_side_partition_and_bearing_mapping(polar, valid_turns):
    table, _ = polar
    assert table["side"].isin({"positive", "negative", "none"}).all()
    counts = table["side"].value_counts()
    assert int(counts.get("positive", 0)) == int((valid_turns["turn_angle"]
                                                 > 0).sum())
    assert int(counts.get("negative", 0)) == int((valid_turns["turn_angle"]
                                                 < 0).sum())
    assert int(counts.sum()) == len(table)
    # the drawn bearing IS the measured angle here (none exceeds 180°)
    assert not bool(table["bearing_wrapped"].any())
    assert np.allclose(table["bearing_deg"], table["turn_angle_deg"],
                       atol=1e-9)
    assert table["bearing_deg"].abs().max() <= 180.0


# ------------------------------------------------------ 2. the drawn geometry
def test_zero_is_at_the_bottom_and_positive_runs_clockwise(polar, cfg):
    """Pinned to the drawn axes, in pixels, not to prose."""
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        assert len(fig.axes) == 1
        ax = fig.axes[0]
        assert ax.name == "polar"
        fig.canvas.draw()
        # zero at South and clockwise: the two settings that make the map
        assert ax.get_theta_direction() == -1
        assert ax.get_theta_offset() == pytest.approx(1.5 * np.pi)
        centre = ax.transData.transform((0.0, 0.0))
        down = ax.transData.transform((0.0, 1.0))
        right_turn = ax.transData.transform((0.5 * np.pi, 1.0))
        assert abs(down[0] - centre[0]) < 1e-6
        assert down[1] < centre[1], "theta 0 must be drawn below the centre"
        assert right_turn[0] < centre[0], (
            "a positive angle must be drawn clockwise, i.e. to the left")
        assert abs(right_turn[1] - centre[1]) < 1e-6
        # radial axis carries no variable: no radial tick labels drawn
        assert [t.get_text() for t in ax.get_yticklabels() if t.get_text()] == []
    finally:
        plt.close(fig)


def test_every_turn_is_drawn_at_its_bearing(polar, cfg):
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax = fig.axes[0]
        # the scattered offsets are (theta, r) in radians / radial units
        collections = [c for c in ax.collections if len(c.get_offsets()) == len(table)]
        assert collections, "the turns are not drawn as one point each"
        offsets = np.asarray(collections[0].get_offsets(), dtype=float)
        assert np.allclose(offsets[:, 0], np.radians(table["bearing_deg"]),
                           atol=1e-9)
        assert np.allclose(offsets[:, 1], table["radius_drawn"].to_numpy(),
                           atol=1e-9)
    finally:
        plt.close(fig)


# ------------------------------------------------------------------ 3. lobes
def test_lobe_stats_are_recomputed_from_the_turn_table(polar, valid_turns):
    _, context = polar
    positive = valid_turns.loc[valid_turns["turn_angle"] > 0, "turn_angle"]
    negative = valid_turns.loc[valid_turns["turn_angle"] < 0, "turn_angle"]
    for side, series in (("positive", positive), ("negative", negative)):
        lobe = context["lobes"][side]
        assert lobe["n"] == len(series)
        assert lobe["median_deg"] == pytest.approx(float(series.median()),
                                                   abs=1e-6)
        assert lobe["q25_deg"] == pytest.approx(float(series.quantile(0.25)),
                                                abs=1e-6)
        assert lobe["q75_deg"] == pytest.approx(float(series.quantile(0.75)),
                                                abs=1e-6)
    assert context["lobes"]["positive"]["median_deg"] > 45.0
    assert context["lobes"]["negative"]["median_deg"] < -45.0
    assert context["n_wrapped"] == 0


# ------------------------------------------------------------- 4. drawn text
def test_drawn_text_is_the_title_annotations_and_honesty_line(drawn, polar):
    table, context = polar
    strings = drawn["strings"]
    assert figures.BACKPEDAL_POLAR_TITLE in strings
    assert not any(ch.isdigit() for ch in figures.BACKPEDAL_POLAR_TITLE)
    assert drawn["banned_hits"] == []
    assert drawn["title"] == figures.BACKPEDAL_POLAR_TITLE
    assert figures.BACKPEDAL_POLAR_HONESTY in strings
    count_line = (f"{context['n_turns']:,} turns from "
                  f"{context['n_players']:,} of "
                  f"{context['n_players_tracked']:,} players tracked in this "
                  "drill")
    assert count_line in strings, count_line
    for side in ("positive", "negative"):
        label = figures._backpedal_polar_lobe_label(side, context["lobes"][side])
        assert label in strings, label
    # the protractor is labelled on scale only
    assert "0" in strings and "45" in strings and "180" in strings
    assert "left" not in " ".join(strings).lower()
    assert "right" not in " ".join(strings).lower()


def test_no_caption_and_no_legend(polar, cfg):
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        assert [t.get_text() for t in fig.texts if t.get_text()] == []
        assert len(fig.axes) == 1
        assert fig.axes[0].get_legend() is None
    finally:
        plt.close(fig)


# ------------------------------------------------------------------- 5. no gate
def test_two_rows_still_render_and_a_wide_angle_wraps(cfg, tmp_path):
    table, context = _synthetic([45.0, 200.0])
    assert len(table) == 2
    assert context["n_wrapped"] == 1
    wide = table[table["turn_angle_deg"] == 200.0].iloc[0]
    assert wide["bearing_deg"] == pytest.approx(-160.0)
    assert bool(wide["bearing_wrapped"]) is True
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        dump = figures.backpedal_polar_drawn_text(fig, cfg, "sample")
        assert figures.BACKPEDAL_POLAR_TITLE in dump["strings"]
        out = figures.render_backpedal_polar(fig, cfg, tmp_path / "two.png")
        assert Path(out).stat().st_size > 0
    finally:
        plt.close("all")


def test_one_row_still_renders(cfg, tmp_path):
    table, context = _synthetic([-113.0])
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        out = figures.render_backpedal_polar(fig, cfg, tmp_path / "one.png")
        assert Path(out).stat().st_size > 0
    finally:
        plt.close("all")


# ------------------------------------------------------------------ 6. CSV
def test_csv_schema_is_exactly_the_spec_columns():
    committed = pd.read_csv(FIGS / f"{FIGURE}.csv")
    assert list(committed.columns) == REQUIRED_COLUMNS
    for column in ("provenance", "stamp", "config_sha", "figure"):
        assert committed[column].notna().all(), column
    assert committed["figure"].eq(FIGURE).all()
    assert committed["drill_name"].eq(figures.BACKPEDAL_DRILL).all()
    assert committed["side"].isin({"positive", "negative", "none"}).all()


# -------------------------------------------------------------- 7. manifest
def test_manifest_entry_and_its_population(valid_turns):
    document = json.loads(
        (FIGS / "figures_manifest.json").read_text(encoding="utf-8"))
    entry = document["figures"][FIGURE]
    assert entry["stage"] == "06_figures"
    assert entry["title"] == figures.BACKPEDAL_POLAR_TITLE
    assert entry["stamp"] == figures.stamp_text("sample")
    assert entry["drill_name"] == figures.BACKPEDAL_DRILL
    assert entry["png"] == f"outputs/figures/{FIGURE}.png"
    assert entry["csv"] == f"outputs/figures/{FIGURE}.csv"
    assert entry["drawn_text"] == f"outputs/figures/{FIGURE}.drawn_text.json"
    assert entry["caveats"] and any("no facing" in c.lower()
                                    for c in entry["caveats"])
    population = entry["population"]
    assert population["n_turns"] == len(valid_turns)
    assert population["n_players"] == int(valid_turns["nfl_id"].nunique())
    assert population["position_groups"] == {"DB": len(valid_turns)}
    assert population["n_positive"] == int((valid_turns["turn_angle"] > 0).sum())
    assert population["n_negative"] == int((valid_turns["turn_angle"] < 0).sum())
    assert population["median_abs_turn_angle"] == pytest.approx(
        float(valid_turns["abs_turn_angle"].median()))
    positive = valid_turns.loc[valid_turns["turn_angle"] > 0, "turn_angle"]
    assert population["lobes"]["positive"]["median_deg"] == pytest.approx(
        float(positive.median()), abs=1e-6)


# ------------------------------------------------------------- determinism
def test_two_renders_are_byte_identical(polar, cfg, tmp_path):
    table, context = polar
    digests = []
    for name in ("one.png", "two.png"):
        fig = figures.plot_backpedal_polar(table, context, cfg)
        out = figures.render_backpedal_polar(fig, cfg, tmp_path / name)
        digests.append(hashlib.sha256(Path(out).read_bytes()).hexdigest())
    assert digests[0] == digests[1]


# ---------------------------------------------------------- committed artefacts
def test_committed_drawn_text_dump_matches_the_spec():
    payload = json.loads(
        (FIGS / f"{FIGURE}.drawn_text.json").read_text(encoding="utf-8"))
    assert payload["figure"] == FIGURE
    assert payload["title"] == figures.BACKPEDAL_POLAR_TITLE
    assert payload["banned_hits"] == []
    assert payload["strings"].count(figures.BACKPEDAL_POLAR_TITLE) == 1
    assert figures.BACKPEDAL_POLAR_HONESTY in payload["strings"]
    assert payload["scan_strings"][0] == figures.BACKPEDAL_POLAR_TITLE
