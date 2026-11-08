"""Stage 06 — figure 1, "The Hole" (DB turn supply vs demand).

Renders one figure only. The other deliverables (figures 2-3) are deliberately
out of scope for this run; stage 06 grows here, one committed figure at a time.

Usage:
    python src/06_figures.py --mode sample
    python src/06_figures.py --mode full     # refuses on sample thresholds
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bdb27 import common, figures

STAGE = figures.STAGE


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true",
                   help="re-render even if artefacts already exist (always does)")
    p.add_argument("--config", default=None)
    return p.parse_args(argv)


def check_provenance(cfg: common.Config, mode: str) -> None:
    """Full mode only: name every missing precondition in one refusal."""
    if mode != "full":
        return
    problems = []
    tp = cfg.thresholds_path()
    if not tp.exists():
        problems.append(f"outputs/thresholds.json missing ({tp}) — run "
                        "00_thresholds --mode full first")
    else:
        prov = json.loads(tp.read_text()).get("provenance")
        if prov != common.PROVENANCE_FULL:
            problems.append(f"thresholds provenance is {prov!r}, expected FULL — "
                            "refusing to render figures from placeholder thresholds")
    prereg = cfg.repo_path("prereg")
    if not prereg.exists():
        problems.append(f"prereg missing ({prereg}) — TASK.md requires "
                        "notes/prereg.md before the first full-data run")
    if problems:
        raise common.StageRefused(f"{STAGE} --mode full: " + "; ".join(problems))


def _write_manifest(payload: dict, path: Path, mode: str,
                    cfg: common.Config) -> None:
    """Merge this figure into an existing manifest instead of clobbering it."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text()).get("figures", {})
        except Exception:  # corrupt manifest is rebuilt, not trusted
            existing = {}
    existing[payload["figure"]] = payload
    common.write_json(
        {
            "stage": STAGE,
            "artefact_version": figures.ARTEFACT_VERSION,
            "stamp": figures.STAMP,
            "figures": existing,
        },
        path, mode, cfg,
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    check_provenance(cfg, args.mode)

    turns_path = cfg.repo_path("turns") / "turns.parquet"
    common.require_inputs([turns_path], stage=STAGE)

    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    position = str(cfg.get("figures.hole.position_group"))

    outdir = cfg.repo_path("figures")
    png_path = outdir / f"{figures.FIGURE_KEY}.png"
    csv_path = outdir / f"{figures.FIGURE_KEY}.csv"
    manifest_path = outdir / "figures_manifest.json"

    with common.StageTimer(STAGE, args.mode, cfg) as timer:
        turns = figures.load_turns(turns_path)
        table, context = figures.band_table(turns, edges, overflow, position)
        table_csv = figures.csv_frame(table)
        common.write_csv(table_csv, csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY})
        plot = figures.build_plot(table, context, cfg)
        figures.render(plot, cfg, png_path)
        payload = figures.manifest_payload(table, context)
        payload["stamp"] = figures.STAMP
        _write_manifest(payload, manifest_path, args.mode, cfg)

        drill_turns = figures.load_drill_turns(turns_path)
        drill_table, drill_context = figures.drill_table(drill_turns, cfg)
        drill_csv = figures.drill_csv_frame(drill_table)
        drill_png = outdir / f"{figures.FIGURE_KEY_DRILLS}.png"
        drill_csv_path = outdir / f"{figures.FIGURE_KEY_DRILLS}.csv"
        common.write_csv(drill_csv, drill_csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY_DRILLS})
        drill_plot = figures.plot_drill_coverage(drill_table, drill_context, cfg)
        figures.render(drill_plot, cfg, drill_png, block="drill_coverage")
        drill_payload = figures.drill_manifest_payload(drill_table, drill_context)
        drill_payload["stamp"] = figures.STAMP
        _write_manifest(drill_payload, manifest_path, args.mode, cfg)

        timer.rows = context["n_valid"]
        timer.detail = (f"{figures.FIGURE_KEY}: bands={len(context['bands'])} "
                        f"valid_{position}={context['n_valid']}; "
                        f"{figures.FIGURE_KEY_DRILLS}: "
                        f"drills={drill_context['n_drills']} "
                        f"turns={drill_context['n_turns']}")

    print(f"{STAGE}: wrote {png_path.name}, {csv_path.name}, "
          f"{drill_png.name}, {drill_csv_path.name}, {manifest_path.name} "
          f"({context['n_valid']} valid {position} turns)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(f"[{STAGE}] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
