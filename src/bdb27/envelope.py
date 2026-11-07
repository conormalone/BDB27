"""Phase 3 — the turn envelope: lateral capacity and plant capacity.

Binding spec: ``TASK.md`` @ 52015cd, section **3. Turn envelope (capacity)**.

What is estimated
-----------------
* **Arc capacity** — the upper envelope of lateral acceleration demand as a function
  of apex speed, ``a_max(v) = a0 + a1 * (v - v_ref)`` (``linear_centred``). The
  friction-circle anchor ``sqrt(a_tan^2 + a_lat^2) <= a_max(v)`` is the physical
  reason to look at the *upper* envelope, and the total-demand form is fitted as a
  sensitivity (``form=friction_circle_upper``).
* **Plant capacity** — an upper envelope of speed retention and a lower envelope of
  time-to-regain, both linear-interaction surfaces in (entry speed, |turn angle|).
* **Feasible region** — the (entry speed x turn angle) cells whose predicted speed
  retention is at least ``feasibility.retention_tolerance``, with an observed /
  extrapolated split against the Combine entry-speed range.
* **Model-free cross-check** — per-bin upper quantiles and top-k means beside the
  model. Never used to fit, never used to choose anything.

Estimator
---------
Random-effects (partial-pooling) quantile regression. Per position group: fixed
intercept + slope, per player a Gaussian random intercept ``u_j`` with variance
``tau^2``. The asymmetric check loss is minimised by IRLS (numpy only):

    sum_i rho_tau(y_i - x_i'b - u_j) + sum_j u_j^2 / (2 tau^2)

Each IRLS step is the exact weighted-ridge solution for ``[beta, u]``. Because the
only random effect is an intercept, the normal equations reduce by Schur complement
to a ``p x p`` system (``p`` = number of fixed effects) after per-player sums, so a
fit costs ``O(n p^2 + J p^3)`` and hundreds of bootstrap replicates are cheap.

``tau^2`` is updated with the standard random-intercept EM moment
``mean(u^2) + mean(tau^2 / (1 + A_j tau^2))`` (``A_j`` = summed IRLS weight of
player ``j``), floored so it can never collapse to zero; if the moment is not
finite the configured design prior ``between_player_sd_prior`` is used and the fit
row says so (``prior_used``).

Uncertainty
-----------
A **player-weighted bootstrap** (``bootstrap_kind: player_weighted``): every
replicate draws one ``Exp(1)`` weight per player, applies it to all of that
player's turns, redraws each turn's demand / retention uniformly inside its
smoother min/max when available, and refits. Reported standard errors are the SD
across replicates and p05/p95 are their percentiles.

**These are bootstrap draws, not posterior draws.** Nothing in this module is a
Bayesian sampler, so no artefact may call them posterior. The manifest records
``uncertainty: player_weighted_bootstrap; posterior: false``.

Open decisions
--------------
D124 (segmentation rule) and D125 (drill geometry model) are OPEN with Conor. This
module reads neither, settles neither, and names both in the manifest and in the
per-player fit's ``open_decisions`` context. The primary capacity measure is
provisional: ``prereg``` decides it by audit reliability and counts, never by
outcomes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa

from . import common

STAGE = "03_envelope"

#: Columns the stage cannot run without (TASK.md Phase 3 inputs).
REQUIRED_COLUMNS = [
    "setting", "nfl_id", "player_name", "position", "position_group",
    "entry_speed", "exit_speed", "apex_speed", "turn_angle", "abs_turn_angle",
    "speed_retention", "regain_entry_speed_s", "regain_exhausted",
    "tangential_accel_entry", "tangential_accel_exit",
    "peak_lateral_accel_yd_s2", "lateral_accel_v2_over_r",
    "turn_type", "plant", "valid",
]

#: Smoother-to-smoother support columns. Missing ones are an EDGE CASE, not a
#: refusal: the fit uses primary values and the artefacts say the spread is absent.
SMOOTHER_COLUMNS = [
    "peak_lateral_accel_smoother_min", "peak_lateral_accel_smoother_max",
    "speed_retention_smoother_min", "speed_retention_smoother_max",
    "n_smoothers_matched",
]

#: Demand column for each configured ``envelope.capacity_demand`` value.
DEMAND_COLUMNS = {"peak_lateral_accel": "peak_lateral_accel_yd_s2"}

SETTINGS = ("combine", "game", "combined")


# --------------------------------------------------------------------- schemas

ENVELOPE_PLAYER_SCHEMA = pa.schema([
    ("setting", pa.string()),
    ("nfl_id", pa.int64()),
    ("player_name", pa.string()),
    ("position", pa.string()),
    ("position_group", pa.string()),
    ("n_turns", pa.int32()),
    ("n_capacity_turns", pa.int32()),
    ("n_plant", pa.int32()),
    ("n_arc", pa.int32()),
    ("n_invalid", pa.int32()),
    ("n_regain", pa.int32()),
    ("reliable", pa.bool_()),
    ("fully_pooled", pa.bool_()),
    ("single_setting", pa.bool_()),
    ("shrinkage_weight", pa.float64()),
    ("capacity_ref", pa.float64()),
    ("capacity_ref_se", pa.float64()),
    ("capacity_ref_p05", pa.float64()),
    ("capacity_ref_p95", pa.float64()),
    ("capacity_slope", pa.float64()),
    ("capacity_slope_se", pa.float64()),
    ("capacity_at_2", pa.float64()),
    ("capacity_at_2_se", pa.float64()),
    ("capacity_at_4", pa.float64()),
    ("capacity_at_4_se", pa.float64()),
    ("capacity_at_6", pa.float64()),
    ("capacity_at_6_se", pa.float64()),
    ("capacity_at_8", pa.float64()),
    ("capacity_at_8_se", pa.float64()),
    ("capacity_at_median_speed", pa.float64()),
    ("capacity_at_median_speed_se", pa.float64()),
    ("retention_ref", pa.float64()),
    ("retention_ref_se", pa.float64()),
    ("retention_entry_coef", pa.float64()),
    ("retention_entry_se", pa.float64()),
    ("retention_angle_coef", pa.float64()),
    ("retention_angle_se", pa.float64()),
    ("retention_interaction", pa.float64()),
    ("retention_interaction_se", pa.float64()),
    ("retention_source", pa.string()),
    ("regain_ref_s", pa.float64()),
    ("regain_ref_s_se", pa.float64()),
    ("regain_entry_coef", pa.float64()),
    ("regain_entry_se", pa.float64()),
    ("regain_angle_coef", pa.float64()),
    ("regain_angle_se", pa.float64()),
    ("regain_source", pa.string()),
    ("modelfree_q75_ref", pa.float64()),
    ("modelfree_q90_ref", pa.float64()),
    ("modelfree_top3_ref", pa.float64()),
    ("flags", pa.string()),
])

FEASIBLE_SCHEMA = pa.schema([
    ("setting", pa.string()),
    ("nfl_id", pa.int64()),
    ("player_name", pa.string()),
    ("position", pa.string()),
    ("position_group", pa.string()),
    ("cell_index", pa.int16()),
    ("entry_lo", pa.float64()),
    ("entry_hi", pa.float64()),
    ("angle_lo", pa.float64()),
    ("angle_hi", pa.float64()),
    ("entry_mid", pa.float64()),
    ("angle_mid", pa.float64()),
    ("observed", pa.bool_()),
    ("retention_pred", pa.float64()),
    ("retention_p05", pa.float64()),
    ("retention_p95", pa.float64()),
    ("p_inside", pa.float64()),
    ("inside", pa.bool_()),
    ("inside_prob", pa.bool_()),
    ("n_turns_cell", pa.int32()),
    ("n_turns_cell_player", pa.int32()),
])


# ------------------------------------------------------------------ small maths

def _seed(text: str, base: int) -> int:
    """Deterministic RNG seed from a label (same scheme as the Phase 2 null)."""
    digest = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    return int((base + digest) % (2 ** 32))


def _num(df: pd.DataFrame, col: str) -> np.ndarray:
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def _bool(df: pd.DataFrame, col: str) -> np.ndarray:
    return df[col].where(df[col].notna(), False).to_numpy(dtype=bool)


def _sd(values: np.ndarray) -> float:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    return float(np.std(v, ddof=1)) if len(v) > 1 else float("nan")


def _pct(values: np.ndarray, q: float) -> float:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    return float(np.percentile(v, q)) if len(v) else float("nan")


def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks (ties share the mean rank); no scipy dependency."""
    v = np.asarray(values, dtype=float)
    order = np.argsort(v, kind="stable")
    ranks = np.empty(len(v), dtype=float)
    ranks[order] = np.arange(1, len(v) + 1, dtype=float)
    # average ties
    uniq, inverse, counts = np.unique(v, return_inverse=True, return_counts=True)
    if len(uniq) < len(v):
        sums = np.bincount(inverse, weights=ranks)
        ranks = (sums / counts)[inverse]
    return ranks


