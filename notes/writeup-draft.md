# Burst vs. jump: how much of a pass rusher's get-off is legs, and how much is reading the snap?

<!--
DRAFT SKELETON — 2026-11-06. Not a result. No full-data run has happened.

Two kinds of numbers appear in this file, and nothing in between:

1. FROZEN DESIGN CONSTANTS (onset threshold, sampling rate, rep minimum,
   word and figure caps). Each is cited to `notes/prereg.md` (frozen at
   `ca8e2a7`) or to `TASK.md`. They are design choices, not estimates.
2. RESULT FIELDS, written only as `{{PENDING_FULL_RUN: <artefact>.<path>}}`,
   where `<artefact>` is `decomposition` (outputs/phase4/decomposition.json)
   or `validation` (outputs/validation/validation.json). Every token names an
   exact path in that file; `src/writeup_render.py` fills them and refuses to
   fill anything from sample provenance.

No result value is typed into this draft, estimated, rounded or described as
"approximately". A section that depends on the full run reads as an empty
frame with named fields until the renderer has a FULL-stamped artefact.
-->

## 1. Question

A pass rusher's get-off is usually treated as one quantity: how fast he reaches
the line after the snap. That number mixes two things. Part of it is physical —
the time a given body needs to cover a given distance, which the Combine measures
with no opponent, no cadence and no ball to react to. The rest is everything
else: the read, the first step, the stance, the technique, the alignment.

We ask what share of the **between-player** variance in get-off is accounted for
by Combine-predicted physical time, and what share is left to a residual player
effect that we call **jump**. We then ask whether that residual is a stable trait
— reliable across a player's games — and whether it lines up with a direct
tracking measure of reaction latency (snap to first movement).

The design is a variance decomposition, not a causal model. "Jump" is a residual:
it holds anticipation if anticipation exists, but also stance, start technique,
alignment, opponent quality and every definitional difference between the two
settings. Nothing here measures football intelligence. Claims are limited to
defensive linemen who reached the NFL, because only drafted players have game
tracking: the population is Combine attendees from the 2023–2025 classes with at
least one recorded NFL pass-rush rep.

The paper reports an interval, not a verdict: no smallest effect of interest, no
confirmatory threshold, no pass/fail rule (prereg §10, frozen at `ca8e2a7`). A
small jump share with a wide interval is reported as a small estimate with a wide
interval, with the design's resolution limit beside it.

## 2. Data audit

The analysis joins two tracking sources through the player identifier: Combine
drill tracking, and regular-season game tracking with the play and player tables
that carry the snap, the line of scrimmage and the alignment label.

The primary snap set is every defensive-line pass-rush rep in the release:
players whose in-game `lined_up_position` is edge or interior line, on pass
plays. The blitz exclusion is deliberately **not** applied: the release has no
blitz-call field and the only candidate flag behaves like a participation flag,
so the primary set is all DL pass-rush reps and the writeup never claims a
non-blitz sample. Non-DL players are excluded, including cornerbacks who
rushed; that exclusion is counted and reversible.

Rusher–blocker assignments do not exist in the release. No assignment is
inferred from tracking geometry, and no blocker term enters any model, so the
physical/jump split is unadjusted for opponent quality and double-teams.

Stance is not labelled in any released file: there is no three-point/stand-up
column and no Combine start-type column. No stance rule or proxy is invented,
and no stance-specific calibration is estimated; stance variation is absorbed
into the residual and named as part of what "jump" contains.

The outcome is the **tracked line-of-scrimmage crossing**: the interpolated time
from the snap event to the first tracked position past the derived line. The
published `player_get_off` column is used as a validation check only. It never
enters a model and never replaces the outcome, because its definition differs
from a line crossing (Section 6). No pressure field enters any model: the
pressure secondary was withdrawn before any model was fitted.

The fitted sample is `{{PENDING_FULL_RUN: decomposition.pooled.n_players}}`
players and `{{PENDING_FULL_RUN: decomposition.pooled.n_reps}}` reps across
`{{PENDING_FULL_RUN: decomposition.pooled.n_team_games}}` team-games, with
`{{PENDING_FULL_RUN: decomposition.edge.n_players}}` edge and
`{{PENDING_FULL_RUN: decomposition.interior.n_players}}` interior players fitted
separately. Admission needs the frozen minimum of 20 primary-set reps (prereg
§9) and a tracked Combine forty attempt; players without one are excluded, not
imputed, and each gate's exclusion share is reported.

## 3. Physical-time construction

For each rusher we ask a narrow question: starting from first movement, how long
does his Combine body need to cover the distance he actually has to cover on a
given snap?

**Onset** is defined once and applied identically in both settings (prereg §4):
the first frame at which speed reaches 1.0 yd/s and stays at or above it for two
consecutive frames at 10 Hz. The threshold is absolute, not baseline-relative.
If a rep has no detectable onset it is flagged and excluded from onset-dependent
quantities; no fallback onset is invented.

