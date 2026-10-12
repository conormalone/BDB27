# Pre-registration — BDB27 ceiling-binding task  (**FROZEN**)

> **Status: FROZEN** (2026-10-12). Every numeric value below is fixed **before** the `--mode full` runs
> that consume it. No item may be edited after results for the corresponding stage are first computed
> (anti-HARKing, §Anti-HARKing discipline). Any later change is recorded as a dated `notes/decisions.md`
> entry with the reason and the pre-change value, and flagged in `notes/limitations.md`.
>
> Approving authority: **Conor** (2026-10-12). Implemented in `config.yaml` (each row names its key).
> Per `TASK.md`, a full run may **not** proceed while this file is missing / unfrozen → that is a blocker
> (`notes/blockers.md`). The freeze is enforced in code by `src/gate.py` (see §Gate).

## Freeze checklist (ticked)

| # | Item | config.yaml key | Value | Frozen? |
|---|---|---|---|---|
| 1 | Quantile set (τ) | `models.quantiles` | `[0.5, 0.75, 0.9, 0.99]` | ☑ |
| 2 | `k` for top-k efforts per fixed route-snap count | `game.top_k_efforts` / `game.route_snap_count` | `5` / `100` | ☑ |
| 3 | Combine feature set (≤3) + selection rule | `envelope.max_features` / `envelope.selection_rule` | `3`; `[icc, vif]` | ☑ |
| 4 | ICC source + threshold | `envelope.icc_min` (source: `audit.min_icc`) | `0.5` / `0.5` | ☑ |
| 5 | Sample minima: matched WR, route snaps/player | `audit.min_matched_wr` / `audit.min_route_snaps_per_player` | `60` / `100` | ☑ |
| 6 | Exceedance margin + reframe rule | `audit.exceedance_margin_rel` / `audit.exceedance_reframe_rate` | `0.0` / `0.25` (see §6) | ☑ |
| 7 | Estimation-stability fallback triggers | `models.fallbacks` | ordered list (see §7) | ☑ |
| 8 | VIF ceiling | `envelope.vif_max` | `5.0` | ☑ |
| 9 | Model-free split / speed-at-time / cut metrics | `envelope.split_distances_yd` / `envelope.speed_at_times_s` / `envelope.cut_metrics` | `[5,10,20]` / `[1.0,2.0]` / `[entry_speed, exit_speed, cod_deficit]` | ☑ |
| 10 | Minimum dash attempts to fit an envelope | `envelope.min_attempts_per_player` | `1` (shrinkage stabilises thin players) | ☑ |
| 11 | Effort cut-offs (percentiles) | `game.thresholds.high_speed_percentile` / `accel_percentile` | `0.90` / `0.90` | ☑ |
| 12 | Bootstrap reps | `models.bootstrap_reps` | `1000` | ☑ |
| 13 | Controls (without draft) | `models.controls_without_draft` | `[position, age, height, weight, bmi]` | ☑ |
| 14 | Cross-val folds / permutation reps | `validate.cv_folds` / `validate.perm_reps` | `5` / `1000` | ☑ |
| 15 | Measurement-error correction | `validate.measurement_error_correction` | `icc_attenuation` | ☑ |
| 16 | Pre-declared sensitivity analyses | `validate.sensitivity` | see §8 | ☑ |

---

## 1. Quantile set (τ)

- **Fixed:** τ ladder `[0.5, 0.75, 0.9, 0.99]` (`models.quantiles`). Quantile regression, **effort-level**,
  **player-clustered** bootstrap.
- **Rationale:** question (2) asks whether the Combine→game link **strengthens toward the upper tail**;
  that is only testable against a pre-declared ladder. τ=0.5 is the central reference; 0.75/0.9/0.99 trace
  the tail. The top of the ladder (0.99) is deliberately extreme so a weakening/turn-up is falsifiable, at
  the cost of high variance — covered by the §7 fallbacks.

## 2. Top-k efforts per fixed route-snap count

- **Fixed:** per player, take the **top `k=5` efforts** (`game.top_k_efforts`) ranked by the effort metric,
  from a **fixed denominator of `route_snap_count=100` route snaps** (`game.route_snap_count`).
