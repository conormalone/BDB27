#!/usr/bin/env python3
"""04_models.py — Phase 4: quantile models of in-game effort speed on the Combine ceiling.

Per `TASK.md` Phase 4 and the FROZEN `notes/prereg.md`:

* **quantile regression** at the pre-registered τ ladder (`models.quantiles = [0.5, 0.75, 0.9, 0.99]`);
* **effort-level** (one row per in-game route-snap effort) with a **player-clustered bootstrap**
  (`models.bootstrap_reps`, seed = config `seed` + `models.bootstrap_seed_offset`);
* controls **without draft** (`models.controls_without_draft`) and **with draft**
  (`models.controls_with_draft`; "undrafted" is an explicit `draft_position` level);
* the **exceedance rate** (share of in-game efforts that beat the Combine ceiling) and the
  **utilization** `u` (= in-game effort speed ÷ ceiling);
* **pooled vs per-position** fits (`models.pooling`);
* the pre-declared ordered **fallback** chain (`models.fallbacks`), each firing logged.

Response / predictor (config-driven): ``response = models.response`` (in-game effort speed) and
``primary_feature = models.primary_feature`` (the Combine ceiling, read from the Phase-3
``efforts.csv`` column ``ceiling_speed_yds``).

Run modes / provenance:

* ``--mode full`` — gated on the pre-registration freeze **and** the audit go/no-go (`src/gate.py`)
  **and** the Phase 0.5 external proxy (existence + exact schema; `external.require_for_phase4`);
  requires ``outputs/game/efforts.csv`` and ``outputs/thresholds.json`` with provenance ``FULL``.
* ``--mode sample`` — game demand is full-only → writes a labelled ``UNVALIDATED SAMPLE OUTPUT``
  placeholder (``PENDING FULL RUN``), never fabricated numbers.
* ``--synthetic`` — identical code path on the committed synthetic fixture (planted ceiling;
  provenance ``SYNTHETIC FIXTURE (not a finding)``), for BOTH regimes; never presented as a result.

Discipline (TASK.md): config-driven (no magic numbers); no full CSV into pandas (Parquet + DuckDB,
explicit columns); wall time + peak RAM to ``outputs/run_log.csv``; checkpoint + skip unless
``--force``; every CSV's FIRST column is ``provenance``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import model_features as mf
import gate

PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PENDING = "PENDING FULL RUN"
EFFORT_COLS = ["nfl_id", "combine_position", "peak_speed", "peak_accel",
               "ceiling_speed_yds", "max_over_ceiling", "is_exceed"]


# --------------------------------------------------------------------------- #
# emit / blocker helpers (house convention: provenance FIRST)
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
        fh.write(f"\n- **BDB27 04_models BLOCKER** ({stamp}): {msg}\n")
    if log:
        log.error("BLOCKER: %s", msg)


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def _bool(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "t", "1", "yes"):
        return True
    if s in ("false", "f", "0", "no"):
        return False
    return None


def load_efforts(con, path: Path, cfg: dict) -> list[dict]:
    """Read the Phase-3 effort table via DuckDB (explicit columns; no pandas)."""
    rows = con.execute(
        f"SELECT {', '.join(EFFORT_COLS)} FROM read_csv_auto('{path}')").fetchall()
    out: list[dict] = []
    for r in rows:
        out.append(dict(
            nfl_id=int(r[0]), position=(str(r[1]) if r[1] is not None else None),
            peak_speed=float(r[2]) if r[2] is not None else float("nan"),
            peak_accel=float(r[3]) if r[3] is not None else float("nan"),
            ceiling=float(r[4]) if r[4] is not None else float("nan"),
            util=float(r[5]) if r[5] is not None else float("nan"),
            is_exceed=_bool(r[6]),
        ))
    return out


def parquet_from(con, src: Path, cache: Path, force: bool, log=None) -> Path:
    return common.to_parquet(con, src, cache, force=force, logger=log)


# --------------------------------------------------------------------------- #
# model rows
# --------------------------------------------------------------------------- #
def build_model_rows(efforts: list[dict], cov: dict[int, dict], cfg: dict) -> list[dict]:
    """Join per-effort rows to per-player covariates; keep complete cases with a finite response."""
    primary = cfg["models"]["primary_feature"]
    rows: list[dict] = []
    for e in efforts:
        c = cov.get(e["nfl_id"])
        if c is None:
            continue
        y = e["peak_speed"]
        x = e["ceiling"]
        if not (np.isfinite(y) and np.isfinite(x)):
            continue
        rows.append(dict(
            nfl_id=int(e["nfl_id"]), y=float(y), env_vmax=float(x),
            position=(e["position"] or c.get("position")),
            age=c["age"], height=c["height"], weight=c["weight"], bmi=c["bmi"],
            draft_position=c["draft_position"], optout=bool(c["optout"]),
            util=e["util"], is_exceed=e["is_exceed"], pass_length=None,
        ))
    # sanity: primary key must be present
    for r in rows:
        if primary not in r:
            r[primary] = r["env_vmax"] if primary == "env_vmax" else float("nan")
    return rows


# --------------------------------------------------------------------------- #
# quantile fits + fallback chain
# --------------------------------------------------------------------------- #
def _find(records: list[dict], pool: str, cname: str, tau: float) -> dict | None:
    for r in records:
        if r["pooling"] == pool and r["controls"] == cname and abs(r["tau"] - tau) < 1e-12:
            return r
    return None


def _fit_one(cfg: dict, rows: list[dict], pool: str, cname: str, ckeys: list[str],
             primary: str, tau: float) -> tuple[dict, list[dict]]:
    m = cfg["models"]
    n = len(rows)
    players = sorted({r["nfl_id"] for r in rows})
    rec: dict[str, Any] = dict(pooling=pool, controls=cname, tau=float(tau), feature=primary,
                               n_obs=n, n_players=len(players), estimate=None, reported=None,
                               boot_se=None, ci_lo=None, ci_hi=None, n_boot_ok=0,
                               converged=False, unstable=False, action="", note="", dropped_controls="")
    events: list[dict] = []
    if n < int(m["min_obs_per_stratum"]) or len(players) < 2:
        rec["action"] = "report_null"
        rec["note"] = f"thin stratum (n_obs={n}, n_players={len(players)})"
        events.append(dict(pooling=pool, controls=cname, tau=float(tau), trigger="thin_stratum",
                           fallback="report_null"))
        return rec, events
    try:
        y, Z, names, dropped, pidx = mf.build_design(rows, primary, ckeys, cfg)
    except ValueError as exc:
        rec["action"] = "report_null"
        rec["note"] = f"design degenerate: {exc}"
        events.append(dict(pooling=pool, controls=cname, tau=float(tau), trigger="degenerate_design",
                           fallback="report_null"))
        return rec, events
    rec["dropped_controls"] = "; ".join(f"{a}:{b}" for a, b in dropped)

    p = mf.qr_fit(y, Z, tau, cfg, int(m["fit_max_iter"]))
    if p is None:
        events.append(dict(pooling=pool, controls=cname, tau=float(tau), trigger="non_convergence",
                           fallback="refit_increased_max_iter"))
        p = mf.qr_fit(y, Z, tau, cfg, int(m["fit_max_iter_retry"]))
    if p is None:
        rec["action"] = "report_null"
        rec["note"] = "no convergence after refit_increased_max_iter"
        events.append(dict(pooling=pool, controls=cname, tau=float(tau), trigger="non_convergence",
                           fallback="report_null"))
        return rec, events

    rec["converged"] = True
    point = float(p[pidx])
    groups = np.array([r["nfl_id"] for r in rows])
    seed = int(cfg["seed"]) + int(m["bootstrap_seed_offset"])
    boot = mf.cluster_bootstrap(y, Z, groups, pidx, tau, int(m["bootstrap_reps"]), seed, cfg)
    rec.update(estimate=point, boot_se=boot["se"], ci_lo=boot["ci_lo"], ci_hi=boot["ci_hi"],
               n_boot_ok=boot["n_ok"])
    rec["reported"] = point
    if boot["se"] is not None and abs(boot["se"]) > abs(point):
        rec["unstable"] = True
    return rec, events


def fit_models(cfg: dict, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Fit every (pooling × controls × τ) stratum, then apply the pre-declared fallback chain."""
    m = cfg["models"]
    primary = m["primary_feature"]
    quantiles = [float(q) for q in m["quantiles"]]
    control_sets = [("without_draft", list(m["controls_without_draft"])),
                    ("with_draft", list(m["controls_with_draft"]))]
    records: list[dict] = []
    events: list[dict] = []
    for pool in m["pooling"]:
        sub = rows if pool == "pooled" else [r for r in rows if r["position"] == pool]
        for cname, ckeys in control_sets:
            for tau in quantiles:
                rec, ev = _fit_one(cfg, sub, pool, cname, ckeys, primary, tau)
                records.append(rec)
                events += ev
    events += _apply_instability(cfg, records)
    return records, events


