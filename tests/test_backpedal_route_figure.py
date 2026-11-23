"""Figure 20 — "Route and heading through the backpedal drill".

Conor, 2026-11-23 14:10: "Ok is there a plot we could do that maybe show the
route of backpedal 45 and an arrow showing where they were looking for the
turn?" This file pins what the answer may and may not do.

What is tested here, and why:

* the reconstruction: the series drawn in the figure is rebuilt the way stage 02
  builds it, and its heading change reproduces the committed ``turn_angle`` for
  every drawn turn — the figure draws the measurement, not raw x/y;
* the population: one row per valid turn of the drill, and the manifest's counts
  recomputed from ``turns.parquet`` and the Combine tracking file rather than
  trusted from the figure code;
* the two heading columns are internally consistent with the angle drawn;
* "no facing": the Combine tracking schema carries no ``o`` column and ``dir``
  equals the travel heading — the panel's honesty line is pinned to the data;
* the drawn text: the title, three panel headings, one axis label pair, the
  count line, the exemplar's labels and the honesty line — no caption, no
  subtitle, no legend, no banned token, and no digits in the title;
* no gate: a two-row input still renders.
"""

from __future__ import annotations

import csv
import copy
import dataclasses
import hashlib
import json
import shutil
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

from bdb27 import common, figures, kinematics, turns  # noqa: E402

TURNS = ROOT / "outputs" / "turns" / "turns.parquet"
TRACKING = ROOT / "data" / "raw" / "combine_tracking.csv"
FIGURE = figures.FIGURE_KEY_BACKPEDAL
FIGS = ROOT / "outputs" / "figures"

REQUIRED_COLUMNS = [
    "provenance", "stamp", "config_sha", "figure", "event_id", "nfl_id",
    "attempt", "drill_name", "frame_start", "frame_centre", "frame_end",
    "n_points", "attempt_frames", "path_length_yd", "net_displacement_yd",
    "entry_heading_deg", "exit_heading_deg", "turn_angle_deg",
    "abs_turn_angle_deg", "apex_speed_yd_s", "entry_speed_yd_s",
    "exit_speed_yd_s", "recon_angle_deg", "reconstruction_agrees",
    "dir_vs_heading_deg", "is_exemplar",
]


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(str(ROOT / "config.yaml"))


@pytest.fixture(scope="module")
def built(cfg):
    return figures.backpedal_route_table(cfg, "sample", TURNS)


@pytest.fixture(scope="module")
def table_and_context(built):
    return built


@pytest.fixture(scope="module")
def drawn(table_and_context, cfg):
    table, context = table_and_context
    fig = figures.plot_backpedal_route(table, context, cfg)
    try:
        return figures.backpedal_route_drawn_text(fig, cfg, "sample")
    finally:
        plt.close(fig)


@pytest.fixture(scope="module")
def valid_turns():
    table = pd.read_parquet(TURNS)
    valid = table["valid"].fillna(False).astype(bool)
    return table[(table["setting"] == "combine")
                 & (table["drill_name"] == figures.BACKPEDAL_DRILL) & valid]


def _cfg_with(cfg, **detector_updates):
    """A copy of ``cfg`` with detector keys changed (the file is untouched)."""
    raw = copy.deepcopy(cfg.raw)
    raw[common.BLOCK]["detector"].update(detector_updates)
    return dataclasses.replace(cfg, raw=raw)


# ------------------------------------------------------------ 1. reconstruction
def test_reconstruction_reproduces_every_drawn_turn(table_and_context):
    table, context = table_and_context
    assert len(table) > 0
    gap = (table["recon_angle_deg"] - table["turn_angle_deg"]).abs()
    assert float(gap.max()) < 1e-3, (
        f"max |reconstruction - turn_angle| = {float(gap.max()):.3e} deg")
    assert bool(table["reconstruction_agrees"].all())
    assert context["n_recon_agree"] == len(table)
    # the figure's own tolerance is the one the flag was computed at
    assert figures.BACKPEDAL_RECON_TOL_DEG == pytest.approx(1e-3)


def test_routes_are_rotated_so_the_entry_heading_points_up(table_and_context):
    """Heading is 0 = +y, clockwise (`kinematics.heading_deg`); the rotation
    must therefore send the entry travel direction to +y, not to +x."""
    table, context = table_and_context
    worst = 0.0
    for record in table.itertuples(index=False):
        item = context["series"][figures._backpedal_key(
            record.event_id, record.nfl_id, record.attempt)]
        rx, ry = figures._backpedal_rotated(
            item, int(record.frame_start), int(record.frame_end))
        dx, dy = np.diff(rx), np.diff(ry)
        moving = np.flatnonzero(np.hypot(dx, dy) > 1e-6)
        assert len(moving), "a drawn route has no movement to orient"
        i = int(moving[0])
        worst = max(worst, abs(float(np.degrees(np.arctan2(dx[i], dy[i])))))
    assert worst < 10.0, (
        f"entry direction is {worst:.1f}° off +y in the rotated frame")


