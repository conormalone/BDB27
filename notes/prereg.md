# Pre-registration — Intra-drive regeneration, inter-snap rest, and pass-rush kinematic deficits

**Status: FROZEN.** Frozen 2026-11-04 (IDR spec).

**Claim hierarchy (final, D85).** P2 is the **single confirmatory test**; its
smallest effect of interest was fixed before any result and is not moved. **P1
is a primary estimand reported as an estimate with its interval — no threshold,
no significance claim, no confirmatory label.**

Why, recorded so it is not mistaken for an oversight: no defensible smallest
effect of interest exists for the P1 interaction (a change in log acceleration
deficit per 1 SD of load × 1 SD of rest). A threshold chosen before seeing data
would be invented; one chosen after would not be a test. Rather than invent one
or weaken the design with a season hold-out, P1 is estimated and reported in
full, and the writeup states plainly that its size is judged against football
consequences, not against a pre-set number.

This supersedes two same-day decisions: choosing the P1 threshold after the full
run ("Nah I'll just pick after the run"), and the 2023–24 / 2025 season hold-out
("Ok let's do 1 above", D84). Conor's instruction to proceed: *"Ok just go ahead
with it them both"* — both questions stay in the paper, with the hierarchy above.

Written 2026-11-04 for the IDR spec (`TASK.md`, supplied by Conor the same day,
superseding the 2026-10-30 acceleration-ceiling spec archived at
`notes/archive-tasks/TASK-dl-acceleration-ceiling-2026-10-30.md`).

**This document transcribes decisions that already exist** in `config.yaml`,
`notes/decisions.md` (D80–D83) and `notes/schema.md`. It introduces no new
threshold, grid, control or definition. Where `TASK.md` is silent, the choice is
marked **[declared]** — a decision made in the build and recorded here so it
cannot drift afterwards.

No full-data result has been seen. No full-data file exists on this host.

---

## 1. Questions and claim hierarchy

1. **P1 (confirmatory).** Does the interaction between rolling kinematic load and
   inter-snap rest predict a player's acceleration deficit on the current snap?
   Estimand: the load × rest coefficient on `log(deficit_ratio)`, with load and
   rest standardised, and a random intercept (plus random slope on load where
   estimable) by rusher.
2. **P2 (confirmatory, second).** Do transient, snap-level kinematic deficits
   predict time to pressure better than absolute cumulative game load, out of
   sample, with equal tuning freedom? Estimand: difference in held-out log
   predictive density, deficit arm minus absolute arm.
3. **E1 (exploratory).** At what rest-to-load ratio does an elite starter's
   pressure probability fall below that of a fresh rotational player?
   Descriptive curve, no confirmatory claim.

**P2 is the only confirmatory test.** P1 is a primary estimand reported as an
estimate with its interval (see the header). E1 is exploratory and is not a
test.

## 2. Data and cohort

* The seven provided files only. No external data.
* Combine side: `combine_tracking.csv`, `combine_results.csv`, `players.csv`.
* Game side: `player_play.csv`, `game_tracking_{2023,2024,2025}.csv`, `games.csv`.
* Cohort: matched DL (`players.nfl_position ∈ {DE, DT, NT}` joined to
  `combine_position == "DL"`), draft classes 2023–2025. Claims are limited to
  draftee DL with game tracking.
* The 2027 tracking product is **prospect-only**: team-level rush and protection
  counts are unobservable, not missing (D78). Anything requiring all-22 counts
  is out of scope and is not proxied.

## 3. Baseline: the Combine acceleration ceiling

* Per attempt, fit `v(t) = v_max * (1 - exp(-t / τ))` on straight-line drills;
  `a_0 = v_max / τ`.
* Pool attempts per player by empirical-Bayes shrinkage on the log scale; use
  the shrunken estimate and its delta-method SE everywhere downstream; never a
  best attempt.
* `a_0` is the **baseline**, not a predictor. It is the denominator of the
  deficit and a player-level covariate, and it enters no model as an exposure.
* Reliability gate: `icc_min = 0.50` on `a_0`. Sample value 0.532 (71 players,
  136 attempts) — borderline. If the full-run ICC is below 0.50, the run stops
  and the fallback is proposed to Conor, never adopted automatically. The
  threshold is never lowered.

## 4. Derived per-snap quantities (Phase 3)

All three are computed once, written to the snap table, and never recomputed
inside a model.

* **`inter_snap_rest_s`** — seconds from the **end of the previous play** to the
  snap of the current play. A play-level quantity: computed once per
  (game, team, play) and joined to each rusher, never computed per player.
  `NA` on a rusher-game's first snap. Never imputed across a gap: if
  intermediate snaps are missing, the row is flagged and rest is not carried
  through.
* **`rep_kinematic_load_w_per_kg`** — **power**, `∫ a·v dt` over a rep, with
  `a` in m/s² and `v` in m/s, so the unit is W/kg. Not `∫a²dt`.