def _apply_instability(cfg: dict, records: list[dict]) -> list[dict]:
    """Apply adjacent_tau_report / pool_positions / report_null to bootstrap-unstable strata."""
    m = cfg["models"]
    ladder = [float(q) for q in m["quantiles"]]
    top = max(ladder)
    events: list[dict] = []
    for rec in records:
        if not rec.get("unstable"):
            continue
        tau, pool, cname = rec["tau"], rec["pooling"], rec["controls"]
        if abs(tau - top) < 1e-12:
            adj = None
            for q in sorted([x for x in ladder if x < tau], reverse=True):
                r = _find(records, pool, cname, q)
                if r and r["converged"] and not r["unstable"] and r["estimate"] is not None:
                    adj = r
                    break
            rec["action"] = "adjacent_tau_report"
            rec["reported"] = None
            rec["note"] = (f"unstable extreme tail (boot_se>|estimate|); adjacent stable τ="
                           f"{adj['tau']} estimate={adj['estimate']:.4f}" if adj
                           else "unstable extreme tail; no adjacent stable τ")
            events.append(dict(pooling=pool, controls=cname, tau=tau, trigger="bootstrap_instability",
                               fallback="adjacent_tau_report"))
        elif pool != "pooled":
            pr = _find(records, "pooled", cname, tau)
            rec["action"] = "pool_positions"
            rec["note"] = (f"unstable per-position fit; pooled estimate={pr['estimate']:.4f} reported "
                           f"alongside" if pr and pr["estimate"] is not None else
                           "unstable per-position fit; pooled reported alongside")
            events.append(dict(pooling=pool, controls=cname, tau=tau, trigger="bootstrap_instability",
                               fallback="pool_positions"))
        else:
            rec["action"] = "report_null"
            rec["reported"] = None
            rec["note"] = "bootstrap instability in the pooled fit → report null"
            events.append(dict(pooling=pool, controls=cname, tau=tau, trigger="bootstrap_instability",
                               fallback="report_null"))
    return events


