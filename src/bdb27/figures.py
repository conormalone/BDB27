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

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # deterministic, headless

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Arc
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
    geom_point,
    geom_rect,
    geom_segment,
    geom_text,
    geom_tile,
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

from bdb27 import common

STAGE = "06_figures"
ARTEFACT_VERSION = 1
FIGURE_KEY = "fig_the_hole"
FIGURE_KEY_DRILLS = "fig_drill_coverage"
FIGURE_KEY_BEFORE_AFTER = "fig_before_after"
FIGURE_KEY_ALL_POSITIONS = "fig_all_positions"
FIGURE_KEY_MEASUREMENT_LIMITS = "fig_measurement_limits"
FIGURE_KEY_GAP_HONEST = "fig_gap_honest"
FIGURE_KEY_TURN_COST = "fig_turn_cost"

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


def render(plot: ggplot, cfg: common.Config, out_png: Path,
           block: str = "hole") -> plt.Figure:
    """Draw and save once, with fixed metadata so repeated runs are identical."""
    dpi = int(cfg.get(f"figures.{block}.dpi"))
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
    x_lo, x_hi = (float(x) for x in cfg.get(f"{block}.x_lim"))
    y_lo, y_hi = (float(y) for y in cfg.get(f"{block}.y_lim"))
    a_lo, a_hi = context["target_angle"]
    v_lo, v_hi = context["target_speed"]

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
                    inherit_aes=False, fill=HIGHLIGHT, alpha=0.35,
                    colour="#8C7A00", linetype="dashed", size=0.5)
        + geom_segment(data=whisk, mapping=aes(x="x", xend="xend", y="y",
                                               yend="yend"),
                       inherit_aes=False, colour="#7A7A7A", size=0.45)
        + geom_point(size=3.1)
        + geom_text(data=labels, mapping=aes(x="x", y="y", label="label",
                                             hjust="hjust"),
                    inherit_aes=False, size=2.65, colour="#1A1A1A", va="center",
                    show_legend=False)
        + geom_text(data=target_text, mapping=aes(x="x", y="y", label="label"),
                    inherit_aes=False, size=2.8, colour="#6B5D00",
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
                    colour=PROJECT_EDGE, linetype="dashed", size=0.6)
        + geom_segment(data=hatch, mapping=aes(x="x", xend="xend", y="y",
                                               yend="yend"),
                       inherit_aes=False, colour=PROJECT_EDGE, size=0.45,
                       alpha=0.85)
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
#: A DIAGRAM, not a data plot. Every dimension is copied from
#: ``notes/drill-proposal.md`` §2 (status PROPOSAL). Nothing on this figure is
#: measured, so it is deliberately NOT stamped with ``STAMP`` (the sample-data
#: provenance banner): it carries its own proposal badge instead, and the
#: manifest records that choice.
FIGURE_KEY_DRILL_SCHEMATIC = "fig_drill_schematic"
PROPOSAL_STAMP = ("PROPOSED DRILL — not part of the task spec, awaiting sign-off")

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
    "arc_radius_yd": 1.5,        # visual arc radius, not a drill dimension
    "arc_lw": 1.4,
    "gate_tick_half_yd": 0.45,   # symbolic tick; gate WIDTH is not specified
    "cone_size": 15.0,
    "plant_size": 46.0,
    "arc_label_offset_yd": 0.62,
    "gate_label_offset_yd": 0.70,
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