def test_heading_hold_is_read_from_config_not_assumed(cfg):
    """`detector.low_speed_heading_hold` changes the series the detector
    measured; the drawn series must follow the flag, not the default."""
    frame, _inventory = figures.backpedal_frames(cfg, "sample")
    floor = turns.Engine.from_config(cfg, "combine").det.speed_floor
    off = figures.backpedal_attempt_series(
        _cfg_with(cfg, low_speed_heading_hold=False), frame)
    on = figures.backpedal_attempt_series(
        _cfg_with(cfg, low_speed_heading_hold=True), frame)
    assert len(off) == len(on) > 0

    differs, checked = 0, 0
    for key, item in off.items():
        uw_off = item["heading_unwrapped"]
        uw_on = on[key]["heading_unwrapped"]
        if np.allclose(uw_off, uw_on):
            continue
        differs += 1
        assert on[key]["heading_held"] is True
        assert item["heading_held"] is False
        speed = item["speed"]
        heading = item["heading"]
        below = speed < floor
        assert below.any(), "an attempt's series changed but never drops below"
        # above the floor the drawn heading is exactly the raw smoothed one
        above = ~below
        if above.any():
            gap = kinematics.wrap180(uw_on[above] - uw_off[above])
            assert float(np.max(np.abs(gap))) < 1e-9
        # inside a run of sub-floor frames the held heading does not move
        step = kinematics.wrap180(np.diff(uw_on))
        both_below = below[1:] & below[:-1]
        if both_below.any():
            assert float(np.max(np.abs(step[both_below]))) < 1e-9
        # each run of sub-floor frames is seeded with the heading of the frame
        # before the run (or the attempt's own first frame, when it starts low)
        starts = np.flatnonzero(
            below & np.concatenate([[True], ~below[:-1]]))
        assert len(starts), "an attempt's series changed but never drops below"
        for start in starts:
            start = int(start)
            seed = heading[start - 1] if start > 0 else heading[0]
            delta = kinematics.wrap180(float(uw_on[start]) - float(seed))
            assert abs(float(delta)) < 1e-9, (
                f"held heading at {start} is {delta:.3f}° from the heading that "
                "seeds the run")
            checked += 1
    assert differs > 0, (
        "flipping detector.low_speed_heading_hold changed no drawn series — "
        "the flag is being assumed, not read")
    assert checked > 0, "no sub-floor run after an above-floor frame was found"


# --------------------------------------------------------------- 2. population
def test_one_record_per_valid_turn_and_csv_rows_match(valid_turns):
    committed = pd.read_csv(FIGS / f"{FIGURE}.csv")
    assert len(committed) == len(valid_turns), (
        f"the figure drew {len(committed)} turns, the drill has "
        f"{len(valid_turns)} valid turns")
    keys = ["event_id", "nfl_id", "attempt", "frame_start"]
    assert not committed.duplicated(keys).any(), "a turn is drawn twice"
    assert not valid_turns.duplicated(
        ["event_id", "nfl_id", "attempt", "frame_start"]).any()
    def key_set(frame):
        normalised = frame[keys].copy()
        normalised["nfl_id"] = normalised["nfl_id"].astype(int).astype(str)
        normalised["attempt"] = normalised["attempt"].astype(int).astype(str)
        normalised["frame_start"] = (normalised["frame_start"]
                                     .astype(int).astype(str))
        return set(map(tuple, normalised.values))

    assert key_set(committed) == key_set(valid_turns)


# ---------------------------------------------------- 3. headings and the angle
def test_exit_minus_entry_is_the_drawn_angle(table_and_context):
    table, _ = table_and_context
    gap = (table["exit_heading_deg"] - table["entry_heading_deg"]
           - table["turn_angle_deg"]).abs()
    assert float(gap.max()) < 1e-6, float(gap.max())
    # the independent full-precision reconstruction (float64) agrees with the
    # same difference to float32 storage precision, which is what the
    # reconstruction flag already reports
    gap_recon = (table["exit_heading_deg"] - table["entry_heading_deg"]
                 - table["recon_angle_deg"]).abs()
    assert float(gap_recon.max()) < 1e-5


