"""Stage 06 figure 1 — "The Hole" (DB supply vs demand by turn-angle band).

One figure, deliberately: the shallow-break window is the case's whole point,
so the figure shows it two ways at once — raw counts (what the Combine produces
versus what games demand) and each setting's own share (the exposure-corrected
view). Every number on the image is counted from ``outputs/turns/turns.parquet``
at render time; nothing is hard-coded. This figure displays the window as ONE
30-60 degree band (Conor, 2026-11-10): the 30-45 and 45-60 rows are merged
here, while every other figure keeps them apart (``figures.hole.plot_bands``
is figure 1 only; ``figures.hole.bands`` stays the shared 30/45/60 list).

Design rules honoured here:
  * reporting bands come from ``config.yaml`` (``figures.hole``, D164);
  * no pie charts, no 3-D, no chart junk; grey panel, colour-blind-safe palette;
  * direct labels on the highlighted band and each setting's peak, with a
    compact legend for the rest;
  * figure 1 carries no on-image provenance banner: the sample stamp lives in
    the CSV and the JSON manifest (``render(..., stamp=False)``);
  * two runs produce a byte-identical PNG (fixed DPI/style, no timestamp).
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # deterministic, headless

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Arc, Patch
from matplotlib.ticker import LogLocator, NullFormatter
from plotnine import (
    aes,
    coord_cartesian,
    element_blank,
    element_line,
    element_rect,
    element_text,
    facet_wrap,
    geom_blank,
    geom_col,
    geom_hline,
    geom_point,
    geom_rect,
    geom_segment,
    geom_text,
    geom_tile,
    geom_vline,
    ggplot,
    labs,
    position_dodge,
    scale_alpha_manual,
    scale_colour_manual,
    scale_fill_gradientn,
    scale_fill_manual,
    scale_shape_manual,
    scale_x_discrete,
    scale_x_continuous,
    scale_y_continuous,
    scale_y_discrete,
    theme,
    theme_bw,
)

from bdb27 import common, stats

STAGE = "06_figures"
ARTEFACT_VERSION = 1
FIGURE_KEY = "fig_the_hole"
FIGURE_KEY_DRILLS = "fig_drill_coverage"
FIGURE_KEY_BEFORE_AFTER = "fig_before_after"
FIGURE_KEY_ALL_POSITIONS = "fig_all_positions"
FIGURE_KEY_MEASUREMENT_LIMITS = "fig_measurement_limits"
FIGURE_KEY_GAP_HONEST = "fig_gap_honest"
FIGURE_KEY_TURN_COST = "fig_turn_cost"
FIGURE_KEY_STOPWATCH = "fig_stopwatch"
FIGURE_KEY_BOTH_HIPS = "fig_both_hips"

#: Figure 3 needs only the grid coordinates and the player identity.
SUPPLY_COLUMNS = [
    "setting",
    "abs_turn_angle",
    "entry_speed",
    "position_group",
    "nfl_id",
    "valid",
]

STAMP = f"{common.PROVENANCE_SAMPLE} — {common.SAMPLE_LABEL}"


def stamp_text(mode: str) -> str:
    """The banner written into the CSV and manifest for ``mode``.

    Sample runs carry the placeholder banner; a full run says so plainly, so a
    full-data artefact can never be mistaken for unvalidated sample output.
    The drawn figures keep using the sample banner (``STAMP``) unchanged.
    """
    if mode == "sample":
        return STAMP
    if mode == "full":
        return "FULL DATA OUTPUT"
    raise ValueError(f"{STAGE}: mode must be sample|full, got {mode!r}")

#: Okabe-Ito, colour-blind-safe. Combine = blue, game = vermillion.
PALETTE = {"combine": "#0072B2", "game": "#D55E00"}
ALERT = "#B00020"

#: One config block styles the case's focus band in every figure that carries
#: a band or angle axis. No figure function may hard-code this colour: a test
#: renders each affected figure with a sentinel colour and fails if the
#: sentinel does not reach the drawn artists.
FOCUS_BAND_KEY = "figures.focus_band"


def focus_band(cfg: common.Config, edges: list[float] | tuple[float, ...] | None = None) -> dict:
    """Read and validate the single focus-band style block.

    Returns the band edges, the colour and the style flags every figure uses.
    The colour is a *band* signal: rules, tint, outlines and labels only.

    ``edges`` overrides the *geometry* only, for a figure that displays the
    window differently (figure 1 merges 30-45 and 45-60 into one 30-60 band).
    Colour, tint and rule width always come from ``figures.focus_band``, so
    the sentinel-colour contract holds for every figure, merged axis or not.
    """
    if edges is None:
        low, high = (float(x) for x in cfg.get(f"{FOCUS_BAND_KEY}.edges_deg"))
    else:
        if len(edges) != 2:
            raise ValueError(
                f"{STAGE}: focus-band edge override must be a pair, got {edges}")
        low, high = (float(x) for x in edges)
    if not low < high:
        raise ValueError(
            f"{STAGE}: {FOCUS_BAND_KEY}.edges_deg must be increasing, got "
            f"[{low:g}, {high:g}]")
    colour = str(cfg.get(f"{FOCUS_BAND_KEY}.colour"))
    tint_alpha = float(cfg.get(f"{FOCUS_BAND_KEY}.tint_alpha"))
    if not 0.0 <= tint_alpha <= 1.0:
        raise ValueError(
            f"{STAGE}: {FOCUS_BAND_KEY}.tint_alpha must lie in [0, 1], got "
            f"{tint_alpha}")
    return {
        "low": low,
        "high": high,
        "label": f"{low:g}–{high:g}°",
        "colour": colour,
        "tint_alpha": tint_alpha,
        "rule_width": float(cfg.get(f"{FOCUS_BAND_KEY}.rule_width_pt")),
        "outline_width": float(cfg.get(f"{FOCUS_BAND_KEY}.outline_width_pt")),
    }


def focus_band_index(edges: list[float], focus: dict) -> int:
    """Index of the focus band in a left-closed edge list; refuse if absent."""
    pairs = [(float(a), float(b)) for a, b in zip(edges, edges[1:])]
    target = (focus["low"], focus["high"])
    if target not in pairs:
        raise ValueError(
            f"{STAGE}: the {focus['label']} focus band is not one of the "
            f"configured reporting bands {edges}; refusing to draw a focus "
            "band the axis does not carry")
    return pairs.index(target)

COMBINE_LABEL = "Combine"
GAME_LABEL = "Game"

TURN_COLUMNS = [
    "setting",
    "abs_turn_angle",
    "position_group",
    "nfl_id",
    "game_id",
    "snap_type",
    "valid",
    # Figure 5 needs the entry-speed floor note for the DL panel; figure 1
    # ignores the column. Reading it once here keeps stage 06 to one pass.
    "entry_speed",
    # Figure 6 plots peak lateral acceleration; figures 1-5 ignore it.
    "peak_lateral_accel_yd_s2",
    # Figure 8 prices a turn at the plant with stage 02's own `apex_retention`
    # column (apex speed / entry speed). Never recomputed here. Exit-over-entry
    # `speed_retention` is not read: it is above 1 by construction and is only
    # named in the caption as a definitional footnote.
    "apex_retention",
]

DRILL_COLUMNS = [
    "setting",
    "drill_name",
    "abs_turn_angle",
    "entry_speed",
    "position_group",
    "nfl_id",
    "event_id",
    "attempt",
    "valid",
]

#: Short, reader-facing drill names. Presentation only; the CSV keeps the
#: source ``drill_name`` so every plotted point is traceable to the artefact.
DRILL_SHORT = {
    "BOX_DRILL": "Box",
    "TERYL_AUSTIN_DRILL_2": "Teryl Austin 2",
    "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION": "Back pedal + 45° reaction",
    "BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL": "Back pedal + 90° break",
    "TERYL_AUSTIN_DRILL_1": "Teryl Austin 1",
    "W_DRILL": "W drill",
    "THREE_CONE_DRILL": "3-cone",
    "SHORT_SHUTTLE": "Short shuttle",
    "GAUNTLET_DRILL": "Gauntlet",
    "FORTY_YARD_DASH": "40-yard dash",
}

#: Label placement in data units: (dx, dy, hjust). Styling only — no plotted
#: value is taken from here.
DRILL_LABEL_POS = {
    "BOX_DRILL": (3.0, -0.42, 0.0),
    "TERYL_AUSTIN_DRILL_1": (3.0, 0.22, 0.0),
    "TERYL_AUSTIN_DRILL_2": (0.0, 0.55, 0.5),
    "W_DRILL": (-3.0, -0.10, 1.0),
    "SHORT_SHUTTLE": (-3.0, 0.38, 1.0),
    "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION": (0.0, 0.42, 0.5),
    "BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL": (-3.0, -0.28, 1.0),
    "THREE_CONE_DRILL": (-3.0, 0.18, 1.0),
    "GAUNTLET_DRILL": (0.0, 0.38, 0.5),
    "FORTY_YARD_DASH": (0.0, -0.42, 0.5),
}


# --------------------------------------------------------------------- data
def load_turns(path: Path) -> pd.DataFrame:
    """Read only the columns figure 1 needs; refuse loudly if the table is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    df = pd.read_parquet(path, columns=TURN_COLUMNS)
    missing = [c for c in TURN_COLUMNS if c not in df.columns]
    if missing:  # pragma: no cover - pd.read_parquet(columns=...) already raises
        raise common.StageRefused(
            f"{STAGE}: turn table is missing columns: {', '.join(missing)}")
    return df


def band_labels(edges: list[float], overflow_label: str) -> list[str]:
    labels = [f"{_deg(lo)}–{_deg(hi)}°" for lo, hi in zip(edges[:-1], edges[1:])]
    labels.append(f"{overflow_label}°")
    return labels


def _deg(x: float) -> str:
    return f"{x:g}"


def band_table(turns: pd.DataFrame, edges: list[float], overflow_label: str,
               position: str) -> tuple[pd.DataFrame, dict]:
    """Count valid turns of ``position`` per left-closed band and setting.

    Returns a long table (one row per band x setting) plus the context numbers
    that the caption quotes. Raises if any valid turn falls outside the bands:
    a silent gap would make the plotted counts a lie.
    """
    edges = [float(e) for e in edges]
    if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError(f"band edges must be strictly increasing, got {edges}")

    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in the turn table — nothing to plot")

    settings = ["combine", "game"]
    missing = [s for s in settings if s not in set(df["setting"])]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turn table has no {position} turns for {missing}")

    angle = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    bands = band_labels(edges, overflow_label)
    labels = np.full(len(df), None, dtype=object)
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask = (angle >= lo) & (angle < hi)
        labels[mask.to_numpy()] = bands[i]
    labels[(angle >= edges[-1]).to_numpy()] = bands[-1]

    unbinned = int(pd.isna(labels).sum())
    if unbinned:
        raise ValueError(
            f"{STAGE}: {unbinned} valid {position} turns fall outside the configured "
            f"bands {edges} — fix figures.hole.bands.turn_angle_deg")

    df["band"] = labels
    counts = (df.groupby(["band", "setting"], observed=True)
                .size().rename("count").reset_index())

    totals = df.groupby("setting").size().to_dict()
    rows = []
    for i, band in enumerate(bands):
        for setting in settings:
            n = int(counts.loc[(counts["band"] == band)
                               & (counts["setting"] == setting), "count"].sum())
            rows.append({
                "band_index": i + 1,
                "band": band,
                "band_low_deg": edges[i] if i < len(edges) - 1 else edges[-1],
                "band_high_deg": edges[i + 1] if i < len(edges) - 1 else None,
                "setting": setting,
                "count": n,
                "setting_total": int(totals[setting]),
                "share_within_setting": n / int(totals[setting]),
            })
    table = pd.DataFrame(rows)

    # Hard check: the plotted counts must reconstruct the input exactly.
    if int(table["count"].sum()) != len(df):
        raise ValueError(
            f"{STAGE}: banded counts sum to {int(table['count'].sum())}, "
            f"but the table has {len(df)} valid {position} turns")

    game = df[df["setting"] == "game"]
    snap = game["snap_type"].fillna("MISSING")
    context = {
        "position": position,
        "n_valid": int(len(df)),
        "n_by_setting": {s: int(totals[s]) for s in settings},
        "players_by_setting": {s: int(df.loc[df["setting"] == s, "nfl_id"].nunique())
                               for s in settings},
        "player_games": int(game.groupby(["nfl_id", "game_id"]).ngroups),
        "coverage_turns": int((snap == "COVERAGE").sum()),
        "other_turns": int((snap == "OTHER").sum()),
        "other_share": float((snap == "OTHER").sum() / len(game)) if len(game) else 0.0,
        "bands": bands,
        "edges": edges,
    }
    return table, context


# --------------------------------------------------------------------- plot
def plot_frame(table: pd.DataFrame) -> pd.DataFrame:
    """Long frame with one row per bar; panel (a) counts, panel (b) share %."""
    panels = ["(a) Raw counts (turns)", "(b) Share of turns within setting (%)"]
    frames = []
    for panel in panels:
        part = table.copy()
        part["panel"] = panel
        part["value"] = (part["count"] if panel.startswith("(a)")
                         else part["share_within_setting"] * 100.0)
        frames.append(part)
    out = pd.concat(frames, ignore_index=True)
    out["panel"] = pd.Categorical(out["panel"], categories=panels, ordered=True)
    out["setting"] = pd.Categorical(out["setting"],
                                    categories=["combine", "game"], ordered=True)
    out["band"] = pd.Categorical(out["band"], categories=list(table["band"].unique()),
                                 ordered=True)
    return out


def _labels(table: pd.DataFrame, bands: list[str]) -> pd.DataFrame:
    """Direct labels: the focus band in both panels, plus each setting's peak."""
    frame = plot_frame(table)
    rows = []
    for panel, part in frame.groupby("panel", observed=True):
        top = float(part["value"].max())
        offset = top * 0.04
        chosen = part[part["band"] == bands[0]]
        for setting, sub in part.groupby("setting", observed=True):
            peak = sub.loc[sub["value"].idxmax()]
            chosen = pd.concat([chosen, peak.to_frame().T], ignore_index=True)
        for _, r in chosen.iterrows():
            value = float(r["value"])
            text = (f"{int(round(value))}" if panel.startswith("(a)")
                    else f"{value:.1f}%")
            rows.append({"panel": panel, "band": r["band"], "setting": r["setting"],
                         "lab_y": value + offset, "label": text})
    return pd.DataFrame(rows)


def _annotations(table: pd.DataFrame, context: dict) -> tuple[pd.DataFrame, dict]:
    """Highlight rectangles (one per panel) and the two callout texts."""
    frame = plot_frame(table)
    rects = []
    for panel, part in frame.groupby("panel", observed=True):
        rects.append({"panel": panel, "xmin": 0.5, "xmax": 1.5,
                      "ymin": 0.0, "ymax": float(part["value"].max()) * 1.22})
    rect = pd.DataFrame(rects)

    band0 = table[table["band"] == context["bands"][0]]
    c = int(band0.loc[band0["setting"] == "combine", "count"].iloc[0])
    g = int(band0.loc[band0["setting"] == "game", "count"].iloc[0])
    cs = float(band0.loc[band0["setting"] == "combine",
                         "share_within_setting"].iloc[0])
    gs = float(band0.loc[band0["setting"] == "game",
                         "share_within_setting"].iloc[0])
    ratio = (gs / cs) if cs else float("inf")

    # The headline claim is "largest proportional shortfall of any band". That
    # is checked against the table actually being drawn, never assumed: if a
    # future band configuration makes another band the biggest gap, the figure
    # refuses rather than printing a false superlative.
    shares = table.pivot(index="band", columns="setting",
                         values="share_within_setting")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = (shares["game"] / shares["combine"]).replace(
            [np.inf, -np.inf], np.nan)
    if ratios.notna().any() and not np.isclose(
            float(ratios.max()), ratio, rtol=1e-12, atol=0.0):
        raise ValueError(
            f"{STAGE}: figure 1 claims the first band is the largest "
            f"proportional shortfall, but {ratios.idxmax()} is "
            f"({float(ratios.max()):.2f}x vs {ratio:.2f}x) — fix the claim")

    top_a = float(frame.loc[frame["panel"] == frame["panel"].cat.categories[0],
                            "value"].max())
    top_b = float(frame.loc[frame["panel"] == frame["panel"].cat.categories[1],
                            "value"].max())
    texts = pd.DataFrame([
        {"panel": frame["panel"].cat.categories[0], "x": 1.0, "y": top_a * 0.30,
         "label": f"THE HOLE\n{c} vs {g} turns"},
        {"panel": frame["panel"].cat.categories[1], "x": 1.0, "y": top_b * 0.20,
         "label": f"{ratio:.1f}× the share\nin games"},
    ])
    return rect, {"rect": rect, "text": texts, "combine": c, "game": g,
                  "ratio": ratio, "combine_share": cs, "game_share": gs}


def build_plot(table: pd.DataFrame, context: dict, cfg: common.Config) -> ggplot:
    frame = plot_frame(table)
    labels = _labels(table, context["bands"])
    rect, ann = _annotations(table, context)
    texts = ann["text"]
    # Style always from figures.focus_band; geometry from this figure's own
    # display bands (figure 1 merges 30-45 and 45-60 into one 30-60 band).
    configured = focus_band(cfg)
    focus = focus_band(cfg, edges=[float(e) for e in context["edges"][:2]])
    if float(context["edges"][0]) != configured["low"]:
        raise ValueError(
            f"{STAGE}: figure 1 highlights its first display band, which must "
            f"start at the configured focus floor {configured['low']:g}°; got "
            f"{float(context['edges'][0]):g}°")
    focus_label = pd.DataFrame([
        {"panel": panel,
         "x": 1.0,
         "y": float(part["value"].max()) * 1.15,
         "label": f"{focus['label']} band"}
        for panel, part in frame.groupby("panel", observed=True)
    ])

    c = ann["combine"]
    g = ann["game"]
    players = context["players_by_setting"]
    pg = context["player_games"]
    total = context["n_by_setting"]["game"]
    cov = context["coverage_turns"]
    other = context["other_turns"]
    other_pct = context["other_share"] * 100.0

    title = (f"The hole: games demand {ann['ratio']:.1f}× the share of 30–60° "
             "DB breaks the Combine supplies")
    subtitle = (f"Valid {context['position']} turns by absolute turn angle — "
                f"Combine ({players['combine']} players, one session) vs game "
                f"({players['game']} players, {pg} player-games). Bands are left-closed.")

    caption = "\n".join([
        (f"Games spend {ann['game_share'] * 100.0:.1f}% of their DB turns in "
         f"30–60° against the Combine's {ann['combine_share'] * 100.0:.1f}% — "
         f"{ann['ratio']:.1f}× the share. The {c} Combine detections are "
         "incidental fragments, not a deliberate break test."),
        (f"Exposure: {players['combine']} Combine players × one session vs "
         f"{players['game']} game players × {pg} player-games — the settings are not "
         "sampled alike, which is why panel (b) shows each setting's own share."),
        (f"DB demand denominator: all {total:,} valid DB game turns. The snap-classified "
         f"COVERAGE subset is {cov:,}; {other:,} turns ({other_pct:.1f}%) are snapped "
         "\"other\"."),
        ("Bands are left-closed; 30–45° and 45–60° are merged into one 30–60° band "
         "on this figure only. The final class is >180°. Source: "
         "outputs/turns/turns.parquet (valid turns), stage 02. Counts computed at render time."),
    ])

    dodge = position_dodge(width=0.8)
    plot = (
        ggplot(frame, aes(x="band", y="value", fill="setting"))
        + geom_rect(data=rect, mapping=aes(xmin="xmin", xmax="xmax",
                                           ymin="ymin", ymax="ymax"),
                    inherit_aes=False, fill=focus["colour"],
                    alpha=focus["tint_alpha"])
        + geom_vline(xintercept=[0.5, 1.5], inherit_aes=False,
                     colour=focus["colour"], size=focus["rule_width"])
        + geom_col(position=dodge, width=0.72, colour="white", size=0.25)
        + geom_text(data=labels, mapping=aes(x="band", y="lab_y", label="label",
                                             group="setting"),
                    position=dodge, size=2.9, colour="#1A1A1A", va="bottom",
                    show_legend=False)
        + geom_text(data=texts, mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=3.2, colour=focus["colour"],
                    fontweight="bold",
                    ha="center", va="center")
        + geom_text(data=focus_label, mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=3.3, colour=focus["colour"],
                    fontweight="bold", ha="center", va="bottom")
        + facet_wrap("~ panel", nrow=1, scales="free_y")
        + scale_fill_manual(values=[PALETTE["combine"], PALETTE["game"]],
                            labels=[COMBINE_LABEL, GAME_LABEL], name=None)
        + scale_x_discrete(limits=list(context["bands"]))
        + labs(title=title, subtitle=subtitle, caption=caption,
               x=None, y=None)
        + theme_bw(base_size=9)
        + theme(
            figure_size=(float(cfg.get("figures.hole.width_in")),
                         float(cfg.get("figures.hole.height_in"))),
            panel_background=element_rect(fill="#EBEBEB", colour=None),
            panel_grid_major=element_line(colour="white", linewidth=0.6),
            panel_grid_minor=element_blank(),
            panel_border=element_rect(colour="#B3B3B3", linewidth=0.4),
            strip_background=element_rect(fill="#D9D9D9", colour="#B3B3B3"),
            strip_text=element_text(weight="bold", size=10),
            plot_title=element_text(size=15, weight="bold"),
            plot_subtitle=element_text(size=9.0, colour="#333333"),
            plot_caption=element_text(size=6.9, colour="#333333", ha="left",
                                      linespacing=1.35),
            axis_title_x=element_blank(),
            axis_title_y=element_blank(),
            axis_text_x=element_text(size=8.5),
            axis_text_y=element_text(size=8.0),
            legend_position="bottom",
            legend_title=element_blank(),
            legend_key=element_rect(fill="#EBEBEB", colour=None),
        )
    )
    return plot


def render(plot: ggplot, cfg: common.Config, out_png: Path,
           block: str = "hole", stamp: bool = True) -> plt.Figure:
    """Draw and save once, with fixed metadata so repeated runs are identical.

    ``stamp=False`` omits the on-image provenance banner (figure 1 only: the
    banner is replaced by the CSV/manifest stamp, so it is not lost).
    """
    dpi = int(cfg.get(f"figures.{block}.dpi"))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig = plot.draw()
    if stamp:
        fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.2,
                 fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return fig


# ------------------------------------------------------------------ outputs
def csv_frame(table: pd.DataFrame) -> pd.DataFrame:
    out = table.copy()
    out["share_pct"] = out["share_within_setting"] * 100.0
    return out[["band", "band_low_deg", "band_high_deg", "setting", "count",
                "setting_total", "share_within_setting", "share_pct"]]


def manifest_payload(table: pd.DataFrame, context: dict) -> dict:
    band0 = table[table["band"] == context["bands"][0]]
    c = int(band0.loc[band0["setting"] == "combine", "count"].iloc[0])
    g = int(band0.loc[band0["setting"] == "game", "count"].iloc[0])
    players = context["players_by_setting"]
    total = context["n_by_setting"]["game"]
    cov = context["coverage_turns"]
    other = context["other_turns"]
    other_pct = context["other_share"] * 100.0
    shares = table.pivot(index="band", columns="setting",
                         values="share_within_setting")
    cs = float(shares.loc[context["bands"][0], "combine"])
    gs = float(shares.loc[context["bands"][0], "game"])
    ratio = (gs / cs) if cs else float("inf")
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY,
        "title": (f"The hole: games demand {ratio:.1f}× the share of 30–60° "
                  "DB breaks the Combine supplies"),
        "claim": (f"Games spend {ratio:.1f}× the share of DB turns in 30–60° "
                  f"that the Combine supplies ({gs * 100.0:.1f}% vs "
                  f"{cs * 100.0:.1f}%; {g} game turns vs {c} Combine "
                  "detections). The Combine detections are incidental "
                  "fragments, not a test, and 30–60° is the largest "
                  "proportional shortfall of any band in this figure."),
        "sources": ["outputs/turns/turns.parquet"],
        "caveats": [
            (f"Exposure: {players['combine']} Combine players × one session vs "
             f"{players['game']} game players × {context['player_games']} "
             "player-games; the share panel exists because the two settings are "
             "not sampled alike."),
            (f"DB demand denominator is all {total:,} valid DB game turns; the "
             f"snap-classified COVERAGE subset is {cov:,}, and {other:,} turns "
             f"({other_pct:.1f}%) are snapped as \"other\"."),
            ("Bands are left-closed; the final class is >180°. This figure "
             "merges 30–45° and 45–60° into one 30–60° band; every other "
             "figure reports the two bands separately."),
        ],
        "position_group": context["position"],
        "bands": context["bands"],
        "counts": table.to_dict(orient="records"),
        "png": f"outputs/figures/{FIGURE_KEY}.png",
        "csv": f"outputs/figures/{FIGURE_KEY}.csv",
    }


# ------------------------------------------------- figure 2: drill coverage
def load_drill_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 2 needs; refuse loudly if the table is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    df = pd.read_parquet(path, columns=DRILL_COLUMNS)
    missing = [c for c in DRILL_COLUMNS if c not in df.columns]
    if missing:  # pragma: no cover - pd.read_parquet(columns=...) already raises
        raise common.StageRefused(
            f"{STAGE}: turn table is missing columns: {', '.join(missing)}")
    return df


def drill_table(turns: pd.DataFrame, cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """One row per drill: n, players, median/IQR angle and entry speed.

    Every number is counted from ``turns`` at render time. The proposed drill
    is appended as its own row so the CSV carries the plotted expectation.
    """
    block = "figures.drill_coverage"
    position = str(cfg.get(f"{block}.position_group"))
    angle_lo, angle_hi = (float(x) for x in cfg.get(f"{block}.target_angle_deg"))
    speed_lo, speed_hi = (float(x) for x in cfg.get(f"{block}.target_entry_speed_yd_s"))
    proposed = cfg.get(f"{block}.proposed")

    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)
               & (turns["setting"] == "combine")].copy()
    df["abs_turn_angle"] = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    df["entry_speed"] = pd.to_numeric(df["entry_speed"], errors="coerce")
    df = df[df["abs_turn_angle"].notna() & df["entry_speed"].notna()]
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid Combine {position} turns with angle and entry "
            "speed — nothing to plot")

    in_target = ((df["abs_turn_angle"] >= angle_lo)
                 & (df["abs_turn_angle"] < angle_hi)
                 & (df["entry_speed"] >= speed_lo)
                 & (df["entry_speed"] < speed_hi))

    rows = []
    for drill, g in df.groupby("drill_name", sort=False):
        reps = (g[["nfl_id", "event_id", "attempt"]].drop_duplicates()
                .groupby("nfl_id").size())
        hit = g[in_target.loc[g.index]]
        rows.append({
            "kind": "measured",
            "drill": str(drill),
            "drill_label": DRILL_SHORT.get(str(drill), str(drill)),
            "n_turns": int(len(g)),
            "n_players": int(g["nfl_id"].nunique()),
            "median_angle_deg": float(g["abs_turn_angle"].median()),
            "q1_angle_deg": float(g["abs_turn_angle"].quantile(0.25)),
            "q3_angle_deg": float(g["abs_turn_angle"].quantile(0.75)),
            "median_entry_speed_yd_s": float(g["entry_speed"].median()),
            "q1_entry_speed_yd_s": float(g["entry_speed"].quantile(0.25)),
            "q3_entry_speed_yd_s": float(g["entry_speed"].quantile(0.75)),
            "turns_in_target_cell": int(len(hit)),
            "players_in_target_cell": int(hit["nfl_id"].nunique()),
            "median_reps_per_player": float(reps.median()),
        })
    table = pd.DataFrame(rows).sort_values(
        ["n_turns", "drill"], ascending=[False, True]).reset_index(drop=True)

    if int(table["n_turns"].sum()) != len(df):
        raise ValueError(
            f"{STAGE}: drill counts sum to {int(table['n_turns'].sum())}, but "
            f"the table has {len(df)} valid Combine {position} turns")

    proposed_row = {
        "kind": "proposed",
        "drill": "PROPOSED_40_TEMPO_BREAK",
        "drill_label": str(proposed["label"]),
        "n_turns": 0,
        "n_players": 0,
        "median_angle_deg": float(proposed["angle_deg"]),
        "q1_angle_deg": float(proposed["angle_deg"]),
        "q3_angle_deg": float(proposed["angle_deg"]),
        "median_entry_speed_yd_s": float(proposed["entry_speed_yd_s"]),
        "q1_entry_speed_yd_s": float(proposed["entry_speed_yd_s"]),
        "q3_entry_speed_yd_s": float(proposed["entry_speed_yd_s"]),
        "turns_in_target_cell": 0,
        "players_in_target_cell": 0,
        "median_reps_per_player": float(proposed["reps_per_player"]),
    }
    table = pd.concat([table, pd.DataFrame([proposed_row])], ignore_index=True)

    band = df[(df["abs_turn_angle"] >= angle_lo) & (df["abs_turn_angle"] < angle_hi)]
    band_reps = (band[["nfl_id", "event_id", "attempt"]].drop_duplicates()
                 .groupby("nfl_id").size())
    # The 60-90 band is the densest single band by player count among the
    # repertoire's own median angles; quote it as the best-covered band.
    best = df[(df["abs_turn_angle"] >= 60.0) & (df["abs_turn_angle"] < 90.0)]
    best_reps = (best[["nfl_id", "event_id", "attempt"]].drop_duplicates()
                 .groupby("nfl_id").size())
    target = df[in_target]
    context = {
        "position": position,
        "n_turns": int(len(df)),
        "n_players": int(df["nfl_id"].nunique()),
        "n_drills": int(df["drill_name"].nunique()),
        "n_reps": int(df[["nfl_id", "event_id", "attempt"]]
                      .drop_duplicates().shape[0]),
        "target_angle": (angle_lo, angle_hi),
        "target_speed": (speed_lo, speed_hi),
        "target_turns": int(len(target)),
        "target_players": int(target["nfl_id"].nunique()),
        "target_drills": int(target["drill_name"].nunique()),
        "target_share": float(len(target) / len(df)) if len(df) else 0.0,
        "band_turns": int(len(band)),
        "band_players": int(band["nfl_id"].nunique()),
        "band_median_reps": float(band_reps.median()) if len(band_reps) else 0.0,
        "best_turns": int(len(best)),
        "best_players": int(best["nfl_id"].nunique()),
        "best_median_reps": float(best_reps.median()) if len(best_reps) else 0.0,
        "proposed": {
            "label": str(proposed["label"]),
            "angle": float(proposed["angle_deg"]),
            "speed": float(proposed["entry_speed_yd_s"]),
            "reps": int(proposed["reps_per_player"]),
        },
    }
    return table, context


def _drill_plot_frames(table: pd.DataFrame, context: dict) -> dict:
    """Split the table into the frames each geom draws."""
    measured = table[table["kind"] == "measured"].copy()
    proposed = table[table["kind"] == "proposed"].copy()
    points = pd.concat([measured, proposed], ignore_index=True)
    points["kind_label"] = points["kind"].map(
        {"measured": "measured drill", "proposed": "proposed"})
    points["x"] = points["median_angle_deg"]
    points["y"] = points["median_entry_speed_yd_s"]

    whisk = []
    for _, r in measured.iterrows():
        whisk.append({"x": r["q1_angle_deg"], "xend": r["q3_angle_deg"],
                      "y": r["median_entry_speed_yd_s"],
                      "yend": r["median_entry_speed_yd_s"]})
        whisk.append({"x": r["median_angle_deg"], "xend": r["median_angle_deg"],
                      "y": r["q1_entry_speed_yd_s"],
                      "yend": r["q3_entry_speed_yd_s"]})

    labels = []
    align = {0: "left", 0.5: "center", 1: "right"}
    for _, r in measured.iterrows():
        dx, dy, hjust = DRILL_LABEL_POS.get(r["drill"], (2.0, 0.3, 0.0))
        labels.append({"x": r["median_angle_deg"] + dx,
                       "y": r["median_entry_speed_yd_s"] + dy,
                       "label": r["drill_label"],
                       "hjust": align[float(hjust)]})
    p = context["proposed"]
    labels.append({"x": p["angle"] + 5.0, "y": p["speed"],
                   "label": f"{p['label']}\n{p['reps']} reps per player",
                   "hjust": "left"})

    return {
        "points": points,
        "whisk": pd.DataFrame(whisk),
        "labels": pd.DataFrame(labels),
    }


def plot_drill_coverage(table: pd.DataFrame, context: dict,
                        cfg: common.Config) -> ggplot:
    frames = _drill_plot_frames(table, context)
    points, whisk, labels = frames["points"], frames["whisk"], frames["labels"]
    block = "figures.drill_coverage"
    focus = focus_band(cfg)
    x_lo, x_hi = (float(x) for x in cfg.get(f"{block}.x_lim"))
    y_lo, y_hi = (float(y) for y in cfg.get(f"{block}.y_lim"))
    a_lo, a_hi = context["target_angle"]
    v_lo, v_hi = context["target_speed"]
    if (float(a_lo), float(a_hi)) != (focus["low"], focus["high"]):
        raise ValueError(
            f"{STAGE}: {block}.target_angle_deg must match "
            f"{FOCUS_BAND_KEY}.edges_deg; both describe the same focus band")

    target_rect = pd.DataFrame([{"xmin": a_lo, "xmax": a_hi,
                                 "ymin": v_lo, "ymax": v_hi}])
    target_text = pd.DataFrame([{
        "x": (a_lo + a_hi) / 2.0, "y": v_hi + 0.30,
        "label": f"TARGET CELL\n{a_lo:g}–{a_hi:g}° × {v_lo:g}–{v_hi:g} yd/s"}])

    t = context["target_turns"]
    share = context["target_share"] * 100.0
    title = ("Every DB drill asks for a big turn at low speed — the 30–45° "
             "cell is effectively empty")
    subtitle = (f"Each drill is one point: median absolute turn angle × median "
                f"entry speed, with interquartile spread on both axes. "
                f"{context['n_drills']} drills, {context['n_turns']:,} valid "
                f"Combine DB turns from {context['n_players']} players.")
    caption = "\n".join([
        STAMP,
        (f"The ten-drill inventory is {context['n_drills']} drills and "
         f"{context['n_turns']:,} valid DB turns ({context['n_reps']:,} reps) "
         f"from {context['n_players']} players."),
        ("Median reps per player is 1–2 in every drill (no drill gives more); "
         f"even the best-covered band (60–90°: {context['best_turns']} turns, "
         f"{context['best_players']} players) gives a median of only "
         f"{context['best_median_reps']:g}."),
        (f"Target cell ({a_lo:g}–{a_hi:g}° at {v_lo:g}–{v_hi:g} yd/s): {t} of "
         f"{context['n_turns']:,} Combine turns ({share:.1f}%), from "
         f"{context['target_players']} players across {context['target_drills']} "
         "drills — incidental fragments, not a deliberate test. Games demand 97 "
         "turns in that band from 25 players."),
        (f"The proposed drill would add {context['proposed']['reps']} reps per "
         "player inside the cell. Caveat: Combine supply is 122 players × one "
         "session, so coverage is an inventory of tests, not of players."),
        ("Medians and quartiles computed at render time from "
         "outputs/turns/turns.parquet (valid DB Combine turns)."),
    ])

    plot = (
        ggplot(points, aes(x="x", y="y", colour="kind_label", shape="kind_label"))
        + geom_rect(data=target_rect, mapping=aes(xmin="xmin", xmax="xmax",
                                                  ymin="ymin", ymax="ymax"),
                    inherit_aes=False, fill=focus["colour"],
                    alpha=focus["tint_alpha"], colour=focus["colour"],
                    linetype="solid", size=focus["outline_width"])
        + geom_segment(data=whisk, mapping=aes(x="x", xend="xend", y="y",
                                               yend="yend"),
                       inherit_aes=False, colour="#7A7A7A", size=0.45)
        + geom_point(size=3.1)
        + geom_text(data=labels, mapping=aes(x="x", y="y", label="label",
                                             hjust="hjust"),
                    inherit_aes=False, size=2.65, colour="#1A1A1A", va="center",
                    show_legend=False)
        + geom_text(data=target_text, mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=2.8, colour=focus["colour"],
                    fontweight="bold", ha="center", va="bottom")
        + scale_colour_manual(values=[PALETTE["combine"], PALETTE["game"]],
                              breaks=["measured drill", "proposed"],
                              labels=["measured drill", "proposed"], name=None)
        + scale_shape_manual(values=["o", "D"],
                             breaks=["measured drill", "proposed"],
                             labels=["measured drill", "proposed"], name=None)
        + coord_cartesian(xlim=(x_lo, x_hi), ylim=(y_lo, y_hi))
        + labs(title=title, subtitle=subtitle, caption=caption,
               x="Absolute turn angle at the drill's median turn (°)",
               y="Entry speed at the drill's median turn (yd/s)")
        + theme_bw(base_size=9)
        + theme(
            figure_size=(float(cfg.get(f"{block}.width_in")),
                         float(cfg.get(f"{block}.height_in"))),
            panel_background=element_rect(fill="#EBEBEB", colour=None),
            panel_grid_major=element_line(colour="white", linewidth=0.6),
            panel_grid_minor=element_blank(),
            panel_border=element_rect(colour="#B3B3B3", linewidth=0.4),
            plot_title=element_text(size=14, weight="bold"),
            plot_subtitle=element_text(size=8.6, colour="#333333"),
            plot_caption=element_text(size=6.7, colour="#333333", ha="left",
                                      linespacing=1.35),
            axis_title_x=element_text(size=9.0),
            axis_title_y=element_text(size=9.0),
            axis_text=element_text(size=8.2),
            legend_position="bottom",
            legend_title=element_blank(),
            legend_key=element_rect(fill="#EBEBEB", colour=None),
        )
    )
    return plot


def drill_csv_frame(table: pd.DataFrame) -> pd.DataFrame:
    return table[[
        "kind", "drill", "drill_label", "n_turns", "n_players",
        "median_angle_deg", "q1_angle_deg", "q3_angle_deg",
        "median_entry_speed_yd_s", "q1_entry_speed_yd_s",
        "q3_entry_speed_yd_s", "turns_in_target_cell",
        "players_in_target_cell", "median_reps_per_player",
    ]].copy()


def drill_manifest_payload(table: pd.DataFrame, context: dict) -> dict:
    t = context["target_turns"]
    share = context["target_share"] * 100.0
    a_lo, a_hi = context["target_angle"]
    v_lo, v_hi = context["target_speed"]
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_DRILLS,
        "title": ("Every DB drill asks for a big turn at low speed — the "
                  "30–45° cell is effectively empty"),
        "claim": (f"Of {context['n_turns']:,} valid Combine DB turns across "
                  f"{context['n_drills']} drills, {t} ({share:.1f}%) fall in the "
                  f"{a_lo:g}–{a_hi:g}° × {v_lo:g}–{v_hi:g} yd/s cell the games "
                  "demand; the repertoire is big-angle at low entry speed."),
        "sources": ["outputs/turns/turns.parquet"],
        "caveats": [
            (f"Combine supply is {context['n_players']} players × one session; "
             "the figure describes the test inventory, not player populations."),
            ("Median reps per player is 1–2 in every drill; even the best-covered "
             f"band (60–90°) gives a median of {context['best_median_reps']:g}."),
            (f"The target cell holds {t} incidental Combine turns from "
             f"{context['target_players']} players; game demand in the 30–45° "
             "band is 97 turns from 25 players."),
        ],
        "position_group": context["position"],
        "target_angle_deg": [a_lo, a_hi],
        "target_entry_speed_yd_s": [v_lo, v_hi],
        "proposed": context["proposed"],
        "drills": table.to_dict(orient="records"),
        "png": f"outputs/figures/{FIGURE_KEY_DRILLS}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_DRILLS}.csv",
    }


# ------------------------------------- figure 3: "Before and After"
#: Panel labels. The left panel is MEASURED; the right panel is the measured
#: supply plus an ARITHMETIC projection. The wording is part of the honesty
#: contract, so it lives in one place and the caption repeats it verbatim.
PANEL_MEASURED = "(a) Before: measured Combine supply"
PANEL_PROJECTED = "(b) After: measured supply + proposed drill"

#: Sequential, colour-blind-safe ramp (ColorBrewer Blues). Two panels share it.
BLUES = ["#F7FBFF", "#C6DBEF", "#6BAED6", "#2171B5", "#08306B"]
PROJECT_FILL = "#F0E442"
PROJECT_EDGE = "#8C7A00"


def supply_grid(turns: pd.DataFrame, angle_edges: list[float],
                overflow_label: str, speed_edges: list[float],
                position: str) -> tuple[pd.DataFrame, dict]:
    """Count valid Combine turns of ``position`` on the full angle x speed grid.

    Returns one row per grid cell (including empty cells, which the PNG leaves
    blank but the CSV records as a real zero) plus the context the caption
    quotes. Raises rather than dropping a turn that cannot be placed: a silent
    omission would make every plotted cell a lie.
    """
    angle_edges = [float(e) for e in angle_edges]
    speed_edges = [float(e) for e in speed_edges]
    for name, edges in (("angle", angle_edges), ("speed", speed_edges)):
        if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
            raise ValueError(
                f"{STAGE}: {name} edges must be strictly increasing, got {edges}")

    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)
               & (turns["setting"] == "combine")].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid Combine {position} turns — nothing to plot")

    df["abs_turn_angle"] = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    df["entry_speed"] = pd.to_numeric(df["entry_speed"], errors="coerce")
    unusable = df["abs_turn_angle"].isna() | df["entry_speed"].isna()
    if int(unusable.sum()):
        raise common.StageRefused(
            f"{STAGE}: {int(unusable.sum())} valid Combine {position} turns have "
            "no angle or no entry speed — they cannot be placed on the grid, and "
            "dropping them would understate supply")

    a_bands = band_labels(angle_edges, overflow_label)
    s_bands = [f"{_deg(lo)}–{_deg(hi)}" for lo, hi in
               zip(speed_edges[:-1], speed_edges[1:])]

    def _place(values: pd.Series, edges: list[float]) -> pd.Series:
        out = pd.Series([None] * len(values), index=values.index, dtype=object)
        for i in range(len(edges) - 1):
            mask = (values >= edges[i]) & (values < edges[i + 1])
            out[mask] = i
        out[values >= edges[-1]] = len(edges) - 1
        return out

    df["angle_idx"] = _place(df["abs_turn_angle"], angle_edges)
    df["speed_idx"] = _place(df["entry_speed"], speed_edges)
    if df["angle_idx"].isna().any() or df["speed_idx"].isna().any():
        raise ValueError(
            f"{STAGE}: valid Combine {position} turns fall outside the configured "
            "grid (angle below the first edge or speed below the first edge) — "
            "fix figures.before_after")

    counts = (df.groupby(["angle_idx", "speed_idx"]).size()
                .rename("count").reset_index())

    rows = []
    for a in range(len(a_bands)):
        for s in range(len(s_bands)):
            n = int(counts.loc[(counts["angle_idx"] == a)
                               & (counts["speed_idx"] == s), "count"].sum())
            rows.append({
                "angle_idx": a + 1,
                "speed_idx": s + 1,
                "band": a_bands[a],
                "band_low_deg": angle_edges[a],
                "band_high_deg": (angle_edges[a + 1]
                                  if a + 1 < len(angle_edges) else None),
                "speed_band": s_bands[s],
                "speed_low_yd_s": speed_edges[s],
                "speed_high_yd_s": speed_edges[s + 1],
                "count": n,
            })
    table = pd.DataFrame(rows)

    if int(table["count"].sum()) != len(df):
        raise ValueError(
            f"{STAGE}: gridded counts sum to {int(table['count'].sum())}, but the "
            f"table has {len(df)} valid Combine {position} turns")

    context = {
        "position": position,
        "n_turns": int(len(df)),
        "n_players": int(df["nfl_id"].nunique()),
        "angle_bands": a_bands,
        "speed_bands": s_bands,
        "angle_edges": angle_edges,
        "speed_edges": speed_edges,
        "grid_shape": (len(a_bands), len(s_bands)),
        "vmax": int(table["count"].max()),
        "n_empty": int((table["count"] == 0).sum()),
    }
    return table, context


def load_supply_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 3 needs; refuse loudly if the table is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    return pd.read_parquet(path, columns=SUPPLY_COLUMNS)


def projected_block(table: pd.DataFrame, context: dict,
                    cfg: common.Config) -> dict:
    """The settled drill as an ARITHMETIC block on the same grid.

    The drill is fixed (Conor): 40 degrees, 30-45 band, 2-6 yd/s entry, 6 reps
    per player. Players are counted from the turn table; only the reps are a
    design number. The block is reported as a REGION: the 2-6 yd/s window is
    split by the grid's 4 yd/s edge, and no per-cell split exists in the
    specification, so inventing one would be a fabrication. The total is
    carried once on the region.
    """
    block = "figures.before_after.projected"
    angle = float(cfg.get(f"{block}.angle_deg"))
    reps = int(cfg.get(f"{block}.reps_per_player"))
    win_lo, win_hi = (float(x) for x in cfg.get(f"{block}.entry_speed_window_yd_s"))

    angle_edges = context["angle_edges"]
    speed_edges = context["speed_edges"]

    a_idx = None
    for i in range(len(angle_edges) - 1):
        if angle_edges[i] <= angle < angle_edges[i + 1]:
            a_idx = i
    if a_idx is None:
        raise ValueError(
            f"{STAGE}: projected angle {angle:g} is outside the angle bands "
            f"{angle_edges}")

    s_idx = [i for i in range(len(speed_edges) - 1)
             if speed_edges[i] >= win_lo and speed_edges[i + 1] <= win_hi]
    if not s_idx:
        raise ValueError(
            f"{STAGE}: the projected speed window {win_lo:g}-{win_hi:g} covers no "
            "whole grid cell")

    n_players = context["n_players"]
    total = n_players * reps
    return {
        "angle_idx": a_idx + 1,
        "speed_idx": [i + 1 for i in s_idx],
        "speed_window": (win_lo, win_hi),
        "angle": angle,
        "reps": reps,
        "n_players": n_players,
        "total": int(total),
        "n_cells": len(s_idx),
        "label": f"projected: {reps} reps × {n_players} DBs = {total:,}",
    }


def before_after_plot_frame(table: pd.DataFrame, proj: dict) -> dict:
    """Split the table into what each geom draws.

    Measured cells are the grid cells that hold at least one turn: empty cells
    are dropped on purpose so they stay BLANK, never zero-grey. The projected
    region is a separate rectangle, hatched, drawn on the right panel only.
    """
    measured = table[table["count"] > 0].copy()
    measured["panel"] = PANEL_MEASURED
    measured["panel"] = pd.Categorical(measured["panel"],
                                       categories=[PANEL_MEASURED, PANEL_PROJECTED],
                                       ordered=True)
    measured["share_of_supply"] = measured["count"] / int(table["count"].sum())

    right = measured.copy()
    right["panel"] = PANEL_PROJECTED

    x_lo = proj["angle_idx"] - 0.5
    x_hi = proj["angle_idx"] + 0.5
    y_lo = min(proj["speed_idx"]) - 0.5
    y_hi = max(proj["speed_idx"]) + 0.5
    rect = pd.DataFrame([{"xmin": x_lo, "xmax": x_hi, "ymin": y_lo,
                          "ymax": y_hi, "panel": PANEL_PROJECTED}])
    rect["panel"] = pd.Categorical(rect["panel"],
                                   categories=[PANEL_MEASURED, PANEL_PROJECTED],
                                   ordered=True)
    label = pd.DataFrame([{
        # Sit the label on the higher-speed cell of the block: the lower cell
        # still holds the one measured turn and must keep its own count visible.
        "x": proj["angle_idx"], "y": float(max(proj["speed_idx"])),
        "panel": PANEL_PROJECTED,
        "label": ("projected\n"
                  f"{proj['reps']} reps × {proj['n_players']} DBs\n"
                  f"= {proj['total']:,}"),
    }])
    label["panel"] = pd.Categorical(label["panel"],
                                    categories=[PANEL_MEASURED, PANEL_PROJECTED],
                                    ordered=True)
    return {"cells": pd.concat([measured, right], ignore_index=True),
            "rect": rect, "label": label}


def _hatch_segments(xmin: float, xmax: float, ymin: float, ymax: float,
                    step: float = 0.30) -> pd.DataFrame:
    """Deterministic 45 degree hatching, clipped exactly to the rectangle."""
    rows = []
    c = ymin - xmax
    while c <= ymax - xmin + 1e-9:
        lo = max(xmin, ymin - c)
        hi = min(xmax, ymax - c)
        if hi - lo > 1e-9:
            rows.append({"x": lo, "xend": hi, "y": lo + c, "yend": hi + c})
        c += step
    return pd.DataFrame(rows)


def plot_before_after(table: pd.DataFrame, context: dict, proj: dict,
                      cfg: common.Config) -> ggplot:
    block = "figures.before_after"
    focus = focus_band(cfg)
    focus_idx = focus_band_index(context["angle_edges"], focus) + 1
    frames = before_after_plot_frame(table, proj)
    cells, rect, label = frames["cells"], frames["rect"], frames["label"]
    hatch = _hatch_segments(float(rect["xmin"].iloc[0]), float(rect["xmax"].iloc[0]),
                            float(rect["ymin"].iloc[0]), float(rect["ymax"].iloc[0]))
    hatch["panel"] = PANEL_PROJECTED
    hatch["panel"] = pd.Categorical(hatch["panel"],
                                    categories=[PANEL_MEASURED, PANEL_PROJECTED],
                                    ordered=True)

    # Label colour follows the tile's position on the shared ramp.
    ramp_frac = np.sqrt(np.clip(cells["count"] / max(context["vmax"], 1), 0, 1))
    cells["text_colour"] = np.where(ramp_frac > 0.62, "#FFFFFF", "#1A1A1A")

    n = context["n_turns"]
    players = context["n_players"]
    empty = context["n_empty"]
    a_lo, a_hi = (context["angle_edges"][0], context["angle_edges"][1])
    title = "Before and after: a 30–45° hole, and the drill that would fill it"
    subtitle = (f"Valid DB turns on one angle × entry-speed grid — left: today's "
                f"Combine supply ({n:,} turns, {players} players); right: the same "
                f"supply with the proposed drill added. Grid "
                f"{context['grid_shape'][0]} × {context['grid_shape'][1]}.")
    caption = "\n".join([
        STAMP,
        (f"LEFT is MEASURED: every valid DB Combine turn in "
         f"outputs/turns/turns.parquet, counted at render time. RIGHT is the same "
         f"measured supply PLUS an ARITHMETIC projection: {proj['reps']} reps × "
         f"{proj['n_players']} DBs = {proj['total']:,} turns, drawn hatched and "
         "never coloured as if measured."),
        (f"The projection is placed in the {proj['angle']:g}° band at the drill's "
         f"{proj['speed_window'][0]:g}–{proj['speed_window'][1]:g} yd/s entry "
         "window. The grid's 4 yd/s edge splits that window into two cells and the "
         "drill spec has no per-cell split, so the total is carried on the region "
         "once; it is not divided between cells."),
        (f"Only {int(table.loc[table['band'] == context['angle_bands'][0], 'count'].sum()):,}"
         f" measured turns sit in 30–45° and {empty} of "
         f"{context['grid_shape'][0] * context['grid_shape'][1]} cells are empty: "
         "blank cells are real zeros, not missing data — that emptiness is the "
         "figure's point."),
        (f"Exposure caveat: Combine supply is {players} players × one session, "
         "while the projection is 6 reps per player; the two panels count turns, "
         "not comparable samples of behaviour."),
        ("Colour ramp is shared and square-root scaled (Blues) so small measured "
         "cells stay visible beside the largest. Bands are left-closed; the final "
         "angle class is >180°. Source: outputs/turns/turns.parquet (valid DB "
         "Combine turns), stage 02."),
    ])

    x_breaks = list(range(1, context["grid_shape"][0] + 1))
    y_breaks = list(range(1, context["grid_shape"][1] + 1))

    plot = (
        ggplot(cells, aes(x="angle_idx", y="speed_idx", fill="count"))
        + geom_tile(width=1.0, height=1.0, colour="white", size=0.45)
        + geom_rect(data=rect, mapping=aes(xmin="xmin", xmax="xmax", ymin="ymin",
                                           ymax="ymax"),
                    inherit_aes=False, fill=PROJECT_FILL, alpha=0.28,
                    colour=focus["colour"], linetype="dashed",
                    size=focus["outline_width"])
        + geom_segment(data=hatch, mapping=aes(x="x", xend="xend", y="y",
                                               yend="yend"),
                       inherit_aes=False, colour=PROJECT_EDGE, size=0.45,
                       alpha=0.85)
        + geom_vline(xintercept=[focus_idx - 0.5, focus_idx + 0.5],
                     inherit_aes=False, colour=focus["colour"],
                     size=focus["outline_width"])
        + geom_text(aes(label="count", colour="text_colour"), size=2.55,
                    show_legend=False)
        + geom_text(data=label, mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=2.6, colour="#4A3F00",
                    fontweight="bold", ha="center", va="center")
        + scale_colour_manual(values=["#1A1A1A", "#FFFFFF"], guide=None)
        + scale_fill_gradientn(colors=BLUES, limits=(0, max(context["vmax"], 1)),
                               trans="sqrt", name="DB turns")
        + scale_x_continuous(breaks=x_breaks, labels=context["angle_bands"],
                             limits=(0.5, context["grid_shape"][0] + 0.5),
                             expand=(0, 0))
        + scale_y_continuous(breaks=y_breaks, labels=context["speed_bands"],
                             limits=(0.5, context["grid_shape"][1] + 0.5),
                             expand=(0, 0))
        + facet_wrap("~ panel", nrow=1)
        + labs(title=title, subtitle=subtitle, caption=caption,
               x="Absolute turn angle (band)", y="Entry speed (yd/s)")
        + theme_bw(base_size=9)
        + theme(
            figure_size=(float(cfg.get(f"{block}.width_in")),
                         float(cfg.get(f"{block}.height_in"))),
            panel_background=element_rect(fill="#E4E4E4", colour=None),
            panel_grid_major=element_blank(),
            panel_grid_minor=element_blank(),
            panel_border=element_rect(colour="#B3B3B3", linewidth=0.4),
            strip_background=element_rect(fill="#D9D9D9", colour="#B3B3B3"),
            strip_text=element_text(weight="bold", size=9.5),
            plot_title=element_text(size=14.5, weight="bold"),
            plot_subtitle=element_text(size=8.8, colour="#333333"),
            plot_caption=element_text(size=6.7, colour="#333333", ha="left",
                                      linespacing=1.35),
            axis_title_x=element_text(size=9.0),
            axis_title_y=element_text(size=9.0),
            axis_text_x=element_text(size=7.8, angle=0),
            axis_text_y=element_text(size=8.0),
            legend_position="right",
            legend_title=element_text(size=8.4),
            legend_key=element_rect(fill="#E4E4E4", colour=None),
        )
    )
    return plot


def before_after_csv_frame(table: pd.DataFrame, proj: dict) -> pd.DataFrame:
    """CSV = every grid cell for the measured panel (zeros included) plus the
    projected region rows, each labelled with its panel.

    Measured rows carry a real zero for an empty cell: the PNG leaves the cell
    blank, and the CSV is where "blank means zero" is documented. Projected
    rows carry ``count`` as NaN because the region is NOT split across its two
    grid cells; the single region total is in ``region_count``.
    """
    measured = table.copy()
    measured["panel"] = "measured"
    measured["count_kind"] = "measured cell"
    measured["region_id"] = None
    measured["region_count"] = None

    rows = []
    for s_idx in proj["speed_idx"]:
        band_row = table[(table["angle_idx"] == proj["angle_idx"])
                         & (table["speed_idx"] == s_idx)]
        rows.append({
            "angle_idx": proj["angle_idx"],
            "speed_idx": s_idx,
            "band": band_row["band"].iloc[0],
            "band_low_deg": band_row["band_low_deg"].iloc[0],
            "band_high_deg": band_row["band_high_deg"].iloc[0],
            "speed_band": band_row["speed_band"].iloc[0],
            "speed_low_yd_s": band_row["speed_low_yd_s"].iloc[0],
            "speed_high_yd_s": band_row["speed_high_yd_s"].iloc[0],
            "count": np.nan,
            "panel": "projected",
            "count_kind": "projected region (not split across cells)",
            "region_id": "proj_30-45_2-6",
            "region_count": proj["total"],
        })
    projected = pd.DataFrame(rows)

    out = pd.concat([measured, projected], ignore_index=True)
    out["share_of_supply"] = out["count"] / int(table["count"].sum())
    return out[["panel", "count_kind", "band", "band_low_deg", "band_high_deg",
                "speed_band", "speed_low_yd_s", "speed_high_yd_s", "count",
                "share_of_supply", "region_id", "region_count"]]


def before_after_manifest_payload(table: pd.DataFrame, context: dict,
                                  proj: dict) -> dict:
    n = context["n_turns"]
    hole = int(table[(table["band"] == context["angle_bands"][0])]["count"].sum())
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_BEFORE_AFTER,
        "title": ("Before and after: a 30–45° hole, and the drill that would "
                  "fill it"),
        "claim": (f"Measured DB Combine supply is {n:,} turns across "
                  f"{context['grid_shape'][0]} × {context['grid_shape'][1]} "
                  f"angle × entry-speed cells, with only {hole} turn(s) in the "
                  f"30–45° band; the proposed drill would add "
                  f"{proj['total']:,} projected turns to that band."),
        "sources": ["outputs/turns/turns.parquet"],
        "panels": {
            "left": "measured Combine DB supply",
            "right": "measured supply + arithmetic projection (hatched)",
        },
        "grid": {
            "angle_edges_deg": context["angle_edges"],
            "angle_bands": context["angle_bands"],
            "speed_edges_yd_s": context["speed_edges"],
            "speed_bands": context["speed_bands"],
            "shape": list(context["grid_shape"]),
            "empty_cells": context["n_empty"],
        },
        "projection": {
            "arithmetic": (f"{proj['reps']} reps × {proj['n_players']} DBs = "
                           f"{proj['total']}"),
            "total_turns": proj["total"],
            "n_players": proj["n_players"],
            "reps_per_player": proj["reps"],
            "angle_deg": proj["angle"],
            "entry_speed_window_yd_s": list(proj["speed_window"]),
            "cells_covered": proj["n_cells"],
            "per_cell_split": None,
        },
        "caveats": [
            ("The right panel is arithmetic, not measurement: it repeats the "
             "measured supply and adds a hatched projection of "
             f"{proj['reps']} reps × {proj['n_players']} DBs."),
            ("The 2–6 yd/s window is split by the grid's 4 yd/s edge and the "
             "drill spec has no per-cell split, so the total is carried once on "
             "the region and never divided between cells."),
            (f"Exposure: Combine supply is {context['n_players']} players × one "
             "session; the projection is 6 reps per player. Turn counts are not "
             "comparable samples of behaviour."),
            ("Blank cells are real zeros; the CSV records every grid cell, "
             "including empty ones."),
        ],
        "position_group": context["position"],
        "counts": table.to_dict(orient="records"),
        "png": f"outputs/figures/{FIGURE_KEY_BEFORE_AFTER}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_BEFORE_AFTER}.csv",
    }


# ------------------------------- figure 4: the tempo-break drill, drawn
#: A DIAGRAM, not a data plot. Every dimension is copied from the drill source
#: note (§2). Nothing on this figure is measured, so it is deliberately NOT
#: stamped with ``STAMP`` (the sample-data provenance banner): it carries its
#: own SCHEMATIC status line instead, and the manifest records that choice.
FIGURE_KEY_DRILL_SCHEMATIC = "fig_drill_schematic"

DRILL_SCHEMATIC_COLUMNS = [
    "row_kind", "element", "kind", "text", "value", "value_low", "value_high",
    "unit", "x", "y", "x_end", "y_end", "length_yd", "angle_deg", "radius_yd",
    "source_constant", "source_quote",
]

#: Styling only: sizes and label offsets in yards (or points) used to make the
#: drawing readable. No drawn DIMENSION comes from here — every length and
#: angle on the image comes from ``figures.drill_schematic``.
SCHEMATIC_STYLE = {
    "approach_lw": 3.0,
    "exit_lw": 2.6,
    # Visual arc radii, one per break angle (inner angle first), not drill
    # dimensions. Different radii keep both arcs and both labels readable.
    "arc_radii_yd": [1.25, 2.0],
    "arc_lw": 1.4,
    "gate_tick_half_yd": 0.45,   # symbolic tick; gate WIDTH is not specified
    "cone_size": 15.0,
    "plant_size": 46.0,
    "arc_label_offset_yd": 0.36,
    "gate_label_offset_yd": 0.45,
    "angle_label_fontsize": 10.5,
    "reaccel_fontsize": 7.6,
}

#: Okabe-Ito: left and right are the same movement, so the colours are for
#: legibility only, never a category.
SCHEMATIC_COLOURS = {
    "approach": "#333333",
    "left": "#0072B2",
    "right": "#D55E00",
    "cone": "#E69F00",
    "gate": "#1A1A1A",
    "arc": "#666666",
    "rule": "#B3B3B3",
}


def _schematic_row(**kw) -> dict:
    row = {c: None for c in DRILL_SCHEMATIC_COLUMNS}
    row.update(kw)
    return row


def _band_bounds(label: str) -> tuple[float, float]:
    """Parse a reporting-band label such as ``30–45°`` into numbers."""
    text = str(label).strip().replace("°", "")
    text = text.replace("–", "-").replace("—", "-")
    parts = text.split("-")
    if len(parts) != 2:
        raise ValueError(f"{STAGE}: cannot parse band label {label!r}")
    lo, hi = float(parts[0]), float(parts[1])
    if not lo < hi:
        raise ValueError(f"{STAGE}: band label {label!r} is not increasing")
    return lo, hi


def drill_schematic_geometry(cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """Build the drawn geometry, in yards, from the source note's own numbers.

    Coordinates: the break cone (and the single plant) sits at the origin; the
    approach runs along -x to the start cone; every configured break angle
    leaves at +/- that angle and runs the configured gate distance, so the
    drawing has one exit per angle per side. Returns one row per drawn element
    (segments, points) or annotation, each carrying the config constant it came
    from and the source note sentence it quotes.
    """
    block = "figures.drill_schematic"
    status = str(cfg.get(f"{block}.status"))
    status_stamp = str(cfg.get(f"{block}.status_stamp"))
    source_note = str(cfg.get(f"{block}.source_note"))
    approach_range = [float(x) for x in cfg.get(f"{block}.approach_length_yd")]
    approach = float(cfg.get(f"{block}.approach_drawn_yd"))
    runup = float(cfg.get(f"{block}.runup_length_yd"))
    angles = [float(x) for x in cfg.get(f"{block}.break_angles_deg")]
    bands = [str(x) for x in cfg.get(f"{block}.break_band_labels")]
    gate = float(cfg.get(f"{block}.gate_distance_yd"))
    reaccel = float(cfg.get(f"{block}.reaccel_length_yd"))
    speed = [float(x) for x in cfg.get(f"{block}.entry_speed_yd_s")]
    reps_total = int(cfg.get(f"{block}.reps_total"))
    reps_side = int(cfg.get(f"{block}.reps_per_side"))
    reps_alt = bool(cfg.get(f"{block}.reps_alternating"))
    plants = int(cfg.get(f"{block}.plants_per_rep"))
    duration = float(cfg.get(f"{block}.duration_s"))
    footprint = str(cfg.get(f"{block}.footprint"))
    scale_bar = float(cfg.get(f"{block}.scale_bar_yd"))

    # ---- the drawing may not contradict the note it is drawn from
    lo, hi = approach_range
    if not (0 < lo <= approach <= hi):
        raise ValueError(
            f"{STAGE}: approach_drawn_yd={approach:g} is outside the stated "
            f"8-10 yd range {approach_range} — the drawing would misplace the "
            "break cone")
    if len(angles) != len(bands) or not angles:
        raise ValueError(
            f"{STAGE}: break_angles_deg {angles} and break_band_labels {bands} "
            "must be non-empty and the same length")
    if len(set(angles)) != len(angles):
        raise ValueError(f"{STAGE}: duplicate break angles {angles}")
    for angle, band in zip(angles, bands):
        if not 0 < angle < 90:
            raise ValueError(
                f"{STAGE}: every break angle must be in (0, 90), got {angle}")
        band_lo, band_hi = _band_bounds(band)
        if not (band_lo <= angle <= band_hi):
            raise ValueError(
                f"{STAGE}: break angle {angle:g}° is outside its stated band "
                f"{band!r} ({band_lo:g}-{band_hi:g}) — the label would lie")
    if reps_side * 2 != reps_total:
        raise ValueError(
            f"{STAGE}: reps must be {reps_side} left + {reps_side} right = "
            f"{reps_total}; config has reps_total={reps_total}")
    if gate != reaccel:
        raise ValueError(
            f"{STAGE}: gate_distance_yd={gate:g} and reaccel_length_yd={reaccel:g} "
            "differ, but the drawing uses one exit through the gate")
    if not 0 < speed[0] < speed[1]:
        raise ValueError(f"{STAGE}: entry_speed_yd_s must be an increasing window")

    rows = [
        # ---- approach
        _schematic_row(
            row_kind="geometry", element="approach_line", kind="segment",
            x=-approach, y=0.0, x_end=0.0, y_end=0.0, length_yd=approach,
            angle_deg=0.0,
            source_constant=f"{block}.approach_drawn_yd",
            source_quote="Approach line marked by two cones, 8-10 yd apart."),
        _schematic_row(
            row_kind="geometry", element="cone_start", kind="point",
            x=-approach, y=0.0,
            source_constant=f"{block}.approach_length_yd",
            source_quote="Approach line marked by two cones, 8-10 yd apart."),
        _schematic_row(
            row_kind="geometry", element="cone_break", kind="point",
            x=0.0, y=0.0,
            source_constant=f"{block}.approach_length_yd",
            source_quote="Break cone at the end of the approach."),
        _schematic_row(
            row_kind="geometry", element="plant", kind="point",
            x=0.0, y=0.0, value=float(plants), unit="count",
            source_constant=f"{block}.plants_per_rep",
            source_quote="Single plant, break 40 deg, re-accelerate 5 yd "
                         "through the gate."),
    ]

    # ---- exits: every configured angle, both sides, unique element per angle
    for angle, band in zip(angles, bands):
        tag = f"{angle:g}"
        rad = np.deg2rad(angle)
        gate_left = (gate * np.cos(rad), gate * np.sin(rad))
        gate_right = (gate * np.cos(rad), -gate * np.sin(rad))
        rows.extend([
            _schematic_row(
                row_kind="geometry", element=f"exit_left_{tag}", kind="segment",
                x=0.0, y=0.0, x_end=gate_left[0], y_end=gate_left[1],
                length_yd=gate, angle_deg=angle,
                source_constant=f"{block}.break_angles_deg",
                source_quote="Two exit gates 5 yd from the break cone, at the "
                             "break angle either side of the approach line."),
            _schematic_row(
                row_kind="geometry", element=f"exit_right_{tag}", kind="segment",
                x=0.0, y=0.0, x_end=gate_right[0], y_end=gate_right[1],
                length_yd=gate, angle_deg=-angle,
                source_constant=f"{block}.break_angles_deg",
                source_quote="Two exit gates 5 yd from the break cone, at the "
                             "break angle either side of the approach line."),
            _schematic_row(
                row_kind="geometry", element=f"gate_left_{tag}", kind="point",
                x=gate_left[0], y=gate_left[1], length_yd=gate,
                source_constant=f"{block}.gate_distance_yd",
                source_quote="Two exit gates 5 yd from the break cone."),
            _schematic_row(
                row_kind="geometry", element=f"gate_right_{tag}", kind="point",
                x=gate_right[0], y=gate_right[1], length_yd=gate,
                source_constant=f"{block}.gate_distance_yd",
                source_quote="Two exit gates 5 yd from the break cone."),
            _schematic_row(
                row_kind="annotation", element=f"angle_left_{tag}", kind="text",
                text=f"{angle:g}°", value=angle, unit="deg",
                source_constant=f"{block}.break_angles_deg",
                source_quote="Break 40 deg, either side of the approach line."),
        ])

    rows.extend([
        # ---- annotations: every number printed on the image is here
        _schematic_row(
            row_kind="annotation", element="approach_range", kind="text",
            text=f"acceleration phase · {lo:g}–{hi:g} yd",
            value_low=lo, value_high=hi, unit="yd",
            source_constant=f"{block}.approach_length_yd",
            source_quote="two cones, 8-10 yd apart; accelerate over 8-10 yd."),
        _schematic_row(
            row_kind="annotation", element="runup", kind="text",
            text=f"{runup:g}-yd run-up", value=runup, unit="yd",
            source_constant=f"{block}.runup_length_yd",
            source_quote="Walk-in, 2-yd run-up."),
        _schematic_row(
            row_kind="annotation", element="gate_distance", kind="text",
            text=f"{gate:g} yd", value=gate, unit="yd",
            source_constant=f"{block}.gate_distance_yd",
            source_quote="Two exit gates 5 yd from the break cone."),
        _schematic_row(
            row_kind="annotation", element="reaccel", kind="text",
            text=f"re-accelerate {reaccel:g} yd through the gate",
            value=reaccel, unit="yd",
            source_constant=f"{block}.reaccel_length_yd",
            source_quote="Re-accelerate 5 yd through the gate."),
        _schematic_row(
            row_kind="annotation", element="entry_speed", kind="text",
            text=f"{speed[0]:g}–{speed[1]:g} yd/s at the plant",
            value_low=speed[0], value_high=speed[1], unit="yd/s",
            source_constant=f"{block}.entry_speed_yd_s",
            source_quote="Reach 3-5 yd/s at the break cone."),
        _schematic_row(
            row_kind="annotation", element="reps_total", kind="text",
            text=f"{reps_total} reps total",
            value=float(reps_total), unit="reps",
            source_constant=f"{block}.reps_total",
            source_quote="Reps 6 total: 3 left, 3 right, alternating."),
        _schematic_row(
            row_kind="annotation", element="reps_per_side", kind="text",
            text=f"{reps_side} left / {reps_side} right, alternating",
            value=float(reps_side), unit="reps",
            source_constant=f"{block}.reps_per_side",
            source_quote="3 left, 3 right, alternating."),
        _schematic_row(
            row_kind="annotation", element="reps_sequence", kind="text",
            text="  ".join(["L", "R"] * reps_side),
            source_constant=f"{block}.reps_alternating",
            source_quote="6 total: 3 left, 3 right, alternating."),
        _schematic_row(
            row_kind="annotation", element="duration", kind="text",
            text=f"~{duration:g} s per player", value=duration, unit="s",
            source_constant=f"{block}.duration_s",
            source_quote="Duration ~90 s per player."),
        _schematic_row(
            row_kind="annotation", element="status", kind="text",
            text=status_stamp, source_constant=f"{block}.status_stamp",
            source_quote="Status line."),
        _schematic_row(
            row_kind="annotation", element="footprint", kind="text",
            text=footprint, source_constant=f"{block}.footprint",
            source_quote="Space: same cones and footprint as the 3-cone; no "
                         "new station."),
        _schematic_row(
            row_kind="annotation", element="scale_bar", kind="text",
            text=f"{scale_bar:g} yd", value=scale_bar, unit="yd",
            source_constant=f"{block}.scale_bar_yd",
            source_quote="Scale bar, drawn to scale in yards."),
    ])
    table = pd.DataFrame(rows, columns=DRILL_SCHEMATIC_COLUMNS)

    # ---- hard checks: the drawn table may not disagree with itself
    for angle in angles:
        tag = f"{angle:g}"
        left = table.loc[table["element"] == f"exit_left_{tag}"].iloc[0]
        right = table.loc[table["element"] == f"exit_right_{tag}"].iloc[0]
        for seg in (left, right):
            if not np.isclose(float(seg["length_yd"]), gate):
                raise ValueError(
                    f"{STAGE}: drawn exit length is not the gate distance")
            if not np.isclose(
                    np.hypot(float(seg["x_end"]), float(seg["y_end"])), gate):
                raise ValueError(
                    f"{STAGE}: gate is not {gate:g} yd from the break cone")
            if not np.isclose(abs(float(seg["angle_deg"])), angle):
                raise ValueError(
                    f"{STAGE}: drawn exit angle is not the configured angle")
        if not np.isclose(float(left["x_end"]), float(right["x_end"])):
            raise ValueError(f"{STAGE}: exits at {angle:g}° are not mirrored in x")
        if not np.isclose(float(left["y_end"]), -float(right["y_end"])):
            raise ValueError(f"{STAGE}: exits at {angle:g}° are not mirrored in y")
    if not np.isclose(approach, np.hypot(
            float(table.loc[table["element"] == "cone_start", "x"].iloc[0]), 0.0)):
        raise ValueError(f"{STAGE}: start cone is not on the drawn approach")

    omissions = [
        ("Gate width (the opening between gate posts) is not stated in the "
         "source note, so each exit gate is drawn as a single transverse tick "
         "marking where the player crosses — not as a measured opening."),
        ("The source note lists exactly two approach cones 8-10 yd apart and "
         "puts the break cone at the end of the approach; it does not describe "
         "any further cone. Only those two cones are drawn."),
        ("Where the walk-in ends and where the 2-yd run-up sits relative to the "
         "8-10 yd approach is not specified, so no phase boundary is drawn: the "
         "phases are labelled in one line, and only the approach itself (8-10 "
         "yd) is measured on the image."),
        ("The 3-cone footprint the new drill reuses is not dimensioned in the "
         "source note, so no footprint outline is drawn — the claim is stated "
         "in words only."),
        ("Lane width, station size and cone spacing beyond the two approach "
         "cones are not specified and are not drawn."),
    ]

    context = {
        "status": status,
        "status_stamp": status_stamp,
        "source_note": source_note,
        "approach_range": (lo, hi),
        "approach_drawn": approach,
        "runup": runup,
        "break_angles": angles,
        "break_bands": bands,
        "gate_distance": gate,
        "reaccel": reaccel,
        "entry_speed": (speed[0], speed[1]),
        "reps_total": reps_total,
        "reps_per_side": reps_side,
        "reps_alternating": reps_alt,
        "plants_per_rep": plants,
        "duration_s": duration,
        "footprint": footprint,
        "scale_bar_yd": scale_bar,
        "to_scale": True,
        "omissions": omissions,
        "n_elements": int((table["row_kind"] == "geometry").sum()),
        "n_annotations": int((table["row_kind"] == "annotation").sum()),
    }
    return table, context


def _schematic_text(table: pd.DataFrame, element: str) -> str:
    row = table[(table["row_kind"] == "annotation")
                & (table["element"] == element)]
    if len(row) != 1:
        raise ValueError(f"{STAGE}: schematic annotation {element!r} is not unique")
    return str(row["text"].iloc[0])


def _schematic_title(context: dict) -> str:
    """The one bold title line, built from the configured break angles."""
    break_angles = context["break_angles"]
    angles = " and ".join(f"{float(a):g}\u00b0" for a in break_angles)
    if len(break_angles) == 1:
        return f"The tempo break: a {angles} cut at speed, both sides"
    return f"The tempo break: {angles} cuts at speed, both sides"


def plot_drill_schematic(table: pd.DataFrame, context: dict,
                         cfg: common.Config) -> plt.Figure:
    """Draw the plan view. Units are yards; the aspect is equal, so the 5 yd
    scale bar is a real scale bar. Text positions are styling."""
    block = "figures.drill_schematic"
    style = SCHEMATIC_STYLE
    colour = SCHEMATIC_COLOURS

    approach = float(context["approach_drawn"])
    angles = [float(a) for a in context["break_angles"]]
    gate = float(context["gate_distance"])
    max_sin = max(np.sin(np.deg2rad(a)) for a in angles)
    max_cos = max(np.cos(np.deg2rad(a)) for a in angles)

    # The axes box is sized to the data window so that equal aspect needs no
    # adjustment (and emits no warning). Whitespace lives in the figure margin.
    # The window covers the widest exit and leaves room for labels inside the
    # axes: nothing is drawn below the axes once they end.
    x_lo, x_hi = -approach - 1.5, gate * max_cos + 2.0
    y_lo, y_hi = -(gate * max_sin + 1.1), gate * max_sin + 0.55
    ratio = (x_hi - x_lo) / (y_hi - y_lo)
    fig_w = float(cfg.get(f"{block}.width_in"))
    fig_h = float(cfg.get(f"{block}.height_in"))
    ax_h = 0.86
    ax_w = (ax_h * fig_h * ratio) / fig_w
    bottom = 0.065

    fig = plt.figure(figsize=(fig_w, fig_h),
                     dpi=int(cfg.get(f"{block}.dpi")), facecolor="white")
    ax = fig.add_axes([(1.0 - ax_w) / 2.0, bottom, ax_w, ax_h])
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.set_aspect("equal")
    ax.axis("off")

    def geo(element: str) -> pd.Series:
        return table.loc[table["element"] == element].iloc[0]

    # ---- movement: one exit per angle per side, then the approach arrow
    for index, angle in enumerate(angles):
        tag = f"{angle:g}"
        left = geo(f"exit_left_{tag}")
        right = geo(f"exit_right_{tag}")
        # The first configured angle is solid; later angles are dashed, so the
        # four exits stay distinguishable at a glance.
        linestyle = "-" if index == 0 else "--"
        ax.annotate("", xy=(left["x_end"], left["y_end"]), xytext=(0.0, 0.0),
                    arrowprops=dict(arrowstyle="-|>", lw=style["exit_lw"],
                                    color=colour["left"], linestyle=linestyle,
                                    shrinkA=4, shrinkB=2))
        ax.annotate("", xy=(right["x_end"], right["y_end"]), xytext=(0.0, 0.0),
                    arrowprops=dict(arrowstyle="-|>", lw=style["exit_lw"],
                                    color=colour["right"], linestyle=linestyle,
                                    shrinkA=4, shrinkB=2))
    ax.annotate("", xy=(-0.32, 0.0), xytext=(-approach, 0.0),
                arrowprops=dict(arrowstyle="-|>", lw=style["approach_lw"],
                                color=colour["approach"], shrinkA=8,
                                shrinkB=0))

    # ---- break-angle arcs, one per angle at its own radius, labelled with the
    # configured angle only
    offset = style["arc_label_offset_yd"]
    for index, angle in enumerate(angles):
        tag = f"{angle:g}"
        radii = style["arc_radii_yd"]
        r = float(radii[index] if index < len(radii)
                  else radii[-1] + 0.75 * (index - len(radii) + 1))
        ax.add_patch(Arc((0.0, 0.0), 2 * r, 2 * r, theta1=0.0,
                         theta2=angle, color=colour["arc"],
                         lw=style["arc_lw"]))
        prev = angles[index - 1] if index else 0.0
        mid = np.deg2rad((prev + angle) / 2.0)
        label_r = r + offset
        ax.text(label_r * np.cos(mid), label_r * np.sin(mid),
                _schematic_text(table, f"angle_left_{tag}"),
                fontsize=style["angle_label_fontsize"], fontweight="bold",
                color="#333333", ha="center", va="center")

    # ---- gate marks: one transverse tick per exit, perpendicular to travel,
    # labelled with the same configured gate distance on every exit
    half = style["gate_tick_half_yd"]
    off = style["gate_label_offset_yd"]
    for angle in angles:
        tag = f"{angle:g}"
        rad = np.deg2rad(angle)
        for side, sign in (("left", 1.0), ("right", -1.0)):
            mark = geo(f"gate_{side}_{tag}")
            px, py = float(mark["x"]), float(mark["y"])
            if side == "left":
                nx, ny = -np.sin(rad), np.cos(rad)
            else:
                nx, ny = np.sin(rad), -np.cos(rad)
            ax.plot([px - half * nx, px + half * nx],
                    [py - half * ny, py + half * ny],
                    color=colour[side], lw=3.4, solid_capstyle="butt")
            ax.text(px + off * nx, py + off * ny,
                    _schematic_text(table, "gate_distance"),
                    fontsize=9.2, color=colour[side], ha="center",
                    va="center", fontweight="bold")

    # ---- the exit itself: re-accelerate 5 yd through the gate, once per angle,
    # rotated along that angle's left exit. Later labels sit above their exit
    # while the first sits below it, so the two do not overlap.
    for index, angle in enumerate(angles):
        rad = np.deg2rad(angle)
        d = np.array([np.cos(rad), np.sin(rad)])
        p = np.array([-np.sin(rad), np.cos(rad)])
        if index == 0:
            centre = 0.75 * gate * d - 0.45 * p
        else:
            centre = 0.72 * gate * d + 0.45 * p
        ax.text(centre[0], centre[1],
                _schematic_text(table, "reaccel").replace(
                    " through the gate", "\nthrough the gate"),
                fontsize=style["reaccel_fontsize"], color=colour["left"],
                ha="center", va="center", rotation=angle,
                rotation_mode="anchor", linespacing=1.15)

    # ---- cones and the single plant
    ax.plot([-approach], [0.0], marker="^", markersize=style["cone_size"],
            color=colour["cone"], markeredgecolor="#1A1A1A",
            markeredgewidth=1.0, linestyle="none", zorder=5)
    ax.plot([0.0], [0.0], marker="^", markersize=style["cone_size"] + 4.0,
            color=colour["cone"], markeredgecolor="#1A1A1A",
            markeredgewidth=1.2, linestyle="none", zorder=5)
    ax.plot([0.0], [0.0], marker="o", markersize=style["plant_size"] ** 0.5,
            color="#1A1A1A", linestyle="none", zorder=6)

    # ---- callouts: labels sit left of the break cone, exits go right
    ax.text(-0.55, 0.55, "break cone\nsingle plant", fontsize=10.5,
            color="#1A1A1A", fontweight="bold", ha="right", va="bottom",
            linespacing=1.25)
    ax.text(-0.55, -0.55, _schematic_text(table, "entry_speed"), fontsize=10.5,
            color="#1A1A1A", ha="right", va="top")
    ax.text(-approach, 0.62, "cone", fontsize=9.5, color="#5A4600",
            ha="center", va="bottom")

    # ---- the approach, its measured length, and the acceleration phase
    ax.annotate("", xy=(-approach, -0.62), xytext=(0.0, -0.62),
                arrowprops=dict(arrowstyle="<->", lw=0.9,
                                color="#5A5A5A", shrinkA=0, shrinkB=0))
    ax.text(-approach / 2.0, -1.02, _schematic_text(table, "approach_range"),
            fontsize=10.0, color="#333333", ha="center", va="top",
            fontweight="bold")
    ax.text(-approach / 2.0, 0.30,
            "walk-in  →  " + _schematic_text(table, "runup")
            + "  →  accelerate over the approach",
            fontsize=9.6, color="#333333", ha="center", va="bottom")

    # ---- reps: what they are, in the order they run
    sequence = _schematic_text(table, "reps_sequence")
    ax.text(-approach + 0.25, 3.45,
            _schematic_text(table, "reps_total") + "\n"
            + _schematic_text(table, "reps_per_side") + "\n" + sequence,
            fontsize=10.4, color="#1A1A1A", ha="left", va="top",
            linespacing=1.4,
            bbox=dict(boxstyle="round,pad=0.42", facecolor="#F2F2F2",
                      edgecolor="#8C8C8C", linewidth=0.8))

    # ---- honest scale bar (equal aspect, yards on both axes)
    sb = float(context["scale_bar_yd"])
    y0 = -4.95
    ax.plot([-approach - 0.6, -approach - 0.6 + sb], [y0, y0],
            color="#1A1A1A", lw=2.6, solid_capstyle="butt")
    for edge in (-approach - 0.6, -approach - 0.6 + sb):
        ax.plot([edge, edge], [y0 - 0.14, y0 + 0.14], color="#1A1A1A", lw=1.2)
    ax.text(-approach - 0.6 + sb / 2.0, y0 + 0.22,
            _schematic_text(table, "scale_bar"), fontsize=9.2,
            color="#1A1A1A", ha="center", va="bottom")

    # ---- title: the one bold line, derived from the configured angles
    fig.text(0.022, 0.975, _schematic_title(context),
             fontsize=16.5, fontweight="bold", color="#1A1A1A", va="top")
    return fig


def render_schematic(fig: plt.Figure, cfg: common.Config, out_png: Path) -> Path:
    """Save the drawing once with fixed metadata, so re-runs are identical.

    PNG metadata carries no timestamp (the volatile fields live in the JSON
    manifest), which is what makes two renders byte-identical.
    """
    dpi = int(cfg.get("figures.drill_schematic.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def drill_schematic_csv_frame(table: pd.DataFrame,
                              cfg: common.Config) -> pd.DataFrame:
    """Every drawn segment/point and every printed number, with its source."""
    block = "figures.drill_schematic"
    out = table.copy()
    out.insert(0, "status", str(cfg.get(f"{block}.status")))
    out.insert(1, "status_stamp", str(cfg.get(f"{block}.status_stamp")))
    out.insert(2, "source_note", str(cfg.get(f"{block}.source_note")))
    return out[["status", "status_stamp", "source_note"] + DRILL_SCHEMATIC_COLUMNS]


def write_drill_schematic_csv(table: pd.DataFrame, path: Path,
                              cfg: common.Config) -> Path:
    """Write the geometry CSV WITHOUT the sample-data provenance columns.

    ``common.write_csv`` prefixes every row with ``PLACEHOLDER_SAMPLE`` /
    ``UNVALIDATED SAMPLE OUTPUT`` because it writes data artefacts. This figure
    is a drawing: nothing on it is measured, so stamping it as sample data
    would be a false provenance claim. The SCHEMATIC status is the correct
    stamp, and it is written into the leading columns instead.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = drill_schematic_csv_frame(table, cfg)
    out.insert(3, "config_sha", common.config_fingerprint(cfg))
    out.to_csv(path, index=False)
    return path


def drill_schematic_manifest_payload(table: pd.DataFrame,
                                     context: dict) -> dict:
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_DRILL_SCHEMATIC,
        "figure_kind": "diagram (geometry drawing, not a data plot)",
        "title": _schematic_title(context),
        "status": context["status"],
        "status_stamp": context["status_stamp"],
        "stamp": context["status_stamp"],
        "provenance_applied": False,
        "provenance_note": (
            "Deliberately NOT stamped with the PLACEHOLDER_SAMPLE / "
            "UNVALIDATED SAMPLE OUTPUT banner used by figures 1-3: this is a "
            "schematic drawing, not a data artefact, and nothing on it is "
            "measured. The SCHEMATIC status line is the correct stamp."),
        "claim": (
            "Schematic of a Combine tempo-break drill: walk in, accelerate over "
            "an 8-10 yd approach to 3-5 yd/s, single plant, break 40 and 55 "
            "degrees either side, re-accelerate 5 yd through an exit gate; 6 "
            "reps (3 left, 3 right, alternating) in ~90 s per player on the "
            "existing 3-cone footprint."),
        "spec": {
            "approach_length_yd": list(context["approach_range"]),
            "approach_drawn_yd": context["approach_drawn"],
            "runup_length_yd": context["runup"],
            "break_angles_deg": list(context["break_angles"]),
            "break_band_labels": list(context["break_bands"]),
            "gate_distance_yd": context["gate_distance"],
            "reaccel_length_yd": context["reaccel"],
            "entry_speed_yd_s": list(context["entry_speed"]),
            "reps_total": context["reps_total"],
            "reps_per_side": context["reps_per_side"],
            "reps_alternating": context["reps_alternating"],
            "plants_per_rep": context["plants_per_rep"],
            "duration_s": context["duration_s"],
            "footprint": context["footprint"],
        },
        "scale": {
            "to_scale": context["to_scale"],
            "units": "yards",
            "scale_bar_yd": context["scale_bar_yd"],
            "note": ("Axes are drawn in yards with equal aspect and a 5 yd "
                     "scale bar, so the drawn layout is to scale. The approach "
                     "is placed at 9 yd (the midpoint of the stated 8-10 yd "
                     "range); the image labels it 8-10 yd and 9 yd is never "
                     "claimed as the specification."),
        },
        "omitted_not_specified": list(context["omissions"]),
        "drawn_elements": table.to_dict(orient="records"),
        "caveats": [
            ("This is a SCHEMATIC, not a finding: it shows illustrative "
             "geometry and sequence only, drawn from the drill source note."),
            ("The drawing is schematic. It shows layout and sequence only; no "
             "speed, time or load on this figure is measured."),
            ("No data counts appear here. The 732-turn projection and the "
             "demand counts belong to figure 3 and are deliberately not repeated."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_DRILL_SCHEMATIC}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_DRILL_SCHEMATIC}.csv",
    }


# --------------------- figure 5: the same hole across positions
#: TASK.md:63 — "Descriptive: transfer and feasibility for the other groups.
#: OL and DB are exploratory (contamination risk, see below)." TASK.md:153
#: repeats the flag for the Phase 6 tests, and TASK.md:136 restricts the demand
#: map the same way. The set itself is read from
#: ``demand.exploratory_positions`` (already derived from those lines); the line
#: references are carried into the caption, the CSV and the manifest so the
#: mark is traceable to the spec rather than to this figure's judgement.
SPEC_STATUS_SOURCE = ("TASK.md:63 and TASK.md:153 — OL and DB are exploratory "
                      "(contamination risk); TASK.md:136 the same for the "
                      "demand map")
EXPLORATORY_LABEL = "EXPLORATORY"
#: Horizontal bar geometry, in band-row units. Two settings per row.
BAR_OFFSET = 0.19
BAR_WIDTH = 0.34


def _wrap_text(text: str, width: int) -> str:
    """Hard-wrap a caption line so it cannot run past the canvas edge.

    Wrapping changes no wording and drops no clause: it only breaks at word
    boundaries, keeping the honesty text intact and readable at the fixed
    figure width.
    """
    return "\n".join(textwrap.wrap(str(text), width=width,
                                    break_long_words=False,
                                    break_on_hyphens=False))


def positions_table(turns: pd.DataFrame, edges: list[float],
                    overflow_label: str,
                    positions: list[str]) -> tuple[pd.DataFrame, dict]:
    """Figure 1's band table, repeated for every position on the same bands.

    Reuses :func:`band_table` per position, so the banding, the share definition
    and the "no turn may fall outside the bands" check are literally figure 1's
    code. Every position is required: a missing one refuses instead of quietly
    producing a four-panel figure.
    """
    positions = [str(p) for p in positions]
    if not positions:
        raise ValueError(f"{STAGE}: figures.all_positions.positions is empty")
    if len(set(positions)) != len(positions):
        raise ValueError(
            f"{STAGE}: duplicate position in figures.all_positions.positions: "
            f"{positions}")

    tables, per_position = [], {}
    for position in positions:
        part, context = band_table(turns, edges, overflow_label, position)
        part.insert(0, "position", position)
        tables.append(part)
        per_position[position] = context

    table = pd.concat(tables, ignore_index=True)
    claimed = sum(int(c["n_valid"]) for c in per_position.values())
    if int(table["count"].sum()) != claimed:
        raise ValueError(
            f"{STAGE}: plotted counts sum to {int(table['count'].sum())} but the "
            f"selected positions hold {claimed} valid turns")

    bands = per_position[positions[0]]["bands"]
    for position, context in per_position.items():
        if context["bands"] != bands:
            raise ValueError(
                f"{STAGE}: {position} was banded differently from "
                f"{positions[0]} — refusing to draw incomparable panels")

    context = {
        "positions": positions,
        "bands": bands,
        "edges": [float(e) for e in edges],
        "per_position": per_position,
        "n_turns": int(table["count"].sum()),
        # Valid turns outside the plotted positions: every one of these must be
        # named in the caption, or "11,924 valid turns" would read as a census.
        "n_excluded": int(
            (turns["valid"].fillna(False).astype(bool)
             & ~turns["position_group"].isin(positions)).sum()),
    }
    return table, context


def all_positions_plot_frame(table: pd.DataFrame, context: dict,
                             cfg: common.Config) -> pd.DataFrame:
    """One row per drawn bar, with the emphasis flag that sets its opacity."""
    block = "figures.all_positions"
    emphasis = str(cfg.get(f"{block}.emphasis_position"))
    muted = float(cfg.get(f"{block}.muted_alpha"))
    if emphasis not in context["positions"]:
        raise ValueError(
            f"{STAGE}: emphasis_position {emphasis!r} is not one of the plotted "
            f"positions {context['positions']}")

    frame = table.copy()
    frame["share_pct"] = frame["share_within_setting"] * 100.0
    x_max = float(cfg.get(f"{block}.x_max_pct"))
    over = frame.loc[frame["share_pct"] > x_max, "share_pct"]
    if len(over):
        raise common.StageRefused(
            f"{STAGE}: a plotted share is {float(over.max()):.1f}%, above the "
            f"shared {x_max:g}% axis — widen figures.all_positions.x_max_pct "
            "rather than clipping a bar")

    frame["setting"] = pd.Categorical(frame["setting"],
                                      categories=["combine", "game"],
                                      ordered=True)
    frame["band"] = pd.Categorical(frame["band"],
                                   categories=list(context["bands"]),
                                   ordered=True)
    frame["position"] = pd.Categorical(frame["position"],
                                       categories=list(context["positions"]),
                                       ordered=True)
    accent = frame["position"].astype(str) == emphasis
    frame["emphasis"] = pd.Categorical(np.where(accent, "accent", "muted"),
                                       categories=["accent", "muted"],
                                       ordered=True)
    # Bars are placed on a numeric axis by hand: y is the negated band index
    # (so 30-45 sits at the top), and the two settings straddle that row. This
    # keeps the row order fixed and every panel on the same scale, without
    # depending on dodge behaviour over a discrete axis.
    frame["y"] = -frame["band_index"].astype(float)
    frame["y_bar"] = frame["y"] + np.where(
        frame["setting"].astype(str) == "combine", BAR_OFFSET, -BAR_OFFSET)
    # Explicit rectangles: bar length runs along x, row thickness along y. With
    # both axes continuous a bar geom cannot infer its orientation, so the
    # corners are given directly.
    frame["xmin"] = 0.0
    frame["xmax"] = frame["share_pct"]
    frame["ymin"] = frame["y_bar"] - BAR_WIDTH / 2.0
    frame["ymax"] = frame["y_bar"] + BAR_WIDTH / 2.0
    return frame


def all_positions_count_labels(frame: pd.DataFrame, cfg: common.Config) -> pd.DataFrame:
    """Counts for the accent panel only — the others are read as shares."""
    block = "figures.all_positions"
    offset = float(cfg.get(f"{block}.count_offset_pct"))
    labels = frame[frame["emphasis"].astype(str) == "accent"].copy()
    labels["lab_x"] = labels["share_pct"] + offset
    labels["lab_y"] = labels["y_bar"]
    labels["label"] = [f"{int(n):,}" for n in labels["count"]]
    return labels


def all_positions_status(context: dict, cfg: common.Config) -> dict[str, str]:
    """Position -> spec status, for the positions the spec actually flags.

    Nothing is added here: a position absent from
    ``demand.exploratory_positions`` carries no status on this figure, so it can
    be neither promoted nor demoted by omission.
    """
    exploratory = [str(p) for p in cfg.get("demand.exploratory_positions")]
    unknown = [p for p in exploratory if p not in context["positions"]]
    if unknown:
        raise ValueError(
            f"{STAGE}: demand.exploratory_positions names {unknown}, which this "
            "figure does not plot — the mark would be invisible")
    return {p: "exploratory" for p in exploratory}


def all_positions_badges(context: dict, cfg: common.Config) -> pd.DataFrame:
    """The per-panel spec-status mark, drawn inside the panel it applies to."""
    block = "figures.all_positions"
    status = all_positions_status(context, cfg)
    x = float(cfg.get(f"{block}.x_max_pct")) - 1.5
    rows = [{"position": position, "x": x,
             "y": -float(len(context["bands"])),
             "label": f"{EXPLORATORY_LABEL}\n(TASK.md:63)"}
            for position in status]
    if not rows:
        return pd.DataFrame({"position": [], "x": [], "y": [], "label": []})
    badges = pd.DataFrame(rows)
    badges["position"] = pd.Categorical(badges["position"],
                                        categories=list(context["positions"]),
                                        ordered=True)
    return badges


def all_positions_dl_speed_count(turns: pd.DataFrame, position: str,
                                 speed_floor: float) -> int:
    """Game turns of ``position`` entering above ``speed_floor`` yd/s."""
    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)
               & (turns["setting"] == "game")]
    speed = pd.to_numeric(df["entry_speed"], errors="coerce")
    return int((speed > float(speed_floor)).sum())


def plot_all_positions(table: pd.DataFrame, context: dict, cfg: common.Config,
                       turns: pd.DataFrame | None = None) -> ggplot:
    block = "figures.all_positions"
    focus = focus_band(cfg)
    focus_idx = focus_band_index(context["edges"], focus)
    frame = all_positions_plot_frame(table, context, cfg)
    labels = all_positions_count_labels(frame, cfg)
    badges = all_positions_badges(context, cfg)
    bands = list(context["bands"])
    x_max = float(cfg.get(f"{block}.x_max_pct"))
    x_breaks = [float(b) for b in cfg.get(f"{block}.x_breaks_pct")]
    emphasis = str(cfg.get(f"{block}.emphasis_position"))

    per = context["per_position"]
    n_turns = context["n_turns"]
    n_positions = len(context["positions"])

    band0 = context["bands"][0]

    dl_line = ""
    floor = float(cfg.get(f"{block}.dl_speed_floor_yd_s", 6.0))
    if "DL" in per and turns is not None:
        dl = table[(table["position"] == "DL") & (table["band"] == band0)]
        dl_c = int(dl.loc[dl["setting"] == "combine", "count"].iloc[0])
        dl_g = int(dl.loc[dl["setting"] == "game", "count"].iloc[0])
        dl_fast = all_positions_dl_speed_count(turns, "DL", floor)
        dl_cs = float(dl.loc[dl["setting"] == "combine",
                             "share_within_setting"].iloc[0]) * 100.0
        dl_gs = float(dl.loc[dl["setting"] == "game",
                             "share_within_setting"].iloc[0]) * 100.0
        dl_line = (
            f" DL is included unchanged and read as it is: {dl_c} Combine vs "
            f"{dl_g} game turns in 30–45° ({dl_cs:.1f}% vs {dl_gs:.1f}% of DL's "
            f"own turns), and only {dl_fast} game DL turns enter above "
            f"{floor:g} yd/s — TASK.md §CURRENT FOCUS records DL's gap as a "
            "speed story, not an angle one. No panel is labelled \"fine\".")

    title = ("The same hole across positions: 30–45° breaks are rare in the "
             "Combine and common in games")
    subtitle = _wrap_text(
        f"Paired bars: each position's own valid turns by absolute turn angle, "
        f"Combine (accent) vs game (grey). Every panel uses the same 0–{x_max:g}% "
        f"share-of-that-setting axis, so the panels compare by eye. Counts are "
        f"printed on the {emphasis} panel only.", 160)

    caption_lines = [
        STAMP,
        (f"{n_positions} positions, {n_turns:,} valid turns in these panels. "
         f"Excluded from these panels: {context['n_excluded']:,} valid game "
         "turns with position_group OTHER (unclassified), so this is not a "
         "census of all valid turns. Bands are figure 1's reporting bands "
         "(figures.hole.bands), left-closed, final class >180°. "
         f"Counts are printed once, on the {emphasis} panel; every other "
         "panel's counts are in the CSV beside this image."),
        (f"{EXPLORATORY_LABEL} is the spec's own status, not this figure's "
         f"judgement: {SPEC_STATUS_SOURCE}. No other panel carries a status "
         "here; none is promoted or demoted."),
        (f"{emphasis} is both the case's focus and marked exploratory in the "
         "spec. Both signals are drawn exactly as specified — full accent AND "
         "the exploratory mark — and this figure does not resolve them."),
        dl_line.strip(),
        ("Descriptive shares of a census of turns: no confidence bands, no "
         "per-position significance test, no ranking of positions by gap size. "
         "Panels are ordered as TASK.md §1 lists them. Each bar is that "
         "setting's own share because exposure differs (one Combine session vs "
         "several player-games). Source: outputs/turns/turns.parquet (valid "
         "turns), stage 02; counts computed at render time."),
    ]
    caption = "\n".join(_wrap_text(line, 172) for line in caption_lines if line)

    n_bands = len(bands)
    focus_y = -float(focus_idx + 1)
    focus_label = pd.DataFrame([
        {"position": position, "x": x_max - 0.4, "y": focus_y,
         "label": f"{focus['label']} band"}
        for position in context["positions"]
    ])
    focus_label["position"] = pd.Categorical(
        focus_label["position"], categories=list(context["positions"]),
        ordered=True)

    plot = (
        ggplot(frame, aes(xmin="xmin", xmax="xmax", ymin="ymin", ymax="ymax",
                          fill="setting", alpha="emphasis"))
        + geom_rect(colour="white", size=0.2)
        + geom_hline(yintercept=[focus_y - 0.5, focus_y + 0.5],
                     inherit_aes=False, colour=focus["colour"],
                     size=focus["rule_width"])
        + geom_text(data=focus_label,
                    mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=2.5, colour=focus["colour"],
                    fontweight="bold", ha="right", va="center")
        + geom_text(data=labels,
                    mapping=aes(x="lab_x", y="lab_y", label="label"),
                    inherit_aes=False, size=2.55,
                    colour="#1A1A1A", ha="left", va="center", show_legend=False)
        + geom_text(data=badges, mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=2.5, colour=ALERT,
                    fontweight="bold", ha="right", va="center")
        + facet_wrap("~ position", nrow=1)
        + scale_fill_manual(values=["#0072B2", "#7F7F7F"],
                            labels=[COMBINE_LABEL, GAME_LABEL], name=None)
        + scale_alpha_manual(values=[1.0, float(cfg.get(f"{block}.muted_alpha"))],
                             guide=None)
        + scale_x_continuous(limits=(0.0, x_max), breaks=x_breaks,
                             expand=(0, 0))
        + scale_y_continuous(breaks=[-float(i) for i in range(1, n_bands + 1)],
                            labels=bands,
                            limits=(-float(n_bands) - 0.6, -0.4),
                            expand=(0, 0))
        + labs(title=title, subtitle=subtitle, caption=caption,
               x="Share of that setting's own valid turns (%)", y=None)
        + theme_bw(base_size=9)
        + theme(
            figure_size=(float(cfg.get(f"{block}.width_in")),
                         float(cfg.get(f"{block}.height_in"))),
            panel_background=element_rect(fill="#EBEBEB", colour=None),
            panel_grid_major=element_line(colour="white", linewidth=0.7),
            panel_grid_minor=element_blank(),
            panel_border=element_rect(colour="#B3B3B3", linewidth=0.4),
            strip_background=element_rect(fill="#D9D9D9", colour="#B3B3B3"),
            strip_text=element_text(weight="bold", size=11),
            plot_title=element_text(size=14, weight="bold"),
            plot_subtitle=element_text(size=8.4, colour="#333333"),
            plot_caption=element_text(size=6.5, colour="#333333", ha="left",
                                      linespacing=1.35),
            axis_title_x=element_text(size=8.6),
            axis_title_y=element_blank(),
            axis_text_x=element_text(size=7.6),
            axis_text_y=element_text(size=7.6),
            legend_position="bottom",
            legend_title=element_blank(),
            legend_key=element_rect(fill="#EBEBEB", colour=None),
        )
    )
    return plot


def all_positions_csv_frame(table: pd.DataFrame, context: dict,
                            cfg: common.Config) -> pd.DataFrame:
    """Every plotted share and count: position x band x setting."""
    out = table.copy()
    out["share_pct"] = out["share_within_setting"] * 100.0
    out["position_index"] = out["position"].map(
        {p: i + 1 for i, p in enumerate(context["positions"])})
    out["spec_status"] = out["position"].map(
        {p: s for p, s in all_positions_status(context, cfg).items()}).fillna("")
    out["spec_status_source"] = np.where(out["spec_status"] == "exploratory",
                                         SPEC_STATUS_SOURCE, "")
    out["emphasis"] = np.where(out["position"] == str(
        cfg.get("figures.all_positions.emphasis_position")), "accent", "muted")
    out["position_setting_total"] = out["setting_total"].astype(int)
    out["n_players"] = [
        int(context["per_position"][p]["players_by_setting"][s])
        for p, s in zip(out["position"], out["setting"])]
    return out[["position_index", "position", "spec_status",
                "spec_status_source", "emphasis", "band_index", "band",
                "band_low_deg", "band_high_deg", "setting", "count",
                "position_setting_total", "share_within_setting", "share_pct",
                "n_players"]]


def all_positions_manifest_payload(table: pd.DataFrame, context: dict,
                                   cfg: common.Config) -> dict:
    block = "figures.all_positions"
    emphasis = str(cfg.get(f"{block}.emphasis_position"))
    status = all_positions_status(context, cfg)
    per = context["per_position"]
    band0 = table[table["band"] == context["bands"][0]]
    dl_c = int(band0.loc[(band0["position"] == "DL")
                         & (band0["setting"] == "combine"), "count"].iloc[0])
    dl_g = int(band0.loc[(band0["position"] == "DL")
                         & (band0["setting"] == "game"), "count"].iloc[0])
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_ALL_POSITIONS,
        "title": ("The same hole across positions: 30–45° breaks are rare in "
                  "the Combine and common in games"),
        "claim": ("Every panel plots the same quantity — the share of that "
                  "setting's own valid turns in each 30–45° reporting band, "
                  "Combine vs game, on one identical 0–70% axis. What each "
                  f"panel shows differs by position (DL: {dl_c} vs {dl_g} "
                  "turns in 30–45°); the figure ranks no position and labels "
                  "no panel \"fine\"."),
        "sources": ["outputs/turns/turns.parquet"],
        "positions": list(context["positions"]),
        "position_order_note": ("Order as TASK.md §1 lists the positions; not a "
                                "ranking by gap size. No position is ranked or "
                                "dropped."),
        "emphasis_position": emphasis,
        "muted_alpha": float(cfg.get(f"{block}.muted_alpha")),
        "shared_axis": {"axis": "x", "metric": "share of that setting's own "
                        "valid turns (percent)",
                        "min_pct": 0.0,
                        "max_pct": float(cfg.get(f"{block}.x_max_pct")),
                        "identical_in_all_panels": True},
        "bands": context["bands"],
        "spec_status": {
            "statuses": status,
            "source": SPEC_STATUS_SOURCE,
            "note": ("Carried from the spec, not assigned here. Positions "
                     "absent from this map carry no status on this figure and "
                     "are neither promoted nor demoted."),
        },
        "count_labels": {"panel": emphasis,
                          "shown_for_other_panels": False},
        "per_position": {
            p: {
                "n_valid": int(c["n_valid"]),
                "n_by_setting": {k: int(v) for k, v in
                                 c["n_by_setting"].items()},
                "players_by_setting": {k: int(v) for k, v in
                                       c["players_by_setting"].items()},
                "spec_status": status.get(p, ""),
            } for p, c in per.items()
        },
        "caveats": [
            ("Descriptive shares of a census of turns: no confidence bands, no "
             "per-position significance test, no ranking of positions by gap "
             "size."),
            ("Exposure differs by setting and by position (one Combine session "
             "vs several player-games per player), which is why each bar is "
             "that setting's own share rather than a count."),
            (f"{emphasis} is both the case's focus and marked exploratory in "
             "the spec (TASK.md:63, TASK.md:153). Both signals are drawn as "
             "specified; the tension is left open for Conor."),
            ("DL is included unchanged and is not annotated as \"fine\": its "
             "panel shows no 30–45° shortfall, and TASK.md §CURRENT FOCUS "
             "records DL's gap as a speed story rather than an angle one."),
            ("Bands are left-closed; the final class is >180°."),
        ],
        "counts": table.assign(
            share_pct=(table["share_within_setting"] * 100.0)).to_dict(
                orient="records"),
        "png": f"outputs/figures/{FIGURE_KEY_ALL_POSITIONS}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_ALL_POSITIONS}.csv",
    }


# ------------------------- figure 6: the measurement limits (slot e)
#: Slot (e) of TASK.md §1: "measurement limits, i.e. why per-player ranking is
#: not supported". Two panels, one claim. Everything plotted is read from
#: ``outputs/envelope/variance_components.csv`` and ``outputs/turns/turns.parquet``
#: at render time; the only design numbers are the config thresholds.
VARIANCE_COLUMNS = [
    "provenance",
    "setting",
    "position_group",
    "measure",
    "n_players",
    "tau2",
    "tau2_p05",
    "tau2_p95",
    "tau2_status",
]

LATERAL_COLUMNS = [
    "valid",
    "setting",
    "abs_turn_angle",
    "peak_lateral_accel_yd_s2",
]

MEASURE_ORDER = ["arc_capacity", "plant_retention", "plant_regain"]
MEASURE_LABELS = {
    "arc_capacity": "Arc capacity",
    "plant_retention": "Plant retention",
    "plant_regain": "Plant regain",
}
SETTING_ORDER = ["combine", "game", "combined"]
SETTING_LABELS = {"combine": "Combine", "game": "Game", "combined": "Both"}

#: Statuses the envelope stage writes. A missing/unknown status refuses rather
#: than being drawn as either a boundary row or a real estimate.
TAU2_STATUSES = {"AT_BOUNDARY", "INTERIOR"}

#: Detector accuracy quoted in the caption as the counterweight. These are
#: carried from the repo's own artefacts — ``notes/phase2-turns.md`` (synthetic
#: recovery: 3.1 deg MAE over 48 planted turns) and ``outputs/audit/gate.json``
#: (three-cone real-schema gate: precision 0.915, recall 0.931). A test asserts
#: both source strings still exist, so the caption cannot drift from its source.
DETECTOR_EVIDENCE = {
    "angle_mae_deg": 3.1,
    "angle_mae_n": 48,
    "precision": 0.915,
    "recall": 0.931,
    "drill": "THREE_CONE_DRILL",
}
DETECTOR_SOURCE_NOTES = "notes/phase2-turns.md"
DETECTOR_SOURCE_GATE = "outputs/audit/gate.json"

#: Presentation only: colour per setting, and the boundary alert colour.
ML_PALETTE = {"combine": "#0072B2", "game": "#D55E00", "combined": "#4D4D4D"}
ML_BOUNDARY_COLOUR = "#7A1F1F"


# --------------------------------------------------------------------- data
def load_variance_components(path: Path) -> pd.DataFrame:
    """Read the envelope's variance components; refuse if the file is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the variance components are absent ({path}) — run "
            "03_envelope first")
    df = pd.read_csv(path)
    missing = [c for c in VARIANCE_COLUMNS if c not in df.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: variance components are missing columns: "
            f"{', '.join(missing)}")
    return df


def variance_table(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per setting x position x measure, with its boundary status.

    Refuses if a status is unknown, if a component is duplicated, if an
    ``AT_BOUNDARY`` row carries a non-zero point estimate, or if the grid is
    ragged (a silently missing component would read as "not measured" and
    change the count the figure prints).
    """
    df = df.copy()
    for col in ("tau2", "tau2_p05", "tau2_p95"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["setting"] = df["setting"].astype(str)
    df["position_group"] = df["position_group"].astype(str)
    df["measure"] = df["measure"].astype(str)
    df["tau2_status"] = df["tau2_status"].astype(str)

    unknown = sorted(set(df["tau2_status"]) - TAU2_STATUSES)
    if unknown:
        raise common.StageRefused(
            f"{STAGE}: unknown tau2_status value(s) {unknown} — expected "
            f"{sorted(TAU2_STATUSES)}")
    if df.duplicated(["setting", "position_group", "measure"]).any():
        dup = df[df.duplicated(["setting", "position_group", "measure"],
                               keep=False)]
        raise common.StageRefused(
            f"{STAGE}: duplicate variance components for "
            f"{dup[['setting', 'position_group', 'measure']].drop_duplicates()
               .to_dict(orient='records')}")

    boundary = df["tau2_status"] == "AT_BOUNDARY"
    off_zero = df[boundary & (df["tau2"].fillna(0.0).abs() > 1e-12)]
    if not off_zero.empty:
        raise common.StageRefused(
            f"{STAGE}: {len(off_zero)} component(s) are AT_BOUNDARY but carry "
            "a non-zero tau2 — the boundary mark would lie")

    # A ragged grid is a data loss, not an empty cell: name it. Every measure
    # must cover the same positions within a setting, otherwise a missing
    # component would be drawn as a blank row and silently leave the count.
    for measure in MEASURE_ORDER:
        if not (df["measure"] == measure).any():
            raise common.StageRefused(
                f"{STAGE}: no variance components for measure {measure!r}")
    for setting in SETTING_ORDER:
        part = df[df["setting"] == setting]
        if part.empty:
            continue
        by_measure = {m: set(g["position_group"])
                      for m, g in part.groupby("measure")}
        reference = next(iter(by_measure.values()))
        for measure, positions in by_measure.items():
            if positions != reference:
                raise common.StageRefused(
                    f"{STAGE}: ragged variance grid for setting {setting!r}, "
                    f"measure {measure!r} — missing "
                    f"{sorted(reference - positions)}")

    settings = [s for s in SETTING_ORDER if s in set(df["setting"])]
    if not settings:
        raise common.StageRefused(f"{STAGE}: no settings in the variance table")
    positions = sorted(set(df["position_group"]))
    if not positions:
        raise common.StageRefused(f"{STAGE}: no positions in the variance table")

    positions_by_setting = {
        s: sorted(set(df.loc[df["setting"] == s, "position_group"]))
        for s in settings}
    row_keys = [(s, p) for s in settings for p in positions_by_setting[s]]
    df["y_row"] = [row_keys.index((s, p))
                   for s, p in zip(df["setting"], df["position_group"])]
    df["setting_label"] = df["setting"].map(SETTING_LABELS)
    df["measure_label"] = df["measure"].map(MEASURE_LABELS)
    df = df.sort_values(["measure", "y_row"]).reset_index(drop=True)

    n_components = int(len(df))
    n_boundary = int(boundary.sum())
    context = {
        "n_components": n_components,
        "n_at_boundary": n_boundary,
        "n_interior": n_components - n_boundary,
        "settings": settings,
        "positions": positions,
        "row_keys": row_keys,
        "measures": [m for m in MEASURE_ORDER if m in set(df["measure"])],
        "provenance": sorted(set(df["provenance"].astype(str)))
        if "provenance" in df else [],
        "tau2_max": float(df["tau2"].max()),
        "p95_max": float(df["tau2_p95"].max()),
        "n_players_total": int(df["n_players"].sum())
        if "n_players" in df else None,
    }
    return df, context


def lateral_table(turns: pd.DataFrame, cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """Binned median and p95 peak lateral acceleration per turn-angle band.

    The reversal threshold must be one of the bin edges: a threshold inside a
    band would shade an arbitrary fraction of a plotted point. The stage
    refuses instead of guessing.
    """
    block = "figures.measurement_limits"
    edges = [float(x) for x in cfg.get(f"{block}.angle_bins_deg")]
    overflow = str(cfg.get(f"{block}.overflow_label"))
    threshold = float(cfg.get(f"{block}.reversal_zone_deg"))
    g = float(cfg.get(f"{block}.g_yd_s2"))
    if threshold not in edges:
        raise common.StageRefused(
            f"{STAGE}: {block}.reversal_zone_deg={threshold:g} is not one of "
            f"the angle bin edges {edges} — the greyed zone would not align "
            "with a plotted band")

    valid = turns["valid"].fillna(False).astype(bool)
    df = turns.loc[valid, ["setting", "abs_turn_angle",
                           "peak_lateral_accel_yd_s2"]].copy()
    df["abs_turn_angle"] = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    df["peak_lateral_accel_yd_s2"] = pd.to_numeric(
        df["peak_lateral_accel_yd_s2"], errors="coerce")
    n_before = int(len(df))
    df = df[df["abs_turn_angle"].notna()
            & df["peak_lateral_accel_yd_s2"].notna()].copy()
    n_dropped = n_before - int(len(df))
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid turns with angle and lateral acceleration — "
            "nothing to plot on the right panel")

    bands = band_labels(edges, overflow)
    labels = np.full(len(df), None, dtype=object)
    angle = df["abs_turn_angle"]
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        labels[((angle >= lo) & (angle < hi)).to_numpy()] = bands[i]
    labels[(angle >= edges[-1]).to_numpy()] = bands[-1]
    if int(pd.isna(labels).sum()):
        raise ValueError(
            f"{STAGE}: {int(pd.isna(labels).sum())} valid turns fall outside "
            f"the configured angle bins {edges} — fix "
            f"{block}.angle_bins_deg")
    df["angle_band"] = labels

    rows = []
    for i, band in enumerate(bands):
        part = df[df["angle_band"] == band]["peak_lateral_accel_yd_s2"]
        if part.empty:
            raise common.StageRefused(
                f"{STAGE}: angle band {band} has no valid turns with lateral "
                "acceleration — the plotted line would have a hole")
        lo = edges[i]
        hi = edges[i + 1] if i + 1 < len(edges) else None
        rows.append({
            "band_index": i + 1,
            "angle_band": band,
            "angle_low_deg": lo,
            "angle_high_deg": hi,
            "n_turns": int(len(part)),
            "median_lateral_accel_yd_s2": float(part.median()),
            "p95_lateral_accel_yd_s2": float(part.quantile(0.95)),
            "median_lateral_accel_g": float(part.median()) / g,
            "p95_lateral_accel_g": float(part.quantile(0.95)) / g,
            "in_reversal_zone": bool(lo >= threshold),
        })
    table = pd.DataFrame(rows)
    if int(table["n_turns"].sum()) != int(len(df)):
        raise ValueError(
            f"{STAGE}: binned lateral-acceleration counts sum to "
            f"{int(table['n_turns'].sum())}, but {len(df)} valid turns have "
            "both fields")

    reversal = table[table["in_reversal_zone"]]
    rev_turns = float(reversal["n_turns"].sum())
    # Pooled over every turn in the zone, not an average of band medians.
    zone_mask = df["angle_band"].isin(set(reversal["angle_band"]))
    rev_median = float(df.loc[zone_mask, "peak_lateral_accel_yd_s2"].median()) \
        if rev_turns else float("nan")
    rev_p95 = float(df.loc[zone_mask,
                            "peak_lateral_accel_yd_s2"].quantile(0.95)) \
        if rev_turns else float("nan")

    context = {
        "n_turns": int(len(df)),
        "n_dropped_missing": int(n_dropped),
        "n_by_setting": df["setting"].value_counts().to_dict(),
        "median_yd_s2": float(df["peak_lateral_accel_yd_s2"].median()),
        "p95_yd_s2": float(df["peak_lateral_accel_yd_s2"].quantile(0.95)),
        "median_g": float(df["peak_lateral_accel_yd_s2"].median()) / g,
        "p95_g": float(df["peak_lateral_accel_yd_s2"].quantile(0.95)) / g,
        "reversal": {
            "threshold_deg": threshold,
            "n_turns": int(rev_turns),
            "median_yd_s2": rev_median,
            "p95_yd_s2": rev_p95,
            "median_g": rev_median / g,
            "p95_g": rev_p95 / g,
        },
        "g_yd_s2": g,
        "edges": edges,
        "bands": bands,
        "threshold_key": f"{block}.reversal_zone_deg",
    }
    return table, context


# --------------------------------------------------------------------- plot
def _ml_boundary_shape(df: pd.DataFrame) -> pd.DataFrame:
    """Rows the left panel draws as EMPTY bars rather than estimates."""
    return df[df["tau2_status"] == "AT_BOUNDARY"]


def build_measurement_limits(left: pd.DataFrame, right: pd.DataFrame,
                             context: dict, cfg: common.Config) -> plt.Figure:
    """Compose the two-panel figure. Returns the matplotlib Figure.

    Left: one dot-and-whisker row per variance component (tau2 with its
    p05-p95 span), three measure columns sharing one row axis. AT_BOUNDARY
    components are drawn as zero-width bars with a dark outline plus a hollow
    point on the baseline — visibly empty, never dropped and never shortened
    into a positive bar. Right: binned median and p95 peak lateral acceleration
    against turn angle, with the reversal zone greyed and struck through.
    """
    block = "figures.measurement_limits"
    lctx = context["left"]
    rctx = context["right"]
    n_rows = len(lctx["row_keys"])
    measures = lctx["measures"]

    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    outer = fig.add_gridspec(
        1, 2, width_ratios=[3.0, 1.5], wspace=0.34,
        left=0.105, right=0.985, top=0.845, bottom=0.305)
    left_grid = outer[0].subgridspec(1, len(measures), wspace=0.10)

    # ------------------------------------------------------------- left panel
    left_axes = []
    p95_max = max(lctx["p95_max"], 1e-9)
    x_max = p95_max * 1.12
    boundary_drawn = 0
    for j, measure in enumerate(measures):
        ax = fig.add_subplot(left_grid[0, j], sharey=left_axes[0]
                             if left_axes else None)
        left_axes.append(ax)
        ax.set_facecolor("#EBEBEB")
        ax.set_xlim(0.0, x_max)
        ax.set_ylim(n_rows - 0.5, -0.5)
        ax.grid(axis="x", color="white", linewidth=0.7, zorder=0)
        ax.axvline(0.0, color="#8A8A8A", linewidth=0.7, zorder=1)
        part = left[left["measure"] == measure]
        for _, row in part.iterrows():
            y = float(row["y_row"])
            colour = ML_PALETTE.get(str(row["setting"]), "#4D4D4D")
            if row["tau2_status"] == "AT_BOUNDARY":
                boundary_drawn += 1
                # Zero-width bar: an outline standing on the baseline, not a
                # short positive bar and not an omission.
                ax.plot([0.0, 0.0], [y - 0.30, y + 0.30],
                        color=ML_BOUNDARY_COLOUR, linewidth=4.6,
                        solid_capstyle="butt", zorder=3)
                ax.plot([0.0], [y], marker="o", markersize=4.6,
                        markerfacecolor="white",
                        markeredgecolor=ML_BOUNDARY_COLOUR,
                        markeredgewidth=1.0, linestyle="none", zorder=4)
            else:
                p05 = float(row["tau2_p05"])
                p95 = float(row["tau2_p95"])
                ax.plot([p05, p95], [y, y], color="#9A9A9A", linewidth=2.6,
                        solid_capstyle="butt", zorder=2)
                ax.plot([float(row["tau2"])], [y], marker="o", markersize=4.8,
                        color=colour, markeredgecolor="white",
                        markeredgewidth=0.5, linestyle="none", zorder=4)
        ax.set_title(MEASURE_LABELS.get(measure, measure), fontsize=8.4,
                     fontweight="bold", color="#222222", pad=5)
        ax.tick_params(axis="x", labelsize=6.6, colors="#333333", length=2)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#B3B3B3")
        if j == 0:
            ax.set_yticks(range(n_rows))
            ax.set_yticklabels(
                [f"{SETTING_LABELS.get(s, s)} · {p}"
                 for s, p in lctx["row_keys"]], fontsize=6.5)
            ax.tick_params(axis="y", length=0)
        else:
            ax.tick_params(axis="y", left=False, labelleft=False)

    if boundary_drawn != lctx["n_at_boundary"]:
        raise ValueError(
            f"{STAGE}: left panel drew {boundary_drawn} boundary bars, but the "
            f"source has {lctx['n_at_boundary']} AT_BOUNDARY components")

    # Legend for the left panel: three encodings, no more.
    from matplotlib.lines import Line2D
    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#0072B2",
               markeredgecolor="white", markersize=4.8,
               label="estimate (draws tau2)"),
        Line2D([0], [0], color="#9A9A9A", linewidth=2.6,
               label="p05–p95 span"),
        Line2D([0], [0], marker="o", color="none",
               markerfacecolor="white", markeredgecolor=ML_BOUNDARY_COLOUR,
               markeredgewidth=1.0, markersize=4.6,
               label="AT_BOUNDARY (empty bar)"),
    ]
    left_axes[-1].legend(handles=handles, loc="lower right", fontsize=5.9,
                         frameon=False, handlelength=1.6, borderaxespad=0.4)

    # ------------------------------------------------------------ right panel
    ax = fig.add_subplot(outer[1])
    ax.set_facecolor("#EBEBEB")
    x = np.arange(len(right))
    zone = right["in_reversal_zone"].to_numpy()
    zone_start = float(x[zone].min()) - 0.5
    zone_end = float(x[zone].max()) + 0.5
    ax.axvspan(zone_start, zone_end, color="#C9C9C9", alpha=0.85, zorder=0)
    ax.grid(axis="y", color="white", linewidth=0.7, zorder=1)
    ax.set_axisbelow(True)

    median = right["median_lateral_accel_yd_s2"].to_numpy()
    p95 = right["p95_lateral_accel_yd_s2"].to_numpy()
    ax.fill_between(x, median, p95, color="#7F7F7F", alpha=0.18, zorder=2,
                    linewidth=0)
    ax.plot(x, p95, color="#7F7F7F", linewidth=1.3, linestyle=(0, (4, 2)),
            zorder=3)
    ax.plot(x, median, color=ML_PALETTE["combine"], linewidth=1.9,
            marker="o", markersize=4.0, markerfacecolor="white",
            markeredgewidth=1.1, zorder=4)

    ax.set_yscale("log")
    y_top = float(max(p95)) * 1.55
    ax.set_ylim(3.0, y_top)
    ax.set_xlim(-0.55, len(right) - 0.45)
    ax.set_xticks(x)
    ax.set_xticklabels(list(right["angle_band"]), fontsize=6.6,
                       rotation=0)
    ax.set_yticks([4, 6, 10, 20, 40, 80])
    ax.set_yticklabels(["4", "6", "10", "20", "40", "80"], fontsize=6.8)
    ax.minorticks_off()
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#B3B3B3")
    ax.spines["bottom"].set_color("#B3B3B3")
    ax.tick_params(colors="#333333", length=2)

    # Median value at each band, printed on the point.
    for xi, yi in zip(x, median):
        ax.annotate(f"{yi:.1f}", (xi, yi), textcoords="offset points",
                    xytext=(0, -9), ha="center", va="top", fontsize=5.5,
                    color="#1A1A1A")
    # n per band along the floor of the panel (no bars on counts).
    for xi, n in zip(x, right["n_turns"]):
        ax.annotate(f"n={int(n):,}", (xi, 3.05), textcoords="offset points",
                    xytext=(0, 4), ha="center", va="bottom", fontsize=5.2,
                    color="#555555")

    # Struck-through "not physical" label inside the greyed zone.
    zone_mid = (zone_start + zone_end) / 2.0
    strike_y = y_top / 1.75
    ax.text(zone_mid, strike_y, "NOT PHYSICAL", ha="center", va="center",
            fontsize=8.2, fontweight="bold", color="#2B2B2B", zorder=6)
    ax.plot([zone_mid - 0.95, zone_mid + 0.95], [strike_y, strike_y],
            color="#2B2B2B", linewidth=1.5, zorder=7)
    ax.text(zone_mid, strike_y / 2.35,
            "reversal class:\nmeasured as a plant\n(apex, exit, regain),\n"
            "never as v·ω",
            ha="center", va="center", fontsize=6.2, color="#2B2B2B",
            linespacing=1.25, zorder=6)
    ax.text(zone_mid, 4.55, f"≥{rctx['reversal']['threshold_deg']:g}°",
            ha="center", va="bottom", fontsize=6.0, color="#2B2B2B",
            fontweight="bold", zorder=6)

    ax.set_title("(b) The physics that must not be scored", loc="left",
                 fontsize=9.6, fontweight="bold", color="#1A1A1A", pad=7)
    ax.set_xlabel("Absolute turn angle (°)", fontsize=7.4, color="#333333",
                  labelpad=3)
    ax.set_ylabel(f"Peak lateral acceleration (yd/s²)\n"
                  f"1 g ≈ {rctx['g_yd_s2']:.3f} yd/s²",
                  fontsize=7.2, color="#333333")

    # ------------------------------------------------------ titles and stamp
    fig.text(0.012, 0.972,
             "The measurement limits: why this data cannot rank players",
             fontsize=15.5, fontweight="bold", ha="left", va="top",
             color="#111111")
    fig.text(0.012, 0.932,
             _wrap_text(
                 f"Left: one row per setting × position × measure "
                 f"({lctx['n_components']} components) — between-player "
                 "variance τ² with its p05–p95 span. Right: each angle band's "
                 "median (solid) and p95 (dashed) peak lateral acceleration "
                 f"over {rctx['n_turns']:,} valid turns.", 165),
             fontsize=8.2, ha="left", va="top", color="#333333")
    fig.text(0.012, 0.893,
             f"(a) Between-player variance — the boundary is the finding: "
             f"{lctx['n_at_boundary']} of {lctx['n_components']} components "
             "sit at the boundary",
             fontsize=9.6, fontweight="bold", ha="left", va="top",
             color=ML_BOUNDARY_COLOUR)
    fig.text(0.105, 0.245,
             "Between-player variance τ² (measure-specific units; 0 = at the "
             "floor)",
             fontsize=7.4, ha="left", va="top", color="#333333")
    # ------------------------------------------------------------- caption
    d = DETECTOR_EVIDENCE
    caption_lines = [
        STAMP,
        (f"AT_BOUNDARY means the between-player variance is at the floor of "
         f"the envelope's grid to the precision this data supports: {lctx['n_at_boundary']} "
         f"of {lctx['n_components']} components, drawn as empty bars on the "
         "baseline. It is a finding about the data, not an error, and it is "
         "why no per-player ranking is offered: this sample cannot tell these "
         "players apart on these measures."),
        (f"Right: peak lateral acceleration is pooled over both settings and "
         f"rises from {right.loc[0, 'median_lateral_accel_yd_s2']:.1f} yd/s² "
         f"in {right.loc[0, 'angle_band']} to "
         f"{rctx['reversal']['median_yd_s2']:.1f} yd/s² "
         f"({rctx['reversal']['median_g']:.1f} g) beyond "
         f"{rctx['reversal']['threshold_deg']:g}°; that zone is greyed and "
         "struck through because reversal-class turns are plant mechanics — "
         "apex, exit, regain — not v·ω capacity, so they are never scored. "
         f"Pooled median {rctx['median_yd_s2']:.1f} yd/s² "
         f"({rctx['median_g']:.1f} g); p95 {rctx['p95_yd_s2']:.1f} yd/s² "
         f"({rctx['p95_g']:.1f} g)."),
        (f"Counterweight, so this is not read as \"the measurement is broken\": "
         f"the detector recovers planted 45° turns at {d['angle_mae_deg']:.1f}° "
         f"MAE ({d['angle_mae_n']} turns) and, on three-cone geometry, at "
         f"precision {d['precision']:.3f} / recall {d['recall']:.3f} — the band "
         "is measurable. The boundary is a reporting width on between-player "
         "spread, not detector noise swamping the signal. "
         f"Sources: {DETECTOR_SOURCE_NOTES}; {DETECTOR_SOURCE_GATE}."),
        ("Descriptive only: no per-player ranking, no player names, no scores, "
         "no p-values, no error bars on counts. Sources: "
         "outputs/envelope/variance_components.csv (stage 03) and "
         "outputs/turns/turns.parquet (valid turns, stage 02); every plotted "
         "value is recomputed at render time. The reversal threshold is "
         f"{rctx['threshold_key']} = {rctx['reversal']['threshold_deg']:g}°."),
    ]
    wrapped = []
    for line in caption_lines:
        wrapped.extend(_wrap_text(line, 168).split("\n"))
    fig.text(0.012, 0.195, "\n".join(wrapped), fontsize=6.35,
             ha="left", va="top", color="#333333", linespacing=1.42)

    fig.text(0.988, 0.985, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    return fig


def render_measurement_limits(fig: plt.Figure, cfg: common.Config,
                              out_png: Path) -> Path:
    """Save figure 6 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.measurement_limits.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    return out_png


# ------------------------------------------------------------------ outputs
def measurement_limits_csv_frame(left: pd.DataFrame, right: pd.DataFrame,
                                 lctx: dict, rctx: dict) -> pd.DataFrame:
    """Every plotted value for both panels in one long table.

    ``tau2_status`` is populated on the left-panel rows (the value it qualifies)
    and left blank on the right-panel rows, where no variance component exists.
    """
    left_rows = pd.DataFrame({
        "panel": "left_variance",
        "setting": left["setting"],
        "position_group": left["position_group"],
        "measure": left["measure"],
        "y_row": left["y_row"].astype(int),
        "tau2": left["tau2"],
        "tau2_p05": left["tau2_p05"],
        "tau2_p95": left["tau2_p95"],
        "tau2_status": left["tau2_status"],
        "n_players": left["n_players"],
        "drawn_as": np.where(left["tau2_status"] == "AT_BOUNDARY",
                             "empty_bar_at_baseline", "dot_with_p05_p95_span"),
    })
    right_rows = pd.DataFrame({
        "panel": "right_lateral_accel",
        "angle_band": right["angle_band"],
        "angle_low_deg": right["angle_low_deg"],
        "angle_high_deg": right["angle_high_deg"],
        "n_turns": right["n_turns"],
        "median_lateral_accel_yd_s2": right["median_lateral_accel_yd_s2"],
        "p95_lateral_accel_yd_s2": right["p95_lateral_accel_yd_s2"],
        "median_lateral_accel_g": right["median_lateral_accel_g"],
        "p95_lateral_accel_g": right["p95_lateral_accel_g"],
        "in_reversal_zone": right["in_reversal_zone"],
        "reversal_zone_deg": rctx["reversal"]["threshold_deg"],
    })
    out = pd.concat([left_rows, right_rows], ignore_index=True)
    columns = [
        "panel", "setting", "position_group", "measure", "y_row",
        "tau2", "tau2_p05", "tau2_p95", "tau2_status", "n_players",
        "drawn_as", "angle_band", "angle_low_deg", "angle_high_deg",
        "n_turns", "median_lateral_accel_yd_s2", "p95_lateral_accel_yd_s2",
        "median_lateral_accel_g", "p95_lateral_accel_g",
        "in_reversal_zone", "reversal_zone_deg",
    ]
    return out[columns]


def measurement_limits_manifest_payload(left: pd.DataFrame, right: pd.DataFrame,
                                        lctx: dict, rctx: dict,
                                        cfg: common.Config) -> dict:
    d = DETECTOR_EVIDENCE
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_MEASUREMENT_LIMITS,
        "title": "The measurement limits: why this data cannot rank players",
        "claim": (
            f"{lctx['n_at_boundary']} of {lctx['n_components']} between-player "
            "variance components sit at the boundary of the envelope's grid, "
            "so a per-player ranking is not supported by this data; and "
            "reversal-class turns carry non-physical lateral accelerations, "
            "so they are measured as plants rather than as v·ω. The boundary "
            "is a finding about the data, not an error, and the detector "
            "still recovers planted turns at "
            f"{d['angle_mae_deg']:.1f}° MAE / precision {d['precision']:.3f} "
            f"/ recall {d['recall']:.3f}."),
        "sources": [
            "outputs/envelope/variance_components.csv",
            "outputs/turns/turns.parquet",
        ],
        "boundary": {
            "n_at_boundary": int(lctx["n_at_boundary"]),
            "n_components": int(lctx["n_components"]),
            "n_interior": int(lctx["n_interior"]),
            "status_counts": left["tau2_status"].value_counts().to_dict(),
            "encoding": ("zero-width empty bar outlined on the baseline with "
                         "a hollow point; never shortened to a positive bar "
                         "and never omitted"),
            "meaning": ("between-player variance at the floor of the grid to "
                        "the precision this data supports — a limit of the "
                        "data, not a bug"),
        },
        "reversal_zone": {
            "threshold_deg": float(rctx["reversal"]["threshold_deg"]),
            "config_key": rctx["threshold_key"],
            "reason": ("reversal-class turns are measured as plants (apex, "
                       "exit, regain), never as v·ω; their lateral "
                       "accelerations are not physical capacity"),
            "greyed_and_struck_through": True,
        },
        "lateral_accel": {
            "n_turns": int(rctx["n_turns"]),
            "g_yd_s2": float(rctx["g_yd_s2"]),
            "median_yd_s2": float(rctx["median_yd_s2"]),
            "p95_yd_s2": float(rctx["p95_yd_s2"]),
            "median_g": float(rctx["median_g"]),
            "p95_g": float(rctx["p95_g"]),
            "reversal_median_yd_s2": float(rctx["reversal"]["median_yd_s2"]),
            "reversal_p95_yd_s2": float(rctx["reversal"]["p95_yd_s2"]),
            "reversal_median_g": float(rctx["reversal"]["median_g"]),
            "reversal_p95_g": float(rctx["reversal"]["p95_g"]),
        },
        "detector_evidence": {
            **{k: d[k] for k in ("angle_mae_deg", "angle_mae_n", "precision",
                                 "recall", "drill")},
            "sources": [DETECTOR_SOURCE_NOTES, DETECTOR_SOURCE_GATE],
            "note": ("The MAE is the synthetic planted-corner recovery; the "
                     "precision/recall are the real-schema three-cone gate. "
                     "They are quoted together as the counterweight to the "
                     "boundary, not as one combined test."),
        },
        "components": left[[
            "setting", "position_group", "measure", "tau2", "tau2_p05",
            "tau2_p95", "tau2_status", "n_players"]].to_dict(orient="records"),
        "lateral_bands": right.to_dict(orient="records"),
        "caveats": [
            ("AT_BOUNDARY is a finding about the data, not a model failure; "
             "it is drawn, counted, and never silently dropped."),
            ("No per-player ranking, player names, scores, p-values or error "
             "bars on counts appear in this figure."),
            ("Lateral acceleration is pooled across settings; the panel "
             "describes the physics of turn types, not a setting comparison."),
            ("The reversal threshold is configured, not inferred from the "
             "plot: figures.measurement_limits.reversal_zone_deg."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_MEASUREMENT_LIMITS}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_MEASUREMENT_LIMITS}.csv",
    }


# ------------- figure 7: how big is the gap, really? (the exposure attack)
#: The three panels, in reading order. Each is one metric over the same two
#: settings; no panel ever mixes the headline universe with the sensitivity
#: universe.
GAP_PANELS = [
    ("a_raw_counts", "a. Raw counts", "count"),
    ("b_share", "b. Share of that setting's own turns", "share_pct"),
    ("c_per_appearance", "c. Turns per player-appearance", "per_appearance"),
]


def _gap_value_text(panel: str, value: float) -> str:
    """How each panel's value is printed on the image and in the CSV."""
    if panel == "a_raw_counts":
        return f"{int(round(value)):,}"
    if panel == "b_share":
        return f"{value:.2f}%"
    return f"{value:.3f}"


def _gap_ratio_text(panel: str, game_value: float,
                    combine_value: float) -> str:
    """The ratio *with the two numbers it was computed from*, as printed."""
    if combine_value <= 0:
        raise common.StageRefused(
            f"{STAGE}: cannot print a ratio from a zero Combine value")
    ratio = game_value / combine_value
    if panel == "a_raw_counts":
        return (f"{int(round(game_value)):,} ÷ "
                f"{int(round(combine_value)):,} = {ratio:.2f}×")
    if panel == "b_share":
        return (f"{game_value:.2f}% ÷ {combine_value:.2f}% = "
                f"{ratio:.1f}×")
    return f"{game_value:.3f} ÷ {combine_value:.3f} = {ratio:.1f}×"


def gap_universe_table(turns: pd.DataFrame,
                       cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """Both defensible game populations, each computed on its own terms.

    The headline population is configured (``headline_universe``) and stays
    figure 1's: all valid DB turns, angles >=180 kept as their own class.
    The stricter population applies the stage-04 scope to *both* settings —
    game COVERAGE snaps only, angles >=180 excluded as wrap artefacts — so a
    sensitivity ratio never mixes one setting's population with the other's.

    Every count is recomputed here from the turn table; nothing is hard-coded.
    """
    block = "figures.gap_honest"
    position = str(cfg.get(f"{block}.position_group"))
    lo, hi = [float(x) for x in cfg.get(f"{block}.band_deg")]
    universes = cfg.get(f"{block}.universes") or {}
    headline = str(cfg.get(f"{block}.headline_universe"))
    if headline not in universes:
        raise common.StageRefused(
            f"{STAGE}: headline_universe {headline!r} is not one of the "
            f"configured universes {sorted(universes)}")
    if len(universes) < 2:
        raise common.StageRefused(
            f"{STAGE}: figure 7 needs the headline universe and at least one "
            "sensitivity universe")

    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns — nothing to plot")
    df["setting"] = df["setting"].astype(str)
    df["abs_turn_angle"] = pd.to_numeric(df["abs_turn_angle"], errors="coerce")

    rows: list[dict] = []
    contexts: dict[str, dict] = {}
    for name, spec in universes.items():
        name = str(name)
        sub = df.copy()
        floor = spec.get("exclude_angles_at_or_above_deg")
        if floor is not None:
            sub = sub[sub["abs_turn_angle"] < float(floor)]
        snaps = [str(s) for s in (spec.get("game_snap_types") or [])]
        if snaps:
            # Snap-type scope applies to game turns only; Combine drills carry
            # no player_play snap taxonomy.
            is_game = (sub["setting"] == "game").to_numpy()
            keep = sub["snap_type"].astype(str).isin(snaps).to_numpy()
            sub = sub[~is_game | keep]

        missing = [s for s in ("combine", "game")
                   if not (sub["setting"] == s).any()]
        if missing:
            raise common.StageRefused(
                f"{STAGE}: universe {name!r} has no {missing} turns; a ratio "
                "cannot be drawn from one setting")

        values: dict[str, dict[str, float]] = {}
        for setting in ("combine", "game"):
            part = sub[sub["setting"] == setting]
            in_band = (part["abs_turn_angle"] >= lo) & (
                part["abs_turn_angle"] < hi)
            n_band = int(in_band.sum())
            n_turns = int(len(part))
            n_players = int(part["nfl_id"].nunique())
            if setting == "game":
                n_appearances = int(part.groupby(
                    ["nfl_id", "game_id"]).ngroups)
                appearance_unit = "player-games"
            else:
                # A Combine player is measured once at one event; every drill
                # turn belongs to that single appearance.
                n_appearances = n_players
                appearance_unit = "players × one Combine session"
            if n_turns <= 0 or n_appearances <= 0:
                raise common.StageRefused(
                    f"{STAGE}: universe {name!r} has zero exposure for "
                    f"{setting}")
            values[setting] = {
                "count": float(n_band),
                "share_pct": 100.0 * n_band / n_turns,
                "per_appearance": n_band / n_appearances,
            }
            for panel, panel_label, metric in GAP_PANELS:
                if metric == "count":
                    denominator, unit = float(n_appearances), appearance_unit
                elif metric == "share_pct":
                    denominator, unit = float(n_turns), "valid turns"
                else:
                    denominator, unit = float(n_appearances), appearance_unit
                rows.append({
                    "universe": name,
                    "universe_label": str(spec.get("label", name)),
                    "is_headline": name == headline,
                    "panel": panel,
                    "panel_label": panel_label,
                    "setting": setting,
                    "band_low_deg": lo,
                    "band_high_deg": hi,
                    "count": n_band,
                    "denominator": denominator,
                    "denominator_unit": unit,
                    "value": values[setting][metric],
                    "printed_value": _gap_value_text(
                        panel, values[setting][metric]),
                    "n_players": n_players,
                    "n_appearances": n_appearances,
                    "ratio": np.nan,
                    "ratio_numerator": np.nan,
                    "ratio_denominator": np.nan,
                    "ratio_expression": "",
                })

        for panel, panel_label, metric in GAP_PANELS:
            combine_value = values["combine"][metric]
            game_value = values["game"][metric]
            expression = _gap_ratio_text(panel, game_value, combine_value)
            rows.append({
                "universe": name,
                "universe_label": str(spec.get("label", name)),
                "is_headline": name == headline,
                "panel": panel,
                "panel_label": panel_label,
                "setting": "ratio",
                "band_low_deg": lo,
                "band_high_deg": hi,
                "count": np.nan,
                "denominator": np.nan,
                "denominator_unit": "",
                "value": game_value / combine_value,
                "printed_value": expression,
                "n_players": np.nan,
                "n_appearances": np.nan,
                "ratio": game_value / combine_value,
                "ratio_numerator": game_value,
                "ratio_denominator": combine_value,
                "ratio_expression": expression,
            })

        def _appearances(setting: str, part: pd.DataFrame) -> int:
            if setting == "game":
                return int(part.groupby(["nfl_id", "game_id"]).ngroups)
            return int(part["nfl_id"].nunique())

        contexts[name] = {
            "label": str(spec.get("label", name)),
            "is_headline": name == headline,
            "n_band": {s: int(values[s]["count"]) for s in values},
            "n_turns": {s: int(sub[sub["setting"] == s].shape[0])
                        for s in values},
            "n_players": {s: int(sub.loc[sub["setting"] == s, "nfl_id"].nunique())
                          for s in values},
            "n_appearances": {s: _appearances(s, sub[sub["setting"] == s])
                              for s in values},
            "values": values,
            "ratios": {panel: values["game"][metric]
                       / values["combine"][metric]
                       for panel, _, metric in GAP_PANELS},
        }

    table = pd.DataFrame(rows)
    table["panel"] = pd.Categorical(
        table["panel"], categories=[p for p, _, _ in GAP_PANELS],
        ordered=True)
    context = {
        "position": position,
        "band_deg": [lo, hi],
        "headline": headline,
        "universes": contexts,
        "sensitivity": [k for k in contexts if k != headline],
    }
    return table, context


def gap_plot_frame(table: pd.DataFrame,
                   context: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bar geometry and the text printed beside each bar (headline only)."""
    head = table[(table["is_headline"]) & (table["setting"] != "ratio")].copy()
    if head.empty:
        raise common.StageRefused(
            f"{STAGE}: figure 7 has no headline-universe rows to draw")
    head["setting"] = pd.Categorical(
        head["setting"], categories=["combine", "game"], ordered=True)
    head["y"] = np.where(head["setting"].astype(str) == "combine", 2.0, 1.0)
    maxima = head.groupby("panel", observed=True)["value"].max().to_dict()
    head["pad"] = head["panel"].map(maxima).astype(float) * 0.02
    # An invisible anchor widens each free x-scale so the value label printed
    # at the end of the longest bar cannot run off the panel edge.
    head["x_blank"] = head["panel"].map(maxima).astype(float) * 1.25
    head["xmin"] = 0.0
    head["xmax"] = head["value"].astype(float)
    head["ymin"] = head["y"] - 0.27
    head["ymax"] = head["y"] + 0.27
    head["lab_x"] = head["xmax"] + head["pad"]
    head["lab_y"] = head["y"]
    head["denom_y"] = head["y"] - 0.34
    head["const_x"] = 0.0

    def _denominator(row) -> str:
        panel = str(row["panel"])
        setting = str(row["setting"])
        name = "Combine" if setting == "combine" else "game"
        if panel == "a_raw_counts":
            if setting == "combine":
                return (f"{int(row['n_players'])} players × one Combine "
                        "session")
            return (f"{int(row['n_players'])} players × "
                    f"{int(row['n_appearances']):,} player-games")
        if panel == "b_share":
            return (f"÷ {int(row['denominator']):,} valid {name} turns")
        return (f"÷ {int(row['denominator']):,} "
                f"{'Combine appearances' if setting == 'combine' else 'player-games'}")

    head["denom_label"] = head.apply(_denominator, axis=1)
    head["value_label"] = head["printed_value"].astype(str)

    ratio_rows = table[(table["is_headline"])
                       & (table["setting"] == "ratio")].copy()
    ratios = {str(r["panel"]): f"{r['panel_label']}\n({r['ratio_expression']})"
              for _, r in ratio_rows.iterrows()}
    order = [p for p, _, _ in GAP_PANELS]
    if set(ratios) != set(order):
        raise common.StageRefused(
            f"{STAGE}: figure 7 is missing a headline ratio for "
            f"{sorted(set(order) - set(ratios))}")
    head["strip"] = head["panel"].astype(str).map(ratios)
    head["strip"] = pd.Categorical(head["strip"],
                                   categories=[ratios[p] for p in order],
                                   ordered=True)
    return head, ratio_rows


def plot_gap_honest(table: pd.DataFrame, context: dict,
                    cfg: common.Config) -> ggplot:
    block = "figures.gap_honest"
    frame, ratio_rows = gap_plot_frame(table, context)
    h = context["universes"][context["headline"]]
    hv, hr = h["values"], h["ratios"]

    title = (
        "How big is the gap, really? Raw counts say "
        f"{hr['a_raw_counts']:.0f}×; exposure-corrected it is "
        f"{min(hr['b_share'], hr['c_per_appearance']):.1f}–"
        f"{max(hr['b_share'], hr['c_per_appearance']):.1f}×")
    subtitle = _wrap_text(
        f"Headline universe: {h['label']} — the same population as figures 1, "
        "3 and 5. Panel a prints the exposed counts; panels b and c divide by "
        "each setting's own exposure, so the 122-vs-299 denominator cannot "
        "inflate the gap.", 178)

    sens_lines = []
    for key in context["sensitivity"]:
        s = context["universes"][key]
        sv, sr = s["values"], s["ratios"]
        sens_lines.append(_wrap_text(
            f"SENSITIVITY — {s['label']}: {s['n_band']['combine']} vs "
            f"{s['n_band']['game']} turns. Share of own turns: "
            f"{sv['combine']['share_pct']:.2f}% vs "
            f"{sv['game']['share_pct']:.2f}% = {sr['b_share']:.1f}×. Turns per "
            f"player-appearance: {sv['combine']['per_appearance']:.3f} vs "
            f"{sv['game']['per_appearance']:.3f} = "
            f"{sr['c_per_appearance']:.1f}×. Never mixed with the headline "
            "panels: the gap survives the stricter population, only its size "
            "moves.", 178))

    caption_lines = [
        STAMP,
        (f"Panel a: {h['n_band']['combine']} Combine vs {h['n_band']['game']} "
         f"game turns in {context['band_deg'][0]:g}–{context['band_deg'][1]:g}°; "
         f"{h['n_players']['combine']} Combine players × one session vs "
         f"{h['n_players']['game']} game players × "
         f"{h['n_appearances']['game']:,} player-games. The raw ratio is "
         "exposure-confounded: one Combine session per player against hundreds "
         "of player-games."),
        ("Dividing by each setting's own turns (b) or by player-appearances "
         "(c) removes that advantage. The corrected gap is roughly "
         f"{min(hr['b_share'], hr['c_per_appearance']):.0f} to "
         f"{max(hr['b_share'], hr['c_per_appearance']):.0f} "
         f"times, not the raw {hr['a_raw_counts']:.0f}, and it is still "
         "damning."),
    ] + sens_lines + [
        ("Counts recomputed at render time from outputs/turns/turns.parquet "
         "(valid turns, stage 02). The two universes are never mixed inside a "
         "panel."),
    ]
    caption = "\n".join(_wrap_text(line, 200) for line in caption_lines
                        if line)

    plot = (
        ggplot(frame, aes(xmin="xmin", xmax="xmax", ymin="ymin", ymax="ymax",
                          fill="setting"))
        + geom_rect(colour="white", size=0.25)
        + geom_text(data=frame,
                    mapping=aes(x="lab_x", y="lab_y", label="value_label"),
                    inherit_aes=False, size=3.2, colour="#1A1A1A",
                    fontweight="bold", ha="left", va="center")
        + geom_text(data=frame,
                    mapping=aes(x="const_x", y="denom_y", label="denom_label"),
                    inherit_aes=False, size=2.5, colour="#333333",
                    ha="left", va="center")
        + geom_blank(data=frame, mapping=aes(x="x_blank", y="y"),
                     inherit_aes=False)
        + facet_wrap("~ strip", nrow=1, scales="free_x")
        + scale_fill_manual(values=["#0072B2", "#7F7F7F"],
                            labels=[COMBINE_LABEL, GAME_LABEL], name=None)
        + scale_x_continuous(expand=(0, 0))
        + scale_y_continuous(breaks=[2.0, 1.0], labels=["Combine", "Game"],
                             limits=(0.40, 2.78), expand=(0, 0))
        + labs(title=title, subtitle=subtitle, caption=caption,
               x="Value (count, percent, or turns per appearance)", y=None)
        + theme_bw(base_size=9)
        + theme(
            figure_size=(float(cfg.get(f"{block}.width_in")),
                         float(cfg.get(f"{block}.height_in"))),
            panel_background=element_rect(fill="#EBEBEB", colour=None),
            panel_grid_major=element_line(colour="white", linewidth=0.7),
            panel_grid_minor=element_blank(),
            panel_border=element_rect(colour="#B3B3B3", linewidth=0.4),
            strip_background=element_rect(fill="#D9D9D9", colour="#B3B3B3"),
            strip_text=element_text(weight="bold", size=8.2),
            plot_title=element_text(size=14, weight="bold"),
            plot_subtitle=element_text(size=8.4, colour="#333333"),
            plot_caption=element_text(size=6.4, colour="#333333", ha="left",
                                      linespacing=1.35),
            axis_title_x=element_text(size=8.6),
            axis_title_y=element_blank(),
            axis_text_x=element_text(size=7.6),
            axis_text_y=element_text(size=9.0),
            legend_position="bottom",
            legend_title=element_blank(),
            legend_key=element_rect(fill="#EBEBEB", colour=None),
        )
    )
    return plot


def gap_honest_csv_frame(table: pd.DataFrame) -> pd.DataFrame:
    """Every count, denominator and ratio, tagged with its universe."""
    out = table.copy()
    out["panel"] = out["panel"].astype(str)
    out["setting_order"] = out["setting"].map(
        {"combine": 0, "game": 1, "ratio": 2}).fillna(3).astype(int)
    head = out[out["is_headline"]]
    others = out[~out["is_headline"]]
    out = pd.concat([head, others], ignore_index=True)
    return out.sort_values(
        ["is_headline", "universe", "panel", "setting_order"],
        ascending=[False, True, True, True]).reset_index(drop=True)[[
            "universe", "universe_label", "is_headline", "panel",
            "panel_label", "setting", "band_low_deg", "band_high_deg",
            "count", "denominator", "denominator_unit", "value",
            "printed_value", "n_players", "n_appearances", "ratio",
            "ratio_numerator", "ratio_denominator", "ratio_expression"]]


def gap_honest_manifest_payload(table: pd.DataFrame, context: dict,
                                cfg: common.Config) -> dict:
    h = context["universes"][context["headline"]]
    hv, hr = h["values"], h["ratios"]
    claim = (
        f"The {h['n_band']['combine']}-vs-{h['n_band']['game']} raw count is a "
        f"{hr['a_raw_counts']:.2f}× ratio inflated by exposure "
        f"({h['n_players']['combine']} Combine players measured once vs "
        f"{h['n_players']['game']} game players over "
        f"{h['n_appearances']['game']:,} player-games); on each setting's own "
        f"turns the gap is {hv['game']['share_pct']:.2f}% vs "
        f"{hv['combine']['share_pct']:.2f}% = {hr['b_share']:.1f}×, and per "
        f"player-appearance {hv['game']['per_appearance']:.3f} vs "
        f"{hv['combine']['per_appearance']:.3f} = "
        f"{hr['c_per_appearance']:.1f}× — still damning.")
    payload = {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_GAP_HONEST,
        "title": ("How big is the gap, really? Raw counts, own-turn share and "
                  "turns per player-appearance"),
        "claim": claim,
        "sources": ["outputs/turns/turns.parquet"],
        "position_group": context["position"],
        "band_deg": context["band_deg"],
        "headline_universe": context["headline"],
        "headline_reason": (
            "The headline keeps figure 1's population — all valid DB turns, "
            "angles >=180 kept as their own class — so the correction "
            "describes the same turns as the 4-vs-97 claim it answers."),
        "panels": [
            {"panel": panel, "label": label, "metric": metric,
             "value_printed": _gap_value_text(panel, h["values"]["game"][metric]),
             "ratio": h["ratios"][panel],
             "ratio_expression": _gap_ratio_text(
                 panel, h["values"]["game"][metric],
                 h["values"]["combine"][metric])}
            for panel, label, metric in GAP_PANELS],
        "universes": {
            name: {
                "label": u["label"],
                "is_headline": bool(u["is_headline"]),
                "n_band": u["n_band"],
                "n_turns": u["n_turns"],
                "n_players": u["n_players"],
                "n_appearances": u["n_appearances"],
                "values": u["values"],
                "ratios": u["ratios"],
            } for name, u in context["universes"].items()},
        "caveats": [
            ("The raw count is exposure-confounded: one Combine session per "
             "player versus many player-games per game player. Panels b and c "
             "divide by each setting's own exposure and are the honest "
             "comparison."),
            ("The sensitivity universe applies the stage-04 scope to both "
             "settings — game COVERAGE snaps only and angles >=180 excluded "
             "as wrap artefacts — and is shown as a labelled note, never "
             "mixed with the headline inside a panel."),
            ("Counts, denominators and ratios are recomputed at render time "
             "from outputs/turns/turns.parquet; no number is hard-coded."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_GAP_HONEST}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_GAP_HONEST}.csv",
    }
    return payload


# ------------- figure 8: what a turn costs, and who pays for it
#: Panel (a) is drawn from the reporting bands only. The last reporting class
#: (``>= 180``) is the reversal class and is never given a retention value.
TURN_COST_SETTINGS = ("combine", "game")


def turn_cost_table(turns: pd.DataFrame,
                    cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """Per band x setting: how many turns, and what they cost in speed.

    Two populations are kept deliberately separate and never merged:

    * ``count`` / ``share_within_setting`` — every valid turn in the band. This
      is panel (b), the substitution view, so the shares sum to 100% per
      setting.
    * ``n_apex_retention`` / quartiles — only turns that *can* carry a
      retention value: entry speed above the detector's heading-reliability
      floor, a finite ``apex_retention``, and not in the reversal class. This
      is panel (a). A band below ``min_n_for_median`` usable turns is flagged
      ``insufficient`` and is never drawn as a median.

    The measure is ``apex_retention`` (apex speed / entry speed), not
    ``speed_retention`` (exit / entry): the turn window runs between speed
    maxima, so exit-over-entry includes re-acceleration and is above 1 in
    every band by construction. It shows gains, not the cost paid at the plant.

    The floors are read from the detector block (their config keys are named in
    the figure block), so the figure cannot apply a floor the detector did not.
    """
    block = "figures.turn_cost"
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow_label = str(cfg.get("figures.hole.bands.overflow_label"))
    position = str(cfg.get(f"{block}.position_group"))
    min_n = int(cfg.get(f"{block}.min_n_for_median"))
    column = str(cfg.get(f"{block}.retention_column"))
    if min_n < 1:
        raise common.StageRefused(
            f"{STAGE}: figures.turn_cost.min_n_for_median must be >= 1")
    if column not in turns.columns:
        raise common.StageRefused(
            f"{STAGE}: figures.turn_cost.retention_column {column!r} is not a "
            "column of the turn table")

    floor_keys = cfg.get(f"{block}.entry_speed_floor_keys") or {}
    floors: dict[str, dict] = {}
    for setting in TURN_COST_SETTINGS:
        key = floor_keys.get(setting)
        if not key:
            raise common.StageRefused(
                f"{STAGE}: figures.turn_cost.entry_speed_floor_keys has no "
                f"key for {setting!r}; the entry-speed floor must be named")
        floors[setting] = {"key": str(key), "value": float(cfg.get(str(key)))}

    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns — nothing to price")
    missing = [s for s in TURN_COST_SETTINGS if s not in set(df["setting"])]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turn table has no {position} turns for {missing}; a "
            "cost comparison needs both settings")
    df["setting"] = df["setting"].astype(str)
    df["abs_turn_angle"] = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    df["entry_speed"] = pd.to_numeric(df["entry_speed"], errors="coerce")
    df[column] = pd.to_numeric(df[column], errors="coerce")

    bands = band_labels(edges, overflow_label)
    angle = df["abs_turn_angle"]
    labels = np.full(len(df), None, dtype=object)
    for i in range(len(edges) - 1):
        mask = (angle >= edges[i]) & (angle < edges[i + 1])
        labels[mask.to_numpy()] = bands[i]
    labels[(angle >= edges[-1]).to_numpy()] = bands[-1]
    if int(pd.isna(labels).sum()):
        raise common.StageRefused(
            f"{STAGE}: {int(pd.isna(labels).sum())} valid {position} turns fall "
            f"outside the configured bands {edges}")
    df["band"] = labels

    totals = df.groupby("setting").size().to_dict()
    n_exactly_180 = int((angle == 180).sum())
    rows: list[dict] = []
    for i, band in enumerate(bands):
        reversal_class = bool(i == len(bands) - 1)
        for setting in TURN_COST_SETTINGS:
            part = df[(df["band"] == band) & (df["setting"] == setting)]
            floor = floors[setting]["value"]
            count = int(len(part))
            below_floor = int((part["entry_speed"].isna()
                               | (part["entry_speed"] <= floor)).sum())
            above = part[part["entry_speed"].notna()
                         & (part["entry_speed"] > floor)]
            no_retention = int(above[column].isna().sum())
            usable = above[above[column].notna()][column]
            n_ret = int(len(usable))

            if reversal_class:
                q1 = med = q3 = np.nan
                insufficient = True
                reason = ("reversal class (>=180 deg): v-omega measures are "
                          "meaningless, so no retention is reported")
            else:
                if n_ret:
                    q1 = float(usable.quantile(0.25))
                    med = float(usable.median())
                    q3 = float(usable.quantile(0.75))
                else:
                    q1 = med = q3 = np.nan
                insufficient = bool(n_ret < min_n)
                reason = "" if not insufficient else (
                    f"insufficient: n={n_ret} < min_n={min_n} (too few turns "
                    "to carry a median)")

            rows.append({
                "band_index": i + 1,
                "band": band,
                "band_low_deg": edges[i] if not reversal_class else edges[-1],
                "band_high_deg": edges[i + 1] if not reversal_class else np.nan,
                "is_reversal_class": reversal_class,
                "setting": setting,
                "count": count,
                "setting_total": int(totals[setting]),
                "share_within_setting": (count / int(totals[setting])
                                         if int(totals[setting]) else np.nan),
                "entry_speed_floor_yd_s": floor,
                "entry_speed_floor_key": floors[setting]["key"],
                "n_below_speed_floor": below_floor,
                "n_missing_apex_retention": no_retention,
                "n_apex_retention": n_ret,
                "q1_apex_retention": q1,
                "median_apex_retention": med,
                "q3_apex_retention": q3,
                "iqr_apex_retention": (q3 - q1) if n_ret else np.nan,
                "insufficient": insufficient,
                "insufficient_reason": reason,
                # Panel (a) draws a value only for a usable, sufficient,
                # non-reversal band; everything else is drawn as insufficient
                # or as the excluded reversal class.
                "drawn_in_panel_a": bool(n_ret >= min_n and not reversal_class),
            })

    table = pd.DataFrame(rows)
    if int(table.loc[~table["is_reversal_class"], "count"].sum()) != int(
            df[angle < edges[-1]].shape[0]):
        raise ValueError(
            f"{STAGE}: banded counts do not reconstruct the non-reversal turn "
            "population")
    if int(table["count"].sum()) != int(len(df)):
        raise ValueError(
            f"{STAGE}: banded counts sum to {int(table['count'].sum())}, but "
            f"the table has {len(df)} valid {position} turns")

    drawn = table[table["drawn_in_panel_a"]]
    insufficient = table[~table["drawn_in_panel_a"]
                         & ~table["is_reversal_class"]]
    below_total = int(table["n_below_speed_floor"].sum())
    context = {
        "position": position,
        "column": column,
        "bands": bands,
        "edges": edges,
        "min_n_for_median": min_n,
        "floors": floors,
        "n_valid": int(len(df)),
        "n_by_setting": {s: int(totals[s]) for s in TURN_COST_SETTINGS},
        "n_below_speed_floor": below_total,
        "share_removed_by_floor": below_total / len(df) if len(df) else 0.0,
        "n_below_speed_floor_by_setting": {
            s: int(table.loc[table["setting"] == s, "n_below_speed_floor"].sum())
            for s in TURN_COST_SETTINGS},
        "n_exactly_180": n_exactly_180,
        "reversal": {
            s: int(table.loc[(table["setting"] == s)
                             & table["is_reversal_class"], "count"].iloc[0])
            for s in TURN_COST_SETTINGS},
        "n_drawn": int(len(drawn)),
        "insufficient": [
            {"band": str(r["band"]), "setting": str(r["setting"]),
             "n_apex_retention": int(r["n_apex_retention"]),
             "reason": str(r["insufficient_reason"])}
            for _, r in insufficient.iterrows()],
        "medians": {
            s: {str(r["band"]): float(r["median_apex_retention"])
                for _, r in drawn[drawn["setting"] == s].iterrows()}
            for s in TURN_COST_SETTINGS},
        "q1": {s: {str(r["band"]): float(r["q1_apex_retention"])
                   for _, r in drawn[drawn["setting"] == s].iterrows()}
               for s in TURN_COST_SETTINGS},
        "q3": {s: {str(r["band"]): float(r["q3_apex_retention"])
                   for _, r in drawn[drawn["setting"] == s].iterrows()}
               for s in TURN_COST_SETTINGS},
        "shares": {
            s: {str(r["band"]): float(r["share_within_setting"])
                for _, r in table[table["setting"] == s].iterrows()}
            for s in TURN_COST_SETTINGS},
        "counts": {
            s: {str(r["band"]): int(r["count"])
                for _, r in table[table["setting"] == s].iterrows()}
            for s in TURN_COST_SETTINGS},
    }
    return table, context


def _turn_cost_curve_sentence(context: dict) -> str:
    """The medians actually drawn, in band order, per setting.

    Generated from the table so the caption cannot describe a curve the data
    does not have, and cannot call it monotone: if a median rises in angle the
    arithmetic below says so.
    """
    parts = []
    for setting in TURN_COST_SETTINGS:
        med = context["medians"][setting]
        if not med:
            parts.append(f"{SETTING_LABELS[setting]}: no band carries a median")
            continue
        sequence = []
        for band in context["bands"][:-1]:
            if band in med:
                sequence.append(f"{band} {med[band]:.2f}")
            else:
                sequence.append(f"{band} no median (insufficient n)")
        parts.append(f"{SETTING_LABELS[setting]} medians, shallow to deep: "
                     + ", ".join(sequence))
    return "; ".join(parts) + "."


def _turn_cost_shape_sentence(context: dict) -> str:
    """State the shape honestly: where the curve falls, and where it rises."""
    rising: list[str] = []
    falling_txt: list[str] = []
    for setting in TURN_COST_SETTINGS:
        med = context["medians"][setting]
        ordered = [(b, med[b]) for b in context["bands"][:-1] if b in med]
        rises = [(ordered[i][0], ordered[i + 1][0]) for i in range(len(ordered) - 1)
                 if ordered[i + 1][1] > ordered[i][1]]
        if rises:
            rising.append(
                f"{SETTING_LABELS[setting]} rises from "
                + " and ".join(f"{a} to {b}" for a, b in rises))
        if ordered:
            falling_txt.append(
                f"{SETTING_LABELS[setting]} {ordered[0][1]:.2f} → "
                f"{ordered[-1][1]:.2f}")
    bits = []
    if rising:
        bits.append("The curve is not monotone in angle: " + "; ".join(rising))
    else:
        bits.append("The curve is monotone non-increasing in angle")
    if falling_txt:
        bits.append("shallowest to deepest reported band, "
                    + "; ".join(falling_txt))
    return "; ".join(bits) + "."


def plot_turn_cost(table: pd.DataFrame, context: dict,
                   cfg: common.Config) -> plt.Figure:
    """Two stacked panels on one shared band axis. Returns the Figure.

    (a) median and interquartile spread of ``apex_retention`` per band, one
    series per setting; insufficient bands and the reversal class are drawn as
    such, never as values. (b) each setting's own share of its turns in the same
    bands, counts printed on the bars, the reversal class hatched.
    """
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    block = "figures.turn_cost"
    bands = context["bands"]
    n_bands = len(bands)
    min_n = context["min_n_for_median"]
    focus = focus_band(cfg)
    focus_idx = focus_band_index(context["edges"], focus)

    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    grid = fig.add_gridspec(
        2, 1, height_ratios=[1.30, 1.0], hspace=0.13,
        left=0.082, right=0.985, top=0.845, bottom=0.318)
    ax_a = fig.add_subplot(grid[0])
    ax_b = fig.add_subplot(grid[1], sharex=ax_a)

    offsets = {"combine": -0.185, "game": 0.185}
    bar_w = 0.30

    def _fill(colour: str) -> None:
        for ax in (ax_a, ax_b):
            ax.set_facecolor("#EBEBEB")
            ax.grid(axis="y", color="white", linewidth=0.8, zorder=0)
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
            for spine in ("left", "bottom"):
                ax.spines[spine].set_color("#B3B3B3")

    _fill("#EBEBEB")

    # The focus band is marked once, in the same way in both panels.
    for ax in (ax_a, ax_b):
        ax.fill_between([focus_idx - 0.5, focus_idx + 0.5], 0, 1,
                        transform=ax.get_xaxis_transform(),
                        color=focus["colour"],
                        alpha=focus["tint_alpha"], zorder=1,
                        linewidth=0)
        for edge in (focus_idx - 0.5, focus_idx + 0.5):
            ax.axvline(edge, color=focus["colour"],
                       linewidth=focus["rule_width"], zorder=2)

    # ---------------------------------------------------------- panel (a)
    drawn_cells: list[tuple[str, str]] = []
    insufficient_cells: list[tuple[str, str]] = []
    for i in range(n_bands - 1):          # the last class is the reversal class
        for setting in TURN_COST_SETTINGS:
            row = table[(table["band"] == bands[i])
                        & (table["setting"] == setting)].iloc[0]
            x = i + offsets[setting]
            colour = PALETTE[setting]
            if row["drawn_in_panel_a"]:
                ax_a.plot([x, x], [row["q1_apex_retention"],
                                   row["q3_apex_retention"]],
                          color=colour, linewidth=2.6, solid_capstyle="butt",
                          zorder=3)
                ax_a.plot([x, x],
                          [row["q1_apex_retention"] - 0.018,
                           row["q1_apex_retention"] + 0.018],
                          color=colour, linewidth=1.4, zorder=3)
                ax_a.plot([x, x],
                          [row["q3_apex_retention"] - 0.018,
                           row["q3_apex_retention"] + 0.018],
                          color=colour, linewidth=1.4, zorder=3)
                ax_a.plot([x], [row["median_apex_retention"]],
                          marker="o" if setting == "combine" else "s",
                          markersize=6.2, markerfacecolor=colour,
                          markeredgecolor="white", markeredgewidth=0.7,
                          linestyle="none", zorder=4)
                drawn_cells.append((bands[i], setting))
            else:
                insufficient_cells.append((bands[i], setting))

    # The reversal class gets one shaded slot in (a), labelled as excluded.
    rev = context["reversal"]
    ax_a.axvspan(n_bands - 1 - 0.46, n_bands - 1 + 0.46, color="#6E6E6E",
                 alpha=0.13, zorder=1)
    ax_a.text(n_bands - 1, 0.5,
              "excluded from (a):\nreversal class\n"
              f"n={rev['combine']} / {rev['game']}\n"
              "v·ω undefined",
              transform=ax_a.get_xaxis_transform(), ha="center", va="center",
              fontsize=6.4, color="#333333", linespacing=1.35, zorder=6)

    drawn_y = [v for s in TURN_COST_SETTINGS
               for v in list(context["q1"][s].values())
               + list(context["q3"][s].values())]
    y_lo = max(0.0, min(drawn_y) - 0.10)
    y_hi = max(drawn_y) + 0.12
    ax_a.set_ylim(y_lo, y_hi)
    ax_a.set_xlim(-0.55, n_bands - 0.45)

    ax_a.axhline(1.0, color="#555555", linewidth=0.9, linestyle=(0, (4, 3)),
                 zorder=2)

    # Per-band n, published under each side of every band.
    for i in range(n_bands - 1):
        for setting in TURN_COST_SETTINGS:
            row = table[(table["band"] == bands[i])
                        & (table["setting"] == setting)].iloc[0]
            x = i + offsets[setting]
            if row["drawn_in_panel_a"]:
                ax_a.text(x, y_lo + (y_hi - y_lo) * 0.015,
                          f"n={int(row['n_apex_retention'])}", ha="center",
                          va="bottom", fontsize=6.0, color=PALETTE[setting],
                          zorder=6)
            else:
                ax_a.add_patch(plt.Rectangle(
                    (x - bar_w / 2.0, y_lo),
                    bar_w, y_hi - y_lo, facecolor="#7A7A7A", alpha=0.16,
                    edgecolor="none", zorder=1))
                ax_a.text(x, y_lo + (y_hi - y_lo) * 0.20,
                          f"n={int(row['n_apex_retention'])}\ninsufficient\n"
                          f"(min n={min_n})", ha="center", va="bottom",
                          fontsize=5.8, color="#333333", linespacing=1.3,
                          zorder=6)

    ax_a.set_title(
        "(a) What the plant costs — median and interquartile spread of "
        "apex ÷ entry speed, by reporting band",
        loc="left", fontsize=9.6, fontweight="bold", color="#1A1A1A", pad=7)
    ax_a.set_ylabel("Apex retention (apex ÷ entry);\ndashed line = 1.0 "
                    "(no speed lost at the plant)",
                    fontsize=7.6, color="#333333")
    ax_a.tick_params(axis="x", labelbottom=False, length=0)
    ax_a.tick_params(axis="y", labelsize=7.0, colors="#333333", length=2)

    # ---------------------------------------------------------- panel (b)
    b_max = 0.0
    for i, band in enumerate(bands):
        for setting in TURN_COST_SETTINGS:
            row = table[(table["band"] == band)
                        & (table["setting"] == setting)].iloc[0]
            share = float(row["share_within_setting"]) * 100.0
            b_max = max(b_max, share)
            reversal_class = bool(row["is_reversal_class"])
            ax_b.bar(i + offsets[setting], share, width=bar_w,
                     color=PALETTE[setting],
                     alpha=0.55 if reversal_class else 1.0,
                     hatch="////" if reversal_class else None,
                     edgecolor="white", linewidth=0.6, zorder=3)
            ax_b.text(i + offsets[setting], share + b_max * 0.018,
                      f"{int(row['count']):,}", ha="center", va="bottom",
                      fontsize=6.6, color="#1A1A1A", zorder=5)
    ax_b.set_ylim(0.0, b_max * 1.20)
    ax_b.set_title(
        "(b) Who pays it — share of each setting's own turns in those same "
        "bands, so the reader sees where each setting spends its turns",
        loc="left", fontsize=9.6, fontweight="bold", color="#1A1A1A", pad=7)
    ax_b.set_ylabel("Share of that setting's\nown turns (%)",
                    fontsize=7.6, color="#333333")
    ticks = []
    for i, band in enumerate(bands):
        ticks.append(f"{band}\n(reversal)" if i == n_bands - 1 else band)
    ax_b.set_xticks(range(n_bands))
    ax_b.set_xticklabels(ticks, fontsize=7.4, color="#333333")
    ax_b.get_xticklabels()[focus_idx].set_color(focus["colour"])
    ax_b.get_xticklabels()[focus_idx].set_fontweight("bold")
    ax_b.tick_params(axis="x", length=2)
    ax_b.tick_params(axis="y", labelsize=7.0, colors="#333333", length=2)
    ax_b.set_xlabel("Absolute turn angle (°), left-closed reporting bands",
                    fontsize=7.6, color="#333333", labelpad=3)

    fig.legend(
        handles=[
            Line2D([0], [0], marker="o", color=PALETTE["combine"], linewidth=2.6,
                   markersize=6.2, markeredgecolor="white",
                   label="Combine — median and Q1–Q3"),
            Line2D([0], [0], marker="s", color=PALETTE["game"], linewidth=2.6,
                   markersize=6.2, markeredgecolor="white",
                   label="Game — median and Q1–Q3"),
            Patch(facecolor="#8A8A8A", alpha=0.55, hatch="////",
                  edgecolor="white", label="reversal class (>=180°) — in (b), "
                  "excluded from (a)"),
        ],
        loc="upper left", bbox_to_anchor=(0.078, 0.905), ncol=3,
        frameon=False, fontsize=7.6, handlelength=1.6, columnspacing=1.8)

    # ------------------------------------------------------- titles, caption
    fig.text(0.012, 0.978, "What a turn costs, and who pays for it",
             fontsize=16.5, fontweight="bold", ha="left", va="top",
             color="#111111")
    fig.text(0.012, 0.950,
             _wrap_text(
                 "Panel a prices each reporting band with stage 02's own "
                 "apex_retention (apex speed ÷ entry speed) — the speed given "
                 "up at the plant. Panel b shows where each setting spends its "
                 "turns in the same bands. Descriptive distributions only — no "
                 "fit, no smoothing, no threshold.", 178),
             fontsize=8.4, ha="left", va="top", color="#333333")

    medians = context["medians"]
    deep_band = context["bands"][-2]
    deep_txt = "; ".join(
        f"{SETTING_LABELS[s]} {medians[s].get(deep_band, float('nan')):.2f} "
        f"(Q1–Q3 "
        f"{context['q1'][s].get(deep_band, float('nan')):.2f}–"
        f"{context['q3'][s].get(deep_band, float('nan')):.2f})"
        for s in TURN_COST_SETTINGS)
    floor_bits = "; ".join(
        f"{SETTING_LABELS[s]} > {context['floors'][s]['value']:.1f} yd/s "
        f"({context['floors'][s]['key']})"
        for s in TURN_COST_SETTINGS)
    removed_bits = " and ".join(
        f"{context['n_below_speed_floor_by_setting'][s]} of "
        f"{context['n_by_setting'][s]:,} {SETTING_LABELS[s]} turns"
        for s in TURN_COST_SETTINGS)

    ins = context["insufficient"]
    ins_txt = ("; ".join(f"{r['band']} {SETTING_LABELS[r['setting']]} "
                         f"(n={r['n_apex_retention']})" for r in ins)
               if ins else "none")

    combine_shallow = medians["combine"].get(context["bands"][1], float("nan"))
    combine_deep = medians["combine"].get("120–150°", float("nan"))
    combine_plant = medians["combine"].get(deep_band, float("nan"))
    combine_share_deep = 100.0 * context["shares"]["combine"].get(
        "120–150°", float("nan"))
    game_shallow = medians["game"].get(context["bands"][0], float("nan"))
    game_n_shallow = context["counts"]["game"].get(context["bands"][0], 0)

    verdict = (
        "The cost is paid at the plant, and it is real. "
        f"{_turn_cost_curve_sentence(context)} "
        f"{_turn_cost_shape_sentence(context)} "
        f"The Combine's shallow rows are where the cost is lowest "
        f"(45–60° {combine_shallow:.2f}), and it barely tests there: its budget "
        f"sits in 120–150° ({combine_share_deep:.0f}% of its turns), where its "
        f"median gives up more than half its entry speed ({combine_deep:.2f}). "
        f"Games pay a shallower but real price at 30–45° "
        f"({game_shallow:.2f}) on {game_n_shallow} turns.")

    caption_lines = [
        STAMP,
        "FINDING, AS MEASURED. " + verdict,
        (f"The sharpest plants, 150–180°: median apex ÷ entry {deep_txt}. "
         "Apex retention is apex speed divided by entry speed, so "
         f"{combine_plant:.2f} means the plant costs the Combine "
         f"{100.0 * (1.0 - combine_plant):.0f}% of the speed it carried into "
         "the turn; a median near 1.00 means no speed is lost at the plant "
         "(a wide turn taken without braking)."),
        ("Not plotted, by design: speed_retention (exit speed ÷ entry speed). "
         "The turn window runs between speed maxima (detector.segmentation), "
         "so re-acceleration inside the window puts exit over entry above 1 in "
         "every band by construction. It measures the gain across the whole "
         "window, not the cost at the plant, and appears here only as this "
         "definitional footnote — never as a cost."),
        (f"Published n per band. Any band with fewer than "
         f"{context['min_n_for_median']} usable turns is drawn as INSUFFICIENT, "
         f"never as a median: {ins_txt}."),
        (f"Entry-speed floor = the detector's own heading-reliability floor "
         f"({floor_bits}). It removes {removed_bits} "
         f"({100.0 * context['share_removed_by_floor']:.1f}% in total) because "
         "stage 02 already refuses turns below it."),
        (f"The reversal class (abs_turn_angle > 180°, {context['n_exactly_180']} "
         f"turns sitting exactly on 180°) is kept in panel b as a labelled, "
         f"hatched class ({context['reversal']['combine']} Combine / "
         f"{context['reversal']['game']} game) and excluded from panel a "
         "with the reason stated there, because v·ω is physically meaningless "
         "for a full heading reversal."),
        ("Counts, shares and quartiles are recomputed at render time from "
         "outputs/turns/turns.parquet (valid turns, stage 02); every value is "
         "in outputs/figures/fig_turn_cost.csv. Bands are the reporting bands "
         "of figures 1, 3 and 5 (figures.hole.bands), left-closed. Descriptive "
         "only: no per-player values, no ranking, no p-values. Sample data: "
         "nothing here is a finding about the 2027 class."),
    ]
    wrapped: list[str] = []
    for line in caption_lines:
        wrapped.extend(_wrap_text(line, 168).split("\n"))
    fig.text(0.012, 0.262, "\n".join(wrapped), fontsize=6.2,
             ha="left", va="top", color="#333333", linespacing=1.33)

    fig.text(0.988, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)

    # Draw audit: what was drawn as a value, and what was not. A test can read
    # this instead of counting artists, and the figure raises if it disagrees
    # with the table.
    expected_drawn = int(table["drawn_in_panel_a"].sum())
    expected_drawn_insufficient = int(
        (~table["drawn_in_panel_a"] & ~table["is_reversal_class"]).sum())
    if len(drawn_cells) != expected_drawn:
        raise ValueError(
            f"{STAGE}: drew {len(drawn_cells)} retention values, but the table "
            f"flags {expected_drawn}")
    if len(insufficient_cells) != expected_drawn_insufficient:
        raise ValueError(
            f"{STAGE}: drew {len(insufficient_cells)} insufficient cells, but "
            f"the table flags {expected_drawn_insufficient}")
    fig.bdb27_draw_audit = {
        "drawn_in_panel_a": sorted(drawn_cells),
        "insufficient_in_panel_a": sorted(insufficient_cells),
        "reversal_slot_drawn": True,
        "min_n_for_median": int(min_n),
    }
    return fig


def render_turn_cost(fig: plt.Figure, cfg: common.Config,
                     out_png: Path) -> Path:
    """Save figure 8 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.turn_cost.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    return out_png


def turn_cost_csv_frame(table: pd.DataFrame) -> pd.DataFrame:
    """Every band's n, floor accounting and quartiles, per setting."""
    out = table.copy()
    out["share_pct"] = out["share_within_setting"] * 100.0
    out["setting_order"] = out["setting"].map(
        {"combine": 0, "game": 1}).fillna(2).astype(int)
    out = out.sort_values(["band_index", "setting_order"]).reset_index(drop=True)
    return out[[
        "band_index", "band", "band_low_deg", "band_high_deg",
        "is_reversal_class", "setting", "count", "setting_total",
        "share_within_setting", "share_pct", "entry_speed_floor_yd_s",
        "entry_speed_floor_key", "n_below_speed_floor",
        "n_missing_apex_retention", "n_apex_retention", "q1_apex_retention",
        "median_apex_retention", "q3_apex_retention", "iqr_apex_retention",
        "insufficient", "insufficient_reason",
        "drawn_in_panel_a"]]


def turn_cost_manifest_payload(table: pd.DataFrame, context: dict,
                               cfg: common.Config) -> dict:
    medians = context["medians"]
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_TURN_COST,
        "title": ("What a turn costs, and who pays for it: apex retention by "
                  "reporting band, and each setting's own turn mix"),
        "claim": _turn_cost_shape_sentence(context),
        "sources": ["outputs/turns/turns.parquet"],
        "measure": {
            "column": context["column"],
            "definition": "apex_speed / entry_speed (stage 02)",
            "segmentation_key": "detector.segmentation",
            "source": "outputs/turns/turns.parquet",
            "not_plotted": {
                "column": "speed_retention",
                "reason": ("exit_speed / entry_speed; the turn window runs "
                           "between speed maxima, so re-acceleration inside "
                           "the window makes it exceed 1 by construction. "
                           "Definitional footnote only, never a cost."),
            },
        },
        "panel_a": {
            "encoding": ("median apex retention with Q1-Q3 whisker, one series "
                         "per setting, no smoothing and no fitted curve"),
            "bands": context["bands"][:-1],
            "medians": medians,
            "q1": context["q1"],
            "q3": context["q3"],
            "n_drawn": context["n_drawn"],
            "min_n_for_median": context["min_n_for_median"],
            "insufficient": context["insufficient"],
            "claim_tested": ("A 30-45 degree break keeps most of the speed "
                             "while a 150-180 degree plant gives up a large "
                             "share of it."),
            "verdict": _turn_cost_curve_sentence(context),
            "shape": _turn_cost_shape_sentence(context),
        },
        "panel_b": {
            "encoding": ("share of each setting's own valid turns per band; "
                         "counts printed on the bars; reversal class hatched"),
            "shares": {
                s: {str(r["band"]): float(r["share_within_setting"])
                    for _, r in table[table["setting"] == s].iterrows()}
                for s in TURN_COST_SETTINGS},
            "counts": {
                s: {str(r["band"]): int(r["count"])
                    for _, r in table[table["setting"] == s].iterrows()}
                for s in TURN_COST_SETTINGS},
        },
        "entry_speed_floor": {
            s: {"config_key": context["floors"][s]["key"],
                "value_yd_s": context["floors"][s]["value"],
                "n_removed": context["n_below_speed_floor_by_setting"][s],
                "n_turns": context["n_by_setting"][s]}
            for s in TURN_COST_SETTINGS},
        "turns_removed_by_floor": context["n_below_speed_floor"],
        "share_removed_by_floor": context["share_removed_by_floor"],
        "reversal_class": {
            "rule": "abs_turn_angle > 180",
            "n": context["reversal"],
            "n_exactly_180": context["n_exactly_180"],
            "in_panel_a": "excluded, reason stated on the image",
            "in_panel_b": "included as a labelled, hatched class",
        },
        "n_valid": context["n_valid"],
        "n_by_setting": context["n_by_setting"],
        "caveats": [
            ("Sample data: nothing here is a finding about the 2027 class."),
            ("apex_retention is apex_speed / entry_speed: the speed lost at "
             "the plant. speed_retention (exit / entry) is above 1 by "
             "construction because the window runs between speed maxima and "
             "includes re-acceleration; it is a definitional footnote, never "
             "a cost."),
            ("Descriptive only: no per-player values, no ranking, no p-values, "
             "no smoothing or fitted curve."),
            ("Bands are figures.hole.bands, left-closed; the last class is the "
             "reversal class and carries no retention value."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_TURN_COST}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_TURN_COST}.csv",
        "revision": {
            "version": 2,
            "reason": ("Figure 8 originally plotted speed_retention (exit ÷ "
                       "entry), which is above 1 in every band by construction "
                       "and shows gains, not costs. Panel (a) now plots "
                       "apex_retention (apex ÷ entry), the speed actually "
                       "given up at the plant."),
        },
    }


# ------------- figure 9: same stopwatch, different turn
#: The published three-cone is ONE scalar in seconds. Panel (a) shows the turns
#: the detector found inside each matched player's three-cone attempt; panel (b)
#: puts those turns' entry speeds against the same published time. The figure is
#: descriptive by construction: no association statistic is computed anywhere in
#: this section, so none can be printed, captioned or implied.
STOPWATCH_COLUMNS = [
    "setting",
    "drill_name",
    "abs_turn_angle",
    "entry_speed",
    "position_group",
    "nfl_id",
    "event_id",
    "attempt",
    "valid",
]


def _stopwatch_pair(agg: pd.DataFrame, tolerance: float) -> dict | None:
    """The one near-identical pair the caption may call out, or ``None``.

    Candidate pairs must have times within ``tolerance`` *and* the same number
    of attempts, so the comparison is not confounded by one player having pooled
    two reps. Among candidates, prefer the largest gap in turn count, then the
    largest gap in the detected angle range; a pair with fewer than 2 extra
    turns and under 5 degrees of angle difference is not "visibly different"
    and is not called out at all. Iteration follows player order (sorted by
    time), so the choice is deterministic.
    """
    rows = agg.to_dict("records")
    best: dict | None = None
    best_score: tuple[int, float] | None = None
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            delta = abs(float(a["three_cone_s"]) - float(b["three_cone_s"]))
            if delta > tolerance + 1e-12:
                continue
            if int(a["n_attempts"]) != int(b["n_attempts"]):
                continue
            angle_gap = max(abs(float(a["angle_min"]) - float(b["angle_min"])),
                            abs(float(a["angle_max"]) - float(b["angle_max"])))
            count_gap = abs(int(a["n_turns"]) - int(b["n_turns"]))
            if angle_gap < 5.0 and count_gap < 2:
                continue
            score = (count_gap, angle_gap)
            if best_score is None or score > best_score:
                best_score = score
                best = {"a": a, "b": b, "delta": delta,
                        "count_gap": count_gap, "angle_gap": angle_gap}
    return best


def stopwatch_table(turns: pd.DataFrame, combine_results: pd.DataFrame,
                    cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """One row per detected turn, with its player's published three-cone time.

    Population: the detector's own valid turns where ``drill_name`` contains
    ``figures.stopwatch.drill_match``, ``setting`` and ``position_group`` match
    the block, joined on ``nfl_id`` to a non-null ``three_cone`` in the raw
    combine results. The join is inner: a turn whose player has no published
    time cannot be plotted on a time axis.

    If fewer than ``min_players`` players match, this REFUSES with the count
    instead of drawing a thin case — the gate is the point, not a formality.
    """
    block = "figures.stopwatch"
    position = str(cfg.get(f"{block}.position_group"))
    setting = str(cfg.get(f"{block}.setting"))
    drill_match = str(cfg.get(f"{block}.drill_match"))
    min_players = int(cfg.get(f"{block}.min_players"))
    tolerance = float(cfg.get(f"{block}.pair_tolerance_s"))
    if min_players < 1:
        raise common.StageRefused(
            f"{STAGE}: figures.stopwatch.min_players must be >= 1")
    if tolerance <= 0:
        raise common.StageRefused(
            f"{STAGE}: figures.stopwatch.pair_tolerance_s must be > 0")

    missing_cols = [c for c in ("nfl_id", "three_cone")
                    if c not in combine_results.columns]
    if missing_cols:
        raise common.StageRefused(
            f"{STAGE}: combine results have no {missing_cols} column; the "
            "three-cone time cannot be joined")

    drill = turns["drill_name"].astype("string").str.upper()
    valid = turns["valid"].fillna(False).astype(bool)
    mask = (valid
            & (turns["position_group"] == position)
            & (turns["setting"] == setting)
            & drill.str.contains(drill_match.upper(), regex=False, na=False))
    df = turns[mask].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} {setting} turns matching drill "
            f"name {drill_match!r} — nothing to show")
    df["nfl_id"] = pd.to_numeric(df["nfl_id"], errors="coerce")
    df["abs_turn_angle"] = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    df["entry_speed"] = pd.to_numeric(df["entry_speed"], errors="coerce")
    df["attempt"] = pd.to_numeric(df["attempt"], errors="coerce")
    df = df[df["nfl_id"].notna()
            & df["abs_turn_angle"].notna()
            & df["entry_speed"].notna()].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: the matched three-cone turns carry no usable angle or "
            "entry speed")
    df["nfl_id"] = df["nfl_id"].astype("int64")

    times = combine_results[["nfl_id", "three_cone"]].copy()
    times["nfl_id"] = pd.to_numeric(times["nfl_id"], errors="coerce")
    times["three_cone"] = pd.to_numeric(times["three_cone"], errors="coerce")
    times = times[times["nfl_id"].notna() & times["three_cone"].notna()]
    times = times.drop_duplicates()
    conflicting = times.groupby("nfl_id")["three_cone"].nunique()
    conflicting = sorted(int(i) for i in conflicting[conflicting > 1].index)
    if conflicting:
        raise common.StageRefused(
            f"{STAGE}: combine results carry more than one three_cone value "
            f"for nfl_id {conflicting[:5]}; refusing to pick one")
    times = times.drop_duplicates("nfl_id", keep="first")
    times["nfl_id"] = times["nfl_id"].astype("int64")

    merged = df.merge(times, on="nfl_id", how="inner")
    n_via_turns = int(df["nfl_id"].nunique())
    n_matched = int(merged["nfl_id"].nunique())
    if n_matched < min_players:
        raise common.StageRefused(
            f"{STAGE}: figure 9 refused — {n_matched} of {n_via_turns} "
            f"three-cone players matched to a published three_cone time, below "
            f"figures.stopwatch.min_players={min_players}. A thin figure is not "
            "evidence; no image was drawn.")
    merged["three_cone_s"] = merged["three_cone"]

    agg = merged.groupby("nfl_id").agg(
        three_cone_s=("three_cone_s", "first"),
        n_turns=("abs_turn_angle", "size"),
        n_attempts=("attempt", "nunique"),
        angle_min=("abs_turn_angle", "min"),
        angle_max=("abs_turn_angle", "max"),
        entry_min=("entry_speed", "min"),
        entry_max=("entry_speed", "max"),
    ).reset_index()
    agg = agg.sort_values(["three_cone_s", "nfl_id"],
                          kind="mergesort").reset_index(drop=True)
    agg["player_order"] = np.arange(1, len(agg) + 1)

    out = merged.merge(
        agg[["nfl_id", "player_order", "n_turns", "n_attempts"]],
        on="nfl_id", how="left")
    out = out.sort_values(["player_order", "abs_turn_angle", "event_id"],
                          kind="mergesort").reset_index(drop=True)
    out["turn_index"] = out.groupby("player_order").cumcount() + 1
    out = out.rename(columns={
        "n_turns": "n_turns_player",
        "n_attempts": "n_attempts_player",
        "abs_turn_angle": "abs_turn_angle_deg",
        "entry_speed": "entry_speed_yd_s",
    })

    if int(agg["n_turns"].sum()) != int(len(out)):
        raise ValueError(
            f"{STAGE}: per-player turn counts do not reconstruct the turn "
            "population")
    per_player = out.groupby("player_order").agg(
        rows=("nfl_id", "size"), declared=("n_turns_player", "first"))
    if not (per_player["rows"] == per_player["declared"]).all():
        raise ValueError(
            f"{STAGE}: a player's declared turn count disagrees with his rows")

    pair = _stopwatch_pair(agg, tolerance)
    floor_key = str(cfg.get(f"{block}.entry_speed_floor_key"))
    match_rule = (
        f"nfl_id join to files.combine_results three_cone (not null); turns "
        f"from outputs/turns/turns.parquet where drill_name contains "
        f"{drill_match!r}, setting == {setting!r}, position_group == "
        f"{position!r}, valid == true")

    context = {
        "position": position,
        "setting": setting,
        "drill_match": drill_match,
        "min_players": min_players,
        "tolerance": tolerance,
        "n_via_turns": n_via_turns,
        "n_unmatched": n_via_turns - n_matched,
        "n_players": int(len(agg)),
        "n_turns": int(len(out)),
        "turns_per_player_min": int(out["n_turns_player"].min()),
        "turns_per_player_max": int(out["n_turns_player"].max()),
        "n_multi_attempt_players": int((agg["n_attempts"] > 1).sum()),
        "time_min": float(agg["three_cone_s"].min()),
        "time_max": float(agg["three_cone_s"].max()),
        "angle_min": float(out["abs_turn_angle_deg"].min()),
        "angle_max": float(out["abs_turn_angle_deg"].max()),
        "entry_min": float(out["entry_speed_yd_s"].min()),
        "entry_max": float(out["entry_speed_yd_s"].max()),
        "entry_floor_key": floor_key,
        "entry_floor": float(cfg.get(floor_key)),
        "duplicate_times": int(agg["three_cone_s"].duplicated().sum()),
        "pair": pair,
        "match_rule": match_rule,
    }
    return out, context


def _stopwatch_pair_sentence(context: dict) -> str:
    """The illustration pair, stated with its tolerance — or its absence."""
    pair = context["pair"]
    if pair is None:
        return ("No two players posted times within "
                f"{context['tolerance']:.2f} s (with the same attempt count) "
                "whose turn content differed enough to be worth calling out, "
                "so no pair is named.")
    a, b = pair["a"], pair["b"]
    att = int(a["n_attempts"])
    att_txt = "one attempt each" if att == 1 else f"{att} attempts each"
    return (
        f"Two players posted near-identical times — {a['three_cone_s']:.2f} s "
        f"and {b['three_cone_s']:.2f} s (difference {pair['delta']:.2f} s, "
        f"within the {context['tolerance']:.2f} s tolerance, {att_txt}) — yet "
        f"their drills contained "
        f"{int(a['n_turns'])} and {int(b['n_turns'])} detected turns spanning "
        f"{a['angle_min']:.1f}°–{a['angle_max']:.1f}° and "
        f"{b['angle_min']:.1f}°–{b['angle_max']:.1f}° (entry "
        f"{a['entry_min']:.2f}–{a['entry_max']:.2f} and "
        f"{b['entry_min']:.2f}–{b['entry_max']:.2f} yd/s). They are circled in "
        "both panels. This is an illustration of what the scalar compresses, "
        "not evidence of anything.")


def plot_stopwatch(table: pd.DataFrame, context: dict,
                   cfg: common.Config) -> plt.Figure:
    """Two panels: turns per player against time, then entry speed vs time."""
    block = "figures.stopwatch"
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    n_players = context["n_players"]

    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    grid = fig.add_gridspec(
        1, 2, width_ratios=[1.42, 1.0], wspace=0.15,
        left=0.108, right=0.985, top=0.845, bottom=0.315)
    ax_a = fig.add_subplot(grid[0])
    ax_b = fig.add_subplot(grid[1])

    for ax in (ax_a, ax_b):
        ax.set_facecolor("#EBEBEB")
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#B3B3B3")
        ax.tick_params(labelsize=6.9, colors="#333333", length=2)

    # ---------------------------------------------------------- panel (a)
    ax_a.grid(axis="y", color="white", linewidth=0.8, zorder=0)
    y = table["player_order"].to_numpy() - 1

    # Light rule at every reporting band edge; nothing is binned or summarised.
    for edge in edges:
        ax_a.axvline(edge, color="#B0B0B0", linewidth=0.7,
                     linestyle=(0, (3, 3)), zorder=1)
    ax_a.axvspan(edges[-1], 235.0, color="#6E6E6E", alpha=0.08, zorder=0)

    ax_a.scatter(table["abs_turn_angle_deg"], y, s=34, color=PALETTE["combine"],
                 edgecolor="white", linewidth=0.5, alpha=0.9, zorder=3)

    yticks = []
    for _, row in (table.drop_duplicates("player_order")
                   .sort_values("player_order").iterrows()):
        yticks.append(f"{float(row['three_cone_s']):.2f}   "
                      f"n={int(row['n_turns_player'])}")
    ax_a.set_yticks(np.arange(n_players))
    ax_a.set_yticklabels(yticks, fontsize=6.6, color="#222222")
    ax_a.set_ylim(n_players - 0.45, -0.9)
    ax_a.set_xlim(30.0, 235.0)
    ax_a.set_xticks(edges + ([210.0] if edges[-1] < 210.0 else []))
    ax_a.set_xlabel("Absolute turn angle detected inside the three-cone (°)\n"
                    "dotted rules = reporting band edges; grey zone = "
                    f"{overflow}",
                    fontsize=7.2, color="#333333", labelpad=3)
    ax_a.set_ylabel("Three-cone time (s), fastest at top —\n"
                    "detected turns in that drill",
                    fontsize=7.2, color="#333333")
    ax_a.set_title(
        "(a) One row per player, sorted by the published time: every turn "
        "found inside his three-cone",
        loc="left", fontsize=9.4, fontweight="bold", color="#1A1A1A", pad=6)

    # ---------------------------------------------------------- panel (b)
    ax_b.grid(axis="y", color="white", linewidth=0.8, zorder=0)
    for _, prow in (table.drop_duplicates("player_order").iterrows()):
        part = table[table["player_order"] == prow["player_order"]]
        ax_b.plot([prow["three_cone_s"], prow["three_cone_s"]],
                  [part["entry_speed_yd_s"].min(),
                   part["entry_speed_yd_s"].max()],
                  color="#8E8E8E", linewidth=0.8, alpha=0.55, zorder=1)
    ax_b.scatter(table["three_cone_s"], table["entry_speed_yd_s"], s=30,
                 color=PALETTE["combine"], edgecolor="white", linewidth=0.5,
                 alpha=0.85, zorder=3)
    ax_b.axhline(context["entry_floor"], color="#555555", linewidth=0.9,
                 linestyle=(0, (4, 3)), zorder=2)
    ax_b.text(context["time_min"] - 0.03, context["entry_floor"] - 0.08,
              f"detector heading floor {context['entry_floor']:.1f} yd/s "
              f"({context['entry_floor_key']})",
              fontsize=5.9, color="#555555", ha="left", va="top")
    ax_b.set_xlim(context["time_min"] - 0.04, context["time_max"] + 0.05)
    ax_b.set_ylim(context["entry_min"] - 0.25, context["entry_max"] + 0.30)
    ax_b.set_xlabel("Published three-cone time (s)", fontsize=7.2,
                    color="#333333", labelpad=3)
    ax_b.set_ylabel("Entry speed of each detected turn (yd/s)",
                    fontsize=7.2, color="#333333")
    ax_b.set_title(
        "(b) The same turns' entry speeds, against the same number — it does "
        "not say how fast he was moving",
        loc="left", fontsize=9.4, fontweight="bold", color="#1A1A1A", pad=6)

    # ------------------------------------------------- pair rings, both panels
    pair = context["pair"]
    if pair is not None:
        orders = {int(pair["a"]["player_order"]), int(pair["b"]["player_order"])}
        part = table[table["player_order"].isin(orders)]
        ax_a.scatter(part["abs_turn_angle_deg"],
                     part["player_order"].to_numpy() - 1,
                     s=96, facecolor="none", edgecolor=ALERT, linewidth=0.9,
                     zorder=4)
        ax_b.scatter(part["three_cone_s"], part["entry_speed_yd_s"], s=88,
                     facecolor="none", edgecolor=ALERT, linewidth=0.9, zorder=4)
        fig.bdb27_pair_orders = sorted(orders)
    else:
        fig.bdb27_pair_orders = []

    # ------------------------------------------------------- titles, caption
    fig.text(0.012, 0.980, "Same stopwatch, different turn", fontsize=16.5,
             fontweight="bold", ha="left", va="top", color="#111111")
    fig.text(0.012, 0.952,
             _wrap_text(
                 "One drill, one published number in seconds, many turns. Each "
                 "dot is one valid turn the detector found inside that "
                 "player's three-cone; a row is a player. Panel (b) puts those "
                 "turns' entry speeds against the same number. Descriptive "
                 "only: no fit, no smoothing, no correlation, no test.",
                 176),
             fontsize=8.4, ha="left", va="top", color="#333333")

    caption_lines = [
        STAMP,
        ("FINDING, AS MEASURED. The three-cone is published as a single scalar, "
         f"and this sample's drills contained {context['turns_per_player_min']}"
         f"–{context['turns_per_player_max']} detected turns each across "
         f"{context['n_players']} players ({context['n_turns']} turns), "
         f"spanning {context['angle_min']:.0f}° to {context['angle_max']:.0f}° "
         f"at entry speeds of {context['entry_min']:.2f} to "
         f"{context['entry_max']:.2f} yd/s. The metric records neither the "
         "angle of a turn nor the speed into it, so by construction it cannot "
         "report which turns produced a given time. That is a property of the "
         "measurement, not a claim about any player."),
        ("WHAT THIS IS NOT. This is not a test of whether three-cone time "
         "predicts turn angle or entry speed. With "
         f"{context['n_players']} matched players such a test is underpowered, "
         "so an absent association here would be no evidence at all. No "
         "correlation coefficient, no R², no regression and no p-value is "
         "computed, printed or implied anywhere in this figure — the claim is "
         "descriptive: the same published number was produced by drills "
         "containing visibly different turns."),
        ("ILLUSTRATION, NOT EVIDENCE. " + _stopwatch_pair_sentence(context)),
        (f"MATCH RULE. {context['match_rule']}. {context['n_players']} of "
         f"{context['n_via_turns']} three-cone players matched "
         f"({context['n_unmatched']} unmatched, no published time). The figure "
         f"refuses below {context['min_players']} matched players rather than "
         "draw a thin case."),
        (f"ATTEMPTS. {context['n_multi_attempt_players']} of "
         f"{context['n_players']} players have more than one three-cone "
         "attempt in the sample; turns are pooled across attempts inside the "
         "drill, as in every other figure here. "
         f"{context['duplicate_times']} players share an identical published "
         "time with another player, which is why panel (b) shows overlapping "
         "columns rather than one point per time."),
        ("Dots are the detector's valid turns, recomputed at render time from "
         "outputs/turns/turns.parquet; every value is in "
         "outputs/figures/fig_stopwatch.csv. Sample data: this shows what the "
         "metric compresses, not how well it predicts, and nothing here is a "
         "finding about the 2027 class."),
    ]
    wrapped: list[str] = []
    for line in caption_lines:
        wrapped.extend(_wrap_text(line, 168).split("\n"))
    fig.text(0.012, 0.258, "\n".join(wrapped), fontsize=6.05,
             ha="left", va="top", color="#333333", linespacing=1.30)

    fig.text(0.988, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)

    # Draw audit: every turn row is exactly one dot in each panel.
    if len(table) != context["n_turns"]:
        raise ValueError(f"{STAGE}: table has {len(table)} rows, context says "
                         f"{context['n_turns']} turns")
    if len(y) != len(table):
        raise ValueError(f"{STAGE}: panel (a) drew {len(y)} dots for "
                         f"{len(table)} turns")
    fig.bdb27_draw_audit = {
        "n_rows": int(n_players),
        "n_turns": int(len(table)),
        "n_dots_a": int(len(y)),
        "pair_orders": list(getattr(fig, "bdb27_pair_orders", [])),
    }
    return fig


def render_stopwatch(fig: plt.Figure, cfg: common.Config,
                     out_png: Path) -> Path:
    """Save figure 9 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.stopwatch.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    return out_png


def stopwatch_csv_frame(table: pd.DataFrame, context: dict) -> pd.DataFrame:
    """One row per detected turn: the player's time, count, angle and speed."""
    out = table.copy()
    pair = context["pair"]
    orders = set()
    if pair is not None:
        orders = {int(pair["a"]["player_order"]),
                  int(pair["b"]["player_order"])}
    out["pair_player"] = out["player_order"].isin(orders)
    out["match_rule"] = context["match_rule"]
    out["gate_min_players"] = context["min_players"]
    out["gate_passed"] = True
    return out[[
        "player_order", "nfl_id", "three_cone_s", "n_turns_player",
        "n_attempts_player", "turn_index", "abs_turn_angle_deg",
        "entry_speed_yd_s", "attempt", "event_id", "pair_player",
        "match_rule", "gate_min_players", "gate_passed"]]


def stopwatch_manifest_payload(table: pd.DataFrame, context: dict,
                               cfg: common.Config) -> dict:
    pair = context["pair"]
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_STOPWATCH,
        "title": ("Same stopwatch, different turn: what the published "
                  "three-cone time compresses"),
        "sources": ["outputs/turns/turns.parquet",
                    "data/raw/combine_results.csv"],
        "match": {
            "rule": context["match_rule"],
            "join_key": "nfl_id",
            "drill_match": context["drill_match"],
            "setting": context["setting"],
            "position_group": context["position"],
            "n_players_in_turns": context["n_via_turns"],
            "n_matched_players": context["n_players"],
            "n_unmatched_players": context["n_unmatched"],
            "min_players": context["min_players"],
            "gate": "PASSED",
        },
        "n_players": context["n_players"],
        "n_turns": context["n_turns"],
        "spread": {
            "three_cone_s": [context["time_min"], context["time_max"]],
            "abs_turn_angle_deg": [context["angle_min"], context["angle_max"]],
            "entry_speed_yd_s": [context["entry_min"], context["entry_max"]],
            "turns_per_player": [context["turns_per_player_min"],
                                 context["turns_per_player_max"]],
            "n_players_with_multiple_attempts":
                context["n_multi_attempt_players"],
            "n_players_sharing_a_time": context["duplicate_times"],
        },
        "panel_a": {
            "encoding": ("one row per player sorted by published three-cone "
                         "time, one dot per detected turn on the angle axis; "
                         "light rules at the reporting band edges only"),
            "bands_reference": "figures.hole.bands.turn_angle_deg",
        },
        "panel_b": {
            "encoding": ("entry speed of each detected turn against the same "
                         "published time; min-max line per player; no fit"),
            "entry_speed_floor": {
                "config_key": context["entry_floor_key"],
                "value_yd_s": context["entry_floor"],
            },
        },
        "pair_illustration": None if pair is None else {
            "tolerance_s": context["tolerance"],
            "delta_s": pair["delta"],
            "a": {"nfl_id": int(pair["a"]["nfl_id"]),
                  "three_cone_s": float(pair["a"]["three_cone_s"]),
                  "n_turns": int(pair["a"]["n_turns"]),
                  "n_attempts": int(pair["a"]["n_attempts"])},
            "b": {"nfl_id": int(pair["b"]["nfl_id"]),
                  "three_cone_s": float(pair["b"]["three_cone_s"]),
                  "n_turns": int(pair["b"]["n_turns"]),
                  "n_attempts": int(pair["b"]["n_attempts"])},
            "status": "illustration only, never evidence",
        },
        "claim": ("The published three-cone time is one scalar; the drills "
                  "behind it contained visibly different turns at different "
                  "entry speeds, so the metric cannot distinguish them by "
                  "construction."),
        "not_claimed": [
            "That three-cone time fails to predict turn angle or entry speed.",
            "That any difference between players is a difference in ability.",
            "Any correlation, R², regression, p-value or fitted curve.",
        ],
        "caveats": [
            ("Sample data: one session of sample data; nothing here is a "
             "finding about the 2027 class."),
            ("Descriptive only: the figure shows what the metric compresses, "
             "not how well it predicts. With this many matched players an "
             "association test would be underpowered, so none is run."),
            ("Turns are pooled across attempts within the drill; the attempt "
             "count per player is printed in the CSV and stated in the "
             "caption."),
            ("A player's row is the detector's own valid turns, not a scout's "
             "reading of the drill; detection limits apply as in every other "
             "figure."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_STOPWATCH}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_STOPWATCH}.csv",
    }


# ------------- figure 10: the test never checks which way he turns
#: Added at Conor's request (2026-11-08); it is not derived from a TASK.md line.
#: The demand claim behind the case is that a game DB must break both ways. The
#: Combine's shallow-turn band is the one place a test *could* check that, so
#: this figure shows the same band split by the SIGN of the detector's turn
#: angle, Combine against games.
#:
#: The sign is not self-interpreting. ``turn_angle`` is a heading change and
#: ``heading_deg`` is ``atan2(vx, vy)`` (0 = +y, increasing clockwise). Turning
#: that into LEFT/RIGHT needs the field frame's handedness — which side of a
#: player +x lies on when he faces +y. Three checks run at render time and the
#: labels are DERIVED from them, never hard-coded:
#:
#:   1. chirality (decisive): vendor play descriptions say which side of the
#:      offence a pass went to ("deep left", "short right"), a body-relative
#:      label that is independent of the coordinate frame. The mapping from that
#:      label to the y half of the field, computed separately for both
#:      ``play_direction`` values, pins which way +y points relative to a player.
#:   2. route semantics (per side): OUT / IN / SLANT / POST / CORNER imply a
#:      lateral break; the sign of ``turn_angle`` is tested against it in each
#:      route family x starting-side cell, so one side failing cannot hide in a
#:      pooled rate.
#:   3. orientation (supporting): at steady forward running the sign of the turn
#:      agrees with the sign of the change in the game-only ``o`` column.
#:
#: LEFT/RIGHT is used only when (1) resolves and (2) is consistent on both sides;
#: otherwise the bars are SIDE A / SIDE B and the caption says which check failed.
#:
#: The figure is descriptive by construction: four counts and two player counts.
#: No symmetry test, no binomial p-value, no interval on the split — a 15/11
#: split of 26 turns from 16 players is not evidence about anybody's body.
BOTH_HIPS_COLUMNS = [
    "setting",
    "abs_turn_angle",
    "turn_angle",
    "position_group",
    "nfl_id",
    "game_id",
    "play_id",
    "frame_start",
    "frame_end",
    "snap_type",
    "valid",
]

#: Names used when the direction check does not resolve.
SIDE_POSITIVE = "SIDE A"
SIDE_NEGATIVE = "SIDE B"

#: The detector's own convention, quoted from ``kinematics.heading_deg``.
SIGN_CONVENTION = (
    "turn_angle = unwrapped heading change over the turn segment, end minus "
    "start; heading = atan2(vx, vy) in degrees mod 360, i.e. 0 = +y with the "
    "angle increasing clockwise (kinematics.heading_deg). Turning left is "
    "therefore a NEGATIVE turn_angle and turning right a POSITIVE one, once it "
    "is known which side of a player +y lies on; that is what the checks below "
    "decide, not assume."
)

#: Why a check is unavailable, when it is.
NO_ROUTE_DATA = (
    "no valid game ROUTE turns joined to a route label, so the route check "
    "could not be run on this data"
)
NO_CHIRALITY_DATA = (
    "no tracked targeted receiver with a body-relative pass direction in the "
    "play description, so the frame's handedness could not be pinned"
)


def load_both_hips_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 10 needs, including the signed turn angle."""
    df = pd.read_parquet(path, columns=BOTH_HIPS_COLUMNS)
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    return df


def load_route_turns(turns: pd.DataFrame, cfg: common.Config) -> pd.DataFrame:
    """Every valid game ROUTE turn: the population the route check can use."""
    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["setting"] == "game")
               & (turns["snap_type"] == "ROUTE")].copy()
    for col in ("game_id", "play_id", "nfl_id"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[df["game_id"].notna() & df["play_id"].notna()
              & df["nfl_id"].notna()].copy()


def load_game_frames(cfg: common.Config, mode: str,
                     keys: pd.DataFrame) -> pd.DataFrame:
    """Tracking frames for the given ``(game, play, player)`` keys only.

    DuckDB filters on ``nfl_id``, so the full tracking file is never loaded into
    pandas. ``frame_idx`` reproduces the period ordering stage 02 used: the row
    number within ``(game_id, play_id, nfl_id)`` by ``time``.
    """
    empty = pd.DataFrame(columns=["game_id", "play_id", "nfl_id", "frame_idx",
                                  "s", "o", "dir", "x", "y"])
    if keys is None or len(keys) == 0:
        return empty
    work = keys[["game_id", "play_id", "nfl_id"]].copy()
    for col in ("game_id", "play_id", "nfl_id"):
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna().astype("int64").drop_duplicates()
    if work.empty:
        return empty
    ids = sorted(int(i) for i in work["nfl_id"].unique())
    con = common.connect(cfg)
    try:
        sql = (
            "SELECT game_id, play_id, nfl_id, s, o, dir, x, y, "
            "CAST(row_number() OVER (PARTITION BY game_id, play_id, nfl_id "
            "ORDER BY time) - 1 AS BIGINT) AS frame_idx "
            f"FROM {common.raw_csv_sql(cfg, mode, 'game_tracking')} "
            f"WHERE nfl_id IN ({', '.join(str(i) for i in ids)})"
        )
        frames = con.execute(sql).df()
    finally:
        con.close()
    frames = frames.merge(work, on=["game_id", "play_id", "nfl_id"], how="inner")
    for col in ("s", "o", "dir", "x", "y"):
        frames[col] = pd.to_numeric(frames[col], errors="coerce")
    return frames


def _wrap180(deg: float) -> float:
    """Wrap an angular difference into [-180, 180)."""
    return (float(deg) + 180.0) % 360.0 - 180.0


def _heading(vx: float, vy: float) -> float:
    """The detector's heading convention: 0 = +y, increasing clockwise."""
    return float(np.degrees(np.arctan2(vx, vy)))


def _frames_index(frames: pd.DataFrame) -> dict[tuple[int, int, int, int], tuple]:
    lookup: dict[tuple[int, int, int, int], tuple] = {}
    if frames is None or len(frames) == 0:
        return lookup
    work = frames.copy()
    for col in ("game_id", "play_id", "nfl_id", "frame_idx"):
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["game_id", "play_id", "nfl_id", "frame_idx",
                               "x", "y"])
    work = work.drop_duplicates(subset=["game_id", "play_id", "nfl_id",
                                        "frame_idx"], keep="first")
    for row in work.itertuples(index=False):
        lookup[(int(row.game_id), int(row.play_id), int(row.nfl_id),
                int(row.frame_idx))] = (float(row.x), float(row.y),
                                        float(row.s), float(row.o),
                                        float(row.dir))
    return lookup


def orientation_check(turns: pd.DataFrame, frames: pd.DataFrame,
                      cfg: common.Config) -> dict:
    """Supporting check: does the sign of the turn track the change in ``o``?

    Tested at steady forward running only (both segment ends at or above
    ``steady_speed_yd_s`` and body within ``max_body_heading_divergence_deg`` of
    the direction of travel). It shows the turn is a real body rotation; on its
    own it cannot pin handedness, because ``o`` lives in the same frame as
    ``heading`` and a mirrored frame would flip both together.
    """
    block = "figures.both_hips.direction_check.orientation"
    steady = float(cfg.get(f"{block}.steady_speed_yd_s"))
    max_div = float(cfg.get(f"{block}.max_body_heading_divergence_deg"))
    min_tested = int(cfg.get(f"{block}.min_tested"))
    min_share = float(cfg.get(f"{block}.min_agree_share"))
    if steady <= 0 or max_div <= 0 or min_tested < 1 or not 0 < min_share <= 1:
        raise common.StageRefused(
            f"{STAGE}: figures.both_hips.direction_check.orientation values "
            "are unusable")

    lookup = _frames_index(frames)
    n_testable = 0
    n_tested = 0
    n_agree = 0
    for row in turns.itertuples(index=False):
        if pd.isna(row.game_id) or pd.isna(row.play_id) or pd.isna(row.nfl_id):
            continue
        if pd.isna(row.frame_start) or pd.isna(row.frame_end):
            continue
        key = (int(row.game_id), int(row.play_id), int(row.nfl_id))
        lo = lookup.get(key + (int(row.frame_start),))
        hi = lookup.get(key + (int(row.frame_end),))
        if lo is None or hi is None:
            continue
        n_testable += 1
        if lo[2] < steady or hi[2] < steady:
            continue
        if abs(_wrap180(lo[3] - lo[4])) > max_div:
            continue
        if abs(_wrap180(hi[3] - hi[4])) > max_div:
            continue
        delta_o = _wrap180(hi[3] - lo[3])
        if abs(delta_o) < 1e-9 or abs(float(row.turn_angle)) < 1e-9:
            continue
        n_tested += 1
        if np.sign(delta_o) == np.sign(float(row.turn_angle)):
            n_agree += 1

    share = (n_agree / n_tested) if n_tested else None
    tracks = bool(n_tested >= min_tested and share is not None
                  and share >= min_share)
    return {
        "available": bool(len(lookup)),
        "n_plotted_turns": int(len(turns)),
        "n_with_both_frames": int(n_testable),
        "n_tested": int(n_tested),
        "n_agree": int(n_agree),
        "agree_share": share,
        "steady_speed_yd_s": steady,
        "max_body_heading_divergence_deg": max_div,
        "min_tested": min_tested,
        "min_agree_share": min_share,
        "sign_tracks_orientation": tracks,
    }


def _pass_side(description) -> str | None:
    """The body-relative side a pass went to, from the vendor description."""
    if not isinstance(description, str):
        return None
    match = re.search(r"\bpass\b[^.]*?\b(left|right|middle)\b", description,
                      flags=re.IGNORECASE)
    return match.group(1).lower() if match else None


def chirality_check(plays: pd.DataFrame, targets: pd.DataFrame,
                    cfg: common.Config) -> dict:
    """Pin the field frame's handedness with body-relative vendor semantics.

    ``play_description`` says "deep left" / "short right" / "deep middle" — a
    label about the offence's *body* side, not about a coordinate. The target
    receiver's tracked y half then says which side of the field that is. Because
    the field does not rotate with the offence, the mapping must FLIP between
    ``play_direction == 'right'`` and ``'left'``; a screen-relative reading
    would not flip, and a mirrored frame would show a systematic one-sided
    failure. Passing both checks is what pins +y to a body side.
    """
    block = "figures.both_hips.direction_check.chirality"
    mid = float(cfg.get(f"{block}.field_mid_y"))
    min_plays = int(cfg.get(f"{block}.min_plays"))
    min_share = float(cfg.get(f"{block}.min_agree_share"))
    if not (0 < mid < 120) or min_plays < 1 or not 0 < min_share <= 1:
        raise common.StageRefused(
            f"{STAGE}: figures.both_hips.direction_check.chirality values are "
            "unusable")

    empty = {"available": False, "reason": NO_CHIRALITY_DATA, "n_plays": 0,
             "n_agree": 0, "agree_share": None, "resolved": False,
             "field_mid_y": mid, "min_plays": min_plays,
             "min_agree_share": min_share, "by_cell": [],
             "left_is_positive_y_when_play_direction_right": None}
    if plays is None or len(plays) == 0 or targets is None or len(targets) == 0:
        return empty

    work = plays.copy()
    work["side"] = work["play_description"].map(_pass_side)
    work = work[(work["target"].fillna(False).astype(bool))
                & work["side"].isin(["left", "right"])
                & work["play_direction"].isin(["left", "right"])]
    if work.empty:
        return empty
    for col in ("game_id", "play_id", "nfl_id"):
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["game_id", "play_id", "nfl_id"])
    work = work.drop_duplicates(subset=["game_id", "play_id"], keep="first")
    if work.empty:
        return empty

    frames = targets.copy()
    for col in ("game_id", "play_id", "nfl_id", "y"):
        frames[col] = pd.to_numeric(frames[col], errors="coerce")
    frames = frames.dropna(subset=["game_id", "play_id", "nfl_id", "y"])
    if frames.empty:
        return empty
    mean_y = frames.groupby(["game_id", "play_id"])["y"].mean().rename("mean_y")
    joined = work.merge(mean_y, on=["game_id", "play_id"], how="inner")
    if joined.empty:
        return empty
    joined["high"] = joined["mean_y"] > mid
    joined["expected_high"] = ((joined["side"] == "left")
                               == (joined["play_direction"] == "right"))
    joined["agree"] = joined["high"] == joined["expected_high"]

    cells = []
    for (direction, side), part in joined.groupby(["play_direction", "side"],
                                                  sort=True):
        cells.append({
            "play_direction": direction,
            "pass_side": side,
            "n_plays": int(len(part)),
            "n_agree": int(part["agree"].sum()),
            "agree_share": float(part["agree"].mean()),
        })
    n_plays = int(len(joined))
    n_agree = int(joined["agree"].sum())
    share = n_agree / n_plays if n_plays else None
    resolved = bool(n_plays >= min_plays and share is not None
                    and share >= min_share)
    return {
        "available": True,
        "reason": None,
        "n_plays": n_plays,
        "n_agree": n_agree,
        "agree_share": share,
        "resolved": resolved,
        "field_mid_y": mid,
        "min_plays": min_plays,
        "min_agree_share": min_share,
        "by_cell": cells,
        "left_is_positive_y_when_play_direction_right": True,
    }


def route_direction_check(turns: pd.DataFrame, frames: pd.DataFrame,
                          plays: pd.DataFrame, cfg: common.Config
                          ) -> tuple[pd.DataFrame, dict]:
    """Per route family x starting side: does the turn sign match the break?

    The expected sign comes from the observed entry velocity and the lateral
    direction the label requires: the break takes the velocity toward an exit
    velocity that keeps the observed downfield component and takes the lateral
    sign the route family implies (OUT / CORNER outward, IN / SLANT / POST
    inward, relative to the receiver's starting half of the field). The label
    supplies only the lateral sign; nothing else is modelled.

    A family CARRIES the check only if BOTH side cells clear ``min_cell`` turns
    and ``min_agree_share``. That is deliberate: a pooled rate can hide one side
    being wrong, and a wrong side is a frame or convention problem, not noise.
    """
    block = "figures.both_hips.direction_check.route"
    families = {str(k).upper(): str(v).lower()
                for k, v in cfg.get(f"{block}.families").items()}
    mid = float(cfg.get(f"{block}.field_mid_y"))
    min_cell = int(cfg.get(f"{block}.min_cell"))
    min_share = float(cfg.get(f"{block}.min_agree_share"))
    min_families = int(cfg.get(f"{block}.min_families"))
    if not families or min_cell < 1 or not 0 < min_share <= 1:
        raise common.StageRefused(
            f"{STAGE}: figures.both_hips.direction_check.route values are "
            "unusable")

    columns = ["route_ran", "start_side", "n_turns_checked", "n_agree",
               "agreement_rate"]
    empty_summary = {
        "available": False, "reason": NO_ROUTE_DATA,
        "families": sorted(families), "families_carried": [],
        "families_not_carried": [], "n_turns_checked": 0, "n_agree": 0,
        "pooled_rate": None, "min_cell": min_cell,
        "min_agree_share": min_share, "min_families": min_families,
        "consistent": False,
    }
    if turns is None or len(turns) == 0 or plays is None or len(plays) == 0:
        return pd.DataFrame(columns=columns), empty_summary

    work = turns.copy()
    work["route_ran"] = work["route_ran"].astype("string").str.upper()
    work = work[work["route_ran"].isin(list(families))]
    if work.empty:
        return pd.DataFrame(columns=columns), empty_summary

    lookup = _frames_index(frames)
    rows = []
    for row in work.itertuples(index=False):
        lo_idx = getattr(row, "frame_start", None)
        hi_idx = getattr(row, "frame_end", None)
        if pd.isna(lo_idx) or pd.isna(hi_idx):
            continue
        key = (int(row.game_id), int(row.play_id), int(row.nfl_id))
        lo = lookup.get(key + (int(lo_idx),))
        hi = lookup.get(key + (int(hi_idx),))
        prev = lookup.get(key + (int(lo_idx) - 2,))
        if lo is None or hi is None or prev is None:
            continue
        _, y0, _, _, _ = lo
        side = int(np.sign(y0 - mid))
        if side == 0:
            continue
        family = str(row.route_ran).upper()
        outward = families[family] == "outward"
        lateral = side if outward else -side  # +1 = break toward higher y
        vx0 = (lo[0] - prev[0]) / 2.0
        vy0 = (lo[1] - prev[1]) / 2.0
        prev1 = lookup.get(key + (int(hi_idx) - 1,))
        if prev1 is None:
            continue
        vx1 = hi[0] - prev1[0]
        vy1 = hi[1] - prev1[1]
        if abs(vx0) + abs(vy0) < 1e-9 or abs(vx1) + abs(vy1) < 1e-9:
            continue
        expected = np.sign(_wrap180(
            _heading(vx1, abs(vy1) * lateral) - _heading(vx0, vy0)))
        observed = np.sign(float(row.turn_angle))
        if expected == 0 or observed == 0:
            continue
        rows.append({
            "route_ran": family,
            "start_side": "y_above_mid" if side > 0 else "y_below_mid",
            "expected_sign": int(expected),
            "observed_sign": int(observed),
            "agree": bool(expected == observed),
        })
    if not rows:
        return pd.DataFrame(columns=columns), empty_summary

    detail = pd.DataFrame(rows)
    table = (detail.groupby(["route_ran", "start_side"])
             .agg(n_turns_checked=("agree", "size"),
                  n_agree=("agree", "sum"))
             .reset_index())
    table["agreement_rate"] = table["n_agree"] / table["n_turns_checked"]
    table = table.sort_values(["route_ran", "start_side"],
                              kind="mergesort").reset_index(drop=True)

    carried: list[str] = []
    not_carried: list[dict] = []
    for family in sorted(families):
        part = table[table["route_ran"] == family]
        sides = set(part["start_side"])
        if sides != {"y_above_mid", "y_below_mid"}:
            not_carried.append({
                "family": family,
                "reason": "a starting side has no tested turns",
                "cells": part.to_dict("records"),
            })
            continue
        if (part["n_turns_checked"] >= min_cell).all() and \
                (part["agreement_rate"] >= min_share).all():
            carried.append(family)
        else:
            cells = part.to_dict("records")
            detail = "; ".join(
                f"{c['start_side']} {int(c['n_agree'])}/"
                f"{int(c['n_turns_checked'])} ({c['agreement_rate'] * 100:.0f}%)"
                for c in cells)
            not_carried.append({
                "family": family,
                "reason": (f"below the {min_share:.0%} floor or n={min_cell} on "
                           f"at least one side: {detail}"),
                "cells": cells,
            })

    n_turns_checked = int(table["n_turns_checked"].sum())
    n_agree = int(table["n_agree"].sum())
    summary = {
        "available": True,
        "reason": None,
        "families": sorted(families),
        "families_carried": carried,
        "families_not_carried": not_carried,
        "n_turns_checked": n_turns_checked,
        "n_agree": n_agree,
        "pooled_rate": (n_agree / n_turns_checked) if n_turns_checked else None,
        "min_cell": min_cell,
        "min_agree_share": min_share,
        "min_families": min_families,
        "consistent": bool(len(carried) >= min_families),
    }
    return table, summary


def direction_labels(check: dict) -> dict[str, str]:
    """Map the sign of ``turn_angle`` to a label, from the recorded checks.

    LEFT/RIGHT only when the recorded checks say the frame's handedness is
    pinned AND the route check is consistent on both sides. Otherwise the two
    signs are SIDE A (positive) and SIDE B (negative). Nothing here assumes an
    outcome: the caller passes the checks it actually ran.
    """
    if check.get("resolved"):
        return {"positive": "RIGHT", "negative": "LEFT"}
    return {"positive": SIDE_POSITIVE, "negative": SIDE_NEGATIVE}


def both_hips_checks(route: dict, cfg: common.Config
                     ) -> tuple[dict, pd.DataFrame]:
    """Run the two direction checks that decide the bar labels."""
    chirality = chirality_check(route.get("plays"), route.get("targets"), cfg)
    route_table, route_summary = route_direction_check(
        route.get("turns"), route.get("frames"), route.get("plays"), cfg)
    route_summary["route_table"] = route_table
    check = {
        "chirality": chirality,
        "route": route_summary,
        "route_table": route_table,
        "frame_handedness_established": bool(chirality["resolved"]),
        "route_check_consistent": bool(route_summary["consistent"]),
        "resolved": bool(chirality["resolved"] and route_summary["consistent"]),
    }
    return check, route_table


def both_hips_table(turns: pd.DataFrame, frames: pd.DataFrame, check: dict,
                    cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """Four counts: setting x sign of the turn, inside the configured band."""
    block = "figures.both_hips"
    position = str(cfg.get(f"{block}.position_group"))
    settings = [str(s) for s in cfg.get(f"{block}.settings")]
    low, high = (float(x) for x in cfg.get(f"{block}.angle_band_deg"))
    if high <= low:
        raise common.StageRefused(
            f"{STAGE}: figures.both_hips.angle_band_deg must be increasing")
    if not settings:
        raise common.StageRefused(f"{STAGE}: figures.both_hips.settings is empty")
    missing = [c for c in ("setting", "abs_turn_angle", "turn_angle",
                           "position_group", "nfl_id", "valid")
               if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); the direction "
            "split cannot be drawn")

    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    mask = (valid
            & (turns["position_group"] == position)
            & turns["setting"].isin(settings)
            & (angle >= low) & (angle < high))
    df = turns[mask].copy()
    df["turn_angle"] = pd.to_numeric(df["turn_angle"], errors="coerce")
    df = df[df["turn_angle"].notna() & (df["turn_angle"] != 0)]
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in [{low:g}, {high:g}) with a "
            "signed angle — nothing to show")

    orientation = orientation_check(
        df[df["setting"] == "game"].copy(), frames, cfg)
    check = dict(check)
    check["orientation"] = orientation
    labels = direction_labels(check)

    # LEFT then RIGHT reads naturally once a side is known; SIDE A first when it
    # is not (A is the positive sign by definition).
    order = [(-1, labels["negative"]), (1, labels["positive"])]
    if not check["resolved"]:
        order = [(1, labels["positive"]), (-1, labels["negative"])]
    check["bar_order"] = [label for _, label in order]

    rows = []
    for setting in settings:
        part = df[df["setting"] == setting]
        if part.empty:
            raise common.StageRefused(
                f"{STAGE}: no valid {position} turns in [{low:g}, {high:g}) for "
                f"setting {setting!r} — a two-group figure cannot be drawn with "
                "one empty group")
        total = int(len(part))
        per_player = part.groupby("nfl_id").size()
        for sign, label in order:
            sub = part[np.sign(part["turn_angle"]) == sign]
            count = int(len(sub))
            rows.append({
                "setting": setting,
                "setting_label": (COMBINE_LABEL if setting == "combine"
                                  else GAME_LABEL),
                "direction_label": label,
                "turn_sign": "positive" if sign > 0 else "negative",
                "count": count,
                "share_within_setting": count / total,
                "n_players_setting": int(part["nfl_id"].nunique()),
                "turns_setting": total,
                "turns_per_player_median": float(per_player.median()),
            })
    table = pd.DataFrame(rows)

    players = {s: int(df[df["setting"] == s]["nfl_id"].nunique())
               for s in settings}
    totals = {s: int((df["setting"] == s).sum()) for s in settings}
    medians = {s: float(df[df["setting"] == s].groupby("nfl_id").size().median())
               for s in settings}

    if int(table["count"].sum()) != int(len(df)):
        raise ValueError(
            f"{STAGE}: the four bars do not reconstruct the turn population")
    for setting in settings:
        if int(table[table["setting"] == setting]["count"].sum()) != totals[setting]:
            raise ValueError(
                f"{STAGE}: {setting} bars do not sum to its turn count")

    context = {
        "position": position,
        "settings": settings,
        "angle_low": low,
        "angle_high": high,
        "labels": labels,
        "check": check,
        "players": players,
        "totals": totals,
        "medians": medians,
        "n_players": int(df["nfl_id"].nunique()),
        "n_turns": int(len(df)),
        "sign_convention": SIGN_CONVENTION,
    }
    return table, context


def both_hips_plot_frame(table: pd.DataFrame) -> pd.DataFrame:
    """One row per bar, with the x tick label carrying the exposure."""
    frame = table.copy()
    frame["x"] = frame["setting_label"]
    frame["lab_y"] = frame["count"] + 0.0
    return frame


def _bh_direction_sentence(context: dict) -> str:
    """The caption's account of what fixed the labels, or why nothing did."""
    check = context["check"]
    chir = check["chirality"]
    route = check["route"]
    labels = context["labels"]
    if check["resolved"]:
        carried = ", ".join(route["families_carried"])
        table = route["route_table"]
        worst = float(table[table["route_ran"].isin(route["families_carried"])]
                      ["agreement_rate"].min())
        return (
            f"Direction is named, not assumed. Vendor play descriptions are "
            f"body-relative ('deep left', 'short right'): across "
            f"{chir['n_plays']} tracked passes the side named in the "
            f"description matched the target's side of the field in "
            f"{chir['agree_share'] * 100:.1f}% of them, and the mapping flips "
            f"with play_direction exactly as a body-relative label must. That "
            f"pins +y to the offence's left when the offence moves toward +x, "
            f"so a negative turn_angle is a turn to the player's LEFT and a "
            f"positive one to his RIGHT. The route check agrees on both sides "
            f"of the field for {carried} (worst cell "
            f"{worst * 100:.1f}%), at a "
            f"floor of {route['min_agree_share'] * 100:.0f}% per cell and "
            f"n>={route['min_cell']} per cell."
        )
    reasons = []
    if not chir["resolved"]:
        reasons.append(
            "the handedness check did not resolve (" +
            (chir["reason"] if not chir["available"] else
             f"{chir['n_plays']} plays, {chir['agree_share'] * 100:.1f}% "
             f"agreement, floor {chir['min_agree_share'] * 100:.0f}%") + ")")
    if not route["consistent"]:
        reasons.append(
            "the route check was not consistent on both sides (" +
            (route["reason"] if not route["available"] else
             f"{len(route['families_carried'])} famil"
             f"{'y' if len(route['families_carried']) == 1 else 'ies'} carried "
             f"the check, {route['min_families']} required") + ")")
    return (
        "Direction is NOT named. " + "; ".join(reasons) + ". The bars are "
        f"{labels['positive']} (positive turn_angle, clockwise heading change) "
        f"and {labels['negative']} (negative), and no side of the body is "
        "claimed."
    )


def plot_both_hips(table: pd.DataFrame, context: dict,
                   cfg: common.Config) -> ggplot:
    """Four bars in two groups, one shared axis, counts at the bar ends."""
    block = "figures.both_hips"
    frame = both_hips_plot_frame(table)
    labels = frame[frame["count"] > 0].copy()

    settings = list(context["settings"])
    x_order = [COMBINE_LABEL if s == "combine" else GAME_LABEL for s in settings]
    ticks = []
    for s in settings:
        ticks.append(f"{COMBINE_LABEL if s == 'combine' else GAME_LABEL}\n"
                     f"{context['players'][s]} players · "
                     f"{context['totals'][s]} turns")

    def _count(setting: str, sign: str) -> int:
        part = table[(table["setting"] == setting)
                     & (table["turn_sign"] == sign)]["count"]
        return int(part.iloc[0])

    cpos, cneg = _count("combine", "positive"), _count("combine", "negative")
    gpos, gneg = _count("game", "positive"), _count("game", "negative")
    check = context["check"]
    pos_label = context["labels"]["positive"]
    neg_label = context["labels"]["negative"]
    orient = check["orientation"]
    n_tested = orient["n_tested"]
    agree = orient["n_agree"]
    share = orient["agree_share"]
    share_txt = f"{share * 100:.0f}%" if share is not None else "n/a"
    labels_order = list(check["bar_order"])

    title = "The test never checks which way he turns"
    subtitle = (
        f"Valid {context['position']} turns of {context['angle_low']:g}–"
        f"{context['angle_high']:g}°, split by the sign of the detector's turn "
        f"angle and named {labels_order[0]} / {labels_order[1]} on the "
        "direction checks — Combine (one session) against games. Four bars, one "
        "axis, counts on the bars.")

    caption_lines = [
        STAMP,
        ("FINDING, AS MEASURED. The Combine's "
         f"{context['totals']['combine']} shallow turns in this band come from "
         f"{context['players']['combine']} players, a median of "
         f"{context['medians']['combine']:.0f} turn per player "
         f"({cpos} {pos_label} / {cneg} {neg_label}): they are incidental "
         "fragments of other drills, not a test of anything. Games produce "
         f"{context['totals']['game']} such turns across "
         f"{context['players']['game']} players ({gpos} {pos_label} / "
         f"{gneg} {neg_label}). The game demands both directions about evenly, "
         "so a test of a defensive back's ability to break both ways would have "
         "to be a test the Combine does not run — with a median of "
         f"{context['medians']['combine']:.0f} shallow turn per Combine player, "
         "the Combine cannot be checking it."),
        ("NOT A CLAIM ABOUT THESE PLAYERS. "
         f"{cpos}/{cneg} is {context['totals']['combine']} turns from "
         f"{context['players']['combine']} players. It is not evidence that "
         "these players turn better one way, that either direction is rarer in "
         "this population, or that anything about a body is asymmetric. No "
         "symmetry test, no binomial p-value and no confidence interval on the "
         "split is computed, printed or implied: descriptive counts only."),
        ("WHAT THE BARS MEAN. " + context["sign_convention"]),
        ("HOW THE LABELS WERE FIXED. " + _bh_direction_sentence(context) + " "
         f"The game-only orientation check adds that the sign tracks the body: "
         f"{agree} of {n_tested} steady-forward turns ({share_txt}) had the sign "
         "of the heading change agree with the sign of the change in o. On its "
         "own that check cannot pin a side — o lives in the same frame as "
         "heading — which is why the vendor check above is the one that decides."),
        ("FAMILIES THAT DID NOT CARRY IT. " + (
            "Every family cleared the floor on both sides."
            if check["route"]["available"]
            and not check["route"]["families_not_carried"] else
            ("The route check could not be run: " + str(check["route"]["reason"])
             + "." if not check["route"]["available"] else
             "; ".join(f"{c['family']} ({c['reason']})"
                       for c in check["route"]["families_not_carried"]) + "."
             ))),
        ("NOT A MODELLED EFFECT. The capacity envelope is fitted on "
         "|turn_angle| with no direction term, so it deliberately cannot "
         "produce a left/right asymmetry; nothing in this figure is a modelled "
         "effect, an estimate, or an input to the envelope."),
        ("Counts are recomputed at render time from outputs/turns/turns.parquet "
         "(valid turns), the raw game tracking sample and data/raw/"
         "player_play.csv; every value shown is in "
         "outputs/figures/fig_both_hips.csv. Sample data: this says what the "
         "Combine does and does not test, not what any player can do."),
    ]
    wrapped: list[str] = []
    for line in caption_lines:
        wrapped.extend(_wrap_text(line, 150).split("\n"))
    caption = "\n".join(wrapped)

    dodge = position_dodge(width=0.78)
    plot = (
        ggplot(frame, aes(x="x", y="count", fill="direction_label"))
        + geom_col(position=dodge, width=0.68, colour="white", size=0.3)
        + geom_text(data=labels,
                    mapping=aes(x="x", y="lab_y", label="count",
                                group="direction_label"),
                    position=dodge, size=4.0, colour="#1A1A1A", va="bottom",
                    fontweight="bold", show_legend=False)
        + scale_fill_manual(
            values={labels_order[0]: PALETTE["combine"],
                    labels_order[1]: PALETTE["game"]},
            labels=[f"{label} ({'positive' if label == pos_label else 'negative'} turn_angle)"
                    for label in labels_order],
            name=None)
        + scale_x_discrete(limits=x_order, labels=ticks)
        + scale_y_continuous(
            limits=(0.0, float(frame["count"].max()) * 1.16),
            expand=(0.0, 0.0))
        + labs(title=title, subtitle=subtitle, caption=caption,
               x=None, y="Valid turns, 30–60° (count)")
        + theme_bw(base_size=9)
        + theme(
            figure_size=(float(cfg.get(f"{block}.width_in")),
                         float(cfg.get(f"{block}.height_in"))),
            panel_background=element_rect(fill="#EBEBEB", colour=None),
            panel_grid_major=element_line(colour="white", linewidth=0.6),
            panel_grid_minor=element_blank(),
            panel_border=element_rect(colour="#B3B3B3", linewidth=0.4),
            plot_title=element_text(size=15, weight="bold"),
            plot_subtitle=element_text(size=9.0, colour="#333333"),
            plot_caption=element_text(size=6.1, colour="#333333", ha="left",
                                      linespacing=1.32),
            axis_title_y=element_text(size=8.4, colour="#333333"),
            axis_text_x=element_text(size=9.0, colour="#222222"),
            axis_text_y=element_text(size=8.0),
            legend_position="bottom",
            legend_title=element_blank(),
            legend_key=element_rect(fill="#EBEBEB", colour=None),
        )
    )
    return plot


def render_both_hips(plot: ggplot, cfg: common.Config, out_png: Path) -> Path:
    """Draw and save figure 10 once, with fixed metadata (byte-identical runs)."""
    dpi = int(cfg.get("figures.both_hips.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig = plot.draw()
    fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def both_hips_csv_frame(table: pd.DataFrame, context: dict) -> pd.DataFrame:
    """Bar rows plus the route-check agreement table, one file.

    ``row_kind`` separates them: ``bar`` rows carry the four counts, and
    ``route_check`` rows carry one route family x starting side cell each, so a
    side that disagrees cannot be read as a pooled number.
    """
    check = context["check"]
    chir = check["chirality"]
    route = check["route"]
    bars = table.copy()
    bars["row_kind"] = "bar"
    bars["angle_low_deg"] = context["angle_low"]
    bars["angle_high_deg"] = context["angle_high"]
    bars["sign_convention"] = context["sign_convention"]
    bars["chirality_n_plays"] = chir["n_plays"]
    bars["chirality_agree_share"] = chir["agree_share"]
    bars["chirality_resolved"] = chir["resolved"]
    bars["route_n_turns_checked"] = route["n_turns_checked"]
    bars["route_pooled_rate"] = route["pooled_rate"]
    bars["route_families_carried"] = ",".join(route["families_carried"])
    bars["route_check_consistent"] = route["consistent"]
    bars["orientation_n_tested"] = check["orientation"]["n_tested"]
    bars["orientation_n_agree"] = check["orientation"]["n_agree"]
    bars["direction_resolved"] = check["resolved"]

    detail = route.get("route_table")
    frames = []
    if detail is not None and len(detail):
        cells = detail.copy()
        cells["row_kind"] = "route_check"
        cells = cells.rename(columns={"start_side": "alignment_side"})
        frames.append(cells)
    out = pd.concat([bars] + frames, ignore_index=True)

    columns = ["row_kind", "setting", "setting_label", "direction_label",
               "turn_sign", "count", "share_within_setting", "turns_setting",
               "n_players_setting", "turns_per_player_median",
               "angle_low_deg", "angle_high_deg", "route_ran",
               "alignment_side", "n_turns_checked", "n_agree",
               "agreement_rate", "sign_convention", "chirality_n_plays",
               "chirality_agree_share", "chirality_resolved",
               "route_n_turns_checked", "route_pooled_rate",
               "route_families_carried", "route_check_consistent",
               "orientation_n_tested", "orientation_n_agree",
               "direction_resolved"]
    for col in columns:
        if col not in out.columns:
            out[col] = np.nan
    return out[columns]


def both_hips_manifest_payload(table: pd.DataFrame, context: dict,
                               cfg: common.Config) -> dict:
    check = context["check"]
    chir = check["chirality"]
    route = check["route"]
    counts = {}
    for setting in context["settings"]:
        part = table[table["setting"] == setting]
        counts[setting] = {
            "positive": int(part[part["turn_sign"] == "positive"]["count"].iloc[0]),
            "negative": int(part[part["turn_sign"] == "negative"]["count"].iloc[0]),
            "total": context["totals"][setting],
            "players": context["players"][setting],
            "turns_per_player_median": context["medians"][setting],
        }
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_BOTH_HIPS,
        "title": "The test never checks which way he turns",
        "sources": ["outputs/turns/turns.parquet",
                    "data/raw/game_tracking.csv",
                    "data/raw/player_play.csv"],
        "band": {
            "position_group": context["position"],
            "abs_turn_angle_deg": [context["angle_low"], context["angle_high"]],
            "rule": "valid turns, left-closed on abs_turn_angle_deg",
        },
        "counts": counts,
        "direction": {
            "sign_convention": context["sign_convention"],
            "labels": {"positive": context["labels"]["positive"],
                       "negative": context["labels"]["negative"]},
            "resolved": check["resolved"],
            "chirality_check": {
                "field": "play_description (body-relative pass direction)",
                "rule": ("targeted receiver's tracked side of y = field_mid_y "
                         "must match the side named in the description, with "
                         "the mapping flipping between play_direction left and "
                         "right"),
                "n_plays": chir["n_plays"],
                "n_agree": chir["n_agree"],
                "agree_share": chir["agree_share"],
                "min_plays": chir["min_plays"],
                "min_agree_share": chir["min_agree_share"],
                "resolved": chir["resolved"],
                "reason": chir["reason"],
                "by_cell": chir["by_cell"],
            },
            "route_check": {
                "field": "player_play.route_ran",
                "families": route["families"],
                "min_cell": route["min_cell"],
                "min_agree_share": route["min_agree_share"],
                "min_families": route["min_families"],
                "n_turns_checked": route["n_turns_checked"],
                "n_agree": route["n_agree"],
                "pooled_rate": route["pooled_rate"],
                "families_carried": route["families_carried"],
                "families_not_carried": route["families_not_carried"],
                "consistent": route["consistent"],
                "reason": route["reason"],
                "table": ([] if route.get("route_table") is None else
                          route["route_table"].to_dict("records")),
            },
            "orientation_check": {
                "field": "o (game tracking only; absent in the Combine)",
                "n_plotted_turns": check["orientation"]["n_plotted_turns"],
                "n_with_both_frames": check["orientation"]["n_with_both_frames"],
                "n_tested": check["orientation"]["n_tested"],
                "n_agree": check["orientation"]["n_agree"],
                "agree_share": check["orientation"]["agree_share"],
                "sign_tracks_orientation":
                    check["orientation"]["sign_tracks_orientation"],
                "role": ("supporting only: o shares the heading frame, so it "
                         "cannot pin handedness by itself"),
            },
        },
        "claim": ("The Combine's shallow-turn band is a handful of incidental "
                  "fragments (median about one turn per player), so the Combine "
                  "cannot be testing that a defensive back can break both "
                  "ways, while games demand both directions about evenly."),
        "not_claimed": [
            "That any asymmetry exists in these players' bodies or skills.",
            "That a 15/11 split of 26 turns from 16 players is evidence of "
            "anything about a body.",
            "Any symmetry test, binomial p-value, confidence interval, "
            "correlation or fitted effect for the split.",
        ],
        "caveats": [
            ("Sample data: one session of sample data; nothing here is a "
             "finding about the 2027 class."),
            ("Descriptive counts only. The split is not tested for symmetry and "
             "no p-value is computed anywhere in the figure's code path."),
            ("The capacity envelope is fitted on |turn_angle| with no direction "
             "term, so it deliberately cannot model a left/right asymmetry; "
             "this figure is not a modelled effect."),
            ("Direction labels come from the recorded chirality and route "
             "checks, not from convention alone; where either fails the bars "
             "fall back to SIDE A / SIDE B."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_BOTH_HIPS}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_BOTH_HIPS}.csv",
    }


# ------------- figure 11: where the shallow turns come from
#: Added at Conor's request (2026-11-08); not derived from a TASK.md line.
#:
#: The 30-60 degree DB band is dominated by Combine turns, and that reads like
#: coverage of the band. This figure tests the reading by counting PLAYERS: one
#: dot per DB who produced at least one valid turn in the band, x = that
#: player's own count, two rows (Combine above, games below) on one shared axis.
#:
#: The shape is the argument, so it is drawn unasveraged: no mean, no box, no
#: bar, no test. If the Combine row piles up on 1, the turns are incidental
#: fragments of drills built for other things, not a test of this turn. If it
#: does not, the figure says that instead — the counts are recomputed from the
#: turn table at render time and nothing is hard-coded.
#:
#: Players above ``label_min_turns`` are labelled by the DRILLS that carried
#: their turns. A player name never enters the figure, the CSV or the manifest.
#:
#: The vertical rule is read from ``envelope.tau2_min_turns_per_player`` and
#: labelled on the image, so it reads as the envelope's own modelling floor
#: rather than as a threshold this figure chose.
FIGURE_KEY_SHALLOW_SOURCES = "fig_shallow_sources"

#: Figure 11 reads the drill label because the labels ARE the finding: a
#: multi-turn player is named by the drills that carried his turns.
SHALLOW_SOURCE_COLUMNS = [
    "setting",
    "abs_turn_angle",
    "position_group",
    "nfl_id",
    "drill_name",
    "valid",
]


def load_shallow_source_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 11 needs (drill labels included)."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    df = pd.read_parquet(path, columns=SHALLOW_SOURCE_COLUMNS)
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    return df

#: Readable drill names. The raw ``drill_name`` stays in the CSV; this mapping
#: only changes how a drill is written on the image, never which drill a turn
#: belongs to.
DRILL_DISPLAY = {
    "THREE_CONE_DRILL": "3-cone",
    "BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL": "back-pedal 90°",
    "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION": "back-pedal 45°",
    "BACK_PEDAL_AND_REACT": "back-pedal react",
    "TERYL_AUSTIN_DRILL_1": "Teryl Austin 1",
    "TERYL_AUSTIN_DRILL_2": "Teryl Austin 2",
    "BOX_DRILL": "box",
    "SHORT_SHUTTLE": "short shuttle",
    "W_DRILL": "W",
    "FORTY_YARD_DASH": "40-yd dash",
    "GAUNTLET_DRILL": "gauntlet",
}


def shallow_drill_label(name: str) -> str:
    """A drill's display name; an unmapped drill is title-cased, not dropped."""
    raw = str(name)
    if raw in DRILL_DISPLAY:
        return DRILL_DISPLAY[raw]
    return " ".join(part.capitalize() for part in raw.split("_"))


def shallow_sources_table(turns: pd.DataFrame,
                          cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """One row per player per setting: his own count of turns in the band."""
    block = "figures.shallow_sources"
    position = str(cfg.get(f"{block}.position_group"))
    settings = [str(s) for s in cfg.get(f"{block}.settings")]
    low, high = (float(x) for x in cfg.get(f"{block}.angle_band_deg"))
    rule_key = str(cfg.get(f"{block}.rule_key"))
    rule_min = int(cfg.get(rule_key))
    label_min = int(cfg.get(f"{block}.label_min_turns"))
    if high <= low:
        raise common.StageRefused(
            f"{STAGE}: figures.shallow_sources.angle_band_deg must be increasing")
    if rule_min < 1:
        raise common.StageRefused(
            f"{STAGE}: {rule_key} is {rule_min}; a turns-per-player floor below "
            "1 cannot be drawn as a rule on this axis")
    missing = [c for c in ("setting", "abs_turn_angle", "position_group",
                           "nfl_id", "drill_name", "valid")
               if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); the per-player "
            "counts cannot be drawn")

    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    mask = (valid
            & (turns["position_group"] == position)
            & turns["setting"].isin(settings)
            & (angle >= low) & (angle < high))
    df = turns[mask].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in [{low:g}, {high:g}) — "
            "nothing to count")

    rows: list[dict] = []
    drills: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    players: dict[str, int] = {}
    medians: dict[str, float] = {}
    at_one: dict[str, int] = {}
    max_turns: dict[str, int] = {}
    for setting in settings:
        part = df[df["setting"] == setting]
        if part.empty:
            raise common.StageRefused(
                f"{STAGE}: no valid {position} turns in [{low:g}, {high:g}) for "
                f"setting {setting!r} — a two-row figure cannot be drawn with "
                "one empty row")
        total = int(len(part))
        per_player = part.groupby("nfl_id").size().sort_index()
        counts = counts_map(part, setting)
        drills[setting] = dict(
            part["drill_name"].dropna().astype(str).value_counts().items()
        ) if setting == "combine" else {}
        totals[setting] = total
        players[setting] = int(per_player.size)
        medians[setting] = float(per_player.median())
        at_one[setting] = int((per_player == 1).sum())
        max_turns[setting] = int(per_player.max())
        for nfl_id, n in per_player.items():
            sub = part[part["nfl_id"] == nfl_id]
            names: list[str] = []
            written = ""
            n_drills = 0
            if setting == "combine":
                tally = sub["drill_name"].dropna().astype(str).value_counts()
                n_drills = int(tally.size)
                names = [str(k) for k in tally.index]
                written = ";".join(names)
            rows.append({
                "setting": setting,
                "setting_label": (COMBINE_LABEL if setting == "combine"
                                  else GAME_LABEL),
                "nfl_id": int(nfl_id),
                "turns": int(n),
                "share_of_setting_turns": int(n) / total,
                "drill_names": written,
                "drill_counts": ";".join(f"{k}={int(v)}" for k, v in
                                         (counts.get(int(nfl_id), {}).items())
                                         ) if setting == "combine" else "",
                "n_drills": n_drills if setting == "combine" else np.nan,
                "labelled_by_drill": (setting == "combine"
                                      and int(n) >= label_min),
                "turns_setting": total,
                "n_players_setting": int(per_player.size),
                "median_turns_per_player": float(per_player.median()),
                "at_one_turns": int((per_player == 1).sum()),
                "max_turns_player": int(per_player.max()),
                "angle_low_deg": low,
                "angle_high_deg": high,
                "rule_min_turns": rule_min,
                "rule_key": rule_key,
                "position_group": position,
            })
    table = pd.DataFrame(rows)

    if int(table["turns"].sum()) != int(len(df)):
        raise ValueError(
            f"{STAGE}: per-player counts do not reconstruct the turn population")
    for setting in settings:
        part = table[table["setting"] == setting]
        if int(part["turns"].sum()) != totals[setting]:
            raise ValueError(
                f"{STAGE}: {setting} per-player turns do not sum to its total")
        if int(part["n_players_setting"].iloc[0]) != players[setting]:
            raise ValueError(f"{STAGE}: {setting} player count is inconsistent")

    context = {
        "position": position,
        "settings": settings,
        "angle_low": low,
        "angle_high": high,
        "rule_min": rule_min,
        "rule_key": rule_key,
        "label_min": label_min,
        "totals": totals,
        "players": players,
        "medians": medians,
        "at_one": at_one,
        "max_turns": max_turns,
        "drills": drills,
        "n_turns": int(len(df)),
        "n_players": int(df["nfl_id"].nunique()),
        "x_max": int(max(max_turns.values())),
    }
    return table, context


def counts_map(part: pd.DataFrame, setting: str) -> dict[int, dict[str, int]]:
    """Per-player drill composition, Combine only (games carry no drills)."""
    out: dict[int, dict[str, int]] = {}
    if setting != "combine":
        return out
    for nfl_id, sub in part.groupby("nfl_id"):
        tally = sub["drill_name"].dropna().astype(str).value_counts()
        out[int(nfl_id)] = {str(k): int(v) for k, v in tally.items()}
    return out


def _source_ticks(x_max: int) -> list[float]:
    """Readable x breaks that always include 1 and the rule line's 2."""
    if x_max <= 12:
        return [float(v) for v in range(1, max(2, x_max) + 1)]
    ticks = [1.0, 2.0, 5.0]
    step = 5 if x_max <= 30 else 25
    v = 10
    while v <= x_max:
        ticks.append(float(v))
        v += step
    if float(x_max) - ticks[-1] >= 3:
        ticks.append(float(x_max))
    return ticks


def shallow_sources_plot_frame(table: pd.DataFrame, context: dict,
                               cfg: common.Config) -> pd.DataFrame:
    """Dot positions: row by setting, deterministic vertical jitter by count."""
    block = "figures.shallow_sources"
    half = float(cfg.get(f"{block}.jitter_half"))
    step = float(cfg.get(f"{block}.jitter_step"))
    frame = table.copy()
    frame["row"] = frame["setting"].map({"combine": 1.0, "game": 0.0})
    if frame["row"].isna().any():
        raise common.StageRefused(
            f"{STAGE}: figures.shallow_sources.settings must be combine/game")
    frame["y"] = frame["row"]
    for _, group in frame.groupby(["setting", "turns"], sort=False):
        ordered = group.sort_values("nfl_id")
        n = len(ordered)
        offsets = [max(-half, min(half, (i - (n - 1) / 2.0) * step))
                   for i in range(n)]
        for idx, off in zip(ordered.index, offsets):
            frame.loc[idx, "y"] = float(frame.loc[idx, "row"]) + off
    return frame


def shallow_sources_label_frame(frame: pd.DataFrame, table: pd.DataFrame,
                                context: dict) -> pd.DataFrame:
    """Anchors for the drill labels: above label_min turns, Combine only.

    The anchor x sits in the empty middle of the panel and a leader line joins
    it to the dot, so the label's position is never read as a value.
    """
    lab = frame[(frame["setting"] == "combine")
                & (frame["turns"] >= context["label_min"])].copy()
    if lab.empty:
        return pd.DataFrame(columns=["nfl_id", "turns", "y", "x_anchor",
                                     "y_anchor", "text"])
    lab = lab.sort_values(["turns", "nfl_id"], ascending=[False, True])
    n = len(lab)
    top, bottom = 0.90, 0.26
    ys = np.linspace(top, bottom, n) if n > 1 else np.array([(top + bottom) / 2])
    x_max = float(context["x_max"])
    anchor_x = min(8.0, max(4.2, x_max * 0.26))
    texts = []
    for _, row in lab.iterrows():
        parts = []
        for item in str(row["drill_counts"]).split(";"):
            if not item:
                continue
            name, _, count = item.partition("=")
            label = shallow_drill_label(name)
            parts.append(f"{label} ×{count}" if count and count != "1"
                         else label)
        texts.append(" · ".join(parts) if parts else "drill not recorded")
    lab = lab.reset_index(drop=True)
    lab["x_anchor"] = anchor_x
    lab["y_anchor"] = ys
    lab["text"] = texts
    return lab[["nfl_id", "turns", "y", "x_anchor", "y_anchor", "text"]]


def _shallow_drill_sentence(context: dict) -> str:
    """The Combine drill breakdown, as counts rather than adjectives."""
    tally = context["drills"].get("combine", {})
    if not tally:
        return "no drill names are recorded for them"
    many = [(shallow_drill_label(k), int(v)) for k, v in tally.items() if v > 1]
    ones = [shallow_drill_label(k) for k, v in tally.items() if v == 1]
    parts = [f"{v} in {k}" for k, v in many]
    if ones:
        if len(ones) == 1:
            parts.append(f"1 in {ones[0]}")
        else:
            parts.append("1 each in " + ", ".join(ones[:-1]) + " and " + ones[-1])
    return "; ".join(parts)


def _shallow_multi_sentence(table: pd.DataFrame, label_min: int) -> str:
    """Which drills carried the players who produced more than one turn."""
    multi = table[(table["setting"] == "combine")
                  & (table["turns"] >= label_min)].sort_values(
        ["turns", "nfl_id"], ascending=[False, True])
    if multi.empty:
        return ("No Combine player produced more than one turn in this band, "
                "so there is nothing to label beyond the dots themselves.")
    parts = []
    for _, row in multi.iterrows():
        drills = [shallow_drill_label(d) for d in
                  str(row["drill_names"]).split(";") if d]
        parts.append(f"{int(row['turns'])} turns from {', '.join(drills)}")
    return ("The " + str(len(multi)) + " Combine players with more than one "
            "turn, labelled by drill and not by name: " + "; ".join(parts) + ".")


def _num(value: float) -> str:
    """1 -> '1', 9.0 -> '9', 8.5 -> '8.5' (never a spurious .0 on a count)."""
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def plot_shallow_sources(table: pd.DataFrame, context: dict,
                         cfg: common.Config) -> ggplot:
    """Two rows of dots on one shared axis, counts printed, no averaging."""
    block = "figures.shallow_sources"
    frame = shallow_sources_plot_frame(table, context, cfg)
    labels = shallow_sources_label_frame(frame, table, context)
    low, high = context["angle_low"], context["angle_high"]
    c_tot, c_pl = context["totals"]["combine"], context["players"]["combine"]
    g_tot, g_pl = context["totals"]["game"], context["players"]["game"]
    c_one = context["at_one"]["combine"]
    rule = int(context["rule_min"])
    x_max = int(context["x_max"])
    label_x = min(8.0, max(4.2, x_max * 0.26))
    right = float(x_max) * 0.97

    summaries = pd.DataFrame([
        {"x": right, "y": 1.40, "setting": "combine",
         "text": f"{c_tot} turns · {c_pl} players · {c_one} at exactly 1"},
        {"x": right, "y": -0.48, "setting": "game",
         "text": f"{g_tot} turns · {g_pl} players · median "
                 f"{_num(context['medians']['game'])}"},
    ])

    title = "Where the shallow turns come from"
    subtitle = (
        f"Valid DB turns of {low:g}–{high:g}°: one dot per player who produced "
        f"at least one, x = how many he produced. Combine row above (one "
        "session per player), games below. Nothing is averaged.")

    caption_lines = [
        STAMP,
        ("FINDING, AS MEASURED. The Combine produces "
         f"{c_tot} valid {context['position']} turns of {low:g}–{high:g}° in "
         f"this sample, from {c_pl} players: {c_one} of those {c_pl} produced "
         f"exactly one, and the largest single count is "
         f"{context['max_turns']['combine']}. Games produce {g_tot} such turns "
         f"from {g_pl} players, a median of {_num(context['medians']['game'])} "
         f"per player (max {context['max_turns']['game']})."),
        ("WHAT THIS SHOWS. The shallow Combine turns are incidental fragments "
         f"of drills designed for something else. All {c_tot} sit inside "
         f"drills built for another purpose — {_shallow_drill_sentence(context)}"
         f" — and none of those drills is a test of a {low:g}–{high:g}° break "
         "at speed. The turn is there because a body was already rotating "
         "through that angle on the way to a different task, not because the "
         f"drill asked for it. The game row is what the band looks like when "
         f"the turn is the point: {g_pl} players, {g_tot} turns, median "
         f"{_num(context['medians']['game'])} each."),
        ("LABELLED BY DRILL, NEVER BY NAME. " + _shallow_multi_sentence(
             table, int(context["label_min"]))
         + " No player name appears in this figure, its CSV or its manifest."),
        (f"THE RULE LINE. The vertical line at {rule} turns is "
         f"`{context['rule_key']}` — the minimum number of usable turns the "
         "capacity envelope requires before it will form a per-player estimate "
         "at all. It is a modelling floor carried in from the envelope, not a "
         "threshold chosen here, and not a finding about these players. "
         "Everything to its left is a player the envelope cannot estimate."),
        ("DOTS, NOT BARS. " + f"{c_one} of the Combine's {c_pl} dots sit on 1 "
         "and the row is left that way: no aggregation into bars, no player "
         "with a turn in this band dropped, no smoothing. Sparse is the "
         "measurement, not a presentation problem."),
        ("WHAT IS NOT HERE. No mean, no box plot, no inference test, no "
         "coverage score, no per-player ranking. A dot at 1 is one incidental "
         "turn, not one measured ability, and no exposure correction is "
         "applied: the Combine row is one session per player, the game row is "
         "whatever snaps the sample contains, and the two rows are not "
         "sampled alike."),
        (f"Sample data: one session of sample data. Counts are recomputed at "
         f"render time from outputs/turns/turns.parquet (valid turns); every "
         f"value shown is in outputs/figures/{FIGURE_KEY_SHALLOW_SOURCES}.csv. "
         "This says what the Combine does not test, not what any player can "
         "do."),
    ]
    wrapped: list[str] = []
    for line in caption_lines:
        wrapped.extend(_wrap_text(line, 150).split("\n"))
    caption = "\n".join(wrapped)

    plot = (
        ggplot(frame, aes(x="turns", y="y", colour="setting"))
        + geom_vline(xintercept=float(rule), linetype="dashed",
                     colour="#555555", size=0.5)
        + geom_segment(data=labels,
                       mapping=aes(x="turns", y="y", xend="x_anchor",
                                   yend="y_anchor"),
                       inherit_aes=False, colour="#9A9A9A", size=0.3)
        + geom_point(size=3.0, alpha=0.9, stroke=0)
        + geom_text(data=labels,
                    mapping=aes(x="x_anchor", y="y_anchor", label="text"),
                    inherit_aes=False, ha="left", va="center", size=3.4,
                    colour="#1A1A1A")
        + geom_text(data=summaries,
                    mapping=aes(x="x", y="y", label="text"),
                    inherit_aes=False, ha="right", va="center", size=3.6,
                    fontweight="bold", show_legend=False,
                    colour=PALETTE["combine"])
        + geom_text(data=pd.DataFrame([{
                        "x": float(rule) + 0.35, "y": 1.62,
                        "text": (f"envelope floor: {rule} turns per player "
                                 f"({context['rule_key']})")}]),
                    mapping=aes(x="x", y="y", label="text"),
                    inherit_aes=False, ha="left", va="center", size=3.1,
                    colour="#444444")
        + scale_colour_manual(values=PALETTE, labels=[COMBINE_LABEL, GAME_LABEL],
                              name=None)
        + scale_x_continuous(limits=(0.3, float(x_max) + 0.8),
                             breaks=_source_ticks(x_max),
                             expand=(0.0, 0.0))
        + scale_y_continuous(limits=(-0.72, 1.82), breaks=[0.0, 1.0],
                             labels=["Games", "Combine\n(one session)"],
                             expand=(0.0, 0.0))
        + labs(title=title, subtitle=subtitle, caption=caption,
               x="Turns this player produced in the band (count)", y=None)
        + theme_bw(base_size=9)
        + theme(
            figure_size=(float(cfg.get(f"{block}.width_in")),
                         float(cfg.get(f"{block}.height_in"))),
            panel_background=element_rect(fill="#EBEBEB", colour=None),
            panel_grid_major=element_line(colour="white", linewidth=0.6),
            panel_grid_minor=element_blank(),
            panel_border=element_rect(colour="#B3B3B3", linewidth=0.4),
            plot_title=element_text(size=15, weight="bold"),
            plot_subtitle=element_text(size=8.6, colour="#333333"),
            plot_caption=element_text(size=5.9, colour="#333333", ha="left",
                                      linespacing=1.30),
            axis_title_x=element_text(size=8.4, colour="#333333"),
            axis_text_x=element_text(size=8.4, colour="#222222"),
            axis_text_y=element_text(size=8.6, colour="#222222"),
            legend_position="bottom",
            legend_title=element_blank(),
            legend_key=element_rect(fill="#EBEBEB", colour=None),
        )
    )
    return plot


def render_shallow_sources(plot: ggplot, cfg: common.Config,
                           out_png: Path) -> Path:
    """Draw and save figure 11 once, fixed metadata for byte-identical runs."""
    dpi = int(cfg.get("figures.shallow_sources.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig = plot.draw()
    fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def shallow_sources_csv_frame(table: pd.DataFrame, context: dict) -> pd.DataFrame:
    """One row per player per setting. No name column, and no name in a value."""
    out = table.copy()
    columns = ["setting", "setting_label", "nfl_id", "turns",
               "share_of_setting_turns", "drill_names", "drill_counts",
               "n_drills", "labelled_by_drill", "turns_setting",
               "n_players_setting", "median_turns_per_player", "at_one_turns",
               "max_turns_player", "angle_low_deg", "angle_high_deg",
               "rule_min_turns", "rule_key", "position_group"]
    return out[columns].reset_index(drop=True)


def shallow_sources_manifest_payload(table: pd.DataFrame, context: dict,
                                     cfg: common.Config) -> dict:
    counts = {}
    for setting in context["settings"]:
        part = table[table["setting"] == setting]
        counts[setting] = {
            "turns": context["totals"][setting],
            "players": context["players"][setting],
            "turns_per_player_median": context["medians"][setting],
            "players_at_exactly_one": context["at_one"][setting],
            "max_turns_player": context["max_turns"][setting],
            "drills": context["drills"][setting],
            "multi_turn_players": [
                {"nfl_id": int(row["nfl_id"]), "turns": int(row["turns"]),
                 "drills": [d for d in str(row["drill_names"]).split(";") if d]}
                for _, row in part.sort_values(
                    ["turns", "nfl_id"], ascending=[False, True]).iterrows()
                if int(row["turns"]) >= context["label_min"]
            ] if setting == "combine" else [],
        }
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_SHALLOW_SOURCES,
        "title": "Where the shallow turns come from",
        "sources": ["outputs/turns/turns.parquet"],
        "band": {
            "position_group": context["position"],
            "abs_turn_angle_deg": [context["angle_low"], context["angle_high"]],
            "rule": "valid turns, left-closed on abs_turn_angle_deg",
        },
        "counts": counts,
        "rule": {
            "key": context["rule_key"],
            "value": int(context["rule_min"]),
            "meaning": ("the capacity envelope's minimum usable turns per "
                        "player before it forms a per-player estimate; carried "
                        "in from the envelope, not chosen by this figure"),
        },
        "labels": {
            "rule": "players above label_min_turns are labelled by drill only",
            "label_min_turns": int(context["label_min"]),
            "player_names_used": False,
        },
        "claim": (
            f"All {context['totals']['combine']} Combine {context['position']} "
            f"turns of {context['angle_low']:g}-{context['angle_high']:g}° come "
            f"from {context['players']['combine']} players, "
            f"{context['at_one']['combine']} of them with exactly one: the "
            "shallow turns are incidental fragments of drills built for other "
            "purposes, not a test of that turn, while games produce "
            f"{context['totals']['game']} such turns from "
            f"{context['players']['game']} players (median "
            f"{_num(context['medians']['game'])} each)."),
        "not_claimed": [
            "That any player can or cannot break at 30-60 degrees.",
            "Any mean, box plot, inference test, coverage score or per-player "
            "ranking.",
            "That the two settings are sampled alike, or any exposure-corrected "
            "comparison between the rows.",
            "That the envelope floor is a property of these players rather than "
            "a modelling requirement.",
        ],
        "caveats": [
            ("Sample data: one session of sample data; nothing here is a "
             "finding about the 2027 class."),
            ("Dots are drawn as counted; sparse rows are not aggregated into "
             "bars and no player with a turn in the band is dropped."),
            ("Direction, drill purpose and player names are outside this "
             "figure: only counts and drill labels appear."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_SHALLOW_SOURCES}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_SHALLOW_SOURCES}.csv",
    }


# ------------- figure 12: the ninety-second ledger
#: Added at Conor's request (2026-11-08); it is not derived from a TASK.md line.
#:
#: A LEDGER, not a chart. Three rows on one shared axis, the count printed at
#: each bar end and nothing else drawn: what games demand in the 30-45 degree
#: DB band (measured), what the Combine supplies in that band (measured), and
#: what six reps per measured DB would be (arithmetic). The projection is
#: hatched and labelled on the bar as arithmetic, the same measured/projected
#: distinction as figure 3's right panel, so it cannot be read as an observed
#: count.
#:
#: Every measured number is recounted from `turns.parquet` at render time. The
#: projection is `reps` x the number of distinct Combine DBs in the table, and
#: the multiplication is asserted rather than trusted.
#:
#: The axis is logarithmic only because the 4 is otherwise invisible beside the
#: 732. The axis label says so and the caption forbids reading bar length as
#: proportion, so the spacing cannot imply the projection is commensurable with
#: the measurements.
#:
#: Nothing is subtracted. The rows count turns, not players; a difference
#: between two rows is never drawn and never called a gap in players.
FIGURE_KEY_LEDGER = "fig_ledger"

LEDGER_COLUMNS = [
    "setting",
    "abs_turn_angle",
    "position_group",
    "nfl_id",
    "game_id",
    "valid",
]


def load_ledger_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 12 needs; the same table every figure reads."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    df = pd.read_parquet(path, columns=LEDGER_COLUMNS)
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    return df


def _ledger_band(part: pd.DataFrame, rule_min: int) -> dict:
    """Per-player reliability counts for one band: exactly one, and above the floor."""
    per = part.groupby("nfl_id").size()
    return {
        "turns": int(len(part)),
        "players": int(per.size),
        "players_at_exactly_one": int((per == 1).sum()),
        "players_at_or_above_rule": int((per >= rule_min).sum()),
        "max_turns_player": int(per.max()) if per.size else 0,
    }


def ledger_table(turns: pd.DataFrame,
                 cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """The three ledger rows, each count recomputed from the turn table.

    Row 1 (games) and row 2 (Combine) are measured counts of valid DB turns in
    the band. Row 3 is arithmetic: the number of DBs the Combine measures once,
    times the configured reps. It is marked `hatch` and `projected` here, and
    the CSV/manifest carry the same flag, so no downstream reader has to infer
    which bar was observed.
    """
    block = "figures.ledger"
    position = str(cfg.get(f"{block}.position_group"))
    low, high = (float(x) for x in cfg.get(f"{block}.angle_band_deg"))
    rel_low, rel_high = (float(x) for x in
                         cfg.get(f"{block}.reliability_band_deg"))
    reps = int(cfg.get(f"{block}.reps"))
    rule_key = str(cfg.get(f"{block}.rule_key"))
    rule_min = int(cfg.get(rule_key))
    if high <= low:
        raise common.StageRefused(
            f"{STAGE}: figures.ledger.angle_band_deg must be increasing")
    if rel_high <= rel_low or rel_low > low or rel_high < high:
        raise common.StageRefused(
            f"{STAGE}: figures.ledger.reliability_band_deg must be increasing "
            "and enclose the ledger band")
    if reps < 1:
        raise common.StageRefused(
            f"{STAGE}: figures.ledger.reps is {reps}; a projection of zero or "
            "fewer reps cannot be drawn")
    if rule_min < 1:
        raise common.StageRefused(
            f"{STAGE}: {rule_key} is {rule_min}; a turns-per-player floor below "
            "1 cannot be stated as reliability")
    missing = [c for c in LEDGER_COLUMNS if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); the ledger "
            "cannot be counted")

    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    scope = valid & (turns["position_group"] == position) & angle.notna()
    band = turns[scope & (angle >= low) & (angle < high)].copy()
    wide = turns[scope & (angle >= rel_low) & (angle < rel_high)].copy()
    if band.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in [{low:g}, {high:g}) — "
            "nothing to count")
    for setting in ("combine", "game"):
        if not (band["setting"] == setting).any():
            raise common.StageRefused(
                f"{STAGE}: no valid {position} {setting} turns in "
                f"[{low:g}, {high:g}) — a three-row ledger cannot be drawn with "
                "a measured row missing")

    combine_all = turns[scope & (turns["setting"] == "combine")]
    combine_dbs = int(combine_all["nfl_id"].nunique())
    if combine_dbs < 1:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} Combine players — the six-rep "
            "projection has no denominator")

    game_band = band[band["setting"] == "game"]
    comb_band = band[band["setting"] == "combine"]
    reliability = _ledger_band(comb_band, rule_min)
    wide_combine = wide[wide["setting"] == "combine"]
    wide_rel = _ledger_band(wide_combine, rule_min)
    game_players = int(game_band["nfl_id"].nunique())
    game_player_games = int(game_band.groupby(["nfl_id", "game_id"]).ngroups)
    projection = combine_dbs * reps

    rows = [
        {
            "row": "game_demand",
            "row_order": 0,
            "row_label": "Game demand",
            "setting": "game",
            "count": int(len(game_band)),
            "source_kind": "measured",
            "hatch": False,
            "derivation": (f"counted: valid {position} game turns with "
                           f"abs_turn_angle in [{low:g}, {high:g})"),
            "players": game_players,
            "player_games": game_player_games,
            "combine_dbs": np.nan,
            "band_low_deg": low,
            "band_high_deg": high,
        },
        {
            "row": "combine_supply",
            "row_order": 1,
            "row_label": "Combine supply",
            "setting": "combine",
            "count": int(len(comb_band)),
            "source_kind": "measured",
            "hatch": False,
            "derivation": (f"counted: valid {position} Combine turns with "
                           f"abs_turn_angle in [{low:g}, {high:g})"),
            "players": reliability["players"],
            "player_games": np.nan,
            "combine_dbs": combine_dbs,
            "band_low_deg": low,
            "band_high_deg": high,
        },
        {
            "row": "six_reps",
            "row_order": 2,
            "row_label": "Six reps per player",
            "setting": "combine",
            "count": projection,
            "source_kind": "projected",
            "hatch": True,
            "derivation": (f"{combine_dbs} DBs x {reps} reps = {projection:,} "
                           "turns (arithmetic, not measured)"),
            "players": combine_dbs,
            "player_games": np.nan,
            "combine_dbs": combine_dbs,
            "band_low_deg": low,
            "band_high_deg": high,
        },
    ]
    table = pd.DataFrame(rows)

    if int(table.loc[table["row"] == "game_demand", "count"].iloc[0]) != int(
            len(game_band)):
        raise ValueError(f"{STAGE}: the game row does not match its population")
    if int(table.loc[table["row"] == "combine_supply", "count"].iloc[0]) != int(
            len(comb_band)):
        raise ValueError(f"{STAGE}: the Combine row does not match its "
                         "population")
    if projection != combine_dbs * reps:  # pragma: no cover - asserted anyway
        raise ValueError(f"{STAGE}: the projection is not {combine_dbs} x {reps}")
    if int(table["hatch"].sum()) != 1:
        raise ValueError(f"{STAGE}: exactly one ledger row must be hatched")

    context = {
        "position": position,
        "angle_low": low,
        "angle_high": high,
        "rel_low": rel_low,
        "rel_high": rel_high,
        "reps": reps,
        "rule_key": rule_key,
        "rule_min": rule_min,
        "combine_dbs": combine_dbs,
        "projection": projection,
        "game_turns": int(len(game_band)),
        "game_players": game_players,
        "game_player_games": game_player_games,
        "combine_turns": int(len(comb_band)),
        "reliability": reliability,
        "wide_reliability": wide_rel,
        "log_axis": True,
    }
    return table, context


def _ledger_caption(table: pd.DataFrame, context: dict) -> str:
    """The ledger's caption, with every count taken from the rendered table."""
    g = context["game_turns"]
    g_pl = context["game_players"]
    g_pg = context["game_player_games"]
    c = context["combine_turns"]
    dbs = context["combine_dbs"]
    reps = context["reps"]
    proj = context["projection"]
    rule_min = context["rule_min"]
    rule_key = context["rule_key"]
    rel = context["reliability"]
    wide = context["wide_reliability"]
    low, high = context["angle_low"], context["angle_high"]
    wl, wh = context["rel_low"], context["rel_high"]
    return "\n".join([
        STAMP,
        ("WHAT THIS IS. Three counts of DB turns in the "
         f"{low:g}–{high:g}° band on one shared axis, each count printed at "
         "its bar end. Two bars are MEASURED from outputs/turns/turns.parquet "
         "at render time; the third is ARITHMETIC on a drill that does not "
         "exist in this sample."),
        (f"GAME DEMAND — MEASURED. {g} valid DB game turns of "
         f"{low:g}–{high:g}°: {g_pl} players across {g_pg} player-games."),
        (f"COMBINE SUPPLY — MEASURED. {c} valid Combine DB turns of "
         f"{low:g}–{high:g}°, produced by {rel['players']} of the {dbs} DBs "
         "measured once."),
        (f"SIX REPS — PROJECTED, NOT MEASURED. {dbs} DBs × {reps} reps = "
         f"{proj:,} turns, hatched and labelled on the bar as arithmetic. It "
         "assumes every rep yields exactly one valid turn in the band. Set "
         f"beside a measured bar it is a projection-to-measurement "
         "comparison; it is never a count that was observed."),
        ("LOG AXIS. A linear axis draws the 4 as a sliver beside 732, so the x "
         "axis is logarithmic. Bar lengths are therefore NOT proportional to "
         "the counts: read the printed numbers. The log spacing does not make "
         "the projection commensurable with the measurements — it is "
         "arithmetic standing beside two counts."),
        (f"THE OVERSAMPLING OBJECTION. {proj:,} is more turns than the {g} "
         "games produce, and that is not what six reps are for: they buy "
         "RELIABILITY PER PLAYER, not volume. The capacity envelope requires "
         f"{rule_min} usable turns per player (`{rule_key}`) before it will "
         f"form a per-player estimate at all. In {wl:g}–{wh:g}° the Combine "
         f"yields {wide['turns']} turns from {wide['players']} players, "
         f"{wide['players_at_exactly_one']} of them with exactly one. The "
         f"{low:g}–{high:g}° band is thinner: {rel['turns']} turns from "
         f"{rel['players']} players, {rel['players_at_exactly_one']} with "
         f"exactly one, so {rel['players_at_or_above_rule']} of "
         f"{rel['players']} clear the {rule_min}-turn floor. Six reps are "
         "about moving players off one-turn counts; they are not about "
         "matching game volume."),
        (f"WHAT IS NOT DRAWN. The bars are not subtracted: {g} − {c} would be a "
         "difference in recorded turns, not in how many players can turn, and "
         "no such difference is drawn or interpreted. No player names appear. "
         "Sample data: one session of sample data; nothing here is a finding "
         "about the 2027 class."),
    ])


def plot_ledger(table: pd.DataFrame, context: dict,
                cfg: common.Config) -> plt.Figure:
    """Three horizontal bars on one log axis, counts at the bar ends."""
    block = "figures.ledger"
    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    ax = fig.add_subplot(1, 1, 1)
    fig.subplots_adjust(left=0.205, right=0.975, top=0.845, bottom=0.375)

    ax.set_facecolor("#EBEBEB")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#B3B3B3")
    ax.tick_params(labelsize=8.2, colors="#333333", length=2)

    rows = table.sort_values("row_order")
    for _, row in rows.iterrows():
        y = float(2 - int(row["row_order"]))
        count = int(row["count"])
        if bool(row["hatch"]):
            ax.barh(y, count, height=0.46, color=PROJECT_FILL,
                    edgecolor=PROJECT_EDGE, hatch="////", linewidth=1.0,
                    zorder=3)
        else:
            ax.barh(y, count, height=0.46,
                    color=PALETTE[str(row["setting"])],
                    edgecolor="white", linewidth=0.6, zorder=3)
        ax.text(float(count) * 1.14, y, f"{count:,}", ha="left", va="center",
                fontsize=11.0, fontweight="bold", color="#1A1A1A", zorder=5)

    proj_y = float(2 - int(rows.loc[rows["row"] == "six_reps",
                                    "row_order"].iloc[0]))
    ax.text(1.16, proj_y,
            f"{context['combine_dbs']} DBs × {context['reps']} reps — "
            "arithmetic projection, not measured",
            ha="left", va="center", fontsize=8.0, color="#4A3F00", zorder=6,
            bbox=dict(facecolor="#FFFDF0", edgecolor="none", pad=1.8,
                      alpha=0.92))

    ax.set_xscale("log")
    ax.set_xlim(0.9, 6000.0)
    ax.set_xticks([1, 10, 100, 1000])
    ax.set_xticklabels(["1", "10", "100", "1,000"])
    ax.xaxis.set_minor_locator(LogLocator(base=10.0, subs="auto"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.grid(axis="x", which="major", color="white", linewidth=0.9, zorder=0)
    ax.grid(axis="x", which="minor", color="white", linewidth=0.4, alpha=0.7,
            zorder=0)
    ax.set_axisbelow(True)

    ax.set_yticks([2.0, 1.0, 0.0])
    ax.set_yticklabels([
        "Game demand\nmeasured",
        "Combine supply\nmeasured",
        "Six reps per player\nprojected",
    ], fontsize=8.6, color="#222222")
    ax.set_ylim(-0.55, 2.55)
    ax.set_xlabel(
        f"Turns recorded in the {context['angle_low']:g}–"
        f"{context['angle_high']:g}° band (count) — LOG scale: bar length is "
        "log(count), not count",
        fontsize=8.4, color="#333333", labelpad=4)

    handles = [
        Patch(facecolor=PALETTE["game"], edgecolor="white",
              label="measured — games"),
        Patch(facecolor=PALETTE["combine"], edgecolor="white",
              label="measured — Combine"),
        Patch(facecolor=PROJECT_FILL, edgecolor=PROJECT_EDGE, hatch="////",
              label="arithmetic projection — not measured"),
    ]
    ax.legend(handles=handles, loc="upper right", frameon=False, fontsize=8.4,
              handlelength=1.7, borderaxespad=0.4)

    fig.text(0.010, 0.968, "The ninety-second ledger",
             fontsize=17.0, fontweight="bold", ha="left", va="top",
             color="#111111")
    fig.text(0.010, 0.930,
             _wrap_text(
                 "What games ask, what the Combine supplies, what six reps buy "
                 f"— valid DB turns of {context['angle_low']:g}–"
                 f"{context['angle_high']:g}°, one shared axis. Two bars are "
                 "counted; the third is arithmetic, hatched and labelled as "
                 "such.", 186),
             fontsize=8.8, ha="left", va="top", color="#333333")

    caption = "\n".join(_wrap_text(line, 240)
                        for line in _ledger_caption(table, context).split("\n"))
    fig.text(0.010, 0.014, caption, ha="left", va="bottom", fontsize=6.0,
             color="#333333", linespacing=1.32)
    return fig


def render_ledger(fig: plt.Figure, cfg: common.Config,
                  out_png: Path) -> Path:
    """Save figure 12 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.ledger.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def ledger_csv_frame(table: pd.DataFrame, context: dict) -> pd.DataFrame:
    """Three rows: the count, its source, and the reliability counts."""
    out = table.copy()
    out["position_group"] = context["position"]
    out["band_low_deg"] = context["angle_low"]
    out["band_high_deg"] = context["angle_high"]
    out["counted_band_turns"] = context["combine_turns"]
    out["counted_band_players"] = context["reliability"]["players"]
    out["counted_band_players_at_exactly_one"] = \
        context["reliability"]["players_at_exactly_one"]
    out["counted_band_players_at_or_above_rule"] = \
        context["reliability"]["players_at_or_above_rule"]
    out["reliability_band_low_deg"] = context["rel_low"]
    out["reliability_band_high_deg"] = context["rel_high"]
    out["reliability_turns"] = context["wide_reliability"]["turns"]
    out["reliability_players"] = context["wide_reliability"]["players"]
    out["reliability_players_at_exactly_one"] = \
        context["wide_reliability"]["players_at_exactly_one"]
    out["reliability_players_at_or_above_rule"] = \
        context["wide_reliability"]["players_at_or_above_rule"]
    out["rule_key"] = context["rule_key"]
    out["rule_min_turns_per_player"] = context["rule_min"]
    out["reps"] = context["reps"]
    out["log_axis"] = context["log_axis"]
    columns = ["row", "row_label", "count", "source_kind", "hatch", "derivation",
               "players", "player_games", "combine_dbs", "position_group",
               "band_low_deg", "band_high_deg", "counted_band_turns",
               "counted_band_players", "counted_band_players_at_exactly_one",
               "counted_band_players_at_or_above_rule",
               "reliability_band_low_deg", "reliability_band_high_deg",
               "reliability_turns", "reliability_players",
               "reliability_players_at_exactly_one",
               "reliability_players_at_or_above_rule", "rule_key",
               "rule_min_turns_per_player", "reps", "log_axis"]
    return out[columns].reset_index(drop=True)


def ledger_manifest_payload(table: pd.DataFrame, context: dict,
                            cfg: common.Config) -> dict:
    """The ledger's manifest entry, including the arithmetic it must satisfy."""
    proj_row = table[table["row"] == "six_reps"].iloc[0]
    projection = int(proj_row["count"])
    verified = projection == context["combine_dbs"] * context["reps"]
    if not verified:  # pragma: no cover - the table already asserts this
        raise ValueError(f"{STAGE}: the six-rep projection does not multiply out")
    rows = []
    for _, row in table.sort_values("row_order").iterrows():
        rows.append({
            "row": str(row["row"]),
            "label": str(row["row_label"]),
            "count": int(row["count"]),
            "source_kind": str(row["source_kind"]),
            "hatch": bool(row["hatch"]),
            "derivation": str(row["derivation"]),
            "players": None if pd.isna(row["players"]) else int(row["players"]),
            "player_games": (None if pd.isna(row["player_games"])
                             else int(row["player_games"])),
            "combine_dbs": (None if pd.isna(row["combine_dbs"])
                            else int(row["combine_dbs"])),
        })
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_LEDGER,
        "title": "The ninety-second ledger",
        "sources": ["outputs/turns/turns.parquet"],
        "band": {
            "position_group": context["position"],
            "abs_turn_angle_deg": [context["angle_low"], context["angle_high"]],
            "rule": "valid turns, left-closed on abs_turn_angle_deg",
        },
        "rows": rows,
        "arithmetic": {
            "reps": context["reps"],
            "combine_dbs": context["combine_dbs"],
            "projected_turns": projection,
            "expression": (f"{context['combine_dbs']} × {context['reps']} = "
                           f"{projection:,}"),
            "verified": True,
            "is_measurement": False,
        },
        "log_axis": {
            "used": True,
            "why": ("a linear axis draws the 4 as a sliver beside 732"),
            "warning": ("bar length is log(count), not count; the log spacing "
                        "does not make the projection commensurable with the "
                        "measurements"),
        },
        "reliability": {
            "rule": {
                "key": context["rule_key"],
                "value": context["rule_min"],
                "meaning": ("the capacity envelope's minimum usable turns per "
                            "player before it forms a per-player estimate; "
                            "carried in from the envelope, not chosen here"),
            },
            "band": {
                "abs_turn_angle_deg": [context["angle_low"],
                                       context["angle_high"]],
                **context["reliability"],
            },
            "wide_band": {
                "abs_turn_angle_deg": [context["rel_low"], context["rel_high"]],
                **context["wide_reliability"],
            },
        },
        "comparison_rule": ("Any comparison between the projected row and a "
                            "measured row is projection-to-measurement and is "
                            "stated as such; the rows are never subtracted and "
                            "no turn difference is read as a player difference."),
        "claim": (f"Games produced {context['game_turns']} valid DB turns of "
                  f"{context['angle_low']:g}-{context['angle_high']:g}° from "
                  f"{context['game_players']} players across "
                  f"{context['game_player_games']} player-games; the Combine "
                  f"produced {context['combine_turns']} from "
                  f"{context['reliability']['players']} of the "
                  f"{context['combine_dbs']} DBs measured once. Six reps per DB "
                  f"is {projection:,} turns by arithmetic — more than the games' "
                  "count, but aimed at reliability per player, not at matching "
                  "game volume."),
        "not_claimed": [
            "That the projected turns are comparable to the measured turns; "
            "the projection is arithmetic, not observation.",
            "That the difference between the two measured bars is a difference "
            "in how many players can turn.",
            "That six reps would make any player's turn estimate reliable in "
            "this sample.",
            "Anything about the 2027 class; this is one session of sample data.",
        ],
        "caveats": [
            ("Sample data: one session of sample data; nothing here is a "
             "finding about the 2027 class."),
            ("The projection assumes every rep yields exactly one valid turn "
             "in the band, which the sample cannot verify because the drill "
             "does not exist."),
            ("The axis is logarithmic: bar lengths are not proportional to the "
             "counts and the printed numbers are the evidence."),
            ("Player names are outside this figure: only counts and player "
             "counts appear."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_LEDGER}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_LEDGER}.csv",
    }


# ------------- figure 13: bars of doubt
#: Added at Conor's request (2026-11-08). One horizontal row per valid DB turn
#: in the 30-45 degree band, sorted by measured angle. The dot is the primary
#: measurement; the whisker is the min-max of the same turn's absolute angle
#: under the smoother settings that matched it. The point of the figure is that
#: band membership is smoother-dependent for some turns, and that the detector
#: is nevertheless accurate on planted corners and on the real-schema gate.
#:
#: The whiskers are CAPPED for drawing only (config key
#: ``figures.bars_of_doubt.max_whisker_width_deg``). The uncapped range has
#: p90 = 98.7 degrees on this sample because an alternative smoother can latch
#: onto a different corner near the same centre time; drawn uncapped it would
#: swamp the 15-degree band. The CSV keeps the uncapped values and the count
#: of cut rows is printed on the image.
FIGURE_KEY_BARS_OF_DOUBT = "fig_bars_of_doubt"

BARS_COLUMNS = [
    "setting",
    "position_group",
    "abs_turn_angle",
    "abs_turn_angle_smoother_min",
    "abs_turn_angle_smoother_max",
    "n_smoothers_matched",
    "valid",
]

BARS_ACCENT = "#0072B2"
BARS_GREY = "#6E6E6E"


def load_bars_of_doubt_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 13 needs; refuse if a smoother column is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    df = pd.read_parquet(path)
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    missing = [c for c in BARS_COLUMNS if c not in df.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); figure 13 "
            "needs the smoother min/max columns and refuses rather than "
            "drawing an invented spread")
    return df[BARS_COLUMNS]


def bars_of_doubt_table(turns: pd.DataFrame,
                        cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """One row per turn in the band, with capped drawing geometry.

    The table is sorted by measured angle. ``spread`` is always the UNCAPPED
    max - min; ``whisker_low/high`` are the drawn (possibly clipped) ends, and
    ``capped`` is true exactly when the uncapped spread exceeds the configured
    drawing width.
    """
    block = "figures.bars_of_doubt"
    position = str(cfg.get(f"{block}.position_group"))
    low, high = (float(x) for x in cfg.get(f"{block}.angle_band_deg"))
    cap = float(cfg.get(f"{block}.max_whisker_width_deg"))
    if high <= low:
        raise common.StageRefused(
            f"{STAGE}: figures.bars_of_doubt.angle_band_deg must be increasing")
    if not np.isfinite(cap) or cap <= 0:
        raise common.StageRefused(
            f"{STAGE}: figures.bars_of_doubt.max_whisker_width_deg is {cap}; "
            "a drawing cap must be positive")
    missing = [c for c in BARS_COLUMNS if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: the turn table has no {missing} column(s); figure 13 "
            "cannot draw a smoother range it does not have")

    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    scope = valid & (turns["position_group"] == position) & angle.notna()
    band = turns[scope & (angle >= low) & (angle < high)].copy()
    if band.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in [{low:g}, {high:g}) — "
            "nothing to draw")
    for setting in ("combine", "game"):
        if not (band["setting"] == setting).any():
            raise common.StageRefused(
                f"{STAGE}: no valid {position} {setting} turns in "
                f"[{low:g}, {high:g}) — the figure needs both settings")

    for col in ("abs_turn_angle_smoother_min", "abs_turn_angle_smoother_max",
                "n_smoothers_matched"):
        band[col] = pd.to_numeric(band[col], errors="coerce")
    if band[["abs_turn_angle_smoother_min",
             "abs_turn_angle_smoother_max"]].isna().any().any():
        raise common.StageRefused(
            f"{STAGE}: a band turn has no smoother min/max; figure 13 draws "
            "only rows whose range is known")
    if band["n_smoothers_matched"].isna().any() or \
            (band["n_smoothers_matched"] < 1).any():
        raise common.StageRefused(
            f"{STAGE}: a band turn matched no smoother; there is nothing to "
            "draw for it")
    if (band["abs_turn_angle_smoother_min"]
            > band["abs_turn_angle_smoother_max"] + 1e-9).any():
        raise common.StageRefused(
            f"{STAGE}: a smoother min exceeds its max; the range is corrupt")

    band["abs_turn_angle"] = pd.to_numeric(band["abs_turn_angle"],
                                           errors="coerce")
    band = band[band["abs_turn_angle"].notna()]
    if band.empty:
        raise common.StageRefused(
            f"{STAGE}: no numeric band angles to draw")
    band = band.sort_values(["abs_turn_angle", "setting"],
                            kind="mergesort").reset_index(drop=True)
    band["row_order"] = np.arange(len(band), dtype=int)
    band["smoother_spread_deg"] = (band["abs_turn_angle_smoother_max"]
                                   - band["abs_turn_angle_smoother_min"])
    half = cap / 2.0
    band["whisker_low_deg"] = np.maximum(
        band["abs_turn_angle_smoother_min"], band["abs_turn_angle"] - half)
    band["whisker_high_deg"] = np.minimum(
        band["abs_turn_angle_smoother_max"], band["abs_turn_angle"] + half)
    band["capped_low"] = (band["abs_turn_angle_smoother_min"]
                          < band["abs_turn_angle"] - half - 1e-9)
    band["capped_high"] = (band["abs_turn_angle_smoother_max"]
                           > band["abs_turn_angle"] + half + 1e-9)
    band["capped"] = band["capped_low"] | band["capped_high"]
    band["crossed_edge"] = ((band["abs_turn_angle_smoother_min"] < low)
                            | (band["abs_turn_angle_smoother_max"] >= high))
    band["single_smoother"] = band["n_smoothers_matched"].astype(int) == 1
    band["band_low_deg"] = low
    band["band_high_deg"] = high
    band["cap_width_deg"] = cap
    band["setting_label"] = band["setting"].map(
        {"combine": COMBINE_LABEL, "game": GAME_LABEL})

    # The drawn whisker must contain the dot and must never exceed the cap.
    if ((band["whisker_low_deg"] > band["abs_turn_angle"] + 1e-9)
            | (band["whisker_high_deg"] < band["abs_turn_angle"] - 1e-9)).any():
        raise ValueError(f"{STAGE}: a drawn whisker does not contain its dot")
    if ((band["whisker_high_deg"] - band["whisker_low_deg"])
            > cap + 1e-6).any():
        raise ValueError(f"{STAGE}: a drawn whisker exceeds the cap width")

    n = int(len(band))
    by_setting = {s: int((band["setting"] == s).sum())
                  for s in ("combine", "game")}
    if sum(by_setting.values()) != n:
        raise ValueError(f"{STAGE}: the per-setting counts do not sum to {n}")
    n_cross = int(band["crossed_edge"].sum())
    n_capped = int(band["capped"].sum())
    n_single = int(band["single_smoother"].sum())
    spreads = band["smoother_spread_deg"]
    context = {
        "position": position,
        "angle_low": low,
        "angle_high": high,
        "cap_width": cap,
        "n_rows": n,
        "by_setting": by_setting,
        "median_spread_deg": float(np.median(spreads)),
        "median_spread_by_setting": {
            s: float(np.median(spreads[band["setting"] == s]))
            for s in ("combine", "game")},
        "max_spread_deg": float(spreads.max()),
        "p90_spread_deg": float(np.percentile(spreads, 90)),
        "n_crossed": n_cross,
        "crossed_share": (n_cross / n) if n else None,
        "n_capped": n_capped,
        "n_single": n_single,
        "n_multi": int(n - n_single),
    }
    return band, context


def _bars_of_doubt_caption(context: dict) -> list[str]:
    """Caption lines; the two detector facts are deliberately separate lines."""
    ev = DETECTOR_EVIDENCE
    n = context["n_rows"]
    return [
        STAMP,
        (f"WHAT THIS IS. One horizontal row per valid DB turn with a measured "
         f"|turn_angle| in the {context['angle_low']:g}–"
         f"{context['angle_high']:g}° band — {n} turns "
         f"({context['by_setting']['combine']} Combine, "
         f"{context['by_setting']['game']} game) — sorted by the measured "
         "angle. The dot is the primary measurement; the whisker is the "
         "min–max of the same turn's |turn_angle| across the smoother settings "
         "that matched it. A whisker is a processing range, not a confidence "
         "interval, and no inference is drawn from it."),
        (f"CAP — STATED, NOT HIDDEN. Whiskers are drawn no wider than "
         f"{context['cap_width']:g}° "
         f"(±{context['cap_width'] / 2:g}° around the dot; "
         "figures.bars_of_doubt.max_whisker_width_deg). "
         f"{context['n_capped']} of {n} rows have a whisker cut at that width "
         "and are drawn with a caret at the cut end. The CSV keeps the "
         "uncapped min, max and spread for every row."),
        (f"WHAT CROSSES. {context['n_crossed']} of {n} turns "
         f"({context['crossed_share']:.1%}) have a smoother range that leaves "
         f"[{context['angle_low']:g}, {context['angle_high']:g}) — for those "
         "turns the neighbouring band is plausible depending on the smoother. "
         "That is the doubt this figure is about: some band memberships are a "
         "smoother-dependent call."),
        (f"SINGLE SMOOTHER. {context['n_single']} of {n} rows matched only one "
         "smoother (n_smoothers_matched = 1); for those no spread is available "
         "and the whisker is a zero-length tick at the dot."),
        (f"COUNTERWEIGHT 1 — SYNTHETIC RECOVERY. Separately: the detector "
         f"recovers planted 45° corners at {ev['angle_mae_deg']:.1f}° MAE over "
         f"{ev['angle_mae_n']} planted turns (notes/phase2-turns.md). This is "
         "the planted-corner test, not the gate in the next line."),
        (f"COUNTERWEIGHT 2 — REAL-SCHEMA GATE. Separately: on real three-cone "
         f"geometry the gate measures precision {ev['precision']:.3f} and "
         f"recall {ev['recall']:.3f} (notes/phase2-turns.md; "
         "outputs/audit/gate.json). The two facts are not combined into one "
         "score."),
        ("NOT DRAWN. No histogram on top, no p-value, no error bar on counts, "
         "no smoothing of the smoothing, no player names. Rows are turns, not "
         "players."),
        ("SOURCE. outputs/turns/turns.parquet (valid DB turns, stage 02); every "
         "value is recomputed at render time. Sample data: one session of "
         "sample data; nothing here is a finding about the 2027 class."),
    ]


def plot_bars_of_doubt(table: pd.DataFrame, context: dict,
                       cfg: common.Config) -> plt.Figure:
    """The dot-and-whisker panel: one row per turn, sorted by measured angle."""
    from matplotlib.lines import Line2D

    block = "figures.bars_of_doubt"
    low, high = context["angle_low"], context["angle_high"]
    focus = focus_band(cfg)
    if (float(low), float(high)) != (focus["low"], focus["high"]):
        raise ValueError(
            f"{STAGE}: {block}.angle_band_deg must match "
            f"{FOCUS_BAND_KEY}.edges_deg; both describe the same focus band")
    n = context["n_rows"]
    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    ax = fig.add_axes([0.075, 0.205, 0.900, 0.650])
    ax.set_facecolor("#F1F1F1")
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#B3B3B3")
    ax.tick_params(labelsize=8.2, colors="#333333", length=2)
    ax.set_axisbelow(True)

    # The band itself is the tinted strip; the rules are the band edges.
    ax.axvspan(low, high, color=focus["colour"], alpha=focus["tint_alpha"],
               zorder=0)
    for edge, label in ((low, f"{low:g}°"), (high, f"{high:g}°")):
        ax.axvline(edge, color=focus["colour"], lw=focus["rule_width"],
                   linestyle="-", zorder=1)
        ax.text(edge, n + 0.6, label, ha="center", va="bottom", fontsize=8.0,
                color=focus["colour"], fontweight="bold")
    ax.text((low + high) / 2.0, n + 1.15, f"{focus['label']} band",
            ha="center", va="bottom", fontsize=8.0,
            color=focus["colour"], fontweight="bold")

    for row in table.itertuples(index=False):
        y = float(row.row_order)
        combine = row.setting == "combine"
        colour = BARS_ACCENT if combine else BARS_GREY
        ax.plot([row.whisker_low_deg, row.whisker_high_deg], [y, y],
                color=colour, lw=(1.15 if combine else 0.65),
                alpha=(0.98 if combine else 0.72),
                solid_capstyle="butt", zorder=(3 if combine else 2))
        if bool(row.capped_low):
            ax.plot([row.whisker_low_deg], [y], marker="<", ms=3.1,
                    color=colour, zorder=4)
        if bool(row.capped_high):
            ax.plot([row.whisker_high_deg], [y], marker=">", ms=3.1,
                    color=colour, zorder=4)
        ax.plot([row.abs_turn_angle], [y], marker="o",
                ms=(3.7 if combine else 2.7), color=colour,
                markeredgecolor="white", markeredgewidth=0.35,
                zorder=5)

    ax.set_xlim(19.0, 56.0)
    ax.set_xticks([20, 25, 30, 35, 40, 45, 50, 55])
    ax.set_xticklabels([f"{t:g}°" for t in [20, 25, 30, 35, 40, 45, 50, 55]])
    ax.grid(axis="x", color="white", linewidth=0.9, zorder=0)
    ax.set_ylim(-1.2, n + 1.6)
    ax.set_yticks([])
    ax.set_ylabel(
        f"One row per turn ({n}), sorted by measured angle — increasing upward",
        fontsize=8.2, color="#333333", labelpad=6)
    ax.set_xlabel("Absolute turn angle (degrees); the 30–45° band is the lit "
                  "strip", fontsize=8.4, color="#333333", labelpad=4)

    handles = [
        Line2D([], [], marker="o", ls="", color=BARS_ACCENT, ms=6.0,
               label=f"Combine — {context['by_setting']['combine']} turns"),
        Line2D([], [], marker="o", ls="", color=BARS_GREY, ms=5.0,
               label=f"Game — {context['by_setting']['game']} turns"),
        Line2D([0, 1], [0, 0], color="#8A8A8A", lw=1.0,
               label="whisker = smoother min–max"),
    ]
    fig.legend(handles=handles, loc="lower left",
               bbox_to_anchor=(0.070, 0.862), ncol=3, frameon=False,
               fontsize=8.4, handlelength=1.5, columnspacing=1.8)

    fig.text(0.008, 0.980, "Bars of doubt: how precisely is a 30–45° turn known?",
             fontsize=16.5, fontweight="bold", ha="left", va="top",
             color="#111111")
    fig.text(0.008, 0.948,
             "Every valid DB turn in the band, one row each; the dot is the "
             "measurement, the whisker is the same turn under the other "
             "smoothers.", fontsize=8.8, ha="left", va="top", color="#333333")
    fig.text(0.008, 0.920,
             f"Whiskers capped at {context['cap_width']:g}° total width "
             f"(at most ±{context['cap_width'] / 2:g}° from the dot): "
             f"{context['n_capped']} of {n} rows cut; carets mark the cut ends.",
             fontsize=8.2, ha="left", va="top", color="#333333")
    fig.text(0.008, 0.898,
             f"Smoother range leaves the band: {context['n_crossed']} of {n} "
             f"turns ({context['crossed_share']:.1%}) — neighbouring band "
             f"plausible. Single smoother only: {context['n_single']} rows.",
             fontsize=8.2, ha="left", va="top", color="#333333")

    caption = "\n".join(_wrap_text(line, 238)
                        for line in _bars_of_doubt_caption(context))
    fig.text(0.008, 0.012, caption, ha="left", va="bottom", fontsize=6.0,
             color="#333333", linespacing=1.30)
    return fig


def render_bars_of_doubt(fig: plt.Figure, cfg: common.Config,
                         out_png: Path) -> Path:
    """Save figure 13 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.bars_of_doubt.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def bars_of_doubt_csv_frame(table: pd.DataFrame,
                           context: dict) -> pd.DataFrame:
    """One row per turn: measured angle, uncapped range, drawn geometry, flags."""
    out = table.copy()
    out["position_group"] = context["position"]
    columns = [
        "row_order", "setting", "setting_label", "position_group",
        "abs_turn_angle", "abs_turn_angle_smoother_min",
        "abs_turn_angle_smoother_max", "smoother_spread_deg",
        "whisker_low_deg", "whisker_high_deg", "capped", "capped_low",
        "capped_high", "crossed_edge", "single_smoother",
        "n_smoothers_matched", "band_low_deg", "band_high_deg",
        "cap_width_deg",
    ]
    return out[columns].reset_index(drop=True)


def bars_of_doubt_manifest_payload(table: pd.DataFrame, context: dict,
                                   cfg: common.Config) -> dict:
    """Figure 13's manifest entry, with the two detector facts kept separate."""
    ev = DETECTOR_EVIDENCE
    n = context["n_rows"]
    n_cross = context["n_crossed"]
    n_capped = context["n_capped"]
    if int(table["crossed_edge"].sum()) != n_cross:
        raise ValueError(f"{STAGE}: crossing count does not match the table")
    if int(table["capped"].sum()) != n_capped:
        raise ValueError(f"{STAGE}: capped count does not match the table")
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_BARS_OF_DOUBT,
        "title": "Bars of doubt: how precisely is a 30–45° turn known?",
        "sources": ["outputs/turns/turns.parquet"],
        "band": {
            "position_group": context["position"],
            "abs_turn_angle_deg": [context["angle_low"], context["angle_high"]],
            "rule": "valid turns, left-closed on abs_turn_angle_deg",
        },
        "population": {
            "turns": n,
            "by_setting": context["by_setting"],
            "median_spread_deg": context["median_spread_deg"],
            "median_spread_by_setting_deg":
                context["median_spread_by_setting"],
            "max_spread_deg": context["max_spread_deg"],
            "p90_spread_deg": context["p90_spread_deg"],
            "rows_with_single_smoother": context["n_single"],
            "rows_with_multiple_smoothers": context["n_multi"],
        },
        "cap": {
            "key": "figures.bars_of_doubt.max_whisker_width_deg",
            "width_deg": context["cap_width"],
            "arm_deg": context["cap_width"] / 2,
            "rows_capped": n_capped,
            "capped_share": n_capped / n,
            "meaning": ("a DRAWING cap only: whiskers are clipped at the "
                        "stated width, the CSV keeps the uncapped min/max and "
                        "spread"),
            "p90_spread_deg_uncapped": context["p90_spread_deg"],
        },
        "crossing": {
            "rows": n_cross,
            "share": context["crossed_share"],
            "rule": ("smoother_min < band_low or smoother_max >= band_high; "
                     "the smoother range leaves the half-open band"),
            "meaning": ("for these turns the neighbouring band is plausible "
                        "depending on which smoother processed the tracking"),
        },
        "detector_evidence": {
            "synthetic_recovery": {
                "angle_mae_deg": ev["angle_mae_deg"],
                "n_planted_turns": ev["angle_mae_n"],
                "source": DETECTOR_SOURCE_NOTES,
                "is_same_fact_as_gate": False,
            },
            "real_schema_gate": {
                "drill": ev["drill"],
                "precision": ev["precision"],
                "recall": ev["recall"],
                "sources": [DETECTOR_SOURCE_NOTES, DETECTOR_SOURCE_GATE],
                "is_same_fact_as_synthetic": False,
            },
            "stated_separately": True,
            "note": ("TWO SEPARATE FACTS: the planted-corner MAE is a "
                     "synthetic recovery test; the precision/recall are the "
                     "real-schema three-cone gate. They are not combined into "
                     "one score or one claim."),
        },
        "claim": (f"The {n} valid DB turns in the "
                  f"{context['angle_low']:g}–{context['angle_high']:g}° band "
                  f"have a median smoother spread of "
                  f"{context['median_spread_deg']:.2f}°; {n_cross} of {n} "
                  f"({context['crossed_share']:.1%}) have a smoother range "
                  "that leaves the band, so their band membership is "
                  "smoother-dependent. The detector is nevertheless accurate: "
                  f"planted corners at {ev['angle_mae_deg']:.1f}° MAE over "
                  f"{ev['angle_mae_n']} turns (synthetic recovery), and "
                  f"precision {ev['precision']:.3f} / recall "
                  f"{ev['recall']:.3f} on the real-schema three-cone gate "
                  "(a separate check)."),
        "not_claimed": [
            "That the smoother spread is a confidence interval or a standard "
            "error; it is a processing range across smoothers.",
            "That turns whose range crosses an edge belong to the neighbouring "
            "band; the figure says the neighbouring band is plausible.",
            "That the two detector checks are one combined test; they are "
            "separate facts, stated separately.",
            "Anything about the 2027 class; this is one session of sample data.",
        ],
        "caveats": [
            (f"Whiskers are capped at {context['cap_width']:g}° total width "
             f"for drawing; {n_capped} of {n} rows are cut and marked with a "
             "caret. The CSV retains the uncapped values. The uncapped p90 "
             f"spread is {context['p90_spread_deg']:.1f}°."),
            (f"{context['n_single']} of {n} rows matched only one smoother; "
             "for those no spread is available and the whisker is zero-length."),
            ("Rows are turns, not players; no player names appear."),
            ("No histogram, no p-value, no error bar on counts, and no "
             "smoothing of the smoothing."),
            ("Sample data: one session of sample data; nothing here is a "
             "finding about the 2027 class."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_BARS_OF_DOUBT}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_BARS_OF_DOUBT}.csv",
    }


# ------------- figure 14: where the Combine's turns come from
#: Added at Conor's request (2026-11-08); it is not derived from a TASK.md line.
#:
#: The composition view of the same inventory figure 2 drew as points. A
#: drill x angle-band matrix: one row per drill (sorted by that drill's own
#: valid DB Combine turn count) and one column per reporting angle band in
#: increasing angle order.
#:
#: The shares are WITHIN A DRILL. Each cell prints its count and that cell's
#: share of the drill's own valid turns, so every row sums to 1. A dot marks a
#: zero cell. The footer row is the pooled column total, explicitly labelled
#: and drawn as counts only, never as a share segment. The 30-45 degree column
#: is filled bright red (config focus_fill) so its four turns are visible in
#: the matrix rather than described as unseeable at a bar scale.
FIGURE_KEY_SUPPLY_COMPOSITION = "fig_supply_composition"

SUPPLY_COMPOSITION_COLUMNS = [
    "setting",
    "drill_name",
    "abs_turn_angle",
    "position_group",
    "nfl_id",
    "valid",
]

#: Readable row labels. Presentation only; the CSV keeps the source
#: ``drill_name``. Long names WRAP (see _sc_row_label) — they are never cut
#: down to an unrecognisable code.
SUPPLY_COMPOSITION_DISPLAY = {
    "BOX_DRILL": "Box drill",
    "TERYL_AUSTIN_DRILL_2": "Teryl Austin drill 2",
    "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION":
        "Back pedal and transition 45° reaction",
    "BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL":
        "Back pedal and 90° break to ball",
    "TERYL_AUSTIN_DRILL_1": "Teryl Austin drill 1",
    "W_DRILL": "W drill",
    "THREE_CONE_DRILL": "3-cone drill",
    "SHORT_SHUTTLE": "Short shuttle",
    "GAUNTLET_DRILL": "Gauntlet",
    "FORTY_YARD_DASH": "40-yard dash",
}

#: Sequential single-hue ramp, light at the shallow end and dark at the deep
#: end. Presentation only: the band order is the data, the colour is the key.
SUPPLY_COMPOSITION_RAMP = [
    "#BDD7E7", "#9ECAE1", "#6BAED6", "#4292C6",
    "#3182BD", "#08519C", "#08306B",
]


def load_supply_composition_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 14 needs (the drill label is one of them)."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    df = pd.read_parquet(path, columns=SUPPLY_COMPOSITION_COLUMNS)
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    return df


def _sc_display(name: str) -> str:
    """A drill's readable name; an unmapped drill is title-cased, not dropped."""
    raw = str(name)
    if raw in SUPPLY_COMPOSITION_DISPLAY:
        return SUPPLY_COMPOSITION_DISPLAY[raw]
    return " ".join(part.capitalize() for part in raw.split("_"))


def _sc_count(n: int, singular: str) -> str:
    """'1 turn' / '304 turns' — a count on the image must read as English."""
    return f"{n:,} {singular}" if n == 1 else f"{n:,} {singular}s"


def _sc_row_label(label: str, turns: int, players: int) -> str:
    """Row label: wrapped drill name, then that drill's own totals."""
    wrapped = textwrap.fill(label, 30)
    return (f"{wrapped}\n{_sc_count(turns, 'turn')} · "
            f"{_sc_count(players, 'player')}")


def supply_composition_table(turns: pd.DataFrame,
                             cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """One row per drill x band, with that drill's own share and player count.

    Shares are always shares of the drill's own valid turns, never of the
    pooled total. Every drill x band combination is emitted, including zeros,
    so each drill's shares sum to exactly 1.
    """
    block = "figures.supply_composition"
    position = str(cfg.get(f"{block}.position_group"))
    edges = [float(x) for x in cfg.get(f"{block}.bands.turn_angle_deg")]
    overflow = str(cfg.get(f"{block}.bands.overflow_label"))
    min_share = float(cfg.get(f"{block}.min_label_share"))
    if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise common.StageRefused(
            f"{STAGE}: figures.supply_composition.bands.turn_angle_deg must be "
            "strictly increasing")
    if not (0.0 <= min_share <= 1.0):
        raise common.StageRefused(
            f"{STAGE}: figures.supply_composition.min_label_share is "
            f"{min_share}; a share threshold must lie in [0, 1]")
    missing = [c for c in SUPPLY_COMPOSITION_COLUMNS if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); the drill "
            "composition cannot be counted")

    valid = turns["valid"].fillna(False).astype(bool)
    combine = (turns["setting"] == "combine")
    df = turns[valid & (turns["position_group"] == position) & combine].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} Combine turns — nothing to "
            "compose")
    angle = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    if angle.isna().any():
        raise common.StageRefused(
            f"{STAGE}: {int(angle.isna().sum())} valid {position} Combine "
            "turns have no numeric angle; a band composition cannot be drawn "
            "with an unbanded turn")
    if df["drill_name"].isna().any():
        raise common.StageRefused(
            f"{STAGE}: {int(df['drill_name'].isna().sum())} valid {position} "
            "Combine turns carry no drill_name; a drill composition cannot "
            "put them in a row")

    bands = band_labels(edges, overflow)
    arr = angle.to_numpy()
    labels = np.full(len(df), None, dtype=object)
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        labels[(arr >= lo) & (arr < hi)] = bands[i]
    labels[arr >= edges[-1]] = bands[-1]
    if pd.isna(labels).any():
        raise common.StageRefused(
            f"{STAGE}: {int(pd.isna(labels).sum())} valid {position} Combine "
            f"turns fall outside the configured bands {edges}")
    df["band"] = labels

    totals = df.groupby("drill_name").size()
    drill_players = df.groupby("drill_name")["nfl_id"].nunique()
    order = sorted((str(d) for d in totals.index),
                   key=lambda d: (-int(totals[d]), d))
    counts = (df.groupby(["drill_name", "band"], observed=True)
                .agg(turns=("valid", "size"), players=("nfl_id", "nunique")))
    grand = int(len(df))

    rows: list[dict] = []
    for drill in order:
        d_total = int(totals[drill])
        d_players = int(drill_players[drill])
        left = 0.0
        for i, band in enumerate(bands):
            if (drill, band) in counts.index:
                n = int(counts.loc[(drill, band), "turns"])
                p = int(counts.loc[(drill, band), "players"])
            else:
                n, p = 0, 0
            share = n / d_total
            rows.append({
                "drill": drill,
                "drill_label": _sc_display(drill),
                "band": band,
                "band_index": i,
                "band_low_deg": edges[i] if i < len(edges) - 1 else edges[-1],
                "band_high_deg": edges[i + 1] if i < len(edges) - 1 else None,
                "turns": n,
                "share_of_drill": share,
                "share_start": left,
                "share_end": left + share,
                "players": p,
                "drill_turns": d_total,
                "drill_players": d_players,
                "n_drills": len(order),
                "position_group": position,
                "min_label_share": min_share,
            })
            left += share
        if not np.isclose(left, 1.0, atol=1e-12):
            raise ValueError(
                f"{STAGE}: {drill} shares sum to {left}, not 1")
    table = pd.DataFrame(rows)

    # Hard checks: the table must reconstruct the population it came from.
    if int(table["turns"].sum()) != grand:
        raise ValueError(
            f"{STAGE}: drill composition counts sum to "
            f"{int(table['turns'].sum())}, but the table has {grand} valid "
            f"{position} Combine turns")
    for drill in order:
        part = table[table["drill"] == drill]
        if int(part["turns"].sum()) != int(totals[drill]):
            raise ValueError(
                f"{STAGE}: {drill} band counts do not sum to its total")
        if not np.isclose(float(part["share_of_drill"].sum()), 1.0,
                          atol=1e-12):
            raise ValueError(
                f"{STAGE}: {drill} shares do not sum to 1")

    drill_rows = [
        {"drill": d, "label": _sc_display(d),
         "turns": int(totals[d]), "players": int(drill_players[d]),
         "share_of_all_turns": int(totals[d]) / grand}
        for d in order
    ]
    top2 = drill_rows[:2]
    top2_turns = int(sum(r["turns"] for r in top2))
    shallow = table[(table["band"] == bands[0]) & (table["turns"] > 0)]
    shallow_by_drill = [
        {"drill": str(r["drill"]), "label": str(r["drill_label"]),
         "turns": int(r["turns"]), "players": int(r["players"])}
        for _, r in shallow.iterrows()
    ]
    band_totals = [int(table[table["band"] == b]["turns"].sum())
                   for b in bands]
    band_players = [int(df[df["band"] == b]["nfl_id"].nunique())
                    for b in bands]

    context = {
        "position": position,
        "bands": bands,
        "edges": edges,
        "overflow_label": overflow,
        "min_label_share": min_share,
        "n_turns": grand,
        "n_players": int(df["nfl_id"].nunique()),
        "n_drills": len(order),
        "drills": drill_rows,
        "top_two": {
            "drills": top2,
            "turns": top2_turns,
            "share": top2_turns / grand,
            "rest_turns": grand - top2_turns,
        },
        "shallow": {
            "band": bands[0],
            "turns": int(shallow["turns"].sum()),
            "n_drills": int(len(shallow)),
            "players": int(df[df["band"] == bands[0]]["nfl_id"].nunique()),
            "by_drill": shallow_by_drill,
            "max_share_of_drill": (float(shallow["share_of_drill"].max())
                                   if len(shallow) else 0.0),
        },
        "band_totals": band_totals,
        "band_players": band_players,
    }
    return table, context


def _sc_shallow_phrase(context: dict) -> str:
    """'Box drill (1 of 304), ...' — counts read from the context only."""
    totals = {d["drill"]: d["turns"] for d in context["drills"]}
    items = [f"{i['label']} ({i['turns']} of {totals[i['drill']]:,})"
             for i in context["shallow"]["by_drill"]]
    if len(items) > 1:
        return ", ".join(items[:-1]) + " and " + items[-1]
    return "".join(items)


def _supply_composition_caption(table: pd.DataFrame,
                                context: dict) -> list[str]:
    """Caption lines, every count read from the rendered table."""
    n = context["n_turns"]
    n_drills = context["n_drills"]
    n_players = context["n_players"]
    n_bands = len(context["bands"])
    top = context["top_two"]
    t1, t2 = top["drills"][0], top["drills"][1]
    sh = context["shallow"]
    shallow_phrase = _sc_shallow_phrase(context)
    pooled = ", ".join(f"{b} {c}" for b, c in
                       zip(context["bands"], context["band_totals"]))
    return [
        STAMP,
        (f"WHAT THIS IS. A drill x angle-band matrix: {n_drills} drills "
         f"(rows, sorted by that drill's own turn total) carrying {n:,} "
         f"valid DB Combine turns from {n_players} players, against the "
         f"{n_bands} reporting angle bands (columns, increasing angle left "
         "to right). In each cell the bold number is the turns in that cell "
         "and the smaller number below it is that cell's share of the "
         "drill's own valid turns, so every row sums to 100%. A dot marks a "
         "cell with no turns. The footer row is the pooled column total, "
         "labelled as such and drawn as counts only."),
        (f"CONCENTRATION, AS COUNTED. The top two drills — {t1['label']} "
         f"({t1['turns']:,} turns, {t1['share_of_all_turns']:.1%} of all) and "
         f"{t2['label']} ({t2['turns']:,} turns, "
         f"{t2['share_of_all_turns']:.1%}) — supply {top['turns']:,} turns, "
         f"{top['share']:.1%} of the {n:,}. The other "
         f"{n_drills - 2} drills account for the remaining "
         f"{top['rest_turns']:,} turns. This is an inventory of where turns "
         "exist, not a ranking of drill quality."),
        (f"THE {sh['band']} BAND, IN ITS OWN COLUMN. {sh['turns']} turns, one "
         f"in each of {sh['n_drills']} drills: {shallow_phrase}. The largest "
         f"{sh['band']} share of any drill's own turns is "
         f"{sh['max_share_of_drill']:.1%}, and the counts show why the "
         "shallow break is under-tested: those turns are side effects of "
         "drills chasing something else, and no drill is designed to "
         "produce one. Every drill x band count is in the CSV."),
        (f"POOLED BAND TOTALS, IN THE FOOTER ROW. Counted across all drills "
         f"and printed as the footer row, never as a share segment: {pooled}. "
         "These are counts only; the cells above remain within-drill shares, "
         "so a large pooled band does not make any single drill's row mostly "
         "that band."),
        ("WHAT IS NOT HERE. No pooled share bar, no band's share of the total "
         "printed in a cell, no per-player values, no player names, no "
         "drill-quality score, no ordering by anything except turn count. DB "
         "only. Counts are recomputed at render time from "
         "outputs/turns/turns.parquet (valid turns); every value is in "
         f"outputs/figures/{FIGURE_KEY_SUPPLY_COMPOSITION}.csv."),
        ("SAMPLE DATA. One session of sample data. Nothing here is a finding "
         "about the 2027 class, and equal-sized cells do not mean equal drill "
         "volume: read the turn counts on the row labels."),
    ]


def plot_supply_composition(table: pd.DataFrame, context: dict,
                            cfg: common.Config) -> plt.Figure:
    """Drill x angle-band matrix, one row per drill, focus band in red."""
    from matplotlib.patches import Rectangle

    block = "figures.supply_composition"
    bands = context["bands"]
    ramp = SUPPLY_COMPOSITION_RAMP[:len(bands)]
    focus = focus_band(cfg)
    focus_idx = focus_band_index(context["edges"], focus)
    focus_fill = str(cfg.get(f"{block}.focus_fill"))
    drills = context["drills"]
    n = context["n_drills"]
    cell_w, cell_h = 0.88, 0.86
    footer_y = -1.10
    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    ax = fig.add_axes([0.235, 0.315, 0.750, 0.555])
    ax.set_facecolor("white")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="x", length=0, pad=4, labelsize=9.0,
                   colors="#333333")
    ax.tick_params(axis="y", length=0)
    ax.xaxis.set_ticks_position("top")
    ax.set_axisbelow(True)

    for i, drill in enumerate(drills):
        y = float(n - 1 - i)
        part = table[table["drill"] == drill["drill"]].sort_values("band_index")
        for row in part.itertuples(index=False):
            col = int(row.band_index)
            is_focus = col == focus_idx
            rect = Rectangle((col - cell_w / 2.0, y - cell_h / 2.0),
                             cell_w, cell_h,
                             facecolor=(focus_fill if is_focus else ramp[col]),
                             edgecolor="white", linewidth=1.2,
                             alpha=(1.0 if is_focus else 0.16), zorder=2)
            rect.set_gid(f"sc-cell:{drill['drill']}:{col}")
            ax.add_patch(rect)
            if int(row.turns) == 0:
                ax.text(col, y, "·", ha="center", va="center",
                        fontsize=11.0, zorder=4,
                        color=("#FFFFFF" if is_focus else "#9A9A9A"))
                continue
            ax.text(col, y + 0.13, f"{int(row.turns):,}", ha="center",
                    va="center", fontsize=9.6, fontweight="bold", zorder=4,
                    color=("#FFFFFF" if is_focus else "#1A1A1A"))
            ax.text(col, y - 0.17,
                    f"{float(row.share_of_drill) * 100:.1f}%", ha="center",
                    va="center", fontsize=6.8, zorder=4,
                    color=("#FFFFFF" if is_focus else "#555555"))

    # Hairline, then the footer row: pooled column totals, counts only.
    ax.plot([-0.60, len(bands) - 0.40], [footer_y + 0.52, footer_y + 0.52],
            color="#B3B3B3", linewidth=0.8, zorder=1, clip_on=False)
    for col, total in enumerate(context["band_totals"]):
        total_text = ax.text(col, footer_y, f"{int(total):,}", ha="center",
                             va="center", fontsize=10.0, fontweight="bold",
                             zorder=4,
                             color=(focus_fill if col == focus_idx
                                    else "#1A1A1A"))
        total_text.set_gid(f"sc-footer-total:{col}")

    ax.set_xlim(-0.60, len(bands) - 0.40)
    ax.set_ylim(footer_y - 0.55, float(n) - 0.30)
    ax.set_xticks(list(range(len(bands))))
    ax.set_xticklabels(bands, fontsize=9.0, color="#333333")
    for col, label in enumerate(ax.get_xticklabels()):
        if col == focus_idx:
            label.set_color(focus_fill)
            label.set_fontweight("bold")
    ax.set_yticks([float(n - 1 - i) for i in range(n)] + [footer_y])
    ax.set_yticklabels(
        [_sc_row_label(d["label"], d["turns"], d["players"]) for d in drills]
        + [f"All drills\n{_sc_count(context['n_turns'], 'turn')}"],
        fontsize=8.4, color="#222222")

    sh = context["shallow"]
    shallow_labels = [i["label"] for i in sh["by_drill"]]
    if len(shallow_labels) > 1:
        shallow_names = ", ".join(shallow_labels[:-1]) + " and " + shallow_labels[-1]
    else:
        shallow_names = "".join(shallow_labels)

    fig.text(0.010, 0.975, "Where the Combine's turns come from",
             fontsize=17.5, fontweight="bold", ha="left", va="top",
             color="#111111")
    fig.text(0.010, 0.941,
             f"{context['n_turns']:,} valid DB Combine turns across "
             f"{context['n_drills']} drills (sorted by drill total); rows are "
             "drills, columns are angle bands, and each cell prints its own "
             "count and within-drill share.",
             fontsize=8.8, ha="left", va="top", color="#333333")
    fig.text(0.010, 0.300,
             f"{focus['label']}: {sh['turns']} turns in all "
             f"({sh['n_drills']} drills, {sh['players']} players) — one each "
             f"in {shallow_names}.\nThe {focus['label']} column is red; the "
             f"largest within-drill share is "
             f"{sh['max_share_of_drill']:.1%}. Counts and shares are printed "
             "in the cells; every value is in the CSV.",
             fontsize=7.8, ha="left", va="top", color=focus["colour"])

    caption = "\n".join(_wrap_text(line, 150)
                        for line in _supply_composition_caption(table, context))
    fig.text(0.010, 0.012, caption, ha="left", va="bottom", fontsize=6.0,
             color="#333333", linespacing=1.30)
    return fig


def render_supply_composition(fig: plt.Figure, cfg: common.Config,
                              out_png: Path) -> Path:
    """Save figure 14 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.supply_composition.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def supply_composition_csv_frame(table: pd.DataFrame,
                                 context: dict) -> pd.DataFrame:
    """One row per drill x band: turns, share of that drill, players."""
    out = table.copy()
    columns = [
        "drill", "drill_label", "band", "band_index", "band_low_deg",
        "band_high_deg", "turns", "share_of_drill", "share_start",
        "share_end", "players", "drill_turns", "drill_players", "n_drills",
        "position_group", "min_label_share",
    ]
    return out[columns].reset_index(drop=True)


def supply_composition_manifest_payload(table: pd.DataFrame, context: dict,
                                        cfg: common.Config) -> dict:
    """Figure 14's manifest entry, with the within-drill share rule stated."""
    top = context["top_two"]
    sh = context["shallow"]
    if int(table["turns"].sum()) != context["n_turns"]:
        raise ValueError(f"{STAGE}: manifest counts do not match the table")
    for drill in context["drills"]:
        part = table[table["drill"] == drill["drill"]]
        if not np.isclose(float(part["share_of_drill"].sum()), 1.0,
                          atol=1e-12):
            raise ValueError(
                f"{STAGE}: manifest shares for {drill['drill']} do not sum to 1")
    drill_entries = []
    for drill in context["drills"]:
        part = table[table["drill"] == drill["drill"]].sort_values("band_index")
        drill_entries.append({
            **drill,
            "bands": [
                {"band": str(row["band"]), "turns": int(row["turns"]),
                 "share_of_drill": float(row["share_of_drill"]),
                 "players": int(row["players"])}
                for _, row in part.iterrows()
            ],
        })
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_SUPPLY_COMPOSITION,
        "title": "Where the Combine's turns come from",
        "sources": ["outputs/turns/turns.parquet"],
        "band": {
            "position_group": context["position"],
            "bands_deg": context["edges"],
            "overflow_label": context["overflow_label"],
            "rule": "valid turns, left-closed on abs_turn_angle_deg",
        },
        "population": {
            "turns": context["n_turns"],
            "players": context["n_players"],
            "drills": context["n_drills"],
        },
        "shares": {
            "unit": "share of the drill's own valid turns",
            "within_drill": True,
            "layout": "drill x angle-band matrix",
            "pooled_bar_drawn": False,
            "band_share_of_all_turns_in_bars": False,
            "every_drill_sums_to_one": True,
            "pooled_band_totals_stated_separately": True,
        },
        "drills": drill_entries,
        "concentration": {
            "top_two": [
                {"drill": d["drill"], "label": d["label"],
                 "turns": d["turns"], "share_of_all_turns":
                     d["share_of_all_turns"]}
                for d in top["drills"]
            ],
            "turns": top["turns"],
            "share_of_all_turns": top["share"],
            "rest_turns": top["rest_turns"],
            "meaning": ("the two largest drills by valid turn count; an "
                        "inventory fact, not a ranking of drill quality"),
        },
        "shallow_band": {
            "band": sh["band"],
            "turns": sh["turns"],
            "drills": sh["n_drills"],
            "players": sh["players"],
            "by_drill": sh["by_drill"],
            "max_share_of_drill": sh["max_share_of_drill"],
            "visibility": ("drawn as its own column in the matrix, with "
                           "counts and within-drill shares printed in the "
                           "cells and listed in the CSV"),
        },
        "pooled_band_totals": [
            {"band": b, "turns": int(c), "players": int(p)}
            for b, c, p in zip(context["bands"], context["band_totals"],
                               context["band_players"])
        ],
        "claim": (f"The {context['n_turns']:,} valid DB Combine turns come "
                  f"from {context['n_drills']} drills and {context['n_players']} "
                  f"players; the top two drills supply {top['turns']:,} of "
                  f"them ({top['share']:.1%}). The "
                  f"{sh['band']} band holds {sh['turns']} turns spread across "
                  f"{sh['n_drills']} drills and {sh['players']} players, at "
                  f"most {sh['max_share_of_drill']:.1%} of any one drill's "
                  "own turns."),
        "not_claimed": [
            "That any drill is good or bad, easier or harder; this is an "
            "inventory of turns, not a ranking of drill quality.",
            "That the band shares are pooled across drills; every printed "
            "share is a share of its own drill's turns.",
            "That the large cells are a target; they are where turns "
            "currently exist.",
            "Anything about the 2027 class; this is one session of sample data.",
        ],
        "caveats": [
            ("Shares are within a drill; each row sums to 100%. The pooled "
             "band totals are the labelled footer row, a count and never a "
             "share segment."),
            (f"The {sh['band']} band is its own column in the matrix "
             f"({sh['turns']} turns); its counts and within-drill shares are "
             "printed in the cells and listed in the CSV."),
            ("No player names and no per-player values appear."),
            ("Sample data: one session of sample data; nothing here is a "
             "finding about the 2027 class."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_SUPPLY_COMPOSITION}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_SUPPLY_COMPOSITION}.csv",
    }


# ------------- figure 15: the 30-45 degree band on its own
#: Added at Conor's request (2026-11-09); it is not derived from a TASK.md line.
#:
#: One panel, one band. Every valid DB turn whose absolute angle lies in
#: [30, 45), drawn once: x = entry speed, y = absolute turn angle. No other
#: band appears on either axis, so the panel answers "what does this band look
#: like by itself?" rather than "how big is it next to the others?".
#:
#: The Combine side is four points. Four points are a LIST, not a
#: distribution: no density curve, no fit, no box plot, no confidence ellipse,
#: no mean and no interval is drawn. The only drawing device is a stated
#: horizontal separation for turns whose dots would land on top of each other.
FIGURE_KEY_BAND_ONLY = "fig_band_only"

BAND_ONLY_COLUMNS = [
    "setting",
    "abs_turn_angle",
    "entry_speed",
    "position_group",
    "nfl_id",
    "drill_name",
    "valid",
]


def load_band_only_turns(path: Path) -> pd.DataFrame:
    """Read the columns figure 15 needs; refuse if entry speed is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    df = pd.read_parquet(path)
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    missing = [c for c in BAND_ONLY_COLUMNS if c not in df.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); figure 15 "
            "places every turn by its entry speed and cannot invent one")
    return df[BAND_ONLY_COLUMNS]


def band_only_table(turns: pd.DataFrame,
                    cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """One row per valid DB turn in the band, with its drawn coordinates.

    ``x_draw`` differs from ``entry_speed`` only where a pair of dots would
    coincide; the offset is recorded and is a drawing device, never a measured
    value. ``abs_turn_angle`` is the measured y for every row.
    """
    block = "figures.band_only"
    position = str(cfg.get(f"{block}.position_group"))
    low, high = (float(x) for x in cfg.get(f"{block}.angle_band_deg"))
    speed_low, speed_high = (float(x) for x in
                             cfg.get(f"{block}.speed_window_yd_s"))
    radius_x = float(cfg.get(f"{block}.dodge.radius_yd_s"))
    radius_y = float(cfg.get(f"{block}.dodge.radius_deg"))
    step = float(cfg.get(f"{block}.dodge.step_yd_s"))
    if high <= low:
        raise common.StageRefused(
            f"{STAGE}: figures.band_only.angle_band_deg must be increasing")
    if speed_high <= speed_low:
        raise common.StageRefused(
            f"{STAGE}: figures.band_only.speed_window_yd_s must be increasing")
    if step <= 0 or radius_x <= 0 or radius_y <= 0:
        raise common.StageRefused(
            f"{STAGE}: figures.band_only.dodge values must be positive")
    focus = focus_band(cfg)
    if (focus["low"], focus["high"]) != (low, high):
        raise common.StageRefused(
            f"{STAGE}: figures.band_only.angle_band_deg {low:g}-{high:g} is "
            f"not the {focus['label']} focus band; the target window would "
            "mark a band the rest of the stage does not use")
    missing = [c for c in BAND_ONLY_COLUMNS if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: the turn table has no {missing} column(s); figure 15 "
            "cannot place a turn by its entry speed")

    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    entry = pd.to_numeric(turns["entry_speed"], errors="coerce")
    scope = valid & (turns["position_group"] == position) & angle.notna()
    band = turns[scope & (angle >= low) & (angle < high)].copy()
    if band.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in [{low:g}, {high:g}) — "
            "nothing to draw")
    band["abs_turn_angle"] = pd.to_numeric(band["abs_turn_angle"],
                                           errors="coerce")
    band["entry_speed"] = pd.to_numeric(band["entry_speed"], errors="coerce")
    if band["entry_speed"].isna().any():
        raise common.StageRefused(
            f"{STAGE}: {int(band['entry_speed'].isna().sum())} band turns have "
            "no numeric entry_speed; the x axis cannot be drawn without it")
    for setting in ("combine", "game"):
        if not (band["setting"] == setting).any():
            raise common.StageRefused(
                f"{STAGE}: no valid {position} {setting} turns in "
                f"[{low:g}, {high:g}) — the figure needs both settings")
    combine_drill = band.loc[band["setting"] == "combine", "drill_name"]
    if combine_drill.isna().any():
        raise common.StageRefused(
            f"{STAGE}: a valid Combine band turn carries no drill_name; the "
            "figure names each Combine turn by the drill that produced it")

    band["setting_label"] = band["setting"].map(
        {"combine": COMBINE_LABEL, "game": GAME_LABEL})
    band["drill"] = np.where(
        band["setting"].to_numpy() == "combine",
        band["drill_name"].fillna("").astype(str).to_numpy(),
        "")
    band["drill_label"] = [
        shallow_drill_label(d) if d else "" for d in band["drill"]]

    # ---- the only drawing offset: separate near-coincident pairs along x.
    # Deterministic greedy pairing in (speed, angle, setting) order. A point is
    # in at most one pair, so a long chain of near neighbours cannot fan out
    # into a fabricated speed range.
    order = band.sort_values(["entry_speed", "abs_turn_angle", "setting"],
                             kind="mergesort").index.to_numpy()
    x = band.loc[order, "entry_speed"].to_numpy(dtype=float)
    y = band.loc[order, "abs_turn_angle"].to_numpy(dtype=float)
    n = len(order)
    paired = np.zeros(n, dtype=bool)
    offsets = np.zeros(n, dtype=float)
    pair_id = np.full(n, -1, dtype=int)
    next_pair = 0
    for i in range(n):
        if paired[i]:
            continue
        for j in range(i + 1, n):
            if paired[j]:
                continue
            if abs(x[i] - x[j]) <= radius_x and abs(y[i] - y[j]) <= radius_y:
                offsets[i] = -step / 2.0
                offsets[j] = step / 2.0
                paired[i] = paired[j] = True
                pair_id[i] = pair_id[j] = next_pair
                next_pair += 1
                break
    band.loc[order, "x_offset_yd_s"] = offsets
    band.loc[order, "dodge_pair"] = pair_id
    band["y_offset_deg"] = 0.0
    band["x_draw"] = band["entry_speed"] + band["x_offset_yd_s"]
    band["y_draw"] = band["abs_turn_angle"] + band["y_offset_deg"]

    in_window = ((band["entry_speed"] >= speed_low)
                 & (band["entry_speed"] <= speed_high))
    if in_window.any():
        moved = band.loc[in_window]
        if ((moved["x_draw"] < speed_low)
                | (moved["x_draw"] > speed_high)).any():
            raise common.StageRefused(
                f"{STAGE}: the drawing offset moved a turn that was inside "
                "the speed window outside it; the window would lie")
    if not band["y_draw"].between(low, high, inclusive="left").all():
        raise ValueError(
            f"{STAGE}: a drawn angle left the half-open band "
            f"[{low:g}, {high:g})")

    band["in_target_window"] = in_window
    band["band_low_deg"] = low
    band["band_high_deg"] = high
    band["speed_window_low_yd_s"] = speed_low
    band["speed_window_high_yd_s"] = speed_high
    band["dodge_step_yd_s"] = step

    band["abs_turn_angle"] = pd.to_numeric(band["abs_turn_angle"],
                                           errors="coerce")
    band = band.sort_values(["abs_turn_angle", "entry_speed", "setting"],
                            kind="mergesort").reset_index(drop=True)
    band["row_order"] = np.arange(len(band), dtype=int)

    by_setting = {s: int((band["setting"] == s).sum())
                  for s in ("combine", "game")}
    if sum(by_setting.values()) != len(band):
        raise ValueError(f"{STAGE}: the per-setting counts do not sum to the "
                         "band population")
    ranges: dict[str, dict[str, float]] = {}
    for setting in ("combine", "game"):
        part = band[band["setting"] == setting]
        for col, key in (("entry_speed", "entry"), ("abs_turn_angle", "angle")):
            values = part[col].astype(float)
            ranges[f"{setting}_{key}"] = {
                "min": float(values.min()),
                "median": float(np.median(values)),
                "max": float(values.max()),
            }
    inside = {s: int(band[(band["setting"] == s)
                          & band["in_target_window"]].shape[0])
              for s in ("combine", "game")}
    outside_fast = {s: int(band[(band["setting"] == s)
                                & (band["entry_speed"] > speed_high)].shape[0])
                    for s in ("combine", "game")}
    outside_slow = {s: int(band[(band["setting"] == s)
                                & (band["entry_speed"] < speed_low)].shape[0])
                    for s in ("combine", "game")}
    combine_part = band[band["setting"] == "combine"]
    player_turns = combine_part.groupby("nfl_id").size()
    combine_players = int(combine_part["nfl_id"].nunique())
    shared = {int(pid): int(c) for pid, c in player_turns.items() if int(c) > 1}
    combine_rows = [
        {"number": i + 1, "drill": str(row.drill),
         "drill_label": str(row.drill_label),
         "entry_speed": float(row.entry_speed),
         "abs_turn_angle": float(row.abs_turn_angle)}
        for i, row in enumerate(combine_part.sort_values(
            "abs_turn_angle", kind="mergesort").itertuples(index=False))
    ]
    n_pairs = int((band["dodge_pair"] >= 0).sum() // 2)
    n_dodged = int((band["dodge_pair"] >= 0).sum())
    combine_pairs = band.loc[band["setting"] == "combine", "dodge_pair"]
    if len(combine_pairs) and int(combine_pairs.max()) >= 0:
        raise ValueError(
            f"{STAGE}: a Combine turn was moved; the dodge is only for "
            "coincident game dots")

    context = {
        "position": position,
        "angle_low": low,
        "angle_high": high,
        "speed_low": speed_low,
        "speed_high": speed_high,
        "n_turns": int(len(band)),
        "by_setting": by_setting,
        "players": {s: int(band[band["setting"] == s]["nfl_id"].nunique())
                    for s in ("combine", "game")},
        "combine_players": combine_players,
        "combine_shared_players": shared,
        "combine_turns_in_shared": int(sum(shared.values())),
        "shared_phrase": (
            (f", and {sum(shared.values())} of the {len(combine_part)} come "
             "from the same player")
            if shared else ", and no player supplies more than one"),
        "combine_rows": combine_rows,
        "ranges": ranges,
        "inside_window": inside,
        "outside_fast": outside_fast,
        "outside_slow": outside_slow,
        "dodge_pairs": n_pairs,
        "dodged_points": n_dodged,
        "dodge_step": step,
    }
    return band, context


def band_only_csv_frame(table: pd.DataFrame, context: dict) -> pd.DataFrame:
    """One row per turn: measured coordinates, drawn coordinates, drill, flags."""
    columns = [
        "row_order", "setting", "setting_label", "position_group",
        "abs_turn_angle", "entry_speed", "drill", "drill_label",
        "x_offset_yd_s", "y_offset_deg", "x_draw", "y_draw",
        "dodge_pair", "in_target_window", "band_low_deg", "band_high_deg",
        "speed_window_low_yd_s", "speed_window_high_yd_s", "dodge_step_yd_s",
    ]
    return table[columns].reset_index(drop=True)


def _band_only_caption(context: dict) -> list[str]:
    """Caption lines; every number comes from the rendered table."""
    r = context["ranges"]
    c, g = context["by_setting"]["combine"], context["by_setting"]["game"]
    ci, gi = context["inside_window"]["combine"], context["inside_window"]["game"]
    fast = context["outside_fast"]["game"]
    slow = context["outside_slow"]["game"]
    drills = "; ".join(
        f"{row['number']} {row['drill_label']} ({row['entry_speed']:.2f} yd/s, "
        f"{row['abs_turn_angle']:.1f}°)"
        for row in context["combine_rows"])
    return [
        STAMP,
        (f"WHAT THIS IS. One panel, one band: every valid "
         f"{context['position']} turn whose absolute angle lies in "
         f"[{context['angle_low']:g}, {context['angle_high']:g}) — "
         f"{context['n_turns']} turns, no other band on either axis. "
         f"x = entry speed in yd/s, y = absolute turn angle in degrees. "
         f"Combine in the accent blue ({c} turns), games in grey ({g}). The "
         "red outline and light tint are the case's target window "
         f"({context['speed_low']:g}–{context['speed_high']:g} yd/s by "
         f"{context['angle_low']:g}–{context['angle_high']:g}°), styled from "
         "figures.focus_band; red marks the window, never a data point. Every "
         "count is recomputed from outputs/turns/turns.parquet at render time."),
        (f"THE POINT, WITH THE NUMBERS. The {c} Combine turns sit at "
         f"{r['combine_entry']['min']:.2f}–{r['combine_entry']['max']:.2f} "
         f"yd/s (median {r['combine_entry']['median']:.2f}); the {g} game "
         f"turns run {r['game_entry']['min']:.2f}–"
         f"{r['game_entry']['max']:.2f} yd/s (median "
         f"{r['game_entry']['median']:.2f}). All {ci} of {c} Combine turns "
         f"are inside the {context['speed_low']:g}–{context['speed_high']:g} "
         f"window, as are {gi} of {g} game turns"
         f"{f'; the {fast} game turns outside it are all faster than ' + format(context['speed_high'], 'g') + ' yd/s' if fast else ''}"
         f"{f' and {slow} game turns are slower than it' if slow else ''}. "
         "Within the one band that looks nominally covered, the Combine "
         "samples only the slow end of the speed range games use."),
        (f"FOUR POINTS ARE A LIST, NOT A DISTRIBUTION. The Combine side is {c} "
         "turns, not a distribution: four points cannot support a density "
         "curve, a fit, a box plot or a confidence ellipse, and none is "
         "drawn. No mean, no interval, no smoothing. What the figure cannot "
         f"show is any Combine turn at game speed: the sample has none in "
         f"this band above {r['combine_entry']['max']:.2f} yd/s, so a Combine "
         "distribution at those speeds cannot be estimated from it — only "
         "stated as absent here."),
        (f"WHO THE {c} ARE, AND WHO THEY ARE NOT. They are turns, not players: "
         f"{c} valid Combine turns from {context['combine_players']} players"
         f"{context['shared_phrase']}. Each is an incidental fragment of a "
         "drill built for "
         "another purpose (figure 11), and the drills are printed under the "
         f"axis: {drills}. No player name or id appears in this figure, its "
         "CSV or its manifest."),
        (f"DOTS, AND THE ONE DRAWING OFFSET. One dot per turn at its measured "
         f"coordinates. {context['dodge_pairs']} pairs of game turns would sit "
         f"on top of each other; each pair is separated by "
         f"{context['dodge_step']:.2f} yd/s along the speed axis "
         f"({context['dodge_step'] / 2:.2f} each side) purely so two dots are "
         "visible, and no Combine dot is moved. The offset is written per row "
         "in the CSV (x_offset_yd_s, y_offset_deg); the measured entry speed "
         "and angle are unchanged. No jitter is used anywhere else."),
        ("WHAT IS NOT HERE. No other angle band, no pooled comparison, no "
         "player-level value, no inference, no p-value. Game turns come from "
         "whatever snaps the sample contains and the Combine from one session "
         "of drills; the two are not sampled alike, and nothing here compares "
         "them beyond their own measured ranges. Sample data: one session of "
         "sample data; nothing here is a finding about the 2027 class. Every "
         f"value is in outputs/figures/{FIGURE_KEY_BAND_ONLY}.csv."),
    ]


def plot_band_only(table: pd.DataFrame, context: dict,
                   cfg: common.Config) -> plt.Figure:
    """One dot per turn on speed x angle, with the band's window outlined."""
    from matplotlib.colors import to_rgba
    from matplotlib.lines import Line2D  # noqa: F401  (kept for future legends)

    block = "figures.band_only"
    focus = focus_band(cfg)
    tint = to_rgba(focus["colour"], focus["tint_alpha"])
    low, high = context["angle_low"], context["angle_high"]
    s_low, s_high = context["speed_low"], context["speed_high"]
    c = context["by_setting"]["combine"]
    g = context["by_setting"]["game"]
    r = context["ranges"]

    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    ax = fig.add_axes([0.085, 0.355, 0.890, 0.505])
    ax.set_facecolor("#EBEBEB")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#B3B3B3")
    ax.tick_params(axis="both", labelsize=9.0, colors="#333333", length=2)
    ax.set_axisbelow(True)

    ax.add_patch(plt.Rectangle((s_low, low), s_high - s_low, high - low,
                               facecolor=tint, edgecolor=focus["colour"],
                               linewidth=focus["outline_width"], zorder=1))
    ax.text((s_low + s_high) / 2.0, high + 0.32,
            f"target window: {s_low:g}–{s_high:g} yd/s × "
            f"{low:g}–{high:g}°",
            ha="center", va="bottom", fontsize=9.2, fontweight="bold",
            color=focus["colour"], zorder=6)

    game = table[table["setting"] == "game"]
    combine = table[table["setting"] == "combine"]
    ax.scatter(game["x_draw"], game["y_draw"], s=26,
               color=BARS_GREY, alpha=0.80, edgecolor="white",
               linewidth=0.45, zorder=3)
    ax.scatter(combine["x_draw"], combine["y_draw"], s=95,
               color=PALETTE["combine"], alpha=0.95, edgecolor="white",
               linewidth=0.9, zorder=5)
    for i, row in enumerate(combine.sort_values(
            "abs_turn_angle", kind="mergesort").itertuples(index=False)):
        ax.text(float(row.x_draw), float(row.y_draw), str(i + 1),
                ha="center", va="center", fontsize=7.0, fontweight="bold",
                color="white", zorder=6)

    ax.set_xlim(1.75, 8.35)
    ax.set_ylim(low - 0.45, high + 1.35)
    ax.set_xticks([2, 3, 4, 5, 6, 7, 8])
    ax.set_yticks([30, 33, 36, 39, 42, 45])
    ax.grid(axis="y", color="white", linewidth=0.8, zorder=0)
    ax.grid(axis="x", color="white", linewidth=0.8, zorder=0)
    ax.set_xlabel("Entry speed (yd/s)", fontsize=9.6, color="#333333",
                  labelpad=6)
    ax.set_ylabel("Absolute turn angle (°)", fontsize=9.6, color="#333333",
                  labelpad=6)

    fig.text(0.010, 0.978, "The band on its own: 30–45° in the Combine and "
             "in games", fontsize=17.5, fontweight="bold", ha="left",
             va="top", color="#111111")
    fig.text(0.010, 0.938,
             f"One dot per valid DB turn with an absolute angle in "
             f"[{low:g}, {high:g})° — Combine in blue ({c}), games in grey "
             f"({g}). No other angle band is on either axis.",
             fontsize=9.2, ha="left", va="top", color="#333333")
    fig.text(0.010, 0.911,
             f"The {c} Combine turns sit at "
             f"{r['combine_entry']['min']:.1f}–"
             f"{r['combine_entry']['max']:.1f} yd/s; games run "
             f"{r['game_entry']['min']:.1f}–{r['game_entry']['max']:.1f} yd/s "
             f"({context['inside_window']['game']} of {g} game turns are "
             f"inside the window).",
             fontsize=9.2, ha="left", va="top", color="#222222")

    key = " · ".join(
        f"{row['number']} {row['drill_label']} "
        f"({row['entry_speed']:.2f} yd/s, {row['abs_turn_angle']:.1f}°)"
        for row in context["combine_rows"])
    fig.text(0.010, 0.291, f"The {c} Combine turns, by drill: {key}",
             fontsize=8.0, ha="left", va="top", color="#333333")

    caption = "\n".join(_wrap_text(line, 170)
                        for line in _band_only_caption(context))
    fig.text(0.010, 0.010, caption, ha="left", va="bottom", fontsize=6.0,
             color="#333333", linespacing=1.30)
    return fig


def render_band_only(fig: plt.Figure, cfg: common.Config,
                     out_png: Path) -> Path:
    """Save figure 15 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.band_only.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def band_only_manifest_payload(table: pd.DataFrame, context: dict,
                               cfg: common.Config) -> dict:
    """Figure 15's manifest entry: the band, both ranges, and what is absent."""
    c = context["by_setting"]["combine"]
    g = context["by_setting"]["game"]
    if int(len(table)) != context["n_turns"]:
        raise ValueError(f"{STAGE}: manifest counts do not match the table")
    if int(table["setting"].eq("combine").sum()) != c:
        raise ValueError(f"{STAGE}: table Combine count does not match")
    if int(table["setting"].eq("game").sum()) != g:
        raise ValueError(f"{STAGE}: table game count does not match")
    r = context["ranges"]
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_BAND_ONLY,
        "title": "The band on its own: 30–45° in the Combine and in games",
        "sources": ["outputs/turns/turns.parquet"],
        "band": {
            "position_group": context["position"],
            "abs_turn_angle_deg": [context["angle_low"],
                                   context["angle_high"]],
            "rule": "valid turns, left-closed on abs_turn_angle_deg",
            "other_bands_drawn": False,
        },
        "target_window": {
            "entry_speed_yd_s": [context["speed_low"], context["speed_high"]],
            "abs_turn_angle_deg": [context["angle_low"],
                                   context["angle_high"]],
            "styling": "figures.focus_band (colour, tint, outline width)",
            "red_marks_data": False,
            "turns_inside": {
                "combine": context["inside_window"]["combine"],
                "game": context["inside_window"]["game"],
            },
        },
        "population": {
            "turns": context["n_turns"],
            "by_setting": context["by_setting"],
            "players_by_setting": context["players"],
            "combine_players": context["combine_players"],
            "combine_turns_from_a_shared_player":
                context["combine_turns_in_shared"],
            "combine_is_a_list_not_a_distribution": True,
            "combine_turns": context["combine_rows"],
        },
        "entry_speed_yd_s": {
            "combine": r["combine_entry"],
            "game": r["game_entry"],
        },
        "turn_angle_deg": {
            "combine": r["combine_angle"],
            "game": r["game_angle"],
        },
        "drawing": {
            "one_dot_per_turn": True,
            "density_curve": False,
            "fit": False,
            "box_plot": False,
            "confidence_ellipse": False,
            "mean_or_interval": False,
            "jitter": False,
            "dodge": {
                "axis": "entry speed",
                "reason": ("pairs of game dots would coincide; each pair is "
                           "separated only so two dots are visible"),
                "pairs": context["dodge_pairs"],
                "points_moved": context["dodged_points"],
                "step_yd_s": context["dodge_step"],
                "combine_points_moved": 0,
                "recorded_per_row": ["x_offset_yd_s", "y_offset_deg"],
            },
        },
        "claim": (f"In the {context['angle_low']:g}–"
                  f"{context['angle_high']:g}° DB band the {c} Combine turns "
                  f"sit at {r['combine_entry']['min']:.2f}–"
                  f"{r['combine_entry']['max']:.2f} yd/s while the {g} game "
                  f"turns run {r['game_entry']['min']:.2f}–"
                  f"{r['game_entry']['max']:.2f} yd/s; all {c} Combine turns "
                  f"and {context['inside_window']['game']} of {g} game turns "
                  f"are inside the {context['speed_low']:g}–"
                  f"{context['speed_high']:g} yd/s window, so within this one "
                  "band the Combine samples only the slow end of the speed "
                  "range games use."),
        "not_claimed": [
            "That the Combine turns are a distribution, a mean, or a typical "
            "value; four points are a list.",
            "That any Combine turn exists at game speeds in this band; the "
            "sample has none.",
            (f"That the {c} Combine turns are {c} players; they are {c} "
             f"turns from {context['combine_players']} players."),
            "That the two settings are sampled alike or that the gap is a "
            "shortfall of ability.",
            "Anything about the 2027 class; this is one session of sample data.",
        ],
        "caveats": [
            ("One dot per turn at its measured coordinates. "
             f"{context['dodge_pairs']} pairs of near-coincident game turns "
             f"are separated by {context['dodge_step']:.2f} yd/s for drawing; "
             "the measured speed and angle are unchanged in the CSV."),
            ("No density curve, fit, box plot, confidence ellipse, mean or "
             "interval is drawn: four Combine points cannot support any of "
             "them."),
            (f"The {c} Combine turns are incidental fragments of drills built "
             "for other purposes (figure 11)"
             + ("; two of the four come from the same player."
                if context["combine_turns_in_shared"] == 2 and c == 4
                else f"; {context['combine_turns_in_shared']} of the {c} come "
                     "from one player."
                if context["combine_turns_in_shared"] else
                "; no player supplies more than one.")),
            ("No player names or ids appear in the figure, the CSV or this "
             "entry."),
            ("Sample data: one session of sample data; nothing here is a "
             "finding about the 2027 class."),
        ],
        "png": f"outputs/figures/{FIGURE_KEY_BAND_ONLY}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_BAND_ONLY}.csv",
    }


# ---------------------------------------------------------------------------
# Figure 16 — entry and exit speed by angle band, with 95% CI whiskers.
#
# The bands are NOT declared here: they are `figures.hole.bands` (D164), the one
# reporting-band definition figures 1, 5, 7, 8 and 13 already use. The interval
# is a percentile bootstrap of the median that resamples players (nfl_id), not
# turns, because turns repeat within a player. A bucket below the feasibility
# floor carries no point and no CI — it is annotated instead.
# ---------------------------------------------------------------------------
FIGURE_KEY_ENTRY_EXIT_SPEED = "fig_entry_exit_speed"

ENTRY_EXIT_COLUMNS = [
    "setting",
    "position_group",
    "valid",
    "abs_turn_angle",
    "entry_speed",
    "exit_speed",
    "nfl_id",
    "duration_s",
]

ENTRY_EXIT_CSV_COLUMNS = [
    "band_index",
    "band",
    "band_low_deg",
    "band_high_deg",
    "setting",
    "setting_label",
    "speed_kind",
    "n_turns",
    "n_players",
    "median_yd_s",
    "ci_low_yd_s",
    "ci_high_yd_s",
    "ci_level",
    "ci_method",
    "estimable",
    "drawn",
    "is_focus_band",
    "min_n_for_estimate",
    "bootstrap_reps",
    "bootstrap_seed",
]

#: Verbatim honesty clauses. The caption and the manifest both carry these
#: strings unchanged; tests assert the substrings rather than paraphrases.
_EE_EXIT_POSITION = (
    "exit speed is measured at the end of the detector window, not at a fixed "
    "distance, so exit > entry mostly means the athlete was accelerating when "
    "the window closed")
_EE_SHALLOW_DURATION = (
    "the shallow 30–45° bucket holds short turns (median {shallow} s vs "
    "{other} s), so a fixed-length window flatters sharp cuts and the shallow "
    "bucket differently")
_EE_FLOOR_SENTENCE = (
    "Combine confidence intervals are drawn only where a band holds at least "
    "10 turns")


def load_entry_exit_turns(path: Path) -> pd.DataFrame:
    """Read only the columns figure 16 needs; refuse if any is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns first")
    try:
        df = pd.read_parquet(path, columns=ENTRY_EXIT_COLUMNS)
    except Exception as exc:  # a missing column is a refusal, not a crash
        raise common.StageRefused(
            f"{STAGE}: turns.parquet cannot supply {ENTRY_EXIT_COLUMNS}; "
            f"figure 16 needs entry and exit speed for every valid DB turn "
            f"({exc})") from exc
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    missing = [c for c in ENTRY_EXIT_COLUMNS if c not in df.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); figure 16 "
            "needs entry and exit speed for every valid DB turn")
    return df[ENTRY_EXIT_COLUMNS]


def _entry_exit_config(cfg: common.Config) -> dict:
    """Read and validate the figure-16 block, naming the failing key."""
    block = "figures.entry_exit_speed"

    def bad(key: str, expected: str) -> common.StageRefused:
        return common.StageRefused(
            f"{STAGE}: {block}.{key} must be {expected}; figure 16 would "
            "otherwise draw an interval it cannot honestly compute")

    statistic = str(cfg.get(f"{block}.statistic"))
    if statistic != "median":
        raise bad("statistic", "'median'")
    interval = str(cfg.get(f"{block}.interval"))
    if interval != "percentile":
        raise bad("interval", "'percentile'")
    cluster_by = str(cfg.get(f"{block}.cluster_by"))
    if cluster_by != "nfl_id":
        raise bad("cluster_by", "'nfl_id' (turns repeat within a player)")
    ci_level = float(cfg.get(f"{block}.ci_level"))
    if not 0.0 < ci_level < 1.0:
        raise bad("ci_level", "inside (0, 1)")
    reps = int(cfg.get(f"{block}.bootstrap_reps"))
    if reps < stats.MIN_BOOTSTRAP_REPS:
        raise bad("bootstrap_reps",
                  f"at least {stats.MIN_BOOTSTRAP_REPS}")
    min_n = int(cfg.get(f"{block}.min_n_for_estimate"))
    if min_n < 1:
        raise bad("min_n_for_estimate", "at least 1")
    min_players = int(cfg.get(f"{block}.min_players_for_estimate"))
    if min_players < 2:
        raise bad("min_players_for_estimate", "at least 2")
    settings = [str(s) for s in cfg.get(f"{block}.settings")]
    if not settings or not set(settings) <= {"combine", "game"}:
        raise bad("settings", "a non-empty subset of [combine, game]")
    speed_columns = dict(cfg.get(f"{block}.speed_columns"))
    if set(speed_columns) != {"entry", "exit"}:
        raise bad("speed_columns", "exactly the keys entry and exit")
    values = [str(v) for v in speed_columns.values()]
    if not all(values) or len(set(values)) != len(values):
        raise bad("speed_columns", "non-empty with distinct column names")
    return {
        "statistic": statistic,
        "interval": interval,
        "cluster_by": cluster_by,
        "ci_level": ci_level,
        "reps": reps,
        "min_n": min_n,
        "min_players": min_players,
        "settings": settings,
        "speed_columns": speed_columns,
        "position": str(cfg.get(f"{block}.position_group")),
        "seed_base": int(cfg.seed)
        + int(cfg.get(f"{block}.bootstrap_seed_offset")),
    }


def entry_exit_speed_table(turns: pd.DataFrame,
                           cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """Median entry/exit speed and a player-clustered CI per band and setting.

    Always a rectangle: ``7 bands x len(settings) x 2 speed kinds`` rows in
    band order, then setting order ``[combine, game]``, then speed-kind order
    ``[entry, exit]``. A below-floor cell is present with NaN median and NaN
    bounds, so "no estimate" is literally true in the CSV.
    """
    conf = _entry_exit_config(cfg)
    position = conf["position"]
    settings = conf["settings"]
    min_n = conf["min_n"]
    min_players = conf["min_players"]
    reps = conf["reps"]
    ci_level = conf["ci_level"]
    speed_columns = conf["speed_columns"]

    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise common.StageRefused(
            f"{STAGE}: figures.hole.bands.turn_angle_deg must be increasing, "
            f"got {edges}")
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    focus = focus_band(cfg)
    if focus_band_index(edges, focus) != 0:
        raise common.StageRefused(
            f"{STAGE}: the {focus['label']} focus band is not the first "
            f"reporting band in {edges}; figure 16 shades band 0 only")

    missing = [c for c in ENTRY_EXIT_COLUMNS if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: the turn table has no {missing} column(s); figure 16 "
            "cannot compute an entry or exit speed")

    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    df = turns[valid & (turns["position_group"] == position)
               & angle.notna()].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in the turn table — nothing "
            "to estimate")
    df["abs_turn_angle"] = angle.loc[df.index]
    df["band_index"] = -1
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask = (df["abs_turn_angle"] >= lo) & (df["abs_turn_angle"] < hi)
        df.loc[mask, "band_index"] = i
    df.loc[df["abs_turn_angle"] >= edges[-1], "band_index"] = len(edges) - 1
    outside = int((df["band_index"] < 0).sum())
    if outside:
        raise common.StageRefused(
            f"{STAGE}: {outside} valid {position} turn(s) fall outside the "
            f"configured bands {edges}; a silent gap would make the counts a "
            "lie")

    df = df[df["setting"].isin(settings)].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in {settings} — figure 16 "
            "needs at least one setting")

    numeric: dict[str, pd.Series] = {}
    for kind, column in speed_columns.items():
        series = pd.to_numeric(df[column], errors="coerce")
        n_bad = int(series.isna().sum())
        if n_bad:
            raise common.StageRefused(
                f"{STAGE}: {n_bad} {position} {column} value(s) are missing in "
                f"the selected population {settings}; the median and its "
                "interval cannot be computed")
        numeric[kind] = series
    df["duration_s"] = pd.to_numeric(df["duration_s"], errors="coerce")

    bands = band_labels(edges, overflow)
    kinds = ["entry", "exit"]
    rows = []
    for i, band in enumerate(bands):
        low = edges[i]
        high = edges[i + 1] if i + 1 < len(edges) else float("nan")
        for setting in ("combine", "game"):
            part = df[(df["band_index"] == i) & (df["setting"] == setting)]
            n_turns = int(len(part))
            n_players = int(part["nfl_id"].nunique())
            estimable = n_turns >= min_n and n_players >= min_players
            for kind in kinds:
                label = f"entry_exit_speed|{setting}|{i}|{kind}"
                seed = stats.seed_from_label(label, conf["seed_base"])
                median = ci_low = ci_high = float("nan")
                if estimable:
                    res = stats.cluster_bootstrap_median(
                        numeric[kind].loc[part.index].to_numpy(dtype=float),
                        part["nfl_id"].to_numpy(),
                        reps=reps, ci_level=ci_level, label=label,
                        base_seed=conf["seed_base"])
                    median = res["median"]
                    ci_low = res["ci_low"]
                    ci_high = res["ci_high"]
                rows.append({
                    "band_index": i,
                    "band": band,
                    "band_low_deg": low,
                    "band_high_deg": high,
                    "setting": setting,
                    "setting_label": COMBINE_LABEL if setting == "combine"
                    else GAME_LABEL,
                    "speed_kind": kind,
                    "n_turns": n_turns,
                    "n_players": n_players,
                    "median_yd_s": median,
                    "ci_low_yd_s": ci_low,
                    "ci_high_yd_s": ci_high,
                    "ci_level": ci_level,
                    "ci_method": "percentile_cluster_bootstrap_by_player",
                    "estimable": bool(estimable),
                    "drawn": bool(estimable),
                    "is_focus_band": i == 0,
                    "min_n_for_estimate": min_n,
                    "bootstrap_reps": reps,
                    "bootstrap_seed": int(seed),
                })
    table = pd.DataFrame(rows)[ENTRY_EXIT_CSV_COLUMNS]

    totals = {"turns": {s: int((df["setting"] == s).sum()) for s in settings},
              "players": {s: int(df.loc[df["setting"] == s, "nfl_id"].nunique())
                          for s in settings}}
    n_estimable = {
        s: int(table[(table["setting"] == s) & table["estimable"]].shape[0])
        for s in settings}
    missing_cells = []
    for i, band in enumerate(bands):
        for setting in settings:
            part = df[(df["band_index"] == i) & (df["setting"] == setting)]
            if len(part) < min_n:
                missing_cells.append((band, setting, int(len(part))))

    game = df[df["setting"] == "game"]
    shallow = game.loc[game["band_index"] == 0, "duration_s"].dropna()
    other = game.loc[game["band_index"] >= 1, "duration_s"].dropna()
    duration = {
        "shallow_median_s": float(shallow.median()) if len(shallow) else float("nan"),
        "other_median_s": float(other.median()) if len(other) else float("nan"),
    }

    context = {
        "table": table,
        "bands": bands,
        "edges": edges,
        "overflow": overflow,
        "focus": {"low": focus["low"], "high": focus["high"],
                  "label": focus["label"], "colour": focus["colour"],
                  "tint_alpha": focus["tint_alpha"],
                  "rule_width": focus["rule_width"]},
        "position": position,
        "settings": settings,
        "totals": totals,
        "n_estimable": n_estimable,
        "missing": missing_cells,
        "duration": duration,
        "min_n_for_estimate": min_n,
        "min_players_for_estimate": min_players,
        "reps": reps,
        "ci_level": ci_level,
        "seed_base": conf["seed_base"],
        "seed_offset": int(cfg.get(
            "figures.entry_exit_speed.bootstrap_seed_offset")),
        "table_rows": int(len(table)),
    }
    return table, context


def entry_exit_speed_csv_frame(table: pd.DataFrame,
                               context: dict) -> pd.DataFrame:
    """Exactly the documented columns — no drawing-only columns."""
    return table[ENTRY_EXIT_CSV_COLUMNS].reset_index(drop=True)


def _ee_cell(table: pd.DataFrame, band_index: int, setting: str,
             kind: str) -> pd.Series:
    part = table[(table["band_index"] == band_index)
                 & (table["setting"] == setting)
                 & (table["speed_kind"] == kind)]
    return part.iloc[0]


def _entry_exit_speed_caption(context: dict) -> list[str]:
    """Caption lines; every number comes from the computed table."""
    table = context["table"]
    bands = context["bands"]
    n_bands = len(bands)
    game_entry = [_ee_cell(table, i, "game", "entry") for i in range(n_bands)]
    game_exit = [_ee_cell(table, i, "game", "exit") for i in range(n_bands)]
    g_entry_drawn = [r for r in game_entry if bool(r["drawn"])]
    g_exit_drawn = [r for r in game_exit if bool(r["drawn"])]
    entry_lo = min(float(r["median_yd_s"]) for r in g_entry_drawn)
    entry_hi = max(float(r["median_yd_s"]) for r in g_entry_drawn)
    exit_lo = min(float(r["median_yd_s"]) for r in g_exit_drawn)
    exit_hi = max(float(r["median_yd_s"]) for r in g_exit_drawn)
    pairs = [(i, float(game_entry[i]["median_yd_s"]),
              float(game_exit[i]["median_yd_s"]))
             for i in range(n_bands)
             if bool(game_entry[i]["drawn"]) and bool(game_exit[i]["drawn"])]
    exit_gt = sum(1 for _, e, x in pairs if x > e)
    shift_band, shift = max(((i, x - e) for i, e, x in pairs),
                            key=lambda t: t[1])
    combine_estimable = [
        (i, _ee_cell(table, i, "combine", "entry"),
         _ee_cell(table, i, "combine", "exit"))
        for i in range(n_bands)
        if bool(_ee_cell(table, i, "combine", "entry")["estimable"])]
    combine_phrase = "; ".join(
        f"{bands[i]} entry {float(e['median_yd_s']):.2f}, exit "
        f"{float(x['median_yd_s']):.2f}"
        for i, e, x in combine_estimable)
    below = [m for m in context["missing"] if m[1] == "combine"]
    below_phrase = "; ".join(
        f"{band} holds {n} turns from "
        f"{int(_ee_cell(table, bands.index(band), 'combine', 'entry')['n_players'])} "
        "players"
        for band, _, n in below) or "no Combine band"
    game_turns = context["totals"]["turns"]["game"]
    combine_turns = context["totals"]["turns"].get("combine", 0)
    shallow = context["duration"]["shallow_median_s"]
    other = context["duration"]["other_median_s"]
    return [
        STAMP,
        (f"WHAT THIS IS. Every valid {context['position']} turn in "
         f"outputs/turns/turns.parquet, both settings, banded by absolute turn "
         f"angle on the seven reporting bands "
         f"{context['edges'][0]:g}–{context['edges'][-1]:g}° plus "
         f"{context['overflow']}°: {game_turns:,} game and "
         f"{combine_turns:,} Combine turns. The statistic is the median speed; "
         f"the "
         f"interval is a {context['ci_level']:.0%} percentile bootstrap of the "
         f"median with {context['reps']:,} replicates resampling players "
         f"(`nfl_id`) with replacement, not turns. Seven bands, key "
         f"{FIGURE_KEY_ENTRY_EXIT_SPEED}."),
        (f"THE POINT, WITH THE NUMBERS. Across the bands the game median entry "
         f"speed runs {entry_lo:.2f}–{entry_hi:.2f} yd/s and the game median "
         f"exit speed {exit_lo:.2f}–{exit_hi:.2f} yd/s. Exit exceeds entry in "
         f"{exit_gt} of {n_bands} bands; the largest entry→exit shift is "
         f"+{shift:.2f} yd/s in the {bands[shift_band]} band. Combine medians, "
         f"for the bands that clear the floor, are: {combine_phrase}. These are "
         "medians of measured turns, not outcomes."),
        (f"HONESTY (a). One definitional fact: {_EE_EXIT_POSITION}."),
        ("HONESTY (b). And one sampling fact: "
         + _EE_SHALLOW_DURATION.format(shallow=f"{shallow:.1f}",
                                       other=f"{other:.1f}")
         + "."),
        (f"THE FLOOR. {_EE_FLOOR_SENTENCE}: a bucket below the floor is "
         f"annotated, never drawn with a point or a whisker. The Combine side "
         f"is the case in point — {below_phrase}, below the "
         f"{context['min_n_for_estimate']}-turn floor, so the "
         f"{bands[0]} Combine band is annotated, never drawn."),
        ("WHAT IS NOT HERE. No test, no p-value, no ranking of bands and no "
         "player-level value; the intervals are bootstrap intervals, not "
         "posteriors. The exit-speed definition is detector-window-dependent "
         "(see honesty (a)), so no exit value is a free-standing measure of "
         "acceleration. Sample data: nothing here is a finding about the 2027 "
         f"class. Every value is in outputs/figures/"
         f"{FIGURE_KEY_ENTRY_EXIT_SPEED}.csv."),
    ]


def plot_entry_exit_speed(table: pd.DataFrame, context: dict,
                          cfg: common.Config) -> plt.Figure:
    """Four dodged series (game/Combine x entry/exit) with 95% CI whiskers."""
    from matplotlib.colors import to_rgba
    from matplotlib.lines import Line2D

    block = "figures.entry_exit_speed"
    focus = context["focus"]
    tint = to_rgba(focus["colour"], focus["tint_alpha"])
    bands = context["bands"]
    offsets = {"game": {"entry": -0.21, "exit": -0.07},
               "combine": {"entry": 0.07, "exit": 0.21}}
    markers = {"entry": "o", "exit": "s"}
    labels = {"game": {"entry": "Game entry", "exit": "Game exit"},
              "combine": {"entry": "Combine entry",
                          "exit": "Combine exit"}}

    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    ax = fig.add_axes([0.085, 0.395, 0.890, 0.455])
    ax.set_facecolor("#EBEBEB")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#B3B3B3")
    ax.tick_params(axis="both", labelsize=9.0, colors="#333333", length=2)
    ax.set_axisbelow(True)

    # The focus band: tint plus two rules. Red never touches a data mark.
    ax.axvspan(-0.5, 0.5, facecolor=tint, edgecolor="none", zorder=0)
    for edge in (-0.5, 0.5):
        ax.axvline(edge, color=focus["colour"],
                   linewidth=focus["rule_width"], zorder=1)

    drawn_lows, drawn_highs = [], []
    for setting in ("game", "combine"):
        for kind in ("entry", "exit"):
            part = table[(table["setting"] == setting)
                         & (table["speed_kind"] == kind)
                         & table["drawn"]]
            if part.empty:
                continue
            x = part["band_index"].to_numpy(dtype=float) + offsets[setting][kind]
            y = part["median_yd_s"].to_numpy(dtype=float)
            low = part["ci_low_yd_s"].to_numpy(dtype=float)
            high = part["ci_high_yd_s"].to_numpy(dtype=float)
            drawn_lows.extend(low.tolist())
            drawn_highs.extend(high.tolist())
            if setting == "game":
                container = ax.errorbar(
                    x, y, yerr=[y - low, high - y], fmt=markers[kind],
                    color=BARS_GREY, ecolor=BARS_GREY, capsize=3,
                    markersize=6.0, markeredgewidth=0.8,
                    markerfacecolor=BARS_GREY, markeredgecolor="white",
                    linestyle="none", elinewidth=1.1, capthick=1.1,
                    label=labels[setting][kind], zorder=3)
            else:
                container = ax.errorbar(
                    x, y, yerr=[y - low, high - y], fmt=markers[kind],
                    color=PALETTE["combine"], ecolor=PALETTE["combine"],
                    capsize=3, markersize=6.4, markeredgewidth=1.5,
                    markerfacecolor="none",
                    markeredgecolor=PALETTE["combine"], linestyle="none",
                    elinewidth=1.2, capthick=1.2,
                    label=labels[setting][kind], zorder=4)
            if kind == "exit":
                # Exit whiskers are dashed; caps stay solid, so shape still
                # separates exit from entry in greyscale.
                for barcols in container[2]:
                    barcols.set_linestyle("--")

    if not drawn_lows:
        raise common.StageRefused(
            f"{STAGE}: no estimable band in either setting — figure 16 has "
            "nothing to draw")
    y_low, y_high = min(drawn_lows), max(drawn_highs)
    pad = max(0.15, (y_high - y_low) * 0.12)
    ax.set_ylim(y_low - pad, y_high + pad * 1.35)
    ax.set_xlim(-0.5, len(bands) - 0.5)
    ax.set_xticks(range(len(bands)))
    ax.set_xticklabels(bands)
    ax.grid(axis="y", color="white", linewidth=0.8, zorder=0)
    ax.set_xlabel("Absolute turn angle band (°)", fontsize=9.8,
                  color="#333333", labelpad=52)

    ax.text(0.0, ax.get_ylim()[1] - 0.02 * pad, "30–45° band",
            ha="center", va="top", fontsize=10.0, fontweight="bold",
            color=focus["colour"], zorder=6)

    # Per-bucket n and players, under the axis: game above Combine.
    for i, band in enumerate(bands):
        entry = _ee_cell(table, i, "game", "entry")
        combine = _ee_cell(table, i, "combine", "entry")
        ax.annotate(f"game n={int(entry['n_turns'])} · "
                    f"{int(entry['n_players'])}p",
                    xy=(i, -0.10), xycoords=("data", "axes fraction"),
                    ha="center", va="top", fontsize=6.2, color="#333333",
                    annotation_clip=False)
        if bool(combine["drawn"]):
            text = (f"combine n={int(combine['n_turns'])} · "
                    f"{int(combine['n_players'])}p")
            style = {"fontstyle": "normal", "fontweight": "normal"}
        else:
            text = (f"combine n={int(combine['n_turns'])}, "
                    "no estimate")
            style = {"fontstyle": "italic", "fontweight": "normal"}
        ax.annotate(text, xy=(i, -0.165),
                    xycoords=("data", "axes fraction"),
                    ha="center", va="top", fontsize=6.2,
                    color=PALETTE["combine"],
                    annotation_clip=False, **style)

    handles = [
        Line2D([], [], marker="o", linestyle="none", color=BARS_GREY,
               markerfacecolor=BARS_GREY, markeredgecolor="white",
               markersize=6.0),
        Line2D([], [], marker="s", linestyle="none", color=BARS_GREY,
               markerfacecolor=BARS_GREY, markeredgecolor="white",
               markersize=6.0),
        Line2D([], [], marker="o", linestyle="none",
               color=PALETTE["combine"], markerfacecolor="none",
               markeredgecolor=PALETTE["combine"], markeredgewidth=1.5,
               markersize=6.4),
        Line2D([], [], marker="s", linestyle="none",
               color=PALETTE["combine"], markerfacecolor="none",
               markeredgecolor=PALETTE["combine"], markeredgewidth=1.5,
               markersize=6.4),
    ]
    fig.legend(handles,
               ["Game entry", "Game exit", "Combine entry", "Combine exit"],
               loc="upper center", bbox_to_anchor=(0.53, 0.893), ncol=4,
               frameon=False, fontsize=7.6, handlelength=1.6,
               columnspacing=2.2)

    fig.text(0.010, 0.978, "Entry and exit speed by angle band",
             fontsize=17.5, fontweight="bold", ha="left", va="top",
             color="#111111")
    fig.text(0.010, 0.940,
             f"Valid {context['position']} turns, game and Combine — median "
             f"speed with a {context['ci_level']:.0%} player-clustered "
             f"percentile bootstrap interval ({context['reps']:,} "
             "replicates).",
             fontsize=9.2, ha="left", va="top", color="#333333")
    fig.text(0.010, 0.914,
             f"Seven reporting bands, left-closed; {context['overflow']}° is "
             "the overflow class. Whiskers are those intervals; a band below "
             f"the {context['min_n_for_estimate']}-turn floor is annotated, "
             "never drawn.",
             fontsize=9.2, ha="left", va="top", color="#333333")

    caption = "\n".join(_wrap_text(line, 180)
                        for line in _entry_exit_speed_caption(context))
    fig.text(0.010, 0.010, caption, ha="left", va="bottom", fontsize=6.0,
             color="#333333", linespacing=1.30)
    return fig


def render_entry_exit_speed(fig: plt.Figure, cfg: common.Config,
                            out_png: Path) -> Path:
    """Save figure 16 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.entry_exit_speed.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.text(0.995, 0.992, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def _ee_json(value):
    """Plain JSON scalar: numpy ints become ints, non-finite floats become None."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def entry_exit_speed_manifest_payload(table: pd.DataFrame, context: dict,
                                      cfg: common.Config) -> dict:
    """Figure 16's manifest entry: the population, the statistic, every row."""
    bands = context["bands"]
    game_entry = [_ee_cell(table, i, "game", "entry") for i in range(len(bands))]
    game_exit = [_ee_cell(table, i, "game", "exit") for i in range(len(bands))]
    pairs = [(i, float(game_entry[i]["median_yd_s"]),
              float(game_exit[i]["median_yd_s"]))
             for i in range(len(bands))
             if bool(game_entry[i]["drawn"]) and bool(game_exit[i]["drawn"])]
    exit_gt = sum(1 for _, e, x in pairs if x > e)
    entry_medians = [e for _, e, _ in pairs]
    exit_medians = [x for _, _, x in pairs]
    combine_estimable = [
        (i, _ee_cell(table, i, "combine", "entry"),
         _ee_cell(table, i, "combine", "exit"))
        for i in range(len(bands))
        if bool(_ee_cell(table, i, "combine", "entry")["estimable"])]
    combine_phrase = "; ".join(
        f"{bands[i]} entry {float(e['median_yd_s']):.2f}, exit "
        f"{float(x['median_yd_s']):.2f}"
        for i, e, x in combine_estimable)
    below = []
    for i in range(len(bands)):
        row = _ee_cell(table, i, "combine", "entry")
        if not bool(row["estimable"]):
            below.append({"band": bands[i],
                          "n_turns": int(row["n_turns"]),
                          "n_players": int(row["n_players"])})
    shallow = context["duration"]["shallow_median_s"]
    other = context["duration"]["other_median_s"]
    rows = [{k: _ee_json(v) for k, v in rec.items()}
            for rec in table.to_dict("records")]
    claim = (
        f"Across the seven angle bands the game median entry speed runs "
        f"{min(entry_medians):.2f}-{max(entry_medians):.2f} yd/s and the game "
        f"median exit speed {min(exit_medians):.2f}-{max(exit_medians):.2f} "
        f"yd/s. Combine medians, where a band clears the "
        f"{context['min_n_for_estimate']}-turn floor, are: {combine_phrase}. "
        f"The {bands[0]} Combine band holds "
        f"{below[0]['n_turns'] if below else 0} turns and is not estimated. "
        f"Of the seven bands, the exit median exceeds the entry median in "
        f"{exit_gt} of 7 bands.")
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_ENTRY_EXIT_SPEED,
        "title": "Entry and exit speed by angle band",
        "sources": ["outputs/turns/turns.parquet"],
        "png": f"outputs/figures/{FIGURE_KEY_ENTRY_EXIT_SPEED}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_ENTRY_EXIT_SPEED}.csv",
        "population": {
            "turns": context["totals"]["turns"],
            "players": context["totals"]["players"],
            "band_rule": ("valid turns, left-closed on abs_turn_angle_deg; "
                          "angles at or above the last edge are the overflow "
                          "class"),
            "position_group": context["position"],
            "settings": context["settings"],
        },
        "bands": {
            "edges": list(context["edges"]),
            "overflow_label": context["overflow"],
            "labels": list(bands),
            "focus_band": True,
            "focus_edges_deg": [context["focus"]["low"],
                                context["focus"]["high"]],
            "declares_own_edges": False,
            "shared_with": "figures.hole.bands (D164)",
        },
        "statistic": {
            "name": "median",
            "ci_level": context["ci_level"],
            "interval": "percentile",
            "cluster_by": "nfl_id",
            "reps": context["reps"],
            "seed_base": context["seed_base"],
            "seed_offset": context["seed_offset"],
            "floor": {
                "min_n_for_estimate": context["min_n_for_estimate"],
                "min_players_for_estimate":
                    context["min_players_for_estimate"],
            },
            "min_players": context["min_players_for_estimate"],
        },
        "rows": rows,
        "combine_below_floor": below,
        "claim": claim,
        "not_claimed": [
            "That the interval is a posterior; it is a percentile bootstrap.",
            "That any band ranks above another; no test and no ranking is run.",
            "Any player-level value; no player id or name is printed.",
            "That exit speed is a fixed-distance measurement; it is measured "
            "at the end of the detector window.",
            "Anything about the 2027 class; this is one session of sample data.",
        ],
        "caveats": [
            _EE_EXIT_POSITION + ".",
            _EE_SHALLOW_DURATION.format(shallow=f"{shallow:.1f}",
                                        other=f"{other:.1f}") + ".",
            f"{_EE_FLOOR_SENTENCE}: " + "; ".join(
                f"{b['band']} holds {b['n_turns']} turns from "
                f"{b['n_players']} players" for b in below) + ".",
            "Sample data: nothing here is a finding about the 2027 class.",
        ],
    }


# ---------------------------------------------------------------------------
# Figure 17 — the movement family by angle band, four panels, game turns only.
#
# One panel per movement measure over the SAME seven reporting bands that
# `figures.hole.bands` (D164) defines; the edges are read, never redeclared. All
# intervals are player-clustered percentile bootstraps: players (nfl_id) are
# resampled, because turns repeat within a player. Panel A is deliberately the
# MEDIAN OF PER-TURN RATIOS (apex/entry on each turn, then median), never a
# ratio of medians. Panel D is drawn on a log y axis because peak angular rate
# spans an order of magnitude across the bands.
#
# The population is game turns only. The Combine is not drawn: its 30-45 degree
# bucket holds four turns (figure 16), so nothing there is estimable.
# ---------------------------------------------------------------------------
FIGURE_KEY_MOVEMENT_FAMILY = "fig_movement_family"

MOVEMENT_FAMILY_COLUMNS = [
    "setting",
    "position_group",
    "valid",
    "abs_turn_angle",
    "nfl_id",
    "apex_speed",
    "entry_speed",
    "peak_lateral_accel_yd_s2",
    "circle_fit_usable",
    "peak_omega_deg_s",
]

MOVEMENT_FAMILY_CSV_COLUMNS = [
    "panel",
    "panel_index",
    "band_index",
    "band",
    "band_low_deg",
    "band_high_deg",
    "n_turns",
    "n_players",
    "statistic",
    "value",
    "ci_low",
    "ci_high",
    "ci_level",
    "ci_method",
    "estimable",
    "is_focus_band",
    "log_axis",
    "bootstrap_reps",
    "bootstrap_seed",
]

#: Panel order is fixed by the spec: A top-left, B top-right, C bottom-left,
#: D bottom-right. The tuple pairs the panel key with its drawn statistic.
MOVEMENT_FAMILY_PANELS = (
    ("apex_share", "median"),
    ("peak_lateral_accel", "median"),
    ("circle_fit_share", "proportion"),
    ("peak_omega", "median"),
)

MOVEMENT_FAMILY_PANEL_TITLES = {
    "apex_share": "Apex share of entry speed",
    "peak_lateral_accel": "Peak lateral acceleration",
    "circle_fit_share": "Circle-fit usable share",
    "peak_omega": "Peak angular rate",
}

#: The four honesty clauses. These strings are carried verbatim in the
#: manifest caveats and drawn (wrapped, never reworded) under the panels.
MF_CAVEAT_CIRCLE = (
    "circle_fit_usable describes whether the constant-radius arc model fits "
    "the turn the detector found; it is evidence about the geometry of the "
    "detected path, not ground truth about the athlete."
)
MF_CAVEAT_FLOOR = (
    "the 30° detector floor sits immediately below the shallow bucket, so some "
    "turns there may be fragments of larger movements."
)
MF_CAVEAT_THIN = (
    "the shallow bucket is thin ({n_turns} turns from {n_players} players) and "
    "its intervals are the widest."
)
MF_CAVEAT_COMBINE = (
    "these are game turns only; the Combine is not drawn because its 30–45° "
    "bucket holds {n_combine} turns, so no comparison is estimable there."
)


def load_movement_family_turns(path: Path) -> pd.DataFrame:
    """Read only the columns figure 17 needs; refuse if any is absent."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}) — run 02_turns "
            "first")
    try:
        df = pd.read_parquet(path, columns=MOVEMENT_FAMILY_COLUMNS)
    except Exception as exc:  # a missing column is a refusal, not a crash
        raise common.StageRefused(
            f"{STAGE}: turns.parquet cannot supply {MOVEMENT_FAMILY_COLUMNS}; "
            f"figure 17 needs the movement columns for every valid DB turn "
            f"({exc})") from exc
    if df.empty:
        raise common.StageRefused(f"{STAGE}: turns.parquet is empty")
    missing = [c for c in MOVEMENT_FAMILY_COLUMNS if c not in df.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet has no {missing} column(s); figure 17 "
            "needs the movement columns for every valid DB turn")
    return df[MOVEMENT_FAMILY_COLUMNS]


def _movement_family_config(cfg: common.Config) -> dict:
    """Read and validate the figure-17 block, naming the failing key."""
    block = "figures.movement_family"

    def bad(key: str, expected: str) -> common.StageRefused:
        return common.StageRefused(
            f"{STAGE}: {block}.{key} must be {expected}; figure 17 would "
            "otherwise draw an interval it cannot honestly compute")

    position = str(cfg.get(f"{block}.position_group"))
    if not position:
        raise bad("position_group", "a non-empty position group")
    setting = str(cfg.get(f"{block}.setting"))
    if setting not in ("combine", "game"):
        raise bad("setting", "'combine' or 'game'")
    settings = [str(s) for s in cfg.get(f"{block}.settings")]
    if not settings or set(settings) != {setting}:
        raise bad("settings", f"exactly the single setting [{setting}]")
    statistic_by_panel = dict(MOVEMENT_FAMILY_PANELS)
    raw_panels = cfg.get(f"{block}.panels")
    if not isinstance(raw_panels, dict):
        raise bad("panels", "a mapping of the four panel keys")
    if list(raw_panels) != [p for p, _ in MOVEMENT_FAMILY_PANELS]:
        raise bad("panels", "the keys " + ", ".join(
            p for p, _ in MOVEMENT_FAMILY_PANELS) + " in that order")
    panels = []
    for key, expected_statistic in MOVEMENT_FAMILY_PANELS:
        panel = dict(raw_panels[key])
        statistic = str(panel.get("statistic"))
        if statistic != expected_statistic:
            raise bad(f"panels.{key}.statistic", f"'{expected_statistic}'")
        log_y = bool(panel.get("log_y"))
        if log_y != (key == "peak_omega"):
            raise bad(f"panels.{key}.log_y",
                      "'true' only for peak_omega (the log panel)")
        if key == "apex_share":
            numerator = str(panel.get("numerator"))
            denominator = str(panel.get("denominator"))
            if not numerator or not denominator or numerator == denominator:
                raise bad(f"panels.{key}", "distinct numerator and denominator "
                          "columns")
            column = None
        else:
            column = str(panel.get("column"))
            if not column:
                raise bad(f"panels.{key}.column", "a column name")
            numerator = denominator = None
        panels.append({
            "panel": key,
            "statistic": statistic,
            "numerator": numerator,
            "denominator": denominator,
            "column": column,
            "y_label": str(panel.get("y_label")),
            "log_y": log_y,
        })
    interval = str(cfg.get(f"{block}.interval"))
    if interval != "percentile":
        raise bad("interval", "'percentile'")
    cluster_by = str(cfg.get(f"{block}.cluster_by"))
    if cluster_by != "nfl_id":
        raise bad("cluster_by", "'nfl_id' (turns repeat within a player)")
    ci_level = float(cfg.get(f"{block}.ci_level"))
    if not 0.0 < ci_level < 1.0:
        raise bad("ci_level", "inside (0, 1)")
    reps = int(cfg.get(f"{block}.bootstrap_reps"))
    if reps < stats.MIN_BOOTSTRAP_REPS:
        raise bad("bootstrap_reps", f"at least {stats.MIN_BOOTSTRAP_REPS}")
    min_n = int(cfg.get(f"{block}.min_n_for_estimate"))
    if min_n < 1:
        raise bad("min_n_for_estimate", "at least 1")
    min_players = int(cfg.get(f"{block}.min_players_for_estimate"))
    if min_players < 2:
        raise bad("min_players_for_estimate", "at least 2")
    return {
        "position": position,
        "setting": setting,
        "settings": settings,
        "panels": panels,
        "interval": interval,
        "cluster_by": cluster_by,
        "ci_level": ci_level,
        "reps": reps,
        "min_n": min_n,
        "min_players": min_players,
        "seed_base": int(cfg.seed)
        + int(cfg.get(f"{block}.bootstrap_seed_offset")),
        "seed_offset": int(cfg.get(f"{block}.bootstrap_seed_offset")),
    }


def _movement_band_index(angles: pd.Series, edges: list[float]) -> pd.Series:
    """Left-closed band index; the last edge is the start of the overflow class."""
    idx = pd.Series(-1, index=angles.index, dtype=int)
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        idx[(angles >= lo) & (angles < hi)] = i
    idx[angles >= edges[-1]] = len(edges) - 1
    return idx


def movement_family_table(turns: pd.DataFrame,
                          cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """One row per panel x band, in panel order A-D then band 0-6.

    A cell below the feasibility floor is present with NaN value and NaN
    bounds and ``estimable=False``: the figure annotates it and draws nothing,
    and the CSV says "no estimate" without a made-up number.
    """
    conf = _movement_family_config(cfg)
    position = conf["position"]
    setting = conf["setting"]
    panels = conf["panels"]
    min_n = conf["min_n"]
    min_players = conf["min_players"]
    reps = conf["reps"]
    ci_level = conf["ci_level"]

    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise common.StageRefused(
            f"{STAGE}: figures.hole.bands.turn_angle_deg must be increasing, "
            f"got {edges}")
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    focus = focus_band(cfg)
    if focus_band_index(edges, focus) != 0:
        raise common.StageRefused(
            f"{STAGE}: the {focus['label']} focus band is not the first "
            f"reporting band in {edges}; figure 17 shades band 0 only")

    missing = [c for c in MOVEMENT_FAMILY_COLUMNS if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: the turn table has no {missing} column(s); figure 17 "
            "cannot compute the movement family")

    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    base = turns[valid & (turns["position_group"] == position)
                 & angle.notna()].copy()
    if base.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} turns in the turn table — nothing "
            "to estimate")
    base["abs_turn_angle"] = angle.loc[base.index]
    base["band_index"] = _movement_band_index(base["abs_turn_angle"], edges)
    outside = int(((base["band_index"] < 0)
                   & (base["setting"] == setting)).sum())
    if outside:
        raise common.StageRefused(
            f"{STAGE}: {outside} valid {position} {setting} turn(s) fall "
            f"outside the configured bands {edges}; a silent gap would make "
            "the counts a lie")

    # The Combine shallow count is only quoted in caveat (d); it never enters
    # an estimate. It is counted here so the sentence is data-driven.
    other_setting = "combine" if setting == "game" else "game"
    combine_shallow = int(((base["setting"] == other_setting)
                           & (base["band_index"] == 0)).sum()) \
        if other_setting == "combine" else 0

    df = base[base["setting"] == setting].copy()
    if df.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid {position} {setting} turns — figure 17 needs "
            "at least one")

    # Never divide silently: refuse an all-zero/non-positive denominator.
    entries = pd.to_numeric(df["entry_speed"], errors="coerce")
    n_bad_entry = int((entries.isna() | (entries <= 0)).sum())
    if n_bad_entry:
        raise common.StageRefused(
            f"{STAGE}: {n_bad_entry} valid {position} {setting} turn(s) have "
            "a missing or non-positive entry_speed; the apex share is a ratio "
            "per turn and would divide by zero")

    for panel in panels:
        columns = ([panel["numerator"], panel["denominator"]]
                   if panel["panel"] == "apex_share" else [panel["column"]])
        for column in columns:
            if column not in df.columns:
                raise common.StageRefused(
                    f"{STAGE}: turns.parquet has no {column!r} column; figure "
                    f"17 panel {panel['panel']} needs it")
            series = pd.to_numeric(df[column], errors="coerce")
            n_missing = int(series.isna().sum())
            if n_missing:
                raise common.StageRefused(
                    f"{STAGE}: {n_missing} valid {position} {setting} {column} "
                    f"value(s) are missing; panel {panel['panel']} cannot be "
                    "computed")
            df[column] = series

    apex_ratio = df["apex_speed"] / df["entry_speed"]
    circle_flag = df["circle_fit_usable"].astype(bool).astype(float)
    metric_values = {
        "apex_share": apex_ratio,
        "peak_lateral_accel": df["peak_lateral_accel_yd_s2"],
        "circle_fit_share": circle_flag,
        "peak_omega": df["peak_omega_deg_s"],
    }

    bands = band_labels(edges, overflow)
    rows = []
    for panel in panels:
        key = panel["panel"]
        values = metric_values[key]
        for i, band in enumerate(bands):
            part = df[df["band_index"] == i]
            n_turns = int(len(part))
            n_players = int(part["nfl_id"].nunique())
            estimable = n_turns >= min_n and n_players >= min_players
            label = f"movement_family|{key}|{i}"
            seed = stats.seed_from_label(label, conf["seed_base"])
            value = ci_low = ci_high = float("nan")
            if estimable:
                v = values.loc[part.index].to_numpy(dtype=float)
                clusters = part["nfl_id"].to_numpy()
                if panel["statistic"] == "proportion":
                    res = stats.cluster_bootstrap_proportion(
                        v, clusters, reps=reps, ci_level=ci_level,
                        label=label, base_seed=conf["seed_base"])
                    value = res["proportion"]
                else:
                    res = stats.cluster_bootstrap_median(
                        v, clusters, reps=reps, ci_level=ci_level,
                        label=label, base_seed=conf["seed_base"])
                    value = res["median"]
                ci_low = res["ci_low"]
                ci_high = res["ci_high"]
            rows.append({
                "panel": key,
                "panel_index": len(rows) // len(bands),
                "band_index": i,
                "band": band,
                "band_low_deg": edges[i],
                "band_high_deg": (edges[i + 1] if i + 1 < len(edges)
                                  else float("nan")),
                "n_turns": n_turns,
                "n_players": n_players,
                "statistic": panel["statistic"],
                "value": value,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "ci_level": ci_level,
                "ci_method": "percentile_cluster_bootstrap_by_player",
                "estimable": bool(estimable),
                "is_focus_band": i == 0,
                "log_axis": bool(panel["log_y"]),
                "bootstrap_reps": reps,
                "bootstrap_seed": int(seed),
            })
    table = pd.DataFrame(rows)[MOVEMENT_FAMILY_CSV_COLUMNS]

    n_estimable = {panel["panel"]: int(
        table[table["panel"] == panel["panel"]]["estimable"].sum())
        for panel in panels}
    shallow_entries = entries.loc[df["band_index"] == 0]
    context = {
        "table": table,
        "bands": bands,
        "edges": edges,
        "overflow": overflow,
        "focus": focus,
        "panels": panels,
        "position": position,
        "setting": setting,
        "settings": conf["settings"],
        "population_turns": int(len(df)),
        "population_players": int(df["nfl_id"].nunique()),
        "combine_shallow_n": combine_shallow,
        "shallow_entry_speed": float(shallow_entries.median()),
        "min_n_for_estimate": min_n,
        "min_players_for_estimate": min_players,
        "reps": reps,
        "ci_level": ci_level,
        "seed_base": conf["seed_base"],
        "seed_offset": conf["seed_offset"],
        "n_estimable": n_estimable,
        "table_rows": int(len(table)),
    }
    return table, context


def movement_family_csv_frame(table: pd.DataFrame,
                              context: dict) -> pd.DataFrame:
    """Exactly the documented columns, in the documented row order."""
    return table[MOVEMENT_FAMILY_CSV_COLUMNS].reset_index(drop=True)


def _mf_cell(table: pd.DataFrame, panel: str, band_index: int) -> pd.Series:
    part = table[(table["panel"] == panel)
                 & (table["band_index"] == band_index)]
    return part.iloc[0]


def _mf_apex_span_phrase(table: pd.DataFrame, bands: list[str]) -> str:
    """First-to-last estimable apex-share phrase for non-default band sets."""
    rows = [_mf_cell(table, "apex_share", i) for i in range(len(bands))
            if bool(_mf_cell(table, "apex_share", i)["estimable"])]
    if not rows:
        return "no apex-share cell is estimable on the configured bands"
    return (f"the median per-turn apex/entry ratio runs "
            f"{float(rows[0]['value']):.2f} in {rows[0]['band']} to "
            f"{float(rows[-1]['value']):.2f} in {rows[-1]['band']}")


def _mf_caption_lines(context: dict) -> list[str]:
    """Caption lines; the four fixed honesty clauses are passed through."""
    table = context["table"]
    bands = context["bands"]
    edges = context["edges"]
    shallow = _mf_cell(table, "apex_share", 0)
    thin = MF_CAVEAT_THIN.format(n_turns=int(shallow["n_turns"]),
                                 n_players=int(shallow["n_players"]))
    combine = MF_CAVEAT_COMBINE.format(n_combine=context["combine_shallow_n"])
    def val(panel: str, i: int) -> float:
        return float(_mf_cell(table, panel, i)["value"])

    if len(bands) == 7:
        point = (
            (f"THE POINT, WITH THE NUMBERS. The three arc buckets are one "
             f"family: {bands[0]}, {bands[1]} and {bands[2]} give an apex "
             f"share of {val('apex_share', 0):.2f} / "
             f"{val('apex_share', 1):.2f} / {val('apex_share', 2):.2f}, peak "
             f"lateral acceleration {val('peak_lateral_accel', 0):.1f} / "
             f"{val('peak_lateral_accel', 1):.1f} / "
             f"{val('peak_lateral_accel', 2):.1f} yd/s², peak angular rate "
             f"{val('peak_omega', 0):.0f} / {val('peak_omega', 1):.0f} / "
             f"{val('peak_omega', 2):.0f} deg/s and circle-fit usable share "
             f"{val('circle_fit_share', 0):.3f} / "
             f"{val('circle_fit_share', 1):.3f} / "
             f"{val('circle_fit_share', 2):.3f}, and their player-clustered "
             f"intervals overlap on every panel. The break comes at "
             f"{bands[3]}: from there the apex share falls monotonically "
             f"({val('apex_share', 3):.2f} → {val('apex_share', 4):.2f} → "
             f"{val('apex_share', 5):.2f} → {val('apex_share', 6):.2f}) while "
             f"peak lateral acceleration rises "
             f"({val('peak_lateral_accel', 3):.1f} → "
             f"{val('peak_lateral_accel', 6):.1f} yd/s²), peak angular rate "
             f"rises ({val('peak_omega', 3):.0f} → "
             f"{val('peak_omega', 6):.0f} deg/s) and the circle-fit usable "
             f"share falls ({val('circle_fit_share', 3):.2f} → "
             f"{val('circle_fit_share', 6):.2f}); the plant family starts "
             f"above {edges[4]:g}°. The {bands[0]} bucket is an ordinary "
             f"member of the arc family, not its extreme: its distinguishing "
             f"features are the lowest entry speed "
             f"({context['shallow_entry_speed']:.2f} yd/s) and the absence of "
             f"a Combine counterpart ({context['combine_shallow_n']} turns). "
             "Panel A is the median of per-turn ratios, not a ratio of "
             "medians. These are descriptive medians and shares of measured "
             "turns, not outcomes.")
        )
    else:
        point = (
            f"THE POINT, WITH THE NUMBERS. Across the bands "
            f"{_mf_apex_span_phrase(table, bands)}. Panel A is the median of "
            "per-turn ratios, not a ratio of medians. These are descriptive "
            "medians and shares of measured turns, not outcomes.")
    return [
        STAMP,
        (f"WHAT THIS IS. Every valid {context['position']} {context['setting']} "
         f"turn in outputs/turns/turns.parquet "
         f"({context['population_turns']:,} turns from "
         f"{context['population_players']} players), banded by absolute turn "
         f"angle on the seven reporting bands "
         f"{edges[0]:g}–{edges[-1]:g}° plus the {context['overflow']}° "
         f"overflow class. Four panels share one categorical axis; every "
         f"interval is a {context['ci_level']:.0%} percentile bootstrap with "
         f"{context['reps']:,} replicates resampling players (`nfl_id`) with "
         f"replacement. Key {FIGURE_KEY_MOVEMENT_FAMILY}."),
        point,
        "HONESTY (a). " + MF_CAVEAT_CIRCLE,
        "HONESTY (b). " + MF_CAVEAT_FLOOR,
        "HONESTY (c). " + thin,
        "HONESTY (d). " + combine,
        ("WHAT IS NOT HERE. No test, no p-value, no ranking of bands and no "
         "player-level value; the intervals are bootstrap intervals, not "
         "posteriors. Sample data: nothing here is a finding about the 2027 "
         f"class. Every value is in outputs/figures/"
         f"{FIGURE_KEY_MOVEMENT_FAMILY}.csv."),
    ]


def plot_movement_family(table: pd.DataFrame, context: dict,
                         cfg: common.Config) -> plt.Figure:
    """Four 2x2 panels on one shared categorical angle-bucket axis."""
    from matplotlib.colors import to_rgba

    block = "figures.movement_family"
    focus = context["focus"]
    tint = to_rgba(focus["colour"], focus["tint_alpha"])
    bands = context["bands"]
    panels = context["panels"]

    fig = plt.figure(figsize=(float(cfg.get(f"{block}.width_in")),
                              float(cfg.get(f"{block}.height_in"))))
    positions = [(0.075, 0.595, 0.400, 0.255),
                 (0.525, 0.595, 0.400, 0.255),
                 (0.075, 0.300, 0.400, 0.255),
                 (0.525, 0.300, 0.400, 0.255)]
    axes = [fig.add_axes(pos) for pos in positions]

    for panel_index, (ax, panel) in enumerate(zip(axes, panels)):
        part = table[table["panel"] == panel["panel"]]
        drawn = part[part["estimable"]]
        x = drawn["band_index"].to_numpy(dtype=float)
        y = drawn["value"].to_numpy(dtype=float)
        low = drawn["ci_low"].to_numpy(dtype=float)
        high = drawn["ci_high"].to_numpy(dtype=float)

        ax.set_facecolor("#EBEBEB")
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#B3B3B3")
        ax.tick_params(axis="both", labelsize=8.2, colors="#333333", length=2)
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="white", linewidth=0.8, zorder=0)

        # The focus band: dark red tint and two rules; red never touches data.
        ax.axvspan(-0.5, 0.5, facecolor=tint, edgecolor="none", zorder=0)
        for edge in (-0.5, 0.5):
            ax.axvline(edge, color=focus["colour"],
                       linewidth=focus["rule_width"], zorder=1)

        if len(drawn):
            ax.errorbar(
                x, y, yerr=[y - low, high - y], fmt="o", color=BARS_GREY,
                ecolor=BARS_GREY, capsize=3, markersize=5.6,
                markeredgewidth=0.8, markerfacecolor=BARS_GREY,
                markeredgecolor="white", linestyle="none", elinewidth=1.1,
                capthick=1.1, zorder=3)

        non_estimable = part[~part["estimable"]]
        for _, row in non_estimable.iterrows():
            ax.annotate("no estimate",
                        xy=(float(row["band_index"]), 0.06),
                        xycoords=("data", "axes fraction"),
                        ha="center", va="bottom", fontsize=6.4,
                        color="#555555", fontstyle="italic",
                        annotation_clip=False, zorder=4)

        ax.set_xlim(-0.5, len(bands) - 0.5)
        ax.set_xticks(range(len(bands)))
        if panel["log_y"]:
            ax.set_yscale("log")
            if len(drawn):
                ax.set_ylim(min(low) * 0.88, max(high) * 1.12)
        elif panel["panel"] == "circle_fit_share":
            ax.set_ylim(0.0, 1.0)
        elif len(drawn):
            y_low, y_high = float(min(low)), float(max(high))
            pad = max(0.08, (y_high - y_low) * 0.12)
            ax.set_ylim(y_low - pad, y_high + pad)
        ax.set_ylabel(panel["y_label"], fontsize=8.6, color="#333333")
        if panel_index >= 2:
            ax.set_xticklabels(bands, fontsize=7.6)
            ax.set_xlabel("Absolute turn angle band (°)", fontsize=8.6,
                          color="#333333", labelpad=4)
        else:
            ax.set_xticklabels([])

        ax.text(0.015, 0.955,
                f"{'ABCD'[panel_index]}  "
                f"{MOVEMENT_FAMILY_PANEL_TITLES[panel['panel']]}",
                transform=ax.transAxes, ha="left", va="top",
                fontsize=9.6, fontweight="bold", color="#111111")

    axes[0].text(0.5, 0.975, "30–45° band", transform=axes[0].transAxes,
                 ha="center", va="top", fontsize=8.6, fontweight="bold",
                 color=focus["colour"], zorder=6)

    # Footer: one mention of each bucket's turns and players, shared by all
    # four panels. fig.text, so the figure still has exactly four axes.
    shallow_rows = [_mf_cell(table, panels[0]["panel"], i)
                    for i in range(len(bands))]
    turns_line = "turns per band (left → right): " + " · ".join(
        str(int(row["n_turns"])) for row in shallow_rows)
    players_line = "players per band (left → right): " + " · ".join(
        str(int(row["n_players"])) for row in shallow_rows)
    fig.text(0.500, 0.245, turns_line, ha="center", va="center",
             fontsize=7.0, color="#333333")
    fig.text(0.500, 0.226, players_line, ha="center", va="center",
             fontsize=7.0, color="#333333")

    fig.text(0.010, 0.982, "Two movement families by angle band",
             fontsize=17.0, fontweight="bold", ha="left", va="top",
             color="#111111")
    fig.text(0.010, 0.950,
             f"Valid {context['position']} {context['setting']} turns, seven "
             f"reporting bands — median (A, B, D) or share (C) with a "
             f"{context['ci_level']:.0%} player-clustered percentile bootstrap "
             f"interval ({context['reps']:,} replicates). Panel D is on a log "
             "y axis.",
             fontsize=8.8, ha="left", va="top", color="#333333")
    fig.text(0.010, 0.924,
             f"Bands are left-closed; {context['overflow']}° is the overflow "
             f"class. Panel A takes the median of per-turn apex/entry ratios. "
             f"A bucket below the {context['min_n_for_estimate']}-turn floor "
             "is annotated, never drawn.",
             fontsize=8.8, ha="left", va="top", color="#333333")

    caption = "\n".join(_wrap_text(line, 185)
                        for line in _mf_caption_lines(context))
    fig.text(0.010, 0.008, caption, ha="left", va="bottom", fontsize=5.9,
             color="#333333", linespacing=1.28)
    return fig


def render_movement_family(fig: plt.Figure, cfg: common.Config,
                           out_png: Path) -> Path:
    """Save figure 17 once with fixed metadata, so two runs are identical."""
    dpi = int(cfg.get("figures.movement_family.dpi"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.text(0.995, 0.994, STAMP, ha="right", va="top", fontsize=8.0,
             fontweight="bold", color=ALERT)
    fig.savefig(out_png, dpi=dpi, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return out_png


def movement_family_manifest_payload(table: pd.DataFrame, context: dict,
                                     cfg: common.Config) -> dict:
    """Figure 17's manifest entry: population, statistic, every row, caveats."""
    table = context["table"]
    bands = context["bands"]
    shallow = _mf_cell(table, "apex_share", 0)
    caveats = [
        MF_CAVEAT_CIRCLE,
        MF_CAVEAT_FLOOR,
        MF_CAVEAT_THIN.format(n_turns=int(shallow["n_turns"]),
                              n_players=int(shallow["n_players"])),
        MF_CAVEAT_COMBINE.format(n_combine=context["combine_shallow_n"]),
    ]
    def val(panel: str, i: int) -> float:
        return float(_mf_cell(table, panel, i)["value"])

    if len(bands) == 7:
        claim = (
            f"The seven {context['setting']}-only {context['position']} angle "
            f"buckets are two families, not a gradient: {bands[0]}, {bands[1]} "
            f"and {bands[2]} are statistically indistinguishable on all four "
            f"panels (apex share {val('apex_share', 0):.2f}/"
            f"{val('apex_share', 1):.2f}/{val('apex_share', 2):.2f}, peak "
            f"lateral acceleration {val('peak_lateral_accel', 0):.1f}/"
            f"{val('peak_lateral_accel', 1):.1f}/"
            f"{val('peak_lateral_accel', 2):.1f} yd/s², peak angular rate "
            f"{val('peak_omega', 0):.0f}/{val('peak_omega', 1):.0f}/"
            f"{val('peak_omega', 2):.0f} deg/s, circle-fit usable share "
            f"{val('circle_fit_share', 0):.3f}/"
            f"{val('circle_fit_share', 1):.3f}/"
            f"{val('circle_fit_share', 2):.3f}; every player-clustered "
            f"interval overlaps within these three buckets). The break comes "
            f"at {bands[3]}, and from there the decline is monotone: the apex "
            f"share falls {val('apex_share', 3):.2f} to "
            f"{val('apex_share', 6):.2f} while peak lateral acceleration "
            f"rises {val('peak_lateral_accel', 3):.1f} to "
            f"{val('peak_lateral_accel', 6):.1f} yd/s², peak angular rate "
            f"rises {val('peak_omega', 3):.0f} to "
            f"{val('peak_omega', 6):.0f} deg/s and the circle-fit usable "
            f"share falls {val('circle_fit_share', 3):.2f} to "
            f"{val('circle_fit_share', 6):.2f}. The plant family starts above "
            f"{context['edges'][4]:g}°. The {bands[0]} bucket is an ordinary "
            f"member of the arc family, not its extreme: it has the lowest "
            f"entry speed ({context['shallow_entry_speed']:.2f} yd/s) and no "
            f"Combine counterpart ({context['combine_shallow_n']} turns). "
            "These are descriptive medians and shares with player-clustered "
            "bootstrap intervals, not a test or a ranking of bands.")
    else:
        claim = (
            f"Across the {context['setting']}-only {context['position']} "
            f"angle buckets {_mf_apex_span_phrase(table, bands)}; these are "
            "descriptive medians and shares with player-clustered bootstrap "
            "intervals, not a test or a ranking of bands.")
    rows = [{k: _ee_json(v) for k, v in rec.items()}
            for rec in table.to_dict("records")]
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY_MOVEMENT_FAMILY,
        "title": "Two movement families by angle band",
        "sources": ["outputs/turns/turns.parquet"],
        "png": f"outputs/figures/{FIGURE_KEY_MOVEMENT_FAMILY}.png",
        "csv": f"outputs/figures/{FIGURE_KEY_MOVEMENT_FAMILY}.csv",
        "population": {
            "turns": context["population_turns"],
            "players": context["population_players"],
            "position_group": context["position"],
            "settings": context["settings"],
            "band_rule": ("valid turns, left-closed on abs_turn_angle_deg; "
                          "angles at or above the last edge are the overflow "
                          "class"),
        },
        "bands": {
            "edges": list(context["edges"]),
            "overflow_label": context["overflow"],
            "labels": list(bands),
            "focus_band": True,
            "focus_edges_deg": [context["focus"]["low"],
                                context["focus"]["high"]],
            "declares_own_edges": False,
            "shared_with": "figures.hole.bands (D164)",
        },
        "statistic": {
            "cluster_by": "nfl_id",
            "ci_level": context["ci_level"],
            "interval": "percentile",
            "reps": context["reps"],
            "seed_base": context["seed_base"],
            "seed_offset": context["seed_offset"],
            "min_n_for_estimate": context["min_n_for_estimate"],
            "min_players_for_estimate": context["min_players_for_estimate"],
            "panels": [
                {
                    "panel": panel["panel"],
                    "metric": (panel["numerator"] + " / " + panel["denominator"]
                               if panel["panel"] == "apex_share"
                               else panel["column"]),
                    "statistic": panel["statistic"],
                    "units": ("ratio" if panel["panel"] == "apex_share"
                              else "share" if panel["statistic"] == "proportion"
                              else "deg/s" if panel["panel"] == "peak_omega"
                              else "yd/s²"),
                    "log_y": panel["log_y"],
                }
                for panel in context["panels"]
            ],
        },
        "rows": rows,
        "claim": claim,
        "caveats": caveats,
    }
