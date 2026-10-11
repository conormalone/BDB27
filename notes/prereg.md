# Pre-registration — BDB27 ceiling-binding task  (**SKELETON — values TBD**)

> **Status: SKELETON.** This file exists so the design is fixed **before** the full run. Numeric values
> marked `TBD` must be filled in and this file frozen (no HARKing — no tuning against results) **before a
> `--mode full` run** of the stage that consumes them. Per `TASK.md`, **a full run may not proceed while
> `notes/prereg.md` is missing / unfrozen** → it is a blocker (`notes/blockers.md`).
>
> Each item below states: what is fixed, where it is implemented (`config.yaml` key), and its freeze
> state. The `config.yaml` skeleton carries the same keys (many as `null`).

## Freeze checklist (tick as each is fixed)

| # | Item | config.yaml key | Value | Frozen? |
|---|---|---|---|---|
| 1 | Quantile set (tau) | `models.quantiles` | TBD | ☐ |
| 2 | `k` for top-k efforts per fixed route-snap count | `game.top_k_efforts` / `game.route_snap_count` | TBD | ☐ |
| 3 | Combine feature set (≤3) + selection rule | `envelope.max_features` / `envelope.selection_rule` | ≤3; ICC → VIF | ☐ |
| 4 | ICC source + threshold | `envelope.icc_min` (source: `audit.min_icc` gate) | TBD | ☐ |
| 5 | Sample minima: matched WR, route snaps/player | `audit.min_matched_wr` / `audit.min_route_snaps_per_player` | TBD | ☐ |
| 6 | Exceedance threshold + reframe rule if exceedance is common | (see §6) | TBD | ☐ |
| 7 | Estimation-stability fallback triggers | `models.fallbacks` | TBD | ☐ |

---

## 1. Quantile set (τ)

- **Fixed before full run:** the set of τ at which the **upper-tail** link is estimated (quantile
  regression, `models.quantiles`). Rationale: question (2) asks whether the Combine→game relationship
  **strengthens toward the upper tail**; this is only testable against a **pre-declared** τ ladder.
- **TBD:** the ladder (e.g. median + two upper tails). A median/base quantile is included so the tail
  contrast is defined against a central reference.

## 2. Top-k efforts per fixed route-snap count

- **Fixed before full run:** `k` (efforts per player) and the **fixed route-snap denominator** so that
  every player's "max effort" is drawn from a **comparable** number of route snaps (avoids tail-selection
  inflation from simply having more snaps).
- **TBD:** `k` and the snap count. Define whether ties at the k-th value are kept/dropped.

## 3. ≤3 Combine features + selection rule (ICC then VIF)

- **Fixed rule:** at most **3** Combine features enter the primary model; candidates are ranked by
  **ICC** (reliability of the per-player Combine estimate) and then pruned for collinearity by **VIF**.
- **TBD:** the candidate feature pool and the exact ICC/VIF cut-offs (items 4, below). Selection is
  performed on the **Combine** side (data complete), never on game outcomes.

## 4. ICC source + threshold

- **Source:** the ICC of each per-player Combine feature, estimated as part of the audit gate
  (`audit.min_icc`); the Phase-2 envelope hierarchy contributes a shrinkage/reliability view.
- **TBD:** the ICC estimator (which variance components) and the minimum ICC to retain a feature.

## 5. Sample minima (go/no-go gate)

- **Fixed before full run:** the **minimum matched WR count** and the **minimum route snaps/player**
  below which the study cannot proceed (`audit.min_matched_wr`, `audit.min_route_snaps_per_player`).
- **TBD:** the numeric minima. If the full run falls below either → **blocker** (`notes/blockers.md`),
  not a silent design change.

## 6. Exceedance threshold + reframe rule

- **What "exceedance" means here:** the share of in-game max-effort observations that **beat** a player's
  estimated Combine ceiling (question 1: is the ceiling a real bound?).
- **Fixed before full run:** the tolerance that defines "at/over the ceiling" (a margin, since Combine and
  game measurement units/frames differ) and the **reframe rule** to apply **if exceedance turns out to be
  common** (i.e. the ceiling is not a hard bound). The reframe must be declared in advance so it is not a
  post-hoc rescue (e.g. re-purpose the ceiling as a *scaling reference* / "how close to ceiling" rather
  than a bound, and say so).
- **TBD:** the margin and the exact reframe statement.

## 7. Estimation-stability fallback triggers

- **Fixed before full run:** the conditions under which a model estimator is deemed unstable and the
  pre-declared fallback is used (quantile-regression non-convergence, degenerate per-player variance,
  bootstrap instability). Mirrors the prior project's discipline: a **reliability/gate** must be
  declared and honoured (report a null rather than a forced result).
- **TBD:** the trigger conditions and the ordered fallback list (`models.fallbacks`).

---

## Anti-HARKing discipline

- Nothing in this file may be edited **after** results for the corresponding stage are first computed.
- Any change after freezing is recorded as a dated `notes/decisions.md` entry **with the reason** and the
  pre-change value, and flagged in `notes/limitations.md`.
