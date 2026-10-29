#!/usr/bin/env python3
"""05_models.py — the primary within-player leverage estimand (TASK.md Phase 4).

Reads ``04_efforts/efforts.csv`` and fits

    log(max_speed) ~ leverage + route family + centred break_depth_yd
                     + break_found + time_to_throw + cumulative_load
                     + quarter + score margin + down/distance

with player-clustered standard errors.  The estimand is the population
coefficient on leverage.  ``break_depth_yd`` is a **control**: it is reported,
never interpreted.

Convergence fallbacks, tried in the declared order and recorded in the output:

1. ``uncorrelated_re`` — (1|player) + (0+leverage|player), uncorrelated;
2. ``player_fixed_effects`` — player fixed effects;
3. ``random_intercept`` — random intercept only.

The achieved analytic MDE is reported next to every interval.  It is stated,
never used as a gate: D60's 0.0443 log-speed at 40 efforts/player.

Out of scope this cycle (recorded as NOT YET WIRED in D65): matched design,
headroom moderation, ceiling bound, dose-response, per-player slopes, TE
replication and all of Phase 5 validation.  Nothing here pretends they ran.

Run:
    python src/05_models.py --mode sample [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import lc_common as C
import lc_models as M

STAGE = "05_models"

NOT_YET_WIRED = [
    "Phase 4 matched design (CEM high vs low leverage, clustered bootstrap)",
    "Phase 4 headroom moderation (leverage x baseline utilization)",
    "Phase 4 ceiling bound check (predicted effort fraction vs plausible ceiling)",
    "Phase 4 dose-response across leverage bins",
    "Phase 4 per-player leverage slopes + reliability screen",
    "Phase 4 TE replication",
    "Phase 5 validation (leverage permutation, time/load orthogonalised refit, "
    "placebos, tactical-demand and garbage-time checks, equivalence test)",
]


def _output_dir(cfg: dict, mode: str) -> Path:
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["models"].get("out_subdir", "game/lc/models")
    out = paths["outputs"] / sub
    out.mkdir(parents=True, exist_ok=True)
    return out


def _efforts_path(cfg: dict, mode: str) -> Path:
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["efforts"].get("out_subdir", "game/lc/efforts")
    return paths["outputs"] / sub / "efforts.csv"


def load_efforts(cfg: dict, mode: str) -> pd.DataFrame:
    path = _efforts_path(cfg, mode)
    if not path.exists():
        raise RuntimeError(
            f"STAGE {STAGE!r}: {path} is absent. Run 04_efforts first.")
    frame = pd.read_csv(path, low_memory=False)
    M.assert_effort_provenance(frame, mode, STAGE)
    return frame


def _normalise_term(name: str) -> str:
    if name in ("_break_depth_centred", "break_depth_yd_centred"):
        return "break_depth_yd_centred"
    if name == "_break_found":
        return "break_found"
    return name


def _depth_report(result: dict) -> dict:
    terms = {_normalise_term(t["term"]): t for t in result.get("terms", [])}
    depth = terms.get("break_depth_yd_centred", {})
    indicator = terms.get("break_found", {})
    return {
        "note": "break_depth_yd is a CONTROL; reported, never interpreted",
        "depth_coefficient": depth.get("estimate"),
        "depth_se": depth.get("se"),
        "break_found_coefficient": indicator.get("estimate"),
        "break_found_se": indicator.get("se"),
    }


def fit_and_summarise(table: pd.DataFrame, cfg: dict, mode: str) -> dict:
    block = C.lc(cfg)["models"]
    controls = tuple(block["controls"])
    outcome = str(block.get("outcome", "log_max_speed"))
    leverage_col = str(block.get("leverage_column", "leverage_z"))
    player_col = "nfl_id"

    work = table.copy()
    for column in (outcome, leverage_col):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=[outcome, leverage_col]).reset_index(drop=True)
    if work.empty:
        raise RuntimeError(
            f"STAGE {STAGE!r}: no rows with both {outcome!r} and "
            f"{leverage_col!r}; nothing to estimate.")
    work["nfl_id"] = work["nfl_id"].astype(str)

    result = M.fit_ladder(work, outcome=outcome, leverage_col=leverage_col,
                          controls=controls, player_col=player_col, cfg=cfg)
    n = int(result.get("n", len(work)))
    n_players = int(result.get("n_players", work["nfl_id"].nunique()))
    per_player = n / max(n_players, 1)
    mde = M.analytic_mde(per_player, cfg)
    mde["mde_at_configured"] = M.analytic_mde(
        mde["configured_efforts_per_player"], cfg)["mde"]

    one_bin = 0
    if "one_bin_player" in work.columns:
        values = work["one_bin_player"]
        if values.dtype == object:
            values = values.astype(str).str.lower().isin(("true", "1"))
        one_bin = int(work.loc[values.astype(bool), "nfl_id"].nunique())
    counts = work.groupby("nfl_id").size()

    summary = {
        "stage": STAGE,
        "mode": mode,
        "specification": (
            f"{outcome} ~ {leverage_col} + " + " + ".join(controls)),
        "random_effects": {
            "uncorrelated_re": "(1 | player) + (0 + leverage | player), uncorrelated",
            "player_fixed_effects": "player fixed effects (no random effects)",
            "random_intercept": "(1 | player)",
        }.get(result.get("fallback_used"), "none"),
        "estimand": (
            "population coefficient on leverage (per 1 within-player SD); "
            "player-clustered SE"),
        "outcome": outcome,
        "leverage_column": leverage_col,
        "leverage_units": "per 1 within-player SD of 4*p*(1-p)",
        "controls": list(controls),
        "cluster": "player",
        "n_efforts": n,
        "n_players": n_players,
        "efforts_per_player": float(per_player),
        "beta": result.get("beta"),
        "se": result.get("se"),
        "t": result.get("t"),
        "p": result.get("p"),
        "ci_low": result.get("ci_low"),
        "ci_high": result.get("ci_high"),
        "fallback_used": result.get("fallback_used"),
        "ladder": result.get("ladder", []),
        "mde": mde,
        "depth_control": _depth_report(result),
        "edge_cases": {
            "n_players_one_leverage_bin": one_bin,
            "n_single_effort_players": int((counts == 1).sum()),
            "n_players_below_min_efforts": int(
                (counts < int(C.lc(cfg)["thresholds"]["min_efforts_per_player"])).sum()),
        },
        "not_yet_wired": NOT_YET_WIRED,
    }
    coefficient_rows = []
    for term in result.get("terms", []):
        row = dict(term)
        row["term"] = _normalise_term(row["term"])
        coefficient_rows.append(row)
    summary["_coefficients"] = coefficient_rows
    return summary


def _fmt(value) -> str:
    try:
        if value is None or not np.isfinite(float(value)):
            return "NA"
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "NA"


def _markdown(summary: dict) -> str:
    mde = summary["mde"]
    lines = [
        "# Primary within-player leverage model (TASK.md Phase 4)",
        "",
        "**This is a result only when the provenance line says FULL.** Sample "
        "output is plumbing and is not interpreted.",
        "",
        f"- Specification: `{summary['specification']}`",
        f"- Random effects: {summary['random_effects']}",
        f"- Estimand: {summary['estimand']}",
        f"- Efforts: {summary['n_efforts']:,}  ·  players: {summary['n_players']}  ·  "
        f"efforts/player: {summary['efforts_per_player']:.1f}",
        f"- Convergence fallback used: **{summary['fallback_used']}**",
        "",
        "## Leverage coefficient",
        "",
        f"- estimate: **{_fmt(summary['beta'])}** log-speed per 1 within-player SD "
        f"of leverage",
        f"- clustered SE: {_fmt(summary['se'])}  ·  t = {_fmt(summary['t'])}  ·  "
        f"p = {_fmt(summary['p'])}",
        f"- 95% CI: [{_fmt(summary['ci_low'])}, {_fmt(summary['ci_high'])}]",
        f"- achieved analytic MDE at {summary['efforts_per_player']:.1f} "
        f"efforts/player: **{mde['mde']:.4f}** log-speed "
        f"(D60: {mde['mde_at_configured']:.4f} at the configured "
        f"{mde['configured_efforts_per_player']:.0f}); smallest effect of interest "
        f"{mde['smallest_effect_of_interest']:.2f}. The MDE is stated, not a gate.",
        "",
        "## Depth control — not a finding",
        "",
        f"`break_depth_yd` is a CONTROL: reported, never interpreted. "
        f"Centred depth coefficient {_fmt(summary['depth_control']['depth_coefficient'])} "
        f"(SE {_fmt(summary['depth_control']['depth_se'])}); "
        f"`break_found` {_fmt(summary['depth_control']['break_found_coefficient'])} "
        f"(SE {_fmt(summary['depth_control']['break_found_se'])}).",
        "",
        "## Fallback ladder (tried in order)",
        "",
        "| rung | status | reason |",
        "|---|---|---|",
    ]
    for step in summary["ladder"]:
        lines.append(f"| {step['rung']} | {step['status']} | "
                     f"{step.get('reason', '')} |")
    lines += [
        "",
        "## Edge cases (reported, never crashed on)",
        "",
        f"- Players with effects in only one leverage bin: "
        f"{summary['edge_cases']['n_players_one_leverage_bin']}",
        f"- Single-effort players: {summary['edge_cases']['n_single_effort_players']}",
        f"- Players below the configured minimum efforts: "
        f"{summary['edge_cases']['n_players_below_min_efforts']}",
        "",
        "## Not yet wired (recorded in D65; nothing below ran in this stage)",
        "",
    ]
    lines += [f"- {item}" for item in summary["not_yet_wired"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sample", "full"), default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    cfg = C.load_config(args.config)
    mode = args.mode or C.lc(cfg)["mode"]["default"]
    paths = C.resolve_paths(cfg, mode)
    C.assert_disk_space(paths, cfg)
    C.require_full_provenance(cfg, mode, STAGE)
    if C.is_done(cfg, STAGE, mode, force=args.force):
        print(f"[{STAGE}] checkpoint present — skipping (use --force to rebuild)")
        return 0
    out = _output_dir(cfg, mode)
    table = load_efforts(cfg, mode)

    with C.timed_stage(cfg, STAGE, mode,
                       note="primary within-player leverage estimand") as stage:
        summary = fit_and_summarise(table, cfg, mode)
        stage.note(f"beta={_fmt(summary['beta'])}")
        stage.note(f"fallback={summary['fallback_used']}")

    coefficients = summary.pop("_coefficients")
    C.write_json(out / "model_result.json", summary, mode=mode, cfg=cfg)
    C.write_csv(out / "model_coefficients.csv", coefficients,
                ["term", "estimate", "se", "ci_low", "ci_high", "role"],
                mode=mode, cfg=cfg)
    C.write_markdown(out / "SUMMARY.md", _markdown(summary), mode=mode, cfg=cfg)
    C.mark_done(cfg, STAGE, mode, extra={
        "beta": summary["beta"],
        "fallback_used": summary["fallback_used"],
        "n_efforts": summary["n_efforts"],
    })
    print(f"[{STAGE}] beta={_fmt(summary['beta'])} "
          f"CI=[{_fmt(summary['ci_low'])}, {_fmt(summary['ci_high'])}] "
          f"fallback={summary['fallback_used']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
