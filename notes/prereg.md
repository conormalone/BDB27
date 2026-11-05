# Pre-registration — burst vs. jump (BDB27)

**Status: FROZEN.** Frozen 2026-11-05 by Conor's decision "just report whatever
it gives you" (22:16 GMT): there is **no confirmatory smallest-effect-of-interest
threshold**. The primary result is a share with a 95% interval, and the design's
resolution limit is reported alongside as a resolution statement (§10). After
this commit no element below may change; any deviation is logged in
`notes/decisions.md` and reported as exploratory.

**Provenance.** Written 2026-11-05 from the binding `TASK.md` at commit
`a57bd7c`, the four locked Conor decisions of 2026-11-05 (D110–D113), the
crossing diagnostic (D109), the Phase 3b reliability evidence, and the design
power calculation (D114). Numbers are stamped per §13. No full-data result has
been seen; the sample is never interpreted or tuned against.

---

## 1. Question and estimand

Split between-player differences in defensive-line **get-off** into a
**physical** part predicted from the Combine 40-yard dash and a residual
**"jump"** part, then ask whether the residual is a stable trait.

**Primary estimand.** The share of **between-player variance** in the outcome
attributable to `b · phys_i(d_ij)` versus `u_i`, with posterior intervals,
pooled and for edge and interior linemen. Denominator B: between-player
variance, not total variance. The within-player remainder is reported separately
as a share of total variance.

**Primary secondary.** Reliability of `u_i` across games, and its agreement with
the direct reaction measure (snap-to-onset latency). Agreement supports the
interpretation of "jump" as reaction; disagreement is a finding.

**Exploratory.** Within-player year-to-year change in the residual.

**Claims are limited** to DL who reached the NFL (only draftees have game
tracking). "Jump" is a residual, not a measurement of anticipation or football
IQ. It contains stance, technique, alignment and onset-definition differences.

---

## 2. Data and analysis sets

**Study population.** Combine attendees from the 2023–2025 classes with ≥1 NFL
pass-rush rep in the release. This is a selected population (B5); results do not
generalise to undrafted DL.

**Primary snap set.** All DL pass-rush reps (`lined_up_position ∈ {EDGE,
INTERIOR_LINE}`) on pass plays (`pass_result ∈ {C, I, S, R, IN}`). **The blitz
exclusion is dropped (D113):** every DL pass-rush rep stays. The raw `blitzing`
flag is descriptive only. Non-DL players remain excluded. This is a documented
limitation, not a claim of a non-blitz sample.

**Fittable set for Phase 4.** A primary-set player with a tracked Combine
40-yard-dash attempt. Players without one contribute nothing to the
decomposition and are not imputed.

**Outcome availability.** The outcome is the tracked LOS crossing (§3). A rep
enters the Phase 4 model only if the crossing is computable and the pre-snap
depth is derivable. Reps whose crossing precedes the detected onset are flagged
and retained as evidence for the onset check, not repaired. The share of reps
dropped by each gate is reported.

---

## 3. Outcome definition — the tracked line-of-scrimmage crossing

**Primary outcome (`cross_time_s`).** The time from the `ball_snap` event to the
first tracked position of the rusher that crosses the **line of scrimmage**,
computed from `yardline_side`, `yardline_number` and `play_direction`. The
crossing is obtained by linear interpolation between the two frames that
straddle the line, on the tracked x-position along the direction of travel. This
is Phase 3's existing `cross_time_s`; no formula changes are made (D109 found no
defect in the LOS formula, sign convention or depth sign).

**This outcome is used on both sides of the model.** `phys_i(d_ij)` is evaluated
at the distance to this same crossing — the pre-snap depth (§5). The outcome and
the predictor therefore refer to the same event.

**`player_get_off` is a validation check only (D110).** It never enters a model,
never replaces the outcome, and is never used to calibrate an offset. It is
compared with the tracked crossing and reported as validation evidence. The
measured disagreement is **median +0.379 s** (p10 +0.191, p90 +0.664, SD 0.205;
1,819 sample reps) and a **median plane offset of 0.86 yd** (sample). Both
numbers are `UNVALIDATED SAMPLE OUTPUT` and are **not** model inputs, **not**
constants, and **not** hard-coded anywhere.

