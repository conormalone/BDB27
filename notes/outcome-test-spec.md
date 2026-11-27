# Outcome-test specification — separation at pass forward against break retention

**Written 2026-11-14, before any coefficient was computed.** This file is the pre-registered
specification in the only sense this project uses one: a written statement of the estimand,
sample, controls, estimator and decision rule, committed to git *before* the result is looked at.
If the result differs from the expectation, the expectation is not edited afterwards.

Authorising words: Conor, 2026-11-14 14:59 — *"Without a link to separation, coverage success,
tackling or injury, the recommendation is an assertion. Even a modest result (for example, DBs who
retain more speed through 30–60° breaks allow less separation, after controlling for speed and
position) would turn this from an observation into a finding."* And 15:37 — *"It's the only way, go
for it."*

`TASK.md` is unchanged by this cycle. `TASK.md` currently lists "No outcome test" under *what may
not be claimed*; Conor's instruction of 2026-11-14 authorises this test as a separate analysis.
Following the standing rule, this cycle does not rewrite the claim, the band table, the figures or
the report draft. If the result warrants it, a new section is proposed to Conor, not inserted.

---

## 0. The constraint that fixes the design

Every tracking file on this machine is the **1,048,575-row truncation** (2²⁰ − 1;
`data/raw/game_tracking_2023_sample.csv` = 1,048,575 data rows, 333 games, 12,638 plays of 31,720,
154 of 497 players, never more than four players on a play). The frozen 3-yd proximity rule
(D197) needs `(x, y)` for both the DB and the opposing receiver at the break frame. It cannot be
evaluated here, and the note's own gate — 500 attributed turns needs 16.6% of 3,009 candidates
unique at 3 yd — is therefore **undecidable on this machine, not merely undecided**.

Waiting for it means no estimate runs. So this cycle estimates the relationship on the population
where attribution needs no coordinates at all: **turns whose play carries exactly one opposing
route-runner**, where elimination leaves one possible receiver.

## 1. Unit and population

- **Setting** `game`, `position_group == 'DB'`, `valid == TRUE`, `30 ≤ abs_turn_angle < 60`.
  Source: `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` (**FULL**).
- **Turn key** `(game_id, play_id, nfl_id, frame_start)`. Never the three-column player key (D198).
- Band turn rows: **7,777**, from 113 players, 7,198 plays, 809 games.

## 2. Attribution — elimination matching (`INFERENCE`)

A turn enters the primary sample iff **both**:

