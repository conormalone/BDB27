"""Stage 00 — thresholds contract.

Writes ``outputs/thresholds.json``: the smoothing choice, the detector floors,
the bin edges and the plant tolerance, each with a provenance stamp.

Provenance rules (TASK.md):
  * ``--mode sample``  -> ``PLACEHOLDER_SAMPLE``. Never set thresholds from data.
  * ``--mode full``    -> ``FULL``, and only after the full-data inputs exist.
Stages 02-06 must refuse to run in full mode unless provenance == FULL.
"""

from __future__ import annotations

import argparse
import sys

from bdb import common


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.add_argument("--config", default=None)
    return p.parse_args(argv)


def build(cfg: common.Config, mode: str) -> dict:
    smoother = cfg.get("smoothing.primary")
    cands = {c["name"]: c for c in cfg.get("smoothing.candidates")}
    if smoother not in cands:
        raise common.StageRefused(
            f"smoothing.primary={smoother!r} is not one of the configured candidates "
            f"{sorted(cands)}")
    return {
        "spec_commit": cfg.get("spec_commit"),
        "git_rev": common.git_rev(),
        "mode": mode,
        "provenance": common.provenance(mode),
        "frozen": False,  # becomes true only when notes/prereg.md exists and is cited
        "note": (
            "Thresholds are design choices from config.yaml, NOT estimates. "
            "PLACEHOLDER_SAMPLE until prereg fixes them; never tune on sample results."
        ),
        "smoothing": {
            "primary": smoother,
            "primary_params": cands[smoother],
            "alternatives": [c["name"] for n, c in cands.items() if n != smoother],
            "accel_cap_yd_s2": cfg.get("smoothing.accel_cap_yd_s2"),
        },
        "detector": {
            "window_s": cfg.get("detector.window_s"),
            "min_turn_angle_deg": cfg.get("detector.min_turn_angle_deg"),
            "speed_floor": cfg.get(
                "detector.speed_floor_combine" if mode == "sample" else "detector.speed_floor_game"),
            "speed_floor_combine": cfg.get("detector.speed_floor_combine"),
            "speed_floor_game": cfg.get("detector.speed_floor_game"),
            "nms_min_separation_s": cfg.get("detector.nms_min_separation_s"),
        },
        "plant": {"retention_delta": cfg.get("plant.retention_delta")},
        "bins": {
            "entry_speed": cfg.get("bins.entry_speed"),
            "turn_angle": cfg.get("bins.turn_angle"),
            "speed_band": cfg.get("bins.speed_band"),
        },
        "validation_thresholds": cfg.get("validation"),
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    out = cfg.thresholds_path()
    if out.exists() and not args.force:
        print(f"00_thresholds: {out} exists (use --force)", file=sys.stderr)
        return 0
    with common.StageTimer("00_thresholds", args.mode, cfg) as timer:
        if args.mode == "full":
            # Refuse honestly if the full inputs are absent: a FULL stamp may only
            # be written for a run whose inputs exist.
            common.require_inputs(
                [cfg.raw_file("full", k) for k in ("combine_tracking", "game_tracking")],
                stage="00_thresholds --mode full")
        payload = build(cfg, args.mode)
        common.write_json(payload, out, args.mode, cfg)
        timer.rows = 1
    print(f"00_thresholds: wrote {out} (provenance {common.provenance(args.mode)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
