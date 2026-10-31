"""dl_survival — the Part-1 / Part-2 estimators (prereg §10, design §5.5).

Everything here is pure numeric machinery: no file paths, no config file, no
provenance.  Design constants (quadrature nodes, iteration caps, chunk size)
are **arguments** so the stage can read them from ``config.yaml``.

Estimators
----------
``fit_weibull(...)``
    Weibull accelerated-failure-time model.  ``S(t | u) = exp(-u H)`` with
    ``H = (t / exp(x'beta)) ** k`` and hazard ``h(t | u) = u k t**(k-1)
    exp(-k x'beta)``.  One call covers every form the prereg asks for, because
    they differ only in the frailty term:

    * ``frailty='lognormal'`` — log-normal frailty by rusher multiplying the
      hazard, integrated with Gauss–Hermite quadrature (20 nodes, prereg §10).
      This is the **P1 primary**.
    * ``frailty='none'`` — no frailty; optionally cluster-robust SEs by player
      (the P1 rung 2 / Part-2 population model).
    * ``random_slope=True`` — a log-normal random slope on a covariate (load)
      by rusher, product quadrature, optionally correlated with the intercept.
      This is the P2 "random slope on load when estimable" and its
      "uncorrelated random effects" fallback.

    The **hazard ratio per unit x is exp(-k * beta)** and the **time ratio is
    exp(beta)**.  Both are available; the prereg reports the HR.

``fit_cox(...)``
    Cox PH with player-clustered robust SE (P1 rung 1), via
    ``statsmodels.duration.hazard_regression.PHReg`` (Efron ties).

``fit_logit_ri(...)``
    Logistic random-intercept model for ``quick_pressure`` (P1 secondary),
    integrated with the same Gauss–Hermite rule.

``fit_lmm_ri(...)``
    Gaussian linear mixed model for ``player_get_off`` (P1 secondary), via
    ``statsmodels.regression.mixed_linear_model.MixedLM``.

Scoring
-------
``log_score`` returns the per-snap held-out log predictive density: the same
integrand the likelihood uses, evaluated at the held-out observation with the
training parameters and the frailty integrated out.  Higher is better.
``concordance`` is Harrell's C on the risk score.

Honesty
-------
A fit that does not converge says so: ``Fit.converged`` is ``False`` and the
message records the optimiser's own words.  Nothing is silently restarted into
a different model; the caller owns the fallback ladder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy import optimize

__all__ = [
    "Fit",
    "concordance",
    "design_matrix",
    "fit_cox",
    "fit_lmm_ri",
    "fit_logit_ri",
    "fit_weibull",
    "gh_nodes",
    "independent_columns",
    "log_score",
    "risk_score",
    "standardise",
    "vif_table",
    "weibull_eval",
]

_EPS = 1e-12
_LOG_FLOOR = -700.0
_LOG_CEIL = 700.0


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def gh_nodes(nodes: int) -> tuple[np.ndarray, np.ndarray]:
    """Gauss–Hermite nodes/weights for ``E[f(Z)]``, ``Z ~ N(0, 1)``."""
    if int(nodes) < 2:
        raise ValueError(f"nodes must be >= 2, got {nodes!r}")
    z, w = np.polynomial.hermite.hermgauss(int(nodes))
    return z * np.sqrt(2.0), w / np.sqrt(np.pi)


def standardise(values: Sequence[float]) -> tuple[np.ndarray, float, float]:
    """``(z, mean, sd)``; a zero-variance column is centred and left at 0."""
    x = np.asarray(values, dtype=float)
    mean = float(np.mean(x)) if x.size else 0.0
    sd = float(np.std(x, ddof=0)) if x.size else 0.0
    if not np.isfinite(sd) or sd <= _EPS:
        return np.zeros_like(x), mean, 0.0
    return (x - mean) / sd, mean, sd


def design_matrix(frame, columns: Sequence[str], *,
                  categorical: Sequence[str] = ()) -> tuple[np.ndarray, list[str]]:
    """Build a numeric design matrix from a DataFrame-like object.

    Categorical columns expand to treatment dummies with the first (sorted)
    level as reference; the layout therefore does not depend on row order.  A
    single-level categorical contributes nothing and the caller sees the
    reduced width.
    """
    import pandas as pd

    blocks: list[np.ndarray] = []
    names: list[str] = []
    for col in columns:
        if col not in frame.columns:
            raise KeyError(f"design column {col!r} is not in the frame")
        if col in categorical:
            series = frame[col].astype("object")
            levels = sorted({str(v) for v in series.dropna().unique()})
            if not levels:
                continue
            for level in levels[1:]:
                blocks.append((series.astype(str) == level).to_numpy(float))
                names.append(f"{col}={level}")
        else:
            values = pd.to_numeric(frame[col], errors="coerce").to_numpy(float)
            blocks.append(values)
            names.append(str(col))
    if not blocks:
        raise ValueError("design matrix would have no columns")
    X = np.column_stack(blocks).astype(float)
    if not np.all(np.isfinite(X)):
        raise ValueError("design matrix contains non-finite values; "
                         "the caller must drop or impute rows explicitly")
    return X, names


def vif_table(X: np.ndarray, names: Sequence[str]) -> dict[str, float]:
    """Variance inflation factors: ``1 / (1 - R_j^2)`` per column."""
    X = np.asarray(X, dtype=float)
    out: dict[str, float] = {}
    p = X.shape[1]
    for j in range(p):
        y = X[:, j]
        others = np.delete(X, j, axis=1)
        if others.shape[1] == 0:
            out[names[j]] = 1.0
            continue
        if np.std(y) <= _EPS:
            out[names[j]] = float("inf")
            continue
        A = np.column_stack([np.ones(len(y)), others])
        try:
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            resid = y - A @ coef
            ss_res = float(resid @ resid)
            ss_tot = float(((y - y.mean()) ** 2).sum())
            r2 = 1.0 - ss_res / ss_tot if ss_tot > _EPS else 1.0
            out[names[j]] = (float("inf") if r2 >= 1.0 - 1e-12
                             else float(1.0 / (1.0 - r2)))
        except np.linalg.LinAlgError:
            out[names[j]] = float("inf")
    return out


def independent_columns(X: np.ndarray, names: Sequence[str]) -> tuple[np.ndarray, list[str], list[str]]:
    """Drop numerically dependent columns, keeping design order.

    A random-intercept model needs a full-rank design; two identical control
    dummies (or a constant column) make the fit singular.  The dependency is a
    reparameterisation, not a specification change, so the first column of each
    dependent set is kept and the rest are reported as dropped.
    """
    from scipy.linalg import qr

    X = np.asarray(X, float)
    if X.shape[1] == 0:
        return X, [], []
    scale = np.maximum(np.abs(X).max(axis=0), 1e-12)
    _, r, piv = qr(X / scale, mode="economic", pivoting=True)
    rank = int(np.sum(np.abs(np.diag(r)) > 1e-10 * max(np.abs(r[0, 0]), 1e-12)))
    keep = np.sort(piv[:rank])
    keep = [int(i) for i in keep]
    dropped = [str(names[i]) for i in range(len(names)) if i not in keep]
    return X[:, keep], [str(names[i]) for i in keep], dropped


@dataclass
class Fit:
    """A fitted model plus everything a report needs to be honest about it."""

    names: list[str]
    params: np.ndarray
    cov: np.ndarray | None
    loglik: float
    converged: bool
    message: str
    model: str
    n_obs: int
    n_events: int
    extras: dict[str, Any] = field(default_factory=dict)

    def index(self, name: str) -> int:
        return self.names.index(name)

    def param(self, name: str) -> float:
        return float(self.params[self.index(name)])

    def se(self, name: str) -> float:
        if self.cov is None:
            return float("nan")
        return float(np.sqrt(max(self.cov[self.index(name), self.index(name)], 0.0)))

    def transform(self, name: str, fn, *, z: float = 1.959963984540054
                  ) -> dict[str, float]:
        """Point estimate + delta-method interval for ``fn(coefficient)``."""
        est = self.param(name)
        se = self.se(name)
        value = float(fn(est))
        if not (np.isfinite(se) and se > 0.0):
            return {"estimate": value, "se": float("nan"),
                    "lo": float("nan"), "hi": float("nan")}
        h = max(abs(est) * 1e-6, 1e-9)
        grad = (float(fn(est + h)) - float(fn(est - h))) / (2.0 * h)
        return {"estimate": value, "se": float(abs(grad) * se),
                "lo": float(value - z * abs(grad) * se),
                "hi": float(value + z * abs(grad) * se)}


# --------------------------------------------------------------------------- #
# Weibull AFT core (shared by fitting and scoring)
# --------------------------------------------------------------------------- #
def _layout(frailty: str, random_slope: bool, correlated: bool):
    if frailty not in ("lognormal", "none"):
        raise ValueError(f"unknown frailty {frailty!r}")
    if random_slope and frailty == "none":
        raise ValueError("random_slope requires frailty='lognormal'")
    labels: list[str] = []
    if frailty != "none":
        labels.append("log_theta_int")
        if random_slope:
            labels.append("log_theta_slope")
            if correlated:
                labels.append("atanh_rho")
    return len(labels), labels


def _quadrature(nodes: int, random_slope: bool, slope_nodes: int | None = None):
    if random_slope:
        z1_, w1 = gh_nodes(nodes)
        z2_, w2 = gh_nodes(int(slope_nodes or nodes))
        z1 = np.repeat(z1_, len(z2_))
        z2 = np.tile(z2_, len(z1_))
        ww = np.repeat(w1, len(w2)) * np.tile(w2, len(w1))
        return z1, z2, np.log(np.maximum(ww, _EPS))
    z, w = gh_nodes(nodes)
    return z, np.zeros_like(z), np.log(np.maximum(w, _EPS))


def weibull_eval(params: np.ndarray, t: np.ndarray, event: np.ndarray,
                 X: np.ndarray, *, frailty: str = "lognormal",
                 random_slope: bool = False, correlated: bool = False,
                 nodes: int = 20, load: np.ndarray | None = None,
                 chunk_rows: int = 4000, need_grad: bool = False,
                 need_scores: bool = False, slope_nodes: int | None = None):
    """Evaluate the Weibull log-likelihood (and its gradient) at ``params``.

    Returns ``(per_obs_ll, grad, scores)``.  ``scores`` is the per-observation
    score matrix (only when ``need_scores``), used for cluster-robust SEs.
    This is the single implementation shared by :func:`fit_weibull`,
    :func:`log_score` and the gradient test.
    """
    t = np.asarray(t, float)
    event = np.asarray(event, float)
    X = np.asarray(X, float)
    n, p = X.shape
    n_theta, _ = _layout(frailty, random_slope, correlated)
    beta = params[:p]
    log_k = params[p]
    theta = params[p + 1:]
    k = float(np.exp(log_k))
    log_t = np.log(t)
    xb = X @ beta
    logH = np.clip(k * (log_t - xb), _LOG_FLOOR, _LOG_CEIL)
    H0 = np.exp(logH)
    z1, z2, log_w = _quadrature(nodes, random_slope, slope_nodes)
    q_nodes = len(z1)

    # log(u) and its parameter derivatives, per observation.
    rho, root, zs = 0.0, 1.0, z1
    if n_theta == 0:
        logu = np.zeros((n, 1))
    elif not random_slope:
        logu = np.broadcast_to((theta[0] * z1)[None, :], (n, q_nodes)).copy()
    else:
        rho = float(np.tanh(theta[2])) if correlated else 0.0
        root = np.sqrt(max(1.0 - rho * rho, _EPS))
        zs = rho * z1 + root * z2
        logu = theta[0] * z1[None, :] + theta[1] * zs[None, :] * load[:, None]

    ll = np.empty(n)
    grad = np.zeros_like(params) if need_grad else None
    scores = np.zeros((n, len(params))) if need_scores else None

    for start in range(0, n, int(chunk_rows)):
        stop = min(start + int(chunk_rows), n)
        sl = slice(start, stop)
        lu = logu[sl]
        u = np.exp(np.clip(lu, _LOG_FLOOR, _LOG_CEIL))
        Hs = H0[sl]
        ev = event[sl]
        # u*H is clipped on the log scale: during a line search the optimiser can
        # probe huge variance parameters, and an inf*0 product would poison the
        # gradient with NaN.
        uH = np.exp(np.clip(lu + logH[sl][:, None], _LOG_FLOOR, 500.0))
        base = log_w[None, :] - uH
        ev_term = base + (lu if frailty != "none" else 0.0)
        ll_ev = (np.logaddexp.reduce(ev_term, axis=1)
                 + log_k + (k - 1.0) * log_t[sl] - k * xb[sl])
        ll_c = np.logaddexp.reduce(base, axis=1)
        ll[sl] = np.where(ev > 0, ll_ev, ll_c)

        if not need_grad:
            continue
        q = np.exp(np.where(ev[:, None] > 0, ev_term, base) - ll[sl][:, None])
        q = q / np.maximum(q.sum(axis=1, keepdims=True), _EPS)
        Eu = (q * u).sum(axis=1)
        g_beta = k * (X[sl] * (Eu * Hs - ev)[:, None])
        g_logk = ev * (1.0 + logH[sl]) - Eu * Hs * logH[sl]
        grad[:p] += g_beta.sum(axis=0)
        grad[p] += float(g_logk.sum())
        if n_theta:
            # w_ij = q_ij * d log L_i / d u_ij : (1 - uH) for an event, -uH for
            # a censored row.  The derivative of log u is separable --
            #   d/dtheta_int  = z1_j
            #   d/dtheta_slope = zs_j * load_i
            #   d/drho         = theta_slope (1-rho^2) (z1_j - rho/root z2_j) load_i
            # -- so each parameter's score is a matrix-vector product, never an
            # (n, Q, n_theta) array.  Same arithmetic, far less memory traffic.
            w = np.where(ev[:, None] > 0, q * (1.0 - uH), -q * uH)
            g_theta = np.empty((stop - start, n_theta))
            g_theta[:, 0] = w @ z1
            if random_slope:
                g_theta[:, 1] = (w @ zs) * load[sl]
                if correlated:
                    g_theta[:, 2] = ((w @ (z1 - (rho / root) * z2))
                                     * (1.0 - rho * rho) * theta[1] * load[sl])
            grad[p + 1:] += g_theta.sum(axis=0)
            if need_scores:
                scores[sl, p + 1:] = g_theta
        if need_scores:
            scores[sl, :p] = g_beta
            scores[sl, p] = g_logk
    return ll, grad, scores


def _bounds(p: int, n_theta: int, random_slope: bool, correlated: bool):
    lower = [-30.0] * p + [-4.0]
    upper = [30.0] * p + [4.0]
    for idx in range(n_theta):
        if idx == 0 or idx == 1:
            lower.append(-8.0)
            upper.append(3.0)
        else:
            lower.append(-6.0)
            upper.append(6.0)
    return lower, upper


def fit_weibull(t: Sequence[float], event: Sequence[float], X: np.ndarray,
                names: Sequence[str], *,
                frailty: str = "lognormal",
                random_slope: bool = False,
                correlated: bool = False,
                slope_column: str | None = None,
                nodes: int = 20,
                cluster_groups: np.ndarray | None = None,
                max_iter: int = 500,
                tol: float = 1e-7,
                starts: int = 2,
                seed: int = 0,
                chunk_rows: int = 4000,
                with_cov: bool = True,
                method: str = "L-BFGS-B",
                slope_nodes: int | None = None) -> Fit:
    """Fit a Weibull AFT, optionally with a log-normal frailty / random slope."""
    t = np.asarray(t, dtype=float)
    event = np.asarray(event, dtype=float)
    X = np.asarray(X, dtype=float)
    n, p = X.shape
    if len(t) != n or len(event) != n:
        raise ValueError("t, event and X must have the same number of rows")
    if not np.all(np.isfinite(t)) or np.any(t <= 0):
        raise ValueError("observed times must be finite and > 0")
    if len(names) != p:
        raise ValueError(f"{len(names)} names for {p} columns")
    n_theta, theta_labels = _layout(frailty, random_slope, correlated)
    all_names = list(names) + ["log_shape_k"] + theta_labels
    n_events = int(event.sum())

    load = None
    if random_slope:
        if slope_column is None or slope_column not in list(names):
            raise ValueError("random_slope needs slope_column present in names")
        load = X[:, list(names).index(slope_column)].copy()
        load = load - load.mean()

    def objective(params):
        # Scaled by n: the total log-likelihood's gradient grows with the sample
        # size and makes L-BFGS-B's first trial step overshoot on a Pi-sized
        # problem.  Scaling changes neither the optimum nor the covariance
        # (the Hessian below is computed from the *total* gradient).
        ll, grad, _ = weibull_eval(
            params, t, event, X, frailty=frailty, random_slope=random_slope,
            correlated=correlated, nodes=nodes, load=load,
            chunk_rows=chunk_rows, need_grad=True, slope_nodes=slope_nodes)
        value = -float(ll.sum()) / max(n, 1)
        if not np.isfinite(value) or not np.all(np.isfinite(grad)):
            return 1e10, np.zeros_like(params)
        return value, -grad / max(n, 1)

    p0 = np.zeros(len(all_names))
    p0[p] = 0.0
    if n_theta:
        p0[p + 1] = np.log(0.5)
        if random_slope:
            p0[p + 2] = np.log(0.05)
            if correlated:
                p0[p + 3] = 0.0
    lower, upper = _bounds(p, n_theta, random_slope, correlated)

    rng = np.random.default_rng(seed)
    best = None
    for attempt in range(max(int(starts), 1)):
        start = (p0.copy() if attempt == 0
                 else np.clip(p0 + rng.normal(scale=0.05, size=p0.size),
                              np.array(lower) + 1e-9, np.array(upper) - 1e-9))
        try:
            options = ({"maxfun": int(max_iter), "ftol": tol, "gtol": tol}
                       if str(method).upper() == "TNC"
                       else {"maxiter": int(max_iter), "ftol": tol, "gtol": tol})
            res = optimize.minimize(
                objective, start, jac=True, method=str(method),
                bounds=list(zip(lower, upper)), options=options)
        except (ValueError, FloatingPointError, np.linalg.LinAlgError):
            continue
        if best is None or res.fun < best.fun:
            best = res
    if best is None:
        return Fit(list(all_names), p0, None, float("inf"), False,
                   "optimiser raised on every start", "weibull", n, n_events)

    params = best.x
    ll, grad, scores = weibull_eval(
        params, t, event, X, frailty=frailty, random_slope=random_slope,
        correlated=correlated, nodes=nodes, load=load,
        chunk_rows=chunk_rows, need_grad=True, need_scores=True,
        slope_nodes=slope_nodes)
    converged = bool(best.success) and bool(np.all(np.isfinite(params)))
    message = str(best.message)

    h = 1e-5
    m = len(params)
    hess = np.zeros((m, m))
    for j in (range(m) if with_cov else []):
        step = np.zeros(m)
        step[j] = h
        _, gp, _ = weibull_eval(params + step, t, event, X, frailty=frailty,
                                random_slope=random_slope, correlated=correlated,
                                nodes=nodes, load=load, chunk_rows=chunk_rows,
                                need_grad=True, slope_nodes=slope_nodes)
        _, gm, _ = weibull_eval(params - step, t, event, X, frailty=frailty,
                                random_slope=random_slope, correlated=correlated,
                                nodes=nodes, load=load, chunk_rows=chunk_rows,
                                need_grad=True, slope_nodes=slope_nodes)
        hess[:, j] = -(gp - gm) / (2.0 * h)
    hess = 0.5 * (hess + hess.T)
    cov = None
    hpd = False
    if not with_cov:
        hpd = True
    elif np.all(np.isfinite(hess)):
        try:
            hpd = bool(np.linalg.eigvalsh(hess).min() > 0.0)
            cov = np.linalg.inv(hess) if hpd else np.linalg.pinv(hess)
        except np.linalg.LinAlgError:
            cov = np.linalg.pinv(hess)
    if not hpd:
        message = f"{message}; Hessian not positive definite (SEs model-based only)"

    if cluster_groups is not None and frailty == "none" and cov is not None:
        groups = np.asarray(cluster_groups)
        uniq = np.unique(groups)
        meat = np.zeros_like(hess)
        for g in uniq:
            mask = groups == g
            if not np.any(mask):
                continue
            s = scores[mask].sum(axis=0)
            meat += np.outer(s, s)
        bread = np.linalg.pinv(hess)
        cov = bread @ meat @ bread
        message = f"{message}; cluster-robust SE over {len(uniq)} players"

    extras = {
        "loglik": float(ll.sum()),
        "event_rate": float(n_events / n) if n else 0.0,
        "grad_inf_norm": float(np.max(np.abs(grad))) if grad is not None else float("nan"),
        "n_iter": int(getattr(best, "nit", -1)),
        "frailty": frailty,
        "random_slope": bool(random_slope),
        "correlated": bool(correlated),
        "nodes": int(nodes),
        "slope_nodes": int(slope_nodes or nodes),
        "k": float(np.exp(params[p])),
        "theta": [float(v) for v in params[p + 1:]],
        "frailty_sd": float(np.exp(params[p + 1])) if n_theta else 0.0,
        "slope_sd": (float(np.exp(params[p + 2])) if (n_theta and random_slope) else 0.0),
        "slope_column": slope_column,
        "load_mean": (float(load.mean()) if load is not None else 0.0),
        "rho": (float(np.tanh(params[p + 3]))
                if (n_theta and random_slope and correlated) else 0.0),
        "cluster_robust": bool(cluster_groups is not None and frailty == "none"),
        "hessian_pd": hpd,
        "n_obs": int(n), "n_events": n_events,
    }
    return Fit(list(all_names), params, cov, float(ll.sum()), converged,
               message, "weibull", n, n_events, extras)


def log_score(t: Sequence[float], event: Sequence[float], X: np.ndarray,
              fit: Fit) -> np.ndarray:
    """Per-observation log predictive density under a fitted Weibull model."""
    ex = fit.extras
    X = np.asarray(X, float)
    load = None
    if ex.get("random_slope"):
        column = ex.get("slope_column")
        if column is None or column not in fit.names:
            raise ValueError("random-slope scoring needs the load column name")
        load = X[:, fit.names.index(column)] - float(ex.get("load_mean", 0.0))
    ll, _, _ = weibull_eval(
        fit.params, np.asarray(t, float), np.asarray(event, float),
        np.asarray(X, float), frailty=ex.get("frailty", "none"),
        random_slope=bool(ex.get("random_slope", False)),
        correlated=bool(ex.get("correlated", False)),
        nodes=int(ex.get("nodes", 20)), load=load)
    return ll


def risk_score(X: np.ndarray, fit: Fit) -> np.ndarray:
    """Linear predictor on the log-hazard scale (higher = earlier pressure)."""
    p = X.shape[1]
    return -(X @ fit.params[:p]) * float(np.exp(fit.params[p]))


def concordance(t: Sequence[float], event: Sequence[float],
                risk: Sequence[float]) -> float:
    """Harrell's C: P(risk higher for the subject with the shorter time)."""
    t = np.asarray(t, float)
    event = np.asarray(event, float)
    risk = np.asarray(risk, float)
    num = 0.0
    den = 0.0
    order = np.argsort(t)
    for rank, i in enumerate(order):
        if event[i] <= 0:
            continue
        later = order[rank + 1:]
        if later.size == 0:
            continue
        diff = risk[i] - risk[later]
        den += later.size
        num += float((diff > 0).sum()) + 0.5 * float((diff == 0).sum())
    return float(num / den) if den else float("nan")


