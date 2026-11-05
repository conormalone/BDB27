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
from dl import dl_idr_models as IDR  # noqa: E402
from dl import dl_idr_part2 as IDR2  # noqa: E402
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


def _write_idr_artefacts(out_dir: Path, cfg: Mapping, mode: str,
                         block: Mapping, idr: Mapping,
                         p2_idr: Mapping | None = None) -> None:
    """IDR Part-1 artefacts, in their own subdirectory (config: out_subdir)."""
    subdir = str((block.get("idr_part1") or {}).get("out_subdir", "idr"))
    target = out_dir / subdir
    D.write_json(target / "part1_idr.json", _clean(idr), mode=mode, cfg=cfg)
    D.write_csv(target / "idr_windows.csv", _idr_window_rows(idr), [
        "window_snaps", "is_primary", "status", "reason", "rung_used",
        "n_snaps", "n_players", "n_games", "coef_std", "se_std",
        "ci_low_std", "ci_high_std", "p", "coef_raw", "se_raw", "cluster",
        "se_iid", "design_effect"], mode=mode, cfg=cfg)
    D.write_csv(target / "idr_terms.csv", _idr_term_rows(idr), [
        "window_snaps", "term", "coef_std", "se_std", "p_std", "coef_raw",
        "se_raw", "ci_low_raw", "ci_high_raw", "raw_unit", "is_interaction",
        "inference"], mode=mode, cfg=cfg)
    if p2_idr is not None:
        D.write_json(target / "part2_idr.json", _clean(p2_idr), mode=mode, cfg=cfg)
        D.write_csv(target / "part2_idr_folds.csv", _idr_part2_fold_rows(p2_idr), [
            "arm", "fold", "n_train", "n_test", "n_train_players",
            "n_test_players", "selected_grid_value", "n_scored",
            "mean_logscore", "rung_used", "status"], mode=mode, cfg=cfg)
        D.write_csv(target / "part2_idr_tuning.csv", _idr_part2_tuning_rows(p2_idr), [
            "arm", "fold", "candidate_value", "training_loglik", "n_train_obs",
            "status", "selected", "criterion"], mode=mode, cfg=cfg)


