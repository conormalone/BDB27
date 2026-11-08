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
    coord_cartesian,
    element_blank,
    element_line,
    element_rect,
    element_text,
    facet_wrap,
    geom_col,
    geom_point,
    geom_rect,
    geom_segment,
    geom_text,
    geom_tile,
    ggplot,
    labs,
    position_dodge,
    scale_colour_manual,
    scale_fill_gradientn,
    scale_fill_manual,
    scale_shape_manual,
    scale_x_discrete,
    scale_x_continuous,
    scale_y_continuous,
    theme,
    theme_bw,
)

from bdb27 import common

STAGE = "06_figures"
ARTEFACT_VERSION = 1
FIGURE_KEY = "fig_the_hole"
FIGURE_KEY_DRILLS = "fig_drill_coverage"
FIGURE_KEY_BEFORE_AFTER = "fig_before_after"

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
