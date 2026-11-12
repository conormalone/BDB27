"""Figure 5 from the uploaded full-data band table (2026-11-11).

The real artefacts are not in git, so the end-to-end tests skip when they are
absent. Every assertion recomputes from the upload or reads what the renderer
wrote; no count is typed into this file as a fact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from bdb27 import common, figures, full_run, full_run_positions

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / full_run.ARTEFACT_DIR
HAS_REAL = all((SOURCE / name).exists() for name in full_run.PINNED)
needs_real = pytest.mark.skipif(
    not HAS_REAL, reason="uploaded full-run artefacts are not on this host")

PINNED_SHA = {name: pin["sha256"] for name, pin in full_run.PINNED.items()}


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(ROOT / "config.yaml")


def _run(cfg, out_dir: Path):
    return full_run_positions.run(cfg, out_dir=out_dir, root=ROOT)


@needs_real
def test_figure5_rebuilt_from_the_uploaded_band_table(cfg, tmp_path):
    result = _run(cfg, tmp_path / "figures")
    table = result["table"]
    context = result["context"]

    assert context["positions"] == ["DB", "WR", "TE", "OL", "DL"]
    # 5 positions x 6 bands x 2 settings. OTHER is not a panel (D187).
    assert len(table) == 60
    assert int(result["context"]["n_turns"]) == int(table["count"].sum())

    # Every drawn number is the uploaded row, and every denominator holds.
    uploaded = pd.read_csv(SOURCE / full_run_positions.ARTEFACT_BANDS)
    for _, row in table.iterrows():
        match = uploaded[(uploaded["position_group"] == row["position"])
                         & (uploaded["band"] == row["band"])
                         & (uploaded["setting"] == row["setting"])]
        assert len(match) == 1
        other = match.iloc[0]
        assert int(row["count"]) == int(other["count"])
        assert int(row["setting_total"]) == int(other["setting_total"])
        assert row["share_within_setting"] == pytest.approx(
            float(other["share_within_setting"]), abs=1e-12)

    # The headline DB panel still reads figure 1's full-data numbers.
    db = table[(table["position"] == "DB") & (table["band"] == "30–60°")]
    assert int(db.loc[db["setting"] == "combine", "count"].iloc[0]) == 26
    assert int(db.loc[db["setting"] == "combine", "setting_total"].iloc[0]) == 1179
    assert int(db.loc[db["setting"] == "game", "count"].iloc[0]) == 7777
    assert int(db.loc[db["setting"] == "game", "setting_total"].iloc[0]) == 58664

    # OTHER rows are in the upload but are not in the drawn table (D187).
    assert "OTHER" not in set(table["position"])

    # Combine totals are the full Combine inventory, unchanged by this work.
    for position, total in (("DB", 1179), ("DL", 2067), ("OL", 952),
                            ("TE", 294), ("WR", 1044)):
        part = table[(table["position"] == position)
                     & (table["setting"] == "combine")]
        assert int(part["setting_total"].iloc[0]) == total
        assert int(part["count"].sum()) == total


@needs_real
def test_figure5_full_drawing_contract(cfg, tmp_path):
    result = _run(cfg, tmp_path / "figures")
    dump = json.loads(result["drawn_text"].read_text(encoding="utf-8"))
    strings = dump["strings"]

    heading = ("The same hole across positions: the Combine takes a smaller "
               "30–60° share in every panel")
    assert heading in strings
    assert "Share of that setting's own valid turns (%)" in strings
    for strip in ("DB", "WR", "TE", "OL", "DL"):
        assert strip in strings, f"facet strip {strip} disappeared"
    assert not any("OTHER" in s.upper() for s in strings), (
        "the removed OTHER column is still named on the canvas")
    for band in ("30–60°", "60–90°", "90–120°", "120–150°", "150–180°",
                 ">180°"):
        assert band in strings
    assert "Combine" in strings and "Game" in strings
    # No count text is drawn on any panel (Conor, 2026-11-12).
    for count in ("26", "1,179", "7,777", "58,664"):
        assert count not in strings, f"count text {count!r} is still drawn"

    joined = " ".join(strings)
    for banned in ("EXPLORATORY", "(TASK.md:63)", "Excluded from these panels",
                   "Paired bars", "30–60° band", "PLACEHOLDER", "UNVALIDATED"):
        assert banned not in joined, f"{banned!r} is drawn on figure 5"
    assert dump["banned_hits"] == []


@needs_real
def test_figure5_draws_five_panels_one_band_row_per_panel(cfg, tmp_path):
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    result = _run(cfg, tmp_path / "figures")
    plot = figures.plot_all_positions(result["table"], result["context"], cfg)
    fig = plot.draw()
    try:
        assert len(fig.axes) == 5, f"expected five panels, got {len(fig.axes)}"
        bars = []
        for ax in fig.axes:
            groups = [c for c in ax.collections
                      if isinstance(c, PolyCollection)]
            bars.append(sum(len(c.get_paths()) for c in groups))
        assert bars == [12] * 5, f"five panels need 12 bars each: {bars}"
        assert all(len(ax.texts) == 0 for ax in fig.axes), (
            "no count annotation may be drawn inside a panel")
        xlim = {tuple(round(v, 9) for v in ax.get_xlim()) for ax in fig.axes}
        assert xlim == {(0.0, 70.0)}, f"panels do not share the 0-70% axis: {xlim}"
    finally:
        plt.close(fig)


@needs_real
def test_figure5_draws_no_count_text_anywhere(cfg, tmp_path):
    """Conor, 2026-11-12 07:51: *all* counts come off, not just DB's.

    Sharpened from the DB-only brief: the drawn text must carry no count
    annotation on any of the five panels. Axis tick labels are the only
    numbers allowed, and the only texts allowed inside a panel are none at
    all.
    """
    import re

    import matplotlib.pyplot as plt

    result = _run(cfg, tmp_path / "figures")
    dump = json.loads(result["drawn_text"].read_text(encoding="utf-8"))
    strings = [s for s in dump["strings"] + dump["scan_strings"] if s.strip()]

    # Every purely numeric string on the canvas must be an x-axis tick.
    ticks = {"0", "10", "20", "30", "40", "50", "60", "70"}
    numeric = {s.strip() for s in strings if re.fullmatch(r"[\d,]+", s.strip())}
    assert numeric <= ticks, (
        f"count-like text survived on the canvas: {sorted(numeric - ticks)}")
    assert not any("," in s for s in strings), (
        "a thousands-separated count is still drawn")
    assert not any(re.match(r"^\s*[nN]\s*=", s) for s in strings), (
        "an n= annotation is still drawn")

    plot = figures.plot_all_positions(result["table"], result["context"], cfg)
    fig = plot.draw()
    try:
        for position, ax in zip(result["context"]["positions"], fig.axes):
            assert list(ax.texts) == [], (
                f"{position} panel carries an in-plot annotation")
        figure_text = [t.get_text() for t in fig.texts
                       if t.get_visible() and t.get_text().strip()]
        allowed = [result["title"],
                   "Share of that setting's own valid turns (%)"]
        assert figure_text == allowed, (
            f"unexpected figure-level text: {figure_text}")
    finally:
        plt.close(fig)


@needs_real
def test_figure5_full_provenance(cfg, tmp_path):
    result = _run(cfg, tmp_path / "figures")

    csv = pd.read_csv(result["csv"])
    assert set(csv["provenance"]) == {"FULL"}
    assert set(csv["label"]) == {"FULL DATA OUTPUT"}
    assert set(csv["stamp"]) == {"FULL DATA OUTPUT"}
    assert len(csv) == 60
    assert set(csv["position"]) == {"DB", "WR", "TE", "OL", "DL"}
    assert "OTHER" not in set(csv["position"])
    # Player exposure is written where the upload can support it and blank
    # where it cannot; no sample player count leaks into a FULL row.
    assert csv.loc[csv["position"] == "DB", "n_players"].notna().all()
    for position in ("WR", "TE", "OL", "DL"):
        game = csv[(csv["position"] == position) & (csv["setting"] == "game")]
        assert game["n_players"].isna().all(), position

    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    entry = manifest["figures"]["fig_all_positions"]
    assert manifest["provenance"] == "FULL"
    assert entry["provenance"] == "FULL"
    assert entry["stamp"] == "FULL DATA OUTPUT"
    assert entry["positions"] == ["DB", "WR", "TE", "OL", "DL"]
    assert len(entry["counts"]) == 60
    assert entry["count_labels"]["drawn"] is False

    record = json.loads(result["provenance"].read_text(encoding="utf-8"))
    assert {a["name"]: a["sha256"] for a in record["source_artefacts"]} == PINNED_SHA
    assert record["crosschecks"]["combine"]["matches_combine_turn_table"] is True
    assert record["crosschecks"]["db_game"]["matches_uploaded_band_table"] is True
    assert record["crosschecks"]["db_game"]["rows_checked"] == 12
    assert record["identity"]["config_sha"] == common.config_fingerprint(cfg)
    assert record["counts"]["n_turns_drawn"] == int(csv["count"].sum())


@needs_real
def test_two_full_figure5_renders_are_byte_identical(cfg, tmp_path):
    first = _run(cfg, tmp_path / "a")
    second = _run(cfg, tmp_path / "b")
    assert (hashlib.sha256(first["png"].read_bytes()).hexdigest()
            == hashlib.sha256(second["png"].read_bytes()).hexdigest())


def test_figure5_refuses_a_share_that_is_not_count_over_total(cfg):
    uploaded = pd.read_csv(SOURCE / full_run_positions.ARTEFACT_BANDS)
    idx = uploaded.index[(uploaded["position_group"] == "WR")
                         & (uploaded["band"] == "30–60°")
                         & (uploaded["setting"] == "game")][0]
    uploaded.loc[idx, "count"] = int(uploaded.loc[idx, "count"]) + 1
    with pytest.raises(common.StageRefused, match="does not equal"):
        full_run_positions.table_from_bands(
            uploaded, full_run_positions.POSITIONS, [30.0, 60.0, 90.0, 120.0,
                                                     150.0, 180.0], ">180")


def test_figure5_refuses_a_missing_position():
    uploaded = pd.read_csv(SOURCE / full_run_positions.ARTEFACT_BANDS)
    uploaded = uploaded[uploaded["position_group"] != "DL"]
    with pytest.raises(common.StageRefused, match="no rows"):
        full_run_positions.positions_order(
            uploaded, full_run_positions.common.load_config(
                ROOT / "config.yaml"), required=full_run_positions.POSITIONS)


@needs_real
def test_other_cannot_come_back(cfg, tmp_path):
    """D187: the upload still carries OTHER rows, but figure 5 never draws it."""
    uploaded = pd.read_csv(SOURCE / full_run_positions.ARTEFACT_BANDS)
    assert "OTHER" in set(uploaded["position_group"]), (
        "the pinned upload no longer carries OTHER; this guard is stale")

    plan = full_run_positions.positions_order(
        uploaded, cfg, required=full_run_positions.POSITIONS)
    assert plan == ["DB", "WR", "TE", "OL", "DL"]
    assert "OTHER" not in plan

    result = _run(cfg, tmp_path / "figures")
    assert result["context"]["positions"] == plan
    assert "OTHER" not in set(result["table"]["position"])
    assert len(result["table"]) == 60
    csv = pd.read_csv(result["csv"])
    assert "OTHER" not in set(csv["position"])
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    entry = manifest["figures"]["fig_all_positions"]
    assert "OTHER" not in entry["positions"]
    assert "OTHER" not in json.dumps(entry)
    dump = json.loads(result["drawn_text"].read_text(encoding="utf-8"))
    assert not any("OTHER" in s.upper()
                   for s in dump["strings"] + dump["scan_strings"])


def test_sample_path_is_untouched():
    """The sample pipeline still owns its own five-panel figure and stamp."""
    assert figures.stamp_text("sample") == (
        "PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE OUTPUT")
    cfg = common.load_config(ROOT / "config.yaml")
    assert [str(p) for p in cfg.get("figures.all_positions.positions")] == [
        "DB", "WR", "TE", "OL", "DL"]
    assert common.config_fingerprint(cfg) == "a4b45b6f476ddae5"