# --------------------------------------------------------------------------- #
# exceedance + utilization
# --------------------------------------------------------------------------- #
def exceedance_utilization(cfg: dict, rows: list[dict]) -> list[dict]:
    """Per pooling: exceedance rate, binding verdict (prereg §6), and utilization ``u`` summary."""
    m = cfg["models"]
    refr = float(cfg["audit"]["exceedance_reframe_rate"])
    do_exc = bool(m.get("exceedance", True))
    qlo, qmid, qhi = [float(x) for x in m["utilization_percentiles"]]
    util_name = str(m.get("utilization", "u"))
    pooled = list(m["pooling"])
    seen: list[str] = []
    for p in ["pooled"] + pooled:
        if p not in seen:
            seen.append(p)
    out: list[dict] = []
    for pool in seen:
        sub = rows if pool == "pooled" else [r for r in rows if r["position"] == pool]
        n = len(sub)
        exc = [r["is_exceed"] for r in sub if r["is_exceed"] is not None] if do_exc else []
        ns = sum(1 for r in sub if r["util"] is not None and np.isfinite(r["util"]))
        us = np.array([r["util"] for r in sub if r["util"] is not None and np.isfinite(r["util"])], float)
        rate = (round(len([x for x in exc if x]) / len(exc), 4) if exc else None)
        reframe = (rate is not None and rate > refr)
        out.append(dict(
            pooling=pool, n_efforts=n, n_players=len({r["nfl_id"] for r in sub}),
            n_exceed=sum(1 for x in exc if x), n_effort_compare=len(exc),
            exceedance_rate=rate, exceedance_reframe_rate=refr,
            binding=(None if rate is None else (not reframe)),
            verdict=("binding" if (rate is not None and not reframe)
                     else ("not_binding_use_utilization" if rate is not None else "no_data")),
            utilization=util_name,
            u_n=ns, u_mean=(round(float(np.mean(us)), 4) if us.size else None),
            u_median=(round(float(np.median(us)), 4) if us.size else None),
            u_lo=(round(float(np.quantile(us, qlo)), 4) if us.size else None),
            u_hi=(round(float(np.quantile(us, qhi)), 4) if us.size else None),
        ))
    return out


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def summarize(cfg: dict, prov: str, mode: str, dataset: str, records: list[dict],
              events: list[dict], exu: list[dict], n_rows: int) -> dict:
    pooled0 = exu[0] if exu else {}
    stable = [r for r in records if r["reported"] is not None]
    return dict(
        provenance=prov, mode=mode, dataset=dataset,
        response=cfg["models"]["response"], primary_feature=cfg["models"]["primary_feature"],
        quantiles=[float(q) for q in cfg["models"]["quantiles"]],
        pooling=list(cfg["models"]["pooling"]),
        bootstrap_reps=int(cfg["models"]["bootstrap_reps"]),
        n_effort_rows=int(n_rows), n_strata=int(len(records)), n_strata_reported=int(len(stable)),
        n_fallbacks_fired=int(len(events)),
        exceedance_utilization=exu,
        overall_exceedance_rate=pooled0.get("exceedance_rate"),
        overall_verdict=pooled0.get("verdict"),
        fallbacks_fired=events,
        coefficients=[dict(pooling=r["pooling"], controls=r["controls"], tau=r["tau"],
                           estimate=r["estimate"], reported=r["reported"], boot_se=r["boot_se"],
                           ci_lo=r["ci_lo"], ci_hi=r["ci_hi"], n_obs=r["n_obs"],
                           n_players=r["n_players"], unstable=r["unstable"], action=r["action"])
                      for r in records],
    )


