"""Figure 21 — "Every turn in the backpedal".

Conor, 2026-11-23 19:41: "Ok maybe draw a circle where the line down is zero and
then plot all the angles in the backpedal 45 on it" (confirmed: "That ok"),
revised 2026-11-24 09:09: "Could you reverse the X axis so zero is up and remove
the text on the plot" (D215), retitled 2026-11-24 11:03: "Make the title 'every
turn in the backpedal'" (D216).

What is tested here, and why:

* the population: one point per valid turn of the drill, the count and the
  players recomputed from ``turns.parquet`` rather than trusted from the figure;
* the geometry: zero at the top and positive running clockwise are properties of
  the DRAWN AXES, checked by transforming probe points to display pixels — not by
  trusting prose. A positive turn angle is the clockwise rotation of the travel
  direction (heading 0 = +y, increasing clockwise), so a point's bearing from the
  top line is the exit travel direction when the entry points up, read as an
  ordinary compass bearing;
* the scale: one full circle labelled 0, 45, ... 315 — not the mirrored half
  scale the first cut drew (Conor, 2026-11-23 22:06: "The other plots have it on
  a 360 scale so please do the same", D214), and no duplicate 360 on the 0 spoke
  (Conor, 2026-11-24 09:12: "And just zero, remove the 360", D215);
* the lobes: counts and medians recomputed from the committed turn table, with
  each median a bearing on that 0-360 scale (243.7°, not -116.3°);
* the drawn text: the title (no digits) and the tick scale, and NOTHING else —
  the lobe labels, the count line and the honesty line Conor asked off the plot
  must stay off it (D215), with no caption, no subtitle, no legend, no banned
  token;
* the overhead player at the centre (D217, Conor 2026-11-24 11:24): three
  vector patches — shoulders, chest wedge, head — centred on the origin, facing
  the 0° spoke at the top, drawn under the turns and clear of the nearest dot
  by the real ``min(radius_drawn)``, with no text of its own;
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
    # the drawn bearing is the measured angle read clockwise from zero-down on
    # a 0-360 scale; the signed measurement stays beside it in the CSV
    assert not bool(table["bearing_wrapped"].any())
    assert np.allclose(table["bearing_deg"],
                       np.mod(table["turn_angle_deg"], 360.0), atol=1e-9)
    assert table["bearing_deg"].ge(0.0).all()
    assert table["bearing_deg"].lt(360.0).all()


# ------------------------------------------------------ 2. the drawn geometry
def test_zero_is_at_the_top_and_positive_runs_clockwise(polar, cfg):
    """Pinned to the drawn axes, in pixels, not to prose (D215)."""
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        assert len(fig.axes) == 1
        ax = fig.axes[0]
        assert ax.name == "polar"
        fig.canvas.draw()
        # zero at North and clockwise: the two settings that make a compass rose
        assert ax.get_theta_direction() == -1
        # matplotlib measures theta from East, so North is +90 deg of offset
        assert ax.get_theta_offset() == pytest.approx(0.5 * np.pi)
        centre = ax.transData.transform((0.0, 0.0))
        top = ax.transData.transform((0.0, 1.0))
        right_turn = ax.transData.transform((0.5 * np.pi, 1.0))
        assert abs(top[0] - centre[0]) < 1e-6
        assert top[1] > centre[1], "theta 0 must be drawn above the centre"
        assert right_turn[0] > centre[0], (
            "a positive angle must be drawn clockwise, i.e. to the right")
        assert abs(right_turn[1] - centre[1]) < 1e-6
        # radial axis carries no variable: no radial tick labels drawn
        assert [t.get_text() for t in ax.get_yticklabels() if t.get_text()] == []
    finally:
        plt.close(fig)


# ------------------------------------------- 2b. the scale is a full circle
def test_protractor_is_labelled_zero_to_315_with_no_duplicate_360(polar, cfg):
    """One full circle, and the 0 spoke carries "0" only (D214, D215).

    The first cut labelled the left half 0..180 and mirrored it back down the
    right (0, 45, 90, 135, 180, 135, 90, 45). D214 made it one full circle
    0..360; the "360" shared the 0 spoke and was nudged aside to stay legible.
    Conor, 2026-11-24 09:12: "And just zero, remove the 360" — the scale now
    ends at 315 and prints no duplicate.
    """
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax = fig.axes[0]
        fig.canvas.draw()
        labels = [t.get_text() for t in ax.get_xticklabels()]
        expected = [str(tick) for tick in range(0, 360, 45)]
        assert labels == expected, labels
        # no value printed twice: the mirrored half is gone
        assert len(set(labels)) == len(labels) == 8
        # and the duplicate 360 is off the canvas entirely
        assert "360" not in labels
        assert "360" not in figures.backpedal_polar_drawn_text(
            fig, cfg, "sample")["strings"]
        ticks = np.degrees(np.asarray(ax.get_xticks(), dtype=float))
        assert np.allclose(ticks, np.arange(0.0, 360.0, 45.0))
        # and the scale now starts at the top (D215)
        centre = ax.transData.transform((0.0, 0.0))
        assert ax.transData.transform((0.0, 1.0))[1] > centre[1]
    finally:
        plt.close(fig)


def test_bearings_are_a_zero_to_360_reading_and_turns_stay_signed(
        polar, valid_turns):
    """The display scale moved; the measurement did not."""
    table, _ = polar
    measured = valid_turns["turn_angle"].to_numpy(float)
    assert np.allclose(np.sort(table["turn_angle_deg"].to_numpy(float)),
                       np.sort(measured), atol=1e-9)
    assert table["turn_angle_deg"].min() < 0.0 < table["turn_angle_deg"].max()
    assert np.allclose(np.sort(table["bearing_deg"].to_numpy(float)),
                       np.sort(np.mod(measured, 360.0)), atol=1e-9)
    assert table["bearing_deg"].ge(0.0).all()
    assert table["bearing_deg"].lt(360.0).all()


def test_lobe_medians_are_stated_as_bearings(polar, cfg, valid_turns):
    """Counter-clockwise -116.3 deg is reported as 243.7 deg.

    The median is a bearing on the drawn 0-360 scale (D214). It used to be
    printed inside the circle; since D215 the plot carries no text, so the
    manifest claim is where the reading has to hold.
    """
    table, context = polar
    negative = valid_turns.loc[valid_turns["turn_angle"] < 0, "turn_angle"]
    bearing = float(np.median(np.mod(negative.to_numpy(float), 360.0)))
    assert context["lobes"]["negative"]["median_deg"] == pytest.approx(
        bearing, abs=1e-6)
    assert context["lobes"]["negative"]["n"] == len(negative)
    claim = figures.backpedal_polar_manifest_payload(
        table, context, cfg, "sample")["claim"]
    assert "243.7" in claim, claim
    assert "111.0" in claim, claim
    assert "-116" not in claim, claim
    assert f"n = {len(negative):,}" in claim, claim
    assert "0-360" in claim, claim


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
        bearing = np.mod(series.to_numpy(float), 360.0)
        assert lobe["n"] == len(series)
        assert lobe["median_deg"] == pytest.approx(float(np.median(bearing)),
                                                   abs=1e-6)
        assert lobe["q25_deg"] == pytest.approx(
            float(np.percentile(bearing, 25)), abs=1e-6)
        assert lobe["q75_deg"] == pytest.approx(
            float(np.percentile(bearing, 75)), abs=1e-6)
    # both medians are bearings: the clockwise lobe sits in the right half of
    # the circle, the counter-clockwise lobe past straight up (180 deg)
    assert 45.0 < context["lobes"]["positive"]["median_deg"] < 180.0
    assert 180.0 < context["lobes"]["negative"]["median_deg"] < 360.0
    assert context["n_wrapped"] == 0


# ------------------------------------ 3b. the overhead player at the centre
def _glyph(fig):
    """The drawn player glyph: patches, plus their geometry through the axes.

    Measured from the transform chain (``transAxes`` -> display -> polar data),
    never from the constants: whatever is actually on the canvas is what is
    asserted below.
    """
    ax = fig.axes[0]
    fig.canvas.draw()
    centre = ax.transData.transform((0.0, 0.0))
    measured = []
    for patch in ax.patches:
        display = patch.get_transform().transform(patch.get_path().vertices)
        theta, radius = ax.transData.inverted().transform(display).T
        measured.append({"patch": patch, "display": np.asarray(display),
                         "theta": np.asarray(theta),
                         "radius": np.asarray(radius)})
    return ax, centre, measured


def test_the_overhead_player_sits_at_the_centre_facing_the_zero_spoke(
        polar, cfg):
    """Conor, 2026-11-24 11:24: an overhead player, facing the way 0 reads.

    Three vector patches: shoulders, chest wedge, head. The chest points up the
    0 spoke; the shoulders are drawn across it, so the facing is read from the
    body, not from a label or an arrow annotation.
    """
    from matplotlib.patches import Ellipse, Polygon

    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax, centre, glyph = _glyph(fig)
        assert len(glyph) == 3, [type(g["patch"]).__name__ for g in glyph]
        assert sorted(type(g["patch"]).__name__ for g in glyph) == [
            "Ellipse", "Ellipse", "Polygon"]
        assert all(isinstance(g["patch"], (Ellipse, Polygon)) for g in glyph)

        # the widest patch is the shoulders: wider across than deep, horizontal
        shoulders = max(glyph, key=lambda g: np.ptp(g["display"][:, 0]))
        width = np.ptp(shoulders["display"][:, 0])
        depth = np.ptp(shoulders["display"][:, 1])
        assert width > 1.5 * depth, (width, depth)
        shoulders_mid = 0.5 * (shoulders["display"].min(axis=0)
                               + shoulders["display"].max(axis=0))
        assert abs(shoulders_mid[0] - centre[0]) < 1.0
        assert abs(shoulders_mid[1] - centre[1]) < 1.0

        # the chest wedge: its furthest vertex is on the 0 spoke, above centre
        px_per_unit = abs(centre[1] - ax.transData.transform(
            (0.0, figures.BACKPEDAL_POLAR_RMAX))[1]) / figures.BACKPEDAL_POLAR_RMAX
        apex_radius, apex = max((float(g["radius"].max()), g) for g in glyph)
        assert apex_radius == pytest.approx(
            figures.BACKPEDAL_POLAR_MARKER_EXTENT, abs=1e-6)
        top = apex["display"][np.argmax(apex["radius"])]
        assert abs(top[0] - centre[0]) < 0.5, "the front must be on the 0 spoke"
        assert top[1] - centre[1] == pytest.approx(
            apex_radius * px_per_unit, abs=0.5), (
            "the front must point up the page, where 0 is drawn")

        # the head: a round circle, not a stretched ellipse
        head = min((g for g in glyph if isinstance(g["patch"], Ellipse)),
                   key=lambda g: np.ptp(g["display"][:, 0]))
        head_w = np.ptp(head["display"][:, 0])
        head_h = np.ptp(head["display"][:, 1])
        assert abs(head_w - head_h) / head_w < 0.01, (head_w, head_h)
        head_mid = 0.5 * (head["display"].min(axis=0)
                          + head["display"].max(axis=0))
        assert abs(head_mid[0] - centre[0]) < 1.0
        assert abs(head_mid[1] - centre[1]) < 1.0

        # vector only: the glyph carries no text of its own
        assert [t.get_text() for t in ax.texts] == [
            figures.BACKPEDAL_POLAR_TITLE]
        assert all((patch.get_label() or "") in ("", "_nolegend_") or
                   patch.get_label().startswith("_")
                   for patch in ax.patches)
    finally:
        plt.close(fig)


def test_the_player_glyph_stays_clear_of_every_drawn_turn(polar, cfg):
    """It is drawn under the turns and never reaches one (D217).

    The clearance is asserted against the actual smallest ``radius_drawn`` in
    the drawn table, not a guessed threshold, and against the 0.80 x RMAX
    canvas budget: a marker that grew into the dots would cover data.
    """
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax, _, glyph = _glyph(fig)
        drawn_max = max(float(g["radius"].max()) for g in glyph)
        nearest_dot = float(table["radius_drawn"].min())
        margin = 0.05
        assert drawn_max <= nearest_dot - margin, (drawn_max, nearest_dot)
        assert drawn_max < 0.80 * figures.BACKPEDAL_POLAR_RMAX, drawn_max
        assert len(table) > 0
        # and it is below the scatter in z-order, so a dot always wins
        scatter = [c for c in ax.collections
                   if len(c.get_offsets()) == len(table)]
        assert scatter
        assert max(p.get_zorder() for p in ax.patches) < min(
            c.get_zorder() for c in scatter)
        # the median spokes start at the origin, so they run under the glyph:
        # the bearing lines emerge from the player instead of crossing his head
        spokes = [line for line in ax.lines
                  if line.get_color() == figures.ALERT
                  and np.ptp(np.asarray(line.get_xdata(), dtype=float)) == 0.0]
        assert spokes, "the lobe medians are not drawn as radial spokes"
        assert max(line.get_zorder() for line in spokes) < min(
            p.get_zorder() for p in ax.patches)
    finally:
        plt.close(fig)


def test_the_player_glyph_is_a_schematic_not_data(cfg, tmp_path):
    """No gate: it is drawn even when the table is empty."""
    table, context = _synthetic([])
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax, centre, glyph = _glyph(fig)
        assert len(glyph) == 3
        assert max(float(g["radius"].max()) for g in glyph) == pytest.approx(
            figures.BACKPEDAL_POLAR_MARKER_EXTENT, abs=1e-6)
        out = figures.render_backpedal_polar(fig, cfg, tmp_path / "empty.png")
        assert Path(out).stat().st_size > 0
    finally:
        plt.close("all")


# ------------------------------------------------------------- 4. drawn text
def test_drawn_text_is_only_the_title_and_the_scale(drawn):
    """D215: the canvas carries the title and the tick scale, nothing else.

    Ticks run 0 ... 315; the 360 that used to share the 0 spoke is gone
    (Conor, 2026-11-24 09:12: "And just zero, remove the 360").
    """
    expected = ([figures.BACKPEDAL_POLAR_TITLE]
                + [str(tick) for tick in range(0, 360, 45)])
    assert drawn["strings"] == expected, drawn["strings"]
    assert drawn["scan_strings"] == expected, drawn["scan_strings"]
    assert "360" not in drawn["strings"]
    assert not any(ch.isdigit() for ch in figures.BACKPEDAL_POLAR_TITLE)
    assert drawn["banned_hits"] == []
    assert drawn["title"] == figures.BACKPEDAL_POLAR_TITLE
    assert "left" not in " ".join(expected).lower()
    assert "right" not in " ".join(expected).lower()


def test_the_lobe_labels_count_line_and_honesty_line_stay_off_the_plot(
        polar, cfg):
    """The text Conor asked off the plot does not come back (D215).

    On canvas until 2026-11-24: "clockwise side: n = 63, median 111.0°",
    "counter-clockwise side: n = 70, median 243.7°", "133 turns from 71 of 122
    players tracked in this drill", and the two-line honesty note. If any of
    them returns, this fails.
    """
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax = fig.axes[0]
        assert [t.get_text() for t in ax.texts] == [
            figures.BACKPEDAL_POLAR_TITLE], "the axes draws more than its title"
        assert [t.get_text() for t in fig.texts if t.get_text()] == []
        drawn = " | ".join(t.get_text() for t in ax.texts)
        for gone in ("clockwise side", "counter-clockwise side", "median ",
                     "turns from", "players tracked",
                     "exit direction of travel", "records no facing",
                     "each point is"):
            assert gone not in drawn, gone
    finally:
        plt.close(fig)


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
    # a 200 deg turn is drawn where -160 deg lands on the 0-360 scale (D214)
    assert wide["bearing_deg"] == pytest.approx(200.0)
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
        float(np.median(np.mod(positive.to_numpy(float), 360.0))), abs=1e-6)
    assert population["lobes"]["negative"]["median_deg"] > 180.0


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
    assert payload["strings"] == ([figures.BACKPEDAL_POLAR_TITLE]
                                  + [str(tick) for tick in range(0, 360, 45)])
    assert payload["scan_strings"][0] == figures.BACKPEDAL_POLAR_TITLE