# --------------------------------------------------------------------------- #
# Cox PH (P1 robustness rung 1)
# --------------------------------------------------------------------------- #
def fit_cox(t: Sequence[float], event: Sequence[float], X: np.ndarray,
            names: Sequence[str], *, cluster_groups: np.ndarray | None = None,
            max_iter: int = 500) -> Fit:
    """Cox PH with player-clustered robust SE (Efron ties)."""
    from statsmodels.duration.hazard_regression import PHReg

    t = np.asarray(t, float)
    event = np.asarray(event, float)
    X = np.asarray(X, float)
    try:
        model = PHReg(t, X, status=event, ties="efron")
        if cluster_groups is not None:
            res = model.fit(groups=np.asarray(cluster_groups), disp=0,
                            maxiter=int(max_iter))
        else:
            res = model.fit(disp=0, maxiter=int(max_iter))
    except Exception as exc:  # statsmodels raises several unrelated types
        return Fit(list(names), np.zeros(X.shape[1]), None, float("nan"), False,
                   f"{type(exc).__name__}: {exc}", "cox", len(t), int(event.sum()))
    params = np.asarray(res.params, float)
    cov = np.asarray(res.cov_params(), float)
    converged = bool(np.all(np.isfinite(params))) and bool(np.all(np.isfinite(cov)))
    return Fit(list(names), params, cov, float(getattr(res, "llf", np.nan)),
               converged,
               "cox ph, efron ties, cluster-robust" if cluster_groups is not None
               else "cox ph, efron ties", "cox", len(t), int(event.sum()),
               {"cluster_robust": cluster_groups is not None})


