#!/usr/bin/env python3
"""stage_04_models — Phase 4: P1 (ceiling), P2 (load), S1 and E1.

Run order: after ``03_game_snaps``, before ``05_validate``.

    cd src && python -m dl.run 04_models --mode sample
    python src/dl/stage_04_models.py --mode sample --force

What it writes (``notes/dl-design.md`` §5.5)
------------------------------------------
``part1_result.json``    P1 primary + the declared robustness rungs + secondaries
``part2_result.json``    relative vs absolute load, CV, bootstrap, SOI verdict
``cv_folds.csv``         one row per definition x fold (tuning value, log score)
``load_tuning.csv``      every candidate grid value per fold, with its loglik
``mediation.json``       E1, labelled DESCRIPTIVE
``reliability.csv``      a0 ICC and the model-based load-slope reliability
``model_result.json``    the gate verdict and the run-level summary
``SUMMARY.md``           the human summary, provenance first

Honesty rules (the point of this stage)
---------------------------------------
* Sample mode stamps every artefact ``PLACEHOLDER_SAMPLE`` and sets
  ``reportable: false``.  The summary says plainly that no number here may be
  quoted as a result.
* A full run requires ``FULL`` provenance and refuses otherwise.
* Every §15 stopping condition is evaluated and reported as a verdict.  A
  missing required field is never papered over with a reduced model: the
  model is reported as NOT_ESTIMABLE with the field named.
* Every estimated model is reported, including non-converged ones, together
  with the fallback actually used.  No model is dropped for an unfavourable
  estimate and no rung is chosen because it looks better.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402
from dl import dl_models_core as M  # noqa: E402
from dl import stage_00_thresholds as stage00  # noqa: E402

STAGE = "04_models"


# --------------------------------------------------------------------------- #
# Paths / provenance
# --------------------------------------------------------------------------- #
def _out_dir(cfg: Mapping, mode: str) -> Path:
    paths = D.resolve_paths(cfg, mode)
    return paths["outputs"] / str(D.dl(cfg)["models"]["out_subdir"])


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(D.REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def check_provenance(cfg: Mapping, mode: str) -> dict[str, Any]:
    level = D.require_full_provenance(cfg, mode, STAGE)
    D.require_prereg(cfg, mode, STAGE)
    thresholds = stage00._read_existing(D.resolve_paths(cfg, mode)["thresholds"])
    th_level = stage00._provenance_level(thresholds) or "missing"
    if mode == "full":
        if level != "FULL" or th_level != "FULL":
            raise RuntimeError(
                f"STAGE {STAGE!r}: full mode requires FULL provenance for both the "
                f"config and thresholds.json (got {level!r}, {th_level!r}). "
                f"Sample-tuned thresholds can never feed a reportable model.")
        if not stage00.is_full_provenance(thresholds):
            raise RuntimeError(
                f"STAGE {STAGE!r}: thresholds.json is present but not FULL; "
                f"refusing a reportable model run.")
    return {"config_level": level, "thresholds": th_level,
            "reportable": level == "FULL"}


def _existing_full(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    prov = str(payload.get("provenance", ""))
    return prov == "FULL" or prov.startswith("FULL:")


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def load_inputs(cfg: Mapping, mode: str) -> dict[str, pd.DataFrame]:
    paths = D.resolve_paths(cfg, mode)
    ins = D.dl(cfg)["models"]["inputs"]
    snaps_path = paths["outputs"] / str(ins["snaps"])
    ceiling_path = paths["outputs"] / str(ins["ceiling"])
    raw = paths["raw"]
    players_path = raw / str(ins["players"])
    combine_path = raw / str(ins["combine_results"])
    for path in (snaps_path, ceiling_path, players_path, combine_path):
        if not path.exists():
            raise FileNotFoundError(
                f"STAGE {STAGE!r}: required input {_display(path)} is missing. "
                f"Run the upstream stages (or set the fixture config) first.")
    return {
        "snaps": pd.read_csv(snaps_path, low_memory=False),
        "ceiling": pd.read_csv(ceiling_path, low_memory=False),
        "players": pd.read_csv(players_path, low_memory=False),
        "combine": pd.read_csv(combine_path, low_memory=False),
    }


def _audit_decision(cfg: Mapping, mode: str) -> dict | None:
    path = D.resolve_paths(cfg, mode)["outputs"] / "audit" / "decision.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# JSON-safe conversion
# --------------------------------------------------------------------------- #
def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.DataFrame):
        return _clean(value.to_dict(orient="records"))
    return value


# --------------------------------------------------------------------------- #
# Artefacts
# --------------------------------------------------------------------------- #
def _write_artefacts(out_dir: Path, cfg: Mapping, mode: str, payload: dict) -> None:
    for name, obj in payload["json"].items():
        D.write_json(out_dir / name, _clean(obj), mode=mode, cfg=cfg)
    for name, table in payload["csv"].items():
        rows, fields = table
        D.write_csv(out_dir / name, rows, fields, mode=mode, cfg=cfg)
    D.write_markdown(out_dir / "SUMMARY.md", payload["summary"], mode=mode, cfg=cfg)


def _fold_rows(part2: Mapping) -> list[dict]:
    rows = []
    for definition in ("absolute", "relative"):
        block = (part2 or {}).get(definition) or {}
        for fold in block.get("folds", []):
            rows.append({
                "definition": definition,
                "fold": fold.get("fold"),
                "n_train": fold.get("n_train"),
                "n_test": fold.get("n_test"),
                "n_train_players": fold.get("n_train_players"),
                "n_test_players": fold.get("n_test_players"),
                "selected_grid_value": fold.get("selected"),
                "n_scored": fold.get("n_scored"),
                "mean_logscore": fold.get("mean_logscore"),
                "rung_used": fold.get("rung"),
                "status": fold.get("status"),
            })
    return rows


def _tuning_rows(part2: Mapping) -> list[dict]:
    rows = []
    for definition in ("absolute", "relative"):
        block = (part2 or {}).get(definition) or {}
        for fold in block.get("folds", []):
            for cand in fold.get("candidates", []):
                rows.append({
                    "definition": definition,
                    "fold": fold.get("fold"),
                    "candidate_value": cand.get("value"),
                    "training_loglik": cand.get("loglik"),
                    "n_train_obs": cand.get("n_obs"),
                    "status": cand.get("status"),
                    "selected": bool(cand.get("value") == fold.get("selected")),
                    "criterion": "training-fold log likelihood",
                })
    return rows


def _reliability_rows(cfg: Mapping, frame: pd.DataFrame,
                      frame_report: Mapping, part2: Mapping,
                      s1: Mapping | None) -> list[dict]:
    a0_icc = None
    if "a0_icc" in frame and pd.to_numeric(frame["a0_icc"], errors="coerce").notna().any():
        a0_icc = float(pd.to_numeric(frame["a0_icc"], errors="coerce").dropna().iloc[0])
    rows = [{
        "quantity": "a0_icc_combine_test_retest",
        "estimate": a0_icc,
        "threshold": float(cfg["thresholds"]["icc_min"]),
        "definition": "ICC of shrunken a0 (passes through from the ceiling stage)",
        "note": "prereg §13: icc_min = 0.50, never lowered",
    }]
    if s1 and s1.get("reliability") is not None:
        rows.append({
            "quantity": "load_slope_reliability",
            "estimate": float(s1["reliability"]),
            "threshold": None,
            "definition": s1.get("reliability_definition"),
            "note": f"definition={s1.get('definition')}; model-based, not split-half",
        })
    else:
        rows.append({
            "quantity": "load_slope_reliability", "estimate": None,
            "threshold": None,
            "definition": "var(slope)/(var(slope)+mean(SE^2))",
            "note": "not estimable" if not s1 else s1.get("status"),
        })
    return rows


def _derivation_markdown(derivation: Mapping) -> list[str]:
    """The ``## Derived controls`` block (§5.4a) — one row per check."""
    lines = ["## Derived controls\n"]
    lines.append("Source: the all-22 derivation (design §5.4a). These checks "
                 "test the distribution of the four derived columns against the "
                 "declared ranges; they are controls on the derivation, not on "
                 "the model.\n")
    lines.append(f"* status: **{derivation.get('status')}** — "
                 f"{derivation.get('statement')}")
    lines.append("")
    lines.append("| check | status | value | threshold |")
    lines.append("|---|---|---|---|")
    for name, chk in derivation.get("checks", {}).items():
        value = chk.get("value")
        if isinstance(value, (int, float)):
            value_txt = f"{value:g}"
        elif value is None:
            value_txt = "—"
        else:
            value_txt = str(value)
        threshold = chk.get("threshold")
        if isinstance(threshold, (list, tuple)):
            threshold_txt = f"[{float(threshold[0]):g}, {float(threshold[1]):g}]"
        elif isinstance(threshold, (int, float)):
            threshold_txt = f"{threshold:g}"
        else:
            threshold_txt = ""
        lines.append(f"| {name} | {chk.get('status')} | {value_txt} | "
                     f"{threshold_txt} |")
    lines.append("")
    na = derivation.get("checks", {}).get("derivation_na_share") or {}
    na_columns = na.get("columns") or {}
    numeric_shares = {c: s for c, s in na_columns.items()
                      if isinstance(s, (int, float))}
    if numeric_shares:
        lines.append("* NA share by column (expected denominator): " + ", ".join(
            f"`{c}` {share:.3f} ({na.get('non_null', {}).get(c, 0)} non-null)"
            for c, share in numeric_shares.items()))
    elif na_columns:
        # NOT_APPLICABLE (D78): the columns are all-NA by construction, so there
        # is no share to quote — only a status and the non-null count (0).
        lines.append("* per-column status: " + ", ".join(
            f"`{c}` {status} ({na.get('non_null', {}).get(c, 0)} non-null)"
            for c, status in na_columns.items()))
    if na.get("denominator"):
        lines.append(f"* rows evaluated: {na.get('n')} "
                     f"(denominator: `{na['denominator']}`)")
    coverage = na.get("coverage") or {}
    if coverage.get("na_share_all"):
        lines.append(
            "* raw all-row NA share (coverage, not gated): "
            + ", ".join(f"`{c}` {share:.3f}" for c, share in
                        coverage["na_share_all"].items())
            + f" over {coverage.get('n_rows_all')} rows")
    lines.append("")
    reasons: dict[str, list[str]] = {}
    for name in (derivation.get("failing", []) + derivation.get("pending", [])
                 + derivation.get("not_applicable", [])):
        reason = (derivation.get("checks", {}).get(name) or {}).get("reason")
        if reason:
            reasons.setdefault(reason, []).append(name)
    for reason, names in reasons.items():
        lines.append("* " + ", ".join(f"`{n}`" for n in names) + f": {reason}")
    lines.append("")
    return lines