- **Definition of a route snap:** `player_play.route_ran IS NOT NULL` (it is **categorical**, not a flag;
  `notes/schema.md` §7, audit d). Players with **< 100 route snaps** are excluded from the primary
  fixed-denominator tail (they are reported descriptively, not pooled).
- **Ties:** if efforts tie at the k-th value, **keep all tied** values (do not drop) — recorded, counted,
  never silently truncated.
- **Rationale:** "max effort" must be drawn from a **comparable** number of snaps, otherwise a player with
  more snaps wins the tail by sampling alone (tail-selection inflation).

## 3. ≤3 Combine features + selection rule (ICC then VIF)

- **Fixed rule** (`envelope.max_features=3`, `envelope.selection_rule=[icc, vif]`):
  1. **Candidate pool** — all model-free Combine features **recomputed from `x,y`** for the straight-line
     40-yd dash (`envelope.fit_drills`). The **vendor-provided `s`-derived feature is excluded** (the task
     mandates recomputing kinematics from `x,y` because provided `s`/`a`/`dis` are unreliable on cutting
     drills — audit f). Pool: `peak_speed`, `speed_at_{5,10,20}yd`, `speed_at_t{1.0,2.0}s`,
     `time_to_{5,10,20}yd`, `min_speed_after_peak`, `speed_deficit_after_peak`, `max_decel`,
     `env_vmax`, `env_tau`, `env_a0`.
  2. **ICC filter** — retain features with test-retest ICC(1,1) ≥ `envelope.icc_min` (`0.5`). (ICC computed
     from repeated attempts of the same `(nfl_id, drill_type)`; explicit `UNESTIMABLE` when repeats are too
     thin — never a silent blank.)
  3. **VIF prune** — while **more than `max_features`** features remain, remove the **least reliable
     (lowest-ICC)** retained feature whose **VIF > `envelope.vif_max` (`5.0`)**; recompute VIF after each
     removal. The VIF ceiling is the **pruning trigger** (redundancy detector), not a guarantee that all
     retained features are mutually independent — see the flag rule below.
  4. **Result:** the ≤3 retained features enter the primary model. Report ICC **and** VIF for every
     candidate; any **residual VIF above the ceiling** among the retained features is **flagged in the
     table and in `notes/limitations.md`** (no silent failure).
- **Frozen expected outcome (declared in advance, not tuned):** `peak_speed`, `speed_at_20yd`, `env_vmax`.
  These are one representative of each mechanical construct — raw peak, a fixed distance split, and the
  fitted envelope ceiling — at the ICC-reliable end.
- **Selection is performed on the Combine side only** (data complete), never against game outcomes.

## 4. ICC source + threshold

- **Source:** test-retest **ICC(1,1)** of each per-player Combine feature, from repeated attempts
  (distinct `event_id`) of the same `(nfl_id, drill_type)`; computed in `01_audit` (g) and re-derived in
  `02_combine_envelope`. The Phase-2 hierarchy contributes the shrinkage/reliability view (SE on the shrunk
  per-player ceiling).
- **Threshold:** `audit.min_icc = envelope.icc_min = 0.5`. A feature below the threshold is **dropped and
  recorded**, never silently kept. Audit evidence (40-yd dash): `peak_speed` 0.975, `speed_at_20yd` 0.969,
  `speed_at_10yd` 0.883, `env_vmax` 0.643 (retained); `env_a0` 0.337, `env_tau` 0.097 (dropped).

## 5. Sample minima (go/no-go gate)

- **Fixed:** `audit.min_matched_wr = 60` matched WR (Combine→NFL), and
  `audit.min_route_snaps_per_player = 100` median route snaps/player.
- **Rationale:** the audit preview (complete `player_play.csv`) gives matched **WR 106/107, TE 38/42** and
  median route snaps **WR 261 / TE 184**, so the minima are set with headroom below the preview yet well
  above a study that cannot support a per-player tail. **Falling below either → blocker**
  (`notes/blockers.md`), not a silent design change.

## 6. Exceedance threshold + reframe rule

- **What "exceedance" means:** the share of in-game max-effort observations that **beat** a player's
  estimated Combine ceiling (question 1: is the ceiling a real bound?).