def write_outputs(cfg: dict, prov: str, out_dir: Path, records: list[dict], events: list[dict],
                  exu: list[dict], summary: dict, log) -> None:
    recs = sorted(records, key=lambda r: (r["pooling"], r["controls"], r["tau"]))
    evs = sorted(events, key=lambda r: (r["pooling"], r["controls"], r["tau"], r["fallback"]))
    _emit(out_dir / "model_coefficients.csv", recs, prov, log)
    _emit(out_dir / "exceedance_utilization.csv", exu, prov, log)
    _emit(out_dir / "fallbacks_fired.csv", evs, prov, log)
    (out_dir / "SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (out_dir / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")


def render_summary(s: dict) -> str:
    lines = [f"# Phase 4 — Quantile models ({s['provenance']})", ""]
    lines.append(f"- **mode:** `{s['mode']}`  ·  **dataset:** `{s['dataset']}`")
    lines.append(f"- **response:** `{s['response']}` (in-game effort speed)  ·  "
                 f"**primary feature:** `{s['primary_feature']}`")
    lines.append(f"- **τ ladder:** `{s['quantiles']}`  ·  **pooling:** `{s['pooling']}`  ·  "
                 f"**player-clustered bootstrap reps:** {s['bootstrap_reps']}")
    lines.append(f"- **effort rows:** {s['n_effort_rows']}  ·  **strata:** {s['n_strata']} "
                 f"({s['n_strata_reported']} reported)  ·  **fallbacks fired:** {s['n_fallbacks_fired']}")
    lines.append(f"- **overall exceedance rate:** **{s['overall_exceedance_rate']}**  ·  "
                 f"**verdict:** `{s['overall_verdict']}` (prereg §6)")
    lines.append("")
    lines.append("## exceedance + utilization `u`")
    lines.append("| pooling | n_efforts | n_players | exceedance_rate | verdict | u_median | u_mean |")
    lines.append("|---|---:|---:|---:|---|---:|---:|")
    for r in s["exceedance_utilization"]:
        lines.append(f"| {r['pooling']} | {r['n_efforts']} | {r['n_players']} | {r['exceedance_rate']} "
                     f"| {r['verdict']} | {r['u_median']} | {r['u_mean']} |")
    lines.append("")
    lines.append("Controls (config driven): `age` = draft_year − birth_year (age at the Combine), "
                 "`bmi` = `models.bmi_constant`·weight/height², `draft_position` = draft round tier "
                 "with `undrafted` an explicit level. Near-collinear controls are VIF-pruned "
                 "(`models.control_vif_max`), never the primary feature.")
    lines.append("")
    lines.append("## τ-gradient of the Combine-ceiling slope (controls without draft)")
    lines.append("| pooling | τ | estimate | boot_se | ci_lo | ci_hi | n_obs | unstable | action |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|---|")
    for c in s["coefficients"]:
        if c["controls"] != "without_draft":
            continue
        lines.append(f"| {c['pooling']} | {c['tau']} | {c['estimate']} | {c['boot_se']} | "
                     f"{c['ci_lo']} | {c['ci_hi']} | {c['n_obs']} | {c['unstable']} | {c['action']} |")
    lines.append("")
    lines.append("## fallbacks fired (prereg §7)")
    if s["fallbacks_fired"]:
        lines.append("| pooling | controls | τ | trigger | fallback |")
        lines.append("|---|---|---:|---|---|")
        for e in s["fallbacks_fired"]:
            lines.append(f"| {e['pooling']} | {e['controls']} | {e['tau']} | {e['trigger']} | {e['fallback']} |")
    else:
        lines.append("- none (all strata converged and were bootstrap-stable)")
    lines.append("")
    lines.append("The τ-gradient answers question (2): a slope that rises with τ means the "
                 "Combine→game link strengthens toward the upper tail. `reported` is blank when a "
                 "fallback nulled the estimate (never a forced number).")
    lines.append("")
    return "\n".join(lines)


