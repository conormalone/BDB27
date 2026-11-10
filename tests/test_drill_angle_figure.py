"""Figure 18 contract: one row per turn, row order, band, jitter, refusal.

Figure 18 is the only figure that draws every valid Combine turn at its
measured angle against the drill that produced it. These are unit-level tests:
no stage run. The fixture is a small, fully deterministic turn table with DB
and non-DB turns, angles chosen so the band counts, the per-drill medians and
the row order can all be recomputed by hand.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from matplotlib.colors import to_hex, to_rgba

from bdb27 import common, figures

ROOT = Path(__file__).resolve().parents[1]

#: (drill, position, count, angle start) — medians deliberately out of order in
#: the input so the ascending row sort is exercised rather than assumed.
DRILLS = [
    ("DEEP_DRILL", "WR", 12, 150.0),
    ("SHALLOW_ROUTE", "WR", 8, 31.0),
    ("BOX", "DB", 10, 40.0),
    ("MID_ROUTE", "DB", 6, 90.0),
    ("TINY_DRILL", "DL", 3, 200.0),
]


def _frame(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for drill, position, n, start in DRILLS:
        angle = start + 0.7 * np.arange(n)
        angle = angle + rng.uniform(0.0, 0.2, n)
        for j in range(n):
            rows.append({
                "setting": "combine",
                "drill_name": drill,
                "position_group": position,
                "nfl_id": 1000 + (j % 3) + (0 if position == "DB" else 50),
                "abs_turn_angle": float(angle[j]),
                "valid": True,
            })
    # Rows the figure must exclude: invalid, game, and a non-numeric angle.
    rows.append({"setting": "combine", "drill_name": "BOX",
                 "position_group": "DB", "nfl_id": 1,
                 "abs_turn_angle": 45.0, "valid": False})
    rows.append({"setting": "game", "drill_name": None,
                 "position_group": "DB", "nfl_id": 2,
                 "abs_turn_angle": 60.0, "valid": True})
    rows.append({"setting": "combine", "drill_name": "BOX",
                 "position_group": "DB", "nfl_id": 3,
                 "abs_turn_angle": np.nan, "valid": True})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(ROOT / "config.yaml")


@pytest.fixture(scope="module")
def built(cfg):
    return figures.drill_angle_table(_frame(), cfg)


def test_one_row_per_valid_combine_turn(built):
    table, context = built
    assert len(table) == sum(n for _, _, n, _ in DRILLS)
    assert context["n_turns"] == len(table)
    assert context["n_drills"] == len(DRILLS)
    assert table["setting"].eq("combine").all()


def test_rows_ascend_by_each_drills_own_median(built):
    table, context = built
    medians = [row["median_deg"] for row in context["drills"]]
    assert medians == sorted(medians)
    # every turn carries its own drill's median, and the row index agrees
    for row in context["drills"]:
        part = table[table["drill_name"] == row["drill_name"]]
        assert len(part) == row["turns"]
        assert np.isclose(float(part["abs_turn_angle"].median()),
                          row["median_deg"])
        assert part["row_index"].nunique() == 1
        assert int(part["row_index"].iloc[0]) == row["row_index"]


def test_x_is_the_measured_angle_and_y_reconstructs(built):
    table, context = built
    assert np.array_equal(table["x_draw"].to_numpy(dtype=float),
                          table["abs_turn_angle"].to_numpy(dtype=float))
    rebuilt = (table["row_index"].to_numpy(dtype=float)
               + table["y_offset_rows"].to_numpy(dtype=float))
    assert np.allclose(table["y_draw"].to_numpy(dtype=float), rebuilt,
                       rtol=0.0, atol=1e-12)
    # the offset never moves a dot out of its own drill row
    span = context["jitter_span"]
    assert table["y_offset_rows"].abs().max() <= span / 2.0 + 1e-12
    assert span < 1.0


def test_jitter_is_seeded_and_span_bounded(cfg):
    table_a, ctx_a = figures.drill_angle_table(_frame(), cfg)
    table_b, ctx_b = figures.drill_angle_table(_frame(), cfg)
    assert np.array_equal(table_a["y_offset_rows"].to_numpy(),
                          table_b["y_offset_rows"].to_numpy())
    assert ctx_a["jitter_seed"] == ctx_b["jitter_seed"]

    block = "figures.drill_angle_scatter"
    cfg.raw[common.BLOCK]["figures"]["drill_angle_scatter"]["jitter"][
        "span_rows"] = 0.9
    wide, ctx_wide = figures.drill_angle_table(_frame(), cfg)
    assert ctx_wide["jitter_span"] == 0.9
    assert wide["y_offset_rows"].abs().max() <= 0.45 + 1e-12


def test_band_counts_and_shares_match_the_table(built):
    table, context = built
    focus = context["focus"]
    inside = ((table["abs_turn_angle"] >= focus["low"])
              & (table["abs_turn_angle"] < focus["high"]))
    assert int(table["in_band"].sum()) == int(inside.sum())
    assert context["turns_in_band"] == int(inside.sum())
    for position, n in context["by_position"].items():
        part = table[table["position_group"] == position]
        assert n == len(part)
        assert context["band_by_position"][position] == int(part["in_band"].sum())
        assert np.isclose(context["share_by_position"][position],
                          part["in_band"].mean())
    assert sum(context["by_position"].values()) == len(table)
    assert sum(context["band_by_position"].values()) == context["turns_in_band"]


def test_csv_frame_carries_every_turn_and_its_offset(built):
    table, context = built
    csv = figures.drill_angle_csv_frame(table, context)
    assert len(csv) == len(table) == context["n_turns"]
    for column in ("drill_name", "drill_label", "row_median_deg",
                   "drill_turns", "position_group", "abs_turn_angle",
                   "x_draw", "y_offset_rows", "y_draw", "in_band",
                   "band_low_deg", "band_high_deg", "jitter_span_rows"):
        assert column in csv.columns
    assert "nfl_id" not in csv.columns
    assert np.array_equal(csv["x_draw"].to_numpy(dtype=float),
                          csv["abs_turn_angle"].to_numpy(dtype=float))
    assert csv["row_order"].tolist() == list(range(len(csv)))


def test_manifest_payload_agrees_with_the_table(built, cfg):
    table, context = built
    payload = figures.drill_angle_manifest_payload(table, context, cfg)
    assert payload["figure"] == figures.FIGURE_KEY_DRILL_ANGLE
    assert payload["population"]["turns"] == len(table)
    assert payload["population"]["drills"] == context["n_drills"]
    assert payload["population"]["valid_only"] is True
    assert payload["band"]["turns_inside"] == context["turns_in_band"]
    assert payload["band"]["edges_deg"] == [30.0, 60.0]
    assert payload["band"]["red_marks_data"] is False
    assert payload["drawing"]["x_binned"] is False
    assert payload["drawing"]["x_jitter"] is False
    assert len(payload["rows"]) == context["n_drills"]
    assert "30–60°" in payload["claim"]
    assert any("floor" in caveat for caveat in payload["caveats"])


# ----------------------------------------------------------------- refusals
def test_refuses_when_turns_parquet_is_absent(tmp_path):
    with pytest.raises(common.StageRefused):
        figures.load_drill_angle_turns(tmp_path / "turns.parquet")


def test_refuses_when_a_required_column_is_missing(cfg):
    frame = _frame().drop(columns=["drill_name"])
    with pytest.raises(common.StageRefused):
        figures.drill_angle_table(frame, cfg)


def test_refuses_a_turn_below_the_detector_floor(cfg):
    frame = _frame()
    frame.loc[0, "abs_turn_angle"] = 29.0
    with pytest.raises(common.StageRefused, match="detector floor"):
        figures.drill_angle_table(frame, cfg)


def test_refuses_a_valid_combine_turn_without_a_drill(cfg):
    frame = _frame()
    frame.loc[frame.index[0], "drill_name"] = np.nan
    with pytest.raises(common.StageRefused, match="drill_name"):
        figures.drill_angle_table(frame, cfg)


def test_refuses_a_jitter_span_that_would_cross_rows(cfg):
    cfg.raw[common.BLOCK]["figures"]["drill_angle_scatter"]["jitter"][
        "span_rows"] = 1.0
    with pytest.raises(common.StageRefused, match="jitter"):
        figures.drill_angle_table(_frame(), cfg)


# ----------------------------------------------------------------- rendering
def test_two_renders_are_byte_identical(built, cfg, tmp_path):
    table, context = built
    digests = []
    for i in range(2):
        out = tmp_path / f"fig{i}.png"
        fig = figures.plot_drill_angle(table, context, cfg)
        figures.render_drill_angle(fig, cfg, out)
        digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


def _colours(fig) -> set[str]:
    found: set[str] = set()
    for artist in fig.findobj():
        for name in ("get_color", "get_facecolor", "get_edgecolor"):
            getter = getattr(artist, name, None)
            if getter is None:
                continue
            try:
                value = to_rgba(getter())
                found.add(to_hex(value))
            except (TypeError, ValueError):
                continue
    return found


def test_band_style_comes_from_the_config_block(built, tmp_path):
    """A sentinel focus colour must reach the drawn band, so red is config."""
    table, context = built
    sentinel = "#0f0e0d"
    cfg = common.load_config(ROOT / "config.yaml")
    cfg.raw[common.BLOCK]["figures"]["focus_band"]["colour"] = sentinel
    fig = figures.plot_drill_angle(table, context, cfg)
    fig.canvas.draw()
    assert sentinel in _colours(fig), (
        "figure 18 drew no artist in the configured focus_band colour")
    plt.close(fig)


def test_no_data_mark_is_drawn_in_the_focus_colour(built, cfg):
    table, context = built
    focus = to_hex(to_rgba(context["focus"]["colour"]))
    fig = figures.plot_drill_angle(table, context, cfg)
    fig.canvas.draw()
    dots: set[str] = set()
    for coll in fig.findobj(matplotlib.collections.PathCollection):
        for rgba in np.atleast_2d(coll.get_facecolor()):
            dots.add(to_hex(rgba))
    assert dots, "no scatter marks were drawn"
    assert focus not in dots, "a data dot is drawn in the focus red"
    assert to_hex(to_rgba(figures.PALETTE["combine"])) in dots
    assert to_hex(to_rgba(figures.BARS_GREY)) in dots
    plt.close(fig)


def test_figure_draws_every_dot(built, cfg):
    table, context = built
    fig = figures.plot_drill_angle(table, context, cfg)
    fig.canvas.draw()
    drawn = sum(len(coll.get_offsets())
                for coll in fig.findobj(matplotlib.collections.PathCollection))
    assert drawn == len(table)
    plt.close(fig)


# ------------------------------------------------------- committed artefact
REPO_CSV = ROOT / "outputs" / "figures" / f"{figures.FIGURE_KEY_DRILL_ANGLE}.csv"
REPO_TURNS = ROOT / "outputs" / "turns" / "turns.parquet"


@pytest.mark.skipif(not (REPO_CSV.exists() and REPO_TURNS.exists()),
                    reason="committed figure artefact not present")
def test_committed_csv_holds_every_valid_combine_turn():
    turns = pd.read_parquet(REPO_TURNS)
    valid = turns["valid"].fillna(False).astype(bool)
    expected = int((valid & (turns["setting"] == "combine")
                    & pd.to_numeric(turns["abs_turn_angle"],
                                    errors="coerce").notna()).sum())
    csv = pd.read_csv(REPO_CSV)
    assert len(csv) == expected
    assert np.array_equal(csv["x_draw"].to_numpy(dtype=float),
                          csv["abs_turn_angle"].to_numpy(dtype=float))
    medians = csv.groupby("row_index")["row_median_deg"].first().to_dict()
    assert list(medians) == sorted(medians), "rows are not ascending by median"
