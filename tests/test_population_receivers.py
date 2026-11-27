"""Figure 22 — "The receiver ladder": the rebuilt prototype's contract.

Conor, 2026-11-26 22:16, on the 18 Nov prototype: "Rebuild it." The figure is
approved as drawn; this file pins what it may and may not do.

What is tested here, and why:

* the five bar numbers (7,777 / 6,315 / 3,009 / 2,601 / 2,558 -> 413), the four
  bar-4 segments (1,882 / 534 / 137 / 5) and the bar-5 split (213 + 200),
  recomputed from the five committed files, never trusted from the figure code;
* the set algebra that the canvas must NOT assert: bar 4 is not a nested subset
  of bar 3 — 194 of the 2,601 are never identified and 151 separation-less
  turns are identified, so 2,407 are in both;
* that every ``source_artefact`` a row names actually exists on disk, and that
  the two newly committed received pastes are the pinned bytes;
* the drawn text: five state labels, five ``survives of entered`` pairs, four
  segment labels, the one note — and no caption, subtitle or banner;
* determinism, and that figure 19's PNG is byte-identical when the drawing is
  called without ``segments``/``notes`` (the reuse must not have moved it).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bdb27 import common, figures  # noqa: E402

FIGURE = figures.FIGURE_KEY_POPULATION_RECEIVERS
FIGS = ROOT / "outputs" / "figures"
RECEIVED = ROOT / "outputs" / "ambiguity_attribution_received"

#: The approved drawing's numbers (spec §2 and §3). They are asserted here so
#: that a change in the drawing code cannot move them quietly; the figure code
#: derives each one from a file.
APPROVED_ENTERED = [7777, 6315, 3009, 2601, 2558]
APPROVED_SURVIVES = [6315, 3009, 2601, 2558, 413]
APPROVED_LOST = [1462, 3306, 408, 43, 2145]
APPROVED_SEGMENTS = {1: 1882, 2: 534, 3: 137, 4: 5}
APPROVED_WITHIN3_BY_MEN = {2: 148, 3: 51, 4: 1}
ALGEBRA = {
    "entered": 2601,
    "survives": 2558,
    "in_both": 2407,
    "surviving_from_outside_entered": 151,
    "entered_not_surviving": 194,
}


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(str(ROOT / "config.yaml"))


@pytest.fixture(scope="module")
def ladder():
    return figures.population_receivers_ladder(ROOT)


@pytest.fixture(scope="module")
def drawn(ladder, cfg):
    table, context = ladder
    fig = figures.plot_population_receivers(table, context, cfg)
    try:
        return figures.population_receivers_drawn_text(fig, cfg)
    finally:
        plt.close(fig)


def _bars(table: pd.DataFrame) -> pd.DataFrame:
    return table[table["row_kind"] == "bar"].reset_index(drop=True)


def _segments(table: pd.DataFrame) -> pd.DataFrame:
    return table[table["row_kind"] == "segment"].reset_index(drop=True)


# ------------------------------------------------------------------ the ladder
def test_the_five_bar_numbers(ladder):
    table, context = ladder
    bars = _bars(table)
    assert list(bars["entered"]) == APPROVED_ENTERED
    assert list(bars["survives"]) == APPROVED_SURVIVES
    assert list(bars["lost"]) == APPROVED_LOST
    assert list(bars["state"]) == list(figures.POP_RECV_STATES)
    assert context["steps"] == 5
    assert context["band_turns"] == 7777
    assert context["final"] == 413


def test_the_bars_are_a_ladder_that_links(ladder):
    """Each bar enters what the one above it leaves; nothing is typed."""
    table, _ = ladder
    bars = _bars(table)
    for above, below in zip(bars.itertuples(index=False),
                            list(bars.itertuples(index=False))[1:]):
        assert int(above.survives) == int(below.entered)
    for record in bars.itertuples(index=False):
        assert int(record.lost) == int(record.entered) - int(record.survives)


def test_the_four_segments(ladder):
    table, context = ladder
    segments = _segments(table)
    assert len(segments) == 4
    found = {int(row.segment): int(row.survives)
             for row in segments.itertuples(index=False)}
    assert found == APPROVED_SEGMENTS
    # the segments fill bar 4 exactly, and segment 1 is the frozen rule plus the
    # recovered residual (1,731 + 151).
    assert sum(found.values()) == 2558
    assert found[1] == context["n_frozen"] + ALGEBRA[
        "surviving_from_outside_entered"]
    assert context["n_frozen"] == 1731
    assert context["union"] == 676


def test_the_bar_five_split(ladder):
    """413 = the frozen rule's 213 + the union's 200, split 148/51/1."""
    _, context = ladder
    within = context["within3"]
    assert within["single"] == 213
    assert within["union"] == 200
    assert within["single"] + within["union"] == context["final"] == 413
    assert within["by_segment"] == APPROVED_WITHIN3_BY_MEN
    assert sum(within["by_segment"].values()) == within["union"]


def test_the_set_algebra_is_not_a_nested_subset(ladder):
    """2,601 -> 2,558 loses 194 and gains 151; it is not a subtraction."""
    _, context = ladder
    algebra = context["algebra"]
    assert algebra == ALGEBRA
    assert algebra["in_both"] + algebra["entered_not_surviving"] == (
        algebra["entered"])
    assert algebra["in_both"] + algebra["surviving_from_outside_entered"] == (
        algebra["survives"])
    # the "lost" a nested drawing would show is entered - survives; the algebra
    # says that net -43 is 151 gained and 194 lost, not 43 dropped.
    assert algebra["entered"] - algebra["survives"] == 43
    assert algebra["entered_not_surviving"] - (
        algebra["surviving_from_outside_entered"]) == 43


# --------------------------------------------------------------- provenance
def test_every_row_names_an_artefact_that_exists(ladder):
    table, _ = ladder
    separators = figures.POP_RECV_SOURCE_SEP
    for record in table.itertuples(index=False):
        for path in str(record.source_artefact).split(separators):
            assert (ROOT / path).exists(), f"{record.step}: {path} is missing"
        assert str(record.source_field).strip()


def test_the_received_pastes_are_the_pinned_bytes():
    geometry = RECEIVED / "geometry.csv"
    route_a = RECEIVED / "route_a_turns.csv"
    assert figures._pop_recv_sha256(geometry) == (
        figures.POP_RECV_GEOMETRY_SHA256)
    assert figures._pop_recv_sha256(route_a) == figures.POP_RECV_ROUTE_A_SHA256
    assert geometry.stat().st_size == figures.POP_RECV_GEOMETRY_BYTES
    assert route_a.stat().st_size == figures.POP_RECV_ROUTE_A_BYTES
    # CRLF, a Windows write, as PROVENANCE.md says
    assert b"\r\n" in geometry.read_bytes()
    assert b"\r\n" in route_a.read_bytes()


def test_provenance_doc_describes_all_three_files():
    text = (RECEIVED / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "It holds three files" in text
    for digest in ("896a1f5c189ff4c409cf72d8051b85c9dd0c52dc0c09074ceeb32edbea3aaeb8",
                   "d93eab30a790ca7646359202d4d284045515e151aad16b80d4cda6e30b270074",
                   "79ed9a087a59e3a6fc13f3de02c2fd83ea8bcc7b9a31bc59eabf068d8fa4af51"):
        assert digest in text
    assert "07:17" in text and "11:07" in text
    assert "verifies nothing about the run" in text


def _copy_inputs(root: Path) -> Path:
    for relative in (figures.POP_RECV_FUNNEL, figures.POP_RECV_PRECEDENCE,
                     figures.POP_RECV_GEOMETRY, figures.POP_RECV_ROUTE_A,
                     figures.POP_RECV_SINGLE):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return root


def test_a_corrupted_paste_refuses_and_names_the_provenance_doc(tmp_path):
    root = _copy_inputs(tmp_path)
    # sanity: the pristine copy reads
    table, _ = figures.population_receivers_ladder(root)
    assert list(_bars(table)["survives"]) == APPROVED_SURVIVES

    geometry = root / figures.POP_RECV_GEOMETRY
    data = geometry.read_bytes()
    geometry.write_bytes(data.replace(b"14.788255756290013",
                                      b"14.788255756290014", 1))
    with pytest.raises(common.StageRefused) as excinfo:
        figures.population_receivers_ladder(root)
    message = str(excinfo.value)
    assert "PROVENANCE.md" in message
    assert figures.POP_RECV_GEOMETRY_SHA256 in message

    # the same guard protects the Route-A paste
    geometry.write_bytes(data)
    route_a = root / figures.POP_RECV_ROUTE_A
    route_a.write_bytes(route_a.read_bytes().replace(b"target", b"targeT", 1))
    with pytest.raises(common.StageRefused) as excinfo:
        figures.population_receivers_ladder(root)
    assert figures.POP_RECV_ROUTE_A_SHA256 in str(excinfo.value)


def test_a_missing_input_is_named(tmp_path):
    root = _copy_inputs(tmp_path)
    (root / figures.POP_RECV_ROUTE_A).unlink()
    with pytest.raises(common.StageRefused) as excinfo:
        figures.population_receivers_ladder(root)
    assert "route_a_turns.csv" in str(excinfo.value)


# ---------------------------------------------------------------- the drawing
def test_drawn_text_is_the_approved_text(drawn):
    strings = drawn["strings"]
    assert drawn["figure"] == FIGURE
    assert drawn["banned_hits"] == []
    assert drawn["stamp"] == "FULL + RECEIVED"
    assert drawn["label"] == "FULL DATA OUTPUT + RECEIVED STEPS"
    # five state labels
    for state in figures.POP_RECV_STATES:
        assert strings.count(state) == 1, state
    # five survives-of-entered pairs
    pairs = ["6,315 of 7,777", "3,009 of 6,315", "2,601 of 3,009",
             "2,558 of 2,601", "413 of 2,558"]
    for pair in pairs:
        assert strings.count(pair) == 1, pair
    # four segment labels: the prototype labels each sliver with the number of
    # men it holds (1/2/3/4), not with its count — the count is the fill.
    for label in ("1", "2", "3", "4"):
        assert strings.count(label) == 1, label
    assert "1,882" not in strings
    # the one note, wording changed on purpose
    assert strings.count(figures.POP_RECV_NOTE) == 1
    assert "(+151 not measured)" not in strings
    assert drawn["notes"] == [figures.POP_RECV_NOTE]
    # title and the one axis label
    assert strings.count(figures.POP_RECV_TITLE) == 1
    assert strings.count(figures.POP_RECV_XLABEL) == 1
    assert drawn["scan_strings"][0] == figures.POP_RECV_TITLE


def test_the_state_labels_are_the_approved_wording(drawn):
    strings = drawn["strings"]
    assert "A listing opponent who ran a route" in strings
    assert "Number of receivers" in strings
    assert "Nearest opponent within 3 yd" in strings
    # the prototype's "Receiver within 3 yd" is not what the data shows
    assert "Receiver within 3 yd" not in strings


def test_no_figure_level_text_is_drawn(ladder, cfg):
    table, context = ladder
    fig = figures.plot_population_receivers(table, context, cfg)
    try:
        assert [text.get_text() for text in fig.texts if text.get_text()] == []
        assert len([ax for ax in fig.axes if ax.get_title()]) == 0
    finally:
        plt.close(fig)


def test_two_renders_are_byte_identical(ladder, cfg, tmp_path):
    import hashlib

    table, context = ladder
    digests = []
    for name in ("one.png", "two.png"):
        fig = figures.plot_population_receivers(table, context, cfg)
        path = figures.render_population_receivers(fig, cfg, tmp_path / name)
        digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
    assert digests[0] == digests[1], "two renders of figure 22 differ"


def test_figure_19_is_unchanged_by_the_segment_extension(cfg, tmp_path):
    """With no ``segments``/``notes`` the drawing is figure 19's, byte for byte."""
    import hashlib

    table, context = figures.population_shrink_ladder(ROOT)
    fig = figures.plot_population_shrink(table, context, cfg)
    path = figures.render_population_shrink(fig, cfg, tmp_path / "fig19.png")
    found = hashlib.sha256(path.read_bytes()).hexdigest()
    committed = hashlib.sha256(
        (FIGS / "fig_population_shrink.png").read_bytes()).hexdigest()
    assert found == committed, (
        "figure 19's PNG moved; the segments/notes extension must be inert "
        f"when both are None (found {found}, committed {committed})")