1. the turn's play carries **exactly one opposing-side `player_play` row with a non-`NA`
   `route_ran`** (opposing = `team_abbr <> ` the turning DB's own `team_abbr`), and
2. that single opposing route-runner **has `separation_at_pass_forward` recorded**.

Turn rows are never merged across their own `player_play` row (checked: one own row per band
turn), and a turn is never counted twice.

**This is weaker than the frozen 3-yd rule and the difference is stated in every artefact.** The
frozen rule uses proximity at the break frame to say *this* is the man he covered. Elimination
matching only says *this is the only opposing receiver the file can see on this play*; the single
tracked opponent need not be the receiver the DB was covering. It removes the many-to-one
ambiguity; it does not establish that a one-to-one matchup occurred. Every sentence that reports a
coefficient describes the sample as "turns where exactly one opposing route-runner is visible",
never as "matched coverage".

## 3. Outcome, exposure and controls

| Role | Variable | Source | Handling |
|---|---|---|---|
| **Outcome** `y` | `separation_at_pass_forward` (yd) | matched receiver's own row | as recorded; no transform in the primary |
| **Exposure** `x` | `apex_retention` (apex speed ÷ entry speed) | turn table | as recorded |
| Control | `entry_speed` (yd/s) | turn table | linear + square |
| Control | position | turn table `position` (CB / SS / FS) | fixed effects |
| Control | season | turn table `season` (2023–2025) | fixed effects |
| Control | coverage family | DB's own row `coverage_assignment` | MAN / ZONE / MISSING, fixed effects |
| Not controlled | `cushion` | DB's own row | reported as a secondary block only; 34.7% missing |
| Not controlled | route family | matched receiver's `route_ran` | robustness only — post-snap, so a bad control |

`coverage_assignment` is a pre-snap label; `ZONE` is any non-`MAN`, non-`NA` value, and
`NO_ASSIGNMENT` is grouped with `MISSING` because the artefact says nothing about a zone family.
No variable measured after the break enters the primary controls. Retaining speed through the
break and the route being covered are jointly determined, so route family is a robustness check,
not a control.

## 4. Estimator

Primary: OLS of `y` on `x` plus the controls above, with **standard errors clustered by player**
(CR1 finite-sample correction, G = 102 clusters). The player is the unit of independence: a
player's turns share his ability, his scheme and his opponents.

The coefficient of interest is **β₁, the change in separation (yd) per 1.00 unit of
`apex_retention`**; because a full unit is large on this scale, results are also reported per
**0.10 retention**, which is roughly a tenth of the observed spread.

Secondary (the note's §5 form): **player fixed effects**, within-player slope, clustered by
player. Players with a single attributed turn contribute nothing to it; their count is reported.

Deterministic: no sampling anywhere except the fixed-seed wild cluster bootstrap (below).

## 5. Pre-declared sensitivity set

Each is reported whatever it shows; none is substituted for the primary.

1. Player fixed effects (within-player).
2. **MAN-only** subsample (`coverage_assignment == 'MAN'`) — the strongest prior for one-to-one.
3. **One turn per play** — drop plays carrying more than one attributed turn, in case a shared
   play correlates the outcome.
4. Pass plays only (`pass_result` in C/I), dropping `IN` (n = 48).
5. `speed_retention` in place of `apex_retention` (secondary exposure, never a substitute).
6. Route-family fixed effects added (bad-control check: the coefficient should not change sign).
7. `log(separation)` outcome.
8. Cluster by play instead of player (1,598 clusters).
9. Wild cluster bootstrap (Rademacher weights, 999 replications, seed 20261114) for the primary
   β₁, because 102 clusters with a skewed outcome make the asymptotic t-test optimistic.

## 6. Decision rule — stated before the result

A **finding** requires all five:

- **(a)** primary β₁ < 0 — more retention, less separation, the direction Conor specified;
- **(b)** the 95% player-clustered interval on the primary β₁ excludes zero;
- **(c)** the player-FE specification has the same sign with an interval excluding zero;
- **(d)** the sign holds in the MAN-only and one-turn-per-play sensitivities;
- **(e)** |β₁| ≥ 2.0 yd per unit retention, i.e. **0.10 more retention ≈ 0.20 yd less
  separation**. This is deliberately modest (about a tenth of a standard deviation of the
  outcome): a trivial coefficient that is statistically detectable is not a finding.

Outcomes and their labels, fixed now:

| Result | Label |
|---|---|
| (a)–(e) all hold | **finding** |
| (a)–(d) hold, (e) fails | **detectable but too small to be a finding** |
| (b) or (c) fails | **estimate only, no claim** — report with interval |
| β₁ > 0 | **estimate only, opposite to the stated expectation** |

The point estimate and interval are reported in all four cases. A null is not upgraded, and a
number is not withheld because it is inconvenient.

## 7. Limits that ship beside the number

1. **The attribution is an inference.** Elimination matching says one opposing receiver is
   visible, not that he was covered.
2. **Prospect-versus-prospect only.** Both players must be in the 497 named players. This is not a
   random slice of NFL coverage; it selects on being drafted into the cohort and on which eligible
   players happen to be in the file.
3. **Zone-assigned turns can still enter** (the one visible receiver exists on zone calls too),
   but turns whose play carries more than one visible receiver — including every turn where the
   man and a second receiver are both tracked — are excluded, so the sample is not coverage as
   played.
4. **102 players is the independent unit.** Precision is player-clustered; a coefficient here is a
   sample of 102 careers, not of the league.
5. **Selection on being close enough to be visible is unmeasured.** Whether the single visible
   opponent was near the DB at the break cannot be checked without tracking; the frozen 3-yd rule
   remains unevaluated on this machine.
6. **No causal reading.** The break is not randomised; scheme, opponent quality and game state
   drive both retention and separation.
7. **Only one outcome.** Separation at pass forward on plays where it was recorded; coverage
   success, tackling and injury are not estimated here.
8. **Provenance.** Outcome-bearing artefact is `FULL` for turns and `player_play`; the tracking
   file that would be needed for proximity is the 1,048,575-row truncation and is used for
   nothing.

## 8. What ships

`src/bdb27/outcome_test.py` (estimator and sample construction), `scripts/outcome_test.py`
(writes `outputs/outcome_test/`), `tests/test_outcome_test.py`, and
`notes/outcome-test-result.md` with the number, its interval, the criterion verdict and the
caveats. No new figure (TASK.md names six). `TASK.md`, the band table, the existing
figures and `notes/report-draft.md` are untouched.

Small in-scope fix: `notebooks/bdb27_full_game_turns.ipynb` states the tracking sample has
"4,228 plays"; 4,228 is the number of distinct `play_id` values, while distinct
`(game_id, play_id)` pairs number **12,638**. The notebook sentence is corrected, and the same
correction is noted where the figure is asserted in `notes/schema.md` and
`notes/phase1-audit.md`.