* **`rolling_kinematic_load_w_per_kg`** — the sum of the previous **3** snaps'
  power for that rusher in that game (window fixed at 3 in `config.yaml`;
  windows 1 and 5 are a declared sensitivity panel and are never used to select
  the primary).
* **`a0_snap`** — fitted, not differentiated: the same `v(t)` model is fitted to
  each in-game rep's post-snap speed trace and `a_0 = v_max / τ` is read off it,
  with `τ` retained. Raw double-differentiation is kept as a labelled
  robustness column and is never primary.
* **`deficit_ratio`** = `a0_snap / baseline a0`. Below 1 is a deficit. The
  baseline SE is carried so the error can be propagated downstream.

Coverage is counted, never assumed: `rows_rusher_tracked`, `frames_used`,
`fit_ok`, `fit_at_bound`, `rep_too_short`, `fit_r2`, and
`rest_sequence_complete`. Rows that fail are counted and reported; nothing is
dropped silently and nothing is imputed.

## 5. Outcomes

| role | outcome | definition |
|---|---|---|
| P1 | `log(deficit_ratio)` | as defined in §4; complete rows only |
| P2 | time to pressure | event = pressure; time = `time_to_pressure`; censored at `dropback_duration` |
| exploratory | `quick_pressure` | binary, pressure within 2.50 s; nulls are "no pressure charged" |
| exploratory | `sack` | rare; reported, never primary |

**Not outcomes** (recorded so they are not reintroduced): `player_get_off`,
`time_to_qb_hurry` and `unblocked_pressure` appear in the data but `TASK.md`
does not name them as outcomes. **Pass-rush win rate** is named in `TASK.md` Q2
but no such column exists in the provided `player_play.csv` (64 columns; the
only "win" fields are win probability). Conor's decision 2026-11-04: if nothing
can be done with the rushing-related rows, drop it. **P2's outcome is therefore
time to pressure only, and the writeup states that the win-rate label is not
available in this data release.** No proxy is substituted.

**Null semantics.** Null `time_to_pressure` / `quick_pressure` = no event
charged to that rusher. Null is not zero and not missing. No imputation.

## 6. Censoring

* Censor time = `dropback_duration`, constant within play, joined at
  `(game_id, play_id)`. Used **only** as the censoring time, never as a
  covariate.
* A snap with no `dropback_duration` cannot be censored and is excluded, counted.
* `time_to_pressure > dropback_duration` is set to `event = 0` at
  `dropback_duration` and counted as an inconsistency.
* The event rate is reported with every fit, and an assertion requires it below
  0.5. Never fit on pressured snaps only.

## 7. Snap set

* Primary: DL pass-rush snaps — `player_play` rows with a rush assignment
  (`blitzing == TRUE`, the D69 semantic: TRUE ⇔ `player_get_off` present), a
  valid censor time, and a shrunken baseline `a_0`.
* Interior vs edge (`DE` = edge; `DT`/`NT` = interior) reported separately and
  pooled with an interaction. Not a primary claim.
* `blitzing` is carried into the snap table and declared as a control (§8), but
  on this snap set it is constant (TRUE for every row, because these rows *are*
  the rush assignments). The model reports it as not identified rather than
  claiming a blitz adjustment it cannot make. A team-level blitz indicator is
  not derivable from this product.

## 8. Controls

* **P1**: alignment, down, yards to go, score margin, and `blitzing` (declared,
  not identified — see §7). `TASK.md` does not specify a control list for P1;
  this list is **[declared]** and is exactly what §4's spec sentence implies.
  Random intercept by rusher; random slope on load where estimable.
* **P2**: position group, alignment, down, yards to go, quarter, score margin,
  seconds since last snap, snaps in drive, height, weight, years since Combine,
  `blitzing`; crossed fixed effects for team and season; plus baseline `a0`
  level. Frailty by rusher.
* The control list is fixed. No control may be added, dropped or redefined after
  seeing full-data results.

## 9. Minimum sizes

* P1: ≥ 100 complete snaps and ≥ 8 rushers for a fit. Game-clustered SEs are
  reported only above 10 game clusters; below that they are `NOT_ESTIMABLE` and
  the count is printed.
* P2: ≥ 100 rows, ≥ 8 players, ≥ 20 events.
* Player-grouped CV folds must contain ≥ 10 players; if not, folds are reduced
  and the reduction is logged. A reduction is not a deviation.
* Rows below a minimum stay in descriptive tables and are excluded from the fit
  with the reason recorded.

## 10. Models

* **P1**: linear mixed model of `log(deficit_ratio)` on standardised load,
  standardised rest and their product, with the §8 controls. Convergence ladder,
  walked in order and logged at every rung: correlated random slopes →
  uncorrelated random effects with an alternate optimiser → random intercept
  only → fixed effects only. **The interaction appears in every rung.** If it
  cannot be estimated, that is reported with the reason; it is never dropped to
  obtain a fit.
* **P1 inference**: CR1 player-clustered sandwich standard errors, t with G−1
  degrees of freedom. Game-clustered SEs reported only above 10 clusters. Naive
  i.i.d. intervals are computed as a diagnostic and **never** reported as the
  result.
