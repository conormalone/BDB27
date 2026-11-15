"""Outcome test: separation at pass forward against speed retained through the break.

Sample construction and the estimator specified in ``notes/outcome-test-spec.md``.

Why elimination matching
------------------------
The frozen attribution rule (the single opposing route-runner within 3 yd of the
DB at the break frame, D197) needs ``(x, y)`` for both players at one timestamp.
Every tracking file on this machine is the 1,048,575-row truncation, so that rule
cannot be evaluated here at all. What *can* be built without coordinates is the
subset of turns whose play carries exactly one visible opposing route-runner: on
those turns elimination leaves one possible receiver, so the outcome column on
that receiver's row can be attached to the turning DB.

That is an **inference**, and a weaker one than the frozen rule: the single
visible opponent need not be the man the DB covered. Nothing in this module
treats it as a match; every function name and output says ``attributed``.

Estimator
---------
OLS of separation on ``apex_retention`` with controls, standard errors clustered
by player (CR1). Player fixed effects and the sensitivity set are in
``notes/outcome-test-spec.md`` §5 and are implemented here so the script and the
tests share one code path.

No function here samples at random; the only randomness is the fixed-seed wild
cluster bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import duckdb
import numpy as np
import pandas as pd

# ------------------------------------------------------------------ constants
BAND_LO, BAND_HI = 30.0, 60.0
TURN_KEY: tuple[str, ...] = ("game_id", "play_id", "nfl_id", "frame_start")

#: Positions kept as their own fixed-effect level. Anything else is pooled.
KNOWN_POSITIONS: tuple[str, ...] = ("CB", "SS", "FS")

#: Pre-declared effect-size floor: |beta| in yd per 1.00 unit of retention.
#: 2.0 yd/unit == 0.20 yd per 0.10 retention.
MIN_EFFECT_YD_PER_UNIT = 2.0

#: Bootstrap replications and seed, fixed by the spec.
BOOTSTRAP_REPS = 999
BOOTSTRAP_SEED = 20261114


# ------------------------------------------------------------------ sample
def connect(memory_limit: str = "1400MB", threads: int = 2) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'; SET threads={int(threads)}")
    return con


def _posix(path: str | Path) -> str:
    return Path(path).as_posix()


def build_sample(con: duckdb.DuckDBPyConnection,
                 turns_path: str | Path,
                 player_play_path: str | Path) -> pd.DataFrame:
    """One row per elimination-attributed 30-60 degree DB game turn.

    A turn is kept iff the play carries **exactly one** opposing-side
    ``player_play`` row with a non-``NA`` ``route_ran`` *and* that row carries
    ``separation_at_pass_forward``. Ties and unmatched turns are dropped, never
    resolved (no radius, distance or row order is used to choose).
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE pp_raw AS SELECT * FROM read_csv(
          '{_posix(player_play_path)}', header=true, all_varchar=true,
          nullstr=['NA',''])""")
    con.execute("""
        CREATE OR REPLACE TABLE pp AS
        SELECT CAST(game_id AS BIGINT) AS game_id,
               CAST(play_id AS BIGINT) AS play_id,
               CAST(nfl_id AS BIGINT) AS nfl_id,
               team_abbr, lined_up_position, route_ran,
               TRY_CAST(target AS BOOLEAN) AS target,
               TRY_CAST(separation_at_pass_forward AS DOUBLE) AS separation_at_pass_forward,
               TRY_CAST(cushion AS DOUBLE) AS cushion,
               coverage_assignment, TRY_CAST(down AS INTEGER) AS down,
               TRY_CAST(yards_to_go AS DOUBLE) AS yards_to_go,
               team_coverage_man_zone, possession_team, pass_result
        FROM pp_raw""")
    con.execute(f"""
        CREATE OR REPLACE TABLE band AS
        SELECT * FROM read_parquet('{_posix(turns_path)}')
        WHERE valid AND setting = 'game' AND position_group = 'DB'
          AND abs_turn_angle >= {BAND_LO} AND abs_turn_angle < {BAND_HI}""")
    con.execute("""
        CREATE OR REPLACE TABLE own AS
        SELECT game_id, play_id, nfl_id,
               MIN(team_abbr) FILTER (WHERE team_abbr IS NOT NULL) AS own_team,
               COUNT(DISTINCT team_abbr) AS n_own_teams
        FROM pp GROUP BY 1, 2, 3""")
    # Counted per *turn's* DB (o.nfl_id), never per candidate id.
    con.execute("""
        CREATE OR REPLACE TABLE cand AS
        SELECT o.game_id, o.play_id, o.nfl_id,
          COUNT(*) FILTER (WHERE c.team_abbr IS NOT NULL AND c.team_abbr <> o.own_team
                             AND c.route_ran IS NOT NULL) AS n_opp_route,
          COUNT(*) FILTER (WHERE c.team_abbr IS NOT NULL AND c.team_abbr <> o.own_team
                             AND c.route_ran IS NOT NULL
                             AND c.separation_at_pass_forward IS NOT NULL) AS n_opp_route_sep
        FROM own o
        JOIN pp c ON c.game_id = o.game_id AND c.play_id = o.play_id
                 AND c.nfl_id <> o.nfl_id
        GROUP BY 1, 2, 3""")
    con.execute("""
        CREATE OR REPLACE TABLE attributed AS
        SELECT b.game_id, b.play_id, b.nfl_id, b.frame_start, b.position,
               b.season, b.abs_turn_angle, b.entry_speed, b.apex_speed,
               b.apex_retention, b.speed_retention, b.plant, b.turn_type,
               o.own_team,
               k.n_opp_route, k.n_opp_route_sep,
               r.nfl_id AS receiver_nfl_id,
               r.lined_up_position AS receiver_position,
               r.route_ran AS receiver_route,
               r.separation_at_pass_forward AS separation_yd,
               r.target AS receiver_target,
               d.coverage_assignment, d.cushion, d.down, d.yards_to_go,
               d.team_coverage_man_zone, d.pass_result
        FROM band b
        JOIN own o ON o.game_id = b.game_id AND o.play_id = b.play_id
                  AND o.nfl_id = b.nfl_id
        JOIN cand k ON k.game_id = b.game_id AND k.play_id = b.play_id
                   AND k.nfl_id = b.nfl_id
        JOIN pp r ON r.game_id = b.game_id AND r.play_id = b.play_id
                 AND r.team_abbr IS NOT NULL AND r.team_abbr <> o.own_team
                 AND r.route_ran IS NOT NULL
                 AND r.separation_at_pass_forward IS NOT NULL
        LEFT JOIN pp d ON d.game_id = b.game_id AND d.play_id = b.play_id
                      AND d.nfl_id = b.nfl_id
        WHERE k.n_opp_route = 1 AND k.n_opp_route_sep = 1""")
    df = con.execute("""
        SELECT * FROM attributed
        ORDER BY game_id, play_id, nfl_id, frame_start""").df()
    return derive_columns(df)


def derive_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Analysis-ready columns, added in one place so every caller agrees."""
    out = df.copy()
    pos = out["position"].fillna("UNKNOWN")
    out["position_fe"] = np.where(pos.isin(KNOWN_POSITIONS), pos, "OTHER")

    cov = out["coverage_assignment"]
    out["coverage_family"] = np.select(
        [cov.eq("MAN"), cov.isna() | cov.isin(["NO_ASSIGNMENT", ""])],
        ["MAN", "MISSING"], default="ZONE")

    route = out["receiver_route"].fillna("UNKNOWN").astype(str)
    counts = route.value_counts()
    out["route_fe"] = np.where(route.map(counts).to_numpy(dtype=float) >= 20, route, "OTHER")
    out["season_fe"] = out["season"].astype("Int64").astype(str)
    out["one_turn_per_play"] = ~out.duplicated(subset=["game_id", "play_id"], keep=False)
    out["_play"] = out["game_id"].astype(str) + "-" + out["play_id"].astype(str)
    return out


