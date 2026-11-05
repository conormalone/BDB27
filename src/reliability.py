"""reliability — per-player reliability of a mean get-off estimate.

This module answers one question and nothing else: **how much of a player's
observed mean get-off is signal rather than sampling error?** It is pure
arithmetic on per-player aggregates; it never reads a file and never fits a
Phase 4 model.

Three estimators, deliberately reported side by side because they can disagree
and the disagreement is itself the finding:

1. **Split-half across games** (model-free). Split each player's games into odd
   and even halves, take the mean get-off in each half, and correlate the two
   half means across players. The Pearson ``r`` is attenuated when a player's
   halves hold different numbers of reps, so the raw ``r`` is *not* a
   reliability estimate on its own; :func:`simulate_split_half_curve` shows how
   much attenuation to expect under a fitted model.

2. **Two-level model** (``k = sigma_e^2 / sigma_u^2``), the estimator
   ``TASK.md``/Phase 4 names::

       y_ij = mu + u_i + e_ij,   u_i ~ (0, s2u),  e_ij ~ (0, s2e)
       reliability_i = n_i / (n_i + k) = s2u / (s2u + s2e / n_i)

   Variance components are moment estimators (the same moments
   :func:`stats.hierarchical_normal` and :func:`stats.icc_oneway` use), so the
   three cannot disagree about the components.

3. **Three-level model** (reps nested in games nested in players)::

       y_igj = mu + u_i + v_ig + e_igj

   ``v_ig`` is a game-within-player effect. Reps inside one game share it, so
   they are not independent evidence; ignoring it overstates reliability. This
   is the estimator that reproduces the observed split-half curve
   (:func:`simulate_split_half_curve`). It is reported as a diagnostic and a
   proposal, not as the rule the task specified.

Every function is deterministic: randomness is confined to the two bootstrap /
simulation helpers, which take an explicit seed.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

import numpy as np


# --------------------------------------------------------------------------- #
# Aggregation: raw rows -> cells
# --------------------------------------------------------------------------- #
def player_cells(rows: Iterable[tuple[object, float]]) -> list[dict]:
    """Collapse ``(player, value)`` rows to one cell per player."""
    acc: dict[object, list[float]] = {}
    for pid, value in rows:
        if value is None:
            continue
        v = float(value)
        if math.isnan(v):
            continue
        a = acc.setdefault(pid, [0, 0.0, 0.0])
        a[0] += 1
        a[1] += v
        a[2] += v * v
    return [{"player": p, "n": a[0], "sum": a[1], "sumsq": a[2]}
            for p, a in acc.items()]


def game_cells(rows: Iterable[tuple[object, object, float]]) -> list[dict]:
    """Collapse ``(player, game, value)`` rows to one cell per player-game."""
    acc: dict[tuple[object, object], list[float]] = {}
    for pid, gid, value in rows:
        if value is None:
            continue
        v = float(value)
        if math.isnan(v):
            continue
        a = acc.setdefault((pid, gid), [0, 0.0, 0.0])
        a[0] += 1
        a[1] += v
        a[2] += v * v
    return [{"player": p, "game": g, "n": a[0], "sum": a[1], "sumsq": a[2]}
            for (p, g), a in acc.items()]


# --------------------------------------------------------------------------- #
# Variance components
# --------------------------------------------------------------------------- #
def _ms_pair(groups: Sequence[tuple[float, float, float]]) -> dict:
    """Between/within mean squares for ``(n, sum, sumsq)`` cells."""
    N = sum(g[0] for g in groups)
    P = len(groups)
    if P < 2 or N - P < 1:
        return {"N": N, "P": P, "msb": float("nan"), "msw": float("nan"),
                "n0": float("nan"), "ok": False}
    grand = sum(g[1] for g in groups) / N
    ssb = sum(g[0] * (g[1] / g[0] - grand) ** 2 for g in groups)
    ssw = sum(g[2] - g[1] ** 2 / g[0] for g in groups)
    n0 = (N - sum(g[0] ** 2 for g in groups) / N) / (P - 1)
    return {"N": N, "P": P, "grand": grand, "ssb": ssb, "ssw": ssw,
            "msb": ssb / (P - 1), "msw": ssw / (N - P), "n0": n0, "ok": True}


def two_level_components(cells: Sequence[Mapping]) -> dict:
    """Player/rep variance components and ``k = sigma_e^2 / sigma_u^2``.

    ``tau2`` is truncated at zero (a negative moment estimate means the data
    cannot distinguish the player means from pure noise). ``k`` is then
    infinite and every reliability is 0 — reported, never floored.
    """
    groups = [(float(c["n"]), float(c["sum"]), float(c["sumsq"]))
              for c in cells if float(c["n"]) > 0]
    ms = _ms_pair(groups)
    if not ms["ok"]:
        return {"ok": False, "n_obs": ms["N"], "n_players": ms["P"],
                "sigma2": float("nan"), "tau2": float("nan"),
                "k": float("inf"), "icc": float("nan"),
                "msb": float("nan"), "msw": float("nan"), "n0": float("nan")}
    s2e = ms["msw"]
    raw_tau2 = (ms["msb"] - s2e) / ms["n0"] if ms["n0"] > 0 else float("nan")
    tau2 = max(0.0, raw_tau2)
    if tau2 > 0:
        k = s2e / tau2
    else:
        k = float("inf")
    return {"ok": True, "n_obs": ms["N"], "n_players": ms["P"],
            "sigma2": s2e, "tau2": tau2, "tau2_raw": raw_tau2, "k": k,
            "icc": tau2 / (tau2 + s2e) if (tau2 + s2e) > 0 else float("nan"),
            "msb": ms["msb"], "msw": s2e, "n0": ms["n0"],
            "n_obs_per_player": ms["N"] / ms["P"], "grand": ms["grand"]}


def three_level_components(cells: Sequence[Mapping]) -> dict:
    """Player / game-within-player / rep variance components.

    Nested unbalanced ANOVA by the method of moments (Searle):

        E[MS_cell] = s2e
        E[MS_gw]   = s2e + c_b * s2v
        E[MS_p]    = s2e + c_a * s2v + n0_p * s2u

    with ``c_b`` and ``c_a`` the cell-count coefficients implied by the design.
    Components are truncated at zero; the number of truncated components is
    returned so a degenerate fit is visible rather than silent.
    """
    cell = {}
    for c in cells:
        key = (c["player"], c["game"])
        a = cell.setdefault(key, [0, 0.0, 0.0])
        a[0] += float(c["n"])
        a[1] += float(c["sum"])
        a[2] += float(c["sumsq"])
    if len(cell) < 3:
        return {"ok": False, "reason": "fewer than 3 player-game cells"}

    per_player: dict[object, list[float]] = {}
    per_player_sq: dict[object, float] = {}
    for (pid, _gid), (n, s, _ss) in cell.items():
        a = per_player.setdefault(pid, [0, 0.0])
        a[0] += n
        a[1] += s
        per_player_sq[pid] = per_player_sq.get(pid, 0.0) + n * n

    N = sum(v[0] for v in per_player.values())
    P = len(per_player)
    G = len(cell)
    if P < 2 or G - P < 1 or N - G < 1:
        return {"ok": False, "reason": "not enough games per player"}

    grand = sum(v[1] for v in per_player.values()) / N
    pmean = {p: v[1] / v[0] for p, v in per_player.items()}
    cmean = {(p, g): s / n for (p, g), (n, s, _ss) in cell.items()}

    ss_cell = sum(ss - s * s / n for (n, s, ss) in cell.values())
    ss_gw = sum(n * (cmean[(p, g)] - pmean[p]) ** 2
                for (p, g), (n, s, _ss) in cell.items())
    ss_p = sum(v[0] * (pmean[p] - grand) ** 2 for p, v in per_player.items())

    ms_cell = ss_cell / (N - G)
    ms_gw = ss_gw / (G - P)
    ms_p = ss_p / (P - 1)

    # Cell coefficients for the unbalanced nested design. Derivation (balanced
    # check: P players x b games x a reps must give c_b = c_a = a):
    #   E[MS_gw] = s2e + c_b * s2v,
    #       c_b = sum_games m_g (1 - m_g/n_player) / (G - P)
    #   E[MS_p]  = s2e + c_a * s2v + n0_p * s2u,
    #       c_a = sum_games (1 - n_player/N) * m_g^2/n_player / (P - 1)
    # Using N in place of n_player here is the classic mistake: it gives
    # c_a ~= N/(P-1) (hundreds) instead of ~a (a dozen), which drives s2u to
    # near zero and understates reliability by ~0.13.
    c_b = sum(n * (1.0 - n / per_player[p][0])
              for (p, _g), (n, _s, _ss) in cell.items()) / (G - P)
    c_a = sum((1.0 - per_player[p][0] / N) * n * n / per_player[p][0]
              for (p, _g), (n, _s, _ss) in cell.items()) / (P - 1)
    n0_p = (N - sum(v[0] ** 2 for v in per_player.values()) / N) / (P - 1)

    s2e = ms_cell
    s2v_raw = (ms_gw - s2e) / c_b if c_b > 0 else float("nan")
    s2v = max(0.0, s2v_raw)
    s2u_raw = ((ms_p - s2e - c_a * s2v) / n0_p) if n0_p > 0 else float("nan")
    s2u = max(0.0, s2u_raw)

    truncated = int(sum(1 for v in (s2e, s2v_raw, s2u_raw)
                        if not (v is not None and not math.isnan(v) and v > 0)))
    total = s2e + s2v + s2u
    return {"ok": True, "n_obs": N, "n_players": P, "n_cells": G,
            "sigma2_rep": s2e, "sigma2_game": s2v, "sigma2_player": s2u,
            "sigma2_game_raw": s2v_raw, "sigma2_player_raw": s2u_raw,
            "ms_cell": ms_cell, "ms_gw": ms_gw, "ms_player": ms_p,
            "c_b": c_b, "c_a": c_a, "n0_player": n0_p,
            "share_player": s2u / total if total > 0 else float("nan"),
            "share_game": s2v / total if total > 0 else float("nan"),
            "share_rep": s2e / total if total > 0 else float("nan"),
            "grand": grand, "components_truncated": truncated}


# --------------------------------------------------------------------------- #
# Reliability implied by the components
# --------------------------------------------------------------------------- #
def reliability_from_k(n: float, k: float) -> float:
    """``n / (n + k)`` — the two-level reliability of a mean of ``n`` reps."""
    if n is None or n <= 0:
        return float("nan")
    if k == float("inf"):
        return 0.0
    if not (k >= 0) or math.isnan(k):
        return float("nan")
    return n / (n + k)


def reliability_three_level(n: float, games: float, s2u: float, s2v: float,
                            s2e: float) -> float:
    """``s2u / (s2u + s2v/G + s2e/n)`` for a player with ``n`` reps in ``G`` games."""
    if n is None or n <= 0 or games is None or games <= 0:
        return float("nan")
    denom = s2u + s2v / games + s2e / n
    if denom <= 0:
        return float("nan")
    return s2u / denom


def rep_cut_for_reliability(k: float, target: float) -> float:
    """Smallest ``n`` with ``n/(n+k) >= target``: ``n* = k * target/(1-target)``.

    Reported so that the algebraic equivalence of a reliability rule and a raw
    rep rule (under a common ``k``) is a number, not an assertion.
    """
    if not (0 < target < 1) or k in (None, float("inf")) or math.isnan(k):
        return float("nan")
    return k * target / (1.0 - target)


# --------------------------------------------------------------------------- #
# Split-half across games
# --------------------------------------------------------------------------- #
def split_half_means(rows: Iterable[tuple[object, object, float]],
                     games_in_order: Mapping[object, Sequence[object]]) -> list[dict]:
    """Odd/even game means per player.

    ``games_in_order`` maps player -> that player's distinct games in
    chronological order (the caller sorts; this module never guesses an order).
    Half 0 is the 1st, 3rd, 5th ... game; half 1 is the 2nd, 4th, ... game.
    """
    index: dict[object, dict[object, int]] = {}
    for pid, seq in games_in_order.items():
        index[pid] = {g: i for i, g in enumerate(seq)}

    acc: dict[tuple[object, int], list[float]] = {}
    for pid, gid, value in rows:
        if value is None:
            continue
        v = float(value)
        if math.isnan(v):
            continue
        rank = index.get(pid, {}).get(gid)
        if rank is None:
            continue
        a = acc.setdefault((pid, rank % 2), [0, 0.0, 0.0])
        a[0] += 1
        a[1] += v
        a[2] += v * v

    per_player: dict[object, dict] = {}
    for (pid, half), (n, s, ss) in acc.items():
        rec = per_player.setdefault(pid, {"player": pid, "games": 0,
                                          "reps": 0, "halves": {}})
        rec["halves"][half] = {"n": n, "mean": s / n}
        rec["reps"] += n
    for pid, seq in games_in_order.items():
        if pid in per_player:
            per_player[pid]["games"] = len(seq)
    out = []
    for pid in sorted(per_player, key=str):
        rec = per_player[pid]
        h = rec["halves"]
        out.append({
            "player": pid, "games": rec["games"], "reps": rec["reps"],
            "n_half0": h.get(0, {}).get("n", 0),
            "n_half1": h.get(1, {}).get("n", 0),
            "mean_half0": h.get(0, {}).get("mean"),
            "mean_half1": h.get(1, {}).get("mean"),
            "estimable": (0 in h and 1 in h),
        })
    return out


def pearson_r(pairs: Sequence[tuple[float, float]]) -> float:
    """Pearson correlation; ``nan`` when it is not defined."""
    xs = np.asarray([p[0] for p in pairs], dtype=float)
    ys = np.asarray([p[1] for p in pairs], dtype=float)
    if len(xs) < 3:
        return float("nan")
    xs = xs - xs.mean()
    ys = ys - ys.mean()
    denom = math.sqrt(float((xs * xs).sum()) * float((ys * ys).sum()))
    return float((xs * ys).sum() / denom) if denom > 0 else float("nan")


def spearman_brown(r: float, n_items: int = 2) -> float:
    """Step a split-half correlation up to the reliability of the whole test."""
    if r is None or math.isnan(r) or (1 + (n_items - 1) * r) == 0:
        return float("nan")
    return n_items * r / (1 + (n_items - 1) * r)


def split_half_report(halves: Sequence[Mapping]) -> dict:
    """Correlations of the half means, overall and by minimum half size."""
    pairs_all = [(h["mean_half0"], h["mean_half1"]) for h in halves
                 if h["estimable"]]
    r = pearson_r(pairs_all)
    return {"n_players": len(pairs_all), "r": r,
            "spearman_brown": spearman_brown(r, 2)}


def split_half_strata(halves: Sequence[Mapping],
                      cuts: Sequence[int]) -> list[dict]:
    """Raw half-half correlation restricted to players with >= cut reps per half.

    Included because the raw correlation is an average over players whose
    halves differ in length by a factor of hundreds; the trend with ``cut`` is
    the attenuation signature, not a series of reliability estimates.
    """
    out = []
    for cut in cuts:
        sel = [h for h in halves
               if h["estimable"] and h["n_half0"] >= cut and h["n_half1"] >= cut]
        pairs = [(h["mean_half0"], h["mean_half1"]) for h in sel]
        r = pearson_r(pairs)
        med = (float(np.median([min(h["n_half0"], h["n_half1"]) for h in sel]))
               if sel else float("nan"))
        out.append({"min_reps_per_half": int(cut), "n_players": len(sel),
                    "r": r, "spearman_brown": spearman_brown(r, 2),
                    "median_min_reps_per_half": med})
    return out


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _rng(seed: int):
    return np.random.default_rng(int(seed))


def bootstrap_two_level(cells: Sequence[Mapping], n_resamples: int,
                        seed: int) -> dict:
    """Cluster bootstrap over players for ``s2e``, ``s2u`` and ``k``."""
    n = np.array([float(c["n"]) for c in cells])
    s = np.array([float(c["sum"]) for c in cells])
    ss = np.array([float(c["sumsq"]) for c in cells])
    keep = n > 0
    n, s, ss = n[keep], s[keep], ss[keep]
    P = len(n)
    if P < 3:
        return {"ok": False, "reason": "fewer than 3 players"}
    rng = _rng(seed)
    draws = {"sigma2": [], "tau2": [], "k": []}
    for _ in range(int(n_resamples)):
        idx = rng.integers(0, P, P)
        nn, ss_, sq = n[idx], s[idx], ss[idx]
        N = nn.sum()
        grand = ss_.sum() / N
        ssb = float((nn * (ss_ / nn - grand) ** 2).sum())
        ssw = float((sq - ss_ * ss_ / nn).sum())
        if N - P < 1:
            continue
        msb, msw = ssb / (P - 1), ssw / (N - P)
        n0 = float((N - (nn * nn).sum() / N) / (P - 1))
        tau2 = max(0.0, (msb - msw) / n0) if n0 > 0 else 0.0
        draws["sigma2"].append(msw)
        draws["tau2"].append(tau2)
        draws["k"].append(msw / tau2 if tau2 > 0 else float("inf"))
    return _summarise_draws(draws)


def bootstrap_three_level(cells: Sequence[Mapping], n_resamples: int,
                          seed: int) -> dict:
    """Cluster bootstrap over players for the three-level components."""
    # Build per-player cell blocks once; resampling a player takes all his games.
    blocks: dict[object, list[tuple[float, float, float]]] = {}
    for c in cells:
        blocks.setdefault(c["player"], []).append(
            (float(c["n"]), float(c["sum"]), float(c["sumsq"])))
    players = [p for p in blocks if blocks[p]]
    if len(players) < 3:
        return {"ok": False, "reason": "fewer than 3 players"}
    rng = _rng(seed)
    draws = {"sigma2_rep": [], "sigma2_game": [], "sigma2_player": [],
             "share_player": [], "truncated_game": 0, "truncated_player": 0}
    for _ in range(int(n_resamples)):
        pick = rng.integers(0, len(players), len(players))
        flat = []
        for draw, j in enumerate(pick):
            pid = players[int(j)]
            # A player drawn twice is two independent clusters, so the drawn
            # copy needs its own id; merging them would silently halve the
            # resampled player count and shrink every interval.
            key = (draw, pid)
            flat.extend((key, k, a, b, c) for k, (a, b, c)
                        in enumerate(blocks[pid]))
        comp = three_level_components(
            [{"player": f[0], "game": f[1], "n": f[2], "sum": f[3],
              "sumsq": f[4]} for f in flat])
        if not comp.get("ok"):
            continue
        draws["sigma2_rep"].append(comp["sigma2_rep"])
        draws["sigma2_game"].append(comp["sigma2_game"])
        draws["sigma2_player"].append(comp["sigma2_player"])
        draws["share_player"].append(comp["share_player"])
        if comp["sigma2_game_raw"] <= 0:
            draws["truncated_game"] += 1
        if comp["sigma2_player_raw"] <= 0:
            draws["truncated_player"] += 1
    out = _summarise_draws({k: v for k, v in draws.items()
                            if isinstance(v, list)})
    out["truncated_game"] = draws["truncated_game"]
    out["truncated_player"] = draws["truncated_player"]
    return out


def _percentile_ci(values: Sequence[float], lo: float = 2.5,
                   hi: float = 97.5) -> dict:
    v = np.asarray([x for x in values
                    if x is not None and not math.isnan(x)], dtype=float)
    if v.size == 0:
        return {"n": 0}
    return {"n": int(v.size), "mean": float(v.mean()),
            "median": float(np.median(v)),
            "p_lo": float(np.percentile(v, lo)),
            "p_hi": float(np.percentile(v, hi))}


def _summarise_draws(draws: Mapping[str, Sequence[float]]) -> dict:
    return {"ok": True,
            "intervals": {k: _percentile_ci(v) for k, v in draws.items()}}


# --------------------------------------------------------------------------- #
# Diagnostics: does the fitted model reproduce the split-half curve?
# --------------------------------------------------------------------------- #
def simulate_split_half_curve(game_cells_: Sequence[Mapping],
                              components: Mapping[str, float],
                              cuts: Sequence[int], n_sims: int, seed: int,
                              model: str = "three_level") -> list[dict]:
    """Parametric replication of the half-half correlation at each cut.

    Uses the real (player, game, reps) design, so unequal halves, missing
    games and small-n players are all carried over. If the fitted components
    are right, the simulated correlation at each cut matches the observed one;
    a systematic gap says the model is missing a variance component (this is
    how the rep-only model was caught overstating reliability).
    """
    cell = {}
    for c in game_cells_:
        key = (c["player"], c["game"])
        a = cell.setdefault(key, [0.0, 0.0, 0.0])
        a[0] += float(c["n"])
        a[1] += float(c["sum"])
        a[2] += float(c["sumsq"])
    players = sorted({p for p, _g in cell}, key=str)
    pidx = {p: i for i, p in enumerate(players)}
    games = sorted({g for _p, g in cell}, key=str)
    gidx = {g: i for i, g in enumerate(games)}
    # chronological rank within player = order of first appearance in game_cells_
    gorder: dict[object, list] = {}
    for c in game_cells_:
        lst = gorder.setdefault(c["player"], [])
        if c["game"] not in lst:
            lst.append(c["game"])
    rank = {(p, g): i for p, seq in gorder.items() for i, g in enumerate(seq)}

    rows = [(pidx[p], gidx[g], rank[(p, g)], n)
            for (p, g), (n, _s, _ss) in cell.items()]
    pp = np.array([r[0] for r in rows])
    gg = np.array([r[1] for r in rows])
    rk = np.array([r[2] for r in rows]) % 2
    nn = np.array([r[3] for r in rows])
    P = len(players)
    Gcells = len(games)

    # The caller normalises both models to the same three keys, so the
    # simulation never has to guess which component block it was handed.
    s2u = float(components["sigma2_player"])
    s2v = 0.0 if model == "two_level" else float(
        components.get("sigma2_game", 0.0))
    s2e = float(components["sigma2_rep"])

    rng = _rng(seed)
    out_m0 = {int(c): [] for c in cuts}
    out_m1 = {int(c): [] for c in cuts}
    n0 = np.zeros(P)
    n1 = np.zeros(P)
    np.add.at(n0, pp[rk == 0], nn[rk == 0])
    np.add.at(n1, pp[rk == 1], nn[rk == 1])

    for _ in range(int(n_sims)):
        u = rng.normal(0.0, math.sqrt(s2u), P)
        v = rng.normal(0.0, math.sqrt(s2v), Gcells) if s2v > 0 else np.zeros(Gcells)
        cellmean = u[pp] + v[gg] + rng.normal(0.0, 1.0, len(nn)) * np.sqrt(s2e / nn)
        s0 = np.zeros(P)
        s1 = np.zeros(P)
        np.add.at(s0, pp[rk == 0], cellmean[rk == 0] * nn[rk == 0])
        np.add.at(s1, pp[rk == 1], cellmean[rk == 1] * nn[rk == 1])
        with np.errstate(invalid="ignore", divide="ignore"):
            m0 = np.where(n0 > 0, s0 / n0, np.nan)
            m1 = np.where(n1 > 0, s1 / n1, np.nan)
        ok = (n0 > 0) & (n1 > 0)
        for cut in cuts:
            sel = ok & (n0 >= cut) & (n1 >= cut)
            if int(sel.sum()) > 3:
                out_m0[int(cut)].append(m0[sel])
                out_m1[int(cut)].append(m1[sel])

    out = []
    for cut in cuts:
        c = int(cut)
        if not out_m0[c]:
            out.append({"min_reps_per_half": c, "n_players": 0,
                        "r_predicted": float("nan"),
                        "r_lo": float("nan"), "r_hi": float("nan")})
            continue
        rs = [pearson_r(list(zip(a, b))) for a, b in zip(out_m0[c], out_m1[c])]
        rs = np.asarray([r for r in rs if not math.isnan(r)], dtype=float)
        out.append({"min_reps_per_half": c,
                    "n_players": int(len(out_m0[c][0])),
                    "r_predicted": float(rs.mean()),
                    "r_lo": float(np.percentile(rs, 2.5)),
                    "r_hi": float(np.percentile(rs, 97.5))})
    return out


# --------------------------------------------------------------------------- #
# Effective player count under unequal reliability
# --------------------------------------------------------------------------- #
def effective_players(reliabilities: Sequence[float]) -> float:
    """Kish effective sample size ``(sum r)^2 / sum r^2``.

    The ``z*sqrt(2/P)`` floor assumes every player is an equally informative,
    independent estimate. A player whose mean is 50% noise contributes about
    half a player to that count, so the honest denominator is this, not the
    raw headcount.
    """
    r = np.asarray([x for x in reliabilities
                    if x is not None and not math.isnan(x)], dtype=float)
    if r.size == 0:
        return float("nan")
    denom = float((r * r).sum())
    return float(r.sum() ** 2 / denom) if denom > 0 else 0.0
