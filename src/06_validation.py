#!/usr/bin/env python3
"""06_validation.py — Phase 5 validation of the within-player leverage estimand.

Reads ``04_efforts/efforts.csv`` and the ``05_models`` result, then runs the
TASK.md Phase 5 checks without ever re-specifying the model:

* leverage permutation within player-game and within player-route-family
  (declared shuffle count, proper null distribution written out);
* the time-and-load orthogonalised refit (``lc_models.fit_orthogonalised``);
* placebos on route depth, the break indicator and time to throw;
* the garbage-time exclusion (rule frozen in ``leverage.garbage_time``),
  reported with and without;
* the declared sensitivity curve (leverage definition, bin cuts, effort
  threshold, minimum efforts, final two minutes, time-to-throw restriction);
* the TOST equivalence test against the configured margin, with an explicit
  informative / uninformative verdict.

Outputs (``outputs/game/lc/validation/``): ``validation_result.json``,
``permutation_null.csv``, ``placebo_checks.csv``, ``garbage_time.csv``,
``sensitivity_curve.csv``, ``orthogonalisation.csv`` and ``SUMMARY.md``.  Every
artefact is stamped by ``lc_common`` with the run's provenance.

Run:
    python src/06_validation.py --mode sample [--force]
"""

from __future__ import annotations

import argparse
import numpy as np
import pandas as pd

import lc_common as C
import lc_models as M
import lc_validation as V

STAGE = "06_validation"

PLACEBO_FIELDS = ["outcome", "status", "beta", "se", "p", "n", "n_players", "note"]
GARBAGE_FIELDS = ["sample", "beta", "se", "ci_low", "ci_high", "p", "n",
                  "n_players"]
SENSITIVITY_FIELDS = ["spec", "status", "comparison", "leverage", "beta", "se",
                      "p", "beta_per_within_player_sd", "within_player_sd", "n",
                      "n_players", "note"]
ORTH_FIELDS = ["model", "beta", "se", "ci_low", "ci_high", "p", "n", "n_players"]


# --------------------------------------------------------------------------- #
# Paths and inputs
# --------------------------------------------------------------------------- #
def _out_dir(cfg: dict, mode: str):
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["validation"].get("out_subdir", "game/lc/validation")
    out = paths["outputs"] / sub
    out.mkdir(parents=True, exist_ok=True)
    return out


def _efforts_path(cfg: dict, mode: str):
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["efforts"].get("out_subdir", "game/lc/efforts")
    return paths["outputs"] / sub / "efforts.csv"


def _model_path(cfg: dict, mode: str):
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["models"].get("out_subdir", "game/lc/models")
    return paths["outputs"] / sub / "model_result.json"


def load_efforts(cfg: dict, mode: str) -> pd.DataFrame:
    path = _efforts_path(cfg, mode)
    if not path.exists():
        raise RuntimeError(f"STAGE {STAGE!r}: {path} is absent. Run 04_efforts first.")
    frame = pd.read_csv(path, low_memory=False)
    M.assert_effort_provenance(frame, mode, STAGE)
    return frame