def _idr_part2_fold_rows(p2_idr: Mapping) -> list[dict]:
    rows = []
    for arm in ("deficit", "absolute"):
        block = (p2_idr or {}).get(arm) or {}
        for fold in block.get("folds", []):
            rows.append({
                "arm": arm,
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


def _idr_part2_tuning_rows(p2_idr: Mapping) -> list[dict]:
    rows = []
    for arm in ("deficit", "absolute"):
        block = (p2_idr or {}).get(arm) or {}
        for fold in block.get("folds", []):
            for cand in fold.get("candidates", []):
                rows.append({
                    "arm": arm,
                    "fold": fold.get("fold"),
                    "candidate_value": cand.get("value"),
                    "training_loglik": cand.get("loglik"),
                    "n_train_obs": cand.get("n_obs"),
                    "status": cand.get("status"),
                    "selected": bool(cand.get("value") == fold.get("selected")),
                    "criterion": "training-fold log likelihood",
                })
    return rows


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


_PART2_REASON_KEYS = (
    ("part2_reason_absolute", "absolute"),
    ("part2_reason_relative", "relative"),
    ("part2_reason_comparison", "comparison"),
)


def _fmt_score(value: Any) -> str:
    """Short display string for a log score (``NA`` when absent/non-finite)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(number):
        return "NA"
    return f"{number:.3g}"


def _reason_markdown(p2: Mapping | None) -> list[str]:
    """Render the Part-2 reason records (code + sentence) for SUMMARY.md.

    Tolerant of older artefacts: a payload without the three reason keys, or
    with a malformed record, renders nothing rather than raising.
    """
    if not isinstance(p2, Mapping):
        return []
    lines: list[str] = []
    for key, label in _PART2_REASON_KEYS:
        record = p2.get(key)
        if not isinstance(record, Mapping):
            continue
        code = " ".join(str(record.get("code") or "UNKNOWN").split())
        message = " ".join(str(record.get("message") or "").split())
        lines.append(f"* {label} reason (`{code}`): {message}")
    return lines


def _idr_window_rows(idr: Mapping) -> list[dict]:
    """One row per fitted window (primary + sensitivity panel)."""
    rows = []
    for entry in (idr or {}).get("panel", []):
        inter = entry.get("interaction") or {}
        rows.append({
            "window_snaps": entry.get("window"),
            "is_primary": bool(entry.get("window") == (idr or {}).get("primary_window")),
            "status": entry.get("status"),
            "reason": entry.get("reason"),
            "rung_used": entry.get("rung_used"),
            "n_snaps": entry.get("n_snaps"),
            "n_players": entry.get("n_players"),
            "n_games": entry.get("n_games"),
            "coef_std": inter.get("coef_std"),
            "se_std": inter.get("se_std"),
            "ci_low_std": inter.get("ci_low_std"),
            "ci_high_std": inter.get("ci_high_std"),
            "p": inter.get("p"),
            "coef_raw": inter.get("coef_raw"),
            "se_raw": inter.get("se_raw"),
            "cluster": inter.get("cluster"),
            "se_iid": entry.get("se_iid"),
            "design_effect": entry.get("design_effect"),
        })
    return rows


def _idr_term_rows(idr: Mapping) -> list[dict]:
    """Fixed-effect table of the PRIMARY window (standardised and raw scale)."""
    rows = []
    primary = (idr or {}).get("primary") or {}
    for term in primary.get("terms", []):
        rows.append({
            "window_snaps": primary.get("window"),
            "term": term.get("term"),
            "coef_std": term.get("coef_std"),
            "se_std": term.get("se_std"),
            "p_std": term.get("p_std"),
            "coef_raw": term.get("coef_raw"),
            "se_raw": term.get("se_raw"),
            "ci_low_raw": term.get("ci_low_raw"),
            "ci_high_raw": term.get("ci_high_raw"),
            "raw_unit": term.get("raw_unit"),
            "is_interaction": term.get("is_interaction"),
            "inference": "player-clustered (CR1)",
        })
    return rows


def _idr_markdown(idr: Mapping | None, prov: Mapping) -> list[str]:
    """The confirmatory Part 1 (IDR spec) block."""
    lines = ["## P1 (IDR) — rolling load × inter-snap rest → per-snap deficit "
             "(confirmatory)\n"]
    if not idr:
        lines.append("* **NOT RUN**: no `idr_part1` result was produced by this "
                     "invocation.\n")
        return lines
    lines.append(f"* estimand: **{idr.get('estimand')}**")
    lines.append(f"* outcome: `{idr.get('outcome')}`"
                 " (`deficit_ratio = a0_snap / baseline_a0`, below 1 = deficit)")
    lines.append(f"* primary load window: **{idr.get('primary_window')}** prior "
                 f"snaps; sensitivity panel "
                 f"{idr.get('sensitivity_windows')}")
    lines.append(f"* window choice: {idr.get('window_choice')}")
    lines.append("")
    if not prov["reportable"]:
        lines.append("> **UNVALIDATED SAMPLE OUTPUT — NOT A FINDING.** The "
                     "estimates below are a machinery check on the provided "
                     "sample; the Phase-3 deficit only reaches a few hundred "
                     "rows here, which is a coverage artefact of the ~100 MB "
                     "sample. Nothing in this section may be quoted.\n")
    primary = idr.get("primary") or {}
    if primary.get("status") != "OK":
        lines.append(f"* **NOT ESTIMABLE**: {primary.get('reason')}")
        absent = ((primary.get("frame") or {}).get("n_complete"))
        if absent is not None:
            lines.append(f"* complete rows for window {primary.get('window')}: "
                         f"{absent}")
        lines.append("* the interaction was **not** dropped to force a fit; no "
                     "reduced specification was substituted.")
        lines.append("")
    else:
        inter = primary.get("interaction") or {}
        lines.append(f"* model: mixed effects on `log(deficit_ratio)`; random "
                     f"intercept by rusher and a random slope on load")
        lines.append(f"* rung used: **`{primary.get('rung_used')}`** "
                     f"(declared ladder "
                     f"{' → '.join(primary.get('rungs_intended', []))})")
        for rung in primary.get("ladder", []):
            detail = (f", optimiser `{rung.get('optimiser')}`"
                      if rung.get("optimiser") else "")
            reason = f" — {rung['reason']}" if rung.get("reason") else ""
            lines.append(f"  * `{rung['rung']}`: {rung['status']}{detail}{reason}")
        lines.append("")
        lines.append(
            f"* **load × rest interaction**: {inter.get('coef_std'):.5f} per "
            f"(SD load × SD rest); player-clustered 95% CI "
            f"{inter.get('ci_low_std'):.5f} to {inter.get('ci_high_std'):.5f}; "
            f"p = {inter.get('p'):.4g}; n_snaps = {primary.get('n_snaps')}, "
            f"n_players = {primary.get('n_players')}")
        lines.append(
            f"* raw scale: {inter.get('coef_raw'):.3e} "
            f"(SE {inter.get('se_raw'):.3e}) {inter.get('raw_unit')}")
        contrast = primary.get("contrast") or {}
        lines.append(
            f"* implied contrast: change in log-deficit per +1 SD load is "
            f"{contrast.get('log_deficit_per_sd_load_at_low_rest'):.5f} at "
            f"-1 SD rest and "
            f"{contrast.get('log_deficit_per_sd_load_at_high_rest'):.5f} at "
            f"+1 SD rest (difference {contrast.get('difference'):.5f}). "
            f"{contrast.get('interpretation')}")
        lines.append(
            f"* clustering: player-clustered SE is "
            f"{primary.get('cluster', {}).get('player', {}).get('se'):.5f}; "
            f"naive i.i.d. SE is {primary.get('se_iid'):.5f} "
            f"(design effect {primary.get('design_effect'):.2f}). The i.i.d. "
            f"SE is a diagnostic only and is never used for inference.")
        game_block = primary.get("cluster", {}).get("game", {})
        if game_block.get("status") == "OK":
            lines.append(f"* game-clustered SE: {game_block.get('se'):.5f} "
                         f"over {game_block.get('n_clusters')} games")
        else:
            lines.append(f"* game-clustered SE: {game_block.get('status')} — "
                         f"{game_block.get('reason')}")
        controls = primary.get("controls") or {}
        if controls:
            lines.append("* controls: " + ", ".join(
                f"`{name}` {info.get('status')}"
                + (f" ({info.get('coverage'):.1%} non-null)" if info.get("coverage") else "")
                for name, info in controls.items()))
        lines.append("")
    lines.append("### Load-window sensitivity (declared panel, same code path)\n")
    lines.append("| window | status | rung | n snaps | n players | coef (per SD pair) | 95% CI | p |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in _idr_window_rows(idr):
        if row["status"] == "OK":
            lines.append(
                f"| {row['window_snaps']} | OK | `{row['rung_used']}` | "
                f"{row['n_snaps']} | {row['n_players']} | "
                f"{row['coef_std']:.5f} | {row['ci_low_std']:.5f} to "
                f"{row['ci_high_std']:.5f} | {row['p']:.4g} |")
        else:
            lines.append(f"| {row['window_snaps']} | {row['status']} | — | "
                         f"{row['n_snaps'] or '—'} | "
                         f"{row['n_players'] or '—'} | — | — | — |")
    lines.append("")
    failed = [r for r in (idr.get("panel") or [])
              if r.get("status") != "OK" and r.get("reason")]
    for entry in failed:
        lines.append(f"* window {entry.get('window')} reason: "
                     f"{entry.get('reason')}")
    if failed:
        lines.append("")
    return lines


def _idr_part2_markdown(p2: Mapping | None) -> list[str]:
    """The binding IDR Part 2: dynamic deficit vs absolute cumulative load."""
    lines = ["## P2 (IDR) — dynamic deficit vs absolute cumulative load "
             "(confirmatory)\n"]
    if not isinstance(p2, Mapping):
        lines.append("* **NOT RUN** — no IDR Part 2 payload was produced.")
        lines.append("")
        return lines
    controls = p2.get("controls") or {}
    blitz = controls.get("blitzing") or {}
    lines.append("* model: Weibull AFT, log-normal frailty by rusher; "
                 "crossed effects "
                 + ", ".join(f"`{c}`" for c in controls.get(
                     "crossed_fixed_effects", []))
                 + "; identical fixed controls for both arms, including the "
                 "per-player `blitzing` flag")
    lines.append(f"* exposures: deficit `{p2['exposures']['deficit']}` vs "
                 f"absolute `{p2['exposures']['absolute']}` "
                 f"(threshold {p2.get('absolute_threshold_yds')} yd/s, the "
                 "a-priori midpoint of the declared speed grid)")
    lines.append(f"* tuning freedom: **equal and degenerate** — one fixed "
                 f"candidate per arm (`grid_budget: {p2.get('grid_budget')}`); "
                 "neither arm can win through tuning")
    if p2.get("status") == "OK":
        lines.append(f"* common estimation sample: **{p2.get('n_snaps')} snaps**, "
                     f"{p2.get('n_players')} rushers, {p2.get('n_events')} "
                     f"pressure events; {p2.get('fold_info', {}).get('folds')} "
                     "player-grouped folds")
        mean = p2.get("mean_logscore") or {}
        lines.append(f"* mean held-out log score: deficit "
                     f"{_fmt_score(mean.get('deficit'))}, absolute "
                     f"{_fmt_score(mean.get('absolute'))} nats/snap")
        lo, hi = p2.get("difference_interval") or [None, None]
        lines.append(f"* difference (deficit − absolute): "
                     f"**{p2['difference_nats_per_snap']:.5f}** "
                     f"(95% CI {lo:.5f} to {hi:.5f}); smallest effect of "
                     f"interest {p2.get('soi')}")
        lines.append(f"* exceeds the prereg smallest effect: "
                     f"**{p2.get('exceeds_soi')}**; interval excludes zero: "
                     f"**{p2.get('excludes_zero')}**")
        lines.append(f"* verdict: **{p2.get('verdict')}**")
    else:
        lines.append(f"* **NOT ESTIMABLE**: {p2.get('reason')}")
    if blitz:
        counts = blitz.get("counts") or {}
        detail = (f"True {counts.get('True')}, False {counts.get('False')}, "
                  f"NA {counts.get('NA')}")
        if blitz.get("material_na"):
            detail += f" (NA share {blitz.get('na_share'):.3f})"
        lines.append(f"* `blitzing` on the modelled snap set: {detail}")
        if blitz.get("status") == "COLUMN_ABSENT":
            lines.append("  * **column absent** from the snap table; the "
                         "control could not enter the design and the artefact "
                         "records that.")
        elif not blitz.get("identified"):
            lines.append("  * only one observed level on this snap set, so the "
                         "dummy is not identified and adds no design column.")
    lines.append("")
    for key, label in (("reason_deficit", "deficit"),
                       ("reason_absolute", "absolute"),
                       ("reason_comparison", "comparison")):
        record = p2.get(key)
        if isinstance(record, Mapping):
            lines.append(f"* {label} reason (`{record.get('code')}`): "
                         f"{record.get('message')}")
    if any(p2.get(f"reason_{k}") for k in ("deficit", "absolute", "comparison")):
        lines.append("")
    return lines


def _summary_markdown(cfg: Mapping, mode: str, prov: Mapping, frame_report: Mapping,
                      field_info: Mapping[str, dict], gate: Mapping,
                      p1: Mapping | None, p2: Mapping | None,
                      s1: Mapping | None, e1: Mapping | None,
                      frame: pd.DataFrame,
                      derivation: Mapping | None = None,
                      idr: Mapping | None = None,
                      p2_idr: Mapping | None = None) -> str:
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
    lines.extend(_idr_markdown(idr, prov))
    lines.extend(_idr_part2_markdown(p2_idr))

    lines.append("## P1 — Combine ceiling and time to pressure — "
                 "**SUPERSEDED** by the IDR spec (2026-11-04)\n")
    lines.append("> **SUPERSEDED — kept for provenance only.** The binding "
                 "`TASK.md` replaced the a0 → pressure hypothesis with the IDR "
                 "spec (D80): a0 is now a *baseline*, not a predictor. The "
                 "numbers below are historical, are **not** a result of the "
                 "current task, and must not be quoted. The confirmatory Part 1 "
                 "is the IDR section above.\n")
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

    lines.append("## P2 (legacy) — relative vs absolute load — **SUPERSEDED**\n")
    lines.append("> **SUPERSEDED — kept for provenance only.** The binding "
                 "`TASK.md` (2026-11-04) defines Part 2 as the time-to-pressure "
                 "survival comparison above: dynamic deficit vs absolute "
                 "cumulative load. The relative-vs-absolute comparison below "
                 "belongs to the retired spec; its code and "
                 "`part2_result.json` are kept, its numbers are **not** a result "
                 "of the current task and must not be quoted.\n")
    if p2 and p2.get("status") == "OK":
        lines.append(f"* mean held-out log score: absolute "
                     f"{_fmt_score(p2['absolute']['mean_logscore'])}, relative "
                     f"{_fmt_score(p2['relative']['mean_logscore'])} nats/snap")
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
                     "(prereg §15).")
        if not prov["reportable"]:
            lines.append("* Sample mode: the load columns are `NA` where the "
                         "sample's per-game tracking sequences are incomplete. "
                         "That is a property of this sample, not of the full data.")
    lines.extend(_reason_markdown(p2))
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
    lines.append("* **Confirmatory:** P1 (IDR) — the load × rest interaction on "
                 "the per-snap deficit, one test — and P2 (IDR) — the dynamic "
                 "deficit vs absolute cumulative load held-out comparison, one "
                 "test.")
    lines.append("* **SUPERSEDED, not a claim:** the legacy relative-vs-absolute "
                 "P2 numbers (kept in `part2_result.json` for provenance).")
    lines.append("* **SUPERSEDED, not a claim:** the old a0 → pressure P1. Its "
                 "numbers are historical provenance only.")
    lines.append("* **Secondary:** S1 (its own interval).")
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
    p2 = M.run_part2(
        frame, block,
        # wording only: a FULL run's reason sentences may not defer to the
        # sample or to a larger run
        provenance=("FULL" if prov["reportable"] else prov["config_level"]))

    # Phase 4 Part 1 under the binding IDR spec (TASK.md, 2026-11-04): the
    # confirmatory estimand is the rolling-load x rest interaction on the
    # per-snap deficit.  Independent of P1/P2 above; it reads the Phase-3
    # columns the snap table already carries.
    idr = IDR.run_idr(frame, block.get("idr_part1"),
                      mode=("full" if prov["reportable"] else mode))
    p2_idr = IDR2.run_idr_part2(
        frame, block,
        provenance=("FULL" if prov["reportable"] else prov["config_level"]))

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
        # IDR spec (2026-11-04): the confirmatory Part 1.  Additive key; the
        # old `part1_*` keys above are retained for provenance only and are
        # marked SUPERSEDED in SUMMARY.md.
        "part1_idr": {
            "estimand": idr.get("estimand"),
            "status": (idr.get("primary") or {}).get("status"),
            "rung_used": (idr.get("primary") or {}).get("rung_used"),
            "primary_window": idr.get("primary_window"),
            "sensitivity_windows": idr.get("sensitivity_windows"),
            "interaction": (idr.get("primary") or {}).get("interaction"),
            "n_snaps": (idr.get("primary") or {}).get("n_snaps"),
            "n_players": (idr.get("primary") or {}).get("n_players"),
            "reason": (idr.get("primary") or {}).get("reason"),
            "supersedes": "part1_result.json (a0 -> pressure, TASK.md superseded)",
        },
        "part2_status": p2.get("status"),
        "part2_verdict": p2.get("verdict"),
        # IDR spec (2026-11-04): the confirmatory Part 2 comparison.
        "part2_idr_status": p2_idr.get("status"),
        "part2_idr_verdict": p2_idr.get("verdict"),
        "part2_idr_difference": p2_idr.get("difference_nats_per_snap"),
        "part2_idr_interval": p2_idr.get("difference_interval"),
        "part2_idr_blitz": (p2_idr.get("controls") or {}).get("blitz"),
        "supersedes_part2": "part2_result.json (relative vs absolute, TASK.md superseded)",
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
                                p1, p2, s1, e1, frame, derivation=dgate,
                                idr=idr, p2_idr=p2_idr)
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
    _write_idr_artefacts(out_dir, cfg, mode, block, idr, p2_idr)
    D.mark_done(cfg, STAGE, mode, extra={
        "gate": gate["verdict"], "part1": p1.get("status"),
        "part2": p2.get("status"),
        "part1_idr": (idr.get("primary") or {}).get("status"),
        "part2_idr": p2_idr.get("status"),
        "part2_idr_verdict": p2_idr.get("verdict"),
        "reportable": prov["reportable"]})
    return {"skipped": False, "path": out_dir, "gate": gate, "derivation": dgate,
            "p1": p1, "p2": p2, "s1": s1, "e1": e1, "idr": idr,
            "p2_idr": p2_idr,
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
            idr_primary = (outcome.get("idr") or {}).get("primary") or {}
            st.note(f"gate={gate['verdict']} p1={p1.get('status')} "
                    f"p1_idr={idr_primary.get('status')} "
                    f"p2={p2.get('status')}")
            print(f"{STAGE}: wrote {outcome['path']}")
            print(f"  gate: {gate['verdict']}  failing={gate['failing']}  "
                  f"pending={gate['pending']}")
            print(f"  P1: {p1.get('status')}  rung={p1.get('rung_used')}")
            print(f"  P1-IDR: {idr_primary.get('status')}  "
                  f"rung={idr_primary.get('rung_used')}  "
                  f"window={(outcome.get('idr') or {}).get('primary_window')}")
            print(f"  P2: {p2.get('status')}  verdict={p2.get('verdict')}")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