# --------------------------------------------------------------------------- #
# Logistic random intercept (P1 secondary: quick_pressure)
# --------------------------------------------------------------------------- #
def fit_logit_ri(y: Sequence[float], X: np.ndarray, names: Sequence[str],
                 groups: np.ndarray, *, nodes: int = 20,
                 max_iter: int = 500, tol: float = 1e-8) -> Fit:
    """Logistic regression with a log-normal random intercept.

    ``P(y=1 | b) = plogis(x'beta + b)``, ``b ~ N(0, theta^2)``, integrated with
    Gauss–Hermite quadrature.  ``groups`` is accepted for API symmetry with the
    other estimators and recorded, but an exchangeable random intercept has no
    group-varying covariate, so the marginal likelihood does not depend on the
    labels beyond their count.
    """
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    groups = np.asarray(groups)
    X, names, dropped = independent_columns(X, names)
    n, p = X.shape
    z, w = gh_nodes(nodes)
    log_w = np.log(np.maximum(w, _EPS))

    def loglik_grad(params: np.ndarray, need_grad: bool = True):
        beta = params[:p]
        theta = float(np.exp(params[p]))
        eta = X @ beta
        logits = eta[:, None] + (theta * z)[None, :]
        ll1 = -np.logaddexp(0.0, -logits)
        ll0 = -np.logaddexp(0.0, logits)
        terms = np.where(y[:, None] > 0, ll1, ll0) + log_w[None, :]
        ll = np.logaddexp.reduce(terms, axis=1)
        if not need_grad:
            return float(ll.sum()), None
        q = np.exp(terms - ll[:, None])
        q = q / np.maximum(q.sum(axis=1, keepdims=True), _EPS)
        pnode = np.exp(-np.logaddexp(0.0, -logits))     # plogis(logits)
        resid = y[:, None] - pnode
        grad = np.empty(len(params))
        grad[:p] = (q * resid).sum(axis=1) @ X
        grad[p] = float((q * resid * (theta * z)[None, :]).sum())
        return float(ll.sum()), grad

    def objective(params):
        ll, grad = loglik_grad(params)
        return -ll / max(n, 1), -grad / max(n, 1)

    p0 = np.zeros(p + 1)
    p0[p] = np.log(0.5)
    res = optimize.minimize(objective, p0, jac=True, method="L-BFGS-B",
                            bounds=[(-30, 30)] * p + [(-8, 3)],
                            options={"maxiter": int(max_iter), "ftol": tol,
                                     "gtol": tol})
    params = res.x
    h = 1e-5
    m = len(params)
    hess = np.zeros((m, m))
    for j in range(m):
        step = np.zeros(m)
        step[j] = h
        _, gp = loglik_grad(params + step)
        _, gm = loglik_grad(params - step)
        hess[:, j] = -(gp - gm) / (2.0 * h)
    hess = 0.5 * (hess + hess.T)
    cov = None
    ok = False
    try:
        ok = bool(np.linalg.eigvalsh(hess).min() > 0.0)
        cov = np.linalg.inv(hess) if ok else np.linalg.pinv(hess)
    except np.linalg.LinAlgError:
        cov = None
    ll, _ = loglik_grad(params, need_grad=False)
    return Fit(list(names) + ["log_theta_int"], np.asarray(params, float), cov,
               float(ll), bool(res.success) and ok, str(res.message),
               "logit_ri", n, int(y.sum()),
               {"theta_sd": float(np.exp(params[p])), "nodes": int(nodes),
                "n_groups": int(len(np.unique(groups))), "hessian_pd": ok,
                "dropped_columns": dropped})