* **P2**: Weibull AFT on time to pressure, one arm with `deficit_ratio` and one
  with cumulative absolute load above 2.5 yd/s, on the same rows, folds and
  controls; frailty by rusher; the same convergence ladder; tuned with **equal
  freedom by construction** (one fixed candidate per arm, `grid_budget = 1`).
* **E1**: descriptive predicted-pressure curves across the rest-to-load ratio,
  for a high-snap-share and a low-snap-share group. No test, no significance
  claim.

## 11. Comparison metric and resampling (P2)

* Metric: mean held-out **log predictive density per snap** (nats/snap), from
  player-grouped folds with no player in both train and test.
* Difference: deficit arm minus absolute arm, in nats/snap.
* Interval: bootstrap over players, resampling players and all their snaps
  together, seeded and config-driven. Meaningful only if the interval excludes
  zero **and** the point estimate exceeds the smallest effect of interest.
* Fold assignment is seeded and shared by both arms.

## 12. Smallest effects of interest and equivalence

* **P2: 0.005 nats/snap.** Equivalence margin 0.002. Carried forward unchanged
  from the 2026-10-31 prereg — same metric, same comparison structure.
* **P1: NO THRESHOLD.** By decision (D85), the P1 interaction is reported as
  an estimate with a 95% interval and a standardised effect size, and is not
  tested against a smallest effect of interest. It is not described as
  confirmatory anywhere in the report, and no threshold is added after the fact.
  The writeup gives the estimate, the interval, and a plain-language reading of
  whether it is large relative to plausible football consequences.
* Applies to P2 only: a null is informative only if the interval excludes the
  smallest effect of interest; otherwise it is reported as inconclusive. P1 has
  no equivalence test and no null claim.

## 13. Validation (Phase 5)

* **Within-player rest permutation** — shuffle `inter_snap_rest_s` across snaps
  **within each (rusher, game)**, leaving load, outcome and controls in place;
  refit the P1 model and compare the observed interaction with the permutation
  null. The shuffle preserves each block's rest multiset and never moves a value
  across players or games; both are asserted per replicate. 199 replicates,
  seeded; p resolution 1/200 = 0.005 by construction and stated as such. Snaps
  in singleton blocks are counted and reported, never moved.
* **Context controls**: garbage time (Q4 margins 4 / 21 / 28) and the final two
  minutes of each half (quarters 2 and 4, clock ≤ 120 s) are re-estimated.
* **Obvious passing situations**: **SKIPPED BY DECISION** (Conor, 2026-11-04:
  "Don't care, skip"). Recorded in D83 and `notes/blockers.md` B-DL-IDR-01, and
  emitted as `SKIPPED_BY_DECISION` in every artefact. The writeup states that
  two of three context controls ran and the third was deliberately skipped.
* No other validation check is run: placebos, rotation/survivorship panels and
  mechanical-coupling checks belonged to the superseded spec and are not part of
  this one.

## 14. Multiplicity and reporting

* Two confirmatory tests: the P1 interaction and the P2 comparison. Everything
  else is secondary or exploratory and labelled so in every table.
* All fitted models are reported, including non-converged ones and the ladder
  rung actually used. No model is dropped for an unfavourable estimate.
* Every number in a full-run report comes from an artefact stamped `FULL`.
  Sample artefacts may never be quoted as results.
* Sample mode is a machinery check and is stamped
  `PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT` throughout.

## 15. Stopping conditions (bind the full run)

Stop and report, without adjusting the design, if any of the following holds:

* matched DL below the configured minimum, or median pass-rush snaps per rusher
  below 100;
* baseline `a_0` ICC below 0.50;
* the rusher's own tracking row is absent on more than half of his primary
  snaps, so that the deficit is unmeasurable for most of the sample;
* `rest_sequence_complete` fails on more than half of candidate snaps, so rest
  cannot be computed;
* fewer than half of rusher-games are permutable in Phase 5 (everything
  singleton), which would make the validation test vacuous;
* null semantics unresolved, or a required field missing.

If only P2's comparison is unestimable, P1 continues and P2 is reported as
`NOT_ESTIMABLE` with the reason.

## 16. What this prereg forbids

* Changing the exposure, outcome, censoring rule, control list, load definition,
  window, grid, CV scheme, metric or threshold after seeing full-data results.
* Selecting the load window, the absolute threshold or the random-effect
  structure by within-sample fit.
* Reporting a sample-mode number as a finding.
* Substituting a proxy for an unavailable label (win rate, team blitz) instead
  of reporting it as unavailable.
* Treating the deficit as a pure physiological measure: `a0_snap` is derived
  from tracking, and the claim is a kinematic deficit under the stated controls,
  not proven fatigue.

---

**FROZEN 2026-11-04 · IDR spec · D85: P2 is the single confirmatory test; P1 is
reported as an estimate with its interval and no threshold. Season hold-out
withdrawn.**
