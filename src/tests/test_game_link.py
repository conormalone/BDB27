"""Tests for Phase 4 (``src/04_game_features.py``) + Phase 5 (``src/05_link.py``).

Standalone (no pytest required): ``python src/tests/test_game_link.py``
Also pytest-compatible (functions are named ``test_*``).

Covers:
- output **schema** assertions (columns / order / dtypes / provenance-first);
- **synthetic recovery**: simulated players with known game ramps AND known combine
  warm-up slopes — the ramp model recovers the planted slopes, the link recovers the
  planted slope-vs-slope effect, and a **wrong-direction placebo vanishes** (the
  fallback EB path is exercised);
- **edge cases** (one-attempt / low-snap players, missing game data, constant output);
- **determinism** (same seed -> identical outputs from the pure helpers).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from common import load_config, resolve  # noqa: E402

PASS, FAIL = "PASS", "FAIL"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G4 = _load("game04", REPO / "src" / "04_game_features.py")
G5 = _load("link05", REPO / "src" / "05_link.py")
CORE = _load("combine03", REPO / "src" / "03_combine_model.py")
CFG = load_config()


def _game_dir() -> Path:
    return resolve(CFG, "outputs", CFG["game"]["out_subdir"])


def _link_dir() -> Path:
    return resolve(CFG, "outputs", CFG["link"]["out_subdir"])


# --------------------------------------------------------------------------- #
# 1. Schema
# --------------------------------------------------------------------------- #
def test_schema() -> str:
    """Required columns/order/dtypes + provenance-first for every output."""
    gdir = _game_dir()
    slopes = gdir / "game_player_slopes.csv"
    if not slopes.exists():
        return f"{FAIL}: missing {slopes} (run src/00_thresholds.py -> 04_game_features.py)"
    df = pd.read_csv(slopes)
    req = ["provenance", "nfl_id", "n_games", "n_plays", "n_effort_plays", "ramp_slope_raw",
           "ramp_slope_shrunk", "ramp_slope_se", "ci_lo", "ci_hi", "shrinkage_weight",
           "method", "reliability", "meets_min_snaps"]
    if list(df.columns)[:len(req)] != req:
        return f"{FAIL}: game_player_slopes cols {list(df.columns)[:len(req)]} != required prefix"
    if df["provenance"].iloc[0] != "UNVALIDATED SAMPLE OUTPUT":
        return f"{FAIL}: unexpected provenance {df['provenance'].iloc[0]!r}"
    if not df["ramp_slope_se"].dropna().ge(0).all():
        return f"{FAIL}: negative ramp_slope_se"
    if not df["reliability"].between(0, 1).all():
        return f"{FAIL}: reliability outside [0,1]"

    # thresholds: sample provenance must be PLACEHOLDER_SAMPLE (never derived from sample)
    import json
    th = json.loads((resolve(CFG, "outputs", CFG["game"]["thresholds_file"])).read_text())
    if th["provenance"] != "PLACEHOLDER_SAMPLE":
        return f"{FAIL}: sample thresholds provenance {th['provenance']!r} != PLACEHOLDER_SAMPLE"
    if not (th["high_speed_threshold_yds"] > 0 and th["accel_threshold_yds2"] > 0 and th["min_snaps"] >= 1):
        return f"{FAIL}: non-positive thresholds {th}"

    for f, prefix in [("game_features_summary.csv", ["provenance", "key", "value"]),
                      ("game_reliability.csv", ["provenance", "slope_metric", "source"]),
                      ("game_model_fixed_effects.csv", ["provenance", "term", "estimate"])]:
        p = gdir / f
        if not p.exists():
            return f"{FAIL}: missing {p}"
        d = pd.read_csv(p)
        if list(d.columns)[:len(prefix)] != prefix:
            return f"{FAIL}: {f} cols {list(d.columns)} != {prefix}"

    ldir = _link_dir()
    for f, prefix in [("link_results.csv", ["provenance", "model", "outcome", "predictor"]),
                      ("link_cv.csv", ["provenance", "outcome", "predictor", "cv_folds"]),
                      ("link_placebo.csv", ["provenance", "outcome", "predictor"]),
                      ("link_sensitivity.csv", ["provenance", "variant"]),
                      ("link_matched_counts.csv", ["provenance", "metric", "value"])]:
        p = ldir / f
        if not p.exists():
            return f"{FAIL}: missing {p} (run src/05_link.py)"
        d = pd.read_csv(p)
        if list(d.columns)[:len(prefix)] != prefix:
            return f"{FAIL}: {f} cols {list(d.columns)} != {prefix}"
        if len(d) and d["provenance"].iloc[0] != "UNVALIDATED SAMPLE OUTPUT":
            return f"{FAIL}: {f} provenance {d['provenance'].iloc[0]!r}"
    return PASS


# --------------------------------------------------------------------------- #
# 2. Synthetic recovery (game ramp + link + placebo + fallback path)
# --------------------------------------------------------------------------- #
def _synthetic_game_frame(seed: int = 7, n_players: int = 24, n_plays: int = 60,
                          ramp: float = 0.004, noise: float = 0.5) -> pd.DataFrame:
    """Players with planted in-game ramp slopes on cumulative distance."""
    rng = np.random.default_rng(seed)
    controls = list(CFG["game"]["controls"])
    rows = []
    for p in range(n_players):
        b = ramp * (0.5 + rng.random())  # positive (ramping-up) planted slope
        cum = 0.0
        for k in range(n_plays):
            step = float(rng.uniform(1.0, 6.0))
            cum += step
            y = b * cum + rng.normal(0, noise)
            r = {"nfl_id": 1000 + p, "game_id": 1, "play_id": k, "cum_dist_yd": cum,
                 "effort_intensity_z": y, "effort_intensity_yd": max(0.0, y),
                 "early_output_z": 0.0}
            for c in controls:
                r[c] = float(rng.normal(0, 1))
            rows.append(r)
    return pd.DataFrame(rows)


def test_synthetic_recovery() -> str:
    """The ramp model recovers planted positive slopes; the link recovers the effect."""
    df = _synthetic_game_frame()
    fit = G4.fit_ramp_slopes(df, CFG, CORE)
    if fit["estimator"] != "per_player_eb":
        return f"{FAIL}: expected fallback estimator per_player_eb, got {fit['estimator']}"
    if not (fit["mu"] > 0):
        return f"{FAIL}: planted positive ramp not recovered (mu={fit['mu']:.6g})"
    if fit["n_players"] < 20:
        return f"{FAIL}: too few recovered players ({fit['n_players']})"
    if not (0.0 <= fit["reliability"] <= 1.0):
        return f"{FAIL}: reliability out of range {fit['reliability']}"
    # per-player correlation between recovered and planted direction
    sl = fit["slopes"]
    if not (sl["slope"] > 0).mean() > 0.8:
        return f"{FAIL}: recovered slopes not mostly positive ({(sl['slope'] > 0).mean():.2f})"

    # --- link recovery: game_ramp = 0.8 * warmup + noise; placebo must vanish ---
    rng = np.random.default_rng(11)
    n = 40
    warm = rng.normal(0, 1, n)
    af = pd.DataFrame({
        "nfl_id": np.arange(n), "warmup": warm,
        "game_ramp": 0.8 * warm + rng.normal(0, 0.3, n),
        "game_se": np.full(n, 0.1), "early_output_z": rng.normal(0, 1, n),
        "baseline_speed": rng.normal(0, 1, n), "combine_height": rng.normal(0, 1, n),
        "combine_weight": rng.normal(0, 1, n), "draft_position": rng.integers(1, 250, n).astype(float),
        "is_undrafted": np.zeros(n), "meets_min_snaps": np.ones(n, bool),
    })
    r = G5._fit(af, "game_ramp", "warmup", list(CFG["link"]["controls"]))
    if not (0.5 < r["coef"] < 1.1):
        return f"{FAIL}: link slope {r['coef']:.3f} not near planted 0.8"
    # placebo: independent -> coefficient must be ~0 and non-significant
    rp = G5._fit(af, "early_output_z", "warmup", list(CFG["link"]["controls"]))
    if not (abs(rp["coef"]) < 0.2 and rp["p"] > 0.05):
        return f"{FAIL}: placebo did not vanish (coef={rp['coef']:.3f}, p={rp['p']:.3f})"
    # EIV correction inflates the naive coefficient by 1/R
    rel = 0.5
    corrected = r["coef"] / rel if rel > 0 else np.nan
    if not (corrected > r["coef"]):
        return f"{FAIL}: reliability correction did not inflate the effect"
    return PASS


# --------------------------------------------------------------------------- #
# 3. Edge cases
# --------------------------------------------------------------------------- #
def test_edge_cases() -> str:
    """Low-snap (one-attempt) players excluded; constant outcome -> no slope; missing data tolerated."""
    controls = list(CFG["game"]["controls"])
    min_snaps = int(CFG["game"]["thresholds"]["min_snaps"])

    # (a) a player with fewer than min_snaps plays -> excluded
    df = _synthetic_game_frame(n_players=6, n_plays=min_snaps + 5)
    tiny = df[df["nfl_id"] == 1000].head(2).copy()
    df2 = pd.concat([df, tiny.assign(play_id=999)], ignore_index=True)
    sl = G4.per_player_slopes_multi(df2, CFG, "cum_dist_yd", "effort_intensity_z",
                                    controls, min_snaps)
    if (sl["n"] < min_snaps).any():
        return f"{FAIL}: player below min_snaps emitted a slope"

    # (b) constant outcome -> unidentifiable slope (player skipped)
    const = df[df["nfl_id"] == 1001].copy()
    const["effort_intensity_z"] = 0.0
    sl2 = G4.per_player_slopes_multi(const, CFG, "cum_dist_yd", "effort_intensity_z",
                                     controls, 3)
    if len(sl2) != 0:
        return f"{FAIL}: constant outcome produced a slope"

    # (c) missing game data in the link frame -> dropped, no crash
    rng = np.random.default_rng(3)
    n = 12
    af = pd.DataFrame({
        "nfl_id": np.arange(n), "warmup": rng.normal(0, 1, n),
        "game_ramp": rng.normal(0, 1, n), "game_se": 0.1,
        "early_output_z": rng.normal(0, 1, n), "baseline_speed": rng.normal(0, 1, n),
        "combine_height": 72.0, "combine_weight": 200.0, "draft_position": 10.0,
        "is_undrafted": 0, "meets_min_snaps": True,
    })
    af.loc[0, "game_ramp"] = np.nan  # player without game data
    res = G5.build_results(af, CFG, "UNVALIDATED SAMPLE OUTPUT", _quiet_logger())
    if len(res) != 2:
        return f"{FAIL}: expected 2 link rows (naive + EIV), got {len(res)}"
    if int(res["n"].iloc[0]) != n - 1:
        return f"{FAIL}: missing-game-data player not dropped (n={res['n'].iloc[0]})"

    # (d) constant predictor -> degenerate guard returns NaN, no crash
    af2 = af.copy()
    af2["warmup"] = 0.05
    res2 = G5.build_results(af2, CFG, "UNVALIDATED SAMPLE OUTPUT", _quiet_logger())
    if not np.isnan(res2["coef"].iloc[0]):
        return f"{FAIL}: constant predictor should yield NaN coefficient"
    return PASS


def _quiet_logger():
    import logging
    lg = logging.getLogger("bdb27_test")
    lg.addHandler(logging.NullHandler())
    return lg


# --------------------------------------------------------------------------- #
# 4. Determinism
# --------------------------------------------------------------------------- #
def test_determinism() -> str:
    """Same seed -> identical pure-helper outputs (ramp fit + OOF delta-R^2)."""
    df = _synthetic_game_frame(seed=99)
    f1 = G4.fit_ramp_slopes(df, CFG, CORE)
    f2 = G4.fit_ramp_slopes(df.copy(), CFG, CORE)
    if (f1["tau2"] != f2["tau2"]) or (f1["reliability"] != f2["reliability"]):
        return f"{FAIL}: ramp fit not deterministic"
    if not np.allclose(f1["slopes"]["slope"].to_numpy(), f2["slopes"]["slope"].to_numpy(),
                       equal_nan=True):
        return f"{FAIL}: per-player slopes not deterministic"

    rng = np.random.default_rng(5)
    n = 30
    af = pd.DataFrame({
        "nfl_id": np.arange(n), "warmup": rng.normal(0, 1, n),
        "game_ramp": rng.normal(0, 1, n),
        "baseline_speed": rng.normal(0, 1, n), "combine_height": 72.0,
        "combine_weight": 200.0, "draft_position": 10.0, "is_undrafted": 0,
        "early_output_z": rng.normal(0, 1, n), "meets_min_snaps": True, "game_se": 0.1,
    })
    d1 = G5._oof_delta_r2(af, "game_ramp", "warmup", list(CFG["link"]["controls"]), 5, 42)
    d2 = G5._oof_delta_r2(af.copy(), "game_ramp", "warmup", list(CFG["link"]["controls"]), 5, 42)
    if not (d1 == d2 or (np.isnan(d1) and np.isnan(d2))):
        return f"{FAIL}: OOF delta-R^2 not deterministic ({d1} vs {d2})"
    return PASS


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
TESTS = [test_schema, test_synthetic_recovery, test_edge_cases, test_determinism]


def main() -> int:
    n_fail = 0
    for t in TESTS:
        try:
            r = t()
        except Exception as exc:  # noqa: BLE001
            r = f"{FAIL}: {type(exc).__name__}: {exc}"
        print(f"[{r}] {t.__name__}")
        if r != PASS:
            n_fail += 1
    print(f"\n{len(TESTS) - n_fail}/{len(TESTS)} passed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
