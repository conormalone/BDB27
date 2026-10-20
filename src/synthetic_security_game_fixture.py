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

* ``dose`` / ``dose_null`` / ``dose_kink`` / ``dose_capital`` (D50) — the **continuous** dose panel
  for ``src/10_dose_response.py``. The gap equation carries a planted linear slope on
  ``edp_trend`` (``dose``: −0.002 per EDP spot fallen; ``dose_null``: 0), a segmented slope
  (``dose_kink``: 0 below 5 spots, −0.006 per spot above 5 **in the mandated segmented basis**
  ``b1*d + b2*d*I(d>=5)`` — that basis carries a jump of ``+knot*b2`` at the knot, so the fixture
  plants the form the estimator actually nests and the recovery is exact rather than attenuated)
  or no direct dose effect at all
  (``dose_capital``: ``overall_pick`` drives the gap and ``edp_trend`` is generated **from** the
  pick, so the unadjusted slope is non-zero and the pick-adjusted slope is ≈ 0).

  **Legacy byte-identity.** The three legacy regimes are untouched: every new RNG draw lives inside
  a ``DOSE_REGIMES`` branch, ``has_trend``/``edp_trend``/``gap`` keep their original expressions for
  them, and the ``overall_pick`` column is written **only** for the four dose regimes. Regenerating
  ``planted``/``null``/``selection`` therefore reproduces the committed files byte-for-byte.

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
DOSE_REGIMES = ("dose", "dose_null", "dose_kink", "dose_capital")   # D50 dose panel
LEGACY_REGIMES = ("planted", "null", "selection")

EXPOSURE_COLS = ["nfl_id", "draft_year", "position", "position_group", "edp_pre", "log_edp_pre",
                 "edp_trend", "consensus_share", "n_snapshots", "has_primary", "has_trend",
                 "participation_pattern", "n_drills"]
ENVELOPE_COLS = ["nfl_id", "combine_position", "draft_year", "n_attempts", "n_fit_ok", "peak_speed",
                 "peak_speed_se", "speed_at_20yd", "speed_at_20yd_se", "env_vmax", "env_vmax_se"]
ATTEMPT_COLS = ["nfl_id", "event_id", "draft_year", "drill_type", "n_frames", "peak_speed",
                "speed_at_20yd", "env_vmax"]
COV_COLS = ["nfl_id", "position", "height", "weight", "age", "draft_position", "draft_year",
            "bmi", "optout"]
# D50: `overall_pick` is appended ONLY for the dose regimes, so the three legacy fixtures regenerate
# byte-identically (no extra column, no extra RNG draw).
COV_COLS_DOSE = COV_COLS + ["overall_pick"]
POOL_COLS = ["nfl_id", "position_group", "position", "edp_pre", "log_edp_pre", "edp_trend",
             "has_trend", "has_primary", "participation_pattern", "height", "weight",
             "log_combine_speed", "draft_year", "appeared"]
EFFORT_COLS = ["nfl_id", "combine_position", "game_id", "play_id", "season", "season_type",
               "route_ran", "pass_length",
               "target", "n_frames", "peak_speed", "peak_accel", "ceiling_speed_yds",
               "ceiling_accel_yds2", "max_over_ceiling", "accel_over_ceiling", "is_high_speed",
               "is_high_accel", "is_exceed"]
TOPK_COLS = ["nfl_id", "combine_position", "rank", "game_id", "play_id", "season", "season_type",
             "season_scope", "route_ran",
             "pass_length", "target", "peak_speed", "peak_accel", "max_over_ceiling", "is_exceed"]
SUM_COLS = ["nfl_id", "combine_position", "n_route_snaps", "eligible", "n_efforts_tracked",
            "ceiling_speed_yds", "n_exceed", "exceedance_rate", "topk_n", "topk_mean_speed",
            "topk_mean_envelope_fraction", "topk_max_envelope_fraction", "max_envelope_fraction"]
