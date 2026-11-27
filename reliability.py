"""How repeatable is a defensive back's turn? ICC(1), and what it means.

The 30-60 degree band is the cell a break test would target. For that band to be
useful as a player-level trait it has to be repeatable: a back's typical speed
retention through those breaks should look like himself from turn to turn. This
computes the one-way random-effects intraclass correlation, ICC(1), with the
player as the group, on apex retention (apex speed / entry speed).

ICC(1) is the ratio of the between-player variance component to the total: how
much of the variation is between players rather than within them. It is not the
observed share of variance that sits between players, which is a different and
always larger-looking number.

Two numbers are reported, and they answer different questions:

* every valid turn in the band, which is the population figure;
* turns entered at 3-5 yd/s, the speed window a short-area break drill is run
  at. That subset is also the one that is adjusted: apex retention is
  residualised on entry speed and turn angle (linear and squared) first, so the
  correlation cannot come from players simply meeting different kinds of turn.

Spearman-Brown then says what happens to the reliability of a player's *mean*
when he runs k turns instead of one: R = k*rho / (1 + (k-1)*rho). A band that is
barely repeatable on one turn can still be usable on the average of thirty.

Run ``python reliability.py`` after ``python turns.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from turns import load_turns, valid_turns

BAND_LO, BAND_HI = 30.0, 60.0
ENTRY_LO, ENTRY_HI = 3.0, 5.0
K_REPORT = 30.0            # turns per player at which reliability is quoted
TARGET_R = 0.70


def icc1(y, groups) -> dict:
    """One-way random-effects ICC(1), unbalanced design, from ANOVA moments.

    ``k0 = (N - sum(k_i^2)/N) / (g - 1)``, ``sigma2_a = (MSB - MSW)/k0``,
    ``sigma2_e = MSW``, ``ICC = sigma2_a / (sigma2_a + sigma2_e)``. When
    ``MSB < MSW`` the point estimate is reported as 0 with ``clamped=True``;
    that is a display convention, and the mean squares are reported unchanged
    beside it. Degenerate input returns a status, never an exception.
    """
    df = pd.DataFrame({"y": np.asarray(y, float), "g": np.asarray(groups)}).dropna()
    n_obs = int(len(df))
    grouped = df.groupby("g")["y"]
    k = grouped.size().to_numpy(float)
    mean = grouped.mean().to_numpy(float)
    g = int(len(k))
    N = float(k.sum())
    out = {"n_obs": n_obs, "n_groups": g, "kbar": float(k.mean()) if g else None,
           "icc": None, "clamped": False, "msb": None, "msw": None, "status": "ok"}
    if g < 2 or N <= g:
        out["status"] = ("not estimable: fewer than two players, or no "
                         "within-player degrees of freedom")
        return out
    grand = float(df["y"].mean())
    ss_between = float(np.sum(k * (mean - grand) ** 2))
    ss_within = float(sum(((sub - sub.mean()) ** 2).sum()
                          for _, sub in df.groupby("g")["y"]))
    msb = ss_between / (g - 1)
    msw = ss_within / (N - g)
    k0 = (N - np.sum(k ** 2) / N) / (g - 1)
    out.update({"msb": float(msb), "msw": float(msw), "k0": float(k0)})
    if k0 <= 0:
        out["status"] = "not estimable: k0 = 0"
        return out
    sigma2_a = (msb - msw) / k0
    out["clamped"] = bool(sigma2_a < 0)
    sigma2_a = max(sigma2_a, 0.0)
    denom = sigma2_a + msw
    out["icc"] = float(sigma2_a / denom) if denom > 0 else None
    return out


def residualise(frame: pd.DataFrame) -> tuple[np.ndarray, float]:
    """OLS residuals of apex retention on entry speed and angle, linear + squared."""
    y = frame["apex_retention"].to_numpy(float)
    es = frame["entry_speed"].to_numpy(float)
    ang = frame["abs_turn_angle"].to_numpy(float)
    X = np.column_stack([np.ones(len(frame)), es, es ** 2, ang, ang ** 2])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return resid, (1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"))


def spearman_brown(k: float, rho) -> float:
    if rho is None:
        return float("nan")
    return (k * rho) / (1.0 + (k - 1.0) * rho)


def k_for(target: float, rho) -> float:
    if not rho or rho <= 0:
        return float("nan")
    return target * (1.0 - rho) / (rho * (1.0 - target))


def _icc_text(result: dict) -> str:
    if result["icc"] is None:
        return f"not estimable ({result['status']})"
    return (f"{result['icc']:.4f}"
            + (" (clamped at 0: within-player variance exceeds between)"
               if result["clamped"] else ""))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    turns = load_turns()
    game = valid_turns(turns, "game")
    if game.empty:
        print("no game turns in the turn table. The 30-60 degree game band is "
              "where this reliability lives: the Combine side cannot supply it, "
              "because what is being measured is the game side's repetition.")
        print("Nothing was computed. Build the turn table against the full game "
              "tracking file, then re-run this script.")
        return 0

    band = game[(game["abs_turn_angle"] >= BAND_LO)
                & (game["abs_turn_angle"] < BAND_HI)]
    band = band.dropna(subset=["apex_retention", "entry_speed", "abs_turn_angle",
                               "nfl_id"])
    if band.empty:
        print(f"no game DB turns in the {BAND_LO:.0f}-{BAND_HI:.0f} degree band "
              f"(the game side has {len(game):,} valid turns)")
        return 0

    fast = band[(band["entry_speed"] >= ENTRY_LO) & (band["entry_speed"] <= ENTRY_HI)]
    all_icc = icc1(band["apex_retention"], band["nfl_id"])
    fast_icc = icc1(fast["apex_retention"], fast["nfl_id"]) if len(fast) else None
    adj_icc = None
    adj_r2 = float("nan")
    if len(fast):
        resid, adj_r2 = residualise(fast)
        adj_icc = icc1(resid, fast["nfl_id"])

    per_player = band.groupby("nfl_id").size()
    print(f"game DB turns in {BAND_LO:.0f}-{BAND_HI:.0f} degrees: "
          f"{len(band):,} turns from {band['nfl_id'].nunique():,} players "
          f"(median {per_player.median():.0f} per player, range "
          f"{per_player.min()}-{per_player.max()})")
    print(f"  ICC(1) of apex retention, all band turns: {_icc_text(all_icc)}")
    if all_icc["msb"] is not None:
        print(f"    mean squares between {all_icc['msb']:.6f}, within "
              f"{all_icc['msw']:.6f}; k0 = {all_icc['k0']:.2f}")

    if fast_icc is None:
        print(f"  entry speed {ENTRY_LO:g}-{ENTRY_HI:g} yd/s: no turns in the window")
        return 0

    print(f"  entry speed {ENTRY_LO:g}-{ENTRY_HI:g} yd/s: {len(fast):,} turns from "
          f"{fast['nfl_id'].nunique():,} players, mean {fast_icc['kbar']:.1f} turns "
          f"per player")
    print(f"    ICC(1): {_icc_text(fast_icc)}")
    print(f"    adjusted for entry speed and turn angle (linear and squared): "
          f"{_icc_text(adj_icc)}; the covariates explain {adj_r2 * 100:.1f}% of "
          "the variance in apex retention")

    rho = adj_icc["icc"] if adj_icc and adj_icc["icc"] is not None else fast_icc["icc"]
    print(f"  Spearman-Brown at k = {K_REPORT:.0f} turns per player: "
          f"R = {spearman_brown(K_REPORT, rho) * 100:.0f}% "
          f"(unadjusted ICC instead: {spearman_brown(K_REPORT, fast_icc['icc']) * 100:.0f}%)")
    print(f"  turns per player needed for R = {TARGET_R:.2f} at the adjusted "
          f"ICC: {k_for(TARGET_R, rho):.0f}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([
            {"measure": "icc1_all_band", "value": all_icc["icc"]},
            {"measure": "icc1_entry_3_5", "value": fast_icc["icc"]},
            {"measure": "icc1_entry_3_5_adjusted", "value": adj_icc["icc"]},
            {"measure": "adjustment_r2", "value": adj_r2},
            {"measure": "spearman_brown_k30_adjusted",
             "value": spearman_brown(K_REPORT, rho)},
            {"measure": "k_needed_for_R_0.70", "value": k_for(TARGET_R, rho)},
            {"measure": "band_turns", "value": len(band)},
            {"measure": "band_players", "value": band["nfl_id"].nunique()},
            {"measure": "entry_turns", "value": len(fast)},
        ]).to_csv(out, index=False)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
