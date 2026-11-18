"""Figure 19 — "What survives each step": the population ladder's contract.

Conor, 2026-11-18 12:12 GMT: "No that's perfect, cleaned up the plot titles as
well". The chart is approved; this file pins what it may and may not do.

What is tested here, and why:

* the six approved numbers (7,777 / 6,315 / 3,009 / 2,601 / 1,731 / 213) and
  every piece of arithmetic between them — recomputed from the two committed
  files, never trusted from the figure code;
* that the two sources stay distinct: steps 1-4 are the committed funnel (and
  are verified here), step 5 is the received paste (and is not);
* the drawn-text dump: the title, one axis label, the five step labels, the
  five ``survives of entered`` pairs and the count axis' tick labels — and
  nothing else, no caption, no subtitle, no legend, no annotation block;
* a guard that the 213 cannot become a different number without a visible code
  change: the received bytes are pinned by sha256 in the drawing module.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import pytest

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bdb27 import common, figures  # noqa: E402

FUNNEL = ROOT / figures.POP_SHRINK_FUNNEL
RECEIVED = ROOT / figures.POP_SHRINK_GEOMETRY
LOCAL_GEOMETRY = ROOT / figures.POP_SHRINK_LOCAL_GEOMETRY
FIGURE = figures.FIGURE_KEY_POPULATION_SHRINK
FIGS = ROOT / "outputs" / "figures"

APPROVED = [7777, 6315, 3009, 2601, 1731, 213]
APPROVED_ENTERED = [7777, 6315, 3009, 2601, 1731]
APPROVED_SURVIVES = [6315, 3009, 2601, 1731, 213]
APPROVED_LOST = [1462, 3306, 408, 870, 1518]


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(str(ROOT / "config.yaml"))


@pytest.fixture(scope="module")
def ladder():
    return figures.population_shrink_ladder(ROOT)


@pytest.fixture(scope="module")
def figure_texts_dump(ladder, cfg):
    table, context = ladder
    fig = figures.plot_population_shrink(table, context, cfg)
    try:
        return figures.population_shrink_drawn_text(fig, cfg)
    finally:
        plt.close(fig)


# ------------------------------------------------------------------ the ladder
def test_the_six_approved_numbers(ladder):
    table, context = ladder
    assert list(table["entered"]) == APPROVED_ENTERED
    assert list(table["survives"]) == APPROVED_SURVIVES
    assert list(table["lost"]) == APPROVED_LOST
    # the six approved numbers, in the request's order
    assert APPROVED_ENTERED + [APPROVED_SURVIVES[-1]] == [
        7777, 6315, 3009, 2601, 1731, 213]
    assert context["band_turns"] == 7777
    assert context["final"] == 213


def test_every_step_reconciles(ladder):
    table, context = ladder
    for record in table.itertuples(index=False):
        assert int(record.entered) - int(record.survives) == int(record.lost)
    # the chain: what one step leaves is exactly what the next one enters
    for previous, following in zip(table.itertuples(index=False),
                                   table.iloc[1:].itertuples(index=False)):
        assert int(previous.survives) == int(following.entered)
    # the losses, stated as the request states them
    assert APPROVED_LOST == [int(v) for v in table["lost"]]
    # the totals close
    assert 1462 + 6315 == 7777
    assert 3306 + 3009 == 6315
    assert 408 + 2601 == 3009
    assert 870 + 1731 == 2601
    assert 213 + 1518 == 1731


def test_numbers_are_recomputed_from_the_two_files(ladder):
    """Read both artefacts independently of the module and compare."""
    table, context = ladder
    with FUNNEL.open(newline="") as handle:
        funnel = {row["step"]: row for row in csv.DictReader(handle)
                  if row["grouping"] == "band_row"}
    band = int(funnel["band_turns"]["n"])
    no_opposing = int(funnel["no_opposing_player_play_row"]["n"])
    with_runner = int(funnel["turns_with_opposing_route_runner"]["n"])
    separation = int(funnel["turns_with_a_candidate_separation"]["n"])
    one = int(funnel["turns_with_exactly_one_candidate_separation"]["n"])
    assert band == int(table["entered"].iloc[0])
    assert band - no_opposing == int(table["survives"].iloc[0])
    assert with_runner == int(table["survives"].iloc[1])
    assert separation == int(table["survives"].iloc[2])
    assert one == int(table["survives"].iloc[3])

    with RECEIVED.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == one == int(table["entered"].iloc[4])
    within = sum(1 for row in rows if row["within_radius"].strip() == "True")
    assert within == int(table["survives"].iloc[4]) == 213


def test_the_funnel_rows_carry_their_own_agreement(ladder):
    """The artefact's `matches` column is the funnel's own expectation check."""
    funnel = pd.read_csv(FUNNEL)
    band = funnel[funnel["grouping"] == "band_row"].set_index("step")
    for step in ("band_turns", "no_opposing_player_play_row",
                 "turns_with_opposing_route_runner",
                 "turns_with_a_candidate_separation",
                 "turns_with_exactly_one_candidate_separation"):
        assert bool(band.loc[step, "matches"]) is True, step


# ------------------------------------------------------- the provenance split
def test_steps_one_to_four_are_marked_verified_here(ladder):
    table, _ = ladder
    assert list(table["verified_on_this_machine"]) == [True] * 4 + [False]
    assert list(table["provenance"])[:4] == ["FULL"] * 4
    assert table["provenance"].iloc[-1] == "FULL+RECEIVED"
    assert list(table["source_artefact"])[:4] == [figures.POP_SHRINK_FUNNEL] * 4
    assert table["source_artefact"].iloc[-1] == figures.POP_SHRINK_GEOMETRY


def test_the_local_geometry_file_cannot_produce_213():
    """The claim that step 5 is not reproducible here, checked on this disk."""
    assert LOCAL_GEOMETRY.exists()
    local = pd.read_csv(LOCAL_GEOMETRY)
    assert len(local) == 1731
    assert local["within_radius"].isna().all(), (
        "the local copy now carries within_radius values — this machine may "
        "have gained a tracking file, in which case the sidecar's 'not "
        "reproducible here' line is out of date")
    assert (local["geometry_status"].astype(str).str.contains(
        "NOT EVALUABLE").all())


def test_received_paste_is_the_pinned_bytes():
    assert RECEIVED.exists()
    digest = hashlib.sha256(RECEIVED.read_bytes()).hexdigest()
    assert digest == figures.POP_SHRINK_RECEIVED_SHA256
    assert RECEIVED.stat().st_size == figures.POP_SHRINK_RECEIVED_BYTES


def test_received_provenance_says_it_is_not_a_pipeline_output():
    text = (RECEIVED.parent / "PROVENANCE.md").read_text(encoding="utf-8")
    first = text.splitlines()[0]
    assert first.startswith("# Received artefact")
    assert "not produced by any code in this repository" in first
    flat = " ".join(text.split())
    assert figures.POP_SHRINK_RECEIVED_SHA256 in flat
    assert "No manifest came with it" in flat
    assert "1,720" in flat, "the tracked count the file actually carries"


# --------------------------------------------------------------- drawn text
def test_drawn_text_is_the_title_axis_label_and_chart_furniture(figure_texts_dump):
    strings = figure_texts_dump["strings"]
    # exactly: title, one axis label, five step labels, five pairs, five ticks
    assert len(strings) == 17, strings
    assert strings.count(figures.POP_SHRINK_TITLE) == 1
    assert strings.count(figures.POP_SHRINK_XLABEL) == 1
    for state in figures.POP_SHRINK_STATES:
        assert strings.count(state) == 1, state
    for entered, survives in zip(APPROVED_ENTERED, APPROVED_SURVIVES):
        assert f"{survives:,} of {entered:,}" in strings
    assert [s for s in strings if s.replace(",", "").isdigit()] == [
        "0", "2,000", "4,000", "6,000", "8,000"]
    assert figure_texts_dump["banned_hits"] == []
    assert figure_texts_dump["stamp"] == figures.POP_SHRINK_STAMP


def test_the_figure_draws_no_caption_subtitle_or_legend(ladder, cfg):
    table, context = ladder
    fig = figures.plot_population_shrink(table, context, cfg)
    try:
        assert [t.get_text() for t in fig.texts if t.get_text()] == [], (
            "figure-level text is a caption/banner; the approved drawing has "
            "none")
        assert len(fig.axes) == 1
        ax = fig.axes[0]
        assert ax.get_legend() is None
        # one axis label only: the count axis is labelled, the step axis is not
        assert ax.get_xlabel() == figures.POP_SHRINK_XLABEL
        assert ax.get_ylabel() == ""
        # no ticks on the step axis, so no second set of drawn labels
        assert len(ax.get_yticks()) == 0
        # and the heading is a single string, not a title plus a subtitle
        assert ax.get_title() == ""
    finally:
        plt.close(fig)


def test_drawn_text_dump_is_identical_on_a_second_build(figure_texts_dump,
                                                        ladder, cfg):
    table, context = ladder
    fig = figures.plot_population_shrink(table, context, cfg)
    try:
        again = figures.population_shrink_drawn_text(fig, cfg)
    finally:
        plt.close(fig)
    assert json.dumps(again, sort_keys=True) == json.dumps(
        figure_texts_dump, sort_keys=True)


def test_no_player_names_or_ids_on_the_figure(figure_texts_dump):
    blob = " ".join(figure_texts_dump["strings"])
    assert "nfl_id" not in blob
    for token in figure_texts_dump["strings"]:
        assert not any(len(part) >= 5 and part.isdigit()
                       for part in token.replace(",", "").split()), token


# ------------------------------------------------------------------- drawing
def test_the_png_is_wide_and_two_renders_are_byte_identical(ladder, cfg,
                                                            tmp_path):
    from PIL import Image

    table, context = ladder
    digests = []
    for name in ("a.png", "b.png"):
        fig = figures.plot_population_shrink(table, context, cfg)
        out = figures.render_population_shrink(fig, cfg, tmp_path / name)
        digests.append(hashlib.sha256(Path(out).read_bytes()).hexdigest())
        with Image.open(out) as image:
            assert image.size[0] >= 1500, image.size
    assert digests[0] == digests[1]


def test_bars_share_one_origin_and_one_colour_family(ladder, cfg):
    table, context = ladder
    fig = figures.plot_population_shrink(table, context, cfg)
    try:
        ax = fig.axes[0]
        assert ax.get_xlim()[0] == 0, "the bars must share a left origin"
        bars = [patch for patch in ax.patches]
        # two rectangles per step: the population that entered, and the part
        # that came out of it, drawn on top of each other
        assert len(bars) == 2 * len(table)
        colours = {patch.get_facecolor() for patch in bars}
        assert len(colours) == 2, "one colour family: track and fill only"
        heights = {round(patch.get_height(), 6) for patch in bars}
        assert heights == {figures.POP_SHRINK_BAR_HEIGHT}
        # the filled bar is never longer than the one it sits on
        pairs = list(zip(bars[0::2], bars[1::2]))
        for track, fill in pairs:
            assert fill.get_width() <= track.get_width()
            assert track.get_y() == fill.get_y()
    finally:
        plt.close(fig)


# --------------------------------------------------------- the 213 guard
def _tmp_root(tmp_path, *, mutate=None, drop_rows=0):
    funnel = tmp_path / figures.POP_SHRINK_FUNNEL
    received = tmp_path / figures.POP_SHRINK_GEOMETRY
    funnel.parent.mkdir(parents=True, exist_ok=True)
    received.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FUNNEL, funnel)
    rows = list(csv.DictReader(RECEIVED.open(newline="")))
    if mutate is not None:
        mutate(rows)
    rows = rows[:len(rows) - drop_rows] if drop_rows else rows
    with received.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path


def test_a_changed_paste_refuses_before_the_number_moves(tmp_path, monkeypatch):
    def flip_one(rows):
        for row in rows:
            if row["within_radius"].strip() == "True":
                row["within_radius"] = "False"
                return

    root = _tmp_root(tmp_path, mutate=flip_one)
    with pytest.raises(common.StageRefused) as excinfo:
        figures.population_shrink_ladder(root)
    message = str(excinfo.value)
    assert "sha256" in message
    assert "PROVENANCE.md" in message
    # honesty: the refusal names both the pinned and the found digest
    assert figures.POP_SHRINK_RECEIVED_SHA256 in message


def test_a_re_pinned_paste_must_still_be_the_right_population(tmp_path,
                                                             monkeypatch):
    def drop_one(rows):
        del rows[-1]

    root = _tmp_root(tmp_path, drop_rows=1)
    changed = root / figures.POP_SHRINK_GEOMETRY
    digest = hashlib.sha256(changed.read_bytes()).hexdigest()
    monkeypatch.setattr(figures, "POP_SHRINK_RECEIVED_SHA256", digest)
    with pytest.raises(common.StageRefused) as excinfo:
        figures.population_shrink_ladder(root)
    message = str(excinfo.value)
    assert "1,730" in message and "1,731" in message


def test_the_pin_is_the_committed_file_not_a_typed_number():
    """213 is computed from the file; the only typed number is the sha256."""
    import ast

    tree = ast.parse(Path(figures.__file__).read_text(encoding="utf-8"))
    numeric = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant)
               and isinstance(node.value, int)}
    for value in (213, 1731, 2601, 3009, 6315, 7777):
        assert value not in numeric, (
            f"{value} appears as a numeric constant in figures.py — the "
            "ladder must be read from its artefacts, not typed")
    # the pin itself is a string constant, and it is there
    source = Path(figures.__file__).read_text(encoding="utf-8")
    assert figures.POP_SHRINK_RECEIVED_SHA256 in source


# ------------------------------------------------------- committed artefacts
def test_committed_csv_states_the_split_row_by_row():
    path = FIGS / f"{FIGURE}.csv"
    assert path.exists()
    frame = pd.read_csv(path)
    assert len(frame) == 5
    assert list(frame["entered"]) == APPROVED_ENTERED
    assert list(frame["survives"]) == APPROVED_SURVIVES
    assert list(frame["lost"]) == APPROVED_LOST
    assert list(frame["verified_on_this_machine"])[:4] == [True] * 4
    assert not bool(frame["verified_on_this_machine"].iloc[4])
    assert frame["provenance"].iloc[-1] == "FULL+RECEIVED"
    assert frame["source_field"].iloc[-1] == "within_radius"
    # DB only, no player identifiers, no names
    for column in frame.columns:
        assert "nfl_id" not in column.lower()
        assert "name" not in column.lower()
    assert not frame.astype(str).apply(
        lambda column: column.str.contains(r"\b\d{5,}\b")).any().any()


def test_committed_sidecar_says_step_five_is_not_reproducible_here():
    path = FIGS / f"{FIGURE}.provenance.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["provenance"] == figures.POP_SHRINK_STAMP
    assert payload["reproducible_on_this_machine"]["steps_1_to_4"] is True
    assert payload["reproducible_on_this_machine"]["step_5"] is False
    note = payload["provenance_note"]
    assert figures.POP_SHRINK_GEOMETRY in note
    assert "PROVENANCE.md" in note
    assert "No manifest came with that paste" in note
    assert "cannot regenerate 213" in note
    sources = {item["path"]: item for item in payload["source_artefacts"]}
    assert sources[figures.POP_SHRINK_GEOMETRY]["provenance"] == "RECEIVED"
    assert sources[figures.POP_SHRINK_GEOMETRY]["sha256"] == (
        figures.POP_SHRINK_RECEIVED_SHA256)
    assert sources[figures.POP_SHRINK_FUNNEL]["provenance"] == "FULL"


def test_committed_manifest_entry_carries_the_ladder_and_the_split():
    document = json.loads(
        (FIGS / "figures_manifest.json").read_text(encoding="utf-8"))
    entry = document["figures"][FIGURE]
    assert entry["stamp"] == figures.POP_SHRINK_STAMP
    assert [row["entered"] for row in entry["ladder"]] == APPROVED_ENTERED
    assert [row["survives"] for row in entry["ladder"]] == APPROVED_SURVIVES
    assert [row["verified_on_this_machine"] for row in entry["ladder"]] == (
        [True] * 4 + [False])
    assert entry["reproducible_on_this_machine"]["step_5"] is False
    detail = entry["final_step_detail"]
    assert detail["rows_in_received_paste"] == 1731
    assert detail["within_radius"] == 213
    assert detail["not_evaluable"] == 11
    assert detail["lost_total"] == 1518
    assert detail["beyond_radius"] + detail["not_evaluable"] == 1518
    assert entry["bar_encoding"]["common_left_origin"] is True
    assert entry["bar_encoding"]["second_shade_on_last_step"] is False


def test_committed_drawn_text_dump_is_the_approved_text():
    payload = json.loads(
        (FIGS / f"{FIGURE}.drawn_text.json").read_text(encoding="utf-8"))
    assert payload["figure"] == FIGURE
    assert len(payload["strings"]) == 17
    assert payload["banned_hits"] == []
    assert payload["strings"].count(figures.POP_SHRINK_TITLE) == 1
    assert "213 of 1,731" in payload["strings"]
    assert payload["scan_strings"][0] == figures.POP_SHRINK_TITLE