def sample_placeholder(cfg: dict, prov: str, out_dir: Path, log) -> None:
    note = ("quantile models need the full game-tracking files (Phase-3 demand); the 2023 sample "
            "is a non-representative prefix")
    _emit(out_dir / "models_summary.csv",
          [dict(stage="04_models", status=PENDING, dataset="sample", note=note)], prov, log)
    (out_dir / "SUMMARY.md").write_text(
        f"# Phase 4 — Quantile models ({prov})\n\n- **status:** `{PENDING}` — {note}\n", encoding="utf-8")


def _ensure(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 Phase 4 — quantile models (ceiling → game tail)")
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

    gate.enforce(cfg, mode, "04_models", requires_game_side=True, requires_external_proxy=True,
                 logger=log)

    sub = cfg["models"]["out_subdir"]
    if args.synthetic:
        out_dir = common.resolve(cfg, "outputs", sub) / "synthetic" / args.synthetic_regime
        prov = PROV_SYNTH
    else:
        out_dir = common.resolve(cfg, "outputs", sub)
        prov = common.provenance(cfg, mode)
    _ensure(out_dir)

    done = out_dir / "_DONE"
    if done.exists() and not args.force:
        rec = common.read_done_marker(done)
        if common.checkpoint_match(rec, mode=mode, provenance=prov,
                                   synthetic=args.synthetic):
            log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done)
            common.append_run_log(cfg, f"04_models[{mode}{'-synthetic' if args.synthetic else ''}]",
                                  mode, 0.0, common.peak_ram_mb(), "", {"skipped": "checkpoint"})
            return
        log.info("checkpoint marker %s does not match the requested run (%s) — rebuilding",
                 done.name, common.checkpoint_mismatch_note(
                     rec, mode=mode, provenance=prov, synthetic=args.synthetic))

    stage = f"04_models[{mode}{'-synthetic' if args.synthetic else ''}]"
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
                cr_pq = parquet_from(con, fix / "combine_results.csv", cache / "combine_results.parquet",
                                     args.force)
                pl_pq = parquet_from(con, fix / "players.csv", cache / "players.parquet", args.force)
                dataset = f"synthetic:{args.synthetic_regime}"
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
                cr_pq = parquet_from(con, common.csv_path(cfg, "combine_results"),
                                     parq / "combine_results.parquet", args.force, log)
                pl_pq = parquet_from(con, common.csv_path(cfg, "players"),
                                     parq / "players.parquet", args.force, log)
                dataset = "game_tracking_2023/24/25 (full)"

            game_base = common.resolve(cfg, "outputs") / cfg["game"]["out_subdir"]
            eff_path = (game_base / "synthetic" / args.synthetic_regime / "efforts.csv"
                        if args.synthetic else game_base / "efforts.csv")
            if not eff_path.exists():
                record_blocker(cfg, f"Phase-3 effort table missing at {eff_path}; run "
                                    f"`python src/03_game_demand.py "
                                    f"{'--synthetic --synthetic-regime ' + args.synthetic_regime if args.synthetic else '--mode full'}` "
                                    f"first", log)
                raise SystemExit(2)

            cov = mf.load_covariates(con, cr_pq, pl_pq, cfg)
            efforts = load_efforts(con, eff_path, cfg)
            rows = build_model_rows(efforts, cov, cfg)
            log.info("%s: %d effort rows, %d players", dataset, len(rows), len({r["nfl_id"] for r in rows}))
            records, events = fit_models(cfg, rows)
            exu = exceedance_utilization(cfg, rows)
            summary = summarize(cfg, prov, mode_label, dataset, records, events, exu, len(rows))
            write_outputs(cfg, prov, out_dir, records, events, exu, summary, log)

    done.write_text(json.dumps({"stage": "04_models", "mode": mode, "provenance": prov,
                                "synthetic": args.synthetic}, indent=2), encoding="utf-8")
    log.info("Phase 4 models complete -> %s", out_dir)


if __name__ == "__main__":
    main()