# ------------------------------------------------------------- committed files
def test_the_four_committed_files_exist_and_are_not_empty():
    for suffix in ("png", "csv", "drawn_text.json", "provenance.json"):
        path = FIGS / f"{FIGURE}.{suffix}"
        assert path.exists(), f"{FIGURE}.{suffix} is missing"
        assert path.stat().st_size > 0


def test_the_committed_csv_is_flat_and_carries_the_algebra():
    frame = pd.read_csv(FIGS / f"{FIGURE}.csv")
    assert list(frame.columns) == figures.POP_RECV_CSV_COLUMNS
    assert list(frame["row_kind"]) == ["bar"] * 5 + ["segment"] * 4
    bars = frame[frame["row_kind"] == "bar"].reset_index(drop=True)
    assert list(bars["entered"]) == APPROVED_ENTERED
    assert list(bars["survives"]) == APPROVED_SURVIVES
    assert list(bars["provenance"]) == ["FULL", "FULL", "FULL",
                                        "RECEIVED", "RECEIVED"]
    assert list(bars["verified_on_this_machine"]) == [True, True, True,
                                                      False, False]
    fourth = bars.iloc[3]
    assert int(fourth["in_both"]) == 2407
    assert int(fourth["surviving_from_outside_entered"]) == 151
    assert int(fourth["entered_not_surviving"]) == 194
    assert frame["in_both"].isna().sum() == 8  # only bar 4 carries it
    segments = frame[frame["row_kind"] == "segment"].reset_index(drop=True)
    assert {int(row.segment): int(row.survives)
            for row in segments.itertuples(index=False)} == APPROVED_SEGMENTS
    for column in frame.columns:
        assert "nfl_id" not in column.lower()
        assert "name" not in column.lower()
    raw = (FIGS / f"{FIGURE}.csv").read_text()
    assert not re.search(r"\b\d{5,}\b", raw), "an identifier leaked into the CSV"