def sample_summary(df: pd.DataFrame) -> dict:
    return {
        "n_turns": int(len(df)),
        "n_players": int(df["nfl_id"].nunique()),
        "n_plays": int(df[["game_id", "play_id"]].drop_duplicates().shape[0]),
        "n_seasons": int(df["season"].nunique()),
        "players_with_one_turn": int((df.groupby("nfl_id").size() == 1).sum()),
        "players_with_two_or_more_turns": int((df.groupby("nfl_id").size() >= 2).sum()),
        "median_turns_per_player": float(df.groupby("nfl_id").size().median()),
        "max_turns_per_player": int(df.groupby("nfl_id").size().max()),
        "position_counts": {k: int(v) for k, v in df["position_fe"].value_counts().items()},
        "coverage_counts": {k: int(v) for k, v in df["coverage_family"].value_counts().items()},
        "mean_separation_yd": round(float(df["separation_yd"].mean()), 4),
        "sd_separation_yd": round(float(df["separation_yd"].std(ddof=1)), 4),
        "mean_apex_retention": round(float(df["apex_retention"].mean()), 4),
        "sd_apex_retention": round(float(df["apex_retention"].std(ddof=1)), 4),
        "turns_with_cushion": int(df["cushion"].notna().sum()),
        "turns_one_turn_per_play": int(df["one_turn_per_play"].sum()),
        "attribution": "INFERENCE — elimination matching, not the frozen 3-yd rule",
    }