**Measurement error in the outcome.** The derived line is validated against the
offensive line's body geometry and bounded at ≈0.3 yd; the integer
`yardline_number` leaves a per-play ball-spot error of ≈0.29 yd (uniform ±0.5,
SD 0.289; the 0.29 is a sample estimate, `UNVALIDATED SAMPLE OUTPUT`). In time
terms these are ≈0.15 s and ≈0.14 s at the median crossing
speed. These errors are carried in the model's residual; they are not corrected
with an invented offset. They reduce the effective reliability of the outcome
and are a stated limitation.

**Outcome definitions that are dropped.** `quick_pressure`, `time_to_pressure`,
`time_to_qb_hurry`, `sack` and all other pressure fields are **not outcomes and
not model inputs** (D112). The `quick_pressure` null → false rule is withdrawn.
No binary or survival outcome model is fitted. No null→false assumption is made
anywhere.

---

## 4. Onset definition (identical in both settings)

Onset is **D87**: the first frame at which speed reaches **1.0 yd/s** and stays
at or above it for **2 consecutive frames** at 10 Hz (0.2 s), searched from the
snap in game tracking and from the start of the attempt in the Combine. The
threshold is **absolute**, not baseline-relative; the pre-onset baseline
(0.5 s window, ≥3 frames) is estimated and reported only as context.
Configuration: `bdb27.onset` (`speed_yds: 1.0`, `sustained_frames: 2`,
`hz: 10`, `max_latency_s: 2.0`, `search_window_s: 3.0`).

The definition is identical in both settings. If a rep has no detectable onset,
it is flagged `ONSET_NOT_DETECTED` and excluded from onset-dependent quantities;
it is not given a fallback onset. The onset threshold sensitivity grid in
`config.yaml` is a robustness check only and cannot change the primary
definition.

---

## 5. Distance definition

For each rep, `d_ij` is the **pre-snap depth to the line of scrimmage along the
direction of travel**: the perpendicular distance from the rusher's tracked
position at the snap to the derived LOS. This is the distance the rusher must
cover to cross the line — the same event as the outcome (§3). `phys_i(d_ij)` is
obtained by interpolating the player's Combine distance–time curve at exactly
that distance, using the model-free interpolation specified in Phase 2; the
parametric speed–time fit is a cross-check only.

**No offset is applied.** The 0.86 yd plane offset is a property of the published
label (sample median), not of the tracked crossing, and is not added to `d_ij`
or to any time. If a distance correction is ever contemplated it must be
estimated on full data under a preregistered rule and reported as a distance,
never as a time offset (D109, D110). It is not contemplated here.

**Label validation.** The published label times a median ≈1.50 yd of travel
(sample), not the pre-snap depth (median 0.65 yd), which is why it is
validation-only. This is a definitional fact about the label, not a correction
to the outcome.

---

## 6. Stance handling

**No stance label exists in any released file** (B8; no data dictionary, no
stance column, no start-type column for the Combine). No inference rule is
invented: stance is **not** a covariate, not a stratifier and not imputed.

Stance variation is therefore **absorbed into the residual `u_i` and the
rep-level error**. This is a documented component of what "jump" contains: the
residual is everything the legs do not explain, including stance, technique,
alignment and start type. The writeup must say this. A global stance/start-type
calibration offset is not estimable and `outputs/calibration.json` remains
`PLACEHOLDER`; Phase 4 does not read it.

Alignment (edge vs. interior) *is* labelled and enters the model as a fixed
effect. Depth enters as a fixed effect. Neither is a stance proxy.

---

## 7. Variance-decomposition model

**Primary model** (rep `j` of player `i` on team-game `g`):

```
cross_time_ij = a + b * phys_i(d_ij) + u_i
                + f_alignment(ij) + f_depth(ij)
                + v_g + e_ij

u_i  ~ N(0, sigma_u^2)        player "jump" effect
v_g  ~ N(0, sigma_v^2)        team-game random effect
e_ij ~ N(0, sigma_e^2)        rep-level residual
phys_i(d) ~ N(phys_hat_i(d), se_i(d)^2)   measurement-error model
```

