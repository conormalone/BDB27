"""Exploratory figure: WHERE on the field valid DB game turns happen.

Conor, 2026-11-13: "a plot of the actual locations of the turns, in game.
Maybe from 30 to 90 bucketed in fives."

This is NOT one of the report figures (D189 fixes the writeup to four figures).
It is rendered from the sample turn table only:

  * ``outputs/turns/turns.parquet`` is PLACEHOLDER_SAMPLE and carries
    ``x_centre`` / ``y_centre`` at the turn apex;
  * the uploaded FULL turn table
    (``data/interim/full_run_20261111/bdb27_full_db_turns.parquet``) has no
    location columns and the full game tracking CSVs are not on this machine,
    so a FULL-provenance version is not buildable here.

Coordinate frame. NFL tracking x is the raw 0-120 field coordinate; it is NOT
canonical per play direction (46% of matched sample plays have a negative
median vx). ``player_play.csv`` supplies ``play_direction`` per play, and every
turn in the sample joins to it. Left-direction plays are rotated 180 degrees
into the offence's frame:

    x' = x            , y' = y             when play_direction == "right"
    x' = 120 - x      , y' = 53.3 - y      when play_direction == "left"

so x' is always measured from the offence's own goal line. The output states
the frame in the axes, the CSV and the manifest.

Outputs (written beside the report figures, one provenance-stamped entry):

  outputs/figures/fig_turn_locations_sample.png
  outputs/figures/fig_turn_locations_sample.csv
  outputs/figures/fig_turn_locations_sample.provenance.json
  outputs/figures/figures_manifest.json   (one merged entry)

Usage:
    .venv/bin/python scripts/plot_turn_locations.py --mode sample
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import common, figures  # noqa: E402

MODE = "sample"
STAGE = "06_figures"
FIGURE_KEY = "fig_turn_locations_sample"

BIN_LO, BIN_HI, BIN_W = 30.0, 90.0, 5.0
FIELD_X, FIELD_Y = 120.0, 53.3
OWN, OPP = "#0072B2", "#D55E00"
PANEL = "#EBEBEB"
BORDER = "#B3B3B3"

X_FRAME = ("x = distance from the offence's own goal line (yd), "
           "direction-normalised")
Y_FRAME = "y = field width (yd), direction-normalised"
MIRROR_RULE = ("play_direction == 'left': x -> 120 - x, y -> 53.3 - y; "
               "play_direction == 'right': unchanged")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["sample", "full"], default="sample")
    p.add_argument("--config", default=None)
    return p.parse_args(argv)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_rev() -> str | None:
    try:
        import subprocess
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return None


def load_turn_locations(cfg: common.Config) -> tuple[pd.DataFrame, dict]:
    """DB, game, valid turns with a direction-normalised apex location."""
    turns_path = cfg.repo_path("turns") / "turns.parquet"
    player_play = ROOT / "data" / "raw" / "player_play.csv"
    games_csv = ROOT / "data" / "raw" / "games.csv"
    common.require_inputs([turns_path, player_play, games_csv], stage=STAGE)

    turns = pd.read_parquet(turns_path)
    need = {"setting", "position_group", "valid", "x_centre", "y_centre",
            "game_id", "play_id"}
    missing = need - set(turns.columns)
    if missing:
        raise common.StageRefused(
            f"{STAGE}: turns.parquet lacks {sorted(missing)}; cannot locate turns")

    filtered = turns[(turns["setting"] == "game")
                     & (turns["position_group"] == "DB")
                     & (turns["valid"])].copy()
    if filtered.empty:
        raise common.StageRefused(f"{STAGE}: no valid DB game turns in {turns_path}")

    directions = pd.read_csv(
        player_play, usecols=["game_id", "play_id", "play_direction"]
    ).drop_duplicates(["game_id", "play_id"])
    n_before = len(filtered)
    filtered = filtered.merge(directions, on=["game_id", "play_id"], how="left")
    if filtered["play_direction"].isna().any():
        n = int(filtered["play_direction"].isna().sum())
        raise common.StageRefused(
            f"{STAGE}: {n}/{n_before} DB game turns have no play_direction in "
            "player_play.csv; refusing to plot a field location in an unknown frame")
    if not filtered["play_direction"].isin(["left", "right"]).all():
        bad = sorted(filtered.loc[~filtered["play_direction"].isin(
            ["left", "right"]), "play_direction"].unique())
        raise common.StageRefused(
            f"{STAGE}: unexpected play_direction values {bad}; "
            "refusing to guess the frame")

    left = filtered["play_direction"] == "left"
    filtered["x_norm"] = np.where(left, FIELD_X - filtered["x_centre"],
                                  filtered["x_centre"])
    filtered["y_norm"] = np.where(left, FIELD_Y - filtered["y_centre"],
                                  filtered["y_centre"])
    context = {
        "n": int(len(filtered)),
        "games": int(filtered["game_id"].nunique()),
        "plays": int(filtered[["game_id", "play_id"]].drop_duplicates().shape[0]),
        "n_left": int(left.sum()),
        "n_right": int((~left).sum()),
        "turns_path": turns_path,
    }
    seasons = pd.read_csv(games_csv, usecols=["game_id", "season"])
    season = sorted(seasons.loc[seasons["game_id"].isin(filtered["game_id"]),
                                "season"].astype(int).unique())
    context["seasons"] = season
    return filtered, context


def bin_counts(located: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    edges = np.arange(BIN_LO, BIN_HI + BIN_W, BIN_W)
    counts, _ = np.histogram(located["x_norm"], bins=edges)
    table = pd.DataFrame({
        "bin_index": np.arange(1, len(edges)),
        "bin_low_yd": edges[:-1],
        "bin_high_yd": edges[1:],
        "bin": [f"{int(a)}–{int(b)}" for a, b in zip(edges[:-1], edges[1:])],
        "count": counts.astype(int),
    })
    total = int(len(located))
    in_window = int(table["count"].sum())
    summary = {
        "n": total,
        "n_in_window": in_window,
        "n_below_30": int((located["x_norm"] < BIN_LO).sum()),
        "n_above_90": int((located["x_norm"] > BIN_HI).sum()),
        "edges": [float(e) for e in edges],
    }
    table["share_of_window"] = table["count"] / in_window if in_window else np.nan
    table["share_of_all"] = table["count"] / total if total else np.nan
    return table, summary


def draw(frame: pd.DataFrame, table: pd.DataFrame, summary: dict,
         context: dict, out_png: Path) -> None:
    """Two stacked panels: 5-yd buckets on top (primary), field map beneath."""
    fig = plt.figure(figsize=(12.6, 10.3), dpi=150)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.55, 5.49], hspace=0.26,
                          left=0.075, right=0.985, top=0.905, bottom=0.145)
    ax_b = fig.add_subplot(gs[0])   # (a) 5-yd buckets - the primary panel
    ax_f = fig.add_subplot(gs[1])   # (b) field map - the companion
    fig.patch.set_facecolor("white")

    # ---- (a) 5-yd buckets, 30-90 yd -------------------------------------
    ax_b.set_facecolor(PANEL)
    x = np.arange(len(table))
    ax_b.bar(x, table["count"], width=0.72, color=OPP, edgecolor="white",
             linewidth=0.6, zorder=2)
    top = int(table["count"].max())
    for xi, count in zip(x, table["count"]):
        ax_b.text(xi, count + top * 0.015, f"{int(count)}", ha="center",
                  va="bottom", fontsize=8.0, color="#333333")
    ax_b.set_xticks(x)
    ax_b.set_xticklabels(table["bin"], fontsize=8.0)
    ax_b.set_yticks(list(range(0, 201, 50)))
    ax_b.set_ylim(0, top * 1.24)
    ax_b.yaxis.grid(True, color="white", linewidth=0.7, zorder=0)
    ax_b.set_axisbelow(True)
    ax_b.set_xlabel("Distance from own goal line, 5-yd bins (yd, "
                    "direction-normalised)", fontsize=9)
    ax_b.set_ylabel("DB game turns (count)", fontsize=9)
    ax_b.set_title("(a) The same turns in 5-yd buckets, 30–90 yd — the "
                   "requested view", fontsize=11, fontweight="bold", loc="left")
    ax_b.tick_params(labelsize=8)
    for spine in ax_b.spines.values():
        spine.set_color(BORDER)
    ax_b.text(0.985, 0.97,
              f"n = {summary['n']:,} valid DB game turns; "
              f"{summary['n_in_window']:,} fall in 30–90 yd.\n"
              f"{summary['n_below_30']:,} below 30 yd and "
              f"{summary['n_above_90']:,} above 90 yd are outside the window "
              "and not drawn.",
              transform=ax_b.transAxes, ha="right", va="top", fontsize=8.0,
              color="#444444")

    # ---- (b) field map ---------------------------------------------------
    ax_f.set_facecolor(PANEL)
    ax_f.add_patch(Rectangle((0, 0), FIELD_X, FIELD_Y, facecolor="#F7F7F7",
                             edgecolor=BORDER, linewidth=1.0, zorder=0))
    for yard in range(0, 121, 10):
        ax_f.plot([yard, yard], [0, FIELD_Y], color="white", linewidth=0.8,
                  zorder=1)
        if yard % 20 == 0:
            ax_f.text(yard, -2.6, str(yard), ha="center", va="top",
                      fontsize=8, color="#555555")
    ax_f.plot([60, 60], [0, FIELD_Y], color="#D0D0D0", linewidth=1.0, zorder=1)

    ax_f.axvspan(BIN_LO, BIN_HI, facecolor=OWN, alpha=0.06, zorder=1)
    for edge in (BIN_LO, BIN_HI):
        ax_f.axvline(edge, color=OWN, linestyle="--", linewidth=1.1, zorder=2)

    ax_f.scatter(frame["x_norm"], frame["y_norm"], s=7, c=OPP, alpha=0.30,
                 linewidths=0, zorder=3)
    ax_f.set_xlim(-3, FIELD_X + 3)
    ax_f.set_ylim(-4.5, FIELD_Y + 2.5)
    ax_f.set_xticks(list(range(0, 121, 20)))
    ax_f.set_yticks(list(range(0, 54, 10)))
    ax_f.set_aspect("equal", adjustable="box")
    ax_f.set_xlabel("Distance from own goal line (yd, direction-normalised); "
                    "dashed lines bound the 30–90 yd window", fontsize=9)
    ax_f.set_ylabel(Y_FRAME, fontsize=9)
    ax_f.set_title("(b) Where those turns are on the field — one dot per "
                   "valid DB game turn", fontsize=11, fontweight="bold",
                   loc="left")
    ax_f.tick_params(labelsize=8)
    for spine in ax_f.spines.values():
        spine.set_color(BORDER)
    ax_f.text(0.5, FIELD_Y + 1.2, "own goal line", ha="left", va="bottom",
              fontsize=7.5, color="#555555")
    ax_f.text(FIELD_X - 0.5, FIELD_Y + 1.2, "opponent goal line", ha="right",
              va="bottom", fontsize=7.5, color="#555555")

    seasons = "/".join(str(s) for s in context["seasons"])
    fig.suptitle(f"Where in-game DB turns happen — {summary['n']:,} valid sample turns",
                 fontsize=15, fontweight="bold", x=0.012, ha="left", y=0.982)
    fig.text(0.012, 0.055,
             f"PLACEHOLDER_SAMPLE — {context['label']}   |   "
             f"{summary['n']:,} turns · {context['plays']:,} plays · "
             f"{context['games']} games ({seasons} sample)",
             fontsize=7.4, color="#B00020", fontweight="bold", ha="left")
    fig.text(0.012, 0.018,
             f"Coordinate frame: {MIRROR_RULE}   |   "
             "not a report figure (D189 fixes the writeup to four figures)",
             fontsize=7.4, color="#444444", ha="left")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)


def manifest_entry(table: pd.DataFrame, summary: dict, context: dict,
                   cfg: common.Config, png: Path, csv: Path) -> dict:
    return {
        "stage": STAGE,
        "artefact_version": figures.ARTEFACT_VERSION,
        "figure": FIGURE_KEY,
        "exploratory": True,
        "in_report": False,
        "title": "Where in-game DB turns happen (30–90 yd in 5-yd buckets)",
        "claim": ("Descriptive locations of valid DB game turns in the "
                  "direction-normalised field frame. Not a report figure."),
        "png": str(png.relative_to(ROOT)),
        "caveats": [
            ("PLACEHOLDER_SAMPLE: a FULL version needs the full game tracking "
             "CSVs plus player_play.csv, which are not on this machine; the "
             "FULL turn table carries no x_centre/y_centre."),
            (f"Only the 30-90 yd window is bucketed: {summary['n_below_30']} "
             f"turns below 30 yd and {summary['n_above_90']} above 90 yd are "
             "counted in the manifest but not drawn."),
            ("Exploratory: not one of the four report figures (D189); the "
             "writeup does not cite it."),
        ],
        "provenance": common.PROVENANCE_SAMPLE,
        "stamp": figures.stamp_text(MODE),
        "filter": ("turns.parquet: setting == 'game' & position_group == 'DB' "
                   "& valid == True"),
        "n": summary["n"],
        "n_by_direction": {"left": context["n_left"], "right": context["n_right"]},
        "plays": context["plays"],
        "games": context["games"],
        "coordinate_frame": {
            "x": X_FRAME,
            "y": Y_FRAME,
            "mirror_rule": MIRROR_RULE,
            "field_length_yd": FIELD_X,
            "field_width_yd": FIELD_Y,
            "x_needed_normalising": True,
            "evidence": ("46% of matched sample plays have median vx < 0; "
                         "raw x is not canonical per play direction"),
        },
        "bins_yd": {
            "edges": summary["edges"],
            "window": [BIN_LO, BIN_HI],
            "counts": {row.bin: int(row["count"])
                       for _, row in table.iterrows()},
            "n_in_window": summary["n_in_window"],
            "n_below_30": summary["n_below_30"],
            "n_above_90": summary["n_above_90"],
        },
        "sources": [
            str(context["turns_path"].relative_to(ROOT)),
            "data/raw/player_play.csv",
            "data/raw/games.csv",
        ],
        "source_artefacts": [
            {"path": str(context["turns_path"].relative_to(ROOT)),
             "sha256": sha256(context["turns_path"]),
             "provenance": common.PROVENANCE_SAMPLE},
            {"path": "data/raw/player_play.csv",
             "sha256": sha256(ROOT / "data" / "raw" / "player_play.csv"),
             "role": "play_direction, one row per (game_id, play_id)"},
        ],
        "outputs": {
            "png": str(png.relative_to(ROOT)),
            "png_sha256": sha256(png),
            "csv": str(csv.relative_to(ROOT)),
            "csv_sha256": sha256(csv),
        },
        "git_rev": git_rev(),
        "config_sha": common.config_fingerprint(cfg),
        "full_data_caveat": (
            "PLACEHOLDER_SAMPLE. The FULL turn table has no x_centre/y_centre "
            "and the full game tracking CSVs are not on this machine. A FULL "
            "version needs those tracking files plus play_direction, and a "
            "re-render through this same script."),
    }


def merge_manifest(entry: dict, path: Path, cfg: common.Config) -> None:
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = {}
    figs = dict(existing.get("figures", {}))
    figs[FIGURE_KEY] = entry
    n_full = sum(1 for v in figs.values()
                 if str(v.get("stamp", "")).startswith("FULL"))
    n_schem = sum(1 for v in figs.values()
                  if str(v.get("stamp", "")).startswith("SCHEMATIC"))
    n_sample = len(figs) - n_full - n_schem
    label = f"MIXED PROVENANCE — {n_full} FULL, {n_sample} sample, {n_schem} schematic"
    existing["figures"] = figs
    existing["stage"] = STAGE
    existing["artefact_version"] = figures.ARTEFACT_VERSION
    existing["stamp"] = label
    existing["label"] = label
    existing["config_sha"] = common.config_fingerprint(cfg)
    # Let write_json stamp this run's time rather than preserving a stale one.
    existing.pop("generated_utc", None)
    common.write_json(existing, path, MODE, cfg)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode == "full":
        raise common.StageRefused(
            f"{STAGE}: FULL turn locations are not buildable here — the FULL "
            "turn table carries no x_centre/y_centre and the full game tracking "
            "CSVs are absent. Run this script on the machine that holds them.")
    cfg = common.load_config(args.config)
    outdir = cfg.repo_path("figures")
    png = outdir / f"{FIGURE_KEY}.png"
    csv = outdir / f"{FIGURE_KEY}.csv"
    prov = outdir / f"{FIGURE_KEY}.provenance.json"

    located, context = load_turn_locations(cfg)
    context["label"] = figures.stamp_text(MODE)
    table, summary = bin_counts(located)

    out = table.copy()
    common.write_csv(out, csv, MODE, cfg, extra={
        "figure": FIGURE_KEY,
        "stamp": figures.stamp_text(MODE),
        "n_db_game_turns": summary["n"],
        "n_in_window": summary["n_in_window"],
        "n_below_30": summary["n_below_30"],
        "n_above_90": summary["n_above_90"],
        "x_frame": X_FRAME,
    })

    draw(located, table, summary, context, png)

    entry = manifest_entry(table, summary, context, cfg, png, csv)
    common.write_json(entry, prov, MODE, cfg)
    merge_manifest(entry, outdir / "figures_manifest.json", cfg)

    print(f"{FIGURE_KEY}: n={summary['n']} in-window={summary['n_in_window']} "
          f"png={png} csv={csv}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