# ------------------------------------------------------------------ estimation
@dataclass
class Fit:
    name: str
    beta: float
    se: float
    lo: float
    hi: float
    p: float
    n: int
    n_clusters: int
    r2: float
    controls: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "spec": self.name, "beta_yd_per_unit_retention": round(self.beta, 4),
            "se_cluster": round(self.se, 4), "ci95_lo": round(self.lo, 4),
            "ci95_hi": round(self.hi, 4), "p_value": round(self.p, 5),
            "beta_per_0.10_retention_yd": round(self.beta * 0.10, 4),
            "n_turns": self.n, "n_clusters": self.n_clusters,
            "r2": round(self.r2, 4), "controls": "; ".join(self.controls),
            "note": self.note,
        }


def _dummies(s: pd.Series, drop_first: bool = True) -> np.ndarray:
    d = pd.get_dummies(s.astype(str), drop_first=drop_first, dtype=float)
    return d.to_numpy()


def design(df: pd.DataFrame, *, exposure: str = "apex_retention",
           player_fe: bool = False, route_fe: bool = False) -> tuple[np.ndarray, np.ndarray, list[str]]:
    cols: list[float] = []
    names: list[str] = []
    x = df[exposure].to_numpy(float)
    cols.append(x); names.append(exposure)
    es = df["entry_speed"].to_numpy(float)
    cols += [es, es ** 2]; names += ["entry_speed", "entry_speed_sq"]
    for col, prefix in (("position_fe", "pos"), ("season_fe", "season"),
                        ("coverage_family", "coverage")):
        d = _dummies(df[col])
        for j in range(d.shape[1]):
            cols.append(d[:, j])
            names.append(f"{prefix}_{sorted(df[col].astype(str).unique())[j + 1]}")
    if route_fe:
        d = _dummies(df["route_fe"])
        for j in range(d.shape[1]):
            cols.append(d[:, j])
            names.append(f"route_{sorted(df['route_fe'].astype(str).unique())[j + 1]}")
    if player_fe:
        d = _dummies(df["nfl_id"])
        for j in range(d.shape[1]):
            cols.append(d[:, j])
            names.append(f"player_{j}")
    X = np.column_stack([np.ones(len(df))] + cols)
    return X, df["separation_yd"].to_numpy(float), ["intercept"] + names