**Distance** is the pre-snap depth to the line along the direction of travel:
the perpendicular distance from the tracked snap position to the derived line. It
is the distance the outcome times, so predictor and outcome refer to the same
event. No global stance or start-type offset is applied: it would need labels
that do not exist (Section 2), and the calibration artefact is a placeholder a
full run refuses to read.

**Interpolation** is model-free. Each attempt contributes a tracked
distance–time curve; physical time at a target distance is interpolated from that
curve, not fitted from a speed model. A parametric speed–time fit is a
cross-check only. Attempts are pooled per player hierarchically with shrinkage,
and the posterior mean **and** standard error are carried forward; no player is
represented by his best attempt.

The construction is judged by a coefficient, not a match. If the two settings
were identical, the coefficient on physical time in Section 4 would sit near one.
It is reported with its interval, and a value away from one is read as evidence
about setting differences — stance, start type, surfaces, the presence of an
opponent — rather than smoothed away.

## 4. Decomposition

The primary model is a hierarchical regression of the tracked crossing time on
physical time, alignment, pre-snap depth, a player random effect, a team-game
random effect and a rep-level residual (prereg §7):

> crossing = a + b · physical(depth) + alignment + depth + u(player) + v(team-game) + e

Physical time is **latent**, with its posterior standard error carried from
Section 3, so a player's curve is never plugged in as exact. The model is fitted
by EM on the mixed-model equations with regression calibration; intervals are
player-cluster bootstrap intervals, not Bayesian credible intervals, because no
sampler is available on the fitting host. The uncorrected fit is reported beside
it so the size of the correction is visible.

The split uses **between-player variance** as the denominator: the physical and
player-effect shares sum to one. The within-player remainder is a separate share
of **total** variance and is never mixed into that split.

Pooled, the coefficient on physical time is
`{{PENDING_FULL_RUN: decomposition.pooled.b}}`
(95% interval `{{PENDING_FULL_RUN: decomposition.pooled.bootstrap.b_ci95}}`).
The physical share of between-player variance is
`{{PENDING_FULL_RUN: decomposition.pooled.share_phys}}`
(95% interval `{{PENDING_FULL_RUN: decomposition.pooled.bootstrap.share_phys_ci95}}`)
and the jump share is
`{{PENDING_FULL_RUN: decomposition.pooled.share_u}}`
(95% interval `{{PENDING_FULL_RUN: decomposition.pooled.bootstrap.share_u_ci95}}`).
The implied jump standard deviation is
`{{PENDING_FULL_RUN: decomposition.pooled.jump_sd_ms}}` ms, and the within-player
remainder is `{{PENDING_FULL_RUN: decomposition.pooled.within_player_remainder_total}}`
of total variance. Edge and interior are fitted with the same specification:
jump share `{{PENDING_FULL_RUN: decomposition.edge.share_u}}` for edge and
`{{PENDING_FULL_RUN: decomposition.interior.share_u}}` for interior.

The fit used convergence rung `{{PENDING_FULL_RUN: decomposition.pooled.rung_used}}`
of the frozen ladder. The uncorrected (no measurement-error) fit gives a physical
share of `{{PENDING_FULL_RUN: decomposition.pooled.ladder.rung_1_no_me_correction.share_phys}}`;
the difference between the two is the correction, and both are reported rather
than the preferred one alone.

**Resolution.** No confirmatory threshold is applied. The design's resolution
limit, recomputed from the realised sample rather than copied from the prereg, is
a smallest detectable jump SD of
`{{PENDING_FULL_RUN: validation.resolution.smallest_detectable_jump_sd_ms_realised}}` ms
at an effective player count of
`{{PENDING_FULL_RUN: validation.resolution.p_eff_realised}}`. This is a statement
of what the design can distinguish from zero at its floor, not a bar the estimate
must clear.

## 5. Reliability

A residual is only a trait if it repeats. Two estimates are reported, because
they answer slightly different questions.

The **split-half** estimate correlates each player's residual estimated from
odd-numbered games with the same player's residual from even-numbered games, then
applies the Spearman–Brown correction for the halved sample. It asks whether the
player ordering is stable from one set of games to another. The **model-based**
estimate is the player variance divided by itself plus the mean squared standard
error of the player effects; it asks how much of the observed spread in the
residuals is signal.

Pooled, split-half reliability is
`{{PENDING_FULL_RUN: decomposition.pooled.reliability.split_half.spearman_brown}}`
and model-based reliability is
`{{PENDING_FULL_RUN: decomposition.pooled.reliability.model_based.reliability}}`.

Agreement is the second half of the interpretation. The residual is correlated
with mean snap-to-onset latency across players
(`{{PENDING_FULL_RUN: decomposition.pooled.agreement.u_vs_onset_latency.r}}`), and
physical time is correlated with mean onset-to-crossing time
(`{{PENDING_FULL_RUN: decomposition.pooled.agreement.phys_vs_onset_to_cross.r}}`).
Agreement supports reading the residual as reaction; disagreement is reported as
a finding, not treated as a failure. Neither correlation identifies anticipation:
the latency measure shares the onset definition, the sampling rate and the
tracking quality with the outcome.