# ------------------------------------------------------------- 4. no facing
def test_dir_equals_travel_heading_and_there_is_no_o_column(table_and_context):
    table, _ = table_and_context
    assert float(table["dir_vs_heading_deg"].median()) < 1.0
    with TRACKING.open(newline="") as handle:
        header = next(csv.reader(handle))
    assert "o" not in header, (
        "the Combine tracking file now carries an `o` column — the figure's "
        "'no facing' statement would be out of date")
    for column in ("x", "y", "s", "a", "dis", "dir"):
        assert column in header


# --------------------------------------------------------------- 5. drawn text
def test_drawn_text_is_the_title_panels_and_annotations(drawn, table_and_context,
                                                        cfg):
    table, context = table_and_context
    strings = drawn["strings"]
    assert figures.BACKPEDAL_TITLE in strings
    assert not any(ch.isdigit() for ch in figures.BACKPEDAL_TITLE)
    for panel in figures.BACKPEDAL_PANEL_TITLES:
        assert panel in strings, panel
    assert figures.BACKPEDAL_XLABEL in strings
    assert figures.BACKPEDAL_YLABEL in strings
    assert figures.BACKPEDAL_ENTRY_LABEL in strings
    assert figures.BACKPEDAL_HONESTY in strings
    count_line = (f"{context['n_turns']:,} turns from "
                  f"{context['n_players']:,} of "
                  f"{context['n_players_tracked']:,} players tracked in this "
                  "drill")
    assert count_line in strings, count_line
    exemplar = context["exemplar"]
    assert "apex" in strings
    assert f"entry {exemplar['entry_speed_yd_s']:.1f} yd/s" in strings
    assert f"exit {exemplar['exit_speed_yd_s']:.1f} yd/s" in strings
    assert (f"turn angle {exemplar['turn_angle_deg']:.1f}\u00b0"
            in strings)
    assert drawn["banned_hits"] == []
    assert drawn["title"] == figures.BACKPEDAL_TITLE
    assert drawn["stamp"] == figures.stamp_text("sample")


def test_no_caption_and_no_legend_beyond_the_stamp(table_and_context, cfg):
    table, context = table_and_context
    fig = figures.plot_backpedal_route(table, context, cfg)
    try:
        # figure-level text is a caption/banner; only the switch may add one
        assert [t.get_text() for t in fig.texts if t.get_text()] == []
        assert len(fig.axes) == 3
        for ax in fig.axes:
            assert ax.get_legend() is None
        # panel 2 is drawn in yards, at true aspect
        assert fig.axes[1].get_aspect() == 1.0
    finally:
        plt.close(fig)


# ------------------------------------------------------------------- 6. no gate
def test_two_rows_still_render(table_and_context, cfg, tmp_path):
    table, _ = table_and_context
    small = table.head(2).copy()
    keys = small[["event_id", "nfl_id", "attempt", "frame_start"]]
    source = pd.read_parquet(TURNS)
    mask = pd.Series(False, index=source.index)
    for record in keys.itertuples(index=False):
        mask |= ((source["event_id"].astype(str) == str(record.event_id))
                 & (source["nfl_id"] == record.nfl_id)
                 & (source["attempt"] == record.attempt)
                 & (source["frame_start"] == record.frame_start))
    two_turns = source[mask]
    assert len(two_turns) == 2
    path = tmp_path / "turns_two.parquet"
    two_turns.to_parquet(path, index=False)
    two_table, two_context = figures.backpedal_route_table(
        cfg, "sample", path)
    assert len(two_table) == 2
    assert two_context["n_turns"] == 2
    fig = figures.plot_backpedal_route(two_table, two_context, cfg)
    try:
        assert len(fig.axes) == 3
        dump = figures.backpedal_route_drawn_text(fig, cfg, "sample")
        assert figures.BACKPEDAL_TITLE in dump["strings"]
        out = figures.render_backpedal_route(fig, cfg, tmp_path / "two.png")
        assert Path(out).stat().st_size > 0
    finally:
        plt.close("all")


