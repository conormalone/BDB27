#!/usr/bin/env python3
"""00_thresholds.py — Phase 3a: route-effort cut-offs (TASK.md Phase 3).

Produces the route-effort cut-offs that `03_game_demand.py` applies when scoring "max-effort"
game observations. Per `TASK.md` / FROZEN `notes/prereg.md`:

* ``--mode sample`` writes **a-priori PLACEHOLDER** values (from ``game.sample_placeholders``),
  **never** derived from the sample; the file's provenance is ``PLACEHOLDER``.
* ``--mode full`` **derives** the cut-offs from the full game data (percentile method at the frozen
  ``game.thresholds.*_percentile`` values) with provenance ``FULL``.
* ``--synthetic`` exercises the identical derivation path on the committed synthetic fixture
  (planted ceiling; provenance ``SYNTHETIC FIXTURE (not a finding)`` — never presented as a result).

A full run is gated on the pre-registration freeze **and** the audit go/no-go (`src/gate.py`); a
full-run output whose provenance is not ``FULL`` is a blocker (`notes/blockers.md`).

Discipline (TASK.md): config-driven (no magic numbers); no full CSV into pandas (DuckDB streaming,
explicit columns, per-play aggregation); the percentile is accumulated in a fixed histogram;
wall time + peak RAM logged to ``outputs/run_log.csv``; checkpoint + skip finished work unless
``--force``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import game_features as gf
import gate

PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def thresholds_path(cfg: dict, synthetic: bool, regime: str) -> Path:
    """Output path: ``outputs/thresholds.json`` (real) or a per-regime synthetic path."""
    name = cfg["game"]["thresholds_file"]
    if synthetic:
        base = common.resolve(cfg, "outputs", cfg["game"]["out_subdir"])
        return base / "synthetic" / regime / name
    return common.resolve(cfg, "outputs", name)


def _parquet(con, cfg: dict, name: str, force: bool, log) -> Path:
    parq = common.resolve(cfg, "parquet")
    dst = parq / (Path(cfg["files"][name]).stem + ".parquet")
    return common.to_parquet(con, common.csv_path(cfg, name), dst, force=force, logger=log)


def _parquet_full(con, cfg: dict, name: str, force: bool, log) -> list[Path]:
    parq = common.resolve(cfg, "parquet")
    out = []
    for fn in cfg["files"][name]:
        src = common.resolve(cfg, "raw", fn)
        if not src.exists():
            raise FileNotFoundError(f"full mode requires {src} (absent on this Pi) -> notes/blockers.md")
        dst = parq / (Path(fn).stem + ".parquet")
        out.append(common.to_parquet(con, src, dst, force=force, logger=log))
    return out


# --------------------------------------------------------------------------- #
# payload builders
# --------------------------------------------------------------------------- #
def placeholder_payload(cfg: dict, mode: str) -> dict[str, Any]:
    """Sample mode: a-priori placeholders ONLY — never derived from the sample (prereg §8)."""
    g = cfg["game"]["thresholds"]
    ph = cfg["game"]["sample_placeholders"]
    return dict(
        provenance="PLACEHOLDER",
        label=cfg["mode"]["sample"]["provenance"],  # 'UNVALIDATED SAMPLE OUTPUT' discipline
        mode=mode,
        method="a_priori_placeholder",
        high_speed_percentile=g["high_speed_percentile"],
        accel_percentile=g["accel_percentile"],
        high_speed_threshold_yds=ph["high_speed_threshold_yds"],
        accel_threshold_yds2=ph["accel_threshold_yds2"],
        n_efforts=None,
        source="a-priori placeholder (NEVER derived from the sample; prereg sec8)",
        generated_by="00_thresholds.py",
    )


def derived_payload(cfg: dict, mode: str, prov: str, derived: dict[str, Any], source: str) -> dict[str, Any]:
    g = cfg["game"]["thresholds"]
    return dict(
        provenance=prov,
        mode=mode,
        method=g["method"],
        high_speed_percentile=g["high_speed_percentile"],
        accel_percentile=g["accel_percentile"],
        high_speed_threshold_yds=derived["high_speed_threshold_yds"],
        accel_threshold_yds2=derived["accel_threshold_yds2"],
        n_efforts=derived["n_efforts"],
        speed_values_total=derived["speed_values_total"],
        accel_values_total=derived["accel_values_total"],
        histogram=dict(g["histogram"]),
        source=source,
        generated_by="00_thresholds.py",
    )


def record_blocker(cfg: dict, msg: str, log=None) -> None:
    """Append a stage blocker to notes/blockers.md (preserving existing content)."""
    path = common.resolve(cfg, "notes") / "blockers.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- **BDB27 00_thresholds BLOCKER** ({stamp}): {msg}\n")
    if log:
        log.error("BLOCKER: %s", msg)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def derive_on_synthetic(con, cfg: dict, regime: str, force: bool, log) -> dict[str, Any]:
    fixdir = common.resolve(cfg, "synthetic") / regime
    out_base = common.resolve(cfg, "outputs", cfg["game"]["out_subdir"]) / "synthetic" / regime
    cache = out_base / "_cache"
    tabs = {}
    for name, fn in (("game_tracking", "game_tracking.csv"), ("player_play", "player_play.csv"),
                     ("combine_results", "combine_results.csv")):
        dst = cache / (name + ".parquet")
        common.to_parquet(con, fixdir / fn, dst, force=force, logger=None)
        tabs[name] = dst
    efforts = gf.extract_efforts(con, [tabs["game_tracking"]], tabs["player_play"],
                                 tabs["combine_results"], cfg)
    log.info("synthetic[%s]: %d route-snap efforts", regime, len(efforts))
    return gf.derive_thresholds(efforts, cfg)


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 Phase 3a — route-effort cut-offs")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore the checkpoint / rebuild Parquet")
    ap.add_argument("--synthetic", action="store_true",
                    help="derive on the committed synthetic fixture (planted ceiling; not a finding)")
    ap.add_argument("--synthetic-regime", choices=["binding", "null"], default="binding")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()

    gate.enforce(cfg, mode, "00_thresholds", requires_game_side=True, logger=log)

    out_path = thresholds_path(cfg, args.synthetic, args.synthetic_regime)
    stage = f"00_thresholds[{mode}{'-synthetic' if args.synthetic else ''}]"
    mode_label = "synthetic" if args.synthetic else mode
    expected_prov = PROV_SYNTH if args.synthetic else ("PLACEHOLDER" if mode == "sample" else
                                                       common.provenance(cfg, mode))

    # checkpoint: skip a finished run unless --force (match on provenance to avoid staleness)
    if out_path.exists() and not args.force:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8")).get("provenance")
        except (ValueError, OSError):
            existing = None
        if existing == expected_prov:
            log.info("checkpoint hit (%s) — skipping; use --force to rebuild", out_path)
            common.append_run_log(cfg, stage, mode, 0.0, common.peak_ram_mb(), "", {"skipped": "checkpoint"})
            return

    if args.synthetic:
        con = common.connect(cfg["runtime"]["duckdb_threads"])
        with common.timed_stage(cfg, stage, mode, synthetic=True):
            derived = derive_on_synthetic(con, cfg, args.synthetic_regime, args.force, log)
            payload = derived_payload(cfg, mode_label, PROV_SYNTH, derived,
                                      source=f"synthetic fixture ({args.synthetic_regime}); NOT a finding")
    elif mode == "sample":
        with common.timed_stage(cfg, stage, mode, synthetic=False):
            payload = placeholder_payload(cfg, mode_label)
    else:  # full
        con = common.connect(cfg["runtime"]["duckdb_threads"])
        with common.timed_stage(cfg, stage, mode, synthetic=False):
            gt = _parquet_full(con, cfg, "game_tracking_full", args.force, log)
            pp = _parquet(con, cfg, "player_play", args.force, log)
            cr = _parquet(con, cfg, "combine_results", args.force, log)
            efforts = gf.extract_efforts(con, gt, pp, cr, cfg)
            log.info("full: %d route-snap efforts across the game data", len(efforts))
            if not efforts:
                record_blocker(cfg, "no route-snap efforts with tracking -> cannot derive FULL "
                                    "thresholds; see notes/pending_full_run.md", log)
                raise SystemExit(2)
            derived = gf.derive_thresholds(efforts, cfg)
            payload = derived_payload(cfg, mode_label, common.provenance(cfg, mode), derived,
                                      source="full game tracking (percentile method)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("wrote %s (provenance=%s)", out_path, payload["provenance"])


if __name__ == "__main__":
    main()
