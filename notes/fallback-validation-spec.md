# Fallback validation — specification: is the shallow break a measurable capacity the battery misses?

**Written 2026-11-15, before any ICC, correlation or coefficient in this cycle is computed.** Same
sense as the other specs in this project: the estimand, the sample, the estimator, the numbers and
the decision rule are committed to git *before* the result is looked at. If a result differs from
what is expected here, the expectation is not edited afterwards.

Authorising words: Conor, 2026-11-15 00:18 — *"Ok, and we need another plan to validate this if
this doesn't work out"*.

Spec written by the PM. `TASK.md`, the band table, the four figures and `notes/report-draft.md` are
untouched by this cycle.

---

## 0. Why this cycle exists, and what it can and cannot license

**The outcome route is capped, not unsolved.** The tracking on this machine carries the
draft-prospect cohort only. 5,176 of the 7,777 DB 30–60° game turns have no visible opposing
route-runner at all; the 1,731 that do gave +0.05 yd per unit retention, 95% CI −0.36 to +0.46
(D199), on an attribution that is an inference; and the geometric notebook (D200) refines the
attribution *inside* that subset without widening it. A better match cannot manufacture opponents
the file does not carry. So the fallback must rest on quantities that need **no opponent
identifier at all**.

This cycle tests two such quantities, plus one descriptive statement.

**Three things must be said before any number is quoted.**

1. **The task's own claim is not at risk here.** `TASK.md`'s claim is supply against demand by turn
   angle, and it is `[FULL]`: 26 Combine turns against 7,777 game turns in the band, 2.21% against
   13.26%, 6.01×. No leg of this cycle can weaken that. What is under test is the *recommendation* —
   that the Combine should add a 30–60° drill.
2. **Every output of this cycle is a diagnostic note, not a deliverable.** `TASK.md` lists "No
   per-prospect anything", "Between-player capacity is not identified and this task does not try",
   "No outcome test" and "No transfer or feasibility test" under *what may not be claimed*. Leg 1
   is explicitly between-player capacity and leg 2 is explicitly a transfer test. They are
   authorised here as a separate analysis in exactly the way D199 was (Conor, 2026-11-14), and by
   the same rule nothing produced here enters `TASK.md`, the band table, the four figures or the
   report draft. If a leg warrants a report sentence, it is **proposed** to Conor in the result
   note, never inserted.
3. **Two of the three legs are inferences, and are labelled as such.** Leg 1 measures the
   reliability of *in-game* band retention. It does not measure the reliability of a drill, because
   the drill does not exist. It is the best available proxy for whether a drill score *could* be
   reliable, and the step from one to the other is stated in every sentence that reports it. Leg 2
   measures what the existing drills do predict. Neither is a demonstration that the proposed drill
   would work.

---

## 1. Leg 1 — Reliability: is 30–60° retention a stable player trait?

### 1.1 Estimand

The intraclass correlation of `apex_retention` across turns **within the same player**, at
30 ≤ `abs_turn_angle` < 60 in games. "How much of the between-player spread in band retention is
signal about the player rather than noise between his own turns."

Interpretation fixed in advance: a low ICC means the band is not a trait in this cohort, and a drill
built to measure it would be measuring mostly turn-to-turn noise. That is a finding about the
recommendation, and it is reported as the headline, not buried.

### 1.2 Sample

- `setting == 'game'`, `position_group == 'DB'`, `valid == TRUE`, `30 ≤ abs_turn_angle < 60`.
- Source: `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` (**FULL**).
- **7,777 turns, 113 players**, 7,198 plays, 809 games. Turns per player: min 1, median 48, max 300; 8 players
  have ≤ 4 turns.
- No opponent identifier is used, no `player_play` merge, no tracking file. This leg is computable
  from the committed parquet alone.
- `[FULL]` on every artefact.

### 1.3 Estimators (all three are primary; all three are printed)

1. **ICC(1), one-way random effects, unbalanced.** Model
   `y_ij = μ + a_i + e_ij`, `a_i ~ (0, σ²_a)`, `e_ij ~ (0, σ²_e)`, player `i`, turn `j`.
   ANOVA moments; `k0 = (N − Σk_i²/N)/(g − 1)` with `g = 113`; `σ²_a = (MSB − MSW)/k0`,
   `σ²_e = MSW`; `ICC = σ²_a/(σ²_a + σ²_e)`. If `MSB < MSW`, `σ²_a` is clamped to 0 and the point
   estimate is reported as `0.000 (clamped)` with both mean squares printed beside it — the clamp is
   a display convention, the maths is not changed.
   **CI:** player-level cluster bootstrap over resampled players (preserves the unbalanced design
   exactly as it is), 2,000 resamples, seed `20261115`, percentile interval.
   Also print the analytic CI for cross-check and say which is quoted.

