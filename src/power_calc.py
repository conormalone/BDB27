#!/usr/bin/env python3
"""power_calc — write the design-power artefact for the locked 50 ms jump SD.

    python src/power_calc.py --mode sample

This is a standalone evidence script, like ``crossing_diagnostic.py``: it is not
a stage in ``run.py`` (whose order is frozen by TASK.md), but it still uses
``common.stage_bootstrap`` so the same full-mode gates apply. It reads only the
complete label-file artefacts from Phase 3b and writes
``outputs/prereg/power.json`` plus a short Markdown table.

Provenance is ``FULL`` because every input is a complete file. Every tracking
number is absent by construction: the 0.86 yd plane offset does not enter, and
the locked outcome's spot error is carried as a stated caveat rather than
silently modelled.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402
import power as P  # noqa: E402

STAGE = P.STAGE


def _fmt(value, digits=1):
    if value is None:
        return "n/a"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    if x != x:  # NaN
        return "n/a"
    return f"{x:.{digits}f}"


def table_markdown(report: dict) -> str:
    lines = [
        "| min reps/player | P admitted | fittable P | median reliability, "
        "fittable (three-level) | P_eff | smallest detectable jump SD (ms) | 50 ms? |",
        "|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in report["table"]:
        lines.append(
            f"| {int(row['min_reps_per_player'])} | {row['players_admitted']} | "
            f"{row['players_fittable']} | "
            f"{_fmt(row['median_reliability_fittable_three_level'], 3)} | "
            f"{_fmt(row['effective_players_fittable'], 1)} | "
            f"{_fmt(row['smallest_detectable_jump_sd_ms_effective'], 1)} | "
            f"{'yes' if row['resolves_50ms'] else 'no'} |")
    a = report["no_rep_cut"]
    lines.append(
        f"| no cut (all) | {a['players_admitted']} | {a['players_fittable']} | "
        f"{_fmt(a['median_reliability_fittable_three_level'], 3)} | "
        f"{_fmt(a['effective_players_fittable'], 1)} | "
        f"{_fmt(a['smallest_detectable_jump_sd_ms_effective'], 1)} | "
        f"{'yes' if a['resolves_50ms'] else 'no'} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    cfg, mode, _force = C.stage_bootstrap(argv, STAGE)
    p = C.paths(cfg, mode)
    pw = C.section(cfg, "power")
    phase3b = p["phase3b"]
    summary_path = phase3b / str(pw["summary_table"])
    rel_path = phase3b / str(pw["reliability_table"])
    for path in (summary_path, rel_path):
        if not path.exists():
            raise C.GateRefused(
                f"{STAGE}: refusing to run — {path} does not exist. Run "
                f"03b_reliability first; the power question is answered on its "
                f"complete-file evidence.")

    with C.timed_stage(cfg, STAGE, mode, note="design power for 50 ms jump SD"):
        summary = P.load_summary(summary_path)
        players = P.load_player_table(rel_path)
        between_sd = float(summary["distribution"]["sd_between_player_anova"])
        jump_sd = float(pw["jump_sd_ms"]) / 1000.0
        z = float(pw["z"])
        report = P.build_report(
            players, summary,
            cuts=[float(c) for c in pw["min_reps_candidates"]],
            between_sd_s=between_sd, jump_sd_s=jump_sd, z=z,
            output_inputs={
                "phase3b_summary": str(summary_path),
                "phase3b_summary_sha256": P.sha256_file(summary_path),
                "reliability_table": str(rel_path),
                "reliability_table_sha256": P.sha256_file(rel_path),
                "reliability_estimator": "three-level nested ANOVA (reps in "
                                         "games in players), method of moments",
            })

        out_dir = p["prereg_outputs"]
        out_dir.mkdir(parents=True, exist_ok=True)
        out = C.write_json(out_dir / "power.json", report, mode=mode, cfg=cfg,
                           stamp="FULL")
        C.write_markdown(out_dir / "power_table.md", table_markdown(report),
                         mode=mode, cfg=cfg, stamp="FULL")

        print(f"{STAGE}: 50 ms = "
              f"{report['locked_soi']['jump_share_of_between_player_variance']:.3f} "
              f"of between-player variance")
        print(table_markdown(report))
        print("answers:")
        for key in ("detectable_at_any_candidate_cut",
                    "smallest_cut_resolving_50ms",
                    "smallest_detectable_jump_sd_ms_at_20_reps",
                    "smallest_detectable_jump_sd_ms_best_case",
                    "players_required_for_50ms",
                    "fittable_players_available"):
            print(f"  {key}: {report['answers'][key]}")
        print(f"  verdict: {report['answers']['verdict']}")
        print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