`phys_i(d)` is **latent** with the posterior mean and SE carried forward from
Phase 2's hierarchical pooling of attempts. It is never a best attempt and never
a point value without its SE. `f_alignment` is a fixed edge/interior effect;
`f_depth` is a fixed effect for pre-snap depth (linear plus a spline sensitivity;
the primary form is fixed in the model code before the full run).

**No blocker term.** Rusher–blocker assignments do not exist (D98); nothing is
inferred from geometry.

**Priors (weakly informative, fixed before the full run).** `a ~ N(0, 1)`;
`b ~ N(0, 1)`; `sigma_u, sigma_v, sigma_e ~ Half-Normal(0, 0.5)` on the SD
scale; alignment and depth coefficients `~ N(0, 0.5)`. Priors are not tuned on
the sample. Any change after seeing full-data results is a logged deviation.

**Reported quantities.**
- `b` and its 95% interval. Physics predicts `b ≈ 1`. A `b` far from 1 means a
  systematic difference between the Combine and in-game settings, and is
  reported as such — not smoothed away.
- `share_phys = Var(b·phys_i) / [Var(b·phys_i) + sigma_u^2]` and
  `share_u = sigma_u^2 / [Var(b·phys_i) + sigma_u^2]` — denominator B, the
  between-player variance.
- The within-player remainder as a share of **total** variance, reported
  separately and never mixed into the between-player split.
- All of the above pooled, edge and interior. Pooled is primary; edge and
  interior use the same specification with smaller samples and wider intervals.

**Fallback ladder** (logged; the trigger is non-convergence, `Rhat > 1.01` or
effective sample size `< 400` for a reported quantity):
1. simpler variance model with `phys` as a fixed covariate and no
   measurement-error correction — report both estimates side by side;
2. per-player estimates with empirical-Bayes shrinkage;
3. random-intercept-only model.
The ladder is not a licence to substitute a preferred number: the rung used is
reported with the reason.

**Reliability of jump.** Split-half across odd/even games of player-level `u`,
and model-based `sigma_u^2 / (sigma_u^2 + mean SE^2)`. Both are reported.

**Agreement with direct latency.** Correlation between player-level `u` and mean
snap-to-onset latency, and between `phys` and mean onset-to-crossing time. Both
are reported; disagreement is a finding, not a failure.

**Exploratory year-to-year analysis.** Within player, decompose change in the
outcome into change in `u` and change in physical terms. Weight and role changes
are noted as confounds. This analysis cannot confirm the primary.

---

## 8. Reliability threshold and estimator

**Estimator.** The **three-level** model (reps nested in games nested in
players), fitted by the nested unbalanced ANOVA method of moments in
`src/reliability.py`:

```
y_igj = mu + u_i + v_ig + e_igj
reliability_i = sigma_u^2 / (sigma_u^2 + sigma_v^2/G_i + sigma_e^2/n_i)
```

This is the estimator that reproduces the observed split-half attenuation curve
(RMSE 0.018 against 0.072 for the two-level model). The two-level
`k = sigma_e^2/sigma_u^2 = 6.17` is a cross-check, not the inclusion rule.

**Threshold.** A player's mean outcome is admissible only if its three-level
reliability is **≥ 0.5** (the Phase 1 gate threshold, `audit.icc_min`). At the
20-rep minimum (§9) every admitted fittable player clears it (minimum 0.667), so
the threshold is **non-binding** at this cut. Raising it to 0.7 or 0.8 would
bind and shrink the fittable set; it is not raised.

**Important limit.** These reliabilities are for the **raw mean outcome**, an
**upper bound** on the reliability of the residual "jump": removing 25% of
between-player variance gives 0.869, removing 50% gives 0.815 (FULL evidence).
The true reliability of `u_i` is unknown until Phase 4 runs and cannot be
higher than these numbers.

---

## 9. Minimum snaps per player

**Frozen minimum: 20 primary-set reps per player.** This admits **115**
players, of whom **90** are fittable (have a Combine 40 attempt); `P_eff = 89.3`.

