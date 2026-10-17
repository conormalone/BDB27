#!/usr/bin/env python3
"""synthetic_security_game_fixture.py — deterministic game-side fixture for Phase 5 B–G.

Why this exists
---------------
The game-side security models (`src/07_security_models.py`) need Phase-3-shaped tables (per-effort
speeds, top-k efforts, player summaries, a Combine envelope, attempt features, covariates, a selection
pool) that **cannot be produced on this Pi for the real data** (the full `game_tracking_*.csv` files are
absent and the 2023 sample is a non-representative prefix). `TASK.md` requires a synthetic **recovery**
case (a planted effect the pipeline must find) **and** a **null** case (it must not find one) **and** a
**selection** case (unweighted estimates biased, IPW moving back toward the planted value).

What it writes (``fixtures/synthetic/security_game/<regime>/``)
---------------------------------------------------------------
``efforts.csv``, ``top_k_efforts.csv``, ``player_summary.csv``, ``thresholds_applied.csv``,
``security_exposure.csv``, ``per_player_envelope.csv``, ``attempt_features.csv``,
``covariates.csv``, ``selection_pool.csv``, ``feature_reliability.csv``, ``meta.json``.

Regimes (all share one population and RNG stream, so planted vs null is an exact counterfactual):

* ``planted`` — ``moved_down`` players have a **smaller** Combine→game gap
  (``log game speed = log ceiling + gap0 + δ·1[moved_down]``, δ = config ``delta`` ≈ 0.08), so the
  group coefficient is **positive** (the incentive hypothesis: moving down ⇒ stronger reason to
  invest in the speed mechanism ⇒ higher ceiling realisation). Includes a continuous
  ``dose_gamma`` gradient in ``log(EDP_pre)``.
* ``null`` — identical generator with δ = 0 (``delta_null``); the pipeline must **not** find a group
  effect.
* ``selection`` — planted δ **and** appearance probability falling in ``log(EDP_pre)`` (at-risk
  players less likely to reach game data), strong enough that the unweighted group coefficient is
  biased and IPW moves back toward the planted value.

Design details that matter for the tests
----------------------------------------
* top-k efforts (5) carry the planted δ; **bottom-k efforts (5) carry no δ** — the pre-declared
  low-effort placebo (F2).
* `moved_down` / `high_secure` are assigned by the **same** pre-Combine sort key the stage uses
  (``security_features.assign_security_groups``), and the ground-truth groups are recorded in
  ``meta.json`` so a test can check the stage reproduces them.
* forced edge players: no 2-week snapshot (level fallback), one effort, <100 route snaps, no game
  data, missing size covariates, no 1-week-pre snapshot.
* The declared reliability (ICC) file is a **pipeline input**, not a property of this small fixture —
  so the attenuation correction is expected to overshoot here; the tests assert direction, not
  calibration.
* **Never a finding.** Every output is stamped ``SYNTHETIC FIXTURE (not a finding)``.

Usage
-----
    python src/synthetic_security_game_fixture.py [--regime planted|null|selection] [--config ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import security_features as sf

PROV = "SYNTHETIC FIXTURE (not a finding)"
ROUTES = ["SLANT", "HITCH", "CROSS", "IN", "OUT", "POST", "GO", "CORNER", "FLAT", "SCREEN"]

EXPOSURE_COLS = ["nfl_id", "draft_year", "position", "position_group", "edp_pre", "log_edp_pre",
                 "edp_trend", "consensus_share", "n_snapshots", "has_primary", "has_trend",
                 "participation_pattern", "n_drills"]
ENVELOPE_COLS = ["nfl_id", "combine_position", "draft_year", "n_attempts", "n_fit_ok", "peak_speed",
                 "peak_speed_se", "speed_at_20yd", "speed_at_20yd_se", "env_vmax", "env_vmax_se"]
ATTEMPT_COLS = ["nfl_id", "event_id", "draft_year", "drill_type", "n_frames", "peak_speed",
                "speed_at_20yd", "env_vmax"]
COV_COLS = ["nfl_id", "position", "height", "weight", "age", "draft_position", "draft_year",
            "bmi", "optout"]
POOL_COLS = ["nfl_id", "position_group", "position", "edp_pre", "log_edp_pre", "edp_trend",
             "has_trend", "has_primary", "participation_pattern", "height", "weight",
             "log_combine_speed", "draft_year", "appeared"]
EFFORT_COLS = ["nfl_id", "combine_position", "game_id", "play_id", "route_ran", "pass_length",
               "target", "n_frames", "peak_speed", "peak_accel", "ceiling_speed_yds",
               "ceiling_accel_yds2", "max_over_ceiling", "accel_over_ceiling", "is_high_speed",
               "is_high_accel", "is_exceed"]
TOPK_COLS = ["nfl_id", "combine_position", "rank", "game_id", "play_id", "route_ran",
             "pass_length", "target", "peak_speed", "peak_accel", "max_over_ceiling", "is_exceed"]
SUM_COLS = ["nfl_id", "combine_position", "n_route_snaps", "eligible", "n_efforts_tracked",
            "ceiling_speed_yds", "n_exceed", "exceedance_rate", "topk_n", "topk_mean_speed",
            "topk_mean_envelope_fraction", "topk_max_envelope_fraction", "max_envelope_fraction"]
THR_COLS = ["method", "thresholds_provenance", "high_speed_threshold_yds", "accel_threshold_yds2",
            "high_speed_percentile", "accel_percentile", "top_k_efforts", "route_snap_count",
            "top_k_metric"]


def _r(x: float, nd: int = 6) -> float:
    return round(float(x), nd)


def _write(path: Path, cols: list[str], rows: list[dict], prov: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (["provenance"] if prov else []) + cols
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            d = ({"provenance": PROV} if prov else {})
            for c in cols:
                v = row.get(c)
                if isinstance(v, float) and not math.isfinite(v):
                    v = ""
                d[c] = v
            w.writerow(d)


def _clip(x: float, lo: float, hi: float) -> float:
    return float(min(max(x, lo), hi))


def _logistic(x: float) -> float:
    return float(1.0 / (1.0 + math.exp(-x)))


def build_fixture(out_dir: Path, cfg: dict, regime: str, seed: int) -> dict:
    """Generate one regime into ``out_dir``. Deterministic given ``(cfg, regime, seed)``."""
    s = cfg["security_models"]["synthetic"]
    rng = np.random.default_rng(int(seed))
    delta = {"planted": float(s["delta"]), "null": float(s["delta_null"]),
             "selection": float(s["delta_selection"])}[regime]
    # the continuous dose gradient is part of the planted signal: the null regime has neither
    gamma = 0.0 if regime == "null" else float(s["dose_gamma"])
    n_wr, n_te, n_edge = int(s["n_wr"]), int(s["n_te"]), int(s["n_edge"])
    mu, sd = float(s["log_edp_mu"]), float(s["log_edp_sd"])
    margin = float(cfg["audit"]["exceedance_margin_rel"])
    k_top, k_bottom = int(s["k_top"]), int(s["k_bottom"])

    # forced edge flags, by position inside the edge block
    edge_flags = ["one_effort", "low_snap", "no_game", "missing_cov", "no_trend", "no_primary",
                  "low_snap+no_game", "one_effort+missing_cov"]

    players: list[dict] = []
    for i in range(n_wr + n_te + n_edge):
        if i < n_wr:
            pos = "WR"
        elif i < n_wr + n_te:
            pos = "TE"
        else:
            pos = "WR" if (i - n_wr - n_te) % 2 == 0 else "TE"
        flags = set(edge_flags[i - n_wr - n_te].split("+")) if i >= n_wr + n_te else set()
        log_edp = _clip(float(rng.normal(mu, sd)), math.log(1.0), math.log(300.0))
        combine = _clip(float(rng.normal(s["combine_speed_mu"], s["combine_speed_sd"])), 7.5, 11.5)
        height = _clip(float(rng.normal(s["height_mu"], s["height_sd"])), 66.0, 80.0)
        weight = _clip(float(rng.normal(s["weight_mu"], s["weight_sd"])), 160.0, 260.0)
        players.append(dict(
            idx=i, nfl_id=900001 + i, position=pos, position_group=pos,
            log_edp_pre=log_edp, edp_pre=math.exp(log_edp),
            draft_year=2023 + (i % 3), combine=combine, height=height, weight=weight,
            flags=flags,
            has_primary=("no_primary" not in flags),
            has_trend=("no_trend" not in flags and "no_primary" not in flags
                       and float(rng.random()) < float(s["trend_share"])),
        ))

    # ---- pre-Combine EDP snapshots (the ONLY inputs to the sort key) ----
    corr = float(s["trend_level_corr"])
    for p in players:
        p["edp_trend"] = None
        if p["has_trend"]:
            z = (p["log_edp_pre"] - mu) / sd
            drift = float(s["trend_sd_edp"]) * (corr * z + math.sqrt(max(0.0, 1 - corr * corr))
                                                * float(rng.normal(0.0, 1.0)))
            edp_2wk = max(0.5, p["edp_pre"] - drift)
            p["edp_trend"] = p["edp_pre"] - edp_2wk

    # ---- sort key: the SAME shared implementation the stage uses ----
    pool = [dict(nfl_id=p["nfl_id"], position_group=p["position_group"], edp_pre=p["edp_pre"],
                 log_edp_pre=p["log_edp_pre"], edp_trend=p["edp_trend"],
                 has_trend=p["has_trend"], has_primary=p["has_primary"]) for p in players]
    assign = sf.assign_security_groups(pool, cfg)
    gmap = {int(a["nfl_id"]): a for a in assign["assignments"]}
    for p in players:
        p["group"] = gmap[p["nfl_id"]]["group"]
        p["stratum"] = gmap[p["nfl_id"]]["stratum"]

    # ---- participation pattern (skipping is NOT security) ----
    for p in players:
        if float(rng.random()) < float(s["skipper_rate"]):
            p["pattern"] = "cut_only"
            p["n_drills"] = 1
        elif float(rng.random()) < 0.05:
            p["pattern"] = "no_speed_drills"
            p["n_drills"] = 0
        else:
            p["pattern"] = "dash_and_cut"
            p["n_drills"] = 2

    # ---- appearance in game data ----
    for p in players:
        if "no_game" in p["flags"]:
            p["appeared"] = False
            continue
        if regime == "selection":
            z = (p["log_edp_pre"] - mu) / sd
            zspd = (p["combine"] - float(s["combine_speed_mu"])) / float(s["combine_speed_sd"])
            eta = (float(s["selection_intercept"]) + float(s["selection_log_edp_slope"]) * z
                   + float(s["selection_speed_slope"]) * zspd
                   + (float(s["selection_te_shift"]) if p["position"] == "TE" else 0.0))
            p["appeared"] = bool(float(rng.random()) < _logistic(eta))
        else:
            p["appeared"] = bool(float(rng.random()) >= float(s["absence_rate"]))

    # ---- outcomes ----
    for p in players:
        z = (p["log_edp_pre"] - mu) / sd
        moved = 1.0 if p["group"] == "moved_down" else 0.0
        base = float(s["base_gap_log"])
        if p["has_primary"]:
            gap = (base + delta * moved + gamma * (p["log_edp_pre"] - mu)
                   + float(rng.normal(0.0, s["gap_player_sd"])))
            # low-effort placebo: NO planted group delta and NO dose gradient (F2)
            gap_low = base + float(rng.normal(0.0, s["gap_player_sd"]))
        else:  # no pre-Combine snapshot: no group, no planted delta, no dose
            gap = base + float(rng.normal(0.0, s["gap_player_sd"]))
            gap_low = gap
        p["gap"], p["gap_low"] = gap, gap_low
        p["n_snaps"] = (int(s["low_snap_min"]) + int(rng.integers(0, int(s["low_snap_max"])
                                                                  - int(s["low_snap_min"]) + 1))
                        if "low_snap" in p["flags"]
                        else int(s["route_snaps_min"]) + int(rng.integers(
                            0, int(s["route_snaps_max"]) - int(s["route_snaps_min"]) + 1)))
        # observed (shrunken) Combine features carry a little measurement noise
        p["env_vmax"] = p["combine"] * math.exp(float(rng.normal(0.0, s["measurement_noise_sd"])))
        p["peak_speed"] = p["combine"] * math.exp(float(rng.normal(0.0, s["attempt_sd"])))
        p["speed_at_20yd"] = p["peak_speed"] * float(s["speed_at_20yd_ratio"])
        p["n_attempts"] = 1 if ("one_effort" in p["flags"]) else 2

    # ---- game-side tables (appeared players only) ----
    efforts: list[dict] = []
    topk: list[dict] = []
    summaries: list[dict] = []
    attempts: list[dict] = []
    for p in players:
        if not p["appeared"]:
            continue
        n_top = 1 if "one_effort" in p["flags"] else k_top
        n_bottom = 0 if "one_effort" in p["flags"] else k_bottom
        rows: list[dict] = []
        for j in range(n_top + n_bottom):
            is_top = j < n_top
            gap = p["gap"] if is_top else p["gap_low"] - float(s["bottom_gap_offset"])
            log_sp = math.log(p["combine"]) + gap + float(rng.normal(0.0, s["gap_noise_sd"]))
            peak = math.exp(log_sp)
            accel = float(rng.normal(28.0, 3.0))
            row = dict(
                nfl_id=p["nfl_id"], combine_position=p["position"],
                game_id=2025000000 + p["idx"] * 100 + j, play_id=100 + j,
                route_ran=ROUTES[(p["idx"] * 7 + j) % len(ROUTES)],
                pass_length=int(rng.integers(0, 31)), target=bool(j == 0 and is_top),
                n_frames=int(rng.integers(8, 20)),
                peak_speed=peak, peak_accel=max(1.0, accel),
                ceiling_speed_yds=p["env_vmax"], ceiling_accel_yds2=p["combine"] / 1.3,
                max_over_ceiling=peak / p["env_vmax"],
                accel_over_ceiling=max(1.0, accel) / (p["combine"] / 1.3),
                is_high_speed=False, is_high_accel=False,
                is_exceed=bool(peak > p["env_vmax"] * (1.0 + margin)),
                is_top=is_top,
            )
            rows.append(row)
        rows.sort(key=lambda r: (-r["peak_speed"], r["game_id"], r["play_id"]))
        # the emitted top-k is the true speed ranking (what Phase 3 would emit)
        for rank, row in enumerate(rows[:n_top], 1):
            topk.append(dict(row, rank=rank))
        for row in rows:
            efforts.append(row)
        n_eff = len(rows)
        n_exc = sum(1 for r in rows if r["is_exceed"])
        tk = rows[:n_top]
        fr = [r["max_over_ceiling"] for r in tk]
        summaries.append(dict(
            nfl_id=p["nfl_id"], combine_position=p["position"],
            n_route_snaps=p["n_snaps"],
            eligible=bool(p["n_snaps"] >= int(cfg["game"]["route_snap_count"])),
            n_efforts_tracked=n_eff, ceiling_speed_yds=p["env_vmax"], n_exceed=n_exc,
            exceedance_rate=_r(n_exc / n_eff), topk_n=len(tk),
            topk_mean_speed=_r(float(np.mean([r["peak_speed"] for r in tk]))),
            topk_mean_envelope_fraction=_r(float(np.mean(fr))),
            topk_max_envelope_fraction=_r(float(np.max(fr))),
            max_envelope_fraction=_r(float(np.max([r["max_over_ceiling"] for r in rows])))))
        # Combine attempts (best-minus-mean marker): a peaking marker correlated with the gap
        for a in range(p["n_attempts"]):
            sp = p["combine"] * math.exp(float(rng.normal(0.0, s["attempt_sd"]))
                                         + (0.35 * (p["gap"] - float(s["base_gap_log"]))
                                            if a == 0 else 0.0))
            attempts.append(dict(nfl_id=p["nfl_id"], event_id=f"SYN{p['nfl_id']:06d}-{a + 1}",
                                 draft_year=p["draft_year"], drill_type="FORTY_YARD_DASH",
                                 n_frames=45, peak_speed=_r(sp),
                                 speed_at_20yd=_r(sp * float(s["speed_at_20yd_ratio"])),
                                 env_vmax=_r(p["env_vmax"])))

    # ---- thresholds applied (descriptive cut-offs for the fixture) ----
    peaks = [r["peak_speed"] for r in efforts]
    accels = [r["peak_accel"] for r in efforts]
    thr = dict(method="percentile", thresholds_provenance=PROV,
               high_speed_threshold_yds=_r(float(np.quantile(peaks, float(
                   cfg["game"]["thresholds"]["high_speed_percentile"])))),
               accel_threshold_yds2=_r(float(np.quantile(accels, float(
                   cfg["game"]["thresholds"]["accel_percentile"])))),
               high_speed_percentile=cfg["game"]["thresholds"]["high_speed_percentile"],
               accel_percentile=cfg["game"]["thresholds"]["accel_percentile"],
               top_k_efforts=k_top, route_snap_count=cfg["game"]["route_snap_count"],
               top_k_metric="peak_speed")
    for r in efforts:
        r["is_high_speed"] = bool(r["peak_speed"] >= thr["high_speed_threshold_yds"])
        r["is_high_accel"] = bool(r["peak_accel"] >= thr["accel_threshold_yds2"])

    # ---- per-player envelopes, covariates, exposure, selection pool ----
    envelope, cov, exposure, sel_pool = [], [], [], []
    for p in players:
        bad_cov = "missing_cov" in p["flags"]
        envelope.append(dict(
            nfl_id=p["nfl_id"], combine_position=p["position"], draft_year=p["draft_year"],
            n_attempts=p["n_attempts"], n_fit_ok=p["n_attempts"],
            peak_speed=_r(p["peak_speed"]), peak_speed_se=0.0,
            speed_at_20yd=_r(p["speed_at_20yd"]), speed_at_20yd_se=0.0,
            env_vmax=_r(p["env_vmax"]),
            env_vmax_se=_r(0.10 + 0.02 * (p["idx"] % 4))))
        cov.append(dict(
            nfl_id=p["nfl_id"], position=p["position"],
            height=("" if bad_cov else _r(p["height"])),
            weight=("" if bad_cov else _r(p["weight"])),
            age=float(p["draft_year"] - 22), draft_position=f"R{1 + (p['idx'] % 7)}",
            draft_year=p["draft_year"],
            bmi=("" if bad_cov else _r(703.0 * p["weight"] / (p["height"] ** 2))),
            optout=bool(bad_cov)))
        exposure.append(dict(
            nfl_id=p["nfl_id"], draft_year=p["draft_year"], position=p["position"],
            position_group=p["position_group"],
            edp_pre=(_r(p["edp_pre"]) if p["has_primary"] else ""),
            log_edp_pre=(_r(p["log_edp_pre"]) if p["has_primary"] else ""),
            edp_trend=(_r(p["edp_trend"]) if p["edp_trend"] is not None else ""),
            consensus_share=_r(_clip(0.55 + 0.10 * (p["idx"] % 5) / 4.0, 0.0, 1.0)),
            n_snapshots=(3 if p["has_trend"] else (2 if p["has_primary"] else 0)),
            has_primary=p["has_primary"], has_trend=p["has_trend"],
            participation_pattern=p["pattern"], n_drills=p["n_drills"]))
        sel_pool.append(dict(
            nfl_id=p["nfl_id"], position_group=p["position_group"], position=p["position"],
            edp_pre=(_r(p["edp_pre"]) if p["has_primary"] else ""),
            log_edp_pre=(_r(p["log_edp_pre"]) if p["has_primary"] else ""),
            edp_trend=(_r(p["edp_trend"]) if p["edp_trend"] is not None else ""),
            has_trend=p["has_trend"], has_primary=p["has_primary"],
            participation_pattern=p["pattern"],
            height=("" if bad_cov else _r(p["height"])), weight=("" if bad_cov else _r(p["weight"])),
            log_combine_speed=_r(math.log(p["env_vmax"])), draft_year=p["draft_year"],
            appeared=p["appeared"]))

    # ---- write (sorted for byte-identical determinism) ----
    efforts.sort(key=lambda r: (r["nfl_id"], r["rank"] if "rank" in r else 0, r["game_id"],
                                r["play_id"]))
    topk.sort(key=lambda r: (r["nfl_id"], r["rank"]))
    summaries.sort(key=lambda r: r["nfl_id"])
    attempts.sort(key=lambda r: (r["nfl_id"], r["event_id"]))
    envelope.sort(key=lambda r: r["nfl_id"])
    cov.sort(key=lambda r: r["nfl_id"])
    exposure.sort(key=lambda r: r["nfl_id"])
    sel_pool.sort(key=lambda r: r["nfl_id"])

    _write(out_dir / "efforts.csv", EFFORT_COLS, efforts)
    _write(out_dir / "top_k_efforts.csv", TOPK_COLS, topk)
    _write(out_dir / "player_summary.csv", SUM_COLS, summaries)
    _write(out_dir / "thresholds_applied.csv", THR_COLS, [thr])
    _write(out_dir / "security_exposure.csv", EXPOSURE_COLS, exposure)
    _write(out_dir / "per_player_envelope.csv", ENVELOPE_COLS, envelope)
    _write(out_dir / "attempt_features.csv", ATTEMPT_COLS, attempts)
    _write(out_dir / "covariates.csv", COV_COLS, cov)
    _write(out_dir / "selection_pool.csv", POOL_COLS, sel_pool)
    _write(out_dir / "feature_reliability.csv", ["feature", "icc", "source"],
           [{"feature": f, "icc": v, "source": f"fixture:{regime}"}
            for f, v in dict(s["icc"]).items()], prov=False)

    meta = dict(
        provenance=PROV, regime=regime, seed=int(seed),
        design=dict(delta=delta, delta_planted=float(s["delta"]), dose_gamma=gamma,
                    base_gap_log=float(s["base_gap_log"]), n_wr=n_wr, n_te=n_te, n_edge=n_edge,
                    k_top=k_top, k_bottom=k_bottom, bottom_carries_delta=False,
                    gap_player_sd=float(s["gap_player_sd"]),
                    gap_noise_sd=float(s["gap_noise_sd"]),
                    selection=dict(intercept=float(s["selection_intercept"]),
                                   log_edp_slope=float(s["selection_log_edp_slope"]),
                                   speed_slope=float(s["selection_speed_slope"]),
                                   te_shift=float(s["selection_te_shift"]))
                    if regime == "selection" else None,
                    reliability=dict(s["icc"]),
                    note=("Signal convention: +delta on moved_down => a SMALLER gap => a POSITIVE "
                          "group coefficient (the incentive hypothesis). The brief's example "
                          "expression 'gap0 - delta' contradicts its own sign-convention paragraph; "
                          "the sign convention and the hypothesis govern (see decisions.md D39).")),
        ground_truth=dict(
            delta=delta,
            # population contrast (all players with a group; independent of appearance) -- the
            # target an IPW refit should move toward in the selection regime
            target_group_coef=(
                float(np.mean([p["gap"] for p in players if p["group"] == "moved_down"]))
                - float(np.mean([p["gap"] for p in players if p["group"] == "high_secure"]))
                if any(p["group"] == "moved_down" for p in players)
                and any(p["group"] == "high_secure" for p in players) else None),
            groups={str(p["nfl_id"]): p["group"] for p in players},
            strata={str(p["nfl_id"]): p["stratum"] for p in players},
            appeared=[p["nfl_id"] for p in players if p["appeared"]],
            edge_players={str(p["nfl_id"]): sorted(p["flags"]) for p in players if p["flags"]},
            counts=assign["counts"], cutoffs=assign["cutoffs"],
            trend_epsilon=assign["trend_epsilon"]))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return {"dir": out_dir, "meta": meta, "n_efforts": len(efforts), "n_topk": len(topk),
            "n_players": len(players), "n_appeared": sum(1 for p in players if p["appeared"])}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the synthetic game-side security fixture")
    ap.add_argument("--regime", choices=["planted", "null", "selection", "all"], default="all")
    ap.add_argument("--out", default=None, help="output root (default: fixtures/synthetic/security_game)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    common.seed_everything(cfg["seed"])
    s = cfg["security_models"]["synthetic"]
    root = Path(args.out) if args.out else common.resolve(cfg, "fixtures", s["root"])
    seed = int(args.seed) if args.seed is not None else int(cfg["seed"]) + int(s["seed_offset"])
    regimes = ["planted", "null", "selection"] if args.regime == "all" else [args.regime]
    for regime in regimes:
        res = build_fixture(root / regime, cfg, regime, seed)
        print(f"[{regime}] {res['n_players']} players ({res['n_appeared']} appeared), "
              f"{res['n_efforts']} efforts, {res['n_topk']} top-k -> {res['dir']}")


if __name__ == "__main__":
    main()
