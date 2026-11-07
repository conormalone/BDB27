"""Stage 03 — Phase 3: the turn envelope (TASK.md section 3).

Reads only ``outputs/turns/turns.parquet`` (written by 02_turns) and writes the
per-player envelope, the fit summary, the model-free cross-check, the feasible
region and the manifest under ``outputs/envelope/``.

Run:
    python src/03_envelope.py --mode sample [--force] [--config path]

Sample mode always runs and stamps everything ``PLACEHOLDER_SAMPLE``. Full mode
refuses unless the thresholds provenance is ``FULL`` **and** ``notes/prereg.md``
exists: the task requires a frozen prereg before the first full-data run, and a
placeholder thresholds contract can never be the basis of a result.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from bdb27 import common, envelope

STAGE = envelope.STAGE


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
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
                            "refusing to fit an envelope on placeholder thresholds")
    prereg = cfg.repo_path("prereg")
    if not prereg.exists():
        problems.append(f"prereg missing ({prereg}) — TASK.md requires "
                        "notes/prereg.md before the first full-data run")
    if problems:
        raise common.StageRefused("03_envelope --mode full: " + "; ".join(problems))


def checkpoint_ok(cfg: common.Config, mode: str, outdir) -> bool:
    man = outdir / "manifest.json"
    if not man.exists() or not (outdir / "envelope_players.parquet").exists():
        return False
    try:
        d = json.loads(man.read_text())
    except Exception:  # pragma: no cover - corrupt manifest means rebuild
        return False
    return (d.get("config_sha") == common.config_fingerprint(cfg)
            and d.get("mode") == mode
            and d.get("provenance") == common.provenance(mode))


def _write_artefacts(result: envelope.EnvelopeResult, cfg: common.Config,
                     mode: str, outdir) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    common.write_parquet(result.players, outdir / "envelope_players.parquet",
                         envelope.ENVELOPE_PLAYER_SCHEMA, mode, cfg)
    common.write_parquet(result.feasible, outdir / "feasible_region.parquet",
                         envelope.FEASIBLE_SCHEMA, mode, cfg)
    common.write_csv(result.fit, outdir / "envelope_fit.csv", mode, cfg)
    common.write_csv(result.modelfree, outdir / "envelope_modelfree.csv", mode, cfg)
    common.write_csv(result.feasible_summary, outdir / "feasible_region_summary.csv",
                     mode, cfg)
    manifest = dict(result.manifest)
    manifest.update({
        "mode": mode,
        "provenance": common.provenance(mode),
        "label": common.stamp(mode),
        "git_rev": common.git_rev(),
        "config_git_rev": common.git_rev(),
    })
    common.write_json(manifest, outdir / "manifest.json", mode, cfg)


def _headline(result: envelope.EnvelopeResult, mode: str, outdir) -> None:
    man = result.manifest
    counts = man["counts"]
    print(f"{STAGE}: {common.stamp(mode)}")
    print(f"  settings fitted : {', '.join(man['settings_fitted'])}")
    print(f"  players fitted  : " + ", ".join(
        f"{k}={v}" for k, v in man["players_fitted"].items()))
    ex = counts["excluded"]
    print(f"  turns           : in={counts['turns_in']} used={counts['turns_used']} "
          f"excluded(invalid={ex['invalid']}, below_apex_floor={ex['below_apex_floor']}, "
          f"nonfinite={ex['nonfinite']}, out_of_range={ex['out_of_range']})")
    fit = result.fit
    primary = fit[(fit.model == "capacity") & (fit.form == "lateral_at_apex")
                  & (np.isclose(fit.quantile, man["estimator"]["quantile"]))]
    for _, row in primary.sort_values(["setting", "position_group"]).iterrows():
        print(f"  capacity@4 yd/s  : {row['setting']:<8} {row['position_group']:<6} "
              f"median={row['capacity_at_4_median']:.2f} "
              f"[p5,p95]=[{row['capacity_at_4_p05']:.2f},{row['capacity_at_4_p95']:.2f}] "
              f"(n={int(row['n_players'])})")
    summary = result.feasible_summary
    if len(summary):
        for _, row in summary.iterrows():
            print(f"  feasible cells  : {row['setting']:<8} {row['position_group']:<6} "
                  f"{row['cell_scope']:<12} prob-feasible={int(row['n_cells_prob_feasible'])}"
                  f"/{int(row['n_cells'])} "
                  f"share-players={row['share_player_cells_prob_feasible']:.2f}")
    else:
        print("  feasible cells  : none computed (no plant turns)")
    print(f"  primary measure : {man['primary_measure']}")
    print(f"  wrote           : {outdir}")


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    check_provenance(cfg, args.mode)

    outdir = cfg.repo_path("envelope")
    if checkpoint_ok(cfg, args.mode, outdir) and not args.force:
        print(f"{STAGE}: checkpoint reused ({outdir / 'envelope_players.parquet'})")
        common.log_run(STAGE, args.mode, 0.0, 0, "OK",
                       "checkpoint reused, nothing rebuilt", cfg=cfg)
        return 0

    turns = envelope.load_turns(cfg.repo_path("turns") / "turns.parquet")
    with common.StageTimer(STAGE, args.mode, cfg) as timer:
        result = envelope.Envelope(turns, cfg).run()
        timer.rows = int(result.manifest["counts"]["turns_used"])
        timer.detail = (f"settings={len(result.manifest['settings_fitted'])} "
                        f"players={sum(result.manifest['players_fitted'].values())}")
        _write_artefacts(result, cfg, args.mode, outdir)
        _headline(result, args.mode, outdir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(f"[{STAGE}] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