2. **Split-half (odd/even) + Spearman–Brown.** Within each player, order turns deterministically by
   `(season, game_id, play_id, frame_start)` and assign alternately to half A / half B. Correlation
   of the two half-means across players; `R_sb = 2r/(1 + r)`. Players with no turn in one half are
   **not estimable (saturated)** and are printed as such with their counts. Bootstrap CI over
   players, same seed.
   Sensitivity, printed beside it: restrict to players with ≥ 4 turns in each half.

3. **Season-to-season.** Per-player season means. Pearson `r` between the 2024 and 2025 means (the
   two populated seasons) across players present in both. Printed twice: all players with ≥ 1 turn
   in each season, and those with ≥ 10 turns in each. Both n's are printed with both numbers.
   Seasons in the band: 2023 = 8,016 turns / 33 players, 2024 = 21,275 / 77, 2025 = 29,373 / 101.
   Per `TASK.md` honesty limit 9, season rates are **not** comparable and no pooled-vs-season claim
   is made; this is a within-player consistency check, not a rate comparison.

**Derived, printed as a consequence:** the reliability of a player's *mean* band retention,
`R_mean = k̄·ICC / (1 + (k̄ − 1)·ICC)` with `k̄` the harmonic mean of turns per player, and the
implied standard error of each player's mean. This is the number that governs leg 2: leg 2 regresses
on player means, so its exposure is attenuated by exactly this quantity. Reported for both the
sample-size-weighted and the harmonic-mean `k̄`.

### 1.4 Numbers fixed before the fit

| Label | Condition | Consequence for the recommendation |
|---|---|---|
| **STABLE** | `ICC(1) ≥ 0.50` **and** lower 95% bound `≥ 0.30` **and** `R_sb ≥ 0.50` | the band is a trait; leg 2 decides whether the battery already reaches it |
| **WEAK** | anything not STABLE and `ICC(1) ≥ 0.20` | trait-like enough to be worth naming, too weak to carry a recommendation on its own |
| **VOID** | `ICC(1) < 0.20` **and** upper 95% bound `< 0.40` | no stable capacity in this cohort; the recommendation is void and leg 2 is reported descriptively only, with that stated |

These are **verdict labels, never control flow.** The notebook computes and prints every statistic in
§1.3 whatever the label is. Nothing stops, nothing raises, nothing is skipped because a number came
out small (D202).

---

## 2. Leg 2 — Discriminant validity: does the existing battery already capture it?

### 2.1 Estimand

For each Combine drill in {`three_cone`, `short_shuttle`, `forty`, `ten_yd_split`}: how strongly the
drill predicts a player's **shallow-break** retention, against how strongly the same drill predicts
his **deep-plant** retention, on the same players.

The recommendation survives only if the battery reaches the deep plant and does not reach the shallow
break. If a drill predicts both, the battery workspace is wider than the band argument says and the
recommendation is weakened. If a drill predicts neither, that drill says nothing either way and is
reported as such.

### 2.2 Sample

Player level, one row per game-tracked DB with at least one band turn.

- **Outcome S** (`shallow`): mean `apex_retention` over `30 ≤ abs_turn_angle < 60`, **113 players,
  7,777 turns** `[FULL]`.
- **Outcome P** (`deep plant`): mean `apex_retention` over `120 ≤ abs_turn_angle < 150`, **115
  players, 11,444 turns** `[FULL]`. This is the band the battery over-supplies (42.32% of Combine
  turns against 19.51% of game turns) and the band where plants dominate (79.0% of its turns).
  `150–180°` (114 players, 8,137 turns, 93.4% plants) is carried as a declared **sensitivity**, not
  as a third primary outcome.
