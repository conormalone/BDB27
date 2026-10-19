#!/usr/bin/env python3
"""06_security.py — Phase 4: security exposures + the early Analysis-A gate (BDB27 stakes task).

Two things, one stage:

1. **Security exposure table** (``outputs/security/security_exposure.parquet`` + ``.csv``). One row
   per board player (joined on ``nfl_id``): ``log(EDP)`` at the **1-week-pre** snapshot (the primary
   exposure), the EDP trend (1wk-pre − 2wk-pre), the consensus share ``n/n_total``, and the
   **drill-participation pattern** (kept separate from security). Exposures are **pre-Combine only**
   — ``Combine Week`` and the post-Combine snapshots are never exposures. Schema is asserted on write.

2. **Analysis A (the early gate)** — the spec's Phase-5A: regress the pre→post-Combine EDP change on
   the shrunken Combine speed, **interacted with** pre-Combine EDP, to test whether the market
   rewarded speed **more for at-risk players**. This uses the board + the Phase-2 Combine envelope
   ONLY (no game data), so it runs for real on this Pi. Its result is what decides whether the
   *security* story can be the paper's primary framing (TASK.md "Headline gate").

Discipline (TASK.md): config-driven (no magic numbers); never load full CSVs into pandas (the
Combine side is small and read via DuckDB); wall time + peak RAM to ``outputs/run_log.csv``;
checkpoint + skip unless ``--force``; sample outputs labelled ``UNVALIDATED SAMPLE OUTPUT``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import gate
import security_features as sf

PENDING = "PENDING FULL RUN"


# --------------------------------------------------------------------------- #
# paths / inputs
# --------------------------------------------------------------------------- #
def security_dir(cfg: dict) -> Path:
    return common.resolve(cfg, "outputs", cfg["security"]["out_subdir"])


def envelope_path(cfg: dict) -> Path:
    return common.resolve(cfg, "outputs", cfg["envelope"]["out_subdir"]) / "per_player_envelope.csv"


def synthetic_root(cfg: dict, regime: str) -> Path:
    return common.resolve(cfg, "fixtures", "synthetic/security") / regime


def load_board_con(con, cfg: dict, synthetic: bool, sec_regime: str, log):
    """Resolve + read the board (real file or synthetic fixture); return (rows, testing_days, drills, meta)."""
    if synthetic:
        root = synthetic_root(cfg, sec_regime)
        board = root / "board.csv"
        if not board.exists():
            raise FileNotFoundError(f"synthetic security fixture missing at {board}; run "
                                    f"`python src/synthetic_security_fixture.py` first")
        rows = sf.load_board_rows(con, cfg, board)
        testing = _read_testing(root / "combine_testing.csv")
        drills = _read_drills(root / "combine_drills.csv")
        return rows, testing, drills, {"source": f"synthetic fixture ({sec_regime})"}
    board = sf.board_path(cfg)
    if not board.exists():
        raise FileNotFoundError(f"board file missing at {board} -> notes/blockers.md")
    meta = sf.verify_board(cfg, board, log)
    if not meta.get("permitted"):
        raise SystemExit("board data not permitted (external.board.permitted=false) -> notes/blockers.md")
    meta["source"] = str(board)
    rows = sf.load_board_rows(con, cfg, board)
    ct = None
    parq = common.resolve(cfg, "parquet")
    ct_pq = parq / (Path(cfg["files"]["combine_tracking"]).stem + ".parquet")
    if ct_pq.exists():
        ct = ct_pq
    else:
        ct = common.to_parquet(con, common.csv_path(cfg, "combine_tracking"), ct_pq, logger=log)
    testing = sf.combine_testing_days(con, ct)
    drills = sf.combine_drills_by_player(con, ct, cfg)
    return rows, testing, drills, meta


def _read_testing(path: Path) -> dict:
    from datetime import date
    out = {}
    if not path.exists():
        return out
    import csv as _csv
    with open(path, newline="", encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            d, m, y = str(r["testing_day"]).split("/")
            out[int(r["nfl_id"])] = date(int(y), int(m), int(d))
    return out


def _read_drills(path: Path) -> dict:
    out: dict[int, set[str]] = {}
    if not path.exists():
        return out
    import csv as _csv
    with open(path, newline="", encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            out.setdefault(int(r["nfl_id"]), set()).add(str(r["drill_type"]))
    return out


def load_envelope(con, cfg: dict, synthetic: bool, sec_regime: str) -> dict[int, dict]:
    """Read the shrunken per-player Combine table (Phase-2 output / synthetic fixture).

    Returns ``{nfl_id: {column: float}}`` for every numeric column present (e.g. ``env_vmax``,
    ``peak_speed``, ``speed_at_20yd``). Missing file → {}. Used to pick the Analysis-A speed feature
    and the robustness panel.
    """
    p = (synthetic_root(cfg, sec_regime) / "combine_envelope.csv") if synthetic else envelope_path(cfg)
    if not p.exists():
        return {}
    cols = [r[0] for r in con.execute(f"describe select * from read_csv_auto('{p}', header=true)").fetchall()]
    numeric = [c for c in cols if c not in ("nfl_id", "combine_position", "draft_year", "provenance")]
    if not numeric:
        return {}
    sel = ", ".join(f'"{c}"' for c in numeric)
    rows = con.execute(f"SELECT nfl_id, {sel} FROM read_csv_auto('{p}', header=true)").fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        out[int(r[0])] = {c: (float(v) if v is not None else None) for c, v in zip(numeric, r[1:])}
    return out


# --------------------------------------------------------------------------- #
# Analysis A
# --------------------------------------------------------------------------- #
def _standardize(x: np.ndarray) -> tuple[np.ndarray, float, float]:
    mu, sd = float(np.mean(x)), float(np.std(x, ddof=0))
    sd = sd if sd > 0 else 1.0
    return (x - mu) / sd, mu, sd


def analysis_a(board_rows: list[dict], testing_days: dict, envelope: dict[int, dict], cfg: dict) -> dict:
    """Regress pre→post-Combine EDP change on z(speed) × z(log EDP_pre) (+ position/year controls).

    Primary spec (pre-declared in ``security.analysis_a``): speed = the shrunken Combine **ceiling**
    feature ``env_vmax`` (the repo's pre-registered primary Combine feature), outcome = raw EDP change.
    A **robustness panel** refits the same spec over the other shrunken speed features and the
    log-ratio outcome, so any sign instability is reported rather than hidden. ``gate_pass`` uses the
    primary spec only.
    """
    acfg = cfg["security"]["analysis_a"]
    pp = sf.pre_post_edp(board_rows, cfg, testing_days)
    players = sf.board_players(board_rows)
    pos_of = {p["nfl_id"]: sf.position_group(cfg, p["position"]) for p in players}
    year_of = {p["nfl_id"]: int(p["draft_year"]) for p in players}

    recs = []
    for nid, d in pp.items():
        if not d.get("has_primary") or not d.get("has_post"):
            continue
        edp_pre, edp_post = d["edp_pre"], d["edp_post"]
        if not edp_pre or not edp_post or edp_pre <= 0 or edp_post <= 0 or nid not in envelope:
            continue
        feats = {k: v for k, v in envelope[nid].items() if v is not None and np.isfinite(v)}
        if not feats:
            continue
        recs.append(dict(nfl_id=nid, edp_pre=edp_pre, edp_change=(edp_post - edp_pre),
                         edp_log_change=math.log(edp_post / edp_pre), feats=feats,
                         position_group=pos_of.get(nid, "OTHER"), draft_year=year_of.get(nid, 0)))
    n = len(recs)
    primary_feature = acfg["speed_feature"]
    summary: dict = {"n": n, "alpha": acfg["alpha"], "min_n": acfg["min_n"],
                     "speed_feature": primary_feature,
                     "population": "board players with Combine tracking",
                     "controls": acfg["controls"]}

    def _fit(sub: list[dict], feature: str, outcome: str) -> dict | None:
        sub = [r for r in sub if feature in r["feats"] and np.isfinite(r["feats"][feature])]
        if len(sub) < 12:
            return None
        y = np.array([r[outcome] for r in sub], float)
        speed = np.array([r["feats"][feature] for r in sub], float)
        log_edp = np.array([math.log(r["edp_pre"]) for r in sub], float)
        z_speed = _standardize(speed)[0] if acfg.get("standardize_speed") else speed
        z_edp = _standardize(log_edp)[0]

        import pandas as pd
        import statsmodels.api as sm
        X = pd.DataFrame({"z_speed": z_speed, "z_logedp": z_edp, "z_speed_x_logedp": z_speed * z_edp})
        if "position_group" in acfg["controls"]:
            d = pd.get_dummies([r["position_group"] for r in sub], prefix="pos", drop_first=True).astype(float)
            X = pd.concat([X, d.reset_index(drop=True)], axis=1)
        if "draft_year" in acfg["controls"]:
            yr = pd.get_dummies(pd.Series([r["draft_year"] for r in sub]), prefix="yr",
                                drop_first=True).astype(float)
            X = pd.concat([X, yr.reset_index(drop=True)], axis=1)
        model = sm.OLS(y, sm.add_constant(X)).fit(cov_type="HC1")
        out = {"n": len(sub), "r2": float(model.rsquared)}
        for name in ("z_speed", "z_speed_x_logedp"):
            out[name] = {"coef": float(model.params[name]), "se": float(model.bse[name]),
                         "pvalue": float(model.pvalues[name])}
        return out

    summary["primary"] = _fit(recs, primary_feature, "edp_change")
    if summary["primary"] is None or n < max(int(acfg["min_n"]), 12):
        summary.update(gate_pass=False, note=f"underpowered/unavailable: n={n} < min_n={acfg['min_n']}",
                       speed_coef=None, interaction_coef=None)
        summary["robustness"] = []
        return summary

    primary = summary["primary"]
    expected = -1.0 if str(acfg["speed_effect_sign"]).lower().startswith("neg") else 1.0
    alpha = float(acfg["alpha"])
    speed_c, inter_c = primary["z_speed"], primary["z_speed_x_logedp"]
    responded = bool(speed_c["pvalue"] < alpha and math.copysign(1, speed_c["coef"]) == expected)
    inter_sig = bool(inter_c["pvalue"] < alpha and math.copysign(1, inter_c["coef"]) == expected)

    robustness = []
    for feat in list(acfg.get("robustness_features", [primary_feature])):
        if feat == primary_feature:
            continue
        for scope, sub in (("all", recs), ("WR", [r for r in recs if r["position_group"] == "WR"])):
            for out_name in ("edp_change", "edp_log_change"):
                r = _fit(sub, feat, out_name)
                if r:
                    robustness.append({"feature": feat, "outcome": out_name, "scope": scope,
                                       "n": r["n"], "speed_coef": r["z_speed"]["coef"],
                                       "speed_p": r["z_speed"]["pvalue"],
                                       "interaction_coef": r["z_speed_x_logedp"]["coef"],
                                       "interaction_p": r["z_speed_x_logedp"]["pvalue"]})
    # full panel (includes the PRIMARY feature) for the D38 attenuation appendix
    panel = []
    features = list(dict.fromkeys([primary_feature] + list(acfg.get("robustness_features", []))))
    for feat in features:
        for scope, sub in (("all", recs), ("WR", [r for r in recs if r["position_group"] == "WR"])):
            for out_name in ("edp_change", "edp_log_change"):
                r = _fit(sub, feat, out_name)
                if r:
                    panel.append({"feature": feat, "outcome": out_name, "scope": scope,
                                  "n": r["n"], "speed_coef": r["z_speed"]["coef"],
                                  "speed_p": r["z_speed"]["pvalue"],
                                  "interaction_coef": r["z_speed_x_logedp"]["coef"],
                                  "interaction_p": r["z_speed_x_logedp"]["pvalue"]})
    summary.update(
        speed_coef=speed_c, interaction_coef=inter_c, r2=primary["r2"],
        market_responded_to_speed=responded, rewarded_more_for_at_risk=inter_sig,
        gate_pass=bool(responded), robustness=robustness,
        panel=panel,
        note=("market rewarded speed (p<alpha, expected sign)" if responded
              else "no significant speed response at alpha for the pre-declared primary spec"),
    )
    return summary


def attenuation_panel(cfg: dict, summary: dict) -> list[dict]:
    """D38 appendix: attenuation-corrected Analysis-A coefficients (approximate, clearly labelled).

    The correction is the pre-registered ``icc_attenuation`` convention (divide the raw coefficient by
    the feature's test–retest ICC). It is an **approximation** (no SE/p correction is propagated; the
    corrected p-value is deliberately left null rather than fabricated).
    """
    acfg = cfg["security"]["analysis_a"]
    icc = sf.load_feature_icc(cfg)
    min_n = int(acfg.get("attenuation_min_n", acfg["min_n"]))
    panel = summary.get("panel") or []
    if not panel:
        return [dict(feature=None, outcome=acfg["outcome"], scope=None, n=summary.get("n"),
                     raw_speed_coef=None, raw_speed_p=None, reliability_icc=None,
                     reliability_source=None, corrected_speed_coef=None,
                     raw_interaction_coef=None, raw_interaction_p=None,
                     corrected_interaction_coef=None, method="icc_attenuation",
                     note="reported null: Analysis A underpowered/unavailable")]
    def _r(v):
        try:
            return round(float(v), 6)
        except (TypeError, ValueError):
            return v
    rows = []
    for r in panel:
        rel = icc.get(r["feature"]) or {}
        lam = rel.get("icc")
        raw_s, raw_i = r["speed_coef"], r["interaction_coef"]
        ok = lam is not None and 0 < float(lam) <= 1 and r["n"] >= min_n
        rows.append(dict(
            feature=r["feature"], outcome=r["outcome"], scope=r["scope"], n=r["n"],
            raw_speed_coef=_r(raw_s), raw_speed_p=_r(r["speed_p"]),
            reliability_icc=_r(lam), reliability_source=rel.get("source"),
            corrected_speed_coef=(_r(raw_s / float(lam)) if ok else None),
            raw_interaction_coef=_r(raw_i), raw_interaction_p=_r(r["interaction_p"]),
            corrected_interaction_coef=(_r(raw_i / float(lam)) if ok else None),
            method="icc_attenuation",
            note=("approximate: corrected = raw / ICC (pre-registered icc_attenuation; no SE/p "
                  "propagated)") if ok else
                 (f"reported null: ICC unavailable for {r['feature']}" if lam is None
                  else f"reported null: n={r['n']} < attenuation_min_n={min_n}")))
    return rows


def _write_analysis_a(cfg: dict, sec_dir: Path, summary: dict, prov: str, log) -> None:
    sec_dir.mkdir(parents=True, exist_ok=True)
    att = attenuation_panel(cfg, summary) if cfg["security"]["analysis_a"].get("attenuation") else []
    summary = dict(provenance=prov, **summary)
    (sec_dir / "analysis_a.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    rows = []
    for name in ("speed_coef", "interaction_coef"):
        c = summary.get(name)
        if isinstance(c, dict):
            rows.append(dict(term=name, coef=c["coef"], se=c["se"], pvalue=c["pvalue"]))
    if not rows:
        rows = [dict(term="none", coef="", se="", pvalue="", note=summary.get("note"))]
    common.write_csv(sec_dir / "analysis_a.csv",
                     [dict(provenance=prov, **r) for r in rows], fieldnames=["provenance", "term", "coef", "se", "pvalue"])
    if att:
        common.write_csv(sec_dir / "analysis_a_attenuation.csv",
                         [dict(provenance=prov, **r) for r in att],
                         fieldnames=["provenance", "feature", "outcome", "scope", "n",
                                     "raw_speed_coef", "raw_speed_p", "reliability_icc",
                                     "reliability_source", "corrected_speed_coef",
                                     "raw_interaction_coef", "raw_interaction_p",
                                     "corrected_interaction_coef", "method", "note"])
        log.info("analysis A attenuation appendix: %d rows -> %s", len(att),
                 sec_dir / "analysis_a_attenuation.csv")
    log.info("analysis A: n=%s gate_pass=%s speed_coef=%s", summary.get("n"), summary.get("gate_pass"),
             summary.get("speed_coef"))


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(cfg: dict, mode: str, synthetic: bool, sec_regime: str, force: bool, log) -> dict:
    con = common.connect(cfg["runtime"]["duckdb_threads"])
    sec_dir = security_dir(cfg)
    prov = "SYNTHETIC FIXTURE (not a finding)" if synthetic else common.provenance(cfg, mode)

    board_rows, testing, drills, meta = load_board_con(con, cfg, synthetic, sec_regime, log)
    exp_rows = sf.build_exposures(board_rows, testing, drills, cfg)
    # parquet (EXACT schema) + csv (provenance first)
    pq_path = sec_dir / cfg["security"]["exposures_file"]
    sf.write_exposure_parquet(pq_path, exp_rows, cfg)
    fields = ["provenance"] + list(cfg["security"]["schema"])
    common.write_csv(sec_dir / "security_exposure.csv",
                     [dict(provenance=prov, **{k: r.get(k) for k in cfg["security"]["schema"]}) for r in exp_rows],
                     fieldnames=fields)
    log.info("security exposure table: %d players (board rows=%d); source=%s", len(exp_rows),
             len(board_rows), meta.get("source"))

    envelope = load_envelope(con, cfg, synthetic, sec_regime)
    a_summary = analysis_a(board_rows, testing, envelope, cfg)
    _write_analysis_a(cfg, sec_dir, a_summary, prov, log)

    summary = dict(provenance=prov, mode=mode, synthetic=synthetic, board_source=meta.get("source"),
                   n_board_rows=len(board_rows), n_board_players=len(sf.board_players(board_rows)),
                   n_exposures=len(exp_rows),
                   n_with_primary=int(sum(1 for r in exp_rows if r["has_primary"])),
                   n_dropped_no_primary=int(sum(1 for r in exp_rows if not r["has_primary"])),
                   n_combine_speed=int(len(envelope)),
                   analysis_a=a_summary,
                   note=("Analysis A uses the EDP board + Combine envelope only (both complete); no game data."
                         if not synthetic else "SYNTHETIC FIXTURE (not a finding)"))
    (sec_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (sec_dir / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")
    return summary


def _render(s: dict) -> str:
    a = s.get("analysis_a", {})
    lines = [f"# Phase 4 — Security exposures + Analysis A ({s['provenance']})", ""]
    lines.append(f"- **mode:** `{s['mode']}`  ·  **synthetic:** {s['synthetic']}  ·  board source: `{s['board_source']}`")
    lines.append(f"- **board rows:** {s['n_board_rows']}  ·  **board players (nfl_id):** {s['n_board_players']}  ·  "
                 f"**exposures:** {s['n_exposures']}  ·  **no 1-week-pre (dropped from B):** {s['n_dropped_no_primary']}")
    lines.append(f"- **Combine speed available (Phase-2 envelope):** {s['n_combine_speed']}")
    lines.append("")
    lines.append("## Analysis A — APPENDIX ONLY (the headline gate is RETIRED by D38)")
    lines.append("")
    lines.append("**D38 (PI override):** Analysis A is **not** a gate and **not** part of the paper "
                 "argument. Headline-gate condition 4 (\"analysis A shows the market responded to "
                 "speed\") is **dropped**. Analysis A survives only as this short descriptive "
                 "appendix; the gate fields below are retained unchanged for provenance. The new "
                 "spine is **incentive exposure** (`src/07_security_models.py`).")
    lines.append("")
    lines.append(f"- n = {a.get('n')}  ·  gate_pass = **{a.get('gate_pass')}**  ·  "
                 f"market_responded_to_speed = {a.get('market_responded_to_speed')}  ·  "
                 f"rewarded_more_for_at_risk = {a.get('rewarded_more_for_at_risk')}")
    for name in ("speed_coef", "interaction_coef"):
        c = a.get(name)
        if isinstance(c, dict):
            lines.append(f"- `{name}`: coef={c['coef']:.4f}, se={c['se']:.4f}, p={c['pvalue']:.4g}")
    lines.append(f"- note: {a.get('note')}")
    lines.append("")
    lines.append("### Attenuation-corrected panel (approximate; `analysis_a_attenuation.csv`)")
    lines.append("Corrected coefficient = raw coefficient ÷ the feature's pre-declared test–retest "
                 "ICC (`validate.measurement_error_correction = icc_attenuation`). The correction is "
                 "an approximation: no standard error or p-value is propagated, so the corrected "
                 "p-value is left null rather than fabricated.")
    lines.append("")
    lines.append(s.get("note", ""))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="BDB27 Phase 4 — security exposures + Analysis A")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore the checkpoint / rebuild")
    ap.add_argument("--synthetic", action="store_true", help="use the synthetic security fixture")
    ap.add_argument("--synthetic-regime", choices=["planted", "null"], default="planted")
    ap.add_argument("--config", default=None)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()
    sec_dir = security_dir(cfg)
    sec_dir.mkdir(parents=True, exist_ok=True)

    # Combine/board-side stage: freeze-only gate in full mode (no game data required).
    gate.enforce(cfg, mode, "06_security", requires_game_side=False, logger=log)

    regime = "null" if args.synthetic_regime == "null" else "planted"
    stage = f"06_security[{mode}{'-synthetic-' + regime if args.synthetic else ''}]"
    prov = "SYNTHETIC FIXTURE (not a finding)" if args.synthetic else common.provenance(cfg, mode)
    done = sec_dir / "_DONE.json"
    if done.exists() and not args.force:
        rec = common.read_done_marker(done)
        if common.checkpoint_match(
                rec, mode=mode, provenance=prov, synthetic=args.synthetic, regime=regime,
                path=done, fingerprint=common.config_fingerprint(cfg, "06_security")):
            log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done)
            common.append_run_log(cfg, stage, mode, 0.0, common.peak_ram_mb(), "", {"skipped": "checkpoint"})
            return
        log.info("checkpoint marker %s does not match the requested run (%s) — rebuilding — "
                 "a sample/other-mode marker never satisfies a full run",
                 done.name, common.checkpoint_mismatch_note(
                     rec, mode=mode, provenance=prov, synthetic=args.synthetic, regime=regime,
                     fingerprint=common.config_fingerprint(cfg, "06_security")))

    with common.timed_stage(cfg, stage, mode, synthetic=args.synthetic):
        summary = run(cfg, mode, args.synthetic, regime, args.force, log)
    (sec_dir / "_DONE.json").write_text(json.dumps(common.done_marker(
        cfg, "06_security", mode=mode, provenance=prov,
        synthetic=bool(args.synthetic), regime=regime), indent=2), encoding="utf-8")
    log.info("Phase 4 security complete -> %s (gate_pass=%s)", sec_dir, summary["analysis_a"].get("gate_pass"))


if __name__ == "__main__":
    main()
