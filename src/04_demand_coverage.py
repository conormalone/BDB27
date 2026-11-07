"""Stage 04 — Phase 4–5: the demand map, Turn Coverage and the Cut Card data.

Reads only Stage 02/03 artefacts (``outputs/turns/turns.parquet``,
``outputs/envelope/feasible_region.parquet``,
``outputs/envelope/envelope_players.parquet``,
``outputs/envelope/variance_components.csv``) plus ``player_play`` for the DL
outcome weighting, and writes ``outputs/demand_coverage/``.

Run:
    python src/04_demand_coverage.py --mode sample [--force] [--config path]

Sample mode always runs and stamps everything ``PLACEHOLDER_SAMPLE``. Full mode
refuses unless the thresholds provenance is ``FULL`` **and** ``notes/prereg.md``
exists, naming every missing precondition: a placeholder thresholds contract can
never be the basis of a result, and the task requires a frozen prereg before the
first full-data run.

This stage writes Cut Card **data** only. Rendering belongs to Stage 06 and is
not performed here.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from bdb27 import common, demand_coverage

STAGE = demand_coverage.STAGE


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
                            "refusing to score coverage on placeholder thresholds")
    prereg = cfg.repo_path("prereg")
    if not prereg.exists():
        problems.append(f"prereg missing ({prereg}) — TASK.md requires "
                        "notes/prereg.md before the first full-data run")
    if problems:
        raise common.StageRefused(f"{STAGE} --mode full: " + "; ".join(problems))


def checkpoint_ok(cfg: common.Config, mode: str, outdir) -> bool:
    man = outdir / "manifest.json"
    needed = [man, outdir / "demand_map.csv", outdir / "coverage_players.parquet"]
    if not all(p.exists() for p in needed):
        return False
    try:
        d = json.loads(man.read_text())
    except Exception:  # pragma: no cover - corrupt manifest means rebuild
        return False
    return (d.get("config_sha") == common.config_fingerprint(cfg)
            and d.get("mode") == mode
            and d.get("provenance") == common.provenance(mode)
            and d.get("artefact_version") == demand_coverage.ARTEFACT_VERSION)


def _write_artefacts(result: demand_coverage.DemandCoverageResult,
                     cfg: common.Config, mode: str, outdir) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    common.write_csv(result.demand_map, outdir / "demand_map.csv", mode, cfg)
    common.write_parquet(result.coverage, outdir / "coverage_players.parquet",
                         demand_coverage.COVERAGE_SCHEMA, mode, cfg)
    common.write_csv(result.ranking, outdir / "coverage_position_ranking.csv",
                     mode, cfg)
    common.write_csv(result.cut_gaps_players, outdir / "cut_gaps_players.csv",
                     mode, cfg)
    common.write_csv(result.cut_gaps_position, outdir / "cut_gaps_position.csv",
                     mode, cfg)
    common.write_parquet(result.cut_card_cells, outdir / "cut_card_cells.parquet",
                         demand_coverage.CUT_CARD_CELL_SCHEMA, mode, cfg)
    common.write_csv(result.cut_card_players, outdir / "cut_card_players.csv",
                     mode, cfg)
    manifest = dict(result.manifest)
    manifest.update({
        "mode": mode,
        "provenance": common.provenance(mode),
        "label": common.stamp(mode),
        "git_rev": common.git_rev(),
    })
    common.write_json(manifest, outdir / "manifest.json", mode, cfg)


def _fmt(x, spec: str = ".3f") -> str:
    try:
        value = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return format(value, spec) if np.isfinite(value) else "n/a"


def _headline(result: demand_coverage.DemandCoverageResult, mode: str, outdir) -> None:
    man = result.manifest
    counts = man["demand_counts"]
    print(f"{STAGE}: {common.stamp(mode)}")

    per_group = counts["per_group"]
    print("  positions       : " + ", ".join(
        f"{g}={int(per_group.get(g, 0))} eligible turns"
        for g in man["positions"]))
    ex = counts["excluded"]
    print(f"  turns           : in={counts['turns_in']} eligible={counts['eligible']} "
          f"excluded(" + ", ".join(f"{k}={v}" for k, v in ex.items()) + ")")

    mass = man["demand_counts"]["total_demand_mass"]
    print(f"  demand mass     : {_fmt(mass, '.0f')} weighted turns "
          f"(frequency); per group " + ", ".join(
              f"{g}={_fmt(result.demand_map[(result.demand_map.position_group == g) & (result.demand_map.weight_mode == 'frequency')].weight.sum(), '.0f')}"
              for g in man["positions"]))

    dist = man["coverage"]["distribution"]
    print("  coverage        : " + ", ".join(
        f"{scope} mean={_fmt(dist[scope]['mean'])} "
        f"[{_fmt(dist[scope]['min'])},{_fmt(dist[scope]['max'])}]"
        for scope in ("combined", "observed", "extrapolated")))
    print(f"  coverage rows   : {man['coverage']['n_players']} Combine player(s), "
          f"method={man['coverage']['method']}, "
          f"intervals={man['coverage']['interval_method']}")

    boundary = man["coverage"]["boundary"]
    print(f"  boundary share  : {man['coverage']['n_shared_players']}/"
          f"{man['coverage']['n_players']} players in a shared group "
          f"({man['coverage']['n_shared_groups']}/{man['coverage']['n_groups']} groups "
          f"group-level); plant_retention "
          f"{boundary['n_components_at_boundary']}/{boundary['n_components']} "
          "components AT_BOUNDARY")

    gaps = man["cut_gaps"]
    print(f"  cut gaps        : {gaps['n_players_with_gaps']} player(s), "
          f"{gaps['n_player_cell_rows']} player-cell row(s), "
          f"top_n={gaps['top_n']}, mean gap={_fmt(gaps['mean_gap_score'])}")

    outcome = man["outcome_weighting"]
    scope_counts = outcome["counts"]
    print(f"  outcome weights : {outcome['status']}"
          + (f" ({outcome['reason']})" if outcome.get("reason") else "")
          + f", positions={','.join(outcome['positions'])}, "
            f"qualifying turns={scope_counts.get('qualifying_turns_in_scope', 0)}"
            f"/{scope_counts.get('turns_in_scope', 0)} in scope")

    cards = man["cut_cards"]
    print(f"  cut cards       : {cards['n_cards']} selected "
          f"({cards['selection']}); rendering={cards['rendering']}")
    print(f"  wrote           : {outdir}")


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    check_provenance(cfg, args.mode)

    outdir = cfg.repo_path("demand_coverage")
    if checkpoint_ok(cfg, args.mode, outdir) and not args.force:
        print(f"{STAGE}: checkpoint reused ({outdir / 'coverage_players.parquet'})")
        common.log_run(STAGE, args.mode, 0.0, 0, "OK",
                       "checkpoint reused, nothing rebuilt", cfg=cfg)
        return 0

    turns = demand_coverage.load_turns(cfg.repo_path("turns") / "turns.parquet")
    feasible = demand_coverage.load_feasible(
        cfg.repo_path("envelope") / "feasible_region.parquet")
    players = demand_coverage.load_envelope_players(
        cfg.repo_path("envelope") / "envelope_players.parquet")
    variance = demand_coverage.load_variance(
        cfg.repo_path("envelope") / "variance_components.csv")
    labels, label_meta = demand_coverage.load_outcome_labels(cfg, args.mode)

    with common.StageTimer(STAGE, args.mode, cfg) as timer:
        result = demand_coverage.DemandCoverage(
            turns, feasible, players, variance, cfg,
            labels=labels, label_meta=label_meta).run()
        timer.rows = int(result.manifest["demand_counts"]["eligible"])
        timer.detail = (f"groups={len(result.manifest['positions'])} "
                        f"players={result.manifest['coverage']['n_players']} "
                        f"cards={result.manifest['cut_cards']['n_cards']}")
        _write_artefacts(result, cfg, args.mode, outdir)
        _headline(result, args.mode, outdir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(f"[{STAGE}] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
