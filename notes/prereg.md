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
zero** and **not missing**. `sack` / `unblocked_pressure` on the **dropback
analysis frame** are treated as observed (null ≠ 0); that frame has zero nulls
in both fields, so no rows are excluded. Out-of-frame nulls on live pass plays
without a recorded dropback are a recorded data-quality residual and never enter
the table (wording corrected 2026-10-31, D71: the earlier text said "live pass
play" and "109 rows excluded"; the rule itself is unchanged — the model frame
was always the censorable dropback snap). No imputation of any label.

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

*Amended 2026-11-02 (D78), **before any full-data result was seen** and before
any full-data file existed on this host. This section is the only part of the
frozen text the amendment touches: §1–§7 and §9–§16 stand unchanged.*

### 8.1 Why the control list changed — the tracking product

The 2027 game tracking is a **prospect-only product**: it follows the combine
cohort, not the 22 players on the field. The measured density is 1.5 tracked
players per play on full data and 1.16 on the provided sample (median 1, maximum
4); `player_play` itself carries 2.7 rows per play (median 2). The coverage
guard therefore requires something the data cannot contain: `all22_min_players`
(20) is structurally unreachable. The full run of the derivation reported
**0 of 14,677 plays** with 20 or more tracked players at the snap frame, **0**
derivable plays, all four derived columns `NA`, and both models returned
`NOT_ESTIMABLE` — not because the models were wrong, but because the control
list asked for columns the product cannot populate.

### 8.2 Removed from §8: `blitz`, `n_blockers`, `primary_blocker_group`

These three are **team-level rush and protection counts**. §5.4a derived them
geometrically from all-22 tracking. On a prospect-only product that derivation
cannot run: you cannot count four rushers or five protectors when only one or
two of them are tracked. Team fixed effects (§8.4) absorb a team's *average*
rush and protection tendency, but **not** the play-level variation — a blitz on
this snap versus a four-man rush on the next. The three controls are therefore
**unobservable on this dataset, not merely unmeasured**, and they are removed
from the specification rather than replaced by a proxy.

No proxy is substituted. `player_play.blitzing` is a per-player rush assignment
(D69: TRUE on 98.3 % of interior rows), not a team blitz; using it as one would
silently redefine the control while appearing to keep it. `n_blockers` and
`primary_blocker_group` have no substitute at all. `src/dl/dl_blockers.py` is
**parked, not deleted**: it and its planted-truth tests remain in the tree for a
future all-22 product, and stage 03 short-circuits before calling it when
`tracking_product: prospect_only` (D78).

**Consequence for the claim, stated in the report.** P1 cannot condition on the
number of rushers, on blitz, or on which blocker the rusher beat. The estimand
remains a hazard ratio conditional on position, alignment, play context and
size — not on protection scheme at the play level. This is a limitation of the
data product, not a choice.

### 8.3 Primary controls (fixed list)

Identical in P1 and in both load definitions; fixed before any full-data result:

| control | source | role |
|---|---|---|
| `position_group` | `players.nfl_position` (DE / DT / NT) | position |
| `alignment` | `player_play.lined_up_position` (edge vs interior) | alignment |
| `down` | `player_play.down` | play context |
| `yards_to_go` | `player_play.yards_to_go` | play context |
| `quarter` | `player_play.quarter` | play context |
| `score_margin` | pre-snap scores, defender's perspective | game state |
| `seconds_since_last_snap` | load module, per rusher-game | rest |
| `snaps_in_drive` | load module, per rusher-drive | within-drive load |
| `height_in`, `weight_lb` | `combine_results` | size (retained from the frozen text; §1 requires "beyond position, size and play context") |
| `years_since_combine` | `games.season − players.draft_year` | career timing (retained from the frozen text) |

`seconds_since_last_snap` is structurally undefined on the first snap of each
player-game (there is no prior snap). That snapshot is **excluded by complete
case, counted, and never imputed**. Players below the §9 floor after this
exclusion stay out of the model, as §9 already requires.

### 8.4 Fixed effects

**Team fixed effects** (the rusher's own team, `player_play.team_abbr`) and
**season fixed effects** (`games.season`, derived from the `game_id` join to
`games.csv`, never from a filename or a clock). Both enter as fixed effects,
not random effects; the random intercept is by rusher (§10). `season` replaces
the old opponent-team crossed effect's role as the schedule/era adjustment and
keeps the design identified across the three draft classes. Note that
`years_since_combine = season − draft_year` is thereby identified only off
cross-cohort variation; it is retained because §1 and `TASK.md` require it, and
the control set's VIF is reported (§13).

### 8.5 Sensitivity panel (never primary)

Reported as a separate panel, never as the confirmatory fit, and never used to
select the headline specification:

* `team_coverage_man_zone` — man versus zone, the scheme control most likely to
  change time to pressure;
* `offense_formation` — formation family (8 levels), a second scheme proxy;
* `pre_snap_depth_yd` — the rusher's own depth at the snap. It is observable,
  but only on snaps where the prospect's own tracking row exists (2.3 % of the
  provided sample; complete in a full-22 product). As a complete-case primary
  control it would silently restrict the estimand to tracked plays, so it is
  exercised here and can be promoted only by a logged deviation.

### 8.6 Unchanged rules

Random intercept by rusher; `player_get_off` is **not** a control in P1 (it is
on the mediation path) and is a control in a sensitivity model only. The list
above is fixed: no control may be added, dropped or redefined after seeing
full-data results.

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

**§8 AMENDED 2026-11-02 · D78 · before any full-data result.** The three
team-level controls (`blitz`, `n_blockers`, `primary_blocker_group`) were
removed as unobservable on a prospect-only tracking product; team and season
fixed effects and the declared sensitivity panel were added in their place.
No other section changed.