## 6. Label validation

The published `player_get_off` column is not the outcome and is never a model
input. It is checked against the tracked crossing because the two should describe
the same football event, and the check is what tells the reader how far the
release's own label sits from the constructed one.

The check is definitional, not a correction. On the sample the label behaves as
the crossing of a plane beyond the derived line rather than at it — an
`UNVALIDATED SAMPLE OUTPUT` distance, not a constant, not an offset, not
hard-coded, and never added to any distance or time. The full-run distance comes
from the crossing diagnostic stage and is inserted when it exists.

Three mechanical checks ask whether the split survives obvious perturbations.
Shuffling physical time across players within position is the permutation check:
the observed physical share sits at percentile
`{{PENDING_FULL_RUN: validation.checks.permutation.observed_share_phys_percentile}}`
of `{{PENDING_FULL_RUN: validation.checks.permutation.n_requested}}` refits, with a
one-sided Monte-Carlo p of
`{{PENDING_FULL_RUN: validation.checks.permutation.one_sided_monte_carlo_p}}`; a
boundary estimate is reported as uninformative rather than as a pass. The height/weight placebo gives a pseudo-physical share of
`{{PENDING_FULL_RUN: validation.checks.placebo.placebo.share_phys}}` against the
real `{{PENDING_FULL_RUN: validation.checks.placebo.real.share_phys}}`. Restricting
to snaps with clean onset detection drops
`{{PENDING_FULL_RUN: validation.checks.snap_quality.fitted_set.share_dropped}}`
of the fitted set; the shares on the clean subset are reported in the artefact.
The onset-threshold sweep, the depth- and alignment-removed refits and the
equal-snap-per-player refit are reported beside the primary, not used to select
it.

## 7. Limitations

**Stance and start definition.** Stance is unlabelled. The Combine start is a
track and a timer; the game start is a stance, a cadence and a ball. A shared
onset *definition* does not make the settings the same, and the residual absorbs
the difference.

**Alignment, role and opponent.** Alignment is labelled coarsely and enters the
model, but shade and assignment do not. Rotation is unmodelled: a specialist's
reps are a selected subset of situations, so a stable residual can be a stable
role. With no rusher–blocker assignments the split is also unadjusted for
opponent quality and double-teams.

**Resolution in the first second.** At 10 Hz one frame is a tenth of a second — a material
fraction of a typical get-off. Interpolation recovers sub-frame timing
but not sub-frame detection, and the residual frame error inflates within-player
variance.

**Shared baseline error.** If a player's onsets are systematically early or late,
the error is shared across all his reps and does not average out. It can
manufacture a stable residual — the failure mode that most resembles the result
we are looking for. The measurement-error treatment covers physical time, not
this; the direct latency correlation is the only anchor with a different error
structure.

**Spot error.** The line is derived from an integer yard line, so the ball spot
carries a fractional error that varies play to play. It cancels within a play,
not across plays, and lowers the effective reliability of the outcome. No offset
is applied: a correction needs data the release does not contain.

**Selection.** Only drafted players appear, so the results describe
differentiation among Combine-calibre players, not among football players in
general. The 2025 class has at most one NFL season, so the year-to-year question
is answerable for the 2023 and 2024 classes only.

**Estimator.** The intervals come from a bootstrap, not a posterior; a Bayesian
fit could widen or narrow them, especially near a variance boundary. Edge and
interior fits are smaller and their intervals wider.

**What "jump" is not.** It is not a measurement of anticipation, reaction time or
football intelligence, and it contains stance, technique, alignment, opponent and
definitional differences alongside anything cognitive.

---

<!-- END OF WRITEUP BODY -->

**Word count:** 1,954 words of prose, counting each placeholder token as one
word (i.e. the projected final count). Final ceiling 2,000 words (`TASK.md` §6
and `notes/rules.md` L1 — the cap itself is `UNVERIFIED` for 2027).

## What still has to be filled

- [ ] Every placeholder token above — the renderer lists any it cannot resolve
      by name; nothing is filled from a sample-provenance artefact.
- [ ] Section 2 sample sizes and per-gate exclusions from the FULL Phase 3/4 run.
- [ ] Section 4 coefficient, shares, intervals, rung and uncorrected comparison.
- [ ] Section 5 both reliability estimates and both agreement correlations.
- [ ] Section 6 permutation percentile and p, placebo shares, clean-subset drop
      share — with the FULL reading of each, including a boundary estimate
      reported as uninformative.
- [ ] Section 7: confirm the spot-error and resolution numbers against the FULL
      artefacts rather than carrying the sample figures.
- [ ] Four figures from `render_figures` on FULL artefacts (Section 6 of the
      notebook); none exists yet.
- [ ] Trim to the 2,000-word cap once values replace tokens.