def load_primary(cfg: dict, mode: str) -> dict:
    """The 05_models result, required for the equivalence test."""
    import json

    path = _model_path(cfg, mode)
    if not path.exists():
        raise RuntimeError(
            f"STAGE {STAGE!r}: {path} is absent. Run 05_models first — the "
            "equivalence test is against the primary fit, and validation does "
            "not re-specify the model.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    provenance = str(payload.get("provenance", ""))
    if mode == "full" and provenance != "FULL":
        raise RuntimeError(
            f"STAGE {STAGE!r}: mode=full but {path.name} provenance is "
            f"{provenance!r}, not 'FULL'. Rebuild 05_models on the full effort "
            "table before validating.")
    return payload


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #
def _jsonable(obj):
    """Recursively convert numpy scalars/arrays so JSON is plain and stable."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return value if np.isfinite(value) else None
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _fmt(value, nd: int = 4, signed: bool = False) -> str:
    try:
        if value is None or not np.isfinite(float(value)):
            return "NA"
        text = f"{float(value):.{nd}f}"
    except (TypeError, ValueError):
        return "NA"
    return f"+{text}" if signed and float(value) > 0 else text


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def _markdown(result: dict) -> str:
    eq = result.get("equivalence") or {}
    orth = result.get("orthogonalisation") or {}
    garbage = result.get("garbage_time") or {}
    sizes = result.get("sample_sizes") or {}
    mde = sizes.get("mde") or {}
    spec = result.get("specification_under_test") or {}
    primary = spec.get("primary_model_result") or {}

    lines = [
        "# Phase 5 validation — leverage permutation, confound, placebos, garbage time (D66)",
        "",
        "**This is a result only when the provenance says FULL. Sample output is "
        "plumbing and is not interpreted.**",
        "",
        "The specification under test is the `05_models` fit, unchanged:",
        f"`{spec.get('outcome')} ~ {spec.get('leverage_column')} + "
        + " + ".join(spec.get("controls") or []) + "`.",
        "",
        f"- efforts: {sizes.get('n_efforts')}  ·  players: {sizes.get('n_players')}"
        f"  ·  efforts/player: {_fmt(sizes.get('efforts_per_player'), 1)}",
        f"- primary estimate: {_fmt(primary.get('beta'), signed=True)} log-speed per 1 "
        f"within-player SD (fallback `{primary.get('fallback_used')}`)",
        f"- achieved analytic MDE: {_fmt(mde.get('mde'))} log-speed  ·  smallest "
        f"effect of interest {mde.get('smallest_effect_of_interest')}",
        "",
        "## 1. Leverage permutation (shuffle within the declared blocks)",
        "",
        "| scheme | blocks | reps | observed beta | p (two-sided) | null mean | null sd | null 2.5% | null 97.5% |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for scheme, row in (result.get("permutation", {}).get("schemes") or {}).items():
        q = row.get("quantiles") or {}
        lines.append(
            f"| {scheme} | {row.get('blocks')} | {row.get('reps')} | "
            f"{_fmt(row.get('observed_beta'), signed=True)} | "
            f"{_fmt(row.get('p_value'))} | "
            f"{_fmt(row.get('null_mean'), signed=True)} | {_fmt(row.get('null_sd'))} | "
            f"{_fmt(q.get('q025'), signed=True)} | {_fmt(q.get('q975'), signed=True)} |")
    lines += [
        "",
        "The p-value is `(1 + #{|null| >= |observed|}) / (reps + 1)`. The null "
        "distribution is written in full to `permutation_null.csv`.",
        "",
        "## 2. Time-and-load orthogonalisation",
        "",
        f"- naive FE estimate: {_fmt((orth.get('naive') or {}).get('beta'), signed=True)} "
        f"(SE {_fmt((orth.get('naive') or {}).get('se'))}, "
        f"p {_fmt((orth.get('naive') or {}).get('p'))})",
        f"- orthogonalised FE estimate: "
        f"{_fmt((orth.get('orthogonalised') or {}).get('beta'), signed=True)} "
        f"(SE {_fmt((orth.get('orthogonalised') or {}).get('se'))}, "
        f"p {_fmt((orth.get('orthogonalised') or {}).get('p'))})",
        f"- attenuation: {_fmt(orth.get('attenuation_fraction'))} of the naive "
        "magnitude",
        f"- within-player R^2 of leverage on the time/load controls: "
        f"{_fmt(orth.get('r2_leverage_on_time_load_within_player'))}",
        f"- within-player correlations: time "
        f"{_fmt(orth.get('within_player_corr_time'), signed=True)}, load "
        f"{_fmt(orth.get('within_player_corr_load'), signed=True)}",
        f"- effect survives the orthogonalisation: {orth.get('effect_survives')}",
        f"- {orth.get('note')}",
        "",
        "## 3. Placebos (leverage predicting pre-snap / route features)",
        "",
        "| outcome | status | beta | SE | p | n |",
        "|---|---|---|---|---|---|",
    ]
    for row in (result.get("placebo", {}).get("checks") or []):
        lines.append(
            f"| {row.get('outcome')} | {row.get('status')} | "
            f"{_fmt(row.get('beta'), signed=True)} | {_fmt(row.get('se'))} | "
            f"{_fmt(row.get('p'))} | {row.get('n')} |")
    lines += [
        "",
        "`break_depth_yd` is fitted among detected breaks only; its depth is "
        "missing elsewhere and is never imputed. The matched (CEM) versions and "
        "the non-max-effort contrast are not wired — see §7.",
        "",
        "## 4. Garbage time",
        "",
    ]
    if garbage.get("status") == "ok":
        rule = garbage.get("rule") or {}
        lines += [
            f"- rule: `{rule.get('rule')}` — |margin| >= {rule.get('q4_margin')} "
            f"from Q{rule.get('q4')} on, or |margin| >= "
            f"{rule.get('any_quarter_margin')} in any quarter",
            f"- garbage-time efforts: {garbage.get('n_garbage_time')} of "
            f"{garbage.get('n_total')} "
            f"({_fmt(garbage.get('share_garbage_time'))})",
            f"- share of each leverage bin that is garbage time: "
            + ", ".join(f"{k} {_fmt(v)}" for k, v in
                        (garbage.get('share_by_leverage_bin') or {}).items()),
            f"- with garbage time: beta "
            f"{_fmt((garbage.get('with_garbage_time') or {}).get('beta'), signed=True)}"
            f" (n {(garbage.get('with_garbage_time') or {}).get('n')})",
            f"- without garbage time: beta "
            f"{_fmt((garbage.get('without_garbage_time') or {}).get('beta'), signed=True)}"
            f" (n {(garbage.get('without_garbage_time') or {}).get('n')})",
        ]
    else:
        lines.append(f"- not evaluable: {garbage.get('status')}")
    lines += [
        "",
        "## 5. Declared sensitivity curve",
        "",
        "| spec | status | comparison | beta | SE | p | n |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in (result.get("sensitivity", {}).get("rows") or []):
        lines.append(
            f"| {row.get('spec')} | {row.get('status')} | {row.get('comparison')} | "
            f"{_fmt(row.get('beta'), signed=True)} | {_fmt(row.get('se'))} | "
            f"{_fmt(row.get('p'))} | {row.get('n')} |")
    lines += [
        "",
        "A high-vs-low row's coefficient is a bin difference, not a per-SD slope; "
        "the raw-`leverage` row is per unit of 4p(1-p), also not comparable to the "
        "primary scale. Both are labelled in the CSV.",
        "",
        "## 6. Equivalence test",
        "",
        f"- margin: {eq.get('margin')}  ·  smallest effect of interest: "
        f"{eq.get('smallest_effect_of_interest')}  ·  alpha: {eq.get('alpha')}",
        f"- estimate {_fmt(eq.get('beta'), signed=True)}, SE {_fmt(eq.get('se'))}, "
        f"95% CI [{_fmt(eq.get('ci_low'), signed=True)}, "
        f"{_fmt(eq.get('ci_high'), signed=True)}]",
        f"- TOST p = {_fmt(eq.get('p_tost'))}; equivalent at the margin: "
        f"{eq.get('equivalent_at_margin')}",
        f"- interval excludes the smallest effect of interest: "
        f"{eq.get('excludes_smallest_effect')}",
        f"- **verdict: {eq.get('verdict')}**",
        "",
        "A null is informative only when the interval lies inside "
        "(-smallest effect of interest, +smallest effect of interest).",
        "",
        "## 7. Edge cases (reported, never crashed on)",
        "",
    ]
    edge = result.get("edge_cases") or {}
    lines += [
        f"- single-effort players: {edge.get('n_single_effort_players')}",
        f"- single-effort player-games (blocks that cannot shuffle): "
        f"{edge.get('n_single_effort_player_games')}",
        f"- players with zero within-player leverage variance: "
        f"{edge.get('n_players_zero_leverage_variance')} "
        f"({edge.get('zero_variance_rule')})",
        "",
        "## 8. Not wired this cycle",
        "",
    ]
    lines += [f"- {item}" for item in (result.get("not_wired") or [])]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
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

    out = _out_dir(cfg, mode)
    table = load_efforts(cfg, mode)
    primary = load_primary(cfg, mode)
    if not V.validation_block(cfg):
        raise RuntimeError(
            f"STAGE {STAGE!r}: config bdb27_lc.validation is absent; Phase 5 has "
            "nothing declared to run.")

    with C.timed_stage(cfg, mode=mode, stage=STAGE,
                       note="Phase 5 validation of the primary leverage fit") as stage:
        result = V.run_validation(table, cfg, mode, primary=primary)
        stage.note(f"p_player_game={_fmt(result['permutation']['schemes']
                                         .get('player_game', {}).get('p_value'))}")
        stage.note(f"attenuation={_fmt(result['orthogonalisation']
                                       .get('attenuation_fraction'))}")
        stage.note(f"equivalence={result['equivalence'].get('verdict')}")

    nulls = result.pop("_permutation_null", {})
    garbage_rows = result.pop("_garbage_rows", [])
    orth_rows = result.pop("_orth_rows", [])
    result = _jsonable(result)

    C.write_json(out / C.lc(cfg)["validation"].get("summary_filename",
                                                   "validation_result.json"),
                 result, mode=mode, cfg=cfg)
    C.write_csv(out / "permutation_null.csv",
                [{"scheme": scheme, "rep": i, "beta": float(value)}
                 for scheme, values in nulls.items()
                 for i, value in enumerate(np.asarray(values, dtype=float))],
                ["scheme", "rep", "beta"], mode=mode, cfg=cfg)
    C.write_csv(out / "placebo_checks.csv",
                result["placebo"]["checks"], PLACEBO_FIELDS, mode=mode, cfg=cfg)
    C.write_csv(out / "garbage_time.csv", garbage_rows, GARBAGE_FIELDS,
                mode=mode, cfg=cfg)
    C.write_csv(out / "sensitivity_curve.csv",
                result["sensitivity"]["rows"], SENSITIVITY_FIELDS, mode=mode, cfg=cfg)
    C.write_csv(out / "orthogonalisation.csv", orth_rows, ORTH_FIELDS,
                mode=mode, cfg=cfg)
    C.write_markdown(out / "SUMMARY.md", _markdown(result), mode=mode, cfg=cfg)

    C.mark_done(cfg, STAGE, mode, extra={
        "p_player_game": result["permutation"]["schemes"].get(
            "player_game", {}).get("p_value"),
        "attenuation": result["orthogonalisation"].get("attenuation_fraction"),
        "equivalence_verdict": result["equivalence"].get("verdict"),
        "n_efforts": result["sample_sizes"].get("n_efforts"),
    })
    print(f"[{STAGE}] "
          f"p(player_game)={_fmt(result['permutation']['schemes'].get('player_game', {}).get('p_value'))} "
          f"p(route_family)={_fmt(result['permutation']['schemes'].get('route_family', {}).get('p_value'))} "
          f"attenuation={_fmt(result['orthogonalisation'].get('attenuation_fraction'))} "
          f"equivalence={result['equivalence'].get('verdict')} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