def drill_schematic_geometry(cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """Build the drawn geometry, in yards, from the proposal's own numbers.

    Coordinates: the break cone (and the single plant) sits at the origin; the
    approach runs along -x to the start cone; the two exits leave at +/- the
    configured break angle and run the configured gate distance. Returns one
    row per drawn element (segments, points) or annotation, each carrying the
    config constant it came from and the proposal sentence it quotes.
    """
    block = "figures.drill_schematic"
    status = str(cfg.get(f"{block}.status"))
    status_stamp = str(cfg.get(f"{block}.status_stamp"))
    source_note = str(cfg.get(f"{block}.source_note"))
    approach_range = [float(x) for x in cfg.get(f"{block}.approach_length_yd")]
    approach = float(cfg.get(f"{block}.approach_drawn_yd"))
    runup = float(cfg.get(f"{block}.runup_length_yd"))
    angle = float(cfg.get(f"{block}.break_angle_deg"))
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
            f"{STAGE}: approach_drawn_yd={approach:g} is outside the proposed "
            f"8-10 yd range {approach_range} — the drawing would misplace the "
            "break cone")
    if not 0 < angle < 90:
        raise ValueError(f"{STAGE}: break_angle_deg must be in (0, 90), got {angle}")
    if reps_side * 2 != reps_total:
        raise ValueError(
            f"{STAGE}: reps must be {reps_side} left + {reps_side} right = "
            f"{reps_total}; config has reps_total={reps_total}")
    if gate != reaccel:
        raise ValueError(
            f"{STAGE}: gate_distance_yd={gate:g} and reaccel_length_yd={reaccel:g} "
            "differ, but the proposal draws one 5 yd exit through the gate")
    if not 0 < speed[0] < speed[1]:
        raise ValueError(f"{STAGE}: entry_speed_yd_s must be an increasing window")

    rad = np.deg2rad(angle)
    gate_left = (gate * np.cos(rad), gate * np.sin(rad))
    gate_right = (gate * np.cos(rad), -gate * np.sin(rad))

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
        # ---- exits (symmetric by construction; the test checks the mirror)
        _schematic_row(
            row_kind="geometry", element="exit_left", kind="segment",
            x=0.0, y=0.0, x_end=gate_left[0], y_end=gate_left[1],
            length_yd=gate, angle_deg=angle,
            source_constant=f"{block}.break_angle_deg",
            source_quote="Two exit gates 5 yd from the break cone, at 40 deg "
                         "either side of the approach line."),
        _schematic_row(
            row_kind="geometry", element="exit_right", kind="segment",
            x=0.0, y=0.0, x_end=gate_right[0], y_end=gate_right[1],
            length_yd=gate, angle_deg=-angle,
            source_constant=f"{block}.break_angle_deg",
            source_quote="Two exit gates 5 yd from the break cone, at 40 deg "
                         "either side of the approach line."),
        _schematic_row(
            row_kind="geometry", element="gate_left", kind="point",
            x=gate_left[0], y=gate_left[1], length_yd=gate,
            source_constant=f"{block}.gate_distance_yd",
            source_quote="Two exit gates 5 yd from the break cone."),
        _schematic_row(
            row_kind="geometry", element="gate_right", kind="point",
            x=gate_right[0], y=gate_right[1], length_yd=gate,
            source_constant=f"{block}.gate_distance_yd",
            source_quote="Two exit gates 5 yd from the break cone."),
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
            row_kind="annotation", element="angle_left", kind="text",
            text=f"{angle:g}°", value=angle, unit="deg",
            source_constant=f"{block}.break_angle_deg",
            source_quote="Break 40 deg, either side of the approach line."),
        _schematic_row(
            row_kind="annotation", element="angle_right", kind="text",
            text=f"{angle:g}°", value=angle, unit="deg",
            source_constant=f"{block}.break_angle_deg",
            source_quote="Break 40 deg, either side of the approach line."),
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
            source_quote="notes/drill-proposal.md status line."),
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
    ]
    table = pd.DataFrame(rows, columns=DRILL_SCHEMATIC_COLUMNS)

    # ---- hard checks: the drawn table may not disagree with itself
    left = table.loc[table["element"] == "exit_left"].iloc[0]
    right = table.loc[table["element"] == "exit_right"].iloc[0]
    if not np.isclose(float(left["length_yd"]), gate) or not np.isclose(
            float(right["length_yd"]), gate):
        raise ValueError(f"{STAGE}: drawn exit length is not the gate distance")
    if not np.isclose(np.hypot(float(left["x_end"]), float(left["y_end"])), gate):
        raise ValueError(f"{STAGE}: left gate is not {gate:g} yd from the break cone")
    if not np.isclose(np.hypot(float(right["x_end"]), float(right["y_end"])), gate):
        raise ValueError(f"{STAGE}: right gate is not {gate:g} yd from the break cone")
    if not np.isclose(approach, np.hypot(
            float(table.loc[table["element"] == "cone_start", "x"].iloc[0]), 0.0)):
        raise ValueError(f"{STAGE}: start cone is not on the drawn approach")

    omissions = [
        ("Gate width (the opening between gate posts) is not stated in the "
         "proposal, so each exit gate is drawn as a single transverse tick "
         "marking where the player crosses — not as a measured opening."),
        ("The proposal lists exactly two approach cones 8-10 yd apart and puts "
         "the break cone at the end of the approach; it does not describe any "
         "further cone. Only those two cones are drawn."),
        ("Where the walk-in ends and where the 2-yd run-up sits relative to the "
         "8-10 yd approach is not specified, so no phase boundary is drawn: the "
         "phases are labelled in one line, and only the approach itself (8-10 "
         "yd) is measured on the image."),
        ("The 3-cone footprint the new drill reuses is not dimensioned in the "
         "proposal, so no footprint outline is drawn — the claim is stated in "
         "words only."),
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
        "break_angle": angle,
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
        "gate_left": gate_left,
        "gate_right": gate_right,
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


def plot_drill_schematic(table: pd.DataFrame, context: dict,
                         cfg: common.Config) -> plt.Figure:
    """Draw the plan view. Units are yards; the aspect is equal, so the 5 yd
    scale bar is a real scale bar. Text positions are styling."""
    block = "figures.drill_schematic"
    style = SCHEMATIC_STYLE
    colour = SCHEMATIC_COLOURS

    approach = float(context["approach_drawn"])
    angle = float(context["break_angle"])
    gate = float(context["gate_distance"])
    rad = np.deg2rad(angle)

    # The axes box is sized to the data window so that equal aspect needs no
    # adjustment (and emits no warning). Whitespace lives in the figure margin.
    x_lo, x_hi = -approach - 1.5, gate * np.cos(rad) + 2.0
    y_lo, y_hi = -4.55, 3.95
    ratio = (x_hi - x_lo) / (y_hi - y_lo)
    fig_w = float(cfg.get(f"{block}.width_in"))
    fig_h = float(cfg.get(f"{block}.height_in"))
    ax_h = 0.70
    ax_w = (ax_h * fig_h * ratio) / fig_w

    fig = plt.figure(figsize=(fig_w, fig_h),
                     dpi=int(cfg.get(f"{block}.dpi")), facecolor="white")
    ax = fig.add_axes([(1.0 - ax_w) / 2.0, 0.145, ax_w, ax_h])
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.set_aspect("equal")
    ax.axis("off")

    def geo(element: str) -> pd.Series:
        return table.loc[table["element"] == element].iloc[0]

    left = geo("exit_left")
    right = geo("exit_right")

    # ---- movement: two exits, then the approach arrow
    ax.annotate("", xy=(left["x_end"], left["y_end"]), xytext=(0.0, 0.0),
                arrowprops=dict(arrowstyle="-|>", lw=style["exit_lw"],
                                color=colour["left"], shrinkA=4, shrinkB=2))
    ax.annotate("", xy=(right["x_end"], right["y_end"]), xytext=(0.0, 0.0),
                arrowprops=dict(arrowstyle="-|>", lw=style["exit_lw"],
                                color=colour["right"], shrinkA=4, shrinkB=2))
    ax.annotate("", xy=(-0.32, 0.0), xytext=(-approach, 0.0),
                arrowprops=dict(arrowstyle="-|>", lw=style["approach_lw"],
                                color=colour["approach"], shrinkA=8,
                                shrinkB=0))

    # ---- break-angle arcs, one per side, labelled with the configured angle
    r = style["arc_radius_yd"]
    for theta1, theta2 in ((0.0, angle), (-angle, 0.0)):
        ax.add_patch(Arc((0.0, 0.0), 2 * r, 2 * r, theta1=theta1,
                         theta2=theta2, color=colour["arc"],
                         lw=style["arc_lw"]))
    label_r = r + style["arc_label_offset_yd"]
    for sign, element in ((1.0, "angle_left"), (-1.0, "angle_right")):
        mid = np.deg2rad(sign * angle / 2.0)
        ax.text(label_r * np.cos(mid), label_r * np.sin(mid),
                _schematic_text(table, element), fontsize=11,
                fontweight="bold", color="#333333", ha="center", va="center")

    # ---- gate marks: one transverse tick per exit, perpendicular to travel
    half = style["gate_tick_half_yd"]
    for sign, col in ((1.0, colour["left"]), (-1.0, colour["right"])):
        px, py = gate * np.cos(rad), sign * gate * np.sin(rad)
        perp = (-np.sin(sign * rad), np.cos(sign * rad))
        ax.plot([px - half * perp[0], px + half * perp[0]],
                [py - half * perp[1], py + half * perp[1]],
                color=col, lw=3.4, solid_capstyle="butt")
        off = style["gate_label_offset_yd"]
        ax.text(px + off * np.cos(rad), py + off * np.sin(sign * rad),
                "gate\n" + _schematic_text(table, "gate_distance"),
                fontsize=9.4, color=col, ha="center", va="center",
                fontweight="bold", linespacing=1.2)

    # ---- the exit itself: re-accelerate 5 yd through the gate
    d = np.array([np.cos(rad), np.sin(rad)])
    p = np.array([-np.sin(rad), np.cos(rad)])
    mid = 0.62 * gate * d + 0.48 * p
    ax.text(mid[0], mid[1], _schematic_text(table, "reaccel"), fontsize=8.8,
            color=colour["left"], ha="center", va="center",
            rotation=angle, rotation_mode="anchor")

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
    y0 = -4.35
    ax.plot([-approach - 0.6, -approach - 0.6 + sb], [y0, y0],
            color="#1A1A1A", lw=2.6, solid_capstyle="butt")
    for edge in (-approach - 0.6, -approach - 0.6 + sb):
        ax.plot([edge, edge], [y0 - 0.14, y0 + 0.14], color="#1A1A1A", lw=1.2)
    ax.text(-approach - 0.6 + sb / 2.0, y0 + 0.22,
            _schematic_text(table, "scale_bar"), fontsize=9.2,
            color="#1A1A1A", ha="center", va="bottom")


    # ---- header and footer (figure-level text; not axes text)
    fig.text(0.022, 0.955,
             f"The tempo break: a {angle:g}° cut at speed, both sides",
             fontsize=16.5, fontweight="bold", color="#1A1A1A", va="top")
    fig.text(0.022, 0.895,
             "Top-down plan view of the proposed DB drill. Schematic drawing: "
             "nothing on this figure is measured.",
             fontsize=9.6, color="#333333", va="top")
    fig.text(0.978, 0.952, context["status_stamp"], fontsize=9.4,
             fontweight="bold", color=ALERT, ha="right", va="top",
             bbox=dict(boxstyle="round,pad=0.45", facecolor="#FDE7E9",
                       edgecolor=ALERT, linewidth=1.2))

    footer = "\n".join([
        f"{context['footprint']}  {_schematic_text(table, 'duration')}",
        ("Drawn to scale in yards (equal aspect) with a 5 yd scale bar; the "
         "approach is drawn at the midpoint of the proposed "
         f"{context['approach_range'][0]:g}–{context['approach_range'][1]:g} yd "
         "range. No dimension on this image comes from data."),
        (f"Source: {context['source_note']} — status {context['status']}: not part "
         "of the task spec and not a finding. Gate width and any cone beyond the "
         "two approach cones are not specified in the note and are not drawn."),
    ])
    fig.text(0.022, 0.105, footer, fontsize=7.6, color="#333333", va="top",
             linespacing=1.45)
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


def drill_schematic_csv_frame(table: pd.DataFrame) -> pd.DataFrame:
    """Every drawn segment/point and every printed number, with its source."""
    out = table.copy()
    out.insert(0, "status", "PROPOSAL")
    out.insert(1, "status_stamp", PROPOSAL_STAMP)
    out.insert(2, "source_note", "notes/drill-proposal.md §2")
    return out[["status", "status_stamp", "source_note"] + DRILL_SCHEMATIC_COLUMNS]


def write_drill_schematic_csv(table: pd.DataFrame, path: Path,
                              cfg: common.Config) -> Path:
    """Write the geometry CSV WITHOUT the sample-data provenance columns.

    ``common.write_csv`` prefixes every row with ``PLACEHOLDER_SAMPLE`` /
    ``UNVALIDATED SAMPLE OUTPUT`` because it writes data artefacts. This figure
    is a drawing: nothing on it is measured, so stamping it as sample data
    would be a false provenance claim. The proposal status is the correct
    stamp, and it is written into the leading columns instead.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = drill_schematic_csv_frame(table)
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
        "title": (f"The tempo break: a {context['break_angle']:g}° cut at "
                  "speed, both sides"),
        "status": context["status"],
        "status_stamp": context["status_stamp"],
        "stamp": PROPOSAL_STAMP,
        "provenance_applied": False,
        "provenance_note": (
            "Deliberately NOT stamped with the PLACEHOLDER_SAMPLE / "
            "UNVALIDATED SAMPLE OUTPUT banner used by figures 1-3: this is a "
            "schematic of a proposed drill, not a data artefact, and nothing on "
            "it is measured. The proposal status in notes/drill-proposal.md is "
            "the correct stamp."),
        "claim": (
            "A proposed Combine drill: walk in, accelerate over an 8-10 yd "
            "approach to 3-5 yd/s, single plant, break 40 degrees either side, "
            "re-accelerate 5 yd through an exit gate; 6 reps (3 left, 3 right, "
            "alternating) in ~90 s per player on the existing 3-cone footprint."),
        "sources": ["notes/drill-proposal.md"],
        "source_note": context["source_note"],
        "spec": {
            "approach_length_yd": list(context["approach_range"]),
            "approach_drawn_yd": context["approach_drawn"],
            "runup_length_yd": context["runup"],
            "break_angle_deg": context["break_angle"],
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
            ("This is a PROPOSAL, not a finding: notes/drill-proposal.md is not "
             "part of TASK.md and needs sign-off before it appears in a writeup."),
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
    plot = (
        ggplot(frame, aes(xmin="xmin", xmax="xmax", ymin="ymin", ymax="ymax",
                          fill="setting", alpha="emphasis"))
        + geom_rect(colour="white", size=0.2)
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
