"""The focus band is styled from one config block, in every affected figure.

The test renders each figure with a sentinel colour in
``turn_coverage.figures.focus_band.colour`` and walks the drawn artists. If a
figure hard-codes its own red (or forgets the block), the sentinel never reaches
the canvas and the test fails. The geometry (which band) is asserted separately:
the focus edges must be one of the reporting bands each figure already draws.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from matplotlib.colors import to_hex, to_rgba

from bdb27 import common, figures

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "#0f0e0d"          # deliberately not a palette colour
AFFECTED = ["fig_the_hole", "fig_drill_coverage", "fig_before_after",
            "fig_all_positions", "fig_turn_cost", "fig_bars_of_doubt",
            "fig_supply_composition", "fig_band_only",
            "fig_entry_exit_speed"]


@pytest.fixture(scope="module")
def sentinel_cfg():
    cfg = common.load_config(ROOT / "config.yaml")
    cfg.raw[common.BLOCK]["figures"]["focus_band"]["colour"] = SENTINEL
    return cfg


@pytest.fixture(scope="module")
def turns():
    return figures.load_turns(ROOT / "outputs/turns/turns.parquet")


def _hexes(value) -> set[str]:
    """Normalise any matplotlib colour value (scalar or RGBA array)."""
    if value is None:
        return set()
    try:
        arr = to_rgba(value)
    except (TypeError, ValueError):
        return set()
    return {to_hex(arr)}


def _drawn_colours(fig) -> set[str]:
    found: set[str] = set()
    for artist in fig.findobj():
        for name in ("get_color", "get_facecolor", "get_edgecolor"):
            getter = getattr(artist, name, None)
            if getter is None:
                continue
            try:
                found |= _hexes(getter())
            except Exception:
                continue
    return found


def _draw(plot):
    fig = plot.draw()
    assert fig is not None
    return fig


@pytest.fixture(scope="module")
def drawn(sentinel_cfg, turns):
    """Build (not save) every affected figure once, with the sentinel colour."""
    cfg = sentinel_cfg
    figures_out: dict[str, object] = {}

    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    position = str(cfg.get("figures.hole.position_group"))

    table, context = figures.band_table(turns, edges, overflow, position)
    figures_out["fig_the_hole"] = _draw(figures.build_plot(table, context, cfg))

    drill_turns = figures.load_drill_turns(ROOT / "outputs/turns/turns.parquet")
    dt, dc = figures.drill_table(drill_turns, cfg)
    figures_out["fig_drill_coverage"] = _draw(
        figures.plot_drill_coverage(dt, dc, cfg))

    ba_block = "figures.before_after"
    ba_angle = [float(x) for x in cfg.get(f"{ba_block}.angle_edges_deg")]
    ba_speed = [float(x) for x in cfg.get(f"{ba_block}.speed_edges_yd_s")]
    ba_overflow = str(cfg.get(f"{ba_block}.overflow_label"))
    ba_position = str(cfg.get(f"{ba_block}.position_group"))
    supply = figures.load_supply_turns(ROOT / "outputs/turns/turns.parquet")
    bat, bac = figures.supply_grid(supply, ba_angle, ba_overflow,
                                   ba_speed, ba_position)
    bap = figures.projected_block(bat, bac, cfg)
    figures_out["fig_before_after"] = _draw(
        figures.plot_before_after(bat, bac, bap, cfg))

    ap_positions = [str(p) for p in cfg.get("figures.all_positions.positions")]
    ap_edges = [float(x) for x in
                cfg.get("figures.hole.plot_bands.turn_angle_deg")]
    apt, apc = figures.positions_table(turns, ap_edges, overflow, ap_positions)
    figures_out["fig_all_positions"] = _draw(
        figures.plot_all_positions(apt, apc, cfg, turns=turns))

    tct, tcc = figures.turn_cost_table(turns, cfg)
    figures_out["fig_turn_cost"] = figures.plot_turn_cost(tct, tcc, cfg)

    bdt, bdc = figures.bars_of_doubt_table(
        figures.load_bars_of_doubt_turns(ROOT / "outputs/turns/turns.parquet"),
        cfg)
    figures_out["fig_bars_of_doubt"] = figures.plot_bars_of_doubt(bdt, bdc, cfg)

    sct, scc = figures.supply_composition_table(
        figures.load_supply_composition_turns(
            ROOT / "outputs/turns/turns.parquet"), cfg)
    figures_out["fig_supply_composition"] = figures.plot_supply_composition(
        sct, scc, cfg)

    bot, boc = figures.band_only_table(
        figures.load_band_only_turns(ROOT / "outputs/turns/turns.parquet"), cfg)
    figures_out["fig_band_only"] = figures.plot_band_only(bot, boc, cfg)

    eest, eesc = figures.entry_exit_speed_table(
        figures.load_entry_exit_turns(ROOT / "outputs/turns/turns.parquet"),
        cfg)
    figures_out["fig_entry_exit_speed"] = figures.plot_entry_exit_speed(
        eest, eesc, cfg)

    yield figures_out
    for fig in figures_out.values():
        plt.close(fig)


@pytest.mark.parametrize("name", AFFECTED)
def test_focus_edge_uses_config_colour(drawn, name):
    """Every affected figure must draw the config colour somewhere."""
    assert SENTINEL in _drawn_colours(drawn[name]), (
        f"{name} drew no artist in the configured focus_band colour "
        f"{SENTINEL}; it is not reading figures.focus_band")


def test_focus_colour_is_not_hard_coded():
    """The focus red lives in config.yaml only, never in a figure function."""
    source = (ROOT / "src/bdb27/figures.py").read_text(encoding="utf-8")
    assert "#C00000" not in source.upper()


def test_figure_14_focus_fill_is_read_from_config():
    """The bright figure-14 red is configured, never hard-coded."""
    cfg = common.load_config(ROOT / "config.yaml")
    fill = str(cfg.get("figures.supply_composition.focus_fill"))
    assert fill
    source = (ROOT / "src/bdb27/figures.py").read_text(encoding="utf-8")
    assert "#E4002B" not in source.upper(), (
        "the figure-14 focus fill is hard-coded in figures.py; read it from "
        "figures.supply_composition.focus_fill instead")
    assert fill.upper() not in source.upper()


def test_figure_14_focus_fill_differs_from_focus_band_colour():
    """The bright column fill must stay distinct from the dark focus red."""
    cfg = common.load_config(ROOT / "config.yaml")
    fill = str(cfg.get("figures.supply_composition.focus_fill"))
    focus = figures.focus_band(cfg)
    assert fill.upper() != str(focus["colour"]).upper()


def test_focus_edges_are_reporting_bands_everywhere():
    """The focus band must exist, unchanged, on every affected band axis."""
    cfg = common.load_config(ROOT / "config.yaml")
    focus = figures.focus_band(cfg)
    assert (focus["low"], focus["high"]) == (30.0, 45.0)

    axes = {
        "hole": [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")],
        "before_after": [float(x) for x in
                         cfg.get("figures.before_after.angle_edges_deg")],
        "turn_cost": [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")],
        "supply_composition": [float(x) for x in
                               cfg.get("figures.supply_composition.bands.turn_angle_deg")],
        "band_only": [float(x) for x in
                      cfg.get("figures.band_only.angle_band_deg")],
        "entry_exit_speed": [float(x) for x in
                             cfg.get("figures.hole.bands.turn_angle_deg")],
    }
    for name, edges in axes.items():
        assert figures.focus_band_index(edges, focus) == 0, name
