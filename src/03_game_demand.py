#!/usr/bin/env python3
"""03_game_demand.py — Phase 3b: in-game demand (max-effort route efforts).

Per `TASK.md` Phase 3 and the FROZEN `notes/prereg.md`:

* route snaps via ``route_ran`` (a route snap = ``route_ran IS NOT NULL``; it is **categorical**,
  not a boolean flag — `notes/schema.md` §7 / audit d);
* **top-``k`` efforts per fixed route-snap count**: per player, the top ``k=game.top_k_efforts``
  efforts ranked by the effort metric, from a fixed denominator of ``game.route_snap_count`` route
  snaps (ties at the k-th value kept — prereg §2);
* per-effort **speed / accel** features, recomputed from ``x,y`` (audit f);
* the **envelope fraction** ``max_over_ceiling`` = effort speed / the player's Phase-2 ceiling
  (``env_vmax`` from `02_combine_envelope`).

Run modes / provenance:

* ``--mode full`` — gated on the pre-registration freeze **and** the audit go/no-go (`src/gate.py`).
  Requires ``outputs/thresholds.json`` with provenance ``FULL`` (else it is a blocker) and the
  Phase-2 ceiling table; streams the game tracking per play/player.
* ``--mode sample`` — game demand is a **full-only** quantity: writes a clearly-labelled
  ``UNVALIDATED SAMPLE OUTPUT`` placeholder (``PENDING FULL RUN``), never fabricated numbers.
* ``--synthetic`` — exercises the identical code path on the committed synthetic fixture (planted
  ceiling; provenance ``SYNTHETIC FIXTURE (not a finding)``); the fixture's ceilings come from the
  shared Phase-2 fitter, so the planted ceiling is recovered. Never presented as a result.

Discipline (TASK.md): config-driven (no magic numbers); no full CSV into pandas (Parquet + DuckDB,
explicit columns, one streaming per-play aggregation); downcast dtypes; wall time + peak RAM logged
to ``outputs/run_log.csv``; checkpoint + skip finished work unless ``--force``; every CSV's FIRST
column is ``provenance``.
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
import game_features as gf
import gate

PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PENDING = "PENDING FULL RUN"


# --------------------------------------------------------------------------- #
# CSV emit (provenance FIRST)
# --------------------------------------------------------------------------- #
def _f(x: Any) -> Any:
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
        fh.write(f"\n- **BDB27 03_game_demand BLOCKER** ({stamp}): {msg}\n")
    if log:
        log.error("BLOCKER: %s", msg)


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def _parquet(con, cfg, name, force, log) -> Path:
    parq = common.resolve(cfg, "parquet")
    dst = parq / (Path(cfg["files"][name]).stem + ".parquet")
    return common.to_parquet(con, common.csv_path(cfg, name), dst, force=force, logger=log)


def _parquet_full(con, cfg, name, force, log) -> list[Path]:
    parq = common.resolve(cfg, "parquet")
    out = []
    for fn in cfg["files"][name]:
        src = common.resolve(cfg, "raw", fn)
        if not src.exists():
            raise FileNotFoundError(f"full mode requires {src} (absent on this Pi) -> notes/blockers.md")
        dst = parq / (Path(fn).stem + ".parquet")
        out.append(common.to_parquet(con, src, dst, force=force, logger=log))
    return out


def thresholds_path(cfg: dict, synthetic: bool, regime: str) -> Path:
    name = cfg["game"]["thresholds_file"]
    if synthetic:
        return common.resolve(cfg, "outputs", cfg["game"]["out_subdir"]) / "synthetic" / regime / name
    return common.resolve(cfg, "outputs", name)


def ceilings_path(cfg: dict) -> Path:
    c = cfg["game"]["ceiling"]
    return common.resolve(cfg, "outputs", c["source_subdir"], c["file"])


# --------------------------------------------------------------------------- #
# core computation
# --------------------------------------------------------------------------- #
def _metric_column(cfg: dict) -> str:
    m = str(cfg["game"].get("top_k_metric", "speed")).lower()
    return {"speed": "peak_speed", "accel": "peak_accel"}.get(m, "peak_speed")


def compute(cfg: dict, efforts: list[dict], snap_counts: dict[int, int],
            thresholds: dict, ceilings: dict[int, dict], prov: str) -> dict:
    """Attach ceilings/envelope fractions, build tables, and summarise. Pure (no I/O)."""
    g = cfg["game"]
    margin = float(cfg["audit"]["exceedance_margin_rel"])
    k = int(g["top_k_efforts"])
    min_snaps = int(g["route_snap_count"])
    metric_col = _metric_column(cfg)
    hs = thresholds.get("high_speed_threshold_yds")
    ac = thresholds.get("accel_threshold_yds2")

    n_missing_ceiling = 0
    for e in efforts:
        c = ceilings.get(e["nfl_id"])
        vmax = float(c["vmax"]) if c and np.isfinite(c.get("vmax", np.nan)) else float("nan")
        a0 = float(c["a0"]) if c and np.isfinite(c.get("a0", np.nan)) else float("nan")
        if not (np.isfinite(vmax) and vmax > 0):
            n_missing_ceiling += 1
        e["ceiling_speed_yds"] = vmax
        e["ceiling_accel_yds2"] = a0
        e["max_over_ceiling"] = (e["peak_speed"] / vmax) if (np.isfinite(vmax) and vmax > 0) else float("nan")
        e["accel_over_ceiling"] = (e["peak_accel"] / a0) if (np.isfinite(a0) and a0 > 0) else float("nan")
        e["is_high_speed"] = (bool(e["peak_speed"] >= float(hs)) if hs is not None and np.isfinite(e["peak_speed"]) else None)
        e["is_high_accel"] = (bool(e["peak_accel"] >= float(ac)) if ac is not None and np.isfinite(e["peak_accel"]) else None)
        e["is_exceed"] = (bool(e["peak_speed"] > vmax * (1.0 + margin))
                          if np.isfinite(vmax) and vmax > 0 else None)

    by_player: dict[int, list[dict]] = {}
    for e in efforts:
        by_player.setdefault(e["nfl_id"], []).append(e)

    tk_rows: list[dict] = []
    ps_rows: list[dict] = []
    for nid in sorted(by_player):
        effs = by_player[nid]
        pos = next((e["combine_position"] for e in effs if e["combine_position"]), None)
        n_snaps = int(snap_counts.get(nid, 0))
        eligible = n_snaps >= min_snaps
        tk = gf.top_k_efforts(effs, k, metric_col)
        for rank, e in enumerate(tk, 1):
            tk_rows.append(dict(nfl_id=nid, combine_position=pos, rank=rank,
                                game_id=e["game_id"], play_id=e["play_id"], route_ran=e["route_ran"],
                                pass_length=e["pass_length"], target=e["target"],
                                peak_speed=e["peak_speed"], peak_accel=e["peak_accel"],
                                max_over_ceiling=e["max_over_ceiling"],
                                is_exceed=e["is_exceed"]))
        fr = [e["max_over_ceiling"] for e in effs if np.isfinite(e["max_over_ceiling"])]
        tk_fr = [e["max_over_ceiling"] for e in tk if np.isfinite(e["max_over_ceiling"])]
        n_exc = sum(1 for e in effs if e["is_exceed"] is True)
        n_eff = len(effs)
        ps_rows.append(dict(
            nfl_id=nid, combine_position=pos,
            n_route_snaps=n_snaps, eligible=eligible, n_efforts_tracked=n_eff,
            ceiling_speed_yds=effs[0]["ceiling_speed_yds"],
            n_exceed=n_exc, exceedance_rate=(round(n_exc / n_eff, 4) if n_eff else None),
            topk_n=len(tk),
            topk_mean_speed=(round(float(np.mean([e["peak_speed"] for e in tk])), 4) if tk else None),
            topk_mean_envelope_fraction=(round(float(np.mean(tk_fr)), 4) if tk_fr else None),
            topk_max_envelope_fraction=(round(float(np.max(tk_fr)), 4) if tk_fr else None),
            max_envelope_fraction=(round(float(np.max(fr)), 4) if fr else None),
        ))

    tot_eff = len(efforts)
    tot_exc = sum(1 for e in efforts if e["is_exceed"] is True)
    pos_tot: dict[str, int] = {}
    pos_exc: dict[str, int] = {}
    for e in efforts:
        p = e["combine_position"] or "UNKNOWN"
        pos_tot[p] = pos_tot.get(p, 0) + 1
        if e["is_exceed"] is True:
            pos_exc[p] = pos_exc.get(p, 0) + 1
    by_pos = {p: dict(n_efforts=pos_tot[p], n_exceed=pos_exc.get(p, 0),
                      exceedance_rate=round(pos_exc.get(p, 0) / pos_tot[p], 4)) for p in sorted(pos_tot)}

    eligible_players = [r for r in ps_rows if r["eligible"]]
    exceed_rates = [e for e in (r["exceedance_rate"] for r in eligible_players) if e is not None]
    overall = dict(
        n_route_snaps=int(sum(snap_counts.values())),
        n_efforts_tracked=int(tot_eff),
        n_players=int(len(ps_rows)),
        n_players_eligible=int(len(eligible_players)),
        n_players_missing_ceiling=int(sum(1 for r in ps_rows if not np.isfinite(r["ceiling_speed_yds"]))),
        n_missing_ceiling_efforts=int(n_missing_ceiling),
        overall_exceedance_rate=(round(tot_exc / tot_eff, 4) if tot_eff else None),
        exceedance_reframe_rate=cfg["audit"]["exceedance_reframe_rate"],
        exceedance_margin_rel=margin,
        by_position=by_pos,
        mean_player_exceedance_rate=(round(float(np.mean(exceed_rates)), 4) if exceed_rates else None),
    )
    return dict(efforts=efforts, top_k=tk_rows, players=ps_rows,
                top_k_efforts=k, route_snap_count=min_snaps, top_k_metric=metric_col,
                thresholds=thresholds, overall=overall)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(con, cfg: dict, prov: str, mode: str, dataset: str, inputs: dict,
        thresholds: dict, ceilings: dict, out_dir: Path, log) -> dict:
    efforts = gf.extract_efforts(con, inputs["gt"], inputs["pp"], inputs["cr"], cfg)
    snap_counts = gf.route_snap_counts(con, inputs["pp"])
    log.info("%s: %d route-snap efforts; %d players with route snaps",
             dataset, len(efforts), len(snap_counts))
    res = compute(cfg, efforts, snap_counts, thresholds, ceilings, prov)

    efforts_out = sorted(res["efforts"], key=lambda e: (e["nfl_id"], e["game_id"], e["play_id"]))
    tk_out = sorted(res["top_k"], key=lambda e: (e["nfl_id"], e["rank"]))
    ps_out = sorted(res["players"], key=lambda e: (e["combine_position"] or "", e["nfl_id"]))

    thr_row = dict(method=thresholds.get("method"),
                   thresholds_provenance=thresholds.get("provenance"),
                   high_speed_threshold_yds=thresholds.get("high_speed_threshold_yds"),
                   accel_threshold_yds2=thresholds.get("accel_threshold_yds2"),
                   high_speed_percentile=thresholds.get("high_speed_percentile"),
                   accel_percentile=thresholds.get("accel_percentile"),
                   top_k_efforts=res["top_k_efforts"], route_snap_count=res["route_snap_count"],
                   top_k_metric=res["top_k_metric"])

    _emit(out_dir / "efforts.csv", efforts_out, prov, log)
    _emit(out_dir / "top_k_efforts.csv", tk_out, prov, log)
    _emit(out_dir / "player_summary.csv", ps_out, prov, log)
    _emit(out_dir / "thresholds_applied.csv", [thr_row], prov, log)

    summary = dict(provenance=prov, mode=mode, dataset=dataset,
                   top_k_efforts=res["top_k_efforts"], route_snap_count=res["route_snap_count"],
                   top_k_metric=res["top_k_metric"],
                   thresholds=thresholds, **res["overall"])
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out_dir / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


def render_summary(s: dict) -> str:
    lines = [f"# Phase 3 — Game demand ({s['provenance']})", ""]
    lines.append(f"- **mode:** `{s['mode']}`  ·  **dataset:** `{s['dataset']}`")
    lines.append(f"- **effort sampling:** top-`{s['top_k_efforts']}` efforts per player ranked by "
                 f"`{s['top_k_metric']}`, fixed denominator `{s['route_snap_count']}` route snaps (prereg §2)")
    lines.append(f"- **thresholds:** provenance `{s['thresholds'].get('provenance')}`, method "
                 f"`{s['thresholds'].get('method')}`, high-speed "
                 f"`{s['thresholds'].get('high_speed_threshold_yds')}` yd/s, accel "
                 f"`{s['thresholds'].get('accel_threshold_yds2')}` yd/s²")
    lines.append(f"- **route snaps:** {s['n_route_snaps']}  ·  **efforts (tracked):** "
                 f"{s['n_efforts_tracked']}  ·  **players:** {s['n_players']} "
                 f"(eligible ≥{s['route_snap_count']} snaps: {s['n_players_eligible']})")
    lines.append(f"- **overall exceedance rate** (effort speed > ceiling, margin "
                 f"{s['exceedance_margin_rel']}): **{s['overall_exceedance_rate']}**  ·  "
                 f"reframe threshold {s['exceedance_reframe_rate']} "
                 f"(prereg §6: >{s['exceedance_reframe_rate']} ⇒ not binding ⇒ utilization)")
    lines.append(f"- **missing ceiling:** {s['n_missing_ceiling_efforts']} efforts / "
                 f"{s['n_players_missing_ceiling']} players (reported, not silently dropped)")
    lines.append("")
    lines.append("## exceedance by position")
    lines.append("| position | n_efforts | n_exceed | exceedance_rate |")
    lines.append("|---|---:|---:|---:|")
    for p, d in (s.get("by_position") or {}).items():
        lines.append(f"| {p} | {d['n_efforts']} | {d['n_exceed']} | {d['exceedance_rate']} |")
    lines.append("")
    lines.append("## envelope fraction")
    lines.append("`max_over_ceiling` = per-effort peak speed ÷ the player's Phase-2 Combine ceiling "
                 "(`env_vmax`); `accel_over_ceiling` uses `env_a0`.")
    lines.append("")
    return "\n".join(lines)


def sample_placeholder(cfg: dict, prov: str, out_dir: Path, log) -> None:
    """Sample mode: game demand needs full tracking -> a labelled PENDING placeholder."""
    note = ("game demand requires the full game-tracking files; the 2023 sample is a "
            "non-representative prefix (notes/schema.md §8)")
    _emit(out_dir / "game_demand_summary.csv",
          [dict(stage="03_game_demand", status=PENDING, dataset="sample", note=note)], prov, log)
    _emit(out_dir / "thresholds_applied.csv",
          [dict(stage="03_game_demand", status=PENDING, note=note)], prov, log)
    (out_dir / "SUMMARY.md").write_text(
        f"# Phase 3 — Game demand ({prov})\n\n- **status:** `{PENDING}` — {note}\n",
        encoding="utf-8")


def _ensure_dirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 Phase 3b — in-game demand (max-effort efforts)")
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

    gate.enforce(cfg, mode, "03_game_demand", requires_game_side=True, logger=log)

    game_sub = cfg["game"]["out_subdir"]
    if args.synthetic:
        out_dir = common.resolve(cfg, "outputs", game_sub) / "synthetic" / args.synthetic_regime
        prov = PROV_SYNTH
    else:
        out_dir = common.resolve(cfg, "outputs", game_sub)
        prov = common.provenance(cfg, mode)
    _ensure_dirs(out_dir)

    done = out_dir / "_DONE"
    if done.exists() and not args.force:
        try:
            rec = json.loads(done.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            rec = {}
        if (rec.get("mode") == mode and rec.get("provenance") == prov
                and bool(rec.get("synthetic")) == bool(args.synthetic)):
            log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done)
            common.append_run_log(cfg, f"03_game_demand[{mode}{'-synthetic' if args.synthetic else ''}]",
                                  mode, 0.0, common.peak_ram_mb(), "", {"skipped": "checkpoint"})
            return

    stage = f"03_game_demand[{mode}{'-synthetic' if args.synthetic else ''}]"
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
                inputs = {}
                for name, fn in (("gt", "game_tracking.csv"), ("pp", "player_play.csv"),
                                 ("cr", "combine_results.csv"), ("ct", "combine_tracking.csv")):
                    dst = cache / (name + ".parquet")
                    common.to_parquet(con, fix / fn, dst, force=args.force, logger=None)
                    inputs[name] = dst
                ceilings = gf.combine_ceilings(con, [inputs["ct"]], cfg)
                inputs = {"gt": [inputs["gt"]], "pp": inputs["pp"], "cr": inputs["cr"]}
                thr_path = thresholds_path(cfg, True, args.synthetic_regime)
                if not thr_path.exists():
                    record_blocker(cfg, f"synthetic thresholds missing at {thr_path}; run "
                                        f"`python src/00_thresholds.py --synthetic "
                                        f"--synthetic-regime {args.synthetic_regime}` first", log)
                    raise SystemExit(2)
                thresholds = json.loads(thr_path.read_text(encoding="utf-8"))
                run(con, cfg, prov, mode_label, f"synthetic:{args.synthetic_regime}",
                    inputs, thresholds, ceilings, out_dir, log)
            else:  # full
                thr_path = thresholds_path(cfg, False, "")
                if not thr_path.exists():
                    record_blocker(cfg, f"thresholds missing at {thr_path}; run "
                                        f"`python src/00_thresholds.py --mode full` first", log)
                    raise SystemExit(2)
                thresholds = json.loads(thr_path.read_text(encoding="utf-8"))
                if str(thresholds.get("provenance")) != "FULL":
                    record_blocker(cfg, f"thresholds provenance is '{thresholds.get('provenance')}', "
                                        f"not FULL — a full run requires FULL (TASK.md blockers)", log)
                    raise SystemExit(2)
                cp = ceilings_path(cfg)
                if not cp.exists():
                    record_blocker(cfg, f"Phase-2 ceiling table missing at {cp}; run "
                                        f"`python src/02_combine_envelope.py --mode full` first", log)
                    raise SystemExit(2)
                ceilings = gf.load_ceilings(cp, cfg["game"]["ceiling"]["speed_column"],
                                            cfg["game"]["ceiling"]["accel_column"])
                gt = _parquet_full(con, cfg, "game_tracking_full", args.force, log)
                pp = _parquet(con, cfg, "player_play", args.force, log)
                cr = _parquet(con, cfg, "combine_results", args.force, log)
                run(con, cfg, prov, "full", "game_tracking_2023/24/25 (full)",
                    {"gt": gt, "pp": pp, "cr": cr}, thresholds, ceilings, out_dir, log)

    done.write_text(json.dumps({"stage": "03_game_demand", "mode": mode, "provenance": prov,
                                "synthetic": args.synthetic}, indent=2), encoding="utf-8")
    log.info("Phase 3 game demand complete -> %s", out_dir)


if __name__ == "__main__":
    main()