def _summary_markdown(cfg: Mapping, mode: str, prov: Mapping, frame_report: Mapping,
                      field_info: Mapping[str, dict], gate: Mapping,
                      p1: Mapping | None, p2: Mapping | None,
                      s1: Mapping | None, e1: Mapping | None,
                      frame: pd.DataFrame,
                      derivation: Mapping | None = None) -> str:
    lines: list[str] = []
    lines.append("# DL stage 04 — models\n")
    if prov["reportable"]:
        lines.append("**Provenance: FULL.** These numbers come from the full-data "
                     "run and may be reported under the prereg's rules.\n")
    else:
        lines.append("**PROVENANCE: PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE OUTPUT.**\n")
        lines.append("> **No number in this directory may be quoted as a result.** "
                     "The provided game data is a ~100 MB sample; the model frame it "
                     "supports is incomplete by construction. Every figure here is "
                     "either a machinery check or a statement about what the sample "
                     "cannot support. Reportable numbers require `provenance == FULL`.\n")

    lines.append("## Gate verdict\n")
    lines.append(f"**{gate['verdict']}** — §15 stopping conditions evaluated: "
                 f"{len(gate['checks'])}.\n")
    lines.append("| condition | status | value | threshold | clause |")
    lines.append("|---|---|---|---|---|")
    for name, chk in gate["checks"].items():
        value = chk.get("value")
        value_txt = f"{value:g}" if isinstance(value, (int, float)) else (
            ", ".join(chk.get("absent", [])) if chk.get("absent") else "")
        threshold = chk.get("threshold")
        if isinstance(threshold, (list, tuple)):
            threshold_txt = f"[{float(threshold[0]):g}, {float(threshold[1]):g}]"
        elif isinstance(threshold, (int, float)):
            threshold_txt = f"{threshold:g}"
        else:
            threshold_txt = ""
        lines.append(f"| {name} | {chk['status']} | {value_txt} | {threshold_txt} | "
                     f"{chk.get('clause', '')} |")
    lines.append("")

    if derivation is not None:
        lines.extend(_derivation_markdown(derivation))

    lines.append("## Frame\n")
    lines.append(f"* snaps in the snap table: **{frame_report.get('n_snaps')}**, "
                 f"rushers: **{frame_report.get('n_players')}**")
    if len(frame):
        rate = float(pd.to_numeric(frame['event'], errors='coerce').mean())
        lines.append(f"* event rate (pressure): **{rate:.4f}**")
    lines.append("* required-field coverage (§8 controls + keys):\n")
    lines.append("| field | present | non-null | fraction |")
    lines.append("|---|---|---|---|")
    for field, info in field_info.items():
        lines.append(f"| {field} | {info['present']} | {info['non_null']} | "
                     f"{info['fraction']:.3f} |")
    lines.append("")

    partial = [f for f, i in field_info.items()
               if i["present"] and 0 < i["non_null"] < len(frame)]
    if partial:
        lines.append("* partially covered (present on some snaps only): "
                     + ", ".join(f"`{f}` ({field_info[f]['fraction']:.1%})"
                                 for f in partial))
    lines.append("## P1 — Combine ceiling and time to pressure (confirmatory)\n")
    if p1 and p1.get("status") == "OK":
        est = p1.get("estimate") or {}
        lines.append(f"* model: **{p1.get('rung_used')}** "
                     f"({'primary' if p1.get('rung_used') == 'aft_frailty_lognormal' else 'FALLBACK — the primary did not converge'})")
        lines.append(f"* HR per 1 SD `a0`: **{est.get('estimate'):.4f}** "
                     f"(95% CI {est.get('lo'):.4f}–{est.get('hi'):.4f})")
        lines.append(f"* time ratio: {p1.get('time_ratio'):.4f}; "
                     f"n = {p1.get('n_obs')}, events = {p1.get('n_events')} "
                     f"({p1.get('event_rate'):.4f})")
        for rung in p1.get("rungs", []):
            lines.append(f"  * rung `{rung['name']}`: converged={rung['converged']} "
                         f"HR={rung['estimate'].get('estimate')}")
    else:
        lines.append(f"* **NOT ESTIMABLE**: {(p1 or {}).get('reason')}")
        absent = [f for f, i in field_info.items()
                  if (not i["present"]) or i["non_null"] == 0]
        if absent:
            lines.append(f"* fields with no usable value: `{'`, `'.join(absent)}`")
            lines.append("* no reduced model was substituted: the §8 control list is "
                         "part of the specification, and dropping controls to make the "
                         "sample fit would change the estimand.")
    lines.append("")

    lines.append("## P2 — relative vs absolute load (confirmatory)\n")
    if p2 and p2.get("status") == "OK":
        lines.append(f"* mean held-out log score: absolute "
                     f"{p2['absolute']['mean_logscore']:.5f}, relative "
                     f"{p2['relative']['mean_logscore']:.5f} nats/snap")
        lines.append(f"* difference (relative − absolute): "
                     f"**{p2['difference_nats_per_snap']:.5f}** "
                     f"(95% CI {p2['difference_interval'][0]:.5f} to "
                     f"{p2['difference_interval'][1]:.5f}); SOI {p2['soi']}, "
                     f"equivalence margin {p2['equivalence_margin']}")
        lines.append(f"* verdict: **{p2['verdict']}** (a null is informative only if "
                     f"the interval excludes the SOI)")
        lines.append(f"* folds: {p2['fold_info'].get('folds')} "
                     f"(requested {p2['fold_info'].get('requested')}, "
                     f"reduced={p2['fold_info'].get('reduced')})")
    else:
        lines.append(f"* **NOT ESTIMABLE**: {(p2 or {}).get('reason')}")
        lines.append("* Part 2 is dropped if snap coverage fails; P1 continues "
                     "(prereg §15). The load columns are `NA` in the sample snap "
                     "table because per-game tracking sequences are incomplete.")
    lines.append("")

    lines.append("## S1 — population load decline (secondary)\n")
    if s1 and s1.get("status") == "OK":
        na = s1["naive"]
        lines.append(f"* definition: `{s1.get('definition')}`; load coefficient "
                     f"{na['load_coef']:.4f} (SE {na['load_se']:.4f}), "
                     f"load×a0 interaction {na['interaction_coef']:.4f} "
                     f"(SE {na['interaction_se']:.4f})")
        corr = s1.get("corrected") or {}
        if corr.get("interaction_coef") is not None:
            lines.append(f"* measurement-error corrected interaction: "
                         f"{corr['interaction_coef']:.4f} "
                         f"(SD across {corr['n_draws']} posterior draws "
                         f"{corr.get('interaction_sd')})")
        lines.append(f"* slope reliability: {s1.get('reliability')}")
    else:
        lines.append(f"* **NOT ESTIMABLE**: {(s1 or {}).get('reason', 'Part 2 did not run')}")
    lines.append("")

    lines.append("## E1 — decomposition through get-off (EXPLORATORY, DESCRIPTIVE)\n")
    if e1 and e1.get("status") == "OK":
        lines.append("* This is a descriptive product-of-coefficients decomposition, "
                     "not a causal mediation claim.")
        lines.append(f"* alpha (a0 → get-off): {e1['alpha_a0_to_getoff']:.5f}; "
                     f"beta (get-off → hazard): {e1['beta_getoff_to_hazard']:.5f}; "
                     f"product {e1['indirect_product']:.5f} "
                     f"(95% {e1['interval'][0]:.5f} to {e1['interval'][1]:.5f})")
    else:
        lines.append(f"* **NOT RUN / NOT ESTIMABLE**: {(e1 or {}).get('reason', 'no frame')}")
    lines.append("")

    lines.append("## Claim hierarchy\n")
    lines.append("* **Confirmatory:** P1 (one test) and P2 (one test).")
    lines.append("* **Secondary:** S1 (its own interval), P1's two secondary outcomes.")
    lines.append("* **Exploratory / descriptive:** E1, every robustness rung's "
                 "interpretation, and anything not marked confirmatory above.")
    lines.append("")
    lines.append("## What this stage did not do\n")
    lines.append("* It did not change the exposure, outcome, censoring rule, control "
                 "list, grid sizes, CV scheme, metric, SOI or any threshold.")
    lines.append("* It did not tune anything outside a training fold, and did not "
                 "select a load definition by within-sample fit.")
    lines.append("* It did not drop a model for an unfavourable estimate.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(cfg: Mapping, mode: str, force: bool = False) -> dict[str, Any]:
    out_dir = _out_dir(cfg, mode)
    result_path = out_dir / "model_result.json"
    if mode == "sample" and _existing_full(result_path):
        raise RuntimeError(
            f"STAGE {STAGE!r}: refusing to run — {result_path} already carries FULL "
            f"provenance. A sample run must never overwrite a full-run model.")
    prov = check_provenance(cfg, mode)
    if D.is_done(cfg, STAGE, mode, force=force):
        return {"skipped": True, "path": out_dir}

    D.assert_disk_space(cfg, mode)
    block = D.dl(cfg)          # the model core reads the bdb27_dl block only
    inputs = load_inputs(cfg, mode)
    frame, frame_report = M.build_frame(inputs["snaps"], inputs["ceiling"],
                                        inputs["players"], inputs["combine"])
    fields = M.required_fields(block)
    field_info = M.field_availability(frame, fields)

    # Load panel (§7).  Built for every row; columns stay NA where the snap has
    # no measured in-game speed.
    panel = M.build_load_panel(frame, block)
    frame = pd.concat([frame, panel], axis=1)

    p1 = M.fit_p1(frame, block)
    p2 = M.run_part2(frame, block)

    better = None
    if p2.get("status") == "OK":
        better = p2.get("better_definition")
    s1 = None
    if better:
        # The population estimate is taken at the definition's most frequently
        # selected fold value (or the first grid point if none was selected).
        chosen = _modal_selected(p2, better) or p2["grids"][better][0]
        column = ("absolute_load_" if better == "absolute" else "relative_load_") + \
            f"{float(chosen):.1f}".replace(".", "p")
        s1 = M.fit_s1(M.prepare_frame(frame, block), block, load_column=column)
    e1 = M.fit_e1(M.prepare_frame(frame, block), block)

    dgate = M.derivation_gate(block, frame, mode)
    gate = M.gate_evaluate(block, mode=mode, field_info=field_info, frame=frame,
                           p1=p1, p2=p2, audit_decision=_audit_decision(cfg, mode),
                           derivation=dgate)

    reliability = _reliability_rows(block, frame, frame_report, p2, s1)
    part1_out = {k: v for k, v in p1.items() if k != "secondary"}
    part1_out["secondary"] = p1.get("secondary", {})
    part2_out = {k: v for k, v in p2.items() if k not in ("absolute", "relative")}
    part2_out["folds"] = _fold_rows(p2)
    part2_out["mean_logscore"] = {
        "absolute": (p2.get("absolute") or {}).get("mean_logscore"),
        "relative": (p2.get("relative") or {}).get("mean_logscore"),
    }

    model_result = {
        "stage": STAGE, "mode": mode,
        "reportable": bool(prov["reportable"]),
        "config_provenance": prov["config_level"],
        "thresholds_provenance": prov["thresholds"],
        "no_number_may_be_quoted": not prov["reportable"],
        "statement": ("Sample mode: every artefact is PLACEHOLDER_SAMPLE and no "
                      "number here may be quoted as a result."
                      if not prov["reportable"] else
                      "Full run: numbers are reportable under the prereg."),
        "gate": gate,
        "derivation": dgate,
        "frame": {k: v for k, v in frame_report.items() if k != "columns"},
        "field_availability": field_info,
        "missing_required_fields": [f for f, i in field_info.items()
                                    if (not i["present"]) or i["non_null"] == 0],
        "part1_status": p1.get("status"),
        "part1_rung_used": p1.get("rung_used"),
        "part2_status": p2.get("status"),
        "part2_verdict": p2.get("verdict"),
        "s1_status": (s1 or {}).get("status"),
        "e1_status": (e1 or {}).get("status"),
        "estimators": {
            "p1_primary": block["models"]["primary_estimator"],
            "quadrature_nodes": block["models"]["frailty_quadrature_nodes"],
            "rungs": block["models"]["rungs"],
        },
        # §8 as amended in D78: what enters the model, and what the product
        # cannot supply.  Reported so the control list is auditable from the
        # artefact, not inferred from the source.
        "controls": {
            "primary": list(block["models"]["controls"]),
            "fixed_effects": list(block["models"].get("crossed_fixed_effects", [])),
            "sensitivity_panel": list(
                block["models"].get("sensitivity_controls", [])),
            "tracking_product": str(block["game"]["tracking_product"]),
            "removed_unobservable": ["blitz", "n_blockers",
                                     "primary_blocker_group"],
            "amendments": "prereg §8 (D78, 2026-11-02): team-level rush and "
                          "protection counts are unobservable on a prospect-only "
                          "tracking product",
        },
    }

    summary = _summary_markdown(block, mode, prov, frame_report, field_info, gate,
                                p1, p2, s1, e1, frame, derivation=dgate)
    payload = {
        "json": {
            "part1_result.json": part1_out,
            "part2_result.json": part2_out,
            "mediation.json": e1 or {"status": "NOT_RUN"},
            "model_result.json": model_result,
        },
        "csv": {
            "cv_folds.csv": (_fold_rows(p2), [
                "definition", "fold", "n_train", "n_test", "n_train_players",
                "n_test_players", "selected_grid_value", "n_scored",
                "mean_logscore", "rung_used", "status"]),
            "load_tuning.csv": (_tuning_rows(p2), [
                "definition", "fold", "candidate_value", "training_loglik",
                "n_train_obs", "status", "selected", "criterion"]),
            "reliability.csv": (reliability, [
                "quantity", "estimate", "threshold", "definition", "note"]),
        },
        "summary": summary,
    }
    _write_artefacts(out_dir, cfg, mode, payload)
    D.mark_done(cfg, STAGE, mode, extra={
        "gate": gate["verdict"], "part1": p1.get("status"),
        "part2": p2.get("status"), "reportable": prov["reportable"]})
    return {"skipped": False, "path": out_dir, "gate": gate, "derivation": dgate,
            "p1": p1, "p2": p2, "s1": s1, "e1": e1,
            "frame_report": frame_report}


def _modal_selected(part2: Mapping, definition: str) -> float | None:
    block = (part2 or {}).get(definition) or {}
    values = [f.get("selected") for f in block.get("folds", [])
              if f.get("selected") is not None]
    if not values:
        return None
    return max(sorted(set(values)), key=values.count)


def main(argv=None) -> int:
    args = D.parse_args(argv)
    cfg = D.load_config(args.config)
    D.seed_everything(int(D.dl(cfg)["seeds"]["base"]))
    try:
        with D.timed_stage(cfg, STAGE, args.mode) as st:
            outcome = run(cfg, args.mode, force=args.force)
            if outcome.get("skipped"):
                st.note("skipped: checkpoint current")
                print(f"{STAGE}: up to date (use --force to rebuild)")
                return 0
            gate = outcome["gate"]
            p1, p2 = outcome["p1"], outcome["p2"]
            st.note(f"gate={gate['verdict']} p1={p1.get('status')} "
                    f"p2={p2.get('status')}")
            print(f"{STAGE}: wrote {outcome['path']}")
            print(f"  gate: {gate['verdict']}  failing={gate['failing']}  "
                  f"pending={gate['pending']}")
            print(f"  P1: {p1.get('status')}  rung={p1.get('rung_used')}")
            print(f"  P2: {p2.get('status')}  verdict={p2.get('verdict')}")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