def cluster_ols(X: np.ndarray, y: np.ndarray, groups: np.ndarray) -> dict:
    """OLS with CR1 cluster-robust covariance on the first slope (index 1)."""
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    n, k = X.shape
    # A design with at least as many parameters as turns fits exactly: the
    # residual is zero, so the standard error is zero to machine precision and a
    # "95% CI" would print as a point. That is not precision, it is an
    # unidentified coefficient, so the interval is reported as not finite and the
    # notebook says the subset is too thin for one.
    identified = int(np.linalg.matrix_rank(X)) >= k and n > k
    # CR1: meat summed within clusters, small-sample factor G/(G-1) * (n-1)/(n-k)
    uniq, inv = np.unique(groups, return_inverse=True)
    G = len(uniq)
    scores = X * resid[:, None]
    meat = np.zeros((k, k))
    for g in range(G):
        s = scores[inv == g].sum(axis=0)
        meat += np.outer(s, s)
    dof = max(n - k, 1)
    scale = (G / max(G - 1, 1)) * ((n - 1) / dof)
    cov = scale * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    t = beta[1] / se[1] if se[1] > 0 else np.nan
    from scipy import stats
    p = float(2 * stats.t.sf(abs(t), df=G - 1)) if np.isfinite(t) else np.nan
    crit = float(stats.t.ppf(0.975, df=G - 1))
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    if not identified:
        return {"beta": float(beta[1]), "se": float("nan"), "lo": float("nan"),
                "hi": float("nan"), "p": float("nan"), "n": n, "n_clusters": G,
                "r2": float("nan"), "beta_all": beta, "identified": False}
    return {"beta": float(beta[1]), "se": float(se[1]),
            "lo": float(beta[1] - crit * se[1]), "hi": float(beta[1] + crit * se[1]),
            "p": p, "n": n, "n_clusters": G,
            "r2": 1 - ss_res / ss_tot if ss_tot else np.nan,
            "beta_all": beta, "identified": True}


def fit(df: pd.DataFrame, name: str, *, exposure: str = "apex_retention",
        player_fe: bool = False, route_fe: bool = False, cluster: str = "nfl_id",
        note: str = "") -> Fit:
    X, y, names = design(df, exposure=exposure, player_fe=player_fe, route_fe=route_fe)
    res = cluster_ols(X, y, df[cluster].to_numpy())
    if not res.get("identified", True):
        note = (note + "; " if note else "") + (
            f"coefficient not identified: {res['n']} turns, {X.shape[1]} parameters")
    return Fit(name=name, beta=res["beta"], se=res["se"], lo=res["lo"], hi=res["hi"],
               p=res["p"], n=res["n"], n_clusters=res["n_clusters"], r2=res["r2"],
               controls=[n for n in names if n not in ("intercept", exposure)][:8],
               note=note)


def wild_cluster_bootstrap(df: pd.DataFrame, *, reps: int = BOOTSTRAP_REPS,
                           seed: int = BOOTSTRAP_SEED) -> dict:
    """Restricted wild cluster bootstrap p-value for the primary slope.

    Null imposed: the exposure coefficient is zero. Residuals from the restricted
    model are re-signed by Rademacher weights drawn per player, and the
    unrestricted t statistic is recomputed on each replicate.
    """
    X, y, _ = design(df)
    groups = df["nfl_id"].to_numpy()
    uniq, inv = np.unique(groups, return_inverse=True)
    G = len(uniq)
    rng = np.random.default_rng(seed)

    res_full = cluster_ols(X, y, groups)
    t_obs = abs(res_full["beta"] / res_full["se"])

    Xr = np.delete(X, 1, axis=1)
    beta_r = np.linalg.pinv(Xr.T @ Xr) @ Xr.T @ y
    fit_r = Xr @ beta_r
    resid_r = y - fit_r

    exceed = 0
    for _ in range(reps):
        w = rng.choice(np.array([-1.0, 1.0]), size=G)
        y_star = fit_r + resid_r * w[inv]
        r = cluster_ols(X, y_star, groups)
        if r["se"] > 0 and abs(r["beta"] / r["se"]) >= t_obs:
            exceed += 1
    return {"reps": reps, "seed": seed, "t_obs": round(float(t_obs), 4),
            "p_wild_cluster": round((exceed + 1) / (reps + 1), 4),
            "n_clusters": G}


