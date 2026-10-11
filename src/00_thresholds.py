"""Phase 4 prerequisite — game-side effort thresholds for the BDB27 study.

Freezes the high-intensity effort cut-offs and the minimum-snaps rule used by
``04_game_features.py`` and ``05_link.py`` (TASK.md Phase 4).

Modes
-----
- ``--mode full``   : the cut-offs are computed **data-driven from the full game
  tracking data** (DuckDB streaming quantiles; ``config.yaml`` ``game.thresholds``
  selects the method/percentiles). The written JSON carries ``provenance = "FULL"``.
- ``--mode sample`` : the cut-offs are the **a-priori placeholders** in
  ``config.yaml`` ``game.sample_placeholders`` — **never derived from the sample**
  (TASK.md: "Never set thresholds from the sample"). The JSON carries
  ``provenance = "PLACEHOLDER_SAMPLE"``.

``04``/``05`` assert ``provenance == "FULL"`` in full mode (TASK.md stop rule).

Memory discipline: the game CSVs are streamed by DuckDB (``read_csv_auto`` +
scalar quantile aggregates) — never loaded into pandas.

Run:
    python src/00_thresholds.py --mode sample
    python src/00_thresholds.py --mode full --force
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    append_run_log,
    connect,
    get_logger,
    load_config,
    peak_ram_mb,
    provenance,
    resolve,
    seed_everything,
    timed_stage,
)

PROV_SAMPLE = "PLACEHOLDER_SAMPLE"
PROV_FULL = "FULL"
PROV_FULL_FIXED = "FULL_FIXED"


def _source_globs(cfg: dict[str, Any], mode: str) -> list[Path]:
    """Resolve the game-tracking CSV source(s) for the given mode."""
    if mode == "sample":
        name = cfg["files"]["game_tracking_sample"]
        return [resolve(cfg, "raw", name)]
    names = cfg["files"].get("game_tracking_full") or []
    if not names:
        raise FileNotFoundError("config.files.game_tracking_full is empty (full-mode source)")
    return [resolve(cfg, "raw", n) for n in names]


def _union_sql(paths: list[Path]) -> str:
    """A DuckDB read_csv_auto over one-or-many CSVs (union_by_name)."""
    lst = ", ".join(f"'{p}'" for p in paths)
    return f"read_csv_auto([{lst}], header=true, union_by_name=true, nullstr=['NA',''])"


def compute_full_thresholds(cfg: dict[str, Any], logger) -> dict[str, Any]:
    """Data-driven cut-offs from the FULL game data (DuckDB streaming quantiles)."""
    t = cfg["game"]["thresholds"]
    method = str(t["method"]).lower()
    src = _union_sql(_source_globs(cfg, "full"))
    con = connect(threads=int(cfg.get("runtime", {}).get("duckdb_threads", 1)))

    if method == "fixed":
        return {
            "method": "fixed",
            "high_speed_threshold_yds": float(t["high_speed_threshold_yds"]),
            "accel_threshold_yds2": float(t["accel_threshold_yds2"]),
        }

    hs_pct = float(t["high_speed_percentile"]) / 100.0
    ac_pct = float(t["accel_percentile"]) / 100.0
    spd_floor = float(t["min_speed_floor_yds"])
    acc_floor = float(t["min_accel_floor_yds2"])

    row = con.execute(
        f"""
        SELECT
          count(*) AS n_frames,
          quantile_cont(s, {hs_pct}) AS hs_thr,
          quantile_cont(abs(a), {ac_pct}) AS ac_thr,
          max(s) AS max_speed_yds,
          max(abs(a)) AS max_abs_accel_yds2
        FROM {src}
        WHERE s IS NOT NULL
          AND s > {spd_floor}
          AND a IS NOT NULL
          AND abs(a) >= {acc_floor}
        """
    ).fetchone()
    n_frames, hs_thr, ac_thr, max_s, max_a = row
    logger.info("full thresholds: n_frames=%s hs=%.4f ac=%.4f (max s=%.3f a=%.3f)",
                n_frames, hs_thr or float("nan"), ac_thr or float("nan"),
                max_s or float("nan"), max_a or float("nan"))
    return {
        "method": "percentile",
        "high_speed_threshold_yds": float(hs_thr) if hs_thr is not None else float(t["high_speed_threshold_yds"]),
        "accel_threshold_yds2": float(ac_thr) if ac_thr is not None else float(t["accel_threshold_yds2"]),
        "n_frames_scanned": int(n_frames or 0),
        "max_speed_yds": float(max_s) if max_s is not None else None,
        "max_abs_accel_yds2": float(max_a) if max_a is not None else None,
    }


def compute_sample_placeholders(cfg: dict[str, Any]) -> dict[str, Any]:
    """The config a-priori placeholders (never derived from the sample, D8)."""
    ph = cfg["game"]["sample_placeholders"]
    return {
        "method": "placeholder",
        "high_speed_threshold_yds": float(ph["high_speed_threshold_yds"]),
        "accel_threshold_yds2": float(ph["accel_threshold_yds2"]),
    }


def build_thresholds(cfg: dict[str, Any], mode: str, logger) -> dict[str, Any]:
    """Assemble the thresholds.json payload for the given mode."""
    min_snaps = int(cfg["game"]["thresholds"]["min_snaps"])
    if mode == "sample":
        prov = PROV_SAMPLE
        core = compute_sample_placeholders(cfg)
    else:
        core = compute_full_thresholds(cfg, logger)
        prov = PROV_FULL_FIXED if core["method"] == "fixed" else PROV_FULL
    payload = {
        "provenance": prov,
        "mode": mode,
        "method": core["method"],
        "high_speed_threshold_yds": core["high_speed_threshold_yds"],
        "accel_threshold_yds2": core["accel_threshold_yds2"],
        "min_snaps": min_snaps,
        "source_files": [str(p) for p in _source_globs(cfg, mode)],
    }
    for k, v in core.items():
        payload.setdefault(k, v)
    return payload


def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    out_path = resolve(cfg, "outputs", cfg["game"]["thresholds_file"])

    logger.info("=" * 72)
    logger.info("BDB27 Phase 4 thresholds | mode=%s", mode)
    logger.info("=" * 72)

    if out_path.exists() and not force:
        logger.info("checkpoint hit: %s exists (use --force to rebuild)", out_path)
        append_run_log(cfg, "00_thresholds:checkpoint", mode, 0.0, peak_ram_mb(), "",
                       {"skipped": True, "reason": "thresholds.json exists"})
        return 0

    with timed_stage(cfg, f"00_thresholds:{mode}", mode, out=str(out_path)):
        payload = build_thresholds(cfg, mode, logger)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")

    logger.info("wrote %s | provenance=%s hs=%.4f yd/s ac=%.4f yd/s^2 min_snaps=%d",
                out_path.name, payload["provenance"], payload["high_speed_threshold_yds"],
                payload["accel_threshold_yds2"], payload["min_snaps"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 4 — game effort thresholds")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild outputs (ignore checkpoint)")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or str(cfg["mode"]["default"])
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
