#!/usr/bin/env python3
"""02_combine_ceiling.py — Phase 2 Combine ceiling + reliability screen.

Runs after the audit and before thresholds in the task's run order.  It fits
the straight-line Combine speed curve per attempt, pools attempts per player
with empirical-Bayes shrinkage, reports test-retest ICC and VIF, and writes one
row per player-feature with a standard error.

Every artefact is written through ``lc_common.write_csv`` / ``write_json``, so
sample-mode output carries the sample stamp without the caller doing anything.
"""

from __future__ import annotations

import sys

import lc_common as C
import ceiling_core


def run(cfg: dict, mode: str, force: bool = False) -> dict:
    paths = C.resolve_paths(cfg, mode)
    block = C.lc(cfg)["ceiling"]
    stage = "02_combine_ceiling"
    if C.is_done(cfg, stage, mode, force=force):
        return {"skipped": True, "stage": stage}

    C.assert_disk_space(paths, cfg)
    out_dir = paths["outputs"] / block["out_subdir"]
    result = ceiling_core.compute_ceiling_features(cfg)
    summary = result["summary"]

    fields = list(block["output_schema"])
    C.write_csv(out_dir / "player_ceiling.csv", result["rows"], fields,
                mode=mode, cfg=cfg)
    C.write_json(out_dir / block["summary_filename"], {
        "stage": stage,
        "mode": mode,
        "n_rows": len(result["rows"]),
        "selected_features": result["selected"],
        "reliable": summary["reliable"],
        "icc": summary["icc"],
        "vif": summary["vif"],
        "fit": summary["fit"],
        "n_players_with_fits": summary["n_players_with_fits"],
        "n_attempts_used": summary["n_attempts_used"],
        "icc_min": summary["icc_min"],
        "vif_max": summary["vif_max"],
        "min_r2": summary["min_r2"],
        "note": ("Ceiling feature table. Sample-mode output is not a result; "
                 "it exists to prove the code path runs."),
    }, mode=mode, cfg=cfg)
    C.mark_done(cfg, stage, mode, extra={
        "n_rows": len(result["rows"]),
        "selected_features": result["selected"],
    })
    return {"skipped": False, "stage": stage, "result": result}


def main(argv=None) -> int:
    args = C.mode_from_argv(argv)
    cfg = C.load_config(args.config)
    C.seed_everything(int(C.lc(cfg)["seeds"]["base"]))
    try:
        with C.timed_stage(cfg, "02_combine_ceiling", args.mode) as st:
            outcome = run(cfg, args.mode, force=args.force)
            if outcome.get("skipped"):
                st.note("skipped: checkpoint current")
                print("02_combine_ceiling: up to date (use --force to rebuild)")
                return 0
            summary = outcome["result"]["summary"]
            st.note(f"players={summary['n_players_with_fits']} "
                    f"attempts={summary['n_attempts_used']} "
                    f"selected={','.join(summary['selected_features']) or 'NONE'}")
            print(f"02_combine_ceiling: {summary['n_players_with_fits']} players, "
                  f"{summary['n_attempts_used']} attempts")
            for feature, value in summary["icc"].items():
                print(f"  ICC {feature}: {value:.3f}")
            print(f"  selected: {summary['selected_features']}")
            if not summary["reliable"]:
                print("ERROR: no ceiling feature passed the ICC screen "
                      f"(icc_min={summary['icc_min']})", file=sys.stderr)
                return 3
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