SNAP_COLS = ["nfl_id", "season", "season_type", "n_route_snaps"]
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
    dcfg = cfg["dose"]["fixture"]
    kink_at = float(cfg["dose"]["kink_at"])
    is_dose = regime in DOSE_REGIMES
    delta = (0.0 if is_dose else
             {"planted": float(s["delta"]), "null": float(s["delta_null"]),
              "selection": float(s["delta_selection"])}[regime])
    # the continuous dose gradient is part of the planted signal: the null regime has neither
    gamma = 0.0 if (regime == "null" or is_dose) else float(s["dose_gamma"])
    n_wr, n_te, n_edge = int(s["n_wr"]), int(s["n_te"]), int(s["n_edge"])
    mu, sd = float(s["log_edp_mu"]), float(s["log_edp_sd"])
    margin = float(cfg["audit"]["exceedance_margin_rel"])
    k_top, k_bottom = int(s["k_top"]), int(s["k_bottom"])
    round_size = int(dcfg["pick_round_size"])

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
            # D50: deterministic draft slot inside the same round band as `draft_position`
            # (no RNG draw — the legacy RNG stream is untouched).
            overall_pick=1 + (i % 7) * round_size + (i * 7) % round_size,
            flags=flags,
            has_primary=("no_primary" not in flags),
            has_trend=("no_trend" not in flags and "no_primary" not in flags
                       and float(rng.random()) < float(s["trend_share"])),
        ))

    # ---- pre-Combine EDP snapshots (the ONLY inputs to the sort key) ----
    corr = float(s["trend_level_corr"])
    if is_dose:
        # D50 regimes: a deterministic trend grid (no RNG at all), so the planted dose is exact and
        # the legacy stream is untouched. `dose_capital` instead derives the trend FROM the pick.
        for p in players:
            p["has_trend"] = bool(p["has_primary"]) and "no_trend" not in p["flags"]
        lo, hi = (float(dcfg["kink_trend_range"][0]), float(dcfg["kink_trend_range"][1])) \
            if regime == "dose_kink" else (float(dcfg["trend_range"][0]),
                                           float(dcfg["trend_range"][1]))
        nlev = int(dcfg["trend_levels"])
        mean_pick = float(np.mean([p["overall_pick"] for p in players]))
        for p in players:
            if not p["has_trend"]:
                p["edp_trend"] = None
                continue
            if regime == "dose_capital":
                p["edp_trend"] = (float(dcfg["capital_trend_per_pick"])
                                  * (float(p["overall_pick"]) - mean_pick)
                                  + float(rng.normal(0.0, float(dcfg["capital_trend_noise_sd"]))))
            else:
                k = (int(p["idx"]) * 13) % nlev
                p["edp_trend"] = lo + (hi - lo) * float(k) / float(nlev - 1)
    else:
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
    mean_pick_all = float(np.mean([p["overall_pick"] for p in players]))
    for p in players:
        z = (p["log_edp_pre"] - mu) / sd
        moved = 1.0 if p["group"] == "moved_down" else 0.0
        base = float(s["base_gap_log"])
        if is_dose:
            # D50: the planted dose lives in the gap equation, so the target equals the fitted slope.
            d = float(p["edp_trend"]) if p["edp_trend"] is not None else 0.0
            if regime == "dose":
                planted = float(dcfg["trend_slope"]) * d
            elif regime == "dose_null":
                planted = 0.0
            elif regime == "dose_kink":
                # The mandated estimator is `b1*d + b2*d*I(d >= knot)` (§3.5), whose above-knot
                # branch is `(b1+b2)*d` — i.e. it carries a jump of `+knot*b2` at the knot. Planting
                # the *continuous* `max(0, d-knot)` form would therefore be unrepresentable by the
                # mandated basis and would attenuate the recovered above-knot slope. The fixture
                # plants exactly the form the estimator nests: b2 * d * I(d >= knot).
                planted = float(dcfg["kink_slope_above"]) * d * (1.0 if d >= kink_at else 0.0)
            else:  # dose_capital: no direct dose effect — the PICK moves the gap
                planted = float(dcfg["capital_gap_per_pick"]) * (
                    float(p["overall_pick"]) - mean_pick_all)
            gap = base + planted + float(rng.normal(0.0, dcfg["dose_gap_player_sd"]))
            # low-effort placebo: never carries the planted dose (F2)
            gap_low = base + float(rng.normal(0.0, dcfg["dose_gap_player_sd"]))
        elif p["has_primary"]:
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
        p["env_vmax"] = p["combine"] * math.exp(
            float(rng.normal(0.0, (dcfg["dose_measurement_noise_sd"] if is_dose
                                   else s["measurement_noise_sd"]))))
        p["peak_speed"] = p["combine"] * math.exp(float(rng.normal(0.0, s["attempt_sd"])))
        p["speed_at_20yd"] = p["peak_speed"] * float(s["speed_at_20yd_ratio"])
        p["n_attempts"] = 1 if ("one_effort" in p["flags"]) else 2

    # ---- game-side tables (appeared players only) ----
    efforts: list[dict] = []
    topk: list[dict] = []
    summaries: list[dict] = []
    attempts: list[dict] = []
    snap_counts: list[dict] = []
    games_by_id: dict[int, dict] = {}
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
            # D49: date-shaped game ids + the TRUE season/season_type in games.csv. Top-k rows
            # are REG (the primary scope); one non-top row per player is PRE so the scope filter
            # is exercised on effort rows and snap counts without disturbing the planted signal.
            gid = 2024000000 + p["idx"] * 10 + j
            stype = "PRE" if j == n_top + n_bottom - 1 and n_bottom > 0 else "REG"
            games_by_id[gid] = dict(game_id=gid, game_key=80000 + len(games_by_id),
                                    season=2024, season_type=stype,
                                    week=(1 if stype == "PRE" else 1 + (j % 18)),
                                    home_team_abbr="SYN", visitor_team_abbr="OPP")
            row = dict(
                nfl_id=p["nfl_id"], combine_position=p["position"],
                game_id=gid, play_id=100 + j, season=2024, season_type=stype,
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
            # D49: one build carries both scopes; 07 selects the label it is running
            for scope_name in ("primary", "sensitivity"):
                topk.append(dict(row, rank=rank, season_scope=scope_name))
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
        # D49 snap counts: REG reproduces the historical denominator, PRE is additional. One
        # forced low-snap player is majority-PRE, so the primary scope fails `min_snaps` while
        # the sensitivity scope passes it (the gate moves with the scope, never silently).
        reg_snaps = int(p["n_snaps"])
        pre_snaps = 10 + (p["idx"] * 7) % 21
        if "low_snap" in p["flags"]:
            reg_snaps, pre_snaps = int(p["n_snaps"]), int(p["n_snaps"]) + 55
        snap_counts.append(dict(nfl_id=p["nfl_id"], season=2024, season_type="REG",
                                n_route_snaps=reg_snaps))
        snap_counts.append(dict(nfl_id=p["nfl_id"], season=2024, season_type="PRE",
                                n_route_snaps=pre_snaps))
        # keep the summary's denominator the PRIMARY scope count
        summaries[-1]["n_route_snaps"] = reg_snaps
        summaries[-1]["eligible"] = bool(reg_snaps >= int(cfg["game"]["route_snap_count"]))
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
            optout=bool(bad_cov), overall_pick=p["overall_pick"]))
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
    snap_counts.sort(key=lambda r: (r["nfl_id"], r["season_type"]))
    envelope.sort(key=lambda r: r["nfl_id"])
    cov.sort(key=lambda r: r["nfl_id"])
    exposure.sort(key=lambda r: r["nfl_id"])
    sel_pool.sort(key=lambda r: r["nfl_id"])

    _write(out_dir / "efforts.csv", EFFORT_COLS, efforts)
    _write(out_dir / "top_k_efforts.csv", TOPK_COLS, topk)
    _write(out_dir / "player_summary.csv", SUM_COLS, summaries)
    _write(out_dir / "route_snap_counts.csv", SNAP_COLS, snap_counts)
    _write(out_dir / "games.csv",
           ["game_id", "game_key", "season", "season_type", "week", "home_team_abbr",
            "visitor_team_abbr"], [games_by_id[g] for g in sorted(games_by_id)], prov=False)
    _write(out_dir / "thresholds_applied.csv", THR_COLS, [thr])
    _write(out_dir / "security_exposure.csv", EXPOSURE_COLS, exposure)
    _write(out_dir / "per_player_envelope.csv", ENVELOPE_COLS, envelope)
    _write(out_dir / "attempt_features.csv", ATTEMPT_COLS, attempts)
    # D50: the pick column exists only in the dose regimes (legacy fixtures stay byte-identical).
    _write(out_dir / "covariates.csv", COV_COLS_DOSE if is_dose else COV_COLS, cov)
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
            fall_threshold=assign["fall_threshold"],
            sensitivity_threshold=float(cfg["security_models"]["sort"]["sensitivity_threshold"])))
    if is_dose:
        # D50: the planted truth the dose tests assert against (never a finding).
        meta["design"]["dose"] = dict(
            regime=regime,
            trend_slope=(float(dcfg["trend_slope"]) if regime == "dose" else
                         (0.0 if regime == "dose_null" else None)),
            kink_at=kink_at if regime == "dose_kink" else None,
            kink_slope_above=(float(dcfg["kink_slope_above"])
                              if regime == "dose_kink" else None),
            capital_trend_per_pick=(float(dcfg["capital_trend_per_pick"])
                                    if regime == "dose_capital" else None),
            capital_gap_per_pick=(float(dcfg["capital_gap_per_pick"])
                                  if regime == "dose_capital" else None),
            trend_range=[float(dcfg["trend_range"][0]), float(dcfg["trend_range"][1])],
            has_overall_pick=True,
            note=("The dose is planted in the GAP equation (gap = log game speed - log ceiling), so "
                  "the fitted slope targets the planted value exactly. NEGATIVE = a player whose EDP "
                  "number rose (stock fell) realises LESS of the ceiling (D50 sign convention)."))
        meta["ground_truth"]["dose"] = dict(
            trend_slope=meta["design"]["dose"]["trend_slope"],
            kink_slope_above=meta["design"]["dose"]["kink_slope_above"],
            kink_at=meta["design"]["dose"]["kink_at"])
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return {"dir": out_dir, "meta": meta, "n_efforts": len(efforts), "n_topk": len(topk),
            "n_players": len(players), "n_appeared": sum(1 for p in players if p["appeared"])}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the synthetic game-side security fixture")
    ap.add_argument("--regime", choices=list(LEGACY_REGIMES) + list(DOSE_REGIMES) + ["all", "dose_all"],
                    default="all")
    ap.add_argument("--out", default=None, help="output root (default: fixtures/synthetic/security_game)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    common.seed_everything(cfg["seed"])
    s = cfg["security_models"]["synthetic"]
    root = Path(args.out) if args.out else common.resolve(cfg, "fixtures", s["root"])
    seed = int(args.seed) if args.seed is not None else int(cfg["seed"]) + int(s["seed_offset"])
    if args.regime == "all":
        regimes = list(LEGACY_REGIMES)
    elif args.regime == "dose_all":
        regimes = list(DOSE_REGIMES)
    else:
        regimes = [args.regime]
    for regime in regimes:
        res = build_fixture(root / regime, cfg, regime, seed)
        print(f"[{regime}] {res['n_players']} players ({res['n_appeared']} appeared), "
              f"{res['n_efforts']} efforts, {res['n_topk']} top-k -> {res['dir']}")


if __name__ == "__main__":
    main()
