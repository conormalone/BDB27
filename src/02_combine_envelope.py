#!/usr/bin/env python3
"""02_combine_envelope.py — Phase 2: the Combine speed envelope (ceiling) and feature selection.

Per `TASK.md` Phase 2 and the FROZEN `notes/prereg.md`:

  * fit ``v(t) = vmax*(1 - exp(-t/tau))`` on the straight-line drill(s) (`envelope.fit_drills`),
    with ``a0 = vmax/tau``;
  * model-free split / speed-at-time features, and cut metrics (entry/exit speed, CoD deficit)
    for the cutting drills;
  * **hierarchical shrinkage** of per-player envelope parameters (two-stage empirical Bayes);
  * select **<= max_features** Combine features by **ICC then VIF** (reproducing the pre-declared
    reliable set), and emit the per-player table **with SEs**.

The Combine tracking file is COMPLETE, so this stage runs for real; a `--mode full` run is gated on
the pre-registration freeze (`src/gate.py`). Sample outputs are stamped `UNVALIDATED SAMPLE OUTPUT`.

Discipline (TASK.md): config-driven (no magic numbers); never load full CSVs into pandas
(Parquet + DuckDB only, explicit columns); wall time + peak RAM logged to `outputs/run_log.csv`;
checkpoint + skip finished work unless `--force`; every CSV's FIRST column is `provenance`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import combine_features as cf
import gate


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _f(x: Any) -> Any:
    """Round floats for CSV output; map NaN/Inf to empty string."""
    if isinstance(x, float):
        if not np.isfinite(x):
            return ""
        return round(x, 4)
    return x


def _cols(rows: list[dict]) -> list[str]:
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    return ["provenance"] + [f for f in fields if f != "provenance"]


def _emit(path: Path, rows: list[dict], prov: str, log) -> None:
    """Write a CSV with `provenance` first; '-' floats become blanks."""
    if not rows:
        rows = [{}]
    fields = _cols(rows)
    norm = []
    for r in rows:
        d = {"provenance": prov}
        for k, v in r.items():
            if k != "provenance":
                d[k] = _f(v)
        norm.append(d)
    common.write_csv(path, norm, fieldnames=fields)
    log.info("wrote %s (%d rows)", path.name, len(norm))


def _s(x: Any) -> str:
    return x.decode() if isinstance(x, (bytes, bytearray)) else str(x)


# --------------------------------------------------------------------------- #
# data loading (DuckDB; explicit columns; no pandas)
# --------------------------------------------------------------------------- #
def load_attempts(con, ct_parquet: Path, drills: list[str]) -> dict:
    """Fetch per-attempt tracking (x,y,time) for `drills`, ordered for streaming."""
    dlist = ",".join(f"'{d}'" for d in drills)
    return con.execute(f"""
        SELECT nfl_id, event_id, draft_year, drill_type, x, y,
               (epoch_ms(time) - min(epoch_ms(time)) OVER (PARTITION BY event_id)) / 1000.0 AS t
        FROM {common.pq(ct_parquet)}
        WHERE entity_type = 'PLAYER' AND drill_type IN ({dlist})
        ORDER BY nfl_id, drill_type, event_id, time
    """).fetchnumpy()


def positions(con, cr_parquet: Path) -> dict[int, tuple[str, int]]:
    """nfl_id -> (combine_position, draft_year)."""
    rows = con.execute(f"SELECT nfl_id, combine_position, draft_year FROM {common.pq(cr_parquet)}").fetchall()
    return {int(n): (str(p), int(y)) for n, p, y in rows}


# --------------------------------------------------------------------------- #
# per-attempt grouping
# --------------------------------------------------------------------------- #
def group_attempts(arr: dict, cfg: dict) -> tuple[dict, dict, list[dict]]:
    """Group frames into attempts; return (straight_by_player, cut_by_player, straight_attempt_rows)."""
    fit_drill = cfg["envelope"]["fit_drills"]
    cut_drills = set(cfg["audit"]["cutting_drills"])
    straight: dict[int, list[dict]] = {}
    cut: dict[int, list[dict]] = {}
    attempt_rows: list[dict] = []

    n = len(arr["nfl_id"])
    if n == 0:
        return straight, cut, attempt_rows
    key = None
    cur: dict | None = None

    def flush(k, c):
        if c is None or len(c["x"]) < 2:
            return
        nid, ev, dy, dt = k
        if dt == fit_drill:
            feats = cf.attempt_straight_features(c["x"], c["y"], c["t"], cfg)
            straight.setdefault(nid, []).append(feats)
            attempt_rows.append(dict(nfl_id=nid, event_id=ev, draft_year=dy, drill_type=dt,
                                     n_frames=len(c["x"]), **feats))
        elif dt in cut_drills:
            feats = cf.attempt_cut_features(c["x"], c["y"], c["t"], cfg)
            cut.setdefault((nid, dt), []).append(feats)

    for i in range(n):
        k = (int(arr["nfl_id"][i]), _s(arr["event_id"][i]), int(arr["draft_year"][i]), _s(arr["drill_type"][i]))
        if k != key:
            flush(key, cur)
            key, cur = k, {"x": [], "y": [], "t": []}
        cur["x"].append(float(arr["x"][i]))
        cur["y"].append(float(arr["y"][i]))
        cur["t"].append(float(arr["t"][i]))
    flush(key, cur)
    return straight, cut, attempt_rows


# --------------------------------------------------------------------------- #
# per-player aggregation
# --------------------------------------------------------------------------- #
def _mean_se(vals: list[float]) -> tuple[float, float]:
    v = [x for x in vals if np.isfinite(x)]
    if not v:
        return float("nan"), float("nan")
    m = float(np.mean(v))
    se = float(np.std(v, ddof=1) / np.sqrt(len(v))) if len(v) > 1 else float("nan")
    return m, se


def aggregate_players(straight: dict, cut: dict, cfg: dict, posmap: dict) -> tuple[list[dict], list[dict]]:
    """Build the per-player envelope table (raw + hierarchically shrunk, with SEs) and cut metrics."""
    env_cfg = cfg["envelope"]
    cand = list(env_cfg["candidate_features"])
    cut_metrics = list(env_cfg["cut_metrics"])

    # --- hierarchical shrinkage of the envelope parameters (vmax, tau) ---
    vmax_by = {nid: [a["env_vmax"] for a in att] for nid, att in straight.items()}
    tau_by = {nid: [a["env_tau"] for a in att] for nid, att in straight.items()}
    shrink_v = cf.shrink_hierarchical(vmax_by, env_cfg["min_attempts_per_player"])
    shrink_t = cf.shrink_hierarchical(tau_by, env_cfg["min_attempts_per_player"])
    comp_v = shrink_v.pop("__components__")
    comp_t = shrink_t.pop("__components__")

    rows: list[dict] = []
    for nid in sorted(straight):
        atts = straight[nid]
        position, draft_year = posmap.get(nid, ("UNKNOWN", 0))
        r: dict[str, Any] = {"nfl_id": nid, "combine_position": position, "draft_year": draft_year,
                             "n_attempts": len(atts), "n_fit_ok": int(sum(np.isfinite(a["env_vmax"]) for a in atts))}

        for f in cand:
            if f.startswith("env_"):
                continue
            m, se = _mean_se([a.get(f, float("nan")) for a in atts])
            r[f] = m
            r[f + "_se"] = se

        sv, st = shrink_v.get(nid, {}), shrink_t.get(nid, {})
        r["raw_env_vmax"], r["raw_env_vmax_se"] = sv.get("raw_mean", float("nan")), sv.get("raw_se", float("nan"))
        r["raw_env_tau"], r["raw_env_tau_se"] = st.get("raw_mean", float("nan")), st.get("raw_se", float("nan"))
        r["raw_env_a0"] = (sv.get("raw_mean", np.nan) / st.get("raw_mean", np.nan)
                           if st.get("raw_mean") else float("nan"))
        r["raw_env_a0_se"] = cf.delta_se_ratio(sv.get("raw_mean", np.nan), st.get("raw_mean", np.nan),
                                               sv.get("raw_se", np.nan), st.get("raw_se", np.nan))
        r["env_vmax"], r["env_vmax_se"] = sv.get("mean", float("nan")), sv.get("se", float("nan"))
        r["env_tau"], r["env_tau_se"] = st.get("mean", float("nan")), st.get("se", float("nan"))
        r["env_a0"] = (sv.get("mean", np.nan) / st.get("mean", np.nan)) if st.get("mean") else float("nan")
        r["env_a0_se"] = cf.delta_se_ratio(sv.get("mean", np.nan), st.get("mean", np.nan),
                                           sv.get("se", np.nan), st.get("se", np.nan))
        r["shrink_weight_vmax"] = sv.get("shrink_weight", float("nan"))
        r["shrink_weight_tau"] = st.get("shrink_weight", float("nan"))
        # plausibility flag (prereg sec3.4 pattern): a ceiling far above the observed peak is an
        # unidentifiable extrapolation on a noisy/thin attempt — FLAG it, never silently drop it.
        flag = float(env_cfg.get("flag_vmax_over_peak", float("nan")))
        raw_v, pk = sv.get("raw_mean", float("nan")), r.get("peak_speed", float("nan"))
        r["env_vmax_implausible"] = bool(np.isfinite(raw_v) and np.isfinite(pk) and np.isfinite(flag)
                                         and raw_v > flag * pk)
        rows.append(r)

    cut_rows: list[dict] = []
    for (nid, drill) in sorted(cut):
        atts = cut[(nid, drill)]
        position, draft_year = posmap.get(nid, ("UNKNOWN", 0))
        cr: dict[str, Any] = {"nfl_id": nid, "combine_position": position, "drill_type": drill,
                              "n_attempts": len(atts)}
        for m in cut_metrics:
            mn, se = _mean_se([a.get(m, float("nan")) for a in atts])
            cr[m], cr[m + "_se"] = mn, se
        cut_rows.append(cr)

    return rows, cut_rows, {"env_vmax": comp_v, "env_tau": comp_t}


# --------------------------------------------------------------------------- #
# feature selection: ICC -> VIF
# --------------------------------------------------------------------------- #
def build_matrix(player_rows: list[dict], cand: list[str]) -> tuple[dict[str, np.ndarray], list[int]]:
    """Complete-case per-player feature matrix (players with all candidate features present)."""
    keep = [r for r in player_rows if all(np.isfinite(float(r.get(f, np.nan))) for f in cand)]
    matrix = {f: np.array([float(r[f]) for r in keep]) for f in cand}
    return matrix, [r["nfl_id"] for r in keep]


def select_features(straight: dict, player_rows: list[dict], cfg: dict) -> dict:
    env_cfg = cfg["envelope"]
    audit = cfg["audit"]
    cand = list(env_cfg["candidate_features"])
    max_features = int(env_cfg["max_features"])
    icc_min = float(env_cfg["icc_min"])
    vif_max = float(env_cfg["vif_max"])
    min_rep = int(audit["icc_min_players_with_repeat"])

    # --- ICC (test-retest over repeated attempts; all players of the drill) ---
    icc: dict[str, dict] = {}
    for f in cand:
        groups = {nid: [a.get(f, float("nan")) for a in atts] for nid, atts in straight.items()}
        icc[f] = cf.icc_1_1(groups, min_rep)

    # --- VIF on the complete-case per-player matrix ---
    matrix, used_ids = build_matrix(player_rows, cand)
    vif_pool = cf.vif_table(matrix, cand)

    retained = [f for f in cand if icc[f]["icc"] is not None and icc[f]["icc"] >= icc_min]
    dropped: dict[str, str] = {}
    for f in cand:
        if f not in retained:
            st = icc[f]["status"]
            dropped[f] = (f"ICC below threshold ({icc[f]['icc']})" if icc[f]["icc"] is not None
                          else f"{st}")

    trace: list[dict] = []
    while len(retained) > max_features:
        vifs = cf.vif_table(matrix, retained)
        over = [f for f in retained if vifs.get(f, float("nan")) > vif_max]
        if not over:
            trace.append(dict(action="stop", note=f"no retained feature has VIF>{vif_max}; "
                                                f"{len(retained)} remain (> max_features)"))
            break
        worst = min(over, key=lambda f: (icc[f]["icc"], f))  # least reliable (lowest ICC)
        retained.remove(worst)
        dropped[worst] = f"VIF pruning: vif={vifs[worst]:.3f} > {vif_max} and lowest ICC among over-ceiling"
        trace.append(dict(action="drop", feature=worst, vif=round(vifs[worst], 4),
                          icc=round(icc[worst]["icc"], 4)))

    vif_final = cf.vif_table(matrix, retained) if len(retained) >= 2 else {f: float("nan") for f in retained}
    residual = [f for f in retained if vif_final.get(f, float("nan")) > vif_max]

    return dict(candidate=cand, icc=icc, vif_pool=vif_pool, retained=retained, dropped=dropped,
                trace=trace, vif_final=vif_final, residual_vif_flags=residual,
                n_players_vif=len(used_ids), vif_max=vif_max, icc_min=icc_min,
                max_features=max_features, expected=list(env_cfg.get("expected_features", [])))


def selection_rows(sel: dict) -> list[dict]:
    cand = sel["candidate"]
    out = []
    for f in cand:
        i = sel["icc"][f]
        out.append(dict(feature=f,
                        icc=(round(i["icc"], 4) if i["icc"] is not None else None),
                        icc_status=i["status"],
                        n_players_with_repeat=i["n_players_with_repeat"],
                        n_attempts_used=i["n_attempts_used"],
                        vif_full_pool=(round(sel["vif_pool"][f], 4) if np.isfinite(sel["vif_pool"].get(f, np.nan)) else None),
                        vif_final=(round(sel["vif_final"][f], 4) if np.isfinite(sel["vif_final"].get(f, np.nan)) else None),
                        retained=(f in sel["retained"]),
                        decision=(sel["dropped"].get(f, "retained"))))
    return out


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(con, cfg: dict, mode: str, prov: str, log) -> dict:
    env_cfg = cfg["envelope"]
    parq = common.resolve(cfg, "parquet")
    ct_pq = parq / (Path(cfg["files"]["combine_tracking"]).stem + ".parquet")
    cr_pq = parq / (Path(cfg["files"]["combine_results"]).stem + ".parquet")

    drills = list(dict.fromkeys(list(env_cfg["fit_drills"] if isinstance(env_cfg["fit_drills"], list)
                                     else [env_cfg["fit_drills"]]) + list(cfg["audit"]["cutting_drills"])))
    arr = load_attempts(con, ct_pq, drills)
    posmap = positions(con, cr_pq)
    straight, cut, attempt_rows = group_attempts(arr, cfg)
    player_rows, cut_rows, components = aggregate_players(straight, cut, cfg, posmap)
    sel = select_features(straight, player_rows, cfg)

    # positions summary
    pos_counts: dict[str, int] = {}
    for r in player_rows:
        pos_counts[r["combine_position"]] = pos_counts.get(r["combine_position"], 0) + 1

    out_dir = common.resolve(cfg, "outputs", env_cfg["out_subdir"])
    attempt_rows.sort(key=lambda r: (r["nfl_id"], r["event_id"]))
    player_rows.sort(key=lambda r: (r["combine_position"], r["nfl_id"]))
    cut_rows.sort(key=lambda r: (r["combine_position"], r["nfl_id"], r["drill_type"]))
    _emit(out_dir / "per_player_envelope.csv", player_rows, prov, log)
    _emit(out_dir / "attempt_features.csv", attempt_rows, prov, log)
    _emit(out_dir / "cut_metrics.csv", cut_rows, prov, log)
    _emit(out_dir / "feature_selection.csv", selection_rows(sel), prov, log)

    expected = sel["expected"]
    summary = dict(
        provenance=prov, mode=mode,
        fit_drills=env_cfg["fit_drills"], curve=env_cfg["curve"], a0_definition=env_cfg["a0_definition"],
        n_players=int(len(player_rows)), n_attempts=int(len(attempt_rows)),
        position_counts=pos_counts,
        n_players_by_attempts={"1": int(sum(r["n_attempts"] == 1 for r in player_rows)),
                               "2": int(sum(r["n_attempts"] == 2 for r in player_rows)),
                               ">=3": int(sum(r["n_attempts"] >= 3 for r in player_rows))},
        envelope_components=components,
        retained_features=sel["retained"],
        expected_features=expected,
        retained_matches_expected=(sorted(sel["retained"]) == sorted(expected)),
        residual_vif_flags=sel["residual_vif_flags"],
        n_env_vmax_implausible=int(sum(bool(r.get("env_vmax_implausible")) for r in player_rows)),
        vmax_flag_mult=env_cfg.get("flag_vmax_over_peak"),
        selection_trace=sel["trace"],
        vif_max=sel["vif_max"], icc_min=sel["icc_min"], max_features=sel["max_features"],
        n_players_vif_matrix=sel["n_players_vif"],
        cut_metrics=env_cfg["cut_metrics"],
        n_cut_rows=int(len(cut_rows)),
    )
    (out_dir / "selection.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("retained features: %s (expected %s; match=%s)",
             sel["retained"], expected, summary["retained_matches_expected"])
    return summary


def render_summary(s: dict) -> str:
    lines = [f"# Phase 2 — Combine envelope ({s['provenance']})", ""]
    lines.append(f"- **mode:** `{s['mode']}`  ·  **fit drills:** `{s['fit_drills']}`  ·  "
                 f"**curve:** `{s['curve']}`  ·  **a0:** `{s['a0_definition']}`")
    lines.append(f"- **players:** {s['n_players']}  ·  **attempts:** {s['n_attempts']}  ·  "
                 f"**positions:** {s['position_counts']}")
    lines.append(f"- **attempts/player:** {s['n_players_by_attempts']}")
    lines.append(f"- **retained features ({s['max_features']} max):** `{s['retained_features']}`  ·  "
                 f"**expected:** `{s['expected_features']}`  ·  **match:** {s['retained_matches_expected']}")
    lines.append(f"- **VIF ceiling:** {s['vif_max']}  ·  **ICC floor:** {s['icc_min']}  ·  "
                 f"**VIF matrix players:** {s['n_players_vif_matrix']}")
    if s["residual_vif_flags"]:
        lines.append(f"- **residual VIF > ceiling (flagged):** `{s['residual_vif_flags']}` "
                     f"(reported, not silently dropped — see `notes/limitations.md`)")
    lines.append(f"- **implausible-ceiling flags** (raw ceiling > {s.get('vmax_flag_mult')}x mean peak, "
                 f"i.e. unidentifiable extrapolation on a noisy/thin attempt): "
                 f"{s.get('n_env_vmax_implausible', 0)} players (flagged in the table, never dropped)")
    lines.append("")
    lines.append("## envelope components (two-stage empirical Bayes)")
    for name, comp in (s["envelope_components"] or {}).items():
        lines.append(f"- `{name}`: {comp}")
    lines.append("")
    lines.append("## selection trace (ICC -> VIF)")
    for t in s["selection_trace"]:
        lines.append(f"- {t}")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="BDB27 Phase 2 — Combine envelope + feature selection")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore the checkpoint / rebuild Parquet")
    ap.add_argument("--config", default=None)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()
    out_dir = common.resolve(cfg, "outputs", cfg["envelope"]["out_subdir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-registration freeze gate: a full run may not proceed on an unfrozen/missing prereg.
    gate.enforce(cfg, mode, "02_combine_envelope", requires_game_side=False, logger=log)

    prov = common.provenance(cfg, mode)
    done = out_dir / "_DONE"
    if done.exists() and not args.force:
        rec = common.read_done_marker(done)
        if common.checkpoint_match(rec, mode=mode, provenance=prov, path=done,
                                   fingerprint=common.config_fingerprint(
                                       cfg, "02_combine_envelope")):
            log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done.name)
            common.append_run_log(cfg, f"02_combine_envelope[{mode}]", mode, 0.0,
                                  common.peak_ram_mb(), "", {"skipped": "checkpoint"})
            return
        log.info("checkpoint marker %s does not match the requested run (%s) — rebuilding",
                 done.name, common.checkpoint_mismatch_note(
                     rec, mode=mode, provenance=prov,
                     fingerprint=common.config_fingerprint(cfg, "02_combine_envelope")))

    con = common.connect(cfg["runtime"]["duckdb_threads"])
    with common.timed_stage(cfg, f"02_combine_envelope[{mode}]", mode):
        parq = common.resolve(cfg, "parquet")
        for name in ("combine_tracking", "combine_results"):
            common.to_parquet(con, common.csv_path(cfg, name),
                              parq / (Path(cfg["files"][name]).stem + ".parquet"),
                              force=args.force, logger=log)
        summary = run(con, cfg, mode, prov, log)

    (out_dir / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")
    done.write_text(json.dumps(common.done_marker(
        cfg, "02_combine_envelope", mode=mode, provenance=prov), indent=2), encoding="utf-8")
    log.info("Phase 2 complete -> %s", out_dir)


if __name__ == "__main__":
    main()
