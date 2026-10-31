# Pre-registration — DL acceleration ceiling and in-game pass-rush pressure

**Status: FROZEN.** Frozen 2026-10-31 (D70). Gates `--mode full` only.
Written after the Phase 1 audit and the Phase 2 reliability measurement on the
provided sample, and **before** any Phase 4 model code was written. No full-data
result has been seen; no full-data file exists on this host.

Provenance of the numbers quoted below: `outputs/dl/audit/SUMMARY.md` and
`outputs/dl/ceiling/summary.json`, sample mode, stamped
`PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT`. Sample values are stated here
only to fix **rules**, never as findings. Where a rule depends on a measurement,
the rule is written so that a different full-data measurement changes the
**verdict**, not the analysis.

Deviations from this document are permitted only as exploratory analyses and
must be logged in `notes/decisions.md` with the date and the reason. A
confirmatory claim may not be moved after the fact.

---

## 1. Questions and claim hierarchy

1. **P1 (confirmatory).** Does the Combine acceleration ceiling predict time to
   pressure on pass-rush snaps, beyond position, size and play context?
   Estimand: hazard ratio per **1 SD** of `a0`, with a player-level frailty.
2. **P2 (confirmatory).** Does cumulative load measured **relative to the
   player's own ceiling** predict later pass-rush outcomes better than
   **absolute** cumulative load, out of sample, with equal tuning freedom?
   Estimand: difference in held-out log score, relative minus absolute.
3. **S1 (secondary).** Is there a population-level decline in pass-rush
   outcomes with accumulated load under the better definition, and does `a0`
   moderate it?
4. **E1 (exploratory, descriptive only).** How much of the `a0` effect on
   pressure runs through `player_get_off`?

Only P1 and P2 can be called confirmatory. S1 is secondary and reported with
its own interval. E1 is descriptive; no causal mediation claim is made.

## 2. Data and cohort

* Source: the seven provided files only. No external data.
* Cohort: `players.nfl_position ∈ {DE, DT, NT}` (88 players), joined to
  `combine_position == "DL"`. Claims are limited to drafted DL with game
  tracking, and to the 2023–2025 draft classes.
* Game side: `player_play` rows flagged `blitzing == TRUE` are interpreted as
  **rush assignments** (D69 evidence: TRUE ⇔ `player_get_off` present,
  15,633/17,159 vs 2/322 on dropback rows). This is a semantic decision fixed
  before modelling; it is not re-litigated per result.
* `player_play` contains the prospect cohort only (497 distinct `nfl_id`,
  2.73 rows/play). It is not a full participation table; team-level blitz and
  blocker assignments are derived from tracking at full run and are `NA` where
  tracking is absent.
* Seasons 2023–2025 including postseason; preseason games are excluded from
  modelling (kept in the audit).

## 3. Primary exposure and the feature set

* **Primary: `a0 = vmax / tau`** from the per-attempt fit
  `v(t) = vmax * (1 - exp(-t/tau))` on `FORTY_YARD_DASH`, pooled across
  attempts by empirical-Bayes shrinkage on the log scale, always using the
  shrunken estimate and its delta-method SE. Never a best attempt.
* `a0` is retained unconditionally as the estimand; the ICC/VIF screen ranks
  only the alternates (D69). A screen that drops `a0` is a bug, not a finding.
* Alternates, used in a **specification curve** (one per model, never joint):
  `speed_at_1_0`, `speed_at_2_0`, `vmax`, `tau`, `ten_yd_split` (from
  `combine_results`; no test-retest ICC is estimable), `peak_accel`
  (exploratory).
* Lateral mechanics (shuttle/cone entry speed, peak, exit speed, total time,
  braking distance) are **exploratory only** and enter no confirmatory model.
* The exposure enters every model standardised to 1 SD of the shrunken
  estimate in the estimation sample, so the HR is per 1 SD by construction.

## 4. Outcomes

| role | outcome | definition |
|---|---|---|
| P1 primary | time to pressure | `event = 1` iff `time_to_pressure` non-null on that DL row; event time = `time_to_pressure` |
| P1 secondary | `quick_pressure` | binary, non-null rows only; nulls are "no pressure charged", not zero |
| P1 secondary | `player_get_off` | linear mixed model, seconds |
| P2 | time to pressure | same as P1, with load added |
| exploratory | `sack` | rare (8.7% of rush rows are pressures; sacks far rarer); reported, never primary |