def test_turn_rows_that_match_no_tracking_attempt_draw_a_zero(cfg, tmp_path):
    """A turn table and a tracking file that disagree draw an empty population
    with its denominator. They do not stop the notebook."""
    source = pd.read_parquet(TURNS)
    valid = source["valid"].fillna(False).astype(bool)
    part = source[(source["setting"] == "combine")
                  & (source["drill_name"] == figures.BACKPEDAL_DRILL)
                  & valid].head(3).copy()
    assert len(part) == 3
    part["nfl_id"] = 999_999  # no such player: no key can match a series
    path = tmp_path / "turns_orphan.parquet"
    part.to_parquet(path, index=False)

    table, context = figures.backpedal_route_table(cfg, "sample", path)
    assert len(table) == 0
    assert context["n_turns"] == 0
    assert context["n_turns_in_turn_table"] == 3
    assert context["n_unmatched"] == 3
    fig = figures.plot_backpedal_route(table, context, cfg)
    try:
        assert len(fig.axes) == 3
        payload = figures.backpedal_route_manifest_payload(
            table, context, cfg, "sample")
        assert "could be matched" in payload["claim"]
        out = figures.render_backpedal_route(fig, cfg, tmp_path / "zero.png")
        assert Path(out).stat().st_size > 0
    finally:
        plt.close(fig)


# ------------------------------------------------------------------ 7. CSV shape
def test_csv_schema_is_exactly_the_spec_columns():
    committed = pd.read_csv(FIGS / f"{FIGURE}.csv")
    assert list(committed.columns) == REQUIRED_COLUMNS
    for column in ("provenance", "stamp", "config_sha", "figure"):
        assert committed[column].notna().all(), column
    assert committed["figure"].eq(FIGURE).all()
    assert committed["drill_name"].eq(figures.BACKPEDAL_DRILL).all()
    assert int(committed["is_exemplar"].sum()) == 1


# -------------------------------------------------------------- 8. manifest
def test_manifest_entry_and_its_population():
    document = json.loads(
        (FIGS / "figures_manifest.json").read_text(encoding="utf-8"))
    entry = document["figures"][FIGURE]
    assert entry["stage"] == "06_figures"
    assert entry["title"] == figures.BACKPEDAL_TITLE
    assert entry["stamp"] == figures.stamp_text("sample")
    assert entry["drill_name"] == figures.BACKPEDAL_DRILL
    assert entry["png"] == f"outputs/figures/{FIGURE}.png"
    assert entry["csv"] == f"outputs/figures/{FIGURE}.csv"
    assert entry["drawn_text"] == f"outputs/figures/{FIGURE}.drawn_text.json"
    assert entry["caveats"] and any("no `o`" in c or "No facing" in c
                                    for c in entry["caveats"])

    population = entry["population"]
    turns = pd.read_parquet(TURNS)
    valid = turns["valid"].fillna(False).astype(bool)
    part = turns[(turns["setting"] == "combine")
                 & (turns["drill_name"] == figures.BACKPEDAL_DRILL) & valid]
    assert population["n_turns"] == len(part)
    assert population["n_players"] == int(part["nfl_id"].nunique())
    assert population["median_abs_turn_angle"] == pytest.approx(
        float(part["abs_turn_angle"].median()))

    tracking = pd.read_parquet(
        common.ensure_parquet(common.load_config(str(ROOT / "config.yaml")),
                              "sample", "combine_tracking",
                              ["entity_type", "drill_name", "nfl_id",
                               "event_id", "attempt"]))
    drill_rows = tracking[(tracking["entity_type"] == "PLAYER")
                          & (tracking["drill_name"] == figures.BACKPEDAL_DRILL)]
    assert population["n_players_tracked"] == int(drill_rows["nfl_id"].nunique())
    assert population["n_attempts"] == int(
        drill_rows.groupby(["event_id", "nfl_id", "attempt"]).ngroups)


# ------------------------------------------------------------- determinism
def test_two_renders_are_byte_identical(table_and_context, cfg, tmp_path):
    table, context = table_and_context
    digests = []
    for name in ("one.png", "two.png"):
        fig = figures.plot_backpedal_route(table, context, cfg)
        out = figures.render_backpedal_route(fig, cfg, tmp_path / name)
        digests.append(hashlib.sha256(Path(out).read_bytes()).hexdigest())
    assert digests[0] == digests[1]


# ---------------------------------------------------------- committed artefacts
def test_committed_drawn_text_dump_matches_the_spec():
    payload = json.loads(
        (FIGS / f"{FIGURE}.drawn_text.json").read_text(encoding="utf-8"))
    assert payload["figure"] == FIGURE
    assert payload["title"] == figures.BACKPEDAL_TITLE
    assert payload["banned_hits"] == []
    assert payload["strings"].count(figures.BACKPEDAL_TITLE) == 1
    assert figures.BACKPEDAL_HONESTY in payload["strings"]
    assert payload["scan_strings"][0] == figures.BACKPEDAL_TITLE