def _corr(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Pearson and Spearman correlations; NaN when either side is constant."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan"), float("nan")
    pearson = float(np.corrcoef(x, y)[0, 1])
    spearman = float(np.corrcoef(_ranks(x), _ranks(y))[0, 1])
    return pearson, spearman


# ---------------------------------------------------- random-effects quantile fit

@dataclass
class REFit:
    """A fitted random-intercept quantile regression for one group."""

    beta: np.ndarray            # (p,) fixed effects
    u: np.ndarray               # (J,) random intercepts
    tau2: float
    prior_used: bool
    player_ids: np.ndarray      # (J,) nfl_id
    weight_mass: np.ndarray     # (J,) summed IRLS weight A_j
    n_obs: np.ndarray           # (J,) observation count
    n_iter: int
    converged: bool

    def index(self, nfl_id: int) -> int | None:
        hit = np.flatnonzero(self.player_ids == nfl_id)
        return int(hit[0]) if len(hit) else None

    def shrinkage(self) -> np.ndarray:
        """Share of a player's estimate that comes from his own data, in [0, 1]."""
        mass = self.weight_mass * self.tau2
        return mass / (1.0 + mass)


def _group_sums(X: np.ndarray, y: np.ndarray, gidx: np.ndarray, J: int,
                w: np.ndarray):
    """Per-player weighted cross-products, via bincount (no Python loop over rows)."""
    p = X.shape[1]
    A = np.bincount(gidx, weights=w, minlength=J)
    D = np.bincount(gidx, weights=w * y, minlength=J)
    B = np.empty((J, p))
    E = np.empty((J, p))
    for k in range(p):
        xk = X[:, k]
        B[:, k] = np.bincount(gidx, weights=w * xk, minlength=J)
        E[:, k] = np.bincount(gidx, weights=w * xk * y, minlength=J)
    C = np.zeros((J, p, p))
    for k in range(p):
        for l in range(k, p):
            v = np.bincount(gidx, weights=w * X[:, k] * X[:, l], minlength=J)
            C[:, k, l] = v
            C[:, l, k] = v
    return A, B, C, D, E


def _solve_re_irls(X: np.ndarray, y: np.ndarray, gidx: np.ndarray, J: int, *,
                   tau: float, tau2: float, base_w: np.ndarray, max_iter: int,
                   tol: float, eps: float) -> tuple[np.ndarray, np.ndarray, int, bool]:
    """One IRLS solve at a fixed ``tau2``. Returns (beta, u, iterations, converged)."""
    p = X.shape[1]
    lam = 1.0 / max(tau2, 1e-12)
    # start from the pooled weighted least squares fit
    XtWX = np.zeros((p, p))
    XtWy = np.zeros(p)
    for k in range(p):
        XtWy[k] = np.sum(base_w * X[:, k] * y)
        for l in range(k, p):
            XtWX[k, l] = XtWX[l, k] = np.sum(base_w * X[:, k] * X[:, l])
    try:
        beta = np.linalg.solve(XtWX + 1e-9 * np.eye(p), XtWy)
    except np.linalg.LinAlgError:  # pragma: no cover - degenerate design
        beta = np.zeros(p)
    u = np.zeros(J)
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        r = y - X @ beta - u[gidx]
        w = np.where(r > 0, tau, 1.0 - tau) / np.maximum(np.abs(r), eps)
        w = w * base_w
        A, B, C, D, E = _group_sums(X, y, gidx, J, w)
        d = A + lam
        # Schur-reduced p x p normal equations for beta
        M = np.zeros((p, p))
        for k in range(p):
            for l in range(k, p):
                v = float(np.sum(C[:, k, l] - B[:, k] * B[:, l] / d))
                M[k, l] = M[l, k] = v
        rhs = np.array([float(np.sum(E[:, k] - D * B[:, k] / d)) for k in range(p)])
        M = M + 1e-10 * (1.0 + abs(np.trace(M)) / max(p, 1)) * np.eye(p)
        try:
            beta_new = np.linalg.solve(M, rhs)
        except np.linalg.LinAlgError:  # pragma: no cover - degenerate design
            break
        if not np.all(np.isfinite(beta_new)):
            break
        u_new = (D - B @ beta_new) / d
        delta = max(float(np.max(np.abs(beta_new - beta), initial=0.0)),
                    float(np.max(np.abs(u_new - u), initial=0.0)))
        beta, u = beta_new, u_new
        if delta < tol:
            converged = True
            break
    return beta, u, it, converged


def fit_re_quantile(X: np.ndarray, y: np.ndarray, player_ids: np.ndarray, *,
                    tau: float, prior_sd: float, max_iter: int, tol: float,
                    eps: float, base_w: np.ndarray | None = None,
                    tau_floor_frac: float = 0.25) -> REFit:
    """Random-effects quantile regression with an EM update for ``tau^2``."""
    player_ids = np.asarray(player_ids, dtype=np.int64)
    uniq, gidx = np.unique(player_ids, return_inverse=True)
    J = len(uniq)
    n = len(y)
    if base_w is None:
        base_w = np.ones(n)
    base_w = np.asarray(base_w, dtype=float)
    tau2 = float(prior_sd) ** 2
    prior_used = False
    floor = (tau_floor_frac * float(prior_sd)) ** 2
    cap = (10.0 * float(prior_sd)) ** 2
    it_total = 0
    converged = False
    beta = np.zeros(X.shape[1])
    u = np.zeros(J)
    for _ in range(int(max_iter)):
        beta, u, it, conv = _solve_re_irls(
            X, y, gidx, J, tau=tau, tau2=tau2, base_w=base_w,
            max_iter=int(max_iter), tol=tol, eps=eps)
        it_total += it
        converged = conv
        # summed weight mass per player at the solution (for shrinkage / EM)
        r = y - X @ beta - u[gidx]
        w = np.where(r > 0, tau, 1.0 - tau) / np.maximum(np.abs(r), eps) * base_w
        A = np.bincount(gidx, weights=w, minlength=J)
        moment = float(np.mean(u ** 2)) + float(np.mean(tau2 / (1.0 + A * tau2)))
        if not np.isfinite(moment):
            prior_used = True
            tau2_new = float(prior_sd) ** 2
        else:
            tau2_new = min(max(moment, floor), cap)
        if abs(tau2_new - tau2) <= max(tol, 1e-9) * max(1.0, tau2):
            tau2 = tau2_new
            break
        tau2 = tau2_new
    # final pass at the settled variance so beta/u and A agree
    beta, u, it, conv = _solve_re_irls(
        X, y, gidx, J, tau=tau, tau2=tau2, base_w=base_w,
        max_iter=int(max_iter), tol=tol, eps=eps)
    it_total += it
    converged = converged or conv
    r = y - X @ beta - u[gidx]
    w = np.where(r > 0, tau, 1.0 - tau) / np.maximum(np.abs(r), eps) * base_w
    A = np.bincount(gidx, weights=w, minlength=J)
    if not np.all(np.isfinite(beta)) or not np.all(np.isfinite(u)):
        raise common.StageRefused(
            f"{STAGE}: quantile regression diverged (non-finite coefficients)")
    return REFit(beta=beta, u=u, tau2=float(tau2), prior_used=prior_used,
                 player_ids=uniq, weight_mass=A,
                 n_obs=np.bincount(gidx, minlength=J).astype(float),
                 n_iter=int(it_total), converged=bool(converged))