**Why 20.** The earlier rule — "the most restrictive cut whose fittable P still
resolves a 50 ms threshold" — depended on a smallest-effect-of-interest gate
that Conor withdrew on 2026-11-05 22:16 (§10). With no threshold, 20 reps is the
**least restrictive** candidate (25/30/40/50/60 are the alternatives in the
resolution table) and maximises fittable players, which is the defensible
choice when the aim is to report an interval rather than to pass a test.

Players below 20 reps are not admitted even though some are fittable: a
1–6-rep mean is mostly noise (every player below 0.5 reliability has ≤6 reps),
and admitting them lowers `P_eff` without adding information. This is a
headcount choice, not a reliability-threshold choice.

---

## 10. Resolution limit and interval reporting — no confirmatory threshold

**Conor's decision (2026-11-05 22:16): "just report whatever it gives you".**
There is **no smallest-effect-of-interest gate**, no confirmatory jump-share
threshold, and no pass/fail rule. The primary result is a **share with a 95%
interval**. The design's resolution limit is reported alongside as a
**resolution statement only** — it says what jump SD the design can distinguish
from zero at the design floor, not a threshold the estimate must pass.

**Resolution estimator.** The design floor `z·sqrt(2/P_eff)` (z = 1.96), the
Phase 1(i) / Phase 3b estimator, with `P_eff` the Kish count of the fittable
players' three-level reliabilities. The smallest detectable jump SD is
`between_sd · sqrt(floor)`. Functions: `src/stats.py`
(`effective_players`, `min_detectable_share_effective`,
`variance_share_from_sd`, `sd_from_variance_share`,
`players_required_for_share`). This is a design approximation, not a posterior
width; the Phase 4 fit is the confirmation.

**Resolution table** (FULL evidence; produce with `python src/power_calc.py
--mode sample`; artefact `outputs/prereg/power.json`):

| min reps/player | P admitted | fittable P | median reliability, fittable (three-level) | P_eff | smallest detectable jump SD (ms) |
|---:|---:|---:|---:|---:|---:|
| 20 | 115 | 90 | 0.943 | 89.3 | 54.4 |
| 25 | 112 | 87 | 0.944 | 86.4 | 54.8 |
| 30 | 109 | 85 | 0.945 | 84.5 | 55.1 |
| 40 | 103 | 80 | 0.945 | 79.7 | 55.9 |
| 50 | 99 | 77 | 0.946 | 76.8 | 56.5 |
| 60 | 92 | 71 | 0.949 | 70.9 | 57.6 |
| no cut (all) | 158 | 123 | 0.897 | 108.0 | 51.8 |

**Resolution statement.** At the frozen 20-rep minimum the design resolves a
jump SD of about **54 ms**; with every fittable player it resolves about
**52 ms**. The release supplies at most 123 fittable players, so a 50 ms jump SD
is at or just below the design floor at every candidate cut. This is what the
design can resolve, not a null-result rule.

**What this means for inference — fixed now, before results.**
- There is no "confirmed" or "null" verdict. A small estimated share with a
  wide interval is reported as a small estimate with a wide interval.
- The study does **not** claim the absence of a 50 ms jump when an interval is
  wide; the resolution statement is the honest bound on what the interval can
  show.
- The table's attenuation uses the Kish count, which weights players by
  *relative* reliability. A cruder `P × mean reliability` attenuation gives a
  slightly larger smallest detectable SD at every cut; the resolution statement
  is unchanged.
- No redefinition, no switching the denominator, and no post-hoc threshold.
  The resolution limit is recomputed from the final sample and the realised
  `P_eff`, never copied from the table.
- The table is optimistic for the locked tracking outcome: it uses reliability
  measured on the published label, and the tracked crossing adds the ≈0.29 yd
  sample spot error (`UNVALIDATED SAMPLE OUTPUT`). The realised resolution is
  therefore expected to be larger than the table.

---

## 11. Analysis plan and decision rules

1. Fit the primary pooled model on the fittable set at the minimum in §9.
2. Report `b`, the phys/jump variance shares and the within-player remainder,
   with 95% intervals; then edge and interior.
3. Report split-half and model-based reliability of `u`, and agreement with
   onset latency.