`pressure_allowed` (OL-side) is **not** an outcome. It is used only as an
independent cross-check of label attribution, because the two systems disagree
on roughly half of dropback plays (audit item e) and because
`peak_pressure_probability_allowed` is probability-valued and therefore
model-derived.

**Null semantics fixed here:** null `time_to_pressure` / `quick_pressure` /
`time_to_qb_hurry` = no event charged to that rusher (event absent), **not
zero** and **not missing**. `sack` / `unblocked_pressure` on a live pass play
are treated as observed (null ≠ 0); 109 rows with unexplained nulls are
excluded from models that use those fields and counted. No imputation of any
label.

## 5. Censoring rule

* Censor time = `dropback_duration`, joined at `(game_id, play_id)` from the OL
  row (it is constant within play) and used **only** as the censoring time.
* A snap with no `dropback_duration` is excluded and counted: it cannot be
  censored.
* If `time_to_pressure > dropback_duration`, the snap is set to `event = 0` at
  `dropback_duration` and counted as an inconsistency; it is not silently kept.
* `dropback_duration` never enters any model as a covariate.
* Never fit on pressured snaps only; the event rate must be reported alongside
  every fit, and a check asserts it is below 0.5.

## 6. Primary snap set and strata

* Primary: DL rows with a rush assignment (`blitzing == TRUE`), a valid censor
  time, and a shrunken `a0`.
* Exploratory strata: DL rows with `blitzing == FALSE` (dropped into coverage);
  team blitz (rushers > 4, tracking-derived) — `NA` when underivable.
* Interior vs edge (`DE` = edge; `DT`/`NT` = interior) reported separately and
  pooled with an interaction. The interaction is not a primary claim.

## 7. Load definitions and tuning (Part 2)

Both definitions are computed on the same snap sequence, with the same recency
weighting and the same number of tuned parameters.

* **Absolute load**: cumulative distance (yards) accumulated above a fixed
  speed threshold `τ_abs`, over earlier snaps in the same game, exponentially
  recency-weighted with half-life 300 s. Grid:
  `τ_abs ∈ {1.5, 2.0, 2.5, 3.0, 3.5}` yd/s (5 values).
* **Relative load**: cumulative distance above `f × v_player(t_match)`, where
  `v_player` is the shrunken fitted Combine curve and `t_match` is the in-game
  elapsed time matching the Combine window (1.0 s and 2.0 s). Grid:
  `f ∈ {0.5, 0.6, 0.7, 0.8, 0.9}` (5 values).
* Ceiling uncertainty is carried into the load by 200 posterior draws from the
  shrunken estimate and its SE (seeded); the draw-mean load is used, and the
  point-estimate load is reported alongside.
* **Tuning**: both grids are searched **inside each training fold only**, by the
  same criterion (training-fold log likelihood), over the same number of
  candidate values (5 each), with the same optimiser settings. The selected
  value is applied unchanged to the held-out fold. No tuning on held-out data,
  no tuning on the sample, no per-definition extra parameters.
* Rest is accounted for by `seconds_since_last_snap` and `snaps_in_drive`,
  which enter both definitions identically as covariates.
* Total distance is a reported benchmark, not a competing definition.

## 8. Controls

Fixed list, identical in both load definitions and in P1:
alignment (edge/interior), pre-snap depth, blitz flag, number of blockers, down,
yards to go, score margin, quarter, position group, height, weight, years since
Combine. Random intercept by rusher; crossed effects for opponent team and
primary blocker group. `player_get_off` is **not** a control in P1 (it is on the
mediation path); it is a control in a sensitivity model only.

## 9. Minimum snaps per player

* A rusher enters a model only with **≥ 100** primary pass-rush snaps.
* A player-grouped CV fold must contain ≥ 10 such rushers; if not, folds are
  reduced and the reduction is logged (a reduction is not a deviation).
* Players below the minimum remain in the descriptive tables.

## 10. Models

* **P1 primary**: Weibull AFT with a log-normal frailty by rusher (Gauss–
  Hermite quadrature, 20 nodes), fixed effects as §8. Reported as a time ratio
  and converted to a hazard ratio; the frailty is a random intercept, not a
  random slope.
