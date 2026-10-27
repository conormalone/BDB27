# Pre-registration — DB in-game decay of effort fraction  ·  **DRAFT — NOT FROZEN**

> **Status: DRAFT — NOT FROZEN (2026-10-26).** This file is the design for the active task
> **"In-game decay of DB performance relative to the Combine ceiling"** (`TASK.md`, rewritten 2026-10-25;
> `notes/decisions.md` D51–D57). It **supersedes** the frozen WR/TE ceiling-binding pre-registration, which
> is archived verbatim at `notes/archive-tasks/prereg-wr-security.md`.
>
> **It is not binding yet.** Per `TASK.md` ("before the first full-data run, write `notes/prereg.md` fixing:
> effort definition, load definition, controls, garbage-time rule, minimum snaps per player, primary and
> secondary estimands, reliability threshold, and smallest effect of interest"), a `--mode full` run of
> `03_game_efforts` or later **must not start** until Conor approves this file and the status line reads
> **FROZEN**. Freezing authority: **Conor**. Freeze mechanics: `src/gate.py` (it currently freezes the
> archived security prereg; it must be repointed at this file when this one is frozen).
>
> **ACTIVE-TASK NOTE (2026-10-27, D59–D61).** The binding task is now **leverage × ceiling**
> (`TASK.md`; `bdb27_lc`); this file is still the DB-decay draft and its full rewrite remains
> `PENDING_REWRITE_FOR_LC_TASK`. **§17 is the exception**: it carries the frozen *route-depth control*
> definition decided in D61 and must survive that rewrite verbatim. It is **not** frozen as a
> pre-registration because the depth-coverage/power call it exposes is still Conor's (D61 §6).
>
> **This file contains design, not results.** No sample statistic, effect size, ICC value or n appears here
> as a design input — every unresolved value is either a stated design constant or an entry in the **TBD
> register (§13)** with the stage that fixes it. Where a threshold is a *placeholder* until the full run,
> it says so (`PLACEHOLDER_SAMPLE`), exactly as `00_thresholds` will stamp it. Observed audit values live in
> `outputs/audit/db_decay/` and are cited from `notes/decisions.md` (D53–D56), never as evidence *for* a
> design choice after the fact.
>
> **Known config mismatches are listed in §15** — this file is the design of record; `config.yaml` currently
> disagrees on three items and must be reconciled before stage 03 (it is owned by another agent right now,
> so nothing here edits it).

---

## 1. Population and unit

**Population.** Defensive backs — `players.nfl_position ∈ {CB, SS, FS, DB}` — with (a) Combine tracking
(`combine_tracking.csv`, ≥1 `FORTY_YARD_DASH` attempt so a ceiling can be fit) and (b) **≥1 NFL defensive
snap** in `player_play.csv`, seasons REG+POST (D49; preseason excluded).

**Unit of analysis.** The **effort episode within a player-game**. One row per episode in the stage-03
summary table; modelling stages read only that table (never frame data).

**Claim limit (must appear verbatim in the writeup).** *Claims are limited to DBs who reached the NFL and
appear in game data.* This is **selection on reaching the league and playing**, not "draftee-only": 24 of
the 118 DBs carry no draft slot (structural "undrafted" level) and **all 24 do appear in `player_play`**
(`players.csv`; `notes/rules.md` §9.1). Draftee-only selection (and its survivors) is a **stated limitation**
(`notes/limitations.md` §40), not something a design fix can remove.

**Not modelled.** College/non-NFL performance, offense, special teams. No claim about players who never
reached the NFL, and no claim that decay is physiological fatigue (it is a decline in realised effort
fraction under the stated controls).

## 2. Ceiling — single feature, with uncertainty

**Curve.** `v(t) = vmax · (1 − exp(−t/tau))` fit per 40-yard-dash attempt on the **smoothed speed** derived
from `x`/`y`. `a0 = vmax/tau` is reported but **excluded from the model** (§2.3).

**Feature set.** **`vmax` only.** A single-feature ceiling is a *consequence* of the pre-registered selection
rule, not a preference (§2.3).

**Attempt hygiene (pre-registered, already in config before the audit ran).** An attempt is excluded from the
ceiling fit if the fitted `vmax > 12.5 yd/s` or `vmax > 1.5 ×` the player's observed peak speed
(`db_decay.ceiling.sanity.vmax_yds_max`, `db_decay.ceiling.sanity.vmax_over_peak_max`). Excluded attempts are
**counted and reported**; the rule is data hygiene (an unidentifiable 2-parameter fit on a noisy attempt),
not a tuning knob, and it was fixed before any ICC was computed (D54).

**Pooling.** Hierarchical (empirical-Bayes) shrinkage across a player's attempts toward the DB-wide mean,
using the reliability-weighted form `grand + rel·(player_mean − grand)`,
`rel = n·ICC/(1 + (n−1)·ICC)`; the shrunken value **carries an SE** that is propagated to every downstream
estimate. **Never** a best attempt.

**Selection rule (fixed): ICC then VIF, ≤3 features.** Candidate features are the model-free kinematics
recomputed from `x`/`y` plus the envelope parameters (`vmax`, `tau`, `a0`). Rule
(`db_decay.ceiling.selection_rule = [icc, vif]`, `max_features = 3`):

1. retain candidates with test-retest ICC(1,1) ≥ the primary threshold (`db_decay.audit.min_icc_primary`,
   0.60); report every candidate's ICC **and** CI, including failures;
2. prune by VIF > `db_decay.ceiling.vif_max` (5.0) until ≤3 remain.

**`a0` exits the feature set** because it fails the ICC screen (audit g), i.e. it is **dropped as an
unreliable candidate**, not treated as a project-level stop condition. This was Conor's ruling (D53): the
task's rule is ICC-then-VIF, so a failing candidate leaves the set; the go/no-go gate is about whether
*enough* reliable features survive, not whether every candidate passes.

**Consequences, fixed here:**

- The ceiling is **single-feature (`vmax`)**. The load × ceiling interaction (§10) is therefore tested on
  **`vmax` only**; there is no acceleration-moderated ceiling test in this paper.
- Because the retained feature's ICC is only moderately high and its CI lower bound sits under the
  threshold (D53), the ceiling's **SE must propagate into every downstream estimate**, and a
  **measurement-error-corrected** estimate (attenuation correction on the ceiling's reliability) is reported
  **alongside** the naive one — never instead of it. A null that survives only the naive fit is not a null.
- `a0`, `tau`, cut/COD metrics are **reported descriptively** (they exist in the audit) but are **not**
  model inputs.

**Fit quality guards.** `tau` is bounded by the Combine-side envelope configuration (D29: 0.05–10 s); this
file adds no new bound and does not re-tune it. Attempts where the curve does not
identify (flat start, tracking gap inside the peak window) are flagged and reported, and the player's
ceiling falls back to the shrunk value with a wider SE. No silent substitution of a "good" attempt.

## 3. Effort definition (episodes)

All kinematics are **derived from `x`/`y`**. Specifically:

- **Speed:** Savitzky–Golay smoothing, **7-frame window, polynomial order 3**, applied to per-frame
  displacement speed within a player-game, then `s_t = |Δxy|/Δt`. Position noise is removed *before* the
  peak is taken, because a max over raw frames is an upward-biased estimator.
- **Acceleration:** the **time derivative of the smoothed speed** (`ds/dt`). Never the provided game `a`
  (it fails its definition check: r ≈ 0.003 vs `ds/dt`, bias +1.73 yd/s² — D55) and never a second
  difference of raw positions.
- **Never** the provided `dis` in either pipeline (Combine `dis` breaches the 5% tolerance on 75.6% of 40-yd
  frames; game `dis` is acceptable but is used only as a cross-check — D55). Path length and cumulative
  distance come from `x`/`y` in both pipelines.

**Episode definition (a high-intensity effort).** An episode starts when either branch is satisfied and ends
when it is no longer satisfied:

| Branch | Start condition | Sustain |
|---|---|---|
| Speed | smoothed speed > **max(0.85 × shrunken vmax, 6.0 yd/s)** | **≥ 0.3 s** |
| Acceleration | derived acceleration > **4.0 yd/s²** | **≥ 0.2 s** |

- The speed branch is **player-relative above the floor**: 85% of that player's own shrunken ceiling, but
  never below 6.0 yd/s, so a slow-ceiling DB cannot generate episodes by jogging.
- The acceleration branch is **absolute**: `a0` is not a ceiling feature any more (§2.3), so a
  fraction-of-`a0` threshold has no denominator. (The audit's placeholder threshold used `0.80 × a0` + a
  4.0 floor; that fraction is now inert — see §15.)
- **Merge rule:** two episodes separated by < 0.3 s below threshold are one episode (avoids splitting a
  single break into two). Episodes are **not** split at the ball-snap/whistle boundary mid-play; the episode
  inherits the play it starts in.
- **Truncation rule:** an episode touching the first or last frame of a tracked unit is flagged
  `truncated` and excluded from speed-peak-derived quantities (the tail is cut, so peaks are biased down),
  but kept for count-based load.
- Both thresholds are `PLACEHOLDER_SAMPLE` until `00_thresholds` writes `FULL` provenance (§13, T1).

## 4. Max-effort subset (primary) and all-efforts (secondary)

**Primary analysis = episodes that are least discretionary.** An episode is **max-effort** if either:

1. **Ball in the air:** the episode overlaps `pass_forward → pass_arrived` (or the pass outcome:
   `pass_outcome_caught` / `pass_outcome_incomplete` / `pass_outcome_interception`) on the same play, from the
   tracking `event` column; **or**
2. **Pursuit / tackle:** the episode overlaps a pursuit/tackle window — the player is **within 5 yd of the
   ball carrier** during the episode (requires the ball-carrier trajectory; if unavailable, see §13 T5) **or**
   the player records `first_contact` / `tackle` / `assist` on that play in `player_play`.

**All-efforts = secondary** (every episode, including pass-rush and run-fit movement where effort is more
discretionary). Reported with the same specification, never as the headline.

**Why this subset.** On a ball-in-air or pursuit rep the defender's effort is constrained by the opponent and
the ball, so it is the closest thing to a non-discretionary measure in tracking data. It reduces (does not
eliminate) the "he was pacing himself / the play was away from him" confound (`limitations.md` §45).

**Event vocabulary caveat.** `pass_forward`, `pass_arrived`, `pass_outcome_*`, `first_contact` and `tackle`
are present in the 2023 sample's `event` column (`notes/schema.md` §8), but the full-data vocabulary is
**unverified** (TBD T8). If a window cannot be built for a play, the episode is **not** promoted to
max-effort; it stays in the all-efforts set, and the exclusion count is reported.

## 5. Effort fraction (the outcome)

`EF = episode peak smoothed speed ÷ shrunken vmax` for that player.

- **No clipping at 1.** `EF > 1` is **information, not error**: the Combine ceiling is a *test-day
  measurement* (one day, spikes, a straight-line drill), so an in-game peak above it is a real observation
  about the ceiling's calibration — exactly the quantity the exceedance question is about. Clipping would
  destroy the top of the distribution and bias the load slope toward zero. The share of `EF > 1` is reported.
- **Ceiling SE is carried** into the model: EF is a ratio with a noisy denominator, so the ceiling enters
  with its SE, and the **measurement-error-corrected** variant (attenuation-corrected by the ceiling's
  reliability) is reported **beside** the naive one (§2.3).
- EF is bounded below by construction (episodes require ≥ 6.0 yd/s or the player-relative cut), so no
  log transform is used; the model is on the EF scale, which is also the scale of the smallest effect of
  interest (§12).

## 6. Load (the exposure)

**Primary: `prior_hi_efforts_per10`** — the count of the player's **prior high-intensity episodes in the same
game, strictly earlier** (same player-game, `time` < episode start), **divided by 10**. One unit = ten prior
high-intensity episodes. "High-intensity" here is the §3 episode definition (speed or acceleration branch),
**not** the max-effort subset — load accumulates from all efforts, including non-max-effort ones.

**Secondary: `prior_distance_yd_per100`** — cumulative `x`/`y` path length (yards) by the player earlier in
the same game, divided by 100. This is the physical-work load, independent of how episodes are counted, and
is the check on whether the effort count is just a proxy for snaps.

**Also in the model:** `snaps_in_drive` (defensive snaps so far in the current drive) and
`seconds_since_rest`, where **rest = a gap of ≥ 120 s without a snap**. Both are measured from
`player_play` (snap times) plus `game_clock`, not from tracking.

**Fixed properties.** Load is **mean-centred and rescaled** before fitting; it is computed **within
player-game only** (no cross-game accumulation); the first episode of a game therefore has load 0 by
construction and is retained (it is a legitimate low-load observation, not a missing value).

## 7. Controls

Entered as fixed effects unless noted; every level set is fixed here.

| Control | Levels / form |
|---|---|
| Play type | pass / run / other (from `pass_result`, `play_description`; excluded plays per §8) |
| Alignment | `outside_corner` / `slot_corner` / `safety` / `other`, from `lined_up_position` (`CB → outside_corner`, `SLOT_CB → slot_corner`, `HIGH_SAFETY`/`BOX_SAFETY → safety`, else `other`; `db_decay.audit.alignment_map`) |
| Down | 1 / 2 / 3 / 4 |
| Yards to go | numeric (continuous), plus a short-yardage indicator if the fit needs it (declared sensitivity, not a re-spec) |
| Score margin band | **0–7 / 8–14 / 15–21 / 22+** (absolute pre-snap margin) |
| Quarter | 1–4 (+ OT if present, its own level) |
| Garbage time | flag (see §8) |
| Man/zone | `team_coverage_man_zone` where available, **plus an explicit `unknown` level** (13.8% missing on DB rows — `notes/schema.md` §7) |
| Dropback duration | where available — **joined from the opponent (offense) row of the same play**, because it is 0% non-null on DB rows (TBD T9) |
| Random effects | `(1 + load \| player)` |

**Not controls (and why).** Draft slot/draft round (post-Combine outcome; `notes/rules.md` §8). `target`
(post-snap, role-dependent). Any post-snap outcome (`pass_result` beyond play-type classification,
`yards_gained`, EPA, win probability). Combine anthropometrics other than the ceiling itself would be
between-player constants that the random intercept already absorbs; if added they are a declared sensitivity,
not the primary spec.

## 8. Garbage time and excluded plays

- **Exclude**: any play with **|margin| ≥ 28** at the snap.
- **Exclude**: **Q4** plays with **|margin| ≥ 21** at the snap.
- **Exclude**: any play where `play_nullified_by_penalty` is true (the rep did not count).
- The garbage-time **flag** is retained as a column even for excluded plays, so the pre-declared sensitivity
  (rule relaxed to flag-as-control) can re-include them without re-deriving anything.
- Margins are computed from `pre_snap_home_score` / `pre_snap_visitor_score`. The rule is
  `PLACEHOLDER_SAMPLE` until `00_thresholds` confirms it against the full-data margin distribution
  (T7) — the *numbers* 21/28 are fixed here; only their provenance stamp is pending.

## 9. Minimum snaps and minimum efforts

| Filter | Value | Key |
|---|---|---|
| Defensive snaps in the season | **≥ 100** | `db_decay.game.min_snaps_per_player` |
| Max-effort episodes per DB (population models) | **≥ 20** | `db_decay.audit.min_efforts_per_db` |
| Max-effort episodes per DB (per-player slopes) | **≥ 60** | `db_decay.audit.min_efforts_per_db_slope` |

Snap source is `player_play` (`db_decay.audit.snap_source`), not tracking (tracking coverage is partial —
§4 and `notes/schema.md` §8). Players below a floor are **counted and reported**, never silently dropped; the
count of excluded DBs is a required output of stage 03.

## 10. Estimands

Ordered, with the primary first. All coefficients are on the **EF scale** and the load scale of §6 (per 10
prior high-intensity episodes).

1. **Primary β — population decay.** Mixed model on the **max-effort subset**:
   `EF ~ load + snaps_in_drive + seconds_since_rest + controls + (1 + load | player)`.
   Estimand: the population-level coefficient on `load`. Negative β = effort fraction declines with
   accumulated in-game load. Reported with a player-clustered interval, **naive and
   measurement-error-corrected** (ceiling SE propagated).
2. **Secondary β_int — ceiling moderation.** Same model plus `load × vmax_z` (vmax standardised across
   players). Positive β_int = a higher ceiling protects against decay. This is the **only** interaction
   test (single-feature ceiling, §2.3).
3. **Exploratory — per-player slopes.** Empirical-Bayes slopes from the same fit, reported **only** for
   players passing the reliability threshold (§11). Descriptive of heterogeneity; not a claim about
   individuals.
4. **Secondary (declared, not headline) — all-efforts model.** Same spec on all episodes.
5. **Within-player contrast (declared).** Same DB early vs. late in the same game, matched on play type,
   alignment and score-margin band. This is the design that removes between-player confounding; it is a
   robustness estimate, not a replacement for the primary β.

**Fallback chain (fixed, applied in order, each logged):** (1) uncorrelated random effects
`(1|player) + (0+load|player)` with an alternate optimiser; (2) empirical-Bayes per-player slopes; (3) random
intercept only — report the population β and **skip** per-player slopes. A convergence failure is **not**
grounds to change the estimand.

## 11. Per-player slope reliability threshold

A per-player slope is reported only if the **model-based reliability**

`var(slope) / (var(slope) + mean SE²) ≥ 0.50`

(`db_decay.model.reliability_threshold`). **Explicitly NOT an odd/even split-half**: split-half reliability
answers a different question (internal consistency of two arbitrary halves) and is unstable at 20–60
episodes. The model-based ratio is the quantity that decides whether a slope is distinguishable from its own
noise, which is what a per-player claim requires. Slopes below the threshold are reported as a count, never
as numbers.

## 12. Smallest effect of interest (SESOI)

**0.01 effort fraction (1% of the ceiling) per 10 prior high-intensity episodes.**

Fixed before results. Its role: a null is **informative** only if the interval excludes ±0.01; an interval
that contains it is "not resolved", not "no effect". The audit's placeholder power calculation scaled to this
SESOI is sample-mode only and is **recalibrated on full data** (T6) — the number in `i_power*.csv` must not
be quoted in the writeup.

## 13. TBD register (every unresolved value, and the stage that fixes it)

No silent placeholders: if a value is not fixed above, it is here.

| # | Unresolved | Fixed by (stage / command) | Config key | Blocks |
|---|---|---|---|---|
| **T1** | Effort thresholds (0.85×vmax, 6.0 floor, 4.0 accel, sustain times) carry provenance `PLACEHOLDER_SAMPLE` | `00_thresholds` (`python src/00_thresholds.py --mode full`) writes `outputs/thresholds.json` with provenance `FULL` | `db_decay.game.effort_*`, `db_decay.audit.provenance_thresholds` | stage 03 (asserts `FULL`) |
| **T2** | Matched DBs with frame-level tracking; efforts/DB; total efforts; games/DB | `01_db_audit --mode full` (items c, h, i) | `db_decay.audit.min_matched_dbs` (40), `min_efforts_per_db` (20), `min_total_efforts` (4000) | go/no-go gate; any population claim |
| **T3** | Observed **load SD per 10 efforts** (audit assumed 2.0) | `03_game_efforts` writes the load distribution to the effort summary | `db_decay.power.assumed_load_sd_per10` (placeholder) | MDE recalibration |
| **T4** | **Lateral alignment derivation** (outside vs slot): tracking has no ball/lateral reference in the sample | `01_db_audit --mode full` item c; if underivable, `lined_up_position` stands alone | `db_decay.audit.alignment.*` | alignment control's validity |
| **T5** | **Nearest-receiver / co-location availability** (all-22 or combine-class only) | `01_db_audit --mode full` item d | *(none yet — add if available)* | max-effort condition 2 (within 5 yd of ball carrier); matchup-demand covariate |
| **T6** | **Full-run MDE** for the §10 primary β | `05_validate --mode full` (power block) | `db_decay.power.*`, `db_decay.model.*` | the "informative null" claim |
| **T7** | Garbage-time rule confirmation against full margin distribution | `00_thresholds --mode full` | `db_decay.game.garbage_time_q4_margin` / `_any_margin` | §8 exclusions |
| **T8** | Full-data **`event` vocabulary** (is `pass_forward`/`pass_arrived`/`first_contact`/`tackle` complete?); also whether all 22 are tracked | `01_db_audit --mode full` item d | *(no key)* | §4 max-effort subset |
| **T9** | **`dropback_duration` join** from the opponent row (0% on DB rows) and its missingness | `03_game_efforts` join; count nulls | *(no key — add `db_decay.game.dropback_join`)* | dropback-duration control |
| **T10** | **Config reconciliation**: smoothing window (config 5 frames vs 7 here), missing SG poly-order key, single 0.3 s sustain vs 0.3/0.2 s branches, inert `effort_accel_frac_of_a0` | config edit + `03_game_efforts` | `db_decay.ceiling.smooth_window_frames`, `db_decay.game.min_effort_duration_s`, `db_decay.game.effort_accel_frac_of_a0` | stage 03 correctness |
| **T11** | **No opponent co-location** ⇒ whether a within-game contrast is enough | `01_db_audit --mode full` item d; decision at design review | *(none)* | §10.5 within-player contrast |
| **T12** | Ceiling **SE propagation method** (analytic delta vs bootstrap) and the attenuation correction's exact form | `02_combine_ceiling --mode full` | `db_decay.ceiling.shrink_method` | §5 corrected variant |
| **T13** | Season scope of load accumulation (REG+POST per D49; are POST games included per player, and does a player's load reset across games?) | `03_game_efforts` scope assert | `game.scope.primary` (`[REG, POST]`) | §6 load definition |
| **T14** | Per-player slope count ≥60 achievable for how many DBs | `03` / `04` | `db_decay.audit.min_efforts_per_db_slope` | §11 reporting |
| **T15** | Whether `first_contact`/`tackle` in `player_play` match tracking events on the same play | `03` cross-tab | *(none)* | §4 condition 2 |

**Any TBD that cannot be resolved by its stage becomes a blocker** (`notes/blockers.md`), not a silent
default.

## 14. Analysis integrity

- **No full-data peeking.** No stage-03/04/05 output may be inspected before this file is FROZEN. Thresholds,
  minima and the SESOI are frozen **before** the first full run.
- **Any deviation** is logged as a dated entry in `notes/prereg-deviations.md` **and** `notes/decisions.md`,
  labelled **exploratory**, with the pre-change value. Deviations do not rewrite this file; they annotate it.
- **Sample numbers are never evidence.** Every sample output is stamped `UNVALIDATED SAMPLE OUTPUT`; the
  gate cannot return GO from sample mode (D57). No threshold, no power number and no effect size in this
  file may be tuned against sample output.
- **Synthetic fixtures** prove plumbing and sign conventions only; they are never findings.
- **Reported-null discipline.** A null is reported with its interval and the MDE. A model that fails to
  converge reports the fallback it used; a slope below the reliability threshold is reported as a count.

## 15. Config key map and known mismatches (must be reconciled before stage 03)

| Design item (this file) | Config key | Current state |
|---|---|---|
| Effort speed cut 0.85×vmax, floor 6.0 | `db_decay.game.effort_speed_frac_of_vmax`, `effort_speed_floor_yds` | present, matches |
| Effort accel cut **4.0 yd/s² absolute** | `db_decay.game.effort_accel_floor_yds2` | present; **`effort_accel_frac_of_a0: 0.80` is now inert** (no `a0` ceiling) — remove or mark unused |
| Sustain 0.3 s (speed) / **0.2 s (accel)** | `db_decay.game.min_effort_duration_s` | **single value 0.3; no accel-specific key — MISMATCH** |
| Smoothing **7 frames, poly 3** | `db_decay.ceiling.smooth_window_frames` | **5 frames; no poly-order key — MISMATCH** |
| Min snaps 100 | `db_decay.game.min_snaps_per_player` | matches |
| Min efforts 20 / 60 | `db_decay.audit.min_efforts_per_db` / `_slope` | matches |
| ICC thresholds 0.60 / 0.50 | `db_decay.audit.min_icc_primary` / `_secondary` | matches |
| Garbage time 21/28 | `db_decay.game.garbage_time_*` | matches |
| Reliability 0.50 | `db_decay.model.reliability_threshold` | matches |
| Random effects `(1 + load \| player)` | `db_decay.model.slope_spec` | matches |
| Load names | `db_decay.game.load_primary` / `_secondary` | matches |
| Ceiling sanity 12.5 / 1.5× | `db_decay.ceiling.sanity_*` | matches |

## 16. Not in this pre-registration

Deliberately out of scope, so nobody assumes otherwise: route-level or coverage-scheme attribution; any
offense-side question; in-game injury detection; a physiological fatigue mechanism; predictive modelling of
player value; per-play win-probability effects. The archived WR/TE security estimands (EDP groups, dose
response, selection/IPW) are **not** part of this file and carry no standing here.

## 17. Route-depth control — depth at the break (D61, frozen definition; prereg still DRAFT)

**Status.** This section is the design of record for the **depth control** only. It applies to the
leverage × ceiling task (`TASK.md`, `bdb27_lc`) and must be carried verbatim into the pending rewrite. It
is **not** frozen as a pre-registration: the coverage/power question in §17.6 is Conor's to settle. The
estimand, the primary hypothesis, the leverage definition, the ceiling feature and the effort definition
are untouched by this section.

### 17.1 What depth is, and what it is not

Depth is **realised geometric depth at the break**, read from the 10 Hz tracking. It is **not** route
family alone, a design-predicted depth from the route label, a depth bin, alignment, cushion,
start-to-end displacement, or `pass_length`.

* Start-to-end displacement is rejected: over a fixed route duration it is mechanically a function of
  speed, i.e. the same quantity that generates peak speed (sample correlation 0.82; D61 §1). It would
  absorb the outcome.
* `pass_length` is rejected: it is the literal string `"NA"` in 129,458 of 314,197 `player_play` rows and,
  where present, tracks the thrown ball (median 6.0 yd on GO routes), not the route design (D61 §2).

### 17.2 The definition (all constants in `bdb27_lc.route_depth`)

1. **Play frame.** `+x` = downfield from `play_direction` (`right` ⇒ field `+x`; `left` ⇒ field `-x`;
   lateral `y` is flipped with the same rotation). Origin = the receiver's position at the **snap frame**.
2. **Smoothing.** The existing centred rolling-median smoother, window **5 frames (0.5 s)**
   (`thresholds.smoothing.window_frames`). No second smoothing convention.
3. **Heading.** Direction of the centred displacement over **0.2 s**, interpolated onto the actual frame
   times. Frames with implied speed < **2.0 yd/s** have no heading.
4. **Stem.** At least **0.8 s** of post-snap motion before a break can be declared.
5. **Break.** First frame at/after the stem where cumulative `|dtheta|` over a sliding **0.3 s** window
   reaches **45 deg**, and the following **0.3 s** of heading stays within **20 deg** of its own circular
   mean. The timestamp is the **first frame of the sustained change**.
6. **Depth at break.** Downfield displacement at that frame, in yards, relative to the snap position.
7. **No break ⇒ missing.** `break_found = False`, depth **missing**. Never imputed from family,
   displacement or `pass_length`.
8. **Throw flag.** A break after the tracking `pass_forward` frame is `break_after_throw = True`. The flag
   never deletes a row; the model is reported with and without post-throw breaks.

### 17.3 Model entry

Route family (**always**) + centred `break_depth_yd` (continuous, yards) + `break_found` indicator.
Depth is a **control**: reported, never interpreted. The depth term is centred on detected breaks and
coded 0 for no-break rows, with the indicator carrying those rows; the data table keeps NaN for no-break
routes (reference coding, not imputation; asserted by test).

### 17.4 Validation (synthetic truth)

204 planted breaks, 84 no-break routes. Frozen constants: 204/204 detected; median absolute depth error
**0.18 yd**, maximum **0.91 yd**; zero false positives on no-break routes; zero fabricated depths; all 48
planted post-throw breaks flagged. Specification curve ±10 deg × ±0.1 s: one-step perturbations recover
≥99.5% with ≤0.91 yd error; the strictest corner (55 deg, 0.2 s) recovers 84.3%. Deterministic
re-runs are identical.

### 17.5 Sample coverage (UNVALIDATED SAMPLE OUTPUT — descriptive only, never a tuning input)

3,054 analysed route snaps: **89 breaks (2.9%)**; WR 2.8%, TE 3.5%; **76.5% of breaks with a known throw
are after the throw**; pre-throw coverage **0.9%** (27 breaks). Coverage by family and the full
specification curve are in `outputs/game/lc/route_depth/SUMMARY.md`. These numbers are stamped sample
output and may **not** be used to choose a constant.

### 17.6 Open question — blocks the freeze (Conor)

The literal 45 deg/0.3 s criterion yields ~3% coverage on the provided sample, and most detected breaks
are post-throw adjustments. If that generalises, the continuous depth control is estimated off a few dozen
pre-throw breaks and the model is effectively route family + a rare indicator. Options, to be decided on
design grounds **before** freezing (not by tuning on the sample): (a) freeze as specified and accept the
coverage; (b) relax the break window (the fragile axis; 35 deg/0.4 s ⇒ 16.6% on the sample);
(c) reinterpret the criterion as *total* turn since the snap (a different definition, near-full coverage);
(d) drop the continuous term and use route family + break indicator, reporting depth descriptively.

The **power call** is also still open (D60): analytic MDE 0.044 at 40 efforts/player; 88 efforts/player
needed for MDE 0.03. No prereg freeze is possible until Conor answers §17.6 and the power question.