- **Margin (fixed):** an effort is counted as **exceeding** the ceiling when its in-game peak speed exceeds
  the player's estimated ceiling speed by more than **`audit.exceedance_margin_rel = 0.0`** (strict beat;
  both quantities are yards/second, so no unit rescaling is needed). The margin is a declared tolerance for
  measurement/digitisation noise; a **sensitivity grid over the margin** is pre-declared (§8).
- **Reframe rule (fixed):** if the overall exceedance rate exceeds **`audit.exceedance_reframe_rate = 0.25`**
  — i.e. **">25% speed exceedance"** — the ceiling is declared **not binding**, and the analysis is
  **reframed as *utilization*** ("how close to ceiling", a scaling reference) rather than a bound. This is
  declared **before** results so it is not a post-hoc rescue.
- **Statement:** *">25% speed exceedance → envelope not binding → reframe as utilization."*

## 7. Estimation-stability fallback triggers

- **Fixed triggers** (any ⇒ the fallback chain applies for that estimate):
  (a) quantile-regression **non-convergence**; (b) **degenerate per-player variance** (near-zero, or a
  feature ICC `UNESTIMABLE`); (c) **bootstrap instability** (bootstrap SE larger than the point estimate, or
  the τ=0.99 interval spanning the whole plausible range).
- **Fixed ordered fallback list** (`models.fallbacks`, applied in order, each **logged** in
  `notes/decisions.md`):
  1. `refit_increased_max_iter` — retry the quantile fit with a higher iteration cap.
  2. `adjacent_tau_report` — report the adjacent stable τ and **flag** that τ=0.99 is unstable (report a
     **null** for the extreme tail rather than a forced number).
  3. `pool_positions` — refit with WR/TE pooled (reported alongside, never replacing the per-position fit).
  4. `report_null` — if none converge, report a **null** for that stratum (no forced result).

## 8. Other frozen values

- **Model-free feature grid:** `envelope.split_distances_yd = [5, 10, 20]`;
  `envelope.speed_at_times_s = [1.0, 2.0]`; `envelope.cut_metrics = [entry_speed, exit_speed,
  cod_deficit]` (cutting drills only). Cut metrics are **not headlined** (audit g: cutting-drill ICC ≈
  0.1–0.3; several `UNESTIMABLE`).
- **Minimum dash attempts per player:** `envelope.min_attempts_per_player = 1` — the hierarchical shrinkage
  is *designed* to stabilise thin per-player ceilings (40-yd dash median 2 attempts), so requiring ≥2 would
  discard ~13% of players without scientific gain and bias toward players who happened to run twice.
- **Effort cut-offs:** `game.thresholds.{high_speed_percentile, accel_percentile} = 0.90` (percentile
  method); the **sample-placeholder** thresholds (`thresholds.sample_placeholders`) are **a-priori** only and
  never derived from the sample; full-run thresholds must carry provenance `FULL`.
- **Bootstrap:** `models.bootstrap_reps = 1000`, player-clustered, seed = config `seed` (+ offset).
- **Controls:** without draft `[position, age, height, weight, bmi]`; with draft adds `draft_position`
  ("undrafted" = explicit level).
- **Validation:** `validate.cv_folds = 5` (player-grouped), `validate.perm_reps = 1000`,
  `validate.measurement_error_correction = icc_attenuation` (divide the estimated slope by the feature
  reliability to correct attenuation), baseline = controls-only.
- **Pre-declared sensitivity analyses (`validate.sensitivity`):** `alt_quantiles`, `drop_thin_repeats`,
  `exclude_optouts`, `margin_grid`.

---

## Gate (enforced in code)

`src/gate.py` freezes this file into a runtime check: a `--mode full` run of any stage that consumes a
pre-registered value **must** find this file `FROZEN` with all required `config.yaml` keys non-null, else it
stops and points at `notes/blockers.md`. Combine-side stages (`02_combine_envelope`) require the freeze
only; game-side stages additionally require the audit go/no-go gate (matched WR, route snaps, missing
fields, ICC) to pass.

## Anti-HARKing discipline

- Nothing in this file may be edited **after** results for the corresponding stage are first computed.
- Any change after freezing is recorded as a dated `notes/decisions.md` entry **with the reason** and the
  pre-change value, and flagged in `notes/limitations.md`.