* **P1 robustness rungs** (all reported): (1) Cox PH with player-clustered
  robust SE; (2) AFT without frailty, cluster-robust SE. If the primary fails
  to converge, the rungs are the fallback, and the fallback used is logged —
  the point estimate is never chosen by which rung is most favourable.
* **P1 secondary**: logistic mixed model for `quick_pressure`; linear mixed
  model for `player_get_off`.
* **P2**: the same AFT structure with the load covariate added, fitted once
  with absolute and once with relative load, plus a random slope on load by
  rusher when estimable. Convergence fallbacks in order: uncorrelated random
  effects with an alternate optimiser; per-player slopes with empirical-Bayes
  shrinkage; random intercept only (population-level results only). The
  fallback used is logged.
* **S1**: population load coefficient under the better definition, plus a
  load × `a0` interaction, reported both naive and measurement-error-corrected
  (posterior draws of `a0`), with model-based slope reliability
  `var(slope)/(var(slope)+mean(SE²))`.
* **E1**: product-of-coefficients decomposition of the `a0` effect through
  `player_get_off`, with bootstrap intervals over players. Descriptive.

## 11. Comparison metric and resampling (P2)

* **Metric**: mean held-out log score per snap (higher is better), from
  player-grouped 5-fold CV with no player in both train and test. Concordance
  is reported as a secondary metric.
* **Difference**: relative minus absolute, in nats per snap.
* **Interval**: bootstrap over players (1,000 reps, seeded), resampling players
  and all their snaps together. The difference is meaningful only if the
  interval excludes zero **and** the point estimate exceeds the smallest effect
  of interest.
* Fold assignment is seeded and fixed; the same folds are used for both
  definitions.

## 12. Smallest effects of interest and equivalence

* P1: **HR 1.10** per 1 SD `a0`. Equivalence margin for a null claim: HR 1.05.
* P2: **0.005 nats/snap**. Equivalence margin: 0.002 nats/snap.
* A null result is interpreted as informative only if the interval excludes the
  smallest effect of interest; otherwise it is reported as inconclusive.
* Attenuation is stated explicitly: the observed log-HR is attenuated by
  `sqrt(ICC_a0)`. Power is calculated both raw and attenuated. On the sample's
  event count the attenuated MDE is HR 1.075, so the design can detect the SOI;
  if the full-run ICC is lower, the MDE worsens and that is reported, not
  corrected by changing the SOI.

## 13. Reliability thresholds (fixed, never lowered)

* `icc_min = 0.50` on `a0`. Measured on the sample: **0.532** (71 players, 136
  attempts, 6 one-attempt players excluded) — borderline, and the reason the
  gate is CONDITIONAL.
* If the full-run `a0` ICC is **below 0.50**, P1 is **NO-GO** as specified. The
  documented fallback (`speed_at_1_0`, sample ICC 0.640) is proposed to Conor
  and **not** adopted automatically; the threshold is never lowered.
* `vif_max = 5.0` applies to the control set, not to the alternate curve
  features (which are one-per-model by construction).

## 14. Multiplicity and reporting

* One confirmatory test for P1 and one for P2. Everything else is secondary or
  exploratory and is labelled as such in every table.
* All estimated models are reported, including non-converged ones and the
  fallback actually used. No model is dropped for an unfavourable estimate.
* Every number in a full-run report comes from a stage artefact with `FULL`
  provenance. Sample artefacts may not be quoted as results.

## 15. Stopping conditions (bind the full run)

Stop and report if any of the following holds, without adjusting the design:
missing required field; matched DL < 40; median primary snaps < 100; `a0` ICC
< 0.50; Part 1 null semantics unresolved; Part-1 MDE worse than HR 1.15.
If only snap coverage fails, Part 2 is dropped and P1 continues.

## 16. What this prereg forbids

* Changing the exposure, outcome, censoring rule, control list, grid sizes,
  CV scheme, metric, SOI or reliability threshold after seeing full data.
* Reporting a sample-mode number as a finding.
* Selecting the load definition by within-sample fit.
* Presenting `a0` as independent of the pressure label's own inputs: the label
  is model-derived and likely uses rusher kinematics, so the claim is
  "predicts the provided pressure label", stated plainly in the report.

---

**FROZEN 2026-10-31 · D70 · gates `--mode full` only.**