def run_all(df: pd.DataFrame) -> tuple[list[Fit], dict]:
    """The primary, the note's §5 form, and the pre-declared sensitivity set."""
    fits: list[Fit] = []
    fits.append(fit(df, "primary: OLS + controls, SE clustered by player"))
    fits.append(fit(df, "player fixed effects (within-player slope)",
                    player_fe=True, note="players with one turn do not contribute"))
    man = df[df["coverage_family"] == "MAN"]
    if len(man) > 40:
        fits.append(fit(man, "MAN-only subsample", note=f"n={len(man)}"))
    else:
        fits.append(Fit("MAN-only subsample", float("nan"), float("nan"),
                        float("nan"), float("nan"), float("nan"), len(man), 0,
                        float("nan"), note=f"n={len(man)}: too few turns to estimate"))
    one = df[df["one_turn_per_play"]]
    fits.append(fit(one, "one attributed turn per play", note=f"n={len(one)}"))
    pas = df[df["pass_result"].isin(["C", "I"])]
    fits.append(fit(pas, "pass plays only (C/I)", note=f"n={len(pas)}"))
    fits.append(fit(df, "speed_retention instead of apex_retention",
                    exposure="speed_retention", note="secondary exposure, never primary"))
    fits.append(fit(df, "route-family fixed effects added", route_fe=True,
                    note="post-snap control: a bad-control check, not the primary"))
    df_log = df.assign(separation_yd=np.log(df["separation_yd"].clip(lower=0.05)))
    fits.append(fit(df_log, "log(separation) outcome"))
    fits.append(fit(df, "primary, SE clustered by play instead of player",
                    cluster="_play"))
    boot = wild_cluster_bootstrap(df)
    return fits, boot


def diagnostics(df: pd.DataFrame) -> dict:
    """Positive controls: does the outcome move with things it must move with?

    If route family and coverage family did not shift separation, the pipeline
    would be dead and a null on retention would mean nothing. These are
    descriptive checks, not part of the decision rule.
    """
    def means(col: str, min_n: int = 20) -> dict:
        g = df.groupby(col)["separation_yd"].agg(["mean", "count"])
        g = g[g["count"] >= min_n].sort_values("mean", ascending=False)
        return {str(k): {"mean_yd": round(float(v["mean"]), 3), "n": int(v["count"])}
                for k, v in g.iterrows()}

    base = float(df["separation_yd"].std(ddof=1))
    # Share of outcome variance explained by the control block alone (no exposure),
    # which is the ceiling a dead-pipeline worry would be checked against.
    X, y, names = design(df)
    Xc = np.delete(X, 1, axis=1)
    b = np.linalg.pinv(Xc.T @ Xc) @ Xc.T @ y
    r2_controls = 1 - float(((y - Xc @ b) ** 2).sum()) / float(((y - y.mean()) ** 2).sum())
    return {
        "sd_separation_yd": round(base, 3),
        "separation_by_route_family": means("route_fe"),
        "separation_by_coverage_family": means("coverage_family"),
        "separation_by_position": means("position_fe"),
        "r2_of_control_block_without_exposure": round(r2_controls, 4),
        "note": "route and coverage move separation; the estimator is not fitted to a dead outcome",
    }


def verdict(fits: Sequence[Fit], boot: dict) -> dict:
    """Apply the spec's §6 decision rule verbatim, in order."""
    p = fits[0]
    fe = fits[1]
    man = fits[2]
    one = fits[3]

    ok_a = p.beta < 0
    ok_b = p.hi < 0
    ok_c = (fe.beta < 0) and (fe.hi < 0)
    ok_d = (man.beta < 0) and (one.beta < 0)
    ok_e = abs(p.beta) >= MIN_EFFECT_YD_PER_UNIT

    if ok_a and ok_b and ok_c and ok_d and ok_e:
        label = "FINDING"
    elif ok_a and ok_b and ok_c and ok_d:
        label = "DETECTABLE BUT TOO SMALL TO BE A FINDING"
    elif p.beta > 0:
        label = "ESTIMATE ONLY — OPPOSITE TO THE STATED EXPECTATION"
    else:
        label = "ESTIMATE ONLY — NO CLAIM"

    return {
        "label": label,
        "a_direction_negative": bool(ok_a),
        "b_primary_ci_excludes_zero": bool(ok_b),
        "c_player_fe_ci_excludes_zero": bool(ok_c),
        "d_sign_holds_in_man_and_one_per_play": bool(ok_d),
        "e_effect_at_least_2yd_per_unit": bool(ok_e),
        "effect_floor_yd_per_unit": MIN_EFFECT_YD_PER_UNIT,
        "primary_beta": round(p.beta, 4),
        "primary_ci95": [round(p.lo, 4), round(p.hi, 4)],
        "primary_p": round(p.p, 5),
        "wild_cluster_bootstrap_p": boot["p_wild_cluster"],
        "man_beta": round(man.beta, 4) if np.isfinite(man.beta) else None,
        "one_per_play_beta": round(one.beta, 4),
    }