4. Report the design's resolution limit (smallest detectable jump SD) actually
   achieved, recomputed from the final sample and the realised `P_eff` — never
   copied from §10 — as a resolution statement, not a pass/fail test.
5. Sensitivity analyses, pre-specified: onset-threshold grid; clean-onset
   subset; depth and alignment effects removed; equal-snap weighting per player;
   edge/interior separate fits. All are reported, not selected.
6. Phase 5 permutation (shuffle Combine physical time across players within
   position) and placebo (height/weight alone) checks.
7. No outcome model, no pressure field, no binary or survival analysis
   (D112).
8. Multiple quantities are reported with intervals; no thresholded
   "significance" claim and no p-value screening. The primary result is the
   interval for the jump share/SD. There is no confirmatory threshold (§10).

**Missing data.** No imputation of Combine times. Players without a 40 attempt
are excluded from the decomposition. Reps without a computable crossing or depth
are excluded and counted. Missingness is reported by season, team and alignment;
it is not assumed to be random.

**Determinism.** Fixed seeds from `config.yaml`; one code path for sample and
full; all stages checkpoint and skip finished work unless `--force`.

---

## 12. Deliverables

Four figures (D112 dropped the three-model outcome figure):

1. The variance split with intervals, edge vs. interior.
2. Combine-predicted physical time versus observed tracked crossing, with named
   players off the diagonal.
3. Reliability and agreement of the jump.
4. One rusher's rep-level story.

Report sections: question, data audit, physical-time construction,
decomposition, reliability, label validation, limitations. The label validation
section reports the `player_get_off` check (§3), not an outcome model.

---

## 13. Provenance of the numbers in this document

| quantity | value | stamp | use |
|---|---:|---|---|
| between-player SD | 0.1004 s | **FULL** (`outputs/phase3b/phase3b_summary.json`) | resolution statement |
| between-player share (noise-corrected) | 0.139 | **FULL** (same) | context only |
| three-level reliability at median | 0.898 | **FULL** (same) | resolution, upper bound |
| two-level `k` | 6.17 | **FULL** (same) | cross-check only |
| fittable players (all) | 123 | **FULL** (`reliability_by_player.csv`) | resolution |
| fittable players at 20 reps | 90 | **FULL** (same) | min-reps |
| smallest detectable jump SD, 20 reps | 54.4 ms | **FULL** (`outputs/prereg/power.json`) | resolution statement |
| smallest detectable jump SD, all fittable | 51.8 ms | **FULL** (same) | resolution statement |
| players at which 50 ms would be resolved | 125 | **FULL** (same) | resolution context |
| label − tracked crossing, median | +0.379 s | **UNVALIDATED SAMPLE OUTPUT** (`outputs/crossing_diagnostic/`) | label validation only |
| plane offset, median | 0.86 yd | **UNVALIDATED SAMPLE OUTPUT** (same) | label validation only; never hard-coded |
| label travel, median | ≈1.50 yd | **UNVALIDATED SAMPLE OUTPUT** (same) | why the label is validation-only |
| integer-yard spot error | ≈0.29 yd / ≈0.14 s | **UNVALIDATED SAMPLE OUTPUT** (diagnostic); the ±0.5 yd rounding is definitional | limitation |

Onset thresholds, depth bounds, the frozen 20-rep minimum and z = 1.96 are
**design choices** in `config.yaml` or fixed by Conor, not estimates. No sample
number is presented as a finding, and no smallest-effect-of-interest threshold
is used.

---

## 14. Freeze record

**Frozen 2026-11-05** by Conor's decision "just report whatever it gives you"
(22:16 GMT). The freeze removes the confirmatory smallest-effect-of-interest
threshold: the primary result is reported as a share with a 95% interval and the
design's resolution limit is reported alongside it (§10). The 20-rep minimum
(§9), onset (§4), distance (§5), stance handling (§6), model form (§7),
reliability threshold (§8) and outcome (§3) are frozen as written.

The commit that sets `**Status: FROZEN.**` is the freeze; Phase 4 code may be
written only after it. After freezing, this file is not edited. Deviations are
logged in `notes/decisions.md` as exploratory and reported as such.
