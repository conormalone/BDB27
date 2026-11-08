"""Stage 06 — figures 1-9.

  1. "The Hole"         — DB supply vs demand by turn-angle band.
  2. "Drill coverage"   — what each DB drill asks for (angle x entry speed).
  3. "Before and After" — measured supply vs supply + the proposed drill, on
                          one angle x entry-speed grid.
  4. "Tempo break"      — the proposed drill drawn to scale (a diagram).
  5. "Same hole"        — figure 1's paired share panel repeated for DB, WR,
                          TE, OL and DL on one identical axis.
  6. "Measurement limits" — why per-player ranking is not supported: variance
                          components at the boundary, and the reversal zone
                          that is not physical capacity.
  7. "How big is the gap, really?" — the 4-vs-97 count answered three ways:
                          raw counts, each setting's own share, and turns per
                          player-appearance, with the stricter stage-04
                          population as a labelled sensitivity note.
  8. "What a turn costs" — apex retention (apex ÷ entry) by reporting band
                          (median and interquartile spread, both settings on
                          one axis), with each setting's own turn mix directly
                          beneath so the substitution is visible. Exit ÷ entry
                          retention is a footnote only: it is above 1 by
                          construction.
  9. "Same stopwatch, different turn" — the published three-cone time is one
                          scalar; every turn the detector found inside each
                          matched player's three-cone is drawn against that
                          time, with those turns' entry speeds beside it.
                          Descriptive only: no association statistic.

Stage 06 grows one committed figure at a time; each figure writes a PNG, a
CSV beside it, and one entry in outputs/figures/figures_manifest.json.

Usage:
    python src/06_figures.py --mode sample
    python src/06_figures.py --mode full     # refuses on sample thresholds
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

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
    variance_path = cfg.repo_path("envelope") / "variance_components.csv"
    common.require_inputs([turns_path, variance_path], stage=STAGE)

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

        # ---- figure 3: before and after (measured supply vs supply + drill)
        ba_block = "figures.before_after"
        ba_angle = [float(x) for x in cfg.get(f"{ba_block}.angle_edges_deg")]
        ba_speed = [float(x) for x in cfg.get(f"{ba_block}.speed_edges_yd_s")]
        ba_overflow = str(cfg.get(f"{ba_block}.overflow_label"))
        ba_position = str(cfg.get(f"{ba_block}.position_group"))

        supply_turns = figures.load_supply_turns(turns_path)
        ba_table, ba_context = figures.supply_grid(
            supply_turns, ba_angle, ba_overflow, ba_speed, ba_position)
        ba_proj = figures.projected_block(ba_table, ba_context, cfg)
        ba_csv = figures.before_after_csv_frame(ba_table, ba_proj)
        ba_png = outdir / f"{figures.FIGURE_KEY_BEFORE_AFTER}.png"
        ba_csv_path = outdir / f"{figures.FIGURE_KEY_BEFORE_AFTER}.csv"
        common.write_csv(ba_csv, ba_csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY_BEFORE_AFTER})
        ba_plot = figures.plot_before_after(ba_table, ba_context, ba_proj, cfg)
        figures.render(ba_plot, cfg, ba_png, block="before_after")
        ba_payload = figures.before_after_manifest_payload(
            ba_table, ba_context, ba_proj)
        ba_payload["stamp"] = figures.STAMP
        _write_manifest(ba_payload, manifest_path, args.mode, cfg)

        # ---- figure 4: the tempo-break drill, drawn (a diagram, no data)
        ds_table, ds_context = figures.drill_schematic_geometry(cfg)
        ds_png = outdir / f"{figures.FIGURE_KEY_DRILL_SCHEMATIC}.png"
        ds_csv_path = outdir / f"{figures.FIGURE_KEY_DRILL_SCHEMATIC}.csv"
        figures.write_drill_schematic_csv(ds_table, ds_csv_path, cfg)
        ds_fig = figures.plot_drill_schematic(ds_table, ds_context, cfg)
        figures.render_schematic(ds_fig, cfg, ds_png)
        ds_payload = figures.drill_schematic_manifest_payload(ds_table, ds_context)
        _write_manifest(ds_payload, manifest_path, args.mode, cfg)

        # ---- figure 5: the same hole across positions (five panels, one axis)
        ap_block = "figures.all_positions"
        ap_positions = [str(p) for p in cfg.get(f"{ap_block}.positions")]
        ap_table, ap_context = figures.positions_table(
            turns, edges, overflow, ap_positions)
        ap_csv = figures.all_positions_csv_frame(ap_table, ap_context, cfg)
        ap_png = outdir / f"{figures.FIGURE_KEY_ALL_POSITIONS}.png"
        ap_csv_path = outdir / f"{figures.FIGURE_KEY_ALL_POSITIONS}.csv"
        common.write_csv(ap_csv, ap_csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY_ALL_POSITIONS})
        ap_plot = figures.plot_all_positions(ap_table, ap_context, cfg,
                                             turns=turns)
        figures.render(ap_plot, cfg, ap_png, block="all_positions")
        ap_payload = figures.all_positions_manifest_payload(
            ap_table, ap_context, cfg)
        ap_payload["stamp"] = figures.STAMP
        _write_manifest(ap_payload, manifest_path, args.mode, cfg)

        # ---- figure 6: the measurement limits (slot e)
        ml_components = figures.load_variance_components(
            cfg.repo_path("envelope") / "variance_components.csv")
        ml_left, ml_lctx = figures.variance_table(ml_components)
        ml_right, ml_rctx = figures.lateral_table(turns, cfg)
        ml_table = figures.measurement_limits_csv_frame(
            ml_left, ml_right, ml_lctx, ml_rctx)
        ml_png = outdir / f"{figures.FIGURE_KEY_MEASUREMENT_LIMITS}.png"
        ml_csv_path = outdir / f"{figures.FIGURE_KEY_MEASUREMENT_LIMITS}.csv"
        common.write_csv(ml_table, ml_csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY_MEASUREMENT_LIMITS})
        ml_fig = figures.build_measurement_limits(
            ml_left, ml_right, {"left": ml_lctx, "right": ml_rctx}, cfg)
        figures.render_measurement_limits(ml_fig, cfg, ml_png)
        ml_payload = figures.measurement_limits_manifest_payload(
            ml_left, ml_right, ml_lctx, ml_rctx, cfg)
        ml_payload["stamp"] = figures.STAMP
        _write_manifest(ml_payload, manifest_path, args.mode, cfg)

        # ---- figure 7: how big is the gap, really? (the exposure attack)
        gh_table, gh_context = figures.gap_universe_table(turns, cfg)
        gh_csv = figures.gap_honest_csv_frame(gh_table)
        gh_png = outdir / f"{figures.FIGURE_KEY_GAP_HONEST}.png"
        gh_csv_path = outdir / f"{figures.FIGURE_KEY_GAP_HONEST}.csv"
        common.write_csv(gh_csv, gh_csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY_GAP_HONEST})
        gh_plot = figures.plot_gap_honest(gh_table, gh_context, cfg)
        figures.render(gh_plot, cfg, gh_png, block="gap_honest")
        gh_payload = figures.gap_honest_manifest_payload(
            gh_table, gh_context, cfg)
        gh_payload["stamp"] = figures.STAMP
        _write_manifest(gh_payload, manifest_path, args.mode, cfg)

        # ---- figure 8: what a turn costs, and who pays for it
        tc_table, tc_context = figures.turn_cost_table(turns, cfg)
        tc_csv = figures.turn_cost_csv_frame(tc_table)
        tc_png = outdir / f"{figures.FIGURE_KEY_TURN_COST}.png"
        tc_csv_path = outdir / f"{figures.FIGURE_KEY_TURN_COST}.csv"
        common.write_csv(tc_csv, tc_csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY_TURN_COST})
        tc_fig = figures.plot_turn_cost(tc_table, tc_context, cfg)
        figures.render_turn_cost(tc_fig, cfg, tc_png)
        tc_payload = figures.turn_cost_manifest_payload(
            tc_table, tc_context, cfg)
        tc_payload["stamp"] = figures.STAMP
        _write_manifest(tc_payload, manifest_path, args.mode, cfg)

        # ---- figure 9: same stopwatch, different turn
        combine_results = pd.read_csv(cfg.raw_file(args.mode, "combine_results"))
        sw_table, sw_context = figures.stopwatch_table(
            drill_turns, combine_results, cfg)
        sw_csv = figures.stopwatch_csv_frame(sw_table, sw_context)
        sw_png = outdir / f"{figures.FIGURE_KEY_STOPWATCH}.png"
        sw_csv_path = outdir / f"{figures.FIGURE_KEY_STOPWATCH}.csv"
        common.write_csv(sw_csv, sw_csv_path, args.mode, cfg,
                         extra={"stamp": figures.STAMP,
                                "figure": figures.FIGURE_KEY_STOPWATCH})
        sw_fig = figures.plot_stopwatch(sw_table, sw_context, cfg)
        figures.render_stopwatch(sw_fig, cfg, sw_png)
        sw_payload = figures.stopwatch_manifest_payload(
            sw_table, sw_context, cfg)
        sw_payload["stamp"] = figures.STAMP
        _write_manifest(sw_payload, manifest_path, args.mode, cfg)

        timer.rows = context["n_valid"]
        timer.detail = (f"{figures.FIGURE_KEY}: bands={len(context['bands'])} "
                        f"valid_{position}={context['n_valid']}; "
                        f"{figures.FIGURE_KEY_DRILLS}: "
                        f"drills={drill_context['n_drills']} "
                        f"turns={drill_context['n_turns']}; "
                        f"{figures.FIGURE_KEY_BEFORE_AFTER}: "
                        f"grid={ba_context['grid_shape'][0]}x"
                        f"{ba_context['grid_shape'][1]} "
                        f"turns={ba_context['n_turns']} "
                        f"projected={ba_proj['total']}; "
                        f"{figures.FIGURE_KEY_DRILL_SCHEMATIC}: "
                        f"status={ds_context['status']} "
                        f"elements={ds_context['n_elements']} "
                        f"annotations={ds_context['n_annotations']}; "
                        f"{figures.FIGURE_KEY_ALL_POSITIONS}: "
                        f"positions={len(ap_context['positions'])} "
                        f"turns={ap_context['n_turns']}; "
                        f"{figures.FIGURE_KEY_MEASUREMENT_LIMITS}: "
                        f"components={ml_lctx['n_components']} "
                        f"at_boundary={ml_lctx['n_at_boundary']} "
                        f"turns={ml_rctx['n_turns']}; "
                        f"{figures.FIGURE_KEY_GAP_HONEST}: "
                        f"headline={gh_context['headline']} "
                        f"ratios="
                        f"{gh_context['universes'][gh_context['headline']]['ratios']} "
                        f"{figures.FIGURE_KEY_TURN_COST}: "
                        f"drawn={tc_context['n_drawn']} "
                        f"insufficient={len(tc_context['insufficient'])} "
                        f"floor_removed={tc_context['n_below_speed_floor']}; "
                        f"{figures.FIGURE_KEY_STOPWATCH}: "
                        f"players={sw_context['n_players']} "
                        f"turns={sw_context['n_turns']} "
                        f"gate={sw_context['min_players']} ")
    print(f"{STAGE}: wrote {png_path.name}, {csv_path.name}, "
          f"{drill_png.name}, {drill_csv_path.name}, {ba_png.name}, "
          f"{ba_csv_path.name}, {ds_png.name}, {ds_csv_path.name}, "
          f"{ap_png.name}, {ap_csv_path.name}, "
          f"{ml_png.name}, {ml_csv_path.name}, "
          f"{gh_png.name}, {gh_csv_path.name}, "
          f"{tc_png.name}, {tc_csv_path.name}, "
          f"{sw_png.name}, {sw_csv_path.name}, "
          f"{manifest_path.name} "
          f"({context['n_valid']} valid {position} turns; "
          f"{ba_context['n_turns']} valid {ba_position} Combine turns on a "
          f"{ba_context['grid_shape'][0]}x{ba_context['grid_shape'][1]} grid; "
          f"drill schematic status {ds_context['status']}; "
          f"{ap_context['n_turns']} valid turns across "
          f"{len(ap_context['positions'])} positions; "
          f"{ml_lctx['n_at_boundary']} of {ml_lctx['n_components']} variance "
          f"components at the boundary; figure 7 headline "
          f"{gh_context['headline']}; figure 8 drew {tc_context['n_drawn']} "
          f"retention values with {len(tc_context['insufficient'])} band(s) "
          f"marked insufficient; figure 9 matched "
          f"{sw_context['n_players']} of {sw_context['n_via_turns']} three-cone "
          f"players over {sw_context['n_turns']} turns)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(f"[{STAGE}] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
