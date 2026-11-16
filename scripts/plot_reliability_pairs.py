"""Reliability pair figures from the uploaded full-run turn table.

Two figures, both FULL-only and DB-only, on one slice of
``data/interim/full_run_20261111/bdb27_full_db_turns.parquet``
(``setting == 'game'``, ``position_group == 'DB'``,
``30 <= abs_turn_angle < 60``, ``valid == True``):

* ``--figure odd``    — one dot per player at (mean odd-turn retention, mean
  even-turn retention); ``fig_odd_vs_even``.
* ``--figure season`` — 2024 player mean against 2025 player mean in two
  panels (>= 1 turn in both seasons / >= 10 turns in each);
  ``fig_season_vs_season``.

Plus one descriptive artefact, not a figure:

* ``--write-descriptive-share`` — the raw descriptive share of observed
  variation that sits between players (eta^2 = SSB / SST), with a player-level
  cluster-bootstrap interval; ``outputs/reliability_figures/``.

Every run prints the slice counts and refuses when they differ from the
uploaded full run: 7,777 turns, 113 players, 7,198 plays, 809 games, 2,197
turns at exactly 1.000. That is data integrity, not a threshold. ``--mode``
accepts only ``full``; ``sample`` refuses with the reason.

Outputs (per figure): ``outputs/figures/<key>.png``, ``.csv``,
``.provenance.json``, ``.drawn_text.json``, and exactly one entry in
``outputs/figures/figures_manifest.json``.

Usage:
    .venv/bin/python scripts/plot_reliability_pairs.py --figure odd --mode full
    .venv/bin/python scripts/plot_reliability_pairs.py --figure season --mode full
    .venv/bin/python scripts/plot_reliability_pairs.py --write-descriptive-share
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import common, reliability_pairs as rp  # noqa: E402

STAGE = rp.STAGE


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--figure", choices=["odd", "season"], default=None,
                   help="which reliability figure to draw")
    p.add_argument("--write-descriptive-share", action="store_true",
                   help="write the descriptive between-player share artefact")
    p.add_argument("--mode", choices=["full", "sample"], default="full")
    p.add_argument("--config", default=None)
    args = p.parse_args(argv)
    if args.figure is None and not args.write_descriptive_share:
        p.error("choose --figure {odd,season} or --write-descriptive-share")
    if args.figure is not None and args.write_descriptive_share:
        p.error("--figure and --write-descriptive-share are separate runs")
    return args


def _paths(cfg: common.Config, key: str) -> dict[str, Path]:
    outdir = cfg.repo_path("figures")
    return {
        "png": outdir / f"{key}.png",
        "csv": outdir / f"{key}.csv",
        "prov": outdir / f"{key}.provenance.json",
        "text": outdir / f"{key}.drawn_text.json",
    }


def run_odd(cfg: common.Config) -> int:
    turns = rp.load_turns()
    slice_df = rp.select_slice(turns)
    rp.verify_slice(slice_df)

    stats = rp.split_half_points(slice_df)
    rp.reconcile_odd(stats)
    ci = rp.load_split_half_ci()

    paths = _paths(cfg, rp.FIGURE_ODD)
    visible, texts = rp.draw_odd(stats, ci, colour=str(
        cfg.get("figures.focus_band.colour")), out_png=paths["png"])

    table = rp.odd_csv_frame(stats["players"])
    common.write_csv(table, paths["csv"], "full", cfg, extra={
        "figure": rp.FIGURE_ODD,
        "stamp": rp.figures.stamp_text("full"),
        "n_players_total": stats["n_players_total"],
        "n_turns_slice": stats["n_turns"],
        "n_ceiling_at_1.000": stats["n_ceiling"],
        "n_dropped_one_sided": stats["n_dropped"],
        "r": round(float(stats["r"]), 4),
        "spearman_brown": round(float(stats["spearman_brown"]), 4),
        "ci_lo": round(ci["lo"], 4),
        "ci_hi": round(ci["hi"], 4),
    })

    entry = rp.manifest_entry_odd(stats, ci, cfg, paths["png"], paths["csv"])
    rp.write_figure_artefacts(
        figure=rp.FIGURE_ODD, entry=entry, visible=visible, texts=texts,
        csv_path=paths["csv"], prov_path=paths["prov"], text_path=paths["text"],
        cfg=cfg)

    print(f"{rp.FIGURE_ODD}: r = {stats['r']:.4f}  Spearman-Brown = "
          f"{stats['spearman_brown']:.4f}  n = {stats['n_players']}  "
          f"dropped one-sided = {stats['n_dropped']} of "
          f"{stats['n_players_total']}")
    print(f"{rp.FIGURE_ODD}: 95% bootstrap CI on r "
          f"[{ci['lo']:.4f}, {ci['hi']:.4f}]")
    print(f"{rp.FIGURE_ODD}: png={paths['png']}")
    return 0


def run_season(cfg: common.Config) -> int:
    turns = rp.load_turns()
    slice_df = rp.select_slice(turns)
    counts = rp.verify_slice(slice_df)

    panels = rp.season_panels(slice_df)
    rp.reconcile_season(panels)

    paths = _paths(cfg, rp.FIGURE_SEASON)
    visible, texts = rp.draw_season(
        panels, counts, panels["joined"],
        colour=str(cfg.get("figures.focus_band.colour")), out_png=paths["png"])

    table = rp.season_csv_frame(panels["players"])
    common.write_csv(table, paths["csv"], "full", cfg, extra={
        "figure": rp.FIGURE_SEASON,
        "stamp": rp.figures.stamp_text("full"),
        "n_panel_min1": panels["n1"],
        "r_panel_min1": round(float(panels["r1"]), 4),
        "n_panel_min10": panels["n10"],
        "r_panel_min10": round(float(panels["r10"]), 4),
        "n_ceiling_at_1.000": counts["ceiling_at_1.000"],
        "n_turns": counts["turns"],
    })

    entry = rp.manifest_entry_season(panels, counts, cfg, paths["png"],
                                     paths["csv"])
    rp.write_figure_artefacts(
        figure=rp.FIGURE_SEASON, entry=entry, visible=visible, texts=texts,
        csv_path=paths["csv"], prov_path=paths["prov"], text_path=paths["text"],
        cfg=cfg)

    print(f"{rp.FIGURE_SEASON}: >=1 turn n = {panels['n1']}  r = "
          f"{panels['r1']:.4f}   >=10 turns n = {panels['n10']}  r = "
          f"{panels['r10']:.4f}")
    print(f"{rp.FIGURE_SEASON}: hollow (min1 panel) = {panels['n_hollow']}  "
          f"pool = {panels['joined']['pool_2024']}/"
          f"{panels['joined']['pool_2025']}/"
          f"{panels['joined']['n_both']}  png={paths['png']}")
    return 0


def run_descriptive_share(cfg: common.Config) -> int:
    turns = rp.load_turns()
    slice_df = rp.select_slice(turns)
    counts = rp.verify_slice(slice_df)

    share = rp.descriptive_share(slice_df)
    icc = rp.load_icc_committed()
    point = share["point"]
    ci = share["ci"]
    ci_precision = share["ci_precision"]

    note = (
        "The planning figure quoted [1.31%, 2.30%]; our "
        f"{ci['reps']:,}-rep draw gives its own interval "
        f"[{100 * ci['lo']:.2f}%, {100 * ci['hi']:.2f}%] and the "
        f"{ci_precision['reps']:,}-rep run is "
        f"[{100 * ci_precision['lo']:.2f}%, "
        f"{100 * ci_precision['hi']:.2f}%] \u2014 the same estimator, "
        "differing in the second decimal only by Monte Carlo draw.")

    outdir = rp.repo_path(rp.SHARE_DIR)
    outdir.mkdir(parents=True, exist_ok=True)
    share_path = outdir / "descriptive_share.json"
    manifest_path = outdir / "manifest.json"

    document: dict = {
        "stage": "descriptive_between_player_share",
        "estimator": {
            "name": "eta squared (SSB / SST), raw descriptive between-player "
                    "share",
            "formula": ("SSB = sum_i n_i (mean_i - grand)^2; "
                        "SST = sum (y - grand)^2; eta2 = SSB / SST"),
            "units": ("share of observed variation in apex_retention that sits "
                      "between players"),
            "not_the_icc": (
                "This is not the ICC(1). The ICC(1) is the ratio of the "
                "between-player variance component to total variance under the "
                "one-way random-effects model; eta2 is the observed "
                "descriptive share and the two quantities are different."),
        },
        "slice": {
            "path": rp.TURNS_PARQUET.as_posix(),
            "sha256": rp.sha256(rp.TURNS_PARQUET),
            "filter": ("setting == 'game' & position_group == 'DB' & "
                       "30 <= abs_turn_angle < 60 & valid == True"),
            **counts,
        },
        "point": round(point["eta2"], 7),
        "point_full_precision": point["eta2"],
        "point_float64": share["point_float64"],
        "precision_note": (
            "The quoted point is computed in the parquet's stored precision "
            "(float32). A float64 recomputation gives "
            f"{share['point_float64']:.10f}; the two differ by "
            f"{abs(point['eta2'] - share['point_float64']):.1e}."),
        "ssb": point["ssb"],
        "sst": point["sst"],
        "grand_mean": point["grand_mean"],
        "ci": ci,
        "ci_20000": ci_precision,
        "note": note,
        "icc_committed": icc,
        "seed": rp.fv.SEED,
        "reps": int(ci["reps"]),
        "written_utc": common.utc_now(),
        "git_head": common.git_rev(),
    }
    share_path.write_text(json.dumps(document, indent=2, allow_nan=False),
                          encoding="utf-8")

    manifest = {
        "stage": "descriptive_between_player_share",
        "written_utc": common.utc_now(),
        "git_head": common.git_rev(),
        "seed": rp.fv.SEED,
        "reps": int(ci["reps"]),
        "inputs": [{
            "path": rp.TURNS_PARQUET.as_posix(),
            "sha256": rp.sha256(rp.TURNS_PARQUET),
        }, {
            "path": rp.BAND_RELIABILITY_JSON.as_posix(),
            "sha256": rp.sha256(rp.BAND_RELIABILITY_JSON),
        }],
        "outputs": [{
            "path": str(share_path.relative_to(rp.ROOT)),
            "sha256": rp.sha256(share_path),
        }],
        "numbers": {
            "point": round(point["eta2"], 7),
            "ci_lo": round(ci["lo"], 6),
            "ci_hi": round(ci["hi"], 6),
            "ci_20000_lo": round(ci_precision["lo"], 6),
            "ci_20000_hi": round(ci_precision["hi"], 6),
            "icc_committed": icc["rounded"],
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False),
                             encoding="utf-8")

    print(f"descriptive_share: eta2 = {point['eta2']:.7f} "
          f"({100 * point['eta2']:.2f}%)  n = {point['n_obs']:,} turns / "
          f"{point['n_players']} players")
    print(f"descriptive_share: {ci['reps']:,}-rep CI "
          f"[{ci['lo']:.6f}, {ci['hi']:.6f}] = "
          f"[{100 * ci['lo']:.2f}%, {100 * ci['hi']:.2f}%]")
    print(f"descriptive_share: {ci_precision['reps']:,}-rep CI "
          f"[{ci_precision['lo']:.6f}, {ci_precision['hi']:.6f}] = "
          f"[{100 * ci_precision['lo']:.2f}%, "
          f"{100 * ci_precision['hi']:.2f}%]")
    print(f"descriptive_share: icc_committed = {icc['value']:.6f} "
          f"(variance-component ratio, not a variance share)")
    print(f"descriptive_share: {share_path}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode != "full":
        raise common.StageRefused(
            f"{STAGE}: these reliability artefacts are FULL-only. The sample "
            "mode reads the placeholder sample turn table, which cannot carry "
            "these numbers; use --mode full.")
    cfg = common.load_config(args.config)
    if args.write_descriptive_share:
        return run_descriptive_share(cfg)
    if args.figure == "odd":
        return run_odd(cfg)
    return run_season(cfg)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
