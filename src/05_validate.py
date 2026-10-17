#!/usr/bin/env python3
"""05_validate.py — Phase 5: validation of the Combine-ceiling → in-game tail link.

Per `TASK.md` Phase 5 and the FROZEN `notes/prereg.md`:

* **player-grouped cross-validation** (`validate.cv_folds = 5`, `validate.cv_seed`) of the Phase-4
  model **against a controls-only baseline** (`validate.baseline = controls_only`), scored with the
  proper **pinball (quantile) loss** at each pre-registered τ;
* **permutation** test (`validate.perm_reps`, player-level, so clustering is preserved);
* **measurement-error correction** (`validate.measurement_error_correction = icc_attenuation`):
  divide the estimated slope by the feature reliability (ICC);
* the pre-declared **sensitivity** analyses (`validate.sensitivity`):
  `alt_quantiles`, `drop_thin_repeats`, `exclude_optouts`, `margin_grid`;
* a **power note** and explicit **limitations**.

It also restates the **binding verdict** for each pooling (`prereg §6`: exceedance rate vs the
`audit.exceedance_reframe_rate` — ">25% speed exceedance → envelope not binding → reframe as
utilization").

Run modes / provenance: `--mode full` is gated on the freeze **and** the audit go/no-go and requires
FULL-provenance thresholds + the Phase-3 effort table; `--mode sample` writes a labelled
``UNVALIDATED SAMPLE OUTPUT`` placeholder; `--synthetic` runs the identical path on the committed
fixture for BOTH regimes (never a finding).

Discipline (TASK.md): config-driven (no magic numbers); Parquet + DuckDB (no full CSV into pandas);
wall time + peak RAM to ``outputs/run_log.csv``; checkpoint + skip unless ``--force``; every CSV's
FIRST column is ``provenance``.
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import model_features as mf
import game_features as gf
import combine_features as cf
import gate

PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PENDING = "PENDING FULL RUN"


# --------------------------------------------------------------------------- #
# emit / blocker helpers
# --------------------------------------------------------------------------- #
def _f(x: Any) -> Any:
    if isinstance(x, bool):
        return x
    if isinstance(x, float):
        if not np.isfinite(x):
            return ""
        return round(x, 4)
    return x


def _emit(path: Path, rows: list[dict], prov: str, log) -> None:
    rows = [dict(r) for r in rows] or [{}]
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    fields = ["provenance"] + [f for f in fields if f != "provenance"]
    norm = []
    for r in rows:
        d = {"provenance": prov}
        for k, v in r.items():
            if k != "provenance":
                d[k] = _f(v)
        norm.append(d)
    common.write_csv(path, norm, fieldnames=fields)
    log.info("wrote %s (%d rows)", path.name, len(norm))


def record_blocker(cfg: dict, msg: str, log=None) -> None:
    path = common.resolve(cfg, "notes") / "blockers.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- **BDB27 05_validate BLOCKER** ({stamp}): {msg}\n")
    if log:
        log.error("BLOCKER: %s", msg)


# --------------------------------------------------------------------------- #
# rows / designs
# --------------------------------------------------------------------------- #
def full_design(cfg: dict, rows: list[dict], primary: bool = True):
    """Design on ALL rows (fixed columns) so train/test/permutation share the same layout."""
    key = cfg["models"]["primary_feature"] if primary else None
    return mf.build_design(rows, key, cfg["models"]["controls_without_draft"], cfg)


def reliability_icc(cfg: dict, con, ct_paths, use_phase2: bool = True) -> tuple[float | None, str]:
    """Feature reliability for the attenuation correction (Phase-2 ICC, else recomputed)."""
    feature = cfg["validate"]["measurement_error_feature"]
    p2 = common.resolve(cfg, "outputs", cfg["envelope"]["out_subdir"]) / "feature_selection.csv"
    if use_phase2 and p2.exists():
        with open(p2, newline="", encoding="utf-8") as fh:
            for r in _csv.DictReader(fh):
                if r.get("feature") == feature and r.get("icc") not in (None, ""):
                    try:
                        return float(r["icc"]), "phase2_feature_selection.csv"
                    except (TypeError, ValueError):
                        break
    # fallback: recompute test-retest ICC of the feature from the Combine tracking
    try:
        per = gf.combine_attempt_env(con, ct_paths, cfg)
        groups = {int(nid): d["vmax"] for nid, d in per.items()}
        r = cf.icc_1_1(groups, int(cfg["audit"]["icc_min_players_with_repeat"]))
        return (float(r["icc"]) if r["icc"] is not None else None,
                f"combine_icc ({r['status']})")
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"unavailable ({type(exc).__name__})"


# --------------------------------------------------------------------------- #
# (1) player-grouped CV vs controls-only baseline
# --------------------------------------------------------------------------- #
def grouped_cv(cfg: dict, rows: list[dict]) -> list[dict]:
    v = cfg["validate"]
    quantiles = [float(q) for q in cfg["models"]["quantiles"]]
    players = np.array([r["nfl_id"] for r in rows])
    folds = mf.player_folds(players.tolist(), int(v["cv_folds"]), int(v["cv_seed"]))
    try:
        y, Z, _, _, pidx = full_design(cfg, rows, primary=True)
        _, Zb, _, _, _ = full_design(cfg, rows, primary=False)
    except ValueError as exc:
        return [dict(tau=q, fold=None, model=None, note=f"design unavailable: {exc}") for q in quantiles]

    out: list[dict] = []
    for tau in quantiles:
        full_l: list[float] = []
        base_l: list[float] = []
        for fi, fp in enumerate(folds):
            fp = set(fp)
            test = np.array([p in fp for p in players])
            tr = ~test
            if int(test.sum()) < 1 or int(tr.sum()) < (Z.shape[1] + 1):
                continue
            pf = mf.qr_fit(y[tr], Z[tr], tau, cfg, cfg["models"]["fit_max_iter"])
            pb = mf.qr_fit(y[tr], Zb[tr], tau, cfg, cfg["models"]["fit_max_iter"])
            if pf is None or pb is None:
                continue
            lf = mf.pinball_loss(y[test], Z[test] @ pf, tau)
            lb = mf.pinball_loss(y[test], Zb[test] @ pb, tau)
            full_l.append(lf)
            base_l.append(lb)
            out.append(dict(tau=tau, fold=fi, model="full", n_test=int(test.sum()), pinball=lf))
            out.append(dict(tau=tau, fold=fi, model="controls_only", n_test=int(test.sum()), pinball=lb))
        if full_l:
            for agg, fn in (("mean", np.mean), ("median", np.median)):
                fv, bv = float(fn(full_l)), float(fn(base_l))
                out.append(dict(tau=tau, fold=agg, model="full", pinball=fv))
                out.append(dict(tau=tau, fold=agg, model="controls_only", pinball=bv))
                out.append(dict(tau=tau, fold=agg, model="improvement", pinball=bv - fv,
                                note="baseline − full (>0 ⇒ the ceiling adds out-of-sample signal; "
                                     "median is robust to a single exploding fold on a tiny fixture)"))
    return out


# --------------------------------------------------------------------------- #
# (2) permutation null (player-level)
# --------------------------------------------------------------------------- #
def permutation_test(cfg: dict, rows: list[dict]) -> dict:
    v = cfg["validate"]
    tau = float(v["perm_tau"])
    try:
        y, Z, _, _, pidx = full_design(cfg, rows, primary=True)
    except ValueError as exc:
        return dict(tau=tau, n_reps=0, observed_slope=None, p_two_sided=None, n_ok=0, note=str(exc))
    p0 = mf.qr_fit(y, Z, tau, cfg, cfg["models"]["fit_max_iter"])
    if p0 is None:
        return dict(tau=tau, n_reps=0, observed_slope=None, p_two_sided=None, n_ok=0,
                    note="observed fit failed")
    obs = float(p0[pidx])
    players = [r["nfl_id"] for r in rows]
    uplayers = sorted(set(players))
    val_by_player = {}
    for r in rows:
        val_by_player[r["nfl_id"]] = float(r[cfg["models"]["primary_feature"]])
    rows_player = np.array(players)
    rng = np.random.default_rng(int(cfg["seed"]) + int(v["perm_seed_offset"]))
    n_ok = ge = 0
    for _ in range(int(v["perm_reps"])):
        perm = rng.permutation(len(uplayers))
        mp = {uplayers[k]: val_by_player[uplayers[perm[k]]] for k in range(len(uplayers))}
        xnew = np.array([mp[p] for p in rows_player])
        Zp = Z.copy()
        Zp[:, pidx] = xnew
        p = mf.qr_fit(y, Zp, tau, cfg, cfg["models"]["fit_max_iter"])
        if p is None:
            continue
        n_ok += 1
        if abs(float(p[pidx])) >= abs(obs):
            ge += 1
    return dict(tau=tau, n_reps=int(v["perm_reps"]), observed_slope=obs,
                p_two_sided=(round(ge / n_ok, 4) if n_ok else None), n_ok=n_ok,
                n_ge=ge, expr="|perm slope| >= |observed slope| (player-level permutation)")


# --------------------------------------------------------------------------- #
# (3) measurement-error correction
# --------------------------------------------------------------------------- #
def measurement_error(cfg: dict, rows: list[dict], icc: float | None, src: str) -> list[dict]:
    out: list[dict] = []
    try:
        y, Z, _, _, pidx = full_design(cfg, rows, primary=True)
    except ValueError as exc:
        return [dict(tau=q, note=f"design unavailable: {exc}") for q in cfg["models"]["quantiles"]]
    for tau in [float(q) for q in cfg["models"]["quantiles"]]:
        p = mf.qr_fit(y, Z, tau, cfg, cfg["models"]["fit_max_iter"])
        slope = float(p[pidx]) if p is not None else None
        corrected = (slope / icc) if (slope is not None and icc not in (None, 0)) else None
        out.append(dict(tau=tau, feature=cfg["validate"]["measurement_error_feature"],
                        reliability_icc=icc, reliability_source=src,
                        slope_uncorrected=slope, slope_corrected=corrected,
                        note="icc_attenuation: corrected = slope / reliability"))
    return out


# --------------------------------------------------------------------------- #
# (4) pre-declared sensitivity analyses
# --------------------------------------------------------------------------- #
def _slope_at(cfg: dict, rows: list[dict], tau: float) -> tuple[float | None, int, int]:
    if len(rows) < int(cfg["models"]["min_obs_per_stratum"]) or len({r["nfl_id"] for r in rows}) < 2:
        return None, len(rows), len({r["nfl_id"] for r in rows})
    try:
        y, Z, _, _, pidx = full_design(cfg, rows, primary=True)
    except ValueError:
        return None, len(rows), len({r["nfl_id"] for r in rows})
    p = mf.qr_fit(y, Z, tau, cfg, cfg["models"]["fit_max_iter"])
    return (float(p[pidx]) if p is not None else None), len(rows), len({r["nfl_id"] for r in rows})


def sensitivity(cfg: dict, rows: list[dict], attempts: dict[int, int]) -> list[dict]:
    v = cfg["validate"]
    analyses = list(v["sensitivity"])
    tau0 = float(v["perm_tau"])
    out: list[dict] = []
    if "alt_quantiles" in analyses:
        for q in [float(x) for x in v["sensitivity_alt_quantiles"]]:
            s, n, np_ = _slope_at(cfg, rows, q)
            out.append(dict(analysis="alt_quantiles", detail=f"tau={q}", tau=q, slope=s,
                            n_obs=n, n_players=np_))
    if "drop_thin_repeats" in analyses:
        thr = int(v["min_repeats_for_sensitivity"])
        keep = [r for r in rows if attempts.get(r["nfl_id"], 0) >= thr]
        dropped = len({r["nfl_id"] for r in rows}) - len({r["nfl_id"] for r in keep})
        s, n, np_ = _slope_at(cfg, keep, tau0)
        out.append(dict(analysis="drop_thin_repeats", detail=f">= {thr} dash attempts", tau=tau0,
                        slope=s, n_obs=n, n_players=np_, n_players_dropped=dropped))
    if "exclude_optouts" in analyses:
        keep = [r for r in rows if not r.get("optout")]
        dropped = len({r["nfl_id"] for r in rows}) - len({r["nfl_id"] for r in keep})
        s, n, np_ = _slope_at(cfg, keep, tau0)
        out.append(dict(analysis="exclude_optouts", detail="drop Combine opt-outs", tau=tau0,
                        slope=s, n_obs=n, n_players=np_, n_players_dropped=dropped))
    if "margin_grid" in analyses:
        refr = float(cfg["audit"]["exceedance_reframe_rate"])
        for m in [float(x) for x in v["sensitivity_margin_grid"]]:
            comp = [r for r in rows if np.isfinite(r["env_vmax"]) and r["env_vmax"] > 0]
            n = len(comp)
            ne = sum(1 for r in comp if r["y"] > r["env_vmax"] * (1.0 + m))
            rate = round(ne / n, 4) if n else None
            out.append(dict(analysis="margin_grid", detail=f"margin={m}", exceedance_rate=rate,
                            n_efforts=n, reframe_rate=refr,
                            verdict=("not_binding_use_utilization" if (rate is not None and rate > refr)
                                     else "binding" if rate is not None else "no_data")))
    # never silently drop a declared-but-unrecognised analysis
    implemented = {"alt_quantiles", "drop_thin_repeats", "exclude_optouts", "margin_grid"}
    for name in analyses:
        if name not in implemented:
            out.append(dict(analysis=name, detail="declared in validate.sensitivity but not implemented",
                            note="no-op (reported, not silently dropped)"))
    return out


# --------------------------------------------------------------------------- #
# binding verdict (restated)
# --------------------------------------------------------------------------- #
def binding_verdict(cfg: dict, rows: list[dict]) -> dict:
    refr = float(cfg["audit"]["exceedance_reframe_rate"])
    comp = [r for r in rows if r["is_exceed"] is not None]
    n = len(comp)
    ne = sum(1 for r in comp if r["is_exceed"])
    rate = round(ne / n, 4) if n else None
    return dict(n_efforts=n, n_exceed=ne, exceedance_rate=rate, reframe_rate=refr,
                verdict=("binding" if (rate is not None and rate <= refr)
                         else ("not_binding_use_utilization" if rate is not None else "no_data")))


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def build_rows(cfg: dict, con, eff_path: Path, cr_pq: Path, pl_pq: Path, log) -> list[dict]:
    col = ["nfl_id", "combine_position", "peak_speed", "ceiling_speed_yds", "is_exceed"]
    raw = con.execute(f"SELECT {', '.join(col)} FROM read_csv_auto('{eff_path}')").fetchall()
    cov = mf.load_covariates(con, cr_pq, pl_pq, cfg)
    rows: list[dict] = []
    for r in raw:
        nid = int(r[0])
        c = cov.get(nid)
        y = float(r[2]) if r[2] is not None else float("nan")
        x = float(r[3]) if r[3] is not None else float("nan")
        if c is None or not (np.isfinite(y) and np.isfinite(x)):
            continue
        ex = r[4]
        ex = (ex if isinstance(ex, bool) else
              (None if ex is None else str(ex).strip().lower() in ("true", "t", "1", "yes")))
        rows.append(dict(nfl_id=nid, y=y, env_vmax=x, position=(str(r[1]) if r[1] is not None else None),
                         age=c["age"], height=c["height"], weight=c["weight"], bmi=c["bmi"],
                         draft_position=c["draft_position"], optout=bool(c["optout"]), is_exceed=ex))
    return rows


def summarize(cfg: dict, prov: str, mode: str, dataset: str, cv: list[dict], perm: dict,
              meas: list[dict], sens: list[dict], verdict: dict, icc_src: str) -> dict:
    mean_cv = [r for r in cv if r.get("fold") in ("mean", "median")]
    return dict(
        provenance=prov, mode=mode, dataset=dataset,
        cv_grouping=cfg["validate"]["cv_grouping"], cv_folds=int(cfg["validate"]["cv_folds"]),
        cv_seed=int(cfg["validate"]["cv_seed"]), baseline=cfg["validate"]["baseline"],
        perm_reps=int(cfg["validate"]["perm_reps"]), perm_tau=float(cfg["validate"]["perm_tau"]),
        measurement_error_correction=cfg["validate"]["measurement_error_correction"],
        reliability_source=icc_src,
        sensitivity=list(cfg["validate"]["sensitivity"]),
        binding_verdict=verdict,
        cv=mean_cv, permutation=perm, measurement_error=meas, sensitivity_results=sens,
    )


def render_summary(s: dict) -> str:
    lines = [f"# Phase 5 — Validation ({s['provenance']})", ""]
    lines.append(f"- **mode:** `{s['mode']}`  ·  **dataset:** `{s['dataset']}`")
    lines.append(f"- **CV:** player-grouped, `{s['cv_folds']}` folds (seed `{s['cv_seed']}`) vs "
                 f"baseline `{s['baseline']}`; metric = pinball loss (proper QR scoring rule)")
    lines.append(f"- **permutation:** {s['perm_reps']} reps at τ={s['perm_tau']} (player-level)")
    lines.append(f"- **measurement-error correction:** `{s['measurement_error_correction']}` "
                 f"(reliability source: {s['reliability_source']})")
    lines.append(f"- **binding verdict:** `{s['binding_verdict']['verdict']}` "
                 f"(exceedance {s['binding_verdict']['exceedance_rate']} vs reframe "
                 f"{s['binding_verdict']['reframe_rate']})")
    lines.append("")
    lines.append("## cross-validation (pinball loss; mean and median over folds)")
    lines.append("| τ | full(mean) | ctrl(mean) | impr(mean) | full(med) | ctrl(med) | impr(med) |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    by = {}
    for r in s["cv"]:
        by.setdefault((r["tau"], r["fold"]), {})[r["model"]] = r["pinball"]
    for tau in sorted({t for t, _ in by}):
        m = by.get((tau, "mean"), {})
        md = by.get((tau, "median"), {})
        lines.append(f"| {tau} | {m.get('full')} | {m.get('controls_only')} | {m.get('improvement')} "
                     f"| {md.get('full')} | {md.get('controls_only')} | {md.get('improvement')} |")
    lines.append("")
    lines.append("## permutation null")
    p = s["permutation"]
    lines.append(f"- observed slope (τ={p.get('tau')}): {p.get('observed_slope')}  ·  "
                 f"p (two-sided, {p.get('n_ok')}/{p.get('n_reps')} usable): **{p.get('p_two_sided')}**")
    lines.append("")
    lines.append("## measurement-error correction (icc_attenuation)")
    lines.append("| τ | slope (uncorr) | reliability (ICC) | slope (corrected) |")
    lines.append("|---:|---:|---:|---:|")
    for r in s["measurement_error"]:
        lines.append(f"| {r.get('tau')} | {r.get('slope_uncorrected')} | {r.get('reliability_icc')} "
                     f"| {r.get('slope_corrected')} |")
    lines.append("")
    lines.append("## pre-declared sensitivity analyses")
    lines.append("| analysis | detail | slope | exceedance_rate | verdict | n_obs | n_players |")
    lines.append("|---|---|---:|---:|---|---:|---:|")
    for r in s["sensitivity_results"]:
        lines.append(f"| {r.get('analysis')} | {r.get('detail')} | {r.get('slope')} "
                     f"| {r.get('exceedance_rate')} | {r.get('verdict')} | {r.get('n_obs')} "
                     f"| {r.get('n_players')} |")
    lines.append("")
    lines.append("## power note")
    lines.append("- The primary sample is **effort-level** (one row per in-game route-snap effort); "
                 "the fixed-denominator design (`game.top_k_efforts` per `game.route_snap_count` snaps) "
                 "caps each player's contribution, so the effective sample is ~`k × #eligible players` "
                 "efforts, not the raw frame count. The player-clustered bootstrap SE and the "
                 "player-grouped CV are the honest precision measures; a slope is only interpreted when "
                 "the bootstrap interval excludes 0 and the CV improvement is positive.")
    lines.append("")
    lines.append("## limitations")
    lines.append("- Combine ceilings are estimated from **thin** straight-line repeats (dash median 2) "
                 "with hierarchical shrinkage; measurement error attenuates the slope (corrected here by "
                 "`icc_attenuation`). See `notes/limitations.md`.")
    lines.append("- Combine **opt-outs are non-random** (≈17% missing `forty`), a selection threat; the "
                 "`exclude_optouts` sensitivity bounds its influence.")
    lines.append("- Causal claims are out of scope: this is a **prediction/description** of the "
                 "Combine→in-game tail link among players who reached the NFL.")
    lines.append("")
    return "\n".join(lines)


def sample_placeholder(cfg: dict, prov: str, out_dir: Path, log) -> None:
    note = "validation needs the full game-tracking files (Phase-3 demand) and the full-run effort tables"
    _emit(out_dir / "validation_summary.csv",
          [dict(stage="05_validate", status=PENDING, dataset="sample", note=note)], prov, log)
    (out_dir / "SUMMARY.md").write_text(
        f"# Phase 5 — Validation ({prov})\n\n- **status:** `{PENDING}` — {note}\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 Phase 5 — validation (CV, permutation, error, sensitivity)")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore the checkpoint / rebuild Parquet")
    ap.add_argument("--synthetic", action="store_true",
                    help="run on the committed synthetic fixture (planted ceiling; not a finding)")
    ap.add_argument("--synthetic-regime", choices=["binding", "null"], default="binding")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()

    gate.enforce(cfg, mode, "05_validate", requires_game_side=True, logger=log)

    sub = cfg["validate"]["out_subdir"]
    if args.synthetic:
        out_dir = common.resolve(cfg, "outputs", sub) / "synthetic" / args.synthetic_regime
        prov = PROV_SYNTH
    else:
        out_dir = common.resolve(cfg, "outputs", sub)
        prov = common.provenance(cfg, mode)
    out_dir.mkdir(parents=True, exist_ok=True)

    done = out_dir / "_DONE"
    if done.exists() and not args.force:
        rec = common.read_done_marker(done)
        if common.checkpoint_match(rec, mode=mode, provenance=prov,
                                   synthetic=args.synthetic):
            log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done)
            common.append_run_log(cfg, f"05_validate[{mode}{'-synthetic' if args.synthetic else ''}]",
                                  mode, 0.0, common.peak_ram_mb(), "", {"skipped": "checkpoint"})
            return
        log.info("checkpoint marker %s does not match the requested run (%s) — rebuilding",
                 done.name, common.checkpoint_mismatch_note(
                     rec, mode=mode, provenance=prov, synthetic=args.synthetic))

    stage = f"05_validate[{mode}{'-synthetic' if args.synthetic else ''}]"
    mode_label = "synthetic" if args.synthetic else mode
    con = common.connect(cfg["runtime"]["duckdb_threads"])

    if mode == "sample" and not args.synthetic:
        with common.timed_stage(cfg, stage, mode, synthetic=False):
            sample_placeholder(cfg, prov, out_dir, log)
    else:
        with common.timed_stage(cfg, stage, mode, synthetic=args.synthetic):
            if args.synthetic:
                fix = common.resolve(cfg, "synthetic") / args.synthetic_regime
                cache = out_dir / "_cache"
                cr_pq = common.to_parquet(con, fix / "combine_results.csv",
                                          cache / "combine_results.parquet", force=args.force)
                pl_pq = common.to_parquet(con, fix / "players.csv", cache / "players.parquet",
                                          force=args.force)
                ct_pq = common.to_parquet(con, fix / "combine_tracking.csv",
                                          cache / "combine_tracking.parquet", force=args.force)
                dataset = f"synthetic:{args.synthetic_regime}"
                ct_paths = [ct_pq]
            else:
                thr_path = common.resolve(cfg, "outputs", cfg["game"]["thresholds_file"])
                if not thr_path.exists():
                    record_blocker(cfg, f"thresholds missing at {thr_path}; run "
                                        f"`python src/00_thresholds.py --mode full` first", log)
                    raise SystemExit(2)
                thr = json.loads(thr_path.read_text(encoding="utf-8"))
                if str(thr.get("provenance")) != "FULL":
                    record_blocker(cfg, f"thresholds provenance is '{thr.get('provenance')}', not FULL "
                                        f"— a full run requires FULL (TASK.md blockers)", log)
                    raise SystemExit(2)
                parq = common.resolve(cfg, "parquet")
                cr_pq = common.to_parquet(con, common.csv_path(cfg, "combine_results"),
                                          parq / "combine_results.parquet", force=args.force, logger=log)
                pl_pq = common.to_parquet(con, common.csv_path(cfg, "players"),
                                          parq / "players.parquet", force=args.force, logger=log)
                ct_pq = common.to_parquet(con, common.csv_path(cfg, "combine_tracking"),
                                          parq / "combine_tracking.parquet", force=args.force, logger=log)
                dataset = "game_tracking_2023/24/25 (full)"
                ct_paths = [ct_pq]

            game_base = common.resolve(cfg, "outputs") / cfg["game"]["out_subdir"]
            eff_path = (game_base / "synthetic" / args.synthetic_regime / "efforts.csv"
                        if args.synthetic else game_base / "efforts.csv")
            if not eff_path.exists():
                record_blocker(cfg, f"Phase-3 effort table missing at {eff_path}; run "
                                    f"`python src/03_game_demand.py` first", log)
                raise SystemExit(2)

            rows = build_rows(cfg, con, eff_path, cr_pq, pl_pq, log)
            log.info("%s: %d effort rows, %d players", dataset, len(rows), len({r["nfl_id"] for r in rows}))
            per = gf.combine_attempt_env(con, ct_paths, cfg)
            attempts = {nid: d["n_attempts"] for nid, d in per.items()}
            icc, icc_src = reliability_icc(cfg, con, ct_paths, use_phase2=not args.synthetic)

            cv = grouped_cv(cfg, rows)
            perm = permutation_test(cfg, rows)
            meas = measurement_error(cfg, rows, icc, icc_src)
            sens = sensitivity(cfg, rows, attempts)
            verdict = binding_verdict(cfg, rows)
            summary = summarize(cfg, prov, mode_label, dataset, cv, perm, meas, sens, verdict, icc_src)

            _emit(out_dir / "cv_results.csv", cv, prov, log)
            _emit(out_dir / "permutation.csv", [perm], prov, log)
            _emit(out_dir / "measurement_error.csv", meas, prov, log)
            _emit(out_dir / "sensitivity.csv", sens, prov, log)
            (out_dir / "SUMMARY.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
            (out_dir / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")

    done.write_text(json.dumps({"stage": "05_validate", "mode": mode, "provenance": prov,
                                "synthetic": args.synthetic}, indent=2), encoding="utf-8")
    log.info("Phase 5 validation complete -> %s", out_dir)


if __name__ == "__main__":
    main()
