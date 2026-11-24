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
* the overhead player at the centre (D217), replaced by the image Conor
  supplied (D218, 2026-11-24 13:35: "Use this image instead of whatever that is
  and drop all the red") and drawn in his own colours (D219, 2026-11-24 15:29:
  "Ok, try it again, use the original colours of the player this time"): the
  committed image, its light grey background masked from a threshold estimated
  off the border, the subject's source RGB carried through the mask, centred on
  the origin, aspect preserved, drawn under the turns and clear of the nearest
  dot by the real ``min(radius_drawn)``, with no text of its own;
* red is confined to that marker: the lobe medians, the middle-half arcs and the
  provenance stamp are neutral ink, and a scan of the rendered PNG counts the
  reddish pixels inside and outside the marker's drawn footprint — inside may be
  the jersey, outside must be zero (D218's "drop all the red" still governs the
  plot furniture; D219 restores Conor's image colours);
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


# ------------------------------- 3b. the overhead player image at the centre
def _marker_radii(ax, image) -> dict:
    """Corner and centre radii of the drawn image, in polar data units.

    D220's nudge is horizontal, so the image's LEFT corners are the ones that
    reach furthest from the origin and the ones the clearance check must name;
    both sides are reported so the test asserts the side it means.
    """
    fig = ax.figure
    fig.canvas.draw()
    x0, x1, y0, y1 = image.get_extent()
    inverse = ax.transData.inverted()

    def radius(x, y):
        return float(inverse.transform(ax.transAxes.transform((x, y)))[1])

    left = max(radius(x0, y0), radius(x0, y1))
    right = max(radius(x1, y0), radius(x1, y1))
    # the image's own centre: D220 moved it, so (0.5, 0.5) is no longer it
    centre = inverse.transform(
        ax.transAxes.transform(((x0 + x1) / 2.0, (y0 + y1) / 2.0)))
    return {"left": left, "right": right, "corner": max(left, right),
            "centre_theta": float(centre[0]),
            "centre_radius": float(centre[1])}


def test_the_overhead_player_is_the_supplied_image_at_the_centre(polar, cfg):
    """Conor, 2026-11-24 13:35: "Use this image instead of whatever that is".

    The marker is one drawn image (not vector patches), centred on the polar
    origin, drawn above the lobe spokes and below the turns, with its own
    aspect ratio preserved — so the subject is not stretched into the circle.
    D220 then moved him 10 saved px left on Conor's instruction (2026-11-24
    16:25), so the centre is deliberately off the exact origin; the test now
    asserts the offset he should have instead of a centred glyph.
    """
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax = fig.axes[0]
        fig.canvas.draw()
        assert len(ax.images) == 1, "the centre marker is not one drawn image"
        image = ax.images[0]
        assert not ax.patches, "the removed vector glyph is still drawn"
        radii = _marker_radii(ax, image)
        assert radii["corner"] > 0.2, radii["corner"]
        # D220: he sits off the exact origin by design — the nudge, and only
        # in x: |radial_x| out along the screen-left (270 deg) direction.
        offset = figures.backpedal_polar_marker_offset_metadata(fig)
        assert radii["centre_radius"] == pytest.approx(
            abs(offset["radial_x"]), rel=1e-9), radii
        x0, x1, _, _ = image.get_extent()
        centre_axes_x = (x0 + x1) / 2.0
        assert centre_axes_x - 0.5 == pytest.approx(
            offset["axes_fraction_x"], rel=1e-9), centre_axes_x
        assert centre_axes_x < 0.5, "D220: he sits LEFT of the dial centre"
        assert offset["radial_x"] < 0.0

        # aspect preserved: drawn pixels / source pixels, both dimensions
        rgba = np.asarray(image.get_array(), dtype=float)
        x0, x1, y0, y1 = image.get_extent()
        fraction_x, fraction_y = figures._polar_axes_fraction_per_unit(ax, fig)
        drawn_w = (x1 - x0) / fraction_x
        drawn_h = (y1 - y0) / fraction_y
        source_w, source_h = rgba.shape[1], rgba.shape[0]
        assert drawn_w / drawn_h == pytest.approx(source_w / source_h,
                                                  rel=1e-9)

        # under the turns, above the median spokes
        scatter = [c for c in ax.collections if len(c.get_offsets()) == len(table)]
        assert scatter
        assert image.get_zorder() < min(c.get_zorder() for c in scatter)
        spokes = [line for line in ax.lines
                  if np.ptp(np.asarray(line.get_xdata(), dtype=float)) == 0.0
                  and np.asarray(line.get_ydata(), dtype=float).max() > 0.5]
        assert spokes, "the lobe medians are not drawn as radial spokes"
        assert image.get_zorder() > max(line.get_zorder() for line in spokes)

        # clipped to the polar axes, and it carries no text of its own
        assert image.get_clip_path() is not None
        assert [t.get_text() for t in ax.texts] == [
            figures.BACKPEDAL_POLAR_TITLE]
    finally:
        plt.close(fig)


def test_the_marker_keeps_the_players_own_colours(polar, cfg):
    """D219: the source RGB survives the mask; only the alpha is the mask.

    Conor, 2026-11-24 15:29: "Ok, try it again, use the original colours of the
    player this time". The alpha channel is still the mask, so the subject's
    internal shape (not a rectangle) is what gets painted — but the painted
    pixels are the jersey and kit in their own colours, not a grey ramp.
    """
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        image = fig.axes[0].images[0]
        rgba = np.asarray(image.get_array(), dtype=float)
        assert rgba.shape[2] == 4
        # the mask is a subject silhouette, not a full rectangle
        alpha = rgba[:, :, 3]
        assert alpha.min() == 0.0
        assert alpha.max() == pytest.approx(1.0, abs=1e-6)
        opaque = alpha > 0.5 * alpha.max()
        assert 0.05 < opaque.mean() < 0.95, opaque.mean()
        # original colours: the jersey's channel spread is carried through, not
        # flattened to grey (D218's neutral ramp is gone)
        rgb = rgba[:, :, :3]
        assert not np.array_equal(rgb[:, :, 0], rgb[:, :, 1])
        assert not np.array_equal(rgb[:, :, 0], rgb[:, :, 2])
        mean = rgb[opaque].mean(axis=0)
        assert mean[0] > mean[1] + 0.05, mean
        assert mean[0] > mean[2] + 0.05, mean
        dominance = rgb[:, :, 0] - 0.5 * (rgb[:, :, 1] + rgb[:, :, 2])
        assert dominance[opaque].max() > 0.3, dominance[opaque].max()
    finally:
        plt.close(fig)


def test_marker_threshold_is_estimated_from_the_image_border():
    """The masking threshold is measured and printed, never a magic number."""
    meta = figures._backpedal_polar_marker_metadata()
    assert meta["red_threshold"] == pytest.approx(
        meta["border_red_p99"] + figures.BACKPEDAL_POLAR_MARKER_RED_MARGIN)
    assert meta["subject_pixels"] > 0
    left, top, right, bottom = meta["source_crop_bbox_xyxy"]
    assert right > left and bottom > top
    assert meta["alpha_peak"] == pytest.approx(1.0, abs=1e-6)
    assert meta["colour_mode"] == "original"
    source = ROOT / meta["path"]
    assert source.exists()
    assert meta["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert meta["rotation_deg"] == figures.BACKPEDAL_POLAR_MARKER_ROTATION_DEG


def test_the_marker_stays_clear_of_every_drawn_turn(polar, cfg):
    """It is drawn under the turns and never reaches one.

    The clearance is asserted against the actual smallest ``radius_drawn`` in
    the drawn table plus the code's own margin, not a guessed threshold, and
    the radius is measured through the transform chain — not trusted from the
    constants. D220 moved the marker LEFT, so the left corners are the ones
    that reach furthest out; the assertion names that side, and the nearest
    drawn dot on this data is at 0.9017 radial units.
    """
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax = fig.axes[0]
        radii = _marker_radii(ax, ax.images[0])
        nearest_dot = float(table["radius_drawn"].min())
        assert radii["left"] >= radii["right"], radii
        assert radii["left"] + figures.BACKPEDAL_POLAR_MARKER_MARGIN <= \
            nearest_dot, (radii, nearest_dot)
        assert nearest_dot == pytest.approx(0.9017, abs=5e-4)
    finally:
        plt.close(fig)


def test_the_player_marker_is_a_schematic_not_data(cfg, tmp_path):
    """No gate: it is drawn even when the table is empty."""
    table, context = _synthetic([])
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax = fig.axes[0]
        assert len(ax.images) == 1
        out = figures.render_backpedal_polar(fig, cfg, tmp_path / "empty.png")
        assert Path(out).stat().st_size > 0
    finally:
        plt.close("all")


def test_the_marker_moves_ten_pixels_left_in_the_saved_png(
        polar, cfg, tmp_path, monkeypatch):
    """D220: Conor, 2026-11-24 16:25: "Could you move him 10 pixels to the left".

    Measured in the SAVED file, not read off the constant. The save uses
    ``bbox_inches="tight"``, so the honest proof is a paired render: the same
    figure drawn with the committed offset and with it forced to zero, both
    rendered through the real save path, then the jersey's red footprint
    centroid compared in PNG pixels. If the tight crop absorbed the nudge the
    difference would be smaller than the constant; if it exaggerated it,
    larger. It must be 10 px, within a pixel, in x only.
    """
    table, context = polar
    offset_px = figures.BACKPEDAL_POLAR_MARKER_OFFSET_PX_X
    centroids = {}
    sizes = {}
    for name in ("shifted.png", "unshifted.png"):
        if name == "unshifted.png":
            monkeypatch.setattr(
                figures, "BACKPEDAL_POLAR_MARKER_OFFSET_PX_X", 0.0)
        fig = figures.plot_backpedal_polar(table, context, cfg)
        out = figures.render_backpedal_polar(fig, cfg, tmp_path / name)
        footprint = figures.backpedal_polar_red_footprint(out)
        centroids[name] = footprint["centroid_px"]
        sizes[name] = footprint["png_size_px"]
    # the crop itself must not move, or "10 px in the file" is not measurable
    assert sizes["shifted.png"] == sizes["unshifted.png"]
    dx = centroids["shifted.png"][0] - centroids["unshifted.png"][0]
    dy = centroids["shifted.png"][1] - centroids["unshifted.png"][1]
    print(f"marker red-centroid: unshifted x = "
          f"{centroids['unshifted.png'][0]:.3f}, shifted x = "
          f"{centroids['shifted.png'][0]:.3f}, dx = {dx:.3f} px, "
          f"dy = {dy:.3f} px")
    assert dx == pytest.approx(offset_px, abs=1.0)
    assert abs(dy) < 1.0, dy


# ------------------------------------------------------------- 4. drawn text
def test_the_lobes_keep_their_geometry_in_neutral_ink(polar, cfg):
    """D218: the medians and arcs stay, in grey — nothing is deleted."""
    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        ax = fig.axes[0]
        colours = [line.get_color() for line in ax.lines]
        assert figures.ALERT not in colours
        spokes = [line for line in ax.lines
                  if np.ptp(np.asarray(line.get_xdata(), dtype=float)) == 0.0
                  and np.asarray(line.get_ydata(), dtype=float).max() > 0.5]
        arcs = [line for line in ax.lines
                if len(np.asarray(line.get_xdata())) == 64]
        assert len(spokes) == sum(
            1 for side in ("positive", "negative")
            if context["lobes"][side]["n"] > 0)
        assert len(arcs) == len(spokes)
        assert all(line.get_color() == figures.BACKPEDAL_POLAR_MEDIAN_INK
                   for line in spokes)
        assert all(line.get_color() == figures.BACKPEDAL_POLAR_ARC_INK
                   for line in arcs)
        # the spokes still run to the documented radial extent
        for line in spokes:
            assert np.asarray(line.get_ydata(), dtype=float).max() == \
                pytest.approx(figures.BACKPEDAL_POLAR_MEDIAN_TOP)
    finally:
        plt.close(fig)


def _marker_footprint(png, fig, image, pad_px=2) -> np.ndarray:
    """The marker's drawn extent mapped onto the saved PNG's pixel grid.

    ``render_backpedal_polar`` saves with ``bbox_inches="tight"`` and
    ``pad_inches=0.25``, so the canvas and the PNG do not share an origin. The
    crop is rebuilt from the same tight bbox the save used, and the image's
    display-space extent is mapped into the cropped grid; ``pad_px`` absorbs the
    one-pixel rounding in that crop and the antialiased edge.
    """
    from PIL import Image

    fig.canvas.draw()
    # the save is at the figure's own dpi, not the on-screen canvas dpi, so
    # measure the crop with a renderer at that dpi
    dpi = figures.BACKPEDAL_POLAR_DPI
    fig.set_dpi(dpi)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    tight = fig.get_tightbbox(renderer)          # inches
    pad = 0.25
    x0 = (tight.x0 - pad) * dpi                  # crop origin, display px
    y0 = (tight.y0 - pad) * dpi
    with Image.open(png) as handle:
        width, height = handle.size
    # the tight crop we rebuild must be the PNG we are scanning
    assert abs((tight.width + 2.0 * pad) * dpi - width) <= 1.0, (
        (tight.width + 2.0 * pad) * dpi, width)
    assert abs((tight.height + 2.0 * pad) * dpi - height) <= 1.0, (
        (tight.height + 2.0 * pad) * dpi, height)

    extent = image.get_window_extent(renderer)   # display px, origin bottom-left
    left = extent.x0 - x0
    right = extent.x1 - x0
    # PNG rows run top-down; display y runs bottom-up
    bottom = height - (extent.y1 - y0)
    top = height - (extent.y0 - y0)
    footprint = np.zeros((height, width), dtype=bool)
    footprint[max(int(np.floor(bottom)) - pad_px, 0):
              min(int(np.ceil(top)) + pad_px, height),
              max(int(np.floor(left)) - pad_px, 0):
              min(int(np.ceil(right)) + pad_px, width)] = True
    return footprint


def test_red_is_confined_to_the_marker_footprint(polar, cfg, tmp_path):
    """D219: the jersey's red is allowed inside the marker, nowhere else.

    D218 required zero reddish pixels on the whole PNG, which was right while
    the subject was forced to neutral ink. Conor, 2026-11-24 15:29: "use the
    original colours of the player this time" — his image legitimately carries
    red, so the invariant moves with the request: red inside the marker's drawn
    footprint may be the jersey (and must be present, or the colours are not
    original), red outside it must be zero. The plot furniture stays neutral.
    """
    from PIL import Image

    table, context = polar
    fig = figures.plot_backpedal_polar(table, context, cfg)
    out = figures.render_backpedal_polar(fig, cfg, tmp_path / "colours.png")
    # render_* closed that figure; rebuild it for the footprint mapping — the
    # drawing is deterministic, so the geometry is the same
    fig = figures.plot_backpedal_polar(table, context, cfg)
    try:
        footprint = _marker_footprint(out, fig, fig.axes[0].images[0])
    finally:
        plt.close(fig)
    # the footprint must be the marker's box, not the whole canvas: otherwise
    # "zero outside" would be vacuous
    assert 0.01 < footprint.mean() < 0.25, footprint.mean()
    pixels = np.asarray(Image.open(out).convert("RGB"), dtype=float) / 255.0
    red = pixels[:, :, 0]
    green = pixels[:, :, 1]
    blue = pixels[:, :, 2]
    reddish = (red > 0.35) & (red - np.maximum(green, blue) > 0.10)
    inside = int((reddish & footprint).sum())
    outside = int((reddish & ~footprint).sum())
    print(f"red pixels: inside the marker footprint = {inside:,}, "
          f"outside = {outside:,}")
    assert inside > 0, "the jersey's own red did not reach the canvas"
    assert outside == 0, f"{outside:,} reddish pixels outside the marker"


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


def test_sidecar_records_the_offset_and_the_measured_displacement():
    """D220: the sidecar states the constant AND the shift it actually caused.

    The displacement is re-measured here from the committed PNG against the
    committed pre-shift baseline, so the number in the sidecar is checked
    against the file rather than believed.
    """
    payload = json.loads(
        (FIGS / f"{FIGURE}.drawn_text.json").read_text(encoding="utf-8"))
    offset = payload["marker_offset"]
    assert offset["px_x"] == figures.BACKPEDAL_POLAR_MARKER_OFFSET_PX_X
    assert offset["px_x"] == pytest.approx(-10.0)
    assert offset["axes_fraction_x"] < 0.0
    assert offset["radial_x"] < 0.0
    assert offset["marker_centre_axes_xy"][0] == pytest.approx(
        0.5 + offset["axes_fraction_x"])
    measured = offset["measured_shift_px"]
    assert measured[0] == pytest.approx(-10.0, abs=1.0), measured
    assert abs(measured[1]) < 1.0, measured
    # the reach the clearance test measures, stated in the sidecar too
    assert offset["left_corner_radial_from_origin"] == pytest.approx(
        0.6127, abs=5e-4)
    again = figures.backpedal_polar_measured_shift(
        FIGS / f"{FIGURE}.png")["measured_shift_px"]
    assert measured == pytest.approx(again, abs=1e-9)
    assert payload["marker"]["colour_mode"] == "original"
    marker = payload["marker"]
    assert marker["colour_mode"] == "original"
    assert marker["rotation_deg"] == figures.BACKPEDAL_POLAR_MARKER_ROTATION_DEG
    assert marker["sha256"] == hashlib.sha256(
        (ROOT / marker["path"]).read_bytes()).hexdigest()
    for key in ("border_red_p99", "red_threshold", "subject_pixels",
                "source_crop_bbox_xyxy", "alpha_peak"):
        assert key in marker, key
