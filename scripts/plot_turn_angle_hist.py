"""Turn-angle histogram: valid in-game DB turns, 30-90 degrees in 5-degree bins.

Conor, 2026-11-13 13:14: "Not where they happen, just the angle of the turn.",
with the 30-90 degree / five-degree bucketing from 13:08. This is an angle
distribution, not a location figure (the location version was withdrawn).

Provenance is FULL: the uploaded full-run turn table
(``data/interim/full_run_20261111/bdb27_full_db_turns.parquet``) carries
``abs_turn_angle`` for 58,665 rows, of which 58,664 are valid DB game turns —
the same count the uploaded band table carries. The sample turn table is not
used for the drawn series.

The Combine battery is NOT drawn: the only Combine turns on this machine are
PLACEHOLDER_SAMPLE, and mixing a sample series into a FULL-stamped figure would
make the stamp false.

Outputs:

  outputs/figures/fig_turn_angle_hist_30_90.png
  outputs/figures/fig_turn_angle_hist_30_90.csv
  outputs/figures/fig_turn_angle_hist_30_90.provenance.json
  outputs/figures/figures_manifest.json   (one merged entry)

Usage:
    .venv/bin/python scripts/plot_turn_angle_hist.py --mode full
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import common, figures  # noqa: E402

STAGE = "06_figures"
FIGURE_KEY = "fig_turn_angle_hist_30_90"
FULL_TURNS = ROOT / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet"

BIN_LO, BIN_HI, BIN_W = 30.0, 90.0, 5.0
GAME = "#D55E00"
PANEL = "#EBEBEB"
BORDER = "#B3B3B3"
ALERT = "#B00020"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["full", "sample"], default="full")
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


def load_turns() -> tuple[pd.DataFrame, dict]:
    """Valid DB game turns with their absolute turn angle (FULL table)."""
    common.require_inputs([FULL_TURNS], stage=STAGE)
    turns = pd.read_parquet(FULL_TURNS)
    need = {"setting", "position_group", "valid", "abs_turn_angle"}
    missing = need - set(turns.columns)
    if missing:
        raise common.StageRefused(
            f"{STAGE}: {FULL_TURNS.name} lacks {sorted(missing)}")

    game = turns[(turns["setting"] == "game")
                 & (turns["position_group"] == "DB")]
    valid = game[game["valid"]].copy()
    if valid.empty:
        raise common.StageRefused(f"{STAGE}: no valid DB game turns in {FULL_TURNS}")

    ctx = {
        "n": int(len(valid)),
        "rows": int(len(turns)),
        "games": int(valid["game_id"].nunique()),
        "seasons": sorted(int(s) for s in valid["season"].unique())
        if "season" in valid.columns else [],
    }
    return valid, ctx


def bin_counts(valid: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    edges = np.arange(BIN_LO, BIN_HI + BIN_W, BIN_W)
    counts, _ = np.histogram(valid["abs_turn_angle"], bins=edges)
    table = pd.DataFrame({
        "bin_index": np.arange(1, len(edges)),
        "bin_low_deg": edges[:-1],
        "bin_high_deg": edges[1:],
        "bin": [f"{int(a)}–{int(b)}" for a, b in zip(edges[:-1], edges[1:])],
        "count": counts.astype(int),
    })
    total = int(len(valid))
    in_window = int(table["count"].sum())
    summary = {
        "n": total,
        "n_in_window": in_window,
        "n_below_30": int((valid["abs_turn_angle"] < BIN_LO).sum()),
        "n_above_90": int((valid["abs_turn_angle"] > BIN_HI).sum()),
        "edges": [float(e) for e in edges],
    }
    table["share_of_window"] = table["count"] / in_window if in_window else np.nan
    table["share_of_all"] = table["count"] / total if total else np.nan
    return table, summary


def draw(table: pd.DataFrame, summary: dict, context: dict, out_png: Path) -> None:
    fig, ax = plt.subplots(figsize=(12.0, 6.2), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor(PANEL)

    x = np.arange(len(table))
    ax.bar(x, table["count"], width=0.74, color=GAME, edgecolor="white",
           linewidth=0.7, zorder=2)
    top = int(table["count"].max())
    for xi, count in zip(x, table["count"]):
        ax.text(xi, count + top * 0.015, f"{int(count):,}", ha="center",
                va="bottom", fontsize=9, color="#333333")

    ax.set_xticks(x)
    ax.set_xticklabels(table["bin"], fontsize=9)
    ax.set_ylim(0, top * 1.20)
    ax.yaxis.grid(True, color="white", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_xlabel("absolute turn angle (degrees)", fontsize=10)
    ax.set_ylabel("DB game turns (count)", fontsize=10)
    ax.tick_params(labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(BORDER)

    ax.set_title("In-game DB turns by absolute turn angle, 30–90° in 5° bins",
                 fontsize=14, fontweight="bold", loc="left")
    ax.set_yticks(list(range(0, 2501, 500)))
    ax.text(0.012, 0.96,
            f"FULL — {summary['n']:,} valid DB game turns (2023–2025) · "
            f"{summary['n_in_window']:,} fall in 30–90° · "
            f"{summary['n_above_90']:,} are >90° and not drawn",
            transform=ax.transAxes, ha="left", va="top", fontsize=9,
            color="#444444")

    fig.text(0.012, 0.02,
             f"FULL DATA OUTPUT — {summary['n']:,} valid DB game turns, "
             f"{context['games']:,} games, seasons "
             f"{', '.join(str(s) for s in context['seasons'])}; bins are "
             "left-closed (30–35 contains exactly 30.0°)",
             fontsize=8, color=ALERT, fontweight="bold", ha="left")
    fig.subplots_adjust(left=0.075, right=0.985, top=0.855, bottom=0.135)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)


def band_crosscheck(table: pd.DataFrame) -> dict | None:
    """Reproduce the uploaded 30-60 and 60-90 band counts from the 5-degree bins.

    The FULL band table is the published count for those windows; if the bins
    do not add up to it, the figure is wrong and this returns a mismatch flag.
    """
    bands_csv = ROOT / "data/interim/full_run_20261111/bdb27_full_game_bands.csv"
    if not bands_csv.exists():
        return None
    bands = pd.read_csv(bands_csv)
    rows = bands[(bands["setting"] == "game")
                 & (bands["position_group"] == "DB")]
    lookup = dict(zip(rows["band"], rows["count"].astype(int)))
    below45 = int(table.loc[table["bin_high_deg"] <= 45, "count"].sum())
    to60 = int(table.loc[table["bin_high_deg"] <= 60, "count"].sum())
    to90 = int(table.loc[table["bin_high_deg"] <= 90, "count"].sum())
    out = {
        "source": str(bands_csv.relative_to(ROOT)),
        "30-60": {"bins_sum": to60, "band_table": lookup.get("30–60°")},
        "60-90": {"bins_sum": to90 - to60, "band_table": lookup.get("60–90°")},
        "30-45": {"bins_sum": below45},
    }
    out["matches"] = (out["30-60"]["band_table"] == to60
                      and out["60-90"]["band_table"] == to90 - to60)
    return out


def manifest_entry(table: pd.DataFrame, summary: dict, context: dict,
                   cfg: common.Config, png: Path, csv: Path) -> dict:
    return {
        "stage": STAGE,
        "artefact_version": figures.ARTEFACT_VERSION,
        "figure": FIGURE_KEY,
        "exploratory": True,
        "in_report": False,
        "title": "In-game DB turns by absolute turn angle, 30–90° in 5° bins (FULL)",
        "claim": ("Distribution of the absolute turn angle of valid DB game "
                  "turns in the 30–90° window, five-degree bins, FULL run."),
        "png": str(png.relative_to(ROOT)),
        "caveats": [
            ("Only the 30–90° window is drawn: 37,810 of the 58,664 valid DB "
             "game turns are >90° and are not shown (none are <30°)."),
            ("No Combine series is drawn: the Combine turns on this machine are "
             "PLACEHOLDER_SAMPLE, and a sample series inside a FULL-stamped "
             "figure would make the stamp false."),
            ("Exploratory: not one of the four report figures (D189); the "
             "writeup does not cite it."),
        ],
        "provenance": common.PROVENANCE_FULL,
        "stamp": figures.stamp_text("full"),
        "filter": ("bdb27_full_db_turns.parquet: setting == 'game' & "
                   "position_group == 'DB' & valid == True"),
        "n": summary["n"],
        "n_in_window": summary["n_in_window"],
        "n_below_30": summary["n_below_30"],
        "n_above_90": summary["n_above_90"],
        "games": context["games"],
        "seasons": context["seasons"],
        "bin_width_deg": BIN_W,
        "bins_deg": {row.bin: int(row["count"]) for _, row in table.iterrows()},
        "band_crosscheck": band_crosscheck(table),
        "sources": [str(FULL_TURNS.relative_to(ROOT))],
        "source_artefacts": [{
            "path": str(FULL_TURNS.relative_to(ROOT)),
            "sha256": sha256(FULL_TURNS),
            "provenance": common.PROVENANCE_FULL,
            "role": "uploaded full-run turn table (2023–2025), DB only",
        }],
        "outputs": {
            "png": str(png.relative_to(ROOT)),
            "png_sha256": sha256(png),
            "csv": str(csv.relative_to(ROOT)),
            "csv_sha256": sha256(csv),
        },
        "git_rev": git_rev(),
        "config_sha": common.config_fingerprint(cfg),
    }


def merge_manifest(entry: dict, path: Path, cfg: common.Config) -> None:
    existing: dict = {}
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
    existing.pop("generated_utc", None)
    common.write_json(existing, path, "full", cfg)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode != "full":
        raise common.StageRefused(
            f"{STAGE}: this figure is FULL-only — the corrected request is the "
            "full-run angle distribution, not the sample. Use --mode full.")
    cfg = common.load_config(args.config)
    outdir = cfg.repo_path("figures")
    png = outdir / f"{FIGURE_KEY}.png"
    csv = outdir / f"{FIGURE_KEY}.csv"
    prov = outdir / f"{FIGURE_KEY}.provenance.json"

    valid, context = load_turns()
    table, summary = bin_counts(valid)

    common.write_csv(table, csv, "full", cfg, extra={
        "figure": FIGURE_KEY,
        "stamp": figures.stamp_text("full"),
        "n_db_game_turns": summary["n"],
        "n_in_window": summary["n_in_window"],
        "n_below_30": summary["n_below_30"],
        "n_above_90": summary["n_above_90"],
    })

    draw(table, summary, context, png)

    entry = manifest_entry(table, summary, context, cfg, png, csv)
    common.write_json(entry, prov, "full", cfg)
    merge_manifest(entry, outdir / "figures_manifest.json", cfg)

    print(f"{FIGURE_KEY}: n={summary['n']} in-window={summary['n_in_window']} "
          f"above90={summary['n_above_90']} png={png}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