- **Predictor**: `combine_results.csv`, joined on `nfl_id`, `combine_position == 'DB'`.
- **Join coverage — the constraint that governs the whole leg, printed with every number:**

  | Drill | Players with the drill (of 115 game DBs) |
  |---|---:|
  | `three_cone` | **23** |
  | `short_shuttle` | **31** |
  | `forty` | **98** |
  | `ten_yd_split` | **98** |

  The two drills that actually contain a turning test — the 3-cone and the short shuttle — have 23
  and 31 players. **This is a real limit and it is pre-declared, not discovered.** Every coefficient
  carries its own n. No drill is dropped for being small; each is fitted on whatever it has and
  labelled.
- No other position group is used, at any point, as background or as a comparison.

### 2.3 Estimators

- **Standardisation.** Each drill is flipped where needed so that **positive = faster/better** and
  z-scored on its own estimation sample, so a coefficient reads *retention change per one standard
  deviation of the drill*. Both the z-scale coefficients and the raw-unit coefficients are written
  to the artefact.
- **Primary model**, per drill, univariate OLS on the player means:
  `S_i = a + b_S·z_i + u_i` and `P_i = a + b_P·z_i + u_i`, with HC1 standard errors. Univariate, not
  joint: with 23 players on the 3-cone, a four-drill joint model is not identified in any useful
  sense, and the drills are collinear by construction.
- **The discriminant statistic is the paired difference** `Δ = b_P − b_S`, estimated by a player-level
  bootstrap that refits **both** equations on the same resampled players (2,000 reps, seed
  `20261115`), percentile CI.
  *Why paired and not "one significant, one not":* the difference between significant and
  non-significant is not itself significant (Gelman & Stern 2006). `Δ` with its own CI is the only
  honest test of "the drill tracks one band more than the other", and it is what the verdict uses.
- **Materiality bar, fixed now.** A drill must move retention by at least **a quarter of the
  between-player standard deviation of the outcome it is fitted to** before the association is called
  material: `|b_S| ≥ 0.25·SD(S)` and `|b_P| ≥ 0.25·SD(P)`. `SD(S)` and `SD(P)` are printed. A
  coefficient below its bar is reported as `not material`, with its value and CI, whatever its
  p-value.
- **Precision-weighted sensitivity.** Player means differ in precision (band turns per player 1 to
  300). Primary fit is unweighted; sensitivity weights by `n_i`. Both printed. If the two disagree in
  sign or in materiality, the disagreement is reported and the unweighted fit is quoted.
- **Minimum detectable effect, printed and labelled as post-hoc.** For each drill: the smallest
  `|b|` detectable at 80% power, two-sided α = 0.05, given the achieved n and the residual SD. This
  is what says out loud that 23 players cannot settle a quarter-SD question, and it is the reason a
  null here is written "not detectable", never "not present".
- **Multiplicity.** Four drills × two outcomes = eight coefficients plus four `Δ`. No correction is
  applied to the point estimates; the note says how many intervals would be expected to exclude zero
  by chance at 95%, and the verdict rule in §4 is written to not turn on a single marginal interval.

### 2.4 Numbers fixed before the fit

| Label | Condition | Consequence |
|---|---|---|
| **DISCRIMINANT** | for `three_cone` **or** `short_shuttle`: `Δ ≥ 0.25·SD(S)` **and** its 95% CI excludes 0, **and** `|b_S| < 0.25·SD(S)` | the battery reaches the deep plant and not the shallow break — supports the recommendation |
| **TRANSFERS** | for `three_cone` or `short_shuttle`: `|b_S| ≥ 0.25·SD(S)` **and** its CI excludes 0 | the battery already predicts the shallow break — **weakens** the recommendation |
| **SPEED-TRANSFERS** | for **both** `forty` **and** `ten_yd_split`: `|b_S| ≥ 0.25·SD(S)` with CI excluding 0, **and** `Δ` not ≥ `0.25·SD(S)` | linear speed already reaches the band — **weakens** the recommendation, and this is the best-powered test in the set at n = 98 |
| **NULL-BATTERY** | every drill's `|b_P| < 0.25·SD(P)` | the drills do not even predict the deep plant in this cohort: the discriminant logic has no purchase and the leg is reported as **uninformative**, not as support |
| **UNDERPOWERED** | none of the above fires | with the MDEs printed |

`TRANSFERS` outranks `DISCRIMINANT` if both fire for the same drill; they cannot both fire for the
same drill on the same coefficient, but they can fire for different drills, and when they do the
note reports both and the verdict is **WEAKENED**.

---

## 3. Leg 3 — Speed coverage: what the Combine ever records, against what the band requires