def test_the_committed_drawn_text_is_the_approved_text():
    payload = json.loads((FIGS / f"{FIGURE}.drawn_text.json").read_text())
    assert payload["figure"] == FIGURE
    assert payload["banned_hits"] == []
    assert payload["strings"].count(figures.POP_RECV_NOTE) == 1
    assert len(payload["strings"]) == len(figures.POP_RECV_STATES) + 5 + 4 + 1 + 7


def test_the_committed_sidecar_says_bars_four_and_five_are_not_reproducible():
    payload = json.loads((FIGS / f"{FIGURE}.provenance.json").read_text())
    assert payload["provenance"] == "FULL + RECEIVED"
    assert payload["reproducible_on_this_machine"]["steps_1_to_3"] is True
    assert payload["reproducible_on_this_machine"]["steps_4_to_5"] is False
    assert payload["set_algebra"] == ALGEBRA
    sources = {item["path"]: item for item in payload["source_artefacts"]}
    assert sources[figures.POP_RECV_FUNNEL]["provenance"] == "FULL"
    assert sources[figures.POP_RECV_GEOMETRY]["sha256"] == (
        figures.POP_RECV_GEOMETRY_SHA256)
    assert sources[figures.POP_RECV_ROUTE_A]["sha256"] == (
        figures.POP_RECV_ROUTE_A_SHA256)
    for item in payload["source_artefacts"]:
        if item["path"] != figures.POP_RECV_FUNNEL:
            assert item["provenance"] == "RECEIVED"


def test_the_committed_manifest_entry_carries_the_ladder_and_the_algebra():
    document = json.loads((FIGS / "figures_manifest.json").read_text())
    entry = document["figures"][FIGURE]
    assert entry["stamp"] == "FULL + RECEIVED"
    bars = [row for row in entry["ladder"] if row["row_kind"] == "bar"]
    assert [row["entered"] for row in bars] == APPROVED_ENTERED
    assert [row["survives"] for row in bars] == APPROVED_SURVIVES
    assert [row["verified_on_this_machine"] for row in bars] == (
        [True, True, True, False, False])
    assert entry["set_algebra"]["in_both"] == 2407
    assert entry["set_algebra"]["entered_not_surviving"] == 194
    assert entry["within_radius_detail"]["frozen_rule"] == 213
    assert entry["within_radius_detail"]["multi_candidate_union"] == 200
    assert entry["within_radius_detail"]["total"] == 413
    assert {int(row["segment"]): row["survives"]
            for row in entry["ladder"]
            if row["row_kind"] == "segment"} == APPROVED_SEGMENTS
    assert "PLACEHOLDER" not in json.dumps(entry)
    for caveat in entry["caveats"]:
        assert caveat.strip()
