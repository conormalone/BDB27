"""Stage 06 figure 1 — "The Hole" (DB supply vs demand by turn-angle band).

One figure, deliberately: the 30-45 degree band is the case's whole point, so
the figure shows it two ways at once — raw counts (what the Combine produces
versus what games demand) and each setting's own share (the exposure-corrected
view). Every number on the image is counted from ``outputs/turns/turns.parquet``
at render time; nothing is hard-coded.

Design rules honoured here:
  * reporting bands come from ``config.yaml`` (``figures.hole``, D164);
  * no pie charts, no 3-D, no chart junk; grey panel, colour-blind-safe palette;
  * direct labels on the highlighted band and each setting's peak, with a
    compact legend for the rest;
  * an on-image provenance stamp, repeated in the CSV and the JSON manifest;
  * two runs produce a byte-identical PNG (fixed DPI/style, no timestamp).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # deterministic, headless

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from plotnine import (
    aes,
    element_blank,
    element_line,
    element_rect,
    element_text,
    facet_wrap,
    geom_col,
    geom_rect,
    geom_text,
    ggplot,
    labs,
    position_dodge,
    scale_fill_manual,
    scale_x_discrete,
    theme,
    theme_bw,
)

from bdb27 import common

STAGE = "06_figures"
ARTEFACT_VERSION = 1
FIGURE_KEY = "fig_the_hole"

STAMP = f"{common.PROVENANCE_SAMPLE} — {common.SAMPLE_LABEL}"

#: Okabe-Ito, colour-blind-safe. Combine = blue, game = vermillion.
PALETTE = {"combine": "#0072B2", "game": "#D55E00"}
HIGHLIGHT = "#F0E442"
ALERT = "#B00020"

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
]


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
    """Direct labels: the 30-45 band in both panels, plus each setting's peak."""
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

    top_a = float(frame.loc[frame["panel"] == frame["panel"].cat.categories[0],
                            "value"].max())
    top_b = float(frame.loc[frame["panel"] == frame["panel"].cat.categories[1],
                            "value"].max())
    texts = pd.DataFrame([
        {"panel": frame["panel"].cat.categories[0], "x": 1.0, "y": top_a * 0.30,
         "label": f"THE HOLE\n{c} vs {g} turns"},
        {"panel": frame["panel"].cat.categories[1], "x": 1.0, "y": top_b * 0.20,
         "label": f"{ratio:.1f}× more often\nin games"},
    ])
    return rect, {"rect": rect, "text": texts, "combine": c, "game": g,
                  "ratio": ratio}


def build_plot(table: pd.DataFrame, context: dict, cfg: common.Config) -> ggplot:
    frame = plot_frame(table)
    labels = _labels(table, context["bands"])
    rect, ann = _annotations(table, context)
    texts = ann["text"]

    c = ann["combine"]
    g = ann["game"]
    players = context["players_by_setting"]
    pg = context["player_games"]
    total = context["n_by_setting"]["game"]
    cov = context["coverage_turns"]
    other = context["other_turns"]
    other_pct = context["other_share"] * 100.0

    title = "The hole: games demand 30–45° DB breaks the Combine does not test"
    subtitle = (f"Valid {context['position']} turns by absolute turn angle — "
                f"Combine ({players['combine']} players, one session) vs game "
                f"({players['game']} players, {pg} player-games). Bands are left-closed.")

    caption = "\n".join([
        STAMP,
        ("The Combine contains no deliberate 30–45° break at speed: the "
         f"{c} detections are incidental fragments, not a test."),
        (f"Exposure: {players['combine']} Combine players × one session vs "
         f"{players['game']} game players × {pg} player-games — the settings are not "
         "sampled alike, which is why panel (b) shows each setting's own share."),
        (f"DB demand denominator: all {total:,} valid DB game turns. The snap-classified "
         f"COVERAGE subset is {cov:,}; {other:,} turns ({other_pct:.1f}%) are snapped "
         "\"other\"."),
        ("Bands are left-closed; the final class is >180°. Source: "
         "outputs/turns/turns.parquet (valid turns), stage 02. Counts computed at render time."),
    ])

    dodge = position_dodge(width=0.8)
    plot = (
        ggplot(frame, aes(x="band", y="value", fill="setting"))
        + geom_rect(data=rect, mapping=aes(xmin="xmin", xmax="xmax",
                                           ymin="ymin", ymax="ymax"),
                    inherit_aes=False, fill=HIGHLIGHT, alpha=0.45)
        + geom_col(position=dodge, width=0.72, colour="white", size=0.25)
        + geom_text(data=labels, mapping=aes(x="band", y="lab_y", label="label",
                                             group="setting"),
                    position=dodge, size=2.9, colour="#1A1A1A", va="bottom",
                    show_legend=False)
        + geom_text(data=texts, mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=3.2, colour=ALERT, fontweight="bold",
                    ha="center", va="center")
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


def render(plot: ggplot, cfg: common.Config, out_png: Path) -> plt.Figure:
    """Draw and save once, with fixed metadata so repeated runs are identical."""
    dpi = int(cfg.get("figures.hole.dpi"))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig = plot.draw()
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
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_KEY,
        "title": "The hole: games demand 30–45° DB breaks the Combine does not test",
        "claim": ("The Combine contains no deliberate 30–45° DB break at speed: "
                  f"the {c} sample detections are incidental fragments, not a test, "
                  f"while games demand {g} such turns."),
        "sources": ["outputs/turns/turns.parquet"],
        "caveats": [
            (f"Exposure: {players['combine']} Combine players × one session vs "
             f"{players['game']} game players × {context['player_games']} "
             "player-games; the share panel exists because the two settings are "
             "not sampled alike."),
            (f"DB demand denominator is all {total:,} valid DB game turns; the "
             f"snap-classified COVERAGE subset is {cov:,}, and {other:,} turns "
             f"({other_pct:.1f}%) are snapped as \"other\"."),
            ("Bands are left-closed; the final class is >180°."),
        ],
        "position_group": context["position"],
        "bands": context["bands"],
        "counts": table.to_dict(orient="records"),
        "png": f"outputs/figures/{FIGURE_KEY}.png",
        "csv": f"outputs/figures/{FIGURE_KEY}.csv",
    }