Descriptive. **No criterion, no verdict, no inference** — this leg exists because it needs none.

Printed, each with its n:

1. **Game, band, entry speed** (7,777 turns): 5/10/25/50/75/90/95th percentiles, and the shares
   below 3, 3–5, 5–7 and above 7 yd/s. Median in the band is 3.68 yd/s.
2. **Combine, band, entry speed** (the 26 turns, 16 players): the full sorted list of 26 values —
   it is small enough to print whole — with its range and percentiles.
3. **Combine, by drill** (1,179 turns): entry-speed percentiles for each drill that contributes to
   the band (3-cone n = 14, back-pedal-and-90 n = 4, Teryl Austin 2 n = 2, box n = 1, Teryl Austin 1
   n = 1, back-pedal-transition-45 n = 1, forty n = 1, short shuttle n = 1, W-drill n = 1), plus the
   drill-level medians from `TASK.md` §3 (3-cone entry 3.98, short shuttle 3.55) quoted as `[FULL]`
   with that provenance, not recomputed.
4. **The single number the leg exists for:** the **maximum entry speed observed anywhere in the
   Combine DB set** (1,179 turns), and the share of the 7,777 game band turns whose entry speed
   **exceeds** it. That is the gap, stated as a count and a share, with no model in it.
5. **The joint gap.** A 4 × 2 crosstab of entry-speed class (below 3 / 3–5 / 5–7 / above 7 yd/s)
   against turn-angle class (30–60 / 60–90 / 90–120 / 120–180) for each setting, with counts. This
   shows the thing the marginals hide: the Combine records high entry speeds and it records shallow
   angles, but whether it records them **together** is the question, and the crosstab answers it in
   counts.

**No new figure is produced.** `TASK.md` allows exactly four figures and forbids a fifth in "the
writeup, the notebook or the draft". This cycle adds tables only.

---

## 4. The verdict rule, in full, fixed before the fit

Evaluated in this order; the first that fires gives the headline. Every later row still prints.

| # | Condition | Headline verdict for the recommendation |
|---|---|---|
| 1 | Leg 1 = **VOID** | **VOID** — no stable band capacity in this cohort to measure |
| 2 | Leg 2 = **TRANSFERS** or **SPEED-TRANSFERS** | **WEAKENED** — the battery's measures already reach the band |
| 3 | Leg 2 = **NULL-BATTERY** | **UNINFORMATIVE** — the discriminant test has no purchase |
| 4 | Leg 1 = **STABLE** and Leg 2 = **DISCRIMINANT** | **SUPPORTED** — stable band trait the battery reaches only at the deep plant |
| 5 | Leg 1 = **WEAK** and Leg 2 = **DISCRIMINANT** | **PARTIALLY SUPPORTED** — direction consistent, trait strength below the bar |
| 6 | otherwise | **INCONCLUSIVE / UNDERPOWERED**, with the MDEs printed |

### 4.1 What refutes the recommendation

Written as a statement of the result that would make it wrong, before looking:

> **The recommendation is refuted if band retention turns out not to be a property of the player.**
> Specifically: an ICC(1) below 0.20 whose upper 95% bound stays under 0.40, or a split-half
> reliability under 0.50, means the 30–60° band does not carry a stable between-player signal in this
> cohort. A drill can then only measure turn-to-turn noise, and "add a drill" asks the Combine to buy
> a number that will not repeat. This is reported as the finding.

> **It is also refuted if the existing battery already covers the band.** Specifically: if the
> 40-yard dash and the 10-yard split — the two drills with an adequate n of 98 — each predict shallow
> band retention by at least a quarter of its between-player SD with an interval excluding zero, and
> do not predict the deep plant materially better, then the band is a speed trait the battery already
> prices in, and the 6.01× shortfall in Combine *turn share* does not imply a shortfall in Combine
> *information*.

> **It is not refuted by a null on the 3-cone or the short shuttle.** Those two are fitted on 23 and
> 31 players. A wide interval there is an absence of evidence, and the note will say so in those
> words.

### 4.2 The one piece of evidence that would make it a finding