# ------------------------------------------------------------------- data prep

def check_columns(turns: pd.DataFrame) -> tuple[bool, list[str]]:
    """Refuse on a missing required column; report missing smoother support."""
    missing = [c for c in REQUIRED_COLUMNS if c not in turns.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: the turn table is missing required columns: "
            + ", ".join(missing))
    missing_smoother = [c for c in SMOOTHER_COLUMNS if c not in turns.columns]
    return (not missing_smoother), missing_smoother


def load_turns(path: Path) -> pd.DataFrame:
    """Read the turn table with an explicit column list (never ``SELECT *``)."""
    import pyarrow.parquet as pq

    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the per-turn table is absent ({path}); run 02_turns first")
    names = set(pq.ParquetFile(path).schema_arrow.names)
    wanted = [c for c in REQUIRED_COLUMNS if c in names]
    wanted += [c for c in SMOOTHER_COLUMNS if c in names]
    df = pd.read_parquet(path, columns=wanted)
    if len(df) == 0:
        raise common.StageRefused(f"{STAGE}: the per-turn table has zero rows ({path})")
    check_columns(df)
    return df


def _smoother_draw(rng: np.random.Generator, primary: np.ndarray,
                   lo: np.ndarray | None, hi: np.ndarray | None) -> np.ndarray:
    """Uniform draw inside the smoother min/max where both exist, else primary."""
    out = primary.copy()
    if lo is None or hi is None:
        return out
    ok = np.isfinite(lo) & np.isfinite(hi) & (hi >= lo)
    if ok.any():
        a = np.minimum(lo[ok], primary[ok])
        b = np.maximum(hi[ok], primary[ok])
        out[ok] = a + (b - a) * rng.random(int(ok.sum()))
    return out


def _demand(frame: pd.DataFrame, cfg: common.Config, form: str) -> np.ndarray:
    col = DEMAND_COLUMNS[str(cfg.get("envelope.capacity_demand"))]
    lateral = _num(frame, col)
    if form == "friction_circle_upper":
        tan = np.maximum(np.abs(_num(frame, "tangential_accel_entry")),
                         np.abs(_num(frame, "tangential_accel_exit")))
        tan = np.where(np.isfinite(tan), tan, 0.0)
        total = np.sqrt(np.maximum(lateral, 0.0) ** 2 + tan ** 2)
        return total
    return lateral


def _capacity_mask(frame: pd.DataFrame, cfg: common.Config, demand: np.ndarray
                   ) -> tuple[np.ndarray, dict[str, int]]:
    """TASK.md capacity-observation validity rules, with counts by reason."""
    valid = _bool(frame, "valid")
    apex = _num(frame, "apex_speed")
    finite = np.isfinite(demand) & np.isfinite(apex)
    floor = float(cfg.get("envelope.apex_speed_floor"))
    cap = float(cfg.get("envelope.winsor_lateral_yd_s2"))
    invalid = ~valid
    nonfinite = valid & ~finite
    below = valid & finite & (apex < floor)
    out_range = valid & finite & (apex >= floor) & ((demand <= 0) | (demand > cap))
    kept = valid & finite & (apex >= floor) & (demand > 0)
    counts = {
        "invalid": int(invalid.sum()),
        "below_apex_floor": int(below.sum()),
        "nonfinite": int(nonfinite.sum()),
        "out_of_range": int(out_range.sum()),
        "winsorised": int(((demand > cap) & kept).sum()),
    }
    return kept, counts


def _retention_mask(frame: pd.DataFrame, cfg: common.Config) -> np.ndarray:
    angle = _num(frame, "abs_turn_angle")
    ret = _num(frame, "speed_retention")
    return (_bool(frame, "valid") & np.isfinite(ret) & np.isfinite(angle)
            & (angle >= float(cfg.get("detector.min_turn_angle_deg"))))


def _regain_mask(frame: pd.DataFrame, cfg: common.Config) -> np.ndarray:
    t = _num(frame, "regain_entry_speed_s")
    exhausted = _bool(frame, "regain_exhausted")
    return (_bool(frame, "valid") & np.isfinite(t) & ~exhausted
            & np.isfinite(_num(frame, "abs_turn_angle")))


# ------------------------------------------------------------------ fit tables

@dataclass
class GroupFit:
    """Everything a group fit produced, point estimates plus bootstrap draws."""

    label: str
    player_ids: np.ndarray
    fit: REFit
    beta_reps: np.ndarray | None = None       # (R, p)
    u_reps: np.ndarray | None = None          # (R, J)
    prior_used_reps: np.ndarray | None = None


@dataclass
class EnvelopeResult:
    players: pd.DataFrame
    fit: pd.DataFrame
    modelfree: pd.DataFrame
    feasible: pd.DataFrame
    feasible_summary: pd.DataFrame
    manifest: dict
    diagnostics: dict = field(default_factory=dict)


# ------------------------------------------------------------------- the stage