# --------------------------------------------------------------------------- #
# Linear mixed model (P1 secondary: player_get_off)
# --------------------------------------------------------------------------- #
def fit_lmm_ri(y: Sequence[float], X: np.ndarray, names: Sequence[str],
               groups: np.ndarray, *, max_iter: int = 500) -> Fit:
    """Gaussian random-intercept model via ``statsmodels.MixedLM``."""
    from statsmodels.regression.mixed_linear_model import MixedLM

    y = np.asarray(y, float)
    X = np.asarray(X, float)
    groups = np.asarray(groups)
    X, names, dropped = independent_columns(X, names)
    try:
        model = MixedLM(y, X, groups=groups)
        res = model.fit(reml=False, method="lbfgs", maxiter=int(max_iter), disp=False)
    except Exception as exc:
        return Fit(list(names), np.zeros(X.shape[1]), None, float("nan"), False,
                   f"{type(exc).__name__}: {exc}", "lmm_ri", len(y), 0)
    params = np.asarray(res.fe_params, float)
    cov = np.asarray(res.cov_params(), float)[:len(params), :len(params)]
    converged = bool(getattr(res, "converged", True))
    group_var = float("nan")
    try:
        group_var = float(np.asarray(res.cov_re)[0, 0])
    except (TypeError, ValueError, IndexError):
        pass
    return Fit(list(names), params, cov, float(res.llf), converged,
               "mixedlm random intercept", "lmm_ri", len(y), 0,
               {"group_var": group_var, "dropped_columns": dropped})