The recommendation becomes a finding when a 30–60° break executed **at the speed games actually
use** is shown to be (i) reliable for a player, (ii) not redundant with the existing battery, and
(iii) related to something that matters on the field. On this machine the third is capped by the
cohort-only tracking (D199) and the first two are only reachable by proxy: **the missing item is a
score from an actual 45°-break drill for these 115 players, which does not exist in any file here.**
The ICC of the band is the closest available stand-in for (i); the discriminant test is the closest
available stand-in for (ii). Both are labelled proxies in the note, and the note says which single
artefact would replace both.

---

## 5. Provenance inventory (verified this cycle, 2026-11-15)

| Artefact | Contents | Stamp |
|---|---|---|
| `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` | 58,665 rows, 1 invalid; 115 players, 38,439 `(game_id, play_id)`; bands as `TASK.md` | **FULL** |
| ↳ band turns | 7,777 turns, 113 players, 7,198 plays, `apex_retention` non-null on all 7,777 | **FULL** |
| ↳ deep-plant turns | 120–150°: 11,444 turns, 115 players. 150–180°: 8,137, 114 players | **FULL** |
| `outputs/sensitivity/speed_floor/combine_db_turns_floor2.5_control.csv` | 1,179 Combine DB turns, 122 players, 10 drills, `entry_speed` non-null on all; 26 in band from 16 players (14 are 3-cone fragments, per `TASK.md` §2) | **FULL** (D190 control pass) |
| `data/raw/combine_results.csv` | 510 prospects, 122 DB; `three_cone` 23 of the 115 game DBs, `short_shuttle` 31, `forty` 98, `ten_yd_split` 98 | **FULL** |

Every number in this cycle is therefore computable on this machine. No leg is deferred to the
laptop, and no tracking file is read.

---

## 6. What this cycle will not do

- It will not touch `TASK.md`, the band table, the four figures, `notes/report-draft.md`, or
  `outputs/figures/`.
- It will not add a figure.
- It will not re-run or re-interpret D199 or D200, or read the tracking file.
- It will not claim that the proposed drill works, that retention causes anything, or that a null is
  evidence of absence.
- It will not use any position group other than DB, at any point, in any capacity.
- It will not introduce a control-flow branch on any computed number. Every threshold in §1.4, §2.4
  and §4 produces a label and a printed denominator, never a skip, a stop or a raise (D202). The
  only legitimate failures remain: a required file not found (every path tried is named), a file
  unreadable, a required column missing.
- It will not change any existing estimator's maths, identification guard or point estimate.

## 7. Display rule for saturated cells (display only)

Where an estimator is saturated — `n` observations ≤ `n` parameters, as with a player holding 4 band
turns split into 2 per season, or a single-turn half — the interval is printed as
**`not estimable (saturated)`** and the cell's counts are printed beside it. The point estimate is
still printed where one exists, and no zero-width interval is ever produced. **The estimator's maths,
its identification guard and its reported point estimate are unchanged.** This applies to leg 1's
split-half and season cells and leg 2's per-drill fits when a drill's non-missing n falls to its
parameter count; nothing else.

## 8. Deliverables

| File | Role |
|---|---|
| `notes/fallback-validation-spec.md` | this file, committed **before** any fit |
| `src/bdb27/fallback_validation.py` | estimators and label rules; pure functions over frames |
| `scripts/fallback_validation.py` | headless runner used to produce the committed numbers |
| `notebooks/bdb27_fallback_validation.ipynb` | **Conor's interface**: Run All, no CLI, nothing to edit |
| `outputs/fallback_validation/` | `reliability.json`, `discriminant.csv`, `speed_coverage.json`, `verdict.txt`, `manifest.json` (sha256 of every input and output, git HEAD) |
| `tests/test_fallback_validation.py` | the estimators, the label rules, the saturated display, determinism, the no-gate contract |
| `notes/fallback-validation-result.md` | the numbers, the verdict, and the paragraph proposed for Conor |
| `notes/decisions.md` | a D-number with the reason |

## 9. Acceptance

1. `./.venv/bin/python -m pytest -q` green.
2. Two consecutive runs produce byte-identical `outputs/fallback_validation/*` (honouring the
   manifest's own timestamps).
3. The notebook is committed **unexecuted** and executes end to end here on the committed data.
4. Every reported number carries its n and its provenance stamp.
5. The verdict in `verdict.txt` is one of the six labels in §4, derived only from the pre-stated
   rules, and the note states which rule fired.
6. Each leg that could not be computed is named with the counts and the data that would be needed —
   the expectation is that none is, and that expectation is a claim this cycle must verify rather
   than assume.