class Envelope:
    """Fit the Phase 3 envelope for every setting present in the turn table."""

    def __init__(self, turns: pd.DataFrame, cfg: common.Config, *,
                 quantile: float | None = None, with_bootstrap: bool = True,
                 seed_extra: int = 0, settings: Iterable[str] = SETTINGS):
        self.cfg = cfg
        self.with_bootstrap = with_bootstrap
        self.quantile = float(quantile if quantile is not None
                              else cfg.get("envelope.quantile"))
        self.seed_extra = int(seed_extra)
        self.smoother_spread, self.missing_smoother = check_columns(turns)
        self.turns = turns.reset_index(drop=True)
        self.settings = [s for s in settings]
        self.players_frames = {
            "combine": self.turns[self.turns.setting == "combine"].reset_index(drop=True),
            "game": self.turns[self.turns.setting == "game"].reset_index(drop=True),
        }
        self.players_frames["combined"] = pd.concat(
            [f for f in (self.players_frames["combine"], self.players_frames["game"])
             if len(f)], ignore_index=True)
        # combined is the union; when only one setting exists it is not a second one
        self._settings_fitted: list[str] = []
        # per-setting models
        self.capacity: dict[str, dict[str, GroupFit]] = {}
        self.retention: dict[str, dict[str, GroupFit]] = {}
        self.regain: dict[str, dict[str, GroupFit]] = {}
        self.player_capacity_group: dict[str, dict[int, str]] = {}
        self.player_retention_group: dict[str, dict[int, str]] = {}
        self.player_capacity_median: dict[str, dict[int, float]] = {}
        self.group_sizes: dict[str, dict[str, int]] = {}
        self.exclusion: dict[str, dict[str, int]] = {}
        self.range_combine: tuple[float, float] | None = None
        self.warnings: list[str] = []
        self._feature_cache: dict[str, dict] = {}

    # -- helpers ---------------------------------------------------------------
    def _sub(self, setting: str) -> pd.DataFrame:
        return self.players_frames[setting]

    def _tau2_prior(self) -> float:
        return float(self.cfg.get("envelope.between_player_sd_prior"))

    def _assign_groups(self, setting: str, mask: np.ndarray, min_turns: int,
                       frame: pd.DataFrame, setting_label: str) -> tuple[dict[int, str], dict[str, int]]:
        """Position groups with at least ``min_turns`` rows; everything else ALL."""
        grp = frame["position_group"].where(frame["position_group"].notna(), "OTHER")
        grp = grp.astype(str)
        counts = pd.Series(grp[mask]).value_counts()
        mapping = {g: (g if int(n) >= min_turns else "ALL") for g, n in counts.items()}
        sizes = {g: int(n) for g, n in counts.items()}
        player_group = {}
        for nid, g in zip(_num(frame, "nfl_id").astype(np.int64), grp):
            player_group[int(nid)] = mapping.get(g, "ALL")
        return player_group, sizes

    def _fit_group(self, label: str, X: np.ndarray, y: np.ndarray,
                   players: np.ndarray, setting: str, model: str,
                   tau: float) -> GroupFit:
        fit = fit_re_quantile(
            X, y, players,
            tau=tau, prior_sd=self._tau2_prior(),
            max_iter=int(self.cfg.get("envelope.em_max_iter")),
            tol=float(self.cfg.get("envelope.em_tol")),
            eps=float(self.cfg.get("envelope.irls_eps")))
        return GroupFit(label=label, player_ids=fit.player_ids, fit=fit)

    def _bootstrap_group(self, label: str, X: np.ndarray, y: np.ndarray,
                         players: np.ndarray, setting: str, model: str, tau: float,
                         draw_lo: np.ndarray | None, draw_hi: np.ndarray | None,
                         tau2_hint: float) -> tuple[np.ndarray, np.ndarray]:
        reps = int(self.cfg.get("envelope.bootstrap_reps"))
        rng = np.random.default_rng(_seed(
            f"{setting}|{model}|{label}|{self.seed_extra}",
            int(self.cfg.seed) + int(self.cfg.get("envelope.bootstrap_seed_offset"))))
        uniq = np.unique(players)
        index = {int(p): i for i, p in enumerate(uniq)}
        gidx = np.array([index[int(p)] for p in players], dtype=np.int64)
        J = len(uniq)
        betas = np.empty((reps, X.shape[1]))
        us = np.empty((reps, J))
        for r in range(reps):
            player_w = rng.exponential(1.0, J)
            base_w = player_w[gidx]
            y_draw = _smoother_draw(rng, y, draw_lo, draw_hi)
            fit = fit_re_quantile(
                X, y_draw, players,
                tau=tau, prior_sd=self._tau2_prior(),
                max_iter=int(self.cfg.get("envelope.em_max_iter")),
                tol=max(float(self.cfg.get("envelope.em_tol")), 1e-5),
                eps=float(self.cfg.get("envelope.irls_eps")), base_w=base_w)
            betas[r] = fit.beta
            # align on this group's player order
            us[r] = fit.u
        return betas, us

    # -- run -------------------------------------------------------------------
    def run(self) -> EnvelopeResult:
        player_rows: list[dict] = []
        fit_rows: list[dict] = []
        modelfree_rows: list[dict] = []
        feasible_rows: list[dict] = []
        self._combine_range()
        for setting in self.settings:
            frame = self._sub(setting)
            if len(frame) == 0:
                continue
            self._settings_fitted.append(setting)
            self._run_setting(setting, frame, player_rows, fit_rows,
                              modelfree_rows, feasible_rows)
        players = self._finalise_players(player_rows)
        feasible = pd.DataFrame(feasible_rows)
        fit = pd.DataFrame(fit_rows)
        modelfree = pd.DataFrame(modelfree_rows)
        summary = self._feasible_summary(feasible)
        self._attach_feasibility_to_fit(fit, summary)
        manifest = self._manifest(len(self.turns))
        return EnvelopeResult(players=players, fit=fit, modelfree=modelfree,
                              feasible=feasible, feasible_summary=summary,
                              manifest=manifest)

    # -- one setting -----------------------------------------------------------
    def _run_setting(self, setting: str, frame: pd.DataFrame, player_rows: list,
                     fit_rows: list, modelfree_rows: list, feasible_rows: list) -> None:
        cfg = self.cfg
        ref = float(cfg.get("envelope.capacity_reference_speed"))
        floor = float(cfg.get("envelope.apex_speed_floor"))
        cap = float(cfg.get("envelope.winsor_lateral_yd_s2"))
        speeds = [float(s) for s in cfg.get("envelope.capacity_reference_speeds")]
        min_turns = int(cfg.get("envelope.min_turns_per_position"))
        min_reliable = int(cfg.get("envelope.min_turns_reliable"))
        quantile = self.quantile
        sensitivity = [float(q) for q in cfg.get("envelope.quantile_sensitivity")]

        nfl = _num(frame, "nfl_id").astype(np.int64)
        valid = _bool(frame, "valid")
        planar_angle = _num(frame, "abs_turn_angle")

        # ---- capacity observations ------------------------------------------
        base_demand = _demand(frame, cfg, "lateral_at_apex")
        kept, counts = _capacity_mask(frame, cfg, base_demand)
        demand = np.clip(base_demand, 0.0, cap)
        apex = _num(frame, "apex_speed")
        self.exclusion[setting] = counts
        player_group, group_sizes = self._assign_groups(
            setting, kept, min_turns, frame, setting)
        self.player_capacity_group[setting] = player_group
        self.group_sizes[setting] = group_sizes
        group_of_row = np.array([player_group.get(int(p), "ALL") for p in nfl])

        # ---- model-free (capacity observations only) ------------------------
        modelfree_rows.extend(self._modelfree(setting, frame, kept, demand, nfl))

        # ---- capacity fits, per group and per form --------------------------
        forms = ["lateral_at_apex"]
        if bool(cfg.get("envelope.friction_circle_sensitivity")):
            forms.append("friction_circle_upper")
        primary_form = "lateral_at_apex"
        capacity_models: dict[str, dict[str, GroupFit]] = {}
        for form in forms:
            y_form = np.clip(_demand(frame, cfg, form), 0.0, cap)
            capacity_models[form] = {}
            for group in sorted(set(player_group.values())):
                mask = kept & (group_of_row == group)
                if mask.sum() < 3:
                    continue
                X = np.column_stack([np.ones(int(mask.sum())), apex[mask] - ref])
                y = y_form[mask]
                pl = nfl[mask]
                gf = self._fit_group(group, X, y, pl, setting, f"capacity:{form}",
                                     quantile if form == primary_form else
                                     float(sensitivity[-1]))
                capacity_models[form][group] = gf
                if self.with_bootstrap and form == primary_form:
                    lo = _num(frame, "peak_lateral_accel_smoother_min") if self.smoother_spread else None
                    hi = _num(frame, "peak_lateral_accel_smoother_max") if self.smoother_spread else None
                    gf.beta_reps, gf.u_reps = self._bootstrap_group(
                        group, X, np.clip(y_form[mask], 0, cap), pl, setting,
                        f"capacity:{form}", quantile,
                        None if lo is None else lo[mask],
                        None if hi is None else hi[mask], gf.fit.tau2)
                    self._check_recovery_frame(gf)
        self.capacity[setting] = capacity_models

        # quantile sensitivity point fits (cheap, no bootstrap)
        for q in sensitivity:
            if abs(q - quantile) < 1e-12:
                continue
            for group, gf in capacity_models[primary_form].items():
                mask = kept & (group_of_row == group)
                X = np.column_stack([np.ones(int(mask.sum())), apex[mask] - ref])
                gfs = self._fit_group(group, X, np.clip(base_demand[mask], 0, cap),
                                      nfl[mask], setting, f"capacity:{primary_form}",
                                      float(q))
                capacity_models.setdefault(f"__sens_{q}", {})[group] = gfs

        # ---- retention (plant) fit ------------------------------------------
        ret_mask = _retention_mask(frame, cfg)
        ret = np.clip(_num(frame, "speed_retention"),
                      float(cfg.get("envelope.winsor_retention")[0]),
                      float(cfg.get("envelope.winsor_retention")[1]))
        ret_group, ret_sizes = self._assign_groups(
            setting, ret_mask, min_turns, frame, setting)
        self.player_retention_group[setting] = ret_group
        ret_group_of_row = np.array([ret_group.get(int(p), "ALL") for p in nfl])
        retention_models: dict[str, GroupFit] = {}
        if ret_mask.sum() >= 3:
            entry = _num(frame, "entry_speed")
            de = entry - float(cfg.get("envelope.retention_entry_centre"))
            da = planar_angle - float(cfg.get("envelope.retention_angle_centre"))
            for group in sorted(set(ret_group.values())):
                mask = ret_mask & (ret_group_of_row == group)
                if mask.sum() < 3:
                    continue
                X = np.column_stack([np.ones(int(mask.sum())), de[mask], da[mask],
                                     (de * da)[mask]])
                gf = self._fit_group(group, X, ret[mask], nfl[mask], setting,
                                     "retention", quantile)
                if self.with_bootstrap:
                    lo = _num(frame, "speed_retention_smoother_min") if self.smoother_spread else None
                    hi = _num(frame, "speed_retention_smoother_max") if self.smoother_spread else None
                    gf.beta_reps, gf.u_reps = self._bootstrap_group(
                        group, X, ret[mask], nfl[mask], setting, "retention",
                        quantile, None if lo is None else lo[mask],
                        None if hi is None else hi[mask], gf.fit.tau2)
                retention_models[group] = gf
        self.retention[setting] = retention_models

        # ---- regain (lower envelope) fit ------------------------------------
        reg_mask = _regain_mask(frame, cfg)
        reg = _num(frame, "regain_entry_speed_s")
        regain_models: dict[str, GroupFit] = {}
        if reg_mask.sum() >= 3:
            entry = _num(frame, "entry_speed")
            de = entry - float(cfg.get("envelope.retention_entry_centre"))
            da = planar_angle - float(cfg.get("envelope.retention_angle_centre"))
            rq = float(cfg.get("envelope.regain_quantile"))
            for group in sorted(set(ret_group.values())):
                mask = reg_mask & (ret_group_of_row == group)
                if mask.sum() < 3:
                    continue
                X = np.column_stack([np.ones(int(mask.sum())), de[mask], da[mask],
                                     (de * da)[mask]])
                # lower envelope of time == upper envelope of -time
                gf = self._fit_group(group, X, -reg[mask], nfl[mask], setting,
                                     "regain", 1.0 - rq)
                if self.with_bootstrap:
                    gf.beta_reps, gf.u_reps = self._bootstrap_group(
                        group, X, -reg[mask], nfl[mask], setting, "regain",
                        1.0 - rq, None, None, gf.fit.tau2)
                regain_models[group] = gf
        self.regain[setting] = regain_models

        # ---- per-player rows -------------------------------------------------
        frame_index = frame.copy()
        frame_index["_nfl"] = nfl
        for nid, sub in frame_index.groupby("_nfl", sort=True):
            nid = int(nid)
            row = self._player_row(setting, nid, sub, player_group, ret_group,
                                   capacity_models, retention_models, regain_models,
                                   kept, ret_mask, reg_mask, apex, min_reliable,
                                   primary_form, speeds, ref, quantile)
            if row is not None:
                player_rows.append(row)

        # ---- fit table -------------------------------------------------------
        fit_rows.extend(self._fit_rows(
            setting, frame, kept, ret_mask, reg_mask, capacity_models,
            retention_models, regain_models, player_group, ret_group,
            sensitivity, quantile, nfl, apex))

        # ---- feasible region -------------------------------------------------
        feasible_rows.extend(self._feasible_rows(
            setting, frame, nfl, player_group, ret_group, retention_models,
            planar_angle, kept, int(cfg.get("feasibility.probability_inside_min"))))

    # -- helpers for one setting ----------------------------------------------
    def _combine_range(self) -> None:
        comb = self.players_frames["combine"]
        if len(comb) == 0:
            self.range_combine = None
            self.warnings.append("no Combine turns: every feasibility cell is extrapolated")
            return
        entry = _num(comb, "entry_speed")
        ok = _bool(comb, "valid") & np.isfinite(entry)
        if not ok.any():
            self.range_combine = None
            self.warnings.append("no valid Combine turns: every feasibility cell is extrapolated")
            return
        self.range_combine = (float(entry[ok].min()), float(entry[ok].max()))

    def _check_recovery_frame(self, gf: GroupFit) -> None:
        """Guard against a silently exploded fit (a warning, never a silent pass)."""
        if not np.all(np.isfinite(gf.fit.beta)) or gf.fit.tau2 <= 0:
            self.warnings.append(f"non-finite capacity fit for group {gf.label}")

    def _modelfree(self, setting: str, frame: pd.DataFrame, kept: np.ndarray,
                   demand: np.ndarray, nfl: np.ndarray) -> list[dict]:
        cfg = self.cfg
        edges = [float(e) for e in cfg.get("envelope.modelfree_speed_edges")]
        k = int(cfg.get("envelope.modelfree_top_k"))
        min_player = int(cfg.get("envelope.modelfree_min_turns"))
        apex = _num(frame, "apex_speed")
        grp = frame["position_group"].where(frame["position_group"].notna(), "OTHER").astype(str)
        rows: list[dict] = []
        bin_id = np.full(len(frame), -1, dtype=int)
        for b in range(len(edges) - 1):
            bin_id[(apex >= edges[b]) & (apex < edges[b + 1])] = b
        for b in range(len(edges) - 1):
            for g in sorted(set(grp[kept])):
                m = kept & (bin_id == b) & (grp == g).to_numpy()
                if not m.any():
                    continue
                rows.append(self._modelfree_row(
                    setting, g, "position", b, edges[b], edges[b + 1], demand[m],
                    nfl[m], k))
            for p in np.unique(nfl[kept]):
                m = kept & (bin_id == b) & (nfl == p)
                if m.sum() < min_player:
                    continue
                rows.append(self._modelfree_row(
                    setting, str(grp[(nfl == p)].iloc[0]), "player", b,
                    edges[b], edges[b + 1], demand[m], nfl[m], k, player=int(p)))
        return rows

    def _modelfree_row(self, setting: str, group: str, level: str, b: int,
                       lo: float, hi: float, y: np.ndarray, players: np.ndarray,
                       k: int, player: int | None = None) -> dict:
        n = len(y)
        top = np.sort(y)[::-1]
        half = max(1, n // 2)
        return {
            "setting": setting,
            "level": level,
            "position_group": group,
            "nfl_id": player,
            "bin": b,
            "speed_lo": lo,
            "speed_hi": hi,
            "speed_mid": 0.5 * (lo + hi),
            "n": int(n),
            "n_players": int(len(np.unique(players))),
            "q75": float(np.quantile(y, 0.75)),
            "q90": float(np.quantile(y, 0.90)),
            "top3_mean": float(np.mean(top[:min(k, n)])),
            "top_half_mean": float(np.mean(top[:half])),
        }

    def _player_row(self, setting, nid, sub, player_group, ret_group,
                    capacity_models, retention_models, regain_models, kept, ret_mask,
                    reg_mask, apex, min_reliable, primary_form, speeds, ref,
                    quantile) -> dict | None:
        cfg = self.cfg
        # rows of this player in this setting (the sub frame is already the player)
        idx = sub.index.to_numpy()
        n_turns = len(sub)
        n_invalid = int((~_bool(sub, "valid")).sum())
        n_plant = int(_bool(sub, "plant").sum())
        n_arc = int((sub["turn_type"].astype(str) == "arc").sum())
        n_capacity = int(kept[idx].sum())
        n_regain = int(reg_mask[idx].sum())
        if n_turns == 0:
            return None
        group = player_group.get(nid, "ALL")
        rgroup = ret_group.get(nid, "ALL")
        flags: list[str] = []
        if not self.smoother_spread:
            flags.append("missing_smoother_spread")
        if n_capacity < int(cfg.get("envelope.min_turns_per_position")):
            flags.append("insufficient_turns")
        if n_plant == 0:
            flags.append("no_plant_turns")
            if n_arc > 0:
                flags.append("all_arc")
        if n_regain == 0:
            flags.append("no_regain")

        gf = capacity_models.get(primary_form, {}).get(group)
        cap = self._capacity_values(gf, nid, apex, idx, speeds, ref, n_capacity)
        ret = self._retention_values(retention_models.get(rgroup), nid, n_plant)
        reg = self._regain_values(regain_models.get(rgroup), nid)

        # model-free reference-window values for this player
        mf_q75, mf_q90, mf_top3 = self._modelfree_ref(sub, idx, kept, nid)

        reliable = n_capacity >= min_reliable
        shrink = cap.get("shrinkage", float("nan"))
        fully_pooled = bool(n_capacity < int(cfg.get("envelope.min_turns_per_position"))
                            or (np.isfinite(shrink)
                                and shrink < float(cfg.get("envelope.fully_pooled_shrinkage_max"))))
        return {
            "setting": setting,
            "nfl_id": nid,
            "player_name": sub["player_name"].dropna().iloc[0]
            if sub["player_name"].notna().any() else None,
            "position": sub["position"].dropna().iloc[0]
            if sub["position"].notna().any() else None,
            "position_group": group,
            "n_turns": n_turns,
            "n_capacity_turns": n_capacity,
            "n_plant": n_plant,
            "n_arc": n_arc,
            "n_invalid": n_invalid,
            "n_regain": n_regain,
            "reliable": bool(reliable),
            "fully_pooled": fully_pooled,
            "single_setting": bool(nid in self._single_setting_ids),
            "shrinkage_weight": shrink,
            **cap,
            **ret,
            **reg,
            "modelfree_q75_ref": mf_q75,
            "modelfree_q90_ref": mf_q90,
            "modelfree_top3_ref": mf_top3,
            "flags": ";".join(flags),
        }

    def _capacity_values(self, gf: GroupFit | None, nid: int, apex: np.ndarray,
                         idx: np.ndarray, speeds: list[float], ref: float,
                         n_capacity: int) -> dict:
        out: dict[str, Any] = {k: float("nan") for k in
                               ["capacity_ref", "capacity_slope",
                                "capacity_at_2", "capacity_at_4",
                                "capacity_at_6", "capacity_at_8",
                                "capacity_at_median_speed"]}
        for v in ["capacity_ref", "capacity_slope", "capacity_at_2", "capacity_at_4",
                  "capacity_at_6", "capacity_at_8", "capacity_at_median_speed"]:
            out[v + "_se"] = float("nan")
        out["capacity_ref_p05"] = float("nan")
        out["capacity_ref_p95"] = float("nan")
        out["shrinkage"] = float("nan")
        if gf is None:
            return out
        j = gf.fit.index(nid)
        if j is None:
            return out
        median_speed = float(np.median(apex[idx])) if n_capacity else float("nan")
        b0, b1 = float(gf.fit.beta[0]), float(gf.fit.beta[1])
        u = float(gf.fit.u[j])
        out["capacity_ref"] = b0 + u
        out["capacity_slope"] = b1
        for v, key in ((2.0, "capacity_at_2"), (4.0, "capacity_at_4"),
                       (6.0, "capacity_at_6"), (8.0, "capacity_at_8")):
            out[key] = b0 + u + b1 * (v - ref)
        out["capacity_at_median_speed"] = (b0 + u + b1 * (median_speed - ref)
                                           if np.isfinite(median_speed) else float("nan"))
        out["shrinkage"] = float(gf.fit.shrinkage()[j])
        if gf.beta_reps is not None and gf.u_reps is not None:
            beta = gf.beta_reps
            reps_u = gf.u_reps[:, j]
            c0 = beta[:, 0] + reps_u
            out["capacity_ref_se"] = _sd(c0)
            out["capacity_ref_p05"] = _pct(c0, 5)
            out["capacity_ref_p95"] = _pct(c0, 95)
            out["capacity_slope_se"] = _sd(beta[:, 1])
            for v, key in ((2.0, "capacity_at_2"), (4.0, "capacity_at_4"),
                           (6.0, "capacity_at_6"), (8.0, "capacity_at_8")):
                out[key + "_se"] = _sd(beta[:, 0] + reps_u + beta[:, 1] * (v - ref))
            if np.isfinite(median_speed):
                out["capacity_at_median_speed_se"] = _sd(
                    beta[:, 0] + reps_u + beta[:, 1] * (median_speed - ref))
        return out

    def _retention_values(self, gf: GroupFit | None, nid: int, n_plant: int) -> dict:
        keys = ["retention_ref", "retention_entry_coef", "retention_angle_coef",
                "retention_interaction"]
        out: dict[str, Any] = {k: float("nan") for k in keys}
        out.update({k + "_se": float("nan") for k in keys})
        out["retention_source"] = None
        if gf is None:
            return out
        j = gf.fit.index(nid)
        if j is None:
            return out
        out["retention_source"] = "player" if n_plant > 0 else "position"
        for k, col in zip(keys, range(4)):
            out[k] = float(gf.fit.beta[col] + (gf.fit.u[j] if col == 0 else 0.0))
            if gf.beta_reps is not None and gf.u_reps is not None:
                vals = gf.beta_reps[:, col] + (gf.u_reps[:, j] if col == 0 else 0.0)
                out[k + "_se"] = _sd(vals)
        return out

    def _regain_values(self, gf: GroupFit | None, nid: int) -> dict:
        keys = ["regain_ref_s", "regain_entry_coef", "regain_angle_coef"]
        out: dict[str, Any] = {k: float("nan") for k in keys}
        out.update({k + "_se": float("nan") for k in keys})
        out["regain_source"] = None
        if gf is None:
            return out
        j = gf.fit.index(nid)
        if j is None:
            return out
        out["regain_source"] = "player" if gf.fit.n_obs[j] > 0 else "position"
        for k, col in zip(keys, range(3)):
            if col < gf.fit.beta.shape[0]:
                value = gf.fit.beta[col] + (gf.fit.u[j] if col == 0 else 0.0)
                out[k] = -float(value)  # undo the sign flip used for the lower envelope
                if gf.beta_reps is not None and gf.u_reps is not None:
                    vals = -(gf.beta_reps[:, col] + (gf.u_reps[:, j] if col == 0 else 0.0))
                    out[k + "_se"] = _sd(vals)
        return out

    def _modelfree_ref(self, sub: pd.DataFrame, idx: np.ndarray, kept: np.ndarray,
                       nid: int) -> tuple[float, float, float]:
        """Upper quantiles in the speed bin nearest the reference speed."""
        cfg = self.cfg
        ref = float(cfg.get("envelope.capacity_reference_speed"))
        edges = [float(e) for e in cfg.get("envelope.modelfree_speed_edges")]
        k = int(cfg.get("envelope.modelfree_top_k"))
        apex = _num(sub, "apex_speed")
        y = np.clip(_num(sub, "peak_lateral_accel_yd_s2"),
                    0.0, float(cfg.get("envelope.winsor_lateral_yd_s2")))
        m = kept[idx]
        mids = [(0.5 * (edges[b] + edges[b + 1]), b) for b in range(len(edges) - 1)]
        mids.sort(key=lambda t: abs(t[0] - ref))
        for mid, b in mids:
            sel = m & (apex >= edges[b]) & (apex < edges[b + 1])
            if sel.sum() >= 1:
                yy = y[sel]
                top = np.sort(yy)[::-1]
                return (float(np.quantile(yy, 0.75)), float(np.quantile(yy, 0.90)),
                        float(np.mean(top[:min(k, len(top))])))
        return float("nan"), float("nan"), float("nan")

    def _fit_rows(self, setting, frame, kept, ret_mask, reg_mask, capacity_models,
                  retention_models, regain_models, player_group, ret_group,
                  sensitivity, quantile, nfl, apex) -> list[dict]:
        rows: list[dict] = []
        cap_group_of_row = np.array([player_group.get(int(p), "ALL") for p in nfl])
        ret_group_of_row = np.array([ret_group.get(int(p), "ALL") for p in nfl])
        for form, models in capacity_models.items():
            if form.startswith("__sens_"):
                q = float(form.split("__sens_")[1])
                mask = kept
                for group, gf in models.items():
                    rows.append(self._fit_row(
                        setting, group, "capacity", "lateral_at_apex", q, gf,
                        mask & (cap_group_of_row == group), apex))
                continue
            q = quantile if form == "lateral_at_apex" else float(sensitivity[-1])
            for group, gf in models.items():
                rows.append(self._fit_row(
                    setting, group, "capacity", form, q, gf,
                    kept & (cap_group_of_row == group), apex))
        for group, gf in retention_models.items():
            rows.append(self._fit_row(
                setting, group, "retention", "linear_interaction", quantile, gf,
                ret_mask & (ret_group_of_row == group), apex))
        for group, gf in regain_models.items():
            rows.append(self._fit_row(
                setting, group, "regain", "linear_interaction",
                float(self.cfg.get("envelope.regain_quantile")), gf,
                reg_mask & (ret_group_of_row == group), apex))
        return rows

    def _fit_row(self, setting, group, model, form, quantile, gf: GroupFit,
                 mask: np.ndarray, apex) -> dict:
        row = {
            "setting": setting,
            "position_group": group,
            "model": model,
            "form": form,
            "quantile": float(quantile),
            "n_players": int(len(gf.fit.player_ids)),
            "n_obs": int(mask.sum()),
            "random_effect_sd": float(np.sqrt(max(gf.fit.tau2, 0.0))),
            "prior_used": bool(gf.fit.prior_used),
            "irls_iterations": int(gf.fit.n_iter),
            "converged": bool(gf.fit.converged),
        }
        for i in range(4):
            row[f"coef{i}"] = float(gf.fit.beta[i]) if i < len(gf.fit.beta) else float("nan")
            row[f"coef{i}_se"] = (float(np.std(gf.beta_reps[:, i], ddof=1))
                                  if gf.beta_reps is not None and i < gf.beta_reps.shape[1]
                                  else float("nan"))
        if model == "capacity" and form == "lateral_at_apex" and abs(quantile - self.quantile) < 1e-12:
            ref = float(self.cfg.get("envelope.capacity_reference_speed"))
            vals = [float(gf.fit.beta[0] + gf.fit.u[j]) for j in range(len(gf.fit.u))]
            row["capacity_at_4_median"] = _pct(np.array(vals), 50)
            row["capacity_at_4_p05"] = _pct(np.array(vals), 5)
            row["capacity_at_4_p95"] = _pct(np.array(vals), 95)
        return row

    # -- feasible region -------------------------------------------------------
    def _cells(self) -> list[dict]:
        bins_s = [float(x) for x in self.cfg.get("bins.entry_speed")]
        bins_a = [float(x) for x in self.cfg.get("bins.turn_angle")]
        cells = []
        for i in range(len(bins_s) - 1):
            for j in range(len(bins_a) - 1):
                cells.append({
                    "cell_index": len(cells),
                    "entry_lo": bins_s[i], "entry_hi": bins_s[i + 1],
                    "angle_lo": bins_a[j], "angle_hi": bins_a[j + 1],
                    "entry_mid": 0.5 * (bins_s[i] + bins_s[i + 1]),
                    "angle_mid": 0.5 * (bins_a[j] + bins_a[j + 1]),
                })
        return cells

    def _observed(self, cell: dict) -> bool:
        if self.range_combine is None:
            return False
        lo, hi = self.range_combine
        return bool(cell["entry_lo"] >= lo and cell["entry_hi"] <= hi)

    def _feasible_rows(self, setting, frame, nfl, player_group, ret_group,
                       retention_models, angle, kept, prob_min) -> list[dict]:
        if not retention_models:
            self.warnings.append(
                f"{setting}: no plant turns -> no retention model -> no feasible region")
            return []
        cfg = self.cfg
        tol = float(cfg.get("feasibility.retention_tolerance"))
        e_centre = float(cfg.get("envelope.retention_entry_centre"))
        a_centre = float(cfg.get("envelope.retention_angle_centre"))
        cells = self._cells()
        Xc = np.column_stack([
            np.ones(len(cells)),
            [c["entry_mid"] - e_centre for c in cells],
            [c["angle_mid"] - a_centre for c in cells],
            [(c["entry_mid"] - e_centre) * (c["angle_mid"] - a_centre) for c in cells],
        ])
        entry = _num(frame, "entry_speed")
        valid = _bool(frame, "valid")
        cell_of = np.full(len(frame), -1, dtype=int)
        for c in cells:
            cell_of[(entry >= c["entry_lo"]) & (entry < c["entry_hi"])
                    & (angle >= c["angle_lo"]) & (angle < c["angle_hi"])] = c["cell_index"]
        setting_counts = np.bincount(cell_of[valid & (cell_of >= 0)],
                                     minlength=len(cells))
        meta = {}
        for nid, sub in frame.groupby(frame["nfl_id"], sort=False):
            name = sub["player_name"].dropna()
            pos = sub["position"].dropna()
            meta[int(nid)] = (name.iloc[0] if len(name) else None,
                              pos.iloc[0] if len(pos) else None)
        rows: list[dict] = []
        for nid in sorted(set(int(p) for p in nfl)):
            group = ret_group.get(nid, "ALL")
            gf = retention_models.get(group)
            if gf is None:
                continue
            j = gf.fit.index(nid)
            if j is None:
                continue
            point = Xc @ gf.fit.beta + gf.fit.u[j]
            p05 = p95 = p_inside = None
            if gf.beta_reps is not None and gf.u_reps is not None:
                preds = gf.beta_reps @ Xc.T + gf.u_reps[:, j][:, None]
                p05 = np.percentile(preds, 5, axis=0)
                p95 = np.percentile(preds, 95, axis=0)
                p_inside = (preds >= tol).mean(axis=0)
            player_counts = np.bincount(
                cell_of[(nfl == nid) & valid & (cell_of >= 0)], minlength=len(cells))
            for c in cells:
                i = c["cell_index"]
                pname, ppos = meta.get(nid, (None, None))
                rows.append({
                    "setting": setting,
                    "nfl_id": nid,
                    "player_name": pname,
                    "position": ppos,
                    "position_group": group,
                    "cell_index": int(i),
                    "entry_lo": c["entry_lo"], "entry_hi": c["entry_hi"],
                    "angle_lo": c["angle_lo"], "angle_hi": c["angle_hi"],
                    "entry_mid": c["entry_mid"], "angle_mid": c["angle_mid"],
                    "observed": self._observed(c),
                    "retention_pred": float(point[i]),
                    "retention_p05": float(p05[i]) if p05 is not None else float("nan"),
                    "retention_p95": float(p95[i]) if p95 is not None else float("nan"),
                    "p_inside": float(p_inside[i]) if p_inside is not None else float("nan"),
                    "inside": bool(point[i] >= tol),
                    "inside_prob": bool(p_inside[i] >= prob_min) if p_inside is not None else False,
                    "n_turns_cell": int(setting_counts[i]),
                    "n_turns_cell_player": int(player_counts[i]),
                })
        return rows

    # -- assembly --------------------------------------------------------------
    @property
    def _single_setting_ids(self) -> set[int]:
        comb = set(int(p) for p in _num(self.players_frames["combine"], "nfl_id"))
        game = set(int(p) for p in _num(self.players_frames["game"], "nfl_id"))
        return comb.symmetric_difference(game)

    def _finalise_players(self, rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=[f.name for f in ENVELOPE_PLAYER_SCHEMA])
        df = pd.DataFrame(rows)
        # enforce the published column order and drop helper keys
        cols = [f.name for f in ENVELOPE_PLAYER_SCHEMA]
        for c in cols:
            if c not in df.columns:
                df[c] = np.nan
        df = df[cols]
        df = df.sort_values(["setting", "position_group", "nfl_id"],
                            kind="stable").reset_index(drop=True)
        return df

    def _feasible_summary(self, feasible: pd.DataFrame) -> pd.DataFrame:
        cols = ["setting", "position_group", "cell_scope", "n_cells",
                "n_player_cells", "n_cells_point_feasible",
                "share_player_cells_point_feasible", "n_cells_prob_feasible",
                "share_player_cells_prob_feasible", "n_turns_in_scope",
                "share_turns_in_prob_feasible_cells"]
        if len(feasible) == 0:
            return pd.DataFrame(columns=cols)
        rows = []
        for setting, sdf in feasible.groupby("setting", sort=True):
            for group in sorted(sdf.position_group.unique()):
                g = sdf[sdf.position_group == group]
                for scope, sub in (("observed", g[g.observed]), ("extrapolated", g[~g.observed])):
                    cells = sub.cell_index.nunique()
                    n_pc = len(sub)
                    rows.append({
                        "setting": setting,
                        "position_group": group,
                        "cell_scope": scope,
                        "n_cells": int(cells),
                        "n_player_cells": int(n_pc),
                        "n_cells_point_feasible": int(
                            sub.groupby("cell_index").inside.any().sum()) if n_pc else 0,
                        "share_player_cells_point_feasible": float(sub.inside.mean()) if n_pc else float("nan"),
                        "n_cells_prob_feasible": int(
                            sub.groupby("cell_index").inside_prob.any().sum()) if n_pc else 0,
                        "share_player_cells_prob_feasible": float(sub.inside_prob.mean()) if n_pc else float("nan"),
                        "n_turns_in_scope": int(sub.groupby("cell_index").n_turns_cell.first().sum()) if n_pc else 0,
                        "share_turns_in_prob_feasible_cells": (
                            float(sub[sub.inside_prob].groupby("cell_index").n_turns_cell.first().sum()
                                  / max(sub.groupby("cell_index").n_turns_cell.first().sum(), 1))
                            if n_pc else float("nan")),
                    })
        out = pd.DataFrame(rows, columns=cols)
        return out.sort_values(["setting", "position_group", "cell_scope"],
                               kind="stable").reset_index(drop=True)

    def _attach_feasibility_to_fit(self, fit: pd.DataFrame, summary: pd.DataFrame) -> None:
        """Put per-position feasibility counts beside the capacity fit rows."""
        for col in ("feasible_cells_observed", "feasible_cells_extrapolated",
                    "share_players_inside_observed", "share_players_inside_extrapolated"):
            if col not in fit.columns:
                fit[col] = np.nan
        if len(summary) == 0 or len(fit) == 0:
            return
        primary = (fit.model == "capacity") & (fit.form == "lateral_at_apex")
        for i, row in fit[primary].iterrows():
            sub = summary[(summary.setting == row["setting"])
                          & (summary.position_group == row["position_group"])]
            if len(sub) == 0:
                continue
            for scope, suffix in (("observed", "observed"), ("extrapolated", "extrapolated")):
                hit = sub[sub.cell_scope == scope]
                if len(hit):
                    fit.at[i, f"feasible_cells_{suffix}"] = int(hit.n_cells_prob_feasible.iloc[0])
                    fit.at[i, f"share_players_inside_{suffix}"] = float(
                        hit.share_player_cells_prob_feasible.iloc[0])

    # -- manifest --------------------------------------------------------------
    def _manifest(self, turns_in: int) -> dict:
        cfg = self.cfg
        used = 0
        excl_total = {"invalid": 0, "below_apex_floor": 0, "nonfinite": 0,
                      "out_of_range": 0}
        # count over the raw input once (not once per setting)
        valid = _bool(self.turns, "valid")
        kept, counts = _capacity_mask(self.turns, cfg,
                                      _demand(self.turns, cfg, "lateral_at_apex"))
        used = int(kept.sum())
        for k in excl_total:
            excl_total[k] = int(counts.get(k, 0))
        fitted_players = {}
        for setting in self._settings_fitted:
            frame = self._sub(setting)
            if len(frame):
                fitted_players[setting] = int(
                    len(np.unique(_num(frame, "nfl_id").astype(np.int64))))
        return {
            "stage": STAGE,
            "mode": None,  # filled by the CLI
            "provenance": None,
            "label": None,
            "gap": "not_applicable",
            "spec_commit": cfg.get("spec_commit"),
            "estimator": {
                "name": str(cfg.get("envelope.estimator")),
                "quantile": float(self.quantile),
                "quantile_sensitivity": [float(q) for q in cfg.get("envelope.quantile_sensitivity")],
                "form": str(cfg.get("envelope.capacity_form")),
                "friction_circle_sensitivity": bool(cfg.get("envelope.friction_circle_sensitivity")),
                "reference_speed": float(cfg.get("envelope.capacity_reference_speed")),
                "speed_variable": str(cfg.get("envelope.capacity_speed_variable")),
                "demand": str(cfg.get("envelope.capacity_demand")),
                "apex_speed_floor": float(cfg.get("envelope.apex_speed_floor")),
                "winsor_lateral_yd_s2": float(cfg.get("envelope.winsor_lateral_yd_s2")),
                "random_effects": str(cfg.get("envelope.random_effects")),
                "between_player_sd_prior": float(cfg.get("envelope.between_player_sd_prior")),
                "bootstrap_reps": int(cfg.get("envelope.bootstrap_reps")),
                "bootstrap_kind": str(cfg.get("envelope.bootstrap_kind")),
                "uncertainty": "player_weighted_bootstrap",
                "posterior": False,
            },
            "counts": {
                "turns_in": int(turns_in),
                "turns_used": int(used),
                "excluded": excl_total,
                "winsorised_demand": int(counts.get("winsorised", 0)),
            },
            "settings_fitted": list(self._settings_fitted),
            "players_fitted": fitted_players,
            "position_groups": {s: sorted(set(g for g in self.player_capacity_group.get(s, {}).values()))
                                for s in self._settings_fitted},
            "combine_entry_speed_range": list(self.range_combine) if self.range_combine else None,
            "observed_rule": str(cfg.get("feasibility.observed_rule")),
            "primary_measure": ("arc_capacity (provisional — prereg decides by "
                                "reliability/counts, never outcomes)"),
            "retention_tolerance": float(cfg.get("feasibility.retention_tolerance")),
            "feasibility_cells": int(len(self._cells())),
            "smoother_spread_available": bool(self.smoother_spread),
            "missing_smoother_columns": list(self.missing_smoother),
            "open_decisions": ["D124 segmentation rule", "D125 drill geometry model"],
            "prereg_needs": ["quantile", "capacity form", "reference speed", "apex floor",
                             "primary measure", "retention tolerance", "bin edges",
                             "observed rule", "shrinkage model", "SE method"],
            "warnings": list(dict.fromkeys(self.warnings)),
            "note": ("Uncertainty is a player-weighted bootstrap. These are NOT posterior "
                     "draws: no sampler is used anywhere in this stage."),
        }


# --------------------------------------------------------------- stage helpers

def read_run_config(cfg: common.Config) -> dict:
    """The config block the notebook/README quote, with no interpretation."""
    return {
        "quantile": float(cfg.get("envelope.quantile")),
        "form": str(cfg.get("envelope.capacity_form")),
        "reference_speed": float(cfg.get("envelope.capacity_reference_speed")),
        "retention_tolerance": float(cfg.get("feasibility.retention_tolerance")),
    }
